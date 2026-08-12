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
from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _series_pressure_provenance_diagnostic,
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
from simulator.evaporation import (
    _load_evaporation_alpha_by_species,
    _pre_rg_effective_pressure_source,
)
from simulator.state import (
    MOLAR_MASS,
    CampaignPhase,
    DecisionType,
)
from tests.chemistry.conftest import _build_sim


# shadow-parity simulation runs clip/fail under xdist coscheduling.
pytestmark = [pytest.mark.serial]

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
    double-count guard, stir saturation, and vacuum gas-film limit) are pinned
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
        gas_resistance_enabled = bool(
            series_config.get("gas_resistance_enabled", True)
        )
        P_ambient_Pa = (
            sim._evaporation_bulk_partial_pressure_pa(species)
            if gas_resistance_enabled
            else 0.0
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
                series_config.get("melt_resistance_enabled", False)
            ),
            melt_surface_renewal_base_kg_s_m2_pa=float(
                series_config.get("melt_surface_renewal_base_kg_s_m2_pa", 0.0)
            ),
            melt_surface_renewal_source=str(
                series_config.get(
                    "melt_surface_renewal_source",
                    "disabled:missing-species-state-dependent-melt-transfer-inputs",
                )
            ),
            gas_resistance_enabled=gas_resistance_enabled,
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


@pytest.mark.xdist_group("serial")
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


@pytest.mark.xdist_group("serial")
def test_provider_declares_only_cleaned_melt_account():
    provider = BuiltinEvaporationFluxProvider()
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({"process.cleaned_melt"})


# ---------------------------------------------------------------------------
# 2. Kernel filter scopes the account view
# ---------------------------------------------------------------------------


@pytest.mark.xdist_group("serial")
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
        "process.metal_phase", {"Fe": 0.5}, source="test seed",
        material_origin="feedstock",
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
                'overhead_pressure_pa': 0.0,
                'vapour_batch_flux_pressures_Pa': {},
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


@pytest.mark.xdist_group("serial")
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
            'overhead_pressure_pa': 0.0,
            'vapour_batch_flux_pressures_Pa': {'Na': 100.0},
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


@pytest.mark.xdist_group("serial")
def test_evaporation_caller_counts_cro2_mn_alpha_fallback_engagement(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1500.0
    sim._dispatch_only = lambda intent, *, control_inputs: SimpleNamespace(
        status="ok",
        diagnostic={
            "evaporation_flux_kg_hr": {},
            "unmeasured_alpha_fallback_species": ["CrO2", "Mn"],
        },
    )
    # t-383: Na standard_reaction_term refuses pre-RG eligibility without
    # NaO0.5 activity evidence. This counter test only needs a flux-eligible
    # seam claim so the mocked EVAPORATION_FLUX path can record alpha fallbacks.
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 1.0},
        vapor_pressures_source={},
        activity_coefficients={},
        diagnostics={},
        liquid_fraction=1.0,
    )

    sim._calculate_evaporation(equilibrium)

    summary = sim._degraded_path_engagement_summary()
    assert summary["unmeasured_alpha_evaporation_fallback"]["total_count"] == 2


@pytest.mark.xdist_group("serial")
def test_k_at_1650c_drives_flux_with_extrapolation_status(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    setpoints_data = dict(setpoints_data)
    kernel_config = dict(setpoints_data.get("chemistry_kernel", {}) or {})
    series_config = dict(
        kernel_config.get("evaporation_series_resistance", {}) or {}
    )
    series_config["gas_resistance_enabled"] = False
    kernel_config["evaporation_series_resistance"] = series_config
    setpoints_data["chemistry_kernel"] = kernel_config
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1650.0

    equilibrium = sim._get_equilibrium()
    effective_source = _pre_rg_effective_pressure_source(
        sim.vapor_pressures, equilibrium
    )
    flux = sim._calculate_evaporation(equilibrium)

    assert flux.species_kg_hr.get("K", 0.0) > 0.0
    answer = sim._last_vapour_batch.channel("K")
    continuation_pa = answer.pressure.pa
    seam_pa = effective_source.pressure_pa("K")
    assert seam_pa is not None and seam_pa > 0.0
    assert seam_pa != pytest.approx(continuation_pa, rel=1.0e-6)
    assert answer.extra.get("out_of_range") is True
    assert answer.verdict_status == "status_bearing_non_authoritative"
    assert answer.certification_ceiling == "never"
    overlay = sim._last_vapour_batch_flux_overlay
    assert overlay["selected_runtime_pa_by_species"]["K"] == pytest.approx(
        seam_pa, rel=0.0, abs=0.0
    )
    assert (
        overlay["selected_pressure_source_by_species"]["K"]
        == effective_source.source_id
    )
    assert "K" in overlay["extrapolated_flux_species"]
    assert "K" not in overlay["catalog_continuation_flux_species"]
    expected_flux = _series_resistance_reference_flux(
        sim,
        {"K": seam_pa},
    )
    assert flux.species_kg_hr["K"] == pytest.approx(expected_flux["K"])
    degraded = sim._degraded_path_engagement_summary()
    assert degraded["vapour_pressure_extrapolation"]["total_count"] >= 1


@pytest.mark.xdist_group("serial")
def test_unmeasured_alpha_fallback_allowlist_is_scoped_and_loud():
    allowed = _w3_result_with_controls(
        1.0,
        alpha={},
        allow_unmeasured_alpha_fallback=True,
        unmeasured_alpha_fallback_species=["Na"],
    )

    assert allowed.status == "ok"
    assert allowed.diagnostic["unmeasured_alpha_fallback_species"] == ["Na"]
    assert any(
        "WARNING" in warning
        and "alpha=1.0 prototype fallback" in warning
        and "Na" in warning
        for warning in allowed.warnings
    )

    refused = _w3_result_with_controls(
        1.0,
        alpha={},
        allow_unmeasured_alpha_fallback=True,
        unmeasured_alpha_fallback_species=["CrO2", "Mn"],
    )

    assert refused.status == "ok"
    assert refused.diagnostic["evaporation_flux_kg_hr"] == {}
    assert set(refused.diagnostic["missing_alpha"]) == {"Na"}
    assert refused.diagnostic["species_refusals"]["Na"]["status"] == "refused"
    assert (
        refused.diagnostic["species_refusals"]["Na"]["disposition"]
        == "retained_in_condensed_parent_oxide"
    )
    assert "unmeasured_alpha_fallback_species" not in refused.diagnostic


@pytest.mark.xdist_group("serial")
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
            'overhead_pressure_pa': 0.0,
            'vapour_batch_flux_pressures_Pa': {'Na': 20.0},
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


@pytest.mark.xdist_group("serial")
def test_gas_rail_provenance_does_not_fabricate_antoine_reference():
    diagnostic = _series_pressure_provenance_diagnostic(
        species="K",
        P_eq_Pa=12.0,
        P_bulk_Pa=2.0,
        pressure_provenance_by_species={
            "K": {
                "pressure_kind": "effective_equilibrium",
                "pressure_rail": "gas_fugacity",
                "P_eq_Pa": 12.0,
                "source_label": "builtin_authoritative:gas_standard_fugacity",
            }
        },
        vapor_pressure_sources={},
        vapor_pressure_activities={},
        pO2_bar=1e-9,
    )

    assert diagnostic["P_eq_Pa"] == pytest.approx(12.0)
    assert "P_reference_Antoine_Pa" not in diagnostic


@pytest.mark.xdist_group("serial")
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


@pytest.mark.xdist_group("serial")
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


@pytest.mark.xdist_group("serial")
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
        material_origin="feedstock",
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


@pytest.mark.xdist_group("serial")
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


@pytest.mark.xdist_group("serial")
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
            'overhead_pressure_pa': 0.0,
            'vapour_batch_flux_pressures_Pa': {'Si': 0.27728678068938384},
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


@pytest.mark.xdist_group("serial")
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
            'overhead_pressure_pa': 0.0,
            **base_controls,
            'vapour_batch_flux_pressures_Pa': {'Na': 100.0},
        },
    )
    mixed_request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={
            'overhead_pressure_pa': 0.0,
            **base_controls,
            'vapour_batch_flux_pressures_Pa': {'Na': 100.0, 'Unobtainium': 100.0},
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


@pytest.mark.xdist_group("serial")
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
            'overhead_pressure_pa': 0.0,
            'vapour_batch_flux_pressures_Pa': {'Na': 1e6},
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
# 5. Caller wiring matches the shared series-resistance helper on a known case
# ---------------------------------------------------------------------------


@pytest.mark.xdist_group("serial")
def test_evaporation_caller_wiring_matches_shared_helper_for_lunar_case(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Wiring-only parity for batch-eligible species between caller and helper.

    Independent flux math is pinned in
    ``tests/chemistry/test_evaporation_series_resistance_flux.py``.
    """

    setpoints_data = dict(setpoints_data)
    kernel_config = dict(setpoints_data.get("chemistry_kernel", {}) or {})
    series_config = dict(
        kernel_config.get("evaporation_series_resistance", {}) or {}
    )
    # This parity fixture exercises caller wiring. F-230 separately pins that
    # finite-pressure transport refuses species without Chapman-Enskog data.
    series_config["gas_resistance_enabled"] = False
    kernel_config["evaporation_series_resistance"] = series_config
    setpoints_data["chemistry_kernel"] = kernel_config
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
    assert vapor_pressures_Pa, (
        "simulator produced no vapor pressures; provider parity coverage "
        "would be vacuous"
    )

    reference_flux = _series_resistance_reference_flux(sim, vapor_pressures_Pa)
    kernel_flux = dict(sim._calculate_evaporation(equilibrium).species_kg_hr)
    refusals = sim._last_vapour_batch_report["refusals_by_species"]
    refused_reference_species = set(reference_flux) & set(refusals)

    assert reference_flux, (
        "series-resistance reference returned no flux -- the test "
        "fixture is not exercising the path it claims to cover"
    )
    # MC-4A activates CrO2 with a numeric, explicitly non-authoritative alpha
    # proxy, so every reference species is now batch-eligible at this C0 point.
    assert refused_reference_species == set()
    for species, legacy_value in reference_flux.items():
        if species in refused_reference_species:
            continue
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
# gate-2: short-run shadow parity exceeded 300 s under 3-chain slot contention.
@pytest.mark.xdist_group("magemin_fullrun_a")
@pytest.mark.timeout(900)
def test_evaporation_caller_wiring_matches_shared_helper_across_short_run(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Wiring-only parity across each evaporation tick in a short run.

    This is the parity gate that justified flipping the EVAPORATION_FLUX
    intent. Stays in the suite as a regression guard against future
    intent flips that touch the same call site. Independent flux math is
    pinned in ``tests/chemistry/test_evaporation_series_resistance_flux.py``.
    """

    setpoints_data = dict(setpoints_data)
    kernel_config = dict(setpoints_data.get("chemistry_kernel", {}) or {})
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    series_config = dict(
        kernel_config.get("evaporation_series_resistance", {}) or {}
    )
    series_config["gas_resistance_enabled"] = False
    kernel_config["evaporation_series_resistance"] = series_config
    setpoints_data["chemistry_kernel"] = kernel_config
    sim = _build_sim(
        feedstock_key,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives_kg,
    )
    stage0_only_species = {
        species_id
        for section_name in ("metals", "oxide_vapors")
        for species_id, row in (
            vapor_pressure_data.get(section_name, {}) or {}
        ).items()
        if str((row or {}).get("hot_train_applicability") or "") == "stage0_only"
    }
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    worst_delta_kg_hr = 0.0
    refused_reference_species_seen: set[str] = set()
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
        refusals = sim._last_vapour_batch_report["refusals_by_species"]
        refused_reference_species = set(reference_flux) & set(refusals)
        # ce14fd3 (VR-11; DESIGN-REV5 §1.2/§7.4), later pinned as b-114
        # in 1a6ad25, made batch eligibility authoritative.
        # 2026-08-05 phosphorus carrier activation 7e6bebc adds a C0b cleanup
        # stage whose declared predicate admits only P2O5-sourced carriers. The
        # legacy math helper has no stage predicate, so typed non-P refusals are
        # outside wiring parity. MC-4A makes CrO2 executable, so at C0b it follows
        # that same typed predicate refusal instead of missing a channel contract.
        for species in refused_reference_species:
            refusal = refusals[species]
            assert refusal["refusal_code"] == "inapplicable_by_declared_predicate"
            assert "c0b_p_cleanup admits only P2O5-sourced carrier rules" in (
                refusal["extra"]["detail"]
            )
        refused_reference_species_seen.update(refused_reference_species)

        for species in set(reference_flux) | set(kernel_flux):
            if species in refused_reference_species:
                continue
            legacy_value = float(reference_flux.get(species, 0.0))
            kernel_value = float(kernel_flux.get(species, 0.0))
            if species in stage0_only_species:
                # The shared legacy helper has no process-stage predicate;
                # _calculate_evaporation dispatches the hot-train request, whose
                # batch eligibility may suppress or floor-limit Stage-0-only
                # phosphorus carriers. MC-4b makes P2O5(g) large enough at
                # 950 C for this old latent mismatch to exceed the numerical
                # tolerance, so exclude the declared stage boundary from parity.
                assert math.isfinite(kernel_value) and kernel_value >= 0.0
                continue
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
    if feedstock_key in {"lunar_mare_low_ti", "s_type_asteroid_silicate"}:
        assert "CrO2" in refused_reference_species_seen
    assert worst_delta_kg_hr <= 1.0, (
        f"worst observed parity delta {worst_delta_kg_hr:.6g} kg/hr is "
        f"suspiciously large for a refactor-only change"
    )


# ---------------------------------------------------------------------------
# 7. Stir-factor validation and bounded feasible adjustment
# ---------------------------------------------------------------------------
#
# Exact zero is a valid halt and values above the feasible maximum clamp to the
# ceiling. Negative, non-finite, and boolean inputs carry no physical state and
# must refuse rather than masquerade as a valid zero-stir request.


def _w3_result_with_controls(stir_control, **control_overrides):
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "Na2O": 1.0}},
        species_formula_registry={},
    )

    controls = {
        'vapour_batch_flux_pressures_Pa': {'Na': 100.0},
        'overhead_partials_Pa': {},
        # True vacuum: Kn domain refusal is for nonzero overhead outside
        # viscous Poiseuille validity; HKL unit tests use P=0 upper-bound path.
        'overhead_pressure_pa': 0.0,
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
    }
    controls.update(control_overrides)
    return provider.dispatch(IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs=controls,
    ))


def _w3_dispatch_with_stir(stir_control) -> dict:
    """Dispatch with custom stir control and return series diagnostics."""

    result = _w3_result_with_controls(stir_control)
    return result.diagnostic['evaporation_series_resistance']['Na']


@pytest.mark.parametrize("area", [-1.0, float("nan"), float("inf"), "invalid"])
@pytest.mark.xdist_group("serial")
def test_provider_refuses_invalid_melt_surface_area(area):
    result = _w3_result_with_controls(1.0, melt_surface_area_m2=area)

    assert result.status == "refused"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    assert result.diagnostic["reason"] == "invalid_melt_surface_area_m2"


@pytest.mark.xdist_group("serial")
def test_provider_accepts_zero_melt_surface_area_as_valid_halt():
    result = _w3_result_with_controls(1.0, melt_surface_area_m2=0.0)

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}


@pytest.mark.xdist_group("serial")
def test_provider_zero_axial_stir_keeps_hkl_upper_bound_without_x6_multiplier():
    result = _w3_result_with_controls({"axial": 0.0})

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    assert result.diagnostic["authority_class"] == "upper-bound"
    assert result.diagnostic["authority_reason"] == (
        "missing-species-state-dependent-melt-transfer-inputs"
    )
    diagnostic = result.diagnostic["evaporation_series_resistance"]["Na"]
    assert diagnostic["r_melt"] == 0.0
    assert diagnostic["melt_resistance_enabled"] is False
    assert diagnostic["authority_class"] == "upper-bound"
    assert diagnostic["authority_reason"] == (
        "missing-species-state-dependent-melt-transfer-inputs"
    )


@pytest.mark.xdist_group("serial")
def test_provider_refuses_universal_melt_renewal_model():
    result = _w3_result_with_controls(
        1.0,
        evaporation_series_resistance={
            "melt_resistance_enabled": True,
            "melt_surface_renewal_base_kg_s_m2_pa": 1.0e-4,
        },
    )

    assert result.status == "refused"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    assert result.diagnostic["reason"] == "uncertified_melt_resistance_model"


@pytest.mark.xdist_group("serial")
def test_provider_refuses_transitional_kn_domain_without_fabricating_zero_flux():
    # Kn≈0.1 at T=2023 K, D=0.12 m, P≈3.63 Pa — transitional. Viscous
    # Poiseuille P_bulk is out of domain: flux is not evaluated, not zero.
    # Not a Kn safety/coating gate. Free-molecular + viscous paths remain.
    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=3.632,
        pipe_diameter_m=0.12,
        gas_temperature_K=2023.15,
    )

    assert result.status == "refused"
    assert result.diagnostic["reason"] == "viscous_p_bulk_transport_out_of_domain"
    assert result.diagnostic["evaporation_flux_status"] == "not_evaluated"
    assert result.diagnostic["evaporation_flux_kg_hr"] is None
    assert result.diagnostic["affected_species"] == ("Na",)
    assert result.diagnostic["ledger_yields_authorized"] is False
    assert result.diagnostic["authority_class"] == "diagnostic-limited"
    assert result.diagnostic["p_bulk_transport_domain"] == (
        "out_of_domain_transitional"
    )
    assert result.diagnostic["knudsen_number"] > 0.01
    assert result.diagnostic["knudsen_number"] < 10.0
    assert "transport_model_validity_domain" in result.diagnostic["framing"]
    assert any(
        "viscous_p_bulk_transport_out_of_domain" in w for w in result.warnings
    )

    # Explicit hard-refuse helper uses the same typed refusal.
    from simulator.evaporation import (
        EvaporationFluxRefusal,
        refuse_viscous_p_bulk_out_of_domain,
    )
    import pytest

    with pytest.raises(EvaporationFluxRefusal) as ei:
        refuse_viscous_p_bulk_out_of_domain(
            knudsen_number=result.diagnostic["knudsen_number"],
            overhead_pressure_pa=3.632,
            pipe_diameter_m=0.12,
            gas_temperature_K=2023.15,
        )
    assert ei.value.reason == "viscous_p_bulk_transport_out_of_domain"


@pytest.mark.xdist_group("serial")
def test_transitional_refusal_reports_actual_and_commanded_pressure():
    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=3.632,
        commanded_pressure_pa=0.0,
        pipe_diameter_m=0.12,
        gas_temperature_K=2023.15,
    )

    assert result.status == "refused"
    assert result.diagnostic["overhead_pressure_pa"] == pytest.approx(3.632)
    assert result.diagnostic["commanded_pressure_pa"] == 0.0
    assert result.diagnostic["evaporation_flux_status"] == "not_evaluated"
    assert result.diagnostic["evaporation_flux_kg_hr"] is None


@pytest.mark.parametrize(
    ("control_overrides", "expected_flux_species"),
    (
        ({"vapour_batch_flux_pressures_Pa": {}}, None),
        ({"vapour_batch_flux_pressures_Pa": {"Na": 0.0}}, None),
        ({"melt_surface_area_m2": 0.0}, None),
        ({"available_oxide_kg": {"Na": 0.0}}, None),
        ({"overhead_partials_Pa": {"Na": 200.0}}, None),
        (
            {
                "vapour_batch_flux_pressures_Pa": {"Na": 3.0},
                "overhead_partials_Pa": {"Na": 3.0 - 1.0e-13},
            },
            None,
        ),
        ({"alpha": 0.0}, None),
        ({"alpha": {"Na": 0.0}}, None),
        ({"alpha": 1.0e-20}, None),
        ({"pipe_diameter_m": 0.0}, None),
        ({"hkl_upper_bound_transport_species": ("Na",)}, "Na"),
    ),
    ids=(
        "empty_batch_map",
        "nonpositive_equilibrium_pressure",
        "zero_surface_area",
        "zero_inventory",
        "nonpositive_pressure_drive",
        "de_minimis_positive_pressure_drive",
        "zero_scalar_accommodation_coefficient",
        "zero_species_accommodation_coefficient",
        "de_minimis_hkl_upper_bound",
        "zero_pipe_diameter",
        "explicit_species_hkl_upper_bound",
    ),
)
@pytest.mark.xdist_group("serial")
def test_transitional_domain_preserves_proven_zero_and_explicit_hkl_paths(
    control_overrides,
    expected_flux_species,
):
    controls = {
        "overhead_pressure_pa": 3.632,
        "pipe_diameter_m": 0.12,
        "gas_temperature_K": 2023.15,
        **control_overrides,
    }
    result = _w3_result_with_controls(1.0, **controls)

    assert result.status == "ok"
    flux = result.diagnostic["evaporation_flux_kg_hr"]
    if expected_flux_species is None:
        assert flux == {}
    else:
        assert flux[expected_flux_species] > 0.0


@pytest.mark.xdist_group("serial")
def test_provider_transitional_hkl_upper_bound_ignores_viscous_p_bulk():
    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=3.632,
        overhead_partials_Pa={"Na": 200.0},
        evaporation_series_resistance={"gas_resistance_enabled": False},
    )

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Na"] > 0.0
    diagnostic = result.diagnostic["evaporation_series_resistance"]["Na"]
    assert diagnostic["r_gas"] == 0.0
    assert diagnostic["P_bulk_Pa"] == 0.0
    assert diagnostic["P_eq_Pa"] == pytest.approx(100.0)


@pytest.mark.xdist_group("serial")
def test_provider_refuses_missing_species_transport_parameters():
    result = _w3_result_with_controls(
        1.0,
        overhead_pressure_pa=1000.0,
        vapour_batch_flux_pressures_Pa={"Si": 100.0},
        molar_mass_kg_mol={"Si": 0.028085},
        stoich_by_species={
            "Si": {
                "parent_oxide": "SiO2",
                "oxide_per_product_kg": 2.139,
                "O2_per_product_kg": 1.139,
            }
        },
        available_oxide_kg={"Si": 10.0},
        alpha={"Si": 0.5},
    )

    assert result.status == "unavailable"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    missing = result.diagnostic["missing_transport_parameters"]["Si"]
    assert missing["policy"] == "fail_loud_missing_transport_parameters"
    assert "missing Chapman-Enskog" in missing["reason"]
    # Warnings must be a 1-tuple of the full message — a bare string here
    # would iterate per-character into the operator warning rail.
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith(
        "missing Chapman-Enskog transport parameters for sampled species:"
    )


@pytest.mark.xdist_group("serial")
def test_provider_keeps_nonempty_computable_transport_set_when_flux_is_zero():
    # Free-molecular / vacuum path (overhead_pressure_pa default 0): continuum
    # gas film is off, so Chapman-Enskog parameters are not required. Si is
    # therefore computable alongside Na (pre-Bug-B Fuchs film required CE for
    # every species and excluded Si). Continuum CE failure is covered by
    # test_provider_refuses_missing_species_transport_parameters at 1000 Pa.
    result = _w3_result_with_controls(
        1.0,
        melt_surface_area_m2=0.0,
        vapour_batch_flux_pressures_Pa={"Na": 100.0, "Si": 100.0},
        molar_mass_kg_mol={"Na": 0.023, "Si": 0.028085},
        stoich_by_species={
            "Na": {
                "parent_oxide": "Na2O",
                "oxide_per_product_kg": 1.347,
                "O2_per_product_kg": 0.347,
            },
            "Si": {
                "parent_oxide": "SiO2",
                "oxide_per_product_kg": 2.139,
                "O2_per_product_kg": 1.139,
            },
        },
        available_oxide_kg={"Na": 10.0, "Si": 10.0},
        alpha={"Na": 0.5, "Si": 0.5},
    )

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"] == {}
    assert set(result.diagnostic["evaporation_series_resistance"]) == {"Na", "Si"}
    assert "missing_transport_parameters" not in result.diagnostic
    assert result.diagnostic["authority_class"] == "upper-bound"
    assert result.warnings == ()


@pytest.mark.xdist_group("serial")
def test_provider_refuses_dict_axial_nan():
    result = _w3_result_with_controls({"axial": float("nan")})

    assert result.status == "refused"
    assert result.diagnostic["reason"] == "invalid_stir_factor"


@pytest.mark.xdist_group("serial")
def test_provider_refuses_dict_axial_negative():
    result = _w3_result_with_controls({"axial": -5.0})

    assert result.status == "refused"
    assert result.diagnostic["reason"] == "invalid_stir_factor"


@pytest.mark.xdist_group("serial")
def test_provider_clamps_dict_axial_over_max_to_ceiling():
    """``{"axial": 1000}`` saturates at the operator ceiling."""

    diagnostic = _w3_dispatch_with_stir({"axial": 1000.0})
    max_diagnostic = _w3_dispatch_with_stir({"axial": 10.0})
    assert diagnostic["axial_stir_clamped"] is True
    assert diagnostic["axial_stir_applied"] == pytest.approx(10.0)
    assert diagnostic["flux_kg_s_m2"] == pytest.approx(
        max_diagnostic["flux_kg_s_m2"], rel=1e-12
    )


@pytest.mark.xdist_group("serial")
def test_provider_validates_scalar_legacy_input_too():
    assert _w3_dispatch_with_stir(500.0)["axial_stir_applied"] == pytest.approx(10.0)
    for invalid in (float("nan"), float("inf"), -3.0):
        result = _w3_result_with_controls(invalid)
        assert result.status == "refused"
        assert result.diagnostic["reason"] == "invalid_stir_factor"


@pytest.mark.xdist_group("serial")
def test_provider_refuses_bool_input():
    for invalid in (True, False):
        result = _w3_result_with_controls(invalid)
        assert result.status == "refused"
        assert result.diagnostic["reason"] == "invalid_stir_factor"


@pytest.mark.parametrize("axis", ["axial", "radial"])
@pytest.mark.xdist_group("serial")
def test_provider_refuses_invalid_stir_on_both_axes(axis):
    result = _w3_result_with_controls({axis: -1.0})

    assert result.status == "refused"
    assert result.diagnostic["reason"] == "invalid_stir_factor"


@pytest.mark.xdist_group("serial")
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
