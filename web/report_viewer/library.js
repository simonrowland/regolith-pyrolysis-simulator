"use strict";

const RUNS_URL = "./runs-index.json";
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
const starred = new Map();

function folderMatches(run) {
  if (activeFolder === "All") return true;
  if (activeFolder === "Favorites") return Boolean(starred.get(run.run_id));
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
  if (!yields || typeof yields !== "object") return "";
  return `<div class="yield-track">${Object.entries(yields).map(([species, value]) =>
    `<div class="yield-chip"><div class="el">${esc(species)}</div><div class="kg">${exactNumber(value, "kg")}</div></div>`
  ).join("")}</div>`;
}

function runCard(run) {
  const isStarred = Boolean(starred.get(run.run_id));
  const canLoad = typeof run.artifact === "string" && /^[A-Za-z0-9._/-]+\.html$/.test(run.artifact);
  const unavailable = canLoad ? "" : `<p class="demo-note">${esc(run.unavailable_note || "demo metadata — no artifact")}</p>`;
  return `<article class="card run-card">
    <div class="run-card-head">
      <div><div class="ct">${esc(run.folder)} · ${esc(run.status)}</div><h2>${esc(run.name)}</h2></div>
      <button class="star-button${isStarred ? " active" : ""}" type="button" data-star="${esc(run.run_id)}" aria-pressed="${isStarred}" aria-label="${esc(`${isStarred ? "Remove" : "Add"} ${run.name} ${isStarred ? "from" : "to"} favorites`)}">${isStarred ? "★" : "☆"}</button>
    </div>
    <p class="run-summary">${esc(run.summary)}</p>
    ${yieldChips(run)}
    ${unavailable}
    <div class="run-actions"><span class="mono">${esc(run.run_id)}</span><button class="load-button" type="button" data-load="${esc(run.artifact)}"${canLoad ? "" : " disabled"}>Load</button></div>
  </article>`;
}

function renderList() {
  $("#folder-list").innerHTML = folderButtons();
  const visible = filteredRuns();
  $("#run-list").innerHTML = visible.length
    ? visible.map(runCard).join("")
    : runs.length
      ? `<div class="pending"><strong>No matching runs</strong><p>No indexed run matches this folder and filter.</p></div>`
      : `<div class="pending"><strong>No indexed runs</strong><p>The run index is valid but contains no entries.</p></div>`;
}

function bindControls() {
  $("#run-filter").addEventListener("input", renderList);
  $("#run-sort").addEventListener("change", renderList);
  $("#library").addEventListener("click", (event) => {
    const folderButton = event.target.closest("[data-folder]");
    if (folderButton) {
      activeFolder = folderButton.dataset.folder;
      renderList();
      return;
    }
    const starButton = event.target.closest("[data-star]");
    if (starButton) {
      const runId = starButton.dataset.star;
      starred.set(runId, !starred.get(runId));
      renderList();
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
  runs.forEach((run) => starred.set(String(run.run_id), Boolean(run.starred)));
  $("#library").innerHTML = `<header>
    <div class="masthead"><div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div><div class="doc-label">Engine-free<br>local index</div></div>
    <div class="eyebrow">PHASE 2 · RUN LIBRARY</div><h1>Run library</h1>
    <p class="lede">Browse frozen-run metadata. Stars are client state only; this screen does not save or execute runs.</p>
  </header>
  <section><h2><span class="sect">01</span>Find a run</h2><p class="sub">Fixed system folders, text filter, and index-only sorting.</p>
    <div class="library-controls"><nav id="folder-list" class="folder-list" aria-label="System folders"></nav>
      <div class="filter-controls"><label>Filter<input id="run-filter" type="search" placeholder="Name, ID, feedstock, status…"></label>
      <label>Sort<select id="run-sort"><option value="created">Newest created</option><option value="name">Name</option><option value="status">Status</option></select></label></div></div>
  </section>
  <section><h2><span class="sect">02</span>Indexed runs</h2><p class="sub">Only entries with a local artifact can be loaded.</p><div id="run-list" class="run-list"></div></section>
  <footer class="footer"><span>Local flatfile index · engine-free · no backend calls</span><a href="./index.html">Open sample report</a></footer>`;
  bindControls();
  renderList();
}

fetch(RUNS_URL)
  .then((response) => {
    if (!response.ok) throw new Error(`Run index request failed (${response.status})`);
    return response.json();
  })
  .then(render)
  .catch((error) => {
    $("#library").innerHTML = `<div class="fatal"><div class="eyebrow">Library unavailable</div><h1>Could not read the run index</h1><p>${esc(error.message)}</p><p>If your browser blocks local <code>file:</code> fetches, serve this directory with an offline local static server and open <code>library.html</code> there.</p></div>`;
  });
