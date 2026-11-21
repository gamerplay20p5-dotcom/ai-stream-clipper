# Integração dos PRs #16, #18 e #23

Nenhum dos três foi mesclado. A integração depende de autorização explícita do dono, PR por PR.

## Dependências

```
main ──┬── #16 painel-212-senhas         contas, senhas, SMTP, banco da 212 (backend + dashboard)
       │
       └── #18 feat/commercial-landing   landing, lista de espera, Stripe (USD), InfinitePay (BRL)
                 │                        (já contém #20, #21 e #22, mesclados com merge commit)
                 └── #23 feat/billing-entitlements
                           base = #18, e inclui um merge do #16 (commit 82444e2)
```

- **#16 e #18 são independentes:** nenhum arquivo em comum. O #18 só mexe em `commercial/` e no `.gitignore`.
- **#23 depende dos dois:** ele usa as contas e o SMTP do #16 e o serviço comercial do #18.
- Todos os commits do #16 e do #18 estão no histórico do #23 (`git merge-base --is-ancestor`). Nada foi copiado à mão nem reescrito, então não há duplicação.
- O #23 altera 11 arquivos que vieram do #16, por exemplo `routes_auth.py`, `routes_users.py`, `accounts.py` e os testes. É evolução, não perda: o papel `customer`, o cadastro, a origem canônica dos links e o SMTP com TLS obrigatório.

## Simulação

Feita com `git merge-tree` e `git commit-tree`, só com objetos temporários e sem alterar nenhuma branch:

| Passo | Resultado |
| --- | --- |
| `main` + #16 | Sem conflito |
| + #18 | Sem conflito |
| + #23 | Sem conflito. A árvore final é **idêntica** à do #23 |

## Sequência segura

1. **#16 → `main`**, com **merge commit** (não squash nem rebase).
   - Se o #16 for "squashado", o `main` ganha commits novos com o mesmo conteúdo. O #23, que contém os commits originais, passaria a mostrar o #16 de novo no diff e poderia ter conflitos.
   - Antes do deploy: backup do banco e migrações 0015 e 0016.
2. **#18 → `main`**, com **merge commit**.
   - **Não configure chaves de pagamento em produção só com o #18.** Ele ainda tem o checkout anônimo, que não libera acesso. O #23 é que o remove.
3. **Trocar a base do #23 para `main`** (`gh pr edit 23 --base main`). Com o #16 e o #18 já no `main`, o diff passa a mostrar só as mudanças de assinaturas e cotas. Revisar de novo e esperar o CI verde.
4. **#23 → `main`**, com merge commit.
   - Antes do deploy: backup do banco e migração 0017.
   - Configurar `ASC_PUBLIC_ORIGIN` (https) na 210. O compose da 210 não sobe sem ela e a API recusa iniciar em produção sem ela.
   - Configurar `ASC_BILLING_SECRET` e `ASC_COMMERCIAL_URL` na API, e `ASC_BILLING_SECRET`, `ASC_MAIN_API_URL` e `ASC_APP_URL` no serviço comercial.

**Não faça:**
- mesclar o #23 na `feat/commercial-landing` antes do #18 ir para o `main`, porque o #18 passaria a carregar o #16 e as assinaturas;
- mesclar o #18 antes do #16 sem antes conferir de novo. Funciona (são independentes), mas a ordem acima mantém cada PR revisável sozinho.

## English

#16 (accounts) and #18 (landing and payments) are independent: no shared files, each one based on `main`. #23 (subscriptions) is based on #18 and contains a merge of #16. Every commit of both is in its history, so nothing is lost or duplicated.

A dry run with `git merge-tree` (temporary objects only) of `main` + #16 + #18 + #23 merges cleanly and yields exactly #23's tree.

Safe order:
1. Merge #16 into `main` with a merge commit. Do not squash: a squash would make #23 show #16 again and could conflict.
2. Merge #18 into `main` with a merge commit, and configure no payment keys in production until #23 lands.
3. Retarget #23 to `main`, review again and wait for green CI.
4. Merge #23. Back up the database before migrations 0015-0017, and set `ASC_PUBLIC_ORIGIN` and the billing link variables.
