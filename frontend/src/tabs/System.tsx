import { useEffect, useState } from "react";
import { api, shortTs } from "../api";
import { Card, Stat, Loading, ErrorBox, Btn } from "../components";

interface SysStatus {
  service: string;
  database: { reachable: boolean };
  market_data_provider: { reachable: boolean };
  scheduler_running: boolean;
  trading_enabled: boolean;
  paper_trading_enabled: boolean;
  disk: { total_gb: number; used_pct: number };
  memory: { total_mb: number; used_pct: number };
}

interface SysEvent { ts: string; level: string; event: string; message: string }

function Dot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-600" : "bg-rose-600"}`} />;
}

const LEVEL_STYLE: Record<string, string> = {
  INFO: "text-sky-600", WARNING: "text-amber-600", ERROR: "text-rose-600", CRITICAL: "text-rose-700 font-bold",
};

export default function System() {
  const [status, setStatus] = useState<SysStatus | null>(null);
  const [events, setEvents] = useState<SysEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [s, e] = await Promise.all([
        api.get<SysStatus>("/api/system/status"),
        api.get<{ events: SysEvent[] }>("/api/system/events?limit=80"),
      ]);
      setStatus(s); setEvents(e.events);
    } catch (err: any) { setError(err.message); } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card><div className="flex items-center gap-2 text-sm"><Dot ok={status?.database.reachable ?? false} /> Base de données</div></Card>
        <Card><div className="flex items-center gap-2 text-sm"><Dot ok={status?.market_data_provider.reachable ?? false} /> Binance (données)</div></Card>
        <Card><div className="flex items-center gap-2 text-sm"><Dot ok={status?.scheduler_running ?? false} /> Scheduler</div></Card>
        <Card><div className="flex items-center gap-2 text-sm"><Dot ok={!(status?.paper_trading_enabled ?? false)} /> Paper trading {status?.paper_trading_enabled ? "ACTIF" : "désactivé"}</div></Card>
        <Card><Stat label="Disque" value={`${status?.disk.used_pct} %`} sub={`${status?.disk.total_gb} Go`} /></Card>
        <Card><Stat label="RAM" value={`${status?.memory.used_pct} %`} sub={`${status?.memory.total_mb} Mo`} /></Card>
        <Card><Stat label="Kill switch" value={status?.trading_enabled ? "non engagé" : "ENGAGÉ"} tone={status?.trading_enabled ? "neutral" : "neg"} /></Card>
        <Card><div className="flex h-full items-center"><Btn kind="ghost" onClick={load}>Rafraîchir</Btn></div></Card>
      </div>

      <Card title="Journal d'événements">
        {events.length === 0 && <p className="text-sm text-slate-500">Aucun événement.</p>}
        <div className="max-h-[500px] space-y-1 overflow-y-auto">
          {events.map((e, i) => (
            <div key={i} className="flex items-start gap-3 rounded glass-deep px-3 py-1.5 text-sm">
              <span className="whitespace-nowrap text-slate-500">{shortTs(e.ts)}</span>
              <span className={`w-20 shrink-0 font-semibold ${LEVEL_STYLE[e.level] ?? "text-slate-500"}`}>{e.level}</span>
              <span className="w-36 shrink-0 text-slate-500">{e.event}</span>
              <span className="text-slate-600">{e.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
