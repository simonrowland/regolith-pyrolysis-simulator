"""Persisted method-split experiments keep referential integrity.

write_outputs emits only experiments_by_work. A method split that stores the
new experiment in the in-memory dict and skips that list drops the id on
reload. Asset inputs fail with it, because asset ids are read from the
resolved work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.migrate import Migrator, load_migrated_store
from simulator.battery.store_validation import _issue_shape
from simulator.battery.validate import validate_corpus
from tests.battery.test_migrate import _migrate_real_extract

# Mysen still mints a method id: Mössbauer is a printed method and the
# wet-chemical rows are unstated, so they stay on the named experiment.
# Shornikov's gaps are unstated or the same Knudsen method and must not mint.
MYSEN = "mysen-1985-cmas-redox.yaml"
_REFINT_SHAPES = frozenset(
    {
        "referential_integrity:experiment_id",
        "referential_integrity:inputs",
        "referential_integrity:derived_from",
    }
)


def _assert_reloaded_split_ids_resolve(root: Path) -> None:
    works, experiments, observations = load_migrated_store(root)
    split_rows = [
        observation
        for observation in observations.values()
        if "::method:" in observation.experiment_id
    ]
    assert split_rows, "mysen did not mint a method-qualified experiment id"
    missing = sorted(
        {
            observation.experiment_id
            for observation in split_rows
            if observation.experiment_id not in experiments
        }
    )
    assert not missing, missing[:5]
    report = validate_corpus(works, experiments, observations)
    shapes = [
        f"{_issue_shape(issue)} {issue.path} {issue.detail}"
        for issue in report.hard_issues
        if _issue_shape(issue) in _REFINT_SHAPES
    ]
    assert not shapes, "\n".join(shapes[:20])


def test_method_split_ids_resolve_after_reload(tmp_path: Path) -> None:
    _migrate_real_extract(
        tmp_path,
        MYSEN,
        write=True,
        use_repository_index_row=True,
    )
    _assert_reloaded_split_ids_resolve(tmp_path / "tree")


def test_dropping_the_publish_map_dangles_method_split_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drop_registration(self, experiment):
        self.result.experiments[experiment.experiment_id] = experiment
        return experiment

    monkeypatch.setattr(Migrator, "_publish_experiment", drop_registration)
    result = _migrate_real_extract(
        tmp_path,
        MYSEN,
        write=True,
        use_repository_index_row=True,
    )
    split_rows = [
        observation
        for observation in result.observations.values()
        if "::method:" in observation.experiment_id
    ]
    assert split_rows
    work_id = result.experiments[split_rows[0].experiment_id].work_id or ""
    assert split_rows[0].experiment_id not in result.experiments_by_work[work_id]
    with pytest.raises(AssertionError):
        _assert_reloaded_split_ids_resolve(tmp_path / "tree")
