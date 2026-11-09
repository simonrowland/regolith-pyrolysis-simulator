from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from simulator.battery.residue import _make_residue_pressure_model
from simulator.engine_cache import EngineCache


def _openimcc_bridge_result() -> Any:
    from simulator.melt_backend.imcc_adapter_labels import ImccAdapterLabels
    from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult

    return OpenImccBridgeResult(
        parent_oxide_activities=MappingProxyType({"SiO2": 0.5}),
        parent_oxides=("SiO2",),
        flags=(),
        notices=(),
        acid_sink_ratio=None,
        pack_model_id="fixture-model",
        pack_version="fixture-v1",
        pack_digest="fixture-digest",
        openimcc_version="fixture-openimcc",
        envelope_status="inside",
        extrapolated=False,
        labels=ImccAdapterLabels(
            identity=MappingProxyType({"model_id": "fixture-model"}),
            coverage=MappingProxyType({"SiO2": "supported"}),
            trust="internal-analytical",
            envelope_status="inside",
            flags=(),
            notices=(),
        ),
        coverage=MappingProxyType({"SiO2": "supported"}),
        activity_coefficients=MappingProxyType({}),
        parent_oxide_x_star_ratios=MappingProxyType({}),
    )


def test_openimcc_residue_gas_calls_are_cached_across_pressure_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openimcc = pytest.importorskip("openimcc")
    import simulator.battery.residue as residue

    engine_calls = {"bridge": 0, "gas": 0}

    def evaluate_gas(*_args: Any, **_kwargs: Any) -> dict[str, float]:
        engine_calls["gas"] += 1
        return {"SiO(g)": 0.25}

    def evaluate_bridge(*_args: Any, **_kwargs: Any) -> Any:
        engine_calls["bridge"] += 1
        return _openimcc_bridge_result()

    monkeypatch.setattr(openimcc, "evaluate_gas", evaluate_gas)
    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", evaluate_bridge)
    gas_pack = SimpleNamespace(
        _gas_digest="gas-content-digest",
        _condensate_digest="condensate-content-digest",
    )
    common = {
        "engine": "openimcc",
        "gas_channels": (("SiO(g)", ("SiO2", 1.0, 0.0)),),
        "gas_pack": gas_pack,
        "temperature_K": 1703.0,
        "total_pressure_Pa": 100_000.0,
        "engine_metadata": {},
        "engine_flags": set(),
        "missing_channels_reason": "test",
        "caller": "cache test",
    }
    cache_path = tmp_path / "residue-openimcc.sqlite"
    cold_cache = EngineCache(cache_path)
    cold_model = _make_residue_pressure_model(
        **common, engine_cache=cold_cache
    )
    cold = cold_model({"SiO2": 1.0}, -10.0)

    warm_cache = EngineCache(cache_path)
    warm_model = _make_residue_pressure_model(
        **common, engine_cache=warm_cache
    )
    warm = warm_model({"SiO2": 1.0}, -10.0)

    assert cold == warm
    assert engine_calls == {"bridge": 1, "gas": 1}
    assert not any(warm_cache.counters["openimcc"]["calls_by_kind"].values())


def test_internal_analytical_residue_calls_are_cached_across_pressure_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    calls = 0

    def evaluate_vapor_pressure(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return SimpleNamespace(vapor_pressures_Pa={"SiO(g)": 5.0})

    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", evaluate_vapor_pressure
    )
    monkeypatch.setattr(
        battery, "_gas_formula_for_oxygen_balance", lambda channel: channel
    )
    common = {
        "engine": "internal-analytical",
        "gas_channels": (("SiO(g)", ("SiO2", 1.0, 0.0)),),
        "gas_pack": None,
        "temperature_K": 1703.0,
        "total_pressure_Pa": 100_000.0,
        "engine_metadata": {},
        "engine_flags": set(),
        "missing_channels_reason": "test",
        "caller": "cache test",
    }
    cache_path = tmp_path / "residue-internal.sqlite"
    cold_cache = EngineCache(cache_path)
    cold_model = _make_residue_pressure_model(
        **common, engine_cache=cold_cache
    )
    cold = cold_model({"SiO2": 1.0}, -10.0)

    warm_cache = EngineCache(cache_path)
    warm_model = _make_residue_pressure_model(
        **common, engine_cache=warm_cache
    )
    warm = warm_model({"SiO2": 1.0}, -10.0)

    assert cold == warm
    assert calls == 1
    assert not any(
        warm_cache.counters["internal-analytical"]["calls_by_kind"].values()
    )
