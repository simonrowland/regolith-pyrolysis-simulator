"""Battery scorer workers must close before multiprocessing joins children."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_idle_worker():
    return None, "ready"


def _handle_idle_worker(_resource, request, _diagnostic_log):
    return request


def _start_registered_scorer_worker(pid_file: str) -> None:
    """Install an idle WarmEngineWorker as the scorer's cell singleton."""
    import atexit
    import multiprocessing.util
    import threading

    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.engine_pool import WarmEngineWorker

    process_worker = WarmEngineWorker(
        name="test scorer cell",
        bootstrap=_bootstrap_idle_worker,
        handler=_handle_idle_worker,
        daemon=False,
    )
    cell_worker = object.__new__(binary_pot_battery._IsolatedCellWorker)
    cell_worker.engine = "thermoengine"
    cell_worker._lock = threading.Lock()
    cell_worker._worker = process_worker
    binary_pot_battery._ISOLATED_CELL_WORKERS["thermoengine"] = cell_worker
    process_worker.start()
    assert process_worker.process is not None
    Path(pid_file).write_text(str(process_worker.process.pid), encoding="ascii")

    # Exercise the ordering hazard documented by residue's worker owner:
    # multiprocessing's exit handler may run before ordinary atexit cleanup.
    atexit.unregister(multiprocessing.util._exit_function)
    atexit.register(multiprocessing.util._exit_function)


class BatteryScoreWorkerShutdownTest(unittest.TestCase):
    def test_worker_is_stopped_before_normal_and_exceptional_exit(self) -> None:
        child_script = (
            "import sys\n"
            "from test_battery_score_worker_shutdown import "
            "_start_registered_scorer_worker\n"
            "_start_registered_scorer_worker(sys.argv[1])\n"
            "if sys.argv[2] == 'exception':\n"
            "    raise RuntimeError('intentional shutdown test')\n"
        )
        env = os.environ.copy()
        import_paths = [str(ROOT), str(Path(__file__).parent)]
        if env.get("PYTHONPATH"):
            import_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(import_paths)

        for mode, expected_code in (("normal", 0), ("exception", 1)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                pid_file = Path(tmp) / "worker.pid"
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        child_script,
                        str(pid_file),
                        mode,
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                startup_deadline = time.monotonic() + 60.0
                while not pid_file.exists() and process.poll() is None:
                    if time.monotonic() >= startup_deadline:
                        process.kill()
                        _stdout, stderr = process.communicate()
                        self.fail(f"worker did not start: {stderr}")
                    time.sleep(0.02)
                if not pid_file.exists():
                    _stdout, stderr = process.communicate()
                    self.fail(f"worker exited before startup: {stderr}")

                started_after_worker_ready = time.monotonic()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired as exc:
                    if pid_file.exists():
                        try:
                            pid = int(pid_file.read_text(encoding="ascii"))
                            os.kill(pid, signal.SIGKILL)
                        except (OSError, ValueError):
                            pass
                    process.kill()
                    process.communicate()
                    self.fail(f"worker shutdown hung during {mode} exit: {exc}")

                _stdout, stderr = process.communicate()
                self.assertEqual(
                    process.returncode,
                    expected_code,
                    msg=stderr,
                )
                self.assertLess(time.monotonic() - started_after_worker_ready, 5.0)


if __name__ == "__main__":
    unittest.main()
