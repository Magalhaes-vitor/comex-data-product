import os
import sys
import logging
import numpy as np
import pandas as pd

# Adiciona a raiz do projeto ao path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from src.utils.date_rules import DateRules
from src.utils.storage import connector
from src.utils.notifier import notifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GoldBuilder:
    def __init__(self, ano, mes):
        self.ano = str(ano)
        self.mes = str(mes)
        
        self.aps_filename = f"aps_movimentacao_{self.ano}_{self.mes}.parquet"
        self.bacen_filename = f"bacen_ptax_{self.ano}_{self.mes}.parquet"
        self.mdic_filename = f"mdic_comex_{self.ano}_{self.mes}.parquet"
        self.conab_filename = f"conab_safra_{self.ano}_{self.mes}.parquet"

        self.gold_aps_filename = f"fato_movimentacao_cambio_{self.ano}_{self.mes}.parquet"
        self.gold_mdic_filename = f"fato_balanca_mdic_{self.ano}_{self.mes}.parquet"
        self.gold_agro_filename = f"fato_origem_agricola_{self.ano}_{self.mes}.parquet"

    def build(self):
        logger.info("=== Iniciando cruzamento de dados para a Camada Gold ===")

        # 1. Leitura e Validação
        # ------------------------------------------------------------------
        # REGRA DE NEGÓCIO: a CONAB é OPCIONAL aqui. Nem todo mês tem levantamento de safra publicado.
        # ------------------------------------------------------------------
        logger.info("Lendo tabelas da camada Silver via DataLakeConnector...")
        df_aps = connector.read_parquet("silver", "aps", self.aps_filename)
        df_bacen = connector.read_parquet("silver", "bacen", self.bacen_filename)
        df_mdic = connector.read_parquet("silver", "mdic", self.mdic_filename)
        df_conab = connector.read_parquet("silver", "conab", self.conab_filename)

        conab_disponivel = df_conab is not None and not df_conab.empty

        # As fontes essenciais (câmbio, portuária e balança comercial) continuam
        # obrigatórias: sem elas não há como montar nenhum dos três fatos.
        if any(df is None or df.empty for df in [df_aps, df_bacen, df_mdic]):
            msg = "Arquivo(s) essencial(is) da Silver (APS, BACEN ou MDIC) não encontrado(s)/vazio(s)."
            logger.error(msg)
            notifier.send_message(f" *Gold Builder Abortado*\n{msg}", "warning")
            return False

        if not conab_disponivel:
            logger.info(
                f"CONAB indisponível na Silver para {self.mes.upper()}/{self.ano} "
                f"(boletim de safra não publicado neste mês — comportamento esperado). "
                f"Fato Agro será pulado; Fatos APS e MDIC seguem normalmente."
            )

        # 2. Câmbio
        ptax_media_compra = df_bacen['cotacao_compra'].mean()
        ptax_media_venda = df_bacen['cotacao_venda'].mean()

        # 3. Fato 1: APS
        logger.info("Enriquecendo dados portuários (APS)...")
        df_gold_aps = df_aps.copy()
        df_gold_aps['ano'] = self.ano
        df_gold_aps['mes'] = self.mes
        df_gold_aps['porto'] = 'Porto de Santos'
        if 'tipo_operacao' in df_gold_aps.columns:
            df_gold_aps = df_gold_aps.rename(columns={'tipo_operacao': 'sentido'})
        elif 'sentido' not in df_gold_aps.columns:
            logger.warning("Coluna 'tipo_operacao'/'sentido' ausente na Silver da APS — direção do fluxo ficará desconhecida.")
            df_gold_aps['sentido'] = 'Não Informado'
        df_gold_aps['ptax_media_compra'] = round(ptax_media_compra, 4)
        df_gold_aps['ptax_media_venda'] = round(ptax_media_venda, 4)
        df_gold_aps['fonte_cambio'] = 'Bacen API - Média Mensal'
        sucesso_aps = connector.save_parquet(df_gold_aps, "gold", "market_intelligence_aps", self.gold_aps_filename)
        
        # 4. Fato 2: MDIC
        logger.info("Enriquecendo dados da Balança Comercial (MDIC)...")
        df_gold_mdic = df_mdic.copy()
        df_gold_mdic['ano'] = self.ano
        df_gold_mdic['mes'] = self.mes
        df_gold_mdic.columns = df_gold_mdic.columns.str.lower()
        
        # Consolidando colunas de NCM que o Pydantic gerou e removendo duplicatas
        ncm_cols = [col for col in ['concm', 'co_ncm', 'ncm'] if col in df_gold_mdic.columns]
        if ncm_cols:
            # Pega a primeira coluna da lista como base
            df_gold_mdic['ncm_final'] = df_gold_mdic[ncm_cols[0]]
            # Preenche eventuais nulos usando as outras colunas (se existirem)
            for col in ncm_cols[1:]:
                df_gold_mdic['ncm_final'] = df_gold_mdic['ncm_final'].fillna(df_gold_mdic[col])
            
            # Deleta as colunas antigas (isso mata a duplicidade que quebra o Parquet)
            df_gold_mdic = df_gold_mdic.drop(columns=ncm_cols)
            # Renomeia a consolidada para 'ncm'
            df_gold_mdic = df_gold_mdic.rename(columns={'ncm_final': 'ncm'})

        df_gold_mdic['ptax_media_venda'] = round(ptax_media_venda, 4)
        usd_col = 'valor_fob_usd' if 'valor_fob_usd' in df_gold_mdic.columns else 'vl_fob'
        if usd_col in df_gold_mdic.columns:
            df_gold_mdic['valor_fob_brl'] = round(df_gold_mdic[usd_col] * ptax_media_venda, 2)
        df_gold_mdic['fonte_cambio'] = 'Bacen API - Média Mensal'
        sucesso_mdic = connector.save_parquet(df_gold_mdic, "gold", "market_intelligence_mdic", self.gold_mdic_filename)
        
        # 5. Fato 3: Agro (MDM Aplicado) — OPCIONAL, depende da CONAB
        sucesso_agro = None  # None = pulado (CONAB indisponível neste mês); True/False = tentativa executada
        qtd_agro = 0

        if conab_disponivel:
            logger.info("Construindo Inteligência Geográfica (APS + CONAB) com De-Para Semântico...")
            try:
                df_aps['volume_toneladas'] = df_aps['volume_toneladas'].astype(float)
                df_conab['producao_mil_t'] = df_conab['producao_mil_t'].astype(float)

                # A. Carga do Dicionário Semântico
                ref_path = os.path.join(project_root, "src", "reference", "mercadoria_cultura_map.csv")
                df_map = pd.read_csv(ref_path)

                # Trava de Segurança MDM: Verifica se o CSV pede culturas que não existem na CONAB Silver
                culturas_validas_conab = set(df_conab['cultura'].unique())
                culturas_no_mapa = set(df_map['cultura_conab'].dropna().unique())
                culturas_inexistentes = culturas_no_mapa - culturas_validas_conab

                if culturas_inexistentes:
                    logger.warning(f" Alerta MDM: O 'De-Para' aponta para culturas inexistentes na Silver da CONAB: {culturas_inexistentes}.")

                # B. Join do Dicionário na Tabela da APS
                df_aps_mapped = df_aps.merge(df_map, on='mercadoria', how='left')

                # C. Gestão de Rejeições (Ignora químicos e tipos de embalagem)
                nao_agricolas_e_genericos = [
                    'Adubo', 'Ácido Fosfórico', 'Enxofre', 'Sulfato Dissódico', 'Amônia', 'Amonia',
                    'Metanol', 'Estireno', 'Soda Cáustica', 'Óleo Diesel e Gasóleo', 'Sal',
                    'Óleo Combustível', 'Gasolina', 'Gás Liquefeito de Petróleo',
                    'Consumo de Bordo', 'Conteinerizada', 'Solta', 'Granel Líquido', 'Granel Sólido'
                ]

                # Filtra os válidos e busca quem ficou sem mapeamento para gerar o alerta cirúrgico
                candidatos_agro = df_aps_mapped[~df_aps_mapped['mercadoria'].isin(nao_agricolas_e_genericos)]
                mercadorias_sem_mapa = candidatos_agro[candidatos_agro['cultura_conab'].isna()]['mercadoria'].unique()

                if len(mercadorias_sem_mapa) > 0:
                    logger.warning(f" Atenção! Mercadorias possivelmente agrícolas sem chave no 'De-Para': {mercadorias_sem_mapa}")

                # D. Cálculo do Share Geográfico
                df_conab_share = df_conab.copy()
                df_conab_share['producao_total_br'] = df_conab_share.groupby('cultura')['producao_mil_t'].transform('sum')
                df_conab_share['share_estado'] = df_conab_share.apply(
                    lambda row: row['producao_mil_t'] / row['producao_total_br'] if row['producao_total_br'] > 0 else 0, axis=1
                )

                # E. O Join Definitivo usando a nova chave semântica (cultura_conab)
                df_gold_agro = pd.merge(
                    df_aps_mapped.dropna(subset=['cultura_conab']),
                    df_conab_share,
                    left_on='cultura_conab',
                    right_on='cultura',
                    how='inner'
                )

                df_gold_agro['ano'] = self.ano
                df_gold_agro['mes'] = self.mes
                df_gold_agro['porto'] = 'Porto de Santos'
                if 'tipo_operacao' in df_gold_agro.columns:
                    df_gold_agro = df_gold_agro.rename(columns={'tipo_operacao': 'sentido'})
                elif 'sentido' not in df_gold_agro.columns:
                    logger.warning("Coluna 'tipo_operacao'/'sentido' ausente na Silver da APS — direção do fluxo ficará desconhecida.")
                    df_gold_agro['sentido'] = 'Não Informado'

                # F. Projeção Heurística (Válida APENAS para Exportação)
                # O Trigo Importado da Argentina não vem das lavouras do RS.
                df_gold_agro['volume_toneladas_estimado'] = np.where(
                    df_gold_agro['sentido'] == 'Exportação',
                    (df_gold_agro['volume_toneladas'] * df_gold_agro['share_estado']).round(2),
                    np.nan
                )

                logger.info("Nota de Modelagem: A alocação (volume_toneladas_estimado) é um modelo estático baseado no share produtivo (CONAB), não refletindo gargalos logísticos reais de estados como RR/AC para o Porto de Santos.")

                # Limpeza Final
                df_gold_agro = df_gold_agro.drop(columns=['cultura', 'cultura_conab'])
                sucesso_agro = connector.save_parquet(df_gold_agro, "gold", "market_intelligence_agro", self.gold_agro_filename)
                qtd_agro = len(df_gold_agro)
            except FileNotFoundError as e:
                logger.error(f"Arquivo de Referência do De-Para não encontrado: {e}")
                sucesso_agro = False
        else:
            logger.info(f"Fato Agro pulado para {self.mes.upper()}/{self.ano}: sem boletim CONAB publicado neste mês.")

        # Consolidação final: os fatos essenciais (APS e MDIC) precisam ter sido
        # salvos com sucesso. O Fato Agro é tratado à parte — sua ausência
        # esperada (CONAB não publicada) não derruba o resultado geral do mês.
        if sucesso_aps and sucesso_mdic:
            resumo = f"Fato APS: {len(df_gold_aps)} reg. | Fato MDIC: {len(df_gold_mdic)} reg."
            if sucesso_agro is True:
                resumo += f" | Fato Agro: {qtd_agro} reg."
            elif sucesso_agro is None:
                resumo += " | Fato Agro: pulado (CONAB não publicada neste mês)."
            else:
                resumo += " | Fato Agro: falhou ao salvar."
            logger.info(f"Sucesso! {resumo}")
            return True
        else:
            return False

if __name__ == "__main__":
    periodo = DateRules.get_target_period()
    ano_alvo = str(periodo["ano"])
    mes_alvo = periodo["mes_str"]
    
    logger.info(f"Regra de Negócio: Alvo do Gold Builder definido para: {mes_alvo.upper()}/{ano_alvo}")
    
    builder = GoldBuilder(ano=ano_alvo, mes=mes_alvo)
    sucesso = builder.build()
    
    if sucesso:
        notifier.send_message(f"✅ *Pipeline Gold Concluído ({mes_alvo.upper()}/{ano_alvo})*\nTabelas Fato Integradas geradas com sucesso no S3!", "success")
        sys.exit(0)
    else:
        notifier.send_message(f"❌ *Falha no Gold Builder ({mes_alvo.upper()}/{ano_alvo})*\nVerifique os logs de processamento.", "error")
        sys.exit(1)