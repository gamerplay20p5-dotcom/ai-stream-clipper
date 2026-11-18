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

## Etapa E (v0.5.5): quem está falando, legendas, metadados e marca

**Diarização.** O worker de áudio agrupa as palavras do Whisper em falas, com corte em pausas de mais de 0,7 s ou a cada 8 s. Para cada fala, calcula a impressão de voz com o WeSpeaker ResNet34 (ONNX, CPU, licença Apache-2.0); falas de 3 s ou mais também são medidas por metade. A API então rotula cada fala:
- `streamer`;
- `other:N`, um grupo estável de outra voz: amigos no rádio ou personagens do jogo;
- `unknown`, quando há dúvida (nunca forçado);
- `overlap`, quando uma metade da fala é claramente do streamer e a outra claramente de outra pessoa.

O volume da voz nunca é usado. O perfil do streamer começa como **hipótese**: é a voz com mais tempo de fala, depois de 5 minutos, e só se tiver 40% ou mais da fala agrupada. O perfil vira certeza apenas quando você confirma em **Streamers → Voz do streamer**, ouvindo trechos. "Não é o streamer" descarta a voz e a lembra.

**Legendas.** Cada grupo de palavras pertence a um só locutor. As cores e o tamanho dependem de quem fala e do modo escolhido:

| Locutor | Modo "destaque" (padrão) | Modo "só o streamer" | Modo "todas" |
|---|---|---|---|
| Streamer | estilo principal, palavra falada em amarelo | estilo principal | mesmo estilo para todos |
| Outras vozes | 22% menores, cor própria | ocultas, se a voz estiver confirmada | mesmo estilo para todos |

Sem voz confirmada, o modo "só o streamer" não oculta nada, porque uma hipótese errada apagaria falas.

Outros ajustes:
- fonte (só as instaladas), tamanho, posição dentro da área segura, animação e cores;
- pontuação no estilo de vídeo curto: mantém ? ! e ..., e remove vírgulas e pontos finais;
- correção manual no corte, com prévia sobre o vídeo, nova renderização e opção de voltar à transcrição automática.

**Metadados.** O modelo de texto recebe separadamente:
- a fala do streamer;
- as outras vozes;
- a memória da história.

Ele devolve descrição e hashtags. O texto muda por plataforma:

| Plataforma | Texto |
|---|---|
| YouTube | descrição, créditos, rodapé, hashtags e #Shorts |
| Instagram | título na primeira linha, depois descrição e hashtags |
| TikTok | legenda curta |

A situação da história vira hashtag (#perseguicao, #tiroteio…).

**Marca.** Nome do canal, marca d'água em texto discreta (semitransparente, longe das legendas), chamada no fim do vídeo, hashtags e rodapé da descrição. Nada é adicionado enquanto não for preenchido.

**Validação reproduzível.**
- *Amostra:* 12 cortes reais já renderizados, de 2 streamers e 3 lives, com 387 s de áudio, processados pelo mesmo caminho do worker. O roteiro está em `docs/benchmarks/etapa_e.md`.

| Medida | Resultado |
|---|---|
| Separação entre streamers: falas de um rotuladas como o outro | 0 de 27 e 0 de 39 |
| Semelhança (cosseno) entre os dois perfis | 0,29, bem abaixo do limite de 0,55 |
| Fala atribuída à voz dominante | 74% e 68% |
| Fala que ficou como "não identificado" | 10% e 13% |
| Custo da impressão de voz | cerca de 0,4 s por segmento de 10 s, com 2 threads (Whisper small: cerca de 5,5 s no mesmo notebook) |
| Legendas | 12 de 12 arquivos ASS renderizados pelo libass sem erro |

- *Exemplo visto:* a fala de um personagem do jogo, que também aparecia na legenda do próprio jogo, saiu como "outra voz".
- *Limitação:* ainda não há rótulos humanos de locutor. A taxa de acerto por fala **não está medida**: a confirmação no painel existe para isso.
- *Limitação:* amigos no rádio e vozes do jogo ficam no mesmo tipo `other:N`.

**Espaço.** Cada fala guarda a impressão de voz (cerca de 2 a 5 kB em JSON), o que dá uns 2 a 5 MB por hora de live no Postgres.

## Etapa F (v0.5.6): operação editorial

- **Aprovação em lote:** na Central de Cortes, selecione cortes e aprove ou rejeite todos de uma vez. Cada decisão continua sendo um rótulo humano para o aprendizado.
- **Agendamento em lote:** os cortes selecionados entram nos próximos horários livres da conta, o de maior nota primeiro. Os horários respeitam a regra da conta (ou 12h, 18h e 21h, até 3 por dia) e o envio é sempre **privado**.
- **Modo semiautomático** (regra "Semiautomático"):
  - a IA cria **propostas** com os melhores cortes elegíveis, nos horários da regra;
  - nada é enviado sem confirmação humana, que pode ser de uma em uma ou em lote;
  - proposta não confirmada expira no horário e pode voltar no próximo horário livre;
  - proposta recusada não volta.

  Proposta pública é permitida porque cada uma é confirmada por uma pessoa; a publicação pública **automática** continua exigindo ativação explícita. O TikTok continua só manual.
- **Pausa de emergência:** enquanto ativa, nada novo é enfileirado nem iniciado, e as regras não geram propostas. Um envio que já estava em andamento termina. Fica registrado na auditoria.
- **Calendário editorial:** próximos 14 dias no horário de Brasília, com mudança de horário de itens ainda não enviados.
- **Relatórios:** publicações, views e curtidas por plataforma, por categoria da história, por horário e por título; também a taxa de propostas confirmadas, recusadas e expiradas. São métricas oficiais das plataformas; amostras pequenas são só indício.
- **Correção:** o reenvio administrativo de uma publicação com falha reutilizava a chave da fila e não era executado. Agora cada rodada tem chave própria.

## Riscos

- **Poucos rótulos:** com 31 decisões humanas no início, qualquer conclusão é um indício. O painel mostra o tamanho de cada amostra e os intervalos de confiança.
- **Mídia já perdida:** o buffer de captura dura 30 minutos, então a revisão pós-live só alcança o que ainda existe: mídia dos cortes, mídia do dataset e os 30 minutos finais.
- **Separar a voz do streamer da fala do jogo:** desde a v0.5.5 há identificação de locutor; a precisão por fala ainda depende da sua confirmação da voz.

---

**EN summary:** Phase 5 extends the existing director, scoring, evaluation and publisher instead of rebuilding them.
- **New:** a bounded temporal story memory (`ai/story.py`), editorial criteria with preliminary/final scores and confidence, a re-watch job, and a media lifecycle (quarantine → purge) that keeps rows and labels.
- **v0.5.0:** clip approvals and rejections become human labels, and a low-priority learning job keeps the preference profile and the shadow ranker up to date.
- **Defaults:** destructive features start disabled or in dry-run.
- **v0.5.5 (stage E):** speaker diarization (WeSpeaker ONNX on CPU; streamer voice is a hypothesis until the owner confirms it; uncertain speech stays `unknown`), speaker-aware captions with manual correction, per-platform metadata from the streamer's own speech and the story memory, and optional branding (watermark, end card, hashtags, footer).
- **v0.5.6 (stage F):** batch approve/reject/schedule, editorial calendar with rescheduling, semi-automatic rules (the AI proposes, a human confirms; proposals expire), emergency pause, reports by platform/category/hour/title; fix: admin retry of a failed publication now really re-queues it.
