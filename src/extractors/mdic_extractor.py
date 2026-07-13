import os
import sys
import time
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _is_retryable(exception):
    if isinstance(exception, requests.exceptions.HTTPError):
        status = exception.response.status_code if exception.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))

class MDICExtractor:
    def __init__(self, ano=None, mes=None):
        self.api_url = "https://api-comexstat.mdic.gov.br/general"
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # [NOVO] Desacoplamento Temporal
        if ano and mes:
            self.ano = str(ano)
            self.mes = str(mes).lower()
        else:
            periodo = DateRules.get_target_period()
            self.ano = str(periodo["ano"])
            self.mes = periodo["mes_str"]

        meses_map = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
                     'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}
        self.mes_num = meses_map.get(self.mes)

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=12, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _fetch_flow(self, ano, mes_num, flow):
        periodo_str = f"{ano}-{mes_num:02d}"
        payload = {
            "flow": flow,
            "monthDetail": True,
            "period": {"from": periodo_str, "to": periodo_str},
            "details": ["country", "ncm"],
            "metrics": ["metricFOB", "metricKG"]
        }

        logger.info(f"Solicitando dados de {flow.upper()} ({periodo_str}) ao MDIC API...")
        time.sleep(1)
        response = requests.post(self.api_url, headers=self.headers, json=payload, timeout=20)

        if response.status_code != 200:
            logger.warning(f"Falha na API MDIC para fluxo '{flow}' (Status {response.status_code}): {response.text[:300]}")

        response.raise_for_status()
        data = response.json()

        if data and "data" in data:
            registros = data["data"].get("list", data["data"])
            if registros:
                qtd = len(registros) if hasattr(registros, "__len__") else "N/A"
                logger.info(f"Sucesso ({flow})! {qtd} registros comerciais localizados.")
                return data

        logger.warning(f"A API respondeu com sucesso, mas nenhum dado foi retornado para '{flow}' no período.")
        return None

    def fetch_comex_data(self, ano, mes_num):
        resultado = {
            "export": self._fetch_flow(ano, mes_num, "export"),
            "import": self._fetch_flow(ano, mes_num, "import"),
        }
        if resultado["export"] is None and resultado["import"] is None:
            return None
        return resultado

    def save_to_bronze(self, data, ano, mes_str):
        if not data:
            return False
        file_name = f"mdic_comex_{ano}_{mes_str}.json"
        logger.info(f"Iniciando ingestão do arquivo (Camada Bronze): {file_name}")
        sucesso = connector.save_json(data, "bronze", "mdic", file_name)

        if sucesso:
            logger.info("JSON do MDIC salvo com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o arquivo JSON do MDIC no conector.")
            return False

    def run(self):
        logger.info(f"=== Iniciando Extração MDIC para: {self.mes.upper()}/{self.ano} ===")

        if self.mes_num is None:
            msg_erro = f"Pipeline interrompido: mês '{self.mes}' não reconhecido no mapeamento."
            logger.error(msg_erro)
            notifier.send_message(f"❌ *Falha Extrator MDIC*\n{msg_erro}", "error")
            return False

        try:
            payload = self.fetch_comex_data(int(self.ano), self.mes_num)
            if payload:
                return self.save_to_bronze(payload, self.ano, self.mes)
            else:
                msg_erro = "Pipeline interrompido: Dados do MDIC indisponíveis para o período."
                logger.error(msg_erro)
                notifier.send_message(f"⚠️ *Extrator MDIC*\n{msg_erro}", "warning")
                return False
        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro final: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator MDIC*\n{error_msg}", "error")
            return False
        finally:
            logger.info("=== Processo Finalizado ===")

if __name__ == "__main__":
    extractor = MDICExtractor()
    sucesso = extractor.run()
    if sucesso:
        notifier.send_message("✅ *Extrator MDIC*\nIngestão do JSON da Balança Comercial concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)