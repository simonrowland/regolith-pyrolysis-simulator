from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile

from flask import Flask
import pytest
import yaml

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.corpus_version import current_corpus_version
from simulator.cost_parameters import default_cost_parameters_block
from simulator.optimize.evalspec import EvalSpec, current_code_version
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize import import_bundle as import_bundle_module
from simulator.optimize.import_bundle import (
    IMPORTED_DIR_NAME,
    ImportBundleError,
    YAML_CAP_BYTES,
    import_study_bundle,
    imported_study_model,
    open_untrusted_result_db,
)
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.recipe import RecipeSchema
from simulator.optimize.save_bundle import (
    ARTIFACT_INDEX_NAME,
    MEMBER_SCHEMA_VERSION,
    SAVE_SCHEMA_VERSION,
    export_study_bundle,
)
from web import routes as web_routes


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    return app.test_client()


def _schema() -> RecipeSchema:
    return RecipeSchema()


def _data_digests() -> dict[str, str]:
    return {
        "setpoints": "setpoints-digest",
        "feedstocks": "feedstocks-digest",
        "foulant_thermo": "foulant-thermo-digest",
        "materials": "materials-digest",
        "vapor_pressures": "vapor-pressures-digest",
        "species_catalog": "species-catalog-digest",
        "profile": "profile-digest",
    }


def _eval_spec_payload(
    *,
    code_version: str = current_code_version(),
    data_digests: dict[str, str] | None = None,
    cost_parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    schema = _schema()
    return {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": code_version,
        "data_digests": data_digests or _data_digests(),
        "backend_name": ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        "chemistry_kernel": {"engine": "builtin"},
        "allowlist_version": schema.allowlist_version,
        "bounds_digest": schema.bounds_digest,
        "cost_parameters": cost_parameters or default_cost_parameters_block(),
    }


def _eval_spec(**kwargs: object) -> EvalSpec:
    payload = _eval_spec_payload(**kwargs)
    return EvalSpec(**payload)  # type: ignore[arg-type]


def _margin() -> GateMargin:
    return GateMargin(
        gate="delivered_stream_purity",
        feasible=True,
        margin=0.25,
        threshold=ThresholdSpec(
            id="purity",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test",
        ),
        observed=0.98,
        detail="test margin",
    )


def _scored(
    *,
    oxygen: float,
    code_version: str = current_code_version(),
    candidate_id: str = "candidate-a",
    cost_parameters: dict[str, object] | None = None,
) -> ScoredResult:
    spec = _eval_spec(code_version=code_version, cost_parameters=cost_parameters)
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key="cache-a",
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        failing_gates=(),
        run_reference=RunReference(status="ok"),
        notes=(),
    )


def _evaluator(
    *,
    oxygen: float,
    code_version: str = current_code_version(),
    cost_parameters: dict[str, object] | None = None,
):
    def evaluate_patch(
        patch,
        feedstock_id,
        fidelity,
        *,
        profile,
        candidate_id=None,
        schema=None,
        cost_parameters=None,
    ) -> ScoredResult:
        return _scored(
            oxygen=oxygen,
            code_version=code_version,
            candidate_id=candidate_id or "candidate-a",
            cost_parameters=cost_parameters,
        )

    return evaluate_patch


def _sqlite_bytes(
    tmp_path: Path,
    *,
    oxygen: float = 10.0,
    code_version: str = current_code_version(),
    rows: list[dict[str, object]] | None = None,
    cost_parameters: dict[str, object] | None = None,
) -> bytes:
    path = tmp_path / f"cache-{len(list(tmp_path.glob('cache-*.sqlite')))}.sqlite"
    records = rows or [
        {
            "cache_key": "cache-a",
            "candidate_id": "candidate-a",
            "oxygen": oxygen,
            "code_version": code_version,
        }
    ]
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE results (
                cache_key TEXT,
                candidate_id TEXT,
                feedstock_id TEXT,
                profile_id TEXT,
                fidelity TEXT,
                corpus_version TEXT,
                feasible INTEGER,
                objectives TEXT,
                feasibility_margins TEXT,
                eval_spec TEXT
            )
            """
        )
        for record in records:
            row_code_version = str(record.get("code_version") or code_version)
            spec_payload = _eval_spec_payload(
                code_version=row_code_version,
                cost_parameters=(
                    record.get("cost_parameters")
                    if isinstance(record.get("cost_parameters"), dict)
                    else cost_parameters
                ),
            )
            if record.get("conditional_subspace_digest"):
                spec_payload["conditional_subspace_digest"] = str(
                    record["conditional_subspace_digest"]
                )
            conn.execute(
                """
                INSERT INTO results
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("cache_key") or "cache-a"),
                    str(record.get("candidate_id") or "candidate-a"),
                    "lunar_mare_low_ti",
                    "oxygen-yield-v1",
                    "fast",
                    current_corpus_version(),
                    1,
                    json.dumps(
                        [
                            {
                                "metric": "oxygen_kg",
                                "sense": "maximize",
                                "value": float(record.get("oxygen") or oxygen),
                                "units": "kg",
                                "ordinal": 0,
                            }
                        ]
                    ),
                    json.dumps(
                        {
                            "delivered_stream_purity": {
                                "feasible": True,
                                "margin": 0.25,
                                "observed": 0.98,
                            }
                        }
                    ),
                    json.dumps(spec_payload),
                ),
            )
    return path.read_bytes()


def _sqlite_view_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / f"cache-view-{len(list(tmp_path.glob('cache-view-*.sqlite')))}.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE VIEW results AS
            WITH RECURSIVE cnt(x) AS (
                VALUES(1)
                UNION ALL
                SELECT x + 1 FROM cnt WHERE x < 100000000
            )
            SELECT CAST(x AS TEXT) AS cache_key FROM cnt
            """
        )
    return path.read_bytes()


def _leaderboard_bytes(
    *,
    oxygen: float = 10.0,
    patch: dict[str, object] | None = None,
    patch_json: str | None = None,
    rows: list[dict[str, object]] | None = None,
) -> bytes:
    handle = io.StringIO()
    writer = csv.DictWriter(
        handle,
        fieldnames=[
            "rank",
            "candidate_id",
            "cache_key",
            "is_pareto",
            "is_winner",
            "oxygen_kg",
            "patch_json",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    payload_rows = rows or [
        {
            "rank": "1",
            "candidate_id": "candidate-a",
            "cache_key": "cache-a",
            "is_pareto": "true",
            "is_winner": "true",
            "oxygen_kg": str(oxygen),
            "patch_json": json.dumps(patch or {}) if patch_json is None else patch_json,
        }
    ]
    for row in payload_rows:
        writer.writerow(row)
    return handle.getvalue().encode("utf-8")


def _leaderboard_row(
    rank: int,
    candidate_id: str,
    cache_key: str,
    *,
    is_pareto: bool,
    is_winner: bool = False,
    oxygen: float = 10.0,
    patch_json: str | None = None,
) -> dict[str, object]:
    return {
        "rank": str(rank),
        "candidate_id": candidate_id,
        "cache_key": cache_key,
        "is_pareto": "true" if is_pareto else "false",
        "is_winner": "true" if is_winner else "false",
        "oxygen_kg": str(oxygen),
        "patch_json": json.dumps({}) if patch_json is None else patch_json,
    }


def _members(
    tmp_path: Path,
    *,
    study_id: str = "study123",
    oxygen: float = 10.0,
    code_version: str = current_code_version(),
    study_status: str = "completed",
    summary_extra: dict[str, object] | None = None,
    manifest_extra: dict[str, object] | None = None,
    leaderboard_rows: list[dict[str, object]] | None = None,
    sqlite_rows: list[dict[str, object]] | None = None,
    leaderboard_patch: dict[str, object] | None = None,
    leaderboard_patch_json: str | None = None,
    cost_parameters: dict[str, object] | None = None,
) -> dict[str, bytes]:
    schema = _schema()
    summary = {
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        "save_schema_version": SAVE_SCHEMA_VERSION,
        "study_id": study_id,
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "study_status": study_status,
        "created_at": "2026-07-08T00:00:00+00:00",
    }
    summary.update(summary_extra or {})
    manifest = {
        "member_schema_version": MEMBER_SCHEMA_VERSION,
        "save_schema_version": SAVE_SCHEMA_VERSION,
        "study_id": study_id,
        "study_status": study_status,
        "created_at": "2026-07-08T00:00:00+00:00",
        "code_version": code_version,
        "recipe_schema_version": schema.recipe_schema_version,
        "allowlist_version": schema.allowlist_version,
        "bounds_digest": schema.bounds_digest,
        "data_digests": _data_digests(),
        "corpus_version": current_corpus_version(),
        "search_space_identity": {
            "recipe_schema_version": schema.recipe_schema_version,
            "allowlist_version": schema.allowlist_version,
            "bounds_digest": schema.bounds_digest,
            "data_digests": _data_digests(),
            "corpus_version": current_corpus_version(),
        },
    }
    manifest.update(manifest_extra or {})
    return {
        "study.manifest.json": _json_bytes(manifest),
        "study.summary.json": _json_bytes(summary),
        "study.profile.yaml": (
            "profile_id: oxygen-yield-v1\n"
            "feedstock: lunar_mare_low_ti\n"
            "objectives:\n"
            "  - metric: oxygen_kg\n"
            "    sense: maximize\n"
            "    units: kg\n"
            "constraints:\n"
            "  gates:\n"
            "    - delivered_stream_purity\n"
        ).encode("utf-8"),
        "cache.sqlite": _sqlite_bytes(
            tmp_path,
            oxygen=oxygen,
            code_version=code_version,
            rows=sqlite_rows,
            cost_parameters=cost_parameters,
        ),
        "pareto.json": _json_bytes(
            {"member_schema_version": MEMBER_SCHEMA_VERSION, "pareto": []}
        ),
        "leaderboard.csv": _leaderboard_bytes(
            oxygen=oxygen,
            patch=leaderboard_patch,
            patch_json=leaderboard_patch_json,
            rows=leaderboard_rows,
        ),
        "job_status.json": _json_bytes(
            {
                "member_schema_version": MEMBER_SCHEMA_VERSION,
                "status": "SUCCEEDED",
                "success": True,
            }
        ),
    }


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _write_zip(
    path: Path,
    members: dict[str, bytes],
    extra: dict[str, bytes] | None = None,
    *,
    member_schema_version: int = MEMBER_SCHEMA_VERSION,
    index_mutator=None,
) -> Path:
    payload = dict(members)
    if extra:
        payload.update(extra)
    index_members = {
        name: {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "member_schema_version": member_schema_version,
            "producer_code_version": current_code_version(),
        }
        for name, data in payload.items()
        if name != ARTIFACT_INDEX_NAME
    }
    index_payload = {
        "member_schema_version": member_schema_version,
        "members": index_members,
    }
    if index_mutator is not None:
        index_mutator(index_payload)
    payload[ARTIFACT_INDEX_NAME] = _json_bytes(
        index_payload
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
    return path


def _export_bundle(tmp_path: Path, members: dict[str, bytes]) -> Path:
    run_dir = tmp_path / f"run-{len(list(tmp_path.glob('run-*')))}"
    run_dir.mkdir()
    for name, data in members.items():
        (run_dir / name).write_bytes(data)
    return export_study_bundle(run_dir, output_path=tmp_path / f"{run_dir.name}.rpstudy.zip")


def test_import_accepts_current_schema_version_bundle(tmp_path: Path) -> None:
    bundle = _write_zip(tmp_path / "current-version.rpstudy.zip", _members(tmp_path))

    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        verification_tier=0,
        evaluator=_evaluator(oxygen=10.0),
    )

    assert imported.study_id == "study123"
    assert (imported.path / "study.profile.yaml").is_file()


def test_t155_import_preserves_conditional_subspace_identity_in_cache(
    tmp_path: Path,
) -> None:
    digest = "conditional-subspace-digest"
    bundle = _write_zip(
        tmp_path / "conditional-identity.rpstudy.zip",
        _members(
            tmp_path,
            sqlite_rows=[
                {
                    "cache_key": "cache-a",
                    "candidate_id": "candidate-a",
                    "conditional_subspace_digest": digest,
                }
            ],
        ),
    )

    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        verification_tier=0,
        evaluator=_evaluator(oxygen=10.0),
    )
    with sqlite3.connect(imported.path / "cache.sqlite") as conn:
        payload = json.loads(conn.execute("SELECT eval_spec FROM results").fetchone()[0])
    assert payload["conditional_subspace_digest"] == digest


def test_import_rejects_failed_job_status_before_commit(tmp_path: Path) -> None:
    members = _members(tmp_path)
    members["job_status.json"] = _json_bytes(
        {
            "member_schema_version": MEMBER_SCHEMA_VERSION,
            "status": "FAILED",
            "success": False,
        }
    )
    bundle = _write_zip(tmp_path / "failed-job.rpstudy.zip", members)

    with pytest.raises(ImportBundleError, match="job_status.json reports unsuccessful"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )

    assert not (tmp_path / "runs" / IMPORTED_DIR_NAME / "study123").exists()


def test_import_rejects_manifest_summary_study_id_mismatch(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "study-id-mismatch.rpstudy.zip",
        _members(
            tmp_path,
            manifest_extra={"study_id": "manifest-id"},
            summary_extra={"study_id": "summary-id"},
        ),
    )

    with pytest.raises(ImportBundleError, match="study_id mismatch"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_raw_study_id_mismatch_that_sanitizes_equal(tmp_path: Path) -> None:
    # Distinct RAW study_ids that sanitize to the same safe token ("a/b" and "a-b" both ->
    # "a-b") must still be rejected as a mismatch, else provenance lands under the wrong id.
    bundle = _write_zip(
        tmp_path / "sanitized-collision.rpstudy.zip",
        _members(
            tmp_path,
            manifest_extra={"study_id": "a/b"},
            summary_extra={"study_id": "a-b"},
        ),
    )
    with pytest.raises(ImportBundleError, match="study_id mismatch"):
        import_study_bundle(
            bundle, tmp_path / "runs", verification_tier=0, evaluator=_evaluator(oxygen=10.0)
        )


def test_import_rejects_running_job_status_with_success_true(tmp_path: Path) -> None:
    # A non-terminal / failure-like job status (RUNNING/ERROR/…) must be rejected even when
    # `success` was set true — only an affirmative success token is accepted.
    members = _members(tmp_path)
    members["job_status.json"] = _json_bytes(
        {
            "member_schema_version": MEMBER_SCHEMA_VERSION,
            "status": "RUNNING",
            "success": True,
        }
    )
    bundle = _write_zip(tmp_path / "running-job.rpstudy.zip", members)
    with pytest.raises(ImportBundleError, match="job_status.json reports unsuccessful"):
        import_study_bundle(
            bundle, tmp_path / "runs", verification_tier=0, evaluator=_evaluator(oxygen=10.0)
        )


@pytest.mark.parametrize("status", ["SUCCESS", "COMPLETED", "COMPLETE"])
def test_import_rejects_producer_impossible_success_aliases(
    tmp_path: Path,
    status: str,
) -> None:
    members = _members(tmp_path)
    members["job_status.json"] = _json_bytes(
        {
            "member_schema_version": MEMBER_SCHEMA_VERSION,
            "status": status,
            "success": True,
        }
    )
    bundle = _write_zip(tmp_path / f"alias-{status}.rpstudy.zip", members)

    with pytest.raises(ImportBundleError, match="job_status.json reports unsuccessful"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_manifest_summary_study_status_mismatch(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "study-status-mismatch.rpstudy.zip",
        _members(
            tmp_path,
            manifest_extra={"study_status": "aborted"},
            summary_extra={"study_status": "completed"},
        ),
    )

    with pytest.raises(ImportBundleError, match="study_status mismatch"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_invalid_profile_yaml_at_tier0(tmp_path: Path) -> None:
    members = _members(tmp_path)
    members["study.profile.yaml"] = b": invalid: yaml: ["
    bundle = _write_zip(tmp_path / "invalid-profile.rpstudy.zip", members)

    with pytest.raises(ImportBundleError, match="study.profile.yaml is not valid YAML"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_malformed_index_size_bytes_as_import_error(
    tmp_path: Path,
) -> None:
    def null_size(index_payload: dict[str, object]) -> None:
        members = index_payload["members"]
        assert isinstance(members, dict)
        entry = members["study.summary.json"]
        assert isinstance(entry, dict)
        entry["size_bytes"] = None

    bundle = _write_zip(
        tmp_path / "null-size.rpstudy.zip",
        _members(tmp_path),
        index_mutator=null_size,
    )

    with pytest.raises(ImportBundleError, match="malformed size_bytes"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_nonterminal_study_status_before_commit(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "running-status.rpstudy.zip",
        _members(tmp_path, study_status="running"),
    )

    with pytest.raises(ImportBundleError, match="study_status is not terminal"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )

    assert not (tmp_path / "runs" / IMPORTED_DIR_NAME / "study123").exists()


def test_import_rejects_json_member_schema_version_disagreement(
    tmp_path: Path,
) -> None:
    members = _members(tmp_path)
    summary = json.loads(members["study.summary.json"])
    summary["member_schema_version"] = 0
    members["study.summary.json"] = _json_bytes(summary)
    bundle = _write_zip(tmp_path / "member-schema-disagreement.rpstudy.zip", members)

    with pytest.raises(ImportBundleError, match="member_schema_version mismatch"):
        import_study_bundle(
            bundle,
            tmp_path / "runs",
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )


def test_import_rejects_unsupported_member_schema_version_before_member_parse(
    tmp_path: Path,
) -> None:
    # The artifact.index member_schema_version gate runs BEFORE any untrusted member body
    # is JSON-parsed: a corrupt manifest body must NOT be reached when the index already
    # declares an unsupported member schema. (save_schema_version is a manifest/summary
    # field, not an index field — Spec §Integrity — so the surviving pre-parse gate is
    # member_schema_version, validated from the index that is parsed first.)
    members = _members(tmp_path)
    members["study.manifest.json"] = b"{not valid json"
    bundle = _write_zip(
        tmp_path / "bad-member-schema-pre-parse.rpstudy.zip",
        members,
        member_schema_version=999,
    )

    with pytest.raises(
        ImportBundleError,
        match="unsupported member_schema_version",
    ):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_unsupported_manifest_save_schema_version(
    tmp_path: Path,
) -> None:
    bundle = _write_zip(
        tmp_path / "bad-manifest-save-schema.rpstudy.zip",
        _members(tmp_path, manifest_extra={"save_schema_version": 999}),
    )

    with pytest.raises(
        ImportBundleError,
        match="study.manifest.json unsupported save_schema_version",
    ):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_unsupported_member_schema_version(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "bad-member-schema.rpstudy.zip",
        _members(tmp_path),
        member_schema_version=999,
    )

    with pytest.raises(ImportBundleError, match="unsupported member_schema_version"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_unknown_member(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "unknown.rpstudy.zip",
        _members(tmp_path),
        {"job.log": b"nope"},
    )

    with pytest.raises(ImportBundleError, match="unknown bundle member"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_zip_slip_member(tmp_path: Path) -> None:
    bundle = _write_zip(
        tmp_path / "zipslip.rpstudy.zip",
        _members(tmp_path),
        {"../study.summary.json": b"{}"},
    )

    with pytest.raises(ImportBundleError, match="zip-slip"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_oversized_yaml_member(tmp_path: Path) -> None:
    members = _members(tmp_path)
    members["study.profile.yaml"] = b"x" * (YAML_CAP_BYTES + 1)
    bundle = _write_zip(tmp_path / "oversized.rpstudy.zip", members)

    with pytest.raises(ImportBundleError, match="study.profile.yaml exceeds 1MB"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_aggregate_uncompressed_zip_bomb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_bundle_module, "BUNDLE_CAP_BYTES", 10_000)
    bundle = _write_zip(
        tmp_path / "aggregate-bomb.rpstudy.zip",
        _members(tmp_path),
        {"study.events.jsonl": b"\n" * 12_000},
    )
    assert bundle.stat().st_size < import_bundle_module.BUNDLE_CAP_BYTES

    with pytest.raises(ImportBundleError, match="bundle uncompressed size exceeds"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_zip_entry_count_before_zipfile_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _write_zip(tmp_path / "entry-count.rpstudy.zip", _members(tmp_path))
    monkeypatch.setattr(import_bundle_module, "MAX_IMPORTED_ELEMENT_COUNT", 1)

    def fail_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not open over-cap entry-count bundles")

    monkeypatch.setattr(import_bundle_module.zipfile, "ZipFile", fail_zipfile)

    with pytest.raises(ImportBundleError, match="bundle exceeds 1 zip entry cap"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_lied_zip_entry_count_before_zipfile_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _write_zip(tmp_path / "lied-entry-count.rpstudy.zip", _members(tmp_path))
    data = bytearray(bundle.read_bytes())
    eocd_offset = data.rfind(b"PK\x05\x06")
    assert eocd_offset > 0
    data[eocd_offset + 8 : eocd_offset + 10] = (1).to_bytes(2, "little")
    data[eocd_offset + 10 : eocd_offset + 12] = (1).to_bytes(2, "little")
    bundle.write_bytes(data)
    monkeypatch.setattr(import_bundle_module, "MAX_IMPORTED_ELEMENT_COUNT", 1)

    def fail_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile must not open lied entry-count bundles")

    monkeypatch.setattr(import_bundle_module.zipfile, "ZipFile", fail_zipfile)

    with pytest.raises(ImportBundleError, match="bundle exceeds 1 zip entry cap"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_duplicate_zip_member_without_count_scan(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "duplicate.rpstudy.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in _members(tmp_path).items():
            archive.writestr(name, data)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("study.summary.json", b"{}")

    source = Path(import_bundle_module.__file__).read_text(encoding="utf-8")
    assert ".count(" not in source

    with pytest.raises(ImportBundleError, match="duplicate bundle member"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_zip_member_extraction_counts_actual_inflated_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_bundle_module, "ZIP_READ_CHUNK_BYTES", 4)
    payload = b"x" * 15
    read_sizes: list[int] = []

    class FakeHandle:
        def __init__(self, data: bytes):
            self._data = data
            self._offset = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            if self._offset >= len(self._data):
                return b""
            end = min(self._offset + size, len(self._data))
            chunk = self._data[self._offset:end]
            self._offset = end
            return chunk

    class FakeArchive:
        def open(self, info: zipfile.ZipInfo, mode: str):
            assert info.file_size == 10
            assert mode == "r"
            return FakeHandle(payload)

    info = zipfile.ZipInfo("cache.sqlite")
    info.file_size = 10
    target = tmp_path / "cache.sqlite"

    with pytest.raises(ImportBundleError, match="cache.sqlite exceeds 10 byte cap"):
        import_bundle_module._extract_zip_member_bounded(
            FakeArchive(),
            info,
            target,
            member_cap=10,
            bundle_cap=100,
        )

    assert max(read_sizes) <= 4
    assert target.stat().st_size <= 10


def test_open_untrusted_result_db_is_query_only_and_blocks_attach(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    db_path.write_bytes(_sqlite_bytes(tmp_path))

    conn = open_untrusted_result_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("CREATE TABLE blocked (id INTEGER)")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE ':memory:' AS other")
    finally:
        conn.close()


def test_open_untrusted_result_db_bounds_first_preflight_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    db_path.write_bytes(b"placeholder")
    events: list[str] = []

    class FakeCursor:
        def __init__(self, value: int | None = None):
            self._value = value

        def fetchone(self):
            return (self._value,)

    class FakeConnection:
        row_factory = None

        def set_progress_handler(self, callback, step: int) -> None:
            events.append("progress")

        def set_authorizer(self, callback) -> None:
            events.append("authorizer")

        def enable_load_extension(self, enabled: bool) -> None:
            events.append("load-extension")

        def execute(self, sql: str):
            events.append(f"execute:{sql}")
            if sql == "PRAGMA page_size":
                return FakeCursor(4096)
            if sql == "PRAGMA page_count":
                return FakeCursor(1)
            return FakeCursor()

        def close(self) -> None:
            events.append("close")

    def fake_connect(database: str, *, uri: bool = False):
        events.append("connect")
        return FakeConnection()

    monkeypatch.setattr(import_bundle_module.sqlite3, "connect", fake_connect)

    open_untrusted_result_db(db_path)

    first_execute = next(
        index for index, event in enumerate(events) if event.startswith("execute:")
    )
    assert events.index("progress") < first_execute
    assert events.index("authorizer") < first_execute


def test_imported_rows_installs_limit_before_schema_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cache.sqlite"
    db_path.write_bytes(_sqlite_bytes(tmp_path))
    events: list[str] = []
    original_install = import_bundle_module._install_progress_limit
    original_require = import_bundle_module._require_results_table

    def spy_install(conn: sqlite3.Connection) -> None:
        events.append("install")
        original_install(conn)

    def spy_require(conn: sqlite3.Connection) -> None:
        events.append("require")
        assert events[-2:] == ["install", "require"]
        original_require(conn)

    monkeypatch.setattr(import_bundle_module, "_install_progress_limit", spy_install)
    monkeypatch.setattr(import_bundle_module, "_require_results_table", spy_require)

    rows = import_bundle_module._imported_rows_by_cache_key(db_path)

    assert rows["cache-a"]["candidate_id"] == "candidate-a"


def test_import_rejects_results_view_before_untrusted_query(tmp_path: Path) -> None:
    members = _members(tmp_path)
    members["cache.sqlite"] = _sqlite_view_bytes(tmp_path)
    bundle = _export_bundle(tmp_path, members)

    with pytest.raises(ImportBundleError, match="results must be a table"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_results_table_over_row_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_bundle_module, "SQLITE_RESULTS_MAX_ROWS", 1)
    members = _members(
        tmp_path,
        sqlite_rows=[
            {"cache_key": "cache-a", "candidate_id": "candidate-a", "oxygen": 10.0},
            {"cache_key": "cache-b", "candidate_id": "candidate-b", "oxygen": 10.0},
        ],
        leaderboard_rows=[
            _leaderboard_row(1, "candidate-a", "cache-a", is_pareto=True, is_winner=True),
            _leaderboard_row(2, "candidate-b", "cache-b", is_pareto=True),
        ],
    )
    bundle = _export_bundle(tmp_path, members)

    with pytest.raises(ImportBundleError, match="results exceeds 1 row cap"):
        import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))


def test_import_rejects_over_cap_leaderboard_without_committing(tmp_path: Path) -> None:
    study_id = "overcap-leaderboard"
    rows = [
        _leaderboard_row(
            rank,
            f"candidate-{rank}",
            f"cache-{rank}",
            is_pareto=False,
        )
        for rank in range(1, import_bundle_module.MAX_IMPORTED_ELEMENT_COUNT + 2)
    ]
    rows[0] = _leaderboard_row(
        1,
        "candidate-a",
        "cache-a",
        is_pareto=True,
        is_winner=True,
    )
    bundle = _export_bundle(
        tmp_path,
        _members(tmp_path, study_id=study_id, leaderboard_rows=rows),
    )
    runs_root = tmp_path / "runs"

    with pytest.raises(ImportBundleError, match="leaderboard.csv exceeds 10000 row cap"):
        import_study_bundle(bundle, runs_root, evaluator=_evaluator(oxygen=10.0))

    assert not (runs_root / IMPORTED_DIR_NAME / study_id).exists()


def test_over_cap_import_rejected_even_when_quarantine_dir_preexists(
    tmp_path: Path,
) -> None:
    # Regression: safety caps must run BEFORE the dedupe early-return, so an
    # over-cap bundle whose study_id already collides with a quarantine dir is
    # still rejected (a dedupe short-circuit must not skip the row/count caps).
    study_id = "overcap-dedupe"
    rows = [
        _leaderboard_row(rank, f"candidate-{rank}", f"cache-{rank}", is_pareto=False)
        for rank in range(1, import_bundle_module.MAX_IMPORTED_ELEMENT_COUNT + 2)
    ]
    rows[0] = _leaderboard_row(
        1, "candidate-a", "cache-a", is_pareto=True, is_winner=True
    )
    bundle = _export_bundle(
        tmp_path, _members(tmp_path, study_id=study_id, leaderboard_rows=rows)
    )
    runs_root = tmp_path / "runs"
    # Pre-create a colliding quarantine dir so the dedupe/collision path is live.
    preexisting = runs_root / IMPORTED_DIR_NAME / study_id
    preexisting.mkdir(parents=True)
    (preexisting / "sentinel").write_text("prior", encoding="utf-8")

    with pytest.raises(ImportBundleError, match="leaderboard.csv exceeds 10000 row cap"):
        import_study_bundle(bundle, runs_root, evaluator=_evaluator(oxygen=10.0))

    # The pre-existing quarantine is untouched; no over-cap content was committed.
    assert (preexisting / "sentinel").read_text(encoding="utf-8") == "prior"


def test_tier0_import_rejects_over_cap_sqlite_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study_id = "tier0-overcap-sqlite"
    monkeypatch.setattr(import_bundle_module, "SQLITE_RESULTS_MAX_ROWS", 1)
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id=study_id,
            sqlite_rows=[
                {"cache_key": "cache-a", "candidate_id": "candidate-a", "oxygen": 10.0},
                {"cache_key": "cache-b", "candidate_id": "candidate-b", "oxygen": 10.0},
            ],
        ),
    )
    runs_root = tmp_path / "runs"

    with pytest.raises(ImportBundleError, match="cache.sqlite results exceeds 1 row cap"):
        import_study_bundle(
            bundle,
            runs_root,
            verification_tier=0,
            evaluator=_evaluator(oxygen=10.0),
        )

    assert not (runs_root / IMPORTED_DIR_NAME / study_id).exists()


def test_read_leaderboard_rejects_csv_row_count_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "leaderboard.csv"
    path.write_text("candidate_id\na\nb\n", encoding="utf-8")
    monkeypatch.setattr(import_bundle_module, "MAX_IMPORTED_ELEMENT_COUNT", 1)

    with pytest.raises(ImportBundleError, match="leaderboard.csv exceeds 1 row cap"):
        import_bundle_module._read_leaderboard(path)


def test_validate_jsonl_rejects_row_count_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_bundle_module, "MAX_IMPORTED_ELEMENT_COUNT", 1)

    with pytest.raises(ImportBundleError, match="study.events.jsonl exceeds 1 row cap"):
        import_bundle_module._validate_jsonl_member("study.events.jsonl", b"{}\n{}\n")


def test_parse_json_rejects_array_element_count_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_bundle_module, "MAX_IMPORTED_ELEMENT_COUNT", 1)

    with pytest.raises(ImportBundleError, match="pareto.json JSON array exceeds"):
        import_bundle_module._parse_json_object("pareto.json", b'{"pareto":[{},{}]}')


def test_import_quarantine_is_excluded_from_native_run_index(client, tmp_path: Path) -> None:
    runs_root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    bundle = _export_bundle(tmp_path, _members(tmp_path))

    imported = import_study_bundle(bundle, runs_root, evaluator=_evaluator(oxygen=10.0))

    assert imported.path == runs_root / IMPORTED_DIR_NAME / "study123"
    native_payload = client.get("/api/optimizer/runs").get_json()
    assert native_payload["runs"] == []
    imported_payload = client.get("/api/optimizer/imported").get_json()
    assert [row["study_id"] for row in imported_payload["imported"]] == ["study123"]


def test_import_overlay_badges_are_authoritative_over_summary_claims(tmp_path: Path) -> None:
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            summary_extra={
                "badges": {"ux_label": "CERTIFIED"},
                "verification": {"candidates": [{"verdict": "confirmed"}]},
            },
        ),
    )

    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        verification_tier=0,
        evaluator=_evaluator(oxygen=10.0),
    )
    model = imported_study_model(imported.path, tmp_path / "runs")

    assert model["summary"]["badges"]["ux_label"] == "CERTIFIED"
    assert model["badges"]["origin"] == "imported"
    assert model["badges"]["ux_label"] == "UNVERIFIED"


def test_tier1_verifies_winner_and_all_pareto_candidates(tmp_path: Path) -> None:
    rows = [
        _leaderboard_row(1, "candidate-a", "cache-a", is_pareto=True, is_winner=True),
        _leaderboard_row(2, "candidate-b", "cache-b", is_pareto=True),
        _leaderboard_row(3, "candidate-c", "cache-c", is_pareto=True),
        _leaderboard_row(4, "candidate-d", "cache-d", is_pareto=False),
    ]
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id="multi-pareto",
            leaderboard_rows=rows,
            sqlite_rows=[
                {"cache_key": "cache-a", "candidate_id": "candidate-a", "oxygen": 10.0},
                {"cache_key": "cache-b", "candidate_id": "candidate-b", "oxygen": 10.0},
                {"cache_key": "cache-c", "candidate_id": "candidate-c", "oxygen": 10.0},
                {"cache_key": "cache-d", "candidate_id": "candidate-d", "oxygen": 10.0},
            ],
        ),
    )

    imported = import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))
    verification = imported_study_model(imported.path, tmp_path / "runs")["verification"]

    assert verification["coverage"]["verified_count"] == 3
    assert [row["candidate_id"] for row in verification["candidates"]] == [
        "candidate-a",
        "candidate-b",
        "candidate-c",
    ]


def test_winner_recipe_yaml_fallback_is_used_for_missing_leaderboard_patch(
    tmp_path: Path,
) -> None:
    cost_parameters = default_cost_parameters_block()
    cost_parameters["parameters"]["electricity_cost_per_kWh"]["value"] = 0.42
    observed_cost_parameters: list[dict[str, object]] = []

    def evaluator(
        patch,
        feedstock_id,
        fidelity,
        *,
        profile,
        candidate_id=None,
        schema=None,
        cost_parameters=None,
    ) -> ScoredResult:
        observed_cost_parameters.append(cost_parameters)
        return _scored(
            oxygen=10.0,
            candidate_id=candidate_id or "candidate-a",
            cost_parameters=cost_parameters,
        )

    bundle = _export_bundle(
        tmp_path,
        {
            **_members(
                tmp_path,
                study_id="winner-fallback",
                leaderboard_patch_json="",
                cost_parameters=cost_parameters,
            ),
            "winner.recipe.yaml": yaml.safe_dump(
                {
                    "cost_parameters": cost_parameters,
                    "furnace_max_T_C": 1500.0,
                },
                sort_keys=False,
            ).encode("utf-8"),
        },
    )

    imported = import_study_bundle(bundle, tmp_path / "runs", evaluator=evaluator)
    verification = imported_study_model(imported.path, tmp_path / "runs")["verification"]

    assert verification["candidates"][0]["verdict"] == "confirmed"
    assert observed_cost_parameters[0]["parameters"]["electricity_cost_per_kWh"][
        "value"
    ] == pytest.approx(0.42)


def test_import_identity_detects_cost_parameter_drift() -> None:
    local_costs = default_cost_parameters_block()
    local_costs["parameters"]["electricity_cost_per_kWh"]["value"] = 0.42
    local = import_bundle_module._local_claim(
        _scored(oxygen=10.0, cost_parameters=local_costs)
    )["identity"]
    imported = copy.deepcopy(local)
    imported["cost_parameters"] = default_cost_parameters_block()

    same, moved = import_bundle_module._same_identity_epoch(local, imported)

    assert same is False
    assert moved == ["cost_parameters"]


def test_bundle_verification_detects_winner_recipe_cost_drift(tmp_path: Path) -> None:
    recipe_costs = default_cost_parameters_block()
    recipe_costs["parameters"]["electricity_cost_per_kWh"]["value"] = 0.42
    bundle = _export_bundle(
        tmp_path,
        {
            **_members(tmp_path, study_id="cost-drift"),
            "winner.recipe.yaml": yaml.safe_dump(
                {
                    "cost_parameters": recipe_costs,
                    "furnace_max_T_C": 1500.0,
                },
                sort_keys=False,
            ).encode("utf-8"),
        },
    )

    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=10.0),
    )
    candidate = imported_study_model(imported.path, tmp_path / "runs")[
        "verification"
    ]["candidates"][0]

    assert candidate["verdict"] == "unchanged"
    assert candidate["reason_codes"] == ["cost_parameters"]


def test_not_reevaluable_vocabulary_drift_uses_locked_reason_code(tmp_path: Path) -> None:
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id="vocabulary-drift",
            leaderboard_patch={"retired_knob": 1.0},
        ),
    )

    imported = import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))
    candidate = imported_study_model(imported.path, tmp_path / "runs")["verification"]["candidates"][0]

    assert candidate["verdict"] == "not-re-evaluable"
    assert candidate["reason_codes"] == ["knob-vocabulary-changed"]


def test_malformed_patch_json_uses_locked_reason_code(tmp_path: Path) -> None:
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id="malformed-patch",
            leaderboard_patch_json="{not-json",
        ),
    )

    imported = import_study_bundle(bundle, tmp_path / "runs", evaluator=_evaluator(oxygen=10.0))
    candidate = imported_study_model(imported.path, tmp_path / "runs")["verification"]["candidates"][0]

    assert candidate["verdict"] == "not-re-evaluable"
    assert candidate["reason_codes"] == ["knob-vocabulary-changed"]


def test_aborted_no_winner_verifies_top_n_rows(tmp_path: Path) -> None:
    rows = [
        _leaderboard_row(1, "candidate-a", "cache-a", is_pareto=False),
        _leaderboard_row(2, "candidate-b", "cache-b", is_pareto=False),
        _leaderboard_row(3, "candidate-c", "cache-c", is_pareto=False),
    ]
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id="aborted-top-n",
            study_status="aborted",
            leaderboard_rows=rows,
            sqlite_rows=[
                {"cache_key": "cache-a", "candidate_id": "candidate-a", "oxygen": 10.0},
                {"cache_key": "cache-b", "candidate_id": "candidate-b", "oxygen": 10.0},
                {"cache_key": "cache-c", "candidate_id": "candidate-c", "oxygen": 10.0},
            ],
        ),
    )

    imported = import_study_bundle(
        bundle,
        tmp_path / "runs",
        verification_top_n=2,
        evaluator=_evaluator(oxygen=10.0),
    )
    verification = imported_study_model(imported.path, tmp_path / "runs")["verification"]

    assert verification["coverage"]["verified_count"] == 2
    assert [row["candidate_id"] for row in verification["candidates"]] == [
        "candidate-a",
        "candidate-b",
    ]


def test_tier0_verification_records_hash_check_without_candidates(tmp_path: Path) -> None:
    imported = import_study_bundle(
        _export_bundle(tmp_path, _members(tmp_path, study_id="tier0")),
        tmp_path / "runs",
        verification_tier=0,
        evaluator=_evaluator(oxygen=10.0),
    )

    model = imported_study_model(imported.path, tmp_path / "runs")

    assert model["verification"]["hash_check"]["verdict"] == "confirmed"
    assert model["verification"]["candidates"] == []
    assert model["badges"]["ux_label"] == "UNVERIFIED"


def test_overlay_badge_upgrades_to_confirmed_after_tier1(tmp_path: Path) -> None:
    imported = import_study_bundle(
        _export_bundle(tmp_path, _members(tmp_path, study_id="badge-confirmed")),
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=10.0),
    )

    model = imported_study_model(imported.path, tmp_path / "runs")

    assert model["badges"]["ux_label"] == "CONFIRMED"


def test_tier1_verification_reports_confirmed_disputed_and_drifted(tmp_path: Path) -> None:
    confirmed = import_study_bundle(
        _export_bundle(tmp_path, _members(tmp_path, study_id="confirmed")),
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=10.0),
    )
    confirmed_report = imported_study_model(confirmed.path, tmp_path / "runs")["verification"]
    assert confirmed_report["candidates"][0]["verdict"] == "confirmed"

    disputed = import_study_bundle(
        _export_bundle(tmp_path, _members(tmp_path, study_id="disputed")),
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=11.0),
    )
    disputed_report = imported_study_model(disputed.path, tmp_path / "runs")["verification"]
    assert disputed_report["candidates"][0]["verdict"] == "disputed"
    assert "objective-mismatch" in disputed_report["candidates"][0]["reason_codes"]

    drifted = import_study_bundle(
        _export_bundle(
            tmp_path,
            _members(tmp_path, study_id="drifted", code_version="old-code-version"),
        ),
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=11.0, code_version=current_code_version()),
    )
    drifted_report = imported_study_model(drifted.path, tmp_path / "runs")["verification"]
    assert drifted_report["candidates"][0]["verdict"] == "drifted"
    assert "code_version" in drifted_report["candidates"][0]["reason_codes"]

    unchanged = import_study_bundle(
        _export_bundle(
            tmp_path,
            _members(tmp_path, study_id="unchanged", code_version="old-code-version"),
        ),
        tmp_path / "runs",
        evaluator=_evaluator(oxygen=10.0, code_version=current_code_version()),
    )
    unchanged_report = imported_study_model(unchanged.path, tmp_path / "runs")["verification"]
    assert unchanged_report["candidates"][0]["verdict"] == "unchanged"
    assert "code_version" in unchanged_report["candidates"][0]["reason_codes"]


def test_imported_detail_template_autoescapes_summary_content(client, tmp_path: Path) -> None:
    runs_root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    bundle = _export_bundle(
        tmp_path,
        _members(
            tmp_path,
            study_id="escaped",
            summary_extra={"feedstock_id": "<script>alert(1)</script>"},
        ),
    )
    import_study_bundle(bundle, runs_root, evaluator=_evaluator(oxygen=10.0))

    response = client.get("/optimizer/imported/escaped")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_imported_detail_view_renders_overlay_verification_candidates(
    client,
    tmp_path: Path,
) -> None:
    runs_root = Path(client.application.config["OPTIMIZER_RUNS_DIR"])
    bundle = _export_bundle(tmp_path, _members(tmp_path, study_id="overlay-view"))
    import_study_bundle(bundle, runs_root, evaluator=_evaluator(oxygen=11.0))

    response = client.get("/optimizer/imported/overlay-view")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "candidate-a" in html
    assert "disputed" in html
    assert "objective-mismatch" in html
