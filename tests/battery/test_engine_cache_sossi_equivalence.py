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
    )
