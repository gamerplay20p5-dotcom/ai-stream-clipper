# Benchmark da PR #31 na 201 / PR #31 benchmark on host 201

[Português (Brasil)](#português-brasil) · [English](#english) · [Comandos / Commands](#comandos--commands)

## Português (Brasil)

A Intel Arc A750 e o driver são compartilhados. Docker limita CPU, RAM e processos, mas não reserva GPU ou VRAM. Reserve uma janela de baixa atividade e confirme com os responsáveis pelos outros serviços. O comando recusa a partida se já houver um cliente da GPU, inclusive ocioso, e interrompe o benchmark se aparecer outro cliente, um contêiner monitorado mudar de estado ou saúde, ou os recursos do host ficarem abaixo da reserva. A checagem é periódica. Picos entre amostras ou travamentos do driver ainda podem afetar outros serviços. Se a GPU estiver ocupada ou a checagem falhar, aguarde uma janela livre e mantenha os serviços rodando.

Use os arquivos já preparados em `/home/asc/bench/pr31/`. O comando aceita uma única opção, `--check`, e não recebe caminhos. Precisa de Docker Engine e CLI 25+, cgroups com limites de CPU, RAM, swap e processos, seccomp, e da imagem já usada pelo `asc-worker-vision` em execução. A imagem é selecionada pelo ID local. O replay roda com `PYTHONPATH=/pr31/backend`, sem rede, sem configuração da produção, sem fallback para CPU e com código, mídia e `/opt/asc/models` somente leitura. Os limites são 4 CPUs, 12 GiB de RAM sem swap, 256 processos e 3600 s. O comando verifica a memória, a carga, o disco e a permissão do dispositivo antes de iniciar. Com a configuração e a mídia validadas, a etapa inicial no sandbox verifica o acesso do OpenVINO à GPU e os modelos locais. Se o export habilitar Whisper e houver áudio, a imagem existente precisa incluir `faster-whisper` e o modelo deve estar no cache local. A imagem padrão de visão usa o extra `vision`, que não inclui `faster-whisper`. Se a imagem da 201 tiver essa limitação, o preflight interrompe o replay sem alterar a imagem ou os critérios.

- Use um `annotations.json` ou `manifest.json`. Referencie somente vídeos dentro de `pr31/`, com nome composto de letras ASCII, números, `.`, `_` ou `-`. O limite é 32 casos, 64 arquivos distintos, 2 GiB por arquivo e 4 GiB no total. Links simbólicos e hard links são recusados.
- Use um `editorial.json`, `settings.json`, `editorial-75.json` ou `settings-75.json`, sem credenciais e com `min_score` numérico igual a `75`. Esse limiar vale para este benchmark. Os demais critérios vêm do export. O relatório do replay registra as diferenças intencionais em `settings_differences`.
- Mantenha o `backend/clipper` da PR #31 no commit `33f3e00953c35dfcaf301a086aaf75b1743e31e0`, direto em `pr31/` ou sob `src/`, `checkout/` ou `code/`. O comando confere o conteúdo antes de montar a cópia. Use um `out/` real, gravável por `asc`.

Após instalar manualmente, use primeiro `--check`. Essa opção verifica o host, o export e os arquivos, sem iniciar contêiner ou inferência. O comando completo grava `asc-bench-pr31-*.status.json` em `/home/asc/bench/pr31/out/` quando a tentativa chega à criação do sandbox. Ao concluir o replay, grava também `asc-bench-pr31-*.json`. O status registra o ID da imagem, amostras da GPU, resultado e motivo de interrupção, quando aplicável. Falhas anteriores à criação são exibidas no terminal. Para reverter, aguarde o fim do benchmark ou interrompa com `Ctrl+C` e siga os comandos de rollback abaixo.

## English

The Intel Arc A750 and its driver are shared. Docker caps CPU, memory and processes but cannot reserve GPU or VRAM. Schedule a low-activity window with the owners of the other services. The command refuses to start while any process holds the GPU, including an idle client. It stops the benchmark if a second client appears, a monitored container changes state or health, or host resources fall below the reserve. Monitoring is periodic. Spikes between samples and driver hangs can still affect services. Wait for a quiet window if the GPU is busy or preflight fails, and leave the services running.

Reuse the files prepared in `/home/asc/bench/pr31/`. The only option is `--check`. The command takes no paths. It requires Docker Engine and CLI 25+, enforced CPU, memory, swap and PID limits, seccomp, and the image used by the running `asc-worker-vision`. It resolves the existing local image ID. The replay runs with `PYTHONPATH=/pr31/backend`, networking disabled, production configuration excluded, CPU fallback disabled, and read-only code, media and `/opt/asc/models` mounts. Limits are 4 CPUs, 12 GiB RAM with swap disabled, 256 processes and 3600 s. Host memory, load, disk and device permissions are checked before launch. After validating the settings and media, sandbox preflight checks OpenVINO GPU access and local models. For media with audio and Whisper enabled in the export, the existing image needs `faster-whisper` and the model must be cached locally. The standard vision image uses the `vision` extra, which omits `faster-whisper`. If that applies on host 201, preflight stops without changing the image or editorial criteria.

- Supply either `annotations.json` or `manifest.json`. Reference videos within `pr31/` using ASCII letters, digits, `.`, `_` or `-` in path components. Limits are 32 cases, 64 distinct files, 2 GiB per file and 4 GiB total. Symlinks and hard links are rejected.
- Supply one of `editorial.json`, `settings.json`, `editorial-75.json` or `settings-75.json`. Use a credential-free export with numeric `min_score` set to `75` for this benchmark. The other criteria come from the export. The replay report records intentional differences in `settings_differences`.
- Keep `backend/clipper` from PR #31 commit `33f3e00953c35dfcaf301a086aaf75b1743e31e0` directly under `pr31/` or inside `src/`, `checkout/` or `code/`. The command verifies bytes before mounting a copy. Use a real `out/` directory writable by `asc`.

After manual installation, start with `--check`. This validates the host, export and files without starting a container or running inference. The full command writes `asc-bench-pr31-*.status.json` to `/home/asc/bench/pr31/out/` once it reaches sandbox creation. It also writes `asc-bench-pr31-*.json` when the replay finishes. The status file contains the image ID, GPU samples, outcome and any reason for stopping. Failures before creation are shown in the terminal. To roll back, wait for the benchmark to finish or stop it with `Ctrl+C`, then use the rollback commands below.

## Comandos / Commands

### Instalação manual / Manual installation

O administrador da 201 substitui somente o binário já instalado, a partir de um checkout revisado desta PR. Guarde a versão anterior até a validação terminar.

The administrator on host 201 replaces the existing executable using a reviewed checkout of this PR. Keep the previous version until validation is complete.

```bash
SOURCE=/absolute/path/to/reviewed-checkout/infrastructure/admin/asc-ctl
EXPECTED=f86b372caa1fdbd851d7dcfb12e9af34fb16f2a0fc3c6f6a7e078210f258c610
sudo install -o root -g root -m 0600 "$SOURCE" /usr/local/sbin/asc-ctl.pr31.new
sudo bash -ceu '
  cd /usr/local/sbin
  printf "%s  %s\n" "$1" asc-ctl.pr31.new | sha256sum -c -
  test -f asc-ctl
  test ! -e asc-ctl.pre-pr31
  install -o root -g root -m 0755 asc-ctl asc-ctl.pre-pr31
  chmod 0755 asc-ctl.pr31.new
  mv -T asc-ctl.pr31.new asc-ctl
' _ "$EXPECTED"
sudo -n /usr/local/sbin/asc-ctl version
```

### Verificação e benchmark / Preflight and benchmark

```bash
sudo -n /usr/local/sbin/asc-ctl bench-pr31 --check
# Após a checagem e na janela aprovada / After preflight, during the approved window
sudo -n /usr/local/sbin/asc-ctl bench-pr31
```

### Rollback

Liste os sandboxes pelo rótulo. Se uma interrupção deixou um órfão, confirme o ID e o rótulo e remova somente aquele ID. Então restaure o binário anterior. Os relatórios continuam no `out/`.

List sandboxes by label. If an interruption left an orphan, verify its ID and label and remove that ID only. Then restore the prior executable. Reports remain in `out/`.

```bash
sudo docker ps -a --no-trunc --filter label=com.asc.bench=pr31-201 --format '{{.ID}} {{.Names}} {{.Status}}'
# Somente para um órfão verificado / For a verified orphan only
# sudo docker rm -fv FULL_VERIFIED_SANDBOX_ID
sudo bash -ceu '
  cd /usr/local/sbin
  test -f asc-ctl.pre-pr31
  install -o root -g root -m 0755 asc-ctl.pre-pr31 asc-ctl.rollback.new
  mv -T asc-ctl.rollback.new asc-ctl
'
sudo -n /usr/local/sbin/asc-ctl version
```
