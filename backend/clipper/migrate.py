"""Programmatic Alembic upgrades (used at API start-up and by ``clipper migrate``)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade_db(database_url: str, revision: str = "head") -> None:
    if database_url.startswith("sqlite:///"):
        path = database_url[len("sqlite:///"):]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(database_url), revision)


def downgrade_db(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
