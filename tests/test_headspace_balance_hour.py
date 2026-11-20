"""Chunk 3: the headspace balance is the committed hour.

The seven tests named in the approved design. Goldens are not
regenerated here. A cold O2 inventory commits. A hot lunar mixture
puts Al into the fast sweep. Al has no certified collision diameter.
The pipe extrapolates one from the P0a band, flags that species, and
the hour still commits. A missing molar mass still refuses. This
chunk does not add a collision diameter and does not drop the vapor.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
from unittest.mock import patch

from simulator.accounting.lots import MaterialLot
from simulator.accounting.run_artifact import (
    CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT,
)
from simulator.capacity_coupling import (
    CapacityCouplingRefusalError,
    CapacityShadowRefusal,
    CapacityShadowResult,
    solve_capacity_shadow,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.core import (
    FO2_BUFFER_ACCOUNT,
    OXYGEN_RESERVOIR_NOOP_MOL,
    OXYGEN_SPECIES,
    PyrolysisSimulator,
    _sorted_positive_stoich_o2_mol,
)
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR
from simulator.state import MOLAR_MASS, CampaignPhase
from simulator.thermal_train import NoColdTrain
from tests.chemistry.conftest import _build_sim, _load_yaml
from tests.test_capacity_coupling import (
    _HS_D_M,
    _HS_L_M,
    _HS_T_K,
    _HS_V_M3,
    _SOURCE_02_KG_H,
)


def _lunar():
    sim = _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
    sim.melt.campaign = CampaignPhase.C4
    return sim


def _hold_temperature(sim, temperature_C: float) -> None:
    sim.melt.temperature_C = temperature_C

    def _target(*_args, **_kwargs):
        return temperature_C, 0.0

    sim.campaign_mgr.get_temp_target = _target


def _seed(sim, account: str, species_mol: dict[str, float], source: str) -> None:
    sim.atom_ledger.load_external_mol(
        account,
        species_mol,
        source=source,
        material_origin="feedstock",
    )


def _names(sim, start: int) -> list[str]:
    return [transition.name for transition in sim.atom_ledger.transitions[start:]]


def _geometry_o2(source_kg_hr: float, rating_Pa: float):
    # Same pipe the live hour passes: diameter, length, and the
    # inventory temperature and volume the chunk-2 O2 root used.
    # The rating is the only change between the two calls.
    return solve_capacity_shadow(
        pre_holdup_mol={},
        molar_mass_kg_mol={"O2": _o2_mass()},
        flux_kg_hr_at_partials=lambda _partials: {"O2": source_kg_hr},
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=_HS_T_K,
        volume_m3=_HS_V_M3,
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=0.0,
        p_open_Pa=1.0,
        vessel_rating_Pa=rating_Pa,
        pipe_diameter_m=_HS_D_M,
        pipe_length_m=_HS_L_M,
        authoritative=True,
    )


def _o2_mass() -> float:
    from simulator.state import MOLAR_MASS

    return MOLAR_MASS["O2"] / 1000.0


def _al_notice(notices):
    matched = [
        notice
        for notice in notices
        if notice["species"] == "Al"
        and notice["reason"] == "uncertified_collision_diameter"
    ]
    assert matched
    notice = matched[0]
    assert notice["authority_level"] == "extrapolated"
    assert notice["was_clamped"] is False
    band = notice["certified_band"]
    assert "O2" in band["species"]
    assert "Al" not in band["species"]
    low, high = band["collision_diameter_m"]
    assert low > 0.0
    assert high >= low
    assert low <= notice["collision_diameter_m_used"] <= high
    return notice


def test_evaporation_bulk_partial_is_the_solved_inventory():
    """The opening publisher call sees the seeded inventory, not the map.

    The clamp zeros Na at that partial. Na has no certified diameter.
    The hour commits with that flag, and the opening rate stays 0.
    """

    sim = _lunar()
    _hold_temperature(sim, _HS_T_K - 273.15)
    # n = P V / (R T). 1e6 Pa is far above a melt vapor pressure, so
    # the opening Langmuir rate of the seeded species is the clamp.
    # Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
    moles = 1.0e6 * sim._headspace_volume_m3() / (
        GAS_CONSTANT * sim._headspace_temperature_K()
    )
    _seed(sim, "process.overhead_gas", {"Na": moles}, "hsc3 partial seed")
    seeded_pa = sim._headspace_inventory_pressure_Pa()
    sim._melt_headspace_composition_mbar = {"Na": 0.0}
    sim._evaporation_bulk_partial_pressure_pa = lambda _species: 0.0

    snapshot = sim._step_one_hour()

    recorded = sim._headspace_publisher_partials_Pa
    assert recorded["Na"] == pytest.approx(seeded_pa)
    assert recorded["Na"] != pytest.approx(0.0)
    assert sim._headspace_publisher_rates_at_open_kg_hr.get("Na", 0.0) == 0.0
    cap = sim._headspace_capacity_result
    assert cap is not None and cap.authoritative is True
    na = [
        notice
        for notice in cap.species_notices
        if notice["species"] == "Na"
        and notice["reason"] == "uncertified_collision_diameter"
    ]
    assert na
    assert na[0]["authority_level"] == "extrapolated"
    assert "Na" not in na[0]["certified_band"]["species"]
    assert math.isfinite(float(cap.evaporation_flux_kg_hr["Na"]))
    assert any(
        notice["species"] == "Na" for notice in snapshot.headspace_species_notices
    )


def test_snapshot_pressure_is_the_inventory_partial():
    sim = _lunar()
    _hold_temperature(sim, 25.0)
    sim.melt.p_total_mbar = 10000.0
    _seed(sim, "process.overhead_gas", {"O2": 1.0}, "hsc3 snapshot seed")

    snapshot = sim.step()

    cap = sim._headspace_capacity_result
    assert cap.authoritative is True
    solved_pa = math.fsum(float(p) for p in cap.partial_pressures_Pa.values())
    solved_o2 = float(cap.partial_pressures_Pa.get(OXYGEN_SPECIES, 0.0))
    assert sim.overhead.pressure_mbar == pytest.approx(solved_pa / 100.0)
    assert float(sim.overhead.composition.get(OXYGEN_SPECIES, 0.0)) == pytest.approx(
        solved_o2 / 100.0
    )
    assert sim.overhead.pressure_mbar != pytest.approx(10000.0)
    assert all(
        notice["species"] != OXYGEN_SPECIES for notice in cap.species_notices
    )
    notice = snapshot.headspace_carry_shortfall
    assert notice["direction"] == "below_command"
    assert notice["commanded_pressure_bar"] == pytest.approx(10000.0 * 100.0 / PA_PER_BAR)
    assert notice["solved_pressure_bar"] == pytest.approx(solved_pa / PA_PER_BAR)
    assert snapshot.overhead.pressure_mbar == pytest.approx(sim.overhead.pressure_mbar)


def test_redox_credit_and_bleed_share_one_hour():
    """A saturated hour with FeO too thin to absorb the credit exchanges once.

    2 FeO + 1/2 O2 = Fe2O3, so the uptake cannot exceed a quarter of
    the FeO inventory. Leaving 0.4 mol FeO keeps that cap under the
    evaporative credit, and the post-uptake excess is positive. The
    settle posts that sourced excess once. The respeciation debit is
    the uptake. The published pressure is the solved inventory. A
    constant-rate re-solve of the booked sources, and the pure-O2
    root of that O2 rate, are the production solver's numbers. The
    live root uses q(P), so those two are not forced onto it.
    """

    sim = _lunar()
    temperature_C = _HS_T_K - 273.15
    _hold_temperature(sim, temperature_C)
    sim.melt.p_total_mbar = 0.2
    reservoir = sim.melt.oxygen_reservoir
    reservoir.reference_T_K = temperature_C + 273.15
    upper = sim._melt_redox_fO2_upper_bound_log()
    reservoir.melt_intrinsic_fO2_log = upper
    feo_kg = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get("FeO", 0.0)
        or 0.0
    )
    leave_kg = 0.4 * MOLAR_MASS["FeO"] / 1000.0
    assert feo_kg > leave_kg
    sim.atom_ledger.transfer(
        "hsc3f_feo_aside",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"FeO": feo_kg - leave_kg},
                source="leave a thin FeO inventory",
            ),
        ),
        credits=(
            MaterialLot(
                "terminal.slag",
                {"FeO": feo_kg - leave_kg},
                source="leave a thin FeO inventory",
            ),
        ),
        reason="saturate Fe uptake below the evaporative credit",
    )
    sim._project_cleaned_melt_from_atom_ledger()
    _seed(sim, "process.overhead_gas", {"N2": 1.0}, "hsc3 redox carrier")
    # The seed is an input. Leaving it out of the stage-0 total makes
    # the closure the seed's mass, not the hour's.
    sim.inventory.stage0_external_inputs_kg["N2"] = MOLAR_MASS["N2"] / 1000.0
    assert sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(
        OXYGEN_SPECIES, 0.0
    ) <= OXYGEN_RESERVOIR_NOOP_MOL
    assert sim._overhead_holdup_mol().get("N2", 0.0) == pytest.approx(1.0)
    assert sim._evaporative_redox_is_saturated(
        internal_o2_capacity_mol=1.0,
    )
    hour_before = int(sim.melt.campaign_hour)
    before = len(sim.atom_ledger.transitions)

    snapshot = sim._step_one_hour()

    names = _names(sim, before)
    assert names.count(ChemistryIntent.FE_REDOX_RESPECIATION.value) == 1
    assert names.count(ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE.value) == 1
    credit = float(sim._fe_redox_internal_o2_capacity_mol_this_hr)
    consumed = float(sim._fe_redox_internal_o2_consumed_mol_this_hr)
    assert credit - consumed > OXYGEN_RESERVOIR_NOOP_MOL
    m_o2 = _o2_mass()
    exchange_kg = 0.0
    respeciation_kg = 0.0
    for transition in sim.atom_ledger.transitions[before:]:
        moved = sum(
            float(lot.species_kg.get(OXYGEN_SPECIES, 0.0) or 0.0)
            for lot in transition.debits
        )
        if transition.name == ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE.value:
            exchange_kg += moved
        if transition.name == ChemistryIntent.FE_REDOX_RESPECIATION.value:
            respeciation_kg += moved
    exchange_mol = exchange_kg / m_o2
    assert exchange_mol > OXYGEN_RESERVOIR_NOOP_MOL
    # The buffer after the settle plus the one exchange is the
    # post-uptake excess. The respeciation debit is that uptake.
    assert exchange_mol + _buffer_o2(sim) == pytest.approx(credit - consumed)
    assert respeciation_kg / m_o2 == pytest.approx(consumed)
    assert abs(snapshot.mass_balance_error_pct) <= (
        CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT
    )
    cap = sim._headspace_capacity_result
    assert cap is not None and cap.authoritative is True
    solved = math.fsum(float(p) for p in cap.partial_pressures_Pa.values())
    assert snapshot.overhead.pressure_mbar == pytest.approx(solved / 100.0)
    end_o2 = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            OXYGEN_SPECIES, 0.0
        )
        or 0.0
    )
    # The exchange is the sourced excess. The hour's O2 bleed is
    # larger: the evaporation projector also puts O2 in the headspace.
    # Do not force the bleed onto the exchange.
    assert float(cap.bled_species_mol[OXYGEN_SPECIES]) > (
        exchange_mol - end_o2
    )
    rates = {}
    end_mol = sim.atom_ledger.mol_by_account("process.overhead_gas")
    for species, bled in cap.bled_species_mol.items():
        if species not in MOLAR_MASS:
            continue
        n0 = 1.0 if species == "N2" else 0.0
        source_mol = (
            float(bled) + float(end_mol.get(species, 0.0) or 0.0) - n0
        )
        if source_mol > 0.0:
            rates[species] = source_mol * MOLAR_MASS[species] / 1000.0
    assert rates[OXYGEN_SPECIES] > 0.0
    capacity, cold = sim._cold_train_capacity_policy()
    masses = {
        species: MOLAR_MASS[species] / 1000.0
        for species in set(rates) | {"N2", OXYGEN_SPECIES}
    }
    shared = dict(
        pre_holdup_mol={"N2": 1.0},
        capacity=capacity,
        head_bled_species_mol={},
        external_o2_holdup_mol=0.0,
        temperature_K=sim._headspace_temperature_K(),
        volume_m3=sim._headspace_volume_m3(),
        dt_hr=1.0,
        bleed_conductance_kg_s=0.0,
        downstream_pressure_Pa=(
            sim._headspace_downstream_pressure_bar() * PA_PER_BAR
        ),
        k_relief_kg_hr_Pa=float(cold.relief["k_relief_kg_hr_Pa"]),
        p_open_Pa=float(cold.relief["p_open_Pa"]),
        vessel_rating_Pa=float(cold.relief["vessel_rating_Pa"]),
        pipe_diameter_m=float(sim.overhead_model.pipe_diameter_m),
        pipe_length_m=float(sim.overhead_model.pipe_length_m),
        authoritative=True,
    )
    booked = solve_capacity_shadow(
        molar_mass_kg_mol=masses,
        flux_kg_hr_at_partials=lambda _partials: dict(rates),
        **shared,
    )
    pure = solve_capacity_shadow(
        molar_mass_kg_mol={"O2": m_o2, "N2": MOLAR_MASS["N2"] / 1000.0},
        flux_kg_hr_at_partials=lambda _partials: {
            OXYGEN_SPECIES: rates[OXYGEN_SPECIES],
        },
        **shared,
    )
    assert isinstance(booked, CapacityShadowResult) and booked.authoritative
    assert isinstance(pure, CapacityShadowResult) and pure.authoritative
    booked_pa = math.fsum(float(p) for p in booked.partial_pressures_Pa.values())
    pure_pa = math.fsum(float(p) for p in pure.partial_pressures_Pa.values())
    # q(P) on the live hour and the constant booked rate are one
    # balance. They stay in the same pascal range and are not one number.
    assert booked_pa == pytest.approx(solved, rel=1.0e-3)
    assert pure_pa == pytest.approx(solved, rel=1.0e-3)
    assert abs(booked_pa - solved) > 1.0e-6
    assert abs(pure_pa - solved) > 1.0e-6
    _al_notice(cap.species_notices)
    _al_notice(snapshot.headspace_species_notices)
    assert "Al" in cap.evaporation_flux_kg_hr
    assert math.isfinite(float(cap.evaporation_flux_kg_hr["Al"]))
    assert int(sim.melt.campaign_hour) == hour_before + 1


def test_unsaturated_hour_posts_no_exchange():
    """The unsaturated settle does not dump the buffer. The hour commits.

    fO2 starts two log units under the bound and the buffer starts
    above the noop. The stored melt capacity is still zero before the
    hour, so the predicate's second clause is true until the
    pre-evaporation exchange writes a real capacity. That exchange is
    the carried-in path this design keeps. It does not dump the
    buffer. The sweep flags Al and the hour commits. After the hour
    the reservoir has a capacity above the noop and fO2 still under
    the bound, so a trial credit does not saturate. S_redox does not
    take the buffer.
    """

    sim = _lunar()
    temperature_C = _HS_T_K - 273.15
    _hold_temperature(sim, temperature_C)
    sim.melt.p_total_mbar = 0.2
    reservoir = sim.melt.oxygen_reservoir
    reservoir.reference_T_K = temperature_C + 273.15
    upper = sim._melt_redox_fO2_upper_bound_log()
    reservoir.melt_intrinsic_fO2_log = upper - 2.0
    assert reservoir.melt_intrinsic_fO2_log < upper
    # The hour's exchange is what writes this capacity. Before that
    # write the field is the empty initial state, not a melt capacity.
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 <= (
        OXYGEN_RESERVOIR_NOOP_MOL
    )
    _seed(sim, "process.overhead_gas", {"N2": 1.0}, "hsc3 unsaturated carrier")
    _seed(
        sim,
        FO2_BUFFER_ACCOUNT,
        {OXYGEN_SPECIES: 1.0e-6},
        "hsc3 unsaturated buffer",
    )
    buffer_before = _buffer_o2(sim)
    assert buffer_before > OXYGEN_RESERVOIR_NOOP_MOL
    hour_before = int(sim.melt.campaign_hour)
    before = len(sim.atom_ledger.transitions)

    snapshot = sim._step_one_hour()

    names = _names(sim, before)
    # The carried-in exchange posts once. The settle does not post
    # another, and it does not empty the buffer into the headspace.
    assert names.count(ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE.value) == 1
    buffer_after = _buffer_o2(sim)
    assert buffer_after > OXYGEN_RESERVOIR_NOOP_MOL
    assert buffer_after > 0.5 * buffer_before
    cap = sim._headspace_capacity_result
    assert cap is not None and cap.authoritative is True
    _al_notice(cap.species_notices)
    _al_notice(snapshot.headspace_species_notices)
    assert "Al" in cap.evaporation_flux_kg_hr
    assert math.isfinite(float(cap.evaporation_flux_kg_hr["Al"]))
    assert int(sim.melt.campaign_hour) == hour_before + 1
    after = sim.melt.oxygen_reservoir
    assert after.melt_intrinsic_fO2_log < upper
    assert after.melt_redox_capacity_mol_per_ln_fO2 > OXYGEN_RESERVOIR_NOOP_MOL
    assert not sim._evaporative_redox_is_saturated(
        internal_o2_capacity_mol=1.0,
    )


def test_headspace_carry_shortfall_notice_does_not_move_the_clock():
    sim = _lunar()
    _hold_temperature(sim, 25.0)
    sim.melt.p_total_mbar = 10000.0
    _seed(sim, "process.overhead_gas", {"O2": 1.0}, "hsc3 shortfall seed")
    hour_before = int(sim.melt.campaign_hour)

    snapshot = sim.step()

    notice = snapshot.headspace_carry_shortfall
    assert set(notice) == {
        "solved_pressure_bar",
        "commanded_pressure_bar",
        "direction",
        "binding_cause",
        "binding_causes",
        "unrelieved_mass_kg",
        "hours_to_clear",
    }
    assert notice["direction"] == "below_command"
    assert notice["hours_to_clear"] is None
    assert int(sim.melt.campaign_hour) == hour_before + 1
    assert int(snapshot.hour) == hour_before + 1


def test_vessel_rating_still_refuses():
    """A rating below the solved O2 root is still a typed refusal.

    The open rating commits. The 1 Pa rating quotes that same pressure
    and refuses. The cold hour passes the configured rating through to
    the same solver.
    """

    committed = _geometry_o2(_SOURCE_02_KG_H, 1.0e12)
    assert isinstance(committed, CapacityShadowResult)
    solved = float(committed.partial_pressures_Pa["O2"])
    assert solved > 1.0
    refused = _geometry_o2(_SOURCE_02_KG_H, 1.0)
    assert isinstance(refused, CapacityShadowRefusal)
    assert refused.reason.startswith("vessel_total_pressure_exceeds_rating:")
    quoted = float(refused.reason.split(":", 1)[1].split(">", 1)[0])
    assert quoted == pytest.approx(solved, abs=1.0e-6)

    sim = _lunar()
    _hold_temperature(sim, 25.0)
    # 50 Pa is above the 1 Pa rating and below the 0.01 bar downstream,
    # so the pipe stays shut and the relief does not have to be the
    # thing that keeps the pressure. n = P V / (R T).
    # Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
    sim._overhead_headspace_config["downstream_pressure_bar"] = 0.01
    seeded_pa = 50.0
    moles = seeded_pa * sim._headspace_volume_m3() / (
        GAS_CONSTANT * sim._headspace_temperature_K()
    )
    _seed(sim, "process.overhead_gas", {"O2": moles}, "hsc3 rating wiring")
    capacity, cold = sim._cold_train_capacity_policy()
    relief = dict(cold.relief)
    relief["vessel_rating_Pa"] = 1.0
    relief["p_open_Pa"] = 0.1
    cold = replace(cold, relief=relief)
    sim._cold_train_capacity_policy = lambda: (capacity, cold)
    with pytest.raises(CapacityCouplingRefusalError) as exc_info:
        sim._step_one_hour()
    reason = exc_info.value.refusal.reason
    assert reason.startswith("vessel_total_pressure_exceeds_rating:")
    quoted = float(reason.split(":", 1)[1].split(">", 1)[0])
    rating = float(reason.split(">", 1)[1])
    assert quoted == pytest.approx(seeded_pa)
    assert rating == pytest.approx(1.0)
    assert quoted > rating


def _buffer_o2(sim) -> float:
    return float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(OXYGEN_SPECIES, 0.0)
        or 0.0
    )


def _kg_of(sim, moles: float, species: str) -> float:
    from simulator.accounting import resolve_species_formula

    formula = resolve_species_formula(species, sim.species_formula_registry)
    return moles * formula.molar_mass_kg_per_mol()


def test_reducing_fe2o3_pressure_matches_the_inventory():
    """Fe2O3 -> 2 FeO + 1/2 O2, and that O2 is credited to the gas.

    The parents of the elemental vapors are parked, so the internal-O2
    counter stays 0 and the settle's else branch credits overhead.
    SiO2 remains, which keeps a debiting vapor. The published root is
    the ledger inventory. The oracle is that inventory helper, which
    is n R T / V with the full gas constant.
    """

    sim = _lunar()
    temperature_C = 1240.0
    _hold_temperature(sim, temperature_C)
    melt = {
        name: float(mass)
        for name, mass in sim.atom_ledger.kg_by_account(
            "process.cleaned_melt"
        ).items()
    }
    parents = ("Na2O", "K2O", "MgO", "CaO", "MnO", "Cr2O3", "Al2O3", "TiO2")
    keep_feo = _kg_of(sim, 0.04, "FeO")
    moves = {
        name: melt[name]
        for name in parents
        if melt.get(name, 0.0) > 0.0
    }
    feo_out = melt.get("FeO", 0.0) - keep_feo
    if feo_out > 1.0e-9:
        moves["FeO"] = feo_out
    sim.atom_ledger.transfer(
        "reducing-park",
        [MaterialLot("process.cleaned_melt", moves)],
        [MaterialLot("terminal.slag", dict(moves))],
        reason="park elemental parents",
    )
    sim.atom_ledger.load_external_mol(
        FO2_BUFFER_ACCOUNT,
        {OXYGEN_SPECIES: 0.01},
        source="reducing fe2o3 oxygen",
        material_origin="feedstock",
    )
    sim.atom_ledger.transfer(
        "reducing-fe2o3",
        [
            MaterialLot("process.cleaned_melt", {"FeO": keep_feo}),
            MaterialLot(
                FO2_BUFFER_ACCOUNT,
                {OXYGEN_SPECIES: _kg_of(sim, 0.01, "O2")},
            ),
        ],
        [MaterialLot("process.cleaned_melt", {"Fe2O3": _kg_of(sim, 0.02, "Fe2O3")})],
        reason="2 FeO + 1/2 O2 -> Fe2O3",
    )
    sim._project_cleaned_melt_from_atom_ledger()
    sim.melt.p_total_mbar = 0.2
    reservoir = sim.melt.oxygen_reservoir
    reservoir.reference_T_K = temperature_C + 273.15
    reservoir.melt_intrinsic_fO2_log = (
        sim._melt_redox_fO2_upper_bound_log() - 10.0
    )
    reservoir.melt_redox_capacity_mol_per_ln_fO2 = 1.0
    reservoir.exchange_o2_mol = 0.0
    fe_before = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("Fe2O3", 0.0)
        or 0.0
    )

    sim._step_one_hour()

    published_pa = float(sim.overhead.pressure_mbar) * 100.0
    inventory_pa = float(sim._headspace_inventory_pressure_Pa())
    assert abs(published_pa - inventory_pa) <= 1.0e-6
    fe_after = float(
        sim.atom_ledger.mol_by_account("process.cleaned_melt").get("Fe2O3", 0.0)
        or 0.0
    )
    assert fe_after < fe_before
    credited = [
        item
        for item in list(
            getattr(sim, "_fe_redox_respeciation_diagnostics_this_hr", []) or []
        )
        if item.get("direction") == "reducing"
        and item.get("o2_account") == "process.overhead_gas"
    ]
    assert credited
    assert float(credited[-1]["o2_credit_mol"]) > OXYGEN_RESERVOIR_NOOP_MOL
    assert _buffer_o2(sim) <= OXYGEN_RESERVOIR_NOOP_MOL


def test_published_pressure_off_the_inventory_refuses():
    sim = _lunar()
    inventory_pa = sim._headspace_inventory_pressure_Pa()
    sim.overhead.pressure_mbar = inventory_pa / 100.0
    sim._refuse_if_headspace_pressure_is_not_inventory()
    sim.overhead.pressure_mbar = inventory_pa / 100.0 + 1.0
    with pytest.raises(CapacityCouplingRefusalError) as caught:
        sim._refuse_if_headspace_pressure_is_not_inventory()
    assert (
        caught.value.refusal.reason
        == "headspace_published_pressure_not_inventory"
    )


def test_carry_shortfall_and_hour_gates_read_default_abs_tol_pa(monkeypatch):
    # A 1 Pa gap is outside 1e-6 Pa and inside a patched 10 Pa gate.
    # The early return is the constant. The hour's capture move and
    # the reducing re-solve read that same name.
    sim = _lunar()
    sim.melt.p_total_mbar = 0.01
    result = SimpleNamespace(partial_pressures_Pa={"O2": 0.0})
    try:
        sim._publish_headspace_carry_shortfall(
            result,
            pre_holdup_mol={},
            molar_mass_kg_mol={},
        )
    except AttributeError:
        continued = True
    else:
        continued = sim._headspace_carry_shortfall is not None
    assert continued
    monkeypatch.setattr(
        "simulator.capacity_coupling.DEFAULT_ABS_TOL_PA",
        10.0,
    )
    sim._publish_headspace_carry_shortfall(
        result,
        pre_holdup_mol={},
        molar_mass_kg_mol={},
    )
    assert sim._headspace_carry_shortfall is None
    hour = inspect.getsource(
        PyrolysisSimulator._solve_and_commit_headspace_hour
    )
    assert "move_pa > DEFAULT_ABS_TOL_PA" in hour
    assert "gap_pa > DEFAULT_ABS_TOL_PA" in hour
    assert "move_pa > 1.0e-6" not in hour
    assert "gap_pa > 1.0e-6" not in hour


def test_capture_pressure_stays_unresolved_when_the_move_does_not_fall():
    sim = _lunar()
    _hold_temperature(sim, 25.0)
    sim.melt.p_total_mbar = 10000.0
    _seed(sim, "process.overhead_gas", {"O2": 1.0}, "capture unresolved seed")
    with patch(
        "simulator.capacity_coupling.headspace_capture_move_Pa",
        return_value=1.0e-3,
    ):
        with pytest.raises(CapacityCouplingRefusalError) as caught:
            sim.step()
    assert (
        caught.value.refusal.reason
        == "headspace_capture_pressure_unresolved"
    )


def test_capture_move_inside_a_widened_gate_is_not_unresolved(monkeypatch):
    sim = _lunar()
    _hold_temperature(sim, 25.0)
    sim.melt.p_total_mbar = 10000.0
    _seed(sim, "process.overhead_gas", {"O2": 1.0}, "capture widened gate")
    monkeypatch.setattr(
        "simulator.capacity_coupling.DEFAULT_ABS_TOL_PA",
        1.0,
    )
    monkeypatch.setattr(
        "simulator.capacity_coupling.headspace_capture_move_Pa",
        lambda *_args, **_kwargs: 1.0e-3,
    )
    sim.step()
    assert sim._headspace_capacity_result is not None
    assert sim._headspace_capacity_result.authoritative is True


def test_stoich_o2_sum_is_sorted_and_keeps_the_small_term():
    # Large-first += loses both 1e-16 terms. Sorted fsum keeps them.
    # Ten 1e-32 terms cross the 1e-15 noop under fsum. += of 1e-15
    # never does, so that gate would stay closed.
    stoich = {
        "Na": {"O2_per_product_kg": 1.0},
        "K": {"O2_per_product_kg": 1.0},
        "Fe": {"O2_per_product_kg": 1.0},
    }
    forward = {"Na": 1.0, "K": 1.0e-16, "Fe": 1.0e-16}
    backward = {"Fe": 1.0e-16, "K": 1.0e-16, "Na": 1.0}
    total = _sorted_positive_stoich_o2_mol(forward, stoich, 1.0)
    assert total == _sorted_positive_stoich_o2_mol(backward, stoich, 1.0)
    assert total == math.fsum((1.0e-16, 1.0e-16, 1.0))
    binary = 1.0
    binary += 1.0e-16
    binary += 1.0e-16
    assert binary == 1.0
    assert total != binary
    # One 1e-32 term is under half an ulp of 1e-15, so fsum stays
    # on the noop. Ten of them cross it. += of 1e-15 never does.
    small = {f"M{index:02d}": 1.0e-32 for index in range(10)}
    small["Na"] = 1.0e-15
    small_stoich = {
        name: {"O2_per_product_kg": 1.0} for name in small
    }
    near = _sorted_positive_stoich_o2_mol(small, small_stoich, 1.0)
    binary_near = 1.0e-15
    for _term in range(10):
        binary_near += 1.0e-32
    assert binary_near == 1.0e-15
    assert near > OXYGEN_RESERVOIR_NOOP_MOL
    assert near == math.fsum((1.0e-15, *([1.0e-32] * 10)))
    sim = _lunar()
    assert sim._headspace_reducing_overhead_o2_mol(
        opening_gross_kg_hr={"Na": 1.0},
        stoich_by_species={
            "Na": {
                "O2_per_product_kg": 1.0,
                "oxygen_destination": "reservoir.fo2_buffer",
            },
        },
    ) == 0.0
    hour = inspect.getsource(
        PyrolysisSimulator._solve_and_commit_headspace_hour
    )
    assert "_sorted_positive_stoich_o2_mol(" in hour


def test_condensation_overhead_o2_is_the_product_helper():
    from engines.builtin.condensation_route import (
        baffle_product_is_subfloor,
        committed_wall_deposit_fraction,
    )
    from simulator.accounting.lots import EMPTY_KG_TOLERANCE

    sim = _lunar()
    captured_kg = 1.0e-6
    metals = sim.vapor_pressures.get("metals", {}) or {}
    oxides = sim.vapor_pressures.get("oxide_vapors", {}) or {}
    sp_data = dict(metals.get("CrO2") or oxides.get("CrO2") or {})
    assert sp_data["condensation_product_accounts"]["O2"] == (
        "process.overhead_gas"
    )
    product_mol, product_kg = sim._condensed_products_for_vapor(
        "CrO2",
        captured_kg,
        sp_data,
    )
    assert product_kg
    assert not any(
        baffle_product_is_subfloor(kg, EMPTY_KG_TOLERANCE)
        for kg in product_kg.values()
    )
    assert sim._condensation_overhead_o2_mol("CrO2", captured_kg) == (
        float(product_mol["O2"])
    )
    # A wall fraction of 1 still disproportionates. The solid moves to
    # the wall and the oxygen credit stays the full captured mass.
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=1.0,
    ) == float(product_mol["O2"])
    half_mol, half_kg = sim._condensed_products_for_vapor(
        "CrO2",
        captured_kg * 0.5,
        sp_data,
    )
    assert half_kg
    assert not any(
        baffle_product_is_subfloor(kg, EMPTY_KG_TOLERANCE)
        for kg in half_kg.values()
    )
    # Both shares clear the floor, so the credit is the whole parcel.
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=0.5,
    ) == float(product_mol["O2"])
    # Shrink the baffle until a product sits in (0, floor]. The route
    # retains the parcel and posts no overhead oxygen.
    fraction = 0.0
    withheld = False
    for _ in range(40):
        baffle_kg = captured_kg * (1.0 - fraction)
        _mol, kg = sim._condensed_products_for_vapor(
            "CrO2",
            baffle_kg,
            sp_data,
        )
        if any(
            baffle_product_is_subfloor(mass, EMPTY_KG_TOLERANCE)
            for mass in kg.values()
        ):
            withheld = True
            break
        fraction = 1.0 - (1.0 - fraction) * 0.1
    assert withheld
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=fraction,
    ) == 0.0
    # CrO3 has no static overhead account. Its oxygen is the wall
    # booking, and a present booking replaces the static map rather
    # than adding to it. The yield is the posted mols per booked mol
    # of vapor, applied to the captured mass.
    from simulator.accounting.formulas import resolve_species_formula

    assert sim._condensation_overhead_o2_mol("CrO3", captured_kg) == 0.0
    cro3_mol = captured_kg / resolve_species_formula(
        "CrO3",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    cro3_booking = {
        "baffle_oxygen_credit_mol": 1.0e-9,
        "baffle_oxygen_debit_mol": 2.0e-9,
        "wall_oxygen_credit_mol": 4.0e-9,
        "wall_oxygen_debit_mol": 0.0,
        "baffle_products_mol": {"Cr2O3": 1.0e-4},
        "wall_products_mol_by_account": {
            "process.wall_deposit": {"Cr2O3": 1.0e-4},
        },
        "reaction_legs": [
            {"surface": "baffle", "vapour_mol": cro3_mol * 0.5},
            {"surface": "wall", "vapour_mol": cro3_mol * 0.5},
        ],
    }
    assert sim._condensation_overhead_o2_mol(
        "CrO3",
        captured_kg,
        wall_fraction=0.5,
        booking=cro3_booking,
    ) == 3.0e-9
    half_vapour = {
        "baffle_oxygen_credit_mol": 1.0e-9,
        "baffle_products_mol": {"Cr2O3": 1.0e-4},
        "wall_products_mol_by_account": {},
        "reaction_legs": [
            {"surface": "baffle", "vapour_mol": cro3_mol / 2.0},
        ],
    }
    assert sim._condensation_overhead_o2_mol(
        "CrO3",
        captured_kg,
        booking=half_vapour,
    ) == 2.0e-9
    assert sim._condensation_overhead_o2_mol(
        "CrO3",
        1.0e-13,
        booking=cro3_booking,
    ) == 0.0
    cro2_mol = captured_kg / resolve_species_formula(
        "CrO2",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    wall_only_drop = 5.0e-12 / captured_kg
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=wall_only_drop,
        booking={
            "baffle_oxygen_credit_mol": 1.0e-9,
            "wall_oxygen_credit_mol": 4.0e-9,
            "baffle_products_mol": {"Cr2O3": 1.0e-4},
            "wall_products_mol_by_account": {
                "process.wall_deposit": {"Cr2O3": 1.0e-14},
            },
            "reaction_legs": [
                {
                    "surface": "baffle",
                    "vapour_mol": cro2_mol * (1.0 - wall_only_drop),
                },
                {"surface": "wall", "vapour_mol": cro2_mol * wall_only_drop},
            ],
        },
    ) == 1.0e-9
    # One wall account under the floor retains the whole parcel. The
    # provider posts no oxygen. Summing that account into its neighbour
    # would credit oxygen the ledger does not carry.
    cr2o3_mass = resolve_species_formula(
        "Cr2O3",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    cro3_mass = resolve_species_formula(
        "CrO3",
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()
    tiny_product_mol = 1.0e-13 / cr2o3_mass
    tiny_vapour_mol = tiny_product_mol * 2.0
    assert 0.0 < tiny_vapour_mol * cro3_mass <= EMPTY_KG_TOLERANCE
    large_vapour_mol = cro3_mol * 0.5 - tiny_vapour_mol
    assert sim._condensation_overhead_o2_mol(
        "CrO3",
        captured_kg,
        wall_fraction=0.5,
        booking={
            "baffle_oxygen_credit_mol": 1.0e-9,
            "baffle_oxygen_debit_mol": 2.0e-9,
            "wall_oxygen_credit_mol": 4.0e-9,
            "baffle_products_mol": {"Cr2O3": 1.0e-4},
            "wall_products_mol_by_account": {
                "process.wall_deposit_segment_stage_0_to_stage_1": {
                    "Cr2O3": 1.0e-4,
                },
                "process.wall_deposit_segment_stage_1_to_stage_2": {
                    "Cr2O3": tiny_product_mol,
                },
            },
            "reaction_legs": [
                {"surface": "baffle", "vapour_mol": cro3_mol * 0.5},
                {
                    "surface": "wall",
                    "account": (
                        "process.wall_deposit_segment_stage_0_to_stage_1"
                    ),
                    "vapour_mol": large_vapour_mol,
                },
                {
                    "surface": "wall",
                    "account": (
                        "process.wall_deposit_segment_stage_1_to_stage_2"
                    ),
                    "vapour_mol": tiny_vapour_mol,
                },
            ],
        },
    ) == 0.0
    replaced = sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        booking={
            "baffle_oxygen_credit_mol": 3.0e-9,
            "baffle_products_mol": {"Cr2O3": 1.0e-4},
            "wall_products_mol_by_account": {},
            "reaction_legs": [
                {"surface": "baffle", "vapour_mol": cro2_mol},
            ],
        },
    )
    assert replaced == 3.0e-9
    assert replaced != float(product_mol["O2"])
    # One scale. The commit posts this booking, and S reads its oxygen.
    # A fresh route booking is a different extent and must not survive.
    from engines.builtin.condensation_route import (
        booked_overhead_oxygen_mol,
        booking_scaled_to_vapour_split,
        booking_wall_account_fractions,
    )
    from simulator.condensation import CondensationRouteResult
    from simulator.state import EvaporationFlux

    halved = booking_scaled_to_vapour_split(
        {
            "baffle_oxygen_credit_mol": 2.0,
            "baffle_oxygen_debit_mol": 0.5,
            "baffle_products_mol": {"Cr2O3": 4.0},
            "wall_oxygen_credit_mol": 1.0,
            "wall_oxygen_debit_mol": 0.0,
            "wall_products_mol_by_account": {
                "process.wall_deposit": {"Cr2O3": 2.0},
            },
            "reaction_legs": [
                {
                    "surface": "baffle",
                    "vapour_mol": 4.0,
                    "products_mol": {"Cr2O3": 4.0},
                    "oxygen_credit_mol": 2.0,
                    "oxygen_debit_mol": 0.5,
                },
                {
                    "surface": "wall",
                    "vapour_mol": 2.0,
                    "account": "process.wall_deposit",
                    "products_mol": {"Cr2O3": 2.0},
                    "oxygen_credit_mol": 1.0,
                    "oxygen_debit_mol": 0.0,
                },
            ],
        },
        baffle_mol=2.0,
        wall_mol=1.0,
    )
    debit_mol, credit_mol = booked_overhead_oxygen_mol(
        halved,
        include_wall=True,
    )
    assert debit_mol == 0.25
    assert credit_mol == 1.5
    assert halved["baffle_products_mol"]["Cr2O3"] == 2.0
    assert halved["reaction_legs"][0]["vapour_mol"] == 2.0
    assert booking_wall_account_fractions(halved) == {
        "process.wall_deposit": 1.0,
    }
    flux = EvaporationFlux(
        species_kg_hr={"CrO3": captured_kg, "CrO2": captured_kg},
    )
    fresh = CondensationRouteResult(
        remaining_by_species={"CrO3": 0.0, "CrO2": 0.0},
        wall_deposit_fraction_by_species={"CrO3": 0.0, "CrO2": 1.0},
        wall_deposit_account_fractions_by_species={
            "CrO2": {"process.wall_deposit": 1.0},
        },
        wall_reaction_booking_by_species={
            "CrO3": {
                "baffle_oxygen_credit_mol": 1.0e-3,
                "baffle_products_mol": {"Cr2O3": 1.0},
                "reaction_legs": [
                    {"surface": "baffle", "vapour_mol": 1.0},
                ],
            },
            "CrO2": {
                "baffle_oxygen_credit_mol": 9.0,
                "baffle_products_mol": {"Cr2O3": 9.0},
                "reaction_legs": [
                    {"surface": "baffle", "vapour_mol": 9.0},
                ],
            },
        },
    )
    committed_route = sim._commit_headspace_vapour_booking(
        fresh,
        flux,
        phase_scalar=1.0,
        bookings={"CrO3": cro3_booking},
        wall_fractions={"CrO3": 0.5},
        wall_accounts={"CrO3": {"process.wall_deposit": 1.0}},
    )
    assert "CrO2" not in committed_route.wall_reaction_booking_by_species
    assert committed_route.wall_deposit_fraction_by_species["CrO2"] == 0.0
    posted_booking = committed_route.wall_reaction_booking_by_species["CrO3"]
    assert posted_booking["baffle_oxygen_credit_mol"] == 1.0e-9
    posted_debit, posted_credit = booked_overhead_oxygen_mol(
        posted_booking,
        include_wall=True,
    )
    assert posted_credit - posted_debit == sim._condensation_overhead_o2_mol(
        "CrO3",
        captured_kg,
        wall_fraction=0.5,
        booking=cro3_booking,
    )
    # A retained parcel from the previous hour drains when its products
    # clear the floor. The oxygen is the product helper on that mass,
    # and on the sum when this parcel also commits.
    from simulator.account_ids import CONDENSATION_RETAINED_HOLDUP_ACCOUNT

    _seed(
        sim,
        CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
        {"CrO2": 1.0e-8},
        "prior retained CrO2",
    )
    prior_kg = float(
        sim.atom_ledger.kg_by_account(
            CONDENSATION_RETAINED_HOLDUP_ACCOUNT
        ).get("CrO2", 0.0)
        or 0.0
    )
    prior_mol, prior_product_kg = sim._condensed_products_for_vapor(
        "CrO2",
        prior_kg,
        sp_data,
    )
    assert prior_kg > 0.0
    assert not any(
        baffle_product_is_subfloor(mass, EMPTY_KG_TOLERANCE)
        for mass in prior_product_kg.values()
    )
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=fraction,
    ) == float(prior_mol["O2"])
    summed_mol, _summed_kg = sim._condensed_products_for_vapor(
        "CrO2",
        captured_kg + prior_kg,
        sp_data,
    )
    assert sim._condensation_overhead_o2_mol(
        "CrO2",
        captured_kg,
        wall_fraction=0.0,
    ) == float(summed_mol["O2"])
    assert sim._condensation_overhead_o2_mol("CrO2", 0.0) == 0.0
    assert sim._condensation_overhead_o2_mol("SiO", captured_kg) == 0.0
    assert committed_wall_deposit_fraction(0.35, {"wall": 1.0}) == 0.35
    assert committed_wall_deposit_fraction(
        EMPTY_KG_TOLERANCE,
        {"wall": 1.0},
    ) == 0.0
    assert committed_wall_deposit_fraction(0.35, {}) == 0.0


def test_subfloor_evaporation_parcel_does_not_post():
    from engines.builtin.evaporation_transition import (
        coupled_evaporation_legs_kg,
        evaporation_parent_is_below_floor,
        subfloor_legs_kg,
    )

    legs = coupled_evaporation_legs_kg(
        oxide_removed_kg=2.0e-12,
        remaining_vapor_kg=2.0e-12,
        oxygen_coproduct_kg=-5.0e-13,
        condensed_product_kg={},
    )
    assert subfloor_legs_kg(legs) == {"oxygen_coproduct": 5.0e-13}
    assert evaporation_parent_is_below_floor(2.0e-12, 1.0) is False
    assert evaporation_parent_is_below_floor(1.0e-12, 1.0) is True
    assert evaporation_parent_is_below_floor(2.0e-12, 1.0e-12) is True
    sim = _lunar()
    row = {
        "parent_oxide": "Al2O3",
        "oxide_per_product_kg": 1.0,
        "O2_per_product_kg": 0.1,
    }
    # 2e-12 kg/h * 0.1 kg O2 / kg vapor = 2e-13 kg O2, below 1e-12 kg.
    # Parent and vapor are 2e-12 kg, so only the oxygen leg is subfloor.
    assert sim._evaporation_transition_posts_parcel(
        2.0e-12,
        row,
        1.0,
    ) is False
    assert sim._evaporation_transition_posts_parcel(
        1.0e-9,
        row,
        1.0,
    ) is True
    assert sim._evaporation_transition_posts_parcel(1.0e-9, {}, 0.0) is True
