"""Small HTML building blocks shared by the saved-transcript renderers
(reporting/transcript.py, reporting/teaching_cards.py) — mirroring the
live page's static/app.js helpers of the same purpose."""

import html


def esc(value) -> str:
    """HTML-escape any value as text."""
    return html.escape("" if value is None else str(value), quote=True)



MAX_CELL_CHARS = 300  # cells wrap (see style.css); only very long text is cut


TRANSPOSE_MIN_COLUMNS = 8  # same as static/app.js's dataTable


TRANSPOSE_MAX_ROWS = 5


def data_table_html(columns: list, rows: list) -> str:
    """A data table; a wide sample (many columns, few rows) turned on its
    side so every column is visible without scrolling sideways."""
    if not columns or not rows:
        return ""
    table_class = "preview-table"
    if len(columns) > TRANSPOSE_MIN_COLUMNS and len(rows) <= TRANSPOSE_MAX_ROWS:
        header = ["column", *(f"row {i + 1}" for i in range(len(rows)))]
        turned = [[col, *(row[c] for row in rows)] for c, col in enumerate(columns)]
        columns, rows, table_class = header, turned, "preview-table turned-table"

    def cell(value) -> str:
        text = "" if value is None else str(value)
        return esc(text[:MAX_CELL_CHARS] + "…" if len(text) > MAX_CELL_CHARS else text)

    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>" for row in rows
    )
    return (
        f'<div class="table-scroll"><table class="{table_class}">'
        f"<tr>{head}</tr>{body}</table></div>"
    )
