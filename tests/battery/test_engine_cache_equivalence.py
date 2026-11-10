from __future__ import annotations

import json
import importlib.util
import builtins
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Mapping

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    Quantity,
    Rail,
)
from simulator.battery.records import Composition, State
from simulator.battery.score import (
    ScoreContext,
    _score_store_to_jsonl,
    is_store_stamp_payload,
)
from simulator.diagnostic_helpers.binary_pot_battery import (
    EquilibrateCell,
    EngineHandle,
    Po2Request,
    close_battery_engine_handle,
    equilibrate_cell,
    open_battery_engine,
    load_binary_pots,
)
import simulator.diagnostic_helpers.binary_pot_battery as battery
from simulator.engine_cache import EngineCache, cached_solve
from simulator.melt_backend.base import EquilibriumResult
from tests.battery import factories as F
from tests.battery.cache_test_support import (
    CACHE_TEST_ENGINES,
    missing_engine_reason,
    open_available_engine as _open_available_engine,
    skip_if_engine_missing,
)


ROOT = Path(__file__).resolve().parents[2]


def _canonical_residual_rows(path: Path) -> bytes:
    def without_counters_and_timing(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: without_counters_and_timing(item)
                for key, item in value.items()
                if key
                not in {
                    "engine_cache",
                    "cache_counters",
                    "wall_s",
                    "cpu_s",
                    "elapsed_s",
                }
            }
        if isinstance(value, list):
            return [without_counters_and_timing(item) for item in value]
        return value

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
                    without_counters_and_timing(payload),
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
    return b"\n".join(rows) + (b"\n" if rows else b"")


def _residual_payloads(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        return [
            payload
            for line in stream
            if line.strip()
            and isinstance(
                payload := json.loads(line),
                dict,
            )
            and not is_store_stamp_payload(payload)
        ]


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES)
def test_engine_availability_is_reported_per_engine(engine: Engine, request) -> None:
    handle = open_battery_engine(engine.value)
    try:
        if not handle.available or handle.backend is None:
            skip_if_engine_missing(
                handle, engine.value, nodeid=request.node.nodeid
            )
    finally:
        close_battery_engine_handle(handle)


def test_required_engine_skip_uses_the_probe_reason(
    monkeypatch: pytest.MonkeyPatch,
    request,
) -> None:
    unavailable = EngineHandle(
        name=Engine.OPENIMCC.value,
        backend=None,
        available=False,
        unavailable_reason="OpenIMCC runtime package missing",
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    closed: list[EngineHandle] = []
    monkeypatch.setattr(battery, "open_battery_engine", lambda name: unavailable)
    monkeypatch.setattr(battery, "close_battery_engine_handle", closed.append)

    with pytest.raises(
        pytest.skip.Exception,
        match=(
            f"{request.node.nodeid}: "
            "openimcc unavailable: OpenIMCC runtime package missing"
        ),
    ):
        _open_available_engine(Engine.OPENIMCC, nodeid=request.node.nodeid)

    assert closed == [unavailable]


@pytest.mark.parametrize("isolated", (False, True), ids=("direct", "isolated"))
def test_internal_analytical_keys_the_instantiated_oxygen_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated: bool,
    request,
) -> None:
    backend = object.__new__(battery._InternalAnalyticalBatteryBackend)
    backend._core = SimpleNamespace(
        _last_high_t_melt_activity={},
        _last_high_t_melt_activity_temperature_K=None,
        _high_t_melt_activity_fallback_steps=[],
        _last_vapor_pressure_diagnostic={},
    )
    backend._oxygen_balance_pack = "configured-pack"
    backend._oxygen_balance_backend = SimpleNamespace(
        _pack_name="effective-pack",
        _effective_pack_name="effective-pack",
    )
    handle = EngineHandle(
        name=Engine.INTERNAL_ANALYTICAL.value,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    pot = battery.identity_battery_pot(
        "oxygen-pack-key-fixture", {"SiO2": 50.0, "FeO": 50.0}
    )
    po2 = Po2Request(battery.PO2_OXYGEN_BALANCE_EFFUSION, None, "W")
    pack_values = {"configured-pack": 0.25, "effective-pack": 0.5}
    direct_packs: list[str] = []
    isolated_packs: list[str] = []

    def fake_analytical_adapter(**kwargs: object) -> EquilibriumResult:
        oxygen_backend = kwargs["oxygen_balance_backend"]
        pack = oxygen_backend._effective_pack_name
        direct_packs.append(pack)
        return EquilibriumResult(
            phase_assemblage_available=False,
            activity_coefficients={"SiO2": pack_values[pack]},
        )

    def fake_isolated_cell(
        _handle: EngineHandle,
        _pot: object,
        *,
        temperature_K: float,
        po2: Po2Request,
        timeout_s: float,
        qualification: bool,
        arm: str,
        notices: object,
        authority: str | None,
        certified_band: object,
        simulate_crash: str | None = None,
        physical_pressure_bar: float | None = None,
        defer_qualification_decoration: bool = False,
        engine_settings: Mapping[str, object] | None = None,
    ) -> EquilibrateCell:
        del timeout_s, qualification, notices, authority, certified_band
        del simulate_crash, physical_pressure_bar, defer_qualification_decoration
        pack = str(engine_settings["oxygen_balance_backend"]["pack"])
        isolated_packs.append(pack)
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=handle.name,
            temperature_K=temperature_K,
            po2=po2,
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={"SiO2": pack_values[pack]},
            gas_partial_pressures_Pa={},
            liquid_fraction=None,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="fixture",
            arm=arm,
        )

    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", fake_analytical_adapter
    )
    monkeypatch.setattr(battery, "_run_cell_in_subprocess", fake_isolated_cell)

    common = {
        "temperature_K": 1700.0,
        "po2": po2,
        "timeout_s": 30.0,
        "physical_pressure_bar": 1.0,
    }
    off_direct = equilibrate_cell(handle, pot, **common, isolated=False)
    off_isolated = equilibrate_cell(handle, pot, **common, isolated=True)
    direct_cache = EngineCache(tmp_path / "direct.sqlite")
    isolated_cache = EngineCache(tmp_path / "isolated.sqlite")
    cold_direct = equilibrate_cell(
        handle, pot, **common, isolated=False, engine_cache=direct_cache
    )
    warm_direct = equilibrate_cell(
        handle, pot, **common, isolated=False, engine_cache=direct_cache
    )
    cold_isolated = equilibrate_cell(
        handle, pot, **common, isolated=True, engine_cache=isolated_cache
    )
    warm_isolated = equilibrate_cell(
        handle, pot, **common, isolated=True, engine_cache=isolated_cache
    )

    def stable(cell: EquilibrateCell) -> str:
        return json.dumps(
            replace(cell, wall_s=0.0, cpu_s=0.0, hostname="fixture").to_cache_payload(),
            sort_keys=True,
        )

    expected = stable(off_direct)
    assert expected == stable(off_isolated)
    assert all(
        stable(cell) == expected
        for cell in (cold_direct, warm_direct, cold_isolated, warm_isolated)
    )
    assert direct_packs == ["effective-pack", "effective-pack"]
    assert isolated_packs == ["effective-pack", "effective-pack"]
    vector = battery._engine_cache_request_vector(
        handle,
        pot,
        temperature_K=1700.0,
        po2=po2,
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )
    assert vector is not None
    assert vector["engine_settings"]["oxygen_balance_backend"]["pack"] == "effective-pack"


def test_bench_openimcc_reference_check_returns_typed_skip_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.generators import bench

    original_import = builtins.__import__

    def import_without_openimcc(name: str, *args: object, **kwargs: object) -> object:
        if name == "openimcc" or name.startswith("openimcc."):
            raise ModuleNotFoundError("OpenIMCC missing in fixture")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_openimcc)
    available, reason = bench._imcc_reports_parent_oxide("SiO2")

    assert not available
    assert reason.startswith("typed-skip:openimcc_not_importable:")


def test_scored_activity_runs_other_engines_when_openimcc_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_handle = EngineHandle(
        name=Engine.OPENIMCC.value,
        backend=None,
        available=False,
        unavailable_reason="OpenIMCC runtime package missing",
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    real_open = open_battery_engine

    def open_without_openimcc(name: str) -> EngineHandle:
        if name == Engine.OPENIMCC.value:
            return unavailable_handle
        return real_open(name)

    monkeypatch.setattr(
        sys.modules[__name__], "open_battery_engine", open_without_openimcc
    )
    engines: list[Engine] = []
    unavailable: dict[str, str] = {}
    for engine in CACHE_TEST_ENGINES:
        handle = open_without_openimcc(engine.value)
        try:
            missing_reason = missing_engine_reason(handle, engine.value)
            if missing_reason is None:
                engines.append(engine)
            else:
                unavailable[engine.value] = (
                    f"typed-skip:engine_unavailable:{engine.value}:"
                    f"{missing_reason}"
                )
        finally:
            close_battery_engine_handle(handle)
    if not engines:
        pytest.skip(
            f"{request.node.nodeid}: all non-OpenIMCC scored engines "
            f"unavailable: {unavailable}"
        )
    assert unavailable[Engine.OPENIMCC.value].startswith(
        "typed-skip:engine_unavailable:openimcc:"
    )

    original_import = builtins.__import__

    def import_without_openimcc(name: str, *args: object, **kwargs: object) -> object:
        if name == "openimcc" or name.startswith("openimcc."):
            raise ModuleNotFoundError("OpenIMCC missing in scored fixture")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_openimcc)
    from simulator.battery.generators import bench

    available, typed_skip = bench._imcc_reports_parent_oxide("SiO2")
    assert not available
    assert typed_skip.startswith("typed-skip:openimcc_not_importable:")

    work = F.work()
    composition = Composition(
        basis="printed_mole_fraction",
        components=(("SiO2", Decimal("0.5")), ("FeO", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    experiment = F.tabulation_experiment(
        experiment_id="cache-openimcc-unavailable-exp",
        work_id=work.work_id,
        T_K=Decimal("1703"),
        total_P=Decimal("100000"),
    )
    observation = F.observation(
        "cache-openimcc-unavailable-activity",
        experiment.experiment_id,
        F.activity_identity(
            formula="SiO2",
            T_K=Decimal("1703"),
            component_basis="SiO2",
            composition=composition,
            fO2_Pa=Decimal("1"),
            total_P=Decimal("100000"),
        ),
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
    )
    output = tmp_path / "openimcc-unavailable.jsonl"
    _score_store_to_jsonl(
        context,
        output,
        root=ROOT,
        engines=tuple(engines),
        rail=Rail.MELT_ACTIVITY,
        limit=1,
        include_diagnostics=False,
    )
    rows = _residual_payloads(output.with_name(output.name + ".partial"))
    assert rows, "scoring other available engines failed without OpenIMCC"


def test_scored_fixture_rows_match_cache_off_cold_and_warm(
    tmp_path: Path,
    request,
) -> None:
    engines = []
    unavailable: dict[str, str] = {}
    for engine in CACHE_TEST_ENGINES:
        handle = open_battery_engine(engine.value)
        try:
            missing_reason = missing_engine_reason(handle, engine.value)
            if missing_reason is None:
                engines.append(engine)
            else:
                unavailable[engine.value] = (
                    f"typed-skip:engine_unavailable:{engine.value}:"
                    f"{missing_reason}"
                )
        finally:
            close_battery_engine_handle(handle)
    if not engines:
        pytest.skip(
            f"{request.node.nodeid}: all scored engines unavailable: "
            f"{unavailable}"
        )

    work = F.work()
    compositions = (
        Composition(
            basis="printed_mole_fraction",
            components=(("SiO2", Decimal("0.5")), ("FeO", Decimal("0.5"))),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
        Composition(
            basis="printed_mole_fraction",
            components=(
                ("SiO2", Decimal("0.6")),
                ("FeO", Decimal("0.2")),
                ("MgO", Decimal("0.2")),
            ),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
        Composition(
            basis="printed_mole_fraction",
            components=(
                ("SiO2", Decimal("0.4")),
                ("FeO", Decimal("0.3")),
                ("MgO", Decimal("0.3")),
            ),
            amount_basis=AmountBasis.MOLE_FRACTION,
        ),
    )
    temperatures = tuple(
        Decimal(str(value)) for value in (1695, 1699, 1703, 1707, 1711, 1715)
    )
    pressures_Pa = tuple(
        Decimal(str(value)) for value in (100000, 100000, 0.1, 0.09, 1, 100000)
    )
    oxygen_Pa = tuple(
        Decimal(str(value)) for value in (0.1, 1, 10, 100, 100000, 0.1)
    )
    experiments = {}
    observations = {}
    # Use distinct Fe-bearing requests at 1 bar so the cold run populates stable
    # entries; repeated-key behavior is covered by the cache unit tests.
    for index in range(6):
        variant = index % len(compositions)
        temperature = temperatures[index]
        composition = compositions[variant]
        experiment = F.tabulation_experiment(
            experiment_id=f"cache-equivalence-exp-{index:02d}",
            work_id=work.work_id,
            T_K=temperature,
            total_P=pressures_Pa[index],
        )
        observation = F.observation(
            f"cache-equivalence-activity-{index:02d}",
            experiment.experiment_id,
            F.activity_identity(
                formula="SiO2",
                T_K=temperature,
                component_basis="SiO2",
                composition=composition,
                fO2_Pa=oxygen_Pa[index],
                total_P=pressures_Pa[index],
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

    vapor_experiment = F.tabulation_experiment(
        experiment_id="cache-equivalence-vapor-exp",
        work_id=work.work_id,
        T_K=Decimal("1703"),
        total_P=Decimal("100000"),
    )
    vapor_activity_identity = F.activity_identity(
        T_K=Decimal("1703"),
        composition=compositions[0],
        fO2_Pa=Decimal("0.1"),
        total_P=Decimal("100000"),
    )
    vapor_identity = replace(
        F.pref_identity(T_K=Decimal("1703"), fO2_Pa=Decimal("0.1")),
        quantity=Quantity.P_PARTIAL,
        composition=vapor_activity_identity.composition,
        total_pressure_Pa=vapor_activity_identity.total_pressure_Pa,
    )
    vapor_observation = F.observation(
        "cache-equivalence-vapor-pressure",
        vapor_experiment.experiment_id,
        vapor_identity,
        Decimal("1e-6"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    vapor_context = replace(
        context,
        experiments={
            **context.experiments,
            vapor_experiment.experiment_id: vapor_experiment,
        },
        observations={
            **context.observations,
            vapor_observation.observation_id: vapor_observation,
        },
    )

    def score(
        name: str,
        cache: EngineCache | None = None,
        *,
        workers: int = 1,
        score_context: ScoreContext | None = None,
        score_engines: tuple[Engine, ...] | None = None,
        score_rail: Rail = Rail.MELT_ACTIVITY,
        score_limit: int = 6,
    ) -> tuple[bytes, Mapping[str, Mapping[str, object]] | None]:
        path = tmp_path / f"{name}.jsonl"
        _row_count, metadata = _score_store_to_jsonl(
            score_context or context,
            path,
            root=ROOT,
            engines=score_engines or tuple(engines),
            rail=score_rail,
            limit=score_limit,
            include_diagnostics=False,
            workers=workers,
            engine_cache=cache,
        )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters,
        )

    off, _off_counts = score("off")
    off_parallel, _off_parallel_counts = score("off-parallel", workers=3)
    serial_cache_path = tmp_path / "engine-cache-serial.sqlite"
    parallel_cache_path = tmp_path / "engine-cache-parallel.sqlite"
    cold_cache = EngineCache(serial_cache_path)
    cold, cold_counts = score("cold", cold_cache, workers=1)
    cold_parallel_cache = EngineCache(parallel_cache_path)
    cold_parallel, cold_parallel_counts = score(
        "cold-parallel", cold_parallel_cache, workers=3
    )
    warm_cache = EngineCache(serial_cache_path)
    warm, warm_counts = score("warm", warm_cache, workers=1)
    parallel_warm_cache = EngineCache(parallel_cache_path)
    parallel_warm, parallel_counts = score(
        "parallel-warm", parallel_warm_cache, workers=3
    )

    assert off

    def assert_rows_equal(label: str, actual: bytes, expected: bytes) -> None:
        if actual == expected:
            return
        actual_rows = actual.splitlines()
        expected_rows = expected.splitlines()
        mismatch = next(
            (
                index
                for index, pair in enumerate(zip(actual_rows, expected_rows))
                if pair[0] != pair[1]
            ),
            min(len(actual_rows), len(expected_rows)),
        )
        pytest.fail(
            f"{label} residual mismatch at row {mismatch}: "
            f"actual={actual_rows[mismatch:mismatch + 1]!r}; "
            f"expected={expected_rows[mismatch:mismatch + 1]!r}"
        )

    assert_rows_equal("cold cache vs cache off", cold, off)
    assert_rows_equal("parallel off vs serial off", off_parallel, off)
    assert_rows_equal("parallel cold cache vs cache off", cold_parallel, off)
    assert_rows_equal("warm cache vs cache off", warm, off)
    assert_rows_equal("parallel warm cache vs cache off", parallel_warm, off)
    assert warm_counts is not None
    assert parallel_counts is not None
    assert cold_counts is not None
    assert cold_parallel_counts is not None
    activity_rows = _residual_payloads(tmp_path / "cold.jsonl.partial")
    assert any(
        any(
            isinstance(notice, dict)
            and notice.get("kind") == "out_of_certified_band"
            for notice in (row.get("notices") or [])
        )
        for row in activity_rows
    ), "scored fixture did not include a MELTS qualification row"
    warm_misses = {
        engine.value: {
            "serial": warm_counts[engine.value],
            "parallel": parallel_counts[engine.value],
        }
        for engine in engines
        if warm_counts[engine.value]["misses"]
        or parallel_counts[engine.value]["misses"]
    }
    assert not warm_misses, f"warm engine cache misses: {warm_misses}"
    warm_engine_calls = {
        engine.value: {
            "serial": warm_counts[engine.value]["calls_by_kind"],
            "parallel": parallel_counts[engine.value]["calls_by_kind"],
        }
        for engine in engines
        if any(warm_counts[engine.value]["calls_by_kind"].values())
        or any(parallel_counts[engine.value]["calls_by_kind"].values())
    }
    assert not warm_engine_calls, f"warm engine calls remain: {warm_engine_calls}"
    assert sum(warm_counts[engine.value]["hits"] for engine in engines) > 0
    parallel_warm_hits = {
        engine.value: parallel_counts[engine.value]["hits"]
        for engine in engines
        if parallel_counts[engine.value]["hits"] <= 0
    }
    assert not parallel_warm_hits, (
        f"parallel warm worker cache hits missing: {parallel_warm_hits}"
    )
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
    vapor_engines = tuple(
        engine
        for engine in (Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK)
        if engine in engines
    )
    if not vapor_engines:
        pytest.skip(
            f"{request.node.nodeid}: vapor fixture engines unavailable: "
            + json.dumps(
                {
                    engine.value: unavailable[engine.value]
                    for engine in (Engine.INTERNAL_ANALYTICAL, Engine.VAPOROCK)
                    if engine.value in unavailable
                },
                sort_keys=True,
            )
        )
    vapor_off, _vapor_off_counts = score(
        "vapor-off",
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    vapor_off_parallel, _vapor_off_parallel_counts = score(
        "vapor-off-parallel",
        workers=3,
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    vapor_cache_path = tmp_path / "vapor-engine-cache-serial.sqlite"
    vapor_parallel_cache_path = tmp_path / "vapor-engine-cache-parallel.sqlite"
    vapor_cold, vapor_cold_counts = score(
        "vapor-cold",
        EngineCache(vapor_cache_path),
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    vapor_cold_parallel, vapor_cold_parallel_counts = score(
        "vapor-cold-parallel",
        EngineCache(vapor_parallel_cache_path),
        workers=3,
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    vapor_parallel, vapor_parallel_counts = score(
        "vapor-parallel-warm",
        EngineCache(vapor_parallel_cache_path),
        workers=3,
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    vapor_warm, vapor_warm_counts = score(
        "vapor-warm",
        EngineCache(vapor_cache_path),
        workers=1,
        score_context=vapor_context,
        score_engines=vapor_engines,
        score_rail=Rail.VAPOUR,
        score_limit=1,
    )
    assert_rows_equal("vapor parallel off vs serial off", vapor_off_parallel, vapor_off)
    assert_rows_equal("vapor cold cache vs cache off", vapor_cold, vapor_off)
    assert_rows_equal("vapor parallel cold cache vs cache off", vapor_cold_parallel, vapor_off)
    assert_rows_equal("vapor warm cache vs cache off", vapor_warm, vapor_off)
    assert_rows_equal("vapor parallel warm cache vs cache off", vapor_parallel, vapor_off)
    assert vapor_cold_counts is not None
    assert vapor_cold_parallel_counts is not None
    assert vapor_warm_counts is not None
    assert vapor_parallel_counts is not None
    for engine in vapor_engines:
        assert vapor_cold_counts[engine.value]["calls_by_kind"][
            "equilibrate_cell"
        ] > 0, f"{engine.value} was not called in the scored vapor fixture"
        assert not any(vapor_warm_counts[engine.value]["calls_by_kind"].values())
        assert not any(
            vapor_parallel_counts[engine.value]["calls_by_kind"].values()
        )
        assert sum(
            int(value)
            for value in vapor_cold_parallel_counts[engine.value]["calls_by_kind"].values()
        ) > 0
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

    redox_warm_counts = None
    redox_parallel_counts = None
    redox_cold_counts = None
    redox_cold_parallel_counts = None
    redox_rows: list[dict[str, object]] = []
    if Engine.OPENIMCC in engines:
        redox_off, _redox_off_counts = score_redox("redox-off")
        redox_off_parallel, _redox_off_parallel_counts = score_redox(
            "redox-off-parallel", workers=3
        )
        redox_cache_path = tmp_path / "redox-engine-cache-serial.sqlite"
        redox_parallel_cache_path = tmp_path / "redox-engine-cache-parallel.sqlite"
        redox_cold_cache = EngineCache(redox_cache_path)
        redox_cold, redox_cold_counts = score_redox(
            "redox-cold", redox_cold_cache, workers=1
        )
        redox_cold_parallel, redox_cold_parallel_counts = score_redox(
            "redox-cold-parallel", EngineCache(redox_parallel_cache_path), workers=3
        )
        redox_warm_cache = EngineCache(redox_cache_path)
        redox_warm, redox_warm_counts = score_redox(
            "redox-warm", redox_warm_cache, workers=1
        )
        redox_parallel, redox_parallel_counts = score_redox(
            "redox-parallel-warm", EngineCache(redox_parallel_cache_path), workers=3
        )

        assert redox_off
        assert redox_off_parallel == redox_off
        assert redox_cold == redox_off
        assert redox_cold_parallel == redox_off
        assert redox_warm == redox_off
        assert redox_parallel == redox_warm == redox_off
        assert redox_cold_counts is not None
        assert redox_warm_counts is not None
        assert redox_parallel_counts is not None
        assert redox_cold_parallel_counts is not None
        assert redox_cold_counts[Engine.OPENIMCC.value]["calls_by_kind"]["evaluate_redox"] > 0
        assert redox_cold_parallel_counts[Engine.OPENIMCC.value]["calls_by_kind"]["evaluate_redox"] > 0
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
        redox_rows = _residual_payloads(tmp_path / "redox-cold.jsonl.partial")
    else:
        assert unavailable[Engine.OPENIMCC.value].startswith(
            "typed-skip:engine_unavailable:openimcc:"
        )

    def engine_calls(
        counters: Mapping[str, Mapping[str, object]], engine: Engine
    ) -> int:
        entry = counters.get(engine.value, {})
        by_kind = entry.get("calls_by_kind", {})
        if not isinstance(by_kind, Mapping):
            return 0
        return sum(int(value) for value in by_kind.values())

    def engine_hits(
        counters: Mapping[str, Mapping[str, object]], engine: Engine
    ) -> int:
        entry = counters.get(engine.value, {})
        return int(entry.get("hits", 0))

    serial_cold_counter_sets = [cold_counts, vapor_cold_counts]
    parallel_cold_counter_sets = [cold_parallel_counts, vapor_cold_parallel_counts]
    serial_warm_counter_sets = [warm_counts, vapor_warm_counts]
    parallel_warm_counter_sets = [parallel_counts, vapor_parallel_counts]
    if redox_cold_counts is not None:
        serial_cold_counter_sets.append(redox_cold_counts)
        parallel_cold_counter_sets.append(redox_cold_parallel_counts)
        serial_warm_counter_sets.append(redox_warm_counts)
        parallel_warm_counter_sets.append(redox_parallel_counts)
    missing_scored_coverage = {
        engine.value: {
            "serial_cold_calls": sum(
                engine_calls(counters, engine)
                for counters in serial_cold_counter_sets
                if counters is not None
            ),
            "parallel_cold_calls": sum(
                engine_calls(counters, engine)
                for counters in parallel_cold_counter_sets
                if counters is not None
            ),
            "serial_warm_hits": sum(
                engine_hits(counters, engine)
                for counters in serial_warm_counter_sets
                if counters is not None
            ),
            "parallel_warm_hits": sum(
                engine_hits(counters, engine)
                for counters in parallel_warm_counter_sets
                if counters is not None
            ),
        }
        for engine in engines
    }
    missing_scored_coverage = {
        engine: values
        for engine, values in missing_scored_coverage.items()
        if not all(values.values())
    }
    assert not missing_scored_coverage, (
        "available engines were not called cold and hit warm in serial and "
        f"parallel scored paths: {missing_scored_coverage}"
    )

    for counts in (
        warm_counts,
        parallel_counts,
        vapor_warm_counts,
        vapor_parallel_counts,
        *(item for item in (redox_warm_counts, redox_parallel_counts) if item is not None),
    ):
        assert all(
            not any(counts[engine.value]["calls_by_kind"].values())
            for engine in engines
        )

    scored_rows = (
        activity_rows
        + _residual_payloads(tmp_path / "vapor-cold.jsonl.partial")
        + redox_rows
    )
    stable_refusals = [
        row
        for row in scored_rows
        if row.get("status") == "refused"
        and isinstance(row.get("refusal"), dict)
        and row["refusal"].get("reason")
        not in {"engine_timeout", "engine_crash", "attempted_unavailable"}
    ]
    assert stable_refusals, "scored fixtures did not include a stable refusal row"


def test_direct_openimcc_results_and_refusals_round_trip_through_cache(
    tmp_path: Path,
    request,
) -> None:
    handle = _open_available_engine(Engine.OPENIMCC, nodeid=request.node.nodeid)
    close_battery_engine_handle(handle)
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

    evaluation_nonconvergence_calls = 0

    def fail_evaluation_nonconvergence() -> OpenImccBridgeResult:
        nonlocal evaluation_nonconvergence_calls
        evaluation_nonconvergence_calls += 1
        raise RuntimeError(
            "isolated OpenIMCC ImccNonconvergenceError: fixture retry"
        )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="fixture retry"):
            cached_openimcc_evaluate(
                cache,
                **{**evaluate_request, "temperature_K": 1704.0},
                solve_fn=fail_evaluation_nonconvergence,
            )
    assert evaluation_nonconvergence_calls == 2

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
        "evaluate": 4,
        "evaluate_redox": 2,
        "evaluate_gas": 0,
        "residue_vapor_pressure": 0,
    }


def test_stable_refusal_and_transient_cell_equivalence(tmp_path: Path) -> None:
    def cell(
        *,
        status: str,
        refusal_reason: str,
        engine_status: str,
        engine_reason: str,
        disposition: str | None = None,
    ) -> EquilibrateCell:
        outcome = EquilibrateCell(
            pot_id="cache-equivalence",
            engine="magemin",
            temperature_K=1703.0,
            po2=Po2Request("engine_default", None),
            status=status,
            refusal_reason=refusal_reason,
            engine_status=engine_status,
            engine_reason=engine_reason,
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=None,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="fixture",
        )
        if disposition is not None:
            outcome.backend_failure_disposition = disposition
        return outcome

    cache = EngineCache(tmp_path / "refusal-transient.sqlite")
    stable_request = {"call": "equilibrate_cell", "case": "stable-refusal"}
    stable = cell(
        status="refusal",
        refusal_reason="unsupported_composition",
        engine_status="refused",
        engine_reason="unsupported_composition",
    )
    off_refusal = stable.as_payload()
    cold_refusal = cached_solve(
        cache, "magemin", stable_request, lambda: stable
    )
    warm_refusal = cached_solve(
        cache,
        "magemin",
        stable_request,
        lambda: pytest.fail("cached refusal unexpectedly called solve_fn"),
    )

    transient_request = {"call": "equilibrate_cell", "case": "worker-start"}
    transient_calls = 0

    def fail_transiently() -> EquilibrateCell:
        nonlocal transient_calls
        transient_calls += 1
        return cell(
            status="refusal",
            refusal_reason="engine_unavailable",
            engine_status="not_converged",
            engine_reason="warm worker could not be started",
            disposition="environment",
        )

    off_transient = cell(
        status="refusal",
        refusal_reason="engine_unavailable",
        engine_status="not_converged",
        engine_reason="warm worker could not be started",
        disposition="environment",
    ).as_payload()
    cold_transient = cached_solve(
        cache, "magemin", transient_request, fail_transiently
    )
    warm_transient = cached_solve(
        cache, "magemin", transient_request, fail_transiently
    )

    assert cold_refusal.as_payload() == warm_refusal.as_payload() == off_refusal
    assert cold_transient.as_payload() == warm_transient.as_payload() == off_transient
    assert transient_calls == 2
    assert cache.counters["magemin"]["hits"] == 1
    assert cache.counters["magemin"]["transient_skips"] == 2
