# Implantação e rollback

Tudo roda no projeto Compose `asc`, com redes `asc_net_<servidor>` e volumes `asc_*` próprios. Nenhum comando abaixo para, altera ou remove contêineres existentes, e nada usa `prune`.

## 0. Pré-requisitos (uma vez)

1. Copie o repositório para os três servidores, por exemplo em `/opt/ai-stream-clipper`, e rode `chmod +x infrastructure/scripts/*.sh` (os arquivos foram criados no Windows).
2. Gere os segredos em qualquer máquina:
   ```bash
   ./infrastructure/scripts/gen-secrets.sh
   ```
   Use o **mesmo** `ASC_INTERNAL_SECRET` em 201, 206 e 210.
3. Crie `.env.210`, `.env.206` e `.env.201` a partir de `.env.example`. Diferenças principais:

   | variável | 210 | 206 | 201 |
   |---|---|---|---|
   | `ASC_BIND_IP` | 192.168.1.210 | 192.168.1.206 | (não usa) |
   | `ASC_API_URL` | (interno) | http://192.168.1.210:8780 | http://192.168.1.210:8780 |
   | `ASC_RENDER_GID` | `getent group render` | (não usa) | `getent group render` |
   | `ASC_JWT_SECRET`, `ASC_DB_PASSWORD`, `ASC_ADMIN_*` | sim | não | não |
4. Firewall (recomendado, aplicar conforme a política de vocês). As portas 8780 (210) e 8781 (206) só precisam ser alcançáveis a partir de 192.168.1.201, .206 e .210. Exemplo com ufw, **não aplicado automaticamente**:
   ```bash
   sudo ufw allow from 192.168.1.0/24 to any port 8780 proto tcp   # no 210
   sudo ufw allow from 192.168.1.0/24 to any port 8781 proto tcp   # no 206
   ```
   Atenção: o Docker publica portas por iptables antes do ufw. Por isso o Compose já faz bind apenas no IP privado (`ASC_BIND_IP`), e nada fica em `0.0.0.0`.

## 1. Servidor 210 (banco + API + painel)

```bash
cd /opt/ai-stream-clipper
./infrastructure/scripts/preflight.sh 210       # só leitura: portas, disco, GPU, contêineres existentes
./infrastructure/scripts/deploy.sh 210          # sobe postgres, api, worker-scoring
curl http://192.168.1.210:8780/readyz
```

Acesse `http://192.168.1.210:8780`, entre com `ASC_ADMIN_EMAIL` / `ASC_ADMIN_PASSWORD` e troque a senha:

```bash
docker exec -it asc-api clipper create-admin admin@empresa.com
```

Para HTTPS pelo Caddy existente, sem alterar a configuração atual sem revisão: um bloco novo `reverse_proxy 192.168.1.210:8780` em um subdomínio próprio, e `ASC_COOKIE_SECURE=true`. A integração com Authentik (OIDC) está preparada nas variáveis `ASC_OIDC_*`, mas ainda não está ativa.

## 2. Servidor 206 (captura, áudio, render)

```bash
./infrastructure/scripts/preflight.sh 206
./infrastructure/scripts/deploy.sh 206
```

Baixe o YAMNet (classes acústicas, 20 MB): `./infrastructure/scripts/download-models.sh 206`.

Na primeira transcrição o worker de áudio baixa o modelo Whisper (`small`, cerca de 500 MB) para o volume `asc_models`. Em **Infraestrutura**, o 206 deve aparecer com três workers online.

Limites (somados): 14 CPUs e 21 GiB, de 32 threads e 200 GiB. Ajuste `ASC_WHISPER_CPU_THREADS`, `ASC_CONCURRENCY_AUDIO` e os `cpus` no Compose conforme a carga observada.

## 3. Servidor 201 (visão GPU) e GPU do 210

```bash
./infrastructure/scripts/preflight.sh 201
docker compose -p asc -f infrastructure/compose/server-201.yml --env-file .env.201 build
sudo mkdir -p /opt/asc/models && sudo chown 10001:10001 /opt/asc/models
./infrastructure/scripts/download-models.sh 201   # Qwen3-VL-2B + Qwen3-1.7B (~2,9 GB)
./infrastructure/scripts/validate-gpu.sh          # precisa passar antes de subir o worker
./infrastructure/scripts/validate-gpu.sh --vlm qwen3-vl-2b-int4   # latência/VRAM reais -> registrar em FASE2.md
./infrastructure/scripts/deploy.sh 201
# no 210, opcional:
./infrastructure/scripts/deploy.sh 210 --gpu
```

O provedor padrão é o `heuristic` (OpenCV), que já foi validado. Para o modelo multimodal, veja [VISAO_GPU.md](VISAO_GPU.md).

## Atualização

Altere `ASC_IMAGE_TAG` no `.env.<servidor>` (por exemplo `0.1.1`) e rode `deploy.sh` de novo. A API aplica as migrações Alembic ao iniciar. A tag anterior fica registrada em `.deploy/previous-images-<servidor>.txt`.

## Rollback

```bash
# 1. (se a versão nova trouxe migração) volte o schema ANTES de trocar a imagem
./infrastructure/scripts/rollback.sh 210 --db 0001
# 2. volte para a imagem anterior (precisa existir localmente)
./infrastructure/scripts/rollback.sh 210 --to 0.1.0
./infrastructure/scripts/rollback.sh 206 --to 0.1.0
# parar tudo do asc em um servidor (dados preservados)
./infrastructure/scripts/rollback.sh 206 --stop
```

Remoção completa, **apagando os dados** (só com decisão explícita):
`docker compose -p asc -f infrastructure/compose/server-XXX.yml --env-file .env.XXX down -v`

## Backup

```bash
./infrastructure/scripts/backup-db.sh /opt/backups/asc     # pg_dump compactado
```

Os vídeos ficam no volume `asc_storage` (210). Faça backup com a ferramenta de volumes que vocês já usam.

## Migração do banco para o servidor 212

1. `backup-db.sh` no 210 e restauração no PostgreSQL do 212.
2. No `.env.210`, defina `ASC_DB_HOST=192.168.1.212` (e a senha correspondente). O serviço `postgres` local pode então ser parado com `docker compose ... stop postgres`.
3. `deploy.sh 210`.
