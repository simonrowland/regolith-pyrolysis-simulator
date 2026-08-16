"""Unit tests for the regenerate-and-replace shrinkage guard."""

from __future__ import annotations

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
