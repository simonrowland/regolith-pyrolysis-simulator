from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import pytest

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
from simulator.melt_backend.base import EquilibriumResult, StubBackend
from simulator.reduced_real_determinism import (
    PT0CacheCollision,
    PT0CacheMiss,
    PT0DeterminismStore,
    canonical_replay_key,
)
from simulator.state import CampaignPhase


class _FakeLiveRealBackend:
    name = "fake-live-real"
    engine_version = "fake-live-real-v1"
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
    engine_version = "alphamelts-test-v1"


def _cache_config(
    db_path: Path,
    miss_policy: str,
    *,
    name: str = _FakeLiveRealBackend.name,
    version: str = _FakeLiveRealBackend.engine_version,
) -> dict[str, str]:
    return {
        "db_path": str(db_path),
        "miss_policy": miss_policy,
        "authorized_backend_name": name,
        "authorized_backend_version": version,
    }


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


def test_cached_real_resolver_requires_cache_config() -> None:
    with pytest.raises(BackendUnavailableError, match="cached-real requires"):
        resolve_backend("cached-real", BackendSelectionPolicy.RUNNER_STRICT)


def test_cached_real_resolver_returns_non_stub_backend(tmp_path: Path) -> None:
    backend = resolve_backend(
        "cached-real",
        BackendSelectionPolicy.RUNNER_STRICT,
        cached_real_config=_cache_config(tmp_path / "cache.db", "fail-loud"),
    )

    assert isinstance(backend, CachedRealBackend)
    assert not isinstance(backend, StubBackend)


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
        "cached_interpolated",
        "live_fill",
    )


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
        version="other-v1",
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
        version="other-v1",
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


def test_cached_real_fail_loud_miss_never_calls_stub(tmp_path: Path) -> None:
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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
        version="alphamelts-test-v1",
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

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version=AlphaMELTSBackend.engine_version,
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


def test_cached_real_direct_alphamelts_version_mismatch_misses(
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

    replay_config = _cache_config(
        db_path,
        "fail-loud",
        name="alphamelts",
        version="alphamelts-test-v2",
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
    with pytest.raises(PT0CacheMiss):
        replay_sim._pt0_store().replay_gate_curve(
            replay_sim,
            fO2_log=replay_sim._compute_intrinsic_melt_fO2(),
        )


def test_cached_real_refuses_authoritative_gate_cache_for_fallback_curve(
    tmp_path: Path,
) -> None:
    cache_config = _cache_config(
        tmp_path / "cached-real.db",
        "live-fill",
        name="alphamelts",
        version="alphamelts-test-v1",
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
