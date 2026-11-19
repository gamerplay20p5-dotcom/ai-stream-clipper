# AI Stream Clipper by HTS / HowTo.Software

## Direção de arte

A identidade visual traz o contraste de Vice City ao entardecer para uma bancada de revisão audiovisual. Uma orla art déco criada para o projeto aparece na Visão geral, no login e na página pública. A mídia em revisão ocupa o centro das telas de trabalho. [Referência visual da Rockstar Games](https://www.rockstargames.com/VI/vice-city)

| Elemento | Tratamento |
| --- | --- |
| Fundo | Azul de meia-noite `#171526` |
| Painéis | Violeta noturno `#201D33` com elevação `#312845` |
| Monitor de vídeo | Preto azulado `#0E0E1B` |
| Texto | Branco quente `#FFF4ED` |
| Seleção e timeline | Rosa néon `#FFA4CF` |
| Ação principal | Pêssego `#FFB992` |
| Tipografia | Barlow Condensed nos títulos principais e Archivo Variable nos controles e textos. Algarismos tabulares nos indicadores e monoespaçados nos tempos. |
| Movimento | 150 ms nos controles e 200 ms ao abrir um painel. A preferência de movimento reduzido elimina as transições. |

A mesma tipografia, contraste e controles servem todas as áreas do painel. A cor de estado vem acompanhada de texto.

## Bancada de revisão

- **Diretor IA.** O seletor de live fica no cabeçalho. Na timeline, a altura indica a fase e a posição horizontal indica o tempo na transmissão. Selecionar um acontecimento atualiza a evidência, a fonte e a confiança no painel de leitura. Os cortes associados ficam no mesmo contexto.
- **Central de cortes.** A fila fica ao lado do monitor no desktop e pode ser recolhida em telas menores. A edição fixa o corte na URL e conserva o rascunho ao consultar outra aba. Os players da comparação cega têm o mesmo peso visual.
- **Parâmetros.** Seis abas organizam os controles de IA. O rascunho é compartilhado entre elas.
- **Operação.** A Visão geral destaca os próximos cortes da fila. O calendário agrupa as publicações por dia e mantém a edição em horário de Brasília. O diagnóstico abre com a transmissão investigada e o armazenamento distingue a ocupação do disco da simulação de limpeza.

## Capturas do tema

| Tela | Captura a 1440 × 900 |
| --- | --- |
| Visão geral | [Abrir captura](design/visao-geral.webp) |
| Diretor IA | [Abrir captura](design/diretor-ia.webp) |
| Central de cortes | [Abrir captura](design/central-de-cortes.webp) |
| Login | [Abrir captura](design/login.webp) |

As telas foram inspecionadas em 1440, 1024 e 390 px. A verificação de layout também cobre 320 px. Os fotogramas da demonstração foram adaptados do trailer de *Sintel*, © Blender Foundation. [Sintel](https://studio.blender.org/films/sintel/) · [Licenciamento do Blender Studio](https://studio.blender.org/remixing/)

A validação inclui o build Vite, a checagem de tipos, as verificações de privacidade e o Playwright com API de teste. Os cenários incluem seleção de situação e evidência, fila de cortes, comparação cega, rascunhos de edição, autenticação e reagendamento. As regras automatizadas WCAG A/AA foram verificadas com axe nos estados de teste a 390 px.

<details>
<summary>Prompt da arte original</summary>

A imagem foi criada com o gerador de imagens integrado e exportada em WebP para o frontend. [Orla ao entardecer](../dashboard/public/site/orla-ao-entardecer.webp)

> Create an original cinematic editorial photograph for the AI Stream Clipper dashboard and sign-in art. View from across the boulevard of a fictional Miami Beach Art Deco oceanfront corner hotel at blue hour. A vivid peach sunset still burns across the horizon, dusty orchid sky, electric rose neon tracing a rounded Streamline Moderne canopy, cool aqua reflections in rain-dark pavement, and dark silhouettes of tall palms. A low wide camera, diagonal recession, foreground palms and premium precise architectural details. Horizontally composed around a 16:9 ratio with a wide usable crop. Concentrate the beautiful hotel facade and palm silhouettes in the right-center, with darker violet shadows at left. This should feel like a real location scouting photograph on 35mm film, rich colors with natural exposure, believable architecture, no artificial glowing atmosphere. No people in focus, no lettering or logos, no interface, entirely original architecture and scene unrelated to any specific game asset.

</details>