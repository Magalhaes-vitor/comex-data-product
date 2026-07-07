import os
import sys
import pytest
from pydantic import ValidationError

# Adiciona a raiz do projeto ao path para conseguirmos importar a pasta src
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.models.contracts import MovimentacaoPortuaria, CotacaoBacen

def test_movimentacao_portuaria_limpeza_numero_brasileiro():
    """Testa se o validador limpa o formato brasileiro (ex: 1.234,56) corretamente."""
    dado = MovimentacaoPortuaria(
        ano=2026,
        mes="mai",
        mercadoria="Soja em Grãos",
        tipo_operacao="Exportação",
        volume_toneladas="1.450.320,55" # String com formatação PT-BR
    )
    # O Pydantic deve converter automaticamente para um float limpo
    assert dado.volume_toneladas == 1450320.55

def test_movimentacao_portuaria_bloqueia_mes_invalido():
    """Testa se o contrato gera um erro (Data Drift) caso o mês venha fora do padrão."""
    with pytest.raises(ValidationError):
        MovimentacaoPortuaria(
            ano=2026,
            mes="maio", # O contrato exige exatamente 3 letras (ex: 'mai')
            mercadoria="Milho",
            tipo_operacao="Importação",
            volume_toneladas="1000"
        )

def test_cotacao_bacen_extracao_data_com_hora():
    """Testa se a data vinda da API (com horas) é limpa apenas para YYYY-MM-DD."""
    dado = CotacaoBacen(
        data_cotacao="2026-05-04 13:09:25.789",
        cotacao_compra=4.9581,
        cotacao_venda=4.9587
    )
    # A hora deve ser descartada para permitir o join posterior
    assert dado.data_cotacao == "2026-05-04"

def test_cotacao_bacen_bloqueia_texto_na_cotacao():
    """Garante que a cotação aceita apenas números válidos."""
    with pytest.raises(ValidationError):
        CotacaoBacen(
            data_cotacao="2026-05-04",
            cotacao_compra="quatro reais", # Valor inválido intencional
            cotacao_venda=4.9587
        )