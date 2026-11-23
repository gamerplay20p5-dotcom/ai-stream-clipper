# Redesign premium: evidências antes e depois (23/11/2026)

Capturas reais, feitas com os mesmos scripts antes e depois:
- landing: `commercial/browser-tests/capture.mjs`;
- área do cliente: `dashboard/scripts/ux/account_capture.py`, sobre a API e o painel reais, com SQLite temporário.

O conjunto completo (PT e EN; 390, 768, 1440 e 1920 px; primeira tela e página inteira) ficou fora do repositório,
em `Desktop/redesign-evidencias/`.

| Tela | Antes | Depois |
| --- | --- | --- |
| Landing, 1440 px | `landing-1440-antes.webp` | `landing-1440-depois.webp` |
| Landing, 390 px | `landing-390-antes.webp` | `landing-390-depois.webp` |
| Landing inteira, 1440 px | | `landing-1440-pagina-inteira-depois.webp` |
| Minha assinatura sem plano, 1440 px | `conta-planos-1440-antes.webp` | `conta-planos-1440-depois.webp` |
| Minha assinatura sem plano, 390 px | `conta-planos-390-antes.webp` | `conta-planos-390-depois.webp` |
| Minha assinatura com Creator ativo, 1440 px | `conta-ativa-1440-antes.webp` | `conta-ativa-1440-depois.webp` |

## Desempenho da landing

Chromium, mediana de 3 cargas a frio, sem cache, servidor local. Perfis:
- celular: 390×844, CPU 4x mais lenta e rede tipo 4G rápida;
- desktop: 1440×900, sem limitação.

Os números completos estão em `desempenho-antes.json` e `desempenho-depois.json`.

| Métrica | Celular antes | Celular depois | Desktop antes | Desktop depois |
| --- | --- | --- | --- | --- |
| FCP / LCP | 2984 ms | 912 ms | 660 ms | 212 ms |
| Total Blocking Time | 1106 ms | 103 ms | 70 ms | 77 ms |
| CLS | 0 | 0 | 0,011 | 0 |
| HTML (transferido) | 111 KB | 78 KB | 111 KB | 78 KB |
| JavaScript (transferido) | 285 KB | 287 KB | 285 KB | 287 KB |
| CSS (transferido) | 39 KB | 49 KB | 39 KB | 49 KB |

O ganho principal veio da cena do hero. Os prédios e as janelas eram centenas de elementos SVG hidratados pelo
React e agora são dois `path`. As animações usam só `transform` e `opacity`, e o desfoque fica restrito à barra do
cabeçalho.

O servidor local não comprime as respostas; em produção a Cloudflare comprime, e os KB transferidos caem.

Na área do cliente, o JavaScript do painel passou de 222,6 KB para 225,6 KB com gzip. A fonte Space Grotesk só é
baixada nas telas do cliente que a usam.

## Prévia local (sem produção)

```
# landing: http://127.0.0.1:8793
cd commercial && npm ci && npm run build && COMMERCIAL_PORT=8793 node server/index.js

# Minha assinatura: http://127.0.0.1:8794/login
# Contas: sem-plano@, creator@, pro@ e atrasado@exemplo.test; senha: senha-de-teste-ux-9
cd dashboard && npm ci && npm run build && cd .. && python dashboard/scripts/ux/account_stack.py --port 8794
```
