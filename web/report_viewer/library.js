"use strict";

const { fmtNum, fmtRunId, prettySpecies } = globalThis.ReportLabels;
const LIVE_RUNS_URL = "/api/runs";
const STATIC_RUNS_URL = "./runs-index.json";
const SYSTEM_FOLDERS = ["All", "Favorites", "My runs", "Default runs", "Bootstrap ladder"];
// Match report-viewer: nameless live runs often use the raw hash as `name`.
const isHashLike = (value) => typeof value === "string"
  && /^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f-]{27,})$/i.test(value.trim());

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const hasNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const exactNumber = (value, unit) => hasNumber(value)
  ? `<span title="${esc(`${String(value)}${unit ? ` ${unit}` : ""}`)}">${esc(fmtNum(value, unit))}</span>`
  : "not emitted";
const runIdSpan = (value) => `<span title="${esc(value)}">${esc(fmtRunId(value))}</span>`;
const speciesSpan = (value) => esc(prettySpecies(value));
// Readable H2: never dump 32 hex chars as the card title (full id stays in the mono span + title).
const runDisplayTitle = (run) => {
  const name = typeof run.name === "string" ? run.name.trim() : "";
  return name && !isHashLike(name) ? name : `Untitled run · ${fmtRunId(run.run_id ?? name)}`;
};

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

function folderCount(folder) {
  if (folder === "All") return runs.length;
  if (folder === "Favorites") return runs.filter((run) => isRunStarred(run)).length;
  return runs.filter((run) => run.folder === folder).length;
}

function runSearchHaystack(run) {
  const campaign = Array.isArray(run.campaign_chain) ? run.campaign_chain.join(" ") : "";
  return [run.run_id, run.name, run.feedstock_id, run.status, run.folder, run.summary, campaign]
    .map((value) => String(value ?? "").toLocaleLowerCase())
    .join(" ");
}

function filteredRuns() {
  const query = $("#run-filter").value.trim().toLocaleLowerCase();
  const sort = $("#run-sort").value;
  const visible = runs.filter((run) => folderMatches(run) && (
    !query || runSearchHaystack(run).includes(query)
  ));
  return visible.sort((left, right) => {
    if (sort === "name") {
      return runDisplayTitle(left).localeCompare(runDisplayTitle(right));
    }
    if (sort === "status") return String(left.status ?? "").localeCompare(String(right.status ?? ""));
    return String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""));
  });
}

function folderButtons() {
  return SYSTEM_FOLDERS.map((folder) => {
    const count = folderCount(folder);
    return `<button class="folder-button${folder === activeFolder ? " active" : ""}" type="button" data-folder="${esc(folder)}" aria-pressed="${folder === activeFolder}">${esc(folder)} <span class="folder-count">${esc(String(count))}</span></button>`;
  }).join("");
}

// Headline chips must not invent recovery/origin claims. Prefer explicit
// headline_yield_semantics; O₂ without a label is source-side potential only.
function yieldQualifier(species, semantics) {
  const key = String(species ?? "");
  const token = semantics && typeof semantics === "object" ? semantics[key] : null;
  if (token === "evolved_product") return "evolved";
  if (token === "source_side_potential") return "source-side potential (not recovered)";
  if (typeof token === "string" && token.trim()) return token.trim().replace(/_/g, " ");
  return "";
}

function yieldChips(run) {
  const yields = run.headline_yields_kg;
  const semantics = run.headline_yield_semantics && typeof run.headline_yield_semantics === "object"
    ? run.headline_yield_semantics
    : {};
  const entries = yields && typeof yields === "object"
    ? Object.entries(yields).filter(([species]) => species !== "O2")
    : [];
  const chips = entries.map(([species, value]) => {
    const qualifier = yieldQualifier(species, semantics);
    return `<div class="yield-chip"><div class="el">${speciesSpan(species)}</div>` +
      `<div class="kg">${exactNumber(value, "kg")}${qualifier ? ` · ${esc(qualifier)}` : ""}</div></div>`;
  });
  const o2 = run.O2_source_side_potential_kg_cumulative ?? yields?.O2;
  if (o2 !== undefined || run.O2_metric_label || semantics.O2 === "source_side_potential") {
    // Never present O₂ as recovered product mass: default label is source-side only.
    const o2Label = run.O2_metric_label
      || (semantics.O2 === "source_side_potential" || o2 !== undefined
        ? "O₂ source-side potential (not recovered)"
        : "O₂ metric label not emitted");
    chips.push(`<div class="yield-chip"><div class="el">${esc(o2Label)}</div>` +
      `<div class="kg">${exactNumber(o2, "kg")}</div></div>`);
  }
  if (!chips.length) return "";
  return `<div class="yield-track" aria-label="Headline mass metrics">${chips.join("")}</div>`;
}

function runMetaLine(run) {
  // Prefer structured fields over the API's yield-only summary string (often unformatted
  // multi-sig-fig noise that duplicates the yield chips).
  const parts = [];
  if (run.feedstock_id) parts.push(String(run.feedstock_id));
  if (Array.isArray(run.campaign_chain) && run.campaign_chain.length) {
    parts.push(run.campaign_chain.map((step) => String(step)).join("→"));
  }
  const hasYields = Boolean(
    run.headline_yields_kg
    && typeof run.headline_yields_kg === "object"
    && Object.keys(run.headline_yields_kg).length
  );
  const summary = typeof run.summary === "string" ? run.summary.trim() : "";
  if (!hasYields && summary) parts.push(summary);
  if (hasNumber(run.hours)) parts.push(exactNumber(run.hours, "h"));
  if (hasNumber(run.peak_T_C)) parts.push(`peak ${exactNumber(run.peak_T_C, "°C")}`);
  if (run.created_at) {
    const stamp = String(run.created_at);
    const day = stamp.slice(0, 10);
    parts.push(/^\d{4}-\d{2}-\d{2}$/.test(day) ? day : stamp);
  }
  return parts;
}

function runCard(run) {
  const runId = String(run.run_id);
  const title = runDisplayTitle(run);
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
  const displayName = title;
  const cancelledBadge = run.lifecycle === "cancelled" ? ` <span class="verdict contaminated">CANCELLED</span>` : "";
  // Omit "unfiled" noise when the index has no folder (typical for live runs).
  const folderStatus = run.folder
    ? `${esc(run.folder)} · ${esc(run.status)}${cancelledBadge}`
    : `${esc(run.status)}${cancelledBadge}`;
  const meta = runMetaLine(run);
  const metaHtml = meta.length
    ? `<p class="run-summary">${meta.map((part) => (part.includes("<span") ? part : esc(part))).join(" · ")}</p>`
    : "";
  const loadDisabledAttrs = canLoad
    ? ` aria-label="${esc(`Load report for ${displayName}`)}"`
    : ` disabled aria-disabled="true" title="${esc(run.unavailable_note || "No loadable artifact for this entry")}" aria-label="${esc(`Load unavailable for ${displayName}`)}"`;
  return `<article class="card run-card">
    <div class="run-card-head">
      <div class="run-card-title"><div class="ct">${folderStatus}</div><h2 title="${esc(typeof run.name === "string" && run.name.trim() ? run.name : run.run_id)}">${esc(displayName)}</h2></div>
      <button class="star-button${isStarred ? " active" : ""}" type="button" data-star="${esc(runId)}" aria-pressed="${isStarred}" aria-label="${esc(`${isStarred ? "Remove" : "Add"} ${displayName} ${isStarred ? "from" : "to"} favorites`)}"${isStarPending ? " disabled aria-busy=\"true\"" : ""}>${isStarred ? "★" : "☆"}</button>
    </div>
    ${starError ? `<p class="demo-note" role="alert">${esc(`Could not save star for ${displayName}. ${starError}`)}</p>` : ""}
    ${metaHtml}
    ${yieldChips(run)}
    ${unavailable}
    <div class="run-actions"><span class="mono">${runIdSpan(run.run_id)}</span><button class="load-button" type="button" data-load="${esc(loadTarget)}"${loadDisabledAttrs}>Load</button></div>
  </article>`;
}

function renderList() {
  $("#folder-list").innerHTML = folderButtons();
  const visible = filteredRuns();
  const fallbackNotice = liveIndexError
    ? `<div class="fatal"><strong>Live run index unavailable</strong><p>Showing the static sample index only. ${esc(liveIndexError.message || String(liveIndexError))}</p></div>`
    : "";
  const countLine = runs.length
    ? `<p class="sub run-count" id="run-count">Showing ${esc(String(visible.length))} of ${esc(String(runs.length))} indexed run${runs.length === 1 ? "" : "s"}</p>`
    : "";
  $("#run-list").innerHTML = fallbackNotice + countLine + (visible.length
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
  <section><h2 id="find-run-heading"><span class="sect">01</span>Find a run</h2><p class="sub">Fixed system folders, text filter, and index-only sorting.</p>
    <div class="library-controls"><nav id="folder-list" class="folder-list" role="toolbar" aria-label="System folders"></nav>
      <div class="filter-controls"><label for="run-filter">Filter<input id="run-filter" type="search" autocomplete="off" placeholder="Name, ID, feedstock, status…"></label>
      <label for="run-sort">Sort<select id="run-sort"><option value="created">Newest created</option><option value="name">Name</option><option value="status">Status</option></select></label></div></div>
  </section>
  <section><h2 id="indexed-runs-heading"><span class="sect">02</span>Indexed runs</h2><p class="sub">Only entries with a local artifact or live API id can be loaded. Yield chips use source-side O₂ labels (not recovered product).</p><div id="run-list" class="run-list" role="region" aria-labelledby="indexed-runs-heading"></div></section>
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
