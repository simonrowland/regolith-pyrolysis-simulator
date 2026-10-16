"""Render-checked row count and valence anchors for Sutton et al. (2005)."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sutton-2005-vanadium-xanes.yaml"


def test_sutton_table_valences_match_render_and_printed_row_counts():
    extract = yaml.safe_load(EXTRACT.read_text())
    observations = extract["species"]["V"]["observations"]
    schreiber = observations[0]["values"]["points"]
    synthetic = observations[1]["values"]["points"]
    natural = observations[2]["values"]["points"]

    assert len(schreiber) + len(synthetic) == 49  # Table 2, page 2339, PDF page 7
    assert len(natural) == 10  # Table 3 individual samples, page 2344, PDF page 12
    assert (schreiber[0]["sample"], schreiber[0]["value"], schreiber[0]["uncertainty"]) == (
        "X116",
        4.87,
        0.15,
    )
    assert (synthetic[4]["sample"], synthetic[4]["value"], synthetic[4]["uncertainty"]) == (
        "93-14-15 (1596B)",
        4.13,
        0.07,
    )
    assert (natural[0]["sample"], natural[0]["value"], natural[0]["uncertainty"]) == (
        "Green (15318, 15425, 15426)",
        2.87,
        0.07,
    )
    assert (natural[-1]["sample"], natural[-1]["value"], natural[-1]["uncertainty"]) == (
        "Cerro Negro inclusions",
        4.06,
        0.07,
    )
