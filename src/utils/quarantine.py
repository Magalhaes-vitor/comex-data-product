import os
import uuid
import json
import logging
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

class QuarantineManager:
    def __init__(self, pipeline_name: str, ano: str, mes: str, arquivo_origem: str):
        """
        Gerenciador de Quarentena (DLQ) com avaliação dupla de qualidade (Linhas e Volume).
        """
        self.pipeline_name = pipeline_name.lower()
        self.ano = str(ano)
        self.mes = str(mes)
        self.arquivo_origem = arquivo_origem
        self.execution_id = str(uuid.uuid4())
        
        self.rejeitos = []
        
        # Criação de uma zona irmã paralela a Bronze/Silver/Gold
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.quarantine_dir = os.path.join(project_root, "data", "quarantine", self.pipeline_name)
        os.makedirs(self.quarantine_dir, exist_ok=True)

    def log_rejection(self, 
                      conteudo_bruto_linha: dict | list,
                      mensagem_erro_pydantic: str,
                      campo_com_erro: str = "desconhecido",
                      valor_bruto_campo_erro: str = "",
                      pagina_pdf: int = None,
                      indice_linha_tabela: int = None,
                      tipo_operacao_tentativa: str = "",
                      mercadoria_tentativa: str = ""):
        """
        Registra uma falha de validação como um evento imutável.
        """
        categoria_erro = "erro_tipagem_numerica" if campo_com_erro == "volume_toneladas" else \
                         "erro_dominio_temporal" if campo_com_erro in ["mes", "ano"] else \
                         "erro_generico"

        rejeito = {
            "execution_id": self.execution_id,
            "timestamp_processamento": datetime.now(),
            "ano_referencia": self.ano,
            "mes_referencia": self.mes,
            "arquivo_origem": self.arquivo_origem,
            "pagina_pdf": pagina_pdf,
            "indice_linha_tabela": indice_linha_tabela,
            "conteudo_bruto_linha": json.dumps(conteudo_bruto_linha, ensure_ascii=False),
            "campo_com_erro": campo_com_erro,
            "valor_bruto_campo_erro": str(valor_bruto_campo_erro),
            "mensagem_erro_pydantic": str(mensagem_erro_pydantic),
            "categoria_erro": categoria_erro,
            "tipo_operacao_tentativa": tipo_operacao_tentativa,
            "mercadoria_tentativa": mercadoria_tentativa
        }
        
        self.rejeitos.append(rejeito)

    def evaluate_line_breaker(self, total_linhas_tentadas: int, threshold_percentual: float = 5.0) -> bool:
        """
        Breaker 1: Protege contra falhas generalizadas de layout (muitas linhas falhando).
        """
        if total_linhas_tentadas == 0:
            return True
            
        taxa_erro = (len(self.rejeitos) / total_linhas_tentadas) * 100
        logger.info(f"[Breaker Linhas] {len(self.rejeitos)} rejeições em {total_linhas_tentadas} linhas ({taxa_erro:.2f}% de falha).")
        
        if taxa_erro > threshold_percentual:
            logger.error(f"CIRCUIT BREAKER ACIONADO (Linhas)! Taxa de erro de {taxa_erro:.2f}% superou o limite de {threshold_percentual}%.")
            return False
            
        return True

    def evaluate_coverage_breaker(self, volume_validado: float, volume_total_oficial: float, threshold_percentual: float = 5.0) -> bool:
        """
        Breaker 2: Protege contra perda de linhas de alto peso (Soja, Açúcar).
        Compara as toneladas capturadas com o total oficial impresso no PDF.
        """
        if volume_total_oficial <= 0:
            logger.error("[Breaker Volume] TOTAL GERAL não localizado no PDF — tratando como falha estrutural e bloqueando a ingestão.")
            return False
            
        diferenca_volume = abs(volume_total_oficial - volume_validado)
        taxa_perda = (diferenca_volume / volume_total_oficial) * 100
        
        logger.info(f"[Breaker Volume] Cobertura: {volume_validado:,.2f} ton extraídas vs {volume_total_oficial:,.2f} ton declaradas. (Discrepância de {taxa_perda:.2f}%).")
        
        if taxa_perda > threshold_percentual:
            logger.error(f"CIRCUIT BREAKER ACIONADO (Volume)! Perda de {taxa_perda:.2f}% superou o limite aceitável de {threshold_percentual}%.")
            return False
            
        return True

    def save_rejections(self):
        """
        Salva as rejeições acumuladas em um arquivo Parquet isolado (append by file).
        """
        if not self.rejeitos:
            return None
            
        df_rejeitos = pd.DataFrame(self.rejeitos)
        df_rejeitos['timestamp_processamento'] = pd.to_datetime(df_rejeitos['timestamp_processamento'])
        
        file_name = f"{self.pipeline_name}_rejeitos_{self.ano}_{self.mes}_{self.execution_id[:8]}.parquet"
        file_path = os.path.join(self.quarantine_dir, file_name)
        
        df_rejeitos.to_parquet(file_path, index=False)
        logger.warning(f"Quarentena: {len(self.rejeitos)} registros salvos em {file_path} para auditoria forense.")
        
        return file_path