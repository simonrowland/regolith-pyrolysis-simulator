"use strict";

const ARTIFACT_URL = "./sample-run-artifact.json";
const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value ?? "—").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));
const hasNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value));
const displayNumber = (value, unit = "") => hasNumber(value)
  ? `<span title="${esc(`${String(value)}${unit ? ` ${unit}` : ""}`)}">${esc(Number(value).toLocaleString(undefined, { maximumSignificantDigits: 4 }))}${unit ? ` ${esc(unit)}` : ""}</span>`
  : "not emitted";

function dataBlock(value) {
  if (value === undefined || value === null) return `<div class="pending"><strong>Not captured</strong><p>This field is absent from the sample header.</p></div>`;
  return `<pre class="data-block">${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function settingsField(number, title, subtitle, content) {
  return `<section><h2><span class="sect">${esc(String(number).padStart(2, "0"))}</span>${esc(title)}</h2><p class="sub">${esc(subtitle)}</p>${content}</section>`;
}

function costBlock(cost) {
  if (!cost || typeof cost !== "object") return `<div class="pending"><strong>Pending W-A5a</strong><p>header.cost_block is absent.</p></div>`;
  return `<div class="cards">
    <div class="card"><div class="ct">Owner energy price · electrical</div><div class="cbig">${displayNumber(cost.electrical_cost_per_kWh, "USD/kWh")}</div></div>
    <div class="card"><div class="ct">Owner energy price · solar heat</div><div class="cbig">${displayNumber(cost.solar_heat_cost_per_kWh, "USD/kWh")}</div></div>
  </div>${cost._provenance == null ? "" : `<div class="note">Price provenance: ${esc(cost._provenance)}</div>`}`;
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
    return `<div class="pending"><strong>Effective config not captured</strong><p>captured at run merge points (W-A5) — not in this sample</p></div>`;
  }
  const entries = configEntries(config);
  if (!entries.length) return `<div class="pending"><strong>Effective config empty</strong><p>No per-key values were captured.</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Key</th><th>Value</th><th>Source</th></tr></thead><tbody>${entries.map((entry) => {
    const source = String(entry.source ?? "unknown");
    const nonDefault = source !== "default";
    return `<tr class="${nonDefault ? "config-override" : ""}"><td class="mono">${esc(entry.key)}</td><td class="mono">${esc(JSON.stringify(entry.value))}</td><td><span class="chip${nonDefault ? " accent" : ""}">${esc(source)}</span></td></tr>`;
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
  link.download = "run.yaml";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function render(artifact) {
  if (!artifact || !artifact.header || typeof artifact.header !== "object") throw new Error("Artifact header is absent or malformed.");
  const header = artifact.header;
  $("#settings").innerHTML = `<header>
    <div class="masthead"><div class="brand"><strong>DIRECT LEAP</strong> TECHNOLOGIES</div><div class="doc-label">Read-only<br>owner T-8</div></div>
    <div class="eyebrow">PHASE 2 · SETTINGS INSPECTOR</div><h1>Captured run settings</h1>
    <p class="lede"><span class="mono">${esc(header.run_id)}</span> · settings copied from the frozen artifact header.</p>
    <div class="settings-actions"><a href="./index.html">← Back to report</a><button id="download-run" type="button">Download run.yaml</button></div>
    <div class="note"><b>Read-only.</b> Config editing remains owner T-8. The download contains the captured header only.</div>
  </header>
  ${settingsField(1, "Recipe snapshot", "Captured recipe material only; absent values are not reconstructed.", dataBlock(header.recipe_snapshot))}
  ${settingsField(2, "Engine identity", "Backend identity recorded by the run header.", dataBlock(header.engine_identity))}
  ${settingsField(3, "C3 dose · kg by species", "Captured alkali-shuttle dose in kg (not mol); no recipe inference.", dataBlock(header.c3_dose))}
  ${settingsField(4, "Owner's two energy prices", "Electrical and solar-heat prices bind directly to header.cost_block.", costBlock(header.cost_block))}
  ${settingsField(5, "Effective config", "Per-key merged value and source; every non-default source is highlighted.", effectiveConfig(header.effective_config))}
  <footer class="footer"><span>Frozen header inspection · engine-free · no edit controls</span><a href="./library.html">Run library</a></footer>`;
  $("#download-run").addEventListener("click", () => downloadHeader(header));
}

fetch(ARTIFACT_URL)
  .then((response) => {
    if (!response.ok) throw new Error(`Artifact request failed (${response.status})`);
    return response.json();
  })
  .then(render)
  .catch((error) => {
    $("#settings").innerHTML = `<div class="fatal"><div class="eyebrow">Settings unavailable</div><h1>Could not read the artifact header</h1><p>${esc(error.message)}</p><p>If your browser blocks local <code>file:</code> fetches, serve this directory with an offline local static server and open <code>settings.html</code> there.</p></div>`;
  });
