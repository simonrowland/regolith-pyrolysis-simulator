"""Runner output must not depend on scenarios executed earlier in process."""

from tests.test_runner_smoke import SCENARIOS, _run_scenario


def test_lunar_flux_and_ledger_ignore_an_interleaved_scenario():
    lunar = SCENARIOS[0]
    interleaved = tuple(
        {**scenario, "hours": 1} for scenario in SCENARIOS[1:]
    )

    _first_payload, first = _run_scenario(lunar, capture=True)
    for scenario in interleaved:
        _run_scenario(scenario, capture=True)
    _repeat_payload, repeated = _run_scenario(lunar, capture=True)

    assert repeated["flux_maps"] == first["flux_maps"]
    assert repeated["ledger_output"] == first["ledger_output"]
