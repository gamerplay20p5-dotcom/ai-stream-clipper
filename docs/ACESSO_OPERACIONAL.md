# Acesso operacional aos servidores (deploy sem senha, com permissões mínimas)

O operador (o Claude Code no Windows do dono) administra o AI Stream Clipper nos quatro servidores sem senha. O acesso
se limita ao executor restrito do projeto e não usa root amplo.

## Como funciona

| Item | Configuração |
| --- | --- |
| Chave | `%USERPROFILE%\.ssh\id_ed25519_asc` (ED25519, comentário `ai-stream-clipper-deploy`), só no PC do dono. Fica protegida pelas permissões do Windows e nunca vai para o repositório |
| Conta nos servidores | `asc`, a dona do projeto em 201, 206, 210 e 212, com login só por essa chave |
| Privilégio | `/etc/sudoers.d/asc-ctl`: `asc ALL=(root) NOPASSWD: /usr/local/sbin/asc-ctl` e nada mais |
| Executor restrito | `asc-ctl`, arquivo do root (0755) instalado com conferência de SHA-256. Só opera o projeto Docker `asc` e passa cada operação pela política de isolamento (ver `infrastructure/admin/README.md`). Toda chamada fica registrada em `/var/log/asc-ctl.log` |
| Contas administrativas | `malaio` (201, 206, 210) e `hcahill` (212) **não** recebem a chave: o sudo delas é root irrestrito. Ficam só para os atos de administrador (instalar um `asc-ctl` novo) |

## O que dá para fazer sem senha

- **Deploy:** `set-tag`, `compose up`, `stop`, `start` e `restart` (com `--gpu` e `--tunnel` onde se aplica), `build`,
  `image-save`, `image-load`, `images` e `asc-send` (só a 206 envia imagens para a 210 e a 201).
- **Logs e estado:** `compose ps`, `compose logs`, `status` (só leitura, inclusive de outros projetos).
- **Banco (212):**
  - `backup-db`;
  - `restore-check`, a restauração de um backup num PostgreSQL descartável, sem rede e sem porta, com comparação
    tabela a tabela (asc-ctl 1.2.2);
  - consultas pelo `exec asc-postgres -- psql`.
- **Migrações:** ao iniciar a API, ou com `exec asc-api -- clipper migrate`.
- **Pausa de publicações:** `exec asc-api -- clipper publish-pause on|off`.

## O que continua exigindo o administrador

- **Trocar o `asc-ctl`:** é root e precisa do hash informado. O comando é
  `sudo bash <pasta com asc-ctl e install-asc-ctl.sh>/install-asc-ctl.sh <servidor> <sha256>`.
- **`/etc/asc/asc.env`:** gerar a configuração com segredos novos (o `env-install` instala; quem cria o arquivo é o
  administrador ou o dono).
- **Fora do projeto `asc`:** qualquer coisa nos outros projetos do Hank.

## Versões do `asc-ctl`

| Servidor | Versão | SHA-256 |
| --- | --- | --- |
| 201, 206 | 1.2.0 | `84a8a2bad2dfa62c7aa26959c00742f6fc68b0d67e5bd9b304da63e2a1acdf09` |
| 210 | 1.2.1 (aceita `ASC_PUBLIC_ORIGIN`) | `ff4539e7497f874cc81789967b0ed35026eef0b9710351143810ae291c239533` |
| 212 | 1.2.2 (acrescenta `restore-check`) | `c3efa1bda702bf1e319ddcbd770003d85b0c531d57be37061fb1e8fc8af7bb62` |

## Revogar

Use a parte que for necessária:

1. **Tirar o acesso do PC:** apague `%USERPROFILE%\.ssh\id_ed25519_asc` e o `.pub` correspondente.
2. **Tirar a chave de um servidor:** remova a linha `ai-stream-clipper-deploy` de `/home/asc/.ssh/authorized_keys`.
   Isso pode ser feito como administrador ou como `asc`.
3. **Tirar o privilégio:** `sudo rm /etc/sudoers.d/asc-ctl /usr/local/sbin/asc-ctl` (administrador). Os contêineres
   continuam rodando. Os dados, os volumes, `/opt/asc` e `/etc/asc` ficam intactos.
4. **Auditoria:** o histórico do que foi feito está em `/var/log/asc-ctl.log` de cada servidor.

## English

- **Access:** passwordless operations run as `asc`, with a dedicated ED25519 key that lives only on the owner's PC.
- **Privilege:** sudo covers only the root-owned, hash-checked `asc-ctl` executor, which is limited to the `asc` Docker
  project and audited in `/var/log/asc-ctl.log`.
- **Admin accounts:** they do not get the key. Installing a new `asc-ctl` or creating secret config files stays an
  administrator action.
- **asc-ctl 1.2.2** adds `restore-check`, an isolated restore test of a backup that removes only its own container.
- **Revoke:** delete the key, remove the `authorized_keys` line, or remove `/etc/sudoers.d/asc-ctl` and the `asc-ctl`
  binary.
