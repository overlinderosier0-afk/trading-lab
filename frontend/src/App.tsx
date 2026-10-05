import { useState } from "react";
import Market from "./tabs/Market";
import SignalLab from "./tabs/SignalLab";
import System from "./tabs/System";

const TABS = [
  { id: "signallab", label: "Signal Lab" },
  { id: "market", label: "Marché" },
  { id: "system", label: "Système" },
];

export default function App() {
  const [tab, setTab] = useState("signallab");
  return (
    <div className="min-h-screen text-[#c9d4e3]">
      <div className="aurora-bg" aria-hidden="true" />
      <header className="topbar sticky top-0 z-50">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
          <span className="term-prompt text-lg font-bold">$</span>
          <h1 className="font-display text-glow-cyan text-lg font-bold tracking-tight">
            trading-lab<span className="term-cursor" />
          </h1>
          <span className="text-xs font-bold text-[#5b6b82]">live · paper</span>
          <span className="glass-banner px-2.5 py-1 text-xs font-bold">
            PAPER TRADING — AUCUN ARGENT RÉEL
          </span>
        </div>
        <nav className="mx-auto max-w-7xl px-4 pb-3">
          <div className="tabbar flex gap-1 overflow-x-auto p-1.5">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`whitespace-nowrap px-4 py-2 text-sm font-semibold ${
                  tab === t.id ? "tab-active" : "tab-idle"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">
        {tab === "signallab" && <SignalLab />}
        {tab === "market" && <Market />}
        {tab === "system" && <System />}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-center text-xs text-[#5b6b82]">
        // trading-lab — recherche et simulation. backtest != garantie de performance future.
      </footer>
    </div>
  );
}
