import os
import json
import logging
import pandas as pd
from pydantic import ValidationError

import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(project_root)
from src.models.contracts import CotacaoBacen

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BacenCleaner:
    def __init__(self, ano, mes):
        self.ano = ano
        self.mes = mes
        self.bronze_path = os.path.join(project_root, "data", "bronze", "bacen", f"bacen_ptax_{ano}_{mes}.json")
        self.silver_dir = os.path.join(project_root, "data", "silver", "bacen")
        os.makedirs(self.silver_dir, exist_ok=True)

    def extract_and_clean(self):
        logger.info(f"Iniciando leitura e validação do JSON: {self.bronze_path}")
        
        if not os.path.exists(self.bronze_path):
            logger.error("Arquivo da camada Bronze não encontrado.")
            return False

        with open(self.bronze_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cotacoes = data.get("value", [])
        if not cotacoes:
            logger.error("Nenhum dado de cotação encontrado no arquivo JSON.")
            return False

        linhas_validadas = []
        
        for item in cotacoes:
            try:
                # Valida e limpa os dados usando o nosso Contrato
                obj_valido = CotacaoBacen(
                    data_cotacao=item.get("dataHoraCotacao"),
                    cotacao_compra=item.get("cotacaoCompra"),
                    cotacao_venda=item.get("cotacaoVenda")
                )
                linhas_validadas.append(obj_valido.model_dump())
            except ValidationError as e:
                logger.error(f"DATA DRIFT BLOQUEADO! Falha ao validar cotação. Detalhes: {e}")
                return False

        if linhas_validadas:
            # Converte para DataFrame e salva como Parquet
            df = pd.DataFrame(linhas_validadas)
            
            # Garante que a coluna de data seja do tipo datetime no Parquet para facilitar análises
            df['data_cotacao'] = pd.to_datetime(df['data_cotacao'])
            
            silver_file = os.path.join(self.silver_dir, f"bacen_ptax_{self.ano}_{self.mes}.parquet")
            df.to_parquet(silver_file, index=False)
            
            logger.info(f"Sucesso! {len(df)} dias de cotação validados.")
            logger.info(f"Arquivo Silver gerado em: {silver_file}")
            return True
            
        return False

if __name__ == "__main__":
    cleaner = BacenCleaner(ano="2026", mes="mai")
    cleaner.extract_and_clean()