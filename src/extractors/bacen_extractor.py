import os
import sys
import time
import logging
import json
import requests
from datetime import datetime
import calendar
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import Notifier

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BacenExtractor:
    def __init__(self):
        self.api_base_url = (
            "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
            "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        )
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.bronze_dir = os.path.join(project_root, "data", "bronze", "bacen")
        os.makedirs(self.bronze_dir, exist_ok=True)
        
        self.meses_pt = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

    def get_target_period_dates(self):
        hoje = datetime.now()
        dia_atual = hoje.day
        mes_atual = hoje.month
        ano_atual = hoje.year

        if dia_atual <= 15:
            mes_alvo_num = mes_atual - 2
        else:
            mes_alvo_num = mes_atual - 1

        ano_alvo = ano_atual
        if mes_alvo_num <= 0:
            mes_alvo_num += 12
            ano_alvo -= 1

        _, ultimo_dia = calendar.monthrange(ano_alvo, mes_alvo_num)
        
        data_inicial = f"{mes_alvo_num:02d}-01-{ano_alvo}"
        data_final = f"{mes_alvo_num:02d}-{ultimo_dia:02d}-{ano_alvo}"
        mes_alvo_str = self.meses_pt[mes_alvo_num - 1]
        
        logger.info(f"Regra de Negócio: Intervalo cambial definido para: 01/{mes_alvo_num:02d}/{ano_alvo} até {ultimo_dia:02d}/{mes_alvo_num:02d}/{ano_alvo}")
        return data_inicial, data_final, str(ano_alvo), mes_alvo_str

    
    # Tenta até 3 vezes. Espera 2s, depois 4s, depois desiste se continuar a falhar.
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True
    )
    def fetch_ptax_data(self, data_inicial, data_final):
        params = {
            "@dataInicial": f"'{data_inicial}'",
            "@dataFinalCotacao": f"'{data_final}'",
            "$top": 100,
            "$format": "json"
        }
        
        logger.info(f"Solicitando dados de câmbio ao Bacen Olinda API...")
        
        
        time.sleep(1)
        response = requests.get(self.api_base_url, headers=self.headers, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"Falha na API (Status {response.status_code}). O Tenacity tentará novamente se possível...")
        
        response.raise_for_status() # Lança a exceção que engatilha o retry
        
        data = response.json()
        if "value" in data and len(data["value"]) > 0:
            logger.info(f"Sucesso! {len(data['value'])} registos de cotação diária localizados.")
            return data
        else:
            logger.warning("A API respondeu com sucesso, mas nenhum dado foi retornado para o período.")
            return None

    def save_to_bronze(self, data, ano, mes):
        if not data:
            return False
            
        file_name = f"bacen_ptax_{ano}_{mes}.json"
        file_path = os.path.join(self.bronze_dir, file_name)
        
        logger.info(f"Iniciando ingestão do ficheiro (Camada Bronze): {file_name}")
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"JSON salvo com sucesso em: {file_path}")
            return True
        except IOError as e:
            logger.error(f"Erro de I/O ao salvar o ficheiro JSON: {e}")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: Banco Central (PTAX) ===")
        data_ini, data_fim, ano, mes = self.get_target_period_dates()
        
        try:
            payload = self.fetch_ptax_data(data_ini, data_fim)
            if payload:
                self.save_to_bronze(payload, ano, mes)
            else:
                logger.error("Pipeline interrompido: Ingestão cambial indisponível.")
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro final: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
                
            Notifier.send_alert("Bacen Extractor (PTAX)", error_msg)
            
        logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = BacenExtractor()
    extractor.run()