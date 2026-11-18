# Fase 4: publicação automática (YouTube Shorts, TikTok, Instagram Reels)

## Estado de cada integração

| Plataforma | Código | Validação real | Observação |
|---|---|---|---|
| YouTube Shorts | Pronto: OAuth com PKCE, upload retomável, metadados, privacidade, agendamento (`publishAt`), status e estatísticas | **Validado em 18/11/2026**: vídeo `-6KAO94bZUg` privado no canal de teste, `processed` segundo a API (detalhes abaixo) | Projetos do Google não verificados só conseguem enviar vídeos **privados** (política do Google) |
| TikTok | Pronto: Content Posting API (Direct Post, envio de arquivo), `creator_info`, consulta de status | Não validado: exige um app do TikTok com os escopos `video.publish` e `video.upload` | Apps sem auditoria só postam `SELF_ONLY`; **somente manual** (consentimento a cada postagem) |
| Instagram Reels | Pronto: Instagram API com login do Instagram, envio retomável pelo `rupload` (sem URL pública), publicação e insights | Não validado: exige app da Meta e conta profissional | Reels são sempre públicos; postagem automática exige ativação explícita de "público automático" |

## Primeiro envio real (v0.4.0, 18/11/2026)

- **O que foi enviado:** o corte 9, um trecho de 40 s do vídeo sintético próprio `chase_crash`, sem conteúdo de terceiros. Publicação manual, como **privado**.
- **Resultado:** o vídeo `-6KAO94bZUg` (https://youtube.com/shorts/-6KAO94bZUg) foi criado no canal conectado.
- **Tempos:** o upload levou cerca de 2 s (10,6 MB) e o processamento no YouTube cerca de 37 s.
- **Confirmação independente pela API** (`videos.list`):
  - `uploadStatus=processed`
  - `privacyStatus=private`
  - `processingStatus=succeeded`
  - duração `PT41S`
- **Gravado no PostgreSQL:**
  - publicação `published`, com 1 tentativa;
  - job `publish` `done`;
  - cota do dia em 1600 unidades;
  - token guardado cifrado (o texto puro não aparece no banco).
- **Validado em produção:**
  - pedir de novo o mesmo corte devolve a mesma publicação, sem novo envio;
  - o reenvio de uma publicação já concluída é recusado (409);
  - um corte rejeitado é recusado com a explicação;
  - um `state` OAuth forjado é recusado.

## Arquitetura

- **`asc-worker-publisher` (210)** é um serviço próprio: `clipper publisher`, imagem do app, 1 CPU e 1 GB.
  - Acessa o PostgreSQL diretamente, então os tokens nunca passam pela API interna dos workers.
  - Lê os vídeos renderizados no volume `asc_clips`, montado como somente leitura.
  - Fala com as APIs oficiais por HTTPS.
- **Fila:** usa a mesma fila do PostgreSQL, com jobs do tipo `publish`.
- **Laço do publisher:**
  1. batimento;
  2. regras automáticas;
  3. enfileira as publicações que chegaram na hora;
  4. envia uma publicação;
  5. consulta o processamento a cada 60 s;
  6. atualiza as métricas oficiais a cada 6 h.
- **Tabelas** (migração `0005`): `publish_apps`, `oauth_states`, `publish_accounts`, `publish_rules`, `publications`.

## Segurança e regras de publicação

- **Tokens e segredos:** os tokens OAuth e o *client secret* são cifrados com AES-256-GCM. A chave é derivada por HKDF do `ASC_JWT_SECRET`, com rótulo próprio. A API nunca os devolve: mostra apenas "configurado".
  - Se o `ASC_JWT_SECRET` for trocado, os tokens guardados deixam de abrir e as contas precisam ser reconectadas.
- **OAuth:**
  - `state` de uso único, com validade de 15 minutos;
  - PKCE no Google e no TikTok;
  - cada conexão e desconexão fica registrada na auditoria.
- **Um corte é enviado no máximo uma vez por conta:**
  - restrição única no banco para o par (corte, conta);
  - a sessão de upload retomável é gravada antes do primeiro byte. Depois de um timeout ou reinício, o envio continua de onde parou. Se o envio já tinha terminado, o vídeo criado é recuperado pela própria sessão.
- **Elegibilidade:** verificada ao criar a publicação e de novo antes de enviar.
  - Cortes de **teste manual** nunca são publicados automaticamente. Também não se publica corte rejeitado, sem vídeo ou com análise incompleta.
  - **Direito de republicação:** o streamer precisa estar marcado na página Publicação. Na publicação manual, você confirma caso a caso.
  - **Aprovação humana:** é exigida por padrão nas regras.
  - **Filtros:** nota mínima, avaliação humana "ruim/indevido", "falta de contexto", "duplicado", e momento muito próximo de outro já publicado na mesma conta.
  - **Privacidade:** o padrão é **privado**. Postagem pública automática exige marcar "permitir público automático" na regra.
- **Limites:**
  - máximo de publicações por dia por regra;
  - pelo menos 60 s entre envios na mesma conta;
  - cota da YouTube Data API controlada por conta (`videos.insert` custa 1600 das 10 000 unidades diárias);
  - respeito ao `Retry-After` e ao limite de 24 h do Instagram.
- **Erros:**
  - token revogado: a conta vai para "expirada" e a publicação falha, sem repetir em laço;
  - erros temporários: o sistema tenta de novo com espera crescente, até 6 vezes;
  - reenvio administrativo pelo painel.

## Configurar o YouTube (passo a passo)

1. **Projeto e API:** no [Google Cloud Console](https://console.cloud.google.com/), crie um projeto (ou use um existente) e ative a **YouTube Data API v3**.
2. **Tela de consentimento OAuth:**
   - tipo **Externo**;
   - status **Em teste**;
   - adicione o seu e-mail como **usuário de teste**;
   - escopos: `youtube.upload` e `youtube.readonly`.
3. **Credencial:** em **Credenciais**, crie um **ID do cliente OAuth** do tipo **App para computador**.
   - Esse tipo aceita retorno em `http://127.0.0.1`, o que é necessário porque o servidor só existe na rede local.
4. **Cadastro no painel:** em **Publicação**, no app do YouTube, preencha:
   - Client ID e Client secret;
   - URI de retorno `http://127.0.0.1:8765/`.
5. **Conexão:**
   - clique em **Conectar conta YouTube Shorts** e autorize na aba que abrir;
   - a página final vai dar erro de conexão, o que é esperado;
   - copie o endereço completo da barra (com `?code=`), cole no painel e clique em **Concluir conexão**.
6. **Primeiro envio de teste:** na Central de cortes, abra um corte pronto, depois **Publicar nas redes** → conta → **Privado** → **Publicar agora**. Acompanhe em Publicação → Calendário.

Em modo "Em teste", o Google expira o refresh token em 7 dias: reconecte quando o painel mostrar "expirada". Para uso contínuo e vídeos públicos, o projeto precisa passar pela verificação do Google e pela auditoria de conformidade da YouTube API.

## TikTok e Instagram (quando houver apps aprovados)

- **TikTok:** crie um app no TikTok for Developers com Login Kit e Content Posting API.
  - Escopos: `user.info.basic`, `video.publish`, `video.upload`.
  - Cadastre um Redirect URI `https` e use o mesmo no painel.
  - Até a auditoria do TikTok, só é possível postar como "Só eu".
- **Instagram (Login Unificado da Meta, apps novos):** em Meta for Developers → app → "API do Instagram (login unificado)":
  - **URL de redirecionamento:** `https://192.168.1.210:8780/api/publish/oauth/instagram/callback` (HTTPS, exatamente igual).
  - Copie o **config_id** da configuração (Login do Facebook para Empresas → Configurações).
  - Copie o **ID do app** e a **chave secreta** em Configurações do app → Básico.
  - No painel (Publicação → Instagram): ID do app, chave secreta, a mesma URL de redirecionamento e o config_id.
  - A conta precisa ser profissional e, no modo Desenvolvimento, ter papel no app (testador do Instagram).
  - O retorno chega direto ao painel, sem colar endereço. O `state` de uso único autentica o retorno, e o cookie do painel continua `SameSite=strict`.
  - O token é de usuário do sistema da empresa (SUAT). Quando expirar, a conta precisa ser reconectada.
  - Sem config_id, o sistema usa o fluxo antigo (login do Instagram, com escopos).
- **Conexão:** no TikTok, o fluxo no painel é o mesmo do YouTube: conectar, autorizar e colar o endereço final. No Instagram com login unificado, o retorno é automático.

## Direitos de terceiros

O sistema só automatiza a publicação de streamers marcados com direito de republicação, e toda publicação leva uma linha de créditos com o link da origem. Isso não substitui a autorização: republicar lives de terceiros sem permissão pode violar direitos autorais e os termos das plataformas. Use apenas conteúdo próprio ou autorizado.
