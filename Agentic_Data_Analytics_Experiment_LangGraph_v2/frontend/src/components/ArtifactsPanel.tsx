import { useEffect, useMemo, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { api, type ArtifactFile } from "../api";

const PINNED: { path: string; label: string }[] = [
  { path: "report/final_report.html", label: "Final report" },
  { path: "data_card.md", label: "Data card" },
  { path: "eda/eda_report.md", label: "EDA report" },
  { path: "raw/sources.json", label: "Sources & licenses" },
  { path: "evaluation/evaluation.md", label: "Evaluation" },
  { path: "objective_spec.json", label: "Objective spec" },
  { path: "data_requirements.json", label: "Data requirements" },
];

function dirOf(path: string) {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i + 1);
}

function resolveRel(base: string, src: string) {
  const parts = (base + src).split("/");
  const out: string[] = [];
  for (const p of parts) {
    if (p === "..") out.pop();
    else if (p !== "." && p !== "") out.push(p);
  }
  return out.join("/");
}

function SourcesTable({ text }: { text: string }) {
  let rows: any[] = [];
  try { rows = JSON.parse(text); } catch { return <pre className="text-xs">{text}</pre>; }
  return (
    <table className="w-full text-left text-xs">
      <thead><tr className="border-b border-stone-200 dark:border-stone-800"><th className="py-1 pr-2">File</th><th className="pr-2">Source</th><th className="pr-2">License</th><th>Retrieved</th></tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-stone-100 align-top dark:border-stone-900">
            <td className="py-1 pr-2 font-mono">{r.path}</td>
            <td className="pr-2"><a className="text-blue-600 underline dark:text-blue-400" href={r.url} target="_blank" rel="noreferrer">{r.source_name || r.url}</a></td>
            <td className={`pr-2 ${!r.license || /unknown/i.test(r.license) ? "text-amber-600" : ""}`}>{r.license || "unknown"}</td>
            <td className="whitespace-nowrap">{(r.retrieved_at || "").replace("T", " ").slice(0, 16)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Viewer({ runId, path }: { runId: string; path: string }) {
  const [text, setText] = useState<string>("");
  const url = api.fileUrl(runId, path);
  const isImg = path.endsWith(".png");
  const isHtml = path.endsWith(".html");
  useEffect(() => {
    if (isImg || isHtml) return;
    setText("loading…");
    api.fileText(runId, path).then(setText).catch((e) => setText(String(e)));
  }, [runId, path, isImg, isHtml]);
  if (isImg) return <img src={url} alt={path} className="max-w-full rounded border border-stone-200 dark:border-stone-800" />;
  if (isHtml) return (
    <div className="space-y-2">
      <a href={url} target="_blank" rel="noreferrer" className="inline-block rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white">Open report in new tab ↗</a>
      <iframe src={url} sandbox="" className="h-[560px] w-full rounded border border-stone-200 bg-white dark:border-stone-800" title="report" />
    </div>
  );
  if (path.endsWith("sources.json")) return <SourcesTable text={text} />;
  if (path.endsWith(".md")) {
    const renderer = new marked.Renderer();
    const base = dirOf(path);
    renderer.image = ({ href, text: alt }) =>
      `<img alt="${alt}" src="${href.startsWith("http") || href.startsWith("data:") ? href : api.fileUrl(runId, resolveRel(base, href))}" />`;
    return <div className="prose-md" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(text, { renderer, async: false }) as string) }} />;
  }
  return <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap font-mono text-[11px]">{text}</pre>;
}

export default function ArtifactsPanel({ runId, version }: { runId: string; version: number }) {
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { api.artifacts(runId).then(setFiles).catch(() => setFiles([])); }, [runId, version]);
  useEffect(() => { setSelected(null); }, [runId]);
  const have = useMemo(() => new Set(files.map((f) => f.path)), [files]);
  const charts = files.filter((f) => f.path.endsWith(".png"));
  const groups = useMemo(() => {
    const g: Record<string, ArtifactFile[]> = {};
    for (const f of files) {
      if (f.path.endsWith(".png")) continue;
      const top = f.path.includes("/") ? f.path.split("/")[0] : "(root)";
      (g[top] ??= []).push(f);
    }
    return g;
  }, [files]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap gap-1.5 border-b border-stone-200 px-3 py-2 dark:border-stone-800">
        {PINNED.filter((p) => have.has(p.path)).map((p) => (
          <button key={p.path} onClick={() => setSelected(p.path)}
            className={`rounded-full px-2.5 py-0.5 text-xs ${selected === p.path ? "bg-blue-600 text-white" : "bg-stone-100 dark:bg-stone-800"}`}>{p.label}</button>
        ))}
        {charts.length > 0 && (
          <button onClick={() => setSelected("__charts__")}
            className={`rounded-full px-2.5 py-0.5 text-xs ${selected === "__charts__" ? "bg-blue-600 text-white" : "bg-stone-100 dark:bg-stone-800"}`}>Charts ({charts.length})</button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {selected === "__charts__" ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {charts.map((c) => (
              <figure key={c.path}>
                <img src={api.fileUrl(runId, c.path)} alt={c.path} className="w-full rounded border border-stone-200 dark:border-stone-800" />
                <figcaption className="mt-1 font-mono text-[10px] text-stone-500">{c.path}</figcaption>
              </figure>
            ))}
          </div>
        ) : selected ? (
          <div>
            <button onClick={() => setSelected(null)} className="mb-2 text-xs text-blue-600 dark:text-blue-400">← all files</button>
            <div className="mb-2 font-mono text-xs text-stone-500">{selected}</div>
            <Viewer runId={runId} path={selected} />
          </div>
        ) : files.length === 0 ? (
          <div className="py-10 text-center text-sm text-stone-400">No artifacts yet.</div>
        ) : (
          <div className="space-y-3">
            {Object.entries(groups).map(([dir, fs]) => (
              <div key={dir}>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-stone-500">{dir}</div>
                <ul className="space-y-0.5">
                  {fs.map((f) => (
                    <li key={f.path}>
                      <button onClick={() => setSelected(f.path)} className="flex w-full justify-between gap-2 rounded px-1 text-left font-mono text-xs hover:bg-stone-100 dark:hover:bg-stone-800">
                        <span className="truncate">{f.path}</span><span className="shrink-0 text-stone-400">{(f.bytes / 1024).toFixed(1)} KB</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
