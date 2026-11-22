"""Disposable PostgreSQL for homologation (never a real server).

* ``ASC_TEST_PG_URL``: admin URL of a THROWAWAY server, e.g. the container of
  ``infrastructure/compose/test-postgres.yml`` (postgresql+psycopg://asc_test:asc_test@127.0.0.1:55432/postgres).
* ``ASC_REQUIRE_PG=1`` (set by ``scripts/homologacao.*``): a PostgreSQL test without a server FAILS instead of being
  skipped, so a homologation report can never show PostgreSQL tests as "skipped".

Safety: only localhost / 127.0.0.1 / ``postgres`` (CI service name) are accepted; anything else, and the production
database host 192.168.1.212 in particular, is refused before any connection. Every database created here is named
``asc_test_*`` and dropped at the end.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
FORBIDDEN = ("192.168.1.212", "212")


class Refused(RuntimeError):
    pass


def admin_url() -> str | None:
    """The validated admin URL, or None when no server is configured."""
    url = os.environ.get("ASC_TEST_PG_URL", "").strip()
    if not url:
        return None
    parts = urlsplit(url.replace("postgresql+psycopg", "postgresql", 1))
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS or any(f in host for f in FORBIDDEN):
        raise Refused(f"refusing to use PostgreSQL at {host!r}: only a local/CI throwaway server is allowed")
    return url


def required() -> bool:
    return os.environ.get("ASC_REQUIRE_PG", "") == "1"


def create_database(prefix: str = "asc_test") -> tuple[str, str]:
    """(database name, URL) of a brand-new empty database on the throwaway server."""
    import sqlalchemy as sa

    url = admin_url()
    if url is None:
        raise Refused("ASC_TEST_PG_URL not set")
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    return name, url.rsplit("/", 1)[0] + f"/{name}"


def drop_database(name: str) -> None:
    import sqlalchemy as sa

    url = admin_url()
    if url is None or not name.startswith("asc_"):
        return
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


def server_version() -> str:
    import sqlalchemy as sa

    engine = sa.create_engine(admin_url())
    with engine.connect() as c:
        version = c.execute(sa.text("SHOW server_version")).scalar()
    engine.dispose()
    return str(version)
