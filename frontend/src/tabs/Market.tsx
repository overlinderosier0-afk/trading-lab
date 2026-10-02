import { useEffect, useState } from "react";
import { api, shortTs } from "../api";
import { Card, Field, inputCls, LineChart, Loading, ErrorBox } from "../components";

interface Candle { ts: string; open: number; high: number; low: number; close: number; volume: number }

export default function Market() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load(sym = symbol, tf = timeframe) {
    setLoading(true);
    setError("");
    try {
      const [c, s] = await Promise.all([
        api.get<{ candles: Candle[] }>(`/api/market/candles?symbol=${sym}&timeframe=${tf}&limit=200`),
        api.get<{ pairs: any[] }>("/api/market/status"),
      ]);
      setCandles(c.candles);
      setStatus(s.pairs);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  const change = (s: string, t: string) => { setSymbol(s); setTimeframe(t); load(s, t); };
  const last = candles[candles.length - 1];
  const first = candles[0];
  const chg = last && first ? (last.close - first.close) / first.close : 0;

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Symbole">
            <select className={inputCls} value={symbol} onChange={(e) => change(e.target.value, timeframe)}>
              {["BTCUSDT", "ETHUSDT"].map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Timeframe">
            <select className={inputCls} value={timeframe} onChange={(e) => change(symbol, e.target.value)}>
              {["1h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          {last && (
            <div className="ml-auto text-right">
              <div className="text-2xl font-bold">${last.close.toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
              <div className={`text-sm ${chg >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {(chg >= 0 ? "+" : "") + (chg * 100).toFixed(2)} % sur la période
              </div>
            </div>
          )}
        </div>
      </Card>

      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={() => load()} /> : (
        <Card title={`${symbol} ${timeframe} — clôtures (200 dernières)`}>
          <LineChart data={candles.map((c) => ({ ts: c.ts, value: c.close }))} color="#60a5fa" formatY={(v) => "$" + v.toFixed(0)} />
        </Card>
      )}

      <Card title="Couverture des données">
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead><tr className="text-left text-xs uppercase text-slate-500">
            <th className="py-2 pr-3">Paire</th><th className="pr-3 text-right">Bougies</th>
            <th className="pr-3">Première</th><th className="pr-3">Dernière</th><th className="text-right">Trous</th>
          </tr></thead>
          <tbody>{status.map((p, i) => (
            <tr key={i} className="border-t border-white/10">
              <td className="py-1.5 pr-3 text-slate-300">{p.symbol} <span className="text-slate-500">{p.timeframe}</span></td>
              <td className="pr-3 text-right text-slate-400">{p.candles}</td>
              <td className="pr-3 text-slate-500">{shortTs(p.first_ts)}</td>
              <td className="pr-3 text-slate-500">{shortTs(p.last_ts)}</td>
              <td className={`text-right font-semibold ${p.gaps ? "text-amber-400" : "text-emerald-400"}`}>{p.gaps}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
    </div>
  );
}
