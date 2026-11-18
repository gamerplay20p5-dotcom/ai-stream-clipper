# Estado do projeto: entrega 1 (fases 1 a 4, base da fase 5)

Data: 2026-11-17. Ambiente de desenvolvimento: Windows 11, Python 3.14, FFmpeg 8, Node 24. Sem Docker e sem acesso SSH aos servidores (porta 22 responde, mas a chave local é recusada).

## O que foi implementado

**Fase 1 (fundação)**
* API FastAPI com modelos e migrações de streamer, sessão, segmento, transcrição, evento, corte, job, worker, auditoria e configurações.
* Autenticação com cookie JWT, CSRF, Argon2 e limite de login; OIDC preparado. Health checks e métricas.

**Fase 2 (captura)**
* URL validada (anti-SSRF) e verificação de live com streamlink.
* Captura `streamlink | ffmpeg` em segmentos de 10 s, com timestamps exatos e buffer circular com remoção negociada.
* Reconexão com backoff, limites de duração e de disco.
* Servidor de segmentos autenticado com extração por trecho e SHA-256.

**Fase 3 (detecção)**
* Atributos acústicos, VAD e transcrição.
* Eventos acústicos e textuais, momentos, pontuação em 10 critérios com explicações, deduplicação e análise visual opcional.

**Fase 4 (produção)**
* Bordas do corte em pausas e fins de frase, legendas ASS palavra por palavra com destaque, três layouts verticais e título.
* MP4 H.264/AAC e miniatura; prévia com range, download, edição e nova renderização versionada.

**Fase 5 (base)**
* Workers por papel com registro de capacidade (GPU), heartbeat, leases, retries com backoff, dead-letter e limites globais de concorrência.
* Compose por servidor com limites de recursos, e scripts de deploy e rollback.

## Testes executados (resultados reais)

`cd backend && python -m pytest -q` → **51 passed** (cerca de 35 s)

| Arquivo | O que cobre |
|---|---|
| `test_platforms_security.py` (25) | URLs válidas e rejeitadas (IP interno, host falso, credenciais, porta, VOD), confinamento de `file://`, Argon2, assinatura HMAC (adulteração, replay, chave errada) |
| `test_queue.py` (7) | idempotência, filtro de GPU, backoff e dead-letter, falha não recuperável, lease expirado (simula crash), limite global, cancelamento |
| `test_scoring.py` (6) | timeline **real** (atributos + palavras do faster-whisper sobre o vídeo de teste): detecta o evento em 22–26 s com contexto antes e depois, sem cortar palavras; deduplicação; conversa calma não vira corte; espera o contexto posterior; legendas |
| `test_api.py` (9) | login, CSRF, CRUD de streamers com auditoria, validação de configurações (`auto_publish` bloqueado), rejeição de requisições internas sem assinatura ou adulteradas, worker sem GPU não recebe job de GPU, monitor idempotente |
| `test_render.py` (3) | os três layouts geram 1080×1920 H.264 yuv420p + AAC, duração correta, miniatura, legendas e título |
| `test_e2e.py` (1) | processos reais de API e worker: vídeo de teste → corte pronto em **15 s** para 41 s de fonte; transcrição contém "no way"; visão concluída; download 1080×1920; prévia com HTTP 206; depois **mata o worker, reinicia a API, edita e renderiza de novo**: estado preservado, versão 2 pronta, nenhum job morto |

Medições adicionais (manuais, nesta máquina):
* Live real do YouTube (canal 24/7 de música): detectada como ao vivo com título, **55 segmentos capturados e analisados**, análise acompanhando a captura, **0 eventos e 0 cortes** (sem falsos positivos em música calma).
* Whisper `tiny` int8, 4 threads: cerca de 0,5 s por segmento de 10 s. O modelo `small` (padrão de produção) não foi medido.
* Painel conferido visualmente (desktop 1440 px e celular 390 px) com dados reais.

## Não validado / limitações conhecidas

1. **PostgreSQL**: todos os testes rodaram em SQLite. O código usa `SKIP LOCKED` e advisory lock só no PostgreSQL, e as migrações são geradas pelo Alembic sem nada específico de dialeto. Primeiro passo no 210: `deploy.sh 210` e depois `docker exec asc-api python -m pytest`. Recomendo também rodar a suíte com `ASC_DATABASE_URL` apontando para o PostgreSQL.
2. **Docker/Compose**: arquivos escritos e revisados, mas nunca executados (sem Docker aqui). Pontos de atenção na primeira execução: pacote `fonts-montserrat` no Debian bookworm, tag da imagem OpenVINO e permissões de `read_only` com o cache do HuggingFace (fica em volume).
3. **Twitch**: mesmo caminho do YouTube (streamlink), mas não foi testado contra uma live real da Twitch.
4. **GPU / VLM**: provedor OpenVINO implementado e não medido. Ver [VISAO_GPU.md](VISAO_GPU.md).
5. **Chat**: o critério "chat" existe no motor (peso configurável), mas a coleta ainda não foi implementada e aparece como "n/d".
6. **Risadas**: detectadas pelo texto (kkkk, haha, risos) e por rajadas de energia; não há classificador acústico dedicado.
7. **Webcam**: detecção por rosto Haar; webcams pequenas ou com o rosto de perfil podem falhar. O enquadramento pode ser fixado por streamer (`webcam_region`) ou escolhido no editor.
8. **Fronteiras de segmento**: cada segmento de 10 s é transcrito isoladamente, então uma palavra cortada na fronteira pode sair errada.
9. **Reconexão**: depois de uma queda, o novo trecho é posicionado pelo relógio de parede, e o buraco fica registrado. Cortes não atravessam trechos de reconexões diferentes.
10. **Idioma**: com `language=pt` forçado, falas em inglês são transcritas/traduzidas para português (comportamento do Whisper). Para canais em outro idioma, defina o idioma no streamer ou use `auto`.
11. **OIDC/Authentik**: só variáveis de configuração; o fluxo de login ainda não existe.

## Próximo passo técnico

1. Implantar no 210 e no 206 com os scripts, rodar os testes contra o PostgreSQL real e acompanhar uma live de GTA RP por algumas horas, calibrando `sensitivity` e `min_score` pelos cortes aprovados e rejeitados.
2. No 201, rodar `validate-gpu.sh` e o benchmark do VLM, e registrar os números em `VISAO_GPU.md`.
3. Fase 6: análise de chat (IRC da Twitch, só leitura), classificador de risada, aprendizado de pesos a partir das aprovações e rejeições, e login via Authentik.

---

## Atualização: Fase 2 (versão 0.2.0)

Veja [FASE2.md](FASE2.md). Resumo:

- **77 testes** (sem modelos) + 3 testes de inferência real (`-m models`), todos aprovados nesta máquina.
- **Modelo multimodal real** (Qwen3-VL-2B INT4) validado em CPU e iGPU Intel, inclusive pelo sistema completo (API + worker). A Arc A750 continua pendente.
- **Benchmark:** falsos positivos de 2 para 0; contexto da perseguição de 68% para 100%; nenhuma regressão no caso da Fase 1.
- Corrigida uma condição de corrida da Fase 1 (cortes duplicados com dois jobs de pontuação em paralelo).
- Os itens pendentes da Fase 1 (PostgreSQL real, Docker, Twitch real) continuam pendentes.

---

## Atualização: produção distribuída (v0.2.9, 2026-11-18)

**Em produção:**
- 210: `asc-postgres`, `asc-api` (HTTPS `https://192.168.1.210:8780`), `asc-worker-scoring` e `asc-worker-vision` (Arc A750).
- 206: captura, áudio (Whisper small e YAMNet) e renderização.
- 201: `asc-worker-vision` (Arc A750).

Todos os contêineres são operados pelo `asc-ctl`, sem grupo docker permanente. Os outros projetos dos três servidores foram conferidos contra a linha de base (201: 6/6, 206: 21/21, 210: 8/8 em execução, sem diferenças).

**Por que a live não gerava cortes (resolvido):** o worker de pontuação nunca chegou a rodar. Primeiro falhou por um volume criado com dono root (corrigido na 0.2.3) e depois ficou parado à espera da transferência da imagem, com 46 jobs `score` na fila. Com a 0.2.3 ele processou a fila e a live passou a gerar cortes automaticamente (cortes 5, 6 e 7 em GTA RP real). Em nenhum momento a nota mínima foi reduzida.

**Novidades de 0.2.4 a 0.2.9:**
- Página **Diagnóstico**, com explicações baseadas nos dados reais e "repetir falhas" idempotente.
- **Corte de teste manual** (selo TESTE MANUAL, fora das métricas, no máximo 2 em processamento).
- Transferência direta entre servidores com chave exclusiva e restrita (`asc-send`/`asc-receive`).
- Fontes `file://` distribuídas.
- VLM com decodificação restrita por gramática e recuperação sintática.

Benchmarks na Arc em [VISAO_GPU.md](VISAO_GPU.md).

**Testes:** 94 aprovados (`-m "not models"`) e 3 com modelos reais. Corte de teste ponta a ponta em produção: 1080×1920 H.264/AAC, prévia com HTTP 206 e download.

**Pendências:**
1. O administrador reinstala o asc-ctl 1.0.1 e reemite a CA (ver IMPLANTACAO.md). Até lá, clientes Python 3.13+ precisam relaxar `VERIFY_X509_STRICT`.
2. Remover `asc` do grupo docker no 201 (`sudo gpasswd -d asc docker`); o asc-ctl já cobre a operação.
3. Anotar trechos reais de GTA RP para medir a qualidade da visão.
4. Twitch real, coleta de chat e OIDC continuam como na lista acima.
