# Análise visual e GPU Intel Arc

## Provedores

| Provedor | Onde roda | Estado |
|---|---|---|
| `heuristic` (OpenCV) | qualquer CPU | **Validado** nos testes: mudanças de cena, movimento, clarões, detecção de webcam |
| `openvino_vlm` (OpenVINO GenAI `VLMPipeline`, Qwen3-VL-2B INT4) | GPU Intel (201/210), fallback CPU | **Em produção nas duas Arc A750** (201 e 210), com inferência real em cortes de live |

O worker de IA sempre roda a heurística (cenas, centróide do movimento, rosto). Com `openvino_vlm`, ele também envia ao modelo até 8 quadros: metade concentrada perto do pico e o resto espalhado pela janela, cada um com o seu timestamp. Junto vão a transcrição e os sinais acústicos. A resposta é um JSON validado (`schemas.VisualAnalysis`); eventos com tempos fora dos quadros mostrados são descartados. O diretor usa essa resposta com ceticismo (ver [FASE2.md](FASE2.md)).

**Roteamento.** Jobs de visão pedem um modelo. Só workers que o têm instalado e com VRAM suficiente os recebem, e entre eles o de menor latência medida tem prioridade. Se nenhum worker online tiver o modelo, qualquer worker de visão roda a heurística e o corte segue em frente. O 206 não tem papel de visão.

## Validação obrigatória antes de ativar

```bash
# 1. dispositivo + compilação/inferência FP16/FP32 de um modelo pequeno na GPU
./infrastructure/scripts/validate-gpu.sh

# 2. baixar os modelos para /opt/asc/models
./infrastructure/scripts/download-models.sh 201   # baixa e converte o tokenizer quando preciso

# 3. latência e memória reais no Arc A750 com quadros do vídeo de teste
./infrastructure/scripts/validate-gpu.sh --vlm qwen3-vl-2b-int4
```

Modelos no catálogo (`backend/clipper/ai/registry.py`):

1. `qwen3-vl-2b-int4`: padrão. Exportação OpenVINO comunitária (helenai), tokenizer convertido no download. **Validado em CPU e iGPU.**
2. `qwen3-vl-4b-int4`: exportação oficial da Intel (3,1 GB). Testar na A750 se o 2B for fraco.
3. `internvl2-1b-int4`: oficial, 0,8 GB. Opção leve para CPU.

O Qwen2.5-VL-3B não tem conversão OpenVINO publicada (só a 7B, grande demais para 8 GB com folga).

Registre o resultado de `vlm_bench` (latência por chamada, RSS, resposta) nesta página. Critério sugerido para ativar no painel (Configurações da IA → Provedor): latência abaixo de 10 s por corte e JSON válido em pelo menos 9 de 10 chamadas.

## Resultados medidos

Máquina de desenvolvimento, OpenVINO 2026.2.0, Qwen3-VL-2B INT4, 8 quadros de 448 px. Detalhes em [FASE2.md](FASE2.md) e `docs/benchmarks/`.

| Dispositivo | Carga | Latência por corte | Memória |
|---|---|---|---|
| CPU i5-12450H | 5 s | 37 – 78 s | RSS ≈ 4,8 – 5,2 GB |
| iGPU Intel UHD (mesmo plugin GPU da Arc) | 44 s (compilação, uma vez) | 42 – 64 s | VRAM 2,39 GB |
| **Arc A750, 210** (`vlm_bench`, 10 chamadas) | 13,7 s | média 9,4 s (6,3 – 17,8 s), 0 falhas | VRAM 2,42 GB, RSS ≈ 1,9 GB |
| **Arc A750, 201** (`vlm_bench`, 10 chamadas) | 19,3 s | média 11,1 s (7,4 – 20,7 s), 0 falhas | VRAM 2,42 GB |

A Arc é cerca de 5× mais rápida que a CPU de desenvolvimento (48 s em média). `gpu_probe` (convolução FP16): 7,4 ms no 210 e 4,3 ms no 201. Durante os benchmarks no 201, a carga máxima foi 3,1 (de 36 threads) e os servidores de jogo não foram afetados.

Observação: nos vídeos sintéticos o modelo descreve corretamente "tela de teste de cores". A qualidade visual só pode ser julgada com trechos reais de gameplay anotados.

## Produção (v0.2.9, 2026-11-18)

- **Cortes automáticos da live:** os cortes 5, 6 e 7 (GTA RP real) foram analisados pelo Qwen3-VL-2B na GPU do 210, com VLM entre 5,5 e 10,4 s e de 20 a 28 s no total por corte, sem fallback. Os títulos vieram do Qwen3-1.7B, também na GPU.
- **Concorrência** (`docs/benchmarks/concurrency-prod-arc.json`): 3 análises disparadas ao mesmo tempo foram divididas pela fila entre as duas GPUs (210: 2, 201: 1), todas sem fallback, em 68 s no total. Carga máxima de 0,91 no 210 e 3,38 no 201; RAM +0,8 GB em cada. Cada GPU processa uma análise por vez (`concurrency_vision=1`), porque o `VLMPipeline` não aceita gerações paralelas na mesma instância. A vazão cresce com o número de GPUs, não com paralelismo dentro de uma GPU.
- **CPU como alternativa:** não foi medida nos servidores de propósito, porque ocuparia os núcleos dos servidores de jogo do 201. A referência é a máquina de desenvolvimento (48 s por corte). `ASC_AI_ALLOW_CPU_FALLBACK` fica desligado nos workers de GPU.

### Decodificação restrita (v0.2.7)

No Arc, o Qwen3-VL-2B às vezes entrava em laço dentro do primeiro texto ("o carro… e o carro…"), até o limite de tokens. Foram 6.645 caracteres no `summary`, e o modelo nunca chegou a escrever as notas. Desde a v0.2.7, a primeira geração usa a saída estruturada do OpenVINO GenAI (`StructuredOutputConfig.regex`, em `ANSWER_REGEX` no `vlm.py`). A gramática impõe JSON válido, resumo com até 300 caracteres, evento-chave com até 160, no máximo 4 eventos com rótulos de até 140 caracteres, e notas e instantes numéricos. Se um texto atinge exatamente o limite, a última palavra, que pode ter sido cortada, é descartada (v0.2.9; com rótulos de 80 caracteres apareceu "políça").

A gramática controla só a sintaxe e o tamanho; o conteúdo continua sendo do modelo. O custo é de cerca de 4 s por análise. Como reserva, existem três camadas:
- uma segunda tentativa sem gramática;
- a recuperação sintática de JSON truncado (`salvage_truncated`, que não inventa valores);
- a remoção de frases repetidas (`dedupe_sentences`, v0.2.8).

Se o runtime recusar a gramática, o processo volta à geração livre.
