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