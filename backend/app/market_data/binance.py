"""Client minimal Binance Spot — données OHLCV publiques, SANS clé API.

Référence : https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
URL par défaut : data-api.binance.vision, endpoint officiel "market data only"
de Binance (mêmes chemins /api/v3/...). api.binance.com peut renvoyer HTTP 451
(géo-restriction) depuis certaines IPs de VPS — d'où ce choix.
Limites : généreuses pour notre usage ; on reste poli avec une petite pause
entre les pages + retries avec backoff.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.config import settings

log = logging.getLogger("tradinglab")

MAX_LIMIT = 1000  # maximum autorisé par /api/v3/klines


def base_url() -> str:
    """URL de base de l'API publique (configurable via BINANCE_BASE_URL)."""
    return settings.binance_base_url.rstrip("/")


class BinanceError(Exception):
    """La récupération a échoué après retries."""


def normalize_symbol(symbol: str) -> str:
    """'BTC/USDT' -> 'BTCUSDT' (format natif Binance)."""
    return symbol.replace("/", "").replace("-", "").upper()


def _request_with_retry(client: httpx.Client, params: dict, attempts: int = 3) -> list:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get("/api/v3/klines", params=params)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                log.warning(
                    "Rate limit Binance, pause %ss",
                    retry_after, extra={"event": "DATA_RATE_LIMIT"},
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status = exc.response.status_code
            log.warning(
                "Binance HTTP %s (tentative %s/%s)",
                status, attempt, attempts, extra={"event": "DATA_FETCH_RETRY"},
            )
            if status in (451, 403):
                break  # géo-restriction/blocage : inutile de réessayer en boucle
        except httpx.HTTPError as exc:
            last_exc = exc
            log.warning(
                "Erreur réseau Binance (tentative %s/%s) : %s",
                attempt, attempts, type(exc).__name__,
                extra={"event": "DATA_FETCH_RETRY"},
            )
        time.sleep(2 ** attempt)
    raise BinanceError(
        f"Binance /klines a échoué après {attempts} tentatives "
        f"({params.get('symbol')} {params.get('interval')})"
    ) from last_exc


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = MAX_LIMIT,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Récupère des bougies brutes (la plus récente peut être incomplète : voir sync)."""
    own_client = client is None
    client = client or httpx.Client(base_url=base_url(), timeout=15.0)
    try:
        params: dict = {
            "symbol": normalize_symbol(symbol),
            "interval": interval,
            "limit": min(limit, MAX_LIMIT),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        rows = _request_with_retry(client, params)
        return [
            {
                "open_ms": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "close_ms": int(r[6]),
            }
            for r in rows
        ]
    finally:
        if own_client:
            client.close()
