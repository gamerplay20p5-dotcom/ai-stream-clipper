# Incidente: monitoramento de lives parado (produção v0.6.2, 2026-11-19)

## 1. Evidências (somente leitura, via API pública do painel em 2026-11-19 ~20:13 UTC)

O SSH para 201, 206 e 210 deu *timeout* a partir desta máquina. A API pública respondeu, então todas as leituras abaixo vieram dela, só com GET.

| Fonte | Leitura |
|---|---|
| `GET /healthz` | `{"status": "ok", "version": "0.6.2"}` |
| `GET /api/streamers` | Os 10 canais habilitados (Twitch e YouTube) tinham a última verificação há **9,8 h**, todos no mesmo instante. Nenhum tinha `last_error`. 3 apareciam como `live` e 7 como `offline`, todos com dados de 9,8 h atrás. |
| `GET /api/infrastructure` | O worker **`206-capture`** (papéis `monitor` e `capture`) estava **offline, com o último heartbeat há 9,8 h**. Os outros workers do 206 (`206-audio`, `206-render`) e os do 201 e 210 estavam online, com heartbeat recente. |
| Falhas recentes | Job `capture` 17633 com "lease expired (worker 206-capture lost)", e `vision_analyze` com "segment server unreachable" (o servidor de segmentos roda dentro do worker de captura). |
| `GET /api/diagnostics` | Fila de `capture` com **2 jobs `queued` sem nenhum worker para pegá-los**. |

## 2. Causa raiz: o que está provado e o que falta provar

**Provado:**
- A parada não é de uma plataforma. Twitch e YouTube pararam juntos, no mesmo segundo.
- Não é a API nem a rede do 206 até o 210, porque os outros workers do 206 seguem com heartbeat.
- O **processo do worker de captura inteiro** parou de responder: heartbeat, busca de jobs, renovação de *lease*, servidor de segmentos e monitor. Quem viu o incidente como "monitor parado" estava vendo o sintoma mais visível de um worker parado.

**Linha do tempo exata** ([evidências](incidentes/2026-11-19-monitoramento-evidencias.md)): os 10 canais foram verificados entre 10:22:58 e 10:23:51 UTC. O último heartbeat foi às 10:23:50, e o seguinte, esperado por volta de 10:24:00, não chegou. A última rodada **terminou**, então o processo parou de uma vez logo depois dela.

**A hipótese inicial não explica sozinha o que se observou.** No `monitor_loop` da v0.6.2, `asyncio.gather` sobre `asyncio.to_thread(probe_live)` congela apenas a tarefa do monitor quando uma probe trava. O heartbeat é outra tarefa do mesmo event loop e continuaria. Como o heartbeat também parou, aconteceu uma de duas coisas:

1. **o event loop do processo travou** (alguma chamada síncrona bloqueante dentro do loop, ou um travamento nativo do interpretador). O contêiner continua "de pé" e o Docker não reinicia contêiner *unhealthy*; ou
2. **o processo/contêiner parou e não voltou** (por exemplo, encerrado pelo sistema sem a política de reinício agir).

**Sem acesso ao 206 não dá para distinguir 1 de 2.** Comandos somente leitura para o operador rodar no 206, nesta ordem:

```
sudo -n asc-ctl status                          # nome, imagem e estado (Up/unhealthy/Restarting/Exited) dos contêineres
sudo -n asc-ctl compose ps worker-capture       # estado do serviço asc-worker-capture
sudo -n asc-ctl compose logs worker-capture     # últimas linhas antes de parar
```

Se o contêiner estiver em execução mas *unhealthy*, o caso é o 1. Se os logs terminarem de repente, sem "worker shutting down", reforça o caso 1. Se o contêiner estiver parado ou reiniciando, é o caso 2.

## 3. Correção (branch `hotfix/monitoramento-v0.6.2`, a partir da tag `v0.6.2`)

| Requisito | Implementação |
|---|---|
| Verificações independentes, sem esperar a rodada | `workers/monitor.py`: uma tarefa por canal. O loop de consulta nunca aguarda as probes, e cada canal tem no máximo uma probe em andamento. |
| Timeout real | `workers/probe.py`: a probe roda em **processo filho**, encerrado com `kill` no prazo (`ASC_MONITOR_PROBE_TIMEOUT_S`, 45 s). Não sobram threads nem processos presos (testado). |
| Concorrência configurável | `ASC_MONITOR_CONCURRENCY` (4) |
| Rede, DNS, Streamlink e plataformas | Exceções e travamentos viram erro reportado **por canal**. A falha de um canal não afeta os outros. |
| Supervisão e recuperação | `run_supervised` reinicia o loop se ele cair ou ficar `ASC_MONITOR_STALL_S` (600 s) sem tentar consultar, cancelando e encerrando as probes em andamento. As capturas não são tocadas. |
| Worker inteiro travado (o caso observado) | `LoopWatchdog` em uma thread comum, fora do event loop. Ele só passa a contar depois do primeiro tique de uma tarefa do loop, que roda inclusive durante o registro. Por isso a API fora do ar, mesmo por muito tempo, não é confundida com travamento. Se o loop ficar `ASC_LOOP_STALL_EXIT_S` (300 s) sem rodar, ele grava a pilha de todas as threads no stderr e encerra o processo com código 70. O `restart: unless-stopped` sobe um worker limpo, e os jobs voltam pela expiração do *lease*, como depois de um crash. `kill -USR1` grava a pilha sem parar o worker. |
| Métricas | O heartbeat passa a levar `monitor`: última consulta, última consulta com sucesso, último relatório, probes em andamento, timeouts, falhas e reinícios. |
| Atraso visível mesmo com heartbeat saudável | `GET /api/streamers` ganha `monitor.state` (`live`, `offline`, `checking`, `stale`, `error`, `paused`) e a idade da última verificação. A tela de Streamers mostra "Monitoramento atrasado" com o texto pedido e um alerta no topo. O último resultado conhecido aparece separado. |
| "Verificar agora" com retorno real | Mostra "solicitada, aguardando o monitor", "concluída: …" (só quando o monitor de fato reportou), "falhou" ou "solicitada há N s e ainda não executada". |
| Captura | A checagem de "ainda ao vivo?" dentro da captura também usa a probe isolada. |
| Logs | Mensagens estruturadas com `streamer_id`, plataforma, erro resumido e tempo. Sem URLs com token e sem credenciais. |

Não há migração nem mudança de esquema. O contrato da API só **ganha** campos.

## 4. Testes (`tests/test_monitor_hotfix.py`, 22, e `dashboard/scripts/check_streamers_ui.py`, 5 cenários)

- **Probes e processos:**
  - probe bloqueada para sempre é encerrada no prazo;
  - processo que cai vira erro, não espera;
  - DNS indisponível;
  - 8 timeouts seguidos sem sobrar processo ou thread.
- **Isolamento entre canais:**
  - 5 canais com 1 travado: os outros 4 são reportados de imediato;
  - falha parcial da Twitch e do YouTube;
  - uma probe por canal.
- **API e supervisão:**
  - API interna fora do ar e depois de volta, com o relatório refeito;
  - supervisor reinicia um monitor sem progresso e **preserva a captura ativa**;
  - monitor com erro continua consultando;
  - event loop travado leva a uma saída controlada (código 70, uma vez só).
- **Watchdog no runtime real:**
  - registro indisponível por mais de 3 vezes o limite **não** encerra o processo (o teste falha com a ordem antiga);
  - um bloqueio real do event loop é detectado (código 70);
  - o watchdog não conta antes do primeiro tique nem durante o desligamento.
- **"Verificar agora":**
  - a API devolve o horário em que aceitou o pedido;
  - na tela (com a API simulada no navegador), o pedido recusado mostra a falha;
  - um pedido aceito e não executado, ou um resultado anterior ao aceite, **nunca** aparece como concluído;
  - um resultado registrado depois do aceite conclui o pedido.
- **Painel e recuperação:**
  - estado antigo sinalizado como atrasado, mesmo se o último resultado era "ao vivo";
  - solicitação manual não processada fica visível;
  - API expõe `monitor` e `check_requested`;
  - relatórios repetidos depois da recuperação não duplicam sessão nem job de captura.

Suíte completa: 314 passaram e 3 foram pulados (modelos reais de IA ausentes nesta máquina). O e2e real (`pytest -m e2e tests/test_e2e.py`: API e worker com monitor e captura de verdade, live detectada, corte gerado) passou.

## 5. Riscos da recuperação

- **Reinício do worker de captura:** as capturas em andamento são interrompidas e retomadas pelo *lease*, com um novo `run_id` e uma lacuna de alguns segundos a minutos. Numa live, isso já acontecia em qualquer reinício.
- **Watchdog que dispara à toa:** 300 s é muito mais que qualquer operação legítima dentro do loop. Se disparar, a pilha gravada no log mostra a causa. Para desligar, basta `ASC_LOOP_STALL_EXIT_S=0`.
- **Limite de 3 GB do contêiner de captura:** um OOM dentro do contêiner pode derrubar o processo; o watchdog não muda isso. Vale conferir `asc-ctl status` (código de saída 137) durante o diagnóstico.
- **Probes em processo:** cada probe custa um processo Python (~1 s de CPU para importar o Streamlink). Com 10 canais a cada 60 s e concorrência 4, a carga é pequena.
- **Recuperação atual:** os 2 jobs de captura parados na fila e as sessões marcadas como "ao vivo" são retomados quando o worker voltar. O relatório "ao vivo" repetido não cria sessão duplicada (testado).

## 6. Procedimento seguro para produção (só com autorização)

**Pré-condição:** acesso pelos caminhos autorizados (SSH com a chave `asc` e `sudo -n asc-ctl`). **Se o SSH continuar indisponível, nada é feito remotamente por caminhos improvisados** (painel, túnel, API ou outra máquina). O procedimento espera o acesso voltar.

### 6.1 Diagnóstico somente leitura no 206 (antes de qualquer reinício)

```
sudo -n asc-ctl status                       # asc-worker-capture: Up / Up (unhealthy) / Restarting / Exited (código)
sudo -n asc-ctl compose ps worker-capture    # estado e saúde do serviço
sudo -n asc-ctl compose logs worker-capture  # últimas linhas: o que aconteceu antes de parar
```

Guardar a saída: é a evidência da causa original. Como interpretar:
- `Up (unhealthy)` com logs que param de repente → event loop travado;
- `Exited (137)` → falta de memória (limite de 3 GB);
- `Restarting` → falha ao iniciar.

### 6.2 O que precisa ser atualizado (e o que não)

| Servidor | Serviço | Atualizar? | Motivo |
|---|---|---|---|
| 206 | `worker-capture` | **sim** | monitor novo, watchdog e probe isolada |
| 210 | `api` | **sim** | `monitor.state` em `/api/streamers` e a tela de Streamers (o painel vem na imagem da API) |
| 206 | `worker-audio`, `worker-render` | não | código sem mudança relevante |
| 210 | `worker-scoring`, `worker-publisher`, `worker-vision`, `postgres`, `tunnel` | não | o publisher não é recriado: as publicações agendadas seguem com o processo atual |
| 201 | `worker-vision` | não | sem mudança |

**Cuidado com o `set-tag`.** `asc-ctl set-tag` (no 210, `env-install` do `asc.env`) grava **uma** `ASC_IMAGE_TAG` usada por **todos** os serviços do servidor, inclusive `ai-stream-clipper-vision` no 210. Por isso:
- o `compose up` é sempre feito **com o nome do serviço**: `asc-ctl compose up worker-capture` recria só aquele contêiner. Os outros continuam com a imagem 0.6.2 até um `compose up` futuro;
- **as duas imagens com a tag nova são geradas antes** (`app` e `vision`), mesmo sem iniciar a visão agora. Assim, um `compose up` completo acidental ou futuro não falha por imagem ausente (o `up` usa `--no-build`);
- **não** se usa `compose up` sem nomes de serviço nesta implantação.

### 6.3 Passos

1. **Código e imagens.** O pacote do código é a tag do hotfix, gerada com `git archive`, como nos deploys anteriores. No 206:
   - `asc-ctl build app 0.6.2-hotfix.1` e `asc-ctl build vision 0.6.2-hotfix.1`;
   - `image-save` dos dois e `asc-send` para o 210 (e, se quiser manter a tag consistente, a de visão também para o 201);
   - no 210, `image-load` dos dois.
2. **Conferir as imagens antes de iniciar qualquer serviço.** `asc-ctl images` no 206 e no 210 deve listar `ai-stream-clipper:0.6.2-hotfix.1` e `ai-stream-clipper-vision:0.6.2-hotfix.1`. Se faltar alguma, parar aqui.
3. **206, primeiro, porque restaura a detecção:**
   - `asc-ctl set-tag 0.6.2-hotfix.1`;
   - `asc-ctl compose up worker-capture`.

   Conferir:
   - `compose ps worker-capture` com `healthy`;
   - no painel, `206-capture` online;
   - em `GET /api/streamers` (API ainda 0.6.2), `last_checked_at` avançando em todos os canais habilitados em até 2 minutos.
4. **210:**
   - escolher um momento **sem publicação agendada nos 15 minutos seguintes** (ver a agenda), porque a API fica alguns segundos fora durante a troca;
   - atualizar a tag no `asc.env` e rodar `asc-ctl env-install`;
   - `asc-ctl compose up api`. O Postgres não é recriado: a configuração dele não muda e ele só é conferido como dependência.

   A API roda `alembic upgrade head` ao iniciar; **não há migração** (fica em 0010). Conferir:
   - `/healthz` com versão `0.6.2-hotfix.1`;
   - `monitor.state` presente;
   - a tela de Streamers sem "atrasado" para os canais recém-verificados;
   - as publicações agendadas iguais às de antes (comparar id, horário, privacidade e conta).
5. **Acompanhar por 30 minutos:**
   - métricas `monitor.last_report_ok_at` avançando no heartbeat do `206-capture`;
   - nenhum `stalled`;
   - "Verificar agora" concluindo.

### 6.4 Rollback (por serviço, sem migração)

- **206:** `asc-ctl set-tag 0.6.2` e `asc-ctl compose up worker-capture`.
- **210:** tag 0.6.2 no `asc.env`, `env-install` e `asc-ctl compose up api`.

A API 0.6.2 ignora os campos novos, e o worker 0.6.2 funciona com a API do hotfix (os contratos internos não mudaram).

Nenhuma publicação, agendamento, OAuth ou serviço do Hank é alterado. Nenhum comando global do Docker é usado.

## EN summary

On 2026-11-19 the production capture worker (roles monitor and capture) stopped heartbeating 9.8 h before diagnosis. Every channel check stopped at that same instant, and the other workers were healthy. The whole worker process froze or died, which is more than a stuck probe could cause. Without SSH it is not yet proven whether the event loop blocked or the container stopped; the read-only commands to settle it are in section 2.

The hotfix makes this failure self-healing and visible:
- per-channel probes run in killable child processes with hard deadlines, and the poll loop never waits for them;
- the monitor loop is supervised;
- a thread watchdog dumps stacks and exits for a controlled restart when the event loop blocks;
- monitor progress metrics are added to the heartbeat;
- the panel shows "monitoring delayed" instead of stale results, and "Check now" gives truthful feedback.

There is no migration. 18 new tests pass, and the full suite passes (314 passed, 3 skipped).
