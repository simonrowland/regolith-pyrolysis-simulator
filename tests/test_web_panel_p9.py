from __future__ import annotations

import json
from pathlib import Path
import subprocess


def _render_panel(artifact: dict) -> str:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
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
    return region.split(marker, 1)[1].split("</dd>", 1)[0]


def _badge_value(html: str, label: str) -> str:
    marker = f"<span>{label}</span><b>"
    return html.split(marker, 1)[1].split("</b>", 1)[0]


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
    assert _detail_value(run_input, "Charge mass") == "125.5 kg"
    assert "1.25 kg" in _detail_value(run_input, "Additives")
    assert "0.5 kg" in _detail_value(run_input, "Additives")
    assert _detail_value(run_input, "Started at (UTC)") == "2026-07-20T12:34:56Z"
    run_sha = _detail_value(run_input, "Kernel commit SHA")
    assert "01234567…" in run_sha and f'title="{sha}"' in run_sha
    assert 'tabindex="0"' in run_sha
    assert _detail_value(backend, "Backend status") == "ok"
    assert _detail_value(backend, "Backend authoritative") == "true"
    assert _detail_value(backend, "Certification allowed") == "true"
    assert "No degradation reason in emitted fields" in _detail_value(backend, "Degradation reason")
    assert "No degradation origin in emitted fields" in _detail_value(backend, "Degraded from")
    assert "sec-p9-state-context" not in html
    assert f'title="{sha}"' in _detail_value(identity, "Kernel commit SHA")
    assert "builtin-vapor-pressure" in _detail_value(engine_chain, "Vapor Pressure")
    assert "magemin" in _detail_value(engine_chain, "Authoritative provider")
    assert "alphamelts" in _detail_value(engine_chain, "Shadow providers")
    assert "No fallback provider in emitted field" in engine_chain
    assert "proof_inputs" in _detail_value(backend, "Label source")
    assert "backend_selection:auto" in _detail_value(backend, "Label sources")
    assert "No confidence tier is computed" in html
    assert "Confidence grade" not in html
    assert "sec-p9-confidence" not in html


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
    assert "No <small>(emitted)</small>" in _badge_value(html, "Real engine active")
    assert "diagnostic_only" in reason_context
    assert "not emitted" not in reason_context
    assert _detail_value(backend, "Degradation reason") == "diagnostic_only"
    origin = _detail_value(backend, "Degraded from")
    assert "diagnostic_only" in origin and "unavailable" in origin
    assert "legacy_backend_authoritative" in _detail_value(backend, "Label sources")
    assert _detail_value(backend, "Backend authoritative") == "false"
    assert _detail_value(backend, "Certification allowed") == "false"
    assert "grounded" not in html.lower()
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
    assert "Real-engine state not emitted" in html
    assert "Start time not emitted" in html
    assert "Engine chain not emitted" in html
    assert "Label source not emitted" in html
    additives = _detail_value(run_input, "Additives")
    assert "Additives not emitted" in additives
    assert "No additive entries in emitted map" not in additives
    label_sources = _detail_value(backend, "Label sources")
    degradation_origin = _detail_value(backend, "Degraded from")
    assert "Label sources not emitted" in label_sources
    assert "Degradation origin not emitted" in degradation_origin
    assert "emitted list is empty" not in label_sources
    assert "emitted list is empty" not in degradation_origin
    assert "sec-p9-state-context" not in html
    assert "0 kg" not in html
    assert "internal-analytical" not in html
    assert "grounded" not in html.lower()


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
    assert "Real-engine state not emitted" in _detail_value(backend, "Real engine active")
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
    assert "Start time not emitted" in _detail_value(run_input, "Started at (UTC)")
    assert "Certification allowed not emitted" in _detail_value(backend, "Certification allowed")
    assert "Evidence class not emitted" in _detail_value(backend, "Evidence class")
    assert _detail_value(backend, "Backend authoritative") == "true"
    assert "Backend wire token not emitted" in _detail_value(identity, "Backend wire token")
    assert "Cache version not emitted" in _detail_value(identity, "Cache version")
    additives = _detail_value(run_input, "Additives")
    assert "0.75 kg" in additives and "0.5 kg" in additives
    assert "1.25 kg" not in additives
    assert "Yes <small>(emitted)</small>" not in html
    assert "grounded" not in html.lower()

    backend_only_html = _render_panel({
        "header": {"engine_identity": {"name": "internal-analytical"}},
        "terminal": {"run_metadata": {"backend": "internal-analytical"}},
    })
    assert "Backend status not emitted" in backend_only_html
    assert "Runtime status not emitted" in backend_only_html
    assert "Real-engine state not emitted" in backend_only_html


def test_p9_malformed_parent_records_are_not_reported_as_absent() -> None:
    html = _render_panel({
        "header": {"engine_identity": ["not", "an", "object"]},
        "terminal": {"run_metadata": "not-an-object"},
    })
    run_input = _article(html, "Run input provenance")
    backend = _article(html, "Backend evidence &amp; degradation")

    assert "terminal.run_metadata is malformed; expected an object" in html
    assert "header.engine_identity is malformed; expected an object" in html
    assert "Real-engine state unavailable because run metadata is malformed" in html
    assert "Engine chain unavailable because run metadata is malformed" in html
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
    assert "Real-engine state emitted as null" in _detail_value(backend, "Real engine active")
    assert "Label source emitted as null" in _detail_value(backend, "Label source")
    assert "Label sources: emitted list is empty" in _detail_value(backend, "Label sources")
    assert "Degradation origin emitted as null" in _detail_value(backend, "Degraded from")
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
    assert "Real-engine state is malformed" in _detail_value(malformed_backend, "Real engine active")
    assert "Engine chain is malformed" in _article(malformed_shapes_html, "Engine chain")

    malformed_mass_html = _render_panel({
        "header": {"engine_identity": {"name": "state-check"}},
        "terminal": {"run_metadata": {"additives_kg": {"C": "bad-mass"}}},
    })
    assert "Additive mass is malformed" in _detail_value(
        _article(malformed_mass_html, "Run input provenance"), "Additives"
    )
