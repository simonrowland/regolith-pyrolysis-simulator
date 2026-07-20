"use strict";

(function registerSankeyPanel(root) {
  const {
    fmtNum, prettySpecies, accountLabel, speciesColor, esc
  } = root.ReportLabels;

  const PANEL_ID = "sec-p14-sankey";
  const TRACE_FRACTION = 0.005;
  const SQRT_SCALE_RATIO = 20;
  const SEPARATE_O2_ACCOUNTS = Object.freeze(new Set([
    "terminal.oxygen_stage0_stored",
    "terminal.oxygen_melt_offgas_stored",
    "terminal.oxygen_melt_offgas_vented_to_vacuum",
    "terminal.oxygen_bubbler_external_vented_to_vacuum",
    "terminal.oxygen_melt_offgas_captured",
    "terminal.oxygen_mre_anode_stored",
    "reservoir.oxygen_cistern_liquid_inventory"
  ]));
  const AUTHORITY_FIELDS = Object.freeze([
    "authoritative", "diagnostic_only", "extrapolation", "high_uncertainty",
    "status", "source", "reference", "skip_reason"
  ]);
  const ACCOUNT_RANK = Object.freeze({
    "process.metal_phase_bottom_pool": 0,
    "process.metal_phase_float_layer": 1,
    "terminal.drain_tap_material": 2,
    "process.metal_phase": 3,
    "process.condensation_train": 10,
    "terminal.oxygen_stage0_stored": 19,
    "terminal.oxygen_melt_offgas_stored": 20,
    "terminal.oxygen_melt_offgas_vented_to_vacuum": 21,
    "terminal.oxygen_bubbler_external_vented_to_vacuum": 22,
    "terminal.oxygen_melt_offgas_captured": 23,
    "terminal.oxygen_mre_anode_stored": 24,
    "reservoir.oxygen_cistern_liquid_inventory": 25,
    "terminal.offgas": 30,
    "process.overhead_gas": 50,
    "process.reagent_inventory": 51,
    "process.spent_reductant_residue": 53,
    "reservoir.fo2_buffer": 54,
    "process.cleaned_melt": 100
  });

  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isObjectMap = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isFiniteNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const molText = (value) => esc(fmtNum(value, "mol"));

  function stableAccountId(account) {
    const text = String(account);
    let hash = 0;
    for (let index = 0; index < text.length; index += 1) {
      hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
    }
    const slug = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "account";
    return `sec-p14-account-${slug}-${hash.toString(36)}`;
  }

  function accountRank(account) {
    if (own(ACCOUNT_RANK, account)) return ACCOUNT_RANK[account];
    const wall = account.match(/^process\.wall_deposit_segment_stage_(\d+)_to_stage_/);
    if (wall) return 40 + Number(wall[1]) / 100;
    if (/^reservoir\.reagent\./.test(account)) return 52;
    return 90;
  }

  function orderedAccounts(finalState) {
    return Object.entries(finalState)
      .map(([account, species]) => ({ account, species }))
      .sort((left, right) => accountRank(left.account) - accountRank(right.account)
        || left.account.localeCompare(right.account));
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = [...values].sort((left, right) => left - right);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function accountData(account, species) {
    if (!isObjectMap(species)) {
      return { account, species, entries: [], total: null, positiveTotal: null, malformed: true };
    }
    const entries = Object.entries(species).map(([name, value]) => ({ name, value }));
    const malformed = entries.some((entry) => !isFiniteNumber(entry.value));
    return {
      account,
      species,
      entries,
      total: malformed ? null : entries.reduce((sum, entry) => sum + entry.value, 0),
      positiveTotal: malformed ? null : entries.reduce(
        (sum, entry) => sum + (entry.value > 0 ? entry.value : 0),
        0
      ),
      malformed
    };
  }

  function traceGroups(account, cutoff) {
    if (account.malformed) return { major: [], trace: [] };
    const positive = account.entries.filter((entry) => entry.value > 0);
    if (cutoff === null || SEPARATE_O2_ACCOUNTS.has(account.account)) {
      return { major: positive, trace: [] };
    }
    return {
      major: positive.filter((entry) => entry.value >= cutoff),
      trace: positive.filter((entry) => entry.value < cutoff)
    };
  }

  function ribbonBackground(groups, total) {
    const visible = groups.major.map((entry) => ({ color: speciesColor(entry.name), value: entry.value }));
    groups.trace.forEach((entry) => visible.push({ color: speciesColor(entry.name), value: entry.value }));
    let cursor = 0;
    return visible.map((entry) => {
      const start = cursor / total * 100;
      cursor += entry.value;
      const end = cursor / total * 100;
      return `${entry.color} ${start.toFixed(4)}%, ${entry.color} ${end.toFixed(4)}%`;
    }).join(", ");
  }

  function speciesInventoryText(entries) {
    if (!entries.length) return "emitted empty account · 0 species";
    return entries.map((entry) => isFiniteNumber(entry.value)
      ? `${prettySpecies(entry.name)} ${fmtNum(entry.value, "mol")}`
      : `${prettySpecies(entry.name)} non-numeric (${Array.isArray(entry.value) ? "array" : typeof entry.value})`
    ).join("; ");
  }

  function authorityValueText(value) {
    return value !== null && typeof value === "object" ? JSON.stringify(value) : String(value);
  }

  function authorityChips(payload) {
    return AUTHORITY_FIELDS.filter((field) => own(payload, field)).map((field) => {
      return `<span class="sec-p14-flag"><b>${esc(field.replaceAll("_", " "))}</b> ${esc(authorityValueText(payload[field]))}</span>`;
    }).join("");
  }

  function provenanceTier(terminal) {
    if (!own(terminal, "yield_disposition")) {
      return `<div class="pending sec-p14-provenance"><strong>Pending provenance</strong>`
        + `<p>feedstock-provenance tier pending (origin data not emitted for this run).</p></div>`;
    }
    const payload = terminal.yield_disposition;
    if (!isObjectMap(payload)) {
      return `<div class="pending sec-p14-provenance"><strong>Malformed provenance payload</strong>`
        + `<p>yield_disposition was emitted, but it is not an origin-resolved account map. No feedstock shares are shown.</p></div>`;
    }
    const basis = own(payload, "basis")
      ? `<span class="sec-p14-flag"><b>basis</b> ${esc(authorityValueText(payload.basis))}</span>`
      : "";
    const flags = authorityChips(payload);
    return `<details class="sec-p14-provenance"><summary>yield_disposition emitted · provenance schema check</summary>`
      + `<div class="sec-p14-flags">${basis}${flags}</div>`
      + `<div class="pending sec-p14-provenance-pending"><strong>Pending origin-resolved shares</strong>`
      + `<p>This payload does not expose a producer-defined chart-ready origin-to-account link schema. `
      + `No feedstock percentages are inferred from target fractions or terminal mol inventories.</p></div></details>`;
  }

  function availabilityNote(finalState) {
    const standIns = ["process.condensation_train", "process.cleaned_melt"]
      .filter((account) => own(finalState, account))
      .map((account) => esc(accountLabel(account)));
    const standInText = standIns.length
      ? `Emitted aggregate terminal stand-ins in this run: ${standIns.join(", ")}.`
      : "No aggregate condensation-train or cleaned-melt stand-in account is emitted in this run.";
    return `Only literal account keys emitted under terminal.final_state are shown. `
      + `Dedicated per-stage condenser, glass, refractory-rump, and ceramic-product destinations are not inferred when their account keys are absent. `
      + standInText;
  }

  function accountDetail(account, groups) {
    const detailId = stableAccountId(account.account);
    if (account.malformed) {
      return `<details class="sec-p14-account-detail" id="${esc(detailId)}">`
        + `<summary>${esc(accountLabel(account.account))}</summary>`
        + `<p class="sec-p14-account-key"><code>${esc(account.account)}</code> · malformed species map · values pending</p></details>`;
    }
    const rows = account.entries.length
      ? account.entries.map((entry) => `<tr><td>${esc(prettySpecies(entry.name))}</td>`
        + `<td class="num">${isFiniteNumber(entry.value) ? molText(entry.value) : `non-numeric (${esc(Array.isArray(entry.value) ? "array" : typeof entry.value)})`}</td></tr>`).join("")
      : `<tr><td colspan="2">emitted empty account</td></tr>`;
    const trace = groups.trace.length
      ? `<span class="sec-p14-trace">${groups.trace.length} species in global trace node</span>`
      : "";
    return `<details class="sec-p14-account-detail" id="${esc(detailId)}">`
      + `<summary>${esc(accountLabel(account.account))} ${trace}</summary>`
      + `<p class="sec-p14-account-key"><code>${esc(account.account)}</code> · emitted basis: mol by species</p>`
      + `<div class="sec-p14-table-wrap"><table><thead><tr><th>Species</th><th class="num">Emitted mol</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div></details>`;
  }

  function accountRow(account, groups, scale, largestVisible) {
    const detailId = stableAccountId(account.account);
    const inventoryText = speciesInventoryText(account.entries);
    let ribbon = "";
    let status = "";
    if (account.malformed) {
      status = `<span class="sec-p14-non-ribbon">width pending · malformed species map</span>`;
    } else if (groups.major.length) {
      const visibleTotal = groups.major.reduce((sum, entry) => sum + entry.value, 0);
      const ratio = visibleTotal / largestVisible;
      const width = (scale === "sqrt" ? Math.sqrt(ratio) : ratio) * 100;
      const background = ribbonBackground({ major: groups.major, trace: [] }, visibleTotal);
      const hover = `${accountLabel(account.account)} — emitted mol: ${inventoryText}`;
      const ariaQuantity = visibleTotal === account.total
        ? `${fmtNum(account.total, "mol")} account display sum`
        : `${fmtNum(visibleTotal, "mol")} ribbon of ${fmtNum(account.total, "mol")} account display sum`;
      ribbon = `<a class="sec-p14-ribbon" href="#${esc(detailId)}" data-p14-account="${esc(account.account)}"`
        + ` style="--sec-p14-width:${width.toFixed(5)}%;--sec-p14-ribbon:linear-gradient(90deg,${esc(background)})"`
        + ` title="${esc(hover)}" aria-label="${esc(`${accountLabel(account.account)}, ${ariaQuantity}, mol basis`)}"></a>`;
      if (account.total < 0 || account.entries.some((entry) => isFiniteNumber(entry.value) && entry.value < 0)) {
        status = `<span class="sec-p14-non-ribbon">signed negative components excluded from ribbon width</span>`;
      } else if (groups.trace.length) {
        status = `<span class="sec-p14-trace">${groups.trace.length} species merged into global trace node</span>`;
      }
    } else if (groups.trace.length) {
      status = `<span class="sec-p14-trace">positive inventory merged into global trace node</span>`;
    } else if (account.total < 0) {
      status = `<span class="sec-p14-signed">signed reservoir credit balance · no Sankey width</span>`;
    } else if (!account.entries.length) {
      status = `<span class="sec-p14-empty">emitted empty account · no ribbon</span>`;
    } else {
      status = `<span class="sec-p14-empty">emitted zero inventory · no ribbon</span>`;
    }
    const total = account.total === null ? "account display sum pending" : `${fmtNum(account.total, "mol")} · viewer display sum`;
    return `<div class="sec-p14-row"><div class="sec-p14-track">${ribbon}</div>`
      + `<div class="sec-p14-destination"><a href="#${esc(detailId)}" data-p14-account="${esc(account.account)}">${esc(accountLabel(account.account))}</a>`
      + `<span>${esc(total)}</span>${status}</div></div>`;
  }

  function traceRow(members, scale, largestVisible) {
    if (!members.length) return "";
    const detailId = "sec-p14-trace-inventory";
    const total = members.reduce((sum, member) => sum + member.value, 0);
    const speciesCount = new Set(members.map((member) => member.name)).size;
    const widthRatio = total / largestVisible;
    const width = (scale === "sqrt" ? Math.sqrt(widthRatio) : widthRatio) * 100;
    const background = ribbonBackground({ major: members, trace: [] }, total);
    const memberText = members.map((member) => `${accountLabel(member.account)} · ${prettySpecies(member.name)} ${fmtNum(member.value, "mol")}`).join("; ");
    const tracePercent = TRACE_FRACTION * 100;
    return `<div class="sec-p14-row sec-p14-trace-node"><div class="sec-p14-track">`
      + `<a class="sec-p14-ribbon" href="#${detailId}" data-p14-trace="true"`
      + ` style="--sec-p14-width:${width.toFixed(5)}%;--sec-p14-ribbon:linear-gradient(90deg,${esc(background)})"`
      + ` title="${esc(`trace inventory members — ${memberText}`)}" aria-label="${esc(`trace inventory, ${speciesCount} species, ${fmtNum(total, "mol")}, mol basis`)}"></a></div>`
      + `<div class="sec-p14-destination"><a href="#${detailId}" data-p14-trace="true">trace inventory (${speciesCount} species)</a>`
      + `<span>${molText(total)} · viewer-clustered display node</span><span>each member &lt; ${esc(tracePercent)}% of displayed terminal mol</span></div></div>`;
  }

  function traceDetail(members) {
    if (!members.length) return "";
    const speciesCount = new Set(members.map((member) => member.name)).size;
    const rows = members.map((member) => `<tr><td>${esc(accountLabel(member.account))}</td>`
      + `<td><code>${esc(member.account)}</code></td><td>${esc(prettySpecies(member.name))}</td>`
      + `<td class="num">${molText(member.value)}</td></tr>`).join("");
    return `<details class="sec-p14-account-detail" id="sec-p14-trace-inventory"><summary>trace inventory (${speciesCount} species) · emitted members</summary>`
      + `<div class="sec-p14-table-wrap"><table><thead><tr><th>Account</th><th>Literal key</th><th>Species</th><th class="num">Emitted mol</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div></details>`;
  }

  function render(artifact) {
    const terminal = isObjectMap(artifact?.terminal) ? artifact.terminal : {};
    if (!own(terminal, "final_state")) {
      return `<section class="sec-p14-sankey" id="${PANEL_ID}"><h2><span class="sect">14</span>Terminal account-inventory distribution</h2>`
        + `<p class="sub">Terminal snapshot only · mol basis · not process movement, feedstock provenance, yield, or a conservation claim.</p>`
        + `<div class="pending sec-p14-pending"><strong>Pending terminal inventory</strong><p>terminal.final_state is not emitted for this run.</p></div>`
        + `${provenanceTier(terminal)}</section>`;
    }
    const finalState = terminal.final_state;
    if (!isObjectMap(finalState)) {
      return `<section class="sec-p14-sankey" id="${PANEL_ID}"><h2><span class="sect">14</span>Terminal account-inventory distribution</h2>`
        + `<p class="sub">Terminal snapshot only · mol basis · not process movement, feedstock provenance, yield, or a conservation claim.</p>`
        + `<div class="pending sec-p14-pending"><strong>Malformed terminal inventory</strong><p>terminal.final_state is present but is not an account map.</p></div>`
        + `${provenanceTier(terminal)}</section>`;
    }
    const rawAccounts = orderedAccounts(finalState);
    if (!rawAccounts.length) {
      return `<section class="sec-p14-sankey" id="${PANEL_ID}"><h2><span class="sect">14</span>Terminal account-inventory distribution</h2>`
        + `<p class="sub">Terminal snapshot only · mol basis · not process movement, feedstock provenance, yield, or a conservation claim.</p>`
        + `<div class="pending sec-p14-pending"><strong>Empty terminal inventory</strong><p>terminal.final_state was emitted with no account keys.</p></div>`
        + `${provenanceTier(terminal)}</section>`;
    }

    const accounts = rawAccounts.map(({ account, species }) => accountData(account, species));
    const displayTotalComplete = accounts.every((account) => !account.malformed);
    const displayTotal = displayTotalComplete
      ? accounts.reduce((sum, account) => sum + account.total, 0)
      : null;
    const cutoff = displayTotal !== null && displayTotal > 0 ? displayTotal * TRACE_FRACTION : null;
    const groupsByAccount = accounts.map((account) => traceGroups(account, cutoff));
    const traceMembers = accounts.flatMap((account, index) => groupsByAccount[index].trace.map((entry) => ({
      account: account.account, name: entry.name, value: entry.value
    })));
    const visibleTotals = groupsByAccount.map((groups) => groups.major.reduce((sum, entry) => sum + entry.value, 0)).filter((value) => value > 0);
    const traceTotal = traceMembers.reduce((sum, member) => sum + member.value, 0);
    if (traceTotal > 0) visibleTotals.push(traceTotal);
    const largestVisible = visibleTotals.length ? Math.max(...visibleTotals) : 1;
    const visibleMedian = median(visibleTotals);
    const sqrtScaled = visibleMedian !== null && largestVisible > SQRT_SCALE_RATIO * visibleMedian;
    const scale = sqrtScaled ? "sqrt" : "linear";
    const rows = accounts.map((account, index) => accountRow(account, groupsByAccount[index], scale, largestVisible)).join("")
      + traceRow(traceMembers, scale, largestVisible);
    const details = accounts.map((account, index) => accountDetail(account, groupsByAccount[index])).join("")
      + traceDetail(traceMembers);
    const scaleBadge = sqrtScaled
      ? `<span class="sec-p14-badge sec-p14-scale">widths √-scaled for readability — hover for true mol</span>`
      : `<span class="sec-p14-badge">linear ribbon widths</span>`;
    const totalText = displayTotal === null ? "pending · incomplete numeric account map" : fmtNum(displayTotal, "mol");
    const signed = accounts.some((account) => account.entries.some((entry) => isFiniteNumber(entry.value) && entry.value < 0));
    const signedNote = signed
      ? `<div class="note sec-p14-note">Signed reservoir credit balances are included in the displayed Σ, but have no Sankey width. Positive ribbons are not expected to close to that signed display total.</div>`
      : "";

    return `<section class="sec-p14-sankey" id="${PANEL_ID}"><h2><span class="sect">14</span>Terminal account-inventory distribution</h2>`
      + `<p class="sub">Terminal snapshot only · mol basis · account disposition at termination, not process movement, feedstock provenance, yield, charge, or molecule-mol conservation.</p>`
      + `<div class="sec-p14-badges"><span class="sec-p14-badge">mol basis</span>${scaleBadge}</div>`
      + `<div class="sec-p14-flow"><div class="sec-p14-source"><strong>terminal inventory total (Σ accounts, mol — display total, not charge)</strong>`
      + `<span>${esc(totalText)}</span><small>Viewer display sum of emitted numeric species. kg-projected tier pending — backend kg projection not emitted.</small></div>`
      + `<div class="sec-p14-accounts">${rows}</div></div>${signedNote}`
      + `<div class="note sec-p14-note">${availabilityNote(finalState)}</div>`
      + `<details class="sec-p14-ledger"><summary>Underlying emitted account ledger · mol basis</summary>${details}</details>`
      + `${provenanceTier(terminal)}</section>`;
  }

  function installAccountLinks() {
    if (typeof document === "undefined" || !document.addEventListener || root.__ReportPanelP14Links) return;
    root.__ReportPanelP14Links = true;
    document.addEventListener("click", (event) => {
      const trigger = event.target?.closest?.("[data-p14-account], [data-p14-trace]");
      if (!trigger) return;
      const account = trigger.getAttribute("data-p14-account");
      const href = trigger.getAttribute("href") || "";
      const targetId = href.startsWith("#") ? href.slice(1) : "";
      const localDetail = targetId ? document.getElementById(targetId) : null;
      const sharedRows = [...document.querySelectorAll(".disposition-group tbody tr")];
      const sharedRow = sharedRows.find((row) => [...row.querySelectorAll("span[title]")]
        .some((span) => span.getAttribute("title") === account));
      if (sharedRow && targetId) {
        if (localDetail) localDetail.removeAttribute("id");
        sharedRow.id = targetId;
        sharedRow.setAttribute("tabindex", "-1");
        sharedRow.scrollIntoView?.({ block: "center" });
        sharedRow.focus?.({ preventScroll: true });
      } else if (localDetail) {
        const ledgerDisclosure = localDetail.closest?.("details.sec-p14-ledger");
        if (ledgerDisclosure) ledgerDisclosure.open = true;
        localDetail.open = true;
        localDetail.scrollIntoView?.({ block: "center" });
      }
    });
  }

  installAccountLinks();
  (root.ReportPanels = root.ReportPanels || []).push({ id: PANEL_ID, render });
}(globalThis));
