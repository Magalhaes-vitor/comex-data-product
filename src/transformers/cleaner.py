import os
import logging
import pdfplumber
import pandas as pd
from pydantic import ValidationError

import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(project_root)
from src.models.contracts import MovimentacaoPortuaria

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class APSCleaner:
    def __init__(self, file_name, ano, mes):
        self.ano = ano
        self.mes = mes
        self.bronze_path = os.path.join(project_root, "data", "bronze", "aps", file_name)
        self.silver_dir = os.path.join(project_root, "data", "silver", "aps")
        os.makedirs(self.silver_dir, exist_ok=True)

    def extract_and_clean(self):
        logger.info(f"Iniciando parsing e validação do PDF: {self.bronze_path}")
        linhas_validadas = []
        
        with pdfplumber.open(self.bronze_path) as pdf:
            target_page = None
            
            # Varredura com Normalização de Texto e Correção de Hífen/Travessão
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    # Remove quebras de linha, junta espaços extras e normaliza os traços
                    texto_limpo = " ".join(text.split()).upper().replace("–", "-").replace("—", "-")
                    
                    # Busca exata pelo título com hífen e exclusão explícita da página de acumulado
                    if ("MOVIMENTO GERAL NO PORTO DE SANTOS - NO MÊS" in texto_limpo or 
                        "MOVIMENTO GERAL NO PORTO DE SANTOS - NO MES" in texto_limpo) and \
                       "ACUMULADO" not in texto_limpo:
                        
                        target_page = page
                        logger.info(f"Página alvo localizada com sucesso (Página {i + 1} do PDF).")
                        break
            
            if not target_page:
                logger.error("Falha Crítica: Página não encontrada mesmo com normalização de texto.")
                return False
                
            logger.info("Extraindo geometria da tabela...")
            
            # Configuração customizada para tabelas de PDF do Governo
            table_settings = {
                "vertical_strategy": "text", 
                "horizontal_strategy": "lines",
                "intersection_tolerance": 15
            }
            
            tables = target_page.extract_tables(table_settings)
            
            if not tables:
                # Fallback para a estratégia padrão caso a customizada falhe
                tables = target_page.extract_tables()
                
            if not tables:
                logger.error("Falha Crítica: Tabela estruturada não detectada na página alvo.")
                return False
                
            tabela_principal = tables[0]
            
            for row in tabela_principal:
                if not row or len(row) < 7: continue
                
                mercadoria = str(row[0]).replace('\n', ' ').strip()
                
                # Regras de exclusão de cabeçalhos e totais
                if not mercadoria or mercadoria.isupper() or mercadoria in ['Soma', 'Outros', 'TOTAL GERAL']:
                    continue
                    
                try:
                    vol_importacao = row[3] if len(row) > 3 else "0"
                    if vol_importacao and str(vol_importacao).strip() not in ['-', '', 'None']:
                        obj_imp = MovimentacaoPortuaria(
                            ano=self.ano, mes=self.mes, mercadoria=mercadoria,
                            tipo_operacao="Importação", volume_toneladas=vol_importacao
                        )
                        linhas_validadas.append(obj_imp.model_dump())
                        
                    vol_exportacao = row[6] if len(row) > 6 else "0"
                    if vol_exportacao and str(vol_exportacao).strip() not in ['-', '', 'None']:
                        obj_exp = MovimentacaoPortuaria(
                            ano=self.ano, mes=self.mes, mercadoria=mercadoria,
                            tipo_operacao="Exportação", volume_toneladas=vol_exportacao
                        )
                        linhas_validadas.append(obj_exp.model_dump())
                        
                except ValidationError as e:
                    logger.error(f"DATA DRIFT BLOQUEADO! Linha: '{mercadoria}'. Erro: {e}")
                    return False

        if linhas_validadas:
            df = pd.DataFrame(linhas_validadas)
            silver_file = os.path.join(self.silver_dir, f"aps_movimentacao_{self.ano}_{self.mes}.parquet")
            df.to_parquet(silver_file, index=False)
            
            logger.info(f"Sucesso! {len(df)} registros validados pelo Data Contract.")
            logger.info(f"Arquivo Silver gerado em: {silver_file}")
            return True
        
        logger.warning("Nenhum dado válido extraído para a camada Silver.")
        return False

if __name__ == "__main__":
    cleaner = APSCleaner(file_name="aps_mensario_2026_mai_id_163184.pdf", ano=2026, mes="mai")
    cleaner.extract_and_clean()