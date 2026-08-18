from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from engines.alphamelts.subprocess_runner import equilibrate_via_subprocess
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH,
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    ALPHAMELTS_REASON_TIMEOUT,
    AlphaMELTSBackend,
    AlphaMELTSSubprocessRunMode,
)


def _composition_mol_by_account() -> dict[str, dict[str, float]]:
    return {
        'process.cleaned_melt': {
            'SiO2': 1.0,
            'Al2O3': 0.2,
            'FeO': 0.15,
            'MgO': 0.2,
            'CaO': 0.15,
            'Na2O': 0.05,
        }
    }


def _backend(binary: Path, *, timeout_s: float) -> AlphaMELTSBackend:
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = binary
    backend._timeout_s = timeout_s
    backend._engine_version = 'alphamelts-test-stub'
    return backend


def _run(
    backend: AlphaMELTSBackend,
    composition_mol_by_account: dict[str, dict[str, float]] | None = None,
):
    return equilibrate_via_subprocess(
        backend,
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol_by_account=(
            composition_mol_by_account or _composition_mol_by_account()
        ),
        species_formula_registry={},
        run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name != 'posix', reason='process-group contract is POSIX')
def test_hanging_alphamelts_times_out_kills_group_and_reaps(tmp_path, monkeypatch):
    pid_file = tmp_path / 'alphamelts-pids'
    stub = tmp_path / 'alphamelts'
    stub.write_text(
        '#!/bin/sh\n'
        'sleep 60 &\n'
        'child=$!\n'
        'printf "%s %s\\n" "$$" "$child" > "$ALPHAMELTS_TEST_PID_FILE"\n'
        'wait "$child"\n'
    )
    stub.chmod(0o755)
    monkeypatch.setenv('ALPHAMELTS_TEST_PID_FILE', str(pid_file))
    backend = _backend(stub, timeout_s=1.0)

    started = time.monotonic()
    result = _run(backend)
    elapsed = time.monotonic() - started

    assert elapsed < 1.2
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == ALPHAMELTS_REASON_TIMEOUT
    failure = result.diagnostics['subprocess_failure']
    assert failure['timeout_s'] == pytest.approx(1.0)
    assert failure['process_group_killed'] is True
    assert failure['launcher_reaped'] is True
    assert pid_file.is_file()
    pids = [int(value) for value in pid_file.read_text().split()]
    deadline = time.monotonic() + 1.0
    while any(_pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(_pid_exists(pid) for pid in pids)


@pytest.mark.skipif(os.name != 'posix', reason='signal contract is POSIX')
def test_sigsegv_alphamelts_returns_composition_and_crash_point(tmp_path):
    stub = tmp_path / 'alphamelts'
    stub.write_text('#!/bin/sh\nkill -SEGV $$\n')
    stub.chmod(0o755)
    backend = _backend(stub, timeout_s=1.0)

    result = _run(backend)

    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['subprocess_failure'] == {
        'stage': 'alphamelts_subprocess_execute',
        'command': [str(stub), '1'],
        'returncode': -11,
        'signal': 'SIGSEGV',
    }
    crash_point = result.diagnostics['out_of_domain_crash_point']
    assert crash_point['stage'] == 'alphamelts_subprocess_execute'
    assert crash_point['composition_mol_by_account'] == (
        _composition_mol_by_account()
    )
    assert any('SIGSEGV' in warning for warning in result.warnings)


def test_known_crashing_alkali_silica_binary_refuses_before_launch(tmp_path):
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(f'#!/bin/sh\nprintf invoked > "{marker}"\n')
    stub.chmod(0o755)
    backend = _backend(stub, timeout_s=1.0)
    composition = {
        'process.cleaned_melt': {
            'Na2O': 0.247,
            'SiO2': 0.753,
        }
    }

    result = _run(backend, composition)

    assert result.status != 'out_of_domain'
    assert result.status == 'not_converged'
    assert result.diagnostics['backend_status_reason'] == (
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['subprocess_input_guard']['predicate'] == (
        'fe_free_and_imposed_absolute_fo2'
    )
    assert result.diagnostics['subprocess_input_guard']['matching_scope'] == (
        'two_component_na2o_or_k2o_silica_family'
    )
    assert result.diagnostics['subprocess_input_guard']['not_the_predicate'] == (
        'no_Fe'
    )
    assert result.diagnostics['subprocess_input_guard']['active_components'] == [
        'Na2O',
        'SiO2',
    ]
    assert 'Fe-free' in result.warnings[0]
    assert 'imposed' in result.warnings[0]
    assert 'two-component alkali-silica' not in result.warnings[0]
    assert result.diagnostics['out_of_domain_crash_point'][
        'composition_mol_by_account'
    ] == composition
    assert not marker.exists()
