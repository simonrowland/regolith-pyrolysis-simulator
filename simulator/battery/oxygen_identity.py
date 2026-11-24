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
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping, Sequence

from simulator.battery.decimal_tokens import decimal_token as _dec_str
from simulator.battery.default_policy import resolve as resolve_default_policy
from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    NoticeKind,
    Quantity,
    ValueKind,
)
from simulator.battery.identity import Identity
from simulator.battery.records import (
    AmbientPressureEvidence,
    Located,
    Notice,
    NoticePolicy,
    Observation,
    Value,
    nominal_pressure_pa,
)
from simulator.chemistry.fo2_buffers import evaluate_buffer
from simulator.physical_constants import (
    BAR_PA_DECIMAL,
    CELSIUS_TO_KELVIN_OFFSET,
    STANDARD_ATMOSPHERE_PA,
)
from simulator.source_ids import SOSSI_SOURCE_ID
from simulator.state_types import State

# 1 bar = 1e5 Pa exactly. An atmosphere frame passes 101325 Pa instead.
BAR_PA = BAR_PA_DECIMAL

_BUFFER_CALIBRATION_TARGET_QUANTITIES = {
    "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas":
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    "jugo-2010-sulfur-xanes": Quantity.VALENCE_RATIO,
    "gaillard-2003-hydrous-silicic-melt-activity": Quantity.ACTIVITY,
    "borisov-veksler-2021-k-fe-supplement":
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    "zagrtdenov-2020-chromite-magnesiochromite-solubility":
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    "neuman-2026-levitation-k-isotopes": Quantity.MASS_LOSS_FRACTION,
    "lu-2026-mercury-sulfur-liquidus":
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation":
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
}

_BUFFER_OFFSET_PATTERN = re.compile(
    r"(?:^|\s)buffer=([^\s;]+).*?(?:^|\s)offset=([^\s;]+)"
)


def _printed_buffer_offset(reason: str) -> tuple[str, Decimal] | None:
    match = _BUFFER_OFFSET_PATTERN.search(reason)
    if match is None:
        return None
    buffer, raw_offset = match.groups()
    try:
        offset = Decimal(raw_offset.replace("−", "-"))
    except (InvalidOperation, ValueError):
        return None
    if not offset.is_finite():
        return None
    return buffer, offset


def _dp002_applicability(
    evidence: AmbientPressureEvidence | None,
    *,
    atmosphere_condition: Located[Any] | None = None,
) -> str | None:
    if evidence is None:
        return (
            'experiment "in air"'
            if _air_composition_quote(
                None, atmosphere_condition=atmosphere_condition
            )
            is not None
            else None
        )
    if evidence.evidence_class is AmbientPressureEvidenceClass.GAS_MIXING:
        return "gas-mixing furnace"
    if evidence.evidence_class is AmbientPressureEvidenceClass.GAS_FLOW:
        quote = " ".join(evidence.quote.casefold().replace("-", " ").split())
        if "wire loop" in quote and "equilibr" in quote:
            return "wire-loop equilibration"
    if _air_composition_quote(
        evidence, atmosphere_condition=atmosphere_condition
    ) is not None:
        return 'experiment "in air"'
    return None


# Negated or excluded air is not a run atmosphere. The categorical token is
# matched before this, so a located value "air" is not sent through prose.
# A claim is one clause: the sample, the equilibration event, and the
# atmosphere have to belong to that same clause. Preparation under argon
# does not veto a later air equilibration, and a reference gas equilibrated
# in air is not the sample. "argon sample" does not count.
_CLAIM_BOUNDARY = re.compile(r"\b(?:while|whereas|but)\b|[.;!?]+")
_AIR_SUBJECT = re.compile(
    r"\b(?:samples|sample|melts|melt|glasses|glass|charges|charge)\b"
)
_OTHER_ATMOSPHERE_SUBJECT = re.compile(r"\breference\s+gas\b")
_AIR_EQUILIBRATION = re.compile(
    r"\bequilibrat\w*(?:\s+\w+){0,6}\s+"
    r"(?:in|under|atmosphere)\s+(?:ambient\s+)?air\b(?!\s+free\b)"
)
_AIR_SENSOR_OR_ROOM = re.compile(r"\b(?:sensor|room)\b")


_AIR_NEGATION = re.compile(
    r"(?:"
    r"\b(?:not|never|without|no|n't)\b(?:\s+\w+){0,6}\s+equilibrat"
    r"|"
    r"\bair\b(?:\s+\w+){0,8}\s+(?:excluded|displaced|replaced|removed|absent)\b"
    r"|"
    r"\b(?:excluded|displaced|replaced|removed|without|not|no)\b"
    r"(?:\s+\w+){0,8}\s+air\b"
    r")"
)


def _air_composition_quote(
    evidence: AmbientPressureEvidence | None,
    *,
    atmosphere_condition: Located[Any] | None = None,
) -> str | None:
    """Return printed evidence that explicitly names an air atmosphere."""

    if (
        isinstance(atmosphere_condition, Located)
        and atmosphere_condition.inference is None
        and atmosphere_condition.state.is_value
    ):
        value = atmosphere_condition.state.value
        if isinstance(value, Value):
            value = (
                value.categorical
                if value.kind is ValueKind.CATEGORICAL
                else None
            )
        if not isinstance(value, str):
            return None
        quote = value
    elif evidence is not None:
        quote = evidence.quote
    else:
        return None
    source_quote = quote
    normalized_quote = " ".join(quote.casefold().replace("-", " ").split())
    atmosphere = _atmosphere_kind(normalized_quote)
    if atmosphere is not None and atmosphere[0] == "air":
        return source_quote
    if _AIR_NEGATION.search(normalized_quote):
        return None
    # Air has to be the sample atmosphere during equilibration. "Held in air
    # before the run" is not that stage. Sensor and room air reject only the
    # clause that contains them. "air-free" normalizes to "air free".
    # The last subject before the equilibration decides the claim.
    clauses = [
        part.strip()
        for part in _CLAIM_BOUNDARY.split(normalized_quote)
        if part.strip()
    ]
    for clause in clauses:
        if _AIR_SENSOR_OR_ROOM.search(clause) is not None:
            continue
        equilibration = _AIR_EQUILIBRATION.search(clause)
        if equilibration is None:
            continue
        prefix = clause[: equilibration.start()]
        subjects = (
            *((match.start(), "sample") for match in _AIR_SUBJECT.finditer(prefix)),
            *(
                (match.start(), "other")
                for match in _OTHER_ATMOSPHERE_SUBJECT.finditer(prefix)
            ),
        )
        if not subjects or max(subjects)[1] != "sample":
            continue
        return source_quote
    return None


def _dp002_pressure_default(
    evidence: AmbientPressureEvidence | None,
    quantity: Quantity,
    *,
    atmosphere_condition: Located[Any] | None = None,
) -> tuple[Decimal | None, Notice | None]:
    applicability = _dp002_applicability(
        evidence, atmosphere_condition=atmosphere_condition
    )
    if applicability is None:
        return None, None
    policy = resolve_default_policy("DP-002")
    pressure_pa = policy.value
    if not isinstance(pressure_pa, Decimal):
        return None, None
    return pressure_pa, Notice(
        kind=policy.flag,
        affected_quantities=(quantity,),
        reason=(
            f"DP-002 applies to registered condition {applicability}; "
            "chamber pressure is unreported"
        ),
        origin="score:DP-002",
        policy=NoticePolicy(**policy.payload["policy"]),
    )


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


def _dp112_receipts(observation: Observation) -> tuple[Notice, ...]:
    """Score-time notices that record a DP-112 buffer evaluation."""

    return tuple(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        and notice.policy is not None
        and notice.policy.id == "DP-112"
    )


def _resolve_score_time_buffer_identity(
    observation: Observation,
    *,
    pressure_pa: Decimal | None = None,
    pressure_source: str | None = None,
    ambient_pressure_evidence: AmbientPressureEvidence | None = None,
) -> Observation:
    """Resolve the eight numeric-source buffer markers for score-time use."""

    if not isinstance(observation.identity, Identity):
        return observation
    expected_quantity = _BUFFER_CALIBRATION_TARGET_QUANTITIES.get(
        observation.source_id or ""
    )
    quantity = observation.identity.quantity
    if (
        expected_quantity is None
        or not quantity.is_value
        or quantity.value is not expected_quantity
    ):
        return observation

    if _dp112_receipts(observation):
        return observation
    marker = next(
        (
            notice
            for notice in observation.notices
            if notice.kind
            is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        ),
        None,
    )
    if marker is None:
        return observation

    printed_buffer = _printed_buffer_offset(marker.reason)
    temperature_state = observation.identity.temperature_K
    if (
        printed_buffer is None
        or temperature_state is None
        or not temperature_state.is_value
    ):
        return observation
    buffer, offset = printed_buffer
    temperature = temperature_state.value
    if (
        not isinstance(temperature, Decimal)
        or not temperature.is_finite()
        or temperature <= 0
    ):
        return observation

    pressure_state = observation.identity.total_pressure_Pa
    pressure_route = pressure_source or "pressure_boundary"
    pressure_notice = None
    if pressure_state is not None and pressure_state.is_value:
        resolved_pressure_pa = nominal_pressure_pa(pressure_state)
        if resolved_pressure_pa is not None:
            pressure_route = "identity.total_pressure_Pa"
            if not resolved_pressure_pa.is_finite() or resolved_pressure_pa < 0:
                return observation
            pressure_pa = resolved_pressure_pa
    if pressure_pa is not None:
        if not pressure_pa.is_finite() or pressure_pa < 0:
            return observation
    else:
        pressure_pa, pressure_notice = _dp002_pressure_default(
            ambient_pressure_evidence,
            expected_quantity,
            atmosphere_condition=(observation.point_conditions or {}).get(
                "atmosphere"
            ),
        )
        if pressure_pa is not None:
            pressure_route = "method_policy:DP-002"
        else:
            pressure_notice = Notice(
                kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
                affected_quantities=(expected_quantity,),
                reason=(
                    "DP-112 total pressure is unreported and no applicable pressure "
                    "policy resolves it; evaluate_buffer pressure term omitted at its "
                    "one-bar reference"
                ),
                origin="score:DP-112",
            )
    pressure_bar = None if pressure_pa is None else pressure_pa / BAR_PA
    try:
        evaluation = evaluate_buffer(buffer, temperature, pressure_bar=pressure_bar)
    except (KeyError, ValueError):
        return observation
    log10_fO2_bar = evaluation.log10_fO2_bar + offset
    resolved_pa = log10_fo2_to_pa(log10_fO2_bar)
    band = (
        "unsized"
        if evaluation.band_dex is None
        else format(evaluation.band_dex, "f")
    )
    pressure_text = (
        "not supplied (term omitted at one-bar reference)"
        if pressure_bar is None
        else format(pressure_bar, "f")
    )
    resolved_policy = resolve_default_policy("DP-112", formula_result=resolved_pa)
    notice = Notice(
        kind=resolved_policy.flag,
        affected_quantities=(expected_quantity,),
        reason=(
            f"DP-112 buffer={buffer} offset={offset} calibration="
            f"{evaluation.calibration_id} T_K={temperature} P_bar={pressure_text} "
            f"pressure_route={pressure_route} "
            f"log10_fO2_bar={log10_fO2_bar} band_dex={band}"
        ),
        origin="score:DP-112",
        band=band,
        policy=NoticePolicy(**resolved_policy.payload["policy"]),
    )
    identity = replace(observation.identity, fO2_Pa=State.of(resolved_pa))
    return replace(
        observation,
        identity=identity,
        notices=(
            *observation.notices,
            *((pressure_notice,) if pressure_notice is not None else ()),
            notice,
        ),
    )


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
    printed_log10_fO2_bar: Decimal | None
    computed_log10_fO2_bar: Decimal | None
    delta_log10_fO2_dex: Decimal | None
    checked_log10_fO2_bar: Decimal | None
    refusal_reason: str | None = None
    refusal_detail: str | None = None
    row_validated: bool = False


@dataclass(frozen=True)
class SossiRunOxygenDecision:
    experiment_id: str
    log10_fO2_bar: Decimal | None
    source: str | None
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

    rows_by_run: dict[str, list[Mapping[str, Any]]] = {}
    for row in table2_series:
        raw_run_id = row.get("run_id") if isinstance(row, Mapping) else None
        run_id = str(raw_run_id or "").strip().rstrip("*")
        if run_id:
            rows_by_run.setdefault(run_id, []).append(row)

    checks: dict[str, SossiGasEquilibriumCheck] = {}
    for run_id, experiment in experiments_by_run.items():
        experiment_id = str(experiment.get("experiment_id") or "")
        if not experiment_id:
            continue
        rows = rows_by_run.get(run_id, ())
        if len(rows) != 1:
            reason = (
                "sossi_table2_gas_row_missing"
                if not rows
                else "sossi_table2_gas_row_repeated"
            )
            checks[experiment_id] = SossiGasEquilibriumCheck(
                experiment_id,
                run_id,
                "unavailable",
                None,
                None,
                None,
                None,
                reason,
                f"expected one Table 2 row for {run_id}, found {len(rows)}",
            )
            continue
        try:
            parsed_run_id, temperature_C, co_sccm, co2_sccm, printed_log = (
                _sossi_table2_gas_row(rows[0])
            )
            checks[experiment_id] = _check_sossi_table2_row(
                experiment,
                run_id=parsed_run_id,
                temperature_C=temperature_C,
                co_sccm=co_sccm,
                co2_sccm=co2_sccm,
                printed_log=printed_log,
            )
        except (TypeError, ValueError) as exc:
            structured_log = rows[0].get("log10_fO2")
            if isinstance(structured_log, Mapping):
                structured_log = structured_log.get("value")
            try:
                row_locator = rows[0].get("locator")
                quote = row_locator.get("quote") if isinstance(row_locator, Mapping) else None
                quoted_run, quoted_temperature, co_sccm, co2_sccm, quoted_log = (
                    _sossi_table2_gas_row_from_quote(
                        quote,
                        expected_run_id=run_id,
                    )
                )
                structured_temperature = rows[0].get("T_C")
                if (
                    str(exc) != f"sossi_table2_gas_row_quote_disagrees:{run_id}"
                    or quoted_run != run_id
                    or structured_temperature is None
                    or _sossi_decimal(
                        structured_temperature, f"{run_id}.T_C"
                    )
                    != quoted_temperature
                    or structured_log is None
                    or quoted_log is None
                    or _sossi_decimal(
                        structured_log, f"{run_id}.log10_fO2"
                    )
                    == quoted_log
                ):
                    raise ValueError(str(exc))
                gas_check = _check_sossi_table2_row(
                    experiment,
                    run_id=run_id,
                    temperature_C=quoted_temperature,
                    co_sccm=co_sccm,
                    co2_sccm=co2_sccm,
                    printed_log=quoted_log,
                )
                checks[experiment_id] = SossiGasEquilibriumCheck(
                    experiment_id,
                    run_id,
                    gas_check.method,
                    quoted_log,
                    gas_check.computed_log10_fO2_bar,
                    gas_check.delta_log10_fO2_dex,
                    None,
                    "sossi_table2_structured_fo2_disagrees_with_quote",
                    str(exc),
                    True,
                )
                continue
            except (TypeError, ValueError):
                pass
            checks[experiment_id] = SossiGasEquilibriumCheck(
                experiment_id,
                run_id,
                "unavailable",
                None,
                None,
                None,
                None,
                str(exc).split(":", 1)[0],
                str(exc),
            )
    return checks


def _sossi_experiment_run_id(experiment: Mapping[str, Any]) -> str:
    locator = (
        experiment.get("locator")
        if isinstance(experiment, Mapping)
        else getattr(experiment, "locator", None)
    )
    note = (
        locator.get("note")
        if isinstance(locator, Mapping)
        else getattr(locator, "note", None)
    )
    prefix = "Printed Table 2 physical run "
    if not isinstance(note, str) or not note.startswith(prefix):
        raise ValueError("sossi_table2_experiment_locator_invalid")
    run_id = note[len(prefix) :].split(";", 1)[0].strip().rstrip("*")
    page = locator.get("page") if isinstance(locator, Mapping) else getattr(locator, "page", None)
    table = locator.get("table") if isinstance(locator, Mapping) else getattr(locator, "table", None)
    if table != "2" or page not in {54, 55} or not run_id:
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
    try:
        expected_run_id = _sossi_experiment_run_id(experiment)
        recorded_run_id = _sossi_experiment_run_id({"locator": locator})
    except ValueError:
        return None
    if recorded_run_id != expected_run_id:
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
) -> tuple[str, Decimal, Decimal | None, Decimal | None, Decimal | None]:
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

    raw_temperature = row.get("T_C")
    structured_temperature = (
        _sossi_decimal(raw_temperature, f"{run_id}.T_C")
        if raw_temperature is not None
        else None
    )
    structured_log = row.get("log10_fO2")
    if isinstance(structured_log, Mapping):
        structured_log = structured_log.get("value")
    parsed_structured_log = (
        _sossi_decimal(structured_log, f"{run_id}.log10_fO2")
        if structured_log is not None
        else None
    )
    if (
        quoted_run_id != run_id
        or (
            structured_temperature is not None
            and structured_temperature != temperature_C
        )
        or (
            parsed_structured_log is not None
            and parsed_structured_log != printed_log
        )
    ):
        raise ValueError(f"sossi_table2_gas_row_quote_disagrees:{run_id}")
    return run_id, temperature_C, co_sccm, co2_sccm, printed_log


def _sossi_table2_gas_row_from_quote(
    quote: str, *, expected_run_id: str | None = None
) -> tuple[str, Decimal, Decimal | None, Decimal | None, Decimal | None]:
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
        r"(?P<fo2>-|–|—|-?\d+(?:\.\d+)?)\s+(?:PT|RE|IR)\b",
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
    printed_log = (
        None
        if match.group("fo2") in {"-", "–", "—"}
        else _sossi_decimal(match.group("fo2"), f"{run_id}.logfO2")
    )
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


def _sossi_table2_flow_interpretation(
    co_sccm: Decimal | None,
    co2_sccm: Decimal | None,
) -> tuple[str, str] | None:
    if (
        co_sccm is not None
        and co_sccm > 0
        and co2_sccm is not None
        and co2_sccm > 0
    ):
        return "imposed_fo2", "co_co2"
    if (
        (co_sccm is None or co_sccm == 0)
        and co2_sccm is not None
        and co2_sccm > 0
    ):
        return "co2_dissociation_partials_bar", "co2"
    if co_sccm is None and co2_sccm is None:
        return "printed_air", "air"
    return None


def _sossi_point(experiment_field: Any) -> Decimal | None:
    return nominal_pressure_pa(experiment_field)


def _sossi_evidence_decimal(evidence: Any) -> Decimal | None:
    if isinstance(evidence, Located):
        if not evidence.state.is_value:
            return None
        evidence = evidence.state.value
    if isinstance(evidence, Value):
        if evidence.kind is not ValueKind.POINT or evidence.point is None:
            return None
        evidence = evidence.point
    try:
        value = Decimal(str(evidence))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return value if value.is_finite() else None


def _sossi_field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _sossi_cover_matches(experiment: Mapping[str, Any], expected: str) -> bool:
    pressure = (
        experiment.get("pressure_environment")
        if isinstance(experiment, Mapping)
        else getattr(experiment, "pressure_environment", None)
    )
    sweep_gas = (
        pressure.get("sweep_gas")
        if isinstance(pressure, Mapping)
        else getattr(pressure, "sweep_gas", None)
    )
    state = (
        sweep_gas.get("state")
        if isinstance(sweep_gas, Mapping)
        else getattr(sweep_gas, "state", None)
    )
    if isinstance(state, Mapping):
        if state.get("tag") == "unknown":
            return True
        if state.get("tag") != "value":
            return False
        value = state.get("value")
        text = value if isinstance(value, str) else getattr(value, "species", None)
    elif state is not None and hasattr(state, "is_value"):
        if not state.is_value:
            return True
        value = state.value
        text = value if isinstance(value, str) else getattr(value, "species", None)
    else:
        return True
    if not isinstance(text, str):
        return False
    text = text.upper().replace("₂", "2")
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
    printed_log: Decimal | None,
) -> SossiGasEquilibriumCheck:
    experiment_id = str(_sossi_field(experiment, "experiment_id") or "")

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
            True,
        )

    pressure_environment = _sossi_field(experiment, "pressure_environment")
    total_pressure_pa = _sossi_point(
        _sossi_field(pressure_environment, "total_pressure_Pa")
    )
    thermal_schedule = _sossi_field(experiment, "thermal_schedule")
    setpoints = _sossi_field(thermal_schedule, "setpoints_and_holds", ()) or ()
    first_setpoint = setpoints[0] if setpoints else None
    temperature_K = _sossi_point(_sossi_field(first_setpoint, "temperature_K"))
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

    flow_interpretation = _sossi_table2_flow_interpretation(co_sccm, co2_sccm)
    if flow_interpretation is None:
        return refuse(
            "gas_equilibrium_fo2_disagrees_with_printed",
            "Table 2 does not provide a complete CO/CO2 cover-gas basis.",
        )
    method, expected_cover = flow_interpretation

    if not _sossi_cover_matches(experiment, expected_cover):
        return refuse(
            "cover_not_printed_mixture",
            f"{run_id} does not use the cover gas printed in Table 2.",
            method=method,
        )
    if method == "printed_air":
        if printed_log is None:
            return refuse(
                "gas_equilibrium_fo2_unavailable",
                "The printed air row has no run-log fO2 to use as an engine input.",
                method=method,
            )
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
            row_validated=True,
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

    if printed_log is None:
        return SossiGasEquilibriumCheck(
            experiment_id,
            run_id,
            method,
            None,
            computed_log,
            None,
            None,
            "gas_equilibrium_fo2_unavailable",
            "The Table 2 run-log fO2 is not printed.",
            True,
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
        row_validated=True,
    )


def resolve_sossi_run_oxygen(
    check: SossiGasEquilibriumCheck,
    *,
    recorded_log: Decimal | None,
    ladder_log: Decimal | None,
    ladder_route: str | None = None,
) -> SossiRunOxygenDecision:
    """Select this run's validated fO2 premise in the ratified source order."""
    unvalidated_row_routes = {
        "observation_fO2_log",
        "observation_fO2_Pa_to_log_fO2",
        "identity_fO2_Pa",
    }
    if not check.row_validated and ladder_route in unvalidated_row_routes:
        ladder_log = None
    gas_derived_log = None
    if check.row_validated and (
        check.refusal_reason is None
        or (
            check.refusal_reason == "gas_equilibrium_fo2_unavailable"
            and check.printed_log10_fO2_bar is None
        )
    ):
        gas_derived_log = check.computed_log10_fO2_bar
    candidates = (
        ("validated_table2_gas_equilibrium", check.checked_log10_fO2_bar),
        (
            "independently_published_table2_run_log",
            check.printed_log10_fO2_bar if check.row_validated else None,
        ),
        ("located_run_recorded_fO2", recorded_log),
        ("vetted_gas_derived_fO2", gas_derived_log),
        ("oxygen_ladder_premise", ladder_log),
    )
    for source, value in candidates:
        if value is not None and value.is_finite() and value <= 0:
            return SossiRunOxygenDecision(check.experiment_id, value, source)
    detail = check.refusal_detail or "no finite same-run oxygen premise is available"
    return SossiRunOxygenDecision(
        check.experiment_id,
        None,
        None,
        check.refusal_reason or "gas_equilibrium_fo2_unavailable",
        detail,
    )


def sossi_table2_gas_boundary(
    experiment: Any,
    observation: Any = None,
    *,
    expected_run_id: str | None = None,
) -> Mapping[str, Any] | None:
    """Resolve one RPS gas boundary from its own validated Table 2 row."""
    try:
        run_id = _sossi_experiment_run_id(experiment)
    except ValueError:
        return None
    if expected_run_id is not None and expected_run_id != run_id:
        return None
    experiment_id = str(_sossi_field(experiment, "experiment_id") or "")
    if observation is not None and getattr(observation, "source_id", None) != SOSSI_SOURCE_ID:
        observation = None

    temperature = None
    point_conditions = _sossi_field(observation, "point_conditions", {}) or {}
    if isinstance(point_conditions, Mapping):
        temperature = point_conditions.get("temperature_K")
    row_locator = _sossi_field(temperature, "locator")
    if not _sossi_locator_has_quote(row_locator):
        row_locator = _sossi_field(observation, "locator")
    if not _sossi_locator_has_quote(row_locator):
        row_locator = _sossi_field(experiment, "locator")
    page = _sossi_field(row_locator, "page")
    table = _sossi_field(row_locator, "table")
    note = _sossi_field(row_locator, "note", "") or ""
    quote_marker = "quote="
    quote = note.split(quote_marker, 1)[1] if quote_marker in note else None
    reason = None
    if observation is not None and getattr(observation, "experiment_id", None) != experiment_id:
        reason = "sossi_table2_gas_row_experiment_mismatch"
        quote = None
    elif not _sossi_locator_has_quote(row_locator):
        reason = "sossi_table2_gas_record_not_located"
        quote = None
    elif quote is None:
        reason = "sossi_table2_gas_quote_missing"

    co_sccm = co2_sccm = None
    printed_log = None
    quote_validated = False
    if quote is not None and reason is None:
        temperature_K = _sossi_evidence_decimal(temperature)
        fO2_log = point_conditions.get("fO2_log") if isinstance(point_conditions, Mapping) else None
        structured_log = _sossi_evidence_decimal(fO2_log)
        row = {
            "run_id": run_id,
            "T_C": (
                temperature_K - Decimal(str(CELSIUS_TO_KELVIN_OFFSET))
                if temperature_K is not None
                else None
            ),
            "log10_fO2": structured_log,
            "locator": {"table": table, "page": page, "quote": quote},
        }
        try:
            _parsed_run, quoted_temperature_C, co_sccm, co2_sccm, printed_log = (
                _sossi_table2_gas_row(row)
            )
            quote_validated = True
        except (TypeError, ValueError) as exc:
            reason = str(exc)

    pressure_environment = _sossi_field(experiment, "pressure_environment")
    total_pressure = _sossi_point(
        _sossi_field(pressure_environment, "total_pressure_Pa")
    )
    if quote_validated:
        flow_interpretation = _sossi_table2_flow_interpretation(co_sccm, co2_sccm)
        if flow_interpretation is None:
            reason = "sossi_table2_gas_cover_incomplete"
            quote_validated = False
        elif not _sossi_cover_matches(experiment, flow_interpretation[1]):
            reason = "cover_not_printed_mixture"
            quote_validated = False
        elif (
            total_pressure is not None
            and total_pressure != Decimal(str(STANDARD_ATMOSPHERE_PA))
        ):
            reason = "sossi_table2_pressure_conflicts_with_one_atmosphere_citation"
            quote_validated = False
        else:
            schedule = _sossi_field(experiment, "thermal_schedule")
            setpoints = _sossi_field(schedule, "setpoints_and_holds", ()) or ()
            run_temperature = _sossi_evidence_decimal(
                _sossi_field(setpoints[0], "temperature_K") if setpoints else None
            )
            quoted_temperature = quoted_temperature_C + Decimal(
                str(CELSIUS_TO_KELVIN_OFFSET)
            )
            if run_temperature is not None and run_temperature != quoted_temperature:
                reason = "sossi_table2_temperature_conflicts_with_run"
                quote_validated = False

    citation_id = getattr(observation, "source_id", None) or SOSSI_SOURCE_ID
    quote_digest = sha256((quote or "").encode("utf-8")).hexdigest()
    row_source = {
        "source_class": "literature_primary",
        "citation_id": citation_id,
        "source_ref": (
            f"PDF page {page}, Table 2, run {run_id}"
            if page in {54, 55} and table == "2"
            else f"PDF Table 2 run {run_id}; gas quote not located"
        ),
    }
    status = reason or "sossi_table2_gas_record_not_available"
    not_reported = {
        **row_source,
        "reported_status": "not_reported",
        "digest": quote_digest,
    }
    if quote_validated and co_sccm is None and co2_sccm is None:
        background_gas = {**row_source, "species": "air"}
        imposed_flow_reason = (
            "The source leaves both CO and CO2 flow columns blank for this run."
        )
    elif quote_validated:
        background_gas = {
            **not_reported,
            "reason": "The source reports separate CO and CO2 component flows.",
        }
        imposed_flow_reason = "The source reports component flows separately."
    else:
        background_gas = {**not_reported, "reason": status}
        imposed_flow_reason = status

    pressure_method_implied = total_pressure is None
    boundary: dict[str, Any] = {
        "background_gas": background_gas,
        "imposed_flow": {**not_reported, "reason": imposed_flow_reason},
        "pressure_control": {
            "mode": "controlled",
            "pressure_driver": "imposed",
            **({"pressure_basis": "DP-002 method_implied"} if pressure_method_implied else {}),
            "source_class": "literature_primary",
            "citation_id": citation_id,
            "source_ref": "PDF page 2: one-atmosphere gas-mixing furnace",
        },
    }
    component_flows = {
        species: {**row_source, "value": value, "unit": "sccm"}
        for species, value in (("CO", co_sccm), ("CO2", co2_sccm))
        if quote_validated and value is not None
    }
    if component_flows:
        boundary["component_flows"] = component_flows
        boundary["flow_reference_state"] = {
            **not_reported,
            "reason": NoticeKind.SCCM_REFERENCE_STATE_UNREPORTED.value,
            "conversion_role": "diagnostic_only",
            "conversion_note": (
                "Total pressure is imposed and fO2 uses the CO/CO2 ratio, "
                "so standard-state conversion is not load-bearing."
            ),
        }
        approximate_total_flow = _sossi_table2_approximate_total_flow_sccm(
            run_id, co_sccm, co2_sccm
        )
        if approximate_total_flow is not None:
            boundary["reported_total_flow"] = {
                **row_source,
                "value": approximate_total_flow,
                "unit": "sccm",
                "qualifier": "approximate",
                "source_ref": (
                    "PDF page 6, section 2.1: approximately 200 sccm; "
                    "air and ANU runs excepted"
                ),
            }
    return boundary


def _sossi_locator_has_quote(locator: Any) -> bool:
    page = _sossi_field(locator, "page")
    table = _sossi_field(locator, "table")
    note = _sossi_field(locator, "note", "") or ""
    return page in {54, 55} and table == "2" and "quote=" in note
