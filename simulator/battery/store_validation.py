"""Validation of the persisted migrated battery store."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, NoticeKind
from simulator.battery.migrate import REPO_ROOT, load_migrated_store
from simulator.battery.score import derive_store_stamp
from simulator.battery.validate import ValidationIssue, validate_corpus

_ALLOWED_HARD_ISSUE_PATH_SUFFIXES = (
    ".derived_from",
    ".derivation",
    ".attribution",
)
# J02 restored derived ancestry requirements; quoted-attributed rows require
# attribution. All other hard-issue shapes remain forbidden.


def _issue_shape(issue: ValidationIssue) -> str:
    """Group hard issues by reason and their final field name."""

    return f"{issue.reason.value}:{issue.path.rsplit('.', 1)[-1]}"


def validate_migrated_store(root: Path | None = None) -> dict[str, object]:
    """Run the migrated-store corpus validation and return its content record."""

    root = root or REPO_ROOT
    report_path = root / "data" / "battery" / "migration-report.md"
    works_dir = root / "data" / "literature" / "works"
    obs_dir = root / "data" / "literature" / "observations-v2"
    extracts_v2 = root / "data" / "literature" / "extracts-v2"

    works, experiments, observations = load_migrated_store(root)
    assert works
    assert observations
    figure_only = observations[
        "1997jonesthermo::jones_1997_imcc_mgo_activity_comparison"
    ]
    assert figure_only.admission.status is AdmissionStatus.PENDING
    assert not any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED
        for notice in figure_only.notices
    )

    referenced_context_ids = set()
    for observation in observations.values():
        referenced_context_ids.update(
            parent
            for parent in observation.derived_from or ()
            if "::context::" in parent
        )
        if observation.derivation is not None:
            referenced_context_ids.update(
                parent
                for parent in observation.derivation.inputs
                if "::context::" in parent
            )
    referenced_context_ids.update(
        experiment.equipment_context_id
        for experiment in experiments.values()
        if experiment.equipment_context_id is not None
    )
    context_rows = {}
    for work_path in works_dir.glob("*.yaml"):
        source = work_path.read_text(encoding="utf-8")
        if not any(context_id in source for context_id in referenced_context_ids):
            continue
        payload = yaml.safe_load(source) or {}
        for row in payload.get("context") or ():
            context_id = str(row["context_id"])
            if context_id in referenced_context_ids:
                context_rows[context_id] = row

    report = validate_corpus(
        works,
        experiments,
        observations,
        residuals=None,
        context_rows=context_rows,
    )
    hard_issue_counts = Counter(_issue_shape(issue) for issue in report.hard_issues)
    forbidden_shape_count = sum(
        issue.reason.value != "conditional_field"
        or not issue.path.endswith(_ALLOWED_HARD_ISSUE_PATH_SUFFIXES)
        for issue in report.hard_issues
    )

    assert report_path.is_file()
    assert extracts_v2.is_dir() or obs_dir.is_dir()
    return {
        "store_stamp": derive_store_stamp(root),
        "hard_issue_counts_by_shape": dict(sorted(hard_issue_counts.items())),
        "forbidden_shape_count": forbidden_shape_count,
    }
