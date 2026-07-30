from __future__ import annotations

import os
import re
import shutil
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


_SESSION_WATCHDOG: SessionWatchdog | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    global _SESSION_WATCHDOG
    _SESSION_WATCHDOG = SessionWatchdog()
    _SESSION_WATCHDOG.start()


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
    del session, exitstatus
    # t-420: close warm engine pools before the b-093 watchdog disarms, so the
    # bounded-cleanup path still runs under session-safety supervision.
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
