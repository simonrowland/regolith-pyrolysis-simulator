from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import PhaseRole, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sineva-2023-ni-sn-zn-slag-copper.yaml"

# Independent Table II/III render transcription pins. PDF pages 7, 9, and 10
# were rendered at 600 dpi; Table II's dropped minus signs were restored only
# after checking the printed glyphs on those renders.
RENDER_PINS = {
    "sineva23_tii_exp1_metal_cu2o": ("Cu2O", Decimal("96.0"), 7, Decimal("-7.1")),
    "sineva23_tii_exp1_slag_feo": ("FeO", Decimal("61.9"), 7, Decimal("-7.1")),
    "sineva23_tiii_exp56_slag_cao": ("CaO", Decimal("14.4"), 10, Decimal("-8.34")),
    "sineva23_tii_exp5_wüstite_feo": ("FeO", Decimal("97.0"), 7, Decimal("-9.0")),
}
RUN_PRODUCTS_RENDER_PINS = {
    ("II", "1"): "Metal + Slag + Spinel",
    ("II", "5"): "Metal + Slag + Wüstite",
    ("II", "13"): "Metal + Slag + Tridymite",
    ("III", "45"): "Metal + Slag + Ca2Fe2O5",
}
RUN_COMPOSITION_UNKNOWN_REASON = (
    "Per-run starting/bulk composition is not numerically reported; "
    "initial mixtures were selected using FactSage calculations."
)


def _observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    for observation_id, (formula, number, page, oxygen) in RENDER_PINS.items():
        row = observations[observation_id]
        assert row["values"]["component_as_published"] == formula
        assert Decimal(str(row["values"]["value"])) == number
        assert Decimal(str(row["values"]["log_fO2"]["value"])) == oxygen
        assert row["locator"]["page"] == page
        assert row["locator"]["pdf_page_index"] == page - 1
        assert row["values"]["log_fO2"]["units"] == "log10(atm)"


def _assert_phase_role_source_binding(row: dict, printed_phase: str) -> None:
    assert row["phase_as_published"] == printed_phase
    assert row["values"].get("phase_label") == printed_phase
    assert row["values"].get("basis") == "wt_percent"
    composition = row["point_conditions"]["composition"]
    assert composition["state"]["tag"] == "unknown"
    assert composition["state"]["reason"] == RUN_COMPOSITION_UNKNOWN_REASON
    assert composition["locator"]["published_page"] == 4256
    assert composition["locator"]["section"] == "Experimental Methodology"
    run_products = row["point_conditions"]["assemblage_as_published"]
    assert run_products["state"]["tag"] == "value"
    assert run_products["locator"]["table"] == row["locator"]["table"]
    assert run_products["locator"]["row"] == row["locator"]["row"]


def test_sineva_tables_match_render_and_all_rows_migrate_with_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    observations = _observations(document)
    assert len(observations) == 696
    _assert_render_pins(document)
    wustite_rows = [row for row in observations.values() if row["phase_as_published"] == "Wüstite"]
    assert len(wustite_rows) == 48
    assert {row["point_conditions"]["sample_id"]["state"]["value"] for row in wustite_rows} == {
        "5", "6", "11", "12", "24", "25", "26", "31", "48", "49", "50", "62"
    }

    figures = [row for row in document["species"]["SiO2"]["context"] if row["type"] == "figure_record"]
    assert len(figures) == 7
    assert {row["locator"]["figure"] for row in figures} == {str(i) for i in range(1, 8)}
    assert all(row["values"]["caption"] and row["values"]["digitisation"] for row in figures)
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])
    figure5 = next(row for row in figures if row["locator"]["figure"] == "5")
    assert figure5["values"]["printed_bound_as_published"] == "The difference is < 0.3 in log10L values."

    changed = deepcopy(document)
    changed_row = _observations(changed)["sineva23_tii_exp5_wüstite_feo"]
    changed_row["values"]["value"] = 97.1
    with pytest.raises(AssertionError):
        _assert_render_pins(changed)

    role_row = _observations(document)["sineva23_tii_exp10_metal_cu2o"]
    _assert_phase_role_source_binding(role_row, "Metal")
    changed_role = deepcopy(role_row)
    changed_role["values"].pop("phase_label")
    with pytest.raises(AssertionError):
        _assert_phase_role_source_binding(changed_role, "Metal")
    changed_composition = deepcopy(role_row)
    changed_composition["point_conditions"].pop("composition")
    with pytest.raises(KeyError):
        _assert_phase_role_source_binding(changed_composition, "Metal")
    changed_run_products = deepcopy(role_row)
    changed_run_products["point_conditions"].pop("assemblage_as_published")
    with pytest.raises(KeyError):
        _assert_phase_role_source_binding(changed_run_products, "Metal")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 696
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind is ValueKind.POINT for row in migrated)

    by_id = {row.observation_id.split("::", 1)[-1]: row for row in migrated}
    for observation_id, (formula, number, _page, oxygen) in RENDER_PINS.items():
        row = by_id[observation_id]
        assert row.value.point == number
        assert row.identity.species.formula == formula
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1523.15")
        for key, expected in (("temperature_K", Decimal("1523.15")), ("fO2_log", oxygen)):
            condition = row.point_conditions[key]
            assert condition.state.is_value
            assert condition.state.value == expected
            assert condition.locator is not None

    role_rows = [
        row
        for row in observations.values()
        if row.get("values", {}).get("phase_label") in {"Metal", "Slag"}
        and row.get("locator", {}).get("table") in {"II", "III"}
    ]
    assert len(role_rows) == 122
    assert sum(row["values"]["phase_label"] == "Metal" for row in role_rows) == 61
    assert sum(row["values"]["phase_label"] == "Slag" for row in role_rows) == 61
    runs: dict[tuple[str, str], set[str]] = {}
    for source_row in role_rows:
        observation_id = source_row["observation_id"]
        printed_phase = source_row["phase_as_published"]
        _assert_phase_role_source_binding(source_row, printed_phase)
        conditions = source_row["point_conditions"]
        run_key = (
            source_row["locator"]["table"],
            conditions["sample_id"]["state"]["value"],
        )
        runs.setdefault(run_key, set()).add(
            conditions["assemblage_as_published"]["state"]["value"]
        )
        row = by_id[observation_id]
        expected_role = PhaseRole(printed_phase.casefold())
        assert row.identity.phase_role.is_value
        assert row.identity.phase_role.value is expected_role
        assert row.identity.species.phase.value.value == "l"
        assert row.identity.subtype.is_value
        assert row.identity.subtype.value == "wt_percent"
        composition = row.point_conditions["composition"]
        assert composition.state.is_unknown
        assert composition.state.reason == RUN_COMPOSITION_UNKNOWN_REASON
        assert composition.locator is not None
        oxygen = row.point_conditions["fO2_log"]
        assert oxygen.state.is_value
        assert isinstance(oxygen.state.value, Decimal)
        assert oxygen.locator is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1523.15")
        assert temperature.locator is not None

    assert len(runs) == 61
    assert all(len(assemblages) == 1 for assemblages in runs.values())
    for run_key, expected in RUN_PRODUCTS_RENDER_PINS.items():
        assert runs[run_key] == {expected}
    printed_assemblages = [next(iter(assemblages)) for assemblages in runs.values()]
    assert all(assemblage.startswith("Metal + Slag + ") for assemblage in printed_assemblages)
    assert not any(
        token in assemblage.casefold()
        for assemblage in printed_assemblages
        for token in ("sulfide", "sulphide", "fes", "matte", "globule")
    )

    for row in migrated:
        assert row.identity.species.formula in document["species"]
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["fO2_log"].locator is not None
        assert not any("source_value_outside_physical_range" in notice for notice in row.notices)

    wustite = by_id["sineva23_tii_exp5_wüstite_feo"]
    assert wustite.value.point == Decimal("97.0")
    assert wustite.identity.species.formula == "FeO"
    assert wustite.identity.species.phase.value.value == "cr"
    assert wustite.point_conditions["fO2_log"].state.value == Decimal("-9.0")
