CREATE EXTERNAL TABLE IF NOT EXISTS fato_movimentacao_cambio (
    ano STRING,
    mes STRING,
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

CREATE EXTERNAL TABLE IF NOT EXISTS fato_balanca_mdic (
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

CREATE EXTERNAL TABLE IF NOT EXISTS fato_origem_agricola (
    ano STRING,
    mes STRING,
    ano_referencia STRING,
    mes_referencia STRING,
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