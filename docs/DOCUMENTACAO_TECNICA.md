# Documentação Técnica — Comex Data Product

> Pipeline serverless de extração, validação e reconciliação de dados públicos de comércio exterior brasileiro, com estudo de caso na movimentação portuária de Santos.

**Repositório:** github.com/Magalhaes-vitor/comex-data-product
**Autor:** Vitor De Toledo Magalhães
**Última atualização deste documento:** julho de 2026

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Arquitetura](#2-arquitetura)
3. [Fontes de dados e contratos](#3-fontes-de-dados-e-contratos)
4. [Pipeline por camada (Bronze / Silver / Gold)](#4-pipeline-por-camada-bronze--silver--gold)
5. [Estratégia de qualidade de dados](#5-estratégia-de-qualidade-de-dados)
6. [Camada de apresentação (views Athena)](#6-camada-de-apresentação-views-athena)
7. [Backfill histórico](#7-backfill-histórico)
8. [Observabilidade e resiliência](#8-observabilidade-e-resiliência)
9. [Infraestrutura e deploy](#9-infraestrutura-e-deploy)
10. [Runbook operacional](#10-runbook-operacional)
11. [Limitações conhecidas](#11-limitações-conhecidas)
12. [Glossário](#12-glossário)

---

## 1. Visão geral

O sistema extrai, valida e cruza quatro fontes públicas de dados de comércio exterior — Autoridade Portuária de Santos (APS), Banco Central (Bacen), MDIC (Comex Stat) e CONAB — produzindo um data lake em camadas (bronze/silver/gold) consumido por dashboards de Power BI via Amazon Athena.

**Execução de referência (maio/2026):** 117.451 registros MDIC, 20 cotações PTAX, 30 linhas de movimentação portuária, ~70s de execução, ~616MB de pico de memória.

**Backfill histórico consolidado:** 79 meses (out/2019–mai/2026), 8.860.902 registros de balança comercial.

---

## 2. Arquitetura

```
EventBridge (cron mensal, dia 15)
        │
        ▼
AWS Lambda (imagem de contêiner, 2048MB, timeout 900s, fora de VPC)
        │
        ├─→ Extractors (Bronze)  → S3 / disco local (fallback)
        ├─→ Cleaners (Silver)    → Pydantic + Quarentena → Parquet
        └─→ GoldBuilder (Gold)   → cruzamento entre fontes → Parquet
        │
        ▼
Amazon Athena (views SQL) → Power BI
        │
        └─→ CloudWatch → SNS/exit code → Slack (falhas)
```

### 2.1 Decisão: Lambda em vez de Fargate

Arquitetura original cogitava AWS Fargate em subnet pública sem NAT Gateway. A decisão final migrou para **AWS Lambda**, justificada por:

| Critério | Observação |
|---|---|
| Execução | ~70s, 616MB pico — confortável dentro do limite (900s / 2048MB) |
| Rede | Lambda fora de VPC tem saída à internet por padrão — **elimina o dilema do NAT Gateway**, não apenas o contorna |
| Custo | ~143 GB-s/mês (2048MB × 70s), muito abaixo do free tier (400.000 GB-s/mês) |
| Cadência | Execução mensal única — não há vantagem de contêiner de longa duração |

**IaC:** AWS SAM (`template.yaml`). Terraform foi descartado por complexidade desproporcional a um projeto solo.

---

## 3. Fontes de dados e contratos

| Fonte | Extrator | Cleaner | Cadência | Obrigatória? |
|---|---|---|---|---|
| APS | `aps_extractor.py` | `aps_cleaner.py` (antigo `cleaner.py`) | Mensal | Sim |
| Bacen (PTAX) | `bacen_extractor.py` | `bacen_cleaner.py` | Diária | Sim |
| MDIC (Comex Stat) | `mdic_extractor.py` | `mdic_cleaner.py` | Mensal | Sim |
| CONAB (safra) | `conab_extractor.py` | `conab_cleaner.py` | Sazonal, irregular | **Não** — ver 11.3 |

### 3.1 Contratos Pydantic (`src/models/contracts.py`)

- `MovimentacaoPortuaria` — mercadoria, volume (validador de formato numérico BR), mês (lista fechada de 3 letras)
- `CotacaoBacen` — data da cotação, compra, venda
- Contratos para MDIC e CONAB seguem o mesmo padrão declarativo — tipagem, validadores de campo, bloqueio de schema divergente

### 3.2 Extração da APS — nota técnica crítica

A extração de tabelas do PDF do Mensário Estatístico usa `pdfplumber` com `vertical_strategy: "lines"` (**não** `"text"`). A estratégia textual foi abandonada após causar corrupção sistemática de colunas em meses com totais numericamente mais largos (ex.: quebra da linha `TOTAL GERAL` em duas células). Ver Seção 11.1 para detalhes.

O portal da APS (`intranet.portodesantos.com.br`) apresenta cadeia de certificação TLS incompleta (falta o certificado intermediário Sectigo). O cliente HTTP precisa de bundle de CA customizado — **não usar `verify=False`**.

---

## 4. Pipeline por camada (Bronze / Silver / Gold)

- **Bronze**: dado bruto (PDF, JSON, XLSX), sem transformação, via `src/utils/storage.py` (abstração com fallback S3 ↔ disco local, detecta ambiente Lambda automaticamente).
- **Silver**: Pydantic + limpeza (formato numérico BR) + Parquet, uma tabela por fonte. Ver Seção 5 para a política de rejeição.
- **Gold**: `gold_builder.py` cruza APS + PTAX + MDIC + CONAB (quando disponível), produzindo:
  - `fato_movimentacao_cambio` — volume físico enriquecido por câmbio
  - `fato_balanca_mdic` — balança comercial nacional
  - `fato_origem_agricola` — alocação geográfica agrícola (modelo estático de participação por estado — ver Seção 11.2)

---

## 5. Estratégia de qualidade de dados

Implementada em `src/utils/quarantine.py`. Dois circuit breakers **independentes**, ambos precisam passar para liberar a Silver:

1. **Breaker de linha** — taxa de registros rejeitados / total processado excede limiar.
2. **Breaker de cobertura de volume** — volume validado (toneladas) vs. `TOTAL GERAL` declarado pelo próprio documento excede limiar de divergência. **Ausência do total oficial é tratada como falha estrutural (fail-closed)**, não como validação ignorada — essa foi uma correção crítica após um incidente real em que a extração capturou a tabela errada e o breaker, na versão anterior, liberou a Silver por não ter o denominador.

Registros rejeitados vão para `data/quarantine/{fonte}/` (zona irmã da Silver, não subpasta), com: `execution_id`, timestamp, arquivo de origem, página, índice da linha, conteúdo bruto (JSON), campo com erro, mensagem de validação.

---

## 6. Camada de apresentação (views Athena)

> Importante: estas views **não substituem** modelagem dimensional física (star schema). São agregações sobre tabelas fato desnormalizadas — reduzem volume de linhas retornado, não a redundância de armazenamento. Ver Seção 11.4.

```sql
-- Resumo mensal por fluxo (import/export)
CREATE OR REPLACE VIEW vw_mdic_resumo_mensal AS
SELECT ano, mes, fluxo,
       SUM(valor_fob_usd) AS valor_fob_usd,
       SUM(valor_fob_brl) AS valor_fob_brl,
       MAX(ptax_media_venda) AS ptax_media_venda
FROM fato_balanca_mdic
GROUP BY ano, mes, fluxo;

-- Ranking de países por mês/fluxo (DENSE_RANK)
CREATE OR REPLACE VIEW vw_mdic_top_paises AS
SELECT ano, mes, fluxo, pais, valor_fob_usd,
       DENSE_RANK() OVER (PARTITION BY ano, mes, fluxo ORDER BY valor_fob_usd DESC) AS rank_pais
FROM (
  SELECT ano, mes, fluxo, pais, SUM(valor_fob_usd) AS valor_fob_usd
  FROM fato_balanca_mdic
  GROUP BY ano, mes, fluxo, pais
);

-- Agrupamento por capítulo NCM (2 primeiros dígitos)
CREATE OR REPLACE VIEW vw_mdic_top_capitulo AS
SELECT ano, mes, fluxo,
       SUBSTR(CAST(ncm AS VARCHAR), 1, 2) AS capitulo_ncm,
       SUM(valor_fob_usd) AS valor_fob_usd,
       SUM(peso_kg) AS peso_kg
FROM fato_balanca_mdic
GROUP BY ano, mes, fluxo, SUBSTR(CAST(ncm AS VARCHAR), 1, 2);

-- Exportação agrícola por estado de origem estimado
CREATE OR REPLACE VIEW vw_origem_agricola_export AS
SELECT ano, mes, estado, mercadoria,
       SUM(volume_toneladas_estimado) AS volume_toneladas_estimado
FROM fato_origem_agricola
WHERE sentido = 'Exportação'
GROUP BY ano, mes, estado, mercadoria;
```

---

## 7. Backfill histórico

Branch `feat/historical-backfill`. Arquivos exclusivos: `discover_backfill_start.py`, `backfill_orchestrator.py`.

- **Descoberta de piso histórico**: `discover_backfill_start.py` sonda cada fonte isoladamente (2015–2020) e define o início do backfill como `max(FLOOR_MINIMO=2019-01, max(piso de cada fonte))` — não é uma data arbitrária.
- **Política de erro diferente da produção mensal**: o pipeline mensal é fail-fast (interrompe tudo na primeira falha). O backfill isola falhas por fonte/período e segue para o próximo lote, reportando falhas acumuladas ao final de cada lote via Slack — necessário para não abortar ~90 lotes por uma falha pontual.
- **Rate limiting reforçado**: 5s de resfriamento entre lotes mensais.
- **Sem checkpoint automático**: retomada após interrupção exige alterar `BACKFILL_START` manualmente. Ver Seção 11.5.

---

## 8. Observabilidade e resiliência

- **Retries**: Tenacity, com backoff exponencial, restrito a exceções transitórias (rede, 5xx) — não repete em erro estrutural.
- **Notificações**: `src/utils/notifier.py` envia a webhook real do Slack (`SLACK_WEBHOOK_URL` via variável de ambiente — ver Seção 11.6 sobre gestão de segredo).
- **Exit codes**: cada etapa propaga código de saída não-zero em falha, essencial para o CloudWatch detectar falha de execução do Lambda (uma exceção não tratada já falha a invocação automaticamente; falhas de negócio — como circuit breaker disparado — precisam de tratamento explícito).

---

## 9. Infraestrutura e deploy

- **IaC**: AWS SAM, `template.yaml` — `AWS::Serverless::Function`, `PackageType: Image`.
- **Trigger**: EventBridge, `cron(0 12 15 * ? *)` — dia 15 de cada mês (data em que o Mensário do mês anterior já costuma estar publicado).
- **Deploy**: `sam build && sam deploy`.
- **Custo mensal estimado**: ~143 GB-s de computação, dentro do free tier.

---

## 10. Runbook operacional

| Sintoma | Causa provável | Ação |
|---|---|---|
| Log: `SSLCertVerificationError` | Cadeia de certificado incompleta da APS | Confirmar bundle de CA customizado está presente na imagem; não usar `verify=False` |
| Log: `Volume oficial não localizado` | Extração pegou tabela errada (layout mudou) | Breaker de cobertura deve bloquear a Silver automaticamente (fail-closed); investigar `vertical_strategy` e o filtro de página |
| CONAB retorna vazio ou "conteúdo restrito" | Inconsistência de publicação da própria CONAB | Comportamento esperado — pipeline segue sem CONAB (fonte opcional); não é falha a corrigir no código |
| Notificação de circuit breaker (linha ou volume) disparada | Ver mensagem específica no Slack | Consultar `data/quarantine/{fonte}/` para o `execution_id` correspondente; auditar `conteudo_bruto_linha` |
| Backfill interrompido no meio | Falha de rede, cota de API, intervenção manual | Identificar último período processado no data lake; ajustar `BACKFILL_START` manualmente e reexecutar |
| CloudWatch não reporta falha apesar de erro de negócio | Exit code não propagado | Confirmar que o bloco principal do módulo captura o retorno `False` do cleaner/breaker e chama `sys.exit(1)` |

---

## 11. Limitações conhecidas

1. **Fragilidade de extração posicional (mitigada)** — a migração para `vertical_strategy: "lines"` resolveu a corrupção de colunas por largura numérica variável, mas índices de coluna ainda são parcialmente fixos no código; mapeamento dinâmico a partir do cabeçalho da tabela é recomendado como próximo passo de robustez.
2. **Modelo estático de alocação agrícola** — share de produção por estado, sem considerar restrições logísticas reais de escoamento.
3. **CONAB como fonte opcional** — conteúdo sinalizado como "restrito" para 2022 e 2024, e publicação duplicada para 2023. Fora do controle do pipeline.
4. **Views Athena ≠ modelagem dimensional física** — tabelas fato permanecem desnormalizadas; ver Seção 6.
5. **Backfill sem checkpoint** — retomada de execução interrompida é manual.
6. **Webhook do Slack sem gestor de segredo dedicado** — hoje via variável de ambiente; AWS Secrets Manager é o próximo passo recomendado.
7. **H2 (reconciliação físico-financeira) não plenamente testável** — MDIC é nacional, APS é só Santos; comparação atual é direcional, não uma reconciliação porto-a-porto.
8. **ANTAQ não integrada** — painel estatístico (Qlik Sense) instável no período de desenvolvimento; permanece como trabalho futuro.

---

## 12. Glossário

- **Bronze/Silver/Gold**: camadas de maturidade de dado em arquitetura medallion — bruto, validado, cruzado/modelado.
- **Circuit breaker**: mecanismo que interrompe processamento ao detectar taxa de erro acima de um limiar.
- **Data contract**: schema formal (aqui, Pydantic) que valida estrutura de dado antes de propagação.
- **Fail-closed**: postura de segurança que bloqueia por padrão diante de incerteza (oposto de fail-open).
- **PTAX**: taxa de câmbio de referência do Banco Central do Brasil.
- **Quarentena**: zona de armazenamento para registros que falharam validação, preservados para auditoria.
