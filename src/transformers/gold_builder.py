import os
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GoldBuilder:
    def __init__(self, ano, mes):
        self.ano = ano
        self.mes = mes
        
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        
        # Caminhos de origem (Camada Silver)
        self.silver_aps_path = os.path.join(project_root, "data", "silver", "aps", f"aps_movimentacao_{ano}_{mes}.parquet")
        self.silver_bacen_path = os.path.join(project_root, "data", "silver", "bacen", f"bacen_ptax_{ano}_{mes}.parquet")
        
        # Caminho de destino (Camada Gold)
        self.gold_dir = os.path.join(project_root, "data", "gold", "market_intelligence")
        os.makedirs(self.gold_dir, exist_ok=True)

    def build(self):
        logger.info("=== Iniciando cruzamento de dados para a Camada Gold ===")
        
        if not os.path.exists(self.silver_aps_path) or not os.path.exists(self.silver_bacen_path):
            logger.error("Arquivos da camada Silver não encontrados. Execute os cleaners primeiro.")
            return False

        # 1. Leitura dos dados limpos em altíssima velocidade (Parquet)
        logger.info("Lendo tabelas da camada Silver...")
        df_aps = pd.read_parquet(self.silver_aps_path)
        df_bacen = pd.read_parquet(self.silver_bacen_path)

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

        # 4. Salvando a Tabela Fato na Camada Gold
        gold_file = os.path.join(self.gold_dir, f"fato_movimentacao_cambio_{self.ano}_{self.mes}.parquet")
        df_gold.to_parquet(gold_file, index=False)
        
        logger.info(f"Sucesso! Tabela Fato (Gold) gerada com {len(df_gold)} registros.")
        logger.info(f"Arquivo salvo em: {gold_file}")
        logger.info("=== Processo Finalizado ===")
        
        return True

if __name__ == "__main__":
    builder = GoldBuilder(ano="2026", mes="mai")
    builder.build()