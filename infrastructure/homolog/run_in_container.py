"""Runs inside the throwaway runner container started by homologacao-pg.sh (never on the host).

/src is the read-only checkout, /out the report directory. Installs only backend[dev,vision] (no audio/AI models),
runs the whole backend suite on the throwaway PostgreSQL (except the Whisper end-to-end tests) and the PostgreSQL
concurrency and migration tests, then writes the PT/EN report with scripts/homologacao_report.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SRC, OUT, WORK = Path("/src"), Path("/out"), Path("/work")
PG_URL = "postgresql+psycopg://asc_test:asc_test@postgres:5432/postgres"  # the run's own container only
STEPS = OUT / "steps.jsonl"
SCOPE = ("PostgreSQL real: suíte completa do backend (exceto e2e com Whisper) + concorrência + migrações. "
         "SQLite, e2e, comercial, Playwright e dashboard foram homologados na máquina do desenvolvedor.")


def record(step: str, status: str, seconds: float, command: str, reason: str = "") -> None:
    with STEPS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"step": step, "status": status, "seconds": seconds, "command": command,
                            "reason": reason, "required": True}) + "\n")


def step(name: str, cmd: list[str], cwd: Path, env: dict | None = None) -> bool:
    print(f"==> {name}", flush=True)
    start = time.time()
    with (OUT / f"{name}.log").open("w", encoding="utf-8") as log:
        code = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=log, stderr=subprocess.STDOUT).returncode
    ok = code == 0
    record(name, "passed" if ok else "failed", time.time() - start, " ".join(cmd))
    print("    ok" if ok else f"    FALHOU (veja {name}.log)", flush=True)
    return ok


def main() -> int:
    STEPS.write_text("", encoding="utf-8")
    env_info = {"commit": os.environ.get("HOMOLOG_COMMIT", "?"), "branch": os.environ.get("HOMOLOG_BRANCH", "?"),
                "dirty": bool(os.environ.get("HOMOLOG_DIRTY")), "os": os.environ.get("HOMOLOG_OS", "?"),
                "docker": os.environ.get("HOMOLOG_DOCKER", "?"), "compose": "não usado (containers avulsos)",
                "postgres": os.environ.get("HOMOLOG_POSTGRES", "?"), "python": sys.version.split()[0],
                "node": "não usado nesta homologação", "scope": SCOPE, "run_id": os.environ.get("HOMOLOG_RUN_ID", ""),
                "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (OUT / "environment.json").write_text(json.dumps(env_info, indent=2, ensure_ascii=False), encoding="utf-8")

    # private copy of the read-only checkout (without node dependencies or old reports)
    shutil.copytree(SRC, WORK / "src", ignore=shutil.ignore_patterns("node_modules", ".git", "reports", ".venv",
                                                                    ".homolog", "dist", "__pycache__"))
    src = WORK / "src"
    ok = step("system-deps", ["sh", "-c", "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                                          "--no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core >/dev/null"], src)
    ok = ok and step("python-deps", [sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir", "-e",
                                     ".[dev,vision]"], src / "backend")
    if ok:
        pytest = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        step("backend-postgres-suite", pytest + ["-m", "not models and not e2e and not postgres",
                                                 f"--junitxml={OUT}/backend-postgres-suite.xml"],
             src / "backend", {"ASC_TEST_SUITE_DB": "postgres", "ASC_TEST_PG_URL": PG_URL})
        step("postgres-concurrency-migrations", pytest + ["-m", "postgres", f"--junitxml={OUT}/postgres-concurrency-migrations.xml",
                                                          "../tests/test_postgres_shared_lives.py",
                                                          "../tests/test_postgres_customer_publishing.py",
                                                          "../tests/test_postgres_migrations.py",
                                                          "../tests/test_postgres_benefits.py"],
             src / "backend", {"ASC_REQUIRE_PG": "1", "ASC_TEST_PG_URL": PG_URL})
    else:
        for name in ("backend-postgres-suite", "postgres-concurrency-migrations"):
            record(name, "not_run", 0, "pytest", "dependências não instaladas (veja os logs anteriores)")
    return subprocess.run([sys.executable, str(src / "scripts" / "homologacao_report.py"), str(OUT)]).returncode


if __name__ == "__main__":
    sys.exit(main())
