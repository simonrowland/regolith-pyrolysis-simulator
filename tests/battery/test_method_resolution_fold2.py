from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import MethodToken, NoticeKind
from simulator.battery.migrate import Migrator
from tests.battery.test_migrate import (
    FIXTURE_EXTRACT,
    REPO_ROOT,
    _migrate_real_extract,
)


def _rows_for_local_id(result, source_id, local_id):
    prefix = f"{source_id}::{local_id}"
    return [
        row
        for observation_id, row in result.observations.items()
        if observation_id == prefix or observation_id.startswith(f"{prefix}::")
    ]


def _assert_split_evidence_is_cleared(snapshot):
    assert snapshot["experiment_id"] not in snapshot["evidence"]


def _fixture_with_shared_methods():
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    source_row = extract["species"]["Na"]["observations"][0]
    source_row.pop("regime")
    source_row["experiment"] = "declared-run"
    source_row["observation_id"] = "inferred-first"
    source_row["note"] = "Measured by solution calorimetry."

    knudsen_row = yaml.safe_load(yaml.safe_dump(source_row))
    knudsen_row["observation_id"] = "split-knudsen"
    knudsen_row.pop("note")
    knudsen_row["regime"] = "kems_effusion"

    calorimetry_row = yaml.safe_load(yaml.safe_dump(source_row))
    calorimetry_row["observation_id"] = "exact-calorimetry"
    calorimetry_row.pop("note")
    calorimetry_row["regime"] = "solution calorimetry"

    extract["experiments"] = [
        {"experiment_id": "declared-run", "method": "unspecified"}
    ]
    extract["species"]["Na"]["observations"] = [
        source_row,
        knudsen_row,
        calorimetry_row,
    ]
    return extract


def test_split_clears_inferred_method_evidence_and_rejects_legacy_leak(
    tmp_path, monkeypatch
):
    extract = _fixture_with_shared_methods()
    original = Migrator._migrate_extract_observation
    split_snapshot = {}

    def capture_split_evidence(self, **kwargs):
        original(self, **kwargs)
        if kwargs["obs"].get("observation_id") == "split-knudsen":
            split_snapshot["experiment_id"] = kwargs["declared_experiment_id"]
            split_snapshot["evidence"] = dict(
                self._inferred_method_evidence_by_experiment
            )

    with monkeypatch.context() as capture:
        capture.setattr(
            Migrator, "_migrate_extract_observation", capture_split_evidence
        )
        result = _migrate_real_extract(
            tmp_path / "current", "zhang-2021-acsesc-si.yaml", extract_doc=extract
        )
    inferred_rows = _rows_for_local_id(
        result, extract["source_id"], "inferred-first"
    )
    exact_rows = _rows_for_local_id(
        result, extract["source_id"], "exact-calorimetry"
    )
    assert inferred_rows and exact_rows
    assert all(
        any(n.kind is NoticeKind.METHOD_INFERRED for n in row.notices)
        for row in inferred_rows
    )
    assert all(
        not any(n.kind is NoticeKind.METHOD_INFERRED for n in row.notices)
        for row in exact_rows
    ), [(row.observation_id, row.experiment_id, row.notices) for row in exact_rows]
    _assert_split_evidence_is_cleared(split_snapshot)

    legacy_snapshot = {}

    def legacy_split_keeps_inference(self, **kwargs):
        original(self, **kwargs)
        if kwargs["obs"].get("observation_id") == "split-knudsen":
            experiment_id = kwargs["declared_experiment_id"]
            self._inferred_method_evidence_by_experiment[experiment_id] = (
                MethodToken.SOLUTION_CALORIMETRY,
                "source text names 'solution calorimetry'",
                "source method text: measured by solution calorimetry",
            )
            legacy_snapshot["experiment_id"] = experiment_id
            legacy_snapshot["evidence"] = dict(
                self._inferred_method_evidence_by_experiment
            )

    with monkeypatch.context() as mutation:
        mutation.setattr(
            Migrator,
            "_migrate_extract_observation",
            legacy_split_keeps_inference,
        )
        _migrate_real_extract(
            tmp_path / "legacy",
            "zhang-2021-acsesc-si.yaml",
            extract_doc=extract,
        )
    assert legacy_snapshot["experiment_id"] in legacy_snapshot["evidence"]
    with pytest.raises(AssertionError):
        _assert_split_evidence_is_cleared(legacy_snapshot)


def test_missing_row_method_is_order_independent_without_declared_method(tmp_path):
    filename = "mysen-1985-cmas-redox.yaml"
    source_id = "mysen-1985-cmas-redox"
    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts" / filename).read_text(
            encoding="utf-8"
        )
    )
    observations = extract["species"]["Fe"]["observations"]
    wet = next(
        row for row in observations
        if row["observation_id"] == "mysen_1985_table1_wet_chemistry_fe3_sumfe"
    )
    wet["values"].pop("method")
    mossbauer = next(
        row for row in observations
        if row["observation_id"] == "mysen_1985_table1_mossbauer_fe3_sumfe"
    )
    wet_index, mossbauer_index = observations.index(wet), observations.index(mossbauer)

    signatures = []
    for reverse, label in ((False, "forward"), (True, "reverse")):
        ordered = yaml.safe_load(yaml.safe_dump(extract))
        if reverse:
            rows = ordered["species"]["Fe"]["observations"]
            rows[wet_index], rows[mossbauer_index] = rows[mossbauer_index], rows[wet_index]
        result = _migrate_real_extract(
            tmp_path / label, filename, extract_doc=ordered
        )
        wet_rows = _rows_for_local_id(
            result, source_id, "mysen_1985_table1_wet_chemistry_fe3_sumfe"
        )
        mossbauer_rows = _rows_for_local_id(
            result, source_id, "mysen_1985_table1_mossbauer_fe3_sumfe"
        )
        assert wet_rows and mossbauer_rows
        assert all(
            result.experiments[row.experiment_id].method.value
            is MethodToken.UNSPECIFIED
            for row in wet_rows
        )
        assert all(
            any(n.kind is NoticeKind.METHOD_UNSTATED for n in row.notices)
            for row in wet_rows
        )
        assert all(
            result.experiments[row.experiment_id].method.value
            is MethodToken.MOSSBAUER_SPECTROSCOPY
            for row in mossbauer_rows
        )
        signatures.append(
            {
                row.observation_id: (
                    result.experiments[row.experiment_id].method,
                    row.experiment_id,
                    tuple(
                        (notice.kind, notice.reason)
                        for notice in row.notices
                        if notice.kind
                        in {NoticeKind.METHOD_UNSTATED, NoticeKind.METHOD_INFERRED}
                    ),
                )
                for row in (*wet_rows, *mossbauer_rows)
            }
        )
    assert signatures[0] == signatures[1]


def test_missing_row_method_inherits_only_a_declared_experiment_method(tmp_path):
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row.pop("regime")
    row["experiment"] = "declared-run"
    extract["experiments"] = [
        {"experiment_id": "declared-run", "method": "kems_effusion"}
    ]

    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    observations = _rows_for_local_id(result, extract["source_id"], "na_psat")
    assert observations
    assert all(
        result.experiments[observation.experiment_id].method.value
        is MethodToken.KNUDSEN_EFFUSION
        for observation in observations
    )
    assert all(
        not any(notice.kind is NoticeKind.METHOD_UNSTATED for notice in row.notices)
        for row in observations
    )


def test_method_split_preserves_printed_oxygen_from_earlier_sibling_row(tmp_path):
    filename = "darken-gurry-1946-iron-oxygen-ii.yaml"
    source_id = "darken-gurry-1946-iron-oxygen-ii"
    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts" / filename).read_text(
            encoding="utf-8"
        )
    )
    selected_ids = {
        "dg46_t1_l_1589_fe2o3",
        "dg46_t1_l_1636_fe2o3",
    }
    for species in extract["species"].values():
        species["observations"] = [
            row
            for row in species["observations"]
            if row.get("observation_id") in selected_ids
        ]

    result = _migrate_real_extract(
        tmp_path, filename, extract_doc=extract
    )
    rows = [
        row
        for local_id in selected_ids
        for row in _rows_for_local_id(result, source_id, local_id)
    ]
    assert len(rows) == 2
    assert len({row.experiment_id for row in rows}) == 1
    experiment = result.experiments[rows[0].experiment_id]
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.UNSPECIFIED
    control = experiment.fO2_control
    assert control is not None
    assert control.oxygen_partial_pressure_Pa is not None
    assert (
        control.oxygen_partial_pressure_Pa.state.value.point
        == Decimal("101325")
    )


BRITT_SOURCE = "britt-2019-asteroid-simulants"
BRITT_FILENAME = f"{BRITT_SOURCE}.yaml"
BRITT_CI_ROW = "britt_2019_ci_table3_xrf_major_oxide_composition"
BRITT_DECLARED_EXPERIMENT = f"{BRITT_SOURCE}::experiment::xrf-ci"


def _assert_britt_registry_map_is_not_repeated(result):
    rows = _rows_for_local_id(result, BRITT_SOURCE, BRITT_CI_ROW)
    assert rows
    # Unmapped XRF text is UNSPECIFIED, not a second method. The rows stay
    # on the named experiment. The sample map stays on that experiment.
    assert all(row.experiment_id == BRITT_DECLARED_EXPERIMENT for row in rows)
    assert all(
        "printed_composition" not in (row.point_conditions or {})
        for row in rows
    )


def test_britt_registry_sample_map_stays_owned_after_method_split(tmp_path):
    result = _migrate_real_extract(
        tmp_path, BRITT_FILENAME
    )
    _assert_britt_registry_map_is_not_repeated(result)


def test_britt_registry_repeat_mutation_restores_post_split_key_bug(
    tmp_path, monkeypatch
):
    original = Migrator._repeats_declared_sample_map

    def legacy_post_split_key(self, experiment_id, printed, *, declared_experiment_id):
        return original(
            self,
            experiment_id,
            printed,
            declared_experiment_id=None,
        )

    with monkeypatch.context() as mutation:
        mutation.setattr(
            Migrator, "_repeats_declared_sample_map", legacy_post_split_key
        )
        result = _migrate_real_extract(
            tmp_path / "mutated", BRITT_FILENAME
        )

    with pytest.raises(AssertionError):
        _assert_britt_registry_map_is_not_repeated(result)


def _fixture_with_unstated_method():
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    source_row = extract["species"]["Na"]["observations"][0]
    source_row.pop("regime", None)
    source_row["experiment"] = "declared-run"
    source_row["observation_id"] = "unstated-a"
    other = yaml.safe_load(yaml.safe_dump(source_row))
    other["observation_id"] = "unstated-b"
    extract["experiments"] = [{"experiment_id": "declared-run"}]
    extract["species"]["Na"]["observations"] = [source_row, other]
    return extract


def _unstated_experiment_ids(result, source_id):
    rows = [
        *_rows_for_local_id(result, source_id, "unstated-a"),
        *_rows_for_local_id(result, source_id, "unstated-b"),
    ]
    assert rows
    return {row.experiment_id for row in rows}


def test_unstated_method_does_not_mint_a_second_experiment(tmp_path):
    extract = _fixture_with_unstated_method()
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    experiment_ids = _unstated_experiment_ids(result, extract["source_id"])
    assert len(experiment_ids) == 1
    assert not any(experiment_id.endswith(":unspecified") for experiment_id in experiment_ids)


def test_legacy_unspecified_split_mints_a_second_experiment(tmp_path, monkeypatch):
    """The old rule treated UNSPECIFIED as its own method and doubled the run."""

    extract = _fixture_with_unstated_method()
    original = Migrator._method_row_splits

    def split_unspecified(self, experiment_id, method):
        if (
            method.is_value
            and method.value is MethodToken.UNSPECIFIED
            and experiment_id in self._method_split_home
        ):
            return True
        return original(self, experiment_id, method)

    monkeypatch.setattr(Migrator, "_method_row_splits", split_unspecified)
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    with pytest.raises(AssertionError):
        experiment_ids = _unstated_experiment_ids(result, extract["source_id"])
        assert len(experiment_ids) == 1
        assert not any(
            experiment_id.endswith(":unspecified") for experiment_id in experiment_ids
        )
