from __future__ import annotations

from pathlib import Path

from tests.battery.engine_cache_score_equivalence import (
    assert_residue_score_cache_equivalence,
)


def test_scored_hashimoto_residues_match_off_cold_warm_and_parallel_cache(
    tmp_path: Path,
) -> None:
    assert_residue_score_cache_equivalence(
        tmp_path,
        source="kems-015-hashimoto-1983",
        experiment_ids=(
            "10.2343/geochemj.17.111::experiment::hashimoto-1983-run-20b2",
        ),
    )
