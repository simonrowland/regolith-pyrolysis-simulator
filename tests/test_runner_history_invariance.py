"""Runner output must not depend on scenarios executed earlier in process."""

from tests.test_runner_smoke import (
    SCENARIOS,
    _assert_authoritative_flux_capture,
    _run_scenario,
)


def test_lunar_flux_and_ledger_ignore_an_interleaved_scenario():
    lunar = SCENARIOS[0]
    interleaved = SCENARIOS[1:]

    first_payload, first = _run_scenario(lunar, capture=True)
    _assert_authoritative_flux_capture(first_payload, first)
    for scenario in interleaved:
        payload, capture = _run_scenario(scenario, capture=True)
        _assert_authoritative_flux_capture(payload, capture)
    repeat_payload, repeated = _run_scenario(lunar, capture=True)
    _assert_authoritative_flux_capture(repeat_payload, repeated)

    assert repeated["flux_maps"] == first["flux_maps"]
    assert repeated["ledger_output"] == first["ledger_output"]
