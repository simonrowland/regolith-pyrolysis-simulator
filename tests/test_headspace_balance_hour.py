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

import simulator.capacity_coupling as capacity_coupling
from simulator.accounting.lots import MaterialLot
from simulator.accounting.run_artifact import (
    CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT,
)
from simulator.capacity_coupling import (
    DEFAULT_ABS_TOL_PA,
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


_IFT_SPECIES = (
    "O2", "Na", "K", "Fe", "Mg", "Si", "Ti", "Al", "Ca", "Cr",
    "Mn", "Ni", "Co", "N2",
)
_IFT_T_K = 1453.15
_IFT_V_M3 = 0.08538380762699734
_IFT_D_M = 0.12
_IFT_L_M = 1.0
_IFT_DOWNSTREAM_PA = 20.0
_IFT_OPENING_PA = 20.1386
_IFT_COMPOSITION_TIME_S = 1347.398588
_IFT_VAPOUR_SLOPE_S = 677.44


def _reduced_manifold_case(*, stiff: bool, track_extent: bool = False):
    species = _IFT_SPECIES if stiff else ("O2",)
    masses = {name: MOLAR_MASS[name] / 1000.0 for name in species}
    factor = GAS_CONSTANT * _IFT_T_K / _IFT_V_M3
    opening_total = _IFT_OPENING_PA / factor
    fractions = {name: 1.0 / len(species) for name in species}
    target_source = opening_total / _IFT_COMPOSITION_TIME_S

    def pipe_at(pressure: float) -> dict[str, float]:
        result = capacity_coupling.pipe_species_outflow_mol_s(
            pressure,
            _IFT_DOWNSTREAM_PA,
            _IFT_T_K,
            fractions,
            _IFT_D_M,
            _IFT_L_M,
            molar_mass_kg_mol=masses,
        )
        return {name: float(result.species_mol_s[name]) for name in species}

    if stiff:
        low, high = _IFT_DOWNSTREAM_PA, _IFT_OPENING_PA
        for _step in range(80):
            middle = 0.5 * (low + high)
            if math.fsum(pipe_at(middle).values()) < target_source:
                low = middle
            else:
                high = middle
        root = 0.5 * (low + high)
        base_rates = pipe_at(root)
    else:
        root = _IFT_DOWNSTREAM_PA + 6.36e-4
        base_rates = pipe_at(root)

    widths = {
        name: base_rates[name] * factor / _IFT_VAPOUR_SLOPE_S
        for name in species
    }
    reference_partials = {name: fractions[name] * root for name in species}
    holder = {"gross_kg_hr": {}} if track_extent else None

    def flux(partials):
        result = {}
        for name in species:
            rate = base_rates[name]
            delta = float(partials.get(name, 0.0)) - reference_partials[name]
            if stiff and abs(delta) < 1.0e-3:
                rate = max(0.0, rate * (1.0 - delta / widths[name]))
            result[name] = rate * masses[name] * 3600.0
        if holder is not None:
            holder["gross_kg_hr"] = dict(result)
        return result

    opening_moles = {
        name: fractions[name] * opening_total for name in species
    }
    built = capacity_coupling._build_law(
        pre_holdup_mol=opening_moles,
        molar_mass_kg_mol=masses,
        flux_kg_hr_at_partials=flux,
        capacity=NoColdTrain("runtime_enforcement_disabled"),
        temperature_K=_IFT_T_K,
        volume_m3=_IFT_V_M3,
        dt_hr=1.0,
        downstream_pressure_Pa=_IFT_DOWNSTREAM_PA,
        pipe_diameter_m=_IFT_D_M,
        pipe_length_m=_IFT_L_M,
        outflow_cap_kg_s=None,
        redox_source_mol_s=None,
        extent_holder=holder,
    )
    assert not isinstance(built, CapacityShadowRefusal)
    law, _opening = built
    law._record_opening_scales(opening_moles, 3600.0)
    if stiff:
        fractions[species[0]] += 1.0e-7
        fractions[species[1]] -= 1.0e-7
    start_total = root / factor
    start_moles = {name: fractions[name] * start_total for name in species}
    return law, holder, fractions, start_moles, root


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


def test_reduced_radau_matches_old_rk45_on_a_nonstiff_hour(monkeypatch):
    """The IFT/Radau path matches the former warm-root/RK45 result."""

    radau_law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=False,
    )
    radau = capacity_coupling._reduced_manifold(
        radau_law, fractions, start, root, 3600.0, track_extent=False,
    )
    assert radau is not None

    rk45_law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=False,
    )
    import scipy.integrate

    solve_ivp = scipy.integrate.solve_ivp

    def rk45(fun, t_span, y0, **kwargs):
        kwargs.pop("jac", None)
        return solve_ivp(fun, t_span, y0, method="RK45", **kwargs)

    def warm_fixed_point(law_arg, mix, hint_Pa):
        return capacity_coupling._open_or_shut_fixed_point(
            law_arg, mix, near_Pa=hint_Pa,
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(scipy.integrate, "solve_ivp", rk45)
        scoped.setattr(
            capacity_coupling,
            "_reduced_open_fixed_point",
            warm_fixed_point,
        )
        legacy = capacity_coupling._reduced_manifold(
            rk45_law, fractions, start, root, 3600.0, track_extent=False,
        )
    assert legacy is not None
    mole_atol = capacity_coupling._SLOW_ATOL_PA / (
        GAS_CONSTANT * _IFT_T_K / _IFT_V_M3
    )
    for name in radau[0]:
        assert abs(radau[0][name] - legacy[0][name]) <= mole_atol


def test_stiff_reduced_synthetic_hour_finishes_below_flux_cap(monkeypatch):
    """The stiff hour fits the validated IFT budget; warm Newton exceeds it."""

    law, holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
        track_extent=True,
    )
    result = capacity_coupling._reduced_manifold(
        law, fractions, start, root, 3600.0, track_extent=True,
    )

    assert result is not None
    assert law.opening_composition_tau_s == pytest.approx(
        _IFT_COMPOSITION_TIME_S,
        rel=1.0e-12,
    )
    assert len(result[0]) == 14
    assert holder is not None
    assert law.flux_evaluations == 3281
    assert law._rhs_flux_counts
    assert max(law._rhs_flux_counts) <= 10

    warm_law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
        track_extent=True,
    )

    def warm_fixed_point(law_arg, mix, hint_Pa):
        return capacity_coupling._open_or_shut_fixed_point(
            law_arg, mix, near_Pa=hint_Pa,
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            capacity_coupling,
            "_reduced_open_fixed_point",
            warm_fixed_point,
        )
        warm_result = capacity_coupling._reduced_manifold(
            warm_law, fractions, start, root, 3600.0, track_extent=True,
        )
    assert warm_result is not None
    warm_samples = sorted(warm_law._rhs_flux_counts)
    assert warm_samples
    assert warm_samples[len(warm_samples) // 2] > 4


def test_reduced_extent_quadrature_preserves_synthetic_hour_closure():
    law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
        track_extent=True,
    )
    result = capacity_coupling._reduced_manifold(
        law, fractions, start, root, 3600.0, track_extent=True,
    )

    assert result is not None
    n_end, added, bled, _vapour, _redox_added, _time, _dy = result
    pa_per_mol = GAS_CONSTANT * _IFT_T_K / _IFT_V_M3
    closure_error_pa = max(
        abs((n_end[name] - start[name] - added[name] + bled[name]) * pa_per_mol)
        for name in start
    )
    assert closure_error_pa <= DEFAULT_ABS_TOL_PA


def test_passive_extent_kink_does_not_cause_reduced_newton_failures():
    law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
        track_extent=True,
    )
    result = capacity_coupling._reduced_manifold(
        law, fractions, start, root, 3600.0, track_extent=True,
    )

    assert result is not None
    assert law.reduced_newton_nonconvergence_failures == 0


def test_reduced_endpoint_pressure_miss_declines_stall_handoff(monkeypatch):
    """A forced P* mismatch leaves the live full-system Radau path in control."""

    law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
        track_extent=True,
    )
    state = list(start) + [0.0] * (4 * len(start))
    end_check_started = {"value": False}
    original_fractions = capacity_coupling._fractions_of
    original_fixed_point = capacity_coupling._open_or_shut_fixed_point

    def mark_end_check(law_arg, moles):
        result = original_fractions(law_arg, moles)
        end_check_started["value"] = True
        return result

    def force_pressure_miss(law_arg, mix, near_Pa=None):
        result = original_fixed_point(law_arg, mix, near_Pa=near_Pa)
        kind, pressure = result
        if end_check_started["value"] and kind == "open" and pressure is not None:
            return kind, pressure + 2.0 * DEFAULT_ABS_TOL_PA
        return result

    monkeypatch.setattr(capacity_coupling, "_fractions_of", mark_end_check)
    monkeypatch.setattr(
        capacity_coupling,
        "_open_or_shut_fixed_point",
        force_pressure_miss,
    )
    step = capacity_coupling._hold_fixed_point(
        law,
        start,
        3600.0,
        state,
        0.0,
        root,
        "open",
        root,
        1.0e-3,
        fractions,
        track_extent=True,
    )

    # _slow_converge's Radau callback raises _QuasiSteadyStall only for a
    # returned step; None lets the full-system solve continue and integrate B.
    assert end_check_started["value"] is True
    assert step is None
    assert law._quasi_steady_declined is True


def test_reduced_ift_falls_back_when_corrected_root_residual_is_too_large(
    monkeypatch,
):
    law = SimpleNamespace(downstream_pa=20.0)
    root = 20.00108
    fallbacks = []
    accepted = []

    def residual(_law, _fractions, pressure):
        offset = float(pressure) - root
        return -offset - 900.0 * offset * offset

    def fallback(law_arg, fractions, near_Pa=None):
        fallbacks.append(near_Pa)
        return "open", root

    def accept(_value_at, _piece_at, _anchor, _downstream, candidate):
        accepted.append(candidate)
        return "accept", candidate

    monkeypatch.setattr(capacity_coupling, "_mixture_g", residual)
    monkeypatch.setattr(
        capacity_coupling,
        "_mixture_piece",
        lambda *_args: ("open",),
    )
    monkeypatch.setattr(
        capacity_coupling,
        "_continuous_warm_root",
        accept,
    )
    monkeypatch.setattr(
        capacity_coupling,
        "_open_or_shut_fixed_point",
        fallback,
    )

    result = capacity_coupling._reduced_open_fixed_point(
        law, {"O2": 1.0}, hint_Pa=20.001,
    )

    assert result == ("open", root)
    assert fallbacks == [20.001]
    assert accepted == []


def test_reduced_rhs_reuses_one_root_anchor_across_trial_states(monkeypatch):
    """Repeated RHS calls at one state ignore an intervening trial state."""

    law, _holder, fractions, start, root = _reduced_manifold_case(
        stiff=True,
    )
    root_hints = []
    rates = {}
    original_root = capacity_coupling._reduced_open_fixed_point

    def record_root(law_arg, mix, hint_Pa):
        root_hints.append(hint_Pa)
        return original_root(law_arg, mix, hint_Pa)

    def inspect_rhs(fun, _t_span, y0, **_kwargs):
        rates["first"] = fun(0.0, y0)
        trial = list(y0)
        trial[0] *= 1.0 + 1.0e-4
        fun(0.5, trial)
        rates["repeat"] = fun(0.0, y0)
        return SimpleNamespace(success=False)

    import scipy.integrate

    monkeypatch.setattr(scipy.integrate, "solve_ivp", inspect_rhs)
    monkeypatch.setattr(
        capacity_coupling,
        "_reduced_open_fixed_point",
        record_root,
    )
    result = capacity_coupling._reduced_manifold(
        law, fractions, start, root, 3600.0, track_extent=False,
    )

    assert result is None
    assert rates["first"] == rates["repeat"]
    assert len(root_hints) == 3
    assert root_hints == [root, root, root]


def test_reduced_pstar_finite_difference_is_smooth_below_outer_atol():
    law, _holder, fractions, _start, anchor = _reduced_manifold_case(stiff=True)
    species = tuple(law.species)
    species_a, species_b = species[:2]
    initial_a = fractions[species_a]
    curvature_per_Pa2 = 4.5e8
    composition_sensitivity_Pa = 0.1

    def total_outflow(pressure_Pa, mix):
        result = capacity_coupling.pipe_species_outflow_mol_s(
            pressure_Pa,
            _IFT_DOWNSTREAM_PA,
            _IFT_T_K,
            mix,
            _IFT_D_M,
            _IFT_L_M,
            molar_mass_kg_mol=law.masses,
        )
        return math.fsum(
            float(result.species_mol_s.get(name, 0.0)) for name in species
        )

    derivative_step_Pa = 1.0e-5
    outflow_slope_mol_s_Pa = (
        total_outflow(anchor + derivative_step_Pa, fractions)
        - total_outflow(anchor - derivative_step_Pa, fractions)
    ) / (2.0 * derivative_step_Pa)

    def smooth_flux(partials):
        pressure_Pa = math.fsum(
            float(partials.get(name, 0.0)) for name in species
        )
        if pressure_Pa <= 0.0:
            return {species_a: 0.0}
        mix = {
            name: float(partials.get(name, 0.0)) / pressure_Pa
            for name in species
        }
        outflow = total_outflow(pressure_Pa, mix)
        root_Pa = anchor + composition_sensitivity_Pa * (
            mix[species_a] - initial_a
        )
        offset_Pa = pressure_Pa - root_Pa
        residual_mol_s = -outflow_slope_mol_s_Pa * (
            offset_Pa + curvature_per_Pa2 * offset_Pa**3
        )
        return {
            species_a: (outflow + residual_mol_s)
            * law.masses[species_a]
            * 3600.0
        }

    # Premise: the single-root smooth law is G = -m(δP + c·δP³), with
    # δP = P - P*(x) and P*(x) = anchor + 0.1·(x - x₀). The IFT pressure
    # stencil is about 2e-5 Pa, so c·h² = 0.18; for Δx = 1e-7 the old
    # one-chord miss is about 1.5e-9 Pa: above 1e-10 Pa (10⁻³ outer atol)
    # but below the former 1e-6 Pa gate. Unit check: m [mol/(s·Pa)] times
    # [δP + c·δP³] [Pa] gives G [mol/s]. As c → 0, one chord is exact.
    law.flux = smooth_flux
    center_kind, center_root = capacity_coupling._open_or_shut_fixed_point(
        law, fractions, near_Pa=anchor,
    )
    assert center_kind == "open"
    assert center_root is not None

    reduced_roots = {}
    resolved_roots = {}
    for sign in (-1.0, 1.0):
        sample = dict(fractions)
        sample[species_a] += sign * 1.0e-7
        sample[species_b] -= sign * 1.0e-7
        reduced_kind, reduced_root = capacity_coupling._reduced_open_fixed_point(
            law, sample, float(center_root),
        )
        resolved_kind, resolved_root = capacity_coupling._open_or_shut_fixed_point(
            law, sample, near_Pa=float(center_root),
        )
        assert reduced_kind == resolved_kind == "open"
        assert reduced_root is not None and resolved_root is not None
        reduced_roots[sign] = float(reduced_root)
        resolved_roots[sign] = float(resolved_root)

    reduced_difference_Pa = reduced_roots[1.0] - reduced_roots[-1.0]
    resolved_difference_Pa = resolved_roots[1.0] - resolved_roots[-1.0]
    assert abs(reduced_difference_Pa - resolved_difference_Pa) <= (
        1.0e-3 * capacity_coupling._SLOW_ATOL_PA
    )


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


def test_inventory_gate_boundary_is_one_micro_pascal():
    # The production comparison is strict `> DEFAULT_ABS_TOL_PA`.
    # Equality is inside. The millibar round trip can move a 1e-6 Pa
    # gap, so the read-back decides which side the gate sees.
    sim = _lunar()
    inventory_pa = float(sim._headspace_inventory_pressure_Pa())

    def read_back(gap_pa: float) -> float:
        sim.overhead.pressure_mbar = (inventory_pa + gap_pa) / 100.0
        return abs(float(sim.overhead.pressure_mbar) * 100.0 - inventory_pa)

    inside = DEFAULT_ABS_TOL_PA
    inside_gap = read_back(inside)
    while inside_gap > DEFAULT_ABS_TOL_PA:
        inside = math.nextafter(inside, 0.0)
        inside_gap = read_back(inside)
    assert 0.0 < inside_gap <= DEFAULT_ABS_TOL_PA
    sim._refuse_if_headspace_pressure_is_not_inventory()

    outside = math.nextafter(inside, math.inf)
    outside_gap = read_back(outside)
    while outside_gap <= DEFAULT_ABS_TOL_PA:
        outside = math.nextafter(outside, math.inf)
        outside_gap = read_back(outside)
    assert outside_gap > DEFAULT_ABS_TOL_PA
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


def test_continuation_posts_the_captured_share_not_the_gross():
    """A held interval credits A to overhead and G − A to the condenser.

    Capture is 0.9999. CrO, CrO2 and CrO3 integrate to 9e-13 kg. That
    mass is under the kilogram floor, and the same mass over 3.24 s is
    a rate above the floor, so the solve's q is already net of capture.
    Mg, Fe, Na and K integrate above the floor. CrO3 carries the
    declared oxygen-crediting wall booking. Na and K are on the K trap.
    """

    from engines.builtin.condensation_route import (
        BuiltinCondensationRouteProvider,
        booking_scaled_to_vapour_split,
    )
    from engines.builtin.evaporation_transition import (
        evaporation_parent_is_below_floor,
    )
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.lots import EMPTY_KG_TOLERANCE
    from simulator.account_ids import (
        ALKALI_CARTRIDGE_ACCOUNT,
        ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT,
        CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
    )
    from simulator.capacity_coupling import (
        _closed_manifold,
        headspace_capture_fractions,
        headspace_capture_scale,
    )
    from simulator.condensation import CondensationRouteResult
    from simulator.condensation_routing import (
        K_TRAP_STAGE_KEY,
        K_TRAP_STAGE_NUMBER,
        K_TRAP_SUB_ORDER,
        stage_index_for_visit_identity,
    )
    from simulator.evaporation import _stoich_o2_is_overhead
    from simulator.state import EvaporationFlux
    from simulator.vapour_rail.wall_saturation import _assert_atom_balance

    capture = 0.9999
    held_s = 3.24
    subfloor_kg = 9.0e-13
    above_kg = 1.0e-6
    gross_kg = {
        "CrO": subfloor_kg,
        "CrO2": subfloor_kg,
        "CrO3": subfloor_kg,
        "Mg": above_kg,
        "Fe": above_kg,
        "Na": above_kg,
        "K": above_kg,
    }
    # The capture scale is the production remainder. One dummy partial
    # of 1 Pa matches the total passed to the scale, so q = gross * (1 − f).
    _frozen, scale = headspace_capture_scale(
        gross_kg,
        {species: capture for species in gross_kg},
        1.0,
        fractions_frozen={},
        capture_C={},
    )
    fractions = headspace_capture_fractions(
        gross_kg,
        {"held": 1.0},
        capture_C=scale,
        fractions_frozen=_frozen,
    )
    net_kg = {}
    for species, kilograms in gross_kg.items():
        assert fractions[species] == capture
        net_kg[species] = min(
            kilograms,
            max(0.0, float(scale[species])) / 1.0,
        )
        assert kilograms - net_kg[species] > 0.0

    sim = _lunar()
    registry = sim.species_formula_registry

    def _row(species: str) -> dict:
        metals = sim.vapor_pressures.get("metals", {}) or {}
        oxides = sim.vapor_pressures.get("oxide_vapors", {}) or {}
        row = dict(metals.get(species) or oxides.get(species) or {})
        assert row.get("parent_oxide"), species
        return row

    def _molar(species: str) -> float:
        return resolve_species_formula(
            species, registry,
        ).molar_mass_kg_per_mol()

    rows = {species: _row(species) for species in gross_kg}
    stoich = {
        species: sim._evaporation_stoich(species, rows[species])
        for species in gross_kg
    }
    # t_rem = 1 in the manifold's product, so vapour is the integral and
    # added is the net moles. The 3.24 s figure is that integral divided
    # by a rate the instantaneous floor would still treat as captured.
    inflow = {
        species: (net_kg[species] / _molar(species))
        for species in gross_kg
    }
    held = _closed_manifold(
        tuple(gross_kg),
        {species: 1.0 for species in gross_kg},
        inflow,
        {species: 0.0 for species in gross_kg},
        dict(gross_kg),
        1.0,
    )
    assert held is not None
    _n_end, added, _bled, vapour, _redox, _composition_time, _dy = held
    extent = {}
    remaining = {}
    for species in gross_kg:
        assert vapour[species] == gross_kg[species]
        assert added[species] == inflow[species]
        extent[species] = vapour[species]
        remaining[species] = added[species] * _molar(species)
    assert extent["CrO"] <= EMPTY_KG_TOLERANCE
    assert extent["CrO"] / (held_s / 3600.0) > EMPTY_KG_TOLERANCE
    assert 0.0 < extent["CrO"] - remaining["CrO"] <= EMPTY_KG_TOLERANCE
    assert extent["Na"] - remaining["Na"] > EMPTY_KG_TOLERANCE

    parent_mol: dict[str, float] = {}
    for species in gross_kg:
        parent = stoich[species]["parent_oxide"]
        parent_mol.setdefault(parent, 1.0 / _molar(parent))
    _seed(sim, "process.cleaned_melt", parent_mol, "continuation parents")
    _seed(sim, "reservoir.fo2_buffer", {"O2": 1.0}, "continuation oxidant")
    available = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    post_subfloor = []
    for species, kilograms in extent.items():
        parent = stoich[species]["parent_oxide"]
        oxide_kg = kilograms * stoich[species]["oxide_per_product_kg"]
        stock = float(available.get(parent, 0.0) or 0.0)
        posts = sim._evaporation_transition_posts_parcel(
            kilograms,
            stoich[species],
            stock,
        )
        if evaporation_parent_is_below_floor(oxide_kg, stock) or not posts:
            post_subfloor.append(species)
    assert set(post_subfloor) == {"CrO", "CrO2", "CrO3"}

    # The booking is the declared reaction that credits oxygen, per mol
    # of vapour. The saturation ladder is not required to scale it:
    # vapour_stoichiometry is the divisor the ledger already uses.
    declared = rows["CrO3"]["wall_reactions"]["reactions"]
    crediting = next(
        reaction
        for reaction in declared
        if float(reaction["oxygen_stoichiometry"]) < 0.0
        and float(reaction["vapour_stoichiometry"]) > 0.0
    )
    _assert_atom_balance("CrO3", crediting)
    nu_vapour = float(crediting["vapour_stoichiometry"])
    nu_oxygen = float(crediting["oxygen_stoichiometry"])
    products = {
        str(product["species"]): float(product["stoichiometry"]) / nu_vapour
        for product in crediting["products"]
    }
    oxygen_credit = (-nu_oxygen) / nu_vapour
    booking = {
        "baffle_products_mol": products,
        "baffle_oxygen_credit_mol": oxygen_credit,
        "baffle_oxygen_debit_mol": 0.0,
        "wall_products_mol_by_account": {},
        "wall_oxygen_credit_mol": 0.0,
        "wall_oxygen_debit_mol": 0.0,
        "reaction_legs": [
            {
                "surface": "baffle",
                "vapour_mol": 1.0,
                "products_mol": dict(products),
                "oxygen_credit_mol": oxygen_credit,
                "oxygen_debit_mol": 0.0,
                "reaction_id": crediting.get("id"),
            },
        ],
    }
    assert stage_index_for_visit_identity(
        sim.train, K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER,
    ) is not None
    route_result = CondensationRouteResult(
        remaining_by_species={
            species: extent[species] for species in gross_kg
        },
        condensed_by_stage_species={
            K_TRAP_STAGE_KEY: {
                "Na": extent["Na"],
                "K": extent["K"],
            },
        },
        capture_kg_by_stage_identity={
            (K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER): {
                "Na": extent["Na"],
                "K": extent["K"],
            },
        },
    )
    sim.condensation_model.route = lambda _flux, _melt: route_result
    flux = EvaporationFlux(species_kg_hr=dict(extent))
    flux.update_totals()
    sim._route_to_condensation(
        flux,
        headspace_remaining_kg_hr=remaining,
        headspace_booking_by_species={"CrO3": booking},
        headspace_wall_fraction_by_species={},
        headspace_wall_accounts_by_species={},
        post_subfloor_species=post_subfloor,
    )

    def _mol(account: str, species: str) -> float:
        return float(
            sim.atom_ledger.mol_by_account(account).get(species, 0.0) or 0.0
        )

    train = "process.condensation_train"
    overhead = "process.overhead_gas"
    chromium = BuiltinCondensationRouteProvider.CHROMIUM_CONDENSED_ACCOUNT
    for species in ("CrO", "Mg", "Fe", "Na", "K"):
        # One gross credit, split. Overhead is what the captured share
        # leaves behind. The condenser holds (G − A) / M. Summing the
        # two mols is the gross mol, so G is not posted twice.
        captured_kg = extent[species] - remaining[species]
        captured_mol = captured_kg / _molar(species)
        gross_mol = extent[species] / _molar(species)
        overhead_mol = _mol(overhead, species)
        train_mol = _mol(train, species)
        assert train_mol == captured_mol
        assert overhead_mol == gross_mol - captured_mol
        assert overhead_mol + train_mol == gross_mol
        assert overhead_mol != gross_mol
        assert train_mol != 0.0
    cro2_captured = extent["CrO2"] - remaining["CrO2"]
    cro2_mol, _cro2_kg = sim._condensed_products_for_vapor(
        "CrO2",
        cro2_captured,
        rows["CrO2"],
    )
    cro2_accounts = dict(
        rows["CrO2"].get("condensation_product_accounts") or {}
    )
    assert cro2_accounts["Cr2O3"] == chromium
    assert cro2_accounts["O2"] == overhead
    cro3_captured_mol = (
        extent["CrO3"] - remaining["CrO3"]
    ) / _molar("CrO3")
    scaled = booking_scaled_to_vapour_split(
        booking,
        baffle_mol=cro3_captured_mol,
        wall_mol=0.0,
    )
    for species in ("CrO2", "CrO3"):
        captured_kg = extent[species] - remaining[species]
        captured_mol = captured_kg / _molar(species)
        gross_mol = extent[species] / _molar(species)
        assert _mol(overhead, species) == gross_mol - captured_mol
        assert _mol(overhead, species) != gross_mol
    assert _mol(train, "CrO2") == 0.0
    assert _mol(train, "CrO3") == 0.0
    assert _mol(chromium, "Cr2O3") == (
        float(cro2_mol["Cr2O3"])
        + float(scaled["baffle_products_mol"]["Cr2O3"])
    )
    expected_overhead_o2 = float(cro2_mol["O2"]) + float(
        scaled["baffle_oxygen_credit_mol"]
    )
    for species, kilograms in extent.items():
        o2_kg = kilograms * float(stoich[species]["O2_per_product_kg"])
        if o2_kg <= 0.0:
            continue
        atoms = float(
            resolve_species_formula(species, registry).elements.get("O", 0.0)
            or 0.0
        )
        if _stoich_o2_is_overhead(
            o2_kg,
            str(stoich[species].get("oxygen_destination") or ""),
            atoms,
        ):
            expected_overhead_o2 += o2_kg / _molar("O2")
    assert _mol(overhead, "O2") == expected_overhead_o2
    assert _mol(CONDENSATION_RETAINED_HOLDUP_ACCOUNT, "CrO") == 0.0
    assert _mol(CONDENSATION_RETAINED_HOLDUP_ACCOUNT, "CrO2") == 0.0
    assert _mol(ALKALI_CARTRIDGE_ACCOUNT, "Na") == 0.0
    assert _mol(ALKALI_CARTRIDGE_ACCOUNT, "K") == 0.0
    assert _mol(ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT, "Na") == 0.0
    assert _mol(ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT, "K") == 0.0
    names = [transition.name for transition in sim.atom_ledger.transitions]
    for species in ("Na", "K"):
        assert f"alkali_cartridge_accumulate_{species}" in names
        assert f"alkali_cartridge_melt_out_{species}" in names
        accumulate = next(
            transition
            for transition in sim.atom_ledger.transitions
            if transition.name == f"alkali_cartridge_accumulate_{species}"
        )
        moved = float(accumulate.credits[0].species_kg[species])
        assert moved == extent[species] - remaining[species]


def _alkali_reaction(species: str, captured_kg: float, molar_kg: float) -> dict:
    """4 M + O2 -> 2 M2O, the production stage-3 update.

    uptake is the captured share. The debit is uptake/4 and the oxide
    is uptake/2, which is the stoichiometry condensation.py writes
    once the reaction is favoured. The ledger assertion is the
    remaining share, not this ratio.
    """

    uptake_mol = captured_kg / molar_kg
    oxide = "Na2O" if species == "Na" else "K2O"
    return {
        "uptake_mol": uptake_mol,
        "oxide_species": oxide,
        "oxide_product_mol": uptake_mol / 2.0,
        "oxygen_debit_mol": uptake_mol / 4.0,
        "silica_partner": "structural substrate/deposit; no SiO2 ledger debit",
    }


def _route_one_parcel(
    sim,
    species: str,
    gross_kg: float,
    remaining_kg: float,
    *,
    headspace: bool,
    reaction: dict | None = None,
    post_subfloor: bool = False,
    booking: dict | None = None,
):
    from simulator.condensation import CondensationRouteResult
    from simulator.state import EvaporationFlux

    route_result = CondensationRouteResult(
        remaining_by_species={species: remaining_kg},
        stage3_reactive_alkali_by_species=(
            {species: dict(reaction)} if reaction is not None else {}
        ),
    )
    sim.condensation_model.route = lambda _flux, _melt: route_result
    flux = EvaporationFlux(species_kg_hr={species: gross_kg})
    flux.update_totals()
    kwargs = {}
    if headspace:
        kwargs["headspace_remaining_kg_hr"] = {species: remaining_kg}
    if post_subfloor:
        kwargs["post_subfloor_species"] = (species,)
    # The booking is S's oxygen. Passing it without the remaining
    # override keeps the condensation floor and still scales the
    # declared reaction, which is the withheld side of the identity.
    if booking is not None:
        kwargs["headspace_booking_by_species"] = {species: booking}
        kwargs["headspace_wall_fraction_by_species"] = {}
        kwargs["headspace_wall_accounts_by_species"] = {}
    sim._route_to_condensation(flux, **kwargs)


def _ledger_mol(sim, account: str, species: str) -> float:
    return float(sim.atom_ledger.mol_by_account(account).get(species, 0.0) or 0.0)


def test_favoured_alkali_headspace_hours_post_the_captured_share():
    """Na 9e-13, Na 2e-12 and K 2e-12 close onto the remaining share.

    The favoured reaction's lightest leg is the O2 debit, under the
    kilogram floor. Na 2e-12 and K 2e-12 are themselves above that
    floor. The headspace commit posts the oxide and leaves the
    remainder in the overhead. The same parcel with the evaporation
    leg posted and the condensation flag off keeps the gross metal
    and posts no oxide. An above-floor Na hour is the same ledger
    with the flag and without it.
    """

    from engines.builtin.evaporation_transition import (
        evaporation_parent_is_below_floor,
    )
    from simulator.accounting.formulas import resolve_species_formula

    capture = 0.9999
    cases = (("Na", 9.0e-13), ("Na", 2.0e-12), ("K", 2.0e-12))
    for species, captured_kg in cases:
        gross_kg = captured_kg / capture
        remaining_kg = gross_kg - captured_kg
        posted = _lunar()
        withheld = _lunar()
        molar = resolve_species_formula(
            species, posted.species_formula_registry,
        ).molar_mass_kg_per_mol()
        reaction = _alkali_reaction(species, captured_kg, molar)
        oxide = reaction["oxide_species"]
        row = dict(
            (posted.vapor_pressures.get("metals", {}) or {}).get(species)
            or {}
        )
        stoich = posted._evaporation_stoich(species, row)
        parent = stoich["parent_oxide"]
        parent_molar = resolve_species_formula(
            parent, posted.species_formula_registry,
        ).molar_mass_kg_per_mol()
        for sim in (posted, withheld):
            _seed(
                sim,
                "process.cleaned_melt",
                {parent: 1.0 / parent_molar},
                f"{species} parent",
            )
            _seed(
                sim,
                "process.overhead_gas",
                {"O2": 1.0},
                f"{species} overhead oxygen",
            )
        stock = float(
            posted.atom_ledger.kg_by_account("process.cleaned_melt").get(
                parent, 0.0,
            )
            or 0.0
        )
        oxide_kg = gross_kg * float(stoich["oxide_per_product_kg"])
        needs_evaporation_flag = (
            evaporation_parent_is_below_floor(oxide_kg, stock)
            or not posted._evaporation_transition_posts_parcel(
                gross_kg, stoich, stock,
            )
        )
        _route_one_parcel(
            posted,
            species,
            gross_kg,
            remaining_kg,
            headspace=True,
            reaction=reaction,
            post_subfloor=needs_evaporation_flag,
        )
        _route_one_parcel(
            withheld,
            species,
            gross_kg,
            remaining_kg,
            headspace=False,
            reaction=reaction,
            post_subfloor=needs_evaporation_flag,
        )
        captured_mol = captured_kg / molar
        gap_pa = posted._headspace_inventory_pressure_Pa(
            {species: captured_mol},
        )
        assert gap_pa > DEFAULT_ABS_TOL_PA
        overhead = "process.overhead_gas"
        train = "process.condensation_train"
        overhead_mol = _ledger_mol(posted, overhead, species)
        # The ledger debits the captured mol from the gross mol. The
        # remaining mass divided by the molar mass is that difference
        # to a unit in the last place, whose pressure is dust beside
        # the 1e-6 Pa gate.
        dust_pa = posted._headspace_inventory_pressure_Pa(
            {species: abs(overhead_mol - remaining_kg / molar)},
        )
        assert dust_pa < 1.0e-18
        assert posted._headspace_inventory_pressure_Pa(
            {species: abs(overhead_mol - gross_kg / molar)},
        ) == gap_pa or abs(
            posted._headspace_inventory_pressure_Pa(
                {species: abs(overhead_mol - gross_kg / molar)},
            )
            - gap_pa
        ) < 1.0e-18
        assert _ledger_mol(posted, train, oxide) == reaction["oxide_product_mol"]
        assert _ledger_mol(posted, overhead, "O2") == (
            1.0 - reaction["oxygen_debit_mol"]
        )
        assert _ledger_mol(withheld, train, oxide) == 0.0
        withheld_mol = _ledger_mol(withheld, overhead, species)
        assert posted._headspace_inventory_pressure_Pa(
            {species: abs(withheld_mol - gross_kg / molar)},
        ) < 1.0e-18
        assert _ledger_mol(withheld, overhead, "O2") == 1.0

    # Above the floor on every leg. The flag and the kilogram floor
    # post the same reaction, so the two ledgers match.
    species = "Na"
    captured_kg = 1.0e-6
    gross_kg = captured_kg / capture
    remaining_kg = gross_kg - captured_kg
    flagged = _lunar()
    plain = _lunar()
    molar = resolve_species_formula(
        species, flagged.species_formula_registry,
    ).molar_mass_kg_per_mol()
    reaction = _alkali_reaction(species, captured_kg, molar)
    parent = flagged._evaporation_stoich(
        species,
        dict((flagged.vapor_pressures.get("metals", {}) or {}).get(species) or {}),
    )["parent_oxide"]
    parent_molar = resolve_species_formula(
        parent, flagged.species_formula_registry,
    ).molar_mass_kg_per_mol()
    for sim in (flagged, plain):
        _seed(
            sim,
            "process.cleaned_melt",
            {parent: 1.0 / parent_molar},
            "above-floor parent",
        )
        _seed(sim, "process.overhead_gas", {"O2": 1.0}, "above-floor oxygen")
    _route_one_parcel(
        flagged, species, gross_kg, remaining_kg,
        headspace=True, reaction=reaction,
    )
    _route_one_parcel(
        plain, species, gross_kg, remaining_kg,
        headspace=False, reaction=reaction,
    )
    oxide = reaction["oxide_species"]
    for account, label in (
        ("process.overhead_gas", species),
        ("process.condensation_train", oxide),
        ("process.overhead_gas", "O2"),
    ):
        assert _ledger_mol(flagged, account, label) == _ledger_mol(
            plain, account, label,
        )
    assert _ledger_mol(flagged, "process.condensation_train", oxide) == (
        reaction["oxide_product_mol"]
    )


def _oxygen_crediting_booking(sim, species: str) -> dict:
    """The declared wall reaction that credits oxygen, per mol of vapour.

    The continuation hour builds this same booking inline for CrO3.
    The scale the predictor and the route share is
    booking_scaled_to_vapour_split, not this dict.
    """

    from simulator.vapour_rail.wall_saturation import _assert_atom_balance

    oxides = sim.vapor_pressures.get("oxide_vapors", {}) or {}
    row = dict(oxides.get(species) or {})
    declared = row["wall_reactions"]["reactions"]
    crediting = next(
        reaction
        for reaction in declared
        if float(reaction["oxygen_stoichiometry"]) < 0.0
        and float(reaction["vapour_stoichiometry"]) > 0.0
    )
    _assert_atom_balance(species, crediting)
    nu_vapour = float(crediting["vapour_stoichiometry"])
    nu_oxygen = float(crediting["oxygen_stoichiometry"])
    products = {
        str(product["species"]): float(product["stoichiometry"]) / nu_vapour
        for product in crediting["products"]
    }
    oxygen_credit = (-nu_oxygen) / nu_vapour
    return {
        "baffle_products_mol": products,
        "baffle_oxygen_credit_mol": oxygen_credit,
        "baffle_oxygen_debit_mol": 0.0,
        "wall_products_mol_by_account": {},
        "wall_oxygen_credit_mol": 0.0,
        "wall_oxygen_debit_mol": 0.0,
        "reaction_legs": [
            {
                "surface": "baffle",
                "vapour_mol": 1.0,
                "products_mol": dict(products),
                "oxygen_credit_mol": oxygen_credit,
                "oxygen_debit_mol": 0.0,
                "reaction_id": crediting.get("id"),
            },
        ],
    }


def test_cro3_band_hour_oxygen_matches_the_integrated_captured_mass():
    """The CrO3 product floor no longer splits the rate from its integral.

    The declared reductive booking credits oxygen and posts Cr2O3.
    A captured mass in (1e-12, 1.32e-12] kg still has that product in
    (0, 1e-12] kg, and 1.2e-12 kg/h over 0.8 h is 9.6e-13 kg. Off the
    headspace commit the predictor and the route post no oxygen. On
    it the predictor is homogeneous in the captured mass, so the rate
    integrated over the hour equals the oxygen the route posts for
    the integrated mass. A mass whose product clears the floor posts
    the same oxygen with the flag and without it.

    The evaporation oxidant is one debit of the seeded buffer. The
    booking's oxygen is an overhead credit, not a second buffer debit.

    Mutation: debiting reservoir.fo2_buffer from the condensation
    booking, on either flag, leaves a mol other than the seeded stock
    plus mass * O2_per_product_kg / M_O2.
    """

    from engines.builtin.condensation_route import (
        BuiltinCondensationRouteProvider,
        booking_scaled_to_vapour_split,
    )
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.lots import EMPTY_KG_TOLERANCE

    sim = _lunar()
    registry = sim.species_formula_registry
    row = dict(
        (sim.vapor_pressures.get("oxide_vapors", {}) or {}).get("CrO3") or {}
    )
    assert row
    booking = _oxygen_crediting_booking(sim, "CrO3")
    vapour_molar = resolve_species_formula(
        "CrO3", registry,
    ).molar_mass_kg_per_mol()

    def _product_kg(mass_kg: float) -> dict[str, float]:
        scaled = booking_scaled_to_vapour_split(
            booking,
            baffle_mol=mass_kg / vapour_molar,
            wall_mol=0.0,
        )
        masses = {}
        for name, mol in dict(scaled["baffle_products_mol"]).items():
            solid = resolve_species_formula(
                str(name), registry,
            ).molar_mass_kg_per_mol()
            masses[str(name)] = float(mol) * solid
        return masses

    def _predict(mass_kg: float, *, headspace: bool) -> float:
        return sim._condensation_overhead_o2_mol(
            "CrO3",
            mass_kg,
            booking=booking,
            post_subfloor_captured_share=headspace,
        )

    rate_kg_h = 1.2e-12
    hours = 0.8
    integral_kg = rate_kg_h * hours
    assert EMPTY_KG_TOLERANCE < rate_kg_h
    assert integral_kg <= EMPTY_KG_TOLERANCE
    assert any(
        0.0 < kg <= EMPTY_KG_TOLERANCE
        for kg in _product_kg(rate_kg_h).values()
    )
    assert _predict(rate_kg_h, headspace=False) == 0.0
    assert _predict(integral_kg, headspace=False) == 0.0
    per_hour = _predict(rate_kg_h, headspace=True)
    posted_o2 = _predict(integral_kg, headspace=True)
    assert per_hour > 0.0
    assert per_hour * hours == posted_o2

    chromium = BuiltinCondensationRouteProvider.CHROMIUM_CONDENSED_ACCOUNT
    stoich = sim._evaporation_stoich("CrO3", row)
    parent = stoich["parent_oxide"]
    parent_molar = resolve_species_formula(
        parent, registry,
    ).molar_mass_kg_per_mol()
    # Premise: CrO1.5(l) + oxidant -> CrO3(g). The oxidant is one
    # debit of the gross vapour, and O2_per_product_kg is that
    # kilogram ratio, negative.
    # Algebra: n = n_seed + m_vapour * (kg_O2 / kg_vapour) / M_O2.
    # Unit check: kg * (kg/kg) / (kg/mol) = mol.
    # Limit: m_vapour = 0 leaves the seeded 1 mol. A second debit of
    # the same oxidant, or of the condensation credit, does not.
    o2_per_product_kg = float(stoich["O2_per_product_kg"])
    assert o2_per_product_kg < 0.0
    o2_molar = resolve_species_formula(
        "O2", registry,
    ).molar_mass_kg_per_mol()

    def _buffer_after_one_debit(mass_kg: float) -> float:
        return 1.0 + mass_kg * o2_per_product_kg / o2_molar

    def _routed(mass_kg: float, *, headspace: bool) -> tuple[float, float]:
        trial = _lunar()
        _seed(
            trial,
            "process.cleaned_melt",
            {parent: 1.0 / parent_molar},
            "CrO3 parent",
        )
        _seed(trial, "reservoir.fo2_buffer", {"O2": 1.0}, "CrO3 oxidant")
        before = _ledger_mol(trial, "process.overhead_gas", "O2")
        _route_one_parcel(
            trial,
            "CrO3",
            mass_kg,
            0.0,
            headspace=headspace,
            post_subfloor=True,
            booking=booking,
        )
        after = _ledger_mol(trial, "process.overhead_gas", "O2")
        assert _ledger_mol(trial, "reservoir.fo2_buffer", "O2") == (
            _buffer_after_one_debit(mass_kg)
        )
        return after - before, _ledger_mol(trial, chromium, "Cr2O3")

    delta, oxide = _routed(integral_kg, headspace=True)
    assert delta == posted_o2
    assert oxide > 0.0
    withheld_delta, withheld_oxide = _routed(integral_kg, headspace=False)
    assert withheld_delta == 0.0
    assert withheld_oxide == 0.0
    for mass_kg in (1.01e-12, 1.2e-12):
        assert mass_kg > EMPTY_KG_TOLERANCE
        assert any(
            0.0 < kg <= EMPTY_KG_TOLERANCE
            for kg in _product_kg(mass_kg).values()
        )
        flagged = _predict(mass_kg, headspace=True)
        assert _predict(mass_kg, headspace=False) == 0.0
        assert flagged > 0.0
        delta, oxide = _routed(mass_kg, headspace=True)
        assert delta == flagged
        assert oxide > 0.0
        withheld_delta, withheld_oxide = _routed(mass_kg, headspace=False)
        assert withheld_delta == 0.0
        assert withheld_oxide == 0.0
    clearing = 1.5e-12
    assert all(kg > EMPTY_KG_TOLERANCE for kg in _product_kg(clearing).values())
    clearing_on = _predict(clearing, headspace=True)
    clearing_off = _predict(clearing, headspace=False)
    assert clearing_off > 0.0
    assert clearing_on == clearing_off
    delta, _oxide = _routed(clearing, headspace=True)
    withheld_delta, _withheld_oxide = _routed(clearing, headspace=False)
    assert delta == clearing_on
    assert withheld_delta == clearing_off


def _stage_species_kg(sim, species: str) -> float:
    return math.fsum(
        float(stage.collected_kg.get(species, 0.0) or 0.0)
        for stage in sim.train.stages
    )


def test_headspace_source_keeps_a_subfloor_captured_rate():
    """The headspace source does not zero a captured rate under 1e-12 kg/h.

    Mutation: restoring ``captured_kg <= rate_floor_kg_hr`` zeros a
    5e-13 kg/h share before the oxygen credit. The hour then
    integrates no reductive oxygen, and the predicate call is gone
    from that clip.
    """

    hour = inspect.getsource(
        PyrolysisSimulator._solve_and_commit_headspace_hour
    )
    start = hour.index("captured_kg = rate * captured_f")
    window = hour[start:start + 700]
    assert "headspace_commit_drops_positive_mass" in window
    assert "post_subfloor_captured_share=True" in window
    assert "rate_floor_kg_hr" not in window


def test_headspace_subfloor_solid_is_the_stage_inventory():
    """A subfloor solid on the ledger is the cartridge inventory.

    The CrO3 booking posts Cr2O3 under 1e-12 kg. The stage book and
    the purity report used to drop that kilogram with no flag.

    Mutation: comparing the solid, the captured vapour, or the purity
    total with 1e-12 leaves collected_kg empty and the report
    indeterminate. Projecting the same solid with the flag off, or
    reporting the flagged train without the flag, puts the solid
    where the ordinary floor still hides it. This test fails that.
    """

    from types import SimpleNamespace

    from engines.builtin.condensation_route import (
        BuiltinCondensationRouteProvider,
    )
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.accounting.lots import EMPTY_KG_TOLERANCE
    from simulator.condensation import (
        STAGE_PURITY_VERDICT_INDETERMINATE,
        STAGE_PURITY_VERDICT_PURE,
        headspace_commit_posted_subfloor_share,
        stage_purity_report,
    )
    from simulator.condensation import CondensationRouteResult
    from simulator.condensation_routing import STAGE_KEY_BY_NUMBER

    vapor_kg = 1.2e-12
    assert vapor_kg > EMPTY_KG_TOLERANCE
    sim = _lunar()
    registry = sim.species_formula_registry
    row = dict(
        (sim.vapor_pressures.get("oxide_vapors", {}) or {}).get("CrO3") or {}
    )
    booking = _oxygen_crediting_booking(sim, "CrO3")
    parent = sim._evaporation_stoich("CrO3", row)["parent_oxide"]
    parent_molar = resolve_species_formula(
        parent, registry,
    ).molar_mass_kg_per_mol()
    _seed(
        sim, "process.cleaned_melt",
        {parent: 1.0 / parent_molar}, "stage-book parent",
    )
    _seed(sim, "reservoir.fo2_buffer", {"O2": 1.0}, "stage-book oxidant")
    _route_one_parcel(
        sim, "CrO3", vapor_kg, 0.0,
        headspace=True, post_subfloor=True, booking=booking,
    )
    chromium = BuiltinCondensationRouteProvider.CHROMIUM_CONDENSED_ACCOUNT
    ledger_kg = float(
        sim.atom_ledger.kg_by_account(chromium).get("Cr2O3", 0.0) or 0.0
    )
    assert 0.0 < ledger_kg <= EMPTY_KG_TOLERANCE
    oxide_mol = _ledger_mol(sim, chromium, "Cr2O3")
    proposal = SimpleNamespace(credits={chromium: {"Cr2O3": oxide_mol}})
    kept = sim._stage_baffle_product_kg_from_credits(
        proposal, post_subfloor_captured_share=True,
    )
    assert kept[chromium]["Cr2O3"] == ledger_kg
    withheld = sim._stage_baffle_product_kg_from_credits(
        proposal, post_subfloor_captured_share=False,
    )
    assert "Cr2O3" not in withheld.get(chromium, {})
    snap = sim._last_condensation_baffle_product_kg_by_account
    assert snap[chromium]["Cr2O3"] == ledger_kg

    stage_key = STAGE_KEY_BY_NUMBER[2]

    def _project(host, mass_kg: float, *, headspace: bool) -> None:
        route = CondensationRouteResult(
            condensed_by_stage_species={stage_key: {"CrO3": mass_kg}},
            capture_kg_by_stage_identity={(2, 0): {"CrO3": mass_kg}},
            wall_reaction_booking_by_species={"CrO3": booking},
        )
        host._last_condensation_baffle_product_kg_by_account = {
            chromium: {"Cr2O3": ledger_kg},
        }
        host._project_routed_condensate(
            route, "CrO3", mass_kg, row,
            post_subfloor_captured_share=headspace,
        )

    assert _stage_species_kg(sim, "Cr2O3") == 0.0
    _project(sim, vapor_kg, headspace=True)
    assert _stage_species_kg(sim, "Cr2O3") == ledger_kg
    flagged = stage_purity_report(
        sim.train,
        post_subfloor_captured_share=(
            headspace_commit_posted_subfloor_share(sim)
        ),
    )
    assert headspace_commit_posted_subfloor_share(sim) is True
    assert flagged[stage_key]["designated_species_kg"]["Cr2O3"] == ledger_kg
    assert flagged[stage_key]["activity"]["Cr2O3"] is True
    assert flagged[stage_key]["verdict"] == STAGE_PURITY_VERDICT_PURE
    plain = stage_purity_report(sim.train)
    assert "Cr2O3" not in plain[stage_key]["designated_species_kg"]
    assert plain[stage_key]["verdict"] == STAGE_PURITY_VERDICT_INDETERMINATE

    # The captured vapour itself under the floor. The early return
    # used to drop it before the solid was scaled.
    subfloor_vapor = ledger_kg
    assert subfloor_vapor <= EMPTY_KG_TOLERANCE
    held = _lunar()
    _project(held, subfloor_vapor, headspace=True)
    assert _stage_species_kg(held, "Cr2O3") == ledger_kg
    missed = _lunar()
    _project(missed, vapor_kg, headspace=False)
    assert _stage_species_kg(missed, "Cr2O3") == 0.0
    missed_vapor = _lunar()
    _project(missed_vapor, subfloor_vapor, headspace=False)
    assert _stage_species_kg(missed_vapor, "Cr2O3") == 0.0
