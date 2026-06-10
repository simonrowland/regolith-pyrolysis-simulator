import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

import simulator.reduced_real_determinism as rrd


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "populate_reduced_real_cache",
    REPO_ROOT / "scripts" / "populate_reduced_real_cache.py",
)
driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(driver)


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_magemin_row(db_path, suffix):
    key = {
        "schema_version": "test",
        "code_version": "test",
        "engine_version": "test",
        "data_digests": {},
        "provider": {
            "resolved_provider_id": driver.MAGEMIN_PROVIDER_ID,
            "resolved_role": "silicate_liquidus",
        },
        "suffix": suffix,
    }
    payload = {"suffix": suffix}
    key_bytes = _canonical_bytes(key)
    payload_bytes = _canonical_bytes(payload)
    driver.PT1PersistentEquilibriumStore(db_path).put(
        artifact="equilibrium_result",
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )


def _write_equilibrium_post_record_row_raw(
    db_path,
    *,
    backend_name,
    provider_id,
):
    key = {
        "schema_version": rrd.SCHEMA_VERSION,
        "code_version": "test",
        "engine_version": "test",
        "data_digests": {},
        "backend": {
            "backend_name": backend_name,
            "backend_class": backend_name,
            "backend_version": "test",
        },
        "provider": {
            "resolved_provider_id": provider_id,
            "authoritative_provider_id": provider_id,
            "fallback_provider_id": None,
        },
    }
    payload = {"equilibrium_result": {"status": "ok"}}
    key_bytes = _canonical_bytes(key)
    payload_bytes = _canonical_bytes(payload)
    driver.PT1PersistentEquilibriumStore(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO {driver.PT1_EQUILIBRIUM_TABLE} (
                key_hash,
                artifact,
                store_schema_version,
                request_schema_version,
                key_sha256,
                payload_sha256,
                key_bytes,
                payload_bytes,
                code_version,
                engine_version,
                data_digests_json,
                created_at,
                git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hashlib.sha256(key_bytes).hexdigest(),
                "equilibrium_post_record",
                rrd.PT1_STORE_SCHEMA_VERSION,
                key["schema_version"],
                hashlib.sha256(key_bytes).hexdigest(),
                hashlib.sha256(payload_bytes).hexdigest(),
                sqlite3.Binary(key_bytes),
                sqlite3.Binary(payload_bytes),
                key["code_version"],
                key["engine_version"],
                _canonical_bytes(key["data_digests"]).decode("utf-8"),
                "2026-06-04T00:00:00Z",
                0,
            ),
        )


def _result(*, status="complete", marker="same", row_mass=0.0, trace_mass=None, mode="capture"):
    if trace_mass is None:
        trace_mass = row_mass
    return {
        "status": status,
        "case": {"mode": mode},
        "stop_reason": "max_hours" if status == "complete" else "mass_balance_failed",
        "elapsed_s": 0.0,
        "hours_completed": 1,
        "rows": [
            {
                "hour_index": 1,
                "mass_balance_error_pct": row_mass,
                "magemin_calls": [] if mode == "replay" else [{"elapsed_s": 1.0, "status": "ok"}],
            }
        ],
        "mass_balance_gate": {
            "threshold_pct": driver.MASS_BALANCE_GATE_PCT,
            "passed": status == "complete",
            "max_abs_error_pct": abs(row_mass),
            "failed_row": None,
        },
        "store_summary": {
            "cache_state_counts": {"cached_exact": 1} if mode == "replay" else {"live_fill": 1},
            "misses": 0,
        },
        "magemin_timings": [] if mode == "replay" else [{"elapsed_s": 1.0, "status": "ok"}],
        "trace_view": {
            "campaign": "C2A_continuous",
            "campaign_hour": 1.0,
            "temperature_C": 1200.0,
            "mass_balance_error_pct": trace_mass,
            "products": {"marker": marker},
            "rows": 1,
        },
    }


def _patch_common(monkeypatch, emitted):
    monkeypatch.setattr(driver, "_resolve_profile", lambda path: REPO_ROOT / "dummy-profile.yaml")
    monkeypatch.setattr(driver, "_load_yaml", lambda path: {"feedstock": "fake_feedstock"})
    monkeypatch.setattr(driver, "_magemin_status", lambda: {"available": True})
    monkeypatch.setattr(driver, "_full_population_command", lambda args, profile_path: "full")
    monkeypatch.setattr(driver, "_emit", lambda result, json_out: emitted.append(result))


def test_multi_feedstock_run_resolves_additives_per_feedstock_in_replay(
    tmp_path,
    monkeypatch,
):
    emitted = []
    calls = []
    target_db = tmp_path / "target.db"

    monkeypatch.setattr(driver, "_magemin_status", lambda: {"available": True})
    monkeypatch.setattr(driver, "_full_population_command", lambda args, profile_path: "full")
    monkeypatch.setattr(driver, "_emit", lambda result, json_out: emitted.append(result))

    def fake_run_case(*, db_path, mode, feedstock, campaign, additives_kg, **kwargs):
        calls.append(
            {
                "mode": mode,
                "feedstock": feedstock,
                "campaign": campaign,
                "additives_kg": dict(additives_kg),
            }
        )
        if mode == "capture":
            _write_magemin_row(db_path, f"{feedstock}-{campaign}")
        return _result(marker="same", mode=mode)

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(
        [
            "--profile",
            "data/optimize_profiles/mars_basalt.yaml",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--validate-replay",
            "--feedstock",
            "mars_basalt",
            "--feedstock",
            "lunar_mare_low_ti",
        ]
    )

    assert rc == 0
    assert emitted[-1]["additives_kg"] == {
        "mars_basalt": {"C": 30.0},
        "lunar_mare_low_ti": {},
    }
    assert {call["mode"] for call in calls} == {"capture", "replay"}
    for call in calls:
        if call["feedstock"] == "mars_basalt":
            assert call["additives_kg"] == {"C": 30.0}
        elif call["feedstock"] == "lunar_mare_low_ti":
            assert call["additives_kg"] == {}
        else:
            pytest.fail(f"unexpected feedstock: {call['feedstock']}")


def test_validation_requires_trace_and_mass_balance_equality():
    trace_diverged = driver._validation_summary(
        [_result(marker="live", mode="capture")],
        [_result(marker="replay", mode="replay")],
    )
    assert trace_diverged["trace_equal"] is False
    assert trace_diverged["cached_exact_confirmed"] is False

    mass_diverged = driver._validation_summary(
        [_result(row_mass=0.0, trace_mass=0.0, mode="capture")],
        [_result(row_mass=1e-13, trace_mass=0.0, mode="replay")],
    )
    assert mass_diverged["trace_equal"] is True
    assert mass_diverged["mass_balance_equal"] is False
    assert mass_diverged["cached_exact_confirmed"] is False


def test_validate_replay_exits_nonzero_when_trace_differs(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"

    def fake_run_case(*, db_path, mode, **kwargs):
        if mode == "capture":
            _write_magemin_row(db_path, "captured")
            return _result(marker="live", mode="capture")
        assert Path(db_path) != target_db
        assert driver._cache_row_summary(db_path)["rows"] == 1
        return _result(marker="replay", mode="replay")

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(
        [
            "--profile",
            "unused",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--validate-replay",
        ]
    )

    assert rc == 3
    assert emitted[-1]["status"] == "failed"
    assert emitted[-1]["failed_reason"] == "replay_validation_failed"
    assert emitted[-1]["validation"]["cached_exact_confirmed"] is False
    assert emitted[-1]["cache_merges"][0]["discarded"] is True
    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_mass_balance_failure_discards_shard_without_seeding_target(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"

    def fake_run_case(*, db_path, mode, **kwargs):
        _write_magemin_row(db_path, "bad")
        return _result(status="failed", marker="bad", row_mass=1e-6, mode=mode)

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(["--profile", "unused", "--db", str(target_db), "--hours", "1"])

    assert rc == 4
    assert emitted[-1]["status"] == "failed"
    assert emitted[-1]["failed_reason"] == "mass_balance_gate_failed"
    assert emitted[-1]["live"][0]["cache_merge"]["discarded"] is True
    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_passing_run_merges_shard_but_estimate_excludes_old_rows(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"
    _write_magemin_row(target_db, "old")

    def fake_run_case(*, db_path, mode, **kwargs):
        _write_magemin_row(db_path, "new")
        return _result(marker="new", mode=mode)

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(["--profile", "unused", "--db", str(target_db), "--hours", "1"])

    assert rc == 0
    assert emitted[-1]["status"] == "complete"
    assert emitted[-1]["cache"]["magemin_unique_keys"] == 2
    assert emitted[-1]["estimate"]["observed_magemin_keys"] == 1
    assert emitted[-1]["estimate"]["key_rate_basis"] == "run_local_temporary_capture_shards"


def test_known_chemistry_edges_are_isolated_per_case(
    tmp_path,
    monkeypatch,
    capsys,
):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"

    def fake_load_yaml(path):
        path = Path(path)
        if path.name == "dummy-profile.yaml":
            return {"feedstock": "fake_feedstock"}
        return {"feedstock": path.stem}

    monkeypatch.setattr(driver, "_load_yaml", fake_load_yaml)

    def fake_run_case(*, db_path, feedstock, campaign, mode, **kwargs):
        assert mode == "capture"
        if feedstock == "m_type_metallic_phase":
            raise RuntimeError(
                "Authoritative VAPOR_PRESSURE dispatch returned "
                "status='out_of_domain' with no pressures and "
                "allow_fallback_vapor=False; refusing to silently continue "
                "on backend vapor pressures. Diagnostic keys: "
                "['provider_id', 'status']"
            )
        if feedstock == "mars_phyllosilicate_clay" and campaign == "C4":
            raise driver.PT0NonFinitePayload(
                "non-finite value in PT-0 payload: inf"
            )
        _write_magemin_row(db_path, f"{feedstock}-{campaign}")
        return _result(marker=f"{feedstock}-{campaign}", mode=mode)

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(
        [
            "--profile",
            "unused",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--feedstock",
            "m_type_metallic_phase",
            "--feedstock",
            "mars_phyllosilicate_clay",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C2A_continuous",
            "--campaign",
            "C4",
        ]
    )

    assert rc == 0
    assert emitted[-1]["status"] == "complete"
    assert driver._cache_row_summary(target_db)["rows"] == 3
    assert emitted[-1]["domain_gaps"] == emitted[-1]["case_gaps"]
    assert emitted[-1]["domain_gap_count"] == 3
    assert {
        (gap["feedstock"], gap["campaign"], gap["reason"])
        for gap in emitted[-1]["domain_gaps"]
    } == {
        (
            "m_type_metallic_phase",
            "C2A_continuous",
            "vapor_pressure_out_of_domain",
        ),
        ("m_type_metallic_phase", "C4", "vapor_pressure_out_of_domain"),
        ("mars_phyllosilicate_clay", "C4", "non_finite_payload"),
    }
    assert "CASE-GAP: m_type_metallic_phase/C2A_continuous" in capsys.readouterr().out


def test_gate_liquidus_unavailable_is_isolated_per_case(
    tmp_path,
    monkeypatch,
    capsys,
):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"

    def fake_load_yaml(path):
        path = Path(path)
        if path.name == "dummy-profile.yaml":
            return {"feedstock": "fake_feedstock"}
        return {"feedstock": path.stem}

    monkeypatch.setattr(driver, "_load_yaml", fake_load_yaml)

    gate_message = (
        "freeze_gate.enabled requires a liquid_fraction(T) source; "
        "no liquidus engine produced usable solidus/liquidus bounds. "
        "gate liquid fraction unavailable: status=not_converged"
    )

    def fake_run_case(*, db_path, feedstock, campaign, mode, **kwargs):
        assert mode == "capture"
        if feedstock == "mars_sulfate_rich":
            raise RuntimeError(gate_message)
        _write_magemin_row(db_path, f"{feedstock}-{campaign}")
        return _result(marker=f"{feedstock}-{campaign}", mode=mode)

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(
        [
            "--profile",
            "unused",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--feedstock",
            "mars_sulfate_rich",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C2A_staged",
        ]
    )

    assert rc == 0
    assert emitted[-1]["status"] == "complete"
    assert driver._cache_row_summary(target_db)["rows"] == 1
    assert emitted[-1]["domain_gaps"] == emitted[-1]["case_gaps"]
    assert emitted[-1]["domain_gap_count"] == 1
    assert emitted[-1]["domain_gaps"][0] == {
        "feedstock": "mars_sulfate_rich",
        "campaign": "C2A_staged",
        "reason": "gate_liquidus_unavailable",
        "detail": gate_message,
    }
    out = capsys.readouterr().out
    assert "[case] feedstock=mars_sulfate_rich campaign=C2A_staged start" in out
    assert (
        "[case] feedstock=mars_sulfate_rich campaign=C2A_staged "
        "status=gate_liquidus_unavailable hours=0"
    ) in out
    assert "[case] feedstock=lunar_mare_low_ti campaign=C2A_staged start" in out
    assert (
        "[case] feedstock=lunar_mare_low_ti campaign=C2A_staged "
        "status=ok hours=1"
    ) in out
    assert "CASE-GAP: mars_sulfate_rich/C2A_staged gate_liquidus_unavailable" in out


def test_non_prefix_gate_liquidus_runtime_error_still_aborts(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)

    def fake_run_case(**kwargs):
        raise RuntimeError(
            "gate liquid fraction unavailable: freeze_gate.enabled requires "
            "a liquid_fraction(T) source"
        )

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    with pytest.raises(RuntimeError, match="gate liquid fraction unavailable"):
        driver.main(
            [
                "--profile",
                "unused",
                "--db",
                str(tmp_path / "target.db"),
                "--hours",
                "1",
            ]
        )

    assert emitted == []


def test_gate_liquidus_message_prefix_pinned_to_upstream_source():
    assert driver.GATE_LIQUIDUS_UNAVAILABLE_PREFIX == (
        "freeze_gate.enabled requires a liquid_fraction(T) source"
    )
    evaporation_source = (
        REPO_ROOT / "simulator" / "evaporation.py"
    ).read_text(encoding="utf-8")
    assert driver.GATE_LIQUIDUS_UNAVAILABLE_PREFIX in evaporation_source


def test_unrelated_runtime_error_still_propagates(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)

    def fake_run_case(**kwargs):
        raise RuntimeError("unexpected mass-balance breach")

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    with pytest.raises(RuntimeError, match="unexpected mass-balance breach"):
        driver.main(
            [
                "--profile",
                "unused",
                "--db",
                str(tmp_path / "target.db"),
                "--hours",
                "1",
            ]
        )

    assert emitted == []


def test_validate_replay_publishes_only_after_success(tmp_path, monkeypatch):
    emitted = []
    _patch_common(monkeypatch, emitted)
    target_db = tmp_path / "target.db"
    replay_db_paths = []

    def fake_run_case(*, db_path, mode, **kwargs):
        if mode == "capture":
            _write_magemin_row(db_path, "validated")
            return _result(marker="same", mode="capture")
        replay_db_paths.append(Path(db_path))
        assert Path(db_path) != target_db
        assert driver._cache_row_summary(db_path)["rows"] == 1
        return _result(marker="same", mode="replay")

    monkeypatch.setattr(driver, "_run_case", fake_run_case)

    rc = driver.main(
        [
            "--profile",
            "unused",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--validate-replay",
        ]
    )

    assert rc == 0
    assert replay_db_paths
    assert emitted[-1]["status"] == "complete"
    assert emitted[-1]["validation"]["cached_exact_confirmed"] is True
    assert emitted[-1]["cache_merges"][0]["merged"] is True
    assert driver._cache_row_summary(target_db)["rows"] == 1

    emitted.clear()
    rc = driver.main(
        [
            "--profile",
            "unused",
            "--db",
            str(target_db),
            "--hours",
            "1",
            "--validate-replay",
        ]
    )

    assert rc == 0
    assert emitted[-1]["validation"]["cached_exact_confirmed"] is True
    assert emitted[-1]["replay"][0]["store_summary"]["cache_state_counts"][
        "cached_exact"
    ] == 1
    assert driver._cache_row_summary(target_db)["rows"] == 1


def test_merge_cache_shard_rolls_back_mid_merge_error(tmp_path):
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_magemin_row(shard_db, "first")
    _write_magemin_row(shard_db, "second")
    driver.PT1PersistentEquilibriumStore(target_db)
    with sqlite3.connect(target_db) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER abort_second_cache_insert
            BEFORE INSERT ON {driver.PT1_EQUILIBRIUM_TABLE}
            WHEN (SELECT COUNT(*) FROM {driver.PT1_EQUILIBRIUM_TABLE}) >= 1
            BEGIN
                SELECT RAISE(ABORT, 'simulated mid-merge abort');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated mid-merge abort"):
        driver._merge_cache_shard(shard_db, target_db)

    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_merge_cache_shard_rejects_stub_equilibrium_post_record(tmp_path):
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_equilibrium_post_record_row_raw(
        shard_db,
        backend_name="StubBackend",
        provider_id="builtin-backend-equilibrium",
    )

    with pytest.raises(RuntimeError, match="builtin-backend-equilibrium"):
        driver._merge_cache_shard(shard_db, target_db)

    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_full_population_command_documents_authorized_backend():
    args = driver._parse_args([])
    command = driver._full_population_command(
        args,
        REPO_ROOT / "data" / "optimize_profiles" / "lunar_mare_low_ti.yaml",
    )

    assert args.backend == "alphamelts"
    assert "--backend alphamelts" in command
    assert "--require-magemin" in command
    assert "--allow-stub-equilibrium" not in command
