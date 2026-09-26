import { useEffect, useState } from "react";
import { api } from "../api";

function score(s: any) {
  return s == null ? "–" : Number(s).toFixed(3);
}

export default function Improvements() {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api.improvements().then(setRows).catch((e) => setError(String(e)));
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="mx-auto max-w-5xl p-4">
      <h2 className="text-lg font-semibold">Self-improvement cycles</h2>
      <p className="mb-4 text-sm text-stone-500">
        Each cycle runs the benchmark suite, lets the ImproverAgent propose ≤ 3 changes to prompts/, config.yaml (non-budget keys) or
        agents/tools/, re-runs the benchmark on a git branch, and accepts only a gain beyond noise with all tests passing.
        Gates, evaluation code, holdouts, budgets and benchmarks are off-limits (path allowlist enforced in code).
      </p>
      {error && <div className="text-sm text-rose-600">{error}</div>}
      {rows.length === 0 && <div className="rounded-lg border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500 dark:border-stone-700">No cycles yet — run <code className="font-mono">make improve</code>.</div>}
      <div className="space-y-3">
        {rows.map((r) => (
          <details key={r.id} className="rounded-lg border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
            <summary className="flex cursor-pointer flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${r.accepted ? "bg-emerald-600" : r.status === "error" ? "bg-stone-500" : "bg-rose-600"}`}>
                {r.accepted ? "accepted" : r.status === "error" ? "error" : "rejected"}
              </span>
              <span className="font-mono text-xs">{r.id}</span>
              <span className="text-xs text-stone-500">{r.base_version} → {r.branch}</span>
              <span className="ml-auto font-mono text-xs">score {score(r.baseline?.mean)} → {score(r.candidate?.mean)}
                {r.delta != null && <b className={r.delta > 0 ? " text-emerald-600" : " text-rose-600"}> ({r.delta > 0 ? "+" : ""}{Number(r.delta).toFixed(3)})</b>}
              </span>
            </summary>
            <div className="space-y-2 border-t border-stone-200 p-3 text-sm dark:border-stone-800">
              <div><b>Diagnosis:</b> {r.proposal?.diagnosis}</div>
              <div><b>Expected effect:</b> {r.proposal?.expected_effect}</div>
              <div><b>Decision:</b> {r.reason}</div>
              <div className="text-xs text-stone-500">tests: {r.tests_passed ? "passed" : "failed / not run"} · noise margin {score(r.margin)} · tasks {Object.keys(r.baseline?.per_task ?? {}).join(", ")}</div>
              <table className="w-full text-xs">
                <thead><tr className="text-left text-stone-500"><th>task</th><th>baseline scores</th><th>candidate scores</th></tr></thead>
                <tbody>{Object.keys(r.baseline?.per_task ?? {}).map((t) => (
                  <tr key={t}><td className="font-mono">{t}</td><td className="font-mono">{(r.baseline.per_task[t] ?? []).map(score).join(", ")}</td><td className="font-mono">{(r.candidate?.per_task?.[t] ?? []).map(score).join(", ")}</td></tr>
                ))}</tbody>
              </table>
              {r.diff && <pre className="max-h-96 overflow-auto rounded bg-stone-900 p-2 font-mono text-[11px] text-stone-100">{r.diff}</pre>}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
