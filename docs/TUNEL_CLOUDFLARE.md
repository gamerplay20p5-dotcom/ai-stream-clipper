# Acesso público: `clipper.howto.software`

## Situação atual (18/11/2026)

| Item | Estado verificado |
|---|---|
| DNS | `howto.software` com nameservers na Cloudflare (`ruth`, `giancarlo`); `clipper.howto.software` → A `136.34.124.155`, **DNS only** (sem proxy da Cloudflare) |
| TLS | certificado **Let's Encrypt** válido para `clipper.howto.software` (expira em 16/02/2027), emitido pelo proxy do servidor |
| Quem atende | `caddy-proxy` do Hank (Caddy + CrowdSec) no 210 → API do Clipper em `192.168.1.210:8780` (HTTPS) |
| Túnel Cloudflare `asc-clipper-210` (FriendHost) | **aposentado**: contêiner `asc-tunnel` parado; o domínio novo **não depende dele** (responde com o túnel parado) |

O painel inteiro fica acessível pelo domínio. A segurança está na aplicação:
- **Rotas privadas:** `/api/*` exige sessão (cookie `HttpOnly`, `Secure`, `SameSite=strict`) e cabeçalho CSRF; o resto responde 401.
- **Login:** limitado a 10 falhas em 5 minutos **por IP real**. Desde a v0.4.4, o `X-Forwarded-For` só é aceito quando vem do proxy local (rede Docker `172.16.0.0/12`). Antes, todos os visitantes da internet apareciam com o mesmo IP do proxy.
- **`/internal/*`:** a API dos workers responde 404 para quem chega pelo domínio. Os workers usam a rede local ou a rede Docker.
- **Produção:** `/api/openapi.json` e `/api/docs` estão desligados, e o HSTS está ativo (sem `includeSubDomains`, para não afetar outros subdomínios do Hank).
- **Callback OAuth:** público, autenticado pelo `state` cifrado, de uso único, válido por 15 minutos e criado por um admin logado.

## Callbacks OAuth

`https://clipper.howto.software/api/publish/oauth/<instagram|tiktok|youtube>/callback`

O YouTube continua usando o retorno `http://127.0.0.1:8765/` (cliente "App para computador"); nada muda para ele.

## Limpeza do ambiente FriendHost

| Componente | Estado | Ação |
|---|---|---|
| CNAME `clipper.friendhost.com.br` | não existe (NXDOMAIN) | nenhuma |
| `friendhost.com.br` | nameservers de volta no Registro.br (`*.sec.dns.br`); site (Vercel) e TXT do Google respondendo | **reativar o DNSSEC no Registro.br**: hoje o DS não existe mais |
| Contêiner `asc-tunnel` | parado (`Exited`) | pode continuar parado (perfil `tunnel`, não sobe sozinho) |
| Token `/etc/asc/tunnel/token` | removido na v0.4.4 com `asc-ctl tunnel-token-remove` | nenhuma |
| Túnel `asc-clipper-210` na conta pessoal da Cloudflare | sem conector (token removido) | apagar no painel: Zero Trust → Networks → Tunnels → `asc-clipper-210` → Delete |

O suporte a túnel do asc-ctl 1.1.0 continua disponível caso um túnel volte a ser necessário (`tunnel-token-install`, `compose up --tunnel`). O token é sempre lido por stdin e montado como arquivo `0400`.
