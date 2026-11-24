"""Golden pins for phase policy tokens shared by migration and scoring."""

from simulator.battery import score


def test_two_phase_bulk_policy_tokens_keep_the_existing_contract() -> None:
    assert (
        score.TWO_PHASE_BULK_COMPOSITION_STATUS
        == "two_phase_bulk_composition_not_liquid_composition"
    )
    assert (
        score.TWO_PHASE_BULK_COMPOSITION_PHASE_MARKER
        == "bulk_composition_in_two_phase_region"
    )
