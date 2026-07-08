# Comex Data Product: RPA e Serverless AWS Aplicados à Balança Comercial

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![Status](https://img.shields.io/badge/Status-Em_desenvolvimento-yellow.svg)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

> Este repositório está em construção pública. Este README é atualizado a cada etapa concluída — não a cada etapa planejada. O que está documentado como "implementado" reflete o código real em execução.

---

## Navegação
- [Histórico de Branches e Evolução](#historico-de-branches-e-evolucao)
- [Status do projeto](#status-do-projeto)
- [Visão geral](#visao-geral)
- [Arquitetura Atual (Local)](#arquitetura-atual-local)
- [Arquitetura Alvo (Cloud)](#arquitetura-alvo-aws-cloud-native)
- [Fontes de dados](#fontes-de-dados)
- [Modelagem da camada Gold](#modelagem-da-camada-gold)
- [Hipóteses de análise (a validar)](#hipoteses-de-analise-a-validar)
- [Decisões de design aplicadas](#decisoes-de-design-aplicadas)
- [Governança e uso responsável de dados públicos](#governanca-e-uso-responsavel-de-dados-publicos)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Acompanhando o progresso](#acompanhando-o-progresso)
- [Autor](#autor)

---

## Histórico de Branches e Evolução

A construção deste pipeline seguiu uma ordem rigorosa de refatoração para garantir a integridade dos dados antes da migração para a nuvem. A evolução pode ser rastreada através do seguinte fluxo de branches:

1. **`main` (Base inicial):** Setup de web scrapers, parsers de PDF (pdfplumber) e consumo de API (Bacen), com validação básica via Pydantic.
2. **`fix/cleaners-and-fixtures`:** Implementação de testes automatizados (pytest) utilizando PDFs reais de meses anteriores como *fixtures*. Refatoração da extração de PDFs para o método geométrico (`vertical_strategy: "lines"`), substituindo índices fixos frágeis por mapeamento dinâmico de colunas.
3. **`fix/core-daterules`:** Resolução do P0 (Risco de dessincronização temporal). Criação do módulo centralizado `DateRules` para garantir que extratores, limpadores e builders processem estritamente o mesmo período, garantindo a idempotência do pipeline.
4. **`feat/quarantine-circuit-breakers`:** Implementação da Zona de Quarentena (DLQ) para segregar dados corrompidos sem interromper o pipeline. Criação de duplo *Circuit Breaker*: Breaker de Linhas (protege contra falhas generalizadas) e Breaker de Volume/Cobertura (valida a tonelagem extraída contra o total oficial impresso no documento).

---

## Status do projeto

Datas são por número de semana do projeto, não calendário — evita prometer data e atrasar publicamente.

### Fase 1 — Fundação (Concluída)
- [x] Definição do escopo e das fontes de dados
- [x] Diagrama de arquitetura-alvo
- [x] Repositório público com esqueleto de pastas
- [x] Primeiro download funcional do PDF da APS

### Fase 2 — Parsing, Ingestão e Qualidade (Concluída)
- [x] Extração de dados da API Olinda (Banco Central)
- [x] Extração da tabela de "Movimentação de Cargas" via pdfplumber
- [x] Data contract em Pydantic para o schema esperado
- [x] Testes unitários e de integração com PDFs de meses anteriores como fixture
- [x] Centralizar regra de negócio temporal (`DateRules`)
- [x] Implementar Zona de Quarentena com *Circuit Breakers* duplos (Linha e Volume)

### Fase 3 — Resiliência e Observabilidade (Em andamento)
- [x] Implementar retry pattern (Tenacity) nos web scrapers e chamadas de API
- [x] Conectar módulo de notificações a um Webhook real do Slack
- [x] Garantir códigos de saída (`sys.exit`) corretos para monitoramento de contêineres

### Fase 4 — Data Lake e Camadas (Roadmap)
- [ ] Refatorar caminhos locais de disco para AWS S3 (boto3)
- [ ] Silver: consolidação do armazenamento limpo no S3
- [ ] Gold: cruzamento das fontes e modelo dimensional
- [ ] Consulta via Amazon Athena

### Fase 5 — Infraestrutura como código (Roadmap)
- [ ] Containerizar os pipelines com Docker
- [ ] Deploy serverless no AWS ECS / Fargate (com gatilho EventBridge)
- [ ] Provisionamento via AWS SAM ou Terraform dos recursos validados

### Fase 6 — Profundidade Analítica (Roadmap)
- [ ] Reconciliação com Comex Stat (MDIC)
- [ ] Contextualização sazonal com calendário CONAB
- [ ] Comparativo de market share via ANTAQ

### Fase 7 — Entrega (Roadmap)
- [ ] Dashboard Power BI
- [ ] Case study final e retrospectiva

---

## Visão geral

Este projeto pretende resolver um problema real de Comércio Exterior (Comex): **reconciliar** o que o Porto de Santos registra fisicamente (toneladas movimentadas) com o que a alfândega (MDIC) registra financeiramente (valor FOB em USD), contextualizando esses números pela sazonalidade da safra (CONAB) e pela variação cambial (Bacen).

Para isso, será construído um pipeline de engenharia de dados e RPA que extrai dados não-estruturados de PDFs públicos, enriquece com APIs governamentais e consolida os resultados em um data lake estruturado (arquitetura medallion) na AWS, orquestrado de forma serverless.

Todos os dados usados são reais e públicos — nenhum dado é simulado ou inventado.

---

## Arquitetura alvo (AWS Cloud-Native)

*Esta é a arquitetura planejada. O status de implementação de cada componente está na seção [Status do projeto](#-status-do-projeto).*

```mermaid
graph TD
    classDef source fill:#E8EAF6,stroke:#3949AB,stroke-width:2px;
    classDef aws fill:#FF9900,stroke:#232F3E,stroke-width:2px,color:#FFFFFF;
    classDef storage fill:#FFF3E0,stroke:#F57C00,stroke-width:2px;
    classDef compute fill:#E0F7FA,stroke:#00838F,stroke-width:2px;
    classDef monitor fill:#FFEBEE,stroke:#C62828,stroke-width:2px;
    classDef bi fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px;

    subgraph Extras["Fontes de dados externas (cadências diferentes)"]
        APS[APS - Site oficial<br>PDF mensal]:::source
        BACEN[Bacen - API<br>PTAX diária]:::source
        MDIC[MDIC - Comex Stat<br>Valor declarado, mensal]:::source
        CONAB[CONAB - Calendário<br>Safra agrícola]:::source
        ANTAQ[ANTAQ - Painel estatístico<br>Arquivos batch anuais]:::source
    end

    EB(("EventBridge<br>Trigger mensal")):::aws --> Fargate

    subgraph Compute["AWS Fargate (container Python)"]
        direction TB
        Orchestrator[Orchestrator<br>Tenacity + rate limiter]:::compute
        Extract[Extract module<br>requests + pdfplumber]:::compute
        Validation[Validação Pydantic<br>Data contracts]:::compute
        Transform[Transform module<br>Limpeza e star schema]:::compute
    end

    subgraph Lake["Amazon S3 - Data Lake (Medallion)"]
        Bronze[Bronze<br>Raw PDFs e JSONs]:::storage
        Silver[Silver<br>Tabelas limpas por fonte]:::storage
        Gold[Gold<br>Star schema / Parquet]:::storage
    end

    Athena[Amazon Athena<br>SQL serverless]:::aws --> BI[Power BI<br>Dashboard de reconciliação]:::bi

    CW[CloudWatch<br>Logs e métricas]:::aws --> SNS[Amazon SNS]:::aws --> Slack[Slack Webhook<br>Alertas de falha/drift]:::monitor

    APS --> Extract
    BACEN --> Extract
    MDIC --> Extract
    CONAB --> Extract
    ANTAQ --> Extract

    Extract --> Orchestrator
    Orchestrator --> Validation

    Validation -- "Schema OK" --> Transform
    Validation -- "Data drift" --> SNS

    Transform -->|Salva raw| Bronze
    Transform -->|Salva limpo| Silver
    Transform -->|Salva modelado| Gold

    Gold --> Athena
    Transform -.->|Envia logs| CW
```

**Stack planejada:**
- Orquestração: Amazon EventBridge (gatilho mensal)
- Processamento: AWS Fargate
- Data lake (S3 medallion): bronze, silver, gold
- Consulta: Amazon Athena
- Observabilidade: Amazon SNS + Slack
- IaC: AWS SAM

---

## Fontes de dados

| Fonte | O que fornece | Cadência | Formato de acesso |
|---|---|---|---|
| Autoridade Portuária de Santos (APS) | Volume físico (toneladas) por mercadoria | Mensal | PDF (Mensário Estatístico) |
| Banco Central (Bacen) | PTAX (câmbio) | Diária | API pública (SGS/OData) — **não requer chave de autenticação** |
| Comex Stat (MDIC) | Valor FOB (USD) declarado na alfândega | Mensal | Consulta/download estruturado |
| CONAB | Calendário de safra agrícola | Sazonal | Boletins/planilhas |
| ANTAQ | Estatísticas de movimentação de todos os portos | Anual | Painel estatístico (Qlik Sense) com download de arquivos compactados — **não é uma API REST simples**, exige um mini-ETL de arquivo batch |

---

## Modelagem planejada da camada Gold (Star Schema)

*Ainda não implementada. Desenho alvo:*

- **Tabela fato:** `fact_exports` — volume (toneladas), valor FOB (USD), taxa PTAX aplicada na data do embarque.
- **Dimensões:**
  - `dim_date` — dia útil, mês de safra (CONAB), trimestre.
  - `dim_commodity` — produtos e categorias.
  - `dim_port` — porto de origem e região.
  - `dim_currency` — metadados da taxa de câmbio.

Objetivo: responder perguntas como *"qual o volume médio de soja exportada por Santos nos meses de pico de safra, ajustado pela variação do dólar?"* com queries SQL simples no Athena.

---

## Hipóteses de análise (a validar)

Estas são hipóteses que o pipeline vai testar quando houver dados suficientes — não conclusões já demonstradas.

- **H1 — Sazonalidade domina o câmbio no curto prazo:** a expectativa, baseada na literatura de comércio exterior, é que o volume de exportação portuária seja tracionado principalmente pelo calendário de colheita, com o efeito cambial defasado e de menor magnitude. Isso será testado cruzando volume mensal com o calendário CONAB e a série de PTAX, e só será tratado como conclusão depois de série histórica suficiente (referência: 24+ meses).
- **H2 — Divergência físico x financeiro:** espera-se que o volume físico (APS) e o valor declarado (Comex Stat) divirjam de forma explicável por preço de commodity e mix de produto, não por erro de dado. O painel de reconciliação vai quantificar essa diferença, não vai tratá-la como uma inconsistência a "corrigir".

---

## Decisões de design já tomadas

> **PDF vs. portal tabular da APS**
> A APS também disponibiliza dados tabulares além do PDF. A opção pelo PDF (pdfplumber/camelot) é deliberada: demonstra parsing resiliente a mudança de layout, competência mais próxima de cenários reais de Market Intelligence, onde fontes valiosas raramente têm API amigável.

> **Resiliência via Pydantic**
> A extração de PDF está sujeita a mudança de layout sem aviso. Pydantic funciona como contrato de dados: se a estrutura extraída não bater com o schema esperado, o dado é bloqueado antes da camada Silver e um alerta é disparado — em vez de deixar dado ruim propagar silenciosamente.

---

## Governança e uso responsável de dados públicos

Todas as fontes usadas são públicas e institucionais (APS, Bacen, MDIC, CONAB, ANTAQ). A extração segue princípios de coleta responsável:
- Respeito ao `robots.txt` e aos termos de uso de cada site.
- Rate limiting explícito entre requisições (sem paralelismo agressivo).
- Retries com backoff (Tenacity), não repetição imediata em caso de erro.
- Nenhuma técnica de evasão de proteção anti-bot — a coleta é transparente e auditável, adequada ao caráter público das fontes.

---

## Estrutura planejada do projeto

```bash
comex-data-product/
├── src/
│   ├── extractors/
│   │   ├── aps_extractor.py
│   │   ├── bacen_extractor.py
│   │   └── mdic_extractor.py
│   ├── transformers/
│   │   ├── cleaner.py
│   │   └── gold_builder.py
│   ├── models/
│   │   ├── contracts.py
│   │   └── validators.py
│   ├── orchestrator.py
│   └── utils/
├── tests/
│   ├── fixtures/
│   ├── test_extractors.py
│   └── test_contracts.py
├── template.yaml
├── .env.example
└── requirements.txt
```

---

## Acompanhando o progresso

Este projeto está sendo construído em público, com atualizações regulares no LinkedIn a cada fase concluída (não a cada intenção). Comentários e sugestões são bem-vindos — toda sugestão é avaliada contra o roadmap antes de entrar no escopo.

- LinkedIn: [linkedin.com/in/magalhaes-vitor](https://www.linkedin.com/in/magalhaes-vitor/)

---

## Autor

**Vitor De Toledo Magalhães**
Desenvolvedor Python | Especialista em Automação (RPA) | Engenharia de Dados Cloud

- LinkedIn: [linkedin.com/in/magalhaes-vitor](https://www.linkedin.com/in/magalhaes-vitor/)
- GitHub: [github.com/Magalhaes-vitor](https://github.com/Magalhaes-vitor)
- E-mail: vitor.de.toledo.magalhaes@gmail.com
