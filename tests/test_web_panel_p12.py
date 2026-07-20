from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"
_UNDEFINED = object()


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


def _render_rows(rows: object = _UNDEFINED) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const rows = process.argv[4] === "__undefined__" ? undefined : JSON.parse(process.argv[4]);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p12-carrier-pressure");
process.stdout.write(panel.render({}, rows, [], {}));
"""
    encoded_rows = "__undefined__" if rows is _UNDEFINED else json.dumps(rows)
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p12-carrier-pressure.js"),
            encoded_rows,
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


def test_p12_uses_emitted_carrier_not_nondegenerate_pressure_residual() -> None:
    html = _render_panel(
        _artifact(
            {
                "carrier_identity": "N2",
                "p_carrier_bar": 0.0075,
                "P_total_bar": 0.01,
                "pO2_bar": 0.001,
            }
        )
    )

    assert '<div class="sec-p12-headline-value">7.5 mbar</div>' in html
    assert '<div class="sec-p12-headline-value">9 mbar</div>' not in html


def test_p12_does_not_derive_missing_carrier_from_complete_context() -> None:
    html = _render_panel(
        _artifact(
            {
                "carrier_identity": "N2",
                "P_total_bar": 0.01,
                "pO2_bar": 0.0025,
            }
        )
    )

    assert "Pending carrier_identity / P_total−pO₂ is not used as a substitute." in html
    assert '<div class="sec-p12-headline-value">' not in html
    assert "Carrier identity only; pressure not emitted." in html
    assert "bar to mbar unit conversion" not in html
    assert "7.5 mbar" not in html


def test_p12_does_not_infer_missing_identity_from_campaign() -> None:
    html = _render_panel(
        _artifact(
            {
                "campaign": "PN2_SWEEP",
                "p_carrier_bar": 0.003,
                "P_total_bar": 0.01,
                "pO2_bar": 0.001,
            }
        )
    )

    assert '<div class="sec-p12-label">Carrier partial pressure</div>' in html
    assert '<div class="sec-p12-headline-value">3 mbar</div>' in html
    assert "Pending carrier_identity / P_total−pO₂ is not used as a substitute." in html
    assert "N₂ partial pressure" not in html


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


def test_p12_badges_make_only_the_artifact_provenance_claim() -> None:
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
    expected_badge = (
        '<span class="sec-p12-authority" tabindex="0" '
        'aria-label="Artifact-emitted; read directly from the artifact, with no '
        'viewer-inferred pressure." title="Read directly from the artifact; no '
        'pressure is inferred by the viewer.">Artifact-emitted</span>'
    )

    expected_regions = (
        (
            '<div class="sec-p12-label">Ar partial pressure</div>'
            '<div class="sec-p12-headline-value">4 mbar</div>'
            f'<div class="sec-p12-detail">{expected_badge}'
            " bar to mbar unit conversion (×1000).</div>"
        ),
        (
            '<div class="sec-p12-label">Total overhead pressure</div>'
            '<div class="sec-p12-value">0.005 bar</div>'
            f'<div class="sec-p12-detail">{expected_badge}'
            " Terminal total-pressure context.</div>"
        ),
        (
            '<div class="sec-p12-label">O₂ partial pressure</div>'
            '<div class="sec-p12-value">0.001 bar</div>'
            f'<div class="sec-p12-detail">{expected_badge}'
            " Terminal oxygen-pressure context.</div>"
        ),
    )

    for expected_region in expected_regions:
        assert expected_region in html
    assert html.count(expected_badge) == len(expected_regions)
    assert "Verified measurement" not in html


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


def test_p12_malformed_pressure_scalars_are_not_emitted_values() -> None:
    unavailable_or_malformed_values = (None, "", False, "0.003", [], {})
    context_expectations = {
        "P_total_bar": "Total overhead pressure",
        "pO2_bar": "O₂ partial pressure",
    }

    for field in ("p_carrier_bar", *context_expectations):
        for value in unavailable_or_malformed_values:
            html = _render_panel(_artifact({field: value}))

            assert (
                "Pending carrier_identity / P_total−pO₂ is not used as a substitute."
                in html
            )
            if field == "p_carrier_bar":
                assert '<div class="sec-p12-headline">' not in html
                assert "mbar" not in html
            else:
                label = context_expectations[field]
                expected_metric = (
                    f'<div class="sec-p12-label">{label}</div>'
                    '<div class="sec-p12-value">not emitted</div>'
                    '<div class="sec-p12-detail">Pending artifact field.</div>'
                )
                assert expected_metric in html


def test_p12_empty_or_malformed_identity_stays_pending_without_inference() -> None:
    for invalid_identity in ("   ", 17, [], {}):
        html = _render_panel(
            _artifact(
                {
                    "carrier_identity": invalid_identity,
                    "p_carrier_bar": 0.003,
                }
            )
        )

        assert '<div class="sec-p12-label">Carrier partial pressure</div>' in html
        assert '<div class="sec-p12-headline-value">3 mbar</div>' in html
        assert (
            "Pending carrier_identity / P_total−pO₂ is not used as a substitute."
            in html
        )
        assert "N₂ partial pressure" not in html


def test_p12_zero_is_classified_by_emitter_field_semantics() -> None:
    for invalid_carrier in (0, -0.001):
        carrier_html = _render_panel(
            _artifact(
                {
                    "carrier_identity": "N2",
                    "p_carrier_bar": invalid_carrier,
                }
            )
        )

        assert (
            "Pending carrier_identity / P_total−pO₂ is not used as a substitute."
            in carrier_html
        )
        assert '<div class="sec-p12-headline-value">' not in carrier_html
        assert "Carrier identity only; pressure not emitted." in carrier_html
        assert "bar to mbar unit conversion" not in carrier_html

    context_html = _render_panel(_artifact({"P_total_bar": 0, "pO2_bar": 0}))
    assert (
        '<div class="sec-p12-label">Total overhead pressure</div>'
        '<div class="sec-p12-value">0 bar</div>'
        '<div class="sec-p12-detail"><span class="sec-p12-authority"'
        in context_html
    )
    assert (
        '<div class="sec-p12-label">O₂ partial pressure</div>'
        '<div class="sec-p12-value">0 bar</div>'
        '<div class="sec-p12-detail"><span class="sec-p12-authority"'
        in context_html
    )
    assert context_html.count(">Artifact-emitted</span>") == 2


def test_p12_render_contract_tolerates_missing_or_malformed_rows() -> None:
    for rows in (_UNDEFINED, None, {}, "not-rows", [], [None]):
        html = _render_rows(rows)

        assert '<section class="card sec-p12-carrier-pressure"' in html
        assert "Pending carrier_identity / P_total−pO₂ is not used as a substitute." in html
        for label in ("Total overhead pressure", "O₂ partial pressure"):
            expected_metric = (
                f'<div class="sec-p12-label">{label}</div>'
                '<div class="sec-p12-value">not emitted</div>'
                '<div class="sec-p12-detail">Pending artifact field.</div>'
            )
            assert expected_metric in html


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
