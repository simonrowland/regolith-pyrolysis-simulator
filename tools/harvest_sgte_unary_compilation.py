#!/usr/bin/env python3
"""Emit one native record per (element, phase) from the cached SGTE TDB."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from simulator.chemistry.sgte_unary import (  # noqa: E402
    COMPILATION_ROOT,
    build_manifest,
    build_records,
    ensure_source_tdb,
    load_tdb,
    record_to_yaml,
)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=COMPILATION_ROOT)
    return parser.parse_args()


def main() -> int:
    args = cli()
    output_root: Path = args.output_root
    source_tdb = ensure_source_tdb()
    database = load_tdb(source_tdb)
    records = build_records(database)
    records_dir = output_root / "records"
    if records_dir.exists():
        for stale in records_dir.glob("*.yaml"):
            stale.unlink()
    else:
        records_dir.mkdir(parents=True, exist_ok=True)

    for record in records:
        path = records_dir / f"{record.record_id}.yaml"
        path.write_text(record_to_yaml(record), encoding="utf-8")

    # Wall-clock stamps are omitted from committed compilation sidecars so
    # regenerate is byte-stable; pass generated_at only from an explicit caller.
    manifest = build_manifest(database, records, generated_at=None)
    (output_root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    print(
        f"records={len(records)} functions={len(database.functions)} "
        f"ambiguities={manifest['summary']['parse_ambiguity_count']} "
        f"sha256={database.source_sha256} dest={output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
