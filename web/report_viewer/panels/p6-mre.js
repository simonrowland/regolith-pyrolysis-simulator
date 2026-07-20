(function registerMrePanel(root) {
  "use strict";

  const { fmtNum, prettySpecies, speciesColor, accountLabel, esc } = root.ReportLabels;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

  const LABELS = Object.freeze({
    source_species: "Source species",
    produced_species: "Produced species",
    produced_kg: "Produced amount · kg",
    produced_mol: "Produced amount · mol",
    certification: "Certification",
    reference_V: "Reference · V",
    reference_status: "Reference status",
    reason: "Reason",
    status: "Status",
    basis: "Basis",
    units: "Units",
    authority: "Authority",
    authoritative: "Authoritative",
    diagnostic_only: "Diagnostic only",
    extrapolation: "Extrapolation",
    high_uncertainty: "High uncertainty",
    source: "Source",
    reference: "Reference",
    skip_reason: "Skip reason",
    oxide_activity_model: "Activity model"
  });
  const UNITS = Object.freeze({
    produced_kg: "kg",
    produced_mol: "mol",
    reference_V: "V",
    static_declared_V: "V",
    derived_Ed_V: "V",
    delta_vs_declared_rung_V: "V",
    delta_vs_static_declared_V: "V"
  });

  function pending(title, message) {
    return `<div class="pending sec-p6-pending"><strong>${esc(title)}</strong><p>${esc(message)}</p></div>`;
  }

  function latestOwned(rows, key) {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      if (isRecord(rows[index]) && own(rows[index], key)) {
        return { found: true, value: rows[index][key] };
      }
    }
    return { found: false, value: undefined };
  }

  function readableKey(key) {
    if (LABELS[key]) return LABELS[key];
    return String(key).replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function numberValue(value, unit) {
    return hasNumber(value)
      ? esc(fmtNum(value, unit))
      : `<span class="sec-p6-not-emitted">not emitted</span>`;
  }

  function scalarValue(value) {
    if (value === null || value === undefined || typeof value === "object") {
      return `<span class="sec-p6-not-emitted">${esc(value === null || value === undefined ? "not emitted" : value)}</span>`;
    }
    return typeof value === "number" ? esc(fmtNum(value)) : esc(value);
  }

  function speciesBadge(species) {
    if (species === null || species === undefined || typeof species === "object") {
      return `<span class="sec-p6-not-emitted">${esc(species)}</span>`;
    }
    const color = esc(speciesColor(species));
    return `<span class="sec-p6-species" style="--species-color:${color}"><i aria-hidden="true"></i>${esc(prettySpecies(species))}</span>`;
  }

  function fieldValue(key, value) {
    if (UNITS[key]) return numberValue(value, UNITS[key]);
    if (key === "source_species" || key === "produced_species") return speciesBadge(value);
    return scalarValue(value);
  }

  function chip(label, value, tone = "") {
    return `<span class="sec-p6-chip${tone ? ` ${tone}` : ""}"><b>${esc(label)}</b>${scalarValue(value)}</span>`;
  }

  function emittedNumber(record, key, unit) {
    return own(record, key) ? numberValue(record[key], unit) : numberValue(undefined, unit);
  }

  function emittedArray(record, key) {
    if (!own(record, key)) return `<span class="sec-p6-not-emitted">not emitted</span>`;
    const value = record[key];
    if (!Array.isArray(value)) return `<span class="sec-p6-not-emitted">${esc(value)}</span>`;
    if (!value.length) return `<span class="sec-p6-empty">empty emitted list</span>`;
    return `<span class="sec-p6-species-list">${value.map(speciesBadge).join("")}</span>`;
  }

  function renderVoltageMaps(ladder) {
    const derived = isRecord(ladder.derived_Ed_V) ? ladder.derived_Ed_V : null;
    const delta = isRecord(ladder.delta_vs_declared_rung_V)
      ? ladder.delta_vs_declared_rung_V
      : null;
    const species = [];
    if (derived) species.push(...Object.keys(derived));
    if (delta) {
      for (const oxide of Object.keys(delta)) if (!species.includes(oxide)) species.push(oxide);
    }
    if (!species.length) {
      return pending("Pending derived-versus-declared detail", "The emitted ladder has no derived_Ed_V or delta_vs_declared_rung_V entries. The viewer does not reconstruct them from other fields.");
    }
    const rows = species.map((oxide) => `<tr><td>${speciesBadge(oxide)}</td>` +
      `<td class="num">${derived && own(derived, oxide) ? numberValue(derived[oxide], "V") : numberValue(undefined, "V")}</td>` +
      `<td class="num">${delta && own(delta, oxide) ? numberValue(delta[oxide], "V") : numberValue(undefined, "V")}</td></tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr><th>Oxide</th><th class="num">Derived E<sub>d</sub> · V</th><th class="num">Delta vs declared · V</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderReordering(ladder) {
    if (!own(ladder, "reordering")) {
      return pending("Pending reordering evidence", "reordering is not emitted. No ordering verdict is inferred from the species maps.");
    }
    const value = ladder.reordering;
    if (!isRecord(value)) return pending("Malformed reordering evidence", "reordering is present but is not an object.");
    return `<div class="sec-p6-kv"><span>Ordering divergence detected</span><b>${own(value, "ordering_divergence_detected") ? scalarValue(value.ordering_divergence_detected) : scalarValue(undefined)}</b></div>` +
      `<div class="sec-p6-kv"><span>Other species below declared rung</span><b>${emittedArray(value, "other_species_below_declared_rung")}</b></div>` +
      `<div class="sec-p6-kv"><span>Derived order by E<sub>d</sub></span><b>${emittedArray(value, "derived_order_by_Ed")}</b></div>` +
      `<div class="sec-p6-kv"><span>Declared static-voltage order</span><b>${emittedArray(value, "declared_order_by_static_voltage")}</b></div>`;
  }

  function renderNonAuthoritative(ladder) {
    if (!own(ladder, "non_authoritative_voltage_by_oxide")) {
      return pending("Pending non-authoritative voltage entries", "non_authoritative_voltage_by_oxide is not emitted. No panel-wide authority verdict is inferred.");
    }
    const entries = ladder.non_authoritative_voltage_by_oxide;
    if (!isRecord(entries)) return pending("Malformed non-authoritative voltage entries", "The emitted value is not an object.");
    const rows = Object.entries(entries);
    if (!rows.length) return `<div class="note sec-p6-note">The emitted partial map contains no entries. No panel-wide authority verdict is inferred.</div>`;
    return `<div class="sec-p6-authority-list">${rows.map(([oxide, detail]) => {
      if (!isRecord(detail)) return `<div class="sec-p6-authority-row">${speciesBadge(oxide)}${pending("Malformed authority entry", "The per-oxide entry is not an object.")}</div>`;
      return `<div class="sec-p6-authority-row"><div>${speciesBadge(oxide)}</div>` +
        `<div class="sec-p6-chip-row">${chip("Authority", own(detail, "authority") ? detail.authority : undefined)}` +
        `${chip("Authoritative", own(detail, "authoritative") ? detail.authoritative : undefined, detail.authoritative === false ? "sec-p6-warn" : "")}` +
        `${chip("Status", own(detail, "status") ? detail.status : undefined, "sec-p6-warn")}</div>` +
        `<div class="sec-p6-kv"><span>Static declared voltage</span><b>${own(detail, "static_declared_V") ? numberValue(detail.static_declared_V, "V") : numberValue(undefined, "V")}</b></div></div>`;
    }).join("")}</div>`;
  }

  function renderSpeciesDetail(ladder) {
    if (!own(ladder, "species")) return pending("Pending species detail", "species is not emitted for this ladder diagnostic.");
    const entries = ladder.species;
    if (!isRecord(entries)) return pending("Malformed species detail", "species is present but is not an object.");
    if (!Object.keys(entries).length) return pending("Empty species detail", "species was emitted with no entries.");
    const known = [
      "ellingham_species", "static_declared_V", "oxide_activity", "oxide_activity_model",
      "inventory_present", "derived_Ed_V", "delta_vs_declared_rung_V",
      "delta_vs_static_declared_V", "declared_after_held_rung", "voltage_authority",
      "voltage_authoritative", "status"
    ];
    return `<div class="sec-p6-species-cards">${Object.entries(entries).map(([oxide, detail]) => {
      if (!isRecord(detail)) return `<article class="sec-p6-species-card"><h4>${speciesBadge(oxide)}</h4>${pending("Malformed species row", "The emitted species row is not an object.")}</article>`;
      const extras = Object.keys(detail).filter((key) => !known.includes(key)).sort();
      const rows = known.concat(extras).map((key) => `<div class="sec-p6-kv"><span>${esc(readableKey(key))}</span><b>${own(detail, key) ? fieldValue(key, detail[key]) : scalarValue(undefined)}</b></div>`).join("");
      return `<article class="sec-p6-species-card"><h4>${speciesBadge(oxide)}</h4>${rows}</article>`;
    }).join("")}</div>`;
  }

  function renderLadder(result) {
    if (!result.found) {
      return pending("Pending ladder evidence", "summary.mre_ellingham_ladder_diagnostic is not emitted for any timestep.");
    }
    const ladder = result.value;
    if (!isRecord(ladder)) return pending("Malformed ladder evidence", "The latest emitted ladder value is not an object.");
    if (!Object.keys(ladder).length) return pending("Empty ladder evidence", "The latest emitted ladder value is an empty object.");
    const statusChips = `<div class="sec-p6-chip-row">` +
      `${chip("Certification", own(ladder, "certification") ? ladder.certification : undefined, "sec-p6-warn")}` +
      `${chip("Authority", own(ladder, "authority") ? ladder.authority : undefined)}` +
      `${own(ladder, "status") ? chip("Status", ladder.status, "sec-p6-warn") : ""}</div>`;
    return `<div class="sec-p6-block"><div class="sec-p6-block-head"><div><h3>Latest emitted ladder evidence</h3><p>Read-only Ellingham/Nernst diagnostic; not an applied-cell-voltage or MRE-selection verdict.</p></div>${statusChips}</div>` +
      `<div class="sec-p6-headline"><div><span>Declared accounting rung</span><b>${emittedNumber(ladder, "declared_rung_V", "V")}</b></div>` +
      `<div><span>Temperature</span><b>${emittedNumber(ladder, "temperature_C", "°C")} · ${emittedNumber(ladder, "temperature_K", "K")}</b></div>` +
      `<div><span>Ladder diagnostic pO₂</span><b>${emittedNumber(ladder, "pO2_bar", "bar")}</b></div>` +
      `<div><span>Held-rung species</span><b>${emittedArray(ladder, "rung_species")}</b></div></div>` +
      `<details class="sec-p6-disclosure"><summary>Ladder detail and per-species authority</summary>` +
      `<div class="sec-p6-meta"><div class="sec-p6-kv"><span>Schema</span><b>${own(ladder, "schema") ? scalarValue(ladder.schema) : scalarValue(undefined)}</b></div>` +
      `<div class="sec-p6-kv"><span>Activity basis</span><b>${own(ladder, "activity_basis") ? scalarValue(ladder.activity_basis) : scalarValue(undefined)}</b></div></div>` +
      `<h4>Derived versus declared evidence</h4>${renderVoltageMaps(ladder)}` +
      `<h4>Emitted reordering object</h4>${renderReordering(ladder)}` +
      `<h4>Emitted non-authoritative voltage entries</h4>${renderNonAuthoritative(ladder)}` +
      `<h4>Per-species diagnostic rows</h4>${renderSpeciesDetail(ladder)}</details></div>`;
  }

  function renderYield(result) {
    if (!result.found) {
      return pending("Pending uncertified yield", "summary.mre_uncertified_yield is not emitted for any timestep. No yield is inferred from ladder or oxygen data.");
    }
    const entries = result.value;
    if (!isRecord(entries)) return pending("Malformed uncertified yield", "The latest emitted yield value is not a per-species object.");
    if (!Object.keys(entries).length) return pending("Empty uncertified yield", "The latest emitted yield value is an empty per-species object.");
    const required = [
      "source_species", "produced_species", "produced_kg", "produced_mol",
      "certification", "reference_V", "reference_status", "reason"
    ];
    return `<div class="sec-p6-block"><div class="sec-p6-block-head"><div><h3>Latest emitted uncertified yield evidence</h3><p>Per-hour bookkeeping only; emitted kg and mol stay separate and no cross-hour total is derived.</p></div></div>` +
      `<details class="sec-p6-disclosure"><summary>Yield evidence and uncertainty detail</summary><div class="sec-p6-yield-list">` +
      `${Object.entries(entries).map(([species, detail]) => {
        if (!isRecord(detail)) return `<article class="sec-p6-yield-card"><h4>${speciesBadge(species)}</h4>${pending("Malformed yield entry", "The per-species entry is not an object; no unit is assumed.")}</article>`;
        const extra = Object.keys(detail).filter((key) => !required.includes(key)).sort();
        const rows = required.concat(extra).map((key) => `<div class="sec-p6-kv"><span>${esc(readableKey(key))}</span><b>${own(detail, key) ? fieldValue(key, detail[key]) : scalarValue(undefined)}</b></div>`).join("");
        return `<article class="sec-p6-yield-card"><h4>${speciesBadge(species)}</h4>${rows}</article>`;
      }).join("")}</div></details></div>`;
  }

  function renderAnodeO2(artifact) {
    const finalState = artifact && artifact.terminal && artifact.terminal.final_state;
    const accountKey = "terminal.oxygen_mre_anode_stored";
    if (!isRecord(finalState) || !own(finalState, accountKey)) {
      return pending("Pending MRE-anode O₂", "The MRE-anode stored account is not emitted in terminal.final_state. No oxygen value is inferred from other accounts.");
    }
    const account = finalState[accountKey];
    if (!isRecord(account) || !own(account, "O2") || !hasNumber(account.O2)) {
      return pending("Malformed MRE-anode O₂", "The stored account is present but its O₂ mol inventory is not a finite emitted number.");
    }
    return `<div class="sec-p6-block sec-p6-o2"><div><span>${esc(accountLabel(accountKey))}</span><b>${esc(fmtNum(account.O2, "mol"))}</b></div><p>Terminal MRE-anode stored inventory in the mol-native ledger basis.</p></div>`;
  }

  function renderDirectReadouts() {
    return `<div class="sec-p6-direct" aria-label="Direct MRE readouts pending producer emission">` +
      `${["Applied cell voltage · V", "Cell current · A", "Metals production rate · kg/h"].map((label) => `<div><span>${esc(label)}</span><b>Pending producer</b><small>Not emitted in the report artifact</small></div>`).join("")}</div>`;
  }

  function render(artifact, rows) {
    const summaries = Array.isArray(rows) ? rows : [];
    const ladder = latestOwned(summaries, "mre_ellingham_ladder_diagnostic");
    const yieldEvidence = latestOwned(summaries, "mre_uncertified_yield");
    return `<section class="sec-p6-mre" id="sec-p6-mre" aria-labelledby="sec-p6-mre-title">` +
      `<h2 id="sec-p6-mre-title"><span class="sect">P6</span>MRE electrolysis evidence</h2>` +
      `<p class="sub">Diagnostic ladder, uncertified per-hour yield evidence, and terminal MRE-anode stored O₂ inventory.</p>` +
      `${renderLadder(ladder)}${renderYield(yieldEvidence)}${renderAnodeO2(artifact)}${renderDirectReadouts()}</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p6-mre", render });
}(globalThis));
