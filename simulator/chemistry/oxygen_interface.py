"""Pure interface-oxygen balance for a single frozen evaporation step.

The root coordinate is ``log10(p_s / bar)``. ``pressure_model`` returns
equilibrium channel pressures in Pa, while each channel's alpha and series
resistance determine its molar flux. Positive source terms point from melt to
overhead:

``S_melt = (C_m / tau) * (ln(fO2_m) - ln(p_s / bar))``

``S_evap = A/2 * sum((d_O - n_O) * J_i)``

``S_gas = A * (p_s - p_h) / ((R_interface,O2 + R_gas,O2) * M_O2)``

The solved balance is ``S_melt + S_evap - S_gas = 0``. The separate
monotone residual is its negative so positive values at both bracket ends
identify a root below the lower pressure edge.

Writing ``ξ = ln(pO2 / bar)``, the governing root is
``F(ξ_s) = (C_m / τ)(ξ_m - ξ_s) + S_evap(p_s) - S_gas(p_s, p_h) = 0``,
where the signed gas source is
``S_gas = A (p_s - p_h) / ((R_int + R_gas) M_O2) + A J_O / 2``.
The pressure difference is in Pa, so division by ``(m/s)(kg/mol)`` gives
``mol/(m² s)`` and multiplication by area gives ``mol/s``. For zero melt
capacity in the product-only case, ``p_s <= p_h`` gives ``S_evap > 0`` and
``S_gas <= 0``, hence ``F > 0``. Since ``F`` decreases with ``p_s``, the
closed root lies above ``p_h`` and the outward film carries exactly
``S_evap``. The overhead bound is therefore a property of the signed-flux
root; clamping at ``p_h`` leaves the positive evaporation residual open.
With finite capacity and a reservoir below ``p_h``, the root can lie below
the overhead: signed ``J_O2 < 0`` means overhead oxygen enters a melt that is
a net oxygen sink. For ``C_m > 1e6``, the pin sets ``ξ_s = ξ_m`` at every
overhead pressure. A finite melt source as ``C_m -> infinity`` requires
``ξ_s -> ξ_m``, so the limiting source is
``S_gas(ξ_m, p_h) - S_evap(ξ_m)`` with the signed film term, and the pinned
residual is zero.

Product and atomic-oxygen molar flux is the ledger's series helper,
``J_kg / M``. The hour freezes overhead total pressure, temperature,
pipe diameter, carrier, and stir, so Kn does not follow the trial
surface pressure. ``R_gas = 0`` (gas resistance off, or free-molecular
Kn) is the effusion limit.

Analytic fixture: for one ``FeO -> Fe + 1/2 O2`` channel with
``p_Fe = K / sqrt(p / bar)``, unit alphas, vacuum overhead, and no gas
resistance, Hertz-Knudsen molar flux is proportional to ``p / sqrt(M)``.
Closure ``J_O2 = J_Fe / 2`` gives the numerical pressure in bar as
``p = [K * sqrt(M_O2 / M_Fe) / 2] ** (2/3)``, where ``K`` is also expressed
as its numerical pressure in bar. Dimensionally, for a chosen pressure unit
``U``, ``p/U = [(K/U) * sqrt(M_O2 / M_Fe) / 2] ** (2/3)``: every term inside
the power is dimensionless, and multiplying by ``U`` returns pressure. The
limiting case ``K -> 0`` gives ``p -> 0``. With no evaporation or gas transfer,
any positive melt capacity gives ``p_s = fO2_m``; as ``C_m`` grows, finite
source terms produce a vanishing interface offset.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from scipy.optimize import brentq

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.physical_constants import (
    MELT_DISSOCIATION_PO2_MIN_BAR,
    PA_PER_BAR,
)


# Declared common-unity sensitivity for O/O2 free-molecular escape.
# Absolute oxygen-gas alpha is not a measured coefficient. The hour and
# the residue oxygen balance both read this value.
OXYGEN_GAS_ALPHA = 1.0
OXYGEN_GAS_ALPHA_SOURCE = (
    "declared common-unity sensitivity alpha=1.0 for O/O2 free-molecular gas escape; "
    "absolute oxygen-gas alpha is not measured"
)

_LOWER_LOG10_PO2_BAR = -30.0
_UPPER_LOG10_PO2_BAR = 0.0
_MELT_CAPACITY_NOOP_MOL_PER_LN = 1.0e-15
_BUFFERED_CAPACITY_MOL_PER_LN = 1.0e6
_M_O2_KG_PER_MOL = 2.0 * ATOMIC_WEIGHTS_G_PER_MOL["O"] / 1000.0
# Positive drive so the series helper fills r_gas. The helper returns
# before the film when P_eq <= P_bulk, and the signed O2 term needs those
# resistances when p_s < p_h. 1 Pa is not a physical oxygen pressure.
# R_gas depends on the frozen overhead total pressure, not on this drive.
# Limit: gas resistance off gives r_gas = 0 for any positive drive.
_OXYGEN_RESISTANCE_PROBE_PA = 1.0

InterfaceOxygenLabel = Literal["bracket_boundary=lower"]
InterfaceOxygenFailureReason = Literal[
    "non_monotone",
    "not_strictly_monotone",
    "oxygen_balance_no_root",
    "brent_failure",
]
InterfaceOxygenChannelKind = Literal["product", "atomic_oxygen"]


class InterfaceOxygenSolveError(ValueError):
    """Typed failure from the interface pressure solve."""

    def __init__(self, reason: InterfaceOxygenFailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class InterfaceOxygenTransport:
    """Hour inputs frozen for Kn and R_gas.

    The series helper's gas weight is a step at Kn = 0.01. Kn is the
    mean free path divided by the pipe diameter, and the mean free path
    depends on the overhead total pressure. Recomputing Kn from the trial
    surface pressure steps that weight inside the residual and can make
    the residual rise. These fields are the hour's overhead total
    pressure, gas temperature, pipe diameter, carrier, and stir. They
    are not the trial p_s.

    ``gas_resistance_enabled`` False is free-molecular effusion, R_gas = 0,
    which is the Stage C residual. ``melt_resistance_enabled`` is the
    debit's flag; the helper owns ``R_melt = 0`` and the refusal. The
    pipe cap, joint pump scale, multi-parent depletion, and the 1e-12 kg/h
    threshold are downstream of this bundle and are not fields here. The
    post-solve finite-capacity partial override is also downstream.
    """

    carrier_gas: str = "N2"
    pipe_diameter_m: float = 0.12
    axial_stir_factor: float = 0.0
    radial_stir_factor: float = 1.0
    overhead_pressure_pa: float = 0.0
    gas_temperature_K: float | None = None
    gas_resistance_enabled: bool = False
    extrapolate_continuum: bool = False
    melt_resistance_enabled: bool = False
    cold_skull_envelope: Mapping[str, float] | None = None


@dataclass(frozen=True)
class InterfaceOxygenChannel:
    """One pressure-law channel and its resolved kinetic inputs.

    ``r_gas_m_s`` is the gas-film resistance from one series-helper probe.
    The residual does not read it. Product flux is a fresh helper call at
    the trial pressure, so this field cannot be a second flux law. It is
    zero when the helper's gas weight is zero. Atomic oxygen is a
    gas-transfer channel and contributes half its atom flux to ``S_gas``;
    product channels contribute through their parent/product oxygen difference.

    ``uses_gas_resistance`` is false for the ledger's HKL upper-bound
    species. Those channels are effusion, with bulk pressure 0, matching
    the debit. The hour flag on ``InterfaceOxygenTransport`` still has to
    be on; both must be true before the helper builds a film.
    """

    species: str
    molar_mass_kg_per_mol: float
    oxygen_atoms: float
    parent_oxygen_demand: float
    pO2_exponent: float
    alpha: float
    r_gas_m_s: float = 0.0
    bulk_pressure_pa: float = 0.0
    uses_gas_resistance: bool = True
    kind: InterfaceOxygenChannelKind = "product"


@dataclass(frozen=True)
class InterfaceOxygenSolution:
    """Surface pressure and flux accounting at the accepted trial pressure."""

    surface_pO2_bar: float
    surface_log10_pO2_bar: float
    label: InterfaceOxygenLabel | None
    capacity_label: Literal["none:no_melt_redox_capacity"] | None
    channel_pressures_pa: Mapping[str, float]
    product_fluxes_mol_m2_s: Mapping[str, float]
    atomic_oxygen_flux_mol_m2_s: float
    melt_source_mol_s: float
    evaporative_source_mol_s: float
    gas_source_mol_s: float
    residual_mol_s: float
    oxygen_r_interface_m_s: float = 0.0
    oxygen_r_gas_m_s: float = 0.0
    evaporative_oxygen_mol_s: Mapping[str, float] = field(default_factory=dict)
    omitted_reasons: Mapping[str, str] = field(default_factory=dict)


class MissingInterfaceOxygenPotential(AccountingError):
    """The evaporation publish has no usable solved surface potential."""


def require_evaporation_oxygen_potential_log10(solution: object | None) -> float:
    """Return the hour's solved ``log10(p_s / bar)`` for the vapour publish.

    This is ξ_s, the same potential the interface model solved. The melt
    reservoir log is not a substitute. A shuttle-collapsed reservoir near
    −48 is rewritten by ``physical_melt_dissociation_pO2_bar`` onto
    ``MELT_DISSOCIATION_PO2_MIN_BAR`` (1e-30 bar), and that clamp must not
    drive an evaporation debit.

    A missing or non-finite surface is a typed refusal. A log strictly
    below the dissociation floor would be clamped to that same 1e-30 bar,
    so it is a refusal too. The solved bracket edge, exactly the floor, is
    the predicted value (``fO2 < log10(min)`` is the clamp, not equality)
    and is returned.

    Unit check: the return is log10(bar), the vapour law's fO2 argument.
    Limit: no solution object raises; a surface of 1 bar returns 0.
    """

    surface_log = (
        getattr(solution, "surface_log10_pO2_bar", None)
        if solution is not None
        else None
    )
    if surface_log is None:
        raise MissingInterfaceOxygenPotential(
            "evaporation vapour pressures require the hour solved interface "
            "oxygen potential xi_s; missing surface_log10_pO2_bar is a "
            "typed refusal, not a dissociation-floor clamp"
        )
    try:
        log10_p = float(surface_log)
    except (TypeError, ValueError) as exc:
        raise MissingInterfaceOxygenPotential(
            "evaporation vapour pressures require a numeric solved interface "
            f"oxygen potential; got {surface_log!r}"
        ) from exc
    if not math.isfinite(log10_p):
        raise MissingInterfaceOxygenPotential(
            "evaporation vapour pressures require a finite solved interface "
            f"oxygen potential; got {log10_p!r}"
        )
    floor_log = math.log10(MELT_DISSOCIATION_PO2_MIN_BAR)
    if log10_p < floor_log:
        raise MissingInterfaceOxygenPotential(
            "solved interface oxygen potential is below the melt dissociation "
            f"floor ({MELT_DISSOCIATION_PO2_MIN_BAR:g} bar); refusing rather "
            "than publishing the clamped pressure"
        )
    return log10_p


def _series_helper():
    """The debit's flux function. Imported lazily; not copied."""

    from engines.builtin.evaporation_flux import (
        _series_resistance_evaporation_flux_kg_m2_s,
    )

    return _series_resistance_evaporation_flux_kg_m2_s


def _helper_series(
    species: str,
    p_eq_pa: float,
    p_bulk_pa: float,
    molar_mass_kg_per_mol: float,
    alpha: float,
    transport: InterfaceOxygenTransport,
    temperature_K: float,
    *,
    gas_resistance_enabled: bool,
):
    """One series-helper call at the hour's frozen transport.

    Kn is computed inside the helper from ``transport.overhead_pressure_pa``.
    That pressure is the hour's overhead total, not the trial surface
    partial. Passing the trial pressure would step ``gas_weight`` inside
    the bracket. ``R_melt`` stays the helper's value.
    """

    return _series_helper()(
        species=species,
        P_eq_pa=p_eq_pa,
        P_bulk_pa=p_bulk_pa,
        T_surface_K=temperature_K,
        molar_mass_kg_mol=molar_mass_kg_per_mol,
        alpha_i=alpha,
        pipe_diameter_m=transport.pipe_diameter_m,
        overhead_pressure_pa=transport.overhead_pressure_pa,
        axial_stir_factor=transport.axial_stir_factor,
        radial_stir_factor=transport.radial_stir_factor,
        cold_skull_envelope=transport.cold_skull_envelope,
        carrier_gas=transport.carrier_gas,
        T_gas_K=transport.gas_temperature_K,
        melt_resistance_enabled=transport.melt_resistance_enabled,
        gas_resistance_enabled=gas_resistance_enabled,
        extrapolate_continuum=transport.extrapolate_continuum,
    )


def oxygen_series_resistances(
    transport: InterfaceOxygenTransport,
    *,
    temperature_K: float,
    oxygen_alpha: float,
) -> tuple[float, float]:
    """``(r_interface, r_gas)`` for signed O2, from one positive drive.

    The helper returns before the film when ``P_eq <= P_bulk``, so a
    backflow cannot be read off a helper flux. A 1 Pa drive with zero
    bulk fills both resistances. Those resistances multiply the signed
    ``(p_s - p_h)`` term. The 1 Pa drive is not a residual pressure.

    Unit check: the helper's ``r_interface`` and ``r_gas`` are
    m²·Pa·s/kg. Pa = kg/(m·s²), so that unit is m/s. Limit: gas
    resistance off gives ``r_gas = 0``.
    """

    probed = _helper_series(
        "O2",
        _OXYGEN_RESISTANCE_PROBE_PA,
        0.0,
        _M_O2_KG_PER_MOL,
        oxygen_alpha,
        transport,
        temperature_K,
        gas_resistance_enabled=transport.gas_resistance_enabled,
    )
    r_interface = float(probed.r_interface)
    r_gas = float(probed.r_gas)
    if not math.isfinite(r_interface) or r_interface <= 0.0:
        raise ValueError("oxygen interface resistance must be finite and positive")
    if not math.isfinite(r_gas) or r_gas < 0.0:
        raise ValueError("oxygen gas resistance must be finite and non-negative")
    return r_interface, r_gas


def film_channel(
    channel: InterfaceOxygenChannel,
    transport: InterfaceOxygenTransport,
    *,
    temperature_K: float,
) -> tuple[InterfaceOxygenChannel | None, str | None]:
    """Probe one channel. A helper refusal drops it. The field is not ``J``.

    The drive is 1 Pa above the channel bulk, so the omission does not
    depend on the trial surface pressure. ``r_gas_m_s`` is the probed
    value. Product flux is a later helper call at the trial ``P_eq``.
    """

    from engines.builtin.evaporation_flux import EvaporationFluxConfigurationError

    enabled = bool(
        transport.gas_resistance_enabled and channel.uses_gas_resistance
    )
    try:
        probed = _helper_series(
            channel.species,
            channel.bulk_pressure_pa + _OXYGEN_RESISTANCE_PROBE_PA,
            channel.bulk_pressure_pa,
            channel.molar_mass_kg_per_mol,
            channel.alpha,
            transport,
            temperature_K,
            gas_resistance_enabled=enabled,
        )
    except EvaporationFluxConfigurationError as exc:
        return None, str(exc)
    r_gas = float(probed.r_gas)
    if not math.isfinite(r_gas) or r_gas < 0.0:
        return None, f"non-finite gas resistance for {channel.species}"
    return replace(channel, r_gas_m_s=r_gas), None


def channel_state_at_log10(
    pressure_model: Callable[[float], Mapping[str, float]],
    channels: Sequence[InterfaceOxygenChannel],
    *,
    log10_pO2_bar: float,
    temperature_K: float,
    area_m2: float,
    transport: InterfaceOxygenTransport | None = None,
) -> dict[str, object]:
    """Equilibrium pressures and series fluxes at one oxygen potential.

    Molar flux is the helper's kg/(m²·s) divided by ``M`` (kg/mol), so
    the result is mol/(m²·s). The evaporative source is
    ``A/2 * sum((d_O - n_O) * J)``. The solver and the above-bracket
    overhead prediction both use this function.

    ``channel.r_gas_m_s`` is not read. A second resistance here would
    be a second flux law. A helper refusal omits that species. An
    effusion stand-in would price it differently from the ledger.
    """

    frozen = transport if transport is not None else InterfaceOxygenTransport()
    try:
        pressures_raw = pressure_model(log10_pO2_bar)
    except Exception as exc:
        raise ValueError(
            f"pressure model failed at log10(pO2/bar)={log10_pO2_bar:g}"
        ) from exc
    if not isinstance(pressures_raw, Mapping):
        raise ValueError("pressure_model must return a species-to-Pa mapping")
    from engines.builtin.evaporation_flux import EvaporationFluxConfigurationError

    pressures: dict[str, float] = {}
    product_fluxes: dict[str, float] = {}
    evaporative_oxygen: dict[str, float] = {}
    omitted: dict[str, str] = {}
    atomic_oxygen_flux = 0.0
    evaporative_source = 0.0
    for channel in channels:
        try:
            pressure = float(pressures_raw[channel.species])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"pressure model is missing a valid {channel.species!r} pressure"
            ) from exc
        if not math.isfinite(pressure) or pressure < 0.0:
            raise ValueError(
                f"pressure model returned invalid {channel.species!r} pressure"
            )
        enabled = bool(
            frozen.gas_resistance_enabled and channel.uses_gas_resistance
        )
        try:
            probed = _helper_series(
                channel.species,
                pressure,
                channel.bulk_pressure_pa,
                channel.molar_mass_kg_per_mol,
                channel.alpha,
                frozen,
                temperature_K,
                gas_resistance_enabled=enabled,
            )
        except EvaporationFluxConfigurationError as exc:
            omitted[channel.species] = str(exc)
            continue
        # J_mol = J_kg / M. J_kg is kg/(m² s). M is kg/mol.
        # J_mol is mol/(m² s). Limit: helper flux 0 gives molar flux 0.
        flux = float(probed.flux_kg_s_m2) / channel.molar_mass_kg_per_mol
        if not math.isfinite(flux) or flux < 0.0:
            raise ValueError(f"invalid series flux for {channel.species!r}")
        pressures[channel.species] = pressure
        if channel.kind == "atomic_oxygen":
            atomic_oxygen_flux += flux
        else:
            product_fluxes[channel.species] = flux
            # (d_O - n_O)/2 is mol O2 per mol vapour from element counts.
            # Times J_mol * A is mol O2 / s. The sum is S_evap.
            oxygen_mol_s = (
                area_m2
                * (channel.parent_oxygen_demand - channel.oxygen_atoms)
                / 2.0
                * flux
            )
            evaporative_oxygen[channel.species] = oxygen_mol_s
            evaporative_source += oxygen_mol_s
    return {
        "pressures": pressures,
        "product_fluxes": product_fluxes,
        "atomic_oxygen_flux": atomic_oxygen_flux,
        "evaporative_source": evaporative_source,
        "evaporative_oxygen": evaporative_oxygen,
        "omitted": omitted,
    }


def overhead_floor_solution(
    pressure_model: Callable[[float], Mapping[str, float]],
    channels: Sequence[InterfaceOxygenChannel],
    *,
    overhead_pO2_bar: float,
    area_m2: float,
    temperature_K: float,
    transport: InterfaceOxygenTransport | None = None,
    oxygen_alpha: float = OXYGEN_GAS_ALPHA,
    oxygen_r_interface_m_s: float | None = None,
    oxygen_r_gas_m_s: float = 0.0,
) -> InterfaceOxygenSolution:
    """Predict at a physical overhead partial above the 1 bar solver domain.

    ``p_s = p_h``, so the film source is zero and the reservoir is not
    stepped. Channel pressures and product fluxes are still evaluated at
    that partial, through the same series helper as the bracket solve.
    An empty channel map would suppress every evaporative source. The
    partial itself is the caller's measured value. This function does
    not replace it with ``nRT/V`` of an unbled inventory and does not
    clamp it onto 1 bar.
    """

    overhead_bar = float(overhead_pO2_bar)
    if not math.isfinite(overhead_bar) or overhead_bar <= 0.0:
        raise ValueError(
            "overhead floor partial must be finite and positive; "
            f"got {overhead_pO2_bar!r}"
        )
    frozen = transport if transport is not None else InterfaceOxygenTransport()
    active: list[InterfaceOxygenChannel] = []
    omitted: dict[str, str] = {}
    for channel in channels:
        if channel.alpha <= 0.0:
            continue
        kept, reason = film_channel(
            channel, frozen, temperature_K=temperature_K,
        )
        if kept is None:
            omitted[channel.species] = reason or "series helper refusal"
            continue
        active.append(kept)
    if oxygen_r_interface_m_s is None:
        r_interface, r_gas = oxygen_series_resistances(
            frozen,
            temperature_K=temperature_K,
            oxygen_alpha=oxygen_alpha,
        )
    else:
        r_interface = float(oxygen_r_interface_m_s)
        r_gas = float(oxygen_r_gas_m_s)
    state = channel_state_at_log10(
        pressure_model,
        tuple(active),
        log10_pO2_bar=math.log10(overhead_bar),
        temperature_K=temperature_K,
        area_m2=area_m2,
        transport=frozen,
    )
    omitted.update(cast(Mapping[str, str], state["omitted"]))
    evaporative_source = float(state["evaporative_source"])
    return InterfaceOxygenSolution(
        surface_pO2_bar=overhead_bar,
        surface_log10_pO2_bar=math.log10(overhead_bar),
        label="overhead_oxygen_floor",
        capacity_label=None,
        channel_pressures_pa=cast(Mapping[str, float], state["pressures"]),
        product_fluxes_mol_m2_s=cast(Mapping[str, float], state["product_fluxes"]),
        atomic_oxygen_flux_mol_m2_s=float(state["atomic_oxygen_flux"]),
        melt_source_mol_s=0.0,
        evaporative_source_mol_s=evaporative_source,
        gas_source_mol_s=0.0,
        residual_mol_s=evaporative_source,
        oxygen_r_interface_m_s=r_interface,
        oxygen_r_gas_m_s=r_gas,
        evaporative_oxygen_mol_s=cast(
            Mapping[str, float], state["evaporative_oxygen"],
        ),
        omitted_reasons=omitted,
    )


def solve_interface_oxygen(
    pressure_model: Callable[[float], Mapping[str, float]],
    channels: Sequence[InterfaceOxygenChannel],
    *,
    reservoir_log10_fO2_bar: float,
    melt_capacity_mol_per_ln_fO2: float,
    exchange_time_s: float,
    overhead_pO2_bar: float,
    area_m2: float,
    temperature_K: float,
    oxygen_alpha: float,
    oxygen_r_gas_m_s: float = 0.0,
    oxygen_r_interface_m_s: float | None = None,
    transport: InterfaceOxygenTransport | None = None,
) -> InterfaceOxygenSolution:
    """Solve the coupled melt, evaporation, and overhead oxygen balance.

    Channel pressure inputs are ``log10(p_s / bar)`` and channel pressures
    are returned in Pa. Product flux is the ledger series helper. The
    channel ``r_gas_m_s`` field is not a flux input. Signed O2 uses one
    positive-drive probe of that helper, unless the caller passes both
    resistances. A lone ``oxygen_r_gas_m_s`` is not a second law: when
    ``oxygen_r_interface_m_s`` is omitted, both resistances are derived
    and the passed gas resistance is ignored.

    A capacity above ``1e6 mol/nat-log`` pins the surface at the frozen melt
    reservoir to avoid losing precision in a residual scaled by a very large
    conductance. A reservoir with no capacity (at or below ``1e-15``) uses
    the gas/product balance and reports the ferric-capacity scope label.
    """

    reservoir_log = float(reservoir_log10_fO2_bar)
    capacity = float(melt_capacity_mol_per_ln_fO2)
    exchange_time = float(exchange_time_s)
    overhead_bar = float(overhead_pO2_bar)
    area = float(area_m2)
    temperature = float(temperature_K)
    oxygen_alpha_value = float(oxygen_alpha)
    frozen = transport if transport is not None else InterfaceOxygenTransport()
    if not math.isfinite(reservoir_log):
        raise ValueError("reservoir_log10_fO2_bar must be finite")
    if not math.isfinite(capacity) or capacity < 0.0:
        raise ValueError("melt capacity must be finite and non-negative")
    if not math.isfinite(exchange_time) or exchange_time <= 0.0:
        raise ValueError("exchange_time_s must be finite and positive")
    if not math.isfinite(overhead_bar) or not 0.0 <= overhead_bar <= 1.0:
        raise ValueError("overhead_pO2_bar must be finite and in [0, 1]")
    if not math.isfinite(area) or area < 0.0:
        raise ValueError("area_m2 must be finite and non-negative")
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    if not math.isfinite(oxygen_alpha_value) or oxygen_alpha_value <= 0.0:
        raise ValueError("oxygen_alpha must be finite and positive")
    if oxygen_r_interface_m_s is None:
        oxygen_r_interface, oxygen_gas_resistance = oxygen_series_resistances(
            frozen,
            temperature_K=temperature,
            oxygen_alpha=oxygen_alpha_value,
        )
    else:
        oxygen_r_interface = float(oxygen_r_interface_m_s)
        oxygen_gas_resistance = float(oxygen_r_gas_m_s)
        if not math.isfinite(oxygen_r_interface) or oxygen_r_interface <= 0.0:
            raise ValueError(
                "oxygen_r_interface_m_s must be finite and positive"
            )
        if (
            not math.isfinite(oxygen_gas_resistance)
            or oxygen_gas_resistance < 0.0
        ):
            raise ValueError("oxygen_r_gas_m_s must be finite and non-negative")

    effective_capacity = (
        0.0 if capacity <= _MELT_CAPACITY_NOOP_MOL_PER_LN else capacity
    )
    capacity_label = (
        "none:no_melt_redox_capacity"
        if effective_capacity == 0.0
        else None
    )
    active_channels: list[InterfaceOxygenChannel] = []
    omitted_reasons: dict[str, str] = {}
    seen_species: set[str] = set()
    for channel in channels:
        if not channel.species or channel.species in seen_species:
            raise ValueError(
                f"channel species must be nonempty and unique: {channel.species!r}"
            )
        seen_species.add(channel.species)
        values = (
            channel.molar_mass_kg_per_mol,
            channel.oxygen_atoms,
            channel.parent_oxygen_demand,
            channel.pO2_exponent,
            channel.alpha,
            channel.r_gas_m_s,
            channel.bulk_pressure_pa,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"non-finite channel input for {channel.species!r}")
        if channel.molar_mass_kg_per_mol <= 0.0:
            raise ValueError(f"molar mass must be positive for {channel.species!r}")
        if channel.oxygen_atoms < 0.0 or channel.parent_oxygen_demand < 0.0:
            raise ValueError(f"oxygen counts must be non-negative for {channel.species!r}")
        if channel.alpha < 0.0:
            raise ValueError(f"alpha must be non-negative for {channel.species!r}")
        if channel.r_gas_m_s < 0.0 or channel.bulk_pressure_pa < 0.0:
            raise ValueError(
                "channel resistances and bulk pressure must be non-negative for "
                f"{channel.species!r}"
            )
        if channel.kind not in {"product", "atomic_oxygen"}:
            raise ValueError(f"unsupported channel kind for {channel.species!r}")
        if channel.kind == "atomic_oxygen" and (
            channel.oxygen_atoms != 1.0 or channel.parent_oxygen_demand != 0.0
        ):
            raise ValueError("atomic_oxygen channels require one O atom and no parent demand")
        if channel.alpha == 0.0:
            continue
        if channel.kind == "product":
            monotone_weight = (
                channel.oxygen_atoms - channel.parent_oxygen_demand
            ) * channel.pO2_exponent
            if monotone_weight < 0.0:
                raise InterfaceOxygenSolveError(
                    "non_monotone",
                    f"oxygen-balance flux is non-monotone for {channel.species!r}",
                )
        elif channel.pO2_exponent < 0.0:
            raise InterfaceOxygenSolveError(
                "non_monotone",
                f"atomic oxygen pressure decreases with pO2 for {channel.species!r}",
            )
        kept, reason = film_channel(
            channel, frozen, temperature_K=temperature,
        )
        if kept is None:
            omitted_reasons[channel.species] = reason or "series helper refusal"
            continue
        active_channels.append(kept)

    # Signed O2 cannot call the helper: the helper clips delta_p at 0.
    # The probe above supplied r_interface and r_gas. Division by those
    # resistances is mol/(m² s) when pressure is in Pa.
    # Unit: Pa / ((m/s)(kg/mol)) = mol/(m² s). Limit: r_gas = 0 is effusion.
    oxygen_r_total = oxygen_r_interface + oxygen_gas_resistance
    if not math.isfinite(oxygen_r_total) or oxygen_r_total <= 0.0:
        raise ValueError("oxygen series resistance must be finite and positive")
    reservoir_ln = reservoir_log * math.log(10.0)
    reservoir_in_bracket = (
        _LOWER_LOG10_PO2_BAR <= reservoir_log <= _UPPER_LOG10_PO2_BAR
    )

    def evaluate(logp: float, overhead_pa: float) -> dict[str, object]:
        state = channel_state_at_log10(
            pressure_model,
            active_channels,
            log10_pO2_bar=logp,
            temperature_K=temperature,
            area_m2=area,
            transport=frozen,
        )
        surface_bar = 10.0**logp
        surface_pa = surface_bar * PA_PER_BAR
        oxygen_flux = (surface_pa - overhead_pa) / (
            oxygen_r_total * _M_O2_KG_PER_MOL
        )
        melt_source = (
            effective_capacity
            / exchange_time
            * (reservoir_ln - logp * math.log(10.0))
        )
        evaporative_source = float(state["evaporative_source"])
        atomic_oxygen_flux = float(state["atomic_oxygen_flux"])
        gas_source = area * oxygen_flux + area * atomic_oxygen_flux / 2.0
        residual = melt_source + evaporative_source - gas_source
        return {
            "pressures": state["pressures"],
            "product_fluxes": state["product_fluxes"],
            "atomic_oxygen_flux": atomic_oxygen_flux,
            "melt_source": melt_source,
            "evaporative_source": evaporative_source,
            "gas_source": gas_source,
            "residual": residual,
            "evaporative_oxygen": state["evaporative_oxygen"],
            "omitted": state["omitted"],
        }

    def solve_at_overhead(overhead_pa: float) -> tuple[float, InterfaceOxygenLabel | None]:
        if effective_capacity > _BUFFERED_CAPACITY_MOL_PER_LN:
            if not reservoir_in_bracket:
                raise InterfaceOxygenSolveError(
                    "oxygen_balance_no_root",
                    "buffered reservoir fO2 is outside the declared interface bracket",
                )
            return reservoir_log, None

        def increasing_residual(logp: float) -> float:
            return -float(evaluate(logp, overhead_pa)["residual"])

        f_low = increasing_residual(_LOWER_LOG10_PO2_BAR)
        f_high = increasing_residual(_UPPER_LOG10_PO2_BAR)
        if f_low == 0.0:
            return _LOWER_LOG10_PO2_BAR, "bracket_boundary=lower"
        if f_high == 0.0:
            return _UPPER_LOG10_PO2_BAR, None
        if f_low > 0.0 and f_high > 0.0:
            return _LOWER_LOG10_PO2_BAR, "bracket_boundary=lower"
        if f_low < 0.0 and f_high < 0.0:
            raise InterfaceOxygenSolveError(
                "oxygen_balance_no_root",
                "interface oxygen balance has no root in the declared pO2 bracket",
            )
        try:
            root = brentq(
                increasing_residual,
                _LOWER_LOG10_PO2_BAR,
                _UPPER_LOG10_PO2_BAR,
                xtol=1.0e-12,
                rtol=1.0e-12,
                maxiter=200,
            )
        except (RuntimeError, ValueError) as exc:
            raise InterfaceOxygenSolveError(
                "brent_failure",
                "Brent failed inside a sign-changing interface oxygen bracket",
            ) from exc
        return root, None

    if area == 0.0 and effective_capacity == 0.0:
        raise InterfaceOxygenSolveError(
            "not_strictly_monotone",
            "interface oxygen balance has no active oxygen-transfer channel",
        )

    overhead_pa = overhead_bar * PA_PER_BAR
    logp, label = solve_at_overhead(overhead_pa)

    accepted = evaluate(logp, overhead_pa)
    if effective_capacity > _BUFFERED_CAPACITY_MOL_PER_LN:
        # As C_m -> ∞ and ξ_s -> ξ_m, (C_m/τ)(ξ_m - ξ_s) -> S_gas - S_evap.
        accepted["melt_source"] = accepted["gas_source"] - accepted["evaporative_source"]
        accepted["residual"] = 0.0
    return InterfaceOxygenSolution(
        surface_pO2_bar=10.0**logp,
        surface_log10_pO2_bar=logp,
        label=label,
        capacity_label=capacity_label,
        channel_pressures_pa=accepted["pressures"],
        product_fluxes_mol_m2_s=accepted["product_fluxes"],
        atomic_oxygen_flux_mol_m2_s=accepted["atomic_oxygen_flux"],
        melt_source_mol_s=accepted["melt_source"],
        evaporative_source_mol_s=accepted["evaporative_source"],
        gas_source_mol_s=accepted["gas_source"],
        residual_mol_s=accepted["residual"],
        oxygen_r_interface_m_s=oxygen_r_interface,
        oxygen_r_gas_m_s=oxygen_gas_resistance,
        evaporative_oxygen_mol_s=cast(
            Mapping[str, float], accepted["evaporative_oxygen"],
        ),
        omitted_reasons={
            **omitted_reasons,
            **cast(Mapping[str, str], accepted["omitted"]),
        },
    )


INTERFACE_SPECIES_OMITTED_KIND = "interface_oxygen_helper_refusal"


def interface_species_omitted_notice(
    omissions: Mapping[str, str],
) -> dict[str, object] | None:
    """Species the residual dropped because the series helper refused them.

    The ledger records the same fact as ``missing_transport_parameters``
    and continues. Both sides leave the species unpriced. An effusion
    stand-in would be a different price. Empty omissions are not a gap.
    A blank species or a blank reason is a typed refusal.
    """

    if not omissions:
        return None
    resolved: dict[str, str] = {}
    for species, reason in omissions.items():
        name = str(species).strip()
        text = str(reason).strip()
        if not name or not text:
            raise ValueError(
                "interface helper refusal needs a species and a reason; "
                f"got species={species!r} reason={reason!r}"
            )
        resolved[name] = text
    return {
        "kind": INTERFACE_SPECIES_OMITTED_KIND,
        "species": resolved,
    }
