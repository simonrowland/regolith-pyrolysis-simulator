"""Tests for the builtin OVERHEAD_BLEED provider."""

from __future__ import annotations

import copy
import math

import pytest

from engines.builtin.overhead_bleed import BuiltinOverheadBleedProvider
from simulator.accounting import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from simulator.overhead import OverheadConfigurationError
from simulator.state import Atmosphere, EvaporationFlux
from simulator.thermal_train import FiniteCapacity, NoColdTrain
from tests.chemistry.conftest import _build_sim


def test_provider_declares_overhead_bleed_authority():
    provider = BuiltinOverheadBleedProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.OVERHEAD_BLEED})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.OVERHEAD_BLEED}
    )
    assert profile.declared_accounts == frozenset(
        {
            "process.overhead_gas",
            "reservoir.oxygen_cistern_liquid_inventory",
            "terminal.offgas",
            "terminal.oxygen_melt_offgas_stored",
            "terminal.oxygen_melt_offgas_vented_to_vacuum",
            "terminal.oxygen_bubbler_external_vented_to_vacuum",
        }
    )


def test_force_drain_bleed_commits_pure_move_o2_partition(
    vapor_pressure_data, feedstocks_data, setpoints_data, monkeypatch
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 5.0},
        source="test overhead oxygen",
    )
    monkeypatch.setattr(
        sim,
        "_cold_train_capacity_policy",
        lambda: (NoColdTrain(), None),
    )

    result = sim._dispatch_overhead_bleed(
        force_drain_all=True,
    )
    proof = dict(result.transition.atom_balance_proof)

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.debits["process.overhead_gas"]["O2"] > 0.0
    assert "O2" not in result.transition.credits.get("terminal.offgas", {})
    assert abs(proof.get("O", 0.0)) <= 1.0e-12
    assert sim.atom_ledger.kg_by_account("process.overhead_gas").get(
        "O2", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(5.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)


def test_explicit_no_cold_train_uses_provider_storage_partition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 5.0},
        source="explicit NoColdTrain legacy partition fixture",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "cold_train_capacity": NoColdTrain(),
            "force_drain_all": True,
        },
    )

    assert result.status == "ok"
    assert result.transition is not None
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(5.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)


def test_bleed_conductance_caps_partial_drain(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "p_total_bar": 2.0,
            "p_downstream_bar": 1.0,
            "bleed_conductance_kg_s": 0.01,
            "dt_hr": 1.0 / 3600.0,
            "force_drain_all": False,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_kg"] == pytest.approx(0.0075)
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(0.9925)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(0.0075)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("p_total_bar", "p_downstream_bar", "expected_bled_o2_kg"),
    [
        (1.0, 1.0, 0.0),
        (0.9, 1.0, 0.0),
        (float("nan"), 1.0, 0.0),
        (float("inf"), 1.0, 0.0),
        (1.1, float("nan"), 0.0),
        (1.1, 1.0, 0.01 * (1.1**2 - 1.0**2) / 1.1**2),
    ],
)
def test_bleed_conductance_requires_positive_pressure_delta(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    p_total_bar,
    p_downstream_bar,
    expected_bled_o2_kg,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "p_total_bar": p_total_bar,
            "p_downstream_bar": p_downstream_bar,
            "bleed_conductance_kg_s": 0.01,
            "dt_hr": 1.0 / 3600.0,
            "force_drain_all": False,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic.get("bled_o2_kg", 0.0) == pytest.approx(
        expected_bled_o2_kg
    )
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(1.0 - expected_bled_o2_kg)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored"
    ).get("O2", 0.0) == pytest.approx(expected_bled_o2_kg)


def test_bleed_rate_tends_continuously_to_zero_with_pressure_delta():
    provider = BuiltinOverheadBleedProvider()
    common = {
        "bleed_conductance_kg_s": 0.01,
        "dt_hr": 1.0 / 3600.0,
    }

    one_bar_delta = provider._bled_species_mol(
        {"O2": 1.0},
        total_mol=1.0,
        total_kg=1.0,
        controls={**common, "p_total_bar": 2.0, "p_downstream_bar": 1.0},
    )["O2"]
    tiny_delta = provider._bled_species_mol(
        {"O2": 1.0},
        total_mol=1.0,
        total_kg=1.0,
        controls={
            **common,
            "p_total_bar": 1.0 + 1.0e-12,
            "p_downstream_bar": 1.0,
        },
    )["O2"]
    equal_pressure = provider._bled_species_mol(
        {"O2": 1.0},
        total_mol=1.0,
        total_kg=1.0,
        controls={**common, "p_total_bar": 1.0, "p_downstream_bar": 1.0},
    )

    assert one_bar_delta == pytest.approx(0.0075)
    assert 0.0 < tiny_delta < one_bar_delta * 1.0e-10
    assert equal_pressure == {}


@pytest.mark.parametrize(
    ("control_name", "control_value"),
    [
        ("force_drain_all", "false"),
        ("bleed_conductance_kg_s", float("inf")),
        ("dt_hr", float("nan")),
        ("dt_hr", -1.0),
        ("dt_hr", None),
        ("p_total_bar", float("nan")),
        ("p_downstream_bar", float("inf")),
        ("o2_vented_kg", float("inf")),
        ("o2_vented_kg", None),
        ("max_o2_flow_kg_hr", -1.0),
        ("external_o2_in_overhead_mol", float("inf")),
    ],
)
def test_destructive_bleed_controls_fail_closed_without_ledger_mutation(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    control_name,
    control_value,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="test overhead oxygen"
    )
    controls = {
        "force_drain_all": False,
        "bleed_conductance_kg_s": 0.01,
        "dt_hr": 1.0,
        "p_total_bar": 2.0,
        "p_downstream_bar": 0.0,
    }
    controls[control_name] = control_value

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_BLEED,
        temperature_C=1500.0,
        pressure_bar=2.0,
        control_inputs=controls,
    )

    assert result.status == "unsupported"
    assert result.transition is None
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("probe", "expected_reason"),
    [
        ("negative_downstream_pressure", "p_downstream_bar"),
        ("absent_total_pressure", "p_total_bar"),
        ("negative_external_o2", "external_o2_in_overhead_mol"),
    ],
)
def test_core_passes_raw_destructive_bleed_controls_to_provider_guard(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    probe,
    expected_reason,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="test overhead oxygen"
    )
    sim._overhead_headspace_config = {
        "conductance_kg_s": 0.01,
        "downstream_pressure_bar": 0.0,
    }
    monkeypatch.setattr(
        sim,
        "_overhead_gas_equilibrium_diagnostic",
        lambda: {"p_total_bar": 2.0},
    )
    dispatch_kwargs = {"force_drain_all": True}
    if probe == "negative_downstream_pressure":
        sim._overhead_headspace_config["downstream_pressure_bar"] = -1.0
    elif probe == "absent_total_pressure":
        monkeypatch.setattr(sim, "_overhead_gas_equilibrium_diagnostic", lambda: {})
    elif probe == "negative_external_o2":
        sim._o2_bubbler_external_o2_in_overhead_mol = -1.0

    before_melt = copy.deepcopy(sim.melt)
    before_overhead = copy.deepcopy(sim.overhead)
    before_overhead_ledger = copy.deepcopy(
        sim.atom_ledger.kg_by_account("process.overhead_gas")
    )
    before_transitions = copy.deepcopy(tuple(sim.atom_ledger.transitions))
    before_recorded_snapshots = copy.deepcopy(tuple(sim.record.snapshots))

    result = sim._dispatch_overhead_bleed(**dispatch_kwargs)

    assert result.status == "unsupported"
    assert result.transition is None
    assert (result.diagnostic or {})["reason"] == (
        f"{expected_reason} must be a finite non-negative number"
    )
    assert sim.melt == before_melt
    assert sim.overhead == before_overhead
    assert sim.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ) == before_overhead_ledger
    assert tuple(sim.atom_ledger.transitions) == before_transitions
    assert tuple(sim.record.snapshots) == before_recorded_snapshots


def test_core_passes_raw_invalid_bleed_conductance_to_provider_guard(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="test overhead oxygen"
    )
    sim._overhead_headspace_config = {"conductance_kg_s": -0.01}
    before = sim.atom_ledger.kg_by_account("process.overhead_gas")["O2"]

    result = sim._dispatch_overhead_bleed()

    assert result.status == "unsupported"
    assert result.transition is None
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(before)


def test_zero_duration_bleed_is_a_zero_transition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas", {"O2": 1.0}, source="test overhead oxygen"
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "force_drain_all": False,
            "bleed_conductance_kg_s": 1.0,
            "dt_hr": 0.0,
            "p_total_bar": 2.0,
            "p_downstream_bar": 0.0,
        },
    )

    assert result.status == "ok"
    assert result.transition is None
    assert sim.atom_ledger.kg_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(1.0)


def test_live_headspace_bleed_conductance_uses_headspace_species_m_avg(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1500.0
    sim.melt.p_total_mbar = 10.0
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Na": 1.0},
        source="test sodium headspace",
    )

    expected = sim.overhead_model._pipe_conductance(
        1000.0,
        1500.0,
        species_kg_for_M_avg={"Na": 1.0},
    )

    assert sim._headspace_bleed_conductance_kg_s() == pytest.approx(expected)


@pytest.mark.parametrize(
    "downstream_pressure_bar",
    [
        pytest.param(10.0, id="finite_downstream"),
        pytest.param(float("nan"), id="nan_downstream"),
    ],
)
def test_pn2_transport_projection_does_not_bleed_against_downstream_pressure(
    downstream_pressure_bar,
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0
    sim.melt.p_total_mbar = 147.0
    sim.melt.temperature_C = 1500.0
    sim._overhead_headspace_config[
        "downstream_pressure_bar"
    ] = downstream_pressure_bar
    o2_molar_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": o2_molar_mass},
        source="test overhead oxygen",
    )

    ledger_pO2 = sim._headspace_ledger_pO2_bar_from_o2_mol(1.0)
    downstream = sim._headspace_downstream_pressure_bar()
    projected = sim._pn2_sweep_transport_pO2_bar(1.0)

    if math.isfinite(downstream_pressure_bar):
        assert downstream == pytest.approx(downstream_pressure_bar)
        assert ledger_pO2 < downstream
    else:
        assert not math.isfinite(downstream)
    assert projected == pytest.approx(ledger_pO2)


def test_overhead_model_refuses_nonfinite_downstream_pressure(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    with pytest.raises(
        OverheadConfigurationError,
        match="p_downstream_bar must be finite",
    ):
        sim.overhead_model.update(
            EvaporationFlux(),
            sim.melt,
            sim.train,
            p_downstream_bar=float("nan"),
        )


def test_pn2_transport_projection_tends_to_equal_pressure_value_as_dp_closes(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0
    sim.melt.p_total_mbar = 147.0
    sim.melt.temperature_C = 1500.0
    o2_molar_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": o2_molar_mass},
        source="test overhead oxygen",
    )
    sim._overhead_headspace_config["conductance_kg_s"] = 1.0e-6
    p_upstream_bar = sim._headspace_ledger_pO2_bar_from_o2_mol(1.0)

    sim._overhead_headspace_config["downstream_pressure_bar"] = 0.0
    vacuum_projection = sim._pn2_sweep_transport_pO2_bar(1.0)
    sim._overhead_headspace_config[
        "downstream_pressure_bar"
    ] = p_upstream_bar
    equal_pressure_projection = sim._pn2_sweep_transport_pO2_bar(1.0)
    near_equal_projections = []
    for pressure_delta_bar in (1.0e-6, 1.0e-9, 1.0e-12):
        sim._overhead_headspace_config[
            "downstream_pressure_bar"
        ] = p_upstream_bar - pressure_delta_bar
        near_equal_projections.append(sim._pn2_sweep_transport_pO2_bar(1.0))

    assert vacuum_projection < near_equal_projections[0]
    assert near_equal_projections == sorted(near_equal_projections)
    assert near_equal_projections[-1] == pytest.approx(
        equal_pressure_projection,
        abs=1.0e-9,
    )


def test_bleed_conductance_is_kg_s_not_per_bar(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "p_total_bar": 2.0,
            "p_downstream_bar": 1.9,
            "bleed_conductance_kg_s": 0.01,
            "dt_hr": 1.0 / 3600.0,
            "force_drain_all": False,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_kg"] == pytest.approx(0.000975)


def test_legacy_bleed_conductance_alias_remains_kg_s(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 1.0},
        source="test overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "p_total_bar": 2.0,
            "p_downstream_bar": 1.9,
            "bleed_conductance_kg_s": None,
            "bleed_conductance_kg_s_per_bar": 0.01,
            "dt_hr": 1.0 / 3600.0,
            "force_drain_all": False,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_kg"] == pytest.approx(0.000975)


def test_external_o2_bleed_is_not_stored_as_melt_offgas_product(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="test mixed overhead oxygen",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "force_drain_all": True,
            "external_o2_in_overhead_mol": 4.0,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert diagnostic["bled_o2_mol"] == pytest.approx(10.0)
    assert diagnostic["external_o2_bled_mol"] == pytest.approx(4.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(6.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_bubbler_external_vented_to_vacuum"
    )["O2"] == pytest.approx(4.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    ).get("O2", 0.0) == pytest.approx(0.0)


def test_finite_capacity_commits_admission_and_continuous_relief_once(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 12.0 * o2_molar_mass},
        source="test finite-capacity overhead oxygen",
    )
    before_transitions = len(sim.atom_ledger.transitions)
    mre_before = sim.atom_ledger.mol_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0)

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "bleed_conductance_kg_s": 1.0,
            "p_total_bar": 1.0,
            "p_downstream_bar": 0.0,
            "external_o2_in_overhead_mol": 2.0,
            "cold_train_capacity": FiniteCapacity(o2_molar_mass),
            "dt_hr": 1.0,
            "p_ref_Pa": 1000.0,
            "p_open_Pa": 900.0,
            "vessel_rating_Pa": 2000.0,
            "k_relief_kg_hr_Pa": o2_molar_mass / 100.0,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert result.transition is not None
    assert len(sim.atom_ledger.transitions) == before_transitions + 1
    assert diagnostic["o2_admitted_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_relieved_mol"] == pytest.approx(1.0)
    assert diagnostic["external_o2_bled_mol"] == pytest.approx(2.0)
    assert diagnostic["o2_held_mol"] == pytest.approx(8.0)
    assert diagnostic["bled_o2_mol"] == pytest.approx(4.0)
    assert sim.atom_ledger.mol_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(8.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_stored"
    )["O2"] == pytest.approx(1.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    )["O2"] == pytest.approx(1.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_bubbler_external_vented_to_vacuum"
    )["O2"] == pytest.approx(2.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_mre_anode_stored"
    ).get("O2", 0.0) == pytest.approx(mre_before)
    assert max(
        (abs(value) for value in result.transition.atom_balance_proof.values()),
        default=0.0,
    ) <= 1e-12


def test_finite_capacity_relief_flows_with_zero_ordinary_conductance(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 12.0 * o2_molar_mass},
        source="zero-conductance relief fixture",
    )

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "bleed_conductance_kg_s": 0.0,
            "p_total_bar": 1.0,
            "p_downstream_bar": 0.0,
            "external_o2_in_overhead_mol": 2.0,
            "cold_train_capacity": FiniteCapacity(o2_molar_mass),
            "dt_hr": 1.0,
            "p_ref_Pa": 1000.0,
            "p_open_Pa": 900.0,
            "vessel_rating_Pa": 2000.0,
            "k_relief_kg_hr_Pa": o2_molar_mass / 100.0,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert result.transition is not None
    assert diagnostic["candidate_bled_o2_mol"] == 0.0
    assert diagnostic["o2_admitted_mol"] == 0.0
    assert diagnostic["o2_relieved_mol"] == pytest.approx(1.0)
    assert diagnostic["external_o2_bled_mol"] == 0.0
    assert diagnostic["o2_held_mol"] == pytest.approx(9.0)
    assert sim.atom_ledger.mol_by_account("process.overhead_gas")[
        "O2"
    ] == pytest.approx(11.0)
    assert sim.atom_ledger.mol_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum"
    )["O2"] == pytest.approx(1.0)
    assert max(
        (abs(value) for value in result.transition.atom_balance_proof.values()),
        default=0.0,
    ) <= 1e-12


def test_finite_capacity_accumulator_commits_atom_balanced_cistern_inventory(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="accumulator surge fixture",
    )
    total_kg_before = sum(sim.atom_ledger.total_kg_by_account().values())

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "bleed_conductance_kg_s": 1.0,
            "p_total_bar": 1.0,
            "p_downstream_bar": 0.0,
            "cold_train_capacity": FiniteCapacity(o2_molar_mass),
            "dt_hr": 1.0,
            "p_ref_Pa": 1000.0,
            "p_open_Pa": 900.0,
            "vessel_rating_Pa": 2000.0,
            "k_relief_kg_hr_Pa": o2_molar_mass / 100.0,
            "accumulator_enabled": True,
            "cavern_capacity_kg": 5.0 * o2_molar_mass,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert result.transition is not None
    assert diagnostic["o2_admitted_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_accumulated_mol"] == pytest.approx(5.0)
    assert diagnostic["o2_relieved_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_held_mol"] == pytest.approx(3.0)
    assert diagnostic["cistern_fill_kg"] == 0.0
    assert diagnostic["cistern_fill_after_kg"] == pytest.approx(
        5.0 * o2_molar_mass
    )
    assert diagnostic["refreeze_duty_kWh_deferred"] == pytest.approx(
        5.0 * 6820.0 / 3_600_000.0
    )
    assert sim.atom_ledger.mol_by_account(
        "reservoir.oxygen_cistern_liquid_inventory"
    )["O2"] == pytest.approx(5.0)
    assert max(
        (abs(value) for value in result.transition.atom_balance_proof.values()),
        default=0.0,
    ) <= 1e-12
    total_kg_after = sum(sim.atom_ledger.total_kg_by_account().values())
    assert total_kg_after == pytest.approx(total_kg_before, rel=5e-14)


def test_finite_capacity_accumulator_off_is_p2_3_provider_parity(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    outcomes = []
    for explicit_off in (False, True):
        sim = _build_sim(
            "lunar_mare_low_ti",
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
        )
        o2_molar_mass = resolve_species_formula(
            "O2", sim.species_formula_registry
        ).molar_mass_kg_per_mol()
        sim.atom_ledger.load_external(
            "process.overhead_gas",
            {"O2": 10.0 * o2_molar_mass},
            source="accumulator-off parity fixture",
        )
        controls = {
            "bleed_conductance_kg_s": 1.0,
            "p_total_bar": 1.0,
            "p_downstream_bar": 0.0,
            "cold_train_capacity": FiniteCapacity(o2_molar_mass),
            "dt_hr": 1.0,
            "p_ref_Pa": 1000.0,
            "p_open_Pa": 900.0,
            "vessel_rating_Pa": 2000.0,
            "k_relief_kg_hr_Pa": o2_molar_mass / 100.0,
        }
        if explicit_off:
            controls["accumulator_enabled"] = False
        result = sim._dispatch_and_commit(
            ChemistryIntent.OVERHEAD_BLEED,
            control_inputs=controls,
        )
        outcomes.append((
            dict(result.diagnostic or {}),
            result.transition.debits,
            result.transition.credits,
            sim.atom_ledger.mol_by_account(),
        ))

    assert outcomes[1] == outcomes[0]
    assert "o2_accumulated_mol" not in outcomes[0][0]


def test_full_cistern_resumes_relief_through_provider_and_ledger(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    o2_molar_mass = resolve_species_formula(
        "O2", sim.species_formula_registry
    ).molar_mass_kg_per_mol()
    cistern_account = "reservoir.oxygen_cistern_liquid_inventory"
    sim.atom_ledger.load_external(
        cistern_account,
        {"O2": 5.0 * o2_molar_mass},
        source="full cistern fixture",
    )
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"O2": 10.0 * o2_molar_mass},
        source="full cistern surge fixture",
    )
    transitions_before = len(sim.atom_ledger.transitions)
    total_kg_before = sum(sim.atom_ledger.total_kg_by_account().values())

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "bleed_conductance_kg_s": 1.0,
            "p_total_bar": 1.0,
            "p_downstream_bar": 0.0,
            "cold_train_capacity": FiniteCapacity(o2_molar_mass),
            "dt_hr": 1.0,
            "p_ref_Pa": 1000.0,
            "p_open_Pa": 900.0,
            "vessel_rating_Pa": 2000.0,
            "k_relief_kg_hr_Pa": o2_molar_mass,
            "accumulator_enabled": True,
            "cavern_capacity_kg": 5.0 * o2_molar_mass,
        },
    )

    diagnostic = dict(result.diagnostic or {})
    assert len(sim.atom_ledger.transitions) == transitions_before + 1
    assert diagnostic["o2_admitted_mol"] == pytest.approx(1.0)
    assert diagnostic["o2_accumulated_mol"] == 0.0
    assert diagnostic["o2_relieved_mol"] == pytest.approx(9.0)
    assert diagnostic["o2_held_mol"] == 0.0
    assert diagnostic["cistern_fill_after_kg"] == pytest.approx(
        5.0 * o2_molar_mass
    )
    assert sim.atom_ledger.mol_by_account(cistern_account)[
        "O2"
    ] == pytest.approx(5.0)
    assert cistern_account not in result.transition.credits
    assert max(
        (abs(value) for value in result.transition.atom_balance_proof.values()),
        default=0.0,
    ) <= 1e-12
    total_kg_after = sum(sim.atom_ledger.total_kg_by_account().values())
    assert total_kg_after == pytest.approx(total_kg_before, rel=5e-14)


@pytest.mark.parametrize(
    ("name", "value", "reason"),
    [
        ("k_relief_kg_hr_Pa", 0.0, "k_relief_kg_hr_Pa"),
        ("p_open_Pa", 0.0, "p_open_Pa"),
        ("vessel_rating_Pa", 0.0, "vessel_rating_Pa"),
    ],
)
def test_finite_capacity_requires_positive_relief_controls(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    name,
    value,
    reason,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    controls = {
        "cold_train_capacity": FiniteCapacity(1.0),
        "k_relief_kg_hr_Pa": 1.0,
        "p_open_Pa": 1.0,
        "vessel_rating_Pa": 2.0,
    }
    controls[name] = value

    result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs=controls,
    )

    assert result.status == "unsupported"
    assert result.transition is None
    assert (result.diagnostic or {})["reason"] == (
        f"{reason} must be a finite positive number"
    )
