from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path


def test_battery_score_timeout_reaps_spawn_worker_before_process_exit(
    tmp_path: Path,
) -> None:
    driver = tmp_path / "score_worker_exit.py"
    output = tmp_path / "score-output.txt"
    driver.write_text(
        textwrap.dedent(
            """
            import multiprocessing
            import sys
            import threading
            from pathlib import Path

            from simulator.battery.residue import _evaluate_openimcc_isolated

            def hanging_engine(**_kwargs):
                threading.Event().wait(30)

            def main():
                try:
                    _evaluate_openimcc_isolated(hanging_engine, timeout_s=0.1)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("forced engine timeout did not fire")

                Path(sys.argv[1]).write_text("score output written\\n")
                remaining = [
                    p.pid for p in multiprocessing.active_children() if p.is_alive()
                ]
                if remaining:
                    raise AssertionError(f"spawn workers remain: {remaining}")

            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    completed = subprocess.run(
        [sys.executable, str(driver), str(output)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert output.read_text(encoding="utf-8") == "score output written\n"


def test_replacing_idle_score_worker_reaps_old_spawn_before_exit(
    tmp_path: Path,
) -> None:
    driver = tmp_path / "score_worker_replacement.py"
    output = tmp_path / "replacement-output.txt"
    driver.write_text(
        textwrap.dedent(
            """
            import multiprocessing
            import os
            import sys
            from pathlib import Path

            from simulator.battery.residue import _evaluate_openimcc_isolated

            def first_engine(**_kwargs):
                return "first evaluator"

            def second_engine(**_kwargs):
                return "second evaluator"

            def main():
                assert _evaluate_openimcc_isolated(first_engine, timeout_s=5) == "first evaluator"
                previous = multiprocessing.active_children()[0]
                previous_pid = previous.pid
                assert _evaluate_openimcc_isolated(second_engine, timeout_s=5) == "second evaluator"

                try:
                    os.kill(previous_pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError(f"replaced idle worker remains: {previous_pid}")
                Path(sys.argv[1]).write_text("replacement output written\\n")
                remaining = [p.pid for p in multiprocessing.active_children() if p.is_alive()]
                current_pid = remaining[0] if len(remaining) == 1 else None
                if current_pid is None or current_pid == previous_pid:
                    raise AssertionError(f"unexpected live workers: {remaining}")

            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    completed = subprocess.run(
        [sys.executable, str(driver), str(output)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert output.read_text(encoding="utf-8") == "replacement output written\n"


def test_sigterm_ignoring_score_worker_is_killed_and_reaped(tmp_path: Path) -> None:
    driver = tmp_path / "score_worker_sigkill.py"
    output = tmp_path / "sigkill-output.txt"
    driver.write_text(
        textwrap.dedent(
            """
            import multiprocessing
            import signal
            import sys
            from pathlib import Path

            def ignore_sigterm(ready, never_release):
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                ready.set()
                never_release.wait()

            def main():
                context = multiprocessing.get_context("spawn")
                ready = context.Event()
                never_release = context.Event()
                process = context.Process(
                    target=ignore_sigterm,
                    args=(ready, never_release),
                    daemon=True,
                )
                process.start()
                assert ready.wait(10), "worker did not install SIGTERM handler"
                from simulator.battery.residue import _shutdown_openimcc_worker

                class Connection:
                    def send(self, _message):
                        pass

                    def close(self):
                        pass

                _shutdown_openimcc_worker(process, Connection())
                assert process.exitcode == -signal.SIGKILL, process.exitcode
                assert not process.is_alive()
                Path(sys.argv[1]).write_text("SIGKILL worker reaped\\n")
                process.close()

            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    process = subprocess.Popen(
        [sys.executable, str(driver), str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        # A fresh subprocess imports the residue/scoring package before the
        # worker starts; keep that bounded without mistaking cold startup for
        # a shutdown hang.
        stdout, stderr = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        # Without the SIGKILL escalation, multiprocessing's interpreter
        # shutdown waits forever for this daemon. Kill the test process group
        # so the mutation test itself cannot leave a child behind.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise

    assert process.returncode == 0, stdout + stderr
    assert output.read_text(encoding="utf-8") == "SIGKILL worker reaped\n"
