"""Tests for the BuiltinEvaporationFluxProvider -- second intent flip of
\\goal BUILTIN-ENGINE-EXTRACTION (#7).

Covers:

* Capability profile: provider declares only ``EVAPORATION_FLUX`` and
  ``process.cleaned_melt``; authoritative for the intent.
* Unit: provider's Hertz-Knudsen-Langmuir math matches the legacy
  per-species flux loop for a known composition + T + vapor pressure
  payload.
* Account filter: kernel filter scopes the provider's view to
  ``process.cleaned_melt`` only (defence in depth).
* Shadow parity: across a multi-step simulation run on lunar + Mars +
  asteroid feedstocks, the kernel dispatch agrees with the standalone
  legacy Hertz-Knudsen reference (computed inside the test) species by
  species within 1e-9 kg/hr (relative + absolute floor).
* Diagnostic only: ``transition`` is always ``None`` -- EVAPORATION_FLUX
  owns no ledger mutation (that belongs to EVAPORATION_TRANSITION, not
  yet migrated).
* Below 400 K: provider returns empty flux dict.
* Ground truth: pure-Si HKL mass flux matches the Safarian & Engh
  alpha=1 branch cited in vapor_pressures.yaml, not the parity helper.
"""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import (
    ChemistryIntent,
    IntentRequest,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.evaporation import _load_evaporation_alpha_by_species
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


_FLUX_TOLERANCE_REL = 1e-9
_FLUX_TOLERANCE_ABS_KG_HR = 1e-9


def _legacy_hertz_knudsen_flux(
    sim: PyrolysisSimulator,
    vapor_pressures_Pa: dict,
) -> dict:
    """Recompute the legacy per-species flux dict directly.

    Mirrors the pre-flip body of ``_calculate_evaporation`` line-for-line
    so the parity check is anchored against the exact math the flip
    moved. Anchored separately (NOT delegated to
    ``_calculate_evaporation``) because that method now dispatches the
    kernel -- comparing the kernel against itself would be tautological.
    """

    T_K = sim.melt.temperature_C + 273.15
    flux: dict[str, float] = {}
    if T_K < 400 or not vapor_pressures_Pa:
        return flux

    alpha_by_species = _load_evaporation_alpha_by_species(
        sim.vapor_pressures
    )
    metals_data = sim.vapor_pressures.get('metals', {}) or {}
    oxide_vapors_data = sim.vapor_pressures.get('oxide_vapors', {}) or {}
    for species, P_sat_Pa in vapor_pressures_Pa.items():
        if P_sat_Pa <= 0:
            continue

        sp_data = metals_data.get(species, {})
        if not sp_data:
            sp_data = oxide_vapors_data.get(species, {})

        M_kg_mol = sp_data.get(
            'molar_mass_g_mol', MOLAR_MASS.get(species, 50.0)
        ) / 1000.0
        stoich = sim._evaporation_stoich(species, sp_data)
        P_ambient_Pa = sim.overhead.composition.get(species, 0.0) * 100.0
        mass_flux_factor = math.sqrt(
            M_kg_mol / (2 * math.pi * GAS_CONSTANT * T_K)
        )
        alpha = alpha_by_species.get(species, 1.0)
        J_kg_s_m2 = alpha * (P_sat_Pa - P_ambient_Pa) * mass_flux_factor
        if J_kg_s_m2 <= 0:
            continue
        rate_kg_hr = (
            J_kg_s_m2
            * sim.melt.melt_surface_area_m2
            * sim.melt.stir_factor
            * 3600.0
        )
        if rate_kg_hr > 1e-12:
            flux[species] = rate_kg_hr
    return flux


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_evaporation_flux_intent():
    provider = BuiltinEvaporationFluxProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.EVAPORATION_FLUX})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.EVAPORATION_FLUX}
    )
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_only_cleaned_melt_account():
    provider = BuiltinEvaporationFluxProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({"process.cleaned_melt"})


# ---------------------------------------------------------------------------
# 2. Kernel filter scopes the account view
# ---------------------------------------------------------------------------


def test_kernel_filters_provider_to_cleaned_melt_only(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Even when other accounts hold material, the provider must see only
    ``process.cleaned_melt`` -- the kernel account filter is the
    enforcer."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.metal_phase", {"Fe": 0.5}, source="test seed"
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinEvaporationFluxProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinEvaporationFluxProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.EVAPORATION_FLUX,
            temperature_C=1400.0,
            pressure_bar=1e-6,
            control_inputs={
                'vapor_pressures_Pa': {},
                'overhead_partials_Pa': {},
                'molar_mass_kg_mol': {},
                'stoich_by_species': {},
                'available_oxide_kg': {},
                'melt_surface_area_m2': 0.2,
                'stir_factor': 6.0,
                'alpha': {},
            },
        )
    finally:
        BuiltinEvaporationFluxProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    for accounts in seen_accounts:
        assert accounts == frozenset({"process.cleaned_melt"}), (
            "kernel filter leaked an undeclared account into the provider"
        )


# ---------------------------------------------------------------------------
# 3. Provider returns transition=None (EVAPORATION_FLUX is diagnostic)
# ---------------------------------------------------------------------------


def test_provider_emits_no_ledger_transition():
    """EVAPORATION_FLUX owns no ledger mutation -- the atom-conserving
    debit/credit step belongs to EVAPORATION_TRANSITION (not yet
    migrated). The provider must always leave the result transition
    None."""

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "FeO": 1.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            'vapor_pressures_Pa': {'Na': 100.0},
            'overhead_partials_Pa': {},
            'molar_mass_kg_mol': {'Na': 0.023},
            'stoich_by_species': {
                'Na': {
                    'parent_oxide': 'Na2O',
                    'oxide_per_product_kg': 1.347,
                    'O2_per_product_kg': 0.347,
                },
            },
            'available_oxide_kg': {'Na': 10.0},
            'melt_surface_area_m2': 0.2,
            'stir_factor': 6.0,
            'alpha': 0.5,
        },
    )
    result = provider.dispatch(request)
    assert result.transition is None


# ---------------------------------------------------------------------------
# 4. Physics ground-truth anchor, not parity against local code
# ---------------------------------------------------------------------------


def test_provider_matches_safarian_engh_pure_si_hkl_mass_flux():
    """Pure Si branch cited to Safarian & Engh 2013 must project molar HKL
    flux to mass flux with M in the numerator."""

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            'vapor_pressures_Pa': {'Si': 0.27728678068938384},
            'overhead_partials_Pa': {'Si': 0.0},
            'molar_mass_kg_mol': {'Si': 0.02809},
            'stoich_by_species': {
                'Si': {
                    'parent_oxide': 'SiO2',
                    'oxide_per_product_kg': 2.139551442833749,
                    'O2_per_product_kg': 1.139551442833749,
                },
            },
            'available_oxide_kg': {'Si': 10.0},
            'melt_surface_area_m2': 1.0,
            'stir_factor': 1.0,
            'alpha': {'Si': 1.0},
        },
    )

    result = provider.dispatch(request)

    flux_kg_hr = result.diagnostic['evaporation_flux_kg_hr']['Si']
    assert flux_kg_hr == pytest.approx(0.5497026611860572, rel=1e-12)


# ---------------------------------------------------------------------------
# 5. Below 400 K, provider returns empty flux dict
# ---------------------------------------------------------------------------


def test_provider_short_circuits_below_400_k():
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=25.0,  # Well below 400 K
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            'vapor_pressures_Pa': {'Na': 1e6},
            'overhead_partials_Pa': {},
            'molar_mass_kg_mol': {'Na': 0.023},
            'stoich_by_species': {
                'Na': {
                    'parent_oxide': 'Na2O',
                    'oxide_per_product_kg': 1.347,
                    'O2_per_product_kg': 0.347,
                },
            },
            'available_oxide_kg': {'Na': 10.0},
            'melt_surface_area_m2': 0.2,
            'stir_factor': 6.0,
            'alpha': 0.5,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    assert (result.diagnostic or {}).get("evaporation_flux_kg_hr") == {}


# ---------------------------------------------------------------------------
# 5. Provider math matches the legacy Hertz-Knudsen loop on a known case
# ---------------------------------------------------------------------------


def test_provider_matches_legacy_loop_for_known_lunar_composition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive a simulator past the 400 K floor, then assert the kernel
    dispatch reproduces the standalone legacy Hertz-Knudsen flux loop
    species-by-species within tolerance."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    # V1c JANAF constants suppress the old 700 C trickle below the
    # legacy loop's reporting floor; 1000 C used to keep this a low-flux
    # parity case while still exercising real species output.
    # 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip
    # exposes the real holdup-derived pO2 (vacuum-floor 1e-9 bar) in
    # HARD_VACUUM atmosphere instead of the pre-flip synthetic
    # conductance-ratio derived floor. Under the new trajectory the
    # 1/sqrt(pO2) suppression factor multiplies P_sat too aggressively
    # at 1000 C and ALL species drop below the legacy loop's
    # 1e-12 kg/hr reporting threshold (empty flux dict). The 2026-06-14
    # dense VapoRock pseudo-Antoine refit also drops the 1200 C fixture
    # below that floor; 1300 C restores species output while staying below
    # recipe operating T of 1600-1700 C. The provider-vs-legacy parity
    # contract is unchanged.
    while sim.melt.temperature_C < 1300.0:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()

    equilibrium = sim._get_equilibrium()
    vapor_pressures_Pa = dict(equilibrium.vapor_pressures_Pa or {})
    if not vapor_pressures_Pa:
        pytest.skip("simulator did not produce any vapor pressures to test")

    legacy_flux = _legacy_hertz_knudsen_flux(sim, vapor_pressures_Pa)
    kernel_flux = dict(sim._calculate_evaporation(equilibrium).species_kg_hr)

    assert legacy_flux, (
        "legacy Hertz-Knudsen reference returned no flux -- the test "
        "fixture is not exercising the path it claims to cover"
    )
    for species, legacy_value in legacy_flux.items():
        kernel_value = kernel_flux.get(species, 0.0)
        tol = max(
            _FLUX_TOLERANCE_ABS_KG_HR,
            _FLUX_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
        )
        assert abs(kernel_value - legacy_value) <= tol, (
            f"flux for {species!r} disagrees: legacy={legacy_value:.6g} "
            f"kg/hr kernel={kernel_value:.6g} kg/hr (tol={tol:.3g})"
        )

    assert set(kernel_flux) <= set(legacy_flux), (
        f"kernel emitted species the legacy loop did not: "
        f"{set(kernel_flux) - set(legacy_flux)}"
    )


# ---------------------------------------------------------------------------
# 6. Shadow-parity smoke run across lunar + Mars + asteroid feedstocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 60.0}),
        ("s_type_asteroid_silicate", None),
    ],
)
def test_shadow_parity_across_short_simulation_run(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Drive the simulator through C0 -> C2A handoff and assert the
    kernel dispatch and the standalone legacy Hertz-Knudsen reference
    agree at every evaporation tick.

    This is the parity gate that justified flipping the EVAPORATION_FLUX
    intent. Stays in the suite as a regression guard against future
    intent flips that touch the same call site.
    """

    setpoints_data = dict(setpoints_data)
    kernel_config = dict(setpoints_data.get("chemistry_kernel", {}) or {})
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints_data["chemistry_kernel"] = kernel_config
    sim = _build_sim(
        feedstock_key,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives_kg,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    worst_delta_kg_hr = 0.0
    while not sim.is_complete() and steps < 60:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

        T_C = sim.melt.temperature_C
        if T_C + 273.15 < 400:
            continue
        equilibrium = sim._get_equilibrium()
        vapor_pressures_Pa = dict(equilibrium.vapor_pressures_Pa or {})
        if not vapor_pressures_Pa:
            continue

        legacy_flux = _legacy_hertz_knudsen_flux(sim, vapor_pressures_Pa)
        kernel_flux = dict(
            sim._calculate_evaporation(equilibrium).species_kg_hr
        )

        for species in set(legacy_flux) | set(kernel_flux):
            legacy_value = float(legacy_flux.get(species, 0.0))
            kernel_value = float(kernel_flux.get(species, 0.0))
            delta = abs(legacy_value - kernel_value)
            tol = max(
                _FLUX_TOLERANCE_ABS_KG_HR,
                _FLUX_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
            )
            worst_delta_kg_hr = max(worst_delta_kg_hr, delta)
            assert delta <= tol, (
                f"parity broke for {species!r} at step {steps} "
                f"(T={T_C:.1f} C, feedstock={feedstock_key}): "
                f"legacy={legacy_value:.6g} kg/hr "
                f"kernel={kernel_value:.6g} kg/hr "
                f"delta={delta:.6g} tol={tol:.6g}"
            )

    assert steps > 0, f"smoke run for {feedstock_key} executed zero steps"
    assert worst_delta_kg_hr <= 1.0, (
        f"worst observed parity delta {worst_delta_kg_hr:.6g} kg/hr is "
        f"suspiciously large for a refactor-only change"
    )


# ---------------------------------------------------------------------------
# 7. W3 (0.5.4): defensive clamp on stir_factor / stir_state dict input
# ---------------------------------------------------------------------------
#
# The canonical sim path through ``simulator.evaporation`` pre-clamps the
# stir_factor before it lands in the IntentRequest. Direct-provider callers
# (ACP probes, ad-hoc dispatch, tests) bypass that path; W3 adds an
# idempotent inner clamp in the provider so the contract holds regardless
# of who built the request.


def _w3_dispatch_with_stir(stir_control) -> float:
    """Helper: dispatch the provider with a custom ``stir_factor`` control
    and return the realised stir factor as observed in the H-K-L flux
    multiplication. We infer the factor by comparing the realised Na flux
    to the laminar baseline (stir=1.0) — H-K-L is strictly linear in
    stir_factor when α/M/T/P_sat are held fixed."""

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "Na2O": 1.0}},
        species_formula_registry={},
    )

    def _flux_for(stir):
        request = IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=view,
            temperature_C=1500.0,
            pressure_bar=1e-6,
            fO2_log=None,
            control_inputs={
                'vapor_pressures_Pa': {'Na': 100.0},
                'overhead_partials_Pa': {},
                'molar_mass_kg_mol': {'Na': 0.023},
                'stoich_by_species': {
                    'Na': {
                        'parent_oxide': 'Na2O',
                        'oxide_per_product_kg': 1.347,
                        'O2_per_product_kg': 0.347,
                    },
                },
                'available_oxide_kg': {'Na': 10.0},
                'melt_surface_area_m2': 0.2,
                'stir_factor': stir,
                'alpha': 0.5,
            },
        )
        result = provider.dispatch(request)
        return float(
            result.diagnostic.get('evaporation_flux_kg_hr', {}).get('Na', 0.0)
        )

    baseline = _flux_for(1.0)
    realised = _flux_for(stir_control)
    if baseline <= 0.0:
        return 0.0
    return realised / baseline


def test_provider_clamps_dict_axial_nan_to_zero():
    """``{"axial": NaN}`` is a degenerate operator-write (or a poisoned
    upstream computation). W3: clamp_stir_factor maps NaN → 0.0, so
    H-K-L flux is halted instead of propagating NaN downstream."""

    realised_ratio = _w3_dispatch_with_stir({"axial": float("nan")})
    # Realised stir = 0.0 → realised flux = 0 = ratio 0.0
    assert realised_ratio == 0.0


def test_provider_clamps_dict_axial_negative_to_zero():
    """Negative stir is unphysical and reads as a halt-evap signal per
    ``clamp_stir_factor`` (the lower bound is 0.0). The provider must
    not silently pass through a negative multiplier — which would
    invert the flux sign and break mass balance."""

    realised_ratio = _w3_dispatch_with_stir({"axial": -5.0})
    assert realised_ratio == 0.0


def test_provider_clamps_dict_axial_over_max_to_ceiling():
    """``{"axial": 1000}`` exceeds the ``MAX_STIR_FACTOR=10.0``
    operator-facing ceiling (melt-flying-out-of-the-pot physical bound).
    Provider must clamp to MAX so the realised flux ratio is 10.0,
    not 1000x the laminar baseline."""

    realised_ratio = _w3_dispatch_with_stir({"axial": 1000.0})
    # H-K-L flux is linear in stir; ratio of (stir=10) to (stir=1) is 10.0.
    assert realised_ratio == pytest.approx(10.0, rel=1e-9)


def test_provider_clamps_scalar_legacy_input_too():
    """Legacy single-axis scalar caller must get the same defensive
    clamp — pre-W3, the scalar branch went straight through
    ``float(...)`` with only a TypeError/ValueError fallback. Now
    NaN/inf/over-MAX all funnel through clamp_stir_factor."""

    # NaN → 0.0
    assert _w3_dispatch_with_stir(float("nan")) == 0.0
    # Over MAX → MAX
    assert _w3_dispatch_with_stir(500.0) == pytest.approx(10.0, rel=1e-9)
    # Negative → 0.0
    assert _w3_dispatch_with_stir(-3.0) == 0.0


def test_provider_clamps_bool_input_to_zero():
    """Codex chunk-review P3: bool is a Python int subclass; a YAML/
    JSON deserialiser that hands ``True`` would otherwise coerce to
    1.0 (laminar baseline) and ``False`` to 0.0 (halt). Both lies
    silently. ``clamp_stir_factor`` rejects bool explicitly, so the
    direct-provider path must too. Pin both values: True → 0.0
    (halt-evap), False → 0.0 (halt-evap), unambiguous audit trail."""

    # Both bool values must hit halt-evap rather than silently
    # coerce to the float values 1.0 / 0.0.
    assert _w3_dispatch_with_stir(True) == 0.0
    assert _w3_dispatch_with_stir(False) == 0.0


def test_provider_canonical_path_is_idempotent_under_clamp():
    """An already-sanitised scalar (e.g., 6.0) must pass through the
    clamp untouched — the canonical sim path through
    ``simulator/evaporation.py::_pack_controls`` pre-clamps, so this
    second clamp is defense in depth, not a behaviour change. The
    realised ratio for stir=6 must be exactly 6.0."""

    assert _w3_dispatch_with_stir(6.0) == pytest.approx(6.0, rel=1e-9)
    # Dict form, valid axial: same idempotency.
    assert _w3_dispatch_with_stir({"axial": 4.0}) == pytest.approx(
        4.0, rel=1e-9
    )
