# Estudo de viabilidade comercial e precificação — AI Stream Clipper by HTS

**Data:** 20/11/2026 · **Escopo:** Brasil e EUA, planos Basic / Creator / Pro · **Status:** estudo para decisão. Nada foi
implementado, nenhum plano existente foi alterado e não houve deploy.

Cada número traz uma etiqueta:

| Etiqueta | Significado |
|---|---|
| **[VERIFICADO]** | Lido em página oficial ou fonte datada, com URL na seção 9 |
| **[MEDIDO]** | Medido na produção em 20/11/2026, por consulta somente leitura ao banco e às máquinas |
| **[ESTIMATIVA]** | Cálculo a partir de dados medidos ou verificados; o método está descrito |
| **[HIPÓTESE]** | Premissa ainda não validada; precisa de teste ou de dado real |

O modelo de cálculo está em [`docs/estudo_comercial_modelo.py`](estudo_comercial_modelo.py). Ele não tem
dependências; para refazer as contas com outras premissas, edite o topo do arquivo e rode de novo.

---

## 0. Resumo executivo

1. **O gargalo é GPU, não CPU.** Com as duas Arc A750 de hoje, a IA visual suporta com segurança
   **~19 lives simultâneas** na mistura de planos proposta, ou ~28 se todos usassem a IA padrão. Isso equivale a
   **~25 a 63 clientes**, conforme o uso da franquia. A CPU aguentaria ~56 lives. **[ESTIMATIVA sobre dados MEDIDOS]**
   Isso corrige a estimativa informal anterior (200 a 250 clientes), que considerou só a CPU.
2. **Uma GPU a mais rende muito.** Com 3 GPUs: ~38 a 95 clientes. Com 4 GPUs: ~50 a 126 clientes. Uma placa custa
   pouco perto da receita que libera; o custo exato está em [HIPÓTESE] na seção 6.
3. **O custo marginal de processamento é baixo.** Energia e hardware por hora de live ficam em centavos
   **[ESTIMATIVA]**. O que pesa é custo fixo, taxas de pagamento e impostos. A margem de contribuição por cliente
   fica entre 67% e 88% **[ESTIMATIVA]**. A capacidade, e não o custo por cliente, é o que limita o crescimento.
4. **Os preços propostos estão acima do mercado nos planos Creator e Pro dos EUA.** Os concorrentes de clipping
   para streamers custam de US$ 15 a US$ 40 por mês **[VERIFICADO]**. Um Pro a US$ 99,99 só se sustenta como
   produto "para agência e multicanal", não como clipping comum.
5. **No Brasil existem ferramentas locais de R$ 39,90 a R$ 59,90** **[VERIFICADO, fontes dos próprios
   fornecedores]**. O Basic a R$ 59,90 é defensável; Creator a R$ 149,90 e Pro a R$ 299,90 pedem prova de valor.
6. **O plano Pro proposto é o mais caro de servir.** Ele combina 200 h, 3 lives simultâneas e IA pesada (cerca de
   3 vezes a GPU do padrão). Um cliente Pro que usa tudo consome cerca de 15 vezes a GPU de um Basic e paga 5 vezes
   mais. Recomendo limitar a IA avançada por horas, ou rodá-la só na segunda análise.
7. **GTA VI saiu em 19/11/2026**, com recorde histórico de audiência ao vivo (7,91 milhões de pico)
   **[VERIFICADO]**. Ainda não há versão para PC anunciada **[VERIFICADO]**, então o RP de GTA VI (servidores
   modificados) ainda não existe. No curto prazo, o mercado é GTA VI no console mais o GTA V RP que continua.
8. **Lacuna de produto: Kick.** A Kick já tem mais horas assistidas de GTA V que a Twitch **[VERIFICADO]**, e o
   sistema só monitora Twitch e YouTube **[MEDIDO no código]**. Para o nicho de GTA RP, suportar a Kick é
   prioridade.

---

## 1. Pesquisa de mercado

### 1.1 Concorrentes internacionais (preços em USD) **[VERIFICADO]**

Páginas oficiais lidas em 20/11/2026, salvo indicação.

| Ferramenta | Plano | Preço mensal | Anual | O que limita | Observações |
|---|---|---|---|---|---|
| **OpusClip** | Free | US$ 0 | — | 60 min de vídeo/mês | marca d'água, até 1080p |
| | Starter | US$ 15 | — | 150 min/mês | sem marca d'água, posta em YT/TikTok/IG |
| | Pro | US$ 29 | anual não exibido na página; terceiros citam US$ 14,50/mês | 300 min/mês (3.600/ano) | agenda posts, até 4 brand kits, 2–4 usuários, API |
| | Business | sob consulta | — | personalizado | 4K para agendar e baixar |
| **StreamLadder** (página "verificada em 16/11/2026") | Free | US$ 0 | — | 720p30, sem IA | — |
| | Starter | US$ 15 | US$ 150/ano | sem IA (ClipGPT) | 1080p60, publica e agenda |
| | Creator | US$ 27 | US$ 270/ano | IA "ilimitada em uso justo", até 30 clipes por live | Twitch/Kick por link |
| | Pro | US$ 39 | US$ 390/ano | igual ao Creator | **4K60**, 2 editores |
| **Eklipse** (pago desde jun/2026) | Premium | US$ 24,99 | US$ 179,99/ano | não publicado | 1080p60; clipes guardados 14 dias (mensal) ou 90 dias (anual) |
| | Premium Pro | US$ 39,99 | não publicado | não publicado | aparece só no FAQ de preços |
| **Klap** (terceiros) | Basic | ~US$ 29 (US$ 14,50 no anual) | — | 100 clipes/mês | genérico, não focado em streams |
| **Vizard** (terceiros) | Creator / Business | US$ 29 / US$ 39 | US$ 14,50 / US$ 19,50 por mês no anual | minutos de vídeo | genérico |

**O que esses dados mostram [ESTIMATIVA]:**

- **Faixa de preço:** o mercado de clipping automático para streamers se concentra entre **US$ 15 e US$ 40 por
  mês**. Ninguém publica plano acima de ~US$ 40 sem ser "Business/sob consulta".
- **Como cobram:** por **minutos de vídeo processados** (OpusClip, Vizard), por **clipes** (Klap) ou com "uso justo"
  ilimitado (StreamLadder, Eklipse).
- **4K é recurso de topo** (StreamLadder Pro a US$ 39, OpusClip Business).
- **Ninguém publica cota de armazenamento em GB.** Eklipse guarda os clipes por um prazo (14 ou 90 dias), em vez de
  limitar o espaço.

### 1.2 Concorrentes no Brasil (preços em BRL) **[VERIFICADO, páginas dos próprios fornecedores]**

| Ferramenta | Preço | Modelo |
|---|---|---|
| Curupira | R$ 39,90/mês (300 créditos); anual R$ 794,90 | créditos; aceita VODs da Twitch |
| Real Oficial | a partir de R$ 59,90/mês (blog da própria empresa) | Pix, publica automaticamente, automação de DMs |
| CortaIA | por minuto processado; há plano grátis | minutos |
| SmartClips | R$ 47,90 por 6 meses (pagamento único) | programa local para Windows |
| OpusClip para brasileiros | cobrado em USD; Pro ≈ R$ 165 + IOF (estimativa de um concorrente) | sem tabela em BRL encontrada |

**O que esses dados mostram [ESTIMATIVA]:** as ferramentas locais ficam entre **R$ 40 e R$ 60 por mês** e
competem por preço e por Pix. As internacionais saem por **R$ 90 a R$ 240 por mês** para o brasileiro, por causa
do câmbio e do IOF. Há espaço entre as duas faixas para um produto brasileiro mais completo.

### 1.3 Quanto os streamers gastam com ferramentas

**Não existe pesquisa independente** sobre o gasto mensal de streamers com ferramentas **[VERIFICADO: a busca não
encontrou fonte primária]**. O que há são estimativas de blogs e fornecedores, que vão de **US$ 0** (hobby, só
ferramentas grátis) a **~US$ 60/mês** (configuração típica, com alguns pagos) e **US$ 250 a 300/mês** (estimativa de
fornecedor, provavelmente inflada).

Segmentação proposta **[HIPÓTESE, precisa de validação com entrevistas]**:

| Segmento | Perfil | Gasto provável com clipping |
|---|---|---|
| Pequeno (afiliado, < 50 espectadores médios) | lives 3 a 4 vezes por semana, sem editor | US$ 0 a 15 / R$ 0 a 40 |
| Intermediário (50 a 500 espectadores) | lives quase diárias, já posta Shorts | US$ 15 a 40 / R$ 40 a 120 |
| Profissional / agência (500+ espectadores ou vários canais) | tem editor ou agência, multiplataforma | US$ 40 a 150+ / R$ 150 a 500+ |

Tamanho do mercado **[VERIFICADO, fontes secundárias]**: de 6,2 a 7,1 milhões de streamers ativos por mês na Twitch
em 2026; cerca de 62 mil a 80 mil parceiros; e cerca de 1,85 milhão de afiliados (número de blog, não confirmado).
O Brasil seria ~13,5% da audiência da Kick no início de 2026 (fonte de blog, não confirmada).

### 1.4 GTA VI e GTA RP **[VERIFICADO]**

- **Lançamento:** GTA VI saiu em **19/11/2026**, para PS5 e Xbox Series. **Ainda não há versão para PC anunciada.**
- **Audiência no lançamento:** ~**7,91 milhões** de espectadores simultâneos em todas as plataformas, recorde
  histórico para um jogo (3,31 milhões só na Twitch), e ~125,5 milhões de horas assistidas nas primeiras 24 h
  (Streams Charts).
- **Vendas estimadas:** ~19 a 24,6 milhões de cópias nos primeiros dias (Sensor Tower, citado pela CNBC e pela
  Reuters).
- **GTA V:** o tempo assistido de GTA V na Twitch caiu 49% depois do lançamento. Em julho de 2026, GTA V teve
  ~50 milhões de horas assistidas na Twitch e ~82,5 milhões na Kick, ou seja, **a Kick já passou a Twitch** em
  GTA V.
- **Brasileiros entre os maiores:** streamers brasileiros (Paulinho O Loko, LOUD Coringa) aparecem entre os maiores
  canais de GTA V (rankings sem data confirmada).

**Implicações [ESTIMATIVA]:**

1. **Agora:** o pico de interesse é imediato, com muita gente criando conteúdo de GTA VI no console. Os cortes de
   reação, descoberta e caos de mundo aberto valem para esse público.
2. **RP de GTA VI:** depende de uma versão para PC e de ferramentas de servidor modificado. Até lá, o
   **GTA V RP continua** sendo o nicho de RP, em queda mas grande, e **cada vez mais na Kick**.
3. **Kick:** sem suporte à Kick, o produto deixa de fora a maior plataforma de GTA RP hoje.

### 1.5 Diferenciais do AI Stream Clipper

| Diferencial | Concorrentes | Situação no AI Stream Clipper |
|---|---|---|
| Monitora a live sozinho e corta durante a transmissão, sem upload | StreamLadder e Eklipse por link ou VOD; OpusClip por upload | **existe** [MEDIDO no código] |
| Memória de história ("perseguição começou… terminou em prisão"), feita para RP | não encontrado nas páginas lidas | **existe** (Diretor IA, situações) |
| Critérios editoriais medidos (AUC 0,877 nas decisões humanas) | não publicado | **existe** (Fase 7) |
| Português nativo + inglês | maioria só inglês | **existe** (v0.7.5) |
| Legenda por quem fala (streamer × outras vozes) | parcial em alguns | **existe** |
| Kick | StreamLadder aceita Kick por link | **não existe** |
| Postagem automática | todos oferecem | existe só para a HTS; **não será liberada a clientes** (decisão do dono) |

**Ponto de atenção:** sem postagem automática, o produto compete em **qualidade do corte e economia de tempo de
edição**, não em distribuição. Isso precisa ficar claro na comunicação, porque os concorrentes vendem "corta e
posta sozinho".

### 1.6 Estratégia regional

- **Cada mercado tem a própria tabela** (nada de converter pelo câmbio). O Brasil compete com ferramentas locais de
  R$ 40 a 60 e com as internacionais pagas em dólar; os EUA competem com a faixa de US$ 15 a 40.
- **Brasil:** Pix (menor taxa) e cartão. Desconto no anual parecido com o dos concorrentes (anual ≈ 10 meses).
- **EUA:** cartão via Stripe. Anual com 2 meses grátis, padrão de mercado (StreamLadder).
- **Disposição a pagar: hipótese a validar** com lista de espera e duas versões de página de preço, e com
  entrevistas com 10 a 15 streamers de GTA RP de cada país. Não há dado primário de disposição a pagar nesta
  pesquisa.

---

## 2. Infraestrutura confirmada **[MEDIDO]**

Consultas somente leitura em 20/11/2026. Nada foi alterado.

| Máquina | Função implantada | CPU | RAM | Disco | GPU (VRAM utilizável) |
|---|---|---|---|---|---|
| 201 (1htsserver) | IA visual | i9-10980XE, 18 núcleos / 36 threads | 247 GB | NVMe 1 TB (756 GB livres) + **NVMe 4 TB sem uso (NTFS, não montado)** | Arc A750 (**7,54 GB**) |
| 206 (website-applications, ThinkStation P620) | captura, áudio, render | Threadripper PRO 3955WX, 16 núcleos / 32 threads | 200 GB | NVMe 1 TB (641 GB livres) | **nenhuma** |
| 210 (2htswebserver) | API, painel, diretor, IA visual | Threadripper PRO 3955WX, 16 núcleos / 32 threads | 247 GB | NVMe 1 TB (642 GB livres) | Arc A750 (**7,54 GB**) |
| 212 (hts-sql) | **só banco** (PostgreSQL 16, porta 55432) | 2× Xeon E5-2637 v2, 8 núcleos / 16 threads | 751 GB | RAID de HD, 7,3 TB | nenhuma |

O banco já está na 212 desde 20/11/2026, como planejado. A 212 não processa nada e não entra no cálculo de
capacidade.

**As duas GPUs não somam 16 GB.** Cada Arc tem 7,54 GB próprios, e um modelo precisa caber inteiro numa placa.

---

## 3. Resultados medidos (benchmarks de produção) **[MEDIDO]**

Janela das últimas 24 h, com 63,4 horas de live capturadas e 6 lives ao vivo no momento da medição.

| Métrica | Valor |
|---|---|
| Bitrate médio das lives capturadas | **6,63 Mbps** (189 GB de fonte em 24 h) |
| Transcrição (Whisper small, 2 threads por job) | 22.834 jobs; **2.456 s de job por hora de live**; p50 de 5 s por trecho de 10 s; espera p50 1 s / p95 60 s |
| CPU do worker de áudio com 6 lives | ~1.055% (~10,5 núcleos) → **~1,75 núcleo por job em paralelo** |
| Pontuação | 133 s por hora de live |
| IA visual (Qwen3-VL 2B INT4 nas Arc) | 294 análises; **205 s de GPU por hora de live**; p50 42 s por análise |
| Render 1080p (ffmpeg veryfast, CRF 21) | 265 renders; **121 s por hora de live**; ~34 s por Short; p50 22 s, p95 80 s |
| Shorts gerados | **2,37 por hora de live**; média de **16,2 MB** por Short de 36 s |
| Total armazenado hoje | 368 Shorts, **6,3 GB** |
| Temporário de captura | ~39 MB no momento (buffer de 30 min por live, teto configurado de 80 GB) |
| Sessões | média de **4,8 h** por live (34 sessões em 7 dias) |
| Modelos | VLM 2B: ~3,5 GB de VRAM; VLM 4B: ~5,5 GB; LLM 1,7B: ~2 GB (carregado nas duas GPUs) |

**Não medido (precisa de autorização para teste):** **render 4K**. Pedido explícito do estudo: nada de testes
pesados em produção sem autorização.

---

## 4. Estimativas de capacidade **[ESTIMATIVA]**

### 4.1 Custo por hora de live

| Recurso | Por hora de live | Como foi calculado |
|---|---|---|
| CPU | **~1,42 núcleo-hora** | transcrição 2.456 s × 1,75 núcleo + pontuação + render (121 s × 4 núcleos) + captura |
| GPU (IA padrão) | **205 s** | medido |
| GPU (mistura de planos) | **~302 s** | Basic ×1, Creator ×1,5 (mais quadros), Pro ×3 (modelo 4B + segunda análise) **[HIPÓTESE nos multiplicadores]** |
| Banda | 6,63 Mbps contínuos | medido |
| Disco | ~38 MB de Shorts por hora de live (2,37 × 16,2 MB) | medido |

### 4.2 Lives simultâneas

| Limite | Lives | Premissas |
|---|---|---|
| CPU (3 máquinas, 100 threads, 20 reservadas) | **~56** | 1,42 núcleo por live |
| GPU, 2 Arc a 80% de ocupação, só IA padrão | **~28** | 205 s/h |
| GPU, 2 Arc, mistura de planos | **~19** | 302 s/h |
| **Capacidade segura hoje** | **~19 a 28** | a GPU é o gargalo |

### 4.3 Lives no pico por cliente e clientes suportados

As lives se concentram das 18 h à meia-noite. O modelo usa uma janela de 6 h com fator de concentração de 1,6 e o
limite de lives simultâneas de cada plano **[HIPÓTESE]**.

| GPUs | Lives seguras (mistura) | Uso da franquia 40% | 60% | 100% |
|---|---|---|---|---|
| **2 (hoje)** | 19 | 63 | 42 | **25** |
| 3 | 29 | 95 | 63 | 38 |
| 4 | 38 | 126 | 84 | 50 |
| 6 | 57 (aqui a CPU também chega no limite) | 187 | 125 | 75 |

**Na prática, para planejar com segurança: ~40 clientes com o hardware atual.** Daí para cima é preciso comprar GPU.

### 4.4 4K **[ESTIMATIVA, não medido]**

- **Pixels:** 4K vertical (2160×3840) tem 4 vezes os pixels do 1080p. Na CPU (x264 veryfast), o render deve levar
  **3 a 5 vezes mais**, cerca de 2 a 3 minutos por Short de 36 s.
- **Na GPU:** com o encoder de hardware das Arc (QSV, HEVC ou AV1), o 4K deve ficar perto do custo do 1080p na CPU.
  **[HIPÓTESE, precisa de benchmark]**
- **Arquivo:** ~**60 a 80 MB** por Short em 4K, contra 16 MB em 1080p.
- **Qualidade:** a maioria das lives é 1080p (bitrate médio medido de 6,6 Mbps). Um "4K" feito a partir delas é
  **ampliação**: não traz mais detalhe. Vender como "exportação em 4K (upscale)" ou "na melhor qualidade da
  fonte".

### 4.5 Armazenamento

| Cenário | Pior caso (todos com a cota cheia) |
|---|---|
| 50 clientes | 522 GB |
| 100 clientes | 1,0 TB |
| 250 clientes | 2,6 TB |

Hoje há ~640 GB livres em cada NVMe de 1 TB, mais o **NVMe de 4 TB parado na 201** (formatar só com autorização,
porque ele está em NTFS e pode ter dados). Com Shorts de 16 MB, a cota de 5 GB do Basic comporta ~300 Shorts e a de
25 GB do Pro, ~1.500. A cota é mais folgada do que parecia, e dá para reduzir.

### 4.6 Banda

O pico com 19 a 28 lives fica entre **125 e 190 Mbps** contínuos de download. Com 50 lives, ~330 Mbps. **Confirmar o
link contratado [HIPÓTESE].**

---

## 5. Simulação financeira

### 5.1 Premissas **[HIPÓTESE, salvo indicação]**

| Item | Valor | Etiqueta |
|---|---|---|
| Mistura de planos | 50% Basic / 35% Creator / 15% Pro | HIPÓTESE |
| Clientes por mercado | 70% Brasil / 30% EUA no início | HIPÓTESE |
| Câmbio | US$ 1 = R$ 5,86 (fechamento de 18/11/2026) | VERIFICADO |
| Stripe EUA | 2,9% + US$ 0,30 + Billing 0,7% | VERIFICADO (o 0,7% do Billing ainda precisa de confirmação) |
| Cartão Brasil (Stripe) | 3,99% + R$ 0,39 (+0,7% de Billing) | VERIFICADO em fontes secundárias; Pix a 1,19% só por convite |
| Remessa/câmbio do USD | 1,5% | HIPÓTESE |
| Simples Nacional | Anexo V 15,5% (base) / Anexo III 6% (se o Fator R ≥ 28%) | VERIFICADO em fontes secundárias; **validar com contador** |
| Exportação de serviço (clientes dos EUA) | 4% efetivo | HIPÓTESE; validar com contador |
| Energia | ~1,0 kW médio nas 4 máquinas a R$ 1,00/kWh → R$ 730/mês | HIPÓTESE (bandeira verde em nov/2026 VERIFICADA) |
| Internet empresarial | R$ 700/mês | HIPÓTESE |
| Depreciação do hardware | R$ 90 mil em 36 meses → R$ 2.500/mês | HIPÓTESE |
| Manutenção e ferramentas | R$ 700/mês | HIPÓTESE |
| Suporte | R$ 6 por cliente/mês | HIPÓTESE |
| Salário do dono | **não incluído** | — |

### 5.2 Contribuição por cliente/mês (uso de 60%, Anexo V)

| Plano | Mercado | Receita | Taxas | Impostos | Variável | Contribuição | Margem |
|---|---|---|---|---|---|---|---|
| Basic | BR | R$ 60 | R$ 3 | R$ 9 | R$ 7 | R$ 40 | 67% |
| Basic | EUA | R$ 117 | R$ 8 | R$ 5 | R$ 7 | R$ 98 | 83% |
| Creator | BR | R$ 150 | R$ 7 | R$ 23 | R$ 9 | R$ 110 | 73% |
| Creator | EUA | R$ 293 | R$ 17 | R$ 12 | R$ 9 | R$ 255 | 87% |
| Pro | BR | R$ 300 | R$ 14 | R$ 46 | R$ 14 | R$ 225 | 75% |
| Pro | EUA | R$ 586 | R$ 32 | R$ 23 | R$ 14 | R$ 517 | 88% |

### 5.3 Cenários com os preços propostos (uso de 60%, Anexo V, câmbio 5,86)

| Clientes | MRR | Taxas + impostos | Contribuição | Fixos | Resultado | Lives no pico | Ocupação da GPU (2 GPUs) |
|---|---|---|---|---|---|---|---|
| 25 | R$ 4.097 | R$ 640 | R$ 3.231 | R$ 4.630 | **−R$ 1.399** | 11 | 59% |
| 50 | R$ 8.194 | R$ 1.281 | R$ 6.462 | R$ 4.630 | R$ 1.832 | 23 | **119% — precisa de +1 GPU** |
| 100 | R$ 16.388 | R$ 2.560 | R$ 12.924 | R$ 4.630 | R$ 8.294 | 45 | **238% — precisa de 5 GPUs** |
| 200 | R$ 32.775 | R$ 5.121 | R$ 25.847 | R$ 4.630 | R$ 21.217 | 91 | **476% — precisa de nova máquina** |
| 250 | R$ 40.969 | R$ 6.401 | R$ 32.309 | R$ 4.630 | R$ 27.679 | 113 | **595%** |

**Com uso de 100% da franquia:** o resultado quase não muda (o custo variável é baixo), mas a ocupação dobra. Com
25 clientes já se chega a ~99% da GPU atual. **O risco do uso de 100% não é financeiro, é de capacidade.**

A partir de ~40 clientes, o resultado acima **não inclui** a compra de GPUs nem de máquinas. Isso está na seção 6.

### 5.4 Sensibilidade (100 clientes, uso de 60%)

| Variação | Resultado mensal |
|---|---|
| Base (câmbio 5,86, Anexo V) | R$ 8.294 |
| Anexo III (Fator R ≥ 28%) | R$ 9.141 |
| Câmbio a 5,00 | R$ 7.305 |
| Câmbio a 6,50 | R$ 9.030 |
| Preços a 85% dos propostos | R$ 6.220 |
| Preços a 70% dos propostos | R$ 4.145 |

**Ponto de equilíbrio sobre os custos fixos:** ~**35 clientes** na mistura proposta, sem salário. É praticamente o
mesmo número que esgota as 2 GPUs atuais. **Crescer exige comprar GPU logo no início.**

---

## 6. Quando investir **[ESTIMATIVA / HIPÓTESE de preços]**

| Gatilho | Investimento | Ganho |
|---|---|---|
| ~30 a 35 clientes, ou GPU acima de 70% no pico | **+1 GPU na 206** (ThinkStation P620 com slots PCIe x16; conferir fonte e espaço) | +50% de lives (19 → 29) |
| ~55 a 60 clientes | **+1 GPU** (4 no total; uma 2ª placa na 201 ou na 210, se couber) | 38 lives |
| 800 GB+ em Shorts | **formatar e montar o NVMe de 4 TB da 201** (precisa de autorização e de confirmar que não há dados) | +3,6 TB |
| ~90 a 120 clientes | nova máquina de GPU, ou trocar por GPUs com mais VRAM (12 a 16 GB: modelo 4B e de texto juntos) | 57+ lives; a CPU passa a pesar |
| ~150+ clientes | 4ª máquina de produção (CPU) + link de 1 Gbps | dobra a capacidade |

**Otimizações que valem mais do que hardware [HIPÓTESE, precisam de teste]:**

1. Analisar **menos quadros** nos candidatos do Basic, ou em resolução menor: corta o tempo de GPU por hora de live.
2. **Agrupar análises** na GPU (várias por chamada).
3. Rodar a IA avançada **só nos cortes duvidosos** (segunda análise), não em todos os candidatos.
4. Transcrição com **faster-whisper INT8**, ou na GPU: libera CPU.

---

## 7. Recomendações comerciais

### 7.1 Limites dos planos: sustentáveis?

| Item proposto | Avaliação | Recomendação |
|---|---|---|
| Basic 40 h / 1 canal / 1 live / 5 GB / IA econômica | sustentável | manter; 5 GB ≈ 300 Shorts |
| Creator 100 h / 2 canais / 2 lives / 12 GB / IA intermediária | sustentável | manter; 12 GB ≈ 740 Shorts, dá para baixar para **10 GB** |
| Pro 200 h / 4 canais / **3 lives** / 25 GB / **IA avançada em tudo** | **pesado**: um Pro cheio ocupa ~1,8 live no pico × 3 de GPU ≈ 5 lives-padrão | (a) IA avançada **só na segunda análise** ou limitada a **80 h/mês**; (b) **2 lives** simultâneas, com a 3ª como adicional pago; (c) manter 200 h |
| "Shorts sem limite fixo" | ok, porque a geração é ~2,4 por hora de live e o custo está nas horas | manter, com **uso justo** escrito (ex.: até 40 Shorts por live) |
| 4K sob demanda | sem benchmark ainda; arquivo 4 a 5 vezes maior | **limite próprio**: Pro com **30 exportações 4K por mês**; pacote extra pago |

### 7.2 O que justifica o Pro

**Funcionalidades:**
- IA editorial profunda (modelo 4B + segunda análise dos cortes duvidosos);
- variantes de estrutura (gancho, suspense, história completa) para comparar;
- legenda por quem fala;
- logo e selo personalizados, sem marca HTS;
- 4 canais;
- fila prioritária;
- exportação 4K (com limite).

**Público:** **pequenas agências e streamers com vários canais**. Para um streamer solo, o mercado paga no máximo
~US$ 40.

### 7.3 Preços recomendados **[HIPÓTESE fundamentada; validar antes de fixar]**

**EUA (USD):**

| Plano | Proposto | Recomendado (regular) | Lançamento (3 primeiros meses ou fundadores) | Anual |
|---|---|---|---|---|
| Basic | 19,99 | **19,99** | 14,99 | 199 (2 meses grátis) |
| Creator | 49,99 | **39,99** | 29,99 | 399 |
| Pro | 99,99 | **79,99** | 59,99 | 799 |

**Por quê:**
- **Basic:** US$ 19,99 fica entre o Starter (US$ 15) e o Creator (US$ 27) da StreamLadder, e ainda vem com IA, o
  que o Starter deles não tem.
- **Creator:** US$ 49,99 fica acima de qualquer plano publicado (o teto é ~US$ 40). A US$ 39,99 ele empata com
  StreamLadder Pro e Eklipse Premium Pro, com o diferencial de monitorar a live sozinho e de 2 canais.
- **Pro:** com 4 canais, sai por US$ 20 por canal, competitivo. Acima de US$ 80, só com validação.

**Brasil (BRL), tabela própria (não é conversão):**

| Plano | Proposto | Recomendado (regular) | Lançamento | Anual |
|---|---|---|---|---|
| Basic | 59,90 | **49,90** | 39,90 | 499 |
| Creator | 149,90 | **119,90** | 89,90 | 1.199 |
| Pro | 299,90 | **249,90** | 199,90 | 2.499 |

**Por quê:**
- **Basic:** com R$ 49,90 regular e R$ 39,90 no lançamento, ele empata com Curupira e Real Oficial, com mais
  recursos.
- **Creator:** R$ 119,90 fica abaixo do OpusClip Pro convertido (~R$ 165 + IOF) e ainda tem margem.
- **Pro:** voltado a agências brasileiras de cortes, que já pagam editores humanos.

**Impacto:** os preços recomendados reduzem o MRR em ~15 a 20% em relação aos propostos (veja a sensibilidade, com
preços a 85%). Em troca, eles ficam competitivos. Como a capacidade, e não o preço, limita o crescimento, **a decisão
de preço deve se apoiar na conversão medida na lista de espera.**

### 7.4 Adicionais vendidos à parte **[HIPÓTESE]**

| Adicional | Sugestão de preço (EUA / BR) | Custo para nós |
|---|---|---|
| +20 h de live/mês | US$ 7 / R$ 19,90 | GPU |
| +1 canal monitorado | US$ 5 / R$ 14,90 | quase nenhum |
| +1 live simultânea | US$ 10 / R$ 29,90 | **GPU no pico** (o mais caro) |
| +5 GB de armazenamento | US$ 3 / R$ 9,90 | disco |
| Pacote de 20 exportações 4K | US$ 5 / R$ 14,90 | render |
| IA avançada para o Creator (+40 h) | US$ 10 / R$ 29,90 | GPU |
| Edição feita por editor humano da HTS | por Short (ex.: US$ 15 / R$ 39,90) | mão de obra |
| Marca branca para agências | sob consulta | — |

### 7.5 Quantos clientes com segurança

| Hardware | Clientes com segurança (uso de 60%, margem de 20%) |
|---|---|
| Atual (2 GPUs) | **~40** |
| +1 GPU na 206 | ~60 |
| +2 GPUs | ~80 |

O primeiro investimento (+1 GPU) deve acontecer **antes** de chegar a 30 clientes pagantes.

### 7.6 Ordem sugerida

1. **Validar a demanda:** lista de espera + página de preços com duas versões (proposta × recomendada) + 10 a 15
   entrevistas por país. Duas a três semanas.
2. **Benchmark 4K** num corte real (fora do horário de pico, com autorização): CPU × Arc QSV, tempo e tamanho.
3. **Suporte à Kick** (prioridade de produto para GTA RP).
4. **Comprar +1 GPU** para a 206 quando houver ~20 assinantes confirmados.
5. **Implementar planos, cotas e créditos** só depois da validação.
6. **Expansão para outros jogos:** a transcrição, o áudio e o render servem para qualquer jogo. A memória de
   histórias e os critérios editoriais foram feitos para GTA. Para Valorant, Minecraft, Just Chatting etc., é
   preciso uma taxonomia de situações por jogo e uma recalibração com decisões humanas. Estimativa: 1 a 2 semanas
   por jogo novo. **[ESTIMATIVA]**

---

## 8. Separação final

**1. Dados verificados:** preços oficiais de OpusClip, StreamLadder e Eklipse; preços das ferramentas brasileiras
nas páginas dos fornecedores; lançamento e audiência de GTA VI; ausência de versão para PC; GTA V na Kick e na
Twitch; câmbio de 18/11/2026; taxas da Stripe (Billing a confirmar); faixas do Simples (fontes secundárias, validar
com contador); bandeira tarifária de novembro.

**2. Benchmarks (medidos em produção, somente leitura):** hardware das 4 máquinas; VRAM utilizável (7,54 GB por
GPU); consumo por hora de live (transcrição, IA visual, render, pontuação); tamanho médio do Short (16,2 MB);
bitrate (6,63 Mbps); duração média das lives (4,8 h); armazenamento atual (6,3 GB); NVMe de 4 TB sem uso.

**3. Estimativas fundamentadas:** núcleos e GPU por hora de live; lives simultâneas por GPU; clientes suportados;
custo e tamanho do 4K; armazenamento e banda por cenário; contribuição por plano; ponto de equilíbrio.

**4. Hipóteses não validadas:** mistura de planos; divisão Brasil/EUA; concentração no pico; multiplicadores de
GPU por nível de IA; energia, internet, depreciação e suporte; imposto sobre exportação; disposição a pagar e
segmentação de streamers; preços recomendados; preços dos adicionais; tempo para suportar outros jogos.

---

## 9. Fontes (consultadas em 20/11/2026)

**Concorrentes:**
- OpusClip, preços: https://www.opus.pro/pricing
- OpusClip, ajuda sobre planos e créditos: https://help.opus.pro/docs/article/plans-and-credits.md
- StreamLadder, preços ("Last verified November 16, 2026"): https://streamladder.com/pricing
- Eklipse, preços e FAQ: https://eklipse.gg/pricing
- Eklipse, é grátis? (atualizado em 30/08/2026): https://eklipse.gg/help/is-eklipse-free/
- Eklipse, recursos de IA: https://eklipse.gg/features/ai-highlights/
- Klap × Vizard (terceiros, 2026): https://passiveshorts.com/blog/klap-vs-vizard-ai/
- Vizard (terceiros): https://makerstack.co/reviews/vizard-review/

**Ferramentas brasileiras e preço do OpusClip em reais:**
- Curupira: https://curupira.app/
- CortaIA: https://cortaia.com.br/
- SmartClips: https://smartclips.com.br/
- Real Oficial (blog da própria empresa): https://realoficial.com.br/pt/blog/9-ferramentas-de-ia-que-todo-criador-de-conteudo-solo-precisa-em-2026
- OpusClip em reais (texto de concorrente): https://cut.pro/pt-BR/blog/opus-clip-preco-em-reais-2026

**Streamers e audiência:**
- Gasto com ferramentas (blog): https://myfinancialfreedomtracker.com/en/content/expense-tracker-for-twitch-streamers
- Gasto com ferramentas (fornecedor): https://bizzby.ai/start-a/best-twitch-gaming-streamer-software
- Mudança de monetização da Twitch (maio/2026): https://blog.twitch.tv/en/2026/05/13/monetization-for-all/
- Streamers ativos na Twitch: https://xtendedview.com/twitch-statistics.md
- Afiliados da Twitch: https://voxbooster.com/blog/twitch-affiliate-statistics-2026
- Brasil na Kick (blog): https://stream-rise.com/blog/top-kick-streamers-brazil-2026

**GTA VI e GTA V:**
- Recorde de audiência no lançamento: https://streamscharts.com/news/gta-vi-breaks-streaming-launch-records-within-minutes-its-release
- Impacto do lançamento nas plataformas: https://streamscharts.com/news/gta-6-launch-lifts-livestreaming
- Economia de conteúdo no lançamento (20/11/2026): https://www.cnbc.com/2026/11/20/gta-vi-launch-streaming-content-economy.html
- Kick passa a Twitch em GTA V: https://streamscharts.com/news/kick-overtakes-twitch-gta-v-viewership
- GTA V em horas assistidas na Twitch: https://www.statista.com/statistics/1109392/gta-5-hours-watched-twitch/
- Data de lançamento de GTA VI: https://www.latestly.com/technology/rockstar-games-gta-6-release-date-update-november-19-2026-launch-confirmed-amid-2027-delay-rumours-heres-what-we-know-so-far-7259826.html

**Câmbio, taxas, impostos e energia:**
- Câmbio de 18/11/2026: https://brasil61.com/n/dolar-fecha-em-alta-de-1-45-e-chega-a-r-5-87-bras2617492
- Stripe EUA: https://stripe.com/pricing
- Stripe Billing 0,7% (terceiro): https://flexprice.io/blog/stripe-pricing-breakdown-2026
- Stripe Brasil: https://www.abacatepay.com/blog/stripe-ou-mercado-pago
- Simples Nacional para software: https://agilize.com.br/artigos/simples-nacional-para-software
- Bandeira tarifária de novembro/2026: https://movimentoeconomico.com.br/economia/energia/2026/10/30/bandeira-verde-continua-em-novembro-e-conta-de-luz-nao-tera-cobranca-extra/

**Medições internas:** consultas somente leitura ao PostgreSQL de produção (jobs, segmentos, cortes, workers) e às
4 máquinas via `asc-ctl status`, em 20/11/2026.
