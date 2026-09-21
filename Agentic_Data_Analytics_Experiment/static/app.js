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

function dataTable(columns, rows) {
  if (!Array.isArray(columns) || !Array.isArray(rows) || rows.length === 0) return null;

  const wrap = document.createElement("div");
  wrap.className = "table-scroll";

  const table = document.createElement("table");
  table.className = "preview-table";

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
      td.textContent = text.length > 40 ? `${text.slice(0, 40)}…` : text;
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

function buildCollectingCard(data) {
  const { download } = data || {};
  if (!download || Object.keys(download).length === 0) return null;

  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  heading.textContent = download.fallback
    ? "⚠️ Falling back to best available data (not individual-level)"
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
  dl.textContent = download.success
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
  const { profile, clean, store, sql, sketch, preview } = data || {};
  const hasAnything = (profile && "n_rows" in profile) || (clean && "rows_after" in clean) || (store && store.table_name);
  if (!hasAnything) return null;

  const card = document.createElement("article");
  card.className = "phase-card";

  const heading = document.createElement("h2");
  heading.textContent = store && store.table_name
    ? "✅ Data cleaned and stored in a real SQLite database"
    : "⚠️ Preparing & storing didn't finish";
  card.appendChild(heading);

  if (profile && "n_rows" in profile) {
    const grid = document.createElement("div");
    grid.className = "stat-grid";
    grid.append(
      statTile("rows (raw)", profile.n_rows.toLocaleString()),
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
    cl.textContent = `Cleaned: ${clean.rows_before.toLocaleString()} → ${clean.rows_after.toLocaleString()} rows (${clean.dropped_duplicates.toLocaleString()} duplicates, ${clean.dropped_missing.toLocaleString()} missing-value rows dropped).`;
    card.appendChild(cl);
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
    previewHeading.textContent = `First ${preview.rows.length} rows (cleaned)`;
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
