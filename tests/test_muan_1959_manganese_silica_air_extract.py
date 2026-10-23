"""Independent render pins and migration checks for Muan (1959)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/muan-1959-manganese-silica-air.yaml"
COMPONENTS = ("MnO", "Mn2O3", "SiO2")
STARTING_COMPONENTS = ("Mn3O4", "SiO2")

# Independent transcription from Table 1, PDF pp. 14-16, checked against the
# 600 dpi renders. Each row is (T_analysis, starting charge, MnO, Mn2O3, SiO2).
TABLE1_RENDER_GRID = {
    5: ("1574", ("99", "1"), ("38", "61", "1")),
    11: ("1566", ("98", "2"), ("44", "54", "2")),
    13: ("1551", ("95", "5"), ("51", "44", "5")),
    19: ("1515", ("90", "10"), ("45", "45", "10")),
    24: ("1420", ("80", "20"), ("51", "29", "20")),
    44: ("1315", ("71", "29"), ("58", "13", "29")),
    47: ("1297", ("70", "30"), ("59", "11", "30")),
    58: ("1260", ("68", "32"), ("53", "15", "32")),
    71: ("1269", ("60", "40"), ("53.5", "6.5", "40")),
    86: ("1311", ("56.7", "43.3"), ("52", "4", "44")),
    98: ("1381", ("55", "45"), ("51", "4", "45")),
}

TABLE2_RENDER_GRID = {
    (3, 1): {"MnO": "40", "Mn2O3": "60", "SiO2": None},
    (6, 1): {"MnO": "54", "Mn2O3": "13", "SiO2": "33"},
    (7, 1): {"MnO": "54", "Mn2O3": "12", "SiO2": "34"},
    (10, 1): {"MnO": "53", "Mn2O3": "5", "SiO2": "42"},
    (12, 1): {"MnO": "49", "Mn2O3": "2", "SiO2": "49"},
    (13, 1): {"MnO": "44", "Mn2O3": "1", "SiO2": "55"},
    (14, 2): {"MnO": "1", "Mn2O3": None, "SiO2": "99"},
    (16, 1): {"MnO": None, "Mn2O3": None, "SiO2": "100"},
}


def _observations(extract: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for payload in extract["species"].values()
        for row in payload.get("observations", [])
    }


def _table1_grid(extract: dict) -> dict[int, dict]:
    grid: dict[int, dict] = {}
    for row in _observations(extract).values():
        match = re.fullmatch(r"muan1959_t1_r(\d+)_([a-z0-9]+)", row["observation_id"])
        if match is None:
            continue
        row_number, _formula_slug = match.groups()
        values = row["values"]
        charge = values["point_conditions"]["composition"]["state"]["value"]["components"]
        charge_map = {formula: str(amount) for formula, amount in charge}
        entry = grid.setdefault(int(row_number), {"components": {}})
        entry["components"][values["formula"]] = str(values["component_amount"])
        entry["T_C"] = str(values["T_C"])
        entry["charge"] = (charge_map["Mn3O4"], charge_map["SiO2"])
    return grid


def _assert_table1_grid(extract: dict, expected: dict[int, tuple]) -> None:
    actual = _table1_grid(extract)
    assert set(actual) == set(expected)
    for row_number, (temperature, charge, amounts) in expected.items():
        assert actual[row_number] == {
            "T_C": temperature,
            "charge": charge,
            "components": dict(zip(COMPONENTS, amounts)),
        }


def _table2_grid(extract: dict) -> dict[tuple[int, int], dict[str, str | None]]:
    grid: dict[tuple[int, int], dict[str, str | None]] = {}
    for row in _observations(extract).values():
        match = re.match(r"muan1959_t2_r(\d+)_liquid(\d+)_", row["observation_id"])
        if match is None:
            continue
        row_number, liquid_number = map(int, match.groups())
        formula = row["values"]["formula"]
        raw_amount = row["values"].get("component_amount")
        grid.setdefault((row_number, liquid_number), {})[formula] = (
            None if raw_amount is None else str(raw_amount)
        )
    return grid


def test_muan_tables_match_independent_600_dpi_transcription() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table1_grid(extract, TABLE1_RENDER_GRID)
    assert _table2_grid(extract) == TABLE2_RENDER_GRID

    records = _observations(extract)
    assert len(records) == 57
    for row_number, liquid in ((3, 1), (14, 2), (16, 1)):
        for formula, amount in TABLE2_RENDER_GRID[(row_number, liquid)].items():
            suffix = "_unreported" if amount is None else ""
            observation_id = f"muan1959_t2_r{row_number:02d}_liquid{liquid}_{formula.lower()}{suffix}"
            assert observation_id in records
            if amount is None:
                assert records[observation_id]["values"]["unavailable_reason"]
    row13 = [r for r in records.values() if r["observation_id"].startswith("muan1959_t2_r13_")]
    row14 = [r for r in records.values() if r["observation_id"].startswith("muan1959_t2_r14_")]
    assert {r["experiment"] for r in row13 + row14} == {"muan1959-table2-row-13"}
    assert {r["values"]["assemblage_id"] for r in row13 + row14} == {"muan1959-t2-row-13"}

    contexts = [
        context
        for payload in extract["species"].values()
        for context in payload.get("context", [])
    ]
    table1_groups = [c for c in contexts if c["observation_id"].startswith("muan1959_t1_quench_group_")]
    assert sum(len(c["values"]["quench_runs"]) for c in table1_groups) == 126
    table2_rows = {
        int(match.group(1))
        for record in list(records.values()) + contexts
        if (match := re.match(r"muan1959_t2_(?:r|crystalline_transition_r)(\d+)", record["observation_id"]))
    }
    assert table2_rows == set(range(3, 17))

    xrd = next(c for c in contexts if c["observation_id"] == "muan1959_table3_xray_diffraction_crystalline_phases")
    phase_rows = xrd["values"]["phase_rows"]
    assert [len(phase["d_and_relative_intensity_pairs"]) for phase in phase_rows] == [12, 7, 16, 17]
    assert sum(len(phase["d_and_relative_intensity_pairs"]) for phase in phase_rows) == 52


def test_muan_table1_grid_pins_reject_render_and_extract_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    changed_render_grid = deepcopy(TABLE1_RENDER_GRID)
    changed_render_grid[5] = ("1574", ("99", "1"), ("38.1", "61", "1"))
    with pytest.raises(AssertionError):
        _assert_table1_grid(extract, changed_render_grid)

    changed_extract = deepcopy(extract)
    changed_row = _observations(changed_extract)["muan1959_t1_r005_mno"]
    changed_row["values"]["component_amount"] = "38.1"
    with pytest.raises(AssertionError):
        _assert_table1_grid(changed_extract, TABLE1_RENDER_GRID)


def test_muan_typed_rows_migrate_with_species_phase_temperature_and_conditions() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    declared = _observations(extract)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(migrated) == 57
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.values()) == 53
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 4

    for observation_id, row in migrated.items():
        source = declared[observation_id]
        values = source["values"]
        assert row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert row.identity.species.formula == values["formula"]
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.phase_role.value is PhaseRole.MELT
        assert row.identity.temperature_K.value == Decimal(values["T_C"]) + Decimal("273.15")
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == Decimal("21278.25")
        assert row.point_conditions["pO2_Pa"].state.value == Decimal("21278.25")
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert row.point_conditions["gas_mix"].state.value == "air"
        assert row.point_conditions["temperature_K"].state.value == row.identity.temperature_K.value
        assert row.experiment_id in migrator.result.experiments
        assert all(n.kind.value != "source_value_outside_physical_range" for n in row.notices)

        raw_amount = values.get("component_amount")
        if raw_amount is None:
            assert row.value.kind is ValueKind.UNAVAILABLE
            assert values.get("unavailable_reason")
            continue
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(str(raw_amount))

        if observation_id.startswith("muan1959_t1_"):
            composition = row.point_conditions["composition"]
            assert composition.state.is_unknown
            assert "unsupported or ambiguous components" in composition.state.reason
            assert row.identity.composition.is_unknown
            printed_charge = values["point_conditions"]["composition"]["state"]["value"]["components"]
            row_number = int(observation_id.split("_r", 1)[1].split("_", 1)[0])
            expected_charge = TABLE1_RENDER_GRID[row_number][1]
            assert tuple((formula, str(amount)) for formula, amount in printed_charge) == tuple(
                zip(STARTING_COMPONENTS, expected_charge)
            )

    figure_records = {
        context["observation_id"]: context
        for payload in extract["species"].values()
        for context in payload.get("context", [])
        if context.get("type") == "figure_context"
    }
    assert set(figure_records) == {
        "muan1959_fig1_phase_relations",
        "muan1959_fig2_braunite_solid_solution",
        "muan1959_fig3_pressure_dependent_braunite_sketch",
    }
    assert figure_records["muan1959_fig2_braunite_solid_solution"]["values"]["series"][0]["marker_count"] == 3
    assert figure_records["muan1959_fig1_phase_relations"]["values"]["series"][-1]["marker_count"] == 11
