from __future__ import annotations

import json
import sqlite3
import warnings
from pathlib import Path

import pytest

from simulator.engine_local_config import (
    EngineIdentity,
    EngineLocalConfig,
    EnginePaths,
    cache_version_for,
    config_path,
    is_legacy_cache_version,
    load_config,
    render_toml,
    setup_thermoengine_dylib_path,
    warn_legacy_once,
    write_config,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.reduced_real_determinism import (
    PT1_EQUILIBRIUM_TABLE,
    canonical_json_bytes,
)
from scripts.rekey_cache_engine_identity import rekey_cache


def _sample_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineLocalConfig:
    dylib_dir = tmp_path / "lib"
    dylib_dir.mkdir()
    for name in ("libphaseobjc.dylib", "libswimdew.dylib", "libspeciation.dylib"):
        (dylib_dir / name).write_bytes(b"dylib-" + name.encode())

    alpha_bin = tmp_path / "alphamelts2"
    alpha_bin.write_bytes(b"fake-alphamelts-binary")
    magemin_bin = tmp_path / "MAGEMin"
    magemin_bin.write_bytes(b"fake-magemin-binary")

    config = EngineLocalConfig(
        paths=EnginePaths(
            thermoengine_dylib_dir=dylib_dir,
            alphamelts_binary_path=alpha_bin,
            magemin_binary_path=magemin_bin,
        ),
        identities={
            "alphamelts": EngineIdentity(
                name="alphamelts",
                version="alphaMELTS 9.9.9",
                digest="sha256:abc111",
            ),
            "magemin": EngineIdentity(
                name="magemin",
                version="MAGEMin 1.2.3",
                digest="sha256:def222",
                extra={"db": "ig"},
            ),
            "thermoengine": EngineIdentity(
                name="thermoengine",
                version="thermoengine MELTS 1.0.2 (liq_mod v1.0)",
                digest="sha256:ghi333",
            ),
        },
    )
    monkeypatch.setattr(
        "simulator.engine_local_config.config_path",
        lambda: tmp_path / "engines.local.toml",
    )
    write_config(config)
    return config


def test_config_write_and_read_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = _sample_config(tmp_path, monkeypatch)
    loaded = load_config(required=True)
    assert loaded is not None
    assert loaded.paths.alphamelts_binary_path == written.paths.alphamelts_binary_path
    assert loaded.identities["alphamelts"].cache_version() == (
        "alphaMELTS 9.9.9 (digest=sha256:abc111)"
    )
    assert "thermoengine_dylib_dir" in render_toml(written)


def test_identity_derived_from_config_for_alphamelts_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sample_config(tmp_path, monkeypatch)
    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._binary_path = tmp_path / "alphamelts2"
    assert backend.get_engine_version() == "alphaMELTS 9.9.9 (digest=sha256:abc111)"


def test_config_absent_legacy_fallback_emits_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr(
        "simulator.engine_local_config.config_path",
        lambda: missing,
    )
    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._binary_path = tmp_path / "alphamelts2"
    backend._engine_path = backend._binary_path

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        version = backend.get_engine_version()

    assert version == f"alphaMELTS subprocess ({backend._binary_path})"
    assert any("legacy alphaMELTS path-based identity" in str(item.message) for item in caught)


def test_warn_legacy_once_is_single_shot() -> None:
    from simulator import engine_local_config as module

    module._LEGACY_WARNED.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_legacy_once("alphamelts", "legacy once")
        warn_legacy_once("alphamelts", "legacy once")
    assert len(caught) == 1


def test_thermoengine_dylib_dir_fail_loud_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_dir = tmp_path / "empty-lib"
    empty_dir.mkdir()
    config = EngineLocalConfig(
        paths=EnginePaths(thermoengine_dylib_dir=empty_dir),
        identities={},
    )
    monkeypatch.setattr(
        "simulator.engine_local_config.config_path",
        lambda: tmp_path / "engines.local.toml",
    )
    write_config(config)

    with pytest.raises(ImportError, match="ThermoEngine dylibs missing"):
        setup_thermoengine_dylib_path()


def test_thermoengine_dylib_dir_sets_dyld_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dylib_dir = tmp_path / "lib"
    dylib_dir.mkdir()
    for name in ("libphaseobjc.dylib", "libswimdew.dylib", "libspeciation.dylib"):
        (dylib_dir / name).write_bytes(b"x")

    config = EngineLocalConfig(
        paths=EnginePaths(thermoengine_dylib_dir=dylib_dir),
        identities={},
    )
    monkeypatch.setattr(
        "simulator.engine_local_config.config_path",
        lambda: tmp_path / "engines.local.toml",
    )
    write_config(config)
    monkeypatch.delenv("DYLD_FALLBACK_LIBRARY_PATH", raising=False)

    resolved = setup_thermoengine_dylib_path()
    assert resolved == dylib_dir
    assert str(dylib_dir) in __import__("os").environ["DYLD_FALLBACK_LIBRARY_PATH"]


def test_is_legacy_cache_version_detects_path_identity() -> None:
    legacy = "alphaMELTS subprocess (/Users/me/alphamelts2)"
    modern = "alphaMELTS 2.3.1 (digest=sha256:deadbeef)"
    assert is_legacy_cache_version(legacy) is True
    assert is_legacy_cache_version(modern) is False


def test_rekey_migration_round_trip_and_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sample_config(tmp_path, monkeypatch)
    db_path = tmp_path / "cache.sqlite"
    legacy_version = "alphaMELTS subprocess (/Users/me/alphamelts2)"
    target = "analytical-test-v1"
    key = {
        "schema_version": "test",
        "artifact": "equilibrium_post_record",
        "engine_version": legacy_version,
        "code_version": "legacy-app-version",
        "source_module_digest": {"module_set_id": "old"},
        "backend": {
            "backend_name": "AlphaMELTSBackend",
            "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
            "backend_version": legacy_version,
        },
        "provider": {
            "engine_version": legacy_version,
        },
    }
    other_key = {
        **key,
        "engine_version": "vaporock 0.1.0 (/tmp/vaporock)",
        "backend": {
            "backend_name": "VapoRockBackend",
            "backend_class": "engines.vaporock.backend.VapoRockBackend",
            "backend_version": "vaporock 0.1.0 (/tmp/vaporock)",
        },
        "provider": {
            "engine_version": "vaporock 0.1.0 (/tmp/vaporock)",
        },
    }
    key_bytes = canonical_json_bytes(key)
    key_hash = __import__("hashlib").sha256(key_bytes).hexdigest()
    other_key_bytes = canonical_json_bytes(other_key)
    other_key_hash = __import__("hashlib").sha256(other_key_bytes).hexdigest()
    payload_bytes = b'{"payload": true}'

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE {PT1_EQUILIBRIUM_TABLE} (
                key_hash TEXT PRIMARY KEY,
                artifact TEXT NOT NULL,
                store_schema_version TEXT NOT NULL,
                request_schema_version TEXT NOT NULL,
                key_sha256 TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                key_bytes BLOB NOT NULL,
                payload_bytes BLOB NOT NULL,
                code_version TEXT NOT NULL,
                engine_version TEXT,
                data_digests_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                git_dirty INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                key_hash, artifact, store_schema_version,
                request_schema_version, key_sha256, payload_sha256,
                key_bytes, payload_bytes, code_version, engine_version,
                data_digests_json, created_at, git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_hash,
                "equilibrium_post_record",
                "pt1",
                "req",
                key_hash,
                "payload",
                key_bytes,
                payload_bytes,
                "code-v1",
                legacy_version,
                "{}",
                "now",
                0,
            ),
        )
        conn.execute(
            f"""
            INSERT INTO {PT1_EQUILIBRIUM_TABLE} (
                key_hash, artifact, store_schema_version,
                request_schema_version, key_sha256, payload_sha256,
                key_bytes, payload_bytes, code_version, engine_version,
                data_digests_json, created_at, git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                other_key_hash,
                "equilibrium_post_record",
                "pt1",
                "req",
                other_key_hash,
                "payload-2",
                other_key_bytes,
                b'{"other": true}',
                "code-v1",
                other_key["engine_version"],
                "{}",
                "now",
                0,
            ),
        )

    before_dry_run_bytes = db_path.read_bytes()
    dry_run = rekey_cache(
        db_path,
        engine="alphamelts",
        target_corpus_version=target,
        dry_run=True,
    )
    assert dry_run.rows_before == 1
    assert dry_run.rows_updated == 0
    assert dry_run.backup_path is None
    assert db_path.read_bytes() == before_dry_run_bytes

    with sqlite3.connect(db_path) as conn:
        assert "corpus_version" not in {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({PT1_EQUILIBRIUM_TABLE})")
        }
        unchanged = conn.execute(
            f"SELECT key_bytes FROM {PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    assert json.loads(unchanged[0].decode("utf-8"))["engine_version"] == legacy_version

    result = rekey_cache(
        db_path,
        engine="alphamelts",
        target_corpus_version=target,
    )
    before, updated = result
    assert before == 1
    assert updated == 1
    assert result.backup_path is not None
    assert result.backup_path.is_file()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT corpus_version, engine_version, key_bytes, payload_bytes "
            f"FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchall()
    rekeyed = [
        row for row in rows
        if json.loads(row[2].decode("utf-8"))["backend"]["backend_name"]
        == "AlphaMELTSBackend"
    ][0]
    assert rekeyed[0] == target
    assert rekeyed[1] == legacy_version
    assert rekeyed[3] == payload_bytes
    reloaded = json.loads(rekeyed[2].decode("utf-8"))
    assert reloaded["corpus_version"] == target
    assert "engine_version" not in reloaded
    assert "code_version" not in reloaded
    assert "source_module_digest" not in reloaded
    assert reloaded["backend"]["corpus_version"] == target
    assert "backend_version" not in reloaded["backend"]
    assert "engine_version" not in reloaded["provider"]

    untouched = [
        row for row in rows
        if json.loads(row[2].decode("utf-8"))["backend"]["backend_name"]
        == "VapoRockBackend"
    ][0]
    untouched_key = json.loads(untouched[2].decode("utf-8"))
    assert untouched_key["engine_version"] == other_key["engine_version"]
    assert "corpus_version" not in untouched_key

    before2, updated2 = rekey_cache(
        db_path,
        engine="alphamelts",
        target_corpus_version=target,
    )
    assert before2 == 0
    assert updated2 == 0


def test_load_config_returns_none_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.engine_local_config.config_path",
        lambda: tmp_path / "missing.toml",
    )
    assert load_config() is None
    with pytest.raises(FileNotFoundError):
        load_config(required=True)
