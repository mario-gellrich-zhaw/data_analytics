"""Small HTML building blocks shared by the saved-transcript renderers
(reporting/transcript.py, reporting/teaching_cards.py) — mirroring the
live page's static/app.js helpers of the same purpose."""

import html


def esc(value) -> str:
    """HTML-escape any value as text."""
    return html.escape("" if value is None else str(value), quote=True)



MAX_CELL_CHARS = 300  # only very long text is cut; the full value is on hover
MAX_TABLE_ROWS = 10  # same as static/app.js's dataTable


def data_table_html(columns: list, rows: list) -> str:
    """A data sample as one plain grid — columns across, at most
    MAX_TABLE_ROWS records down — in a box that scrolls both ways, missing
    values marked "—" (same as static/app.js's dataTable)."""
    if not columns or not rows:
        return ""

    def cell(value) -> str:
        if value is None or value == "":
            return '<td class="missing">—</td>'
        text = str(value)
        shown = text[:MAX_CELL_CHARS] + "…" if len(text) > MAX_CELL_CHARS else text
        return f'<td title="{esc(text)}">{esc(shown)}</td>'

    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(cell(v) for v in row) + "</tr>" for row in rows[:MAX_TABLE_ROWS]
    )
    return (
        '<div class="table-scroll data-scroll"><table class="preview-table data-table">'
        f"<tr>{head}</tr>{body}</table></div>"
    )


def code_card_html(title: str, check: str, code: str) -> str:
    """A script an agent wrote: only its name and code-check result, the
    full code one click away (same as static/app.js's codeCard)."""
    return (
        '<details class="phase-card code-card">'
        f'<summary><h2>{esc(title)}</h2><span class="result-meta">{esc(check)}</span></summary>'
        f'<pre class="sketch-ascii code-block">{esc(code)}</pre>'
        "</details>"
    )
