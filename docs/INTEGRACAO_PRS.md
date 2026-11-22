# Integração dos PRs #16, #18, #23 e #24

## Estado em 2026-11-21

| PR | Situação | Commit |
| --- | --- | --- |
| #16 contas, senhas, SMTP e banco da 212 | **Mesclado na `main`** (merge commit) | `d15b06e` |
| #18 landing, Stripe (USD) e InfinitePay (BRL), com #20, #21 e #22 | **Mesclado na `main`** (merge commit) | `aff7073` |
| #23 assinaturas, cotas e outbox | Aberto, **base `main`**. Aguarda o CI | `154f472` (conteúdo de `3f1cc64`) |
| #24 lives compartilhadas e direitos por período pago | Aberto, **base #23**. Aguarda o #23 e os testes em PostgreSQL real | `d7e617f` (conteúdo de `5d13049`) |

Nada foi implantado. As migrações 0015 e 0016 (do #16) estão na `main`, mas só rodam quando alguém implanta uma imagem nova (`asc-ctl`, sempre manual). Os pagamentos do #18 continuam desligados: o serviço comercial não tem deploy, Dockerfile nem chaves.

### Verificações antes dos merges

- A `main` não tem proteção de ramo nem regras. O plano gratuito não oferece isso para repositório privado, então não havia proteção a respeitar.
- Não há webhooks, deployments, environments nem workflows na `main`. Nada no repositório puxa a `main` sozinho (sem watchtower, cron ou `git pull` automático).
- O #16 mexe só em `backend/`, `dashboard/` e `tests/`, e o #18 só em `commercial/` e `.gitignore`. Os dois estavam `MERGEABLE`/`CLEAN`, e o #18 foi conferido de novo depois do merge do #16.

### Base de merge do #23 e do #24

O #23 já continha um merge próprio do #16 (`82444e2`). Depois do merge do #16 na `main`, a comparação do PR passou a ter **duas bases de merge**. Por isso o diff mostrava 21 arquivos do #16 que já estavam na `main`, embora nenhum commit estivesse duplicado.

**Correção:**
- a `main` foi mesclada no ramo do #23 (`154f472`), sem mudar o conteúdo, só o ancestral comum. A árvore é idêntica à do `3f1cc64`;
- o #23 foi mesclado no ramo do #24 (`d7e617f`), também sem mudar o conteúdo, igual ao `5d13049`.

**Resultado:**
- O #23 mostra só as próprias mudanças: 71 arquivos e 3 commits próprios, mais o merge de sincronização.
- O #24 mostra só as dele: 28 arquivos.
- O merge simulado da `main` com o #23 dá exatamente a árvore do #23.

### Próximos passos

1. **Liberar o GitHub Actions** (seção abaixo) e esperar o CI do #23 ficar verde.
2. **#23 → `main`** com merge commit. Antes do deploy:
   - backup do banco;
   - migração 0017;
   - `ASC_PUBLIC_ORIGIN`, `ASC_BILLING_SECRET` e `ASC_COMMERCIAL_URL` configurados.
3. **Trocar a base do #24 para `main`**, revisar de novo e rodar os testes em PostgreSQL real:
   - pelo `infrastructure/compose/test-postgres.yml` numa máquina com Docker;
   - ou pelo job `postgres` do CI.
4. **#24 → `main`** com merge commit, só depois dos testes em PostgreSQL. Antes do deploy, backup do banco e migração 0018.

Sempre com merge commit. Nunca squash nem rebase: os PRs seguintes contêm os commits originais.

## GitHub Actions: `startup_failure`

**Teste decisivo (sem custo):**
- um workflow mínimo e válido, cujo único job tem `if: false`, foi enviado num ramo temporário (`ci-probe`, já apagado);
- ele também terminou em `startup_failure` (`BuildFailed`, sem nome e sem jobs);
- **os arquivos de workflow do projeto não são a causa**: o GitHub recusa iniciar qualquer workflow neste repositório.

**Verificado:**
- Actions habilitado e todas as actions permitidas;
- repositório ativo e não arquivado;
- dono do tipo usuário, repositório privado;
- permissões padrão de leitura.

**Fora do meu alcance:** o token do `gh` não tem o escopo `user`, que é o que lê faturamento e uso. A mensagem exata aparece:
- no topo da página do run, aberta logada no navegador. Exemplo: https://github.com/gamerplay20p5-dotcom/ai-stream-clipper/actions/runs/44926370728;
- em **Settings → Billing and plans → Usage / Spending limits / Payment information**.

Causas típicas desse sintoma em repositório privado: conta bloqueada por problema de cobrança, pagamento recusado, ou minutos do Actions esgotados com limite de gastos em 0.

**Ações do dono (nenhuma foi feita, porque envolvem cobrança):**
- regularizar o pagamento;
- ajustar o limite de gastos;
- ou tornar o repositório público (minutos gratuitos e proteção de ramo).

Depois, refazer os runs com `gh run rerun <id>` ou um push novo.

## English

**State:**
- #16 and #18 were merged into `main` with merge commits: `d15b06e` and `aff7073`.
- Checks before merging:
  - no branch protection or rulesets (unavailable on the free plan for private repositories);
  - no webhooks, deployments, environments or workflows on `main`, and nothing pulls `main` automatically;
  - no file overlap between #16 and #18;
  - both were clean.
- Nothing was deployed. Migrations 0015 and 0016 only run on a manual image deployment, and payments stay off (no commercial deployment or keys).

**#23 and #24:**
- #23 now targets `main`.
- Because #23 contained its own merge of #16, the comparison had two merge bases and showed #16 files again. Merging `main` into #23 (`154f472`) and #23 into #24 (`d7e617f`) fixed that without changing either tree.
- #23 now shows only its own 71 files; #24 stays based on #23 with its 28 files.

**Next steps:**
1. Unblock GitHub Actions and get a green CI on #23.
2. Merge #23 (merge commit).
3. Retarget #24 to `main`, run the real PostgreSQL tests, then merge #24.
4. Back up the database before migrations 0017 and 0018.

**Actions:**
- A zero-cost probe (a valid workflow whose only job is `if: false`) also ended in `startup_failure`. The cause is account or repository level, not the workflow files.
- The exact message needs the `user` token scope or the run page in a browser.
- Likely billing: a locked account, a failed payment, or exhausted minutes with a $0 spending limit.
- Only the owner can resolve it. No billing change was made.
