"""Endpoints système : état de santé détaillé et journal d'événements."""

from __future__ import annotations

import shutil

import httpx
from fastapi import APIRouter

from app import db, scheduler
from app.config import settings
from app.risk import limits

router = APIRouter(prefix="/api/system", tags=["system"])


def _mem_info() -> dict:
    try:
        info: dict[str, float] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                    info[parts[0].rstrip(":")] = float(parts[1]) / 1024  # Mo
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", 0)
        return {"total_mb": round(total), "used_pct": round((total - avail) / total * 100, 1)
                if total else 0}
    except OSError:
        return {"total_mb": 0, "used_pct": 0}


def _check_binance() -> dict:
    try:
        r = httpx.get(settings.binance_base_url.rstrip("/") + "/api/v3/ping", timeout=5.0)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return {"reachable": ok}


@router.get("/status")
def system_status() -> dict:
    """État des composants : à afficher sur le dashboard."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        database = {"reachable": True}
    except Exception:
        database = {"reachable": False}
    disk = shutil.disk_usage("/")
    return {
        "service": settings.app_name,
        "database": database,
        "market_data_provider": _check_binance(),
        "scheduler_running": scheduler.is_running(),
        "trading_enabled": limits.kill_switch_on(),
        "paper_trading_enabled": limits.paper_trading_on(),
        "disk": {
            "total_gb": round(disk.total / 1e9, 1),
            "used_pct": round(disk.used / disk.total * 100, 1),
        },
        "memory": _mem_info(),
    }


@router.get("/events")
def system_events(limit: int = 100, level: str | None = None) -> dict:
    """Journal des événements système (PAPER_ORDER, RISK_LIMIT, erreurs...)."""
    limit = max(1, min(limit, 500))
    query = "SELECT ts, level, event, message FROM system_events"
    params: list = []
    if level:
        query += " WHERE level = %s"
        params.append(level.upper())
    query += " ORDER BY ts DESC LIMIT %s"
    params.append(limit)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return {"events": [
        {"ts": r[0].isoformat(), "level": r[1], "event": r[2], "message": r[3]}
        for r in rows
    ]}
