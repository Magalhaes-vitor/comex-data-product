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
        
        # Nomes dos arquivos de origem (Silver)
        self.aps_filename = f"aps_movimentacao_{self.ano}_{self.mes}.parquet"
        self.bacen_filename = f"bacen_ptax_{self.ano}_{self.mes}.parquet"
        self.mdic_filename = f"mdic_comex_{self.ano}_{self.mes}.parquet"

        # Nomes dos arquivos de destino (Gold)
        self.gold_aps_filename = f"fato_movimentacao_cambio_{self.ano}_{self.mes}.parquet"
        self.gold_mdic_filename = f"fato_balanca_mdic_{self.ano}_{self.mes}.parquet"

    def build(self):
        logger.info("=== Iniciando cruzamento de dados para a Camada Gold ===")
        
        # 1. Leitura dos dados limpos da camada Silver via Conector
        logger.info("Lendo tabelas da camada Silver via DataLakeConnector...")
        df_aps = connector.read_parquet("silver", "aps", self.aps_filename)
        df_bacen = connector.read_parquet("silver", "bacen", self.bacen_filename)
        df_mdic = connector.read_parquet("silver", "mdic", self.mdic_filename)

        if df_aps is None or df_bacen is None or df_mdic is None:
            msg = "Arquivos da camada Silver não encontrados. Execute os cleaners primeiro."
            logger.error(msg)
            notifier.send_message(f"⚠️ *Gold Builder Abortado*\n{msg}", "warning")
            return False

        # 2. Inteligência de Negócio: Cálculo da PTAX Média do período
        ptax_media_compra = df_bacen['cotacao_compra'].mean()
        ptax_media_venda = df_bacen['cotacao_venda'].mean()
        
        logger.info(f"Indicador Macroeconômico: PTAX Média de Venda = R$ {ptax_media_venda:.4f}")

        # 3. Construção da Tabela Fato 1: Porto de Santos
        logger.info("Enriquecendo dados portuários (APS)...")
        df_gold_aps = df_aps.copy()
        df_gold_aps['ptax_media_compra'] = round(ptax_media_compra, 4)
        df_gold_aps['ptax_media_venda'] = round(ptax_media_venda, 4)
        df_gold_aps['fonte_cambio'] = 'Bacen API - Média Mensal'

        sucesso_aps = connector.save_parquet(df_gold_aps, "gold", "market_intelligence", self.gold_aps_filename)

        # 4. Construção da Tabela Fato 2: Balança Comercial (MDIC)
        logger.info("Enriquecendo dados da Balança Comercial (MDIC)...")
        df_gold_mdic = df_mdic.copy()
        df_gold_mdic['ptax_media_venda'] = round(ptax_media_venda, 4)
        # Conversão monetária automática usando a PTAX do período
        df_gold_mdic['valor_fob_brl'] = round(df_gold_mdic['valor_fob_usd'] * ptax_media_venda, 2)
        df_gold_mdic['fonte_cambio'] = 'Bacen API - Média Mensal'

        sucesso_mdic = connector.save_parquet(df_gold_mdic, "gold", "market_intelligence", self.gold_mdic_filename)
        
        if sucesso_aps and sucesso_mdic:
            logger.info(f"Sucesso! Tabela Fato APS gerada com {len(df_gold_aps)} registros.")
            logger.info(f"Sucesso! Tabela Fato MDIC gerada com {len(df_gold_mdic)} registros.")
            logger.info("=== Processo Finalizado ===")
            return True
        else:
            logger.error("Falha ao salvar as Tabelas Gold no conector.")
            return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Alvo do Gold Builder definido para: {mes_alvo.upper()}/{ano_alvo}")
    
    builder = GoldBuilder(ano=ano_alvo, mes=mes_alvo)
    sucesso = builder.build()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline Gold Concluído ({mes_alvo.upper()}/{ano_alvo})*\nTabelas Fato de Inteligência de Mercado geradas com sucesso no S3!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Gold Builder ({mes_alvo.upper()}/{ano_alvo})*\nVerifique os logs de processamento.", "error")
        sys.exit(1)