from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ENV_KEYS = (
    "TMPDIR",
    "TEMP",
    "TMP",
    "XDG_CACHE_HOME",
    "GRIND_HOME",
    "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR",
)


def _run_import_conftest(code: str, env: dict[str, str]) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_cache_isolation_is_noop_without_xdist_worker(tmp_path):
    env = os.environ.copy()
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("REGOLITH_PYTEST_WORKER_CACHE", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    for key in ENV_KEYS:
        env[key] = str(tmp_path / key.lower())

    result = _run_import_conftest(
        """
import json
import os
import tempfile

keys = (
    "TMPDIR",
    "TEMP",
    "TMP",
    "XDG_CACHE_HOME",
    "GRIND_HOME",
    "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR",
)
before = {key: os.environ.get(key) for key in keys}
import conftest  # noqa: F401
after = {key: os.environ.get(key) for key in keys}
print(json.dumps({
    "before": before,
    "after": after,
    "worker_cache": os.environ.get("REGOLITH_PYTEST_WORKER_CACHE"),
    "tempfile_tempdir": tempfile.tempdir,
}))
""",
        env,
    )

    assert result["after"] == result["before"]
    assert result["worker_cache"] is None
    assert result["tempfile_tempdir"] is None


def test_xdist_worker_cache_is_fresh_and_cleaned_up(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    cache_root = tmp_path / "worker-cache"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    env["PYTEST_XDIST_WORKER"] = "gw-test"
    env["REGOLITH_PYTEST_WORKER_CACHE_ROOT"] = str(cache_root)

    result = _run_import_conftest(
        """
import json
import os
import tempfile
from pathlib import Path

import conftest  # noqa: F401

worker_root = Path(os.environ["REGOLITH_PYTEST_WORKER_CACHE"])
keys = (
    "TMPDIR",
    "TEMP",
    "TMP",
    "XDG_CACHE_HOME",
    "GRIND_HOME",
    "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR",
)
print(json.dumps({
    "worker_root": str(worker_root),
    "worker_root_exists": worker_root.exists(),
    "env": {key: os.environ.get(key) for key in keys},
    "tempfile_tempdir": tempfile.tempdir,
}))
""",
        env,
    )

    worker_root = Path(str(result["worker_root"]))
    assert worker_root.parent == cache_root
    assert worker_root.name.startswith("gw-test-")
    assert result["worker_root_exists"] is True
    assert result["tempfile_tempdir"] == str(worker_root / "tmp")
    assert result["env"] == {
        "TMPDIR": str(worker_root / "tmp"),
        "TEMP": str(worker_root / "tmp"),
        "TMP": str(worker_root / "tmp"),
        "XDG_CACHE_HOME": str(worker_root / "xdg-cache"),
        "GRIND_HOME": str(worker_root / "grind-home"),
        "REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR": str(worker_root / "optimizer-output"),
    }
    assert not worker_root.exists()
