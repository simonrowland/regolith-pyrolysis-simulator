#!/usr/bin/env python3
"""Seed/check SHADOW sections from stored runner bytes; never execute physics."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.golden_sections import load_json, partition, read_sections, reassemble, write_sections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="directory for generated scenario sections")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for source in sorted((ROOT / "tests/fixtures/runner").glob("*.json")):
        raw = source.read_bytes()
        files = partition(load_json(raw))
        assert reassemble(files) == raw, source
        destination = args.destination / source.stem
        if args.check:
            assert read_sections(destination) == files, source
        else:
            write_sections(destination, files)
        print(f"{source.stem}: {len(files) - 1} value sections; exact bytes")


if __name__ == "__main__":
    main()
