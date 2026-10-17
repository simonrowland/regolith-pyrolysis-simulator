from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, StateTag
from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/nazarov-2015-dhofar-silicides.yaml"
TABLE_ROWS = {
    "Ca": ["1.93 × 10^-9", "6.11 × 10^-9", "1.89 × 10^-8", "5.87 × 10^-8", "1.14 × 10^-7"],
    "Al": ["1.33 × 10^-9", "3.29 × 10^-9", "7.51 × 10^-9", "2.98 × 10^-8", "6.07 × 10^-8"],
    "AlO": ["1.18 × 10^-9", "5.61 × 10^-9", "2.29 × 10^-8"],
    "SiO": ["6.95 × 10^-5", "1.32 × 10^-4", "2.28 × 10^-4", "5.77 × 10^-4", "8.87 × 10^-4"],
    "SiO2": ["1.58 × 10^-8", "3.94 × 10^-8", "8.88 × 10^-8", "2.08 × 10^-7", "4.43 × 10^-7"],
    "O": ["3.16 × 10^-10", "9.20 × 10^-10", "2.63 × 10^-9", "5.00 × 10^-9", "1.40 × 10^-8"],
    "O2": ["3.66 × 10^-12", "1.28 × 10^-11", "4.48 × 10^-11", "7.31 × 10^-11", "2.68 × 10^-10"],
}
TEMPERATURES_K = [1833, 1883, 1933, 1983, 2033]


def _table2_observations(document: dict) -> dict[str, dict]:
    return {
        formula: observation
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
        if observation.get("locator", {}).get("table") == "2"
    }


def _assert_table2_cells(document: dict) -> None:
    observations = _table2_observations(document)
    assert set(observations) == set(TABLE_ROWS)
    assert document["deepening"]["table_inventory"][1]["body_rows"] == 7
    assert document["deepening"]["table_inventory"][1]["numeric_cells"] == 33
    for formula, printed in TABLE_ROWS.items():
        observation = observations[formula]
        points = observation["values"]["points"]
        assert [point["T_K"] for point in points] == (
            TEMPERATURES_K if formula != "AlO" else TEMPERATURES_K[2:]
        )
        assert [point["p_atm_as_printed"] for point in points] == printed
        expected = [Decimal(text.replace(" × 10^", "e").replace(" ", "")) for text in printed]
        assert [Decimal(str(point["value"])) for point in points] == expected
        assert [Decimal(str(point["p_atm"])) for point in points] == expected
        assert all(point["locator"]["pdf_page_index"] == 4 for point in points)
        assert all(point["composition_mol"]["basis"] == "formula_derived_from_synthetic_anorthite" for point in points)


def _migrate() -> Migrator:
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_nazarov_table2_values_row_count_and_render_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table2_cells(document)

    observations = _table2_observations(document)
    assert observations["Ca"]["values"]["points"][0]["value"] == pytest.approx(1.93e-9)
    assert observations["SiO"]["values"]["points"][2]["value"] == pytest.approx(2.28e-4)
    assert observations["O2"]["values"]["points"][4]["value"] == pytest.approx(2.68e-10)
    assert observations["O"]["values"]["method_class"] == "measured_reduced"
    assert observations["O2"]["values"]["method_class"] == "measured_reduced"
    for formula in ("MoO2", "MoO3"):
        assert not document["species"][formula].get("observations")
        context = document["species"][formula]["context"][0]["values"]
        assert context["flag"] == "cell_contamination"
        assert context["pressure_in_table2"] == "not_tabulated"
    assert document["species"]["Si"]["context"][0]["values"]["row_count"] == 16

    mutated = deepcopy(document)
    mutated_point = mutated["species"]["SiO"]["observations"][0]["values"]["points"][2]
    mutated_point["value"] = mutated_point["p_atm"] = 2.29e-4
    with pytest.raises(AssertionError):
        _assert_table2_cells(mutated)


def test_nazarov_table2_real_migration_keeps_row_conditions() -> None:
    migrator = _migrate()
    observations = {
        observation.observation_id: observation
        for observation in migrator.result.observations.values()
        if "nazarov_2015_table2_" in observation.observation_id
    }
    assert len(observations) == 33
    reduced = [
        observation
        for observation in observations.values()
        if "table2_o_calculated" in observation.observation_id
        or "table2_o2_calculated" in observation.observation_id
    ]
    assert len(reduced) == 10
    assert all(
        observation.derivation is not None
        and observation.derivation.has_located_unprinted_raw_inputs
        for observation in reduced
    )

    for observation in observations.values():
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition.is_value
        assert observation.identity.composition.value is not None
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.fO2_Pa.is_unknown
        conditions = observation.point_conditions or {}
        assert conditions["temperature_K"].state.tag is StateTag.VALUE
        assert conditions["temperature_K"].locator is not None
        assert conditions["composition"].state.tag is StateTag.VALUE
        assert conditions["composition"].locator is not None
        assert conditions["composition"].state.value.basis == "formula_derived_from_synthetic_anorthite"
        for axis in ("total_pressure_Pa", "fO2_Pa"):
            assert conditions[axis].state.tag is StateTag.UNKNOWN
            assert conditions[axis].locator is not None

    validation = validate_corpus(
        migrator.result.works,
        migrator.result.experiments,
        migrator.result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()
