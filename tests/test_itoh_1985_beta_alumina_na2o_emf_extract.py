"""Render pins and isolated migration for Itoh et al. (1985)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ReferenceStateConvention, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/itoh-1985-beta-alumina-na2o-emf.yaml"

# Independent transcription from the 600 dpi render of physical PDF page 6.
TABLE1_RENDER = (
    ("(3)", "solid assemblage (alpha+beta)-alumina", "1.25 × 10⁻¹³", "-247"),
    ("(6)", "solid assemblage (alpha+beta)-alumina", "1.62 × 10⁻¹⁴", "-264"),
    ("(22)", "solid assemblage (alpha+beta)-alumina", "1.52 × 10⁻¹⁷", "-322"),
    ("(23)", "solid assemblage (alpha+beta)-alumina", "3.23 × 10⁻¹²", "-220"),
    ("(24)(25)", "solid assemblage (alpha+beta)-alumina", "1.58 × 10⁻¹³", "-245"),
    ("(26)", "solid assemblage (alpha+beta)-alumina", "2.00 × 10⁻¹³", "-243"),
    ("This work / alpha+beta", "solid assemblage (alpha+beta)-alumina", "2.82 × 10⁻¹³", "-240"),
    ("(25)", "solid assemblage (beta+beta-double-prime)-alumina", "1.00 × 10⁻⁹", "—"),
    ("This work / beta+beta-double-prime", "solid assemblage (beta+beta-double-prime)-alumina", "2.95 × 10⁻¹¹", "—"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_grid(document: dict, rendered=TABLE1_RENDER) -> None:
    observations = document["species"]["Na2O"]["observations"]
    by_reference = {}
    for observation in observations:
        values = observation["values"]
        ref = values.get("attribution")
        if ref is None:
            assemblage = "alpha+beta" if "alpha+beta" in values["phase_as_printed"] else "beta+beta-double-prime"
            ref = f"This work / {assemblage}"
        by_reference[(ref, values["phase_as_printed"])] = values["value_as_printed"]

    dg_context = document["species"]["Na2O"]["context"][0]["values"]["values_as_printed"]
    dg_by_ref = {row["row"]: str(row["value"]) for row in dg_context}
    actual = []
    for ref, phase, _activity, _delta_g in TABLE1_RENDER:
        activity = by_reference[(ref, phase)]
        delta_g = "—" if ref in {"(25)", "This work / beta+beta-double-prime"} else dg_by_ref[ref.replace("This work / alpha+beta", "This work")]
        actual.append((ref, phase, activity, delta_g))
    assert tuple(actual) == tuple(rendered)


def test_table1_render_transcription_and_mutation_checks() -> None:
    document = _document()
    _assert_render_grid(document)

    changed_grid = list(TABLE1_RENDER)
    changed_grid[2] = (*changed_grid[2][:2], "1.53 × 10⁻¹⁷", *changed_grid[2][3:])
    with pytest.raises(AssertionError):
        _assert_render_grid(document, changed_grid)

    mutated_extract = deepcopy(document)
    mutated_extract["species"]["Na2O"]["observations"][2]["values"]["value_as_printed"] = "1.53 × 10⁻¹⁷"
    with pytest.raises(AssertionError):
        _assert_render_grid(mutated_extract)


def test_every_typed_activity_migrates_as_the_printed_na2o_point() -> None:
    document = _document()
    activities = document["species"]["Na2O"]["observations"]
    assert len(activities) == 9
    assert all(item["standard_state"]["inferred"] is True for item in activities)
    assert all(
        item["standard_state"]["state"]["value"]["endmember"]["phase"]["value"] == "cr"
        for item in activities
    )
    assert all(item["values"]["quantity"] == "activity" for item in activities)
    assert all(item["phase"] == "cr" for item in activities)
    assert all(item["values"]["phase_as_printed"].startswith("solid assemblage") for item in activities)
    _assert_render_grid(document)

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = tuple(
        item for item in result.observations.values() if item.source_id == EXTRACT.stem
    )

    assert len(migrated) == 9
    assert result.registry_issues == []
    assert not result.validation.hard_issues
    for observation in migrated:
        assert observation.identity.species.formula == "Na2O"
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.value.kind is ValueKind.POINT
        assert observation.notices == ()
        assert observation.identity.temperature_K.value == Decimal("1000")
        assert observation.identity.composition.is_unknown
        assert not observation.identity.reference_state.is_unknown
        reference = observation.identity.reference_state.value
        assert reference.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference.endmember.formula == "Na2O"
        assert reference.endmember.phase.value is Phase.CR
        assert reference.endmember.polymorph.value.value == "alpha"
        assert reference.component_basis == "Na2O"
        assert observation.identity.fO2_Pa.is_unknown
        assert observation.identity.total_pressure_Pa.is_unknown
    actual_by_id = {item.observation_id.split("::", 1)[1]: item.value.point for item in migrated}
    assert actual_by_id["itoh_1985_table1_alpha_beta_this_work_activity_1000k"] == Decimal("2.82e-13")
    assert actual_by_id["itoh_1985_table1_beta_beta_prime_this_work_activity_1000k"] == Decimal("2.95e-11")


def test_figure_records_defer_all_plot_points() -> None:
    document = _document()
    records = [
        item
        for item in document["species"]["Na2O"]["context"]
        if item["type"] == "figure_record"
    ] + [
        item
        for item in document["species"]["Na"]["context"]
        if item["type"] == "figure_record"
    ]
    assert len(records) == 7
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in records)
    assert all("marker_count_per_series" in item["values"] for item in records)


def test_table2_calculated_pressure_rows_are_retained_in_context() -> None:
    record = next(
        item
        for item in _document()["species"]["Na2O"]["context"]
        if item["observation_id"] == "itoh_1985_table2_calculated_partial_pressures"
    )
    rows = record["values"]["rows_as_printed"]
    assert len(rows) == 8
    assert rows[0] == {
        "T_K": 500,
        "alpha_beta": {"SO2": "1.41e-31", "SO3": "1.66e-26", "CO2": "1.32e-1"},
        "beta_beta_prime": {"SO2": "2.40e-34", "SO3": "2.82e-29", "CO2": "2.24e-4"},
    }
    assert rows[-1]["T_K"] == 1200
    assert rows[-1]["alpha_beta"]["CO2"] == "6.64e3"
    assert rows[-1]["beta_beta_prime"]["SO3"] == "9.59e-9"


def test_printed_equations_and_abstract_body_range_discrepancy_are_retained() -> None:
    records = {
        record["observation_id"]: record
        for record in _document()["species"]["Na2O"]["context"]
    }
    thermochemical = records["itoh_1985_thermochemical_equations_12_13_14_15_context"]
    assert len(thermochemical["values"]["equations_as_printed"]) == 4
    vapor = records["itoh_1985_sodium_vapor_pressure_equations_19_20_context"]
    assert vapor["values"]["pressure_as_printed"] == "P = 101325 Pa"
    assert len(vapor["values"]["equations_as_printed"]) == 2
