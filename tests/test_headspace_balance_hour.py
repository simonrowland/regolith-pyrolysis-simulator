"""Chunk 3: the headspace balance is the committed hour.

The seven tests named in the approved design. Goldens are not
regenerated here. A cold O2 inventory commits. A hot lunar mixture
puts Al into the fast sweep. Al has no certified collision diameter.
The pipe extrapolates one from the P0a band, flags that species, and
the hour still commits. A missing molar mass still refuses. This
chunk does not add a collision diameter and does not drop the vapor.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

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
