"""Modelo de capacidade e financeiro do AI Stream Clipper (anexo de docs/ESTUDO_COMERCIAL.md).

Rodar:  python docs/estudo_comercial_modelo.py
Sem dependências. Cada número vem marcado no relatório como MEDIDO, VERIFICADO, ESTIMATIVA ou HIPÓTESE.
Mude as premissas no bloco PREMISSAS e rode de novo.
"""

from __future__ import annotations

# ----------------------------------------------------------------------------- PREMISSAS
# MEDIDO em produção (2026-11-20, últimas 24 h, 63,4 horas de live capturadas)
AUDIO_WALL_S_PER_LIVE_H = 2456      # segundos de job de transcrição por hora de live
AUDIO_CORES_PER_JOB = 1.75          # 6 jobs paralelos = ~1055% de CPU no contêiner (snapshot)
SCORE_S_PER_LIVE_H = 133
RENDER_S_PER_LIVE_H = 121           # 1080p, ffmpeg veryfast/CRF 21
VISION_GPU_S_PER_LIVE_H = 205       # Qwen3-VL 2B INT4 nas Arc A750 (42 s por análise, p50)
CLIPS_PER_LIVE_H = 2.37
CLIP_MB = 16.2                      # Short médio de 36 s em 1080p
SOURCE_MBPS = 6.63                  # bitrate médio das lives capturadas

# ESTIMATIVA de capacidade (3 máquinas de produção; a 212 não processa)
THREADS_TOTAL = 36 + 32 + 32        # 201 + 206 + 210
THREADS_RESERVED = 20               # SO, API, banco-cliente, captura, pico de render, margem
GPUS = 2                            # duas Arc A750 SEPARADAS (não somam 16 GB)
GPU_UTIL_MAX = 0.80                 # não planejar acima de 80% de ocupação
RENDER_CORES = 4                    # ffmpeg por render (estimativa)

CPU_CORE_H_PER_LIVE_H = (AUDIO_WALL_S_PER_LIVE_H * AUDIO_CORES_PER_JOB + SCORE_S_PER_LIVE_H
                         + RENDER_S_PER_LIVE_H * RENDER_CORES) / 3600 + 0.05  # +captura

# custo de IA por plano (multiplicador sobre o padrão medido) - ESTIMATIVA
AI_GPU_MULT = {"basic": 1.0, "creator": 1.5, "pro": 3.0}   # mais quadros; Pro = 4B + 2ª análise

PLANS = {  # proposta do dono (hipótese de produto)
    "basic":   {"hours": 40,  "channels": 1, "simul": 1, "gb": 5,  "usd": 19.99, "brl": 59.90},
    "creator": {"hours": 100, "channels": 2, "simul": 2, "gb": 12, "usd": 49.99, "brl": 149.90},
    "pro":     {"hours": 200, "channels": 4, "simul": 3, "gb": 25, "usd": 99.99, "brl": 299.90},
}
MIX = {"basic": 0.50, "creator": 0.35, "pro": 0.15}        # HIPÓTESE
MARKET_SPLIT_BR = 0.70                                      # HIPÓTESE: 70% Brasil, 30% EUA no início
PEAK_WINDOW_H = 6                                           # lives concentradas 18h-24h (HIPÓTESE)

# custos - VERIFICADO (taxas) / HIPÓTESE (fixos)
FX = 5.86                                    # USD->BRL fechamento 18/11/2026 (verificado)
STRIPE_US = (0.029, 0.30, 0.007)             # 2,9% + US$0,30 + Billing 0,7% (Billing a confirmar)
CARD_BR = (0.0399, 0.39, 0.007)              # 3,99% + R$0,39 (+0,7% Billing, a confirmar)
FX_SPREAD = 0.015                            # câmbio/remessa ao trazer USD (HIPÓTESE)
TAX_BR = {"anexo_iii": 0.06, "anexo_v": 0.155}   # Simples, 1ª faixa (verificado em fontes secundárias)
TAX_EXPORT = 0.04                            # exportação de serviço: HIPÓTESE (validar com contador)
FIXED_BRL = {                                # HIPÓTESES mensais
    "energia (≈1,0 kW médio, R$1,00/kWh)": 730,
    "internet empresarial": 700,
    "depreciação hardware (R$ 90 mil / 36 meses)": 2500,
    "manutenção/peças": 400,
    "domínio, e-mail, backups externos, ferramentas": 300,
}
LABOR_BRL = 0                                # salário do dono fora (mostrar separadamente)
SUPPORT_BRL_PER_CLIENT = 6.0                 # tempo de suporte por cliente/mês (HIPÓTESE)
ENERGY_BRL_PER_LIVE_H = 0.05                 # ~40 Wh incrementais por hora de live (ESTIMATIVA)


# ----------------------------------------------------------------------------- CAPACIDADE
def capacity() -> dict:
    cpu_lives = (THREADS_TOTAL - THREADS_RESERVED) / CPU_CORE_H_PER_LIVE_H
    gpu_s_per_live_h = VISION_GPU_S_PER_LIVE_H * sum(MIX[p] * AI_GPU_MULT[p] for p in MIX)
    gpu_lives = GPUS * 3600 * GPU_UTIL_MAX / gpu_s_per_live_h
    return {"cpu_core_h_per_live_h": CPU_CORE_H_PER_LIVE_H, "cpu_concurrent_lives": cpu_lives,
            "gpu_s_per_live_h_mix": gpu_s_per_live_h, "gpu_concurrent_lives": gpu_lives,
            "safe_concurrent_lives": min(cpu_lives, gpu_lives)}


def peak_lives_per_client(util: float) -> float:
    """Lives simultâneas esperadas por cliente no pico, dado o uso da franquia (0..1)."""
    tot = 0.0
    for p, share in MIX.items():
        daily_h = PLANS[p]["hours"] * util / 30
        tot += share * min(PLANS[p]["simul"], daily_h / PEAK_WINDOW_H * 1.6)  # 1,6 = concentração na noite
    return tot


# ----------------------------------------------------------------------------- FINANÇAS
def per_client_month(plan: str, market: str, util: float, tax_regime: str) -> dict:
    pl = PLANS[plan]
    if market == "us":
        gross_brl = pl["usd"] * FX
        fee_brl = (pl["usd"] * (STRIPE_US[0] + STRIPE_US[2]) + STRIPE_US[1]) * FX + gross_brl * FX_SPREAD
        tax = gross_brl * TAX_EXPORT
    else:
        gross_brl = pl["brl"]
        fee_brl = gross_brl * (CARD_BR[0] + CARD_BR[2]) + CARD_BR[1]
        tax = gross_brl * TAX_BR[tax_regime]
    live_h = pl["hours"] * util
    variable = live_h * ENERGY_BRL_PER_LIVE_H * AI_GPU_MULT[plan] ** 0.3 + SUPPORT_BRL_PER_CLIENT
    return {"gross": gross_brl, "fees": fee_brl, "tax": tax, "variable": variable,
            "contribution": gross_brl - fee_brl - tax - variable}


def scenario(n: int, util: float = 0.6, tax_regime: str = "anexo_v", price_mult: float = 1.0) -> dict:
    gross = fees = tax = var = 0.0
    for p, share in MIX.items():
        for market, ms in (("br", MARKET_SPLIT_BR), ("us", 1 - MARKET_SPLIT_BR)):
            r = per_client_month(p, market, util, tax_regime)
            k = n * share * ms
            gross += r["gross"] * k * price_mult
            fees += r["fees"] * k * price_mult
            tax += r["tax"] * k * price_mult
            var += r["variable"] * k
    fixed = sum(FIXED_BRL.values()) + LABOR_BRL
    peak = n * peak_lives_per_client(util)
    cap = capacity()["safe_concurrent_lives"]
    return {"clients": n, "mrr": gross, "fees": fees, "tax": tax, "variable": var,
            "contribution": gross - fees - tax - var, "fixed": fixed, "result": gross - fees - tax - var - fixed,
            "peak_lives": peak, "capacity_lives": cap, "load_pct": peak / cap * 100,
            "storage_gb_worst": n * sum(MIX[p] * PLANS[p]["gb"] for p in MIX),
            "bandwidth_mbps_peak": peak * SOURCE_MBPS}


def brl(x: float) -> str:
    return f"R$ {x:,.0f}".replace(",", ".")


if __name__ == "__main__":
    c = capacity()
    print("## Capacidade")
    for k, v in c.items():
        print(f"- {k}: {v:.2f}")
    for u in (0.4, 0.6, 1.0):
        print(f"- lives no pico por cliente (uso {u:.0%}): {peak_lives_per_client(u):.3f}"
              f" -> clientes suportados: {c['safe_concurrent_lives'] / peak_lives_per_client(u):.0f}")
    print("\n## Clientes suportados por quantidade de GPUs (uso da franquia 40% / 60% / 100%)")
    print("| GPUs | Lives simultâneas (mistura) | 40% | 60% | 100% |")
    print("|---|---|---|---|---|")
    for g in (2, 3, 4, 6):
        globals()["GPUS"] = g
        cc = capacity()["safe_concurrent_lives"]
        print(f"| {g} | {cc:.0f} | " + " | ".join(f"{cc / peak_lives_per_client(u):.0f}" for u in (0.4, 0.6, 1.0)) + " |")
    globals()["GPUS"] = 2
    print("\n## Unitário por plano (uso 60%, Anexo V no BR)")
    print("| Plano | Mercado | Receita | Taxas | Impostos | Variável | Contribuição | Margem |")
    print("|---|---|---|---|---|---|---|---|")
    for p in PLANS:
        for m in ("br", "us"):
            r = per_client_month(p, m, 0.6, "anexo_v")
            print(f"| {p} | {m.upper()} | {brl(r['gross'])} | {brl(r['fees'])} | {brl(r['tax'])} | {brl(r['variable'])} | "
                  f"{brl(r['contribution'])} | {r['contribution'] / r['gross'] * 100:.0f}% |")
    for util, label in ((0.6, "uso médio 60%"), (1.0, "uso máximo 100%")):
        print(f"\n## Cenários ({label}, preços propostos, Anexo V, câmbio {FX})")
        print("| Clientes | MRR | Taxas | Impostos | Variável | Margem de contribuição | Fixos | Resultado | Lives no pico | Ocupação | Disco pior caso | Banda no pico |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for n in (25, 50, 100, 200, 250):
            s = scenario(n, util)
            print(f"| {n} | {brl(s['mrr'])} | {brl(s['fees'])} | {brl(s['tax'])} | {brl(s['variable'])} | {brl(s['contribution'])} | "
                  f"{brl(s['fixed'])} | {brl(s['result'])} | {s['peak_lives']:.1f} | {s['load_pct']:.0f}% | "
                  f"{s['storage_gb_worst']:.0f} GB | {s['bandwidth_mbps_peak']:.0f} Mbps |")
    print("\n## Sensibilidade (100 clientes, uso 60%)")
    print("| Variação | Resultado mensal |")
    print("|---|---|")
    base = scenario(100)["result"]
    print(f"| base (câmbio {FX}, Anexo V) | {brl(base)} |")
    print(f"| Anexo III (Fator R ≥ 28%) | {brl(scenario(100, tax_regime='anexo_iii')['result'])} |")
    for fx in (5.0, 6.5):
        globals()["FX"] = fx
        print(f"| câmbio {fx} | {brl(scenario(100)['result'])} |")
    globals()["FX"] = 5.86
    for pm in (0.7, 0.85):
        print(f"| preços {pm:.0%} dos propostos | {brl(scenario(100, price_mult=pm)['result'])} |")
