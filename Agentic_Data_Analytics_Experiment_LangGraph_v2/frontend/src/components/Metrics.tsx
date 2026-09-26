import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ComposedChart, Tooltip, XAxis, YAxis } from "recharts";
import type { RunView } from "../runView";

function fmt(n: number | null | undefined, digits = 4) {
  if (n == null || Number.isNaN(n)) return "–";
  return Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : Number(n.toPrecision(digits)).toString();
}

function Meter({ label, used, limit, unit }: { label: string; used: number; limit: number; unit: (n: number) => string }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const tone = pct > 90 ? "bg-rose-500" : pct > 70 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="min-w-0 flex-1">
      <div className="flex justify-between text-[11px] text-stone-500"><span>{label}</span><span className="font-mono">{unit(used)} / {unit(limit)}</span></div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800"><div className={`h-full ${tone} transition-all`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function BudgetMeters({ view }: { view: RunView }) {
  const b = view.budget;
  if (!b) return <div className="text-xs text-stone-400">No spend yet.</div>;
  return (
    <div className="flex flex-wrap gap-4">
      <Meter label="Cost" used={b.usd} limit={b.limits.max_usd} unit={(n) => `$${n.toFixed(2)}`} />
      <Meter label="Tokens" used={b.total_tokens} limit={b.limits.max_tokens} unit={(n) => n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : `${Math.round(n / 1000)}k`} />
      <Meter label="Time" used={b.elapsed_seconds} limit={b.limits.max_wall_seconds} unit={(n) => `${Math.round(n / 60)}m`} />
      <div className="text-[11px] text-stone-500"><div>LLM calls</div><div className="font-mono text-sm text-stone-800 dark:text-stone-200">{b.llm_calls}</div></div>
    </div>
  );
}

export default function Metrics({ view }: { view: RunView }) {
  const metric = view.primaryMetric ?? "metric";
  const baseline = view.validations.length ? view.validations[view.validations.length - 1].baseline : undefined;
  const lower = metric === "mae" || metric === "rmse" || metric === "mape";
  let best: number | null = null;
  const data = view.experiments.filter((e) => e.value != null && Number.isFinite(e.value)).map((e) => {
    best = best == null ? e.value! : lower ? Math.min(best, e.value!) : Math.max(best, e.value!);
    return { ...e, best };
  });
  const h = view.holdout;
  return (
    <div className="space-y-4 p-3">
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-900">
          <div className="text-[11px] text-stone-500">best validation {metric}</div>
          <div className="font-mono text-lg">{fmt(view.validations.length ? view.validations[view.validations.length - 1].value : null)}</div>
        </div>
        <div className="rounded-lg bg-stone-100 p-2 dark:bg-stone-900">
          <div className="text-[11px] text-stone-500">naive baseline / target</div>
          <div className="font-mono text-lg">{fmt(baseline ?? null)} / {fmt(view.threshold)}</div>
        </div>
        <div className={`rounded-lg p-2 ${h ? "bg-blue-50 dark:bg-blue-950" : "bg-stone-100 dark:bg-stone-900"}`}>
          <div className="text-[11px] text-stone-500">locked holdout {metric}</div>
          <div className="font-mono text-lg">{h ? fmt(h.metrics?.[metric]) : "sealed"}</div>
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-stone-600 dark:text-stone-300">Experiments ({metric} on validation)</div>
        {data.length === 0 ? <div className="py-8 text-center text-xs text-stone-400">No experiments logged yet.</div> : (
          <div className="h-56">
            <ResponsiveContainer>
              <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#d6d3d1" opacity={0.5} />
                <XAxis dataKey="index" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={52} domain={["auto", "auto"]} />
                <Tooltip formatter={(v: any) => fmt(Number(v))} labelFormatter={(i: any) => data[Number(i) - 1]?.name ?? i} />
                {baseline != null && <ReferenceLine y={baseline} stroke="#a8a29e" strokeDasharray="4 4" label={{ value: "baseline", fontSize: 10, position: "insideTopRight" }} />}
                {view.threshold != null && <ReferenceLine y={view.threshold} stroke="#16a34a" strokeDasharray="4 4" label={{ value: "target", fontSize: 10, position: "insideBottomRight" }} />}
                <Line type="stepAfter" dataKey="best" name="best so far" stroke="#7c3aed" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                <Scatter dataKey="value" name="experiment" fill="#7c3aed" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
      {view.validations.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-stone-600 dark:text-stone-300">Best model per modeling round (orchestrator-scored)</div>
          <div className="h-36">
            <ResponsiveContainer>
              <LineChart data={view.validations} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#d6d3d1" opacity={0.5} />
                <XAxis dataKey="visit" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} width={52} domain={["auto", "auto"]} />
                <Tooltip formatter={(v: any) => fmt(Number(v))} />
                {view.threshold != null && <ReferenceLine y={view.threshold} stroke="#16a34a" strokeDasharray="4 4" />}
                <Line dataKey="value" name="model" stroke="#2563eb" strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="baseline" name="baseline" stroke="#a8a29e" strokeDasharray="4 4" isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {h && (
        <div className="rounded-lg border border-blue-200 p-2 text-xs dark:border-blue-900">
          <div className="mb-1 font-medium">Locked holdout (n = {h.n_holdout}, evaluated once)</div>
          <table className="w-full">
            <thead><tr className="text-stone-500"><th className="text-left">metric</th><th className="text-right">model</th><th className="text-right">baseline</th></tr></thead>
            <tbody>{Object.entries(h.metrics ?? {}).map(([k, v]) => (
              <tr key={k} className={k === metric ? "font-semibold" : ""}><td>{k}</td><td className="text-right font-mono">{fmt(v as number)}</td><td className="text-right font-mono">{fmt(h.baseline?.[k])}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
