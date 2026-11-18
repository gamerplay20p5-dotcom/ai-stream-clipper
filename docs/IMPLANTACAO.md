# Implantação, atualização e rollback (v0.2.1+)

Tudo roda no projeto Docker `asc`, com redes `asc_net_<servidor>`, volumes `asc_*` e pastas `/opt/asc` e `/etc/asc` próprias. O usuário `asc` **não** fica no grupo `docker`: ele opera pelo `asc-ctl` ([infrastructure/admin/README.md](../infrastructure/admin/README.md)), que só aceita operações no projeto `asc` que passam pela política de isolamento.

| Servidor | Serviços | Acesso |
|---|---|---|
| 210 | `asc-postgres` (sem porta), `asc-api` (HTTPS `192.168.1.210:8780`), `asc-worker-scoring`, `asc-worker-vision` (Arc A750) | painel `https://192.168.1.210:8780` |
| 206 | `asc-worker-capture` (segmentos em `192.168.1.206:8781`, autenticado), `asc-worker-audio`, `asc-worker-render` | — |
| 201 | `asc-worker-vision` (Arc A750, 4 CPUs / 8 GB, prioridade baixa) | — |

## 0. Instalação do asc-ctl (administrador, uma vez por servidor)

```bash
sudo bash /home/asc/ai-stream-clipper/infrastructure/admin/install-asc-ctl.sh <201|206|210> <sha256 informado>
```

No 201, depois que o `asc-ctl` estiver funcionando: `sudo gpasswd -d asc docker`.

## 1. Configuração (como asc)

```bash
cd ~/ai-stream-clipper
cp .env.example ~/asc.env && chmod 600 ~/asc.env      # preencher (segredos: gen-secrets.sh)
sudo asc-ctl env-install ~/asc.env                     # validado -> /etc/asc/asc.env (root 600)
sudo asc-ctl init                                      # /opt/asc/{models,test-media,bench}
```

**HTTPS:**
- No 210: `sudo asc-ctl tls-init`. Isso gera a CA do projeto e o certificado da API, e deixa `~/asc-ca.crt` para distribuição.
- No 206 e no 201: copiar o `asc-ca.crt` e rodar `sudo asc-ctl tls-install-ca ~/asc-ca.crt`.

## 2. Imagens (compiladas só no 206)

```bash
# 206
sudo asc-ctl build app 0.2.1 && sudo asc-ctl build vision 0.2.1
sudo asc-ctl image-save app 0.2.1 && sudo asc-ctl image-save vision 0.2.1   # -> ~/images/*.tar.gz
# copiar para 210 (app + vision) e 201 (vision), depois em cada um:
sudo asc-ctl image-load ~/images/ai-stream-clipper_0.2.1.tar.gz
```

## 3. Subida gradual

```bash
# 210
sudo asc-ctl compose up postgres api
sudo asc-ctl exec asc-api -- python -m pytest ...    # (ver docs/STATUS.md)
sudo asc-ctl compose up worker-scoring
# 206
sudo asc-ctl models yamnet whisper-small
sudo asc-ctl compose up
# 201 e 210 (GPU)
sudo asc-ctl models qwen3-vl-2b-int4 qwen3-1.7b-int4
sudo asc-ctl gpu-validate --vlm qwen3-vl-2b-int4      # resultados em /opt/asc/bench
sudo asc-ctl compose up --gpu                         # 210; no 201: sudo asc-ctl compose up
```

## Acesso ao painel

`https://192.168.1.210:8780`. O certificado é emitido pela CA do projeto. Para o navegador confiar nele, importe o `asc-ca.crt` como autoridade confiável. A alternativa é um túnel SSH: `ssh -L 8780:192.168.1.210:8780 asc@192.168.1.210` e depois abrir `https://localhost:8780`.

## Monitoramento

- `sudo asc-ctl status`: carga, memória, disco, **todos** os contêineres e o uso da GPU (somente leitura).
- `sudo asc-ctl compose ps` e `sudo asc-ctl compose logs <serviço> --tail 200`.
- No painel, a página **Infraestrutura** mostra workers, filas, modelos e latências.

## Transferência direta de imagens (asc-send / asc-receive)

As imagens são compiladas no 206 e enviadas direto ao 210 e ao 201, sem passar pelo notebook (de 5 a 13 s por imagem na rede local):

```bash
sudo asc-ctl image-save app <tag> && sudo asc-ctl image-save vision <tag>
~/bin/asc-send 210 ~/images/ai-stream-clipper_<tag>.tar.gz
~/bin/asc-send 210 ~/images/ai-stream-clipper-vision_<tag>.tar.gz
~/bin/asc-send 201 ~/images/ai-stream-clipper-vision_<tag>.tar.gz
```

A chave `~/.ssh/id_ed25519_transfer` só é aceita no destino com `command="asc-receive"` e `from=` restrito ao 206. O `asc-receive` aceita apenas nomes `ai-stream-clipper(-vision)_<tag>.tar.gz`, grava só em `~/images`, confere o SHA-256 e limita o tamanho a 4 GB. Não há shell, root nem acesso a outros diretórios.

## Corte de teste e diagnóstico

- **Gerar corte de teste** (ícone de frasco na linha do streamer): últimos 30, 60 ou 90 s, ou um intervalo do buffer. Passa pelo pipeline real (visão, diretor e renderização) sem depender da nota, aparece com o selo **TESTE MANUAL** e fica fora das métricas. No máximo 2 testes em processamento ao mesmo tempo; o intervalo é validado contra o buffer.
- **Diagnóstico** (menu lateral): etapa e estado por streamer, com explicação baseada nos dados reais (fila, rejeições do scoring por motivo, melhor nota rejeitada, fallback da visão), e o botão **Repetir falhas**, que é idempotente.

## Reemissão da CA (asc-ctl 1.0.1)

A CA emitida pelo asc-ctl 1.0.0 não tem `keyUsage` e é recusada por verificadores estritos (Python 3.13+, alguns navegadores). Para corrigir, como administrador:

```bash
# nos 3 servidores (atualiza o asc-ctl para 1.0.1)
sudo bash /home/asc/ai-stream-clipper/infrastructure/admin/install-asc-ctl.sh <201|206|210> <sha256 do asc-ctl>
# no 210: nova CA e novo certificado do servidor
sudo asc-ctl tls-init --new-ca
# copiar /etc/asc/tls/ca.crt do 210 para ~/asc-ca.crt no 206 e no 201, e em cada um:
sudo asc-ctl tls-install-ca ~/asc-ca.crt
# reiniciar os contêineres asc nos 3: sudo asc-ctl compose restart   (210: sudo asc-ctl compose restart --gpu)
```

Depois, importe o novo `ca.crt` no navegador (Windows: `certutil -user -addstore Root asc-ca.crt`).

## Atualização

1. Copiar o novo código para `~/ai-stream-clipper` e compilar e distribuir as imagens com a nova tag.
2. No 210, antes de qualquer migração: `sudo asc-ctl backup-db` (vai para `~/backups/`).
3. Em cada servidor: `sudo asc-ctl set-tag <nova>` e `sudo asc-ctl compose up`. As migrações rodam ao iniciar a API.

## Rollback

```bash
sudo asc-ctl set-tag <anterior> && sudo asc-ctl compose up   # imagens anteriores ficam guardadas
sudo asc-ctl compose stop                                   # parar o projeto (dados mantidos)
sudo asc-ctl compose down                                   # remover os contêineres asc (volumes mantidos)
```

Se a versão nova migrou o banco: restaurar o backup de `~/backups` (`gunzip -c` piped para `psql` via `asc-ctl exec asc-postgres`) **antes** de voltar a imagem. O `asc-ctl` nunca remove volumes; apagar dados exige ação manual do administrador.

## Migração futura do banco para o 212

`backup-db` no 210 e restauração no 212. Depois, `ASC_DB_HOST=192.168.1.212` no `asc.env`, `env-install` e `compose up api`.
