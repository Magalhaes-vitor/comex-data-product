-- =========================================================================
-- DATABASE CONFIGURATION
-- =========================================================================
CREATE DATABASE IF NOT EXISTS comex_data_product;

-- =========================================================================
-- TABELA 1: FATO MOVIMENTAÇÃO E CÂMBIO (APS)
-- =========================================================================
CREATE EXTERNAL TABLE IF NOT EXISTS comex_data_product.fato_movimentacao_cambio (
    porto STRING,
    sentido STRING,
    mercadoria STRING,
    volume_toneladas DOUBLE,
    ptax_media_compra DOUBLE,
    ptax_media_venda DOUBLE,
    fonte_cambio STRING
)
STORED AS PARQUET
LOCATION 's3://comex-data-lake-magalhaes-vitor/data/gold/market_intelligence_aps/'
TBLPROPERTIES ('classification'='parquet');

-- =========================================================================
-- TABELA 2: FATO BALANÇA COMERCIAL (MDIC)
-- =========================================================================
CREATE EXTERNAL TABLE IF NOT EXISTS comex_data_product.fato_balanca_mdic (
    ano STRING,
    mes STRING,
    ncm STRING,
    fluxo STRING,
    valor_fob_usd DOUBLE,
    ptax_media_venda DOUBLE,
    valor_fob_brl DOUBLE,
    fonte_cambio STRING
)
STORED AS PARQUET
LOCATION 's3://comex-data-lake-magalhaes-vitor/data/gold/market_intelligence_mdic/'
TBLPROPERTIES ('classification'='parquet');

-- =========================================================================
-- TABELA 3: FATO ORIGEM AGRÍCOLA (APS + CONAB)
-- =========================================================================
CREATE EXTERNAL TABLE IF NOT EXISTS comex_data_product.fato_origem_agricola (
    porto STRING,
    sentido STRING,
    mercadoria STRING,
    volume_toneladas DOUBLE,
    estado STRING,
    producao_mil_t DOUBLE,
    producao_total_br DOUBLE,
    share_estado DOUBLE,
    volume_toneladas_estimado DOUBLE
)
STORED AS PARQUET
LOCATION 's3://comex-data-lake-magalhaes-vitor/data/gold/market_intelligence_agro/'
TBLPROPERTIES ('classification'='parquet');