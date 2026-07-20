from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _render_panel(
    artifact: object,
    *,
    click_account: str = "",
    shared_row: bool = True,
    species_colors: dict[str, str] | None = None,
) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const panelSource = fs.readFileSync(process.argv[3], "utf8");
const artifact = JSON.parse(process.argv[4]);
const clickAccount = process.argv[5];
const options = JSON.parse(process.argv[6]);
let clickHandler = null;
const ledgerDisclosure = { open: false };
const localDetail = {
  open: false, removedId: false, scrolled: 0,
  removeAttribute(name) { if (name === "id") this.removedId = true; },
  scrollIntoView() { this.scrolled += 1; },
  closest(selector) { return selector === "details.sec-p14-ledger" ? ledgerDisclosure : null; }
};
const accountSpan = { getAttribute(name) { return name === "title" ? clickAccount : null; } };
const sharedRow = {
  id: "", attributes: {}, scrolled: 0, focused: 0,
  querySelectorAll(selector) { return selector === "span[title]" ? [accountSpan] : []; },
  setAttribute(name, value) { this.attributes[name] = String(value); },
  scrollIntoView() { this.scrolled += 1; },
  focus() { this.focused += 1; }
};
const context = {
  console,
  document: {
    addEventListener(type, handler) { if (type === "click") clickHandler = handler; },
    getElementById() { return localDetail; },
    querySelectorAll(selector) {
      return selector === ".disposition-group tbody tr" && clickAccount && options.sharedRow ? [sharedRow] : [];
    }
  }
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(labelsSource, context);
const sharedSpeciesColor = context.ReportLabels.speciesColor;
context.ReportLabels = {
  ...context.ReportLabels,
  speciesColor(species) {
    return Object.prototype.hasOwnProperty.call(options.speciesColors, species)
      ? options.speciesColors[species]
      : sharedSpeciesColor(species);
  }
};
vm.runInContext(panelSource, context);
const panel = context.ReportPanels[0];
const html = panel.render(artifact, [], [], {});
if (clickAccount && clickHandler) {
  const marker = `data-p14-account="${clickAccount}"`;
  const markerIndex = html.indexOf(marker);
  const prefix = markerIndex >= 0 ? html.slice(Math.max(0, markerIndex - 400), markerIndex) : "";
  const hrefs = [...prefix.matchAll(/href="([^"]+)"/g)];
  const href = hrefs.length ? hrefs[hrefs.length - 1][1] : "";
  const trigger = {
    getAttribute(name) {
      if (name === "data-p14-account") return clickAccount;
      if (name === "href") return href;
      return null;
    }
  };
  clickHandler({ target: { closest() { return trigger; } } });
}
process.stdout.write(JSON.stringify({
  id: panel.id,
  panelCount: context.ReportPanels.length,
  html,
  interaction: {
    handlerInstalled: Boolean(clickHandler),
    rowId: sharedRow.id,
    rowScrolled: sharedRow.scrolled,
    rowFocused: sharedRow.focused,
    rowTabIndex: sharedRow.attributes.tabindex || null,
    localIdRemoved: localDetail.removedId,
    localOpen: localDetail.open,
    localScrolled: localDetail.scrolled,
    ledgerOpen: ledgerDisclosure.open
  }
}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p14-sankey.js"),
            json.dumps(artifact),
            click_account,
            json.dumps(
                {
                    "sharedRow": shared_row,
                    "speciesColors": species_colors or {},
                }
            ),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _artifact(terminal: dict) -> dict:
    return {"terminal": terminal}


def _html_region(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    end = html.index(end_marker, start) + len(end_marker)
    return html[start:end]


def _html_region_containing(
    html: str,
    marker: str,
    start_marker: str,
    end_marker: str,
) -> str:
    marker_index = html.index(marker)
    start = html.rfind(start_marker, 0, marker_index)
    if start < 0:
        raise AssertionError(f"missing region start {start_marker!r} before {marker!r}")
    end = html.index(end_marker, marker_index) + len(end_marker)
    return html[start:end]


def _account_row(html: str, account: str) -> str:
    return _html_region_containing(
        html,
        f'data-p14-account="{account}"',
        '<div class="sec-p14-row">',
        "</div></div>",
    )


def _source_region(html: str) -> str:
    return _html_region(html, '<div class="sec-p14-source">', "</div>")


def _provenance_region(html: str) -> str:
    return _html_region(html, '<details class="sec-p14-provenance">', "</details>")


def _trace_row(html: str) -> str:
    return _html_region(
        html,
        '<div class="sec-p14-row sec-p14-trace-node">',
        "</div></div>",
    )


def _account_detail(html: str, account: str) -> str:
    return _html_region_containing(
        html,
        f"<code>{account}</code>",
        '<details class="sec-p14-account-detail"',
        "</details>",
    )


def _ribbon_width(region: str) -> float:
    match = re.search(r"--sec-p14-width:([0-9.]+)%", region)
    if match is None:
        raise AssertionError("missing rendered ribbon width")
    return float(match.group(1))


def _rendered_account_keys(html: str) -> set[str]:
    return set(re.findall(r'data-p14-account="([^"]+)"', html))


def _section(html: str) -> str:
    return _html_region(html, '<section class="sec-p14-sankey"', "</section>")


def _destination_blocks(html: str) -> list[str]:
    return re.findall(
        r'<div class="sec-p14-destination">(.*?)</div>',
        html,
        flags=re.DOTALL,
    )


def _destination_account_keys(html: str) -> list[str]:
    keys: list[str] = []
    for block in _destination_blocks(html):
        match = re.search(r'data-p14-account="([^"]+)"', block)
        if match:
            keys.append(match.group(1))
        elif 'data-p14-trace="true"' not in block:
            keys.append(f"__unmarked__:{block[:80]}")
    return keys


def _css_selector_arms(css_text: str) -> list[str]:
    """Return non-at-rule selector arms from the panel CSS (including @media bodies)."""
    without_comments = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
    arms: list[str] = []
    for match in re.finditer(r"([^{}]+)\{", without_comments):
        selector = match.group(1).strip()
        if not selector or selector.startswith("@"):
            continue
        for arm in selector.split(","):
            arm = arm.strip()
            if arm:
                arms.append(arm)
    return arms


NUMERIC_KG = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?\s*kg\b",
    re.IGNORECASE,
)
NUMERIC_MOL = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?\s*mol\b",
    re.IGNORECASE,
)

SECTION_TITLE = "Terminal account-inventory distribution"
SECTION_SUBTITLE = (
    "Terminal snapshot only · mol basis · account disposition at termination, "
    "not process movement, feedstock provenance, yield, charge, or molecule-mol conservation."
)


def test_p14_renders_emitted_accounts_trace_scaling_o2_and_interaction() -> None:
    final_state = {
        "process.cleaned_melt": {"SiO2": 10_000.0},
        "process.metal_phase_bottom_pool": {"Fe": 100.0, "Cr": 1.0},
        "terminal.oxygen_melt_offgas_stored": {"O2": 1.0},
        "terminal.oxygen_mre_anode_stored": {"O2": 2.0},
        "terminal.offgas": {"Na": 3.0},
    }
    state = _render_panel(
        _artifact({"final_state": final_state}),
        click_account="terminal.offgas",
        species_colors={"Fe": "#13579b", "O2": "#2468ac"},
    )
    html = state["html"]
    section = _section(html)
    title = _html_region(html, "<h2>", "</h2>")
    subtitle = _html_region(html, '<p class="sub">', "</p>")
    offgas_row = _account_row(html, "terminal.offgas")
    offgas_href = re.search(r'href="#(sec-p14-account-terminal-offgas-[^"]+)"', offgas_row)
    assert offgas_href is not None
    expected_row_id = offgas_href.group(1)

    assert state["id"] == "sec-p14-sankey"
    assert state["panelCount"] == 1
    assert f'<span class="sect">14</span>{SECTION_TITLE}' in title
    assert SECTION_SUBTITLE in subtitle
    assert "Recovered product" not in section
    assert re.search(r"\brecovered\b", section, re.IGNORECASE) is None
    assert "product flow" not in section.lower()
    assert "terminal inventory total (Σ accounts, mol — display total, not charge)" in html
    assert "widths √-scaled for readability — hover for true mol" in html
    assert 'class="sec-p14-row sec-p14-trace-node"' in html
    assert "trace inventory (2 species)" in html
    trace_row = _trace_row(html)
    trace_detail = _html_region(
        html,
        '<details class="sec-p14-account-detail" id="sec-p14-trace-inventory">',
        "</details>",
    )
    assert (
        'title="trace inventory members — Metal pool (bottom) · Cr 1 mol; '
        'Offgas · Na 3 mol"'
    ) in trace_row
    assert "<span>4 mol · viewer-clustered display node</span>" in trace_row
    assert 'aria-label="trace inventory, 2 species, 4 mol, mol basis"' in trace_row
    assert '<th class="num">Emitted mol</th>' in trace_detail
    assert NUMERIC_KG.search(html) is None
    assert "O₂ stored · melt offgas" in html
    assert "O₂ stored · MRE anode" in html
    assert html.count('data-p14-account="terminal.oxygen_melt_offgas_stored"') >= 2
    assert html.count('data-p14-account="terminal.oxygen_mre_anode_stored"') >= 2
    expected_keys = set(final_state)
    assert _rendered_account_keys(html) == expected_keys
    assert set(_destination_account_keys(html)) == expected_keys
    assert not any(key.startswith("__unmarked__:") for key in _destination_account_keys(html))
    for forbidden in ("glass", "rump", "ceramic", "Stage 3", "per-stage condenser"):
        assert forbidden.lower() not in " ".join(_destination_blocks(html)).lower()
    assert html.index('data-p14-account="process.metal_phase_bottom_pool"') < html.index(
        'data-p14-account="terminal.oxygen_melt_offgas_stored"'
    ) < html.index('data-p14-account="terminal.oxygen_mre_anode_stored"') < html.index(
        'data-p14-account="terminal.offgas"'
    ) < html.index('data-p14-account="process.cleaned_melt"')
    assert "#13579b" in _account_row(html, "process.metal_phase_bottom_pool")
    assert "#2468ac" in _account_row(html, "terminal.oxygen_melt_offgas_stored")
    assert "kg-projected tier pending — backend kg projection not emitted" in html
    assert state["interaction"] == {
        "handlerInstalled": True,
        "rowId": expected_row_id,
        "rowScrolled": 1,
        "rowFocused": 1,
        "rowTabIndex": "-1",
        "localIdRemoved": True,
        "localOpen": False,
        "localScrolled": 0,
        "ledgerOpen": False,
    }
    assert state["interaction"]["rowId"].startswith("sec-p14-account-terminal-offgas-")


def test_p14_distinct_o2_disposition_accounts_are_never_trace_clustered() -> None:
    o2_accounts = {
        "terminal.oxygen_stage0_stored": (1.0, "O₂ stored · Stage 0"),
        "terminal.oxygen_melt_offgas_stored": (2.0, "O₂ stored · melt offgas"),
        "terminal.oxygen_melt_offgas_vented_to_vacuum": (3.0, "O₂ vented · melt offgas"),
        "terminal.oxygen_bubbler_external_vented_to_vacuum": (4.0, "O₂ vented · bubbler"),
        "terminal.oxygen_melt_offgas_captured": (5.0, "O₂ captured · melt offgas"),
        "terminal.oxygen_mre_anode_stored": (6.0, "O₂ stored · MRE anode"),
        "reservoir.oxygen_cistern_liquid_inventory": (7.0, "O₂ cistern (LOX)"),
    }
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    **{account: {"O2": value} for account, (value, _) in o2_accounts.items()},
                }
            }
        )
    )["html"]

    for account, (value, label) in o2_accounts.items():
        row = _account_row(html, account)
        assert row.count(f'data-p14-account="{account}"') == 2
        assert 'class="sec-p14-ribbon"' in row
        assert "merged into global trace node" not in row
        assert f'>{label}</a><span>{value:g} mol · viewer display sum</span>' in row
    assert 'class="sec-p14-row sec-p14-trace-node"' not in html


def test_p14_basis_badge_and_source_total_stay_mol_native() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "terminal.offgas": {"Fe": 2.375},
                    "process.cleaned_melt": {"SiO2": 7.125},
                }
            }
        )
    )["html"]
    badges = _html_region(html, '<div class="sec-p14-badges">', "</div>")
    source = _source_region(html)
    offgas_row = _account_row(html, "terminal.offgas")
    cleaned_melt_row = _account_row(html, "process.cleaned_melt")
    offgas_detail = _account_detail(html, "terminal.offgas")
    subtitle = _html_region(html, '<p class="sub">', "</p>")
    title = _html_region(html, "<h2>", "</h2>")
    ledger = _html_region(html, '<details class="sec-p14-ledger">', "</details>")

    assert f'<span class="sect">14</span>{SECTION_TITLE}' in title
    assert SECTION_SUBTITLE in subtitle
    assert '<span class="sec-p14-badge">mol basis</span>' in badges
    assert "kg basis" not in badges
    assert "<span>9.5 mol</span>" in source
    assert "2.375 mol · viewer display sum" in offgas_row
    assert "7.125 mol · viewer display sum" in cleaned_melt_row
    assert "Offgas, 2.375 mol account display sum, mol basis" in offgas_row
    assert "Offgas — emitted mol: Fe 2.375 mol" in offgas_row
    assert "kg basis" not in subtitle
    assert "<summary>Underlying emitted account ledger · mol basis</summary>" in ledger
    assert "emitted basis: mol by species" in offgas_detail
    assert '<th class="num">Emitted mol</th>' in offgas_detail
    assert 'Fe</td><td class="num">2.375 mol' in offgas_detail
    assert NUMERIC_KG.search(html) is None


def test_p14_ribbon_widths_follow_sqrt_and_linear_scale_badges() -> None:
    sqrt_html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "process.metal_phase_bottom_pool": {"Fe": 100.0},
                    "terminal.offgas": {"Fe": 100.0, "Na": 1.0},
                }
            }
        )
    )["html"]

    assert "widths √-scaled for readability — hover for true mol" in sqrt_html
    assert _ribbon_width(_account_row(sqrt_html, "process.cleaned_melt")) == 100.0
    assert _ribbon_width(_account_row(sqrt_html, "process.metal_phase_bottom_pool")) == 10.0
    assert _ribbon_width(_account_row(sqrt_html, "terminal.offgas")) == 10.0
    assert _ribbon_width(_trace_row(sqrt_html)) == 1.0

    linear_html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 100.0},
                    "terminal.offgas": {"Fe": 25.0},
                }
            }
        )
    )["html"]

    assert "linear ribbon widths" in linear_html
    assert _ribbon_width(_account_row(linear_html, "process.cleaned_melt")) == 100.0
    assert _ribbon_width(_account_row(linear_html, "terminal.offgas")) == 25.0


def test_p14_partial_trace_aria_names_ribbon_and_full_account_sum() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "terminal.offgas": {
                        "Fe": 1_000.0,
                        **{f"trace-{index}": 6.0 for index in range(40)},
                    },
                }
            }
        )
    )["html"]
    row = _account_row(html, "terminal.offgas")

    assert "1,240 mol · viewer display sum" in row
    assert "40 species merged into global trace node" in row
    assert (
        'aria-label="Offgas, 1,000 mol ribbon of 1,240 mol account display sum, mol basis"'
    ) in row


def test_p14_mixed_trace_tooltip_lists_each_species_once() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "terminal.offgas": {"Fe": 100.0, "Na": 0.1},
                }
            }
        ),
        species_colors={"Na": "#n4trace"},
    )["html"]
    offgas_row = _account_row(html, "terminal.offgas")
    trace_row = _trace_row(html)

    assert 'title="Offgas — emitted mol: Fe 100 mol; Na 0.1 mol"' in offgas_row
    assert offgas_row.count("Na 0.1 mol") == 1
    # Trace-clustered Na must still route through speciesColor() (not a hard-coded black).
    assert "#n4trace" in trace_row
    assert 'title="trace inventory members — Offgas · Na 0.1 mol"' in trace_row


def test_p14_trace_disclosure_uses_active_threshold() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "terminal.offgas": {"Fe": 51.0, "Na": 49.0},
                }
            }
        )
    )["html"]
    trace_row = _trace_row(html)
    offgas_row = _account_row(html, "terminal.offgas")

    assert "each member &lt; 0.5% of displayed terminal mol" in trace_row
    assert 'title="trace inventory members — Offgas · Na 49 mol"' in trace_row
    assert "Fe 51 mol" not in trace_row
    assert "1 species merged into global trace node" in offgas_row
    assert "51 mol ribbon of 100 mol account display sum" in offgas_row


def test_p14_trace_threshold_boundary_member_stays_major_only() -> None:
    """Exact 0.5% of display total is major (>= cutoff), never dual-classified as trace."""
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 99.5},
                    "terminal.offgas": {"Na": 0.5},
                }
            }
        )
    )["html"]
    offgas_row = _account_row(html, "terminal.offgas")

    assert "0.5 mol · viewer display sum" in offgas_row
    assert 'class="sec-p14-ribbon"' in offgas_row
    assert "merged into global trace node" not in offgas_row
    assert 'class="sec-p14-row sec-p14-trace-node"' not in html
    assert "trace inventory" not in html


def test_p14_absent_final_state_and_provenance_stay_pending() -> None:
    html = _render_panel(_artifact({}))["html"]
    section = _section(html)
    pending = _html_region(
        html,
        '<div class="pending sec-p14-pending">',
        "</div>",
    )
    title = _html_region(html, "<h2>", "</h2>")
    subtitle = _html_region(html, '<p class="sub">', "</p>")

    assert f'<span class="sect">14</span>{SECTION_TITLE}' in title
    assert SECTION_SUBTITLE in subtitle
    assert "Pending terminal inventory" in pending
    assert "terminal.final_state is not emitted for this run." in pending
    assert "feedstock-provenance tier pending (origin data not emitted for this run)." in html
    assert re.search(r"\b0(?:\.0+)?\s*mol\b", pending) is None
    # No measured mol quantity anywhere in the missing-state section (not only in the pending div).
    assert NUMERIC_MOL.search(section) is None
    assert "sec-p14-ribbon" not in html
    assert "sec-p14-accounts" not in html


def test_p14_distinguishes_empty_and_malformed_final_state() -> None:
    empty_html = _render_panel(_artifact({"final_state": {}}))["html"]
    empty_region = _html_region(
        empty_html,
        '<div class="pending sec-p14-pending">',
        "</div>",
    )
    assert "Empty terminal inventory" in empty_region
    assert "terminal.final_state was emitted with no account keys." in empty_region
    assert NUMERIC_MOL.search(empty_region) is None
    assert "sec-p14-ribbon" not in empty_html

    for malformed in (None, [], "not-an-account-map", 0):
        malformed_html = _render_panel(_artifact({"final_state": malformed}))["html"]
        malformed_region = _html_region(
            malformed_html,
            '<div class="pending sec-p14-pending">',
            "</div>",
        )
        assert "Malformed terminal inventory" in malformed_region
        assert "present but is not an account map" in malformed_region
        assert NUMERIC_MOL.search(malformed_region) is None
        assert "sec-p14-ribbon" not in malformed_html


def test_p14_malformed_account_values_never_claim_empty_mol_inventory() -> None:
    for malformed in (None, [], "not-a-species-map", 0, {"Fe": 5.0, "Si": "7"}):
        html = _render_panel(
            _artifact({"final_state": {"terminal.offgas": malformed}})
        )["html"]
        row = _account_row(html, "terminal.offgas")
        detail = _account_detail(html, "terminal.offgas")

        assert "account display sum pending" in row
        assert "width pending · malformed species map" in row
        assert "malformed species map · values pending" in detail
        assert "emitted basis: mol by species" not in detail
        assert "Emitted mol" not in detail
        assert "emitted empty account" not in detail


def test_p14_present_provenance_surfaces_all_authority_values() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {"terminal.offgas": {"Na": 5.0}},
                "yield_disposition": {
                    "basis": "target_atom_equivalent",
                    "authoritative": False,
                    "diagnostic_only": True,
                    "extrapolation": "tail<fit>",
                    "high_uncertainty": True,
                    "status": "partial<state>",
                    "source": "<untrusted source>",
                    "reference": "ref&catalog",
                    "skip_reason": "coverage < floor",
                },
            }
        )
    )["html"]
    provenance = _provenance_region(html)

    assert "yield_disposition emitted · provenance schema check" in provenance
    assert "basis</b> target_atom_equivalent" in provenance
    assert "authoritative</b> false" in provenance
    assert "diagnostic only</b> true" in provenance
    assert "extrapolation</b> tail&lt;fit&gt;" in provenance
    assert "high uncertainty</b> true" in provenance
    assert "status</b> partial&lt;state&gt;" in provenance
    assert "source</b> &lt;untrusted source&gt;" in provenance
    assert "reference</b> ref&amp;catalog" in provenance
    assert "skip reason</b> coverage &lt; floor" in provenance


def test_p14_structured_authority_values_preserve_emitted_content() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {"terminal.offgas": {"Na": 5.0}},
                "yield_disposition": {
                    "basis": {"unit": "mol", "scope": "target"},
                    "authoritative": False,
                    "source": {"provider": "kernel", "version": "7"},
                    "reference": {"doi": "10.1/example"},
                    "skip_reason": {"code": "coverage", "detail": "floor<limit>"},
                },
            }
        )
    )["html"]
    provenance = _provenance_region(html)

    assert (
        "basis</b> {&quot;unit&quot;:&quot;mol&quot;,&quot;scope&quot;:&quot;target&quot;}"
    ) in provenance
    assert "authoritative</b> false" in provenance
    assert (
        "source</b> {&quot;provider&quot;:&quot;kernel&quot;,&quot;version&quot;:&quot;7&quot;}"
    ) in provenance
    assert "reference</b> {&quot;doi&quot;:&quot;10.1/example&quot;}" in provenance
    assert (
        "skip reason</b> {&quot;code&quot;:&quot;coverage&quot;,&quot;detail&quot;:"
        "&quot;floor&lt;limit&gt;&quot;}"
    ) in provenance
    assert "structured value" not in provenance


def test_p14_provenance_partial_path_does_not_derive_shares() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {"terminal.offgas": {"Na": 5.0}},
                "yield_disposition": {
                    "basis": "target_atom_equivalent",
                    "targets": {
                        "Fe": {
                            "denominator_target_equiv_mol": 2.0,
                            "yield_fraction": 0.3141592653,
                        }
                    },
                },
            }
        )
    )["html"]
    provenance = _provenance_region(html)

    assert "Pending origin-resolved shares" in provenance
    assert "No feedstock percentages are inferred" in provenance
    assert "0.3141592653" not in provenance
    assert "31.4159" not in provenance
    assert "0.3142" not in provenance
    assert "31.42" not in provenance
    assert provenance == (
        '<details class="sec-p14-provenance"><summary>yield_disposition emitted · '
        'provenance schema check</summary><div class="sec-p14-flags"><span '
        'class="sec-p14-flag"><b>basis</b> target_atom_equivalent</span></div>'
        '<div class="pending sec-p14-provenance-pending"><strong>Pending '
        'origin-resolved shares</strong><p>This payload does not expose a '
        'producer-defined chart-ready origin-to-account link schema. No feedstock '
        'percentages are inferred from target fractions or terminal mol inventories.'
        '</p></div></details>'
    )


def test_p14_artifact_text_is_escaped_exactly_once() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "process.<b>acct&raw</b>": {
                        "Fe<script>&raw</script>": 2.0,
                    }
                },
                "yield_disposition": {"basis": "<basis&raw>"},
            }
        )
    )["html"]
    provenance = _provenance_region(html)
    account_detail = _html_region_containing(
        html,
        "<code>process.&lt;b&gt;acct&amp;raw&lt;/b&gt;</code>",
        '<details class="sec-p14-account-detail"',
        "</details>",
    )
    trace_row = _html_region(
        html,
        '<div class="sec-p14-row sec-p14-trace-node">',
        "</div></div>",
    )
    trace_detail = _html_region(
        html,
        '<details class="sec-p14-account-detail" id="sec-p14-trace-inventory">',
        "</details>",
    )

    assert "basis</b> &lt;basis&amp;raw&gt;" in provenance
    assert "process.&lt;b&gt;acct&amp;raw&lt;/b&gt;" in account_detail
    assert "Fe&lt;script&gt;&amp;raw&lt;/script&gt;" in account_detail
    assert "&lt;B&gt;Acct&amp;Raw&lt;/B&gt; · Fe&lt;script&gt;&amp;raw&lt;/script&gt; 2 mol" in trace_row
    assert "&lt;B&gt;Acct&amp;Raw&lt;/B&gt;" in trace_detail
    assert "<code>process.&lt;b&gt;acct&amp;raw&lt;/b&gt;</code>" in trace_detail
    assert "Fe&lt;script&gt;&amp;raw&lt;/script&gt;" in trace_detail
    assert "<basis&raw>" not in html
    assert "<b>acct&raw</b>" not in html
    assert "<B>Acct&Raw</B>" not in html
    assert "<script>&raw</script>" not in html
    assert "&amp;lt;" not in html
    assert "&amp;amp;" not in html


def test_p14_sparse_note_names_only_emitted_standins() -> None:
    final_state = {"terminal.offgas": {"Na": 5.0}}
    html = _render_panel(_artifact({"final_state": final_state}))["html"]
    note = _html_region(
        html,
        '<div class="note sec-p14-note sec-p14-availability-note">',
        "</div>",
    )
    destinations = " ".join(_destination_blocks(html))

    assert "destinations are not inferred when their account keys are absent" in note
    assert "No aggregate condensation-train or cleaned-melt stand-in account is emitted" in note
    assert "remain the available terminal accounts" not in note
    assert "Stage 3 glass" not in destinations
    assert "glass" not in destinations.lower()
    assert "rump" not in destinations.lower()
    assert "ceramic" not in destinations.lower()
    assert set(_destination_account_keys(html)) == set(final_state)
    assert not any("0 mol" in block and "terminal.offgas" not in block for block in _destination_blocks(html))


def test_p14_local_deep_link_fallback_opens_ledger_detail() -> None:
    state = _render_panel(
        _artifact({"final_state": {"terminal.offgas": {"Na": 5.0}}}),
        click_account="terminal.offgas",
        shared_row=False,
    )

    assert 'data-p14-account="terminal.offgas"' in _account_row(
        state["html"], "terminal.offgas"
    )
    assert state["interaction"] == {
        "handlerInstalled": True,
        "rowId": "",
        "rowScrolled": 0,
        "rowFocused": 0,
        "rowTabIndex": None,
        "localIdRemoved": False,
        "localOpen": True,
        "localScrolled": 1,
        "ledgerOpen": True,
    }


def test_p14_mol_map_does_not_derive_kg_projection() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "terminal.oxygen_mre_anode_stored": {"O2": 2.0},
                }
            }
        )
    )["html"]
    source = _source_region(html)

    assert "kg-projected tier pending — backend kg projection not emitted" in source
    assert NUMERIC_KG.search(html) is None


def test_p14_partial_numeric_map_does_not_derive_totals_or_widths() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "terminal.offgas": {"Fe": 5.0, "Si": "7"},
                    "process.cleaned_melt": {"MgO": 2.0},
                }
            }
        )
    )["html"]
    source = _source_region(html)
    offgas_row = _account_row(html, "terminal.offgas")
    cleaned_melt_row = _account_row(html, "process.cleaned_melt")
    offgas_detail = _account_detail(html, "terminal.offgas")

    assert "pending · incomplete numeric account map" in source
    assert NUMERIC_MOL.search(source) is None
    assert "account display sum pending" in offgas_row
    assert "width pending · malformed species map" in offgas_row
    assert 'class="sec-p14-ribbon"' not in offgas_row
    assert 'class="sec-p14-ribbon"' in cleaned_melt_row
    assert "malformed species map · values pending" in offgas_detail
    assert "Emitted mol" not in offgas_detail
    assert "Fe 5 mol" not in offgas_detail
    assert "Si 7 mol" not in html


def test_p14_zero_and_signed_credit_accounts_are_not_trace_or_ribbons() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "reservoir.reagent.Na": {"Na": -8.0},
                    "process.metal_phase": {},
                    "terminal.offgas": {"Na": 0.0},
                }
            }
        )
    )["html"]
    empty_row = _account_row(html, "process.metal_phase")
    zero_row = _account_row(html, "terminal.offgas")
    reservoir_row = _account_row(html, "reservoir.reagent.Na")
    credit_note = _html_region(
        html,
        '<div class="note sec-p14-note sec-p14-credit-note">',
        "</div>",
    )

    assert "-8 mol · viewer display sum" in reservoir_row
    assert "signed reservoir credit balance · no Sankey width" in reservoir_row
    assert "emitted empty account · no ribbon" in empty_row
    assert "emitted zero inventory · no ribbon" not in empty_row
    assert "emitted zero inventory · no ribbon" in zero_row
    assert "emitted empty account · no ribbon" not in zero_row
    assert "trace inventory" not in html
    assert 'class="sec-p14-ribbon"' not in html
    assert "Signed reservoir credit balances are included in the displayed Σ" in credit_note
    assert "invalid negative inventory" not in reservoir_row


def test_p14_negative_normal_account_is_not_reservoir_credit() -> None:
    """Producer-forbidden negative terminal inventory must not be laundered as reservoir credit."""
    pure_html = _render_panel(
        _artifact({"final_state": {"terminal.offgas": {"O2": -1.0}}})
    )["html"]
    pure_row = _account_row(pure_html, "terminal.offgas")
    pure_detail = _account_detail(pure_html, "terminal.offgas")
    pure_source = _source_region(pure_html)

    assert "account display sum pending" in pure_row
    assert "invalid negative inventory · not a producer credit account · no Sankey width" in pure_row
    assert "signed reservoir credit balance" not in pure_row
    assert "signed reservoir credit balance" not in pure_html
    assert "Signed reservoir credit balances are included" not in pure_html
    assert "sec-p14-credit-note" not in pure_html
    assert "only reservoir.* accounts may carry signed credit balances" in pure_detail
    assert "pending · incomplete numeric account map" in pure_source
    assert NUMERIC_MOL.search(pure_source) is None
    assert 'class="sec-p14-ribbon"' not in pure_row
    assert "-1 mol" not in pure_row  # no legitimized measured total

    mixed_html = _render_panel(
        _artifact({"final_state": {"terminal.offgas": {"Fe": 10.0, "O2": -1.0}}})
    )["html"]
    mixed_row = _account_row(mixed_html, "terminal.offgas")
    mixed_source = _source_region(mixed_html)

    assert "invalid negative inventory · not a producer credit account · no Sankey width" in mixed_row
    assert "signed reservoir credit" not in mixed_html
    assert "Signed reservoir credit balances are included" not in mixed_html
    assert 'class="sec-p14-ribbon"' not in mixed_row
    assert "10 mol" not in mixed_row  # do not present partial positive as complete inventory total
    assert "pending · incomplete numeric account map" in mixed_source


def test_p14_render_tolerates_null_array_and_scalar_roots() -> None:
    for root in (None, [], 0, "not-an-artifact", True):
        state = _render_panel(root)
        html = state["html"]
        section = _section(html)
        pending = _html_region(
            html,
            '<div class="pending sec-p14-pending">',
            "</div>",
        )
        assert state["id"] == "sec-p14-sankey"
        assert "Pending terminal inventory" in pending
        assert NUMERIC_MOL.search(section) is None
        assert "sec-p14-ribbon" not in html


def test_p14_css_selectors_are_scoped_under_panel_prefix() -> None:
    css = (VIEWER / "panels" / "p14-sankey.css").read_text(encoding="utf-8")
    arms = _css_selector_arms(css)
    assert arms, "expected at least one CSS selector arm"
    for arm in arms:
        assert ".sec-p14-sankey" in arm, f"unscoped selector arm: {arm!r}"


def test_p14_deep_link_ids_unique_for_slug_colliding_accounts() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.a-b": {"Fe": 1.0},
                    "process.a.b": {"Fe": 2.0},
                }
            }
        )
    )["html"]
    detail_ids = re.findall(
        r'<details class="sec-p14-account-detail" id="([^"]+)"',
        html,
    )
    ribbon_targets = re.findall(
        r'class="sec-p14-ribbon" href="#([^"]+)" data-p14-account="(process\.a[-.]b)"',
        html,
    )
    dest_targets = re.findall(
        r'href="#([^"]+)" data-p14-account="(process\.a[-.]b)"',
        html,
    )

    assert len(detail_ids) == 2
    assert len(set(detail_ids)) == 2
    assert all(detail_id.startswith("sec-p14-account-process-a-b-") for detail_id in detail_ids)
    assert {target for target, _ in ribbon_targets} == set(detail_ids)
    assert {target for target, _ in dest_targets} == set(detail_ids)
    # Hash suffixes must differ even though slugs normalize identically.
    suffixes = {detail_id.rsplit("-", 1)[-1] for detail_id in detail_ids}
    assert len(suffixes) == 2
