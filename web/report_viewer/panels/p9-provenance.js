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
  // Registry projection + operator request echo (runner._engines_used), not an
  // invocation/execution chain. Labels must not claim engines "ran" or "were used".
  const ENGINES_USED_TITLE = "Configured engines (engines_used)";
  const PROVENANCE_LABELS = Object.freeze({
    started_at_utc: "Started at (UTC)",
    backend_real_active: "Real engine active",
    degradation_reason: "Degradation reason",
    degraded_from: "Degraded from",
    backend_authoritative: "Backend authoritative",
    certification_allowed: "Certification allowed",
    engines_used: ENGINES_USED_TITLE
  });
  const IDENTITY_FIELDS = Object.freeze([
    ["name", "Engine name"],
    ["cache_version", "Cache version"],
    ["backend_wire_token", "Backend wire token"],
    ["kernel_commit_sha", "Kernel commit SHA"]
  ]);
  // Boolean trust/authority/uncertainty flags from the fidelity surface and
  // identity/contributor extras. Path-aware: registry.*.authoritative is a
  // provider-id string, not a boolean (see isBooleanTrustKey).
  const BOOLEAN_TRUST_KEYS = Object.freeze(new Set([
    "backend_authoritative",
    "certification_allowed",
    "requires_inherited_evidence_class",
    "high_uncertainty",
    "diagnostic_only",
    "extrapolation",
    "backend_real_active"
  ]));
  const FIELD_LABELS = Object.freeze({
    active: "Authoritative slots (registry projection)",
    requested: "Requested providers (config echo)",
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
    skip_reason: "Skip reason",
    engines_used: ENGINES_USED_TITLE
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
    // Exact hash disclosure: surrounding whitespace is malformed provenance, not
    // a display detail — do not silently trim it away from the accessible value.
    if (typeof value !== "string" || !value.length || value !== value.trim()) {
      return pending(`${label} is malformed`);
    }
    const exact = value;
    let compact = fmtRunId(exact);
    if (compact === exact && /^[0-9a-f]{12,}$/i.test(exact)) compact = `${exact.slice(0, 8)}…`;
    return `<span class="sec-p9-identifier" title="${esc(exact)}" aria-label="${esc(`${label}: ${exact}`)}" tabindex="0">${esc(compact)}</span>`;
  }

  function isProviderSlotPath(path) {
    return path.length === 4 && path[0] === "engines_used" && path[1] === "registry"
      && (path[3] === "authoritative" || path[3] === "fallback");
  }

  function isBooleanTrustKey(key, path) {
    if (key === "authoritative") return !isProviderSlotPath(path);
    return BOOLEAN_TRUST_KEYS.has(key);
  }

  function booleanScalar(value, label, { mono = true } = {}) {
    if (typeof value !== "boolean") return pending(`${label} is malformed`);
    const text = esc(String(value));
    return mono ? `<span class="sec-p9-mono-value">${text}</span>` : text;
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
    // Top-level detail rows keep bare true/false (same shape as prior authority rows).
    if (options.boolean) return booleanScalar(value, label, { mono: false });
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

  function treeValue(value, keyHint = "value", path = [String(keyHint)]) {
    const providerSlot = isProviderSlotPath(path);
    const label = keyHint === "authoritative" && !providerSlot ? "Authoritative" : humanize(keyHint);
    if (value === null && providerSlot && keyHint === "fallback") return empty("No fallback provider in emitted field");
    if (value === null && providerSlot && keyHint === "authoritative") return empty("No authoritative provider in emitted field");
    if (value === null) return pending(`${label} emitted as null`);
    if (value === undefined || value === "") return pending(`${label} emitted without a value`);
    // Strict boolean trust flags: string/number never look like valid claims.
    if (isBooleanTrustKey(keyHint, path)) return booleanScalar(value, label);
    if (typeof value === "string") {
      return /(?:sha|hash|digest|commit)/i.test(String(keyHint))
        ? identifierValue(value, label)
        : `<span class="sec-p9-mono-value">${esc(value)}</span>`;
    }
    if (typeof value === "boolean") return `<span class="sec-p9-mono-value">${esc(String(value))}</span>`;
    if (hasNumber(value)) return `<span class="sec-p9-mono-value">${esc(fmtNum(value))}</span>`;
    if (Array.isArray(value)) {
      if (!value.length) return empty("Emitted list is empty");
      return `<ul class="sec-p9-tree-list">${value.map((item) => `<li>${treeValue(item, keyHint, path)}</li>`).join("")}</ul>`;
    }
    if (isRecord(value)) {
      const entries = Object.entries(value);
      if (!entries.length) return empty("Emitted map is empty");
      return `<dl class="sec-p9-tree">${entries.map(([key, item]) => {
        const childPath = [...path, key];
        const childLabel = key === "authoritative" && !isProviderSlotPath(childPath)
          ? "Authoritative"
          : humanize(key);
        return `<div><dt>${esc(childLabel)}</dt><dd>${treeValue(item, key, childPath)}</dd></div>`;
      }).join("")}</dl>`;
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
        // FIELD_LABELS.authoritative is the engines_used slot name ("Authoritative
        // provider"); identity extras are boolean trust flags, so use the short label.
        key === "authoritative" ? "Authoritative" : humanize(key),
        treeValue(value, key)
      ));
    return `<dl class="sec-p9-facts">${rows.join("")}${extras.join("")}</dl>`;
  }

  function enginesUsedValue(metadata) {
    const title = ENGINES_USED_TITLE;
    if (metadata === MALFORMED_RECORD) return pending(`${title} unavailable because a parent record is malformed`);
    if (!isRecord(metadata) || !own(metadata, "engines_used")) return pending(`${title} not emitted`);
    if (metadata.engines_used === null) return pending(`${title} emitted as null`);
    if (!isRecord(metadata.engines_used)) return pending(`${title} is malformed`);
    return treeValue(metadata.engines_used, "engines_used");
  }

  // Parent/child record diagnosis: absent / null / malformed / ok stay distinct.
  // Do not collapse a broken parent into "child is malformed".
  function resolveTopRecord(artifact, key) {
    if (!isRecord(artifact)) return { kind: "artifact-malformed" };
    if (!own(artifact, key)) return { kind: "absent" };
    if (artifact[key] === null) return { kind: "null" };
    if (!isRecord(artifact[key])) return { kind: "malformed" };
    return { kind: "ok", value: artifact[key] };
  }

  function resolveChildRecord(parent, childKey) {
    if (parent.kind === "artifact-malformed") return { kind: "artifact-malformed" };
    if (parent.kind === "absent") return { kind: "absent" };
    if (parent.kind === "null") return { kind: "parent-null" };
    if (parent.kind === "malformed") return { kind: "parent-malformed" };
    if (!own(parent.value, childKey)) return { kind: "absent" };
    if (parent.value[childKey] === null) return { kind: "null" };
    if (!isRecord(parent.value[childKey])) return { kind: "malformed" };
    return { kind: "ok", value: parent.value[childKey] };
  }

  function recordForRender(resolved) {
    if (resolved.kind === "ok") return resolved.value;
    // Absent or present-as-null: no field values. Notices name the null case.
    if (resolved.kind === "absent" || resolved.kind === "null") return null;
    // parent-null / parent-malformed / malformed / artifact-malformed:
    // fields read as unavailable because a parent record is broken.
    return MALFORMED_RECORD;
  }

  function metadataNoticeHtml(terminal, metadata) {
    if (terminal.kind === "artifact-malformed") {
      return `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is unavailable because the artifact is malformed.</p></div>`;
    }
    if (terminal.kind === "null") {
      return `<div class="pending"><strong>Pending</strong><p>terminal emitted as null; run_metadata unavailable.</p></div>`;
    }
    if (terminal.kind === "malformed") {
      return `<div class="pending"><strong>Pending</strong><p>terminal is malformed; run_metadata unavailable.</p></div>`;
    }
    if (terminal.kind === "absent" || metadata.kind === "absent") {
      return `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is not emitted.</p></div>`;
    }
    if (metadata.kind === "null") {
      return `<div class="pending"><strong>Pending</strong><p>run_metadata emitted as null.</p></div>`;
    }
    if (metadata.kind === "malformed") {
      return `<div class="pending"><strong>Pending</strong><p>terminal.run_metadata is malformed; expected an object.</p></div>`;
    }
    return "";
  }

  function identityNoticeHtml(header, identity) {
    if (header.kind === "artifact-malformed") {
      return `<div class="pending"><strong>Pending</strong><p>header.engine_identity is unavailable because the artifact is malformed.</p></div>`;
    }
    if (header.kind === "null") {
      return `<div class="pending"><strong>Pending</strong><p>header emitted as null; engine_identity unavailable.</p></div>`;
    }
    if (header.kind === "malformed") {
      return `<div class="pending"><strong>Pending</strong><p>header is malformed; engine_identity unavailable.</p></div>`;
    }
    if (header.kind === "absent" || identity.kind === "absent") {
      return `<div class="pending"><strong>Pending</strong><p>header.engine_identity is not emitted.</p></div>`;
    }
    if (identity.kind === "null") {
      return `<div class="pending"><strong>Pending</strong><p>engine_identity emitted as null.</p></div>`;
    }
    if (identity.kind === "malformed") {
      return `<div class="pending"><strong>Pending</strong><p>header.engine_identity is malformed; expected an object.</p></div>`;
    }
    return "";
  }

  function render(artifact) {
    const artifactMalformed = !isRecord(artifact);
    const header = resolveTopRecord(artifact, "header");
    const terminal = resolveTopRecord(artifact, "terminal");
    const metadataResolved = resolveChildRecord(terminal, "run_metadata");
    const identityResolved = resolveChildRecord(header, "engine_identity");
    const metadata = recordForRender(metadataResolved);
    const identity = recordForRender(identityResolved);
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
      detailRow(
        PROVENANCE_LABELS.backend_authoritative,
        fieldValue(metadata, "backend_authoritative", PROVENANCE_LABELS.backend_authoritative, { boolean: true })
      ),
      detailRow(
        PROVENANCE_LABELS.certification_allowed,
        fieldValue(metadata, "certification_allowed", PROVENANCE_LABELS.certification_allowed, { boolean: true })
      ),
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
    const metadataNotice = metadataNoticeHtml(terminal, metadataResolved);
    const identityNotice = identityNoticeHtml(header, identityResolved);
    const degradationStateContext = isRecord(metadata) && own(metadata, "degradation_reason")
      ? `<div class="sec-p9-state-context"><span>${PROVENANCE_LABELS.degradation_reason}</span>${fieldValue(metadata, "degradation_reason", PROVENANCE_LABELS.degradation_reason)}</div>`
      : "";

    return `<section id="sec-p9-provenance" class="sec-p9-provenance" aria-labelledby="sec-p9-provenance-title">` +
      `<h2 id="sec-p9-provenance-title"><span class="sect">P9</span>Run &amp; engine provenance</h2>` +
      `<p class="sub">Emitted run identity, backend state, evidence class, and configured engines. No confidence tier is computed in the viewer. Run-level grade rendering is deferred to the report provenance section.</p>` +
      `${artifactNotice}${metadataNotice}${identityNotice}<div class="sec-p9-badges">${badges}</div>` +
      degradationStateContext +
      `<details class="sec-p9-details"><summary>Full run, engine, and label provenance</summary>` +
      `<div class="sec-p9-detail-grid">` +
      `<article><h3>Run input provenance</h3><dl class="sec-p9-facts">${runFacts}</dl></article>` +
      `<article><h3>Backend evidence &amp; degradation</h3><dl class="sec-p9-facts">${backendFacts}${optionalTrustFields}</dl></article>` +
      `<article><h3>Artifact engine identity</h3>${identityDetail(identity)}</article>` +
      `<article class="sec-p9-engine-chain"><h3>${ENGINES_USED_TITLE}</h3>` +
      `<p class="sec-p9-note">Provider registry projection and operator request echo as emitted — not an invocation or execution trace. Does not establish real-backend activity.</p>` +
      `${enginesUsedValue(metadata)}</article></div></details></section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: "sec-p9-provenance", render });
}(globalThis));
