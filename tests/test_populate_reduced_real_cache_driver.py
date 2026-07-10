import contextlib
import hashlib
import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.reduced_real_determinism as rrd
from simulator.corpus_version import current_corpus_version


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


def test_control_quantization_cli_parse_tier_json_and_bad() -> None:
    tier_args = driver._parse_args(["--control-quantization", "XX-COARSE"])
    assert tier_args.control_quantization == rrd.ControlQuantization.from_name(
        "xx_coarse"
    )

    json_args = driver._parse_args(
        [
            "--control-quantization",
            json.dumps(
                {
                    "t_k_quantum": 2.0,
                    "pressure_bar_quantum": 0.002,
                    "log_fo2_quantum": 0.02,
                    "composition_sig_figs": 3,
                }
            ),
        ]
    )
    assert json_args.control_quantization == rrd.ControlQuantization(
        t_k_quantum=2.0,
        pressure_bar_quantum=0.002,
        log_fo2_quantum=0.02,
        composition_sig_figs=3,
    )

    with pytest.raises(SystemExit):
        driver._parse_args(["--control-quantization", "bad-tier"])


def _write_magemin_row(db_path, suffix, payload=None):
    key = {
        "schema_version": "test",
        "code_version": "test",
        "corpus_version": current_corpus_version(),
        "data_digests": {},
        "provider": {
            "resolved_provider_id": driver.MAGEMIN_PROVIDER_ID,
            "resolved_role": "silicate_liquidus",
        },
        "suffix": suffix,
    }
    payload = payload or {
        "suffix": suffix,
        "last_vapor_pressures_source": {"Na": "builtin_authoritative"},
    }
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


def _strict_vapor_pt1_key(
    suffix,
    *,
    vapor_provider_id="builtin-vapor-pressure",
    fallback_provider_id=None,
):
    return {
        "schema_version": "test",
        "code_version": "test",
        "corpus_version": current_corpus_version(),
        "data_digests": {},
        "provider": {
            "resolved_provider_id": driver.MAGEMIN_PROVIDER_ID,
            "resolved_role": "silicate_liquidus",
        },
        "vapor_pressure_provider": {
            "resolved_provider_id": vapor_provider_id,
            "resolved_role": "authoritative",
            "authoritative_provider_id": vapor_provider_id,
            "fallback_provider_id": fallback_provider_id,
            "fallback_allowed": fallback_provider_id is not None,
        },
        "suffix": suffix,
    }


def _strict_vapor_pt1_payload():
    return {
        "equilibrium_result": {"status": "ok"},
        "last_vapor_pressures_source": {"Na": "builtin_authoritative"},
    }


def _write_equilibrium_post_record_row(
    db_path,
    *,
    key,
    payload,
    strict_vapor_gate=False,
):
    key_bytes = _canonical_bytes(key)
    payload_bytes = _canonical_bytes(payload)
    driver.PT1PersistentEquilibriumStore(
        db_path,
        strict_vapor_gate=strict_vapor_gate,
    ).put(
        artifact="equilibrium_post_record",
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
        "corpus_version": current_corpus_version(),
        "data_digests": {},
        "backend": {
            "backend_name": backend_name,
            "backend_class": backend_name,
            "corpus_version": current_corpus_version(),
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
                corpus_version,
                engine_version,
                data_digests_json,
                created_at,
                git_dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                key["corpus_version"],
                "test",
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
    real_cfg = driver.load_config_bundle()
    feedstocks = dict(real_cfg.feedstocks)
    feedstocks["fake_feedstock"] = {"stage0_verdict_b_subprocess_required": True}
    cfg = SimpleNamespace(
        setpoints=real_cfg.setpoints,
        feedstocks=feedstocks,
    )
    monkeypatch.setattr(driver, "_resolve_profile", lambda path: REPO_ROOT / "dummy-profile.yaml")
    monkeypatch.setattr(driver, "_load_yaml", lambda path: {"feedstock": "fake_feedstock"})
    monkeypatch.setattr(driver, "load_config_bundle", lambda: cfg)
    monkeypatch.setattr(driver, "_magemin_status", lambda: {"available": True})
    monkeypatch.setattr(driver, "_full_population_command", lambda args, profile_path: "full")
    monkeypatch.setattr(driver, "_emit", lambda result, json_out: emitted.append(result))


@pytest.mark.parametrize(
    "backend",
    ("stub", "internal-analytical", " Internal-Analytical ", "INTERNAL_ANALYTICAL"),
)
def test_stub_equivalent_backend_cannot_populate_reduced_real_cache(
    tmp_path,
    monkeypatch,
    backend,
):
    emitted = []
    _patch_common(monkeypatch, emitted)
    monkeypatch.setattr(
        driver,
        "load_config_bundle",
        lambda: SimpleNamespace(setpoints={"chemistry_kernel": {}}),
    )

    with pytest.raises(RuntimeError) as exc:
        driver.main(
            [
                "--profile",
                "unused",
                "--db",
                str(tmp_path / "target.db"),
                "--backend",
                backend,
            ]
        )

    assert str(exc.value) == (
        "stub backend cannot populate the PT-1 reduced-real cache; "
        "use --backend alphamelts --require-magemin"
    )
    assert emitted == []


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


def test_real_backend_out_of_domain_case_gap_is_classified():
    gap = driver._known_chemistry_case_gap(
        RuntimeError(
            "real_backend_out_of_domain: non_silicate_feedstock: "
            "feedstock 'm_type_metallic_phase' has no MELTS oxide-basis "
            "composition; backend cannot solve this composition"
        )
    )

    assert gap is not None
    assert gap["reason"] == "non_silicate_feedstock"


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


def test_merge_cache_shard_rejects_builtin_fallback_vapor_source(tmp_path):
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_magemin_row(
        shard_db,
        "poisoned",
        payload={
            "equilibrium_result": {"status": "ok"},
            "last_vapor_pressures_source": {"Na": "builtin_fallback"},
        },
    )

    with pytest.raises(driver.GrindSourceGateError, match="builtin_fallback"):
        driver._merge_cache_shard(shard_db, target_db)

    assert driver._cache_row_summary(target_db)["rows"] == 0


@pytest.mark.parametrize("write_path", ("put", "merge"))
@pytest.mark.parametrize(
    ("case_name", "key_kwargs", "payload_update", "match"),
    (
        (
            "key_non_authoritative_provider",
            {"vapor_provider_id": "vaporock"},
            {},
            "resolved_provider_id",
        ),
        (
            "key_fallback_provider",
            {"fallback_provider_id": "builtin-vapor-pressure"},
            {},
            "fallback_provider_id",
        ),
        (
            "payload_builtin_fallback",
            {},
            {"last_vapor_pressures_source": {"Na": "builtin_fallback"}},
            "builtin_fallback",
        ),
        (
            "payload_kernel_fallback",
            {},
            {"equilibrium_result": {"status": "ok", "kernel_fallback_used": True}},
            "kernel_fallback_used",
        ),
    ),
)
def test_strict_pt1_write_paths_share_vapor_gate(
    tmp_path,
    write_path,
    case_name,
    key_kwargs,
    payload_update,
    match,
):
    target_db = tmp_path / f"{write_path}-{case_name}-target.db"
    key = _strict_vapor_pt1_key(case_name, **key_kwargs)
    payload = _strict_vapor_pt1_payload()
    payload.update(payload_update)

    def write_put():
        _write_equilibrium_post_record_row(
            target_db,
            key=key,
            payload=payload,
            strict_vapor_gate=True,
        )

    def write_merge():
        shard_db = tmp_path / f"{write_path}-{case_name}-shard.db"
        _write_equilibrium_post_record_row(
            shard_db,
            key=key,
            payload=payload,
            strict_vapor_gate=False,
        )
        driver._merge_cache_shard(shard_db, target_db)

    write = write_put if write_path == "put" else write_merge
    with pytest.raises(driver.GrindSourceGateError, match=match):
        write()

    assert driver._cache_row_summary(target_db)["rows"] == 0

    clean_target_db = tmp_path / f"{write_path}-{case_name}-clean-target.db"
    clean_key = _strict_vapor_pt1_key(f"{case_name}-clean")
    clean_payload = _strict_vapor_pt1_payload()
    if write_path == "put":
        _write_equilibrium_post_record_row(
            clean_target_db,
            key=clean_key,
            payload=clean_payload,
            strict_vapor_gate=True,
        )
    else:
        clean_shard_db = tmp_path / f"{write_path}-{case_name}-clean-shard.db"
        _write_equilibrium_post_record_row(
            clean_shard_db,
            key=clean_key,
            payload=clean_payload,
            strict_vapor_gate=False,
        )
        driver._merge_cache_shard(clean_shard_db, clean_target_db)
    assert driver._cache_row_summary(clean_target_db)["rows"] == 1


def test_merge_cache_shard_rejects_missing_vapor_source_provenance(tmp_path):
    shard_db = tmp_path / "shard.db"
    target_db = tmp_path / "target.db"
    _write_magemin_row(
        shard_db,
        "missing-provenance",
        payload={"equilibrium_result": {"status": "ok"}},
    )

    with pytest.raises(driver.GrindSourceGateError, match="missing last_vapor"):
        driver._merge_cache_shard(shard_db, target_db)

    assert driver._cache_row_summary(target_db)["rows"] == 0


def test_main_preflight_rejects_fallback_enabled_setpoints(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "feedstock: lunar_mare_low_ti\ncampaigns: [C2A_continuous]\n",
        encoding="utf-8",
    )
    bundle = type(
        "Bundle",
        (),
        {"setpoints": {"chemistry_kernel": {"allow_fallback_vapor": True}}},
    )()
    monkeypatch.setattr(driver, "load_config_bundle", lambda: bundle)

    with pytest.raises(driver.GrindSourceGateError, match="allow_fallback_vapor"):
        driver.main(
            [
                "--profile",
                str(profile),
                "--db",
                str(tmp_path / "cache.db"),
                "--hours",
                "1",
            ]
        )


def test_main_preflight_rejects_uncovered_feedstock_before_case_run(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "feedstock: interwindow\ncampaigns: [C2A_continuous]\n",
        encoding="utf-8",
    )
    bundle = SimpleNamespace(
        setpoints={"chemistry_kernel": {}},
        feedstocks={
            "interwindow": {
                "composition_wt_pct": {
                    "SiO2": 42.0,
                    "Al2O3": 12.0,
                    "FeO": 12.0,
                    "MgO": 18.0,
                    "TiO2": 0.3,
                    "CaO": 10.0,
                }
            }
        },
    )
    monkeypatch.setattr(driver, "load_config_bundle", lambda: bundle)
    monkeypatch.setattr(driver, "_magemin_status", lambda: {"available": True})

    def fail_run_case(**kwargs):
        raise AssertionError("preflight must run before cache population cases")

    monkeypatch.setattr(driver, "_run_case", fail_run_case)

    with pytest.raises(driver.GrindSourceGateError, match="interwindow"):
        driver.main(
            [
                "--profile",
                str(profile),
                "--db",
                str(tmp_path / "cache.db"),
                "--hours",
                "1",
            ]
        )


def test_run_case_records_all_builtin_authoritative_source_report(tmp_path, monkeypatch):
    session = _FakeSession(source="builtin_authoritative")
    monkeypatch.setattr(driver, "_start_session", lambda **kwargs: session)
    monkeypatch.setattr(driver, "_apply_pending_decision", lambda session: False)

    result = driver._run_case(
        feedstock="lunar_mare_low_ti",
        campaign="C2A_continuous",
        backend_name="alphamelts",
        mass_kg=1000.0,
        additives_kg={},
        hours=1,
        wall_cap_s=60.0,
        db_path=tmp_path / "case.db",
        mode="capture",
        disable_live=False,
        allow_stub_equilibrium=False,
    )

    source_report = result["rows"][0]["vapor_pressure_source_report"]
    assert source_report["summary"]["builtin_authoritative"]["count"] == 1


def test_run_case_rejects_builtin_fallback_source_report(tmp_path, monkeypatch):
    session = _FakeSession(source="builtin_fallback")
    monkeypatch.setattr(driver, "_start_session", lambda **kwargs: session)
    monkeypatch.setattr(driver, "_apply_pending_decision", lambda session: False)

    with pytest.raises(driver.GrindSourceGateError, match="builtin_fallback"):
        driver._run_case(
            feedstock="lunar_mare_low_ti",
            campaign="C2A_continuous",
            backend_name="alphamelts",
            mass_kg=1000.0,
            additives_kg={},
            hours=1,
            wall_cap_s=60.0,
            db_path=tmp_path / "case.db",
            mode="capture",
            disable_live=False,
            allow_stub_equilibrium=False,
        )


def test_run_case_enforces_wall_cap_during_advance(tmp_path, monkeypatch):
    session = _FakeSession(source="builtin_authoritative")
    advance_started = False

    def slow_advance():
        nonlocal advance_started
        advance_started = True
        time.sleep(1.0)
        return _FakeStep()

    session.advance = slow_advance
    monkeypatch.setattr(driver, "_start_session", lambda **kwargs: session)
    monkeypatch.setattr(driver, "_apply_pending_decision", lambda session: False)
    monkeypatch.setattr(
        driver,
        "_timed_magemin_dispatch",
        lambda timings: contextlib.nullcontext(),
    )

    started = time.perf_counter()
    with pytest.raises(driver.WallCapExceeded, match="exceeded its wall cap"):
        driver._run_case(
            feedstock="lunar_mare_low_ti",
            campaign="C2A_continuous",
            backend_name="alphamelts",
            mass_kg=1000.0,
            additives_kg={},
            hours=1,
            wall_cap_s=0.02,
            db_path=tmp_path / "case.db",
            mode="capture",
            disable_live=False,
            allow_stub_equilibrium=False,
        )

    assert advance_started is True
    assert time.perf_counter() - started < 0.5


class _FakeSnapshot:
    temperature_C = 1600.0
    mass_balance_error_pct = 0.0


class _FakeStep:
    snapshot = _FakeSnapshot()
    backend_error = None


class _FakeCampaign:
    name = "C2A_continuous"


class _FakeMelt:
    campaign = _FakeCampaign()
    campaign_hour = 1.0


class _FakeSimulator:
    melt = _FakeMelt()

    def __init__(self, *, source):
        self._last_vapor_pressures_source = {"Na": source}

    def product_ledger(self):
        return {}


class _FakeSession:
    def __init__(self, *, source):
        self.simulator = _FakeSimulator(source=source)

    def is_complete(self):
        return False

    def advance(self):
        return _FakeStep()


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
