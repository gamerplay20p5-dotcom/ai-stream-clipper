# Implantação do v0.6.2-hotfix.2 (2026-11-20, autorizada)

**Escopo:** só `asc-worker-capture` (206) e `asc-api` (210), com a tag `0.6.2-hotfix.2` (commit `79582c8`). Sem migração. Nenhum outro serviço foi recriado.

## Antes

- Diagnóstico somente leitura: veja [2026-11-20-diagnostico-206.md](2026-11-20-diagnostico-206.md).
- Logs completos do contêiner travado, guardados **antes** de recriá-lo: [logs/2026-11-19-asc-worker-capture-0.6.2-travado.log](logs/2026-11-19-asc-worker-capture-0.6.2-travado.log) (do início às 05:29:31 até a última linha às 10:23:09).
- Publicações: 46 (33 agendadas, 12 publicadas, 1 com falha) e envios não pausados. Próxima publicação agendada: 20/11 às 12:00 (Brasília).

## Passos

1. Código da tag `v0.6.2-hotfix.2` enviado ao 206 e ao 210. O 201 não foi tocado.
2. No 206: `asc-ctl build app` e `build vision` (`0.6.2-hotfix.2`), `image-save` e `asc-send` para o 210, `image-load`. Imagens conferidas nos dois servidores com os mesmos IDs; as imagens `0.6.2` foram mantidas para rollback.
3. No 206: `set-tag 0.6.2-hotfix.2` e `asc-ctl compose up worker-capture`.
4. No 210: tag no `asc.env`, `env-install` e `asc-ctl compose up --gpu api`. O `--gpu` evita que o `worker-vision` seja tratado como órfão pelo `--remove-orphans`.

## Depois

Contêineres do ASC (somente estes dois mudaram de imagem; nenhum contêiner de outro projeto mudou nos três servidores):

206:
```
asc-worker-capture	ai-stream-clipper:0.6.2-hotfix.2	Up About a minute (healthy)
asc-worker-audio	ai-stream-clipper:0.6.2	Up 20 hours (healthy)
asc-worker-render	ai-stream-clipper:0.6.2	Up 20 hours (healthy)
```

210:
```
asc-api	ai-stream-clipper:0.6.2-hotfix.2	Up 18 seconds (healthy)
asc-worker-vision	ai-stream-clipper-vision:0.6.2	Up 20 hours (healthy)
asc-worker-scoring	ai-stream-clipper:0.6.2	Up 20 hours (healthy)
asc-worker-publisher	ai-stream-clipper:0.6.2	Up 20 hours (healthy)
asc-postgres	postgres:16.4-bookworm	Up 45 hours (healthy)
```

**Recuperação:**
- **Sessões vencidas:** as 19 e 20, com mais de 12 h, foram encerradas na hora com `max_duration` (0,03 s), sem travar.
- **Monitor:** voltou em segundos e criou 5 sessões novas (YouTube, SPEED, Elvis Red, Neutro e kaicenat). Duas aguardam vez por causa do limite de 3 capturas simultâneas.
- **Primeiros minutos:** heartbeat a cada 10 s, verificações de todos os canais habilitados com no máximo ~60 s de idade, 20 probes sem falha, 0 timeouts, 0 reinícios.

**Conferências:**
- `/healthz` responde `0.6.2-hotfix.2`;
- o Alembic continua em **0010**;
- `monitor.state` aparece em `/api/streamers`;
- as **46 publicações estão idênticas** antes e depois, e os envios seguem não pausados.

## Rollback (por serviço)

- **206:** `set-tag 0.6.2` e `asc-ctl compose up worker-capture`. Não recomendado: o defeito voltaria no próximo limite de 12 h.
- **210:** tag `0.6.2` no `asc.env`, `env-install` e `asc-ctl compose up --gpu api`.

## Atenção para os próximos `compose up`

A `ASC_IMAGE_TAG` do 206 e do 210 agora é `0.6.2-hotfix.2`. Um `compose up` sem nome de serviço recriaria áudio, render, scoring, publisher e visão com a imagem do hotfix. Elas existem e são compatíveis, mas isso deve ser uma decisão explícita. O 201 continua em `0.6.2`.
