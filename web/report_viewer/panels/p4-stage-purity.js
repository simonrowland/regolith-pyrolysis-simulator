(function registerStagePurityPanel(root) {
  "use strict";

  const { fmtNum, prettySpecies, speciesColor, esc } = root.ReportLabels;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const authorityFields = Object.freeze([
    ["authoritative", "Authoritative"],
    ["diagnostic_only", "Diagnostic only"],
    ["extrapolation", "Extrapolation"],
    ["high_uncertainty", "High uncertainty"],
    ["status", "Status"],
    ["source", "Source"],
    ["reference", "Reference"],
    ["refusal_context", "Refusal context"],
    ["skip_reason", "Skip reason"]
  ]);

  function pending(message) {
    return `<span class="sec-p4-pending-value">Pending · ${esc(message)}</span>`;
  }

  function numberValue(value, unit, missingMessage) {
    return hasNumber(value) ? esc(fmtNum(value, unit)) : pending(missingMessage);
  }

  function stageNumberValue(stage, field, unit) {
    if (!own(stage, field)) return pending(`${field} not emitted`);
    return hasNumber(stage[field])
      ? esc(fmtNum(stage[field], unit))
      : pending(`${field} is malformed`);
  }

  function speciesName(species) {
    return `<span class="sec-p4-species" style="--species-color:${esc(speciesColor(species))}">` +
      `<span class="sec-p4-species-dot" aria-hidden="true"></span>${esc(prettySpecies(species))}</span>`;
  }

  function speciesMap(stage, field, title) {
    if (!own(stage, field)) {
      return `<div class="sec-p4-breakdown"><h4>${esc(title)} <small>kg basis</small></h4>` +
        pending(`${title.toLowerCase()} map not emitted`) + `</div>`;
    }
    if (!isRecord(stage[field])) {
      return `<div class="sec-p4-breakdown"><h4>${esc(title)} <small>kg basis</small></h4>` +
        pending(`${title.toLowerCase()} map is malformed`) + `</div>`;
    }
    const entries = Object.entries(stage[field]);
    if (!entries.length) {
      return `<div class="sec-p4-breakdown"><h4>${esc(title)} <small>kg basis</small></h4>` +
        pending("emitted map contains no measured species values") + `</div>`;
    }
    const items = entries.map(([species, mass]) =>
      `<li>${speciesName(species)}<b>${numberValue(mass, "kg", "species mass is malformed")}</b></li>`
    ).join("");
    return `<div class="sec-p4-breakdown"><h4>${esc(title)} <small>kg basis</small></h4><ul>${items}</ul></div>`;
  }

  function acceptedSpecies(stage) {
    if (!own(stage, "accepted_species")) return pending("accepted species not emitted");
    if (!Array.isArray(stage.accepted_species)) return pending("accepted species contract is malformed");
    if (!stage.accepted_species.length) return `<span class="sec-p4-empty-value">No accepted species in emitted contract</span>`;
    return `<div class="sec-p4-species-row">${stage.accepted_species.map(speciesName).join("")}</div>`;
  }

  function activityDetail(stage) {
    if (!own(stage, "activity")) return pending("per-species activity not emitted");
    if (!isRecord(stage.activity)) return pending("per-species activity map is malformed");
    const speciesList = [...new Set([
      ...(Array.isArray(stage.accepted_species) ? stage.accepted_species : []),
      ...Object.keys(stage.activity)
    ])];
    if (!speciesList.length) return pending("emitted activity map contains no species states");
    return `<ul class="sec-p4-activity-list">${speciesList.map((species) => {
      const active = stage.activity[species];
      const state = !own(stage.activity, species)
        ? "PENDING"
        : typeof active === "boolean" ? (active ? "ACTIVE" : "IDLE") : "PENDING";
      return `<li>${speciesName(species)}<b>${state}</b></li>`;
    }).join("")}</ul>`;
  }

  function authorityValue(value) {
    return esc(isRecord(value) || Array.isArray(value) ? JSON.stringify(value) : value);
  }

  function authorityDetail(stage) {
    const entries = authorityFields.filter(([field]) => own(stage, field));
    if (!entries.length) return "";
    return `<div class="sec-p4-authority"><h4>Authority &amp; context</h4>${entries.map(([field, label]) =>
      `<span><b>${esc(label)}</b> ${authorityValue(stage[field])}</span>`
    ).join("")}</div>`;
  }

  function warningDetail(stage) {
    if (!own(stage, "warning")) return `<p class="sec-p4-warning">${pending("warning not emitted")}</p>`;
    if (typeof stage.warning !== "string") return `<p class="sec-p4-warning">${pending("warning is malformed")}</p>`;
    return stage.warning.trim()
      ? `<p class="sec-p4-warning">${esc(stage.warning)}</p>`
      : `<p class="sec-p4-warning"><span class="sec-p4-empty-value">No warning text in emitted field</span></p>`;
  }

  function verdict(stage) {
    if (!own(stage, "verdict")) return pending("backend verdict not emitted");
    if (typeof stage.verdict !== "string" || !stage.verdict.trim()) return pending("backend verdict is malformed");
    const verdictClass = ["PURE", "MIXED", "CONTAMINATED"].includes(stage.verdict)
      ? stage.verdict.toLowerCase()
      : "unknown";
    return `<span class="sec-p4-verdict sec-p4-verdict-${verdictClass}">${esc(stage.verdict)}</span>`;
  }

  function stageCard(stage) {
    if (!isRecord(stage)) {
      return `<article class="sec-p4-stage-card"><h3>Stage record pending</h3>` +
        `<div class="pending"><strong>Pending</strong><p>Stage detail was not emitted as an object.</p></div></article>`;
    }
    const label = !own(stage, "label")
      ? pending("stage label not emitted")
      : typeof stage.label !== "string" || !stage.label.trim()
        ? pending("stage label is malformed")
        : esc(stage.label);
    const stageNumber = !own(stage, "stage_number")
      ? pending("stage number not emitted")
      : !hasNumber(stage.stage_number)
        ? pending("stage number is malformed")
        : `Stage ${esc(fmtNum(stage.stage_number))}`;
    const massQualifier = hasNumber(stage.total_kg) && stage.total_kg === 0
      ? `<span class="sec-p4-qualifier">empty · 0 kg total</span>`
      : hasNumber(stage.total_kg) && stage.total_kg > 0 && stage.total_kg < 0.01
        ? `<span class="sec-p4-qualifier">trace · &lt;0.01 kg total</span>`
        : "";

    return `<article class="sec-p4-stage-card"><div class="sec-p4-stage-head">` +
      `<div><div class="sec-p4-stage-number">${stageNumber}</div><h3>${label}</h3></div>` +
      `<div class="sec-p4-verdict-line">${verdict(stage)}${massQualifier}</div></div>` +
      `<div class="sec-p4-headline"><div><span>Total stage mass</span>` +
      `<b>${stageNumberValue(stage, "total_kg", "kg")}</b></div>` +
      `<div><span>Purity fraction</span><b>${stageNumberValue(stage, "purity_fraction", "")}</b></div></div>` +
      `<details class="sec-p4-details"><summary>Accepted species, activity &amp; mass detail</summary>` +
      `<div class="sec-p4-detail-grid"><div class="sec-p4-breakdown"><h4>Accepted species</h4>${acceptedSpecies(stage)}</div>` +
      `<div class="sec-p4-breakdown"><h4>Activity</h4>${activityDetail(stage)}</div>` +
      `<div class="sec-p4-breakdown"><h4>Emitted totals <small>kg basis</small></h4>` +
      `<dl><div><dt>Designated + coproduct</dt><dd>${stageNumberValue(stage, "designated_kg", "kg")}</dd></div>` +
      `<div><dt>Impurity</dt><dd>${stageNumberValue(stage, "impurity_kg", "kg")}</dd></div></dl></div>` +
      `${speciesMap(stage, "designated_species_kg", "Designated species")}` +
      `${speciesMap(stage, "coproduct_species_kg", "Coproduct species")}` +
      `${speciesMap(stage, "impurity_species_kg", "Impurity species")}</div>` +
      `${warningDetail(stage)}${authorityDetail(stage)}</details></article>`;
  }

  function render(artifact) {
    const terminal = isRecord(artifact) && isRecord(artifact.terminal) ? artifact.terminal : null;
    const hasStagePurity = terminal && own(terminal, "stage_purity");
    const stages = hasStagePurity && isRecord(terminal.stage_purity)
      ? Object.values(terminal.stage_purity)
      : [];
    const body = !hasStagePurity
      ? `<div class="pending"><strong>Pending</strong><p>terminal.stage_purity is not emitted.</p></div>`
      : !isRecord(terminal.stage_purity)
        ? `<div class="pending"><strong>Pending</strong><p>terminal.stage_purity is not emitted as a stage map.</p></div>`
        : !stages.length
          ? `<div class="pending"><strong>Pending</strong><p>terminal.stage_purity contains no emitted stages.</p></div>`
          : `<div class="sec-p4-stage-grid">${stages.map(stageCard).join("")}</div>`;

    return `<section id="sec-p4-stage-purity" class="sec-p4-stage-purity" aria-labelledby="sec-p4-stage-purity-title">` +
      `<h2 id="sec-p4-stage-purity-title"><span class="sect">P4</span>Condensation-train stage purity</h2>` +
      `<p class="sub">Backend-emitted stage mass, grade, activity, and verdict. Purity and totals are not recomputed in the viewer.</p>` +
      `${body}</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p4-stage-purity", render });
}(globalThis));
