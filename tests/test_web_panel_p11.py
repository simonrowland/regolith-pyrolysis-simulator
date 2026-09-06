from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from simulator.three_product_report import classify_products


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p11-deliverables.js"
INDEX = ROOT / "web/report_viewer/index.html"
SAMPLE = ROOT / "web/report_viewer/sample-run-artifact.json"
RUNNER_MARS = ROOT / "tests/fixtures/runner/mars_basalt_C2A_12h.json"
RUNNER_CI = ROOT / "tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json"
RUNNER_LUNAR = ROOT / "tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json"
_AUTO_ROWS = object()

GRADE_TOKENS = ("soda_lime", "container_sls", "optical_clear", "use_grade")
P15_ROUTE_CHIPS = (
    "qualified silica product",
    "flagged silica product",
    "flagged capture · not a product",
)


def _js_num(value: float | int) -> str:
    text = json.dumps(value)
    if re.fullmatch(r"-?\d+\.0", text):
        return text[:-2]
    return re.sub(r"e([+-])0+(\d)", r"e\1\2", text)


def _render(artifact: object, rows: object = _AUTO_ROWS, panel_source: str | None = None) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifactArg = process.argv[4];
const artifact = artifactArg.startsWith("{") || artifactArg.startsWith("[")
  ? JSON.parse(artifactArg)
  : JSON.parse(fs.readFileSync(artifactArg, "utf8"));
const rows = process.argv[5] === ""
  ? (artifact && Array.isArray(artifact.timesteps)
      ? artifact.timesteps.map((step) => step && step.summary)
      : [])
  : JSON.parse(process.argv[5]);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p11-deliverables");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    artifact_arg = str(artifact) if isinstance(artifact, Path) else json.dumps(artifact)
    rows_json = "" if rows is _AUTO_ROWS else json.dumps(rows)
    tmp_path = None
    panel_path = str(PANEL)
    if panel_source is not None:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        )
        handle.write(panel_source)
        handle.close()
        tmp_path = handle.name
        panel_path = tmp_path
    try:
        completed = subprocess.run(
            ["node", "-", str(LABELS), panel_path, artifact_arg, rows_json],
            input=harness,
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
    return completed.stdout


def _visible(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _card(rendered: str, name: str) -> str:
    match = re.search(
        rf'<article class="[^"]*" data-card="{re.escape(name)}">(.*?)</article>',
        rendered,
        re.DOTALL,
    )
    assert match is not None, f"missing card: {name}\n{rendered[:800]}"
    return match.group(1)


def _field(rendered: str, field: str) -> tuple[str, str]:
    match = re.search(
        rf'<div class="[^"]*" data-field="{re.escape(field)}" data-state="([^"]+)">(.*?)</div>',
        rendered,
        re.DOTALL,
    )
    assert match is not None, f"missing field {field}\n{rendered[:1200]}"
    return match.group(1), match.group(2)


def _field_value(rendered: str, field: str) -> str:
    _state, inner = _field(rendered, field)
    value = re.search(r"<b>(.*?)</b>", inner, re.DOTALL)
    assert value is not None, f"missing value for {field}"
    return value.group(1)


def _assert_traced_kg(rendered: str, field: str, amount: float, *, state: str | None = None) -> None:
    field_state, inner = _field(rendered, field)
    if state is not None:
        assert field_state == state, f"{field} state {field_state} != {state}"
    assert f'title="{_js_num(amount)} kg"' in inner, (
        f"{field} was not traced to emitted {amount!r}: {inner}"
    )


def _state_chips(fragment: str) -> list[str]:
    return re.findall(r'data-p11-state-chip="([^"]+)"', fragment)


def _assert_exclusive_silica_chips(card: str) -> list[str]:
    chips = _state_chips(card)
    assert len(chips) <= 1, f"two state chips on one silica figure: {chips}\n{card}"
    if "qualified silica product" in chips and "flagged capture · not a product" in chips:
        raise AssertionError(f"qualified and flagged chips both present\n{card}")
    return chips


def _runner_block(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload["product_classification"]
    assert "classification" in block, f"{path} is not the wrapped runner envelope"
    return copy.deepcopy(block)


def _wrapped(block: dict, extra_terminal: dict | None = None) -> dict:
    terminal = {"product_classification": block}
    if extra_terminal:
        terminal.update(extra_terminal)
    return {
        "header": {"run_id": "p11-fixture"},
        "timesteps": [],
        "terminal": terminal,
    }


def _mars() -> tuple[dict, dict]:
    block = _runner_block(RUNNER_MARS)
    return block, block["classification"]


def _ci() -> tuple[dict, dict]:
    block = _runner_block(RUNNER_CI)
    return block, block["classification"]


def _lunar() -> tuple[dict, dict]:
    block = _runner_block(RUNNER_LUNAR)
    return block, block["classification"]


def _flagged_silica_product_block() -> dict:
    authority = {
        "species_id": "SiO",
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {"kind": "eligible"},
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "pending_validation",
        "is_flux_active": True,
        "authority_level": "extrapolated",
        "valid_range_K": [1400.0, 2200.0],
        "reason": "outside certified SiO source band",
    }
    snapshots = (
        SimpleNamespace(
            c2a_staged_gas={
                "stage_name": "alkali_early_fe",
                "gas_cover_mode": "po2_hold",
            },
            condensed_by_stage_species_delta={},
            evap_flux=SimpleNamespace(carrier_authority_by_species={}),
        ),
        SimpleNamespace(
            c2a_staged_gas={
                "stage_name": "sio_window",
                "gas_cover_mode": "pn2_sweep",
            },
            condensed_by_stage_species_delta={(3, "SiO2"): 4.5},
            evap_flux=SimpleNamespace(
                carrier_authority_by_species={"SiO": authority}
            ),
        ),
    )
    silica = classify_products(SimpleNamespace(
        train=SimpleNamespace(stages=[
            None,
            None,
            None,
            SimpleNamespace(collected_kg={"SiO2": 4.5}),
        ]),
        record=SimpleNamespace(snapshots=snapshots),
    ))["pure_silica_glass"]
    return {
        "classification": {
            "pure_silica_glass": silica,
        },
        "markdown": "## 2. Pure silica glass\n",
    }


def test_p11_wrapped_runner_mars_renders_emitted_class_totals() -> None:
    block, classification = _mars()
    html = _render(_wrapped(block))
    metals = classification["metals_plus_O2"]
    silica = classification["pure_silica_glass"]
    mixed = classification["industrial_mixed_glass"]
    rump = classification["refractory_ceramic_rump"]
    volatiles = classification["captured_volatiles"]
    metals_card = _card(html, "metals")
    silica_card = _card(html, "silica")
    mixed_card = _card(html, "mixed-glass")
    rump_card = _card(html, "rump")
    volatiles_card = _card(html, "volatiles")

    assert 'id="sec-p11-deliverables"' in html
    assert "P2" in metals_card and "P4" in metals_card
    _assert_traced_kg(
        metals_card, "class_total_kg", metals["class_total_kg"], state="qualified-product"
    )
    _assert_traced_kg(metals_card, "O2_kg", metals["O2_kg"], state="qualified-product")
    _assert_traced_kg(
        silica_card,
        "class_total_kg",
        silica["class_total_kg"],
        state="flagged-unqualified-capture",
    )
    _assert_traced_kg(
        silica_card,
        "stage_3_capture_kg",
        silica["stage_3_capture_kg"],
        state="flagged-unqualified-capture",
    )
    chips = _assert_exclusive_silica_chips(silica_card)
    assert chips == ["flagged capture · not a product"]
    assert "qualified silica product" not in silica_card
    _assert_traced_kg(
        mixed_card,
        "class_total_kg",
        mixed["class_total_kg"],
        state="flagged-unqualified-capture",
    )
    assert "true" != _visible(_field_value(mixed_card, "early_tap_mode")).lower()
    assert mixed["note"] in _visible(_field_value(mixed_card, "note"))
    _assert_traced_kg(
        rump_card, "class_total_kg", rump["class_total_kg"], state="qualified-product"
    )
    _assert_traced_kg(
        rump_card,
        "rump_silicate_residual_kg",
        rump["rump_silicate_residual_kg"],
        state="flagged-unqualified-capture",
    )
    _assert_traced_kg(
        rump_card,
        "rump_total_kg",
        rump["rump_total_kg"],
        state="flagged-unqualified-capture",
    )
    assert "non-product inventory" in rump_card
    assert "sec-p11-card--rump" in html
    assert "physical floor" in block["markdown"] or "by physics" in block["markdown"]
    assert "by physics" in rump_card
    _assert_traced_kg(
        volatiles_card,
        "class_total_kg",
        volatiles["class_total_kg"],
        state="qualified-product",
    )
    _assert_traced_kg(
        volatiles_card, "kg_by_species.CO", volatiles["kg_by_species"]["CO"]
    )
    assert "not a product" in block["markdown"]
    assert "flagged capture · not a product" in silica_card
    assert "early-tap" in mixed_card
    assert not re.search(r">PURE<", html)
    assert "INDETERMINATE" not in html
    for token in GRADE_TOKENS:
        assert token not in html

    # 0+0 on as-shipped Mars is not a hidden pass for sibling sums (F6).
    attack = copy.deepcopy(block)
    attack_silica = attack["classification"]["pure_silica_glass"]
    attack_silica["class_total_kg"] = 3.0
    attack_silica["stage_3_capture_kg"] = 10.0
    attack_silica["stage_3_kg_by_species"] = {"SiO": 10.0}
    attack_card = _card(_render(_wrapped(attack)), "silica")
    _assert_traced_kg(attack_card, "class_total_kg", 3.0)
    _assert_traced_kg(attack_card, "stage_3_capture_kg", 10.0)
    assert 'title="13 kg"' not in attack_card
    assert 'title="13.0 kg"' not in attack_card
    assert _assert_exclusive_silica_chips(attack_card) == ["qualified silica product"]


def test_p11_absent_block_sample_is_not_emitted() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert "product_classification" not in sample.get("terminal", {})
    html = _render(SAMPLE)

    assert 'id="sec-p11-deliverables"' in html
    assert "Product classification not emitted" in html
    assert "not emitted" in html
    assert "0 kg" not in html
    assert "title=" not in html
    for token in GRADE_TOKENS:
        assert token not in html
    assert "soda_lime" not in html
    assert "qualified silica product" not in html
    assert "data-card=" not in html


def test_p11_partial_class_total_is_not_summed_from_species() -> None:
    block, classification = _mars()
    rump = classification["refractory_ceramic_rump"]
    species_sum = sum(rump["rump_kg_by_species"].values())
    rump.pop("class_total_kg")
    rump.pop("rump_total_kg")
    html = _render(_wrapped(block))
    rump_card = _card(html, "rump")
    state, inner = _field(rump_card, "class_total_kg")

    assert state == "not-emitted"
    assert "not emitted" in inner
    assert f'title="{_js_num(species_sum)} kg"' not in rump_card.split("<details")[0]
    assert f'title="{_js_num(rump["rump_kg_by_species"]["SiO2"])} kg"' in rump_card
    assert f'title="{_js_num(species_sum)} kg"' not in _field_value(rump_card, "class_total_kg")


def test_p11_unqualified_silica_consume_only_no_derived_third_number() -> None:
    block, classification = _mars()
    silica = classification["pure_silica_glass"]
    silica["stage_3_capture_kg"] = 4.5
    silica["class_total_kg"] = 1.0
    silica["stage_3_kg_by_species"] = {"SiO": 4.5}
    block["markdown"] = (
        "Stage 3 silica capture (not a product)\n"
        "Pure silica glass is not established.\n"
    )
    html = _render(_wrapped(block))
    silica_card = _card(html, "silica")
    derived = 4.5 - 1.0

    _assert_traced_kg(silica_card, "stage_3_capture_kg", 4.5)
    _assert_traced_kg(silica_card, "class_total_kg", 1.0, state="qualified-product")
    _assert_traced_kg(silica_card, "stage_3_kg_by_species.SiO", 4.5)
    assert f'title="{_js_num(derived)} kg"' not in html
    assert "unqualified_capture_kg" not in html
    assert "4.5−1" not in html and "4.5-1" not in html and "4.5 - 1" not in html
    assert _assert_exclusive_silica_chips(silica_card) == ["qualified silica product"]
    assert 'title="5.5 kg"' not in html


def test_p11_mutant_that_derives_capture_from_two_numbers_must_fail() -> None:
    block, classification = _mars()
    silica = classification["pure_silica_glass"]
    silica["stage_3_capture_kg"] = 4.5
    silica["class_total_kg"] = 1.0
    silica["stage_3_kg_by_species"] = {"SiO": 4.5}
    source = PANEL.read_text(encoding="utf-8")
    mutant = source.replace(
        'kgRow(row, "stage_3_capture_kg", "Stage 3 capture · kg", captureRole)',
        'kv("Derived capture · kg", exactValue(row.stage_3_capture_kg - row.class_total_kg, "kg"), '
        '"unqualified_capture_kg", "flagged-unqualified-capture")',
        1,
    )
    assert mutant != source
    html = _render(_wrapped(block), panel_source=mutant)
    assert 'data-field="unqualified_capture_kg"' in html
    assert 'title="3.5 kg"' in html


def test_p11_rump_split_does_not_headline_residual_as_ceramic_product() -> None:
    block, classification = _mars()
    rump = classification["refractory_ceramic_rump"]
    html = _render(_wrapped(block))
    rump_card = _card(html, "rump")
    headline = re.search(
        r'<div class="sec-p11-headline" data-field="class_total_kg" data-state="([^"]+)">(.*?)</div>',
        rump_card,
        re.DOTALL,
    )
    assert headline is not None
    assert headline.group(1) == "qualified-product"
    assert f'title="{_js_num(rump["class_total_kg"])} kg"' in headline.group(2)
    assert f'title="{_js_num(rump["rump_total_kg"])} kg"' not in headline.group(2)
    assert f'title="{_js_num(rump["rump_silicate_residual_kg"])} kg"' not in headline.group(2)
    silicate = _field(rump_card, "rump_silicate_residual_kg")
    assert silicate[0] == "flagged-unqualified-capture"
    assert "non-product inventory" in rump_card
    assert "sec-p11-card--error" not in html
    assert "red-soft" not in rump_card
    assert "--red" not in rump_card
    assert "warning" not in rump_card.lower()


def test_p11_mutant_that_headlines_silicate_residual_as_ceramic_product_must_fail() -> None:
    block, classification = _mars()
    rump = classification["refractory_ceramic_rump"]
    source = PANEL.read_text(encoding="utf-8")
    mutant = source.replace(
        '`<b>${quantityText(classClaim)}</b></div>`',
        '`<b>${quantityText(quantityClaim(row, "rump_silicate_residual_kg"))}</b></div>`',
        1,
    )
    assert mutant != source
    html = _render(_wrapped(block), panel_source=mutant)
    rump_card = _card(html, "rump")
    headline = re.search(
        r'<div class="sec-p11-headline" data-field="class_total_kg"[^>]*>(.*?)</div>',
        rump_card,
        re.DOTALL,
    )
    assert headline is not None
    assert f'title="{_js_num(rump["rump_silicate_residual_kg"])} kg"' in headline.group(1)
    assert f'title="{_js_num(rump["class_total_kg"])} kg"' not in headline.group(1)


def test_p11_mixed_glass_early_tap_false_is_labelled_not_indeterminate() -> None:
    block, classification = _mars()
    mixed = classification["industrial_mixed_glass"]
    assert mixed["early_tap_mode"] is False
    html = _render(_wrapped(block))
    mixed_card = _card(html, "mixed-glass")

    _assert_traced_kg(mixed_card, "class_total_kg", mixed["class_total_kg"])
    assert _field(mixed_card, "class_total_kg")[0] != "indeterminate"
    assert "no material" not in mixed_card
    assert "INDETERMINATE" not in mixed_card
    assert mixed["note"] in _visible(_field_value(mixed_card, "note"))
    assert "NOT a product class" in mixed_card
    assert not re.search(r">PURE<", mixed_card)
    class_label = _field(mixed_card, "class_total_kg")[1]
    assert "Qualified product" not in class_label
    assert "not a product" in class_label.lower()


def test_p11_unavailable_null_block_is_not_zero() -> None:
    html = _render(
        {"header": {"run_id": "p11-null"}, "timesteps": [], "terminal": {"product_classification": None}}
    )
    assert "attempted but unavailable" in html
    assert "no kilogram product totals are shown" in html
    assert "0 kg" not in html
    assert "title=" not in html
    assert "data-card=" not in html


def test_p11_does_not_read_p15_unwrapped_path() -> None:
    block, classification = _mars()
    block["pure_silica_glass"] = {
        "stage_3_capture_kg": 99.0,
        "class_total_kg": 88.0,
        "stage_3_kg_by_species": {"SiO": 99.0},
    }
    html = _render(_wrapped(block))
    silica_card = _card(html, "silica")
    real = classification["pure_silica_glass"]

    _assert_traced_kg(silica_card, "class_total_kg", real["class_total_kg"])
    _assert_traced_kg(silica_card, "stage_3_capture_kg", real["stage_3_capture_kg"])
    assert 'title="99 kg"' not in silica_card
    assert 'title="88 kg"' not in silica_card
    assert 'title="99.0 kg"' not in silica_card


def test_p11_unwrapped_only_block_is_not_sufficient() -> None:
    html = _render(
        _wrapped(
            {
                "pure_silica_glass": {
                    "stage_3_capture_kg": 4.0,
                    "class_total_kg": 0.0,
                    "stage_3_kg_by_species": {"SiO": 4.0},
                }
            }
        )
    )
    assert "Product classification buckets not emitted" in html
    assert 'title="4 kg"' not in html
    assert 'title="0 kg"' not in html
    assert "qualified silica product" not in html

    # Merge-after-classificationPresent must also fail this test (F6).
    block, classification = _mars()
    block["pure_silica_glass"] = {
        "stage_3_capture_kg": 99.0,
        "class_total_kg": 88.0,
        "stage_3_kg_by_species": {"SiO": 99.0},
    }
    present_html = _render(_wrapped(block))
    silica_card = _card(present_html, "silica")
    real = classification["pure_silica_glass"]
    _assert_traced_kg(silica_card, "class_total_kg", real["class_total_kg"])
    _assert_traced_kg(silica_card, "stage_3_capture_kg", real["stage_3_capture_kg"])
    assert 'title="88 kg"' not in silica_card
    assert 'title="99 kg"' not in silica_card
    assert 'title="88.0 kg"' not in silica_card
    assert 'title="99.0 kg"' not in silica_card


def test_p11_no_glass_grade_fabrication() -> None:
    html = _render(_wrapped(_runner_block(RUNNER_MARS)))
    mixed_card = _card(html, "mixed-glass")
    state, inner = _field(mixed_card, "glass_grade")
    assert state == "not-emitted"
    assert "not emitted" in inner
    for token in GRADE_TOKENS:
        assert token not in html


def test_p11_clarity_model_estimate_is_estimate_not_a_grade() -> None:
    block, classification = _mars()
    mixed = classification["industrial_mixed_glass"]
    mixed["clarity_model"] = {"confidence": "estimate", "clarity_grade": "optical_clear"}
    mixed["family_id"] = "container_sls_analog"
    html = _render(_wrapped(block))
    mixed_card = _card(html, "mixed-glass")
    grade = _visible(_field_value(mixed_card, "glass_grade"))

    assert "estimate" in grade.lower()
    assert "optical_clear" in grade
    assert "container_sls_analog" in grade
    assert "not a certification" in grade
    assert "grade table" not in html.lower()
    assert "ASTM" not in html
    assert "ISO" not in html


def test_p11_ci_runner_captured_volatiles_trace_emitted_species() -> None:
    block, classification = _ci()
    html = _render(_wrapped(block))
    volatiles = classification["captured_volatiles"]
    volatiles_card = _card(html, "volatiles")
    rump = classification["refractory_ceramic_rump"]

    _assert_traced_kg(volatiles_card, "class_total_kg", volatiles["class_total_kg"])
    _assert_traced_kg(volatiles_card, "kg_by_species.H2O", volatiles["kg_by_species"]["H2O"])
    _assert_traced_kg(
        _card(html, "rump"), "class_total_kg", rump["class_total_kg"], state="qualified-product"
    )


def test_p11_indeterminate_empty_stage_is_not_reverdicted_on_class_cards() -> None:
    block, classification = _mars()
    html = _render(
        _wrapped(
            block,
            extra_terminal={
                "stage_purity": {
                    "stage_3_sio_zone": {
                        "label": "SiO Zone",
                        "verdict": "INDETERMINATE",
                        "purity_fraction": None,
                        "total_kg": 0.0,
                    }
                }
            },
        )
    )
    silica_card = _card(html, "silica")
    mixed_card = _card(html, "mixed-glass")
    assert classification["industrial_mixed_glass"]["early_tap_mode"] is False
    assert "INDETERMINATE" not in silica_card
    assert "INDETERMINATE" not in mixed_card
    assert "no material" not in silica_card
    assert "no material" not in mixed_card
    assert not re.search(r">PURE<", html)
    assert "P4" in _card(html, "metals")


def test_p11_registry_and_index_wiring() -> None:
    check = subprocess.run(
        ["node", "--check", str(PANEL)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    index = INDEX.read_text(encoding="utf-8")
    html = _render({"header": {}, "timesteps": [], "terminal": {}})
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const ids = (context.ReportPanels || []).map((entry) => entry && entry.id);
process.stdout.write(JSON.stringify(ids));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == ["sec-p11-deliverables"]
    assert 'id="sec-p11-deliverables"' in html
    assert re.search(
        r'p10-vapor-source\.css">\s*<link rel="stylesheet" href="\./panels/p11-deliverables\.css">\s*<link rel="stylesheet" href="\./panels/p12-carrier-pressure\.css">',
        index,
    )
    assert re.search(
        r'p10-vapor-source\.js" defer></script>\s*<script src="\./panels/p11-deliverables\.js" defer></script>\s*<script src="\./panels/p12-carrier-pressure\.js" defer></script>',
        index,
    )


def test_p11_silica_state_chips_are_mutually_exclusive() -> None:
    mars_block, _ = _mars()
    mars_card = _card(_render(_wrapped(mars_block)), "silica")
    assert _assert_exclusive_silica_chips(mars_card) == ["flagged capture · not a product"]

    lunar_block, lunar_cls = _lunar()
    lunar_silica = lunar_cls["pure_silica_glass"]
    assert lunar_silica["stage_3_capture_kg"] > 0
    lunar_card = _card(_render(_wrapped(lunar_block)), "silica")
    # Classification axis, not certification: this C0 24 h golden never
    # executed the pO2-hold -> pN2 SiO-release switch (mandate section 5,
    # class 2 is on-demand by that switch), so its incidental Stage-3 capture
    # is non-product inventory and the chip is derived, not a snapshot. The
    # Separate certification flags do not change this no-switch classification.
    assert _assert_exclusive_silica_chips(lunar_card) == [
        "flagged capture · not a product"
    ]
    _assert_traced_kg(
        lunar_card,
        "stage_3_capture_kg",
        lunar_silica["stage_3_capture_kg"],
        state="flagged-unqualified-capture",
    )

    block, classification = _mars()
    silica = classification["pure_silica_glass"]
    silica["stage_3_capture_kg"] = 4.5
    silica["class_total_kg"] = 0.0
    silica["stage_3_kg_by_species"] = {"SiO": 4.5}
    block["markdown"] = "Stage 3 silica capture (not a product)\n"
    zero_card = _card(_render(_wrapped(block)), "silica")
    assert _assert_exclusive_silica_chips(zero_card) == ["flagged capture · not a product"]
    assert "qualified silica product" not in zero_card
    _assert_traced_kg(zero_card, "class_total_kg", 0.0, state="flagged-unqualified-capture")
    _assert_traced_kg(zero_card, "stage_3_capture_kg", 4.5, state="flagged-unqualified-capture")


def test_p11_mutant_that_emits_both_silica_route_chips_must_fail() -> None:
    source = PANEL.read_text(encoding="utf-8")
    mutant = source.replace(
        'return chip("qualified silica product");',
        'return chip("qualified silica product") + flagChip("flagged capture · not a product");',
        1,
    )
    assert mutant != source
    block, classification = _mars()
    classification["pure_silica_glass"]["class_total_kg"] = 3.0
    html = _render(_wrapped(block), panel_source=mutant)
    silica_card = _card(html, "silica")
    chips = _state_chips(silica_card)
    assert "qualified silica product" in chips
    assert "flagged capture · not a product" in chips
    assert len(chips) >= 2


def test_p11_switch_executed_class_total_tracks_flagged_stage_3_capture() -> None:
    silica = _flagged_silica_product_block()["classification"]["pure_silica_glass"]
    assert "switch_executed" not in silica
    assert silica["stage_3_capture_kg"] > 0
    assert silica["class_total_kg"] == silica["stage_3_capture_kg"]


def test_p11_switch_executed_extrapolated_silica_is_product_with_flag() -> None:
    block = _flagged_silica_product_block()
    classification = block["classification"]
    silica = classification["pure_silica_glass"]
    html = _render(_wrapped(block))
    silica_card = _card(html, "silica")
    assert _assert_exclusive_silica_chips(silica_card) == ["flagged silica product"]
    _assert_traced_kg(
        silica_card,
        "class_total_kg",
        silica["class_total_kg"],
        state="flagged-product",
    )
    _assert_traced_kg(
        silica_card,
        "stage_3_capture_kg",
        silica["stage_3_capture_kg"],
        state="flagged-product",
    )
    _assert_traced_kg(
        silica_card,
        "stage_3_kg_by_species.SiO2",
        silica["stage_3_kg_by_species"]["SiO2"],
        state="flagged-product",
    )
    assert "flagged capture · not a product" not in silica_card
    visible = _visible(silica_card)
    assert "authority: extrapolated" in visible
    assert "band: [1400,2200]" in visible
    assert "reason: outside certified SiO source band" in visible


def test_p11_indeterminate_classification_kg_is_no_material() -> None:
    block, classification = _mars()
    classification["pure_silica_glass"]["class_total_kg"] = {"verdict": "INDETERMINATE"}
    html = _render(_wrapped(block))
    silica_card = _card(html, "silica")
    state, inner = _field(silica_card, "class_total_kg")
    assert state == "indeterminate"
    assert "no material" in inner
    assert "title=" not in inner
    assert _assert_exclusive_silica_chips(silica_card) == ["flagged capture · not a product"]
    assert "qualified silica product" not in silica_card

    classification["pure_silica_glass"]["class_total_kg"] = "INDETERMINATE"
    string_card = _card(_render(_wrapped(block)), "silica")
    string_state, string_inner = _field(string_card, "class_total_kg")
    assert string_state == "indeterminate"
    assert "no material" in string_inner
    assert _assert_exclusive_silica_chips(string_card) == ["flagged capture · not a product"]


def test_p11_rump_class_total_is_not_hardcoded_floor_gloss() -> None:
    block, classification = _lunar()
    rump = classification["refractory_ceramic_rump"]
    # The producer intentionally defines the current class total as the
    # refractory floor. Give the viewer a distinct emitted value to prove it
    # copies class_total_kg instead of hardcoding the sibling floor field.
    rump["class_total_kg"] = rump["rump_refractory_oxides_kg"] + 1.25
    assert rump["class_total_kg"] != rump["rump_refractory_oxides_kg"]
    html = _render(_wrapped(block))
    rump_card = _card(html, "rump")
    headline = re.search(
        r'<div class="sec-p11-headline" data-field="class_total_kg" data-state="([^"]+)">(.*?)</div>',
        rump_card,
        re.DOTALL,
    )
    assert headline is not None
    label = headline.group(2).split("<b>")[0]
    assert "floor" not in label.lower()
    assert f'title="{_js_num(rump["class_total_kg"])} kg"' in headline.group(2)
    oxides_state, oxides_inner = _field(rump_card, "rump_refractory_oxides_kg")
    assert oxides_state == "qualified-product"
    assert "floor" in oxides_inner.lower()
    _assert_traced_kg(
        rump_card,
        "rump_refractory_oxides_kg",
        rump["rump_refractory_oxides_kg"],
    )


def test_p11_mutant_that_sums_silica_kg_must_fail_wrapped_runner() -> None:
    source = PANEL.read_text(encoding="utf-8")
    mutant = source.replace(
        'kgRow(row, "class_total_kg", classLabel, classRole)',
        'kv(classLabel, exactValue(row.class_total_kg + row.stage_3_capture_kg, "kg"), '
        '"class_total_kg", classRole)',
        1,
    )
    assert mutant != source
    block, _ = _mars()
    block["classification"]["pure_silica_glass"]["class_total_kg"] = 3.0
    block["classification"]["pure_silica_glass"]["stage_3_capture_kg"] = 10.0
    html = _render(_wrapped(block), panel_source=mutant)
    silica_card = _card(html, "silica")
    assert 'title="13 kg"' in silica_card
    assert 'title="3 kg"' not in _field_value(silica_card, "class_total_kg")


def test_p11_mutant_that_merges_p15_after_classification_present_must_fail() -> None:
    source = PANEL.read_text(encoding="utf-8")
    needle = (
        "    if (classificationPresent && isRecord(classificationValue)) {\n"
        "      classification = classificationValue;\n"
        "    }"
    )
    mutant = source.replace(
        needle,
        needle
        + "\n    if (isRecord(classification) && isRecord(block.pure_silica_glass)) {\n"
        "      classification.pure_silica_glass = block.pure_silica_glass;\n"
        "    }",
        1,
    )
    assert mutant != source
    block, _ = _mars()
    block["pure_silica_glass"] = {
        "stage_3_capture_kg": 99.0,
        "class_total_kg": 88.0,
        "stage_3_kg_by_species": {"SiO": 99.0},
    }
    html = _render(_wrapped(block), panel_source=mutant)
    silica_card = _card(html, "silica")
    assert 'title="88 kg"' in silica_card
    assert 'title="99 kg"' in silica_card
