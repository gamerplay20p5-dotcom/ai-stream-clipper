# Análise visual e GPU Intel Arc

## Provedores

| Provedor | Onde roda | Estado |
|---|---|---|
| `heuristic` (OpenCV) | qualquer CPU | **Validado** nos testes: mudanças de cena, movimento, clarões, detecção de webcam |
| `openvino_vlm` (OpenVINO GenAI `VLMPipeline`, Qwen3-VL-2B INT4) | GPU Intel (201/210), fallback CPU | **Validado localmente** (CPU + iGPU Intel). Arc A750 pendente |

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
| **Arc A750 (201 / 210)** | _pendente_ | _pendente_ | _pendente_ |

Observação: nos vídeos sintéticos o modelo descreve corretamente "tela de teste de cores". A qualidade visual só pode ser julgada com trechos reais de gameplay anotados.
