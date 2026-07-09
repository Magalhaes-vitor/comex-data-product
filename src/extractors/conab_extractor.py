import os
import io
import re
import sys
import time
import logging
import requests
import pandas as pd
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
    """Retenta apenas falhas de conexão, timeout ou erros 5xx."""
    if isinstance(exception, requests.exceptions.HTTPError):
        status = exception.response.status_code if exception.response is not None else None
        return status is not None and status >= 500
    return isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))

class CONABExtractor:
    def __init__(self):
        # URL de listagem dos boletins de safra (planilhas .xlsx)
        self.listagem_url = (
            "https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/"
            "safras/safra-de-graos/boletim-da-safra-de-graos"
        )
        self.headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _fetch_listagem_html(self):
        """Baixa o HTML da página de listagem da CONAB."""
        logger.info("Buscando página de listagem de boletins da CONAB...")
        time.sleep(1)
        response = requests.get(self.listagem_url, headers=self.headers, timeout=20)
        
        if response.status_code != 200:
            logger.warning(f"Falha ao acessar listagem (Status {response.status_code}). Tentando novamente...")
            
        response.raise_for_status()
        return response.text

    @staticmethod
    def find_xlsx_url(html, mes_str, ano):
        """Localiza o link direto do arquivo .xlsx via regex (padrão: mês/ano)."""
        padrao = re.compile(
            rf'(?:https?:)?//[^\s"\'<>]*previsao_de_safra-por_produto-{re.escape(mes_str)}-{ano}\.xlsx'
            rf'|/[^\s"\'<>]*previsao_de_safra-por_produto-{re.escape(mes_str)}-{ano}\.xlsx',
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _download_xlsx(self, xlsx_url):
        """Baixa o conteúdo binário da planilha de safra."""
        logger.info(f"Baixando planilha de Safra: {xlsx_url}")
        time.sleep(1)
        response = requests.get(xlsx_url, headers=self.headers, timeout=30)
        
        if response.status_code != 200:
            logger.warning(f"Falha ao baixar xlsx (Status {response.status_code}). Tentando novamente...")
            
        response.raise_for_status()
        return response.content

    @staticmethod
    def parse_xlsx_to_json(conteudo_binario):
        """Converte as abas do Excel para um dicionário JSON serializável."""
        planilhas = pd.read_excel(io.BytesIO(conteudo_binario), sheet_name=None)
        resultado = {}
        for nome_sheet, df in planilhas.items():
            df = df.where(pd.notnull(df), None)
            resultado[nome_sheet] = df.to_dict(orient="records")
        return resultado

    def fetch_crop_data(self, ano, mes_str):
        """Executa o fluxo de raspagem, download e conversão."""
        html = self._fetch_listagem_html()
        xlsx_url = self.find_xlsx_url(html, mes_str, ano)

        if not xlsx_url:
            logger.warning(f"Nenhuma planilha encontrada para {mes_str.upper()}/{ano}. Boletim pode não estar publicado.")
            return None

        conteudo = self._download_xlsx(xlsx_url)

        try:
            dados = self.parse_xlsx_to_json(conteudo)
        except Exception as e:
            logger.error(f"Falha ao converter xlsx em JSON: {e}")
            raise

        if not dados or all(len(registros) == 0 for registros in dados.values()):
            logger.warning("Planilha baixada, mas vazia.")
            return None

        total_linhas = sum(len(registros) for registros in dados.values())
        logger.info(f"Sucesso! {total_linhas} linhas extraídas de {len(dados)} aba(s) para {mes_str.upper()}/{ano}.")

        return {
            "source_url": xlsx_url,
            "sheets": dados
        }

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
        logger.info("=== Iniciando Pipeline de Extração: CONAB (Agronegócio) ===")

        periodo = DateRules.get_target_period()
        ano = str(periodo["ano"])
        mes_str = periodo["mes_str"]

        logger.info(f"Regra de Negócio: Extração Agrícola definida para referência: {mes_str.upper()}/{ano}")

        try:
            payload = self.fetch_crop_data(ano, mes_str)
            
            if payload:
                return self.save_to_bronze(payload, ano, mes_str)
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