"""Trading Lab — backend.

Étape B : /health, /ready.
Étape C : market data (Binance).
Étapes D/E : indicateurs + stratégie démo.
Étape F : backtesting.
Étape G : paper trading + risk management + scheduler.

Logs JSON structurés sur stdout — lisibles via `docker compose logs`.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db, scheduler
from app.api import backtests as backtests_api
from app.api import market as market_api
from app.api import paper as paper_api
from app.api import strategies as strategies_api
from app.api import system as system_api
from app.api import validation as validation_api
from app.config import settings
from app.risk import limits


# ---------------------------------------------------------------- logs
class JsonFormatter(logging.Formatter):
    """Formateur JSON : une ligne = un événement, facile à filtrer."""

    def format(self, record: logging.Record) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "event": getattr(record, "event", "LOG"),
            "msg": record.getMessage(),
            "service": "trading-lab-backend",
        }
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # uvicorn parle trop : on le calme sans le museler
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logging.getLogger("tradinglab")


log = setup_logging()


# ---------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Backend démarré", extra={"event": "STARTUP"})
    try:
        db.init_db()
    except Exception as exc:
        log.critical(
            "init_db impossible après retries : %s",
            type(exc).__name__, extra={"event": "DB_INIT_FAIL"},
        )
    try:
        scheduler.start()
        limits.log_event("INFO", "STARTUP", "Trading Lab démarré (scheduler actif)")
    except Exception as exc:
        log.error("scheduler impossible : %s", type(exc).__name__)
    yield
    scheduler.stop()
    log.info("Backend arrêté", extra={"event": "SHUTDOWN"})


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(market_api.router)
app.include_router(strategies_api.router)
app.include_router(backtests_api.router)
app.include_router(paper_api.router)
app.include_router(system_api.router)
app.include_router(validation_api.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/ready")
def ready() -> JSONResponse:
    """Prêt si PostgreSQL répond (connexion courte, sans secret dans les logs)."""
    try:
        with psycopg.connect(db.libpq_dsn(), connect_timeout=3):
            pass
    except Exception as exc:  # DB pas encore là, mauvais mot de passe, réseau...
        log.warning(
            "PostgreSQL injoignable : %s",
            type(exc).__name__, extra={"event": "DB_UNREACHABLE"},
        )
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": settings.app_name, "database": "down"},
        )
    return {"status": "ready", "service": settings.app_name, "database": "up"}


# ---------------------------------------------------------------- frontend statique
# Le dashboard (build Vite) est servi par le backend lui-même : pas de conteneur
# frontend séparé, et aucune modification du compose tikeayiti nécessaire.
# Le catch-all SPA est enregistré EN DERNIER : il ne doit jamais intercepter
# /api/*, /health, /ready ni /docs (le routage FastAPI matche dans l'ordre
# d'enregistrement — avant ce fix, /health retournait index.html).
import os

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")),
              name="assets")

    @app.get("/", include_in_schema=False)
    def _spa_root():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        candidate = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
else:
    log.warning("Dossier static introuvable : dashboard indisponible",
                extra={"event": "STATIC_MISSING"})
