"""Golden parser-helper outputs for real USGS compilation inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from simulator.battery.generators import usgs_b1259, usgs_b1452, usgs_b1544


GENERATORS = (
    ("b1259", usgs_b1259),
    ("b1452", usgs_b1452),
    ("b1544", usgs_b1544),
)

EXPECTED_PARSER_OUTPUT_SHA256 = {
    "b1259": "45e7170e8fdc1513b0077f7a554ab653bb7cac51b4db15f8eaeb86bbe09d1dc7",
    "b1452": "8c35e41327b92341afe69acbb7332b4370b3bd38fba45ce05066c53cd1a23d51",
    "b1544": "c446507f621c4018bd470d558bf79fcb5a0a736283dc093932bc7a67d6af1b9b",
}


def _record_outputs(name: str, generator, record: dict) -> dict:
    tokens = generator._iter_raw_numeric_tokens(record)
    if name == "b1544":
        grains = generator._column_grain_map(record)
        parsed = []
        for token in tokens:
            value = generator._published_decimal(token.as_published)
            parsed.append(
                (
                    token.path,
                    value,
                    generator._value_grain(value) if value is not None else None,
                    generator._letter_tail_grain_reason(token, grains),
                    generator._usable_reconstruction(token, grains),
                )
            )
        series = generator._signed_series(record)
        neighbours = generator._neighbour_sign_hits(record, series)
        return {"parsed": parsed, "neighbours": neighbours}

    grains = generator._column_grain_map(tokens)
    candidates = []
    merged_records = []
    for token in tokens:
        candidate = generator._candidate_value(token, grains)
        candidates.append((token.path, candidate))
        reconstruction = candidate[1]
        if (
            reconstruction
            and reconstruction.get("method_class") == generator.MERGED_SPLIT_METHOD
        ):
            merged_records.append(
                (
                    token.path,
                    generator._merged_split_record(
                        reconstruction["value"],
                        reconstruction["uncertainty"],
                        evidence=reconstruction["evidence"],
                    ),
                )
            )
    series = generator._signed_series(tokens)
    return {
        "candidates": candidates,
        "merged_records": merged_records,
        "neighbours": generator._neighbour_sign_hits(tokens, series),
    }


def _parser_output_digest(name: str, generator) -> str:
    digest = hashlib.sha256()
    for path in sorted(generator.RECORDS_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = _record_outputs(name, generator, record)
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        digest.update(path.name.encode("utf-8") + b"\0" + encoded + b"\n")
    if name != "b1544":
        weights, names = generator._table2_elements()
        table = (sorted(weights.items()), sorted(names.items()))
        digest.update(
            b"table2\0"
            + json.dumps(
                table, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        )
    return digest.hexdigest()


def test_real_input_parser_outputs_match_pins() -> None:
    for name, generator in GENERATORS:
        digest = _parser_output_digest(name, generator)
        print(f"USGS_PARSER_OUTPUT_DIGEST {name} {digest}")
        assert digest == EXPECTED_PARSER_OUTPUT_SHA256[name], name
