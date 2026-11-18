# AI Stream Clipper

Plataforma que acompanha lives públicas da Twitch e do YouTube, entende quando algo interessante aconteceu e entrega cortes verticais (1080×1920, H.264/AAC, legendas sincronizadas) prontos para revisão em um painel web.

```
URL da live → monitor → captura (buffer circular) → áudio + transcrição → detecção e pontuação
            → análise visual (opcional, GPU Intel) → renderização vertical → painel (prévia, aprovação, download)
```

O foco inicial é GTA V / GTA RP, mas nada no código depende do jogo.

## Estado atual

| Item | Situação |
|---|---|
| Pipeline ponta a ponta com vídeo de teste controlado | **Funcionando e testado** (`pytest -m e2e`) |
| Captura real do YouTube via streamlink | **Testada** manualmente em live pública 24/7 (55 segmentos, sem falsos positivos) |
| Captura real da Twitch | Mesmo caminho de código do YouTube; não testada contra uma live da Twitch |
| Transcrição (faster-whisper + Silero VAD) | **Testada** (modelo tiny nos testes) |
| Detecção, pontuação, explicações, deduplicação | **Testada** com dados reais de transcrição |
| Renderização vertical (3 layouts) + legendas | **Testada** |
| Sobrevivência a reinícios (worker morto, API reiniciada) | **Testada** no e2e |
| PostgreSQL | Código e migrações prontos; **testes rodaram em SQLite** (ver [docs/STATUS.md](docs/STATUS.md)) |
| Docker / Compose por servidor | Escritos; **não executados** (sem Docker nem acesso SSH nesta máquina) |
| GPU Intel Arc / modelo multimodal OpenVINO | Provedor implementado como **experimental**; validação por script em [docs/VISAO_GPU.md](docs/VISAO_GPU.md) |

## Estrutura

```
backend/clipper/          pacote Python único; cada serviço é um "papel" do mesmo código
  api/                    API Gateway (FastAPI) + rotas internas assinadas para workers
  workers/                runtime de workers, captura + servidor de segmentos, handlers
  analysis/               áudio (NumPy), transcrição, léxico PT/EN, visão (OpenCV/OpenVINO)
  scoring/                motor de eventos e pontuação, títulos e descrições
  render/                 legendas ASS e renderização FFmpeg 1080×1920
  pipeline.py             orquestração (task scheduler) - transições atômicas
  queue.py                fila durável com leases, retries e dead-letter
  migrations/             Alembic
dashboard/                React + TypeScript + Tailwind (painel em PT-BR)
infrastructure/
  docker/                 imagem principal e imagem de visão (OpenVINO)
  compose/                server-201.yml, server-206.yml, server-210.yml, local.yml
  scripts/                preflight, deploy, rollback, backup, segredos, validação de GPU
tests/                    testes unitários, de renderização e ponta a ponta + fixtures
docs/                     arquitetura, implantação, GPU, estado e limitações
```

## Rodar localmente (sem Docker)

Requisitos: Python 3.12+, Node 20+, FFmpeg com libass e libx264.

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e "./backend[capture,audio,vision,dev]"
(cd dashboard && npm ci && npm run build)

export ASC_INTERNAL_SECRET=$(openssl rand -hex 32) ASC_JWT_SECRET=$(openssl rand -hex 32)
export ASC_ADMIN_EMAIL=admin@example.com ASC_ADMIN_PASSWORD='troque-esta-senha'
export ASC_DASHBOARD_DIR=$PWD/dashboard/dist ASC_ALLOW_FILE_SOURCES=true ASC_TEST_MEDIA_DIR=$PWD/tests/fixtures

python -m clipper api --host 127.0.0.1 --port 8780 &
python -m clipper worker --roles monitor,capture,audio,scoring,vision,render &
# abra http://127.0.0.1:8780 e cadastre file://gameplay_event.mp4 ou um canal público
```

## Testes

```bash
cd backend
python -m pytest -q              # 51 testes, incluindo o ponta a ponta (~35 s)
python -m pytest -q -m "not e2e" # só unitários (~8 s)
```

## Implantação

Veja [docs/IMPLANTACAO.md](docs/IMPLANTACAO.md). Resumo: `210` primeiro (banco + API), depois `206` (captura, áudio, render) e por fim `201` (visão GPU). Cada servidor usa o projeto Compose `asc`, rede e volumes próprios, limites de CPU/memória e nenhuma porta pública além de IPs da rede privada.

## Uso responsável

O sistema analisa apenas transmissões públicas e não contorna DRM, login ou bloqueios. Analisar uma live **não** autoriza republicar ou monetizar o conteúdo: a publicação automática não existe nesta versão e os cortes ficam para revisão humana.
