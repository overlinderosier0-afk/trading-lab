import { useEffect, useState } from "react";
import { api, shortTs } from "../api";
import { Card, ErrorBox, Field, inputCls, Loading, SignalBadge } from "../components";

// ---------------------------------------------------------------- types
interface StatsBlock {
  total_signals: number; completed_signals: number; pending_signals: number;
  unavailable_signals: number;
  n?: number; sample_quality: string;
  wins: number; losses: number; flats: number;
  win_rate: number | null; loss_rate: number | null; flat_rate: number | null;
  average_return: number | null; average_return_net: number | null;
  median_return: number | null; median_return_net: number | null;
  total_return: number | null; total_return_net: number | null;
  profit_factor: number | null; profit_factor_net: number | null;
  average_mfe: number | null; average_mae: number | null;
  paper_pnl: { total_usdt: number | null; average_usdt: number | null;
               stake_usdt: number; simulated: boolean };
  mixed_timeframes: boolean; timeframes_included: string[];
  disclaimer: string;
}

interface SummaryResp { horizons: Record<string, StatsBlock>; disclaimer: string }
interface BreakdownResp {
  group_by: string; horizon?: number;
  groups: Record<string, StatsBlock>; disclaimer: string;
}
interface EvalCell {
  horizon_minutes: number; status: string; entry_price: number;
  exit_price: number | null; exit_timestamp: string | null;
  return_pct: number | null; direction_correct: boolean | null;
  result: string | null;
  mfe_pct: number | null; mae_pct: number | null;
  evaluated_at: string | null; created_at: string;
}
interface SignalRow {
  id: string; created_at: string; symbol: string; timeframe: string;
  direction: string; score: number; entry_price: number;
  entry_timestamp: string | null; entry_price_source: string | null;
  origin: string; evaluation_state: string;
  evaluations: Record<string, EvalCell>;
}
interface SignalsResp {
  items: SignalRow[]; page: number; page_size: number; total: number;
  disclaimer: string;
}

const HORIZONS = ["5", "15", "30", "60"];
const SCORE_ORDER = ["0-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90-100"];

// Les returns de l'API sont déjà en % (pas de ×100 comme fmtPct).
function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)} %`;
}
function rate(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)} %`;
}
function usd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(digits)}`;
}

function QualityBadge({ q }: { q: string }) {
  const color = q === "Insufficient sample" ? "text-[#5b6b82] border-white/10 bg-white/5"
    : q === "Early evidence" ? "text-[#ffb300] border-[#ffb300]/30 bg-[#ffb300]/10"
    : "text-[#00e676] border-[#00e676]/30 bg-[#00e676]/10";
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-semibold ${color}`}>
      {q}
    </span>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="glass-deep px-3 py-2">
      <div className="text-[11px] uppercase tracking-widest text-[#5b6b82]">{label}</div>
      <div className="font-display mt-1 text-xl font-bold tabular-nums text-[#c9d4e3]">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-[#5b6b82]">{sub}</div>}
    </div>
  );
}

// Barres SVG maison (pas de lib de graphiques) : valeur par tranche de score.
function ScoreBars({ groups, valueKey, fmt }: {
  groups: Record<string, StatsBlock>; valueKey: "average_return" | "win_rate";
  fmt: (v: number | null) => string;
}) {
  const vals = SCORE_ORDER.map((k) => groups[k]?.[valueKey] ?? null);
  const nums = vals.filter((v): v is number => v !== null);
  const maxAbs = Math.max(0.001, ...nums.map((v) => Math.abs(v)));
  const W = 640, H = 180, padL = 8, padB = 34, padT = 10;
  const bw = (W - padL * 2) / SCORE_ORDER.length;
  const zeroY = padT + (H - padT - padB) / 2;
  const scale = (H - padT - padB) / 2 / maxAbs;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      <line x1={padL} y1={zeroY} x2={W - padL} y2={zeroY} stroke="#1c2634" />
      {SCORE_ORDER.map((k, i) => {
        const v = vals[i];
        const n = groups[k]?.completed_signals ?? 0;
        const x = padL + i * bw + bw * 0.18;
        const w = bw * 0.64;
        if (v === null) {
          return (
            <g key={k}>
              <rect x={x} y={padT} width={w} height={H - padT - padB}
                    fill="#1c2634" opacity={0.35} />
              <text x={x + w / 2} y={H - 18} textAnchor="middle" fontSize={10} fill="#5b6b82">{k}</text>
              <text x={x + w / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="#5b6b82">n=0</text>
            </g>
          );
        }
        const h = Math.abs(v) * scale;
        const y = v >= 0 ? zeroY - h : zeroY;
        const col = valueKey === "average_return"
          ? (v >= 0 ? "#00e676" : "#ff5252") : "#00e676";
        return (
          <g key={k}>
            <rect x={x} y={y} width={w} height={Math.max(h, 2)} fill={col} opacity={0.85} />
            <text x={x + w / 2} y={y - 4 < padT ? padT + 8 : y - 4} textAnchor="middle"
                  fontSize={10} fill="#c9d4e3">{fmt(v)}</text>
            <text x={x + w / 2} y={H - 18} textAnchor="middle" fontSize={10} fill="#5b6b82">{k}</text>
            <text x={x + w / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="#5b6b82">n={n}</text>
          </g>
        );
      })}
    </svg>
  );
}

function EvalStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    COMPLETED: "bg-[#00e676]/10 text-[#00e676] border-[#00e676]/30",
    PENDING: "bg-[#ffb300]/10 text-[#ffb300] border-[#ffb300]/30",
    ERROR: "bg-[#ff5252]/10 text-[#ff5252] border-[#ff5252]/30",
  };
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-semibold ${map[status] ?? "bg-white/5 text-[#5b6b82] border-white/10"}`}>
      {status === "COMPLETED" ? "OK" : status === "PENDING" ? "…" : status}
    </span>
  );
}

function ResultBadge({ result }: { result: string | null }) {
  if (result === "WIN")
    return <span className="font-bold text-[#00e676]">WIN</span>;
  if (result === "LOSS")
    return <span className="font-bold text-[#ff5252]">LOSS</span>;
  if (result === "FLAT")
    return <span className="font-bold text-[#5b6b82]">FLAT</span>;
  return <span className="text-[#5b6b82]">—</span>;
}

// ---------------------------------------------------------------- onglet
export default function SignalLabEval() {
  const [horizon, setHorizon] = useState("15");
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState("");
  const [origin, setOrigin] = useState("");
  const [summary, setSummary] = useState<SummaryResp | null>(null);
  const [byHorizon, setByHorizon] = useState<BreakdownResp | null>(null);
  const [byScore, setByScore] = useState<BreakdownResp | null>(null);
  const [signals, setSignals] = useState<SignalsResp | null>(null);
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<SignalRow & { evaluations: EvalCell[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  function qs(extra: Record<string, string> = {}) {
    const p = new URLSearchParams();
    if (symbol) p.set("symbol", symbol);
    if (direction) p.set("direction", direction);
    if (origin) p.set("origin", origin);
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    const s = p.toString();
    return s ? `?${s}` : "";
  }

  async function load(p = page, h = horizon) {
    setLoading(true); setError("");
    try {
      const [s, bh, bs, sg] = await Promise.all([
        api.get<SummaryResp>(`/api/signal-lab/eval/summary${qs({ horizon: h })}`),
        api.get<BreakdownResp>(`/api/signal-lab/eval/breakdown${qs({ group_by: "horizon" })}`),
        api.get<BreakdownResp>(`/api/signal-lab/eval/breakdown${qs({ group_by: "score_range", horizon: h })}`),
        api.get<SignalsResp>(`/api/signal-lab/eval/signals${qs({ page: String(p), page_size: "20" })}`),
      ]);
      setSummary(s); setByHorizon(bh); setByScore(bs); setSignals(sg);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  }

  useEffect(() => { load(1, horizon); setPage(1); }, [horizon, symbol, direction, origin]);
  useEffect(() => { load(page, horizon); }, [page]);

  async function openDetail(id: string) {
    try {
      const d = await api.get<SignalRow & { evaluations: EvalCell[] }>(
        `/api/signal-lab/eval/signals/${id}`);
      setDetail(d);
    } catch (e: any) { setError(e.message); }
  }

  const block = summary?.horizons[horizon];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Horizon">
            <select className={inputCls} value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              {HORIZONS.map((h) => <option key={h} value={h}>{h} min</option>)}
            </select>
          </Field>
          <Field label="Symbole">
            <select className={inputCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">Tous</option>
              <option value="BTCUSDT">BTCUSDT</option>
              <option value="ETHUSDT">ETHUSDT</option>
            </select>
          </Field>
          <Field label="Direction">
            <select className={inputCls} value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="">Toutes</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </Field>
          <Field label="Origine">
            <select className={inputCls} value={origin} onChange={(e) => setOrigin(e.target.value)}>
              <option value="">Toutes</option>
              <option value="auto">Auto</option>
              <option value="manual">Manuel</option>
            </select>
          </Field>
          <div className="ml-auto max-w-md text-xs text-[#5b6b82]">
            Méthode : <strong className="text-[#c9d4e3]">évaluation observationnelle</strong> —
            return mesuré sur bougies 1m à horizon fixe, sans SL/TP.
            Distincte de la <strong className="text-[#c9d4e3]">résolution 3 bougies (SL/TP ATR)</strong>
            affichée dans l'onglet Signaux.
          </div>
        </div>
      </Card>

      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={() => load()} /> : block && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Metric label={`Signaux (horizon ${horizon}m)`} value={String(block.completed_signals)}
                    sub={<><QualityBadge q={block.sample_quality} /> <span className="ml-1">{block.pending_signals} en attente{block.unavailable_signals > 0 && `, ${block.unavailable_signals} N/A`}</span></>} />
            <Metric label="Win rate" value={rate(block.win_rate)}
                    sub={`${block.wins}W / ${block.losses}L / ${block.flats}F`} />
            <Metric label="Avg return (brut / net)" value={pct(block.average_return)}
                    sub={`net ${pct(block.average_return_net)} (frais ${20} bps)`} />
            <Metric label="Profit factor" value={block.profit_factor === null ? "—" : block.profit_factor.toFixed(2)}
                    sub={`net ${block.profit_factor_net === null ? "—" : block.profit_factor_net.toFixed(2)}`} />
            <Metric label="Median return" value={pct(block.median_return)}
                    sub={`total ${pct(block.total_return)}`} />
            <Metric label="Avg MFE / MAE" value={`${pct(block.average_mfe)} / ${pct(block.average_mae)}`} />
            <Metric label="Paper P&L" value={usd(block.paper_pnl.total_usdt)}
                    sub={<span className="rounded border border-[#ffb300]/30 bg-[#ffb300]/10 px-1 text-[#ffb300]">SIMULATED</span>} />
            <Metric label="Timeframes" value={block.timeframes_included.join(", ") || "—"}
                    sub={block.mixed_timeframes ? "mixte : interpréter avec prudence" : "homogène"} />
          </div>

          <Card title="Performance par horizon">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs uppercase text-[#5b6b82]">
                  <th className="py-2 pr-3">Horizon</th><th className="pr-3 text-right">Signaux</th>
                  <th className="pr-3 text-right">Win rate</th><th className="pr-3 text-right">Avg return</th>
                  <th className="pr-3 text-right">Median</th><th className="text-right">Profit factor</th>
                </tr></thead>
                <tbody>
                  {HORIZONS.map((h) => {
                    const b = byHorizon?.groups[h];
                    return (
                      <tr key={h} className={`border-t border-white/10 ${h === horizon ? "bg-white/5" : ""}`}>
                        <td className="py-2 pr-3 font-bold tabular-nums">{h} min</td>
                        <td className="pr-3 text-right tabular-nums">{b?.completed_signals ?? "—"}</td>
                        <td className="pr-3 text-right tabular-nums">{rate(b?.win_rate)}</td>
                        <td className="pr-3 text-right tabular-nums">{pct(b?.average_return)}</td>
                        <td className="pr-3 text-right tabular-nums">{pct(b?.median_return)}</td>
                        <td className="text-right tabular-nums">{b?.profit_factor == null ? "—" : b.profit_factor.toFixed(2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`Score → return moyen (${horizon} min)`}>
              {byScore ? <ScoreBars groups={byScore.groups} valueKey="average_return" fmt={(v) => pct(v)} />
                : <div className="text-sm text-[#5b6b82]">—</div>}
              <div className="mt-2 text-xs text-[#5b6b82]">
                Un score élevé correspond-il à une meilleure performance ? Les barres grisées = tranche vide (n=0).
              </div>
            </Card>
            <Card title={`Score → win rate (${horizon} min)`}>
              {byScore ? <ScoreBars groups={byScore.groups} valueKey="win_rate" fmt={(v) => rate(v)} />
                : <div className="text-sm text-[#5b6b82]">—</div>}
            </Card>
          </div>

          <Card title="Historique des signaux">
            {signals && signals.items.length > 0 ? (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[800px] text-sm">
                    <thead><tr className="text-left text-xs uppercase text-[#5b6b82]">
                      <th className="py-2 pr-3">Heure</th><th className="pr-3">Symbole</th>
                      <th className="pr-3">Dir</th><th className="pr-3 text-right">Score</th>
                      <th className="pr-3 text-right">Entrée</th>
                      {HORIZONS.map((h) => <th key={h} className="pr-3 text-right">{h}m</th>)}
                      <th className="pr-3 text-right">Résultat</th>
                      <th className="text-right">Statut</th>
                    </tr></thead>
                    <tbody>
                      {signals.items.map((s) => (
                        <tr key={s.id} className="cursor-pointer border-t border-white/10 hover:bg-white/5"
                            onClick={() => openDetail(s.id)}>
                          <td className="py-2 pr-3 tabular-nums">{shortTs(s.created_at)}</td>
                          <td className="pr-3">{s.symbol}</td>
                          <td className="pr-3"><SignalBadge signal={s.direction} /></td>
                          <td className="pr-3 text-right font-bold tabular-nums">{s.score}</td>
                          <td className="pr-3 text-right tabular-nums">{s.entry_price}</td>
                          {HORIZONS.map((h) => {
                            const e = s.evaluations[h];
                            return (
                              <td key={h} className="pr-3 text-right tabular-nums">
                                {!e ? <span className="text-[#5b6b82]">—</span>
                                  : e.status === "COMPLETED" ? pct(e.return_pct)
                                  : e.status === "ERROR" ? <span className="text-[#ff5252]">ERR</span>
                                  : s.evaluation_state === "unavailable" ? <span className="text-[#5b6b82]">N/A</span>
                                  : <span className="text-[#ffb300]">…</span>}
                              </td>
                            );
                          })}
                          <td className="pr-3 text-right">
                            {(() => {
                              const e = s.evaluations[horizon];
                              if (!e) return <span className="text-[#5b6b82]">—</span>;
                              if (e.status === "COMPLETED") return <ResultBadge result={e.result} />;
                              if (e.status === "ERROR") return <span className="text-[#ff5252]">ERR</span>;
                              if (s.evaluation_state === "unavailable") return <span className="text-[#5b6b82]">N/A</span>;
                              return <span className="text-[#ffb300]">…</span>;
                            })()}
                          </td>
                          <td className="text-right">
                            {s.evaluation_state === "unavailable"
                              ? <span className="text-xs text-[#5b6b82]">N/A</span>
                              : <EvalStatusBadge status={
                                  HORIZONS.every((h) => s.evaluations[h]?.status === "COMPLETED") ? "COMPLETED"
                                  : HORIZONS.some((h) => s.evaluations[h]?.status === "ERROR") ? "ERROR"
                                  : "PENDING"} />}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 flex items-center justify-between text-sm text-[#5b6b82]">
                  <span>{signals.total} signaux</span>
                  <div className="flex gap-2">
                    <button className="btn-glass btn-glass-ghost" disabled={page <= 1}
                            onClick={() => setPage(page - 1)}>←</button>
                    <span className="px-2 py-2 tabular-nums">{page}</span>
                    <button className="btn-glass btn-glass-ghost"
                            disabled={page * signals.page_size >= signals.total}
                            onClick={() => setPage(page + 1)}>→</button>
                  </div>
                </div>
              </>
            ) : <div className="text-sm text-[#5b6b82]">Aucun signal pour ces filtres.</div>}
          </Card>

          {detail && (
            <Card title="Détail du signal">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <span className="font-bold">{detail.symbol}</span>
                <SignalBadge signal={detail.direction} />
                <span className="font-bold tabular-nums">{detail.score}/100</span>
                <span className="text-sm text-[#5b6b82]">
                  Entrée {detail.entry_price}
                  {detail.entry_timestamp && ` · ${shortTs(detail.entry_timestamp)}`}
                  {detail.entry_price_source && ` (${detail.entry_price_source})`}
                </span>
                <button className="btn-glass btn-glass-ghost ml-auto text-xs"
                        onClick={() => setDetail(null)}>Fermer</button>
              </div>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs uppercase text-[#5b6b82]">
                  <th className="py-2 pr-3">Horizon</th><th className="pr-3 text-right">Sortie</th>
                  <th className="pr-3 text-right">Return</th><th className="pr-3 text-right">MFE</th>
                  <th className="pr-3 text-right">MAE</th><th className="text-right">Résultat</th>
                </tr></thead>
                <tbody>
                  {detail.evaluations.map((e) => (
                    <tr key={e.horizon_minutes} className="border-t border-white/10">
                      <td className="py-2 pr-3 font-bold tabular-nums">{e.horizon_minutes} min</td>
                      <td className="pr-3 text-right tabular-nums">{e.exit_price ?? "—"}</td>
                      <td className="pr-3 text-right tabular-nums">{pct(e.return_pct)}</td>
                      <td className="pr-3 text-right tabular-nums">{pct(e.mfe_pct)}</td>
                      <td className="pr-3 text-right tabular-nums">{pct(e.mae_pct)}</td>
                      <td className="text-right">
                        {e.status === "COMPLETED" ? <ResultBadge result={e.result} />
                          : e.status === "ERROR" ? <span className="text-[#ff5252]">ERR</span>
                          : <span className="text-[#ffb300]">En attente</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          <div className="rounded border border-white/10 bg-white/5 px-4 py-3 text-xs text-[#5b6b82]">
            {block.disclaimer} Le P&amp;L affiché est <strong className="text-[#ffb300]">SIMULATED</strong> :
            aucune transaction réelle n'a eu lieu.
          </div>
        </>
      )}
    </div>
  );
}
