# Descoberta multimodal de cortes

## Fluxo

O job de áudio reaproveita o segmento já baixado e mede movimento localizado em quadros reduzidos a 192 × 108, a até 4 Hz. O detector compara o movimento com a atividade recente, acompanha deslocamento e rotação e filtra mudanças amplas de cena, clarões e áreas periféricas. Pode propor uma janela mesmo em vídeo sem trilha de áudio. A API valida até quatro hipóteses por segmento e guarda somente seus sinais, força e tempos em `AudioFeature.extra`.

O scoring combina as hipóteses com os eventos acústicos e verbais temporalmente próximos. O volume continua ajudando a encontrar reações, com as penalidades existentes para ruído de jogo. As hipóteses visuais podem propor cortes por conta própria. O scoring limita candidatos por hora, espera o desfecho disponível e usa os intervalos do acontecimento para separar eventos próximos que compartilham parte do contexto.

O worker de visão reutiliza os mesmos quadros para a heurística e o VLM. Na análise multimodal, reserva quadros de antes, do pico e de depois da hipótese. A classificação semântica precisa concordar com um evento relevante próximo ao pico e encaixar-se nos quadros mostrados. O Quality Gate avalia o acontecimento, o contexto e o desfecho mesmo sem fala. As variantes de edição preservam a faixa visual protegida.

**Quando e o quê vêm de fontes diferentes.** O instante vem do detector, medido nos quadros decodificados. O VLM nunca fornece o horário usado como evidência. Na homologação, o Qwen3-VL-2B listou um "evento" por quadro mostrado, a partir do primeiro (`t = 0.0, 7.0, 8.7…`), e essa narração é descartada (`ai/vlm.py`, `_narrated`). Um timestamp fora dos quadros mostrados, ausente ou fora de ordem também é descartado e nunca vira zero. Depois da análise principal, uma segunda passagem dirigida (`ai/visual_confirm.py`) faz ao VLM a mesma pergunta fechada sobre dois pares: um quadro antes e um depois da hipótese, e um par de controle antes dela. A ocorrência só é confirmada quando o par do evento mostra um acontecimento com confiança ≥ 0,6 e o par de controle não mostra nenhum. Um modelo que vê acontecimento em tudo falha no próprio controle. Respostas inválidas ou incompletas contam como não confirmadas. A confirmação vale para o clipe apenas se a hipótese tiver pelo menos 1,5 s de contexto antes e 1,0 s de desfecho depois dentro do corte (`confirmed_occurrence`). O título só nomeia o acontecimento quando ele foi confirmado ou corroborado; caso contrário, fica neutro.

Com região de webcam cadastrada, o diretor pode usar deslocamento ou mudança de tamanho do rosto e variação de contraste na região da boca. O sinal precisa durar mais de uma observação e superar a variação anterior. A análise guarda o horário da observação e reaproveita o rastreamento usado pelo editor. A métrica da boca fica na memória do worker. Sem detecção estável, o gameplay segue para análise. O cálculo trabalha com geometria e contraste e independe de identificação pessoal.

A geração de metadados usa o cadastro do canal, o jogo explicitamente configurado e uma taxonomia em PT-BR e EN. Uma hipótese incerta recebe um título neutro. Os nomes nos títulos identificam a origem da live. A descrição conserva a atribuição à transmissão quando a ação pode ter sido de outro jogador. O publicador aplica os limites e formatos por plataforma. Cortes experimentais exigem aprovação humana para o agendamento automático.

## Controles

| Controle | Padrão | Efeito |
|---|---|---|
| `visual_discovery` | `off` | `shadow` compara as duas seleções com o mesmo histórico. `on` inclui candidatos visuais no pipeline. |
| `multimodal_scoring` | `false` | Reduz a contribuição da intensidade sonora quando há uma hipótese visual temporal. |
| `grounded_metadata` | `false` | Ativa os novos metadados para os demais candidatos. Candidatos visuais usam metadados fundamentados por padrão. Em `shadow`, a geração de metadados segue o caminho atual. |
| `ASC_RENDER_ENCODER` | `libx264` | O worker de render aceita `h264_qsv` ou `h264_nvenc` após teste local. Se o encoder falhar, o job tenta `libx264` uma vez e registra o fallback. |
| `ASC_HW_ENCODER_TIMEOUT_S` | `90` | Prazo do encoder de hardware (no máximo 3 × a duração do corte). Se travar, ou se o MP4 não for H.264 1080 × 1920 com a duração esperada, o corte é refeito em `libx264`. |
| `visual_investigate_min` | `60` | Pontuação mínima para INVESTIGAR uma hipótese só visual (VLM + confirmação). O `min_score` continua sendo a barra editorial, aplicada pelo Quality Gate depois do VLM. Candidatos com som mantêm o `min_score`. |
| `visual_max_per_hour` | `6` | Candidatos visuais por canal e hora, dentro de `max_clips_per_hour`. Excedentes recebem `rate_limit`. |
| `ASC_VISUAL_DISCOVERY_SLOTS` | `2` | Varreduras visuais simultâneas no worker de áudio, em threads de baixa prioridade (nice +10 no Linux). Com todos os slots ocupados, o segmento é pulado e contado (`visual_skipped: busy`), nunca enfileirado na frente do áudio. |
| `ASC_VISUAL_DISCOVERY_TIMEOUT_S` | `6` | Orçamento por segmento de 10 s. Ao estourar, a varredura para (`visual_skipped: timeout`) e o áudio segue. |
| Preferência do canal `visual_discovery` | ausente | `off`, `shadow` ou `on` só para aquele canal, sobrepondo o valor global. Permite um piloto em um canal com o global em `off`. Outros valores retornam 422. |

A varredura visual roda em paralelo à análise de áudio, não antes dela. Altere os controles da tabela que não começam com `ASC_` via `PUT /api/settings` como administrador; a preferência do canal via `PATCH /api/streamers/{id}`. Em `shadow`, acompanhe `/api/diagnostics` → `streamers[].scoring.visual_shadow`. As duas seleções usam o mesmo histórico de cortes da sessão.

## Benchmark de descoberta e scoring

Baseline `f788dc3` da `main`. Ambiente local Windows, Intel i5-12450H, Python 3.14, OpenCV 4.14, FFmpeg, Whisper tiny e YAMNet para o corpus original. O scorer foi exercitado 160 vezes por rodada com a fixture repetida em uma timeline de 163,6 s, incluindo lacuna de áudio. Os valores abaixo comparam a mesma carga. A coluna multimodal inclui duas hipóteses visuais adicionais e pontuação multimodal ativa.

| Scoring | Main | PR com flags desligadas | PR com propostas visuais |
|---|---:|---:|---:|
| Mediana por chamada | 101,71 ms | 19,91 ms | 35,08 ms |
| p95 por chamada | 115,15 ms | 36,95 ms | 46,11 ms |
| Timelines/s | 13,01 | 41,86 | 27,66 |
| CPU por chamada | 72,85 ms | 23,63 ms | 33,89 ms |

A vazão com propostas visuais foi **2,13×** a da `main` (+112,5%). Com as flags desligadas, os resultados acústicos dessa carga coincidiram com os da `main` após excluir a chave aditiva de evidência visual vazia. O custo novo de decodificação dos segmentos ocorre no worker de áudio e é medido separadamente.

| Cenário anotado | Encontrados na main | Encontrados na PR | Candidatos sem correspondência |
|---|---:|---:|---:|
| `gameplay_event`, `chase_crash`, `loud_game_noise` | 2/2 | 2/2 | 0 → 0 |
| Queda, mudança de trajetória e capotamento silenciosos. Panorâmica, HUD, clarão e corte brusco como controle negativo. | 0/3 | 3/3 | 0 → 0 |
| Total em 230,13 s de vídeo | 2/5 | 5/5 | 0 → 0 |

Recall nesta amostra foi de **40% para 100%**. Todos os primeiros candidatos retornados por fonte corresponderam à anotação, com precisão@3 de 100% entre as fontes com candidatos. Nenhum falso positivo foi observado na rodada final. O intervalo necessário ficou inteiro nos três eventos silenciosos. O evento `chase_crash` preservou 67,8% do intervalo necessário tanto no baseline quanto na PR no benchmark de candidatos.

O teste de integração usa captura MPEG-TS, API HTTP, workers e renderizador. O caso silencioso produz um MP4 vertical em cerca de 11 s para 24 s de fonte local, com reprodução em velocidade livre, OpenCV e gate desativado apenas nesse teste. Um teste de contrato separado envia um resultado VLM controlado pela API, passa pelo Quality Gate e verifica título e desfecho com esse gate ativo.

Na rodada exploratória em CPU, o Qwen3-VL rotulou a animação geométrica de capotamento como queda. O evento que ele citou estava 6,7 s antes do pico analisado. A checagem temporal descarta esse rótulo para os metadados. O InternVL retornou `unknown` na animação. Essas duas chamadas validam o tratamento de incerteza com modelos reais. A taxa de acerto semântico em gameplay exige trechos autorizados e anotados.

## Reproduzir

Na raiz do checkout da PR, com `backend[dev,audio,vision]`, FFmpeg e os modelos locais disponíveis.

```bash
git worktree add --detach ../asc-baseline f788dc3
python tests/fixtures/make_visual_fixtures.py ../asc-visual-fixtures
export PYTHONHASHSEED=0
MODEL_DIR="/caminho/dos/modelos"
PYTHONPATH=../asc-baseline/backend python tests/fixtures/bench_visual_pipeline.py \
  --annotations tests/fixtures/annotations.json --annotations ../asc-visual-fixtures/annotations.json \
  --models-dir "$MODEL_DIR" --out ../before.json
PYTHONPATH=backend python tests/fixtures/bench_visual_pipeline.py --mode on \
  --annotations tests/fixtures/annotations.json --annotations ../asc-visual-fixtures/annotations.json \
  --models-dir "$MODEL_DIR" --out ../after.json
PYTHONPATH=backend python -m pytest -c backend/pyproject.toml tests/test_multimodal_discovery.py tests/test_render.py
PYTHONPATH=backend python -m pytest -c backend/pyproject.toml -m e2e tests/test_e2e.py
```

## GPU e homologação

O roteamento usa o backend compatível, o modelo, a VRAM total, a memória livre quando o worker envia uma leitura global confiável, a concorrência e a latência observada. Um modelo já residente pode ser reutilizado com VRAM livre baixa. Os workers Intel continuam usando OpenVINO. A estatística de alocação do plugin OpenVINO registra o consumo que ele conhece. A memória livre global deve vir de uma sonda do dispositivo que a reporte de forma confiável.

Compare os encoders no mesmo worker isolado, com a mesma fonte e um processo do benchmark por vez. Use `--device CPU` se a configuração local permitir concorrência com os serviços existentes. Para medir Arc, use um worker dedicado à rodada, `--device GPU` e o modelo já homologado para aquele host.

```bash
PYTHONPATH=backend python -m clipper.bench.pipeline_bench --models-dir "$MODEL_DIR" --render --encoder libx264
PYTHONPATH=backend python -m clipper.bench.pipeline_bench --models-dir "$MODEL_DIR" --render --encoder h264_qsv
ASC_MODELS_DIR="$MODEL_DIR" PYTHONPATH=backend python -m clipper.bench.pipeline_bench \
  --models-dir "$MODEL_DIR" --device GPU --vlm qwen3-vl-2b-int4 --classify-event --out ../arc-bench.json
```

No teste local de um trecho de 20 s, a mediana de duas renderizações foi de 16,91 s em `libx264` e 13,81 s em Quick Sync na Intel UHD, ambas em H.264 1080 × 1920. O lote com NVENC confirmou o retorno automático a `libx264` em um host sem GPU NVIDIA. Compare também tamanho e qualidade visual. O CRF do software e os parâmetros de qualidade dos encoders de hardware têm escalas diferentes.

1. Em homologação isolada, execute com as flags desligadas e preserve os resultados base por canal.
2. Ligue `visual_discovery=shadow` e rode vídeos autorizados anotados, com e sem webcam, em PT-BR e EN. Compare recall, falsos positivos por hora, precisão dos primeiros candidatos e cobertura do contexto e do desfecho.
3. Ligue `on` e as demais flags apenas nessa homologação. Cronometre a latência até o MP4, por etapa e por hora de live. Registre mediana, p95, CPU, RAM, VRAM, profundidade da fila e tempo em fallback. Revise a precisão factual dos títulos manualmente.
4. Na Node0, inventarie o dispositivo e memória livre antes da instalação. Um futuro worker NVIDIA de inferência precisa de backend e modelos compatíveis registrados e de marcação da capacidade no heartbeat. Use `--encoder h264_nvenc` somente após confirmar suporte e validar os MP4 no runtime desse worker.

**Limites da evidência.** A meta de +50% está demonstrada no scoring local. Os resultados editoriais vêm de fixtures sintéticas. O detector pode perder ações pequenas, sem textura, próximas da borda dos segmentos ou durante movimento intenso de câmera, e aproxima tempos em vídeo de taxa variável. A descoberta acrescenta trabalho ao worker de áudio e o custo varia com resolução e codec. A classificação semântica e a latência total em lives autorizadas, nas Arc A750 e sob múltiplas lives ficam para a homologação. A Node0 e a inferência NVIDIA seguem como trabalho futuro de infraestrutura.

**Rollback.** Retorne `visual_discovery` a `off`, as duas flags booleanas a `false` e o encoder do worker a `libx264`. A fila e o banco mantêm os contratos que já existiam. Candidatos experimentais já gravados aparecem para revisão e conservam a trava de aprovação para publicação automática.
