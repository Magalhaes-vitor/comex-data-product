import sys
import time
import logging
from datetime import date

# Extratores (Bronze)
from src.extractors.bacen_extractor import BacenExtractor
from src.extractors.mdic_extractor import MDICExtractor
from src.extractors.conab_extractor import CONABExtractor
from src.extractors.aps_extractor import APSExtractor

# Transformadores / Cleaners (Silver)
from src.transformers.bacen_cleaner import BacenCleaner
from src.transformers.mdic_cleaner import MDICCleaner
from src.transformers.conab_cleaner import CONABCleaner
from src.transformers.aps_cleaner import APSCleaner

# Camada Gold
from src.transformers.gold_builder import GoldBuilder

from src.utils.storage import connector
from src.utils.notifier import notifier # IMPORTANTE: Adicionado o notifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MESES_ORDEM = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

# ---------------------------------------------------------------------------
# REGRA DE NEGÓCIO DO BACKFILL
# ---------------------------------------------------------------------------
FLOOR_MINIMO = (2019, 1)          
BACKFILL_START = (2019, 10)       
BACKFILL_END = None


def _clamp_start(start, floor_minimo):
    return start if start >= floor_minimo else floor_minimo


def _gerar_periodos(inicio, fim):
    """Gera tuplas (ano, mes_str) do início até o fim, inclusive, em ordem cronológica."""
    ano_ini, mes_ini = inicio
    if fim is None:
        hoje = date.today()
        ano_fim, mes_fim = hoje.year, hoje.month
    else:
        ano_fim, mes_fim = fim

    periodos = []
    ano, mes_num = ano_ini, mes_ini
    while (ano, mes_num) <= (ano_fim, mes_fim):
        periodos.append((ano, MESES_ORDEM[mes_num - 1]))
        if mes_num == 12:
            ano += 1
            mes_num = 1
        else:
            mes_num += 1
    return periodos


def run_historical_batch(inicio=BACKFILL_START, fim=BACKFILL_END, floor_minimo=FLOOR_MINIMO):
    inicio = _clamp_start(inicio, floor_minimo)
    periodos = _gerar_periodos(inicio, fim)

    logger.info("=== INICIANDO MOTOR DE BACKFILL HISTÓRICO (BACEN, MDIC, CONAB, APS) ===")
    logger.info(f"Intervalo: {MESES_ORDEM[inicio[1]-1].upper()}/{inicio[0]} até o mês corrente "
                f"({len(periodos)} lotes mensais).")
    
    notifier.send_message(f"🚀 *Iniciando Backfill Histórico*\nDe {MESES_ORDEM[inicio[1]-1].upper()}/{inicio[0]} até o presente ({len(periodos)} lotes).", "info")

    for ano, mes in periodos:
        logger.info("\n" + "=" * 50)
        logger.info(f">>> PROCESSANDO LOTE: {mes.upper()}/{ano} <<<")
        logger.info("=" * 50)

        falhas_lote = []

        # ---------------------------------------------------------
        # 1. ESTEIRA BACEN
        # ---------------------------------------------------------
        try:
            logger.info("--- Iniciando Pipeline BACEN ---")
            bacen_ext = BacenExtractor(ano=ano, mes=mes)
            if bacen_ext.run():
                if not BacenCleaner(ano=ano, mes=mes).extract_and_clean():
                    falhas_lote.append("Bacen (Silver)")
            else:
                 falhas_lote.append("Bacen (Bronze)")
        except Exception as e:
            logger.error(f"Falha na esteira Bacen para {mes}/{ano}: {e}")
            falhas_lote.append(f"Bacen (Erro: {str(e)[:50]})")

        # ---------------------------------------------------------
        # 2. ESTEIRA MDIC
        # ---------------------------------------------------------
        try:
            logger.info("--- Iniciando Pipeline MDIC ---")
            mdic_ext = MDICExtractor(ano=ano, mes=mes)
            if mdic_ext.run():
                if not MDICCleaner(ano=ano, mes=mes).extract_and_clean():
                    falhas_lote.append("MDIC (Silver)")
            else:
                 falhas_lote.append("MDIC (Bronze)")
        except Exception as e:
            logger.error(f"Falha na esteira MDIC para {mes}/{ano}: {e}")
            falhas_lote.append(f"MDIC (Erro: {str(e)[:50]})")

        # ---------------------------------------------------------
        # 3. ESTEIRA CONAB
        # ---------------------------------------------------------

        try:
            logger.info("--- Iniciando Pipeline CONAB ---")
            conab_ext = CONABExtractor(ano=ano, mes=mes)
            if conab_ext.run():
                if not CONABCleaner(ano=ano, mes=mes).extract_and_clean():
                     falhas_lote.append("CONAB (Silver)")
            else:
                logger.info(
                    f"CONAB sem boletim publicado para {mes.upper()}/{ano} — "
                    f"comportamento esperado, seguindo o backfill normalmente."
                )
        except Exception as e:
            logger.error(f"Falha na esteira CONAB para {mes}/{ano}: {e}")
            falhas_lote.append(f"CONAB (Erro: {str(e)[:50]})")

        # ---------------------------------------------------------
        # 4. ESTEIRA APS
        # ---------------------------------------------------------
        try:
            logger.info("--- Iniciando Pipeline APS ---")
            aps_ext = APSExtractor(ano=ano, mes=mes)
            if aps_ext.run():
                prefixo = f"aps_mensario_{ano}_{mes}_id_"
                nome_arquivo = connector.find_latest_file("bronze", "aps", prefixo, ".pdf")
                if nome_arquivo:
                    if not APSCleaner(file_name=nome_arquivo, ano=ano, mes=mes).extract_and_clean():
                         falhas_lote.append("APS (Silver)")
                else:
                    logger.error(f"PDF da APS extraído mas não localizado na Bronze para {mes}/{ano}.")
                    falhas_lote.append("APS (Bronze -> Silver Não Localizado)")
            else:
                falhas_lote.append("APS (Bronze)")
        except Exception as e:
            logger.error(f"Falha na esteira APS para {mes}/{ano}: {e}")
            falhas_lote.append(f"APS (Erro: {str(e)[:50]})")

        # ---------------------------------------------------------
        # 5. CAMADA GOLD
        # ---------------------------------------------------------
        try:
            logger.info("--- Iniciando Pipeline GOLD ---")
            if not GoldBuilder(ano=ano, mes=mes).build():
                 falhas_lote.append("Gold Builder")
        except Exception as e:
            logger.error(f"Falha na construção da Gold para {mes}/{ano}: {e}")
            falhas_lote.append(f"Gold Builder (Erro: {str(e)[:50]})")

        # ---------------------------------------------------------
        # NOTIFICAÇÃO DO LOTE
        # ---------------------------------------------------------
        if falhas_lote:
            notifier.send_message(f"⚠️ *Aviso no Lote {mes.upper()}/{ano}*\nO backfill reportou problemas nas seguintes etapas:\n" + "\n".join([f"- {f}" for f in falhas_lote]), "warning")
        else:
             notifier.send_message(f"✅ *Lote Concluído: {mes.upper()}/{ano}*\nTodas as fontes essenciais e a Camada Gold foram processadas com sucesso.", "success")

        logger.info("\nLote concluído. Aguardando 5 segundos de resfriamento para as APIs...")
        time.sleep(5)

    notifier.send_message(f"🏁 *Backfill Histórico Finalizado!*\nProcessados {len(periodos)} lotes.", "success")
    logger.info("=== LOTE DE BACKFILL CONCLUÍDO ===")


if __name__ == "__main__":
    run_historical_batch()