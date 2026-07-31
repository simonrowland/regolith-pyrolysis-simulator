"""Strict clean-oxide KEMS case loading and diagnostic reproduction adapter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.chemistry.langmuir_knudsen import knudsen_effusion_molar_flux
from simulator.diagnostic_helpers.reproduction_compare import (
    ComparisonRecord,
    compare_values,
)


_CASE_KEYS = frozenset(
    {
        "schema_version",
        "case_id",
        "source_id",
        "oxide",
        "samples",
        "cell",
        "temperature_program",
        "exterior_chamber_pressure",
        "provider_inputs",
        "calibration",
        "measurement_selectors",
        "citations",
        "assumptions",
    }
)
_OXIDE_KEYS = frozenset(
    {"formula", "identity", "purity_fraction", "purity_source"}
)
_SAMPLE_KEYS = frozenset(
    {"measurement_id", "initial_mass_mg", "post_mass_mg"}
)
_CELL_KEYS = frozenset(
    {"material", "orifice_diameter_m", "orifice_area_m2", "transmission_factor"}
)
_TRANSMISSION_KEYS = frozenset(
    {"status", "value", "derivation", "source_locator"}
)
_TEMPERATURE_KEYS = frozenset(
    {
        "mode",
        "start_K",
        "end_K",
        "step_K",
        "hold_s",
        "temperature_uncertainty_K",
        "repeat_count",
        "range_status",
        "isothermal_hold",
    }
)
_ISOTHERMAL_KEYS = frozenset(
    {"temperature_K", "temperature_uncertainty_K", "duration_h"}
)
_PRESSURE_KEYS = frozenset({"operator", "value_pa", "source_locator"})
_PROVIDER_INPUT_KEYS = frozenset({"pO2_bar", "status", "rationale"})
_CALIBRATION_KEYS = frozenset(
    {
        "standard",
        "method",
        "sensitivity_factor",
        "sensitivity_factor_units",
        "source_locator",
    }
)
_SELECTOR_KEYS = frozenset(
    {
        "observable_id",
        "observable",
        "species",
        "evidence_scope",
    }
)
_CITATION_KEYS = frozenset(
    {"source_id", "citation", "doi", "url", "locators"}
)
_OBSERVATION_ROOT_KEYS = frozenset({"schema_version", "sources", "cases"})
_OBSERVATION_SOURCE_KEYS = frozenset(
    {"citation", "doi", "url", "extraction_note"}
)
_OBSERVATION_CASE_KEYS = frozenset({"case_id", "source_id", "points"})
_OBSERVATION_POINT_KEYS = frozenset(
    {
        "observable_id",
        "species",
        "allow_total_pressure_fallback",
        "coordinate",
        "partial_pressure_pa",
        "ion_intensity",
        "effusion_rate_mol_s",
        "total_pressure_pa",
        "uncertainty",
        "status",
        "source_locator",
        "extraction_method",
        "note",
    }
)
_OBSERVABLE_KEYS = frozenset(
    {
        "partial_pressure_pa",
        "ion_intensity",
        "effusion_rate_mol_s",
        "total_pressure_pa",
    }
)
_TOTAL_PRESSURE_EVIDENCE_SCOPE = "total-pressure-fallback"
_NUMERIC_SCALAR_TYPES = frozenset({int, float, str})
_PROVIDER_STATUSES = frozenset(
    {
        "ok",
        "refused",
        "not_converged",
        "out_of_domain",
        "unavailable",
        "unsupported",
    }
)


class KEMSSchemaError(ValueError):
    """Raised when a KEMS recipe or observation sidecar violates the schema."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise KEMSSchemaError(f"{field} must be a mapping")
    return dict(value)


def _strict_keys(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str] | None = None,
    field: str,
) -> None:
    keys = frozenset(str(key) for key in value)
    unknown = keys - allowed
    missing = (required if required is not None else allowed) - keys
    if unknown:
        raise KEMSSchemaError(f"{field} has unknown keys: {sorted(unknown)}")
    if missing:
        raise KEMSSchemaError(f"{field} is missing keys: {sorted(missing)}")


def _positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if type(value) not in _NUMERIC_SCALAR_TYPES:
        raise KEMSSchemaError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise KEMSSchemaError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0 or (not allow_zero and number == 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise KEMSSchemaError(f"{field} must be finite and {qualifier}")
    return number


def _has_provenance(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_provenance(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_provenance(child) for child in value)
    if isinstance(value, str):
        return bool(value.strip())
    # Content-bearing leaves are non-blank strings or finite non-bool numeric
    # identifiers (page/table/equation numbers). Booleans and None are
    # content-free poison values, not provenance; exact-type check keeps
    # bool (a builtin-int subclass) out.
    if type(value) in (int, float):
        return math.isfinite(float(value))
    return False


def validate_kems_case(case: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(case, "case")
    _strict_keys(data, allowed=_CASE_KEYS, field="case")
    if data["schema_version"] != 1:
        raise KEMSSchemaError("case.schema_version must be 1")
    if not str(data["case_id"]).strip() or not str(data["source_id"]).strip():
        raise KEMSSchemaError("case_id and source_id are required")

    oxide = _mapping(data["oxide"], "case.oxide")
    _strict_keys(oxide, allowed=_OXIDE_KEYS, field="case.oxide")
    purity = _positive(oxide["purity_fraction"], "case.oxide.purity_fraction")
    if purity > 1.0:
        raise KEMSSchemaError("case.oxide.purity_fraction must be <= 1")

    samples = data["samples"]
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)) or not samples:
        raise KEMSSchemaError("case.samples must be a non-empty list")
    for index, sample_value in enumerate(samples):
        sample = _mapping(sample_value, f"case.samples[{index}]")
        _strict_keys(sample, allowed=_SAMPLE_KEYS, field=f"case.samples[{index}]")
        _positive(sample["initial_mass_mg"], f"case.samples[{index}].initial_mass_mg")
        _positive(sample["post_mass_mg"], f"case.samples[{index}].post_mass_mg")

    cell = _mapping(data["cell"], "case.cell")
    _strict_keys(cell, allowed=_CELL_KEYS, field="case.cell")
    diameter = _positive(cell["orifice_diameter_m"], "case.cell.orifice_diameter_m")
    area = _positive(cell["orifice_area_m2"], "case.cell.orifice_area_m2")
    area_from_diameter = math.pi * (diameter / 2.0) ** 2
    if not math.isclose(area, area_from_diameter, rel_tol=1e-6, abs_tol=0.0):
        raise KEMSSchemaError(
            "case.cell.orifice_area_m2 must equal pi*(orifice_diameter_m/2)^2"
        )
    transmission = _mapping(
        cell["transmission_factor"], "case.cell.transmission_factor"
    )
    _strict_keys(
        transmission,
        allowed=_TRANSMISSION_KEYS,
        field="case.cell.transmission_factor",
    )
    transmission_status = str(transmission["status"])
    if transmission_status not in {"reported", "derived", "not_reported"}:
        raise KEMSSchemaError(
            "case.cell.transmission_factor.status must be reported, derived, "
            "or not_reported"
        )
    if transmission_status == "not_reported":
        if transmission["value"] is not None:
            raise KEMSSchemaError(
                "not_reported transmission factor must have null value"
            )
    else:
        factor = _positive(
            transmission["value"],
            "case.cell.transmission_factor.value",
        )
        if factor > 1.0:
            raise KEMSSchemaError("transmission factor must be <= 1")
        if not _has_provenance(transmission["source_locator"]):
            raise KEMSSchemaError(
                "reported or derived transmission factor requires source_locator"
            )
        if (
            transmission_status == "derived"
            and not _has_provenance(transmission["derivation"])
        ):
            raise KEMSSchemaError(
                "derived transmission factor requires derivation"
            )

    program = _mapping(data["temperature_program"], "case.temperature_program")
    _strict_keys(program, allowed=_TEMPERATURE_KEYS, field="case.temperature_program")
    start = _positive(program["start_K"], "case.temperature_program.start_K")
    end = _positive(program["end_K"], "case.temperature_program.end_K")
    _positive(program["step_K"], "case.temperature_program.step_K")
    _positive(program["hold_s"], "case.temperature_program.hold_s")
    _positive(
        program["temperature_uncertainty_K"],
        "case.temperature_program.temperature_uncertainty_K",
    )
    _positive(program["repeat_count"], "case.temperature_program.repeat_count")
    if end < start:
        raise KEMSSchemaError("case.temperature_program.end_K must be >= start_K")
    isothermal = _mapping(
        program["isothermal_hold"], "case.temperature_program.isothermal_hold"
    )
    _strict_keys(
        isothermal,
        allowed=_ISOTHERMAL_KEYS,
        field="case.temperature_program.isothermal_hold",
    )
    for key in _ISOTHERMAL_KEYS:
        _positive(isothermal[key], f"case.temperature_program.isothermal_hold.{key}")

    pressure = _mapping(
        data["exterior_chamber_pressure"], "case.exterior_chamber_pressure"
    )
    _strict_keys(
        pressure,
        allowed=_PRESSURE_KEYS,
        field="case.exterior_chamber_pressure",
    )
    _positive(
        pressure["value_pa"],
        "case.exterior_chamber_pressure.value_pa",
        allow_zero=True,
    )

    provider_inputs = _mapping(data["provider_inputs"], "case.provider_inputs")
    _strict_keys(
        provider_inputs,
        allowed=_PROVIDER_INPUT_KEYS,
        field="case.provider_inputs",
    )
    _positive(provider_inputs["pO2_bar"], "case.provider_inputs.pO2_bar")
    if provider_inputs["status"] not in {"reported", "derived", "assumed"}:
        raise KEMSSchemaError(
            "case.provider_inputs.status must be reported, derived, or assumed"
        )

    calibration = _mapping(data["calibration"], "case.calibration")
    _strict_keys(
        calibration,
        allowed=_CALIBRATION_KEYS,
        field="case.calibration",
    )
    _positive(
        calibration["sensitivity_factor"],
        "case.calibration.sensitivity_factor",
    )

    selectors = data["measurement_selectors"]
    if (
        not isinstance(selectors, Sequence)
        or isinstance(selectors, (str, bytes))
        or not selectors
    ):
        raise KEMSSchemaError("case.measurement_selectors must be a non-empty list")
    seen: set[str] = set()
    for index, selector_value in enumerate(selectors):
        selector = _mapping(
            selector_value, f"case.measurement_selectors[{index}]"
        )
        _strict_keys(
            selector,
            allowed=_SELECTOR_KEYS,
            field=f"case.measurement_selectors[{index}]",
        )
        observable_id = str(selector["observable_id"])
        if observable_id in seen:
            raise KEMSSchemaError(f"duplicate observable_id: {observable_id}")
        seen.add(observable_id)
        if selector["observable"] not in _OBSERVABLE_KEYS:
            raise KEMSSchemaError(
                f"unsupported KEMS observable: {selector['observable']!r}"
            )
        if selector["observable"] == "total_pressure_pa":
            if selector["species"] is not None:
                raise KEMSSchemaError(
                    "total_pressure_pa selectors require species: null"
                )
        elif not str(selector["species"]).strip():
            raise KEMSSchemaError(
                "species-resolved selectors require a species"
            )

    citations = data["citations"]
    if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
        raise KEMSSchemaError("case.citations must be a list")
    for index, citation_value in enumerate(citations):
        citation = _mapping(citation_value, f"case.citations[{index}]")
        _strict_keys(
            citation,
            allowed=_CITATION_KEYS,
            field=f"case.citations[{index}]",
        )
    if not isinstance(data["assumptions"], list):
        raise KEMSSchemaError("case.assumptions must be a list")
    return data


def load_kems_case(path: str | Path) -> dict[str, Any]:
    case_path = Path(path)
    try:
        loaded = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise KEMSSchemaError(f"cannot load KEMS case {case_path}: {exc}") from exc
    return validate_kems_case(_mapping(loaded, str(case_path)))


def validate_kems_observations(
    observations: Mapping[str, Any],
) -> dict[str, Any]:
    data = _mapping(observations, "observations")
    _strict_keys(
        data,
        allowed=_OBSERVATION_ROOT_KEYS,
        field="observations",
    )
    if data["schema_version"] != 1:
        raise KEMSSchemaError("observations.schema_version must be 1")
    sources = _mapping(data["sources"], "observations.sources")
    for source_key, source_value in sources.items():
        source = _mapping(
            source_value,
            f"observations.sources.{source_key}",
        )
        _strict_keys(
            source,
            allowed=_OBSERVATION_SOURCE_KEYS,
            field=f"observations.sources.{source_key}",
        )
    cases = _mapping(data["cases"], "observations.cases")
    for case_key, case_value in cases.items():
        case = _mapping(case_value, f"observations.cases.{case_key}")
        _strict_keys(
            case,
            allowed=_OBSERVATION_CASE_KEYS,
            field=f"observations.cases.{case_key}",
        )
        if str(case["case_id"]) != str(case_key):
            raise KEMSSchemaError(
                f"observations case key {case_key!r} must equal case_id"
            )
        if str(case["source_id"]) not in data["sources"]:
            raise KEMSSchemaError(
                f"observations case {case_key!r} references an unknown source_id"
            )
        points = case["points"]
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise KEMSSchemaError(
                f"observations.cases.{case_key}.points must be a list"
            )
        for index, point_value in enumerate(points):
            point = _mapping(
                point_value,
                f"observations.cases.{case_key}.points[{index}]",
            )
            _strict_keys(
                point,
                allowed=_OBSERVATION_POINT_KEYS,
                required=frozenset(
                    {
                        "observable_id",
                        "species",
                        "allow_total_pressure_fallback",
                        "coordinate",
                        "uncertainty",
                        "status",
                        "source_locator",
                        "extraction_method",
                        "note",
                    }
                ),
                field=f"observations.cases.{case_key}.points[{index}]",
            )
            present_observables = _OBSERVABLE_KEYS.intersection(point)
            if len(present_observables) != 1:
                raise KEMSSchemaError(
                    f"observations.cases.{case_key}.points[{index}] must "
                    "declare exactly one observable value"
                )
            _mapping(
                point["coordinate"],
                f"observations.cases.{case_key}.points[{index}].coordinate",
            )
            if point["status"] not in {"reported", "assumed", "absent"}:
                raise KEMSSchemaError(
                    f"invalid observation status: {point['status']!r}"
                )
            observable_key = next(iter(present_observables))
            observable_value = point[observable_key]
            allow_total_pressure_fallback = point[
                "allow_total_pressure_fallback"
            ]
            if not isinstance(allow_total_pressure_fallback, bool):
                raise KEMSSchemaError(
                    "allow_total_pressure_fallback must be boolean"
                )
            if observable_key == "total_pressure_pa":
                if not allow_total_pressure_fallback:
                    raise KEMSSchemaError(
                        "total pressure requires sidecar fallback declaration"
                    )
                if point["species"] is not None:
                    raise KEMSSchemaError(
                        "total-pressure observations require species: null"
                    )
            elif allow_total_pressure_fallback:
                raise KEMSSchemaError(
                    "total-pressure fallback may only be declared by a "
                    "total_pressure_pa observation"
                )
            elif not str(point["species"]).strip():
                raise KEMSSchemaError(
                    "species-resolved observations require a species"
                )
            if point["status"] == "absent" and observable_value is not None:
                raise KEMSSchemaError("absent observations must have null values")
            if point["status"] != "absent":
                _positive(
                    observable_value,
                    f"observations.cases.{case_key}.points[{index}].{observable_key}",
                    allow_zero=True,
                )
                if point["uncertainty"] is None:
                    raise KEMSSchemaError(
                        "numeric observations require cited uncertainty"
                    )
                uncertainty = _mapping(
                    point["uncertainty"],
                    f"observations.cases.{case_key}.points[{index}].uncertainty",
                )
                if not {"kind", "value"}.issubset(uncertainty):
                    raise KEMSSchemaError(
                        "numeric observation uncertainty requires kind and value"
                    )
    return data


def load_kems_observations(path: str | Path) -> dict[str, Any]:
    observations_path = Path(path)
    try:
        loaded = yaml.safe_load(observations_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise KEMSSchemaError(
            f"cannot load KEMS observations {observations_path}: {exc}"
        ) from exc
    return validate_kems_observations(_mapping(loaded, str(observations_path)))


def apparatus_effusion_molar_rate(
    ideal_flux_mol_m2_s: float,
    *,
    orifice_area_m2: float,
    transmission_factor: float,
) -> float:
    """Apply apparatus geometry to the ideal per-area Knudsen flux."""

    flux = _positive(ideal_flux_mol_m2_s, "ideal_flux_mol_m2_s", allow_zero=True)
    area = _positive(orifice_area_m2, "orifice_area_m2")
    factor = _positive(transmission_factor, "transmission_factor")
    if factor > 1.0:
        raise KEMSSchemaError("transmission_factor must be <= 1")
    return flux * area * factor


@dataclass(frozen=True)
class KEMSRun:
    records: tuple[ComparisonRecord, ...]
    runtime_rows: tuple[Mapping[str, Any], ...]


class KEMSAdapter:
    """Run strict KEMS selectors against the builtin vapor-pressure provider."""

    def __init__(self, vapor_pressure_data: Mapping[str, Any]) -> None:
        from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

        self._vapor_pressure_data = vapor_pressure_legacy_view(vapor_pressure_data)
        self._provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    def _molar_mass_kg_mol(self, species: str) -> float | None:
        for group in ("metals", "oxide_vapors"):
            row = (self._vapor_pressure_data.get(group, {}) or {}).get(species)
            if isinstance(row, Mapping) and row.get("molar_mass_g_mol") is not None:
                return (
                    _positive(
                        row["molar_mass_g_mol"],
                        f"vapor-pressure molar mass for {species}",
                    )
                    / 1000.0
                )
        return None

    def _surface(
        self,
        case: Mapping[str, Any],
        *,
        temperature_K: float,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        oxide = str(case["oxide"]["formula"])
        pO2_bar = float(case["provider_inputs"]["pO2_bar"])
        exterior_pa = float(case["exterior_chamber_pressure"]["value_pa"])
        request = IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {oxide: 1.0}},
                species_formula_registry={},
            ),
            temperature_C=temperature_K - 273.15,
            pressure_bar=max(exterior_pa / 100000.0, 1e-30),
            fO2_log=math.log10(pO2_bar),
            control_inputs={"pO2_bar": pO2_bar},
        )
        result = self._provider.dispatch(request)
        provider_status = str(result.status)
        if provider_status not in _PROVIDER_STATUSES:
            raise KEMSSchemaError(
                f"unsupported provider status: {provider_status!r}"
            )
        diagnostic = dict(result.diagnostic or {})
        surface = {
            str(species): _positive(
                value,
                f"provider vapor pressure for {species}",
                allow_zero=True,
            )
            for species, value in dict(
                diagnostic.get("vapor_pressures_Pa") or {}
            ).items()
            if value is not None
        }
        runtime = {
            "provider_id": self._provider.name,
            "provider_status": provider_status,
            "temperature_K": temperature_K,
            "pO2_bar": pO2_bar,
            "vapor_pressures_Pa": surface,
            "warnings": list(result.warnings),
        }
        return surface, runtime

    def evaluate(
        self,
        case: Mapping[str, Any],
        observations: Mapping[str, Any],
        *,
        runtime_settings: Mapping[str, Any] | None = None,
    ) -> KEMSRun:
        recipe = validate_kems_case(case)
        sidecar = validate_kems_observations(observations)
        case_id = str(recipe["case_id"])
        observation_case = _mapping(
            sidecar["cases"].get(case_id),
            f"observations.cases.{case_id}",
        )
        if observation_case["source_id"] != recipe["source_id"]:
            raise KEMSSchemaError("case and observation source_id must match")
        observation_source = _mapping(
            sidecar["sources"][observation_case["source_id"]],
            f"observations.sources.{observation_case['source_id']}",
        )
        observation_package = {
            "schema_version": sidecar["schema_version"],
            "source_id": observation_case["source_id"],
            "source": observation_source,
            "case": observation_case,
        }

        selectors = {
            str(selector["observable_id"]): dict(selector)
            for selector in recipe["measurement_selectors"]
        }
        settings = dict(runtime_settings or {})
        unsupported_runtime_keys = set(settings) - {"evaporation_alpha"}
        if unsupported_runtime_keys:
            raise KEMSSchemaError(
                "unsupported KEMS runtime settings: "
                f"{sorted(unsupported_runtime_keys)}"
            )

        records: list[ComparisonRecord] = []
        runtime_rows: list[Mapping[str, Any]] = []
        surface_cache: dict[float, tuple[dict[str, float], dict[str, Any]]] = {}
        program = recipe["temperature_program"]
        recipe_assumed_input = recipe["provider_inputs"]["status"] == "assumed"
        transmission = recipe["cell"]["transmission_factor"]

        for point_value in observation_case["points"]:
            point = dict(point_value)
            observable_id = str(point["observable_id"])
            selector = selectors.get(observable_id)
            if selector is None:
                raise KEMSSchemaError(
                    f"observation references unknown selector: {observable_id}"
                )
            if point["species"] != selector["species"]:
                raise KEMSSchemaError(
                    f"selector species mismatch for {observable_id}"
                )
            point_observable = next(iter(_OBSERVABLE_KEYS.intersection(point)))
            selector_observable = str(selector["observable"])
            if point_observable != selector_observable:
                raise KEMSSchemaError(
                    f"selector observable mismatch for {observable_id}: "
                    f"{selector_observable!r} != {point_observable!r}"
                )
            coordinate = dict(point["coordinate"])
            if set(coordinate) != {"temperature_K"}:
                raise KEMSSchemaError(
                    "KEMS chunk 1 supports only temperature_K coordinates"
                )
            temperature_K = _positive(
                coordinate["temperature_K"],
                f"observation {observable_id} temperature_K",
            )
            out_of_domain = not (
                float(program["start_K"])
                <= temperature_K
                <= float(program["end_K"])
            )

            if out_of_domain:
                surface: dict[str, float] = {}
                runtime = {
                    "provider_id": self._provider.name,
                    "provider_status": "out_of_domain",
                    "temperature_K": temperature_K,
                    "vapor_pressures_Pa": {},
                    "warnings": [],
                }
            else:
                if temperature_K not in surface_cache:
                    surface_cache[temperature_K] = self._surface(
                        recipe,
                        temperature_K=temperature_K,
                    )
                surface, runtime = surface_cache[temperature_K]

            observable = selector_observable
            species = (
                str(selector["species"])
                if selector["species"] is not None
                else None
            )
            provider_non_ok = runtime["provider_status"] != "ok"
            exact_pressure = (
                surface.get(species)
                if species is not None and not provider_non_ok
                else None
            )
            unsupported_speciation = (
                not out_of_domain
                and not provider_non_ok
                and observable != "total_pressure_pa"
                and exact_pressure is None
            )
            ideal_flux = None
            molar_mass = (
                self._molar_mass_kg_mol(species)
                if species is not None
                else None
            )
            if exact_pressure is not None and molar_mass is not None:
                ideal_flux = knudsen_effusion_molar_flux(
                    temperature_K,
                    exact_pressure,
                    molar_mass_kg_mol=molar_mass,
                )

            apparatus_effusion = None
            if ideal_flux is not None and transmission["value"] is not None:
                apparatus_effusion = apparatus_effusion_molar_rate(
                    ideal_flux,
                    orifice_area_m2=float(recipe["cell"]["orifice_area_m2"]),
                    transmission_factor=float(transmission["value"]),
                )

            if observable == "partial_pressure_pa":
                actual_value = exact_pressure
                units = "Pa"
            elif observable == "effusion_rate_mol_s":
                actual_value = apparatus_effusion
                units = "mol/s"
            elif observable == "ion_intensity":
                actual_value = None
                units = "paper-defined intensity"
            elif observable == "total_pressure_pa":
                if not point["allow_total_pressure_fallback"]:
                    raise KEMSSchemaError(
                        "total pressure requires sidecar fallback declaration"
                    )
                actual_value = (
                    sum(surface.values())
                    if surface and not provider_non_ok
                    else None
                )
                units = "Pa"
            else:
                raise KEMSSchemaError(f"unsupported observable: {observable}")

            expected_value = point[observable]
            uncertainty = point.get("uncertainty")
            comparison_uncertainty = (
                dict(uncertainty)
                if isinstance(uncertainty, Mapping)
                else None
            )
            runtime_payload = {
                **runtime,
                "runtime_settings": settings,
                "species": species,
                "ideal_flux_mol_m2_s": ideal_flux,
                "orifice_area_m2": float(recipe["cell"]["orifice_area_m2"]),
                "transmission_factor": transmission["value"],
                "apparatus_effusion_rate_mol_s": apparatus_effusion,
            }
            runtime_rows.append(runtime_payload)
            records.append(
                compare_values(
                    case_id=case_id,
                    source_id=str(recipe["source_id"]),
                    observable_id=observable_id,
                    species=species,
                    coordinate=coordinate,
                    expected_value=(
                        float(expected_value)
                        if expected_value is not None
                        else None
                    ),
                    expected_uncertainty=comparison_uncertainty,
                    actual_value=actual_value,
                    units=units,
                    evidence_scope=(
                        _TOTAL_PRESSURE_EVIDENCE_SCOPE
                        if observable == "total_pressure_pa"
                        else str(selector["evidence_scope"])
                    ),
                    source_locator=point["source_locator"],
                    recipe=recipe,
                    observation=observation_package,
                    runtime=runtime_payload,
                    unsupported_speciation=unsupported_speciation,
                    assumed_input=recipe_assumed_input
                    or point["status"] == "assumed",
                    out_of_domain=out_of_domain or provider_non_ok,
                )
            )
        return KEMSRun(records=tuple(records), runtime_rows=tuple(runtime_rows))
