from __future__ import annotations

import json
import importlib.util
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import pytest

from simulator.battery.enums import AmountBasis, Engine, EvidenceClass, Rail
from simulator.battery.records import Composition
from simulator.battery.score import (
    ScoreContext,
    _score_store_to_jsonl,
    is_store_stamp_payload,
)
from simulator.diagnostic_helpers.binary_pot_battery import (
    close_battery_engine_handle,
    open_battery_engine,
)
from simulator.engine_cache import EngineCache
from tests.battery import factories as F


ROOT = Path(__file__).resolve().parents[2]
CACHE_TEST_ENGINES = (
    Engine.OPENIMCC,
    Engine.INTERNAL_ANALYTICAL,
    Engine.VAPOROCK,
    Engine.MAGEMIN,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
)


def _canonical_residual_rows(path: Path) -> bytes:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if is_store_stamp_payload(payload):
                continue
            rows.append(
                json.dumps(
                    payload,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
    return b"\n".join(rows) + (b"\n" if rows else b"")


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES)
def test_engine_availability_is_reported_per_engine(engine: Engine) -> None:
    handle = open_battery_engine(engine.value)
    try:
        if not handle.available:
            pytest.skip(f"{engine.value} unavailable: {handle.unavailable_reason}")
    finally:
        close_battery_engine_handle(handle)


def test_scored_fixture_rows_match_cache_off_cold_and_warm(tmp_path: Path) -> None:
    engines = []
    for engine in CACHE_TEST_ENGINES:
        handle = open_battery_engine(engine.value)
        try:
            if handle.available:
                engines.append(engine)
        finally:
            close_battery_engine_handle(handle)
    if not engines:
        pytest.skip("no battery engines are available in this seat")

    work = F.work()
    compositions = (
        Composition(
            basis="printed_mole_fraction",
            components=(("SiO2", Decimal("0.5")), ("Al2O3", Decimal("0.5"))),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
        Composition(
            basis="printed_mole_fraction",
            components=(("SiO2", Decimal("0.6")), ("FeO", Decimal("0.4"))),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
        Composition(
            basis="printed_mole_fraction",
            components=(("SiO2", Decimal("0.4")), ("MgO", Decimal("0.6"))),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
    )
    temperatures = (Decimal("1703"), Decimal("1711"), Decimal("1695"))
    pressures_Pa = (Decimal("100000"), Decimal("0.1"), Decimal("0.0001"))
    oxygen_Pa = (Decimal("1e-8"), Decimal("1e-7"), Decimal("1e-9"))
    experiments = {}
    observations = {}
    for index in range(12):
        variant = index % len(compositions)
        temperature = temperatures[variant]
        pressure = pressures_Pa[variant]
        composition = compositions[variant]
        experiment = F.tabulation_experiment(
            experiment_id=f"cache-equivalence-exp-{index:02d}",
            work_id=work.work_id,
            T_K=temperature,
            total_P=pressure,
        )
        observation = F.observation(
            f"cache-equivalence-activity-{index:02d}",
            experiment.experiment_id,
            F.activity_identity(
                formula="SiO2",
                T_K=temperature,
                component_basis="SiO2",
                composition=composition,
                fO2_Pa=oxygen_Pa[variant],
                total_P=pressure,
            ),
            Decimal("0.4") + Decimal(variant) / Decimal("10"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        )
        experiments[experiment.experiment_id] = experiment
        observations[observation.observation_id] = observation
    context = ScoreContext(
        works={work.work_id: work},
        experiments=experiments,
        observations=observations,
    )

    def score(
        name: str,
        cache: EngineCache | None = None,
        *,
        workers: int = 1,
    ) -> tuple[bytes, Mapping[str, Mapping[str, object]] | None]:
        path = tmp_path / f"{name}.jsonl"
        _row_count, metadata = _score_store_to_jsonl(
            context,
            path,
            root=ROOT,
            engines=engines,
            rail=Rail.MELT_ACTIVITY,
            limit=12,
            include_diagnostics=False,
            workers=workers,
            engine_cache=cache,
        )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters,
        )

    off, _off_counts = score("off")
    cache_path = tmp_path / "engine-cache.sqlite"
    cache_path.touch()
    cold_cache = EngineCache(cache_path)
    cold, _cold_counts = score("cold", cold_cache)
    warm_cache = EngineCache(cache_path)
    warm, warm_counts = score("warm", warm_cache, workers=1)
    parallel_warm_cache = EngineCache(cache_path)
    parallel_warm, parallel_counts = score(
        "parallel-warm", parallel_warm_cache, workers=3
    )

    assert off
    assert cold == off
    assert warm == off
    assert parallel_warm == warm == off
    assert warm_counts is not None
    assert parallel_counts is not None
    assert _cold_counts is not None
    assert all(
        sum(_cold_counts[engine.value]["calls_by_kind"].values()) > 0
        for engine in engines
    )
    assert all(
        warm_counts[engine.value]["misses"]
        == parallel_counts[engine.value]["misses"]
        == 0
        for engine in engines
    )
    assert all(
        not any(warm_counts[engine.value]["calls_by_kind"].values())
        and not any(parallel_counts[engine.value]["calls_by_kind"].values())
        for engine in engines
    )
    assert sum(warm_counts[engine.value]["hits"] for engine in engines) > 0
    assert warm_counts[Engine.OPENIMCC.value]["calls_by_kind"] == {
        "equilibrate_cell": 0,
        "evaluate": 0,
        "evaluate_redox": 0,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }
    assert parallel_counts[Engine.OPENIMCC.value]["calls_by_kind"] == {
        "equilibrate_cell": 0,
        "evaluate": 0,
        "evaluate_redox": 0,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }

    from tests.battery.test_redox_extract_scoring import (
        _sossi_aithala_redox_context,
    )

    redox_context, redox_observations = _sossi_aithala_redox_context()
    redox_observation_id = (
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    )
    redox_observation = redox_observations[redox_observation_id]
    redox_context = replace(
        redox_context,
        observations={redox_observation_id: redox_observation},
        origins={redox_observation_id: redox_context.origins[redox_observation_id]},
    )

    def score_redox(
        name: str,
        cache: EngineCache | None = None,
        *,
        workers: int = 1,
    ) -> tuple[bytes, Mapping[str, Mapping[str, object]] | None]:
        path = tmp_path / f"{name}.jsonl"
        _row_count, metadata = _score_store_to_jsonl(
            redox_context,
            path,
            root=ROOT,
            engines=(Engine.OPENIMCC,),
            rail=Rail.REDOX,
            limit=1,
            include_diagnostics=False,
            workers=workers,
            engine_cache=cache,
        )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters,
        )

    redox_off, _redox_off_counts = score_redox("redox-off")
    redox_cache_path = tmp_path / "redox-engine-cache.sqlite"
    redox_cache_path.touch()
    redox_cold_cache = EngineCache(redox_cache_path)
    redox_cold, redox_cold_counts = score_redox("redox-cold", redox_cold_cache)
    redox_warm_cache = EngineCache(redox_cache_path)
    redox_warm, redox_warm_counts = score_redox(
        "redox-warm", redox_warm_cache, workers=1
    )
    redox_parallel_cache = EngineCache(redox_cache_path)
    redox_parallel, redox_parallel_counts = score_redox(
        "redox-parallel-warm", redox_parallel_cache, workers=3
    )

    assert redox_off
    assert redox_cold == redox_off
    assert redox_warm == redox_off
    assert redox_parallel == redox_warm == redox_off
    assert redox_cold_counts is not None
    assert redox_warm_counts is not None
    assert redox_parallel_counts is not None
    assert redox_cold_counts[Engine.OPENIMCC.value]["calls_by_kind"]["evaluate_redox"] > 0
    assert redox_warm_counts[Engine.OPENIMCC.value]["calls_by_kind"] == {
        "equilibrate_cell": 0,
        "evaluate": 0,
        "evaluate_redox": 0,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }
    assert redox_parallel_counts[Engine.OPENIMCC.value]["calls_by_kind"] == {
        "equilibrate_cell": 0,
        "evaluate": 0,
        "evaluate_redox": 0,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }


def test_direct_openimcc_results_and_refusals_round_trip_through_cache(
    tmp_path: Path,
) -> None:
    module_name = "simulator.melt_backend.openimcc_cache"
    assert importlib.util.find_spec(module_name) is not None, (
        "direct OpenIMCC cache codecs have not been added"
    )
    from simulator.melt_backend.openimcc_cache import (
        cached_openimcc_evaluate,
        cached_openimcc_redox,
    )
    from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult
    from simulator.melt_backend.imcc_adapter_labels import ImccAdapterLabels
    import openimcc
    from openimcc.redox import RedoxResult

    cache = EngineCache(tmp_path / "direct-cache.sqlite")
    evaluate_calls = 0
    redox_calls = 0
    bridge_result = OpenImccBridgeResult(
        parent_oxide_activities=MappingProxyType({"SiO2": 0.25}),
        parent_oxides=("SiO2",),
        flags=("stable",),
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
            flags=("stable",),
            notices=(),
        ),
        coverage=MappingProxyType({"SiO2": "supported"}),
        activity_coefficients=MappingProxyType(
            {"Si": MappingProxyType({"value": 0.5})}
        ),
        parent_oxide_x_star_ratios=MappingProxyType({"SiO2": 0.75}),
    )
    redox_result = RedoxResult(
        temperature_K=1703.0,
        mode="closed",
        lambda_ln_f_o2=-3.0,
        lambda_sat=-2.5,
        parent_moles=MappingProxyType({"FeO": 1.0}),
        parent_activity=MappingProxyType({"FeO": 0.4}),
        species_moles=MappingProxyType({"FeO": 1.0}),
        ferric_fe_mol=0.2,
        ferrous_fe_mol=0.8,
        oxygen_inventory_mol=1.0,
        oxygen_mol=1.0,
        oxygen_residual_mol=0.0,
        oxygen_uptake_mol=0.0,
        oxygen_capacity_mol_per_ln_f_o2=0.1,
        metal_moles=0.0,
        metal_buffered=False,
        endpoint_status="interior",
        flags=("stable",),
        last_resort_ideal_parents=(),
        extrapolated_redox_rows=(),
        active_row_band_intersection_K=(1000.0, 2000.0),
        solver_path="fixture",
        fallback_fired=False,
        warm_start=(),
        element_moles_by_oxidation_state=MappingProxyType(
            {"Fe": MappingProxyType({2: 0.8, 3: 0.2})}
        ),
        unmodelled_redox_couples=(
            MappingProxyType({"element": "Cr", "states": (2, 3)}),
        ),
        redox_pack_digest="fixture-redox-digest",
        melt_pack_binding_digest="fixture-binding-digest",
    )

    def evaluate_once() -> OpenImccBridgeResult:
        nonlocal evaluate_calls
        evaluate_calls += 1
        return bridge_result

    def redox_once() -> RedoxResult:
        nonlocal redox_calls
        redox_calls += 1
        return redox_result

    evaluate_request = {
        "composition_mol": {"SiO2": 0.5, "Al2O3": 0.5},
        "temperature_K": 1703.0,
        "pack": None,
        "allow_extrapolation": True,
        "allow_out_of_envelope": True,
    }
    redox_request = {
        "inventory": {"Fe": 1.0, "O": 1.0},
        "temperature_K": 1703.0,
        "mode": "closed",
        "lambda_imposed": None,
    }
    evaluate_cold = cached_openimcc_evaluate(
        cache, **evaluate_request, solve_fn=evaluate_once
    )
    evaluate_warm = cached_openimcc_evaluate(
        cache, **evaluate_request, solve_fn=evaluate_once
    )
    redox_cold = cached_openimcc_redox(
        cache, **redox_request, solve_fn=redox_once
    )
    redox_warm = cached_openimcc_redox(
        cache, **redox_request, solve_fn=redox_once
    )

    assert evaluate_warm == evaluate_cold == bridge_result
    assert redox_warm == redox_cold == redox_result
    assert evaluate_calls == redox_calls == 1

    evaluation_refusal_request = {
        **evaluate_request,
        "composition_mol": {"Fe2O3": 1.0},
    }
    evaluation_refusal_calls = 0

    def refuse_evaluation() -> OpenImccBridgeResult:
        nonlocal evaluation_refusal_calls
        evaluation_refusal_calls += 1
        raise RuntimeError(
            "isolated OpenIMCC ImccComponentOutsideDomainError: fixture refusal"
        )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="fixture refusal"):
            cached_openimcc_evaluate(
                cache,
                **evaluation_refusal_request,
                solve_fn=refuse_evaluation,
            )
    assert evaluation_refusal_calls == 1

    refusal_request = {
        **redox_request,
        "inventory": {"Fe": 0.0},
        "mode": "imposed",
        "lambda_imposed": -1.5,
    }
    refusal_calls = 0

    def refuse() -> RedoxResult:
        nonlocal refusal_calls
        refusal_calls += 1
        raise openimcc.ImccRefusal("fixture refusal")

    for _ in range(2):
        with pytest.raises(openimcc.ImccRefusal, match="fixture refusal"):
            cached_openimcc_redox(
                cache, **refusal_request, solve_fn=refuse
            )
    assert refusal_calls == 1
    counts = cache.counters
    assert counts["openimcc"]["calls_by_kind"] == {
        "equilibrate_cell": 0,
        "evaluate": 2,
        "evaluate_redox": 2,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }
