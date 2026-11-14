"""Furnace material catalog and max-temperature resolver."""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from simulator.scalar_boundary import is_declared_real_scalar
from simulator.yaml_cache import load_cached_safe_yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_FURNACE_MATERIALS_PATH = DATA_DIR / "furnace_materials.yaml"


class FurnaceMaterialCatalogLoadError(RuntimeError):
    """Typed refusal when the catalog cannot supply the furnace envelope."""

    def __init__(self, load_error: Exception) -> None:
        self.load_error = load_error
        super().__init__(f"furnace material catalog unavailable: {load_error}")


class InterfaceChemistryRefusal(ValueError):
    """Typed refusal for optional liner chemistry data."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail or reason)
        super().__init__(self.detail)


# RUNTIME TEMPERATURE ENVELOPE.
#
# The ceiling is DERIVED FROM THE CATALOG, not asserted here. It is the highest
# max_service_T_C among enabled materials, so the envelope always admits the best
# pipe the catalog actually ships. Previously this was the literal 2000.0, which
# silently derated zirconia_ysz (rated 2200 C for BOTH continuous and max service)
# to a 2000 C applied ceiling. That derate had no materials basis: the resolver's
# own comment justified it only as cross-layer agreement with the CampaignManager
# guard (BUG-076), and that guard's comment states outright that "a literature
# derivation is unestablished here" and that its cited research file is not in the
# repository. An undocumented constant was truncating a documented material rating.
#
# Deriving it keeps BUG-076's actual invariant intact -- resolver and CampaignManager
# still read ONE value, so they cannot disagree on admissibility -- while removing the
# arbitrary truncation. Adding a better-rated pipe to the catalog now raises the
# envelope automatically instead of being clipped by a number nobody can source.
#
# The FLOOR stays 1200.0 and stays asymmetric (fail loud, never clamp up), per the
# resolver's rationale: silently raising a sub-floor request would run the furnace
# HOTTER than asked, which is a rewrite of operator intent.
def _catalog_max_service_T_C() -> float:
    """Highest enabled max_service_T_C in the catalog; refuse if unavailable."""
    try:
        catalog = load_furnace_materials()
    except Exception as exc:
        raise FurnaceMaterialCatalogLoadError(exc) from exc
    # The YAML nests entries under a single "furnace_materials" key; tolerate both
    # that shape and an already-unwrapped mapping.
    if (
        isinstance(catalog, Mapping)
        and len(catalog) == 1
        and "furnace_materials" in catalog
    ):
        catalog = catalog["furnace_materials"]
    ratings = [
        float(entry["max_service_T_C"])
        for entry in (catalog or {}).values()
        if isinstance(entry, Mapping)
        and entry.get("enabled")
        and isinstance(entry.get("max_service_T_C"), (int, float))
    ]
    if not ratings:
        error = ValueError(
            "furnace material catalog has no enabled numeric max_service_T_C rating"
        )
        raise FurnaceMaterialCatalogLoadError(error) from error
    return max(ratings)


CERTIFIED_WALL_ANCHORED_FURNACE_MATERIALS = frozenset(
    {
        "dense_alumina_continuous",
        "dense_alumina_max",
        "zirconia_ysz",
        "plasma_sprayed_alumina",
        "fused_silica",
    }
)
ALLOWED_FURNACE_GROUNDING_TIERS = frozenset({"proxy-sintering"})
PROXY_FURNACE_GROUNDING_TIERS = frozenset({"proxy-sintering"})


@lru_cache(maxsize=4)
def load_furnace_materials(
    path: Path | str = DEFAULT_FURNACE_MATERIALS_PATH,
) -> dict[str, Any]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"required furnace material catalog missing: {source_path}")
    with source_path.open(encoding="utf-8") as handle:
        data = load_cached_safe_yaml(handle.read()) or {}
    catalog = data.get("furnace_materials")
    if not isinstance(catalog, dict):
        raise ValueError(f"furnace material catalog is malformed: {source_path}")
    for material_id, row in catalog.items():
        try:
            validate_furnace_material_grounding(str(material_id), row)
        except ValueError as exc:
            raise ValueError(
                f"furnace material catalog is malformed: {source_path}: {exc}"
            ) from exc
    return catalog


def load_interface_chemistry(
    material_id: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Load one optional interface chemistry record and its generated bulk.

    The furnace catalog remains loadable before generated artifacts exist.
    A selected material that points at a not-yet-generated rump gets a typed
    refusal here, so unrelated furnace settings and golden outputs stay stable.
    """

    materials = catalog if catalog is not None else load_furnace_materials()
    row = materials.get(str(material_id)) if isinstance(materials, Mapping) else None
    if not isinstance(row, Mapping):
        raise InterfaceChemistryRefusal(
            "furnace_material_missing", str(material_id)
        )
    raw_chemistry = row.get("interface_chemistry")
    if raw_chemistry is None:
        return None
    if not isinstance(raw_chemistry, Mapping):
        raise InterfaceChemistryRefusal(
            "interface_chemistry_invalid", str(material_id)
        )
    chemistry = dict(raw_chemistry)
    composition = chemistry.get("composition")
    composition_file = chemistry.get("composition_file")
    if not isinstance(composition, Mapping) or not composition:
        if not isinstance(composition_file, str) or not composition_file.strip():
            raise InterfaceChemistryRefusal("liner_composition_missing")
        source_path = Path(composition_file)
        if source_path.is_absolute():
            raise InterfaceChemistryRefusal(
                "interface_chemistry_path_invalid", composition_file
            )
        source_path = DATA_DIR.parent / source_path
        if not source_path.is_file():
            raise InterfaceChemistryRefusal(
                str(chemistry.get("missing_composition_reason") or "liner_composition_missing"),
                f"generated composition is absent: {source_path}",
            )
        try:
            generated = load_cached_safe_yaml(source_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 - expose typed catalog refusal
            raise InterfaceChemistryRefusal(
                "liner_composition_invalid", str(source_path)
            ) from exc
        if not isinstance(generated, Mapping):
            raise InterfaceChemistryRefusal(
                "liner_composition_invalid", str(source_path)
            )
        composition = generated.get("composition")
        if not isinstance(composition, Mapping) or not composition:
            raise InterfaceChemistryRefusal(
                "liner_composition_missing", str(source_path)
            )
        chemistry["composition"] = dict(composition)
        provenance = generated.get("provenance")
        if isinstance(provenance, Mapping):
            chemistry["generated_provenance"] = dict(provenance)

    citation = chemistry.get("citation")
    if not isinstance(citation, str) or not citation.strip():
        raise InterfaceChemistryRefusal("liner_composition_citation_missing")
    try:
        from simulator.liner_corrosion import validate_oxide_composition

        chemistry["composition"] = validate_oxide_composition(composition)
    except Exception as exc:
        if isinstance(exc, InterfaceChemistryRefusal):
            raise
        reason = getattr(exc, "reason", "liner_composition_invalid")
        raise InterfaceChemistryRefusal(str(reason), str(exc)) from exc
    return chemistry


def load_interface_invariant_table(
    material_id: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the optional Tier A invariant table or refuse with a typed reason."""

    chemistry = load_interface_chemistry(material_id, catalog=catalog)
    if chemistry is None:
        raise InterfaceChemistryRefusal("interface_chemistry_missing", material_id)
    table = chemistry.get("invariant_table")
    if isinstance(table, Mapping) and table:
        if table.get("loader") != "liner_corrosion_source_extracts":
            return dict(table)
        subsystems = table.get("subsystems")
        if (
            not isinstance(subsystems, list)
            or not subsystems
            or any(not isinstance(item, str) or not item.strip() for item in subsystems)
        ):
            raise InterfaceChemistryRefusal(
                "liner_invariant_subsystems_invalid", material_id
            )
        try:
            from simulator.liner_corrosion import load_liner_corrosion_invariants

            rows = load_liner_corrosion_invariants()
        except Exception as exc:  # noqa: BLE001 - keep source failures typed
            raise InterfaceChemistryRefusal(
                "liner_invariant_source_unavailable", str(exc)
            ) from exc
        requested = {str(item).strip() for item in subsystems}
        selected = [row for row in rows if row.get("subsystem") in requested]
        if not selected:
            raise InterfaceChemistryRefusal(
                "liner_invariant_rows_missing", ", ".join(sorted(requested))
            )
        return {
            "schema": "liner-source-invariants-v1",
            "source": table.get("loader"),
            "subsystems": sorted(requested),
            "acquisition_gaps": dict(table.get("acquisition_gaps") or {}),
            "invariants": selected,
        }
    table_path = chemistry.get("invariant_table_file")
    if not isinstance(table_path, str) or not table_path.strip():
        raise InterfaceChemistryRefusal("liner_invariant_table_missing", material_id)
    path = Path(table_path)
    if path.is_absolute():
        raise InterfaceChemistryRefusal("interface_chemistry_path_invalid", table_path)
    path = DATA_DIR.parent / path
    if not path.is_file():
        raise InterfaceChemistryRefusal(
            "liner_invariant_table_missing", f"invariant table is absent: {path}"
        )
    try:
        data = load_cached_safe_yaml(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - preserve a typed refusal boundary
        raise InterfaceChemistryRefusal("liner_invariant_table_invalid", str(path)) from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("invariants"), list):
        raise InterfaceChemistryRefusal("liner_invariant_table_invalid", str(path))
    return dict(data)


def require_interface_porosity(chemistry: Mapping[str, Any]) -> float:
    """Return declared sintered-body porosity for later infiltration models."""

    raw = chemistry.get("porosity_fraction")
    if raw is None:
        raise InterfaceChemistryRefusal("liner_porosity_missing")
    if not is_declared_real_scalar(raw):
        raise InterfaceChemistryRefusal("liner_porosity_invalid")
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise InterfaceChemistryRefusal("liner_porosity_invalid")
    return value


def validate_furnace_material_grounding(material_id: str, row: Any) -> None:
    """Validate grounding metadata required for enabled non-certified rows."""

    if not isinstance(row, Mapping) or row.get("enabled") is not True:
        return
    grounding = row.get("grounding")
    if grounding is None and material_id in CERTIFIED_WALL_ANCHORED_FURNACE_MATERIALS:
        return
    if not isinstance(grounding, Mapping):
        raise ValueError(
            f"{material_id}.grounding must be a mapping for enabled material"
        )

    tier = grounding.get("tier")
    if not isinstance(tier, str) or not tier.strip():
        raise ValueError(f"{material_id}.grounding.tier must be a non-empty string")
    tier = tier.strip()
    if tier not in ALLOWED_FURNACE_GROUNDING_TIERS:
        allowed = ", ".join(sorted(ALLOWED_FURNACE_GROUNDING_TIERS))
        raise ValueError(
            f"{material_id}.grounding.tier must be one of: {allowed}"
        )

    source = grounding.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{material_id}.grounding.source must be a non-empty string")

    if tier in PROXY_FURNACE_GROUNDING_TIERS:
        caveat = grounding.get("caveat")
        if not isinstance(caveat, str) or not caveat.strip():
            raise ValueError(f"{material_id} proxy tier needs grounding.caveat")


def resolve_furnace_max_T_C(
    material_id: str,
    requested_cap: float | int | None = None,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> float:
    return resolve_furnace_temperature_caps(
        material_id,
        requested_cap,
        catalog=catalog,
    )["effective_applied_ceiling_T_C"]


def resolve_furnace_temperature_caps(
    material_id: str,
    requested_cap: float | int | None = None,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    materials = _catalog_items(catalog or load_furnace_materials())
    material = materials.get(str(material_id))
    if not isinstance(material, Mapping):
        raise ValueError(f"unknown furnace material: {material_id}")
    if material.get("enabled") is not True:
        reason = str(material.get("not_selectable_reason") or "disabled")
        raise ValueError(f"{material_id} not selectable yet: {reason}")
    validate_furnace_material_grounding(str(material_id), material)

    material_max = _finite_float(
        material.get("max_service_T_C"),
        f"{material_id}.max_service_T_C",
    )
    requested_ceiling = material_max
    if requested_cap is None:
        effective_ceiling = material_max
    else:
        requested_ceiling = _finite_float(requested_cap, "requested_cap")
        effective_ceiling = min(requested_ceiling, material_max)
    # BUG-076: the resolver must never emit an effective ceiling the shared runtime
    # envelope FURNACE_MAX_T_BOUNDS_C rejects -- otherwise a resolver call site and the
    # CampaignManager envelope guard (simulator/campaigns.py:135-145) disagree on
    # admissibility, which is the cross-layer trap this bug is about. The two bounds are
    # handled ASYMMETRICALLY, on purpose:
    #   * Ceiling: a material rated above the envelope (e.g. zirconia_ysz at 2200 C)
    #     keeps its raw service_rating_T_C, but the applied ceiling is CLAMPED DOWN to
    #     the envelope max. Nobody requested the over-max temperature -- it is the
    #     material's rating, not operator intent -- so clamping down to the highest
    #     modelable temperature loses no intent and the derating stays visible
    #     (service_rating_T_C 2200 vs effective_applied_ceiling_T_C 2000).
    #   * Floor: a resolved ceiling below the envelope floor (a sub-floor *requested*
    #     cap, or a mis-catalogued enabled material) FAILS LOUD, never clamps up.
    #     Silently raising a sub-floor request would run the furnace HOTTER than the
    #     operator asked -- a silent rewrite of intent the mandate forbids.
    effective_ceiling = min(effective_ceiling, FURNACE_MAX_T_BOUNDS_C[1])
    if effective_ceiling < FURNACE_MAX_T_BOUNDS_C[0]:
        raise ValueError(
            f"{material_id}: resolved applied ceiling {effective_ceiling:.0f} C is below "
            f"the runtime envelope floor {FURNACE_MAX_T_BOUNDS_C[0]:.0f} C "
            f"(requested_cap={requested_cap}); not runtime-admissible"
        )
    return {
        "service_rating_T_C": material_max,
        "requested_ceiling_T_C": requested_ceiling,
        "effective_applied_ceiling_T_C": effective_ceiling,
    }


# Fallback used ONLY when a setpoints mapping names no pipe material and requests
# no explicit ceiling. It is not a material limit and not a derived figure -- it is
# the historic literal, kept so an incomplete setpoints file still runs. Any real
# vessel should reach here via `furnace_material`.
FURNACE_CEILING_FALLBACK_C = 1800.0


def setpoints_furnace_ceiling_C(
    setpoints: Mapping[str, Any],
    *,
    fallback_C: float = FURNACE_CEILING_FALLBACK_C,
) -> float:
    """Ceiling a setpoints mapping implies: explicit request, else pipe material.

    SINGLE SOURCE OF TRUTH for "how hot may this furnace run", so the campaign
    runner and the optimizer's thermal-window scheduler cannot disagree. They did:
    CampaignManager inherited the ceiling from `furnace_material` while
    evaluate._furnace_ceiling_C still read `setpoints.get("furnace_max_T_C", 1800.0)`,
    so an unpatched evaluation scheduled against 1800 C on a vessel the catalog rates
    to 2200 C (b-329).

    Resolution order, highest priority first:
      1. an explicit `furnace_max_T_C` (the operator's derate hook), returned AS
         GIVEN -- coercion, envelope validation and derating against the material
         belong to the caller, which is why this returns a bare float and raises
         nothing but the float() TypeError on junk;
      2. `furnace_material`'s max_service_T_C from data/furnace_materials.yaml;
      3. `fallback_C`.

    Note `.get(key, default)` is NOT equivalent to step 1: it supplies the default
    only when the key is ABSENT, so an explicit `furnace_max_T_C: null` -- which is
    how setpoints.yaml spells "inherit" -- reaches float(None) and raises. That
    exact call is what this function replaces.
    """
    requested = setpoints.get("furnace_max_T_C")
    if requested is not None:
        return float(requested)
    material = setpoints.get("furnace_material")
    if material:
        material_ceiling = resolve_furnace_max_T_C(str(material))
        if material_ceiling is not None:
            return float(material_ceiling)
    return float(fallback_C)


def _catalog_items(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = catalog.get("furnace_materials")
    if isinstance(nested, Mapping):
        return nested
    return catalog


def _finite_float(value: Any, label: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


# --- envelope rebind (MUST be last) -------------------------------------------
# Deferred to end-of-module ON PURPOSE. load_furnace_materials() calls
# validate_furnace_material_grounding(), so evaluating this any earlier raises
# NameError. Keep the calculation after the validator so failures propagate as
# FurnaceMaterialCatalogLoadError instead of becoming an ordinary temperature.
FURNACE_MAX_T_BOUNDS_C = (1200.0, _catalog_max_service_T_C())
