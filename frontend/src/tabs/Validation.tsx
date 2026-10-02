import { useEffect, useState } from "react";
import { api, fmtPct, fmtRate, shortTs } from "../api";
import type { StrategyInfo } from "../api";
import { Card, Stat, Btn, Field, inputCls, Loading, ErrorBox, toneFor, EdgeBadge } from "../components";

const DEFAULT_GRID = { ema_fast: "10, 20, 30", ema_slow: "40, 50, 60", rsi_buy: "25, 30", rsi_sell: "65, 70, 75" };

function parseList(s: string): number[] {
  return s.split(",").map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
}

function verdictTone(v: string): "pos" | "neg" | "neutral" {
  if (v === "robuste") return "pos";
  if (v === "dégradé") return "neutral";
  return "neg";
}

function MetricRow({ label, is, oos, fmt }: { label: string; is: any; oos: any; fmt: (v: any) => string }) {
  return (
    <tr className="border-t border-white/10">
      <td className="py-1.5 pr-2 text-slate-400">{label}</td>
      <td className="py-1.5 pr-2 text-right font-mono">{fmt(is)}</td>
      <td className="py-1.5 text-right font-mono">{fmt(oos)}</td>
    </tr>
  );
}

export default function Validation() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [strategy, setStrategy] = useState("ema_rsi_demo");
  const [isRatio, setIsRatio] = useState(0.7);
  const [trainDays, setTrainDays] = useState(180);
  const [testDays, setTestDays] = useState(30);
  const [grid, setGrid] = useState({ ...DEFAULT_GRID });
  const [isoRes, setIsoRes] = useState<any>(null);
  const [wfRes, setWfRes] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [running, setRunning] = useState<"iso" | "wf" | null>(null);
  const [error, setError] = useState("");

  async function refresh() {
    const h = await api.get<{ runs: any[] }>("/api/validation/runs?limit=20");
    setHistory(h.runs);
  }

  useEffect(() => {
    api.get<{ strategies: StrategyInfo[] }>("/api/strategies")
      .then((s) => setStrategies(s.strategies))
      .catch((e) => setError(e.message));
    refresh().catch((e) => setError(e.message));
  }, []);

  const paramGrid = {
    ema_fast: parseList(grid.ema_fast),
    ema_slow: parseList(grid.ema_slow),
    rsi_buy: parseList(grid.rsi_buy),
    rsi_sell: parseList(grid.rsi_sell),
  };

  async function runIso() {
    setRunning("iso");
    setError("");
    try {
      const r = await api.post<{ id: string }>("/api/validation/is-oos", {
        symbol, timeframe, strategy, is_ratio: isRatio,
        param_grid: paramGrid, min_trades: 20,
      });
      const d = await api.get<any>(`/api/validation/runs/${r.id}`);
      setIsoRes(d.results);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(null);
    }
  }

  async function runWf() {
    setRunning("wf");
    setError("");
    try {
      const r = await api.post<{ id: string }>("/api/validation/walk-forward", {
        symbol, timeframe, strategy,
        param_grid: paramGrid, train_days: trainDays, test_days: testDays, min_trades: 20,
      });
      const d = await api.get<any>(`/api/validation/runs/${r.id}`);
      setWfRes(d.results);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(null);
    }
  }

  async function open(id: string) {
    const d = await api.get<any>(`/api/validation/runs/${id}`);
    if (d.kind === "is_oos") { setIsoRes(d.results); setWfRes(null); }
    else { setWfRes(d.results); setIsoRes(null); }
    window.scrollTo({ top: 0 });
  }

  const setG = (k: string) => (e: any) => setGrid((g) => ({ ...g, [k]: e.target.value }));
  const dg = isoRes?.degradation;
  const agg = wfRes?.aggregate;

  return (
    <div className="space-y-4">
      {error && <ErrorBox message={error} />}

      <Card title="IS / OOS — le passé prédit-il l'avenir ?">
        <p className="mb-3 text-sm text-slate-400">
          Découpe l'historique en deux : optimisation de la grille sur la partie IS,
          évaluation des meilleurs paramètres sur la partie OOS (jamais vue).
        </p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="Symbole"><input className={inputCls} value={symbol} onChange={(e) => setSymbol(e.target.value)} /></Field>
          <Field label="Timeframe">
            <select className={inputCls} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {["1h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Stratégie">
            <select className={inputCls} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {strategies.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Ratio IS"><input type="number" step="0.05" min="0.5" max="0.9" className={inputCls} value={isRatio} onChange={(e) => setIsRatio(Number(e.target.value))} /></Field>
          <Field label="Grille ema_fast"><input className={inputCls} value={grid.ema_fast} onChange={setG("ema_fast")} /></Field>
          <Field label="Grille ema_slow"><input className={inputCls} value={grid.ema_slow} onChange={setG("ema_slow")} /></Field>
          <Field label="Grille rsi_buy"><input className={inputCls} value={grid.rsi_buy} onChange={setG("rsi_buy")} /></Field>
          <Field label="Grille rsi_sell"><input className={inputCls} value={grid.rsi_sell} onChange={setG("rsi_sell")} /></Field>
        </div>
        {(() => {
          const s = strategies.find((x) => x.name === strategy);
          return s ? <EdgeBadge status={s.edge_status} note={s.edge_note} /> : null;
        })()}
        <div className="mt-3">
          <Btn onClick={runIso} disabled={running !== null}>
            {running === "iso" ? <Loading label="Validation en cours…" /> : "Lancer IS/OOS"}
          </Btn>
        </div>
        {dg && (
          <div className="mt-4">
            <Stat label="Verdict" value={dg.verdict.replace(/_/g, " ")} tone={verdictTone(dg.verdict)} />
            <table className="mt-2 w-full text-sm">
              <thead><tr className="text-left text-slate-500">
                <th className="py-1 pr-2 font-medium">Métrique</th>
                <th className="py-1 pr-2 text-right font-medium">IS (optimisé)</th>
                <th className="py-1 text-right font-medium">OOS (jamais vu)</th>
              </tr></thead>
              <tbody>
                <MetricRow label="Rendement" is={isoRes.is.metrics.total_return} oos={isoRes.oos.metrics.total_return} fmt={(v) => fmtPct(v)} />
                <MetricRow label="Sharpe" is={isoRes.is.metrics.sharpe_ratio} oos={isoRes.oos.metrics.sharpe_ratio} fmt={(v) => (v ?? "—").toString()} />
                <MetricRow label="Profit factor" is={isoRes.is.metrics.profit_factor} oos={isoRes.oos.metrics.profit_factor} fmt={(v) => (v ?? "—").toString()} />
                <MetricRow label="Max drawdown" is={isoRes.is.metrics.max_drawdown} oos={isoRes.oos.metrics.max_drawdown} fmt={(v) => fmtPct(v)} />
                <MetricRow label="Trades" is={isoRes.is.metrics.n_trades} oos={isoRes.oos.metrics.n_trades} fmt={(v) => String(v)} />
                <MetricRow label="Win rate" is={isoRes.is.metrics.win_rate} oos={isoRes.oos.metrics.win_rate} fmt={(v) => fmtRate(v)} />
              </tbody>
            </table>
            <p className="mt-2 text-xs text-slate-500">
              Coupure : {shortTs(isoRes.split_ts)} — paramètres retenus : {JSON.stringify(isoRes.params)}
            </p>
          </div>
        )}
      </Card>

      <Card title="Walk-forward — robustesse dans le temps">
        <p className="mb-3 text-sm text-slate-400">
          Fenêtre d'optimisation roulante : à chaque pli, la grille est optimisée sur
          le TRAIN puis évaluée sur le TEST suivant. Le TEST ne sert jamais à choisir.
        </p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Train (jours)"><input type="number" className={inputCls} value={trainDays} onChange={(e) => setTrainDays(Number(e.target.value))} /></Field>
          <Field label="Test (jours)"><input type="number" className={inputCls} value={testDays} onChange={(e) => setTestDays(Number(e.target.value))} /></Field>
        </div>
        <div className="mt-3">
          <Btn onClick={runWf} disabled={running !== null}>
            {running === "wf" ? <Loading label="Walk-forward en cours…" /> : "Lancer walk-forward"}
          </Btn>
        </div>
        {agg && (
          <div className="mt-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Plis profitables" value={`${agg.profitable_folds}/${wfRes.n_folds}`} tone={toneFor(agg.profitable_ratio - 0.5)} />
              <Stat label="Rendement test moyen" value={fmtPct(agg.avg_test_return)} tone={toneFor(agg.avg_test_return)} />
              <Stat label="Sharpe test moyen" value={String(agg.avg_test_sharpe)} tone={toneFor(agg.avg_test_sharpe)} />
              <Stat label="Rendement composé" value={fmtPct(agg.compounded_test_return)} tone={toneFor(agg.compounded_test_return)} />
            </div>
            <table className="mt-3 w-full text-sm">
              <thead><tr className="text-left text-slate-500">
                <th className="py-1 pr-2 font-medium">Pli</th>
                <th className="py-1 pr-2 font-medium">Test</th>
                <th className="py-1 pr-2 font-medium">Params (train)</th>
                <th className="py-1 text-right font-medium">Ret. test</th>
                <th className="py-1 text-right font-medium">Sharpe test</th>
              </tr></thead>
              <tbody>
                {wfRes.folds.map((f: any, i: number) => (
                  <tr key={i} className="border-t border-white/10">
                    <td className="py-1.5 pr-2">{i + 1}</td>
                    <td className="py-1.5 pr-2 text-slate-400">{shortTs(f.test_start)} → {shortTs(f.test_end)}</td>
                    <td className="py-1.5 pr-2 font-mono text-xs">
                      ema {f.best_params.ema_fast}/{f.best_params.ema_slow}, rsi {f.best_params.rsi_buy}/{f.best_params.rsi_sell}
                    </td>
                    <td className="py-1.5 text-right font-mono">{fmtPct(f.test_metrics.total_return)}</td>
                    <td className="py-1.5 text-right font-mono">{String(f.test_metrics.sharpe_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Historique des validations">
        {history.length === 0 ? (
          <p className="text-sm text-slate-500">Aucune validation pour l'instant.</p>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500">
              <th className="py-1 pr-2 font-medium">Date</th>
              <th className="py-1 pr-2 font-medium">Type</th>
              <th className="py-1 pr-2 font-medium">Symbole</th>
              <th className="py-1 font-medium">Résumé</th>
            </tr></thead>
            <tbody>
              {history.map((h: any) => (
                <tr key={h.id} className="border-t border-white/10">
                  <td className="py-1.5 pr-2 text-slate-400">{shortTs(h.created_at)}</td>
                  <td className="py-1.5 pr-2">
                    <button className="text-blue-400 hover:underline" onClick={() => open(h.id)}>
                      {h.kind === "is_oos" ? "IS/OOS" : "Walk-forward"}
                    </button>
                  </td>
                  <td className="py-1.5 pr-2 font-mono text-xs">{h.symbol} {h.timeframe}</td>
                  <td className="py-1.5 text-xs text-slate-300">
                    {h.kind === "is_oos"
                      ? `verdict : ${String(h.summary?.verdict ?? "—").replace(/_/g, " ")}`
                      : `${h.summary?.profitable_folds ?? "—"} plis profitables, Sharpe moyen ${h.summary?.avg_test_sharpe ?? "—"}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
