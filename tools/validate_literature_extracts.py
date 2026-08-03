#!/usr/bin/env python3
"""Validate literature extract YAML files (t-508 / literature_extract.v1).

Fail-loud rules (non-exhaustive; see data/literature/extracts/SCHEMA.md):

* ``schema_version`` must be ``literature_extract.v1``
* ``source_id`` must match the filename stem
* source citation + extraction method/date/worker + review_status required
* observation ``type`` in the closed set
* every observation has a locator with at least one location key
* equipment-metadata fields, if present, are objects with value+locator
* bare scalar equipment fields refused; null values refused
* empty / null-only ``values`` payloads refused (pointer rows must carry
  explicit structured content)
* observation_id unique within the file (cross-species)
* fidelity_samples required on extracts that carry any observation
  (ENFORCED_FOR_NEW — pre-policy pilot allowlist is exempt; see
  ``_fidelity_pre_policy_allowlist.yaml``)
* fidelity sample structure: path-based *or* structured line-item form
  (species, observable, T/index, value, locator)
* no silent acceptance of unknown top-level observation types
* absolute machine-local provenance_path refused

Usage::

  python tools/validate_literature_extracts.py
  python tools/validate_literature_extracts.py path/to/extract.yaml
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections.abc import MutableMapping, MutableSequence, MutableSet, Set as AbstractSet
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACTS_DIR = ROOT / "data" / "literature" / "extracts"
SCHEMA_VERSION = "literature_extract.v1"
U0_MANIFEST = ROOT / "data" / "vapour_rail_u0_manifest.yaml"
FIDELITY_POLICY_PATH = EXTRACTS_DIR / "_fidelity_pre_policy_allowlist.yaml"
FIDELITY_POLICY_SCHEMA = "literature_extract_fidelity_policy.v1"
FIDELITY_GRADUATION_LEDGER_PATH = EXTRACTS_DIR / "_fidelity_graduation_ledger.yaml"
FIDELITY_GRADUATION_LEDGER_SCHEMA = (
    "literature_extract_fidelity_graduation_ledger.v1"
)

OBSERVATION_TYPES = frozenset(
    {
        "psat_series",
        "gibbs_table",
        "activity_coefficient",
        "alpha",
        "rate_series",
        "transition_point",
    }
)

REVIEW_STATUSES = frozenset({"draft", "reviewed", "rejected"})

EQUIPMENT_FIELDS = frozenset(
    {
        "orifice_area",
        "clausing_factor",
        "sample_surface_area",
        "cell_material",
        "chamber_pressure",
        "multi_orifice_series",
    }
)

LOCATOR_KEYS = frozenset(
    {
        "page",
        "published_page",
        "pdf_page_index",
        "table",
        "figure",
        "paragraph",
        "section",
        "equation",
        "line_range",
        "note",
        "source_path",
        "record",
    }
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ABS_PATH_RE = re.compile(r"^(/|[A-Za-z]:\\|\\\\)")
# path segments: bare key or key[index_or_id]
_PATH_SEGMENT_RE = re.compile(r"^([^.\[]+)(?:\[([^\]]*)\])?$")


class ExtractValidationError(Exception):
    """One or more extract validation failures."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} extract validation error(s)")


def _is_extract_path(path: Path) -> bool:
    if path.suffix not in {".yaml", ".yml"}:
        return False
    if path.name.startswith("_"):
        return False
    if path.name.upper().startswith("SCHEMA"):
        return False
    return True


def discover_extracts(directory: Path | None = None) -> list[Path]:
    d = directory or EXTRACTS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and _is_extract_path(p))


# ---------------------------------------------------------------------------
# Fidelity policy (ENFORCED_FOR_NEW + shrink-only allowlist)
# ---------------------------------------------------------------------------


def load_fidelity_graduation_ledger(
    path: Path | None = None,
) -> tuple[set[str], list[str]]:
    """Load the canonical append-only graduation history."""
    p = path or FIDELITY_GRADUATION_LEDGER_PATH
    if not p.is_file():
        return set(), [f"{p}: missing canonical fidelity graduation ledger"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return set(), [f"{p}: failed to load YAML: {exc}"]
    if not isinstance(doc, Mapping):
        return set(), [f"{p}: root must be a mapping"]
    errors: list[str] = []
    if doc.get("schema_version") != FIDELITY_GRADUATION_LEDGER_SCHEMA:
        errors.append(
            f"{p}: schema_version must be {FIDELITY_GRADUATION_LEDGER_SCHEMA!r}, "
            f"got {doc.get('schema_version')!r}"
        )
    graduated = doc.get("graduated_pre_policy_source_ids")
    if not isinstance(graduated, list) or not all(
        isinstance(x, str) for x in graduated
    ):
        errors.append(
            f"{p}: graduated_pre_policy_source_ids must be a list of strings"
        )
        return set(), errors
    graduated_set = set(graduated)
    if len(graduated) != len(graduated_set):
        errors.append(f"{p}: graduated_pre_policy_source_ids contains duplicates")
    return graduated_set, errors


def load_committed_fidelity_graduation_history(
    path: Path | None = None,
) -> tuple[set[str], list[str]]:
    """Return every graduation id recorded anywhere in the ledger's Git history.

    Candidate policy and ledger files are mutable together, so current-state
    comparison cannot prove append-only behavior.  Git ancestry is the external
    prior state used by the normal validator/CI path.  The union makes an id
    irreversible even if a later commit deletes the whole ledger entry.
    """
    p = (path or FIDELITY_GRADUATION_LEDGER_PATH).resolve()
    try:
        relative = p.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return set(), [f"{p}: canonical graduation ledger must be inside repository"]
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if shallow.returncode != 0:
            detail = shallow.stderr.strip() or f"git rev-parse exited {shallow.returncode}"
            return set(), [f"{p}: cannot inspect canonical Git history: {detail}"]
        if shallow.stdout.strip() == "true":
            return set(), [
                f"{p}: cannot prove append-only graduation history from a shallow "
                f"clone; fetch full Git ancestry for validation"
            ]
        log = subprocess.run(
            ["git", "log", "--format=%H", "--", relative],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return set(), [f"{p}: cannot inspect canonical Git history: {exc}"]
    if log.returncode != 0:
        detail = log.stderr.strip() or f"git log exited {log.returncode}"
        return set(), [f"{p}: cannot inspect canonical Git history: {detail}"]

    historical: set[str] = set()
    errors: list[str] = []
    for commit in filter(None, (line.strip() for line in log.stdout.splitlines())):
        shown = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        # A deletion commit names the path but has no file at that tree. Older
        # versions still appear later in the log and retain the tombstones.
        if shown.returncode != 0:
            continue
        try:
            doc = yaml.safe_load(shown.stdout)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{p}@{commit}: failed to parse historical ledger: {exc}")
            continue
        ids = doc.get("graduated_pre_policy_source_ids") if isinstance(doc, Mapping) else None
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            errors.append(
                f"{p}@{commit}: historical graduated_pre_policy_source_ids "
                f"must be a list of strings"
            )
            continue
        historical.update(ids)
    return historical, errors


def load_fidelity_policy(
    path: Path | None = None,
    *,
    graduated_ledger_path: Path | None = None,
    prior_graduated_source_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Load the fidelity policy file.

    Returns ``(policy_doc, errors)``. On hard load failure, policy_doc is empty
    and errors is non-empty.

    Shrink-only + no re-activation: the closed set is partitioned into
    ``active_pre_policy_source_ids`` ∪ ``graduated_pre_policy_source_ids``
    (disjoint). Graduating moves an id from active → graduated; re-adding a
    graduated id to active is refused.
    """
    p = path or FIDELITY_POLICY_PATH
    ledger_path = graduated_ledger_path or p.with_name(
        FIDELITY_GRADUATION_LEDGER_PATH.name
    )
    if not p.is_file():
        return {}, [f"{p}: missing fidelity policy file (t-510 ENFORCED_FOR_NEW)"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {}, [f"{p}: failed to load YAML: {exc}"]
    errors: list[str] = []
    if not isinstance(doc, Mapping):
        return {}, [f"{p}: root must be a mapping"]
    if doc.get("schema_version") != FIDELITY_POLICY_SCHEMA:
        errors.append(
            f"{p}: schema_version must be {FIDELITY_POLICY_SCHEMA!r}, "
            f"got {doc.get('schema_version')!r}"
        )
    if doc.get("policy") != "ENFORCED_FOR_NEW":
        errors.append(
            f"{p}: policy must be 'ENFORCED_FOR_NEW', got {doc.get('policy')!r}"
        )
    closed = doc.get("closed_set_source_ids")
    active = doc.get("active_pre_policy_source_ids")
    graduated = doc.get("graduated_pre_policy_source_ids")
    if not isinstance(closed, list) or not all(isinstance(x, str) for x in closed):
        errors.append(f"{p}: closed_set_source_ids must be a list of strings")
        closed_set: set[str] = set()
    else:
        closed_set = set(closed)
        if len(closed) != len(closed_set):
            errors.append(f"{p}: closed_set_source_ids contains duplicates")
    if not isinstance(active, list) or not all(isinstance(x, str) for x in active):
        errors.append(f"{p}: active_pre_policy_source_ids must be a list of strings")
        active_set: set[str] = set()
    else:
        active_set = set(active)
        if len(active) != len(active_set):
            errors.append(f"{p}: active_pre_policy_source_ids contains duplicates")
    # graduated is optional in older snapshots only if empty-equivalent; require list.
    if graduated is None:
        graduated = []
        # Normalize so callers see a stable key (do not mutate caller's view
        # beyond the returned dict copy below).
    if not isinstance(graduated, list) or not all(isinstance(x, str) for x in graduated):
        errors.append(f"{p}: graduated_pre_policy_source_ids must be a list of strings")
        graduated_set: set[str] = set()
    else:
        graduated_set = set(graduated)
        if len(graduated) != len(graduated_set):
            errors.append(f"{p}: graduated_pre_policy_source_ids contains duplicates")
    ledger_set, ledger_errors = load_fidelity_graduation_ledger(ledger_path)
    errors.extend(ledger_errors)
    if prior_graduated_source_ids is not None:
        prior_graduated = set(prior_graduated_source_ids)
        history_errors: list[str] = []
    elif ledger_path.resolve() == FIDELITY_GRADUATION_LEDGER_PATH.resolve():
        prior_graduated, history_errors = load_committed_fidelity_graduation_history(
            ledger_path
        )
        errors.extend(history_errors)
    else:
        prior_graduated = set()
        history_errors = []
    # Shrink-only: active ⊆ closed. Additions beyond closed are refused here.
    extras = active_set - closed_set
    if extras:
        errors.append(
            f"{p}: active_pre_policy_source_ids not ⊆ closed_set_source_ids "
            f"(shrink-only; illegal additions: {sorted(extras)})"
        )
    grad_extras = graduated_set - closed_set
    if grad_extras:
        errors.append(
            f"{p}: graduated_pre_policy_source_ids not ⊆ closed_set_source_ids "
            f"(illegal ids: {sorted(grad_extras)})"
        )
    # No re-activation: graduated ids cannot return to active.
    reactivated = active_set & graduated_set
    if reactivated:
        errors.append(
            f"{p}: active_pre_policy_source_ids ∩ graduated_pre_policy_source_ids "
            f"non-empty (re-activation refused: {sorted(reactivated)})"
        )
    if not ledger_errors:
        missing_tombstones = ledger_set - graduated_set
        if missing_tombstones:
            errors.append(
                f"{p}: graduated_pre_policy_source_ids deleted entries from the "
                f"canonical graduation ledger (append-only tombstones missing: "
                f"{sorted(missing_tombstones)})"
            )
        unrecorded_graduations = graduated_set - ledger_set
        if unrecorded_graduations:
            errors.append(
                f"{p}: graduated_pre_policy_source_ids contains entries absent from "
                f"the canonical graduation ledger (append there in the same change: "
                f"{sorted(unrecorded_graduations)})"
            )
        ledger_reactivated = active_set & ledger_set
        if ledger_reactivated:
            errors.append(
                f"{p}: active_pre_policy_source_ids re-activates ids recorded in the "
                f"canonical graduation ledger: {sorted(ledger_reactivated)}"
            )
    if not history_errors:
        deleted_history = prior_graduated - ledger_set
        if deleted_history:
            errors.append(
                f"{ledger_path}: canonical graduation ledger deleted ids present "
                f"in prior Git history (append-only violation: "
                f"{sorted(deleted_history)})"
            )
        history_reactivated = active_set & prior_graduated
        if history_reactivated:
            errors.append(
                f"{p}: active_pre_policy_source_ids re-activates ids from prior "
                f"canonical graduation history: {sorted(history_reactivated)}"
            )
    # Partition: every closed id is either still exempt or graduated.
    # Prevents silent shrink-without-tombstone that would allow later re-add.
    if closed_set and not (extras or grad_extras or reactivated):
        missing = closed_set - active_set - graduated_set
        if missing:
            errors.append(
                f"{p}: closed-set ids missing from both active and graduated "
                f"(graduate explicitly when removing from active: {sorted(missing)})"
            )
        stray = (active_set | graduated_set) - closed_set
        if stray:
            # Already covered by ⊆ checks; keep for completeness if those skipped.
            pass
    out = dict(doc)
    if "graduated_pre_policy_source_ids" not in out:
        out["graduated_pre_policy_source_ids"] = list(graduated)
    return out, errors


def pre_policy_source_ids(policy: Mapping[str, Any] | None = None) -> set[str]:
    """Return the active pre-policy allowlist (empty if policy unavailable)."""
    if policy is None:
        policy, errs = load_fidelity_policy()
        if errs:
            return set()
    active = policy.get("active_pre_policy_source_ids") or []
    return {str(x) for x in active if isinstance(x, str)}


# ---------------------------------------------------------------------------
# Fidelity sample resolution + match (t-510 gate)
# ---------------------------------------------------------------------------


def _values_equal(
    expected: Any,
    actual: Any,
    *,
    rel_tol: float | None = None,
) -> bool:
    """Type-strict structural equality for fidelity pins.

    Default is exact equality (no float tolerance, no bool/int coercion).
    ``rel_tol`` is only applied when the sample explicitly requests it (and
    both sides are non-bool numbers).
    """
    if expected is None or actual is None:
        return expected is actual
    # bool is a subclass of int — never let False==0 / True==1 pass.
    if type(expected) is bool or type(actual) is bool:
        return type(expected) is bool and type(actual) is bool and expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if type(expected) is not type(actual):
            return False
        if rel_tol is not None:
            return math.isclose(
                float(expected), float(actual), rel_tol=float(rel_tol), abs_tol=0.0
            )
        return expected == actual
    if isinstance(expected, str) and isinstance(actual, str):
        return expected == actual
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        unmatched_actual_keys = list(actual.keys())
        for expected_key, expected_value in expected.items():
            match_index = next(
                (
                    i
                    for i, actual_key in enumerate(unmatched_actual_keys)
                    if type(expected_key) is type(actual_key)
                    and _values_equal(expected_key, actual_key)
                ),
                None,
            )
            if match_index is None:
                return False
            actual_key = unmatched_actual_keys.pop(match_index)
            if not _values_equal(
                expected_value, actual[actual_key], rel_tol=rel_tol
            ):
                return False
        return True
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        return all(
            _values_equal(a, b, rel_tol=rel_tol) for a, b in zip(expected, actual)
        )
    if isinstance(expected, AbstractSet) and isinstance(actual, AbstractSet):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            return False
        unmatched_actual = list(actual)
        for expected_item in expected:
            match_index = next(
                (
                    i
                    for i, actual_item in enumerate(unmatched_actual)
                    if _values_equal(expected_item, actual_item, rel_tol=rel_tol)
                ),
                None,
            )
            if match_index is None:
                return False
            unmatched_actual.pop(match_index)
        return True
    return type(expected) is type(actual) and expected == actual


def _path_is_observation_evidence(path: str) -> bool:
    """True when a path-based sample pins observation values or equipment.

    Required shape::

        species.<id>.observations[<obs_id|index>].(values|equipment)[....]
    """
    if not path or not isinstance(path, str):
        return False
    parts = path.split(".")
    if len(parts) < 4:
        return False
    if parts[0] != "species":
        return False
    obs_seg = _PATH_SEGMENT_RE.match(parts[2])
    if not obs_seg or obs_seg.group(1) != "observations" or obs_seg.group(2) is None:
        return False
    leaf_seg = _PATH_SEGMENT_RE.match(parts[3])
    if not leaf_seg:
        return False
    return leaf_seg.group(1) in ("values", "equipment")


def _sample_expected_value(sample: Mapping[str, Any]) -> Any:
    if "value" in sample and "draft_value" in sample:
        raise KeyError("sample must use exactly one of value or draft_value")
    if "value" in sample:
        return sample["value"]
    if "draft_value" in sample:
        return sample["draft_value"]
    raise KeyError("sample missing value/draft_value")


def _is_null_or_empty_pin(value: Any) -> bool:
    """True when a fidelity pin carries no observed content (null / empty-only)."""
    return not _has_payload_leaf(value)


def _mutable_container_ids(value: Any, seen: set[int] | None = None) -> set[int]:
    """Return identities of every reachable mutable mapping/sequence, cycle-safe."""
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return set()
    seen.add(value_id)
    identities: set[int] = set()
    if isinstance(value, Mapping):
        if isinstance(value, MutableMapping):
            identities.add(value_id)
        for child in value.values():
            identities.update(_mutable_container_ids(child, seen))
    elif isinstance(value, (list, tuple)):
        if isinstance(value, MutableSequence):
            identities.add(value_id)
        for child in value:
            identities.update(_mutable_container_ids(child, seen))
    elif isinstance(value, AbstractSet):
        if isinstance(value, MutableSet):
            identities.add(value_id)
        for child in value:
            identities.update(_mutable_container_ids(child, seen))
    return identities


def _shares_mutable_identity(expected: Any, actual: Any) -> bool:
    """True when pin and body graphs share a mutable object at any depth."""
    return bool(
        _mutable_container_ids(expected) & _mutable_container_ids(actual)
    )


_STRUCTURED_SAMPLE_SELECTOR_KEYS = frozenset(
    {"species", "observation_id", "observable", "field", "value_key", "index", "T_K", "T"}
)


def _sample_addressing_keys(sample: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return populated path and structured selector keys for one sample."""
    path_keys = [key for key in ("path", "field_path") if key in sample]
    structured_keys = [
        key
        for key in _STRUCTURED_SAMPLE_SELECTOR_KEYS
        if key in sample and sample[key] is not None
    ]
    return path_keys, structured_keys


def _sample_mode_errors(sample: Mapping[str, Any]) -> list[str]:
    """Return ambiguity errors for aliases/selectors with precedence semantics."""
    path_keys, structured_keys = _sample_addressing_keys(sample)
    errors: list[str] = []
    for key in (
        "path",
        "field_path",
        "locator",
        "source_locator",
        *sorted(_STRUCTURED_SAMPLE_SELECTOR_KEYS),
    ):
        if key not in sample:
            continue
        value = sample[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"selector {key} must be non-null and non-empty")
    if len(path_keys) > 1:
        errors.append("use exactly one path addressing key (path or field_path)")
    if path_keys and structured_keys:
        errors.append("do not mix path and structured addressing modes")
    for left, right in (
        ("field", "value_key"),
        ("T_K", "T"),
        ("value", "draft_value"),
        ("locator", "source_locator"),
    ):
        if left in sample and right in sample:
            errors.append(f"use exactly one of {left} or {right}")
    temperature_keys = [key for key in ("T_K", "T") if key in sample]
    if "index" in sample and temperature_keys:
        errors.append("use index or a temperature selector, not both")
    return errors


def _validated_rel_tol(sample: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """Validate explicit numeric tolerance without bool/NaN/inf/crash bypasses."""
    if "rel_tol" not in sample:
        return None, None
    rel_tol = sample["rel_tol"]
    if isinstance(rel_tol, bool) or not isinstance(rel_tol, (int, float)):
        return None, f"rel_tol must be a real number (not bool), got {rel_tol!r}"
    rel_tol_f = float(rel_tol)
    if not math.isfinite(rel_tol_f) or rel_tol_f < 0.0 or rel_tol_f >= 1.0:
        return None, (
            f"rel_tol must be finite and satisfy 0 <= rel_tol < 1, got {rel_tol!r}"
        )
    return rel_tol_f, None


def resolve_field_path(doc: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted path with optional ``[key]`` selectors into ``doc``.

    Examples::

        species.Fe.observations[fe_alpha_1].values.alpha
        species.Fe.observations[0].values
    """
    if not path or not isinstance(path, str):
        raise KeyError("empty path")
    cur: Any = doc
    for raw_seg in path.split("."):
        m = _PATH_SEGMENT_RE.match(raw_seg)
        if not m:
            raise KeyError(f"malformed path segment {raw_seg!r} in {path!r}")
        key, bracket = m.group(1), m.group(2)
        if not isinstance(cur, Mapping) or key not in cur:
            raise KeyError(f"missing key {key!r} while resolving {path!r}")
        cur = cur[key]
        if bracket is None:
            continue
        # Bracket: integer index into list, or observation_id match in list,
        # or key into mapping.
        if isinstance(cur, Mapping):
            if bracket not in cur:
                raise KeyError(
                    f"missing bracket key {bracket!r} while resolving {path!r}"
                )
            cur = cur[bracket]
            continue
        if isinstance(cur, list):
            # Prefer integer index when bracket is a bare integer.
            if re.fullmatch(r"-?\d+", bracket):
                idx = int(bracket)
                if idx < 0 or idx >= len(cur):
                    raise KeyError(
                        f"index {idx} out of range while resolving {path!r}"
                    )
                cur = cur[idx]
                continue
            # Match observation_id (or other id field) in a list of mappings.
            found = None
            for item in cur:
                if isinstance(item, Mapping) and str(item.get("observation_id")) == bracket:
                    found = item
                    break
            if found is None:
                raise KeyError(
                    f"no list item with observation_id={bracket!r} "
                    f"while resolving {path!r}"
                )
            cur = found
            continue
        raise KeyError(
            f"cannot apply bracket {bracket!r} to {type(cur).__name__} "
            f"while resolving {path!r}"
        )
    return cur


def _find_observation(
    doc: Mapping[str, Any],
    *,
    species: str,
    observation_id: str | None = None,
    observable: str | None = None,
) -> Mapping[str, Any]:
    """Locate one observation block under ``species``."""
    species_map = doc.get("species")
    if not isinstance(species_map, Mapping) or species not in species_map:
        raise KeyError(f"species {species!r} not in extract")
    block = species_map[species]
    if not isinstance(block, Mapping):
        raise KeyError(f"species {species!r} block is not a mapping")
    obs_list = block.get("observations") or []
    if not isinstance(obs_list, list):
        raise KeyError(f"species {species!r} observations is not a list")
    if observation_id:
        for obs in obs_list:
            if isinstance(obs, Mapping) and obs.get("observation_id") == observation_id:
                if observable and obs.get("type") != observable:
                    raise KeyError(
                        f"observation_id {observation_id!r} has type "
                        f"{obs.get('type')!r}, not requested observable {observable!r}"
                    )
                return obs
        raise KeyError(
            f"observation_id {observation_id!r} not found under species {species!r}"
        )
    if observable:
        matches = [
            o
            for o in obs_list
            if isinstance(o, Mapping) and o.get("type") == observable
        ]
        if not matches:
            raise KeyError(
                f"no observation with type={observable!r} under species {species!r}"
            )
        if len(matches) > 1:
            raise KeyError(
                f"ambiguous type={observable!r} under species {species!r} "
                f"({len(matches)} matches; set observation_id)"
            )
        return matches[0]
    raise KeyError("structured sample needs observation_id or observable")


def resolve_fidelity_sample(
    doc: Mapping[str, Any],
    sample: Mapping[str, Any],
) -> Any:
    """Resolve the extract value a fidelity sample claims to pin.

    Supports:

    * **path-based** (pilot): ``path`` / ``field_path`` → walk the extract
    * **structured** (preferred for OCR): ``species`` + ``observation_id``
      and/or ``observable``, optional ``field`` / ``value_key`` into
      ``values``, optional ``index`` into a series list, optional ``T_K``
      match against a series point's temperature key
    """
    mode_errors = _sample_mode_errors(sample)
    if mode_errors:
        raise KeyError("; ".join(mode_errors))
    path_keys, _structured_keys = _sample_addressing_keys(sample)
    path = sample.get(path_keys[0]) if path_keys else None
    if path:
        return resolve_field_path(doc, str(path))

    species = sample.get("species")
    if not species:
        raise KeyError(
            "fidelity sample needs path/field_path or structured species+observable"
        )
    obs = _find_observation(
        doc,
        species=str(species),
        observation_id=(
            str(sample["observation_id"]) if sample.get("observation_id") else None
        ),
        observable=str(sample["observable"]) if sample.get("observable") else None,
    )
    values = obs.get("values")
    field = sample.get("field") or sample.get("value_key")
    index = sample.get("index")
    t_k = sample.get("T_K") if "T_K" in sample else sample.get("T")

    cur: Any = values
    if index is not None:
        if not isinstance(cur, list):
            raise KeyError(
                f"index={index!r} requires values to be a list, "
                f"got {type(cur).__name__}"
            )
        idx = int(index)
        if idx < 0 or idx >= len(cur):
            raise KeyError(f"index {idx} out of range (len={len(cur)})")
        cur = cur[idx]
    elif t_k is not None:
        if not isinstance(cur, list):
            raise KeyError(
                f"T_K={t_k!r} match requires values to be a list of points"
            )
        target = float(t_k)
        found = None
        for pt in cur:
            if not isinstance(pt, Mapping):
                continue
            for tk in ("T_K", "T", "temperature_K", "temp_K"):
                if tk in pt:
                    try:
                        if math.isclose(float(pt[tk]), target, rel_tol=0.0, abs_tol=1e-6):
                            found = pt
                            break
                    except (TypeError, ValueError):
                        continue
            if found is not None:
                break
        if found is None:
            raise KeyError(f"no series point with T≈{target} under species {species!r}")
        cur = found

    if field:
        if not isinstance(cur, Mapping) or field not in cur:
            raise KeyError(f"field {field!r} not found in resolved values")
        return cur[field]
    return cur


def check_fidelity_sample_matches(
    doc: Mapping[str, Any],
    sample: Mapping[str, Any],
    *,
    label: str = "sample",
) -> list[str]:
    """Return error strings if sample expected value does not match the extract.

    Empty list ⇒ match. Used by the parameterized t-510 gate test and available
    for tranche review tooling.

    Rejects null/empty pins, shared sample/body object identity (YAML aliases),
    and type-coerced near-matches (bool/int, float isclose drift).
    """
    errors: list[str] = []
    if "value" not in sample and "draft_value" not in sample:
        return [f"{label}: sample missing value/draft_value"]
    try:
        expected = _sample_expected_value(sample)
    except KeyError as exc:
        return [f"{label}: invalid fidelity sample value selector: {exc}"]
    if _is_null_or_empty_pin(expected):
        return [
            f"{label}: fidelity sample value must be a non-null observed pin "
            f"(null/empty-only pins refused)"
        ]
    try:
        actual = resolve_fidelity_sample(doc, sample)
    except KeyError as exc:
        return [f"{label}: cannot resolve sample against extract: {exc}"]
    if _is_null_or_empty_pin(actual):
        return [
            f"{label}: resolved extract value must be non-null and non-empty "
            f"(content-free pins do not satisfy the fidelity gate)"
        ]
    # Mutable containers that share identity at any depth co-mutate under edits.
    if _shares_mutable_identity(expected, actual):
        return [
            f"{label}: fidelity sample value shares mutable object identity with "
            f"extract body at some nesting depth (YAML alias); rewrite pin as an "
            f"independent literal"
        ]
    rel_tol_f, rel_tol_error = _validated_rel_tol(sample)
    if rel_tol_error:
        return [f"{label}: {rel_tol_error}"]
    if not _values_equal(expected, actual, rel_tol=rel_tol_f):
        errors.append(
            f"{label}: fidelity sample value mismatch: "
            f"sample={expected!r} extract={actual!r}"
        )
    return errors


def check_all_fidelity_samples_match(
    doc: Mapping[str, Any],
    *,
    label: str = "<document>",
) -> list[str]:
    """Assert every fidelity sample still matches extract content."""
    samples = doc.get("fidelity_samples")
    if not samples:
        return []
    if not isinstance(samples, list):
        return [f"{label}: fidelity_samples must be a list"]
    errors: list[str] = []
    for i, sample in enumerate(samples):
        sp = f"{label}:fidelity_samples[{i}]"
        if not isinstance(sample, Mapping):
            errors.append(f"{sp}: each sample must be a mapping")
            continue
        errors.extend(check_fidelity_sample_matches(doc, sample, label=sp))
    return errors


def load_known_species_ids() -> set[str] | None:
    """Return known U0/manifest ids, or None if the manifest is unavailable."""
    if not U0_MANIFEST.is_file():
        return None
    try:
        doc = yaml.safe_load(U0_MANIFEST.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(doc, Mapping):
        return None
    species = doc.get("species")
    ids: set[str] = set()
    if isinstance(species, list):
        for row in species:
            if isinstance(row, Mapping) and row.get("id"):
                ids.add(str(row["id"]))
    elif isinstance(species, Mapping):
        ids.update(str(k) for k in species.keys())
    return ids or None


def _locator_ok(locator: Any) -> bool:
    """True when locator is a mapping with ≥1 known key carrying a non-empty value.

    Extension keys are allowed but do not alone satisfy the rule — at least one
    key from LOCATOR_KEYS must be present and non-empty (SCHEMA.md contract).
    """
    if not isinstance(locator, Mapping):
        return False
    for key, val in locator.items():
        if key not in LOCATOR_KEYS:
            continue
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return True
    return False


def _has_payload_leaf(values: Any) -> bool:
    """True if values contains at least one non-null, non-empty leaf."""
    if values is None:
        return False
    if isinstance(values, bool):
        return True
    if isinstance(values, (int, float)):
        return True
    if isinstance(values, str):
        return bool(values.strip())
    if isinstance(values, (bytes, bytearray, memoryview)):
        return bool(bytes(values).strip())
    if isinstance(values, Mapping):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values.values())
    if isinstance(values, (list, tuple)):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    if isinstance(values, AbstractSet):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    # Unknown scalar types are accepted only when their own truth-value says
    # they carry content; falsey custom values fail closed.
    return bool(values)


def _check_equipment(
    equipment: Any,
    path: str,
    errors: list[str],
) -> None:
    if equipment is None:
        return
    if not isinstance(equipment, Mapping):
        errors.append(f"{path}: equipment must be a mapping, got {type(equipment).__name__}")
        return
    for field, payload in equipment.items():
        fpath = f"{path}.equipment.{field}"
        if field not in EQUIPMENT_FIELDS:
            errors.append(
                f"{fpath}: unknown equipment field (allowed: {sorted(EQUIPMENT_FIELDS)})"
            )
            continue
        if not isinstance(payload, Mapping):
            errors.append(
                f"{fpath}: equipment field must be an object with value+locator "
                f"(bare {type(payload).__name__} refused; owner rule: no unlocatored equipment)"
            )
            continue
        if "value" not in payload:
            errors.append(
                f"{fpath}: missing value (equipment field must carry value+locator)"
            )
        elif payload.get("value") is None:
            errors.append(f"{fpath}: value must not be null")
        if "locator" not in payload:
            errors.append(
                f"{fpath}: missing locator (validator refuses equipment fields without locators)"
            )
        elif not _locator_ok(payload["locator"]):
            errors.append(
                f"{fpath}: locator must be a mapping with at least one of "
                f"{sorted(LOCATOR_KEYS)}"
            )
        # Truthy inferred (not just identity True) requires a derivation note.
        if payload.get("inferred") and not (
            payload.get("inference") or payload.get("note")
        ):
            errors.append(
                f"{fpath}: inferred geometry requires 'inference' or 'note' stating the derivation"
            )


def _check_observation(
    obs: Any,
    path: str,
    errors: list[str],
    seen_ids: set[str],
) -> None:
    if not isinstance(obs, Mapping):
        errors.append(f"{path}: observation must be a mapping")
        return
    oid = obs.get("observation_id")
    if not oid or not isinstance(oid, str):
        errors.append(f"{path}: observation_id is required (non-empty string)")
    else:
        if oid in seen_ids:
            errors.append(f"{path}: duplicate observation_id '{oid}' within extract")
        seen_ids.add(oid)
        path = f"{path}[{oid}]"

    otype = obs.get("type")
    if otype not in OBSERVATION_TYPES:
        errors.append(
            f"{path}: type must be one of {sorted(OBSERVATION_TYPES)}, got {otype!r}"
        )

    locator = obs.get("locator")
    if locator is None:
        errors.append(f"{path}: locator is required on every observation")
    elif not _locator_ok(locator):
        errors.append(
            f"{path}: locator must be a mapping with at least one of {sorted(LOCATOR_KEYS)}"
        )

    # Refuse recognized equipment field names parked outside equipment: {}
    for eqf in EQUIPMENT_FIELDS:
        if eqf in obs:
            errors.append(
                f"{path}: equipment field {eqf!r} must live under observation.equipment "
                f"(misplaced top-level key refused)"
            )

    if "values" not in obs or obs["values"] is None:
        errors.append(
            f"{path}: values is required (empty/null-only payloads refused; "
            f"pointer rows must carry explicit structured content)"
        )
    else:
        if not isinstance(obs["values"], (Mapping, list)):
            errors.append(f"{path}: values must be a mapping or list when present")
        elif not _has_payload_leaf(obs["values"]):
            errors.append(
                f"{path}: values is empty or null-only (refuse empty evidence payloads)"
            )
        if not obs.get("units"):
            errors.append(f"{path}: units required when values are present")

    # Uncertainty is first-class: if present, must not be a bare null sink.
    unc = obs.get("uncertainty")
    if unc is not None:
        if isinstance(unc, Mapping) and not _has_payload_leaf(unc):
            # Allow {} only when no uncertainty was stated; prefer omission.
            # A mapping whose only leaves are null is a migrator artifact.
            if any(v is None for v in unc.values()) and not _has_payload_leaf(
                {k: v for k, v in unc.items() if v is not None}
            ):
                errors.append(
                    f"{path}: uncertainty mapping is null-only "
                    f"(omit the key if no uncertainty was stated, or retain verbatim text)"
                )
        elif isinstance(unc, str) and not unc.strip():
            errors.append(f"{path}: uncertainty string must be non-empty when present")

    tr = obs.get("T_range_K")
    if tr is not None:
        if not (isinstance(tr, (list, tuple)) and len(tr) == 2):
            errors.append(f"{path}: T_range_K must be [T_min, T_max]")
        else:
            try:
                t0, t1 = float(tr[0]), float(tr[1])
                if t0 > t1:
                    errors.append(f"{path}: T_range_K min > max")
            except (TypeError, ValueError):
                errors.append(f"{path}: T_range_K bounds must be numeric")

    _check_equipment(obs.get("equipment"), path, errors)


def _observation_count(doc: Mapping[str, Any]) -> int:
    species = doc.get("species") or {}
    n_obs = 0
    if isinstance(species, Mapping):
        for block in species.values():
            if isinstance(block, Mapping):
                obs = block.get("observations") or []
                if isinstance(obs, list):
                    n_obs += len(obs)
    return n_obs


def _check_fidelity_sample_shape(
    sample: Mapping[str, Any],
    sp: str,
    errors: list[str],
    *,
    doc: Mapping[str, Any] | None = None,
    enforce_observation_paths: bool = False,
) -> None:
    """Validate one fidelity sample's structure (path-based or structured).

    When ``enforce_observation_paths`` is True (non-allowlisted / new extracts),
    path-based samples must pin under ``species.*.observations[...].(values|equipment)``
    so metadata-only paths cannot satisfy the gate.
    """
    path_keys, structured_keys = _sample_addressing_keys(sample)
    path = sample.get(path_keys[0]) if len(path_keys) == 1 else None
    structured = bool(structured_keys)
    for mode_error in _sample_mode_errors(sample):
        errors.append(f"{sp}: {mode_error}")
    if not path and not structured:
        errors.append(
            f"{sp}: path/field_path OR structured fields "
            f"(species + observation_id/observable) required"
        )
    if structured:
        if not sample.get("species"):
            errors.append(f"{sp}: structured sample requires species")
        if not sample.get("observation_id") and not sample.get("observable"):
            errors.append(
                f"{sp}: structured sample requires observation_id and/or observable"
            )
    if path and enforce_observation_paths:
        if not _path_is_observation_evidence(str(path)):
            errors.append(
                f"{sp}: path-based fidelity sample on new extracts must pin "
                f"observation evidence "
                f"(species.*.observations[id].(values|equipment)...); "
                f"got path={path!r} (metadata-only paths refused)"
            )
    if "value" not in sample and "draft_value" not in sample:
        errors.append(f"{sp}: value (or draft_value) is required")
    else:
        try:
            expected = _sample_expected_value(sample)
        except KeyError as exc:
            errors.append(f"{sp}: invalid fidelity sample value selector: {exc}")
            expected = None
        if _is_null_or_empty_pin(expected):
            errors.append(
                f"{sp}: fidelity sample value must be a non-null observed pin "
                f"(null/empty-only pins refused)"
            )
        elif doc is not None:
            # Shared identity + null actual are shape defects (not just match).
            try:
                actual = resolve_fidelity_sample(doc, sample)
            except KeyError as exc:
                errors.append(f"{sp}: cannot resolve sample against extract: {exc}")
                actual = None
            else:
                if _shares_mutable_identity(expected, actual):
                    errors.append(
                        f"{sp}: fidelity sample value shares mutable object identity with "
                        f"extract body at some nesting depth (YAML alias); rewrite pin as "
                        f"an independent literal"
                    )
                if _is_null_or_empty_pin(actual) and not _is_null_or_empty_pin(expected):
                    errors.append(
                        f"{sp}: resolved extract value must be non-null and non-empty "
                        f"(content-free pins do not satisfy the fidelity gate)"
                    )
    _rel_tol, rel_tol_error = _validated_rel_tol(sample)
    if rel_tol_error:
        errors.append(f"{sp}: {rel_tol_error}")
    locator_keys = [key for key in ("locator", "source_locator") if key in sample]
    loc = sample.get(locator_keys[0]) if len(locator_keys) == 1 else None
    note = sample.get("note")
    # Structured (OCR) samples require a real locator; path-based pilot samples
    # may use note as a soft locator (DRAFT migration legacy).
    if locator_keys:
        if len(locator_keys) == 1 and not _locator_ok(loc):
            errors.append(
                f"{sp}: {locator_keys[0]} must be a mapping with at least one of "
                f"{sorted(LOCATOR_KEYS)}"
            )
    elif structured:
        errors.append(
            f"{sp}: locator/source_locator required on structured fidelity samples "
            f"(reviewer-verified against source/OCR artifact)"
        )
    elif not note:
        errors.append(
            f"{sp}: locator/source_locator or note required "
            f"(source-backed sample must be locatable)"
        )


def _check_fidelity_samples(
    doc: Mapping[str, Any],
    label: str,
    errors: list[str],
    *,
    require_samples: bool = True,
    check_match: bool = False,
) -> None:
    """Validate fidelity samples; optionally require presence + content match.

    ``require_samples`` is False for pre-policy (grandfathered) extracts under
    the ENFORCED_FOR_NEW policy. When samples *are* present they are still
    shape-checked (and optionally match-checked). New extracts
    (``require_samples=True``) must pin observation evidence paths.
    """
    n_obs = _observation_count(doc)
    samples = doc.get("fidelity_samples")

    if n_obs == 0:
        # No observations → samples optional; if present, still shape-check.
        if samples is None:
            return
    elif require_samples:
        if samples is None:
            errors.append(
                f"{label}: fidelity_samples required when observations are present "
                f"(t-510 ENFORCED_FOR_NEW: ≥1 reviewer-verified located sample; "
                f"pre-policy pilot extracts may be allowlisted)"
            )
            return
        if not isinstance(samples, list) or not samples:
            errors.append(f"{label}: fidelity_samples must be a non-empty list")
            return
    else:
        # Grandfathered: absence is OK.
        if samples is None:
            return
        if not isinstance(samples, list):
            errors.append(f"{label}: fidelity_samples must be a list when present")
            return
        if not samples:
            return

    if not isinstance(samples, list):
        errors.append(f"{label}: fidelity_samples must be a list")
        return

    for i, sample in enumerate(samples):
        sp = f"{label}:fidelity_samples[{i}]"
        if not isinstance(sample, Mapping):
            errors.append(f"{sp}: each sample must be a mapping")
            continue
        _check_fidelity_sample_shape(
            sample,
            sp,
            errors,
            doc=doc,
            enforce_observation_paths=require_samples,
        )
        if check_match:
            errors.extend(check_fidelity_sample_matches(doc, sample, label=sp))


def validate_extract_document(
    doc: Any,
    *,
    path: Path | None = None,
    expected_source_id: str | None = None,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
    pre_policy_ids: set[str] | None = None,
    check_fidelity_match: bool = False,
) -> list[str]:
    """Return a list of error strings (empty ⇒ valid).

    Warnings (unknown species ids) are appended to ``collect_warnings`` when
    provided; they do not fail validation (SCHEMA.md: draft may carry unknown ids).

    ``pre_policy_ids``: source_ids exempt from the fidelity_samples presence
    requirement (ENFORCED_FOR_NEW grandfathering). When None, the on-disk
    allowlist is not consulted and samples are required (unit-test default for
    non-allowlisted fixtures). Pass an empty set to require samples for all;
    pass the loaded allowlist from ``validate_all`` for corpus validation.
    """
    errors: list[str] = []
    label = str(path) if path else "<document>"

    if not isinstance(doc, Mapping):
        return [f"{label}: root must be a mapping"]

    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"{label}: schema_version must be {SCHEMA_VERSION!r}, "
            f"got {doc.get('schema_version')!r}"
        )

    source_id = doc.get("source_id")
    if not source_id or not isinstance(source_id, str):
        errors.append(f"{label}: source_id is required")
    elif expected_source_id is not None and source_id != expected_source_id:
        errors.append(
            f"{label}: source_id {source_id!r} does not match filename stem "
            f"{expected_source_id!r}"
        )

    source = doc.get("source")
    if not isinstance(source, Mapping):
        errors.append(f"{label}: source metadata mapping is required")
    else:
        if not source.get("citation"):
            errors.append(f"{label}: source.citation is required")

    extraction = doc.get("extraction")
    if not isinstance(extraction, Mapping):
        errors.append(f"{label}: extraction metadata mapping is required")
    else:
        for key in ("method", "date", "worker"):
            if not extraction.get(key):
                errors.append(f"{label}: extraction.{key} is required")
        date = extraction.get("date")
        if date is not None and isinstance(date, str) and not _ISO_DATE_RE.match(date):
            errors.append(
                f"{label}: extraction.date must be ISO YYYY-MM-DD, got {date!r}"
            )
        prov = extraction.get("provenance_path")
        if prov is not None and isinstance(prov, str) and _ABS_PATH_RE.match(prov):
            errors.append(
                f"{label}: extraction.provenance_path must be repository-relative "
                f"(absolute/machine-local path refused): {prov!r}"
            )

    review = doc.get("review_status")
    if review not in REVIEW_STATUSES:
        errors.append(
            f"{label}: review_status must be one of {sorted(REVIEW_STATUSES)}, "
            f"got {review!r}"
        )

    sp = doc.get("source_priority")
    if sp is not None and not isinstance(sp, Mapping):
        errors.append(f"{label}: source_priority must be a mapping when present")

    species = doc.get("species")
    if species is None:
        errors.append(f"{label}: species map is required (may be empty {{}})")
    elif not isinstance(species, Mapping):
        errors.append(f"{label}: species must be a mapping")
    else:
        # File-scope observation_id uniqueness (SCHEMA.md: unique within the file).
        seen: set[str] = set()
        for sid, block in species.items():
            spath = f"{label}:species.{sid}"
            if known_species_ids is not None and collect_warnings is not None:
                if str(sid) not in known_species_ids:
                    collect_warnings.append(
                        f"{spath}: unknown species id {sid!r} "
                        f"(not in vapour_rail_u0_manifest; allowed in draft)"
                    )
            if not isinstance(block, Mapping):
                errors.append(f"{spath}: species block must be a mapping")
                continue
            obs_list = block.get("observations")
            if obs_list is None:
                errors.append(f"{spath}: observations list is required")
                continue
            if not isinstance(obs_list, list):
                errors.append(f"{spath}: observations must be a list")
                continue
            for i, obs in enumerate(obs_list):
                _check_observation(obs, f"{spath}.observations[{i}]", errors, seen)

    # ENFORCED_FOR_NEW: require samples unless source_id is pre-policy allowlisted.
    is_pre_policy = False
    if pre_policy_ids is not None and isinstance(source_id, str):
        is_pre_policy = source_id in pre_policy_ids
    _check_fidelity_samples(
        doc,
        label,
        errors,
        require_samples=not is_pre_policy,
        check_match=check_fidelity_match,
    )

    return errors


def validate_extract_file(
    path: Path,
    *,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
    pre_policy_ids: set[str] | None = None,
    check_fidelity_match: bool = False,
) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — surface parse failures as validation errors
        return [f"{path}: failed to load YAML: {exc}"]
    return validate_extract_document(
        doc,
        path=path,
        expected_source_id=path.stem,
        known_species_ids=known_species_ids,
        collect_warnings=collect_warnings,
        pre_policy_ids=pre_policy_ids,
        check_fidelity_match=check_fidelity_match,
    )


def validate_source_priority_file(
    path: Path | None = None,
    *,
    known_source_ids: set[str] | None = None,
) -> list[str]:
    p = path or (EXTRACTS_DIR / "_source_priority.yaml")
    if not p.is_file():
        return [f"{p}: missing store-level source_priority file"]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"{p}: failed to load YAML: {exc}"]
    errors: list[str] = []
    if not isinstance(doc, Mapping):
        return [f"{p}: root must be a mapping"]
    if doc.get("schema_version") != "literature_extract_source_priority.v1":
        errors.append(f"{p}: unexpected schema_version {doc.get('schema_version')!r}")
    sp = doc.get("source_priority")
    if not isinstance(sp, Mapping) or not sp:
        errors.append(f"{p}: source_priority map is required")
        return errors
    for family in OBSERVATION_TYPES:
        if family not in sp:
            errors.append(
                f"{p}: source_priority missing required family {family!r} "
                f"(every observable family must have an explicit reviewed order)"
            )
    for family, order in sp.items():
        if family not in OBSERVATION_TYPES:
            errors.append(
                f"{p}: unknown observable family {family!r} "
                f"(allowed: {sorted(OBSERVATION_TYPES)})"
            )
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            errors.append(
                f"{p}: source_priority.{family} must be a list of source_id strings"
            )
        elif len(order) == 0:
            errors.append(
                f"{p}: source_priority.{family} must be non-empty "
                f"(empty family lists fail closed)"
            )
    return errors


def validate_all(
    paths: Iterable[Path] | None = None,
    *,
    check_priority: bool = True,
    check_fidelity_policy: bool = True,
    check_fidelity_match: bool = False,
    collect_warnings: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if check_priority:
        errors.extend(validate_source_priority_file())
    pre_policy: set[str] = set()
    if check_fidelity_policy:
        policy, policy_errs = load_fidelity_policy()
        errors.extend(policy_errs)
        pre_policy = pre_policy_source_ids(policy) if not policy_errs else set()
    files = list(paths) if paths is not None else discover_extracts()
    if not files and paths is None:
        errors.append(f"{EXTRACTS_DIR}: no extract YAML files found")
    known = load_known_species_ids()
    for path in files:
        errors.extend(
            validate_extract_file(
                path,
                known_species_ids=known,
                collect_warnings=collect_warnings,
                pre_policy_ids=pre_policy if check_fidelity_policy else None,
                check_fidelity_match=check_fidelity_match,
            )
        )
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Extract YAML paths (default: all under data/literature/extracts/)",
    )
    parser.add_argument(
        "--skip-priority",
        action="store_true",
        help="Do not validate _source_priority.yaml",
    )
    parser.add_argument(
        "--skip-fidelity-policy",
        action="store_true",
        help="Do not load/apply the ENFORCED_FOR_NEW fidelity allowlist",
    )
    parser.add_argument(
        "--check-fidelity-match",
        action="store_true",
        help="Also assert every fidelity sample still matches extract content",
    )
    parser.add_argument(
        "--show-warnings",
        action="store_true",
        help="Print unknown-species-id warnings (non-fatal)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = [p.resolve() for p in args.paths] if args.paths else None
    warnings: list[str] = []
    errors = validate_all(
        paths,
        check_priority=not args.skip_priority,
        check_fidelity_policy=not args.skip_fidelity_policy,
        check_fidelity_match=args.check_fidelity_match,
        collect_warnings=warnings if args.show_warnings else None,
    )
    if args.show_warnings and warnings:
        for w in warnings:
            print(f"WARN: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"FAIL: {len(errors)} error(s)", file=sys.stderr)
        return 1
    n = len(paths) if paths is not None else len(discover_extracts())
    print(f"OK: {n} extract file(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
