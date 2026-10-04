import { useEffect, useState } from "react";
import { api, fmtUSD, fmtPct, fmtRate, shortTs } from "../api";
import type { BacktestSummary, BacktestDetail, StrategyInfo } from "../api";
import { Card, Stat, Btn, Field, inputCls, LineChart, Loading, ErrorBox, SignalBadge, toneFor, EdgeBadge } from "../components";

const DEFAULT_FORM = {
  symbol: "BTCUSDT", timeframe: "1h", strategy: "ema_rsi_demo",
  initial_capital: 1000, fee_rate: 0.001, slippage: 0.0005,
  risk_per_trade: 0.01, stop_loss_pct: 0.02, take_profit_pct: 0,
  position_pct: 1, allow_short: true,
};

export default function Backtests() {
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [history, setHistory] = useState<BacktestSummary[]>([]);
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    const h = await api.get<{ backtests: BacktestSummary[] }>("/api/backtests?limit=20");
    setHistory(h.backtests);
  }

  useEffect(() => {
    api.get<{ strategies: StrategyInfo[] }>("/api/strategies")
      .then((s) => setStrategies(s.strategies))
      .catch((e) => setError(e.message));
    refresh().catch((e) => setError(e.message));
  }, []);

  async function run() {
    setRunning(true);
    setError("");
    try {
      const res = await api.post<{ id: string }>("/api/backtests", form);
      const d = await api.get<BacktestDetail>(`/api/backtests/${res.id}`);
      setDetail(d);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  async function open(id: string) {
    const d = await api.get<BacktestDetail>(`/api/backtests/${id}`);
    setDetail(d);
    window.scrollTo({ top: 0 });
  }

  const set = (k: string) => (e: any) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? v : (isNaN(Number(v)) || v === "" ? v : Number(v)) }));
  };

  const m = detail?.results.metrics ?? {};

  return (
    <div className="space-y-4">
      <Card title="Nouveau backtest">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Symbole"><input className={inputCls} value={form.symbol} onChange={set("symbol")} /></Field>
          <Field label="Timeframe">
            <select className={inputCls} value={form.timeframe} onChange={set("timeframe")}>
              {["1h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Stratégie">
            <select className={inputCls} value={form.strategy} onChange={set("strategy")}>
              {strategies.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Capital"><input type="number" className={inputCls} value={form.initial_capital} onChange={set("initial_capital")} /></Field>
          <Field label="Frais / côté"><input type="number" step="0.0001" className={inputCls} value={form.fee_rate} onChange={set("fee_rate")} /></Field>
          <Field label="Slippage"><input type="number" step="0.0001" className={inputCls} value={form.slippage} onChange={set("slippage")} /></Field>
          <Field label="Stop-loss %"><input type="number" step="0.01" className={inputCls} value={form.stop_loss_pct} onChange={set("stop_loss_pct")} /></Field>
          <Field label="Take-profit %"><input type="number" step="0.01" className={inputCls} value={form.take_profit_pct} onChange={set("take_profit_pct")} /></Field>
        </div>
        {(() => {
          const s = strategies.find((x) => x.name === form.strategy);
          return s ? <EdgeBadge status={s.edge_status} note={s.edge_note} /> : null;
        })()}
        <div className="mt-3 flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-[#8b98ac]">
            <input type="checkbox" checked={form.allow_short} onChange={set("allow_short")} className="h-4 w-4" />
            Autoriser le short
          </label>
          <Btn onClick={run} disabled={running}>{running ? "Calcul…" : "Lancer le backtest"}</Btn>
        </div>
        {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      </Card>

      {detail && (
        <Card title={`Résultat — ${detail.strategy} · ${detail.symbol} ${detail.timeframe}`}>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Stat label="Capital final" value={fmtUSD(m.final_capital as number)} />
            <Stat label="Rendement" value={fmtPct(m.total_return as number)} tone={toneFor(m.total_return as number)} />
            <Stat label="Trades" value={String(m.n_trades)} sub={`win rate ${fmtRate(m.win_rate as number, 1)}`} />
            <Stat label="Profit factor" value={m.profit_factor === null ? "∞" : String(m.profit_factor)} tone={(m.profit_factor as number) >= 1 ? "pos" : "neg"} />
            <Stat label="Max drawdown" value={fmtPct(m.max_drawdown as number)} tone="warn" />
            <Stat label="Sharpe" value={String(m.sharpe_ratio)} />
            <Stat label="Frais totaux" value={fmtUSD(m.total_fees as number)} />
            <Stat label="Expectancy" value={fmtUSD(m.expectancy as number)} tone={toneFor(m.expectancy as number)} />
          </div>
          <div className="mt-4">
            <LineChart data={detail.results.equity_curve.map((p) => ({ ts: p.ts, value: p.equity }))} color="#00e676" formatY={(v) => "$" + v.toFixed(0)} />
          </div>
          <p className="mt-2 text-xs text-[#5b6b82]">Backtest ≠ garantie de performance future. Frais et slippage inclus.</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs uppercase text-[#5b6b82]">
                <th className="py-2 pr-3">Entrée</th><th className="pr-3">Sortie</th><th className="pr-3">Dir.</th>
                <th className="pr-3 text-right">PnL</th><th className="pr-3 text-right">PnL %</th><th>Raison</th>
              </tr></thead>
              <tbody>
                {detail.results.trades.slice(0, 50).map((t, i) => (
                  <tr key={i} className="border-t border-white/10">
                    <td className="py-1.5 pr-3 text-[#5b6b82]">{shortTs(t.entry_ts)} <span className="text-[#5b6b82]">@ {t.entry_price}</span></td>
                    <td className="pr-3 text-[#5b6b82]">{shortTs(t.exit_ts)} <span className="text-[#5b6b82]">@ {t.exit_price}</span></td>
                    <td className="pr-3"><SignalBadge signal={t.direction} /></td>
                    <td className={`pr-3 text-right font-semibold ${toneFor(t.pnl) === "pos" ? "text-[#00e676]" : toneFor(t.pnl) === "neg" ? "text-[#ff5252]" : ""}`}>{fmtUSD(t.pnl)}</td>
                    <td className="pr-3 text-right text-[#5b6b82]">{fmtPct(t.pnl_pct)}</td>
                    <td className="text-[#5b6b82]">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {detail.results.trades.length > 50 && <p className="mt-2 text-xs text-[#5b6b82]">… {detail.results.trades.length - 50} autres trades</p>}
          </div>
        </Card>
      )}

      <Card title="Historique">
        {history.length === 0 && <p className="text-sm text-[#5b6b82]">Aucun backtest enregistré.</p>}
        <div className="space-y-2">
          {history.map((b) => (
            <button key={b.id} onClick={() => open(b.id)} className="flex w-full items-center justify-between rounded-lg glass-deep px-3 py-2 text-sm hover:bg-white/5">
              <span className="text-[#5b6b82]">{shortTs(b.created_at)}</span>
              <span className="text-[#8b98ac]">{b.strategy} · {b.symbol} {b.timeframe}</span>
              <span className={`font-semibold ${toneFor(Number(b.total_return)) === "pos" ? "text-[#00e676]" : "text-[#ff5252]"}`}>{fmtPct(Number(b.total_return))}</span>
              <span className="text-[#5b6b82]">{b.n_trades} trades</span>
            </button>
          ))}
        </div>
      </Card>
    </div>
  );
}
