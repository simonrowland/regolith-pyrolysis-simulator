from __future__ import annotations

import pytest

from scripts import epoch_grind
from simulator.optimize.evaluate import _is_stale_profile_refusal
from simulator.optimize.profiles import ProfileValidationError


@pytest.mark.parametrize(
    ("reason", "message", "expected"),
    [
        ("ProfileValidationError", "ordinary validation failure", False),
        (
            "ProfileValidationError",
            "out-of-policy gate; FORCE_PROFILES=1",
            True,
        ),
        (
            "ProfileValidationError",
            "FORCE_PROFILES=1 without gate detail",
            False,
        ),
        (
            "ProfileValidationError",
            "out-of-policy gate without regeneration instruction",
            False,
        ),
        ("ProfileValidationError", "", False),
    ],
)
def test_stale_profile_verdicts_match_before_owner_move(
    reason: str, message: str, expected: bool
) -> None:
    evaluator_verdict = reason == "ProfileValidationError" and _is_stale_profile_refusal(
        ProfileValidationError(message)
    )
    epoch_verdict = reason == "ProfileValidationError" and epoch_grind._message_reports_stale_profile(
        message
    )

    assert evaluator_verdict is expected
    assert epoch_verdict is expected
    assert evaluator_verdict == epoch_verdict


def test_epoch_stale_profile_requires_reason_field(tmp_path) -> None:
    status = tmp_path / "job_status.json"
    status.write_text(
        '{"message":"out-of-policy gate; FORCE_PROFILES=1"}',
        encoding="utf-8",
    )

    assert epoch_grind._stale_profile_status(tmp_path) is None
