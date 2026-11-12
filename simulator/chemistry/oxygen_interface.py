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
from dataclasses import dataclass
from typing import Literal

from scipy.optimize import brentq

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.physical_constants import (
    GAS_CONSTANT,
    MELT_DISSOCIATION_PO2_MIN_BAR,
    PA_PER_BAR,
)


_LOWER_LOG10_PO2_BAR = -30.0
_UPPER_LOG10_PO2_BAR = 0.0
_MELT_CAPACITY_NOOP_MOL_PER_LN = 1.0e-15
_BUFFERED_CAPACITY_MOL_PER_LN = 1.0e6
_M_O2_KG_PER_MOL = 2.0 * ATOMIC_WEIGHTS_G_PER_MOL["O"] / 1000.0

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
class InterfaceOxygenChannel:
    """One pressure-law channel and its resolved kinetic inputs.

    ``r_gas_m_s`` is the gas-film resistance returned by the existing series
    flux helper. It stays zero in the Stage B Langmuir fixtures. Atomic oxygen
    is a gas-transfer channel and contributes half its atom flux to ``S_gas``;
    product channels contribute through their parent/product oxygen difference.
    """

    species: str
    molar_mass_kg_per_mol: float
    oxygen_atoms: float
    parent_oxygen_demand: float
    pO2_exponent: float
    alpha: float
    r_gas_m_s: float = 0.0
    bulk_pressure_pa: float = 0.0
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
) -> InterfaceOxygenSolution:
    """Solve the coupled melt, evaporation, and overhead oxygen balance.

    Channel pressure inputs are ``log10(p_s / bar)`` and channel pressures
    are returned in Pa. Product flux is the existing series law in its
    resistance form, ``max(P_eq - P_bulk, 0) / ((R_i + R_g) M_i)``. ``R_g``
    values are supplied by the caller from the established transport helper;
    this function does not recalculate Chapman-Enskog transport.

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
    oxygen_gas_resistance = float(oxygen_r_gas_m_s)
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
    if not math.isfinite(oxygen_gas_resistance) or oxygen_gas_resistance < 0.0:
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
        active_channels.append(channel)

    # Same algebra as langmuir_knudsen.hertz_knudsen_k_kg_s_m2_pa.
    oxygen_r_interface = 1.0 / (
        oxygen_alpha_value
        * math.sqrt(_M_O2_KG_PER_MOL / (2.0 * math.pi * GAS_CONSTANT * temperature))
    )
    oxygen_r_total = oxygen_r_interface + oxygen_gas_resistance
    if not math.isfinite(oxygen_r_total) or oxygen_r_total <= 0.0:
        raise ValueError("oxygen series resistance must be finite and positive")
    reservoir_ln = reservoir_log * math.log(10.0)
    reservoir_in_bracket = (
        _LOWER_LOG10_PO2_BAR <= reservoir_log <= _UPPER_LOG10_PO2_BAR
    )

    def evaluate(logp: float, overhead_pa: float) -> dict[str, object]:
        try:
            pressures_raw = pressure_model(logp)
        except Exception as exc:
            raise ValueError(
                f"pressure model failed at log10(pO2/bar)={logp:g}"
            ) from exc
        if not isinstance(pressures_raw, Mapping):
            raise ValueError("pressure_model must return a species-to-Pa mapping")
        pressures: dict[str, float] = {}
        product_fluxes: dict[str, float] = {}
        atomic_oxygen_flux = 0.0
        evaporative_source = 0.0
        for channel in active_channels:
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
            pressures[channel.species] = pressure
            r_interface = 1.0 / (
                channel.alpha
                * math.sqrt(
                    channel.molar_mass_kg_per_mol
                    / (2.0 * math.pi * GAS_CONSTANT * temperature)
                )
            )
            resistance = r_interface + channel.r_gas_m_s
            if not math.isfinite(resistance) or resistance <= 0.0:
                raise ValueError(f"invalid series resistance for {channel.species!r}")
            flux = max(0.0, pressure - channel.bulk_pressure_pa) / (
                resistance * channel.molar_mass_kg_per_mol
            )
            if channel.kind == "atomic_oxygen":
                atomic_oxygen_flux += flux
            else:
                product_fluxes[channel.species] = flux
                evaporative_source += (
                    channel.parent_oxygen_demand - channel.oxygen_atoms
                ) * flux

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
        evaporative_source *= area / 2.0
        gas_source = area * oxygen_flux + area * atomic_oxygen_flux / 2.0
        residual = melt_source + evaporative_source - gas_source
        return {
            "pressures": pressures,
            "product_fluxes": product_fluxes,
            "atomic_oxygen_flux": atomic_oxygen_flux,
            "melt_source": melt_source,
            "evaporative_source": evaporative_source,
            "gas_source": gas_source,
            "residual": residual,
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
    )
