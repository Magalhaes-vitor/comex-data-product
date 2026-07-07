import os
import re
import sys
import time
import logging
import requests
import urllib3
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import Notifier
from src.utils.date_rules import DateRules

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class APSExtractor:
    def __init__(self):
        self.base_url = "https://www.portodesantos.com.br/informacoes-operacionais/estatisticas/mensario-estatistico/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.bronze_dir = os.path.join(project_root, "data", "bronze", "aps")
        os.makedirs(self.bronze_dir, exist_ok=True)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def get_specific_pdf_link(self, target_year, target_month):
        logger.info(f"Varrendo o portal da APS em busca do ID correspondente...")
        time.sleep(2)
        response = requests.get(self.base_url, headers=self.headers, verify=False, timeout=15)
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
        file_path = os.path.join(self.bronze_dir, file_name)

        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        
        time.sleep(2)
        response = requests.get(pdf_url, headers=self.headers, verify=False, stream=True, timeout=30)
        response.raise_for_status()

        with open(file_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        
        logger.info(f"Download concluído com sucesso! Salvo em: {file_path}")
        return True

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: Porto de Santos ===")
        
        # Consome a fonte única de verdade para regras de datas
        periodo = DateRules.get_target_period()
        ano_alvo = periodo["ano"]
        mes_alvo = periodo["mes_str"]
        
        logger.info(f"Regra de Negócio: Alvo da extração definido para: {mes_alvo.upper()}/{ano_alvo}")
        
        try:
            pdf_link = self.get_specific_pdf_link(ano_alvo, mes_alvo)
            if pdf_link:
                self.download_pdf(pdf_link, ano_alvo, mes_alvo)
            else:
                logger.error(f"Pipeline interrompido: Dado de origem ({mes_alvo}/{ano_alvo}) não localizado.")
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
                            
            Notifier.send_alert("APS Extractor (Porto de Santos)", error_msg)
            
        logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = APSExtractor()
    extractor.run()