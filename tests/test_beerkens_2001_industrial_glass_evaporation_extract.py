from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "beerkens-2001-industrial-glass-evaporation.yaml"
)
SOURCE_ID = "beerkens-2001-industrial-glass-evaporation"

# Independent transcription of all five furnace columns and thirteen rows
# from the 600-dpi render of PDF page 10.
TABLE3_RENDER = {
    "average glass melt surface temperature (CFD calculated)": (
        "1494", "1494", "1511", "1511", "1453"
    ),
    "surface area": ("39", "39", "39", "39", "60"),
    "Na2O content of glass": ("16.5", "16.5", "16.5", "16.5", "16.5"),
    "water vapor pressure in furnace atmosphere": (
        "0.6", "0.6", "0.6", "0.6", "0.6"
    ),
    "glass velocity above melt (CFD calculated)": (
        "0.5", "0.5", "0.7", "0.7", "0.7"
    ),
    "burner type": (
        "tube-in-tube", "tube-in-tube", "sheet flame", "sheet flame",
        "staged, tube-in-tube",
    ),
    "low momentum/high momentum": ("high", "high", "high", "high", "low"),
    "oxygen pressure above melt": ("0.03", "0", "0", "0", "0.005"),
    "CO-pressure above melt": ("0.001", "0.065", "0.065", "0.013", "<0.001"),
    "burner horizontal or directed to glass melt surface": (
        "horizontal", "7° directed", "7° directed", "7° directed", "horizontal"
    ),
    "dust emission": ("0.276", "0.398", "0.559", "0.485", "0.189"),
    "Na2O emission": ("0.096", "0.190", "0.315", "0.262", "0.087"),
    "NaOH concentration in furnace atmosphere": (
        "21.8", "26.0", "36.0", "38.5", "9.9"
    ),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table3_grid(document: dict) -> dict[str, tuple[str, ...]]:
    table = next(
        row
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "beerkens_2001_table3_other_rows"
    )
    return {
        row["label"]: tuple(row["values"])
        for row in table["values"]["rows"]
    }


def _assert_table3_matches_render(document: dict, render_grid: dict) -> None:
    assert _table3_grid(document) == render_grid


def _set_table3_cell(document: dict, label: str, index: int, value: str) -> None:
    table = next(
        row
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "beerkens_2001_table3_other_rows"
    )
    next(row for row in table["values"]["rows"] if row["label"] == label)[
        "values"
    ][index] = value


def _assert_typed_co_matches_render(document: dict, render_grid: dict) -> None:
    observations = document["species"]["CO"]["observations"]
    by_sample = {row["sample_id"]: row for row in observations}
    expected_pressure = render_grid["CO-pressure above melt"]
    expected_oxygen = render_grid["oxygen pressure above melt"]
    expected_temperature = render_grid[
        "average glass melt surface temperature (CFD calculated)"
    ]
    assert set(by_sample) == {"1", "2", "3", "4"}
    for index, sample_id in enumerate(("1", "2", "3", "4")):
        row = by_sample[sample_id]
        assert row["sample_id"] == sample_id
        assert row["values"]["glass_Na2O_content_wt_pct_as_printed"] == (
            render_grid["Na2O content of glass"][index]
        )
        assert row["values"]["partial_pressure_pa_as_printed"] == (
            expected_pressure[index] + " × 10^5 Pa"
        )
        oxygen = row["values"]["oxygen_partial_pressure"]
        assert oxygen["as_printed"] == expected_oxygen[index] + " × 10^5 Pa"
        assert row["values"]["T_C"] == expected_temperature[index]


def test_render_grid_binding_and_mutations() -> None:
    document = _document()
    assert _table3_grid(document) == TABLE3_RENDER
    _assert_table3_matches_render(document, TABLE3_RENDER)
    _assert_typed_co_matches_render(document, TABLE3_RENDER)

    # A changed independent render transcription fails the grid assertion.
    changed_render = deepcopy(TABLE3_RENDER)
    changed_render["Na2O emission"] = ("0.096", "0.190", "0.316", "0.262", "0.087")
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(document, changed_render)

    # A changed extract cell fails the independently transcribed render grid.
    changed_extract = deepcopy(document)
    _set_table3_cell(changed_extract, "Na2O emission", 2, "0.316")
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(changed_extract, TABLE3_RENDER)

    # A mutation to the typed row projection also breaks the render binding.
    changed_pressure = deepcopy(document)
    changed_pressure["species"]["CO"]["observations"][0]["values"][
        "partial_pressure_pa_as_printed"
    ] = "0.00101 × 10^5 Pa"
    with pytest.raises(AssertionError):
        _assert_typed_co_matches_render(changed_pressure, TABLE3_RENDER)


def test_species_conditions_fidelity_and_isolated_migration() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    mutated["species"]["CO"]["observations"][0]["values"]["partial_pressure_pa"] = "101"
    assert check_all_fidelity_samples_match(mutated, label=SOURCE_ID)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    rows = tuple(migrator.result.observations.values())
    assert len(rows) == 5
    assert {row.identity.species.formula for row in rows} == {"CO", "NaCl"}

    co_rows = sorted(
        (row for row in rows if row.identity.species.formula == "CO"),
        key=lambda row: row.observation_id,
    )
    assert len(co_rows) == 4
    expected = (
        ("100", "1494", "3000", "0.5"),
        ("6500", "1494", "0", "0.5"),
        ("6500", "1511", "0", "0.7"),
        ("1300", "1511", "0", "0.7"),
    )
    for row, (pressure, temp_c, oxygen_pa, velocity) in zip(co_rows, expected, strict=True):
        assert row.identity.quantity.value is Quantity.P_PARTIAL
        assert row.identity.species.phase.value == "g"
        assert row.value.point == Decimal(pressure)
        assert row.identity.temperature_K is not None
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_C"].state.value == Decimal(temp_c)
        assert row.point_conditions["fO2_Pa"].state.value == Decimal(oxygen_pa)
        assert row.point_conditions["water_vapor_pressure_Pa"].state.value == Decimal("60000")
        assert row.point_conditions["gas_velocity_m_s"].state.value == Decimal(velocity)
        assert row.point_conditions["composition"].state.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown

    salt = next(row for row in rows if row.identity.species.formula == "NaCl")
    assert salt.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    assert salt.value.point == Decimal("0.5")
    assert salt.value.approximate is True
    assert salt.identity.temperature_K is not None
    assert salt.identity.temperature_K.is_unknown
    assert salt.point_conditions is not None
    for condition in ("temperature_C", "fO2_Pa", "total_pressure_Pa", "composition"):
        assert salt.point_conditions[condition].state.is_unknown
