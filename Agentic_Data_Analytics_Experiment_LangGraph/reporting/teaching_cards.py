"""The teaching cards of a saved transcript, as Markdown and HTML: real
examples an agent showed the class (show_to_class), a look at earlier runs
when stuck, and the app's walkthrough of how a preparation step derived
its columns — mirroring static/app.js's buildExhibitCard,
buildHistoryLookupCard and buildWalkthroughCard."""

import re

from reporting.html_parts import data_table_html as _data_table_html
from reporting.html_parts import esc as _esc


def _exhibit_heading(data: dict) -> str:
    """The heading of a show_to_class exhibit card."""
    kind = data.get("kind")
    if kind == "single_case" and data.get("auto"):
        return f"What this step did to one listing — {data.get('listing_id')}"
    if kind == "single_case":
        dropped = " (dropped by the cleaning)" if data.get("dropped") else ""
        return f"One listing up close — {data.get('listing_id')}{dropped}"
    if kind == "rows":
        shown = len(data.get("rows") or [])
        return f"Real rows from {data.get('file')} ({shown} of {data.get('total_rows')})"
    lines = data.get("lines") or []
    start = data.get("start_line", 1)
    end = start + len(lines) - 1
    return f"{data.get('file')}, lines {start}–{end} of {data.get('total_lines')}"


def _numbered_code(data: dict) -> str:
    """A code exhibit's lines, numbered as in the script."""
    lines = data.get("lines") or []
    start = data.get("start_line", 1)
    width = len(str(start + len(lines) - 1))
    return "\n".join(f"{str(start + i).rjust(width)}  {line}" for i, line in enumerate(lines))


def _words_re(words) -> re.Pattern | None:
    """One pattern for all words, longest first — so 'balconygarden' is
    marked once, not 'balcony' inside it again."""
    words = sorted({w for w in words or () if w}, key=len, reverse=True)
    return re.compile("|".join(map(re.escape, words)), re.IGNORECASE) if words else None


MD_CELL_CHARS = 160  # a whole listing description would swamp a Markdown table row


def _md_cell(value, highlights=()) -> str:
    text = "—" if value is None else " ".join(str(value).split())
    if len(text) > MD_CELL_CHARS:
        text = text[:MD_CELL_CHARS].rstrip() + "…"
    text = text.replace("|", "\\|")
    pattern = _words_re(highlights)
    return pattern.sub(lambda m: f"**{m.group(0)}**", text) if pattern else text


def _mark_html(text: str, highlights) -> str:
    """HTML-escaped text with the derived-from words wrapped in <mark>."""
    escaped = _esc(text)
    pattern = _words_re(_esc(w) for w in highlights or ())
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped) if pattern else escaped


def exhibit_lines(data: dict) -> list[str]:
    """A show_to_class exhibit as Markdown."""
    icon = "📌" if data.get("auto") else "🔎 Shown to the class:"
    lines = [f"**{icon} {_exhibit_heading(data)}**"]
    if data.get("caption"):
        lines.append(f"*{data['caption']}*")
    lines.append("")
    if data.get("kind") == "single_case":
        lines += ["| column | as collected | now | |", "|---|---|---|---|"]
        for field in data.get("fields") or []:
            status = "" if field["status"] == "same" else field["status"]
            marks = data.get("highlights") or ()
            lines.append(
                f"| {field['column']} | {_md_cell(field['before'], marks)} | "
                f"{_md_cell(field['after'], marks)} | {status} |"
            )
    elif data.get("kind") == "rows":
        columns = data.get("columns") or []
        lines += ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
        lines += [
            "| " + " | ".join(_md_cell(v) for v in row) + " |" for row in data.get("rows") or []
        ]
    else:
        lines += ["```python", _numbered_code(data), "```"]
    return lines


def history_lookup_lines(data: dict) -> list[str]:
    """A look at earlier runs (when stuck) as Markdown."""
    found = ", ".join(data.get("found") or []) or "no working script"
    lines = [
        f"**📚 Stuck at the {data.get('stage')} step — looked at "
        f"{data.get('runs_searched')} earlier run(s):** found {found}."
    ]
    lines += [f"- {problem}" for problem in data.get("problems") or []]
    return lines


def _walkthrough_title(data: dict) -> str:
    verb = "changed the data" if data.get("stage") == "clean" else "derived its new columns"
    return f"How {data.get('script')} {verb}"


def _feature_summary(feature: dict) -> str:
    if feature.get("evidence"):
        return f"{feature['summary']} — decided by the words: {', '.join(feature['evidence'])}"
    return feature["summary"]


def _excerpt_label(data: dict, feature: dict) -> str:
    spans = [
        f"{ex['start_line']}–{ex['start_line'] + len(ex['lines']) - 1}" for ex in feature["code"]
    ]
    return f"The agents' code ({data.get('script')}, lines {', '.join(spans)})"


def walkthrough_lines(data: dict) -> list[str]:
    """How a preparation step derived its columns, as Markdown."""
    lines = [f"**📌 {_walkthrough_title(data)}**", ""]
    if data.get("note"):
        lines += [data["note"], ""]
    for feature in data.get("features") or []:
        marks = feature.get("evidence") or ()
        lines += [f"**`{feature['column']}`** — {_feature_summary(feature)}", ""]
        if feature.get("code"):
            lines.append(f"*{_excerpt_label(data, feature)}*")
            for excerpt in feature["code"]:
                lines += ["```python", _numbered_code(excerpt), "```"]
        if feature.get("rows"):
            lines += ["| " + " | ".join(feature["headers"]) + " |",
                      "|" + "---|" * len(feature["headers"])]
            lines += [
                "| " + " | ".join(_md_cell(v, marks) for v in row) + " |"
                for row in feature["rows"]
            ]
        lines.append("")
    return lines


def _case_table_html(data: dict) -> str:
    rows = [
        "<tr><th>column</th><th>as collected</th><th>now</th><th></th></tr>"
    ]
    marks = data.get("highlights") or []
    for field in data.get("fields") or []:
        status = "" if field["status"] == "same" else field["status"]
        before = _mark_html("—" if field["before"] is None else str(field["before"]), marks)
        after_text = "—" if field["after"] is None else str(field["after"])
        if field["status"] == "same" and len(after_text) > 60:
            after = '<td class="case-value unchanged">= unchanged</td>'
        else:
            after = f'<td class="case-value">{_mark_html(after_text, marks)}</td>'
        rows.append(
            f'<tr class="status-{_esc(field["status"])}"><td>{_esc(field["column"])}</td>'
            f'<td class="case-value">{before}</td>{after}<td>{_esc(status)}</td></tr>'
        )
    legend = "Green: added by the preparation · yellow: changed by it"
    if marks:
        legend += f" · marked: words the new columns were derived from ({', '.join(marks)})"
    return (
        '<div class="table-scroll"><table class="preview-table case-table">'
        + "".join(rows)
        + "</table></div>"
        + f'<p class="case-legend">{_esc(legend)}.</p>'
    )


def exhibit_card_html(data: dict) -> str:
    """A show_to_class exhibit as an HTML card."""
    auto = data.get("auto")
    parts = [
        f'<article class="phase-card exhibit{" auto" if auto else ""}">',
        f"<h2>{'📌' if auto else '🔎'} {_esc(_exhibit_heading(data))}</h2>",
    ]
    if data.get("caption"):
        parts.append(f'<p class="exhibit-caption">{_esc(data["caption"])}</p>')
    if data.get("kind") == "single_case":
        parts.append(_case_table_html(data))
    elif data.get("kind") == "rows":
        parts.append(_data_table_html(data.get("columns") or [], data.get("rows") or []))
    else:
        parts.append(f'<pre class="sketch-ascii code-block">{_esc(_numbered_code(data))}</pre>')
    parts.append("</article>")
    return "".join(parts)


def _walkthrough_section_html(data: dict, feature: dict, is_open: bool) -> str:
    marks = feature.get("evidence") or []
    parts = [
        f'<details class="walkthrough-section"{" open" if is_open else ""}>',
        f"<summary>{_esc(feature['column'])}</summary>",
        f'<p class="exhibit-caption">{_esc(_feature_summary(feature))}</p>',
    ]
    if feature.get("code"):
        parts.append(f'<h3 class="preview-heading">{_esc(_excerpt_label(data, feature))}</h3>')
        for excerpt in feature["code"]:
            code = _mark_html(_numbered_code(excerpt), marks)
            parts.append(f'<pre class="sketch-ascii code-block">{code}</pre>')
    if feature.get("rows"):
        parts.append('<h3 class="preview-heading">Selected records</h3>')
        head = "".join(f"<th>{_esc(h)}</th>" for h in feature["headers"])
        true_row = ' class="status-new"'
        body = "".join(
            f"<tr{true_row if flag is True else ''}>"
            + "".join(
                f"<td>{_mark_html('—' if v is None else str(v), marks)}</td>" for v in row
            )
            + "</tr>"
            for row, flag in zip(feature["rows"], feature.get("row_flags") or [])
        )
        parts.append(
            '<div class="table-scroll"><table class="preview-table walkthrough-table">'
            f"<tr>{head}</tr>{body}</table></div>"
        )
    parts.append("</details>")
    return "".join(parts)


def walkthrough_card_html(data: dict) -> str:
    """How a preparation step derived its columns, as an HTML card — one
    <details> section per column, the first one open."""
    parts = [
        '<article class="phase-card exhibit auto">',
        f"<h2>📌 {_esc(_walkthrough_title(data))}</h2>",
    ]
    if data.get("note"):
        parts.append(f'<p class="exhibit-caption">{_esc(data["note"])}</p>')
    for i, feature in enumerate(data.get("features") or []):
        parts.append(_walkthrough_section_html(data, feature, is_open=i == 0))
    overview = data.get("overview") or {}
    if overview.get("rows"):
        kind = "changed" if data.get("stage") == "clean" else "new"
        parts.append(
            f'<h3 class="preview-heading">A few listings: inputs next to the {kind} columns</h3>'
        )
        parts.append(_data_table_html(overview["columns"], overview["rows"]))
    parts.append("</article>")
    return "".join(parts)


def history_lookup_card_html(data: dict) -> str:
    """A look at earlier runs (when stuck) as an HTML card."""
    found = data.get("found") or []
    parts = [
        '<article class="phase-card">',
        f"<h2>📚 Stuck at the {_esc(data.get('stage'))} step — looked at "
        f"{_esc(data.get('runs_searched'))} earlier run(s)</h2>",
        '<p class="result-meta">'
        + (_esc("Working script(s) found: " + ", ".join(found)) if found
           else "No earlier run had a working script for this step.")
        + "</p>",
    ]
    problems = data.get("problems") or []
    if problems:
        parts.append('<h3 class="preview-heading">Problems earlier runs hit</h3>')
        parts.append(f'<pre class="sketch-ascii log-block">{_esc(chr(10).join(problems))}</pre>')
    parts.append("</article>")
    return "".join(parts)
