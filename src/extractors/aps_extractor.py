import os
import re
import time
import logging
import requests
import urllib3
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

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
        
        # Mapeamento de meses em PT-BR para cruzar com o site
        self.meses_pt = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

    def get_target_period(self):
        """
        Aplica a Regra de Negócio de defasagem de publicação:
        Se dia atual <= 15: busca o relatório de 2 meses atrás.
        Se dia atual > 15: busca o relatório de 1 mês atrás.
        """
        hoje = datetime.now()
        dia_atual = hoje.day
        mes_atual = hoje.month
        ano_atual = hoje.year

        if dia_atual <= 15:
            mes_alvo_num = mes_atual - 2
        else:
            mes_alvo_num = mes_atual - 1

        ano_alvo = ano_atual
        # Tratamento para virada de ano (ex: Janeiro buscando Novembro)
        if mes_alvo_num <= 0:
            mes_alvo_num += 12
            ano_alvo -= 1

        mes_alvo_str = self.meses_pt[mes_alvo_num - 1]
        logger.info(f"Regra de Negócio: Dia {dia_atual}. Alvo da extração definido para: {mes_alvo_str.upper()}/{ano_alvo}")
        
        return str(ano_alvo), mes_alvo_str

    def get_specific_pdf_link(self, target_year, target_month):
        logger.info(f"Varrendo o portal da APS em busca do ID correspondente...")
        try:
            time.sleep(2)
            response = requests.get(self.base_url, headers=self.headers, verify=False, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            links = soup.find_all('a', href=True)
            
            for link in links:
                href = link['href']
                if 'doc_codesp_pdf_site.asp?id=' in href:
                    text_mes = link.get_text(strip=True).lower()
                    
                    # Função avançada do BeautifulSoup: busca o último texto que parece um ano (202X) antes deste botão
                    # Isso garante que não peguemos o mês 'mai' do ano errado.
                    ano_elemento = link.find_previous(string=re.compile(r'^20\d{2}$'))
                    
                    if ano_elemento:
                        ano_encontrado = str(ano_elemento).strip()
                        
                        # Cruza as informações extraídas do HTML com o nosso Alvo Matemático
                        if ano_encontrado == target_year and text_mes == target_month:
                            pdf_url = href if href.startswith('http') else f"https://www.portodesantos.com.br{href}"
                            logger.info(f"Link validado! Relatório {target_month.upper()}/{target_year} encontrado na URL: {pdf_url}")
                            return pdf_url

            logger.warning(f"O relatório de {target_month.upper()}/{target_year} ainda não está disponível no site.")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Falha de comunicação com o portal da APS: {e}")
            return None

    def download_pdf(self, pdf_url, ano, mes):
        if not pdf_url:
            return False
            
        parsed_url = urlparse(pdf_url)
        doc_id = parse_qs(parsed_url.query).get('id', ['unknown'])[0]
        
        # Agora o nome do arquivo fica semântico e perfeito para a Camada Bronze!
        file_name = f"aps_mensario_{ano}_{mes}_id_{doc_id}.pdf"
        file_path = os.path.join(self.bronze_dir, file_name)

        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        try:
            time.sleep(2)
            response = requests.get(pdf_url, headers=self.headers, verify=False, stream=True, timeout=30)
            response.raise_for_status()

            with open(file_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            
            logger.info(f"Download concluído com sucesso! Salvo em: {file_path}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao baixar o PDF: {e}")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: Porto de Santos ===")
        
        ano_alvo, mes_alvo = self.get_target_period()
        pdf_link = self.get_specific_pdf_link(ano_alvo, mes_alvo)
        
        if pdf_link:
            self.download_pdf(pdf_link, ano_alvo, mes_alvo)
        else:
            logger.error(f"Pipeline interrompido: Dado de origem ({mes_alvo}/{ano_alvo}) não localizado.")
            
        logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = APSExtractor()
    extractor.run()