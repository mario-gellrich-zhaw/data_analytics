"""How each column a preparation step added or changed was derived — shown
to the class as a worked example after every accepted cleaning/enrichment
run (see teaching.TeachingAids.show_step_example).

For a derived column like `has_balcony` it finds, from the real files:

- the evidence: the words in the listings' text that decide the value —
  taken from the string literals in the agents' own script (e.g. 'balkon',
  'balcony') and kept only if the data confirms them (they occur in the
  text of the True listings, not of the False ones);
- the code: only the lines of the agents' script that produced the column;
- selected records: listings whose text really contains the word, next to
  the new value, plus one without it for contrast;
- a count, and a small overview table (inputs next to the new columns).

Nothing is invented: every value comes from the stage's input and output
files and the saved script.
"""

import ast
import re

import pandas as pd

from tools.exhibits import CONTEXT_COLUMNS, _GENERIC_NAME_PARTS, _cell, _same_value

MAX_FEATURES = 8
MAX_VALUE_FEATURES = 3  # lookups/numbers always get a place next to the flags
MAX_EXCERPT_LINES = 14
MAX_STATEMENT_LINES = 6  # a hit inside a longer statement shows just its neighbours
SNIPPET_RADIUS = 80
TEXT_COLUMNS = ("description", "attributes", "title")
MAX_EVIDENCE_WORDS = 4
OVERVIEW_ROWS = 5
OVERVIEW_CELL_CHARS = 60

_COLUMN_REF_RE = re.compile(r"\[\s*['\"]([\w ]+)['\"]\s*\]")
_TRUE_VALUES = {"true", "1", "1.0"}
_FLAG_VALUES = _TRUE_VALUES | {"false", "0", "0.0"}


# --- small helpers -----------------------------------------------------------


def _word_literals(nodes) -> set[str]:
    words = {
        node.value.strip().lower()
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    return {w for w in words if 3 <= len(w) <= 30 and re.fullmatch(r"[^\W\d_][\w\- ]*", w)}


def script_literals(code: str) -> list[str]:
    """The short word-like string literals in a script — the keyword lists
    agents write, e.g. ['balkon', 'balcony']."""
    try:
        return sorted(_word_literals(ast.walk(ast.parse(code))))
    except SyntaxError:
        return []


def column_keywords(code: str, column: str) -> list[str]:
    """The literals on the lines of a script that name a column — its own
    keyword list: for has_balcony, the line
    'balcony': (['balkon', 'balcony'], ['balcony', 'balconygarden'])."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names = _key_names(column)
    by_line: dict[int, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            by_line.setdefault(node.lineno, set()).add(node.value.strip().lower())
    # Lines whose literals include the column's own key ('balcony'), not
    # merely a word containing it ('balconygarden' on the garden line).
    lines = {n for n, literals in by_line.items() if literals & names}
    if not lines:
        lines = {
            number for number, line in enumerate(code.splitlines(), start=1)
            if any(n in line.lower() for n in names)
        }
    nodes = [n for n in ast.walk(tree) if getattr(n, "lineno", None) in lines]
    return sorted(_word_literals(nodes) - _GENERIC_NAME_PARTS)


def _name_parts(column: str, min_len: int = 4) -> list[str]:
    parts = re.split(r"[_\W]+", column.lower())
    skip = _GENERIC_NAME_PARTS | {"has", "num", "and", "the"}
    return [p for p in parts if len(p) >= min_len and p not in skip]


def _key_names(column: str) -> set[str]:
    """What a script's keyword list for this column is keyed by: the
    column itself, its name without prefix (has_balcony -> 'balcony') and
    its name parts."""
    names = {column.lower(), *_name_parts(column)}
    if "_" in column:
        names.add(column.lower().split("_", 1)[1])
    return names


def _is_flag(series: pd.Series) -> bool:
    values = {str(v).lower() for v in series.dropna().unique()}
    return bool(values) and values <= _FLAG_VALUES


def _truthy(value) -> bool:
    return str(value).lower() in _TRUE_VALUES


def _text(row: pd.Series, columns: list[str]) -> str:
    return " ".join(str(row[c]) for c in columns if c in row and pd.notna(row[c])).lower()


def snippet(text, words: list[str]) -> str:
    """About ±80 characters of `text` around the first of `words` (or its
    start if none occurs), on one line."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    flat = " ".join(str(text).split())
    lower = flat.lower()
    hits = [lower.find(w) for w in words if lower.find(w) >= 0]
    if not hits:
        return flat[: 2 * SNIPPET_RADIUS] + ("…" if len(flat) > 2 * SNIPPET_RADIUS else "")
    start = max(0, min(hits) - SNIPPET_RADIUS)
    end = min(len(flat), min(hits) + SNIPPET_RADIUS)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


# --- the code that produced a column ----------------------------------------


def _statement_spans(tree: ast.AST) -> list[tuple[int, int]]:
    return [
        (node.lineno, node.end_lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt) and node.end_lineno is not None
    ]


def _word_re(word: str) -> re.Pattern:
    """`word` as a whole word — 'balcony' matches 'balcony' and
    get_balcony, not 'balconygarden'."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")


def _source_lines(tree: ast.AST, column: str) -> list[int]:
    """One step of data flow: for `df['bfs_nr'] = muni_df.iloc[:, 1]`, the
    line where `muni_df` is computed (from the listing's lat/lon)."""
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    sources, frames = set(), set()
    for node in assigns:
        targets = " ".join(ast.unparse(t) for t in node.targets)
        if f"'{column}'" in targets or f'"{column}"' in targets:
            sources |= {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            # the table itself (df in df['x'] = ...) isn't a source to follow
            frames |= {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    sources -= frames

    def assigned_names(target):
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, ast.Tuple):
            return {e.id for e in target.elts if isinstance(e, ast.Name)}
        return set()

    return [
        node.lineno for node in assigns
        if any(assigned_names(t) & sources for t in node.targets)
    ]


def _hit_lines(code: str, tree, column: str, words: list[str]) -> list[int]:
    """Line numbers that mention the column, its f-string prefix or a name
    part (evidence words only as a fallback), plus its source lines."""
    patterns = [_word_re(n) for n in {column.lower(), *_name_parts(column, min_len=3)}]
    if not any(p.search(code.lower()) for p in patterns):
        patterns += [_word_re(w) for w in words]
    if column.lower() not in code.lower() and "_" in column:
        patterns.append(re.compile(re.escape(column.split("_")[0].lower() + "_{")))
    hits = [
        number for number, line in enumerate(code.splitlines(), start=1)
        if any(p.search(line.lower()) for p in patterns)
    ]
    if tree:
        hits += _source_lines(tree, column)
    return sorted(set(hits))


def code_excerpts(code: str, column: str, words: list[str]) -> list[dict]:
    """The lines of `code` that produced `column`: every line mentioning
    the column (or, for an f-string name like f'has_{amen}', its prefix)
    or a part of its name — and only if none does, an evidence word — each
    with its short statement or neighbours, merged into numbered excerpts."""
    lines = code.splitlines()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None
    spans = _statement_spans(tree) if tree else []
    windows = []
    for number in _hit_lines(code, tree, column, words):
        inner = [s for s in spans if s[0] <= number <= s[1]]
        span = min(inner, key=lambda s: s[1] - s[0]) if inner else (number, number)
        if span[1] - span[0] + 1 > MAX_STATEMENT_LINES:
            span = (number - 1, number + 1)
        windows.append((max(1, span[0]), min(len(lines), span[1])))
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    excerpts, budget = [], MAX_EXCERPT_LINES
    for start, end in merged:
        while start < end and not lines[start - 1].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        end = min(end, start + budget - 1)
        if budget <= 0 or end < start:
            break
        excerpts.append({"start_line": start, "lines": lines[start - 1 : end]})
        budget -= end - start + 1
    return excerpts


def _input_columns(excerpts: list[dict], available, column: str) -> list[str]:
    """Columns the excerpt reads (df['lat'], x['description'], …)."""
    refs = [
        ref for ex in excerpts for line in ex["lines"] for ref in _COLUMN_REF_RE.findall(line)
    ]
    found = [r for r in dict.fromkeys(refs) if r in available and r != column]
    return found or [c for c in CONTEXT_COLUMNS if c in available][:3]


# --- one feature --------------------------------------------------------------


def evidence_words(flags: pd.Series, texts: pd.Series, candidates: list[str]) -> list[str]:
    """The candidate words the data confirms for a yes/no column: they occur
    in the text of True listings more often than in that of False ones —
    strongest first."""
    true_texts = texts[flags.map(_truthy)]
    false_texts = texts[~flags.map(_truthy)]
    scored = []
    for word in candidates:
        in_true = true_texts.str.contains(word, regex=False).mean() if len(true_texts) else 0
        in_false = false_texts.str.contains(word, regex=False).mean() if len(false_texts) else 0
        if in_true > 0 and in_true > in_false:
            scored.append((in_true - in_false, word))
    return [w for _, w in sorted(scored, reverse=True)[:MAX_EVIDENCE_WORDS]]


def _flag_records(merged, is_true, text_col, words) -> tuple[list, int]:
    """Which listings to show for a yes/no column: two True ones whose text
    column really contains an evidence word (any True ones if none does),
    and one False one for contrast. Also how many True ones contain it."""
    def mentions(value) -> bool:
        return isinstance(value, str) and any(w in value.lower() for w in words)

    true_rows = merged[is_true]
    if text_col:
        via_text = true_rows[true_rows[f"{text_col}__in"].map(mentions)]
    else:
        via_text = true_rows.iloc[0:0]
    picks = list(via_text.head(2).index) or list(true_rows.head(2).index)
    return picks + list(merged[~is_true].head(1).index), len(via_text)


def _evidence_for(column, merged, code, literals, text_cols) -> list[str]:
    """The evidence words for a yes/no column: its own keyword list in the
    script (all word literals if it has none), confirmed by the data."""
    texts = merged.apply(lambda r: _text(r, [f"{c}__in" for c in text_cols]), axis=1)
    candidates = column_keywords(code, column) or sorted({*literals, *_name_parts(column)})
    return evidence_words(merged[column], texts, candidates)


def _flag_feature(column, merged, code, literals) -> dict:
    text_cols = [c for c in TEXT_COLUMNS if f"{c}__in" in merged.columns]
    words = _evidence_for(column, merged, code, literals, text_cols)
    excerpts = code_excerpts(code, column, words)
    shown = [c for c in _input_columns(excerpts, text_cols, column) if c in text_cols] or text_cols
    is_true = merged[column].map(_truthy)
    first_col = "description" if "description" in shown else shown[0] if shown else None
    picks, n_via_first = _flag_records(merged, is_true, first_col, words)
    rows = [
        [_cell(merged.at[i, "listing_id"]),
         *(snippet(merged.at[i, f"{c}__in"], words) for c in shown),
         _cell(merged.at[i, column])]
        for i in picks
    ]
    summary = f"True for {int(is_true.sum())} of {len(merged)} listings"
    if first_col and words:
        summary += f" ({n_via_first} of them say so in the {first_col})"
    return {
        "column": column, "kind": "flag", "summary": summary, "evidence": words,
        "code": excerpts, "headers": ["listing", *shown, column], "rows": rows,
        "row_flags": [bool(is_true[i]) for i in picks],
    }


def _value_feature(column, merged, code, input_cols) -> dict:
    excerpts = code_excerpts(code, column, [])
    inputs = _input_columns(excerpts, input_cols, column)
    filled = merged[merged[column].notna()]
    rows = [
        [_cell(merged.at[i, "listing_id"]),
         *(snippet(merged.at[i, f"{c}__in"], []) for c in inputs), _cell(merged.at[i, column])]
        for i in filled.head(3).index
    ]
    return {
        "column": column, "kind": "value",
        "summary": f"Filled for {len(filled)} of {len(merged)} listings",
        "evidence": [], "code": excerpts, "headers": ["listing", *inputs, column],
        "rows": rows, "row_flags": [None] * len(rows),
    }


def _changed_feature(column, merged, code) -> dict:
    changed = merged[[
        not _same_value(_cell(a), _cell(b))
        for a, b in zip(merged[column], merged[f"{column}__in"])
    ]]
    rows = [
        [_cell(merged.at[i, "listing_id"]), snippet(merged.at[i, f"{column}__in"], []),
         snippet(merged.at[i, column], [])]
        for i in changed.head(3).index
    ]
    return {
        "column": column, "kind": "changed",
        "summary": f"Changed in {len(changed)} of {len(merged)} listings",
        "evidence": [], "code": code_excerpts(code, column, []),
        "headers": ["listing", f"{column} before", f"{column} after"],
        "rows": rows, "row_flags": [None] * len(rows), "n_changed": len(changed),
    }


# --- the whole step -----------------------------------------------------------


def _overview(merged, features, input_cols) -> dict:
    """A few listings: their inputs next to every new/changed column."""
    picked = list(dict.fromkeys(r[0] for f in features for r in f["rows"]))
    ids = (picked + [i for i in merged["listing_id"] if i not in picked])[:OVERVIEW_ROWS]
    rows_by_id = merged.set_index("listing_id")
    used = [h for f in features if f["kind"] != "changed" for h in f["headers"][1:-1]]
    inputs = [c for c in dict.fromkeys(used) if c in input_cols and c != "description"][:4]
    columns = [*inputs, *(f["column"] for f in features)]

    def value(listing, col):
        name = f"{col}__in" if col in inputs else col
        cell = _cell(rows_by_id.at[listing, name])
        text = "" if cell is None else str(cell)
        return text[:OVERVIEW_CELL_CHARS] + ("…" if len(text) > OVERVIEW_CELL_CHARS else "")

    return {
        "columns": ["listing", *columns],
        "rows": [[_cell(listing), *(value(listing, c) for c in columns)] for listing in ids],
    }


def step_walkthrough(stage: str, before: pd.DataFrame, after: pd.DataFrame,
                     code: str, script: str) -> dict | None:
    """How a cleaning/enrichment step derived its columns: one section per
    new (enrich) or changed (clean) column, plus an overview."""
    if "listing_id" not in before.columns or "listing_id" not in after.columns or after.empty:
        return None
    renamed = before.rename(columns={c: f"{c}__in" for c in before.columns if c != "listing_id"})
    merged = after.merge(renamed, on="listing_id", how="left").reset_index(drop=True)
    input_cols = [str(c) for c in before.columns if c != "listing_id"]
    if stage == "clean":
        candidates = [c for c in after.columns if c in before.columns and c != "listing_id"]
        features = [_changed_feature(c, merged, code) for c in candidates]
        features = sorted((f for f in features if f["n_changed"]), key=lambda f: -f["n_changed"])
    else:
        literals = script_literals(code)
        new_cols = [str(c) for c in after.columns if c not in before.columns]
        flags = [_flag_feature(c, merged, code, literals) for c in new_cols
                 if _is_flag(merged[c])]
        flags.sort(key=lambda f: not f["evidence"])
        values = [_value_feature(c, merged, code, input_cols) for c in new_cols
                  if not _is_flag(merged[c])][:MAX_VALUE_FEATURES]
        features = flags[: MAX_FEATURES - len(values)] + values
    features = features[:MAX_FEATURES]
    if not features and stage == "clean":
        # Worth saying too: the script's checks found nothing to fix.
        return {
            "stage": stage, "script": script, "total_rows": len(merged), "features": [],
            "overview": {"columns": [], "rows": []},
            "note": (
                f"No value changed — all {len(merged)} listings already passed every "
                f"check in {script}."
            ),
        }
    if not features:
        return None
    return {
        "stage": stage, "script": script, "total_rows": len(merged),
        "features": features, "overview": _overview(merged, features, input_cols),
    }
