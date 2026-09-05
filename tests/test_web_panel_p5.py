from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p5-alkali-shuttle.js"
INDEX = ROOT / "web/report_viewer/index.html"
SAMPLE = ROOT / "web/report_viewer/sample-run-artifact.json"
_AUTO_ROWS = object()


def _render(artifact: object, rows: object = _AUTO_ROWS) -> str:
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
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p5-alkali-shuttle");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    artifact_arg = str(artifact) if isinstance(artifact, Path) else json.dumps(artifact)
    rows_json = "" if rows is _AUTO_ROWS else json.dumps(rows)
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), artifact_arg, rows_json],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(rendered: str, start: str, end: str) -> str:
    assert start in rendered, f"missing start marker: {start}"
    remainder = rendered.split(start, 1)[1]
    assert end in remainder, f"missing end marker after {start}: {end}"
    return remainder.split(end, 1)[0]


def _value_for_label(rendered: str, label: str) -> str:
    match = re.search(
        rf"<span>{re.escape(label)}</span><b>(.*?)</b>", rendered, re.DOTALL
    )
    assert match is not None, f"missing rendered label/value row: {label}"
    return match.group(1)


def _visible(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _species_card(rendered: str, species: str) -> str:
    encoded = html_lib.escape(species)
    for card in re.findall(
        r'<article class="sec-p5-species-card">(.*?)</article>', rendered, re.DOTALL
    ):
        if f">{encoded}</span>" in card or f">{html_lib.escape(encoded)}</span>" in card:
            return card
        pretty = species.replace("2", "₂")
        if f">{html_lib.escape(pretty)}</span>" in card:
            return card
    raise AssertionError(f"missing rendered species card: {species}")


def _has_species_card(rendered: str, species: str) -> bool:
    try:
        _species_card(rendered, species)
        return True
    except AssertionError:
        return False


def _credit_artifact(
    *,
    header_dose: dict | None = None,
    dose: dict | None = None,
    drawn: dict | None = None,
    outstanding: dict | None = None,
    na_hold: object = None,
    timesteps: list | None = None,
    extra_terminal: dict | None = None,
) -> dict:
    header: dict = {"run_id": "p5-fixture"}
    if header_dose is not None:
        header["c3_dose"] = header_dose
    metadata: dict = {}
    if dose is not None:
        metadata["c3_alkali_credit_dose_kg_by_species"] = dose
    if drawn is not None:
        metadata["c3_alkali_credit_drawn_kg_by_species"] = drawn
    if outstanding is not None:
        metadata["c3_alkali_credit_outstanding_kg_by_species"] = outstanding
    if na_hold is not None:
        metadata["c3_na_hold_adjustment"] = na_hold
    terminal: dict = {"run_metadata": metadata}
    if extra_terminal:
        terminal.update(extra_terminal)
    return {
        "header": header,
        "timesteps": timesteps or [],
        "terminal": terminal,
    }


def _hourly_summary(**overrides: object) -> dict:
    summary = {
        "hour": 32,
        "shuttle_phase": "inject",
        "shuttle_cycle": 1,
        "shuttle_injected_kg_hr": 0.4,
        "shuttle_reduced_kg_hr": 0.2,
        "shuttle_metal_produced_kg_hr": 0.15,
        "shuttle_Na_inventory_kg": 12.5,
        "shuttle_K_inventory_kg": 3.25,
    }
    summary.update(overrides)
    return summary


def test_p5_shipped_sample_renders_emitted_credits_without_hourly_or_hold() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    html = _render(SAMPLE)
    na_card = _species_card(html, "Na")
    k_card = _species_card(html, "K")
    na_drawn = sample["terminal"]["run_metadata"]["c3_alkali_credit_drawn_kg_by_species"]["Na"]
    na_outstanding = sample["terminal"]["run_metadata"][
        "c3_alkali_credit_outstanding_kg_by_species"
    ]["Na"]
    hourly = _between(
        html,
        '<div class="pending sec-p5-pending"><strong>Hourly shuttle readout not emitted</strong>',
        "</div>",
    )
    hold = _between(
        html,
        '<div class="pending sec-p5-pending"><strong>Na-hold adjustment not emitted</strong>',
        "</div>",
    )

    assert 'id="sec-p5-alkali-shuttle"' in html
    assert "Terminal credit line" in html
    assert "net makeup" in html
    assert "reagent-reservoir deficit" in html
    assert _visible(_value_for_label(na_card, "Commanded dose · kg")) == "140 kg"
    assert _visible(_value_for_label(na_card, "Credited dose · kg")) == "140 kg"
    assert f'title="{na_drawn} kg"' in _value_for_label(na_card, "Drawn · kg")
    assert f'title="{na_outstanding} kg"' in _value_for_label(
        na_card, "Outstanding · net makeup · kg"
    )
    assert _visible(_value_for_label(k_card, "Commanded dose · kg")) == "56 kg"
    assert _visible(_value_for_label(k_card, "Credited dose · kg")) == "56 kg"
    assert _visible(_value_for_label(k_card, "Drawn · kg")) == "0 kg"
    assert _visible(_value_for_label(k_card, "Outstanding · net makeup · kg")) == "0 kg"
    assert "-68" not in html
    assert "68.23" not in html
    assert "trace" not in k_card.lower()
    assert "unused" not in k_card.lower()
    assert "leftover" not in k_card.lower()
    assert "pending producer" not in html.lower()
    assert "Hourly shuttle readout not emitted" in html
    assert "shuttle_phase" not in hourly
    assert "0.4" not in hourly
    assert "Na-hold adjustment not emitted" in html
    assert "Na-hold adjustment" in html


def test_p5_all_absent_credit_line_is_not_engaged_without_invented_totals() -> None:
    html = _render({"header": {"run_id": "c0"}, "timesteps": [], "terminal": {}})

    assert "not engaged" in html.lower()
    assert "not emitted" in html.lower()
    assert "pending producer" not in html.lower()
    assert "140" not in html
    assert "208" not in html
    assert "0.0 kg" not in html
    assert "0 kg" not in html
    assert not _has_species_card(html, "Na")
    assert not _has_species_card(html, "K")


def test_p5_partial_path_does_not_copy_dose_into_drawn_or_outstanding() -> None:
    html = _render(
        _credit_artifact(
            header_dose={"Na_kg": 140.0},
            dose={"Na": 140.0},
        )
    )
    na_card = _species_card(html, "Na")
    drawn = _value_for_label(na_card, "Drawn · kg")
    outstanding = _value_for_label(na_card, "Outstanding · net makeup · kg")

    assert _visible(_value_for_label(na_card, "Commanded dose · kg")) == "140 kg"
    assert _visible(_value_for_label(na_card, "Credited dose · kg")) == "140 kg"
    assert "not emitted" in drawn
    assert "not emitted" in outstanding
    assert "140 kg" not in _visible(drawn)
    assert "0 kg" not in _visible(drawn)
    assert "140 kg" not in _visible(outstanding)

    drawn_only = _render(
        _credit_artifact(
            header_dose={"Na_kg": 140.0},
            dose={"Na": 140.0},
            drawn={"Na": 208.23138037211726},
        )
    )
    drawn_card = _species_card(drawn_only, "Na")
    outstanding_only = _value_for_label(
        drawn_card, "Outstanding · net makeup · kg"
    )
    assert 'title="208.23138037211726 kg"' in _value_for_label(drawn_card, "Drawn · kg")
    assert "not emitted" in outstanding_only
    assert "208.23138037211726" not in outstanding_only


def test_p5_hourly_fixture_renders_emitted_fields_without_summing_injected() -> None:
    artifact = _credit_artifact(
        header_dose={"Na_kg": 140.0},
        dose={"Na": 140.0},
        drawn={"Na": 10.0},
        outstanding={"Na": 10.0},
        timesteps=[
            {"hour": 10, "summary": _hourly_summary(hour=10, shuttle_injected_kg_hr=0.4)},
            {
                "hour": 20,
                "summary": _hourly_summary(
                    hour=20,
                    shuttle_phase="bakeout",
                    shuttle_cycle=2,
                    shuttle_injected_kg_hr=0.6,
                    shuttle_reduced_kg_hr=0.5,
                    shuttle_metal_produced_kg_hr=0.3,
                    shuttle_Na_inventory_kg=11.0,
                    shuttle_K_inventory_kg=3.0,
                ),
            },
        ],
    )
    html = _render(artifact)
    hourly = _between(html, '<div class="sec-p5-block sec-p5-hourly">', "</table></div></div>")

    assert "inject" in hourly
    assert "bakeout" in hourly
    assert "0.4 kg/h" in hourly
    assert "0.6 kg/h" in hourly
    assert "0.2 kg/h" in hourly
    assert "0.5 kg/h" in hourly
    assert "0.15 kg/h" in hourly
    assert "12.5 kg" in hourly
    assert "3.25 kg" in hourly
    assert "total drawn" not in hourly.lower()
    assert "<tfoot" not in hourly
    assert not re.search(r">1(?:\.0)? kg(?:/h)?<", hourly)
    assert "Hourly shuttle readout not emitted" not in html


def test_p5_na_hold_disclosure_keeps_status_and_reason() -> None:
    html = _render(
        _credit_artifact(
            header_dose={"Na_kg": 140.0},
            dose={"Na": 140.0},
            na_hold={
                "status": "unavailable",
                "reason": "na_fe_hold_window_authority_unavailable",
                "authority": "builtin",
                "configured_temperature_C": 1150.0,
            },
        )
    )
    hold = _between(html, "<summary>Na-hold adjustment</summary>", "</details>")

    assert _value_for_label(hold, "Status") == "unavailable"
    assert _value_for_label(hold, "Reason") == "na_fe_hold_window_authority_unavailable"
    assert _value_for_label(hold, "Authority") == "builtin"
    assert 'title="1150 °C"' in _value_for_label(hold, "configured temperature C")
    assert "Na-hold adjustment not emitted" not in html


def test_p5_partial_species_does_not_fabricate_the_missing_alkali() -> None:
    html = _render(
        _credit_artifact(
            header_dose={"Na_kg": 1.25},
            dose={"Na": 1.25},
            drawn={"Na": 1.25},
            outstanding={"Na": 1.25},
        )
    )

    assert _has_species_card(html, "Na")
    assert not _has_species_card(html, "K")
    assert "0 kg" not in html
    k_region = html.lower()
    assert "k unused" not in k_region
    assert ">K</span>" not in html


def test_p5_escapes_hostile_species_keys_and_payload_values() -> None:
    hostile_species = "Na<img src=x onerror=alert(1)>"
    html = _render(
        _credit_artifact(
            header_dose={f"{hostile_species}_kg": 4.0},
            dose={hostile_species: 4.0},
            drawn={hostile_species: 4.0},
            outstanding={hostile_species: 4.0},
            na_hold={
                "status": "unavailable",
                "reason": "A & B <hold>",
            },
        )
    )
    encoded = html_lib.escape(hostile_species)

    assert encoded in html
    assert hostile_species not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert _value_for_label(
        _between(html, "<summary>Na-hold adjustment</summary>", "</details>"),
        "Reason",
    ) == "A &amp; B &lt;hold&gt;"
    assert "&amp;amp;" not in html


def test_p5_does_not_fabricate_selectivity_or_k_refusal() -> None:
    html = _render(
        _credit_artifact(
            header_dose={"Na_kg": 140.0, "K_kg": 56.0},
            dose={"Na": 140.0, "K": 56.0},
            drawn={"Na": 208.23138037211726, "K": 0.0},
            outstanding={"Na": 208.23138037211726, "K": 0.0},
        )
    )
    lowered = html.lower()

    assert "refused" not in lowered
    assert "refusal" not in lowered
    assert "won" not in lowered
    assert "idle" not in lowered
    assert "pending producer" not in lowered
    assert "Hourly shuttle readout not emitted" in html
    assert _visible(_value_for_label(_species_card(html, "K"), "Drawn · kg")) == "0 kg"


def test_p5_registry_and_index_wiring() -> None:
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
    assert json.loads(completed.stdout) == ["sec-p5-alkali-shuttle"]
    assert 'id="sec-p5-alkali-shuttle"' in html
    assert re.search(
        r'p4-stage-purity\.css">\s*<link rel="stylesheet" href="\./panels/p5-alkali-shuttle\.css">\s*<link rel="stylesheet" href="\./panels/p6-mre\.css">',
        index,
    )
    assert re.search(
        r'p4-stage-purity\.js" defer></script>\s*<script src="\./panels/p5-alkali-shuttle\.js" defer></script>\s*<script src="\./panels/p6-mre\.js" defer></script>',
        index,
    )
