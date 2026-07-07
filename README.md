# Comex Data Product: RPA e Serverless AWS Aplicados à Balança Comercial

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![Status](https://img.shields.io/badge/Status-Em_desenvolvimento-yellow.svg)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

> Este repositório está em construção pública. Este README é atualizado a cada etapa concluída — não a cada etapa planejada. O que está documentado como "implementado" reflete o código real em execução.

---

## Navegação
- [Histórico de Branches e Evolução](#historico-de-branches-e-evolucao)
- [Status do projeto](#status-do-projeto)
- [Arquitetura Atual (Local)](#arquitetura-atual-local)
- [Arquitetura Alvo (Cloud)](#arquitetura-alvo-cloud)
- [Decisões de Design Aplicadas](#decisoes-de-design-aplicadas)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Acompanhando o progresso](#acompanhando-o-progresso)

---

## Histórico de Branches e Evolução

A construção deste pipeline seguiu uma ordem rigorosa de refatoração para garantir a integridade dos dados antes da migração para a nuvem. A evolução pode ser rastreada através do seguinte fluxo de branches:

1. **`main` (Base inicial):** Setup de web scrapers, parsers de PDF (pdfplumber) e consumo de API (Bacen), com validação básica via Pydantic.
2. **`fix/cleaners-and-fixtures`:** Implementação de testes automatizados (pytest) utilizando PDFs reais de meses anteriores como *fixtures*. Refatoração da extração de PDFs para o método geométrico (`vertical_strategy: "lines"`), substituindo índices fixos frágeis por mapeamento dinâmico de colunas.
3. **`fix/core-daterules`:** Resolução do P0 (Risco de dessincronização temporal). Criação do módulo centralizado `DateRules` para garantir que extratores, limpadores e builders processem estritamente o mesmo período, garantindo a idempotência do pipeline.
4. **`feat/quarantine-circuit-breakers`:** Implementação da Zona de Quarentena (DLQ) para segregar dados corrompidos sem interromper o pipeline. Criação de duplo *Circuit Breaker*: Breaker de Linhas (protege contra falhas generalizadas) e Breaker de Volume/Cobertura (valida a tonelagem extraída contra o total oficial impresso no documento).

---

## Status do projeto

### Fase 1: Ingestão e Modelagem (Concluída)
- [x] Extração automatizada de PDFs do Porto de Santos (BeautifulSoup/Requests)
- [x] Extração de dados da API Olinda (Banco Central)
- [x] Modelagem de contratos de dados com Pydantic
- [x] Parsing complexo de tabelas em PDF (pdfplumber)

### Fase 2: Robustez, Governança e Testes (Concluída)
- [x] Implementar retry pattern (Tenacity) nos web scrapers e chamadas de API
- [x] Criar testes unitários para validadores Pydantic
- [x] Testes de integração com PDFs de meses anteriores como fixture local
- [x] Centralizar regra de negócio temporal (`DateRules`)
- [x] Implementar Zona de Quarentena com *Circuit Breakers* duplos (Linha e Volume)

### Fase 3: Prontidão para Produção (Próximos Passos)
- [ ] Conectar módulo de notificações a um Webhook real do Slack
- [ ] Garantir códigos de saída (sys.exit) corretos para monitoramento de contêineres

### Fase 4: Cloud Migration e Observabilidade (Roadmap)
- [ ] Refatorar caminhos locais de disco para AWS S3 (boto3)
- [ ] Containerizar os pipelines com Docker
- [ ] Deploy serverless no AWS ECS / Fargate

---

## Arquitetura Atual (Local)

Atualmente, o projeto opera simulando um ambiente de Data Lake estruturado no sistema de arquivos local, dividido em quatro zonas semânticas rigorosas:

* **Camada Bronze (Raw):** Armazena os dados brutos de forma imutável, exatamente como foram extraídos das fontes originais (PDFs da APS e JSONs do Bacen).
* **Zona de Quarentena (DLQ - Dead Letter Queue):** Atua como o *Circuit Breaker* de qualidade do Data Lake. Dados que falham nos contratos de schema (Pydantic) são desviados para esta zona paralela e salvos em formato Parquet para auditoria forense, preservando o fluxo dos dados íntegros.
* **Camada Silver (Cleansed):** Dados limpos, tipados e validados estruturalmente. Salvos no formato colunar Parquet para alta performance de leitura. A gravação nesta camada só ocorre se os *Circuit Breakers* aprovarem o lote.
* **Camada Gold (Business):** Contém a regra de negócio consolidada. Modelada como uma Tabela Fato Única (OBT - *One Big Table*) que cruza as movimentações portuárias físicas (Toneladas) com os indicadores macroeconômicos (PTAX Média Mensal).

## Arquitetura Alvo (Cloud)

A infraestrutura final migrará a base lógica atual para serviços gerenciados da AWS:
* **Storage:** AWS S3 (substituindo o sistema de arquivos local).
* **Computação:** Contêineres Docker executados no AWS Fargate (Task acionada por EventBridge).
* **Alertas:** Notificações disparadas para o Slack via Webhook.

---

## Decisões de Design Aplicadas

- **Idempotência Temporal Centralizada:** A regra que define "qual mês processar" foi isolada no módulo `DateRules`. Isso elimina falhas silenciosas onde diferentes etapas do pipeline poderiam inferir datas distintas.
- **Circuit Breakers Duplos:** O pipeline implementa a filosofia de falhar fechado (*fail-closed*). A camada Silver só é alimentada se o lote passar por duas catracas simultâneas:
  1. *Breaker de Linhas:* Aborta se o percentual de falhas no contrato Pydantic for estrutural.
  2. *Breaker de Cobertura:* Aborta se o somatório extraído divergir do total oficial declarado pela fonte, garantindo integridade semântica além da sintática.
- **Mapeamento Dinâmico de Matrizes em PDF:** Para lidar com mudanças na geometria dos relatórios governamentais, o parser evita índices colunares fixos (`hardcoded`). O código lê o cabeçalho das tabelas para inferir dinamicamente os eixos corretos antes da extração de dados físicos, garantindo resiliência contra inserção de novas colunas.
- **Fail-Fast vs. Quarentena:** Erros de linha individuais não derrubam o mês inteiro (desde que respeitem os limites dos *breakers*), mas também não são descartados silenciosamente. Eles são registrados de forma imutável na Zona de Quarentena para posterior revisão da engenharia.

---

## Estrutura do Projeto

```bash
comex-data-product/
├── data/
│   ├── bronze/          # Dados brutos imutáveis (PDF, JSON)
│   ├── quarantine/      # DLQ para auditoria de falhas de contrato (Parquet)
│   ├── silver/          # Dados validados e estruturados (Parquet)
│   └── gold/            # Modelo de negócio cruzado (Parquet)
├── src/
│   ├── extractors/
│   │   ├── aps_extractor.py
│   │   └── bacen_extractor.py
│   ├── transformers/
│   │   ├── cleaner.py
│   │   ├── bacen_cleaner.py
│   │   └── gold_builder.py
│   ├── models/
│   │   └── contracts.py
│   └── utils/
│       ├── date_rules.py    # Fonte única de regras temporais
│       ├── quarantine.py    # Gerenciamento de DLQ e Circuit Breakers
│       └── notifier.py
├── tests/
│   ├── fixtures/
│   ├── test_cleaners.py
│   ├── test_contracts.py
│   ├── test_date_rules.py
│   └── test_extractors.py
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
