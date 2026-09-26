import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, wsUrl, type GraphSpec, type Run, type RunEvent } from "./api";
import { deriveRunView } from "./runView";
import ProcessGraph from "./components/ProcessGraph";
import Timeline from "./components/Timeline";
import ArtifactsPanel from "./components/ArtifactsPanel";
import Metrics, { BudgetMeters } from "./components/Metrics";
import Improvements from "./components/Improvements";

const STATUS_TONE: Record<string, string> = {
  running: "bg-blue-600", completed: "bg-emerald-600", budget_exhausted: "bg-amber-600", stopped: "bg-stone-500",
  failed: "bg-rose-600", interrupted: "bg-orange-600", awaiting_approval: "bg-violet-600",
};

function useRunEvents(runId: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  useEffect(() => {
    setEvents([]); setStatus(null);
    if (!runId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let last = 0;
    const connect = () => {
      ws = new WebSocket(`${wsUrl(runId)}?after=${last}`);
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type === "events") {
          last = msg.events[msg.events.length - 1].id;
          setEvents((prev) => [...prev, ...msg.events]);
        } else if (msg.type === "status") setStatus(msg.status);
      };
      ws.onclose = () => { if (!closed) setTimeout(connect, 1500); };
    };
    connect();
    return () => { closed = true; ws?.close(); };
  }, [runId]);
  return { events, status };
}

function remembered(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

function StartPanel({ onStarted, defaults }: { onStarted: (id: string) => void; defaults: any }) {
  const [objective, setObjective] = useState(() => remembered("ada.objective", "Build a price prediction model for rental apartments in Switzerland, trained on apartment-level data (one row per individual rental listing, not aggregated statistics)."));
  const [region, setRegion] = useState(() => remembered("ada.region", "Switzerland"));
  const [maxUsd, setMaxUsd] = useState<number>(5);
  const [maxMin, setMaxMin] = useState<number>(90);
  const [maxLoops, setMaxLoops] = useState<number>(8);
  const [mode, setMode] = useState<"real" | "stub">("real");
  const [offline, setOffline] = useState(false);
  const [approval, setApproval] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!defaults?.budgets) return;
    setMaxUsd(defaults.budgets.max_usd); setMaxMin(Math.round(defaults.budgets.max_wall_seconds / 60)); setMaxLoops(defaults.budgets.max_total_loopbacks);
  }, [defaults]);
  const start = async () => {
    setBusy(true); setErr(null);
    try { localStorage.setItem("ada.objective", objective); localStorage.setItem("ada.region", region); } catch { /* storage unavailable */ }
    try {
      const { run_id } = await api.start({ objective, region: region || null, mode, offline, max_usd: maxUsd, max_minutes: maxMin, max_loopbacks: maxLoops, human_approval: approval });
      onStarted(run_id);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const slider = (label: string, value: number, set: (n: number) => void, min: number, max: number, step: number, fmt: (n: number) => string) => (
    <label className="block text-xs">
      <div className="flex justify-between text-stone-600 dark:text-stone-400"><span>{label}</span><span className="font-mono">{fmt(value)}</span></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => set(Number(e.target.value))} className="w-full accent-blue-600" />
    </label>
  );
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium">Business objective
        <textarea value={objective} onChange={(e) => setObjective(e.target.value)} rows={3}
          className="mt-1 w-full rounded-md border border-stone-300 bg-white p-2 text-sm dark:border-stone-700 dark:bg-stone-900" />
      </label>
      <label className="block text-xs font-medium">Region / target market
        <input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="empty = agents decide based on data availability"
          className="mt-1 w-full rounded-md border border-stone-300 bg-white p-2 text-sm dark:border-stone-700 dark:bg-stone-900" />
      </label>
      {slider("Max spend", maxUsd, setMaxUsd, 0.5, 20, 0.5, (n) => `$${n.toFixed(1)}`)}
      {slider("Max wall time", maxMin, setMaxMin, 5, 240, 5, (n) => `${n} min`)}
      {slider("Max loop-backs", maxLoops, setMaxLoops, 0, 20, 1, (n) => `${n}`)}
      <div className="flex flex-wrap gap-3 text-xs">
        <label className="flex items-center gap-1"><input type="checkbox" checked={mode === "stub"} onChange={(e) => setMode(e.target.checked ? "stub" : "real")} /> demo (fake agents)</label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} /> offline</label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} /> human approval</label>
      </div>
      <button onClick={start} disabled={busy} className="w-full rounded-md bg-blue-600 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
        {busy ? "Starting…" : "Start run"}
      </button>
      {err && <div className="text-xs text-rose-600">{err}</div>}
    </div>
  );
}

function RunList({ runs, selected, onSelect, onResume, onDelete }: {
  runs: Run[]; selected: string | null; onSelect: (id: string) => void; onResume: (id: string) => void; onDelete: (r: Run) => void;
}) {
  return (
    <ul className="space-y-1">
      {runs.map((r) => (
        <li key={r.id}>
          <button onClick={() => onSelect(r.id)}
            className={`w-full rounded-md px-2 py-1.5 text-left ${selected === r.id ? "bg-blue-50 ring-1 ring-blue-300 dark:bg-blue-950 dark:ring-blue-800" : "hover:bg-stone-100 dark:hover:bg-stone-900"}`}>
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_TONE[r.status] ?? "bg-stone-400"} ${r.active ? "animate-pulse" : ""}`} />
              <span className="truncate text-xs font-medium" title={r.objective}>{r.region ? `[${r.region}] ` : ""}{r.objective}</span>
            </div>
            <div className="mt-0.5 flex items-center gap-2 pl-4 text-[10px] text-stone-500">
              <span className="font-mono">{r.id.slice(0, 15)}</span><span>{r.mode}</span><span>{r.status}</span>
              {r.summary?.budget?.usd != null && <span>${Number(r.summary.budget.usd).toFixed(2)}</span>}
              {["interrupted", "failed"].includes(r.status) && !r.active && (
                <span role="button" onClick={(e) => { e.stopPropagation(); onResume(r.id); }} className="ml-auto rounded bg-orange-600 px-1.5 text-white">resume</span>
              )}
              {!r.active && r.status !== "running" && (
                <span role="button" title="delete this run" aria-label="delete run"
                  onClick={(e) => { e.stopPropagation(); onDelete(r); }}
                  className={`${["interrupted", "failed"].includes(r.status) ? "" : "ml-auto"} rounded px-1 text-stone-400 hover:bg-rose-100 hover:text-rose-700 dark:hover:bg-rose-950`}>
                  🗑
                </span>
              )}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function App() {
  const [tab, setTab] = useState<"run" | "improve">("run");
  const [spec, setSpec] = useState<GraphSpec | null>(null);
  const [defaults, setDefaults] = useState<any>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [side, setSide] = useState<"metrics" | "artifacts">("metrics");
  const [run, setRun] = useState<Run | null>(null);
  const { events, status } = useRunEvents(selected);
  const view = useMemo(() => deriveRunView(events), [events]);
  const firstLoad = useRef(true);
  const [graphHeight, setGraphHeight] = useState<number>(() => {
    try { return Number(localStorage.getItem("ada.graphHeight")) || 380; } catch { return 380; }
  });
  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const y0 = e.clientY, h0 = graphHeight;
    let h = h0;
    const move = (ev: PointerEvent) => { h = Math.max(200, Math.min(1200, h0 + ev.clientY - y0)); setGraphHeight(h); };
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      try { localStorage.setItem("ada.graphHeight", String(h)); } catch { /* ignore */ }
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const refreshRuns = useCallback(() => api.runs().then((rs) => {
    setRuns(rs);
    if (firstLoad.current && rs.length) { setSelected(rs[0].id); firstLoad.current = false; }
  }).catch(() => {}), []);
  useEffect(() => { api.graph().then(setSpec); api.config().then(setDefaults).catch(() => {}); refreshRuns(); const t = setInterval(refreshRuns, 5000); return () => clearInterval(t); }, [refreshRuns]);
  useEffect(() => { if (selected) api.run(selected).then(setRun).catch(() => setRun(null)); }, [selected, status, view.finished]);

  const liveStatus = status ?? run?.status ?? "";
  const pending = run?.interrupt;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-4 border-b border-stone-200 bg-white px-4 py-2 dark:border-stone-800 dark:bg-stone-950">
        <div className="font-semibold">Agentic Analytics <span className="font-normal text-stone-500">· autonomous process model</span></div>
        <nav className="ml-6 flex gap-1 text-sm">
          {(["run", "improve"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-md px-3 py-1 ${tab === t ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "hover:bg-stone-100 dark:hover:bg-stone-900"}`}>
              {t === "run" ? "Runs" : "Self-improvement"}
            </button>
          ))}
        </nav>
      </header>
      {tab === "improve" ? <div className="flex-1 overflow-y-auto"><Improvements /></div> : (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <aside className="w-full shrink-0 space-y-4 overflow-y-auto border-b border-stone-200 bg-white p-4 lg:w-80 lg:border-b-0 lg:border-r dark:border-stone-800 dark:bg-stone-950">
            <StartPanel defaults={defaults} onStarted={(id) => { setSelected(id); refreshRuns(); }} />
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-stone-500">Runs</div>
              <RunList runs={runs} selected={selected} onSelect={setSelected} onResume={(id) => api.resume(id).then(refreshRuns)}
                onDelete={(r) => {
                  if (!confirm(`Delete run ${r.id}?\n\n${r.objective}\n\nThis removes its events, files, model and report.`)) return;
                  api.remove(r.id).then(() => { if (selected === r.id) setSelected(null); refreshRuns(); })
                    .catch((e) => alert(`Could not delete: ${e}`));
                }} />
            </div>
          </aside>
          <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
            {!selected || !spec ? <div className="p-10 text-center text-stone-400">Start a run or pick one from the list.</div> : (
              <>
                <section className="border-b border-stone-200 bg-white px-4 pt-3 dark:border-stone-800 dark:bg-stone-950">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${STATUS_TONE[liveStatus] ?? "bg-stone-400"}`}>{liveStatus || "…"}</span>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{run?.objective}{run?.region ? ` — ${run.region}` : ""}</span>
                    <span className="font-mono text-xs text-stone-500">{selected} · v{run?.system_version}</span>
                    {liveStatus === "running" && <button onClick={() => api.stop(selected)} className="rounded bg-stone-200 px-2 py-0.5 text-xs dark:bg-stone-800">stop</button>}
                  </div>
                  {pending && liveStatus === "awaiting_approval" && (
                    <div className="mt-2 rounded-lg border border-violet-300 bg-violet-50 p-2 text-sm dark:border-violet-800 dark:bg-violet-950">
                      <b>Approval needed</b> after {pending.node}: gate {pending.gate?.decision}. Supervisor proposes <b>{pending.proposed}</b> — {pending.rationale}
                      <div className="mt-1 flex flex-wrap gap-2">
                        {(pending.gate?.options ?? []).map((o: string) => (
                          <button key={o} onClick={() => api.approve(selected, o).then(() => api.run(selected).then(setRun))}
                            className={`rounded px-2 py-0.5 text-xs text-white ${o === pending.proposed ? "bg-violet-700" : "bg-stone-500"}`}>go to {o}</button>
                        ))}
                      </div>
                    </div>
                  )}
                  <ProcessGraph spec={spec} view={view} height={graphHeight} />
                  <div role="separator" aria-orientation="horizontal" title="drag to resize the process graph"
                    onPointerDown={startDrag} onDoubleClick={() => setGraphHeight(380)}
                    className="group -mx-4 flex h-3 cursor-row-resize items-center justify-center hover:bg-stone-100 dark:hover:bg-stone-900">
                    <div className="h-1 w-12 rounded-full bg-stone-300 group-hover:bg-blue-500 dark:bg-stone-700" />
                  </div>
                  <div className="pb-3"><BudgetMeters view={view} /></div>
                </section>
                <section className="grid min-h-[560px] flex-1 grid-cols-1 xl:grid-cols-12">
                  <div className="h-[640px] border-b border-stone-200 xl:col-span-7 xl:border-b-0 xl:border-r dark:border-stone-800"><Timeline events={events} agents={view.agents} /></div>
                  <div className="flex min-h-[480px] flex-col xl:col-span-5">
                    <div className="flex gap-1 border-b border-stone-200 px-3 py-2 text-xs dark:border-stone-800">
                      {(["metrics", "artifacts"] as const).map((s) => (
                        <button key={s} onClick={() => setSide(s)} className={`rounded-md px-2.5 py-1 ${side === s ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "bg-stone-100 dark:bg-stone-800"}`}>
                          {s === "metrics" ? "Metrics & budget" : "Artifacts"}
                        </button>
                      ))}
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto">
                      {side === "metrics" ? <Metrics view={view} /> : <ArtifactsPanel runId={selected} version={view.artifactsVersion} />}
                    </div>
                  </div>
                </section>
              </>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
