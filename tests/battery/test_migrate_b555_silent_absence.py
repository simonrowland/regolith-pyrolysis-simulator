"""b-555: silent absence of extracts is impossible.

Four extracts correctly produce no observations (table-shaped sidecar the
migrator does not consume; model-derived cohort excluded by owner ruling).
Their ABSENCE is correct; the SILENCE is not. Migrate must emit a typed
queue record per extract, and any extract that lands zero observations AND
zero typed records must itself emit one. Nothing is ingested as observations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import (
    SILENT_REASON_MODEL_COHORT,
    SILENT_REASON_NO_OBSERVATIONS,
    SILENT_REASON_TABLE_SHAPED,
    Migrator,
    migrate,
    silent_extract_reason,
)
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _queue_for(result, stem: str):
    needle = f"extracts/{stem}.yaml"
    return [e for e in result.queue if e.source and needle in e.source]


def _table_shaped_extract() -> dict:
    return {
        "schema_version": "literature_extract_table.v1",
        "source_id": "fixture-table-parent",
        "source_table": "S-I",
        "species": "K",
        "locator": {"table": "S-I", "note": "sidecar table"},
        "columns": "sample, p_K",
        "rows": [
            {"sample": "S1", "p_K": "1e-3"},
            {"sample": "S2", "p_K": "2e-3"},
        ],
    }


def _model_cohort_extract(*, source_id: str = "fixture-model-cohort") -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": {
            "citation": "Model, A. (2026), Fake Journal 1:1, DOI 10.1234/MODEL",
            "doi": "10.1234/MODEL",
            "year": 2026,
        },
        "extraction": {"method": "unit_test", "date": "2026-09-22", "worker": "pytest"},
        "review_status": "draft",
        "species": {},
        "evidence_policy": {
            "classification": "model",
            "measurement_claim": "No laboratory measurements.",
        },
        "model_tables": [
            {
                "table": "1",
                "classification": "model_derived",
                "values": {"method_class": "model_derived", "quantity": "inventory"},
            }
        ],
    }


def _empty_species_extract() -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["source_id"] = "fixture-empty-species"
    extract["species"] = {
        "Na": {"observations": [], "context": []},
    }
    return extract


def test_silent_extract_reason_classifies_table_and_model() -> None:
    assert silent_extract_reason(_table_shaped_extract()) == SILENT_REASON_TABLE_SHAPED
    assert silent_extract_reason(_model_cohort_extract()) == SILENT_REASON_MODEL_COHORT
    assert silent_extract_reason(_empty_species_extract()) == SILENT_REASON_NO_OBSERVATIONS


def test_table_shaped_extract_emits_typed_record_not_observations(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path, _table_shaped_extract())
    # rename fixture file to the table stem the migrator discovers
    extracts = root / "data" / "literature" / "extracts"
    (extracts / "fixture-source.yaml").rename(extracts / "fixture-table-s1-k.yaml")
    extracts.joinpath("fixture-table-s1-k.yaml").write_text(
        yaml.safe_dump(_table_shaped_extract(), sort_keys=False), encoding="utf-8"
    )
    result = migrate(root, write=False)
    assert result.observations == {}
    queued = _queue_for(result, "fixture-table-s1-k")
    assert len(queued) == 1
    assert queued[0].why == SILENT_REASON_TABLE_SHAPED
    assert queued[0].axes == ["document"]
    assert queued[0].observation_id == "fixture-table-s1-k::typed_absence"


def test_model_cohort_extract_emits_typed_record_not_observations(tmp_path: Path) -> None:
    doc = _model_cohort_extract()
    root = _write_min_tree(tmp_path, doc)
    extracts = root / "data" / "literature" / "extracts"
    (extracts / "fixture-source.yaml").unlink()
    extracts.joinpath("fixture-model-cohort.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    result = migrate(root, write=False)
    assert result.observations == {}
    queued = _queue_for(result, "fixture-model-cohort")
    assert len(queued) == 1
    assert queued[0].why == SILENT_REASON_MODEL_COHORT
    assert queued[0].observation_id == "fixture-model-cohort::typed_absence"


def test_zero_obs_zero_queue_class_is_impossible(tmp_path: Path) -> None:
    """Any extract that lands nothing must still emit a typed record."""
    doc = _empty_species_extract()
    root = _write_min_tree(tmp_path, doc)
    extracts = root / "data" / "literature" / "extracts"
    (extracts / "fixture-source.yaml").unlink()
    extracts.joinpath("fixture-empty-species.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    result = migrate(root, write=False)
    assert result.observations == {}
    queued = _queue_for(result, "fixture-empty-species")
    assert len(queued) == 1
    assert queued[0].why == SILENT_REASON_NO_OBSERVATIONS


def test_extract_with_observations_does_not_get_silent_record(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path, FIXTURE_EXTRACT)
    result = migrate(root, write=False)
    assert result.observations
    silent = [
        e
        for e in result.queue
        if e.why
        in {
            SILENT_REASON_TABLE_SHAPED,
            SILENT_REASON_MODEL_COHORT,
            SILENT_REASON_NO_OBSERVATIONS,
        }
    ]
    assert silent == []


def test_existing_queue_entry_suppresses_duplicate_silent_record(tmp_path: Path) -> None:
    doc = _model_cohort_extract(source_id="fixture-already-queued")
    root = _write_min_tree(tmp_path, doc)
    extracts = root / "data" / "literature" / "extracts"
    (extracts / "fixture-source.yaml").unlink()
    extracts.joinpath("fixture-already-queued.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )

    class Prefill(Migrator):
        def _migrate_extract(self, path: Path) -> None:  # type: ignore[override]
            rel = (
                path.relative_to(self.root).as_posix()
                if path.is_relative_to(self.root)
                else str(path)
            )
            # Pretend a prior typed refusal already landed for this extract.
            self.result.add_queue(
                work_id="prefill",
                locator={"source_path": rel},
                axes=["document"],
                why="pre-existing typed refusal",
                source=rel,
                observation_id="prefill::typed_absence",
            )
            super()._migrate_extract(path)

    result = Prefill(root).run()
    queued = _queue_for(result, "fixture-already-queued")
    assert len(queued) == 1
    assert queued[0].why == "pre-existing typed refusal"


def test_b555_named_corpus_extracts_emit_expected_reasons() -> None:
    """Live corpus: the four named b-555 extracts are typed, not silent."""
    repo = Path(__file__).resolve().parents[2]
    extracts = repo / "data" / "literature" / "extracts"
    named = {
        "bencze-yazhenskikh-2016-table-s1-k": SILENT_REASON_TABLE_SHAPED,
        "charnoz-2023-hydrogen-magma-ocean": SILENT_REASON_MODEL_COHORT,
        "lebrun-2013-magma-ocean-atmosphere": SILENT_REASON_MODEL_COHORT,
        "vanbuchem-2023-lavatmos": SILENT_REASON_MODEL_COHORT,
    }
    for stem, expected in named.items():
        path = extracts / f"{stem}.yaml"
        if not path.is_file():
            pytest.skip(f"corpus extract missing: {stem}")
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert silent_extract_reason(doc) == expected, stem

    # Migrate only those four in an isolated tree (full-corpus migrate is slow).
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp())
    try:
        lit = root / "data" / "literature"
        (lit / "extracts").mkdir(parents=True)
        (lit / "works").mkdir(parents=True)
        (root / "data" / "battery").mkdir(parents=True)
        (lit / "INDEX.yaml").write_text("sources: []\n", encoding="utf-8")
        (lit / "works" / "ALIASES.yaml").write_text(
            "schema_version: battery_work_aliases.v1\naliases: {}\n",
            encoding="utf-8",
        )
        for stem in named:
            shutil.copy(extracts / f"{stem}.yaml", lit / "extracts" / f"{stem}.yaml")
        result = migrate(root, write=True)
        assert result.observations == {}
        for stem, expected in named.items():
            queued = _queue_for(result, stem)
            assert len(queued) == 1, stem
            assert queued[0].why == expected, (stem, queued[0].why)
            v2 = lit / "extracts-v2" / f"{stem}.yaml"
            assert not v2.is_file(), f"{stem} must not gain an observations sibling"
        # Freshness contract: every extract has a queue trace.
        queue_text = (root / "data" / "battery" / "migration-queue.yaml").read_text(
            encoding="utf-8"
        )
        for stem in named:
            assert f"extracts/{stem}.yaml" in queue_text
    finally:
        shutil.rmtree(root)


def test_b555_silence_guard_mutation_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invert: disable the silence guard and the extract becomes silent again."""
    doc = _model_cohort_extract(source_id="fixture-mutation")
    root = _write_min_tree(tmp_path, doc)
    extracts = root / "data" / "literature" / "extracts"
    (extracts / "fixture-source.yaml").unlink()
    extracts.joinpath("fixture-mutation.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )

    guarded = migrate(root, write=False)
    assert len(_queue_for(guarded, "fixture-mutation")) == 1

    monkeypatch.setattr(
        Migrator,
        "_record_silent_extract_if_needed",
        lambda self, **kwargs: None,
    )
    silent = migrate(root, write=False)
    assert silent.observations == {}
    assert _queue_for(silent, "fixture-mutation") == []
