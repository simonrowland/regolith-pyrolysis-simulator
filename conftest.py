from __future__ import annotations

import importlib.util as _importlib_util
from pathlib import Path as _Path
import sys as _sys

_BLAS_POLICY_NAME = "simulator._blas_policy"
_blas_policy = _sys.modules.get(_BLAS_POLICY_NAME)
if _blas_policy is None:
    if "simulator" in _sys.modules:
        from importlib import import_module as _import_module

        _blas_policy = _import_module(_BLAS_POLICY_NAME)
    else:
        _policy_path = _Path(__file__).resolve().parent / "simulator" / "_blas_policy.py"
        _policy_spec = _importlib_util.spec_from_file_location(_BLAS_POLICY_NAME, _policy_path)
        if _policy_spec is None or _policy_spec.loader is None:
            raise ImportError(f"cannot load {_BLAS_POLICY_NAME} from {_policy_path}")
        _blas_policy = _importlib_util.module_from_spec(_policy_spec)
        _sys.modules[_BLAS_POLICY_NAME] = _blas_policy
        try:
            _policy_spec.loader.exec_module(_blas_policy)
            _blas_policy.prepare()
        except BaseException:
            if _sys.modules.get(_BLAS_POLICY_NAME) is _blas_policy:
                del _sys.modules[_BLAS_POLICY_NAME]
            raise
else:
    _blas_policy.prepare()

from collections.abc import Iterator

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from _pytest_session_safety import (
    CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    SessionWatchdog,
    gateway_child_label,
    install_bounded_execnet_bootstrap,
    install_bounded_gateway_rinfo,
)
from tests.e2e.playwright_support import (
    PLAYWRIGHT_SKIP_MARK,
    PLAYWRIGHT_SYNC_API,
)


pytest_plugins = ("_pytest_loadgroup_order",)


def _safe_worker_id(worker_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", worker_id) or "master"


def _configure_worker_cache_isolation() -> None:
    """Keep xdist workers from sharing scratch/cache SQLite files."""

    raw_worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if not raw_worker_id:
        return

    repo_root = Path(__file__).resolve().parent
    worker_id = _safe_worker_id(raw_worker_id)
    default_cache_root = (
        Path(tempfile.gettempdir()) / "regolith-pytest-worker-cache" / repo_root.name
    )
    cache_root = Path(
        os.environ.get("REGOLITH_PYTEST_WORKER_CACHE_ROOT", default_cache_root)
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    worker_root = Path(tempfile.mkdtemp(prefix=f"{worker_id}-", dir=cache_root))

    tmp_dir = worker_root / "tmp"
    xdg_cache = worker_root / "xdg-cache"
    grind_home = worker_root / "grind-home"
    optimizer_output = worker_root / "optimizer-output"
    for path in (tmp_dir, xdg_cache, grind_home, optimizer_output):
        path.mkdir(parents=True, exist_ok=True)

    os.environ["REGOLITH_PYTEST_WORKER_CACHE"] = str(worker_root)
    os.environ["TMPDIR"] = str(tmp_dir)
    os.environ["TEMP"] = str(tmp_dir)
    os.environ["TMP"] = str(tmp_dir)
    os.environ["XDG_CACHE_HOME"] = str(xdg_cache)
    os.environ["GRIND_HOME"] = str(grind_home)
    os.environ["REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR"] = str(optimizer_output)

    # tempfile caches the resolved temp directory after first use; force this
    # worker to the isolated root even if another import touched tempfile early.
    tempfile.tempdir = str(tmp_dir)

    import atexit

    atexit.register(shutil.rmtree, worker_root, ignore_errors=True)


_configure_worker_cache_isolation()


@pytest.fixture(autouse=True)
def _isolate_vapor_payload_cache_between_tests():
    module = sys.modules.get("engines.builtin.foulant_disposition")
    if module is not None:
        module._VAPOR_PAYLOAD_CACHE.clear()
    yield
    module = sys.modules.get("engines.builtin.foulant_disposition")
    if module is not None:
        module._VAPOR_PAYLOAD_CACHE.clear()


@pytest.fixture
def isolated_engine_config(tmp_path: Path) -> Iterator[Path]:
    from simulator import engine_local_config
    from tests.binding_admission_fixtures import isolate_engine_local_config

    source_path = engine_local_config.config_path()
    host_config_path = tmp_path / "host" / "engines" / source_path.name
    if source_path.is_file():
        host_config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, host_config_path)

    with isolate_engine_local_config(
        tmp_path / "isolated" / "engines",
        source_path=source_path,
    ):
        yield host_config_path


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if PLAYWRIGHT_SYNC_API is not None:
        return
    for item in items:
        if item.get_closest_marker("browser_e2e") is not None:
            item.add_marker(PLAYWRIGHT_SKIP_MARK)


# CI-speed (process-scoped VapoRock warm-boot cache): share one VR-5 warm pool
# across lazy VapoRockProvider constructions within each pytest process.
# Golden-neutral — equilibrate still runs; only the import/worker boot is
# reused. Opt out with REGOLITH_VAPOROCK_SESSION_WARM=0. Runs at import so
# xdist workers inherit the same default as the controller.
if os.environ.get("REGOLITH_VAPOROCK_SESSION_WARM") is None:
    os.environ["REGOLITH_VAPOROCK_SESSION_WARM"] = "1"


_SESSION_WATCHDOG: SessionWatchdog | None = None
DOCS_PRIVATE_MANIFEST_STRICT_ENV = "REGOLITH_DOCS_PRIVATE_MANIFEST_STRICT"
_DOCS_PRIVATE_AUDIT_ROOT: Path | None = None
_DOCS_PRIVATE_MISSING_OPENS: set[tuple[str, str]] = set()
_DOCS_PRIVATE_ACTIVE_TEST_NODEID: str | None = None
_DOCS_PRIVATE_AUDIT_INSTALLED = False


def _preflight_docs_private_inputs(repo_root: Path, manifest_path: Path) -> None:
    if DOCS_PRIVATE_MANIFEST_STRICT_ENV not in os.environ:
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise pytest.UsageError(
            f"{DOCS_PRIVATE_MANIFEST_STRICT_ENV} is set, but cannot read "
            f"docs-private input manifest {manifest_path}: {exc}"
        ) from exc

    missing: list[str] = []
    for entry in manifest["entries"]:
        kind = entry["kind"]
        path = entry["path"]
        reader = entry["reader"]
        input_path = repo_root / path
        if kind == "file":
            if not input_path.is_file():
                missing.append(f"{path} (read by {reader})")
        elif kind == "directory":
            if not input_path.is_dir():
                missing.append(f"{path} (read by {reader})")
        elif kind == "glob":
            required = entry["required"]
            if not required:
                missing.append(f"{path} (no required named files; read by {reader})")
                continue
            matched = {
                match.relative_to(repo_root).as_posix()
                for match in repo_root.glob(path)
            }
            for required_entry in required:
                required_path = required_entry["path"]
                required_reader = required_entry["reader"]
                if required_path not in matched or not (
                    repo_root / required_path
                ).is_file():
                    missing.append(
                        f"{required_path} (required by glob {path}; "
                        f"read by {required_reader})"
                    )
        else:
            raise pytest.UsageError(
                f"invalid docs-private input kind {kind!r} for {path!r} "
                f"in {manifest_path}"
            )

    if missing:
        details = "\n".join(f"  - {item}" for item in missing)
        raise pytest.UsageError(
            f"{DOCS_PRIVATE_MANIFEST_STRICT_ENV} is set, but required docs-private "
            f"inputs are missing or unmatched:\n{details}"
        )


def _record_missing_docs_private_open(event: str, args: tuple[object, ...]) -> None:
    if event != "open" or not args or _DOCS_PRIVATE_AUDIT_ROOT is None:
        return

    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else None
    if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
        return
    if mode is None and isinstance(flags, int):
        if flags & os.O_ACCMODE != os.O_RDONLY:
            return

    try:
        opened = Path(os.fsdecode(os.fspath(args[0])))
        if not opened.is_absolute():
            opened = Path.cwd() / opened
        opened = opened.resolve()
        private_root = (_DOCS_PRIVATE_AUDIT_ROOT / "docs-private").resolve()
        if not opened.is_relative_to(private_root) or opened.exists():
            return
        relative_path = opened.relative_to(_DOCS_PRIVATE_AUDIT_ROOT).as_posix()
    except (OSError, RuntimeError, TypeError, ValueError):
        return

    nodeid = _DOCS_PRIVATE_ACTIVE_TEST_NODEID or "<session setup or collection>"
    _DOCS_PRIVATE_MISSING_OPENS.add((relative_path, nodeid))


def _install_docs_private_open_audit(repo_root: Path) -> bool:
    global _DOCS_PRIVATE_AUDIT_ROOT, _DOCS_PRIVATE_AUDIT_INSTALLED
    if (
        DOCS_PRIVATE_MANIFEST_STRICT_ENV not in os.environ
        or _DOCS_PRIVATE_AUDIT_INSTALLED
    ):
        return False

    _DOCS_PRIVATE_AUDIT_ROOT = repo_root.resolve()
    sys.addaudithook(_record_missing_docs_private_open)
    _DOCS_PRIVATE_AUDIT_INSTALLED = True
    return True


def _fail_on_missing_docs_private_opens(session: pytest.Session) -> str | None:
    if not _DOCS_PRIVATE_MISSING_OPENS:
        return None

    details = "\n".join(
        f"  - {path} (opened by {nodeid})"
        for path, nodeid in sorted(_DOCS_PRIVATE_MISSING_OPENS)
    )
    message = (
        "strict docs-private audit found missing docs-private inputs:\n"
        f"{details}\n"
        "Rerun scripts/derive_docs_private_inputs.py and add the inputs to "
        "tests/docs_private_inputs.json."
    )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    terminalreporter = session.config.pluginmanager.getplugin("terminalreporter")
    if terminalreporter is None:
        print(message, file=sys.stderr)
    else:
        terminalreporter.write_sep("!", message)
    return message


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    repo_root = Path(session.config.rootpath)
    _preflight_docs_private_inputs(
        repo_root,
        repo_root / "tests" / "docs_private_inputs.json",
    )
    _install_docs_private_open_audit(repo_root)
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    global _SESSION_WATCHDOG
    _SESSION_WATCHDOG = SessionWatchdog()
    _SESSION_WATCHDOG.start()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Iterator[object]:
    del nextitem
    if not _DOCS_PRIVATE_AUDIT_INSTALLED:
        yield
        return
    global _DOCS_PRIVATE_ACTIVE_TEST_NODEID
    previous_nodeid = _DOCS_PRIVATE_ACTIVE_TEST_NODEID
    _DOCS_PRIVATE_ACTIVE_TEST_NODEID = item.nodeid
    try:
        yield
    finally:
        _DOCS_PRIVATE_ACTIVE_TEST_NODEID = previous_nodeid


@pytest.hookimpl(optionalhook=True, tryfirst=True)
def pytest_xdist_newgateway(gateway: object) -> None:
    install_bounded_gateway_rinfo(gateway)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_setupnodes(config: pytest.Config, specs: list[object]) -> None:
    del config, specs
    install_bounded_execnet_bootstrap()


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: object) -> None:
    if _SESSION_WATCHDOG is None:
        return
    gateway = node.gateway
    _SESSION_WATCHDOG.arm_child_handshake(
        key=gateway.id,
        child=gateway_child_label(gateway),
        phase="worker-ready",
        process=gateway._io.popen,
        timeout_seconds=CHILD_HANDSHAKE_TIMEOUT_SECONDS,
    )


@pytest.hookimpl(optionalhook=True)
def pytest_testnodeready(node: object) -> None:
    if _SESSION_WATCHDOG is not None:
        _SESSION_WATCHDOG.disarm_child_handshake(node.gateway.id)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: object, error: object | None) -> None:
    del error
    if _SESSION_WATCHDOG is not None:
        _SESSION_WATCHDOG.disarm_child_handshake(node.gateway.id)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    del exitstatus
    _fail_on_missing_docs_private_opens(session)
    # t-420: close warm engine pools before the b-093 watchdog disarms, so the
    # bounded-cleanup path still runs under session-safety supervision.
    # Drop the process-scoped VapoRock warm-boot cache first so its pools are
    # closed under the same session-safety window as every other engine pool.
    try:
        from simulator.melt_backend.vaporock import clear_session_backend_cache

        clear_session_backend_cache()
    except Exception:  # noqa: BLE001 - teardown must not mask suite status
        pass
    from simulator.engine_pool import close_all_engine_pools

    close_all_engine_pools(cancel_pending=True)
    global _SESSION_WATCHDOG
    if _SESSION_WATCHDOG is not None:
        _SESSION_WATCHDOG.stop()
        _SESSION_WATCHDOG = None


@pytest.fixture
def production_configured_condensation_route(monkeypatch):
    """Auto-configure direct condensation routes from live transport state."""

    from simulator.condensation import CondensationModel
    from simulator.core import PyrolysisSimulator
    from simulator.overhead import OverheadGasModel
    from simulator.state import clamp_stir_factor

    route = CondensationModel.route

    def configured_route(model, evap_flux, melt):
        if not model._knudsen_policy_configured:
            segment_temperatures_C = {
                segment.name: segment.wall_temperature_C
                for segment in model.pipe_segments
            }
            overhead_model = OverheadGasModel(
                {
                    "liner_temperature_C": model.wall_temperature_C,
                    "pipe_segment_temperatures_C": {
                        "default_C": model.wall_temperature_C,
                        "segments": segment_temperatures_C,
                    },
                }
            )
            transport = overhead_model.estimate_transport_state(evap_flux, melt)
            carrier_context = SimpleNamespace(
                melt=melt,
                setpoints={},
                _normalize_condensation_carrier_gas=(
                    PyrolysisSimulator._normalize_condensation_carrier_gas
                ),
            )
            model.configure_operating_conditions(
                wall_temperature_C=transport["pipe_temperature_C"],
                overhead_pressure_mbar=transport["pressure_mbar"],
                species_partial_pressures_mbar=(
                    overhead_model.species_partial_pressures(
                        evap_flux,
                        transport["vapor_pressure_mbar"],
                    )
                ),
                pipe_diameter_m=overhead_model.pipe_diameter_m,
                gas_temperature_C=transport["conductance_temperature_C"],
                stage_area_m2_by_stage=transport["stage_area_m2_by_stage"],
                stage_area_geometry_provenance_notice=transport.get(
                    "stage_area_geometry_provenance_notice", {}
                ),
                pipe_segment_temperatures_C=(
                    overhead_model.resolve_pipe_segment_temperatures_C(
                        list(segment_temperatures_C), melt
                    )
                ),
                stir_factor=clamp_stir_factor(
                    getattr(getattr(melt, "stir_state", None), "axial", None)
                ),
                radial_stir_factor=clamp_stir_factor(
                    getattr(getattr(melt, "stir_state", None), "radial", None)
                ),
                carrier_gas=PyrolysisSimulator._resolve_condensation_carrier_gas(
                    carrier_context
                ),
                campaign_name=str(getattr(melt.campaign, "name", "")),
                campaign_hour=float(getattr(melt, "campaign_hour", 0.0) or 0.0),
            )
        return route(model, evap_flux, melt)

    monkeypatch.setattr(CondensationModel, "route", configured_route)


@pytest.fixture
def synthetic_sio_stage_authority(monkeypatch):
    """Bounded 100 Pa authority for direct SiO stage-routing tests."""

    from simulator import condensation
    from simulator.state import EvaporationFlux

    pressure_pa = 100.0
    valid_range_K = (1323.0, 1324.0)
    original = condensation._try_antoine_psat_pa

    def bounded_pressure(species, temperature_K, **kwargs):
        resolved_pa, refused = original(species, temperature_K, **kwargs)
        low_K, high_K = valid_range_K
        if species == "SiO" and refused and low_K <= temperature_K <= high_K:
            return pressure_pa, False
        return resolved_pa, refused

    def flux() -> EvaporationFlux:
        return EvaporationFlux(
            species_kg_hr={"SiO": 1.0},
            total_kg_hr=1.0,
            carrier_authority_by_species={
                "SiO": {
                    "species_id": "SiO",
                    "pressure": {
                        "kind": "value",
                        "pa": pressure_pa,
                        "valid_range_K": list(valid_range_K),
                    },
                    "flux": {"kind": "eligible", "alpha_ref": "test:SiO"},
                    "is_union_flux_eligible": True,
                    "is_flux_active": True,
                    "validation_status": "validated",
                    "verdict_status": "authoritative",
                    "certification_ceiling": "validated_point",
                }
            },
        )

    monkeypatch.setattr(
        condensation,
        "_try_antoine_psat_pa",
        bounded_pressure,
    )
    return SimpleNamespace(flux=flux)
