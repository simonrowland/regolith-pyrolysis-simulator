"""Pin O'Neill (1988) equation 11 coefficients to page 477."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ammin-73-470-oneill-1988.yaml"

EQUATION_11_SEGMENTS = (
    (
        (833.0, 1042.0),
        "-605812 + 1366.718 T - 182.7955 T ln(T) + 0.10359 T^2",
    ),
    (
        (1042.0, 1184.0),
        "-519357 + 59.427 T + 8.9276 T ln(T)",
    ),
    (
        (1184.0, 1450.0),
        "-551159 + 269.404 T - 16.9484 T ln(T)",
    ),
)


def test_fe_feo_piecewise_relation_matches_printed_equation_11():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == "oneill_1988_fe_feo_measured_relation"
    )
    actual = tuple(
        (
            tuple(segment["T_range_K"]),
            segment["relation"].split("=", maxsplit=1)[1].strip(),
        )
        for segment in observation["values"]["piecewise_relation"]
    )

    assert actual == EQUATION_11_SEGMENTS
