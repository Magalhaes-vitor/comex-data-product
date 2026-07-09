import sys
import logging
import pandas as pd
from pydantic import ValidationError

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.models.contracts import CotacaoBacen
from src.utils.date_rules import DateRules
from src.utils.quarantine import QuarantineManager
from src.utils.notifier import notifier
from src.utils.storage import connector  # A nossa nova abstração!

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BacenCleaner:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        self.file_name_bronze = f"bacen_ptax_{ano}_{mes}.json"
        self.file_name_silver = f"bacen_ptax_{ano}_{mes}.parquet"

    def extract_and_clean(self):
        logger.info(f"A solicitar leitura do JSON ao DataLakeConnector: {self.file_name_bronze}")
        
        # O conector decide se lê do disco ou faz download do S3!
        data = connector.read_json("bronze", "bacen", self.file_name_bronze)
        
        if not data:
            logger.error("Ficheiro da camada Bronze não encontrado ou corrompido.")
            return False

        cotacoes = data.get("value", [])
        if not cotacoes:
            logger.error("Nenhum dado de cotação encontrado no ficheiro JSON.")
            return False

        quarantine = QuarantineManager(
            pipeline_name="bacen",
            ano=self.ano,
            mes=self.mes,
            arquivo_origem=self.file_name_bronze
        )

        linhas_validadas = []
        total_linhas_tentadas = 0
        
        for item in cotacoes:
            total_linhas_tentadas += 1
            try:
                obj_valido = CotacaoBacen(
                    data_cotacao=item.get("dataHoraCotacao"),
                    cotacao_compra=item.get("cotacaoCompra"),
                    cotacao_venda=item.get("cotacaoVenda")
                )
                linhas_validadas.append(obj_valido.model_dump())
                
            except ValidationError as e:
                campo_erro = str(e.errors()[0]['loc'][0]) if e.errors() else "desconhecido"
                valor_erro = item.get(campo_erro, "") if campo_erro != "desconhecido" else ""
                
                quarantine.log_rejection(
                    conteudo_bruto_linha=item, 
                    mensagem_erro_pydantic=str(e),
                    campo_com_erro=campo_erro, 
                    valor_bruto_campo_erro=valor_erro,
                    pagina_pdf=None, 
                    indice_linha_tabela=total_linhas_tentadas,
                    tipo_operacao_tentativa="Cambio Mensal", 
                    mercadoria_tentativa="Dolar/PTAX"
                )

        quarantine.save_rejections()
        linha_ok = quarantine.evaluate_line_breaker(total_linhas_tentadas, threshold_percentual=5.0)

        if not linha_ok:
            logger.error("Processamento abortado! Dados cambiais não confiáveis.")
            return False

        if linhas_validadas:
            df = pd.DataFrame(linhas_validadas)
            df['data_cotacao'] = pd.to_datetime(df['data_cotacao'])
            
            # O conector trata da escrita (disco local ou bucket S3)
            sucesso_escrita = connector.save_parquet(df, "silver", "bacen", self.file_name_silver)
            
            if sucesso_escrita:
                logger.info(f"Sucesso! {len(df)} dias de cotação validados e enviados para a camada Silver.")
                return True
            else:
                logger.error("Falha ao persistir dados na camada Silver.")
                return False
            
        logger.warning("Nenhum dado válido extraído para a camada Silver do Bacen.")
        return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Alvo da limpeza definido para: {mes_alvo.upper()}/{ano_alvo}")
    
    cleaner = BacenCleaner(ano=ano_alvo, mes=mes_alvo)
    sucesso = cleaner.extract_and_clean()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline Bacen Concluído ({mes_alvo.upper()}/{ano_alvo})*\nCamada Silver atualizada no backend ativo!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Pipeline Bacen ({mes_alvo.upper()}/{ano_alvo})*\nProcessamento interrompido. Verifique os logs.", "error")
        sys.exit(1)