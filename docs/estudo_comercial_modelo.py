"""Modelo de capacidade, filas e finanças do AI Stream Clipper (anexo de docs/ESTUDO_COMERCIAL.md), v2.

Rodar:  python docs/estudo_comercial_modelo.py   (sem dependências; saída em Markdown)
Etiquetas usadas no relatório: MEDIDO (produção, 20/11/2026, somente leitura), VERIFICADO (fonte com URL),
ESTIMATIVA (cálculo sobre medidos/verificados) e HIPÓTESE (premissa a validar). Altere as premissas aqui.
"""

from __future__ import annotations

import math

# ============================================================================ MEDIDO (produção)
LIVE_H_24H = 63.4                     # horas de live capturadas nas últimas 24 h
AUDIO_JOBS_PER_LIVE_H = 360           # trechos de 10 s
AUDIO_JOB_MEAN_S = 2456 / 360         # 6,8 s por trecho (Whisper small, 2 threads)
AUDIO_CPU_PER_JOB = 1.75              # 6 jobs paralelos = ~1055% de CPU (tempo de CPU lógico)
SCORE_S_PER_LIVE_H = 133
RENDER_S_PER_LIVE_H = 121             # 1080p
RENDER_MEAN_S = 34
VISION_JOBS_PER_LIVE_H = 294 / 63.4   # 4,64 análises por hora de live
VISION_MEAN_S = 41.7                  # média em 7 dias (581 análises); p50 38 s
SHORTS_PER_LIVE_H = 2.37
SHORT_MB = 16.2                       # 1080p, 36 s
SOURCE_MB_PER_CLIP = 16027 / 382      # mídia original guardada por corte: ~42 MB
SOURCE_MBPS = 6.63
PHYS_CORES = 18 + 16 + 16             # 201 + 206 + 210 (núcleos FÍSICOS; 100 threads lógicas)
GPUS_NOW = 2                          # Arc A750 separadas, 7,54 GB utilizáveis cada
CLIP_DISK_FREE_GB = 688               # volume dos vídeos na 210

# ============================================================================ PLANOS e PREÇOS
PLANS = {
    "basic":   {"hours": 40,  "simul": 1, "gb": 5},
    "creator": {"hours": 100, "simul": 2, "gb": 12},
    "pro":     {"hours": 200, "simul": 3, "gb": 25},
}
PRICES = {  # (BRL, USD)
    "propostos":    {"basic": (59.90, 19.99), "creator": (149.90, 49.99), "pro": (299.90, 99.99)},
    "recomendados": {"basic": (49.90, 19.99), "creator": (119.90, 39.99), "pro": (249.90, 79.99)},
    "revisados":    {"basic": (59.90, 19.99), "creator": (139.90, 44.99), "pro": (279.90, 89.99)},
}

# ============================================================================ CENÁRIOS (HIPÓTESES)
SCENARIOS = {
    #                uso franquia, concentração no pico, mistura (B/C/P), fator SMT, núcleos reservados,
    #                teto de ocupação, multiplicador de GPU (Creator, Pro), fração de lives Pro c/ 2ª análise
    "conservador": {"util": 1.00, "peak": 2.0, "mix": (0.40, 0.40, 0.20), "smt": 1.00, "reserve": 10,
                    "cap": 0.75, "gpu_mult": (1.6, 1.40), "second_share": 0.35},
    "provável":    {"util": 0.60, "peak": 1.6, "mix": (0.50, 0.35, 0.15), "smt": 1.20, "reserve": 8,
                    "cap": 0.80, "gpu_mult": (1.4, 1.30), "second_share": 0.25},
    "otimista":    {"util": 0.40, "peak": 1.3, "mix": (0.60, 0.30, 0.10), "smt": 1.35, "reserve": 6,
                    "cap": 0.85, "gpu_mult": (1.25, 1.20), "second_share": 0.15},
}
# provável + otimizações (HIPÓTESE, a medir): transcrição com beam 1 e modelo "base" na IA econômica
# (-40% de CPU), menos quadros no Basic (-30% de GPU no Basic), Pro com 2 lives simultâneas (3ª como adicional)
SCENARIOS["provável + otimizações"] = {**SCENARIOS["provável"], "audio_factor": 0.6, "basic_gpu": 0.7, "pro_simul": 2}
SECOND_ANALYSIS_COST = 2.5            # 2ª análise = situação inteira, mais quadros: ~2,5x uma análise (ESTIMATIVA)
PEAK_WINDOW_H = 6                     # 18h-24h
PEAK_DURATION_H = 3                   # duração do pico para o crescimento de fila

# ============================================================================ CUSTOS
FX = 5.86                             # VERIFICADO (18/11/2026)
MARKET_BR = 0.70                      # HIPÓTESE
STRIPE_US = (0.029 + 0.007, 0.30)     # VERIFICADO (Billing 0,7% a confirmar)
CARD_BR = (0.0399 + 0.007, 0.39)      # VERIFICADO (fontes secundárias)
FX_SPREAD = 0.015                     # HIPÓTESE
TAX_BR = {"anexo_v": 0.155, "anexo_iii": 0.06}
TAX_EXPORT = 0.04                     # HIPÓTESE (contador)
FIXED = 4630                          # energia, internet, depreciação, manutenção, ferramentas (HIPÓTESE)
SUPPORT_PER_CLIENT = 6.0
ENERGY_PER_LIVE_H = 0.05
# remuneração operacional (suporte, monitoramento, operação) por faixa de clientes - HIPÓTESE
OPS_PAY = [(0, 0), (25, 3000), (75, 6000), (150, 10000)]
PRO_LABORE = 0                        # pró-labore do dono (cenário à parte)
GPU_PRICE = 2500                      # placa classe Arc A750/B580 (HIPÓTESE)
GPU_MONTHLY = GPU_PRICE / 24 + 110    # amortização 24 meses + energia (~150 W)
GPU_SLOTS_EXISTING = 6                # até 2 por máquina de produção (HIPÓTESE: conferir fonte/slots)
NODE_PRICE = 30000                    # nova máquina (CPU + 4 slots de GPU) (HIPÓTESE)
NODE_MONTHLY = NODE_PRICE / 36 + 250
NODE_CORES = 16
NVME_4TB_MONTHLY = 1800 / 36
SOURCE_RETENTION_DAYS = 7
K4_EXPORTS_PRO = 30                   # limite proposto
K4_MB = 70
K4_RENDER_S = 120                     # CPU (estimativa, não medido); na GPU QSV, ~30-40 s


# ============================================================================ CAPACIDADE
def mix(sc):
    return dict(zip(("basic", "creator", "pro"), sc["mix"]))


def gpu_mult(sc):
    c, p = sc["gpu_mult"]
    return {"basic": sc.get("basic_gpu", 1.0), "creator": c, "pro": p + sc["second_share"] * SECOND_ANALYSIS_COST}


def simul(sc, p):
    return sc.get("pro_simul", 3) if p == "pro" else PLANS[p]["simul"]


def peak_lives_per_client(sc):
    m = mix(sc)
    return sum(m[p] * min(simul(sc, p), PLANS[p]["hours"] * sc["util"] / 30 / PEAK_WINDOW_H * sc["peak"]) for p in m)


def gpu_s_per_live_h(sc):
    m, g = mix(sc), gpu_mult(sc)
    lives = {p: min(simul(sc, p), PLANS[p]["hours"] * sc["util"] / 30 / PEAK_WINDOW_H * sc["peak"]) for p in m}
    tot = sum(m[p] * lives[p] for p in m)
    return VISION_JOBS_PER_LIVE_H * VISION_MEAN_S * sum(m[p] * lives[p] * g[p] for p in m) / tot


def cpu_core_per_live(sc):
    """Núcleos-equivalentes (tempo de CPU) por live: transcrição + pontuação + render + captura."""
    return (AUDIO_JOBS_PER_LIVE_H * AUDIO_JOB_MEAN_S * AUDIO_CPU_PER_JOB * sc.get("audio_factor", 1.0) + SCORE_S_PER_LIVE_H
            + RENDER_S_PER_LIVE_H * 4) / 3600 + 0.05


def cpu_capacity(sc, extra_nodes=0):
    return (PHYS_CORES + extra_nodes * NODE_CORES - sc["reserve"]) * sc["smt"]


def erlang_c_wait(lam, mu, c):
    """Espera média M/M/c (s). lam/mu em jobs por segundo. Retorna (rho, espera) ou (rho, inf)."""
    a = lam / mu
    rho = a / c
    if rho >= 1:
        return rho, math.inf
    s = sum(a ** k / math.factorial(k) for k in range(c))
    pc = (a ** c / math.factorial(c)) / (1 - rho)
    p_wait = pc / (s + pc)
    return rho, p_wait / (c * mu - lam)


def queues(sc, clients, gpus=GPUS_NOW, extra_nodes=0):
    lives = clients * peak_lives_per_client(sc)
    # visão
    lam_v = lives * VISION_JOBS_PER_LIVE_H / 3600
    mu_v = 1 / (VISION_MEAN_S * gpu_s_per_live_h(sc) / (VISION_JOBS_PER_LIVE_H * VISION_MEAN_S))
    rho_v, w_v = erlang_c_wait(lam_v, mu_v, gpus)
    grow_v = max(0.0, (lam_v - gpus * mu_v) * 3600)  # análises acumuladas por hora
    # CPU (transcrição como fila com c = núcleos / 1,75)
    cpu_need = lives * cpu_core_per_live(sc)
    cpu_cap = cpu_capacity(sc, extra_nodes)
    c_a = max(1, int(cpu_cap / AUDIO_CPU_PER_JOB * 0.8))
    lam_a = lives * AUDIO_JOBS_PER_LIVE_H / 3600
    svc_a = AUDIO_JOB_MEAN_S * sc.get("audio_factor", 1.0)
    rho_a, w_a = erlang_c_wait(lam_a, 1 / svc_a, c_a)
    grow_a = max(0.0, (lam_a - c_a / svc_a) * 3600)
    latency = (w_a + AUDIO_JOB_MEAN_S + 2 + w_v + VISION_MEAN_S + RENDER_MEAN_S) if math.isfinite(w_a + w_v) else math.inf
    backlog_min = (grow_v * PEAK_DURATION_H * VISION_MEAN_S / gpus) / 60 if grow_v else 0
    return {"lives": lives, "rho_gpu": rho_v, "rho_cpu": cpu_need / cpu_cap, "wait_v": w_v, "wait_a": w_a,
            "grow_v": grow_v, "grow_a": grow_a, "latency": latency, "backlog_min": backlog_min}


def max_clients(sc, gpus=GPUS_NOW, extra_nodes=0):
    n = 0
    while True:
        q = queues(sc, n + 1, gpus, extra_nodes)
        if q["rho_gpu"] > sc["cap"] or q["rho_cpu"] > sc["cap"]:
            return n, ("GPU" if q["rho_gpu"] > sc["cap"] else "CPU")
        n += 1
        if n > 5000:
            return n, "-"


def infra_for(sc, clients):
    """Menor expansão (GPUs e máquinas novas) que mantém GPU e CPU abaixo do teto."""
    for nodes in range(0, 13):
        for gpus in range(GPUS_NOW, GPU_SLOTS_EXISTING + 4 * nodes + 1):
            q = queues(sc, clients, gpus, nodes)
            if q["rho_gpu"] <= sc["cap"] and q["rho_cpu"] <= sc["cap"]:
                return gpus, nodes
    return None, None


# ============================================================================ FINANÇAS
def ops_pay(n):
    return max(v for k, v in OPS_PAY if n >= k)


def month(sc, n, prices, tax="anexo_v", pro_labore=PRO_LABORE):
    m = mix(sc)
    gross = fees = taxes = var = 0.0
    for p, share in m.items():
        brl, usd = PRICES[prices][p]
        k_br, k_us = n * share * MARKET_BR, n * share * (1 - MARKET_BR)
        gross += k_br * brl + k_us * usd * FX
        fees += k_br * (brl * CARD_BR[0] + CARD_BR[1]) + k_us * ((usd * STRIPE_US[0] + STRIPE_US[1]) * FX + usd * FX * FX_SPREAD)
        taxes += k_br * brl * TAX_BR[tax] + k_us * usd * FX * TAX_EXPORT
        var += n * share * (PLANS[p]["hours"] * sc["util"] * ENERGY_PER_LIVE_H + SUPPORT_PER_CLIENT)
    gpus, nodes = infra_for(sc, n)
    expansion = (max(0, (gpus or 99) - GPUS_NOW) * GPU_MONTHLY + (nodes or 0) * NODE_MONTHLY) if gpus else math.inf
    storage_gb = n * sum(m[p] * PLANS[p]["gb"] for p in m) + n * sum(
        m[p] * PLANS[p]["hours"] * sc["util"] / 30 * SOURCE_RETENTION_DAYS * SHORTS_PER_LIVE_H * SOURCE_MB_PER_CLIP / 1000 for p in m)
    disks = max(0, math.ceil((storage_gb - CLIP_DISK_FREE_GB * 0.85) / 3600))
    expansion += disks * NVME_4TB_MONTHLY
    ops = ops_pay(n)
    result = gross - fees - taxes - var - FIXED - ops - expansion - pro_labore
    return {"mrr": gross, "fees_tax": fees + taxes, "var": var, "ops": ops, "expansion": expansion, "gpus": gpus,
            "nodes": nodes, "storage_gb": storage_gb, "result": result}


def break_even(sc, prices, pro_labore=PRO_LABORE, limit=250):
    """Menor número de clientes a partir do qual o resultado fica >= 0 até ``limit`` (estável, não o 1º positivo)."""
    ok_from = None
    for n in range(1, limit + 1):
        r = month(sc, n, prices, pro_labore=pro_labore)["result"]
        if math.isfinite(r) and r >= 0:
            ok_from = ok_from or n
        else:
            ok_from = None
    return ok_from if ok_from else f"não atinge até {limit}"


def brl(x):
    return "—" if not math.isfinite(x) else f"R$ {x:,.0f}".replace(",", ".")


def fmt_s(x):
    return "∞ (fila cresce)" if not math.isfinite(x) else (f"{x:.0f} s" if x < 600 else f"{x / 60:.0f} min")


if __name__ == "__main__":
    print("## A. Capacidade por cenário (hardware atual: 2 GPUs, 50 núcleos físicos / 100 threads)\n")
    print("| Cenário | Lives no pico por cliente | GPU por hora de live | CPU por live | Capacidade de CPU (núcleos-equiv.) | Clientes máx. | Gargalo |")
    print("|---|---|---|---|---|---|---|")
    for name, sc in SCENARIOS.items():
        n, b = max_clients(sc)
        print(f"| {name} | {peak_lives_per_client(sc):.3f} | {gpu_s_per_live_h(sc):.0f} s | {cpu_core_per_live(sc):.2f} | "
              f"{cpu_capacity(sc):.0f} | **{n}** | {b} |")
    print("\n## B. Clientes máximos por expansão\n")
    print("| Cenário | 2 GPUs | 3 GPUs | 4 GPUs | 6 GPUs | 6 GPUs + 1 máquina (+4 GPUs, +16 núcleos) |")
    print("|---|---|---|---|---|---|")
    for name, sc in SCENARIOS.items():
        cells = [f"{max_clients(sc, g)[0]} ({max_clients(sc, g)[1]})" for g in (2, 3, 4, 6)]
        cells.append(f"{max_clients(sc, 10, 1)[0]} ({max_clients(sc, 10, 1)[1]})")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print("\n## C. Filas e latência no pico (hardware atual)\n")
    print("| Cenário | Clientes | Lives no pico | Ocupação GPU | Ocupação CPU | Espera IA visual | Espera transcrição | Corte pronto após o momento | Fila de IA cresce (análises/h) | Atraso acumulado em 3 h de pico |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, sc in SCENARIOS.items():
        for n in (25, 40, 60, 100):
            q = queues(sc, n)
            print(f"| {name} | {n} | {q['lives']:.1f} | {q['rho_gpu'] * 100:.0f}% | {q['rho_cpu'] * 100:.0f}% | {fmt_s(q['wait_v'])} | "
                  f"{fmt_s(q['wait_a'])} | {fmt_s(q['latency'])} | {q['grow_v']:.0f} | "
                  f"{(str(round(q['backlog_min'])) + ' min') if q['backlog_min'] else '0'} |")
    for prices in ("propostos", "recomendados", "revisados"):
        for name in ("provável", "provável + otimizações", "conservador"):
            sc = SCENARIOS[name]
            print(f"\n## D. Finanças — preços {prices}, cenário {name} (Anexo V, câmbio {FX}, com expansão e remuneração operacional)\n")
            print("| Clientes | MRR | Taxas + impostos | Variável | Fixos | Remuneração op. | Expansão (GPUs/máquinas/disco) | GPUs | Máquinas novas | Resultado |")
            print("|---|---|---|---|---|---|---|---|---|---|")
            for n in (25, 50, 100, 200, 250):
                r = month(sc, n, prices)
                print(f"| {n} | {brl(r['mrr'])} | {brl(r['fees_tax'])} | {brl(r['var'])} | {brl(FIXED)} | {brl(r['ops'])} | "
                      f"{brl(r['expansion'])} | {r['gpus']} | {r['nodes']} | **{brl(r['result'])}** |")
    print("\n## E. Ponto de equilíbrio (clientes)\n")
    print("| Preços | Cenário | Sem pró-labore | Com pró-labore de R$ 5.000 |")
    print("|---|---|---|---|")
    for prices in ("propostos", "recomendados", "revisados"):
        for name, sc in SCENARIOS.items():
            print(f"| {prices} | {name} | {break_even(sc, prices)} | {break_even(sc, prices, 5000)} |")
    print("\n## F. Sensibilidade (100 clientes, cenário provável, preços recomendados)\n")
    print("| Variação | Resultado |")
    print("|---|---|")
    sc = SCENARIOS["provável"]
    print(f"| base | {brl(month(sc, 100, 'recomendados')['result'])} |")
    print(f"| Anexo III | {brl(month(sc, 100, 'recomendados', tax='anexo_iii')['result'])} |")
    for fx in (5.0, 6.5):
        globals()["FX"] = fx
        print(f"| câmbio {fx} | {brl(month(sc, 100, 'recomendados')['result'])} |")
    globals()["FX"] = 5.86
    for mb in (0.5, 0.9):
        globals()["MARKET_BR"] = mb
        print(f"| {mb:.0%} dos clientes no Brasil | {brl(month(sc, 100, 'recomendados')['result'])} |")
    globals()["MARKET_BR"] = 0.70
    print(f"| uso de 100% da franquia (com expansão necessária) | {brl(month({**sc, 'util': 1.0}, 100, 'recomendados')['result'])} |")
    so = SCENARIOS["provável + otimizações"]
    print(f"| otimizações (transcrição -40%, Basic -30% GPU, Pro 2 lives) | {brl(month(so, 100, 'recomendados')['result'])} |")
    print(f"| otimizações + preços propostos | {brl(month(so, 100, 'propostos')['result'])} |")
    print("\n## G. Custo dos recursos do Pro\n")
    k4_gb = K4_EXPORTS_PRO * K4_MB / 1000
    print(f"- 4K: {K4_EXPORTS_PRO} exportações/mês = {k4_gb:.1f} GB se guardadas; CPU ~{K4_EXPORTS_PRO * K4_RENDER_S / 3600:.1f} h/mês "
          f"por cliente Pro (estimativa sem benchmark).")
    srcs = PLANS["pro"]["hours"] * 0.6 * SHORTS_PER_LIVE_H * SOURCE_MB_PER_CLIP / 1000
    print(f"- Mídia original: ~{SOURCE_MB_PER_CLIP:.0f} MB por corte; um Pro com uso de 60% gera ~{srcs:.0f} GB/mês de fonte; "
          f"{SOURCE_RETENTION_DAYS} dias de retenção = ~{srcs / 30 * SOURCE_RETENTION_DAYS:.1f} GB guardados por cliente Pro.")
    for name, s in SCENARIOS.items():
        g = gpu_mult(s)["pro"]
        print(f"- 2ª análise premium ({name}): {s['second_share']:.0%} dos candidatos × {SECOND_ANALYSIS_COST}x → Pro custa {g:.2f}x de GPU por hora de live.")
