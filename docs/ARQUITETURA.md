# Arquitetura

## Distribuição por servidor

```
                        rede privada 192.168.1.0/24 (HTTP assinado com HMAC)
 ┌──────────────── 210 ────────────────┐      ┌──────────────── 206 ─────────────────┐
 │ asc-api  :8780 (IP privado)         │◄────►│ asc-worker-capture  (monitor,capture) │
 │   API Gateway + painel + scheduler  │      │   servidor de segmentos :8781         │
 │ asc-postgres (sem porta publicada)  │      │ asc-worker-audio    (VAD + Whisper)   │
 │ asc-worker-scoring                  │      │ asc-worker-render   (FFmpeg)          │
 │ asc-worker-vision (GPU, opcional)   │      └───────────────────────────────────────┘
 └─────────────────────────────────────┘                       ▲
                ▲                                              │ trechos sob demanda
                │ jobs / resultados          ┌──────── 201 ────┴────┐
                └────────────────────────────│ asc-worker-vision     │
                                             │   GPU Intel Arc A750  │
                                             └───────────────────────┘
```

* **210**: única máquina que fala com o banco. Expõe a API (painel + `/internal/*`) apenas no IP privado.
* **206**: tudo que é CPU contínuo. Os segmentos ficam no disco do 206; outros workers pedem só o trecho necessário (`/extract?start&end`), com SHA-256 verificado.
* **201**: worker de visão com limite rígido de 4 CPUs / 8 GiB e peso de CPU baixo, para não competir com os servidores de jogo.

Nenhum worker acessa o banco ou o sistema de arquivos de outra máquina.

## Fluxo de dados

1. **Monitor** (206) consulta `/internal/monitor/targets`, verifica a live com streamlink e reporta. Ao detectar live, a API cria a sessão e o job `capture` na mesma transação.
2. **Captura** (206): `streamlink --stdout | ffmpeg -f segment -c copy` gera segmentos MPEG-TS de 10 s. Os tempos vêm da lista CSV do próprio FFmpeg. Cada segmento é registrado com hash, e a API cria um job `audio_analyze` por segmento. Há reconexão com backoff, limite de duração por sessão, buffer circular (padrão 30 min) e um teto de disco. Segmentos ainda necessários (análise pendente, ou corte cuja fonte ainda não foi salva) só são apagados com o teto de disco atingido.
3. **Áudio** (206): extrai PCM 16 kHz, calcula loudness, centróide espectral e fluxo a cada 0,25 s, e transcreve com faster-whisper (Silero VAD embutido) com timestamps por palavra.
4. **Pontuação** (qualquer CPU): quando a análise contígua passa do horizonte de contexto, o motor:
   * detecta eventos: pico de volume (z-score robusto contra a mediana dos últimos 90 s), grito, som estridente, silêncio seguido de evento, rajada de energia, palavras-chave e risadas (léxico PT/EN), fala acelerada;
   * agrupa os eventos em momentos;
   * define as bordas do corte: contexto antes, desfecho depois (espera o áudio voltar ao normal), ajuste a pausas ou fins de frase, sem cortar palavras ao meio;
   * pontua 10 critérios (intensidade, surpresa, humor, reação, clareza narrativa, contexto, singularidade, interesse do público, visual, chat), com pesos configuráveis e explicações curtas;
   * deduplica por sobreposição e por similaridade de transcrição, e limita a quantidade de cortes por hora.

   A gravação usa controle otimista (`scored_until`), então dois avaliadores nunca duplicam cortes.
5. **Visão** (201/210, opcional): amostragem adaptativa (3× mais densa perto do pico), mudanças de cena, movimento, clarões e localização da webcam (rosto fixo em todos os quadros). O resultado entra no critério visual e recalcula a nota. Sem worker visual dentro do prazo, o corte segue sem visão.
6. **Renderização** (206): busca a fonte uma vez (e guarda uma cópia na API, para que reedições não dependam do buffer) e gera o vídeo vertical em um de três layouts (gameplay inteira sobre fundo desfocado, gameplay ampliada, webcam em cima), com legendas ASS palavra por palavra e título. Depois envia o MP4 e a miniatura com hash verificado.
7. **Painel**: revisar, aprovar, rejeitar, editar (título, tempos, legenda, enquadramento), renderizar de novo, baixar e excluir.

## Decisões arquiteturais

| Decisão | Motivo |
|---|---|
| Um pacote Python, vários papéis (`clipper api`, `clipper worker --roles …`) | Uma imagem só, código compartilhado sem duplicação, papéis combináveis por máquina |
| Fila durável no PostgreSQL (`FOR UPDATE SKIP LOCKED` + leases + estado `dead`) em vez de Redis | Resultados e próximos jobs gravados na mesma transação: nenhum estado intermediário se perde em reinícios; um serviço a menos nos servidores compartilhados |
| Workers só falam HTTP com HMAC (método + caminho + timestamp + SHA-256 do corpo) | O segredo nunca trafega, há proteção contra replay (60 s) e o banco nunca fica exposto a 201/206 |
| Uploads como corpo bruto com SHA-256 assinado | A integridade do arquivo faz parte da autenticação |
| Segmentos ficam na máquina de captura e são servidos por trecho | Nada de copiar a live inteira entre servidores |
| Fonte do corte copiada para a API ao primeiro uso | Reedições continuam possíveis depois que o buffer circular gira |
| Pontuação heurística e explicada | Não há dados rotulados ainda; as notas não são probabilidade de viralizar |
| Provedores trocáveis (`Transcriber`, `VisionProvider`) | Trocar modelo sem reescrever o pipeline |
| SQLite apenas para testes/dev | Testes reproduzíveis sem serviços externos; produção usa PostgreSQL |

## Segurança

* URLs aceitas apenas de `twitch.tv` / `youtube.com` / `youtu.be`: sem IP literal, porta, credenciais ou VOD, e com verificação de DNS contra endereços privados (SSRF). Fontes `file://` são só para administradores, desligadas por padrão e confinadas a um diretório.
* Comandos externos sempre como lista de argumentos (sem shell).
* Painel: cookie `HttpOnly` + `SameSite=Strict` com JWT, cabeçalho anti-CSRF obrigatório em mutações, limite de tentativas de login, Argon2 para senhas, CSP restritiva e log de auditoria de ações administrativas.
* Contêineres: usuário sem privilégios (uid 10001), `read_only`, `cap_drop: ALL`, `no-new-privileges`, apenas `/dev/dri/renderD128` nos workers de GPU, sem socket do Docker, rotação de logs.
* Retenção configurável no painel (rejeitados, não revisados, aprovados) e limite total de armazenamento.

## Observabilidade

* Logs JSON estruturados em todos os serviços (`job_id`, `job_type`, `session_id`, `worker`).
* `/healthz`, `/readyz` e `/metrics` (formato Prometheus, protegível por token).
* Heartbeat de workers com CPU, memória, disco e frequência da GPU Intel; a página Infraestrutura mostra filas, tempos médios e falhas definitivas com botão de nova tentativa.

## Fase 2: camada de IA

```
audio worker (206) ── PCM → loudness/centróide/fluxo + YAMNet (ONNX) + tom de voz + Whisper
                                 │
scoring ─ candidatos (regra: pico só vira reação com corroboração) ─┐
                                                                    ▼
AI worker (210 principal, 201 secundário): visão heurística → VLM (Qwen3-VL-2B INT4, OpenVINO, GPU→CPU)
         → diretor (evidências, bordas, manter/descartar) → narrativa (Qwen3-1.7B) → plano de edição
                                                                    │  POST /internal/clips/{id}/analysis
API: aplica a decisão, guarda análise/plano/metadados; sem worker de IA → diretor "audio_only" na própria API
render worker (206): executa o plano (layout action/split/gameplay, zoom ≤ 1,35x, fades, loudness linear)
```

| Decisão | Motivo |
|---|---|
| Contratos Pydantic entre modelo, worker, API e renderizador | Resposta de modelo que não valida é descartada, nunca "consertada" com chute |
| `ModelManager` com um modelo pesado residente | A Arc A750 tem cerca de 8 GB por placa, sem memória compartilhada entre servidores |
| Fallback GPU→CPU e "modelo indisponível" com motivo | O pipeline nunca trava por causa de um modelo; o painel mostra o fallback |
| Roteamento por modelo/VRAM/latência (`routing.py`) | 201 e 210 dividem o trabalho sem duplicar modelos; o mais rápido tem prioridade |
| Plano de edição guardado no corte, com tempos relativos | Reeditar (tempo, legenda, enquadramento) não repete a análise |
| Diretor puro em Python | Roda no worker e também na API como fallback, com o mesmo comportamento |
