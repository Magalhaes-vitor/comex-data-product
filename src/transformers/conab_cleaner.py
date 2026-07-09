import os
import sys
import logging
import pandas as pd
from pydantic import ValidationError

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.models.contracts import RegistroConab
from src.utils.date_rules import DateRules
from src.utils.quarantine import QuarantineManager
from src.utils.notifier import notifier
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CONABCleaner:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        self.file_name_bronze = f"conab_safra_{ano}_{mes}.json"
        self.file_name_silver = f"conab_safra_{ano}_{mes}.parquet"

        # Mapeamento estrito: De 'Aba da CONAB' para 'Mercadoria do APS'
        self.target_sheets = {
            "Soja": "Soja em Grãos",
            "Milho Total": "Milho",
            "Trigo": "Trigo",
            "Arroz Total": "Arroz",
            "Feijão Total": "Feijão"
        }
        
        self.ufs_validas = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']

    def extract_and_clean(self):
        logger.info(f"Solicitando leitura do JSON da CONAB: {self.file_name_bronze}")
        
        data = connector.read_json("bronze", "conab", self.file_name_bronze)
        
        if not data or "sheets" not in data:
            logger.error("Arquivo bruto ou estrutura de abas não localizados.")
            return False

        quarantine = QuarantineManager(
            pipeline_name="conab",
            ano=self.ano,
            mes=self.mes,
            arquivo_origem=self.file_name_bronze
        )

        linhas_validadas = []
        total_linhas_tentadas = 0
        abas = data["sheets"]

        logger.info("Iniciando extração cirúrgica de Produção por Estado (UF)...")

        for nome_aba, cultura_padronizada in self.target_sheets.items():
            if nome_aba not in abas:
                logger.warning(f"Aba '{nome_aba}' não encontrada na planilha atual. Pulando...")
                continue
                
            registros = abas[nome_aba]
            if not isinstance(registros, list):
                continue
                
            for item in registros:
                if not isinstance(item, dict): 
                    continue
                
                estado_raw = str(item.get("Unnamed: 0", "")).strip().upper()
                
                # Só nos importam as linhas que começam com a sigla de um Estado Brasileiro
                if estado_raw not in self.ufs_validas:
                    continue
                    
                total_linhas_tentadas += 1
                
                # A produção da safra mais recente fica tipicamente na coluna de índice 8 ('Unnamed: 8')
                producao_raw = item.get("Unnamed: 8", 0.0)
                
                try:
                    obj_valido = RegistroConab(
                        ano_referencia=self.ano,
                        mes_referencia=self.mes,
                        estado=estado_raw,
                        cultura=cultura_padronizada,
                        producao_mil_t=producao_raw
                    )
                    linhas_validadas.append(obj_valido.model_dump())
                    
                except ValidationError as e:
                    quarantine.log_rejection(
                        conteudo_bruto_linha=item,
                        mensagem_erro_pydantic=str(e),
                        campo_com_erro="producao_mil_t",
                        valor_bruto_campo_erro=producao_raw,
                        pagina_pdf=None,
                        indice_linha_tabela=total_linhas_tentadas,
                        tipo_operacao_tentativa="Filtro Geográfico",
                        mercadoria_tentativa=cultura_padronizada
                    )

        quarantine.save_rejections()
        
        # Como o nosso escopo agora é extremamente limpo, baixamos a tolerância a falhas para 5%
        linha_ok = quarantine.evaluate_line_breaker(total_linhas_tentadas, threshold_percentual=5.0)

        if not linha_ok:
            logger.error("Processamento interrompido: Alto volume de rejeições nos estados analisados.")
            return False

        if linhas_validadas:
            df = pd.DataFrame(linhas_validadas)
            sucesso_escrita = connector.save_parquet(df, "silver", "conab", self.file_name_silver)
            
            if sucesso_escrita:
                logger.info(f"Sucesso! {len(df)} registros geográficos agrícolas salvos na camada Silver.")
                return True
            else:
                logger.error("Falha ao salvar o arquivo Parquet na camada Silver.")
                return False

        logger.warning("Nenhum registro válido foi processado. Verifique a estrutura das abas da CONAB.")
        return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Limpeza da CONAB voltada para: {mes_alvo.upper()}/{ano_alvo}")
    
    cleaner = CONABCleaner(ano=ano_alvo, mes=mes_alvo)
    sucesso = cleaner.extract_and_clean()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline CONAB Silver Concluído ({mes_alvo.upper()}/{ano_alvo})*\nDados de safra por Estado consolidados no S3!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Pipeline CONAB Silver ({mes_alvo.upper()}/{ano_alvo})*\nProcessamento abortado. Verifique os logs.", "error")
        sys.exit(1)