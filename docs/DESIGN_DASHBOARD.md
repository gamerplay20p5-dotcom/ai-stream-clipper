# AI Stream Clipper by HTS

## Direção visual

A identificação HowTo.Software já aparece nas páginas institucionais. O repositório não traz um manual visual nem um arquivo de logotipo da HTS. O símbolo de enquadramento e reprodução, as cores e a tipografia são uma proposta para o AI Stream Clipper. A assinatura “by HTS / HowTo.Software” aparece com o nome do produto na navegação, no login e nas páginas públicas. O menu da conta identifica a sessão e a empresa.

A interface segue a disposição de uma mesa de edição. A coluna de navegação e o cabeçalho identificam a tarefa, o palco escuro delimita a mídia e uma coluna lateral recebe as evidências e os controles. Na comparação, os dois vídeos ocupam a mesma área, com métricas e promoção em uma seção expansível. A visão geral usa uma única faixa de prioridades e painéis de trabalho com dados operacionais.

## Tokens e componentes

| Papel | Valor ou escala | Uso |
| --- | --- | --- |
| Plano de trabalho | `#11131B` | Fundo do aplicativo |
| Painel e superfície elevada | `#181B26` e `#212637` | Seções, menus e seleção |
| Palco de mídia | `#0A0C12` | Miniaturas, players e prévias |
| Texto principal | `#F1F3FB` | Títulos, controles e conteúdo |
| Destaque | `#B2BBFF` | Seleção, ação principal e foco |
| Estados | `#7ADEB9`, `#F5C67D`, `#FF9CA1`, `#94D0F5` | Sucesso, atenção, erro e processamento |
| Tipografia | Archivo Variable e fonte monoespaçada do sistema | Interface, tempos, versões e contadores |
| Espaçamento | Base de 4 px, trilhos de 16–36 px e sidebar de 232 px | Fluxo de leitura e densidade |
| Raios | 6 px nos controles e 10 px nas superfícies | Formas e hierarquia |
| Movimento | 150 ms nos controles e 200 ms nas revelações | Resposta à interação |

Os valores CSS estão em [tokens.css](../dashboard/public/site/tokens.css) e são compartilhados pelo painel e pelas páginas públicas. O Tailwind expõe os tokens com suporte a opacidade. [index.css](../dashboard/src/index.css) concentra campos, botões, painéis, tabelas, filtros, estados transitórios e foco. [ui.tsx](../dashboard/src/components/ui.tsx) inclui cabeçalhos, estados vazios, skeletons, alertas, indicadores, medidores, controles de revelação, confirmação em diálogo e mídia. [workspace.css](../dashboard/src/workspace.css) concentra a estrutura de navegação e login. A sombra fica nos elementos sobrepostos, como o menu da conta e o drawer.

Use texto além da cor ao descrever estados. Use um botão com `aria-pressed` para filtros locais e um link com `aria-current` para seções navegáveis. Reserve o alerta para falhas e o status para feedback de sucesso ou carregamento. A navegação compacta usa um diálogo nativo com foco contido e fechamento por Escape. Requisitos de reprodução e edição que dependem de câmera, som ou legendas continuam sendo apresentados no contexto do vídeo.

## Navegação

| Área | Acesso |
| --- | --- |
| Operação | Visão geral, lives e streamers, diagnóstico |
| Conteúdo | Central de cortes e avaliação editorial |
| Distribuição | Publicações, calendário, contas conectadas |
| Inteligência | Diretor IA, laboratório de referências e configurações da IA |
| Administração | Infraestrutura, armazenamento e histórico de versões |

Calendário, contas, regras e relatórios usam a rota existente `/publicacao` com a seção em `?aba=`. O retorno OAuth abre Contas conectadas. A entrada `/publicacao` abre o acompanhamento por estado. A seleção do corte continua em `/cortes/:id` e mantém os filtros ao voltar à fila. A navegação completa passa para o drawer abaixo de 960 px. As tabelas largas rolam em regiões próprias e os controles quebram linha em telas estreitas.

## Auditoria e decisões por fluxo

- **Visão geral.** A faixa mostra lives, revisão, processamento e agenda. Fila de revisão, canais ao vivo, serviços e atividade recente mantêm links para as respectivas tarefas.
- **Central de cortes.** Miniaturas verticais com título fora da imagem, filtro por estado, seleção em lote e fila com navegação anterior e próxima. A bancada dá prioridade ao vídeo e às ações de revisão. Análise, transcrição, edição, legendas, variantes e envio têm espaços próprios.
- **Variantes.** Os dois players têm o mesmo destaque. O operador escolhe a variante quando há mais de uma, com a identidade de cada lado oculta até votar no modo cego. Linha do tempo, métricas e promoção ficam acessíveis na mesma tela.
- **Distribuição.** Acompanhamento agrupa todos os estados e permite filtrar pendências, agendados, processamento, publicados, falhas, resultados incertos e encerrados. Logs abrem junto à publicação. O calendário exige uma ação explícita para salvar o horário em Brasília. Contas conectadas concentra OAuth, com credenciais em uma seção expansível. Regras e direitos continuam juntos. Relatórios preservam as métricas oficiais.
- **Laboratório de referências.** A biblioteca tem busca e filtros, coleções e controles de envio de prints. Perfis de formato agrupam a imagem, parâmetros editáveis e a geração de prévia. O resultado leva direto à comparação do corte com sua variante de formato. A área de capacidades explicita as permissões verificadas das APIs.
- **Diretor IA e avaliação.** Sessões e exemplos têm listas próprias. Decisões, confiança, fonte, marcações de tempo e evidências ficam junto do contexto editorial. Ações de revisão, aprendizado e escolha de modelos seguem os fluxos existentes.
- **Infraestrutura e diagnóstico.** Os estados de hosts e workers precedem os detalhes de capacidade. Filas e modelos ficam em tabelas roláveis. Mensagens extensas do worker podem ser abertas junto à falha.
- **Configurações, canais e armazenamento.** Configurações usam seções expansíveis e feedback de salvamento. Canais priorizam monitoramento atual e guardam histórico e preferências por canal. Armazenamento começa pelo uso do disco e pela simulação, com ações administrativas em seções próprias.
- **Histórico e identidade.** Versões seguem uma linha cronológica. Login e páginas institucionais compartilham a assinatura de marca. Privacidade e termos preservam o conteúdo legal.

A reformulação usa a Fase 6C como base para cobrir também o laboratório de referências e os perfis de formato. A rota `/referencias` e as APIs do laboratório pertencem à Fase 6C.

## Validação

`npm --prefix dashboard run typecheck`, `npm --prefix dashboard run build` e `npm --prefix dashboard run check:privacy` verificam tipos, empacotamento e escolha explícita de privacidade. A suíte Python foi executada em banco SQLite temporário. O navegador usa cópias locais dos builds e intercepta as chamadas da API. As imagens e registros de validação existem apenas no teste local.

| Verificação | Resultado |
| --- | --- |
| Typecheck e build Vite | Aprovados |
| Privacidade | 10 verificações aprovadas |
| Suíte Python | 321 aprovados e 3 ignorados |
| Playwright | 20 visualizações autenticadas sem rolagem horizontal da página em 320, 390, 1024 e 1440 px |
| Axe | Nenhuma violação automatizada das regras WCAG A/AA nas 20 visualizações verificadas em 390 px |

Os testes de navegador também cobriram login e logout, drawer por teclado, fechamento da confirmação de exclusão, fluxo de revisão e voto cego, reprodução de mídia de teste, consentimento editorial, retorno OAuth, envio de print, edição de perfil, abertura da variante de formato na comparação e reagendamento em Brasília com o navegador em UTC e abertura das páginas institucionais. As verificações automatizadas de acessibilidade cobrem o estado exibido nos cenários de teste.
