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

---

**EN summary:** the audit found a single-number, binary keep/reject decision whose edit suggestions are never applied, and a renderer limited to one continuous segment. YouTube retention needs the Analytics API scope, which we do not hold. Instagram Business Discovery is not assumed to be available. Phase 6 adds a four-dimension quality gate with REWORK (6A), segment-based structural editing (6B), a references lab with permitted metadata and image-based format profiles (6C), abstract pattern library from authorised media (6D), performance learning on our own posts (6E) and fixed-sample validation (6F).
