import React from "react";

// ---------- Carte (verre dépoli) ----------
export function Card({ title, children, className = "" }: {
  title?: string; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={`glass p-5 ${className}`}>
      {title && <h3 className="font-display mb-4 text-sm font-semibold uppercase tracking-widest text-slate-300/80">{title}</h3>}
      {children}
    </div>
  );
}

// ---------- Indicateur coloré ----------
export function Stat({ label, value, sub, tone = "neutral" }: {
  label: string; value: string; sub?: string;
  tone?: "neutral" | "pos" | "neg" | "warn";
}) {
  const colors = {
    neutral: "text-slate-100",
    pos: "text-emerald-400",
    neg: "text-rose-400",
    warn: "text-amber-400",
  };
  return (
    <div>
      <div className="text-xs uppercase tracking-widest text-slate-400/80">{label}</div>
      <div className={`tnum font-display mt-1 text-2xl font-bold ${colors[tone]}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400/70">{sub}</div>}
    </div>
  );
}

export function toneFor(v: number | null | undefined): "pos" | "neg" | "neutral" {
  if (v === null || v === undefined) return "neutral";
  return v > 0 ? "pos" : v < 0 ? "neg" : "neutral";
}

// ---------- Badge signal ----------
export function SignalBadge({ signal }: { signal: string }) {
  const styles: Record<string, string> = {
    BUY: "bg-emerald-400/10 text-emerald-300 border-emerald-400/30 shadow-[0_0_16px_rgba(52,211,153,0.15)]",
    SELL: "bg-rose-400/10 text-rose-300 border-rose-400/30 shadow-[0_0_16px_rgba(251,113,133,0.15)]",
    HOLD: "bg-white/5 text-slate-300 border-white/15",
    long: "bg-emerald-400/10 text-emerald-300 border-emerald-400/30 shadow-[0_0_16px_rgba(52,211,153,0.15)]",
    short: "bg-rose-400/10 text-rose-300 border-rose-400/30 shadow-[0_0_16px_rgba(251,113,133,0.15)]",
  };
  return (
    <span className={`inline-block rounded-lg border px-2 py-0.5 text-xs font-semibold backdrop-blur-md ${styles[signal] ?? styles.HOLD}`}>
      {signal}
    </span>
  );
}

// ---------- Badge statut d'edge d'une stratégie ----------
export function EdgeBadge({ status, note }: { status: string; note?: string }) {
  if (status === "NO_EDGE_DEMONSTRATED") {
    return (
      <div className="glass-banner mt-3 px-3 py-2 text-xs">
        <span className="font-bold">⚠ Aucune preuve d'edge démontré — stratégie de démonstration uniquement.</span>
        {note && <span className="mt-1 block opacity-70">{note}</span>}
      </div>
    );
  }
  if (status === "UNDER_REVIEW") {
    return (
      <div className="mt-3 rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-200 backdrop-blur-md">
        <span className="font-bold">En cours d'évaluation — ne pas utiliser pour trader.</span>
        {note && <span className="mt-1 block opacity-70">{note}</span>}
      </div>
    );
  }
  return null;
}

// ---------- Graphique ligne SVG (sans dépendance) ----------
export function LineChart({ data, height = 220, color = "#34d399", formatY }: {
  data: { ts: string; value: number }[];
  height?: number;
  color?: string;
  formatY?: (v: number) => string;
}) {
  if (!data.length) return <div className="py-8 text-center text-sm text-slate-500">Aucune donnée</div>;
  const w = 900, h = height, pad = 10;
  const vals = data.map((d) => d.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const stepX = (w - pad * 2) / Math.max(1, data.length - 1);
  const pts = data
    .map((d, i) => `${(pad + i * stepX).toFixed(1)},${(h - pad - ((d.value - min) / span) * (h - pad * 2)).toFixed(1)}`)
    .join(" ");
  const fy = formatY ?? ((v: number) => v.toFixed(2));
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none" style={{ height }}>
        <polygon points={`${pad},${h - pad} ${pts} ${w - pad},${h - pad}`} fill={color} opacity={0.1} />
        <polyline points={pts} fill="none" stroke={color} strokeWidth={1.6} />
      </svg>
      <div className="mt-1 flex justify-between text-xs text-slate-500">
        <span>min {fy(min)}</span>
        <span>max {fy(max)}</span>
      </div>
    </div>
  );
}

// ---------- État de chargement / erreur ----------
export function Loading({ label = "Chargement…" }: { label?: string }) {
  return <div className="py-10 text-center text-sm text-slate-500">{label}</div>;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="glass border-rose-400/30 p-4 text-sm text-rose-200">
      <div className="font-semibold">Erreur</div>
      <div className="mt-1 break-words opacity-80">{message}</div>
      {onRetry && (
        <button onClick={onRetry} className="btn-glass btn-glass-danger mt-3 text-xs">
          Réessayer
        </button>
      )}
    </div>
  );
}

// ---------- Bouton ----------
export function Btn({ children, onClick, kind = "primary", disabled }: {
  children: React.ReactNode; onClick?: () => void;
  kind?: "primary" | "danger" | "ghost"; disabled?: boolean;
}) {
  const styles = {
    primary: "btn-glass-primary",
    danger: "btn-glass-danger",
    ghost: "btn-glass-ghost",
  };
  return (
    <button onClick={onClick} disabled={disabled}
      className={`btn-glass ${styles[kind]}`}>
      {children}
    </button>
  );
}

// ---------- Champ formulaire ----------
export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

export const inputCls = "glass-input";
