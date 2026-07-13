import os
import re
import sys
import ssl
import time
import logging
import requests
import urllib3
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.date_rules import DateRules
from src.utils.notifier import notifier
from src.utils.storage import connector

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = urllib3.util.ssl_.create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= 0x4
        kwargs['ssl_context'] = ctx
        return super(LegacySSLAdapter, self).init_poolmanager(*args, **kwargs)

class APSExtractor:
    def __init__(self, ano=None, mes=None):
        self.base_url = "https://www.portodesantos.com.br/informacoes-operacionais/estatisticas/mensario-estatistico/"
        self.session = requests.Session()
        self.session.mount('https://', LegacySSLAdapter())
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.meses_pt = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

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
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def _fetch_page(self, url):
        """Auxiliar para fazer o get de uma página html genérica"""
        time.sleep(2)
        response = self.session.get(url, verify=False, timeout=15)
        response.raise_for_status()
        return BeautifulSoup(response.content, 'html.parser')

    def get_specific_pdf_link(self, target_year, target_month):

        logger.info(f"Buscando na tabela de mensários o período {target_month.upper()}/{target_year}...")
        soup = self._fetch_page(self.base_url)
        pdf_url = self._search_links_in_soup(soup, target_year, target_month)

        if not pdf_url:
            logger.warning(
                f"Período {target_month.upper()}/{target_year} não encontrado na tabela "
                f"de mensários (ano ausente da tabela ou mês ainda não publicado)."
            )
        return pdf_url

    def _search_links_in_soup(self, soup, target_year, target_month):

        target_year = str(target_year)
        current_year = None

        for el in soup.find_all(['th', 'a']):
            if el.name == 'th':
                texto_th = el.get_text(strip=True)
                if re.fullmatch(r'\d{4}', texto_th):
                    current_year = texto_th
                continue

            # el.name == 'a'
            if current_year != target_year:
                continue

            href = el.get('href', '')
            if 'doc_codesp_pdf_site.asp?id=' not in href and '.pdf' not in href.lower():
                continue

            text_mes = el.get_text(strip=True).lower()
            if text_mes == target_month:
                pdf_url = href if href.startswith('http') else f"https://www.portodesantos.com.br{href}"
                logger.info(f"Link validado! Relatório {target_month.upper()}/{target_year} encontrado: {pdf_url}")
                return pdf_url

        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def download_pdf(self, pdf_url, ano, mes):
        if not pdf_url:
            return False
            
        # Tenta extrair o ID para nomear o arquivo. Se não tiver ID (link direto .pdf), usa um hash
        parsed_url = urlparse(pdf_url)
        doc_id = parse_qs(parsed_url.query).get('id', ['unknown'])[0]
        if doc_id == 'unknown':
            doc_id = str(abs(hash(pdf_url)) % 10000) # Fallback para links que não usam o ASP
            
        file_name = f"aps_mensario_{ano}_{mes}_id_{doc_id}.pdf"
        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        
        time.sleep(2)
        response = self.session.get(pdf_url, verify=False, timeout=30)
        response.raise_for_status()

        sucesso = connector.save_binary(response.content, "bronze", "aps", file_name)
        
        if sucesso:
            logger.info("Download e persistência concluídos com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o arquivo binário no conector.")
            return False

    def run(self):
        logger.info(f"=== Iniciando Extração APS para: {self.mes.upper()}/{self.ano} ===")
        
        try:
            pdf_link = self.get_specific_pdf_link(self.ano, self.mes)
            if pdf_link:
                return self.download_pdf(pdf_link, self.ano, self.mes)
            else:
                logger.error(f"Pipeline interrompido: Dado de origem ({self.mes}/{self.ano}) não localizado.")
                notifier.send_message(f"⚠️ *Extrator APS*\nDado de origem ({self.mes.upper()}/{self.ano}) não localizado no portal.", "warning")
                return False
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator APS*\n{error_msg}", "error")
            return False

if __name__ == "__main__":
    extractor = APSExtractor()
    sucesso = extractor.run()
    if sucesso:
        notifier.send_message("✅ *Extrator APS*\nIngestão do PDF bruto concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)