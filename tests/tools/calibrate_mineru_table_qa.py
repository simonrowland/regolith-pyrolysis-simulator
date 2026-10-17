"""Rebuild the reviewed top-40 score comparison against a corpus snapshot."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tools import mineru_table_qa as qa


_MANGLED_TABLES = {
    ("kems-028-yakovlev-1984", "2", "1"),
    ("holzheid-1997-feo-nio-coo-activity-metal-saturated", "7", "6"),
    ("kems-200-ueshima-1983", "8", "3"),
    ("kems-057-kambayashi-1985", "3", "2"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_tsv", type=Path)
    parser.add_argument("corpus_root", type=Path)
    parser.add_argument("output_tsv", type=Path)
    args = parser.parse_args()

    lines = args.baseline_tsv.read_text(encoding="utf-8").splitlines()
    table_header = next(index for index, line in enumerate(lines) if line.startswith("sid\tchunk\t"))
    original = list(csv.DictReader(lines[table_header:table_header + 41], delimiter="\t"))
    current, _ = qa.rank_corpus(args.corpus_root, sorted({row["sid"] for row in original}))
    current_tables = {
        (row["sid"], row["chunk"], str(row["table_index"])): row
        for row in current
    }
    with args.output_tsv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("table_id", "verdict", "old_score", "new_score"))
        for row in original:
            truth_key = (row["sid"], row["absolute_page"], row["table_index"])
            lookup_key = (row["sid"], row["chunk"], row["table_index"])
            updated = current_tables.get(lookup_key)
            table_id = "/".join((row["sid"], str(updated["absolute_page"]) if updated else row["absolute_page"], row["table_index"]))
            writer.writerow((table_id, "MANGLED" if truth_key in _MANGLED_TABLES else "OK",
                             row["combined_score"], updated["combined_score"] if updated else "missing"))


if __name__ == "__main__":
    main()
