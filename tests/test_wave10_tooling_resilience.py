from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from scripts import grind_cleanup, sso2_owner_recipe_report
from scripts.grid_pregrind_writer import FINDER_INPUT_FIELDS, GridCacheWriter
from scripts.grind_harvest import harvest_snapshot


def _inputs(temperature_C: float) -> dict[str, object]:
    composition = {
        oxide: (10.0 if oxide == "SiO2" else 5.0 if oxide == "FeO" else 0.0)
        for oxide in (
            "SiO2",
            "TiO2",
            "Al2O3",
            "Fe2O3",
            "Cr2O3",
            "FeO",
            "MnO",
            "MgO",
            "NiO",
            "CoO",
            "CaO",
            "Na2O",
            "K2O",
            "P2O5",
        )
    }
    values: dict[str, object] = {
        "temperature_C": temperature_C,
        "kress91_partition_provenance": {"status": "test-only"},
        "composition_kg": None,
        "fO2_log": -9.0,
        "pressure_bar": 1.0,
        "composition_mol": composition,
        "composition_mol_by_account": {"process.cleaned_melt": composition},
        "species_formula_registry": None,
        "mode": "subprocess",
        "subprocess_run_mode": "isothermal",
        "redox_buffer": None,
        "fO2_offset": None,
        "Fe3Fet_Liq": None,
        "model": "MELTSv1.0.2",
        "timeout_s": 20.0,
        "require_petthermotools": False,
        "thermoengine_health_timeout_s": 8.0,
    }
    values.update({name: None for name in FINDER_INPUT_FIELDS})
    return values


def _output() -> dict[str, object]:
    return {
        "status": "ok",
        "status_kind": "success",
        "refusal_reason": None,
        "raw_payload": "{}",
        "raw_payload_format": "test",
        "timing_s": 1.0,
        "engine_version": "test",
        "engine_mode": "subprocess",
        "engine_model": "MELTSv1.0.2",
        "generic": {},
        "alphamelts": {},
        "finder": {},
    }


def _source_database(path, *, seed: int, temperature_C: float) -> None:
    with GridCacheWriter(path) as writer:
        batch_id = writer.ensure_batch(
            label="fixed",
            kind="fixed",
            seed=seed,
            params={"seed": seed},
        )
        assert batch_id == 1
        assert writer.materialize_key(
            _inputs(temperature_C),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        pending = writer.pending_rows(batch_id=batch_id)
        assert len(pending) == 1
        assert writer.write_result(pending[0]["grid_key_id"], _output())


def test_pending_grid_key_has_one_owner_and_stale_claim_can_be_recovered(
    tmp_path,
) -> None:
    database = tmp_path / "grid.sqlite"
    first = GridCacheWriter(database)
    batch_id = first.ensure_batch(
        label="fixed",
        kind="fixed",
        seed=1,
        params={"seed": 1},
    )
    first.materialize_key(
        _inputs(1200.0),
        batch_id=batch_id,
        shuffle_rank=0,
        shard=0,
    )
    first.commit()
    second = GridCacheWriter(database, existing_only=True)
    try:
        claimed = first.pending_rows(batch_id=batch_id, fetch_limit=1000)
        assert len(claimed) == 1
        assert second.pending_rows(batch_id=batch_id, fetch_limit=1000) == []
        claim_count = first.connection.execute(
            "SELECT COUNT(*) FROM grid_key_claims"
        ).fetchone()[0]
        assert claim_count == 1

        with pytest.raises(RuntimeError, match="not claimed by this writer"):
            second.write_result(claimed[0]["grid_key_id"], _output())

        first.connection.execute(
            "UPDATE grid_key_claims SET expires_at_epoch = 0"
        )
        first.commit()
        reclaimed = second.pending_rows(batch_id=batch_id, fetch_limit=1000)
        assert [row["grid_key_id"] for row in reclaimed] == [
            claimed[0]["grid_key_id"]
        ]
        with pytest.raises(RuntimeError, match="not claimed by this writer"):
            first.write_result(claimed[0]["grid_key_id"], _output())
        assert second.write_result(reclaimed[0]["grid_key_id"], _output())
    finally:
        second.close()
        first.close()


def test_pending_claim_and_result_do_not_commit_caller_transaction(tmp_path) -> None:
    database = tmp_path / "grid.sqlite"
    with GridCacheWriter(database) as writer:
        batch_id = writer.ensure_batch(
            label="fixed",
            kind="fixed",
            seed=1,
            params={"seed": 1},
        )
        assert writer.materialize_key(
            _inputs(1200.0),
            batch_id=batch_id,
            shuffle_rank=0,
            shard=0,
        )
        assert writer.connection.in_transaction

        pending = writer.pending_rows(batch_id=batch_id)
        assert len(pending) == 1
        assert writer.write_result(pending[0]["grid_key_id"], _output())
        assert writer.connection.in_transaction

        with sqlite3.connect(database) as observer:
            assert observer.execute("SELECT COUNT(*) FROM grid_keys").fetchone()[0] == 0
            assert (
                observer.execute("SELECT COUNT(*) FROM alphamelts_outputs").fetchone()[0]
                == 0
            )

        writer.connection.rollback()


def test_harvest_namespaces_source_local_batch_identity(tmp_path) -> None:
    source_a = tmp_path / "source-a.sqlite"
    source_b = tmp_path / "source-b.sqlite"
    accumulator = tmp_path / "accumulator.sqlite"
    _source_database(source_a, seed=1, temperature_C=1200.0)
    _source_database(source_b, seed=2, temperature_C=1300.0)

    first = harvest_snapshot(source_a, accumulator, source_host="studio")
    second = harvest_snapshot(source_b, accumulator, source_host="studio")
    replay = harvest_snapshot(source_b, accumulator, source_host="studio")

    assert first["inserted"] == second["inserted"] == 1
    assert replay["attempted"] == 0
    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT COUNT(DISTINCT target_batch_id) FROM harvest_batch_map"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM grid_keys"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM alphamelts_outputs"
        ).fetchone()[0] == 2


def test_harvest_backfills_legacy_batch_mapping_without_splitting_batch(
    tmp_path,
) -> None:
    source = tmp_path / "source.sqlite"
    accumulator = tmp_path / "accumulator.sqlite"
    _source_database(source, seed=1, temperature_C=1200.0)
    harvest_snapshot(source, accumulator, source_host="studio")
    with sqlite3.connect(accumulator) as connection:
        legacy_batch_id = connection.execute(
            "SELECT batch_id FROM grid_keys"
        ).fetchone()[0]
        connection.execute("DELETE FROM harvest_batch_map")

    with GridCacheWriter(source, existing_only=True) as writer:
        batch_id = writer.connection.execute(
            "SELECT batch_id FROM batches WHERE label = 'fixed'"
        ).fetchone()[0]
        assert writer.materialize_key(
            _inputs(1300.0),
            batch_id=batch_id,
            shuffle_rank=1,
            shard=0,
        )
        pending = writer.pending_rows(batch_id=batch_id)
        assert len(pending) == 1
        assert writer.write_result(pending[0]["grid_key_id"], _output())

    summary = harvest_snapshot(source, accumulator, source_host="studio")

    assert summary["inserted"] == 1
    with sqlite3.connect(accumulator) as connection:
        assert connection.execute(
            "SELECT COUNT(DISTINCT batch_id) FROM grid_keys"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT target_batch_id FROM harvest_batch_map"
        ).fetchone()[0] == legacy_batch_id


def test_streamed_cleanup_has_no_synthetic_repo_root(tmp_path, monkeypatch) -> None:
    candidate = tmp_path / "grind-smoke"
    candidate.mkdir()
    monkeypatch.setattr(grind_cleanup, "REPO_ROOT", None)

    assert grind_cleanup._repo_root_from_script("<stdin>") is None
    assert grind_cleanup.protected_paths() == ()
    plan = grind_cleanup.build_cleanup_plan([tmp_path])
    assert [item.path for item in plan.candidates] == [candidate]
    assert plan.refusals == ()


def test_doe_timing_log_failure_preserves_primary_evaluator_error(
    tmp_path,
    monkeypatch,
) -> None:
    from scripts import run_fidelity_doe

    class PrimaryEvaluatorError(RuntimeError):
        pass

    def fail_evaluate(*_args, **_kwargs):
        raise PrimaryEvaluatorError("primary evaluator taxonomy")

    monkeypatch.setattr(run_fidelity_doe, "_evaluate", fail_evaluate)
    monkeypatch.setattr(
        run_fidelity_doe,
        "TIMING_LOG",
        str(tmp_path / "missing-parent" / "timings.jsonl"),
    )

    with pytest.raises(PrimaryEvaluatorError) as excinfo:
        run_fidelity_doe._timed_evaluate(
            patch={},
            feedstock_id="feedstock",
            fidelity="high",
            candidate_id="candidate-1",
        )

    assert "primary evaluator taxonomy" in str(excinfo.value)
    notes = getattr(excinfo.value, "__notes__", ())
    assert any(
        "timing-log reporting failed: FileNotFoundError" in note for note in notes
    )


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    (("available", 0), ("missing_stage_3_stream", 2)),
)
def test_sso2_report_exit_code_matches_evidence_status(
    tmp_path,
    monkeypatch,
    capsys,
    status: str,
    expected_exit: int,
) -> None:
    output = tmp_path / f"{status}.md"
    evidence = {"status": status, "status_reason": "test"}
    monkeypatch.setattr(
        sso2_owner_recipe_report,
        "build_sso2_owner_recipe_execution",
        lambda **_kwargs: SimpleNamespace(status="ok"),
    )
    monkeypatch.setattr(
        sso2_owner_recipe_report,
        "sso2_owner_recipe_evidence",
        lambda _execution: evidence,
    )
    monkeypatch.setattr(
        sso2_owner_recipe_report,
        "_markdown_report",
        lambda _evidence, _execution: "report\n",
    )

    exit_code = sso2_owner_recipe_report.main(
        ["--hours", "0", "--output", str(output), "--json"]
    )

    assert exit_code == expected_exit
    assert output.read_text(encoding="utf-8") == "report\n"
    assert output.with_suffix(".json").is_file()
    assert f"status: {status}" in capsys.readouterr().out
