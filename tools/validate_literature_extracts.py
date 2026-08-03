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
* no silent acceptance of unknown top-level observation types
* absolute machine-local provenance_path refused

Usage::

  python tools/validate_literature_extracts.py
  python tools/validate_literature_extracts.py path/to/extract.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACTS_DIR = ROOT / "data" / "literature" / "extracts"
SCHEMA_VERSION = "literature_extract.v1"
U0_MANIFEST = ROOT / "data" / "vapour_rail_u0_manifest.yaml"

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
    if isinstance(values, Mapping):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values.values())
    if isinstance(values, (list, tuple)):
        if not values:
            return False
        return any(_has_payload_leaf(v) for v in values)
    return True


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


def _check_fidelity_samples(
    doc: Mapping[str, Any],
    label: str,
    errors: list[str],
) -> None:
    """Require ≥1 fidelity sample when the extract carries any observation."""
    species = doc.get("species") or {}
    n_obs = 0
    if isinstance(species, Mapping):
        for block in species.values():
            if isinstance(block, Mapping):
                obs = block.get("observations") or []
                if isinstance(obs, list):
                    n_obs += len(obs)
    if n_obs == 0:
        return
    samples = doc.get("fidelity_samples")
    if samples is None:
        errors.append(
            f"{label}: fidelity_samples required when observations are present "
            f"(t-510 / owner fidelity-gate: ≥1 located source-backed sample)"
        )
        return
    if not isinstance(samples, list) or not samples:
        errors.append(f"{label}: fidelity_samples must be a non-empty list")
        return
    for i, sample in enumerate(samples):
        sp = f"{label}:fidelity_samples[{i}]"
        if not isinstance(sample, Mapping):
            errors.append(f"{sp}: each sample must be a mapping")
            continue
        if not sample.get("path") and not sample.get("field_path"):
            errors.append(f"{sp}: path (or field_path) is required")
        if "value" not in sample and "draft_value" not in sample:
            errors.append(f"{sp}: value (or draft_value) is required")
        loc = sample.get("locator") or sample.get("source_locator")
        note = sample.get("note")
        if loc is None and not note:
            errors.append(
                f"{sp}: locator/source_locator or note required "
                f"(source-backed sample must be locatable)"
            )
        elif loc is not None and not _locator_ok(loc) and not note:
            errors.append(
                f"{sp}: locator must be a mapping with at least one of {sorted(LOCATOR_KEYS)}"
            )


def validate_extract_document(
    doc: Any,
    *,
    path: Path | None = None,
    expected_source_id: str | None = None,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
) -> list[str]:
    """Return a list of error strings (empty ⇒ valid).

    Warnings (unknown species ids) are appended to ``collect_warnings`` when
    provided; they do not fail validation (SCHEMA.md: draft may carry unknown ids).
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

    _check_fidelity_samples(doc, label, errors)

    return errors


def validate_extract_file(
    path: Path,
    *,
    known_species_ids: set[str] | None = None,
    collect_warnings: list[str] | None = None,
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
    collect_warnings: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if check_priority:
        errors.extend(validate_source_priority_file())
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
