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
    key = {
        "schema_version": "test",
        "artifact": "equilibrium_post_record",
        "engine_version": legacy_version,
        "backend": {
            "backend_name": "AlphaMELTSBackend",
            "backend_class": "simulator.melt_backend.alphamelts.AlphaMELTSBackend",
            "backend_version": legacy_version,
        },
        "provider": {
            "engine_version": legacy_version,
        },
    }
    key_bytes = canonical_json_bytes(key)
    key_hash = __import__("hashlib").sha256(key_bytes).hexdigest()

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
                b"{}",
                "code-v1",
                legacy_version,
                "{}",
                "now",
                0,
            ),
        )

    before, updated = rekey_cache(db_path, engine="alphamelts")
    assert before == 1
    assert updated == 1
    expected = cache_version_for("alphamelts")
    assert expected is not None

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT engine_version, key_bytes FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()
    assert row[0] == expected
    reloaded = json.loads(row[1].decode("utf-8"))
    assert reloaded["engine_version"] == expected
    assert reloaded["backend"]["backend_version"] == expected

    before2, updated2 = rekey_cache(db_path, engine="alphamelts")
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