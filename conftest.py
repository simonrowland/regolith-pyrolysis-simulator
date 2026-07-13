from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


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
                pipe_diameter_m=overhead_model.pipe_diameter_m,
                gas_temperature_C=transport["pipe_temperature_C"],
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
