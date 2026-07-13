import os
import sys
import logging
import pandas as pd
from pydantic import ValidationError

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.models.contracts import RegistroComexMdic
from src.utils.date_rules import DateRules
from src.utils.quarantine import QuarantineManager
from src.utils.notifier import notifier
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MDICCleaner:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        self.file_name_bronze = f"mdic_comex_{ano}_{mes}.json"
        self.file_name_silver = f"mdic_comex_{ano}_{mes}.parquet"

    def extract_and_clean(self):
        logger.info(f"Solicitando leitura do JSON do MDIC ao DataLakeConnector: {self.file_name_bronze}")
        
        # Recupera os dados consolidados do S3/Local
        data = connector.read_json("bronze", "mdic", self.file_name_bronze)
        
        if not data:
            logger.error("Arquivo bruto da camada Bronze do MDIC não foi localizado.")
            return False

        quarantine = QuarantineManager(
            pipeline_name="mdic",
            ano=self.ano,
            mes=self.mes,
            arquivo_origem=self.file_name_bronze
        )

        linhas_validadas = []
        total_linhas_tentadas = 0

        # O JSON possui chaves separadas para 'export' e 'import' conforme o extrator salvou
        for fluxo in ['export', 'import']:
            fluxo_data = data.get(fluxo)
            if not fluxo_data or "data" not in fluxo_data:
                logger.warning(f"Nenhum bloco de dados encontrado para o fluxo: {fluxo}")
                continue
            
            # A API pode retornar uma lista direto ou encapsulada em 'list'
            registros = fluxo_data["data"].get("list", fluxo_data["data"]) if isinstance(fluxo_data["data"], dict) else fluxo_data["data"]
            
            if not isinstance(registros, list):
                logger.warning(f"Formato inesperado nos registros de {fluxo}. Pulando...")
                continue

            for item in registros:
                if not isinstance(item, dict): continue
                total_linhas_tentadas += 1
                
                try:
                    # Validação rigorosa pelo Pydantic Contract
                    if not item.get("coNcm") and not item.get("co_ncm"):
                        logger.warning("Registro sem chave NCM identificada — payload da API pode ter mudado de formato.")
                    obj_valido = RegistroComexMdic(
                        ano=self.ano,
                        mes=self.mes,
                        fluxo=fluxo,
                        **item
                    )
                    linhas_validadas.append(obj_valido.model_dump())
                    
                except ValidationError as e:
                    campo_erro = str(e.errors()[0]['loc'][0]) if e.errors() else "desconhecido"
                    quarantine.log_rejection(
                        conteudo_bruto_linha=item,
                        mensagem_erro_pydantic=str(e),
                        campo_com_erro=campo_erro,
                        valor_bruto_campo_erro=item.get(campo_erro, ""),
                        pagina_pdf=None,
                        indice_linha_tabela=total_linhas_tentadas,
                        tipo_operacao_tentativa=fluxo,
                        mercadoria_tentativa="Balança Comercial"
                    )

        quarantine.save_rejections()
        linha_ok = quarantine.evaluate_line_breaker(total_linhas_tentadas, threshold_percentual=5.0)

        if not linha_ok:
            logger.error("Processamento interrompido: Volume de rejeições do MDIC ultrapassou o limite aceitável.")
            return False

        if linhas_validadas:
            df = pd.DataFrame(linhas_validadas)
            
            # Encaminha o Parquet estruturado para a camada Silver do S3
            sucesso_escrita = connector.save_parquet(df, "silver", "mdic", self.file_name_silver)
            
            if sucesso_escrita:
                logger.info(f"Sucesso! {len(df)} registros do MDIC validados e salvos na camada Silver.")
                return True
            else:
                logger.error("Falha ao salvar o arquivo Parquet na camada Silver.")
                return False

        logger.warning("Nenhum registro válido foi processado.")
        return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Limpeza do MDIC voltada para: {mes_alvo.upper()}/{ano_alvo}")
    
    cleaner = MDICCleaner(ano=ano_alvo, mes=mes_alvo)
    sucesso = cleaner.extract_and_clean()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline MDIC Silver Concluído ({mes_alvo.upper()}/{ano_alvo})*\nDados comerciais integrados na camada Silver", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Pipeline MDIC Silver ({mes_alvo.upper()}/{ano_alvo})*\nProcessamento abortado. Verifique os logs.", "error")
        sys.exit(1)