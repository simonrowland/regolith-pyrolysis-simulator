#!/usr/bin/env python3
"""Rank empirical battery predictions by residual or grouped RMS."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import re
import sys
import tarfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, Iterator

ROOT = Path(__file__).resolve().parents[2]

from simulator.accounting import UnknownSpeciesError, resolve_species_formula
from simulator.battery.enums import MetricOperation, NoticeKind
from simulator.battery.identity import PA_PER_BAR_DEC
from simulator.config import load_config_bundle
from simulator.battery.score import (
    _engine_from_payload,
    _flagged_payload_strata,
    _median_rms_from_float64,
    _reference_from_metric,
    is_store_stamp_payload,
)

TEMPERATURE_MARGIN_K = 100.0


def _state_value(value: object) -> object | None:
    if isinstance(value, Mapping):
        if value.get("tag") != "value":
            return None
        return value.get("value")
    return value


@contextmanager
def _open_residual_jsonl(path: Path) -> Iterator[BinaryIO]:
    """Open residuals directly, or stream the residual member from a battery archive."""

    if path.name.endswith(".tgz"):
        with tarfile.open(path, "r:gz") as archive:
            try:
                member = archive.getmember("data/battery/residuals.jsonl")
            except KeyError as exc:
                raise ValueError(f"{path}: missing data/battery/residuals.jsonl") from exc
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{path}: cannot read data/battery/residuals.jsonl")
            try:
                yield source
            finally:
                source.close()
    elif path.suffix == ".gz":
        with gzip.open(path, "rb") as source:
            yield source
    else:
        with path.open("rb") as source:
            yield source


def _row_identity(row: Mapping[str, Any]) -> Mapping[str, Any]:
    observation = row.get("candidate_observation")
    if isinstance(observation, Mapping):
        identity = observation.get("identity")
        if isinstance(identity, Mapping):
            return identity
    return {}


def _species_formula(identity: Mapping[str, Any]) -> str:
    species_value = identity.get("species")
    species = (
        _state_value(species_value)
        if isinstance(species_value, Mapping) and "tag" in species_value
        else species_value
    )
    if isinstance(species, Mapping):
        return str(species.get("formula") or "")
    return ""


def _is_d139_uncalibrated_kems_row(row: Mapping[str, Any]) -> bool:
    numeric = row.get("numeric")
    if not isinstance(numeric, Mapping) or numeric.get("decision_band") is not None:
        return False
    if _state_value(_row_identity(row).get("quantity")) != "p_partial":
        return False
    candidate = row.get("candidate_observation")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    notice_sources = (row.get("notices"), candidate.get("notices"))
    return any(
        isinstance(notice, Mapping)
        and notice.get("kind") == NoticeKind.UNVERIFIED_APPARATUS.value
        and isinstance(notice.get("reason"), str)
        and notice["reason"].startswith("calibration_not_grounded:")
        for source in notice_sources
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes))
        for notice in source
    )


def _row_formula(row: Mapping[str, Any]) -> str:
    formula = _species_formula(_row_identity(row))
    if formula:
        return formula
    refusal = row.get("refusal")
    detail = refusal.get("detail") if isinstance(refusal, Mapping) else None
    if isinstance(detail, Mapping) and detail.get("formula"):
        return str(detail["formula"])
    request = row.get("candidate_request")
    if isinstance(request, Mapping):
        species = request.get("species")
        if isinstance(species, Mapping) and species.get("formula"):
            return str(species["formula"])
        if request.get("formula"):
            return str(request["formula"])
    return ""


def _engine_name(row: Mapping[str, Any]) -> str | None:
    request = row.get("candidate_request")
    if isinstance(request, Mapping) and request.get("engine"):
        return str(request["engine"])
    observation = row.get("candidate_observation")
    if isinstance(observation, Mapping):
        engine = observation.get("engine")
        if isinstance(engine, Mapping) and engine.get("name"):
            return str(engine["name"])
    return None


def _row_engine(row: Mapping[str, Any]) -> str:
    engine = _engine_from_payload(row)
    if engine == "unknown":
        return _engine_name(row) or "unknown"
    return engine


def _is_refused_row(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "").casefold() == "refused"


def _with_refusal_attribution(
    rows: Iterator[Mapping[str, Any]],
    refused_by_engine_rail: dict[tuple[str, str], int],
    unattributed_refusals: dict[tuple[str, str], int],
    *,
    rail_filter: str | None,
    species_filter: str | None,
) -> Iterator[Mapping[str, Any]]:
    for payload in rows:
        if not isinstance(payload, Mapping) or is_store_stamp_payload(payload):
            continue
        if _is_refused_row(payload):
            engine = _row_engine(payload)
            rail = str(payload.get("rail") or "").strip()
            species = _row_formula(payload)
            key = (engine, rail or "unknown")
            refused_by_engine_rail[key] = refused_by_engine_rail.get(key, 0) + 1
            if (rail_filter is not None and not rail) or (
                species_filter is not None and not species
            ):
                unattributed_refusals[key] = unattributed_refusals.get(key, 0) + 1
        yield payload


def _hero_decimal(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError, ArithmeticError):
        return None
    return value if value.is_finite() else None


def _hero_metric_temperature(row: Mapping[str, object]) -> Decimal | None:
    # Temperature grouping keys on the measured residual field, not the request:
    # candidate_observation.identity.temperature_K.
    candidate = row.get("candidate_observation")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    identity = candidate.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    temperature = _state_value(identity.get("temperature_K"))
    return _hero_decimal(temperature)


def _number(raw: object) -> float | None:
    if isinstance(raw, Mapping):
        if raw.get("tag") != "value":
            return None
        raw = raw.get("value")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _mapping(raw: object) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def _sequence(raw: object) -> Sequence[Any]:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return raw
    return ()


def _report_rows(report: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = report.get("per_hour_summary")
    if isinstance(rows, Mapping):
        rows = rows.get("per_hour_summary")
    return tuple(row for row in _sequence(rows) if isinstance(row, Mapping))


def _formula_aliases(report: Mapping[str, Any]) -> dict[str, str]:
    instrumentation = _mapping(report.get("vapour_rail_instrumentation"))
    batch = _mapping(_mapping(instrumentation.get("vapour_batch")))
    channels = _mapping(batch.get("channels_by_species"))
    aliases: dict[str, str] = {}
    for species, raw in channels.items():
        formula_id = _mapping(raw).get("formula_id")
        if isinstance(formula_id, str) and formula_id.strip():
            aliases[str(species)] = formula_id.strip()
    return aliases


def _formula_info(
    species: object, aliases: Mapping[str, str]
) -> tuple[str, tuple[tuple[str, float], ...] | None, dict[str, float], float | None]:
    name = str(species)
    candidates = [aliases.get(name), name]
    if name.endswith("_gas"):
        candidates.append(name[: -len("_gas")])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            formula = resolve_species_formula(candidate)
        except (UnknownSpeciesError, ValueError):
            continue
        elements = {
            str(element): float(amount)
            for element, amount in formula.elements.items()
        }
        signature = tuple(sorted(elements.items()))
        return (
            str(candidate),
            signature,
            elements,
            float(formula.molar_mass_kg_per_mol()),
        )
    return name, None, {}, None


def _engine_roles(report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = defaultdict(set)

    def add(role: str | None, raw: object) -> None:
        if role is None:
            return
        if isinstance(raw, str):
            token = raw.strip()
            if token and token.casefold() not in {"none", "unknown"}:
                roles[role].add(token)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for item in raw:
                add(role, item)
        elif isinstance(raw, Mapping):
            for key in ("provider", "engine", "backend", "authoritative"):
                if key in raw:
                    add(role, raw[key])

    def role_for(name: object) -> str | None:
        token = re.sub(r"[^a-z0-9]+", "_", str(name).casefold())
        if "redox" in token or "fo2" in token or "fugacity" in token:
            return "redox"
        if "activity" in token:
            return "melt_activity"
        if "vapor" in token or "vapour" in token:
            return "vapour"
        if "backend" in token or "equilibrium" in token:
            return "phase_equilibrium"
        return None

    def add_activity_sources(raw: object) -> None:
        if isinstance(raw, Mapping):
            for role_name, provider in raw.items():
                add(role_for(role_name), provider)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for item in raw:
                item = _mapping(item)
                add(role_for(item.get("role") or item.get("intent")), item)

    def add_provider_fields(raw: object, role: str) -> None:
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                if "provider" in str(key).casefold() or key in {"engine", "backend"}:
                    add(role, value)
                else:
                    add_provider_fields(value, role)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            for value in raw:
                add_provider_fields(value, role)

    metadata = _mapping(report.get("run_metadata"))
    for source in (report, metadata):
        add_activity_sources(source.get("activity_sources"))
    for backend in (metadata.get("backend"), report.get("backend")):
        add("phase_equilibrium", backend)

    engines_used = _mapping(metadata.get("engines_used"))
    active = _mapping(engines_used.get("active"))
    for intent, provider in active.items():
        add(role_for(intent), provider)
    registry = _mapping(engines_used.get("registry"))
    for intent, raw in registry.items():
        entry = _mapping(raw)
        if role_for(intent) and entry.get("authoritative"):
            add(role_for(intent), entry.get("authoritative"))

    for row in rows:
        add_activity_sources(row.get("activity_sources"))
        high_t = _mapping(row.get("high_t_melt_activity"))
        # The selected high-T melt activity also supplies the vapour calculation.
        add("melt_activity", high_t.get("provider"))
        add("melt_activity", high_t.get("selected_parent_activities_provider"))
        add("vapour", high_t.get("provider"))
        add("vapour", high_t.get("selected_parent_activities_provider"))
        add_provider_fields(row.get("redox_source_breakdown"), "redox")

    instrumentation = _mapping(report.get("vapour_rail_instrumentation"))
    batch = _mapping(_mapping(instrumentation.get("vapour_batch")))
    add_provider_fields(batch.get("channels_by_species"), "vapour")
    add_provider_fields(instrumentation.get("melt_activity_shadow"), "melt_activity")
    return roles


def _iter_species_names(value: object) -> Sequence[str]:
    if isinstance(value, Mapping):
        return tuple(str(name) for name in value)
    return ()


def _positive_species_names(value: object) -> Sequence[str]:
    if not isinstance(value, Mapping):
        return ()
    return tuple(
        str(name)
        for name, amount in value.items()
        if (_number(amount) or 0.0) > 0.0
    )


def _build_envelope(report: Mapping[str, Any]) -> dict[str, Any]:
    rows = _report_rows(report)
    aliases = _formula_aliases(report)
    temperatures = [
        value + 273.15
        for row in rows
        if (value := _number(row.get("T_C"))) is not None
    ]
    pO2_values = [
        value
        for row in rows
        if (value := _number(row.get("pO2_bar"))) is not None
        and value >= 0.0
    ]
    if not temperatures:
        raise ValueError("hero report has no numeric per-hour T_C values")

    vapor_species: set[str] = set()
    captured_species: set[str] = set()
    activity_species: set[str] = set()
    evaporated_mol_by_signature: dict[tuple[tuple[str, float], ...], float] = defaultdict(float)
    evaporated_atoms: dict[str, float] = defaultdict(float)
    previous_hour = 0.0
    for index, row in enumerate(rows, start=1):
        hour = _number(row.get("hour"))
        if hour is None:
            hour = float(index)
        duration_h = max(0.0, hour - previous_hour)
        previous_hour = max(previous_hour, hour)
        rates = _mapping(row.get("vapor_species_kg_hr"))
        for raw_species, raw_rate in rates.items():
            rate = _number(raw_rate)
            if rate is None or rate <= 0.0:
                continue
            name, signature, elements, molar_mass = _formula_info(raw_species, aliases)
            vapor_species.add(name)
            if signature is None or not molar_mass or duration_h <= 0.0:
                continue
            moles = rate * duration_h / molar_mass
            evaporated_mol_by_signature[signature] += moles
            for element, count in elements.items():
                evaporated_atoms[element] += moles * count

        for key in ("condensation_train_kg_cumulative", "condensation_train_kg"):
            for raw_species in _positive_species_names(row.get(key)):
                captured_species.add(_formula_info(raw_species, aliases)[0])

        high_t = _mapping(row.get("high_t_melt_activity"))
        for key in (
            "selected_parent_activities",
            "activities_by_oxide",
            "openimcc_activities_by_oxide",
        ):
            for raw_species in _iter_species_names(high_t.get(key)):
                activity_species.add(_formula_info(raw_species, aliases)[0])

    final = _mapping(report.get("final"))
    for key in ("wall_deposit_by_species_kg", "deposit_by_surface_species_kg"):
        for raw_species in _positive_species_names(final.get(key)):
            captured_species.add(_formula_info(raw_species, aliases)[0])
    classification = _mapping(_mapping(report.get("product_classification")).get("classification"))
    metals = _mapping(_mapping(classification.get("metals_plus_O2")).get("metals_kg"))
    captured_species.update(
        _formula_info(name, aliases)[0] for name in _positive_species_names(metals)
    )

    evolved_elements = {
        element
        for name in vapor_species | captured_species
        for element, amount in _formula_info(name, aliases)[2].items()
        if element != "O" and amount > 0.0
    }
    activity_species = {
        name
        for name in activity_species
        if evolved_elements.intersection(_formula_info(name, aliases)[2])
    }
    vapor_species.update(captured_species)

    roles = _engine_roles(report, rows)
    role_json = {role: sorted(values) for role, values in sorted(roles.items())}
    metadata = _mapping(report.get("run_metadata"))
    feedstock_class = None
    feedstock_class_source = None
    feedstock = _mapping(report.get("feedstock"))
    for source, prefix in ((metadata, "run_metadata"), (feedstock, "feedstock"), (report, "report")):
        for key in ("feedstock_composition_class", "composition_class", "feedstock_class"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                feedstock_class = value.strip()
                feedstock_class_source = f"{prefix}.{key}"
                break
        if feedstock_class:
            break
    if feedstock_class is None:
        feedstock_id = metadata.get("feedstock_id") or report.get("feedstock_id")
        if feedstock_id:
            feedstock_id = str(feedstock_id)
            feedstock_id_source = (
                "run_metadata.feedstock_id"
                if metadata.get("feedstock_id")
                else "report.feedstock_id"
            )
            catalog_entry = _mapping(
                load_config_bundle(ROOT / "data").feedstocks.get(feedstock_id)
            )
            label = catalog_entry.get("label")
            if isinstance(label, str) and label.strip():
                feedstock_class = label.strip()
                feedstock_class_source = f"data/feedstocks.yaml[{feedstock_id}].label"
            else:
                feedstock_class = feedstock_id
                feedstock_class_source = feedstock_id_source
        else:
            feedstock_class = "unknown"
            feedstock_class_source = "unreported"

    species_by_rail = {
        "melt_activity": sorted(activity_species),
        "vapour": sorted(vapor_species),
    }
    names = vapor_species | activity_species
    evaporated_mol_by_species = {}
    for name in sorted(names):
        _canonical, signature, elements, _mass = _formula_info(name, aliases)
        amount = evaporated_mol_by_signature.get(signature, 0.0) if signature else 0.0
        if amount == 0.0:
            cations = {element: count for element, count in elements.items() if element != "O"}
            if cations:
                amount = min(
                    evaporated_atoms.get(element, 0.0) / count
                    for element, count in cations.items()
                )
        evaporated_mol_by_species[name] = amount

    return {
        "feedstock_composition_class": feedstock_class,
        "feedstock_class_source": feedstock_class_source,
        "backend": metadata.get("backend") or report.get("backend"),
        "engines_by_role": role_json,
        "temperature_K": {
            "min": min(temperatures),
            "max": max(temperatures),
            "margin_K": TEMPERATURE_MARGIN_K,
        },
        "pO2_bar": {
            "min": min(pO2_values) if pO2_values else None,
            "max": max(pO2_values) if pO2_values else None,
        },
        "species_by_rail": species_by_rail,
        "evaporated_mol_by_species": evaporated_mol_by_species,
        "evaporated_mol_method": (
            "vapor_species_kg_hr rates integrated over report-hour intervals and "
            "converted with simulator.accounting.formulas; melt-activity species "
            "use non-oxygen formula-unit equivalents"
        ),
    }


def _role_engines_for_rail(rail: str, roles: Mapping[str, set[str]]) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", rail.casefold())
    selected = set(roles.get("phase_equilibrium", set()))
    matched_role = False
    if "activity" in normalized:
        selected.update(roles.get("melt_activity", set()))
        matched_role = True
    if any(token in normalized for token in ("vapour", "vapor", "pressure", "p_partial")):
        selected.update(roles.get("vapour", set()))
        selected.update(roles.get("melt_activity", set()))
        matched_role = True
    if any(token in normalized for token in ("redox", "fo2", "fugacity")):
        selected.update(roles.get("redox", set()))
        matched_role = True
    if not matched_role:
        for values in roles.values():
            selected.update(values)
    return {engine.casefold() for engine in selected}


def _species_signatures(names: Sequence[str], aliases: Mapping[str, str]) -> tuple[set[tuple[tuple[str, float], ...]], set[str]]:
    signatures: set[tuple[tuple[str, float], ...]] = set()
    raw_names: set[str] = set()
    for name in names:
        canonical, signature, _elements, _mass = _formula_info(name, aliases)
        raw_names.add(canonical.casefold())
        if signature is not None:
            signatures.add(signature)
    return signatures, raw_names


def _row_inside_envelope(
    row: Mapping[str, Any],
    rail: str,
    species: str,
    engine: str,
    envelope: Mapping[str, Any],
    roles: Mapping[str, set[str]],
    aliases: Mapping[str, str],
) -> bool:
    temperature = _hero_metric_temperature(row)
    if temperature is None:
        return False
    t_bounds = _mapping(envelope.get("temperature_K"))
    t_min = float(t_bounds["min"]) - float(t_bounds["margin_K"])
    t_max = float(t_bounds["max"]) + float(t_bounds["margin_K"])
    if not t_min <= float(temperature) <= t_max:
        return False

    p_bounds = _mapping(envelope.get("pO2_bar"))
    p_min = _number(p_bounds.get("min"))
    p_max = _number(p_bounds.get("max"))
    identity = _mapping(_mapping(row.get("candidate_observation")).get("identity"))
    if p_min is not None and p_max is not None and "fO2_Pa" in identity:
        fO2_pa = _number(identity.get("fO2_Pa"))
        if fO2_pa is not None:
            pO2_bar = fO2_pa / float(PA_PER_BAR_DEC)
            if not p_min <= pO2_bar <= p_max:
                return False

    species_by_rail = _mapping(envelope.get("species_by_rail"))
    normalized_rail = rail.casefold()
    if "activity" in normalized_rail:
        expected_species = _sequence(species_by_rail.get("melt_activity"))
    elif any(token in normalized_rail for token in ("vapour", "vapor", "pressure", "p_partial")):
        expected_species = _sequence(species_by_rail.get("vapour"))
    else:
        expected_species = tuple(
            name
            for values in species_by_rail.values()
            for name in _sequence(values)
        )
    expected_signatures, expected_names = _species_signatures(expected_species, aliases)
    row_name, row_signature, _elements, _mass = _formula_info(species, aliases)
    if row_name.casefold() not in expected_names and (
        row_signature is None or row_signature not in expected_signatures
    ):
        return False

    allowed_engines = _role_engines_for_rail(rail, roles)
    return engine.casefold() in allowed_engines


def _observation_id(row: Mapping[str, Any]) -> str:
    reference = row.get("reference")
    if reference:
        return str(reference)
    observation = _mapping(row.get("candidate_observation"))
    for key in ("observation_id", "experiment_id", "id"):
        if observation.get(key):
            return str(observation[key])
    request = _mapping(row.get("candidate_request"))
    return str(request.get("observation_id") or request.get("experiment_id") or "")


def _iter_archive_rows(archive: Path) -> Iterator[Mapping[str, Any]]:
    with _open_residual_jsonl(archive) as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{archive}:{line_number}: invalid JSON") from exc
            if isinstance(payload, Mapping) and not is_store_stamp_payload(payload):
                yield payload


def _feedstock_oxide_elements(feedstock: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    composition = _mapping(feedstock.get("composition_wt_pct"))
    oxide_names: set[str] = set()
    elements: set[str] = set()
    for raw_name, raw_amount in composition.items():
        if (_number(raw_amount) or 0.0) <= 0.0:
            continue
        name = str(raw_name)
        _canonical, _signature, formula_elements, _mass = _formula_info(name, {})
        cations = set(formula_elements) - {"O"}
        if "O" in formula_elements and cations:
            oxide_names.add(name.casefold())
            elements.update(cations)
    return oxide_names, elements


def _matches_feedstock_species(species: str, oxide_names: set[str], elements: set[str]) -> bool:
    if species.casefold() in oxide_names:
        return True
    _canonical, _signature, formula_elements, _mass = _formula_info(species, {})
    cations = set(formula_elements) - {"O"}
    if not cations:
        return "O" in formula_elements and bool(elements)
    return cations <= elements


def _explicit_temperature_filter(
    tmin_K: float | None, tmax_K: float | None
) -> dict[str, Any] | None:
    if tmin_K is None and tmax_K is None:
        return None
    low, high = _number(tmin_K), _number(tmax_K)
    if tmin_K is not None and low is None:
        raise ValueError("tmin_K must be a finite number")
    if tmax_K is not None and high is None:
        raise ValueError("tmax_K must be a finite number")
    if low is not None and high is not None and low > high:
        raise ValueError("tmin_K must be less than or equal to tmax_K")
    return {"min": low, "max": high, "source": "explicit CLI bounds"}


def _run_temperature_filter(envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    bounds = _mapping(envelope.get("temperature_K"))
    low, high = _number(bounds.get("min")), _number(bounds.get("max"))
    margin = _number(bounds.get("margin_K")) or 0.0
    if low is None or high is None:
        return None
    return {
        "min": low - margin,
        "max": high + margin,
        "source": f"run report per_hour_summary.T_C with ±{_display(margin)} K margin",
    }


def _decimal_point(raw: object) -> Decimal | None:
    value = _state_value(raw) if isinstance(raw, Mapping) and "tag" in raw else raw
    if isinstance(value, Mapping):
        if value.get("kind") == "point":
            value = value.get("point")
        elif "point" in value:
            value = value.get("point")
        elif "value" in value:
            value = value.get("value")
    return _hero_decimal(value)


def _metric_unit(numeric: Mapping[str, Any]) -> tuple[str, str]:
    operation = str(numeric.get("operation") or "")
    raw_unit = str(numeric.get("unit") or "unknown")
    if operation == MetricOperation.DEX.value:
        return operation, "dex"
    if operation:
        return operation, f"{raw_unit} ({operation})"
    return operation, raw_unit


def _row_flags(
    row: Mapping[str, Any], *, d139_uncalibrated_kems: bool
) -> list[str]:
    def canonical(value: object) -> str:
        return str(value).strip().casefold().replace("-", "_")

    candidate = _mapping(row.get("candidate_observation"))
    flags = {
        canonical(notice.get("kind"))
        for source in (row.get("notices"), candidate.get("notices"))
        for notice in _sequence(source)
        if isinstance(notice, Mapping) and notice.get("kind")
    }
    flags.update(canonical(flag) for flag in _flagged_payload_strata(row))
    flags.discard("")
    if d139_uncalibrated_kems and not flags:
        flags.add("flagged")
    return sorted(flags)


def _engine_filter_values(engine: str | Sequence[str] | None) -> set[str] | None:
    if engine is None:
        return None
    values = (engine,) if isinstance(engine, str) else engine
    normalized = {str(value).strip().casefold() for value in values if str(value).strip()}
    return normalized


def leaderboard(
    rows: Sequence[Mapping[str, Any]] | Iterator[Mapping[str, Any]],
    *,
    feedstock: str = "lunar_mare_low_ti",
    engine: str | Sequence[str] | None = None,
    level: str = "row",
    order: str = "worst",
    limit: int = 10,
    offset: int = 0,
    rail: str | None = None,
    species: str | None = None,
    unit: str | None = None,
    run: Mapping[str, Any] | None = None,
    tmin_K: float | None = None,
    tmax_K: float | None = None,
) -> dict[str, Any]:
    """Rank priced residuals by prediction or by rail/species/engine/unit group."""

    if level not in {"row", "group"}:
        raise ValueError("level must be 'row' or 'group'")
    if order not in {"worst", "best"}:
        raise ValueError("order must be 'worst' or 'best'")
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be non-negative")

    config = load_config_bundle(ROOT / "data")
    feedstock_id = str(feedstock).strip()
    selected_feedstock: Mapping[str, Any] = {}
    if feedstock_id != "all":
        raw_feedstock = config.feedstocks.get(feedstock_id)
        if not isinstance(raw_feedstock, Mapping):
            raise ValueError(f"unknown feedstock id: {feedstock_id}")
        selected_feedstock = raw_feedstock
    oxide_names, oxide_elements = _feedstock_oxide_elements(selected_feedstock)

    envelope = _build_envelope(run) if run is not None else None
    aliases = _formula_aliases(run) if run is not None else {}
    roles = _engine_roles(run, _report_rows(run)) if run is not None else {}
    temperature_filter = (
        _run_temperature_filter(envelope) if envelope is not None else None
    )
    explicit_temperature_filter = _explicit_temperature_filter(tmin_K, tmax_K)
    if explicit_temperature_filter is not None:
        if temperature_filter is None:
            temperature_filter = explicit_temperature_filter
        else:
            run_min, run_max = temperature_filter["min"], temperature_filter["max"]
            explicit_min = explicit_temperature_filter["min"]
            explicit_max = explicit_temperature_filter["max"]
            temperature_filter = {
                "min": max(run_min, explicit_min)
                if explicit_min is not None
                else run_min,
                "max": min(run_max, explicit_max)
                if explicit_max is not None
                else run_max,
                "source": f"{temperature_filter['source']}; explicit CLI bounds",
            }
            if temperature_filter["min"] > temperature_filter["max"]:
                raise ValueError("explicit temperature bounds do not overlap the run")
    engine_values = _engine_filter_values(engine)
    engine_json = None if engine_values is None else sorted(engine_values)

    scored_rows: list[dict[str, Any]] = []
    refused_count = 0
    for payload in rows:
        if not isinstance(payload, Mapping) or is_store_stamp_payload(payload):
            continue
        row = payload
        if _is_refused_row(row):
            refused_count += 1
            continue
        row_rail = str(row.get("rail") or "")
        row_species = _row_formula(row)
        row_engine = _row_engine(row)
        if not row_rail or not row_species:
            continue
        if feedstock_id != "all" and not _matches_feedstock_species(
            row_species, oxide_names, oxide_elements
        ):
            continue
        if engine_values is not None and row_engine.casefold() not in engine_values:
            continue
        if rail is not None and row_rail.casefold() != rail.casefold():
            continue
        if species is not None and row_species.casefold() != species.casefold():
            continue

        numeric = _mapping(row.get("numeric"))
        operation, unit_label = _metric_unit(numeric)
        if unit is not None and unit_label.casefold() != unit.casefold():
            continue
        temperature = _hero_metric_temperature(row)
        if envelope is not None:
            if not _row_inside_envelope(
                row, row_rail, row_species, row_engine, envelope, roles, aliases
            ):
                continue
        if temperature_filter is not None:
            if temperature is None:
                continue
            else:
                if (
                    temperature_filter["min"] is not None
                    and float(temperature) < temperature_filter["min"]
                ):
                    continue
                if (
                    temperature_filter["max"] is not None
                    and float(temperature) > temperature_filter["max"]
                ):
                    continue

        residual = _number(numeric.get("value"))
        residual_decimal = _hero_decimal(numeric.get("value"))
        if residual is None or residual_decimal is None:
            continue

        candidate = _mapping(row.get("candidate_observation"))
        predicted_decimal = _decimal_point(candidate.get("value"))
        try:
            operation_enum = MetricOperation(operation)
        except ValueError:
            operation_enum = None
        observed_decimal = (
            _reference_from_metric(operation_enum, predicted_decimal, residual_decimal)
            if operation_enum is not None and predicted_decimal is not None
            else None
        )
        observation_id = _observation_id(row)
        source = candidate.get("source_id") or row.get("source_id")
        if not source and "::" in observation_id:
            source = observation_id.split("::", 1)[0]
        if not source:
            source = "unknown"
        flags = _row_flags(
            row, d139_uncalibrated_kems=_is_d139_uncalibrated_kems_row(row)
        )
        scored_rows.append(
            {
                "rail": row_rail,
                "species": row_species,
                "engine": row_engine,
                "unit": unit_label,
                "residual": residual,
                "flagged": bool(flags),
                "observation_id": observation_id,
                "source": str(source),
                "T_K": None if temperature is None else float(temperature),
                "observed": None if observed_decimal is None else float(observed_decimal),
                "predicted": None if predicted_decimal is None else float(predicted_decimal),
                "flags": flags,
            }
        )

    if level == "row":
        units = sorted(
            {row["unit"] for row in scored_rows},
            key=lambda value: (value != "dex", str(value).casefold()),
        )
        ranked: list[dict[str, Any]] = []
        for unit_block in units:
            block = [row for row in scored_rows if row["unit"] == unit_block]
            block.sort(
                key=lambda row: (
                    abs(row["residual"]) if order == "best" else -abs(row["residual"]),
                    row["observation_id"],
                    row["source"],
                )
            )
            for rank, row in enumerate(block, start=1):
                ranked.append(
                    {
                        "rank": rank,
                        **{
                            key: row[key]
                            for key in (
                                "observation_id",
                                "source",
                                "rail",
                                "species",
                                "engine",
                                "T_K",
                                "observed",
                                "predicted",
                                "residual",
                                "unit",
                                "flags",
                            )
                        },
                    }
                )
    else:
        groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in scored_rows:
            key = (row["rail"], row["species"], row["engine"], row["unit"])
            group = groups.setdefault(
                key,
                {
                    "rail": row["rail"],
                    "species": row["species"],
                    "engine": row["engine"],
                    "unit": row["unit"],
                    "values": [],
                    "unflagged_values": [],
                    "worst": [],
                    "flagged_n": 0,
                },
            )
            group["values"].append(row["residual"])
            if row["flagged"]:
                group["flagged_n"] += 1
            else:
                group["unflagged_values"].append(row["residual"])
            if row["observation_id"]:
                group["worst"].append((abs(row["residual"]), row["observation_id"]))

        evaporated = _mapping(_mapping(envelope).get("evaporated_mol_by_species"))
        groups_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for group in groups.values():
            stats = _median_rms_from_float64(group["values"])
            median, rms = stats if stats is not None else (None, None)
            unflagged_stats = _median_rms_from_float64(group["unflagged_values"])
            _unflagged_median, unflagged_rms = (
                unflagged_stats if unflagged_stats is not None else (None, None)
            )
            worst_ids: list[str] = []
            seen_ids: set[str] = set()
            for _magnitude, observation_id in sorted(
                group["worst"], key=lambda item: (-item[0], item[1])
            ):
                if observation_id in seen_ids:
                    continue
                seen_ids.add(observation_id)
                worst_ids.append(observation_id)
                if len(worst_ids) == 3:
                    break
            values = group["values"]
            output = {
                "rail": group["rail"],
                "species": group["species"],
                "engine": group["engine"],
                "unit": group["unit"],
                "n": len(values),
                "flagged_n": group["flagged_n"],
                "rms": None if rms is None else float(rms),
                "rms_unflagged_only": (
                    None if unflagged_rms is None else float(unflagged_rms)
                ),
                "median": None if median is None else float(median),
                "bias": math.fsum(values) / len(values) if values else None,
                "hero_evaporated_mol": _number(evaporated.get(group["species"])),
                "worst_observation_ids": worst_ids,
            }
            groups_by_unit[group["unit"]].append(output)

        units = sorted(
            groups_by_unit,
            key=lambda value: (value != "dex", str(value).casefold()),
        )
        ranked = []
        for unit_block in units:
            block = groups_by_unit[unit_block]
            block.sort(
                key=lambda row: (
                    row["rms"] is None,
                    (row["rms"] or 0.0) if order == "best" else -(row["rms"] or 0.0),
                    row["rail"],
                    row["species"],
                    row["engine"],
                )
            )
            for rank, row in enumerate(block, start=1):
                ranked.append({"rank": rank, **row})

    filters = {
        "feedstock": feedstock_id,
        "engine": engine_json,
        "level": level,
        "order": order,
        "rail": rail,
        "species": species,
        "unit": unit,
        "temperature_K": temperature_filter,
        "run_envelope": envelope,
        "refused_count": refused_count,
        "rank_policy": (
            "within the selected unit"
            if unit is not None
            else "within unit blocks; dex first, then remaining units alphabetically"
        ),
    }
    return {
        "schema": "battery-leaderboard.v1",
        "filters": filters,
        "total": len(ranked),
        "limit": limit,
        "offset": offset,
        "rows": ranked[offset : offset + limit],
    }


def _display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _markdown(
    result: Mapping[str, Any],
    *,
    refused_by_engine_rail: Mapping[tuple[str, str], int] | None = None,
    unattributed_refusals: Mapping[tuple[str, str], int] | None = None,
) -> str:
    filters = _mapping(result.get("filters"))
    rows = _sequence(result.get("rows"))
    total, limit, offset = result.get("total", 0), result.get("limit", 0), result.get("offset", 0)
    lines = [
        "# Empirical battery leaderboard",
        "",
        f"Total ranked rows: {total}; page: offset {offset}, limit {limit}.",
        f"Refused rows excluded: {filters.get('refused_count', 0)}.",
        f"Feedstock: `{filters.get('feedstock')}`; engine: `{filters.get('engine') or 'all'}`; "
        f"level: `{filters.get('level')}`; order: `{filters.get('order')}`.",
    ]
    if refused_by_engine_rail is not None:
        refusal_counts = "; ".join(
            f"{engine}/{rail}={count}"
            for (engine, rail), count in sorted(refused_by_engine_rail.items())
        )
        lines.append(f"Refusals by engine/rail: {refusal_counts or 'none'}.")
        if filters.get("rail") is not None or filters.get("species") is not None:
            unattributed_counts = "; ".join(
                f"{engine}/{rail}={count}"
                for (engine, rail), count in sorted(
                    (unattributed_refusals or {}).items()
                )
            )
            lines.append(
                "Unattributed to selected rail/species filters: "
                f"{unattributed_counts or 'none'}."
            )
    temperature = _mapping(filters.get("temperature_K"))
    if temperature:
        low, high = temperature.get("min"), temperature.get("max")
        if low is not None and high is not None:
            bounds = f"{_display(low)}–{_display(high)} K"
        elif low is not None:
            bounds = f"≥{_display(low)} K"
        else:
            bounds = f"≤{_display(high)} K"
        lines.append(f"Temperature filter: {bounds} ({temperature.get('source')}).")
    else:
        lines.append("Temperature: not filtered.")
    envelope = _mapping(filters.get("run_envelope"))
    if envelope:
        pO2 = _mapping(envelope.get("pO2_bar"))
        pO2_text = (
            "unreported"
            if pO2.get("min") is None or pO2.get("max") is None
            else f"{_display(pO2.get('min'))}–{_display(pO2.get('max'))} bar"
        )
        engines = "; ".join(
            f"{role}: {', '.join(str(value) for value in values) or 'none'}"
            for role, values in _mapping(envelope.get("engines_by_role")).items()
        )
        species = "; ".join(
            f"{rail}: {', '.join(str(value) for value in values)}"
            for rail, values in _mapping(envelope.get("species_by_rail")).items()
        )
        lines.extend(
            [
                f"Run feedstock class: `{envelope.get('feedstock_composition_class')}` "
                f"(from `{envelope.get('feedstock_class_source')}`).",
                f"Run pO₂ range: {pO2_text}.",
                f"Run engines by role: {engines or 'unreported'}.",
                f"Run species by rail: {species or 'unreported'}.",
            ]
        )
    lines.extend(
        [
            f"Ranking policy: {filters.get('rank_policy')}; each row includes its unit.",
            "Flagged numeric predictions remain ranked and carry their flags; refusals never enter a rank.",
            "",
        ]
    )
    if not rows:
        lines.append("No ranked rows on this page.")
        return "\n".join(lines) + "\n"

    if filters.get("level") == "row":
        columns = [
            "rank",
            "observation_id",
            "source",
            "rail",
            "species",
            "engine",
            "T_K",
            "observed",
            "predicted",
            "residual",
            "unit",
            "flags",
        ]
    else:
        columns = [
            "rank",
            "rail",
            "species",
            "engine",
            "unit",
            "n",
            "flagged_n",
            "rms",
            "rms_unflagged_only",
            "median",
            "bias",
            "hero_evaporated_mol",
            "worst_observation_ids",
        ]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---:" if column in {"rank", "n", "flagged_n"} else "---" for column in columns) + " |"
    previous_unit: str | None = None
    for row in rows:
        unit_name = str(_mapping(row).get("unit") or "unknown")
        if unit_name != previous_unit:
            lines.extend([f"### Unit: `{unit_name}`", "", header, divider])
            previous_unit = unit_name
        cells: list[str] = []
        for column in columns:
            value = _mapping(row).get(column)
            if column == "flags":
                value = ", ".join(str(flag) for flag in value or [])
            elif column == "worst_observation_ids":
                value = ", ".join(str(item) for item in value or [])
            cells.append(_display(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _csv_text(result: Mapping[str, Any]) -> str:
    columns = (
        [
            "rank",
            "observation_id",
            "source",
            "rail",
            "species",
            "engine",
            "T_K",
            "observed",
            "predicted",
            "residual",
            "unit",
            "flags",
        ]
        if _mapping(result.get("filters")).get("level") == "row"
        else [
            "rank",
            "rail",
            "species",
            "engine",
            "unit",
            "n",
            "flagged_n",
            "rms",
            "rms_unflagged_only",
            "median",
            "bias",
            "hero_evaporated_mol",
            "worst_observation_ids",
        ]
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in _sequence(result.get("rows")):
        values = dict(_mapping(row))
        for column in ("flags", "worst_observation_ids"):
            if column in values:
                values[column] = json.dumps(values[column], ensure_ascii=False)
        writer.writerow({column: values.get(column) for column in columns})
    filters = _mapping(result.get("filters"))
    summary = (
        f"# total={result.get('total', 0)}; limit={result.get('limit', 0)}; "
        f"offset={result.get('offset', 0)}; refused_count={filters.get('refused_count', 0)}\n"
    )
    return summary + stream.getvalue()


def _json_text(result: Mapping[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="battery .tgz archive or residuals JSONL")
    parser.add_argument("--run", type=Path, help="optional runner report for its T/pO2/species envelope")
    parser.add_argument("--feedstock", default="lunar_mare_low_ti", help="feedstock id or 'all'")
    parser.add_argument("--engine", action="append", help="engine filter; repeat for multiple engines")
    parser.add_argument("--level", choices=("row", "group"), default="row")
    parser.add_argument("--order", choices=("worst", "best"), default="worst")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--rail")
    parser.add_argument("--species")
    parser.add_argument("--unit", help="rank only this unit")
    parser.add_argument("--tmin-K", type=float, help="minimum temperature in kelvin")
    parser.add_argument("--tmax-K", type=float, help="maximum temperature in kelvin")
    parser.add_argument("--format", choices=("md", "json", "csv"), default="md")
    parser.add_argument("--out", type=Path, help="write output to this path; markdown also writes a sibling JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run: Mapping[str, Any] | None = None
        if args.run is not None:
            loaded_run = json.loads(args.run.read_text(encoding="utf-8"))
            if not isinstance(loaded_run, Mapping):
                raise ValueError(f"{args.run}: run report must be an object")
            run = loaded_run
        refused_by_engine_rail: dict[tuple[str, str], int] = defaultdict(int)
        unattributed_refusals: dict[tuple[str, str], int] = defaultdict(int)
        result = leaderboard(
            _with_refusal_attribution(
                _iter_archive_rows(args.archive),
                refused_by_engine_rail,
                unattributed_refusals,
                rail_filter=args.rail,
                species_filter=args.species,
            ),
            feedstock=args.feedstock,
            engine=args.engine,
            level=args.level,
            order=args.order,
            limit=args.limit,
            offset=args.offset,
            rail=args.rail,
            species=args.species,
            unit=args.unit,
            run=run,
            tmin_K=args.tmin_K,
            tmax_K=args.tmax_K,
        )
        if args.format == "md":
            rendered = _markdown(
                result,
                refused_by_engine_rail=refused_by_engine_rail,
                unattributed_refusals=unattributed_refusals,
            )
        else:
            rendered = {"json": _json_text, "csv": _csv_text}[args.format](result)
        if args.out is None:
            sys.stdout.write(rendered)
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
            if args.format == "md":
                json_path = (
                    args.out.with_name(f"{args.out.stem}-data.json")
                    if args.out.suffix.casefold() == ".json"
                    else args.out.with_suffix(".json")
                )
                json_path.write_text(_json_text(result), encoding="utf-8")
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"battery leaderboard: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
