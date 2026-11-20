from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate
from tools import validate_literature_extracts as vle

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kems-006-zhang-2021"
SOURCE = ROOT / "data/literature/extracts/kems-006-zhang-2021.yaml"
OXIDES = ("Na2O", "MgO", "CaO", "TiO2", "SiO2", "Al2O3", "FeO", "K2O", "Rb2O")
RUNS = (
    ("Bas1400C0m1", 1400, "0", "zero-time (0 min)", "1.5", ("1.90", "7.11", "10.88", "1.54", "48.89", "15.29", "10.00", "2.50", "1.89")),
    ("Bas1400C15m", 1400, "15", "15 min", "1.5", ("0.66", "7.38", "11.25", "1.60", "50.31", "15.86", "10.15", "1.63", "1.12")),
    ("Bas1400C30m", 1400, "30", "30 min", "1.5", ("0.29", "7.45", "11.41", "1.61", "51.20", "16.16", "10.21", "1.19", "0.74")),
    ("Bas1400C60m", 1400, "60", "60 min", "1.5", ("0.01", "7.61", "11.46", "1.63", "52.55", "16.10", "10.28", "0.28", "0.10")),
    ("Bas1400C90m", 1400, "90", "90 min", "1.5", ("b.d.l.", "7.65", "11.50", "1.65", "52.55", "16.29", "10.30", "0.05", "b.d.l.")),
    ("Bas1200C0h2", 1200, "0", "zero-time (5 min setpoint hold)", "1.5", ("2.30", "6.46", "11.09", "1.57", "49.03", "15.41", "9.69", "2.58", "1.87")),
    ("Bas1200C1h", 1200, "55", "1 h (60 min nominal)", "1.5", ("1.99", "7.00", "10.98", "1.55", "48.43", "15.54", "10.05", "2.53", "1.93")),
    ("Bas1200C4h", 1200, "235", "4 h (240 min nominal)", "1.5", ("1.08", "7.19", "10.99", "1.57", "49.87", "15.54", "10.05", "2.15", "1.56")),
    ("Bas1200C10h", 1200, "595", "10 h (600 min nominal)", "1.5", ("0.69", "7.33", "11.13", "1.59", "50.27", "15.71", "10.17", "1.84", "1.27")),
    ("Bas1200C40h", 1200, "2395", "40 h (2400 min nominal)", "1.25", ("0.10", "7.61", "11.18", "1.64", "51.61", "15.74", "10.35", "1.09", "0.67")),
)
MISSING_CELL_REASONS = {
    "Na2O": "Table 1 prints b.d.l. for the Na2O_wt_pct cell; no numeric Na2O value is reported.",
    "Rb2O": "Table 1 prints b.d.l. for the Rb2O_wt_pct cell; no numeric Rb2O value is reported.",
}


def _document() -> dict:
    return yaml.safe_load(SOURCE.read_text(encoding="utf-8"))


def _series_rows(document: dict) -> dict[str, dict]:
    return {
        row["locator"]["row"]: row
        for row in document["species"]["Na"]["observations"]
        if row["observation_id"].startswith("zhang_2021_table1_residue_compositions_")
    }


def _assert_table1_pins(document: dict) -> None:
    rows = _series_rows(document)
    assert set(rows) == {sample for sample, *_ in RUNS}
    for sample, temperature, time_min, nominal_hold, radius, cells in RUNS:
        row = rows[sample]
        values = row["values"]
        assert values["quantity"] == "residue_component_composition"
        assert values["admission_status"] == "admitted"
        assert row["locator"]["page"] == 71 and row["locator"]["table"] == "1"
        assert sample in row["locator"]["note"]
        assert 'relative to zero-time runs' in row["locator"]["note"]
        point = values["series"][0]
        assert point["sample"] == sample
        assert point["nominal_hold_as_named"] == nominal_hold
        assert point["T_C"] == temperature
        for oxide, cell in zip(OXIDES, cells, strict=True):
            field = f"{oxide}_wt_pct"
            if cell == "b.d.l.":
                assert field not in point
            else:
                assert point[field] == cell
        time = point["point_conditions"]["time_min"]
        assert time["state"]["tag"] == "value"
        assert time["state"]["value"] == time_min
        assert time["locator"]["page"] == 71
        assert time["locator"]["table"] == "1"
        sample_radius = point["point_conditions"]["sample_radius_mm"]
        assert sample_radius["state"]["tag"] == "value"
        assert sample_radius["state"]["value"] == radius
        assert sample_radius["locator"]["page"] == 9
        assert sample_radius["locator"]["table"] == "S1"

    refusals = {
        row["values"]["composition_component"]: row
        for species in document["species"].values()
        for row in species["observations"]
        if row["values"].get("status") == "typed_refusal"
    }
    assert set(refusals) == set(MISSING_CELL_REASONS)
    for component, reason in MISSING_CELL_REASONS.items():
        refusal = refusals[component]
        values = refusal["values"]
        assert values["admission_status"] == values["status"] == "typed_refusal"
        assert values["printed_cell_as_published"] == "b.d.l."
        assert values["reason"] == reason
        assert refusal["T_C"] == 1400
        assert refusal["locator"]["page"] == 71
        assert refusal["locator"]["table"] == "1"
        assert refusal["locator"]["row"] == "Bas1400C90m"
        time = refusal["point_conditions"]["time_min"]
        assert time["state"]["tag"] == "value"
        assert time["state"]["value"] == "90"
        assert time["locator"]["page"] == 71
        radius = refusal["point_conditions"]["sample_radius_mm"]
        assert radius["state"]["tag"] == "value"
        assert radius["state"]["value"] == "1.5"
        assert radius["locator"]["page"] == 9
        assert radius["locator"]["table"] == "S1"


def test_table1_and_table_s1_cells_are_render_pinned_and_mutation_checked() -> None:
    document = _document()
    assert vle.validate_extract_document(document, expected_source_id=SOURCE_ID) == []
    _assert_table1_pins(document)

    changed_cell = deepcopy(document)
    _series_rows(changed_cell)["Bas1400C15m"]["values"]["series"][0]["SiO2_wt_pct"] = "50.32"
    with pytest.raises(AssertionError):
        _assert_table1_pins(changed_cell)

    changed_time = deepcopy(document)
    _series_rows(changed_time)["Bas1200C1h"]["values"]["series"][0]["point_conditions"]["time_min"]["state"]["value"] = "56"
    with pytest.raises(AssertionError):
        _assert_table1_pins(changed_time)

    changed_radius = deepcopy(document)
    _series_rows(changed_radius)["Bas1200C40h"]["values"]["series"][0]["point_conditions"]["sample_radius_mm"]["state"]["value"] = "1.5"
    with pytest.raises(AssertionError):
        _assert_table1_pins(changed_radius)


def test_isolated_migration_emits_every_oxide_with_located_time_and_radius(tmp_path: Path) -> None:
    document = _document()
    _assert_table1_pins(document)

    migrator = battery_migrate.Migrator(root=ROOT)
    migrator._migrate_extract(SOURCE)
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()

    residue = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SOURCE_ID
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value.value == "residue_component_composition"
        and "time_min" in (observation.point_conditions or {})
    ]
    observed = {}
    for observation in residue:
        conditions = observation.point_conditions or {}
        time = conditions["time_min"]
        radius = conditions["sample_radius_mm"]
        temperature = observation.identity.temperature_K
        assert temperature.is_value
        assert conditions["temperature_K"].state.value == temperature.value
        assert conditions["temperature_K"].locator.page == 71
        assert time.state.is_value and time.locator.page == 71
        assert radius.state.is_value and radius.locator.page == 9
        key = (temperature.value, time.state.value, observation.identity.species.formula)
        assert key not in observed
        observed[key] = observation

    assert len(residue) == 90
    for sample, temperature_C, time_min, _nominal_hold, radius_mm, cells in RUNS:
        temperature_K = Decimal(str(temperature_C)) + Decimal("273.15")
        for oxide, cell in zip(OXIDES, cells, strict=True):
            observation = observed.pop((temperature_K, Decimal(time_min), oxide))
            assert Decimal(str(observation.point_conditions["sample_radius_mm"].state.value)) == Decimal(radius_mm)
            if cell == "b.d.l.":
                assert observation.value.kind.value == "unavailable"
                assert observation.admission.status.value == "rejected"
                assert observation.admission.reason == MISSING_CELL_REASONS[oxide]
            else:
                assert observation.value.kind.value == "point"
                assert observation.value.point == Decimal(cell)
                assert observation.admission.status.value == "admitted"
        assert sample in _series_rows(document)
    assert observed == {}

    battery_migrate.write_outputs(migrator.result, root=tmp_path)
    generated = tmp_path / "data/literature/extracts-v2/kems-006-zhang-2021.yaml"
    assert generated.is_file()
    target = ROOT / "data/literature/extracts-v2/kems-006-zhang-2021.yaml"
    assert generated.read_bytes() == target.read_bytes()
