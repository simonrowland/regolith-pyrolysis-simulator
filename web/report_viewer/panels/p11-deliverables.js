(function registerDeliverablesPanel(root) {
  "use strict";

  const { prettySpecies, speciesColor, exactValue, esc, fmtNum } = root.ReportLabels;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const hasNumber = (value) => typeof value === "number" && Number.isFinite(value);

  const PANEL_ID = "sec-p11-deliverables";

  function pendingBlock(title, message) {
    return `<div class="pending sec-p11-pending"><strong>${esc(title)}</strong><p>${esc(message)}</p></div>`;
  }

  function stateSpan(state, text) {
    const klass = state === "unavailable"
      ? "sec-p11-unavailable"
      : state === "indeterminate"
        ? "sec-p11-indeterminate"
        : "sec-p11-not-emitted";
    return `<span class="${klass}">${esc(text)}</span>`;
  }

  function chip(text, extraClass) {
    const klass = extraClass ? `sec-p11-chip ${extraClass}` : "sec-p11-chip";
    return `<span class="${klass}" data-p11-state-chip="${esc(text)}">${esc(text)}</span>`;
  }

  function flagChip(text) {
    return `<p class="sec-p11-flag" data-p11-state-chip="${esc(text)}">${esc(text)}</p>`;
  }

  function speciesBadge(species) {
    const color = esc(speciesColor(species));
    return `<span class="sec-p11-species" style="--species-color:${color}"><i aria-hidden="true"></i>${esc(prettySpecies(species))}</span>`;
  }

  // Wrapped envelope only. The unwrapped P15 path
  // (product_classification.pure_silica_glass) is a filed consume-path bug
  // and is never sufficient here.
  function readEnvelope(artifact) {
    const terminal = artifact && isRecord(artifact.terminal) ? artifact.terminal : null;
    if (!isRecord(terminal) || !own(terminal, "product_classification")) {
      return { kind: "absent", terminal };
    }
    const block = terminal.product_classification;
    if (block === null) return { kind: "unavailable", terminal };
    if (isRecord(block) && typeof block.status === "string"
      && block.status.toLowerCase() === "unavailable") {
      return { kind: "unavailable", terminal, reason: block.reason };
    }
    if (!isRecord(block)) return { kind: "malformed", terminal };
    const classificationPresent = own(block, "classification");
    const classificationValue = classificationPresent ? block.classification : undefined;
    let classification = null;
    if (classificationPresent && classificationValue === null) {
      return {
        kind: "unavailable",
        terminal,
        markdownPresent: own(block, "markdown"),
        markdown: own(block, "markdown") ? block.markdown : undefined
      };
    }
    if (classificationPresent && isRecord(classificationValue)) {
      classification = classificationValue;
    }
    return {
      kind: "present",
      terminal,
      classification,
      classificationPresent,
      classificationMalformed: classificationPresent && !isRecord(classificationValue),
      markdownPresent: own(block, "markdown"),
      markdown: own(block, "markdown") ? block.markdown : undefined,
      commissioningNotice: own(block, "engine_commissioning_notice")
        ? block.engine_commissioning_notice
        : undefined
    };
  }

  function commissioningBanner(notice) {
    if (!isRecord(notice)) return "";
    const authority = typeof notice.authority === "string" && notice.authority
      ? notice.authority
      : "unspecified";
    const items = Array.isArray(notice.notices) ? notice.notices : [];
    const reasons = items.map((item) => {
      if (!isRecord(item) || typeof item.reason !== "string" || !item.reason) return "";
      const band = isRecord(item.certified_band) ? item.certified_band : null;
      const parts = [];
      if (band && Array.isArray(band.sio2_wt_pct) && band.sio2_wt_pct.length === 2) {
        parts.push(`SiO2 [${band.sio2_wt_pct[0]}, ${band.sio2_wt_pct[1]}] wt%`);
      }
      if (band && Array.isArray(band.temperature_K) && band.temperature_K.length === 2) {
        parts.push(`T [${band.temperature_K[0]}, ${band.temperature_K[1]}] K`);
      }
      return parts.length ? `${item.reason} (${parts.join(", ")})` : item.reason;
    }).filter(Boolean);
    const text = `Engine commissioning: authority ${authority}; ` +
      `${reasons.join(", ") || "notice"}; ` +
      `hours ${notice.first_hour}-${notice.last_hour}; ` +
      `${notice.count} step(s). Reported numbers are unchanged.`;
    return `<p class="sec-p11-flag" data-engine-commissioning-notice="true">${esc(text)}</p>`;
  }

  function quantityClaim(record, key) {
    if (!isRecord(record) || !own(record, key)) {
      return { state: "not-emitted", value: undefined };
    }
    const value = record[key];
    if (value === null) return { state: "unavailable", value: null };
    if (isRecord(value) && typeof value.status === "string"
      && value.status.toLowerCase() === "unavailable") {
      return { state: "unavailable", value };
    }
    if (isRecord(value) && value.verdict === "INDETERMINATE") {
      return { state: "indeterminate", value };
    }
    if (value === "INDETERMINATE") return { state: "indeterminate", value };
    if (hasNumber(value)) return { state: "value", value };
    return { state: "malformed", value };
  }

  function quantityText(claim) {
    if (claim.state === "value") return exactValue(claim.value, "kg");
    if (claim.state === "unavailable") return stateSpan("unavailable", "unavailable");
    if (claim.state === "indeterminate") return stateSpan("indeterminate", "no material");
    if (claim.state === "malformed") {
      const kind = claim.value === null ? "null"
        : Array.isArray(claim.value) ? "array"
          : typeof claim.value;
      return stateSpan("not-emitted", `malformed (${kind})`);
    }
    return stateSpan("not-emitted", "not emitted");
  }

  function kv(label, inner, field, state) {
    return `<div class="sec-p11-kv" data-field="${esc(field)}" data-state="${esc(state)}">` +
      `<span>${esc(label)}</span><b>${inner}</b></div>`;
  }

  function kgRow(record, key, label, roleState) {
    const claim = quantityClaim(record, key);
    const state = claim.state === "value" ? roleState : claim.state;
    return kv(label, quantityText(claim), key, state);
  }

  function scalarRow(record, key, label) {
    if (!isRecord(record) || !own(record, key)) {
      return kv(label, stateSpan("not-emitted", "not emitted"), key, "not-emitted");
    }
    const value = record[key];
    if (value === null) {
      return kv(label, stateSpan("unavailable", "unavailable"), key, "unavailable");
    }
    if (typeof value === "boolean") {
      return kv(label, esc(value ? "true" : "false"), key, "value");
    }
    if (typeof value === "string") {
      return kv(label, esc(value), key, "value");
    }
    if (hasNumber(value)) {
      return kv(label, esc(fmtNum(value)), key, "value");
    }
    return kv(label, stateSpan("not-emitted", "malformed"), key, "malformed");
  }

  function speciesList(record, key, emptyText, valueRole) {
    if (!isRecord(record) || !own(record, key)) {
      return `<div class="sec-p11-species-list" data-field="${esc(key)}" data-state="not-emitted">` +
        `${stateSpan("not-emitted", "not emitted")}</div>`;
    }
    const map = record[key];
    if (map === null) {
      return `<div class="sec-p11-species-list" data-field="${esc(key)}" data-state="unavailable">` +
        `${stateSpan("unavailable", "unavailable")}</div>`;
    }
    if (!isRecord(map)) {
      return `<div class="sec-p11-species-list" data-field="${esc(key)}" data-state="malformed">` +
        `${stateSpan("not-emitted", "malformed")}</div>`;
    }
    const names = Object.keys(map).sort();
    if (!names.length) {
      return `<div class="sec-p11-species-list" data-field="${esc(key)}" data-state="empty">` +
        `<p class="sec-p11-note">${esc(emptyText)}</p></div>`;
    }
    const role = valueRole || "flagged-unqualified-capture";
    const rows = names.map((species) => {
      const claim = quantityClaim(map, species);
      const state = claim.state === "value" ? role : claim.state;
      return `<div class="sec-p11-kv" data-field="${esc(key)}.${esc(species)}" data-state="${esc(state)}">` +
        `<span>${speciesBadge(species)}</span><b>${quantityText(claim)}</b></div>`;
    }).join("");
    return `<div class="sec-p11-species-list" data-field="${esc(key)}">${rows}</div>`;
  }

  function partitionRows(record, key) {
    if (!isRecord(record) || !own(record, key)) {
      return kv("O₂ partition", stateSpan("not-emitted", "not emitted"), key, "not-emitted");
    }
    const map = record[key];
    if (map === null) {
      return kv("O₂ partition", stateSpan("unavailable", "unavailable"), key, "unavailable");
    }
    if (!isRecord(map)) {
      return kv("O₂ partition", stateSpan("not-emitted", "malformed"), key, "malformed");
    }
    const names = Object.keys(map).sort();
    if (!names.length) {
      return `<div class="sec-p11-species-list" data-field="${esc(key)}"><p class="sec-p11-note">O₂ partition map empty.</p></div>`;
    }
    return names.map((name) => kgRow(map, name, `O₂ partition · ${name} · kg`, "qualified-product")).join("");
  }

  function bucketRecord(classification, key) {
    if (!isRecord(classification) || !own(classification, key)) {
      return { present: false, value: null };
    }
    const value = classification[key];
    if (value === null) return { present: true, value: null, unavailable: true };
    if (!isRecord(value)) return { present: true, value, malformed: true };
    return { present: true, value };
  }

  function cardShell(name, extraClass, title, subtitle, body) {
    const klass = extraClass ? `sec-p11-card ${extraClass}` : "sec-p11-card";
    return `<article class="${klass}" data-card="${esc(name)}">` +
      `<h3>${esc(title)}</h3>` +
      `<p class="sec-p11-card-sub">${esc(subtitle)}</p>${body}</article>`;
  }

  function missingBucket(name, extraClass, title, subtitle, reason) {
    return cardShell(
      name,
      extraClass,
      title,
      subtitle,
      pendingBlock(reason, "No kg totals are invented from sibling fields or from the unwrapped P15 path.")
    );
  }

  function silicaNonProductMark(markdown, row) {
    if (typeof markdown === "string" && markdown.includes("Stage 3 silica capture (not a product)")) {
      return true;
    }
    if (!isRecord(row) || !own(row, "flag")) return false;
    const flag = row.flag;
    if (typeof flag === "string" && flag.includes("not a product")) return true;
    if (isRecord(flag) && typeof flag.status === "string" && flag.status.includes("not a product")) {
      return true;
    }
    return false;
  }

  function silicaQualifiedProduct(row) {
    const claim = quantityClaim(row, "class_total_kg");
    return claim.state === "value" && claim.value > 0;
  }

  function silicaFlaggedProduct(row) {
    return silicaQualifiedProduct(row) && isRecord(row) && own(row, "flag");
  }

  // Route/classification and certification are separate: positive class mass
  // may be authoritative or flagged, while non-product capture stays distinct.
  function silicaRouteChip(row, markdown) {
    if (silicaQualifiedProduct(row)) {
      if (silicaFlaggedProduct(row)) return flagChip("flagged silica product");
      return chip("qualified silica product");
    }
    if (silicaNonProductMark(markdown, row)) {
      return flagChip("flagged capture · not a product");
    }
    return "";
  }

  function silicaClassRole(row, markdown) {
    const claim = quantityClaim(row, "class_total_kg");
    if (claim.state !== "value") return claim.state;
    if (silicaFlaggedProduct(row)) return "flagged-product";
    if (claim.value > 0) return "qualified-product";
    if (silicaNonProductMark(markdown, row)) return "flagged-unqualified-capture";
    return "qualified-product";
  }

  function silicaCaptureRole(row, markdown) {
    if (silicaNonProductMark(markdown, row)) return "flagged-unqualified-capture";
    if (silicaFlaggedProduct(row)) return "flagged-product";
    if (silicaQualifiedProduct(row)) return "qualified-product";
    return "value";
  }

  function producerFlag(record) {
    if (!isRecord(record) || !own(record, "flag")) return "";
    const flag = record.flag;
    if (typeof flag === "string" && flag.trim()) {
      return `<p class="sec-p11-flag">${esc(flag.trim())}</p>`;
    }
    if (isRecord(flag) && typeof flag.status === "string" && flag.status.trim()) {
      const parts = [
        flag.status.trim(),
        `authority: ${String(flag.authority)}`,
        `band: ${typeof flag.band === "string" ? flag.band : JSON.stringify(flag.band)}`,
        `reason: ${String(flag.reason)}`
      ];
      return `<p class="sec-p11-flag">${esc(parts.join(" · "))}</p>`;
    }
    return "";
  }

  function renderMetals(classification) {
    const title = "Metals + O₂";
    const subtitle = "Mandate class 1. Metal lots: P2. Condenser purity: P4. Source-side O₂ is source-side, not recovered/captured.";
    const bucket = bucketRecord(classification, "metals_plus_O2");
    if (!bucket.present) {
      return missingBucket("metals", "", title, subtitle, "Metals + O₂ not emitted");
    }
    if (bucket.unavailable) {
      return cardShell("metals", "", title, subtitle,
        pendingBlock("Metals + O₂ unavailable", "classification.metals_plus_O2 is explicitly null; no kilogram product total is shown."));
    }
    if (bucket.malformed) {
      return cardShell("metals", "", title, subtitle,
        pendingBlock("Metals + O₂ malformed", "classification.metals_plus_O2 is present but is not an object."));
    }
    const row = bucket.value;
    return cardShell("metals", "", title, subtitle,
      kgRow(row, "class_total_kg", "Qualified product · kg", "qualified-product") +
      kgRow(row, "metals_total_kg", "Metals subtotal · kg", "qualified-product") +
      kgRow(row, "O2_kg", "Source-side O₂ potential · kg", "qualified-product") +
      partitionRows(row, "O2_partition_kg") +
      `<details class="sec-p11-disclosure"><summary>Metals by species</summary>` +
      speciesList(row, "metals_kg", "No metal species emitted in this class.") +
      `</details>` +
      `<p class="sec-p11-note">Empty condenser stages are judged in P4, not here. Lot purity stays on P4 condenser lots and is never a class-total badge.</p>`
    );
  }

  function renderSilica(classification, markdown) {
    const title = "Pure silica glass";
    const subtitle = "Mandate class 2. class_total_kg is product mass; stage_3_capture_kg is Stage-3 capture. Certification flags remain attached.";
    const bucket = bucketRecord(classification, "pure_silica_glass");
    if (!bucket.present) {
      return missingBucket("silica", "", title, subtitle, "Pure silica glass not emitted");
    }
    if (bucket.unavailable) {
      return cardShell("silica", "", title, subtitle,
        pendingBlock("Pure silica glass unavailable", "classification.pure_silica_glass is explicitly null; no kilogram product total is shown."));
    }
    if (bucket.malformed) {
      return cardShell("silica", "", title, subtitle,
        pendingBlock("Pure silica glass malformed", "classification.pure_silica_glass is present but is not an object."));
    }
    const row = bucket.value;
    const classRole = silicaClassRole(row, markdown);
    const captureRole = silicaCaptureRole(row, markdown);
    const classLabel = silicaFlaggedProduct(row)
      ? "Flagged product · kg"
      : classRole === "qualified-product"
        ? "Qualified product · kg"
        : "Class total · kg";
    return cardShell("silica", "", title, subtitle,
      silicaRouteChip(row, markdown) +
      kgRow(row, "class_total_kg", classLabel, classRole) +
      kgRow(row, "stage_3_capture_kg", "Stage 3 capture · kg", captureRole) +
      producerFlag(row) +
      `<details class="sec-p11-disclosure"><summary>Stage 3 capture by species</summary>` +
      speciesList(row, "stage_3_kg_by_species", "No Stage 3 species emitted.", captureRole) +
      `</details>`
    );
  }

  function mixedClassState(row) {
    const claim = quantityClaim(row, "class_total_kg");
    if (claim.state !== "value") return claim.state;
    if (own(row, "early_tap_mode") && row.early_tap_mode === true && claim.value > 0) {
      return "qualified-product";
    }
    return "flagged-unqualified-capture";
  }

  function mixedClassLabel(state) {
    if (state === "qualified-product") return "Qualified product · kg";
    if (state === "indeterminate") return "Class total · kg";
    if (state === "unavailable" || state === "not-emitted" || state === "malformed") {
      return "Class total · kg";
    }
    return "Class total · not a product · kg";
  }

  function renderGlassGrade(row) {
    const keys = [
      "clarity_model", "family_id", "glass_family", "family_candidate", "match",
      "clarity_grade", "use_grade_optical", "use_grade", "confidence", "projection_basis"
    ];
    const present = isRecord(row) && keys.some((key) => own(row, key));
    if (!present) {
      return kv("Glass grade", stateSpan("not-emitted", "not emitted"), "glass_grade", "not-emitted");
    }
    const parts = [];
    if (own(row, "family_id") && typeof row.family_id === "string") {
      parts.push(`Family candidate: ${row.family_id}`);
    } else if (own(row, "glass_family") && typeof row.glass_family === "string") {
      parts.push(`Family candidate: ${row.glass_family}`);
    } else if (own(row, "family_candidate") && typeof row.family_candidate === "string") {
      parts.push(`Family candidate: ${row.family_candidate}`);
    }
    const model = own(row, "clarity_model") ? row.clarity_model : null;
    const confidence = isRecord(model) && typeof model.confidence === "string"
      ? model.confidence
      : (typeof row.confidence === "string" ? row.confidence : "");
    if (own(row, "clarity_grade") && typeof row.clarity_grade === "string") {
      const word = confidence === "estimate" ? "Fe-clarity estimate" : "Fe-clarity";
      parts.push(`${word}: ${row.clarity_grade}`);
    } else if (isRecord(model) && typeof model.clarity_grade === "string") {
      const word = confidence === "estimate" ? "Fe-clarity estimate" : "Fe-clarity";
      parts.push(`${word}: ${model.clarity_grade}`);
    }
    if (confidence) {
      parts.push(`confidence=${confidence}`);
    }
    if (own(row, "projection_basis") && typeof row.projection_basis === "string") {
      parts.push(`Projection basis: ${row.projection_basis}`);
    }
    if (confidence === "estimate" && !parts.some((part) => part.includes("estimate"))) {
      parts.push("estimate");
    }
    const body = parts.length
      ? `${esc(parts.join(" · "))} · ${esc("FLAG: candidate, not a certification")}`
      : esc("FLAG: candidate, not a certification");
    return `<div class="sec-p11-kv" data-field="glass_grade" data-state="flagged-unqualified-capture">` +
      `<span>Glass family / clarity (producer)</span><b>${body}</b></div>`;
  }

  function renderMixedGlass(classification) {
    const title = "Industrial mixed glass";
    const subtitle = "Mandate class 3. Early-tap residual melt only when the producer set early_tap_mode. Cleaned melt is not a product by default.";
    const bucket = bucketRecord(classification, "industrial_mixed_glass");
    if (!bucket.present) {
      return missingBucket("mixed-glass", "", title, subtitle, "Industrial mixed glass not emitted");
    }
    if (bucket.unavailable) {
      return cardShell("mixed-glass", "", title, subtitle,
        pendingBlock("Industrial mixed glass unavailable", "classification.industrial_mixed_glass is explicitly null; no kilogram product total is shown."));
    }
    if (bucket.malformed) {
      return cardShell("mixed-glass", "", title, subtitle,
        pendingBlock("Industrial mixed glass malformed", "classification.industrial_mixed_glass is present but is not an object."));
    }
    const row = bucket.value;
    const classState = mixedClassState(row);
    return cardShell("mixed-glass", "", title, subtitle,
      kv(mixedClassLabel(classState), quantityText(quantityClaim(row, "class_total_kg")), "class_total_kg", classState) +
      kgRow(row, "mixed_melt_residual_kg", "Mixed melt residual · kg", "flagged-unqualified-capture") +
      scalarRow(row, "early_tap_mode", "Early-tap mode") +
      scalarRow(row, "note", "Producer note") +
      renderGlassGrade(row)
    );
  }

  function renderRump(classification) {
    const title = "Refractory rump · bedrock";
    const subtitle = "Mandate class 4. class_total_kg is the emitted class headline. rump_refractory_oxides_kg is the refractory-oxide floor. Residual silicate, unextracted metals, other, and rump_total_kg are non-product inventory.";
    const bucket = bucketRecord(classification, "refractory_ceramic_rump");
    if (!bucket.present) {
      return missingBucket("rump", "sec-p11-card--rump", title, subtitle, "Refractory rump not emitted");
    }
    if (bucket.unavailable) {
      return cardShell("rump", "sec-p11-card--rump", title, subtitle,
        pendingBlock("Refractory rump unavailable", "classification.refractory_ceramic_rump is explicitly null; no kilogram ceramic product is shown."));
    }
    if (bucket.malformed) {
      return cardShell("rump", "sec-p11-card--rump", title, subtitle,
        pendingBlock("Refractory rump malformed", "classification.refractory_ceramic_rump is present but is not an object."));
    }
    const row = bucket.value;
    const classClaim = quantityClaim(row, "class_total_kg");
    const classState = classClaim.state === "value" ? "qualified-product" : classClaim.state;
    return cardShell("rump", "sec-p11-card--rump", title, subtitle,
      `<div class="sec-p11-headline" data-field="class_total_kg" data-state="${esc(classState)}">` +
      `<span>Qualified product · kg</span>` +
      `<b>${quantityText(classClaim)}</b></div>` +
      kgRow(row, "rump_refractory_oxides_kg", "Refractory oxides floor (by physics) · kg", "qualified-product") +
      kgRow(row, "rump_total_kg", "Residual inventory total (not a product total) · kg", "flagged-unqualified-capture") +
      kgRow(row, "rump_silicate_residual_kg", "Silicate residual · non-product inventory · kg", "flagged-unqualified-capture") +
      kgRow(row, "rump_unextracted_metals_kg", "Unextracted metals residue · non-product inventory · kg", "flagged-unqualified-capture") +
      kgRow(row, "rump_other_kg", "Other / unclassified rump · non-product inventory · kg", "flagged-unqualified-capture") +
      `<details class="sec-p11-disclosure"><summary>Residual inventory by species (not all product)</summary>` +
      speciesList(row, "rump_kg_by_species", "No rump species emitted.") +
      `</details>`
    );
  }

  function renderVolatiles(classification) {
    const title = "Captured volatiles";
    const subtitle = "Producer convenience view. Species are copied; offgas vs train is not re-binned here.";
    const bucket = bucketRecord(classification, "captured_volatiles");
    if (!bucket.present) {
      return missingBucket("volatiles", "sec-p11-card--wide", title, subtitle, "Captured volatiles not emitted");
    }
    if (bucket.unavailable) {
      return cardShell("volatiles", "sec-p11-card--wide", title, subtitle,
        pendingBlock("Captured volatiles unavailable", "classification.captured_volatiles is explicitly null; no kilogram capture is shown."));
    }
    if (bucket.malformed) {
      return cardShell("volatiles", "sec-p11-card--wide", title, subtitle,
        pendingBlock("Captured volatiles malformed", "classification.captured_volatiles is present but is not an object."));
    }
    const row = bucket.value;
    return cardShell("volatiles", "sec-p11-card--wide", title, subtitle,
      kgRow(row, "class_total_kg", "Captured volatiles total · kg", "qualified-product") +
      `<details class="sec-p11-disclosure" open><summary>Captured volatiles by species</summary>` +
      speciesList(row, "kg_by_species", "No captured-volatile species emitted.") +
      `</details>`
    );
  }

  function renderInventory(classification) {
    const unclassified = bucketRecord(classification, "unclassified");
    const spent = bucketRecord(classification, "process_inventory_spent_reductant");
    const parts = [];
    if (!unclassified.present) {
      parts.push(pendingBlock("Unclassified inventory not emitted", "classification.unclassified is absent. No mapping-gap total is invented."));
    } else if (unclassified.unavailable) {
      parts.push(pendingBlock("Unclassified inventory unavailable", "classification.unclassified is explicitly null."));
    } else if (unclassified.malformed) {
      parts.push(pendingBlock("Unclassified inventory malformed", "classification.unclassified is present but is not an object."));
    } else {
      const row = unclassified.value;
      const totalKey = own(row, "total_kg") ? "total_kg" : "class_total_kg";
      parts.push(
        `<div class="sec-p11-inventory-block" data-card="unclassified">` +
        `<h4>Unclassified · non-product inventory</h4>` +
        kgRow(row, totalKey, "Unclassified total · kg", "flagged-unqualified-capture") +
        speciesList(row, "kg_by_species", "No unclassified species emitted.") +
        `</div>`
      );
    }
    if (!spent.present) {
      parts.push(pendingBlock("Spent reductant not emitted", "classification.process_inventory_spent_reductant is absent."));
    } else if (spent.unavailable) {
      parts.push(pendingBlock("Spent reductant unavailable", "classification.process_inventory_spent_reductant is explicitly null."));
    } else if (spent.malformed) {
      parts.push(pendingBlock("Spent reductant malformed", "classification.process_inventory_spent_reductant is present but is not an object."));
    } else {
      const row = spent.value;
      parts.push(
        `<div class="sec-p11-inventory-block" data-card="spent-reductant">` +
        `<h4>Spent reductant · process inventory</h4>` +
        kgRow(row, "class_total_kg", "Spent reductant total · kg", "flagged-unqualified-capture") +
        scalarRow(row, "account", "Account") +
        scalarRow(row, "disposition", "Disposition") +
        speciesList(row, "kg_by_species", "No spent-reductant species emitted.") +
        `</div>`
      );
    }
    return `<div class="sec-p11-inventory">${parts.join("")}</div>`;
  }

  function renderTaxonomy(terminal) {
    if (!isRecord(terminal) || !own(terminal, "terminal_product_taxonomy")) {
      return pendingBlock(
        "W-D7 terminal taxonomy absent",
        "terminal.terminal_product_taxonomy is absent. No density, value-grade, use-class, or second rump total is fabricated. Cross-link stays pending."
      );
    }
    const taxonomy = terminal.terminal_product_taxonomy;
    if (taxonomy === null) {
      return pendingBlock(
        "Terminal taxonomy attempted but unavailable",
        "terminal.terminal_product_taxonomy is explicitly null; the producer attempted classification but could not provide the entity."
      );
    }
    if (!isRecord(taxonomy)) {
      return pendingBlock(
        "Terminal taxonomy captured but malformed",
        "terminal.terminal_product_taxonomy must be an object or explicit null."
      );
    }
    const physical = isRecord(taxonomy.physical_composition) ? taxonomy.physical_composition : null;
    const classKg = physical && own(physical, "class_kg") ? physical.class_kg : undefined;
    const display = typeof taxonomy.display_name === "string" && taxonomy.display_name.trim()
      ? taxonomy.display_name.trim()
      : null;
    const name = display
      ? `<p class="sec-p11-note">Taxonomy display_name: ${esc(display)} · cross-link §7, not a second rump total.</p>`
      : `<p class="sec-p11-note">Taxonomy entity present · cross-link §7, not a second rump total.</p>`;
    if (classKg === undefined) {
      return `<div class="sec-p11-taxonomy">${name}` +
        pendingBlock("Taxonomy class_kg not emitted", "physical_composition.class_kg is absent. Rump class_total_kg is not replaced.") +
        `</div>`;
    }
    if (classKg === null) {
      return `<div class="sec-p11-taxonomy">${name}` +
        pendingBlock("Taxonomy class_kg unavailable", "physical_composition.class_kg is explicitly null.") +
        `</div>`;
    }
    if (!isRecord(classKg)) {
      return `<div class="sec-p11-taxonomy">${name}` +
        pendingBlock("Taxonomy class_kg malformed", "physical_composition.class_kg is present but is not an object.") +
        `</div>`;
    }
    const names = Object.keys(classKg).sort();
    const rows = names.length
      ? names.map((nameKey) => kgRow(classKg, nameKey, `Taxonomy class_kg · ${nameKey} · kg`, "flagged-unqualified-capture")).join("")
      : `<p class="sec-p11-note">Taxonomy class_kg map empty.</p>`;
    return `<div class="sec-p11-taxonomy" data-card="taxonomy">${name}${rows}</div>`;
  }

  function renderMarkdown(envelope) {
    if (!envelope.markdownPresent) {
      return pendingBlock(
        "Product-classification markdown not emitted",
        "product_classification.markdown is absent. Honesty sentences are not invented from kg fields."
      );
    }
    const markdown = envelope.markdown;
    if (markdown === null) {
      return pendingBlock(
        "Product-classification markdown unavailable",
        "product_classification.markdown is explicitly null."
      );
    }
    if (typeof markdown !== "string") {
      return pendingBlock(
        "Product-classification markdown malformed",
        "product_classification.markdown is present but is not a string."
      );
    }
    if (!markdown) {
      return pendingBlock(
        "Product-classification markdown empty",
        "product_classification.markdown is an empty string."
      );
    }
    return `<details class="sec-p11-disclosure sec-p11-markdown"><summary>Producer markdown</summary>` +
      `<pre>${esc(markdown)}</pre></details>`;
  }

  function renderPresent(envelope) {
    if (envelope.classificationMalformed) {
      return pendingBlock(
        "Product classification malformed",
        "product_classification.classification is present but is not an object. No class totals are invented."
      ) + renderMarkdown(envelope);
    }
    const classification = envelope.classification;
    if (!envelope.classificationPresent) {
      return pendingBlock(
        "Product classification buckets not emitted",
        "product_classification.classification is absent. The unwrapped P15 path is not read. No kg totals are invented."
      ) + renderMarkdown(envelope);
    }
    if (!isRecord(classification)) {
      return pendingBlock(
        "Product classification buckets not emitted",
        "product_classification.classification is absent. No kg totals are invented."
      ) + renderMarkdown(envelope);
    }
    return commissioningBanner(envelope.commissioningNotice) +
      `<div class="sec-p11-grid">` +
      `${renderMetals(classification)}${renderSilica(classification, envelope.markdown)}` +
      `${renderMixedGlass(classification)}${renderRump(classification)}` +
      `</div>` +
      renderVolatiles(classification) +
      renderInventory(classification) +
      renderTaxonomy(envelope.terminal) +
      renderMarkdown(envelope);
  }

  function render(artifact) {
    const envelope = readEnvelope(artifact);
    let body;
    if (envelope.kind === "absent") {
      body = pendingBlock(
        "Product classification not emitted",
        "terminal.product_classification is absent. No kilogram headlines, glass grades, or class totals are invented. This is the sample-run-artifact path until the producer block is regenerated."
      );
    } else if (envelope.kind === "unavailable") {
      const reason = typeof envelope.reason === "string" && envelope.reason.trim()
        ? envelope.reason.trim()
        : "unspecified";
      body = pendingBlock(
        "Product classification attempted but unavailable",
        `terminal.product_classification is a typed producer refusal. Product-class totals are unavailable; no kilogram product totals are shown. reason: ${reason}`
      );
    } else if (envelope.kind === "malformed") {
      body = pendingBlock(
        "Product classification malformed",
        "terminal.product_classification is present but is not an object. No class totals are invented."
      );
    } else {
      body = renderPresent(envelope);
    }
    return `<section class="sec-p11-deliverables" id="${PANEL_ID}" aria-labelledby="sec-p11-deliverables-title">` +
      `<h2 id="sec-p11-deliverables-title"><span class="sect">P11</span>Deliverables / product-class depth</h2>` +
      `<p class="sub">Consume-only Mandate grid: metals + O₂, pure silica, early-tap mixed glass, rump as bedrock, plus captured volatiles. Every kilogram is copied from terminal.product_classification.classification; the viewer does not sum, subtract, or match a glass grade.</p>` +
      `${body}</section>`;
  }

  (root.ReportPanels = root.ReportPanels || []).push({ id: PANEL_ID, render });
}(globalThis));
