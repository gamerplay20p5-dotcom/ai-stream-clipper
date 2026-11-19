# Painel organizado por tarefa (v0.7.1)

Esta etapa reorganiza o painel do AI Stream Clipper by HTS em torno do trabalho diário. Ela mantém a identidade Vice City do PR #5: azul-noturno, violeta, rosa néon para seleção, pêssego para ação principal e a arte original da orla.

Só a interface mudou. Não há mudança de backend, migração, worker, modelo de IA, agendamento, token OAuth ou serviço do Hank. As únicas mudanças fora do `dashboard/` são o número da versão e a nota de versão.

## 1. Auditoria do PR #5

### Como foi feita

- O frontend do PR #5 rodou contra a API do PR #6, com um SQLite descartável e dados de demonstração locais (`dashboard/scripts/ux/seed_demo.py`, que recusa outro banco).
- As telas foram capturadas com Playwright a 1440, 1280 e 390 px, como administrador e como convidado com 3 cortes compartilhados.
- Nenhuma tela teve rolagem horizontal.

### O que já funcionava e foi mantido

- Identidade visual coerente, com contraste bom em tema escuro.
- Fila e player lado a lado.
- Atalhos A/R/J/K/Esc.
- Comparação cega.
- Timeline do Diretor.
- Rascunho com "Descartar" nos parâmetros.
- Link "pular para o conteúdo", `aria-current` e menu lateral como diálogo.

### Problemas encontrados

| # | Área | Problema |
|---|---|---|
| 1 | Multiusuário | O frontend ignorava `staff` e `areas`. Uma conta convidada via o menu inteiro da equipe e caixas vermelhas de "acesso restrito à equipe" na Visão geral. Não havia Minha Biblioteca, gestão de usuários nem ação de compartilhar. |
| 2 | Navegação | Eram 15 itens em 5 grupos, todos com o mesmo peso. Publicações, Calendário e Contas apareciam no menu e de novo em 5 abas. Itens técnicos ficavam no nível das tarefas do dia. |
| 3 | Visão geral | A arte ocupava cerca de 180 px só com o título. Faltavam propostas a confirmar, revisão humana e envios com resultado incerto. |
| 4 | Central de cortes | No celular, o player só começava em ~700 de 844 px. O "porquê" do corte estava espalhado em três blocos. Havia jargão ("Monitor #1", "Sem análise da Fase 2"). A e R não funcionavam até clicar num corte, sem aviso. O ajuste do corte era só por campo numérico. |
| 5 | Diretor IA | O vocabulário era técnico ("49 decisões", "23 abaixo da nota", "9 só fala"). A confiança aparecia sem dizer o que significa. |
| 6 | Publicações | Havia duas listas por dia quase iguais e nenhum calendário visual. "Automático" era só um texto pequeno. A pausa de emergência, com campo de texto, aparecia no topo de toda tela. |
| 7 | Referências | A tela vazia dizia "Ajuste os filtros" mesmo sem nenhuma referência, e não havia um fluxo guiado. |

## 2. Nova organização

### Equipe

| Grupo | Itens |
|---|---|
| Trabalho do dia | Hoje · Central de cortes · Agenda de publicações · Diretor IA |
| Acervo e pessoas | Referências · Pessoas e acesso |
| Configuração (recolhido) | Canais monitorados · Parâmetros da IA · Contas e regras |
| Sistema (recolhido) | Avaliação editorial · Diagnóstico · Servidores · Armazenamento · Histórico de versões |

Os grupos recolhidos abrem sozinhos quando a página atual está neles. A escolha de abrir ou fechar fica só no navegador de quem usa.

### Convidado

A conta convidada vê apenas a **Minha Biblioteca**, com cabeçalho próprio e sem nada da equipe. A decisão vem de `GET /api/auth/me` (`staff`). O navegador do convidado não chama nenhuma rota da equipe; o cenário 1 verifica isso. A autorização continua no backend.

### Telas

- **Hoje.** "Precisa de você" lista só o que existe agora, com contagem real e uma ação:
  - cortes para revisar;
  - revisão humana;
  - sugestões de publicação;
  - envios incertos ou com falha;
  - tarefas com falha;
  - servidores sem worker;
  - pausa ativa.

  Em seguida vêm os melhores cortes da fila, os próximos envios (com o tipo de decisão), quem está ao vivo, os servidores e a atividade. Quando não há nada pendente, a tela mostra "Tudo em dia".
- **Central de cortes.**
  - Tem cinco áreas: Revisar, Comparar versões, Editar, Publicar e Compartilhar.
  - "Por que este corte?" reúne o resultado do Quality Gate, os motivos registrados, o que a IA viu, o trecho, a duração, a nota e a certeza.
  - As notas por critério e a análise completa ficam em "Detalhes técnicos da IA".
  - No celular, a barra Aprovar / Rejeitar / Próximo fica fixa no rodapé.
  - Com um corte aberto no celular, os filtros e a fila saem da frente.
  - Atalhos: Espaço reproduz ou pausa; A e R valem para o corte na tela; `?` mostra todos.
  - O início e o fim do corte têm botões de ±0,5 s.
- **Agenda de publicações.**
  - Grade semanal no desktop e lista por dia no celular.
  - Três estados com formato e texto, nunca só cor: **Sugestão da IA** (contorno tracejado, aguarda confirmação), **Confirmado** (pêssego) e **Automático** (aqua com anel).
  - Filtro por estado.
  - Detalhe em diálogo, com mudança de horário.
  - Uma faixa única para confirmar ou recusar sugestões.
  - A pausa virou o indicador "Envios ativos · Pausar tudo", que pede confirmação.
  - "Histórico de envios" e "Resultados" ficam na mesma página. Contas e regras ficam em Configuração.
- **Diretor IA.** Cada história responde a quatro perguntas: "O que aconteceu? / Por que importa? / O que a IA decidiu? / Resultado".
  - Os textos são montados só com campos que o diretor registrou (eventos, clímax, objetivos, conflitos, cortes, desfecho, incertezas). Nenhum texto é gerado.
  - A confiança aparece como alta, média ou baixa.
  - A linha do tempo, as evidências e a porcentagem ficam em "Avançado".
- **Parâmetros da IA.**
  - O essencial aparece primeiro. "Mostrar ajustes avançados" revela os ajustes finos e as abas Modelos e Operação.
  - O rascunho mostra quantas alterações não foram salvas.
  - No celular, Salvar e Descartar ficam fixos no rodapé enquanto houver alteração.
- **Referências.**
  - Três passos com progresso real (exemplos guardados e perfis prontos): guardar um exemplo → ensinar um formato → testar num corte seu.
  - A tela vazia diferencia "nada guardado" de "nada com estes filtros".
- **Pessoas e acesso.**
  - Cria conta de convidado ou de equipe; a senha inicial aparece uma única vez e pode ser copiada.
  - Lista as contas com último acesso e cortes compartilhados.
  - Ações: nova senha, encerrar sessões, desativar e reativar.
  - Os compartilhamentos de cada conta permitem trocar o download, enviar a versão atual e revogar.
- **Compartilhar**, na Central.
  - Por padrão, a conta recebe a versão aprovada agora e só assiste.
  - Download, outras versões, "sempre a mais recente" e prazo são escolhas explícitas.
  - Abaixo, a lista "Quem tem acesso" permite revogar.

## 3. Cores e imagens

- **Paleta.** A Rockstar não publica uma paleta oficial de GTA VI. O logo é descrito como um degradê de azul-cobalto a magenta, rosa-choque e laranja queimado, e a capa usa um pôr do sol rosa e azul-esverdeado. Os valores hexadecimais que circulam são estimativas de terceiros. A paleta do PR #5 já cobre essa faixa; esta etapa acrescenta só o **aqua** `rgb(128 222 214)` (`--auto`), reservado ao estado "Automático".
- **Imagens.** Nenhuma imagem da Rockstar é usada. Duas artes originais foram geradas no ForgeGUI, com cena fictícia, sem pessoas, letras ou logotipos:
  - `avenida-de-palmeiras.webp`, cabeçalho da Minha Biblioteca;
  - `pier-ao-anoitecer.webp`, fundo de "Tudo em dia". A geração veio com uma faixa escura à esquerda, então só o recorte da direita é usado.

  Uma terceira arte não foi gerada porque os créditos do ForgeGUI acabaram.

## 4. Estratégia de integração

| Passo | PR | Observação |
|---|---|---|
| 1 | #4 (6C, v0.6.3) | sem mudança |
| 2 | #5 (redesign do Codex) | sem mudança; este branch parte dele (`8a1154d`) |
| 3 | #6 (multiusuário, v0.7.0) | sem mudança; mesclado neste branch com `merge`, sem conflito |
| 4 | **#7, este** (v0.7.1) | base `multiusuario-compartilhamento`. Depois de #4 → #5 → #6, o diff mostra só esta etapa |

Nenhum branch existente foi reescrito. Se o #5 receber novos commits, este branch recebe um novo `merge`, nunca um force push.

## 5. Validação

| Verificação | Resultado |
|---|---|
| `npm run typecheck` | ok |
| `npm run check:privacy` | todas as verificações passaram |
| `npm run build` | ok |
| `pytest` (suíte completa) | 346 passaram e 3 foram pulados (testes de modelos reais de IA que não estão instalados no worktree novo) |
| `dashboard/scripts/ux/scenarios.py` (Playwright, Chromium) | todos os cenários passaram |

### Cenários em navegador real

1. O convidado entra e só vê a biblioteca. `/cortes`, `/publicacao`, `/pessoas` e `/` voltam para `/biblioteca`. O player abre, e o navegador não chama nenhuma rota da equipe.
2. A equipe vê primeiro o trabalho do dia, e "Sistema" começa recolhido.
3. Na Central, `?` abre os atalhos e Esc fecha.
4. Compartilhar pela Central faz o vídeo aparecer na biblioteca do convidado. Revogar faz ele sumir na próxima carga.
5. Na Agenda, os três estados aparecem e o detalhe abre. Pausar pede confirmação; a pausa aparece em Hoje e pode ser retomada.
6. Em Parâmetros, os ajustes avançados aparecem sob demanda e o rascunho é contado e descartado.
7. O Diretor responde às quatro perguntas.
8. A 320 e a 390 px não há rolagem lateral em 9 páginas, e a barra de decisão fica visível.
9. Com `prefers-reduced-motion`, as transições são desligadas.

**Player na Central (celular, corte aberto):**
- PR #5: começava em ~700 px numa tela de 844 px;
- agora: começa em 306 px numa tela de 740 px, a 320 e a 390 px.

As capturas usam dados de demonstração locais. Os vídeos de teste são barras coloridas geradas a partir das fixtures do repositório.

## 6. Antes e depois

| Tela | PR #5 | v0.7.1 |
|---|---|---|
| Visão geral → Hoje | [antes](design/ux/antes-visao-geral.webp) | [depois](design/ux/depois-hoje.webp) |
| Central (desktop) | [antes](design/ux/antes-central.webp) | [depois](design/ux/depois-central.webp) |
| Central (celular) | [antes](design/ux/antes-central-celular.webp) | [depois](design/ux/depois-central-celular.webp) |
| Calendário → Agenda | [antes](design/ux/antes-calendario.webp) | [depois](design/ux/depois-agenda.webp) |
| Diretor IA | [antes](design/ux/antes-diretor.webp) | [depois](design/ux/depois-diretor.webp) |
| Conta convidada | [antes](design/ux/antes-convidado.webp) | [depois](design/ux/depois-biblioteca.webp) · [celular](design/ux/depois-biblioteca-celular.webp) |
| Pessoas e acesso | não existia | [depois](design/ux/depois-pessoas.webp) |
| Parâmetros e Referências (notebook) | — | [parâmetros](design/ux/depois-parametros-notebook.webp) · [referências](design/ux/depois-referencias-notebook.webp) |

## Summary (EN)

v0.7.1 reorganizes the dashboard by task and keeps the PR #5 Vice City identity. It changes the frontend only (plus the version number and release note):

- Staff navigation puts daily work first and collapses configuration and system. Guest accounts get only "Minha Biblioteca" and never call staff endpoints.
- "Today" lists what needs a person, with real counts.
- The clip workspace has a plain-language "Why this clip?", a fixed decision bar on phones, a Share tab and a shortcut help.
- The publishing agenda is a week calendar. AI suggestions, confirmed posts and automatic posts differ by shape and text. Pausing asks for confirmation.
- The AI Director answers four plain questions from recorded fields only.
- Settings are split into essential and advanced.
- References get a guided three-step flow.
- "People and access" manages accounts and shares.

Validated with typecheck, the privacy checks, the build and the full pytest suite (346 passed, 3 skipped: real AI models absent). Playwright scenarios against a local SQLite demo stack also passed. No existing branch was rewritten.
