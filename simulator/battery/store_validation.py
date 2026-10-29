"""Validation of the persisted migrated battery store."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.enums import AdmissionStatus, NoticeKind
from simulator.battery.migrate import (
    REPO_ROOT,
    iter_observation_store_paths,
    iter_work_store_paths,
    load_migrated_store,
)
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


def _migrated_store_content_sha256(root: Path | None = None) -> str:
    """Hash the relative paths and bytes loaded from the persisted store."""

    root = root or REPO_ROOT
    works_dir = root / "data" / "literature" / "works"
    literature_dir = root / "data" / "literature"
    paths = iter_work_store_paths(works_dir)
    for directory_name in ("extracts-v2", "observations-v2"):
        paths.extend(
            iter_observation_store_paths(literature_dir / directory_name)
        )
    paths.sort(key=lambda path: path.relative_to(root).as_posix())

    digest = hashlib.sha256()
    for path in paths:
        relative_path = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


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
    for work_path in iter_work_store_paths(works_dir):
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
        "store_content_sha256": _migrated_store_content_sha256(root),
        "hard_issue_counts_by_shape": dict(sorted(hard_issue_counts.items())),
        "forbidden_shape_count": forbidden_shape_count,
    }
