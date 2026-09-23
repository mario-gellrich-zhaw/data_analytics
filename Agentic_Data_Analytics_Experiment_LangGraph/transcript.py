"""Renders one run's recorded history (see server.py's `DemoRun.history`)
to disk once a run ends: a human-readable Markdown transcript, and a
self-contained HTML page that looks like the live chat — same CSS classes
`static/app.js` builds in the browser (bubbles, phase dividers, result
cards), so a saved run can be opened and read later exactly as it looked
live, offline, with no server running.
"""

import html
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple


class PageAssets(NamedTuple):
    """The live page's own assets, reused so a saved run looks like it —
    read fresh by the caller each time, so an edit to static/style.css or
    static/index.html is picked up without a restart."""

    css: str
    index_html: str
    svg_markup: str

OUTCOME_HEADLINES = {
    "completed": "✅ Completed all 4 steps",
    "completed_with_fallback": "⚠️ Completed with a fallback dataset (not individual-level)",
    "incomplete": "⚠️ Incomplete — no individual-apartment-level dataset confirmed",
    "stopped": "⏹️ Stopped by the user",
    "timed_out": "⏱️ Hit the safety net before finishing",
    "error": "❌ Errored",
    "unknown": "❔ Unknown",
}


def _objective_headline(business_objective: str) -> str:
    # The opener varies run to run (see server.py's BUSINESS_OBJECTIVE_OPENERS);
    # the actual goal sentence that follows it doesn't, so anchor on that
    # instead of assuming a fixed prefix length.
    marker = "Our goal"
    idx = business_objective.find(marker)
    if idx == -1:
        return business_objective.split(". ")[0].strip()
    remainder = business_objective[idx:]
    end = remainder.find(". ")
    return (remainder[: end + 1] if end != -1 else remainder).strip()


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# --- Markdown ----------------------------------------------------------


def _render_step3_result_lines(data: dict) -> list[str]:
    lines: list[str] = []
    opendata = data.get("opendata") or {}
    if opendata.get("query"):
        lines.append(
            f"- Search: \"{opendata['query']}\" → {opendata.get('total_found', 0)} "
            "candidate(s) found on opendata.swiss."
        )
    download = data.get("download") or {}
    if download.get("scraped"):
        lines.append(
            f"- Scraped: {download.get('rows', 0):,} listings from "
            f"{download.get('dataset_organization')} by {download.get('dataset_title')}."
        )
    elif download.get("success"):
        title = download.get("dataset_title")
        if title:
            org = download.get("dataset_organization") or "opendata.swiss"
            url = download.get("dataset_url")
            source = f"[{title}]({url})" if url else title
            lines.append(f"- Downloaded: {source} ({org}).")
        bytes_ = download.get("bytes")
        if bytes_ is not None:
            lines.append(f"- {bytes_:,} bytes saved ({download.get('format', 'file')}).")
    elif download:
        lines.append(f"- Download failed: {download.get('error', 'unknown error')}")
    preview = data.get("preview") or {}
    if preview.get("columns"):
        n_rows = len(preview.get("rows") or [])
        lines.append(
            f"- Preview: {n_rows} rows, columns: {', '.join(map(str, preview['columns']))}."
        )
    return lines


def _render_step4_result_lines(data: dict) -> list[str]:
    lines: list[str] = []
    profile = data.get("profile") or {}
    if "n_rows" in profile:
        n_missing_cols = len(profile.get("missing_values") or {})
        lines.append(
            f"- Profiled: {profile['n_rows']:,} rows, {profile.get('n_columns')} columns, "
            f"{profile.get('duplicate_rows', 0):,} duplicate rows, {n_missing_cols} "
            "columns with missing values."
        )
    clean = data.get("clean") or {}
    if "rows_after" in clean:
        lines.append(
            f"- Cleaned: {clean.get('rows_before', 0):,} → {clean.get('rows_after', 0):,} rows "
            f"({clean.get('dropped_duplicates', 0):,} duplicates, "
            f"{clean.get('dropped_missing', 0):,} missing-value rows dropped)."
        )
    store = data.get("store") or {}
    if store.get("table_name"):
        lines.append(
            f"- Stored {store.get('rows_stored', 0):,} rows in table "
            f"\"{store['table_name']}\" ({store.get('db_bytes', 0):,} bytes)."
        )
    sql = data.get("sql") or {}
    if sql.get("query"):
        n_rows = len(sql.get("rows") or [])
        lines.append(f"- Verification query: `{sql['query']}` → {n_rows} row(s) returned.")
    sketch = data.get("sketch") or {}
    if sketch.get("title"):
        lines.append(f"- Sketch: \"{sketch['title']}\" ({sketch.get('kind', 'ascii')}).")
    return lines


def _describe_request(entry: dict) -> str:
    if entry.get("blocked"):
        return f"✖ {entry.get('url')}\n    blocked: {entry['blocked']}"
    if entry.get("kind") == "robots.txt":
        return f"· {entry.get('url')} → HTTP {entry.get('status')}"
    return f"✔ GET {entry.get('url')} → HTTP {entry.get('status')} ({entry.get('elapsed_ms')} ms)"


def _run_outcome(data: dict) -> str:
    if data.get("timed_out"):
        return "timed out"
    if data.get("exit_code") == 0:
        return "exit code 0"
    return f"crashed (exit code {data.get('exit_code')})"


def _pages_fetched(data: dict) -> int:
    return sum(
        1 for e in data.get("requests") or [] if e.get("kind") == "page" and not e.get("blocked")
    )


def _render_scraper_code_lines(data: dict) -> list[str]:
    check = (
        "check passed"
        if data.get("check_passed")
        else "check failed: " + "; ".join(data.get("problems") or [])
    )
    return [
        f"**Data Analyst wrote `scraper_v{data['version']}.py`** ({data.get('lines')} lines, {check}):",
        "```python",
        data.get("code", "").rstrip(),
        "```",
    ]


def _render_scraper_run_lines(data: dict) -> list[str]:
    lines = [
        f"**Ran `scraper_v{data['version']}.py`:** {_run_outcome(data)}, "
        f"{_pages_fetched(data)} page(s) fetched, {data.get('rows_saved', 0)} row(s) saved, "
        f"{data.get('elapsed_seconds')} s."
    ]
    if data.get("accepted_as_dataset"):
        lines.append("- Accepted as the current dataset.")
    elif data.get("rejected_because"):
        lines.append(f"- Not accepted as a dataset: {data['rejected_because']}")
    if data.get("requests"):
        lines += ["```", *(_describe_request(e) for e in data["requests"]), "```"]
    if data.get("output_tail"):
        lines += ["Printed output:", "```", data["output_tail"].rstrip(), "```"]
    return lines


def _render_phase_result_lines(step: int, data: dict) -> list[str]:
    if step == 3:
        return _render_step3_result_lines(data)
    if step == 4:
        return _render_step4_result_lines(data)
    return []


def render_markdown(
    history: list[dict], started_at: datetime, ended_at: datetime, outcome: dict
) -> str:
    """Render one run's recorded phases/turns as a human-readable Markdown transcript."""
    turn_count = sum(1 for entry in history if entry["kind"] == "turn")
    objective = next((e["goal"] for e in history if e["kind"] == "phase"), "")
    status = outcome.get("status", "unknown")

    lines = [
        "# Agentic Conversation — Data Analytics Process Model",
        f"**Run started:** {started_at:%Y-%m-%d %H:%M:%S}",
        f"**Outcome:** {OUTCOME_HEADLINES.get(status, status)}",
        f"**Turns:** {turn_count} over {_format_duration((ended_at - started_at).total_seconds())}",
    ]
    if objective:
        lines.append(f"**Objective:** {_objective_headline(objective)}")
    lines.append("")

    for entry in history:
        if entry["kind"] == "phase":
            header = f"## Step {entry['step']}/4 · {entry['step_label']}"
            if entry["sub_label"]:
                header += f" — {entry['sub_label']}"
            lines.append(header)
            lines.append(f"*Goal: {entry['goal']}*")
            lines.append("")
        elif entry["kind"] == "turn":
            suffix = " _(used a real tool)_" if entry["action"] else ""
            lines.append(f"**{entry['speaker']}:** {entry['text']}{suffix}")
            lines.append("")
        elif entry["kind"] == "scraper_code":
            lines.extend(_render_scraper_code_lines(entry["data"]))
            lines.append("")
        elif entry["kind"] == "scraper_run":
            lines.extend(_render_scraper_run_lines(entry["data"]))
            lines.append("")
        elif entry["kind"] == "phase_result":
            result_lines = _render_phase_result_lines(entry["step"], entry["data"])
            if result_lines:
                lines.append("**Real results:**")
                lines.extend(result_lines)
                lines.append("")

    lines.append("## Run ended")
    lines.append(f"**Status:** {OUTCOME_HEADLINES.get(status, status)}")
    if outcome.get("message"):
        lines.append(outcome["message"])

    return "\n".join(lines).rstrip() + "\n"


# --- HTML ----------------------------------------------------------------
# Mirrors static/app.js's DOM-building functions (addBubble, addPhaseDivider,
# buildCollectingCard, buildPreparingCard) so a saved run, opened later with
# no server running, looks like the live chat did.

_DOT_VIEWER_SCRIPT = """
<script type="module">
  const boxes = document.querySelectorAll(".sketch-dot[data-dot]");
  if (boxes.length) {
    const fallback = (box) => {
      const pre = document.createElement("pre");
      pre.className = "sketch-ascii";
      pre.textContent = box.dataset.dot;
      box.replaceChildren(pre);
    };
    import("https://cdn.jsdelivr.net/npm/@viz-js/viz@3/lib/viz-standalone.mjs")
      .then((mod) => mod.instance())
      .then((viz) => {
        boxes.forEach((box) => {
          try {
            box.replaceChildren(viz.renderSVGElement(box.dataset.dot));
          } catch (err) {
            fallback(box);
          }
        });
      })
      .catch(() => boxes.forEach(fallback));
  }
</script>
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _speaker_class(speaker: str) -> str:
    if speaker == "Product Manager":
        return "pm"
    if speaker == "Data Engineer":
        return "engineer"
    return "analyst"


def _bubble_html(speaker: str, text: str, used_tool: bool) -> str:
    css_class = f"bubble {_speaker_class(speaker)}" + (" action" if used_tool else "")
    return (
        f'<div class="{css_class}">'
        f'<span class="speaker">{_esc(speaker)}</span>'
        f"<p>{_esc(text)}</p>"
        "</div>"
    )


def _phase_divider_html(step: int, step_label: str, sub_label: str) -> str:
    text = f"Step {step}/4 · {step_label}"
    if sub_label:
        text += f" — {sub_label}"
    return f'<div class="phase-divider">{_esc(text)}</div>'


def _data_table_html(columns: list, rows: list) -> str:
    if not columns or not rows:
        return ""

    def cell(value) -> str:
        text = "" if value is None else str(value)
        return _esc(text[:40] + "…" if len(text) > 40 else text)

    head = "".join(f"<th>{_esc(col)}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>" for row in rows
    )
    return (
        '<div class="table-scroll"><table class="preview-table">'
        f"<tr>{head}</tr>{body}</table></div>"
    )


def _stat_tile_html(label: str, value) -> str:
    return (
        '<div class="stat-tile">'
        f'<div class="stat-value">{_esc(value)}</div>'
        f'<div class="stat-label">{_esc(label)}</div>'
        "</div>"
    )


def _sketch_html(sketch: dict) -> str:
    if not sketch or not sketch.get("content"):
        return ""
    parts = ['<div class="sketch">']
    if sketch.get("title"):
        parts.append(f'<div class="sketch-title">{_esc(sketch["title"])}</div>')
    if sketch.get("kind") == "dot":
        # Rendered client-side on open (see _DOT_VIEWER_SCRIPT) — same
        # Graphviz-via-CDN approach app.js uses live, with the same
        # plain-text fallback if that CDN can't be reached.
        dot_source = _esc(sketch["content"])
        parts.append(f'<div class="sketch-dot" data-dot="{dot_source}">Rendering diagram…</div>')
    else:
        parts.append(f'<pre class="sketch-ascii">{_esc(sketch["content"])}</pre>')
    parts.append("</div>")
    return "".join(parts)


def _scraper_code_card_html(data: dict) -> str:
    check = (
        "Code check passed (only allowed imports; web access only via scraper_kit)."
        if data.get("check_passed")
        else "Code check failed: " + "; ".join(data.get("problems") or [])
    )
    return (
        '<article class="phase-card">'
        f"<h2>🧑‍💻 scraper_v{_esc(data['version'])}.py — written by the Data Analyst "
        f"({_esc(data.get('lines'))} lines)</h2>"
        f'<p class="result-meta">{_esc(check)}</p>'
        f'<pre class="sketch-ascii code-block">{_esc(data.get("code"))}</pre>'
        "</article>"
    )


def _scraper_run_card_html(data: dict) -> str:
    outcome = _run_outcome(data)
    if data.get("timed_out"):
        outcome = f"⏱️ {outcome}"
    elif data.get("exit_code") != 0:
        outcome = f"❌ {outcome}"
    parts = ['<article class="phase-card">']
    parts.append(f"<h2>▶️ Ran scraper_v{_esc(data['version'])}.py — {_esc(outcome)}</h2>")
    parts.append('<div class="stat-grid">')
    parts.append(_stat_tile_html("pages fetched", _pages_fetched(data)))
    parts.append(_stat_tile_html("rows saved", data.get("rows_saved", 0)))
    parts.append(_stat_tile_html("seconds", data.get("elapsed_seconds")))
    parts.append("</div>")
    if data.get("accepted_as_dataset"):
        parts.append(
            '<p class="result-meta">✅ Accepted as the current dataset '
            "(looks like individual listings).</p>"
        )
    elif data.get("rejected_because"):
        parts.append(
            f'<p class="result-meta">⚠️ Not accepted as a dataset: '
            f'{_esc(data["rejected_because"])}</p>'
        )
    requests_log = data.get("requests") or []
    if requests_log:
        parts.append(f'<h3 class="preview-heading">Requests ({len(requests_log)})</h3>')
        text = "\n".join(_describe_request(e) for e in requests_log)
        parts.append(f'<pre class="sketch-ascii">{_esc(text)}</pre>')
    if data.get("output_tail"):
        parts.append('<h3 class="preview-heading">Printed output</h3>')
        parts.append(f'<pre class="sketch-ascii code-block">{_esc(data["output_tail"])}</pre>')
    sample_rows = data.get("sample_rows") or []
    if sample_rows:
        parts.append(f'<h3 class="preview-heading">First {len(sample_rows)} scraped rows</h3>')
        parts.append(_data_table_html(data.get("columns") or [], sample_rows))
    parts.append("</article>")
    return "".join(parts)


def _collecting_card_html(data: dict) -> str:
    download = data.get("download") or {}
    if not download:
        return ""

    parts = ['<article class="phase-card">']

    if download.get("fallback"):
        heading = "⚠️ Falling back to best available data (not individual-level)"
    elif download.get("scraped"):
        heading = "✅ Real data scraped by the agents' own code"
    elif download.get("success"):
        heading = "✅ Real data downloaded"
    else:
        heading = "⚠️ Download didn't complete"

    parts.append(f"<h2>{_esc(heading)}</h2>")

    if download.get("dataset_title"):
        url = download.get("dataset_url") or "#"
        org = download.get("dataset_organization") or "opendata.swiss"
        parts.append(
            '<p class="result-meta">Source: '
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">'
            f'{_esc(download["dataset_title"])}</a> ({_esc(org)})</p>'
        )

    if download.get("fallback"):
        reason = download.get("reason_rejected") or "it did not look individual-level"
        parts.append(
            f'<p class="result-meta">Set aside earlier because {_esc(reason)}, but no '
            "individual-apartment-level dataset was ever confirmed — used as the best "
            "real option found.</p>"
        )

    if download.get("scraped"):
        parts.append(
            f'<p class="result-meta">Scraped: {download.get("rows", 0):,} listings, '
            f'{download.get("bytes", 0):,} bytes saved (CSV).</p>'
        )
    elif download.get("success"):
        parts.append(
            f'<p class="result-meta">Download: {download.get("bytes", 0):,} bytes saved '
            f'({_esc(download.get("format") or "file")}).</p>'
        )
    else:
        parts.append(f'<p class="result-meta">Download failed: {_esc(download.get("error"))}</p>')

    preview = data.get("preview") or {}
    rows = preview.get("rows") or []
    if rows:
        parts.append(f'<h3 class="preview-heading">First {len(rows)} rows (raw)</h3>')
        parts.append(_data_table_html(preview.get("columns") or [], rows))

    parts.append("</article>")
    return "".join(parts)


def _preparing_card_html(data: dict) -> str:
    profile = data.get("profile") or {}
    clean = data.get("clean") or {}
    store = data.get("store") or {}
    sql = data.get("sql") or {}
    preview = data.get("preview") or {}

    if not ("n_rows" in profile or "rows_after" in clean or store.get("table_name")):
        return ""

    heading = (
        "✅ Data cleaned and stored in a real SQLite database"
        if store.get("table_name")
        else "⚠️ Preparing & storing didn't finish"
    )
    parts = ["<article class=\"phase-card\">", f"<h2>{_esc(heading)}</h2>"]

    if "n_rows" in profile:
        parts.append('<div class="stat-grid">')
        parts.append(_stat_tile_html("rows (raw)", f'{profile["n_rows"]:,}'))
        parts.append(_stat_tile_html("columns", profile.get("n_columns", 0)))
        parts.append(_stat_tile_html("duplicate rows", f'{profile.get("duplicate_rows", 0):,}'))
        parts.append(
            _stat_tile_html(
                "cols with missing values", len(profile.get("missing_values") or {})
            )
        )
        parts.append("</div>")

        dtypes = profile.get("dtypes") or {}
        if dtypes:
            parts.append('<h3 class="preview-heading">Column data types</h3>')
            parts.append('<ul class="dataset-list">')
            for col, dtype in list(dtypes.items())[:10]:
                parts.append(f'<li class="dataset-item">{_esc(col)}: {_esc(dtype)}</li>')
            parts.append("</ul>")

    if "rows_after" in clean:
        parts.append(
            '<p class="result-meta">Cleaned: '
            f'{clean.get("rows_before", 0):,} → {clean.get("rows_after", 0):,} rows '
            f'({clean.get("dropped_duplicates", 0):,} duplicates, '
            f'{clean.get("dropped_missing", 0):,} missing-value rows dropped).</p>'
        )

    if store.get("table_name"):
        db_path = _esc(store.get("db_path") or "rental_data.db")
        parts.append(
            '<p class="result-meta">Stored '
            f'{store.get("rows_stored", 0):,} rows in table "{_esc(store["table_name"])}" '
            f"({store.get('db_bytes', 0):,} bytes, {db_path}).</p>"
        )

    rows = preview.get("rows") or []
    if rows:
        parts.append(f'<h3 class="preview-heading">First {len(rows)} rows (cleaned)</h3>')
        parts.append(_data_table_html(preview.get("columns") or [], rows))

    if sql.get("query"):
        parts.append('<h3 class="preview-heading">Verification query</h3>')
        parts.append(f'<p class="result-meta">{_esc(sql["query"])}</p>')
        parts.append(_data_table_html(sql.get("columns") or [], sql.get("rows") or []))

    sketch_html = _sketch_html(data.get("sketch") or {})
    if sketch_html:
        parts.append(sketch_html)

    parts.append("</article>")
    return "".join(parts)


def _render_body_html(history: list[dict]) -> str:
    parts: list[str] = []
    for entry in history:
        if entry["kind"] == "phase":
            parts.append(
                _phase_divider_html(entry["step"], entry["step_label"], entry["sub_label"])
            )
        elif entry["kind"] == "turn":
            parts.append(_bubble_html(entry["speaker"], entry["text"], entry["action"]))
        elif entry["kind"] == "scraper_code":
            parts.append(_scraper_code_card_html(entry["data"]))
        elif entry["kind"] == "scraper_run":
            parts.append(_scraper_run_card_html(entry["data"]))
        elif entry["kind"] == "phase_result":
            if entry["step"] == 3:
                parts.append(_collecting_card_html(entry["data"]))
            elif entry["step"] == 4:
                parts.append(_preparing_card_html(entry["data"]))
    return "".join(parts)


def _landing_header_html(index_html: str, svg_markup: str) -> str:
    """Pull the `<header class="hero">` block out of static/index.html
    (the eyebrow, title, tagline, speaker legend, process diagram) so a
    saved run opens with the same framing the live page has. The
    interactive Start button is dropped (meaningless on a static page),
    and the diagram's `<img src="...">` is replaced with the SVG's own
    markup inlined, so the page stays viewable with no server running —
    the whole point of saving it as a self-contained file."""
    start_tag = '<header class="hero">'
    start = index_html.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)
    end = index_html.find("</header>", start)
    if end == -1:
        return ""
    inner = index_html[start:end]
    inner = re.sub(r'\s*<button id="start-btn">.*?</button>\s*', "\n", inner, flags=re.DOTALL)
    inner = re.sub(
        r'<img\s+src="/static/data_analytics_process_model\.svg"[^>]*>',
        svg_markup,
        inner,
        flags=re.DOTALL,
    )
    return inner.strip()


def render_html(
    history: list[dict],
    started_at: datetime,
    ended_at: datetime,
    outcome: dict,
    assets: PageAssets,
) -> str:
    """Render one run as a self-contained HTML page: the landing page's own
    header, then the same CSS classes static/app.js builds live for the
    chat itself. The stylesheet and process-diagram SVG are both inlined,
    so the file opens correctly on its own, no server or network needed
    (except to render a Graphviz "dot" sketch, if the run included one)."""
    turn_count = sum(1 for entry in history if entry["kind"] == "turn")
    objective = next((e["goal"] for e in history if e["kind"] == "phase"), "")
    status = outcome.get("status", "unknown")
    headline = OUTCOME_HEADLINES.get(status, status)
    meta_line = (
        f"{started_at:%Y-%m-%d %H:%M:%S} · "
        f"{_format_duration((ended_at - started_at).total_seconds())} · {turn_count} turns"
    )
    objective_line = _objective_headline(objective) if objective else ""
    objective_html = f'<p class="tagline">{_esc(objective_line)}</p>' if objective_line else ""
    landing_header = _landing_header_html(assets.index_html, assets.svg_markup)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agentic conversation — {_esc(f"{started_at:%Y-%m-%d %H:%M}")}</title>
<style>
{assets.css}
</style>
</head>
<body>
<main>
  <header class="hero">
    {landing_header}
    <span class="eyebrow">Saved conversation</span>
    <h1>{_esc(headline)}</h1>
    <p class="tagline">{_esc(meta_line)}</p>
    {objective_html}
  </header>
  <div id="chat">
    {_render_body_html(history)}
  </div>
</main>
{_DOT_VIEWER_SCRIPT}
</body>
</html>
"""


# --- Saving ----------------------------------------------------------------


def save(
    history: list[dict], started_at: datetime, outcome: dict, directory: Path, assets: PageAssets
) -> None:
    """Write both the Markdown and HTML transcripts for one run. Best-effort
    only: a failure here should never break the live demo or leave the SSE
    stream hanging."""
    if not history:
        return
    try:
        ended_at = datetime.now()
        directory.mkdir(exist_ok=True)
        stem = f"conversation_{started_at:%Y%m%d_%H%M%S}"
        (directory / f"{stem}.md").write_text(
            render_markdown(history, started_at, ended_at, outcome), encoding="utf-8"
        )
        (directory / f"{stem}.html").write_text(
            render_html(history, started_at, ended_at, outcome, assets), encoding="utf-8"
        )
    except OSError:
        pass
