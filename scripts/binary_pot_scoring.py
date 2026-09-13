#!/usr/bin/env python3
"""Run the binary-pot scoring arm: measured KEMS activities vs each engine.

Pots are the source-tagged scoring_pots block in data/binary_pots.yaml.
Every battery engine is called at the authors' printed temperatures.
Typed refusals are rows. model_derived / quoted extract rows are not scored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.diagnostic_helpers.binary_pot_battery import (  # noqa: E402
    BATTERY_ENGINE_NAMES,
    probe_battery_engines,
)
from simulator.diagnostic_helpers.binary_pot_scoring import (  # noqa: E402
    DEFAULT_POTS_PATH,
    REPORT_DIR,
    run_scoring_arm,
    write_scoring_reports,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORT_DIR,
        help="directory for binary-pot-scoring-arm.json and .md (default: %(default)s)",
    )
    parser.add_argument(
        "--pots",
        type=Path,
        default=DEFAULT_POTS_PATH,
        help="binary pot catalog (default: %(default)s)",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="resolve engines, print availability, and exit without equilibrate()",
    )
    parser.add_argument(
        "--progress-log",
        type=Path,
        default=None,
        help="append one line per cell (and START/DONE) for detached polling",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.probe_only:
        handles = probe_battery_engines(BATTERY_ENGINE_NAMES)
        payload = {
            name: {
                "available": handle.available,
                "unavailable_reason": handle.unavailable_reason,
                "takes_fo2": handle.takes_fo2,
                "identity": dict(handle.identity),
            }
            for name, handle in handles.items()
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    report = run_scoring_arm(
        pots_path=args.pots, progress_log=args.progress_log
    )
    json_path, markdown_path = write_scoring_reports(report, args.output_dir)
    print(f"hostname={report['hostname']}")
    print(f"n_cells={report['n_cells']}")
    print(f"n_ok={report['n_ok']}")
    print(f"n_refused={report['n_refused']}")
    print(f"n_envelope_rows={report['n_envelope_rows']}")
    print(f"n_score_eligible={report['n_score_eligible']}")
    print(f"scored_rows_per_engine={report['scored_rows_per_engine']}")
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
