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
The candidate-only helpers are bound only when this mixin already
has them, so the same fixture reaches that identity resolver.

Invariant, enforced only inside the resolver's surface adjustment:
every species is the published pressure (closure closed, or not a
melt-surface carrier), the one shared surface-pO2 law, or a typed
refusal. A condensed-metal cap does not zero that carrier's oxygen
demand, and its pressure leaves P_sat once the surface rises past the
cap. Transport-headspace pressures are not rewritten. The closure
opens only for the physical floor or a reservoir source capacity at
or under the noop floor. ``equilibrium.liquid_fraction`` is not that
test.
"""

from __future__ import annotations

import functools
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
