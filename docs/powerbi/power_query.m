// ============================================================================
// Comex Data Product — View SQL (AWS Athena) e Power Query M
// ============================================================================

// ----------------------------------------------------------------------------
// 1. ATUALIZAÇÃO DA VIEW NO AWS ATHENA (Rodar no console da AWS antes do PBI)
// Necessário para incluir a coluna "mercadoria" no GROUP BY e evitar falha 
// de granularidade no cálculo da porcentagem dos estados.
// ----------------------------------------------------------------------------
/*
CREATE OR REPLACE VIEW vw_origem_agricola_export AS 
SELECT 
    ano, 
    mes, 
    estado, 
    mercadoria, 
    SUM(volume_toneladas_estimado) AS volume_toneladas_estimado 
FROM fato_origem_agricola 
WHERE sentido = 'Exportação' 
GROUP BY 
    ano, 
    mes, 
    estado, 
    mercadoria;
*/

// ============================================================================
// COMO USAR NO POWER BI:
// 1. Obter dados > Amazon Athena (ou ODBC genérico) >
//    informe o DSN configurado com o driver Simba Athena ODBC.
//    O Athena não tem um conector "M nativo" documentado publicamente — a tela
//    de conexão gera o primeiro passo (Source) automaticamente a partir do DSN.
//    Depois de conectar e escolher Transformar Dados, abra o Editor Avançado
//    de cada consulta e insira os passos abaixo LOGO APÓS o passo "Source"
//    gerado pela interface (mantenha o Source original, só acrescente o resto).
// 2. Rode antes, no console do Athena, as views do bloco de SQL acima.
//    Conecte nas views, não nas tabelas brutas.
// ============================================================================

// ----------------------------------------------------------------------------
// Consulta: Mapa_Meses
// Tabela de apoio: converte a abreviação de mês em português ("abr") usada
// nas fontes em número de mês — necessária porque nenhuma das 3 tabelas Gold
// guarda o mês como número, e sem isso não dá para montar uma coluna de data.
// ----------------------------------------------------------------------------
let
    Origem = #table(
        {"mes", "mes_numero"},
        {
            {"jan", 1}, {"fev", 2}, {"mar", 3}, {"abr", 4},
            {"mai", 5}, {"jun", 6}, {"jul", 7}, {"ago", 8},
            {"set", 9}, {"out", 10}, {"nov", 11}, {"dez", 12}
        }
    )
in
    Origem

// ----------------------------------------------------------------------------
// Consulta: Dim_Calendario
// Dimensão de tempo única, de jan/2019 até o mês corrente. É ela que conecta
// as 3 tabelas fato entre si nos filtros — nunca relacione as fatos direto
// umas com as outras (grãos diferentes = fan-out).
// ----------------------------------------------------------------------------
let
    DataInicio = #date(2019, 1, 1),
    DataFim = Date.EndOfMonth(DateTime.LocalNow()),
    ListaDatas = List.Dates(DataInicio, Duration.Days(DataFim - DataInicio) + 1, #duration(1, 0, 0, 0)),
    Tabela = Table.FromList(ListaDatas, Splitter.SplitByNothing(), {"Data"}),
    TipoData = Table.TransformColumnTypes(Tabela, {{"Data", type date}}),
    AddAno = Table.AddColumn(TipoData, "Ano", each Text.From(Date.Year([Data])), type text),
    AddMesNum = Table.AddColumn(AddAno, "MesNumero", each Date.Month([Data]), Int64.Type),
    AddMesAbrev = Table.AddColumn(AddMesNum, "Mes",
        each {"jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"}{[MesNumero] - 1},
        type text),
    AddAnoMes = Table.AddColumn(AddMesAbrev, "AnoMes", each [Ano] & "-" & Text.PadStart(Text.From([MesNumero]), 2, "0"), type text),
    // mantém só o primeiro dia de cada mês — a granularidade real das fontes é mensal
    SoDiaUm = Table.SelectRows(AddAnoMes, each Date.Day([Data]) = 1)
in
    SoDiaUm

// ----------------------------------------------------------------------------
// Passos a acrescentar em CADA consulta de fato (vw_mdic_resumo_mensal,
// vw_mdic_top_paises, vw_mdic_top_capitulo, fato_movimentacao_cambio,
// vw_origem_agricola_export) logo após o "Source" gerado pela interface:
// ----------------------------------------------------------------------------
//
// MergeComMeses = Table.NestedJoin(Source, {"mes"}, Mapa_Meses, {"mes"}, "MesInfo", JoinKind.LeftOuter),
// ExpandeMesNumero = Table.ExpandTableColumn(MergeComMeses, "MesInfo", {"mes_numero"}, {"mes_numero"}),
// AddData = Table.AddColumn(ExpandeMesNumero, "Data",
//     each #date(Number.From([ano]), [mes_numero], 1), type date),
// TiposFinais = Table.TransformColumnTypes(AddData, {
//     {"Data", type date}
//     // adicione aqui os tipos das colunas numéricas da tabela específica,
//     // ex.: {"valor_fob_usd", type number}, {"volume_toneladas", type number}
// })
//
// A última linha (TiposFinais, ou o nome que você der ao passo) deve ser o
// "in" final da consulta.

// ----------------------------------------------------------------------------
// Nota sobre performance: rode as consultas em modo Import (não DirectQuery).
// As views já vêm agregadas do lado do Athena — importar é mais rápido e mais
// barato (menos query no Athena, que cobra por dado escaneado) do que deixar
// o Power BI reconsultar a cada interação do usuário.
// ----------------------------------------------------------------------------