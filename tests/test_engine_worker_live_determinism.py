"""Optional real-engine warm/cold byte-identity acceptance gates."""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
import json
import os
import statistics
import subprocess
import sys
import time
import warnings

import pytest

import engines.alphamelts.thermoengine as thermoengine_module
from engines.alphamelts.thermoengine import ThermoEngineTransport
from simulator.melt_backend.alphamelts import activity_from_chem_potential
from simulator.melt_backend.engine_worker import (
    EngineWorkerPool,
    WarmEngineWorker,
)
from simulator.melt_backend.magemin import MAGEMinBackend


pytestmark = [pytest.mark.live_engine, pytest.mark.serial]


def _enabled() -> bool:
    return os.environ.get('REGOLITH_RUN_ENGINE_DETERMINISM') == '1'


def _bytes(value) -> bytes:
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode()


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
    return {
        'p50_s': statistics.median(ordered),
        'p95_s': percentile(0.95),
        'p99_s': percentile(0.99),
        'max_s': max(ordered),
    }


def _first_differences(left: bytes, right: bytes, *, limit: int = 12):
    differences = []
    def visit(a, b, path):
        if len(differences) >= limit:
            return
        if type(a) is not type(b):
            differences.append((path, a, b))
        elif isinstance(a, dict):
            if set(a) != set(b):
                differences.append((path + '.keys', sorted(a), sorted(b)))
            for key in sorted(set(a) & set(b)):
                visit(a[key], b[key], f'{path}.{key}')
        elif isinstance(a, list):
            if len(a) != len(b):
                differences.append((path + '.length', len(a), len(b)))
            for index, (av, bv) in enumerate(zip(a, b)):
                visit(av, bv, f'{path}[{index}]')
        elif a != b:
            differences.append((path, a, b))
    visit(json.loads(left), json.loads(right), '$')
    return differences


def _assert_byte_sequences_equal(
    expected: list[bytes],
    actual: list[bytes],
    *,
    points: list[dict],
    worker_pids: list[int] | None = None,
) -> None:
    assert len(actual) == len(expected), (len(expected), len(actual))
    for index, (expected_bytes, actual_bytes) in enumerate(zip(expected, actual)):
        if actual_bytes == expected_bytes:
            continue
        matching_indices = [
            candidate_index
            for candidate_index, candidate_bytes in enumerate(expected)
            if actual_bytes == candidate_bytes
        ]
        classification = (
            'result-routing'
            if matching_indices and index not in matching_indices
            else 'value-difference'
        )
        pytest.fail(json.dumps({
            'classification': classification,
            'point_index': index,
            'matching_cold_point_indices': matching_indices,
            'worker_pid': None if worker_pids is None else worker_pids[index],
            'point': points[index],
            'first_differences': _first_differences(
                expected_bytes,
                actual_bytes,
            ),
        }, sort_keys=True, default=str))


def _handle_traced_thermoengine_request(transport, kwargs, errlog):
    jitter_ms = int(os.environ.get('REGOLITH_ENGINE_LOAD_JITTER_MS', '20'))
    if jitter_ms > 0:
        time.sleep((time.monotonic_ns() % (jitter_ms + 1)) / 1000.0)
    return (
        os.getpid(),
        thermoengine_module._handle_thermoengine_request(
            transport,
            kwargs,
            errlog,
        ),
    )


@contextmanager
def _synthetic_contention():
    worker_count = max(1, int(os.environ.get(
        'REGOLITH_ENGINE_LOAD_WORKERS',
        str(min(12, max(2, (os.cpu_count() or 2) * 2 // 3))),
    )))
    memory_mib = max(
        1,
        int(os.environ.get('REGOLITH_ENGINE_LOAD_MEMORY_MIB', '64')),
    )
    code = (
        f'block=bytearray({memory_mib}*1024*1024); '
        "[block.__setitem__(i,i&255) for i in range(0,len(block),4096)]; "
        'value=1\n'
        'while True: '
        ' value=(value*1664525+1013904223)&0xffffffff'
    )
    processes = []
    try:
        for _ in range(worker_count):
            processes.append(subprocess.Popen(
                [sys.executable, '-c', code],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ))
        time.sleep(1.0)
        failed = [process.pid for process in processes if process.poll() is not None]
        assert not failed, f'synthetic load workers exited during startup: {failed}'
        yield [process.pid for process in processes]
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)


def _without_formula_sign_noise(value: bytes) -> bytes:
    def clean(item):
        if isinstance(item, dict):
            return {
                key: clean(child)
                for key, child in item.items()
                if key != 'composition_formula'
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item
    return _bytes(clean(json.loads(value)))


def _thermo_points():
    basalt = {
        'SiO2': 49.0, 'TiO2': 1.5, 'Al2O3': 14.0, 'FeO': 10.0,
        'Fe2O3': 1.0, 'MgO': 9.0, 'CaO': 11.0, 'Na2O': 2.5,
        'K2O': 0.8, 'Cr2O3': 0.2, 'MnO': 0.2, 'P2O5': 0.3,
    }
    points = []
    for index in range(22):
        comp = dict(basalt)
        delta = 0.05 * (index % 5)
        comp['SiO2'] += delta
        comp['MgO'] -= delta
        points.append({
            'temperature_C': 1350.0 + 10.0 * (index % 7),
            'pressure_bar': (1.0, 100.0, 500.0)[index % 3],
            'comp_wt': comp,
            'fO2_log': (-10.0, -9.0, -8.0)[index % 3],
        })
    points.extend((dict(points[3]), dict(points[4])))
    return points


@pytest.mark.skipif(not _enabled(), reason='set REGOLITH_RUN_ENGINE_DETERMINISM=1')
def test_thermoengine_24_point_warm_cold_byte_identity():
    points = _thermo_points()
    debug_limit = int(os.environ.get('REGOLITH_ENGINE_DETERMINISM_LIMIT', '0'))
    if debug_limit:
        points = points[:debug_limit]
    cold_bytes = []
    for point in points:
        cold = ThermoEngineTransport(
            activity_converter=activity_from_chem_potential,
        )
        try:
            cold.initialize()
            cold_bytes.append(_bytes(cold.equilibrate(**point)))
        finally:
            cold.close()

    warm = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    warm_times = []
    try:
        warm.initialize()
        warm_bytes = []
        for point in points:
            started = time.perf_counter()
            warm_bytes.append(_bytes(warm.equilibrate(**point)))
            warm_times.append(time.perf_counter() - started)
    finally:
        warm.close()
    mismatch = next(
        (index for index, pair in enumerate(zip(cold_bytes, warm_bytes))
         if pair[0] != pair[1]),
        None,
    )
    semantic_mismatches = [
        index for index, (cold_value, warm_value) in enumerate(
            zip(cold_bytes, warm_bytes)
        )
        if _without_formula_sign_noise(cold_value)
        != _without_formula_sign_noise(warm_value)
    ]
    print('THERMOENGINE_WARM_TIMING ' + json.dumps(_stats(warm_times), sort_keys=True))
    print('THERMOENGINE_SEMANTIC_MISMATCHES ' + json.dumps(semantic_mismatches))
    assert mismatch is None, (
        mismatch,
        semantic_mismatches,
        _first_differences(
            cold_bytes[semantic_mismatches[0] if semantic_mismatches else mismatch],
            warm_bytes[semantic_mismatches[0] if semantic_mismatches else mismatch],
        ),
    )
    assert warm_bytes[-2] == warm_bytes[3]
    assert warm_bytes[-1] == warm_bytes[4]
    print(
        f'THERMOENGINE_BYTE_IDENTITY '
        f'{len(points)}/{len(points)} byte-identical'
    )


@pytest.mark.skipif(not _enabled(), reason='set REGOLITH_RUN_ENGINE_DETERMINISM=1')
def test_thermoengine_two_slot_pool_matches_cold_byte_for_byte(tmp_path):
    points = _thermo_points()
    debug_limit = int(os.environ.get('REGOLITH_ENGINE_DETERMINISM_LIMIT', '0'))
    if debug_limit:
        points = points[:debug_limit]
    cold_bytes = []
    for point in points:
        cold = ThermoEngineTransport(
            activity_converter=activity_from_chem_potential,
        )
        try:
            cold.initialize()
            cold_bytes.append(_bytes(cold.equilibrate(**point)))
        finally:
            cold.close()

    def worker_factory(index):
        return WarmEngineWorker(
            name=f'ThermoEngine pool slot {index}',
            bootstrap=thermoengine_module._bootstrap_thermoengine_worker,
            handler=thermoengine_module._handle_thermoengine_request,
            bootstrap_args=('MELTSv1.0.2', activity_from_chem_potential),
            startup_timeout_s=30.0,
            call_timeout_s=60.0,
            diagnostic_log_path=tmp_path / f'thermoengine-pool-{index}.log',
        )

    with EngineWorkerPool(worker_factory, size=2) as pool:
        futures = [pool.submit(point) for point in points]
        pool_bytes = [_bytes(future.result(timeout=65.0)) for future in futures]

    _assert_byte_sequences_equal(cold_bytes, pool_bytes, points=points)
    assert pool_bytes[-2] == pool_bytes[3]
    assert pool_bytes[-1] == pool_bytes[4]
    print(
        f'THERMOENGINE_POOL_BYTE_IDENTITY '
        f'{len(points)}/{len(points)} byte-identical'
    )


@pytest.mark.skipif(not _enabled(), reason='set REGOLITH_RUN_ENGINE_DETERMINISM=1')
def test_thermoengine_pool_matches_cold_under_load(tmp_path):
    points = _thermo_points()
    debug_limit = int(os.environ.get('REGOLITH_ENGINE_DETERMINISM_LIMIT', '0'))
    if debug_limit:
        points = points[:debug_limit]
    cold_bytes = []
    for point in points:
        cold = ThermoEngineTransport(
            activity_converter=activity_from_chem_potential,
        )
        try:
            cold.initialize()
            cold_bytes.append(_bytes(cold.equilibrate(**point)))
        finally:
            cold.close()

    def worker_factory(index):
        return WarmEngineWorker(
            name=f'Loaded ThermoEngine pool slot {index}',
            bootstrap=thermoengine_module._bootstrap_thermoengine_worker,
            handler=_handle_traced_thermoengine_request,
            bootstrap_args=('MELTSv1.0.2', activity_from_chem_potential),
            startup_timeout_s=30.0,
            call_timeout_s=60.0,
            diagnostic_log_path=tmp_path / f'thermoengine-load-pool-{index}.log',
        )

    repeats = max(
        1,
        int(os.environ.get('REGOLITH_ENGINE_LOAD_REPEATS', '3')),
    )
    pool_size = max(
        1,
        int(os.environ.get('REGOLITH_ENGINE_LOAD_POOL_SIZE', '4')),
    )
    with _synthetic_contention() as load_pids:
        for repeat in range(repeats):
            with EngineWorkerPool(worker_factory, size=pool_size) as pool:
                futures = [pool.submit(point) for point in points]
                traced = [future.result(timeout=65.0) for future in futures]
            worker_pids = [worker_pid for worker_pid, _payload in traced]
            pool_bytes = [_bytes(payload) for _worker_pid, payload in traced]
            _assert_byte_sequences_equal(
                cold_bytes,
                pool_bytes,
                points=points,
                worker_pids=worker_pids,
            )
            assert len(set(worker_pids)) == pool_size
            assert pool_bytes[-2] == pool_bytes[3]
            assert pool_bytes[-1] == pool_bytes[4]
            print(
                'THERMOENGINE_LOAD_POOL_BYTE_IDENTITY '
                f'repeat={repeat + 1}/{repeats} '
                f'points={len(points)}/{len(points)} '
                f'load_workers={len(load_pids)} '
                f'pool_workers={len(set(worker_pids))}/{pool_size}'
            )


def _magemin_points(backend: MAGEMinBackend):
    basalt = {
        'SiO2': 49.0, 'TiO2': 1.5, 'Al2O3': 14.0, 'FeO': 10.0,
        'Fe2O3': 1.0, 'MgO': 9.0, 'CaO': 11.0, 'Na2O': 2.5,
        'K2O': 0.8, 'Cr2O3': 0.2, 'MnO': 0.2, 'P2O5': 0.3,
    }
    points = []
    for index in range(22):
        comp = dict(basalt)
        delta = 0.05 * (index % 5)
        comp['SiO2'] += delta
        comp['MgO'] -= delta
        points.append({
            'bulk_projection': backend._build_db_bulk_projection(comp, database='ig'),
            'temperature_C': 1150.0 + 15.0 * (index % 7),
            'pressure_bar': (1.0, 1000.0, 5000.0)[index % 3],
            'fO2_log': (-11.0, -9.0, -7.0)[index % 3],
        })
    points.extend((dict(points[3]), dict(points[4])))
    return points


@pytest.mark.skipif(not _enabled(), reason='set REGOLITH_RUN_ENGINE_DETERMINISM=1')
def test_magemin_24_point_warm_cold_byte_identity():
    cold = MAGEMinBackend()
    warm = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        if not cold.initialize({'warm_worker': False}):
            pytest.skip('MAGEMin unavailable')
        if not warm.initialize({'warm_worker': True}):
            pytest.skip('MAGEMin warm worker unavailable')
    points = _magemin_points(cold)
    try:
        cold_bytes = []
        cold_times = []
        for point in points:
            started = time.perf_counter()
            cold_bytes.append(_bytes(cold._call_magemin(**point)))
            cold_times.append(time.perf_counter() - started)
        warm_bytes = []
        warm_times = []
        for point in points:
            started = time.perf_counter()
            warm_bytes.append(_bytes(warm._call_magemin(**point)))
            warm_times.append(time.perf_counter() - started)
    finally:
        cold.close()
        warm.close()
    assert warm_bytes == cold_bytes
    assert warm_bytes[-2] == warm_bytes[3]
    assert warm_bytes[-1] == warm_bytes[4]
    print(f'MAGEMIN_BYTE_IDENTITY {len(points)}/{len(points)} byte-identical')
    print('MAGEMIN_COLD_TIMING ' + json.dumps(_stats(cold_times), sort_keys=True))
    print('MAGEMIN_WARM_TIMING ' + json.dumps(_stats(warm_times), sort_keys=True))
