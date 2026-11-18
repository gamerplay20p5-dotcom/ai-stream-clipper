# Fase 2: inteligência multimodal e edição inteligente

Versão 0.2.0. Baseline da Fase 1 preservado na tag `v0.1.0-mvp`.

Nesta página, cada item está marcado com uma de três situações:

- **Implementado:** o código existe e tem testes automatizados.
- **Validado localmente:** foi executado de verdade nesta máquina de desenvolvimento (i5-12450H, 12 threads, 16 GB, Intel UHD integrada, OpenVINO 2026.2.0, Windows 11).
- **Pendente em produção:** só pode ser medido nos servidores 201/206/210 (Arc A750, Linux, Docker).

## O que mudou

```
segmento → áudio: loudness/centróide/fluxo + YAMNet (classes) + tom de voz + Whisper
         → pontuação (Fase 1) + regra "volume não é emoção"
         → candidato → worker de IA:
              visão heurística (sempre): cenas, centróide do movimento, trajetória do rosto
              modelo multimodal (Qwen3-VL-2B INT4, OpenVINO): JSON validado com timestamps
              diretor: evidências → nota final, início/fim, manter ou descartar
              narrativa (Qwen3-1.7B INT4): justificativa e títulos a partir dos fatos
              plano de edição estruturado
         → renderizador FFmpeg executa o plano → painel
sem worker de IA / modelo indisponível → diretor roda na API só com áudio (nunca trava)
```

| Componente | Arquivo | Situação |
|---|---|---|
| Contratos validados (análise visual, decisão, plano) | `backend/clipper/ai/schemas.py` | implementado |
| Gerenciador de modelos: um modelo pesado por vez, GPU→CPU, estados, latência | `ai/manager.py`, `ai/registry.py` | implementado; **validado** em CPU e na iGPU |
| Análise multimodal (Qwen3-VL-2B INT4) | `ai/vlm.py` | **validado localmente** (CPU e iGPU); Arc pendente |
| Classes acústicas (YAMNet ONNX) e tom de voz | `ai/acoustic.py` | **validado localmente** |
| Diretor automático | `ai/director.py` | implementado + benchmark |
| Narrativa (Qwen3-1.7B / 0.6B INT4) | `ai/narrative.py` | **validado localmente** |
| Plano de edição + templates | `ai/edit_plan.py` | implementado |
| Renderizador que executa o plano | `render/renderer.py` | **validado localmente** |
| Seleção de worker (modelo, VRAM, latência) | `routing.py` | implementado (teste de unidade); 201+210 juntos pendente |
| Fluxo no pipeline / fallback na API / reanálise | `pipeline_ai.py` | implementado + **sistema completo validado** |
| Benchmarks | `bench/pipeline_bench.py`, `analysis/vlm_bench.py` | executados localmente; prontos para 201/210 |
| Painel: análise da IA, modelos, reanálise, configurações | `dashboard/src/...` | implementado e conferido visualmente |

## Decisões e por quê

- **Qwen3-VL-2B INT4** como modelo visual inicial. A Intel não publica conversão oficial do 2B, então usamos a exportação OpenVINO de `helenai` (engenheira da equipe OpenVINO), com tokenizer convertido localmente. O **Qwen3-VL-4B INT4 oficial** (3,1 GB) fica como opção. O **Qwen2.5-VL-3B não tem conversão OpenVINO publicada** (só existe a de 7B).
- **Um modelo pesado por vez** por GPU (8 GB cada, sem compartilhar memória). A narrativa pode reutilizar o VLM carregado (`same_as_vision`) para não alternar modelos.
- **Ceticismo com o modelo visual**, a partir do que foi medido:
  - a nota "important" é ignorada, porque o 2B deu 10 até para tela de teste;
  - começo e fim respondidos na borda da janela são ignorados;
  - o arco visual só é usado com confiança de 0,6 ou mais;
  - a visão só **aumenta** a nota se houver corroboração (evento perto do pico, movimento medido ou reação no áudio);
  - o ajuste visual fica limitado a ±15 pontos;
  - a visão pode mover as bordas no máximo 15 s além do candidato.
- **Volume não é emoção.** Com dados acústicos disponíveis, um pico só conta como reação se houver palavra de reação, risada ou grito classificado.
- **O texto não substitui a visão.** O modelo narrativo só recebe fatos já extraídos (fala, resumo visual, evidências) e é proibido de inventar.
- **Contexto narrativo determinístico.** O início de uma situação é achado por um léxico de tensão (polícia, "atrás de nós", "vai vai vai"…). Esse léxico é separado do léxico de reação, para não criar cortes sozinho, e só é aceito com sinal a até 12 s do pico e fala contínua.
- **Normalização de áudio sem distorção.** Mede EBU R128 e aplica ganho linear limitado a −1 dBTP, sem compressor nem limitador.

## Resultados medidos (vídeos anotados em `tests/fixtures/annotations.json`)

Vídeos sintéticos com eventos conhecidos:

- `gameplay_event`: silêncio → grito → reação.
- `chase_crash`: perseguição longa → batida → reação → desfecho.
- `loud_game_noise`: explosões e tiros altos com o streamer calmo, que **não** deve virar corte.

Whisper tiny, CPU. Resultados em `docs/benchmarks/`.

| Métrica | Fase 1 | Fase 2 (sem VLM) | Fase 2 (com Qwen3-VL-2B, CPU) |
|---|---|---|---|
| Acontecimentos encontrados | 2/2 | 2/2 | 2/2 |
| Falsos positivos (barulho do jogo) | **2** | **0** | **0** |
| Cobertura do trecho obrigatório na perseguição | 68% | **100%** | **100%** |
| Erro no início do contexto da perseguição | 9,6 s | **0,15 s** | **0,15 s** |
| Erro no início do contexto, vídeo do grito | 4,33 s | 4,33 s | 4,33 s |

Resultados iguais com sensibilidade 0,5 e 0,85.

Tempos medidos por etapa (i5-12450H):

| Etapa | Tempo |
|---|---|
| Transcrição Whisper tiny (≈ 45 s de áudio) | 2,7 – 4,5 s |
| YAMNet (≈ 45 s de áudio) | 0,16 – 0,27 s |
| Visão heurística por candidato (≈ 45 quadros) | 3 – 4,5 s |
| Qwen3-VL-2B, 8 quadros 448 px, **CPU** | 37 – 78 s por corte (RAM do processo ≈ 4,8 – 5,2 GB) |
| Qwen3-VL-2B, **iGPU Intel UHD** | compilação + carga 44 s (uma vez); 42 – 64 s por corte; **VRAM 2,39 GB** |
| Qwen3-1.7B narrativa, CPU | 7,6 – 18 s |
| Diretor + plano | < 0,01 s |
| Render 1080×1920 (25 – 40 s de corte, preset veryfast, plano completo) | 13 – 24 s |
| **Sistema completo** (API + worker, perseguição, CPU) | **130 s** do cadastro do vídeo ao Short pronto |

## O que os testes mostraram (e foi corrigido)

1. O VLM esgotava o limite de tokens com resumos longos e devolvia JSON incompleto. Corrigido com limite de palavras e mais tokens; resposta sem os campos essenciais pede nova tentativa e, se falhar, vira fallback.
2. O detokenizer partia acentos ("vis�­veis"). Corrigido com reparo de UTF-8 em todo texto vindo de modelo.
3. O VLM respondia "começo = primeiro quadro, fim = último quadro" e o diretor esticava o corte para 40 s. Corrigido com o ceticismo descrito acima, e há teste fixando isso.
4. **Condição de corrida, que já existia na Fase 1:** dois jobs de pontuação em paralelo criavam o mesmo corte duas vezes. Corrigido com troca atômica do `scored_until`, e há teste.
5. Na Arc, o cache de compilação seria gravado na pasta do modelo, que é montada somente leitura. Movido para o diretório de trabalho.

## Limitações conhecidas

- **Os vídeos de teste são sintéticos** (padrão de teste do FFmpeg + voz TTS). Eles validam a mecânica, os timestamps e os falsos positivos de volume. **Não avaliam a compreensão visual**: o VLM descreve corretamente "tela de teste de cores", e o YAMNet não reconhece minhas explosões sintéticas como explosão. Para medir a qualidade visual é preciso anotar 10 a 20 trechos reais de lives de GTA RP no mesmo formato de `annotations.json` e rodar `pipeline_bench` e `vlm_bench`.
- **As notas numéricas do Qwen3-VL-2B não são calibradas.** Por isso pesam pouco e exigem corroboração.
- O contexto narrativo por léxico só cobre situações com vocabulário de tensão (polícia, perseguição, tiroteio…). O localizador de contexto por modelo de texto existe como gancho (`context_hint` no diretor), mas não está ligado a um modelo nesta versão.
- A análise de chat continua não implementada (critério "chat" = n/d).
- O rastreamento de rosto usa Haar (OpenCV 4). Rosto de perfil ou webcam muito pequena podem falhar; o enquadramento pode ser forçado por streamer ou no editor.

## Situação em produção (v0.2.9)

| Item | Situação |
|---|---|
| Latência, VRAM e estabilidade do Qwen3-VL-2B na **Arc A750** (201 e 210) | **Validado**: média de 9,4 s (210) e 11,1 s (201), VRAM 2,42 GB, 0 falhas em 20 chamadas. Ver [VISAO_GPU.md](VISAO_GPU.md) |
| Qwen3-VL-4B INT4 cabe e compensa na A750? | Não testado. O 2B já atende à latência; o 4B fica como opção se a qualidade em lives reais for insuficiente |
| Distribuição entre 201 e 210 com carga real | **Validado**: 3 análises simultâneas foram divididas entre as duas GPUs, sem fallback |
| Whisper `small` no 206 acompanha a captura em tempo real | **Validado** na live: 549 análises de áudio e 544 pontuações concluídas, com a fila em dia |
| Imagens Docker da Fase 2 | **Em produção** (`ai-stream-clipper` e `ai-stream-clipper-vision`, com tags por versão para rollback) |
| Qualidade com lives reais de GTA | Parcial: cortes reais com resumos coerentes ("Jogador sai de uma área de floresta…"). Falta anotar trechos reais para medir |
