import os
import sys
import time
import logging
import subprocess

# Garante que o Python encontre a pasta src/
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.notifier import notifier
from src.utils.date_rules import DateRules

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_script(script_path, step_name):
    """Executa um script Python como um subprocesso e monitoriza o código de saída."""
    logger.info(f"--- Iniciando: {step_name} ({script_path}) ---")
    try:
        caminho_absoluto = os.path.join(project_root, script_path)
        subprocess.run([sys.executable, caminho_absoluto], check=True)
        logger.info(f"--- Sucesso: {step_name} ---")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"--- FALHA: {step_name} retornou o código de erro {e.returncode} ---")
        return False
    except Exception as e:
        logger.error(f"--- ERRO INESPERADO ao executar {step_name}: {e} ---")
        return False

def main():
    # Inicia o cronômetro
    start_time = time.time()
    
    periodo = DateRules.get_target_period()
    mes_ano = f"{periodo['mes_str'].upper()}/{periodo['ano']}"
    
    logger.info(f"=== INICIANDO PIPELINE DE DADOS (Referência: {mes_ano}) ===")
    notifier.send_message(f" *Iniciando Pipeline de Dados ({mes_ano})*", "info")

    # =========================================================================
    # FASE 1: CAMADA BRONZE (Extração de Dados Brutos)
    # =========================================================================
    logger.info(">>> FASE 1: EXTRAÇÃO (CAMADA BRONZE)")
    extractors = [
        ("src/extractors/aps_extractor.py", "Extrator APS (Movimentação)"),
        ("src/extractors/bacen_extractor.py", "Extrator BACEN (PTAX)"),
        ("src/extractors/mdic_extractor.py", "Extrator MDIC (Balança Comercial)"),
        ("src/extractors/conab_extractor.py", "Extrator CONAB (Safra Agrícola)"),
        
        # [WIP] Descomentar quando a manutenção da API terminar
        # ("src/extractors/antaq_extractor.py", "Extrator ANTAQ (Atracação)")
    ]

    for script, name in extractors:
        if not run_script(script, name):
            msg = f"Pipeline interrompido na Extração (Bronze): O {name} falhou."
            logger.error(msg)
            notifier.send_message(f"🚨 *Falha Crítica no Pipeline*\n{msg}", "error")
            sys.exit(1)

    # =========================================================================
    # FASE 2: CAMADA SILVER (Limpeza, Tipagem e Quarentena)
    # =========================================================================
    logger.info(">>> FASE 2: TRANSFORMAÇÃO (CAMADA SILVER)")
    cleaners = [
        ("src/transformers/aps_cleaner.py", "Cleaner APS"),
        ("src/transformers/bacen_cleaner.py", "Cleaner BACEN"),
        ("src/transformers/mdic_cleaner.py", "Cleaner MDIC"),
        ("src/transformers/conab_cleaner.py", "Cleaner CONAB"),
        
        # [WIP] Descomentar quando o limpador for implementado
        # ("src/transformers/antaq_cleaner.py", "Cleaner ANTAQ")
    ]

    for script, name in cleaners:
        if not run_script(script, name):
            msg = f"Pipeline interrompido na Limpeza (Silver): O {name} falhou."
            logger.error(msg)
            notifier.send_message(f"🚨 *Falha Crítica no Pipeline*\n{msg}", "error")
            sys.exit(1)

    # =========================================================================
    # FASE 3: CAMADA GOLD (Cruzamento de Inteligência de Negócio)
    # =========================================================================
    logger.info(">>> FASE 3: CONSOLIDAÇÃO (CAMADA GOLD)")
    if not run_script("src/transformers/gold_builder.py", "Gold Builder (Consolidador Fato)"):
        msg = "Pipeline interrompido na Consolidação (Gold): O cruzamento final falhou."
        logger.error(msg)
        notifier.send_message(f"🚨 *Falha Crítica no Pipeline*\n{msg}", "error")
        sys.exit(1)

    # =========================================================================
    # FINALIZAÇÃO E CÁLCULO DE TEMPO
    # =========================================================================
    end_time = time.time()
    elapsed_seconds = end_time - start_time
    mins, secs = divmod(elapsed_seconds, 60)
    tempo_formatado = f"{int(mins)}m {secs:.1f}s" if mins > 0 else f"{secs:.1f}s"

    logger.info(f"=== PIPELINE CONCLUÍDO COM SUCESSO EM {tempo_formatado} ===")
    notifier.send_message(
        f"✅ *Pipeline Finalizado ({mes_ano})*\n⏳ *Tempo total de execução:* {tempo_formatado}\nTodas as camadas (Bronze, Silver, Gold) foram processadas com sucesso!", 
        "success"
    )
    sys.exit(0)

if __name__ == "__main__":
    main()