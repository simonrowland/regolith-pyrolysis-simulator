"""Deny-by-default recipe schema for optimizer-facing setpoint patches."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Any, Literal, Mapping

KeyPath = tuple[str, ...]

recipe_schema_version = "recipe-schema-v1"
allowlist_version = "allowlist-v1"


class RecipeValidationError(ValueError):
    """Raised when a recipe patch attempts an unsafe or unknown mutation."""


@dataclass(frozen=True)
class KnobSpec:
    path: KeyPath
    kind: Literal["float", "int", "categorical"]
    low: float | None = None
    high: float | None = None
    choices: tuple[str, ...] | None = None
    units: str = ""
    bounds_source: str = ""


def _knob(
    path: str,
    kind: Literal["float", "int", "categorical"] = "float",
    *,
    low: float | None = None,
    high: float | None = None,
    choices: tuple[str, ...] | None = None,
    units: str = "",
    bounds_source: str,
) -> KnobSpec:
    return KnobSpec(
        path=tuple(path.split(".")),
        kind=kind,
        low=low,
        high=high,
        choices=choices,
        units=units,
        bounds_source=bounds_source,
    )


class RecipeSchema:
    """Curated allowlist plus global deny prefixes for optimizer recipes."""

    # These whole-value list paths replace YAML ranges such as
    # ``temp_range_C: [lo, hi]``. V1 still forbids list-item paths; future C5
    # endpoint-hours/current and alkali-dose knobs should be added here only
    # after R1/R2 parameterize them as explicit setpoint inputs.
    NUMERIC_PAIR_VALUE_PATHS: frozenset[KeyPath] = frozenset(
        tuple(path.split("."))
        for path in (
            "campaigns.C0.temp_range_C",
            "campaigns.C0b_p_cleanup.temp_range_C",
            "campaigns.C0b_p_cleanup.pO2_mbar",
            "campaigns.C0b_p_cleanup.duration_h",
            "campaigns.C2A_continuous.temp_range_C",
            "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
            "campaigns.C2A_continuous.p_total_mbar",
            "campaigns.C2A_continuous.duration_h",
            "campaigns.C2A_staged.temp_range_C",
            "campaigns.C2A_staged.p_total_mbar",
            "campaigns.C2B.temp_range_C",
            "campaigns.C2B.pO2_mbar",
            "campaigns.C3.K_phase.pO2_bakeout_mbar",
            "campaigns.C3.Na_phase.pO2_bakeout_mbar",
            "campaigns.C3.duration_after_pathA_h",
            "campaigns.C3.duration_after_pathB_h_per_phase",
            "campaigns.C4.temp_range_C",
            "campaigns.C4.pO2_mbar",
            "campaigns.C4.optional_Ca_harvest.pO2_mbar",
            "campaigns.C5.temp_range_C",
            "campaigns.C5.pO2_bar",
            "campaigns.C6.temp_range_C",
            "campaigns.C6.pO2_mbar",
        )
    )

    ALLOWLIST: tuple[KnobSpec, ...] = (
        _knob(
            "campaigns.C0.temp_range_C",
            low=20,
            high=950,
            units="C",
            bounds_source="setpoints:campaigns.C0.temp_range_C",
        ),
        _knob(
            "campaigns.C0.dT_dt_C_per_hr",
            low=10,
            high=100,
            units="C/hr",
            bounds_source="setpoints:campaigns.C0.dT_dt_C_per_hr",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.temp_range_C",
            low=1180,
            high=1320,
            units="C",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.temp_range_C",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.pO2_mbar",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.pO2_mbar_default",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.p_total_mbar_default",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.duration_h",
            low=0.5,
            high=2.5,
            units="h",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.duration_h",
        ),
        _knob(
            "campaigns.C2A_continuous.temp_range_C",
            low=1050,
            high=1600,
            units="C",
            bounds_source="setpoints:campaigns.C2A_continuous.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
            low=10,
            high=20,
            units="C/hr",
            bounds_source="setpoints:campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
        ),
        _knob(
            "campaigns.C2A_continuous.p_total_mbar",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_continuous.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_continuous.p_total_mbar_default",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_continuous.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_continuous.duration_h",
            low=18,
            high=28,
            units="h",
            bounds_source="setpoints:campaigns.C2A_continuous.duration_h",
        ),
        _knob(
            "campaigns.C2A_staged.temp_range_C",
            low=1250,
            high=1750,
            units="C",
            bounds_source="setpoints:campaigns.C2A_staged.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_staged.default_hold_T_C",
            low=1250,
            high=1750,
            units="C",
            bounds_source="setpoints:campaigns.C2A_staged.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_staged.p_total_mbar",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_staged.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_staged.p_total_mbar_default",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_staged.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr",
            low=300,
            high=900,
            units="C/hr",
            bounds_source="setpoints:campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr",
        ),
        _knob(
            "campaigns.C2A_staged.na_shuttle_stage.duration_h",
            low=1,
            high=6,
            units="h",
            bounds_source="setpoints:campaigns.C2A_staged.na_shuttle_stage.duration_h",
        ),
        _knob(
            "campaigns.C2B.temp_range_C",
            low=1320,
            high=1480,
            units="C",
            bounds_source="setpoints:campaigns.C2B.temp_range_C",
        ),
        _knob(
            "campaigns.C2B.pO2_mbar",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C2B.pO2_mbar_default",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C2B.p_total_mbar_default",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C3.pO2_mbar_default",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.p_total_mbar_default",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.K_phase.pO2_bakeout_mbar",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.Na_phase.pO2_bakeout_mbar",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.Na_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.endpoint.hold_time_min",
            "int",
            low=15,
            high=60,
            units="min",
            bounds_source="setpoints:campaigns.C3.endpoint.hold_time_min",
        ),
        _knob(
            "campaigns.C3.duration_after_pathA_h",
            low=10,
            high=18,
            units="h",
            bounds_source="setpoints:campaigns.C3.duration_after_pathA_h",
        ),
        _knob(
            "campaigns.C3.duration_after_pathB_h_per_phase",
            low=20,
            high=35,
            units="h",
            bounds_source="setpoints:campaigns.C3.duration_after_pathB_h_per_phase",
        ),
        _knob(
            "campaigns.C4.temp_range_C",
            low=1580,
            high=1670,
            units="C",
            bounds_source="setpoints:campaigns.C4.temp_range_C",
        ),
        _knob(
            "campaigns.C4.pO2_mbar",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.pO2_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.p_total_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.optional_Ca_harvest.pO2_mbar",
            low=0.03,
            high=0.12,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.optional_Ca_harvest.pO2_mbar",
        ),
        _knob(
            "campaigns.C5.temp_range_C",
            low=1500,
            high=1650,
            units="C",
            bounds_source="setpoints:campaigns.C5.temp_range_C",
        ),
        _knob(
            "campaigns.C5.pO2_bar",
            low=0.01,
            high=0.1,
            units="bar",
            bounds_source="setpoints:campaigns.C5.pO2_bar",
        ),
        _knob(
            "campaigns.C5.pO2_mbar_default",
            low=10,
            high=100,
            units="mbar",
            bounds_source="setpoints:campaigns.C5.pO2_bar",
        ),
        _knob(
            "campaigns.C5.p_total_mbar_default",
            low=10,
            high=100,
            units="mbar",
            bounds_source="setpoints:campaigns.C5.pO2_bar",
        ),
        _knob(
            "campaigns.C6.temp_range_C",
            low=1450,
            high=1550,
            units="C",
            bounds_source="setpoints:campaigns.C6.temp_range_C",
        ),
        _knob(
            "campaigns.C6.default_hold_T_C",
            low=1450,
            high=1550,
            units="C",
            bounds_source="setpoints:campaigns.C6.temp_range_C",
        ),
        _knob(
            "campaigns.C6.pO2_mbar",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
        _knob(
            "campaigns.C6.pO2_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
        _knob(
            "campaigns.C6.p_total_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
    )

    FORBIDDEN_PREFIXES: tuple[str, ...] = (
        "chemistry_kernel",
        "mass_balance",
        "kinetic_parameters",
        "constants",
        "safety",
        "safety_ceilings",
        "*.products",
        "*.mass_balance",
        "*.kinetic_parameters",
        "*.constants",
        "*.safety",
        "*.safety_ceilings",
    )

    recipe_schema_version = recipe_schema_version
    allowlist_version = allowlist_version

    def __init__(
        self,
        allowlist: tuple[KnobSpec, ...] | None = None,
        *,
        forbidden_prefixes: tuple[str, ...] | None = None,
        recipe_schema_version: str | None = None,
        allowlist_version: str | None = None,
    ) -> None:
        self.allowlist = allowlist if allowlist is not None else self.ALLOWLIST
        # FORBIDDEN_PREFIXES is an INVIOLABLE floor: a caller may ADD deny
        # prefixes but can never remove the class-level set. Otherwise a custom
        # schema (e.g. RecipeSchema(forbidden_prefixes=())) passed to
        # RecipePatch.validated() could neuter the safety boundary and validate a
        # *.products / chemistry_kernel path.
        extra_forbidden = tuple(forbidden_prefixes or ())
        self.forbidden_prefixes = tuple(
            dict.fromkeys(type(self).FORBIDDEN_PREFIXES + extra_forbidden)
        )
        self.recipe_schema_version = (
            recipe_schema_version
            if recipe_schema_version is not None
            else type(self).recipe_schema_version
        )
        self.allowlist_version = (
            allowlist_version
            if allowlist_version is not None
            else type(self).allowlist_version
        )
        self._spec_by_path = {spec.path: spec for spec in self.allowlist}

    def spec_for(self, path: KeyPath) -> KnobSpec:
        normalized = _normalize_key_path(path)
        if self.is_forbidden(normalized):
            raise RecipeValidationError(
                f"forbidden recipe path: {_format_path(normalized)}"
            )
        try:
            return self._spec_by_path[normalized]
        except KeyError as exc:
            raise RecipeValidationError(
                f"unknown recipe path: {_format_path(normalized)}"
            ) from exc

    def is_forbidden(self, path: KeyPath) -> bool:
        dotted_prefixes = _dotted_prefixes(path)
        return any(
            fnmatchcase(prefix, pattern)
            for pattern in self.forbidden_prefixes
            for prefix in dotted_prefixes
        )


@dataclass(frozen=True)
class RecipePatch:
    values: Mapping[KeyPath, Any]

    def __post_init__(self) -> None:
        normalized = {
            _normalize_key_path(path): _normalize_value(value)
            for path, value in self.values.items()
        }
        object.__setattr__(self, "values", MappingProxyType(normalized))

    @classmethod
    def from_nested(cls, nested: Mapping[str, Any]) -> "RecipePatch":
        if not isinstance(nested, Mapping):
            raise RecipeValidationError("recipe patch must be a nested mapping")
        if not nested:
            return cls({})

        flat: dict[KeyPath, Any] = {}

        def walk(prefix: KeyPath, node: Any) -> None:
            if isinstance(node, Mapping):
                if not node:
                    raise RecipeValidationError(
                        f"empty nested recipe branch: {_format_path(prefix)}"
                    )
                for key, value in node.items():
                    if not isinstance(key, str):
                        raise RecipeValidationError(
                            "recipe patch nested keys must be strings"
                        )
                    walk(prefix + (key,), value)
                return
            if not prefix:
                raise RecipeValidationError("recipe patch path cannot be empty")
            flat[prefix] = _normalize_value(node)

        walk((), nested)
        return cls(flat)

    def to_nested(self) -> dict[str, Any]:
        nested: dict[str, Any] = {}
        for path, value in sorted(self.values.items()):
            cursor: dict[str, Any] = nested
            for segment in path[:-1]:
                existing = cursor.setdefault(segment, {})
                if not isinstance(existing, dict):
                    raise RecipeValidationError(
                        f"recipe path conflicts with scalar: {_format_path(path)}"
                    )
                cursor = existing
            leaf = path[-1]
            if leaf in cursor:
                raise RecipeValidationError(
                    f"duplicate recipe path: {_format_path(path)}"
                )
            cursor[leaf] = _normalize_value(value)
        return nested

    def validated(self, schema: RecipeSchema | None = None) -> "RecipePatch":
        active_schema = schema or RecipeSchema()
        for path, value in self.values.items():
            spec = active_schema.spec_for(path)
            _validate_value(spec, value, active_schema)
        return RecipePatch(dict(self.values))

    def recipe_id(
        self,
        schema: RecipeSchema | None = None,
        *,
        recipe_schema_version: str | None = None,
    ) -> str:
        active_schema = schema or RecipeSchema()
        schema_version = recipe_schema_version or active_schema.recipe_schema_version
        canonical = self.canonical_json().encode("utf-8")
        payload = canonical + b"\n" + schema_version.encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def canonical_json(self) -> str:
        entries = [
            {"path": list(path), "value": _normalize_value(value)}
            for path, value in sorted(self.values.items())
        ]
        return json.dumps(
            entries,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _normalize_key_path(path: Any) -> KeyPath:
    if not isinstance(path, tuple) or not path:
        raise RecipeValidationError("recipe paths must be non-empty KeyPath tuples")
    if not all(isinstance(segment, str) and segment for segment in path):
        raise RecipeValidationError("recipe path segments must be non-empty strings")
    # Segments must be atomic: "." is the path separator. A segment that embeds a
    # "." (e.g. "products.oxygen_kg") would defeat dotted-prefix forbidden
    # matching -- the joined string would not end in ".products", slipping past
    # the "*.products" deny pattern.
    if any("." in segment for segment in path):
        raise RecipeValidationError(
            "recipe path segments must not contain '.' (the path separator)"
        )
    return path


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecipeValidationError("recipe values must not be NaN or infinite")
        return value
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, Mapping):
        raise RecipeValidationError("recipe values must be scalars or lists")
    return value


def _validate_value(spec: KnobSpec, value: Any, schema: RecipeSchema) -> None:
    if spec.kind == "categorical":
        if not isinstance(value, str):
            raise RecipeValidationError(
                f"{_format_path(spec.path)} requires categorical string value"
            )
        if spec.choices is None or value not in spec.choices:
            raise RecipeValidationError(
                f"{_format_path(spec.path)} value {value!r} not in choices"
            )
        return
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise RecipeValidationError(f"{_format_path(spec.path)} requires int value")
        _validate_numeric_bounds(spec, float(value))
        return
    if spec.kind == "float":
        if isinstance(value, list):
            if spec.path not in schema.NUMERIC_PAIR_VALUE_PATHS:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} requires scalar float value"
                )
            if len(value) != 2:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} requires [low, high] pair"
                )
            numeric_values = [_coerce_float(spec, item) for item in value]
            if numeric_values[0] > numeric_values[1]:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} range low exceeds high"
                )
            for item in numeric_values:
                _validate_numeric_bounds(spec, item)
            return
        _validate_numeric_bounds(spec, _coerce_float(spec, value))
        return
    raise RecipeValidationError(f"{_format_path(spec.path)} has unknown kind")


def _coerce_float(spec: KnobSpec, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{_format_path(spec.path)} requires float value")
    result = float(value)
    if not math.isfinite(result):
        raise RecipeValidationError(
            f"{_format_path(spec.path)} must not be NaN or infinite"
        )
    return result


def _validate_numeric_bounds(spec: KnobSpec, value: float) -> None:
    if spec.low is not None and value < spec.low:
        raise RecipeValidationError(
            f"{_format_path(spec.path)} value {value!r} below lower bound {spec.low!r}"
        )
    if spec.high is not None and value > spec.high:
        raise RecipeValidationError(
            f"{_format_path(spec.path)} value {value!r} above upper bound {spec.high!r}"
        )


def _dotted_prefixes(path: KeyPath) -> tuple[str, ...]:
    return tuple(".".join(path[:idx]) for idx in range(1, len(path) + 1))


def _format_path(path: KeyPath) -> str:
    return ".".join(path)
