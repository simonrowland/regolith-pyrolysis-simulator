from __future__ import annotations

import json

import pytest

from scripts import epoch_grind
from simulator.optimize.evaluate import _is_stale_profile_refusal
from simulator.optimize import profiles as profile_rules
from simulator.optimize.profiles import ProfileValidationError


@pytest.mark.parametrize(
    ("reason", "message", "expected"),
    [
        ("ProfileValidationError", "ordinary validation failure", False),
        ("ValueError", "out-of-policy gate; FORCE_PROFILES=1", False),
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
def test_stale_profile_verdicts_match(
    tmp_path, reason: str, message: str, expected: bool
) -> None:
    status = tmp_path / "job_status.json"
    status.write_text(
        json.dumps({"reason": reason, "message": message}),
        encoding="utf-8",
    )
    evaluator_verdict = (
        _is_stale_profile_refusal(ProfileValidationError(message))
        if reason == "ProfileValidationError"
        else False
    )
    epoch_verdict = epoch_grind._stale_profile_status(tmp_path) is not None

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


def test_mutating_owner_criterion_breaks_both_caller_pins(
    monkeypatch, tmp_path
) -> None:
    status = tmp_path / "job_status.json"
    status.write_text(
        '{"reason":"ProfileValidationError","message":"out-of-policy gate; FORCE_PROFILES=1"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile_rules,
        "classify_profile_validation",
        lambda reason, message: profile_rules.ProfileValidationDecision.VALIDATION_ERROR,
    )

    with pytest.raises(AssertionError):
        assert _is_stale_profile_refusal(
            ProfileValidationError("out-of-policy gate; FORCE_PROFILES=1")
        ) is True
    with pytest.raises(AssertionError):
        assert epoch_grind._stale_profile_status(tmp_path) is not None
