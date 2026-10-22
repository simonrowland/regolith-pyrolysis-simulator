"""Independent Table 3/4 pins and migration checks for Feng et al. (2025)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import wt_pct_to_mole_fraction
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/feng-2025-fe-si-ca-v-ti-phase-diagrams.yaml"
COMPONENTS = ("FeO", "SiO2", "CaO", "V2O3", "TiO2")
STARTING_CHARGES = {
    "1": (
        ("5.29", "24.69", "42.02", "16.00", "12.00"),
        ("28.84", "13.58", "29.58", "16.00", "12.00"),
        ("42.79", "5.62", "23.59", "16.00", "12.00"),
        ("19.12", "17.99", "34.89", "16.00", "12.00"),
        ("52.18", "5.16", "14.66", "16.00", "12.00"),
        ("22.73", "24.81", "24.46", "16.00", "12.00"),
        ("47.05", "12.60", "12.35", "16.00", "12.00"),
        ("59.89", "5.91", "6.20", "16.00", "12.00"),
        ("40.68", "24.39", "6.93", "16.00", "12.00"),
        ("36.06", "17.86", "18.08", "16.00", "12.00"),
        ("48.88", "17.99", "5.13", "16.00", "12.00"),
        ("27.91", "19.25", "24.84", "16.00", "12.00"),
        ("11.11", "30.80", "30.09", "16.00", "12.00"),
        ("5.73", "42.04", "24.23", "16.00", "12.00"),
        ("29.88", "30.22", "11.90", "16.00", "12.00"),
        ("17.93", "36.62", "17.45", "16.00", "12.00"),
        ("6.09", "33.28", "32.63", "16.00", "12.00"),
        ("21.96", "43.59", "6.45", "16.00", "12.00"),
        ("10.87", "50.27", "10.86", "16.00", "12.00"),
        ("5.54", "59.67", "6.79", "16.00", "12.00"),
    ),
    "2": (
        ("49.00", "18.00", "5.00", "16.00", "12.00"),
        ("41.23", "25.77", "5.00", "16.00", "12.00"),
        ("39.08", "27.92", "5.00", "16.00", "12.00"),
        ("36.55", "30.45", "5.00", "16.00", "12.00"),
        ("33.50", "33.50", "5.00", "16.00", "12.00"),
        ("29.78", "37.22", "5.00", "16.00", "12.00"),
    ),
}

# Independent transcription of the 20 Liquid rows in each table. Table 3 was
# checked against PDF p. 5 at 600 dpi; Table 4 against PDF p. 7 at 600 dpi.
LIQUID_GRID = {
    "3": (
        ("6.9", "26.8", "44.0", "18.6", "3.7"),
        ("38.2", "23.1", "27.2", "6.0", "5.5"),
        ("25.7", "28.3", "37.0", "2.4", "6.6"),
        ("18.9", "35.4", "36.8", "3.8", "5.1"),
        ("58.3", "8.2", "22.6", "8.6", "2.3"),
        ("18.2", "31.9", "29.5", "6.3", "14.1"),
        ("29.7", "31.7", "30.5", "0.8", "7.3"),
        ("66.2", "12.0", "13.9", "0.9", "7.0"),
        ("36.7", "36.4", "10.4", "1.7", "14.8"),
        ("19.6", "28.5", "34.4", "5.0", "12.5"),
        ("45.9", "24.2", "14.3", "1.1", "14.5"),
        ("18.5", "36.5", "35.2", "3.0", "6.8"),
        ("11.4", "35.1", "32.2", "7.9", "13.4"),
        ("6.6", "46.6", "26.3", "7.5", "13.0"),
        ("32.8", "40.8", "14.5", "2.9", "9.0"),
        ("19.0", "45.8", "20.2", "6.0", "9.0"),
        ("5.6", "38.4", "37.3", "4.1", "14.6"),
        ("31.4", "42.4", "10.9", "3.6", "11.7"),
        ("16.6", "46.2", "18.9", "4.7", "13.6"),
        ("13.0", "42.7", "22.6", "3.5", "18.2"),
    ),
    "4": (
        ("20.7", "29.8", "37.1", "4.8", "7.6"),
        ("28.9", "33.0", "36.5", "1.2", "0.4"),
        ("34.0", "26.0", "35.2", "2.3", "2.5"),
        ("19.0", "33.4", "40.4", "2.4", "4.8"),
        ("53.3", "16.0", "23.5", "4.8", "2.4"),
        ("15.0", "38.8", "33.4", "1.3", "11.5"),
        ("34.5", "34.5", "26.1", "0.9", "4.0"),
        ("62.6", "16.3", "14.8", "1.3", "5.0"),
        ("39.6", "40.7", "14.6", "1.0", "4.1"),
        ("31.9", "31.1", "27.1", "2.1", "7.8"),
        ("45.3", "34.9", "15.4", "0.8", "3.6"),
        ("37.9", "20.2", "38.6", "1.1", "2.2"),
        ("21.4", "32.4", "37.6", "2.1", "6.5"),
        ("6.1", "59.4", "20.8", "1.9", "11.8"),
        ("31.2", "40.4", "19.3", "1.1", "8.0"),
        ("23.0", "42.9", "22.7", "4.7", "6.7"),
        ("5.9", "40.4", "38.7", "3.1", "11.9"),
        ("26.4", "47.1", "16.0", "3.3", "7.2"),
        ("18.6", "45.0", "21.3", "3.4", "11.7"),
        ("15.7", "57.0", "17.2", "2.4", "7.7"),
    ),
}


def _liquid_grid(extract: dict) -> dict[str, dict[int, dict[str, str]]]:
    grid: dict[str, dict[int, dict[str, str]]] = {"3": {}, "4": {}}
    for payload in extract["species"].values():
        for row in payload.get("observations", []):
            match = re.fullmatch(r"feng2025_t([34])_s(\d+)_liquid_.*", row["observation_id"])
            if match is None:
                continue
            table, sample = match.group(1), int(match.group(2))
            formula = row["values"]["formula"]
            assert formula not in grid[table].setdefault(sample, {})
            grid[table][sample][formula] = str(row["values"]["component_amount"])
    return grid


def _assert_grid_matches(extract: dict, expected: dict[str, tuple[tuple[str, ...], ...]]) -> None:
    grid = _liquid_grid(extract)
    for table, samples in expected.items():
        assert len(grid[table]) == 20
        for sample, amounts in enumerate(samples, 1):
            assert grid[table][sample] == dict(zip(COMPONENTS, amounts))


def test_feng_liquid_grids_match_independent_transcription() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_grid_matches(extract, LIQUID_GRID)


def test_feng_liquid_grid_pins_reject_grid_and_extract_mutations() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = deepcopy(LIQUID_GRID)
    changed_grid["3"] = (("6.91", *changed_grid["3"][0][1:]), *changed_grid["3"][1:])
    with pytest.raises(AssertionError):
        _assert_grid_matches(extract, changed_grid)

    changed_extract = deepcopy(extract)
    row = next(
        row
        for payload in changed_extract["species"].values()
        for row in payload.get("observations", [])
        if row["observation_id"] == "feng2025_t3_s01_liquid_feo"
    )
    row["values"]["component_amount"] = "6.91"
    with pytest.raises(AssertionError):
        _assert_grid_matches(changed_extract, LIQUID_GRID)


def test_feng_all_typed_rows_migrate_with_sample_conditions_and_species() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    declared_rows = {
        row["observation_id"]: row
        for payload in extract["species"].values()
        for row in payload.get("observations", [])
    }
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 204
    assert sum(row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION for row in observations) == 204

    for row in observations:
        assert row.value.kind is ValueKind.POINT
        local_id = row.observation_id.rsplit("::", 1)[-1]
        declared = declared_rows[local_id]
        assert row.identity.species.formula == declared["values"]["formula"]
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.phase_role.value is PhaseRole.SLAG
        assert row.identity.temperature_K.is_value
        assert row.identity.fO2_Pa.value == Decimal("1.01325e-5")
        assert row.point_conditions["pO2_Pa"].state.value == Decimal("1.01325e-5")
        assert row.point_conditions["temperature_K"].state.value == row.identity.temperature_K.value
        composition = row.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.locator is not None
        table = composition.locator.table
        assert composition.locator.note is not None
        sample = int(re.search(r"\d+", composition.locator.note).group())
        printed_charge = STARTING_CHARGES[table][sample - 1]
        expected_charge = wt_pct_to_mole_fraction(
            dict(zip(COMPONENTS, map(Decimal, printed_charge)))
        )
        assert row.identity.composition.is_value
        assert row.identity.composition.value == expected_charge
        assert composition.state.value == expected_charge
        assert row.uncertainty.value is None or row.uncertainty.value != 0
        assert all(notice.kind.value != "source_value_outside_physical_range" for notice in row.notices)

    assert {row.experiment_id.rsplit("::", 1)[-1] for row in observations} == {
        "feng-t3-equilibration",
        "feng-t4-equilibration",
        "feng-v-series-equilibration",
    }

    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in observations}
    assert by_id["feng2025_t3_s01_liquid_feo"].identity.temperature_K.value == Decimal("1673.15")
    assert by_id["feng2025_t4_s20_liquid_tio2"].identity.temperature_K.value == Decimal("1473.15")
    assert by_id["feng2025_fig8_liquid_v_v-3"].value.point == Decimal("1.84")
