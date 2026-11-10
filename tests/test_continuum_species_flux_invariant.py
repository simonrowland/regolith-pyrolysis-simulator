"""Generative pin for the continuum-flux invariant.

INVARIANT: no authorized species flux is an artificial zero. Every species
that shares a continuum-domain diagnostic is one of:

- the computed ordinary series flux (in-domain, or a Stage 0 mark),
- the continuum continuation (authorized extrapolation: gas film kept on),
- a typed refusal (status refused, ledger not authorized).

Positive evaporation driving pressure on an authorized extrapolation is the
continuation and is strictly positive. The native-Fe capacity and the
melt-Fe residual cap both read that choice from
``resolve_species_flux_kg_s_m2``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _EVAPORATION_LJ_PROXY_MOLAR_MASSES,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import IntentResult, ProviderAccountView
from simulator.condensation import _LENNARD_JONES_PARAMS
from simulator.core import PyrolysisSimulator
from simulator.evaporation import (
    _C2A_CONTINUUM_EXTRAPOLATION_CAMPAIGNS,
    pool_first_residual_kg_hr,
    resolve_species_flux_kg_s_m2,
    viscous_p_bulk_out_of_domain_diagnostic,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase
from simulator.transport_regime import (
    _ELECTROLYSIS_CAMPAIGNS,
    _IDLE_CAMPAIGNS,
    _PYROLYSIS_EXTRACTION_CAMPAIGNS,
    _STAGE0_BAKEOUT_CAMPAIGNS,
)

ROOT = Path(__file__).resolve().parents[1]
_CARRIERS = frozenset({"N2", "Ar", "Kr", "CO2", "O2", "He"})
TRANSPORT_SPECIES = tuple(sorted(
    (set(_LENNARD_JONES_PARAMS) - _CARRIERS)
    | set(_EVAPORATION_LJ_PROXY_MOLAR_MASSES)
))
CAMPAIGN_NAMES = tuple(sorted(
    set(_STAGE0_BAKEOUT_CAMPAIGNS)
    | set(_PYROLYSIS_EXTRACTION_CAMPAIGNS)
    | set(_ELECTROLYSIS_CAMPAIGNS)
    | set(_IDLE_CAMPAIGNS)
    | {"", "NOT_A_CAMPAIGN"}
))
# 0.2 mbar, 1600 C, 0.12 m N2: Kn ≈ 0.0168, transitional.
_TRANSITIONAL_PA = 20.0
_VISCOUS_PA = 1000.0
_T_GAS_K = 1873.15
_T_C = 1600.0
_PIPE_M = 0.12
_POOL_MOL = 1.0e-6
_MELT_RAW_KG_HR = 1.0e6


def _molar_mass_kg_mol(species: str) -> float:
    if species in _LENNARD_JONES_PARAMS and species not in _CARRIERS:
        return float(_LENNARD_JONES_PARAMS[species][2]) / 1000.0
    return float(_EVAPORATION_LJ_PROXY_MOLAR_MASSES[species]) / 1000.0


def _series(species: str, *, overhead_pa: float, extrapolate: bool, alpha: float = 1.0):
    return _series_resistance_evaporation_flux_kg_m2_s(
        species=species,
        P_eq_pa=100.0,
        P_bulk_pa=0.0,
        T_surface_K=_T_GAS_K,
        molar_mass_kg_mol=_molar_mass_kg_mol(species),
        alpha_i=alpha,
        pipe_diameter_m=_PIPE_M,
        overhead_pressure_pa=overhead_pa,
        carrier_gas="N2",
        T_gas_K=_T_GAS_K,
        gas_resistance_enabled=True,
        extrapolate_continuum=extrapolate,
    )


def _assert_resolved_flux(campaign_name: str | None, *, overhead_pa: float) -> None:
    ordinary = _series("Fe", overhead_pa=overhead_pa, extrapolate=False)
    diagnostic = viscous_p_bulk_out_of_domain_diagnostic(
        knudsen_number=float(ordinary.knudsen_number),
        overhead_pressure_pa=overhead_pa,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_T_GAS_K,
        carrier_gas="N2",
        campaign_name=campaign_name,
        asking_site="tests.continuum_species_flux_invariant",
    )
    continued = None
    continuation_flux = None
    if (
        diagnostic is not None
        and diagnostic.get("status") == "extrapolated"
        and diagnostic.get("ledger_yields_authorized")
    ):
        continued = _series("Fe", overhead_pa=overhead_pa, extrapolate=True)
        continuation_flux = float(continued.flux_kg_s_m2)
    resolved = resolve_species_flux_kg_s_m2(
        diagnostic,
        ordinary_flux_kg_s_m2=float(ordinary.flux_kg_s_m2),
        continuation_flux_kg_s_m2=continuation_flux,
    )
    name = "" if campaign_name is None else str(campaign_name)
    if name in _C2A_CONTINUUM_EXTRAPOLATION_CAMPAIGNS and overhead_pa == _TRANSITIONAL_PA:
        assert diagnostic is not None
        assert diagnostic["status"] == "extrapolated"
        assert diagnostic["ledger_yields_authorized"] is True
        assert continued is not None
        assert continued.r_gas > 0.0
        assert resolved == pytest.approx(continued.flux_kg_s_m2)
        assert resolved > 0.0
        return
    if diagnostic is None:
        assert resolved == pytest.approx(ordinary.flux_kg_s_m2)
        assert resolved > 0.0
        return
    if diagnostic.get("status") == "refused":
        assert diagnostic["ledger_yields_authorized"] is False
        assert resolved == pytest.approx(0.0)
        return
    assert diagnostic["status"] == "out_of_domain"
    assert diagnostic["ledger_yields_authorized"] is True
    assert ordinary.r_gas == pytest.approx(0.0)
    assert resolved == pytest.approx(ordinary.flux_kg_s_m2)
    assert resolved > 0.0


@pytest.fixture(scope="module")
def transitional_sim() -> PyrolysisSimulator:
    setpoints = yaml.safe_load((ROOT / "data" / "setpoints.yaml").read_text())
    kernel = setpoints.setdefault("chemistry_kernel", {})
    kernel["allow_fallback_vapor"] = True
    kernel["allow_unmeasured_alpha_fallback"] = True
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        setpoints,
        yaml.safe_load((ROOT / "data" / "feedstocks.yaml").read_text()),
        yaml.safe_load((ROOT / "data" / "vapor_pressures.yaml").read_text()),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = _T_C
    sim.overhead.headspace_temperature_K = _T_GAS_K
    sim.overhead_model.pipe_diameter_m = _PIPE_M
    sim.melt.p_total_mbar = _TRANSITIONAL_PA / 100.0
    sim._melt_headspace_composition_mbar = {}
    return sim


def _fe_molar_mass_kg_mol(sim: PyrolysisSimulator) -> float:
    return float(sim.vapor_pressures["metals"]["Fe"]["molar_mass_g_mol"]) / 1000.0


def _independent_native_series(sim: PyrolysisSimulator, part: dict, *, extrapolate: bool):
    series = part["series_resistance"]
    return _series_resistance_evaporation_flux_kg_m2_s(
        species="Fe",
        P_eq_pa=float(part["P_eq_Pa"]),
        P_bulk_pa=float(part["P_bulk_Pa"]),
        T_surface_K=float(part["temperature_K"]),
        molar_mass_kg_mol=_fe_molar_mass_kg_mol(sim),
        alpha_i=float(part["alpha_Fe"]),
        pipe_diameter_m=float(series["transport_length_m"]),
        overhead_pressure_pa=float(part["overhead_pressure_pa"]),
        axial_stir_factor=float(series["axial_stir_applied"]),
        radial_stir_factor=float(series["radial_stir_applied"]),
        carrier_gas=str(part["carrier_gas"]),
        T_gas_K=float(part["series_resistance"]["gas_temperature_K"])
        if "gas_temperature_K" in series
        else _T_GAS_K,
        gas_resistance_enabled=True,
        extrapolate_continuum=extrapolate,
    )


@pytest.mark.parametrize("campaign_name", CAMPAIGN_NAMES + (None,))
def test_chokepoint_enumerates_campaign_branches(campaign_name) -> None:
    _assert_resolved_flux(campaign_name, overhead_pa=_TRANSITIONAL_PA)


@pytest.mark.parametrize(
    "campaign_name",
    tuple(sorted(_C2A_CONTINUUM_EXTRAPOLATION_CAMPAIGNS)),
)
def test_in_band_c2a_chokepoint_is_not_an_extrapolation(campaign_name: str) -> None:
    _assert_resolved_flux(campaign_name, overhead_pa=_VISCOUS_PA)


@pytest.mark.parametrize("phase", list(CampaignPhase), ids=lambda phase: phase.name)
def test_native_and_melt_fe_follow_the_invariant(
    phase: CampaignPhase,
    transitional_sim: PyrolysisSimulator,
) -> None:
    sim = transitional_sim
    sim.melt.temperature_C = _T_C
    sim.overhead.headspace_temperature_K = _T_GAS_K
    sim.overhead_model.pipe_diameter_m = _PIPE_M
    sim.melt.p_total_mbar = _TRANSITIONAL_PA / 100.0
    sim._melt_headspace_composition_mbar = {}
    sim.melt.campaign = phase

    part = sim._native_fe_partition_diagnostic(_POOL_MOL)
    molar = _fe_molar_mass_kg_mol(sim)
    area = float(sim.melt.melt_surface_area_m2)
    residual_mol = float(part["ordinary_melt_fe_residual_capacity_mol_hr"])
    melt_kg_hr = pool_first_residual_kg_hr(
        _MELT_RAW_KG_HR,
        residual_mol,
        molar,
    )
    capacity_kg_hr = float(part["native_fe_vapor_capacity_kg_hr"])
    assert part["P_eq_Pa"] > part["P_bulk_Pa"]

    if phase.name in _C2A_CONTINUUM_EXTRAPOLATION_CAMPAIGNS:
        continued = _independent_native_series(sim, part, extrapolate=True)
        assert part["status"] == "extrapolated"
        assert part["ledger_yields_authorized"] is True
        assert part["evaporation_flux_status"] == "extrapolated"
        assert continued.r_gas > 0.0
        assert part["series_resistance"]["r_gas"] == pytest.approx(continued.r_gas)
        assert part["series_resistance"]["flux_kg_s_m2"] == pytest.approx(
            continued.flux_kg_s_m2
        )
        assert capacity_kg_hr == pytest.approx(
            continued.flux_kg_s_m2 * area * 3600.0
        )
        assert capacity_kg_hr > 0.0
        assert part["native_fe_vapor_mol"] == pytest.approx(_POOL_MOL)
        assert residual_mol > 0.0
        assert melt_kg_hr > 0.0
        assert melt_kg_hr == pytest.approx(residual_mol * molar)
        assert melt_kg_hr < _MELT_RAW_KG_HR
        return

    if part.get("status") == "out_of_domain":
        ordinary = _independent_native_series(sim, part, extrapolate=False)
        assert part["ledger_yields_authorized"] is True
        assert ordinary.r_gas == pytest.approx(0.0)
        assert capacity_kg_hr == pytest.approx(ordinary.flux_kg_s_m2 * area * 3600.0)
        assert capacity_kg_hr > 0.0
        assert residual_mol > 0.0
        assert melt_kg_hr > 0.0
        return

    assert part["status"] == "refused"
    assert part["ledger_yields_authorized"] is False
    assert capacity_kg_hr == pytest.approx(0.0)
    assert part["native_fe_vapor_mol"] == pytest.approx(0.0)
    assert residual_mol == pytest.approx(0.0)
    assert melt_kg_hr == pytest.approx(0.0)


def test_c2a_staged_in_band_native_fe_is_unchanged(
    transitional_sim: PyrolysisSimulator,
) -> None:
    sim = transitional_sim
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.melt.temperature_C = _T_C
    sim.overhead.headspace_temperature_K = _T_GAS_K
    sim.overhead_model.pipe_diameter_m = _PIPE_M
    sim.melt.p_total_mbar = _VISCOUS_PA / 100.0
    sim._melt_headspace_composition_mbar = {}
    part = sim._native_fe_partition_diagnostic(_POOL_MOL)
    ordinary = _independent_native_series(sim, part, extrapolate=False)
    area = float(sim.melt.melt_surface_area_m2)
    assert part.get("status") is None
    assert part.get("ledger_yields_authorized") is None
    assert ordinary.r_gas > 0.0
    assert part["native_fe_vapor_capacity_kg_hr"] == pytest.approx(
        ordinary.flux_kg_s_m2 * area * 3600.0
    )
    assert part["series_resistance"]["r_gas"] > 0.0


def test_c2a_staged_melt_fe_cap_uses_continuation_residual(
    transitional_sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = transitional_sim
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.melt.temperature_C = _T_C
    sim.overhead.headspace_temperature_K = _T_GAS_K
    sim.melt.p_total_mbar = _TRANSITIONAL_PA / 100.0
    sim._melt_headspace_composition_mbar = {}
    part = sim._native_fe_partition_diagnostic(_POOL_MOL)
    residual_mol = float(part["ordinary_melt_fe_residual_capacity_mol_hr"])
    sim._native_fe_vapor_residual_capacity_mol_this_hr = residual_mol
    stub = IntentResult(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        status="ok",
        diagnostic={"evaporation_flux_kg_hr": {"Fe": _MELT_RAW_KG_HR}},
    )

    def dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return stub
        raise AssertionError(intent)

    monkeypatch.setattr(sim, "_dispatch_only", dispatch)
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 100.0},
        vapor_pressures_source={},
        activity_coefficients={},
        diagnostics={},
        liquid_fraction=1.0,
    )
    flux = sim._calculate_evaporation(equilibrium)
    molar = _fe_molar_mass_kg_mol(sim)
    expected = pool_first_residual_kg_hr(_MELT_RAW_KG_HR, residual_mol, molar)
    assert expected > 0.0
    assert flux.species_kg_hr["Fe"] == pytest.approx(expected)
    allocation = sim._last_evaporation_flux_diagnostic["native_fe_capacity_allocation"]
    assert allocation["melt_fe_applied_kg_hr"] == pytest.approx(expected)
    assert allocation["melt_fe_raw_kg_hr"] == pytest.approx(_MELT_RAW_KG_HR)


def test_refused_campaign_melt_fe_cap_stays_a_typed_zero(
    transitional_sim: PyrolysisSimulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = transitional_sim
    sim.melt.campaign = CampaignPhase.C4
    sim.melt.temperature_C = _T_C
    sim.overhead.headspace_temperature_K = _T_GAS_K
    sim.melt.p_total_mbar = _TRANSITIONAL_PA / 100.0
    sim._melt_headspace_composition_mbar = {}
    part = sim._native_fe_partition_diagnostic(_POOL_MOL)
    assert part["ledger_yields_authorized"] is False
    assert part["native_fe_vapor_capacity_kg_hr"] == pytest.approx(0.0)
    sim._native_fe_vapor_residual_capacity_mol_this_hr = float(
        part["ordinary_melt_fe_residual_capacity_mol_hr"]
    )
    stub = IntentResult(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        status="ok",
        diagnostic={"evaporation_flux_kg_hr": {"Fe": _MELT_RAW_KG_HR}},
    )
    monkeypatch.setattr(
        sim,
        "_dispatch_only",
        lambda intent, *args, **kwargs: stub,
    )
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa={"Fe": 100.0},
        vapor_pressures_source={},
        activity_coefficients={},
        diagnostics={},
        liquid_fraction=1.0,
    )
    flux = sim._calculate_evaporation(equilibrium)
    assert "Fe" not in flux.species_kg_hr
    allocation = sim._last_evaporation_flux_diagnostic["native_fe_capacity_allocation"]
    assert allocation["melt_fe_applied_kg_hr"] == pytest.approx(0.0)
    assert allocation["melt_fe_residual_capacity_kg_hr"] == pytest.approx(0.0)


def test_c7_transitional_transport_stays_a_typed_refusal(
    transitional_sim: PyrolysisSimulator,
) -> None:
    sim = transitional_sim
    sim.melt.temperature_C = 1750.0
    sim.melt.p_total_mbar = 0.03632
    sim.overhead.headspace_temperature_K = 2023.15
    sim.overhead_model.pipe_diameter_m = 0.12
    cfg = dict(sim._c7_campaign_config())
    cfg.update(
        active_ca_condensation_route=True,
        dedicated_ca_condenser=True,
        hold_temp_C=1750.0,
        p_total_mbar=0.03632,
        ca_route_surface_area_m2=0.2,
        hold_time_h=1.0,
        stir_factor=1.0,
        radial_stir_factor=1.0,
    )
    extent, diagnostic = sim._c7_transport_extent_mol(cfg, ca_per_extent=3.0)
    assert extent == pytest.approx(0.0)
    assert diagnostic["c7_ca_flux_kg_s_m2"] == pytest.approx(0.0)
    assert diagnostic["ledger_yields_authorized"] is False
    assert diagnostic["authority_class"] == "diagnostic-limited"
    assert diagnostic["reason"] == "viscous_p_bulk_transport_out_of_domain"


def test_authorized_extrapolation_without_continuation_refuses() -> None:
    from simulator.evaporation import EvaporationFluxRefusal

    diagnostic = viscous_p_bulk_out_of_domain_diagnostic(
        knudsen_number=0.016813952116450776,
        overhead_pressure_pa=_TRANSITIONAL_PA,
        pipe_diameter_m=_PIPE_M,
        gas_temperature_K=_T_GAS_K,
        carrier_gas="N2",
        campaign_name="C2A_staged",
        asking_site="tests.missing_continuation",
    )
    assert diagnostic is not None
    assert diagnostic["status"] == "extrapolated"
    with pytest.raises(EvaporationFluxRefusal) as excinfo:
        resolve_species_flux_kg_s_m2(
            diagnostic,
            ordinary_flux_kg_s_m2=0.0,
            continuation_flux_kg_s_m2=None,
        )
    assert excinfo.value.reason == (
        "authorized_extrapolation_missing_continuum_flux"
    )
    assert excinfo.value.diagnostic["ledger_yields_authorized"] is False


def _provider_result(species: str, campaign_name: str, overhead_pa: float):
    molar = _molar_mass_kg_mol(species)
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0}},
        species_formula_registry={},
    )
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=view,
            temperature_C=_T_C,
            pressure_bar=overhead_pa / 1.0e5,
            fO2_log=None,
            control_inputs={
                "campaign_name": campaign_name,
                "vapour_batch_flux_pressures_Pa": {species: 100.0},
                "overhead_partials_Pa": {},
                "overhead_pressure_pa": overhead_pa,
                "pipe_diameter_m": _PIPE_M,
                "gas_temperature_K": _T_GAS_K,
                "carrier_gas": "N2",
                "molar_mass_kg_mol": {species: molar},
                "stoich_by_species": {
                    species: {
                        "parent_oxide": "SiO2",
                        "oxide_per_product_kg": 1.0,
                        "O2_per_product_kg": 0.0,
                    },
                },
                "available_oxide_kg": {species: 10.0},
                "melt_surface_area_m2": 1.0,
                "stir_factor": {"axial": 0.0, "radial": 1.0},
                "alpha": {species: 1.0},
            },
        )
    )


@pytest.mark.parametrize("species", TRANSPORT_SPECIES)
@pytest.mark.parametrize(
    ("campaign_name", "overhead_pa", "branch"),
    (
        ("C2A_staged", _TRANSITIONAL_PA, "extrapolated"),
        ("C2A_STAGED", _TRANSITIONAL_PA, "extrapolated"),
        ("C2A", _TRANSITIONAL_PA, "extrapolated"),
        ("C2A_continuous", _TRANSITIONAL_PA, "extrapolated"),
        ("C4", _TRANSITIONAL_PA, "provider_continuation"),
        ("C0", _TRANSITIONAL_PA, "bakeout_mark"),
        ("C2A_staged", _VISCOUS_PA, "in_domain"),
    ),
)
def test_provider_species_flux_matches_the_invariant(
    species: str,
    campaign_name: str,
    overhead_pa: float,
    branch: str,
) -> None:
    result = _provider_result(species, campaign_name, overhead_pa)
    assert result.status == "ok", result.diagnostic
    flux = result.diagnostic["evaporation_flux_kg_hr"]
    missing = result.diagnostic.get("missing_transport_parameters") or {}
    missing_alpha = result.diagnostic.get("missing_alpha") or {}
    if species in missing or species in missing_alpha:
        assert species not in flux
        record = missing.get(species) or missing_alpha[species]
        assert record.get("status") == "unavailable" or record.get("policy")
        return
    published = float(flux[species])
    assert published > 0.0
    series = result.diagnostic["evaporation_series_resistance"][species]
    if branch in {"extrapolated", "provider_continuation"}:
        notice = result.diagnostic["continuum_extrapolation_notice"]
        assert notice["status"] == "extrapolated"
        assert notice["ledger_yields_authorized"] is True
        continued = _series(species, overhead_pa=overhead_pa, extrapolate=True)
        assert continued.r_gas > 0.0
        assert series["r_gas"] == pytest.approx(continued.r_gas)
        assert published == pytest.approx(continued.flux_kg_s_m2 * 3600.0)
        return
    if branch == "bakeout_mark":
        assert "continuum_extrapolation_notice" not in result.diagnostic
        ordinary = _series(species, overhead_pa=overhead_pa, extrapolate=False)
        assert ordinary.r_gas == pytest.approx(0.0)
        assert series["r_gas"] == pytest.approx(0.0)
        assert published == pytest.approx(ordinary.flux_kg_s_m2 * 3600.0)
        return
    assert "continuum_extrapolation_notice" not in result.diagnostic
    ordinary = _series(species, overhead_pa=overhead_pa, extrapolate=False)
    assert ordinary.r_gas > 0.0
    assert published == pytest.approx(ordinary.flux_kg_s_m2 * 3600.0)
