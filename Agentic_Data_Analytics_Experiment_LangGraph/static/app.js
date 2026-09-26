const startBtn = document.getElementById("start-btn");
const statusBar = document.getElementById("status-bar");
const statusText = document.getElementById("status-text");
const progressFill = document.getElementById("progress-fill");
const chat = document.getElementById("chat");

// A phase's length is dynamic (the agents decide when it's done), so this is
// just an estimate for a smoothly-filling per-phase progress bar.
const EXPECTED_TURNS_PER_PHASE = 7;

let turnsInPhase = 0;
let currentStepLabel = "";
let eventSource = null;
let isRunning = false;
let vizPromise = null; // lazily-loaded Graphviz renderer, only if a "dot" sketch appears

function speakerClass(speaker) {
  if (speaker === "Product Manager") return "pm";
  if (speaker === "Data Engineer") return "engineer";
  return "analyst";
}

function addBubble(speaker, text, isAction) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${speakerClass(speaker)}${isAction ? " action" : ""}`;

  const label = document.createElement("span");
  label.className = "speaker";
  label.textContent = speaker;

  const body = document.createElement("p");
  body.textContent = text;

  bubble.append(label, body);
  chat.appendChild(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "end" });
}

function addPhaseDivider(stepLabel, subLabel, step) {
  const divider = document.createElement("div");
  divider.className = "phase-divider";
  divider.textContent = subLabel ? `Step ${step}/4 · ${stepLabel} — ${subLabel}` : `Step ${step}/4 · ${stepLabel}`;
  chat.appendChild(divider);
  divider.scrollIntoView({ behavior: "smooth", block: "end" });
}

function setProgress(stage) {
  statusText.textContent = currentStepLabel ? `${currentStepLabel} · ${stage}` : stage;
  const pct = Math.min(100, Math.round((turnsInPhase / EXPECTED_TURNS_PER_PHASE) * 100));
  progressFill.style.width = `${pct}%`;
}

function statTile(label, value) {
  const tile = document.createElement("div");
  tile.className = "stat-tile";

  const val = document.createElement("div");
  val.className = "stat-value";
  val.textContent = value;

  const lbl = document.createElement("div");
  lbl.className = "stat-label";
  lbl.textContent = label;

  tile.append(val, lbl);
  return tile;
}

const MAX_CELL_CHARS = 300; // cells wrap (see style.css); only very long text is cut

// A wide sample (many columns, few rows) is shown turned on its side —
// one line per column, one column per row — so every column is visible
// without scrolling sideways.
const TRANSPOSE_MIN_COLUMNS = 8;
const TRANSPOSE_MAX_ROWS = 5;

function dataTable(columns, rows) {
  if (!Array.isArray(columns) || !Array.isArray(rows) || rows.length === 0) return null;
  if (columns.length > TRANSPOSE_MIN_COLUMNS && rows.length <= TRANSPOSE_MAX_ROWS) {
    const header = ["column", ...rows.map((_, i) => `row ${i + 1}`)];
    const turned = columns.map((col, c) => [col, ...rows.map((row) => row[c])]);
    return plainTable(header, turned, "turned-table");
  }
  return plainTable(columns, rows);
}

function plainTable(columns, rows, extraClass = "") {
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";

  const table = document.createElement("table");
  table.className = `preview-table${extraClass ? ` ${extraClass}` : ""}`;

  const thead = document.createElement("tr");
  columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col;
    thead.appendChild(th);
  });
  table.appendChild(thead);

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((value) => {
      const td = document.createElement("td");
      const text = value === null || value === undefined ? "" : String(value);
      td.textContent = text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS)}…` : text;
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });

  wrap.appendChild(table);
  return wrap;
}

function loadViz() {
  if (!vizPromise) {
    vizPromise = import("https://cdn.jsdelivr.net/npm/@viz-js/viz@3/lib/viz-standalone.mjs")
      .then((mod) => mod.instance());
  }
  return vizPromise;
}

function renderSketch(sketch) {
  if (!sketch || !sketch.content) return null;

  const wrap = document.createElement("div");
  wrap.className = "sketch";

  if (sketch.title) {
    const title = document.createElement("div");
    title.className = "sketch-title";
    title.textContent = sketch.title;
    wrap.appendChild(title);
  }

  if (sketch.kind === "dot") {
    const box = document.createElement("div");
    box.className = "sketch-dot";
    box.textContent = "Rendering diagram…";
    wrap.appendChild(box);
    loadViz()
      .then((viz) => {
        box.replaceChildren(viz.renderSVGElement(sketch.content));
      })
      .catch(() => {
        // Graceful fallback if the CDN render fails for any reason.
        const pre = document.createElement("pre");
        pre.className = "sketch-ascii";
        pre.textContent = sketch.content;
        box.replaceChildren(pre);
      });
  } else {
    const pre = document.createElement("pre");
    pre.className = "sketch-ascii";
    pre.textContent = sketch.content;
    wrap.appendChild(pre);
  }

  return wrap;
}

function preBlock(text, extraClass = "") {
  const pre = document.createElement("pre");
  pre.className = `sketch-ascii${extraClass ? ` ${extraClass}` : ""}`;
  pre.textContent = text;
  return pre;
}

function describeRequest(entry) {
  if (entry.blocked) return `✖ ${entry.url}\n    blocked: ${entry.blocked}`;
  if (entry.kind === "robots.txt") return `· ${entry.url} → HTTP ${entry.status}`;
  return `✔ GET ${entry.url} → HTTP ${entry.status} (${entry.elapsed_ms} ms)`;
}

// One scraper version the Data Analyst just wrote — shown inline, in full,
// so the class can read the code the agent came up with.
function buildScraperCodeCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  heading.textContent = `🧑‍💻 scraper_v${data.version}.py — written by the Data Analyst (${data.lines} lines)`;
  card.appendChild(heading);

  const check = document.createElement("p");
  check.className = "result-meta";
  check.textContent = data.check_passed
    ? "Code check passed (only allowed imports; web access only via scraper_kit)."
    : `Code check failed: ${data.problems.join("; ")}`;
  card.appendChild(check);

  card.appendChild(preBlock(data.code, "code-block"));
  return card;
}

// What really happened when a scraper version ran: every request with its
// real status or block reason, the printed output, and rows saved.
function buildScraperRunCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  const outcome = data.timed_out
    ? "⏱️ timed out"
    : data.exit_code === 0 ? "exit code 0" : `❌ crashed (exit code ${data.exit_code})`;
  heading.textContent = `▶️ Ran scraper_v${data.version}.py — ${outcome}`;
  card.appendChild(heading);

  const pages = (data.requests || []).filter((e) => e.kind === "page" && !e.blocked).length;
  const grid = document.createElement("div");
  grid.className = "stat-grid";
  grid.append(
    statTile("pages fetched", pages),
    statTile("rows saved", data.rows_saved),
    statTile("seconds", data.elapsed_seconds),
  );
  card.appendChild(grid);

  if (data.accepted_as_dataset !== undefined) {
    const verdict = document.createElement("p");
    verdict.className = "result-meta";
    verdict.textContent = data.accepted_as_dataset
      ? "✅ Accepted as the current dataset (looks like individual listings)."
      : `⚠️ Not accepted as a dataset: ${data.rejected_because}`;
    card.appendChild(verdict);
  }

  if (data.requests && data.requests.length > 0) {
    const reqHeading = document.createElement("h3");
    reqHeading.className = "preview-heading";
    reqHeading.textContent = `Requests (${data.requests.length})`;
    card.append(reqHeading, preBlock(data.requests.map(describeRequest).join("\n"), "log-block"));
  }

  if (data.output_tail) {
    const outHeading = document.createElement("h3");
    outHeading.className = "preview-heading";
    outHeading.textContent = "Printed output";
    card.append(outHeading, preBlock(data.output_tail, "code-block"));
  }

  if (Array.isArray(data.sample_rows) && data.sample_rows.length > 0) {
    const sampleHeading = document.createElement("h3");
    sampleHeading.className = "preview-heading";
    sampleHeading.textContent = `First ${data.sample_rows.length} scraped rows`;
    card.appendChild(sampleHeading);
    const table = dataTable(data.columns, data.sample_rows);
    if (table) card.appendChild(table);
  }

  return card;
}

// Step 4's agent-written preparation scripts: who owns which stage.
const PREP_STAGES = {
  clean: { label: "cleaning", author: "Data Engineer" },
  enrich: { label: "enrichment", author: "Data Analyst" },
};

function prepFileName(data) {
  return `${data.stage}_v${data.version}.py`;
}

// One cleaning/enrichment script version just written — shown in full.
function buildPrepCodeCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card";

  const stage = PREP_STAGES[data.stage] || { label: data.stage, author: "an agent" };
  const heading = document.createElement("h2");
  heading.textContent = `🧑‍💻 ${prepFileName(data)} — ${stage.label} code written by the ${stage.author} (${data.lines} lines)`;
  card.appendChild(heading);

  const check = document.createElement("p");
  check.className = "result-meta";
  check.textContent = data.check_passed
    ? "Code check passed (data only via prep_kit; web access only via scraper_kit)."
    : `Code check failed: ${data.problems.join("; ")}`;
  card.appendChild(check);

  card.appendChild(preBlock(data.code, "code-block"));
  return card;
}

function columnList(label, columns) {
  const p = document.createElement("p");
  p.className = "result-meta";
  p.textContent = `${label}: ${columns.join(", ")}`;
  return p;
}

// What really happened when a preparation script ran: rows before/after,
// columns added/removed, missing values, requests, printed output.
function buildPrepRunCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  const outcome = data.timed_out
    ? "⏱️ timed out"
    : data.exit_code === 0 ? "exit code 0" : `❌ crashed (exit code ${data.exit_code})`;
  heading.textContent = `▶️ Ran ${prepFileName(data)} — ${outcome}`;
  card.appendChild(heading);

  const grid = document.createElement("div");
  grid.className = "stat-grid";
  grid.append(
    statTile("rows before", data.rows_before),
    statTile("rows after", data.rows_after),
    statTile("columns added", (data.added_columns || []).length),
    statTile("web requests", (data.requests || []).length),
  );
  card.appendChild(grid);

  const verdict = document.createElement("p");
  verdict.className = "result-meta";
  verdict.textContent = data.accepted
    ? "✅ Accepted as the current dataset."
    : `⚠️ Not accepted: ${data.rejected_because}`;
  card.appendChild(verdict);

  if ((data.added_columns || []).length > 0) card.appendChild(columnList("New columns", data.added_columns));
  if ((data.removed_columns || []).length > 0) card.appendChild(columnList("Removed columns", data.removed_columns));
  const missing = Object.entries(data.missing_values || {});
  if (missing.length > 0) {
    card.appendChild(columnList("Missing values after", missing.map(([col, n]) => `${col} ${n}`)));
  }

  if (data.requests && data.requests.length > 0) {
    const reqHeading = document.createElement("h3");
    reqHeading.className = "preview-heading";
    const shown = data.requests.slice(0, 20);
    reqHeading.textContent = shown.length < data.requests.length
      ? `Requests (first ${shown.length} of ${data.requests.length})`
      : `Requests (${data.requests.length})`;
    card.append(reqHeading, preBlock(shown.map(describeRequest).join("\n"), "log-block"));
  }

  if (data.output_tail) {
    const outHeading = document.createElement("h3");
    outHeading.className = "preview-heading";
    outHeading.textContent = "Printed output";
    card.append(outHeading, preBlock(data.output_tail, "code-block"));
  }

  if (Array.isArray(data.sample_rows) && data.sample_rows.length > 0) {
    const sampleHeading = document.createElement("h3");
    sampleHeading.className = "preview-heading";
    sampleHeading.textContent = `First ${data.sample_rows.length} rows of the output`;
    card.appendChild(sampleHeading);
    const table = dataTable(data.columns, data.sample_rows);
    if (table) card.appendChild(table);
  }

  return card;
}

const CASE_STATUS_LABELS = { new: "new", changed: "changed", removed: "removed", same: "" };

function cellText(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Text with the words a new column was derived from marked (<mark>) —
// built from text nodes, never innerHTML.
function appendHighlighted(parent, text, words) {
  if (!words || words.length === 0) {
    parent.textContent = text;
    return;
  }
  // Longest first, so "balconygarden" is marked once, not "balcony" inside it.
  const sorted = [...words].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(${sorted.map(escapeRegExp).join("|")})`, "gi");
  text.split(pattern).forEach((part, i) => {
    if (i % 2 === 1) {
      const mark = document.createElement("mark");
      mark.textContent = part;
      parent.appendChild(mark);
    } else if (part) {
      parent.appendChild(document.createTextNode(part));
    }
  });
}

// ONE listing, field by field: as collected vs. now — full text, no cutting,
// so students can see e.g. which words in a description became which flag.
function buildCaseTable(data) {
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "preview-table case-table";
  const head = document.createElement("tr");
  ["column", "as collected", "now", ""].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    head.appendChild(th);
  });
  table.appendChild(head);
  data.fields.forEach((field) => {
    const tr = document.createElement("tr");
    tr.className = `status-${field.status}`;

    const name = document.createElement("td");
    name.textContent = field.column;
    const before = document.createElement("td");
    before.className = "case-value";
    appendHighlighted(before, cellText(field.before), data.highlights);
    const after = document.createElement("td");
    after.className = "case-value";
    const longSame = field.status === "same" && String(field.after ?? "").length > 60;
    if (longSame) {
      after.classList.add("unchanged");
      after.textContent = "= unchanged";
    } else {
      appendHighlighted(after, cellText(field.after), data.highlights);
    }
    const status = document.createElement("td");
    status.textContent = CASE_STATUS_LABELS[field.status] || "";

    tr.append(name, before, after, status);
    table.appendChild(tr);
  });
  wrap.appendChild(table);
  return wrap;
}

function exhibitHeading(data) {
  if (data.kind === "single_case" && data.auto) {
    return `What this step did to one listing — ${data.listing_id}`;
  }
  if (data.kind === "single_case") {
    return `One listing up close — ${data.listing_id}${data.dropped ? " (dropped by the cleaning)" : ""}`;
  }
  if (data.kind === "rows") {
    return `Real rows from ${data.file} (${(data.rows || []).length} of ${data.total_rows})`;
  }
  const end = data.start_line + (data.lines || []).length - 1;
  return `${data.file}, lines ${data.start_line}–${end} of ${data.total_lines}`;
}

// A real example an agent put in front of the class (show_to_class): one
// listing before/after, a few real rows, or a few real lines of code.
function buildExhibitCard(data) {
  const card = document.createElement("article");
  card.className = `phase-card exhibit${data.auto ? " auto" : ""}`;

  const heading = document.createElement("h2");
  heading.textContent = `${data.auto ? "📌" : "🔎"} ${exhibitHeading(data)}`;
  card.appendChild(heading);

  if (data.caption) {
    const caption = document.createElement("p");
    caption.className = "exhibit-caption";
    caption.textContent = data.caption;
    card.appendChild(caption);
  }

  if (data.kind === "single_case") {
    card.appendChild(buildCaseTable(data));
    const legend = document.createElement("p");
    legend.className = "case-legend";
    legend.textContent = data.highlights && data.highlights.length
      ? `Green: added by the preparation · yellow: changed by it · marked: words the new columns were derived from (${data.highlights.join(", ")}).`
      : "Green: added by the preparation · yellow: changed by it.";
    card.appendChild(legend);
  } else if (data.kind === "rows") {
    const table = dataTable(data.columns, data.rows);
    if (table) card.appendChild(table);
  } else if (data.kind === "code") {
    const width = String(data.start_line + data.lines.length - 1).length;
    const numbered = data.lines
      .map((line, i) => `${String(data.start_line + i).padStart(width)}  ${line}`)
      .join("\n");
    card.appendChild(preBlock(numbered, "code-block"));
  }
  return card;
}

// Numbered code lines (as in the script), with the given words marked.
function codeExcerptBlock(excerpt, words) {
  const pre = document.createElement("pre");
  pre.className = "sketch-ascii code-block";
  const last = excerpt.start_line + excerpt.lines.length - 1;
  const width = String(last).length;
  excerpt.lines.forEach((line, i) => {
    const number = String(excerpt.start_line + i).padStart(width);
    pre.appendChild(document.createTextNode(`${number}  `));
    const span = document.createElement("span");
    appendHighlighted(span, line, words);
    pre.append(span, document.createTextNode("\n"));
  });
  return pre;
}

function subHeading(text) {
  const h = document.createElement("h3");
  h.className = "preview-heading";
  h.textContent = text;
  return h;
}

// One derived column: count, the agents' code that produced it, and a few
// selected records with the deciding words marked in their text.
function buildWalkthroughSection(data, feature) {
  const section = document.createElement("div");
  section.className = "walkthrough-section";

  const summary = document.createElement("p");
  summary.className = "exhibit-caption";
  summary.textContent = feature.evidence.length
    ? `${feature.summary} — decided by the words: ${feature.evidence.join(", ")}`
    : feature.summary;
  section.appendChild(summary);

  if (feature.code.length) {
    const lines = feature.code.map((ex) => `${ex.start_line}–${ex.start_line + ex.lines.length - 1}`);
    section.appendChild(subHeading(`The agents' code (${data.script}, lines ${lines.join(", ")})`));
    feature.code.forEach((ex) => section.appendChild(codeExcerptBlock(ex, feature.evidence)));
  }

  if (feature.rows.length) {
    section.appendChild(subHeading("Selected records"));
    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
    const table = document.createElement("table");
    table.className = "preview-table walkthrough-table";
    const head = document.createElement("tr");
    feature.headers.forEach((label) => {
      const th = document.createElement("th");
      th.textContent = label;
      head.appendChild(th);
    });
    table.appendChild(head);
    feature.rows.forEach((row, r) => {
      const tr = document.createElement("tr");
      if (feature.row_flags[r] === true) tr.className = "status-new";
      row.forEach((value) => {
        const td = document.createElement("td");
        appendHighlighted(td, cellText(value), feature.evidence);
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    section.appendChild(wrap);
  }
  return section;
}

// How an accepted cleaning/enrichment step derived its columns — one
// feature at a time (chips switch between them), plus an overview.
function buildWalkthroughCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card exhibit auto";

  const heading = document.createElement("h2");
  heading.textContent = data.stage === "clean"
    ? `📌 How ${data.script} changed the data`
    : `📌 How ${data.script} derived its new columns`;
  card.appendChild(heading);

  if (data.note) {
    const note = document.createElement("p");
    note.className = "exhibit-caption";
    note.textContent = data.note;
    card.appendChild(note);
  }
  if (!data.features.length) return card;

  const chips = document.createElement("div");
  chips.className = "feature-chips";
  const holder = document.createElement("div");
  const show = (index) => {
    holder.replaceChildren(buildWalkthroughSection(data, data.features[index]));
    chips.querySelectorAll("button").forEach((b, i) => b.classList.toggle("active", i === index));
  };
  data.features.forEach((feature, i) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "feature-chip";
    chip.textContent = feature.column;
    chip.addEventListener("click", () => show(i));
    chips.appendChild(chip);
  });
  card.append(chips, holder);
  show(0);

  if (data.overview && data.overview.rows.length) {
    card.appendChild(subHeading(`A few listings: inputs next to the ${data.stage === "clean" ? "changed" : "new"} columns`));
    const table = dataTable(data.overview.columns, data.overview.rows);
    if (table) card.appendChild(table);
  }
  return card;
}

// An agent got stuck and looked at how earlier runs solved the same step.
function buildHistoryLookupCard(data) {
  const card = document.createElement("article");
  card.className = "phase-card";
  const heading = document.createElement("h2");
  heading.textContent = `📚 Stuck at the ${data.stage} step — looked at ${data.runs_searched} earlier run(s)`;
  card.appendChild(heading);
  const found = document.createElement("p");
  found.className = "result-meta";
  found.textContent = data.found.length
    ? `Working script(s) found: ${data.found.join(", ")}`
    : "No earlier run had a working script for this step.";
  card.appendChild(found);
  if (data.problems.length) {
    const probHeading = document.createElement("h3");
    probHeading.className = "preview-heading";
    probHeading.textContent = "Problems earlier runs hit";
    card.append(probHeading, preBlock(data.problems.join("\n"), "log-block"));
  }
  return card;
}

function buildCollectingCard(data) {
  const { download } = data || {};
  if (!download || Object.keys(download).length === 0) return null;

  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  heading.textContent = download.fallback
    ? "⚠️ Falling back to best available data (not individual-level)"
    : download.scraped
      ? "✅ Real data scraped by the agents' own code"
      : download.success
        ? "✅ Real data downloaded"
        : "⚠️ Download didn't complete";
  card.appendChild(heading);

  if (download.dataset_title) {
    const meta = document.createElement("p");
    meta.className = "result-meta";
    const link = document.createElement("a");
    link.href = download.dataset_url || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = download.dataset_title;
    meta.append("Source: ", link, ` (${download.dataset_organization || "opendata.swiss"})`);
    card.appendChild(meta);
  }

  if (download.fallback) {
    const why = document.createElement("p");
    why.className = "result-meta";
    why.textContent = `Set aside earlier because ${download.reason_rejected || "it did not look individual-level"}, but no individual-apartment-level dataset was ever confirmed — used as the best real option found.`;
    card.appendChild(why);
  }

  const dl = document.createElement("p");
  dl.className = "result-meta";
  dl.textContent = download.scraped
    ? `Scraped: ${download.rows.toLocaleString()} listings, ${download.bytes.toLocaleString()} bytes saved (CSV).`
    : download.success
    ? `Download: ${download.bytes.toLocaleString()} bytes saved (${download.format || "file"}).`
    : `Download failed: ${download.error}`;
  card.appendChild(dl);

  const preview = data && data.preview;
  if (preview && Array.isArray(preview.rows) && preview.rows.length > 0) {
    const previewHeading = document.createElement("h3");
    previewHeading.className = "preview-heading";
    previewHeading.textContent = `First ${preview.rows.length} rows (raw)`;
    card.appendChild(previewHeading);
    const table = dataTable(preview.columns, preview.rows);
    if (table) card.appendChild(table);
  }

  return card;
}

function buildPreparingCard(data) {
  const { profile, clean, enrich, store, sql, sketch, preview } = data || {};
  const hasAnything = (profile && "n_rows" in profile) || (clean && "rows_after" in clean) || (store && store.table_name);
  if (!hasAnything) return null;

  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  heading.textContent = store && store.table_name
    ? `✅ Data ${enrich && "rows_after" in enrich ? "cleaned, enriched" : "prepared"} and stored in a real SQLite database`
    : "⚠️ Preparing & storing didn't finish";
  card.appendChild(heading);

  if (profile && "n_rows" in profile) {
    const grid = document.createElement("div");
    grid.className = "stat-grid";
    grid.append(
      statTile("rows (collected)", profile.n_rows.toLocaleString()),
      statTile("columns", profile.n_columns),
      statTile("duplicate rows", profile.duplicate_rows.toLocaleString()),
      statTile("cols with missing values", Object.keys(profile.missing_values || {}).length),
    );
    card.appendChild(grid);

    if (profile.dtypes && Object.keys(profile.dtypes).length > 0) {
      const dtypesHeading = document.createElement("h3");
      dtypesHeading.className = "preview-heading";
      dtypesHeading.textContent = "Column data types";
      card.appendChild(dtypesHeading);

      const dtypeList = document.createElement("ul");
      dtypeList.className = "dataset-list";
      Object.entries(profile.dtypes).slice(0, 10).forEach(([col, dtype]) => {
        const li = document.createElement("li");
        li.className = "dataset-item";
        li.textContent = `${col}: ${dtype}`;
        dtypeList.appendChild(li);
      });
      card.appendChild(dtypeList);
    }
  }

  if (clean && "rows_after" in clean) {
    const cl = document.createElement("p");
    cl.className = "result-meta";
    cl.textContent = `Cleaned (clean_v${clean.version}.py): ${clean.rows_before.toLocaleString()} → ${clean.rows_after.toLocaleString()} rows${(clean.removed_columns || []).length ? `, removed ${clean.removed_columns.join(", ")}` : ""}.`;
    card.appendChild(cl);
  } else {
    card.appendChild(columnList("Cleaning", ["no cleaning run was accepted"]));
  }

  if (enrich && "rows_after" in enrich) {
    card.appendChild(columnList(`Enriched (enrich_v${enrich.version}.py), new columns`, enrich.added_columns));
  }

  if (store && store.table_name) {
    const st = document.createElement("p");
    st.className = "result-meta";
    st.textContent = `Stored ${store.rows_stored.toLocaleString()} rows in table "${store.table_name}" (${store.db_bytes.toLocaleString()} bytes, ${store.db_path || "rental_data.db"}).`;
    card.appendChild(st);
  }

  if (preview && Array.isArray(preview.rows) && preview.rows.length > 0) {
    const previewHeading = document.createElement("h3");
    previewHeading.className = "preview-heading";
    previewHeading.textContent = `First ${preview.rows.length} rows (prepared)`;
    card.appendChild(previewHeading);
    const previewTable = dataTable(preview.columns, preview.rows);
    if (previewTable) card.appendChild(previewTable);
  }

  if (sql && sql.query) {
    const sqlHeading = document.createElement("h3");
    sqlHeading.className = "preview-heading";
    sqlHeading.textContent = "Verification query";
    const sqlCode = document.createElement("p");
    sqlCode.className = "result-meta";
    sqlCode.textContent = sql.query;
    card.append(sqlHeading, sqlCode);

    const table = dataTable(sql.columns, sql.rows);
    if (table) card.appendChild(table);
  }

  const sketchEl = renderSketch(sketch);
  if (sketchEl) card.appendChild(sketchEl);

  return card;
}

function addPhaseCard(builder, data) {
  const card = builder(data);
  if (!card) return;
  // Appended into the same stream as the chat bubbles, in arrival order, so
  // a step's data card lands right where it happened in the conversation —
  // not collected separately at the top or bottom.
  chat.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "end" });
}

function setRunning(running) {
  isRunning = running;
  startBtn.textContent = running ? "Stop" : "Run again";
  startBtn.classList.toggle("stop", running);
}

function stopDemo() {
  startBtn.disabled = true;
  fetch("/api/stop", { method: "POST" }).finally(() => {
    startBtn.disabled = false;
  });
}

function startDemo() {
  startBtn.disabled = false;
  setRunning(true);
  statusBar.classList.remove("hidden");
  chat.innerHTML = "";
  currentStepLabel = "";
  turnsInPhase = 0;
  setProgress("Connecting…");

  eventSource = new EventSource("/api/stream");

  eventSource.addEventListener("progress", (event) => {
    const { stage } = JSON.parse(event.data);
    setProgress(stage);
  });

  eventSource.addEventListener("phase_start", (event) => {
    const { step, step_label, sub_label } = JSON.parse(event.data);
    currentStepLabel = sub_label ? `Step ${step}/4 · ${step_label} (${sub_label})` : `Step ${step}/4 · ${step_label}`;
    turnsInPhase = 0;
    addPhaseDivider(step_label, sub_label, step);
    setProgress("starting…");
  });

  eventSource.addEventListener("turn", (event) => {
    const { speaker, text, action } = JSON.parse(event.data);
    turnsInPhase += 1;
    addBubble(speaker, text, action);
  });

  eventSource.addEventListener("scraper_code", (event) => {
    addPhaseCard(buildScraperCodeCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("scraper_run", (event) => {
    addPhaseCard(buildScraperRunCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("prep_code", (event) => {
    addPhaseCard(buildPrepCodeCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("prep_run", (event) => {
    addPhaseCard(buildPrepRunCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("exhibit", (event) => {
    addPhaseCard(buildExhibitCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("walkthrough", (event) => {
    addPhaseCard(buildWalkthroughCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("history_lookup", (event) => {
    addPhaseCard(buildHistoryLookupCard, JSON.parse(event.data));
  });

  eventSource.addEventListener("phase_done", (event) => {
    const data = JSON.parse(event.data);
    if (data.step === 3) addPhaseCard(buildCollectingCard, data);
    if (data.step === 4) addPhaseCard(buildPreparingCard, data);
  });

  // Named "app_error" (not "error") on purpose: EventSource treats a
  // literal "error"-named SSE event the same as its own native
  // connection-failure event, so both this listener and the onerror
  // handler below used to fire for the same message, with onerror's
  // generic "Connection lost" silently overwriting the real one.
  eventSource.addEventListener("app_error", (event) => {
    const { message } = event.data ? JSON.parse(event.data) : {};
    setProgress(`⚠️ Error: ${message || "Something went wrong."}`);
    setRunning(false);
    eventSource.close();
  });

  eventSource.addEventListener("done", (event) => {
    const data = event.data ? JSON.parse(event.data) : {};
    if (data.status === "completed" || (!data.status && !data.incomplete)) {
      setProgress("All 4 steps done. (Analysis/modeling is the next step — not part of this demo.)");
    } else {
      // incomplete / stopped / timed_out / anything else with a message —
      // never claim "all done" for a run that didn't actually finish.
      setProgress(`⚠️ ${data.message || "The run ended early."}`);
    }
    setRunning(false);
    eventSource.close();
  });

  eventSource.onerror = () => {
    if (eventSource.readyState === EventSource.CLOSED) return;
    setProgress("⚠️ Connection lost.");
    setRunning(false);
    eventSource.close();
  };
}

startBtn.addEventListener("click", () => {
  if (isRunning) {
    stopDemo();
  } else {
    startDemo();
  }
});
