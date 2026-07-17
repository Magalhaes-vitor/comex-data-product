
-- ==============================================================================
-- DDL DE CRIAÇÃO DAS TABELAS NO AWS ATHENA (CAMADA GOLD - PARTICIONADA)
-- Execute cada bloco de CREATE TABLE e MSCK REPAIR TABLE no console do Athena
-- ==============================================================================

-- ============================================
-- 1) MDIC — Balança Comercial
-- ============================================
CREATE EXTERNAL TABLE fato_balanca_mdic (
    mes STRING,
    fluxo STRING,
    pais STRING,
    valor_fob_usd DOUBLE,
    peso_kg DOUBLE,
    ncm STRING,
    ptax_media_venda DOUBLE,
    valor_fob_brl DOUBLE,
    fonte_cambio STRING
)
PARTITIONED BY (ano STRING)
STORED AS PARQUET
LOCATION 's3://comex-data-lake/data/gold/market_intelligence_mdic/'
TBLPROPERTIES (
    "parquet.compress" = "SNAPPY",
    "projection.enabled" = "true",
    "projection.ano.type" = "integer",
    "projection.ano.range" = "2019,2035",
    "storage.location.template" = "s3://comex-data-lake/data/gold/market_intelligence_mdic/${ano}/"
);

-- ============================================
-- 2) APS — Movimentação Portuária (Câmbio)
-- ============================================
CREATE EXTERNAL TABLE fato_movimentacao_cambio (
    mes STRING,
    mercadoria STRING,
    sentido STRING,
    volume_toneladas DOUBLE,
    porto STRING,
    ptax_media_compra DOUBLE,
    ptax_media_venda DOUBLE,
    fonte_cambio STRING
)
PARTITIONED BY (ano STRING)
STORED AS PARQUET
LOCATION 's3://comex-data-lake/data/gold/market_intelligence_aps/'
TBLPROPERTIES (
    "parquet.compress" = "SNAPPY",
    "projection.enabled" = "true",
    "projection.ano.type" = "integer",
    "projection.ano.range" = "2019,2035",
    "storage.location.template" = "s3://comex-data-lake/data/gold/market_intelligence_aps/${ano}/"
);

-- ============================================
-- 3) AGRO — Origem Agrícola
-- ============================================
CREATE EXTERNAL TABLE fato_origem_agricola (
    mes STRING,
    mercadoria STRING,
    sentido STRING,
    volume_toneladas DOUBLE,
    ano_referencia STRING,
    mes_referencia STRING,
    estado STRING,
    producao_mil_t DOUBLE,
    producao_total_br DOUBLE,
    share_estado DOUBLE,
    porto STRING,
    volume_toneladas_estimado DOUBLE
)
PARTITIONED BY (ano STRING)
STORED AS PARQUET
LOCATION 's3://comex-data-lake/data/gold/market_intelligence_agro/'
TBLPROPERTIES (
    "parquet.compress" = "SNAPPY",
    "projection.enabled" = "true",
    "projection.ano.type" = "integer",
    "projection.ano.range" = "2019,2035",
    "storage.location.template" = "s3://comex-data-lake/data/gold/market_intelligence_agro/${ano}/"
);

-- ============================================================================
-- fato_origem_agricola (market_intelligence_agro)
-- ============================================================================
-- ORIGEM: gold_builder.py — join APS (silver) x CONAB (silver) via De-Para
-- semântico (src/reference/mercadoria_cultura_map.csv), usando mercadoria
-- (APS) -> cultura_conab -> cultura (CONAB).
--
-- ⚠ REGRA 1 — COBERTURA MENSAL NÃO GARANTIDA:
-- A CONAB só publica boletim de safra em parte dos meses do ano. Quando o
-- arquivo Silver da CONAB não existe/está vazio para um ano/mes, o Fato Agro
-- inteiro é PULADO naquele período (nenhum parquet é gravado em
-- .../market_intelligence_agro/{ano}/) — diferente de fato_movimentacao_cambio
-- e fato_balanca_mdic, que são gerados todo mês independentemente da CONAB.
-- Ou seja: "sem partição para o mês" aqui é esperado, não é falha de pipeline.
--
-- ⚠ REGRA 2 — volume_toneladas_estimado É NULO PARA IMPORTAÇÃO POR DESIGN:
-- A estimativa por estado (volume_toneladas * share_estado) só é calculada
-- quando sentido = 'Exportação', pois o share de produção do CONAB reflete
-- origem da lavoura brasileira. Cargas de IMPORTAÇÃO (ex.: trigo argentino)
-- não têm correspondência com estados produtores nacionais, então o campo
-- fica NULL propositalmente — não é dado ausente/erro de join.
--
-- ⚠ REGRA 3 — MODELO ESTÁTICO, NÃO LOGÍSTICO:
-- volume_toneladas_estimado é uma alocação heurística baseada no share
-- produtivo nacional (CONAB), não reflete rota logística real. Estados sem
-- escoamento viável pelo Porto de Santos (ex.: RR, AC) ainda recebem uma
-- fração proporcional à sua produção nacional.
-- ============================================================================

CREATE EXTERNAL TABLE fato_origem_agricola (
    mes STRING COMMENT 'Mês de referência da movimentação portuária (aps)',
    mercadoria STRING COMMENT 'Categoria de carga na APS (ex.: Soja em Grãos, Milho)',
    sentido STRING COMMENT 'Importação ou Exportação',
    volume_toneladas DOUBLE COMMENT 'Volume total movimentado no Porto de Santos (t)',
    ano_referencia STRING COMMENT 'Ano de referência do boletim CONAB usado no join',
    mes_referencia STRING COMMENT 'Mês de referência do boletim CONAB usado no join',
    estado STRING COMMENT 'UF de origem da produção (CONAB)',
    producao_mil_t DOUBLE COMMENT 'Produção da cultura no estado, em mil t (CONAB)',
    producao_total_br DOUBLE COMMENT 'Produção nacional total da cultura no período (CONAB)',
    share_estado DOUBLE COMMENT 'Participação do estado na produção nacional da cultura',
    porto STRING COMMENT 'Fixo: Porto de Santos',
    volume_toneladas_estimado DOUBLE COMMENT
        'Volume alocado ao estado (volume_toneladas * share_estado). '
        'NULO por design quando sentido = Importação — ver Regra 2 no header.'
)
COMMENT
    'Fato Agro: cruza movimentação portuária (APS) com safra por estado (CONAB) '
    'via De-Para semântico mercadoria->cultura. COBERTURA MENSAL NÃO GARANTIDA: '
    'meses sem boletim CONAB publicado não geram partição nesta tabela (ver Regra 1). '
    'volume_toneladas_estimado é NULO para Importação por design (ver Regra 2).'
PARTITIONED BY (ano STRING)
STORED AS PARQUET
LOCATION 's3://comex-data-lake/data/gold/market_intelligence_agro/'
TBLPROPERTIES (
    "parquet.compress" = "SNAPPY",
    "projection.enabled" = "true",
    "projection.ano.type" = "integer",
    "projection.ano.range" = "2019,2035",
    "storage.location.template" = "s3://comex-data-lake/data/gold/market_intelligence_agro/${ano}/"
);

-- ==============================================================================
-- CONSULTAS SQL PARA VALIDAÇÃO DE DADOS (TESTES DE QA)
-- ==============================================================================

-- 1. Qual o volume total exportado de Soja e o valor projetado em Reais (BRL)?
SELECT 
    mercadoria,
    SUM(volume_toneladas) AS total_exportado_ton,
    MAX(ptax_media_venda) AS ptax_aplicada
FROM 
    comex_data_product.fato_movimentacao_cambio
WHERE 
    sentido = 'Exportação' 
    AND mercadoria LIKE '%Soja%'
GROUP BY 
    mercadoria;

-- 2. De onde (qual Estado) veio a carga agrícola escoada pelo porto?
SELECT 
    mercadoria,
    estado,
    share_estado * 100 AS participacao_producao_nacional_pct,
    volume_toneladas_estimado AS volume_escoado_pelo_porto_ton
FROM 
    comex_data_product.fato_origem_agricola
ORDER BY 
    volume_toneladas_estimado DESC;