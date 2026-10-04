"""Configuration : tout vient de l'environnement (injecté par docker-compose via .env)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "Trading Lab"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://tradinglab:changeme@trading-lab-postgres:5432/tradinglab"
    # Kill switch manuel du paper trading (phase 6) : false = aucune nouvelle position.
    trading_enabled: bool = True
    # Market data : API publique Binance. data-api.binance.vision = endpoint officiel
    # "market data only" (pas de géo-restriction, contrairement à api.binance.com qui
    # peut renvoyer HTTP 451 selon l'IP du VPS — vu le 2026-10-01 sur Contabo).
    binance_base_url: str = "https://data-api.binance.vision"
    # Market data : paires suivies par le scheduler (sync + paper trading).
    market_data_symbols: str = "BTC/USDT,ETH/USDT"
    market_data_timeframes: str = "1h,4h,1d"
    # Stratégie démo EMA/RSI (phase 3) — tous les paramètres configurables.
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_buy: float = 50.0
    rsi_sell: float = 50.0
    # Paper trading : désactivé par défaut, activation explicite requise.
    paper_trading_enabled: bool = False
    # Risk management (phase 7).
    initial_capital: float = 1000.0
    risk_per_trade: float = 0.01
    max_open_positions: int = 3
    max_daily_loss: float = 0.03
    max_drawdown: float = 0.10
    fee_rate: float = 0.001
    slippage: float = 0.0005
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.0
    position_pct: float = 1.0
    allow_short: bool = True
    # Signal Lab : génération manuelle de signaux multi-facteurs (pas d'auto-trade).
    # La résolution des signaux échus tourne toujours (toutes les 5 min) :
    # elle ne fait que mesurer win/loss, jamais ouvrir de position.
    signal_lab_enabled: bool = True
    signal_lab_direction_threshold: float = 15.0
    signal_lab_sl_atr_mult: float = 1.5
    signal_lab_tp_atr_mult: float = 2.0


settings = Settings()
