import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BaseEdge, type ReactFlowInstance, EdgeLabelRenderer, Handle, Position, ReactFlow, getBezierPath,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import type { GraphSpec } from "../api";
import type { NodeStatus, RunView } from "../runView";

type PhaseData = { label: string; agent: string; status: NodeStatus; visits: number; retries: number };

const STATUS_STYLE: Record<NodeStatus, string> = {
  idle: "border-stone-300 bg-white dark:border-stone-700 dark:bg-stone-900",
  active: "border-blue-500 bg-blue-50 dark:bg-blue-950 node-active",
  pass: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950",
  retry: "border-amber-500 bg-amber-50 dark:bg-amber-950",
  route_back: "border-orange-500 bg-orange-50 dark:bg-orange-950",
  escalate: "border-rose-500 bg-rose-50 dark:bg-rose-950",
  fail: "border-rose-500 bg-rose-50 dark:bg-rose-950",
};
const STATUS_TEXT: Record<NodeStatus, string> = {
  idle: "", active: "running", pass: "passed", retry: "retry", route_back: "sent back", escalate: "escalated", fail: "failed",
};

function PhaseNode({ data }: NodeProps<Node<PhaseData>>) {
  return (
    <div className={`w-[176px] rounded-xl border-2 px-3 py-2 shadow-sm transition-colors ${STATUS_STYLE[data.status]}`}>
      <Handle type="target" position={Position.Left} id="l" className="!opacity-0" />
      <Handle type="source" position={Position.Right} id="r" className="!opacity-0" />
      <Handle type="source" position={Position.Top} id="ts" className="!opacity-0" />
      <Handle type="target" position={Position.Top} id="tt" className="!opacity-0" />
      <Handle type="source" position={Position.Bottom} id="bs" className="!opacity-0" />
      <Handle type="target" position={Position.Bottom} id="bt" className="!opacity-0" />
      <div className="text-[13px] font-semibold leading-tight">{data.label}</div>
      <div className="text-[11px] text-stone-500 dark:text-stone-400">{data.agent}</div>
      <div className="mt-1 flex items-center gap-1.5 text-[10px]">
        {data.visits > 0 && <span className="rounded bg-stone-200/70 px-1.5 py-0.5 dark:bg-stone-800">visits {data.visits}</span>}
        {data.retries > 0 && <span className="rounded bg-amber-200/70 px-1.5 py-0.5 text-amber-900 dark:bg-amber-900 dark:text-amber-100">↻ {data.retries}</span>}
        {STATUS_TEXT[data.status] && <span className="ml-auto font-medium">{STATUS_TEXT[data.status]}</span>}
      </div>
    </div>
  );
}

type FlowEdgeData = { kind: "forward" | "feedback"; count: number; curve: number };

function PhaseEdge(props: EdgeProps<Edge<FlowEdgeData>>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const feedback = data?.kind === "feedback";
  const used = (data?.count ?? 0) > 0;
  let path: string, lx: number, ly: number;
  if (feedback && Math.abs(sourceY - targetY) < 40) {
    const bend = data?.curve ?? 90;
    const cx = (sourceX + targetX) / 2;
    const cy = Math.min(sourceY, targetY) - bend;
    path = `M ${sourceX} ${sourceY} Q ${cx} ${cy} ${targetX} ${targetY}`;
    lx = cx; ly = (sourceY + targetY) / 2 - bend / 2;
  } else {
    [path, lx, ly] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  }
  const color = used ? (feedback ? "#ea580c" : "#16a34a") : feedback ? "#a8a29e" : "#78716c";
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd}
        style={{ stroke: color, strokeWidth: used ? 2.6 : 1.4, strokeDasharray: feedback ? "6 5" : undefined, opacity: used || !feedback ? 1 : 0.55 }} />
      {used && (
        <EdgeLabelRenderer>
          <div style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
            className={`pointer-events-none absolute rounded-full px-1.5 text-[10px] font-semibold text-white ${feedback ? "bg-orange-600" : "bg-emerald-600"}`}>
            ×{data?.count}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { phase: PhaseNode };
const edgeTypes = { phase: PhaseEdge };

// handle choice per edge so feedback arcs don't overlap the forward arrows
function handles(src: { x: number; y: number }, dst: { x: number; y: number }, kind: string) {
  if (kind === "forward") return { sourceHandle: "r", targetHandle: "l" };
  if (src.y > dst.y + 20) return { sourceHandle: "ts", targetHandle: "bt" };
  if (dst.y > src.y + 20) return { sourceHandle: "bs", targetHandle: "tt" };
  return { sourceHandle: "ts", targetHandle: "tt" };
}

// node box size (tallest node: two-line label) and outer margin — keep in sync with PhaseNode
const NODE_W = 176;
const NODE_H = 92;
const PAD = 10;

/** Highest point of the same-row feedback arcs (quadratic curves drawn above the nodes in PhaseEdge). */
function arcTop(spec: GraphSpec): number {
  const pos = Object.fromEntries(spec.nodes.map((n) => [n.id, n]));
  let top = Infinity;
  for (const e of spec.edges) {
    const s = pos[e.source], t = pos[e.target];
    if (e.kind !== "feedback" || !s || !t || Math.abs(s.y - t.y) >= 40) continue;
    const bend = 40 + Math.abs(s.x - t.x) * 0.12;       // same formula as the edge curve
    top = Math.min(top, Math.min(s.y, t.y) - bend / 2 - 6); // quadratic peak + arrow/label
  }
  return top;
}

export default function ProcessGraph({ spec, view }: { spec: GraphSpec; view: RunView }) {
  const flow = useRef<ReactFlowInstance<Node<PhaseData>, Edge<FlowEdgeData>> | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  // bounding box of the whole diagram in flow coordinates
  const bounds = useMemo(() => {
    const xs = spec.nodes.map((n) => n.x), ys = spec.nodes.map((n) => n.y);
    const top = Math.min(Math.min(...ys), arcTop(spec)) - PAD;
    const bottom = Math.max(...ys) + NODE_H + PAD;
    return { x: Math.min(...xs) - PAD, y: top, width: Math.max(...xs) + NODE_W - Math.min(...xs) + 2 * PAD,
             height: bottom - top };
  }, [spec]);
  // panel height = exactly what the diagram needs at the current width (never more, never cut off)
  const height = width ? Math.max(140, Math.round((width * bounds.height) / bounds.width)) : 0;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (!height) return;
    const t = setTimeout(() => flow.current?.fitBounds(bounds, { padding: 0 }), 0);
    return () => clearTimeout(t);
  }, [height, bounds]);

  const nodes: Node<PhaseData>[] = useMemo(() => spec.nodes.map((n) => ({
    id: n.id, type: "phase", position: { x: n.x, y: n.y }, draggable: false,
    data: { label: n.label, agent: n.agent, status: view.status[n.id] ?? "idle", visits: view.visits[n.id] ?? 0,
            retries: view.edgeCounts[`${n.id}->${n.id}`] ?? 0 },
  })), [spec, view]);
  const pos = useMemo(() => Object.fromEntries(spec.nodes.map((n) => [n.id, n])), [spec]);
  const edges: Edge<FlowEdgeData>[] = useMemo(() => spec.edges.map((e) => {
    const s = pos[e.source], t = pos[e.target];
    const span = Math.abs(s.x - t.x);
    return {
      id: e.id, source: e.source, target: e.target, type: "phase", ...handles(s, t, e.kind),
      markerEnd: { type: "arrowclosed" as any, color: e.kind === "feedback" ? "#ea580c" : "#16a34a" },
      data: { kind: e.kind, count: view.edgeCounts[e.id] ?? 0, curve: e.kind === "feedback" ? 40 + span * 0.12 : 0 },
    };
  }), [spec, view, pos]);

  return (
    <div ref={box} className="w-full" style={{ height: height || 300 }}>
      <ReactFlow onInit={(inst) => { flow.current = inst; if (height) inst.fitBounds(bounds, { padding: 0 }); }} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} nodesConnectable={false} elementsSelectable={false}
        panOnDrag={false} panOnScroll={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false} preventScrolling={false} minZoom={0.1} maxZoom={4} proOptions={{ hideAttribution: true }}>
        <Background gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}
