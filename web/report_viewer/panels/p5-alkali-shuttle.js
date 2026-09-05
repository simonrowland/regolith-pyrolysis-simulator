(function registerAlkaliShuttlePanel(root) {
  "use strict";

  const { prettySpecies, speciesColor, exactValue, esc } = root.ReportLabels;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

  const CREDIT_MAPS = Object.freeze([
    ["c3_alkali_credit_dose_kg_by_species", "Credited dose · kg"],
    ["c3_alkali_credit_drawn_kg_by_species", "Drawn · kg"],
    ["c3_alkali_credit_outstanding_kg_by_species", "Outstanding · net makeup · kg"]
  ]);
  const SPECIES_ORDER = Object.freeze(["Na", "K"]);
  const HOLD_FIELDS = Object.freeze([
    ["status", "Status"],
    ["reason", "Reason"],
    ["authority", "Authority"],
    ["authoritative", "Authoritative"],
    ["diagnostic_only", "Diagnostic only"],
    ["extrapolation", "Extrapolation"],
    ["high_uncertainty", "High uncertainty"],
    ["source", "Source"],
    ["reference", "Reference"],
    ["skip_reason", "Skip reason"]
  ]);
  const SHUTTLE_FIELDS = Object.freeze([
    ["shuttle_phase", "Phase", ""],
    ["shuttle_cycle", "Cycle", ""],
    ["shuttle_injected_kg_hr", "Injected · kg/h", "kg/h"],
    ["shuttle_reduced_kg_hr", "Reduced · kg/h", "kg/h"],
    ["shuttle_metal_produced_kg_hr", "Metal produced · kg/h", "kg/h"],
    ["shuttle_Na_inventory_kg", "Na inventory · kg", "kg"],
    ["shuttle_K_inventory_kg", "K inventory · kg", "kg"]
  ]);
  const HOLD_UNITS = Object.freeze({
    configured_temperature_C: "°C",
    selected_temperature_C: "°C",
    crossover_temperature_C: "°C",
    boundary_tolerance_C: "°C",
    configured_margin_kJ_per_mol_O2: "kJ/mol-O2",
    selected_Fe_produced_kg: "kg"
  });

  function pendingBlock(title, message) {
    return `<div class="pending sec-p5-pending"><strong>${esc(title)}</strong><p>${esc(message)}</p></div>`;
  }

  function notEmitted() {
    return `<span class="sec-p5-not-emitted">not emitted</span>`;
  }

  function malformed(kind) {
    return `<span class="sec-p5-not-emitted">malformed${kind ? ` (${kind})` : ""}</span>`;
  }

  function speciesBadge(species) {
    const color = esc(speciesColor(species));
    return `<span class="sec-p5-species" style="--species-color:${color}"><i aria-hidden="true"></i>${esc(prettySpecies(species))}</span>`;
  }

  function numberCell(value, unit) {
    return hasNumber(value) ? exactValue(value, unit) : malformed("non-numeric");
  }

  function headerRecord(artifact) {
    return artifact && isRecord(artifact.header) ? artifact.header : null;
  }

  function metadataRecord(artifact) {
    const terminal = artifact && isRecord(artifact.terminal) ? artifact.terminal : null;
    return terminal && isRecord(terminal.run_metadata) ? terminal.run_metadata : null;
  }

  function commandedKey(species) {
    return `${species}_kg`;
  }

  function collectSpecies(header, metadata) {
    const names = [];
    const push = (name) => {
      if (!names.includes(name)) names.push(name);
    };
    if (header && isRecord(header.c3_dose)) {
      Object.keys(header.c3_dose).forEach((key) => {
        if (key.endsWith("_kg")) push(key.slice(0, -3));
      });
    }
    if (isRecord(metadata)) {
      CREDIT_MAPS.forEach(([field]) => {
        if (own(metadata, field) && isRecord(metadata[field])) {
          Object.keys(metadata[field]).forEach(push);
        }
      });
    }
    return names.sort((left, right) => {
      const leftRank = SPECIES_ORDER.indexOf(left);
      const rightRank = SPECIES_ORDER.indexOf(right);
      if (leftRank !== -1 && rightRank !== -1) return leftRank - rightRank;
      if (leftRank !== -1) return -1;
      if (rightRank !== -1) return 1;
      return String(left).localeCompare(String(right));
    });
  }

  function creditEngaged(header, metadata) {
    if (header && isRecord(header.c3_dose)
      && Object.keys(header.c3_dose).some((key) => key.endsWith("_kg"))) {
      return true;
    }
    return isRecord(metadata) && CREDIT_MAPS.some(([field]) => own(metadata, field));
  }

  function commandedValue(header, species) {
    if (!header || !own(header, "c3_dose")) return notEmitted();
    if (!isRecord(header.c3_dose)) return malformed("object");
    const key = commandedKey(species);
    if (!own(header.c3_dose, key)) return notEmitted();
    return numberCell(header.c3_dose[key], "kg");
  }

  function creditValue(metadata, field, species) {
    if (!isRecord(metadata) || !own(metadata, field)) return notEmitted();
    const map = metadata[field];
    if (!isRecord(map)) return malformed("object");
    if (!own(map, species)) return notEmitted();
    return numberCell(map[species], "kg");
  }

  function renderSpeciesCard(header, metadata, species) {
    const rows = [
      `<div class="sec-p5-kv"><span>Commanded dose · kg</span><b>${commandedValue(header, species)}</b></div>`,
      ...CREDIT_MAPS.map(([field, label]) =>
        `<div class="sec-p5-kv"><span>${esc(label)}</span><b>${creditValue(metadata, field, species)}</b></div>`
      )
    ].join("");
    return `<article class="sec-p5-species-card">${speciesBadge(species)}${rows}</article>`;
  }

  function renderCreditLine(header, metadata) {
    if (!creditEngaged(header, metadata)) {
      return pendingBlock(
        "C3 credit line not engaged / not emitted",
        "header.c3_dose and terminal.run_metadata C3 credit maps are absent. C3 was not engaged on this run; no credit totals are invented."
      );
    }
    const species = collectSpecies(header, metadata);
    const cards = species.length
      ? `<div class="sec-p5-species-grid">${species.map((name) => renderSpeciesCard(header, metadata, name)).join("")}</div>`
      : pendingBlock(
        "C3 credit maps empty",
        "Credit keys are present but contain no species rows. No Na or K totals are invented."
      );
    return `<div class="sec-p5-block"><div class="sec-p5-block-head"><div><h3>Terminal credit line</h3>` +
      `<p>Commanded header dose is distinct from credited dose. Drawn is gross replenishment. Outstanding is net makeup / reagent-reservoir deficit, not leftover dose. Values are emitted kg; the viewer does not subtract dose minus drawn.</p></div></div>${cards}</div>`;
  }

  function holdValue(key, value) {
    if (hasNumber(value)) {
      const unit = HOLD_UNITS[key]
        || (/_kg$/.test(key) ? "kg" : /_C$/.test(key) ? "°C" : "");
      return exactValue(value, unit);
    }
    if (value === null || value === undefined) return notEmitted();
    if (typeof value === "object") return esc(JSON.stringify(value));
    return esc(String(value));
  }

  function renderNaHold(metadata) {
    if (!isRecord(metadata) || !own(metadata, "c3_na_hold_adjustment")) {
      return pendingBlock(
        "Na-hold adjustment not emitted",
        "terminal.run_metadata.c3_na_hold_adjustment is absent. No hold temperature or zero is invented."
      );
    }
    const adjustment = metadata.c3_na_hold_adjustment;
    if (!isRecord(adjustment)) {
      return pendingBlock(
        "Na-hold adjustment malformed",
        "c3_na_hold_adjustment is present but is not an object."
      );
    }
    const known = HOLD_FIELDS.map(([field]) => field);
    const preferred = HOLD_FIELDS.filter(([field]) => own(adjustment, field));
    const extra = Object.keys(adjustment)
      .filter((key) => !known.includes(key))
      .sort()
      .map((key) => [key, key.replace(/[_-]+/g, " ")]);
    const keys = preferred.concat(extra);
    if (!keys.length) {
      return pendingBlock(
        "Na-hold adjustment empty",
        "c3_na_hold_adjustment is present as an empty object. No hold values are invented."
      );
    }
    const rows = keys.map(([field, label]) =>
      `<div class="sec-p5-kv"><span>${esc(label)}</span><b>${holdValue(field, adjustment[field])}</b></div>`
    ).join("");
    return `<details class="sec-p5-disclosure"><summary>Na-hold adjustment</summary>` +
      `<p class="sec-p5-note">Flagged hold quantities remain quantities. Status, reason, and authority stay visible with the numbers.</p>${rows}</details>`;
  }

  function hourEntries(artifact, rows) {
    if (artifact && Array.isArray(artifact.timesteps) && artifact.timesteps.length) {
      return artifact.timesteps.map((step) => {
        const summary = isRecord(step) && isRecord(step.summary) ? step.summary : null;
        const hour = isRecord(step) && own(step, "hour")
          ? step.hour
          : (summary && own(summary, "hour") ? summary.hour : undefined);
        return { hour, summary };
      });
    }
    const list = Array.isArray(rows) ? rows : [];
    return list.map((summary) => ({
      hour: isRecord(summary) && own(summary, "hour") ? summary.hour : undefined,
      summary: isRecord(summary) ? summary : null
    }));
  }

  function hasShuttleKeys(summary) {
    return isRecord(summary) && SHUTTLE_FIELDS.some(([key]) => own(summary, key));
  }

  function shuttleCell(summary, key, unit) {
    if (!isRecord(summary) || !own(summary, key)) return notEmitted();
    const value = summary[key];
    if (unit) return numberCell(value, unit);
    if (hasNumber(value)) return exactValue(value);
    if (value === null || value === undefined || typeof value === "object") {
      return malformed(Array.isArray(value) ? "array" : "object");
    }
    return esc(String(value));
  }

  function renderHourly(artifact, rows) {
    const entries = hourEntries(artifact, rows).filter((entry) => hasShuttleKeys(entry.summary));
    if (!entries.length) {
      return pendingBlock(
        "Hourly shuttle readout not emitted",
        "timesteps[].summary.shuttle_* keys are not present on this artifact. No hourly inject, reduce, metal, or inventory totals are invented."
      );
    }
    const head = `<tr><th>Hour</th>${SHUTTLE_FIELDS.map(([, label]) => `<th>${esc(label)}</th>`).join("")}</tr>`;
    const body = entries.map((entry) => {
      const hourCell = own(entry, "hour") && entry.hour !== undefined
        ? (hasNumber(entry.hour) ? exactValue(entry.hour) : esc(String(entry.hour)))
        : notEmitted();
      const cells = SHUTTLE_FIELDS.map(([key, , unit]) =>
        `<td>${shuttleCell(entry.summary, key, unit)}</td>`
      ).join("");
      return `<tr><td>${hourCell}</td>${cells}</tr>`;
    }).join("");
    return `<div class="sec-p5-block sec-p5-hourly"><div class="sec-p5-block-head"><div><h3>Hourly shuttle readout</h3>` +
      `<p>Per-hour emitted shuttle fields only. Injected kg/h is not summed across hours.</p></div></div>` +
      `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div></div>`;
  }

  function refusalSource(artifact, rows) {
    const terminal = artifact && isRecord(artifact.terminal) ? artifact.terminal : null;
    if (terminal && own(terminal, "shuttle_refusal_history")) return terminal.shuttle_refusal_history;
    const metadata = metadataRecord(artifact);
    if (metadata && own(metadata, "shuttle_refusal_history")) return metadata.shuttle_refusal_history;
    const entries = hourEntries(artifact, rows);
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const summary = entries[index].summary;
      if (isRecord(summary) && own(summary, "shuttle_refusal_history")) {
        return summary.shuttle_refusal_history;
      }
    }
    return undefined;
  }

  function renderRefusalHistory(artifact, rows) {
    const terminal = artifact && isRecord(artifact.terminal) ? artifact.terminal : null;
    const metadata = metadataRecord(artifact);
    const owned = (terminal && own(terminal, "shuttle_refusal_history"))
      || (metadata && own(metadata, "shuttle_refusal_history"))
      || hourEntries(artifact, rows).some((entry) => isRecord(entry.summary) && own(entry.summary, "shuttle_refusal_history"));
    if (!owned) return "";
    const history = refusalSource(artifact, rows);
    if (!Array.isArray(history)) {
      return pendingBlock(
        "Shuttle refusal history malformed",
        "shuttle_refusal_history is present but is not a list."
      );
    }
    if (!history.length) {
      return `<div class="sec-p5-block"><h3>Shuttle refusal history</h3><p class="sec-p5-note">Emitted list is empty.</p></div>`;
    }
    const items = history.map((entry) => {
      if (!isRecord(entry)) return `<li>${esc(entry)}</li>`;
      const parts = Object.keys(entry).sort().map((key) =>
        `<div class="sec-p5-kv"><span>${esc(key)}</span><b>${holdValue(key, entry[key])}</b></div>`
      ).join("");
      return `<li>${parts}</li>`;
    }).join("");
    return `<details class="sec-p5-disclosure"><summary>Shuttle refusal history</summary><ul class="sec-p5-refusal">${items}</ul></details>`;
  }

  function render(artifact, rows) {
    const header = headerRecord(artifact);
    const metadata = metadataRecord(artifact);
    return `<section class="sec-p5-alkali-shuttle" id="sec-p5-alkali-shuttle" aria-labelledby="sec-p5-alkali-shuttle-title">` +
      `<h2 id="sec-p5-alkali-shuttle-title"><span class="sect">P5</span>Alkali shuttle (C3)</h2>` +
      `<p class="sub">Terminal C3 credit line for the alkali shuttle as reductant and selectivity tool. Hourly shuttle fields render only when emitted.</p>` +
      `${renderCreditLine(header, metadata)}${renderNaHold(metadata)}${renderHourly(artifact, rows)}${renderRefusalHistory(artifact, rows)}</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p5-alkali-shuttle", render });
}(globalThis));
