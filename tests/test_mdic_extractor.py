"""
Suite de testes unitários para o módulo MDICExtractor (Comex Stat).
Garante o comportamento correto das requisições, retries e persistência,
sem realizar chamadas reais à API do governo ou ao AWS S3.
"""
import os
import sys
import pytest
import requests
import tenacity
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from src.extractors.mdic_extractor import MDICExtractor, _is_retryable


# ==============================================================================
# FIXTURES E CONFIGURAÇÕES GERAIS
# ==============================================================================

@pytest.fixture(autouse=True)
def no_wait_between_retries():
    """
    Desativa o backoff exponencial e os sleeps durante a execução dos testes
    para garantir que a suite rode de forma instantânea.
    """
    original_wait = MDICExtractor._fetch_flow.retry.wait
    MDICExtractor._fetch_flow.retry.wait = tenacity.wait_none()
    
    with patch("src.extractors.mdic_extractor.time.sleep", return_value=None):
        yield
        
    MDICExtractor._fetch_flow.retry.wait = original_wait


@pytest.fixture
def extractor():
    """Retorna uma instância limpa do MDICExtractor para cada teste."""
    return MDICExtractor()


def make_response(status_code=200, json_data=None, text="erro"):
    """Utilitário para fabricar respostas HTTP mockadas do Requests."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} Client Error", response=resp
        )
    else:
        resp.raise_for_status.side_effect = None
        
    return resp


# Constantes de Massa de Dados para Testes
COMEX_DATA_OK = {"data": {"list": [{"pais": "China", "vl_fob": 1000}]}}
COMEX_DATA_VAZIO = {"data": {"list": []}}


# ==============================================================================
# TESTES: LÓGICA DE RETRY (_is_retryable)
# ==============================================================================

class TestIsRetryable:
    """Valida as regras de negócio que definem quais erros merecem retentativas."""

    def test_erro_5xx_deve_permitir_retry(self):
        resp = make_response(status_code=502)
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable(exc) is True

    def test_erro_4xx_nao_deve_permitir_retry(self):
        resp = make_response(status_code=400)
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable(exc) is False

    def test_timeout_deve_permitir_retry(self):
        assert _is_retryable(requests.exceptions.Timeout()) is True

    def test_connection_error_deve_permitir_retry(self):
        assert _is_retryable(requests.exceptions.ConnectionError()) is True

    def test_excecao_generica_nao_deve_permitir_retry(self):
        assert _is_retryable(ValueError("Erro Genérico")) is False


# ==============================================================================
# TESTES: EXTRAÇÃO DE FLUXO INDIVIDUAL (_fetch_flow)
# ==============================================================================

class TestFetchFlow:
    """Valida a extração isolada dos fluxos de importação ou exportação."""

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_sucesso_retorna_dados_e_valida_payload(self, mock_post, extractor):
        mock_post.return_value = make_response(200, COMEX_DATA_OK)

        resultado = extractor._fetch_flow(2026, 5, "export")

        assert resultado == COMEX_DATA_OK
        mock_post.assert_called_once()
        
        # Validação do Payload Estrutural da API Comex Stat
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {
            "flow": "export",
            "monthDetail": True,
            "period": {"from": "2026-05", "to": "2026-05"},
            "details": ["country"],
            "metrics": ["metricFOB", "metricKG"],
        }
        assert kwargs["timeout"] == 20

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_sucesso_sem_registros_retorna_none(self, mock_post, extractor):
        mock_post.return_value = make_response(200, COMEX_DATA_VAZIO)
        resultado = extractor._fetch_flow(2026, 5, "import")
        assert resultado is None

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_erro_400_aborta_sem_retry(self, mock_post, extractor):
        mock_post.return_value = make_response(400, text="Fluxo inválido")
        with pytest.raises(requests.exceptions.HTTPError):
            extractor._fetch_flow(2026, 5, "export")
        assert mock_post.call_count == 1

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_erro_502_esgota_tentativas(self, mock_post, extractor):
        mock_post.return_value = make_response(502, text="Bad Gateway")
        with pytest.raises(requests.exceptions.HTTPError):
            extractor._fetch_flow(2026, 5, "export")
        assert mock_post.call_count == 3

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_recuperacao_apos_falha_transitoria(self, mock_post, extractor):
        mock_post.side_effect = [
            make_response(503, text="Service Unavailable"),
            make_response(200, COMEX_DATA_OK),
        ]
        resultado = extractor._fetch_flow(2026, 5, "export")
        assert resultado == COMEX_DATA_OK
        assert mock_post.call_count == 2

    @patch("src.extractors.mdic_extractor.requests.post")
    def test_timeout_aciona_retry(self, mock_post, extractor):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        with pytest.raises(requests.exceptions.Timeout):
            extractor._fetch_flow(2026, 5, "export")
        assert mock_post.call_count == 3


# ==============================================================================
# TESTES: ORQUESTRAÇÃO DE FLUXOS (fetch_comex_data)
# ==============================================================================

class TestFetchComexData:
    """Valida a consolidação dos dados de importação e exportação num único objeto."""

    def test_combina_export_e_import_com_sucesso(self, extractor):
        with patch.object(extractor, "_fetch_flow") as mock_fetch:
            mock_fetch.side_effect = [{"data": "export_ok"}, {"data": "import_ok"}]
            resultado = extractor.fetch_comex_data(2026, 5)

            assert resultado == {"export": {"data": "export_ok"}, "import": {"data": "import_ok"}}
            assert mock_fetch.call_count == 2
            mock_fetch.assert_any_call(2026, 5, "export")
            mock_fetch.assert_any_call(2026, 5, "import")

    def test_um_fluxo_vazio_mantem_o_outro_intacto(self, extractor):
        with patch.object(extractor, "_fetch_flow") as mock_fetch:
            mock_fetch.side_effect = [None, {"data": "import_ok"}]
            resultado = extractor.fetch_comex_data(2026, 5)
            assert resultado == {"export": None, "import": {"data": "import_ok"}}

    def test_ambos_fluxos_vazios_retorna_none(self, extractor):
        with patch.object(extractor, "_fetch_flow", return_value=None):
            resultado = extractor.fetch_comex_data(2026, 5)
            assert resultado is None


# ==============================================================================
# TESTES: PERSISTÊNCIA (save_to_bronze)
# ==============================================================================

class TestSaveToBronze:
    """Valida a integração com a camada de abstração de Storage (DataLakeConnector)."""

    @patch("src.extractors.mdic_extractor.connector")
    def test_dados_nulos_aborta_persistencia(self, mock_connector, extractor):
        resultado = extractor.save_to_bronze(None, "2026", "mai")
        assert resultado is False
        mock_connector.save_json.assert_not_called()

    @patch("src.extractors.mdic_extractor.connector")
    def test_salva_json_com_sucesso(self, mock_connector, extractor):
        mock_connector.save_json.return_value = True
        resultado = extractor.save_to_bronze({"export": {}}, "2026", "mai")
        
        assert resultado is True
        mock_connector.save_json.assert_called_once_with(
            {"export": {}}, "bronze", "mdic", "mdic_comex_2026_mai.json"
        )

    @patch("src.extractors.mdic_extractor.connector")
    def test_falha_no_connector_retorna_false(self, mock_connector, extractor):
        mock_connector.save_json.return_value = False
        resultado = extractor.save_to_bronze({"export": {}}, "2026", "mai")
        assert resultado is False


# ==============================================================================
# TESTES: PIPELINE COMPLETO (run)
# ==============================================================================

class TestRun:
    """Valida a orquestração interna do extrator, datas e disparo de notificações."""

    @patch("src.extractors.mdic_extractor.notifier")
    @patch("src.extractors.mdic_extractor.DateRules")
    def test_run_sucesso_completo_nao_notifica_erro(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "mai"}

        with patch.object(extractor, "fetch_comex_data", return_value={"export": {}}) as mock_fetch, \
             patch.object(extractor, "save_to_bronze", return_value=True) as mock_save:

            resultado = extractor.run()

            assert resultado is True
            mock_fetch.assert_called_once_with(2026, 5)
            mock_save.assert_called_once_with({"export": {}}, "2026", "mai")
            mock_notifier.send_message.assert_not_called()

    @patch("src.extractors.mdic_extractor.notifier")
    @patch("src.extractors.mdic_extractor.DateRules")
    def test_run_sem_dados_notifica_warning(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "mai"}

        with patch.object(extractor, "fetch_comex_data", return_value=None):
            resultado = extractor.run()

        assert resultado is False
        mock_notifier.send_message.assert_called_once()
        args, _ = mock_notifier.send_message.call_args
        assert "indisponíveis" in args[0]
        assert args[1] == "warning"

    @patch("src.extractors.mdic_extractor.notifier")
    @patch("src.extractors.mdic_extractor.DateRules")
    def test_run_excecao_notifica_error(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "mai"}

        with patch.object(extractor, "fetch_comex_data", side_effect=requests.exceptions.HTTPError("500")):
            resultado = extractor.run()

        assert resultado is False
        mock_notifier.send_message.assert_called_once()
        args, _ = mock_notifier.send_message.call_args
        assert "Esgotadas todas as tentativas" in args[0]
        assert args[1] == "error"

    @patch("src.extractors.mdic_extractor.notifier")
    @patch("src.extractors.mdic_extractor.DateRules")
    def test_run_mes_invalido_aborta_antes_da_api(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "xyz"}

        with patch.object(extractor, "fetch_comex_data") as mock_fetch:
            resultado = extractor.run()

        assert resultado is False
        mock_fetch.assert_not_called()
        mock_notifier.send_message.assert_called_once()
        args, _ = mock_notifier.send_message.call_args
        assert args[1] == "error"