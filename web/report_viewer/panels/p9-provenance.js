(function registerProvenancePanel(root) {
  "use strict";

  const {
    fmtNum, fmtRunId, prettySpecies, prettyFeedstock, speciesColor, esc
  } = root.ReportLabels;
  const own = (value, key) => value !== null && typeof value === "object"
    && Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const MALFORMED_RECORD = Symbol("malformed provenance record");
  const PROVENANCE_LABELS = Object.freeze({
    started_at_utc: "Started at (UTC)",
    backend_real_active: "Real engine active",
    degradation_reason: "Degradation reason",
    degraded_from: "Degraded from",
    backend_authoritative: "Backend authoritative",
    certification_allowed: "Certification allowed"
  });
  const IDENTITY_FIELDS = Object.freeze([
    ["name", "Engine name"],
    ["cache_version", "Cache version"],
    ["backend_wire_token", "Backend wire token"],
    ["kernel_commit_sha", "Kernel commit SHA"]
  ]);
  const FIELD_LABELS = Object.freeze({
    active: "Active providers",
    requested: "Requested providers",
    registry: "Provider registry",
    authoritative: "Authoritative provider",
    fallback: "Fallback provider",
    shadows: "Shadow providers",
    backend_wire_token: "Backend wire token",
    cache_version: "Cache version",
    kernel_commit_sha: "Kernel commit SHA",
    started_at_utc: PROVENANCE_LABELS.started_at_utc,
    backend_real_active: PROVENANCE_LABELS.backend_real_active,
    backend_status: "Backend status",
    runtime_status: "Runtime status",
    degradation_reason: PROVENANCE_LABELS.degradation_reason,
    degraded_from: PROVENANCE_LABELS.degraded_from,
    evidence_class: "Evidence class",
    backend_authoritative: PROVENANCE_LABELS.backend_authoritative,
    certification_allowed: PROVENANCE_LABELS.certification_allowed,
    label_source: "Label source",
    requires_inherited_evidence_class: "Requires inherited evidence class",
    high_uncertainty: "High uncertainty",
    diagnostic_only: "Diagnostic only",
    skip_reason: "Skip reason"
  });

  function pending(message) {
    return `<span class="sec-p9-pending">Pending · ${esc(message)}</span>`;
  }

  function empty(message) {
    return `<span class="sec-p9-empty">${esc(message)}</span>`;
  }

  function humanize(key) {
    const raw = String(key ?? "");
    if (own(FIELD_LABELS, raw)) return FIELD_LABELS[raw];
    const words = raw.replace(/[_-]+/g, " ").trim();
    return words ? words.replace(/\b\w/g, (character) => character.toUpperCase()) : "Unlabelled field";
  }

  function identifierValue(value, label) {
    if (typeof value !== "string" || !value.trim()) return pending(`${label} is malformed`);
    const exact = value.trim();
    let compact = fmtRunId(exact);
    if (compact === exact && /^[0-9a-f]{12,}$/i.test(exact)) compact = `${exact.slice(0, 8)}…`;
    return `<span class="sec-p9-identifier" title="${esc(exact)}" aria-label="${esc(`${label}: ${exact}`)}" tabindex="0">${esc(compact)}</span>`;
  }

  function scalarValue(value, label, options = {}) {
    if (options.identifier) return identifierValue(value, label);
    if (options.feedstock) {
      return typeof value === "string" && value.trim()
        ? esc(prettyFeedstock(value))
        : pending(`${label} is malformed`);
    }
    if (options.unit) {
      return hasNumber(value)
        ? esc(fmtNum(value, options.unit))
        : pending(`${label} is malformed`);
    }
    if (typeof value === "string" && value.trim()) return esc(value);
    if (typeof value === "boolean") return esc(String(value));
    if (hasNumber(value)) return esc(fmtNum(value));
    return pending(`${label} is malformed`);
  }

  function fieldValue(record, key, label, options = {}) {
    if (record === MALFORMED_RECORD) return pending(`${label} unavailable because its parent record is malformed`);
    if (!isRecord(record) || !own(record, key)) return pending(`${label} not emitted`);
    if (record[key] === null) return pending(`${label} emitted as null`);
    if (typeof record[key] === "string" && !record[key].trim()) {
      return empty(`${label}: emitted string is empty`);
    }
    return scalarValue(record[key], label, options);
  }

  function realActiveValue(metadata) {
    const label = PROVENANCE_LABELS.backend_real_active;
    if (metadata === MALFORMED_RECORD) return pending(`${label} unavailable because a parent record is malformed`);
    if (!isRecord(metadata) || !own(metadata, "backend_real_active")) return pending(`${label} not emitted`);
    if (metadata.backend_real_active === null) return pending(`${label} emitted as null`);
    if (typeof metadata.backend_real_active !== "boolean") return pending(`${label} is malformed`);
    return `<span class="sec-p9-explicit-state">${metadata.backend_real_active ? "Yes" : "No"} <small>(emitted)</small></span>`;
  }

  function listValue(record, key, label) {
    if (record === MALFORMED_RECORD) return pending(`${label} unavailable because its parent record is malformed`);
    if (!isRecord(record) || !own(record, key)) return pending(`${label} not emitted`);
    if (record[key] === null) return pending(`${label} emitted as null`);
    if (!Array.isArray(record[key])) return pending(`${label} is malformed`);
    if (!record[key].length) return empty(`${label}: emitted list is empty`);
    return `<ul class="sec-p9-inline-list">${record[key].map((value) => `<li>${treeValue(value, key)}</li>`).join("")}</ul>`;
  }

  function degradationReasonValue(metadata) {
    const label = PROVENANCE_LABELS.degradation_reason;
    if (metadata === MALFORMED_RECORD) {
      return pending(`${label} unavailable because its parent record is malformed`);
    }
    if (!isRecord(metadata) || !own(metadata, "degradation_reason")) return pending(`${label} not emitted`);
    return fieldValue(metadata, "degradation_reason", label);
  }

  function degradationOriginValue(metadata) {
    const label = PROVENANCE_LABELS.degraded_from;
    if (metadata === MALFORMED_RECORD) {
      return pending(`${label} unavailable because its parent record is malformed`);
    }
    if (!isRecord(metadata) || !own(metadata, "degraded_from")) return pending(`${label} not emitted`);
    return listValue(metadata, "degraded_from", label);
  }

  function speciesLabel(species) {
    return `<span class="sec-p9-species" style="--species-color:${esc(speciesColor(species))}">` +
      `<span class="sec-p9-species-dot" aria-hidden="true"></span>${esc(prettySpecies(species))}</span>`;
  }

  function additivesValue(metadata) {
    const label = "Additives";
    if (metadata === MALFORMED_RECORD) return pending(`${label} unavailable because a parent record is malformed`);
    if (!isRecord(metadata) || !own(metadata, "additives_kg")) return pending(`${label} not emitted`);
    if (metadata.additives_kg === null) return pending(`${label} emitted as null`);
    if (!isRecord(metadata.additives_kg)) return pending(`${label} map is malformed`);
    const entries = Object.entries(metadata.additives_kg);
    if (!entries.length) return empty("No additive entries in emitted map");
    return `<ul class="sec-p9-additives">${entries.map(([species, mass]) =>
      `<li>${speciesLabel(species)}<b>${hasNumber(mass) ? esc(fmtNum(mass, "kg")) : pending("Additive mass is malformed")}</b></li>`
    ).join("")}</ul>`;
  }

  function treeValue(value, keyHint = "value") {
    const label = humanize(keyHint);
    if (value === null && keyHint === "fallback") return empty("No fallback provider in emitted field");
    if (value === null && keyHint === "authoritative") return empty("No authoritative provider in emitted field");
    if (value === null || value === undefined || value === "") return pending(`${label} emitted without a value`);
    if (typeof value === "string") {
      return /(?:sha|hash|digest|commit)/i.test(String(keyHint))
        ? identifierValue(value, label)
        : `<span class="sec-p9-mono-value">${esc(value)}</span>`;
    }
    if (typeof value === "boolean") return `<span class="sec-p9-mono-value">${esc(String(value))}</span>`;
    if (hasNumber(value)) return `<span class="sec-p9-mono-value">${esc(fmtNum(value))}</span>`;
    if (Array.isArray(value)) {
      if (!value.length) return empty("Emitted list is empty");
      return `<ul class="sec-p9-tree-list">${value.map((item) => `<li>${treeValue(item, keyHint)}</li>`).join("")}</ul>`;
    }
    if (isRecord(value)) {
      const entries = Object.entries(value);
      if (!entries.length) return empty("Emitted map is empty");
      return `<dl class="sec-p9-tree">${entries.map(([key, item]) =>
        `<div><dt>${esc(key === "authoritative" && typeof item === "boolean" ? "Authoritative" : humanize(key))}</dt><dd>${treeValue(item, key)}</dd></div>`
      ).join("")}</dl>`;
    }
    return pending(`${label} is malformed`);
  }

  function detailRow(label, value) {
    return `<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
  }

  function badge(label, value, tone = "neutral") {
    return `<span class="sec-p9-badge sec-p9-badge-${esc(tone)}"><span>${esc(label)}</span><b>${value}</b></span>`;
  }

  function statusTone(value) {
    if (typeof value !== "string") return "pending";
    return value.toLowerCase() === "ok" ? "positive" : "caution";
  }

  function identityDetail(identity) {
    if (identity === MALFORMED_RECORD) return pending("Engine identity unavailable because a parent record is malformed");
    if (!isRecord(identity)) return pending("Engine identity not emitted");
    const known = new Set(IDENTITY_FIELDS.map(([key]) => key));
    const rows = IDENTITY_FIELDS.map(([key, label]) => detailRow(
      label,
      fieldValue(identity, key, label, { identifier: key === "kernel_commit_sha" })
    ));
    const extras = Object.entries(identity)
      .filter(([key]) => !known.has(key))
      .map(([key, value]) => detailRow(
        // FIELD_LABELS.authoritative is the engine-chain slot name ("Authoritative
        // provider"); identity extras are boolean trust flags, so use the short label.
        key === "authoritative" ? "Authoritative" : humanize(key),
        treeValue(value, key)
      ));
    return `<dl class="sec-p9-facts">${rows.join("")}${extras.join("")}</dl>`;
  }

  function engineChainValue(metadata) {
    if (metadata === MALFORMED_RECORD) return pending("Engine chain unavailable because a parent record is malformed");
    if (!isRecord(metadata) || !own(metadata, "engines_used")) return pending("Engine chain not emitted");
    if (metadata.engines_used === null) return pending("Engine chain emitted as null");
    if (!isRecord(metadata.engines_used)) return pending("Engine chain is malformed");
    return treeValue(metadata.engines_used, "engines_used");
  }

  function render(artifact) {
    const artifactMalformed = !isRecord(artifact);
    const header = artifactMalformed
      ? MALFORMED_RECORD
      : own(artifact, "header")
      ? (isRecord(artifact.header) ? artifact.header : MALFORMED_RECORD)
      : null;
    const terminal = artifactMalformed
      ? MALFORMED_RECORD
      : own(artifact, "terminal")
      ? (isRecord(artifact.terminal) ? artifact.terminal : MALFORMED_RECORD)
      : null;
    const metadata = terminal === MALFORMED_RECORD
      ? MALFORMED_RECORD
      : terminal && own(terminal, "run_metadata")
        ? (isRecord(terminal.run_metadata) ? terminal.run_metadata : MALFORMED_RECORD)
        : null;
    const identity = header === MALFORMED_RECORD
      ? MALFORMED_RECORD
      : header && own(header, "engine_identity")
        ? (isRecord(header.engine_identity) ? header.engine_identity : MALFORMED_RECORD)
        : null;
    const metadataRecord = isRecord(metadata) ? metadata : null;

    const badges = [
      badge("Feedstock", fieldValue(metadata, "feedstock_id", "Feedstock", { feedstock: true })),
      badge("Campaign", fieldValue(metadata, "campaign", "Campaign")),
      badge("Track", fieldValue(metadata, "track", "Track")),
      badge("Backend", fieldValue(metadata, "backend", "Backend")),
      badge("Backend status", fieldValue(metadata, "backend_status", "Backend status"), statusTone(metadataRecord?.backend_status)),
      badge("Runtime status", fieldValue(metadata, "runtime_status", "Runtime status"), statusTone(metadataRecord?.runtime_status)),
      badge(PROVENANCE_LABELS.backend_real_active, realActiveValue(metadata), metadataRecord?.backend_real_active === true ? "positive" : metadataRecord?.backend_real_active === false ? "caution" : "pending"),
      badge("Evidence class", fieldValue(metadata, "evidence_class", "Evidence class")),
      badge("Engine identity", fieldValue(identity, "name", "Engine identity"))
    ].join("");

    const runFacts = [
      detailRow("Feedstock ID", fieldValue(metadata, "feedstock_id", "Feedstock ID")),
      detailRow("Campaign", fieldValue(metadata, "campaign", "Campaign")),
      detailRow("Charge mass", fieldValue(metadata, "mass_kg", "Charge mass", { unit: "kg" })),
      detailRow("Additives", additivesValue(metadata)),
      detailRow("Track", fieldValue(metadata, "track", "Track")),
      detailRow(PROVENANCE_LABELS.started_at_utc, fieldValue(metadata, "started_at_utc", PROVENANCE_LABELS.started_at_utc)),
      detailRow("Kernel commit SHA", fieldValue(metadata, "kernel_commit_sha", "Kernel commit SHA", { identifier: true }))
    ].join("");

    const backendFacts = [
      detailRow("Backend", fieldValue(metadata, "backend", "Backend")),
      detailRow("Backend status", fieldValue(metadata, "backend_status", "Backend status")),
      detailRow("Runtime status", fieldValue(metadata, "runtime_status", "Runtime status")),
      detailRow(PROVENANCE_LABELS.backend_real_active, realActiveValue(metadata)),
      detailRow(PROVENANCE_LABELS.degradation_reason, degradationReasonValue(metadata)),
      detailRow(PROVENANCE_LABELS.degraded_from, degradationOriginValue(metadata)),
      detailRow("Evidence class", fieldValue(metadata, "evidence_class", "Evidence class")),
      detailRow(PROVENANCE_LABELS.backend_authoritative, fieldValue(metadata, "backend_authoritative", PROVENANCE_LABELS.backend_authoritative)),
      detailRow(PROVENANCE_LABELS.certification_allowed, fieldValue(metadata, "certification_allowed", PROVENANCE_LABELS.certification_allowed)),
      detailRow("Label source", fieldValue(metadata, "label_source", "Label source")),
      detailRow("Label sources", listValue(metadata, "label_sources", "Label sources"))
    ].join("");

    const optionalTrustFields = ["cache_state", "contributors", "requires_inherited_evidence_class"]
      .filter((key) => isRecord(metadata) && own(metadata, key))
      .map((key) => detailRow(humanize(key), treeValue(metadata[key], key)))
      .join("");

    const artifactNotice = artifactMalformed
      ? `<div class="pending"><strong>Pending</strong><p>Artifact is malformed; expected an object.</p></div>`
      : "";
    const metadataNotice = artifactMalformed
      ? `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is unavailable because the artifact is malformed.</p></div>`
      : metadata === MALFORMED_RECORD
      ? `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is malformed; expected an object.</p></div>`
      : metadata
        ? ""
        : `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is not emitted.</p></div>`;
    const identityNotice = artifactMalformed
      ? `<div class="pending"><strong>Pending</strong><p>header.engine_identity is unavailable because the artifact is malformed.</p></div>`
      : identity === MALFORMED_RECORD
      ? `<div class="pending"><strong>Pending</strong><p>header.engine_identity is malformed; expected an object.</p></div>`
      : identity
        ? ""
        : `<div class="pending"><strong>Pending</strong><p>header.engine_identity is not emitted.</p></div>`;
    const degradationStateContext = isRecord(metadata) && own(metadata, "degradation_reason")
      ? `<div class="sec-p9-state-context"><span>${PROVENANCE_LABELS.degradation_reason}</span>${fieldValue(metadata, "degradation_reason", PROVENANCE_LABELS.degradation_reason)}</div>`
      : "";

    return `<section id="sec-p9-provenance" class="sec-p9-provenance" aria-labelledby="sec-p9-provenance-title">` +
      `<h2 id="sec-p9-provenance-title"><span class="sect">P9</span>Run &amp; engine provenance</h2>` +
      `<p class="sub">Emitted run identity, backend state, evidence class, and engine chain. No confidence tier is computed in the viewer.</p>` +
      `${artifactNotice}${metadataNotice}${identityNotice}<div class="sec-p9-badges">${badges}</div>` +
      degradationStateContext +
      `<details class="sec-p9-details"><summary>Full run, engine, and label provenance</summary>` +
      `<div class="sec-p9-detail-grid">` +
      `<article><h3>Run input provenance</h3><dl class="sec-p9-facts">${runFacts}</dl></article>` +
      `<article><h3>Backend evidence &amp; degradation</h3><dl class="sec-p9-facts">${backendFacts}${optionalTrustFields}</dl></article>` +
      `<article><h3>Artifact engine identity</h3>${identityDetail(identity)}</article>` +
      `<article class="sec-p9-engine-chain"><h3>Engine chain</h3>` +
      `<p class="sec-p9-note">Kernel provider slots are shown as emitted; they do not establish real-backend activity.</p>` +
      `${engineChainValue(metadata)}</article></div></details></section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p9-provenance", render });
}(globalThis));
