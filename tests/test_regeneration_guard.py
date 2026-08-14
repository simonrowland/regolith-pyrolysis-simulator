"""Unit tests for the regenerate-and-replace shrinkage guard."""

from __future__ import annotations

import pytest

from simulator.regeneration_guard import (
    RegenerationGuardReport,
    RegenerationShrinkageError,
    RetiredArtifactWarning,
    assert_no_silent_artifact_loss,
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
