# Privacy Policy

Version {{VERSAO}}, effective {{VIGENCIA}}. The Portuguese version prevails in case of doubt.

This policy explains which personal data AI Stream Clipper by HTS processes, why, who we share it with, how long we keep it and how you exercise your rights. It covers the site getclipper.howto.software, the dashboard clipper.howto.software and the service's e-mails.

## Who the controller is

{{RAZAO_SOCIAL}} ({{NOME_FANTASIA}}), Brazilian company registration (CNPJ) {{CNPJ}}, at {{ENDERECO}}. Data protection officer: {{ENCARREGADO}}, at {{EMAIL_PRIVACIDADE}}.

## Data we process

### Account

- E-mail and password. The password is stored only as an Argon2 hash; nobody at HTS can read it.
- Account role, status (active or deactivated), creation date and last sign-in.
- Sign-up and password links: we keep only a hash of the link, which works once and expires (72 hours for sign-up, 2 hours for a reset).

### Use and security

- Security and audit records: the account e-mail, the action taken (for example signing in, changing a channel or a publishing rule), the date and the IP address. Failed sign-in attempts are recorded too.
- Plan usage counters: live hours followed, channels, simultaneous lives, storage and publications.

### Channels and content

- The public Twitch and YouTube channels you choose to follow.
- From those channels' lives: the stream's audio and video, the speech transcript and audio signals used to find the best moments. Chat is not captured.
- The clips produced, with titles, captions and framing. To frame vertical video we detect where faces are in the image; we do not identify who the person is.
- **Voice profiles:** to tell who is speaking in each moment, the system computes a numeric representation of each utterance's voice and builds a voice profile of the channel's streamer. This may be biometric, sensitive personal data of the streamer and of anyone heard speaking on the live. We use these profiles only to organise speech in clips, never to identify people outside the service.

### Publishing to your networks

If you connect a destination (YouTube today; Instagram and Facebook when available), we store the access tokens the network provides, encrypted with AES-256-GCM, and the publishing rules you set. We also store your authorisation for us to publish on your behalf and your rights declaration for each channel, with date and time. You can disconnect a destination at any time; its tokens are deleted immediately.

### Payment

- Plan, currency, subscription status, period dates, amounts and payment history, subscription identifiers at Stripe or InfinitePay, and promotion codes used.
- **We never receive or store your card details.** Payment happens on Stripe's page (US dollars) or InfinitePay's (Brazilian reais, Pix or card).

### Waitlist

When open: e-mail, plan of interest, country, streaming platform, language, currency and the consent version you accepted.

## Why we use it and on what legal basis

Legal bases under Brazil's LGPD (art. 7 and, for sensitive data, art. 11):

- **Creating and keeping your account, providing the service, producing and delivering clips, billing the plan:** performance of a contract.
- **Publishing to your networks under your rules:** performance of a contract and your consent, which you withdraw by disconnecting the destination.
- **Account security, fraud and abuse prevention, access records:** legitimate interest and legal obligation (Brazilian Internet Civil Framework, art. 15).
- **Tax and payment records:** legal obligation.
- **Voice profiles to tell who is speaking (sensitive data):** {{BASE_LEGAL_VOZ}}.
- **Sign-up, password and renewal e-mails:** performance of a contract.
- **Early-access e-mails (waitlist):** consent.

We do not sell personal data and do not use your data for advertising.

## Artificial intelligence

All analysis (transcription, audio, video, titles) runs on HTS's own servers. No live or clip content is sent to third-party AI services. The AI suggests moments; you decide what gets published.

## Who we share it with

- **Stripe** (US-dollar payments, United States) and **InfinitePay** (Brazilian-real payments, Brazil): receive your e-mail and account identifier to link the payment.
- **Mailgun** (e-mail delivery, United States): receives your e-mail and the content of service messages. Click and open tracking is off.
- **Cloudflare** (site delivery and protection, United States): processes your IP and requests to the site.
- **Google/YouTube and Meta** (when you connect a destination): receive the videos and texts you told us to publish.
- Authorities, when required by law or court order.

Some of these providers are outside Brazil. International transfers rely on these providers' contractual safeguards, under LGPD art. 33.

### Shared lives

When several customers follow the same channel, the live is captured and analysed once. Each customer receives only the clips from the period in which it followed the channel with its plan paid. No customer sees who the others are, how many there are, or their libraries.

## How long we keep it

- **Raw live recording:** held in a temporary buffer of about 30 minutes during capture; whatever does not become a clip is discarded.
- **Transcripts and analysis signals:** deleted 30 days after the live ends.
- **Clips:** stay in your library while your account exists, within your plan's storage. After the subscription ends, clips already delivered remain available to you. After the account is closed, they are deleted within {{PRAZO_EXCLUSAO_CONTA}}, unless the law requires otherwise.
- **Voice profiles and voice segments:** kept while the channel is followed by the service; the data subject can ask for deletion.
- **Access records:** at least 6 months, as Brazilian law requires.
- **Payment data:** for the period required by tax law.
- **Waitlist:** until launch or until you ask to leave.

## Cookies and browser storage

- The dashboard uses a single cookie, `asc_session`, essential to keep you signed in (HttpOnly, SameSite=Strict, 12 hours).
- The dashboard stores your language and display preferences in the browser.
- The commercial site uses no cookies.
- We use no analytics tools, advertising pixels or third-party trackers. Fonts are served from our own servers.

## Your rights

You may ask for: confirmation that we process your data, access, correction, anonymisation or deletion of unnecessary data, portability, information about sharing, review of automated decisions and withdrawal of consent (LGPD art. 18). You may also complain to Brazil's data protection authority (ANPD). If you live in another country, you have the rights your local law grants as well.

To exercise any right, write to {{EMAIL_PRIVACIDADE}} from your account's e-mail. We answer within 15 days.

Streamers and other people heard on the lives being followed, even without an account, can exercise these rights at the same address, including deletion of their voice profile.

## Security

We use HTTPS, Argon2 passwords, encrypted social-network tokens, signed communication between our services, role-based access control and audit logging. If a security incident may cause relevant risk, we notify the affected people and the ANPD.

## Minors

The service is meant for people aged 18 or over, or minors represented by their legal guardians.

## Changes to this policy

We announce relevant changes by e-mail or in the dashboard before they take effect. The version and date are at the top of this page.

## Contact

{{RAZAO_SOCIAL}}, {{EMAIL_CONTATO}}. Privacy and personal data: {{EMAIL_PRIVACIDADE}}.
