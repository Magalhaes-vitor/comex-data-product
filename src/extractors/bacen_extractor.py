import os
import sys
import time
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules
from src.utils.storage import connector

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
        # As dependências de pastas locais (os.makedirs, etc) foram delegadas para o conector.

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
        
        # Chamada direta para o S3 (ou Local) sem manipular caminhos manualmente
        sucesso = connector.save_json(data, "bronze", "bacen", file_name)
        
        if sucesso:
            logger.info("JSON salvo com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o ficheiro JSON no conector.")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: Banco Central (PTAX) ===")
        
        # Consome a fonte única de verdade para regras de datas
        periodo = DateRules.get_target_period()
        data_ini = periodo["data_inicial"]
        data_fim = periodo["data_final"]
        ano = str(periodo["ano"])
        mes = periodo["mes_str"]
        
        logger.info(f"Regra de Negócio: Intervalo cambial definido para: {data_ini} até {data_fim}")
        
        try:
            payload = self.fetch_ptax_data(data_ini, data_fim)
            if payload:
                return self.save_to_bronze(payload, ano, mes)
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