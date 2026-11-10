#!/usr/bin/env python3
"""Rank battery residuals that fall inside a runner report's hero envelope."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.battery_hero_plot import (
    _engine_name,
    _hero_metric_temperature,
    _hero_row_flagged,
    _is_d139_uncalibrated_kems_row,
    _open_residual_jsonl,
    _row_formula,
)
from simulator.accounting import UnknownSpeciesError, resolve_species_formula
from simulator.battery.identity import PA_PER_BAR_DEC
from simulator.config import load_config_bundle
from simulator.battery.score import (
    _engine_from_payload,
    _median_rms_from_float64,
    is_store_stamp_payload,
)

TEMPERATURE_MARGIN_K = 100.0


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


def _score_rows(
    archive: Path,
    envelope: Mapping[str, Any],
    report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    aliases = _formula_aliases(report)
    roles = _engine_roles(report, _report_rows(report))
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    with _open_residual_jsonl(archive) as source:
        saw_stamp = False
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{archive}:{line_number}: invalid JSON") from exc
            if not isinstance(payload, Mapping):
                continue
            if not saw_stamp:
                if not is_store_stamp_payload(payload):
                    raise ValueError(f"{archive}: line 1 is not a battery_store_stamp")
                saw_stamp = True
                continue
            if is_store_stamp_payload(payload):
                continue

            rail = str(payload.get("rail") or "")
            species = _row_formula(payload)
            engine = _engine_from_payload(payload)
            if engine == "unknown":
                engine = _engine_name(payload) or "unknown"
            if not rail or not species or not engine:
                continue
            if not _row_inside_envelope(payload, rail, species, engine, envelope, roles, aliases):
                continue

            numeric = _mapping(payload.get("numeric"))
            residual = _number(numeric.get("value"))
            status = str(payload.get("status") or "").casefold()
            refused = status == "refused"
            flagged = _hero_row_flagged(
                payload,
                d139_uncalibrated_kems=_is_d139_uncalibrated_kems_row(payload),
            )
            if not (refused or flagged or residual is not None):
                continue

            group = groups.setdefault(
                (rail, species, engine),
                {
                    "rail": rail,
                    "species": species,
                    "engine": engine,
                    "values": [],
                    "worst": [],
                    "flagged_count": 0,
                    "refused_count": 0,
                    "units": set(),
                    "operations": set(),
                },
            )
            group["flagged_count"] += int(flagged)
            group["refused_count"] += int(refused)
            operation = str(numeric.get("operation") or "")
            raw_unit = str(numeric.get("unit") or "unknown")
            if operation or raw_unit != "unknown":
                group["operations"].add(operation)
                group["units"].add("dex" if operation == "dex" else raw_unit)
            if refused or flagged or residual is None:
                continue
            observation_id = _observation_id(payload)
            group["values"].append(residual)
            if observation_id:
                group["worst"].append((abs(residual), observation_id))

    result: list[dict[str, Any]] = []
    evaporated_moles = _mapping(envelope.get("evaporated_mol_by_species"))
    for group in groups.values():
        if len(group["units"]) > 1 or len(group["operations"]) > 1:
            raise ValueError(
                "mixed numeric units or operations for "
                f"{group['rail']}/{group['species']}/{group['engine']}"
            )
        values = group["values"]
        statistics = _median_rms_from_float64(values)
        median, rms = statistics if statistics is not None else (None, None)
        worst_ids: list[str] = []
        seen_ids: set[str] = set()
        for _magnitude, observation_id in sorted(
            group["worst"], key=lambda item: (-item[0], item[1])
        ):
            if observation_id not in seen_ids:
                seen_ids.add(observation_id)
                worst_ids.append(observation_id)
            if len(worst_ids) == 3:
                break
        bias = math.fsum(values) / len(values) if values else None
        result.append(
            {
                "rail": group["rail"],
                "species": group["species"],
                "engine": group["engine"],
                "unit": next(iter(group["units"]), "unknown"),
                "n": len(values),
                "rms": None if rms is None else float(rms),
                "median": None if median is None else float(median),
                "bias": bias,
                "hero_evaporated_mol": _number(evaporated_moles.get(group["species"])),
                "flagged_count": group["flagged_count"],
                "refused_count": group["refused_count"],
                "worst_observation_ids": worst_ids,
            }
        )
    result.sort(
        key=lambda row: (
            row["rms"] is None,
            -(row["rms"] or 0.0),
            row["rail"],
            row["species"],
            row["engine"],
        )
    )
    for rank, row in enumerate(result, start=1):
        row["rank"] = rank
    return result


def _display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _markdown(envelope: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    temperature = _mapping(envelope.get("temperature_K"))
    pO2 = _mapping(envelope.get("pO2_bar"))
    pO2_text = (
        "unreported"
        if pO2.get("min") is None or pO2.get("max") is None
        else f"{_display(pO2.get('min'))}–{_display(pO2.get('max'))} bar"
    )
    engines = ", ".join(
        f"{role}: {', '.join(values) or 'none'}"
        for role, values in _mapping(envelope.get("engines_by_role")).items()
    )
    lines = [
        "# Hero recipe empirical battery priorities",
        "",
        f"Feedstock composition class: `{envelope.get('feedstock_composition_class')}` "
        f"(from `{envelope.get('feedstock_class_source')}`).",
        f"Temperature: {_display(temperature.get('min'))}–{_display(temperature.get('max'))} K "
        f"with ±{_display(temperature.get('margin_K'))} K margin; pO₂: {pO2_text}.",
        f"Engines by report role: {engines}.",
        "Rows are sorted by RMS descending. Flagged and refused counts are excluded from n and all metrics.",
        "Moles integrate reported vapor mass rates; melt-activity rows use non-oxygen formula-unit equivalents.",
        "",
        "| # | Rail | Species | Engine | n | RMS (unit) | Median | Bias | Hero evaporated mol/equiv. | Flagged | Refused | Worst observation ids |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        rms = _display(row.get("rms"))
        unit = str(row.get("unit") or "unknown").replace("|", "\\|")
        worst = ", ".join(str(value).replace("|", "\\|") for value in row["worst_observation_ids"])
        lines.append(
            f"| {row['rank']} | {row['rail']} | {row['species']} | {row['engine']} | "
            f"{row['n']} | {rms} {unit} | {_display(row['median'])} | "
            f"{_display(row['bias'])} | {_display(row['hero_evaporated_mol'])} | "
            f"{row['flagged_count']} | {row['refused_count']} | {worst} |"
        )
    lines.append("")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="battery .tgz archive or residuals JSONL")
    parser.add_argument("--hero-run", required=True, type=Path, help="runner hero-run JSON report")
    parser.add_argument("--out", required=True, type=Path, help="Markdown output path; sibling .json is written too")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = json.loads(args.hero_run.read_text(encoding="utf-8"))
        if not isinstance(report, Mapping):
            raise ValueError(f"{args.hero_run}: hero report must be an object")
        envelope = _build_envelope(report)
        rows = _score_rows(args.archive, envelope, report)
        payload = {"hero_envelope": envelope, "rows": rows}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(_markdown(envelope, rows), encoding="utf-8")
        args.out.with_suffix(".json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"battery hero priorities: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
