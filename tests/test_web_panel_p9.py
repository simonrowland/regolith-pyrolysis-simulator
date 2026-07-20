from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


def _render_panel(artifact: object, *, species_color_spy_prefix: str = "") -> str:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
if (process.argv[5]) {
  const prefix = process.argv[5];
  context.ReportLabels = Object.freeze({
    ...context.ReportLabels,
    speciesColor: (species) => `${prefix}${species}`,
  });
}
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels.find((item) => item.id === "sec-p9-provenance");
process.stdout.write(panel.render(JSON.parse(process.argv[4]), [], [], {}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(root / "labels.js"),
            str(root / "panels/p9-provenance.js"),
            json.dumps(artifact),
            species_color_spy_prefix,
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _article(html: str, heading: str) -> str:
    heading_marker = f"<h3>{heading}</h3>"
    heading_start = html.index(heading_marker)
    article_start = html.rfind("<article", 0, heading_start)
    article_end = html.index("</article>", heading_start) + len("</article>")
    return html[article_start:article_end]


def _detail_value(region: str, label: str) -> str:
    marker = f"<dt>{label}</dt><dd>"
    value_start = region.index(marker) + len(marker)
    cursor = value_start
    depth = 1
    while depth:
        next_open = region.find("<dd>", cursor)
        next_close = region.find("</dd>", cursor)
        if next_close < 0:
            raise ValueError(f"unclosed detail value for {label}")
        if 0 <= next_open < next_close:
            depth += 1
            cursor = next_open + len("<dd>")
        else:
            depth -= 1
            if depth == 0:
                return region[value_start:next_close]
            cursor = next_close + len("</dd>")
    raise AssertionError("unreachable")


def _badge_value(html: str, label: str) -> str:
    marker = f"<span>{label}</span><b>"
    return html.split(marker, 1)[1].split("</b>", 1)[0]


def _badge_region(html: str, label: str) -> str:
    label_marker = f"<span>{label}</span><b>"
    label_start = html.index(label_marker)
    badge_start = html.rfind('<span class="sec-p9-badge ', 0, label_start)
    badge_end = html.index("</b></span>", label_start) + len("</b></span>")
    return html[badge_start:badge_end]


def _badge_labels(html: str) -> list[str]:
    marker = '<div class="sec-p9-badges">'
    region = html.split(marker, 1)[1].split("</div>", 1)[0]
    return re.findall(r"<span>([^<]+)</span><b>", region)


def _summary_chrome(html: str) -> str:
    after_subtitle = html.split("</p>", 1)[1]
    return after_subtitle.split('<details class="sec-p9-details">', 1)[0]


def _state_context(html: str) -> str:
    marker = '<div class="sec-p9-state-context">'
    return html.split(marker, 1)[1].split("</div>", 1)[0]


def _complete_metadata(**overrides) -> dict:
    metadata = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C2A",
        "mass_kg": 125.5,
        "additives_kg": {"C": 1.25, "Mg": 0.5},
        "track": "pyrolysis",
        "backend": "magemin",
        "backend_status": "ok",
        "runtime_status": "ok",
        "backend_real_active": True,
        "backend_authoritative": True,
        "evidence_class": "magemin",
        "certification_allowed": True,
        "started_at_utc": "2026-07-20T12:34:56Z",
        "kernel_commit_sha": "0123456789abcdef0123456789abcdef",
        "engines_used": {
            "active": {"vapor_pressure": "builtin-vapor-pressure"},
            "requested": {"gate_liquid_fraction": "magemin"},
            "registry": {
                "gate_liquid_fraction": {
                    "authoritative": "magemin",
                    "fallback": None,
                    "shadows": ["alphamelts"],
                }
            },
        },
        "label_source": "proof_inputs",
        "label_sources": ["proof_inputs", "backend_selection:auto"],
    }
    metadata.update(overrides)
    return metadata


def test_p9_real_run_renders_complete_provenance() -> None:
    sha = "0123456789abcdef0123456789abcdef"
    artifact = {
        "header": {
            "engine_identity": {
                "name": "magemin",
                "cache_version": "magemin-7.2",
                "backend_wire_token": "magemin",
                "kernel_commit_sha": sha,
            }
        },
        "terminal": {"run_metadata": _complete_metadata()},
    }

    html = _render_panel(artifact)
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")
    identity = _article(html, "Artifact engine identity")
    engine_chain = _article(html, "Engine chain")

    assert 'id="sec-p9-provenance"' in html
    assert _badge_value(html, "Feedstock") == "Lunar Mare Low Ti"
    assert _badge_value(html, "Campaign") == "C2A"
    assert _badge_value(html, "Track") == "pyrolysis"
    assert _badge_value(html, "Backend") == "magemin"
    assert _badge_value(html, "Backend status") == "ok"
    assert _badge_value(html, "Runtime status") == "ok"
    assert "Yes <small>(emitted)</small>" in _badge_value(html, "Real engine active")
    assert "sec-p9-badge-positive" in _badge_region(html, "Real engine active")
    assert _badge_value(html, "Evidence class") == "magemin"
    assert _detail_value(run_input, "Charge mass") == "125.5 kg"
    assert "1.25 kg" in _detail_value(run_input, "Additives")
    assert "0.5 kg" in _detail_value(run_input, "Additives")
    assert _detail_value(run_input, "Started at (UTC)") == "2026-07-20T12:34:56Z"
    run_sha = _detail_value(run_input, "Kernel commit SHA")
    assert "01234567…" in run_sha and f'title="{sha}"' in run_sha
    assert 'tabindex="0"' in run_sha
    assert _detail_value(backend, "Backend status") == "ok"
    assert _detail_value(backend, "Evidence class") == "magemin"
    assert _detail_value(backend, "Backend authoritative") == "true"
    assert _detail_value(backend, "Certification allowed") == "true"
    assert "Degradation reason not emitted" in _detail_value(backend, "Degradation reason")
    assert "Degraded from not emitted" in _detail_value(backend, "Degraded from")
    assert "sec-p9-state-context" not in html
    assert f'title="{sha}"' in _detail_value(identity, "Kernel commit SHA")
    assert _detail_value(identity, "Cache version") == "magemin-7.2"
    assert _detail_value(identity, "Backend wire token") == "magemin"
    assert "builtin-vapor-pressure" in _detail_value(engine_chain, "Vapor Pressure")
    requested = _detail_value(engine_chain, "Requested providers")
    assert _detail_value(requested, "Gate Liquid Fraction") == '<span class="sec-p9-mono-value">magemin</span>'
    assert "magemin" in _detail_value(engine_chain, "Authoritative provider")
    assert "alphamelts" in _detail_value(engine_chain, "Shadow providers")
    assert "No fallback provider in emitted field" in engine_chain
    assert "proof_inputs" in _detail_value(backend, "Label source")
    assert "backend_selection:auto" in _detail_value(backend, "Label sources")
    assert "No confidence tier is computed" in html


def test_p9_degraded_run_keeps_reason_and_origin_and_never_claims_grounded() -> None:
    metadata = _complete_metadata(
        backend="internal-analytical",
        backend_status="unavailable",
        runtime_status="unavailable",
        degradation_reason="diagnostic_only",
        degraded_from=["diagnostic_only", "unavailable"],
        backend_real_active=False,
        backend_authoritative=False,
        evidence_class="internal-analytical",
        certification_allowed=False,
        label_source="backend_alias:internal-analytical",
        label_sources=["backend_alias:internal-analytical", "legacy_backend_authoritative"],
    )
    html = _render_panel({
        "header": {"engine_identity": {"name": "internal-analytical"}},
        "terminal": {"run_metadata": metadata},
    })
    backend = _article(html, "Backend evidence &amp; degradation")
    reason_context = _state_context(html)

    assert _badge_value(html, "Backend") == "internal-analytical"
    assert _badge_value(html, "Backend status") == "unavailable"
    assert _badge_value(html, "Evidence class") == "internal-analytical"
    assert "No <small>(emitted)</small>" in _badge_value(html, "Real engine active")
    assert "sec-p9-badge-caution" in _badge_region(html, "Real engine active")
    assert "sec-p9-badge-positive" not in _badge_region(html, "Real engine active")
    assert "diagnostic_only" in reason_context
    assert "not emitted" not in reason_context
    assert _detail_value(backend, "Degradation reason") == "diagnostic_only"
    origin = _detail_value(backend, "Degraded from")
    assert "diagnostic_only" in origin and "unavailable" in origin
    assert "legacy_backend_authoritative" in _detail_value(backend, "Label sources")
    assert _detail_value(backend, "Backend authoritative") == "false"
    assert _detail_value(backend, "Certification allowed") == "false"
    assert _detail_value(backend, "Evidence class") == "internal-analytical"
    assert "Yes <small>(emitted)</small>" not in html


def test_p9_absent_provenance_stays_pending_without_defaults() -> None:
    html = _render_panel({"header": {}, "terminal": {}})
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")

    assert "terminal.run_metadata is not emitted" in html
    assert "header.engine_identity is not emitted" in html
    assert "Feedstock not emitted" in html
    assert "Charge mass not emitted" in html
    assert "Backend not emitted" in html
    assert "Runtime status not emitted" in html
    assert "Real engine active not emitted" in _badge_value(html, "Real engine active")
    assert "Real engine active not emitted" in _detail_value(backend, "Real engine active")
    assert "Started at (UTC) not emitted" in html
    assert "Engine chain not emitted" in html
    assert "Label source not emitted" in html
    additives = _detail_value(run_input, "Additives")
    assert "Additives not emitted" in additives
    assert "No additive entries in emitted map" not in additives
    label_sources = _detail_value(backend, "Label sources")
    degradation_origin = _detail_value(backend, "Degraded from")
    assert "Label sources not emitted" in label_sources
    assert "Degraded from not emitted" in degradation_origin
    assert "emitted list is empty" not in label_sources
    assert "emitted list is empty" not in degradation_origin
    assert "sec-p9-state-context" not in html
    assert "0 kg" not in html
    assert "internal-analytical" not in html


def test_p9_engine_identity_preserves_authority_uncertainty_and_escapes() -> None:
    identity = {
        "name": "engine<script>bad()</script>",
        "cache_version": "v7.2",
        "backend_wire_token": "wire-token",
        "kernel_commit_sha": "abcdef0123456789abcdef0123456789",
        "status": "provisional",
        "version": "7.2<unsafe>",
        "authoritative": False,
        "diagnostic_only": True,
        "high_uncertainty": True,
        "extrapolation": True,
        "source": '<img src=x onerror="bad()">',
        "reference": "audit-R9",
        "skip_reason": "license unavailable",
    }
    html = _render_panel({
        "header": {"engine_identity": identity},
        "terminal": {"run_metadata": _complete_metadata()},
    })
    identity_region = _article(html, "Artifact engine identity")

    assert _detail_value(identity_region, "Status") == '<span class="sec-p9-mono-value">provisional</span>'
    assert _detail_value(identity_region, "Version") == '<span class="sec-p9-mono-value">7.2&lt;unsafe&gt;</span>'
    assert _detail_value(identity_region, "Authoritative") == '<span class="sec-p9-mono-value">false</span>'
    assert _detail_value(identity_region, "Diagnostic only") == '<span class="sec-p9-mono-value">true</span>'
    assert _detail_value(identity_region, "High uncertainty") == '<span class="sec-p9-mono-value">true</span>'
    assert _detail_value(identity_region, "Extrapolation") == '<span class="sec-p9-mono-value">true</span>'
    assert "audit-R9" in _detail_value(identity_region, "Reference")
    assert "license unavailable" in _detail_value(identity_region, "Skip reason")
    assert "engine&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&quot;bad()&quot;&gt;" in html
    assert "<script>" not in html and "<img" not in html
    assert "[object Object]" not in html


def test_p9_nullable_authority_flags_do_not_claim_provider_absence() -> None:
    html = _render_panel({
        "header": {
            "engine_identity": {
                "name": "state-check",
                "authoritative": None,
                "fallback": None,
            }
        },
        "terminal": {
            "run_metadata": {
                "contributors": [{"authoritative": None, "fallback": None}],
                "engines_used": {
                    "registry": {
                        "gate_liquid_fraction": {
                            "authoritative": None,
                            "fallback": None,
                            "shadows": [],
                        }
                    }
                },
            }
        },
    })
    identity = _article(html, "Artifact engine identity")
    backend = _article(html, "Backend evidence &amp; degradation")
    contributors = _detail_value(backend, "Contributors")
    engine_chain = _article(html, "Engine chain")

    identity_authority = _detail_value(identity, "Authoritative")
    identity_fallback = _detail_value(identity, "Fallback provider")
    contributor_authority = _detail_value(contributors, "Authoritative")
    contributor_fallback = _detail_value(contributors, "Fallback provider")
    assert "Authoritative emitted as null" in identity_authority
    assert "Authoritative emitted as null" in contributor_authority
    assert "Fallback provider emitted as null" in identity_fallback
    assert "Fallback provider emitted as null" in contributor_fallback
    assert "No authoritative provider" not in identity_authority
    assert "No authoritative provider" not in contributor_authority
    assert "No fallback provider" not in identity_fallback
    assert "No fallback provider" not in contributor_fallback
    assert "No authoritative provider in emitted field" in _detail_value(
        engine_chain, "Authoritative provider"
    )
    assert "No fallback provider in emitted field" in _detail_value(
        engine_chain, "Fallback provider"
    )


def test_p9_partial_fields_are_not_inferred_from_related_values() -> None:
    metadata = {
        "backend": "alphamelts",
        "backend_status": "real",
        "backend_authoritative": True,
        "additives_kg": {"C": 0.75, "Mg": 0.5},
        "label_sources": ["source-from-list"],
        "degraded_from": ["legacy-origin"],
    }
    artifact = {
        "header": {
            "feedstock_id": "header-only-feedstock",
            "charge_mass_kg": 999.0,
            "created_at": "header-only-time",
            "campaign_chain": ["C9"],
            "engine_identity": {
                "name": "alphamelts",
                "kernel_commit_sha": "header-identity-sha",
            },
        },
        "terminal": {"run_metadata": metadata},
    }

    html = _render_panel(artifact)
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")
    identity = _article(html, "Artifact engine identity")

    assert _badge_value(html, "Backend") == "alphamelts"
    assert _badge_value(html, "Backend status") == "real"
    assert _detail_value(backend, "Backend status") == "real"
    assert "Runtime status not emitted" in _detail_value(backend, "Runtime status")
    assert "Real engine active not emitted" in _detail_value(backend, "Real engine active")
    assert "Real engine active not emitted" in _badge_value(html, "Real engine active")
    assert "Label source not emitted" in _detail_value(backend, "Label source")
    assert "source-from-list" in _detail_value(backend, "Label sources")
    assert "Degradation reason not emitted" in _detail_value(backend, "Degradation reason")
    assert "legacy-origin" in _detail_value(backend, "Degraded from")
    assert "Engine chain not emitted" in _article(html, "Engine chain")
    assert "Kernel commit SHA not emitted" in _detail_value(run_input, "Kernel commit SHA")
    assert "header-identity-sha" in _detail_value(identity, "Kernel commit SHA")
    charge_mass = _detail_value(run_input, "Charge mass")
    assert "Charge mass not emitted" in charge_mass
    assert "999 kg" not in charge_mass
    assert "Feedstock ID not emitted" in _detail_value(run_input, "Feedstock ID")
    assert "Campaign not emitted" in _detail_value(run_input, "Campaign")
    assert "Started at (UTC) not emitted" in _detail_value(run_input, "Started at (UTC)")
    assert "Certification allowed not emitted" in _detail_value(backend, "Certification allowed")
    assert "Evidence class not emitted" in _detail_value(backend, "Evidence class")
    assert _detail_value(backend, "Backend authoritative") == "true"
    assert "Backend wire token not emitted" in _detail_value(identity, "Backend wire token")
    assert "Cache version not emitted" in _detail_value(identity, "Cache version")
    additives = _detail_value(run_input, "Additives")
    assert "0.75 kg" in additives and "0.5 kg" in additives
    assert "1.25 kg" not in additives
    assert "Yes <small>(emitted)</small>" not in html

    backend_only_html = _render_panel({
        "header": {"engine_identity": {"name": "internal-analytical"}},
        "terminal": {"run_metadata": {"backend": "internal-analytical"}},
    })
    assert "Backend status not emitted" in backend_only_html
    assert "Runtime status not emitted" in backend_only_html
    assert "Real engine active not emitted" in backend_only_html


def test_p9_malformed_parent_records_are_not_reported_as_absent() -> None:
    html = _render_panel({
        "header": {"engine_identity": ["not", "an", "object"]},
        "terminal": {"run_metadata": "not-an-object"},
    })
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")

    assert "terminal.run_metadata is malformed; expected an object" in html
    assert "header.engine_identity is malformed; expected an object" in html
    assert "Real engine active unavailable because a parent record is malformed" in html
    assert "Engine chain unavailable because a parent record is malformed" in html
    assert "Feedstock ID unavailable because its parent record is malformed" in _detail_value(run_input, "Feedstock ID")
    assert "Backend status unavailable because its parent record is malformed" in _detail_value(backend, "Backend status")
    assert "Charge mass unavailable because its parent record is malformed" in _detail_value(run_input, "Charge mass")
    assert "Engine identity unavailable because its parent record is malformed" in _badge_value(html, "Engine identity")
    assert "Feedstock ID not emitted" not in _detail_value(run_input, "Feedstock ID")
    assert "Backend status not emitted" not in _detail_value(backend, "Backend status")
    assert "Charge mass not emitted" not in _detail_value(run_input, "Charge mass")
    assert "Engine identity not emitted" not in _badge_value(html, "Engine identity")
    assert "terminal.run_metadata is not emitted" not in html
    assert "header.engine_identity is not emitted" not in html


def test_p9_present_empty_malformed_null_and_zero_states_stay_distinct() -> None:
    html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {
            "run_metadata": {
                "campaign": "",
                "mass_kg": 0,
                "additives_kg": {"C": 0},
                "backend_status": [],
                "backend_real_active": None,
                "label_source": None,
                "label_sources": [],
                "degraded_from": None,
                "engines_used": None,
            }
        },
    })
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")

    campaign = _detail_value(run_input, "Campaign")
    assert "Campaign: emitted string is empty" in campaign
    assert "Campaign not emitted" not in campaign
    assert _detail_value(run_input, "Charge mass") == "0 kg"
    assert "0 kg" in _detail_value(run_input, "Additives")
    assert "Backend status is malformed" in _detail_value(backend, "Backend status")
    assert "Real engine active emitted as null" in _detail_value(backend, "Real engine active")
    assert "Real engine active emitted as null" in _badge_value(html, "Real engine active")
    assert "Label source emitted as null" in _detail_value(backend, "Label source")
    assert "Label sources: emitted list is empty" in _detail_value(backend, "Label sources")
    assert "Degraded from emitted as null" in _detail_value(backend, "Degraded from")
    assert "Engine chain emitted as null" in _article(html, "Engine chain")

    null_additives_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"additives_kg": None}},
    })
    assert "Additives emitted as null" in _detail_value(
        _article(null_additives_html, "Run input provenance"), "Additives"
    )

    empty_maps_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"additives_kg": {}, "engines_used": {}}},
    })
    assert "No additive entries in emitted map" in _detail_value(
        _article(empty_maps_html, "Run input provenance"), "Additives"
    )
    assert "Emitted map is empty" in _article(empty_maps_html, "Engine chain")

    malformed_shapes_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {
            "run_metadata": {
                "additives_kg": [],
                "backend_real_active": "yes",
                "engines_used": [],
            }
        },
    })
    malformed_run_input = _article(malformed_shapes_html, "Run input provenance")
    malformed_backend = _article(malformed_shapes_html, "Backend evidence &amp; degradation")
    assert "Additives map is malformed" in _detail_value(malformed_run_input, "Additives")
    assert "Real engine active is malformed" in _detail_value(malformed_backend, "Real engine active")
    assert "Real engine active is malformed" in _badge_value(malformed_shapes_html, "Real engine active")
    assert "Engine chain is malformed" in _article(malformed_shapes_html, "Engine chain")

    malformed_mass_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"additives_kg": {"C": "bad-mass"}}},
    })
    assert "Additive mass is malformed" in _detail_value(
        _article(malformed_mass_html, "Run input provenance"), "Additives"
    )


def test_p9_present_parent_missing_children_stay_pending_with_consistent_labels() -> None:
    html = _render_panel({
        "header": {"engine_identity": {"name": "magemin"}},
        "terminal": {"run_metadata": {"backend": "magemin"}},
    })
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")

    assert "Started at (UTC) not emitted" in _detail_value(run_input, "Started at (UTC)")
    assert "Real engine active not emitted" in _detail_value(backend, "Real engine active")
    assert "Real engine active not emitted" in _badge_value(html, "Real engine active")
    assert "Degradation reason not emitted" in _detail_value(backend, "Degradation reason")
    assert "Degraded from not emitted" in _detail_value(backend, "Degraded from")
    assert "Backend authoritative not emitted" in _detail_value(backend, "Backend authoritative")
    assert "Certification allowed not emitted" in _detail_value(backend, "Certification allowed")
    assert "sec-p9-empty" not in _detail_value(backend, "Degradation reason")
    assert "sec-p9-empty" not in _detail_value(backend, "Degraded from")
    assert "sec-p9-state-context" not in html
    assert "sec-p9-badge-pending" in _badge_region(html, "Real engine active")


def test_p9_list_values_are_escaped_exactly_once() -> None:
    hostile_origin = "<script>bad()</script>"
    hostile_source = '<img src=x onerror="bad()">'
    html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {
            "run_metadata": {
                "degradation_reason": "diagnostic_only",
                "degraded_from": [hostile_origin],
                "label_sources": [hostile_source],
            }
        },
    })
    backend = _article(html, "Backend evidence &amp; degradation")
    origin = _detail_value(backend, "Degraded from")
    sources = _detail_value(backend, "Label sources")

    assert "&lt;script&gt;bad()&lt;/script&gt;" in origin
    assert "&lt;img src=x onerror=&quot;bad()&quot;&gt;" in sources
    assert "<script>" not in origin and "<img" not in sources
    assert "&amp;lt;" not in origin and "&amp;lt;" not in sources


def test_p9_dynamic_keys_and_identifier_attributes_are_escaped_exactly_once() -> None:
    hostile_species = "<img src=x onerror=bad()>"
    hostile_intent = "<img src=x onerror=bad()>"
    hostile_sha = '" onmouseover="bad()'
    html = _render_panel({
        "header": {
            "engine_identity": {
                "name": "state-check",
                "kernel_commit_sha": hostile_sha,
            }
        },
        "terminal": {
            "run_metadata": {
                "additives_kg": {hostile_species: 1.0},
                "engines_used": {"requested": {hostile_intent: "magemin"}},
            }
        },
    })
    run_input = _article(html, "Run input provenance")
    additives = _detail_value(run_input, "Additives")
    identity = _article(html, "Artifact engine identity")
    identifier = _detail_value(identity, "Kernel commit SHA")
    engine_chain = _article(html, "Engine chain")
    requested = _detail_value(engine_chain, "Requested providers")

    assert "&lt;img src=x onerror=bad()&gt;" in additives
    assert "1 kg" in additives
    assert "<img" not in additives and "&amp;lt;" not in additives
    assert 'title="&quot; onmouseover=&quot;bad()"' in identifier
    assert 'title="" onmouseover="bad()"' not in identifier
    assert "&amp;quot;" not in identifier
    assert "&lt;Img Src=X Onerror=Bad()&gt;" in requested
    assert "<img" not in requested.lower() and "&amp;lt;" not in requested


def test_p9_optional_trust_fields_survive_with_emitted_values() -> None:
    html = _render_panel({
        "header": {"engine_identity": {"name": "magemin"}},
        "terminal": {
            "run_metadata": {
                "cache_state": "served_neighbor",
                "contributors": [{
                    "evidence_class": "melts",
                    "label_source": "backend<unsafe>",
                    "backend_real_active": False,
                    "requires_inherited_evidence_class": True,
                    "source": "FactSAGE",
                    "reference": "audit<R9>",
                    "authoritative": False,
                    "diagnostic_only": True,
                }],
                "requires_inherited_evidence_class": True,
            }
        },
    })
    backend = _article(html, "Backend evidence &amp; degradation")
    contributors = _detail_value(backend, "Contributors")

    assert _detail_value(backend, "Cache State") == '<span class="sec-p9-mono-value">served_neighbor</span>'
    assert _detail_value(backend, "Requires inherited evidence class") == '<span class="sec-p9-mono-value">true</span>'
    assert _detail_value(contributors, "Evidence class") == '<span class="sec-p9-mono-value">melts</span>'
    assert _detail_value(contributors, "Label source") == '<span class="sec-p9-mono-value">backend&lt;unsafe&gt;</span>'
    assert _detail_value(contributors, "Real engine active") == '<span class="sec-p9-mono-value">false</span>'
    assert _detail_value(contributors, "Requires inherited evidence class") == '<span class="sec-p9-mono-value">true</span>'
    assert _detail_value(contributors, "Source") == '<span class="sec-p9-mono-value">FactSAGE</span>'
    assert _detail_value(contributors, "Reference") == '<span class="sec-p9-mono-value">audit&lt;R9&gt;</span>'
    assert _detail_value(contributors, "Authoritative") == '<span class="sec-p9-mono-value">false</span>'
    assert _detail_value(contributors, "Diagnostic only") == '<span class="sec-p9-mono-value">true</span>'
    assert "<unsafe>" not in contributors and "&amp;lt;" not in contributors
    assert "Evidence class not emitted" in _badge_value(html, "Evidence class")
    assert "Evidence class not emitted" in _detail_value(backend, "Evidence class")
    assert "melts" not in _detail_value(backend, "Evidence class")
    assert "Real engine active not emitted" in _badge_value(html, "Real engine active")
    assert "Real engine active not emitted" in _detail_value(backend, "Real engine active")
    assert "false" not in _detail_value(backend, "Real engine active")
    assert "Label source not emitted" in _detail_value(backend, "Label source")
    assert "backend&lt;unsafe&gt;" not in _detail_value(backend, "Label source")
    assert "Backend authoritative not emitted" in _detail_value(backend, "Backend authoritative")
    assert "false" not in _detail_value(backend, "Backend authoritative")


def test_p9_summary_chrome_has_exact_emitted_field_contract() -> None:
    html = _render_panel({
        "header": {"engine_identity": {"name": "magemin"}},
        "terminal": {"run_metadata": _complete_metadata()},
    })
    expected_labels = [
        "Feedstock",
        "Campaign",
        "Track",
        "Backend",
        "Backend status",
        "Runtime status",
        "Real engine active",
        "Evidence class",
        "Engine identity",
    ]
    chrome = _summary_chrome(html)

    assert _badge_labels(html) == expected_labels
    expected_chrome = '<div class="sec-p9-badges">' + "".join(
        _badge_region(html, label) for label in expected_labels
    ) + "</div>"
    assert chrome == expected_chrome


def test_p9_top_level_malformed_artifacts_render_pending() -> None:
    for artifact in (None, [], "not-an-object", 7):
        html = _render_panel(artifact)

        assert 'id="sec-p9-provenance"' in html
        assert "Artifact is malformed; expected an object" in html
        assert "terminal.run_metadata is unavailable because the artifact is malformed" in html
        assert "header.engine_identity is unavailable because the artifact is malformed" in html
        assert "terminal.run_metadata is not emitted" not in html
        assert "header.engine_identity is not emitted" not in html
        assert "Real engine active unavailable because a parent record is malformed" in _badge_value(html, "Real engine active")
        assert "sec-p9-badge-pending" in _badge_region(html, "Real engine active")


def test_p9_additive_colors_use_shared_species_palette() -> None:
    html = _render_panel({
        "header": {"engine_identity": {"name": "magemin"}},
        "terminal": {"run_metadata": {"additives_kg": {"C": 1.25, "Mg": 0.5, "Fe": 0.25}}},
    }, species_color_spy_prefix="sentinel-color-")
    additives = _detail_value(_article(html, "Run input provenance"), "Additives")

    assert 'style="--species-color:sentinel-color-C"' in additives
    assert 'style="--species-color:sentinel-color-Mg"' in additives
    assert 'style="--species-color:sentinel-color-Fe"' in additives


def test_p9_related_fields_do_not_fill_missing_provenance() -> None:
    reason_only_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"degradation_reason": "diagnostic_only"}},
    })
    reason_only_backend = _article(reason_only_html, "Backend evidence &amp; degradation")
    missing_origin = _detail_value(reason_only_backend, "Degraded from")
    assert "Degraded from not emitted" in missing_origin
    assert "diagnostic_only" not in missing_origin

    metadata_sha = "metadata-only-sha"
    sha_only_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"kernel_commit_sha": metadata_sha}},
    })
    sha_run_input = _article(sha_only_html, "Run input provenance")
    sha_identity = _article(sha_only_html, "Artifact engine identity")
    assert metadata_sha in _detail_value(sha_run_input, "Kernel commit SHA")
    missing_identity_sha = _detail_value(sha_identity, "Kernel commit SHA")
    assert "Kernel commit SHA not emitted" in missing_identity_sha
    assert metadata_sha not in missing_identity_sha

    related_only_html = _render_panel({
        "header": {"engine_identity": {"name": "identity-only", "authoritative": False}},
        "terminal": {
            "run_metadata": {
                "backend_status": "ok",
                "evidence_class": "magemin",
                "label_source": "source-only",
                "engines_used": {
                    "registry": {
                        "gate_liquid_fraction": {"authoritative": "magemin"}
                    }
                },
            }
        },
    })
    related_backend = _article(related_only_html, "Backend evidence &amp; degradation")
    missing_label_sources = _detail_value(related_backend, "Label sources")
    missing_authority = _detail_value(related_backend, "Backend authoritative")
    missing_certification = _detail_value(related_backend, "Certification allowed")
    missing_runtime = _detail_value(related_backend, "Runtime status")
    missing_backend = _badge_value(related_only_html, "Backend")

    assert "Label sources not emitted" in missing_label_sources
    assert "source-only" not in missing_label_sources
    assert "Backend authoritative not emitted" in missing_authority
    assert "magemin" not in missing_authority
    assert "Certification allowed not emitted" in missing_certification
    assert "true" not in missing_certification and "false" not in missing_certification
    assert "Runtime status not emitted" in missing_runtime
    assert "ok" not in missing_runtime
    assert "Backend not emitted" in missing_backend
    assert "identity-only" not in missing_backend

    backend_only_html = _render_panel({
        "header": {"engine_identity": {}},
        "terminal": {"run_metadata": {"backend": "magemin"}},
    })
    backend_only_identity = _article(backend_only_html, "Artifact engine identity")
    missing_identity_badge = _badge_value(backend_only_html, "Engine identity")
    missing_identity_name = _detail_value(backend_only_identity, "Engine name")
    assert "Engine identity not emitted" in missing_identity_badge
    assert "magemin" not in missing_identity_badge
    assert "Engine name not emitted" in missing_identity_name
    assert "magemin" not in missing_identity_name
