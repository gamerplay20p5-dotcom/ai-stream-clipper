# Fase 3: avaliação editorial, feedback humano e aprendizado

## Princípio

A verdade de referência vem **somente de avaliações humanas**. As saídas da IA (heurística, Qwen e classificador) ficam guardadas como *predições*, ao lado dos rótulos, e nunca são usadas como rótulo. Sem avaliações suficientes, as métricas aparecem como "n/d" e nenhuma melhoria é anunciada.

## O que existe (v0.3.x)

| Item | Onde |
|---|---|
| Todo momento pontuado vira registro, inclusive os rejeitados, com atributos observáveis e a versão da pontuação | tabela `candidates`, `scoring/engine.py` (`SCORING_VERSION`, `features`) |
| Procedência de cada corte: app, pontuação, cópia das configurações, modelo, dispositivo, versão do prompt, tokens, quadros e diretor | `clips.provenance`, `evaluation/versions.py` |
| Dataset com cópia congelada do contexto (transcrição, eventos acústicos, visão) e mídia própria em 720p | `eval_examples`, job `dataset_media` (206), `evaluation/dataset.py` e `evaluation/media.py` |
| Divisão treino/validação **por sessão**, determinística | `dataset.split_for` |
| Amostragem ativa com cotas por hora de live | `evaluation/sampler.py` |
| Avaliação humana: nota, motivos editoriais separados dos técnicos, tipo de momento, qualidade do contexto, descrição e marcações de tempo | tabela `reviews`, página **Avaliação** |
| Métricas: precisão, cobertura, falsos positivos por hora, falsos negativos conhecidos, erro de início e fim, Precision@5 (A/B/C), p50/p95 operacionais | `evaluation/metrics.py`, aba Métricas |
| Exportação JSONL (contexto, rótulo e versões) e CSV de atributos | `/api/review/export` |
| Classificador leve (regressão logística), comparado com a heurística nos mesmos exemplos de validação | `evaluation/ranker.py`, aba Dataset e modelos |
| Retenção e cota da mídia do dataset (os rótulos nunca são apagados) | Configurações da IA: `dataset_*` |

A fila de revisão é sugerida por amostragem ativa e inclui:
- os cortes gerados;
- momentos perto do limiar;
- rejeitados com reação;
- barulho sem reação;
- amostras de conversa e de notas baixas;
- casos em que áudio e visão discordam, que sobem para o topo.

O classificador funciona assim:
- recusa treinar com dados insuficientes (pelo menos 30 exemplos de treino, 8 por classe, e 10 de validação);
- começa em **sombra**: calcula a nota e mostra, mas não decide;
- só pode ser ativado se superar a heurística na validação, e versões anteriores podem ser restauradas.

## Coleção piloto (18/11/2026)

67 exemplos reais logo após a implantação: 51 cortes automáticos, 4 testes manuais e 12 momentos rejeitados (rejeitados com reação, perto do limiar, conversa e notas baixas). A coleção cresce sozinha com as lives. Todos aguardam avaliação humana: **ainda não há rótulos**, então não existe métrica de qualidade medida.

## Pendente nesta fase

- **Etapa B, julgamento contextual:**
  - memória temporal por transmissão (situações em andamento, como uma perseguição);
  - união de candidatos da mesma sequência;
  - prompt editorial com evidências observáveis.

  As configurações `memory_*` e `merge_gap_seconds` já existem. A lógica ainda não.
- **Etapa C, otimização medida:**
  - amostragem visual adaptativa;
  - pressão da fila de visão (`vision_queue_max`).

  O gargalo real encontrado e corrigido até agora foi o Whisper serializado (v0.3.1): vazão de áudio de 0,14 para 0,43 job/s.
- **Etapa D, aprendizado:** treinar e comparar o classificador assim que houver avaliações suficientes. A comparação A × B × C já está pronta na aba Métricas.
