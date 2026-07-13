import os
import io
import re
import sys
import time
import logging
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _is_retryable(exception):
    if isinstance(exception, requests.exceptions.HTTPError):
        status = exception.response.status_code if exception.response is not None else None
        return status is not None and status >= 500
    return isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))

class CONABExtractor:
    def __init__(self, ano=None, mes=None):
        self.listagem_url = (
            "https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/"
            "safras/safra-de-graos/boletim-da-safra-de-graos"
        )
        self.headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # [NOVO] Desacoplamento Temporal
        if ano and mes:
            self.ano = str(ano)
            self.mes = str(mes).lower()
        else:
            periodo = DateRules.get_target_period()
            self.ano = str(periodo["ano"])
            self.mes = periodo["mes_str"]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _fetch_listagem_html(self, b_start=0):

        logger.info(f"Buscando página de listagem de boletins da CONAB (b_start={b_start})...")
        time.sleep(1)
        params = {"b_start:int": b_start} if b_start else None
        response = requests.get(self.listagem_url, headers=self.headers, params=params, timeout=20)

        if response.status_code != 200:
            logger.warning(f"Falha ao acessar listagem (Status {response.status_code}). Tentando novamente...")

        response.raise_for_status()
        return response.text

    def _find_levantamento_url_paginado(self, mes_str, ano, max_paginas=20, itens_por_pagina=30):

        for pagina in range(max_paginas):
            b_start = pagina * itens_por_pagina
            html = self._fetch_listagem_html(b_start=b_start)
            soup = BeautifulSoup(html, 'html.parser')

            links_pagina = soup.find_all('a', href=True)
            if not links_pagina:
                logger.info(f"Página {pagina} da listagem veio vazia. Fim da paginação.")
                break

            url_encontrada = self._find_levantamento_url(html, mes_str, ano)
            if url_encontrada:
                return url_encontrada

            # Heurística de parada: se todas as datas "Publicado em" desta
            # página já são anteriores ao ano alvo, não adianta continuar.
            datas_pagina = re.findall(r'\d{2}/\d{2}/(\d{4})', soup.get_text(" ", strip=True))
            if datas_pagina and all(int(a) < int(ano) for a in datas_pagina):
                logger.info(
                    f"Página {pagina} já contém apenas datas anteriores a {ano}. "
                    f"Encerrando paginação (boletim provavelmente não existe ou mudou de local)."
                )
                break

        return None

    @staticmethod
    def find_xlsx_url(html, mes_str, ano):

        padrao = re.compile(
            rf'(?:https?:)?//[^\s"\'<>]*previsao[_-]de[_-]safra-por[_-]produto-{re.escape(mes_str)}-{ano}\.xlsx?'
            rf'|/[^\s"\'<>]*previsao[_-]de[_-]safra-por[_-]produto-{re.escape(mes_str)}-{ano}\.xlsx?',
            re.IGNORECASE
        )
        match = padrao.search(html)
        if not match:
            return None

        url = match.group(0)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.gov.br" + url
        return url


    @staticmethod
    def _meses_pt_extenso():
        return {
            'jan': 'janeiro', 'fev': 'fevereiro', 'mar': 'março', 'abr': 'abril',
            'mai': 'maio', 'jun': 'junho', 'jul': 'julho', 'ago': 'agosto',
            'set': 'setembro', 'out': 'outubro', 'nov': 'novembro', 'dez': 'dezembro',
        }

    def _find_levantamento_url(self, html, mes_str, ano):

        soup = BeautifulSoup(html, 'html.parser')
        mes_num = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
                   'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}[mes_str]

        candidatos = soup.find_all('a', href=True)
        for link in candidatos:
            href = link['href']
            if '/boletim-da-safra-de-graos/' not in href or href.rstrip('/').count('/') < 8:
                continue
            # A data de publicação normalmente aparece perto do link, no
            # bloco de resumo do item de listagem.
            container = link.find_parent(['article', 'li', 'div']) or link
            texto_proximo = container.get_text(" ", strip=True)
            data_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', texto_proximo)
            if not data_match:
                continue
            dia, mes_pub, ano_pub = data_match.groups()
            if int(mes_pub) == mes_num and int(ano_pub) == int(ano):
                url = href if href.startswith('http') else f"https://www.gov.br{href}"
                logger.info(f"Levantamento localizado para {mes_str.upper()}/{ano}: {url}")
                return url
        return None

    def _find_tabela_dados_url(self, html):
        """Dentro da subpágina do levantamento, acha o link 'Tabela de dados'."""
        soup = BeautifulSoup(html, 'html.parser')
        for link in soup.find_all('a', href=True):
            texto = link.get_text(strip=True).lower()
            if 'tabela de dados' in texto or 'tabela-de-dados' in link['href'].lower():
                href = link['href']
                return href if href.startswith('http') else f"https://www.gov.br{href}"
        return None

    def find_xlsx_url_via_navegacao(self, mes_str, ano):

        url_levantamento = self._find_levantamento_url_paginado(mes_str, ano)
        if not url_levantamento:
            logger.warning(f"Nenhuma subpágina de levantamento encontrada para {mes_str.upper()}/{ano}.")
            return None

        html_levantamento = self._fetch_generic_html(url_levantamento)
        url_tabela = self._find_tabela_dados_url(html_levantamento)
        if not url_tabela:
            logger.warning(f"Subpágina 'Tabela de dados' não encontrada em {url_levantamento}.")
            return None

        if re.search(r'\.xlsx?$', url_tabela, re.IGNORECASE):
            logger.info(f"Link 'Tabela de dados' já aponta diretamente para a planilha: {url_tabela}")
            return url_tabela

        html_tabela = self._fetch_generic_html(url_tabela)
        xlsx_url = self.find_xlsx_url(html_tabela, mes_str, ano)
        if not xlsx_url:
            # última tentativa: qualquer .xls/.xlsx na página, mesmo que o nome
            # não bata 100% com o padrão previsao_de_safra-por_produto-*
            match = re.search(r'(?:https?:)?//[^\s"\'<>]*\.xlsx?|/[^\s"\'<>]*\.xlsx?', html_tabela, re.IGNORECASE)
            if match:
                url = match.group(0)
                xlsx_url = ("https:" + url) if url.startswith("//") else (
                    "https://www.gov.br" + url if url.startswith("/") else url
                )
                logger.warning(f"Planilha encontrada por padrão genérico (nome fora do esperado): {xlsx_url}")
        return xlsx_url

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _fetch_generic_html(self, url):
        logger.info(f"Navegando para: {url}")
        time.sleep(1)
        response = requests.get(url, headers=self.headers, timeout=20)
        if response.status_code != 200:
            logger.warning(f"Falha ao acessar {url} (Status {response.status_code}). Tentando novamente...")
        response.raise_for_status()
        return response.text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _download_xlsx(self, xlsx_url):
        logger.info(f"Baixando planilha de Safra: {xlsx_url}")
        time.sleep(1)
        response = requests.get(xlsx_url, headers=self.headers, timeout=30)

        if response.status_code != 200:
            logger.warning(f"Falha ao baixar xlsx (Status {response.status_code}). Tentando novamente...")

        response.raise_for_status()
        return response.content

    @staticmethod
    def parse_xlsx_to_json(conteudo_binario, extensao=".xlsx"):

        engine = "xlrd" if extensao.lower() == ".xls" else "openpyxl"
        try:
            if engine == "openpyxl":
                import openpyxl
                # Carrega o workbook ignorando fórmulas e estilos visuais corrompidos
                wb = openpyxl.load_workbook(io.BytesIO(conteudo_binario), data_only=True)
                planilhas = pd.read_excel(wb, sheet_name=None, engine=engine)
            else:
                planilhas = pd.read_excel(io.BytesIO(conteudo_binario), sheet_name=None, engine=engine)
                
        except ImportError as e:
            raise ImportError(
                f"Falha ao ler planilha '{extensao}': é necessário instalar o pacote '{engine}' "
                f"(pip install {engine}). Erro original: {e}"
            )
            
        resultado = {}
        for nome_sheet, df in planilhas.items():
            df = df.where(pd.notnull(df), None)
            resultado[nome_sheet] = df.to_dict(orient="records")
        return resultado

    def fetch_crop_data(self, ano, mes_str):
        html = self._fetch_listagem_html()
        xlsx_url = self.find_xlsx_url(html, mes_str, ano)

        if not xlsx_url:
            logger.warning(
                f"Atalho direto não encontrou o xlsx de {mes_str.upper()}/{ano} na listagem "
                f"principal. Tentando navegação completa (listagem -> levantamento -> "
                f"tabela de dados)..."
            )
            xlsx_url = self.find_xlsx_url_via_navegacao(mes_str, ano)

        if not xlsx_url:
            logger.warning(f"Nenhuma planilha encontrada para {mes_str.upper()}/{ano}. Boletim pode não estar publicado.")
            return None

        conteudo = self._download_xlsx(xlsx_url)
        extensao = os.path.splitext(urlparse(xlsx_url).path)[1] or ".xlsx"

        try:
            dados = self.parse_xlsx_to_json(conteudo, extensao=extensao)
        except Exception as e:
            logger.error(f"Falha ao converter planilha ({extensao}) em JSON: {e}")
            raise

        if not dados or all(len(registros) == 0 for registros in dados.values()):
            logger.warning("Planilha baixada, mas vazia.")
            return None

        total_linhas = sum(len(registros) for registros in dados.values())
        logger.info(f"Sucesso! {total_linhas} linhas extraídas de {len(dados)} aba(s) para {mes_str.upper()}/{ano}.")

        return {"source_url": xlsx_url, "sheets": dados}

    def save_to_bronze(self, data, ano, mes_str):
        if not data:
            return False

        file_name = f"conab_safra_{ano}_{mes_str}.json"
        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        sucesso = connector.save_json(data, "bronze", "conab", file_name)

        if sucesso:
            logger.info("JSON da CONAB salvo com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o arquivo JSON da CONAB no conector.")
            return False

    def run(self):
        logger.info(f"=== Iniciando Extração CONAB para: {self.mes.upper()}/{self.ano} ===")

        try:
            payload = self.fetch_crop_data(self.ano, self.mes)
            if payload:
                return self.save_to_bronze(payload, self.ano, self.mes)
            else:
                msg_erro = "Dados da CONAB indisponíveis para o período."
                logger.error(msg_erro)
                notifier.send_message(f"⚠️ *Extrator CONAB*\n{msg_erro}", "warning")
                return False
        except Exception as e:
            error_msg = f"Esgotadas as tentativas de extração. Erro final: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator CONAB*\n{error_msg}", "error")
            return False
        finally:
            logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = CONABExtractor()
    sucesso = extractor.run()
    if sucesso:
        notifier.send_message("✅ *Extrator CONAB*\nIngestão do JSON do Agronegócio concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)