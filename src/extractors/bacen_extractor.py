import os
import sys
import time
import logging
import requests
import calendar
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BacenExtractor:
    def __init__(self, ano=None, mes=None):
        self.api_base_url = (
            "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
            "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        )
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        # [NOVO] Desacoplamento Temporal
        if ano and mes:
            self.ano = str(ano)
            self.mes = str(mes).lower()
            meses_map = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6, 
                         'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}
            mes_num = meses_map[self.mes]
            ultimo_dia = calendar.monthrange(int(self.ano), mes_num)[1]
            # O Bacen exige formato MM-DD-YYYY
            self.data_ini = f"{mes_num:02d}-01-{self.ano}"
            self.data_fim = f"{mes_num:02d}-{ultimo_dia:02d}-{self.ano}"
        else:
            periodo = DateRules.get_target_period()
            self.ano = str(periodo["ano"])
            self.mes = periodo["mes_str"]
            self.data_ini = periodo["data_inicial"]
            self.data_fim = periodo["data_final"]

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
        
        logger.info("Solicitando dados de câmbio ao Bacen Olinda API...")
        time.sleep(1)
        response = requests.get(self.api_base_url, headers=self.headers, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"Falha na API (Status {response.status_code}). O Tenacity tentará novamente...")
        
        response.raise_for_status() 
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
        logger.info(f"Iniciando ingestão do ficheiro (Camada Bronze): {file_name}")
        sucesso = connector.save_json(data, "bronze", "bacen", file_name)
        
        if sucesso:
            logger.info("JSON salvo com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o ficheiro JSON no conector.")
            return False

    def run(self):
        logger.info(f"=== Iniciando Extração Bacen para: {self.data_ini} até {self.data_fim} ===")
        try:
            payload = self.fetch_ptax_data(self.data_ini, self.data_fim)
            if payload:
                return self.save_to_bronze(payload, self.ano, self.mes)
            else:
                msg_erro = "Pipeline interrompido: Ingestão cambial indisponível."
                logger.error(msg_erro)
                notifier.send_message(f"⚠️ *Extrator Bacen*\n{msg_erro}", "warning")
                return False
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro final: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator Bacen*\n{error_msg}", "error")
            return False
        finally:
            logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = BacenExtractor()
    sucesso = extractor.run()
    if sucesso:
        notifier.send_message("✅ *Extrator Bacen*\nIngestão do JSON cambial concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)