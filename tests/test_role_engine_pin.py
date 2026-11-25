"""The role pin is the owner's decision, and the callers honour it.

Expected booleans come from the selector that runs. This file does not paste
a temperature comparison of its own. A mutation fails when the residue
pressure branch ignores ``decision.vapour`` and keeps the engine argument.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import engines.builtin.vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import (
    BUILTIN_VAPOUR_CHANNEL_SWITCH_K,
    openimcc_binding_selected_by_inputs,
)
from simulator.battery.leaderboard import _engine_roles
from simulator.battery.residue import (
    ResidueInventoryRefusal,
    _make_residue_pressure_model,
    _predict_sossi_residue_cohort,
    _residue_role_decision,
)
from simulator.chemistry.role_engines import (
    HandoffCrossing,
    RoleEngineRefusal,
    handoff_notices,
    resolve_role_engines,
)
from simulator.physical_constants import PA_PER_BAR


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


def _notice_codes(bucket: list[object]) -> set[str]:
    codes = set()
    for notice in bucket:
        reason = str(getattr(notice, "reason", ""))
        assert reason.startswith("handoff:")
        codes.add(reason.split(":", 2)[1])
    return codes


def _stub_pressure_branches(monkeypatch, ia_response: object) -> list[str]:
    """Stub both vapour branches. The list records which one the closure calls."""

    calls: list[str] = []

    def fake_state(composition, temperature_K, **kwargs):
        calls.append("openimcc")
        return SimpleNamespace(
            parent_oxide_activities={"SiO2": 0.4},
            parent_oxides=("SiO2",),
            flags=(),
            extrapolated=False,
            pack_model_id="stub",
            pack_version="stub",
            pack_digest="stub",
            openimcc_version="stub",
        )

    def fake_gas(activities, temperature_K, pressure_bar, gas_pack, **kwargs):
        calls.append("openimcc")
        return {"SiO": 2.0}

    def fake_ia(**kwargs):
        calls.append("internal-analytical")
        return ia_response

    monkeypatch.setattr(
        "simulator.battery.residue._cached_isolated_openimcc_state",
        fake_state,
    )
    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.evaluate_gas",
        fake_gas,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._new_internal_analytical_core",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.internal_analytical_residue_vapor_pressure",
        fake_ia,
    )
    return calls


def _pressure_model(engine: str, temperature_K: float, role_override):
    return _make_residue_pressure_model(
        engine,
        (("SiO", ("SiO2", 1.0, 0.0)),),
        object(),
        temperature_K=temperature_K,
        total_pressure_Pa=1.0,
        engine_metadata={},
        engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing",
        caller="role pin test",
        role_override=role_override,
    )


def test_dropping_the_override_restores_the_unpinned_binding(monkeypatch) -> None:
    """The pressure branch reads decision.vapour. No pin keeps the engine argument."""

    ia_response = SimpleNamespace(
        vapor_pressures_Pa={"SiO": 12.5},
        diagnostics={},
        liquid_fraction=None,
    )
    calls = _stub_pressure_branches(monkeypatch, ia_response)
    above = BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 10.0
    inventory = {"SiO2": 1.0}
    pinned = _pressure_model("openimcc", above, {"vapour": "internal-analytical"})
    pinned_pressures = pinned(dict(inventory), -8.0)
    assert pinned_pressures == {"SiO": 12.5}
    assert pinned.role_engine_decision["vapour"] == "internal-analytical"
    assert calls == ["internal-analytical"]

    calls.clear()
    below = BUILTIN_VAPOUR_CHANNEL_SWITCH_K - 10.0
    unpinned = _pressure_model("openimcc", below, None)
    unpinned_pressures = unpinned(dict(inventory), -8.0)
    # Below the switch the gate would select internal-analytical. The unpinned
    # closure still runs the engine argument, so these pressures are OpenIMCC's.
    assert unpinned_pressures == {"SiO": 2.0 * PA_PER_BAR}
    assert getattr(unpinned, "role_engine_decision", None) is None
    assert calls == ["openimcc", "openimcc"]
    assert pinned_pressures != unpinned_pressures


def test_residue_consumer_uses_the_owner_override(monkeypatch) -> None:
    """A phase-only pin above the switch runs the vapour engine the gate stored."""

    ia_response = SimpleNamespace(
        vapor_pressures_Pa={"SiO": 12.5},
        diagnostics={},
        liquid_fraction=None,
    )
    calls = _stub_pressure_branches(monkeypatch, ia_response)
    temperature_K = BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 150.0
    pin = {"phase_equilibrium": "magemin"}
    decision = _residue_role_decision("internal-analytical", temperature_K, pin)
    binding = openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K,
        controls={
            "high_t_melt_activity": "openimcc",
            "role_engine_override": pin,
        },
        quantity_class="vapour",
    )
    assert (decision.vapour == "openimcc") is binding
    inventory = {"NotAnOxide": 1.0}
    model = _pressure_model("internal-analytical", temperature_K, pin)
    pressures = model(inventory, -8.0)
    assert pressures == {"SiO": 2.0 * PA_PER_BAR}
    assert inventory == {"NotAnOxide": 1.0}
    assert model.role_engine_decision["vapour"] == "openimcc"
    assert "internal-analytical" not in calls
    codes = _notice_codes(model.handoff_notices)
    assert "handoff_activity_standard_state_undeclared" in codes
    assert "handoff_phase_engine_not_applied" in codes
    assert "handoff_formula_unparsed" not in codes
    assert "handoff_element_not_conserved" not in codes
    assert "handoff_mass_not_conserved" not in codes
    assert "handoff_liquid_only_downstream_of_solids" not in codes


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


def test_residue_without_a_vapour_pin_stores_the_gate(monkeypatch) -> None:
    assert _residue_role_decision("openimcc", 2100.0, None) is None
    assert _residue_role_decision("internal-analytical", 2100.0, {}) is None
    pin = {"phase_equilibrium": "magemin"}
    above = BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 150.0
    decision = _residue_role_decision("internal-analytical", above, pin)
    vapour_selected = openimcc_binding_selected_by_inputs(
        temperature_K=above,
        controls={"high_t_melt_activity": "openimcc", "role_engine_override": pin},
        quantity_class="vapour",
    )
    assert decision.phase_equilibrium == "magemin"
    assert decision.redox == "source_oxygen"
    assert (decision.vapour == "openimcc") is vapour_selected
    cold_temperature_K = 1600.0
    cold = _residue_role_decision("internal-analytical", cold_temperature_K, pin)
    activity_selected = openimcc_binding_selected_by_inputs(
        temperature_K=cold_temperature_K,
        controls={"high_t_melt_activity": "openimcc", "role_engine_override": pin},
        quantity_class="activity",
    )
    assert (cold.melt_activity == "openimcc") is activity_selected

    # Below the switch a phase-only pin stays on the analytical branch, and a
    # phase key is not copied onto the core.
    ia_response = SimpleNamespace(
        vapor_pressures_Pa={"SiO": 12.5},
        diagnostics={},
        liquid_fraction=None,
    )
    cores: list[SimpleNamespace] = []

    def fake_core():
        core = SimpleNamespace()
        cores.append(core)
        return core

    _stub_pressure_branches(monkeypatch, ia_response)
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._new_internal_analytical_core",
        fake_core,
    )
    below = BUILTIN_VAPOUR_CHANNEL_SWITCH_K - 10.0
    model = _pressure_model("internal-analytical", below, pin)
    assert model({"SiO2": 1.0}, -8.0) == {"SiO": 12.5}
    assert model.role_engine_decision["vapour"] == "internal-analytical"
    assert not hasattr(cores[-1], "_role_engine_override")
    assert not hasattr(cores[-1], "_high_t_melt_activity")


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


def test_analytical_handoff_fires_on_the_response_crossing(monkeypatch) -> None:
    """Mismatch, fO2, and solids come from the analytical response, not a second inventory."""

    response = SimpleNamespace(
        vapor_pressures_Pa={"SiO": 12.5},
        liquid_fraction=0.4,
        solid_fraction=0.6,
        diagnostics={
            "vapor_pressure_diagnostic": {
                "activities_standard_state": {
                    "convention": "raoultian_pure_endmember",
                    "phase": "liquid",
                    "component_basis": "raoultian_pure_endmember",
                },
                "high_t_melt_activity": {
                    "selected_parent_activities_standard_state": {
                        "convention": "raoultian_pure_endmember",
                        "phase": "liquid",
                        "component_basis": "parent_oxide_formula",
                    },
                },
                "source_reaction_fO2_log10": -6.0,
            },
        },
    )
    cores: list[SimpleNamespace] = []

    def fake_core():
        core = SimpleNamespace()
        cores.append(core)
        return core

    _stub_pressure_branches(monkeypatch, response)
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery._new_internal_analytical_core",
        fake_core,
    )
    inventory = {"NotAnOxide": 1.0}
    model = _pressure_model(
        "internal-analytical",
        1800.0,
        {"vapour": "internal-analytical", "phase_equilibrium": "magemin"},
    )
    pressures = model(inventory, -8.0)
    assert pressures == {"SiO": 12.5}
    assert inventory == {"NotAnOxide": 1.0}
    codes = _notice_codes(model.handoff_notices)
    assert "handoff_activity_standard_state_mismatch" in codes
    assert "handoff_fo2_overwritten" in codes
    assert "handoff_liquid_only_downstream_of_solids" in codes
    assert "handoff_formula_unparsed" not in codes
    assert "handoff_element_not_conserved" not in codes
    assert "handoff_mass_not_conserved" not in codes
    assert cores[-1]._role_engine_override == {"vapour": "internal-analytical"}
    assert not hasattr(cores[-1], "_high_t_melt_activity")


def test_hashimoto_refusal_keeps_the_role_decision() -> None:
    from simulator.battery.enums import Engine, Quantity
    from simulator.battery.score import (
        _HASHIMOTO_SOURCE_ID,
        _hashimoto_residue_prediction,
        _residue_prediction_cache_entry,
    )

    experiment_id = "H-1"
    decision = {
        "phase_equilibrium": "liquid_only",
        "melt_activity": "constant_gamma",
        "vapour": "internal-analytical",
        "redox": "source_oxygen",
    }

    def row(provenance: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            provenance=provenance,
            primary_geometry_policy_id="sphere_shrinking",
            alpha_arm="alpha_runtime_catalog",
            primary_oxide_wt_pct={},
            geometry_oxide_wt_pct={},
            sensitivity_band_wt_pct={},
        )

    refusal = {
        "geometry_refusal_by_geometry": {
            "sphere_shrinking": {
                "reason": "residue_pressure_law_exponent_mismatch",
            },
        },
    }
    reference = SimpleNamespace(
        identity=SimpleNamespace(quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION),
        observation_id="obs",
        source_id=_HASHIMOTO_SOURCE_ID,
        experiment_id=experiment_id,
        locator=None,
        read_from=None,
    )
    engine = Engine.INTERNAL_ANALYTICAL

    def predict(provenance: dict[str, object], role_override):
        cache: dict = {}
        pinned_row = row(provenance)
        _residue_prediction_cache_entry(
            cache,
            _HASHIMOTO_SOURCE_ID,
            engine,
            None,
            value={
                experiment_id + "::alpha_common_unity_sensitivity": pinned_row,
                experiment_id + "::alpha_runtime_catalog": pinned_row,
            },
            role_override=role_override,
        )
        return _hashimoto_residue_prediction(
            None,
            reference,
            engine,
            cache,
            role_override=role_override,
        )

    pinned = predict({**refusal, "role_engine_decision": decision}, {"melt_activity": "constant_gamma"})
    assert pinned.refusal_detail["role_engine_decision"] == decision
    assert pinned.refusal_detail["reason"] == "residue_pressure_law_exponent_mismatch"
    unpinned = predict(refusal, None)
    assert "role_engine_decision" not in unpinned.refusal_detail
    assert unpinned.refusal_detail["reason"] == "residue_pressure_law_exponent_mismatch"
