"""Source-only reproduction cards: frozen inventory, path review and verdicts.

Failure prevented: cherry-picked outcomes or an unreviewed setup earning PASS.
This owner does not resolve physics inputs, execute engines, or certify extracts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from simulator.battery.enums import MetricOperation, SourceRelation, ValueKind
from simulator.battery.identity import Identity
from simulator.battery.migrate import (
    load_migrated_benches,
    load_migrated_context,
    load_migrated_store,
    to_plain,
)
from simulator.battery.records import Experiment, Observation, Work
from simulator.battery.score import (
    _printed_uncertainty_band,
    _rms,
    _row_out_of_certified_band,
    match_status,
    metric_operation_for_identity,
    populate_numeric,
)


Document = Mapping[str, Any]
Store = tuple[dict[str, Work], dict[str, Experiment], dict[str, Observation]]

# The Phase A setup review contract, including explicitly absent determinants.
# Failure prevented: deleting a setup entry and its declared path together.
_SETUP_REVIEW_PATHS = (
    ("method",), ("sample", "initial_composition"), ("sample", "printed_composition"),
    ("sample", "mass_kg"), ("sample", "form"), ("sample", "container"),
    ("sample", "pretreatment"), ("sample", "surface_area_m2"), ("conditions",),
    ("thermal_schedule",), ("pressure_environment",), ("fO2_control",),
    ("apparatus",), ("bench_id",), ("equipment_context_id",), ("work_id",),
)


def compare_point(observation: Observation, candidate: Decimal | None) -> dict[str, Any]:
    """Compare a supplied scalar with the existing battery ticket policy.

    Candidates are comparison data only. No prediction or engine route is added.
    Native membership is the same owner's policy with confidence unstated.
    """
    from dataclasses import replace

    identity = observation.identity
    result: dict[str, Any] = {
        "observation_id": observation.observation_id,
        "status": "MISSING", "reason": None, "residual": None,
        "band": None, "native_band": None, "native_inside": None,
        "native_residual": None,
        "uncertainty": to_plain(observation.uncertainty),
        "locator": to_plain(observation.locator),
        "flags": [to_plain(notice) for notice in observation.notices],
    }
    if observation.value.kind is not ValueKind.POINT:
        result["reason"] = (
            "observed_mass_loss_interval_not_point"
            if observation.value.kind is ValueKind.INTERVAL
            else "observed_value_not_point"
        )
        return result
    if not isinstance(identity, Identity) or not identity.quantity.is_value:
        result["reason"] = "quantity_not_predicted"
        return result
    reference = observation.value.point
    if reference is None or not reference.is_finite():
        result["reason"] = "invalid_reference"
        return result
    quantity = identity.quantity.value
    resolution = _printed_uncertainty_band(
        quantity, observation.uncertainty, reference, source_observation=observation,
    )
    result["band"] = to_plain(resolution.band)
    result["band_refusal"] = to_plain(resolution.refusal)
    result["band_notice"] = to_plain(resolution.notice)
    native = observation.uncertainty
    if isinstance(native.verbatim, Mapping):
        native = replace(native, verbatim={**native.verbatim, "confidence_level": "not_stated"})
    native_resolution = _printed_uncertainty_band(
        quantity, native, reference, source_observation=observation,
    )
    result["native_band"] = to_plain(native_resolution.band)
    if candidate is None or not isinstance(candidate, Decimal) or not candidate.is_finite():
        result["reason"] = "unsupported" if candidate is None else "invalid_prediction"
        return result
    operation = metric_operation_for_identity(identity)
    numeric, refusal, detail = populate_numeric(
        quantity=quantity, candidate=candidate, reference=reference,
        source_relation=SourceRelation.INDEPENDENT,
        printed_band=resolution.band, printed_refusal=resolution.refusal,
        operation_override=operation, band_operation=operation,
    )
    if numeric is None:
        result["reason"] = str(refusal)
        result["detail"] = detail
        return result
    result.update(status=match_status(numeric).value.upper(),
                  residual=str(numeric.value), residual_unit=numeric.unit,
                  band=to_plain(numeric.decision_band), candidate=str(candidate),
                  reference=str(reference))
    absolute, _, _ = populate_numeric(
        quantity=quantity, candidate=candidate, reference=reference,
        source_relation=SourceRelation.INDEPENDENT,
        operation_override=MetricOperation.ABSOLUTE,
    )
    result["native_residual"] = None if absolute is None else str(absolute.value)
    result["native_unit"] = (
        observation.derivation.output_unit if observation.derivation is not None
        else identity.subtype.value if identity.subtype.is_value else numeric.unit
    )
    native_numeric, _, _ = populate_numeric(
        quantity=quantity, candidate=candidate, reference=reference,
        source_relation=SourceRelation.INDEPENDENT,
        printed_band=native_resolution.band, printed_refusal=native_resolution.refusal,
        operation_override=operation, band_operation=operation,
    )
    if native_numeric is not None and native_numeric.decision_band is not None:
        result["native_inside"] = match_status(native_numeric).value == "match"
    return result


def resolve_path(scope: Document, ref: Sequence[str | int]) -> Any:
    """Resolve only the finite source paths enumerated by the review record."""
    value: Any = scope
    for key in ref:
        if isinstance(value, Mapping):
            if key not in value:
                return None
            value = value[key]
        elif isinstance(value, (list, tuple)) and isinstance(key, int) and 0 <= key < len(value):
            value = value[key]
        else:
            return None
    return value


def load_review_scope(root: Path, manifest: Document, *, store: Store | None = None) -> dict[str, Any]:
    """Use the persisted store owner (including both observation directories)."""
    works, experiments, observations = store or load_migrated_store(
        root, sources=manifest["source_ids"],
    )
    selected = {eid for run in manifest["runs"] for eid in run["experiment_ids"]}
    selected_obs = {oid for run in manifest["runs"] for group in run["outcomes"]
                    for oid in group["observation_ids"]}
    return {
        "experiments": {eid: to_plain(experiments[eid]) for eid in selected if eid in experiments},
        "observations": {oid: to_plain(observations[oid]) for oid in selected_obs if oid in observations},
        "works": {manifest["work_id"]: to_plain(works.get(manifest["work_id"]))},
        "benches": {key: to_plain(value) for key, value in load_migrated_benches(root).items()
                    if any(experiments[eid].bench_id == key for eid in selected if eid in experiments)},
        "contexts": {key: value for key, value in load_migrated_context(root).items()
                     if any(experiments[eid].equipment_context_id == key for eid in selected if eid in experiments)},
        "extracts": {sid: yaml.safe_load((root / "data/literature/extracts" / f"{sid}.yaml").read_text())
                     for sid in manifest["source_ids"]},
    }


def _review_causes(run: Document, review: Document, scope: Document) -> list[dict[str, Any]]:
    causes: list[dict[str, Any]] = []
    if run != review["frozen_run"]:
        causes.append({"cause": "review_stale", "reason": "frozen_inventory_or_setup_changed"})
    entries = review["entries"]
    expected = {tuple(path) for path in review["consumed_paths"]}
    actual = [tuple(entry["ref"]) for entry in entries]
    if len(set(actual)) != len(actual) or set(actual) != expected or not actual:
        causes.append({"cause": "review_stale", "reason": "consumed_path_coverage"})
    required: set[tuple[str | int, ...]] = set()
    frozen = review["frozen_run"]
    for group in frozen["outcomes"]:
        for oid in group["observation_ids"]:
            required.update(("observations", oid, field.name) for field in fields(Observation))
    for eid in frozen["experiment_ids"]:
        required.update(("experiments", eid, *path) for path in _SETUP_REVIEW_PATHS)
        for field, owner in (("bench_id", "benches"), ("equipment_context_id", "contexts")):
            linked = resolve_path(scope, ["experiments", eid, field])
            if linked is not None:
                required.add((owner, linked))
    required.update(tuple(item["context_ref"]) for item in frozen["printed_inventory"]
                    if item.get("context_ref"))
    for path in sorted(required, key=str):
        if not any(path[:len(ref)] == ref for ref in actual):
            causes.append({"cause": "review_stale", "reason": "uncovered_consumed_path", "path": list(path)})
    for entry in entries:
        ref = entry["ref"]
        if resolve_path(scope, ref) != entry["resolved"]:
            causes.append({"cause": "review_stale", "path": ref})
        if not all(entry.get(key) for key in ("reviewer", "locator", "quote", "unit")):
            causes.append({"cause": "source_unattested", "path": ref})
        if ref[0] == "observations":
            located = resolve_path(scope, [ref[0], ref[1], "locator"])
            if isinstance(located, Mapping):
                keys = ("page", "pdf_page_index", "published_page", "table", "paragraph")
                if any(key in located and (entry.get("locator") or {}).get(key) != located[key]
                       for key in keys):
                    causes.append({"cause": "source_conflict", "reason": "locator_disagrees", "path": ref})
        status = entry["status"]
        if status == "reviewed_missing":
            causes.append({"cause": "missing_input", "path": ref, "evidence": entry["quote"]})
        elif status != "reviewed":
            causes.append({"cause": status if status in {"source_conflict", "assumed"}
                           else "source_unattested", "path": ref})
    return causes


def _rms_text(values: Sequence[Decimal]) -> str | None:
    value = _rms(values)
    return None if value is None else str(value)


def evaluate_card(
    manifest: Document,
    run: Document,
    review: Document,
    scope: Document,
    observations: Mapping[str, Observation],
    candidates: Mapping[str, Decimal | None] | None = None,
) -> dict[str, Any]:
    """Join source evidence and supplied comparisons without shrinking inventory.

    Failure prevented: missing bands, flags or members improving a denominator.
    The independently frozen review supplies required membership even if a
    candidate manifest has dropped a group or row.
    """
    candidates = candidates or {}
    frozen = review["frozen_run"]
    selected_ids = {oid for group in frozen["outcomes"] for oid in group["observation_ids"]}
    scope = {**scope, "observations": {oid: to_plain(observations[oid])
                                      for oid in selected_ids if oid in observations}}
    causes = _review_causes(run, review, scope)
    group_ids = [group["group_id"] for group in frozen["outcomes"]]
    inventory_ids = {item["group_id"] for item in frozen["printed_inventory"] if item.get("required", True)}
    if len(set(group_ids)) != len(group_ids) or set(group_ids) != inventory_ids:
        causes.append({"cause": "missing_input", "reason": "printed_inventory_membership"})
    for eid in frozen["experiment_ids"]:
        experiment = scope["experiments"].get(eid)
        if experiment is None or experiment.get("work_id") != manifest["work_id"]:
            causes.append({"cause": "missing_input", "reason": "experiment_fk", "experiment_id": eid})
    if not frozen["experiment_ids"] or frozen["setup_ref"] not in frozen["experiment_ids"]:
        causes.append({"cause": "missing_input", "reason": "setup_not_adopted"})
    if run.get("assumptions") or frozen.get("assumptions"):
        causes.append({"cause": "assumed", "reason": "necessary_setup_assumption"})
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in frozen["outcomes"]:
        group_causes: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for blocker in group["blockers"]:
            group_causes.append({"cause": blocker, "group_id": group["group_id"]})
        for oid in group["observation_ids"]:
            obs = observations.get(oid)
            if oid in seen or obs is None:
                group_causes.append({"cause": "missing_input", "reason": "observation_fk_or_duplicate",
                                     "observation_id": oid})
                continue
            seen.add(oid)
            if obs.experiment_id not in frozen["experiment_ids"] or obs.source_id not in manifest["source_ids"]:
                group_causes.append({"cause": "source_conflict", "reason": "observation_fk",
                                     "observation_id": oid})
            if obs.admission.status.value != "admitted" or not obs.evidence.class_.is_value:
                group_causes.append({"cause": "source_unattested", "observation_id": oid})
            row = compare_point(obs, candidates.get(oid))
            rows.append(row)
            if row["band"] is None:
                group_causes.append({"cause": "NO_BAND", "observation_id": oid})
            if row["status"] == "MISSING":
                group_causes.append({"cause": "unsupported", "reason": row["reason"], "observation_id": oid})
            elif row["status"] == "MISMATCH":
                group_causes.append({"cause": "mismatch", "observation_id": oid})
        required = group["required_coordinates"]
        if required < 1 or len(rows) != required:
            group_causes.append({"cause": "unsupported", "reason": "unadopted_printed_coordinates"})
        complete = len(rows) == required and required > 0 and all(
            row["residual"] is not None and row["band"] is not None for row in rows
        )
        band_values = [Decimal(row["band"]["value"]) for row in rows if row["band"]]
        rms_band = None
        if complete and all(width > 0 for width in band_values):
            rms_band = _rms_text([Decimal(row["residual"]) / Decimal(row["band"]["value"]) for row in rows])
        # Diagnostic native RMS is separate for each output unit; ppm/dex and
        # wt% coordinates must never be averaged together.
        units = {row.get("native_unit") for row in rows if row["native_residual"] is not None}
        native_rms = {unit: _rms_text([Decimal(row["native_residual"]) for row in rows
                                 if row.get("native_unit") == unit and row["native_residual"] is not None])
                      for unit in units}
        reproduced = complete and not causes and not group_causes and all(
            row["status"] == "MATCH" for row in rows
        )
        groups.append({
            "group_id": group["group_id"], "observation_ids": list(group["observation_ids"]),
            "n_required": required, "n_numeric": sum(row["residual"] is not None for row in rows),
            "n_banded": sum(row["band"] is not None for row in rows),
            "n_inside_band": sum(row["status"] == "MATCH" for row in rows),
            "n_no_band": sum(row["band"] is None for row in rows),
            "n_refused": required - sum(row["residual"] is not None for row in rows),
            "reproduced": reproduced, "rms_band": rms_band,
            "native_rms": native_rms, "independent": group["independent"],
            "causes": group_causes, "coordinates": rows,
        })
    all_causes = causes + [cause for group in groups for cause in group["causes"]]
    passed = bool(groups) and not all_causes and all(group["reproduced"] for group in groups)
    flagged = any(_row_out_of_certified_band(str(flag.get("kind", "")), flag)
                  for group in groups for row in group["coordinates"] for flag in row["flags"])
    independent = [group for group in groups if group["independent"]]
    full_rms = None
    if groups and all(group["rms_band"] is not None for group in groups) and independent:
        full_rms = _rms_text([Decimal(group["rms_band"]) for group in independent])
    return {
        "card_id": frozen["card_id"], "evidence_label": "development_validation",
        "verdict": ("PASS_WITH_FLAGS" if flagged else "PASS") if passed else "NOT_PASS", "causes": all_causes,
        "authoring_revision": manifest["authoring_revision"],
        "engine_policy": manifest["engine_policy"], "ordinary_row_conversions": 0,
        "n_required_groups": len(groups), "n_reproduced_groups": sum(group["reproduced"] for group in groups),
        "group_fraction": {"numerator": sum(group["reproduced"] for group in groups), "denominator": len(groups)},
        "rms_band": full_rms, "groups": groups,
        **{name: sum(group[name] for group in groups) for name in (
            "n_required", "n_numeric", "n_banded", "n_inside_band", "n_no_band", "n_refused")},
        "review_current": not any(cause["cause"] == "review_stale" for cause in causes),
    }


def group_gate(before: Document, after: Document) -> dict[str, Any]:
    """Report regressions in each frozen group even while a card is incomplete.

    Failure prevented: an unrelated blocker hiding a worsening banded group.
    This checker is report-only; it is not wired to the controller offer hook.
    """
    regressions: list[str] = []
    if (before["card_id"], before["authoring_revision"], before["engine_policy"]) != (
        after["card_id"], after["authoring_revision"], after["engine_policy"]
    ):
        regressions.append("incomparable_receipts")
    new = {group["group_id"]: group for group in after["groups"]}
    for old in before["groups"]:
        gid = old["group_id"]
        current = new.get(gid)
        if current is None or current["observation_ids"] != old["observation_ids"] or current["n_required"] != old["n_required"]:
            regressions.append(f"{gid}:membership")
            continue
        for name in ("n_numeric", "n_banded", "n_inside_band"):
            if current[name] < old[name]:
                regressions.append(f"{gid}:{name}")
        if old["reproduced"] and not current["reproduced"]:
            regressions.append(f"{gid}:lost_reproduction")
        old_rows = {row["observation_id"]: row for row in old["coordinates"]}
        new_rows = {row["observation_id"]: row for row in current["coordinates"]}
        for oid, row in old_rows.items():
            candidate = new_rows.get(oid)
            if candidate is None or candidate["band"] != row["band"]:
                regressions.append(f"{gid}:{oid}:band_or_availability")
                continue
            if row["residual"] is not None and candidate["residual"] is None:
                regressions.append(f"{gid}:{oid}:numeric_availability")
            if row["status"] == "MATCH" and candidate["status"] != "MATCH":
                regressions.append(f"{gid}:{oid}:outside_band")
            if row["flags"] != candidate["flags"]:
                regressions.append(f"{gid}:{oid}:flags_changed")
        if old["rms_band"] is not None and (
            current["rms_band"] is None or Decimal(current["rms_band"]) > Decimal(old["rms_band"])
        ):
            regressions.append(f"{gid}:rms_band")
    if before["verdict"] == "PASS" and after["verdict"] != "PASS":
        regressions.append("card:lost_pass")
    if before["rms_band"] is not None and (
        after["rms_band"] is None or Decimal(after["rms_band"]) > Decimal(before["rms_band"])
    ):
        regressions.append("card:rms_band")
    return {"mode": "report_only", "accepted": not regressions, "regressions": regressions}


def write_study(
    root: Path,
    manifest_path: Path,
    output_dir: Path | None = None,
    *,
    store: Store | None = None,
) -> dict[str, Any]:
    """Write disposable derived JSON; never regenerate the battery store."""
    manifest = yaml.safe_load(manifest_path.read_text())
    review = yaml.safe_load((manifest_path.parent / manifest["review_ref"]).read_text())
    store = store or load_migrated_store(root, sources=manifest["source_ids"])
    scope = load_review_scope(root, manifest, store=store)
    output_dir = output_dir or root / "data/battery/reproduction-cards"
    output_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    for run in manifest["runs"]:
        receipt = evaluate_card(manifest, run, review["runs"][run["card_id"]], scope, store[2])
        receipt["reviewed_revision"] = review["reviewed_revision"]
        receipt["source_asset"] = review["source_asset"]
        receipt["resolved_setup_refs"] = run["experiment_ids"]
        receipt["resolved_outcome_refs"] = [oid for group in run["outcomes"] for oid in group["observation_ids"]]
        receipt["source_store_identity"] = scope["works"][manifest["work_id"]]["source_files"]
        (output_dir / f'{run["card_id"]}.json').write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
        receipts.append({key: receipt[key] for key in ("card_id", "verdict", "group_fraction", "rms_band")})
    summary = {"study_id": manifest["study_id"], "n_cards": len(receipts),
               "n_pass": sum(card["verdict"] == "PASS" for card in receipts),
               "n_pass_with_flags": sum(card["verdict"] == "PASS_WITH_FLAGS" for card in receipts),
               "ordinary_row_conversions": 0, "group_gate_mode": "report_only", "cards": receipts}
    (output_dir / "reproduction-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def write_studies(
    root: Path,
    manifest_paths: Sequence[Path],
    output_dir: Path | None = None,
    *,
    store: Store | None = None,
) -> dict[str, Any]:
    """Keep all four studies in the summary instead of overwriting earlier cards."""
    output_dir = output_dir or root / "data/battery/reproduction-cards"
    studies = [write_study(root, path, output_dir, store=store) for path in manifest_paths]
    summary = {
        "n_cards": sum(study["n_cards"] for study in studies),
        "n_pass": sum(study["n_pass"] for study in studies),
        "n_pass_with_flags": sum(study["n_pass_with_flags"] for study in studies),
        "ordinary_row_conversions": 0, "group_gate_mode": "report_only",
        "studies": studies,
    }
    (output_dir / "reproduction-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
