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
        "degradation_reason": "none",
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
        "degraded_from": [],
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

    assert 'id="sec-p9-provenance"' in html
    assert "Lunar Mare Low Ti" in html
    assert "C2A" in html and "125.5 kg" in html
    assert "1.25 kg" in html and "0.5 kg" in html
    assert "pyrolysis" in html and "magemin" in html
    assert "Real engine active" in html and "Yes <small>(emitted)</small>" in html
    assert "2026-07-20T12:34:56Z" in html
    assert "01234567…" in html and f'title="{sha}"' in html
    assert 'tabindex="0"' in html
    assert "Vapor Pressure" in html and "builtin-vapor-pressure" in html
    assert "Authoritative provider" in html and "Shadow providers" in html
    assert "No fallback provider in emitted field" in html
    assert "proof_inputs" in html and "backend_selection:auto" in html
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

    assert "internal-analytical" in html
    assert "unavailable" in html
    assert "diagnostic_only" in html
    assert "legacy_backend_authoritative" in html
    assert "No <small>(emitted)</small>" in html
    assert "Backend authoritative</dt><dd>false" in html
    assert "Certification allowed</dt><dd>false" in html
    assert "grounded" not in html.lower()
    assert "Yes <small>(emitted)</small>" not in html


def test_p9_absent_provenance_stays_pending_without_defaults() -> None:
    html = _render_panel({"header": {}, "terminal": {}})

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
        "source": '<img src=x onerror="bad()">',
        "reference": "audit-R9",
        "skip_reason": "license unavailable",
    }
    html = _render_panel({
        "header": {"engine_identity": identity},
        "terminal": {"run_metadata": _complete_metadata()},
    })

    assert "Status" in html and "provisional" in html
    assert "Version" in html and "7.2&lt;unsafe&gt;" in html
    assert "Authoritative" in html and "false" in html
    assert "Diagnostic only" in html and "true" in html
    assert "High uncertainty" in html
    assert "audit-R9" in html and "license unavailable" in html
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

    assert "alphamelts" in html and "real" in html
    assert "Runtime status not emitted" in html
    assert "Real-engine state not emitted" in html
    assert "Label source not emitted" in html and "source-from-list" in html
    assert "Degradation reason not emitted" in html and "legacy-origin" in html
    assert "Engine chain not emitted" in html
    assert "Run metadata commit not emitted" in html and "header-identity-sha" in html
    assert "Charge mass not emitted" in html
    assert "Feedstock not emitted" in html and "Campaign not emitted" in html
    assert "Start time not emitted" in html
    assert "Certification permission not emitted" in html
    assert "Evidence class not emitted" in html
    assert "Backend wire token not emitted" in html
    assert "Cache version not emitted" in html
    assert "0.75 kg" in html and "0.5 kg" in html
    assert "1.25 kg" not in html and "999 kg" not in html
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

    assert "terminal.run_metadata is malformed; expected an object" in html
    assert "header.engine_identity is malformed; expected an object" in html
    assert "Real-engine state unavailable because run metadata is malformed" in html
    assert "Engine chain unavailable because run metadata is malformed" in html
    assert "terminal.run_metadata is not emitted" not in html
    assert "header.engine_identity is not emitted" not in html
