"""Rebuild the reviewed top-40 score comparison against a corpus snapshot."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tools import mineru_table_qa as qa


_MANGLED_TABLES = {
    ("kems-028-yakovlev-1984", "2", "1"),
    ("holzheid-1997-feo-nio-coo-activity-metal-saturated", "7", "6"),
    # Ueshima Table 2 is the p006 table; p008/table 3 is unrelated statistics.
    ("kems-200-ueshima-1983", "6", "2"),
    ("kems-057-kambayashi-1985", "3", "2"),
}
_MANGLED_FLAGS = {
    "kems-028-yakovlev-1984": "letter_for_digit",
    "holzheid-1997-feo-nio-coo-activity-metal-saturated": "ragged_rows",
    "kems-200-ueshima-1983": "run_together_columns",
    "kems-057-kambayashi-1985": "fused_reference_decimal",
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
    calibrated = []
    for row in original:
        key = (row["sid"], row["chunk"], row["table_index"])
        updated = current_tables.get(key)
        if updated is None:
            raise ValueError(f"missing calibrated table: {key}")
        calibrated.append((row, updated))
    ranked = sorted(
        calibrated,
        key=lambda pair: (
            -float(pair[1]["combined_score"]),
            pair[0]["sid"],
            int(pair[1]["absolute_page"]),
            int(pair[0]["table_index"]),
            pair[0]["chunk"],
        ),
    )
    ranks = {id(row): rank for rank, (row, _) in enumerate(ranked, start=1)}
    out_of_top_eight = []
    for row, updated in calibrated:
        truth_key = (row["sid"], row["absolute_page"], row["table_index"])
        if truth_key in _MANGLED_TABLES:
            rank = ranks[id(row)]
            required_flag = _MANGLED_FLAGS[row["sid"]]
            flags = {part.split("=", 1)[0] for part in updated["flags"].split(";") if part}
            print(
                f"MANGLED rank {rank}: {row['sid']}/{updated['absolute_page']}/"
                f"{row['table_index']} flags={updated['flags']}"
            )
            if required_flag not in flags:
                out_of_top_eight.append((rank, row["sid"], f"missing {required_flag}"))
            if rank > 8:
                out_of_top_eight.append((rank, row["sid"], row["table_index"]))

    with args.output_tsv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("table_id", "verdict", "old_score", "new_score", "new_rank"))
        for row, updated in calibrated:
            truth_key = (row["sid"], row["absolute_page"], row["table_index"])
            table_id = "/".join((row["sid"], str(updated["absolute_page"]), row["table_index"]))
            writer.writerow((
                table_id,
                "MANGLED" if truth_key in _MANGLED_TABLES else "OK",
                row["combined_score"],
                updated["combined_score"],
                ranks[id(row)],
            ))
    if out_of_top_eight:
        raise SystemExit(f"MANGLED tables outside top 8: {out_of_top_eight}")


if __name__ == "__main__":
    main()
