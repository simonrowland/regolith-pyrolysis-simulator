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
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from simulator.battery.decimal_tokens import decimal_token as _dec_str
from simulator.battery.enums import ValueKind
from simulator.battery.records import Located, Value, nominal_pressure_pa
from simulator.physical_constants import (
    BAR_PA_DECIMAL,
    CELSIUS_TO_KELVIN_OFFSET,
    STANDARD_ATMOSPHERE_PA,
)

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
SOSSI_GAS_EQUILIBRIUM_BAND_DEX = Decimal("0.05")


@dataclass(frozen=True)
class SossiGasEquilibriumCheck:
    experiment_id: str
    run_id: str
    method: str
    printed_log10_fO2_bar: Decimal
    computed_log10_fO2_bar: Decimal | None
    delta_log10_fO2_dex: Decimal | None
    checked_log10_fO2_bar: Decimal | None
    refusal_reason: str | None = None
    refusal_detail: str | None = None


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


def check_sossi_table2_gas_equilibrium(
    experiments: Sequence[Mapping[str, Any]],
    table2_series: Sequence[Mapping[str, Any]],
) -> Mapping[str, SossiGasEquilibriumCheck]:
    """Check each printed Sossi Table 2 fO2 against its metered cover gas."""
    experiments_by_run: dict[str, Mapping[str, Any]] = {}
    for experiment in experiments:
        run_id = _sossi_experiment_run_id(experiment)
        if run_id in experiments_by_run:
            raise ValueError(f"sossi_table2_experiment_repeated:{run_id}")
        experiments_by_run[run_id] = experiment

    checks: dict[str, SossiGasEquilibriumCheck] = {}
    for row in table2_series:
        run_id, temperature_C, co_sccm, co2_sccm, printed_log = (
            _sossi_table2_gas_row(row)
        )
        experiment = experiments_by_run.get(run_id)
        if experiment is None:
            raise ValueError(f"sossi_table2_experiment_missing:{run_id}")
        experiment_id = str(experiment.get("experiment_id") or "")
        if not experiment_id or experiment_id in checks:
            raise ValueError(f"sossi_table2_experiment_id_invalid:{run_id}")
        checks[experiment_id] = _check_sossi_table2_row(
            experiment,
            run_id=run_id,
            temperature_C=temperature_C,
            co_sccm=co_sccm,
            co2_sccm=co2_sccm,
            printed_log=printed_log,
        )

    expected_ids = {
        str(experiment.get("experiment_id") or "") for experiment in experiments
    }
    if set(checks) != expected_ids:
        raise ValueError("sossi_table2_experiment_series_mismatch")
    return checks


def _sossi_experiment_run_id(experiment: Mapping[str, Any]) -> str:
    locator = experiment.get("locator")
    note = locator.get("note") if isinstance(locator, Mapping) else None
    prefix = "Printed Table 2 physical run "
    if not isinstance(note, str) or not note.startswith(prefix):
        raise ValueError("sossi_table2_experiment_locator_invalid")
    run_id = note[len(prefix) :].split(";", 1)[0].strip().rstrip("*")
    page = locator.get("page")
    if locator.get("table") != "2" or page not in {54, 55} or not run_id:
        raise ValueError("sossi_table2_experiment_locator_invalid")
    return run_id


def _sossi_experiment_recorded_log10_fo2(
    experiment: Mapping[str, Any],
) -> Decimal | None:
    """Read an independently located Sossi run fO2 from its buffer record."""
    control = experiment.get("fO2_control")
    buffer = control.get("buffer") if isinstance(control, Mapping) else None
    state = buffer.get("state") if isinstance(buffer, Mapping) else None
    locator = buffer.get("locator") if isinstance(buffer, Mapping) else None
    value = state.get("value") if isinstance(state, Mapping) else None
    if not isinstance(locator, Mapping) or not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*log10\s+fO2\s*=\s*(-?\d+(?:\.\d+)?)\s*", value)
    if match is None:
        return None
    try:
        parsed = Decimal(match.group(1))
    except (ArithmeticError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _sossi_table2_gas_row(
    row: Mapping[str, Any],
) -> tuple[str, Decimal, Decimal | None, Decimal | None, Decimal]:
    run_id = str(row.get("run_id") or "").strip().rstrip("*")
    locator = row.get("locator")
    if not run_id or not isinstance(locator, Mapping):
        raise ValueError("sossi_table2_gas_row_locator_invalid")
    quote = locator.get("quote")
    if (
        locator.get("table") != "2"
        or locator.get("page") not in {54, 55}
        or not isinstance(quote, str)
    ):
        raise ValueError(f"sossi_table2_gas_row_locator_invalid:{run_id}")
    quoted_run_id, temperature_C, co_sccm, co2_sccm, printed_log = (
        _sossi_table2_gas_row_from_quote(quote, expected_run_id=run_id)
    )

    structured_temperature = _sossi_decimal(row.get("T_C"), f"{run_id}.T_C")
    structured_log = row.get("log10_fO2")
    if isinstance(structured_log, Mapping):
        structured_log = structured_log.get("value")
    if (
        quoted_run_id != run_id
        or structured_temperature != temperature_C
        or _sossi_decimal(structured_log, f"{run_id}.log10_fO2") != printed_log
    ):
        raise ValueError(f"sossi_table2_gas_row_quote_disagrees:{run_id}")
    return run_id, temperature_C, co_sccm, co2_sccm, printed_log


def _sossi_table2_gas_row_from_quote(
    quote: str, *, expected_run_id: str | None = None
) -> tuple[str, Decimal, Decimal | None, Decimal | None, Decimal]:
    if not isinstance(quote, str) or not quote.strip():
        label = expected_run_id or "unknown"
        raise ValueError(f"sossi_table2_gas_row_quote_invalid:{label}")
    header = quote.splitlines()[0].split("|")
    if tuple(item.strip() for item in header[:6]) != (
        "Run#",
        "T_C",
        "t_min",
        "CO_sccm",
        "CO2_sccm",
        "logfO2",
    ):
        label = expected_run_id or "unknown"
        raise ValueError(f"sossi_table2_gas_row_header_invalid:{label}")
    row_pattern = re.compile(
        r"(?<![A-Z0-9])(?P<run_id>[A-Z0-9][A-Z0-9./-]*\*?)\s+"
        r"(?P<temperature>\d+(?:\.\d+)?)\s+"
        r"(?P<duration>\d+(?:\.\d+)?)\s+"
        r"(?P<co>-|–|—|\d+(?:\.\d+)?)\s+"
        r"(?P<co2>-|–|—|\d+(?:\.\d+)?)\s+"
        r"(?P<fo2>-?\d+(?:\.\d+)?)\s+(?:PT|RE|IR)\b",
        re.IGNORECASE,
    )
    matches = [
        match
        for match in row_pattern.finditer(quote)
        if expected_run_id is None
        or match.group("run_id").rstrip("*") == expected_run_id
    ]
    if len(matches) != 1:
        label = expected_run_id or "unknown"
        raise ValueError(f"sossi_table2_gas_row_quote_invalid:{label}")
    match = matches[0]
    run_id = match.group("run_id").rstrip("*")
    temperature_C = _sossi_decimal(match.group("temperature"), f"{run_id}.T_C")
    co_sccm = _sossi_optional_flow(match.group("co"), f"{run_id}.CO_sccm")
    co2_sccm = _sossi_optional_flow(match.group("co2"), f"{run_id}.CO2_sccm")
    printed_log = _sossi_decimal(match.group("fo2"), f"{run_id}.logfO2")
    if co_sccm == 0:
        co_sccm = Decimal(0)
    return run_id, temperature_C, co_sccm, co2_sccm, printed_log


def _sossi_table2_approximate_total_flow_sccm(
    run_id: str,
    co_sccm: Decimal | None,
    co2_sccm: Decimal | None,
) -> Decimal | None:
    """Methods p. 6 reports ≈200 sccm except for air and ANU runs.

    The Table 2 ANU runs have C-prefixed IDs. Blank CO/CO2 columns are the
    air runs, so neither exception receives a synthesized total.
    """

    if (co_sccm is None and co2_sccm is None) or run_id.startswith("C"):
        return None
    return Decimal("200")


def _sossi_decimal(raw: Any, label: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"sossi_table2_value_invalid:{label}") from exc
    if not value.is_finite():
        raise ValueError(f"sossi_table2_value_invalid:{label}")
    return value


def _sossi_optional_flow(raw: str, label: str) -> Decimal | None:
    if raw in {"-", "–", "—"}:
        return None
    value = _sossi_decimal(raw, label)
    if value < 0:
        raise ValueError(f"sossi_table2_flow_negative:{label}")
    return value


def _sossi_point(experiment_field: Any) -> Decimal | None:
    return nominal_pressure_pa(experiment_field)


def _sossi_cover_matches(experiment: Mapping[str, Any], expected: str) -> bool:
    pressure = experiment.get("pressure_environment")
    sweep_gas = pressure.get("sweep_gas") if isinstance(pressure, Mapping) else None
    state = sweep_gas.get("state") if isinstance(sweep_gas, Mapping) else None
    if not isinstance(state, Mapping) or state.get("tag") == "unknown":
        return True
    if state.get("tag") != "value" or not isinstance(state.get("value"), str):
        return False
    text = state["value"].upper().replace("₂", "2")
    terms = [
        term.strip()
        for term in re.split(r"\s*(?:\+|/|,|;|–|—|-)\s*", text)
        if term.strip()
    ]
    aliases = {"ARGON": "AR"}
    species = {aliases.get(term, term) for term in terms}
    expected_species = {
        "co_co2": {"CO", "CO2"},
        "co2": {"CO2"},
        "air": {"AIR"},
    }.get(expected)
    recognized = {"CO", "CO2", "H2O", "H2", "O2", "N2", "AR", "AIR", "KR", "HE"}
    return (
        expected_species is not None
        and bool(species)
        and species <= recognized
        and species == expected_species
    )


def _check_sossi_table2_row(
    experiment: Mapping[str, Any],
    *,
    run_id: str,
    temperature_C: Decimal,
    co_sccm: Decimal | None,
    co2_sccm: Decimal | None,
    printed_log: Decimal,
) -> SossiGasEquilibriumCheck:
    experiment_id = str(experiment.get("experiment_id") or "")

    def refuse(
        reason: str,
        detail: str,
        *,
        method: str = "unavailable",
        computed: Decimal | None = None,
        delta: Decimal | None = None,
    ) -> SossiGasEquilibriumCheck:
        return SossiGasEquilibriumCheck(
            experiment_id,
            run_id,
            method,
            printed_log,
            computed,
            delta,
            None,
            reason,
            detail,
        )

    pressure_environment = experiment.get("pressure_environment")
    pressure = pressure_environment if isinstance(pressure_environment, Mapping) else {}
    total_pressure_pa = _sossi_point(pressure.get("total_pressure_Pa"))
    thermal_schedule = experiment.get("thermal_schedule")
    setpoints = (
        thermal_schedule.get("setpoints_and_holds", ())
        if isinstance(thermal_schedule, Mapping)
        else ()
    )
    first_setpoint = setpoints[0] if setpoints and isinstance(setpoints[0], Mapping) else {}
    temperature_K = _sossi_point(first_setpoint.get("temperature_K"))
    if (
        total_pressure_pa != Decimal(str(STANDARD_ATMOSPHERE_PA))
        or temperature_K is None
        or total_pressure_pa is None
    ):
        return refuse(
            "gas_equilibrium_fo2_unavailable",
            "Sossi Table 2 requires its printed run temperature and 101325 Pa pressure.",
        )
    temperature_from_table_K = temperature_C + Decimal(
        str(CELSIUS_TO_KELVIN_OFFSET)
    )
    if temperature_K != temperature_from_table_K:
        return refuse(
            "gas_equilibrium_fo2_unavailable",
            "Table 2 temperature disagrees with the experiment run temperature.",
        )

    if co_sccm is not None and co_sccm > 0 and co2_sccm is not None and co2_sccm > 0:
        method = "imposed_fo2"
        expected_cover = "co_co2"
    elif (co_sccm is None or co_sccm == 0) and co2_sccm is not None and co2_sccm > 0:
        method = "co2_dissociation_partials_bar"
        expected_cover = "co2"
    elif co_sccm is None and co2_sccm is None:
        method = "printed_air"
        expected_cover = "air"
    else:
        return refuse(
            "gas_equilibrium_fo2_disagrees_with_printed",
            "Table 2 does not provide a complete CO/CO2 cover-gas basis.",
        )

    if not _sossi_cover_matches(experiment, expected_cover):
        return refuse(
            "cover_not_printed_mixture",
            f"{run_id} does not use the cover gas printed in Table 2.",
            method=method,
        )
    if method == "printed_air":
        if printed_log != Decimal("-0.68"):
            return refuse(
                "gas_equilibrium_fo2_disagrees_with_printed",
                "Blank Table 2 CO/CO2 flows are the printed air runs at log fO2 -0.68.",
                method=method,
            )
        return SossiGasEquilibriumCheck(
            experiment_id,
            run_id,
            method,
            printed_log,
            None,
            None,
            printed_log,
        )

    from simulator.chemistry.offgas_fo2 import (
        OffgasFO2Unavailable,
        co2_dissociation_partials_bar,
        imposed_fo2,
    )

    try:
        if method == "imposed_fo2":
            if co_sccm is None or co2_sccm is None:
                return refuse(
                    "gas_equilibrium_fo2_unavailable",
                    "The printed CO/CO2 couple is incomplete.",
                    method=method,
                )
            resolution = imposed_fo2(
                {"CO": float(co_sccm), "CO2": float(co2_sccm)},
                float(temperature_K),
            )
            if (
                resolution.coupling != "co_couple_only"
                or resolution.status != "computed_no_reconciliation"
                or resolution.log10_fO2 is None
            ):
                raise OffgasFO2Unavailable("CO/CO2 did not resolve as its printed couple")
            computed_log = Decimal(str(resolution.log10_fO2))
        else:
            partials = co2_dissociation_partials_bar(
                float(temperature_K),
                float(total_pressure_pa / BAR_PA),
                0.0,
            )
            computed_log = Decimal(str(partials["p_O2_bar"])).log10()
        if not computed_log.is_finite():
            return refuse(
                "gas_equilibrium_fo2_unavailable",
                "The equilibrium owner returned a non-finite log fO2.",
                method=method,
            )
    except (ArithmeticError, OffgasFO2Unavailable, TypeError, ValueError) as exc:
        return refuse(
            "gas_equilibrium_fo2_unavailable",
            str(exc),
            method=method,
        )

    delta = computed_log - printed_log
    if abs(delta) > SOSSI_GAS_EQUILIBRIUM_BAND_DEX:
        return refuse(
            "gas_equilibrium_fo2_disagrees_with_printed",
            f"computed={computed_log} printed={printed_log} delta={delta} dex",
            method=method,
            computed=computed_log,
            delta=delta,
        )
    return SossiGasEquilibriumCheck(
        experiment_id,
        run_id,
        method,
        printed_log,
        computed_log,
        delta,
        printed_log,
    )
