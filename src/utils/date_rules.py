import calendar
from datetime import datetime

class DateRules:
    MESES_PT = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

    @classmethod
    def get_target_period(cls, reference_date: datetime = None) -> dict:
        if reference_date is None:
            reference_date = datetime.now()
            
        dia_atual = reference_date.day
        mes_atual = reference_date.month
        ano_atual = reference_date.year

        if dia_atual <= 15:
            mes_alvo_num = mes_atual - 2
        else:
            mes_alvo_num = mes_atual - 1

        ano_alvo = ano_atual
        if mes_alvo_num <= 0:
            mes_alvo_num += 12
            ano_alvo -= 1

        _, ultimo_dia = calendar.monthrange(ano_alvo, mes_alvo_num)
        
        return {
            "ano": str(ano_alvo),
            "mes_str": cls.MESES_PT[mes_alvo_num - 1],
            "mes_num": mes_alvo_num,
            "data_inicial": f"{mes_alvo_num:02d}-01-{ano_alvo}",
            "data_final": f"{mes_alvo_num:02d}-{ultimo_dia:02d}-{ano_alvo}"
        }