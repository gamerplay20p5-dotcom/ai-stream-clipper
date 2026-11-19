# AI Stream Clipper by HTS / HowTo.Software

## Direção de arte

A leitura começa no material audiovisual. No Diretor, a unidade de leitura é um acontecimento localizado no tempo. Na Central, é o corte que está no monitor. A navegação tem peso menor que os dois. A primeira composição, que reservava uma coluna inteira ao seletor de sessões e escondia as evidências, foi descartada. O seletor passa ao cabeçalho e a largura liberada vai para a partitura dos eventos.

| Elemento | Tratamento |
| --- | --- |
| Fundo | Azul de meia-noite `#171526` |
| Instrumentos | Violeta noturno `#201D33`, elevação `#312845` |
| Monitor de vídeo | Preto azulado `#0E0E1B` |
| Texto | Branco quente `#FFF4ED` |
| Seleção e ação | Rosa néon `#FFA4CF` e pêssego `#FFB992` |
| Tipografia | Barlow Condensed nos títulos principais. Archivo Variable nos controles e textos. Algarismos tabulares nos indicadores e monoespaçados nos tempos. |
| Movimento | 150 ms nos controles, 200 ms ao abrir um painel. A preferência de movimento reduzido elimina as transições. |

```
Diretor IA                       [sessão]
Canal • estado     tempo / situações / decisões
Partitura temporal          | Situação selecionada
Evidências por fase         | Confiança e critérios
Seleção de situações        | Cortes associados

Central de cortes               [filtros]
Fila com miniaturas     | Monitor e navegação
                        | Revisão / versões / edição / publicação
```

Os rótulos e títulos alinham à esquerda. A régua do Diretor usa a escala da situação selecionada, com tempos absolutos da transmissão. Fontes e eventos continuam legíveis em texto quando múltiplas marcas coincidem. A confiança pertence à leitura narrativa; as notas pertencem a cortes individuais. No celular, controles de seleção e resumo precedem a leitura extensa. Ações de lote continuam na fila.

A hierarquia da navegação vem do estudo de alinhamento e densidade do Linear. O monitor de revisão e as versões adjacentes derivam das práticas de revisão do Frame.io. A organização por monitor, fila e instrumentos vem da página Edit do Resolve. O Raycast orientou a apresentação das ações e atalhos no contexto do item selecionado. [Linear](https://linear.app/now/how-we-redesigned-the-linear-ui) · [Frame.io](https://frame.io/features/review-and-approval) · [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve/edit) · [Raycast](https://manual.raycast.com/action-panel)

O tema atual usa o contraste de Vice City ao anoitecer como referência. A arte original de uma orla com arquitetura art déco aparece na Visão geral, no login e na página pública. O rosa identifica a seleção e os marcadores da timeline. O pêssego sinaliza a ação principal, enquanto os estados da operação mantêm cores próprias. [Referência visual da Rockstar Games](https://www.rockstargames.com/VI/vice-city)

A mesma navegação, tipografia, contraste, controles e estados servem operação, conteúdo, inteligência, distribuição e administração. Parâmetros mantém suas seis abas e um rascunho entre elas. As páginas públicas e a entrada compartilham os tokens e o símbolo de montagem. A cor de estado vem sempre acompanhada de texto.

## Decisões verificadas no navegador

Na comparação inicial, a seleção de live ocupava 250 px mesmo com uma única opção. A situação, a confiança e seus cortes tinham o mesmo peso, e a fonte de cada evidência dependia de abrir um painel. A nova partitura situa as fases no eixo vertical e o tempo no horizontal. A altura de um ponto identifica a fase. Selecionar um acontecimento atualiza a marca da régua e a evidência no painel de leitura.

Na primeira passagem da Central em 390 px, as três linhas de filtros e a fila adiavam o player até depois da primeira tela. A etapa agora usa um seletor compacto e a fila abre sob demanda. No desktop a fila permanece ao lado do monitor. A edição fixa o corte na URL e conserva o rascunho ao consultar outra aba. Na comparação cega os players mantêm o mesmo peso visual.

Visão geral apresenta um quadro principal e os próximos cortes da fila. O calendário separa datas e horários em colunas no desktop. As contas conectadas reorganizam seus dados em linhas que cabem no celular. O diagnóstico abre com a transmissão investigada e o armazenamento distingue a ocupação do disco da simulação de limpeza. No login, a moldura usa as proporções do vídeo vertical e a marca do produto.

As capturas comparativas usam a revisão `3381174` como “antes”, a mesma sessão e as mesmas mídias de demonstração. Foram inspecionadas as telas em 1440, 1024 e 390 px. A verificação de layout e dos fluxos de revisão também cobre 320 px. A mídia da demonstração fica fora do aplicativo distribuído.

A validação combina `npm --prefix dashboard run build`, `typecheck`, `check:privacy`, a suíte Python com SQLite temporário e Playwright com API isolada. Os cenários incluem troca de sessão e situação, seleção de evidência, corte isolado, estado vazio, fila móvel, comparação de vídeos, edição entre abas, autenticação, reagendamento e consentimento de publicação. O axe verifica as regras automatizadas WCAG A/AA nos estados de teste em 390 px.

## Comparação em desktop

As imagens abaixo registram a mudança para a bancada audiovisual antes do tema atual.

| Tela | Versão anterior | Bancada atual |
| --- | --- | --- |
| Diretor IA | [Antes](design/diretor-antes.webp) | [Depois](design/diretor-depois.webp) |
| Central de cortes | [Antes](design/cortes-antes.webp) | [Depois](design/cortes-depois.webp) |

As quatro imagens são capturas do navegador a 1440 × 900. Os trechos e seus fotogramas foram adaptados do trailer de *Sintel*, © Blender Foundation, com recorte vertical para a demonstração. [Sintel](https://studio.blender.org/films/sintel/) · [Arquivo de origem](https://media.w3.org/2010/05/sintel/trailer.mp4) · [Licenciamento do Blender Studio](https://studio.blender.org/remixing/)

<details>
<summary>Prompt da arte original</summary>

A imagem foi criada com o gerador de imagens integrado e exportada em WebP para o frontend. [Orla ao entardecer](../dashboard/public/site/orla-ao-entardecer.webp)

> Create an original cinematic editorial photograph for the AI Stream Clipper dashboard and sign-in art. View from across the boulevard of a fictional Miami Beach Art Deco oceanfront corner hotel at blue hour. A vivid peach sunset still burns across the horizon, dusty orchid sky, electric rose neon tracing a rounded Streamline Moderne canopy, cool aqua reflections in rain-dark pavement, and dark silhouettes of tall palms. A low wide camera, diagonal recession, foreground palms and premium precise architectural details. Horizontally composed around a 16:9 ratio with a wide usable crop. Concentrate the beautiful hotel facade and palm silhouettes in the right-center, with darker violet shadows at left. This should feel like a real location scouting photograph on 35mm film, rich colors with natural exposure, believable architecture, no artificial glowing atmosphere. No people in focus, no lettering or logos, no interface, entirely original architecture and scene unrelated to any specific game asset.

</details>