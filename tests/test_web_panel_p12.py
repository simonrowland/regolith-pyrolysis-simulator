from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _artifact(*summaries: dict) -> dict:
    return {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "p12-test"},
        "timesteps": [
            {"hour": index + 1, "summary": summary}
            for index, summary in enumerate(summaries)
        ],
        "terminal": {},
    }


def _render_panel(artifact: dict) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = artifact.timesteps.map((timestep) => timestep.summary);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p12-carrier-pressure");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p12-carrier-pressure.js"),
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def test_p12_renders_emitted_carrier_in_mbar_with_separate_context() -> None:
    html = _render_panel(
        _artifact(
            {
                "carrier_identity": "N2",
                "p_carrier_bar": 0.0075,
                "P_total_bar": 0.01,
                "pO2_bar": 0.0025,
            }
        )
    )

    assert "N₂ partial pressure" in html
    assert "7.5 mbar" in html
    assert "Total overhead pressure" in html
    assert "0.01 bar" in html
    assert "O₂ partial pressure" in html
    assert "0.0025 bar" in html
    assert "bar to mbar unit conversion (×1000)" in html
    assert "P_total−pO₂ is not used as a substitute" not in html


def test_p12_absent_carrier_is_pending_not_zero() -> None:
    html = _render_panel(_artifact({}))

    assert "Pending carrier_identity / P_total−pO₂ is not used as a substitute." in html
    assert "0 mbar" not in html
    assert "Carrier partial pressure</div>" not in html
    assert html.count("not emitted") == 2


def test_p12_surfaces_artifact_provenance_without_inventing_authority() -> None:
    html = _render_panel(
        _artifact(
            {
                "carrier_identity": "Ar",
                "p_carrier_bar": 0.004,
                "P_total_bar": 0.005,
                "pO2_bar": 0.001,
            }
        )
    )

    assert html.count(">Artifact-emitted</span>") == 3
    assert "no pressure is inferred by the viewer" in html
    assert html.count('tabindex="0"') == 3
    assert "authoritative" not in html.lower()


def test_p12_partial_context_never_substitutes_total_minus_oxygen() -> None:
    html = _render_panel(
        _artifact(
            {
                "campaign": "PN2_SWEEP",
                "carrier_identity": "N2",
                "p_carrier_bar": 0.0075,
                "P_total_bar": 0.01,
                "pO2_bar": 0.0025,
            },
            {
                "campaign": "PN2_SWEEP",
                "P_total_bar": 0.01,
                "pO2_bar": 0.0025,
            },
        )
    )

    assert "Pending carrier_identity / P_total−pO₂ is not used as a substitute." in html
    assert "7.5 mbar" not in html
    assert "N₂ partial pressure" not in html
    assert "0.01 bar" in html
    assert "0.0025 bar" in html


def test_p12_incomplete_pair_renders_only_the_emitted_carrier_fields() -> None:
    identity_html = _render_panel(
        _artifact({"carrier_identity": "N2", "P_total_bar": 0.01})
    )
    pressure_html = _render_panel(
        _artifact({"p_carrier_bar": 0.003, "P_total_bar": 0.01})
    )

    assert "N₂ partial pressure" in identity_html
    assert "mbar" not in identity_html
    assert "Carrier partial pressure" in pressure_html
    assert "3 mbar" in pressure_html
    assert "N₂ partial pressure" not in pressure_html
    for html in (identity_html, pressure_html):
        assert (
            "Pending carrier_identity / P_total−pO₂ is not used as a substitute."
            in html
        )


def test_p12_partial_context_values_are_never_derived() -> None:
    oxygen_missing = _render_panel(
        _artifact(
            {
                "carrier_identity": "N2",
                "p_carrier_bar": 0.003,
                "P_total_bar": 0.01,
            }
        )
    )
    total_missing = _render_panel(
        _artifact(
            {
                "carrier_identity": "N2",
                "p_carrier_bar": 0.003,
                "pO2_bar": 0.002,
            }
        )
    )

    assert (
        '<div class="sec-p12-label">O₂ partial pressure</div>'
        '<div class="sec-p12-value">not emitted</div>' in oxygen_missing
    )
    assert "0.007 bar" not in oxygen_missing
    assert (
        '<div class="sec-p12-label">Total overhead pressure</div>'
        '<div class="sec-p12-value">not emitted</div>' in total_missing
    )
    assert "0.005 bar" not in total_missing


def test_p12_escapes_emitted_carrier_identity() -> None:
    html = _render_panel(
        _artifact(
            {
                "carrier_identity": 'N2<img src=x onerror="alert(1)">',
                "p_carrier_bar": 0.003,
            }
        )
    )

    assert "<img" not in html
    assert "N2&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
