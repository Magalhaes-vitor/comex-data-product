import os
import sys
import pytest
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from src.transformers.cleaner import APSCleaner

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "aps")

@pytest.mark.parametrize("file_name, ano, mes", [
    ("estatistica_161312.pdf", 2026, "jan"),
    ("estatistica_161678.pdf", 2026, "fev"),
    ("estatistica_162164.pdf", 2026, "mar"),
    ("estatistica_162604.pdf", 2026, "abr")
])
def test_aps_cleaner_parsing_com_pdfs_reais(file_name, ano, mes):
    fixture_path = os.path.join(FIXTURES_DIR, file_name)
    
    if not os.path.exists(fixture_path):
        pytest.skip(f"Fixture {file_name} não encontrada em {FIXTURES_DIR}")
        
    # Cria uma pasta temporária isolada para o teste
    with tempfile.TemporaryDirectory() as tmp_silver_dir:
        cleaner = APSCleaner(file_name=file_name, ano=ano, mes=mes)
        
        # Sobrescreve os caminhos reais para forçar a leitura da fixture e gravação na pasta temporária
        cleaner.bronze_path = fixture_path
        cleaner.silver_dir = tmp_silver_dir
        
        sucesso = cleaner.extract_and_clean()
        
        # Valida se a extração ocorreu sem disparar erros de Data Drift ou Layout
        assert sucesso is True, f"Falha no parsing do arquivo: {file_name}"
        
        # Valida se o DataFrame final foi salvo fisicamente em Parquet
        silver_file = os.path.join(tmp_silver_dir, f"aps_movimentacao_{ano}_{mes}.parquet")
        assert os.path.exists(silver_file), f"Arquivo Parquet não gerado para {mes}/{ano}"