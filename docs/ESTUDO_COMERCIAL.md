# Estudo de viabilidade comercial e precificação — AI Stream Clipper by HTS

**Versão 2 — 20/11/2026** · Escopo: Brasil e EUA · Planos Basic, Creator e Pro · **Foco comercial inicial: GTA VI**

> Este é um estudo para decisão. Nada foi implementado: sem billing, nenhum plano existente foi alterado, nenhum
> benchmark pesado foi executado e não houve merge nem deploy. Na produção foram feitas apenas consultas de
> leitura.

**O que mudou da versão 1 (revisão do PR #17):**
- **Preços:** o modelo agora calcula três tabelas: os preços propostos, os recomendados da v1 e uma tabela revisada.
- **Custos:** entram a expansão de GPU e de máquinas, a remuneração operacional e o uso de 100% da franquia.
- **Capacidade:** três cenários (conservador, provável e otimista), contando **núcleos físicos** em vez de threads
  lógicas, com métricas de fila, espera e crescimento de fila no pico.
- **Medições novas:** mídia original guardada por corte (~42 MB) e uso real da segunda análise (0 em 7 dias).
- **Posicionamento:** o foco passa a ser **GTA VI**, e não GTA RP. A Kick vira prioridade condicionada à demanda dos
  criadores de GTA VI.
- **Conclusão:** a recomendação de preço da v1 **não se sustenta** com os custos completos. Veja a seção 6.

**Etiquetas usadas no texto:**

| Etiqueta | Significado |
|---|---|
| **[VERIFICADO]** | fonte com URL (seção 10) |
| **[MEDIDO]** | produção, 20/11/2026, somente leitura |
| **[ESTIMATIVA]** | cálculo sobre dados medidos ou verificados |
| **[HIPÓTESE]** | precisa de validação |

Todas as tabelas de capacidade e finanças saem de [`docs/estudo_comercial_modelo.py`](estudo_comercial_modelo.py),
que não tem dependências. Para refazer as contas com outras premissas, altere o topo do arquivo e rode de novo.

---

## 0. Conclusões corrigidas

1. **Com o hardware atual, o limite seguro é de ~40 a 45 clientes no cenário provável.** No cenário conservador,
   em que todos usam a franquia inteira no pico, são só ~14. No otimista, ~117. O gargalo imediato é a GPU
   **[ESTIMATIVA sobre dados MEDIDOS]**.
2. **Depois da 3ª GPU, a CPU vira o gargalo**, contando núcleos físicos: 50 núcleos, ~50 núcleos-equivalentes úteis
   no cenário provável. **Comprar só GPU não basta.** Daí em diante, é preciso otimizar a transcrição e/ou comprar
   máquinas.
3. **Com os custos completos** (fixos, remuneração operacional de R$ 3.000 a R$ 10.000 por faixa, GPUs, máquinas e
   disco), o equilíbrio fica em:

   | Preços | Cenário provável | Provável + otimizações | Otimista |
   |---|---|---|---|
   | Propostos (R$ 59,90 / 149,90 / 299,90; US$ 19,99 / 49,99 / 99,99) | **~165** | **~96** | ~95 |
   | Recomendados na v1 (R$ 49,90 / 119,90 / 249,90; US$ 19,99 / 39,99 / 79,99) | **~238** | **~181** | ~171 |
   | Revisados v2 (R$ 59,90 / 139,90 / 279,90; US$ 19,99 / 44,99 / 89,99) | ~188 | **~103** | ~101 |

   Sem pró-labore do dono. Com pró-labore de R$ 5.000, os pontos sobem para 190 a 250 clientes ou mais.
4. **Os preços recomendados na v1 não se pagam** antes de ~180 a 240 clientes, um volume que exige de 3 a 8
   máquinas novas. **Retiro aquela recomendação.**
5. **O que mais pesa não é preço, é o quanto cada cliente consome de capacidade** (horas de live no pico). Por
   isso, as otimizações técnicas valem mais que R$ 10 a 20 de diferença de preço: elas levam o equilíbrio de ~165
   para ~96 clientes com os preços propostos.
6. **O cenário conservador não fecha em nenhuma tabela.** As franquias do Pro (200 h, 3 lives) e do Creator (2 lives)
   precisam de **proteção de uso justo**, para que um cliente que usa tudo, todo dia, não derrube a capacidade dos
   outros. Isso está na seção 7.
7. **Recomendação nova (seção 8):**
   - lançar com os **preços revisados v2**, fazer as **otimizações técnicas antes da venda** e colocar o **Pro com 2
     lives simultâneas** (a 3ª vira adicional);
   - limitar a **segunda análise premium**;
   - abrir com **turma de fundadores limitada** (ex.: 30 vagas), desconto só para ela e capacidade garantida;
   - não abrir vendas acima do que o hardware aguenta.
8. **GTA VI** saiu em 19/11/2026, com recorde histórico de audiência **[VERIFICADO]**. É o foco certo para o
   lançamento: há muito volume de criadores novos e muitos momentos de reação, descoberta e caos de mundo aberto.

---

## 1. Mercado

### 1.1 Concorrentes internacionais (USD) **[VERIFICADO, páginas oficiais em 20/11/2026]**

| Ferramenta | Plano | Mensal | Anual | Limite | Destaques |
|---|---|---|---|---|---|
| OpusClip | Free / Starter / Pro / Business | US$ 0 / 15 / 29 / sob consulta | Pro anual não exibido | 60 / 150 / 300 min de vídeo por mês | Pro: agendamento, 2 a 4 usuários, API. Business: 4K |
| StreamLadder ("verificada em 16/11/2026") | Free / Starter / Creator / Pro | US$ 0 / 15 / 27 / 39 | US$ 150 / 270 / 390 | IA a partir do Creator, "uso justo", até 30 clipes por live | Pro: **4K60**, 2 editores |
| Eklipse (só pago desde jun/2026) | Premium / Premium Pro | US$ 24,99 / 39,99 | US$ 179,99 (Premium) | não publicado | clipes guardados 14 dias (mensal) ou 90 dias (anual); 1080p60 |
| Klap / Vizard (terceiros) | — | ~US$ 29 a 39 | ~US$ 14,50 a 19,50 por mês | clipes ou minutos | ferramentas genéricas |

**Leitura [ESTIMATIVA]:** o teto público do mercado fica em **~US$ 40 por mês**. O 4K aparece só no topo de linha.
Ninguém publica cota em GB; quem limita armazenamento faz isso por **prazo** (Eklipse).

### 1.2 Brasil (BRL) **[VERIFICADO, páginas dos próprios fornecedores]**

| Ferramenta | Preço |
|---|---|
| Curupira | R$ 39,90/mês (300 créditos) |
| Real Oficial | a partir de R$ 59,90/mês |
| CortaIA | por minuto; tem plano grátis |
| SmartClips | R$ 47,90 por 6 meses (programa local) |
| OpusClip Pro | cobrado em USD: ≈ R$ 165 + IOF (estimativa de um concorrente) |

### 1.3 Disposição a pagar

**Não há pesquisa primária** sobre quanto streamers gastam com ferramentas **[VERIFICADO: não encontrada]**. As
estimativas de blogs vão de US$ 0 a ~US$ 60 por mês. A segmentação abaixo é **[HIPÓTESE]**:

| Segmento | Gasto provável com clipping |
|---|---|
| Pequeno | US$ 0 a 15 / R$ 0 a 40 |
| Intermediário | US$ 15 a 40 / R$ 40 a 120 |
| Profissional, agência ou multicanal | US$ 40 a 150+ / R$ 150 a 500+ |

### 1.4 Foco: GTA VI **[VERIFICADO]**

- **Lançamento:** **19/11/2026**, para PS5 e Xbox Series. **Ainda não há versão para PC anunciada.**
- **Audiência ao vivo:** recorde histórico para um jogo, com ~7,91 milhões de espectadores simultâneos em todas as
  plataformas (3,31 milhões na Twitch) e ~125,5 milhões de horas assistidas nas primeiras 24 h.
- **Vendas estimadas:** ~19 a 24,6 milhões de cópias nos primeiros dias.

**Implicações para o produto [ESTIMATIVA]:**

1. **O público é de streamers de console** (capturam pelo PS5 ou Xbox e transmitem na Twitch, no YouTube ou na
   Kick). O sistema já captura qualquer live pública de Twitch e YouTube.
2. **O conteúdo é de mundo aberto:** perseguições, fugas, caos, descobertas e reações. Tipos de situação como
   "perseguição policial" e "susto" já existem. Faltam **assaltos e missões**, **descobertas e easter eggs** e
   **momentos de história**. É preciso recalibrar os critérios com cortes reais de GTA VI.
3. **O pico de interesse começa agora.** A janela comercial é de meses, não de anos, e a vantagem é estar pronto
   enquanto os canais de GTA VI estão nascendo.
4. **O RP de GTA VI não entra no plano inicial**, porque depende de PC e de servidores modificados. Ele fica para
   depois, se e quando acontecer.

### 1.5 Kick: prioridade condicionada à demanda

**Fatos [VERIFICADO]:**
- Em GTA V, a Kick já passou a Twitch em horas assistidas (82,5 mi contra ~50 mi em julho de 2026).
- No lançamento de GTA VI, ~42% do pico ficou na Twitch; o restante se dividiu entre YouTube, Kick e outras
  plataformas.

**Situação do produto [MEDIDO no código]:** o sistema monitora só Twitch e YouTube.

**Recomendação:**
1. Na lista de espera, perguntar **em que plataforma o criador transmite GTA VI**.
2. Se **≥ 20% dos interessados** usarem a Kick como plataforma principal, o suporte à Kick entra antes do
   lançamento. Abaixo disso, fica para depois do lançamento.

### 1.6 Diferenciais

| Diferencial | Situação |
|---|---|
| Monitora a live e corta durante a transmissão, sem upload | existe |
| Memória de "histórias" da live (o momento com começo, meio e fim) | existe; precisa de tipos de situação de GTA VI |
| Critérios editoriais medidos contra decisões humanas (AUC 0,877) | existe |
| Português e inglês | existe |
| Legenda por quem fala | existe |
| Postagem automática | **não será oferecida a clientes** (decisão do dono) |

A comunicação deve vender "**o melhor corte da sua live de GTA VI, pronto para postar**", não distribuição
automática.

---

## 2. Infraestrutura confirmada **[MEDIDO]**

| Máquina | Função | CPU (núcleos físicos / threads) | RAM | Disco | GPU (VRAM utilizável) |
|---|---|---|---|---|---|
| 201 | IA visual | i9-10980XE: **18 / 36** | 247 GB | NVMe 1 TB (756 GB livres) + **NVMe 4 TB sem uso (NTFS)** | Arc A750 (**7,54 GB**) |
| 206 (ThinkStation P620) | captura, áudio, render | TR PRO 3955WX: **16 / 32** | 200 GB | NVMe 1 TB (641 GB livres) | nenhuma |
| 210 | API, painel, diretor, IA visual | TR PRO 3955WX: **16 / 32** | 247 GB | NVMe 1 TB (642 GB livres; **é onde ficam os vídeos**) | Arc A750 (**7,54 GB**) |
| 212 | **só banco** (PostgreSQL 16) | 2× E5-2637 v2: 8 / 16 | 751 GB | RAID HD 7,3 TB | — |

**Total de produção:** **50 núcleos físicos / 100 threads**. As duas GPUs são **independentes**: cada modelo precisa
caber em 7,54 GB, e elas não somam 16 GB.

---

## 3. Medições de produção **[MEDIDO]**

| Métrica | Valor |
|---|---|
| Horas de live nas últimas 24 h | 63,4 h (6 lives simultâneas na hora da medição) |
| Bitrate médio | 6,63 Mbps |
| Transcrição | faster-whisper "small" INT8, beam 3, com VAD; 2 threads por job; **6,8 s por trecho de 10 s**; ~1,75 de CPU por job em paralelo |
| CPU de análise e render por hora de live | **~1,42 núcleo-equivalente** (transcrição ~1,2 + pontuação + render + captura) |
| IA visual | Qwen3-VL 2B INT4; **4,64 análises por hora de live**; **média de 41,7 s** (p50 38 s, 581 análises em 7 dias) |
| Segunda análise ("reassistir") | **0 de 581 em 7 dias**: o código a pula quando a fila de IA está cheia, e a faixa de dúvida é estreita |
| Render 1080p | ~34 s por Short; 121 s de render por hora de live |
| Short médio | **16,2 MB** (36 s, 1080p) |
| Mídia original guardada por corte | **~42 MB** (16,0 GB para 382 cortes) |
| Vídeos guardados hoje | 7,5 GB de Shorts (com variantes) + 16,0 GB de mídia original + 4,8 GB de dataset |
| Duração média das lives | 4,8 h |
| VRAM dos modelos | VLM 2B ≈ 3,5 GB; VLM 4B ≈ 5,5 GB; LLM 1,7B ≈ 2 GB (carregado nas duas GPUs) |

**Não medido:** 4K (exige benchmark autorizado) e o custo real da segunda análise (nunca disparou).

---

## 4. Capacidade, concorrência e filas **[ESTIMATIVA]**

### 4.1 Cenários

| Parâmetro | Conservador | Provável | Otimista |
|---|---|---|---|
| Uso da franquia de horas | 100% | 60% | 40% |
| Concentração no pico (18h a 24h) | 2,0× | 1,6× | 1,3× |
| Mistura Basic / Creator / Pro | 40 / 40 / 20 | 50 / 35 / 15 | 60 / 30 / 10 |
| Ganho de SMT sobre núcleos físicos | 1,00 (nenhum) | 1,20 | 1,35 |
| Núcleos físicos reservados (SO, API, captura, picos de render) | 10 | 8 | 6 |
| Ocupação máxima planejada | 75% | 80% | 85% |
| Multiplicador de GPU: Creator / Pro (base) | 1,6 / 1,4 | 1,4 / 1,3 | 1,25 / 1,2 |
| Parcela de candidatos do Pro com 2ª análise (×2,5 de custo) | 35% | 25% | 15% |

**Por que núcleos físicos:** a CPU medida (~1,75 por job) é tempo de CPU lógico com a máquina folgada (carga ~11 em
16 núcleos físicos). Em saturação, duas threads no mesmo núcleo não rendem o dobro. Por isso a capacidade é
calculada como **núcleos físicos × ganho de SMT**, de 1,0 a 1,35, e não como 100 threads.

### 4.2 Capacidade com o hardware atual

| Cenário | Lives no pico por cliente | GPU por hora de live | CPU útil (núcleos-equiv.) | **Clientes máx.** | Gargalo |
|---|---|---|---|---|---|
| Conservador | 1,07 | 344 s | 40 | **14** | GPU |
| Provável | 0,45 | 288 s | 50 | **44** | GPU |
| Otimista | 0,21 | 243 s | 59 | **117** | GPU |
| Provável + otimizações* | 0,45 | 275 s | 50 | **46** | GPU |

\* **Otimizações** [HIPÓTESE, a medir]:
- transcrição com beam 1 e modelo "base" na IA econômica (~40% menos CPU);
- menos quadros no Basic (~30% menos GPU no Basic);
- Pro com 2 lives simultâneas.

### 4.3 Expansão

| Cenário | 2 GPUs | 3 GPUs | 4 GPUs | 6 GPUs | 6 GPUs + 1 máquina nova |
|---|---|---|---|---|---|
| Conservador | 14 (GPU) | 19 (CPU) | 19 (CPU) | 19 (CPU) | 27 (CPU) |
| Provável | 44 (GPU) | **62 (CPU)** | 62 (CPU) | 62 (CPU) | 86 (CPU) |
| Otimista | 117 (GPU) | 166 (CPU) | 166 (CPU) | 166 (CPU) | 227 (CPU) |
| Provável + otimizações | 46 (GPU) | 69 (GPU) | **92 (GPU)** | 94 (CPU) | 130 (CPU) |

**Leitura:** sem otimizar a transcrição, a **3ª GPU já leva ao limite de CPU**. Com a otimização, GPUs adicionais
rendem até a 4ª. **Otimizar a transcrição é pré-requisito para escalar.**

### 4.4 Filas e latência no pico (hardware atual; modelo M/M/c sobre os tempos medidos)

| Cenário | Clientes | Lives no pico | Ocupação GPU | Ocupação CPU | Espera na fila de IA | Corte pronto após o momento | Fila de IA cresce | Atraso acumulado em 3 h |
|---|---|---|---|---|---|---|---|---|
| Provável | 25 | 11 | 45% | 32% | 16 s | **~1,7 min** | não | 0 |
| Provável | 40 | 18 | 73% | 51% | 69 s | **~2,6 min** | não | 0 |
| Provável | 60 | 27 | **109%** | 76% | fila cresce | — | +10 análises/h | **11 min** |
| Provável | 100 | 45 | 182% | 127% | fila cresce | — | +94/h | **98 min** |
| Conservador | 25 | 27 | **128%** | 94% | fila cresce | — | +27/h | 28 min |
| Conservador | 40 | 43 | 204% | 151% | fila cresce | — | +101/h | 105 min |
| Otimista | 100 | 21 | 72% | 51% | 57 s | ~2,4 min | não | 0 |
| Provável + otimizações | 40 | 18 | 69% | 34% | 54 s | ~2,3 min | não | 0 |
| Provável + otimizações | 60 | 27 | 104% | 51% | fila cresce | — | +5/h | 5 min |

**Como ler:** abaixo de ~75% de ocupação, o corte fica pronto em 2 a 3 minutos depois do momento. Acima de 100%, a
fila cresce durante todo o pico, e o atraso só zera de madrugada. O que protege a experiência dos clientes é
**não vender acima da capacidade** e ter **prioridade por plano**.

**Proteção que já existe [MEDIDO no código]:** quando a fila de IA passa do limite configurado, a segunda análise é
pulada, para que a análise ao vivo tenha prioridade.

### 4.5 Disco e banda

- **Vídeos:** o volume dos vídeos tem ~688 GB livres na 210. A cota de clientes no pior caso mais 7 dias de mídia
  original **[ESTIMATIVA]**: ~0,5 TB com 50 clientes e ~1,1 TB com 100. Acima de ~60 a 70 clientes é preciso de
  disco: um NVMe de 4 TB na 210, **ou** o NVMe parado da 201 servindo a 210 pela rede (isso exige trabalho, e
  formatar exige autorização).
- **Banda de download no pico:** 6,63 Mbps por live → ~120 Mbps com 18 lives e ~300 Mbps com 45. Confirmar o link
  **[HIPÓTESE]**.

---

## 5. Custos

| Item | Valor | Etiqueta |
|---|---|---|
| Taxas de pagamento | Stripe EUA 2,9% + US$ 0,30 + 0,7% Billing; cartão Brasil 3,99% + R$ 0,39 + 0,7%; câmbio e remessa 1,5% | VERIFICADO (Billing a confirmar) / HIPÓTESE |
| Impostos | Simples Anexo V 15,5% (base) ou Anexo III 6%; exportação 4% | VERIFICADO em fontes secundárias / HIPÓTESE; **contador** |
| Fixos | R$ 4.630/mês: energia R$ 730, internet R$ 700, depreciação do hardware atual R$ 2.500, manutenção e ferramentas R$ 700 | HIPÓTESE |
| Remuneração operacional (suporte, monitoramento, operação) | R$ 0 até 24 clientes · R$ 3.000 de 25 a 74 · R$ 6.000 de 75 a 149 · R$ 10.000 a partir de 150 | HIPÓTESE |
| Pró-labore do dono | R$ 0 no cenário base; R$ 5.000 na sensibilidade | HIPÓTESE |
| GPU adicional | R$ 2.500 (classe Arc A750/B580), amortizada em 24 meses + energia ≈ **R$ 214/mês** | HIPÓTESE |
| Máquina nova (CPU + slots de GPU) | R$ 30.000, amortizada em 36 meses + energia ≈ **R$ 1.083/mês** | HIPÓTESE |
| NVMe 4 TB | R$ 1.800 / 36 meses | HIPÓTESE |
| Variável por cliente | suporte R$ 6 + energia ~R$ 0,05 por hora de live | HIPÓTESE / ESTIMATIVA |
| Divisão por mercado | 70% Brasil / 30% EUA | HIPÓTESE |

A depreciação do hardware que já existe (R$ 2.500/mês) é custo econômico, **não saída de caixa**. No caixa, o
resultado de cada cenário melhora em R$ 2.500.

---

## 6. Simulação financeira (Anexo V, câmbio 5,86, com expansão e remuneração operacional)

### 6.1 Cenário provável: comparação entre as tabelas de preço

| Clientes | Propostos: MRR | Propostos: resultado | Recomendados v1: MRR | Recomendados v1: resultado | Revisados v2: MRR | Revisados v2: resultado | GPUs / máquinas novas |
|---|---|---|---|---|---|---|---|
| 25 | R$ 4.097 | −R$ 4.387 | R$ 3.409 | −R$ 4.968 | R$ 3.840 | −R$ 4.608 | 2 / 0 |
| 50 | R$ 8.194 | −R$ 1.358 | R$ 6.818 | −R$ 2.520 | R$ 7.681 | −R$ 1.799 | 3 / 0 |
| 100 | R$ 16.388 | −R$ 517 | R$ 13.635 | −R$ 2.840 | R$ 15.361 | −R$ 1.399 | 5 / 2 |
| 200 | R$ 32.775 | R$ 3.051 | R$ 27.270 | −R$ 1.596 | R$ 30.723 | R$ 1.287 | 10 / 6 |
| 250 | R$ 40.969 | R$ 6.943 | R$ 34.088 | R$ 1.134 | R$ 38.403 | R$ 4.736 | 12 / 8 |

### 6.2 Cenário provável + otimizações

| Clientes | Propostos: resultado | Recomendados v1: resultado | Revisados v2: resultado | GPUs / máquinas novas |
|---|---|---|---|---|
| 25 | −R$ 4.387 | −R$ 4.968 | −R$ 4.608 | 2 / 0 |
| 50 | −R$ 1.358 | −R$ 2.520 | −R$ 1.799 | 3 / 0 |
| 100 | **R$ 567** | −R$ 1.757 | −R$ 316 | 5 / 1 |
| 200 | R$ 6.516 | R$ 1.868 | R$ 4.751 | 9 / 3 |
| 250 | R$ 10.407 | R$ 4.598 | R$ 8.201 | 11 / 5 |

### 6.3 Uso máximo (cenário conservador)

| Clientes | Propostos | Recomendados v1 | Revisados v2 | Infra necessária |
|---|---|---|---|---|
| 25 | −R$ 5.505 | −R$ 6.187 | −R$ 5.776 | 4 GPUs + 1 máquina |
| 50 | −R$ 5.810 | −R$ 7.176 | −R$ 6.353 | 7 GPUs + 4 máquinas |
| 100 | −R$ 10.619 | −R$ 13.349 | −R$ 11.705 | 14 GPUs + 11 máquinas |
| 200+ | inviável no modelo | inviável | inviável | — |

**Conclusão:** se todos usarem 100% no pico, **nenhum preço fecha**. As franquias precisam de proteção de uso
(seção 7.4), e a venda tem de ser controlada pela capacidade.

### 6.4 Ponto de equilíbrio (clientes; resultado ≥ 0 de forma estável até 250)

| Preços | Conservador | Provável | Otimista | Provável + otimizações |
|---|---|---|---|---|
| Propostos | não atinge | 165 | 95 | **96** |
| Recomendados v1 | não atinge | 238 | 171 | 181 |
| Revisados v2 | não atinge | 188 | 101 | **103** |
| *com pró-labore de R$ 5.000:* propostos | não atinge | 234 | 188 | 189 |
| *com pró-labore de R$ 5.000:* revisados v2 | não atinge | não atinge | 200 | 213 |

### 6.5 Sensibilidade (100 clientes, cenário provável, preços recomendados v1)

| Variação | Resultado mensal |
|---|---|
| Base | −R$ 2.840 |
| Anexo III (Fator R ≥ 28%) | −R$ 2.146 |
| Câmbio a 5,00 / 6,50 | −R$ 3.677 / −R$ 2.218 |
| 50% dos clientes no Brasil | −R$ 700 |
| 90% dos clientes no Brasil | −R$ 4.981 |
| Uso de 100% da franquia | −R$ 6.903 |
| Otimizações | −R$ 1.757 |
| Otimizações + preços propostos | **+R$ 567** |

**Leitura:**
- **Clientes dos EUA** pagam mais por hora de live. A proporção EUA/Brasil muda o resultado mais do que câmbio ou
  regime tributário.
- **Uso de 100%** é o maior risco.
- **Otimização técnica e preço** juntos são o que vira o resultado para positivo.

---

## 7. Refinamento dos planos

### 7.1 Estrutura mantida

| | Basic | Creator | Pro |
|---|---|---|---|
| IA | **econômica**: VLM 2B com menos quadros; transcrição "base" com beam 1 | **aprimorada**: VLM 2B com mais quadros; transcrição "small" | **premium seletiva**: base aprimorada + 2ª análise com modelo maior só nos candidatos duvidosos |
| Candidatos a Shorts | sem limite diário fixo, dentro das horas | idem | idem |
| Exportação | 1080p | 1080p | 1080p + **4K sob demanda** |

### 7.2 Limite de 30 exportações 4K por mês no Pro

- **Custo [ESTIMATIVA, sem benchmark]:** na CPU, ~2 min por Short 4K, ou ~1 h de CPU por mês por cliente Pro no
  limite. Na GPU (encoder QSV da Arc), bem menos. Arquivo de ~60 a 80 MB.
- **Avaliação:** **30 por mês é sustentável**. Com 15% de clientes Pro, 100 clientes geram até 450 exportações por
  mês, ~15 h de CPU no mês, o que é desprezível. O disco também não pesa se o 4K for **gerado, baixado e apagado
  em 48 h**.
- **Recomendação:** manter 30, com pacote adicional de 20 exportações. Fazer o benchmark (autorizado, fora do pico)
  antes de publicar.
- **Comunicação:** "4K a partir da melhor qualidade da sua live". A maioria das lives é 1080p, então o 4K é
  ampliação.

### 7.3 Retenção da mídia original (necessária para re-render e 4K)

- **Medido:** ~**42 MB** de fonte por corte, 2,6 vezes o tamanho do Short. Sem a fonte, não há re-render, edição
  nem 4K.
- **Política recomendada [HIPÓTESE]:**

| | Basic | Creator | Pro |
|---|---|---|---|
| Fonte guardada | 3 dias | 7 dias | 14 dias |
| Conta na cota do cliente? | não (custo nosso) | não | não |
| Depois do prazo | o Short continua, mas não dá mais para editar nem exportar 4K | idem | idem |

- **Avisar no painel:** "editável até dd/mm".
- **Fonte de cortes favoritos/protegidos:** fica até o Short ser apagado, com limite de N cortes protegidos por
  plano.
- **Custo estimado de fonte guardada por cliente (uso de 60%):** Basic ~0,25 GB (3 dias), Creator ~1,4 GB
  (7 dias), Pro ~5,6 GB (14 dias). Conta: horas × 60% × 2,37 cortes por hora × 42 MB × dias / 30. O modelo usa 7 dias
  para todos, uma hipótese mais conservadora.

### 7.4 Segunda análise premium

- **Medido:** 0 disparos em 7 dias; o código a pula quando a fila de IA está cheia.
- **Custo [ESTIMATIVA]:** ~2,5 vezes uma análise normal (situação inteira, mais quadros, modelo maior). Com 25% dos
  candidatos, o Pro custa ~1,9 vez a GPU do Basic por hora de live.
- **Limites recomendados:**
  - **até 25% dos candidatos** de cada live, priorizando os de nota perto do corte;
  - **teto mensal**, por exemplo 150 segundas análises por mês;
  - **sempre abaixo da análise ao vivo** na fila: se a GPU estiver acima de 80%, adia para a madrugada e não
    descarta.
- **Modelo maior (4B, ~5,5 GB) + modelo de texto (~2 GB):** os dois juntos ocupam quase uma Arc inteira. Para
  rodar o 4B sem trocar modelos a cada chamada, o ideal é **uma GPU dedicada à análise premium**. Esse é o primeiro
  uso da 3ª GPU.

### 7.5 Proteção contra abuso sem punir cliente legítimo

| Risco | Proteção | Por que não atrapalha o cliente legítimo |
|---|---|---|
| Live 24/7 ou reprise em loop para gastar horas | teto de **12 h por dia por canal**; detectar áudio sem fala e imagem estática por mais de 30 min (pausa a análise, sem descontar horas) | criadores reais raramente passam de 12 h |
| Uma assinatura para vários streamers | trocar canal monitorado **no máximo 2 vezes por mês**; canal vinculado por login ou verificação na bio | troca eventual continua liberada |
| Todos os clientes no pico ao mesmo tempo | **prioridade justa na fila** (por cliente, com peso por plano) | ninguém trava a fila dos outros |
| Uso de 100% da franquia todo dia | aviso aos 80% e **redução de qualidade da IA** ao passar do uso justo (em vez de cortar o serviço) | quem usa normalmente nunca vê isso |
| Muitas exportações 4K | cota mensal + pacote adicional | o limite é visível e previsível |
| Compartilhar a conta | sessões simultâneas limitadas; alerta de login em vários países | uso normal não aciona nada |
| Pagamento com cartão roubado | 3-D Secure; Pix no Brasil | pagamento normal segue igual |
| Vender acima da capacidade | **vendas abertas só até ~75% da capacidade de pico medida**; acima disso, lista de espera | protege a latência de quem já é cliente |

---

## 8. Recomendações finais

### 8.1 Preços: **revisados v2** (substituem a recomendação da v1)

| Plano | EUA | Brasil | Anual |
|---|---|---|---|
| Basic | **US$ 19,99** | **R$ 59,90** | 10× o mensal |
| Creator | **US$ 44,99** | **R$ 139,90** | 10× |
| Pro | **US$ 89,99** | **R$ 279,90** | 10× |

**Por que esses valores:**
- Ficam perto dos concorrentes. O Creator (US$ 44,99) fica pouco acima dos US$ 39 a 40 do mercado, com dois
  diferenciais: monitoramento automático da live e 2 canais.
- Com as otimizações, chegam ao equilíbrio em ~100 clientes.
- **Os preços propostos originalmente** (US$ 49,99 e 99,99; R$ 149,90 e 299,90) dão um equilíbrio melhor (~96
  clientes com as otimizações), mas o **Creator e o Pro dos EUA ficam bem acima de qualquer concorrente
  publicado**. Só use esses preços se a lista de espera mostrar disposição a pagar por eles.

**Lançamento:**
- **Sem desconto amplo.** No lugar dele, uma **turma de fundadores com 30 vagas**: 20% de desconto vitalício,
  capacidade garantida e canal direto de feedback.
- **Por quê:** os números mostram que o problema é capacidade por cliente, não falta de clientes. Desconto amplo
  piora o equilíbrio.

### 8.2 Ajustes de plano

| Item | Recomendação |
|---|---|
| Pro | **2 lives simultâneas** (a 3ª é adicional, ex.: US$ 15 / R$ 39,90) |
| Segunda análise premium | até 25% dos candidatos e no máximo 150 por mês; nunca à frente da análise ao vivo |
| 4K | 30 por mês no Pro + pacote extra |
| Mídia original | 3, 7 e 14 dias por plano |
| Armazenamento | manter 5, 12 e 25 GB (Shorts de 16 MB: ~300, ~740 e ~1.500 Shorts) |
| Uso justo | 12 h por dia por canal; trocas de canal limitadas; redução gradual de qualidade em vez de corte |

### 8.3 Capacidade e investimento

| Etapa | Clientes com segurança (provável) | Ação |
|---|---|---|
| Hoje | **~40** | — |
| Antes de vender | ~46 | **otimizar a transcrição** (beam 1, modelo "base" no Basic) e medir |
| ~30 clientes pagos | ~69 | **+1 GPU na 206** (dedicada à IA premium) |
| ~55 clientes | ~92 | +1 GPU (4 no total) + NVMe 4 TB para vídeos |
| ~85 clientes | ~130 | primeira máquina nova (CPU + GPUs) |

### 8.4 Ordem de execução

1. **Lista de espera de GTA VI**, com página de preços em duas versões (revisados × propostos) e a pergunta "onde
   você transmite?". Entrevistas com 10 a 15 criadores de GTA VI por país.
2. **Otimizações técnicas medidas** (transcrição, quadros do Basic), sem mexer em produção até haver autorização.
3. **Benchmark 4K** autorizado, fora do pico (CPU × Arc QSV).
4. **Tipos de situação de GTA VI** e recalibração com os primeiros cortes reais.
5. **Kick**, se ≥ 20% da lista de espera transmitir lá.
6. **Billing, cotas e uso justo** só depois da validação.
7. **Expansão para outros jogos de mundo aberto e ação** (mesmo pipeline, nova taxonomia de situações): 1 a 2
   semanas por jogo **[ESTIMATIVA]**.

---

## 9. Separação final

**1. Dados verificados:**
- preços oficiais de OpusClip, StreamLadder e Eklipse;
- preços das ferramentas brasileiras;
- lançamento e audiência de GTA VI; ausência de versão para PC;
- GTA V na Kick e na Twitch;
- câmbio de 18/11/2026;
- taxas da Stripe (Billing a confirmar);
- faixas do Simples (fontes secundárias);
- bandeira tarifária de novembro.

**2. Medições de produção:**
- hardware (50 núcleos físicos / 100 threads; 2 × 7,54 GB de VRAM);
- tempos de transcrição, IA visual e render;
- análises por hora de live;
- tamanho do Short (16,2 MB) e da fonte (42 MB);
- uso da segunda análise (0 em 7 dias);
- bitrate (6,63 Mbps);
- duração das lives (4,8 h);
- disco livre;
- NVMe de 4 TB sem uso.

**3. Estimativas:**
- capacidade por cenário;
- filas e latência (M/M/c);
- infraestrutura necessária por número de clientes;
- custo do 4K e da segunda análise;
- disco e banda;
- resultados financeiros e pontos de equilíbrio.

**4. Hipóteses não validadas:**
- mistura de planos; divisão Brasil/EUA;
- concentração no pico; ganho de SMT;
- multiplicadores de GPU por plano;
- efeito das otimizações;
- custos fixos, remuneração operacional, preços de GPU e de máquina;
- imposto sobre exportação;
- disposição a pagar;
- preços recomendados; política de retenção; limites de uso justo.

---

## 10. Fontes (consultadas em 20/11/2026)

**Concorrentes:**
- OpusClip, preços: https://www.opus.pro/pricing
- OpusClip, ajuda sobre planos e créditos: https://help.opus.pro/docs/article/plans-and-credits.md
- StreamLadder, preços: https://streamladder.com/pricing
- Eklipse, preços: https://eklipse.gg/pricing
- Eklipse, é grátis?: https://eklipse.gg/help/is-eklipse-free/
- Eklipse, recursos de IA: https://eklipse.gg/features/ai-highlights/
- Klap × Vizard (terceiros): https://passiveshorts.com/blog/klap-vs-vizard-ai/
- Vizard (terceiros): https://makerstack.co/reviews/vizard-review/

**Ferramentas brasileiras e preço do OpusClip em reais:**
- Curupira: https://curupira.app/
- CortaIA: https://cortaia.com.br/
- SmartClips: https://smartclips.com.br/
- Real Oficial: https://realoficial.com.br/pt/blog/9-ferramentas-de-ia-que-todo-criador-de-conteudo-solo-precisa-em-2026
- OpusClip em reais (texto de concorrente): https://cut.pro/pt-BR/blog/opus-clip-preco-em-reais-2026

**Streamers e audiência:**
- Gasto com ferramentas (blog): https://myfinancialfreedomtracker.com/en/content/expense-tracker-for-twitch-streamers
- Gasto com ferramentas (fornecedor): https://bizzby.ai/start-a/best-twitch-gaming-streamer-software
- Streamers ativos na Twitch: https://xtendedview.com/twitch-statistics.md
- Afiliados da Twitch: https://voxbooster.com/blog/twitch-affiliate-statistics-2026

**GTA VI e GTA V:**
- Recorde de audiência no lançamento: https://streamscharts.com/news/gta-vi-breaks-streaming-launch-records-within-minutes-its-release
- Impacto do lançamento nas plataformas: https://streamscharts.com/news/gta-6-launch-lifts-livestreaming
- Economia de conteúdo no lançamento (CNBC, 20/11/2026): https://www.cnbc.com/2026/11/20/gta-vi-launch-streaming-content-economy.html
- Kick passa a Twitch em GTA V: https://streamscharts.com/news/kick-overtakes-twitch-gta-v-viewership
- Data de lançamento de GTA VI: https://www.latestly.com/technology/rockstar-games-gta-6-release-date-update-november-19-2026-launch-confirmed-amid-2027-delay-rumours-heres-what-we-know-so-far-7259826.html

**Câmbio, taxas, impostos e energia:**
- Câmbio de 18/11/2026: https://brasil61.com/n/dolar-fecha-em-alta-de-1-45-e-chega-a-r-5-87-bras2617492
- Stripe EUA: https://stripe.com/pricing
- Stripe Billing 0,7% (terceiro): https://flexprice.io/blog/stripe-pricing-breakdown-2026
- Stripe Brasil: https://www.abacatepay.com/blog/stripe-ou-mercado-pago
- Simples Nacional para software: https://agilize.com.br/artigos/simples-nacional-para-software
- Bandeira tarifária de novembro/2026: https://movimentoeconomico.com.br/economia/energia/2026/10/30/bandeira-verde-continua-em-novembro-e-conta-de-luz-nao-tera-cobranca-extra/

**Medições internas:** consultas somente leitura ao PostgreSQL de produção (jobs, segmentos, cortes, workers,
armazenamento) e às máquinas via `asc-ctl`, em 20/11/2026.
