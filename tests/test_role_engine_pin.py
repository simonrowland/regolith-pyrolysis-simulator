"""The role pin is the owner's decision, and the callers honour it.

Expected booleans come from ``resolve_role_engines``. This file does not
paste a temperature comparison of its own. Dropping the override inside the
owner must restore the unpinned result; that is the mutation.
"""

from __future__ import annotations

import pytest

import engines.builtin.vapor_pressure as vapor_pressure_module
import simulator.chemistry.role_engines as role_engines_module
from engines.builtin.vapor_pressure import (
    BUILTIN_VAPOUR_CHANNEL_SWITCH_K,
    openimcc_binding_selected_by_inputs,
)
from simulator.battery.leaderboard import _engine_roles
from simulator.battery.residue import (
    ResidueInventoryRefusal,
    _predict_sossi_residue_cohort,
    _record_residue_handoff,
    _residue_role_decision,
)
from simulator.chemistry.role_engines import (
    HandoffCrossing,
    RoleEngineRefusal,
    handoff_notices,
    resolve_role_engines,
)


def _resolve(**overrides):
    kwargs = dict(
        backend_name=None,
        high_t_melt_activity="openimcc",
        temperature_K=BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0,
        intrinsic_fO2_supplied=False,
        vapour_openimcc_above_K=BUILTIN_VAPOUR_CHANNEL_SWITCH_K,
        surface="run",
        evaluate="vapour",
        load_activity_domain_low_K=lambda: (_ for _ in ()).throw(
            AssertionError("activity domain loaded")
        ),
    )
    kwargs.update(overrides)
    return resolve_role_engines(**kwargs)


def _codes(crossing: HandoffCrossing) -> set[str]:
    return {finding.code for finding in handoff_notices(crossing)}


def test_vapour_pin_changes_the_binding_to_the_owner_decision() -> None:
    temperature_K = BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0
    unpinned = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls={},
        quantity_class="vapour",
    )
    controls = {"role_engine_override": {"vapour": "internal-analytical"}}
    pinned = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls=controls,
        quantity_class="vapour",
    )
    owner = _resolve(
        temperature_K=temperature_K,
        role_override=controls["role_engine_override"],
    )
    assert pinned is (owner.vapour == "openimcc")
    assert unpinned is (
        _resolve(temperature_K=temperature_K).vapour == "openimcc"
    )
    assert pinned is not unpinned


def test_dropping_the_override_restores_the_unpinned_binding(monkeypatch) -> None:
    temperature_K = BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0
    controls = {"role_engine_override": {"vapour": "internal-analytical"}}
    production = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls=controls,
        quantity_class="vapour",
    )
    unpinned = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls={},
        quantity_class="vapour",
    )
    assert production is not unpinned
    real = role_engines_module.resolve_role_engines

    def drop_override(**kwargs):
        kwargs["role_override"] = None
        return real(**kwargs)

    monkeypatch.setattr(role_engines_module, "resolve_role_engines", drop_override)
    reverted = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls=controls,
        quantity_class="vapour",
    )
    assert reverted is unpinned
    assert reverted is not production


def test_residue_consumer_uses_the_owner_override(monkeypatch) -> None:
    override = {"vapour": "internal-analytical"}
    production = _residue_role_decision("openimcc", 2200.0, override)
    assert production is not None
    assert production.vapour == _resolve(
        surface="residue",
        engine_argument="openimcc",
        evaluate="all",
        temperature_K=2200.0,
        role_override=override,
    ).vapour
    real = role_engines_module.resolve_role_engines

    def drop_override(**kwargs):
        kwargs["role_override"] = None
        return real(**kwargs)

    monkeypatch.setattr(role_engines_module, "resolve_role_engines", drop_override)
    reverted = _residue_role_decision("openimcc", 2200.0, override)
    assert reverted is not None
    assert reverted.vapour == "openimcc"
    assert reverted.vapour != production.vapour


def test_vapour_and_constant_gamma_do_not_load_the_activity_domain(monkeypatch) -> None:
    def boom():
        raise AssertionError("activity domain loaded")

    monkeypatch.setattr(
        vapor_pressure_module.openimcc_bridge,
        "_temperature_domain_K",
        boom,
    )
    assert openimcc_binding_selected_by_inputs(
        temperature_K=BUILTIN_VAPOUR_CHANNEL_SWITCH_K,
        controls={},
        quantity_class="vapour",
    ) is False
    assert openimcc_binding_selected_by_inputs(
        temperature_K=BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0,
        controls={"high_t_melt_activity": "constant_gamma"},
        quantity_class="activity",
    ) is False


def test_consumer_follows_the_vapour_switch_constant(monkeypatch) -> None:
    shifted = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 100.0
    monkeypatch.setattr(
        vapor_pressure_module,
        "BUILTIN_VAPOUR_CHANNEL_SWITCH_K",
        shifted,
    )
    below = openimcc_binding_selected_by_inputs(
        temperature_K=shifted - 10.0,
        controls={},
        quantity_class="vapour",
    )
    above = openimcc_binding_selected_by_inputs(
        temperature_K=shifted + 10.0,
        controls={},
        quantity_class="vapour",
    )
    assert below is (_resolve(
        temperature_K=shifted - 10.0,
        vapour_openimcc_above_K=shifted,
    ).vapour == "openimcc")
    assert above is (_resolve(
        temperature_K=shifted + 10.0,
        vapour_openimcc_above_K=shifted,
    ).vapour == "openimcc")
    assert below is not above


def test_unpinned_cross_basis_pair_is_not_a_refusal() -> None:
    decision = _resolve(
        surface="run",
        evaluate="all",
        temperature_K=BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0,
        load_activity_domain_low_K=lambda: BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 600.0,
    )
    assert decision.vapour == "openimcc"
    assert decision.melt_activity == "constant_gamma"


def test_override_that_creates_the_cross_basis_pair_is_refused() -> None:
    with pytest.raises(RoleEngineRefusal) as caught:
        _resolve(
            surface="residue",
            evaluate="all",
            engine_argument="openimcc",
            role_override={
                "melt_activity": "constant_gamma",
                "vapour": "openimcc",
            },
        )
    assert caught.value.code == "role_engines_incompatible"


def test_residue_kress_pin_is_unsupported() -> None:
    with pytest.raises(RoleEngineRefusal) as caught:
        _resolve(
            surface="residue",
            evaluate="all",
            engine_argument="internal-analytical",
            role_override={"redox": "kress91"},
        )
    assert caught.value.code == "role_engine_unsupported"


def test_residue_without_a_vapour_pin_keeps_the_engine_argument() -> None:
    assert _residue_role_decision("openimcc", 2100.0, None) is None
    assert _residue_role_decision("internal-analytical", 2100.0, {}) is None
    decision = _resolve(
        surface="residue",
        evaluate="all",
        engine_argument="internal-analytical",
        role_override={"phase_equilibrium": "magemin"},
    )
    assert decision.vapour == "internal-analytical"
    assert decision.phase_equilibrium == "magemin"
    assert decision.redox == "source_oxygen"


def test_sossi_cohort_refuses_a_residue_kress_pin_before_a_prediction() -> None:
    with pytest.raises(ResidueInventoryRefusal) as caught:
        _predict_sossi_residue_cohort(
            [],
            {},
            starting_trace_ppm_by_experiment={},
            buffered_fO2_log_by_experiment={},
            engine="openimcc",
            role_override={"redox": "kress91"},
        )
    assert caught.value.reason == "role_engine_unsupported"


def test_stored_role_decision_replaces_derived_engines() -> None:
    assert _engine_roles({}, []) == {}
    pinned = _engine_roles(
        {"role_engine_decision": {"vapour": "openimcc"}},
        [],
    )
    assert pinned["vapour"] == {"openimcc"}
    derived = _engine_roles({"backend": "alphamelts"}, [])
    assert "alphamelts" in derived["phase_equilibrium"]
    overridden = _engine_roles(
        {
            "backend": "alphamelts",
            "role_engine_decision": {"phase_equilibrium": "liquid_only"},
        },
        [],
    )
    assert overridden["phase_equilibrium"] == {"liquid_only"}
    assert "alphamelts" not in overridden["phase_equilibrium"]


def test_handoff_checks_notice_and_do_not_change_values() -> None:
    producer = {"SiO2": 1.0, "MgO": 0.5}
    consumer = {"SiO2": 1.0, "MgO": 0.5}
    pressures = {"SiO": 12.5}
    same = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        producer_inventory_mol=producer,
        consumer_inventory_mol=consumer,
        activity_handoff=False,
    )
    assert _codes(same).isdisjoint({
        "handoff_element_not_conserved",
        "handoff_mass_not_conserved",
    })
    assert producer == {"SiO2": 1.0, "MgO": 0.5}
    assert consumer == {"SiO2": 1.0, "MgO": 0.5}
    assert pressures == {"SiO": 12.5}

    zero_dropped = dict(producer)
    zero_dropped["FeO"] = 0.0
    assert "handoff_element_not_conserved" not in _codes(HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        producer_inventory_mol=zero_dropped,
        consumer_inventory_mol=consumer,
    ))

    dropped = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        producer_inventory_mol=producer,
        consumer_inventory_mol={"MgO": 0.5},
    )
    assert "handoff_element_not_conserved" in _codes(dropped)
    assert producer == {"SiO2": 1.0, "MgO": 0.5}

    undeclared = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        activity_handoff=True,
    )
    assert _codes(undeclared) == {"handoff_activity_standard_state_undeclared"}

    mismatch = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        producer_standard_state={
            "convention": "raoultian_pure_endmember",
            "phase": "liquid",
            "component_basis": "parent_oxide_formula",
        },
        consumer_standard_state={
            "convention": "raoultian_pure_endmember",
            "phase": "liquid",
            "component_basis": "raoultian_pure_endmember",
        },
    )
    assert _codes(mismatch) == {"handoff_activity_standard_state_mismatch"}

    one_writer = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        fo2_log10_by_role={"redox": -8.0},
    )
    assert "handoff_fo2_overwritten" not in _codes(one_writer)
    two_writers = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        fo2_log10_by_role={"redox": -8.0, "vapour": -6.0},
    )
    assert "handoff_fo2_overwritten" in _codes(two_writers)

    solids = HandoffCrossing(
        producer_role="phase_equilibrium",
        consumer_role="vapour",
        consumer_assumes_liquid_only=True,
        liquid_fraction=0.4,
        solid_fraction=0.6,
        phase_engine_requested="liquid_only",
        phase_engine_applied=True,
    )
    assert "handoff_liquid_only_downstream_of_solids" in _codes(solids)

    phase = HandoffCrossing(
        producer_role="phase_equilibrium",
        consumer_role="vapour",
        consumer_assumes_liquid_only=True,
        phase_engine_requested="magemin",
        phase_engine_applied=False,
    )
    assert "handoff_phase_engine_not_applied" in _codes(phase)

    unparsed = HandoffCrossing(
        producer_role="melt_activity",
        consumer_role="vapour",
        producer_inventory_mol={"NotAnOxide": 1.0},
        consumer_inventory_mol={"NotAnOxide": 1.0},
    )
    assert "handoff_formula_unparsed" in _codes(unparsed)


def test_residue_handoff_record_appends_a_notice_without_changing_inventory() -> None:
    inventory = {"SiO2": 1.0}
    pressures = {"SiO": 3.0}
    decision = _resolve(
        surface="residue",
        evaluate="all",
        engine_argument="openimcc",
        role_override={"phase_equilibrium": "magemin"},
    )
    bucket: list[object] = []
    _record_residue_handoff(
        bucket,
        decision,
        producer_inventory=inventory,
        consumer_inventory=inventory,
        log10_pO2_bar=-8.0,
        temperature_K=1800.0,
        producer_standard_state=None,
        consumer_standard_state=None,
    )
    assert inventory == {"SiO2": 1.0}
    assert pressures == {"SiO": 3.0}
    assert bucket
    assert all(str(notice.reason).startswith("handoff:") for notice in bucket)
