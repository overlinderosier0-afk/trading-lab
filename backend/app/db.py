"""Accès PostgreSQL (psycopg direct).

Pas d'ORM pour l'instant : les tables sont créées via schema.sql (idempotent)
au démarrage. SQLAlchemy + Alembic arriveront à la phase 9.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import psycopg

from app.config import settings

log = logging.getLogger("tradinglab")


def libpq_dsn() -> str:
    """psycopg veut une URL libpq classique, pas le dialecte SQLAlchemy."""
    url = settings.database_url
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def get_conn() -> psycopg.Connection:
    return psycopg.connect(libpq_dsn(), connect_timeout=5)


def init_db(attempts: int = 5, delay_s: float = 3.0) -> None:
    """Crée les tables si besoin (idempotent), avec retries au démarrage."""
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema)
            log.info("Schéma DB vérifié/créé", extra={"event": "DB_INIT"})
            return
        except Exception as exc:
            last_exc = exc
            log.warning(
                "init_db tentative %s/%s : %s",
                i, attempts, type(exc).__name__,
                extra={"event": "DB_INIT_RETRY"},
            )
            time.sleep(delay_s)
    raise RuntimeError("init_db a échoué après retries") from last_exc
