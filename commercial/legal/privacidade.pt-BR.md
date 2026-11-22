# Política de Privacidade

Versão {{VERSAO}}, em vigor desde {{VIGENCIA}}.

Esta política explica quais dados pessoais o AI Stream Clipper by HTS trata, para quê, com quem compartilha, por quanto tempo guarda e como você exerce os seus direitos. Ela vale para o site getclipper.howto.software, o painel clipper.howto.software e os e-mails do serviço.

## Quem é o controlador

{{RAZAO_SOCIAL}} ({{NOME_FANTASIA}}), CNPJ {{CNPJ}}, com endereço em {{ENDERECO}}. Encarregado pelo tratamento de dados pessoais (DPO): {{ENCARREGADO}}, pelo e-mail {{EMAIL_PRIVACIDADE}}.

## Que dados tratamos

### Conta

- E-mail e senha. A senha é guardada apenas como hash Argon2; ninguém na HTS consegue lê-la.
- Papel da conta, situação (ativa ou desativada), data de criação e do último acesso.
- Links de cadastro e de troca de senha: guardamos só uma impressão (hash) do link, que vale uma vez e expira (72 horas no cadastro, 2 horas na troca de senha).

### Uso e segurança

- Registros de segurança e auditoria: e-mail da conta, ação feita (por exemplo, entrar, mudar um canal ou uma regra de publicação), data e endereço IP. Tentativas de login com falha também ficam registradas.
- Contadores de uso do plano: horas de live acompanhadas, canais, lives simultâneas, armazenamento e publicações.

### Canais e conteúdo

- Os canais públicos da Twitch e do YouTube que você escolhe acompanhar.
- Das lives desses canais: áudio e vídeo da transmissão, a transcrição da fala e sinais de áudio usados para achar os melhores momentos. O chat não é capturado.
- Os clipes gerados, com títulos, legendas e o enquadramento. Para enquadrar o vídeo vertical, detectamos onde há rostos na imagem; não identificamos quem é a pessoa.
- **Perfis de voz:** para separar quem fala em cada trecho, o sistema calcula uma representação numérica da voz de cada fala e monta um perfil de voz do streamer do canal. Isso pode ser considerado dado biométrico, ou seja, dado pessoal sensível, do streamer e de quem aparece falando na live. Usamos esses perfis só para organizar a fala nos clipes, nunca para identificar pessoas fora do serviço.

### Publicação nas suas redes

Se você conectar um destino (hoje, YouTube; Instagram e Facebook quando liberados), guardamos os tokens de acesso que a rede fornece, cifrados com AES-256-GCM, e as regras de publicação que você definir. Também guardamos a sua autorização para publicarmos em seu nome e a sua declaração de direitos sobre cada canal, com data e hora. Você pode desconectar um destino a qualquer momento; os tokens são apagados na hora.

### Pagamento

- Plano, moeda, situação da assinatura, datas do período, valores e histórico de pagamentos, identificadores da assinatura no Stripe ou na InfinitePay, e cupons usados.
- **Não recebemos nem guardamos os dados do seu cartão.** O pagamento é feito na página do Stripe (em dólar) ou da InfinitePay (em real, Pix ou cartão).

### Lista de espera

Quando aberta: e-mail, plano de interesse, país, plataforma em que você transmite, idioma, moeda e a versão do consentimento aceito.

## Para que usamos e com qual base legal

Bases legais da LGPD (art. 7º e, para dados sensíveis, art. 11):

- **Criar e manter a conta, prestar o serviço contratado, gerar e entregar clipes, cobrar o plano:** Execução de contrato.
- **Publicar nas suas redes conforme as suas regras:** Execução de contrato e o seu consentimento, que você pode retirar desconectando o destino.
- **Segurança da conta, prevenção de fraude e de abuso, registros de acesso:** Legítimo interesse e obrigação legal (Marco Civil da Internet, art. 15).
- **Registros fiscais e de pagamento:** Obrigação legal.
- **Perfis de voz para separar quem fala (dado sensível):** {{BASE_LEGAL_VOZ}}.
- **E-mails de cadastro, troca de senha e renovação:** Execução de contrato.
- **E-mails sobre o acesso antecipado (lista de espera):** Consentimento.

Não vendemos dados pessoais e não usamos os seus dados para publicidade.

## Inteligência artificial

Toda a análise (transcrição, áudio, vídeo, títulos) roda em servidores da própria HTS. Nenhum conteúdo das lives ou dos clipes é enviado a serviços de IA de terceiros. A IA sugere os momentos; você decide o que publicar.

## Com quem compartilhamos

- **Stripe** (pagamentos em dólar, Estados Unidos) e **InfinitePay** (pagamentos em real, Brasil): recebem o seu e-mail e o identificador da sua conta para vincular o pagamento.
- **Mailgun** (envio de e-mails, Estados Unidos): recebe o seu e-mail e o conteúdo das mensagens do serviço. O rastreamento de cliques e aberturas fica desligado.
- **Cloudflare** (rede de entrega e proteção do site, Estados Unidos): trata o seu IP e as requisições ao site.
- **Google/YouTube e Meta** (quando você conecta um destino): recebem os vídeos e textos que você mandou publicar.
- Autoridades, quando exigido por lei ou ordem judicial.

Alguns desses fornecedores ficam fora do Brasil. A transferência internacional se apoia nas garantias contratuais desses fornecedores, conforme o art. 33 da LGPD.

### Lives compartilhadas

Quando mais de um cliente acompanha o mesmo canal, a live é capturada e analisada uma vez só. Cada cliente recebe apenas os clipes do período em que acompanhava o canal com o plano em dia. Nenhum cliente vê quem são os outros, quantos são, nem as bibliotecas deles.

## Por quanto tempo guardamos

- **Gravação bruta da live:** mantida em memória temporária de cerca de 30 minutos durante a captura; o que não vira clipe é descartado.
- **Transcrições e sinais de análise:** apagados 30 dias depois do fim da live.
- **Clipes:** ficam na sua biblioteca enquanto a sua conta existir, dentro do armazenamento do plano. Depois do fim da assinatura, os clipes já entregues continuam disponíveis para você. Após o encerramento da conta, são excluídos em {{PRAZO_EXCLUSAO_CONTA}}, salvo obrigação legal.
- **Perfis e trechos de voz:** mantidos enquanto o canal for acompanhado pelo serviço; o titular pode pedir a exclusão.
- **Registros de acesso:** pelo menos 6 meses, como exige o Marco Civil da Internet.
- **Dados de pagamento:** pelo prazo exigido pela legislação fiscal.
- **Lista de espera:** até o lançamento ou até você pedir a saída.

## Cookies e armazenamento no navegador

- O painel usa um único cookie, `asc_session`, essencial para manter você conectado (HttpOnly, SameSite=Strict, 12 horas).
- O painel guarda no navegador o idioma e preferências de exibição.
- O site comercial não usa cookies.
- Não usamos ferramentas de análise, pixels de publicidade nem rastreadores de terceiros. As fontes ficam nos nossos servidores.

## Seus direitos

Você pode pedir: confirmação de que tratamos seus dados, acesso, correção, anonimização ou exclusão de dados desnecessários, portabilidade, informação sobre compartilhamento, revisão de decisões automatizadas e a retirada do consentimento (LGPD, art. 18). Também pode reclamar à Autoridade Nacional de Proteção de Dados (ANPD).

Para exercer qualquer direito, escreva para {{EMAIL_PRIVACIDADE}} a partir do e-mail da sua conta. Respondemos em até 15 dias.

Streamers e outras pessoas que aparecem nas lives acompanhadas, mesmo sem conta no serviço, também podem exercer esses direitos pelo mesmo e-mail, inclusive pedir a exclusão do perfil de voz.

## Segurança

Usamos HTTPS, senhas com Argon2, tokens de redes sociais cifrados, comunicação assinada entre os nossos serviços, controle de acesso por papel e registro de auditoria. Se houver um incidente de segurança que possa causar risco relevante, avisamos os titulares afetados e a ANPD.

## Menores de idade

O serviço é destinado a maiores de 18 anos, ou a menores representados por seus responsáveis legais.

## Mudanças nesta política

Quando mudarmos algo relevante, avisamos por e-mail ou no painel antes de a mudança valer. A versão e a data ficam no topo desta página.

## Contato

{{RAZAO_SOCIAL}}, {{EMAIL_CONTATO}}. Privacidade e dados pessoais: {{EMAIL_PRIVACIDADE}}.
