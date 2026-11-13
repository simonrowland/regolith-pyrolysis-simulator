from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/chen-2022-sio2-mno-slag-activities.yaml"
SOURCE_ID = "chen-2022-sio2-mno-slag-activities"


def _state_value(state):
    if state is None:
        return "missing"
    if state.is_value:
        return state.value
    return state.tag.value


def _observation(result, observation_id: str):
    return result.observations[f"{SOURCE_ID}::{observation_id}"]


def test_fidelity_samples_pin_table2_and_fail_after_mutation() -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert len(source["fidelity_samples"]) == 6
    assert vle.check_all_fidelity_samples_match(source, label=SOURCE_ID) == []

    mutated = copy.deepcopy(source)
    mutated["species"]["SiO2"]["observations"][1]["values"][
        "activity_as_printed"
    ] = "4.555"
    errors = vle.check_all_fidelity_samples_match(mutated, label=f"{SOURCE_ID}-mutated")
    assert errors, "changing a page-render-pinned Table 2 value must fail fidelity"


def test_isolated_migration_preserves_rows_conditions_lineage_and_reference_states() -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_observations = {
        observation["observation_id"]: observation
        for payload in source["species"].values()
        for observation in payload["observations"]
    }
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    migrated = [
        observation
        for observation in result.observations.values()
        if observation.source_id == SOURCE_ID
    ]
    hard_count = len(result.validation.hard_issues)
    unavailable_count = sum(
        observation.value.kind is ValueKind.UNAVAILABLE for observation in migrated
    )
    print(
        f"{SOURCE_ID}: observations={len(migrated)} hard_issues={hard_count} "
        f"unavailable={unavailable_count} registry_issues={len(result.registry_issues)}"
    )
    assert len(migrated) == 222
    assert hard_count == 0
    assert not result.registry_issues

    unknown_quantities = [
        observation.observation_id
        for observation in migrated
        if not observation.identity.quantity.is_value
    ]
    unknown_phases = [
        observation.observation_id
        for observation in migrated
        if observation.identity.species is None
        or not observation.identity.species.phase.is_value
    ]
    assert unknown_quantities == []
    assert unknown_phases == []

    expected_counts = {
        "Si": (37, Quantity.EQUILIBRIUM_PHASE_COMPOSITION),
        "Mn": (37, Quantity.EQUILIBRIUM_PHASE_COMPOSITION),
        "SiO2": (37, Quantity.ACTIVITY_COEFFICIENT),
        "MnO": (37, Quantity.ACTIVITY_COEFFICIENT),
    }
    by_species = {}
    for formula, (count, quantity) in expected_counts.items():
        rows = [
            observation
            for observation in migrated
            if observation.identity.species.formula == formula
            and observation.identity.quantity.value is quantity
        ]
        assert len(rows) == count
        by_species[formula] = rows
    for formula in ("SiO2", "MnO"):
        activities = [
            observation
            for observation in migrated
            if observation.identity.species.formula == formula
            and observation.identity.quantity.value is Quantity.ACTIVITY
        ]
        assert len(activities) == 37

    for row in range(1, 38):
        suffix = f"{row:02d}"
        si_parent = _observation(result, f"chen22_table2_metal_si_row_{suffix}")
        mn_parent = _observation(result, f"chen22_table2_metal_mn_row_{suffix}")
        si_gamma = _observation(result, f"chen22_table2_sio2_gamma_row_{suffix}")
        mn_gamma = _observation(result, f"chen22_table2_mno_gamma_row_{suffix}")
        si_activity = _observation(result, f"chen22_table2_sio2_activity_row_{suffix}")
        mn_activity = _observation(result, f"chen22_table2_mno_activity_row_{suffix}")
        row_observations = [si_parent, mn_parent, si_gamma, mn_gamma, si_activity, mn_activity]

        expected_temperature = Decimal("1673.15") if row <= 18 else Decimal("1723.15") if row <= 25 else Decimal("1773.15")
        for observation in row_observations:
            local_id = observation.observation_id.rsplit("::", 1)[-1]
            source_conditions = source_observations[local_id]["point_conditions"]
            assert observation.identity.temperature_K is not None
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == expected_temperature
            assert observation.point_conditions is not None
            temperature = observation.point_conditions["temperature_K"]
            assert temperature.state.is_value
            assert temperature.locator.page == 3
            assert str(temperature.locator.table) == "2"
            composition = observation.point_conditions["composition"]
            assert composition.state.is_value
            assert composition.locator.page == 3
            assert str(composition.locator.table) == "2"
            assert observation.identity.composition is not None
            assert observation.identity.composition.is_value
            assert source_conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
            assert source_conditions["fO2_log"]["state"]["tag"] == "unknown"
            assert source_conditions["fO2_Pa"]["state"]["tag"] == "unknown"
            assert observation.point_conditions["fO2_Pa"].state.is_unknown
            assert observation.identity.total_pressure_Pa is not None
            assert observation.identity.total_pressure_Pa.is_unknown
            assert observation.identity.fO2_Pa is not None
            assert observation.identity.fO2_Pa.is_unknown
            assert observation.point_conditions["pCO_atm_as_printed"].state.value == Decimal("0.286")
            assert observation.point_conditions["atmosphere"].state.value == (
                "Ar 500 mL/min + CO 200 mL/min; pCO = 0.286 atm"
            )

        assert si_gamma.derived_from == (si_parent.observation_id,)
        assert mn_gamma.derived_from == (mn_parent.observation_id,)
        assert si_activity.derived_from == (si_gamma.observation_id,)
        assert mn_activity.derived_from == (mn_gamma.observation_id,)
        assert si_gamma.locator.page == 3 and str(si_gamma.locator.table) == "2"
        assert si_activity.locator.page == 3 and str(si_activity.locator.table) == "2"
        assert mn_gamma.locator.page == 3 and str(mn_gamma.locator.table) == "2"
        assert mn_activity.locator.page == 3 and str(mn_activity.locator.table) == "2"

        assert si_gamma.admission.status is AdmissionStatus.REJECTED
        assert si_activity.admission.status is AdmissionStatus.REJECTED
        assert mn_gamma.admission.status is AdmissionStatus.ADMITTED
        assert mn_activity.admission.status is AdmissionStatus.ADMITTED

    assert _observation(result, "chen22_table2_metal_si_row_01").value.point == Decimal("0.20")
    assert _observation(result, "chen22_table2_metal_mn_row_01").value.point == Decimal("0.73")
    assert _observation(result, "chen22_table2_sio2_gamma_row_01").value.point == Decimal("0.008686")
    assert _observation(result, "chen22_table2_mno_gamma_row_01").value.point == Decimal("0.105")
    assert _observation(result, "chen22_table2_sio2_activity_row_01").value.point == Decimal("0.004554")
    assert _observation(result, "chen22_table2_sio2_activity_row_19").value.point == Decimal("0.009532")
    assert _observation(result, "chen22_table2_sio2_activity_row_37").value.point == Decimal("0.000354")
    assert _observation(result, "chen22_table2_mno_activity_row_01").value.point == Decimal("0.001569")
    assert _observation(result, "chen22_table2_mno_activity_row_19").value.point == Decimal("0.001889")
    assert _observation(result, "chen22_table2_mno_activity_row_37").value.point == Decimal("0.001219")

    for formula, expected_polymorph in (("SiO2", "quartz"), ("MnO", None)):
        row = _observation(
            result,
            "chen22_table2_sio2_activity_row_01"
            if formula == "SiO2"
            else "chen22_table2_mno_activity_row_01",
        )
        reference = row.identity.reference_state
        assert reference is not None and reference.is_value
        assert reference.value.endmember.formula == formula
        assert reference.value.endmember.phase.value is Phase.CR
        if expected_polymorph is None:
            assert reference.value.endmember.polymorph.is_unknown
        else:
            assert reference.value.endmember.polymorph.value.value == expected_polymorph

    for row in (1, 19, 37):
        observation = _observation(result, f"chen22_table2_mno_activity_row_{row:02d}")
        composition = observation.identity.composition.value
        axes = {
            "composition": {name: str(amount) for name, amount in composition.components},
            "total_pressure_Pa": _state_value(observation.identity.total_pressure_Pa),
            "fO2_Pa": _state_value(observation.identity.fO2_Pa),
            "temperature_K": str(observation.identity.temperature_K.value),
        }
        print(f"identity row {row}: {axes}")
