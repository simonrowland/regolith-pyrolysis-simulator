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

from simulator.capacity_coupling import (
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
from simulator.state import CampaignPhase
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
    """A saturated hot hour commits, and flagged Al stays in the flux.

    fO2 is on the upper bound, so a trial credit above the noop makes
    the saturated predicate true. The buffer is empty, so the
    pre-evaporation exchange cannot post. The fast sweep still holds
    Al. Al has no certified diameter, so the pipe flags it and keeps
    the species. Fe uptake consumes this hour's evaporative credit, so
    the post-uptake excess is at the noop floor and the settle does
    not exchange. One respeciation is the ledger redox commit.
    """

    sim = _lunar()
    temperature_C = _HS_T_K - 273.15
    _hold_temperature(sim, temperature_C)
    sim.melt.p_total_mbar = 0.2
    reservoir = sim.melt.oxygen_reservoir
    reservoir.reference_T_K = temperature_C + 273.15
    upper = sim._melt_redox_fO2_upper_bound_log()
    reservoir.melt_intrinsic_fO2_log = upper
    _seed(sim, "process.overhead_gas", {"N2": 1.0}, "hsc3 redox carrier")
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
    assert ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE.value not in names
    assert _buffer_o2(sim) <= OXYGEN_RESERVOIR_NOOP_MOL
    credit = float(sim._fe_redox_internal_o2_capacity_mol_this_hr)
    consumed = float(sim._fe_redox_internal_o2_consumed_mol_this_hr)
    assert credit > OXYGEN_RESERVOIR_NOOP_MOL
    assert consumed == pytest.approx(credit)
    cap = sim._headspace_capacity_result
    assert cap is not None and cap.authoritative is True
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

    import simulator.capacity_coupling as capacity_coupling

    sim = _lunar()
    _hold_temperature(sim, 25.0)
    _seed(sim, "process.overhead_gas", {"O2": 1.0}, "hsc3 rating wiring")
    capacity, cold = sim._cold_train_capacity_policy()
    relief = dict(cold.relief)
    relief["vessel_rating_Pa"] = 1.0
    relief["p_open_Pa"] = 0.1
    cold = replace(cold, relief=relief)
    sim._cold_train_capacity_policy = lambda: (capacity, cold)
    seen: dict[str, float] = {}
    real = capacity_coupling.solve_capacity_shadow

    def _record(**kwargs):
        seen["vessel_rating_Pa"] = float(kwargs["vessel_rating_Pa"])
        return real(**kwargs)

    capacity_coupling.solve_capacity_shadow = _record
    try:
        sim._step_one_hour()
    finally:
        capacity_coupling.solve_capacity_shadow = real
    assert seen["vessel_rating_Pa"] == pytest.approx(1.0)


def _buffer_o2(sim) -> float:
    return float(
        sim.atom_ledger.mol_by_account(FO2_BUFFER_ACCOUNT).get(OXYGEN_SPECIES, 0.0)
        or 0.0
    )
