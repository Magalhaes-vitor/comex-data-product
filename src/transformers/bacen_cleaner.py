import os
import sys
import json
import logging
import pandas as pd
from pydantic import ValidationError

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.models.contracts import CotacaoBacen
from src.utils.date_rules import DateRules
from src.utils.quarantine import QuarantineManager
from src.utils.notifier import notifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BacenCleaner:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        self.file_name = f"bacen_ptax_{ano}_{mes}.json"
        self.bronze_path = os.path.join(project_root, "data", "bronze", "bacen", self.file_name)
        self.silver_dir = os.path.join(project_root, "data", "silver", "bacen")
        os.makedirs(self.silver_dir, exist_ok=True)

    def extract_and_clean(self):
        logger.info(f"A iniciar leitura e validação do JSON: {self.bronze_path}")
        
        if not os.path.exists(self.bronze_path):
            logger.error("Ficheiro da camada Bronze não encontrado.")
            return False

        with open(self.bronze_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cotacoes = data.get("value", [])
        if not cotacoes:
            logger.error("Nenhum dado de cotação encontrado no ficheiro JSON.")
            return False

        quarantine = QuarantineManager(
            pipeline_name="bacen",
            ano=self.ano,
            mes=self.mes,
            arquivo_origem=self.file_name
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
            logger.error("Processamento abortado! Dados cambiais não confiáveis. A camada Silver não será atualizada.")
            return False

        if linhas_validadas:
            df = pd.DataFrame(linhas_validadas)
            df['data_cotacao'] = pd.to_datetime(df['data_cotacao'])
            
            silver_file = os.path.join(self.silver_dir, f"bacen_ptax_{self.ano}_{self.mes}.parquet")
            df.to_parquet(silver_file, index=False)
            
            logger.info(f"Sucesso! {len(df)} dias de cotação aprovados.")
            logger.info(f"Ficheiro Silver gerado em: {silver_file}")
            return True
            
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
        notifier.send_message(f"✅ *Pipeline Bacen Concluído ({mes_alvo.upper()}/{ano_alvo})*\nCamada Silver atualizada com as cotações PTAX!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Pipeline Bacen ({mes_alvo.upper()}/{ano_alvo})*\nProcessamento interrompido. Verifique os logs.", "error")
        sys.exit(1)