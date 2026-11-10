"""b-906: Langmuir surface pO2 under d-376 amendment 1.

Green (16c8dabfa) prices the persisted melt fO2. Pure CaO at 2200 C:

- melt fO2 at the 1e-30 bar physical floor: Ca 2.609947e13 Pa,
  ideal-alpha flux 52330509702177.99 kg m^-2 h^-1
- ordinary in-band melt fO2 = 1e-9 bar: Ca 825.3377 Pa, flux
  1654.836 kg m^-2 h^-1

d-376 amendment 1 keeps the congruent closure (J_O2 = 1/2 J_M) only
when that eval pO2 is at the physical floor, or the d-072 redox
capacity is negligible. Pure CaO has no Fe, so the capacity term is
0 and the free-molecular in-band identity takes the congruent root,
5.3423 Pa at 2.386749e-5 bar. A Fe2+/Fe3+ melt above the floor keeps
green's mass-action pressure. With O2 in the overhead, the surface
stays at or above that partial while O2 is net-leaving, because the
root is the publisher net flux at those partials, not a clamp.

The pins go through the evaporation resolver, which exists at green.
On green the resolver is the identity map, so the sub-floor pin and
the overhead pin fail with green's published pressure on the actual
side. The buffered pin fails when that pressure is moved off green.
The candidate-only helpers are bound only when this mixin already
has them, so the same fixture reaches that identity resolver.

Invariant, enforced only inside ``apply_langmuir_surface_oxygen``:
every caller enters with the partials of that evaluation, and every
species is the published pressure (closure closed, or not a
melt-surface carrier), the one shared surface-pO2 law, or a typed
refusal. The published surface is the root of
``sum (nO - dO) J = 0`` with ``J = k * max(0, P - P_bulk)`` using the
publisher conductance (ideal Hertz-Knudsen when the film is off).
The reported residual is that root's residual. A condensed-metal cap
never publishes above P_sat, including when the solve lowers pO2 and
a subunit activity crosses 1, and the cap does not zero that
carrier's oxygen demand. Transport-headspace pressures are not
rewritten. The closure opens only for the physical floor or a
reservoir source capacity at or under the noop floor.
``equilibrium.liquid_fraction`` is not that test. The free-molecular
host uses ``p_total_mbar = 0`` (production vacuum default) so the
5.3423 Pa anchor stays the ideal root. A positive total pressure is
its own case: the series film is inside the same residual.

Publisher film. When a root closed, the flux kernel receives that
root's partial vector and transport pressure. It does not rebuild
partials from the vapor-map keys: that rebuild drops synthetic O2,
so a commanded pressure below the overhead partial sum publishes a
vacuum film under a root that used the O2 gas film. A step that did
not close a root keeps the vapor-map backpressure. An explicit
override is filmed once and handed to both.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Mapping
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


@functools.lru_cache(maxsize=1)
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


def _bind_mixin_if_present(host, name: str) -> None:
    """Bind a mixin method only when this revision already defines it.

    Green (16c8dabfa) has the evaporation resolver and not the two
    Langmuir helpers. An unconditional bind raises AttributeError before
    any pressure assertion, so a transplant of this file cannot show the
    identity-map failure.
    """

    method = getattr(EvaporationMixin, name, None)
    if callable(method):
        setattr(host, name, method.__get__(host, EvaporationMixin))


def _host(
    compiled,
    legacy,
    diagnostic,
    pressures,
    *,
    redox_capacity_mol_per_ln_fO2: float | None,
    headspace_o2_mbar: float | None = None,
    liquid_fraction: float = 1.0,
    temperature_C: float = _T_C,
    p_total_mbar: float = 0.0,
    headspace_partials_mbar: dict[str, float] | None = None,
):
    equilibrium = EquilibriumResult(
        temperature_C=temperature_C,
        pressure_bar=0.01,
        liquid_fraction=liquid_fraction,
        status="ok",
        vapor_pressures_Pa=dict(pressures),
        warnings=[],
    )
    host = SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=temperature_C,
            melt_surface_area_m2=1.0,
            p_total_mbar=p_total_mbar,
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
            dict(headspace_partials_mbar)
            if headspace_partials_mbar is not None
            else (
                {}
                if headspace_o2_mbar is None
                else {"O2": float(headspace_o2_mbar)}
            )
        ),
        _resolve_evaporation_vapour_batch=_passthrough_batch,
    )
    def _source_capacity(*, fO2_log, T_K, **_ignored):
        del fO2_log, T_K
        if redox_capacity_mol_per_ln_fO2 is None:
            return None
        return float(redox_capacity_mol_per_ln_fO2)

    # Reservoir operand, the same one as ``no_melt_redox_capacity``.
    # Stubbing ``_melt_redox_capacity_mol_per_ln_fO2`` would leave the
    # gate on a missing measurement.
    host._melt_redox_source_capacity_mol_per_ln_fO2 = _source_capacity
    host._resolve_evaporation_batch_flux_state = (
        EvaporationMixin._resolve_evaporation_batch_flux_state.__get__(host)
    )
    host._evaporation_bulk_partial_pressure_pa = (
        EvaporationMixin._evaporation_bulk_partial_pressure_pa.__get__(host)
    )
    _bind_mixin_if_present(host, "_langmuir_overhead_pO2_bar")
    _bind_mixin_if_present(host, "_langmuir_melt_redox_capacity_mol_per_ln_fO2")
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


def _resolve(host, equilibrium, temperature_K: float = _T_K):
    pressures, *_rest = host._resolve_evaporation_batch_flux_state(
        equilibrium,
        temperature_K=temperature_K,
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


def test_overhead_po2_above_congruent_resolves_net_flux() -> None:
    """Overhead O2 above the vacuum root is inside the net-flux balance.

    Pure CaO is ``C_m ~ 0``. The free-molecular root is 2.386749e-5 bar
    and Ca is 5.3423 Pa. Headspace O2 at 1e-3 bar (100 Pa) is above that
    root and is already viscous, so the publisher film is on. Clamping
    the surface to 1e-3 bar leaves net O2 escape at zero while Ca still
    leaves (the old pin, about 0.825 Pa) and keeps the vacuum residual.
    The root here is recomputed from
    ``_series_resistance_evaporation_flux_kg_m2_s``, not from the
    solver's reported residual. Green keeps the published 825.3377 Pa.
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
    # The rejected clamp: surface fixed at the overhead, Ca on the power law.
    clamped_pa = published_ca * (_OVERHEAD_O2_BAR / p_ref_bar) ** (-0.5)
    assert clamped_pa < congruent_pa
    assert published_ca > clamped_pa

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
    o2_bulk_pa = _OVERHEAD_O2_BAR * PA_PER_BAR
    root_bar, root_ca = _series_cao_root(
        published_ca,
        p_ref_bar,
        bulk_o2_pa=o2_bulk_pa,
        bulk_ca_pa=0.0,
        overhead_pa=o2_bulk_pa,
        temperature_K=_T_K,
        legacy=legacy,
    )
    assert notice["overhead_bounded"] is True
    assert notice["surface_pO2_bar"] > _OVERHEAD_O2_BAR
    assert notice["surface_pO2_bar"] == pytest.approx(root_bar, rel=1.0e-6)
    assert notice["relative_residual"] < 1.0e-4
    assert resolved["Ca"] == pytest.approx(root_ca, rel=1.0e-6)
    assert resolved["Ca"] != pytest.approx(clamped_pa, rel=1.0e-3)
    assert resolved["Ca"] < clamped_pa
    residual = _series_cao_residual(
        notice["surface_pO2_bar"],
        published_ca,
        p_ref_bar,
        bulk_o2_pa=o2_bulk_pa,
        bulk_ca_pa=0.0,
        overhead_pa=o2_bulk_pa,
        temperature_K=_T_K,
        legacy=legacy,
        pressure_ca_pa=resolved["Ca"],
    )
    assert residual < 1.0e-4
    # The vacuum root's pressures are not closed once O2 has this bulk.
    vacuum_residual = _series_cao_residual(
        surface_pO2_bar,
        published_ca,
        p_ref_bar,
        bulk_o2_pa=o2_bulk_pa,
        bulk_ca_pa=0.0,
        overhead_pa=o2_bulk_pa,
        temperature_K=_T_K,
        legacy=legacy,
        pressure_ca_pa=congruent_pa,
    )
    assert vacuum_residual > 1.0e-3
    assert equilibrium.vapor_pressures_Pa["Ca"] == published_ca
    _assert_kernel_prices(host, equilibrium, resolved["Ca"])
    assert any(_SURFACE_REASON in item for item in equilibrium.warnings)


# Generative grid. 1900 C sits inside the Fe (1873-2373 K) and Ca
# (1200-2500 K) alpha bands, so the coefficients below are the catalog
# scalars rather than an extrapolation. Pressures are Pa. p_ref is bar.
_GEN_T_C = 1900.0
_GEN_T_K = _GEN_T_C + 273.15
_GEN_P_CA = 1.0e6
_GEN_P_FE = 1.0e4
_GEN_FE_RAW = 100.0
_GEN_P_SIO = 50.0
_GEN_P_SI = 25.0
_GEN_P_TIO2 = 10.0
_GEN_N_METAL = -0.5
_GEN_ALPHA_CA = 1.0
_GEN_ALPHA_FE = 0.02
_GEN_IN_BAND_PO2_BAR = 1.0e-9
_GEN_FLOOR_PO2_BAR = 1.0e-30
_GEN_TRANSPORT_PO2_BAR = 1.0e-8
_M_FE = parse_formula("Fe").molar_mass_kg_per_mol()
_GEN_EXTRAS = ("Fe", "SiO", "Si", "TiO2_gas")
# Published pressures that are not melt-surface carriers. SiO is the
# named transport channel. Si is transport_headspace and not that name.
# TiO2_gas has reaction exponent 0.
_GEN_UNCHANGED = ("SiO", "Si", "TiO2_gas")


def _gen_extra_subsets() -> list[tuple[str, ...]]:
    subsets: list[tuple[str, ...]] = []
    width = len(_GEN_EXTRAS)
    for mask in range(1 << width):
        subsets.append(tuple(
            name for bit, name in enumerate(_GEN_EXTRAS) if mask & (1 << bit)
        ))
    return subsets


def _closure_opens(
    reservoir_cm: float | None,
    *,
    at_floor: bool,
    has_melt_surface_carrier: bool,
) -> bool:
    """d-376 amendment 1, at the resolver gate.

    No melt-surface carrier means there is nothing to close: transport
    and neutral species stay published even at the physical floor.
    A missing reservoir capacity is not ``C_m ~ 0``.
    """

    if not has_melt_surface_carrier:
        return False
    if at_floor:
        return True
    if reservoir_cm is None:
        return False
    return float(reservoir_cm) <= float(OXYGEN_RESERVOIR_NOOP_MOL)


def _gen_published(
    extras: tuple[str, ...],
    *,
    at_floor: bool,
    include_ca: bool,
) -> tuple[dict[str, float], dict, float]:
    p_ref = _GEN_FLOOR_PO2_BAR if at_floor else _GEN_IN_BAND_PO2_BAR
    pressures: dict[str, float] = {}
    provenance: dict[str, dict] = {}
    if include_ca:
        pressures["Ca"] = _GEN_P_CA
        provenance["Ca"] = {
            "pressure_rail": "gas_fugacity",
            "pO2_bar": p_ref,
            "activity_factor": 0.2,
            "raw_metal_activity_root": 0.2,
        }
    if "Fe" in extras:
        pressures["Fe"] = _GEN_P_FE
        provenance["Fe"] = {
            "pressure_rail": "condensed_raoult_psat",
            "pO2_bar": p_ref,
            "activity_factor": 1.0,
            "raw_metal_activity_root": _GEN_FE_RAW,
        }
    if "SiO" in extras:
        pressures["SiO"] = _GEN_P_SIO
        provenance["SiO"] = {
            "oxygen_fugacity_channel": "transport_headspace",
            "pO2_bar": _GEN_TRANSPORT_PO2_BAR,
            "pO2_exponent": -0.5,
        }
    if "Si" in extras:
        pressures["Si"] = _GEN_P_SI
        provenance["Si"] = {
            "pressure_rail": "liquid_oxide_standard_reaction",
            "oxygen_fugacity_channel": "transport_headspace",
            "pO2_bar": _GEN_TRANSPORT_PO2_BAR,
            "pO2_exponent": -1.0,
        }
    if "TiO2_gas" in extras:
        pressures["TiO2_gas"] = _GEN_P_TIO2
        provenance["TiO2_gas"] = {
            "oxygen_fugacity_channel": "intrinsic_melt",
            "pO2_bar": _GEN_TRANSPORT_PO2_BAR,
            "pO2_exponent": 0.0,
        }
    diagnostic = {
        "source_reaction_fO2_bar": p_ref,
        "pO2_bar": _GEN_TRANSPORT_PO2_BAR,
        "vapor_pressure_numerator_provenance": provenance,
    }
    return pressures, diagnostic, p_ref


def _gen_law_pressure(
    published_pa: float,
    p_ref_bar: float,
    surface_pO2_bar: float,
    *,
    raw_activity: float | None = None,
) -> float:
    """Mass-action pressure, with the condensed-metal cap released above p_uncap.

    Off the cap, ``P = P_pub * (p / p_ref)^n``. On a condensed rail the
    published pressure is P_sat and the uncapped continuation is
    ``P = P_sat * raw * (p / p_ref)^n``, which is ``P_sat * 10^log_raw``
    once ``log_raw = log10(raw) + n*log10(p/p_ref)`` drops below 0.
    ``n = -1/2`` for Fe and Ca. Sanity: raw=100 gives
    ``p_uncap / p_ref = 1e4``.
    """

    log_ratio = _GEN_N_METAL * math.log10(surface_pO2_bar / p_ref_bar)
    if raw_activity is None:
        return 10.0 ** (math.log10(published_pa) + log_ratio)
    log_raw = math.log10(raw_activity) + log_ratio
    if log_raw >= 0.0:
        return published_pa
    return 10.0 ** (math.log10(published_pa) + log_raw)


def _gen_oxygen_parts(
    surface_pO2_bar: float,
    p_ref_bar: float,
    *,
    include_ca: bool,
    include_fe: bool,
    fe_demand: bool,
) -> tuple[float, float]:
    """Proportional oxygen-atom and parent-demand terms.

    The Hertz-Knudsen ``1/sqrt(2*pi*R*T)`` cancels. Metal gas has
    ``nO = 0`` and reaction ``n = -1/2``, so ``dO = nO - 2*n = 1`` and
    the signed weight is ``-1``. O2 has ``nO = 2``, ``dO = 0``. Forcing
    ``fe_demand`` false is the capped-rail bug: the local slope is 0,
    and using that slope as stoichiometry drops Fe's parent oxygen.
    """

    oxygen = 2.0 * (surface_pO2_bar * PA_PER_BAR) / math.sqrt(_M_O2)
    parent = 0.0
    if include_ca:
        pressure = _gen_law_pressure(
            _GEN_P_CA, p_ref_bar, surface_pO2_bar
        )
        parent += _GEN_ALPHA_CA * pressure / math.sqrt(_M_CA)
    if include_fe and fe_demand:
        pressure = _gen_law_pressure(
            _GEN_P_FE,
            p_ref_bar,
            surface_pO2_bar,
            raw_activity=_GEN_FE_RAW,
        )
        parent += _GEN_ALPHA_FE * pressure / math.sqrt(_M_FE)
    return oxygen, parent


def _gen_relative_residual(
    surface_pO2_bar: float,
    p_ref_bar: float,
    *,
    include_ca: bool,
    include_fe: bool,
    fe_demand: bool,
) -> float:
    oxygen, parent = _gen_oxygen_parts(
        surface_pO2_bar,
        p_ref_bar,
        include_ca=include_ca,
        include_fe=include_fe,
        fe_demand=fe_demand,
    )
    return abs(oxygen - parent) / max(oxygen, parent, 1.0e-300)


def _gen_surface_pO2_bar(
    p_ref_bar: float,
    *,
    include_ca: bool,
    include_fe: bool,
) -> float:
    """Independent root. Does not call the production balance."""

    from scipy.optimize import brentq

    def residual(log_p: float) -> float:
        oxygen, parent = _gen_oxygen_parts(
            10.0 ** log_p,
            p_ref_bar,
            include_ca=include_ca,
            include_fe=include_fe,
            fe_demand=True,
        )
        return oxygen - parent

    low = residual(-30.0)
    high = residual(0.0)
    assert low < 0.0 < high
    root = brentq(residual, -30.0, 0.0, xtol=1.0e-12, rtol=1.0e-12, maxiter=200)
    return 10.0 ** root


def _assert_langmuir_invariant(
    *,
    extras: tuple[str, ...],
    reservoir_cm: float | None,
    at_floor: bool,
    liquid_fraction: float,
    include_ca: bool,
) -> None:
    published, diagnostic, p_ref = _gen_published(
        extras,
        at_floor=at_floor,
        include_ca=include_ca,
    )
    include_fe = "Fe" in extras
    opens = _closure_opens(
        reservoir_cm,
        at_floor=at_floor,
        has_melt_surface_carrier=include_ca or include_fe,
    )
    compiled, legacy = _catalog()
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=reservoir_cm,
        liquid_fraction=liquid_fraction,
        temperature_C=_GEN_T_C,
    )
    resolved = _resolve(host, equilibrium, temperature_K=_GEN_T_K)
    assert set(resolved) == set(published)
    for species, pressure in resolved.items():
        assert pressure > 0.0
        assert math.isfinite(pressure)
    for species in _GEN_UNCHANGED:
        if species in published:
            assert resolved[species] == published[species]

    if not opens:
        assert resolved == published
        assert not hasattr(host, "_last_langmuir_surface_oxygen")
        return

    surface = _gen_surface_pO2_bar(
        p_ref,
        include_ca=include_ca,
        include_fe=include_fe,
    )
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["refused_species"] == {}
    assert notice["capacity_negligible"] is (
        reservoir_cm is not None
        and float(reservoir_cm) <= float(OXYGEN_RESERVOIR_NOOP_MOL)
    )
    assert notice["eval_pO2_below_physical_floor"] is at_floor
    assert notice["surface_pO2_bar"] == pytest.approx(surface, rel=1.0e-6)
    assert notice["relative_residual"] < 1.0e-4
    assert _gen_relative_residual(
        notice["surface_pO2_bar"],
        p_ref,
        include_ca=include_ca,
        include_fe=include_fe,
        fe_demand=True,
    ) < 1.0e-4
    if include_ca:
        expected_ca = _gen_law_pressure(_GEN_P_CA, p_ref, surface)
        assert resolved["Ca"] == pytest.approx(expected_ca, rel=1.0e-6)
        assert resolved["Ca"] != pytest.approx(published["Ca"], rel=1.0e-3)
    if include_fe:
        # raw=100, n=-1/2: the cap releases at p_ref * 1e4, below this root.
        p_uncap = p_ref * (_GEN_FE_RAW ** (-1.0 / _GEN_N_METAL))
        assert surface > p_uncap
        expected_fe = _gen_law_pressure(
            _GEN_P_FE,
            p_ref,
            surface,
            raw_activity=_GEN_FE_RAW,
        )
        assert expected_fe < published["Fe"]
        assert resolved["Fe"] == pytest.approx(expected_fe, rel=1.0e-6)
        assert resolved["Fe"] < published["Fe"]
        assert _gen_relative_residual(
            notice["surface_pO2_bar"],
            p_ref,
            include_ca=include_ca,
            include_fe=True,
            fe_demand=False,
        ) > 1.0e-3


def test_generative_alphas_match_catalog_scalars() -> None:
    """The mixture algebra uses the in-band Fe and Ca catalog coefficients."""

    from engines.builtin.evaporation_flux import _evaluate_alpha_control
    from simulator.evaporation import (
        _load_evaporation_alpha_by_species,
        _measured_alpha_control_view,
    )

    _compiled, legacy = _catalog()
    alphas = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(dict(legacy))
    )
    calcium, _ca_eval = _evaluate_alpha_control("Ca", _GEN_T_K, alphas["Ca"])
    iron, _fe_eval = _evaluate_alpha_control("Fe", _GEN_T_K, alphas["Fe"])
    assert float(calcium) == _GEN_ALPHA_CA
    assert float(iron) == _GEN_ALPHA_FE
    assert _GEN_T_K == pytest.approx(2173.15)


@pytest.mark.parametrize(
    "reservoir_cm",
    [None, 0.0, 1.0e-15, 1.0e-14, 1.0],
    ids=["missing", "zero", "noop", "above-noop", "buffered"],
)
@pytest.mark.parametrize("at_floor", [False, True], ids=["in-band", "floor"])
@pytest.mark.parametrize("liquid_fraction", [0.0, 0.25, 1.0])
def test_surface_gate_follows_reservoir_capacity_not_liquid_fraction(
    reservoir_cm: float | None,
    at_floor: bool,
    liquid_fraction: float,
) -> None:
    """Closure follows the reservoir noop test, not equilibrium.liquid_fraction.

    The map always contains a capped Fe rail and both transport channels.
    A gate that multiplies source capacity by the equilibrium liquid
    fraction opens at liquid_fraction 0 even when the reservoir capacity
    is 1. The floor arm must not rewrite Si, SiO, or TiO2_gas.
    """

    _assert_langmuir_invariant(
        extras=_GEN_EXTRAS,
        reservoir_cm=reservoir_cm,
        at_floor=at_floor,
        liquid_fraction=liquid_fraction,
        include_ca=True,
    )


@pytest.mark.parametrize(
    "extras",
    _gen_extra_subsets(),
    ids=lambda extras: "+".join(extras) or "none",
)
@pytest.mark.parametrize(
    "include_ca",
    [True, False],
    ids=["with-ca", "no-ca"],
)
@pytest.mark.parametrize(
    ("mode", "reservoir_cm", "at_floor"),
    [
        ("closed", 1.0, False),
        ("open-capacity", 0.0, False),
        ("open-floor", 1.0, True),
    ],
    ids=["closed", "open-capacity", "open-floor"],
)
@pytest.mark.parametrize("liquid_fraction", [0.0, 1.0])
def test_every_species_is_published_surface_law_or_refusal(
    extras: tuple[str, ...],
    include_ca: bool,
    mode: str,
    reservoir_cm: float,
    at_floor: bool,
    liquid_fraction: float,
) -> None:
    """Every carrier class is published, the shared surface law, or a refusal.

    Subsets cover capped Fe, transport SiO, transport Si (not the SiO
    name), and neutral TiO2_gas, with and without the uncapped Ca rail.
    ``mode`` is the closed buffer, the empty-buffer opening, and the
    physical-floor opening. Green returns the published map on all of
    these, so an open case fails on the identity pressure.
    """

    del mode
    _assert_langmuir_invariant(
        extras=extras,
        reservoir_cm=reservoir_cm,
        at_floor=at_floor,
        liquid_fraction=liquid_fraction,
        include_ca=include_ca,
    )


def test_unsolvable_surface_refuses_inverted_carriers_without_silent_zero() -> None:
    """A failed root omits below-band carriers and names the reason.

    Transport and neutral pressures stay published. Nothing in the map
    is replaced with 0.
    """

    published, diagnostic, _p_ref = _gen_published(
        _GEN_EXTRAS,
        at_floor=True,
        include_ca=True,
    )
    compiled, legacy = _catalog()
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=1.0,
        liquid_fraction=0.0,
        temperature_C=_GEN_T_C,
    )
    resolved = _resolve(host, equilibrium, temperature_K=float("nan"))
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["surface_pO2_bar"] is None
    assert notice["eval_pO2_below_physical_floor"] is True
    for species in ("Ca", "Fe"):
        assert species not in resolved
        reason = notice["refused_species"][species]
        assert reason
        assert "0.0" not in reason
    for species in _GEN_UNCHANGED:
        assert resolved[species] == published[species]
        assert resolved[species] > 0.0
    for pressure in resolved.values():
        assert pressure > 0.0


def _catalog_alpha(legacy, species: str, temperature_K: float) -> float:
    from engines.builtin.evaporation_flux import _evaluate_alpha_control
    from simulator.evaporation import (
        _load_evaporation_alpha_by_species,
        _measured_alpha_control_view,
    )

    alphas = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(dict(legacy))
    )
    value, _evaluation = _evaluate_alpha_control(
        species, temperature_K, alphas[species]
    )
    return float(value)


def _ideal_molar_k(alpha: float, molar_mass: float, temperature_K: float) -> float:
    """``alpha / sqrt(2*pi*M*R*T)``, mol s^-1 m^-2 Pa^-1. Not the solver."""

    from simulator.physical_constants import GAS_CONSTANT

    return alpha / math.sqrt(
        2.0 * math.pi * GAS_CONSTANT * temperature_K * molar_mass
    )


def _series_molar_flux(
    *,
    species: str,
    pressure_pa: float,
    bulk_pa: float,
    molar_mass: float,
    alpha: float,
    temperature_K: float,
    overhead_pa: float,
) -> float:
    """Publisher series flux in mol m^-2 s^-1. Not the surface solver."""

    from engines.builtin.evaporation_flux import (
        _series_resistance_evaporation_flux_kg_m2_s,
    )

    probed = _series_resistance_evaporation_flux_kg_m2_s(
        species=species,
        P_eq_pa=pressure_pa,
        P_bulk_pa=bulk_pa,
        T_surface_K=temperature_K,
        molar_mass_kg_mol=molar_mass,
        alpha_i=alpha,
        pipe_diameter_m=0.12,
        overhead_pressure_pa=overhead_pa,
        axial_stir_factor=0.0,
        radial_stir_factor=0.0,
        carrier_gas="N2",
        T_gas_K=temperature_K,
        gas_resistance_enabled=True,
        extrapolate_continuum=False,
        melt_resistance_enabled=False,
    )
    return float(probed.flux_kg_s_m2) / molar_mass


def _series_cao_signed(
    surface_pO2_bar: float,
    published_ca: float,
    p_ref_bar: float,
    *,
    bulk_o2_pa: float,
    bulk_ca_pa: float,
    overhead_pa: float,
    temperature_K: float,
    alpha_ca: float,
    pressure_ca_pa: float | None = None,
) -> tuple[float, float, float]:
    """Return ``(2*J_O2 - J_Ca, J_Ca, J_O2)`` from the publisher series law.

    Ca weight is ``nO - dO = -1``. O2 weight is ``+2``. The common
    temperature factor does not cancel once the film resistances differ.
    """

    if pressure_ca_pa is None:
        pressure_ca_pa = published_ca * (surface_pO2_bar / p_ref_bar) ** (-0.5)
    pressure_o2_pa = surface_pO2_bar * PA_PER_BAR
    j_ca = _series_molar_flux(
        species="Ca",
        pressure_pa=pressure_ca_pa,
        bulk_pa=bulk_ca_pa,
        molar_mass=_M_CA,
        alpha=alpha_ca,
        temperature_K=temperature_K,
        overhead_pa=overhead_pa,
    )
    j_o2 = _series_molar_flux(
        species="O2",
        pressure_pa=pressure_o2_pa,
        bulk_pa=bulk_o2_pa,
        molar_mass=_M_O2,
        alpha=1.0,
        temperature_K=temperature_K,
        overhead_pa=overhead_pa,
    )
    return 2.0 * j_o2 - j_ca, j_ca, j_o2


def _series_cao_residual(
    surface_pO2_bar: float,
    published_ca: float,
    p_ref_bar: float,
    *,
    bulk_o2_pa: float,
    bulk_ca_pa: float,
    overhead_pa: float,
    temperature_K: float,
    legacy,
    pressure_ca_pa: float | None = None,
) -> float:
    alpha_ca = _catalog_alpha(legacy, "Ca", temperature_K)
    signed, j_ca, j_o2 = _series_cao_signed(
        surface_pO2_bar,
        published_ca,
        p_ref_bar,
        bulk_o2_pa=bulk_o2_pa,
        bulk_ca_pa=bulk_ca_pa,
        overhead_pa=overhead_pa,
        temperature_K=temperature_K,
        alpha_ca=alpha_ca,
        pressure_ca_pa=pressure_ca_pa,
    )
    return abs(signed) / max(j_ca, 2.0 * j_o2, 1.0e-300)


def _series_cao_root(
    published_ca: float,
    p_ref_bar: float,
    *,
    bulk_o2_pa: float,
    bulk_ca_pa: float,
    overhead_pa: float,
    temperature_K: float,
    legacy,
) -> tuple[float, float]:
    """Independent Ca+O2 root on the publisher series law."""

    from scipy.optimize import brentq

    alpha_ca = _catalog_alpha(legacy, "Ca", temperature_K)

    def signed(log_p: float) -> float:
        return _series_cao_signed(
            10.0 ** log_p,
            published_ca,
            p_ref_bar,
            bulk_o2_pa=bulk_o2_pa,
            bulk_ca_pa=bulk_ca_pa,
            overhead_pa=overhead_pa,
            temperature_K=temperature_K,
            alpha_ca=alpha_ca,
        )[0]

    low = signed(-30.0)
    high = signed(0.0)
    assert low < 0.0 < high
    log_root = brentq(signed, -30.0, 0.0, xtol=1.0e-12, rtol=1.0e-12, maxiter=200)
    surface = 10.0 ** log_root
    pressure = published_ca * (surface / p_ref_bar) ** (-0.5)
    return surface, pressure


def _assert_viscous_film(overhead_pa: float, temperature_K: float) -> None:
    from simulator.condensation import _knudsen_number
    from simulator.transport_constants import VISCOUS_KNUDSEN_MAX

    knudsen = _knudsen_number(
        overhead_pa, temperature_K, 0.12, carrier_gas="N2"
    )
    assert knudsen < VISCOUS_KNUDSEN_MAX


def test_viscous_total_pressure_uses_publisher_film() -> None:
    """A 10 mbar control floor is viscous. The film is inside the residual.

    Free-molecular closure at ``p_total_mbar = 0`` stays at 5.3423 Pa.
    The same publication at 10 mbar (1000 Pa, the recipe control floor)
    does not: Ca and O2 no longer share one Hertz-Knudsen factor.
    """

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar)
    overhead_pa = 10.0 * 100.0
    _assert_viscous_film(overhead_pa, _T_K)
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        p_total_mbar=10.0,
    )
    resolved = _resolve(host, equilibrium)
    notice = host._last_langmuir_surface_oxygen
    root_bar, root_pa = _series_cao_root(
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=0.0,
        overhead_pa=overhead_pa,
        temperature_K=_T_K,
        legacy=legacy,
    )
    assert resolved["Ca"] == pytest.approx(root_pa, rel=1.0e-6)
    assert resolved["Ca"] != pytest.approx(ideal_pa, rel=1.0e-3)
    assert notice["surface_pO2_bar"] == pytest.approx(root_bar, rel=1.0e-6)
    assert notice["relative_residual"] < 1.0e-4
    assert notice["overhead_bounded"] is False
    assert _series_cao_residual(
        notice["surface_pO2_bar"],
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=0.0,
        overhead_pa=overhead_pa,
        temperature_K=_T_K,
        legacy=legacy,
        pressure_ca_pa=resolved["Ca"],
    ) < 1.0e-4
    alpha_ca = _catalog_alpha(legacy, "Ca", _T_K)
    k_ca = _ideal_molar_k(alpha_ca, _M_CA, _T_K)
    k_o2 = _ideal_molar_k(1.0, _M_O2, _T_K)
    ideal_signed = (
        2.0 * k_o2 * (notice["surface_pO2_bar"] * PA_PER_BAR)
        - k_ca * resolved["Ca"]
    )
    ideal_scale = max(
        k_ca * resolved["Ca"],
        2.0 * k_o2 * notice["surface_pO2_bar"] * PA_PER_BAR,
        1.0e-300,
    )
    assert abs(ideal_signed) / ideal_scale > 1.0e-3
    # d-376 round 5. Diffusive k_O2/k_Ca at 10 mbar, not the effusion
    # weight, is the +8.4% Ca-pressure move. p_total = 0 is the other ratio.
    k_ca_film = _series_molar_flux(
        species="Ca",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_CA,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    k_o2_film = _series_molar_flux(
        species="O2",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_O2,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    k_ca_free = _series_molar_flux(
        species="Ca",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_CA,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=0.0,
    )
    k_o2_free = _series_molar_flux(
        species="O2",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_O2,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=0.0,
    )
    assert k_o2_film / k_ca_film == pytest.approx(1.42427779844656, rel=1.0e-6)
    assert k_o2_free / k_ca_free == pytest.approx(
        math.sqrt(_M_CA / _M_O2), rel=1.0e-12
    )
    assert root_pa / ideal_pa - 1.0 == pytest.approx(0.08368, rel=1.0e-3)


def test_live_override_and_reclose_use_that_partial_vector() -> None:
    """Each partial vector closes on itself. A stale map does not.

    Paths through the one writer: persisted headspace via resolve,
    a later vector via ``_reclose_langmuir_pressures``, and the live
    override via ``_calculate_evaporation``. The vacuum publication is
    not closed at 100 Pa of O2. Closing on that vector is.
    """

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar)
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
    )
    vacuum = _resolve(host, equilibrium)
    assert vacuum["Ca"] == pytest.approx(ideal_pa, rel=1.0e-6)
    o2_bulk_pa = _OVERHEAD_O2_BAR * PA_PER_BAR
    _assert_viscous_film(o2_bulk_pa, _T_K)
    root_bar, root_pa = _series_cao_root(
        published_ca,
        p_ref_bar,
        bulk_o2_pa=o2_bulk_pa,
        bulk_ca_pa=0.0,
        overhead_pa=o2_bulk_pa,
        temperature_K=_T_K,
        legacy=legacy,
    )
    stale = _series_cao_residual(
        host._last_langmuir_surface_oxygen["surface_pO2_bar"],
        published_ca,
        p_ref_bar,
        bulk_o2_pa=o2_bulk_pa,
        bulk_ca_pa=0.0,
        overhead_pa=o2_bulk_pa,
        temperature_K=_T_K,
        legacy=legacy,
        pressure_ca_pa=vacuum["Ca"],
    )
    assert stale > 1.0e-3
    override = {"O2": o2_bulk_pa}
    reclosed = EvaporationMixin._reclose_langmuir_pressures(host, override)
    assert reclosed is not None
    assert reclosed.surface_pO2_bar == pytest.approx(root_bar, rel=1.0e-6)
    assert reclosed.pressures_Pa["Ca"] == pytest.approx(root_pa, rel=1.0e-6)
    assert reclosed.pressures_Pa["Ca"] != pytest.approx(ideal_pa, rel=1.0e-3)
    assert reclosed.relative_residual < 1.0e-4
    # The re-close must not replace the resolver's diagnostic.
    assert host._last_langmuir_surface_oxygen["surface_pO2_bar"] == pytest.approx(
        _surface, rel=1.0e-6
    )
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
    EvaporationMixin._calculate_evaporation(
        host,
        equilibrium,
        overhead_partials_override_Pa=override,
    )
    live_ca = captured["controls"][CONTROL_FLUX_PRESSURES_KEY]["Ca"]
    assert live_ca == pytest.approx(root_pa, rel=1.0e-6)
    assert live_ca != pytest.approx(ideal_pa, rel=1.0e-3)
    assert captured["controls"]["overhead_partials_Pa"]["O2"] == pytest.approx(
        o2_bulk_pa
    )


def test_metal_backpressure_is_inside_the_host_balance() -> None:
    """A Ca bulk the vacuum root cannot clear moves the root until it can."""

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar)
    # 0.5 mbar = 50 Pa. Above the 5.34 Pa free-molecular root, and viscous.
    bulk_ca_pa = 0.5 * 100.0
    _assert_viscous_film(bulk_ca_pa, _T_K)
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        headspace_partials_mbar={"Ca": 0.5},
    )
    resolved = _resolve(host, equilibrium)
    notice = host._last_langmuir_surface_oxygen
    root_bar, root_pa = _series_cao_root(
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=bulk_ca_pa,
        overhead_pa=bulk_ca_pa,
        temperature_K=_T_K,
        legacy=legacy,
    )
    assert resolved["Ca"] == pytest.approx(root_pa, rel=1.0e-6)
    assert resolved["Ca"] > bulk_ca_pa
    assert resolved["Ca"] != pytest.approx(ideal_pa, rel=1.0e-3)
    assert notice["surface_pO2_bar"] == pytest.approx(root_bar, rel=1.0e-6)
    assert notice["relative_residual"] < 1.0e-4
    assert notice["overhead_bounded"] is False
    # The drive is P_Ca - 50 Pa, about 0.075 Pa. A 1e-6 relative miss on
    # P_Ca is a much larger miss on that drive, so the flux residual is
    # checked at the independent root itself. The solver lands on that root.
    assert _series_cao_residual(
        root_bar,
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=bulk_ca_pa,
        overhead_pa=bulk_ca_pa,
        temperature_K=_T_K,
        legacy=legacy,
    ) < 1.0e-4
    clipped = _series_cao_residual(
        _surface,
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=bulk_ca_pa,
        overhead_pa=bulk_ca_pa,
        temperature_K=_T_K,
        legacy=legacy,
        pressure_ca_pa=ideal_pa,
    )
    assert clipped > 1.0e-3


def test_subunit_condensed_metal_caps_when_surface_lowers_po2() -> None:
    """A no-buffer solve that lowers pO2 cannot publish Fe above P_sat.

    Published Fe is subunit: raw = 0.25, P = 1000 Pa, P_sat = 4000 Pa,
    p_ref = 1e-2 bar. The cap binds at ``p <= p_ref * raw**2`` because
    ``n = -1/2``. The oxygen balance at p_ref is oxygen-heavy, so the
    root drops through that threshold. The ordinary power law would
    exceed P_sat. The published pressure is P_sat, and Fe's parent
    oxygen still enters the residual (otherwise the root would sit on
    the uncapped law).
    """

    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    compiled, legacy = _catalog()
    p_ref = 1.0e-2
    raw = 0.25
    p_sat = 4000.0
    published_fe = raw * p_sat
    result = apply_langmuir_surface_oxygen(
        {"Fe": published_fe},
        temperature_K=_GEN_T_K,
        provenance_by_species={
            "Fe": {
                "pressure_rail": "condensed_raoult_psat",
                "pO2_bar": p_ref,
                "activity_factor": raw,
                "raw_metal_activity_root": raw,
                "P_reference_Antoine_Pa": p_sat,
            }
        },
        source_reaction_fO2_bar=p_ref,
        legacy_catalog=legacy,
        compiled_catalog=compiled,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
    )
    surface = result.surface_pO2_bar
    assert surface is not None
    assert result.refused_species == {}
    p_uncap = p_ref * (raw ** 2.0)
    assert surface < p_uncap
    uncapped = p_sat * raw * (surface / p_ref) ** (-0.5)
    assert uncapped > p_sat
    assert result.pressures_Pa["Fe"] == pytest.approx(p_sat, rel=1.0e-6)
    assert result.pressures_Pa["Fe"] < uncapped
    alpha_fe = _catalog_alpha(legacy, "Fe", _GEN_T_K)
    k_fe = _ideal_molar_k(alpha_fe, _M_FE, _GEN_T_K)
    k_o2 = _ideal_molar_k(1.0, _M_O2, _GEN_T_K)
    j_fe = k_fe * p_sat
    j_o2 = k_o2 * surface * PA_PER_BAR
    # dO_Fe = 1, so the residual is 2*J_O2 - J_Fe. Dropping Fe's demand
    # would leave 2*J_O2, which does not close.
    assert abs(2.0 * j_o2 - j_fe) / max(j_fe, 2.0 * j_o2) < 1.0e-4
    assert j_o2 / max(j_fe, 1.0e-300) > 1.0e-4
    assert result.relative_residual < 1.0e-4


_NET_SPECIES_SETS = (
    "ca",
    "ca-fe-capped",
    "fe-cross",
    "ca-sio-tio2",
    "channels-only",
)
_NET_PARTIALS = ("none", "o2-high", "o2-low", "metal", "both")
_NET_LAWS = ("ideal", "scaled")
_NET_GATES = ("closed", "open-capacity", "open-floor")


def _net_gate(mode: str) -> tuple[float, bool, float, float]:
    """Return capacity, at_floor, source pO2, and the metal law's p_ref.

    The floor gate opens because the evaluated source pO2 is 1e-30 bar.
    The published metal pressures stay the in-band law at 1e-9 bar.
    Publishing 1e6 Pa at 1e-30 bar is not a mass-action floor (that
    publication is ~1e13 Pa) and the balance then refuses. Keeping the
    law reference in band makes every partial class solvable, and the
    carriers are not below-band refusals.
    """

    if mode == "closed":
        return 1.0, False, _GEN_IN_BAND_PO2_BAR, _GEN_IN_BAND_PO2_BAR
    if mode == "open-capacity":
        return 0.0, False, _GEN_IN_BAND_PO2_BAR, _GEN_IN_BAND_PO2_BAR
    if mode == "open-floor":
        return 1.0, True, _GEN_FLOOR_PO2_BAR, _GEN_IN_BAND_PO2_BAR
    raise AssertionError(mode)


def _net_specs(species_set: str, p_ref_bar: float) -> list[dict]:
    """Carrier classes the closure has to tell apart.

    ``fe-cross`` keeps its own reference at 1e-2 bar so a falling
    surface can drive the subunit activity through 1. The other metals
    use the gate's publication pO2. SiO is transport. TiO2_gas is the
    neutral exponent.
    """

    specs: list[dict] = []
    if species_set in ("ca", "ca-fe-capped", "ca-sio-tio2"):
        specs.append({
            "species": "Ca",
            "kind": "gas",
            "published": _GEN_P_CA,
            "p_ref": p_ref_bar,
            "exponent": _GEN_N_METAL,
            "alpha": _GEN_ALPHA_CA,
            "molar_mass": _M_CA,
            "n_o": 0.0,
            "scale": 3.0,
        })
    if species_set == "ca-fe-capped":
        specs.append({
            "species": "Fe",
            "kind": "capped",
            "published": _GEN_P_FE,
            "p_sat": _GEN_P_FE,
            "raw": _GEN_FE_RAW,
            "p_ref": p_ref_bar,
            "exponent": _GEN_N_METAL,
            "alpha": _GEN_ALPHA_FE,
            "molar_mass": _M_FE,
            "n_o": 0.0,
            "scale": 2.0,
        })
    if species_set == "fe-cross":
        specs.append({
            "species": "Fe",
            "kind": "capped",
            "published": 1000.0,
            "p_sat": 4000.0,
            "raw": 0.25,
            "p_ref": 1.0e-2,
            "exponent": _GEN_N_METAL,
            "alpha": _GEN_ALPHA_FE,
            "molar_mass": _M_FE,
            "n_o": 0.0,
            "scale": 2.0,
        })
    if species_set in ("ca-sio-tio2", "channels-only"):
        specs.append({
            "species": "SiO",
            "kind": "transport",
            "published": _GEN_P_SIO,
            "p_ref": _GEN_TRANSPORT_PO2_BAR,
            "exponent": -0.5,
            "alpha": 1.0,
            "molar_mass": parse_formula("SiO").molar_mass_kg_per_mol(),
            "n_o": 1.0,
            "scale": 1.0,
        })
        specs.append({
            "species": "TiO2_gas",
            "kind": "neutral",
            "published": _GEN_P_TIO2,
            "p_ref": p_ref_bar,
            "exponent": 0.0,
            "alpha": 1.0,
            "molar_mass": parse_formula("TiO2").molar_mass_kg_per_mol(),
            "n_o": 2.0,
            "scale": 1.0,
        })
    return specs


def _net_provenance(spec: dict) -> dict:
    if spec["kind"] == "transport":
        return {
            "oxygen_fugacity_channel": "transport_headspace",
            "pO2_bar": spec["p_ref"],
            "pO2_exponent": spec["exponent"],
        }
    if spec["kind"] == "neutral":
        return {
            "oxygen_fugacity_channel": "intrinsic_melt",
            "pO2_bar": spec["p_ref"],
            "pO2_exponent": 0.0,
        }
    if spec["kind"] == "capped":
        return {
            "pressure_rail": "condensed_raoult_psat",
            "pO2_bar": spec["p_ref"],
            "activity_factor": 1.0 if spec["raw"] > 1.0 else spec["raw"],
            "raw_metal_activity_root": spec["raw"],
            "P_reference_Antoine_Pa": spec["p_sat"],
        }
    return {
        "pressure_rail": "gas_fugacity",
        "pO2_bar": spec["p_ref"],
        "activity_factor": 0.2,
        "raw_metal_activity_root": 0.2,
    }


def _net_partials(mode: str, specs: list[dict]) -> dict[str, float]:
    metals = {
        spec["species"]: 1.0
        for spec in specs
        if spec["kind"] in ("gas", "capped")
    }
    if mode == "none":
        return {}
    if mode == "o2-high":
        return {"O2": 1000.0}
    if mode == "o2-low":
        return {"O2": 1.0}
    if mode == "metal":
        return metals
    if mode == "both":
        return {"O2": 100.0, **metals}
    raise AssertionError(mode)


def _net_expected_pressure(spec: dict, surface_pO2_bar: float) -> float:
    if spec["kind"] in ("transport", "neutral"):
        return spec["published"]
    exponent = spec["exponent"]
    if spec["kind"] == "gas":
        return spec["published"] * (surface_pO2_bar / spec["p_ref"]) ** exponent
    log_raw = math.log10(spec["raw"]) + exponent * math.log10(
        surface_pO2_bar / spec["p_ref"]
    )
    if log_raw >= 0.0:
        return spec["p_sat"]
    return spec["p_sat"] * (10.0 ** log_raw)


def _net_conductance(
    specs: list[dict],
    *,
    scaled: bool,
) -> dict[str, float]:
    conductance: dict[str, float] = {}
    for spec in specs:
        if spec["kind"] == "transport":
            continue
        value = _ideal_molar_k(spec["alpha"], spec["molar_mass"], _GEN_T_K)
        if scaled:
            value *= spec["scale"]
        conductance[spec["species"]] = value
    o2 = _ideal_molar_k(1.0, _M_O2, _GEN_T_K)
    conductance["O2"] = o2 * (0.4 if scaled else 1.0)
    return conductance


def _net_relative(
    surface_pO2_bar: float,
    specs: list[dict],
    partials: dict[str, float],
    conductance: dict[str, float],
    *,
    pressures: dict[str, float] | None = None,
) -> tuple[float, float]:
    """Independent ``|sum (nO-dO) J| / scale`` and the O2 molar flux.

    ``J = k * max(0, P - P_bulk)``. Transport channels are not in the sum.
    """

    oxygen = 0.0
    parent = 0.0
    for spec in specs:
        if spec["kind"] == "transport":
            continue
        if pressures is None:
            pressure = _net_expected_pressure(spec, surface_pO2_bar)
        else:
            pressure = pressures[spec["species"]]
        bulk = partials.get(spec["species"], 0.0)
        flux = conductance[spec["species"]] * max(0.0, pressure - bulk)
        demand = spec["n_o"] - 2.0 * spec["exponent"]
        oxygen += spec["n_o"] * flux
        parent += demand * flux
    o2_pressure = surface_pO2_bar * PA_PER_BAR
    o2_bulk = partials.get("O2", 0.0)
    j_o2 = conductance["O2"] * max(0.0, o2_pressure - o2_bulk)
    oxygen += 2.0 * j_o2
    return abs(oxygen - parent) / max(oxygen, parent, 1.0e-300), j_o2


def _net_opens(specs: list[dict], capacity: float, at_floor: bool) -> bool:
    participating = any(
        spec["kind"] in ("gas", "capped") for spec in specs
    )
    if not participating:
        return False
    if at_floor:
        return True
    return capacity <= float(OXYGEN_RESERVOIR_NOOP_MOL)


@pytest.mark.parametrize("species_set", _NET_SPECIES_SETS)
@pytest.mark.parametrize("partial_mode", _NET_PARTIALS)
@pytest.mark.parametrize("law", _NET_LAWS)
@pytest.mark.parametrize("gate", _NET_GATES)
def test_net_flux_invariant_over_species_partials_and_conductance(
    species_set: str,
    partial_mode: str,
    law: str,
    gate: str,
) -> None:
    """The chokepoint residual is the net flux of every carrier class.

    Species classes: uncapped Ca, already-capped Fe, subunit Fe that can
    cross into the cap, transport SiO, neutral TiO2_gas, and a map with
    no melt-surface carrier. Partials: none, O2 above a typical root,
    O2 below it, metal backpressure, and both. Conductance: ideal
    Hertz-Knudsen, or a species-specific scale standing in for unequal
    film resistance. Gates: buffered in-band (closed), no buffer, and
    the physical floor.

    An open case is closed on its own partials and conductances.
    Replaying those pressures at an O2 bulk equal to the solved surface
    is not closed, and solving that second vector is. A closed case
    keeps the published map. A condensed pressure is never above P_sat.
    Nothing in an open map is a silent 0.
    """

    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    capacity, at_floor, source_pO2, p_ref = _net_gate(gate)
    specs = _net_specs(species_set, p_ref)
    partials = _net_partials(partial_mode, specs)
    scaled = law == "scaled"
    conductance = _net_conductance(specs, scaled=scaled)
    published = {spec["species"]: spec["published"] for spec in specs}
    provenance = {spec["species"]: _net_provenance(spec) for spec in specs}
    _compiled, legacy = _catalog()
    result = apply_langmuir_surface_oxygen(
        published,
        temperature_K=_GEN_T_K,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=source_pO2,
        transport_pO2_bar=_GEN_TRANSPORT_PO2_BAR,
        legacy_catalog=legacy,
        compiled_catalog=_compiled,
        melt_redox_capacity_mol_per_ln_fO2=capacity,
        overhead_partials_Pa=partials,
        conductance_mol_s_m2_Pa=conductance if scaled else None,
    )
    assert set(result.pressures_Pa) == set(published)
    for pressure in result.pressures_Pa.values():
        assert math.isfinite(pressure)
        assert pressure > 0.0
    for spec in specs:
        if spec["kind"] in ("transport", "neutral"):
            assert result.pressures_Pa[spec["species"]] == spec["published"]
        if spec["kind"] == "capped":
            assert result.pressures_Pa[spec["species"]] <= spec["p_sat"] * (
                1.0 + 1.0e-9
            )
    opens = _net_opens(specs, capacity, at_floor)
    if not opens:
        assert result.applied is False
        assert result.pressures_Pa == published
        assert result.surface_pO2_bar is None
        return

    surface = result.surface_pO2_bar
    assert surface is not None
    assert surface > 0.0
    assert result.refused_species == {}
    assert result.relative_residual < 1.0e-4
    assert result.eval_pO2_below_physical_floor is at_floor
    assert result.capacity_negligible is (
        capacity <= float(OXYGEN_RESERVOIR_NOOP_MOL)
    )
    for spec in specs:
        if spec["kind"] in ("transport", "neutral"):
            continue
        expected = _net_expected_pressure(spec, surface)
        assert result.pressures_Pa[spec["species"]] == pytest.approx(
            expected, rel=1.0e-5, abs=1.0e-12
        )
        if spec["kind"] == "capped":
            log_raw = math.log10(spec["raw"]) + spec["exponent"] * math.log10(
                surface / spec["p_ref"]
            )
            if log_raw >= 0.0:
                uncapped = spec["p_sat"] * spec["raw"] * (
                    surface / spec["p_ref"]
                ) ** spec["exponent"]
                assert uncapped > spec["p_sat"]
                assert result.pressures_Pa[spec["species"]] == pytest.approx(
                    spec["p_sat"], rel=1.0e-6
                )
    residual, j_o2 = _net_relative(
        surface, specs, partials, conductance, pressures=dict(result.pressures_Pa)
    )
    assert residual < 1.0e-4
    o2_bulk = partials.get("O2", 0.0)
    if j_o2 > 0.0 and o2_bulk > 0.0:
        assert surface * PA_PER_BAR >= o2_bulk * (1.0 - 1.0e-9)
        assert result.overhead_bounded is True
    else:
        assert result.overhead_bounded is False

    stale = dict(partials)
    stale["O2"] = surface * PA_PER_BAR
    stale_residual, _stale_o2 = _net_relative(
        surface,
        specs,
        stale,
        conductance,
        pressures=dict(result.pressures_Pa),
    )
    assert stale_residual > 1.0e-3
    again = apply_langmuir_surface_oxygen(
        published,
        temperature_K=_GEN_T_K,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=source_pO2,
        transport_pO2_bar=_GEN_TRANSPORT_PO2_BAR,
        legacy_catalog=legacy,
        compiled_catalog=_compiled,
        melt_redox_capacity_mol_per_ln_fO2=capacity,
        overhead_partials_Pa=stale,
        conductance_mol_s_m2_Pa=conductance if scaled else None,
    )
    assert again.surface_pO2_bar is not None
    assert again.surface_pO2_bar * PA_PER_BAR > stale["O2"]
    assert again.relative_residual < 1.0e-4
    again_residual, again_o2 = _net_relative(
        again.surface_pO2_bar,
        specs,
        stale,
        conductance,
        pressures=dict(again.pressures_Pa),
    )
    assert again_residual < 1.0e-4
    assert again_o2 > 0.0
    assert again.overhead_bounded is True
    for spec in specs:
        if spec["kind"] != "capped":
            continue
        assert again.pressures_Pa[spec["species"]] <= spec["p_sat"] * (
            1.0 + 1.0e-9
        )


# Partial classes the ordinary publisher must not reassemble on its own.
# ``o2`` is the reviewer's case: 100 Pa of headspace O2, absent from the
# vapor map. ``both`` adds a Ca bulk. ``metal`` has no synthetic channel.
_FILM_PARTIAL_CASES = {
    "none": {},
    "o2": {"O2": 100.0},
    "metal": {"Ca": 50.0},
    "both": {"O2": 100.0, "Ca": 50.0},
}
# ``below`` is commanded pressure 0, under a 100 Pa overhead sum.
# ``above`` is 50 mbar = 5000 Pa, over that sum, so only the partial
# vector can still diverge.
_FILM_COMMANDS_MBAR = {
    "below": 0.0,
    "above": 50.0,
}
_FILM_GATES = ("open", "floor", "buffered")
_FILM_ENTRIES = ("persisted", "override", "reclose")


def _film_answer(gate: str):
    log = -40.0 if gate == "floor" else -9.0
    return _cao_answer(log)


def _film_capacity(gate: str) -> float:
    if gate == "open":
        return 0.0
    return _BUFFERED_CAPACITY_MOL_PER_LN


def _positive_partials(partials: Mapping) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for species, value in partials.items():
        number = float(value)
        if math.isfinite(number) and number > 0.0:
            cleaned[str(species)] = number
    return cleaned


def _dispatch_real_flux_provider(host, captured: dict):
    """Run the production flux provider on the controls the live path built.

    Stoich and molar mass are filled only so the provider can emit Ca.
    Partials and ``overhead_pressure_pa`` are the live path's values.
    The return to ``_calculate_evaporation`` is an empty success so the
    rest of that method does not need a full simulator. The assertions
    read ``captured``, including the provider result.
    """

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    provider = BuiltinEvaporationFluxProvider()

    def _dispatch_only(intent, *args, **kwargs):
        del args
        controls = dict(kwargs["control_inputs"])
        captured["intent"] = intent
        captured["controls"] = controls
        enriched = dict(controls)
        stoich = dict(enriched.get("stoich_by_species") or {})
        stoich.setdefault(
            "Ca",
            {"oxide_per_product_kg": 1.4, "parent_oxide": "CaO"},
        )
        enriched["stoich_by_species"] = stoich
        masses = dict(enriched.get("molar_mass_kg_mol") or {})
        masses.setdefault("Ca", _M_CA)
        enriched["molar_mass_kg_mol"] = masses
        try:
            captured["provider"] = provider.dispatch(
                IntentRequest(
                    intent=ChemistryIntent.EVAPORATION_FLUX,
                    account_view=ProviderAccountView(
                        accounts={},
                        species_formula_registry={},
                    ),
                    temperature_C=float(host.melt.temperature_C),
                    pressure_bar=float(host.melt.p_total_mbar) / 1000.0,
                    control_inputs=enriched,
                )
            )
            captured["provider_error"] = None
        except Exception as exc:  # noqa: BLE001 — the assertion reports it
            captured["provider"] = None
            captured["provider_error"] = exc
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    host._dispatch_only = _dispatch_only


@pytest.mark.parametrize("partial_name", list(_FILM_PARTIAL_CASES))
@pytest.mark.parametrize("command_name", list(_FILM_COMMANDS_MBAR))
@pytest.mark.parametrize("gate", _FILM_GATES)
@pytest.mark.parametrize("entry", _FILM_ENTRIES)
def test_publisher_film_matches_the_surface_root_on_every_path(
    partial_name: str,
    command_name: str,
    gate: str,
    entry: str,
) -> None:
    """One film for the root and the flux publisher, on every path.

    Invariant: when this step closed a surface root, the kernel's
    partial vector and transport pressure are that root's film. The
    publisher does not rebuild them from the vapor-map keys. Synthetic
    O2 stays in the film, including when the commanded melt pressure is
    below the overhead partial sum (100 Pa O2, commanded 0). The real
    flux provider's Ca flux is the series law at that pressure, and the
    oxygen residual of that film closes.

    A step that did not close a root keeps the vapor-map backpressure,
    so a buffered in-band melt does not pick up an unused O2 film.
    An explicit override, including a reclose of that same vector, is
    filmed once and shared even when the root stays shut.

    Paths: persisted headspace, explicit override, and reclose.
    Partials: none, O2, Ca, both. Command: below and above the partial
    sum. Gates: no buffer, physical floor, buffered in-band.
    """

    partials_pa = dict(_FILM_PARTIAL_CASES[partial_name])
    p_total_mbar = _FILM_COMMANDS_MBAR[command_name]
    explicit = entry != "persisted"
    answer = _film_answer(gate)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    compiled, legacy = _catalog()
    headspace_mbar = (
        {}
        if explicit
        else {species: value / 100.0 for species, value in partials_pa.items()}
    )
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=_film_capacity(gate),
        p_total_mbar=p_total_mbar,
        headspace_partials_mbar=headspace_mbar,
    )
    if explicit:
        source = _positive_partials(partials_pa)
    else:
        source = EvaporationMixin._langmuir_headspace_partials_Pa(
            host, published
        )
    root_pressure = EvaporationMixin._evaporation_overhead_total_pressure_Pa(
        host, source
    )
    green = {
        species: host._evaporation_bulk_partial_pressure_pa(species)
        for species in published
    }
    for species in published:
        green.setdefault(species, 0.0)
    green_pressure = EvaporationMixin._evaporation_overhead_total_pressure_Pa(
        host, green
    )
    captured: dict = {}
    _dispatch_real_flux_provider(host, captured)
    override = partials_pa if explicit else None
    EvaporationMixin._calculate_evaporation(
        host,
        equilibrium,
        overhead_partials_override_Pa=override,
    )
    notice = getattr(host, "_last_langmuir_surface_oxygen", None)
    root_closed = (
        isinstance(notice, dict) and notice.get("surface_pO2_bar") is not None
    )
    if gate == "buffered":
        assert not root_closed
    elif partial_name != "both":
        assert root_closed, notice
    if root_closed or explicit:
        expected_positive = source
        expected_pressure = root_pressure
    else:
        expected_positive = _positive_partials(green)
        expected_pressure = green_pressure
    controls = captured["controls"]
    kernel_partials = controls["overhead_partials_Pa"]
    kernel_pressure = float(controls["overhead_pressure_pa"])
    kernel_map = controls[CONTROL_FLUX_PRESSURES_KEY]
    for species, value in expected_positive.items():
        assert species in kernel_partials, (
            f"publisher film dropped {species}; "
            f"partials={kernel_partials} pressure={kernel_pressure} "
            f"expected {expected_positive} at {expected_pressure}"
        )
        assert kernel_partials[species] == pytest.approx(value)
    assert kernel_pressure == pytest.approx(expected_pressure)
    if root_closed:
        assert notice is not None
        assert float(notice["relative_residual"]) < 1.0e-4
        # Ca+O2 only. Ca2 and CaO(g) sit outside this check, so a closed
        # root can miss 1e-4 here (the metal-bulk cells land near 3e-4)
        # while a vacuum film under an O2 root misses by order 1.
        # ``both`` puts the Ca bulk above the O2-bounded Ca pressure, so
        # the Ca drive is zero and this ratio is not the root.
        if partial_name != "both":
            residual = _series_cao_residual(
                float(notice["surface_pO2_bar"]),
                published_ca,
                p_ref_bar,
                bulk_o2_pa=float(kernel_partials.get("O2", 0.0)),
                bulk_ca_pa=float(kernel_partials.get("Ca", 0.0)),
                overhead_pa=kernel_pressure,
                temperature_K=_T_K,
                legacy=legacy,
                pressure_ca_pa=float(kernel_map["Ca"]),
            )
            assert residual < 1.0e-3
    if entry == "reclose":
        reclosed = EvaporationMixin._reclose_langmuir_pressures(
            host, partials_pa
        )
        assert reclosed is not None
        assert reclosed.pressures_Pa["Ca"] == pytest.approx(
            float(kernel_map["Ca"]), rel=1.0e-12, abs=0.0
        )
        if root_closed:
            assert reclosed.surface_pO2_bar is not None
            assert reclosed.relative_residual < 1.0e-4
    assert captured["provider_error"] is None
    provider = captured["provider"]
    assert provider is not None
    assert str(provider.status) == "ok", provider.diagnostic
    provider_flux = dict(
        (provider.diagnostic or {}).get("evaporation_flux_kg_hr") or {}
    )
    alpha_ca = _catalog_alpha(legacy, "Ca", _T_K)
    molar_flux = _series_molar_flux(
        species="Ca",
        pressure_pa=float(kernel_map["Ca"]),
        bulk_pa=float(kernel_partials.get("Ca", 0.0)),
        molar_mass=_M_CA,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=kernel_pressure,
    )
    expected_kg_hr = molar_flux * _M_CA * 3600.0 * float(
        host.melt.melt_surface_area_m2
    )
    assert provider_flux.get("Ca", 0.0) == pytest.approx(
        expected_kg_hr, rel=1.0e-6, abs=1.0e-12
    )
    if (
        partial_name == "o2"
        and command_name == "below"
        and gate == "open"
        and entry == "persisted"
    ):
        assert kernel_pressure == pytest.approx(100.0)
        assert kernel_partials["O2"] == pytest.approx(100.0)
        assert provider_flux["Ca"] > 0.0
        # The vacuum film the old publisher built does not close.
        stale = _series_cao_residual(
            float(notice["surface_pO2_bar"]),
            published_ca,
            p_ref_bar,
            bulk_o2_pa=0.0,
            bulk_ca_pa=0.0,
            overhead_pa=0.0,
            temperature_K=_T_K,
            legacy=legacy,
            pressure_ca_pa=float(kernel_map["Ca"]),
        )
        assert stale > 1.0e-3
