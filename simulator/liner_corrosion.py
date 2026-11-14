"""Load source-located Tier A phase invariants for the liner-corrosion model."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


EXTRACTS_DIR = Path(__file__).resolve().parents[1] / "data" / "literature" / "extracts"


class LinerCorrosionDataError(ValueError):
    """A source extract cannot safely supply a Tier A invariant record."""

    def __init__(self, source_id: str, reason: str, detail: str) -> None:
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"{source_id}: {reason}: {detail}")


def _mapping(
    value: Any, source_id: str, observation_id: str, field: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LinerCorrosionDataError(
            source_id, "invalid_record", f"{observation_id}.{field} must be a mapping"
        )
    return value


def _read_extract(filename: str, expected_source_id: str) -> Mapping[str, Any]:
    path = EXTRACTS_DIR / filename
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LinerCorrosionDataError(
            expected_source_id, "source_unavailable", f"cannot read {filename}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise LinerCorrosionDataError(
            expected_source_id, "invalid_yaml", f"cannot parse {filename}: {exc}"
        ) from exc
    if not isinstance(document, Mapping):
        raise LinerCorrosionDataError(
            expected_source_id, "invalid_extract", f"{filename} root must be a mapping"
        )
    if document.get("source_id") != expected_source_id:
        raise LinerCorrosionDataError(
            expected_source_id,
            "source_identity_mismatch",
            f"{filename} declares {document.get('source_id')!r}",
        )
    return document


def _finite_number(
    value: Any, source_id: str, observation_id: str, field: str
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LinerCorrosionDataError(
            source_id, "invalid_numeric_value", f"{observation_id}.{field} is not numeric"
        )
    if not math.isfinite(value):
        raise LinerCorrosionDataError(
            source_id, "non_finite_value", f"{observation_id}.{field} is not finite"
        )
    return value


def _locator(
    observation: Mapping[str, Any], source_id: str, observation_id: str
) -> dict[str, Any]:
    locator = _mapping(
        observation.get("locator"), source_id, observation_id, "locator"
    )
    page = locator.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise LinerCorrosionDataError(
            source_id, "invalid_locator", f"{observation_id} has no physical page"
        )
    if not any(key in locator for key in ("table", "section", "figure")):
        raise LinerCorrosionDataError(
            source_id,
            "invalid_locator",
            f"{observation_id} needs a table, section, or figure locator",
        )
    return dict(locator)


def _phases(
    value: Any, source_id: str, observation_id: str
) -> list[str] | None:
    if value == "not stated":
        return None
    if not isinstance(value, list) or not value:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_phase_list",
            f"{observation_id}.phases_as_printed must be a non-empty list or 'not stated'",
        )
    if any(not isinstance(phase, str) or not phase.strip() for phase in value):
        raise LinerCorrosionDataError(
            source_id, "invalid_phase_list", f"{observation_id} has an empty or non-text phase"
        )
    return list(value)


def _composition(
    values: Mapping[str, Any], source_id: str, observation_id: str
) -> dict[str, int | float] | None:
    if "liquid_composition_mass_percent" in values:
        raw = values["liquid_composition_mass_percent"]
    elif "composition_mass_percent_as_printed" in values:
        raw = values.get("composition_mass_percent_as_printed")
    elif "composition_mass_percent" in values:
        raw = values["composition_mass_percent"]
    elif values.get("liquid_composition_as_printed") == "not stated":
        return None
    else:
        raise LinerCorrosionDataError(
            source_id,
            "missing_composition_status",
            f"{observation_id} does not state whether a liquid composition is available",
        )
    if raw == "not stated":
        return None
    composition = _mapping(raw, source_id, observation_id, "liquid composition")
    if not composition:
        raise LinerCorrosionDataError(
            source_id, "invalid_composition", f"{observation_id} has an empty composition"
        )
    result: dict[str, int | float] = {}
    for oxide, value in composition.items():
        if not isinstance(oxide, str) or not oxide.strip():
            raise LinerCorrosionDataError(
                source_id,
                "invalid_composition_component",
                f"{observation_id} has a non-text oxide key",
            )
        result[oxide] = _finite_number(
            value, source_id, observation_id, f"composition.{oxide}"
        )
    return result


def _temperature(
    observation: Mapping[str, Any],
    values: Mapping[str, Any],
    source_id: str,
    observation_id: str,
) -> tuple[int | float, int | float | None]:
    has_celsius = "temperature_C" in values
    has_kelvin = "temperature_K" in values
    units = observation.get("units")
    if has_celsius == has_kelvin:
        raise LinerCorrosionDataError(
            source_id,
            "ambiguous_temperature_unit",
            f"{observation_id} must have exactly one printed Celsius or Kelvin value",
        )
    if has_kelvin:
        if units != "K":
            raise LinerCorrosionDataError(
                source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
            )
        temperature = _finite_number(
            values["temperature_K"], source_id, observation_id, "temperature_K"
        ) - CELSIUS_TO_KELVIN_OFFSET
        uncertainty_key = "temperature_uncertainty_K"
        uncertainty = (
            _finite_number(values[uncertainty_key], source_id, observation_id, uncertainty_key)
            if uncertainty_key in values
            else None
        )
        return temperature, uncertainty
    if not isinstance(units, str) or "°C" not in units:
        raise LinerCorrosionDataError(
            source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
        )
    temperature = _finite_number(
        values["temperature_C"], source_id, observation_id, "temperature_C"
    )
    uncertainty_key = "temperature_uncertainty_C"
    uncertainty = (
        _finite_number(values[uncertainty_key], source_id, observation_id, uncertainty_key)
        if uncertainty_key in values
        else None
    )
    return temperature, uncertainty


def _observation_record(
    observation: Mapping[str, Any], source_id: str
) -> dict[str, Any]:
    observation_id = observation.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise LinerCorrosionDataError(
            source_id, "missing_observation_id", "transition point has no id"
        )
    values = _mapping(observation.get("values"), source_id, observation_id, "values")
    subsystem = values.get("subsystem_as_printed")
    if not isinstance(subsystem, str) or not subsystem:
        raise LinerCorrosionDataError(
            source_id, "missing_subsystem", f"{observation_id} has no printed subsystem"
        )
    phases = _phases(values.get("phases_as_printed"), source_id, observation_id)
    temperature, uncertainty = _temperature(
        observation, values, source_id, observation_id
    )
    kind = values.get("invariant_type") or values.get("invariant_type_as_printed")
    if not isinstance(kind, str) or not kind:
        raise LinerCorrosionDataError(
            source_id, "missing_invariant_type", f"{observation_id} has no invariant type"
        )
    composition = _composition(values, source_id, observation_id)
    units = observation.get("units")
    if composition is not None and (
        not isinstance(units, str) or "wt%" not in units
    ):
        raise LinerCorrosionDataError(
            source_id,
            "unknown_composition_unit",
            f"{observation_id} has a composition with units {units!r}",
        )
    record: dict[str, Any] = {
        "source_id": source_id,
        "observation_id": observation_id,
        "subsystem": subsystem,
        "T_invariant_C": temperature,
        "phases": phases,
        "liquid_composition": composition,
        "liquid_composition_unit": "wt%" if composition is not None else None,
        "invariant_type": kind,
        "locator": _locator(observation, source_id, observation_id),
    }
    if uncertainty is not None:
        record["temperature_uncertainty_C"] = uncertainty
    for source_field in (
        "compound_as_printed",
        "phase_reaction_as_printed",
        "source_status",
        "temperature_K_as_printed",
        "table_reference_as_printed",
    ):
        if source_field in values:
            record[source_field] = values[source_field]
    return record


def _source_observation_records(
    document: Mapping[str, Any], source_id: str
) -> list[dict[str, Any]]:
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    observations = cao.get("observations")
    if not isinstance(observations, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_observations", "species.CaO.observations must be a list"
        )
    records = []
    for observation in observations:
        if (
            not isinstance(observation, Mapping)
            or observation.get("type") != "transition_point"
        ):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_observation_type",
                "invariant row is not a transition_point",
            )
        records.append(_observation_record(observation, source_id))
    return records


def _reaction_parts(
    reaction: Any, source_id: str, observation_id: str
) -> tuple[list[list[str]], list[str]]:
    if not isinstance(reaction, str) or not reaction.strip():
        raise LinerCorrosionDataError(
            source_id,
            "missing_phase_reaction",
            f"{observation_id} has no printed phase reaction",
        )
    sides = re.split(r"\s*(?:⇌|↔|→|->|=)\s*", reaction)
    if len(sides) != 2:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_phase_reaction",
            f"{observation_id} reaction must have two printed sides",
        )
    parsed_sides: list[list[str]] = []
    phases: list[str] = []
    for side in sides:
        side = re.sub(r"\bL\+\s*$", "L", side.strip())
        side_phases = []
        for printed_phase in side.split("+"):
            phase = printed_phase.strip()
            if not phase:
                raise LinerCorrosionDataError(
                    source_id,
                    "invalid_phase_reaction",
                    f"{observation_id} has an empty phase token",
                )
            if re.fullmatch(r"L(?:\s*\([^)]*\))?", phase):
                phase = "liquid"
            side_phases.append(phase)
            if phase not in phases:
                phases.append(phase)
        parsed_sides.append(side_phases)
    return parsed_sides, phases


def _reaction_type(
    reaction: Any, source_id: str, observation_id: str
) -> tuple[str, list[str]]:
    sides, phases = _reaction_parts(reaction, source_id, observation_id)
    if "liquid" not in phases:
        raise LinerCorrosionDataError(
            source_id,
            "reaction_has_no_liquid",
            f"{observation_id} reaction has no liquid phase",
        )
    solid_counts = [sum(phase != "liquid" for phase in side) for side in sides]
    if sorted(solid_counts) == [0, 1]:
        kind = "congruent melting"
    elif 0 in solid_counts:
        kind = "eutectic"
    else:
        kind = "peritectic"
    return kind, phases


def _morey_context_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "morey-1964-usgs-pp440l-phase-equilibria"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    contexts = cao.get("context")
    if not isinstance(contexts, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_context", "species.CaO.context must be a list"
        )
    table_subsystems = {
        6: "CaO-Al2O3",
        12: "Na2O-CaO-Al2O3",
        21: "MgO-CaO-Al2O3",
        26: "CaO-Al2O3-SiO2",
    }
    records = []
    for context in contexts:
        if not isinstance(context, Mapping):
            raise LinerCorrosionDataError(
                source_id, "invalid_record", "Morey context row must be a mapping"
            )
        values = _mapping(
            context.get("values"), source_id,
            str(context.get("observation_id", "context")), "values"
        )
        table_number = values.get("table_number")
        is_invariant_table_row = (
            isinstance(table_number, int)
            and not isinstance(table_number, bool)
            and table_number in table_subsystems
            and values.get("quantity") == "transition_temperature"
        )
        observation_id = context.get("observation_id")
        is_compound_transition = (
            isinstance(observation_id, str)
            and observation_id in {
                "morey1964_t6_c12a7_1455",
                "morey1964_t6_c12a7_1391",
                "morey1964_cas_gehlenite_1590",
                "morey1964_cas_c3as_1335",
            }
        )
        if not (is_invariant_table_row or is_compound_transition):
            continue
        if not isinstance(observation_id, str) or not observation_id:
            raise LinerCorrosionDataError(
                source_id, "missing_observation_id", "Morey transition has no id"
            )
        subsystem = values.get("subsystem_as_printed")
        if subsystem is None and is_invariant_table_row:
            subsystem = table_subsystems[table_number]
        if not isinstance(subsystem, str) or not subsystem:
            raise LinerCorrosionDataError(
                source_id, "missing_subsystem", f"{observation_id} has no subsystem"
            )
        temperature = _finite_number(
            values.get("temperature_C_as_printed"),
            source_id,
            observation_id,
            "temperature_C_as_printed",
        )
        units = context.get("units")
        if not isinstance(units, str) or "°C" not in units:
            raise LinerCorrosionDataError(
                source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
            )
        composition = _composition(values, source_id, observation_id)
        if composition is not None and "wt%" not in units.replace(" ", "") and "weight" not in str(
            values.get("composition_header_as_printed", "")
        ).lower():
            raise LinerCorrosionDataError(
                source_id, "unknown_composition_unit", f"{observation_id} has no weight-percent basis"
            )
        reaction = values.get("phase_reaction_as_printed")
        if "phases_as_printed" in values:
            phases = _phases(
                values["phases_as_printed"], source_id, observation_id
            )
            inferred_kind, _ = _reaction_type(reaction, source_id, observation_id)
        elif reaction:
            inferred_kind, phases = _reaction_type(
                reaction, source_id, observation_id
            )
        else:
            inferred_kind = values.get("invariant_type")
            phases = _phases(
                values.get("phases_as_printed"), source_id, observation_id
            )
        kind = values.get("invariant_type", inferred_kind)
        if not isinstance(kind, str) or not kind:
            raise LinerCorrosionDataError(
                source_id, "missing_invariant_type", f"{observation_id} has no invariant type"
            )
        locator = _locator(context, source_id, observation_id)
        record: dict[str, Any] = {
            "source_id": source_id,
            "observation_id": observation_id,
            "subsystem": subsystem,
            "T_invariant_C": temperature,
            "phases": phases,
            "liquid_composition": composition,
            "liquid_composition_unit": "wt%" if composition is not None else None,
            "invariant_type": kind,
            "locator": locator,
        }
        if reaction is not None:
            record["phase_reaction_as_printed"] = reaction
        if "compound_as_printed" in values:
            record["compound_as_printed"] = values["compound_as_printed"]
        if "invariant_point_as_printed" in values:
            record["invariant_point_as_printed"] = values["invariant_point_as_printed"]
        if "temperature_qualification_as_printed" in values:
            record["temperature_qualification_as_printed"] = values[
                "temperature_qualification_as_printed"
            ]
        if "temperature_uncertainty_C_as_printed" in values:
            record["temperature_uncertainty_C"] = _finite_number(
                values["temperature_uncertainty_C_as_printed"],
                source_id,
                observation_id,
                "temperature_uncertainty_C_as_printed",
            )
        qualifier = values.get("qualifier")
        if isinstance(qualifier, str) and qualifier:
            record["source_qualifier"] = qualifier
            if "in error" in qualifier.lower():
                record["source_status"] = "source marks this point as in error"
        records.append(record)
    return records


def _shornikov_fusion_record(
    observation: Mapping[str, Any], species_name: str
) -> dict[str, Any]:
    source_id = "shornikov-1997-cao-al2o3"
    observation_id = observation.get("observation_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise LinerCorrosionDataError(
            source_id, "missing_observation_id", "fusion temperature has no id"
        )
    values = _mapping(observation.get("values"), source_id, observation_id, "values")
    printed_temperature = values.get("T_fus_as_printed")
    match = (
        re.fullmatch(
            r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:±\s*([+-]?\d+(?:\.\d+)?))?\s*",
            printed_temperature,
        )
        if isinstance(printed_temperature, str)
        else None
    )
    if match is None:
        raise LinerCorrosionDataError(
            source_id,
            "invalid_printed_temperature",
            f"{observation_id}.T_fus_as_printed is not a single point or point ± uncertainty",
        )
    temperature_k = float(match.group(1))
    stored_temperature = values.get("T_K", values.get("temperature_K"))
    if isinstance(stored_temperature, bool) or str(stored_temperature) != match.group(1):
        raise LinerCorrosionDataError(
            source_id,
            "temperature_value_mismatch",
            f"{observation_id} T_K does not match its printed Tfus value",
        )
    units = observation.get("units")
    locator = _locator(observation, source_id, observation_id)
    is_existing_fusion_enthalpy = (
        values.get("quantity") == "heat_of_fusion" and units == "kJ/mol"
    )
    if locator.get("table") != "3" or not (units == "K" or is_existing_fusion_enthalpy):
        raise LinerCorrosionDataError(
            source_id, "unknown_temperature_unit", f"{observation_id} declares {units!r}"
        )
    compound = values.get("printed_compound", values.get("compound_as_printed"))
    if not isinstance(compound, str) or not compound:
        raise LinerCorrosionDataError(
            source_id, "missing_compound", f"{observation_id} has no printed compound"
        )
    phase_statement = observation.get("phase")
    phases = _phases(values.get("phases_as_printed", "not stated"), source_id, observation_id)
    composition = _composition(
        {**values, "liquid_composition_as_printed": values.get("liquid_composition_as_printed", "not stated")},
        source_id,
        observation_id,
    )
    kind = values.get(
        "invariant_type_as_printed", "fusion temperature; melting mode not stated"
    )
    if not isinstance(kind, str) or not kind:
        raise LinerCorrosionDataError(
            source_id, "missing_invariant_type", f"{observation_id} has no fusion type"
        )
    record: dict[str, Any] = {
        "source_id": source_id,
        "observation_id": observation_id,
        "subsystem": "CaO-Al2O3",
        "T_invariant_C": temperature_k - CELSIUS_TO_KELVIN_OFFSET,
        "phases": phases,
        "liquid_composition": composition,
        "liquid_composition_unit": "wt%" if composition is not None else None,
        "invariant_type": kind,
        "locator": locator,
        "compound_as_printed": compound,
        "temperature_K_as_printed": printed_temperature,
    }
    uncertainty = match.group(2)
    if uncertainty is not None:
        record["temperature_uncertainty_C"] = float(uncertainty)
    if isinstance(phase_statement, str) and phase_statement:
        record["phase_as_printed"] = phase_statement
    if species_name:
        record["formula"] = species_name
    if "source_reference_as_printed" in values:
        record["source_reference_as_printed"] = values["source_reference_as_printed"]
    return record


def _shornikov_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "shornikov-1997-cao-al2o3"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    records = []
    for species_name, section_value in species.items():
        if not isinstance(species_name, str):
            raise LinerCorrosionDataError(
                source_id, "invalid_species", "species key must be text"
            )
        section = _mapping(section_value, source_id, "extract", f"species.{species_name}")
        observations = section.get("observations")
        if not isinstance(observations, list):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_observations",
                f"species.{species_name}.observations must be a list",
            )
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise LinerCorrosionDataError(
                    source_id, "invalid_record", f"species.{species_name} row must be a mapping"
                )
            values = observation.get("values")
            if not isinstance(values, Mapping) or "T_fus_as_printed" not in values:
                continue
            records.append(_shornikov_fusion_record(observation, species_name))
    return records


def _rankin_table_a_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_id = "rankin-wright-1915-ajs-cao-al2o3-sio2"
    species = _mapping(document.get("species"), source_id, "extract", "species")
    cao = _mapping(species.get("CaO"), source_id, "extract", "species.CaO")
    contexts = cao.get("context")
    if not isinstance(contexts, list):
        raise LinerCorrosionDataError(
            source_id, "invalid_context", "species.CaO.context must be a list"
        )
    records = []
    for context in contexts:
        if not isinstance(context, Mapping):
            raise LinerCorrosionDataError(
                source_id, "invalid_record", "Table A context row must be a mapping"
            )
        values = context.get("values")
        if not isinstance(values, Mapping) or values.get("summary_table") != "A":
            continue
        if values.get("quantity") != "transition_temperature":
            continue
        transformation = values.get("transformation_as_printed")
        formula = values.get("formula_as_printed")
        if not isinstance(transformation, str) or not isinstance(formula, str):
            raise LinerCorrosionDataError(
                source_id, "invalid_compound_transition", "Table A transition lacks printed text"
            )
        if not (
            transformation.startswith("Melting")
            or transformation.startswith("Dissociation into ")
        ):
            continue
        observation_id = context.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise LinerCorrosionDataError(
                source_id, "missing_observation_id", "Table A transition has no id"
            )
        components = values.get("compound_composition_mass_percent")
        if not isinstance(components, Mapping):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_compound_composition",
                f"{observation_id} has no compound composition",
            )
        if any(not isinstance(component, str) or not component for component in components):
            raise LinerCorrosionDataError(
                source_id,
                "invalid_compound_composition",
                f"{observation_id} has a non-text component name",
            )
        subsystem = "-".join(components)
        if transformation.startswith("Melting"):
            phases = [formula, "liquid"]
            invariant_type = "congruent melting"
        else:
            products_text = transformation.removeprefix("Dissociation into ")
            products = [part.strip() for part in products_text.split(" and ")]
            if not products or any(not part for part in products):
                raise LinerCorrosionDataError(
                    source_id,
                    "invalid_compound_transition",
                    f"{observation_id} has unparsed dissociation products",
                )
            phases = [formula, *products]
            invariant_type = "dissociation"
        temperature = _finite_number(
            values.get("temperature_C"), source_id, observation_id, "temperature_C"
        )
        locator = _locator(context, source_id, observation_id)
        records.append(
            {
                "source_id": source_id,
                "observation_id": observation_id,
                "subsystem": subsystem,
                "T_invariant_C": temperature,
                "phases": phases,
                "liquid_composition": None,
                "liquid_composition_unit": None,
                "invariant_type": invariant_type,
                "transition_as_printed": transformation,
                "locator": locator,
            }
        )
    return records


def load_liner_corrosion_invariants() -> list[dict[str, Any]]:
    """Return source-located invariant and compound-transition rows in Celsius."""
    records: list[dict[str, Any]] = []

    rankin_id = "rankin-wright-1915-ajs-cao-al2o3-sio2"
    rankin = _read_extract(f"{rankin_id}.yaml", rankin_id)
    records.extend(_source_observation_records(rankin, rankin_id))
    records.extend(_rankin_table_a_records(rankin))

    morey_id = "morey-1964-usgs-pp440l-phase-equilibria"
    morey = _read_extract(f"{morey_id}.yaml", morey_id)
    records.extend(_morey_context_records(morey))

    shults_id = "shults-1995-calcium-aluminate-thermodynamics"
    shults = _read_extract(f"{shults_id}.yaml", shults_id)
    records.extend(_source_observation_records(shults, shults_id))

    shornikov_id = "shornikov-1997-cao-al2o3"
    shornikov = _read_extract(f"{shornikov_id}.yaml", shornikov_id)
    records.extend(_shornikov_records(shornikov))

    return records


__all__ = ("LinerCorrosionDataError", "load_liner_corrosion_invariants")
