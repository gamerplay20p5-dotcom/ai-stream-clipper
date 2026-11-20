# Design system | AI Stream Clipper by HTS

Direção cinematográfica noturna aprovada pela HTS em 20/11/2026. O mockup de referência e a comparação com a implementação estão em [docs/REDESIGN_CINEMATICO.md](docs/REDESIGN_CINEMATICO.md). Os tokens estão em `:root` de [src/styles.css](src/styles.css).

*English summary at the end.*

## Princípios

1. **O produto é o herói.** A primeira tela mostra o software funcionando: a live, o trecho detectado, a lista de momentos e o Short vertical. O texto acompanha essa leitura.
2. **Coral é ação e corte.** O coral marca CTAs, o trecho selecionado, o estado ativo e o resultado. Magenta e roxo iluminam o ambiente (céu, halos, brilho dos painéis) e não preenchem componentes.
3. **Fragmentos do software no lugar de cartões com ícone.** Recursos, demonstração e planos usam pedaços da interface: trilhas, waveform, guia de recorte, biblioteca e formatos.
4. **Sem ponto final** em títulos, subtítulos, cards, botões, navegação e rótulos, em PT-BR e EN. Respostas do FAQ e notas com mais de uma frase usam pontuação normal. A suíte Playwright verifica a regra.
5. **Movimento só como resposta.** As transições acontecem quando o visitante age (etapa, momento, recorte, legenda). `prefers-reduced-motion` desativa todas elas.

## Cor

| Token | Valor | Uso |
| --- | --- | --- |
| `--bg` | `#090B10` | Fundo da página |
| `--panel` | `#11131C` | Janelas do estúdio, painéis de recursos e lista de espera |
| `--surface` | `#151722` | Superfícies internas e cards de plano |
| `--surface-raised` | `#1C1E2B` | Controles ativos e trilhas |
| `--text` | `#F5F6FA` | Texto principal |
| `--text-soft` | `#D4D6E0` | Texto sobre imagem e listas |
| `--muted` | `#ADB1C0` | Texto secundário |
| `--line` / `--line-strong` | `#23263A` / `#343849` | Divisórias e bordas |
| `--coral` | `#FF4F62` | Destaque, trecho, ícones ativos e headline |
| `--coral-text` | `#FF8A97` | Texto coral pequeno e foco |
| `--action` | `#FF4F62` | Fundo dos botões primários, idêntico à referência |
| `--on-action-small` | `#1A0609` | Texto de elementos coral pequenos (CTA do cabeçalho, selos) |
| `--magenta` | `#DF3D83` | Luz ambiente na seção de planos |
| `--violet` | `#7640C8` | Luz ambiente do hero, halos e plano Pro |

Os botões usam exatamente o coral da referência, `#FF4F62`. O texto branco sobre ele tem contraste de 3,2:1, o que atende o WCAG AA só para texto grande. Por isso todo botão coral com texto branco usa ≥ 18,67 px em negrito (1,1875rem, peso 700), e os elementos coral pequenos usam texto escuro `#1A0609` (5,9:1). O hover escurece para `#EE4458` (3,7:1).

## Tipografia

- **Space Grotesk** (variável, 700) para títulos, números de preço, marca e rótulos do estúdio. O espaçamento entre letras é negativo (-0,035 a -0,045 em) nos tamanhos grandes.
- **Instrument Sans** (variável, 400 a 650) para texto corrido, controles e formulário.
- Monoespaçada do sistema só em timecodes e notas da biblioteca.
- As duas fontes usam a licença SIL OFL 1.1 e são servidas localmente pelo Fontsource. A CSP não permite fontes externas.

| Token | Tamanho | Uso |
| --- | --- | --- |
| `--step-4` | `clamp(2.2rem, 2.9vw, 2.9rem)` | H1 do hero, em duas linhas |
| `--step-3` | `clamp(1.9rem, 3.1vw, 2.9rem)` | Títulos de seção |
| `--step-2` | 1,375rem | Títulos de recurso e comparação |
| `--step-1` | 1,125rem | Ledes, etapas e marcadores de seção |
| `--step-0` | 1rem | Texto corrido |
| `--step--1` | 0,8125rem | Selos e notas |

Os marcadores de seção usam a barra coral do mockup (`/ Como funciona`). Não há rótulos em caixa alta.

## Espaço, forma e profundidade

- Escala de espaçamento: 4, 8, 12, 16, 24, 32, 48 e 72 px. Seções com `clamp(64px, 7vw, 112px)`.
- Largura de conteúdo de 1360 px (1520 px a partir de 1600 px de viewport). Margem fluida de 16 a 56 px, ou 12 px abaixo de 380 px.
- Raios por hierarquia: 8 px em botões e campos, 12 px em mídia interna, 18 px em painéis e cards, 24 px em janelas do estúdio e na lista de espera, e cápsula só em selos e filtros.
- Elevação: `--shadow-panel` para janelas sobre o fundo, `--glow-violet` atrás do estúdio e do preview, `--glow-coral` em CTAs, no Creator e no Short.

## Componentes

| Componente | Arquivo | Observações |
| --- | --- | --- |
| Cena do hero | `src/Hero.tsx` (`HeroScene`) | SVG original: céu, sol, skyline, palmeiras e helicóptero. Determinístico, para coincidir no SSR e na hidratação |
| Preview do produto | `src/Hero.tsx` (`ProductPreview`) | `figure` com descrição. O interior é ilustrativo e fica `aria-hidden` |
| Waveform | `src/Waveform.tsx` | Compartilhado entre hero e demo, com trecho e marcadores |
| Demonstração | `src/Demo.tsx` | Abas verticais (setas, Home e End), seletor de momento (radiogroup), recorte, legendas e revisão |
| Recursos | `src/Sections.tsx` | Trilhas de sinais, par 16:9 → 9:16, biblioteca com regra de limpeza, formatos |
| Planos | `src/Pricing.tsx` | Valores de `catalog.json`. Creator recomendado e Pro com luz violeta |
| Lista de espera | `src/Waitlist.tsx` | Validação nativa, estados de envio e erro, foco na confirmação |
| Cabeçalho e rodapé | `src/SiteChrome.tsx` | Menu mobile em `dialog` modal, ciclo de foco e retorno de foco |

Estados interativos: hover com borda coral ou fundo `--coral-wash`, foco visível com contorno `--coral-text` de 2 px, pressionado com deslocamento de 1 px, seleção indicada por `aria-selected`, `aria-checked` ou `aria-pressed`.

## English summary

Night-time cinematic direction approved by HTS. Coral marks action and the selected clip. Magenta and violet are ambient light only. Space Grotesk handles headings and Instrument Sans the body copy, both self-hosted. Sections show fragments of the software rather than icon cards. Headings, subtitles, cards, buttons, navigation and labels never end with a period, and the browser suite enforces this. Primary buttons use the exact reference coral `#FF4F62`. White text on it is set at large bold size (≥ 18.67 px) to meet WCAG AA, and small coral elements use dark `#1A0609` text.
