"""Worker refusal text is byte-identical across checkout roots.

The residual row stores the ThermoEngine equilibrium failure string. That
string used to embed ``traceback.format_exc()``, whose absolute checkout
path and caret underlines changed between two trees of the same commit.
"""

from __future__ import annotations

import inspect
import linecache
import re
import traceback
from pathlib import Path

import pytest

from engines.alphamelts.thermoengine import (
    ThermoEngineNotConvergedError,
    ThermoEngineOutOfDomainError,
    ThermoEngineRefusalCause,
    ThermoEngineTransport,
)
from simulator.engine_pool import (
    EngineWorkerRemoteError,
    _run_engine_worker,
    engine_worker_failure_text,
)
from simulator.melt_backend.alphamelts import _run_petthermotools_worker

_WORKER_FAILURE_SOURCE = """\
def boom():
    try:
        raise ValueError(cause_message)
    except ValueError as cause:
        raise exc_type(message) from cause
boom()
"""

_OOD_SOURCE = """\
def boom():
    raise exc_type(cause)
boom()
"""

_CARET_LINE = re.compile(r"(?m)^[ \t]*\^[\^~ \t]*$")


def _source_line(source: str, needle: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(needle)


def _raise_compiled(source: str, filename: str, namespace: dict):
    linecache.cache[filename] = (
        len(source),
        None,
        [line + "\n" for line in source.splitlines()],
        filename,
    )
    try:
        exec(compile(source, filename, "exec"), namespace)
    except Exception as exc:
        return exc
    raise AssertionError(f"no exception from {filename}")


def _clear_linecache(filename: str) -> None:
    linecache.cache.pop(filename, None)


def _stdlib_traceback(exc: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


def _worker_remote_error(
    root: Path,
) -> tuple[BaseException, EngineWorkerRemoteError, str]:
    filename = str(root / "simulator" / "engine_pool.py")
    exc = _raise_compiled(
        _WORKER_FAILURE_SOURCE,
        filename,
        {
            "exc_type": ThermoEngineNotConvergedError,
            "message": "not_converged: solver returned nothing",
            "cause_message": "fo2_requires_iron: no FeO/Fe2O3",
        },
    )
    assert isinstance(exc, ThermoEngineNotConvergedError)
    remote = EngineWorkerRemoteError(
        type(exc).__name__,
        str(exc),
        engine_worker_failure_text(exc, repo_root=root),
    )
    return exc, remote, filename


def _equilibrate_remote(
    remote: EngineWorkerRemoteError,
    expected: type[BaseException],
) -> BaseException:
    """Run the real ThermoEngine refusal builder against one worker error."""

    transport = ThermoEngineTransport(
        model_name="MELTSv1.0.2",
        activity_converter=lambda _mu, _mu0, _temperature_K: 1.0,
    )
    transport._worker.start_count = 1

    def fail(*_args, **_kwargs):
        raise remote

    transport._worker.call = fail
    try:
        with pytest.raises(expected) as raised:
            transport.equilibrate(
                temperature_C=1200.0,
                pressure_bar=1.0,
                comp_wt={"SiO2": 50.0},
            )
    finally:
        transport.close()
    return raised.value


def test_thermoengine_refusal_detail_is_byte_identical_across_checkout_roots(
    tmp_path: Path,
) -> None:
    roots = (
        tmp_path / "nodes-t1928ab2-base" / "tree",
        tmp_path / "nodes-t1928ab2-car" / "tree",
    )
    raised_failures = []
    details = []
    filenames = []
    try:
        for root in roots:
            exc, remote, filename = _worker_remote_error(root)
            filenames.append(filename)
            raised = _equilibrate_remote(remote, ThermoEngineNotConvergedError)
            assert raised.backend_failure_reason_code == "not_converged"
            detail = str(raised)
            raised_failures.append(exc)
            details.append(detail)
        assert details[0].encode("utf-8") == details[1].encode("utf-8")
        detail = details[0]
        assert "not_converged" in detail
        assert "fo2_requires_iron" in detail
        assert "ThermoEngineNotConvergedError:" in detail
        assert "ValueError:" in detail
        outer_line = _source_line(_WORKER_FAILURE_SOURCE, "raise exc_type")
        cause_line = _source_line(_WORKER_FAILURE_SOURCE, "raise ValueError")
        assert f"simulator/engine_pool.py:{outer_line}:boom" in detail
        assert f"simulator/engine_pool.py:{cause_line}:boom" in detail
        assert "raise exc_type(message)" not in detail
        assert not _CARET_LINE.search(detail)
        assert 'File "' not in detail
        seat = Path(__file__).resolve().parents[1]
        assert str(seat) not in detail
        for root, exc in zip(roots, raised_failures):
            assert str(root) not in detail
            stdlib = _stdlib_traceback(exc)
            assert str(root) in stdlib
            assert 'File "' in stdlib
            assert "raise exc_type(message)" in stdlib
    finally:
        for filename in filenames:
            _clear_linecache(filename)

    pool_source = inspect.getsource(_run_engine_worker)
    assert "format_exc" not in pool_source
    assert pool_source.count("engine_worker_failure_text(") == 2
    assert pool_source.count("failure_disposition") >= 2
    cold_source = inspect.getsource(_run_petthermotools_worker)
    assert "format_exc" not in cold_source
    assert "engine_worker_failure_text(" in cold_source


def test_out_of_domain_worker_refusal_keeps_the_typed_cause(tmp_path: Path) -> None:
    root = tmp_path / "nodes-t1928ab2-base" / "tree"
    filename = str(root / "simulator" / "engine_pool.py")
    cause = ThermoEngineRefusalCause.FO2_REQUIRES_IRON
    try:
        exc = _raise_compiled(
            _OOD_SOURCE,
            filename,
            {"exc_type": ThermoEngineOutOfDomainError, "cause": cause},
        )
    finally:
        _clear_linecache(filename)
    assert isinstance(exc, ThermoEngineOutOfDomainError)
    formatted = engine_worker_failure_text(exc, repo_root=root)
    assert str(root) not in formatted
    assert cause.value in formatted
    assert f"simulator/engine_pool.py:{_source_line(_OOD_SOURCE, 'raise exc_type')}:boom" in formatted

    remote = EngineWorkerRemoteError(type(exc).__name__, str(exc), formatted)
    raised = _equilibrate_remote(remote, ThermoEngineOutOfDomainError)
    assert isinstance(raised, ThermoEngineOutOfDomainError)
    assert raised.cause is cause
    assert str(raised) == str(ThermoEngineOutOfDomainError(cause))
    assert str(root) not in str(raised)
