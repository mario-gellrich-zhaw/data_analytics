import { useEffect, useMemo, useRef, useState } from "react";
import type { RunEvent } from "../api";

const LABEL: Record<string, string> = {
  business_objectives: "Business objectives", define_data: "Define data", collect_data: "Collect data",
  prepare_store: "Prepare & store", eda: "EDA", modeling: "Modeling", evaluation: "Evaluation",
  present_results: "Present results",
};

const AGENT_COLOR: Record<string, string> = {
  ObjectiveAgent: "bg-sky-600", DataRequirementsAgent: "bg-cyan-600", DataCollectorAgent: "bg-teal-600",
  DataEngineerAgent: "bg-emerald-600", EDAAgent: "bg-lime-600", ModelingAgent: "bg-violet-600",
  EvaluationAgent: "bg-fuchsia-600", PresenterAgent: "bg-pink-600", CriticAgent: "bg-amber-600",
  Supervisor: "bg-stone-600", Gate: "bg-stone-800", orchestrator: "bg-stone-500",
};

function Badge({ agent }: { agent: string | null }) {
  const a = agent ?? "system";
  return <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white ${AGENT_COLOR[a] ?? "bg-stone-500"}`}>{a}</span>;
}

function time(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function GateCard({ e }: { e: RunEvent }) {
  const p = e.payload;
  if (p.kind === "holdout_locked" || p.kind === "awaiting_approval" || !p.decision) {
    return <div className="rounded-lg border border-stone-300 bg-stone-100 px-3 py-2 text-sm dark:border-stone-700 dark:bg-stone-900">🔒 {e.summary}</div>;
  }
  const color = p.decision === "pass" ? "border-emerald-500" : p.decision === "retry_same_phase" ? "border-amber-500"
    : p.decision === "route_back" ? "border-orange-500" : "border-rose-500";
  const failed = (p.checks ?? []).filter((c: any) => !c.passed);
  return (
    <div className={`rounded-lg border-l-4 ${color} bg-white px-3 py-2 shadow-sm dark:bg-stone-900`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">Gate · {LABEL[p.node] ?? p.node}</span>
        <span className="rounded bg-stone-100 px-1.5 text-xs font-medium dark:bg-stone-800">{p.decision}</span>
        {p.critic_score != null && <span className="text-xs text-stone-500">critic {Number(p.critic_score).toFixed(1)}/10</span>}
        <span className="text-xs text-stone-500">→ {(p.options ?? []).map((o: string) => LABEL[o] ?? o).join(" / ")}</span>
      </div>
      {(p.reasons ?? []).length > 0 && <div className="mt-1 text-xs text-stone-600 dark:text-stone-400">{p.reasons.join("; ")}</div>}
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer text-stone-500">{(p.checks ?? []).length} checks{failed.length ? `, ${failed.length} failed` : ""}</summary>
        <ul className="mt-1 space-y-0.5">
          {(p.checks ?? []).map((c: any, i: number) => (
            <li key={i} className={c.passed ? "text-emerald-700 dark:text-emerald-400" : c.severity === "warning" ? "text-amber-700 dark:text-amber-400" : "text-rose-700 dark:text-rose-400"}>
              {c.passed ? "✓" : c.severity === "warning" ? "!" : "✗"} {c.name}{c.detail ? ` — ${c.detail}` : ""}
            </li>
          ))}
        </ul>
        {p.critique && (
          <div className="mt-1 rounded bg-amber-50 p-2 dark:bg-amber-950/40">
            <b>Critic:</b> {p.critique.feedback_for_agent || (p.critique.issues ?? []).join("; ")}
          </div>
        )}
      </details>
    </div>
  );
}

function ToolCall({ call, result }: { call: RunEvent; result?: RunEvent }) {
  const ok = result ? result.payload.ok !== false : undefined;
  return (
    <details className="group rounded-md border border-stone-200 bg-stone-50 text-xs dark:border-stone-800 dark:bg-stone-900/60">
      <summary className="flex cursor-pointer items-center gap-2 px-2 py-1">
        <span className={ok === undefined ? "text-blue-600" : ok ? "text-emerald-600" : "text-rose-600"}>{ok === undefined ? "…" : ok ? "✓" : "✗"}</span>
        <span className="font-mono">{call.summary}</span>
        {result?.payload.seconds != null && <span className="ml-auto text-stone-400">{result.payload.seconds}s</span>}
      </summary>
      <div className="space-y-2 border-t border-stone-200 p-2 dark:border-stone-800">
        {call.payload.args?.code && <pre className="max-h-72 overflow-auto rounded bg-stone-900 p-2 font-mono text-[11px] text-stone-100">{call.payload.args.code}</pre>}
        {call.payload.args && !call.payload.args.code && <pre className="overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(call.payload.args, null, 1)}</pre>}
        {result && <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-stone-600 dark:text-stone-300">{result.payload.output ?? result.summary}</pre>}
      </div>
    </details>
  );
}

type Item = { kind: "event"; e: RunEvent } | { kind: "tool"; call: RunEvent; result?: RunEvent };

export default function Timeline({ events, agents }: { events: RunEvent[]; agents: string[] }) {
  const [filter, setFilter] = useState<string>("all");
  const [showBudget, setShowBudget] = useState(false);
  const [follow, setFollow] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);

  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    const open: Record<string, Item & { kind: "tool" }> = {};
    for (const e of events) {
      if (filter !== "all" && e.agent !== filter && e.type !== "transition") continue;
      if (e.type === "budget" && !showBudget && !e.payload.exhausted) continue;
      if (e.type === "tool_call") {
        const it = { kind: "tool" as const, call: e };
        open[`${e.agent}:${e.payload.tool}`] = it;
        out.push(it);
      } else if (e.type === "tool_result") {
        const key = `${e.agent}:${e.payload.tool}`;
        if (open[key] && !open[key].result) { open[key].result = e; delete open[key]; }
        else out.push({ kind: "event", e });
      } else if (!(e.type === "message" && e.payload.kind === "agent_result")) {
        out.push({ kind: "event", e });
      }
    }
    return out;
  }, [events, filter, showBudget]);

  useEffect(() => { if (follow) endRef.current?.scrollIntoView({ block: "end" }); }, [items.length, follow]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-stone-200 px-3 py-2 text-xs dark:border-stone-800">
        {["all", ...agents].map((a) => (
          <button key={a} onClick={() => setFilter(a)}
            className={`rounded-full px-2 py-0.5 ${filter === a ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "bg-stone-100 dark:bg-stone-800"}`}>
            {a}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={showBudget} onChange={(e) => setShowBudget(e.target.checked)} /> cost events</label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {items.length === 0 && <div className="py-10 text-center text-sm text-stone-400">No events yet.</div>}
        {items.map((it, i) => {
          if (it.kind === "tool") return <div key={i} className="pl-8"><ToolCall call={it.call} result={it.result} /></div>;
          const e = it.e;
          if (e.type === "transition") {
            const p = e.payload;
            return (
              <div key={i} className="flex items-center gap-2 py-1 text-xs text-stone-500">
                <div className="h-px flex-1 bg-stone-200 dark:bg-stone-800" />
                <span className={p.kind === "feedback" ? "font-semibold text-orange-600" : p.kind === "retry" ? "font-semibold text-amber-600" : ""}>
                  {p.kind === "start" ? "▶ run started" : p.kind === "end" ? "■ run finished" : p.kind === "resume" ? "⟳ resumed" :
                    `${LABEL[p.from] ?? p.from} → ${LABEL[p.to] ?? p.to}`}{p.kind && !["start", "end", "resume"].includes(p.kind) ? ` · ${p.kind}` : ""}
                </span>
                <div className="h-px flex-1 bg-stone-200 dark:bg-stone-800" />
              </div>
            );
          }
          if (e.type === "gate") return <GateCard key={i} e={e} />;
          const tone = e.type === "error" ? "text-rose-700 dark:text-rose-400" : e.type === "metric" ? "text-violet-700 dark:text-violet-300"
            : e.type === "artifact" ? "text-teal-700 dark:text-teal-300" : "";
          const critique = e.payload.kind === "critique";
          return (
            <div key={i} className="flex items-start gap-2">
              <span className="w-14 shrink-0 pt-0.5 font-mono text-[10px] text-stone-400">{time(e.ts)}</span>
              <Badge agent={e.agent} />
              <div className={`min-w-0 flex-1 whitespace-pre-wrap break-words text-sm ${tone} ${critique ? "rounded bg-amber-50 px-2 py-1 dark:bg-amber-950/40" : ""}`}>
                {e.type === "artifact" ? "📄 " : e.type === "metric" ? "📈 " : e.type === "error" ? "⚠ " : ""}{e.summary}
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
