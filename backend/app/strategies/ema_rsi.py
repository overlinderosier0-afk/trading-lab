"""Stratégie de DÉMONSTRATION : croisement EMA 20/50 filtré par RSI.

BUY  : EMA_fast > EMA_slow ET RSI > rsi_buy
SELL : EMA_fast < EMA_slow ET RSI < rsi_sell
Sinon HOLD.

⚠️ Pédagogique uniquement — ne pas la considérer comme rentable.
Tous les paramètres sont configurables (défauts = .env).
"""

from __future__ import annotations

import numpy as np

from app.config import settings
from app.indicators.momentum import rsi
from app.indicators.trend import ema
from app.strategies.base import Signal, Strategy


class EmaRsiCross(Strategy):
    name = "ema_rsi_demo"
    description = (
        "Démo pédagogique : croisement EMA 20/50 filtré par RSI 14. "
        "Sert à tester le moteur, pas à gagner."
    )
    # Statut acté le 2026-10-02 après IS/OOS + walk-forward sur données réelles :
    # IS/OOS « sur-optimisé » (IS +14,78 % / Sharpe 1,10 -> OOS -1,58 % / -0,18),
    # walk-forward incohérent (Sharpe par pli de -3,78 à +5,55). Aucun edge.
    edge_status = "NO_EDGE_DEMONSTRATED"
    edge_note = (
        "Aucune preuve d'edge démontré — stratégie de démonstration uniquement. "
        "IS/OOS + walk-forward (oct. 2026, BTCUSDT 1h) : sur-optimisation constatée, "
        "aucune cohérence inter-plis. Ne pas utiliser pour trader, même en virtuel."
    )

    @classmethod
    def default_params(cls) -> dict:
        return {
            "ema_fast": settings.ema_fast,
            "ema_slow": settings.ema_slow,
            "rsi_period": settings.rsi_period,
            "rsi_buy": settings.rsi_buy,
            "rsi_sell": settings.rsi_sell,
        }

    def generate(self, candles: dict[str, np.ndarray]) -> np.ndarray:
        close = np.asarray(candles["close"], dtype=float)
        n = len(close)
        p = self.params
        e_fast = ema(close, int(p["ema_fast"]))
        e_slow = ema(close, int(p["ema_slow"]))
        r = rsi(close, int(p["rsi_period"]))

        signals = np.full(n, Signal.HOLD.value, dtype=object)
        valid = ~(np.isnan(e_fast) | np.isnan(e_slow) | np.isnan(r))
        buy = valid & (e_fast > e_slow) & (r > float(p["rsi_buy"]))
        sell = valid & (e_fast < e_slow) & (r < float(p["rsi_sell"]))
        signals[buy] = Signal.BUY.value
        signals[sell] = Signal.SELL.value
        return signals

    def indicator_values(self, candles: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Valeurs des indicateurs (debug + futur dashboard)."""
        close = np.asarray(candles["close"], dtype=float)
        p = self.params
        return {
            "ema_fast": ema(close, int(p["ema_fast"])),
            "ema_slow": ema(close, int(p["ema_slow"])),
            "rsi": rsi(close, int(p["rsi_period"])),
        }
