# Verificação do app OAuth no Google (YouTube)

*Português primeiro; English summary at the end.*

## O que realmente se aplica ao nosso uso

| Ponto | Situação |
|---|---|
| Escopos usados | `youtube.upload` (enviar os cortes) e `youtube.readonly` (ID e nome do canal, processamento e estatísticas dos vídeos que o app enviou). São os mínimos: `channels.list mine=true` e `videos.list` não aceitam o escopo `youtube.upload`. |
| Tipo de escopo | **Sensíveis**, não restritos. Por isso a verificação pede página inicial, política de privacidade, domínio verificado e, em geral, um vídeo de demonstração. Não exige avaliação de segurança (CASA), que é só para escopos restritos. |
| Vídeos públicos | **Já permitidos** sem verificação nem auditoria desde 02/11/2026 ([histórico de revisões](https://developers.google.com/youtube/data/revision_history)). |
| Cota | `videos.insert`: 100 envios por dia no bucket **Video Uploads**. Mais que isso exige a **auditoria de conformidade da YouTube API** ([guia](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits)). |
| Status "Em teste" | O refresh token expira em **7 dias** e só usuários de teste conectam. |
| "Em produção" sem verificação | Para escopos sensíveis, o Google mostra o aviso "app não verificado" e limita o app a 100 usuários. Para uso interno de um único dono de canal, isso pode bastar e o token deixa de expirar em 7 dias. **Confirme na tela do Google Auth Platform antes de decidir.** |

## Checklist

- [ ] **Domínio autorizado** `howto.software` em Google Auth Platform → Branding → Domínios autorizados.
- [x] **Prova de controle do domínio** no [Google Search Console](https://search.google.com/search-console), com uma conta que seja proprietária ou editora do projeto do Google Cloud.
  - Depende do Hank: verificação por registro TXT no DNS de `howto.software`, que está na Cloudflare.
  - **Não vou alterar o DNS:** o registro TXT precisa ser adicionado pelo dono do domínio.
- [ ] **Branding:**
  - nome: "AI Stream Clipper";
  - e-mail de suporte;
  - logotipo de 120×120 px, opcional (com logotipo, o Google também verifica a marca);
  - e-mail de contato do desenvolvedor.
- [ ] **Página inicial:** `https://clipper.howto.software/`.
- [x] **Política de privacidade:** `https://clipper.howto.software/privacidade` (v0.4.6: responsável HowTo.Software, contato friendhost1@gmail.com, resposta em 15 dias; cobre dados, escopos, Limited Use, tokens, compartilhamento, retenção, desconexão e exclusão).
- [ ] **Termos de uso:** `https://clipper.howto.software/termos`.
- [ ] **Escopos** em Acesso a dados: apenas `youtube.upload` e `youtube.readonly`, com justificativa:
  - *upload:* "enviar ao canal do próprio usuário os cortes que ele aprovou no painel";
  - *readonly:* "identificar o canal conectado e acompanhar o processamento e as estatísticas dos vídeos enviados pelo app".
- [ ] **Publicar o app** em Público-alvo, mudando de "Em teste" para "Em produção".
- [ ] **Enviar para verificação** na Central de verificação, se quiser remover o aviso de app não verificado.
- [ ] **Auditoria da YouTube API**, só se precisar de mais de 100 envios por dia: *YouTube API Services – Audit and Quota Extension Form*.

## Roteiro do vídeo de demonstração (se o Google pedir)

Grave em inglês, ou com legendas em inglês, mostrando a URL na barra do navegador:

1. Abrir `https://clipper.howto.software/`, mostrar a página inicial e o link da política de privacidade.
2. Entrar no painel e ir em **Publicação → Conectar conta YouTube**.
3. Mostrar a **tela de consentimento do Google**, com o nome do app e os dois escopos, e o **Client ID** visível na URL. Autorizar.
4. Voltar ao painel com o canal conectado.
5. Abrir um corte na Central de cortes, depois **Publicar nas redes**, escolher a visibilidade e publicar. Mostrar o vídeo enviado no YouTube Studio. Esta etapa demonstra o `youtube.upload`.
6. Mostrar o calendário com o estado "Publicado", o link e as estatísticas. Esta etapa demonstra o `youtube.readonly`.
7. Mostrar **Desconectar** e onde revogar o acesso em `myaccount.google.com/permissions`.

## O que depende de outras pessoas

| Item | Quem |
|---|---|
| Registro TXT de verificação no DNS de `howto.software` | Hank (dono do domínio) |
| Responsável legal, e-mail de privacidade e prazo de resposta nas páginas | definido: HowTo.Software / friendhost1@gmail.com / 15 dias |
| Logotipo, e-mail de suporte e envio do formulário no Google Cloud | você |

---

## English summary

- **Scopes:** `youtube.upload` and `youtube.readonly` (the minimum: `channels.list mine=true` and `videos.list` do not accept the upload scope). Both are *sensitive*, not restricted, so no security assessment is needed.
- **Public uploads:** allowed for unverified projects since 2 Nov 2026.
- **Quota:** `videos.insert` costs 1 unit in the Video Uploads bucket, 100 calls/day by default. More requires the YouTube API Services audit.
- **Verification checklist:**
  - authorised domain `howto.software`, proven in Search Console (DNS TXT by the domain owner);
  - branding;
  - homepage `https://clipper.howto.software/`;
  - privacy policy `/privacidade`;
  - terms `/termos`;
  - scope justifications;
  - publish to production;
  - submit for verification;
  - a demo video following the script above.
- **Done (v0.4.6):** domain verified in Search Console; privacy policy expanded (controller HowTo.Software, privacy contact, 15-day response, Limited Use statement, scopes, retention, disconnection, deletion).
