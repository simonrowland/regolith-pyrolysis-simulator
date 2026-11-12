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
refusal. A required solve that does not close refuses every
melt-surface participant. An in-band carrier above the certified
minimum is not left at the persisted pressure. The published surface is the root of
``sum (nO - dO) J = 0`` with ``J = k * max(0, P - P_bulk)`` using the
publisher conductance (ideal Hertz-Knudsen when the film is off).
Inside that root, overhead O2 is ``P_bulk`` on every branch: a
disabled film and an HKL upper bound set ``R_gas = 0`` and do not
discard the partial. While O2 is net-leaving the surface is not below
it. The flux publisher applies that O2 bulk only when the root owns
the evaluation (``surface_oxygen_bound``). A closed gate uses green's
film rule for O2 as well, so a disabled film and an HKL upper bound
publish a zero O2 bulk. The reported
residual is that root's residual. A condensed-metal cap
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

Publisher policy, one chokepoint. ``alpha_policy_controls`` and the
film parent the closure passes are what ``_evaporation_flux_control_inputs``
hands the flux kernel. Green's film policy: the nested
``evaporation_series_resistance.gas_resistance_enabled`` wins, and a
top-level kernel flag is not copied onto those controls and is not
that parent. Absent or null nested means the film is on. The closure
and the publisher therefore share that flag on an open gate, and a
closed gate keeps green's rates. ``species_drive_bulk_Pa`` is the only
melt-surface bulk. Overhead O2 survives a disabled film or an HKL
upper bound only when ``surface_oxygen_bound`` is true. The allowlist
is green's
``tuple(raw or ())``. A string splits into characters (b-1170, not
fixed here), so ``"Al2"`` does not permit Al2. A measured row, a
``*`` wildcard, and a permitted unmeasured fallback are in the
residual at that alpha. An open gate omits an ``n != 0`` carrier
the policy does not emit: the persisted pressure is not left for
the publisher to emit as unflagged alpha 1 below its parent-mass
or baseline thresholds. A measured alpha of 0 is the surface law.
A closed gate keeps every published pressure. A metal rail
(``gas_fugacity`` or ``condensed_raoult_psat``) with no recorded
``pO2_bar`` does not borrow the source fO2. An open gate omits that
carrier with ``missing pO2 reference``; the persisted pressure is not
returned. The conductance mass is the publisher's catalog mass. The
emitted Ca flux residual uses that mass, not the CIAAW formula mass.

Persisted headspace. The partial vector the film sums is every
positive finite entry of the persisted composition. The vapor map is
not an allowlist, and O2 is not a species added after that
intersection. A non-vapor carrier sets the realized total
``max(sum, commanded)`` that Kn and diffusivity use. An explicit
override is that evaluation's vector and is not filled from the
persisted map. A step that did not close a root keeps vapor-map
backpressure; the stored film still holds the complete vector.
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


def _publisher_molar_mass_kg(legacy, species: str) -> float:
    """The mass ``_build_evaporation_aux_maps`` would hand the publisher."""

    from simulator.evaporation import production_vapor_molar_mass_g_per_mol

    raw = production_vapor_molar_mass_g_per_mol(legacy, species)
    assert raw is not None, species
    return float(raw) / 1000.0


def _congruent_cao_surface(
    pressure_ca_pa: float,
    p_ref_bar: float,
    legacy=None,
) -> tuple[float, float, float]:
    """Independent Ca+O2 root. Does not call the oxygen-balance solver.

    Algebra is the module comment above. Alpha is 1 for Ca and for the
    synthetic O2 channel. Unit check: p in bar, P in Pa, flux in
    kg m^-2 h^-1. Limiting check: the same P_pub * sqrt(p_ref) from the
    floor publication and from 1e-9 bar yields one p. With ``legacy``,
    the mass is the publisher's catalog operand (Ca 0.04008 kg/mol).
    Without it, the mass is the formula-derived CIAAW value. Those two
    roots are not the same number.
    """

    if legacy is None:
        molar_ca = _M_CA
        molar_o2 = _M_O2
    else:
        molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
        molar_o2 = _publisher_molar_mass_kg(legacy, "O2")
    p_pow = (
        pressure_ca_pa
        * math.sqrt(p_ref_bar)
        * math.sqrt(molar_o2)
        / (2.0 * PA_PER_BAR * math.sqrt(molar_ca))
    )
    surface_pO2_bar = p_pow ** (1.0 / 1.5)
    pressure_pa = pressure_ca_pa * (surface_pO2_bar / p_ref_bar) ** (-0.5)
    flux = (
        pressure_pa
        * hertz_knudsen_k_kg_s_m2_pa(_T_K, molar_ca)
        * 3600.0
    )
    return surface_pO2_bar, pressure_pa, flux


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
    chemistry_kernel: dict | None = None,
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
        setpoints=(
            {}
            if chemistry_kernel is None
            else {"chemistry_kernel": dict(chemistry_kernel)}
        ),
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
    _formula_surface, formula_pa, formula_flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    surface_pO2_bar, pressure_pa, flux = _congruent_cao_surface(
        published_ca, p_ref_bar, legacy=legacy
    )
    # CIAAW Ca, 0.040078 kg/mol. The emitted flux uses catalog 0.04008.
    assert _formula_surface == pytest.approx(2.386748965e-5, rel=1.0e-6)
    assert formula_pa == pytest.approx(5.34230068, rel=1.0e-6)
    assert formula_flux == pytest.approx(10.71153209, rel=1.0e-6)
    assert pressure_pa != pytest.approx(formula_pa, rel=1.0e-7)
    assert _publisher_molar_mass_kg(legacy, "Ca") == pytest.approx(0.04008)
    assert _M_CA == pytest.approx(0.040078)

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
        (resolved["Ca"] / math.sqrt(_publisher_molar_mass_kg(legacy, "Ca")))
        / (
            notice["surface_pO2_bar"]
            * PA_PER_BAR
            / math.sqrt(_publisher_molar_mass_kg(legacy, "O2"))
        )
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
    _formula_surface, formula_pa, formula_flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    surface_pO2_bar, pressure_pa, flux = _congruent_cao_surface(
        published_ca, p_ref_bar, legacy=legacy
    )
    assert formula_pa == pytest.approx(5.34230068, rel=1.0e-6)
    assert formula_flux == pytest.approx(10.71153209, rel=1.0e-6)
    assert pressure_pa != pytest.approx(formula_pa, rel=1.0e-7)
    del _formula_surface
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
    assert resolved["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
    assert resolved["Ca"] != pytest.approx(formula_pa, rel=1.0e-7)
    catalog_flux = (
        resolved["Ca"]
        * hertz_knudsen_k_kg_s_m2_pa(
            _T_K, _publisher_molar_mass_kg(legacy, "Ca")
        )
        * 3600.0
    )
    assert catalog_flux == pytest.approx(flux, rel=1.0e-6)
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
        published_ca, p_ref_bar, legacy=legacy
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
        published_ca, p_ref_bar, legacy=legacy
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
    molar_ca: float,
    molar_fe: float,
    molar_o2: float,
) -> tuple[float, float]:
    """Proportional oxygen-atom and parent-demand terms.

    The Hertz-Knudsen ``1/sqrt(2*pi*R*T)`` cancels. Metal gas has
    ``nO = 0`` and reaction ``n = -1/2``, so ``dO = nO - 2*n = 1`` and
    the signed weight is ``-1``. O2 has ``nO = 2``, ``dO = 0``. Forcing
    ``fe_demand`` false is the capped-rail bug: the local slope is 0,
    and using that slope as stoichiometry drops Fe's parent oxygen.
    ``molar_*`` are the publisher operands (catalog grams / 1000, else
    ``MOLAR_MASS``). CIAAW Ca (0.040078) is not the catalog operand
    (0.04008) and is not this root.
    """

    oxygen = 2.0 * (surface_pO2_bar * PA_PER_BAR) / math.sqrt(molar_o2)
    parent = 0.0
    if include_ca:
        pressure = _gen_law_pressure(
            _GEN_P_CA, p_ref_bar, surface_pO2_bar
        )
        parent += _GEN_ALPHA_CA * pressure / math.sqrt(molar_ca)
    if include_fe and fe_demand:
        pressure = _gen_law_pressure(
            _GEN_P_FE,
            p_ref_bar,
            surface_pO2_bar,
            raw_activity=_GEN_FE_RAW,
        )
        parent += _GEN_ALPHA_FE * pressure / math.sqrt(molar_fe)
    return oxygen, parent


def _gen_relative_residual(
    surface_pO2_bar: float,
    p_ref_bar: float,
    *,
    include_ca: bool,
    include_fe: bool,
    fe_demand: bool,
    molar_ca: float,
    molar_fe: float,
    molar_o2: float,
) -> float:
    oxygen, parent = _gen_oxygen_parts(
        surface_pO2_bar,
        p_ref_bar,
        include_ca=include_ca,
        include_fe=include_fe,
        fe_demand=fe_demand,
        molar_ca=molar_ca,
        molar_fe=molar_fe,
        molar_o2=molar_o2,
    )
    return abs(oxygen - parent) / max(oxygen, parent, 1.0e-300)


def _gen_surface_pO2_bar(
    p_ref_bar: float,
    *,
    include_ca: bool,
    include_fe: bool,
    molar_ca: float,
    molar_fe: float,
    molar_o2: float,
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
            molar_ca=molar_ca,
            molar_fe=molar_fe,
            molar_o2=molar_o2,
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
    # Publisher operands from the legacy view the host solves with.
    # Not the closure helper, and not CIAAW formula mass. Catalog Ca
    # is 40.08 g/mol; formula Ca is 40.078. Fe's catalog row matches
    # the formula mass, so a Ca-free cell does not move.
    from simulator.evaporation import production_vapor_molar_mass_g_per_mol

    molar_ca = float(legacy["metals"]["Ca"]["molar_mass_g_mol"]) / 1000.0
    molar_fe = float(legacy["metals"]["Fe"]["molar_mass_g_mol"]) / 1000.0
    molar_o2 = float(MOLAR_MASS["O2"]) / 1000.0
    assert molar_ca == pytest.approx(0.04008)
    assert molar_ca != pytest.approx(_M_CA, rel=1.0e-7)
    assert production_vapor_molar_mass_g_per_mol(legacy, "Ca") == (
        legacy["metals"]["Ca"]["molar_mass_g_mol"]
    )
    assert production_vapor_molar_mass_g_per_mol(legacy, "Fe") == (
        legacy["metals"]["Fe"]["molar_mass_g_mol"]
    )
    assert production_vapor_molar_mass_g_per_mol(legacy, "O2") == MOLAR_MASS["O2"]
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
        molar_ca=molar_ca,
        molar_fe=molar_fe,
        molar_o2=molar_o2,
    )
    if include_ca:
        formula_surface = _gen_surface_pO2_bar(
            p_ref,
            include_ca=True,
            include_fe=include_fe,
            molar_ca=_M_CA,
            molar_fe=_M_FE,
            molar_o2=_M_O2,
        )
        # abs=0: pytest's default absolute tolerance is 1e-12, which
        # hides this 1e-5 relative move when the floor root is ~1e-10.
        assert surface != pytest.approx(formula_surface, rel=1.0e-7, abs=0.0)
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
        molar_ca=molar_ca,
        molar_fe=molar_fe,
        molar_o2=molar_o2,
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
            molar_ca=molar_ca,
            molar_fe=molar_fe,
            molar_o2=molar_o2,
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


# Certified mass-action minimum. Below this, a carrier is below-band.
# The in-band failure is the mutation: this set is empty, and a
# chokepoint that refuses only that set publishes the unclosed pressure.
_CERTIFIED_MIN_BAR = 1.0e-12
# 1000 mbar = 1 bar = the top of the log10 pO2 bracket [-30, 0].
_BRACKET_TOP_OVERHEAD_MBAR = 1000.0


def _oracle_melt_surface_participants(compiled, diagnostic, published) -> dict[str, float]:
    """Melt-surface participants from the catalog. Not the production classifier.

    This oracle is the gate-opening set only: a positive published
    pressure, a negative catalog exponent, and a reference pO2.
    ``n > 0`` is not in this set and is not neutral. The closed-or-
    refused test below covers that sign. An intrinsic-melt channel
    uses the source-reaction fO2, which is the pO2 the mass-action law
    solved at. Transport-headspace and SiO are not participants.
    ``n == 0`` (CaO_gas) is not a participant.
    """

    source = diagnostic.get("source_reaction_fO2_bar")
    provenance = diagnostic.get("vapor_pressure_numerator_provenance") or {}
    participants: dict[str, float] = {}
    for species, pressure in published.items():
        try:
            pressure_value = float(pressure)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pressure_value) or pressure_value <= 0.0:
            continue
        evaluator = compiled.evaluator_for(str(species))
        if evaluator is None:
            continue
        channel = getattr(evaluator, "oxygen_fugacity_channel", None)
        if channel == "transport_headspace" or species == "SiO":
            continue
        try:
            exponent = float(getattr(evaluator, "pO2_exponent", None))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(exponent) or exponent >= 0.0:
            continue
        if channel == "intrinsic_melt":
            reference = source
        else:
            reference = (provenance.get(species) or {}).get("pO2_bar")
        try:
            reference_value = float(reference)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(reference_value) or reference_value <= 0.0:
            continue
        participants[str(species)] = reference_value
    return participants


def _overhead_blocks_the_bracket(overhead_mbar: float) -> bool:
    """True when O2 drive is identically zero inside the declared bracket.

    Drive is ``max(0, P_O2 - P_overhead)``. Headspace mbar converts at
    100 Pa/mbar. The solver bracket is ``log10(pO2/bar)`` on
    ``[-30, 0]``, so the highest pressure it can try is 1 bar. This
    does not call the balance.
    """

    overhead_pa = float(overhead_mbar) * 100.0
    return overhead_pa >= 1.0 * PA_PER_BAR


def _assert_kernel_omits(host, equilibrium, omitted: set[str], kept: dict[str, float]) -> None:
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
    for species in omitted:
        assert species not in live_map
    for species, pressure in kept.items():
        assert live_map[species] == pytest.approx(pressure, rel=1.0e-12)


@pytest.mark.parametrize(
    "case",
    [
        "open-inband-overhead",
        "open-inband-nan-temperature",
        "open-floor-nan-temperature",
        "open-floor-overhead",
        "closed-inband-overhead",
        "closed-inband-nan-temperature",
        "open-inband-vacuum",
    ],
)
def test_failed_open_gate_refuses_every_melt_surface_carrier(case: str) -> None:
    """An open gate that does not close refuses every melt-surface carrier.

    The pure-CaO map is Ca, Ca2, and CaO_gas. Ca and Ca2 have negative
    exponents. CaO_gas is exponent 0 and stays published. The
    open-inband-overhead row is the mutation probe: both references
    are 1e-9 bar, above 1e-12, so a chokepoint that refuses only
    below-band carriers leaves Ca at 825.3377 Pa.
    """

    specs = {
        "open-inband-overhead": (-9.0, 0.0, _BRACKET_TOP_OVERHEAD_MBAR, False, "refuse"),
        "open-inband-nan-temperature": (-9.0, 0.0, None, True, "refuse"),
        "open-floor-nan-temperature": (-40.0, 1.0, None, True, "refuse"),
        "open-floor-overhead": (-40.0, 1.0, _BRACKET_TOP_OVERHEAD_MBAR, False, "refuse"),
        "closed-inband-overhead": (-9.0, 1.0, _BRACKET_TOP_OVERHEAD_MBAR, False, "published"),
        "closed-inband-nan-temperature": (-9.0, 1.0, None, True, "published"),
        "open-inband-vacuum": (-9.0, 0.0, None, False, "closed"),
    }
    f_o2, capacity, overhead_mbar, non_finite_temperature, expect = specs[case]
    compiled, legacy = _catalog()
    answer = _cao_answer(f_o2)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    assert set(published) == {"Ca", "Ca2", "CaO_gas"}
    participants = _oracle_melt_surface_participants(compiled, diagnostic, published)
    assert "Ca" in participants
    assert "CaO_gas" not in participants
    assert participants["Ca"] == pytest.approx(p_ref_bar)
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        headspace_o2_mbar=overhead_mbar,
        chemistry_kernel=(
            None
            if overhead_mbar is None
            else {
                "evaporation_series_resistance": {
                    "gas_resistance_enabled": False,
                },
            }
        ),
    )
    resolved = _resolve(
        host,
        equilibrium,
        temperature_K=float("nan") if non_finite_temperature else _T_K,
    )
    for pressure in resolved.values():
        assert pressure > 0.0
        assert math.isfinite(pressure)

    if expect == "published":
        assert resolved == published
        assert not hasattr(host, "_last_langmuir_surface_oxygen")
        return

    if expect == "closed":
        surface_pO2_bar, pressure_pa, _flux = _congruent_cao_surface(
            published_ca, p_ref_bar, legacy=legacy
        )
        assert resolved["Ca"] == pytest.approx(pressure_pa, rel=1.0e-6)
        assert resolved["Ca"] != pytest.approx(published_ca, rel=1.0e-3)
        assert resolved["CaO_gas"] == published["CaO_gas"]
        notice = host._last_langmuir_surface_oxygen
        assert notice["applied"] is True
        assert notice["surface_pO2_bar"] == pytest.approx(surface_pO2_bar, rel=1.0e-6)
        assert "Ca" not in notice["refused_species"]
        return

    below_band = {
        species
        for species, reference in participants.items()
        if reference < _CERTIFIED_MIN_BAR
    }
    if case == "open-inband-overhead":
        assert _overhead_blocks_the_bracket(overhead_mbar)
        assert below_band == set()
        assert published_ca == pytest.approx(825.3377299577454, rel=1.0e-12)
    if case == "open-floor-overhead":
        assert _overhead_blocks_the_bracket(overhead_mbar)
        assert "Ca" in below_band
    notice = host._last_langmuir_surface_oxygen
    assert notice["applied"] is True
    assert notice["surface_pO2_bar"] is None
    assert set(notice["refused_species"]) == set(participants)
    for species in participants:
        assert species not in resolved
        reason = notice["refused_species"][species]
        assert reason
        assert "0.0" not in reason
    kept = {
        species: pressure
        for species, pressure in published.items()
        if species not in participants
    }
    for species, pressure in kept.items():
        assert resolved[species] == pressure
    if not non_finite_temperature:
        _assert_kernel_omits(host, equilibrium, set(participants), kept)


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
    """``alpha * k_HK / M``. The owner's coefficient, not the solver."""

    return (
        alpha
        * hertz_knudsen_k_kg_s_m2_pa(temperature_K, molar_mass)
        / molar_mass
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
    gas_resistance_enabled: bool = True,
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
        gas_resistance_enabled=gas_resistance_enabled,
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
    molar_mass_ca: float = _M_CA,
    molar_mass_o2: float = _M_O2,
) -> tuple[float, float, float]:
    """Return ``(2*J_O2 - J_Ca, J_Ca, J_O2)`` from the publisher series law.

    Ca weight is ``nO - dO = -1``. O2 weight is ``+2``. The common
    temperature factor does not cancel once the film resistances differ.
    The default masses are CIAAW. Callers that check the emitted flux
    pass the publisher's catalog masses.
    """

    if pressure_ca_pa is None:
        pressure_ca_pa = published_ca * (surface_pO2_bar / p_ref_bar) ** (-0.5)
    pressure_o2_pa = surface_pO2_bar * PA_PER_BAR
    j_ca = _series_molar_flux(
        species="Ca",
        pressure_pa=pressure_ca_pa,
        bulk_pa=bulk_ca_pa,
        molar_mass=molar_mass_ca,
        alpha=alpha_ca,
        temperature_K=temperature_K,
        overhead_pa=overhead_pa,
    )
    j_o2 = _series_molar_flux(
        species="O2",
        pressure_pa=pressure_o2_pa,
        bulk_pa=bulk_o2_pa,
        molar_mass=molar_mass_o2,
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
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar_o2 = _publisher_molar_mass_kg(legacy, "O2")
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
        molar_mass_ca=molar_ca,
        molar_mass_o2=molar_o2,
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
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar_o2 = _publisher_molar_mass_kg(legacy, "O2")

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
            molar_mass_ca=molar_ca,
            molar_mass_o2=molar_o2,
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
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
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
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar_o2 = _publisher_molar_mass_kg(legacy, "O2")
    k_ca = _ideal_molar_k(alpha_ca, molar_ca, _T_K)
    k_o2 = _ideal_molar_k(1.0, molar_o2, _T_K)
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
    # The ratio used is the publisher mass (catalog Ca 0.04008 kg/mol).
    # 1.42427779844656 is the CIAAW Ca probe (0.040078) and is not the
    # conductance the root emits.
    k_ca_film = _series_molar_flux(
        species="Ca",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=molar_ca,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    k_o2_film = _series_molar_flux(
        species="O2",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=molar_o2,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    k_ca_free = _series_molar_flux(
        species="Ca",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=molar_ca,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=0.0,
    )
    k_o2_free = _series_molar_flux(
        species="O2",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=molar_o2,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=0.0,
    )
    k_ca_ciaaw = _series_molar_flux(
        species="Ca",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_CA,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    k_o2_ciaaw = _series_molar_flux(
        species="O2",
        pressure_pa=1.0,
        bulk_pa=0.0,
        molar_mass=_M_O2,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    used_ratio = k_o2_film / k_ca_film
    ciaaw_ratio = k_o2_ciaaw / k_ca_ciaaw
    # Gas-film resistance dominates, so catalog Ca (0.04008) moves this
    # ratio by ~1e-7 against the CIAAW probe. 1e-6 does not separate
    # them. Both still round to 1.424.
    assert ciaaw_ratio == pytest.approx(1.42427779844656, rel=1.0e-9)
    assert used_ratio == pytest.approx(1.4242779893571864, rel=1.0e-9)
    assert used_ratio != pytest.approx(ciaaw_ratio, rel=1.0e-8)
    assert used_ratio == pytest.approx(1.424, rel=1.0e-3)
    assert k_o2_free / k_ca_free == pytest.approx(
        math.sqrt(molar_ca / molar_o2), rel=1.0e-12
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
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
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
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
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
        # Catalog mass, the operand ``_build_evaporation_aux_maps``
        # publishes. Formula CIAAW Ca (0.040078) is a different conductance.
        masses.setdefault(
            "Ca",
            _publisher_molar_mass_kg(
                getattr(host, "vapor_pressures", None), "Ca"
            ),
        )
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
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar_flux = _series_molar_flux(
        species="Ca",
        pressure_pa=float(kernel_map["Ca"]),
        bulk_pa=float(kernel_partials.get("Ca", 0.0)),
        molar_mass=molar_ca,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=kernel_pressure,
    )
    expected_kg_hr = molar_flux * molar_ca * 3600.0 * float(
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


# Placements of the one film flag. ``absent`` omits the key. None is a
# present null. The third column is green's publisher decision: the
# nested key wins, null and absent fall through to on, and a quoted
# false or a numeric 0 on that nested key stays off. The top-level
# kernel flag is not an input, on either gate.
_GAS_FLAG_PLACEMENTS = (
    ("absent", "absent", True),
    ("absent", False, True),
    ("absent", True, True),
    ("absent", None, True),
    ("absent", "false", True),
    ("absent", 0, True),
    (False, "absent", False),
    (True, "absent", True),
    (False, True, False),
    (True, False, True),
    (None, False, True),
    (None, "absent", True),
    (None, None, True),
    ("false", True, False),
    (0, False, False),
    (False, "false", False),
)
_ALPHA_POLICIES = (
    "off",
    "on",
    "allow-Al2",
    "allow-tuple",
    "allow-string",
    "allow-bytes",
    "allow-empty",
    "allow-other",
    "allow-null",
    "wildcard",
)
_ZR_PA = 1.0
_WILDCARD_ALPHA = 0.4


def _flag_id(value: object) -> str:
    if value == "absent":
        return "absent"
    if value is None:
        return "null"
    return repr(value)


def _gas_kernel(nested: object, top: object) -> dict:
    kernel: dict = {}
    if top != "absent":
        kernel["gas_resistance_enabled"] = top
    if nested != "absent":
        kernel["evaporation_series_resistance"] = {
            "gas_resistance_enabled": nested,
        }
    return kernel


def _alpha_kernel(policy: str) -> dict:
    if policy in ("off", "wildcard"):
        return {}
    if policy == "on":
        return {"allow_unmeasured_alpha_fallback": True}
    if policy == "allow-Al2":
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": ["Al2"],
        }
    if policy == "allow-tuple":
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": ("Al2",),
        }
    if policy == "allow-string":
        # b-1170. Green's ``tuple("Al2")`` is the characters, not Al2.
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": "Al2",
        }
    if policy == "allow-bytes":
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": b"Al2",
        }
    if policy == "allow-empty":
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": [],
        }
    if policy == "allow-other":
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": ["NoSuchSpecies"],
        }
    if policy == "allow-null":
        # Present null is stored as an empty tuple: nobody is permitted.
        return {
            "allow_unmeasured_alpha_fallback": True,
            "unmeasured_alpha_fallback_species": None,
        }
    raise AssertionError(policy)


@functools.lru_cache(maxsize=1)
def _cao_al_answer():
    payload = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(payload)
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": {"CaO": 1.0, "Al2O3": 1.0},
                },
                species_formula_registry={},
            ),
            temperature_C=_T_C,
            pressure_bar=0.01,
            control_inputs={
                "pO2_bar": DEFAULT_VACUUM_FLOOR_BAR,
                "intrinsic_fO2_log": -9.0,
                "vacuum_floor_bar": DEFAULT_VACUUM_FLOOR_BAR,
            },
        )
    )


def _legacy_species_row(legacy, species: str):
    for group_name in ("metals", "oxide_vapors"):
        group = legacy.get(group_name) or {}
        row = group.get(species)
        if isinstance(row, dict):
            return row
    return None


def _law_exponent_and_pref(compiled, legacy, species: str, provenance, source_bar):
    """Catalog exponent and the pO2 that published this pressure.

    Metal rails use the Ellingham stoichiometry. Intrinsic-melt oxide
    rows use the source-reaction fO2, not the row's 1 bar reference.
    """

    if species == "Zr":
        return -0.5, 1.0e-9
    rail = str((provenance or {}).get("pressure_rail") or "")
    if rail in ("gas_fugacity", "condensed_raoult_psat"):
        from simulator.chemistry.ellingham_thermo import ellingham_stoichiometry

        n_metal, _oxide = ellingham_stoichiometry(species)
        recorded = float(provenance["pO2_bar"])
        return -1.0 / float(n_metal), recorded
    species_map = getattr(compiled, "species", {}) or {}
    compiled_species = species_map.get(species)
    evaluator = getattr(compiled_species, "evaluator", None)
    exponent = getattr(evaluator, "pO2_exponent", None) if evaluator else None
    channel = (
        getattr(evaluator, "oxygen_fugacity_channel", None) if evaluator else None
    )
    if exponent is None:
        row = _legacy_species_row(legacy, species)
        if isinstance(row, dict) and row.get("pO2_exponent") is not None:
            exponent = row.get("pO2_exponent")
    if exponent is None and provenance.get("pO2_exponent") is not None:
        exponent = provenance.get("pO2_exponent")
    if channel == "intrinsic_melt":
        pref = float(source_bar)
    else:
        recorded = provenance.get("pO2_bar")
        pref = float(recorded) if recorded else float(source_bar)
    if exponent is None:
        return None, pref
    return float(exponent), pref


def _species_molar_kg(legacy, species: str) -> float:
    if species == "Zr":
        return parse_formula("Zr").molar_mass_kg_per_mol()
    row = _legacy_species_row(legacy, species) or {}
    formula = row.get("formula") or species
    return parse_formula(str(formula)).molar_mass_kg_per_mol()


def _catalog_overlay(legacy, metal_rows: Mapping[str, dict]):
    """Shallow catalog copy plus extra metal rows.

    The compiled legacy view holds mapping proxies, so ``copy.deepcopy``
    cannot clone it. Existing rows stay shared and are not mutated.
    """

    copied = dict(legacy)
    metals = dict(legacy.get("metals") or {})
    metals.update(metal_rows)
    copied["metals"] = metals
    copied["oxide_vapors"] = dict(legacy.get("oxide_vapors") or {})
    return copied


def _with_proxy_zr(legacy):
    """Synthetic broad-proxy carrier, same admission class as Al2.

    ``_legacy_evaporation_shadow_pressure_map`` keeps a species only when
    the full alpha loader has a row. A proxy tag passes that gate and is
    then stripped by the measured view, so both consumers see an
    unmeasured carrier. A species with no ``evaporation_alpha`` key never
    reaches the flux map; it is not a second alpha path.
    """

    return _catalog_overlay(
        legacy,
        {
            "Zr": {
                "formula": "Zr",
                # A truthy metals row with no molar_mass_g_mol does not
                # fall through to a formula parse. The publisher then
                # emits no Zr flux, and the open gate omits Zr before
                # the alpha policy can publish it. This is the formula
                # mass the flux provider already uses for Zr.
                "molar_mass_g_mol": (
                    parse_formula("Zr").molar_mass_kg_per_mol() * 1000.0
                ),
                "evaporation_alpha": {
                    "value": 0.2,
                    "tag": "broad_proxy_not_intrinsic",
                },
            }
        },
    )


def _with_wildcard(legacy):
    return _catalog_overlay(
        legacy,
        {
            "*": {
                "formula": "Ca",
                "evaporation_alpha": {"value": _WILDCARD_ALPHA},
            }
        },
    )


def _dispatch_parented_flux_provider(host, captured, molar_kg) -> None:
    """Real flux provider, with a parent mass above the dust floor.

    The dust floor (``available_parent_kg <= 1e-12``) is the publisher's
    inventory gate, not an alpha. Every species here has a parent, so
    the alpha policy and the refusal are the same decision.
    """

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    provider = BuiltinEvaporationFluxProvider()

    def _dispatch_only(intent, *args, **kwargs):
        del args
        controls = dict(kwargs["control_inputs"])
        captured["intent"] = intent
        captured["controls"] = controls
        enriched = dict(controls)
        pressures = dict(enriched.get(CONTROL_FLUX_PRESSURES_KEY) or {})
        stoich = dict(enriched.get("stoich_by_species") or {})
        masses = dict(enriched.get("molar_mass_kg_mol") or {})
        available = dict(enriched.get("available_oxide_kg") or {})
        for species in pressures:
            stoich.setdefault(
                species,
                {"oxide_per_product_kg": 1.0, "parent_oxide": "CaO"},
            )
            masses.setdefault(species, molar_kg(species))
            available.setdefault(species, 1.0)
        enriched["stoich_by_species"] = stoich
        enriched["molar_mass_kg_mol"] = masses
        enriched["available_oxide_kg"] = available
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
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    host._dispatch_only = _dispatch_only


@pytest.mark.parametrize("gate", ("open", "buffered"))
@pytest.mark.parametrize(
    ("nested", "top", "enabled"),
    _GAS_FLAG_PLACEMENTS,
    ids=[
        f"nested-{_flag_id(nested)}-top-{_flag_id(top)}"
        for nested, top, _enabled in _GAS_FLAG_PLACEMENTS
    ],
)
def test_publisher_film_is_greens_on_every_placement(
    nested, top, enabled: bool, gate: str
) -> None:
    """Green's film policy, at every placement, on both gates.

    Invariant: the closure and the publisher share one film flag, and
    that flag is green's. Nested ``evaporation_series_resistance``
    wins; null and absent are on; the top-level kernel key is not on
    the publisher controls and does not select the flag. A top-level
    false with no nested override stays on the 10 mbar film. The open
    gate's Ca pressure is that law's root. The buffered in-band gate
    leaves the publication alone and the Ca flux is still that law.
    """

    from engines.builtin.evaporation_flux import resolve_gas_resistance_enabled

    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
    overhead_pa = 10.0 * 100.0
    _root_bar, film_pa = _series_cao_root(
        published_ca,
        p_ref_bar,
        bulk_o2_pa=0.0,
        bulk_ca_pa=0.0,
        overhead_pa=overhead_pa,
        temperature_K=_T_K,
        legacy=legacy,
    )
    capacity = 0.0 if gate == "open" else _BUFFERED_CAPACITY_MOL_PER_LN
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        p_total_mbar=10.0,
        chemistry_kernel=_gas_kernel(nested, top),
    )
    captured: dict = {}
    _dispatch_real_flux_provider(host, captured)
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    controls = captured["controls"]
    # The chokepoint. Green's controls builder never copied this key.
    assert "gas_resistance_enabled" not in controls
    kernel_map = controls[CONTROL_FLUX_PRESSURES_KEY]
    resolved_flag = resolve_gas_resistance_enabled(
        controls.get("evaporation_series_resistance"),
        controls,
    )
    transport = EvaporationMixin._langmuir_transport(
        host,
        {},
        _T_K,
        float(controls["overhead_pressure_pa"]),
    )
    assert resolved_flag is enabled
    assert transport.gas_resistance_enabled is enabled
    if gate == "open":
        notice = host._last_langmuir_surface_oxygen
        expected_pa = film_pa if enabled else ideal_pa
        other_pa = ideal_pa if enabled else film_pa
        assert kernel_map["Ca"] == pytest.approx(expected_pa, rel=1.0e-6)
        assert kernel_map["Ca"] != pytest.approx(other_pa, rel=1.0e-3)
        assert float(notice["relative_residual"]) < 1.0e-4
    else:
        assert not hasattr(host, "_last_langmuir_surface_oxygen")
        assert kernel_map["Ca"] == pytest.approx(published_ca, rel=1.0e-12)
        assert kernel_map["Ca"] != pytest.approx(film_pa, rel=1.0e-3)
        assert kernel_map["Ca"] != pytest.approx(ideal_pa, rel=1.0e-3)
    provider = captured["provider"]
    assert provider is not None and str(provider.status) == "ok"
    provider_flux = dict(
        (provider.diagnostic or {}).get("evaporation_flux_kg_hr") or {}
    )
    alpha_ca = _catalog_alpha(legacy, "Ca", _T_K)
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar = _series_molar_flux(
        species="Ca",
        pressure_pa=float(kernel_map["Ca"]),
        bulk_pa=0.0,
        molar_mass=molar_ca,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=float(controls["overhead_pressure_pa"]),
        gas_resistance_enabled=enabled,
    )
    expected_kg_hr = molar * molar_ca * 3600.0
    assert provider_flux["Ca"] == pytest.approx(expected_kg_hr, rel=1.0e-6)
    other = _series_molar_flux(
        species="Ca",
        pressure_pa=float(kernel_map["Ca"]),
        bulk_pa=0.0,
        molar_mass=molar_ca,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=float(controls["overhead_pressure_pa"]),
        gas_resistance_enabled=not enabled,
    )
    assert provider_flux["Ca"] != pytest.approx(
        other * molar_ca * 3600.0, rel=1.0e-3
    )


@pytest.mark.parametrize("gate", ("open", "buffered"))
@pytest.mark.parametrize("policy", _ALPHA_POLICIES)
def test_every_species_uses_the_publisher_alpha_policy(
    policy: str, gate: str
) -> None:
    """Eligibility is green's tuple normalisation, on both gates.

    Species classes in one CaO/Al2O3 map, plus synthetic Zr:
    measured rows (Ca, Al, CaO_gas, Ca2), broad-proxy rows the measured
    view strips (Al2, Al2O, AlO, and the other Al oxides), and Zr, a
    proxy-tagged row that is not Al2. Policies: fallback off, fallback
    on, a list, a tuple, a string, bytes, an empty list, a list that
    names nobody here, a present-null allowlist (nobody), and a ``*``
    wildcard. ``alpha_policy_controls`` stores ``tuple(raw or ())``.
    ``tuple("Al2")`` is the characters, so Al2 stays refused (b-1170).
    A parent mass is present. On the buffered gate a non-emitting
    species stays on the publication and the publisher records
    missing-alpha. On the open gate an ``n != 0`` carrier the policy
    does not emit is omitted, so the publisher never sees it and
    cannot emit unflagged alpha 1. An emitted carrier is rescaled.
    ``n == 0`` stays on the publication on both gates.
    """

    from engines.builtin.evaporation_flux import (
        alpha_policy_controls,
        publisher_alpha_policy,
        publisher_effective_alpha,
    )
    from simulator.evaporation import (
        _load_evaporation_alpha_by_species,
        _measured_alpha_control_view,
    )

    compiled, legacy = _catalog()
    kernel = _alpha_kernel(policy)
    legacy = _with_proxy_zr(legacy)
    if policy == "wildcard":
        legacy = _with_wildcard(legacy)
    answer = _cao_al_answer()
    diagnostic = dict(answer.diagnostic)
    published = dict(diagnostic["vapor_pressures_Pa"])
    published["Zr"] = _ZR_PA
    provenance = dict(diagnostic.get("vapor_pressure_numerator_provenance") or {})
    provenance["Zr"] = {
        "oxygen_fugacity_channel": "intrinsic_melt",
        "pO2_bar": 1.0e-9,
        "pO2_exponent": -0.5,
    }
    diagnostic["vapor_pressures_Pa"] = published
    diagnostic["vapor_pressure_numerator_provenance"] = provenance
    source_bar = float(diagnostic["source_reaction_fO2_bar"])
    capacity = 0.0 if gate == "open" else _BUFFERED_CAPACITY_MOL_PER_LN
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
        chemistry_kernel=kernel,
    )
    captured: dict = {}
    _dispatch_parented_flux_provider(
        host,
        captured,
        lambda species: _species_molar_kg(legacy, species),
    )
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    if gate == "open":
        notice = host._last_langmuir_surface_oxygen
        assert notice["applied"] is True
        assert notice["surface_pO2_bar"] is not None
        assert float(notice["relative_residual"]) < 1.0e-4
        surface = float(notice["surface_pO2_bar"])
    else:
        assert not hasattr(host, "_last_langmuir_surface_oxygen")
        surface = None
    controls = captured["controls"]
    closed = dict(controls[CONTROL_FLUX_PRESSURES_KEY])
    if "unmeasured_alpha_fallback_species" in kernel:
        assert controls["unmeasured_alpha_fallback_species"] == tuple(
            kernel["unmeasured_alpha_fallback_species"] or ()
        )
    else:
        assert "unmeasured_alpha_fallback_species" not in controls
    allow, allowlist = publisher_alpha_policy(alpha_policy_controls(kernel))
    # The controls the publisher actually read are that same policy.
    published_allow, published_allowlist = publisher_alpha_policy(controls)
    assert published_allow is allow
    assert published_allowlist == allowlist
    alpha_map = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(dict(legacy))
    )
    provider = captured["provider"]
    assert provider is not None and str(provider.status) == "ok", provider
    diagnostic_out = provider.diagnostic or {}
    flux = dict(diagnostic_out.get("evaporation_flux_kg_hr") or {})
    alpha_used = dict(diagnostic_out.get("alpha_used_by_species") or {})
    missing = dict(diagnostic_out.get("missing_alpha") or {})
    fallback_rows = set(diagnostic_out.get("unmeasured_alpha_fallback_species") or [])
    emitted_proxy = False
    for species, published_pa in published.items():
        decision = publisher_effective_alpha(
            species,
            _T_K,
            alpha_map,
            allow_unmeasured_alpha_fallback=allow,
            unmeasured_alpha_fallback_species_allowlist=allowlist,
        )
        exponent, pref = _law_exponent_and_pref(
            compiled, legacy, species, provenance.get(species) or {}, source_bar
        )
        assert exponent is not None, species
        if decision.policy_emits():
            assert species not in missing
            assert alpha_used[species] == pytest.approx(decision.alpha)
            if decision.unmeasured:
                assert species in fallback_rows
                emitted_proxy = True
            else:
                assert species not in fallback_rows
            mass = _species_molar_kg(legacy, species)
            molar = _series_molar_flux(
                species=species,
                pressure_pa=float(closed[species]),
                bulk_pa=0.0,
                molar_mass=mass,
                alpha=decision.alpha,
                temperature_K=_T_K,
                overhead_pa=float(controls["overhead_pressure_pa"]),
                gas_resistance_enabled=True,
            )
            expected_kg_hr = molar * mass * 3600.0
            assert flux.get(species, 0.0) == pytest.approx(
                expected_kg_hr, rel=1.0e-5, abs=1.0e-12
            )
            if gate == "buffered":
                assert closed[species] == pytest.approx(published_pa)
            elif exponent != 0.0:
                expected_pa = published_pa * (surface / pref) ** exponent
                assert closed[species] == pytest.approx(
                    expected_pa, rel=1.0e-4, abs=1.0e-12
                )
                assert closed[species] != pytest.approx(published_pa, rel=1.0e-3)
        else:
            assert species not in flux
            assert species not in alpha_used
            assert species not in fallback_rows
            # Open gate: n != 0 is omitted. The publisher's missing-alpha
            # record is the buffered-gate path, where the root did not run.
            if gate == "open" and exponent not in (None, 0.0):
                assert species not in closed
                assert species in notice["refused_species"]
                assert notice["refused_species"][species]
                assert species not in missing
            else:
                assert species in missing, species
                assert closed[species] == pytest.approx(published_pa)

    def _assert_policy_refused(species: str) -> None:
        assert species not in flux
        assert species not in alpha_used
        if gate == "open":
            assert species not in closed
            assert species in notice["refused_species"]
            assert species not in missing
        else:
            assert species in missing
            assert closed[species] == pytest.approx(published[species])

    if policy == "on":
        assert emitted_proxy
    if policy == "wildcard":
        assert alpha_used["Al2"] == pytest.approx(_WILDCARD_ALPHA)
        assert alpha_used["Zr"] == pytest.approx(_WILDCARD_ALPHA)
        assert "Al2" not in fallback_rows
        assert "Zr" not in missing
    if policy in ("allow-Al2", "allow-tuple"):
        assert "Al2" in alpha_used
        assert alpha_used["Al2"] == pytest.approx(1.0)
        _assert_policy_refused("Zr")
        _assert_policy_refused("Al2O")
    if policy == "allow-string":
        # Characters, not the species. Buffered and open gates both refuse.
        _assert_policy_refused("Al2")
        assert "Al2" not in alpha_used
        assert "Al2" not in flux
        _assert_policy_refused("Zr")
        _assert_policy_refused("Al2O")
    if policy in ("allow-bytes", "allow-empty", "allow-other", "allow-null"):
        _assert_policy_refused("Al2")
        assert "Al2" not in flux


# d-376 amendment 1, stated as the spec. Not imported from the solver.
# The surface root owns the O2 lower bound: overhead O2 is the drive
# bulk on every transport branch. Every other species, and O2 when the
# root did not run, keeps a bulk only while the film is on and the
# carrier is not an HKL upper bound. A disabled film is ideal
# Hertz-Knudsen, R_gas = 0.
def _spec_drive_bulk(
    species: str,
    physical_pa: float,
    *,
    film_on: bool,
    hkl: frozenset[str],
    surface_oxygen_bound: bool = True,
) -> float:
    if not math.isfinite(physical_pa) or physical_pa <= 0.0:
        return 0.0
    if surface_oxygen_bound and species == "O2":
        return physical_pa
    if film_on and species not in hkl:
        return physical_pa
    return 0.0


def _branch_film_on(species: str, *, film_on: bool, hkl: frozenset[str]) -> bool:
    return film_on and species not in hkl


_BOUND_HKL = {
    "none": frozenset(),
    "O2": frozenset({"O2"}),
    "Ca": frozenset({"Ca"}),
    "both": frozenset({"O2", "Ca"}),
}
_BOUND_PARTIALS = {
    "none": {},
    "o2": {"O2": 100.0},
    "o2+ca": {"O2": 100.0, "Ca": 0.05},
}
_BOUND_GATES = ("open", "floor", "buffered")


def _bound_cases() -> list[tuple]:
    rows = []
    for film_on in (False, True):
        for hkl_name in _BOUND_HKL:
            for partial_name in _BOUND_PARTIALS:
                for gate in _BOUND_GATES:
                    rows.append(("direct", film_on, hkl_name, partial_name, gate))
    for film_on in (False, True):
        for entry in ("live", "reclose"):
            rows.append((entry, film_on, "none", "o2", "open"))
    return rows


def _bound_case_id(case: tuple) -> str:
    entry, film_on, hkl_name, partial_name, gate = case
    film = "film" if film_on else "nofilm"
    return f"{entry}-{film}-hkl-{hkl_name}-{partial_name}-{gate}"


@functools.lru_cache(maxsize=2)
def _bound_answer(gate: str):
    return _cao_answer(-40.0 if gate == "floor" else -9.0)


def _bound_capacity(gate: str) -> float:
    if gate == "open":
        return 0.0
    return _BUFFERED_CAPACITY_MOL_PER_LN


def _bound_transport(film_on: bool, hkl: frozenset[str], partials: dict[str, float]):
    from simulator.langmuir_surface_oxygen import LangmuirTransport

    # Commanded total 0. The realized total is the partial sum, the same
    # pressure Kn would see. Film off does not switch that state to vacuum.
    realized_pa = sum(value for value in partials.values() if value > 0.0)
    return LangmuirTransport(
        temperature_K=_T_K,
        pipe_diameter_m=0.12,
        gas_temperature_K=_T_K,
        carrier_gas="N2",
        axial_stir_factor=0.0,
        radial_stir_factor=0.0,
        overhead_pressure_Pa=realized_pa,
        gas_resistance_enabled=film_on,
        hkl_upper_bound_species=frozenset(hkl),
        extrapolate_continuum=False,
    )


def _bound_cao_root(
    published_ca: float,
    p_ref_bar: float,
    *,
    partials: dict[str, float],
    film_on: bool,
    hkl: frozenset[str],
    legacy,
    realized_pa: float,
) -> tuple[float, float]:
    """Independent Ca+O2 root on the spec drive, not the surface solver."""

    from scipy.optimize import brentq

    alpha_ca = _catalog_alpha(legacy, "Ca", _T_K)
    molar_ca = _publisher_molar_mass_kg(legacy, "Ca")
    molar_o2 = _publisher_molar_mass_kg(legacy, "O2")
    bulk_ca = _spec_drive_bulk(
        "Ca", partials.get("Ca", 0.0), film_on=film_on, hkl=hkl
    )
    bulk_o2 = _spec_drive_bulk(
        "O2", partials.get("O2", 0.0), film_on=film_on, hkl=hkl
    )
    ca_film = _branch_film_on("Ca", film_on=film_on, hkl=hkl)
    o2_film = _branch_film_on("O2", film_on=film_on, hkl=hkl)

    def signed(log_p: float) -> float:
        surface = 10.0 ** log_p
        pressure_ca = published_ca * (surface / p_ref_bar) ** (-0.5)
        j_ca = _series_molar_flux(
            species="Ca",
            pressure_pa=pressure_ca,
            bulk_pa=bulk_ca,
            molar_mass=molar_ca,
            alpha=alpha_ca,
            temperature_K=_T_K,
            overhead_pa=realized_pa,
            gas_resistance_enabled=ca_film,
        )
        j_o2 = _series_molar_flux(
            species="O2",
            pressure_pa=surface * PA_PER_BAR,
            bulk_pa=bulk_o2,
            molar_mass=molar_o2,
            alpha=1.0,
            temperature_K=_T_K,
            overhead_pa=realized_pa,
            gas_resistance_enabled=o2_film,
        )
        return 2.0 * j_o2 - j_ca

    low = signed(-30.0)
    high = signed(0.0)
    assert low < 0.0 < high
    log_root = brentq(signed, -30.0, 0.0, xtol=1.0e-12, rtol=1.0e-12, maxiter=200)
    surface = 10.0 ** log_root
    pressure = published_ca * (surface / p_ref_bar) ** (-0.5)
    return surface, pressure


def _assert_law_bulks(transport, partials: dict[str, float], hkl: frozenset[str], film_on: bool) -> None:
    from simulator.langmuir_surface_oxygen import publisher_net_law

    probes = {"O2": partials.get("O2", 100.0), "Ca": partials.get("Ca", 50.0)}
    masses = {"O2": _M_O2, "Ca": _M_CA}
    for species, physical in probes.items():
        _k, bulk = publisher_net_law(
            species=species,
            alpha=1.0,
            molar_mass_kg_per_mol=masses[species],
            bulk_pressure_Pa=physical,
            temperature_K=_T_K,
            transport=transport,
        )
        assert bulk == pytest.approx(
            _spec_drive_bulk(species, physical, film_on=film_on, hkl=hkl)
        )


def _assert_direct_bound(film_on: bool, hkl_name: str, partial_name: str, gate: str) -> None:
    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    hkl = _BOUND_HKL[hkl_name]
    partials = dict(_BOUND_PARTIALS[partial_name])
    answer = _bound_answer(gate)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    compiled, legacy = _catalog()
    transport = _bound_transport(film_on, hkl, partials)
    _assert_law_bulks(transport, partials, hkl, film_on)
    result = apply_langmuir_surface_oxygen(
        published,
        temperature_K=_T_K,
        provenance_by_species=diagnostic.get(
            "vapor_pressure_numerator_provenance"
        )
        or {},
        source_reaction_fO2_bar=diagnostic.get("source_reaction_fO2_bar"),
        transport_pO2_bar=diagnostic.get("pO2_bar"),
        legacy_catalog=legacy,
        compiled_catalog=compiled,
        melt_redox_capacity_mol_per_ln_fO2=_bound_capacity(gate),
        overhead_partials_Pa=partials,
        transport=transport,
    )
    opens = gate != "buffered"
    if not opens:
        assert result.applied is False
        assert result.surface_pO2_bar is None
        assert dict(result.pressures_Pa) == published
        return
    assert result.applied is True
    assert result.surface_pO2_bar is not None
    assert result.refused_species == {}
    assert result.relative_residual < 1.0e-4
    surface = float(result.surface_pO2_bar)
    physical_o2 = partials.get("O2", 0.0)
    if physical_o2 > 0.0:
        # The disabled-film hole published the vacuum root, 2.38675e-5 bar,
        # under 100 Pa (1e-3 bar) of overhead O2.
        assert surface * PA_PER_BAR >= physical_o2 * (1.0 - 1.0e-9)
        assert surface != pytest.approx(2.386748965e-5, rel=1.0e-3)
        assert result.overhead_bounded is True
    else:
        assert result.overhead_bounded is False
    if hkl_name == "none":
        realized = float(transport.overhead_pressure_Pa)
        root_bar, root_pa = _bound_cao_root(
            published_ca,
            p_ref_bar,
            partials=partials,
            film_on=film_on,
            hkl=hkl,
            legacy=legacy,
            realized_pa=realized,
        )
        assert surface == pytest.approx(root_bar, rel=1.0e-5)
        assert result.pressures_Pa["Ca"] == pytest.approx(root_pa, rel=1.0e-5)


def _provider_drive_bulks(
    *,
    film_on: bool,
    surface_oxygen_bound: bool,
    hkl: frozenset[str] = frozenset(),
    pressures_Pa: dict[str, float] | None = None,
) -> dict:
    """Real flux provider. The O2 lower bound is the root's, not the film's."""

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    provider = BuiltinEvaporationFluxProvider()
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=ProviderAccountView(
                accounts={},
                species_formula_registry={},
            ),
            temperature_C=_T_C,
            pressure_bar=0.0,
            control_inputs={
                "vapour_batch_flux_pressures_Pa": dict(
                    pressures_Pa or {"O2": 250.0, "Ca": 80.0}
                ),
                "overhead_partials_Pa": {"O2": 100.0, "Ca": 50.0},
                "overhead_pressure_pa": 150.0,
                "commanded_pressure_pa": 0.0,
                "molar_mass_kg_mol": {"O2": _M_O2, "Ca": _M_CA},
                "stoich_by_species": {
                    "O2": {
                        "parent_oxide": "CaO",
                        "oxide_per_product_kg": 1.0,
                    },
                    "Ca": {
                        "parent_oxide": "CaO",
                        "oxide_per_product_kg": 1.4,
                    },
                },
                "available_oxide_kg": {"O2": 1.0, "Ca": 1.0},
                "melt_surface_area_m2": 1.0,
                "alpha": 1.0,
                "pipe_diameter_m": 0.12,
                "gas_temperature_K": _T_K,
                "carrier_gas": "N2",
                "hkl_upper_bound_transport_species": tuple(hkl),
                "surface_oxygen_bound": surface_oxygen_bound,
                # Nested series key is the film switch. A parent False is
                # green's audited-default ValueError, not a second switch.
                "evaporation_series_resistance": {
                    "gas_resistance_enabled": film_on,
                },
            },
        )
    )
    assert str(result.status) == "ok", result.diagnostic
    return result.diagnostic or {}


def _assert_host_bound(entry: str, film_on: bool) -> None:
    from simulator.condensation import _knudsen_number

    partials = {"O2": 100.0}
    hkl: frozenset[str] = frozenset()
    compiled, legacy = _catalog()
    answer = _bound_answer("open")
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    # Nested override is green's film switch. A top-level kernel flag
    # is not an input; the generative placement test pins that.
    kernel = {
        "evaporation_series_resistance": {"gas_resistance_enabled": film_on},
    }
    # Persisted headspace for the live path. Reclose starts from an empty
    # headspace and then enters with the O2 vector, the capacity iteration.
    headspace = {"O2": 1.0} if entry == "live" else {}
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=0.0,
        p_total_mbar=0.0,
        headspace_partials_mbar=headspace,
        chemistry_kernel=kernel,
    )
    root_bar, root_pa = _bound_cao_root(
        published_ca,
        p_ref_bar,
        partials=partials,
        film_on=film_on,
        hkl=hkl,
        legacy=legacy,
        realized_pa=100.0,
    )
    if entry == "reclose":
        vacuum = _resolve(host, equilibrium)
        _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
        assert vacuum["Ca"] == pytest.approx(ideal_pa, rel=1.0e-4)
        reclosed = EvaporationMixin._reclose_langmuir_pressures(host, partials)
        assert reclosed is not None
        assert reclosed.surface_pO2_bar is not None
        assert reclosed.surface_pO2_bar * PA_PER_BAR >= 100.0 * (1.0 - 1.0e-9)
        assert reclosed.surface_pO2_bar != pytest.approx(2.386748965e-5, rel=1.0e-3)
        assert reclosed.pressures_Pa["Ca"] == pytest.approx(root_pa, rel=1.0e-5)
        assert reclosed.surface_pO2_bar == pytest.approx(root_bar, rel=1.0e-5)
        assert reclosed.overhead_bounded is True
        assert reclosed.relative_residual < 1.0e-4
        return

    captured: dict = {}
    _dispatch_real_flux_provider(host, captured)
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    notice = host._last_langmuir_surface_oxygen
    assert notice["surface_pO2_bar"] * PA_PER_BAR >= 100.0 * (1.0 - 1.0e-9)
    assert notice["surface_pO2_bar"] != pytest.approx(2.386748965e-5, rel=1.0e-3)
    assert notice["overhead_bounded"] is True
    assert float(notice["relative_residual"]) < 1.0e-4
    controls = captured["controls"]
    # C7: commanded total is 0 and 100 Pa of O2 is present. The realized
    # total is that partial. Kn is this one pressure, including when the
    # film flag is off and R_gas stays 0. It is not a second vacuum total.
    assert float(controls["commanded_pressure_pa"]) == pytest.approx(0.0)
    assert float(controls["overhead_pressure_pa"]) == pytest.approx(100.0)
    assert controls["overhead_partials_Pa"]["O2"] == pytest.approx(100.0)
    kernel_map = controls[CONTROL_FLUX_PRESSURES_KEY]
    assert kernel_map["Ca"] == pytest.approx(root_pa, rel=1.0e-5)
    provider = captured["provider"]
    assert provider is not None and str(provider.status) == "ok", provider
    series = (provider.diagnostic or {}).get("evaporation_series_resistance") or {}
    ca_diag = series["Ca"]
    assert ca_diag["P_bulk_Pa"] == pytest.approx(0.0)
    expected_kn = _knudsen_number(
        100.0,
        float(controls["gas_temperature_K"]),
        float(controls["pipe_diameter_m"]),
        carrier_gas=str(controls["carrier_gas"]),
    )
    assert ca_diag["knudsen_number"] == pytest.approx(expected_kn, rel=1.0e-9)
    if film_on:
        assert float(ca_diag["r_gas"]) > 0.0
    else:
        assert float(ca_diag["r_gas"]) == pytest.approx(0.0)
    # The open root owns the O2 lower bound. The provider call below
    # is that same flag, not a gate-closed dispatch.
    assert controls["surface_oxygen_bound"] is True
    diagnostic = _provider_drive_bulks(
        film_on=film_on,
        surface_oxygen_bound=True,
    )
    series = diagnostic.get("evaporation_series_resistance") or {}
    assert series["O2"]["P_bulk_Pa"] == pytest.approx(
        _spec_drive_bulk(
            "O2", 100.0, film_on=film_on, hkl=hkl, surface_oxygen_bound=True
        )
    )
    assert series["Ca"]["P_bulk_Pa"] == pytest.approx(
        _spec_drive_bulk(
            "Ca", 50.0, film_on=film_on, hkl=hkl, surface_oxygen_bound=True
        )
    )


@pytest.mark.parametrize("case", _bound_cases(), ids=_bound_case_id)
def test_overhead_o2_bound_holds_on_every_transport_branch(case: tuple) -> None:
    """While O2 is net-leaving, surface pO2 is not below the overhead partial.

    Invariant, one drive rule for the closure and the flux publisher.
    Branches: film on and off, HKL membership of O2 and of Ca, partials
    none / O2 / O2+Ca, and the open, floor, and buffered gates. Entries:
    ``apply_langmuir_surface_oxygen`` with that transport, the live
    evaporation path, and the capacity-iteration reclose. A disabled
    film with 100 Pa overhead O2 is the vacuum root 2.38675e-5 bar
    before the rule and a surface just above 1e-3 bar after it.
    Buffered in-band publication stays on the mass-action map.
    """

    entry, film_on, hkl_name, partial_name, gate = case
    if entry == "direct":
        _assert_direct_bound(film_on, hkl_name, partial_name, gate)
    else:
        _assert_host_bound(entry, film_on)


# Equilibrium relative to each species' own overhead partial.
# ``above`` is the reviewer's 250 Pa O2 against 100 Pa of overhead.
_DRIVE_PARTIALS = {"O2": 100.0, "Ca": 50.0}
_DRIVE_PEQ = {
    "above": 2.5,
    "equal": 1.0,
    "below": 0.5,
}


def _drive_cases() -> list[tuple]:
    return [
        (film_on, hkl_name, bound, peq_name)
        for film_on in (False, True)
        for hkl_name in _BOUND_HKL
        for bound in (False, True)
        for peq_name in _DRIVE_PEQ
    ]


def _drive_case_id(case: tuple) -> str:
    film_on, hkl_name, bound, peq_name = case
    film = "film" if film_on else "nofilm"
    owner = "root" if bound else "green"
    return f"{film}-hkl-{hkl_name}-{owner}-{peq_name}"


@pytest.mark.parametrize("case", _drive_cases(), ids=_drive_case_id)
def test_o2_backpressure_follows_the_surface_root_on_every_branch(case: tuple) -> None:
    """Gate-closed O2 drive equals green. The root alone keeps the lower bound.

    Invariant. ``species_drive_bulk_Pa`` is the only melt-surface bulk.
    The closure law calls it with the root owning the drive. The flux
    publisher's gas-transport scan and its emitted flux both call it
    with ``surface_oxygen_bound`` from that root. Branches: film on and
    off, HKL membership of O2 and of Ca, the root owning the drive or
    not, and equilibrium above, at, and below the overhead partial.
    Species: O2 and Ca.

    The reviewer's closed-gate case is ``nofilm-hkl-none-green-above``
    and ``nofilm-hkl-none-green-equal``: 250 Pa of O2 against 100 Pa of
    overhead is a 250 Pa drive, and an equilibrium at or below 100 Pa
    still evaporates. ``nofilm-hkl-O2-green-above`` and
    ``film-hkl-O2-green-above`` are that hole for an HKL upper bound.
    With the root owning the drive, O2 bulk stays the partial on every
    one of those branches. Ca never does unless the film is on and Ca
    is not the HKL carrier.

    The closure law's bulk is the root-owned oracle on every row. A
    publisher that applies the lower bound with the gate closed fails
    the green rows, and a root that drops it fails the law rows.
    """

    from engines.builtin.evaporation_flux import species_drive_bulk_Pa
    from simulator.langmuir_surface_oxygen import publisher_net_law

    film_on, hkl_name, bound, peq_name = case
    hkl = _BOUND_HKL[hkl_name]
    scale = _DRIVE_PEQ[peq_name]
    pressures = {
        species: partial * scale
        for species, partial in _DRIVE_PARTIALS.items()
    }
    transport = _bound_transport(film_on, hkl, _DRIVE_PARTIALS)
    diagnostic = _provider_drive_bulks(
        film_on=film_on,
        surface_oxygen_bound=bound,
        hkl=hkl,
        pressures_Pa=pressures,
    )
    flux = diagnostic.get("evaporation_flux_kg_hr") or {}
    series = diagnostic.get("evaporation_series_resistance") or {}
    masses = {"O2": _M_O2, "Ca": _M_CA}
    for species, partial in _DRIVE_PARTIALS.items():
        publisher_bulk = _spec_drive_bulk(
            species,
            partial,
            film_on=film_on,
            hkl=hkl,
            surface_oxygen_bound=bound,
        )
        root_bulk = _spec_drive_bulk(
            species,
            partial,
            film_on=film_on,
            hkl=hkl,
            surface_oxygen_bound=True,
        )
        assert species_drive_bulk_Pa(
            species,
            partial,
            gas_resistance_enabled=film_on,
            hkl_upper_bound=species in hkl,
            surface_oxygen_bound=bound,
        ) == pytest.approx(publisher_bulk)
        _k, law_bulk = publisher_net_law(
            species=species,
            alpha=1.0,
            molar_mass_kg_per_mol=masses[species],
            bulk_pressure_Pa=partial,
            temperature_K=_T_K,
            transport=transport,
        )
        assert law_bulk == pytest.approx(root_bulk)
        if pressures[species] > publisher_bulk:
            assert series[species]["P_bulk_Pa"] == pytest.approx(publisher_bulk)
            assert flux[species] > 0.0
        else:
            assert species not in flux
            assert species not in series


@pytest.mark.parametrize("gate", ("open", "floor", "buffered"))
@pytest.mark.parametrize("film_on", (False, True))
def test_surface_oxygen_bound_is_set_only_by_a_closed_root(
    gate: str,
    film_on: bool,
) -> None:
    """The live publisher learns the O2 lower bound from the root, not the film.

    Open and floor gates set the flag. A buffered in-band melt does
    not, with the film on or off. The capacity-iteration reclose uses
    the same predicate: applied, and a solved surface.
    """

    compiled, legacy = _catalog()
    answer = _bound_answer(gate)
    diagnostic = answer.diagnostic
    published, _ca, _p_ref = _published_ca(diagnostic)
    kernel = {
        "evaporation_series_resistance": {"gas_resistance_enabled": film_on},
    }
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=_bound_capacity(gate),
        p_total_mbar=0.0,
        headspace_partials_mbar={"O2": 1.0},
        chemistry_kernel=kernel,
    )
    captured: dict = {}
    _dispatch_real_flux_provider(host, captured)
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    owns = gate != "buffered"
    assert captured["controls"]["surface_oxygen_bound"] is owns
    notice = getattr(host, "_last_langmuir_surface_oxygen", None)
    if owns:
        assert isinstance(notice, dict)
        assert notice.get("surface_pO2_bar") is not None
    else:
        assert not isinstance(notice, dict) or notice.get("surface_pO2_bar") is None
    reclosed = EvaporationMixin._reclose_langmuir_pressures(host, {"O2": 100.0})
    assert reclosed is not None
    assert bool(
        reclosed.applied and reclosed.surface_pO2_bar is not None
    ) is owns


# d-376 round 6. The vapor map is not an allowlist. Every positive
# persisted partial is in the film, including a gas that is not a
# vapor species and not O2. Kn and Chapman-Enskog diffusivity use
# that one realized total: max(sum of the vector, commanded pressure).
def _persisted_partial_classes() -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    from simulator.transport_constants import CARRIER_GAS_PROPERTIES

    rows = [
        (species, ((species, 10.0),))
        for species in sorted(CARRIER_GAS_PROPERTIES)
    ]
    # Neither a transport-catalog carrier nor a pure-CaO vapor species.
    rows.append(("CO", (("CO", 10.0),)))
    # In the vapor map. The old allowlist kept it only when the caller
    # passed that key.
    rows.append(("Ca", (("Ca", 10.0),)))
    # Reviewer's underestimate: 10 mbar N2 plus 1 mbar O2. Keeping only
    # O2 publishes 100 Pa instead of 1100 Pa.
    rows.append(("N2+O2", (("N2", 10.0), ("O2", 1.0))))
    return tuple(rows)


_PERSISTED_PARTIAL_CLASSES = _persisted_partial_classes()
_PERSISTED_ENTRIES = (
    "reader",
    "film",
    "live-open",
    "live-open-nofilm",
    "live-floor",
    "live-buffered",
    "live-above",
    "override-keeps",
    "override-omits",
    "reclose",
)
_JUNK_PARTIALS_MBAR = {
    "junk0": 0.0,
    "junkneg": -2.0,
    "junknan": float("nan"),
}


def _persisted_case_id(case: tuple) -> str:
    name, _pairs, entry = case
    return f"{entry}-{name}"


def _persisted_cases() -> list[tuple]:
    return [
        (name, pairs, entry)
        for name, pairs in _PERSISTED_PARTIAL_CLASSES
        for entry in _PERSISTED_ENTRIES
    ]


def _expected_persisted_pa(pairs: tuple[tuple[str, float], ...]) -> dict[str, float]:
    # Persisted composition is mbar. The bulk reader converts with * 100.
    return {species: mbar * 100.0 for species, mbar in pairs}


def _composition_with_junk(pairs: tuple[tuple[str, float], ...]) -> dict[str, float]:
    composition = {species: mbar for species, mbar in pairs}
    composition.update(_JUNK_PARTIALS_MBAR)
    return composition


def _realized_transport_pa(partials_pa: Mapping[str, float], commanded_mbar: float) -> float:
    return max(
        sum(value for value in partials_pa.values() if value > 0.0),
        max(0.0, commanded_mbar) * 100.0,
    )


def _assert_reader_keeps_every_persisted_gas(
    host,
    published: Mapping[str, float],
    expected_pa: Mapping[str, float],
) -> None:
    """The vapor map cannot narrow the persisted vector.

    Decoys are the three allowlists the old reader used: nothing, Ca
    alone, and the pure-CaO publication. O2 used to be appended after
    that intersection, which still dropped N2.
    """

    decoys = ({}, {"Ca": 1.0}, dict(published))
    for decoy in decoys:
        got = EvaporationMixin._langmuir_headspace_partials_Pa(host, decoy)
        assert set(got) == set(expected_pa), (decoy, got, expected_pa)
        for species, pa in expected_pa.items():
            assert got[species] == pytest.approx(pa)
        for junk in _JUNK_PARTIALS_MBAR:
            assert junk not in got


def _assert_film_sums_the_vector(host, expected_pa: Mapping[str, float]) -> None:
    for commanded_mbar in (0.0, _FILM_COMMANDS_MBAR["above"]):
        host.melt.p_total_mbar = commanded_mbar
        cleaned, pressure = EvaporationMixin._langmuir_film(host, expected_pa)
        realized = _realized_transport_pa(expected_pa, commanded_mbar)
        assert pressure == pytest.approx(realized)
        for species, pa in expected_pa.items():
            assert cleaned[species] == pytest.approx(pa)
        for junk in _JUNK_PARTIALS_MBAR:
            assert junk not in cleaned


def _assert_transport_state(
    controls: Mapping,
    provider,
    expected_pa: Mapping[str, float],
    *,
    realized_pa: float,
    film_on: bool,
) -> None:
    from simulator.condensation import _chapman_enskog_d_ab_m2_s, _knudsen_number
    from simulator.transport_constants import VISCOUS_KNUDSEN_MAX

    assert float(controls["overhead_pressure_pa"]) == pytest.approx(realized_pa)
    kernel_partials = controls["overhead_partials_Pa"]
    for species, pa in expected_pa.items():
        assert kernel_partials[species] == pytest.approx(pa), kernel_partials
    series = (provider.diagnostic or {}).get("evaporation_series_resistance") or {}
    # A species whose equilibrium pressure sits under its bulk has no
    # drive, and the provider records no series row for it. Kn is a
    # property of the total, so any row that was computed is the check.
    if not series:
        assert realized_pa <= 0.0 or float(expected_pa.get("Ca", 0.0)) > 0.0
        return
    probe = "Ca" if "Ca" in series else next(iter(series))
    probed = series[probe]
    got_kn = float(probed["knudsen_number"])
    if realized_pa <= 0.0:
        # Commanded total 0 and no partials. Kn is the vacuum limit.
        assert math.isinf(got_kn)
    else:
        expected_kn = _knudsen_number(
            realized_pa,
            float(controls["gas_temperature_K"]),
            float(controls["pipe_diameter_m"]),
            carrier_gas=str(controls["carrier_gas"]),
        )
        assert got_kn == pytest.approx(expected_kn, rel=1.0e-9, abs=0.0)
        assert math.isfinite(got_kn)
    if film_on and realized_pa > 0.0:
        assert float(probed["knudsen_number"]) < VISCOUS_KNUDSEN_MAX
        assert float(probed["d_ab_m2_s"]) > 0.0
        assert float(probed["r_gas"]) > 0.0
        if probe == "Ca":
            # Ca has no Lennard-Jones proxy, so this call is the
            # publisher's diffusivity at the realized total.
            expected_d = _chapman_enskog_d_ab_m2_s(
                "Ca",
                float(controls["gas_temperature_K"]),
                realized_pa,
                carrier=str(controls["carrier_gas"]),
            )
            assert probed["d_ab_m2_s"] == pytest.approx(expected_d, rel=1.0e-9)
    else:
        assert float(probed["r_gas"]) == pytest.approx(0.0)


def _two_species_root_is_the_balance(
    expected_pa: Mapping[str, float],
    *,
    gate: str,
) -> bool:
    """Ca+O2 is the whole oxygen balance only while Ca's bulk is zero.

    A 1000 Pa Ca bulk chokes that carrier. CaO(g) and Ca2 then carry
    the oxygen, so the two-species root is not the solver's root.
    The floor publication has the same problem once a film makes the
    carriers' conductances differ. Open gate, no Ca bulk: the
    two-species root is the independent anchor.
    """

    return gate == "open" and float(expected_pa.get("Ca", 0.0)) <= 0.0


def _assert_closed_surface(
    notice: Mapping,
    kernel_map: Mapping,
    expected_pa: Mapping[str, float],
    *,
    published_ca: float,
    p_ref_bar: float,
    legacy,
    film_on: bool,
    realized_pa: float,
    gate: str,
) -> None:
    bulk_o2 = float(expected_pa.get("O2", 0.0))
    bulk_ca = float(expected_pa.get("Ca", 0.0))
    assert float(notice["relative_residual"]) < 1.0e-4
    if bulk_o2 > 0.0:
        assert notice["overhead_bounded"] is True
        assert notice["surface_pO2_bar"] * PA_PER_BAR >= bulk_o2 * (1.0 - 1.0e-9)
    else:
        assert notice["overhead_bounded"] is False
    if film_on and not _two_species_root_is_the_balance(expected_pa, gate=gate):
        return
    if film_on:
        root_bar, root_pa = _series_cao_root(
            published_ca,
            p_ref_bar,
            bulk_o2_pa=bulk_o2,
            bulk_ca_pa=bulk_ca,
            overhead_pa=realized_pa,
            temperature_K=_T_K,
            legacy=legacy,
        )
    else:
        root_bar, root_pa = _bound_cao_root(
            published_ca,
            p_ref_bar,
            partials={"O2": bulk_o2, "Ca": bulk_ca},
            film_on=False,
            hkl=frozenset(),
            legacy=legacy,
            realized_pa=realized_pa,
        )
    assert notice["surface_pO2_bar"] == pytest.approx(root_bar, rel=1.0e-5)
    assert kernel_map["Ca"] == pytest.approx(root_pa, rel=1.0e-5)


def _assert_persisted_headspace(pairs: tuple[tuple[str, float], ...], entry: str) -> None:
    expected_pa = _expected_persisted_pa(pairs)
    composition = _composition_with_junk(pairs)
    if entry == "live-floor":
        gate = "floor"
    elif entry == "live-buffered":
        gate = "buffered"
    else:
        gate = "open"
    film_on = entry != "live-open-nofilm"
    commanded_mbar = _FILM_COMMANDS_MBAR["above"] if entry == "live-above" else 0.0
    explicit = entry in {"override-keeps", "override-omits"}
    answer = _bound_answer("floor" if gate == "floor" else "open")
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    compiled, legacy = _catalog()
    if entry == "override-keeps":
        headspace: dict[str, float] = {}
    elif entry == "reclose":
        headspace = {}
    else:
        headspace = composition
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=_film_capacity(gate),
        p_total_mbar=commanded_mbar,
        headspace_partials_mbar=headspace,
        chemistry_kernel={
            "evaporation_series_resistance": {
                "gas_resistance_enabled": film_on,
            },
        },
    )
    if entry == "reader":
        _assert_reader_keeps_every_persisted_gas(host, published, expected_pa)
        return
    if entry == "film":
        _assert_film_sums_the_vector(host, expected_pa)
        return

    if entry == "reclose":
        vacuum = _resolve(host, equilibrium)
        _surface, ideal_pa, _flux = _congruent_cao_surface(published_ca, p_ref_bar, legacy=legacy)
        assert vacuum["Ca"] == pytest.approx(ideal_pa, rel=1.0e-4)
        reclosed = EvaporationMixin._reclose_langmuir_pressures(host, expected_pa)
        assert reclosed is not None
        assert reclosed.relative_residual < 1.0e-4
        realized = _realized_transport_pa(expected_pa, 0.0)
        if expected_pa.get("O2", 0.0) > 0.0:
            assert reclosed.overhead_bounded is True
            assert reclosed.surface_pO2_bar * PA_PER_BAR >= float(
                expected_pa["O2"]
            ) * (1.0 - 1.0e-9)
        else:
            assert reclosed.overhead_bounded is False
        if _two_species_root_is_the_balance(expected_pa, gate="open"):
            root_bar, root_pa = _series_cao_root(
                published_ca,
                p_ref_bar,
                bulk_o2_pa=float(expected_pa.get("O2", 0.0)),
                bulk_ca_pa=0.0,
                overhead_pa=realized,
                temperature_K=_T_K,
                legacy=legacy,
            )
            assert reclosed.surface_pO2_bar == pytest.approx(root_bar, rel=1.0e-5)
            assert reclosed.pressures_Pa["Ca"] == pytest.approx(root_pa, rel=1.0e-5)
        else:
            # Ca bulk chokes that carrier. The root is not the vacuum one.
            assert reclosed.surface_pO2_bar != pytest.approx(
                _surface, rel=1.0e-3
            )
        return

    captured: dict = {}
    _dispatch_real_flux_provider(host, captured)
    override = None
    if entry == "override-keeps":
        override = dict(expected_pa)
    elif entry == "override-omits":
        override = {}
    EvaporationMixin._calculate_evaporation(
        host,
        equilibrium,
        overhead_partials_override_Pa=override,
    )
    context = host._langmuir_flux_closure_context
    notice = getattr(host, "_last_langmuir_surface_oxygen", None)
    root_closed = isinstance(notice, dict) and notice.get("surface_pO2_bar") is not None
    if gate == "buffered" and not explicit:
        assert not root_closed
        for species, pa in expected_pa.items():
            assert context["film_partials_Pa"][species] == pytest.approx(pa)
        assert context["film_pressure_Pa"] == pytest.approx(
            _realized_transport_pa(expected_pa, commanded_mbar)
        )
        kernel_partials = captured["controls"]["overhead_partials_Pa"]
        # Amendment 1 carve-out: the publisher does not adopt the unused
        # film. A vapor-map species stays; a non-vapor carrier does not.
        for species, pa in expected_pa.items():
            if species in published:
                assert kernel_partials[species] == pytest.approx(pa)
            else:
                assert species not in kernel_partials
        return

    publisher_pa = {} if entry == "override-omits" else expected_pa
    realized = _realized_transport_pa(publisher_pa, commanded_mbar)
    assert root_closed, notice
    for species, pa in publisher_pa.items():
        assert context["film_partials_Pa"][species] == pytest.approx(pa)
    for species in expected_pa:
        if entry == "override-omits":
            assert species not in context["film_partials_Pa"]
    assert context["film_pressure_Pa"] == pytest.approx(realized)
    assert captured["provider_error"] is None
    provider = captured["provider"]
    assert provider is not None and str(provider.status) == "ok", provider
    _assert_transport_state(
        captured["controls"],
        provider,
        publisher_pa,
        realized_pa=realized,
        film_on=film_on,
    )
    _assert_closed_surface(
        notice,
        captured["controls"][CONTROL_FLUX_PRESSURES_KEY],
        publisher_pa,
        published_ca=published_ca,
        p_ref_bar=p_ref_bar,
        legacy=legacy,
        film_on=film_on,
        realized_pa=realized,
        gate=gate,
    )
    assert float(captured["controls"]["commanded_pressure_pa"]) == pytest.approx(
        commanded_mbar * 100.0
    )


@pytest.mark.parametrize(
    "case",
    _persisted_cases(),
    ids=_persisted_case_id,
)
def test_persisted_headspace_vector_sets_transport_on_every_path(case: tuple) -> None:
    """No persisted gas is dropped before the film sums the total.

    Invariant: the persisted composition is one partial vector. Every
    positive finite entry is in the film whenever that film is built
    from the persisted headspace, whether or not the gas is in the
    vapor map. The realized total is ``max(sum of that vector,
    commanded melt pressure)``, and Kn and diffusivity use it. An
    explicit vector is not filled from the persisted map. A step that
    did not close a root keeps vapor-map backpressure; its stored film
    still holds the complete vector.

    Classes: every transport-catalog carrier, CO (in neither catalog),
    Ca (in the vapor map), and N2 plus O2. Paths: the reader against
    three vapor-map decoys, the film sum, live open / floor / buffered
    / commanded-above, film off, an override that keeps the gas, an
    override that omits a persisted gas, and reclose.
    """

    _name, pairs, entry = case
    _assert_persisted_headspace(pairs, entry)


# Open-gate carriers. ``n`` is the mass-action exponent. Oxygen weight
# is ``2*n``. Ca is the congruent metal (n = -1/2 from 2 Ca + O2).
# AlO2, CrO2 and CrO3 are the catalog's positive exponents. TiO2_gas
# is n = 0. SiO is transport. NotAGas does not parse. Ar has no oxygen,
# so its parent demand is negative.
_N_SIGN_ROWS = (
    ("metal", "Ca", 1000.0, -0.5, 1.0e-9),
    ("positive", "AlO2", 100.0, 0.25, 1.0e-9),
    ("positive", "CrO2", 80.0, 0.25, 1.0e-9),
    ("positive", "CrO3", 40.0, 0.75, 1.0e-9),
    ("neutral", "TiO2_gas", 50.0, 0.0, 1.0e-9),
    ("transport", "SiO", 25.0, -0.5, 1.0e-6),
    ("unparsed", "NotAGas", 10.0, 0.25, 1.0e-9),
    ("negative_demand", "Ar", 12.0, 0.25, 1.0e-9),
)
_N_SIGN_BY_SPECIES = {row[1]: row for row in _N_SIGN_ROWS}
_N_SIGN_T_K = 2473.15
_N_SIGN_P_REF = 1.0e-9


def _n_sign_provenance(role: str, exponent: float, p_ref: float) -> dict:
    if role == "metal":
        return {
            "pressure_rail": "gas_fugacity",
            "pO2_bar": p_ref,
            "activity_factor": 0.2,
        }
    if role == "transport":
        return {
            "oxygen_fugacity_channel": "transport_headspace",
            "pO2_bar": p_ref,
            "pO2_exponent": exponent,
        }
    return {"pO2_bar": p_ref, "pO2_exponent": exponent}


def _oracle_closed_surface() -> tuple[float, dict[str, float]]:
    """Root of ``sum 2*n*J = 0`` with ``J = P`` (k = 1, bulk = 0).

    Written from the oxygen weight. It does not call the surface
    solver, the pressure law, or the conductance owner. Entrants are
    the metal and the positive-exponent carriers, plus synthetic O2
    at ``n = +1``. The bracket is log10(pO2/bar) on [-30, 0].
    """

    from scipy.optimize import brentq

    entrants = [
        row for row in _N_SIGN_ROWS if row[0] in ("metal", "positive")
    ]

    def residual(logp: float) -> float:
        surface = 10.0 ** logp
        total = 0.0
        for _role, _species, pressure, exponent, p_ref in entrants:
            solved = pressure * (surface / p_ref) ** exponent
            total += 2.0 * exponent * solved
        total += 2.0 * surface * PA_PER_BAR
        return total

    low = residual(-30.0)
    high = residual(0.0)
    assert low < 0.0
    assert high > 0.0
    log_root = float(brentq(residual, -30.0, 0.0, xtol=1.0e-12, rtol=1.0e-12))
    assert -30.0 < log_root < 0.0
    surface = 10.0 ** log_root
    pressures = {
        species: pressure * (surface / p_ref) ** exponent
        for _role, species, pressure, exponent, p_ref in entrants
    }
    assert abs(residual(log_root)) / max(abs(low), abs(high)) < 1.0e-8
    return surface, pressures


def _n_sign_mass_catalog() -> dict:
    """Publisher masses for entrants ``MOLAR_MASS`` does not carry.

    AlO2, CrO2 and CrO3 have no ``MOLAR_MASS`` entry. With
    ``legacy_catalog=None`` the publisher emits no flux for them, the
    closure refuses them, and they drop out of the oxygen sum. The
    conductance override is 1, so the gram value does not enter the
    law the oracle sums. Formula grams keep those three in the residual.
    """

    metals = {}
    for role, species, _pressure, _exponent, _p_ref in _N_SIGN_ROWS:
        if role not in ("metal", "positive"):
            continue
        grams = parse_formula(species).molar_mass_kg_per_mol() * 1000.0
        metals[species] = {"molar_mass_g_mol": grams}
    return {"metals": metals}


@functools.lru_cache(maxsize=2)
def _n_sign_surface(mode: str):
    """One real ``apply_langmuir_surface_oxygen`` call per mode."""

    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    pressures = {}
    provenance = {}
    for role, species, pressure, exponent, p_ref in _N_SIGN_ROWS:
        pressures[species] = pressure
        provenance[species] = _n_sign_provenance(role, exponent, p_ref)
    if mode == "closed":
        conductance = {
            species: 1.0
            for role, species, *_rest in _N_SIGN_ROWS
            if role in ("metal", "positive")
        }
        conductance["O2"] = 1.0
    elif mode == "refused":
        # A negative override raises inside the chokepoint after the
        # channel loop has classified every carrier. A 1 bar O2
        # overhead does not: n > 0 still supplies oxygen, so that
        # residual can change sign. The pure-CaO overhead failure is
        # the other test.
        conductance = {"O2": -1.0}
    else:
        raise AssertionError(mode)
    return apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=_N_SIGN_T_K,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=_N_SIGN_P_REF,
        legacy_catalog=_n_sign_mass_catalog(),
        compiled_catalog=None,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
        conductance_mol_s_m2_Pa=conductance,
        allow_unmeasured_alpha_fallback=True,
    )


def _rule_outcome(role: str, published: float, oracle_pressure: float | None, mode: str):
    """Independent expectation. Not the production classifier.

    An open gate rewrites every n != 0 carrier that can enter the sum,
    and refuses every other n != 0 carrier. n == 0 and transport stay
    at the published pressure. A failed solve refuses the whole n != 0
    set, both signs. The old defect treated n >= 0 as published.
    """

    if role in ("neutral", "transport"):
        return ("published", published)
    if mode == "refused" or role in ("unparsed", "negative_demand"):
        return ("refused", None)
    assert oracle_pressure is not None
    return ("pressure", oracle_pressure)


def _bug_outcome(role: str, published: float, oracle_pressure: float | None, mode: str):
    """The defect: n >= 0 stays at the published pressure.

    n < 0 still follows the rule. Neutral and transport agree with
    the rule, so they do not distinguish the bug.
    """

    if role in ("positive", "unparsed", "negative_demand"):
        return ("published", published)
    return _rule_outcome(role, published, oracle_pressure, mode)


@pytest.mark.parametrize("mode", ["closed", "refused"])
@pytest.mark.parametrize("species", [row[1] for row in _N_SIGN_ROWS])
def test_open_gate_closes_or_refuses_every_nonzero_exponent(mode: str, species: str) -> None:
    """n != 0 is the closed surface law or a typed refusal. n > 0 is not neutral.

    Invariant, one chokepoint (``apply_langmuir_surface_oxygen``): once
    the d-376 gate is open, every non-transport carrier with n != 0,
    a reference pO2, and a positive pressure is rewritten to the shared
    surface law or removed with a typed refusal. It is never left at
    the unclosed published pressure. n == 0 and transport stay
    published. An unparsable formula and a negative oxygen demand
    refuse on both the closed root and the failed solve.

    The mutation is a predicate that keeps every n >= 0 pressure
    published. Positive, unparsed, and negative-demand rows disagree
    with that predicate. Neutral and transport agree with it.
    """

    role, _species, published, exponent, _p_ref = _N_SIGN_BY_SPECIES[species]
    result = _n_sign_surface(mode)
    surface, oracle_pressures = _oracle_closed_surface()
    oracle_pressure = oracle_pressures.get(species)
    outcome = _rule_outcome(role, published, oracle_pressure, mode)
    bug = _bug_outcome(role, published, oracle_pressure, mode)
    assert result.applied is True
    if mode == "closed":
        assert result.surface_pO2_bar == pytest.approx(surface, rel=1.0e-8)
        assert result.relative_residual is not None
        assert result.relative_residual < 1.0e-6
    else:
        assert result.surface_pO2_bar is None
    if outcome[0] == "published":
        assert result.pressures_Pa[species] == published
        assert species not in result.refused_species
    elif outcome[0] == "refused":
        assert species not in result.pressures_Pa
        reason = result.refused_species[species]
        assert reason
        assert "0.0" not in reason
        if role == "unparsed":
            assert "formula unresolved" in reason
        if role == "negative_demand":
            assert "oxygen demand is negative" in reason
    else:
        assert oracle_pressure is not None
        assert result.pressures_Pa[species] == pytest.approx(
            oracle_pressure, rel=1.0e-8
        )
        assert result.pressures_Pa[species] != pytest.approx(published, rel=1.0e-3)
        assert species not in result.refused_species
    if role in ("positive", "unparsed", "negative_demand"):
        assert outcome != bug
    else:
        assert outcome == bug
    if role == "positive" and mode == "closed":
        assert exponent > 0.0


def test_vapour_debit_floor_is_the_publisher_threshold() -> None:
    """Melt debit and the publisher share one threshold object."""

    from engines.builtin.evaporation_flux import _NONTRIVIAL_FLUX_KG_HR
    from simulator.evaporation import _vapour_debit_floor_kg_hr

    assert _vapour_debit_floor_kg_hr() is _NONTRIVIAL_FLUX_KG_HR


def test_hertz_knudsen_sqrt_has_one_owner() -> None:
    """Surface and publisher call the Knudsen coefficient. They do not re-derive it."""

    from simulator.langmuir_surface_oxygen import _ideal_molar_conductance

    root = Path(__file__).resolve().parents[1]
    surface = (root / "simulator/langmuir_surface_oxygen.py").read_text()
    publisher = (root / "engines/builtin/evaporation_flux.py").read_text()
    owner = (root / "simulator/chemistry/langmuir_knudsen.py").read_text()
    assert "2.0 * math.pi" not in surface
    assert "2.0 * math.pi" not in publisher
    assert owner.count("math.sqrt(") == 1
    alpha = 0.37
    molar_mass = 0.055845
    temperature_K = 2100.0
    assert _ideal_molar_conductance(alpha, molar_mass, temperature_K) == (
        alpha
        * hertz_knudsen_k_kg_s_m2_pa(temperature_K, molar_mass)
        / molar_mass
    )


# Open-gate carriers the fallback-on matrix never builds. Ca is the only
# residual entrant (measured alpha 1). Zr and AlO2 have no alpha row.
# CrO2 is a measured alpha of 0. TiO2_gas is n = 0. SiO is transport.
# The dust floor and the nontrivial-flux threshold are both 1e-12, the
# publisher's two gates. This oracle writes those numbers; it does not
# call the publisher's refusal helper.
_DUST_PARENT_KG = 1.0e-12
_FLUX_THRESHOLD_KG_HR = 1.0e-12
_UNMEASURED_ROWS = (
    ("Ca", "measured", 1000.0, -0.5, 1.0e-9, "Ca"),
    ("Zr", "unmeasured", 800.0, -0.5, 1.0e-9, "Zr"),
    ("AlO2", "unmeasured", 100.0, 0.25, 1.0e-9, "AlO2"),
    ("CrO2", "zero_alpha", 80.0, 0.25, 1.0e-9, "CrO2"),
    ("TiO2_gas", "neutral", 50.0, 0.0, 1.0e-9, "TiO2"),
    ("SiO", "transport", 25.0, -0.5, 1.0e-6, "SiO"),
)
_UNMEASURED_BY_SPECIES = {row[0]: row for row in _UNMEASURED_ROWS}


def _unmeasured_catalog() -> dict:
    metals: dict[str, dict] = {}
    oxides: dict[str, dict] = {}
    for species, role, _pressure, _exponent, _pref, formula in _UNMEASURED_ROWS:
        row: dict = {"formula": formula}
        if role == "measured":
            row["evaporation_alpha"] = {"value": 1.0}
        elif role == "zero_alpha":
            row["evaporation_alpha"] = {"value": 0.0}
        bucket = metals if species in ("Ca", "Zr") else oxides
        bucket[species] = row
    return {"metals": metals, "oxide_vapors": oxides}


def _unmeasured_provenance(role: str, exponent: float, p_ref: float) -> dict:
    if role == "transport":
        return {
            "oxygen_fugacity_channel": "transport_headspace",
            "pO2_bar": p_ref,
            "pO2_exponent": exponent,
        }
    return {"pO2_bar": p_ref, "pO2_exponent": exponent}


def _oracle_ca_only_surface() -> tuple[float, dict[str, float]]:
    """Root of ``sum 2*n*J = 0`` with only Ca and synthetic O2.

    J = P (k = 1, bulk = 0). Zr, AlO2 and CrO2 are not entrants: the
    first two have no emitting alpha, and CrO2 has alpha 0 so its flux
    is identically zero. Written from that oxygen weight. It does not
    call the surface solver.
    """

    from scipy.optimize import brentq

    pref = 1.0e-9
    published_ca = 1000.0

    def residual(logp: float) -> float:
        surface = 10.0 ** logp
        calcium = published_ca * (surface / pref) ** (-0.5)
        return -calcium + 2.0 * surface * PA_PER_BAR

    assert residual(-30.0) < 0.0
    assert residual(0.0) > 0.0
    log_root = float(brentq(residual, -30.0, 0.0, xtol=1.0e-12, rtol=1.0e-12))
    surface = 10.0 ** log_root
    pressures = {
        "Ca": published_ca * (surface / pref) ** (-0.5),
        "CrO2": 80.0 * (surface / pref) ** 0.25,
    }
    return surface, pressures


def _emit_rule(role: str, published: float, closed: float | None) -> tuple:
    """Successful open gate. Not the production classifier.

    An emitting n != 0 carrier is the surface law. An n != 0 carrier
    with no emitting alpha is refused. A measured alpha of 0 is the
    surface law and not a residual term. n == 0 and transport stay
    published.
    """

    if role in ("neutral", "transport"):
        return ("published", published)
    if role == "unmeasured":
        return ("refused", None)
    assert closed is not None
    return ("pressure", closed)


def _emit_bug(role: str, published: float, closed: float | None) -> tuple:
    """The defect: a non-emitting n != 0 carrier keeps the persisted pressure."""

    if role in ("unmeasured", "zero_alpha"):
        return ("published", published)
    return _emit_rule(role, published, closed)


@functools.lru_cache(maxsize=1)
def _unmeasured_surface():
    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    pressures = {}
    provenance = {}
    for species, role, pressure, exponent, p_ref, _formula in _UNMEASURED_ROWS:
        pressures[species] = pressure
        provenance[species] = _unmeasured_provenance(role, exponent, p_ref)
    return apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=_T_K,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=1.0e-9,
        legacy_catalog=_unmeasured_catalog(),
        compiled_catalog=None,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
        conductance_mol_s_m2_Pa={"Ca": 1.0, "O2": 1.0},
        allow_unmeasured_alpha_fallback=False,
    )


@pytest.mark.parametrize("species", [row[0] for row in _UNMEASURED_ROWS])
def test_open_gate_refuses_unmeasured_and_closes_zero_alpha(species: str) -> None:
    """n != 0 with no emitting alpha is refused. Alpha 0 is the surface law.

    The fallback-on matrix never builds an unmeasured carrier. This one
    does, with the fallback off. The mutation keeps those carriers at
    the persisted pressure. Unmeasured and zero-alpha rows disagree
    with it. Measured, neutral and transport agree with it.
    """

    _species, role, published, _exponent, _pref, _formula = _UNMEASURED_BY_SPECIES[
        species
    ]
    result = _unmeasured_surface()
    surface, closed_pressures = _oracle_ca_only_surface()
    closed = closed_pressures.get(species)
    outcome = _emit_rule(role, published, closed)
    bug = _emit_bug(role, published, closed)
    assert result.applied is True
    assert result.surface_pO2_bar == pytest.approx(surface, rel=1.0e-8)
    assert result.relative_residual is not None
    assert result.relative_residual < 1.0e-6
    if outcome[0] == "published":
        assert result.pressures_Pa[species] == published
        assert species not in result.refused_species
    elif outcome[0] == "refused":
        assert species not in result.pressures_Pa
        reason = result.refused_species[species]
        assert "surface pressure omitted" in reason
        assert "alpha" in reason
    else:
        assert closed is not None
        assert result.pressures_Pa[species] == pytest.approx(closed, rel=1.0e-8)
        assert result.pressures_Pa[species] != pytest.approx(published, rel=1.0e-3)
        assert species not in result.refused_species
    if role in ("unmeasured", "zero_alpha"):
        assert outcome != bug
    else:
        assert outcome == bug


def _spec_unmeasured_publisher(parent_kg: float, baseline_kg_hr: float) -> str:
    """Publisher outcome for an unmeasured species, fallback off, no domain refusal.

    Written from the two thresholds. It does not call the flux provider.
    Parent mass above 1e-12 kg and a baseline above 1e-12 kg/h is
    missing-alpha. Every other combination emits alpha 1 with no
    fallback flag. That includes parent mass at or below 1e-12 kg.
    """

    if (
        parent_kg > _DUST_PARENT_KG
        and baseline_kg_hr > _FLUX_THRESHOLD_KG_HR
    ):
        return "missing_alpha"
    return "unflagged_alpha_one"


def _hk_baseline_kg_hr(species: str, pressure_pa: float) -> float:
    molar = parse_formula(species).molar_mass_kg_per_mol()
    return (
        pressure_pa
        * hertz_knudsen_k_kg_s_m2_pa(_T_K, molar)
        * 3600.0
    )


def _dispatch_alpha_map(
    pressures: Mapping[str, float],
    parent_kg: float,
    *,
    alpha: Mapping[str, float] | None = None,
):
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider

    masses = {
        species: parse_formula(
            "TiO2" if species == "TiO2_gas" else species
        ).molar_mass_kg_per_mol()
        for species in pressures
    }
    stoich = {
        species: {"oxide_per_product_kg": 1.0, "parent_oxide": "CaO"}
        for species in pressures
    }
    available = {species: parent_kg for species in pressures}
    provider = BuiltinEvaporationFluxProvider()
    return provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=ProviderAccountView(
                accounts={},
                species_formula_registry={},
            ),
            temperature_C=_T_C,
            pressure_bar=0.0,
            control_inputs={
                "vapour_batch_flux_pressures_Pa": dict(pressures),
                "overhead_partials_Pa": {},
                "overhead_pressure_pa": 0.0,
                "commanded_pressure_pa": 0.0,
                "molar_mass_kg_mol": masses,
                "stoich_by_species": stoich,
                "available_oxide_kg": available,
                "melt_surface_area_m2": 1.0,
                "alpha": dict(alpha or {}),
                "pipe_diameter_m": 0.12,
                "gas_temperature_K": _T_K,
                "carrier_gas": "N2",
                "evaporation_series_resistance": {
                    "gas_resistance_enabled": False,
                },
            },
        )
    )


def _publisher_buckets(diagnostic: Mapping) -> dict:
    below = diagnostic.get("below_threshold_evaporation_flux") or {}
    return {
        "flux": dict(diagnostic.get("evaporation_flux_kg_hr") or {}),
        "alpha": dict(diagnostic.get("alpha_used_by_species") or {}),
        "below": dict(below.get("fluxes_kg_hr") or {}),
        "missing": dict(diagnostic.get("missing_alpha") or {}),
        "fallback": list(
            diagnostic.get("unmeasured_alpha_fallback_species") or []
        ),
    }


@pytest.mark.parametrize("species", ["Zr", "AlO2"])
@pytest.mark.parametrize("parent_kg", [0.0, 1.0e-12, 1.0])
@pytest.mark.parametrize("pressure_pa", [1.0e-20, 1000.0])
def test_unmeasured_open_gate_pressure_is_not_emitted_as_alpha_one(
    species: str, parent_kg: float, pressure_pa: float
) -> None:
    """The surface omits the carrier, so the publisher cannot emit alpha 1.

    Invariant, both consumers: an open gate does not publish an n != 0
    carrier that has no emitting alpha, at any positive pressure. The
    real publisher, given that omitted map, has no flux, no alpha, and
    no missing-alpha row for it. Given the persisted pressure instead
    (the mutation), vacuum overhead leaves the domain diagnostic unset
    and the publisher emits unflagged alpha 1 whenever the parent is at
    or below 1e-12 kg or the alpha-1 baseline is at or below 1e-12 kg/h.
    Both species and both pressures hit that emission for a dust parent.
    """

    from engines.builtin.evaporation_flux import _NONTRIVIAL_FLUX_KG_HR

    assert _NONTRIVIAL_FLUX_KG_HR == _FLUX_THRESHOLD_KG_HR
    result = _unmeasured_surface()
    # The cached surface uses the row pressures, not this parameter.
    # Rebuild when the published pressure is the case under test so a
    # subthreshold row is actually what the chokepoint saw.
    if pressure_pa != _UNMEASURED_BY_SPECIES[species][2]:
        from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

        pressures = {
            row_species: pressure
            for row_species, _role, pressure, _exponent, _pref, _formula in (
                _UNMEASURED_ROWS
            )
        }
        provenance = {
            row_species: _unmeasured_provenance(role, exponent, pref)
            for row_species, role, _pressure, exponent, pref, _formula in (
                _UNMEASURED_ROWS
            )
        }
        pressures[species] = pressure_pa
        result = apply_langmuir_surface_oxygen(
            pressures,
            temperature_K=_T_K,
            provenance_by_species=provenance,
            source_reaction_fO2_bar=1.0e-9,
            legacy_catalog=_unmeasured_catalog(),
            compiled_catalog=None,
            melt_redox_capacity_mol_per_ln_fO2=0.0,
            conductance_mol_s_m2_Pa={"Ca": 1.0, "O2": 1.0},
            allow_unmeasured_alpha_fallback=False,
        )
    assert species not in result.pressures_Pa
    assert species in result.refused_species
    closed = _dispatch_alpha_map(
        result.pressures_Pa,
        parent_kg,
        alpha={"Ca": 1.0, "CrO2": 0.0},
    )
    assert str(closed.status) == "ok", closed.diagnostic
    closed_buckets = _publisher_buckets(closed.diagnostic or {})
    assert species not in closed_buckets["flux"]
    assert species not in closed_buckets["alpha"]
    assert species not in closed_buckets["below"]
    assert species not in closed_buckets["missing"]
    assert species not in closed_buckets["fallback"]
    baseline = _hk_baseline_kg_hr(species, pressure_pa)
    expected = _spec_unmeasured_publisher(parent_kg, baseline)
    if parent_kg <= _DUST_PARENT_KG or baseline <= _FLUX_THRESHOLD_KG_HR:
        assert expected == "unflagged_alpha_one"
    bug = _dispatch_alpha_map({species: pressure_pa}, parent_kg)
    assert str(bug.status) == "ok", bug.diagnostic
    bug_buckets = _publisher_buckets(bug.diagnostic or {})
    if expected == "missing_alpha":
        assert species in bug_buckets["missing"]
        assert species not in bug_buckets["flux"]
        assert species not in bug_buckets["alpha"]
        assert species not in bug_buckets["fallback"]
    else:
        assert bug_buckets["alpha"][species] == pytest.approx(1.0)
        assert species not in bug_buckets["fallback"]
        assert species not in bug_buckets["missing"]
        assert species in bug_buckets["flux"] or species in bug_buckets["below"]
        assert (
            bug_buckets["flux"].get(species, 0.0)
            + bug_buckets["below"].get(species, 0.0)
        ) > 0.0


# Missing pO2 reference. Metal rails do not borrow source fO2. The
# oracle below is that rule: it does not call ``_reference_pO2_bar``,
# ``_needs_surface_balance``, ``_gate_opener``, or
# ``_assemble_open_gate_map``. Ca on a metal rail has Ellingham
# n_M = 2, so the exponent is -1/2. AlO2 is not in that table, so a
# metal rail falls through to the provenance exponent.
_MISSING_REF_FLOOR_BAR = 1.0e-30
_MISSING_REF_NOOP_MOL = 1.0e-15
_MISSING_REF_METAL_RAILS = frozenset({"gas_fugacity", "condensed_raoult_psat"})
_MISSING_REF_N_METAL = {"Ca": 2.0}
_MISSING_REF_T_K = 2473.15


def _missing_ref_scenarios() -> dict[str, tuple[dict, ...]]:
    ca_missing = {
        "species": "Ca",
        "pressure": 1000.0,
        "rail": "gas_fugacity",
        "recorded": None,
        "exponent": None,
        "transport": False,
    }
    ca_recorded = {
        "species": "Ca",
        "pressure": 1000.0,
        "rail": "gas_fugacity",
        "recorded": 1.0e-9,
        "exponent": None,
        "transport": False,
    }
    condensed_missing = {
        "species": "Ca",
        "pressure": 1000.0,
        "rail": "condensed_raoult_psat",
        "recorded": None,
        "exponent": None,
        "transport": False,
    }
    nonmetal = {
        "species": "Mg",
        "pressure": 800.0,
        "rail": "",
        "recorded": None,
        "exponent": -0.5,
        "transport": False,
    }
    alo2_missing = {
        "species": "AlO2",
        "pressure": 100.0,
        "rail": "gas_fugacity",
        "recorded": None,
        "exponent": 0.25,
        "transport": False,
    }
    neutral = {
        "species": "CaO",
        "pressure": 50.0,
        "rail": "",
        "recorded": None,
        "exponent": 0.0,
        "transport": False,
    }
    transport = {
        "species": "SiO",
        "pressure": 25.0,
        "rail": "",
        "recorded": None,
        "exponent": -0.5,
        "transport": True,
    }
    return {
        "gas_fugacity_ca_missing": (ca_missing,),
        "condensed_ca_missing": (condensed_missing,),
        "gas_fugacity_ca_recorded": (ca_recorded,),
        "nonmetal_source_or_missing": (nonmetal,),
        "companion_positive_missing": (ca_recorded, alo2_missing),
        "neutral_and_transport": (ca_recorded, neutral, transport),
        "positive_only_missing": (alo2_missing,),
    }


def _missing_ref_gates() -> dict[str, tuple[float | None, float | None, float]]:
    """(source fO2 bar, capacity mol per ln fO2, temperature K)."""

    return {
        "floor_empty": (1.0e-30, 0.0, _MISSING_REF_T_K),
        "inband_empty": (1.0e-9, 0.0, _MISSING_REF_T_K),
        "floor_buffered": (1.0e-30, 1.0, _MISSING_REF_T_K),
        "inband_buffered": (1.0e-9, 1.0, _MISSING_REF_T_K),
        "nosource_empty": (None, 0.0, _MISSING_REF_T_K),
        "nosource_missing_capacity": (None, None, _MISSING_REF_T_K),
        "floor_empty_bad_temperature": (1.0e-30, 0.0, math.nan),
    }


def _oracle_exponent(spec: Mapping) -> float | None:
    """Reaction exponent from the rule, not from ``_reaction_exponent``."""

    rail = str(spec["rail"] or "")
    if rail in _MISSING_REF_METAL_RAILS and spec["species"] in _MISSING_REF_N_METAL:
        return -1.0 / _MISSING_REF_N_METAL[spec["species"]]
    exponent = spec["exponent"]
    if exponent is None:
        return None
    return float(exponent)


def _oracle_p_ref(spec: Mapping, source: float | None) -> float | None:
    """Metal rails return the recorded pO2 only. Others may use the source."""

    recorded = spec["recorded"]
    rail = str(spec["rail"] or "")
    if rail in _MISSING_REF_METAL_RAILS or spec["transport"]:
        return None if recorded is None else float(recorded)
    if recorded is not None:
        return float(recorded)
    if source is None:
        return None
    source_value = float(source)
    if not math.isfinite(source_value) or source_value <= 0.0:
        return None
    return source_value


def _oracle_gate_open(
    specs: tuple[Mapping, ...],
    source: float | None,
    capacity: float | None,
) -> bool:
    """Floor or empty buffer, and at least one negative-exponent opener.

    A missing reference does not remove the opener. ``n > 0`` does not
    open. A missing capacity is not zero.
    """

    floor = False
    if source is not None:
        try:
            oxygen = float(source)
        except (TypeError, ValueError):
            oxygen = math.nan
        floor = (
            math.isfinite(oxygen)
            and oxygen > 0.0
            and oxygen <= _MISSING_REF_FLOOR_BAR
        )
    negligible = False
    if capacity is not None:
        try:
            capacity_value = float(capacity)
        except (TypeError, ValueError):
            capacity_value = math.nan
        negligible = (
            math.isfinite(capacity_value)
            and capacity_value <= _MISSING_REF_NOOP_MOL
        )
    opener = False
    for spec in specs:
        exponent = _oracle_exponent(spec)
        if (
            float(spec["pressure"]) > 0.0
            and not spec["transport"]
            and exponent is not None
            and exponent < 0.0
        ):
            opener = True
    return (floor or negligible) and opener


def _oracle_missing_ref_for_map(
    spec: Mapping,
    specs: tuple[Mapping, ...],
    *,
    source: float | None,
    capacity: float | None,
    temperature_K: float,
) -> str:
    gate_open = _oracle_gate_open(specs, source, capacity)
    exponent = _oracle_exponent(spec)
    obligation = (
        float(spec["pressure"]) > 0.0
        and not spec["transport"]
        and exponent is not None
        and exponent != 0.0
    )
    if not gate_open or not obligation:
        return "published"
    try:
        temperature = float(temperature_K)
    except (TypeError, ValueError):
        temperature = math.nan
    if not math.isfinite(temperature) or temperature <= 0.0:
        return "bad_temperature"
    if _oracle_p_ref(spec, source) is None:
        return "missing_reference"
    return "moved"


def _bug_keeps_missing_reference(
    spec: Mapping,
    specs: tuple[Mapping, ...],
    *,
    source: float | None,
    capacity: float | None,
    temperature_K: float,
) -> str:
    """The defect: no recorded pO2 means the carrier is not an opener.

    The old early return then keeps the persisted pressure and writes
    no refusal. A carrier that has a reference still follows the rule.
    """

    if _oracle_p_ref(spec, source) is None and _oracle_exponent(spec) not in (
        None,
        0.0,
    ) and not spec["transport"] and float(spec["pressure"]) > 0.0:
        return "published"
    return _oracle_missing_ref_for_map(
        spec,
        specs,
        source=source,
        capacity=capacity,
        temperature_K=temperature_K,
    )


def _missing_ref_provenance(spec: Mapping) -> dict:
    provenance: dict = {}
    if spec["rail"]:
        provenance["pressure_rail"] = spec["rail"]
    if spec["recorded"] is not None:
        provenance["pO2_bar"] = spec["recorded"]
    if spec["exponent"] is not None:
        provenance["pO2_exponent"] = spec["exponent"]
    if spec["transport"]:
        provenance["oxygen_fugacity_channel"] = "transport_headspace"
    return provenance


def _missing_ref_case_ids() -> list[str]:
    return [
        f"{scenario}-{gate}"
        for scenario in _missing_ref_scenarios()
        for gate in _missing_ref_gates()
    ]


@pytest.mark.parametrize(
    "scenario,gate",
    [
        (scenario, gate)
        for scenario in _missing_ref_scenarios()
        for gate in _missing_ref_gates()
    ],
    ids=_missing_ref_case_ids(),
)
def test_open_gate_refuses_missing_reference_on_every_class(
    scenario: str, gate: str
) -> None:
    """An open gate never returns a persisted pressure that has no reference.

    Invariant, one chokepoint (``apply_langmuir_surface_oxygen``): once
    the d-376 gate is open, every positive-pressure n != 0 melt carrier
    is the solved surface pressure or a typed refusal. A metal rail
    with no recorded ``pO2_bar`` does not borrow source fO2, so that
    carrier is the missing-reference refusal. ``n == 0`` and transport
    stay published. No opener, or a buffered in-band source, leaves the
    published map. A non-finite temperature refuses the obligations and
    does not publish them.

    The mutation keeps every missing-reference obligation at its
    published pressure with an empty refusal. Floor and empty-buffer
    rows of that class disagree with it. Closed-gate rows agree.
    """

    from simulator.chemistry.ellingham_thermo import ellingham_stoichiometry
    from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen

    n_metal, _n_oxide = ellingham_stoichiometry("Ca")
    assert n_metal == 2.0
    with pytest.raises(KeyError):
        ellingham_stoichiometry("AlO2")

    specs = _missing_ref_scenarios()[scenario]
    source, capacity, temperature_K = _missing_ref_gates()[gate]
    pressures = {spec["species"]: spec["pressure"] for spec in specs}
    provenance = {
        spec["species"]: _missing_ref_provenance(spec) for spec in specs
    }
    conductance = {spec["species"]: 1.0 for spec in specs}
    conductance["O2"] = 1.0
    result = apply_langmuir_surface_oxygen(
        pressures,
        temperature_K=temperature_K,
        provenance_by_species=provenance,
        source_reaction_fO2_bar=source,
        legacy_catalog=None,
        compiled_catalog=None,
        melt_redox_capacity_mol_per_ln_fO2=capacity,
        conductance_mol_s_m2_Pa=conductance,
        allow_unmeasured_alpha_fallback=True,
    )
    gate_open = _oracle_gate_open(specs, source, capacity)
    assert result.applied is gate_open
    for spec in specs:
        species = spec["species"]
        published = spec["pressure"]
        outcome = _oracle_missing_ref_for_map(
            spec,
            specs,
            source=source,
            capacity=capacity,
            temperature_K=temperature_K,
        )
        bug = _bug_keeps_missing_reference(
            spec,
            specs,
            source=source,
            capacity=capacity,
            temperature_K=temperature_K,
        )
        if outcome == "published":
            assert result.pressures_Pa[species] == published
            assert species not in result.refused_species
        elif outcome == "missing_reference":
            assert species not in result.pressures_Pa
            reason = result.refused_species[species]
            assert "missing pO2 reference" in reason
            assert "0.0" not in reason
        elif outcome == "bad_temperature":
            assert species not in result.pressures_Pa
            reason = result.refused_species[species]
            assert "temperature_K" in reason
        else:
            assert outcome == "moved"
            if species in result.pressures_Pa:
                assert result.pressures_Pa[species] != pytest.approx(
                    published, rel=1.0e-6
                )
                assert species not in result.refused_species
            else:
                assert result.refused_species[species]
        if _oracle_p_ref(spec, source) is None and outcome != "published":
            assert bug == "published"
            assert outcome != bug
        else:
            assert bug == outcome


def test_emitted_flux_residual_uses_catalog_molar_mass() -> None:
    """The emitted Ca flux closes on the catalog mass, not CIAAW 0.040078.

    The host stub of ``_build_evaporation_aux_maps`` does not supply
    this mass. The closure and the provider both use the metals-row
    ``molar_mass_g_mol`` (Ca 40.08 g/mol). Dividing the emitted kg/h
    by the CIAAW mass misses the oxygen residual at 1e-6. Dividing by
    the catalog mass meets it. The solved Ca pressure is the catalog
    root and not the CIAAW root.
    """

    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.evaporation import production_vapor_molar_mass_g_per_mol

    capacity = _d072_capacity_mol_per_ln_fO2({"CaO": 100.0}, 0.0, -9.0)
    assert capacity == 0.0
    compiled, legacy = _catalog()
    answer = _cao_answer(-9.0)
    diagnostic = answer.diagnostic
    published, published_ca, p_ref_bar = _published_ca(diagnostic)
    formula_surface, formula_pa, _formula_flux = _congruent_cao_surface(
        published_ca, p_ref_bar
    )
    surface, catalog_pa, _catalog_flux = _congruent_cao_surface(
        published_ca, p_ref_bar, legacy=legacy
    )
    catalog_g = legacy["metals"]["Ca"]["molar_mass_g_mol"]
    catalog_kg = float(catalog_g) / 1000.0
    assert catalog_kg == pytest.approx(0.04008)
    assert _M_CA == pytest.approx(0.040078)
    assert catalog_kg != pytest.approx(_M_CA, rel=1.0e-7)
    assert production_vapor_molar_mass_g_per_mol(legacy, "Ca") == catalog_g
    host, equilibrium = _host(
        compiled,
        legacy,
        diagnostic,
        published,
        redox_capacity_mol_per_ln_fO2=capacity,
    )
    resolved = _resolve(host, equilibrium)
    # The free-molecular catalog algebra and the filmed solver agree
    # inside the same 1e-6 band as the other Ca pins. 1e-8 is tighter
    # than that band. The CIAAW root stays outside 1e-7.
    assert resolved["Ca"] == pytest.approx(catalog_pa, rel=1.0e-6)
    assert resolved["Ca"] != pytest.approx(formula_pa, rel=1.0e-7)
    notice = host._last_langmuir_surface_oxygen
    assert notice["surface_pO2_bar"] == pytest.approx(surface, rel=1.0e-6)
    assert notice["surface_pO2_bar"] != pytest.approx(formula_surface, rel=1.0e-7)

    captured: dict = {}
    provider = BuiltinEvaporationFluxProvider()

    def _dispatch_only(intent, *args, **kwargs):
        del args
        controls = dict(kwargs["control_inputs"])
        captured["controls"] = controls
        enriched = dict(controls)
        stoich = dict(enriched.get("stoich_by_species") or {})
        stoich["Ca"] = {"oxide_per_product_kg": 1.4, "parent_oxide": "CaO"}
        enriched["stoich_by_species"] = stoich
        # The yaml row, not the closure helper and not the aux-map stub.
        masses = dict(enriched.get("molar_mass_kg_mol") or {})
        masses["Ca"] = catalog_kg
        enriched["molar_mass_kg_mol"] = masses
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
        return SimpleNamespace(
            status="ok",
            diagnostic={"evaporation_flux_kg_hr": {}},
        )

    host._dispatch_only = _dispatch_only
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    emitted_provider = captured["provider"]
    assert str(emitted_provider.status) == "ok", emitted_provider.diagnostic
    kg_hr = float(
        (emitted_provider.diagnostic or {})["evaporation_flux_kg_hr"]["Ca"]
    )
    area = float(host.melt.melt_surface_area_m2)
    overhead_pa = float(captured["controls"]["overhead_pressure_pa"])
    alpha_ca = _catalog_alpha(legacy, "Ca", _T_K)
    o2_kg = float(MOLAR_MASS["O2"]) / 1000.0
    assert o2_kg == pytest.approx(_M_O2)
    emitted_mol = kg_hr / catalog_kg / 3600.0 / area
    formula_mol = kg_hr / _M_CA / 3600.0 / area
    j_ca = _series_molar_flux(
        species="Ca",
        pressure_pa=float(resolved["Ca"]),
        bulk_pa=0.0,
        molar_mass=catalog_kg,
        alpha=alpha_ca,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    j_o2 = _series_molar_flux(
        species="O2",
        pressure_pa=float(notice["surface_pO2_bar"]) * PA_PER_BAR,
        bulk_pa=0.0,
        molar_mass=o2_kg,
        alpha=1.0,
        temperature_K=_T_K,
        overhead_pa=overhead_pa,
    )
    assert emitted_mol == pytest.approx(j_ca, rel=1.0e-6)
    scale = max(abs(emitted_mol), abs(2.0 * j_o2), 1.0e-300)
    assert abs(2.0 * j_o2 - emitted_mol) / scale < 1.0e-6
    assert abs(2.0 * j_o2 - formula_mol) / scale > 1.0e-6


def test_surface_balance_bracket_follows_melt_dissociation_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root's lower edge is the gate's floor, not a copied -30.

    Margin is 0: ``low`` is ``log10`` of the owned constant. Patching
    that constant must move the bracket. The shipped endpoint is on
    the floor ``_pO2_is_melt_dissociation_floor`` classifies.
    """

    import simulator.physical_constants as physical_constants
    from engines.builtin import vapor_pressure as vapor_pressure
    from simulator.langmuir_surface_oxygen import _surface_balance_log10_bracket

    floor = physical_constants.MELT_DISSOCIATION_PO2_MIN_BAR
    low, high = _surface_balance_log10_bracket()
    assert high == 0.0
    assert low == math.log10(floor)
    assert low == math.log10(vapor_pressure.MELT_DISSOCIATION_PO2_MIN_BAR)
    assert _pO2_is_melt_dissociation_floor(10.0 ** low)
    assert not _pO2_is_melt_dissociation_floor(floor * 10.0)

    patched = 1.0e-18
    monkeypatch.setattr(
        physical_constants,
        "MELT_DISSOCIATION_PO2_MIN_BAR",
        patched,
    )
    patched_low, patched_high = _surface_balance_log10_bracket()
    assert patched_high == 0.0
    assert patched_low == math.log10(patched)
    assert patched_low != low
