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
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.chemistry.kernel import (
    ChemistryIntent,
    IntentRequest,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.evaporation import _EVAPORATION_COEFFICIENT_ALPHA
from simulator.melt_backend.base import StubBackend
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_FLUX_TOLERANCE_REL = 1e-9
_FLUX_TOLERANCE_ABS_KG_HR = 1e-9


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


@pytest.fixture(scope="module")
def vapor_pressure_data() -> dict:
    return _load_yaml("vapor_pressures.yaml")


@pytest.fixture(scope="module")
def feedstocks_data() -> dict:
    return _load_yaml("feedstocks.yaml")


@pytest.fixture(scope="module")
def setpoints_data() -> dict:
    return _load_yaml("setpoints.yaml")


def _build_sim(
    feedstock_key: str,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    additives_kg: dict | None = None,
) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend, setpoints_data, feedstocks_data, vapor_pressure_data
    )
    sim.load_batch(feedstock_key, mass_kg=1000.0, additives_kg=additives_kg)
    return sim


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

    alpha = _EVAPORATION_COEFFICIENT_ALPHA
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
        P_ambient_Pa = sim.overhead.composition.get(species, 0.0) * 100.0
        denominator = math.sqrt(2 * math.pi * M_kg_mol * GAS_CONSTANT * T_K)
        J_kg_s_m2 = alpha * (P_sat_Pa - P_ambient_Pa) / denominator
        if J_kg_s_m2 <= 0:
            continue
        rate_kg_hr = (
            J_kg_s_m2
            * sim.melt.melt_surface_area_m2
            * sim.melt.stir_factor
            * 3600.0
        )
        parent_oxide = sp_data.get('parent_oxide', '')
        if not parent_oxide:
            continue
        available_kg = sim.melt.composition_kg.get(parent_oxide, 0.0)
        stoich = sim._evaporation_stoich(species, sp_data)
        max_product_kg = available_kg / stoich['oxide_per_product_kg']
        rate_kg_hr = min(rate_kg_hr, max_product_kg)
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
                'alpha': _EVAPORATION_COEFFICIENT_ALPHA,
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
# 4. Below 400 K, provider returns empty flux dict
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
    while sim.melt.temperature_C < 700.0:
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
