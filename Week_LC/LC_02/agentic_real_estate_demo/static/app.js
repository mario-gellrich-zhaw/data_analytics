const startBtn = document.getElementById("start-btn");
const statusBar = document.getElementById("status-bar");
const statusText = document.getElementById("status-text");
const progressFill = document.getElementById("progress-fill");
const chat = document.getElementById("chat");
const resultSection = document.getElementById("result");

// Chapter length is dynamic (the agents decide when a chapter is done), so
// this is just an estimate for a smoothly-filling per-chapter progress bar.
const EXPECTED_TURNS_PER_CHAPTER = 8;

let turnsInChapter = 0;
let currentChapter = 1;
let eventSource = null;
let isRunning = false;

function speakerClass(speaker) {
  return speaker === "Data Researcher" ? "researcher" : "expert";
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

function setProgress(stage) {
  statusText.textContent = `Chapter ${currentChapter} · ${stage}`;
  const pct = Math.min(100, Math.round((turnsInChapter / EXPECTED_TURNS_PER_CHAPTER) * 100));
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

function previewTable(preview) {
  if (!preview || !Array.isArray(preview.columns) || !Array.isArray(preview.rows) || preview.rows.length === 0) {
    return null;
  }

  const wrap = document.createElement("div");
  wrap.className = "table-scroll";

  const table = document.createElement("table");
  table.className = "preview-table";

  const thead = document.createElement("tr");
  preview.columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col;
    thead.appendChild(th);
  });
  table.appendChild(thead);

  preview.rows.forEach((row) => {
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

function buildChapterCard(chapterNum, data) {
  const { download, analysis, preview } = data || {};
  const hasAnything = (download && Object.keys(download).length > 0) || (analysis && "n_rows" in analysis);
  if (!hasAnything) return null;

  const card = document.createElement("article");
  card.className = "chapter-card";

  const heading = document.createElement("h2");
  heading.textContent = analysis && analysis.n_rows
    ? `✅ Chapter ${chapterNum} — real data downloaded and analyzed`
    : `⚠️ Chapter ${chapterNum} — didn't finish downloading/analyzing real data`;
  card.appendChild(heading);

  if (download && download.dataset_title) {
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

  if (download && download.success) {
    const dl = document.createElement("p");
    dl.className = "result-meta";
    dl.textContent = `Download: ${download.bytes.toLocaleString()} bytes saved (${download.format || "file"}).`;
    card.appendChild(dl);
  } else if (download && download.error) {
    const dl = document.createElement("p");
    dl.className = "result-meta";
    dl.textContent = `Download failed: ${download.error}`;
    card.appendChild(dl);
  }

  if (analysis && "n_rows" in analysis) {
    const grid = document.createElement("div");
    grid.className = "stat-grid";
    grid.append(
      statTile("rows", analysis.n_rows.toLocaleString()),
      statTile("columns", analysis.n_columns),
      statTile("duplicate rows", analysis.duplicate_rows.toLocaleString()),
      statTile("cols with missing values", Object.keys(analysis.missing_values || {}).length),
    );
    card.appendChild(grid);

    const missingEntries = Object.entries(analysis.missing_values || {});
    if (missingEntries.length > 0) {
      const missingList = document.createElement("ul");
      missingList.className = "dataset-list";
      missingEntries.slice(0, 6).forEach(([col, count]) => {
        const li = document.createElement("li");
        li.className = "dataset-item";
        li.textContent = `${col}: ${count.toLocaleString()} missing`;
        missingList.appendChild(li);
      });
      card.appendChild(missingList);
    }
  }

  const table = previewTable(preview);
  if (table) {
    const previewHeading = document.createElement("h3");
    previewHeading.className = "preview-heading";
    previewHeading.textContent = `First ${preview.rows.length} rows`;
    card.append(previewHeading, table);
  }

  return card;
}

function addChapterCard(chapterNum, data) {
  const card = buildChapterCard(chapterNum, data);
  if (!card) return;
  resultSection.classList.remove("hidden");
  resultSection.appendChild(card);
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
  resultSection.classList.add("hidden");
  resultSection.innerHTML = "";
  currentChapter = 1;
  turnsInChapter = 0;
  setProgress("Connecting…");

  eventSource = new EventSource("/api/stream");

  eventSource.addEventListener("progress", (event) => {
    const { stage } = JSON.parse(event.data);
    setProgress(stage);
  });

  eventSource.addEventListener("turn", (event) => {
    const { speaker, text, action } = JSON.parse(event.data);
    turnsInChapter += 1;
    addBubble(speaker, text, action);
  });

  eventSource.addEventListener("chapter_done", (event) => {
    const data = JSON.parse(event.data);
    addChapterCard(data.chapter, data);
    currentChapter = data.chapter + 1;
    turnsInChapter = 0;
  });

  eventSource.addEventListener("error", (event) => {
    if (event.data) {
      const { message } = JSON.parse(event.data);
      setProgress(`⚠️ Error: ${message}`);
    }
  });

  eventSource.addEventListener("done", () => {
    setProgress("Finished.");
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
