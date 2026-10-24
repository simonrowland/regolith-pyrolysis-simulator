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
