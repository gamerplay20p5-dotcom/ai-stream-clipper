# Redesign cinematográfico da landing comercial

Branch `redesign/commercial-cinematic`, criada a partir de `redesign/commercial-broadcast`. Substitui a direção editorial (bege, verde e âmbar) e a direção Broadcast Studio (ciano). O sistema visual está em [DESIGN.md](../DESIGN.md).

## Relação com as branches e o PR #18

| Branch | Situação |
| --- | --- |
| `feat/commercial-landing` | PR #18 aberto: primeira landing e lista de espera |
| `redesign/commercial-editorial` | Redesign editorial (bege e verde), rejeitado |
| `redesign/commercial-broadcast` | Redesign Broadcast Studio (ciano). Estava sem commit e foi preservado no commit `d97d571`, que faz parte do histórico desta branch |
| `redesign/commercial-cinematic` | Esta entrega, sobre o histórico anterior |

O backend da lista de espera (`server/`) e o catálogo (`catalog.json`) não foram alterados.

## Comparação com a referência aprovada

O mockup aprovado está versionado em [reference.webp](qa/cinematic/reference.webp).

| Área | Comparação |
| --- | --- |
| Primeira tela no tamanho do mockup (1536×1024) | [compare-1536.webp](qa/cinematic/compare-1536.webp) |
| Hero em 1440 px | [compare-hero.webp](qa/cinematic/compare-hero.webp) |
| Planos | [compare-plans.webp](qa/cinematic/compare-plans.webp) |

### Critério reproduzível

[browser-tests/reference.spec.js](../browser-tests/reference.spec.js) roda no viewport do mockup (1536×1024) e compara com o arquivo de referência.

**Estrutura:**
- texto à esquerda;
- janela do produto à direita, com o Short ultrapassando o topo dela;
- benefícios dentro da primeira tela;
- título em duas linhas;
- CTAs na mesma linha;
- navegação na ordem do mockup;
- três planos alinhados.

**Tokens computados no navegador:**
- fundo `#090B10`;
- texto `#F5F6FA` e `#ADB1C0`;
- destaque e CTA `#FF4F62` com texto branco;
- borda do Creator coral;
- superfície `#151722`.

**Pixels:** a primeira tela (cabeçalho, hero e benefícios) é normalizada para a região equivalente do mockup. O teste calcula a similaridade da cor média numa grade de 12×4 células e a sobreposição da distribuição de paleta (escuros, coral, magenta e roxo, quentes e outros). A versão editorial rejeitada serve de controle negativo.

| Versão | Similaridade da grade | Sobreposição de paleta |
| --- | --- | --- |
| Cinematográfica (esta) | 0,944 | 0,890 |
| Editorial rejeitada | 0,461 | 0,269 |

Limites do teste: grade > 0,90 e paleta > 0,85, e cada métrica precisa superar o controle por pelo menos 0,10 e 0,25. Os valores medidos ficam em `test-results/**/reference-fidelity.json`.

O que segue o mockup:
- **Hero:**
  - cabeçalho escuro com PT-BR/EN e CTA coral;
  - selo de pré-lançamento, título em duas linhas com a segunda em coral e CTAs principal e secundário;
  - janela do produto com barra lateral, live, waveform com trecho, miniaturas, lista "Detecção da IA" e "Gerar corte";
  - Short vertical sobreposto à direita;
  - céu ao entardecer com skyline, palmeiras e helicóptero;
  - faixa de quatro benefícios.
- **Seções:**
  - "Como funciona" com quatro etapas, ícones e conectores;
  - "/ Planos para o seu ritmo de live" com seletor BRL/USD, três cards, Creator destacado e Pro com coroa.

Refinamentos em relação ao mockup:
- Os botões usam exatamente `#FF4F62` com texto branco em tamanho grande (≥ 18,67 px em negrito), o que atende o WCAG AA para texto grande. Selos e o CTA do cabeçalho, que são menores, usam texto escuro.
- O selo do Creator diz **"Recomendado"**, não "Mais escolhido". Ainda não há clientes, e a frase seria uma afirmação sem dado.
- O "VI" grande do mockup foi omitido. É o logotipo de GTA VI, e o pedido proíbe usar marcas protegidas sem autorização. Qualquer outra arte do jogo também ficou de fora.
- Os marcadores de acerto viraram coral, no lugar do verde, para não introduzir uma cor fora da paleta.
- "Como funciona" virou uma demonstração interativa ao lado do estúdio. Os planos ficaram numa seção própria com comparação completa, em vez de dividirem a mesma faixa. Com as duas lado a lado, a demonstração ficaria pequena demais para operar.

## Capturas

As capturas são reais, feitas com Playwright no Chromium a partir do build de produção, em `docs/qa/cinematic/`:

| Largura | Primeira tela | Página inteira |
| --- | --- | --- |
| 375 px | [fold-375](qa/cinematic/fold-375.webp) | [full-375](qa/cinematic/full-375.webp) |
| 390 px | [fold-390](qa/cinematic/fold-390.webp) | [full-390](qa/cinematic/full-390.webp) |
| 768 px | [fold-768](qa/cinematic/fold-768.webp) | [full-768](qa/cinematic/full-768.webp) |
| 1440 px | [fold-1440](qa/cinematic/fold-1440.webp) | [full-1440](qa/cinematic/full-1440.webp) |
| 1920 px | [fold-1920](qa/cinematic/fold-1920.webp) | [full-1920](qa/cinematic/full-1920.webp) |

Estados da demonstração, em 1440 e 390 px:
- [Analisar](qa/cinematic/demo-analyze-1440.webp) ([390](qa/cinematic/demo-analyze-390.webp))
- [Editar](qa/cinematic/demo-edit-1440.webp) ([390](qa/cinematic/demo-edit-390.webp))
- [Revisar](qa/cinematic/demo-review-1440.webp) ([390](qa/cinematic/demo-review-390.webp))

Correções feitas a partir da inspeção das capturas:
- miniaturas esticadas;
- título em quatro linhas e, em 1920 px, sobreposto ao preview (agora verificado automaticamente);
- card do Short cobrindo as notas da detecção;
- céu escuro demais e nuvens chapadas;
- excesso de espaço vazio acima do hero;
- rótulo "Momento selecionado" cortado;
- CTA do cabeçalho sem estilo;
- "PT-BR" quebrando em duas linhas;
- 17 px de rolagem horizontal em 320 px;
- marcador duplicado em Planos;
- ponto final nos rótulos do formulário.

## Mídia

Tudo é original. Nada vem de GTA VI, da Rockstar Games ou de capturas reais de clientes.

| Arquivo | Origem |
| --- | --- |
| `live-wide*.webp` | Imagem gerada pelo ForgeGUI (prompt abaixo). O emblema traseiro do carro foi desfocado localmente. Os letreiros mostram texto ilegível |
| `short-vertical.webp`, `short-demo.mp4` | Recorte 9:16 e aproximação lenta (5 s, sem áudio) da mesma imagem, gerados com ImageMagick e ffmpeg |
| `moment-chase.webp`, `moment-palms.webp`, `short-palms.webp` | Outros momentos da mesma live: aproximação no carro, e a avenida espelhada com inclinação e tom mais rosado. Mantêm o mesmo carro e a mesma cidade em toda a página |
| Cena do hero | SVG desenhado no código (`HeroScene`) |
| `short-pt.vtt`, `short-en.vtt` | Legendas da demonstração |

Prompt da imagem: *original open-world video game capture still, 16:9, third-person chase camera behind a sleek dark cherry-red 1990s sports coupe speeding down a wide wet boulevard at night in a fictional tropical city; neon signs in magenta and coral, palm trees, art-deco buildings, purple night sky; no text, logos, plate text, HUD, watermark, people or police*.

Outras três imagens planejadas (céu, segundo ângulo e controle) não foram geradas porque os créditos do ForgeGUI acabaram. O céu virou o SVG. Os outros ângulos saem da imagem gerada, sempre com o mesmo carro vermelho.

A demonstração e o preview trazem o aviso "Demonstração ilustrativa". Notas como 92 e 9,1, canais e armazenamento são valores de exemplo, marcados como tal. Os recursos mostrados existem no produto: monitoramento de canais, detecção multimodal, recorte vertical com legendas, revisão, biblioteca com nota, limpeza por nota e exportação.

## Validação local

| Verificação | Resultado |
| --- | --- |
| `npm run build` (tsc, Vite e pré-render das 4 combinações) | Aprovado |
| `npm test` (HTTP: validação, deduplicação, payload, formato, rate limit e persistência) | Aprovado |
| `npx playwright test` em Chromium, Firefox e WebKit | 21 aprovados, incluindo o teste de fidelidade à referência. Os ignorados são intencionais: capturas, métricas e comparação rodam só no Chromium |
| axe-core, WCAG A/AA 2.0, 2.1 e 2.2 | Zero violações em PT-BR desktop, EN nas etapas Revisar, Editar e Analisar, menu mobile aberto e fechado |
| Sem ponto final em títulos, subtítulos, cards, botões, navegação, rótulos e legendas | Verificado em PT-BR e EN, de 320 a 1920 px |
| Rolagem horizontal | Zero em 320, 375, 390, 768, 1024, 1280, 1440, 1600 e 1920 px |
| Título sobre o preview | Sem sobreposição nas mesmas larguras |
| `npm audit` | 0 vulnerabilidades |

Detalhes:
- **Interações testadas:**
  - abas da demonstração por clique e teclado, com setas verticais e horizontais;
  - escolha de momento, que troca a imagem e o trecho na timeline;
  - posição do recorte, que move a guia e a prévia 9:16;
  - legendas ligadas e desligadas;
  - aprovar e descartar, com anúncio em `role="status"`;
  - reprodução do MP4 com faixa de legenda no idioma;
  - menu mobile com foco preso, Escape e retorno de foco;
  - comparação mobile, FAQ, idioma e moeda independentes e preservados na URL.
- **Lista de espera:**
  - a confirmação espera a resposta 202 e o registro real é lido do SQLite temporário;
  - os erros 422, 429 e 503 mantêm os campos preenchidos.
- **Estabilidade:** com 9 projetos rodando em paralelo, duas falhas intermitentes apareceram:
  - o vídeo não carregava no WebKit com `preload="none"`;
  - um teste do Firefox estourava os 30 s.
  - Corrigi com `preload="metadata"`, reprodução em mudo no teste e limite de 60 s. Depois disso, a suíte completa passou em 3 execuções seguidas.
- **Peso:**
  - JavaScript 86 kB e CSS 8,6 kB, com gzip;
  - imagem principal de 30 kB no celular e 95 kB no desktop;
  - o MP4 de 172 kB fica fora da carga inicial: o painel Revisar só é montado quando o visitante abre a etapa, e então pede apenas os metadados.
  - Os valores são de laboratório em localhost.

## Segurança

- Nenhuma alteração em `server/`. Validação, limite de 4 kB, JSON obrigatório, rate limit por IP, campo antirrobô, consultas parametrizadas, resposta idêntica para e-mail repetido e CSP continuam iguais.
- Os preços e as franquias exibidos vêm de `catalog.json`. O servidor valida o plano contra o mesmo catálogo e registra apenas interesse. Nenhum valor do frontend vira preço, assinatura, cota ou permissão.
- A demonstração não usa dados reais, URLs internas, tokens ou nomes de clientes. Sem billing, sem checkout e sem novas rotas.

## Inconsistências registradas para revisão

1. **Lives simultâneas do Creator:** `catalog.json` define 2. O estudo comercial v2 (PR #17) recomendava 1 no Basic e no Creator, deixando 2 só no Pro, para proteger a capacidade. A landing exibe o catálogo (2) e o mockup também. Precisa de decisão comercial antes da publicação.
2. **Selo "Mais escolhido"** do mockup substituído por "Recomendado", até haver dados de clientes.
3. **Privacidade e termos** continuam como documentos em preparação, como antes. Precisam ser publicados antes do lançamento.

## Descrição do PR

### Português

Reconstrói a landing comercial na direção cinematográfica aprovada: noite, coral elétrico, luz magenta e roxa, e o software como protagonista. O hero mostra a live, o trecho detectado, os momentos encontrados pela IA e o Short vertical. "Como funciona" é uma demonstração interativa em quatro etapas: monitorar, analisar, editar e revisar. Recursos são fragmentos da interface, e os planos seguem o mockup com preços e franquias do catálogo. Inclui um design system documentado, mídia original e capturas reais em 375 a 1920 px, com comparação lado a lado. Validado com build, testes HTTP, Playwright em 3 navegadores, axe WCAG 2.2 AA e npm audit. Sem mudanças no backend, sem billing, sem merge e sem deploy.

### English

Rebuilds the commercial landing page in the approved cinematic direction: night-time, electric coral, magenta and violet light, with the software as the hero. The first screen shows the stream, the detected clip, the moments found by AI and the vertical Short. "How it works" is an interactive four-step demo: monitor, analyze, edit and review. Features are shown as fragments of the interface, and the plans follow the mockup using prices and allowances from the catalog. Includes a documented design system, original media, and real screenshots from 375 to 1920 px with side-by-side comparisons. Validated with the build, HTTP tests, Playwright across three browsers, axe WCAG 2.2 AA and npm audit. No backend changes, no billing, no merge and no deploy.
