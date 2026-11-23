# Benchmark da PR #31 na 201 / PR #31 benchmark on host 201

[Português (Brasil)](#português-brasil) · [English](#english) · [Comandos / Commands](#comandos--commands)

## Português (Brasil)

A Intel Arc A750 e o driver são compartilhados. Docker limita CPU, RAM e processos, mas não reserva GPU ou VRAM. Reserve uma janela de baixa atividade para o benchmark e confirme com os responsáveis pelos outros serviços. O benchmark com GPU recusa a partida se já houver um cliente da placa, inclusive ocioso, e é interrompido se surgir concorrência. A checagem é periódica, e picos entre amostras ou travamentos do driver ainda podem afetar outros serviços. O `--check` roda somente em CPU e pode ser executado com a Arc em uso. Ambos os modos verificam a saúde dos workers e as reservas de recursos do host antes e durante a execução.

Use os arquivos preparados em `/home/asc/bench/pr31/`, incluindo o código da PR #31 no commit `33f3e00953c35dfcaf301a086aaf75b1743e31e0`, `manifest.json`, o diretório `out/` gravável por `asc` e `settings-75.json` sem credenciais com `min_score=75` e `whisper_model=small`. Se `media/` estiver ausente, o launcher busca as referências `media/*` no diretório preparado `bench/media/`. O relatório registra ajustes intencionais em `settings_differences`.

O pacote de áudio separado fornece wheels CPython 3.12 Linux x86-64 verificadas por SHA-256 e o Whisper small já presente na 206. O `asc-ctl` verifica as versões e o snapshot e monta o pacote, código, mídia e modelos somente para leitura. O `--check` usa a imagem de visão local em um contêiner descartável sem rede nem dispositivo GPU. Valida as importações, inicializa o Whisper small em CPU sem transcrever e verifica os arquivos do replay. O replay completo preserva os limites de 4 CPUs, 12 GiB sem swap, 256 processos e 3600 s, com monitoramento da GPU e dos contêineres. Requer Docker Engine e CLI 25+, limites cgroup e seccomp.

A checagem completa usa a imagem existente do `asc-worker-vision` pelo ID local. A troca manual do binário preserva os 21 comandos da 1.2.0 instalada e mantém uma cópia para rollback.

## English

The Intel Arc A750 and its driver are shared. Docker caps CPU, memory and processes but cannot reserve GPU or VRAM. Schedule a low-activity window for the benchmark with the owners of the other services. GPU replay refuses to start while any process holds the GPU, including an idle client, and stops if another client appears. Monitoring is periodic, and spikes between samples or driver hangs can still affect services. The CPU-only `--check` can run while the Arc is in use. Both modes verify worker health and host resource reserves before and during execution.

Reuse the files under `/home/asc/bench/pr31/`, including PR #31 code at `33f3e00953c35dfcaf301a086aaf75b1743e31e0`, `manifest.json`, an `out/` writable by `asc`, and a credential-free `settings-75.json` with `min_score=75` and `whisper_model=small`. If `media/` is absent, the launcher resolves `media/*` in the prepared `bench/media/` directory. The report records intentional adjustments in `settings_differences`.

The separate audio bundle supplies SHA-256-verified Linux x86-64 CPython 3.12 wheels and the existing Whisper small model from host 206. `asc-ctl` validates the versions and snapshot. The bundle, code, media and models are read-only mounts. `--check` uses the local vision image in a disposable container with no network or GPU device. It validates imports, initializes Whisper small on CPU without transcribing, and checks the replay inputs. Full replay retains its 4 CPU, 12 GiB without swap, 256 process and 3600-second limits, GPU guard and peer health monitoring. Docker Engine and CLI 25+, enforced cgroup limits and seccomp are required.

Preflight resolves the existing `asc-worker-vision` image by its local ID. The manual executable update keeps all 21 commands from the installed 1.2.0 release and saves the previous executable for rollback.

## Comandos / Commands

### Preparar dependências / Prepare dependencies

Execute num Linux de preparação com pip, acesso ao PyPI e a chave da 206 carregada. Use o checkout revisado da PR #32. Transfira o arquivo resultante para a 201 e registre seu SHA-256 pelo canal de administração.

On a Linux preparation machine with pip, PyPI access and the SSH key for host 206 loaded, use the reviewed checkout of PR #32. Transfer the resulting archive to host 201 and record the SHA-256 through the administrative channel.

```bash
set -euo pipefail
scratch=$(mktemp -d)
bundle="$scratch/audio"
revision=536b0662742c02347bc0e980a01041f333bce120
repo="models--Systran--faster-whisper-small"
cache="/opt/asc/models/hf/hub/$repo"
snapshot="$bundle/hub/$repo/snapshots/$revision"
mkdir -p "$bundle/site" "$bundle/hub/$repo/refs" "$snapshot"
python3 -m pip --isolated install --index-url https://pypi.org/simple \
  --only-binary=:all: --no-deps --require-hashes --no-compile \
  --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
  --python-version 3.12 --implementation cp --abi cp312 \
  --target "$bundle/site" -r infrastructure/admin/bench-pr31-audio.lock
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes asc@192.168.1.206 \
  "ionice -c 3 nice -n 19 tar -C '$cache/snapshots/$revision' -chf - config.json model.bin tokenizer.json vocabulary.txt" \
  | tar -C "$snapshot" -xf -
(cd "$snapshot" && sha256sum -c <<'SHA256'
b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828  config.json
3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671  model.bin
fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab  tokenizer.json
34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913  vocabulary.txt
SHA256
)
printf '%s' "$revision" > "$bundle/hub/$repo/refs/main"
tar -C "$bundle" -cf "$PWD/asc-pr31-audio.tar" site hub
sha256sum "$PWD/asc-pr31-audio.tar"
```

### Instalação manual / Manual installation

O administrador da 201 substitui somente o binário já instalado, a partir de um checkout revisado desta PR. Guarde a versão anterior até a validação terminar.

The administrator on host 201 replaces the existing executable using a reviewed checkout of this PR. Keep the previous version until validation is complete.

```bash
set -euo pipefail
AUDIO_ARCHIVE=/absolute/path/to/asc-pr31-audio.tar
AUDIO_SHA=copy_the_archive_sha256_from_preparation
SOURCE=/absolute/path/to/reviewed-checkout/infrastructure/admin/asc-ctl
EXPECTED=3491833a450eeb1e4060520335806969dbb96871bd63f65bb21ff4a0d7f1cec6
sudo bash -ceu '
  state=/var/lib/asc-ctl/pr31
  install -d -o root -g root -m 0755 "$state"
  test ! -e "$state/audio"
  scratch=$(mktemp -d "$state/.audio.XXXXXXXX")
  trap '"'"'rm -rf -- "$scratch"'"'"' EXIT
  install -o root -g root -m 0600 "$1" "$scratch/bundle.tar"
  printf "%s  %s\n" "$2" "$scratch/bundle.tar" | sha256sum -c -
  mkdir "$scratch/audio"
  tar --no-same-owner --no-same-permissions -C "$scratch/audio" -xf "$scratch/bundle.tar"
  chmod -R a+rX,go-w "$scratch/audio"
  cd /usr/local/sbin
  printf "%s  %s\n" 84a8a2bad2dfa62c7aa26959c00742f6fc68b0d67e5bd9b304da63e2a1acdf09 asc-ctl | sha256sum -c -
  test ! -e asc-ctl.pre-pr31
  install -o root -g root -m 0600 "$3" asc-ctl.pr31.new
  printf "%s  %s\n" "$4" asc-ctl.pr31.new | sha256sum -c -
  install -o root -g root -m 0755 asc-ctl asc-ctl.pre-pr31
  mv -T "$scratch/audio" "$state/audio"
  chmod 0755 asc-ctl.pr31.new
  mv -T asc-ctl.pr31.new asc-ctl
' _ "$AUDIO_ARCHIVE" "$AUDIO_SHA" "$SOURCE" "$EXPECTED"
sudo -n /usr/local/sbin/asc-ctl version
```

### Atualizar a 1.4.0 instalada / Update an existing 1.4.0 installation

Com o bundle já instalado na 201, substitua somente o binário usando o checkout revisado da PR #32. Para desfazer a atualização, restaure `asc-ctl.pre-pr31-peer-health` com os comandos abaixo.

With the bundle already installed on host 201, replace the executable from the reviewed PR #32 checkout. To undo the update, restore `asc-ctl.pre-pr31-peer-health` using the commands below.

```bash
SOURCE=/absolute/path/to/reviewed-checkout/infrastructure/admin/asc-ctl
EXPECTED=3491833a450eeb1e4060520335806969dbb96871bd63f65bb21ff4a0d7f1cec6
sudo bash -ceu '
  cd /usr/local/sbin
  test ! -e asc-ctl.pre-pr31-peer-health
  tmp=$(mktemp .asc-ctl.XXXXXXXX)
  trap "rm -f -- \"$tmp\"" EXIT
  install -o root -g root -m 0755 "$1" "$tmp"
  printf "%s  %s\n" "$2" "$tmp" | sha256sum -c -
  install -o root -g root -m 0755 asc-ctl asc-ctl.pre-pr31-peer-health
  mv -T "$tmp" asc-ctl
' _ "$SOURCE" "$EXPECTED" &&
  sudo -n /usr/local/sbin/asc-ctl bench-pr31 --check
```

```bash
sudo bash -ceu '
  cd /usr/local/sbin
  install -o root -g root -m 0755 asc-ctl.pre-pr31-peer-health asc-ctl.rollback.new
  mv -T asc-ctl.rollback.new asc-ctl
'
```

### Verificação / Preflight

```bash
sudo -n /usr/local/sbin/asc-ctl bench-pr31 --check
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
