import os
import sys
import glob
import logging
import pdfplumber
import pandas as pd
from pydantic import ValidationError

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.models.contracts import MovimentacaoPortuaria
from src.utils.date_rules import DateRules
from src.utils.quarantine import QuarantineManager
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class APSCleaner:
    def __init__(self, file_name, ano, mes):
        self.file_name = file_name
        self.ano = str(ano)
        self.mes = str(mes)
        self.bronze_path = connector.obtain_file_path("bronze", "aps", self.file_name)
        self.silver_dir = os.path.join(project_root, "data", "silver", "aps")
        os.makedirs(self.silver_dir, exist_ok=True)

    def extract_and_clean(self):
        logger.info(f"Iniciando parsing e validação do PDF: {self.bronze_path}")
        
        quarantine = QuarantineManager(
            pipeline_name="aps",
            ano=self.ano,
            mes=self.mes,
            arquivo_origem=self.file_name
        )
        
        linhas_validadas = []
        total_linhas_tentadas = 0
        volume_validado_toneladas = 0.0
        volume_total_oficial_pdf = 0.0
        
        tabela_principal = None
        numero_pagina_alvo = 0
        idx_imp = None
        idx_exp = None
        
        with pdfplumber.open(self.bronze_path) as pdf:
            # Força o extrator a respeitar as linhas de grade do PDF
            table_settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
            
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                
                texto_limpo = " ".join(text.split()).upper().replace("–", "-").replace("—", "-")
                if ("MOVIMENTO GERAL NO PORTO DE SANTOS" in texto_limpo) and "ACUMULADO" not in texto_limpo:
                    
                    tables = page.extract_tables(table_settings)
                    if not tables: continue
                    
                    for table in tables:
                        temp_idx_imp = None
                        temp_idx_exp = None
                        has_total_geral = False
                        
                        for row in table:
                            if not row: continue
                            row_clean = [str(c).strip().upper() if c else "" for c in row]
                            
                            # 1. Mapeamento Dinâmico de Colunas pelo Cabeçalho
                            if temp_idx_imp is None and row_clean.count('TOTAL') >= 2:
                                totals_found = 0
                                for c_idx, cell_val in enumerate(row_clean):
                                    if cell_val == 'TOTAL':
                                        totals_found += 1
                                        if totals_found == 1: temp_idx_imp = c_idx
                                        elif totals_found == 2: temp_idx_exp = c_idx
                                        
                            # 2. Checagem Estrutural: Existe o Total Geral nesta tabela?
                            val_0 = str(row[0]).strip().upper() if row[0] else ""
                            val_1 = str(row[1]).strip().upper() if len(row) > 1 and row[1] else ""
                            if val_0 == 'TOTAL GERAL' or val_1 == 'TOTAL GERAL':
                                has_total_geral = True
                                
                        if has_total_geral and temp_idx_imp is not None:
                            tabela_principal = table
                            idx_imp = temp_idx_imp
                            idx_exp = temp_idx_exp
                            numero_pagina_alvo = i + 1
                            break # Quebra o loop das tabelas
                            
                    if tabela_principal:
                        break # Quebra o loop das páginas

        if not tabela_principal:
            logger.error("Falha Crítica: Tabela válida com 'TOTAL GERAL' e colunas reconhecidas não foi localizada.")
            return False
            
        logger.info(f"Tabela validada (Página {numero_pagina_alvo}). Índices Dinâmicos mapeados -> Imp: {idx_imp}, Exp: {idx_exp}")
        
        # Processamento Real das Mercadorias
        for indice, row in enumerate(tabela_principal):
            if not row or len(row) <= max(idx_imp, idx_exp): continue
            
            # A categoria fica em row[0], a mercadoria em row[1]. 
            val_col0 = str(row[0]).strip() if row[0] else ""
            val_col1 = str(row[1]).strip() if len(row) > 1 and row[1] else ""
            
            mercadoria = val_col1 if val_col1 else val_col0
            
            # --- CORREÇÃO APLICADA: SANITIZAÇÃO DE WHITESPACE E QUEBRAS DE LINHA (\n) ---
            mercadoria = mercadoria.replace('\n', ' ')
            mercadoria = " ".join(mercadoria.split()).strip()
            # ----------------------------------------------------------------------------
            
            merc_upper = mercadoria.upper()
            
            if not merc_upper: continue
            
            # Captura Dinâmica do Volume Oficial para o Guardião
            if merc_upper == 'TOTAL GERAL':
                try:
                    v_imp_str = str(row[idx_imp]).replace('.', '').replace(',', '.') if row[idx_imp] else "0"
                    v_exp_str = str(row[idx_exp]).replace('.', '').replace(',', '.') if row[idx_exp] else "0"
                    volume_total_oficial_pdf = float(v_imp_str) + float(v_exp_str)
                except Exception as e:
                    logger.warning(f"TOTAL GERAL encontrado, mas falha na conversão de valores. Erro: {e}")
                continue

            # Pular cabeçalhos e as categorias agrupadoras
            if mercadoria.isupper() or merc_upper in ['SOMA', 'OUTROS']:
                continue
                
            # Extração Importação (usando índice dinâmico)
            vol_importacao = row[idx_imp] if row[idx_imp] else "0"
            if vol_importacao and str(vol_importacao).strip() not in ['-', '', 'None']:
                total_linhas_tentadas += 1
                try:
                    obj_imp = MovimentacaoPortuaria(
                        ano=self.ano, mes=self.mes, mercadoria=mercadoria,
                        tipo_operacao="Importação", volume_toneladas=vol_importacao
                    )
                    linhas_validadas.append(obj_imp.model_dump())
                    volume_validado_toneladas += float(str(vol_importacao).replace('.', '').replace(',', '.'))
                    
                except ValidationError as e:
                    campo_erro = str(e.errors()[0]['loc'][0]) if e.errors() else "desconhecido"
                    quarantine.log_rejection(
                        conteudo_bruto_linha=row, mensagem_erro_pydantic=str(e),
                        campo_com_erro=campo_erro, valor_bruto_campo_erro=vol_importacao,
                        pagina_pdf=numero_pagina_alvo, indice_linha_tabela=indice,
                        tipo_operacao_tentativa="Importação", mercadoria_tentativa=mercadoria
                    )
                    
            # Extração Exportação (usando índice dinâmico)
            vol_exportacao = row[idx_exp] if row[idx_exp] else "0"
            if vol_exportacao and str(vol_exportacao).strip() not in ['-', '', 'None']:
                total_linhas_tentadas += 1
                try:
                    obj_exp = MovimentacaoPortuaria(
                        ano=self.ano, mes=self.mes, mercadoria=mercadoria,
                        tipo_operacao="Exportação", volume_toneladas=vol_exportacao
                    )
                    linhas_validadas.append(obj_exp.model_dump())
                    volume_validado_toneladas += float(str(vol_exportacao).replace('.', '').replace(',', '.'))
                    
                except ValidationError as e:
                    campo_erro = str(e.errors()[0]['loc'][0]) if e.errors() else "desconhecido"
                    quarantine.log_rejection(
                        conteudo_bruto_linha=row, mensagem_erro_pydantic=str(e),
                        campo_com_erro=campo_erro, valor_bruto_campo_erro=vol_exportacao,
                        pagina_pdf=numero_pagina_alvo, indice_linha_tabela=indice,
                        tipo_operacao_tentativa="Exportação", mercadoria_tentativa=mercadoria
                    )

        quarantine.save_rejections()
        
        # Avaliação dos Circuit Breakers com os mapeamentos corretos de variáveis
        linha_ok = quarantine.evaluate_line_breaker(total_linhas_tentadas, threshold_percentual=5.0)
        volume_ok = quarantine.evaluate_coverage_breaker(
            volume_validado=volume_validado_toneladas,        
            volume_total_oficial=volume_total_oficial_pdf,  
            threshold_percentual=5.0
        )

        if not linha_ok or not volume_ok:
            logger.error("Processamento abortado pelos Circuit Breakers. A camada Silver não será atualizada.")
            return False

        if not linhas_validadas:
            logger.warning("Nenhum registro de movimentação foi validado com sucesso.")
            return False

        # Converte a lista estruturada de dicionários em DataFrame do Pandas
        df = pd.DataFrame(linhas_validadas)

        nome_arquivo_silver = f"aps_movimentacao_{self.ano}_{self.mes}.parquet"
        sucesso_escrita = connector.save_parquet(df, "silver", "aps", nome_arquivo_silver)
        
        if sucesso_escrita:
            logger.info(f"Sucesso! {len(df)} registros aprovados (Pág {numero_pagina_alvo}). Cobertura: {volume_validado_toneladas:,.2f} ton de {volume_total_oficial_pdf:,.2f} ton declaradas.")
            logger.info(f"Arquivo Silver encaminhado via DataLakeConnector: {nome_arquivo_silver}")
            return True
        else:
            logger.error("Falha ao persistir dados da APS na camada Silver.")
            return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    prefixo = f"aps_mensario_{ano_alvo}_{mes_alvo}_id_"
    nome_arquivo = connector.find_latest_file("bronze", "aps", prefixo, ".pdf")
    
    if not nome_arquivo:
        msg = f"Nenhum ficheiro PDF encontrado na camada Bronze para ({mes_alvo}/{ano_alvo})."
        logger.error(msg)
        notifier.send_message(f"⚠️ *Pipeline APS Abortado*\n{msg}", "warning")
        sys.exit(1)
    else:
        logger.info(f"Regra de Negócio: Ficheiro alvo validado para {mes_alvo.upper()}/{ano_alvo}: {nome_arquivo}")
        
        cleaner = APSCleaner(file_name=nome_arquivo, ano=ano_alvo, mes=mes_alvo)
        sucesso = cleaner.extract_and_clean()
        
        if sucesso:
            notifier.send_message(f"✅ *Pipeline APS Concluído ({mes_alvo.upper()}/{ano_alvo})*\nCamada Silver atualizada com sucesso no backend ativo!", "success")
            sys.exit(0)
        else:
            notifier.send_message(f"❌ *Falha no Pipeline APS ({mes_alvo.upper()}/{ano_alvo})*\nProcessamento interrompido. Verifique os logs e a Quarentena.", "error")
            sys.exit(1)