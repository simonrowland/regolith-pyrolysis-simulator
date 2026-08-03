"""Store-driven single-species reproduction battery (t-512).

Enumerates **ADOPTED** extract-store observations of type ``psat_series``,
``rate_series``, and ``activity_coefficient`` (priority winners from
``tools/extract_merge.py`` / VALUE-PRECEDENCE), runs the engine at each
observation's own conditions, and records comparison residuals via
:mod:`simulator.diagnostic_helpers.reproduction_compare`.

**Doctrine** (``headline-accuracy-is-the-product``): residual / error bars
are the deliverable. Engine refusals surface as typed skips (never silent
pass). Mismatches are FINDINGs with honest residuals — tolerances are never
weakened to go green.

**Geometry note:** ``tools/motzfeldt.py`` is not present in this tree.
Multi-orifice Motzfeldt inversion is therefore **not** applied. When
equipment geometry is absent the battery assumes pure-component / unit
activity oxide melt at the reported T and a default pO₂ boundary (see
``DEFAULT_PO2_BAR`` and cell-material map). Orifice area / Clausing factor,
when present on the extract, feed Knudsen apparatus rates only.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.chemistry.langmuir_knudsen import (
    grounded_alpha,
    knudsen_effusion_molar_flux,
)
from simulator.diagnostic_helpers.reproduction_compare import (
    COMPARISON_STATUSES,
    ComparisonRecord,
    compare_values,
    records_to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTS_DIR = REPO_ROOT / "data" / "literature" / "extracts"
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
MODEL_LIMITATIONS_PATH = REPO_ROOT / "docs" / "model-limitations.md"
MOTZFELDT_TOOL_PATH = REPO_ROOT / "tools" / "motzfeldt.py"
TOOLS_DIR = REPO_ROOT / "tools"

TARGET_TYPES = frozenset({"psat_series", "rate_series", "activity_coefficient"})

# Documented defaults when an observation carries no usable numeric uncertainty.
# These are stated per-observation on the comparison record (defaulted=true).
DEFAULT_PSAT_UNCERTAINTY: dict[str, Any] = {
    "kind": "log10_decades",
    "value": 0.5,
    "defaulted": True,
    "rationale": (
        "extract observation has no usable numeric uncertainty; "
        "default half-dex high-T vapor-pressure envelope (t-512)"
    ),
}
DEFAULT_ALPHA_UNCERTAINTY: dict[str, Any] = {
    "kind": "absolute",
    "value": 0.05,
    "defaulted": True,
    "rationale": (
        "extract observation has no usable numeric uncertainty; "
        "default absolute α envelope ±0.05 (t-512)"
    ),
}
DEFAULT_ACTIVITY_UNCERTAINTY: dict[str, Any] = {
    "kind": "relative_fraction",
    "value": 0.5,
    "defaulted": True,
    "rationale": (
        "extract observation has no usable numeric uncertainty; "
        "default 50% relative activity/γ envelope (t-512)"
    ),
}

# When equipment.cell_material is present but no chamber pO2 is stated,
# map common KEMS cell materials to an effective pO2 boundary (bar).
# Halwax 2024 KEMS presets use 1e-8 bar as the assumed control floor.
CELL_MATERIAL_PO2_BAR: dict[str, float] = {
    "iridium": 1.0e-8,
    "ir": 1.0e-8,
    "platinum": 1.0e-8,
    "pt": 1.0e-8,
    "molybdenum": 1.0e-8,
    "mo": 1.0e-8,
    "tungsten": 1.0e-8,
    "w": 1.0e-8,
    "graphite": 1.0e-10,
    "c": 1.0e-10,
}
DEFAULT_PO2_BAR = 1.0e-8

# Oxide melt wt% composition used when an observation does not state a
# condensed-phase multi-component melt (unit activity pure-oxide assumption).
PARENT_OXIDE_BY_ENGINE_SPECIES: dict[str, str] = {
    "Na": "Na2O",
    "K": "K2O",
    "Mg": "MgO",
    "Fe": "FeO",
    "Ca": "CaO",
    "Al": "Al2O3",
    "Si": "SiO2",
    "SiO": "SiO2",
    "Ti": "TiO2",
    "Cr": "Cr2O3",
    "CrO2": "Cr2O3",
    "Mn": "MnO",
}

ROLLUP_BEGIN = "<!-- BEGIN t-512 extract-store reproduction rollup -->"
ROLLUP_END = "<!-- END t-512 extract-store reproduction rollup -->"

_TYPED_SKIP_PREFIX = "typed-refusal:"


class ExtractReproductionError(RuntimeError):
    """Structural battery failure (not an engine/literature residual)."""


@dataclass(frozen=True)
class AdoptedObservation:
    """One priority-winner observation drawn from the extract store."""

    species_id: str
    source_id: str
    observation_id: str
    obs_type: str
    review_status: str | None
    phase: str | None
    regime: str | None
    standard_state: str | None
    T_range_K: Sequence[float] | None
    units: str | None
    uncertainty: Any
    locator: Any
    values: Mapping[str, Any]
    equipment: Mapping[str, Any]
    disagreement_dex: float | None
    is_priority_winner: bool
    geometry_assumption: str

    @property
    def case_id(self) -> str:
        return f"{self.source_id}::{self.observation_id}"

    def param_id(self) -> str:
        return f"{self.species_id}__{self.source_id}__{self.observation_id}"


@dataclass
class ObservationEvaluation:
    """Full evaluation of one adopted observation (0..N comparison points)."""

    observation: AdoptedObservation
    records: list[ComparisonRecord] = field(default_factory=list)
    skip_reason: str | None = None
    findings: list[str] = field(default_factory=list)
    runtime_notes: list[str] = field(default_factory=list)

    @property
    def statuses(self) -> list[str]:
        return [r.status for r in self.records]


def _ensure_tools_path() -> None:
    tools = str(TOOLS_DIR)
    if tools not in sys.path:
        sys.path.insert(0, tools)


def motzfeldt_available() -> bool:
    return MOTZFELDT_TOOL_PATH.is_file()


def geometry_assumption_text() -> str:
    if motzfeldt_available():
        return "tools/motzfeldt.py present; multi-orifice geometry may be applied"
    return (
        "tools/motzfeldt.py absent — no Motzfeldt multi-orifice inversion; "
        "pure-component / unit-activity oxide melt at reported T; "
        "default pO2 boundary unless equipment.cell_material or "
        "equipment.chamber_pressure supplies one"
    )


def load_vapor_pressure_data(
    path: Path | None = None,
) -> dict[str, Any]:
    p = path or VAPOR_PRESSURES_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ExtractReproductionError(f"vapor_pressures.yaml is not a mapping: {p}")
    return dict(data)


def load_adopted_observations(
    *,
    extracts_dir: Path | None = None,
) -> list[AdoptedObservation]:
    """Return ADOPTED (priority-winner) observations of the three target types."""

    _ensure_tools_path()
    import extract_merge as em  # noqa: WPS433 — tools/ is not a package

    directory = extracts_dir or EXTRACTS_DIR
    extracts = em.load_extracts(directory)
    view = em.build_by_species(extracts)
    geometry = geometry_assumption_text()

    # Index disagreement_dex by (species, type, observation_id) for rollup.
    dex_by_key: dict[tuple[str, str, str], float | None] = {}
    for species_id, block in (view.get("species") or {}).items():
        for group in block.get("observable_groups") or []:
            otype = str(group.get("type") or "")
            dex = group.get("disagreement_dex")
            winner = group.get("priority_winner_source_id")
            for sid in group.get("source_ids") or []:
                if winner is not None and sid == winner:
                    # attach dex to all winner-source obs of this type later
                    dex_by_key[(str(species_id), otype, str(sid))] = (
                        float(dex) if dex is not None else None
                    )

    adopted: list[AdoptedObservation] = []
    for species_id, block in sorted((view.get("species") or {}).items()):
        for obs in block.get("observations") or []:
            otype = str(obs.get("type") or "")
            if otype not in TARGET_TYPES:
                continue
            if obs.get("review_status") == "rejected":
                continue
            if not obs.get("is_priority_winner"):
                continue
            values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
            equipment = (
                obs.get("equipment")
                if isinstance(obs.get("equipment"), Mapping)
                else {}
            )
            t_range = obs.get("T_range_K")
            if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
                try:
                    t_range_out: Sequence[float] | None = (
                        float(t_range[0]),
                        float(t_range[1]),
                    )
                except (TypeError, ValueError):
                    t_range_out = None
            else:
                t_range_out = None
            source_id = str(obs.get("source_id") or "")
            dex = dex_by_key.get((str(species_id), otype, source_id))
            adopted.append(
                AdoptedObservation(
                    species_id=str(species_id),
                    source_id=source_id,
                    observation_id=str(obs.get("observation_id") or ""),
                    obs_type=otype,
                    review_status=(
                        str(obs.get("review_status"))
                        if obs.get("review_status") is not None
                        else None
                    ),
                    phase=str(obs["phase"]) if obs.get("phase") is not None else None,
                    regime=(
                        str(obs["regime"]) if obs.get("regime") is not None else None
                    ),
                    standard_state=(
                        str(obs["standard_state"])
                        if obs.get("standard_state") is not None
                        else None
                    ),
                    T_range_K=t_range_out,
                    units=str(obs["units"]) if obs.get("units") is not None else None,
                    uncertainty=obs.get("uncertainty"),
                    locator=obs.get("locator"),
                    values=dict(values),
                    equipment=dict(equipment),
                    disagreement_dex=dex,
                    is_priority_winner=True,
                    geometry_assumption=geometry,
                )
            )
    adopted.sort(key=lambda row: (row.species_id, row.source_id, row.observation_id))
    return adopted


def _equipment_scalar(equipment: Mapping[str, Any], field: str) -> Any:
    block = equipment.get(field)
    if isinstance(block, Mapping):
        return block.get("value")
    return None


def resolve_pO2_bar(obs: AdoptedObservation) -> tuple[float, str]:
    """Return (pO2_bar, provenance note)."""

    chamber = _equipment_scalar(obs.equipment, "chamber_pressure")
    if chamber is not None:
        try:
            p = float(chamber)
            if math.isfinite(p) and p >= 0.0:
                # chamber_pressure may be total P; treat as O2-equivalent only
                # when clearly labelled — otherwise use as order-of-magnitude floor.
                # Unit match order is longest / most-specific first: "mbar" contains
                # the substring "bar", so a naive `"bar" in units` check would make
                # the mbar branch dead code and silently leave mbar unconverted
                # (1000× too high). Prefer equality / word-boundary-ish tokens.
                units = ""
                block = obs.equipment.get("chamber_pressure")
                if isinstance(block, Mapping):
                    units = str(block.get("units") or "").lower().strip()
                if units in {"mbar", "millibar"} or "mbar" in units:
                    return max(p / 1000.0, 1.0e-30), "equipment.chamber_pressure (mbar→bar)"
                if units in {"pa", "pascal"} or units == "pa" or re.search(
                    r"(^|[^a-z])pa([^a-z]|$)", units
                ):
                    return max(p / 1.0e5, 1.0e-30), "equipment.chamber_pressure (Pa→bar)"
                if units in {"bar"} or re.search(r"(^|[^a-z])bar([^a-z]|$)", units):
                    return p, "equipment.chamber_pressure (bar)"
        except (TypeError, ValueError):
            pass

    material = _equipment_scalar(obs.equipment, "cell_material")
    if material is not None:
        key = re.sub(r"[^a-z0-9]+", "", str(material).strip().lower())
        for name, pO2 in CELL_MATERIAL_PO2_BAR.items():
            if name in key or key == name:
                return pO2, f"cell_material={material!r} → default pO2={pO2:g} bar"

    return DEFAULT_PO2_BAR, f"default pO2={DEFAULT_PO2_BAR:g} bar (no equipment pO2)"


def _engine_species_candidates(obs: AdoptedObservation) -> list[str]:
    """Ordered species labels to try against the vapor-pressure / alpha surface."""

    values = obs.values
    candidates: list[str] = []
    for key in ("gas_species", "species", "engine_species"):
        raw = values.get(key)
        if raw is not None and str(raw).strip():
            candidates.append(str(raw).strip())
    # Condensed pure-metal reservoirs often equal the vapor monatom.
    reservoir = values.get("condensed_reservoir")
    if isinstance(reservoir, str) and reservoir.strip():
        base = reservoir.strip().split("(")[0].strip()
        if base:
            candidates.append(base)
    candidates.append(obs.species_id)
    # Collapse composite store ids (e.g. VO_VO2, Yb_metal_and_YbO).
    sid = obs.species_id
    if "_" in sid:
        for part in re.split(r"[_/]+", sid):
            part = part.strip()
            if part and part.lower() not in {"metal", "and", "ladder", "n"}:
                candidates.append(part)
    # Dedup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _literature_pressure_points(
    obs: AdoptedObservation,
) -> tuple[list[dict[str, Any]] | None, str | None, list[dict[str, Any]]]:
    """Return (points, skip_reason, drops).

    ``drops`` records point-level silent-drop candidates (unrecognized pressure
    keys, non-finite T/P, non-mapping rows) so coverage erosion is never
    invisible — callers must surface each drop as a gap record or runtime note.
    """

    values = obs.values
    drops: list[dict[str, Any]] = []
    points_raw = values.get("points")
    if isinstance(points_raw, list) and points_raw:
        out: list[dict[str, Any]] = []
        for idx, pt in enumerate(points_raw):
            if not isinstance(pt, Mapping):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "point_not_mapping",
                        "raw_type": type(pt).__name__,
                    }
                )
                continue
            T = pt.get("T_K") or pt.get("temperature_K") or pt.get("T")
            P = None
            units_hint = "Pa"
            recognized_pressure = False
            for key, scale, u in (
                ("p_Pa", 1.0, "Pa"),
                ("P_Pa", 1.0, "Pa"),
                ("pressure_Pa", 1.0, "Pa"),
                ("p_bar", 1.0e5, "Pa"),
                ("P_bar", 1.0e5, "Pa"),
                ("p_mmHg", 133.322368, "Pa"),
                ("P_mmHg", 133.322368, "Pa"),
                ("p_torr", 133.322368, "Pa"),
                ("P_torr", 133.322368, "Pa"),
                ("p_atm", 101325.0, "Pa"),
                ("P_atm", 101325.0, "Pa"),
                ("log10_P_Pa", None, "Pa"),
            ):
                if key not in pt:
                    continue
                recognized_pressure = True
                try:
                    raw = float(pt[key])
                except (TypeError, ValueError):
                    drops.append(
                        {
                            "point_index": idx,
                            "reason": "pressure_not_numeric",
                            "key": key,
                            "raw": pt.get(key),
                        }
                    )
                    continue
                if key.startswith("log10_"):
                    P = 10.0**raw
                else:
                    P = raw * scale
                units_hint = u
                break
            if T is None or P is None:
                # Visibility: if *some* points parse, a silent drop here would
                # shrink "N pts" without a record. Always emit a drop note.
                if not recognized_pressure and any(
                    k.lower().startswith(("p", "pressure", "log10"))
                    for k in pt.keys()
                    if isinstance(k, str)
                ):
                    reason = "unrecognized_pressure_key"
                elif T is None:
                    reason = "missing_temperature"
                elif not recognized_pressure:
                    reason = "missing_pressure"
                else:
                    reason = "pressure_unusable"
                drops.append(
                    {
                        "point_index": idx,
                        "reason": reason,
                        "keys": sorted(str(k) for k in pt.keys()),
                    }
                )
                continue
            try:
                T_f = float(T)
                P_f = float(P)
            except (TypeError, ValueError):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "T_or_P_not_numeric",
                        "T": T,
                        "P": P,
                    }
                )
                continue
            if not (math.isfinite(T_f) and math.isfinite(P_f) and T_f > 0.0 and P_f > 0.0):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "T_or_P_non_finite_or_non_positive",
                        "T_K": T_f,
                        "P_Pa": P_f,
                    }
                )
                continue
            out.append(
                {
                    "T_K": T_f,
                    "P_Pa": P_f,
                    "units": units_hint,
                    "point_index": idx,
                    "note": pt.get("note"),
                }
            )
        if out:
            return out, None, drops
        if drops:
            return None, "no_usable_psat_payload", drops

    # Antoine / log10 coefficients → evaluate at T-range mid + endpoints.
    # Accept A_Pa-only runtime coefficients (Pa-converted Antoine). The value
    # path already preferred A_Pa; the presence guard must match or Yb-style
    # A_Pa payloads are mis-labelled no_usable_psat_payload.
    coef = values.get("runtime_coefficients") or values.get("coefficients")
    source_form = str(
        values.get("runtime_form") or values.get("source_form") or obs.units or ""
    )
    has_A = isinstance(coef, Mapping) and (
        coef.get("A") is not None or coef.get("A_Pa") is not None
    )
    if has_A and coef.get("B") is not None:  # type: ignore[union-attr]
        try:
            A = float(coef.get("A_Pa", coef.get("A")))  # type: ignore[union-attr]
            B = float(coef["B"])  # type: ignore[index]
            C = float(coef.get("C") or 0.0)  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return None, "coefficients_not_numeric", drops
        # Prefer runtime_coefficients which already convert to Pa.
        use_pa = "A_Pa" in coef or "P_Pa" in source_form or "log10(P_Pa)" in source_form
        use_bar = (not use_pa) and ("P_bar" in source_form or "log10(P_bar)" in source_form)
        use_mmhg = (not use_pa) and (
            "mmHg" in source_form or "P_mmHg" in source_form or "mm Hg" in source_form
        )
        t_lo = t_hi = None
        if obs.T_range_K is not None:
            t_lo, t_hi = float(obs.T_range_K[0]), float(obs.T_range_K[1])
        elif isinstance(values.get("valid_range_K"), (list, tuple)) and len(
            values["valid_range_K"]
        ) == 2:
            try:
                t_lo = float(values["valid_range_K"][0])
                t_hi = float(values["valid_range_K"][1])
            except (TypeError, ValueError):
                t_lo = t_hi = None
        # Hab64-style runtime_coefficients also ship valid_range via
        # values["valid_range_K"] or T_range on the observation; some extracts
        # only put range on values.valid_range_source narrative — fall through.
        if t_lo is None or t_hi is None:
            # Single mid default only when range unknown — still need a T.
            return None, "coefficients_without_temperature_range", drops
        temps = sorted({t_lo, 0.5 * (t_lo + t_hi), t_hi})
        out = []
        for T in temps:
            if T + C <= 0.0:
                drops.append(
                    {
                        "point_index": None,
                        "reason": "antoine_T_plus_C_non_positive",
                        "T_K": T,
                        "C": C,
                    }
                )
                continue
            log10_p = A - B / (T + C)
            if use_pa or "A_Pa" in coef:
                P = 10.0**log10_p
            elif use_bar:
                P = (10.0**log10_p) * 1.0e5
            elif use_mmhg:
                P = (10.0**log10_p) * 133.322368
            else:
                # Behrens-style log10(P_bar) without explicit runtime conversion.
                if "bar" in source_form.lower():
                    P = (10.0**log10_p) * 1.0e5
                elif "mm" in source_form.lower():
                    P = (10.0**log10_p) * 133.322368
                else:
                    # Prefer Pa-like A magnitude (A~10) vs bar (A~4-6) heuristic.
                    P = 10.0**log10_p if A >= 7.0 else (10.0**log10_p) * 1.0e5
            if not (math.isfinite(P) and P > 0.0):
                drops.append(
                    {
                        "point_index": None,
                        "reason": "antoine_P_non_finite",
                        "T_K": T,
                        "log10_p": log10_p,
                    }
                )
                continue
            out.append({"T_K": float(T), "P_Pa": float(P), "units": "Pa", "note": "antoine_eval"})
        if out:
            return out, None, drops

    # Pointer / pure_Psat anchor without numeric payload.
    if values.get("quantity") or values.get("independence_note") or values.get(
        "semantics"
    ):
        return None, "pointer_or_anchor_without_numeric_points", drops
    return None, "no_usable_psat_payload", drops


def _literature_alpha_points(
    obs: AdoptedObservation,
) -> tuple[list[dict[str, Any]] | None, str | None, list[dict[str, Any]]]:
    """Return (points, skip_reason, drops).

    Rate/flux-only rows in a mixed series are not comparable without Motzfeldt
    geometry — each such drop is recorded so "N pts" cannot silently shrink.
    """

    values = obs.values
    drops: list[dict[str, Any]] = []
    series = values.get("series")
    if isinstance(series, list) and series:
        out: list[dict[str, Any]] = []
        for idx, pt in enumerate(series):
            if not isinstance(pt, Mapping):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "point_not_mapping",
                        "raw_type": type(pt).__name__,
                    }
                )
                continue
            T = pt.get("T_K") or pt.get("temperature_K")
            alpha = pt.get("alpha")
            if T is None or alpha is None:
                # rate/flux-only point — cannot invert without geometry.
                if pt.get("rate") is not None or pt.get("flux") is not None:
                    drops.append(
                        {
                            "point_index": idx,
                            "reason": "rate_or_flux_without_alpha",
                            "keys": sorted(str(k) for k in pt.keys()),
                        }
                    )
                    continue
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "missing_alpha_or_temperature",
                        "keys": sorted(str(k) for k in pt.keys()),
                    }
                )
                continue
            try:
                T_f = float(T)
                a_f = float(alpha)
            except (TypeError, ValueError):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "alpha_or_T_not_numeric",
                        "T": T,
                        "alpha": alpha,
                    }
                )
                continue
            if not (math.isfinite(T_f) and math.isfinite(a_f) and T_f > 0.0):
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "alpha_or_T_non_finite",
                        "T_K": T_f,
                        "alpha": a_f,
                    }
                )
                continue
            sigma = pt.get("sigma") or pt.get("uncertainty") or pt.get("alpha_sigma")
            try:
                sigma_f = float(sigma) if sigma is not None else None
            except (TypeError, ValueError):
                sigma_f = None
            out.append(
                {
                    "T_K": T_f,
                    "alpha": a_f,
                    "sigma": sigma_f,
                    "point_index": idx,
                }
            )
        if out:
            return out, None, drops
        if series:
            return None, "rate_series_without_alpha_and_no_motzfeldt_geometry", drops
    if values.get("alpha") is not None:
        T = None
        if obs.T_range_K is not None:
            T = 0.5 * (float(obs.T_range_K[0]) + float(obs.T_range_K[1]))
        if T is None:
            return None, "alpha_without_temperature", drops
        try:
            return (
                [{"T_K": float(T), "alpha": float(values["alpha"]), "sigma": None}],
                None,
                drops,
            )
        except (TypeError, ValueError):
            return None, "alpha_not_numeric", drops
    return None, "no_usable_rate_series_payload", drops


def resolve_uncertainty(
    obs: AdoptedObservation,
    *,
    point: Mapping[str, Any] | None = None,
    kind_hint: str,
) -> dict[str, Any]:
    """Build a compare_values uncertainty dict; document defaults per-observation."""

    # Point-level sigma for alpha series.
    if point is not None and point.get("sigma") is not None:
        try:
            sigma = float(point["sigma"])
            if math.isfinite(sigma) and sigma >= 0.0:
                return {
                    "kind": "absolute",
                    "value": sigma,
                    "defaulted": False,
                    "source": "point.sigma",
                }
        except (TypeError, ValueError):
            pass

    unc = obs.uncertainty
    if isinstance(unc, Mapping):
        if unc.get("kind") in {"absolute", "relative_fraction", "log10_decades"} and unc.get(
            "value"
        ) is not None:
            try:
                return {
                    "kind": str(unc["kind"]),
                    "value": float(unc["value"]),
                    "defaulted": False,
                    "source": "observation.uncertainty",
                }
            except (TypeError, ValueError):
                pass
        # Coefficient A uncertainty → log10 decades for Antoine.
        for key in ("A_uncertainty", "log10_P_uncertainty", "dex", "disagreement_dex"):
            if unc.get(key) is not None:
                try:
                    v = float(unc[key])
                    if math.isfinite(v) and v >= 0.0:
                        return {
                            "kind": "log10_decades",
                            "value": v,
                            "defaulted": False,
                            "source": f"observation.uncertainty.{key}",
                        }
                except (TypeError, ValueError):
                    pass
        # Relative percent.
        for key in ("relative", "relative_fraction", "rel"):
            if unc.get(key) is not None:
                try:
                    v = float(unc[key])
                    if v > 1.0:
                        v = v / 100.0
                    return {
                        "kind": "relative_fraction",
                        "value": v,
                        "defaulted": False,
                        "source": f"observation.uncertainty.{key}",
                    }
                except (TypeError, ValueError):
                    pass

    # Propagate store-level disagreement_dex when present.
    if obs.disagreement_dex is not None and math.isfinite(obs.disagreement_dex):
        return {
            "kind": "log10_decades",
            "value": float(obs.disagreement_dex),
            "defaulted": False,
            "source": "extract_merge.disagreement_dex",
        }

    if kind_hint == "psat":
        return dict(DEFAULT_PSAT_UNCERTAINTY)
    if kind_hint == "alpha":
        return dict(DEFAULT_ALPHA_UNCERTAINTY)
    return dict(DEFAULT_ACTIVITY_UNCERTAINTY)


def _engine_pure_psat_pa(
    species: str,
    T_K: float,
    vapor_pressure_data: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    """Pure-component Antoine path (wall/condensation helper)."""

    from simulator.condensation import _try_antoine_psat_pa

    try:
        P, refused = _try_antoine_psat_pa(
            species,
            T_K,
            vapor_pressure_data=vapor_pressure_data,
        )
    except Exception as exc:  # noqa: BLE001 — surface as typed refusal
        return None, f"pure_psat_exception:{type(exc).__name__}:{exc}"
    if refused:
        return None, "pure_psat_out_of_certified_range"
    if P is None:
        return None, "pure_psat_unsupported_species"
    if not (math.isfinite(float(P)) and float(P) >= 0.0):
        return None, "pure_psat_non_finite"
    return float(P), None


def _engine_melt_psat_pa(
    species: str,
    T_K: float,
    pO2_bar: float,
    vapor_pressure_data: Mapping[str, Any],
    *,
    oxide: str | None = None,
) -> tuple[float | None, str | None, dict[str, Any]]:
    """Builtin vapor-pressure provider over a pure parent-oxide melt."""

    oxide_formula = oxide or PARENT_OXIDE_BY_ENGINE_SPECIES.get(species)
    if oxide_formula is None:
        return None, "no_parent_oxide_for_species", {}
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {oxide_formula: 100.0}},
            species_formula_registry={},
        ),
        temperature_C=float(T_K) - 273.15,
        pressure_bar=max(float(pO2_bar), 1.0e-30),
        fO2_log=math.log10(max(float(pO2_bar), 1.0e-30)),
        control_inputs={"pO2_bar": float(pO2_bar)},
    )
    try:
        result = provider.dispatch(request)
    except Exception as exc:  # noqa: BLE001
        return None, f"provider_exception:{type(exc).__name__}:{exc}", {}
    status = str(result.status)
    diagnostic = dict(result.diagnostic or {})
    runtime = {
        "provider_id": getattr(provider, "name", "builtin-vapor-pressure"),
        "provider_status": status,
        "temperature_K": float(T_K),
        "pO2_bar": float(pO2_bar),
        "oxide": oxide_formula,
        "vapor_pressures_Pa": dict(diagnostic.get("vapor_pressures_Pa") or {}),
        "warnings": list(result.warnings or ()),
    }
    if status != "ok":
        return None, f"provider_status:{status}", runtime
    surface = dict(diagnostic.get("vapor_pressures_Pa") or {})
    if species not in surface or surface[species] is None:
        return None, f"unsupported_speciation:{species}", runtime
    try:
        P = float(surface[species])
    except (TypeError, ValueError):
        return None, f"non_numeric_pressure:{species}", runtime
    if not math.isfinite(P):
        return None, f"non_finite_pressure:{species}", runtime
    return P, None, runtime


def _engine_alpha(
    species: str,
    T_K: float,
) -> tuple[float | None, str | None, dict[str, Any]]:
    try:
        value, ctx = grounded_alpha(species, float(T_K))
    except KeyError as exc:
        return None, f"alpha_unsupported_species:{exc}", {}
    except Exception as exc:  # noqa: BLE001
        return None, f"alpha_exception:{type(exc).__name__}:{exc}", {}
    if value is None or not math.isfinite(float(value)):
        return None, "alpha_non_finite", dict(ctx)
    return float(value), None, dict(ctx)


def evaluate_observation(
    obs: AdoptedObservation,
    *,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> ObservationEvaluation:
    """Run the engine against one adopted observation; never silent-pass."""

    vp_data = vapor_pressure_data or load_vapor_pressure_data()
    evaluation = ObservationEvaluation(observation=obs)
    evaluation.runtime_notes.append(obs.geometry_assumption)
    pO2, pO2_note = resolve_pO2_bar(obs)
    evaluation.runtime_notes.append(pO2_note)

    if obs.obs_type == "psat_series":
        return _evaluate_psat(obs, evaluation, vp_data, pO2)
    if obs.obs_type == "rate_series":
        return _evaluate_rate(obs, evaluation, vp_data, pO2)
    if obs.obs_type == "activity_coefficient":
        return _evaluate_activity(obs, evaluation)
    evaluation.skip_reason = f"unknown_observation_type:{obs.obs_type}"
    return evaluation


def _compare_point(
    *,
    obs: AdoptedObservation,
    observable_id: str,
    species: str | None,
    coordinate: Mapping[str, Any],
    expected: float | None,
    uncertainty: Mapping[str, Any] | None,
    actual: float | None,
    units: str,
    runtime: Mapping[str, Any],
    unsupported_speciation: bool = False,
    out_of_domain: bool = False,
    assumed_input: bool = False,
) -> ComparisonRecord:
    # compare_values requires uncertainty whenever expected is numeric.
    unc = uncertainty
    if expected is not None and unc is None:
        unc = dict(DEFAULT_PSAT_UNCERTAINTY)
    return compare_values(
        case_id=obs.case_id,
        source_id=obs.source_id,
        observable_id=observable_id,
        species=species,
        coordinate=dict(coordinate),
        expected_value=expected,
        expected_uncertainty=dict(unc) if unc is not None else None,
        actual_value=actual,
        units=units,
        evidence_scope="extract-store-adopted",
        source_locator=obs.locator if obs.locator is not None else {},
        recipe={
            "species_id": obs.species_id,
            "observation_id": obs.observation_id,
            "type": obs.obs_type,
            "geometry_assumption": obs.geometry_assumption,
        },
        observation={
            "source_id": obs.source_id,
            "observation_id": obs.observation_id,
            "type": obs.obs_type,
            "values": dict(obs.values),
            "uncertainty": obs.uncertainty,
        },
        runtime=dict(runtime),
        unsupported_speciation=unsupported_speciation,
        assumed_input=assumed_input,
        out_of_domain=out_of_domain,
    )


def _emit_point_drop_records(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    drops: Sequence[Mapping[str, Any]],
    *,
    units: str,
    extra_runtime: Mapping[str, Any] | None = None,
) -> None:
    """Surface each point-level drop as a gap record + runtime note (no silent shrink)."""

    for drop in drops:
        idx = drop.get("point_index")
        reason = str(drop.get("reason") or "point_dropped")
        oid = (
            f"{obs.observation_id}:dropped_point:{idx}"
            if idx is not None
            else f"{obs.observation_id}:dropped_point"
        )
        runtime: dict[str, Any] = {
            "skip_reason": f"point_drop:{reason}",
            "drop": dict(drop),
        }
        if extra_runtime:
            runtime.update(dict(extra_runtime))
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=oid,
                species=obs.species_id,
                coordinate={"window": "point-drop", "point_index": idx},
                expected=None,
                uncertainty=None,
                actual=None,
                units=units,
                runtime=runtime,
            )
        )
        evaluation.runtime_notes.append(
            f"point drop idx={idx!r} reason={reason} detail={dict(drop)}"
        )


def _evaluate_psat(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    vp_data: Mapping[str, Any],
    pO2: float,
) -> ObservationEvaluation:
    points, skip, drops = _literature_pressure_points(obs)
    if drops:
        _emit_point_drop_records(
            obs,
            evaluation,
            drops,
            units=str(obs.units or "Pa"),
            extra_runtime={"pO2_bar": pO2},
        )
    if skip is not None:
        # Still emit a typed unsupported-observable record so the battery
        # never silently passes an adopted observation.
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=f"{obs.observation_id}:payload",
                species=obs.species_id,
                coordinate={"window": "payload-absent"},
                expected=None,
                uncertainty=None,
                actual=None,
                units=str(obs.units or "Pa"),
                runtime={"skip_reason": skip, "pO2_bar": pO2, "n_point_drops": len(drops)},
            )
        )
        evaluation.skip_reason = skip
        evaluation.runtime_notes.append(f"psat payload skip: {skip}")
        return evaluation

    assert points is not None
    candidates = _engine_species_candidates(obs)
    any_numeric = False
    last_refusal: str | None = None
    for pt in points:
        T_K = float(pt["T_K"])
        expected = float(pt["P_Pa"])
        unc = resolve_uncertainty(obs, point=pt, kind_hint="psat")
        actual: float | None = None
        runtime: dict[str, Any] = {
            "temperature_K": T_K,
            "pO2_bar": pO2,
            "candidates": candidates,
            "geometry_assumption": obs.geometry_assumption,
        }
        unsupported = False
        out_of_domain = False
        matched_species: str | None = None

        for species in candidates:
            # Prefer pure-component path for pure-metal / pure-solid reservoirs.
            P_pure, refuse_pure = _engine_pure_psat_pa(species, T_K, vp_data)
            if P_pure is not None:
                actual = P_pure
                matched_species = species
                runtime["engine_path"] = "pure_component_antoine"
                runtime["species"] = species
                break
            if refuse_pure:
                last_refusal = refuse_pure
                runtime.setdefault("pure_refusals", {})[species] = refuse_pure

            P_melt, refuse_melt, melt_rt = _engine_melt_psat_pa(
                species, T_K, pO2, vp_data
            )
            runtime.setdefault("melt_attempts", {})[species] = {
                "refusal": refuse_melt,
                **{k: melt_rt.get(k) for k in ("provider_status", "oxide", "warnings")},
            }
            if P_melt is not None:
                actual = P_melt
                matched_species = species
                runtime["engine_path"] = "melt_oxide_vapor_pressure"
                runtime["species"] = species
                runtime.update(
                    {
                        k: melt_rt[k]
                        for k in ("provider_id", "provider_status", "oxide")
                        if k in melt_rt
                    }
                )
                break
            if refuse_melt:
                last_refusal = refuse_melt
                if refuse_melt.startswith("provider_status:"):
                    out_of_domain = True

        if actual is None:
            unsupported = not out_of_domain
            if last_refusal and last_refusal.startswith("provider_status:"):
                out_of_domain = True
                unsupported = False
            runtime["refusal"] = last_refusal or "no_engine_species_match"
        else:
            any_numeric = True
            # Optional apparatus effusion when orifice geometry is present.
            orifice = _equipment_scalar(obs.equipment, "orifice_area")
            clausing = _equipment_scalar(obs.equipment, "clausing_factor")
            if orifice is not None and matched_species is not None:
                try:
                    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

                    legacy = vapor_pressure_legacy_view(vp_data)
                    mm = None
                    for group in ("metals", "oxide_vapors"):
                        row = (legacy.get(group) or {}).get(matched_species)
                        if isinstance(row, Mapping) and row.get("molar_mass_g_mol"):
                            mm = float(row["molar_mass_g_mol"]) / 1000.0
                            break
                    if mm is not None:
                        flux = knudsen_effusion_molar_flux(
                            T_K, actual, molar_mass_kg_mol=mm
                        )
                        factor = float(clausing) if clausing is not None else 1.0
                        runtime["apparatus_effusion_rate_mol_s"] = (
                            flux * float(orifice) * factor
                        )
                        runtime["motzfeldt_applied"] = False
                except Exception as exc:  # noqa: BLE001
                    runtime["geometry_note"] = f"orifice present but unused: {exc}"

        # Defaulted tolerances still produce match/mismatch against the
        # documented budget — do NOT route them through assumed-input (that
        # status is for recipe-side assumed controls, not missing literature σ).
        record = _compare_point(
            obs=obs,
            observable_id=f"{obs.observation_id}:T={T_K:g}",
            species=matched_species or obs.species_id,
            coordinate={"temperature_K": T_K},
            expected=expected,
            uncertainty=unc,
            actual=actual,
            units="Pa",
            runtime={**runtime, "uncertainty_defaulted": bool(unc.get("defaulted"))},
            unsupported_speciation=unsupported and actual is None,
            out_of_domain=out_of_domain and actual is None,
            assumed_input=False,
        )
        evaluation.records.append(record)
        if record.status == "mismatch":
            residual_dex = None
            if expected > 0 and actual is not None and actual > 0:
                residual_dex = abs(math.log10(actual / expected))
            evaluation.findings.append(
                f"FINDING mismatch {obs.species_id} {obs.observation_id} "
                f"T={T_K:g}K expected={expected:.6g} Pa actual="
                f"{actual if actual is not None else 'None'} "
                f"residual_dex={residual_dex if residual_dex is not None else 'n/a'} "
                f"budget={unc}"
            )

    if not any_numeric and evaluation.records:
        # All points refused — surface as skip-with-typed-reason for pytest.
        evaluation.skip_reason = (
            f"{_TYPED_SKIP_PREFIX}{last_refusal or 'engine_refusal_all_points'}"
        )
    return evaluation


def _evaluate_rate(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    vp_data: Mapping[str, Any],
    pO2: float,
) -> ObservationEvaluation:
    del vp_data, pO2  # alpha path does not use pO2 / VP surface
    points, skip, drops = _literature_alpha_points(obs)
    if drops:
        _emit_point_drop_records(
            obs,
            evaluation,
            drops,
            units=str(obs.units or "alpha"),
        )
    if skip is not None:
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=f"{obs.observation_id}:payload",
                species=obs.species_id,
                coordinate={"window": "payload-absent"},
                expected=None,
                uncertainty=None,
                actual=None,
                units=str(obs.units or "alpha"),
                runtime={"skip_reason": skip, "n_point_drops": len(drops)},
            )
        )
        evaluation.skip_reason = skip
        return evaluation

    assert points is not None
    candidates = _engine_species_candidates(obs)
    any_numeric = False
    last_refusal: str | None = None
    for pt in points:
        T_K = float(pt["T_K"])
        expected = float(pt["alpha"])
        unc = resolve_uncertainty(obs, point=pt, kind_hint="alpha")
        actual = None
        matched = None
        runtime: dict[str, Any] = {
            "temperature_K": T_K,
            "candidates": candidates,
            "geometry_assumption": obs.geometry_assumption,
            "engine_path": "grounded_alpha",
        }
        for species in candidates:
            value, refuse, ctx = _engine_alpha(species, T_K)
            if value is not None:
                actual = value
                matched = species
                runtime["species"] = species
                runtime["alpha_context"] = {
                    k: ctx.get(k)
                    for k in (
                        "alpha_s",
                        "alpha_s_form",
                        "alpha_s_extrapolated",
                        "species",
                    )
                    if k in ctx
                }
                break
            last_refusal = refuse
            runtime.setdefault("alpha_refusals", {})[species] = refuse

        if actual is not None:
            any_numeric = True
        # Engine-flagged extrapolated α remains scored (residual IS the result)
        # but must be disclosed on FINDING lines and runtime — not hidden.
        extrapolated = bool(
            (runtime.get("alpha_context") or {}).get("alpha_s_extrapolated")
        )
        if extrapolated:
            runtime["alpha_s_extrapolated"] = True
        record = _compare_point(
            obs=obs,
            observable_id=f"{obs.observation_id}:T={T_K:g}",
            species=matched or obs.species_id,
            coordinate={"temperature_K": T_K},
            expected=expected,
            uncertainty=unc,
            actual=actual,
            units="alpha",
            runtime=runtime,
            unsupported_speciation=actual is None,
        )
        evaluation.records.append(record)
        if record.status == "mismatch":
            extr_tag = " extrapolated: true" if extrapolated else ""
            evaluation.findings.append(
                f"FINDING mismatch {obs.species_id} α T={T_K:g}K "
                f"expected={expected:.6g} actual={actual} budget={unc}{extr_tag}"
            )

    if not any_numeric:
        evaluation.skip_reason = (
            f"{_TYPED_SKIP_PREFIX}{last_refusal or 'alpha_unsupported'}"
        )
    return evaluation


def _evaluate_activity(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
) -> ObservationEvaluation:
    values = obs.values
    semantics = str(values.get("semantics") or "")
    if "bound_not_point" in semantics or "ordering" in semantics.lower():
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=f"{obs.observation_id}:qualitative",
                species=obs.species_id,
                coordinate={"window": "qualitative-ordering"},
                expected=None,
                uncertainty=None,
                actual=None,
                units=str(obs.units or "dimensionless"),
                runtime={
                    "skip_reason": "qualitative_bound_not_point_ordering",
                    "semantics": semantics,
                },
            )
        )
        evaluation.skip_reason = "qualitative_bound_not_point_ordering"
        return evaluation

    # Numeric gamma / activity if present.
    gamma = None
    for key in ("gamma", "activity", "value", "alpha"):
        if values.get(key) is not None:
            try:
                gamma = float(values[key])
                break
            except (TypeError, ValueError):
                continue
    if gamma is None:
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=f"{obs.observation_id}:payload",
                species=obs.species_id,
                coordinate={"window": "payload-absent"},
                expected=None,
                uncertainty=None,
                actual=None,
                units=str(obs.units or "dimensionless"),
                runtime={"skip_reason": "activity_without_numeric_gamma"},
            )
        )
        evaluation.skip_reason = "activity_without_numeric_gamma"
        return evaluation

    # Engine melt_oxide_activity is composition-dependent; pure-oxide a≈1 is the
    # only defensible default without a melt recipe — refuse rather than invent.
    evaluation.records.append(
        _compare_point(
            obs=obs,
            observable_id=f"{obs.observation_id}:gamma",
            species=obs.species_id,
            coordinate={"window": "no-melt-recipe"},
            expected=gamma,
            uncertainty=resolve_uncertainty(obs, kind_hint="activity"),
            actual=None,
            units="dimensionless",
            runtime={
                "skip_reason": "activity_requires_melt_recipe",
                "note": (
                    "extract activity/γ is multi-component; battery refuses to "
                    "assume pure-oxide a=1 as a false match"
                ),
            },
            out_of_domain=True,
        )
    )
    evaluation.skip_reason = f"{_TYPED_SKIP_PREFIX}activity_requires_melt_recipe"
    return evaluation


def evaluate_all(
    *,
    observations: Sequence[AdoptedObservation] | None = None,
    vapor_pressure_data: Mapping[str, Any] | None = None,
) -> list[ObservationEvaluation]:
    obs_list = list(observations) if observations is not None else load_adopted_observations()
    vp = vapor_pressure_data or load_vapor_pressure_data()
    return [evaluate_observation(obs, vapor_pressure_data=vp) for obs in obs_list]


def residual_dex(record: ComparisonRecord) -> float | None:
    """|log10(actual/expected)| when both positive; else None.

    This is the scalar pinned by the residual baselines: a FINDING residual
    that *moves* outside its band is a regression; a residual that stays put
    keeps reporting the FINDING (the residual IS the result).
    """

    if (
        record.expected_value is None
        or record.actual_value is None
        or record.expected_value <= 0
        or record.actual_value <= 0
    ):
        return None
    return abs(math.log10(record.actual_value / record.expected_value))


def rollup_species_error_bars(
    evaluations: Sequence[ObservationEvaluation],
) -> list[dict[str, Any]]:
    """Per-species rollup of reproduction residuals (the headline deliverable)."""

    by_species: dict[str, list[ComparisonRecord]] = {}
    findings_by_species: dict[str, list[str]] = {}
    types_by_species: dict[str, set[str]] = {}
    for ev in evaluations:
        sid = ev.observation.species_id
        by_species.setdefault(sid, []).extend(ev.records)
        types_by_species.setdefault(sid, set()).add(ev.observation.obs_type)
        if ev.findings:
            findings_by_species.setdefault(sid, []).extend(ev.findings)

    rows: list[dict[str, Any]] = []
    for sid in sorted(by_species):
        records = by_species[sid]
        statuses = [r.status for r in records]
        dex_vals = [d for r in records if (d := residual_dex(r)) is not None]
        abs_residuals = [
            abs(r.residual)
            for r in records
            if r.residual is not None and math.isfinite(r.residual)
        ]
        match_n = statuses.count("match")
        mismatch_n = statuses.count("mismatch")
        skip_n = sum(
            1
            for s in statuses
            if s
            in {
                "unsupported-observable",
                "unsupported-speciation",
                "out-of-domain",
                "assumed-input",
            }
        )
        max_dex = max(dex_vals) if dex_vals else None
        mean_dex = (sum(dex_vals) / len(dex_vals)) if dex_vals else None
        classification = "no-comparable-points"
        if mismatch_n:
            classification = "FINDING-mismatch"
        elif match_n and not mismatch_n:
            classification = "within-budget"
        elif skip_n and not match_n and not mismatch_n:
            classification = "engine-or-payload-skip"

        rows.append(
            {
                "species": sid,
                "observation_types": ",".join(sorted(types_by_species.get(sid, []))),
                "n_points": len(records),
                "n_match": match_n,
                "n_mismatch": mismatch_n,
                "n_skip_or_gap": skip_n,
                "max_residual_dex": max_dex,
                "mean_residual_dex": mean_dex,
                "max_abs_residual": max(abs_residuals) if abs_residuals else None,
                "classification": classification,
                "findings": list(findings_by_species.get(sid, [])),
            }
        )
    return rows


def format_rollup_markdown(
    rows: Sequence[Mapping[str, Any]],
    *,
    evaluations: Sequence[ObservationEvaluation] | None = None,
) -> str:
    """Markdown section for docs/model-limitations.md."""

    n_obs = len(evaluations) if evaluations is not None else sum(1 for _ in rows)
    n_findings = sum(1 for r in rows if r.get("classification") == "FINDING-mismatch")
    n_comparable = 0
    n_psat_comparable = 0
    n_rate_comparable = 0
    n_activity_comparable = 0
    n_extrapolated_findings = 0
    if evaluations is not None:
        for ev in evaluations:
            for r in ev.records:
                if r.status not in {"match", "mismatch"}:
                    continue
                n_comparable += 1
                otype = ev.observation.obs_type
                if otype == "psat_series":
                    n_psat_comparable += 1
                elif otype == "rate_series":
                    n_rate_comparable += 1
                elif otype == "activity_coefficient":
                    n_activity_comparable += 1
        for row in rows:
            for f in row.get("findings") or []:
                if "extrapolated: true" in str(f):
                    n_extrapolated_findings += 1
    coverage_line = (
        f"Comparable points: **{n_comparable}** "
        f"(α/rate={n_rate_comparable}, psat={n_psat_comparable}, "
        f"activity/γ={n_activity_comparable}). "
        "psat currently yields zero scored comparisons when the engine lacks "
        "the species; activity/γ is structurally skipped pending melt recipes "
        "(`activity_requires_melt_recipe`). Headline residual budget is the "
        f"α rate_series set. Extrapolated-α FINDINGs marked in-line: "
        f"**{n_extrapolated_findings}**."
        if evaluations is not None
        else "Comparable-point coverage computed when evaluations are supplied."
    )
    lines = [
        ROLLUP_BEGIN,
        "",
        "### Extract-store single-species reproduction battery (t-512)",
        "",
        "Generated from ADOPTED (priority-winner) extract-store observations of",
        "type `psat_series` / `rate_series` / `activity_coefficient`. Residuals",
        "are the deliverable (doctrine: *Headline accuracy is the product*).",
        "Engine refusals surface as typed skips; mismatches are FINDINGs —",
        "tolerances are **not** widened to pass. Geometry: "
        + geometry_assumption_text()
        + ".",
        "",
        coverage_line,
        "",
        f"- Adopted observations evaluated: **{n_obs if evaluations is not None else '—'}**",
        f"- Species with FINDING (mismatch outside stated/default budget): **{n_findings}**",
        "",
        "| Species | Types | N pts | Match | Mismatch | Skip/gap | "
        "Max residual (dex) | Mean residual (dex) | Classification |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        max_dex = row.get("max_residual_dex")
        mean_dex = row.get("mean_residual_dex")
        lines.append(
            "| {species} | {types} | {n} | {m} | {mm} | {s} | {mx} | {mn} | {c} |".format(
                species=row["species"],
                types=row.get("observation_types") or "—",
                n=row.get("n_points") or 0,
                m=row.get("n_match") or 0,
                mm=row.get("n_mismatch") or 0,
                s=row.get("n_skip_or_gap") or 0,
                mx=f"{max_dex:.3g}" if max_dex is not None else "—",
                mn=f"{mean_dex:.3g}" if mean_dex is not None else "—",
                c=row.get("classification") or "—",
            )
        )

    # Default-tolerance legend
    lines.extend(
        [
            "",
            "**Default tolerances** (used only when the extract carries no usable",
            "numeric uncertainty; each defaulted comparison carries",
            "`defaulted: true` on the uncertainty dict and still scores",
            "match/mismatch against that documented budget):",
            "",
            f"- `psat_series`: `{DEFAULT_PSAT_UNCERTAINTY['kind']}` = "
            f"{DEFAULT_PSAT_UNCERTAINTY['value']} "
            f"({DEFAULT_PSAT_UNCERTAINTY['rationale']})",
            f"- `rate_series` (α): `{DEFAULT_ALPHA_UNCERTAINTY['kind']}` = "
            f"{DEFAULT_ALPHA_UNCERTAINTY['value']} "
            f"({DEFAULT_ALPHA_UNCERTAINTY['rationale']})",
            f"- `activity_coefficient`: `{DEFAULT_ACTIVITY_UNCERTAINTY['kind']}` = "
            f"{DEFAULT_ACTIVITY_UNCERTAINTY['value']} "
            f"({DEFAULT_ACTIVITY_UNCERTAINTY['rationale']})",
            "",
        ]
    )

    finding_lines = [
        f
        for row in rows
        for f in (row.get("findings") or [])
    ]
    if finding_lines:
        lines.append("**FINDINGS (mismatches outside budget — not tuned away):**")
        lines.append("")
        for f in finding_lines:
            lines.append(f"- {f}")
        lines.append("")

    if evaluations is not None:
        # Compact residual table for comparable points only.
        comparable = [
            r
            for ev in evaluations
            for r in ev.records
            if r.status in {"match", "mismatch"}
        ]
        if comparable:
            lines.append("Comparable point residuals:")
            lines.append("")
            lines.append(records_to_markdown(comparable))
            lines.append("")

    lines.append(ROLLUP_END)
    return "\n".join(lines)


def extract_rollup_section(text: str) -> str | None:
    """Return the marker-delimited t-512 rollup section (inclusive), or None."""

    if ROLLUP_BEGIN not in text or ROLLUP_END not in text:
        return None
    pre, rest = text.split(ROLLUP_BEGIN, 1)
    del pre
    body, _post = rest.split(ROLLUP_END, 1)
    return ROLLUP_BEGIN + body + ROLLUP_END


def append_rollup_to_model_limitations(
    rows: Sequence[Mapping[str, Any]],
    *,
    evaluations: Sequence[ObservationEvaluation] | None = None,
    path: Path | None = None,
) -> Path:
    """Insert or replace the t-512 rollup section in docs/model-limitations.md.

    Prefer writing to a temp path from tests and *diffing* against the committed
    file. The regen escape hatch is the env var ``RPS_T512_REGEN_ROLLUP=1``
    (see ``test_battery_rollup_matches_committed_model_limitations``).
    """

    target = path or MODEL_LIMITATIONS_PATH
    text = target.read_text(encoding="utf-8")
    section = format_rollup_markdown(rows, evaluations=evaluations)
    if ROLLUP_BEGIN in text and ROLLUP_END in text:
        pre = text.split(ROLLUP_BEGIN, 1)[0]
        post = text.split(ROLLUP_END, 1)[1]
        # Keep a single trailing newline boundary.
        new_text = pre.rstrip() + "\n\n" + section + "\n" + post.lstrip("\n")
    else:
        # Append after the distribution / MRE error-budget region if present,
        # else at end of file.
        anchor = "### Yu 2025 hollow-anode MRE comparison error budget"
        if anchor in text:
            # Place after the Yu section's first table block end — append near
            # the validated-system discussion: after distribution paper section
            # is fine; put before Stage 0 if present.
            stage0 = "## Stage 0"
            if stage0 in text:
                pre, post = text.split(stage0, 1)
                new_text = pre.rstrip() + "\n\n" + section + "\n\n" + stage0 + post
            else:
                new_text = text.rstrip() + "\n\n" + section + "\n"
        else:
            new_text = text.rstrip() + "\n\n" + section + "\n"
    target.write_text(new_text, encoding="utf-8")
    return target


def is_typed_skip(reason: str | None) -> bool:
    if not reason:
        return False
    return reason.startswith(_TYPED_SKIP_PREFIX) or reason in {
        "pointer_or_anchor_without_numeric_points",
        "qualitative_bound_not_point_ordering",
        "activity_without_numeric_gamma",
        "no_usable_psat_payload",
        "no_usable_rate_series_payload",
        "coefficients_without_temperature_range",
        "coefficients_not_numeric",
        "rate_series_without_alpha_and_no_motzfeldt_geometry",
        "alpha_without_temperature",
        "alpha_not_numeric",
    }


__all__ = [
    "AdoptedObservation",
    "COMPARISON_STATUSES",
    "DEFAULT_ALPHA_UNCERTAINTY",
    "DEFAULT_PSAT_UNCERTAINTY",
    "ExtractReproductionError",
    "MODEL_LIMITATIONS_PATH",
    "ObservationEvaluation",
    "ROLLUP_BEGIN",
    "ROLLUP_END",
    "TARGET_TYPES",
    "append_rollup_to_model_limitations",
    "evaluate_all",
    "evaluate_observation",
    "extract_rollup_section",
    "format_rollup_markdown",
    "geometry_assumption_text",
    "is_typed_skip",
    "load_adopted_observations",
    "load_vapor_pressure_data",
    "motzfeldt_available",
    "residual_dex",
    "resolve_pO2_bar",
    "resolve_uncertainty",
    "rollup_species_error_bars",
]
