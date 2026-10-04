import { useEffect, useState } from "react";
import { api, shortTs } from "../api";
import { Btn, Card, ErrorBox, Field, inputCls, Loading, SignalBadge } from "../components";

interface Factor { label: string; weight: number; points: number }

interface SignalResult {
  direction: "BUY" | "SELL" | "NEUTRAL";
  score: number;
  total: number;
  factors: Record<string, Factor>;
  justification: string[];
  entry: number;
  stop_loss: number | null;
  take_profit: number | null;
  atr: number;
  indicators: { ema20: number; ema50: number; ema200: number; rsi14: number; macd_hist: number; atr_pct: number };
}

interface GenerateResponse {
  symbol: string; timeframe: string; data_last_ts: string; candles_used: number;
  signal: SignalResult;
  stored: { id: string; created_at: string; resolve_at: string } | null;
  duplicate: boolean;
  disclaimer: string;
}

interface LabSignal {
  id: string; created_at: string; symbol: string; timeframe: string;
  direction: string; score: number; total: number;
  entry_price: number; stop_loss: number; take_profit: number;
  horizon_minutes: number; resolve_at: string; outcome: string;
  exit_price: number | null; sl_hit: boolean | null; tp_hit: boolean | null;
  origin: string; candle_ts: string | null;
}

interface StatsBucket { range: string; n: number; wins: number; win_rate: number | null }
interface Stats {
  buckets: StatsBucket[];
  total: { n: number; wins: number; win_rate: number | null };
  pending: number; note: string;
}

const FACTOR_ORDER = ["trend", "momentum", "price_action", "structure", "volatility"];

function OriginBadge({ origin }: { origin: string }) {
  const isAuto = origin === "auto";
  return (
    <span className={`inline-block rounded-lg border px-2 py-0.5 text-xs font-semibold ${
      isAuto
        ? "bg-sky-600/10 text-[#8b98ac] border-sky-600/30"
        : "bg-white/5 text-[#5b6b82] border-white/10"
    }`}>
      {isAuto ? "Auto" : "Manuel"}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const map: Record<string, string> = {
    win: "bg-[#00e676]/10 text-[#00e676] border-[#00e676]/30",
    loss: "bg-[#ff5252]/10 text-[#ff5252] border-[#ff5252]/30",
    pending: "bg-[#ffb300]/10 text-[#ffb300] border-amber-500/30",
    expired: "bg-white/5 text-[#5b6b82] border-white/10",
  };
  const label: Record<string, string> = {
    win: "✓ WIN", loss: "✗ LOSS", pending: "⏳ En cours", expired: "Expiré",
  };
  return (
    <span className={`inline-block rounded-lg border px-2 py-0.5 text-xs font-semibold ${map[outcome] ?? map.expired}`}>
      {label[outcome] ?? outcome}
    </span>
  );
}

function FactorBar({ factor }: { factor: Factor }) {
  // Barre divergente centrée : -poids … +poids
  const pct = (factor.points / factor.weight) * 50; // -50 … +50
  const left = pct < 0 ? 50 + pct : 50;
  const width = Math.abs(pct);
  const color = factor.points >= 0 ? "bg-[#00e676]/70" : "bg-[#ff5252]/70";
  return (
    <div className="flex items-center gap-3">
      <div className="w-28 shrink-0 text-sm text-[#8b98ac]">{factor.label}</div>
      <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-white/5">
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-white/20" />
        <div className={`absolute top-0 bottom-0 rounded-full ${color}`} style={{ left: `${left}%`, width: `${width}%` }} />
      </div>
      <div className="w-24 shrink-0 text-right text-sm font-semibold tabular-nums">
        <span className={factor.points >= 0 ? "text-[#00e676]" : "text-[#ff5252]"}>
          {factor.points >= 0 ? "+" : ""}{factor.points.toFixed(1)}
        </span>
        <span className="text-[#5b6b82]"> / {factor.weight}</span>
      </div>
    </div>
  );
}

export default function SignalLab() {
  const [universe, setUniverse] = useState<{ symbols: string[]; timeframes: string[] }>({ symbols: [], timeframes: [] });
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [gen, setGen] = useState<GenerateResponse | null>(null);
  const [history, setHistory] = useState<LabSignal[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  async function loadAll(sym = symbol, tf = timeframe) {
    setLoading(true); setError("");
    try {
      const u = await api.get<{ symbols: string[]; timeframes: string[] }>("/api/signal-lab/universe");
      setUniverse({ symbols: u.symbols, timeframes: u.timeframes });
      const s = sym || u.symbols[0] || "BTCUSDT";
      const t = tf || u.timeframes[0] || "1h";
      setSymbol(s); setTimeframe(t);
      const [h, st] = await Promise.all([
        api.get<{ signals: LabSignal[] }>(`/api/signal-lab/signals?symbol=${s}&timeframe=${t}&limit=30`),
        api.get<Stats>(`/api/signal-lab/stats?symbol=${s}&timeframe=${t}`),
      ]);
      setHistory(h.signals); setStats(st);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  }

  useEffect(() => { loadAll(); }, []);

  async function generate() {
    setGenerating(true); setError("");
    try {
      const r = await api.post<GenerateResponse>("/api/signal-lab/generate", { symbol, timeframe });
      setGen(r);
      const [h, st] = await Promise.all([
        api.get<{ signals: LabSignal[] }>(`/api/signal-lab/signals?symbol=${symbol}&timeframe=${timeframe}&limit=30`),
        api.get<Stats>(`/api/signal-lab/stats?symbol=${symbol}&timeframe=${timeframe}`),
      ]);
      setHistory(h.signals); setStats(st);
    } catch (e: any) { setError(e.message); } finally { setGenerating(false); }
  }

  const change = (s: string, t: string) => { setGen(null); loadAll(s, t); };
  const sig = gen?.signal;

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Marché">
            <select className={inputCls} value={symbol} onChange={(e) => change(e.target.value, timeframe)}>
              {universe.symbols.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Timeframe">
            <select className={inputCls} value={timeframe} onChange={(e) => change(symbol, e.target.value)}>
              {universe.timeframes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Btn onClick={generate} disabled={generating}>
            {generating ? "Analyse en cours…" : "Générer un signal"}
          </Btn>
          <div className="ml-auto max-w-xs text-xs text-[#5b6b82]">
            Score /100 = force du modèle, <strong>pas</strong> une probabilité de gain.
            Analyse uniquement — aucune exécution.
          </div>
        </div>
        {gen?.duplicate && (
          <div className="mt-3 rounded-xl bg-[#ffb300]/10 px-3 py-2 text-xs text-[#ffb300]">
            Cette bougie a déjà été scorée — aucun doublon enregistré. Le signal affiché est identique au précédent.
          </div>
        )}
      </Card>

      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={() => loadAll()} /> : (
        <>
          {sig && (
            <div className="grid gap-4 lg:grid-cols-5">
              <Card className="lg:col-span-2">
                <div className="flex items-center justify-between">
                  <div className="text-sm text-[#5b6b82]">{gen.symbol} · {gen.timeframe}</div>
                  <SignalBadge signal={sig.direction} />
                </div>
                <div className="mt-3 text-center">
                  <div className={`font-display text-6xl font-bold tabular-nums ${
                    sig.direction === "BUY" ? "text-[#00e676]" : sig.direction === "SELL" ? "text-[#ff5252]" : "text-[#5b6b82]"
                  }`}>
                    {sig.direction === "NEUTRAL" ? "—" : `${sig.score}/100`}
                  </div>
                  <div className="mt-1 text-xs uppercase tracking-widest text-[#5b6b82]">score du modèle</div>
                  {sig.direction === "NEUTRAL" && (
                    <div className="mt-2 text-sm text-[#8b98ac]">
                      Pas de conviction suffisante — aucun signal enregistré. Un bon modèle sait dire « je ne sais pas ».
                    </div>
                  )}
                </div>
                {sig.direction !== "NEUTRAL" && (
                  <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                    <div><div className="text-xs uppercase text-[#5b6b82]">Entrée</div><div className="font-bold tabular-nums">{sig.entry}</div></div>
                    <div><div className="text-xs uppercase text-[#5b6b82]">Stop</div><div className="font-bold tabular-nums text-[#ff5252]">{sig.stop_loss}</div></div>
                    <div><div className="text-xs uppercase text-[#5b6b82]">Cible</div><div className="font-bold tabular-nums text-[#00e676]">{sig.take_profit}</div></div>
                  </div>
                )}
                <div className="mt-3 grid grid-cols-3 gap-2 text-center text-sm">
                  <div><div className="text-xs uppercase text-[#5b6b82]">EMA 20/50/200</div><div className="tabular-nums">{sig.indicators.ema20} / {sig.indicators.ema50} / {sig.indicators.ema200}</div></div>
                  <div><div className="text-xs uppercase text-[#5b6b82]">RSI 14</div><div className="font-bold tabular-nums">{sig.indicators.rsi14}</div></div>
                  <div><div className="text-xs uppercase text-[#5b6b82]">ATR</div><div className="tabular-nums">{sig.atr} ({sig.indicators.atr_pct} %)</div></div>
                </div>
                {gen.stored && (
                  <div className="mt-3 text-xs text-[#5b6b82]">
                    Signal #{gen.stored.id.slice(0, 8)} enregistré — verdict après horizon
                    (résolution auto le {shortTs(gen.stored.resolve_at)}).
                  </div>
                )}
              </Card>

              <Card title="Décomposition du score" className="lg:col-span-3">
                <div className="space-y-3">
                  {FACTOR_ORDER.map((k) => sig.factors[k] && <FactorBar key={k} factor={sig.factors[k]} />)}
                </div>
                <div className="mt-4 border-t border-white/10 pt-3">
                  <div className="mb-2 text-xs uppercase tracking-widest text-[#5b6b82]">Justification</div>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-[#c9d4e3]">
                    {sig.justification.map((j, i) => <li key={i}>{j}</li>)}
                  </ul>
                </div>
              </Card>
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Calibration — taux de réussite observé">
              {!stats ? <Loading label="Calcul…" /> : stats.total.n === 0 ? (
                <div className="text-sm text-[#5b6b82]">
                  Aucun signal résolu pour l'instant. Génère des signaux : chacun est résolu
                  automatiquement après son horizon (3 bougies), et c'est ici que le taux
                  réel apparaîtra — par tranche de score.
                </div>
              ) : (
                <>
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-xs uppercase text-[#5b6b82]">
                      <th className="py-2 pr-3">Score</th><th className="pr-3 text-right">Signaux</th>
                      <th className="pr-3 text-right">Gagnants</th><th className="text-right">Taux observé</th>
                    </tr></thead>
                    <tbody>
                      {stats.buckets.map((b) => (
                        <tr key={b.range} className="border-t border-white/10">
                          <td className="py-2 pr-3 font-semibold tabular-nums">{b.range}</td>
                          <td className="pr-3 text-right tabular-nums">{b.n}</td>
                          <td className="pr-3 text-right tabular-nums">{b.wins}</td>
                          <td className="text-right font-bold tabular-nums">
                            {b.win_rate === null ? <span className="font-normal text-[#5b6b82]">—</span> : `${(b.win_rate * 100).toFixed(1)} %`}
                          </td>
                        </tr>
                      ))}
                      <tr className="border-t-2 border-white/10 font-bold">
                        <td className="py-2 pr-3">Total</td>
                        <td className="pr-3 text-right tabular-nums">{stats.total.n}</td>
                        <td className="pr-3 text-right tabular-nums">{stats.total.wins}</td>
                        <td className="text-right tabular-nums">
                          {stats.total.win_rate === null ? "—" : `${(stats.total.win_rate * 100).toFixed(1)} %`}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div className="mt-3 text-xs text-[#5b6b82]">{stats.note}</div>
                  {stats.pending > 0 && (
                    <div className="mt-1 text-xs text-[#ffb300]">{stats.pending} signal(aux) en attente de résolution.</div>
                  )}
                </>
              )}
            </Card>

            <Card title="Historique des signaux">
              {history.length === 0 ? (
                <div className="text-sm text-[#5b6b82]">Aucun signal généré pour {symbol} {timeframe}.</div>
              ) : (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {history.map((s) => (
                    <div key={s.id} className="flex items-center gap-3 rounded-xl bg-white/5 px-3 py-2 text-sm">
                      <SignalBadge signal={s.direction} />
                      <div className="font-bold tabular-nums">{s.score}<span className="font-normal text-[#5b6b82]">/100</span></div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs text-[#5b6b82]">
                          {shortTs(s.created_at)} → {shortTs(s.resolve_at)}
                        </div>
                        <div className="text-xs tabular-nums text-[#8b98ac]">
                          entrée {s.entry_price}
                          {s.exit_price !== null && <> → sortie {s.exit_price}</>}
                        </div>
                      </div>
                      <OriginBadge origin={s.origin} />
                      <OutcomeBadge outcome={s.outcome} />
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
