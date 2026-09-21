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
    for attempt in data.get("scrape_attempts") or []:
        verdict = "blocked" if attempt.get("blocked") else "reachable"
        detail = f"- Scrape attempt: {attempt.get('site')} → {verdict}"
        response = attempt.get("response")
        if response:
            detail += (
                f" (HTTP {response.get('status_code')} {response.get('reason', '')}, "
                f"{response.get('elapsed_ms')} ms)"
            )
        elif attempt.get("error"):
            detail += f" ({attempt['error']})"
        lines.append(detail + ".")
        robots = attempt.get("robots_txt")
        if robots and robots.get("found"):
            rule_count = len(robots.get("disallow_rules_sample") or [])
            lines.append(
                f"  - robots.txt (HTTP {robots.get('status_code')}): "
                f"{'allows' if robots.get('allowed') else 'disallows'} this URL for our "
                f"user agent ({rule_count} 'Disallow' rule(s) under 'User-agent: *')."
            )
        elif robots and robots.get("error"):
            lines.append(f"  - robots.txt check failed: {robots['error']}")
    opendata = data.get("opendata") or {}
    if opendata.get("query"):
        lines.append(
            f"- Search: \"{opendata['query']}\" → {opendata.get('total_found', 0)} "
            "candidate(s) found on opendata.swiss."
        )
    download = data.get("download") or {}
    if download.get("success"):
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


def _scrape_attempts_html(scrape_attempts: list[dict]) -> str:
    if not scrape_attempts:
        return ""

    parts = [f'<h3 class="preview-heading">Live scraping attempts ({len(scrape_attempts)})</h3>']
    parts.append('<ul class="dataset-list">')
    for attempt in scrape_attempts:
        verdict = "blocked" if attempt.get("blocked") else "reachable"
        parts.append('<li class="dataset-item">')
        parts.append(f'<p class="sketch-title">{_esc(attempt.get("site"))} — {verdict}</p>')

        lines = [f'GET {attempt.get("url")}']
        robots = attempt.get("robots_txt")
        if robots:
            if not robots.get("found"):
                lines.append(
                    f"robots.txt: request failed ({robots['error']})"
                    if robots.get("error")
                    else f"robots.txt: not found (HTTP {robots.get('status_code', '?')})"
                )
            else:
                rules = robots.get("disallow_rules_sample") or []
                lines.append(
                    f"robots.txt (HTTP {robots.get('status_code')}): "
                    f"{'allows' if robots.get('allowed') else 'disallows'} this URL for our "
                    f"user agent ({len(rules)} 'Disallow' rule(s) under 'User-agent: *')"
                )
                if rules:
                    lines.append(f"  e.g. Disallow: {', '.join(rules[:3])}")

        if attempt.get("error"):
            lines.append(f"Request failed: {attempt['error']}")
        elif attempt.get("response"):
            r = attempt["response"]
            lines.append(
                f'HTTP {r.get("status_code")} {r.get("reason")} — {r.get("elapsed_ms")} ms, '
                f'{r.get("content_bytes", 0):,} bytes'
            )
            if r.get("redirected"):
                lines.append(f'Redirected to: {r.get("final_url")}')
            headers = r.get("headers") or {}
            if headers:
                lines.append(
                    "Response headers: " + " · ".join(f"{k}: {v}" for k, v in headers.items())
                )

        parts.append(f'<pre class="sketch-ascii">{_esc(chr(10).join(lines))}</pre>')
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def _collecting_card_html(data: dict) -> str:
    download = data.get("download") or {}
    scrape_attempts = data.get("scrape_attempts") or []
    if not download and not scrape_attempts:
        return ""

    parts = ['<article class="phase-card">']
    parts.append(_scrape_attempts_html(scrape_attempts))

    if not download:
        parts.append("</article>")
        return "".join(parts)

    if download.get("fallback"):
        heading = "⚠️ Falling back to best available data (not individual-level)"
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

    if download.get("success"):
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
