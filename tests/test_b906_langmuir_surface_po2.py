"""b-906: Langmuir surface pO2 under d-376 amendment 1.

Green (16c8dabfa) prices the persisted melt fO2. Pure CaO at 2200 C:

- melt fO2 at the 1e-30 bar physical floor: Ca 2.609947e13 Pa,
  ideal-alpha flux 52330509702177.99 kg m^-2 h^-1
- ordinary in-band melt fO2 = 1e-9 bar: Ca 825.3377 Pa, flux
  1654.836 kg m^-2 h^-1

d-376 amendment 1 keeps the congruent closure (J_O2 = 1/2 J_M) only
when that eval pO2 is at the physical floor, or the d-072 redox
capacity is negligible. Pure CaO has no Fe, so the capacity term is
0 and the in-band identity takes the congruent root, 5.3423 Pa at
2.386749e-5 bar. A Fe2+/Fe3+ melt above the floor keeps green's
mass-action pressure. While O2 is net-leaving, an overhead pO2 above
the congruent root floors the surface.

The pins go through the evaporation resolver, which exists at green.
On green the resolver is the identity map, so the sub-floor pin and
the overhead pin fail with green's published pressure on the actual
side. The buffered pin fails when that pressure is moved off green.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    _pO2_is_melt_dissociation_floor,
)
from simulator.accounting.formulas import parse_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa
from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL, PyrolysisSimulator
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.evaporation import EvaporationMixin
from simulator.melt_backend.base import EquilibriumResult
from simulator.physical_constants import PA_PER_BAR
from simulator.state import MOLAR_MASS
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
# 1 mbar of O2. The congruent root is 2.39e-5 bar, so this partial is
# above it and must floor the surface. mbar / 1000 = bar.
_OVERHEAD_O2_MBAR = 1.0
_OVERHEAD_O2_BAR = _OVERHEAD_O2_MBAR / 1000.0
_BUFFERED_KG = {"CaO": 0.50, "FeO": 0.40, "SiO2": 0.10}
# Forced above the d-072 noop threshold so the sub-floor pin is the
# floor gate, not the empty-buffer gate. The pressure map stays the
# pure-CaO floor publication (Ca 2.609947e13 Pa).
_BUFFERED_CAPACITY_MOL_PER_LN = 1.0


def _catalog():
    payload = yaml.safe_load(_DATA.read_text())
    compiled = compile_vapour_rail_catalog(payload)
    return compiled, compiled.legacy_view()


def _cao_answer(intrinsic_fO2_log: float, accounts: dict[str, float] | None = None):
    payload = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(payload)
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": dict(accounts or {"CaO": 1.0})
                },
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


def _d072_capacity_mol_per_ln_fO2(
    comp_wt: dict[str, float],
    fe_atom_mol: float,
    fO2_log: float,
) -> float:
    """d-072 ``C_m_full``. Pure CaO (``n_Fe = 0``) returns 0."""

    host = SimpleNamespace(
        melt=SimpleNamespace(p_total_mbar=10.0),
        _cleaned_melt_fe_atom_mol=lambda: fe_atom_mol,
        _melt_oxide_wt_pct=lambda: dict(comp_wt),
        _vacuum_floor_bar=lambda: DEFAULT_VACUUM_FLOOR_BAR,
    )
    host._fe3_over_sigma_fe_at_fO2 = (
        PyrolysisSimulator._fe3_over_sigma_fe_at_fO2.__get__(host)
    )
    return float(PyrolysisSimulator._melt_redox_capacity_mol_per_ln_fO2(
        host,
        fO2_log=fO2_log,
        T_K=_T_K,
    ))


def _buffered_melt():
    total_kg = sum(_BUFFERED_KG.values())
    comp_wt = {
        oxide: kg / total_kg * 100.0 for oxide, kg in _BUFFERED_KG.items()
    }
    fe_atom_mol = _BUFFERED_KG["FeO"] / (MOLAR_MASS["FeO"] / 1000.0)
    return comp_wt, fe_atom_mol


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


def _host(
    compiled,
    legacy,
    diagnostic,
    pressures,
    *,
    redox_capacity_mol_per_ln_fO2: float,
    headspace_o2_mbar: float | None = None,
):
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
        _melt_headspace_composition_mbar=(
            {}
            if headspace_o2_mbar is None
            else {"O2": float(headspace_o2_mbar)}
        ),
        _resolve_evaporation_vapour_batch=_passthrough_batch,
    )
    def _capacity(*, fO2_log, T_K):
        del fO2_log, T_K
        return float(redox_capacity_mol_per_ln_fO2)

    host._melt_redox_capacity_mol_per_ln_fO2 = _capacity
    host._resolve_evaporation_batch_flux_state = (
        EvaporationMixin._resolve_evaporation_batch_flux_state.__get__(host)
    )
    host._evaporation_bulk_partial_pressure_pa = (
        EvaporationMixin._evaporation_bulk_partial_pressure_pa.__get__(host)
    )
    host._langmuir_overhead_pO2_bar = (
        EvaporationMixin._langmuir_overhead_pO2_bar.__get__(host)
    )
    host._langmuir_melt_redox_capacity_mol_per_ln_fO2 = (
        EvaporationMixin._langmuir_melt_redox_capacity_mol_per_ln_fO2.__get__(
            host
        )
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


def _assert_kernel_prices(host, equilibrium, resolved_ca: float) -> None:
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
    assert live_map["Ca"] == pytest.approx(resolved_ca, rel=1.0e-12)


def _published_ca(diagnostic) -> tuple[dict, float, float]:
    published = dict(diagnostic["vapor_pressures_Pa"])
    published_ca = float(published["Ca"])
    p_ref_bar = float(
        diagnostic["vapor_pressure_numerator_provenance"]["Ca"]["pO2_bar"]
    )
    return published, published_ca, p_ref_bar


def test_pure_cao_fO2_minus9_has_no_buffer_so_congruent() -> None:
    """Pure CaO has no multivalents, so C_m ~ 0 and the in-band value is the congruent root.

    d-072's capacity is ``(n_Fe / 4) * d(x_Fe3)/d(ln fO2)``. ``n_Fe = 0``
    returns 0, which is at or under ``OXYGEN_RESERVOIR_NOOP_MOL``. d-376
    amendment 1 therefore does not return this fixture to the 825.3377 Pa
    mass-action pressure. It takes the congruent surface, 5.3423 Pa.
    """

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    assert capacity == 0.0
    assert capacity <= OXYGEN_RESERVOIR_NOOP_MOL

    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    assert diagnostic["source_reaction_fO2_bar"] == pytest.approx(1.0e-9)
    assert not _pO2_is_melt_dissociation_floor(
        diagnostic["source_reaction_fO2_bar"]
    )
    # Green's in-band identity. The closure below is the other number.
    assert published_ca == pytest.approx(825.3377299577454, rel=1.0e-12)
    assert _ca_flux_kg_m2_h(published_ca) == pytest.approx(
        1654.8360177642212, rel=1.0e-9
    )
    surface_pO2_bar, pressure_pa, flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    assert surface_pO2_bar == pytest.approx(2.386748965e-5, rel=1.0e-6)
    assert pressure_pa == pytest.approx(5.34230068, rel=1.0e-6)
    assert flux == pytest.approx(10.71153209, rel=1.0e-6)

    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
    )
    resolved = _resolve(host, equilibrium)
    assert resolved["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["capacity_negligible"] is True
    assert notice["eval_pO2_below_physical_floor"] is False
    assert notice["overhead_bounded"] is False
    assert notice["surface_pO2_bar"] == pytest.approx(surface_pO2_bar, rel=1.0e-6)
    ratio = (
        (resolved["Ca"] / math.sqrt(_M_CA))
        / (notice["surface_pO2_bar"] * PA_PER_BAR / math.sqrt(_M_O2))
    )
    assert ratio == pytest.approx(2.0, rel=1.0e-6)
    _assert_kernel_prices(host, equilibrium, resolved["Ca"])
    assert any(_SURFACE_REASON in item for item in equilibrium.warnings)


def test_subfloor_cao_closes_at_5_3423_pa_even_with_buffer() -> None:
    """The 2.6e13 Pa case closes at 5.3423 Pa even when C_m is above the noop floor.

    Eval pO2 is the 1e-30 bar physical floor. The injected capacity is
    1 mol per ln fO2, above ``OXYGEN_RESERVOIR_NOOP_MOL``, so the closure
    is the floor gate and not ``C_m ~ 0``. Green prices the published
    2.609947e13 Pa; that identity fails this pin.
    """

    assert _BUFFERED_CAPACITY_MOL_PER_LN > OXYGEN_RESERVOIR_NOOP_MOL
    compiled, legacy = _catalog()
    answer = _cao_answer(-40.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    assert _pO2_is_melt_dissociation_floor(
        diagnostic["source_reaction_fO2_bar"]
    )
    assert published_ca == pytest.approx(26099470655394.605, rel=1.0e-12)
    assert _ca_flux_kg_m2_h(published_ca) == pytest.approx(
        52330509702177.99, rel=1.0e-9
    )
    surface_pO2_bar, pressure_pa, flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    assert pressure_pa == pytest.approx(5.34230068, rel=1.0e-6)
    assert flux == pytest.approx(10.71153209, rel=1.0e-6)
    # Green's identity is the high side of the pin.
    assert published_ca > pressure_pa

    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=_BUFFERED_CAPACITY_MOL_PER_LN,
    )
    resolved = _resolve(host, equilibrium)
    assert resolved["Ca"] == pytest.approx(5.34230068, rel=1.0e-6)
    assert _ca_flux_kg_m2_h(resolved["Ca"]) == pytest.approx(flux, rel=1.0e-6)
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["capacity_negligible"] is False
    assert notice["eval_pO2_below_physical_floor"] is True
    assert notice["overhead_bounded"] is False
    assert notice["surface_pO2_bar"] == pytest.approx(surface_pO2_bar, rel=1.0e-6)
    _assert_kernel_prices(host, equilibrium, resolved["Ca"])


def test_buffered_melt_keeps_green_in_band_pressure() -> None:
    """Fe2+/Fe3+ melt at fO2_log -9 keeps the mass-action Ca pressure.

    The Kress derivative at this fO2 is nonzero, so both ferric and
    ferrous iron are in the couple and ``C_m`` is above the noop
    threshold. Eval pO2 is 1e-9 bar, above the physical floor. d-376
    amendment 1 leaves the flux map on green's published pressure.
    """

    comp_wt, fe_atom_mol = _buffered_melt()
    capacity = _d072_capacity_mol_per_ln_fO2(comp_wt, fe_atom_mol, -9.0)
    assert capacity > OXYGEN_RESERVOIR_NOOP_MOL
    redox_host = SimpleNamespace(
        melt=SimpleNamespace(p_total_mbar=10.0),
        _cleaned_melt_fe_atom_mol=lambda: fe_atom_mol,
        _melt_oxide_wt_pct=lambda: dict(comp_wt),
        _vacuum_floor_bar=lambda: DEFAULT_VACUUM_FLOOR_BAR,
    )
    redox_host._fe3_over_sigma_fe_at_fO2 = (
        PyrolysisSimulator._fe3_over_sigma_fe_at_fO2.__get__(redox_host)
    )
    fe3 = redox_host._fe3_over_sigma_fe_at_fO2(
        comp_wt,
        fO2_log=-9.0,
        T_K=_T_K,
        pressure_bar=0.01,
    )
    assert 0.0 < fe3 < 1.0

    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0, _BUFFERED_KG)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    assert diagnostic["source_reaction_fO2_bar"] == pytest.approx(1.0e-9)
    assert not _pO2_is_melt_dissociation_floor(
        diagnostic["source_reaction_fO2_bar"]
    )
    _surface, congruent_pa, _flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    assert published_ca != pytest.approx(congruent_pa, rel=1.0e-3)

    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
    )
    resolved = _resolve(host, equilibrium)
    assert resolved["Ca"] == published_ca
    assert resolved == published
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    assert not hasattr(host, "_last_langmuir_surface_oxygen")
    _assert_kernel_prices(host, equilibrium, published_ca)
    assert not any(
        _SURFACE_REASON in item for item in equilibrium.warnings
    )


def test_overhead_po2_above_congruent_bounds_surface() -> None:
    """Overhead pO2 above the congruent root floors the surface while O2 leaves.

    Pure CaO is ``C_m ~ 0``, so the vacuum root is 2.386749e-5 bar and
    Ca is 5.3423 Pa. Headspace O2 at 1e-3 bar is above that root. The
    surface becomes the overhead partial and Ca follows
    ``P_pub * (p_overhead / p_ref)^(-1/2)``. Green keeps the published
    825.3377 Pa, which is the high side of this pin.
    """

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    assert capacity <= OXYGEN_RESERVOIR_NOOP_MOL
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    surface_pO2_bar, congruent_pa, _flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    assert _OVERHEAD_O2_BAR > surface_pO2_bar
    bounded_pa = published_ca * (_OVERHEAD_O2_BAR / p_ref_bar) ** (-0.5)
    assert bounded_pa < congruent_pa
    assert published_ca > bounded_pa

    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        headspace_o2_mbar=_OVERHEAD_O2_MBAR,
    )
    resolved = _resolve(host, equilibrium)
    notice = host._last_langmuir_surface_oxygen
    assert notice["overhead_bounded"] is True
    assert notice["surface_pO2_bar"] == pytest.approx(_OVERHEAD_O2_BAR, rel=1.0e-12)
    assert resolved["Ca"] == pytest.approx(bounded_pa, rel=1.0e-6)
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    _assert_kernel_prices(host, equilibrium, resolved["Ca"])
    assert any(_SURFACE_REASON in item for item in equilibrium.warnings)
