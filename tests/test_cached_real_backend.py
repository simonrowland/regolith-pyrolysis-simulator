from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pytest

from scripts import populate_reduced_real_cache as populate_driver
from simulator.backends import (
    BackendSelectionPolicy,
    BackendUnavailableError,
    CachedRealBackend,
    SimulatorBuildConfig,
    build_cached_real_store,
    build_simulator,
    normalize_cached_real_config,
    resolve_backend,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.config import load_config_bundle
from simulator.corpus_version import CorpusVersionConfigError, current_corpus_version
from simulator.melt_backend.base import EquilibriumResult, InternalAnalyticalBackend
from simulator.reduced_real_determinism import (
    ControlQuantization,
    PT0CacheCollision,
    PT0CacheMiss,
    PT0DeterminismStore,
    PT1_EQUILIBRIUM_TABLE,
    canonical_json_bytes,
    canonical_replay_key,
    equilibrium_payload,
)
from simulator.state import CampaignPhase


class _FakeLiveRealBackend:
    name = "fake-live-real"
    engine_version = "fake-live-real 1.0.0"
    model = "MELTSv1.0.2"
    mode = "subprocess"
    last_instance: "_FakeLiveRealBackend | None" = None

    def __init__(self) -> None:
        type(self).last_instance = self
        self.calls = 0
        self.composition_mol_by_account = None

    def initialize(self, _config: Mapping[str, Any] | None = None) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, bool]:
        return {"silicate_melt": True, "gas_volatiles": False}

    def get_engine_version(self) -> str:
        return self.engine_version

    def equilibrate(
        self,
        *,
        temperature_C: float,
        composition_mol: Mapping[str, float],
        species_formula_registry: Mapping[str, str],
        fO2_log: float,
        pressure_bar: float,
        composition_mol_by_account: Mapping[str, Mapping[str, float]] | None = None,
        **_kwargs: Any,
    ) -> EquilibriumResult:
        self.calls += 1
        self.composition_mol_by_account = composition_mol_by_account
        return EquilibriumResult(
            temperature_C=float(temperature_C),
            pressure_bar=float(pressure_bar),
            liquid_fraction=1.0,
            phase_assemblage_available=True,
            vapor_pressures_Pa={"SiO": 12.0},
            fO2_log=float(fO2_log),
            warnings=["fake live real backend"],
        )


class AlphaMELTSBackend(_FakeLiveRealBackend):
    name = "alphamelts"
    engine_version = "alphamelts-test 1.0.0"

    def initialize(self, _config: Mapping[str, Any] | None = None) -> bool:
        self._model = self.model
        self._mode = self.mode
        return super().initialize(_config)


class _AlphaMELTSClusterIdentityBackend(_FakeLiveRealBackend):
    name = "alphamelts"
    engine_version = (
        "alphamelts2 2.3.1 (server=studio-a) "
        "(path=/opt/alphamelts-app-2.3.1-macos-arm64/alphamelts2) "
        "(digest=sha256:abc123)"
    )


def _cache_config(
    db_path: Path,
    miss_policy: str,
    *,
    name: str = _FakeLiveRealBackend.name,
    version: str = _FakeLiveRealBackend.engine_version,
    model: str | None = None,
    mode: str | None = None,
) -> dict[str, str]:
    config = {
        "db_path": str(db_path),
        "miss_policy": miss_policy,
        "authorized_backend_name": name,
        "authorized_backend_version": version,
    }
    if model is not None:
        config["authorized_model"] = model
    if mode is not None:
        config["authorized_mode"] = mode
    return config


def _key_hash(key: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(key)).hexdigest()


def _restamp_first_pt1_row_corpus(db_path: Path, corpus_version: str) -> None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT key_hash, key_bytes FROM {PT1_EQUILIBRIUM_TABLE} LIMIT 1"
        ).fetchone()
        key = json.loads(row[1].decode("utf-8"))
        key["corpus_version"] = corpus_version
        backend = key.get("backend")
        if isinstance(backend, dict):
            backend["corpus_version"] = corpus_version
        provider = key.get("provider")
        if isinstance(provider, dict) and "corpus_version" in provider:
            provider["corpus_version"] = corpus_version
        key_bytes = canonical_json_bytes(key)
        key_hash = hashlib.sha256(key_bytes).hexdigest()
        conn.execute(
            f"""
            UPDATE {PT1_EQUILIBRIUM_TABLE}
            SET key_hash = ?,
                key_sha256 = ?,
                key_bytes = ?,
                corpus_version = ?
            WHERE key_hash = ?
            """,
            (key_hash, key_hash, key_bytes, corpus_version, row[0]),
        )


def _write_corpus_version_config(
    path: Path,
    *,
    corpus_version: str,
    interoperable_versions: tuple[str, ...],
) -> None:
    lines = [f"corpus_version: {corpus_version}", "interoperable_versions:"]
    lines.extend(f"  - {version}" for version in interoperable_versions)
    path.write_text("\n".join(lines) + "\n")


def test_cached_real_config_threads_strict_vapor_gate_to_store(
    tmp_path: Path,
) -> None:
    normalized = normalize_cached_real_config(
        {
            **_cache_config(tmp_path / "cached-real.db", "live-fill"),
            "strict_vapor_gate": True,
        }
    )

    store = build_cached_real_store(normalized)

    assert store.strict_vapor_gate is True
    assert store.persistent_store is not None
    assert store.persistent_store.strict_vapor_gate is True


def test_cached_real_config_defaults_model_mode_for_alphamelts_alias(
    tmp_path: Path,
) -> None:
    normalized = normalize_cached_real_config(
        _cache_config(
            tmp_path / "cached-real.db",
            "live-fill",
            name="AlphaMELTSBackend",
        )
    )

    assert normalized.authorized_model == "MELTSv1.0.2"
    assert normalized.authorized_mode == "subprocess"
    assert resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=normalized,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    ).config == normalized


def test_cached_real_config_threads_control_quantization_to_store(
    tmp_path: Path,
) -> None:
    normalized = normalize_cached_real_config(
        {
            **_cache_config(tmp_path / "cached-real.db", "live-fill"),
            "control_quantization": "xx-coarse",
        }
    )

    store = build_cached_real_store(normalized)

    assert normalized.control_quantization == ControlQuantization.from_name(
        "xx_coarse"
    )
    assert store.control_quantization == ControlQuantization.from_name("xx_coarse")
    assert store.persistent_store is not None
    assert store.persistent_store.control_quantization == (
        ControlQuantization.from_name("xx_coarse")
    )


def test_cached_real_config_parses_control_quantization_json_dict(
    tmp_path: Path,
) -> None:
    normalized = normalize_cached_real_config(
        {
            **_cache_config(tmp_path / "cached-real.db", "live-fill"),
            "control_quantization": {
                "t_k_quantum": 2.0,
                "pressure_bar_quantum": 0.002,
                "log_fo2_quantum": 0.02,
                "composition_sig_figs": 3,
            },
        }
    )

    assert normalized.control_quantization == ControlQuantization(
        t_k_quantum=2.0,
        pressure_bar_quantum=0.002,
        log_fo2_quantum=0.02,
        composition_sig_figs=3,
    )


def test_cached_real_config_rejects_bad_control_quantization(
    tmp_path: Path,
) -> None:
    with pytest.raises(BackendUnavailableError, match="unknown control quantization"):
        normalize_cached_real_config(
            {
                **_cache_config(tmp_path / "cached-real.db", "live-fill"),
                "control_quantization": "bad-tier",
            }
        )


def _build_cached_real_sim(
    *,
    backend: CachedRealBackend,
    cache_config: Mapping[str, Any],
):
    bundle = load_config_bundle()
    setpoints = copy.deepcopy(bundle.setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
        )
    )
    normalized = normalize_cached_real_config(cache_config)
    sim.configure_pt0_determinism_store(build_cached_real_store(normalized))
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    return sim


def _build_direct_real_sim(backend, *, db_path: Path | None = None):
    bundle = load_config_bundle()
    setpoints = copy.deepcopy(bundle.setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    kernel_config["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_config
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
        )
    )
    sim.configure_pt0_determinism_store(
        PT0DeterminismStore("capture", db_path=db_path)
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    return sim


def test_cached_real_replay_key_matches_live_alphamelts_identity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cached-real.db"
    live_backend = AlphaMELTSBackend()
    live_backend._model = live_backend.model
    live_backend._mode = live_backend.mode
    live_sim = _build_direct_real_sim(live_backend, db_path=db_path)
    live_key = canonical_replay_key(
        live_sim,
        artifact="equilibrium_post_record",
        intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
        fO2_log=None,
        fe_redox_policy="intrinsic",
    )

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version=live_backend.engine_version,
        model=live_backend._model,
        mode=live_backend._mode,
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )
    replay_key = canonical_replay_key(
        replay_sim,
        artifact="equilibrium_post_record",
        intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
        fO2_log=None,
        fe_redox_policy="intrinsic",
    )

    assert live_key["provider"] == replay_key["provider"]
    assert live_key["model"] == replay_key["model"]
    assert _key_hash(live_key) == _key_hash(replay_key)


def test_cached_real_resolver_requires_cache_config() -> None:
    with pytest.raises(BackendUnavailableError, match="cached-real requires"):
        resolve_backend("cached-real", BackendSelectionPolicy.RUNNER_STRICT)


def test_cached_real_resolver_returns_non_internal_analytical_backend(tmp_path: Path) -> None:
    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=_cache_config(tmp_path / "cache.db", "fail-loud"),
    )

    assert isinstance(backend, CachedRealBackend)
    assert not isinstance(backend, InternalAnalyticalBackend)


def test_cached_real_live_fill_populates_then_fail_loud_hits(tmp_path: Path) -> None:
    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )

    live_result = live_sim._get_equilibrium()

    assert _FakeLiveRealBackend.last_instance is not None
    assert _FakeLiveRealBackend.last_instance.calls == 1
    assert live_result.status == "ok"
    assert live_sim._last_reduced_real_cache_state == "live_fill"

    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    replay_result = replay_sim._get_equilibrium()

    assert replay_result.status == live_result.status
    assert replay_result.liquid_fraction == live_result.liquid_fraction
    assert replay_sim._last_reduced_real_cache_state == "cached_exact"
    summary = replay_sim._pt0_store().summary()
    counts = summary["cache_state_counts_by_artifact"]["equilibrium_post_record"]
    assert counts["cached_exact"] == 1
    assert counts["live_fill"] == 0
    assert summary["misses"] == 0
    assert summary["cache_states"] == (
        "cached_exact",
        "cached_physics_bucket",
        "cached_interpolated",
        "live_fill",
    )


def test_cached_real_row_outside_interoperable_corpus_misses(tmp_path: Path) -> None:
    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_sim._get_equilibrium()
    _restamp_first_pt1_row_corpus(db_path, "analytical-corpus-not-interoperable")

    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    with pytest.raises(PT0CacheMiss):
        replay_sim._get_equilibrium()


def test_cached_real_row_inside_interoperable_corpus_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = "analytical-corpus-current-test"
    previous = "analytical-corpus-previous-test"
    corpus_config = tmp_path / "corpus-version.yaml"
    _write_corpus_version_config(
        corpus_config,
        corpus_version=current,
        interoperable_versions=(current, previous),
    )
    monkeypatch.setenv("REGOLITH_CORPUS_VERSION_FILE", str(corpus_config))

    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_result = live_sim._get_equilibrium()
    _restamp_first_pt1_row_corpus(db_path, previous)

    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    replay_result = replay_sim._get_equilibrium()

    assert replay_result.status == live_result.status
    assert replay_sim._last_reduced_real_cache_state == "cached_exact"
    assert replay_sim._pt0_store().summary()["misses"] == 0


def test_cached_real_replay_ignores_code_version_with_same_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cached-real.db"
    monkeypatch.setattr(
        "simulator.reduced_real_determinism._code_version",
        lambda: "public-app-v1",
    )
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_result = live_sim._get_equilibrium()
    live_key = live_sim._pt0_store().capture_sequence[-1]["key"]

    with sqlite3.connect(db_path) as conn:
        row_code_version = conn.execute(
            f"SELECT code_version FROM {PT1_EQUILIBRIUM_TABLE}"
        ).fetchone()[0]

    monkeypatch.setattr(
        "simulator.reduced_real_determinism._code_version",
        lambda: "public-app-v2",
    )
    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    replay_result = replay_sim._get_equilibrium()

    assert "code_version" not in live_key
    assert row_code_version == "public-app-v1"
    assert replay_result.status == live_result.status
    assert replay_sim._last_reduced_real_cache_state == "cached_exact"
    assert replay_sim._pt0_store().summary()["misses"] == 0


def test_cached_real_corpus_version_change_invalidates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_config = tmp_path / "corpus-version.yaml"
    _write_corpus_version_config(
        corpus_config,
        corpus_version="analytical-corpus-before",
        interoperable_versions=("analytical-corpus-before",),
    )
    monkeypatch.setenv("REGOLITH_CORPUS_VERSION_FILE", str(corpus_config))

    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_sim._get_equilibrium()

    _write_corpus_version_config(
        corpus_config,
        corpus_version="analytical-corpus-after",
        interoperable_versions=("analytical-corpus-after",),
    )
    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    with pytest.raises(PT0CacheMiss):
        replay_sim._get_equilibrium()


def test_cached_real_cache_key_uses_corpus_not_engine_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(
        db_path,
        "live-fill",
        version=(
            "fake-live-real 1.0.0 (server=studio-a) "
            "(path=/opt/grind/fake-live-real) (digest=sha256:abc123)"
        ),
    )
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )

    live_result = live_sim._get_equilibrium()
    live_key = live_sim._pt0_store().capture_sequence[-1]["key"]

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        version=(
            "fake-live-real 1.0.0 (server=mac-studio-b) "
            "(path=/Volumes/grind/fake-live-real) (digest=sha256:stale)"
        ),
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    replay_result = replay_sim._get_equilibrium()

    assert live_key["corpus_version"] == current_corpus_version()
    assert live_key["backend"]["corpus_version"] == current_corpus_version()
    assert "engine_version" not in live_key
    assert "backend_version" not in live_key["backend"]
    assert live_result.status == replay_result.status
    assert replay_sim._last_reduced_real_cache_state == "cached_exact"
    assert replay_sim._pt0_store().summary()["misses"] == 0


def test_cached_real_live_fill_accepts_engine_upgrade_with_same_corpus(
    tmp_path: Path,
) -> None:
    config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version=(
            "alphamelts2 2.3.2 (server=macbook) "
            "(path=/Users/simon/alphamelts-app-2.3.1-macos-arm64/alphamelts2) "
            "(digest=sha256:stale)"
        ),
    )

    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=config,
        cached_real_live_backend_cls=_AlphaMELTSClusterIdentityBackend,
    )

    assert isinstance(backend, CachedRealBackend)


def test_cached_real_live_fill_accepts_path_only_engine_provenance(
    tmp_path: Path,
) -> None:
    config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version=(
            "alphamelts2 2.3.2 (server=studio-a) "
            "(path=/opt/alphamelts-app-2.3.1-macos-arm64/alphamelts2) "
            "(digest=sha256:stale)"
        ),
    )

    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=config,
        cached_real_live_backend_cls=_AlphaMELTSClusterIdentityBackend,
    )

    assert isinstance(backend, CachedRealBackend)


def test_cached_real_missing_corpus_version_fails_loud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_config = tmp_path / "missing-corpus.yaml"
    monkeypatch.setenv("REGOLITH_CORPUS_VERSION_FILE", str(missing_config))
    config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts2 (/opt/alphamelts2)",
    )

    with pytest.raises(CorpusVersionConfigError, match="corpus version config missing"):
        resolve_backend(
            "cached-real",
            BackendSelectionPolicy.RUNNER_STRICT,
            cached_real_config=config,
            cached_real_live_backend_cls=_AlphaMELTSClusterIdentityBackend,
        )


def test_cached_real_replays_row_written_by_populate_driver_store(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "populate-driver.db"
    live_backend = _FakeLiveRealBackend()
    live_sim = _build_direct_real_sim(live_backend)

    live_result = live_sim._get_equilibrium()
    replay_config = _cache_config(db_path, "fail-loud")
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )
    artifact = "equilibrium_post_record"
    key = replay_sim._pt0_store()._equilibrium_key(replay_sim)
    payload = equilibrium_payload(live_sim, live_result)
    key_bytes = canonical_json_bytes(key)
    payload_bytes = canonical_json_bytes(payload)
    populate_driver.PT1PersistentEquilibriumStore(db_path).put(
        artifact=artifact,
        key=key,
        key_bytes=key_bytes,
        key_hash=hashlib.sha256(key_bytes).hexdigest(),
        payload=payload,
        payload_bytes=payload_bytes,
        payload_hash=hashlib.sha256(payload_bytes).hexdigest(),
    )

    replay_result = replay_sim._get_equilibrium()

    assert artifact == "equilibrium_post_record"
    assert live_backend.calls == 1
    assert replay_sim._last_reduced_real_cache_state == "cached_exact"
    assert replay_result.status == live_result.status
    assert replay_result.vapor_pressures_Pa == live_result.vapor_pressures_Pa


def test_cached_real_authorized_backend_identity_partitions_cache(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(db_path, "live-fill")
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_sim._get_equilibrium()

    other_config = _cache_config(
        db_path,
        "fail-loud",
        name="other-live-real",
        version="other-live-real 1.0.0",
    )
    other_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=other_config,
    )
    other_sim = _build_cached_real_sim(
        backend=other_backend,
        cache_config=other_config,
    )

    with pytest.raises(PT0CacheMiss):
        other_sim._get_equilibrium()


def test_cached_real_live_fill_rejects_identity_mismatch(tmp_path: Path) -> None:
    cache_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="other-live-real",
        version="other-live-real 1.0.0",
    )

    with pytest.raises(BackendUnavailableError, match="identity mismatch"):
        resolve_backend(
            "cached-real",
            BackendSelectionPolicy.RUNNER_STRICT,
            cached_real_config=cache_config,
            cached_real_live_backend_cls=_FakeLiveRealBackend,
        )


def test_cached_real_live_fill_forwards_account_scoped_composition(
    tmp_path: Path,
) -> None:
    direct_backend = _FakeLiveRealBackend()
    direct_sim = _build_direct_real_sim(direct_backend)
    direct_sim._get_equilibrium()
    direct_accounts = direct_backend.composition_mol_by_account

    assert direct_accounts is not None
    assert "process.cleaned_melt" in direct_accounts

    db_path = tmp_path / "cached-real.db"
    cache_config = _cache_config(db_path, "live-fill")
    cached_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=cache_config,
        cached_real_live_backend_cls=_FakeLiveRealBackend,
    )
    cached_sim = _build_cached_real_sim(
        backend=cached_backend,
        cache_config=cache_config,
    )
    cached_sim._get_equilibrium()

    assert _FakeLiveRealBackend.last_instance is not None
    assert (
        _FakeLiveRealBackend.last_instance.composition_mol_by_account
        == direct_accounts
    )


def test_cached_real_fail_loud_miss_never_calls_internal_analytical(tmp_path: Path) -> None:
    cache_config = _cache_config(tmp_path / "empty.db", "fail-loud")
    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=cache_config,
    )
    sim = _build_cached_real_sim(backend=backend, cache_config=cache_config)

    with pytest.raises(PT0CacheMiss):
        sim._get_equilibrium()


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(provider.capability_profile().provider_id)


def _authoritative_gate_curve() -> dict[str, Any]:
    return {
        "source": "gate_liquid_fraction",
        "solidus_T_C": 1210.0,
        "liquidus_T_C": 1320.0,
        "path": ((1210.0, 0.0), (1320.0, 1.0)),
    }


def test_cached_real_unwraps_live_alphamelts_for_provider_registration(
    tmp_path: Path,
) -> None:
    cache_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    cached_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=cache_config,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    )
    cached_sim = _build_cached_real_sim(
        backend=cached_backend,
        cache_config=cache_config,
    )
    direct_sim = _build_direct_real_sim(AlphaMELTSBackend())

    cached_sim._register_freeze_gate_liquid_fraction_providers()
    direct_sim._register_freeze_gate_liquid_fraction_providers()

    for intent in (
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    ):
        assert _provider_id(
            cached_sim._chem_registry.authoritative_for(intent)
        ) == _provider_id(direct_sim._chem_registry.authoritative_for(intent))


def test_cached_real_equilibrium_key_uses_alphamelts_provider_identity(
    tmp_path: Path,
) -> None:
    live_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    result = EquilibriumResult(
        status="ok",
        temperature_C=float(live_sim.melt.temperature_C),
        pressure_bar=float(live_sim.melt.p_total_mbar) / 1000.0,
        liquid_fraction=1.0,
        phase_assemblage_available=True,
        vapor_pressures_Pa={"SiO": 12.0},
        fO2_log=-10.0,
    )
    live_sim._pt0_store().capture_equilibrium(live_sim, result)
    live_key = live_sim._pt0_store().capture_sequence[-1]["key"]

    replay_config = _cache_config(
        live_config["db_path"],
        "fail-loud",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )
    replay_key = canonical_replay_key(
        replay_sim,
        artifact="equilibrium_post_record",
        intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
        fO2_log=None,
        fe_redox_policy="intrinsic",
    )

    assert live_key["provider"]["resolved_provider_id"] == "alphamelts-diagnostic"
    assert live_key["provider"] == replay_key["provider"]
    assert live_key["intent"] == replay_key["intent"]
    assert live_key["backend"] == replay_key["backend"]


def test_cached_real_gate_curve_key_uses_alphamelts_authoritative_provenance(
    tmp_path: Path,
) -> None:
    live_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    live_key = canonical_replay_key(
        live_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=-10.0,
        fe_redox_policy="intrinsic",
    )

    replay_config = _cache_config(
        live_config["db_path"],
        "fail-loud",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )
    replay_key = canonical_replay_key(
        replay_sim,
        artifact="freeze_gate_curve",
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        fO2_log=-10.0,
        fe_redox_policy="intrinsic",
    )

    assert live_key["provider"]["resolved_provider_id"] == (
        "alphamelts-diagnostic"
    )
    assert live_key["provider"]["resolved_role"] == "authoritative"
    assert live_key["provider"] == replay_key["provider"]
    assert live_key["backend"] == replay_key["backend"]


def test_cached_real_authoritative_gate_curve_live_fill_replay_hits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cached-real.db"
    live_config = _cache_config(
        db_path,
        "live-fill",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    live_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=live_config,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    )
    live_sim = _build_cached_real_sim(
        backend=live_backend,
        cache_config=live_config,
    )
    fO2_log = live_sim._compute_intrinsic_melt_fO2()
    curve = _authoritative_gate_curve()

    live_sim._pt0_store().capture_gate_curve(
        live_sim,
        fO2_log=fO2_log,
        curve=curve,
    )
    live_key = live_sim._pt0_store().capture_sequence[-1]["key"]

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )
    replay_fO2_log = replay_sim._compute_intrinsic_melt_fO2()

    assert replay_sim._pt0_store().replay_gate_curve(
        replay_sim,
        fO2_log=replay_fO2_log,
    ) == curve
    replay_key = replay_sim._pt0_store().replay_sequence[-1]["key"]

    assert replay_key == live_key
    assert live_key["provider"]["resolved_role"] == "authoritative"
    assert replay_key["provider"]["resolved_role"] == "authoritative"
    assert replay_sim._pt0_store().last_cache_state == "cached_exact"
    assert replay_sim._pt0_store().summary()["misses"] == 0


def test_cached_real_skips_direct_alphamelts_unavailable_equilibrium_cache(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cached-real.db"
    direct_sim = _build_direct_real_sim(
        AlphaMELTSBackend(),
        db_path=db_path,
    )
    direct_result = direct_sim._get_equilibrium()
    fO2_log = direct_sim._compute_intrinsic_melt_fO2()
    curve = _authoritative_gate_curve()
    direct_sim._pt0_store().capture_gate_curve(
        direct_sim,
        fO2_log=fO2_log,
        curve=curve,
    )
    direct_summary = direct_sim._pt0_store().summary()

    assert direct_result.status == "unavailable"
    assert "equilibrium_post_record" not in direct_summary[
        "capture_calls_by_artifact"
    ]
    direct_gate_key = direct_sim._pt0_store().capture_sequence[-1]["key"]

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version=AlphaMELTSBackend.engine_version,
        model=direct_gate_key["model"]["model"],
        mode=direct_gate_key["model"]["mode"],
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    with pytest.raises(PT0CacheMiss):
        replay_sim._get_equilibrium()
    replay_curve = replay_sim._pt0_store().replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    )

    assert replay_curve == curve
    summary = replay_sim._pt0_store().summary()
    assert "equilibrium_post_record" not in summary[
        "cache_state_counts_by_artifact"
    ]
    assert (
        summary["cache_state_counts_by_artifact"]["freeze_gate_curve"][
            "cached_exact"
        ]
        == 1
    )
    assert summary["misses"] == 1


def test_direct_alphamelts_fallback_gate_curve_replay_exact_hits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "direct-alphamelts-fallback-gate.db"
    direct_sim = _build_direct_real_sim(
        AlphaMELTSBackend(),
        db_path=db_path,
    )
    fallback_curve = {
        **_authoritative_gate_curve(),
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
    }

    direct_sim._pt0_store().capture_gate_curve(
        direct_sim,
        fO2_log=direct_sim._compute_intrinsic_melt_fO2(),
        curve=fallback_curve,
    )
    live_key = direct_sim._pt0_store().capture_sequence[-1]["key"]

    replay_sim = _build_direct_real_sim(AlphaMELTSBackend())
    replay_store = PT0DeterminismStore("replay", db_path=db_path)
    replay_sim.configure_pt0_determinism_store(replay_store)
    replay_curve = replay_store.replay_gate_curve(
        replay_sim,
        fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
    )
    replay_key = replay_store.replay_sequence[-1]["key"]
    replay_summary = replay_store.summary()

    assert replay_curve == fallback_curve
    assert live_key["provider"]["resolved_role"] == "fallback"
    assert live_key["provider"]["resolved_provider_id"] == "magemin-shadow"
    assert replay_key == live_key
    assert replay_summary["misses"] == 0
    assert replay_summary["cache_state_counts_by_artifact"]["freeze_gate_curve"][
        "cached_exact"
    ] == 1


def test_cached_real_direct_alphamelts_gate_curve_engine_change_misses_same_corpus(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cached-real.db"
    direct_sim = _build_direct_real_sim(
        AlphaMELTSBackend(),
        db_path=db_path,
    )
    direct_sim._get_equilibrium()
    direct_sim._pt0_store().capture_gate_curve(
        direct_sim,
        fO2_log=direct_sim._compute_intrinsic_melt_fO2(),
        curve=_authoritative_gate_curve(),
    )
    direct_gate_key = direct_sim._pt0_store().capture_sequence[-1]["key"]

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version="alphamelts-test 2.0.0",
        model=direct_gate_key["model"]["model"],
        mode=direct_gate_key["model"]["mode"],
    )
    replay_backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=replay_config,
    )
    replay_sim = _build_cached_real_sim(
        backend=replay_backend,
        cache_config=replay_config,
    )

    with pytest.raises(PT0CacheMiss):
        replay_sim._pt0_store().replay_gate_curve(
            replay_sim,
            fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
        )
    assert replay_sim._pt0_store().summary()["misses"] == 1


def test_cached_real_refuses_authoritative_gate_cache_for_fallback_curve(
    tmp_path: Path,
) -> None:
    cache_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts-test 1.0.0",
    )
    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=cache_config,
        cached_real_live_backend_cls=AlphaMELTSBackend,
    )
    sim = _build_cached_real_sim(backend=backend, cache_config=cache_config)
    curve = {
        **_authoritative_gate_curve(),
        "source": "gate_liquid_fraction:fallback:magemin-shadow",
    }

    with pytest.raises(PT0CacheCollision, match="provider role mismatch"):
        sim._pt0_store().capture_gate_curve(
            sim,
            fO2_log=sim._compute_intrinsic_melt_fO2(),
            curve=curve,
        )
