from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/nbs-1981-tn270-8-alkali-thermochemistry.yaml"
)

EXPECTED_ROW_CELL_COUNTS = {
    ("NaO2", "c"): 5,
    ("NaO2", "g"): 3,
    ("Na2O", "c"): 5,
    ("Na2O", "g"): 5,
    ("Na2O2", "c"): 5,
    ("Na2SiO3", "c"): 3,
    ("Na2SiO3", "gl"): 1,
    ("Na2Si2O5", "c"): 3,
    ("Na2Si2O5", "c2"): 1,
    ("Na2Si2O5", "c3"): 1,
    ("Na2Si2O5", "gl"): 1,
    ("Na4SiO4", "c"): 1,
    ("Na6Si2O7", "c"): 1,
    ("KO2", "c"): 4,
    ("K2O", "c"): 1,
    ("K2O", "g"): 1,
    ("K2O2", "c"): 3,
    ("K2O2", "g"): 1,
    ("K2SiO3", "c"): 3,
    ("K2Si2O5", "c"): 3,
}

H298_MINUS_H0_IDS = frozenset(
    {
        "nbs81_table99_NaO2_c_H298_minus_H0",
        "nbs81_table99_NaO2_g_H298_minus_H0",
        "nbs81_table99_Na2O_c_H298_minus_H0",
        "nbs81_table99_Na2O_g_H298_minus_H0",
        "nbs81_table99_Na2O2_c_H298_minus_H0",
        "nbs81_table100_K2SiO3_c_H298_minus_H0",
        "nbs81_table100_K2Si2O5_c_H298_minus_H0",
    }
)

RENDER_ANCHORS = (
    ("nbs81_table99_Na2O_c_delta_fH", "-99.00", 41, "99"),
    ("nbs81_table99_Na6Si2O7_c_delta_fH", "-868.", 61, "99"),
    ("nbs81_table99_Na2SiO3_gl_delta_fH", "-368.1", 61, "99"),
    ("nbs81_table99_Na2Si2O5_gl_delta_fH", "-584.56", 61, "99"),
    ("nbs81_table100_KO2_c_delta_fH", "-68.10", 72, "100"),
    ("nbs81_table100_K2SiO3_c_S", "34.9", 88, "100"),
)


def _load_document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation_records(document):
    records = {}
    for species_formula, species in document["species"].items():
        for observation in species["observations"]:
            assert observation["values"]["formula"] == species_formula
            records[observation["observation_id"]] = observation
    return records


def _render_anchor_grid(document):
    observations = _observation_records(document)
    return tuple(
        (
            observation_id,
            str(
                observations[observation_id]["values"].get(
                    "value_as_printed",
                    observations[observation_id]["values"]["value"],
                )
            ),
            observations[observation_id]["locator"]["page"],
            observations[observation_id]["locator"]["table"],
        )
        for observation_id, _value, _page, _table in RENDER_ANCHORS
    )


def _not_applicable(axis):
    return axis is not None and axis.tag.value == "not_applicable"


def _unknown(axis):
    return axis is None or axis.is_unknown


def test_printed_rows_cells_and_render_anchors_are_pinned_and_mutation_checked():
    document = _load_document()
    observations = _observation_records(document)
    row_cell_counts = Counter(
        (record["values"]["formula"], record["values"]["phase_as_printed"])
        for record in observations.values()
    )

    assert len(observations) == 51
    assert len(row_cell_counts) == 20
    assert row_cell_counts == EXPECTED_ROW_CELL_COUNTS
    assert sum(1 for formula, _state in row_cell_counts if formula.startswith("Na")) == 13
    assert sum(1 for formula, _state in row_cell_counts if formula.startswith("K")) == 7
    assert _render_anchor_grid(document) == RENDER_ANCHORS
    sample = next(
        sample
        for sample in document["fidelity_samples"]
        if sample["path"]
        == "species.Na2O.observations[nbs81_table99_Na2O_c_delta_fH].values.value"
    )
    assert sample["value"] == RENDER_ANCHORS[0][1]
    assert sample["locator"] == {
        "page": 41,
        "pdf_page_index": 40,
        "table": "99",
        "row": "Na2O c",
        "column": "ΔfH° at 298.15 K",
    }

    changed = deepcopy(document)
    target = _observation_records(changed)["nbs81_table99_Na2O_c_delta_fH"]
    target["values"]["value"] = "-99.10"
    with pytest.raises(AssertionError):
        assert _render_anchor_grid(changed) == RENDER_ANCHORS


def test_isolated_migration_scores_exact_tokens_and_preserves_printed_conditions():
    document = _load_document()
    source_observations = _observation_records(document)
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    observations = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    assert len(observations) == 51

    quantity_unknown_ids = {
        observation_id
        for observation_id, row in observations.items()
        if _unknown(row.identity.quantity)
    }
    phase_unknown_ids = {
        observation_id
        for observation_id, row in observations.items()
        if row.identity.species.phase is None
        or row.identity.species.phase.is_unknown
    }
    unavailable_ids = {
        observation_id
        for observation_id, row in observations.items()
        if row.value.kind.value == "unavailable"
    }
    assert quantity_unknown_ids == H298_MINUS_H0_IDS
    assert unavailable_ids == H298_MINUS_H0_IDS
    assert phase_unknown_ids == set()

    quantity_tokens = {"delta_fH": "delta_fH", "delta_fG": "delta_fG", "S": "S", "cp": "cp"}
    phase_map = battery_migrate.PHASE_MAP
    for observation_id, source in source_observations.items():
        row = observations[observation_id]
        source_values = source["values"]
        assert source_values["attribution"] == "National Bureau of Standards — Technical Note 270-8"
        assert row.evidence.attribution == "National Bureau of Standards — Technical Note 270-8"
        phase_token = source_values["phase"]
        assert phase_token in phase_map
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value == phase_map[phase_token]
        assert row.identity.species.formula == source_values["formula"]

        if phase_token == "glass":
            quench = source["point_conditions"]["quench_state"]
            assert quench["state"]["value"] == "not_stated"
            assert quench["locator"]["page"] == 61
            migrated_quench = row.point_conditions["quench_state"]
            assert migrated_quench.state.is_value
            assert migrated_quench.state.value.value == "not_stated"
            assert migrated_quench.locator.page == 61

        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("298.15")
        assert temperature.locator.page == source["locator"]["page"]
        assert temperature.locator.table == source["locator"]["table"]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("298.15")
        assert row.locator.page == source["locator"]["page"]
        assert row.locator.table == source["locator"]["table"]

        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" not in row.point_conditions

        if observation_id in H298_MINUS_H0_IDS:
            assert row.identity.total_pressure_Pa is None
            assert row.identity.fO2_Pa is None
            assert row.identity.composition is None
        else:
            assert _not_applicable(row.identity.total_pressure_Pa)
            assert _not_applicable(row.identity.fO2_Pa)
            assert _not_applicable(row.identity.composition)

        if phase_token == "g":
            assert "standard_pressure_Pa" not in row.point_conditions
            assert source_values["fugacity_standard_state_as_printed"] == "unit fugacity"
            assert row.identity.standard_pressure_Pa is None or row.identity.standard_pressure_Pa.is_unknown
        else:
            pressure = row.point_conditions["standard_pressure_Pa"]
            assert pressure.state.is_value
            assert pressure.state.value == Decimal("101325")
            assert pressure.locator.page == 10
            assert pressure.locator.section == "Conventions Regarding Pure Substances"
            assert row.identity.standard_pressure_Pa is not None
            assert row.identity.standard_pressure_Pa.is_value
            assert row.identity.standard_pressure_Pa.value == Decimal("101325")

        if observation_id in H298_MINUS_H0_IDS:
            assert source_values["quantity"] == "enthalpy_298K_minus_0K_as_printed"
            assert not row.identity.quantity.is_value
            continue

        source_quantity = source_values["quantity"]
        assert source_quantity in quantity_tokens
        assert row.identity.quantity.is_value
        assert row.identity.quantity.value.value == quantity_tokens[source_quantity]
        assert row.value.kind.value == "point"

    assert observations["nbs81_table99_Na2O_c_delta_fH"].value.point == Decimal("-414.21600")
    assert observations["nbs81_table100_KO2_c_delta_fH"].value.point == Decimal("-284.93040")
    assert observations["nbs81_table100_K2SiO3_c_S"].value.point == Decimal("146.0216")
    assert observations["nbs81_table99_Na2SiO3_gl_delta_fH"].value.point == Decimal("-1540.1304")
    assert observations["nbs81_table99_Na2Si2O5_gl_delta_fH"].value.point == Decimal("-2445.79904")

    for observation_id, expected in (
        ("nbs81_table99_Na2Si2O5_c_delta_fH", "beta"),
        ("nbs81_table99_Na2Si2O5_c_delta_fG", "beta"),
        ("nbs81_table99_Na2Si2O5_c_S", "beta"),
        ("nbs81_table99_Na2Si2O5_c2_delta_fH", "alpha"),
    ):
        polymorph = observations[observation_id].identity.species.polymorph
        assert polymorph is not None and polymorph.is_value
        assert polymorph.value.value == expected
    unstable_polymorph = observations["nbs81_table99_Na2Si2O5_c3_delta_fH"].identity.species.polymorph
    assert unstable_polymorph is not None and unstable_polymorph.is_unknown
    assert "unstable" in (unstable_polymorph.reason or "")
    assert unstable_polymorph.rejection_kind.value == "unrecognised"

    for observation_id in (
        "nbs81_table99_Na2O_c_delta_fH",
        "nbs81_table99_NaO2_g_S",
        "nbs81_table100_K2SiO3_c_S",
    ):
        row = observations[observation_id]
        assert _not_applicable(row.identity.composition)
        assert _not_applicable(row.identity.total_pressure_Pa)
        assert _not_applicable(row.identity.fO2_Pa)
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("298.15")
