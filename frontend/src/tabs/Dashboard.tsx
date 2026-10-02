import { useEffect, useState } from "react";
import { api, fmtUSD, fmtPct, shortTs } from "../api";
import type { BacktestDetail, PaperAccount, SignalItem } from "../api";
import { Card, Stat, SignalBadge, LineChart, Loading, ErrorBox, toneFor } from "../components";

export default function Dashboard() {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [perf, setPerf] = useState<any>(null);
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [curve, setCurve] = useState<{ ts: string; value: number }[]>([]);
  const [btMeta, setBtMeta] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [acc, p, sig, bts] = await Promise.all([
        api.get<PaperAccount>("/api/paper/account"),
        api.get<any>("/api/paper/performance"),
        api.get<{ signals: SignalItem[] }>("/api/paper/signals?limit=8"),
        api.get<{ backtests: { id: string }[] }>("/api/backtests?limit=1"),
      ]);
      setAccount(acc);
      setPerf(p);
      setSignals(sig.signals);
      if (bts.backtests.length) {
        const d = await api.get<BacktestDetail>(`/api/backtests/${bts.backtests[0].id}`);
        setCurve(d.results.equity_curve.map((p) => ({ ts: p.ts, value: p.equity })));
        setBtMeta(`${d.strategy} · ${d.symbol} ${d.timeframe}`);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  const pnl = account ? account.equity - account.capital_initial : 0;
  const dd = perf?.drawdown_from_peak ?? 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card><Stat label="Capital virtuel" value={fmtUSD(account?.equity)} sub={`cash ${fmtUSD(account?.cash)}`} /></Card>
        <Card><Stat label="PnL total" value={fmtUSD(pnl)} sub={fmtPct(pnl / (account?.capital_initial || 1))} tone={toneFor(pnl)} /></Card>
        <Card><Stat label="Drawdown" value={fmtPct(-dd)} tone={dd > 0.05 ? "neg" : "neutral"} sub="depuis le pic" /></Card>
        <Card><Stat label="Positions ouvertes" value={String(account?.open_positions ?? 0)} sub={account?.paper_trading_enabled ? "paper trading actif" : "paper trading désactivé"} /></Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Courbe du capital — dernier backtest${btMeta ? ` (${btMeta})` : ""}`}>
          {curve.length
            ? <LineChart data={curve} color="#60a5fa" formatY={(v) => "$" + v.toFixed(0)} />
            : <p className="text-sm text-slate-500">Aucun backtest pour l'instant — onglet Backtests.</p>}
          <p className="mt-2 text-xs text-slate-500">Backtest ≠ garantie de performance future.</p>
        </Card>
        <Card title="Derniers signaux (paper)">
          {signals.length === 0 && <p className="text-sm text-slate-500">Aucun signal — le scheduler génère les signaux toutes les 15 min.</p>}
          <div className="space-y-2">
            {signals.map((s, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-2 text-sm">
                <span className="text-slate-400">{shortTs(s.ts)}</span>
                <span className="text-slate-300">{s.symbol} <span className="text-slate-500">{s.timeframe}</span></span>
                <SignalBadge signal={s.signal} />
                <span className="text-slate-400">${s.price.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
