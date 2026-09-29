export type EventType =
  | "message" | "tool_call" | "tool_result" | "gate" | "transition"
  | "artifact" | "metric" | "budget" | "error" | "improvement";

export interface RunEvent {
  id: number;
  run_id: string;
  ts: number;
  node: string | null;
  agent: string | null;
  type: EventType;
  summary: string;
  payload: Record<string, any>;
}

export interface Run {
  id: string;
  objective: string;
  region: string | null;
  mode: string;
  status: string;
  created_at: number;
  updated_at: number;
  options: Record<string, any>;
  system_version: string;
  summary: Record<string, any>;
  active?: boolean;
  interrupt?: any;
}

export interface GraphSpec {
  nodes: { id: string; label: string; agent: string; x: number; y: number }[];
  edges: { id: string; source: string; target: string; kind: "forward" | "feedback" }[];
}

export interface ArtifactFile { path: string; bytes: number; mtime: number }

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  graph: () => fetch("/api/graph").then((r) => json<GraphSpec>(r)),
  config: () => fetch("/api/config").then((r) => json<any>(r)),
  runs: () => fetch("/api/runs").then((r) => json<Run[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<Run>(r)),
  start: (body: Record<string, unknown>) =>
    fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => json<{ run_id: string }>(r)),
  resume: (id: string) => fetch(`/api/runs/${id}/resume`, { method: "POST" }).then((r) => json<any>(r)),
  stop: (id: string) => fetch(`/api/runs/${id}/stop`, { method: "POST" }).then((r) => json<any>(r)),
  approve: (id: string, next_node: string, note = "") =>
    fetch(`/api/runs/${id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ next_node, note }),
    }).then((r) => json<any>(r)),
  remove: (id: string) => fetch(`/api/runs/${id}`, { method: "DELETE" }).then((r) => json<any>(r)),
  artifacts: (id: string) => fetch(`/api/runs/${id}/artifacts`).then((r) => json<ArtifactFile[]>(r)),
  fileUrl: (id: string, path: string) => `/api/runs/${id}/file?path=${encodeURIComponent(path)}`,
  fileText: (id: string, path: string) => fetch(`/api/runs/${id}/file?path=${encodeURIComponent(path)}`).then((r) => r.text()),
  improvements: () => fetch("/api/improvements").then((r) => json<any[]>(r)),
};

export function wsUrl(runId: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/runs/${runId}`;
}
