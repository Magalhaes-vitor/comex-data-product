import os
import time
import logging
import json
import requests
from datetime import datetime, timedelta
import calendar

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BacenExtractor:
    def __init__(self):
        # Endpoint oficial OData do Banco Central para cotação do dólar por período
        self.api_base_url = (
            "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
            "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        )
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        # Estrutura local simulando a Camada Bronze para o Bacen
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.bronze_dir = os.path.join(project_root, "data", "bronze", "bacen")
        os.makedirs(self.bronze_dir, exist_ok=True)
        
        self.meses_pt = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

    def get_target_period_dates(self):
        ''' regra de negócio atual'''
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

        # Descobre o último dia do mês alvo dinamicamente para cobrir o período cheio
        _, ultimo_dia = calendar.monthrange(ano_alvo, mes_alvo_num)
        
        # Formato exigido pela API do Bacen: 'MM-DD-YYYY'
        data_inicial = f"{mes_alvo_num:02d}-01-{ano_alvo}"
        data_final = f"{mes_alvo_num:02d}-{ultimo_dia:02d}-{ano_alvo}"
        
        mes_alvo_str = self.meses_pt[mes_alvo_num - 1]
        
        logger.info(f"Regra de Negócio: Intervalo cambial definido para: 01/{mes_alvo_num:02d}/{ano_alvo} até {ultimo_dia:02d}/{mes_alvo_num:02d}/{ano_alvo}")
        return data_inicial, data_final, str(ano_alvo), mes_alvo_str

    def fetch_ptax_data(self, data_inicial, data_final):
        """
        Consome a API do Banco Central utilizando parâmetros de query string controlados.
        """
        params = {
            "@dataInicial": f"'{data_inicial}'",
            "@dataFinalCotacao": f"'{data_final}'",
            "$top": 100,
            "$format": "json"
        }
        
        logger.info(f"Solicitando dados de câmbio ao Bacen Olinda API...")
        try:
            # Ética de extração: timeout e rate limit preventivo
            time.sleep(1)
            response = requests.get(self.api_base_url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            # Validação inicial simples do payload
            if "value" in data and len(data["value"]) > 0:
                logger.info(f"Sucesso! {len(data['value'])} registros de cotação diária localizados.")
                return data
            else:
                logger.warning("A API respondeu com sucesso, mas nenhum dado foi retornado para o período.")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Falha ao consumir API do Banco Central: {e}")
            return None

    def save_to_bronze(self, data, ano, mes):
        if not data:
            return False
            
        file_name = f"bacen_ptax_{ano}_{mes}.json"
        file_path = os.path.join(self.bronze_dir, file_name)
        
        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"JSON salvo com sucesso em: {file_path}")
            return True
        except IOError as e:
            logger.error(f"Erro de I/O ao salvar o arquivo JSON: {e}")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: Banco Central (PTAX) ===")
        data_ini, data_fim, ano, mes = self.get_target_period_dates()
        
        payload = self.fetch_ptax_data(data_ini, data_fim)
        if payload:
            self.save_to_bronze(payload, ano, mes)
        else:
            logger.error("Pipeline interrompido: Ingestão cambial indisponível.")
        logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = BacenExtractor()
    extractor.run()