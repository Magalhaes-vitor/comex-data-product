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
    """Retenta em falhas de rede/timeout ou erros de servidor (5xx).

    NÃO retenta em falha de resolução DNS (host inexistente/errado), pois
    tentar de novo o mesmo host inválido nunca vai funcionar — é um erro de
    configuração, não uma falha transiente.
    """
    if isinstance(exception, requests.exceptions.HTTPError):
        status = exception.response.status_code if exception.response is not None else None
        return status is not None and status >= 500

    if isinstance(exception, requests.exceptions.ConnectionError):
        # ConnectionError cobre tanto DNS quanto recusa/queda de conexão.
        # Se a causa raiz for NameResolutionError, não adianta retentar.
        cause_str = str(exception)
        if "NameResolutionError" in cause_str or "getaddrinfo failed" in cause_str:
            return False
        return True

    return isinstance(exception, requests.exceptions.Timeout)


class ANTAQConfigError(Exception):
    """Erro de configuração: host/URL da API inválido ou não resolvível."""
    pass


class ANTAQExtractor:
    def __init__(self, api_url=None):
        # IMPORTANTE: "api.antaq.gov.br" NÃO existe (confirmado via DNS).
        # A ANTAQ não expõe uma API REST simples de atracação por ano/mês.
        # Os dados abertos reais estão em:
        #   - Portal CKAN: https://dadosabertos.antaq.gov.br (api/3/action/...)
        #   - Estatístico Aquaviário (lotes anuais CSV/TXT):
        #     https://web3.antaq.gov.br/ea/sense/download.html
        # Configure ANTAQ_API_URL com o endpoint correto assim que confirmado
        # (dataset/resource_id do CKAN ou outra fonte oficial).
        self.api_url = api_url or os.environ.get("ANTAQ_API_URL")
        if not self.api_url:
            raise ANTAQConfigError(
                "ANTAQ_API_URL não configurada. O endpoint anterior "
                "(api.antaq.gov.br) não existe (falha de DNS confirmada). "
                "Defina a variável de ambiente ANTAQ_API_URL com o endpoint "
                "correto (ex.: dataset do portal CKAN dadosabertos.antaq.gov.br "
                "ou a fonte de download do Estatístico Aquaviário)."
            )

        self.headers = {
            "Accept": "application/json",
            "User-Agent": "ComexDataProduct/1.0"
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True
    )
    def fetch_atracacao_data(self, ano, mes_num):
        """Busca os registos de atracação para o mês e ano especificados."""
        logger.info(f"Buscando dados de Atracação Portuária (ANTAQ) para {mes_num}/{ano}...")

        params = {
            "ano": ano,
            "mes": mes_num,
            "formato": "json"
        }

        time.sleep(1)

        try:
            response = requests.get(self.api_url, headers=self.headers, params=params, timeout=30)

            if response.status_code != 200:
                logger.warning(f"Status {response.status_code} retornado. O Tenacity avaliará retry...")

            response.raise_for_status()
            data = response.json()

        except requests.exceptions.ConnectionError as e:
            if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                logger.error(
                    f"Host da API não resolve via DNS: '{self.api_url}'. "
                    "Isso é erro de configuração (URL/host errado ou inexistente), "
                    "não uma falha transiente de rede — retry não vai resolver. "
                    "Verifique ANTAQ_API_URL."
                )
            raise

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning("Dados não encontrados (404). O painel da ANTAQ pode não estar atualizado para este período.")
                return None
            raise

        if data and isinstance(data, dict) and "value" in data:
            registros = data["value"]
            logger.info(f"Sucesso! {len(registros)} atracações localizadas.")
            return data
        elif data and isinstance(data, list) and len(data) > 0:
            logger.info(f"Sucesso! {len(data)} atracações localizadas.")
            return data

        logger.warning("A API respondeu com sucesso, mas nenhum dado de atracação foi retornado.")
        return None

    def save_to_bronze(self, data, ano, mes_str):
        if not data:
            return False

        file_name = f"antaq_atracacao_{ano}_{mes_str}.json"
        logger.info(f"Iniciando ingestão do ficheiro (Camada Bronze): {file_name}")

        sucesso = connector.save_json(data, "bronze", "antaq", file_name)

        if sucesso:
            logger.info("JSON da ANTAQ guardado com sucesso via DataLakeConnector!")
            return True
        else:
            logger.error("Falha ao guardar o ficheiro JSON da ANTAQ no conector.")
            return False

    def run(self):
        logger.info("=== Iniciando Pipeline de Extração: ANTAQ (Atracação Portuária) ===")

        periodo = DateRules.get_target_period()
        ano = str(periodo["ano"])
        mes_str = periodo["mes_str"]
        mes_num = str(periodo["mes_num"]).zfill(2)  # Converte para formato '05'

        logger.info(f"Regra de Negócio: Extração ANTAQ definida para referência: {mes_str.upper()}/{ano}")

        try:
            payload = self.fetch_atracacao_data(ano, mes_num)

            if payload:
                return self.save_to_bronze(payload, ano, mes_str)
            else:
                msg_erro = "Pipeline interrompido: Dados da ANTAQ indisponíveis para o período."
                logger.error(msg_erro)
                notifier.send_message(f"⚠️ *Extrator ANTAQ*\n{msg_erro}", "warning")
                return False

        except requests.exceptions.ConnectionError as e:
            if "NameResolutionError" in str(e) or "getaddrinfo failed" in str(e):
                error_msg = (
                    f"URL da API ANTAQ inválida/não resolvível: '{self.api_url}'. "
                    "Verifique a variável ANTAQ_API_URL — este não é um erro transiente."
                )
            else:
                error_msg = f"Falha de conexão com a API ANTAQ: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator ANTAQ*\n{error_msg}", "error")
            return False

        except Exception as e:
            error_msg = f"Esgotadas todas as tentativas de extração. Erro final: {e}"
            logger.error(f"FALHA CRÍTICA: {error_msg}")
            notifier.send_message(f"❌ *Falha Extrator ANTAQ*\n{error_msg}", "error")
            return False

        finally:
            logger.info("=== Processo Finalizado ===")


if __name__ == "__main__":
    try:
        extractor = ANTAQExtractor()
    except ANTAQConfigError as e:
        logger.error(f"FALHA DE CONFIGURAÇÃO: {e}")
        notifier.send_message(f"❌ *Falha Extrator ANTAQ*\nErro de configuração: {e}", "error")
        sys.exit(1)

    sucesso = extractor.run()

    if sucesso:
        notifier.send_message("✅ *Extrator ANTAQ*\nIngestão dos dados de Atracação concluída na camada Bronze!", "success")
        sys.exit(0)
    else:
        sys.exit(1)