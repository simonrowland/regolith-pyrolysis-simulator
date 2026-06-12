from __future__ import annotations

import pytest

from scripts import epoch_grind
from simulator.optimize.pool import PoolEvaluationRequest
from simulator.optimize.recipe import RecipePatch


@pytest.mark.skip(
    reason=(
        "needs-interface: no input-data-only test seam routes PoolEvaluationRequest "
        "through epoch journal/adaptive-stop wiring"
    )
)
def test_epoch_dup_rate_adaptive_stop_green_path_accepts_pool_batch_plateau() -> None:
    batch = (
        PoolEvaluationRequest(
            RecipePatch({("campaigns", "C0b_p_cleanup", "pO2_mbar_default"): 9.0}),
            "lunar_mare_low_ti",
            "fast",
            candidate_id="plateau-a",
        ),
        PoolEvaluationRequest(
            RecipePatch({("campaigns", "C0b_p_cleanup", "pO2_mbar_default"): 9.1}),
            "lunar_mare_low_ti",
            "fast",
            candidate_id="plateau-b",
        ),
    )
    merge_summaries = (
        {
            "inserted_rows": 99,
            "sources": [
                {
                    "source": "epoch-001.sqlite",
                    "source_rows": 1100,
                    "seed_rows": 1000,
                    "inserted_rows": 99,
                }
            ],
        },
        {
            "inserted_rows": 197,
            "sources": [
                {
                    "source": "epoch-002.sqlite",
                    "source_rows": 1200,
                    "seed_rows": 1000,
                    "inserted_rows": 197,
                }
            ],
        },
    )

    dup_rates = [
        epoch_grind.duplication_rate_from_merge(summary)
        for summary in merge_summaries
    ]
    decision = epoch_grind.adaptive_decision(
        dup_rates,
        remaining_jobs=len(batch),
        threshold=0.02,
        consecutive=2,
    )

    assert [request.candidate_id for request in batch] == ["plateau-a", "plateau-b"]
    assert dup_rates == pytest.approx([0.01, 0.015])
    assert decision == epoch_grind.DECISION_FINAL_LONG
