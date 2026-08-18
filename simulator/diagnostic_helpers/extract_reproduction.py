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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.alpha_kinetics import ALPHA_AUTHORITY_STATUS_FIELD
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.comparability_verdict import verdict_from_membership
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.chemistry.langmuir_knudsen import (
    grounded_alpha,
    knudsen_effusion_molar_flux,
    langmuir_molar_flux,
    species_molar_mass_kg_mol,
)
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    melt_oxide_activity,
)
from simulator.state import MOLAR_MASS
from simulator.diagnostic_helpers.reproduction_compare import (
    COMPARISON_STATUSES,
    ComparisonRecord,
    compare_values,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTS_DIR = REPO_ROOT / "data" / "literature" / "extracts"
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"
FEEDSTOCKS_PATH = REPO_ROOT / "data" / "feedstocks.yaml"
MODEL_LIMITATIONS_PATH = REPO_ROOT / "docs" / "model-limitations.md"
MOTZFELDT_TOOL_PATH = REPO_ROOT / "tools" / "motzfeldt.py"
TOOLS_DIR = REPO_ROOT / "tools"

TARGET_TYPES = frozenset(
    {"psat_series", "rate_series", "activity_coefficient", "alpha", "gibbs_table"}
)
KEMS_SOURCE_PREFIX = "kems-"

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
DEFAULT_RATE_UNCERTAINTY: dict[str, Any] = {
    "kind": "log10_decades",
    "value": 0.5,
    "defaulted": True,
    "rationale": (
        "extract rate observation has no usable numeric uncertainty; "
        "default half-dex digitized high-temperature flux envelope (t-512)"
    ),
}

# Engine diagnostic default used only for rate rows that do not state pO2.
# Coverage/reporting retains that this is defaulted; psat comparisons never
# relabel a categorical cell-material boundary or total vacuum as numeric pO2.
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
    adoption_basis: str = "priority_winner"
    # Typed condensed-form axis (state-at-measurement). Optional during
    # migration; alpha residual path fail-closes when missing/unresolved.
    condensed_form: Mapping[str, Any] | None = None

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
    skip_reasons: list[str] = field(default_factory=list)

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
        return (
            "tools/motzfeldt.py available; geometry inversion is used only with "
            "complete numeric inputs, otherwise a typed capability/data gap is reported"
        )
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
    """Return the reproduction scope: priority winners plus every KEMS row.

    VALUE-PRECEDENCE intentionally chooses one source for production data, but
    that is not a license for the validation harness to erase the remaining
    external mass-spectrometry evidence.  All ``kems-*`` observations therefore
    enter the coverage ledger even when their source is not production-priority.
    """

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
            source_id = str(obs.get("source_id") or "")
            if otype not in TARGET_TYPES:
                continue
            if obs.get("review_status") == "rejected":
                continue
            is_priority_winner = bool(obs.get("is_priority_winner"))
            is_mass_spec = source_id.startswith(KEMS_SOURCE_PREFIX)
            # Gibbs tables enter this battery as KEMS evidence coverage only.
            # Pulling every priority-winner thermochemical table would add the
            # full 1,600+ row property corpus to a mass-spectrometry ledger.
            if otype == "gibbs_table" and not is_mass_spec:
                continue
            if not (is_priority_winner or is_mass_spec):
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
            dex = dex_by_key.get((str(species_id), otype, source_id))
            raw_form = obs.get("condensed_form")
            condensed_form: Mapping[str, Any] | None
            if isinstance(raw_form, Mapping):
                condensed_form = dict(raw_form)
            else:
                condensed_form = None
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
                    is_priority_winner=is_priority_winner,
                    geometry_assumption=geometry,
                    adoption_basis=(
                        "priority_winner" if is_priority_winner else "mass_spec_extract"
                    ),
                    condensed_form=condensed_form,
                )
            )
    adopted.sort(key=lambda row: (row.species_id, row.source_id, row.observation_id))
    return adopted


def _equipment_scalar(equipment: Mapping[str, Any], field: str) -> Any:
    block = equipment.get(field)
    if isinstance(block, Mapping):
        return block.get("value")
    return None


def resolve_chamber_pressure_pa(obs: AdoptedObservation) -> tuple[float | None, str]:
    """Resolve total chamber pressure without pretending it is oxygen fugacity."""

    chamber = _equipment_scalar(obs.equipment, "chamber_pressure")
    if chamber is None:
        return None, "chamber pressure not stated"
    block = obs.equipment.get("chamber_pressure")
    units = str(block.get("units") or "").lower().strip() if isinstance(block, Mapping) else ""
    factors = {
        "pa": 1.0,
        "pascal": 1.0,
        "mbar": 100.0,
        "millibar": 100.0,
        "bar": 1.0e5,
        "torr": 133.322368,
        "mmhg": 133.322368,
    }
    factor = factors.get(units)
    if factor is None:
        return None, f"chamber pressure units unsupported:{units or 'missing'}"
    try:
        pressure = float(chamber) * factor
    except (TypeError, ValueError):
        return None, "chamber pressure not numeric"
    if not math.isfinite(pressure) or pressure < 0.0:
        return None, "chamber pressure non-finite or negative"
    return pressure, f"equipment.chamber_pressure ({units}→Pa total pressure)"


def resolve_pO2_bar(obs: AdoptedObservation) -> tuple[float | None, str]:
    """Return quantitative pO2 only when the observation states it.

    Cell material supplies a categorical redox boundary, not a numeric oxygen
    fugacity. Total chamber pressure is likewise not pO2.
    """

    for container_name, container in (("values", obs.values), ("equipment", obs.equipment)):
        if not isinstance(container, Mapping):
            continue
        for key in ("pO2_bar", "fO2_bar", "oxygen_fugacity_bar"):
            raw = container.get(key)
            if isinstance(raw, Mapping):
                raw = raw.get("value")
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0.0:
                return value, f"{container_name}.{key}"
        for key in ("log10_pO2_bar", "fO2_log10_bar"):
            raw = container.get(key)
            if isinstance(raw, Mapping):
                raw = raw.get("value")
            if raw is None:
                continue
            try:
                value = 10.0 ** float(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value) and value > 0.0:
                return value, f"{container_name}.{key} (10^x bar)"

    _ensure_tools_path()
    from motzfeldt import effective_po2_boundary_for_observation  # noqa: WPS433

    boundary = effective_po2_boundary_for_observation({"equipment": obs.equipment})
    if boundary is not None:
        return (
            None,
            "cell_material_boundary="
            f"{boundary.get('boundary')} ({boundary.get('material_normalized')}); "
            "missing quantitative pO2 capability",
        )
    return None, "missing_condition:pO2_boundary"


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


# values keys carrying genuine per-point (T, α) measurements, in priority
# order. Scalar/fit payloads (alpha, alpha_range, alpha_form) are aggregates
# over the observation T_range and can never split a straddling row.
_PER_POINT_ALPHA_SERIES_PRIORITY = (
    "series",
    "per_temperature",
    "alpha_series_figure_approx",
    "pins",
    "points",
)
_PER_POINT_ALPHA_SERIES_NAMES = frozenset(_PER_POINT_ALPHA_SERIES_PRIORITY)


def _literature_alpha_points(
    obs: AdoptedObservation,
) -> tuple[list[dict[str, Any]] | None, str | None, list[dict[str, Any]]]:
    """Return (points, skip_reason, drops).

    Rate/flux-only rows in a mixed series are not comparable without Motzfeldt
    geometry — each such drop is recorded so "N pts" cannot silently shrink.
    """

    values = obs.values
    drops: list[dict[str, Any]] = []
    series = None
    series_name = None
    for candidate_name in _PER_POINT_ALPHA_SERIES_PRIORITY:
        candidate = values.get(candidate_name)
        if isinstance(candidate, list) and candidate:
            series = candidate
            series_name = candidate_name
            break
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
            point_range = pt.get("T_range_K")
            T = pt.get("T_K") or pt.get("temperature_K")
            t_provenance = "explicit" if T is not None else None
            if T is None and isinstance(point_range, (list, tuple)) and len(point_range) == 2:
                T = 0.5 * (float(point_range[0]) + float(point_range[1]))
                t_provenance = "point_range_midpoint"
            if T is None and obs.T_range_K is not None:
                T = 0.5 * (float(obs.T_range_K[0]) + float(obs.T_range_K[1]))
                t_provenance = "obs_range_midpoint"
            alpha = pt.get("alpha")
            if alpha is None:
                alpha = pt.get("alpha_approx")
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
                    "series_name": series_name,
                    "T_provenance": t_provenance,
                }
            )
        if out:
            return out, None, drops
        if series:
            return None, "rate_series_without_alpha_and_no_motzfeldt_geometry", drops

    alpha_form = values.get("alpha_form")
    if isinstance(alpha_form, Mapping) and obs.T_range_K is not None:
        lo, hi = (float(obs.T_range_K[0]), float(obs.T_range_K[1]))
        temperatures = list(dict.fromkeys((lo, 0.5 * (lo + hi), hi)))
        out = []
        for idx, T_K in enumerate(temperatures):
            try:
                if alpha_form.get("A") is not None and alpha_form.get("B_K") is not None:
                    alpha = float(alpha_form["A"]) * math.exp(
                        -float(alpha_form["B_K"]) / T_K
                    )
                    sigma = None
                else:
                    prefactor = float(
                        alpha_form.get("gamma_o", alpha_form.get("c0"))
                    )
                    activation = float(alpha_form["E_J_per_mol"])
                    gas_constant = float(alpha_form.get("R_J_per_mol_K") or 8.314462618)
                    alpha = prefactor * math.exp(-activation / (gas_constant * T_K))
                    sigma_E = alpha_form.get("E_uncertainty_J_per_mol")
                    sigma = (
                        abs(alpha) * float(sigma_E) / (gas_constant * T_K)
                        if sigma_E is not None
                        else None
                    )
            except (KeyError, TypeError, ValueError, OverflowError):
                return None, "alpha_form_not_evaluable", drops
            out.append(
                {
                    "T_K": T_K,
                    "alpha": alpha,
                    "sigma": sigma,
                    "point_index": idx,
                    "series_name": "alpha_form",
                    "T_provenance": "alpha_form_synthetic",
                    "uncertainty_components": (
                        ["Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T)"]
                        if sigma is not None
                        else []
                    ),
                }
            )
        return out, None, drops

    if values.get("alpha") is not None:
        T = None
        if obs.T_range_K is not None:
            T = 0.5 * (float(obs.T_range_K[0]) + float(obs.T_range_K[1]))
        if T is None:
            return None, "alpha_without_temperature", drops
        try:
            return (
                [
                    {
                        "T_K": float(T),
                        "alpha": float(values["alpha"]),
                        "sigma": None,
                        "T_provenance": "obs_range_midpoint",
                    }
                ],
                None,
                drops,
            )
        except (TypeError, ValueError):
            return None, "alpha_not_numeric", drops

    alpha_range = values.get("alpha_range")
    if isinstance(alpha_range, (list, tuple)) and len(alpha_range) == 2:
        if obs.T_range_K is None:
            return None, "alpha_range_without_temperature", drops
        try:
            low, high = float(alpha_range[0]), float(alpha_range[1])
            T = 0.5 * (float(obs.T_range_K[0]) + float(obs.T_range_K[1]))
        except (TypeError, ValueError):
            return None, "alpha_range_not_numeric", drops
        return (
            [
                {
                    "T_K": T,
                    "alpha": 0.5 * (low + high),
                    "sigma": 0.5 * abs(high - low),
                    "point_index": 0,
                    "series_name": "alpha_range",
                    "T_provenance": "obs_range_midpoint",
                }
            ],
            None,
            drops,
        )
    return None, "no_usable_rate_series_payload", drops


def _literature_rate_points(
    obs: AdoptedObservation,
) -> tuple[list[dict[str, Any]] | None, str | None, list[dict[str, Any]]]:
    """Normalize measured species-rate points to mol/(m2*s)."""

    values = obs.values
    drops: list[dict[str, Any]] = []
    figure_points = values.get("figure_2_log10_J_approx")
    if isinstance(figure_points, list) and figure_points:
        out: list[dict[str, Any]] = []
        for idx, point in enumerate(figure_points):
            if not isinstance(point, Mapping):
                drops.append({"point_index": idx, "reason": "rate_point_not_mapping"})
                continue
            log_key = next(
                (str(key) for key in point if str(key).startswith("log10_J_")),
                None,
            )
            try:
                T_K = (
                    float(point["T_K"])
                    if point.get("T_K") is not None
                    else float(point["T_C"]) + 273.15
                )
                log10_rate = float(point[log_key]) if log_key is not None else None
                if log10_rate is None:
                    raise ValueError("missing log10 rate")
                # Published figure units are mol cm^-2 s^-1.  1 m2 = 1e4 cm2.
                rate = (10.0**log10_rate) * 1.0e4
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                drops.append(
                    {
                        "point_index": idx,
                        "reason": "rate_point_not_evaluable",
                        "detail": str(exc),
                    }
                )
                continue
            out.append(
                {
                    "T_K": T_K,
                    "rate_mol_m2_s": rate,
                    "point_index": idx,
                    "source_units": "mol cm^-2 s^-1",
                }
            )
        if out:
            return out, None, drops

    for series_name in ("series", "points"):
        series = values.get(series_name)
        if not isinstance(series, list) or not series:
            continue
        out = []
        for idx, point in enumerate(series):
            if not isinstance(point, Mapping):
                continue
            T = point.get("T_K") or point.get("temperature_K")
            raw = None
            scale = 1.0
            for key, key_scale in (
                ("rate_mol_m2_s", 1.0),
                ("flux_mol_m2_s", 1.0),
                ("rate_mol_cm2_s", 1.0e4),
                ("flux_mol_cm2_s", 1.0e4),
            ):
                if point.get(key) is not None:
                    raw = point[key]
                    scale = key_scale
                    break
            if T is None or raw is None:
                continue
            try:
                out.append(
                    {
                        "T_K": float(T),
                        "rate_mol_m2_s": float(raw) * scale,
                        "point_index": idx,
                        "source_units": str(obs.units or "mol m^-2 s^-1"),
                    }
                )
            except (TypeError, ValueError):
                drops.append({"point_index": idx, "reason": "rate_point_not_numeric"})
        if out:
            return out, None, drops

    quantity = str(values.get("quantity") or "").lower()
    if "clausing" in quantity:
        return None, "unsupported_observable:clausing_factor_not_species_rate", drops
    if values.get("semantics") == "bound_not_point_ordering":
        return None, "missing_numeric_species_rate:qualitative_bound", drops
    return None, "missing_numeric_species_rate", drops


def _has_alpha_payload(obs: AdoptedObservation) -> bool:
    values = obs.values
    return any(
        values.get(key) is not None
        for key in (
            "alpha",
            "alpha_range",
            "alpha_form",
            "per_temperature",
            "alpha_series_figure_approx",
            "pins",
        )
    ) or (
        isinstance(values.get("series"), list)
        and any(
            isinstance(point, Mapping) and point.get("alpha") is not None
            for point in values["series"]
        )
    )


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
                    "components": list(point.get("uncertainty_components") or []),
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
        alpha_range = unc.get("alpha_range")
        if isinstance(alpha_range, (list, tuple)) and len(alpha_range) == 2:
            try:
                low, high = float(alpha_range[0]), float(alpha_range[1])
                return {
                    "kind": "absolute",
                    "value": 0.5 * abs(high - low),
                    "defaulted": False,
                    "source": "observation.uncertainty.alpha_range",
                    "components": ["published alpha range half-width"],
                }
            except (TypeError, ValueError):
                pass

    if kind_hint == "alpha":
        for container, source in (
            (obs.values, "values"),
            (obs.uncertainty if isinstance(obs.uncertainty, Mapping) else {}, "uncertainty"),
        ):
            alpha_range = container.get("alpha_range")
            if isinstance(alpha_range, (list, tuple)) and len(alpha_range) == 2:
                try:
                    low, high = float(alpha_range[0]), float(alpha_range[1])
                    return {
                        "kind": "absolute",
                        "value": 0.5 * abs(high - low),
                        "defaulted": False,
                        "source": f"observation.{source}.alpha_range",
                        "components": ["published alpha range half-width"],
                    }
                except (TypeError, ValueError):
                    pass
        for key in ("sigma", "alpha_uncertainty_sigma"):
            if obs.values.get(key) is not None:
                try:
                    return {
                        "kind": "absolute",
                        "value": float(obs.values[key]),
                        "defaulted": False,
                        "source": f"observation.values.{key}",
                        "components": [key],
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
    if kind_hint == "rate":
        return dict(DEFAULT_RATE_UNCERTAINTY)
    return dict(DEFAULT_ACTIVITY_UNCERTAINTY)


def _engine_pure_psat_pa(
    species: str,
    T_K: float,
    vapor_pressure_data: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    """Pure-component pressure from the same versioned vapor-rail catalog."""

    from simulator.condensation import _try_antoine_psat_pa

    try:
        P, refused = _try_antoine_psat_pa(
            species,
            T_K,
            vapor_pressure_data=vapor_pressure_data,
            # Reproduction against measurement is how an unvalidated row
            # earns applicability; governing this path would lock it out.
            enforce_hot_train_applicability=False,
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
    account_mol: Mapping[str, float] | None = None,
) -> tuple[float | None, str | None, dict[str, Any]]:
    """Builtin vapor-pressure provider at an explicit melt recipe and pO2."""

    oxide_formula = oxide or PARENT_OXIDE_BY_ENGINE_SPECIES.get(species)
    if oxide_formula is None:
        return None, "no_parent_oxide_for_species", {}
    account = (
        {str(key): float(value) for key, value in account_mol.items()}
        if account_mol is not None
        else {oxide_formula: 100.0}
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": account},
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
        "account_mol": account,
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
    if obs.obs_type == "alpha":
        return _evaluate_alpha(obs, evaluation)
    if obs.obs_type == "activity_coefficient":
        return _evaluate_activity(obs, evaluation)
    if obs.obs_type == "gibbs_table":
        evidence_class = str(obs.values.get("evidence_class") or "")
        if evidence_class == "thermodynamic_model_parameter":
            reason = "thermodynamic_model_parameter_not_activity_measurement"
        elif evidence_class == "pure_solid_thermochemistry":
            reason = "pure_solid_thermochemistry_not_melt_activity"
        else:
            reason = "gibbs_table_not_runtime_observable"
        evaluation.skip_reason = f"{_TYPED_SKIP_PREFIX}{reason}"
        evaluation.skip_reasons.append(evaluation.skip_reason)
        evaluation.runtime_notes.append(
            "Gibbs evidence is coverage-only and never residual-pin bearing"
        )
        return evaluation
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
        evaluation.skip_reasons.append(f"point_drop:{reason}")


def _pressure_standard_state_kind(obs: AdoptedObservation) -> str | None:
    """Classify the literature pressure basis without crossing standard states."""

    text = " ".join(
        str(value)
        for value in (
            obs.standard_state,
            obs.phase,
            obs.values.get("standard_state"),
            obs.values.get("quantity"),
            obs.values.get("condensed_reservoir"),
            obs.values.get("material"),
        )
        if value is not None
    ).lower()
    # Melt/multicomponent tokens win over a bare "pure" substring. DeMaria
    # Na figure-digitized rows (t-383 Step 0) say "not pure oxide" over lunar
    # basalt melt — the old pure-first order misrouted them to condensed-metal
    # pure_component Antoine (~6–10 dex residual). Melt-first matches the
    # physical standard state and keeps K/Na DeMaria on the melt rail.
    if any(token in text for token in ("melt", "silicate", "basalt", "slag")):
        return "melt"
    if "pure" in text or "pure_psat" in text:
        return "pure_component"
    return None


# Production vapour-rail residual validation is a claim about the *silicate /
# oxide-condensed carrier* surface. Class axis (DECOMPOSITION §0.2 / B1):
# "interfacial transformation required", not atomicity. Pure-element and
# molten-metal α measure congruent metal evaporation; they are not evidence
# for melt α (rail Fe 0.02 from oxide melt vs congruent metals near 1).
RAIL_COMPARABLE_SYSTEM_CLASSES = frozenset(
    {
        "silicate_melt",
        "solid_solution_silicate",
        "pure_oxide_condensed",
    }
)
RAIL_INCOMPARABLE_SYSTEM_CLASSES = frozenset(
    {
        "pure_element_condensed",
        "molten_metal",
        "solid_film_growth",
    }
)


def observation_system_class(obs: AdoptedObservation) -> str | None:
    """Return typed ``system_class`` or a conservative inference from phase text.

    Prefer the extract's typed ``values.system_class`` (B1 harvest). Pre-B1
    rows often lack it; phase/material/regime tokens then recover the class so
    comparability is not species-name gated.
    """

    raw = obs.values.get("system_class")
    if raw is not None and str(raw).strip():
        return str(raw).strip()

    text = " ".join(
        str(value)
        for value in (
            obs.phase,
            obs.regime,
            obs.standard_state,
            obs.values.get("material"),
            obs.values.get("condensed_reservoir"),
            obs.values.get("quantity"),
            obs.values.get("gas_species"),
        )
        if value is not None
    ).lower()

    if any(
        token in text
        for token in (
            "solid_sio_film",
            "film_growth",
            "solid_sio_growth",
            "growth_coefficient",
        )
    ):
        return "solid_film_growth"
    if any(
        token in text
        for token in (
            "molten_fe",
            "liquid_fe_",
            "steel_melt",
            "fe_mn",
            "fe_cr",
            "fe_s_alloy",
            "molten_metal",
            "liquid metal",
            "fe_alloy",
            "liquid_fe_alloy",
            "olette",
        )
    ):
        return "molten_metal"
    if any(
        token in text
        for token in (
            "solid_polycrystalline",
            "solid_fe",
            "solid_cr",
            "pure_elemental",
            "pure_liquid_metal",
            "electrolytic_fe",
            "solid_metal",
            "solid_cr_metal",
            "solid_fe_metal",
        )
    ):
        return "pure_element_condensed"
    if any(token in text for token in ("olivine", "solid_solution")):
        return "solid_solution_silicate"
    if any(
        token in text
        for token in (
            "silicate",
            "melt",
            "basalt",
            "slag",
            "cai",
            "chondrule",
            "fcmas",
            "fmsca",
            "ferrobasalt",
            "forsterite",
            "cmas",
        )
    ):
        return "silicate_melt"
    if any(
        token in text
        for token in (
            "pure_oxide",
            "oxide_silicate",
            "oxide_source",
            "oxide_literature",
        )
    ):
        return "pure_oxide_condensed"
    return None


def rail_system_class_comparability(
    obs: AdoptedObservation,
) -> tuple[bool, str | None, str | None]:
    """Whether an observation is class-comparable to the production rail carrier.

    Returns ``(comparable, system_class_or_none, skip_reason_suffix_or_none)``.
    A pin is a validation claim about the *carrier*; scoring pure-element /
    molten-metal α against the silicate-melt rail is a category error.
    """

    system_class = observation_system_class(obs)
    if system_class is None:
        # Untyped and uninferable: leave scorable. Silent exclusion of
        # unknown rows would hide coverage drift; typed class is preferred.
        return True, None, None
    if system_class in RAIL_COMPARABLE_SYSTEM_CLASSES:
        return True, system_class, None
    if system_class in RAIL_INCOMPARABLE_SYSTEM_CLASSES:
        return (
            False,
            system_class,
            f"not_comparable_system_class:{system_class}",
        )
    # Explicit but unlisted class — fail closed (do not invent comparability).
    return (
        False,
        system_class,
        f"not_comparable_system_class:{system_class}",
    )


def rail_system_class_verdict(obs: AdoptedObservation) -> str:
    """Three-value class-comparability verdict (b-190). DIAGNOSTIC ONLY.

    ``rail_system_class_comparability`` must keep returning ``True`` for an
    untyped observation -- silently excluding unknown rows would hide coverage
    drift, which is worse than scoring them. But ``True`` there means "not
    excluded", NOT "verified comparable", and nothing downstream could tell the
    two apart. This function draws that distinction without moving the boolean:

        typed and rail-comparable    -> match
        typed and not rail-comparable -> mismatch
        untyped / uninferable         -> undeterminable

    Nothing refuses on this verdict; it is recorded so the untyped population is
    countable. Owner ruling 2026-08-18: third state, no gate.
    """

    return verdict_from_membership(
        observation_system_class(obs), RAIL_COMPARABLE_SYSTEM_CLASSES
    )


# ---------------------------------------------------------------------------
# Condensed-form axis (state-at-measurement)
# ---------------------------------------------------------------------------
# Rail residual pins claim a *liquid silicate melt* specimen form. A solid
# olivine α, partially molten CAI row, amorphous-film growth coefficient, or
# unresolved supercooled/two-phase measurement is not the same observable even
# when system_class is silicate_melt / solid_solution_silicate.
#
# Closed vocabulary (design 2026-08-09-condensed-form). No species branches.
CONDENSED_FORM_STATES = frozenset(
    {
        "liquid_melt",
        "supercooled_liquid",
        "partially_molten",
        "glass_amorphous",
        "crystalline",
        "unresolved",
    }
)
# Exact form match for the production silicate-melt rail residual path.
RAIL_TARGET_CONDENSED_FORM = "liquid_melt"
# States that can never pin the liquid-melt rail without a valid correction.
RAIL_NONMATCH_CONDENSED_FORMS = frozenset(
    {
        "supercooled_liquid",
        "partially_molten",
        "glass_amorphous",
        "crystalline",
        "unresolved",
    }
)
CONDENSED_FORM_CORRECTIONS_PATH = (
    REPO_ROOT / "data" / "condensed_form_corrections.yaml"
)
_R_GAS_J_MOL_K = 8.314462618  # CODATA; used only in form-correction algebra


def observation_condensed_form(
    obs: AdoptedObservation,
) -> Mapping[str, Any] | None:
    """Return the typed condensed_form object, or None when absent."""

    if isinstance(obs.condensed_form, Mapping) and obs.condensed_form:
        return obs.condensed_form
    # Allow values.condensed_form as a migration fallback (B1 harvest style).
    raw = obs.values.get("condensed_form")
    if isinstance(raw, Mapping) and raw:
        return raw
    return None


def observation_condensed_form_state(obs: AdoptedObservation) -> str | None:
    """Return closed-vocabulary ``state`` or None when untyped."""

    form = observation_condensed_form(obs)
    if form is None:
        return None
    raw = form.get("state")
    if raw is None or not str(raw).strip():
        return None
    # Out-of-vocabulary tokens are returned verbatim; the comparability gate
    # fails them closed with typed not_comparable_condensed_form:<token>.
    return str(raw).strip()


def observation_form_transition_context(
    obs: AdoptedObservation,
) -> dict[str, float]:
    """Typed transition temperatures (K) declared on the condensed_form block.

    Returns a dict carrying any of ``Tg_K`` / ``solidus_K`` / ``liquidus_K``
    as finite positive floats (design 2026-08-09-condensed-form
    ``transition_context``). Absent or non-numeric entries are omitted — the
    T cross-check only runs on typed values; an untyped claim keeps the prior
    trust level (no data to check against).
    """

    form = observation_condensed_form(obs)
    ctx = form.get("transition_context") if isinstance(form, Mapping) else None
    out: dict[str, float] = {}
    if not isinstance(ctx, Mapping):
        return out
    for key in ("Tg_K", "solidus_K", "liquidus_K"):
        try:
            value = float(ctx.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            out[key] = value
    return out


# Boundary convention: T == liquidus is liquid-side (the last crystal dissolves
# at the liquidus); T strictly below is subliquidus. Solidus mirrors this.
_SOLID_FORM_CLAIMS = frozenset({"crystalline", "glass_amorphous"})


def _form_claim_T_consistency(
    obs: AdoptedObservation,
    state: str,
    transitions: Mapping[str, float],
    T_K: float | None,
    *,
    point_T_is_measured: bool = False,
) -> dict[str, Any]:
    """Cross-check a typed form claim against T and the typed transitions.

    LABEL-TRUST backstop (grok P1): an authored ``state`` string is not taken
    on faith when the row also carries typed transition temperatures — the
    T_range (or measured point T) must agree with the claim.

    T basis: a *measured* per-point T (``point_T_is_measured=True``) decides on
    its own (state-at-measurement split semantics); otherwise the observation
    ``T_range_K`` decides so an aggregate value cannot hide behind a synthetic
    midpoint on the "safe" side of a straddled transition.

    Returns a detail mapping with keys:

    - ``checked``: False when no T or no relevant transition is typed.
    - ``claimed_state``: the authored state under check.
    - ``conflict``: token naming a claim/T contradiction (fail-closed
      downgrade to ``form_unresolved``), else None.
    - ``straddles``: transition key the observation T_range straddles, else
      None. Only non-match states straddle without conflict; a ``liquid_melt``
      claim straddling its liquidus is a conflict (aggregate contamination).
    - ``point_state``: resolved per-point state for a measured point T on a
      ``partially_molten`` row (``liquid_melt`` on the molten side), else None.
    """

    result: dict[str, Any] = {
        "checked": False,
        "claimed_state": state,
        "conflict": None,
        "straddles": None,
        "point_state": None,
    }
    liquidus = transitions.get("liquidus_K")
    solidus = transitions.get("solidus_K")
    if liquidus is None and solidus is None:
        return result
    if T_K is not None and (point_T_is_measured or obs.T_range_K is None):
        lo = hi = float(T_K)
    elif obs.T_range_K is not None:
        lo, hi = float(obs.T_range_K[0]), float(obs.T_range_K[1])
    else:
        return result
    result["checked"] = True
    result["T_window_K"] = [lo, hi]

    below_liquidus = liquidus is not None and hi < liquidus
    above_liquidus = liquidus is not None and lo >= liquidus
    straddles_liquidus = liquidus is not None and lo < liquidus <= hi
    below_solidus = solidus is not None and hi < solidus
    straddles_solidus = solidus is not None and lo < solidus <= hi

    if state == RAIL_TARGET_CONDENSED_FORM:
        if below_liquidus:
            result["conflict"] = "liquid_melt_below_liquidus"
        elif solidus is not None and below_solidus:
            result["conflict"] = "liquid_melt_below_solidus"
        elif straddles_liquidus:
            result["conflict"] = "liquid_melt_straddles_liquidus"
        return result
    if state in _SOLID_FORM_CLAIMS:
        if above_liquidus:
            result["conflict"] = f"{state}_above_liquidus"
        elif straddles_liquidus:
            result["straddles"] = "liquidus_K"
        elif state == "crystalline" and straddles_solidus:
            result["straddles"] = "solidus_K"
        return result
    if state == "supercooled_liquid":
        # Metastable by definition below the liquidus; above it the claim is
        # self-contradictory.
        if above_liquidus:
            result["conflict"] = "supercooled_liquid_above_liquidus"
        elif straddles_liquidus:
            result["straddles"] = "liquidus_K"
        return result
    if state == "partially_molten":
        if T_K is not None and point_T_is_measured:
            # State-at-measurement split: a *measured* point decides on its own
            # T — the molten side of the typed liquidus is a valid liquid
            # point; below stays excluded as partially_molten.
            if liquidus is not None:
                result["point_state"] = (
                    RAIL_TARGET_CONDENSED_FORM
                    if float(T_K) >= liquidus
                    else "partially_molten"
                )
            return result
        if above_liquidus:
            result["conflict"] = "partially_molten_above_liquidus"
            return result
        if solidus is not None and below_solidus:
            result["conflict"] = "partially_molten_below_solidus"
            return result
        if straddles_liquidus:
            result["straddles"] = "liquidus_K"
        return result
    return result


def load_condensed_form_corrections(
    path: Path | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Load the generalized form-correction catalog keyed by correction_id.

    Catalog entries declare balanced stoichiometry, composition frame, both
    thermo branch references, valid T domain, and provenance. Empty catalog
    is valid: the gate then has no category-2 path and fail-closes mismatches.
    """

    p = path or CONDENSED_FORM_CORRECTIONS_PATH
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        return {}
    corrections = raw.get("corrections") or {}
    if not isinstance(corrections, Mapping):
        return {}
    return {
        str(cid): dict(block)
        for cid, block in corrections.items()
        if isinstance(block, Mapping)
    }


def form_correction_delta_log10_alpha(
    *,
    T_K: float,
    delta_G_o_minus_r_J_mol: float,
    nu_c: float = 1.0,
    nu_g: float = 1.0,
) -> float:
    """Flux-equivalent base-10 alpha correction for a form change.

    Derivation (design 2026-08-09-condensed-form § form-match-or-corrected):

    Common reaction basis (non-target µ fixed on the same frame)::

        ν_c C(f) + … ⇌ ν_g V(g) + …

    Equilibrium shift from observed form o to rail form r::

        ln(p_o / p_r) = (ν_c / ν_g) · (G_o − G_r) / (R T)

    Hertz–Knudsen flux J = α C(T) p_eq implies the rail-equivalent α::

        log10 α_{o→r} = log10 α_o
                        + (ν_c / ν_g) · (G_o − G_r) / (R T ln 10)

    Sign check: higher-G observed form → higher p_eq → larger rail-equivalent α.
    Solid-below-Tm → liquid rail: G_s − G_l < 0 → liquid-equivalent α shrinks.

    Unit check::

        (J mol⁻¹) / ((J mol⁻¹ K⁻¹) · K · ln 10) = dimensionless dex.

    At 1700 K, 10 kJ/mol with ν_c/ν_g = 1 is 0.307 dex.
    """

    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError(f"form correction requires T_K > 0, got {T_K!r}")
    if not math.isfinite(delta_G_o_minus_r_J_mol):
        raise ValueError("form correction requires finite ΔG")
    if not math.isfinite(nu_c) or not math.isfinite(nu_g) or nu_g == 0.0:
        raise ValueError("form correction requires finite nu_c and non-zero nu_g")
    # Δlog10 α = (ν_c/ν_g) · ΔG / (R T ln 10)
    return (nu_c / nu_g) * delta_G_o_minus_r_J_mol / (
        _R_GAS_J_MOL_K * float(T_K) * math.log(10.0)
    )


def resolve_form_correction(
    obs: AdoptedObservation,
    *,
    T_K: float,
    corrections: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[float | None, str | None, Mapping[str, Any] | None]:
    """Attempt a category-2 form correction at ``T_K``.

    Returns ``(delta_log10_alpha, refusal_suffix_or_none, runtime_detail)``.
    A successful correction still yields only a non-pin-bearing
    ``form-corrected`` diagnostic (fail-closed for T3 residual maxima).

    Computable only when the catalog supplies same-composition G branches
    with overlapping certified domains at T. Solid-solution carriers that
    lack partial-molar G, multiphase mixtures without phase fractions, and
    out-of-domain CEA polynomials refuse here (Costa Fo93Fa7 is the audit
    exemplar: Mg2SiO4(L) domain starts 2170 K; Fo93Fa7 is not pure Mg2SiO4).
    """

    form = observation_condensed_form(obs)
    if form is None:
        return None, "form_unresolved", {"detail": "missing_condensed_form"}
    correction_id = form.get("correction_id") or obs.values.get("correction_id")
    if correction_id is None or not str(correction_id).strip():
        return (
            None,
            "missing_form_correction",
            {"detail": "no_correction_id_on_observation"},
        )
    catalog = corrections if corrections is not None else load_condensed_form_corrections()
    entry = catalog.get(str(correction_id))
    if entry is None:
        return (
            None,
            "missing_form_correction",
            {"detail": f"correction_id_not_in_catalog:{correction_id}"},
        )
    # Domain gate — never extrapolate CEA polynomials outside declared range.
    domain = entry.get("valid_T_K") or entry.get("T_domain_K")
    if isinstance(domain, (list, tuple)) and len(domain) == 2:
        try:
            t_lo, t_hi = float(domain[0]), float(domain[1])
        except (TypeError, ValueError):
            return None, "form_correction_domain_invalid", {"correction_id": correction_id}
        if not (t_lo <= float(T_K) <= t_hi):
            return (
                None,
                "form_correction_out_of_domain",
                {
                    "correction_id": correction_id,
                    "T_K": float(T_K),
                    "valid_T_K": [t_lo, t_hi],
                },
            )
    # Prefer pre-tabulated ΔG(T) samples; do not invent G from species names.
    delta_g = entry.get("delta_G_o_minus_r_J_mol")
    if delta_g is None:
        # Optional T-table: list of {T_K, delta_G_o_minus_r_J_mol}
        table = entry.get("delta_G_table") or []
        if isinstance(table, Sequence):
            for row in table:
                if not isinstance(row, Mapping):
                    continue
                try:
                    if abs(float(row["T_K"]) - float(T_K)) < 0.5:
                        delta_g = row["delta_G_o_minus_r_J_mol"]
                        break
                except (KeyError, TypeError, ValueError):
                    continue
    if delta_g is None:
        return (
            None,
            "missing_partial_molar_G",
            {"correction_id": correction_id, "detail": "no_delta_G_at_T"},
        )
    try:
        delta_g_f = float(delta_g)
        nu_c = float(entry.get("nu_c", 1.0))
        nu_g = float(entry.get("nu_g", 1.0))
        delta = form_correction_delta_log10_alpha(
            T_K=float(T_K),
            delta_G_o_minus_r_J_mol=delta_g_f,
            nu_c=nu_c,
            nu_g=nu_g,
        )
    except (TypeError, ValueError) as exc:
        return None, "form_correction_numeric_invalid", {"error": str(exc)}
    return (
        delta,
        None,
        {
            "correction_id": str(correction_id),
            "delta_G_o_minus_r_J_mol": delta_g_f,
            "nu_c": nu_c,
            "nu_g": nu_g,
            "delta_log10_alpha": delta,
            "fidelity": "status_bearing_non_authoritative",
        },
    )


def rail_condensed_form_comparability(
    obs: AdoptedObservation,
    *,
    T_K: float | None = None,
    corrections: Mapping[str, Mapping[str, Any]] | None = None,
    point_T_is_measured: bool = False,
) -> tuple[bool, str | None, str | None, Mapping[str, Any] | None]:
    """Whether the observation form is rail-comparable (match or correctable).

    Returns
    ``(pin_bearing_comparable, state_or_none, skip_reason_suffix_or_none,
    form_runtime_detail_or_none)``.

    Outcomes (design §form-match-or-corrected):

    1. **Exact match** — ``state == liquid_melt`` → pin-bearing comparable.
    2. **Corrected diagnostic** — non-match with a catalog-backed G correction
       valid at T → not pin-bearing (``form_corrected`` path); caller emits a
       non-residual status.
    3. **Excluded** — missing/unresolved/mismatch without correction → typed
       ``not_comparable_condensed_form:<state>`` or ``form_unresolved``.

    LABEL-TRUST backstop (grok P1): a typed claim is cross-checked against the
    typed ``transition_context`` whenever both T and transitions are available
    (:func:`_form_claim_T_consistency`). A claim that contradicts its own
    transitions is downgraded to ``form_unresolved:claim_conflict:<token>`` —
    fail-closed, naming the conflict. A non-match row whose T_range straddles
    the transition keeps its whole-row exclusion but with the typed
    ``:straddles_transition`` suffix; a *measured* per-point T on a
    ``partially_molten`` row splits at the boundary (molten-side points are
    liquid state-at-measurement).

    Composes with :func:`rail_system_class_comparability`: residual pins require
    class-comparable AND form pin-bearing.
    """

    state = observation_condensed_form_state(obs)
    if state is None:
        # Fail closed: untyped form cannot ground a liquid-melt residual pin.
        # Coverage ledger keeps the row with a typed reason.
        return False, None, "form_unresolved", None
    if state not in CONDENSED_FORM_STATES:
        return (
            False,
            state,
            f"not_comparable_condensed_form:{state}",
            None,
        )

    transitions = observation_form_transition_context(obs)
    consistency = _form_claim_T_consistency(
        obs, state, transitions, T_K, point_T_is_measured=point_T_is_measured
    )
    detail: dict[str, Any] = {}
    if consistency["checked"]:
        detail["form_T_consistency"] = consistency
    form_detail: Mapping[str, Any] | None = detail or None

    if consistency["conflict"] is not None:
        # Typed claim contradicts its own typed transitions at this T — the
        # label cannot be trusted in either direction; fail closed.
        return (
            False,
            state,
            f"form_unresolved:claim_conflict:{consistency['conflict']}",
            form_detail,
        )

    if state == RAIL_TARGET_CONDENSED_FORM:
        return True, state, None, form_detail
    if state == "unresolved":
        return False, state, "form_unresolved", form_detail

    # Remaining states are exactly the closed non-match set (enforced, not
    # fall-through): anything else already returned above.
    if state not in RAIL_NONMATCH_CONDENSED_FORMS:
        return False, state, "form_unresolved", form_detail

    # State-at-measurement split (grok P2): a measured point on the molten
    # side of the typed liquidus of a partially_molten row is a valid liquid
    # point. Only genuinely per-point payloads reach this (caller vouches via
    # point_T_is_measured); aggregate/midpoint-scored rows keep the whole-row
    # exclusion with the typed straddles_transition suffix below.
    if consistency["point_state"] == RAIL_TARGET_CONDENSED_FORM:
        split_detail = dict(detail)
        split_detail["form_point_resolution"] = (
            "partially_molten_point_liquid_side_of_liquidus"
        )
        return True, RAIL_TARGET_CONDENSED_FORM, None, split_detail

    # Non-match: attempt category-2 correction only when T is known and a
    # correction_id is declared. Without both, typed exclusion.
    if T_K is not None:
        delta, refusal, corr_detail = resolve_form_correction(
            obs, T_K=float(T_K), corrections=corrections
        )
        if delta is not None and refusal is None:
            # Corrected path is visible but NOT pin-bearing.
            merged = dict(detail)
            merged.update(corr_detail)
            return False, state, "form_corrected", merged

    suffix = f"not_comparable_condensed_form:{state}"
    if consistency["straddles"] is not None:
        # Whole-row exclusion of a straddling row must stay LABELED (grok P2):
        # the molten-side points are knowingly excluded because the payload
        # does not allow a per-point split.
        suffix += ":straddles_transition"
    return (False, state, suffix, form_detail)


def rail_alpha_comparability(
    obs: AdoptedObservation,
    *,
    T_K: float | None = None,
    point_T_is_measured: bool = False,
) -> tuple[bool, str | None, Mapping[str, Any]]:
    """Class ∧ form comparability for α residual pins.

    Returns ``(pin_bearing, primary_skip_suffix_or_none, runtime_axes)``.

    Dual-axis exclusions (grok P2) record BOTH reasons: the primary suffix is
    the compound ``<class_skip>+<form_skip>`` (class first) and
    ``runtime_axes["skip_reasons_all"]`` carries the ordered list.
    """

    class_ok, system_class, class_skip = rail_system_class_comparability(obs)
    form_ok, form_state, form_skip, form_detail = rail_condensed_form_comparability(
        obs, T_K=T_K, point_T_is_measured=point_T_is_measured
    )
    runtime: dict[str, Any] = {
        "system_class": system_class,
        "rail_system_class_comparable": class_ok,
        # b-190: `class_ok` is True both for a verified-comparable class and for
        # an untyped one. The verdict separates them without changing the gate.
        "rail_system_class_verdict": rail_system_class_verdict(obs),
        "condensed_form_state": form_state,
        "rail_condensed_form_comparable": form_ok,
        "rail_target_condensed_form": RAIL_TARGET_CONDENSED_FORM,
    }
    if form_detail is not None:
        detail = dict(form_detail)
        consistency = detail.pop("form_T_consistency", None)
        if consistency is not None:
            runtime["form_T_consistency"] = consistency
        resolution = detail.pop("form_point_resolution", None)
        if resolution is not None:
            runtime["form_point_resolution"] = resolution
        if detail:
            runtime["form_correction"] = detail
    skips = [
        skip
        for skip in (
            class_skip if not class_ok else None,
            form_skip if not form_ok else None,
        )
        if skip is not None
    ]
    if skips:
        if len(skips) > 1:
            runtime["skip_reasons_all"] = list(skips)
        return False, "+".join(skips), runtime
    return True, None, runtime


def _evaluate_psat(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    vp_data: Mapping[str, Any],
    pO2: float | None,
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
        typed_skip = (
            skip if skip.startswith(_TYPED_SKIP_PREFIX) else f"{_TYPED_SKIP_PREFIX}{skip}"
        )
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
                runtime={
                    "skip_reason": typed_skip,
                    "pO2_bar": pO2,
                    "n_point_drops": len(drops),
                },
            )
        )
        evaluation.skip_reason = typed_skip
        evaluation.skip_reasons.append(typed_skip)
        evaluation.runtime_notes.append(f"psat payload skip: {typed_skip}")
        return evaluation

    assert points is not None
    candidates = _engine_species_candidates(obs)
    standard_state_kind = _pressure_standard_state_kind(obs)
    melt_recipe, melt_recipe_source, melt_recipe_error = _melt_recipe_mol(obs)
    any_numeric = False
    last_refusal: str | None = None
    temperature_counts = Counter(float(point["T_K"]) for point in points)
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
            "standard_state_kind": standard_state_kind,
            "melt_recipe_source": melt_recipe_source,
        }
        unsupported = False
        out_of_domain = False
        matched_species: str | None = None

        for species in candidates:
            if standard_state_kind == "pure_component":
                P_pure, refuse_pure = _engine_pure_psat_pa(species, T_K, vp_data)
                if P_pure is not None:
                    actual = P_pure
                    matched_species = species
                    runtime["engine_path"] = "vapor_rail_pure_component"
                    runtime["species"] = species
                    break
                if refuse_pure:
                    last_refusal = refuse_pure
                    runtime.setdefault("pure_refusals", {})[species] = refuse_pure
                continue

            if standard_state_kind is None:
                last_refusal = "missing_condition:standard_state_boundary"
                runtime["refusal"] = last_refusal
                continue
            if melt_recipe is None:
                last_refusal = melt_recipe_error or "missing_condition:melt_composition"
                runtime["refusal"] = last_refusal
                continue
            if pO2 is None:
                last_refusal = "missing_condition:pO2_boundary"
                runtime["refusal"] = last_refusal
                continue
            else:
                P_melt, refuse_melt, melt_rt = _engine_melt_psat_pa(
                    species,
                    T_K,
                    pO2,
                    vp_data,
                    account_mol=melt_recipe,
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
            observable_id=(
                f"{obs.observation_id}:T={T_K:g}"
                + (
                    f":point={pt.get('point_index')}"
                    if temperature_counts[T_K] > 1
                    else ""
                )
            ),
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
        evaluation.skip_reasons.append(evaluation.skip_reason)
    return evaluation


def _evaluate_rate(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    vp_data: Mapping[str, Any],
    pO2: float | None,
) -> ObservationEvaluation:
    """Compare measured flux only when rail conditions match; otherwise diagnose."""

    points, skip, drops = _literature_rate_points(obs)
    if points is None and _has_alpha_payload(obs):
        evaluation.runtime_notes.append(
            "legacy rate_series payload contains alpha, not a measured rate; alpha path used"
        )
        return _evaluate_alpha(obs, evaluation)
    if drops:
        _emit_point_drop_records(
            obs,
            evaluation,
            drops,
            units="mol m^-2 s^-1",
            extra_runtime={"engine_path": "vapor_rail_plus_hkl"},
        )
    if points is None:
        reason = skip or "missing_numeric_species_rate"
        typed = f"{_TYPED_SKIP_PREFIX}{reason}"
        evaluation.records.append(
            _compare_point(
                obs=obs,
                observable_id=f"{obs.observation_id}:payload",
                species=obs.species_id,
                coordinate={"window": "payload-absent"},
                expected=None,
                uncertainty=None,
                actual=None,
                units=str(obs.units or "mol m^-2 s^-1"),
                runtime={"skip_reason": typed, "engine_path": "vapor_rail_plus_hkl"},
            )
        )
        evaluation.skip_reason = typed
        evaluation.skip_reasons.append(typed)
        return evaluation

    candidates = _engine_species_candidates(obs)
    melt_recipe, melt_recipe_source, melt_recipe_error = _melt_recipe_mol(obs)
    condition_gaps: list[str] = []
    if melt_recipe is None:
        condition_gaps.append(melt_recipe_error or "missing_condition:melt_composition")
    if pO2 is None:
        condition_gaps.append("missing_condition:pO2_boundary")
    diagnostic_pO2 = float(pO2) if pO2 is not None else DEFAULT_PO2_BAR
    if condition_gaps:
        evaluation.runtime_notes.append(
            "HKL assumption diagnostic excluded from external residuals: "
            + "; ".join(condition_gaps)
        )
    any_numeric = False
    last_refusal = "missing_capability:vapor_rail_species_or_hkl_inputs"
    for point in points:
        T_K = float(point["T_K"])
        expected = float(point["rate_mol_m2_s"])
        uncertainty = resolve_uncertainty(obs, point=point, kind_hint="rate")
        actual = None
        matched = None
        runtime: dict[str, Any] = {
            "engine_path": "vapor_rail_plus_hkl",
            "temperature_K": T_K,
            "pO2_bar": diagnostic_pO2,
            "pO2_defaulted": pO2 is None,
            "candidates": candidates,
            "source_units": point.get("source_units"),
            "melt_recipe_source": melt_recipe_source,
            "condition_gaps": list(condition_gaps),
            "engine_uncertainty": (
                "unavailable: vapor-pressure, alpha, composition, and pO2 "
                "components are not jointly quantified"
            ),
        }
        for species in candidates:
            if melt_recipe is not None and pO2 is not None:
                pressure, refusal, pressure_runtime = _engine_melt_psat_pa(
                    species,
                    T_K,
                    pO2,
                    vp_data,
                    account_mol=melt_recipe,
                )
                pressure_path = "melt_oxide_vapor_pressure"
            else:
                pressure, refusal, pressure_runtime = _engine_melt_psat_pa(
                    species,
                    T_K,
                    diagnostic_pO2,
                    vp_data,
                )
                pressure_path = "pure_parent_oxide_assumption_diagnostic"
                if pressure is None:
                    pressure, pure_refusal = _engine_pure_psat_pa(
                        species, T_K, vp_data
                    )
                    refusal = refusal or pure_refusal
                    pressure_path = "pure_component_assumption_diagnostic"
            if pressure is None:
                last_refusal = refusal or last_refusal
                runtime.setdefault("pressure_refusals", {})[species] = last_refusal
                continue
            alpha, alpha_refusal, alpha_runtime = _engine_alpha(species, T_K)
            if alpha is None:
                last_refusal = alpha_refusal or "missing_capability:grounded_alpha"
                runtime.setdefault("alpha_refusals", {})[species] = last_refusal
                continue
            try:
                molar_mass = species_molar_mass_kg_mol(species)
                actual = langmuir_molar_flux(
                    T_K,
                    pressure,
                    0.0,
                    alpha,
                    molar_mass_kg_mol=molar_mass,
                )
            except (KeyError, TypeError, ValueError) as exc:
                last_refusal = f"missing_capability:hkl_species_inputs:{type(exc).__name__}"
                runtime.setdefault("hkl_refusals", {})[species] = str(exc)
                continue
            matched = species
            runtime.update(
                {
                    "species": species,
                    "pressure_path": pressure_path,
                    "p_eq_pa": pressure,
                    "alpha": alpha,
                    "molar_mass_kg_mol": molar_mass,
                    "alpha_context": alpha_runtime,
                    "pressure_runtime": pressure_runtime,
                }
            )
            break

        record = _compare_point(
            obs=obs,
            observable_id=f"{obs.observation_id}:T={T_K:g}:rate",
            species=matched or obs.species_id,
            coordinate={"temperature_K": T_K},
            expected=expected,
            uncertainty=uncertainty,
            actual=actual,
            units="mol m^-2 s^-1",
            runtime=runtime,
            unsupported_speciation=actual is None,
            assumed_input=bool(condition_gaps and actual is not None),
        )
        evaluation.records.append(record)
        if actual is not None:
            any_numeric = True
        if record.status == "mismatch":
            dex = residual_dex(record)
            evaluation.findings.append(
                f"FINDING mismatch {obs.species_id} rate T={T_K:g}K "
                f"expected={expected:.6g} actual={actual:.6g} "
                f"residual_dex={dex:.6g} budget={uncertainty}"
            )

    if condition_gaps:
        typed_gaps = [
            gap if gap.startswith(_TYPED_SKIP_PREFIX) else f"{_TYPED_SKIP_PREFIX}{gap}"
            for gap in condition_gaps
        ]
        evaluation.skip_reasons.extend(typed_gaps)
        evaluation.skip_reason = typed_gaps[0]
    elif not any_numeric:
        evaluation.skip_reason = f"{_TYPED_SKIP_PREFIX}{last_refusal}"
        evaluation.skip_reasons.append(evaluation.skip_reason)
    return evaluation


def _evaluate_alpha(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
) -> ObservationEvaluation:
    # Class + form gates run before payload-specific early returns so form
    # exclusions remain visible even when alpha payload parsing also fails
    # (design: 41 function-comparable vs 38 payload-reachable rows).
    pin_ok_obs, axes_skip, axes_runtime = rail_alpha_comparability(obs)
    axes_typed_skip = (
        f"{_TYPED_SKIP_PREFIX}{axes_skip}" if axes_skip is not None else None
    )
    if not pin_ok_obs and axes_typed_skip is not None:
        evaluation.runtime_notes.append(
            "α not pin-bearing for silicate-melt liquid rail: "
            f"class={axes_runtime.get('system_class')!r} "
            f"form={axes_runtime.get('condensed_form_state')!r} "
            f"({axes_typed_skip})"
        )

    points, skip, drops = _literature_alpha_points(obs)
    if drops:
        _emit_point_drop_records(
            obs,
            evaluation,
            drops,
            units=str(obs.units or "alpha"),
        )
    if skip is not None:
        # Form/class exclusion takes precedence over payload gaps so the
        # coverage skip ledger records the comparability reason first.
        if not pin_ok_obs and axes_typed_skip is not None:
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
                    runtime={
                        "skip_reason": axes_typed_skip,
                        "payload_skip": (
                            skip
                            if skip.startswith(_TYPED_SKIP_PREFIX)
                            else f"{_TYPED_SKIP_PREFIX}{skip}"
                        ),
                        "n_point_drops": len(drops),
                        **axes_runtime,
                    },
                    out_of_domain=True,
                )
            )
            evaluation.skip_reason = axes_typed_skip
            evaluation.skip_reasons.append(axes_typed_skip)
            return evaluation
        typed_skip = (
            skip if skip.startswith(_TYPED_SKIP_PREFIX) else f"{_TYPED_SKIP_PREFIX}{skip}"
        )
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
                runtime={
                    "skip_reason": typed_skip,
                    "n_point_drops": len(drops),
                    **axes_runtime,
                },
            )
        )
        evaluation.skip_reason = typed_skip
        evaluation.skip_reasons.append(typed_skip)
        return evaluation

    assert points is not None
    candidates = _engine_species_candidates(obs)
    any_numeric = False
    last_refusal: str | None = None
    temperature_counts = Counter(float(point["T_K"]) for point in points)
    # Observation-level skip (class/form) is the default; per-point form
    # re-check allows T-dependent correction domain gates and the
    # state-at-measurement split of straddling rows (only genuine per-point
    # (T, α) measurements split — aggregate/midpoint-synthesized points keep
    # the whole-row verdict).
    any_pin_bearing = False
    analytical_ceiling_seen = False
    for pt in points:
        T_K = float(pt["T_K"])
        expected = float(pt["alpha"])
        unc = resolve_uncertainty(obs, point=pt, kind_hint="alpha")
        point_T_is_measured = (
            pt.get("T_provenance") == "explicit"
            and pt.get("series_name") in _PER_POINT_ALPHA_SERIES_NAMES
        )
        pin_ok, point_skip, point_axes = rail_alpha_comparability(
            obs, T_K=T_K, point_T_is_measured=point_T_is_measured
        )
        point_typed_skip = (
            f"{_TYPED_SKIP_PREFIX}{point_skip}" if point_skip is not None else None
        )
        actual = None
        matched = None
        runtime: dict[str, Any] = {
            "temperature_K": T_K,
            "candidates": candidates,
            "geometry_assumption": obs.geometry_assumption,
            "engine_path": "grounded_alpha",
            **point_axes,
        }
        if point_typed_skip is not None:
            runtime["skip_reason"] = point_typed_skip
        # Category-2: apply numeric form correction to the literature α when
        # a catalog entry resolves. Result stays non-pin-bearing (out-of-domain).
        compare_expected = expected
        if point_skip == "form_corrected" and point_axes.get("form_correction"):
            delta = point_axes["form_correction"].get("delta_log10_alpha")
            if delta is not None and expected > 0.0:
                compare_expected = expected * (10.0 ** float(delta))
                runtime["form_corrected_alpha"] = compare_expected
                runtime["literature_alpha_uncorrected"] = expected
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
                        ALPHA_AUTHORITY_STATUS_FIELD,
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
        analytical_ceiling = (
            (runtime.get("alpha_context") or {}).get(
                ALPHA_AUTHORITY_STATUS_FIELD
            )
            == "analytical_upper_bound"
        )
        if analytical_ceiling:
            analytical_ceiling_seen = True
            runtime["skip_reason"] = (
                f"{_TYPED_SKIP_PREFIX}analytical_upper_bound_not_measurement"
            )
        # Pin-bearing only when class AND form match. Form-corrected and all
        # other non-matches keep values for audit but out-of-domain status
        # excludes them from residual pins.
        out_of_domain = not pin_ok
        record = _compare_point(
            obs=obs,
            observable_id=(
                f"{obs.observation_id}:T={T_K:g}"
                + (
                    f":point={pt.get('point_index')}"
                    if temperature_counts[T_K] > 1
                    else ""
                )
            ),
            species=matched or obs.species_id,
            coordinate={"temperature_K": T_K},
            expected=compare_expected,
            uncertainty=unc,
            actual=actual,
            units="alpha",
            runtime=runtime,
            unsupported_speciation=actual is None and pin_ok,
            out_of_domain=out_of_domain,
            assumed_input=analytical_ceiling,
        )
        evaluation.records.append(record)
        if pin_ok and not analytical_ceiling:
            any_pin_bearing = True
        if pin_ok and record.status == "mismatch":
            extr_tag = " extrapolated: true" if extrapolated else ""
            evaluation.findings.append(
                f"FINDING mismatch {obs.species_id} α T={T_K:g}K "
                f"expected={compare_expected:.6g} actual={actual} budget={unc}{extr_tag}"
            )

    if not any_pin_bearing and axes_typed_skip is not None:
        evaluation.skip_reason = axes_typed_skip
        evaluation.skip_reasons.append(axes_typed_skip)
    elif analytical_ceiling_seen and not any_pin_bearing:
        evaluation.skip_reason = (
            f"{_TYPED_SKIP_PREFIX}analytical_upper_bound_not_measurement"
        )
        evaluation.skip_reasons.append(evaluation.skip_reason)
    elif not any_numeric:
        evaluation.skip_reason = (
            f"{_TYPED_SKIP_PREFIX}{last_refusal or 'alpha_unsupported'}"
        )
        evaluation.skip_reasons.append(evaluation.skip_reason)
    return evaluation


def _melt_recipe_mol(
    obs: AdoptedObservation,
) -> tuple[dict[str, float] | None, str, str | None]:
    """Resolve an extract recipe to oxide moles, including the production 12022 proxy."""

    values = obs.values
    for key in ("composition_mol", "melt_composition_mol"):
        raw = values.get(key)
        if isinstance(raw, Mapping) and raw:
            try:
                return {str(k): float(v) for k, v in raw.items()}, key, None
            except (TypeError, ValueError):
                return None, key, "invalid_melt_composition_mol"

    raw_wt = (
        values.get("composition_wt_pct")
        or values.get("melt_composition_wt_pct")
        # t-383 DeMaria digitized rows carry sample-specific oxide suites.
        or values.get("sample_oxide_composition_wt_pct")
    )
    provenance = "extract composition_wt_pct"
    if isinstance(values.get("sample_oxide_composition_wt_pct"), Mapping) and raw_wt is values.get(
        "sample_oxide_composition_wt_pct"
    ):
        provenance = "extract sample_oxide_composition_wt_pct"
    material = str(values.get("material") or obs.phase or "").lower()
    if not isinstance(raw_wt, Mapping) and (
        "apollo_12" in material or "lunar_basalt_120" in material
    ):
        feedstocks = yaml.safe_load(FEEDSTOCKS_PATH.read_text(encoding="utf-8")) or {}
        feedstock = feedstocks.get("lunar_mare_low_ti") or {}
        raw_wt = feedstock.get("composition_wt_pct")
        provenance = "data/feedstocks.yaml::lunar_mare_low_ti (Apollo 12 proxy)"
    # Prefer sample_Na2O_wt_pct overlay on Apollo proxy when bulk suite absent
    # (12022 text gives only alkali wt%; full suite is the production proxy).
    if isinstance(raw_wt, Mapping) and values.get("sample_Na2O_wt_pct") is not None:
        try:
            overlaid = dict(raw_wt)
            overlaid["Na2O"] = float(values["sample_Na2O_wt_pct"])
            if values.get("sample_K2O_wt_pct") is not None:
                overlaid["K2O"] = float(values["sample_K2O_wt_pct"])
            raw_wt = overlaid
            provenance = f"{provenance}+sample_alkali_wt_pct_overlay"
        except (TypeError, ValueError):
            pass
    if isinstance(raw_wt, Mapping) and raw_wt:
        try:
            mol = {
                str(oxide): float(wt) / float(MOLAR_MASS[str(oxide)])
                for oxide, wt in raw_wt.items()
                if float(wt) > 0.0 and str(oxide) in MOLAR_MASS
            }
        except (TypeError, ValueError):
            return None, provenance, "invalid_melt_composition_wt_pct"
        if mol:
            return mol, provenance, None
    return None, "none", "missing_condition:melt_composition"


def _activity_skip(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
    reason: str,
    *,
    expected: float | None = None,
    observable: str = "activity",
) -> ObservationEvaluation:
    typed = reason if reason.startswith(_TYPED_SKIP_PREFIX) else f"{_TYPED_SKIP_PREFIX}{reason}"
    evaluation.records.append(
        _compare_point(
            obs=obs,
            observable_id=f"{obs.observation_id}:{observable}",
            species=obs.species_id,
            coordinate={"window": "activity-model-gap"},
            expected=expected,
            uncertainty=(
                resolve_uncertainty(obs, kind_hint="activity")
                if expected is not None
                else None
            ),
            actual=None,
            units="dimensionless",
            runtime={"skip_reason": typed},
            out_of_domain=expected is not None,
        )
    )
    evaluation.skip_reason = typed
    evaluation.skip_reasons.append(typed)
    return evaluation


def _evaluate_activity(
    obs: AdoptedObservation,
    evaluation: ObservationEvaluation,
) -> ObservationEvaluation:
    values = obs.values
    semantics = str(values.get("semantics") or "")
    if "bound_not_point" in semantics or "ordering" in semantics.lower():
        return _activity_skip(
            obs,
            evaluation,
            "unsupported_observable:qualitative_activity_ordering",
            observable="qualitative",
        )

    expected = None
    observable_kind = None
    for key in ("gamma", "activity", "value"):
        if values.get(key) is None:
            continue
        try:
            expected = float(values[key])
            observable_kind = "gamma" if key == "gamma" else "activity"
            break
        except (TypeError, ValueError):
            continue
    if expected is None or observable_kind is None:
        return _activity_skip(
            obs,
            evaluation,
            "missing_numeric_activity",
            observable="payload",
        )

    parent_oxide = PARENT_OXIDE_BY_ENGINE_SPECIES.get(obs.species_id)
    if parent_oxide is None and obs.species_id in MOLAR_MASS:
        parent_oxide = obs.species_id
    if parent_oxide is None:
        return _activity_skip(
            obs,
            evaluation,
            f"missing_capability:parent_oxide_mapping:{obs.species_id}",
            expected=expected,
            observable=observable_kind,
        )

    runtime: dict[str, Any] = {
        "engine_path": "melt_oxide_activity",
        "parent_oxide": parent_oxide,
        "engine_uncertainty": (
            "unavailable: melt-activity coefficient/model uncertainty is not "
            "quantified by this engine path"
        ),
    }
    assumption_gaps: list[str] = []
    if observable_kind == "gamma":
        coefficient = MELT_OXIDE_ACTIVITY_COEFFICIENTS.get(parent_oxide)
        if coefficient is None:
            return _activity_skip(
                obs,
                evaluation,
                f"missing_capability:melt_activity_gamma:{parent_oxide}",
                expected=expected,
                observable=observable_kind,
            )
        actual = float(coefficient.gamma)
        runtime["citation"] = coefficient.citation
    else:
        recipe, recipe_source, recipe_error = _melt_recipe_mol(obs)
        runtime["recipe_source"] = recipe_source
        if recipe is None:
            return _activity_skip(
                obs,
                evaluation,
                recipe_error or "missing_condition:melt_composition",
                expected=expected,
                observable=observable_kind,
            )
        result = melt_oxide_activity(parent_oxide, recipe)
        if result is None:
            return _activity_skip(
                obs,
                evaluation,
                f"missing_capability:melt_activity:{parent_oxide}",
                expected=expected,
                observable=observable_kind,
            )
        actual = float(result.activity)
        runtime.update(result.provenance())
        warning = getattr(result, "warning", None)
        if warning:
            assumption_gaps.append(
                f"missing_capability:documented_melt_activity_coefficient:{parent_oxide}"
            )
        if "proxy" in recipe_source.lower() or "modeled" in recipe_source.lower():
            assumption_gaps.append("missing_condition:source_sample_composition")
        standard_state = str(obs.standard_state or "").lower()
        if "pure fe" in standard_state or "pure iron" in standard_state:
            assumption_gaps.append(
                "missing_capability:reference_state_conversion:pure_Fe_to_FeO"
            )
        qualifier_text = " ".join(
            str(value)
            for value in (
                values.get("activity_note"),
                (obs.uncertainty or {}).get("note")
                if isinstance(obs.uncertainty, Mapping)
                else obs.uncertainty,
            )
            if value is not None
        ).lower()
        if any(
            token in qualifier_text
            for token in ("nearly", "figure-only", "without tabulated", "qualitative")
        ):
            assumption_gaps.append(
                "unsupported_observable:qualitative_activity_not_point"
            )
        runtime["condition_gaps"] = list(dict.fromkeys(assumption_gaps))

    T_mid = (
        0.5 * (float(obs.T_range_K[0]) + float(obs.T_range_K[1]))
        if obs.T_range_K is not None
        else None
    )
    record = _compare_point(
        obs=obs,
        observable_id=f"{obs.observation_id}:{observable_kind}",
        species=obs.species_id,
        coordinate=(
            {"temperature_K": T_mid}
            if T_mid is not None
            else {"window": "temperature-not-stated"}
        ),
        expected=expected,
        uncertainty=resolve_uncertainty(obs, kind_hint="activity"),
        actual=actual,
        units="dimensionless",
        runtime=runtime,
        assumed_input=bool(assumption_gaps),
    )
    evaluation.records.append(record)
    if assumption_gaps:
        typed_gaps = [
            gap if gap.startswith(_TYPED_SKIP_PREFIX) else f"{_TYPED_SKIP_PREFIX}{gap}"
            for gap in dict.fromkeys(assumption_gaps)
        ]
        evaluation.skip_reasons.extend(typed_gaps)
        evaluation.skip_reason = typed_gaps[0]
        evaluation.runtime_notes.append(
            "activity assumption diagnostic excluded from external residuals: "
            + "; ".join(typed_gaps)
        )
        return evaluation
    if record.status == "mismatch":
        dex = residual_dex(record)
        evaluation.findings.append(
            f"FINDING mismatch {obs.species_id} {observable_kind} "
            f"expected={expected:.6g} actual={actual:.6g} "
            f"residual_dex={dex if dex is not None else 'n/a'} "
            f"budget={record.expected_uncertainty}"
        )
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


def normalized_residual(record: ComparisonRecord) -> float | None:
    """Absolute residual divided by the literature-side uncertainty budget."""

    if (
        record.expected_value is None
        or record.actual_value is None
        or record.expected_uncertainty is None
    ):
        return None
    kind = str(record.expected_uncertainty.get("kind") or "")
    try:
        tolerance = float(record.expected_uncertainty["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if tolerance <= 0.0:
        return math.inf if record.actual_value != record.expected_value else 0.0
    if kind == "absolute":
        return abs(record.actual_value - record.expected_value) / tolerance
    if kind == "relative_fraction":
        denominator = abs(record.expected_value) * tolerance
        return (
            abs(record.actual_value - record.expected_value) / denominator
            if denominator > 0.0
            else None
        )
    if kind == "log10_decades":
        dex = residual_dex(record)
        return dex / tolerance if dex is not None else None
    return None


def coverage_summary(
    evaluations: Sequence[ObservationEvaluation],
) -> dict[str, Any]:
    """Observation-first coverage ledger; records/points are secondary detail."""

    entries: list[dict[str, Any]] = []
    for evaluation in evaluations:
        comparison_family = evaluation.observation.obs_type
        if evaluation.observation.obs_type == "rate_series":
            comparison_family = (
                "alpha_in_legacy_rate_series"
                if _has_alpha_payload(evaluation.observation)
                else "rate_hkl"
            )
        comparable_points = sum(
            record.status in {"match", "mismatch"} for record in evaluation.records
        )
        gap_points = len(evaluation.records) - comparable_points
        comparable = comparable_points > 0
        reasons = list(dict.fromkeys(evaluation.skip_reasons))
        if not comparable and not reasons:
            reasons = [
                evaluation.skip_reason
                or "typed-refusal:missing_capability:comparison_not_produced"
            ]
        primary_reason = None if comparable else reasons[0]
        entries.append(
            {
                "source": evaluation.observation.source_id,
                "species": evaluation.observation.species_id,
                "type": evaluation.observation.obs_type,
                "comparison_family": comparison_family,
                "observation_id": evaluation.observation.observation_id,
                "adoption_basis": evaluation.observation.adoption_basis,
                "comparable": comparable,
                "comparable_points": comparable_points,
                "gap_points": gap_points,
                "primary_skip_reason": primary_reason,
                "partial_gap_reasons": reasons if comparable else [],
            }
        )

    def grouped(key: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            buckets[str(entry[key])].append(entry)
        out = []
        for label, rows in sorted(buckets.items()):
            skip_reasons = Counter(
                str(row["primary_skip_reason"])
                for row in rows
                if row["primary_skip_reason"] is not None
            )
            out.append(
                {
                    key: label,
                    "observations": len(rows),
                    "comparable": sum(bool(row["comparable"]) for row in rows),
                    "skipped": sum(not bool(row["comparable"]) for row in rows),
                    "comparable_points": sum(int(row["comparable_points"]) for row in rows),
                    "gap_points": sum(int(row["gap_points"]) for row in rows),
                    "skip_reasons": dict(sorted(skip_reasons.items())),
                }
            )
        return out

    skipped_reasons = Counter(
        str(entry["primary_skip_reason"])
        for entry in entries
        if entry["primary_skip_reason"] is not None
    )
    return {
        "observations": len(entries),
        "comparable": sum(bool(entry["comparable"]) for entry in entries),
        "skipped": sum(not bool(entry["comparable"]) for entry in entries),
        "comparable_points": sum(int(entry["comparable_points"]) for entry in entries),
        "gap_points": sum(int(entry["gap_points"]) for entry in entries),
        "skip_reasons": dict(sorted(skipped_reasons.items())),
        "entries": entries,
        "by_type": grouped("type"),
        "by_family": grouped("comparison_family"),
        "by_species": grouped("species"),
        "by_source": grouped("source"),
    }


def _format_reason_counts(reasons: Mapping[str, Any]) -> str:
    if not reasons:
        return "—"
    return "; ".join(f"`{reason}` ×{count}" for reason, count in reasons.items())


def _format_uncertainty(record: ComparisonRecord) -> str:
    uncertainty = record.expected_uncertainty
    if not uncertainty:
        return "—"
    kind = str(uncertainty.get("kind") or "?")
    value = uncertainty.get("value")
    source = uncertainty.get("source") or (
        "documented default" if uncertainty.get("defaulted") else "extract"
    )
    components = uncertainty.get("components") or []
    detail = f"{kind}={value} ({source})"
    if components:
        detail += "; " + ", ".join(str(component) for component in components)
    return detail.replace("|", "\\|")


def _format_residual_table(evaluations: Sequence[ObservationEvaluation]) -> str:
    lines = [
        "| Source | Observation | Type | Species | Coordinate | Literature | Literature uncertainty | Engine uncertainty | Combined propagated uncertainty | Engine | Residual | Residual dex | Residual / literature budget | Status |",
        "|---|---|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|",
    ]
    for evaluation in evaluations:
        for record in evaluation.records:
            if record.status not in {"match", "mismatch"}:
                continue
            coordinate = ", ".join(
                f"{key}={value:g}" if isinstance(value, (int, float)) else f"{key}={value}"
                for key, value in record.coordinate.items()
            )
            dex = residual_dex(record)
            normalized = normalized_residual(record)
            lines.append(
                "| {source} | {observation} | {type} | {species} | {coordinate} | "
                "{expected:.6g} | {uncertainty} | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | {actual:.6g} | {residual:.6g} | "
                "{dex} | {normalized} | {status} |".format(
                    source=evaluation.observation.source_id,
                    observation=evaluation.observation.observation_id,
                    type=evaluation.observation.obs_type,
                    species=record.species or evaluation.observation.species_id,
                    coordinate=coordinate,
                    expected=float(record.expected_value),
                    uncertainty=_format_uncertainty(record),
                    actual=float(record.actual_value),
                    residual=float(record.residual),
                    dex=f"{dex:.6g}" if dex is not None else "—",
                    normalized=(
                        f"{normalized:.6g}"
                        if normalized is not None and math.isfinite(normalized)
                        else "—"
                    ),
                    status=record.status,
                )
            )
    return "\n".join(lines)


def _format_assumption_diagnostic_table(
    evaluations: Sequence[ObservationEvaluation],
) -> str:
    """Show numerical diagnostics that are intentionally excluded from coverage."""

    lines = [
        "| Source | Observation | Type | Species | Coordinate | Literature | Assumption-only engine value | Raw residual dex | Typed gaps | Status |",
        "|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for evaluation in evaluations:
        reasons = "; ".join(evaluation.skip_reasons).replace("|", "\\|")
        for record in evaluation.records:
            if record.status != "assumed-input":
                continue
            coordinate = ", ".join(
                f"{key}={value:g}" if isinstance(value, (int, float)) else f"{key}={value}"
                for key, value in record.coordinate.items()
            )
            dex = residual_dex(record)
            lines.append(
                "| {source} | {observation} | {type} | {species} | {coordinate} | "
                "{expected:.6g} | {actual:.6g} | {dex} | {reasons} | assumed-input (excluded) |".format(
                    source=evaluation.observation.source_id,
                    observation=evaluation.observation.observation_id,
                    type=evaluation.observation.obs_type,
                    species=record.species or evaluation.observation.species_id,
                    coordinate=coordinate,
                    expected=float(record.expected_value),
                    actual=float(record.actual_value),
                    dex=f"{dex:.6g}" if dex is not None else "—",
                    reasons=reasons or "—",
                )
            )
    return "\n".join(lines)


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
        comparable_records = [r for r in records if r.status in {"match", "mismatch"}]
        dex_vals = [
            d for r in comparable_records if (d := residual_dex(r)) is not None
        ]
        abs_residuals = [
            abs(r.residual)
            for r in comparable_records
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

    n_findings = sum(1 for r in rows if r.get("classification") == "FINDING-mismatch")
    n_extrapolated_findings = 0
    coverage = coverage_summary(evaluations or []) if evaluations is not None else None
    if evaluations is not None:
        for row in rows:
            for f in row.get("findings") or []:
                if "extrapolated: true" in str(f):
                    n_extrapolated_findings += 1
    coverage_line = "Coverage computed when evaluations are supplied."
    if coverage is not None:
        coverage_line = (
            f"Observations: **{coverage['observations']} total / "
            f"{coverage['comparable']} comparable / {coverage['skipped']} skipped**. "
            f"Comparable residual points: **{coverage['comparable_points']}**; "
            f"explicit gap records: **{coverage['gap_points']}**. "
            f"Extrapolated-alpha FINDINGs: **{n_extrapolated_findings}**."
        )
    lines = [
        ROLLUP_BEGIN,
        "",
        # Stable link target for evidence_refs that cite this section. The anchor
        # lives inside the generated block, so the generator must emit it or the
        # committed-vs-generated parity guard fails on a line-count mismatch.
        '<a id="extract-store-reproduction-battery"></a>',
        "",
        "### Extract-store single-species reproduction battery (t-512)",
        "",
        "Generated from production priority-winner observations plus every KEMS",
        "extract observation of type `psat_series` / `rate_series` /",
        "`activity_coefficient` / `alpha`. Residuals",
        "are the deliverable (doctrine: *Headline accuracy is the product*).",
        "Engine refusals surface as typed skips; mismatches are FINDINGs —",
        "tolerances are **not** widened to pass. Geometry: "
        + geometry_assumption_text()
        + ".",
        "",
        coverage_line,
        "",
        f"- In-scope observations evaluated: **{coverage['observations'] if coverage is not None else '—'}**",
        f"- Comparable observations: **{coverage['comparable'] if coverage is not None else '—'}**",
        f"- Skipped observations with typed reasons: **{coverage['skipped'] if coverage is not None else '—'}**",
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

    if coverage is not None:
        lines.extend(
            [
                "",
                "**Typed observation skips (roadmap, one primary reason per skipped observation):**",
                "",
            ]
        )
        for reason, count in coverage["skip_reasons"].items():
            lines.append(f"- `{reason}`: **{count}**")

        for title, key, label in (
            ("Coverage by observation type", "by_type", "Type"),
            ("Coverage by comparison family", "by_family", "Comparison family"),
            ("Coverage by species", "by_species", "Species"),
            ("Coverage by source", "by_source", "Source"),
        ):
            lines.extend(
                [
                    "",
                    f"**{title}:**",
                    "",
                    f"| {label} | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |",
                    "|---|---:|---:|---:|---:|---:|---|",
                ]
            )
            field = {
                "by_type": "type",
                "by_family": "comparison_family",
                "by_species": "species",
                "by_source": "source",
            }[key]
            for coverage_row in coverage[key]:
                lines.append(
                    "| {label} | {observations} | {comparable} | {skipped} | "
                    "{points} | {gaps} | {reasons} |".format(
                        label=coverage_row[field],
                        observations=coverage_row["observations"],
                        comparable=coverage_row["comparable"],
                        skipped=coverage_row["skipped"],
                        points=coverage_row["comparable_points"],
                        gaps=coverage_row["gap_points"],
                        reasons=_format_reason_counts(coverage_row["skip_reasons"]),
                    )
                )

    # Default-tolerance legend
    lines.extend(
        [
            "",
            "**Uncertainty ledger:** extract-side terms are propagated when the",
            "source supplies a quantitative form (for example Arrhenius activation-energy",
            "uncertainty). The engine paths expose no quantitative joint uncertainty for",
            "vapor pressure, activity, composition/redox, or grounded alpha; therefore the",
            "combined propagated uncertainty is reported as **not computable**, not replaced",
            "with an invented model error bar. `Residual / literature budget` uses only the",
            "stated/default literature-side budget.",
            "",
            "**Default tolerances** (used only when the extract carries no usable",
            "numeric uncertainty; each defaulted comparison carries",
            "`defaulted: true` on the uncertainty dict and still scores",
            "match/mismatch against that documented budget):",
            "",
            f"- `psat_series`: `{DEFAULT_PSAT_UNCERTAINTY['kind']}` = "
            f"{DEFAULT_PSAT_UNCERTAINTY['value']} "
            f"({DEFAULT_PSAT_UNCERTAINTY['rationale']})",
            f"- `rate_series` (measured flux): `{DEFAULT_RATE_UNCERTAINTY['kind']}` = "
            f"{DEFAULT_RATE_UNCERTAINTY['value']} "
            f"({DEFAULT_RATE_UNCERTAINTY['rationale']})",
            f"- `alpha`: `{DEFAULT_ALPHA_UNCERTAINTY['kind']}` = "
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
            lines.append("Comparable per-observation residuals and uncertainty ledger:")
            lines.append("")
            lines.append(_format_residual_table(evaluations))
            lines.append("")

        assumed = [
            r
            for ev in evaluations
            for r in ev.records
            if r.status == "assumed-input"
        ]
        if assumed:
            lines.append(
                "Assumption-only engine diagnostics (visible negative results, but excluded "
                "from comparable coverage, headlines, and residual pins):"
            )
            lines.append("")
            lines.append(_format_assumption_diagnostic_table(evaluations))
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
    "DEFAULT_RATE_UNCERTAINTY",
    "ExtractReproductionError",
    "MODEL_LIMITATIONS_PATH",
    "ObservationEvaluation",
    "CONDENSED_FORM_STATES",
    "RAIL_COMPARABLE_SYSTEM_CLASSES",
    "RAIL_INCOMPARABLE_SYSTEM_CLASSES",
    "RAIL_NONMATCH_CONDENSED_FORMS",
    "RAIL_TARGET_CONDENSED_FORM",
    "ROLLUP_BEGIN",
    "ROLLUP_END",
    "TARGET_TYPES",
    "append_rollup_to_model_limitations",
    "coverage_summary",
    "evaluate_all",
    "evaluate_observation",
    "extract_rollup_section",
    "form_correction_delta_log10_alpha",
    "format_rollup_markdown",
    "geometry_assumption_text",
    "is_typed_skip",
    "load_adopted_observations",
    "load_condensed_form_corrections",
    "load_vapor_pressure_data",
    "motzfeldt_available",
    "normalized_residual",
    "observation_condensed_form",
    "observation_condensed_form_state",
    "observation_form_transition_context",
    "observation_system_class",
    "rail_alpha_comparability",
    "rail_condensed_form_comparability",
    "rail_system_class_comparability",
    "rail_system_class_verdict",
    "residual_dex",
    "resolve_chamber_pressure_pa",
    "resolve_form_correction",
    "resolve_pO2_bar",
    "resolve_uncertainty",
    "rollup_species_error_bars",
]
