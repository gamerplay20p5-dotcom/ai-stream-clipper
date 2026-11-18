# Cloudflare Tunnel `asc-clipper-210` (callback OAuth público)

**Objetivo:** expor na internet **somente** `https://clipper.friendhost.com.br/api/publish/oauth/<plataforma>/callback`. O painel e o resto da API continuam apenas na rede local.

**Situação em 18/11/2026:** túnel **não funcional**. A delegação DNS para a Cloudflare ainda não foi feita e o conector ainda não foi instalado.

## 1. DNS: estado atual (consultado em DNS público)

| Registro | Valor atual | O que fazer na zona da Cloudflare **antes** de trocar os nameservers |
|---|---|---|
| NS | `d.sec.dns.br`, `f.sec.dns.br` (Registro.br) | trocar no Registro.br pelos 2 nameservers que a Cloudflare indicar (último passo) |
| **DS (DNSSEC)** | **ativo** (`24706 13 2 …`) | **desativar o DNSSEC no Registro.br antes da troca** (ver abaixo) |
| A `@` | `216.198.79.1` (Vercel) | A `@` → `216.198.79.1`, **DNS only** (nuvem cinza) |
| CNAME `www` | `e127341caf411806.vercel-dns-017.com` | CNAME `www` → mesmo destino, **DNS only** |
| TXT `@` | `google-site-verification=qihbGXf3…` | copiar idêntico |
| MX / SPF / DMARC / DKIM | **nenhum** | nada a preservar: hoje o domínio não recebe e-mail |
| CNAME `clipper` | só na Cloudflare | criado automaticamente pelo túnel (proxied, nuvem laranja) |

A consulta pública não lista subdomínios desconhecidos. Antes da troca, confira no painel DNS do Registro.br se existe algum registro além desses e copie-o também.

**DNSSEC:** com o DS do Registro.br ativo, os resolvedores que validam DNSSEC rejeitam as respostas assinadas pela Cloudflare, e o site sai do ar. A ordem segura é:
1. No Registro.br, desative o DNSSEC do domínio e espere cerca de 2 h (TTL do DS: 3600 s, mais margem).
2. Troque os nameservers pelos da Cloudflare e espere a zona ficar **Active**.
3. Na Cloudflare, em DNS → Settings, ative o **DNSSEC**. Copie o registro DS gerado e cadastre-o no Registro.br.

## 2. Instalação do conector (administrador, no 210)

**Modelo de segurança:**
- **Imagem:** o conector roda como o serviço `asc-tunnel` com a imagem oficial `cloudflare/cloudflared`, em versão fixada (padrão `2025.4.0`, a primeira com `--token-file`).
- **Contêiner restrito:**
  - perfil `tunnel`, que só sobe quando pedido explicitamente;
  - sem nenhuma porta publicada, só conexões de saída;
  - sistema de arquivos somente leitura, `cap_drop ALL`;
  - 0,5 CPU e 256 MB.
- **Token:**
  - fica num arquivo `0400` em `/etc/asc/tunnel/token`, instalado pelo asc-ctl a partir da **entrada padrão (stdin)**;
  - nunca aparece em argumento, variável de ambiente, `ps`, log ou Git.
- **Política do asc-ctl 1.1.0:**
  - aceita o `cloudflared` apenas com esse formato exato, apenas no 210 e apenas com versão fixada (`latest` é recusado);
  - a pasta do token só pode ser montada pelo serviço do túnel.

**Comandos (como root no 210):**
```bash
# 1. atualizar o asc-ctl para 1.1.0 (inclui as correções da 1.0.1)
sudo bash /home/asc/ai-stream-clipper/infrastructure/admin/install-asc-ctl.sh 210 <sha256 do asc-ctl na versão publicada>

# 2. instalar o token SEM colocá-lo na linha de comando: cole quando pedir (não aparece na tela)
sudo asc-ctl tunnel-token-install
#    alternativa: sudo asc-ctl tunnel-token-install < /caminho/arquivo-com-o-token  (e apague o arquivo depois)

# 3. baixar a imagem fixada e subir só o túnel
sudo asc-ctl compose pull --tunnel tunnel
sudo asc-ctl compose up --gpu --tunnel
sudo asc-ctl compose logs tunnel --tail 50     # esperar "Registered tunnel connection"
```

Para remover: `sudo asc-ctl compose stop tunnel` e depois `sudo asc-ctl tunnel-token-remove`.

## 3. Rotas e TLS da origem (painel da Cloudflare → Zero Trust → Tunnels → asc-clipper-210)

**Public hostname:**
- hostname: `clipper.friendhost.com.br`
- path: `^/api/publish/oauth/(instagram|tiktok|youtube)/callback$`
- **Service:** `HTTPS` → `api:8780`, que é o nome da API dentro da rede do projeto

**Configurações TLS da origem** (Additional application settings → TLS):
- **Origin Server Name:** `api` (o certificado do asc-ctl tem `DNS:api` como nome alternativo)
- **CA Pool:** `/etc/asc/tls/ca.crt` (a CA do projeto, montada somente leitura no conector)
- **No TLS Verify:** **desligado**. A origem é validada pela CA do projeto, sem desativar a verificação.

**Catch-all:** HTTP status **404**, para todo o resto.

**Avaliação de segurança:**
- O callback não depende do cookie do painel. Ele é autenticado pelo `state` de uso único, válido por 15 minutos e criado por um admin logado.
- Depois do callback, o navegador volta para o painel da rede local, de onde a autorização começou.
- O cookie do painel continua `SameSite=strict`.

## 4. Validação (só declarar funcional depois de tudo verde)

1. `dig NS friendhost.com.br` retorna os nameservers da Cloudflare, e a zona aparece **Active**.
2. O site antigo responde em `https://friendhost.com.br` e `https://www.friendhost.com.br`.
3. `asc-ctl compose logs tunnel` mostra conexões registradas, e o túnel aparece **Healthy** no painel.
4. `https://clipper.friendhost.com.br/` responde **404**, e `/api/publish/oauth/instagram/callback?state=x&code=y` responde **303** para `/publicacao?erro=…`. Isso prova que o pedido chegou à API pelo túnel e foi recusado pelo `state`.
5. Conexão real: no painel, configure o Instagram com redirect `https://clipper.friendhost.com.br/api/publish/oauth/instagram/callback` (o mesmo cadastrado na Meta) e clique em **Conectar**.
