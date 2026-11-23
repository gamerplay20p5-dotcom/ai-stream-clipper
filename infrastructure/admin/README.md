# asc-ctl: permissão limitada para o AI Stream Clipper

O usuário `asc` não entra no grupo `docker`, porque isso equivale a root. Em vez disso, ele pode rodar **apenas** `/usr/local/sbin/asc-ctl` via sudo. Esse script, de propriedade do root, só executa operações sobre o projeto Docker `asc`, e só depois de validar a configuração contra uma política de isolamento.

## Instalação (administrador, uma vez por servidor)

```bash
sudo bash /home/asc/ai-stream-clipper/infrastructure/admin/install-asc-ctl.sh <201|206|210> <sha256>
```

O SHA-256 esperado é informado junto com cada versão. O instalador confere o hash antes de copiar o script.

## O que o asc pode fazer

| Comando | Efeito |
|---|---|
| `sudo asc-ctl status` | Visão somente leitura: carga, memória, disco, todos os contêineres, uso da GPU |
| `sudo asc-ctl compose up\|down\|stop\|start\|restart\|ps\|logs\|config [--gpu] [serviço...]` | Só no projeto `asc`, só com o Compose deste servidor, só se passar na política. `down` nunca apaga volumes |
| `sudo asc-ctl build app\|vision TAG` | Compila imagens `ai-stream-clipper*` a partir do repositório em `/home/asc` |
| `sudo asc-ctl image-save / image-load / images / image-rm` | Exporta e importa só imagens `ai-stream-clipper*` (o conteúdo do arquivo é inspecionado antes) |
| `sudo asc-ctl env-install FILE` / `env-show` / `set-tag TAG` | Configuração em `/etc/asc/asc.env` (root 600), só com variáveis permitidas |
| `sudo asc-ctl tls-init` (210) / `tls-install-ca FILE` | CA e certificado HTTPS próprios do projeto |
| `sudo asc-ctl init` / `models` / `gpu-validate` / `test-media-add` / `backup-db` | Diretórios em `/opt/asc`, download de modelos, benchmark da GPU, vídeos de teste, backup do banco |
| `sudo asc-ctl bench-pr31 [--check]` (201, 1.4.0+) | Replay isolado da PR #31. Veja o [runbook PT-BR/EN](bench-pr31.md) para preparação, instalação manual e rollback |
| `sudo asc-ctl restore-check FILE` (212, 1.2.2+) | Restaura um backup num PostgreSQL descartável (sem rede, sem porta, removido no fim) e compara as contagens de cada tabela com o banco |
| `sudo asc-ctl exec asc-<contêiner> -- comando` | Só dentro de contêineres do projeto `asc` |

## O que a política impede

Modo privilegiado, capacidades extras, rede/PID/IPC do host, socket do Docker, montagens fora de `/opt/asc` e `/etc/asc/tls`, volumes ou redes de outros projetos, portas diferentes de 8780/8781 ou fora do IP interno do servidor, outros dispositivos além do `renderD128`, grupos além de `render`, imagens de terceiros, serviços sem limite de CPU e RAM, `prune` e `down -v`.

Toda chamada fica registrada em `/var/log/asc-ctl.log`.

## Remoção

```bash
sudo rm /etc/sudoers.d/asc-ctl /usr/local/sbin/asc-ctl
```

Os contêineres do projeto podem ser parados antes com `sudo asc-ctl compose down`.
