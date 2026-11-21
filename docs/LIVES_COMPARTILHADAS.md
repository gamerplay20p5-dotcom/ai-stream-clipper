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
5. **Entrega:** quando o corte fica pronto, ele entra na biblioteca de cada cliente cujo trecho cobre o momento, cujo preset o seleciona e cujo armazenamento comporta o arquivo.

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

## Compatibilidade e migração (0018)

- **Só aditiva:**
  - tabelas `channel_monitors` e `monitor_sessions`;
  - colunas `streamers.hts_monitoring` (verdadeira para todos os canais existentes, então a HTS não muda), `stream_sessions.base_profile` e `clip_shares.monitor_id`;
  - índice `uq_stream_sessions_one_live`.
- **Índice único de live:** só é criado se não houver duas sessões `live` do mesmo canal. Se houver, a migração avisa e não reescreve dados. A trava da linha continua valendo.
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

**Migration 0018** is additive. PR #23 owned channels become monitors, and `owner_id` stays unused.

**Known limits:**
- one base profile per live;
- resuming mid-live after a renewal waits for the next live;
- no per-customer render settings yet;
- the `hts_monitoring` switch is not in the staff UI yet;
- platform content rules are a business and legal responsibility.
