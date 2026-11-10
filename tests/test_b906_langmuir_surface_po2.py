"""b-906: free Langmuir evaporation closes surface pO2 from the oxygen balance.

Green (16c8dabfa) prices the persisted melt fO2. Pure CaO at 2200 C:

- melt fO2 at the 1e-30 bar floor: Ca ideal-alpha flux
  52330509702177.99 kg m^-2 h^-1
- ordinary in-band melt fO2 = 1e-9 bar: Ca 825.3377 Pa, flux
  1654.836 kg m^-2 h^-1

Congruent CaO with only Ca(g) and O2(g), alpha = 1, n_Ca = -1/2, has
J_Ca = 2 J_O2. With P_O2 = p * 1e5 Pa/bar and
P_Ca = P_pub * (p / p_ref)^(-1/2),

    p^(3/2) = P_pub * sqrt(p_ref) * sqrt(M_O2)
              / (2 * 1e5 * sqrt(M_Ca))

That root is 2.386749e-5 bar and Ca is 5.342301 Pa (10.71153 kg m^-2 h^-1)
at both publications, because the power law cancels p_ref. The shared
flux-map resolver is what finite cold-train capacity prices. Live
evaporation must hand that same map to the flux kernel.

The pin imports only modules that exist at green, so collection succeeds
there and the pressure assertion fails on the raw map.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.accounting.formulas import parse_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.evaporation import EvaporationMixin
from simulator.melt_backend.base import EquilibriumResult
from simulator.physical_constants import PA_PER_BAR
from simulator.vapour_rail.batch import (
    FluxEligible,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY

_T_C = 2200.0
_T_K = _T_C + 273.15
_DATA = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
_M_CA = parse_formula("Ca").molar_mass_kg_per_mol()
_M_O2 = parse_formula("O2").molar_mass_kg_per_mol()
_SURFACE_REASON = "langmuir_surface_pO2_from_evaporative_oxygen_balance"


def _catalog():
    payload = yaml.safe_load(_DATA.read_text())
    compiled = compile_vapour_rail_catalog(payload)
    return compiled, compiled.legacy_view()


def _cao_answer(intrinsic_fO2_log: float):
    payload = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(payload)
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"CaO": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=_T_C,
            pressure_bar=0.01,
            control_inputs={
                "pO2_bar": DEFAULT_VACUUM_FLOOR_BAR,
                "intrinsic_fO2_log": intrinsic_fO2_log,
                "vacuum_floor_bar": DEFAULT_VACUUM_FLOOR_BAR,
            },
        )
    )


def _ca_flux_kg_m2_h(pressure_Pa: float) -> float:
    return (
        pressure_Pa
        * hertz_knudsen_k_kg_s_m2_pa(_T_K, _M_CA)
        * 3600.0
    )


def _congruent_cao_surface(pressure_ca_pa: float, p_ref_bar: float) -> tuple[float, float, float]:
    """Independent Ca+O2 root. Does not call the oxygen-balance solver.

    Algebra is the module comment above. Alpha is 1 for Ca and for the
    synthetic O2 channel. Unit check: p in bar, P in Pa, flux in
    kg m^-2 h^-1. Limiting check: the same P_pub * sqrt(p_ref) from the
    floor publication and from 1e-9 bar yields one p.
    """

    p_pow = (
        pressure_ca_pa
        * math.sqrt(p_ref_bar)
        * math.sqrt(_M_O2)
        / (2.0 * PA_PER_BAR * math.sqrt(_M_CA))
    )
    surface_pO2_bar = p_pow ** (1.0 / 1.5)
    pressure_pa = pressure_ca_pa * (surface_pO2_bar / p_ref_bar) ** (-0.5)
    return surface_pO2_bar, pressure_pa, _ca_flux_kg_m2_h(pressure_pa)


def _passthrough_batch(equilibrium, *, temperature_K, effective_pressure_source):
    del temperature_K, effective_pressure_source
    channels = {
        species_id: VapourAnswer(
            species_id=species_id,
            pressure=PressureValue(pa=float(pressure_pa)),
            selected_runtime_pressure=PressureValue(pa=float(pressure_pa)),
            flux=FluxEligible(alpha_ref=f"alpha:{species_id}"),
            source_label="test_b906_passthrough",
            formula_id=species_id,
            source_account="process.cleaned_melt",
            solve_group_id=f"test:{species_id}",
            state_fingerprint="state:test",
            validation_status="pending_validation",
        )
        for species_id, pressure_pa in equilibrium.vapor_pressures_Pa.items()
    }
    return VapourBatch(
        requested_species_ids=frozenset(channels),
        channels_by_species=channels,
        flux_active_species_ids=frozenset(channels),
    )


def _host(compiled, legacy, diagnostic, pressures):
    equilibrium = EquilibriumResult(
        temperature_C=_T_C,
        pressure_bar=0.01,
        liquid_fraction=1.0,
        status="ok",
        vapor_pressures_Pa=dict(pressures),
        warnings=[],
    )
    host = SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=_T_C,
            melt_surface_area_m2=1.0,
            p_total_mbar=10.0,
            stir_state=SimpleNamespace(axial=0.0, radial=0.0),
            campaign=SimpleNamespace(name="C2A"),
        ),
        setpoints={},
        vapor_pressures=legacy,
        vapour_rail_catalog=compiled,
        overhead=SimpleNamespace(headspace_temperature_K=0.0),
        overhead_model=SimpleNamespace(pipe_diameter_m=0.12),
        _last_vapor_pressure_diagnostic=dict(diagnostic),
        _last_vapour_batch_resolve_error={},
        _melt_headspace_composition_mbar={},
        _resolve_evaporation_vapour_batch=_passthrough_batch,
    )
    host._resolve_evaporation_batch_flux_state = (
        EvaporationMixin._resolve_evaporation_batch_flux_state.__get__(host)
    )
    host._evaporation_bulk_partial_pressure_pa = (
        EvaporationMixin._evaporation_bulk_partial_pressure_pa.__get__(host)
    )
    host._evaporation_overhead_total_pressure_Pa = (
        EvaporationMixin._evaporation_overhead_total_pressure_Pa.__get__(host)
    )
    host._evaporation_flux_control_inputs = (
        EvaporationMixin._evaporation_flux_control_inputs.__get__(host)
    )
    host._build_evaporation_aux_maps = lambda pressures: ({}, {}, {})
    host._build_partial_melt_offgassing_diagnostic = (
        lambda *args, **kwargs: {}
    )
    return host, equilibrium


def _resolve(host, equilibrium):
    pressures, *_rest = host._resolve_evaporation_batch_flux_state(
        equilibrium,
        temperature_K=_T_K,
    )
    return pressures


@pytest.mark.parametrize("intrinsic_fO2_log", [-40.0, -9.0])
def test_shared_resolver_matches_congruent_cao(intrinsic_fO2_log: float) -> None:
    compiled, legacy = _catalog()
    answer = _cao_answer(intrinsic_fO2_log)
    diagnostic = answer.diagnostic
    published = dict(diagnostic["vapor_pressures_Pa"])
    published_ca = published["Ca"]
    if intrinsic_fO2_log == -40.0:
        assert _ca_flux_kg_m2_h(published_ca) == pytest.approx(
            52330509702177.99, rel=1.0e-9
        )
    else:
        assert diagnostic["source_reaction_fO2_bar"] == pytest.approx(1.0e-9)
        assert published_ca == pytest.approx(825.3377, rel=1.0e-4)
        assert _ca_flux_kg_m2_h(published_ca) == pytest.approx(
            1654.836, rel=1.0e-4
        )

    p_ref_bar = float(
        diagnostic["vapor_pressure_numerator_provenance"]["Ca"]["pO2_bar"]
    )
    surface_pO2_bar, pressure_pa, flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    # Floor and in-band publications are the same power law, so one root.
    assert surface_pO2_bar == pytest.approx(2.386748965e-5, rel=1.0e-6)
    assert pressure_pa == pytest.approx(5.34230068, rel=1.0e-6)
    assert flux == pytest.approx(10.71153209, rel=1.0e-6)

    host, equilibrium = _host(compiled, legacy, diagnostic, published)
    resolved = _resolve(host, equilibrium)

    assert resolved["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
    assert _ca_flux_kg_m2_h(resolved["Ca"]) == pytest.approx(flux, rel=1.0e-6)
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["surface_pO2_bar"] == pytest.approx(surface_pO2_bar, rel=1.0e-6)
    assert notice["reason"] == _SURFACE_REASON
    # J_Ca / J_O2 = 2 with the synthetic O2 channel P_O2 = p * 1e5.
    ratio = (
        (resolved["Ca"] / math.sqrt(_M_CA))
        / (notice["surface_pO2_bar"] * PA_PER_BAR / math.sqrt(_M_O2))
    )
    assert ratio == pytest.approx(2.0, rel=1.0e-6)


@pytest.mark.parametrize("intrinsic_fO2_log", [-40.0, -9.0])
def test_live_kernel_prices_the_capacity_map(intrinsic_fO2_log: float) -> None:
    compiled, legacy = _catalog()
    answer = _cao_answer(intrinsic_fO2_log)
    diagnostic = answer.diagnostic
    published = dict(diagnostic["vapor_pressures_Pa"])
    p_ref_bar = float(
        diagnostic["vapor_pressure_numerator_provenance"]["Ca"]["pO2_bar"]
    )
    _surface, pressure_pa, _flux = _congruent_cao_surface(
        published["Ca"], p_ref_bar
    )
    host, equilibrium = _host(compiled, legacy, diagnostic, published)
    capacity_map = _resolve(host, equilibrium)
    captured: dict = {}

    def _dispatch_only(intent, *args, **kwargs):
        del args
        captured["intent"] = intent
        captured["controls"] = kwargs["control_inputs"]
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    host._dispatch_only = _dispatch_only
    EvaporationMixin._calculate_evaporation(host, equilibrium)

    assert captured["intent"] is ChemistryIntent.EVAPORATION_FLUX
    live_map = captured["controls"][CONTROL_FLUX_PRESSURES_KEY]
    assert live_map["Ca"] == pytest.approx(capacity_map["Ca"], rel=1.0e-12)
    assert live_map["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
    assert capacity_map["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
    assert equilibrium.vapor_pressures_Pa["Ca"] == published["Ca"]
    assert any(
        _SURFACE_REASON in item for item in equilibrium.warnings
    )
    assert host._last_evaporation_flux_diagnostic["langmuir_surface_oxygen"][
        "surface_pO2_bar"
    ] == pytest.approx(_surface, rel=1.0e-6)
