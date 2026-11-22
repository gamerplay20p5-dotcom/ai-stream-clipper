# Homologação local dos PRs #23, #24 e #25 (sem GitHub Actions)

Decisão do dono: a integração não depende do GitHub Actions. A evidência técnica é o **relatório de homologação local**, gerado por `scripts/homologacao.sh` (Linux, macOS ou Git Bash) ou por `scripts/homologacao.ps1` (Windows), com PostgreSQL 16.4 real num container descartável.

## Segurança do ambiente

| Item | Como é garantido |
| --- | --- |
| Banco | Container `asc-test-postgres` do projeto Compose `asc-homolog` (`infrastructure/compose/test-postgres.yml`), imagem `postgres:16.4-bookworm` (a mesma da produção) |
| Rede | Só `127.0.0.1:55432` |
| Credenciais | Fictícias (`asc_test` / `asc_test`), restritas a esse container |
| Dados | Em memória (`tmpfs`). Cada execução cria bancos `asc_test_*` e os apaga no fim |
| Produção | `tests/pg_support.py` recusa qualquer host que não seja `localhost`, `127.0.0.1` ou `postgres`, e recusa sempre `192.168.1.212`, **antes de conectar** (testado) |
| Limpeza | `docker compose -p asc-homolog down -v`: remove só esse projeto. Nenhum comando global do Docker, nenhum recurso de outro projeto |
| Integrações | YouTube, Instagram, TikTok, Facebook, Stripe e InfinitePay são simulados nos testes. Nenhuma chave real, conta, pagamento ou publicação |

## Como executar

**Pré-requisitos:**
- Docker com Compose v2 (`docker compose version`);
- Python 3.12 ou mais novo;
- Node 20 (dashboard) e Node 24 (serviço comercial; o build do dashboard também funciona com o 24);
- git, e bash no Windows (Git for Windows);
- na primeira vez, os navegadores do Playwright: `cd commercial && npx playwright install --with-deps chromium firefox webkit`.

**Linux, macOS ou Git Bash:**

```bash
./scripts/homologacao.sh                  # tudo: PostgreSQL descartável + todas as suítes + relatório
./scripts/homologacao.sh --no-browser     # sem Playwright (servidor sem dependências gráficas)
./scripts/homologacao.sh --keep           # mantém o container para investigar
./scripts/homologacao.sh --down           # só derruba o container da homologação
HOMOLOG_PYTHON=/caminho/python ./scripts/homologacao.sh   # reaproveita um Python com as dependências
```

**Windows PowerShell:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1
powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -NoBrowser
powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -Down
powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -Python C:\caminho\python.exe
```

**Saída:**
- `reports/homologacao/<data UTC>/RELATORIO.md`, com veredito, commit, sistema, versões, etapas, contagens e problemas;
- um `.log` por etapa e o JUnit XML de cada suíte Python.
- **Código de saída:** `0` = APROVADO, `1` = REPROVADO, `2` = PENDENTE.

### Etapas

| Etapa | Comando | O que valida |
| --- | --- | --- |
| `secrets` | `bash .github/scripts/check-secrets.sh` | Nenhuma chave ou arquivo sensível versionado |
| `python-deps` | `pip install -e "backend[dev,audio,vision]"` (venv isolado `.homolog/venv`) | Dependências do backend |
| `postgres-up` | `docker compose -p asc-homolog ... up -d --wait` | PostgreSQL 16.4 descartável |
| `backend-sqlite` | `pytest -m "not models and not postgres"` | Suíte completa (inclui e2e) em SQLite |
| `backend-postgres-suite` | o mesmo com `ASC_TEST_SUITE_DB=postgres` | **A suíte inteira sobre PostgreSQL real**: assinaturas, cotas, outbox, lives compartilhadas, direitos por período pago, publicação por cliente, isolamento, painel da HTS e as corridas com threads |
| `postgres-concurrency-migrations` | `ASC_REQUIRE_PG=1 pytest -m postgres` | Concorrência com conexões separadas e migrações em PostgreSQL. **Sem servidor, falha em vez de ser ignorada** |
| `commercial-*` | `npm ci`, `build`, `test`, `audit` | Landing e serviço comercial (Stripe e InfinitePay simulados) |
| `commercial-playwright` | `npx playwright test` | Chromium, Firefox e WebKit, com axe |
| `dashboard-*` | `npm ci`, `typecheck`, `check:i18n`, `build` | Painel |

**Regra do veredito** (`scripts/homologacao_report.py`):
- **APROVADO** só quando todas as etapas passam, as etapas de PostgreSQL rodaram de fato (mais de 0 testes) e **nenhum** teste de PostgreSQL foi ignorado;
- **PENDENTE** sem Docker;
- **REPROVADO** em qualquer falha.

### O que é testado em PostgreSQL real

**Lives compartilhadas (#24):**
- 10 clientes no mesmo canal;
- 20 relatórios de "ao vivo" simultâneos, em conexões separadas: 1 sessão e 1 captura;
- 3 workers ao mesmo tempo, mais uma reconexão: 1 análise por segmento e 30 cobranças exatas;
- franquia individual;
- suspensão e cancelamento simultâneos ao segmento seguinte;
- 8 entregas simultâneas: 1 por cliente, num arquivo protegido;
- captura morta substituída uma única vez;
- limite de lives simultâneas entre conexões;
- ausência de deadlock (as contas são travadas em ordem de id);
- os direitos por período pago (suspensão, revisão, contestação, estorno, chargeback) rodam na suíte completa sobre PostgreSQL.

**Publicação (#25):**
- 10 workers disputando as vagas do dia: exatamente o limite;
- novas tentativas sem unidade extra;
- worker × limpeza de reservas em conexões reais.
- Os outros cenários rodam na suíte completa sobre PostgreSQL:
  - Creator 2 destinos e 3 por dia, Pro 4 e 4;
  - nada republicado;
  - remoção de canal × varredura;
  - retenção de 7 dias;
  - reserva abandonada em 6 h;
  - conta desativada;
  - assinatura suspensa ou expirada;
  - isolamento entre clientes e em relação à HTS.

**Migrações:**
- instalação do zero até a 0019, com tabelas, colunas e índices conferidos;
- cadeia 0014 → 0015 → 0016 → 0017 → 0018 → 0019, cada `downgrade` de volta até a 0014 e subida de novo;
- banco representativo da 0014 (a versão da produção) atualizado até a 0019 com os dados preservados;
- 0018 recusando sessões `live` duplicadas **antes de qualquer alteração** (versão, esquema e dados intactos) e concluindo depois que a duplicata é encerrada.

## Resultado nesta máquina (2026-11-21)

**Veredito: PENDENTE.** Os PRs **não** podem ser considerados homologados em PostgreSQL ainda.

| Item | Valor |
| --- | --- |
| Commit testado | `53d09a6` (PR #25, que contém #23 e #24), mais a infraestrutura de homologação desta etapa (ainda não commitada na hora da execução) |
| Sistema | Windows 11 Pro (10.0.26200) |
| Docker / PostgreSQL | **Não disponíveis**: sem Docker, sem WSL e sem PostgreSQL instalados. A instalação de um PostgreSQL embutido foi negada pela política de permissões da sessão e não foi contornada |
| Python / Node | 3.14.2 / v24.13.0 |
| Comando | `powershell -ExecutionPolicy Bypass -File scripts\homologacao.ps1 -Python .venv\Scripts\python.exe` |

| Etapa | Resultado |
| --- | --- |
| secrets | Passou |
| backend-sqlite | **518 aprovados**, 0 falhas, 1 ignorado: `test_a_process_ignoring_sigterm_is_killed`, que usa sinais POSIX e só roda em Linux, onde está a produção |
| backend-postgres-suite | **NÃO EXECUTADA** (sem Docker) |
| postgres-concurrency-migrations | **NÃO EXECUTADA** (sem Docker) |
| commercial: install, build, tests, audit | Passou; 37/37 testes e 0 vulnerabilidades altas |
| commercial-playwright | 34 aprovados, 0 falhas, 8 ignorados por desenho (varreduras de acessibilidade, capturas e métricas que rodam só no Chromium) |
| dashboard: install, typecheck, i18n, build | Passou |

**Separação pedida:**
- **Executado de verdade:** as etapas acima marcadas "Passou", com as integrações externas simuladas.
- **Só preparado:**
  - a suíte inteira sobre PostgreSQL;
  - `test_postgres_shared_lives.py` (8 cenários);
  - `test_postgres_customer_publishing.py` (2);
  - `test_postgres_migrations.py` (4).
  - O código dos 4 cenários de migração foi autoconferido num SQLite descartável, só para achar erros no próprio teste. **Isso não conta como homologação.**
- **Simulado:** YouTube, Instagram, TikTok e Facebook (APIs falsas e `httpx.MockTransport`), Stripe e InfinitePay (mocks e webhooks assinados localmente).
- **Não homologado:** PostgreSQL real, publicação real em qualquer rede, OAuth real, pagamentos reais, deploy e migração em produção.

**Problemas encontrados e corrigidos nesta etapa:**
- **Script no Windows:**
  - o `bash` do Git for Windows fica fora do PATH, por isso a busca de segredos aparecia como "não executada". Agora o script procura nos caminhos padrão;
  - o PowerShell 5.1 lia o script sem BOM como ANSI e corrompia os acentos. O arquivo foi salvo em UTF-8 com BOM.
- **`npm ci` do painel com `EPERM`:**
  - arquivos do `esbuild` e do `rollup` estavam presos por processos `vite preview` órfãos que eu mesmo tinha aberto nas capturas de tela do PR #25;
  - só esses processos foram encerrados (os de outras ferramentas da máquina não foram tocados), e a terceira execução passou.
  - O relatório marcou REPROVADO corretamente enquanto isso falhava.

**Para concluir a homologação**, numa máquina com Docker (por exemplo, uma estação Linux ou Windows com Docker Desktop, **não** os servidores da produção):

```bash
git fetch origin && git checkout feat/customer-publishing && git pull
cd commercial && npx playwright install --with-deps chromium firefox webkit && cd ..
./scripts/homologacao.sh          # ou scripts\homologacao.ps1 no Windows
```

O veredito precisa sair **APROVADO**, com `backend-postgres-suite` e `postgres-concurrency-migrations` passando e 0 testes de PostgreSQL ignorados. Qualquer falha em PostgreSQL deve ser investigada e corrigida no código, nunca contornada.

## Homologação em servidor, executada pelo administrador (sem alterar o `asc-ctl`)

Para uma máquina Linux com Docker onde a conta `asc` **não** tem acesso ao Docker (por exemplo, a 206). O `asc-ctl` de produção **não é substituído**. O administrador roda uma única vez `infrastructure/homolog/homologacao-pg.sh` com `sudo`, e nenhuma permissão nova é dada à conta `asc`.

**O que o script faz:**
- **Verificação prévia, só leitura:**
  - mínimos de 10 GB livres para o Docker, 12 GB de RAM disponível, 6 CPUs e carga abaixo de 70%;
  - recusa a .212;
  - `--check` faz só isso, sem criar nada.
- **Isolamento:**
  - tudo leva um id de execução (`asc-homolog-<hora UTC>`) e um rótulo próprio;
  - PostgreSQL 16.4 numa **rede Docker interna, sem porta publicada**, com dados em memória, senha fictícia e limite de 2 CPUs e 2 GB;
  - os testes rodam num container Python 3.12 descartável, com 4 CPUs, 6 GB, prioridade baixa e o código montado **somente leitura**;
  - só `backend[dev,vision]` é instalado: **sem Whisper, ONNX ou modelos de IA**.
- **Testes:**
  - a suíte inteira do backend sobre PostgreSQL (exceto o e2e com Whisper, homologado na máquina do desenvolvedor);
  - os testes de concorrência e de migração com `ASC_REQUIRE_PG=1`.
- **Limpeza:** remove só os containers e a rede desta execução, e as duas imagens base apenas se esta execução as baixou. Nenhum serviço, container, volume ou imagem existente é tocado. Nada de `prune`.
- **Relatório:** `reports/homologacao/<id>/RELATORIO.md`, mais os logs e o JUnit. Código de saída `0` = APROVADO, `1` = REPROVADO, `2` = PENDENTE.

**Espaço:** cerca de 0,6 GB de imagens, cerca de 2 GB de ambiente de teste (na camada do container, removida no fim) e menos de 20 MB de relatório.

## Cadeia de integração (verificada em 2026-11-21, só com objetos temporários do git)

| Verificação | Resultado |
| --- | --- |
| `main` (`aff7073`) é ancestral do #23 (`d70345b`) | Sim |
| #23 é ancestral do #24 (`ced5ccc`) | Sim |
| #24 é ancestral do #25 (`53d09a6`) | Sim |
| `main` + #23, + #24, + #25 com merges sucessivos | Sem conflitos. A árvore final é **idêntica** à do #25 |
| Diff próprio de cada PR | #23: 71 arquivos. #24: 28 arquivos. #25: 40 arquivos (mais esta infraestrutura de homologação) |
| Estado no GitHub | Os três `CLEAN` |

**Ordem:**
1. #23 → `main`;
2. trocar a base do #24 para `main` e mesclar;
3. trocar a base do #25 para `main` e mesclar.

Sempre com **merge commit**, nunca squash. **Nenhum merge sem a autorização do dono**, e só depois do veredito APROVADO em PostgreSQL real.

## English

**Owner decision:** integration does not depend on GitHub Actions. The technical evidence is the local homologation report produced by `scripts/homologacao.sh` or `scripts/homologacao.ps1`.

**How it runs:**
- **Database:** a throwaway PostgreSQL 16.4 container (project `asc-homolog`, 127.0.0.1:55432, fake credentials, in-memory data, per-run `asc_test_*` databases dropped at the end).
- **Safety:** `tests/pg_support.py` refuses any non-local host and always 192.168.1.212 before connecting.
- **Cleanup:** removes only that Compose project.
- **Integrations:** every external one is simulated.

**Steps:**
- secret scan;
- backend suite on SQLite;
- **the whole backend suite on real PostgreSQL** (`ASC_TEST_SUITE_DB=postgres`);
- PostgreSQL concurrency and migration tests with `ASC_REQUIRE_PG=1` (they fail instead of being skipped without a server);
- commercial build, tests, audit and Playwright;
- dashboard typecheck, i18n and build.

**Verdict:** APROVADO only if everything passed and the PostgreSQL steps really ran with zero skipped tests. PENDENTE without Docker. REPROVADO on any failure. Exit codes 0, 2 and 1.

**Result on this machine (Windows 11, no Docker, WSL or PostgreSQL): PENDENTE.**

| Step | Result |
| --- | --- |
| Backend on SQLite | 518 passed, 1 skipped (a POSIX-only test) |
| Commercial | 37/37 |
| Playwright | 34 passed, 8 skipped by design (Chromium-only scans) |
| Dashboard | Passed |
| Secret scan | Passed |
| Both PostgreSQL steps | **NOT RUN** |

The migration scenario code was self-checked on throwaway SQLite only to catch bugs in the test itself. This is not homologation.

Fixed during this step:
- the script now finds Git Bash outside PATH;
- the script is saved as UTF-8 with BOM for Windows PowerShell 5.1;
- `npm ci` EPERM came from my own orphaned `vite preview` processes, and only those were stopped.

To finish, run `./scripts/homologacao.sh` on a machine with Docker (not the production servers) and require APROVADO with zero skipped PostgreSQL tests.
