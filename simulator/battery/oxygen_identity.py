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
from dataclasses import replace
from decimal import Decimal, InvalidOperation
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
from simulator.physical_constants import BAR_PA_DECIMAL
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
    # A source quote establishes the dry-air rung only when it places the
    # sample in air during equilibration. Air displaced before the run and an
    # air quench after an inert run are not atmosphere evidence for DP-003.
    # "air-free" normalizes to "air free". The sample's atmosphere is the
    # word after "in air", so a following "free" is not air. A furnace
    # sensor reading air outside the chamber is not the sample either.
    equilibration_air = re.search(
        r"\bequilibrat\w*(?:\s+\w+){0,5}\s+(?:in|under)\s+(?:ambient\s+)?air\b"
        r"(?!\s+free\b)",
        normalized_quote,
    )
    air_during_equilibration = re.search(
        r"\bair\b(?!\s+free\b).{0,60}\b(?:during|throughout)\s+equilibrat\w*",
        normalized_quote,
    )
    sensor_outside_chamber = (
        air_during_equilibration is not None
        and re.search(
            r"\b(?:sensor|outside)\b",
            air_during_equilibration.group(0),
        )
        is not None
    )
    if (
        equilibration_air is not None
        and not re.search(
            r"\b(?:argon|quenched|quench|displaced|replaced|before|after|then)\b",
            equilibration_air.group(0),
        )
    ) or (
        air_during_equilibration is not None and not sensor_outside_chamber
    ):
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
