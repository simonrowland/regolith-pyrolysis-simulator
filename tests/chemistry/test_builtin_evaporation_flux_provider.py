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
  series-resistance reference species by species within 1e-9 kg/hr
  (relative + absolute floor).
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

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.chemistry.kernel import (
    ChemistryIntent,
    IntentRequest,
)
from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.accounting import AccountingError
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import GAS_CONSTANT_J_MOL_K, alpha_s
from simulator.core import PyrolysisSimulator
from simulator.evaporation import _load_evaporation_alpha_by_species
from simulator.state import (
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


# shadow-parity simulation runs clip/fail under xdist coscheduling.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

_FLUX_TOLERANCE_REL = 1e-9
_FLUX_TOLERANCE_ABS_KG_HR = 1e-9


def _series_resistance_reference_flux(
    sim: PyrolysisSimulator,
    vapor_pressures_Pa: dict,
) -> dict:
    """Re-invoke the series-resistance source outside ``_calculate_evaporation``.

    NOTE (2026-06-29 review): this calls the SAME production helper
    ``_series_resistance_evaporation_flux_kg_m2_s`` (below), so the parity tests
    that consume it validate CALLER WIRING -- that ``_calculate_evaporation``
    feeds the helper the right per-species args and reproduces its result -- NOT
    that the series-resistance MATH is independently correct. The math's
    first-principles properties (free-molecular limit, alpha_eff<=alpha_i,
    double-count guard, stir saturation, Fuchs-Sutugin transition) are pinned
    independently in ``tests/chemistry/test_evaporation_series_resistance_flux.py``.
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
    for species, P_eq_Pa in vapor_pressures_Pa.items():
        if P_eq_Pa <= 0:
            continue

        sp_data = metals_data.get(species, {})
        if not sp_data:
            sp_data = oxide_vapors_data.get(species, {})

        M_g_mol = sp_data.get('molar_mass_g_mol')
        if M_g_mol is None:
            M_g_mol = MOLAR_MASS.get(species)
        assert M_g_mol is not None, species
        M_kg_mol = M_g_mol / 1000.0
        stoich = sim._evaporation_stoich(species, sp_data)
        P_ambient_Pa = sim.overhead.composition.get(species, 0.0) * 100.0
        alpha = alpha_s(
            species,
            T_K,
            {
                "coefficient_spec": alpha_by_species.get(species, 1.0),
                "allow_unmeasured_alpha_fallback": True,
            },
        )
        kernel_config = dict(sim.setpoints.get("chemistry_kernel", {}) or {})
        series_config = dict(
            kernel_config.get("evaporation_series_resistance", {}) or {}
        )
        carrier_resolver = getattr(sim, "_resolve_condensation_carrier_gas", None)
        carrier_gas = carrier_resolver() if callable(carrier_resolver) else "N2"
        J_kg_s_m2 = _series_resistance_evaporation_flux_kg_m2_s(
            species=species,
            P_eq_pa=P_eq_Pa,
            P_bulk_pa=P_ambient_Pa,
            T_surface_K=T_K,
            molar_mass_kg_mol=M_kg_mol,
            alpha_i=alpha,
            pipe_diameter_m=float(getattr(sim.overhead_model, "pipe_diameter_m", 0.12)),
            overhead_pressure_pa=float(getattr(sim.overhead, "pressure_mbar", 0.0) or 0.0) * 100.0,
            axial_stir_factor=sim.melt.stir_state.axial,
            radial_stir_factor=sim.melt.stir_state.radial,
            carrier_gas=carrier_gas,
            T_gas_K=float(getattr(sim.overhead, "headspace_temperature_K", 0.0) or T_K),
            melt_resistance_enabled=bool(
                series_config.get("melt_resistance_enabled", True)
            ),
            melt_surface_renewal_base_kg_s_m2_pa=float(
                series_config.get("melt_surface_renewal_base_kg_s_m2_pa", 1.0e-4)
            ),
            melt_surface_renewal_source=str(
                series_config.get(
                    "melt_surface_renewal_source",
                    "owner-ratify:melt-side-surface-renewal-v1",
                )
            ),
            gas_resistance_enabled=bool(
                series_config.get("gas_resistance_enabled", True)
            ),
        ).flux_kg_s_m2
        if J_kg_s_m2 <= 0:
            continue
        rate_kg_hr = (
            J_kg_s_m2
            * sim.melt.melt_surface_area_m2
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


def test_provider_attaches_numerator_provenance_and_resistance_shares():
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Na2O": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            'vapor_pressures_Pa': {'Na': 20.0},
            'vapor_pressures_source': {
                'Na': 'builtin_authoritative:backsolved_vaporock_curve_fit',
            },
            'vapor_pressure_numerator_provenance': {
                'Na': {
                    'pressure_kind': 'effective_equilibrium',
                    'P_reference_Antoine_Pa': 200.0,
                    'P_eq_Pa': 20.0,
                    'pO2_bar': 1e-9,
                    'activity_factor': 0.1,
                    'source_label': (
                        'builtin_authoritative:'
                        'backsolved_vaporock_curve_fit'
                    ),
                },
            },
            'overhead_partials_Pa': {'Na': 2.0},
            'molar_mass_kg_mol': {'Na': 0.02298976928},
            'stoich_by_species': {
                'Na': {
                    'parent_oxide': 'Na2O',
                    'oxide_per_product_kg': 1.347,
                    'O2_per_product_kg': 0.347,
                },
            },
            'available_oxide_kg': {'Na': 10.0},
            'melt_surface_area_m2': 1.0,
            'stir_factor': {'axial': 3.0, 'radial': 2.0},
            'alpha': {'Na': 0.13},
            'pO2_bar': 1e-9,
        },
    )

    result = provider.dispatch(request)

    diagnostic = result.diagnostic['evaporation_series_resistance']['Na']
    assert diagnostic['pressure_kind'] == 'effective_equilibrium'
    assert diagnostic['P_reference_Antoine_Pa'] == pytest.approx(200.0)
    assert diagnostic['P_eq_Pa'] == pytest.approx(20.0)
    assert diagnostic['P_bulk_Pa'] == pytest.approx(2.0)
    assert diagnostic['pO2_bar'] == pytest.approx(1e-9)
    assert diagnostic['activity_factor'] == pytest.approx(0.1)
    assert diagnostic['source_label'] == (
        'builtin_authoritative:backsolved_vaporock_curve_fit'
    )
    share_sum = (
        diagnostic['R_interface_fraction']
        + diagnostic['R_gas_fraction']
        + diagnostic['R_melt_fraction']
    )
    assert share_sum == pytest.approx(1.0, rel=1e-12)
    assert diagnostic['limiting_resistance_label'] in {'interface', 'gas', 'melt'}
    assert diagnostic['alpha_eff'] == diagnostic['alpha_effective']
    assert diagnostic['Kn'] == diagnostic['knudsen_number']


def test_evaporation_aux_fails_loud_without_molar_mass_metadata(
    feedstocks_data, setpoints_data
):
    vapor_pressure_data = {
        "metals": {
            "Mystery": {
                "parent_oxide": "FeO",
                "stoich_oxide_per_vapor": 1.0,
                "stoich_O2_per_vapor": 0.0,
            },
        },
        "oxide_vapors": {},
    }
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    with pytest.raises(AccountingError, match="Mystery.*molar_mass_g_mol"):
        sim._build_evaporation_aux_maps({"Mystery": 1.0})


def test_evaporation_aux_uses_atom_ledger_for_parent_oxide_availability(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    _, _, available_oxide_kg = sim._build_evaporation_aux_maps({"Na": 1.0})

    assert available_oxide_kg["Na"] == pytest.approx(
        sim.atom_ledger.kg_by_account("process.cleaned_melt")["Na2O"]
    )


def test_evaporation_aux_includes_spent_reductant_residue_projection_domain(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external(
        "process.raw_feedstock",
        {"Na2O": 0.375},
        source="evaporation parity regression seed",
    )
    sim.atom_ledger.move(
        "evaporation_parity_cleaned_melt_seed",
        "process.raw_feedstock",
        "process.cleaned_melt",
        {"Na2O": 0.25},
        reason="evaporation parity regression seed",
    )
    sim.atom_ledger.move(
        "evaporation_parity_spent_residue_seed",
        "process.raw_feedstock",
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        {"Na2O": 0.125},
        reason="evaporation parity regression seed",
    )
    sim._project_cleaned_melt_from_atom_ledger()

    _, _, available_oxide_kg = sim._build_evaporation_aux_maps({"Na": 1.0})
    cleaned_melt_na2o_kg = sim.atom_ledger.kg_by_account(
        "process.cleaned_melt"
    )["Na2O"]
    spent_residue_na2o_kg = sim.atom_ledger.kg_by_account(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT
    )["Na2O"]

    assert available_oxide_kg["Na"] == pytest.approx(
        cleaned_melt_na2o_kg + spent_residue_na2o_kg
    )


def test_evaporation_aux_rejects_stale_melt_projection(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.composition_kg["Na2O"] += 1e-6

    with pytest.raises(AccountingError, match="projection stale.*Na2O"):
        sim._build_evaporation_aux_maps({"Na": 1.0})


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
            'evaporation_series_resistance': {
                'gas_resistance_enabled': False,
                'melt_resistance_enabled': False,
            },
        },
    )

    result = provider.dispatch(request)

    flux_kg_hr = result.diagnostic['evaporation_flux_kg_hr']['Si']
    expected_kg_hr = (
        0.27728678068938384
        * math.sqrt(
            0.02809
            / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * (1500.0 + 273.15))
        )
        * 3600.0
    )
    assert flux_kg_hr == pytest.approx(expected_kg_hr, rel=1e-12)


def test_provider_skips_species_without_grounded_molar_mass():
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Na2O": 10.0}},
        species_formula_registry={},
    )
    base_controls = {
        'overhead_partials_Pa': {},
        'molar_mass_kg_mol': {'Na': 0.023},
        'stoich_by_species': {
            'Na': {
                'parent_oxide': 'Na2O',
                'oxide_per_product_kg': 1.347,
                'O2_per_product_kg': 0.347,
            },
            'Unobtainium': {
                'parent_oxide': 'Na2O',
                'oxide_per_product_kg': 1.0,
                'O2_per_product_kg': 0.0,
            },
        },
        'available_oxide_kg': {'Na': 10.0, 'Unobtainium': 10.0},
        'melt_surface_area_m2': 0.2,
        'stir_factor': 1.0,
        'alpha': {'Na': 0.5, 'Unobtainium': 0.5},
    }

    normal_request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            **base_controls,
            'vapor_pressures_Pa': {'Na': 100.0},
        },
    )
    mixed_request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            **base_controls,
            'vapor_pressures_Pa': {'Na': 100.0, 'Unobtainium': 100.0},
        },
    )

    normal_result = provider.dispatch(normal_request)
    result = provider.dispatch(mixed_request)

    flux_kg_hr = result.diagnostic['evaporation_flux_kg_hr']
    assert 'Unobtainium' not in flux_kg_hr
    assert flux_kg_hr['Na'] == pytest.approx(
        normal_result.diagnostic['evaporation_flux_kg_hr']['Na'], rel=0, abs=0
    )
    assert result.diagnostic['missing_molar_mass']['Unobtainium'] == {
        "policy": "fail_loud_missing_molar_mass",
        "data_file": "data/vapor_pressures.yaml",
        "control": "molar_mass_kg_mol",
        "P_eq_Pa": 100.0,
    }
    assert any(
        "Unobtainium" in warning and "data/vapor_pressures.yaml" in warning
        for warning in result.warnings
    )


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
# 5. Provider math matches the series-resistance reference on a known case
# ---------------------------------------------------------------------------


def test_provider_matches_legacy_loop_for_known_lunar_composition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Drive a simulator past the 400 K floor, then assert the kernel
    dispatch reproduces the standalone series-resistance flux reference
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
    # 1/sqrt(pO2) suppression factor multiplies P_eq too aggressively
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

    reference_flux = _series_resistance_reference_flux(sim, vapor_pressures_Pa)
    kernel_flux = dict(sim._calculate_evaporation(equilibrium).species_kg_hr)

    assert reference_flux, (
        "series-resistance reference returned no flux -- the test "
        "fixture is not exercising the path it claims to cover"
    )
    for species, legacy_value in reference_flux.items():
        kernel_value = kernel_flux.get(species, 0.0)
        tol = max(
            _FLUX_TOLERANCE_ABS_KG_HR,
            _FLUX_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
        )
        assert abs(kernel_value - legacy_value) <= tol, (
            f"flux for {species!r} disagrees: legacy={legacy_value:.6g} "
            f"kg/hr kernel={kernel_value:.6g} kg/hr (tol={tol:.3g})"
        )

    assert set(kernel_flux) <= set(reference_flux), (
        f"kernel emitted species the reference did not: "
        f"{set(kernel_flux) - set(reference_flux)}"
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
    kernel dispatch and the standalone series-resistance reference agree
    at every evaporation tick.

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

        reference_flux = _series_resistance_reference_flux(sim, vapor_pressures_Pa)
        kernel_flux = dict(
            sim._calculate_evaporation(equilibrium).species_kg_hr
        )

        for species in set(reference_flux) | set(kernel_flux):
            legacy_value = float(reference_flux.get(species, 0.0))
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


def _w3_dispatch_with_stir(stir_control) -> dict:
    """Dispatch with custom stir control and return series diagnostics."""

    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "Na2O": 1.0}},
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
            'stir_factor': stir_control,
            'alpha': 0.5,
        },
    )
    result = provider.dispatch(request)
    return result.diagnostic['evaporation_series_resistance']['Na']


def test_provider_clamps_dict_axial_nan_to_zero():
    """``{"axial": NaN}`` maps to no melt-side stir enhancement."""

    diagnostic = _w3_dispatch_with_stir({"axial": float("nan")})
    assert diagnostic["axial_stir_clamped"] is True
    assert diagnostic["axial_stir_applied"] == 0.0


def test_provider_clamps_dict_axial_negative_to_zero():
    """Negative stir is unphysical and reads as a halt-evap signal per
    ``clamp_stir_factor`` (the lower bound is 0.0). The provider must
    not silently pass through a negative multiplier — which would
    invert the flux sign and break mass balance."""

    diagnostic = _w3_dispatch_with_stir({"axial": -5.0})
    assert diagnostic["axial_stir_clamped"] is True
    assert diagnostic["axial_stir_applied"] == 0.0


def test_provider_clamps_dict_axial_over_max_to_ceiling():
    """``{"axial": 1000}`` saturates at the operator ceiling."""

    diagnostic = _w3_dispatch_with_stir({"axial": 1000.0})
    max_diagnostic = _w3_dispatch_with_stir({"axial": 10.0})
    assert diagnostic["axial_stir_clamped"] is True
    assert diagnostic["axial_stir_applied"] == pytest.approx(10.0)
    assert diagnostic["flux_kg_s_m2"] == pytest.approx(
        max_diagnostic["flux_kg_s_m2"], rel=1e-12
    )


def test_provider_clamps_scalar_legacy_input_too():
    """Legacy single-axis scalar caller must get the same defensive
    clamp — pre-W3, the scalar branch went straight through
    ``float(...)`` with only a TypeError/ValueError fallback. Now
    NaN/inf/over-MAX all funnel through clamp_stir_factor."""

    assert _w3_dispatch_with_stir(float("nan"))["axial_stir_applied"] == 0.0
    assert _w3_dispatch_with_stir(500.0)["axial_stir_applied"] == pytest.approx(10.0)
    assert _w3_dispatch_with_stir(-3.0)["axial_stir_applied"] == 0.0


def test_provider_clamps_bool_input_to_zero():
    """Codex chunk-review P3: bool is a Python int subclass; a YAML/
    JSON deserialiser that hands ``True`` would otherwise coerce to
    1.0 (laminar baseline) and ``False`` to 0.0 (halt). Both lies
    silently. ``clamp_stir_factor`` rejects bool explicitly, so the
    direct-provider path must too. Pin both values: True → 0.0
    (halt-evap), False → 0.0 (halt-evap), unambiguous audit trail."""

    assert _w3_dispatch_with_stir(True)["axial_stir_applied"] == 0.0
    assert _w3_dispatch_with_stir(False)["axial_stir_applied"] == 0.0


def test_provider_canonical_path_is_idempotent_under_clamp():
    """An already-sanitised scalar (e.g., 6.0) must pass through the
    clamp untouched — the canonical sim path through
    ``simulator/evaporation.py::_pack_controls`` pre-clamps, so this
    second clamp is defense in depth, not a behaviour change. The
    applied axial value for stir=6 must stay exactly 6.0."""

    assert _w3_dispatch_with_stir(6.0)["axial_stir_applied"] == pytest.approx(6.0)
    assert _w3_dispatch_with_stir({"axial": 4.0})[
        "axial_stir_applied"
    ] == pytest.approx(4.0)
