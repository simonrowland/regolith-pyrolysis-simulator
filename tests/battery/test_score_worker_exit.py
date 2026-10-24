from __future__ import annotations

import os
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
