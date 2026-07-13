import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.reduced_real_determinism as rrd
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.corpus_version import current_corpus_version


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "populate_reduced_real_cache.py"


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "populate_reduced_real_cache",
        SCRIPT_PATH,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pt1_key(label: str, *, temperature_K: float = 1500.0) -> dict:
    return {
        "schema_version": "test",
        "code_version": "test",
        "corpus_version": current_corpus_version(),
        "artifact": "equilibrium_result",
        "intent": "EQUILIBRIUM",
        "data_digests": {},
        "provider": {
            "resolved_provider_id": "magemin-shadow",
            "resolved_role": "silicate_liquidus",
        },
        "vapor_pressure_provider": {
            "resolved_provider_id": "builtin-vapor-pressure",
            "resolved_role": "authoritative",
            "authoritative_provider_id": "builtin-vapor-pressure",
            "fallback_provider_id": None,
            "fallback_allowed": False,
        },
        "controls": {
            "T_K": temperature_K,
            "pressure_bar": 0.01,
            "pO2_bar": 1.0e-6,
        },
        "composition_mol_fraction": [["SiO2", 0.45], ["FeO", 0.10]],
        "suffix": label,
    }


def _pt1_payload(label: str) -> dict:
    return {
        "suffix": label,
        "last_vapor_pressures_source": {"Na": "builtin_authoritative"},
    }


def _write_pt1_row(db_path: Path, key: dict, payload: dict | None = None) -> str:
    payload = payload or _pt1_payload(str(key["suffix"]))
    key_bytes = _canonical_bytes(key)
    payload_bytes = _canonical_bytes(payload)
    key_hash = hashlib.sha256(key_bytes).hexdigest()
    rrd.PT1PersistentEquilibriumStore(db_path).put(
        artifact=str(key["artifact"]),
        key=key,
        key_bytes=key_bytes,
        key_hash=key_hash,
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )
    return key_hash


def test_profile_recipe_defaults_use_seed_campaigns_and_run_additives():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )

    assert driver._profile_campaigns(profile) == ("C0", "C2B")
    assert driver._profile_additives(profile) == {"C": 30.0}


def test_cli_additive_overrides_profile_additive():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )
    args = driver._parse_args(
        [
            "--profile",
            "data/optimize_profiles/mars_basalt.yaml",
            "--additive",
            "C=31.5",
            "--additive",
            "Na=2.0",
        ]
    )

    additives = driver._feedstock_additives(
        "mars_basalt",
        loaded_profile=profile,
        cli_additives=driver._cli_additives(args.additives),
    )

    assert additives == {"C": 31.5, "Na": 2.0}


def test_other_feedstock_uses_own_profile_additives_not_loaded_profile():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )

    assert (
        driver._feedstock_additives(
            "mars_basalt",
            loaded_profile=profile,
            cli_additives={},
        )
        == driver._profile_additives(profile)
        == {"C": 30.0}
    )
    assert (
        driver._feedstock_additives(
            "lunar_mare_low_ti",
            loaded_profile=profile,
            cli_additives={},
        )
        == {}
    )


def test_start_session_passes_profile_additives_to_session_config(tmp_path, monkeypatch):
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )
    captured_configs = []
    configured_stores = []

    class FakeSession:
        def __init__(self):
            self.simulator = SimpleNamespace(
                configure_pt0_determinism_store=configured_stores.append,
                backend=SimpleNamespace(),
            )

        def start(self, config):
            captured_configs.append(config)
            return self

    monkeypatch.setattr(driver, "SimSession", FakeSession)
    monkeypatch.setattr(
        driver,
        "load_config_bundle",
        lambda: SimpleNamespace(feedstocks={}, setpoints={}, vapor_pressures={}),
    )
    store = driver.PT0DeterminismStore(
        "capture",
        db_path=tmp_path / "cache.db",
    )

    driver._start_session(
        feedstock="mars_basalt",
        campaign="C0",
        backend_name="alphamelts",
        mass_kg=1000.0,
        additives_kg=driver._profile_additives(profile),
        store=store,
        allow_internal_analytical_equilibrium=False,
    )

    assert captured_configs
    assert captured_configs[0].additives_kg == {"C": 30.0}
    assert configured_stores == [store]


@pytest.mark.parametrize("backend_name", ["stub", "internal-analytical"])
def test_start_session_refuses_analytical_backend_aliases_before_start(
    backend_name, monkeypatch
):
    driver = _load_driver()

    class UnstartableSession:
        def start(self, _config):
            pytest.fail("analytical backend authority refusal occurred after start")

    monkeypatch.setattr(driver, "SimSession", UnstartableSession)

    with pytest.raises(RuntimeError, match="internal-analytical backend selected"):
        driver._start_session(
            feedstock="mars_basalt",
            campaign="C0",
            backend_name=backend_name,
            mass_kg=1000.0,
            additives_kg={},
            store=SimpleNamespace(),
            allow_internal_analytical_equilibrium=False,
        )


def test_start_session_canonicalizes_analytical_alias_identity(tmp_path, monkeypatch):
    driver = _load_driver()
    captured_configs = []

    class FakeSession:
        def __init__(self):
            self.simulator = SimpleNamespace(
                configure_pt0_determinism_store=lambda _store: None,
                backend=SimpleNamespace(),
            )

        def start(self, config):
            captured_configs.append(config)
            return self

    monkeypatch.setattr(driver, "SimSession", FakeSession)
    monkeypatch.setattr(
        driver,
        "load_config_bundle",
        lambda: SimpleNamespace(feedstocks={}, setpoints={}, vapor_pressures={}),
    )

    for backend_name in ("stub", "internal-analytical"):
        driver._start_session(
            feedstock="mars_basalt",
            campaign="C0",
            backend_name=backend_name,
            mass_kg=1000.0,
            additives_kg={},
            store=driver.PT0DeterminismStore(
                "capture", db_path=tmp_path / f"{backend_name}.db"
            ),
            allow_internal_analytical_equilibrium=True,
        )

    assert [config.backend_name for config in captured_configs] == [
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    ]


def test_run_case_rejects_zero_hours_before_session_start(tmp_path, monkeypatch):
    driver = _load_driver()
    monkeypatch.setattr(
        driver,
        "_start_session",
        lambda **_kwargs: pytest.fail("zero-hour run started a session"),
    )

    with pytest.raises(ValueError, match="hours must be greater than zero"):
        driver._run_case(
            feedstock="mars_basalt",
            campaign="C0",
            backend_name="alphamelts",
            mass_kg=1000.0,
            additives_kg={},
            hours=0,
            wall_cap_s=60.0,
            db_path=tmp_path / "cache.db",
            mode="capture",
            disable_live=False,
            allow_internal_analytical_equilibrium=False,
        )


def test_merge_cache_shard_recomputes_identity_contract(tmp_path):
    driver = _load_driver()
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    key = _pt1_key("identity")
    _write_pt1_row(shard_db, key)

    summary = driver._merge_cache_shard(shard_db, target_db)

    assert summary["inserted_rows"] == 1
    with sqlite3.connect(target_db) as conn:
        row = conn.execute(
            f"""
            SELECT
                physics_bucket_schema_version,
                physics_bucket_sha256,
                replay_scope_sha256,
                physics_key_bytes,
                physics_bucket_h40_sha256,
                physics_bucket_h40_distance,
                physics_bucket_h30_sha256,
                physics_bucket_h30_distance,
                physics_bucket_h40c_sha256,
                physics_bucket_h40c_distance,
                physics_bucket_h30c_sha256,
                physics_bucket_h30c_distance
            FROM {driver.PT1_EQUILIBRIUM_TABLE}
            """
        ).fetchone()
    assert row is not None
    bucket = rrd.canonical_physics_bucket_key_from_replay_key(key)
    bucket_bytes = rrd.canonical_json_bytes(bucket)
    ladder = rrd._physics_ladder_values_from_replay_key(key)
    assert row == (
        rrd.PHYSICS_BUCKET_SCHEMA_VERSION,
        hashlib.sha256(bucket_bytes).hexdigest(),
        rrd._replay_scope_hash(bucket),
        bucket_bytes,
        ladder["h40"]["sha256"],
        ladder["h40"]["distance"],
        ladder["h30"]["sha256"],
        ladder["h30"]["distance"],
        ladder["h40c"]["sha256"],
        ladder["h40c"]["distance"],
        ladder["h30c"]["sha256"],
        ladder["h30c"]["distance"],
    )


def test_merge_cache_shard_rejects_stored_payload_hash_drift(tmp_path):
    driver = _load_driver()
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    key_hash = _write_pt1_row(shard_db, _pt1_key("hash-drift"))
    with sqlite3.connect(shard_db) as conn:
        conn.execute(
            f"""
            UPDATE {driver.PT1_EQUILIBRIUM_TABLE}
            SET payload_sha256 = ?
            WHERE key_hash = ?
            """,
            ("0" * 64, key_hash),
        )

    with pytest.raises(RuntimeError, match="payload hash mismatch"):
        driver._merge_cache_shard(shard_db, target_db)
    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_merge_cache_shard_refuses_corpus_mismatch_without_mutating_target(tmp_path):
    driver = _load_driver()
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_pt1_row(target_db, _pt1_key("target"))
    shard_key = _pt1_key("shard")
    shard_key["corpus_version"] = "incompatible-test-corpus"
    _write_pt1_row(shard_db, shard_key)
    before = target_db.read_bytes()

    with pytest.raises(
        driver.CacheShardCorpusVersionMismatch,
        match="corpus version mismatch",
    ):
        driver._merge_cache_shard(shard_db, target_db)

    assert target_db.read_bytes() == before


def test_merge_cache_shard_refuses_row_key_corpus_drift_without_mutating_target(
    tmp_path,
):
    driver = _load_driver()
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_pt1_row(target_db, _pt1_key("target"))
    shard_hash = _write_pt1_row(shard_db, _pt1_key("shard"))
    with sqlite3.connect(shard_db) as conn:
        conn.execute(
            f"""
            UPDATE {driver.PT1_EQUILIBRIUM_TABLE}
            SET corpus_version = ?
            WHERE key_hash = ?
            """,
            ("drifted-row-corpus", shard_hash),
        )
    before = target_db.read_bytes()

    with pytest.raises(
        driver.CacheShardCorpusVersionMismatch,
        match="row corpus version mismatch",
    ):
        driver._merge_cache_shard(shard_db, target_db)

    assert target_db.read_bytes() == before


def test_physics_bucket_hit_recomputes_bucket_from_exact_key(tmp_path):
    db_path = tmp_path / "cache.db"
    source_key = _pt1_key("source", temperature_K=1500.0)
    query_key = _pt1_key("query", temperature_K=1600.0)
    key_hash = _write_pt1_row(db_path, source_key)
    query_bucket = rrd.canonical_physics_bucket_key_from_replay_key(query_key)
    query_bucket_bytes = rrd.canonical_json_bytes(query_bucket)
    query_bucket_hash = hashlib.sha256(query_bucket_bytes).hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            UPDATE {rrd.PT1_EQUILIBRIUM_TABLE}
            SET physics_bucket_sha256 = ?,
                replay_scope_sha256 = ?,
                physics_key_bytes = ?
            WHERE key_hash = ?
            """,
            (
                query_bucket_hash,
                rrd._replay_scope_hash(query_bucket),
                sqlite3.Binary(query_bucket_bytes),
                key_hash,
            ),
        )

    store = rrd.PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(rrd.PT1PersistentStoreCorrupt, match="exact key"):
        store.get_by_physics_bucket(
            artifact="equilibrium_result",
            physics_bucket_key=query_bucket,
            physics_bucket_bytes=query_bucket_bytes,
            physics_bucket_hash=query_bucket_hash,
        )


def test_interpolation_candidates_verify_payload_hash_on_read(tmp_path):
    db_path = tmp_path / "cache.db"
    key = _pt1_key("candidate")
    key_hash = _write_pt1_row(db_path, key)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            UPDATE {rrd.PT1_EQUILIBRIUM_TABLE}
            SET payload_bytes = ?
            WHERE key_hash = ?
            """,
            (sqlite3.Binary(b'{"corrupt":true}'), key_hash),
        )

    bucket = rrd.canonical_physics_bucket_key_from_replay_key(key)
    store = rrd.PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(rrd.PT1PersistentStoreCorrupt, match="payload bytes"):
        store.list_interpolation_candidates(
            artifact="equilibrium_result",
            replay_scope_sha256=rrd._replay_scope_hash(bucket),
        )


def _corrupt_key_bytes_only(db_path, key_hash, key):
    corrupt_key = dict(key)
    corrupt_key["suffix"] = f"{key['suffix']}-corrupt"
    corrupt_bytes = rrd.canonical_json_bytes(corrupt_key)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {rrd.PT1_EQUILIBRIUM_TABLE} SET key_bytes = ? WHERE key_hash = ?",
            (sqlite3.Binary(corrupt_bytes), key_hash),
        )
        return conn.execute(
            f"SELECT key_hash, key_sha256, key_bytes, payload_bytes FROM {rrd.PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()


def _stored_row_identity(db_path, key_hash):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            f"SELECT key_hash, key_sha256, key_bytes, payload_bytes FROM {rrd.PT1_EQUILIBRIUM_TABLE} WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()


def test_interpolation_candidates_refuse_key_bytes_hash_drift_without_mutation(tmp_path):
    db_path = tmp_path / "cache.db"
    key = _pt1_key("candidate-key-drift")
    key_hash = _write_pt1_row(db_path, key)
    before = _corrupt_key_bytes_only(db_path, key_hash, key)
    bucket = rrd.canonical_physics_bucket_key_from_replay_key(key)

    store = rrd.PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(rrd.PT1PersistentStoreCorrupt, match="key bytes hash"):
        store.list_interpolation_candidates(
            artifact="equilibrium_result",
            replay_scope_sha256=rrd._replay_scope_hash(bucket),
        )

    assert _stored_row_identity(db_path, key_hash) == before


def test_physics_bucket_read_refuses_key_bytes_hash_drift_without_mutation(tmp_path):
    db_path = tmp_path / "cache.db"
    key = _pt1_key("bucket-key-drift")
    key_hash = _write_pt1_row(db_path, key)
    before = _corrupt_key_bytes_only(db_path, key_hash, key)
    bucket = rrd.canonical_physics_bucket_key_from_replay_key(key)
    bucket_bytes = rrd.canonical_json_bytes(bucket)

    store = rrd.PT1PersistentEquilibriumStore(db_path)
    with pytest.raises(rrd.PT1PersistentStoreCorrupt, match="key bytes hash"):
        store.get_by_physics_bucket(
            artifact="equilibrium_result",
            physics_bucket_key=bucket,
            physics_bucket_bytes=bucket_bytes,
            physics_bucket_hash=hashlib.sha256(bucket_bytes).hexdigest(),
        )

    assert _stored_row_identity(db_path, key_hash) == before

def test_profile_c0b_campaign_key_is_accepted_by_session(tmp_path):
    driver = _load_driver()

    driver._start_session(
        feedstock="lunar_pkt_kreep_average",
        campaign="C0b_p_cleanup",
        backend_name="internal-analytical",
        mass_kg=1000.0,
        additives_kg={},
        store=driver.PT0DeterminismStore(
            "capture",
            db_path=tmp_path / "cache.db",
        ),
        allow_internal_analytical_equilibrium=True,
    )
