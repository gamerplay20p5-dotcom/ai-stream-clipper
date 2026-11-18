# Fase 5: Diretor Editorial Autônomo

*Português; English summary at the end.*

## Plano × código existente

| Item do plano | Já existe (reaproveitar) | Estender | Novo |
|---|---|---|---|
| Detecção em tempo real | `scoring/engine.py` (candidatos e atributos), YAMNet, Whisper, eventos | ligar à memória narrativa | — |
| Memória temporal / situações | — | — | `ai/story.py` (situações em janelas de 10 s, memória limitada) e tabela `situations` |
| Associação de eventos | `director.tension_onset` (contexto de tensão) | união e divisão de cortes por situação | regras de causalidade entre situações |
| Julgamento editorial (14 critérios, nota preliminar e final, confiança) | `director.decide` (fusão cética), `breakdown` da pontuação | — | `ai/editorial.py`; colunas `prelim_score`, `editorial_score` e `confidence` |
| Reassistir (2ª análise) e retrospectiva pós-live | job `vision_analyze`, `reanalyze`, mídia própria do dataset | amostragem mais densa e contexto da situação no prompt | job `editorial_review` e varredura pós-live |
| Limpeza por nota e quarentena | `maintenance.retention` (hoje **apaga registros** de rejeitados/não revisados) | substituída por um ciclo que apaga só a mídia e preserva os registros | campos `lifecycle`/`protected`/`favorite`, tabela `cleanup_log`, simulação e restauração |
| Revisão humana | página Avaliação, Central de cortes, rótulos, marcação de tempos | aprovar/rejeitar nos cortes vira rótulo (v0.5.0) | proteger, favoritar e restaurar |
| Aprendizado | dataset, classificador logístico em sombra, métricas | treino contínuo, perfil editorial e promoção por métricas (v0.5.0) | job `learn` |
| Títulos e descrições | `ai/narrative.py` (Qwen3-1.7B), `publish.suggest_metadata` | prompt por plataforma, focado na fala do streamer | perfil de marca |
| Legendas focadas no streamer | legendas palavra a palavra (Whisper) | separar a voz do streamer da fala do jogo | identificação de locutor |
| Publicação: manual, semiautomático, automático | `publish/*` (regras, aprovação, agendamento, limites) | modo semiautomático com aprovação em lote; pausa de emergência | — |
| Analytics | métricas oficiais por publicação | relatórios por categoria, horário e título | — |

## Estratégia incremental

Cada etapa é implantada sozinha, com backup do banco antes de migrações e tags de versão para rollback. Tudo que é destrutivo começa desligado ou em simulação:
- limpeza em modo simulação;
- publicação pública automática desligada;
- modelos novos em sombra.

| Versão | Conteúdo |
|---|---|
| v0.5.0 | aprendizado contínuo com as decisões humanas; base da memória narrativa e das tabelas da Fase 5 |
| v0.5.x | Etapa A ligada ao pipeline (situações e julgamento editorial); Etapa C (ciclo de armazenamento em simulação) |
| seguintes | Etapa B (reassistir e pós-live); Etapa E (legendas do streamer, títulos, marca); Etapa F (semiautomático, relatórios) |

## Riscos

- **Poucos rótulos:** com 31 decisões humanas no início, qualquer conclusão é um indício. O painel mostra o tamanho de cada amostra e os intervalos de confiança.
- **Mídia já perdida:** o buffer de captura dura 30 minutos, então a revisão pós-live só alcança o que ainda existe: mídia dos cortes, mídia do dataset e os 30 minutos finais.
- **Separar a voz do streamer da fala do jogo:** sem identificação de locutor, essa separação é aproximada.

---

**EN summary:** Phase 5 extends the existing director, scoring, evaluation and publisher instead of rebuilding them.
- **New:** a bounded temporal story memory (`ai/story.py`), editorial criteria with preliminary/final scores and confidence, a re-watch job, and a media lifecycle (quarantine → purge) that keeps rows and labels.
- **v0.5.0:** clip approvals and rejections become human labels, and a low-priority learning job keeps the preference profile and the shadow ranker up to date.
- **Defaults:** destructive features start disabled or in dry-run.
