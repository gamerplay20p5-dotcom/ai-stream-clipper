# AI Stream Clipper by HTS | Landing comercial

O serviço comercial roda isolado do painel e dos workers. A demonstração usa uma cena fictícia original, com três estados interativos, da captura ilustrativa à prévia com legendas.

## Rodar localmente

Com Node.js 24, a partir de `commercial/`:

```sh
npm ci
npm test
npm run build
npm start
```

A landing e o formulário ficam em `http://127.0.0.1:8790`. Para trabalhar no frontend com recarga automática, mantenha o servidor aberto e rode `npm run dev` em outro terminal. O Vite encaminha `/api/waitlist` ao serviço comercial.

Use `?lang=pt-BR&market=BR` ou `?lang=en&market=US` para conferir as combinações iniciais. Idioma e mercado são independentes. Sem parâmetros, o idioma vem do navegador e define o mercado inicial. O seletor de moeda usa a tabela fixa do mercado, sem conversão cambial.

## Lista de espera

`POST /api/waitlist` recebe JSON com `email`, `plan`, `country`, `otherCountry` (quando `country` é `OTHER`), `platform`, `market`, `language` e `consent`. A resposta `202` confirma o envio. Repetir o mesmo e-mail preserva a primeira inscrição e retorna o mesmo status.

O padrão é `commercial/data/waitlist.sqlite`. O banco guarda os dados de contato, as escolhas, a versão do aceite e o horário UTC do registro. A consulta da tabela `waitlist` é feita com acesso autorizado ao arquivo do banco. O serviço não expõe uma rota pública de consulta nem envia mensagens automaticamente.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `COMMERCIAL_DB` | `data/waitlist.sqlite` | Caminho do SQLite, relativo a `commercial/` ou absoluto. |
| `COMMERCIAL_PORT` | `8790` | Porta do serviço e destino do proxy de desenvolvimento. |
| `COMMERCIAL_HOST` | `127.0.0.1` | Interface de escuta do serviço. |
| `COMMERCIAL_TRUST_PROXY` | vazio | IP ou sub-rede do proxy confiável, quando aplicável. |

Para uma futura publicação, use um volume persistente fora da raiz servida e proteja o acesso ao arquivo e aos backups. Configure HTTPS no proxy. Ao ativar `COMMERCIAL_TRUST_PROXY`, restrinja o acesso direto ao serviço e configure o proxy para sobrescrever os cabeçalhos de encaminhamento recebidos do cliente. O limite de inscrições é por IP e por processo.

## Cadastro e cobrança futuros

`catalog.json` concentra os IDs estáveis dos planos, franquias, mercados e valores mensais em centavos. O formulário salva esses IDs como **interesse**, sem criar conta, reserva ou assinatura. Uma integração futura pode associar esses IDs aos produtos de cobrança e usar a lista de espera para convidar criadores. O preço mostrado na landing é provisório.

As franquias e os recursos seguem a proposta revisada do [estudo comercial do PR #17](https://github.com/gamerplay20p5-dotcom/ai-stream-clipper/pull/17). Kick é uma opção de pesquisa no formulário.
