"use strict";

const LIVE_RUNS_URL = "/api/runs";
const STATIC_RUNS_URL = "./runs-index.json";
const SYSTEM_FOLDERS = ["All", "Favorites", "My runs", "Default runs", "Bootstrap ladder"];

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const hasNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const exactNumber = (value, unit) => hasNumber(value)
  ? `<span title="${esc(`${String(value)} ${unit}`)}">${esc(Number(value).toLocaleString(undefined, { maximumSignificantDigits: 4 }))} ${esc(unit)}</span>`
  : "not emitted";

let runs = [];
let activeFolder = "All";
let liveIndexError = null;
const starred = new Map();
const starErrors = new Map();
const starPending = new Set();

function isRunStarred(run) {
  return run.live === true ? Boolean(run.starred) : Boolean(starred.get(String(run.run_id)));
}

function folderMatches(run) {
  if (activeFolder === "All") return true;
  if (activeFolder === "Favorites") return isRunStarred(run);
  return run.folder === activeFolder;
}

function filteredRuns() {
  const query = $("#run-filter").value.trim().toLocaleLowerCase();
  const sort = $("#run-sort").value;
  const visible = runs.filter((run) => folderMatches(run) && [
    run.run_id, run.name, run.feedstock_id, run.status, run.folder, run.summary
  ].some((value) => String(value ?? "").toLocaleLowerCase().includes(query)));
  return visible.sort((left, right) => {
    if (sort === "name") return String(left.name ?? "").localeCompare(String(right.name ?? ""));
    if (sort === "status") return String(left.status ?? "").localeCompare(String(right.status ?? ""));
    return String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""));
  });
}

function folderButtons() {
  return SYSTEM_FOLDERS.map((folder) =>
    `<button class="folder-button${folder === activeFolder ? " active" : ""}" type="button" data-folder="${esc(folder)}" aria-pressed="${folder === activeFolder}">${esc(folder)}</button>`
  ).join("");
}

function yieldChips(run) {
  const yields = run.headline_yields_kg;
  const entries = yields && typeof yields === "object" ? Object.entries(yields).filter(([species]) => species !== "O2") : [];
  const o2 = run.O2_source_side_potential_kg_cumulative ?? yields?.O2;
  if (o2 !== undefined || run.O2_metric_label) {
    entries.push([run.O2_metric_label || "O₂ source-side potential (not recovered)", o2]);
  }
  if (!entries.length) return "";
  return `<div class="yield-track">${entries.map(([species, value]) =>
    `<div class="yield-chip"><div class="el">${esc(species)}</div><div class="kg">${exactNumber(value, "kg")}</div></div>`
  ).join("")}</div>`;
}

function runCard(run) {
  const runId = String(run.run_id);
  const isStarred = isRunStarred(run);
  const starError = starErrors.get(runId);
  const isStarPending = starPending.has(runId);
  // Relative paths only: a leading "/" or "//" (protocol-relative) would let a
  // crafted index entry navigate the Load button off-origin.
  const staticArtifact = typeof run.artifact === "string" && /^(?!\/)[A-Za-z0-9._/-]+\.html$/.test(run.artifact) && !run.artifact.includes("..");
  const canLoad = Boolean(run.live) || staticArtifact;
  const loadTarget = run.live
    ? `./index.html?run=${encodeURIComponent(run.run_id)}`
    : run.artifact;
  const unavailable = canLoad ? "" : `<p class="demo-note">${esc(run.unavailable_note || "demo metadata — no artifact")}</p>`;
  const cancelledBadge = run.lifecycle === "cancelled" ? ` <span class="verdict contaminated">CANCELLED</span>` : "";
  return `<article class="card run-card">
    <div class="run-card-head">
      <div><div class="ct">${esc(run.folder ?? "unfiled")} · ${esc(run.status)}${cancelledBadge}</div><h2>${esc(run.name)}</h2></div>
      <button class="star-button${isStarred ? " active" : ""}" type="button" data-star="${esc(runId)}" aria-pressed="${isStarred}" aria-label="${esc(`${isStarred ? "Remove" : "Add"} ${run.name} ${isStarred ? "from" : "to"} favorites`)}"${isStarPending ? " disabled" : ""}>${isStarred ? "★" : "☆"}</button>
    </div>
    ${starError ? `<p class="demo-note" role="alert">${esc(`Could not save star for ${run.name}. ${starError}`)}</p>` : ""}
    <p class="run-summary">${esc(run.summary)}${hasNumber(run.hours) ? esc(` · ${run.hours} h`) : ""}${hasNumber(run.peak_T_C) ? esc(` · peak ${run.peak_T_C} °C`) : ""}</p>
    ${yieldChips(run)}
    ${unavailable}
    <div class="run-actions"><span class="mono">${esc(run.run_id)}</span><button class="load-button" type="button" data-load="${esc(loadTarget)}"${canLoad ? "" : " disabled"}>Load</button></div>
  </article>`;
}

function renderList() {
  $("#folder-list").innerHTML = folderButtons();
  const visible = filteredRuns();
  const fallbackNotice = liveIndexError
    ? `<div class="fatal"><strong>Live run index unavailable</strong><p>Showing the static sample index only. ${esc(liveIndexError.message || String(liveIndexError))}</p></div>`
    : "";
  $("#run-list").innerHTML = fallbackNotice + (visible.length
    ? visible.map(runCard).join("")
    : runs.length
      ? `<div class="pending"><strong>No matching runs</strong><p>No indexed run matches this folder and filter.</p></div>`
      : liveIndexError
        ? `<div class="pending"><strong>No static sample runs</strong><p>The live index could not be read, and the static sample index contains no entries.</p></div>`
        : `<div class="pending"><strong>No indexed runs</strong><p>The run index is valid but contains no entries.</p></div>`);
}

function bindControls() {
  $("#run-filter").addEventListener("input", renderList);
  $("#run-sort").addEventListener("change", renderList);
  $("#library").addEventListener("click", async (event) => {
    const folderButton = event.target.closest("[data-folder]");
    if (folderButton) {
      activeFolder = folderButton.dataset.folder;
      renderList();
      return;
    }
    const starButton = event.target.closest("[data-star]");
    if (starButton) {
      const runId = starButton.dataset.star;
      const run = runs.find((candidate) => String(candidate.run_id) === runId);
      if (!run || starPending.has(runId)) return;
      const nextStarred = !isRunStarred(run);
      if (run.live !== true) {
        starred.set(runId, nextStarred);
        renderList();
        return;
      }
      starErrors.delete(runId);
      starPending.add(runId);
      renderList();
      try {
        const response = await fetch(`${LIVE_RUNS_URL}/${encodeURIComponent(runId)}/meta`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ starred: nextStarred })
        });
        let payload;
        try {
          payload = await response.json();
        } catch (_error) {
          payload = null;
        }
        if (!response.ok) {
          throw new Error(payload?.error || `Star update failed (${response.status}).`);
        }
        if (typeof payload?.starred !== "boolean") {
          throw new Error("Star update returned a malformed response.");
        }
        run.starred = payload.starred;
      } catch (error) {
        starErrors.set(runId, error?.message || String(error));
      } finally {
        starPending.delete(runId);
        renderList();
      }
      return;
    }
    const loadButton = event.target.closest("[data-load]");
    if (loadButton && !loadButton.disabled && loadButton.dataset.load) {
      window.location.href = loadButton.dataset.load;
    }
  });
}

function render(index) {
  if (!Array.isArray(index)) throw new Error("Run index is malformed.");
  runs = index;
  starred.clear();
  runs.filter((run) => run.live !== true).forEach((run) => starred.set(String(run.run_id), Boolean(run.starred)));
  $("#library").innerHTML = `<header>
    <div class="masthead"><div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div><div class="doc-label">Engine-free<br>local index</div></div>
    <div class="eyebrow">PHASE 2 · RUN LIBRARY</div><h1>Run library</h1>
    <p class="lede">Browse frozen-run metadata. Live-run stars are saved durably; sample-entry stars are client-side only. This screen does not execute runs.</p>
  </header>
  <section><h2><span class="sect">01</span>Find a run</h2><p class="sub">Fixed system folders, text filter, and index-only sorting.</p>
    <div class="library-controls"><nav id="folder-list" class="folder-list" aria-label="System folders"></nav>
      <div class="filter-controls"><label>Filter<input id="run-filter" type="search" placeholder="Name, ID, feedstock, status…"></label>
      <label>Sort<select id="run-sort"><option value="created">Newest created</option><option value="name">Name</option><option value="status">Status</option></select></label></div></div>
  </section>
  <section><h2><span class="sect">02</span>Indexed runs</h2><p class="sub">Only entries with a local artifact can be loaded.</p><div id="run-list" class="run-list"></div></section>
  <footer class="footer"><span>Static index + local run API · engine-free</span><a href="./index.html">Open sample report</a></footer>`;
  bindControls();
  renderList();
}

fetch(STATIC_RUNS_URL)
  .then((response) => {
    if (!response.ok) throw new Error(`Run index request failed (${response.status})`);
    return response.json();
  })
  .then((staticRuns) => fetch(LIVE_RUNS_URL)
    .then((response) => {
      if (!response.ok) throw new Error(`Live run index request failed (${response.status})`);
      return response.json();
    })
    .then((index) => index.map((run) => ({ ...run, live: true })))
    .catch((error) => {
      liveIndexError = error;
      return [];
    })
    .then((liveRuns) => {
      const liveIds = new Set(liveRuns.map((run) => String(run.run_id)));
      return [
        ...liveRuns,
        ...staticRuns.filter((run) => !liveIds.has(String(run.run_id)))
      ];
    }))
  .then(render)
  .catch((error) => {
    $("#library").innerHTML = `<div class="fatal"><div class="eyebrow">Library unavailable</div><h1>Could not read the run index</h1><p>${esc(error.message)}</p><p>If your browser blocks local <code>file:</code> fetches, serve this directory with an offline local static server and open <code>library.html</code> there.</p></div>`;
  });
