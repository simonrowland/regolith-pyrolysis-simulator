from __future__ import annotations

import copy
import csv
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity
from simulator.battery.migrate import convert_temperature_to_k, wt_pct_to_mole_fraction
from simulator.battery.oxygen_identity import log10_fo2_to_pa
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "roeder-1974-iron-activity-olivine-basaltic-liquids"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = (
    ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.table1-cell-audit.csv"
)
ACTIVITY_RELATION_ID = (
    f"{SOURCE_ID}::context::roeder_1974_activity_definition_and_standard_state"
)
ACTIVITY_EQUATION_ID = (
    f"{SOURCE_ID}::context::roeder_1974_activity_equation_and_gamma_fit"
)
OXIDES = ("SiO2", "Al2O3", "FeO", "MgO", "CaO", "Na2O", "K2O", "TiO2", "MnO")
STANDARD_STATE = (
    "The standard state for “FeO” is the liquid oxide in equilibrium with metallic iron."
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _audit_rows() -> dict[int, dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    with CELL_AUDIT.open(encoding="utf-8", newline="") as stream:
        for cell in csv.DictReader(stream):
            run = int(re.search(r"(\d+)$", cell["row"]).group(1))
            row = rows.setdefault(
                run,
                {
                    "label": cell["row"],
                    "page": int(cell["pdf_page"]),
                    "published_page": int(cell["published_page"]),
                    "cells": {},
                },
            )
            assert row["label"] == cell["row"]
            assert row["page"] == int(cell["pdf_page"])
            value = cell["value"] if cell["status"] == "numeric" else None
            if cell["status"] == "absent":
                assert cell["value"] == ""
            row["cells"][cell["column"]] = value
    return rows


def _observation_id(run: int, kind: str) -> str:
    suffix = "activity" if kind == "activity" else "activity_coefficient"
    return f"roeder_1974_table1_run_{run:02d}_{suffix}"


def _assert_source_cells_match_audit(document: dict, audit: dict[int, dict[str, object]]) -> None:
    observations = {
        row["observation_id"]: row
        for row in document["species"]["FeO"]["observations"]
    }
    assert len(audit) == 45
    assert len(observations) == 90

    numeric_cells = sum(
        value is not None
        for source_row in audit.values()
        for value in source_row["cells"].values()
    )
    absent_cells = sum(
        value is None
        for source_row in audit.values()
        for value in source_row["cells"].values()
    )
    for run, source_row in audit.items():
        cells = source_row["cells"]
        activity = observations[_observation_id(run, "activity")]
        gamma = observations[_observation_id(run, "gamma")]
        for obs, kind, cell_name, field in (
            (activity, "activity", "Act. FeO", "activity"),
            (gamma, "gamma", "γ FeO", "gamma"),
        ):
            expected = cells[cell_name]
            assert expected is not None
            assert obs["values"][field] == expected, obs["observation_id"]
            assert obs["values"][f"{field}_as_printed"] == expected
            assert obs["values"]["method_class"] == "measured_reduced"
            assert obs["values"]["evidence_class"] == "measured_reduced"
            assert obs["phase"] == "l"
            assert obs["standard_state"] == STANDARD_STATE
            assert obs["values"]["standard_state_as_printed"] == STANDARD_STATE
            assert obs["locator"]["page"] == source_row["page"]
            assert obs["locator"]["published_page"] == source_row["published_page"]
            assert obs["locator"]["table"] == "1"
            assert f"row {source_row['label']}" in obs["locator"]["note"]
            assert f"column {cell_name}" in obs["locator"]["note"]

            conditions = obs["point_conditions"]
            for key, expected_value, expected_column in (
                ("T_C", cells["Temp."], "Temp."),
                ("fO2_log", cells["Log fO2"], "Log fO2"),
                ("gas_mix", f"CO2/H2 = {cells['CO2/H2']}", "CO2/H2"),
            ):
                condition = conditions[key]
                assert condition["state"]["tag"] == "value"
                if key == "gas_mix":
                    assert condition["state"]["value"] == expected_value
                else:
                    assert condition["state"]["value"] == expected_value
                assert condition["locator"]["page"] == source_row["page"]
                assert condition["locator"]["table"] == "1"
                assert f"row {source_row['label']}" in condition["locator"]["note"]
                assert f"column {expected_column}" in condition["locator"]["note"]

            fo2 = conditions["fO2_log"]
            assert fo2["units"] == "log10(atm)"
            assert obs["values"]["fO2_reference_unit_as_printed"] == "atm"
            assert obs["values"]["fO2_reference_unit_locator"]["page"] == 1

            pressure = conditions["total_pressure_Pa"]
            assert pressure["state"]["tag"] == "unknown"
            assert "not printed" in pressure["state"]["reason"]
            assert "total_pressure_Pa" not in obs["values"]

            oxide_values = obs["values"]["oxide_wt_pct_as_printed"]
            absences = obs["values"]["oxide_absences_as_printed"]
            for oxide in OXIDES:
                value = cells[oxide]
                if value is None:
                    assert oxide not in oxide_values
                    absence = absences[oxide]
                    assert absence["state"]["tag"] == "not_applicable"
                    assert "blank" in absence["state"]["reason"]
                    assert f"column {oxide}" in absence["locator"]["note"]
                else:
                    assert oxide_values[oxide] == value
            assert obs["values"]["Total_wt_pct_as_printed"] == cells["Total"]
            assert obs["values"]["T_C"] == cells["Temp."]
            assert obs["values"]["CO2_H2_ratio_as_printed"] == cells["CO2/H2"]
            assert obs["values"]["fO2_as_printed"] == f"Log fO2 = {cells['Log fO2']}"

            composition = conditions["composition"]
            assert composition["locator"]["page"] == source_row["page"]
            assert composition["locator"]["table"] == "1"
            if cells["MnO"] is None:
                assert composition["state"]["tag"] == "unknown"
                assert "full melt composition is not reported" in composition["state"]["reason"]
            else:
                assert composition["state"]["tag"] == "value"
                assert composition["state"]["value"]["amount_basis"] == "mass_percent"
                assert composition["state"]["value"]["components"] == {
                    oxide: cells[oxide] for oxide in OXIDES
                }
        assert activity["T_C"] == cells["Temp."]
        assert activity["derived_from"] == [
            ACTIVITY_RELATION_ID,
            ACTIVITY_EQUATION_ID,
            f"{SOURCE_ID}::context::roeder_1974_table1_run_{run:02d}_printed_conditions",
        ]
        assert gamma["derived_from"] == [f"{SOURCE_ID}::{_observation_id(run, 'activity')}"]
    assert numeric_cells == 649
    assert absent_cells == 26


def _assert_migrated_cells_match_audit(result, audit: dict[int, dict[str, object]]) -> None:
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 90
    assert sum(row.value.kind.value == "point" for row in result.observations.values()) == 90
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 0
    context_ids = {
        str(context["context_id"])
        for contexts in result.context_by_work.values()
        for context in contexts
    }
    atm_pa = Decimal("101325")

    for run, source_row in audit.items():
        cells = source_row["cells"]
        for kind, cell_name, value_key, quantity in (
            ("activity", "Act. FeO", "activity", Quantity.ACTIVITY),
            ("gamma", "γ FeO", "gamma", Quantity.ACTIVITY_COEFFICIENT),
        ):
            local_id = _observation_id(run, kind)
            row = result.observations[f"{SOURCE_ID}::{local_id}"]
            assert row.value.kind.value == "point"
            assert row.value.point == Decimal(cells[cell_name]), local_id
            assert row.identity.quantity.is_value
            assert row.identity.quantity.value is quantity
            assert row.identity.species is not None
            assert row.identity.species.formula == "FeO"
            assert row.identity.species.phase.is_value
            assert row.identity.species.phase.value is Phase.L
            assert row.evidence.class_.is_value
            assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
            assert row.locator.page == source_row["page"]
            assert row.locator.published_page == source_row["published_page"]
            assert row.locator.table == "1"
            assert f"row {source_row['label']}" in row.locator.note
            assert f"column {cell_name}" in row.locator.note

            temperature = row.point_conditions["T_C"]
            assert temperature.state.is_value
            assert temperature.state.value == Decimal(cells["Temp."])
            assert temperature.locator.page == source_row["page"]
            expected_temperature, trail = convert_temperature_to_k(cells["Temp."], "C")
            assert trail == "celsius_to_kelvin"
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == expected_temperature

            log_fo2 = row.point_conditions["fO2_log"]
            assert log_fo2.state.is_value
            assert log_fo2.state.value == Decimal(cells["Log fO2"])
            assert log_fo2.locator.page == source_row["page"]
            assert row.identity.fO2_Pa.is_value
            assert row.identity.fO2_Pa.value == log10_fo2_to_pa(
                Decimal(cells["Log fO2"]), atm_pa
            )

            gas_mix = row.point_conditions["gas_mix"]
            assert gas_mix.state.is_value
            assert gas_mix.state.value == f"CO2/H2 = {cells['CO2/H2']}"
            assert gas_mix.locator.page == source_row["page"]

            assert row.identity.total_pressure_Pa.is_unknown
            assert "total_pressure_Pa" not in row.point_conditions
            assert row.identity.reference_state.is_unknown
            assert row.identity.reference_state.reason

            composition = row.point_conditions["composition"]
            if cells["MnO"] is None:
                assert composition.state.is_unknown
                assert row.identity.composition.is_unknown
            else:
                assert composition.state.is_value
                expected_wt = {oxide: Decimal(cells[oxide]) for oxide in OXIDES}
                expected_composition = wt_pct_to_mole_fraction(expected_wt)
                assert composition.state.value == expected_composition
                assert row.identity.composition.is_value
                assert row.identity.composition.value == expected_composition

            if kind == "activity":
                run_context = f"{SOURCE_ID}::context::roeder_1974_table1_run_{run:02d}_printed_conditions"
                expected_parents = (ACTIVITY_RELATION_ID, ACTIVITY_EQUATION_ID, run_context)
                assert row.derived_from == expected_parents
                assert set(expected_parents).issubset(context_ids)
                assert row.derivation is not None
                assert row.derivation.relation == "log a_FeO = 12045/T + 0.5 log fO2 - 2.336"
                assert row.derivation.inputs == expected_parents
            else:
                activity_parent = f"{SOURCE_ID}::{_observation_id(run, 'activity')}"
                assert row.derived_from == (activity_parent,)
                assert row.derivation is not None
                assert row.derivation.relation == "gamma = a/X"
                assert row.derivation.inputs == (activity_parent,)


def test_render_audit_binds_every_table1_value_and_condition_with_mutation_check(
    tmp_path: Path,
) -> None:
    document = _document()
    audit = _audit_rows()
    _assert_source_cells_match_audit(document, audit)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    result = _migrate_real_extract(
        tmp_path / "original",
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    _assert_migrated_cells_match_audit(result, audit)

    mutated = copy.deepcopy(document)
    interior = next(
        row
        for row in mutated["species"]["FeO"]["observations"]
        if row["observation_id"] == _observation_id(23, "gamma")
    )
    interior["values"]["gamma"] = "0.999"
    interior["values"]["gamma_as_printed"] = "0.999"
    mutated_result = _migrate_real_extract(
        tmp_path / "mutated",
        EXTRACT.name,
        write=False,
        extract_doc=mutated,
    )
    with pytest.raises(AssertionError, match="run_23_activity_coefficient"):
        _assert_migrated_cells_match_audit(mutated_result, audit)

    assert len(document["species"]["FeO"]["observations"]) == 90
    assert all(
        row.identity.quantity.is_value
        and row.identity.species is not None
        and row.identity.species.phase.is_value
        for row in result.observations.values()
    )
    assert result.observations[f"{SOURCE_ID}::{_observation_id(1, 'activity')}"].identity.composition.is_unknown
    for run, page in ((1, 4), (23, 6), (45, 8)):
        row = result.observations[f"{SOURCE_ID}::{_observation_id(run, 'activity')}"]
        assert row.locator.page == page
        assert row.identity.temperature_K.is_value
        assert row.identity.fO2_Pa.is_value
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.reference_state.is_unknown
