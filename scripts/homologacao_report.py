"""Builds the homologation report (PT/EN) from the step log and the JUnit/console outputs of scripts/homologacao.*.

Usage: python scripts/homologacao_report.py <run directory>
The verdict is APROVADO only when every required step passed, the PostgreSQL steps really ran (tests > 0) and no
PostgreSQL test was skipped. Without Docker/PostgreSQL the verdict is PENDENTE, never approved.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PG_STEPS = {"backend-postgres-suite", "postgres-concurrency-migrations"}


def junit(path: Path) -> dict | None:
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in total:
            total[k] += int(s.get(k, 0) or 0)
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    skipped = [f"{c.get('classname')}::{c.get('name')}" for c in root.iter("testcase") if c.find("skipped") is not None]
    failed = [f"{c.get('classname')}::{c.get('name')}" for c in root.iter("testcase")
              if c.find("failure") is not None or c.find("error") is not None]
    total["skipped_list"], total["failed_list"] = skipped[:50], failed[:50]
    return total


def console(log: Path) -> dict | None:
    if not log.is_file():
        return None
    text = log.read_text(encoding="utf-8", errors="replace")
    node = re.findall(r"ℹ (pass|fail|skipped) (\d+)", text)
    if node:
        d = {k: int(v) for k, v in node}
        return {"passed": d.get("pass", 0), "failures": d.get("fail", 0), "skipped": d.get("skipped", 0)}
    pw = re.search(r"(\d+) passed", text)
    if pw:
        failed = re.search(r"(\d+) failed", text)
        skipped = re.search(r"(\d+) skipped", text)
        return {"passed": int(pw.group(1)), "failures": int(failed.group(1)) if failed else 0,
                "skipped": int(skipped.group(1)) if skipped else 0}
    return None


def main(run: Path) -> int:
    steps = [json.loads(l) for l in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    env = json.loads((run / "environment.json").read_text(encoding="utf-8")) if (run / "environment.json").is_file() else {}
    rows, problems, pending = [], [], []
    for s in steps:
        counts = junit(run / f"{s['step']}.xml") or console(run / f"{s['step']}.log")
        s["counts"] = counts
        if s["status"] == "not_run":
            pending.append(s)
        elif s["status"] != "passed" and s.get("required", True):
            problems.append(s)
        if s["step"] in PG_STEPS and s["status"] == "passed":
            if not counts or counts.get("tests", counts.get("passed", 0)) == 0:
                problems.append({**s, "why": "nenhum teste PostgreSQL executado"})
            elif counts.get("skipped"):
                problems.append({**s, "why": f"{counts['skipped']} teste(s) PostgreSQL ignorado(s)"})
        rows.append(s)
    pg_ran = any(s["step"] in PG_STEPS and s["status"] != "not_run" for s in steps)
    if problems:
        verdict = "REPROVADO"
    elif pending or not pg_ran:
        verdict = "PENDENTE"
    else:
        verdict = "APROVADO"

    def fmt(c):
        if not c:
            return "-"
        return (f"{c.get('passed', 0)} ok, {c.get('failures', 0) + c.get('errors', 0)} falha(s), "
                f"{c.get('skipped', 0)} ignorado(s)")

    lines = [
        "# Relatório de homologação local / Local homologation report", "",
        f"**Veredito / Verdict: {verdict}**", "",
        "| Item | Valor / Value |", "| --- | --- |",
        f"| Commit | `{env.get('commit', '?')}`{' (com alterações locais / dirty)' if env.get('dirty') else ''} |",
        f"| Branch | `{env.get('branch', '?')}` |",
        f"| Sistema / OS | {env.get('os', '?')} |",
        f"| Docker | {env.get('docker', 'não disponível / not available')} |",
        f"| Docker Compose | {env.get('compose', '-')} |",
        f"| PostgreSQL | {env.get('postgres', 'não iniciado / not started')} |",
        f"| Python | {env.get('python', '?')} |",
        f"| Node | {env.get('node', '?')} |",
        f"| Início / Start (UTC) | {env.get('started', '?')} |",
        *( [f"| Escopo / Scope | {env['scope']} |"] if env.get("scope") else [] ),
        *( [f"| Execução / Run id | `{env['run_id']}` |"] if env.get("run_id") else [] ), "",
        "## Etapas / Steps", "",
        "| Etapa / Step | Situação / Status | Resultado / Result | Tempo / Time | Comando / Command |",
        "| --- | --- | --- | --- | --- |",
    ]
    status_pt = {"passed": "passou / passed", "failed": "FALHOU / FAILED", "not_run": "não executada / not run"}
    for s in rows:
        lines.append(f"| {s['step']} | {status_pt.get(s['status'], s['status'])} | {fmt(s['counts'])} | "
                     f"{s.get('seconds', 0):.0f} s | `{s.get('command', '')}` |")
    lines += ["", "## PostgreSQL", ""]
    for s in rows:
        if s["step"] in PG_STEPS:
            c = s["counts"] or {}
            lines.append(f"- **{s['step']}**: {status_pt.get(s['status'], s['status'])}; {fmt(c)}")
            for name in c.get("skipped_list", []):
                lines.append(f"  - ignorado / skipped: `{name}`")
            for name in c.get("failed_list", []):
                lines.append(f"  - falhou / failed: `{name}`")
    if problems:
        lines += ["", "## Problemas / Problems", ""]
        for p in problems:
            lines.append(f"- {p['step']}: {p.get('why', 'falhou / failed')} (log: `{p['step']}.log`)")
    if pending:
        lines += ["", "## Não executado / Not run", ""]
        for p in pending:
            lines.append(f"- {p['step']}: {p.get('reason', '')}")
    lines += ["", "## Classificação / Classification", "",
              "- **Executado de verdade / Really executed**: as etapas com situação \"passou\" ou \"FALHOU\" acima.",
              "- **Apenas preparado / Only prepared**: as etapas \"não executada\".",
              "- **Simulado / Simulated**: YouTube, Instagram, TikTok, Facebook, Stripe e InfinitePay são simulados nos "
              "testes (nenhuma chamada externa, conta, pagamento ou publicação real).",
              "- **Não homologado / Not validated**: publicação real em qualquer rede, OAuth real, pagamentos reais, "
              "deploy e migração em produção.", ""]
    (run / "RELATORIO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4]))
    print(f"Relatório: {run / 'RELATORIO.md'}")
    return {"APROVADO": 0, "PENDENTE": 2}.get(verdict, 1)


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
