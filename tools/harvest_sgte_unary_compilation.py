#!/usr/bin/env python3
"""Copy unary50.tdb and emit one native record per (element, phase)."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from simulator.chemistry.sgte_unary import (  # noqa: E402
    COMPILATION_ROOT,
    build_manifest,
    build_records,
    load_tdb,
    record_to_yaml,
    sha256_file,
)

DEFAULT_SOURCE = Path("/Users/simonrowland/Repos/regolith-corpus-ctl/raw/sgte-unary-5.0")


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=COMPILATION_ROOT)
    return parser.parse_args()


def main() -> int:
    args = cli()
    source_dir: Path = args.source_dir
    output_root: Path = args.output_root
    source_tdb = source_dir / "unary50.tdb"
    source_sidecar = source_dir / "sidecar.yaml"
    if not source_tdb.is_file():
        raise SystemExit(f"missing source TDB: {source_tdb}")

    dest_source = output_root / "source"
    dest_source.mkdir(parents=True, exist_ok=True)
    dest_tdb = dest_source / "unary50.tdb"
    shutil.copy2(source_tdb, dest_tdb)
    if source_sidecar.is_file():
        shutil.copy2(source_sidecar, dest_source / "sidecar.yaml")

    expected = "8e38dcefbeaad1f8ed83ed1f8ccceb0e1701fb584f1bf3798f217488253d4b3f"
    digest = sha256_file(dest_tdb)
    if digest != expected:
        raise SystemExit(f"unary50.tdb sha256 {digest} != {expected}")

    database = load_tdb(dest_tdb)
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
        f"sha256={digest} dest={output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
