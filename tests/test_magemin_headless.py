"""MAGEMin launch isolation and captured numeric output contracts."""

import hashlib
import importlib
import multiprocessing
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import simulator.melt_backend.magemin as magemin
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import MAGEMinBackend


def test_captured_phase_assemblage_numeric_pin():
    stdout = (
        Path(__file__).parent / 'fixtures' / 'magemin'
        / 'lfdrop-diag-1-1700-phase-mode.stdout'
    ).read_text()
    phases = MAGEMinBackend._parse_subprocess_stdout(stdout)
    result = EquilibriumResult(liquid_fraction=0.0)
    MAGEMinBackend()._populate_result(result, {'phases': phases})

    # Pin the captured output before changing launch policy; no physics changes.
    assert result.phases_present == ['liq', 'fsp', 'crst', 'sph']
    assert result.liquid_fraction.hex() == '0x1.6f72127a6f8f9p-1'
    assert {key: value.hex() for key, value in result.phase_masses_kg.items()} == {
        'liq': '0x1.6f7121ab4b72cp-1',
        'fsp': '0x1.aee631f8a0903p-3',
        'crst': '0x1.43fe5c91d14e4p-5',
        'sph': '0x1.0941c8216c615p-5',
    }


@pytest.mark.parametrize('launch', ['version', 'equilibrium', 'pure_phase'])
def test_every_launch_uses_headless_env_and_slot(monkeypatch, tmp_path, launch):
    import simulator.engine_local_config as local_config

    monkeypatch.setenv('DISPLAY', ':99')
    monkeypatch.setenv('MPLBACKEND', 'MacOSX')
    monkeypatch.setenv('OMPI_MCA_btl', 'tcp')
    monkeypatch.setenv('KEEP_MAGEMIN_TEST_VALUE', 'inherited')
    monkeypatch.setattr(local_config, 'cache_version_for', lambda _: None)
    held = []
    seen = []

    @contextmanager
    def slot(_timeout):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    def run(args, **kwargs):
        assert held == [True]
        env = kwargs['env']
        assert env['OMPI_MCA_btl'] == 'self'
        assert env['OMPI_MCA_pml'] == 'ob1'
        loopback = 'lo0' if magemin.sys.platform == 'darwin' else 'lo'
        assert env['PMIX_MCA_ptl_base_if_include'] == loopback
        assert env['PRTE_MCA_prte_if_include'] == loopback
        assert env['HWLOC_COMPONENTS'] == '-opencl'
        assert env['MPLBACKEND'] == 'Agg'
        assert env['KEEP_MAGEMIN_TEST_VALUE'] == 'inherited'
        assert 'DISPLAY' not in env
        assert os.environ['DISPLAY'] == ':99'
        assert os.environ['OMPI_MCA_btl'] == 'tcp'
        seen.append(args)
        output = Path(kwargs['cwd']) / 'output'
        output.mkdir()
        (output / '_matlab_output.txt').write_text('captured matlab')
        return subprocess.CompletedProcess(
            args, 0, stdout='Phase : liq\nMode : 1.000\n', stderr=''
        )

    monkeypatch.setattr(magemin, '_magemin_subprocess_slot', slot)
    monkeypatch.setattr(magemin.subprocess, 'run', run)
    backend = MAGEMinBackend()
    backend._binary_path = tmp_path / 'MAGEMin'
    backend._config = {'timeout_s': 5}
    if launch == 'version':
        with pytest.warns(UserWarning, match='legacy MAGEMin'):
            backend.get_engine_version()
    elif launch == 'equilibrium':
        backend._call_magemin_subprocess(
            bulk_projection=backend._build_db_bulk_projection(
                {'SiO2': 60.0, 'MgO': 40.0}
            ),
            temperature_C=1200.0,
            pressure_kbar=0.001,
            fO2_log=-8.0,
        )
    else:
        backend._run_pure_phase_probe(
            bulk_wt_ig={'SiO2': 60.0, 'MgO': 40.0},
            temperature_C=1200.0,
            pressure_kbar=0.001,
        )
    assert len(seen) == 1
    assert held == []


def test_version_probe_is_cached_by_binary_and_slot_timeout_is_typed(
    monkeypatch, tmp_path,
):
    import simulator.engine_local_config as local_config

    monkeypatch.setattr(local_config, 'cache_version_for', lambda _: None)
    monkeypatch.setattr(local_config, 'warn_legacy_once', lambda *_args: None)
    run_calls = []
    slot_calls = []

    @contextmanager
    def slot(timeout_s):
        slot_calls.append(timeout_s)
        yield

    def run(args, **kwargs):
        run_calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout='MAGEMin 1.9.6\n', stderr=''
        )

    monkeypatch.setattr(magemin, '_magemin_subprocess_slot', slot)
    monkeypatch.setattr(magemin.subprocess, 'run', run)
    binary = tmp_path / 'MAGEMin'
    binary.write_bytes(b'fake MAGEMin executable')
    first = MAGEMinBackend()
    first._binary_path = binary
    second = MAGEMinBackend()
    second._binary_path = binary

    assert first.get_engine_version() == 'MAGEMin 1.9.6'
    assert second.get_engine_version() == 'MAGEMin 1.9.6'
    assert len(run_calls) == 1
    assert slot_calls == [5]

    @contextmanager
    def timed_out_slot(_timeout_s):
        raise magemin._MAGEMinEngineLifecycleError(
            'MAGEMinSubprocessSlotTimeout', 'slot timed out'
        )
        yield

    monkeypatch.setattr(magemin, '_magemin_subprocess_slot', timed_out_slot)
    busy = MAGEMinBackend()
    busy._binary_path = tmp_path / 'BusyMAGEMin'
    assert busy.get_engine_version() == (
        f'MAGEMin subprocess ({busy._binary_path.resolve()})'
    )
    assert len(run_calls) == 1


def test_version_probe_repeats_when_binary_changes_at_same_path(
    monkeypatch, tmp_path,
):
    import simulator.engine_local_config as local_config

    monkeypatch.setattr(local_config, 'cache_version_for', lambda _: None)
    monkeypatch.setattr(local_config, 'warn_legacy_once', lambda *_args: None)
    binary = tmp_path / 'MAGEMin'
    first_bytes = b'1.9.6'
    binary.write_bytes(first_bytes)
    initial_stat = binary.stat()
    run_calls = []

    def run(args, **kwargs):
        run_calls.append(args)
        runtime_bytes = Path(args[0]).read_bytes()
        version = runtime_bytes.decode('ascii')
        digest = hashlib.sha256(runtime_bytes).hexdigest()
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=f'MAGEMin {version} (runtime sha256={digest})\n',
            stderr='',
        )

    monkeypatch.setattr(magemin.subprocess, 'run', run)
    first = MAGEMinBackend()
    first._binary_path = binary
    second = MAGEMinBackend()
    second._binary_path = binary

    first_version = first.get_engine_version()
    assert second.get_engine_version() == first_version
    assert len(run_calls) == 1
    assert (
        f'runtime sha256={hashlib.sha256(first_bytes).hexdigest()}'
        in first_version
    )

    replacement_bytes = b'2.0.0'
    binary.write_bytes(replacement_bytes)
    # Keep the path, inode, and size fixed so the changed mtime proves re-probing.
    replacement_mtime = max(time.time(), initial_stat.st_mtime + 1)
    os.utime(binary, (replacement_mtime, replacement_mtime))
    replacement_stat = binary.stat()
    assert replacement_bytes != first_bytes
    assert replacement_stat.st_ino == initial_stat.st_ino
    assert replacement_stat.st_size == initial_stat.st_size
    assert replacement_stat.st_mtime_ns != initial_stat.st_mtime_ns

    third = MAGEMinBackend()
    third._binary_path = binary
    replacement_version = third.get_engine_version()

    assert replacement_version != first_version
    assert (
        f'runtime sha256={hashlib.sha256(replacement_bytes).hexdigest()}'
        in replacement_version
    )
    assert len(run_calls) == 2


@pytest.mark.parametrize('value, expected', [(None, 2), ('1', 1), ('4', 4),
                                             ('0', 2), ('bad', 2)])
def test_host_limit_default_and_override(monkeypatch, value, expected):
    monkeypatch.delenv('REGOLITH_MAGEMIN_SLOTS', raising=False)
    monkeypatch.delenv('REGOLITH_MAGEMIN_MAX_CONCURRENCY', raising=False)
    if value is not None:
        monkeypatch.setenv('REGOLITH_MAGEMIN_MAX_CONCURRENCY', value)
    assert magemin._magemin_subprocess_slot_count() == expected


def _parallel_slot_caller(scratch, locks, start, release, full, state, mutex):
    os.environ['TMPDIR'] = scratch
    tempfile.tempdir = None
    module = importlib.reload(magemin)
    # Reload under a different TMPDIR: production paths must still agree.
    assert module._MAGEMIN_SUBPROCESS_LOCK_DIR == Path(
        '/tmp/regolith-pyrolysis-simulator'
    )
    module._MAGEMIN_SUBPROCESS_LOCK_DIR = Path(locks)
    module._MAGEMIN_SUBPROCESS_LOCK = Path(locks) / 'magemin.lock'
    start.wait(timeout=30)
    with module._magemin_subprocess_slot(30):
        with mutex:
            state[0] += 1
            state[1] = max(state[1], state[0])
            state[2] += 1
            if state[0] == 2:
                full.set()
        try:
            assert release.wait(timeout=30)
        finally:
            with mutex:
                state[0] -= 1


def test_four_processes_share_two_slots_with_distinct_tmpdirs(monkeypatch, tmp_path):
    monkeypatch.delenv('REGOLITH_MAGEMIN_SLOTS', raising=False)
    monkeypatch.delenv('REGOLITH_MAGEMIN_MAX_CONCURRENCY', raising=False)
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Barrier(5, timeout=30)
    release, full = ctx.Event(), ctx.Event()
    state, mutex = ctx.Array('i', [0, 0, 0]), ctx.Lock()
    workers = []
    for index in range(4):
        scratch = tmp_path / str(index)
        scratch.mkdir()
        workers.append(ctx.Process(target=_parallel_slot_caller, args=(
            str(scratch), str(tmp_path / 'locks'), start, release, full,
            state, mutex,
        )))
    try:
        for worker in workers:
            worker.start()
        start.wait()
        assert full.wait(timeout=30)
        time.sleep(0.1)
        with mutex:
            assert list(state) == [2, 2, 2]
        release.set()
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
        assert list(state) == [0, 2, 4]
    finally:
        release.set()
        for worker in workers:
            if worker.pid is not None:
                worker.join(timeout=5)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=5)


def test_live_phase_and_melt_output_identical_with_headless_env():
    from simulator.melt_backend.alphamelts import _run_alphamelts_subprocess

    backend = MAGEMinBackend()
    binary = backend._resolve_binary_path()
    if binary is None:
        pytest.skip('No compiled MAGEMin binary found')
    args = [str(binary), '--Verb=0', '--db=ig', '--Temp=1200',
            '--Pres=0.001', '--sys_in=wt',
            '--Bulk=49,14,11,9,10,0.8,2.5,1.5,0,0.2,0',
            '--buffer=qfm', '--buffer_n=0']
    outputs = []
    for env in (os.environ.copy(), magemin._magemin_subprocess_env()):
        with magemin._magemin_subprocess_slot(30):
            with tempfile.TemporaryDirectory() as cwd:
                completed = _run_alphamelts_subprocess(
                    args, cwd=cwd, timeout=30, env=env,
                )
        assert completed.returncode == 0, completed.stderr
        # Runtime timing fields vary; Phase/Mode are the native assemblage bytes.
        phase_bytes = b'\n'.join(
            line for line in completed.stdout.encode().splitlines()
            if line.strip().startswith((b'Phase :', b'Mode '))
        )
        assert phase_bytes
        result = EquilibriumResult(liquid_fraction=0.0)
        backend._populate_result(result, {'phases':
            backend._parse_subprocess_stdout(completed.stdout)})
        assert 'liq' in result.phases_present
        assert result.liquid_fraction > 0
        outputs.append((phase_bytes, result.phase_masses_kg,
                        result.liquid_fraction.hex()))
    assert outputs[0] == outputs[1]
