# Integração final: PRs #4 a #8 (proposta, 2026-11-19)

Nada foi mesclado nem implantado. Este documento propõe a ordem de integração, com os SHAs exatos, os conflitos e os riscos.

## 1. Estado dos PRs

| PR | Branch | Base | SHA testado | Conteúdo | Situação |
|---|---|---|---|---|---|
| #4 | `fase6c-laboratorio-referencias` | `main` | `a928aed` | 6C, v0.6.3, migração 0011 | pronto |
| #5 | `redesign/hts-operator-studio` | #4 | `8a1154d` | identidade HTS / Vice City (Codex) | pronto |
| #6 | `multiusuario-compartilhamento` | `main` | `64ff70c` | multiusuário, v0.7.0, migração 0012; **contém o #4** | pronto |
| #7 | `ux/fluxos-de-trabalho` | #6 | `76d855d` (+ esta documentação) | UX v0.7.1; **contém #4, #5 e #6** | Draft até a validação integrada |
| #8 | `hotfix/monitoramento-v0.6.2` | `main` | `ae2db33` | hotfix do monitoramento, `0.6.2-hotfix.1`, sem migração | aguardando autorização |

`main` = `8f80df8` = tag `v0.6.2` (produção). Ancestralidade conferida: #4 ⊂ #5, #4 ⊂ #6, #5 ⊂ #7 e #6 ⊂ #7. Não há commits duplicados entre os branches. O #8 parte da tag `v0.6.2` e não contém nada dos outros.

## 2. Ordem proposta, sem force push

1. **#8 (hotfix) primeiro, no `main`**, e implantação na produção v0.6.2 (prioridade absoluta).
2. **#4**, depois **#5** (retarget para `main` depois do #4), depois **#6**, sempre com *merge commit*.
3. **Merge do `main` no #7.** Ele traz o #8; os conflitos estão na seção 3, já resolvidos numa simulação local. Em seguida, o #7 sai do Draft e é mesclado.

Assim a correção do monitoramento entra na linha principal **antes** da versão unificada, como pedido.

## 3. Conflitos esperados ao juntar #7 e #8 (resolvidos na simulação)

| Arquivo | Resolução |
|---|---|
| `backend/clipper/__init__.py`, `backend/pyproject.toml`, `dashboard/package.json` | Fica a versão **0.7.1**, que passa a conter o hotfix. |
| `backend/clipper/patch_notes.py` | Ficam todas as entradas; a do `0.6.2-hotfix.1` entra entre a 0.6.3 e a 0.6.2. |
| `backend/clipper/workers/handlers.py` | Fica o `monitor_loop` novo (supervisionado) e se mantém o `handle_format` da 6C, que vinha logo depois do loop antigo. |
| `dashboard/src/pages/Streamers.tsx` | Fica o layout do #5 com o `MonitorBadge`, o alerta "Monitoramento atrasado" e o retorno real do "Verificar agora". As cores usam o token `warning` do tema. |

A simulação está no branch **local** `simulacao/integracao-0.7.1` (commit `91d02e6`, não enviado). As correções de texto que o #8 recebeu depois (`5b1162e`, `ae2db33`) já estão aplicadas nela.

## 4. Migrações

A cadeia é única e linear: `0008 → 0009 → 0010 → 0011 (#4) → 0012 (#6)`, com head única. O hotfix não tem migração.

## 5. Testes da versão integrada (simulação #7 + #8)

| Verificação | Resultado |
|---|---|
| `pytest` (suíte completa) | **364 passaram**, 3 pulados (precisam de modelos reais de IA, ausentes nesta máquina) |
| e2e real (`test_e2e.py`: API e worker com o monitor novo, live detectada, captura, corte) | passou |
| e2e de compartilhamento com duas contas | passou dentro da suíte |
| Typecheck, `check:privacy`, build | ok |
| Cenários Playwright (`dashboard/scripts/ux/scenarios.py`) contra a API e o frontend integrados | **21 passaram** |
| Tela de Streamers integrada com canais sem verificação | mostra "Monitoramento atrasado" e o alerta no topo |

Os 21 cenários cobrem:
- login de administrador e de convidado;
- o convidado vê só os vídeos autorizados e não chama rotas da equipe;
- download bloqueado por padrão;
- revogar, com confirmação;
- reaprovação explícita;
- Central de cortes e atalhos;
- Diretor IA;
- rascunho dos parâmetros;
- Referências (guia);
- confirmação em massa só depois da revisão (interceptada; nenhuma sugestão foi confirmada);
- Hoje com dados indisponíveis;
- biblioteca com acesso revogado ou sessão encerrada;
- 320 e 390 px;
- movimento reduzido.

## 6. Pendências antes de implantar a versão unificada

1. **Acesso aos servidores.** O SSH para 201, 206 e 210 dá *timeout* desta máquina. Nada abaixo pode ser feito daqui sem ele.
2. **Auditoria somente leitura do PostgreSQL:**
   - `SELECT version_num FROM alembic_version` (deve ser 0010);
   - publicações `uploading`, `processing` e `uncertain`;
   - agendadas: id, horário, privacidade e conta, guardadas para comparar depois;
   - consultas da `docs/MULTIUSUARIO.md`, seção 1.
3. **Backup completo validado** (`asc-ctl backup-db`, restaurado num banco temporário).
4. **Esquema:** 0011 e 0012 só criam tabelas e colunas. O rollback exige fazer o downgrade com a **imagem nova** primeiro (`docs/FASE6.md`).
5. **Arquivos:** conferir que os vídeos aprovados e os snapshots de publicação existem antes e depois.
6. **Serviços do Hank:** só o projeto compose `asc` é tocado, via `asc-ctl`.

## 7. Implantação e rollback (só com autorização)

**Hotfix (agora):**
- `asc-ctl status` e `asc-ctl compose logs worker-capture`, para registrar a evidência;
- build `0.6.2-hotfix.1`, `set-tag` e `compose up`;
- conferir `monitor.state` e as métricas do monitor avançando.

Rollback: `set-tag 0.6.2`.

**Versão unificada 0.7.1 (depois):**
1. Pausar publicações (`clipper publish-pause on`) e registrar as agendadas.
2. Backup validado.
3. Build e `set-tag 0.7.1`.
4. A API aplica 0011 e 0012.
5. Conferir saúde, monitor, agendadas iguais e cenários principais.
6. Retomar publicações.

Rollback:
1. Pausar publicações.
2. Rodar `clipper rollback-check`.
3. Com a imagem 0.7.1, `migrate --downgrade 0010`.
4. `set-tag 0.6.2-hotfix.1`.

## 8. Recomendação

- **Pode ser mesclado já, com autorização:** o **#8** (hotfix), seguido da implantação controlada na v0.6.2.
- **Pronto para mesclar na ordem:** #4, #5 e #6 (sem mudanças desde as revisões).
- **Precisa de uma última revisão:** o #7, depois de receber o `main` com o #8 (conflitos da seção 3). Sai do Draft depois disso.
- **Antes de implantar a unificada:** acesso aos servidores, auditoria do banco, backup validado e conferência das publicações agendadas.

## EN summary

PR #7 already contains #4, #5 and #6. Merge order:
1. #8 hotfix into `main`, then deploy it on v0.6.2;
2. #4, #5 and #6;
3. `main` into #7, where the hotfix conflicts on versions, patch notes, `handlers.py` and the Streamers page were already resolved in a local simulation;
4. #7.

The Alembic chain is single and linear up to 0012. The integrated simulation passes 364 tests (3 skipped), the real e2e and 21 Playwright scenarios. Production deployment still needs server access, a read-only database audit and a validated backup. Nothing was merged or deployed.
