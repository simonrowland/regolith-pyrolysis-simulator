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

    with args.baseline_tsv.open(encoding="utf-8", newline="") as stream:
        original = list(csv.DictReader(stream, delimiter="\t"))
    if not original or not {"table_id", "verdict", "old_score"}.issubset(original[0]):
        raise ValueError("baseline must be the committed table_id/verdict/old_score TSV")
    identity = {
        row["table_id"]: tuple(row["table_id"].rsplit("/", 2))
        for row in original
    }
    current, _ = qa.rank_corpus(
        args.corpus_root, sorted({parts[0] for parts in identity.values()})
    )
    current_tables = {
        (row["sid"], str(row["absolute_page"]), str(row["table_index"])): row
        for row in current
    }
    calibrated = []
    for row in original:
        key = identity[row["table_id"]]
        updated = current_tables.get(key)
        if updated is None:
            raise ValueError(f"missing calibrated table: {key}")
        calibrated.append((row, updated))
    ranked = sorted(
        calibrated,
        key=lambda pair: (
            -float(pair[1]["combined_score"]),
            identity[pair[0]["table_id"]][0],
            int(pair[1]["absolute_page"]),
            int(identity[pair[0]["table_id"]][2]),
            pair[1]["chunk"],
        ),
    )
    ranks = {row["table_id"]: rank for rank, (row, _) in enumerate(ranked, start=1)}
    out_of_top_eight = []
    for row, updated in calibrated:
        sid, page, table_index = identity[row["table_id"]]
        truth_key = (sid, page, table_index)
        if truth_key in _MANGLED_TABLES:
            rank = ranks[row["table_id"]]
            required_flag = _MANGLED_FLAGS[sid]
            flags = {part.split("=", 1)[0] for part in updated["flags"].split(";") if part}
            print(
                f"MANGLED rank {rank}: {sid}/{updated['absolute_page']}/"
                f"{table_index} flags={updated['flags']}"
            )
            if required_flag not in flags:
                out_of_top_eight.append((rank, sid, f"missing {required_flag}"))
            if rank > 8:
                out_of_top_eight.append((rank, sid, table_index))

    with args.output_tsv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("table_id", "verdict", "old_score", "new_score", "new_rank"))
        for row, updated in calibrated:
            table_id = row["table_id"]
            writer.writerow((
                table_id,
                row["verdict"],
                row["old_score"],
                updated["combined_score"],
                ranks[table_id],
            ))
    if out_of_top_eight:
        raise SystemExit(f"MANGLED tables outside top 8: {out_of_top_eight}")


if __name__ == "__main__":
    main()
