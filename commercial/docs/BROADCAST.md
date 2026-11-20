# Broadcast Studio

## Auditoria visual

O PR #18 apresentava o mesmo quadro em todos os monitores. O redesign editorial posterior colocou o hero e o fluxo em blocos empilhados e repetiu a cena em outras seções. A paleta clara, o grande cabeçalho e a tabela de planos davam à página a aparência de um site institucional.

## Sistema de interface

| Papel | Valor |
| --- | --- |
| Fundo | `#090B10` |
| Superfície | `#171B25` |
| Texto principal | `#F5F6FA` |
| Texto secundário | `#B7BCC8` |
| Coral de ação e corte | `#FF4F62` |
| Ciano de análise e foco | `#4CE0D2` |

Space Grotesk dá forma aos títulos e aos números de preço. Instrument Sans preserva a leitura em textos e controles. Ambas têm licença SIL Open Font License 1.1. A fonte monoespaçada do sistema aparece em formatos, trilhas e timecodes. Usamos uma grade de 1320 px, margens fluidas e bordas discretas. O ciano identifica estado técnico e foco. O coral identifica CTA, seleção editorial e trecho.

No desktop, o texto fica à esquerda de uma bancada funcional com monitor de origem, monitor vertical, trilhas e cinco etapas. No celular, a bancada fica abaixo do texto com as cinco etapas acessíveis. O fluxo abaixo explica o processamento por sinais independentes. A seção comercial expõe as franquias na primeira leitura e usa uma faixa de recursos avançados no Pro. FAQ e formulário têm composições próprias.

## Mídia

As capturas operacionais do repositório servem como referência interna de produto. Para a demonstração pública local, foram usadas duas tomadas originais do ImageGen integrado. Os prompts pediram um carro prata em uma rampa urbana noturna, piso molhado com reflexos frios e luz coral pontual. A segunda tomada mudou para um enquadramento frontal do mesmo carro, preservando a continuidade para um recorte vertical. O Short é uma montagem local das duas imagens. A bancada e o vídeo estão identificados como ilustração, com legendas PT-BR e EN e descrição em texto.

Os recursos utilizados estão em [public/media](../public/media). A imagem panorâmica tem uma variante responsiva, e o vídeo é requisitado ao abrir Revisar.

<details>
<summary>Prompts das tomadas</summary>

1. Original cinematic game-capture still for a professional livestream editing workstation. 16:9 wide image, low third-person camera directly behind a graphite silver 1980s wedge sports coupe with red horizontal tail lights, exiting an underground concrete ramp into a dense elevated downtown at midnight. Car at x52%, y68%, medium sized within a broad cinematic roadway. Wet charcoal road, powerful navy-blue ambient lighting, cool white overhead strip lights, a restrained coral red road signal and small cyan reflections. No palm trees, no warm amber light, no logos, no words, no watermark, no people, no collision, no law enforcement, no HUD. Filmic realism and intentional reflective surfaces. Distinct composition: two overhead concrete slabs form strong diagonals converging on the car, dark negative space above and city window lights in the distance. Crisp readable thumbnail, expressive, photographed as a recognizable captured moment, not a flat illustration.
2. A new cinematic shot that follows the attached fictional game capture, same graphite silver wedge coupe at night. Wide 16:9; shot from a low near-frontal three-quarter camera on the outside of the bend as the coupe exits the underground ramp. Center the whole car in the middle 35% so the key light and windshield still read in a 9:16 vertical crop. Distant geometric elevated city glass, dramatic cyan fluorescent lines, wet charcoal pavement, cold white headlights and a subtle coral reflection off a wall. The car points into the curve, physically plausible dynamic motion, professional game-capture film still. No palms, orange lighting, people, logos, text, HUD, police, watermark, or collision. Use the first frame as continuity only, change camera angle and sense of action decisively.

</details>

## Validação local

- Build de produção e suíte HTTP aprovados. O backend, os preços e o catálogo de franquias são os mesmos da versão editorial. A auditoria npm retornou zero vulnerabilidades no momento da revisão.
- 20 testes passaram em Chromium, Firefox e WebKit. Os testes cobrem menu, foco, cinco etapas, legendas, reprodução, idioma, mercado, preços e inscrição real no SQLite de teste.
- Capturas completas em 320, 375, 390, 768, 1024, 1440 e 1920 px. As verificações automatizadas de acessibilidade não encontraram violações nos estados testados.
- No Chromium local a 390 px, uma execução registrou LCP de 164 ms e deslocamento acumulado de 0,000045. A imagem panorâmica de 29 kB foi selecionada e o MP4 ficou fora da carga inicial. Os números são de laboratório em localhost.

## Descrição bilíngue para revisão futura

### Português

Reconstrói a landing comercial na direção Broadcast Studio. A bancada permite percorrer live, análise multimodal, seleção editorial, enquadramento com legendas e reprodução do Short 9:16. Os recursos são apresentados com diagramas de sinais e de formatos. A comparação mantém as franquias comerciais, com destaque para os benefícios editoriais e as exportações 4K do Pro. A lista de espera continua usando o serviço Express/SQLite existente.

Validação local com build, testes HTTP e Playwright em Chromium, Firefox e WebKit. Capturas em 320, 375, 390, 768, 1024, 1440 e 1920 px e verificações automatizadas de acessibilidade.

### English

Rebuilds the commercial landing page around a broadcast studio. The five demo phases cover the source livestream, multimodal analysis, editorial selection, captioned vertical framing and playable 9:16 review. Signal and format diagrams explain the core capabilities. Pricing preserves the commercial allowances and highlights Pro’s editorial tools and 4K exports. The waitlist continues to use the existing Express/SQLite service.

Local validation includes the build, HTTP tests and Playwright across Chromium, Firefox and WebKit. Screenshots cover 320, 375, 390, 768, 1024, 1440 and 1920 px, with automated accessibility checks.
