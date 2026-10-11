#!/usr/bin/env python3
"""Snapshot and compare one source's migrated battery lineage."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from simulator.battery.enums import QUANTITY_UNITS
from simulator.battery.migrate import Migrator, iter_extract_observations, load_yaml, to_plain
from simulator.battery.score import ScoreContext, comparison_candidates


def _source_extract(root: Path, source_id: str) -> Path:
    path = root / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"legacy extract for source {source_id!r} is missing: {path}"
        )
    return path


def snapshot(source_id: str, root: Path) -> dict[str, Any]:
    path = _source_extract(root, source_id)
    source_doc = load_yaml(path)
    migrator = Migrator(root)
    migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result
    observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
    ]
    if not observations:
        raise ValueError(f"source {source_id!r} produced zero observations")
    candidates = comparison_candidates(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations={o.observation_id: o for o in observations},
        )
    )
    rows = []
    for observation in sorted(observations, key=lambda row: row.observation_id):
        quantity = observation.identity.quantity
        quantity_value = quantity.value if quantity.is_value else None
        rows.append(
            {
                "observation_id": observation.observation_id,
                "quantity": to_plain(quantity),
                "value": to_plain(observation.value),
                "units": QUANTITY_UNITS.get(quantity_value) if quantity_value else None,
                "evidence_class": to_plain(observation.evidence.class_),
                "admission_status": observation.admission.status.value,
                "derived_from": to_plain(observation.derived_from),
                "derivation": to_plain(observation.derivation),
            }
        )
    issues = [
        {
            "path": issue.path,
            "reason": issue.reason.value,
            "detail": issue.detail,
        }
        for issue in (result.validation.hard_issues if result.validation else ())
    ]
    treatment_counts: Counter[str] = Counter()
    if isinstance(source_doc, Mapping):
        for _formula, source_observation in iter_extract_observations(source_doc):
            values = source_observation.get("values")
            treatment = source_observation.get("treatment")
            if treatment is None and isinstance(values, Mapping):
                treatment = values.get("treatment")
            if treatment is not None:
                treatment_counts[str(treatment)] += 1
    result_snapshot = {
        "source_id": source_id,
        "observations": rows,
        "comparison_candidate_ids": sorted(o.observation_id for o in candidates),
        "hard_issues": sorted(issues, key=_issue_key),
    }
    if treatment_counts:
        result_snapshot["treatment_counts"] = dict(sorted(treatment_counts.items()))
    return result_snapshot


def _issue_key(issue: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(issue.get("path", "")), str(issue.get("reason", "")), str(issue.get("detail", "")))


def _read_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError(f"invalid lineage snapshot: {path}")
    return payload


def _lineage_field(issue: Mapping[str, Any]) -> str | None:
    parts = str(issue.get("path", "")).replace("[", ".").replace("]", "").split(".")
    return next((field for field in ("derived_from", "derivation") if field in parts), None)


def _measurement_signature(row: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: row.get(key) for key in ("quantity", "value", "units")},
        sort_keys=True,
        separators=(",", ":"),
    )


def compare(before: Mapping[str, Any], after: Mapping[str, Any], *, allow_rekey: bool = False) -> tuple[bool, str]:
    failures: list[str] = []
    if not before["observations"] or not after["observations"]:
        failures.append("cannot compare snapshots with zero observations on either side")
    before_rows = {row["observation_id"]: row for row in before["observations"]}
    after_rows = {row["observation_id"]: row for row in after["observations"]}
    before_ids, after_ids = set(before_rows), set(after_rows)
    id_map = {observation_id: observation_id for observation_id in before_ids & after_ids}
    removed, added = before_ids - after_ids, after_ids - before_ids
    if removed or added:
        if allow_rekey:
            before_by_value: dict[str, list[str]] = {}
            after_by_value: dict[str, list[str]] = {}
            for observation_id in removed:
                before_by_value.setdefault(_measurement_signature(before_rows[observation_id]), []).append(observation_id)
            for observation_id in added:
                after_by_value.setdefault(_measurement_signature(after_rows[observation_id]), []).append(observation_id)
            if set(before_by_value) == set(after_by_value) and all(
                len(before_by_value[key]) == len(after_by_value[key]) for key in before_by_value
            ):
                for signature, old_ids in before_by_value.items():
                    for old_id, new_id in zip(sorted(old_ids), sorted(after_by_value[signature])):
                        id_map[old_id] = new_id
                        print(f"rekeyed: {old_id} -> {new_id}")
                removed = added = set()
            else:
                failures.append("rekeyed observations do not match one-to-one by quantity/value/units")
        if removed:
            failures.append(f"removed observations: {', '.join(sorted(removed))}")
        if added:
            failures.append(f"added observations: {', '.join(sorted(added))}")

    for old_id, new_id in id_map.items():
        if _measurement_signature(before_rows[old_id]) != _measurement_signature(after_rows[new_id]):
            failures.append(f"measurement changed: {old_id} -> {new_id}")

    mapped_before_candidates = {
        id_map.get(observation_id, observation_id)
        for observation_id in before.get("comparison_candidate_ids", [])
    }
    after_candidates = set(after.get("comparison_candidate_ids", []))
    if mapped_before_candidates != after_candidates:
        failures.append(
            "comparison candidates changed: "
            f"left={sorted(mapped_before_candidates - after_candidates)}, "
            f"entered={sorted(after_candidates - mapped_before_candidates)}"
        )

    before_issues = before.get("hard_issues", [])
    after_issues = after.get("hard_issues", [])
    nonlineage_before = Counter(_issue_key(issue) for issue in before_issues if _lineage_field(issue) is None)
    nonlineage_after = Counter(_issue_key(issue) for issue in after_issues if _lineage_field(issue) is None)
    if nonlineage_before != nonlineage_after:
        failures.append("non-lineage hard issues changed")

    evidence_changes = []
    lineage_changes = {"derived_from": [], "derivation": []}
    candidate_ids = after_candidates
    for old_id, new_id in id_map.items():
        for field in lineage_changes:
            if before_rows[old_id].get(field) != after_rows[new_id].get(field):
                lineage_changes[field].append((old_id, new_id))
        old_class = before_rows[old_id].get("evidence_class")
        new_class = after_rows[new_id].get("evidence_class")
        if old_class != new_class:
            evidence_changes.append((old_id, new_id, old_class, new_class))
            new_value = (new_class or {}).get("value") if isinstance(new_class, Mapping) else None
            if new_id in candidate_ids and new_value in {"quoted_literature", "quoted_attributed"}:
                failures.append(f"candidate {new_id} changed to {new_value}")
    print(f"evidence_class changes: {len(evidence_changes)}")
    for old_id, new_id, old_class, new_class in evidence_changes:
        print(f"  {old_id} -> {new_id}: {old_class} -> {new_class}")

    lineage_counts: dict[str, tuple[int, int]] = {}
    for field in ("derived_from", "derivation"):
        old_count = sum(_lineage_field(issue) == field for issue in before_issues)
        new_count = sum(_lineage_field(issue) == field for issue in after_issues)
        lineage_counts[field] = (old_count, new_count)
    print("lineage hard issues: " + ", ".join(f"{field} {old}->{new}" for field, (old, new) in lineage_counts.items()))
    for field, changes in lineage_changes.items():
        print(f"{field} changes: {len(changes)}")
        for old_id, new_id in changes:
            print(f"  {old_id} -> {new_id}")
    for label, payload in (("before", before), ("after", after)):
        counts = payload.get("treatment_counts")
        if counts:
            print(f"{label} per-treatment counts: {json.dumps(counts, sort_keys=True)}")
    if failures:
        print("FAIL: " + "; ".join(failures))
        return False, ""
    print(f"PASS: {len(before_rows)} observations; {len(after_candidates)} comparison candidates")
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--source", required=True)
    snapshot_parser.add_argument("--root", type=Path, required=True)
    snapshot_parser.add_argument("--out", type=Path, required=True)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--before", type=Path, required=True)
    compare_parser.add_argument("--after", type=Path, required=True)
    compare_parser.add_argument("--allow-rekey", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            payload = snapshot(args.source, args.root)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {len(payload['observations'])} observations to {args.out}")
            return 0
        ok, _ = compare(_read_snapshot(args.before), _read_snapshot(args.after), allow_rekey=args.allow_rekey)
        return 0 if ok else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"lineage_wave_check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
