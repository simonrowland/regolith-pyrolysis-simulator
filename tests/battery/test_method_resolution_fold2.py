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
    assert any(row.experiment_id != BRITT_DECLARED_EXPERIMENT for row in rows)
    assert all(
        "printed_composition" not in (row.point_conditions or {})
        for row in rows
    )


def test_britt_registry_sample_map_stays_owned_after_method_split(tmp_path):
    result = _migrate_real_extract(
        tmp_path, BRITT_FILENAME
    )
    _assert_britt_registry_map_is_not_repeated(result)


def _britt_method_field_split_extract():
    """The CI row names knudsen; the declared technique is electron microprobe.

    Regime no longer mints an experiment id, so the post-split sample-map
    key has to be proved on a method field. The declared token is the home.
    Knudsen leaves, and the registry map must stay off that row's point.
    """

    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts" / BRITT_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    for experiment in extract["experiments"]:
        if experiment.get("experiment_id") == "xrf-ci":
            experiment["method"] = MethodToken.ELECTRON_MICROPROBE.value
    for block in extract["species"].values():
        observations = block.get("observations") or []
        for row in observations:
            if row.get("observation_id") == BRITT_CI_ROW:
                row["method"] = MethodToken.KNUDSEN_EFFUSION.value
                row.pop("regime", None)
    return extract


def _assert_method_field_split_keeps_registry_map(result):
    rows = _rows_for_local_id(result, BRITT_SOURCE, BRITT_CI_ROW)
    assert rows
    assert any(
        row.experiment_id.endswith(
            f"::method:{MethodToken.KNUDSEN_EFFUSION.value}"
        )
        for row in rows
    )
    assert all(
        "printed_composition" not in (row.point_conditions or {})
        for row in rows
    )


def test_method_field_split_keeps_declared_sample_map_off_the_point(tmp_path):
    result = _migrate_real_extract(
        tmp_path,
        BRITT_FILENAME,
        extract_doc=_britt_method_field_split_extract(),
    )
    _assert_method_field_split_keeps_registry_map(result)


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
            tmp_path / "mutated",
            BRITT_FILENAME,
            extract_doc=_britt_method_field_split_extract(),
        )

    with pytest.raises(AssertionError):
        _assert_method_field_split_keeps_registry_map(result)


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


def _assay_on_named_run_extract():
    """Piston-cylinder run; the row prose is the electron-microprobe assay."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row.pop("regime", None)
    row["experiment"] = "named-run"
    row["observation_id"] = "assay-row"
    row["regime"] = "quench_equilibration"
    row["values"]["method_as_printed"] = (
        "Electron microprobe WDS for the sulfur concentration"
    )
    extract["experiments"] = [
        {
            "experiment_id": "named-run",
            "method": "piston_cylinder_equilibration_quench",
        }
    ]
    return extract


def _assert_assay_stays_on_named_run(result, source_id):
    rows = _rows_for_local_id(result, source_id, "assay-row")
    assert rows
    assert len({row.experiment_id for row in rows}) == 1
    assert all("::method:" not in row.experiment_id for row in rows)
    assert all(
        row.experiment_id.endswith("::experiment::named-run") for row in rows
    )


def test_assay_prose_stays_on_the_named_run(tmp_path):
    extract = _assay_on_named_run_extract()
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    _assert_assay_stays_on_named_run(result, extract["source_id"])


def _agreeing_regime_on_named_run_extract():
    """Knudsen run. The regime is the exact alias; the method sentence is not."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["experiment"] = "named-knudsen"
    row["observation_id"] = "agreeing-regime-row"
    row["regime"] = "kems_effusion"
    row["method"] = (
        "Knudsen effusion with high-temperature mass-spectral analysis of the vapor"
    )
    extract["experiments"] = [
        {"experiment_id": "named-knudsen", "method": "knudsen_effusion"}
    ]
    return extract


def _assert_agreeing_regime_has_no_method_notice(result, source_id):
    rows = _rows_for_local_id(result, source_id, "agreeing-regime-row")
    assert rows
    assert all(
        row.experiment_id.endswith("::experiment::named-knudsen") for row in rows
    )
    assert all("::method:" not in row.experiment_id for row in rows)
    assert all(
        result.experiments[row.experiment_id].method.is_value
        and result.experiments[row.experiment_id].method.value
        is MethodToken.KNUDSEN_EFFUSION
        for row in rows
    )
    assert all(
        not any(
            notice.kind in {NoticeKind.METHOD_UNSTATED, NoticeKind.METHOD_INFERRED}
            for notice in row.notices
        )
        for row in rows
    )


def test_agreeing_regime_resolves_without_a_method_notice(tmp_path):
    extract = _agreeing_regime_on_named_run_extract()
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    _assert_agreeing_regime_has_no_method_notice(result, extract["source_id"])


def test_dropping_an_agreeing_regime_emits_a_method_notice(tmp_path, monkeypatch):
    extract = _agreeing_regime_on_named_run_extract()
    original = Migrator._resolve_method_for_observation

    def drop_regime(self, *args, **kwargs):
        kwargs["regime_candidates"] = ()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Migrator, "_resolve_method_for_observation", drop_regime)
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    with pytest.raises(AssertionError):
        _assert_agreeing_regime_has_no_method_notice(result, extract["source_id"])


def test_treating_assay_prose_as_a_method_field_mints_a_second_id(
    tmp_path, monkeypatch
):
    import simulator.battery.migrate as migrate_module

    extract = _assay_on_named_run_extract()
    original = migrate_module._method_claim_fields

    def assay_as_method_field(obs, values, **kwargs):
        regime, raw, printed, evidence, notice_evidence = original(obs, values, **kwargs)
        return regime, (*raw, *printed), (), evidence, notice_evidence

    monkeypatch.setattr(migrate_module, "_method_claim_fields", assay_as_method_field)
    result = _migrate_real_extract(
        tmp_path, "zhang-2021-acsesc-si.yaml", extract_doc=extract
    )
    with pytest.raises(AssertionError):
        _assert_assay_stays_on_named_run(result, extract["source_id"])


def _prose_declared_method_with_unmapped_regime():
    """Prose declared method. Calorimetry is home. Knudsen carries a bad regime."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    source_row = extract["species"]["Na"]["observations"][0]
    source_row.pop("regime", None)
    source_row["experiment"] = "named-run"
    source_row["observation_id"] = "calorimetry-home"
    source_row["method"] = "calorimetry"
    knudsen_row = yaml.safe_load(yaml.safe_dump(source_row))
    knudsen_row["observation_id"] = "knudsen-beside-unmapped-regime"
    knudsen_row["method"] = "knudsen_effusion"
    knudsen_row["regime"] = "not_a_closed_method_label"
    extract["experiments"] = [
        {
            "experiment_id": "named-run",
            "method": "laboratory campaign on one named run",
        }
    ]
    extract["species"]["Na"]["observations"] = [source_row, knudsen_row]
    return extract


def _assert_printed_knudsen_survives_unmapped_regime(result, source_id):
    calorimetry = _rows_for_local_id(result, source_id, "calorimetry-home")
    knudsen = _rows_for_local_id(
        result, source_id, "knudsen-beside-unmapped-regime"
    )
    assert calorimetry and knudsen
    assert all(
        row.experiment_id.endswith("::experiment::named-run")
        and "::method:" not in row.experiment_id
        for row in calorimetry
    )
    stored = []
    for row in knudsen:
        experiment = result.experiments[row.experiment_id]
        method_value = (
            experiment.method.value.value if experiment.method.is_value else None
        )
        stored.append((row.experiment_id, method_value))
        assert row.experiment_id.endswith("::method:knudsen_effusion"), stored
        assert method_value == "knudsen_effusion", stored
        assert "unspecified" not in row.experiment_id
        assert not any(
            "not_a_closed_method_label" in notice.reason for notice in row.notices
        ), stored
    return stored


def test_unmapped_regime_does_not_replace_a_printed_method_on_the_split(
    tmp_path,
):
    extract = _prose_declared_method_with_unmapped_regime()
    result = _migrate_real_extract(
        tmp_path, "fixture-source.yaml", extract_doc=extract
    )
    _assert_printed_knudsen_survives_unmapped_regime(result, extract["source_id"])


def test_old_candidate_order_lets_the_unmapped_regime_lead(tmp_path, monkeypatch):
    import simulator.battery.migrate as migrate_module

    def old_order(
        *,
        method_field_is_printed,
        declared_method_is_printed,
        regime_agrees_with_declared,
    ):
        return (not declared_method_is_printed) or (
            regime_agrees_with_declared and not method_field_is_printed
        )

    monkeypatch.setattr(
        migrate_module,
        "_regime_candidates_precede_method_field",
        old_order,
    )
    extract = _prose_declared_method_with_unmapped_regime()
    result = _migrate_real_extract(
        tmp_path, "fixture-source.yaml", extract_doc=extract
    )
    with pytest.raises(AssertionError):
        _assert_printed_knudsen_survives_unmapped_regime(
            result, extract["source_id"]
        )
