from __future__ import annotations

from pathlib import Path

from tests.battery.engine_cache_score_equivalence import (
    assert_residue_score_cache_equivalence,
)


def test_scored_sossi_residues_match_off_cold_warm_and_parallel_cache(
    tmp_path: Path,
) -> None:
    assert_residue_score_cache_equivalence(
        tmp_path,
        source="kems-012-sossi-2019",
        # Short 900 s runs at 1573.15 K and 1673.15 K. Each has supported Mn/Ti
        # cells and admitted Gd/La/Sc/V/Yb/Zr cells refused by the scorer.
        test_experiment_ids=(
            "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16c",
            "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps2",
        ),
        required_refusal_reason="outside_supported_species",
    )
