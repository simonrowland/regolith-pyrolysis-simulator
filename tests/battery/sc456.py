from __future__ import annotations


_PENDING_EQUATION_DETAILS = {
    "raw_inputs declaration is invalid for model_derived without a located not_printed declaration, method, and named references",
    "model_derived raw_inputs method requires a nonempty value and an equation and physical page locator",
    "derived observation requires derived_from",
}


def assert_only_pending_equation_issues(validation, *, observation_id_fragment: str) -> None:
    assert validation is not None
    assert all(
        observation_id_fragment in issue.path and issue.detail in _PENDING_EQUATION_DETAILS
        for issue in validation.hard_issues
    ), validation.hard_issues
