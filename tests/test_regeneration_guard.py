"""Unit tests for the regenerate-and-replace shrinkage guard."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from simulator.regeneration_guard import (
    RegenerationGuardReport,
    RegenerationShrinkageError,
    RetiredArtifactWarning,
    assert_no_silent_artifact_loss,
    PlannedArtifactNotWrittenError,
    regeneration_guard,
    verify_planned_artifacts_written,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Independent CLIs. There is no single write() chokepoint they all funnel
# through; requiring one would be a package-wide I/O refactor. This inventory
# is detection: a new regen-named producer that is not classified here fails
# CI instead of shipping as a silent bypass.
GUARDED_REGENERATION_WRITERS = frozenset(
    {
        "benchmarks/melt_activity_benchmark.py",
        "tools/migrate_pilot_extracts.py",
    }
)
CLASSIFIED_UNGUARDED_WRITERS = {
    "scripts/regenerate_runner_goldens.py": (
        "multi-artifact goldens; narrowing SCENARIOS leaves orphans"
    ),
    "scripts/regenerate_cache_identity_goldens.py": (
        "golden regen; not wired through the guard"
    ),
    "scripts/regenerate_coating_diagnostic_golden.py": (
        "machine-sensitive golden; not wired through the guard"
    ),
    "scripts/generate_optimizer_recipe_vocabulary.py": (
        "single-artifact rewrite; name-set guard cannot see in-file shrink"
    ),
    "benchmarks/engine_throughput_bench.py": (
        "single-artifact --rebless-ratchet; name-set guard cannot see in-file shrink"
    ),
    "tools/vp_cea_ingest.py": (
        "single-artifact rewrite; name-set guard cannot see in-file shrink"
    ),
    "web/report_viewer/freeze_sample.py": (
        "single-artifact rewrite; name-set guard cannot see in-file shrink"
    ),
}
_REGEN_NAME_RE = re.compile(
    r"(^|[_\-/])(regenerate|regen|rebless)([_\-/]|$)|migrate_.*extract",
    re.IGNORECASE,
)
_SEARCH_ROOTS = ("tools", "scripts", "benchmarks", "web")


def _writer_calls_post_check(source: str) -> bool:
    """True if source uses the context manager or both pre- and post-checks."""
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    if "regeneration_guard" in called:
        return True
    return (
        "assert_no_silent_artifact_loss" in called
        and "verify_planned_artifacts_written" in called
    )


def _discover_regen_named_writers() -> list[str]:
    found: list[str] = []
    for root_name in _SEARCH_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if _REGEN_NAME_RE.search(path.name) or _REGEN_NAME_RE.search(relative):
                found.append(relative)
    return sorted(found)

MANAGED = ("alpha.csv", "beta.csv", "gamma.csv")


def _touch(directory, *names):
    for name in names:
        (directory / name).write_text("previous run\n", encoding="utf-8")


def test_refuses_when_new_set_drops_present_artifacts(tmp_path):
    _touch(tmp_path, "alpha.csv", "beta.csv", "gamma.csv")

    with pytest.raises(RegenerationShrinkageError) as excinfo:
        assert_no_silent_artifact_loss(
            tmp_path, ("alpha.csv",), managed=MANAGED
        )

    message = str(excinfo.value)
    assert "beta.csv, gamma.csv" in message
    assert "alpha.csv" not in message


def test_passes_when_planned_covers_everything_present(tmp_path):
    _touch(tmp_path, "alpha.csv", "beta.csv")

    report = assert_no_silent_artifact_loss(
        tmp_path, ("alpha.csv", "beta.csv", "gamma.csv"), managed=MANAGED
    )

    assert report == RegenerationGuardReport(
        present=frozenset({"alpha.csv", "beta.csv"}),
        planned=frozenset({"alpha.csv", "beta.csv", "gamma.csv"}),
        retired_removed=frozenset(),
    )


def test_empty_directory_has_nothing_to_drop(tmp_path):
    report = assert_no_silent_artifact_loss(tmp_path, (), managed=MANAGED)

    assert report.present == frozenset()


def test_retirement_opt_out_allows_removal_and_warns(tmp_path):
    _touch(tmp_path, "alpha.csv", "beta.csv")

    with pytest.warns(RetiredArtifactWarning, match="beta.csv"):
        report = assert_no_silent_artifact_loss(
            tmp_path, ("alpha.csv",), managed=MANAGED, retired=("beta.csv",)
        )

    assert report.retired_removed == frozenset({"beta.csv"})


def test_retiring_an_absent_artifact_is_a_noop(tmp_path, recwarn):
    _touch(tmp_path, "alpha.csv")

    report = assert_no_silent_artifact_loss(
        tmp_path, ("alpha.csv",), managed=MANAGED, retired=("beta.csv",)
    )

    assert report.retired_removed == frozenset()
    assert not [
        warning
        for warning in recwarn.list
        if issubclass(warning.category, RetiredArtifactWarning)
    ]


def test_files_outside_the_managed_set_are_ignored(tmp_path):
    _touch(tmp_path, "alpha.csv")
    (tmp_path / "operator-notes.txt").write_text("not the step's file\n")

    report = assert_no_silent_artifact_loss(tmp_path, ("alpha.csv",), managed=MANAGED)

    assert report.present == frozenset({"alpha.csv"})


def test_planned_or_retired_outside_managed_is_a_caller_bug(tmp_path):
    with pytest.raises(ValueError, match="outside the managed artifact set"):
        assert_no_silent_artifact_loss(
            tmp_path, ("delta.csv",), managed=MANAGED
        )
    with pytest.raises(ValueError, match="outside the managed artifact set"):
        assert_no_silent_artifact_loss(
            tmp_path, (), managed=MANAGED, retired=("delta.csv",)
        )


def test_planned_but_never_written_is_refused(tmp_path):
    """kimi cross-cut M4: the b-200 shape reproduced inside the b-200 fix.

    The pre-write check compares PRESENT against PLANNED, so a planned name
    passes it by construction. The caller then unlinks every planned name. If
    a conditional write's condition comes out false the artifact never returns,
    the output set has shrunk, and the run reports success -- exactly what the
    guard exists to prevent. Red-by-revert against the pre-write-only guard.
    """
    (tmp_path / "kept.csv").write_text("a\n")
    (tmp_path / "vanishes.csv").write_text("b\n")
    managed = {"kept.csv", "vanishes.csv"}

    report = assert_no_silent_artifact_loss(
        tmp_path, {"kept.csv", "vanishes.csv"}, managed=managed
    )
    # The caller's pre-unlink, then a run that writes only one of the two.
    for name in managed:
        (tmp_path / name).unlink()
    (tmp_path / "kept.csv").write_text("a\n")

    with pytest.raises(PlannedArtifactNotWrittenError) as excinfo:
        verify_planned_artifacts_written(tmp_path, report)
    assert "vanishes.csv" in str(excinfo.value)


def test_zero_byte_planned_artifact_counts_as_not_written(tmp_path):
    """A zero-byte file is silent loss wearing a filename."""
    (tmp_path / "rows.csv").write_text("data\n")
    report = assert_no_silent_artifact_loss(
        tmp_path, {"rows.csv"}, managed={"rows.csv"}
    )
    (tmp_path / "rows.csv").write_text("")
    with pytest.raises(PlannedArtifactNotWrittenError):
        verify_planned_artifacts_written(tmp_path, report)


def test_context_manager_runs_both_checks(tmp_path):
    """The context manager is the forgettable-step-proof form."""
    (tmp_path / "a.csv").write_text("x\n")
    with regeneration_guard(tmp_path, {"a.csv"}, managed={"a.csv"}) as rep:
        (tmp_path / "a.csv").unlink()
        (tmp_path / "a.csv").write_text("y\n")
        assert rep.planned == frozenset({"a.csv"})

    with pytest.raises(PlannedArtifactNotWrittenError):
        with regeneration_guard(tmp_path, {"a.csv"}, managed={"a.csv"}):
            (tmp_path / "a.csv").unlink()

    # An in-block exception propagates unchanged; the post-check must not mask
    # the real error with a complaint about the artifact it never got to write.
    (tmp_path / "a.csv").write_text("z\n")
    with pytest.raises(ValueError, match="boom"):
        with regeneration_guard(tmp_path, {"a.csv"}, managed={"a.csv"}):
            (tmp_path / "a.csv").unlink()
            raise ValueError("boom")


def test_writer_calls_post_check_requires_both_halves_or_the_context_manager():
    pre_only = (
        "from simulator.regeneration_guard import assert_no_silent_artifact_loss\n"
        "assert_no_silent_artifact_loss(d, p, managed=m)\n"
    )
    both = (
        "from simulator.regeneration_guard import (\n"
        "    assert_no_silent_artifact_loss, verify_planned_artifacts_written,\n"
        ")\n"
        "r = assert_no_silent_artifact_loss(d, p, managed=m)\n"
        "verify_planned_artifacts_written(d, r)\n"
    )
    via_cm = (
        "from simulator.regeneration_guard import regeneration_guard\n"
        "with regeneration_guard(d, p, managed=m):\n"
        "    pass\n"
    )
    assert not _writer_calls_post_check(pre_only)
    assert _writer_calls_post_check(both)
    assert _writer_calls_post_check(via_cm)
    assert not _writer_calls_post_check("print('no guard')\n")


def test_guarded_regeneration_writers_call_the_post_check():
    overlap = GUARDED_REGENERATION_WRITERS & CLASSIFIED_UNGUARDED_WRITERS.keys()
    assert not overlap, f"writer listed as both guarded and unguarded: {sorted(overlap)}"
    missing = []
    unguarded = []
    for relative in sorted(GUARDED_REGENERATION_WRITERS):
        path = REPO_ROOT / relative
        if not path.is_file():
            missing.append(relative)
            continue
        if not _writer_calls_post_check(path.read_text(encoding="utf-8")):
            unguarded.append(relative)
    assert not missing, f"guarded writer path missing: {missing}"
    assert not unguarded, (
        "regeneration writer no longer calls the post-check "
        f"(pre-write-only is the 4fb9337f hole): {unguarded}"
    )


def test_classified_unguarded_writers_still_exist():
    missing = [
        relative
        for relative in CLASSIFIED_UNGUARDED_WRITERS
        if not (REPO_ROOT / relative).is_file()
    ]
    assert not missing, f"classified unguarded writer path missing: {missing}"


def test_regen_named_writers_must_be_classified():
    classified = GUARDED_REGENERATION_WRITERS | CLASSIFIED_UNGUARDED_WRITERS.keys()
    discovered = _discover_regen_named_writers()
    unknown = [path for path in discovered if path not in classified]
    assert not unknown, (
        "new regen-named write path is unclassified; add it to "
        "GUARDED_REGENERATION_WRITERS (and wire the guard) or to "
        f"CLASSIFIED_UNGUARDED_WRITERS with a reason: {unknown}"
    )


def _load_migrate_pilot_extracts():
    path = REPO_ROOT / "tools" / "migrate_pilot_extracts.py"
    spec = importlib.util.spec_from_file_location("migrate_pilot_extracts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrate_pilot_extracts_refuses_when_janaf_unlinked_and_not_rewritten(
    tmp_path, monkeypatch
):
    """Live P1-1 bypass: unlink of tracked janaf-4th.yaml plus empty rewrite."""
    migrate = _load_migrate_pilot_extracts()
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    (extracts / "janaf-4th.yaml").write_text("previous run\n", encoding="utf-8")
    (extracts / "nasa-cea-thermo.yaml").write_text("untouched\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "EXTRACTS", extracts)
    monkeypatch.setattr(migrate, "_find_research", lambda *a, **k: None)
    monkeypatch.setattr(migrate, "annotate_all_extracts_fidelity", lambda: 0)

    with pytest.raises(PlannedArtifactNotWrittenError) as excinfo:
        migrate.main()

    assert "janaf-4th.yaml" in str(excinfo.value)
