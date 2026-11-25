from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from simulator.battery.enums import MethodToken, Quantity, ValueKind
from simulator.battery.records import ApproxPressure, BandBasis

EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/zhang-2021-acsesc-si.yaml"
)


def _assert_source_pins(doc: dict) -> None:
    observations = doc["species"]["unknown"]["observations"]
    by_id = {row["observation_id"]: row for row in observations}
    weights = by_id["zhang_si_table_s1_published_weights"]["values"]["runs"]
    compositions = [row for row in observations if "residue_composition" in row["observation_id"]]
    mass_loss = [row for row in observations if row["observation_id"].endswith("_mass_loss")]

    assert len(weights) == 12
    assert len(compositions) == 12
    assert len(mass_loss) == 12
    assert weights[0]["mass_before_mg"] == 40.4  # SI Table S1, PDF p. 9
    assert weights[2]["mass_after_mg"] == 54.3  # SI Table S1, PDF p. 9
    comp_by_id = {row["observation_id"]: row for row in compositions}
    assert comp_by_id["zhang_2021_table1_bas1400c15m_residue_composition"]["values"]["printed_residue_oxide_wt_pct"]["composition_wt_pct"]["Na2O"] == 0.66  # Table 1, PDF p. 71
    assert comp_by_id["zhang_2021_table1_bas1200c40h_residue_composition"]["values"]["printed_residue_oxide_wt_pct"]["composition_wt_pct"]["Rb2O"] == 0.67  # Table 1, PDF p. 71
    assert comp_by_id["zhang_2021_table1_bas1400c90m_residue_composition"]["values"]["printed_residue_oxide_wt_pct"]["composition_wt_pct"]["Na2O"] == "b.d.l."
    assert doc["scope_assessment"]["note"].count("608 individual EPMA spot analyses") == 1
    assert "S2–S9: 488; S10: 67; S11: 53" in doc["scope_assessment"]["note"]
    assert "migrator expands each vector into oxide-cell observations" in doc["scope_assessment"]["note"]
    for row in compositions:
        assert "composition_wt_pct" not in row["values"]
        assert "total_pressure_Pa" not in row["values"]
        assert row["point_conditions"]["total_pressure_Pa"]["state"]["value"] == {
            "kind": "bound",
            "bound_operator": "<",
            "bound_value": "0.0001",
        }
        assert row["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 74
        assert row["point_conditions"]["fO2_Pa"]["locator"]["page"] == 8
        assert row["quench_state"]["locator"]["page"] == 9
    assert doc["scope_assessment"]["measured_reduced_bulk_mass_loss_points"] == 12
    for row in mass_loss:
        conditions = row["point_conditions"]
        assert conditions["fO2_Pa"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["value"]["point"] == "0.0001"
        assert conditions["total_pressure_Pa"]["state"]["value"]["approximate"] is True
        assert conditions["total_pressure_Pa"]["locator"]["page"] == 8


def _source_residue_cells(doc: dict) -> dict[tuple[str, str], Decimal]:
    expected = {}
    for row in doc["species"]["unknown"]["observations"]:
        if "residue_composition" not in row["observation_id"]:
            continue
        sample = row["locator"]["row"]
        for formula, printed in row["values"]["printed_residue_oxide_wt_pct"]["composition_wt_pct"].items():
            if isinstance(printed, str) and printed.casefold() == "b.d.l.":
                continue
            expected[(sample, formula)] = Decimal(str(printed))
    return expected


def test_zhang_si_render_pins_rows_and_rejects_value_mutation() -> None:
    doc = yaml.safe_load(EXTRACT.read_text())
    _assert_source_pins(doc)

    mutated = deepcopy(doc)
    mutated["species"]["unknown"]["observations"][0]["values"]["runs"][0]["mass_before_mg"] = 40.5
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)


def test_zhang_si_migrates_percent_and_keeps_unprinted_fo2_unknown() -> None:
    root = EXTRACT.parents[3]
    migrator = Migrator(root)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "zhang-2021-acsesc-si"
    ]
    mass_loss_rows = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations
        if observation.observation_id.endswith("_mass_loss")
    }
    assert mass_loss_rows["zhang_si_table_s1_bas1400c0m1_mass_loss"].value.point == Decimal("0.00495")
    assert mass_loss_rows["zhang_si_table_s1_bas1400c15m_mass_loss"].value.point == Decimal("0.03894")
    assert mass_loss_rows["zhang_si_table_s1_bas1200c40h_mass_loss"].value.point == Decimal("0.05539")
    assert len(mass_loss_rows) == 12

    for key in (
        "zhang_si_table_s1_bas1400c15m_mass_loss",
        "zhang_si_table_s1_bas1400c60m_mass_loss",
        "zhang_si_table_s1_bas1200c10h_mass_loss",
    ):
        identity = mass_loss_rows[key].identity
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        # About 10^-6 mbar is an approximate print. Identity carries the band
        # around 0.0001 Pa. The measured mass-loss fraction itself stays exact.
        pressure = identity.total_pressure_Pa
        assert pressure is not None and pressure.is_value
        assert isinstance(pressure.value, ApproxPressure)
        assert pressure.value.point == Decimal("0.0001")
        assert (
            pressure.value.band_basis is BandBasis.HALF_DECADE_ORDER_OF_MAGNITUDE
        )
        assert identity.fO2_Pa.is_unknown

    for observation in mass_loss_rows.values():
        identity = observation.identity
        assert identity.method.is_value
        assert identity.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        assert identity.fO2_Pa.is_unknown
        assert identity.sample_mass_kg is not None
        assert identity.sample_mass_kg.is_value
        assert identity.exposure is not None and identity.exposure.is_value
        assert identity.exposure.value.duration_s.is_value
        assert observation.point_conditions["time_min"].locator is not None
        assert observation.point_conditions["sample_mass_kg"].locator is not None
        assert observation.point_conditions["sample_mass_after_kg"].locator is not None
        if observation.observation_id.endswith(
            "zhang_si_table_s1_bas1400c0m2_mass_loss"
        ):
            assert identity.exposure.value.area_m2.is_unknown
        else:
            assert identity.exposure.value.area_m2.is_value
            assert observation.point_conditions[
                "exposed_area_initial_m2"
            ].locator is not None
            assert observation.point_conditions[
                "exposed_area_final_m2"
            ].locator is not None

    residue = next(
        observation
        for observation in observations
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.identity.species.formula == "Na2O"
        and observation.locator is not None
        and observation.locator.note == "row=Bas1400C60m"
    )
    assert residue.identity.total_pressure_Pa.is_unknown
    pressure_bound = residue.point_conditions["total_pressure_Pa"].state.value
    assert pressure_bound.kind.value == "bound"
    assert pressure_bound.bound_operator == "<"
    assert pressure_bound.bound_value == Decimal("0.0001")
    assert residue.identity.composition.is_value
    assert residue.identity.composition == mass_loss_rows[
        "zhang_si_table_s1_bas1400c60m_mass_loss"
    ].identity.composition
    assert residue.identity.composition.value.as_map()["Na2O"] > Decimal("0.001")
    assert residue.value.point == Decimal("0.01")

    expected_cells = _source_residue_cells(yaml.safe_load(EXTRACT.read_text()))
    migrated_cells = {}
    for observation in observations:
        if not (
            observation.identity.quantity.is_value
            and observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and observation.value.kind is ValueKind.POINT
            and observation.locator is not None
        ):
            continue
        sample = observation.locator.note.removeprefix("row=")
        migrated_cells[(sample, observation.identity.species.formula)] = observation.value.point
        conditions = observation.point_conditions
        assert observation.identity.temperature_K.is_value
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].locator is not None
        assert conditions["time_min"].state.is_value
        assert conditions["time_min"].locator is not None
        assert all(condition.locator is not None for condition in conditions.values())
    assert migrated_cells == expected_cells


def test_mutating_residue_vector_expansion_recreates_the_drop(monkeypatch) -> None:
    import simulator.battery.migrate as migrate_module

    monkeypatch.setattr(
        migrate_module,
        "_expand_printed_residue_vector",
        lambda values, locator, point_conditions: None,
    )
    migrator = Migrator(EXTRACT.parents[3])
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated_cells = {
        (observation.locator.note.removeprefix("row="), observation.identity.species.formula): observation.value.point
        for observation in migrator.result.observations.values()
        if observation.source_id == "zhang-2021-acsesc-si"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.value.kind is ValueKind.POINT
        and observation.locator is not None
    }
    expected_cells = _source_residue_cells(yaml.safe_load(EXTRACT.read_text()))
    with pytest.raises(AssertionError):
        assert migrated_cells == expected_cells
