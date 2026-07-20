"use strict";

const { fmtNum, fmtRunId, prettySpecies } = globalThis.ReportLabels;
const RUN_ID = new URLSearchParams(window.location.search).get("run");
const RUN_QUERY = RUN_ID ? `?run=${encodeURIComponent(RUN_ID)}` : "";
const ARTIFACT_URL = RUN_ID
  ? `/api/runs/${encodeURIComponent(RUN_ID)}`
  : "./sample-run-artifact.json";
// Prefer human labels over raw ledger/engine keys (report-viewer uses the same set).
const ENGINE_IDENTITY_LABELS = Object.freeze({
  name: "Engine name",
  backend_wire_token: "Backend wire token",
  kernel_commit_sha: "Kernel commit",
  cache_version: "Engine cache version"
});
const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const hasNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const displayNumber = (value, unit = "") => hasNumber(value)
  ? `<span title="${esc(`${String(value)}${unit ? ` ${unit}` : ""}`)}">${esc(fmtNum(value, unit))}</span>`
  : "not emitted";
const isHashLike = (value) => typeof value === "string"
  && /^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(value.trim());
const runIdSpan = (value) => `<span title="${esc(value)}">${esc(fmtRunId(value))}</span>`;
const speciesSpan = (value) => esc(prettySpecies(value));
// Nameless live runs mirror the run_id hash into `name`; do not dump 32 hex
// chars as the settings lede. Full id stays on the run-id span tooltip.
const settingsLede = (header) => {
  const name = typeof header.name === "string" ? header.name.trim() : "";
  const runId = header.run_id ?? name;
  if (name && !isHashLike(name) && name !== String(runId ?? "")) {
    return `${esc(name)} · <span class="mono">${runIdSpan(runId)}</span>`;
  }
  return `<span class="mono">${runIdSpan(runId)}</span>`;
};

function pending(title, detail) {
  return `<div class="pending"><strong>${esc(title)}</strong><p>${esc(detail)}</p></div>`;
}

function dataBlock(value) {
  if (value === undefined || value === null) {
    return pending("Not captured", "This field is absent from the artifact header.");
  }
  return `<pre class="data-block">${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function settingsField(number, title, subtitle, content) {
  return `<section><h2><span class="sect">${esc(String(number).padStart(2, "0"))}</span>${esc(title)}</h2><p class="sub">${esc(subtitle)}</p>${content}</section>`;
}

function formatScalar(value) {
  if (value === undefined || value === null) return "not emitted";
  if (typeof value === "number") return displayNumber(value);
  if (typeof value === "boolean") return esc(String(value));
  if (typeof value === "string") {
    return isHashLike(value) ? runIdSpan(value) : esc(value);
  }
  return esc(JSON.stringify(value));
}

// Engine-identity values only. cache_version carries a provenance blob (binary
// path, host, sha256 digest) that dumps ~250 chars of line noise into the
// table; show a readable head with the full value on the tooltip. Config and
// setpoint leaves keep formatScalar's untruncated rendering.
const IDENTITY_MAX = 60;

function formatIdentityScalar(value) {
  if (typeof value !== "string" || isHashLike(value) || value.length <= IDENTITY_MAX) {
    return formatScalar(value);
  }
  return `<span title="${esc(value)}">${esc(value.slice(0, IDENTITY_MAX).trimEnd())}…</span>`;
}

function costBlock(cost) {
  if (!cost || typeof cost !== "object") {
    return pending("Pending W-A5a", "header.cost_block is absent.");
  }
  const provenance = typeof cost.provenance === "string" && cost.provenance.trim()
    ? cost.provenance.trim()
    : null;
  return `<div class="cards">
    <div class="card"><div class="ct">Owner energy price · electrical</div><div class="cbig">${displayNumber(cost.electrical_cost_per_kWh, "USD/kWh")}</div></div>
    <div class="card"><div class="ct">Owner energy price · solar heat</div><div class="cbig">${displayNumber(cost.solar_heat_cost_per_kWh, "USD/kWh")}</div></div>
  </div>${provenance ? `<div class="note">Price provenance: ${esc(provenance)}</div>` : ""}`;
}

function c3DoseBlock(dose) {
  if (!dose || typeof dose !== "object" || Array.isArray(dose) || !Object.keys(dose).length) {
    return pending("Not captured", "This field is absent from the artifact header.");
  }
  const rows = Object.entries(dose).map(([species, value]) => {
    const label = species.replace(/_kg$/, "");
    return `<tr><td>${speciesSpan(label)}</td><td class="num">${displayNumber(value, "kg")}</td></tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr><th>Species</th><th class="num">Dose · kg</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function engineIdentityBlock(identity) {
  if (identity === undefined || identity === null) {
    return pending("Not captured", "This field is absent from the artifact header.");
  }
  if (typeof identity !== "object" || Array.isArray(identity)) return dataBlock(identity);
  const entries = Object.entries(identity);
  if (!entries.length) {
    return pending("Engine identity empty", "header.engine_identity carried no keys.");
  }
  const rows = entries.map(([key, value]) => {
    const label = ENGINE_IDENTITY_LABELS[key] || key;
    return `<tr><th>${esc(label)}</th><td class="mono">${formatIdentityScalar(value)}</td></tr>`;
  }).join("");
  return `<div class="table-wrap"><table><tbody>${rows}</tbody></table></div>`;
}

function flattenLeaves(value, prefix = "") {
  if (value === null || value === undefined || typeof value !== "object") {
    return [{ path: prefix || "(root)", value }];
  }
  if (Array.isArray(value)) {
    if (!value.length) return [{ path: prefix || "(root)", value: [] }];
    return value.flatMap((item, index) => flattenLeaves(item, prefix ? `${prefix}[${index}]` : `[${index}]`));
  }
  const entries = Object.entries(value);
  if (!entries.length) return [{ path: prefix || "(root)", value: {} }];
  return entries.flatMap(([key, item]) => flattenLeaves(item, prefix ? `${prefix}.${key}` : key));
}

function recipeSnapshotBlock(snapshot) {
  if (snapshot === undefined || snapshot === null) {
    return pending("Not captured", "This field is absent from the artifact header.");
  }
  if (typeof snapshot !== "object" || Array.isArray(snapshot)) return dataBlock(snapshot);

  const schema = snapshot.recipe_schema_version;
  const pins = snapshot.pins;
  const patch = snapshot.setpoints_patch;
  const known = new Set(["recipe_schema_version", "pins", "setpoints_patch"]);
  const extras = Object.keys(snapshot).filter((key) => !known.has(key));

  const schemaRow = typeof schema === "string" && schema.length
    ? esc(schema)
    : schema === undefined || schema === null
      ? "not emitted"
      : formatScalar(schema);

  let pinsContent;
  if (pins === undefined || pins === null) {
    pinsContent = "not emitted";
  } else if (!Array.isArray(pins)) {
    pinsContent = formatScalar(pins);
  } else if (!pins.length) {
    pinsContent = `<span class="chip">no pins</span>`;
  } else {
    pinsContent = pins.map((pin) => `<span class="chip mono" title="${esc(pin)}">${esc(pin)}</span>`).join(" ");
  }

  let setpointsContent;
  if (patch === undefined || patch === null) {
    setpointsContent = pending("Setpoints not emitted", "recipe_snapshot.setpoints_patch is absent.");
  } else if (typeof patch !== "object" || Array.isArray(patch)) {
    setpointsContent = dataBlock(patch);
  } else if (!Object.keys(patch).length) {
    setpointsContent = `<div class="note">Empty setpoints patch (no owner overrides captured).</div>`;
  } else {
    const leaves = flattenLeaves(patch);
    setpointsContent = `<div class="table-wrap"><table><thead><tr><th>Setpoint</th><th>Value</th></tr></thead><tbody>${leaves.map((leaf) => `<tr><td class="mono">${esc(leaf.path)}</td><td class="mono">${formatScalar(leaf.value)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  const extrasBlock = extras.length
    ? `<div class="note">Additional snapshot keys (shown raw; not part of the run.yaml contract): ${extras.map((key) => esc(key)).join(", ")}</div><pre class="data-block">${esc(JSON.stringify(Object.fromEntries(extras.map((key) => [key, snapshot[key]])), null, 2))}</pre>`
    : "";

  return `<div class="cards recipe-snapshot-cards">
    <div class="card"><div class="ct">Recipe schema</div><div class="cbig" style="font-size:18px">${schemaRow}</div></div>
    <div class="card"><div class="ct">Pinned paths</div><div class="recipe-pins">${pinsContent}</div></div>
  </div>
  <h3 class="settings-subhead">Setpoints patch</h3>
  ${setpointsContent}
  ${extrasBlock}`;
}

function configEntries(config) {
  if (!config || typeof config !== "object") return [];
  return Object.entries(config).map(([key, item]) => {
    if (item && typeof item === "object" && !Array.isArray(item) && ("value" in item || "source" in item)) {
      return { key, value: item.value, source: item.source };
    }
    return { key, value: item, source: "unknown" };
  });
}

function effectiveConfig(config) {
  if (!config || typeof config !== "object") {
    return pending(
      "Effective config not captured",
      "This run's artifact does not carry header.effective_config (live runs record it at the submit merge point)."
    );
  }
  const entries = configEntries(config);
  if (!entries.length) {
    return pending("Effective config empty", "No per-key values were captured.");
  }
  // Non-default sources first so a 600-key dump is still scannable for overrides.
  entries.sort((left, right) => {
    const leftDefault = String(left.source ?? "unknown") === "default";
    const rightDefault = String(right.source ?? "unknown") === "default";
    if (leftDefault !== rightDefault) return leftDefault ? 1 : -1;
    return String(left.key).localeCompare(String(right.key));
  });
  const overrideCount = entries.filter((entry) => String(entry.source ?? "unknown") !== "default").length;
  const summary = `<div class="note">${esc(String(entries.length))} keys · ${esc(String(overrideCount))} non-default source${overrideCount === 1 ? "" : "s"} (highlighted).</div>`;
  return `${summary}<div class="table-wrap"><table><thead><tr><th>Key</th><th>Value</th><th>Source</th></tr></thead><tbody>${entries.map((entry) => {
    const source = String(entry.source ?? "unknown");
    const nonDefault = source !== "default";
    return `<tr class="${nonDefault ? "config-override" : ""}"><td class="mono">${esc(entry.key)}</td><td class="mono">${formatScalar(entry.value)}</td><td><span class="chip${nonDefault ? " accent" : ""}">${esc(source)}</span></td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function yamlScalar(value) {
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(String(value));
}

function toYaml(value, depth = 0) {
  const indent = "  ".repeat(depth);
  if (Array.isArray(value)) {
    if (!value.length) return `${indent}[]`;
    return value.map((item) => item && typeof item === "object"
      ? `${indent}-\n${toYaml(item, depth + 1)}`
      : `${indent}- ${yamlScalar(item)}`).join("\n");
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return `${indent}{}`;
    return entries.map(([key, item]) => item && typeof item === "object"
      ? `${indent}${JSON.stringify(key)}:\n${toYaml(item, depth + 1)}`
      : `${indent}${JSON.stringify(key)}: ${yamlScalar(item)}`).join("\n");
  }
  return `${indent}${yamlScalar(value)}`;
}

function downloadHeader(header) {
  const blob = new Blob([`${toYaml(header)}\n`], { type: "application/yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "run-header.yaml";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function render(artifact) {
  if (!artifact || !artifact.header || typeof artifact.header !== "object") throw new Error("Artifact header is absent or malformed.");
  const header = artifact.header;
  // Mirror the export endpoint's validity rule exactly — offering a download
  // the endpoint would 409 is a false affordance.
  const snapshot = header.recipe_snapshot;
  const hasRecipeSnapshot = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
    && snapshot.setpoints_patch && typeof snapshot.setpoints_patch === "object" && !Array.isArray(snapshot.setpoints_patch)
    && Array.isArray(snapshot.pins) && snapshot.pins.every((pin) => typeof pin === "string")
    && typeof snapshot.recipe_schema_version === "string" && snapshot.recipe_schema_version.length > 0;
  const manifestAction = RUN_ID
    ? hasRecipeSnapshot
      ? `<a class="settings-action" href="/api/runs/${encodeURIComponent(RUN_ID)}/run.yaml" download>Download run.yaml</a>`
      : `<button type="button" disabled aria-disabled="true" title="Artifact carries no recipe snapshot; export would return 409" aria-label="Download run.yaml unavailable — artifact carries no recipe snapshot">Download run.yaml unavailable</button>`
    : "";
  const downloadNote = RUN_ID
    ? hasRecipeSnapshot
      ? "The run.yaml export contains the captured recipe snapshot and identifying run inputs."
      : "This artifact carries no recipe snapshot; run.yaml export is unavailable (endpoint would 409)."
    : "Static samples offer captured-header download only; run.yaml export is available for live runs.";
  $("#settings").innerHTML = `<header>
    <div class="masthead"><div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div><div class="doc-label">Read-only<br>owner T-8</div></div>
    <div class="eyebrow">PHASE 2 · SETTINGS INSPECTOR</div><h1>Captured run settings</h1>
    <p class="lede">${settingsLede(header)} · settings copied from the frozen artifact header.</p>
    <div class="settings-actions"><a class="settings-action" href="./index.html${RUN_QUERY}">← Back to report</a><button id="download-run" type="button">Download captured header (YAML)</button>${manifestAction}</div>
    <div class="note"><b>Read-only.</b> Config editing remains owner T-8. ${downloadNote}</div>
  </header>
  ${settingsField(1, "Recipe snapshot", "Captured recipe material only; absent values are not reconstructed.", recipeSnapshotBlock(header.recipe_snapshot))}
  ${settingsField(2, "Engine identity", "Backend identity recorded by the run header.", engineIdentityBlock(header.engine_identity))}
  ${settingsField(3, "C3 dose · kg by species", "Captured alkali-shuttle dose in kg (not mol); no recipe inference.", c3DoseBlock(header.c3_dose))}
  ${settingsField(4, "Owner's two energy prices", "Electrical and solar-heat prices bind directly to header.cost_block.", costBlock(header.cost_block))}
  ${settingsField(5, "Effective config", "Per-key merged value and source; non-default sources sort first and are highlighted.", effectiveConfig(header.effective_config))}
  <footer class="footer"><span>Frozen header inspection · engine-free · no edit controls</span><a href="./library.html">Run library</a></footer>`;
  $("#download-run").addEventListener("click", () => downloadHeader(header));
}

function artifactFetchError(response) {
  if (response.status === 404) {
    return RUN_ID
      ? `No run artifact was found for run id “${RUN_ID}”. Check the library for a valid id.`
      : "The sample run artifact was not found on this server.";
  }
  return `Artifact request failed (${response.status})`;
}

fetch(ARTIFACT_URL)
  .then(async (response) => {
    if (!response.ok) throw new Error(artifactFetchError(response));
    try {
      return await response.json();
    } catch (_error) {
      throw new Error("Artifact response was not valid JSON (corrupt or truncated payload).");
    }
  })
  .then(render)
  .catch((error) => {
    const hint = RUN_ID
      ? `<p>Open the <a href="./library.html">run library</a> or the <a href="./index.html${RUN_QUERY}">run report</a>.</p>`
      : `<p>If your browser blocks local <code>file:</code> fetches, serve this directory with an offline local static server and open <code>settings.html</code> there.</p>`;
    $("#settings").innerHTML = `<div class="fatal"><div class="eyebrow">Settings unavailable</div><h1>Could not read the artifact header</h1><p>${esc(error.message)}</p>${hint}</div>`;
  });
