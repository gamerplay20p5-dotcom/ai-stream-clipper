# Evidências do incidente de monitoramento (2026-11-19)

Coletadas **somente com leitura** (GET), pela API pública do painel, autenticada como administrador. O SSH para 201, 206 e 210 deu *timeout* nas duas coletas. Nenhum dado foi alterado e nenhum serviço foi reiniciado.

## Linha do tempo (UTC)

| Hora | Fato |
|---|---|
| 05:29:31 | `206-capture` iniciado (deploy da v0.6.2) |
| 10:22:58 a 10:23:51 | os 10 canais habilitados são verificados normalmente (a última rodada **terminou**) |
| 10:23:50 | último heartbeat do `206-capture` |
| ~10:24:00 | heartbeat seguinte esperado (intervalo de 10 s): **não chegou** |
| 10:25:10 | job `capture` 17633: "lease expired (worker 206-capture lost)" |
| 10:33:30 | `vision_analyze` 41249: "segment server unreachable" (o servidor de segmentos fica no mesmo processo) |
| 20:13 | 1ª coleta: 9,8 h sem heartbeat e sem verificações |
| ~00:35 (dia 20) | 2ª coleta: 14,2 h, mesmo estado; 2 jobs de captura parados na fila |

**Leitura:** o processo do worker de captura parou **de uma vez**, entre 10:23:51 e 10:24:00, logo depois de uma rodada completa de verificações. Isso descarta "uma probe presa segurando a rodada" como gatilho deste incidente. A rodada anterior terminou, e o heartbeat também parou. Sobram duas hipóteses:
- o event loop travou numa chamada bloqueante;
- o processo ou contêiner morreu (falta de memória no limite de 3 GB, por exemplo) e não voltou.

Os logs do contêiner (`asc-ctl compose logs worker-capture`) e o estado (`asc-ctl status`) decidem entre as duas. Eles precisam ser coletados **antes** de qualquer reinício, porque reiniciar pode sobrescrever o estado. O hotfix cobre os dois casos e passa a gravar a pilha das threads no log se o loop travar de novo.

## Coleta com horários absolutos

```
canais habilitados: 10 | �ltima verifica��o: mais antiga 2026-11-19T10:22:58.224251Z | mais recente 2026-11-19T10:23:51.491380Z
206-capture: status offline | last_heartbeat_at 2026-11-19T10:23:50.058170Z | version 0.6.2 | started_at 2026-11-19T05:29:31.394002Z
falha vision_analyze 41249 2026-11-19T10:33:30.616878Z segment server unreachable: HTTP 0: download failed: 
falha capture 17633 2026-11-19T10:25:10.232831Z lease expired (worker 206-capture lost)
falha vision_analyze 14972 2026-11-18T18:39:38.088811Z worker shutting down
fila capture: {'done': 13, 'dead': 5, 'queued': 2}
```

## Coleta completa (2ª)

```
healthz {'status': 'ok', 'version': '0.6.2'}

STREAMERS (14)
- #15 twitch   kaicenat               enabled=True status=live     checked=14.2 h check_requested=None live_session=20 err=''
- #14 youtube  @udantt                enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #13 twitch   Neutro                 enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #12 youtube  Elvis Red              enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #11 youtube  PIUZINHO               enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #10 twitch   SPEED                  enabled=True status=live     checked=14.2 h check_requested=None live_session=None err=''
- #9 youtube  YouTube                enabled=True status=live     checked=14.2 h check_requested=None live_session=19 err=''
- #8 file     Teste p�blico (v�deo pr�prio) enabled=False status=unknown  checked=29.5 h check_requested=None live_session=None err=''
- #7 youtube  Paulinho o Loko        enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #6 twitch   bistecone              enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #5 file     Teste persegui��o      enabled=False status=unknown  checked=42.8 h check_requested=None live_session=None err=''
- #4 twitch   Alanzoka               enabled=True status=offline  checked=14.2 h check_requested=None live_session=None err=''
- #2 twitch   Coiote                 enabled=False status=offline  checked=29.2 h check_requested=None live_session=None err=''
- #1 twitch   TeeGrizzley            enabled=False status=offline  checked=42.8 h check_requested=None live_session=None err=''

WORKERS
- 201 201-vision-gpu status=online hb=0.0 h roles=['vision'] metrics={}
- 206 206-audio status=online hb=0.0 h roles=['audio'] metrics={}
- 206 206-capture status=offline hb=14.2 h roles=['monitor', 'capture'] metrics={}
- 206 206-render status=online hb=0.0 h roles=['render'] metrics={}
- 210 210-publisher status=online hb=0.0 h roles=['publisher'] metrics={}
- 210 210-scoring status=online hb=0.0 h roles=['scoring'] metrics={}
- 210 210-vision-gpu status=online hb=0.0 h roles=['vision'] metrics={}

RECENT FAILURES [('vision_analyze', 41249, 'segment server unreachable: HTTP 0: download failed: '), ('capture', 17633, 'lease expired (worker 206-capture lost)'), ('vision_analyze', 14972, 'worker shutting down'), ('capture', 11029, 'worker shutting down'), ('capture', 4066, 'worker shutting down'), ('capture', 2290, 'worker shutting down'), ('capture', 403, 'worker shutting down')]

QUEUE (no queue field)

STATS live_sessions 2 pending_jobs 0 dead_jobs 7
```
