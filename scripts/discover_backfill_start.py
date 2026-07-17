"""
Diagnóstico de disponibilidade histórica por fonte.

Roda apenas as etapas de *descoberta* de cada extrator (sem baixar/gravar
nada em Bronze) para achar, mês a mês a partir de PROBE_START, o primeiro
período em que cada fonte responde com dado válido. No fim, calcula:

    inicio_comum_backfill = max(FLOOR_MINIMO, max(piso de cada fonte))

Ou seja: usa a fonte mais "atrasada" para definir o início comum, mas nunca
abaixo do piso de segurança definido pelo negócio (padrão: 2019-01).

Uso:
    python discover_backfill_start.py

Requer rede real (não roda no sandbox usado para gerar este script).
"""
import sys
import time
import logging
import calendar

project_root = __file__.rsplit("/", 1)[0]
if project_root not in sys.path:
    sys.path.append(project_root)

from src.extractors.bacen_extractor import BacenExtractor
from src.extractors.mdic_extractor import MDICExtractor
from src.extractors.conab_extractor import CONABExtractor
from src.extractors.aps_extractor import APSExtractor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MESES_ORDEM = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
MESES_NUM = {m: i + 1 for i, m in enumerate(MESES_ORDEM)}

PROBE_START = (2015, 1)   # a partir de quando testar
PROBE_END = (2020, 12)    # até quando testar antes de desistir de uma fonte
FLOOR_MINIMO = (2019, 1)  # piso de segurança do negócio — nunca abaixo disso


def _periodos(inicio, fim):
    ano, mes_num = inicio
    ano_fim, mes_fim = fim
    while (ano, mes_num) <= (ano_fim, mes_fim):
        yield (ano, MESES_ORDEM[mes_num - 1])
        if mes_num == 12:
            ano += 1
            mes_num = 1
        else:
            mes_num += 1


def probe_bacen():
    for ano, mes in _periodos(PROBE_START, PROBE_END):
        try:
            ext = BacenExtractor(ano=ano, mes=mes)
            payload = ext.fetch_ptax_data(ext.data_ini, ext.data_fim)
            if payload:
                logger.info(f"[BACEN] Primeiro período com dado: {mes.upper()}/{ano}")
                return (ano, MESES_NUM[mes])
        except Exception as e:
            logger.warning(f"[BACEN] {mes}/{ano} falhou: {e}")
        time.sleep(0.5)
    return None


def probe_mdic():
    for ano, mes in _periodos(PROBE_START, PROBE_END):
        try:
            ext = MDICExtractor(ano=ano, mes=mes)
            payload = ext.fetch_comex_data(int(ext.ano), ext.mes_num)
            if payload:
                logger.info(f"[MDIC] Primeiro período com dado: {mes.upper()}/{ano}")
                return (ano, MESES_NUM[mes])
        except Exception as e:
            logger.warning(f"[MDIC] {mes}/{ano} falhou: {e}")
        time.sleep(0.5)
    return None


def probe_conab():
    for ano, mes in _periodos(PROBE_START, PROBE_END):
        try:
            ext = CONABExtractor(ano=ano, mes=mes)
            # Só descobre a URL do xlsx, não baixa nem faz parsing — mais
            # leve para rodar em loop probing.
            html = ext._fetch_listagem_html()
            url = ext.find_xlsx_url(html, ext.mes, ext.ano)
            if not url:
                url = ext.find_xlsx_url_via_navegacao(ext.mes, ext.ano)
            if url:
                logger.info(f"[CONAB] Primeiro período com dado: {mes.upper()}/{ano} -> {url}")
                return (ano, MESES_NUM[mes])
        except Exception as e:
            logger.warning(f"[CONAB] {mes}/{ano} falhou: {e}")
        time.sleep(0.5)
    return None


def probe_aps():
    for ano, mes in _periodos(PROBE_START, PROBE_END):
        try:
            ext = APSExtractor(ano=ano, mes=mes)
            link = ext.get_specific_pdf_link(ext.ano, ext.mes)
            if link:
                logger.info(f"[APS] Primeiro período com dado: {mes.upper()}/{ano} -> {link}")
                return (ano, MESES_NUM[mes])
        except Exception as e:
            logger.warning(f"[APS] {mes}/{ano} falhou: {e}")
        time.sleep(0.5)
    return None


def main():
    resultados = {
        "BACEN": probe_bacen(),
        "MDIC": probe_mdic(),
        "CONAB": probe_conab(),
        "APS": probe_aps(),
    }

    logger.info("\n" + "=" * 50)
    logger.info("RESULTADO DO DIAGNÓSTICO")
    logger.info("=" * 50)

    pisos_encontrados = []
    for fonte, resultado in resultados.items():
        if resultado is None:
            logger.warning(f"{fonte}: nenhum dado encontrado no intervalo testado "
                            f"({PROBE_START} a {PROBE_END}).")
        else:
            logger.info(f"{fonte}: primeiro período disponível = {resultado}")
            pisos_encontrados.append(resultado)

    if not pisos_encontrados:
        inicio_comum = FLOOR_MINIMO
    else:
        inicio_comum = max(FLOOR_MINIMO, max(pisos_encontrados))

    logger.info(f"\n>>> DATA DE INÍCIO RECOMENDADA PARA O BACKFILL: "
                f"{MESES_ORDEM[inicio_comum[1]-1].upper()}/{inicio_comum[0]} <<<")
    logger.info("Copie esse valor para BACKFILL_START em backfill_orchestrator.py.")

    return inicio_comum


if __name__ == "__main__":
    main()
