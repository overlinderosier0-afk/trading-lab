// Client API minimal (même origine : le backend sert aussi le frontend).

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${path} — ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body?: unknown) =>
    req<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    }),
};

export interface BacktestSummary {
  id: string;
  created_at: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  total_return: string | null;
  max_drawdown: string | null;
  n_trades: string | null;
  profit_factor: string | null;
  sharpe_ratio: string | null;
}

export interface BacktestDetail {
  id: string;
  created_at: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  config: Record<string, unknown>;
  results: {
    metrics: Record<string, number | string | null>;
    trades: Trade[];
    equity_curve: { ts: string; equity: number; drawdown_pct: number }[];
  };
}

export interface Trade {
  symbol: string;
  timeframe: string;
  entry_ts: string;
  entry_price: number;
  exit_ts: string;
  exit_price: number;
  direction: string;
  qty: number;
  fees: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string;
}

export interface PaperAccount {
  capital_initial: number;
  cash: number;
  equity: number;
  unrealized_pnl: number;
  open_positions: number;
  trading_enabled: boolean;
  paper_trading_enabled: boolean;
}

export interface Position {
  symbol: string;
  timeframe: string;
  direction: string;
  qty: number;
  entry_price: number;
  entry_ts: string;
  current_price: number | null;
  unrealized_pnl: number;
  unrealized_pct: number;
  stop_price: number | null;
  take_price: number | null;
}

export interface SignalItem {
  ts: string;
  symbol: string;
  timeframe: string;
  strategy?: string;
  signal: string;
  price: number;
  indicators: Record<string, number | null>;
}

export interface StrategyInfo {
  name: string;
  description: string;
  default_params: Record<string, unknown>;
  edge_status: string;
  edge_note: string;
}

export function fmtUSD(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(digits);
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v < 0 ? "" : "+") + (v * 100).toFixed(digits) + " %";
}

// Pour les ratios déjà en % (win rate...) : jamais de signe +/-, juste la valeur.
export function fmtRate(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v * 100).toFixed(digits) + " %";
}

export function shortTs(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
