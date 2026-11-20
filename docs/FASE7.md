# Fase 7: escola editorial / Phase 7: editorial school (v0.7.3)

**PT.** Esta fase transforma critérios de um editor profissional de vídeos curtos em verificações medíveis e
explicáveis, aplicadas a cada corte depois do Diretor IA. Código: `backend/clipper/ai/playbook.py`.

**EN.** This phase turns a professional short-form editor's checklist into measurable, explainable checks
applied to every clip after the AI Director. Code: `backend/clipper/ai/playbook.py`.

## 1. Critérios / Criteria

Cada critério vai de 0 a 10 e guarda a evidência que o gerou. Sem sinal, o valor fica vazio (nunca um valor
médio inventado). / Each criterion is 0-10 with its evidence; with no signal it stays empty (never a made-up middle).

| Critério | O que um editor verifica | Afeta a nota? |
|---|---|---|
| `ingame` — Acontece dentro do jogo | O corte é sobre o que acontece no jogo/RP, não sobre a live (Discord, assinatura, configurações, pix, “vou mostrar pra vocês”, “o chat”). Também usa o resumo visual (menu, navegador, área de trabalho). | Sim. Abaixo de 4 o corte é **fora do jogo**: nota ×0,7 e bloqueio na revisão de qualidade. |
| `payoff` — Tem desfecho | Algo se resolve dentro do corte: desfecho da história (preso, fugiu, risada), arco visual completo, ou palavras de resultado depois do pico (“consegui”, “deu ruim”, “não acredito”, “kkkk”). Energia sem desfecho é enchimento. | Sim. |
| `intelligible` — Fala compreensível | Quem nunca viu a live entende a fala: mistura de idiomas e fala muito esparsa pesam contra. | Sim. |
| `hook` — Gancho no início | Os primeiros 3 s dão motivo para continuar assistindo. | **Não.** Vira sugestão de edição (novo início). |
| `ending` — Termina no ponto certo | O corte termina pouco depois da reação, não em conversa arrastada. | **Não.** Vira sugestão de edição (novo fim). |

**Por que gancho e final não reduzem a nota / Why hook and ending don't lower the score:** medidos contra as
decisões humanas, os dois não separam aprovados de rejeitados (AUC ~0,50). Eles descrevem o **corte**, que a
edição corrige, e não o **acontecimento**. Por isso entram como correções concretas (`edit_fixes`), não como
penalidade. / Measured against human decisions they don't separate approved from rejected (AUC ~0.50): they
describe the cut, which editing fixes, not the happening.

## 2. Onde é aplicado / Where it runs

1. `pipeline_ai._apply_playbook`: depois que o Diretor define limites e nota. Ajusta `clip.score` com
   `playbook.adjust` (**nunca aumenta** a nota; fora do jogo corta forte) e guarda tudo em
   `provenance["playbook"]` (versão, nota antes/depois, critérios com evidência, correções sugeridas).
   Qualquer erro é registrado e o corte segue sem alteração.
2. `editorial.judge`: os critérios aparecem no julgamento do “Por que este corte?”; correções viram
   sugestões de edição; conteúdo fora do jogo recebe o motivo de rejeição explícito.
3. `quality_gate`: `payoff` entra no peso do acontecimento, `intelligible` no da narrativa, e `ingame < 4`
   é bloqueante (“conteúdo fora do jogo”).
4. Prompts: o VLM (`vlm.py`) aprendeu que menus, configurações, Discord, navegador e conversa sobre a live
   não são acontecimentos; o narrador (`narrative.py`) segue regras de título profissional (quem + o quê,
   prometer só o que é entregue, sem títulos genéricos como “Reação” ou “Momento”).

Sem migração de banco. / No DB migration.

## 3. Medição / Measurement

Avaliação offline contra 123 decisões humanas (33 aprovados, 90 rejeitados) exportadas da produção:

```
PYTHONPATH=backend python -m clipper.evaluation.playbook_eval data/eval/clips_labeled.json
```

| | Antes | Com o playbook |
|---|---|---|
| AUC (aprovado acima de rejeitado) | 0,851 (IC 95% 0,776–0,913) | **0,877** (IC 95% 0,811–0,932) |
| Marcados “fora do jogo” | — | 4 rejeitados, **0 aprovados** |
| Mantidos com nota ≥ 70 (aprov./rej.) | 30 / 30 | 30 / **27** |
| Mantidos com nota ≥ 75 | 23 / 28 | 23 / **25** |
| Mantidos com nota ≥ 80 | 14 / 5 | 14 / **3** |

Separação por critério (AUC): payoff 0,62; intelligible 0,62; ingame 0,54 (raro, mas preciso: só pega
rejeitados); hook 0,49; ending 0,50.

**Limites honestos / Honest limits:** amostra pequena e os intervalos de confiança se sobrepõem; a melhora é
consistente (nenhum aprovado perdido em nenhum limiar), mas deve ser confirmada com as próximas decisões.
A maioria das revisões tem só aprovar/rejeitar, sem motivo; quanto mais motivos forem preenchidos, melhor a
próxima calibração. / Small sample, overlapping CIs; confirm on new decisions.

## 4. Próximos passos / Next steps

- Motivos de rejeição com um clique na Central (sem digitar).
- O editor estrutural aplicar automaticamente as correções de início/fim.
- Reavaliar com decisões novas (holdout) antes de aumentar os pesos.
- Mais quadros ou um VLM maior para o critério `ingame` visual.
