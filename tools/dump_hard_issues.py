#!/usr/bin/env python3
"""Write every migration hard issue as a TSV census."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.identity import quantity_token
from simulator.battery.migrate import migrate


_OBSERVATION_PATH = re.compile(r"^observation\[(.+)\](?:\.(.*))?$")
_FIELDS = (
    "source_id",
    "observation_id",
    "field",
    "issue_code",
    "message",
    "evidence_class",
    "method_class",
    "quantity",
)


def dump_hard_issues(root: Path, out: Path, source: str | None = None) -> int:
    result = migrate(root, write=False)
    hard_issues = result.validation.hard_issues if result.validation is not None else ()
    observations = result.observations
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_FIELDS, delimiter="\t")
        writer.writeheader()
        written = 0
        for issue in hard_issues:
            match = _OBSERVATION_PATH.match(issue.path)
            observation_id = match.group(1) if match else ""
            field = match.group(2) if match and match.group(2) else ""
            observation = observations.get(observation_id)
            source_id = observation.source_id if observation is not None else ""
            if source is not None and source_id != source:
                continue
            identity = observation.identity if observation is not None else None
            evidence = observation.evidence if observation is not None else None
            writer.writerow(
                {
                    "source_id": source_id or "",
                    "observation_id": observation_id,
                    "field": field,
                    "issue_code": issue.reason.value,
                    "message": issue.detail,
                    "evidence_class": (
                        evidence.class_.value.value
                        if evidence is not None and evidence.class_.is_value
                        else ""
                    ),
                    "method_class": evidence.original_method_class or "" if evidence else "",
                    "quantity": (
                        quantity_token(identity).value
                        if identity is not None and quantity_token(identity) is not None
                        else ""
                    ),
                }
            )
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--out", type=Path, required=True, help="TSV output path")
    parser.add_argument("--source", help="include issues for this source_id only")
    args = parser.parse_args(argv)
    print(f"hard_issues_written={dump_hard_issues(args.root, args.out, args.source)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
