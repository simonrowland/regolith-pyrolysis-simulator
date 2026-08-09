#!/usr/bin/env python3
"""Derive by-species views, coverage, and cross-source consistency from extracts.

Evidence stays per-source under ``data/literature/extracts/<source-id>.yaml``.
This tool only *reads* extracts and writes derived artifacts (never runtime
authority).

Outputs:

* **by-species** — map species_id → list of (source_id, observation) with
  competing rows retained, ``uncertainty`` propagated verbatim, and
  ``disagreement_dex`` when multiple same-observable overlapping-range sources
  contribute a comparable scalar.
* **coverage** — species × source → found/empty/absent (payload-aware: empty
  observation lists, empty/null values, and pending/pointer acquisition stubs
  do not count as found; empty is distinct from absent).
* **cross-source consistency** — auto-computed disagreement report for every
  multi-source overlapping-range observable group (no hand curation).

Usage::

  python tools/extract_merge.py --by-species -o /tmp/by_species.yaml
  python tools/extract_merge.py --coverage -o /tmp/coverage.yaml
  python tools/extract_merge.py --consistency -o /tmp/consistency.yaml
  python tools/extract_merge.py --by-species --coverage --consistency \\
      --outdir build/literature
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from validate_literature_extracts import (  # noqa: E402
    EXTRACTS_DIR,
    discover_extracts,
    load_fidelity_policy,
    pre_policy_source_ids,
    validate_extract_file,
    validate_source_priority_file,
)
from motzfeldt import effective_po2_boundary_for_observation  # noqa: E402

U0_MANIFEST = ROOT / "data" / "vapour_rail_u0_manifest.yaml"
PRIORITY_PATH = EXTRACTS_DIR / "_source_priority.yaml"

# Primary scalar keys, ordered by preference, for comparable disagreement.
# T_K is intentionally last-resort and only used for transition_point.
_SCALAR_KEYS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "alpha": ("alpha", "value", "A", "c0"),
    "psat_series": (
        "P_Pa",
        "p_Pa",
        "pressure_Pa",
        "log10_P_Pa",
        "value",
    ),
    "gibbs_table": (
        "delta_fG",
        "Delta_fG",
        "Delta_f_G_298_kJ_mol",
        "Delta_f_H_298_kJ_mol",
        "Delta_f_H_rxn_298_kJ_mol",
        "delta_f_H_298_15_J_per_mol",
        "Kd",
        "value",
    ),
    "activity_coefficient": ("gamma", "activity", "value", "alpha"),
    "rate_series": ("rate", "flux", "value", "alpha"),
    "transition_point": ("T_K", "value_K", "value"),
}


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_source_priority(
    path: Path | None = None,
    *,
    fail_closed: bool = True,
) -> dict[str, list[str]]:
    """Load per-family source priority lists.

    Null-hypothesis (P1 VALUE-PRECEDENCE fail-open): a missing/malformed file
    used to return ``{}`` and lexical source_id won. Fail-closed raises
    SystemExit (or returns empty only when fail_closed=False for debug).
    """
    p = path or PRIORITY_PATH
    if not p.is_file():
        if fail_closed:
            raise SystemExit(f"source_priority missing (fail-closed): {p}")
        return {}
    doc = _load_yaml(p)
    if not isinstance(doc, Mapping):
        if fail_closed:
            raise SystemExit(f"source_priority not a mapping (fail-closed): {p}")
        return {}
    raw = doc.get("source_priority") or {}
    if not isinstance(raw, Mapping) or not raw:
        if fail_closed:
            raise SystemExit(f"source_priority empty (fail-closed): {p}")
        return {}
    out: dict[str, list[str]] = {}
    for fam, order in raw.items():
        if isinstance(order, list) and order:
            out[str(fam)] = [str(x) for x in order]
        elif fail_closed:
            raise SystemExit(
                f"source_priority.{fam} empty or non-list (fail-closed): {p}"
            )
    return out


def load_u0_species_ids(path: Path | None = None) -> list[str]:
    p = path or U0_MANIFEST
    if not p.is_file():
        return []
    doc = _load_yaml(p)
    if not isinstance(doc, Mapping):
        return []
    species = doc.get("species")
    if isinstance(species, list):
        ids: list[str] = []
        for row in species:
            if isinstance(row, Mapping) and row.get("id"):
                ids.append(str(row["id"]))
        return ids
    if isinstance(species, Mapping):
        return [str(k) for k in species.keys()]
    return []


def load_extracts(
    directory: Path | None = None,
    *,
    require_valid: bool = True,
) -> list[dict[str, Any]]:
    """Load all extract documents. Optionally refuse invalid files."""
    files = discover_extracts(directory)
    docs: list[dict[str, Any]] = []
    errors: list[str] = []
    # Honor ENFORCED_FOR_NEW fidelity allowlist (same policy as validate_all).
    pre_policy: set[str] | None = None
    if require_valid:
        policy, policy_errs = load_fidelity_policy()
        if policy_errs:
            # Policy file missing/broken is a hard fail when validating.
            raise SystemExit("fidelity policy invalid:\n" + "\n".join(policy_errs))
        pre_policy = pre_policy_source_ids(policy)
    for path in files:
        if require_valid:
            errs = validate_extract_file(path, pre_policy_ids=pre_policy)
            if errs:
                errors.extend(errs)
                continue
        doc = _load_yaml(path)
        if isinstance(doc, Mapping):
            docs.append(dict(doc))
    if require_valid and errors:
        raise SystemExit("extract validation failed:\n" + "\n".join(errors))
    return docs


def _has_payload_leaf(values: Any) -> bool:
    if values is None:
        return False
    if isinstance(values, bool):
        return True
    if isinstance(values, (int, float)):
        return True
    if isinstance(values, str):
        return bool(values.strip())
    if isinstance(values, Mapping):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values.values())
    if isinstance(values, (list, tuple)):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    return True


def _norm_axis(val: Any) -> str:
    """Normalize phase / standard_state / regime for grouping keys."""
    if val is None:
        return ""
    s = str(val).strip().lower()
    return " ".join(s.split())


def _property_identity(obs: Mapping[str, Any]) -> str:
    """Subtype/property identity for typed-observable key.

    Transition points distinguish melting/boiling/triple via values.property
    (or property_kind). Other types may carry an explicit property/quantity.
    """
    values = obs.get("values")
    if isinstance(values, Mapping):
        for key in ("property", "property_kind", "quantity", "kind"):
            if values.get(key) is not None:
                return _norm_axis(values.get(key))
    # Fall back to observation-level property if present
    for key in ("property", "property_kind", "quantity"):
        if obs.get(key) is not None:
            return _norm_axis(obs.get(key))
    return ""


def _condensed_form_identity(obs: Mapping[str, Any]) -> str:
    """Stable identity for the typed condensed_form axis (state-at-measurement).

    Glass vs crystal of the same phase/regime must not collapse into one
    merge key (design 2026-08-09-condensed-form). Only closed-vocabulary
    state + optional polymorph + metastability enter the key.
    """
    form = obs.get("condensed_form")
    if not isinstance(form, Mapping):
        values = obs.get("values")
        if isinstance(values, Mapping):
            form = values.get("condensed_form")
    if not isinstance(form, Mapping):
        return ""
    state = _norm_axis(form.get("state"))
    polymorph = _norm_axis(form.get("polymorph_name"))
    meta = form.get("metastable")
    if meta is True:
        meta_s = "metastable"
    elif meta is False:
        meta_s = "stable"
    else:
        meta_s = ""
    return "|".join(part for part in (state, polymorph, meta_s) if part)


def observable_key(obs: Mapping[str, Any]) -> tuple[str, ...]:
    """Canonical typed-observable key.

    Null-hypothesis (P1-M1 / cx conflict identity): grouping by (type, regime)
    only mixed phase/standard-state/property into spurious conflicts (Fe mp+bp,
    Li2O gas vs solid, Mg multi-transition). Phase and standard-state branches
    are different observables per SCHEMA / VALUE-PRECEDENCE — not conflicts.
    Condensed-form state is a first-class axis: same phase text on glass vs
    crystal must not merge.
    """
    return (
        _norm_axis(obs.get("type")),
        _norm_axis(obs.get("regime")),
        _norm_axis(obs.get("phase")),
        _norm_axis(obs.get("standard_state")),
        _property_identity(obs),
        _norm_axis(obs.get("units")),
        _condensed_form_identity(obs),
    )


def _first_numeric(mapping: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        if key not in mapping:
            continue
        val = mapping[key]
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and math.isfinite(float(val)):
            return float(val)
        # alpha_range [lo, hi] → geometric mid when both positive
        if key in {"alpha_range", "uncertainty_envelope"} and isinstance(val, (list, tuple)) and len(val) == 2:
            try:
                lo, hi = float(val[0]), float(val[1])
                if lo > 0 and hi > 0:
                    return math.sqrt(lo * hi)
            except (TypeError, ValueError):
                pass
    return None


def comparable_scalar(
    values: Any,
    otype: str | None,
) -> float | None:
    """One canonical same-semantics scalar for disagreement, or None.

    Null-hypothesis (P1 dex within-row): the prior implementation collected
    every numeric leaf (MW + ΔfH + P°) from a single CEA row and reported a
    multi-dex "disagreement". Singletons and mixed-unit bags must not produce
    a dex.
    """
    if not isinstance(values, Mapping):
        if isinstance(values, (int, float)) and not isinstance(values, bool):
            v = float(values)
            return v if math.isfinite(v) else None
        return None
    keys = _SCALAR_KEYS_BY_TYPE.get(str(otype or ""), ())
    # Prefer type-specific keys; never fall through to arbitrary first numeric
    # (that re-introduces cross-unit bag dex).
    preferred = _first_numeric(values, keys)
    if preferred is not None:
        return preferred
    # Nested alpha_form mid-range envelope
    form = values.get("alpha_form")
    if isinstance(form, Mapping):
        env = form.get("uncertainty_envelope")
        if isinstance(env, (list, tuple)) and len(env) == 2:
            try:
                lo, hi = float(env[0]), float(env[1])
                if lo > 0 and hi > 0:
                    return math.sqrt(lo * hi)
            except (TypeError, ValueError):
                pass
        a = form.get("A")
        if isinstance(a, (int, float)) and not isinstance(a, bool):
            return float(a)
    return None


def disagreement_dex(numeric_values: Sequence[float]) -> float | None:
    """Max log10 spread among positive values; None if <2 comparable positives.

    Null-hypothesis (P1 linear-span invent): non-positive spreads used to be
    reported as log10(|Δ|), which is dimensionally arbitrary. Only positive
    same-unit ratios define a dex.
    """
    positives = [v for v in numeric_values if v > 0 and math.isfinite(v)]
    if len(positives) < 2:
        return None
    logs = [math.log10(v) for v in positives]
    return max(logs) - min(logs)


def ranges_overlap(a: Any, b: Any) -> bool:
    """True if T ranges overlap, or either range is absent (treat as open)."""
    def _pair(r: Any) -> tuple[float, float] | None:
        if not (isinstance(r, (list, tuple)) and len(r) == 2):
            return None
        try:
            return (float(r[0]), float(r[1]))
        except (TypeError, ValueError):
            return None

    pa, pb = _pair(a), _pair(b)
    if pa is None or pb is None:
        return True
    return pa[0] <= pb[1] and pb[0] <= pa[1]


def _payload_present(obs: Mapping[str, Any]) -> bool:
    return _has_payload_leaf(obs.get("values"))


def _is_pointer_or_pending_row(obs: Mapping[str, Any], source_id: str) -> bool:
    """True for acquisition stubs / pointer rows that must not count as found.

    Null-hypothesis (KM-M2): structured pointer payloads (quantity label +
    planned T_range_K + phase) pass ``_payload_present`` and overstated
    acquisition in the coverage table. Pending stubs and explicit pointer
    semantics carry no measured datum.
    """
    sid = str(source_id or "")
    if sid.startswith("pending-") or sid.startswith("pending_"):
        return True

    semantics_bits: list[str] = []
    sem = obs.get("semantics")
    if isinstance(sem, str) and sem.strip():
        semantics_bits.append(sem)
    values = obs.get("values")
    if isinstance(values, Mapping):
        vsem = values.get("semantics")
        if isinstance(vsem, str) and vsem.strip():
            semantics_bits.append(vsem)
    for bit in semantics_bits:
        low = bit.strip().lower()
        if (
            low.startswith("pointer")
            or "pointer_to_" in low
            or low.startswith("pending")
            or "acquisition_pending" in low
        ):
            return True

    locator = obs.get("locator")
    if isinstance(locator, Mapping):
        rec = str(locator.get("record") or "").strip()
        if rec.upper().startswith("PENDING"):
            return True
    return False


def _observation_counts_as_found(obs: Mapping[str, Any], source_id: str) -> bool:
    """Coverage 'found' requires a usable measured payload, not a pointer stub."""
    if not isinstance(obs, Mapping):
        return False
    if not _payload_present(obs):
        return False
    if _is_pointer_or_pending_row(obs, source_id):
        return False
    return True


def build_by_species(
    extracts: Sequence[Mapping[str, Any]],
    *,
    source_priority: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Assemble per-species competing observations with disagreement_dex.

    Uncertainty is first-class: each observation's stated ``uncertainty`` is
    retained verbatim on the by-species row so catalog fits inherit an error
    budget input.
    """
    if source_priority is None:
        priority = load_source_priority()
    else:
        priority = {str(k): list(v) for k, v in source_priority.items()}
    by_species: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for doc in extracts:
        source_id = str(doc.get("source_id", ""))
        review = doc.get("review_status")
        species_map = doc.get("species") or {}
        if not isinstance(species_map, Mapping):
            continue
        for sid, block in species_map.items():
            if not isinstance(block, Mapping):
                continue
            for obs in block.get("observations") or []:
                if not isinstance(obs, Mapping):
                    continue
                # Deep-copy uncertainty so derived views cannot alias extract
                unc = obs.get("uncertainty")
                if isinstance(unc, Mapping):
                    unc_out: Any = dict(unc)
                elif isinstance(unc, list):
                    unc_out = list(unc)
                else:
                    unc_out = unc
                # Deep-copy condensed_form so merge views cannot alias extracts.
                raw_form = obs.get("condensed_form")
                if isinstance(raw_form, Mapping):
                    form_out: Any = dict(raw_form)
                else:
                    form_out = raw_form
                entry = {
                    "source_id": source_id,
                    "review_status": review,
                    "observation_id": obs.get("observation_id"),
                    "type": obs.get("type"),
                    "regime": obs.get("regime"),
                    "phase": obs.get("phase"),
                    "standard_state": obs.get("standard_state"),
                    "T_range_K": obs.get("T_range_K"),
                    "units": obs.get("units"),
                    # First-class uncertainty propagation (owner 2026-08-02).
                    "uncertainty": unc_out,
                    "locator": obs.get("locator"),
                    "values": obs.get("values"),
                    "equipment": obs.get("equipment"),
                    # Condensed-form axis (state-at-measurement); pin-bearing
                    # residual path requires form-match or form-corrected.
                    "condensed_form": form_out,
                    "_payload_present": _payload_present(obs),
                }
                # t-511: cell_material → effective-pO₂ boundary (Mo/W reducing
                # vs Ir/alumina neutral). Annotation only — never mutates the
                # extract store; oxide/KEMS consumers read this at merge time.
                po2_ann = effective_po2_boundary_for_observation(obs)
                if po2_ann is not None:
                    entry["effective_po2_boundary"] = po2_ann
                by_species[str(sid)].append(entry)

    species_out: dict[str, Any] = {}
    for sid, rows in sorted(by_species.items()):
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[observable_key(row)].append(row)

        annotated: list[dict[str, Any]] = []
        group_meta: list[dict[str, Any]] = []
        for okey, group_rows in groups.items():
            otype = okey[0] or None
            # One scalar per observation; only include payload-bearing rows.
            per_obs_scalars: list[tuple[dict[str, Any], float]] = []
            for r in group_rows:
                if not r.get("_payload_present"):
                    continue
                sc = comparable_scalar(r.get("values"), otype)
                if sc is not None:
                    per_obs_scalars.append((r, sc))

            # Cross-source, overlapping-range only.
            # When multiple rows from the same source share a group, keep one
            # scalar per source (first) for dex — disagreement is cross-source.
            by_src: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
            for r, sc in per_obs_scalars:
                by_src[str(r.get("source_id"))].append((r, sc))

            # Pairwise overlap filter: include a source if its T range overlaps
            # at least one other source's range (or open ranges).
            source_reps: list[tuple[str, dict[str, Any], float]] = []
            for src, items in by_src.items():
                # Prefer the first observation as representative scalar
                r0, sc0 = items[0]
                source_reps.append((src, r0, sc0))

            comparable_sources: list[tuple[str, dict[str, Any], float]] = []
            for i, (src_i, r_i, sc_i) in enumerate(source_reps):
                overlaps_other = False
                for j, (src_j, r_j, _sc_j) in enumerate(source_reps):
                    if i == j:
                        continue
                    if ranges_overlap(r_i.get("T_range_K"), r_j.get("T_range_K")):
                        overlaps_other = True
                        break
                # Single-source groups: no cross-source disagreement.
                if len(source_reps) == 1:
                    continue
                if overlaps_other:
                    comparable_sources.append((src_i, r_i, sc_i))

            # If ≥2 sources but none pairwise-overlap, no dex.
            # If all pairs overlap (or open), use all source reps.
            if len(source_reps) >= 2 and len(comparable_sources) < 2:
                # Fall back: if every range is open/missing, treat as overlapping.
                if all(
                    not (
                        isinstance(r.get("T_range_K"), (list, tuple))
                        and len(r.get("T_range_K")) == 2
                    )
                    for _s, r, _sc in source_reps
                ):
                    comparable_sources = list(source_reps)
            nums = [sc for _s, _r, sc in comparable_sources]
            dex = disagreement_dex(nums) if len(nums) >= 2 else None

            fam_order = list(priority.get(str(otype), [])) if otype else []
            # Also try raw type string from first row
            if not fam_order and group_rows:
                fam_order = list(priority.get(str(group_rows[0].get("type")), []))

            def _rank(r: Mapping[str, Any]) -> tuple[int, str]:
                sid_ = str(r.get("source_id", ""))
                if sid_ in fam_order:
                    return (fam_order.index(sid_), sid_)
                # Unlisted sources sort after listed ones; they cannot win.
                return (len(fam_order) + 1000, sid_)

            ordered = sorted(group_rows, key=_rank)

            # Winner selection: fail-closed.
            # Null-hypothesis (P1 lexical fail-open): missing priority + unlisted
            # sources used to crown alphabetical first. Now:
            # - only payload-bearing observations can win
            # - winner must be explicitly listed in the family priority
            # - if no listed payload-bearing source exists → no winner
            winner: str | None = None
            for r in ordered:
                sid_ = str(r.get("source_id", ""))
                if not r.get("_payload_present"):
                    continue
                if fam_order and sid_ in fam_order:
                    winner = sid_
                    break
            # Explicit multi-listed same rank: first in priority order among
            # payload-bearing listed sources (deterministic).

            for r in ordered:
                r2 = {k: v for k, v in r.items() if not k.startswith("_")}
                r2["priority_rank"] = _rank(r)[0]
                r2["is_priority_winner"] = (
                    winner is not None
                    and r["source_id"] == winner
                    and bool(r.get("_payload_present"))
                )
                annotated.append(r2)

            # Propagate uncertainties for the group (error-budget input).
            unc_list = []
            for r in ordered:
                if r.get("uncertainty") is not None:
                    unc_list.append(
                        {
                            "source_id": r.get("source_id"),
                            "observation_id": r.get("observation_id"),
                            "uncertainty": r.get("uncertainty"),
                        }
                    )
            winner_unc = None
            if winner is not None:
                for r in ordered:
                    if r.get("source_id") == winner and r.get("uncertainty") is not None:
                        winner_unc = r.get("uncertainty")
                        break

            group_meta.append(
                {
                    "type": group_rows[0].get("type") if group_rows else okey[0],
                    "regime": group_rows[0].get("regime") if group_rows else None,
                    "phase": group_rows[0].get("phase") if group_rows else None,
                    "standard_state": group_rows[0].get("standard_state")
                    if group_rows
                    else None,
                    "property": okey[4] or None,
                    "units": group_rows[0].get("units") if group_rows else None,
                    "n_observations": len(group_rows),
                    "n_sources_comparable": len(comparable_sources),
                    "disagreement_dex": dex,
                    "priority_winner_source_id": winner,
                    "source_ids": [r["source_id"] for r in ordered],
                    # First-class uncertainty propagation into by-species view.
                    "winner_uncertainty": winner_unc,
                    "uncertainties": unc_list,
                }
            )

        species_out[sid] = {
            "observations": annotated,
            "observable_groups": group_meta,
        }

    return {
        "schema_version": "literature_extract_by_species.v1",
        "kind": "derived_by_species",
        "policy_ref": "docs-private/design/2026-07-30-vapour-rail-unification/VALUE-PRECEDENCE.md",
        "source_priority": priority,
        "species": species_out,
    }


def build_coverage(
    extracts: Sequence[Mapping[str, Any]],
    *,
    manifest_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Species × source coverage table (found/empty/absent), payload-aware.

    Cell states (per species × source):

    * **found** — at least one observation with a usable measured payload
    * **empty** — species is present on the source but every observation is
      missing, null-only, or a pending/pointer acquisition stub
    * **absent** — species is not listed under that source at all

    Null-hypothesis (P2-M2 / KM-M2): any non-empty observations list (or any
    structured pointer payload with quantity + T_range_K labels) counted as
    found, so pending stubs and emptied migrator rows overstated acquisition.
    """
    source_ids = [str(d.get("source_id", "")) for d in extracts]
    # species_id → sources with a usable measured payload
    found: dict[str, set[str]] = defaultdict(set)
    # species_id → sources that list the species but have no measured payload
    empty: dict[str, set[str]] = defaultdict(set)
    for doc in extracts:
        sid_src = str(doc.get("source_id", ""))
        species_map = doc.get("species") or {}
        if not isinstance(species_map, Mapping):
            continue
        for sid, block in species_map.items():
            if not isinstance(block, Mapping):
                continue
            obs_list = block.get("observations") or []
            if any(
                isinstance(o, Mapping) and _observation_counts_as_found(o, sid_src)
                for o in obs_list
            ):
                found[str(sid)].add(sid_src)
            else:
                # Species key present (even with empty observations list) → empty,
                # distinct from absent (species not listed on this source).
                empty[str(sid)].add(sid_src)

    if manifest_ids is None:
        manifest_ids = load_u0_species_ids()
    species_ids = list(manifest_ids) if manifest_ids else sorted(
        set(found.keys()) | set(empty.keys())
    )
    for sid in list(found.keys()) + list(empty.keys()):
        if sid not in species_ids:
            species_ids.append(sid)

    rows: list[dict[str, Any]] = []
    n_found = 0
    n_empty = 0
    n_absent = 0
    for sid in species_ids:
        cell: dict[str, str] = {}
        for src in source_ids:
            if src in found.get(sid, set()):
                cell[src] = "found"
                n_found += 1
            elif src in empty.get(sid, set()):
                cell[src] = "empty"
                n_empty += 1
            else:
                cell[src] = "absent"
                n_absent += 1
        rows.append({"species_id": sid, "sources": cell})

    return {
        "schema_version": "literature_extract_coverage.v1",
        "kind": "species_x_source_coverage",
        "source_ids": source_ids,
        "species_count": len(species_ids),
        "source_count": len(source_ids),
        "cells_found": n_found,
        "cells_empty": n_empty,
        "cells_absent": n_absent,
        "rows": rows,
    }


def build_consistency_report(
    extracts: Sequence[Mapping[str, Any]],
    *,
    source_priority: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Cross-source consistency report (auto-computed disagreement_dex).

    Owner-ratified 2026-08-02: no hand curation. Every multi-source
    same-observable overlapping-range group gets a computed dex.
    """
    view = build_by_species(extracts, source_priority=source_priority)
    conflicts: list[dict[str, Any]] = []
    n_groups = 0
    n_with_dex = 0
    for sid, block in (view.get("species") or {}).items():
        for g in block.get("observable_groups") or []:
            n_groups += 1
            n_src = len(set(g.get("source_ids") or []))
            dex = g.get("disagreement_dex")
            if n_src < 2:
                continue
            entry = {
                "species_id": sid,
                "type": g.get("type"),
                "regime": g.get("regime"),
                "phase": g.get("phase"),
                "standard_state": g.get("standard_state"),
                "property": g.get("property"),
                "units": g.get("units"),
                "source_ids": g.get("source_ids"),
                "n_observations": g.get("n_observations"),
                "n_sources_comparable": g.get("n_sources_comparable"),
                "disagreement_dex": dex,
                "priority_winner_source_id": g.get("priority_winner_source_id"),
                "winner_uncertainty": g.get("winner_uncertainty"),
                "uncertainties": g.get("uncertainties"),
            }
            conflicts.append(entry)
            if dex is not None:
                n_with_dex += 1

    # Sort highest disagreement first for review triage.
    conflicts.sort(
        key=lambda c: (
            -(c["disagreement_dex"] if c["disagreement_dex"] is not None else -1.0),
            str(c["species_id"]),
            str(c.get("type")),
        )
    )
    return {
        "schema_version": "literature_extract_consistency.v1",
        "kind": "cross_source_consistency",
        "policy_ref": "docs-private/design/2026-07-30-vapour-rail-unification/VALUE-PRECEDENCE.md",
        "n_observable_groups": n_groups,
        "n_multi_source_groups": len(conflicts),
        "n_with_disagreement_dex": n_with_dex,
        "conflicts": conflicts,
    }


def _dump(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--by-species", action="store_true", help="Emit by-species view")
    parser.add_argument("--coverage", action="store_true", help="Emit coverage table")
    parser.add_argument(
        "--consistency",
        action="store_true",
        help="Emit cross-source consistency report (auto disagreement_dex)",
    )
    parser.add_argument("-o", "--output", type=Path, help="Single-output path (one mode)")
    parser.add_argument(
        "--outdir",
        type=Path,
        help="Directory for by_species.yaml / coverage.yaml / consistency.yaml",
    )
    parser.add_argument(
        "--extracts-dir",
        type=Path,
        default=EXTRACTS_DIR,
        help="Extracts directory (default: data/literature/extracts)",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Load extracts even if validator would refuse (debug only)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    modes = sum(bool(x) for x in (args.by_species, args.coverage, args.consistency))
    if modes == 0:
        parser.error("specify --by-species and/or --coverage and/or --consistency")
    if modes > 1 and args.output and not args.outdir:
        print("use --outdir when emitting multiple views", file=sys.stderr)
        return 2

    priority_path = args.extracts_dir / "_source_priority.yaml"
    if not args.skip_validate:
        if priority_path.is_file():
            pr_errs = validate_source_priority_file(priority_path)
            if pr_errs:
                print("\n".join(pr_errs), file=sys.stderr)
                return 1
        elif args.extracts_dir == EXTRACTS_DIR:
            print(f"missing source_priority: {priority_path}", file=sys.stderr)
            return 1

    extracts = load_extracts(
        args.extracts_dir, require_valid=not args.skip_validate
    )
    if not extracts:
        print("no extracts loaded", file=sys.stderr)
        return 1

    # Always load priority from the extracts dir being merged (P2 custom-dir fix).
    if priority_path.is_file():
        priority = load_source_priority(priority_path, fail_closed=not args.skip_validate)
    else:
        if args.skip_validate:
            priority = {}
        else:
            print(f"source_priority missing: {priority_path}", file=sys.stderr)
            return 1

    planned: list[tuple[str, Path, dict[str, Any]]] = []
    if args.by_species:
        doc = build_by_species(extracts, source_priority=priority)
        if args.outdir:
            out = args.outdir / "by_species.yaml"
        elif args.output:
            out = args.output
        else:
            out = Path("build/literature/by_species.yaml")
        planned.append(("by_species", out, doc))

    if args.coverage:
        doc = build_coverage(extracts)
        if args.outdir:
            out = args.outdir / "coverage.yaml"
        elif args.output and not args.by_species and not args.consistency:
            out = args.output
        else:
            out = Path("build/literature/coverage.yaml")
        planned.append(("coverage", out, doc))

    if args.consistency:
        doc = build_consistency_report(extracts, source_priority=priority)
        if args.outdir:
            out = args.outdir / "consistency.yaml"
        elif args.output and modes == 1:
            out = args.output
        else:
            out = Path("build/literature/consistency.yaml")
        planned.append(("consistency", out, doc))

    # Write only after all modes succeed (avoid partial derived writes).
    for _name, out, doc in planned:
        _dump(out, doc)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
