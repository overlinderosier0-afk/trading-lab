import { useEffect, useState } from "react";
import { api, fmtUSD, fmtPct, fmtRate, shortTs } from "../api";
import type { PaperAccount, Position, Trade } from "../api";
import { Card, Stat, Btn, SignalBadge, Loading, ErrorBox, toneFor } from "../components";

export default function Paper() {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [perf, setPerf] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [a, pos, tr, p] = await Promise.all([
        api.get<PaperAccount>("/api/paper/account"),
        api.get<{ positions: Position[] }>("/api/paper/positions"),
        api.get<{ trades: Trade[] }>("/api/paper/trades?limit=50"),
        api.get<any>("/api/paper/performance"),
      ]);
      setAccount(a); setPositions(pos.positions); setTrades(tr.trades); setPerf(p);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  async function toggle() {
    const enabling = !account?.paper_trading_enabled;
    if (!window.confirm(enabling
      ? "Activer le paper trading ? (virtuel uniquement — aucun argent réel)"
      : "Désactiver le paper trading ? (kill switch : plus aucune nouvelle position)")) return;
    setActing(true);
    try {
      await api.post(enabling ? "/api/paper/start" : "/api/paper/stop");
      await load();
    } catch (e: any) { setError(e.message); } finally { setActing(false); }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  const pnl = account ? account.equity - account.capital_initial : 0;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border border-amber-600/40 bg-amber-500/10 px-4 py-3 text-sm font-semibold text-amber-700">
        PAPER TRADING — AUCUN ARGENT RÉEL. Positions virtuelles uniquement.
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card><Stat label="Equity virtuelle" value={fmtUSD(account?.equity)} /></Card>
        <Card><Stat label="PnL total" value={fmtUSD(pnl)} sub={fmtPct(pnl / (account?.capital_initial || 1))} tone={toneFor(pnl)} /></Card>
        <Card><Stat label="Win rate" value={fmtRate(perf?.win_rate, 1)} sub={`${perf?.n_trades ?? 0} trades clôturés`} /></Card>
        <Card><Stat label="Drawdown" value={fmtPct(-(perf?.drawdown_from_peak ?? 0))} tone={(perf?.drawdown_from_peak ?? 0) > 0.05 ? "neg" : "neutral"} /></Card>
      </div>

      <Card title="Contrôle">
        <div className="flex items-center gap-4">
          <span className="text-sm text-slate-500">
            Statut : {account?.paper_trading_enabled
              ? <span className="font-semibold text-emerald-600">ACTIF</span>
              : <span className="font-semibold text-slate-500">DÉSACTIVÉ</span>}
          </span>
          <Btn onClick={toggle} kind={account?.paper_trading_enabled ? "danger" : "primary"} disabled={acting}>
            {account?.paper_trading_enabled ? "Désactiver (kill switch)" : "Activer le paper trading"}
          </Btn>
        </div>
      </Card>

      <Card title={`Positions ouvertes (${positions.length})`}>
        {positions.length === 0 && <p className="text-sm text-slate-500">Aucune position ouverte.</p>}
        {positions.length > 0 && (
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr className="text-left text-xs uppercase text-slate-500">
              <th className="py-2 pr-3">Paire</th><th className="pr-3">Dir.</th><th className="pr-3 text-right">Entrée</th>
              <th className="pr-3 text-right">Prix actuel</th><th className="pr-3 text-right">PnL latent</th><th className="pr-3 text-right">Stop</th>
            </tr></thead>
            <tbody>{positions.map((p, i) => (
              <tr key={i} className="border-t border-slate-900/10">
                <td className="py-1.5 pr-3 text-slate-600">{p.symbol} <span className="text-slate-500">{p.timeframe}</span></td>
                <td className="pr-3"><SignalBadge signal={p.direction} /></td>
                <td className="pr-3 text-right text-slate-500">{p.entry_price.toFixed(2)}</td>
                <td className="pr-3 text-right text-slate-600">{p.current_price?.toFixed(2) ?? "—"}</td>
                <td className={`pr-3 text-right font-semibold ${toneFor(p.unrealized_pnl) === "pos" ? "text-emerald-600" : toneFor(p.unrealized_pnl) === "neg" ? "text-rose-600" : ""}`}>
                  {fmtUSD(p.unrealized_pnl)} <span className="text-xs">({fmtPct(p.unrealized_pct)})</span>
                </td>
                <td className="pr-3 text-right text-slate-500">{p.stop_price?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>

      <Card title="Trades clôturés">
        {trades.length === 0 && <p className="text-sm text-slate-500">Aucun trade clôturé pour l'instant.</p>}
        {trades.length > 0 && (
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr className="text-left text-xs uppercase text-slate-500">
              <th className="py-2 pr-3">Sortie</th><th className="pr-3">Paire</th><th className="pr-3">Dir.</th>
              <th className="pr-3 text-right">PnL</th><th className="pr-3">Raison</th>
            </tr></thead>
            <tbody>{trades.map((t, i) => (
              <tr key={i} className="border-t border-slate-900/10">
                <td className="py-1.5 pr-3 text-slate-500">{shortTs(t.exit_ts)}</td>
                <td className="pr-3 text-slate-600">{t.symbol} <span className="text-slate-500">{t.timeframe}</span></td>
                <td className="pr-3"><SignalBadge signal={t.direction} /></td>
                <td className={`pr-3 text-right font-semibold ${toneFor(t.pnl) === "pos" ? "text-emerald-600" : "text-rose-600"}`}>{fmtUSD(t.pnl)}</td>
                <td className="text-slate-500">{t.exit_reason}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
