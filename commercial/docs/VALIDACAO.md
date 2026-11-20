# Validação do redesign comercial

> Registro da versão editorial, que foi substituída. A validação atual está em [REDESIGN_CINEMATICO.md](REDESIGN_CINEMATICO.md).

## Comparação visual

| Tamanho | PR #18 | Redesign |
| --- | --- | --- |
| Desktop, 1440 px | [Antes](qa/desktop-before.webp) | [Depois](qa/desktop-after.webp) |
| Mobile, 390 px | [Antes](qa/mobile-before.webp) | [Depois](qa/mobile-after.webp) |

No PR #18, o menu desktop desaparecia no celular, o mesmo quadro aparecia em todas as etapas e não havia FAQ. A composição dependia de cartões e da mesma escala visual em várias seções. No redesign, a diferença entre imagem bruta e corte editado organiza o hero. O visitante passa por quatro etapas, troca quadros, alterna as legendas e reproduz o vídeo de exemplo. Recursos ficam em linhas editoriais. No celular, a comparação abre um recurso por vez e apresenta os três planos na mesma linha.

## Acessibilidade e interação

- Capturas completas e inspeção em 320, 375, 390, 768, 1024, 1440 e 1920 px. A suíte verifica a largura do documento e a pontuação dos títulos e controles.
- Chromium, Firefox e WebKit validam teclado, abertura e fechamento do menu, retorno de foco, etapas da demo, FAQ, idioma, moeda e os estados do formulário. O menu faz o ciclo de Tab explicitamente para incluir os links também no WebKit.
- O axe-core encontrou zero violações nas regras automatizadas WCAG A/AA 2.0, 2.1 e 2.2 habilitadas, nos estados executados. O escopo inclui PT-BR em desktop, EN com o vídeo, menu mobile aberto e página mobile com o menu fechado. A página usa HTML semântico, foco visível, preferência por movimento reduzido, legendas e descrição textual do Short.
- A automação e a inspeção de teclado cobrem os cenários descritos. Uma auditoria de conformidade completa inclui sessões com tecnologias assistivas e usuários reais.

## Desempenho

- Build de produção com HTML pré-renderizado nas quatro combinações de idioma e mercado. Os metadados localizados chegam na primeira resposta HTTP.
- JavaScript inicial de aproximadamente 82 kB e CSS de 5,6 kB, ambos com gzip. Fontes variáveis são servidas localmente. A imagem panorâmica menor pesa 62 kB e foi selecionada no cenário de 390 px com densidade 1×. Mídia fora da primeira dobra usa carregamento tardio.
- No Chromium local, em 390 px, uma execução registrou LCP de 156 ms e deslocamento acumulado de aproximadamente 0,000004. O MP4 de 334 kB só é requisitado ao abrir a etapa Revisar. Esses valores são de laboratório em localhost, sem limitação de rede, e não representam métricas de produção.
- `npm audit --audit-level=moderate` retornou zero vulnerabilidades no momento da revisão. Playwright e axe-core são dependências de desenvolvimento. Os repositórios oficiais e os metadados npm foram verificados antes da instalação.

## Segurança

- A alteração no Express serve HTML pré-renderizado. Idioma e mercado são selecionados por lista permitida antes de formar o caminho. A resposta define `Content-Language` e varia por `Accept-Language`.
- A rota de lista de espera conserva validação de campos, limite de 4 kB, JSON, limitação por IP, campo antirrobô, consultas parametrizadas e resposta idêntica para cadastro novo e e-mail existente. O formulário pré-renderizado declara POST para evitar que um envio antes da hidratação coloque o e-mail na URL.
- O navegador envia preferência de plano e mercado. O servidor registra interesse, sem derivar dele uma assinatura, benefícios, permissões ou uma cobrança. Sem segredo no bundle, CORS adicional, rastreamento, nova rota de leitura ou mudança na configuração do proxy.
- A suíte HTTP cobre entradas inválidas, duplicidade, limitação, payload, formato e persistência. O Playwright comprova que o sucesso espera a resposta 202 e consulta o SQLite temporário para validar a inscrição real.
- As três skills solicitadas já estavam instaladas. A revisão local usou documentação Markdown. SkillSpector não estava disponível localmente e nenhuma skill adicional foi instalada.
