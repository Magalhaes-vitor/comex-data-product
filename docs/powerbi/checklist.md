# Checklist de Configuração — Power BI (Frontend)

Este documento descreve os passos exatos para conectar o modelo semântico do Power BI ao Data Lake construído no AWS Athena, garantindo que as medidas DAX e o roteamento de datas funcionem corretamente.

## Passo 1: Conexão com o AWS Athena
1. No Power BI Desktop, vá em **Obter Dados > Mais... > Amazon Athena** (ou ODBC genérico caso utilize o driver Simba Athena).
2. Insira o DSN configurado na sua máquina local.
3. Conecte-se às views da camada Gold (e não às tabelas brutas):
   - `vw_mdic_resumo_mensal`
   - `vw_mdic_top_paises`
   - `vw_mdic_top_capitulo`
   - `vw_origem_agricola_export`
   - `fato_movimentacao_cambio`

## Passo 2: Transformação (Power Query M)
1. Clique em **Transformar Dados**.
2. Cole o código do arquivo `power_query.m` nas respectivas consultas (Advanced Editor) logo após a etapa `Source` gerada automaticamente.
3. Isso garantirá a criação da coluna `Data` em formato padrão de data (tipo `date`) a partir do cruzamento com a tabela de apoio `Mapa_Meses`.

## Passo 3: Criação da Dimensão Calendário
1. Utilize o script M fornecido para criar a tabela `Dim_Calendario`.
2. Essa tabela atuará como a dimensão de tempo única (jan/2019 até o mês corrente), filtrando todas as tabelas fato simultaneamente.

## Passo 4: Relacionamentos (Modelo de Dados)
Vá para a aba "Exibição de Modelo" e garanta que os seguintes relacionamentos sejam criados (Um para Muitos, filtro Único):
- `Dim_Calendario[Data]` 1 ➔ * `fato_movimentacao_cambio[Data]`
- `Dim_Calendario[Data]` 1 ➔ * `vw_mdic_resumo_mensal[Data]`
- `Dim_Calendario[Data]` 1 ➔ * `vw_mdic_top_paises[Data]`
- `Dim_Calendario[Data]` 1 ➔ * `vw_origem_agricola_export[Data]`

*Atenção: Nunca relacione as tabelas Fato diretamente umas com as outras para evitar ambiguidades (fan-out).*

## Passo 5: Inserção das Medidas DAX
1. Com os relacionamentos criados, abra o arquivo `medidas.dax`.
2. Crie as medidas em suas respectivas tabelas indicadas nos comentários do código.
3. A medida `Participação do Estado %` e o filtro visual de Top 10 exigirão a função nativa de Filtro N Superior no painel lateral.