"""
Suite de testes unitários para o módulo CONABExtractor (Agronegócio).
Garante o comportamento correto do web scraping, regex, retries e persistência,
sem realizar chamadas reais à página do governo ou ao AWS S3.
"""

import pytest
import requests
import tenacity
from unittest.mock import patch, MagicMock

from src.extractors.conab_extractor import CONABExtractor, _is_retryable


# ==============================================================================
# FIXTURES E CONFIGURAÇÕES GERAIS
# ==============================================================================

@pytest.fixture(autouse=True)
def no_wait_between_retries():
    """
    Desativa o backoff exponencial e os sleeps durante a execução dos testes
    para garantir que a suite rode de forma instantânea.
    """
    original_wait_html = CONABExtractor._fetch_listagem_html.retry.wait
    original_wait_xlsx = CONABExtractor._download_xlsx.retry.wait
    
    CONABExtractor._fetch_listagem_html.retry.wait = tenacity.wait_none()
    CONABExtractor._download_xlsx.retry.wait = tenacity.wait_none()
    
    with patch("src.extractors.conab_extractor.time.sleep", return_value=None):
        yield
        
    CONABExtractor._fetch_listagem_html.retry.wait = original_wait_html
    CONABExtractor._download_xlsx.retry.wait = original_wait_xlsx


@pytest.fixture
def extractor():
    """Retorna uma instância limpa do CONABExtractor para cada teste."""
    return CONABExtractor()


def make_response(status_code=200, content=b"", text="erro"):
    """Utilitário para fabricar respostas HTTP mockadas do Requests."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content
    resp.text = text
    
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} Client Error", response=resp
        )
    else:
        resp.raise_for_status.side_effect = None
        
    return resp


# ==============================================================================
# TESTES: LÓGICA DE RETRY (_is_retryable)
# ==============================================================================

class TestIsRetryable:
    def test_erro_5xx_deve_permitir_retry(self):
        resp = make_response(status_code=502)
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable(exc) is True

    def test_erro_404_nao_deve_permitir_retry(self):
        resp = make_response(status_code=404)
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable(exc) is False

    def test_timeout_deve_permitir_retry(self):
        assert _is_retryable(requests.exceptions.Timeout()) is True

    def test_connection_error_deve_permitir_retry(self):
        assert _is_retryable(requests.exceptions.ConnectionError()) is True


# ==============================================================================
# TESTES: REGEX DE LOCALIZAÇÃO DO ARQUIVO (find_xlsx_url)
# ==============================================================================

class TestFindXlsxUrl:
    """Valida se a lógica de Web Scraping consegue encontrar a URL correta no HTML."""
    
    def test_encontra_url_com_http_direto(self):
        html = '<a href="https://site.gov.br/arquivos/previsao_de_safra-por_produto-mai-2026.xlsx">Link</a>'
        url = CONABExtractor.find_xlsx_url(html, "mai", "2026")
        assert url == "https://site.gov.br/arquivos/previsao_de_safra-por_produto-mai-2026.xlsx"

    def test_encontra_url_sem_protocolo_iniciado(self):
        html = 'href="//site.gov.br/previsao_de_safra-por_produto-mai-2026.xlsx"'
        url = CONABExtractor.find_xlsx_url(html, "mai", "2026")
        assert url == "https://site.gov.br/previsao_de_safra-por_produto-mai-2026.xlsx"

    def test_encontra_url_relativa(self):
        html = 'href="/caminho/previsao_de_safra-por_produto-mai-2026.xlsx"'
        url = CONABExtractor.find_xlsx_url(html, "mai", "2026")
        assert url == "https://www.gov.br/caminho/previsao_de_safra-por_produto-mai-2026.xlsx"

    def test_retorna_none_se_nao_encontrar_arquivo_do_mes(self):
        html = '<a href="https://site.gov.br/arquivos/outro_arquivo_qualquer.xlsx">Link</a>'
        url = CONABExtractor.find_xlsx_url(html, "mai", "2026")
        assert url is None


# ==============================================================================
# TESTES: EXTRAÇÃO E CONVERSÃO (fetch_crop_data e parse_xlsx_to_json)
# ==============================================================================

class TestFetchCropData:
    @patch("src.extractors.conab_extractor.requests.get")
    def test_fetch_html_sucesso(self, mock_get, extractor):
        mock_get.return_value = make_response(200, text="<html>conteudo</html>")
        html = extractor._fetch_listagem_html()
        assert html == "<html>conteudo</html>"

    @patch("src.extractors.conab_extractor.requests.get")
    def test_download_xlsx_falha_500_esgota_retry(self, mock_get, extractor):
        mock_get.return_value = make_response(500)
        with pytest.raises(requests.exceptions.HTTPError):
            extractor._download_xlsx("http://url.com/safra.xlsx")
        assert mock_get.call_count == 3

    @patch("src.extractors.conab_extractor.pd.read_excel")
    def test_parse_xlsx_to_json_valida_estrutura(self, mock_read_excel):
        import pandas as pd
        # Simula o retorno do pd.read_excel (onde sheet_name=None retorna um dict de DataFrames)
        df_mock = pd.DataFrame([{"col1": "valor1", "col2": None}])
        mock_read_excel.return_value = {"Soja": df_mock}
        
        resultado = CONABExtractor.parse_xlsx_to_json(b"bytes_simulados")
        
        # Valida se os Nones do Pandas foram convertidos corretamente
        assert resultado == {"Soja": [{"col1": "valor1", "col2": None}]}

    @patch.object(CONABExtractor, "_fetch_listagem_html")
    @patch.object(CONABExtractor, "find_xlsx_url")
    def test_fetch_crop_data_abortado_se_url_nao_encontrada(self, mock_find, mock_html, extractor):
        mock_html.return_value = "<html>Sem link</html>"
        mock_find.return_value = None
        
        resultado = extractor.fetch_crop_data("2026", "mai")
        assert resultado is None


# ==============================================================================
# TESTES: PIPELINE COMPLETO (run)
# ==============================================================================

class TestRun:
    @patch("src.extractors.conab_extractor.notifier")
    @patch("src.extractors.conab_extractor.DateRules")
    def test_run_sucesso_completo_notifica_sucesso(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "mai"}
        
        with patch.object(extractor, "fetch_crop_data", return_value={"sheets": {"Soja": []}}) as mock_fetch, \
             patch.object(extractor, "save_to_bronze", return_value=True) as mock_save:
             
            resultado = extractor.run()
            
            assert resultado is True
            mock_fetch.assert_called_once_with("2026", "mai")
            mock_save.assert_called_once_with({"sheets": {"Soja": []}}, "2026", "mai")
            mock_notifier.send_message.assert_not_called() # Notificação de sucesso ocorre fora do run() no __main__

    @patch("src.extractors.conab_extractor.notifier")
    @patch("src.extractors.conab_extractor.DateRules")
    def test_run_falha_sem_dados_notifica_warning(self, mock_date_rules, mock_notifier, extractor):
        mock_date_rules.get_target_period.return_value = {"ano": 2026, "mes_str": "mai"}
        
        with patch.object(extractor, "fetch_crop_data", return_value=None):
            resultado = extractor.run()
            
        assert resultado is False
        mock_notifier.send_message.assert_called_once()
        args, _ = mock_notifier.send_message.call_args
        assert "indisponíveis" in args[0]
        assert args[1] == "warning"