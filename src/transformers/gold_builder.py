import os
import sys
import logging
import pandas as pd

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.date_rules import DateRules
from src.utils.storage import connector
from src.utils.notifier import notifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GoldBuilder:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        
        # Nomes dos arquivos padronizados
        self.aps_filename = f"aps_movimentacao_{self.ano}_{self.mes}.parquet"
        self.bacen_filename = f"bacen_ptax_{self.ano}_{self.mes}.parquet"
        self.gold_filename = f"fato_movimentacao_cambio_{self.ano}_{self.mes}.parquet"

    def build(self):
        logger.info("=== Iniciando cruzamento de dados para a Camada Gold ===")
        
        # 1. Leitura dos dados limpos em altíssima velocidade via Conector
        logger.info("Lendo tabelas da camada Silver via DataLakeConnector...")
        df_aps = connector.read_parquet("silver", "aps", self.aps_filename)
        df_bacen = connector.read_parquet("silver", "bacen", self.bacen_filename)

        if df_aps is None or df_bacen is None:
            msg = "Arquivos da camada Silver não encontrados. Execute os cleaners primeiro."
            logger.error(msg)
            notifier.send_message(f"⚠️ *Gold Builder Abortado*\n{msg}", "warning")
            return False

        # 2. Inteligência de Negócio: Cálculo da PTAX Média do período
        ptax_media_compra = df_bacen['cotacao_compra'].mean()
        ptax_media_venda = df_bacen['cotacao_venda'].mean()
        
        logger.info(f"Indicador Macroeconômico calculado: PTAX Média de Venda = R$ {ptax_media_venda:.4f}")

        # 3. Enriquecimento (Join/Merge lógico) da tabela do Porto
        logger.info("Enriquecendo dados portuários com indicadores cambiais...")
        df_gold = df_aps.copy()
        df_gold['ptax_media_compra'] = round(ptax_media_compra, 4)
        df_gold['ptax_media_venda'] = round(ptax_media_venda, 4)
        
        # Adiciona um metadado de linhagem de dados (Data Lineage)
        df_gold['fonte_cambio'] = 'Bacen API - Média Mensal'

        # 4. Salvando a Tabela Fato na Camada Gold via Conector
        sucesso = connector.save_parquet(df_gold, "gold", "market_intelligence", self.gold_filename)
        
        if sucesso:
            logger.info(f"Sucesso! Tabela Fato (Gold) gerada com {len(df_gold)} registros.")
            logger.info("=== Processo Finalizado ===")
            return True
        else:
            logger.error("Falha ao salvar a Tabela Gold no conector.")
            return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Alvo do Gold Builder definido para: {mes_alvo.upper()}/{ano_alvo}")
    
    builder = GoldBuilder(ano=ano_alvo, mes=mes_alvo)
    sucesso = builder.build()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline Gold Concluído ({mes_alvo.upper()}/{ano_alvo})*\nTabela Fato de Inteligência de Mercado gerada com sucesso no S3!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Gold Builder ({mes_alvo.upper()}/{ano_alvo})*\nVerifique os logs de processamento.", "error")
        sys.exit(1)