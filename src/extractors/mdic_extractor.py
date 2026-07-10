import os
import sys
import time
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

# Adiciona a raiz do projeto ao path para permitir os imports da pasta src
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules
from src.utils.storage import connector

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _is_retryable(exception):
    #Filtro de resiliência: retenta erros 5xx, timeout, conexões e 429 (Rate Limit).
    if isinstance(exception, requests.exceptions.HTTPError):
        status = exception.response.status_code if exception.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


class MDICExtractor:
    def __init__(self):
        # Endpoint público do Comex Stat para dados agregados (Dados Gerais)
        self.api_url = "https://api-comexstat.mdic.gov.br/general"
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=12, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def _fetch_flow(self, ano, mes_num, flow):
        #Busca os dados de um fluxo específico (import ou export) para o mês alvo.

        periodo_str = f"{ano}-{mes_num:02d}"

        payload = {
            "flow": flow,
            "monthDetail": True,
            "period": {
                "from": periodo_str,
                "to": periodo_str
            },
            "details": ["country", "ncm"],
            "metrics": ["metricFOB", "metricKG"]
        }

        logger.info(f"Solicitando dados de {flow.upper()} ({periodo_str}) ao MDIC API...")
        time.sleep(1)
        response = requests.post(self.api_url, headers=self.headers, json=payload, timeout=20)

        if response.status_code != 200:
            logger.warning(
                f"Falha na API MDIC para fluxo '{flow}' (Status {response.status_code}): "
                f"{response.text[:300]}"
            )

        response.raise_for_status()

        data = response.json()

        if data and "data" in data:
            registros = data["data"].get("list", data["data"])
            # Valida se a lista de registros realmente possui itens
            if registros:
                qtd = len(registros) if hasattr(registros, "__len__") else "N/A"
                logger.info(f"Sucesso ({flow})! {qtd} registros comerciais localizados.")
                return data

        logger.warning(f"A API respondeu com sucesso, mas nenhum dado foi retornado para '{flow}' no período.")
        return None

    def fetch_comex_data(self, ano, mes_num):
        #Busca os dados de importação e exportação (Balança Comercial) do mês alvo.
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

        # O conector faz a mágica de salvar na AWS S3
        sucesso = connector.save_json(data, "bronze", "mdic", file_name)

        if sucesso:
            logger.info("JSON do MDIC salvo com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao salvar o arquivo JSON do MDIC no conector.")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: MDIC (Comex Stat) ===")

        periodo = DateRules.get_target_period()
        ano = str(periodo["ano"])
        mes_str = periodo["mes_str"]

        # Converte a string do mês ('mai') para o número do mês (5) para a API do MDIC
        meses_map = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
                     'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}
        mes_num = meses_map.get(mes_str)

        if mes_num is None:
            msg_erro = f"Pipeline interrompido: mês '{mes_str}' não reconhecido no mapeamento."
            logger.error(msg_erro)
            notifier.send_message(f"❌ *Falha Extrator MDIC*\n{msg_erro}", "error")
            return False

        logger.info(f"Regra de Negócio: Intervalo MDIC definido para: {mes_str.upper()}/{ano}")

        try:
            payload = self.fetch_comex_data(int(ano), mes_num)
            if payload:
                return self.save_to_bronze(payload, ano, mes_str)
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