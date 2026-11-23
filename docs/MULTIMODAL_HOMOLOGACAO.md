# Homologação multimodal: correções P0/P1 da PR #29

Correções feitas depois da homologação da PR #29, comparadas com o estado da PR em `d66fae8`. As novas opções ficam desligadas por padrão: `visual_discovery=off`, e o canal sem preferência segue o valor global. Pagamentos, cadastro, SMTP e o PostgreSQL de produção não foram alterados.

## Resumo

| Item | Antes (`d66fae8`) | Depois |
|---|---|---|
| Timestamps do VLM | Qwen3-VL-2B listou um evento por quadro mostrado, a partir de t = 0. A análise era descartada e o gate reprovava | Narração quadro a quadro, horário fora dos quadros, ausente ou fora de ordem: descartados, nunca viram zero |
| Quando e o quê | O VLM precisava acertar o instante | O instante vem do detector. O VLM só confirma **se** algo aconteceu e **o quê**, com um par antes/depois e um par de controle |
| Falso positivo da confirmação | Não havia confirmação | Rejeitou 11/11 hipóteses em vídeos sem acontecimento real (VLM real, GPU) |
| Contexto e desfecho | Não verificados para a evidência visual | Confirmação exige ≥ 1,5 s antes e ≥ 1,0 s depois da hipótese dentro do corte |
| Hipótese visual × `min_score` 70 | Piso `min(70, 100 × confiança)`: só confiança ≥ 0,70 virava candidata | `visual_investigate_min` (60) decide a investigação; o `min_score` continua editorial, no gate |
| Piloto | Só global | Preferência `visual_discovery` por canal (`off`/`shadow`/`on`), validada (422) |
| Descoberta no worker de áudio | Antes do áudio, sem limite | Em paralelo ao áudio, slots (2), orçamento por segmento (6 s), threads com nice +10, descarte contado quando ocupado |
| Encoder de hardware travado | Esperava o timeout do software (≥ 600 s) | Prazo de 90 s (máx. 3 × duração), saída validada (H.264 1080 × 1920, duração) e refeita em `libx264` |
| Testes | — | `tests/test_multimodal_fixes.py`: 25 testes. Suíte completa: 577 passaram, 19 pulados |

## Timestamps: o que é aceito

| Caso | Exemplo | Resultado |
|---|---|---|
| Correto | `t` igual a um quadro mostrado | Mantido |
| Incorreto | `t = 23,9` sem quadro nesse instante | Descartado, com nota |
| Ausente | sem `t`, ou `t: null` | Descartado (não vira 0) |
| Inconsistente | 3 ou mais eventos nos primeiros quadros, em ordem (narração) | Todos descartados como evidência temporal |
| Arco fora de ordem | clímax antes do início | Arco descartado |

A confirmação nunca é relaxada para aprovar: resposta inválida ou incompleta, confiança < 0,6, acontecimento também no par de controle ou nenhum acontecimento no par do evento contam como "não confirmado". O título fica neutro.

## Resultado com o VLM real

Rodadas locais em Intel UHD (GPU), OpenVINO, `qwen3-vl-2b-int4`, sem forçar o gate.

**Ponta a ponta** (API, workers de captura, áudio, scoring, visão e render reais; gate ativo; global `off` e piloto `on` só nos canais de teste):

| Cena | Hipótese do detector | VLM principal | Confirmação | Gate |
|---|---|---|---|---|
| Capotamento sintético | 12,27 s (`abrupt_change`, 0,66) | `unknown`, eventos narrados descartados | não confirmado: "nenhum acontecimento marcante" | Reprovado, título neutro |
| Direção normal (controle) | nenhuma | — | — | sem candidato |

Tempos do corte investigado: VLM 51,0 s, confirmação 14,8 s, total 72 s na Intel UHD.

**A condição de liberação do piloto ainda não foi cumprida.** Nenhum Short silencioso foi aprovado com o gate ativo. O motivo foi medido, não presumido. A cena de capotamento é montada a partir de recortes de imagens do projeto. O Qwen3-VL-2B descreve o antes e o depois com a mesma frase, mesmo com o carro já tombado. O Qwen3-VL-4B, testado no mesmo par, descreve a imagem como "a traseira do carro refletida numa lente" ou "efeito de lente distorcida", e também não confirma. Os dois rejeitaram corretamente a direção normal. A rejeição é o comportamento correto para uma montagem. A validação semântica precisa de gameplay real autorizado.

Limitação também medida: a colisão sintética, com a câmera tremendo, não gera hipótese. A mudança é ampla demais para o detector de objeto localizado.

## Custo e latência (local, i5-12450H, mediana de 5)

Descoberta por segmento de 10 s, em processo novo a cada medida:

| Formato | CPU antes | CPU depois | Tempo antes | Tempo depois | Memória |
|---|---:|---:|---:|---:|---:|
| 720p30 | 1,08 s | 1,17 s | 0,85 s | 0,85 s | 71 MB |
| 1080p30 | 1,61 s | 1,88 s | 1,51 s | 1,57 s | 97 MB |
| 1080p60 | 2,06 s | 2,19 s | 1,91 s | 2,03 s | 96 MB |

O novo sinal `abrupt_change` custa 5% a 20% de CPU. A decodificação H.264 precisa de todos os quadros, porque os quadros dependem uns dos outros. O leitor já descarta sem converter os que não usa, e a leitura pelo ffmpeg sem o filtro de bloco foi medida e rejeitada: perdeu detecções e foi mais lenta no total. Na 206, o custo é controlado por **onde** a descoberta roda: só nos canais piloto, com 2 slots, orçamento de 6 s e descarte quando ocupado.

Tempo até a análise de áudio ficar pronta (Whisper tiny + YAMNet):

| Formato | Só áudio | Antes: varredura e depois áudio | Depois: em paralelo |
|---|---:|---:|---:|
| 720p30 | 2,76 s | 4,93 s | 3,76 s (−24%) |
| 1080p30 | 3,47 s | 5,40 s | 4,01 s (−26%) |
| 1080p60 | 3,87 s | 4,66 s | 4,16 s (−11%) |

A diferença que sobra em relação ao áudio sozinho é disputa de CPU no notebook; no Linux, as threads da varredura rodam com nice +10.

## Plano de piloto (sem ativação até autorização)

1. **Gameplay real autorizado.** Reunir de 3 a 5 trechos de lives com permissão do criador, com um acontecimento silencioso e o instante aproximado anotados. Incluir trechos sem acontecimento como controles. Rodar o [replay local](#replay-local-com-vlm-e-quality-gate) e revisar o JSON.
2. **Critério para liberar.** Pelo menos um acontecimento silencioso confirmado pelo VLM real, com o corte contendo contexto e desfecho, aprovado pelo gate ativo, e nenhum falso positivo nos controles.
3. **Piloto por canal.** Com o global em `off`, definir `visual_discovery: "shadow"` só no canal autorizado (`PATCH /api/streamers/{id}`), por 1 a 2 semanas. Acompanhar `visual_skipped` (busy/timeout), CPU da 206, latência do áudio e propostas por hora. Passar a `on` só nesse canal depois disso.
4. **Rollback.** Remover a preferência do canal (ou `null`). Nada no banco precisa ser desfeito.

## Benchmark isolado nas Arc A750 (201 e 210)

O `asc-ctl gpu-validate` instalado roda o `gpu_probe` e o `vlm_bench` num contêiner avulso com a GPU, mas sempre com a imagem de produção (`ASC_IMAGE_TAG`), e o `asc-ctl build` compila a partir do checkout de produção. Para medir esta branch sem tocar nos serviços, a proposta abaixo precisa de uma nova versão do `asc-ctl`. **Ela não foi instalada** e depende de autorização.

| Comando proposto | Faz |
|---|---|
| `asc-ctl bench-checkout REF` | `git worktree` de um commit da origem em `/srv/asc/bench/src-<sha>`, sem mexer no checkout de produção |
| `asc-ctl bench-build TAG` | Imagem `ai-stream-clipper-vision:bench-TAG` a partir desse worktree |
| `asc-ctl bench-run TAG probe\|confirm\|vlm [--model M]` | Contêiner avulso com `/dev/dri/renderD128`, `--cpus 4 --memory 12g --pids-limit 256`, `--cpu-shares 256` na 201, modelos e mídia somente leitura, resultado em `/srv/asc/bench`, limite de 30 min, um benchmark por vez (lock) |
| `asc-ctl bench-rm TAG` | Remove a imagem e o worktree de bench |

Ordem sugerida: primeiro a 201, fora do horário de pico, com o `probe` e o `confirm` nas fixtures do repositório; depois com a mídia autorizada (`asc-ctl test-media-add`). Comparar com a Intel UHD local: tempo do VLM, tempo da confirmação e respostas iguais para os mesmos quadros. Nenhuma flag de produção muda nesse processo.

## Reproduzir

```bash
PYTHONPATH=backend python -m pytest tests/test_multimodal_fixes.py
python tests/fixtures/make_realistic_fixtures.py ../fixtures-realistas
PYTHONPATH=backend python tests/fixtures/probe_vlm_contrast.py "$MODEL_DIR" ../fixtures-realistas GPU ../contraste.json
```

## Replay local com VLM e Quality Gate

`clipper.bench.replay` roda os mesmos trechos com a descoberta visual `off` e `on`. Usa os handlers de áudio, scoring e visão, inclusive a confirmação do VLM e o Quality Gate. Os dois modos compartilham os limiares e pesos editoriais do JSON importado. O banco SQLite, os segmentos copiados e os arquivos intermediários ficam em diretórios temporários. A comunicação com a API e o servidor de segmentos ocorre dentro do processo. O replay termina na decisão do gate, antes da renderização.

Exemplo de manifesto `annotations.json`. Caminhos relativos partem da pasta do manifesto. Para `video`, os tempos são segundos desde o início do arquivo. Para `segments`, copie `file`, `start` e `duration` do índice de captura para um trecho contínuo da mesma execução e anote no relógio original da sessão. O replay copia esses segmentos byte a byte. Em arquivos de vídeo contínuo, usa a segmentação sem recodificação do capturador.

```json
{
  "videos": [
    {"video": "capotamento.mp4", "game": "GTA V", "language": "pt",
     "events": [{"t": 13.2, "label": "capotamento", "must_include": [10, 17]}]},
    {"segments": [
      {"file": "seg_000012.ts", "start": 120, "duration": 10},
      {"file": "seg_000013.ts", "start": 130, "duration": 10}
     ], "events": [{"t": 129.5, "label": "colisão", "must_include": [125, 134]}]},
    {"video": "direcao-normal.mp4", "events": []}
  ]
}
```

Use trechos curtos, FFmpeg e as dependências de áudio e OpenVINO do backend. Para fonte com áudio, prepare o Whisper no cache local ou passe o caminho do checkpoint em `--whisper`. O VLM vem do diretório de modelos informado.

```bash
PYTHONPATH=backend python -m clipper.bench.replay ./annotations.json \
  --settings ./editorial.json --models-dir "$MODEL_DIR" --device GPU --out ./comparacao.json
```

### Configuração editorial (PT-BR)

Passe em `--settings` um arquivo local com o JSON de `GET /api/settings`, sem credenciais. Use o export completo para reproduzir pesos, limites e os limiares do gate. O arquivo precisa incluir `min_score`. Um valor de `70` é preservado em ambos os modos. Sem o arquivo, o replay usa os padrões locais anteriores. Campos opcionais ausentes em um export usam o padrão do esquema deste checkout.

O relatório identifica a origem em `settings_source`, grava a configuração efetiva em `settings` e lista as mudanças em `settings_differences`, com valor anterior, valor aplicado e motivo. O replay força VLM e Quality Gate ativos, desativa coleta de dataset e edição estrutural e usa `narrative_model=template`. Os modelos do export prevalecem quando `--model` e `--whisper` são omitidos. Um idioma explícito no manifesto prevalece para aquele vídeo e consta nas diferenças do vídeo.

### Editorial configuration (EN)

Pass a local, credential-free `GET /api/settings` JSON file with `--settings`. Use the full export to reproduce weights, limits and gate thresholds. The file must include `min_score`. A value of `70` applies to both modes. Without the file, replay uses its previous local defaults. Optional fields absent from an export take this checkout's schema defaults.

The report records the source in `settings_source`, effective values in `settings`, and the previous value, applied value and reason in `settings_differences`. Replay enables the real VLM and Quality Gate, disables dataset collection and structural editing, and uses `narrative_model=template`. Exported models apply unless overridden by `--model` or `--whisper`. An explicit manifest language takes priority for that video and is listed in its differences.

O JSON traz hipóteses, confirmação direcionada, decisão do gate, cobertura de `must_include`, hashes dos segmentos e contagens TP/FP/FN por etapa e modo. A comparação usa tolerância de 3 s por padrão e associa cada pico a no máximo uma anotação. `gate_ready` conta somente decisões `READY`. Uma rejeição editorial é um resultado válido. Falha de infraestrutura retorna código 2. Quando o worker registra fallback de visão, o JSON preserva a falha em `errors`.
