# Fase 6: Viral Intelligence, seleção inteligente e edição orientada à retenção

*Português; English summary at the end.*

## 1. Auditoria (produção v0.5.7, 2026-11-18)

### Como a nota final é calculada hoje
1. **Pontuação acústica e textual (`scoring/engine.py`):** 10 critérios ponderados geram o candidato com `score`.
2. **Diretor (`ai/director.py`):** funde áudio, fala, visão (VLM e heurística) e `story`, e devolve `keep`, os limites e o `score`. É ele que decide `keep`.
3. **Julgamento editorial (`ai/editorial.py`):** 14 critérios de 0 a 10 com evidências; o peso do VLM é limitado a 0,35. A nota `final` é 50% editorial e 50% diretor. A confiança mede a quantidade de evidência, não a chance de viralizar.
4. **`pipeline_ai.apply_analysis`:** combina as notas, nesta ordem:
   - revisão aprofundada na faixa de dúvida;
   - `waiting`, quando a história ainda está em andamento (até 3 análises);
   - resgate de história completa com `final` acima do mínimo;
   - caso contrário, `keep=False` vira **`rejected`** direto.

**Problema confirmado:** tudo cabe num único número e a decisão é binária. Um bom acontecimento com uma abertura ruim é rejeitado com o mesmo motivo de um momento vazio. As `edit_suggestions` do julgamento ("começar antes", "estender até o desfecho") são calculadas e **nunca aplicadas**.

### Descarte e limpeza
- **Rejeição** muda só o estado. A mídia fica elegível na simulação de limpeza depois de `cleanup_rejected_days` (7). A limpeza está **desligada e em simulação**.
- **Proteções:** aprovado, protegido, favorito, publicado e em processamento (`BUSY = candidate, analyzing, rendering, waiting`) nunca entram na limpeza. Etapa 6A não mexe nisso.

### Renderização (`render/renderer.py`, `ai/edit_plan.py`)
- **Um único trecho contínuo** (`-ss` e `-t`). Não existe corte de pausas, remoção de trechos redundantes nem reordenação (gancho antecipado).
- **Edição atual:** layout fixo por corte (gameplay, split com webcam, action com recorte que acompanha o movimento), no máximo 3 zooms, fade, legendas, título e normalização de volume.
- **`EditPlan` não tem segmentos.** É a principal limitação estrutural e o objeto da Etapa 6B.

### Analytics e permissões OAuth
- **YouTube:** escopos `youtube.upload` e `youtube.readonly`.
  - Coletamos `statistics` (views, likes, comments) dos **nossos** vídeos.
  - Retenção e visualizações engajadas exigem a **YouTube Analytics API** (`yt-analytics.readonly`), que **não está concedida**. Pedir esse escopo exige reconectar a conta e atualizar a verificação do Google, então só faremos isso se a Etapa 6E precisar.
  - Metadados públicos de canais e vídeos de terceiros (título, duração, data, estatísticas públicas) podem ser lidos com `youtube.readonly` ou com chave de API, respeitando a cota e a política de armazenamento: estatísticas atualizadas ou apagadas em até 30 dias, o que já fazemos para as nossas.
- **Instagram:** escopos `instagram_business_basic`, `instagram_business_content_publish` e `instagram_business_manage_insights` (login unificado).
  - **Business Discovery** (metadados públicos de outras contas profissionais) é um recurso do **Facebook Login** (`instagram_basic` + página vinculada). Não presumimos que a nossa configuração permite. A Etapa 6C **testa a capacidade real** e mostra "indisponível" quando não houver acesso.
  - Nenhuma API oficial entrega o **arquivo de vídeo** de terceiros, então não haverá download de mídia alheia.

### O que reaproveitar (sem duplicar)

| Necessidade da Fase 6 | Já existe | Como reaproveitar |
|---|---|---|
| Qualidade do acontecimento | `editorial` (interesse, humor, surpresa, tensão, reação, originalidade, desfecho) | agrupar em dimensões em vez de recalcular |
| Contexto e desfecho fora do corte | `story.py` (início da situação, `climax_t`, `outcome`) | base do potencial de recuperação |
| Pausas e densidade de fala | palavras do Whisper com tempo | medir os tempos mortos e, na Etapa 6B, cortá-los |
| Quem fala | diarização da v0.5.5 | alimentar a nota técnica e a avaliação pós-edição |
| Nova tentativa | revisão aprofundada e `analysis_runs` | REWORK usa a mesma fila, com limite próprio |
| Avaliação humana e aprendizado | `Review`, `learn`, perfil Wilson | comparação cega de versões e experimentos |
| Métricas reais | `Publication.metrics` | aprendizado por desempenho (Etapa 6E) |

## 2. Plano técnico por etapa

| Etapa | Entrega | Mudança de banco |
|---|---|---|
| **6A Quality Gate 2.0** | 4 notas separadas e explicáveis (acontecimento, narrativa, recuperação, técnica); resultados READY, REWORK, CONTEXT_PENDING, HUMAN_REVIEW e REJECTED; REWORK reaplica limites sugeridos (começar antes, estender até o desfecho, aproximar do ponto alto) com até N tentativas; conflito entre diretor e gate vira HUMAN_REVIEW | coluna `clips.gate` (0009) |
| **6B Editor estrutural** | `EditPlan.segments` (trechos da fonte em ordem); corte de pausas; estruturas A a E; gancho antecipado só com marcação explícita; render por segmentos com `concat`; versões comparáveis | sem tabela nova (plano versionado no JSON) |
| **6C Laboratório de Referências** | coleções, links, categorias e tags; leitura de metadados permitidos (YouTube Data API; Instagram Business Discovery se disponível); **referência de formato por imagem** (print da tela, foto de perfil, nome): o VLM extrai parâmetros abstratos (posição da webcam e da legenda, selo com foto e nome) que viram um **perfil de formato** aplicado pelo renderer | `reference_*`, `format_profiles` |
| **6D Análise e padrões** | analisador de estrutura (gancho, contexto, ritmo, desfecho, identidade visual) só para mídia autorizada (nossos vídeos e uploads com licença declarada); biblioteca de padrões abstratos com confiança e origem | `edit_patterns` |
| **6E Aprendizado por desempenho** | estrutura editorial × métricas reais das nossas publicações, normalizadas por idade e conta; experimentos controlados sem publicar variantes sem autorização | usa as tabelas existentes |
| **6F Validação** | benchmark fixo nos mesmos cortes; comparação cega humana; mudança de critério só com evidência | — |

Princípios fixos:
- nada de scraping nem download de mídia de terceiros;
- estilo de terceiros vira parâmetro abstrato, nunca cópia de grafismo ou fala;
- nenhum efeito "para parecer viral";
- publicação pública automática e limpeza retroativa continuam exigindo autorização.

## 3. Etapa 6A entregue (v0.6.0)

- **Implementado:**
  - `ai/quality_gate.py`: função pura com 4 dimensões e 5 resultados;
  - `_apply_gate` no `pipeline_ai`: REWORK reenfileira a análise com os limites propostos, com prioridade 85, abaixo das lives;
  - coluna `clips.gate` (migração 0009);
  - filtro e aba "Revisão humana";
  - bloco "Quality Gate" no corte;
  - limiares em Configurações da IA.
- **Reaproveitado:** os 14 critérios do julgamento editorial (sem nova nota), a memória da história (início, clímax e desfecho da situação), os tempos das palavras (tempo morto), a confiança das palavras e a diarização (nota técnica), e a fila de análise já usada pela revisão aprofundada.
- **Regras de segurança:**
  - aprovado manualmente e corte de teste nunca são descartados;
  - REWORK deixa o corte em `analyzing`, que a limpeza já protege;
  - rejeitar não apaga arquivo;
  - armazenamento e simulação não foram alterados;
  - sem evidência editorial, prevalece a decisão do diretor;
  - diretor e gate em desacordo vão para revisão humana.
- **Testes:** 15 novos (`tests/test_quality_gate.py`), cobrindo os cenários do item 13 que valem para esta etapa: abertura ruim, perseguição sem desfecho, conversa sem mudança visual, muito movimento sem interesse, clímax depois do limite, início atrasado, corte que não melhora em duas tentativas, aprovado que não pode ser descartado, corte em processamento protegido e modelos em conflito. Total: 215 testes passando.
- **Custo:** a avaliação é aritmética, abaixo de 1 ms. Cada REWORK é uma análise visual a mais na fila de baixa prioridade, com no máximo 2 por corte.
- **Evidência real:** reaplicação somente leitura nos 6 cortes de produção que têm julgamento editorial. Os 6 foram rejeitados pelo diretor com nota editorial entre 55 e 69; com o gate, 2 iriam para REWORK e 4 para revisão humana, em vez de descarte silencioso. **Nenhuma melhora de qualidade está comprovada**: esses 6 não têm rótulo humano. A taxa de acerto do gate será medida pelas decisões na aba "Revisão humana".
- **Limitações:** o corte de pausas (`dead_time`) é detectado, mas só será aplicado pelo editor estrutural (6B). A troca de enquadramento ainda não entra como correção.

### Ajustes da revisão do PR #1

1. **Limites de mídia:**
   - uma correção só é aplicável se o trecho novo estiver **inteiramente** dentro dos segmentos gravados disponíveis, sem buraco de reconexão (`quality_gate.covered`);
   - o desfecho precisa de pelo menos 1 s de reação gravada depois dele;
   - não há mais "corte parcial": se o início real da história está antes da gravação, a correção fica não aplicável e o motivo aparece no painel;
   - a mídia já copiada para a API (fonte do corte) também conta.
2. **Evidências:** cada dimensão mostra as evidências concretas dos 14 critérios de `ai/editorial.py`, como "Interesse 7/10 — situação: perseguição até a delegacia". Quando um critério não tem evidência, aparece "sem evidência específica", nunca um texto inventado.
3. **Tempo morto:** análise visual válida (`visual_ok`) é diferente de acontecimento visual relevante (`visual_events`). Um silêncio só deixa de contar como tempo morto se houver um evento inesperado ou engraçado, ou ação forte **com reação do streamer** (`pipeline_ai.visual_relevant`).
4. **Testes:** 6 de regressão em `test_quality_gate.py` e 4 de migração em `test_migration_0009.py`, cobrindo:
   - clímax depois do último segmento;
   - início antes da gravação;
   - buraco de reconexão;
   - evidências concretas;
   - análise válida sem evento visual;
   - regra de relevância visual;
   - pipeline com segmentos expirados;
   - integridade da migração.

### Segunda revisão do PR #1

5. **Extensão do desfecho sem cruzar lacunas:** o novo fim é o menor entre "desfecho + 4 s" e o ponto até onde a gravação segue **contínua** a partir do fim atual (`quality_gate.reach`). Se não houver pelo menos 1 s de reação gravada, a correção é inaplicável. Exemplo testado: segmentos (0, 32) e (35, 40), corte até 30 s e clímax em 30,8 s levam o novo fim a 32 s, e não a 34,8 s.
6. **Som alto não justifica silêncio:** impacto do jogo ou pico de volume só deixa de contar como tempo morto junto com uma reação de voz (risada, grito), uma situação narrativa em andamento ou um evento visual relevante. Conversa engraçada e situação interessante sem diálogo continuam preservadas.

### Migração 0009 e rollback

- **Integridade testada em SQLite** (`tests/test_migration_0009.py`):
  - existe uma única head (0009 depois de 0008);
  - subir preserva as linhas (cortes antigos ficam com `gate = ''`);
  - a coluna é `NOT NULL` com padrão do servidor `''`, com índice `ix_clips_gate`;
  - descer para 0008 remove só a coluna e o índice, mantendo os dados;
  - é possível subir de novo.
- **PostgreSQL:** `ADD COLUMN ... DEFAULT '' NOT NULL` é instantâneo no PostgreSQL 11 ou superior, sem reescrever a tabela.
- **Atenção, comprovado em teste:** a API executa `alembic upgrade head` ao iniciar. Uma imagem antiga (v0.5.7) diante de um banco já na 0009 **não sobe**, porque não conhece essa revisão. Por isso o rollback tem ordem obrigatória:

```
# 0. (antes do deploy) backup
sudo -n asc-ctl backup-db                       # no 210

# 1. com a imagem v0.6.0 ainda rodando: descer o banco para 0008
sudo -n asc-ctl exec asc-api -- clipper migrate --downgrade 0008   # no 210

# 2. voltar a imagem nos três servidores (as imagens 0.5.7 ficam guardadas pelo deploy)
#    210: ASC_IMAGE_TAG=0.5.7 em ~/asc.env -> sudo -n asc-ctl env-install ~/asc.env -> sudo -n asc-ctl compose up --gpu
#    206 e 201: sudo -n asc-ctl set-tag 0.5.7 -> sudo -n asc-ctl compose up

# Se o passo 1 falhar: restaurar o backup do passo 0 (operação destrutiva: só com autorização).
```

O downgrade apaga apenas a coluna `gate`, que é recalculada na próxima análise; nenhum corte, rótulo ou publicação é afetado. O mesmo cuidado vale para qualquer rollback que cruze uma migração.

## 4. Etapa 6B: editor estrutural (v0.6.1)

**Implementado**
- **`ai/structure.py`** (funções puras):
  - cinco estruturas (A gancho imediato, B suspense, C humor de reação, D história completa, E momento rápido) e a escolha explicada por categoria, configurável;
  - corte de pausas com intervalos protegidos: ponto alto, reações, desfecho e os últimos 2,5 s;
  - prévia opcional do clímax, sempre marcada como "Daqui a pouco..." e seguida da sequência real em ordem;
  - métricas objetivas: duração, tempo até o ponto alto, tempo sem fala, cortes, reações preservadas, ordem cronológica, falas mantidas e **falas do original deixadas de fora**.
- **Renderer:** um passo 1 emenda os trechos num intermediário quase sem perdas, com fades de 30 ms no áudio. O passo 2 é o render normal, com palavras, zooms, enquadramento e rostos remapeados para a nova linha do tempo.
- **Variantes (`pipeline_structure.py`):** a reedição é renderizada ao lado do vídeo principal.
  - nunca o substitui;
  - nunca sobrescreve a fonte armazenada do corte;
  - uma falha no render dela nunca marca o corte como falho;
  - um upload obsoleto é descartado.
- **Modos:**
  - **sombra** (padrão): o vídeo principal continua o original;
  - **ligado**: a reestruturada vira a principal e o original fica como variante;
  - **desligado**.
- **Comparação:** lado a lado, cega por padrão; os votos ficam em `edit_comparisons` (migração 0010). A promoção da variante não exige novo render. Há um resultado agregado por estrutura.
- **Orçamento:** variantes automáticas só quando a mudança é relevante, até 12 por hora (configurável) e com prioridade 80, abaixo das lives e dos renders principais.

**Reaproveitado:** palavras do Whisper, categorias do YAMNet (reações), memória da história (início e clímax), Quality Gate (segmentos gravados e `dead_time`), legendas inteligentes e o renderer existente (passo 2 sem mudança).

**Testes:** 246 passando, com 31 novos nesta etapa (`test_structure.py`, `test_structure_pipeline.py`, render estrutural com ffmpeg real e migração 0010). Eles cobrem:
- abertura ruim que vira gancho perto da ação;
- conversa engraçada que mantém a fala e a risada inteira;
- suspense que exige situação longa;
- prévia explícita e cronológica;
- momento rápido que recusa situações que precisam de contexto;
- trecho fora da gravação;
- render interrompido que não derruba o corte;
- corte aprovado que mantém o vídeo principal;
- upload obsoleto descartado.

**Validação nos mesmos 12 cortes reais da Etapa E** (`docs/benchmarks/etapa_6b_validacao.py`, Whisper small e YAMNet locais, sem memória da história):

| Medida (média) | Original | Estrutura escolhida |
|---|---|---|
| Duração | 32,2 s | 14,4 s |
| Ponto alto aos | 19,9 s | 4,1 s |
| Tempo sem fala | 54% | 37% |
| Reações preservadas / ordem real | — | 12 de 12 / 12 de 12 |
| Falas do original deixadas de fora | — | **38,8%** (contexto antes do gancho) |

- **Estruturas escolhidas:** 11 de 12 foram A (sem a memória da história, quase tudo cai no gancho imediato) e 1 foi C. O planejador leva 0,5 ms por corte.
- **Leitura honesta:** isto mede a mecânica, não a qualidade. O gancho fica mais rápido, mas joga fora quase 40% da fala original. Se isso melhora o vídeo, só as comparações cegas vão dizer. Por isso o padrão é o modo **sombra**.

**Custo:** cada variante é um render extra no 206: um passo rápido (ultrafast) mais o render normal de um vídeo mais curto. O teto é o orçamento por hora.

**Limitações**
- Um corte de pausa pode cair no meio do movimento da tela; o enquadramento não é reavaliado por trecho.
- O ponto alto pode ficar a menos de 3 s do início quando a pausa antes dele é removida (mínimo observado: 1,9 s).
- A transição é um corte seco: sem efeitos, por escolha.
- Avaliar o resultado com o VLM depois do render é a próxima etapa, sem usar a nota do próprio modelo como prova.

---

**EN summary:** the audit found a single-number, binary keep/reject decision whose edit suggestions are never applied, and a renderer limited to one continuous segment. YouTube retention needs the Analytics API scope, which we do not hold. Instagram Business Discovery is not assumed to be available. Phase 6 adds a four-dimension quality gate with REWORK (6A), segment-based structural editing (6B), a references lab with permitted metadata and image-based format profiles (6C), abstract pattern library from authorised media (6D), performance learning on our own posts (6E) and fixed-sample validation (6F).

**6B (v0.6.1):** five faithful structures, pause removal that never touches reactions, peak or outcome, an optional explicitly labelled teaser, segment rendering, shadow variants with blind human comparison and promotion. On 12 real clips the chosen structure shortened clips from 32.2 s to 14.4 s and moved the peak from 19.9 s to 4.1 s, keeping all reactions, but it left out 38.8% of the original speech. No quality improvement is claimed until blind comparisons exist.
