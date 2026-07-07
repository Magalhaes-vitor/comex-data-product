import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from src.utils.date_rules import DateRules

def test_date_rules_inicio_do_mes():
    # Dia 10 de Junho -> Alvo: Abril
    ref_date = datetime(2026, 6, 10)
    resultado = DateRules.get_target_period(ref_date)
    
    assert resultado["ano"] == "2026"
    assert resultado["mes_str"] == "abr"
    assert resultado["data_inicial"] == "04-01-2026"
    assert resultado["data_final"] == "04-30-2026"

def test_date_rules_fim_do_mes():
    # Dia 20 de Junho -> Alvo: Maio
    ref_date = datetime(2026, 6, 20)
    resultado = DateRules.get_target_period(ref_date)
    
    assert resultado["ano"] == "2026"
    assert resultado["mes_str"] == "mai"
    assert resultado["data_inicial"] == "05-01-2026"
    assert resultado["data_final"] == "05-31-2026"

def test_date_rules_viragem_de_ano_inicio_do_mes():
    # Dia 10 de Janeiro de 2027 -> Alvo: Novembro de 2026
    ref_date = datetime(2027, 1, 10)
    resultado = DateRules.get_target_period(ref_date)
    
    assert resultado["ano"] == "2026"
    assert resultado["mes_str"] == "nov"
    assert resultado["data_inicial"] == "11-01-2026"
    assert resultado["data_final"] == "11-30-2026"

def test_date_rules_viragem_de_ano_fim_do_mes():
    # Dia 20 de Janeiro de 2027 -> Alvo: Dezembro de 2026
    ref_date = datetime(2027, 1, 20)
    resultado = DateRules.get_target_period(ref_date)
    
    assert resultado["ano"] == "2026"
    assert resultado["mes_str"] == "dez"
    assert resultado["data_inicial"] == "12-01-2026"
    assert resultado["data_final"] == "12-31-2026"