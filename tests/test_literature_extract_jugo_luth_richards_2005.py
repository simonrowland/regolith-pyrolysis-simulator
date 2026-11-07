"""Rendered-cell pins and isolated migration for Jugo et al. (2005)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[1]
SID = "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
RENDER_PINS = {
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_2_sio2": (
        "SiO2", "47.4", "47.4 (2)", "2", "SiO2"
    ),
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_2_s": (
        "S", "0.14", "0.14 (2)", "2", "S"
    ),
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_sio2": (
        "SiO2", "46.3", "46.3 (5)", "30A", "SiO2"
    ),
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_s": (
        "S", "1.34", "1.34 (7)", "30A", "S"
    ),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block["observations"]
    }


def _assert_paper_method_locators(document: dict) -> None:
    observations = _source_observations(document)
    expected_method_locator = {
        "page": 5,
        "pdf_page_index": 4,
        "published_page": 787,
        "section": "Analytical techniques",
    }
    assert len(observations) == 198
    assert all(
        row["values"]["method_locator"] == expected_method_locator
        for row in observations.values()
    )

    context = document["species"]["S"]["context"]
    mineral_method_rows = [
        row for row in context if "method_locator" in row.get("values", {})
    ]
    assert len(mineral_method_rows) == 4
    assert all(
        row["values"]["method_locator"] == expected_method_locator
        for row in mineral_method_rows
    )

    sulfide = next(
        row for row in context
        if row["observation_id"].endswith("_sulfide_pooled_composition_context")
    )
    assert sulfide["locator"] == {
        "page": 6,
        "pdf_page_index": 5,
        "published_page": 788,
        "section": "Sulfides",
        "note": "Pooled composition, n=12; no run assignment for individual analyses.",
    }


def _assert_render_pins(document: dict) -> None:
    observations = _source_observations(document)
    context = document["species"]["S"]["context"]
    assert len(observations) == 198
    assert len(document["experiments"]) == 22
    assert len(context) == 10
    _assert_paper_method_locators(document)

    for observation_id, (species, value, printed, run_id, column) in RENDER_PINS.items():
        row = observations[observation_id]
        assert row["locator"]["page"] == 8
        assert row["locator"]["table"] == "4"
        assert row["locator"]["row"] == run_id
        assert row["locator"]["column"] == column
        assert row["phase"] == "glass"
        assert row["units"] == "wt%"
        assert row["values"]["formula"] == species
        assert row["values"]["value"] == value
        assert row["values"]["value_as_published"] == printed
        assert row["quench_statement"]["preservation"] == "not_stated"

    table4_totals = next(
        row for row in context
        if row["observation_id"].endswith("_table4_calculated_totals_context")
    )
    assert len(table4_totals["values"]["rows_as_printed"]) == 22
    assert table4_totals["values"]["calculation_as_printed"] == "Totals are calculated with S as SO3."

    table2 = next(
        row for row in context
        if row["observation_id"].endswith("_table2_capsules_and_charge_masses_context")
    )
    capsules = table2["values"]["rows_as_printed"][0]
    assert capsules["27"] == "C–Pt"
    assert capsules["35B"] == "C–Pt"
    assert table2["values"]["footnote_as_printed"].endswith("C–Pt, graphite-lined platinum capsules.")

    run_2 = next(row for row in document["experiments"] if row["experiment_id"] == "run_2")
    assert run_2["temperature_C_as_printed"] == "1355"
    assert run_2["pressure_GPa_as_printed"] == "1.6"
    assert run_2["duration_h_as_printed"] == "24"
    assert run_2["locator"]["page"] == 7
    assert run_2["locator"]["table"] == "3"


def _migrated_by_id(result) -> dict:
    return {
        row.observation_id.rsplit("::", 1)[1]: row
        for row in result.observations.values()
        if row.source_id == SID
    }


def test_rendered_table_cells_and_run_count_are_pinned() -> None:
    _assert_render_pins(_document())


def test_mutating_a_rendered_table_cell_breaks_the_pin() -> None:
    document = _document()
    _assert_render_pins(document)
    mutated = deepcopy(document)
    _source_observations(mutated)[
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_2_s"
    ]["values"]["value"] = "0.15"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_mutating_paper_method_pages_breaks_the_pin() -> None:
    mutated = deepcopy(_document())
    for row in _source_observations(mutated).values():
        row["values"]["method_locator"]["page"] = 999
    context = mutated["species"]["S"]["context"]
    for row in context:
        if "method_locator" in row.get("values", {}):
            row["values"]["method_locator"]["page"] = 999
        if row["observation_id"].endswith("_sulfide_pooled_composition_context"):
            row["locator"]["page"] = 999
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_isolated_migration_keeps_printed_values_conditions_and_identity_axes() -> None:
    document = _document()
    _assert_render_pins(document)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    migrated = _migrated_by_id(result)
    assert len(migrated) == 198
    assert sum(row.value.kind.value == "unavailable" for row in migrated.values()) == 0
    assert sum(row.value.kind.value == "point" for row in migrated.values()) == 198
    assert {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    } == set()
    assert {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species.phase.is_unknown
    } == set()

    required_conditions = {"temperature_K", "total_pressure_Pa", "composition", "fO2_control"}
    for row in migrated.values():
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.composition.is_value
        assert row.identity.fO2_Pa.is_unknown
        assert required_conditions <= set(row.point_conditions)
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["fO2_control"].locator.page in {7, 10}
        assert row.identity.composition.value == row.point_conditions["composition"].state.value

    # These rendered values are checked after the real migrator has parsed them.
    expected_values = {
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_2_sio2": Decimal("47.4"),
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_2_s": Decimal("0.14"),
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_sio2": Decimal("46.3"),
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_s": Decimal("1.34"),
    }
    for observation_id, expected in expected_values.items():
        row = migrated[observation_id]
        assert row.value.point == expected
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.composition.is_value
        assert row.point_conditions["temperature_K"].locator.page == 7
        assert row.point_conditions["total_pressure_Pa"].locator.page == 7

    run_2_si = migrated[next(key for key in expected_values if key.endswith("run_2_sio2"))]
    assert run_2_si.identity.temperature_K.value == Decimal("1628.15")
    assert run_2_si.identity.total_pressure_Pa.value == Decimal("1600000000.0")
    assert run_2_si.point_conditions["temperature_K"].locator.table == "3"
    assert run_2_si.point_conditions["total_pressure_Pa"].locator.table == "3"
    assert run_2_si.point_conditions["composition"].locator.page == 3
    assert run_2_si.point_conditions["composition"].locator.table == "1"

    run_30a_s = migrated[
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_s"
    ]
    assert run_30a_s.identity.temperature_K.value == Decimal("1588.15")
    assert run_30a_s.identity.total_pressure_Pa.value == Decimal("1200000000.0")
    assert run_30a_s.point_conditions["composition"].locator.page == 4
    assert run_30a_s.point_conditions["composition"].locator.table == "2"
    assert run_30a_s.point_conditions["fO2_control"].state.is_unknown
    assert "30A" in run_30a_s.point_conditions["fO2_control"].state.reason

    run_26_s = migrated[
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_26_s"
    ]
    assert run_26_s.point_conditions["fO2_control"].locator.page == 10
    assert run_26_s.point_conditions["fO2_control"].locator.table == "5"
    assert run_26_s.point_conditions["fO2_control"].state.is_value
    assert run_26_s.identity.fO2_Pa.is_unknown

    # Verify the sulfur-bearing starting bulk reaches identity through the existing converter.
    printed_30a = _source_observations(document)[
        "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation_table4_run_30A_s"
    ]
    printed_map = {
        formula: Decimal(str(amount))
        for formula, amount in printed_30a["point_conditions"]["composition"]["state"]["value"]["components"]
    }
    expected_30a_composition = wt_pct_to_mole_fraction(printed_map)
    assert run_30a_s.identity.composition.value == expected_30a_composition
    assert "SO3" in dict(run_30a_s.identity.composition.value.components)
