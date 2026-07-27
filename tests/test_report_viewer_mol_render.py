import json
import subprocess
from pathlib import Path

from simulator.accounting.run_artifact import build_run_artifact
from simulator.terminal_product_taxonomy import (
    build_terminal_product_taxonomy_entity,
)


_ROOT = Path(__file__).resolve().parents[1]
_HARNESS = (
    _ROOT / "tests/fixtures/web_render/render_report_ledger_values.mjs"
)
_SCRIPT = _ROOT / "web/report_viewer/report-viewer.js"


def _taxonomy_terminal_sections() -> dict:
    entity = build_terminal_product_taxonomy_entity(
        {"MgO": 1.4, "SiO2": 1.1},
        species_mol={"MgO": 35, "SiO2": 18.3},
        class_kg={"oxide_ceramic": 2.5},
        furnace_ceiling_c=1300,
    )
    properties = entity["matched_nodes"][0]["properties"]
    for optional_field in ("notes", "service_temperature", "liner_suitability"):
        properties.pop(optional_field)

    base_payload = {
        "status": "ok",
        "final_state": {
            "process.cleaned_melt": {"MgO": 35, "SiO2": 18.3},
        },
    }
    present = build_run_artifact(
        {**base_payload, "terminal_product_taxonomy": entity},
        run_id="viewer-taxonomy-present",
    )
    # Runner producer failures preserve the attempted field as explicit null.
    explicit_null = build_run_artifact(
        {**base_payload, "terminal_product_taxonomy": None},
        run_id="viewer-taxonomy-producer-failure",
    )
    absent = build_run_artifact(
        base_payload,
        run_id="viewer-taxonomy-legacy",
    )
    return {
        "present": present["terminal"],
        "explicit_null": explicit_null["terminal"],
        "absent": absent["terminal"],
    }


def _render_report_contract() -> dict:
    completed = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(
            {
                "script_path": str(_SCRIPT),
                "taxonomy_terminal_sections": _taxonomy_terminal_sections(),
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# Regression: WEBQA-002 — JS coercion could fabricate boolean/array mol cells.
# Found by /qa on 2026-07-19
# Report: docs-private/research/2026-07-19-webqa/report.md
def test_every_timestep_and_terminal_ledger_render_true_finite_mol_only():
    rendered = _render_report_contract()

    assert len(rendered["timesteps"]) == 2
    assert "2.5 mol" in rendered["timesteps"][0]
    assert "7.25 mol" in rendered["timesteps"][1]
    for html in [*rendered["timesteps"], rendered["terminal"]]:
        assert "non-numeric (boolean)" in html
        assert "non-numeric (array)" in html
        assert "non-numeric (string)" in html
        assert "non-numeric (number)" in html
        for fabricated in (
            ">1 mol<",
            ">0 mol<",
            ">3 mol<",
            ">NaN mol<",
            ">Infinity mol<",
        ):
            assert fabricated not in html
    assert "4.5 mol" in rendered["terminal"]
    assert "not numeric" in rendered["terminal"]


def test_terminal_taxonomy_present_object_renders_entity_contract():
    html = _render_report_contract()["taxonomy"]["present"]

    assert "classification verdict" in html
    assert "matched_single" in html
    assert "oxide_ceramic" in html
    assert "Forsterite" in html
    assert "properties.density_g_cm3" in html
    assert "3.27" in html
    assert "properties.melting_c" in html
    assert "1890" in html
    assert "properties.use_class" in html
    assert "refractory" in html
    assert "properties.strength.status" in html
    assert "sourced_qualitative_text" in html
    assert "properties.strength.text" in html
    assert (
        "Mohs **7**; fracture toughness of dense nano-forsterite researched as "
        "relatively high among ceramics; brittle. [web:34][web:31]"
    ) in html
    assert "properties.notes" not in html
    assert "properties.service_temperature" not in html
    assert "properties.liner_suitability" not in html
    assert "Physical terminal-rump mass" in html
    assert "2.5 kg" in html
    assert "1.4 kg" in html
    assert "35 mol" in html
    assert "56%" in html
    assert "kg_projected_from_mol_ledger" in html
    assert "terminal.terminal_product_taxonomy is absent" not in html


def test_terminal_taxonomy_explicit_null_renders_attempted_unavailable_contract():
    html = _render_report_contract()["taxonomy"]["explicit_null"]

    assert "attempted but unavailable" in html
    assert "explicitly null" in html
    assert "terminal.terminal_product_taxonomy is absent" not in html


def test_terminal_taxonomy_absent_keeps_legacy_pending_contract():
    html = _render_report_contract()["taxonomy"]["absent"]

    assert "Pending W-D7" in html
    assert "terminal.terminal_product_taxonomy is absent" in html
    assert "attempted but unavailable" not in html
