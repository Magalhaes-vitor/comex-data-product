import os
import sys
import pytest
from datetime import datetime
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from src.extractors.bacen_extractor import BacenExtractor

def test_bacen_extractor_calculo_de_datas_inicio_do_mes():
    """
    Testa se o extrator calcula corretamente o mês alvo caso o robô rode no 
    início do mês (ex: dia 10 de Junho deve buscar Abril).
    """
    extractor = BacenExtractor()
    
    # Usamos o 'patch' (Mock) para congelar o tempo. 
    # Fingimos que hoje é dia 10 de Junho de 2026.
    with patch('src.extractors.bacen_extractor.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 6, 10)
        
        data_inicial, data_final, ano, mes = extractor.get_target_period_dates()
        
        # Como é dia 10 (antes do dia 15), o robô tem que voltar 2 meses (Abril)
        assert data_inicial == "04-01-2026"
        assert data_final == "04-30-2026"
        assert ano == "2026"
        assert mes == "abr"

def test_bacen_extractor_calculo_de_datas_fim_do_mes():
    """
    Testa se o extrator calcula corretamente o mês alvo caso o robô rode no 
    final do mês (ex: dia 20 de Junho deve buscar Maio).
    """
    extractor = BacenExtractor()
    
    # Fingimos que hoje é dia 20 de Junho de 2026.
    with patch('src.extractors.bacen_extractor.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 6, 20)
        
        data_inicial, data_final, ano, mes = extractor.get_target_period_dates()
        
        # Como é dia 20 (depois do dia 15), o robô tem que voltar 1 mês (Maio)
        assert data_inicial == "05-01-2026"
        assert data_final == "05-31-2026"
        assert ano == "2026"
        assert mes == "mai"