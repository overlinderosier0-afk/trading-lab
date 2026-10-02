"""Moteur de backtesting : simulation réaliste, sans look-ahead.

Conventions d'exécution (anti look-ahead strict) :
- Les signaux sont calculés sur bougies CLOSES (index i).
- Un ordre est exécuté à l'OUVERTURE de la bougie suivante (open[i+1]).
- Stop-loss / take-profit : testés sur high/low des bougies APRÈS l'entrée.
  Si stop et take sont touchés dans la même bougie, le stop gagne (conservateur).
- Frais + slippage appliqués à chaque exécution (aller et retour).
- Pas de levier : notionnel <= equity disponible.
- Short : vente à découvert avec rachat (collatéral supposé suffisant, pas de levier).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("tradinglab")

PERIODS_PER_YEAR = {"1h": 8760, "4h": 2190, "1d": 365}
MIN_NOTIONAL_USD = 10.0  # en dessous : trade ignoré (dust)
MAX_CURVE_POINTS = 2000  # échantillonnage de la courbe d'equity en sortie


@dataclass
class BacktestConfig:
    initial_capital: float = 1000.0
    fee_rate: float = 0.001       # 0.1 % par côté (ordre de grandeur Binance spot)
    slippage: float = 0.0005      # 0.05 % en défaveur du trader, à chaque exécution
    risk_per_trade: float = 0.01  # fraction d'equity risquée (si stop_loss_pct > 0)
    stop_loss_pct: float = 0.02   # 2 % ; 0 = désactivé
    take_profit_pct: float = 0.0  # 0 = désactivé
    position_pct: float = 1.0     # fraction d'equity engagée (si pas de stop-loss)
    allow_short: bool = True


def _exec_price(price: float, buying: bool, slippage: float) -> float:
    """Prix d'exécution avec slippage, toujours en défaveur du trader."""
    return price * (1 + slippage) if buying else price * (1 - slippage)


def position_notional(equity: float, cfg: BacktestConfig) -> float:
    """Taille de position : calibrée sur le risque si stop-loss, sinon fraction d'equity."""
    if cfg.stop_loss_pct > 0:
        notional = equity * cfg.risk_per_trade / cfg.stop_loss_pct
    else:
        notional = equity * cfg.position_pct
    return min(notional, equity)  # pas de levier


def run_backtest(
    timestamps: list[str],
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    signals: np.ndarray,
    cfg: BacktestConfig,
    timeframe: str = "1h",
) -> dict:
    """Simule la stratégie barre par barre. Retourne trades + courbes + métriques."""
    n = len(closes)
    if n == 0:
        raise ValueError("aucune bougie à tester")
    if not (len(opens) == len(highs) == len(lows) == n and len(signals) == n):
        raise ValueError("tableaux de tailles incohérentes")

    cash = float(cfg.initial_capital)
    pos = 0            # 0 = flat, +1 = long, -1 = short
    qty = 0.0
    entry_price = 0.0
    entry_fee = 0.0
    entry_cash = 0.0
    entry_idx = -1
    stop_px = 0.0
    take_px = 0.0

    trades: list[dict] = []
    equity_curve = np.empty(n)
    in_market = np.zeros(n, dtype=bool)

    def close_position(exit_idx: int, exit_price: float, reason: str) -> None:
        nonlocal cash, pos, qty
        buying = pos < 0  # pour clôturer un short, on ACHÈTE
        px = _exec_price(exit_price, buying, cfg.slippage)
        if pos > 0:  # clôture long : on vend
            proceeds = qty * px
            fee = proceeds * cfg.fee_rate
            cash += proceeds - fee
        else:  # clôture short : on rachète
            cost = qty * px
            fee = cost * cfg.fee_rate
            cash -= cost + fee
        pnl = cash - entry_cash
        trades.append({
            "entry_ts": timestamps[entry_idx],
            "entry_price": round(entry_price, 6),
            "exit_ts": timestamps[exit_idx],
            "exit_price": round(px, 6),
            "direction": "long" if pos > 0 else "short",
            "qty": qty,
            "fees": round(entry_fee + fee, 6),
            "pnl": round(pnl, 6),
            "pnl_pct": round(pnl / entry_cash, 6) if entry_cash else 0.0,
            "exit_reason": reason,
        })
        pos = 0
        qty = 0.0

    for i in range(n):
        # 1) Stop-loss / take-profit sur la bougie i, dès la bougie d'entrée
        #    (l'entrée se fait à l'ouverture : tout le range de la bougie se
        #    développe après notre fill — en live, le stop serait touché).
        if pos != 0 and i >= entry_idx and cfg.stop_loss_pct > 0:
            hit_stop = (lows[i] <= stop_px) if pos > 0 else (highs[i] >= stop_px)
            hit_take = False
            if cfg.take_profit_pct > 0:
                hit_take = (highs[i] >= take_px) if pos > 0 else (lows[i] <= take_px)
            if hit_stop:
                close_position(i, stop_px, "stop_loss")
            elif hit_take:
                close_position(i, take_px, "take_profit")

        # 2) Signal de la bougie i -> exécution à l'ouverture de i+1
        #    BUY = passer long, SELL = passer short (ou flat si short interdit),
        #    HOLD = garder la position actuelle.
        if signals[i] == "BUY":
            target = 1
        elif signals[i] == "SELL":
            target = -1 if cfg.allow_short else 0
        else:
            target = pos
        if target != pos and i + 1 < n:
            if pos != 0:
                close_position(i + 1, opens[i + 1], "signal")
            if target != 0:
                equity = cash  # flat -> equity = cash
                notional = position_notional(equity, cfg)
                if notional >= MIN_NOTIONAL_USD:
                    buying = target > 0
                    px = _exec_price(opens[i + 1], buying, cfg.slippage)
                    qty = notional / px
                    if target > 0:
                        cost = qty * px
                        entry_fee = cost * cfg.fee_rate
                        cash -= cost + entry_fee
                        stop_px = px * (1 - cfg.stop_loss_pct)
                        take_px = px * (1 + cfg.take_profit_pct) if cfg.take_profit_pct > 0 else 0.0
                    else:
                        proceeds = qty * px
                        entry_fee = proceeds * cfg.fee_rate
                        cash += proceeds - entry_fee
                        stop_px = px * (1 + cfg.stop_loss_pct)
                        take_px = px * (1 - cfg.take_profit_pct) if cfg.take_profit_pct > 0 else 0.0
                    entry_price = px
                    entry_cash = equity
                    entry_idx = i + 1
                    pos = target

        # 3) Valorisation fin de bougie i (mark-to-market)
        if pos > 0:
            equity_curve[i] = cash + qty * closes[i]
        elif pos < 0:
            equity_curve[i] = cash - qty * closes[i]
        else:
            equity_curve[i] = cash
        in_market[i] = pos != 0

    # Position encore ouverte en fin de données -> clôture au dernier close
    if pos != 0:
        close_position(n - 1, closes[n - 1], "end_of_data")
        equity_curve[n - 1] = cash

    return {
        "trades": trades,
        "metrics": compute_metrics(trades, equity_curve, float(cfg.initial_capital),
                                   cfg, in_market, timeframe=timeframe),
        "equity_curve": _downsample_curve(timestamps, equity_curve),
    }


def _downsample_curve(timestamps: list[str], equity_curve: np.ndarray) -> list[dict]:
    n = len(equity_curve)
    step = max(1, n // MAX_CURVE_POINTS)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    peak = np.maximum.accumulate(equity_curve)
    out = []
    for j in idx:
        dd_pct = (equity_curve[j] - peak[j]) / peak[j] if peak[j] > 0 else 0.0
        out.append({
            "ts": timestamps[j],
            "equity": round(float(equity_curve[j]), 2),
            "drawdown_pct": round(float(dd_pct), 6),
        })
    return out


def _streaks(pnls: np.ndarray) -> tuple[int, int]:
    win_streak = loss_streak = 0
    cw = cl = 0
    for p in pnls:
        if p > 0:
            cw += 1
            cl = 0
            win_streak = max(win_streak, cw)
        elif p < 0:
            cl += 1
            cw = 0
            loss_streak = max(loss_streak, cl)
        else:
            cw = cl = 0
    return win_streak, loss_streak


def compute_metrics(
    trades: list[dict],
    equity_curve: np.ndarray,
    initial_capital: float,
    cfg: BacktestConfig,
    in_market: np.ndarray,
    timeframe: str = "1h",
) -> dict:
    final = float(equity_curve[-1])
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    n_trades = len(trades)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0

    peak = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.min()) if n_trades else 0.0

    per_ret = np.diff(equity_curve) / np.where(equity_curve[:-1] != 0, equity_curve[:-1], 1.0)
    ppy = PERIODS_PER_YEAR.get(timeframe, 8760)
    if len(per_ret) > 1 and float(per_ret.std()) > 0:
        sharpe = float(per_ret.mean() / per_ret.std() * np.sqrt(ppy))
        volatility = float(per_ret.std() * np.sqrt(ppy))
    else:
        sharpe = 0.0
        volatility = 0.0

    win_streak, loss_streak = _streaks(pnls)
    total_fees = round(float(sum(t["fees"] for t in trades)), 6)

    return {
        "initial_capital": initial_capital,
        "final_capital": round(final, 2),
        "total_return": round(final / initial_capital - 1, 6),
        "n_trades": n_trades,
        "win_rate": round(len(wins) / n_trades, 4) if n_trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "expectancy": round(float(pnls.mean()), 4) if n_trades else 0.0,
        "max_drawdown": round(max_dd, 6),
        "sharpe_ratio": round(sharpe, 4),
        "volatility_annualized": round(volatility, 4),
        "win_streak_max": win_streak,
        "loss_streak_max": loss_streak,
        "total_fees": total_fees,
        "time_in_market": round(float(in_market.mean()), 4),
        "fee_rate": cfg.fee_rate,
        "slippage": cfg.slippage,
    }
