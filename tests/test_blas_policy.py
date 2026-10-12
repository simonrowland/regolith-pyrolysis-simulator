"""Fresh-process checks for the BLAS startup policy owner."""

from __future__ import annotations

import os
import multiprocessing
from pathlib import Path
import subprocess
import sys


POLICY_PATH = Path(__file__).resolve().parents[1] / "simulator" / "_blas_policy.py"
THREAD_NAMES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def _run(code: str, *, overrides: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    for name in THREAD_NAMES:
        env.pop(name, None)
    env["PYTHONPATH"] = "/private/tmp/claude-501/oimcc-1f3c82d/src"
    env.update(overrides or {})
    completed = subprocess.run(
        [sys.executable, "-c", code, str(POLICY_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


_LOAD = r'''
import importlib.util, json, os, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("policy_probe", path)
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)
'''


def test_blas_policy_defaults_before_numpy_load() -> None:
    output = _run(
        _LOAD
        + r'''
policy.prepare()
assert all(os.environ.get(name) == "1" for name in policy.THREAD_ENV_VARS)
import numpy
state = policy.finish()
from threadpoolctl import threadpool_info
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
build_name = numpy.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"]
build = next(runtime for runtime in state.runtimes if ("numpy", build_name) in runtime.build_records)
assert build.name == build_name and build.api == "openblas" and build.observable
assert pool["num_threads"] == 1
assert runtime.how_obtained == "variables-before-load"
print(json.dumps({"pool": pool["num_threads"], "how": runtime.how_obtained}))
'''
    )
    assert '"pool": 1' in output
    assert '"how": "variables-before-load"' in output


def test_blas_policy_limits_already_loaded_pool_for_process_lifetime() -> None:
    output = _run(
        _LOAD
        + r'''
import numpy
from threadpoolctl import threadpool_info, threadpool_limits
setup_limiter = threadpool_limits(limits=4)
for name in policy.THREAD_ENV_VARS:
    os.environ.pop(name, None)
policy.prepare()
state = policy.finish()
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
assert policy._limiter is not None
assert runtime.how_obtained == "threadpoolctl-after-load"
assert next(pool["num_threads"] for pool in threadpool_info() if pool["internal_api"] == "openblas") == 1
numpy.linalg.solve(numpy.eye(2), numpy.ones(2))
assert next(pool["num_threads"] for pool in threadpool_info() if pool["internal_api"] == "openblas") == 1
print("pool=1 after info and solve")
'''
    )
    assert output == "pool=1 after info and solve"


def test_blas_policy_honours_launcher_openblas_before_and_after_load() -> None:
    for imports_first in (False, True):
        prefix = (
            "import numpy\npolicy.prepare()\nassert policy._loaded_at_prepare\n"
            if imports_first
            else "policy.prepare()\nassert policy._loaded_at_prepare == ()\nimport numpy\n"
        )
        output = _run(
            _LOAD
            + prefix
            + r'''
state = policy.finish()
from threadpoolctl import threadpool_info
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
assert pool["num_threads"] == 2
assert runtime.how_obtained == "explicit-launcher-value"
assert sum(notice.kind == "honoured-launcher-value" for notice in state.notices) == 1
print("pool=2")
''',
            overrides={"OPENBLAS_NUM_THREADS": "2"},
        )
        assert output == "pool=2"


def test_blas_policy_omp_override_does_not_mask_openblas() -> None:
    output = _run(
        _LOAD
        + r'''
policy.prepare()
assert "OPENBLAS_NUM_THREADS" not in os.environ
import numpy
from threadpoolctl import threadpool_info
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
assert pool["num_threads"] == 2
assert os.environ["OMP_NUM_THREADS"] == "2"
print("pool=2 openblas_default=absent")
''',
        overrides={"OMP_NUM_THREADS": "2"},
    )
    assert output == "pool=2 openblas_default=absent"


def test_blas_policy_omp_override_does_not_mask_mkl_variable() -> None:
    output = _run(
        _LOAD
        + r'''
policy.prepare()
assert os.environ["OMP_NUM_THREADS"] == "2"
assert "MKL_NUM_THREADS" not in os.environ
print("mkl_default=absent")
''',
        overrides={"OMP_NUM_THREADS": "2"},
    )
    assert output == "mkl_default=absent"


def test_blas_policy_openblas_launcher_does_not_suppress_other_defaults() -> None:
    output = _run(
        _LOAD
        + r'''
policy.apply_environment_defaults()
assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
assert os.environ["OMP_NUM_THREADS"] == "1"
assert os.environ["MKL_NUM_THREADS"] == "1"
assert os.environ["NUMEXPR_NUM_THREADS"] == "1"
assert os.environ["VECLIB_MAXIMUM_THREADS"] == "1"
print("openblas launcher preserved; other defaults=1")
''',
        overrides={"OPENBLAS_NUM_THREADS": "2"},
    )
    assert output == "openblas launcher preserved; other defaults=1"


def test_blas_policy_mkl_launcher_does_not_suppress_other_defaults() -> None:
    output = _run(
        _LOAD
        + r'''
policy.apply_environment_defaults()
assert os.environ["MKL_NUM_THREADS"] == "4"
assert os.environ["OMP_NUM_THREADS"] == "1"
assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
assert os.environ["NUMEXPR_NUM_THREADS"] == "1"
assert os.environ["VECLIB_MAXIMUM_THREADS"] == "1"
print("mkl launcher preserved; other defaults=1")
''',
        overrides={"MKL_NUM_THREADS": "4"},
    )
    assert output == "mkl launcher preserved; other defaults=1"


def test_blas_policy_classifier_directly_covers_runtime_input_cross_product() -> None:
    from itertools import product

    from simulator._blas_policy import _RuntimeClassification
    from simulator._blas_policy import _classify_runtime

    documented_rows = tuple(
        int(line.strip()[0])
        for line in (_classify_runtime.__doc__ or "").splitlines()
        if line.strip()[:2] in {f"{number}." for number in range(1, 8)}
    )
    assert documented_rows == tuple(range(1, 8))

    values = (None, "1", "2")
    no_ctl = _RuntimeClassification("not-in-effect", False, "threadpoolctl readback unavailable")
    snapshot_unavailable = _RuntimeClassification(
        "not-in-effect", False,
        "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed",
    )
    no_ctl_both_late = _RuntimeClassification(
        "not-in-effect", False,
        "threadpoolctl readback unavailable; loaded before start-up; not controllable; "
        "OPENBLAS_NUM_THREADS, OMP_NUM_THREADS set after load",
    )
    no_ctl_openblas_late = _RuntimeClassification(
        "not-in-effect", False,
        "threadpoolctl readback unavailable; loaded before start-up; not controllable; "
        "OPENBLAS_NUM_THREADS set after load",
    )
    no_ctl_omp_late = _RuntimeClassification(
        "not-in-effect", False,
        "threadpoolctl readback unavailable; loaded before start-up; not controllable; "
        "OMP_NUM_THREADS set after load",
    )
    no_ctl_loaded = _RuntimeClassification(
        "not-in-effect", False,
        "threadpoolctl readback unavailable; loaded before start-up; not controllable",
    )
    before = _RuntimeClassification("variables-before-load", False, None)
    before_bound = _RuntimeClassification("variables-before-load", True, None)
    after_bound = _RuntimeClassification("threadpoolctl-after-load", True, None)
    after_unbound = _RuntimeClassification("threadpoolctl-after-load", False, None)
    explicit_bound = _RuntimeClassification("explicit-launcher-value", True, None)
    explicit_unbound = _RuntimeClassification("explicit-launcher-value", False, None)
    loaded_late_omp = _RuntimeClassification(
        "not-in-effect", False,
        "loaded before start-up; not controllable; OMP_NUM_THREADS set after load",
    )
    loaded_late_both = _RuntimeClassification(
        "not-in-effect", False,
        "loaded before start-up; not controllable; OPENBLAS_NUM_THREADS, OMP_NUM_THREADS set after load",
    )
    loaded_late_openblas = _RuntimeClassification(
        "not-in-effect", False,
        "loaded before start-up; not controllable; OPENBLAS_NUM_THREADS set after load",
    )
    loaded = _RuntimeClassification(
        "not-in-effect", False, "loaded before start-up; not controllable"
    )
    limiter_unavailable = _RuntimeClassification(
        "not-in-effect", False,
        "threadpoolctl limiter could not be installed at start-up",
    )

    # Literal oracle, indexed in product order: observed None/1/2, and within
    # each observed value loaded False/True, finish-time readback unavailable/
    # available. This remains the independent 108-cell readback oracle.
    expected = {
        (None, None): (
            no_ctl, before, no_ctl_both_late, loaded_late_both,
            before_bound, before_bound, after_bound, after_bound,
            before, before, after_unbound, after_unbound,
        ),
        (None, "1"): (
            no_ctl, before, no_ctl_openblas_late, loaded_late_openblas,
            before_bound, before_bound, after_bound, after_bound,
            before, before, after_unbound, after_unbound,
        ),
        (None, "2"): (
            no_ctl, before, no_ctl_openblas_late, loaded_late_openblas,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
            explicit_bound, explicit_bound, explicit_bound, explicit_bound,
        ),
        ("1", None): (
            no_ctl, before, no_ctl_omp_late, loaded_late_omp,
            before_bound, before_bound, after_bound, after_bound,
            before, before, after_unbound, after_unbound,
        ),
        ("1", "1"): (
            no_ctl, before, no_ctl_loaded, loaded,
            before_bound, before_bound, after_bound, after_bound,
            before, before, after_unbound, after_unbound,
        ),
        ("1", "2"): (
            no_ctl, before, no_ctl_loaded, loaded,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
        ),
        ("2", None): (
            no_ctl, before, no_ctl_omp_late, loaded_late_omp,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
            explicit_bound, explicit_bound, explicit_bound, explicit_bound,
        ),
        ("2", "1"): (
            no_ctl, before, no_ctl_loaded, loaded,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
        ),
        ("2", "2"): (
            no_ctl, before, no_ctl_loaded, loaded,
            explicit_unbound, explicit_unbound, explicit_unbound, explicit_unbound,
            explicit_bound, explicit_bound, explicit_bound, explicit_bound,
        ),
    }
    assert len(expected) == 9 and all(len(row) == 12 for row in expected.values())
    index = 0
    cells = 0
    for specific, generic in product(values, repeat=2):
        launcher = (
            ("OPENBLAS_NUM_THREADS", specific),
            ("OMP_NUM_THREADS", generic),
        )
        for (
            observed,
            loaded_before,
            available,
            limiter_installed,
            snapshot_available,
        ) in product(
            (None, 1, 2), (False, True), (False, True), (False, True), (False, True)
        ):
            before_launcher = dict(launcher)
            actual = _classify_runtime(
                observed,
                loaded_before,
                launcher,
                ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"),
                available,
                limiter_installed,
                startup_pool_snapshot_available=snapshot_available,
            )
            explicit_non_one = specific not in (None, "1") or generic not in (None, "1")
            row_matches = (
                not snapshot_available,
                snapshot_available and observed is None and not available,
                snapshot_available and observed is None and loaded_before and available,
                snapshot_available and observed is None and not loaded_before and available,
                snapshot_available and observed is not None and explicit_non_one,
                snapshot_available
                and observed is not None
                and loaded_before
                and not explicit_non_one
                and not limiter_installed,
                snapshot_available
                and observed is not None
                and loaded_before
                and not explicit_non_one
                and limiter_installed,
                snapshot_available
                and observed is not None
                and not loaded_before
                and not explicit_non_one,
            )
            assert sum(row_matches) == 1, (
                observed,
                loaded_before,
                specific,
                generic,
                available,
                limiter_installed,
                snapshot_available,
                row_matches,
            )
            if not snapshot_available:
                assert actual == snapshot_unavailable
            elif limiter_installed:
                assert actual == expected[(specific, generic)][index], (
                    observed, loaded_before, specific, generic, available, actual
                )
            elif row_matches[5]:
                assert actual == limiter_unavailable
            assert dict(launcher) == before_launcher
            cells += 1
            if snapshot_available and limiter_installed:
                index += 1
        index = 0
    assert sum(len(row) for row in expected.values()) == 108
    assert cells == 432


def test_blas_policy_limits_only_unoverridden_runtime_with_mapping() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [
    {"internal_api": "openblas", "prefix": "libopenblas", "filepath": "/fake/libopenblas.so", "version": "1", "num_threads": 4},
    {"internal_api": "mkl", "prefix": "libmkl_rt", "filepath": "/fake/libmkl_rt.so", "version": "2", "num_threads": 4},
]
calls = []
def fake_limits(*, limits):
    calls.append(limits)
    return object()
policy._threadpoolctl = lambda: (lambda: pools, fake_limits)
policy.prepare()
assert calls == [{"libopenblas": 1}]
assert policy._limiter is not None
print("eligible mapping=" + str(calls[0]))
''',
        overrides={"MKL_NUM_THREADS": "2"},
    )
    assert output == "eligible mapping={'libopenblas': 1}"


def test_blas_policy_build_name_projects_resolved_api() -> None:
    output = _run(
        _LOAD
        + r'''
assert policy._api_for_build_name("OpenBLAS64") == "openblas"
print("build name projects matched API")
'''
    )
    assert output == "build name projects matched API"


def test_blas_policy_fallback_api_keeps_readable_build_with_observed_pool() -> None:
    for build_name in ("blis", "customblas"):
        output = _run(
            _LOAD
            + f'''\nbuild_name = {build_name!r}
pool = {{
    "internal_api": build_name,
    "prefix": "lib" + build_name,
    "filepath": "/fake/lib" + build_name + ".so",
    "version": "1",
    "num_threads": 4,
}}
policy._build_records = lambda: (("numpy", build_name, True),)
def fake_limits(*, limits):
    assert limits == {{"lib" + build_name: 1}}
    pool["num_threads"] = 1
    return object()
policy._threadpoolctl = lambda: (lambda: [pool], fake_limits)
policy.prepare()
state = policy.finish()
assert len(state.runtimes) == 1
runtime = state.runtimes[0]
assert runtime.api == build_name.lower()
assert runtime.build_records == (("numpy", build_name,),)
assert runtime.observable and runtime.num_threads == 1
assert runtime.how_obtained == "threadpoolctl-after-load"
print(runtime.api + " records=" + str(len(state.runtimes)))
'''
        )
        assert output == f"{build_name} records=1"


def test_blas_policy_runtime_controls_project_resolved_row() -> None:
    output = _run(
        _LOAD
        + r'''
assert policy._controls_for_runtime("OpenBLAS64") == (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"
)
print("runtime controls project matched row")
'''
    )
    assert output == "runtime controls project matched row"


def test_blas_policy_keeps_same_api_pools_as_separate_entries() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [
    {"internal_api": "openblas", "prefix": "libopenblas", "filepath": "/first/libopenblas.so", "version": "one", "num_threads": 1},
    {"internal_api": "openblas", "prefix": "libopenblas", "filepath": "/second/libopenblas.so", "version": "two", "num_threads": 1},
]
policy._threadpoolctl = lambda: (lambda: pools, lambda **kwargs: object())
policy.prepare()
state = policy.finish()
runtimes = [runtime for runtime in state.runtimes if runtime.api == "openblas"]
assert [(item.library_filename, item.version) for item in runtimes] == [
    ("libopenblas.so", "one"), ("libopenblas.so", "two")
]
print("two same-api pools retained")
'''
    )
    assert output == "two same-api pools retained"


def test_blas_policy_classifies_same_api_pool_load_order_per_library() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [
    {"internal_api": "openblas", "prefix": "first", "filepath": "/first/libopenblas.so", "version": "one", "num_threads": 1},
]
policy._threadpoolctl = lambda: (lambda: pools, lambda **kwargs: object())
policy.prepare()
pools.append(
    {"internal_api": "openblas", "prefix": "second", "filepath": "/second/libopenblas.so", "version": "two", "num_threads": 1}
)
runtimes = [item for item in policy.finish().runtimes if item.api == "openblas"]
assert [(item.library_filename, item.how_obtained) for item in runtimes] == [
    ("libopenblas.so", "threadpoolctl-after-load"),
    ("libopenblas.so", "variables-before-load"),
]
print("same-api pool provenance is per library")
'''
    )
    assert output == "same-api pool provenance is per library"


def test_blas_policy_identity_distinguishes_same_path_name_and_version() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [
    {"internal_api": "openblas", "prefix": "libopenblas", "filepath": "/first/libopenblas.so", "version": "1", "num_threads": 4},
]
calls = []
def fake_limits(*, limits):
    calls.append(limits)
    for pool in pools:
        if pool["prefix"] in limits:
            pool["num_threads"] = limits[pool["prefix"]]
    return object()
policy._threadpoolctl = lambda: (lambda: pools, fake_limits)
policy.prepare()
pools.append(
    {"internal_api": "openblas", "prefix": "libopenblas", "filepath": "/second/libopenblas.so", "version": "1", "num_threads": 1}
)
state = policy.finish()
runtimes = [item for item in state.runtimes if item.api == "openblas"]
assert [item.how_obtained for item in runtimes] == [
    "threadpoolctl-after-load", "variables-before-load"
]
assert [item.library_filename for item in runtimes] == [
    "libopenblas.so", "libopenblas.so"
]
assert "/first/" not in repr(state) and "/second/" not in repr(state)
assert calls == [{"libopenblas": 1}]
print("same basename/version paths retain distinct provenance")
'''
    )
    assert output == "same basename/version paths retain distinct provenance"


def test_blas_policy_unobservable_preloaded_backend_is_not_in_effect() -> None:
    output = _run(
        _LOAD
        + r'''
from types import SimpleNamespace
scipy = SimpleNamespace(show_config=lambda **kwargs: {"Build Dependencies": {"blas": {"name": "Accelerate"}}})
sys.modules["scipy"] = scipy
policy._threadpoolctl = lambda: (lambda: [], lambda **kwargs: object())
policy.prepare()
state = policy.finish()
runtime = next(item for item in state.runtimes if any(module == "scipy" for module, _name in item.build_records))
assert runtime.name == "Accelerate"
assert ("scipy", "Accelerate") in runtime.build_records
assert runtime.num_threads is None and not runtime.observable
assert runtime.how_obtained == "not-in-effect"
assert "loaded before start-up" in runtime.reason
assert "not controllable" in runtime.reason
assert "VECLIB_MAXIMUM_THREADS set after load" in runtime.reason
print("preloaded backend not in effect")
'''
    )
    assert output == "preloaded backend not in effect"


def test_blas_policy_unobservable_backend_loaded_after_prepare_uses_variables() -> None:
    output = _run(
        _LOAD
        + r'''
policy._threadpoolctl = lambda: (lambda: [], lambda **kwargs: object())
policy.prepare()
from types import SimpleNamespace
sys.modules["scipy"] = SimpleNamespace(
    show_config=lambda **kwargs: {"Build Dependencies": {"blas": {"name": "Accelerate"}}}
)
runtime = next(item for item in policy.finish().runtimes if any(module == "scipy" for module, _name in item.build_records))
assert runtime.name == "Accelerate"
assert runtime.how_obtained == "variables-before-load"
assert not runtime.limit_known_to_bind and not runtime.observable
print("post-start backend uses variables before load")
'''
    )
    assert output == "post-start backend uses variables before load"


def test_blas_policy_known_to_bind_requires_matching_observed_size() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [{"internal_api": "openblas", "filepath": "/fake/libopenblas.so", "version": "1", "num_threads": 1}]
policy._threadpoolctl = lambda: (lambda: pools, lambda **kwargs: object())
policy.prepare()
runtime = next(item for item in policy.finish().runtimes if item.api == "openblas")
assert runtime.how_obtained == "explicit-launcher-value"
assert not runtime.limit_known_to_bind
print("observed override mismatch")
''',
        overrides={"OPENBLAS_NUM_THREADS": "2"},
    )
    assert output == "observed override mismatch"


def test_blas_policy_records_conflicting_launcher_values_without_rewriting() -> None:
    output = _run(
        _LOAD
        + r'''
policy.prepare()
import numpy
state = policy.finish()
from threadpoolctl import threadpool_info
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
assert os.environ["OPENBLAS_NUM_THREADS"] == "1" and os.environ["OMP_NUM_THREADS"] == "2"
assert dict(state.launcher_values)["OPENBLAS_NUM_THREADS"] == "1"
assert dict(state.launcher_values)["OMP_NUM_THREADS"] == "2"
assert any(notice.kind == "conflicting-launcher-values" for notice in state.notices)
assert any(runtime.api == "openblas" and runtime.num_threads == pool["num_threads"] for runtime in state.runtimes)
print("conflict recorded; observed pool=" + str(pool["num_threads"]))
''',
        overrides={"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "2"},
    )
    assert output.startswith("conflict recorded; observed pool=")


def test_blas_policy_continues_when_threadpoolctl_import_is_unavailable() -> None:
    output = _run(
        _LOAD
        + r'''
import builtins
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "threadpoolctl":
        raise ImportError("blocked for test")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
policy.prepare()
assert all(os.environ.get(name) == "1" for name in policy.THREAD_ENV_VARS)
import numpy
state = policy.finish()
assert any(notice.kind == "not-in-effect" and dict(notice.values)["reason"] == "threadpoolctl limiter could not be installed at start-up" for notice in state.notices)
print("not in effect: limiter unavailable at start-up")
'''
    )
    assert output == "not in effect: limiter unavailable at start-up"


def test_blas_policy_recovers_readback_after_startup_import_failure() -> None:
    late_output = _run(
        _LOAD
        + r'''
import builtins
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "threadpoolctl":
        raise ImportError("blocked for test")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
policy.prepare()
builtins.__import__ = real_import
import numpy
state = policy.finish()
runtime = next(item for item in state.runtimes if item.api == "openblas")
assert any(
    notice.kind == "not-in-effect"
    and dict(notice.values)["reason"] == "threadpoolctl limiter could not be installed at start-up"
    for notice in state.notices
)
assert runtime.num_threads == 1
assert runtime.how_obtained == "not-in-effect"
assert not runtime.limit_known_to_bind
assert runtime.reason == "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed"
print("recovered late pool count=1; provenance unattributed")
'''
    )
    assert late_output == "recovered late pool count=1; provenance unattributed"

    preloaded_output = _run(
        _LOAD
        + r'''
import numpy
import builtins
from threadpoolctl import threadpool_info
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "threadpoolctl":
        raise ImportError("blocked for test")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
policy.prepare()
builtins.__import__ = real_import
observed = next(pool["num_threads"] for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(item for item in policy.finish().runtimes if item.api == "openblas")
assert runtime.num_threads == observed
assert runtime.how_obtained == "not-in-effect"
assert not runtime.limit_known_to_bind
assert runtime.reason == "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed"
print("preloaded pool read back without a startup limiter")
'''
    )
    assert preloaded_output == "preloaded pool read back without a startup limiter"


def test_blas_policy_does_not_infer_library_load_order_without_startup_pool_snapshot() -> None:
    output = _run(
        _LOAD
        + r'''
pools = [
    {"internal_api": "openblas", "prefix": "first", "filepath": "/first/libopenblas.so", "version": "1", "num_threads": 4},
    {"internal_api": "openblas", "prefix": "second", "filepath": "/second/libopenblas.so", "version": "1", "num_threads": 1},
]
policy._threadpoolctl = lambda: None
policy._build_records = lambda: (("numpy", "openblas", True),)
policy.prepare()
policy._build_records = lambda: (("numpy", "openblas", True), ("scipy", "OpenBLAS64", True))
policy._threadpoolctl = lambda: (lambda: pools, lambda **kwargs: object())
observations, available = policy._gather_runtime_observations()
assert available and [item.num_threads for item in observations if item.api == "openblas"] == [4, 1]
assert not any(item.loaded_before for item in observations if item.api == "openblas")
runtimes = [item for item in policy.finish().runtimes if item.api == "openblas"]
reason = "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed"
assert [item.num_threads for item in runtimes] == [4, 1]
assert [item.how_obtained for item in runtimes] == ["not-in-effect", "not-in-effect"]
assert [item.limit_known_to_bind for item in runtimes] == [False, False]
assert [item.reason for item in runtimes] == [reason, reason]
print("both pools read; load order unattributed")
'''
    )
    assert output == "both pools read; load order unattributed"


def test_blas_policy_reports_scipy_first_backend_as_unobservable() -> None:
    output = _run(
        _LOAD
        + r'''
import scipy.linalg
policy.prepare()
state = policy.finish()
build_name = scipy.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"]
runtime = next(runtime for runtime in state.runtimes if any(module == "scipy" for module, _name in runtime.build_records))
assert ("scipy", build_name) in runtime.build_records
if not runtime.observable:
    assert runtime.name == build_name
    assert runtime.num_threads is None
    assert runtime.how_obtained == "not-in-effect"
    print("scipy backend=" + runtime.name + " not observable")
else:
    print("scipy backend=" + runtime.name + " observable")
'''
    )
    assert output.startswith("scipy backend=")


def test_blas_policy_prepare_and_finish_are_one_shot() -> None:
    output = _run(
        _LOAD
        + r'''
policy.prepare()
snapshot = policy._snapshot()
os.environ["OMP_NUM_THREADS"] = "7"
policy.prepare()
assert policy._snapshot() is snapshot
first = policy.finish()
os.environ["OPENBLAS_NUM_THREADS"] = "9"
second = policy.finish()
assert first is second
assert first.launcher_values == snapshot
print("same snapshot and state")
'''
    )
    assert output == "same snapshot and state"


def test_blas_policy_module_import_is_stdlib_only() -> None:
    output = _run(
        r'''
import importlib.util, sys
for name in ("numpy", "scipy", "threadpoolctl"):
    sys.modules.pop(name, None)
spec = importlib.util.spec_from_file_location("policy_probe", sys.argv[1])
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)
assert not any(name == "numpy" or name.startswith("numpy.") for name in sys.modules)
assert not any(name == "scipy" or name.startswith("scipy.") for name in sys.modules)
assert "threadpoolctl" not in sys.modules
print("stdlib-only")
'''
    )
    assert output == "stdlib-only"


def test_blas_policy_simulator_first_import_has_cached_state() -> None:
    output = _run(
        r'''
import simulator
from threadpoolctl import threadpool_info
state = simulator._blas_policy.state()
assert state is not None
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
assert pool["num_threads"] == 1
assert runtime.how_obtained == "variables-before-load"
print("state=ready pool=1")
'''
    )
    assert output == "state=ready pool=1"


def test_blas_policy_numpy_first_import_limits_loaded_pool() -> None:
    output = _run(
        r'''
import os
import numpy
from threadpoolctl import threadpool_info, threadpool_limits
limiter = threadpool_limits(limits=4)
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.pop(name, None)
import simulator
state = simulator._blas_policy.state()
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
assert runtime.how_obtained == "threadpoolctl-after-load"
assert pool["num_threads"] == 1
print("loaded pool=1")
'''
    )
    assert output == "loaded pool=1"


def test_blas_policy_scipy_first_import_reports_unobservable_backend() -> None:
    output = _run(
        r'''
import scipy.linalg
import simulator
state = simulator._blas_policy.state()
build_name = scipy.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"]
runtime = next(runtime for runtime in state.runtimes if any(module == "scipy" for module, _name in runtime.build_records))
assert ("scipy", build_name) in runtime.build_records
if runtime.observable:
    assert runtime.num_threads is not None
    print("scipy=" + runtime.name + " observable")
else:
    assert runtime.name == build_name
    assert runtime.num_threads is None
    assert runtime.how_obtained == "not-in-effect"
    assert not runtime.limit_known_to_bind
    print("scipy=" + runtime.name + " not observable")
'''
    )
    assert output.startswith("scipy=")


def test_blas_policy_launcher_openblas_two_survives_package_startup() -> None:
    output = _run(
        r'''
import os
import simulator
from threadpoolctl import threadpool_info
state = simulator._blas_policy.state()
pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
runtime = next(runtime for runtime in state.runtimes if runtime.api == "openblas")
assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
assert pool["num_threads"] == 2
assert runtime.how_obtained == "explicit-launcher-value"
print("pool=2")
''',
        overrides={"OPENBLAS_NUM_THREADS": "2"},
    )
    assert output == "pool=2"




def test_blas_policy_package_import_survives_unavailable_threadpoolctl() -> None:
    output = _run(
        r'''
import builtins
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "threadpoolctl":
        raise ImportError("blocked for test")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
import simulator
state = simulator._blas_policy.state()
assert any(notice.kind == "not-in-effect" for notice in state.notices)
assert state.runtimes
assert all(
    runtime.num_threads is None
    and not runtime.observable
    and runtime.how_obtained == "not-in-effect"
    and runtime.reason == "start-up pool snapshot unavailable (threadpoolctl could not be imported at start-up); load order and binding are not attributed"
    for runtime in state.runtimes
)
print("package import completed")
'''
    )
    assert output == "package import completed"


def test_blas_policy_conftest_and_package_share_canonical_module() -> None:
    for conftest_first in (True, False):
        code = (
            r'''
import conftest
owner = sys.modules["simulator._blas_policy"]
snapshot = owner._snapshot()
import simulator
assert owner is simulator._blas_policy
assert owner._snapshot() is snapshot
assert owner.state() is not None
print("same object and snapshot")
'''
            if conftest_first
            else r'''
import simulator
owner = simulator._blas_policy
snapshot = owner._snapshot()
import conftest
assert owner is sys.modules["simulator._blas_policy"]
assert owner is simulator._blas_policy
assert owner._snapshot() is snapshot
print("same object and snapshot")
'''
        )
        assert _run("import sys\n" + code) == "same object and snapshot"


def _spawn_child(queue) -> None:
    import simulator
    from threadpoolctl import threadpool_info

    pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
    queue.put((simulator._blas_policy.state() is not None, pool["num_threads"]))


def test_blas_policy_spawned_child_initializes_its_own_pool() -> None:
    env = os.environ.copy()
    for name in THREAD_NAMES:
        env.pop(name, None)
    env["PYTHONPATH"] = f"/private/tmp/claude-501/oimcc-1f3c82d/src:{Path(__file__).resolve().parents[1]}"
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--spawn-parent"],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "spawn_state=True pool=1"


def _spawn_parent_main() -> None:
    import simulator

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    child = context.Process(target=_spawn_child, args=(queue,))
    child.start()
    result = queue.get(timeout=60)
    child.join(timeout=60)
    assert child.exitcode == 0
    print(f"spawn_state={result[0]} pool={result[1]}")


def test_blas_policy_forked_child_keeps_limited_pool_when_supported() -> None:
    output = _run(
        r'''
import os
if not hasattr(os, "fork"):
    print("fork unavailable")
else:
    import simulator
    from threadpoolctl import threadpool_info
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        pool = next(pool for pool in threadpool_info() if pool["internal_api"] == "openblas")
        os.write(write_fd, str(pool["num_threads"]).encode())
        os._exit(0)
    os.close(write_fd)
    value = os.read(read_fd, 20).decode()
    _, status = os.waitpid(pid, 0)
    assert status == 0
    assert value == "1"
    print("fork pool=1")
'''
    )
    assert output in {"fork pool=1", "fork unavailable"}


def test_blas_policy_real_openimcc_path_runs_after_state_readback() -> None:
    output = _run(
        r'''
import simulator
state = simulator._blas_policy.state()
assert state is not None
from simulator.melt_backend.openimcc_bridge import evaluate_cleaned_melt
result = evaluate_cleaned_melt({"Na2O": 0.5, "SiO2": 0.5}, 1800.0)
assert result.bridge.envelope_status == "inside"
print("state read before representative evaluation")
'''
    )
    assert output == "state read before representative evaluation"


if __name__ == "__main__" and sys.argv[1:] == ["--spawn-parent"]:
    _spawn_parent_main()
