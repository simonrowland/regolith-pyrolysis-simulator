"""Runtime support for gram-scale vacuum-pyrolysis lab geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Mapping

from simulator.state import PipeSegment
from simulator.scalar_boundary import is_declared_real_scalar


LAB_GEOMETRY_SCALE = "gram_lab"
LAB_FIXED_EQUIPMENT_SIZING = "lab_fixed_geometry"
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
GAS_FILM_SPHERE = "sphere_equivalent"
GAS_FILM_DISK = "disk_on_plane"


class LabGeometryError(ValueError):
    """Named refusal for invalid lab-geometry runtime input."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def read_gram_lab_exposed_melt_area(
    lab_geometry: Mapping[str, Any] | None,
) -> float | None:
    """Printed gram-lab melt face, or None when this geometry has no sample face.

    None means the mapping is not a gram-lab sample. The scale token is
    ``LAB_GEOMETRY_SCALE``. A gram-lab sample whose area is null or blank
    raises ``exposed_melt_area_not_printed``. A present value that is not
    a finite positive declared scalar raises
    ``invalid_lab_geometry_positive_value``. Numeric strings stay admitted.
    A binary scalar, and an integer that ``float()`` cannot convert, are
    that refusal. ``LabGeometryError`` is a ``ValueError``, so this
    function raises it outside the conversion ``except``.
    """
    if not isinstance(lab_geometry, Mapping):
        return None
    if str(lab_geometry.get('scale') or '').strip() != LAB_GEOMETRY_SCALE:
        return None
    sample = lab_geometry.get('sample')
    if not isinstance(sample, Mapping):
        return None
    raw_area = sample.get('exposed_melt_area_m2', None)
    if raw_area in (None, ''):
        raise LabGeometryError(
            'exposed_melt_area_not_printed',
            'lab_geometry.sample.exposed_melt_area_m2 is null; '
            'size_crucible must not supply the melt face',
        )
    try:
        if is_declared_real_scalar(raw_area, allow_numeric_str=True):
            area: float | None = float(raw_area)
        else:
            area = None
    except (TypeError, ValueError, OverflowError) as exc:
        raise LabGeometryError(
            'invalid_lab_geometry_positive_value',
            'lab_geometry.sample.exposed_melt_area_m2 must be finite positive',
        ) from exc
    if area is None or not math.isfinite(area) or area <= 0.0:
        raise LabGeometryError(
            'invalid_lab_geometry_positive_value',
            'lab_geometry.sample.exposed_melt_area_m2 must be finite positive',
        )
    return area


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
                0.0 if self.temperature_C is None else self.temperature_C
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


@dataclass(frozen=True)
class LabGasFilmGeometry:
    """One sample-scale length, evaporating area, shape, and provenance."""

    length_m: float
    shape: str
    area_m2: float
    length_method: str
    flags: tuple[str, ...]

    def as_diagnostic(self) -> dict[str, Any]:
        return {
            "transport_length_m": self.length_m,
            "gas_film_shape": self.shape,
            "melt_surface_area_m2": self.area_m2,
            "gas_film_length_method": self.length_method,
            "typed_flags": list(self.flags),
        }


def resolve_lab_gas_film_geometry(
    lab_geometry: Mapping[str, Any] | None,
) -> LabGasFilmGeometry | None:
    """Resolve a gram-lab film and area from the same declared sample geometry.

    Order: a printed/deck-declared diameter, printed exposed area, printed
    sample mass, then the flagged DP-113 bench-scale length. Apparatus selects
    the shape: a crucible/support is a disk on a plane; a free pellet, bead, or
    wire is sphere-equivalent; an unmarked apparatus defaults to a sphere. A
    mass-only supported melt is a hemispherical pool, while a free body is
    sphere-equivalent. When area is absent, the same resolved length supplies
    its shape-consistent evaporating area.
    """
    if not isinstance(lab_geometry, Mapping):
        return None
    if str(lab_geometry.get("scale") or "").strip() != LAB_GEOMETRY_SCALE:
        return None

    sample_raw = lab_geometry.get("sample")
    sample = sample_raw if isinstance(sample_raw, Mapping) else {}
    surface_rows = lab_geometry.get("surfaces")
    surfaces = (
        tuple(row for row in surface_rows if isinstance(row, Mapping))
        if isinstance(surface_rows, (list, tuple))
        else ()
    )
    apparatus_fields = (
        "crucible_material",
        "support_material",
        "support",
        "crucible_inner_diameter_mm",
        "exposed_melt_diameter_mm",
    )
    apparatus_text = " ".join(
        str(sample.get(field) or "").casefold()
        for field in apparatus_fields[:3]
    )
    holder_surfaces = tuple(
        row for row in surfaces
        if str(row.get("role") or "").casefold() == "sample_holder"
    )
    holder_text = " ".join(
        str(value or "").casefold()
        for row in holder_surfaces
        for value in (
            row.get("id"),
            row.get("liner_material"),
            row.get("extraction_note"),
        )
    )
    holder_declares_support = any(
        marker in holder_text
        for marker in ("crucible", "support")
    )
    apparatus_text = " ".join((apparatus_text, holder_text))
    wire_loop_sample = any(
        marker in apparatus_text
        for marker in ("wire", "loop", "bead", "free pellet", "free body")
    )
    container_declared = (
        any(bool(sample.get(field)) for field in apparatus_fields[:3])
        or holder_declares_support
    )
    disk_diameter_declared = any(
        sample.get(field) not in (None, "") for field in apparatus_fields[3:]
    )
    supported = (
        not wire_loop_sample
        and (container_declared or disk_diameter_declared)
    )
    shape = GAS_FILM_DISK if supported else GAS_FILM_SPHERE
    shape_flag = f"gas_film_shape:{shape}"
    shape_is_stated = (
        supported
        or wire_loop_sample
        or declared_pellet_diameter_m(sample) is not None
    )

    declared_diameter_keys = (
        ("crucible_inner_diameter_mm", "exposed_melt_diameter_mm")
        if supported
        else ()
    )
    length_m: float | None = None
    length_method = ""
    pellet_diameter_m = declared_pellet_diameter_m(sample)
    if pellet_diameter_m is not None and not supported:
        length_m = pellet_diameter_m
        length_method = "declared:pellet_diameter_mm"
    for key in declared_diameter_keys:
        raw = sample.get(key)
        if raw in (None, ""):
            continue
        value = _lab_positive_scalar(raw, field=f"sample.{key}")
        length_m = value / 1000.0
        length_method = f"declared:{key}"
        break

    area_m2: float | None = None
    raw_area = sample.get("exposed_melt_area_m2")
    melt_diameter_owns_area = length_method in {
        "declared:crucible_inner_diameter_mm",
        "declared:exposed_melt_diameter_mm",
    }
    if raw_area not in (None, "") and not melt_diameter_owns_area:
        area_m2 = read_gram_lab_exposed_melt_area(lab_geometry)
        if length_m is None:
            length_m = (
                math.sqrt(4.0 * area_m2 / math.pi)
                if supported
                else math.sqrt(area_m2 / math.pi)
            )
            length_method = "derived:printed_exposed_area"

    flags = [shape_flag]
    if not shape_is_stated:
        flags.append(
            "gas_film_shape_assumed:sphere_equivalent:apparatus_unspecified"
        )
    if length_m is not None:
        if length_method.startswith("declared:"):
            flags.append("gas_film_length_declared")
        else:
            flags.append("gas_film_length_derived")
    else:
        raw_mass_g = sample.get("mass_g")
        if raw_mass_g not in (None, ""):
            mass_g = _lab_positive_scalar(raw_mass_g, field="sample.mass_g")
            # EquipmentDesigner owns the melt density used by equipment sizing.
            from simulator.equipment import EquipmentDesigner

            volume_m3 = (mass_g / 1000.0) / EquipmentDesigner.MELT_DENSITY_KG_M3
            if supported:
                # V = (2/3) pi r^3 for a hemispherical pool; d = 2r.
                length_m = (12.0 * volume_m3 / math.pi) ** (1.0 / 3.0)
                length_method = "derived:hemispherical_pool_from_mass"
            else:
                length_m = (6.0 * volume_m3 / math.pi) ** (1.0 / 3.0)
                length_method = "derived:sphere_from_mass"
            flags.append("gas_film_length_derived")
        else:
            length_m = _dp_113_gas_film_length_m()
            length_method = "assumed:DP-113"
            flags.append("gas_film_length_assumed")

    if area_m2 is None:
        area_m2 = (
            math.pi * length_m**2 / 4.0
            if supported
            else math.pi * length_m**2
        )
    return LabGasFilmGeometry(
        length_m=length_m,
        shape=shape,
        area_m2=area_m2,
        length_method=length_method,
        flags=tuple(flags),
    )


def declared_pellet_diameter_m(
    sample: Mapping[str, Any] | None,
) -> float | None:
    """Return the deck's positive pellet diameter in metres, if declared."""
    if not isinstance(sample, Mapping):
        return None
    raw = sample.get("pellet_diameter_mm")
    if raw in (None, ""):
        return None
    return _lab_positive_scalar(raw, field="sample.pellet_diameter_mm") / 1000.0


def _dp_113_gas_film_length_m() -> float:
    """Read the flagged gram-lab length from its policy owner."""
    import yaml

    policy_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "battery"
        / "default_policy.yaml"
    )
    document = yaml.safe_load(policy_path.read_text())
    policies = document.get("policies") if isinstance(document, Mapping) else None
    row = next(
        (
            item for item in policies or ()
            if isinstance(item, Mapping) and item.get("id") == "DP-113"
        ),
        None,
    )
    if not isinstance(row, Mapping) or row.get("units") != "m":
        raise LabGeometryError(
            "dp113_policy_invalid",
            "data/battery/default_policy.yaml must declare DP-113 in metres",
        )
    return _lab_positive_scalar(row.get("value"), field="DP-113.value")


def _lab_positive_scalar(value: Any, *, field: str) -> float:
    code = "invalid_lab_gas_film_geometry"
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise LabGeometryError(
            code,
            f"lab_geometry.{field} must be finite and positive",
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LabGeometryError(
            code,
            f"lab_geometry.{field} must be finite and positive",
        ) from exc
    if not math.isfinite(number) or number <= 0.0:
        raise LabGeometryError(
            code,
            f"lab_geometry.{field} must be finite and positive",
        )
    return number


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


def _parse_lab_surface(
    index: int,
    raw: Any,
    *,
    allow_temperature_profiles: bool,
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
    temperature_C = _surface_temperature_C(
        raw,
        surface_id,
        temperature_profile=temperature_profile,
        allow_temperature_profiles=allow_temperature_profiles,
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


def _surface_temperature_C(
    raw: Mapping[str, Any],
    surface_id: str,
    *,
    temperature_profile: str,
    allow_temperature_profiles: bool,
) -> float | None:
    for key in ("temperature_C", "wall_temperature_C"):
        if key in raw:
            return _required_finite(raw[key], f"{surface_id}.{key}")
    if allow_temperature_profiles and temperature_profile:
        return None
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
