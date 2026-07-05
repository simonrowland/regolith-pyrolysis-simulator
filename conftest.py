from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path


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
