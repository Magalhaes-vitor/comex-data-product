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

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.storage import connector

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LegacySSLAdapter(HTTPAdapter):
    """Adaptador que força o OpenSSL a aceitar cifras antigas e ignora validações de certificado."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = urllib3.util.ssl_.create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= 0x4  # Força o parâmetro ssl.OP_LEGACY_SERVER_CONNECT
        kwargs['ssl_context'] = ctx
        return super(LegacySSLAdapter, self).init_poolmanager(*args, **kwargs)

class APSExtractor:
    def __init__(self, ano=None, mes=None):
        # Desacoplamento: usa o parâmetro se fornecido, senão usa a regra de negócio oficial
        from src.utils.date_rules import DateRules

        if ano and mes:
            # mes_str precisa ser a abreviação em pt-br (ex: "mai")
            period = {
                "ano": str(ano),
                "mes_str": DateRules.MESES_PT[int(mes) - 1]
            }
        else:
            period = DateRules.get_target_period()

        self.target_year = period["ano"]
        self.target_month = period["mes_str"]

        self.base_url = "https://www.portodesantos.com.br/informacoes-operacionais/estatisticas/mensario-estatistico/"
        
        # Centraliza as requisições em uma sessão blindada contra falhas de SSL/TLS
        self.session = requests.Session()
        self.session.mount('https://', LegacySSLAdapter())
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        
        self.meses_pt = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def get_specific_pdf_link(self, target_year, target_month):
        logger.info(f"Varrendo o portal da APS em busca do ID correspondente...")
        time.sleep(2)
        
        # Realiza a chamada repassando o parâmetro de desativação de checagem
        response = self.session.get(self.base_url, verify=False, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        links = soup.find_all('a', href=True)
        
        for link in links:
            href = link['href']
            if 'doc_codesp_pdf_site.asp?id=' in href:
                text_mes = link.get_text(strip=True).lower()
                ano_elemento = link.find_previous(string=re.compile(r'^20\d{2}$'))
                
                if ano_elemento:
                    ano_encontrado = str(ano_elemento).strip()
                    
                    if ano_encontrado == target_year and text_mes == target_month:
                        pdf_url = href if href.startswith('http') else f"https://www.portodesantos.com.br{href}"
                        logger.info(f"Link validado! Relatório {target_month.upper()}/{target_year} encontrado na URL: {pdf_url}")
                        return pdf_url

        logger.warning(f"O relatório de {target_month.upper()}/{target_year} ainda não está disponível no site.")
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
            
        parsed_url = urlparse(pdf_url)
        doc_id = parse_qs(parsed_url.query).get('id', ['unknown'])[0]
        
        file_name = f"aps_mensario_{ano}_{mes}_id_{doc_id}.pdf"
        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        
        time.sleep(2)
        response = self.session.get(pdf_url, verify=False, timeout=30)
        response.raise_for_status()

        # O conector gerencia o destino final baseado no .env (Local ou S3)
        sucesso = connector.save_binary(response.content, "bronze", "aps", file_name)
        
        if sucesso:
            logger.info("Download e persistência concluídos com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o arquivo binário no conector.")
            return False

    def run(self):
        logger.info(f"=== Iniciando Pipeline de Extração APS para: {self.target_month.upper()}/{self.target_year} ===")
        
        try:
            pdf_link = self.get_specific_pdf_link(self.target_year, self.target_month)
            if pdf_link:
                return self.download_pdf(pdf_link, self.target_year, self.target_month)
            else:
                logger.error(f"Pipeline interrompido: Dado de origem ({self.target_month}/{self.target_year}) não localizado.")
                notifier.send_message(f"⚠️ *Extrator APS*\nDado de origem ({self.target_month.upper()}/{self.target_year}) não localizado no portal.", "warning")
                return False
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator APS*\n{error_msg}", "error")
            return False

if __name__ == "__main__":
    # Teste de execução direta: usará o DateRules (mês corrente defasado)
    extractor = APSExtractor()
    sucesso = extractor.run()
    
    if sucesso:
        notifier.send_message("✅ *Extrator APS*\nIngestão do PDF bruto concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)