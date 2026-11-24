"""Canonical printed-oxygen identity and gas-mixture evidence helpers.

Migration, prediction-identity construction, and comparison all use this
conversion, so one printed log becomes one ``identity.fO2_Pa``. Comparison
does not convert again: it checks exact equality of states this function
built. A sibling that writes a resolved log into a comparison identity
imports this module. It is a leaf: it does not import the migrator or the
scorer.

Printed gas descriptions and their evaluation temperature are interpreted
here as part of the same oxygen identity domain. Predictor inputs stay the
printed log; these helpers resolve only the pascal identity and admission
evidence inputs.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Mapping, Sequence

from simulator.battery.decimal_tokens import decimal_token as _dec_str
from simulator.battery.enums import ValueKind
from simulator.battery.records import Located, Value
from simulator.physical_constants import BAR_PA_DECIMAL

# 1 bar = 1e5 Pa exactly. An atmosphere frame passes 101325 Pa instead.
BAR_PA = BAR_PA_DECIMAL

def log10_fo2_to_pa(
    log10_fO2: Decimal, reference_pressure_pa: Decimal = BAR_PA
) -> Decimal:
    """log10 fO2 (bar) -> Pa = 10^x × 1e5.

    An atm frame uses 101325 Pa instead of 1e5. Callers pass that reference.
    Unit check: 10^x is dimensionless, so 10^x × Pa is Pa.
    Sanity: log10 fO2 = -9.56 (bar) -> 2.754e-5 Pa
    (1e5 × 10^-9.56 = 0.00002754228703338166448631210659 Pa).
    """

    return reference_pressure_pa * (Decimal(10) ** log10_fO2)


_ATM_BAR = Decimal("1.01325")

# Dry-air O2 is 20.946% by volume (IUPAC Gold Book, "composition of pure air",
# doi:10.1351/goldbook.C01214). The U.S. Standard Atmosphere 1976 table gives
# 20.9476%: 0.0016 percentage points higher, i.e. log10(0.209476/0.20946)
# = +3.3e-5 dex, below the precision of every four-place pin here. For
# ideal gases that volume fraction equals the mole fraction: log10(0.20946) is
# -0.67890 at 1 bar total, and log10(0.20946 × 1.01325) is -0.67318 at 1 atm
# total (both to five decimal places). Versus 0.2095, the delta is
# log10(0.20946 / 0.2095) = -0.00008293 dex (~1e-4), below the precision of
# every printed four-place pin except when rounding flips its fourth place;
# these anchors change from -0.6788/-0.6731 to -0.6789/-0.6732.
_AIR_O2_MOLE_FRACTION = Decimal("0.20946")


def _oxygen_pressure_point(located: object) -> Decimal | None:
    if not isinstance(located, Located) or not located.state.is_value:
        return None
    raw = located.state.value
    if isinstance(raw, Value):
        if raw.kind is not ValueKind.POINT or raw.point is None:
            return None
        raw = raw.point
    if isinstance(raw, Decimal) and raw.is_finite():
        return raw
    return None


def _gas_mixture_evaluation_temperature(
    stated_temperature: Located[Any] | None,
    run_temperature_K: Decimal | None,
) -> tuple[Decimal | None, str | None, str | None]:
    """Use the source's gas-fO2 temperature, or visibly fall back to run T."""

    if stated_temperature is None:
        if (
            run_temperature_K is None
            or not run_temperature_K.is_finite()
            or run_temperature_K <= 0
        ):
            return None, None, None
        return (
            run_temperature_K,
            "source does not state the gas-fO2 evaluation temperature; "
            f"run temperature {_dec_str(run_temperature_K)} K used "
            "for the gas-mixture check",
            None,
        )

    if not isinstance(stated_temperature, Located) or not stated_temperature.state.is_value:
        reason = (
            stated_temperature.state.reason
            if isinstance(stated_temperature, Located)
            else "malformed temperature evidence"
        )
        return (
            None,
            None,
            "gas_mix_fo2_band_unavailable: source gas-fO2 evaluation "
            f"temperature is not a point: {reason or 'unknown'}",
        )

    raw = stated_temperature.state.value
    temperature = (
        raw.point
        if isinstance(raw, Value) and raw.kind is ValueKind.POINT
        else raw if isinstance(raw, Decimal) else None
    )
    if (
        temperature is None
        or not temperature.is_finite()
        or temperature <= 0
    ):
        return (
            None,
            None,
            "gas_mix_fo2_band_unavailable: source gas-fO2 evaluation "
            "temperature must be a positive finite point",
        )
    return temperature, None, None


def _atmosphere_kind(
    as_printed: str,
) -> tuple[str, Decimal, Decimal | None] | None:
    """Recognise only explicitly printed air or pure oxygen atmospheres."""

    text = as_printed.strip().casefold().replace("₂", "2")
    compact = re.sub(r"\s+", " ", text)
    air = re.fullmatch(r"(?:dry )?air(?: at 1(?:\.0+)? (atm|bar))?", compact)
    if air is not None:
        unit = air.group(1)
        total_pressure_bar = (
            _ATM_BAR if unit == "atm" else Decimal(1) if unit == "bar" else None
        )
        return "air", _AIR_O2_MOLE_FRACTION, total_pressure_bar
    oxygen = re.fullmatch(
        r"(?:pure )?(?:o2|oxygen)(?: at 1(?:\.0+)? (atm|bar))?", compact
    )
    if oxygen is not None:
        unit = oxygen.group(1)
        total_pressure_bar = (
            _ATM_BAR if unit == "atm" else Decimal(1) if unit == "bar" else None
        )
        return "pure O2", Decimal(1), total_pressure_bar
    if re.fullmatch(
        r"o2\s+\d+(?:\.\d+)?\s*[×x]\s*\d+(?:\.\d+)?\s*sccm",
        compact,
    ):
        return "pure O2", Decimal(1), None
    return None


def _last_printed_digit(value: Decimal) -> Decimal:
    return Decimal(1).scaleb(value.as_tuple().exponent)


def _display_fo2_dex(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "+.4f")


def _row_printed_atmospheres(
    raw_item: object, experiment_atmospheres: Sequence[str]
) -> tuple[str, ...]:
    """A run's explicit gas label narrows its experiment-set evidence."""

    if isinstance(raw_item, Mapping):
        values = raw_item.get("values")
        containers = (raw_item, values) if isinstance(values, Mapping) else (raw_item,)
        for container in containers:
            for key in (
                "gas_mix_as_printed",
                "atmosphere_as_printed",
                "gas_mixture_as_printed",
            ):
                value = container.get(key)
                if isinstance(value, Mapping):
                    state = value.get("state")
                    value = state.get("value") if isinstance(state, Mapping) else None
                if isinstance(value, str) and value.strip():
                    return (value.strip(),)
    return tuple(experiment_atmospheres)


def _printed_sccm_gas_mixture(
    as_printed: str,
) -> tuple[dict[str, Decimal], dict[str, float]] | None:
    """Parse printed flow/ratio gas mixtures to normalized mole fractions."""

    normalized = as_printed.replace("₂", "2").replace("₀", "0")
    ratio = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(CO2|CO|H2O|H2)\s*:\s*"
        r"(\d+(?:\.\d+)?)\s*(CO2|CO|H2O|H2)\s*",
        normalized,
        re.IGNORECASE,
    )
    if ratio is not None:
        first_amount, first_species, second_amount, second_species = ratio.groups()
        if first_species.upper() == second_species.upper():
            return None
        amounts = {
            first_species.upper(): Decimal(first_amount),
            second_species.upper(): Decimal(second_amount),
        }
        total = sum(amounts.values())
        if total <= 0:
            return None
        # Volume ratio at common T/P is mole ratio. Normalizing the total to
        # one mole makes the WGS inputs dimensionless mole fractions.
        return (
            {name: amount / total for name, amount in amounts.items()},
            {name: 0.0 for name in amounts},
        )
    flow_ratio = re.fullmatch(
        r"\s*(CO2|CO|H2O|H2)\s*:\s*(CO2|CO|H2O|H2)\s*:\s*(N2|AR)\s*=\s*"
        r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*:\s*"
        r"(\d+(?:\.\d+)?)\s*(?:c\.c\.s/min)?\s*",
        normalized,
        re.IGNORECASE,
    )
    if flow_ratio is not None:
        first_species, second_species, inert_species, *raw_amounts = flow_ratio.groups()
        first_species, second_species, inert_species = (
            first_species.upper(), second_species.upper(), inert_species.upper()
        )
        if first_species == second_species:
            return None
        amounts = tuple(Decimal(value) for value in raw_amounts)
        if any(amount < 0 for amount in amounts) or sum(amounts) <= 0:
            return None
        total = sum(amounts)
        # c.c.s/min is a volumetric-flow unit; common T/P makes its ratios
        # proportional molar flows. N2 is normalized with the whole feed and
        # remains in the total pressure while contributing no reactive atoms.
        # Its dilution effect cancels only when dissociation is negligible;
        # CO2/H2O dissociation has Δn=1/2 and responds to partial pressure.
        return (
            dict(zip((first_species, second_species, inert_species), (amount / total for amount in amounts))),
            {name: 0.0 for name in (first_species, second_species, inert_species)},
        )
    pieces = normalized.split("+")
    species_mol: dict[str, Decimal] = {}
    relative_precision: dict[str, float] = {}
    component = re.compile(
        r"^\s*(CO2|CO|O2|H2O|H2|N2|AR)\s+(\d+(?:\.\d+)?)\s*[×x]\s*"
        r"(\d+(?:\.\d+)?)\s*SCCM\s*$",
        re.IGNORECASE,
    )
    for piece in pieces:
        match = component.fullmatch(piece)
        if match is None:
            return None
        species = match.group(1).upper()
        fraction = Decimal(match.group(2))
        flow = Decimal(match.group(3))
        if species in species_mol or fraction <= 0 or flow <= 0:
            return None
        species_mol[species] = fraction * flow
        # The printed percent and SCCM value both contribute to this component
        # flow's absolute last-digit uncertainty.
        relative_precision[species] = float(
            _last_printed_digit(fraction) / fraction
            + _last_printed_digit(flow) / flow
        )
    if len(species_mol) < 2:
        return None
    total = sum(species_mol.values())
    return (
        {name: amount / total for name, amount in species_mol.items()},
        relative_precision,
    )
