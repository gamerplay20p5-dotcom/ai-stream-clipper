# Análise visual e GPU Intel Arc

## Provedores

| Provedor | Onde roda | Estado |
|---|---|---|
| `heuristic` (OpenCV) | qualquer CPU | **Validado** nos testes: mudanças de cena, movimento, clarões, detecção de webcam |
| `openvino_vlm` (OpenVINO GenAI `VLMPipeline`) | GPU Intel (201/210) | **Experimental, não validado**: código pronto, falta medir em hardware real |

O `openvino_vlm` roda primeiro a heurística (cena, movimento, webcam) e depois pergunta ao modelo, com até 4 quadros próximos do pico e a transcrição, por uma resposta em JSON (`interest`, `event`, `funny`, `action`). A nota visual final é `0,6 × modelo + 0,4 × heurística`, e a descrição do modelo vira uma explicação no painel.

Jobs com `openvino_vlm` são marcados `requires_gpu`. Só workers que registraram `gpu: true` os recebem. Isso exige o render node presente **e** o OpenVINO listando `GPU`, então o 206 nunca recebe esses jobs.

## Validação obrigatória antes de ativar

```bash
# 1. dispositivo + compilação/inferência FP16/FP32 de um modelo pequeno na GPU
./infrastructure/scripts/validate-gpu.sh

# 2. exportar um VLM leve (em uma máquina com internet; não precisa de GPU)
pip install "optimum-intel[openvino]"
optimum-cli export openvino --model Qwen/Qwen2-VL-2B-Instruct --weight-format int4 /opt/asc/models/vlm

# 3. latência e memória reais no Arc A750 com quadros do vídeo de teste
./infrastructure/scripts/validate-gpu.sh --vlm /opt/asc/models/vlm
```

Candidatos (ordem sugerida de teste, todos suportados pelo `VLMPipeline` segundo a documentação do OpenVINO GenAI; **nenhum foi medido aqui**):

1. `Qwen/Qwen2-VL-2B-Instruct` em INT4. Bom em PT-BR, cabe com folga em 8 GB.
2. `OpenGVLab/InternVL2-1B` em INT4. O mais leve.
3. `microsoft/Phi-3.5-vision-instruct` em INT4. Maior, testar só se os anteriores forem fracos.

Registre o resultado de `vlm_bench` (latência por chamada, RSS, resposta) nesta página. Critério sugerido para ativar no painel (Configurações da IA → Provedor): latência abaixo de 10 s por corte e JSON válido em pelo menos 9 de 10 chamadas.

## Resultados medidos

_Nenhum ainda: preencher após rodar os scripts acima nos servidores 201 e 210._
