# Lives compartilhadas e personalização por cliente

Complementa `docs/ASSINATURAS.md`. O SaaS atende donos de canais, clipadores e editores. O cliente não precisa ser dono do canal, e vários clientes podem acompanhar o mesmo streamer ao mesmo tempo.

**Regra:** cada live tem **um único** pipeline-base de captura e análise, qualquer que seja o número de clientes que a acompanham.

## Conceitos

| Conceito | Onde fica | Observação |
| --- | --- | --- |
| Canal de origem | `streamers` (único por plataforma + canal) | Já era único. Agora é compartilhado: nenhum cliente é dono |
| Sessão de live | `stream_sessions` | **Uma sessão `live` por canal**, garantida por um índice único parcial e pela trava da linha do canal |
| Captura compartilhada | job `capture:{sessão}` | Um único job por sessão |
| Análise-base | `segments`, `audio_features`, `transcript_words`, `events`, `clips` | Um job `audio:{sessão}:{segmento}` por segmento. O perfil (idioma e modelo do Whisper) fica fixado em `stream_sessions.base_profile` no início da live |
| Inscrição de monitoramento | `channel_monitors` | Cliente + canal + preset editorial. No máximo uma ativa por cliente e canal |
| Participação na live | `monitor_sessions` | O trecho da live que o plano daquele cliente cobriu (`from_t` até `until_t`) e o motivo da parada |
| Perfil editorial | `channel_monitors.preset` | Escolhido dentro do que o plano permite |
| Corte entregue | `clip_shares` com `monitor_id` | A biblioteca do cliente. O arquivo físico é o mesmo para todos |

```
                 ┌───────────── canal (streamers) ─────────────┐
 monitor A ──┐   │  sessão live ── captura ── segmentos ──┐     │
 monitor B ──┼──▶│  (uma por live)   (um job)   (1 análise│     │
 monitor C ──┘   │                               por seg.)▼     │
                 │            momentos candidatos (clips) ─ render (um arquivo)
                 └──────────────────────────────────────┬──────┘
                    para cada participação que cobre o momento:
                    preset do cliente seleciona? cota de armazenamento ok?
                                                        ▼
                    biblioteca A        biblioteca B        biblioteca C
                    (clip_share)        (clip_share)        (clip_share)
```

## Ciclo de uma live

1. **Início:** o monitor informa que o canal está ao vivo (`report_status`).
   - A sessão começa se a HTS consome o canal (`streamers.hts_monitoring`) ou se algum cliente inscrito pode acompanhá-la agora.
   - A linha do canal é travada e o índice único impede uma segunda sessão `live`. Se dois relatórios chegarem juntos, o segundo entra na sessão existente.
   - Cada cliente elegível é anexado (`monitor_sessions`, estado `watching`). Quem não pode (sem assinatura, sem horas, no limite de lives simultâneas ou sem armazenamento) fica `refused` com o motivo, e é testado de novo a cada segmento.
2. **Cada segmento novo:**
   - cada cliente anexado é cobrado uma única vez (`live:{cliente}:{sessão}:{segmento}`), dentro da **própria** franquia;
   - o segmento que ultrapassa a franquia ainda é entregue ao cliente, que para logo depois (`until_t`);
   - reconexões não cobram de novo;
   - o segmento só é analisado se alguém (a HTS ou um cliente) ainda precisa dele.
3. **Captura:** continua enquanto a HTS consome o canal ou algum cliente está em `watching`. Quando não sobra ninguém, ela recebe `stop`.
4. **Recursos premium sobre o trabalho compartilhado:** segunda análise, variantes e prioridade rodam **uma vez** e só quando um cliente com o recurso no plano cobre aquele momento (ou quando a HTS consome o canal).
5. **Entrega:** quando o corte fica pronto, ele entra na biblioteca de cada cliente cujo trecho cobre o corte **inteiro**, cujo preset o seleciona e cujo armazenamento comporta o arquivo.

### Cobertura temporal

O intervalo completo do Short (`start` até `end`) precisa estar dentro do trecho acompanhado pelo cliente (`from_t` até `until_t`). A tolerância é de **0,5 s**, só para arredondamento entre segmentos e cortes.

| Caso | Entregue? |
| --- | --- |
| Começa antes de o cliente aderir | Não |
| Termina depois de a franquia esgotar | Não |
| Inteiramente dentro do trecho | Sim |
| Cruza uma suspensão, um cancelamento ou um estorno | Não |
| Capturado dentro do trecho, mas pronto depois de o acesso acabar (cancelamento, expiração ou suspensão) | Sim: as horas foram pagas enquanto o acesso valia |
| O mesmo caso, depois de um estorno total do período | Não |
| O mesmo caso, com o pagamento em revisão ou contestação aberta | **Pendente**: sai sozinho quando a revisão é resolvida, ou nunca, se ela terminar em estorno |
| Entregue antes do fim do acesso | Continua na biblioteca: entregas legítimas nunca são retiradas por isso |

### Direito à entrega por período pago (decisão do dono, 2026-11-21)

Cada segmento cobrado fica ligado ao **período pago** que o cobriu: a tabela `entitlement_periods` guarda a assinatura, o plano e os limites daquele momento, e `usage_entries.period_id` aponta para ela. Um corte é julgado por esses períodos, **nunca pela assinatura atual ou mais recente** (`entitlements.delivery_rights`).

1. **Janela:** o corte inteiro precisa estar no trecho acompanhado.
2. **Conta:** a conta não pode estar bloqueada por segurança (`users.active`).
3. **Pagamento:** todos os segmentos sob o corte precisam ter sido cobrados deste cliente. Não há direito sobre horas nunca pagas.
4. **Estado de cada período**, a partir da fotografia mais recente da assinatura:
   - **estornado** (estorno total, chargeback ou contestação perdida): bloqueia. O estorno de um mês antigo (pagamento com status `refunded` dentro daquele período) atinge só esse mês;
   - **revisão financeira ou contestação aberta** (`review` preenchido): pendente. A fotografia que resolve a revisão libera as entregas (`editorial.redeliver_for_user`, chamado a cada fotografia aplicada);
   - **pago**: entrega, mesmo depois de vencimento, cancelamento normal ou suspensão operacional.
5. **Plano aplicado:** o preset, as variantes e o limite de armazenamento são os do período do primeiro segmento do corte. Uma troca de plano no meio do período gera um período novo, e cada hora mantém o plano sob o qual foi paga.

| Situação | Novas entregas | Shorts já entregues |
| --- | --- | --- |
| Vencimento ou cancelamento normal | Só momentos já pagos | Mantidos |
| Suspensão operacional (inadimplência, pausa) | Só momentos já pagos. O acompanhamento das lives para | Mantidos |
| Revisão financeira ou contestação aberta | Pendentes até a resolução | Mantidos |
| Estorno total ou contestação perdida | Nenhuma daquele período | Mantidos (podem ser revogados pela equipe se houver fundamento contratual ou legal) |
| Bloqueio por segurança (conta desativada pela HTS) | Nenhuma | Mantidos, mas inacessíveis enquanto a conta estiver bloqueada. Voltam ao reativar |

**Limite conhecido:** a revisão vale para a assinatura inteira, porque a fotografia não diz qual cobrança está em revisão. Por precaução, todas as entregas daquela assinatura esperam.

## Cotas

| Recurso | Como conta |
| --- | --- |
| Horas | Por cliente, só pelos segmentos que ele acompanhou. A captura ser compartilhada não reduz nem divide a conta |
| Canais | Inscrições ativas do cliente. Seguir um canal que outro cliente já segue conta igual |
| Lives simultâneas | Participações `watching` em sessões ao vivo |
| Armazenamento | Soma dos cortes na biblioteca do cliente. Um arquivo compartilhado conta em cada biblioteca que o tem |
| Suspensão, cancelamento ou expiração | Para só as participações daquele cliente. A captura segue para os outros |

O cliente não aumenta a franquia trocando de preset nem acompanhando um canal já capturado.

## Presets editoriais

Os presets **não rodam modelo nenhum**. Eles repesam os critérios que a análise-base já calculou (`clips.score_breakdown`: intensidade, surpresa, humor, reação, singularidade, audiência e visual). A nota final é metade a nota-base (com as penalidades dela) e metade os critérios do preset. O corte entra quando essa nota fica acima da nota mínima das configurações.

| Preset | Critérios |
| --- | --- |
| Seleção equilibrada | Nota-base |
| Melhores momentos | Nota-base, com 10 pontos a mais de exigência |
| Humor e reações | Humor 45%, reação 35%, audiência 20% |
| Jogadas e highlights | Surpresa 35%, visual 30%, intensidade 20%, singularidade 15% |
| Momentos intensos | Intensidade 50%, reação 30%, surpresa 20% |

| Plano | O que pode |
| --- | --- |
| Basic | Seleção equilibrada (os resultados-base) |
| Creator | Escolhe o preset por canal |
| Pro | Escolhe o preset, e a segunda análise roda nos momentos que ele cobre, quando ela é necessária |

Não há diferença de modelo de IA entre os planos. A interface não promete isso.

## Armazenamento

- **Um arquivo por corte** (`clips/{id}`), compartilhado por todas as bibliotecas que o recebem.
- **Autorização:** cada entrega é um `clip_share` do cliente. A biblioteca responde 404 para quem não tem a entrega, mesmo que o arquivo exista.
- **Proteção:** a limpeza (`storage_lifecycle.protection`) não apaga mídia com compartilhamento ativo. Enquanto um cliente ou a HTS precisar dela, ela fica.
- **Saída de um cliente:**
  - deixar de acompanhar um canal não apaga nada, e os Shorts já recebidos continuam na biblioteca;
  - a rota antiga do #23 apagava o canal e todos os cortes, e foi removida.
- **Reprovação e falha:**
  - a reprovação editorial da HTS não tira o corte da biblioteca de um cliente;
  - só uma falha de render tira.

## O que o cliente vê

- Meus canais, com a preferência de cortes e o estado do acompanhamento: acompanhando agora, ao vivo, pausado pelo plano, ou fora do plano nesta live (com o motivo).
- Shorts gerados (biblioteca), plano e consumo.

**Nunca aparecem:**
- outros clientes ou quantos acompanham o canal;
- workers, jobs ou o pipeline;
- erros internos do canal (`last_error`);
- agendamentos.

O cliente não controla nem agenda processos internos.

## Concorrência (PostgreSQL)

- **Uma sessão `live` por canal:** índice único parcial, trava da linha do canal (`FOR UPDATE`) e inserção em savepoint. Quem perde a corrida entra na sessão existente.
- **Contas:** as linhas de assinatura de todos os clientes envolvidos são travadas **em ordem de id** antes das decisões de cota (`lock_accounts`). Dois workers na mesma live não entram em deadlock.
- **Inserções que podem colidir:** participação na live (única por inscrição e sessão), entrega (um compartilhamento ativo por corte e conta) e job (`idempotency_key`). Todas rodam em savepoint e, se colidirem, reaproveitam o registro do vencedor.
- **Canais por plano:** além da trava, há uma recontagem depois da inserção.
- **Testes reais:** `tests/test_postgres_shared_lives.py` usa um PostgreSQL descartável (`infrastructure/compose/test-postgres.yml` ou o serviço do job `postgres` no CI).
  - Só roda com `ASC_TEST_PG_URL`.
  - Recusa qualquer host que não seja local ou o do CI, e recusa sempre a 212.

## Compatibilidade e migração (0018)

- **Só aditiva:**
  - tabelas `channel_monitors` e `monitor_sessions`;
  - colunas `streamers.hts_monitoring` (verdadeira para todos os canais existentes, então a HTS não muda), `stream_sessions.base_profile` e `clip_shares.monitor_id`;
  - índice `uq_stream_sessions_one_live`.
- **Índice único de live:** se houver duas sessões `live` do mesmo canal, a migração **falha antes de qualquer alteração**.
  - O erro lista os canais e os ids das sessões.
  - Nada é apagado nem corrigido.
  - O operador encerra as duplicatas e roda de novo.
  - No fim, a migração confere que o índice único existe.
  - Testado: falha, repetição, `downgrade` e nova aplicação.
- **Canais do PR #23:** cada canal com `owner_id` vira uma inscrição desse cliente e passa a `hts_monitoring = false`. O acesso por streamer que o dono recebia é encerrado, e os cortes continuam na biblioteca. `owner_id` fica na tabela, sem uso.
- **Workers:** sem mudança de contrato. O job de áudio recebe o perfil fixado da sessão nos mesmos campos (`language`, `whisper_model`).

## Limites conhecidos

- **Perfil-base único por live:** a análise-base tem um perfil por live. Uma segunda análise-base com outro modelo ou idioma, na mesma live, exigiria chavear `audio_features` e `transcript_words` pelo perfil. Hoje os clientes não escolhem modelo, então a compatibilidade é garantida por construção.
- **Retomada no meio da live:** um cliente que parou por falta de horas e renovou no meio da live volta na próxima live, não na mesma.
- **Uma render por corte:** a renderização é compartilhada porque todos usam a mesma saída. Edição por cliente (legendas e layout próprios) ainda não existe.
- **Interruptor da HTS:** `streamers.enabled` continua sendo o interruptor global da HTS. Desativar um canal para todos os clientes. Para a HTS parar de consumir sem afetar clientes, use `hts_monitoring = false`, que ainda não aparece no painel da equipe.
- **Regras das plataformas:** o uso do conteúdo de terceiros segue os termos da Twitch e do YouTube e os direitos do criador. Isso é responsabilidade comercial e jurídica, e o sistema não verifica.

## English

One capture and one base analysis per live, however many customers follow the channel.

**Model:**
- The source channel (`streamers`) is shared.
- A customer follows it through `channel_monitors`, with its own editorial preset.
- Each live has a single `live` session, enforced by a partial unique index and a row lock.
- Each customer is attached to it (`monitor_sessions`) only within its own plan, and is charged once per followed segment against its own franchise.
- A customer that runs out, is suspended or cancels stops alone. The capture continues while the HTS or any customer still needs it.
- The base analysis profile is pinned at session start, so settings changes never mix incompatible results.

**Presets** re-weight the criteria already computed by the base analysis (no extra model runs):
- Basic uses the base selection;
- Creator and Pro choose a preset per channel;
- Pro triggers the second pass only for moments it covers.

**Media:**
- One rendered file per clip.
- Each customer's library is a set of `clip_shares` (`monitor_id`), with backend authorization and cleanup protection, so a file is never deleted while anyone still holds it.
- Leaving a channel keeps the customer's library. HTS editorial rejection does not remove customers' deliveries; a failed render does.

**Migration 0018** is additive, and PR #23 owned channels become monitors (`owner_id` stays unused). It **fails before changing anything** when a channel has duplicate live sessions, listing them; it never fixes data itself, and it verifies the unique index at the end.

**Temporal coverage:** a Short is delivered only if its whole interval lies inside the customer's followed window (0.5 s tolerance).
- Clips captured while access was valid but ready after it ended are still delivered, except after a refund, a chargeback or during a payment review.
- Earlier deliveries are never withdrawn.

**Delivery rights per paid period (owner decision):** each charged segment is linked to the paid period that covered it (`entitlement_periods`: subscription, plan and limits at that time). A clip is judged by those periods, never by the current or latest subscription:
- paid periods deliver even after expiry, normal cancellation or operational suspension;
- a fully refunded period (refund, chargeback, lost dispute) delivers nothing;
- a financial review or open dispute keeps deliveries pending until a later snapshot resolves it;
- the plan, preset and limits applied are those of the paying period;
- a security block (deactivated account) stops deliveries and hides the library until reactivation;
- legitimately delivered Shorts are kept.

**PostgreSQL:** concurrency relies on:
- the partial unique index and row locks;
- account rows locked in id order;
- savepoint inserts for attachments, deliveries and jobs.

`tests/test_postgres_shared_lives.py` runs against a disposable PostgreSQL only (test compose or CI service) and refuses 212.

**Known limits:**
- one base profile per live;
- resuming mid-live after a renewal waits for the next live;
- no per-customer render settings yet;
- the `hts_monitoring` switch is not in the staff UI yet;
- platform content rules are a business and legal responsibility.
