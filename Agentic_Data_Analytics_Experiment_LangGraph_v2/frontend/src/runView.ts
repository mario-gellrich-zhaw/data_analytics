import type { RunEvent } from "./api";

export type NodeStatus = "idle" | "active" | "pass" | "retry" | "route_back" | "escalate" | "fail";

export interface MetricPoint { index: number; name: string; value: number | null; model_type?: string }
export interface ValidationPoint { visit: number; value: number; baseline: number }

export interface RunView {
  active: string | null;
  status: Record<string, NodeStatus>;
  visits: Record<string, number>;
  edgeCounts: Record<string, number>;
  budget: { usd: number; total_tokens: number; elapsed_seconds: number; llm_calls: number;
            limits: { max_usd: number; max_tokens: number; max_wall_seconds: number } } | null;
  primaryMetric: string | null;
  threshold: number | null;
  experiments: MetricPoint[];
  validations: ValidationPoint[];
  holdout: Record<string, any> | null;
  agents: string[];
  artifactsVersion: number;
  finished: boolean;
}

export function deriveRunView(events: RunEvent[]): RunView {
  const v: RunView = {
    active: null, status: {}, visits: {}, edgeCounts: {}, budget: null, primaryMetric: null, threshold: null,
    experiments: [], validations: [], holdout: null, agents: [], artifactsVersion: 0, finished: false,
  };
  const agents = new Set<string>();
  for (const e of events) {
    if (e.agent) agents.add(e.agent);
    const p = e.payload || {};
    switch (e.type) {
      case "transition": {
        if (p.kind === "end") { v.status[e.node ?? "present_results"] = "pass"; v.active = null; v.finished = true; break; }
        const to = p.to as string | undefined;
        if (p.from && to && p.from !== "start" && p.from !== "resume") {
          v.edgeCounts[`${p.from}->${to}`] = p.count ?? (v.edgeCounts[`${p.from}->${to}`] ?? 0) + 1;
        }
        if (to && to !== "end") {
          v.active = to;
          v.visits[to] = (v.visits[to] ?? 0) + (p.kind === "resume" ? 0 : 1);
        }
        break;
      }
      case "gate": {
        if (!e.node || !p.decision) break;
        const d = p.decision as string;
        v.status[e.node] = d === "pass" ? "pass" : d === "retry_same_phase" ? "retry" : d === "route_back" ? "route_back" : "escalate";
        break;
      }
      case "budget":
        if (p.totals) v.budget = p.totals;
        break;
      case "metric":
        if (p.kind === "experiment") {
          const m = p.metrics || {};
          const key = v.primaryMetric && m[v.primaryMetric] !== undefined ? v.primaryMetric : Object.keys(m)[0];
          v.experiments.push({ index: v.experiments.length + 1, name: p.name, value: key ? Number(m[key]) : null, model_type: p.model_type });
        } else if (p.kind === "validation") {
          v.primaryMetric = p.metric; v.threshold = p.threshold ?? v.threshold;
          v.validations.push({ visit: v.validations.length + 1, value: p.metrics?.[p.metric], baseline: p.baseline?.[p.metric] });
        } else if (p.kind === "holdout") {
          v.primaryMetric = p.metric; v.holdout = p;
        }
        break;
      case "artifact":
        v.artifactsVersion += 1;
        break;
      case "message":
        if (p.kind === "agent_result" && e.node === "business_objectives" && p.result) {
          v.primaryMetric = p.result.primary_metric; v.threshold = p.result.success_threshold;
        }
        break;
    }
  }
  if (v.active) v.status[v.active] = "active";
  v.agents = [...agents].sort();
  return v;
}
