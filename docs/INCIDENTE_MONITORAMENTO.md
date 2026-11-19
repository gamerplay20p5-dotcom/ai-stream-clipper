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
| Worker inteiro travado (o caso observado) | `LoopWatchdog` em uma thread comum, fora do event loop. Se o loop ficar `ASC_LOOP_STALL_EXIT_S` (300 s) sem rodar, ele grava a pilha de todas as threads no stderr e encerra o processo com código 70. O `restart: unless-stopped` sobe um worker limpo, e os jobs voltam pela expiração do *lease*, como depois de um crash. `kill -USR1` grava a pilha sem parar o worker. |
| Métricas | O heartbeat passa a levar `monitor`: última consulta, última consulta com sucesso, último relatório, probes em andamento, timeouts, falhas e reinícios. |
| Atraso visível mesmo com heartbeat saudável | `GET /api/streamers` ganha `monitor.state` (`live`, `offline`, `checking`, `stale`, `error`, `paused`) e a idade da última verificação. A tela de Streamers mostra "Monitoramento atrasado" com o texto pedido e um alerta no topo. O último resultado conhecido aparece separado. |
| "Verificar agora" com retorno real | Mostra "solicitada, aguardando o monitor", "concluída: …" (só quando o monitor de fato reportou), "falhou" ou "solicitada há N s e ainda não executada". |
| Captura | A checagem de "ainda ao vivo?" dentro da captura também usa a probe isolada. |
| Logs | Mensagens estruturadas com `streamer_id`, plataforma, erro resumido e tempo. Sem URLs com token e sem credenciais. |

Não há migração nem mudança de esquema. O contrato da API só **ganha** campos.

## 4. Testes (`tests/test_monitor_hotfix.py`, 18)

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

1. **Diagnóstico** (seção 2): `asc-ctl status` e `asc-ctl logs worker-capture` no 206. Guardar o log, que é a evidência da causa.
2. **Mitigação imediata, se autorizada:** `sudo -n asc-ctl compose restart worker-capture` no 206 (só o contêiner `asc-worker-capture` do projeto `asc`). Nada do Hank é tocado, e nenhum comando global do Docker é usado.
3. **Hotfix:**
   - build da imagem `0.6.2-hotfix.1` no 206;
   - `asc-ctl set-tag 0.6.2-hotfix.1`;
   - `compose up` dos serviços do ASC.

   Não há migração de banco, então não é preciso backup antes deste passo; mesmo assim, o backup diário deve estar válido.
4. **Verificação:**
   - `206-capture` online;
   - `GET /api/streamers` com `monitor.state` diferente de `stale` em até 2 minutos;
   - métricas `monitor.last_report_ok_at` avançando;
   - "Verificar agora" concluindo.
5. **Rollback:** `asc-ctl set-tag 0.6.2` e `compose up`. Não há esquema novo.

Nenhuma publicação, agendamento, OAuth ou serviço do Hank é alterado.

## EN summary

On 2026-11-19 the production capture worker (roles monitor and capture) stopped heartbeating 9.8 h before diagnosis. Every channel check stopped at that same instant, and the other workers were healthy. The whole worker process froze or died, which is more than a stuck probe could cause. Without SSH it is not yet proven whether the event loop blocked or the container stopped; the read-only commands to settle it are in section 2.

The hotfix makes this failure self-healing and visible:
- per-channel probes run in killable child processes with hard deadlines, and the poll loop never waits for them;
- the monitor loop is supervised;
- a thread watchdog dumps stacks and exits for a controlled restart when the event loop blocks;
- monitor progress metrics are added to the heartbeat;
- the panel shows "monitoring delayed" instead of stale results, and "Check now" gives truthful feedback.

There is no migration. 18 new tests pass, and the full suite passes (314 passed, 3 skipped).
