# Etapa E: validação de locutores e legendas (2026-11-18)

Roteiro: `etapa_e_validacao.py <pasta>`. A pasta precisa ter `meta.json` e os MP4 dos cortes, baixados do painel.

| Medida | Valor |
|---|---|
| Amostra | 12 cortes renderizados: Alanzoka (lives 5 e 8) e bistecone (live 6); 386,9 s de áudio |
| Whisper small int8 (4 threads) | 210,9 s |
| Impressão de voz (2 threads) | 15,7 s, cerca de 407 ms por 10 s de áudio |
| Falas | 39 (Alanzoka) e 27 (bistecone) |
| Fala na voz dominante | 74% e 68% |
| Fala como "não identificado" | 9,6 s e 7,9 s |
| Cosseno entre os perfis dos dois streamers | 0,286 |
| Falas de bistecone rotuladas como Alanzoka | 0 de 27 |
| Falas de Alanzoka rotuladas como bistecone | 0 de 39 |
| Legendas: eventos | 615 antes e 514 depois (a chamada final substitui as legendas dos últimos 3 s); 92 eventos de outras vozes |
| Renderização pelo libass | 12 de 12 sem erro |

Ainda não há rótulos humanos de locutor, então a precisão por fala não foi medida.
