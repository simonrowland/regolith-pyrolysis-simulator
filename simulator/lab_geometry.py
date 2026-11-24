"""Runtime support for gram-scale vacuum-pyrolysis lab geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from simulator.state import PipeSegment
from simulator.scalar_boundary import is_declared_real_scalar


LAB_GEOMETRY_SCALE = "gram_lab"
LAB_FIXED_EQUIPMENT_SIZING = "lab_fixed_geometry"
LAB_EXPOSED_MELT_AREA_BASIS = "gram_lab_exposed_melt"
LAB_EXPOSED_MELT_AREA_ASSUMED_PELLET_DIAMETER = (
    "assumed_from_printed_pellet_diameter"
)
_LAB_SURFACE_TEMPERATURE_DEFAULT_C = 0.0
LAB_SURFACE_ROLES = frozenset({
    "holder",
    "window",
    "condenser",
    "filter",
    "chamber_wall",
})
LAB_SURFACE_ROLE_ALIASES = {
    "sample_holder": "holder",
    "transparent_wall": "window",
    "collector": "condenser",
    "downstream_filter": "filter",
    "wall": "chamber_wall",
}
LAB_GEOMETRY_SOURCE_CLASSES = frozenset({
    "literature_sidecar",
    "measured",
    "assumption_with_sensitivity_marker",
})
LAB_MIN_PIPE_DIAMETER_M = 1.0e-9


class LabGeometryError(ValueError):
    """Named refusal for invalid lab-geometry runtime input."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class GramLabMeltAreaResolution:
    """Effective melt-face area and whether runtime sizing supplied it."""

    area_m2: float | None
    defaulted: bool = False
    out_of_domain: bool = False
    basis: str | None = None
    locator: str | None = None
    default_reason: str | None = None


@dataclass(frozen=True)
class _LabSurfaceProfileResolution:
    """Resolved schedule points or the typed bench default for one profile."""

    schedule_points: Any | None = None
    default_temperature_C: float | None = None
    notice: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LabSurface:
    """One declared gram-scale deposition surface."""

    surface_id: str
    role: str
    area_m2: float
    temperature_C: float | None
    view_factor_from_melt: float
    line_of_sight_to_melt: bool
    source_class: str
    sensitivity_marker: str = ""
    temperature_profile: str = ""
    distance_from_melt_m: float | None = None
    equivalent_diameter_m: float | None = None
    extraction_note: str = ""
    liner_material: str = ""

    @property
    def wall_deposit_account(self) -> str:
        return f"process.wall_deposit_segment_{self.surface_id}"

    def to_pipe_segment(
        self,
        *,
        upstream_stage: str,
        downstream_stage: str,
        default_diameter_m: float,
    ) -> PipeSegment:
        diameter_m = self.equivalent_diameter_m
        if diameter_m is None:
            diameter_m = math.sqrt(self.area_m2 / math.pi)
            diameter_field = f"{self.surface_id}.derived_equivalent_diameter_m"
        else:
            diameter_field = f"{self.surface_id}.equivalent_diameter_m"
        diameter_m = require_lab_pipe_diameter(diameter_m, diameter_field)
        length_m = require_lab_pipe_length(
            self.area_m2 / (math.pi * diameter_m),
            f"{self.surface_id}.pipe_length_m",
        )
        return PipeSegment(
            name=self.surface_id,
            upstream_stage=upstream_stage,
            downstream_stage=downstream_stage,
            wall_temperature_C=(
                _LAB_SURFACE_TEMPERATURE_DEFAULT_C
                if self.temperature_C is None
                else self.temperature_C
            ),
            length_m=length_m,
            inner_diameter_m=diameter_m,
            role=self.role,
            declared_area_m2=self.area_m2,
            view_factor_from_melt=self.view_factor_from_melt,
            line_of_sight_to_melt=self.line_of_sight_to_melt,
            source_class=self.source_class,
            sensitivity_marker=self.sensitivity_marker,
            extraction_note=self.extraction_note,
            liner_material=self.liner_material,
        )


@dataclass(frozen=True)
class LabGeometry:
    """Validated gram-lab geometry block."""

    geometry_id: str
    scale: str
    equipment_sizing: str
    surfaces: tuple[LabSurface, ...] = field(default_factory=tuple)
    sample_mass_g: float | None = None

    @property
    def total_surface_area_m2(self) -> float:
        return sum(surface.area_m2 for surface in self.surfaces)

    @property
    def wall_deposit_accounts(self) -> tuple[str, ...]:
        return tuple(surface.wall_deposit_account for surface in self.surfaces)

    def to_pipe_segments(
        self,
        *,
        default_diameter_m: float = 0.02,
    ) -> list[PipeSegment]:
        segments: list[PipeSegment] = []
        for index, surface in enumerate(self.surfaces):
            segments.append(surface.to_pipe_segment(
                upstream_stage=(
                    "stage_0" if index == 0 else f"lab_surface_{index - 1}"
                ),
                downstream_stage=f"lab_surface_{index}",
                default_diameter_m=default_diameter_m,
            ))
        return segments


def parse_lab_geometry(
    raw: Mapping[str, Any] | None,
    *,
    allow_temperature_profiles: bool = False,
) -> LabGeometry | None:
    """Validate and normalize a runtime lab_geometry mapping."""

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise LabGeometryError(
            "invalid_lab_geometry", "lab_geometry must be a mapping"
        )
    scale = str(raw.get("scale") or "").strip()
    if not scale:
        raise LabGeometryError(
            "missing_lab_geometry_scale", "lab_geometry.scale is required"
        )
    equipment_sizing = str(raw.get("equipment_sizing") or "").strip()
    if scale == LAB_GEOMETRY_SCALE and equipment_sizing != LAB_FIXED_EQUIPMENT_SIZING:
        raise LabGeometryError(
            "gram_lab_requires_lab_fixed_geometry",
            "gram_lab geometry must declare equipment_sizing=lab_fixed_geometry",
        )
    surfaces_raw = raw.get("surfaces")
    if not isinstance(surfaces_raw, list) or not surfaces_raw:
        raise LabGeometryError(
            "missing_lab_surfaces", "lab_geometry.surfaces must be non-empty"
        )
    surfaces = tuple(
        _parse_lab_surface(
            index,
            surface,
            allow_temperature_profiles=allow_temperature_profiles,
            allow_temperature_default=scale == LAB_GEOMETRY_SCALE,
        )
        for index, surface in enumerate(surfaces_raw)
    )
    return LabGeometry(
        geometry_id=str(raw.get("id") or "lab_geometry"),
        scale=scale,
        equipment_sizing=equipment_sizing,
        surfaces=surfaces,
        sample_mass_g=_optional_finite_positive(
            (raw.get("sample") or {}).get("mass_g")
            if isinstance(raw.get("sample"), Mapping)
            else None,
            field="lab_geometry.sample.mass_g",
        ),
    )


def resolve_gram_lab_exposed_melt_area(
    lab_geometry: Mapping[str, Any] | None,
    *,
    crucible_face_area_m2: float | None = None,
) -> GramLabMeltAreaResolution:
    """Resolve a printed gram-lab melt face or its sized crucible default."""

    if not isinstance(lab_geometry, Mapping):
        return GramLabMeltAreaResolution(None)
    if str(lab_geometry.get("scale") or "").strip() != LAB_GEOMETRY_SCALE:
        return GramLabMeltAreaResolution(None)
    sample = lab_geometry.get("sample")
    if not isinstance(sample, Mapping):
        return GramLabMeltAreaResolution(None)

    raw_area = sample.get("exposed_melt_area_m2")
    raw_basis = sample.get("exposed_melt_area_basis")
    basis = str(raw_basis).strip() if raw_basis not in (None, "") else None
    raw_locator = sample.get("exposed_melt_area_source_ref")
    locator = (
        raw_locator.strip()
        if isinstance(raw_locator, str) and raw_locator.strip()
        else None
    )
    area = None
    default_reason = None
    defaulted = raw_area in (None, "")
    if defaulted:
        default_reason = (
            "lab_geometry_exposed_melt_area_basis_without_value"
            if basis is not None
            else "exposed_melt_area_not_printed"
        )
        if crucible_face_area_m2 is None:
            return GramLabMeltAreaResolution(None)
        raw_area = crucible_face_area_m2
    else:
        # A supplied number remains subject to the positive finite contract
        # even when its provenance cannot be trusted.
        try:
            area = (
                float(raw_area)
                if is_declared_real_scalar(raw_area, allow_numeric_str=True)
                else None
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise LabGeometryError(
                "invalid_lab_geometry_positive_value",
                "lab_geometry.sample.exposed_melt_area_m2 must be finite positive",
            ) from exc
        if area is None or not math.isfinite(area) or area <= 0.0:
            raise LabGeometryError(
                "invalid_lab_geometry_positive_value",
                "lab_geometry.sample.exposed_melt_area_m2 must be finite positive",
            )
        basis = basis or "printed"
        if basis not in {
            "printed",
            LAB_EXPOSED_MELT_AREA_ASSUMED_PELLET_DIAMETER,
        }:
            default_reason = "invalid_lab_geometry_exposed_melt_area_basis"
        elif locator is None:
            default_reason = "missing_lab_geometry_exposed_melt_area_locator"
        if default_reason is not None:
            if crucible_face_area_m2 is None:
                return GramLabMeltAreaResolution(None)
            raw_area = crucible_face_area_m2
            defaulted = True
            area = None

    if area is None:
        try:
            area = float(raw_area)
        except (TypeError, ValueError, OverflowError) as exc:
            raise LabGeometryError(
                "invalid_lab_geometry_positive_value",
                "lab_geometry.sample.exposed_melt_area_m2 must be finite positive",
            ) from exc
        if not math.isfinite(area) or area <= 0.0:
            raise LabGeometryError(
                "invalid_lab_geometry_positive_value",
                "lab_geometry.sample.exposed_melt_area_m2 must be finite positive",
            )
    return GramLabMeltAreaResolution(
        area_m2=area,
        defaulted=defaulted,
        out_of_domain=(
            defaulted
            or basis == LAB_EXPOSED_MELT_AREA_ASSUMED_PELLET_DIAMETER
        ),
        basis=basis,
        locator=locator,
        default_reason=default_reason,
    )


def gram_lab_exposed_melt_area_bridge(
    lab_geometry: Mapping[str, Any] | None,
    *,
    crucible_face_area_m2: float | None = None,
) -> dict[str, Any]:
    resolution = resolve_gram_lab_exposed_melt_area(
        lab_geometry,
        crucible_face_area_m2=crucible_face_area_m2,
    )
    if resolution.area_m2 is None:
        return {}
    bridge: dict[str, Any] = {
        "effective_exposed_area_m2": resolution.area_m2,
        "area_basis": LAB_EXPOSED_MELT_AREA_BASIS,
    }
    if resolution.defaulted:
        notice = {
            "code": "knob_defaulted",
            "path": "lab_geometry.sample.exposed_melt_area_m2",
            "value": resolution.area_m2,
            "unit": "m2",
            "default_source": "EquipmentDesigner.size_crucible",
            "source_class": "assumption_with_sensitivity_marker",
            "sensitivity_marker": "defaulted_crucible_face",
            "reason": resolution.default_reason,
            "original_reason": resolution.default_reason,
        }
        if resolution.out_of_domain:
            notice["out_of_domain"] = True
        bridge["notices"] = [notice]
    elif (
        resolution.basis
        == LAB_EXPOSED_MELT_AREA_ASSUMED_PELLET_DIAMETER
    ):
        sample = (
            lab_geometry.get("sample")
            if isinstance(lab_geometry, Mapping)
            else None
        )
        notice = {
            "code": "knob_assumed",
            "path": "lab_geometry.sample.exposed_melt_area_m2",
            "knob": "exposed_melt_area_m2",
            "value": resolution.area_m2,
            "unit": "m2",
            "basis": resolution.basis,
            "derivation": "pi*(D/2)^2",
            "locator": resolution.locator,
            "source_class": (
                sample.get("exposed_melt_area_source_class")
                if isinstance(sample, Mapping)
                else None
            ),
            "sensitivity_marker": (
                sample.get("exposed_melt_area_sensitivity_marker")
                if isinstance(sample, Mapping)
                else None
            ),
            "out_of_domain": True,
            "out_of_domain_reason": "molten_bead_is_not_a_flat_face",
        }
        bridge["notices"] = [notice]
    return bridge


def _lab_surface_temperature_default_notices(
    raw: Mapping[str, Any],
    *,
    sample_mass_g: float | None,
    allow_temperature_profiles: bool,
) -> list[dict[str, Any]]:
    """Describe gram-lab surface temperatures supplied by the pipe fallback."""

    if str(raw.get("scale") or "").strip() != LAB_GEOMETRY_SCALE:
        return []
    surfaces = raw.get("surfaces")
    if not isinstance(surfaces, list):
        return []
    notices: list[dict[str, Any]] = []
    for surface in surfaces:
        if not isinstance(surface, Mapping):
            continue
        surface_id = str(surface.get("id") or "").strip()
        if not surface_id:
            continue
        temperature_profile = str(
            surface.get("temperature_profile") or ""
        ).strip()
        _, defaulted = _surface_temperature_resolution(
            surface,
            surface_id,
            temperature_profile=temperature_profile,
            allow_temperature_profiles=allow_temperature_profiles,
            allow_temperature_default=True,
        )
        if not defaulted:
            continue
        notice: dict[str, Any] = {
            "code": "knob_defaulted",
            "path": f"lab_geometry.surfaces.{surface_id}.temperature_C",
            "value": _LAB_SURFACE_TEMPERATURE_DEFAULT_C,
            "unit": "C",
            "default_source": "LabSurface.to_pipe_segment.wall_temperature_C",
            "source_class": "assumption_with_sensitivity_marker",
            "sensitivity_marker": "defaulted_lab_surface_temperature",
        }
        if sample_mass_g is None:
            notice["out_of_domain"] = True
        notices.append(notice)
    return notices


def _resolve_lab_surface_profile(
    surface: LabSurface,
    surface_schedule: Mapping[str, Any],
    *,
    scale: str,
) -> _LabSurfaceProfileResolution:
    """Resolve the declared profile or return its typed bench fallback."""

    profile_name = surface.temperature_profile
    if profile_name in surface_schedule:
        return _LabSurfaceProfileResolution(
            schedule_points=surface_schedule[profile_name],
        )
    reason = f"lab_schedule_missing_surface_temperature: {profile_name}"
    if scale != LAB_GEOMETRY_SCALE:
        return _LabSurfaceProfileResolution(reason=reason)

    from simulator.battery.default_policy import resolve

    policy = resolve(
        "DP-008",
        runtime_default=_LAB_SURFACE_TEMPERATURE_DEFAULT_C,
    )
    notice = {
        "code": policy.flag.value,
        "path": f"lab_geometry.surfaces.{surface.surface_id}.temperature_C",
        "value": policy.value,
        "unit": "C",
        "default_source": "LabSurface.to_pipe_segment.wall_temperature_C",
        "source_class": "assumption_with_sensitivity_marker",
        "sensitivity_marker": "defaulted_lab_surface_temperature",
        "profile": profile_name,
        "reason": reason,
        "original_reason": reason,
        "policy": dict(policy.payload["policy"]),
    }
    return _LabSurfaceProfileResolution(
        default_temperature_C=float(policy.value),
        notice=notice,
        reason=reason,
    )


def _parse_lab_surface(
    index: int,
    raw: Any,
    *,
    allow_temperature_profiles: bool,
    allow_temperature_default: bool,
) -> LabSurface:
    if not isinstance(raw, Mapping):
        raise LabGeometryError(
            "invalid_lab_surface", f"surface[{index}] must be a mapping"
        )
    surface_id = str(raw.get("id") or "").strip()
    if not surface_id:
        raise LabGeometryError(
            "missing_lab_surface_id", f"surface[{index}].id is required"
        )
    role = _canonical_role(raw.get("role"), surface_id=surface_id)
    area_m2 = _required_finite_positive(raw.get("area_m2"), f"{surface_id}.area_m2")
    temperature_profile = str(raw.get("temperature_profile") or "").strip()
    temperature_C, _ = _surface_temperature_resolution(
        raw,
        surface_id,
        temperature_profile=temperature_profile,
        allow_temperature_profiles=allow_temperature_profiles,
        allow_temperature_default=allow_temperature_default,
    )
    view_factor = _required_unit_interval(
        raw.get("view_factor_from_melt"),
        f"{surface_id}.view_factor_from_melt",
    )
    source_class = _source_class(
        raw.get("source_class"), f"{surface_id}.source_class")
    sensitivity_marker = _sensitivity_marker(
        raw.get("sensitivity_marker"),
        source_class=source_class,
        field=f"{surface_id}.sensitivity_marker",
    )
    return LabSurface(
        surface_id=surface_id,
        role=role,
        area_m2=area_m2,
        temperature_C=temperature_C,
        view_factor_from_melt=view_factor,
        line_of_sight_to_melt=_line_of_sight(raw, surface_id),
        source_class=source_class,
        sensitivity_marker=sensitivity_marker,
        temperature_profile=temperature_profile,
        distance_from_melt_m=_optional_finite_positive(
            raw.get("distance_from_melt_m"),
            field=f"{surface_id}.distance_from_melt_m",
        ),
        equivalent_diameter_m=_optional_lab_pipe_diameter(
            raw.get("equivalent_diameter_m"),
            field=f"{surface_id}.equivalent_diameter_m",
        ),
        extraction_note=_required_extraction_note(
            raw.get("extraction_note"), f"{surface_id}.extraction_note"),
        liner_material=str(raw.get("liner_material") or "").strip(),
    )


def _canonical_role(value: Any, *, surface_id: str) -> str:
    role = str(value or "").strip()
    if role in LAB_SURFACE_ROLE_ALIASES:
        role = LAB_SURFACE_ROLE_ALIASES[role]
    if role not in LAB_SURFACE_ROLES:
        allowed = ", ".join(sorted(LAB_SURFACE_ROLES))
        raise LabGeometryError(
            "unknown_lab_surface_role",
            f"{surface_id}: role {role!r} is not one of {allowed}",
        )
    return role


def _surface_temperature_resolution(
    raw: Mapping[str, Any],
    surface_id: str,
    *,
    temperature_profile: str,
    allow_temperature_profiles: bool,
    allow_temperature_default: bool,
) -> tuple[float | None, bool]:
    for key in ("temperature_C", "wall_temperature_C"):
        if key in raw:
            return _required_finite(raw[key], f"{surface_id}.{key}"), False
    if temperature_profile:
        if allow_temperature_profiles:
            return None, False
        raise LabGeometryError(
            "missing_lab_surface_temperature",
            f"{surface_id}: temperature_C or resolvable temperature_profile is required",
        )
    if allow_temperature_default:
        return _LAB_SURFACE_TEMPERATURE_DEFAULT_C, True
    raise LabGeometryError(
        "missing_lab_surface_temperature",
        f"{surface_id}: temperature_C or resolvable temperature_profile is required",
    )


def _line_of_sight(raw: Mapping[str, Any], surface_id: str) -> bool:
    for key in ("line_of_sight_to_melt", "line_of_sight"):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"yes", "true", "direct"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"no", "false", "blocked"}:
            return False
        raise LabGeometryError(
            "invalid_lab_surface_line_of_sight",
            f"{surface_id}: {key} must be boolean or direct/blocked",
        )
    raise LabGeometryError(
        "missing_lab_surface_line_of_sight",
        f"{surface_id}: line_of_sight_to_melt is required",
    )


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LabGeometryError("missing_lab_geometry_source_class", field)
    return text


def _source_class(value: Any, field: str) -> str:
    source_class = _required_text(value, field)
    if source_class not in LAB_GEOMETRY_SOURCE_CLASSES:
        allowed = ", ".join(sorted(LAB_GEOMETRY_SOURCE_CLASSES))
        raise LabGeometryError(
            "invalid_lab_geometry_source_class",
            f"{field}={source_class!r} is not one of {allowed}",
        )
    return source_class


def _sensitivity_marker(
    value: Any,
    *,
    source_class: str,
    field: str,
) -> str:
    marker = str(value or "").strip()
    if source_class == "assumption_with_sensitivity_marker" and not marker:
        raise LabGeometryError("missing_lab_geometry_sensitivity_marker", field)
    return marker


def _required_extraction_note(value: Any, field: str) -> str:
    note = str(value or "").strip()
    if not note:
        raise LabGeometryError("missing_lab_geometry_extraction_note", field)
    return note


def _required_finite_positive(value: Any, field: str) -> float:
    result = _required_finite(value, field)
    if result <= 0.0:
        raise LabGeometryError("invalid_lab_geometry_positive_value", field)
    return result


def _optional_finite_positive(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return _required_finite_positive(value, field)


def _optional_lab_pipe_diameter(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return require_lab_pipe_diameter(value, field)


def require_lab_pipe_diameter(value: Any, field: str) -> float:
    result = _required_finite(value, field)
    if result < LAB_MIN_PIPE_DIAMETER_M:
        raise LabGeometryError(
            "invalid_lab_geometry_pipe_diameter",
            (
                f"{field} must be >= {LAB_MIN_PIPE_DIAMETER_M:g} m; "
                f"got {result:g} m"
            ),
        )
    return result


def require_lab_pipe_length(value: Any, field: str) -> float:
    result = _required_finite(value, field)
    if result < LAB_MIN_PIPE_DIAMETER_M:
        raise LabGeometryError(
            "invalid_lab_geometry_pipe_length",
            (
                f"{field} must be >= {LAB_MIN_PIPE_DIAMETER_M:g} m; "
                f"got {result:g} m"
            ),
        )
    return result


def _required_unit_interval(value: Any, field: str) -> float:
    result = _required_finite(value, field)
    if result < 0.0 or result > 1.0:
        raise LabGeometryError("invalid_lab_geometry_view_factor", field)
    return result


def _required_finite(value: Any, field: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LabGeometryError("invalid_lab_geometry_number", field) from exc
    if not math.isfinite(result):
        raise LabGeometryError("invalid_lab_geometry_number", field)
    return result
