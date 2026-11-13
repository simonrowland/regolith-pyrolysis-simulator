"""Drop the printed overhead pressure and prove the flux-contract test goes red.

Run from the seat with PYTHONPATH set:
``python tests/battery/mutation_w1103sem_1598b_wall_flux_contract.py``.
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

import simulator.battery.score as score


_NEEDLE = "            overhead_pressure_pa=float(pressure),\n"
_TEST = (
    "tests/battery/test_wall_deposit_areal_rate_and_gas_volume.py::"
    "test_areal_rate_calls_the_existing_wall_flux"
)


def main() -> None:
    original = score._wall_areal_rate_from_existing_flux
    source = textwrap.dedent(inspect.getsource(original))
    if source.count(_NEEDLE) != 1:
        raise SystemExit("overhead_pressure_pa argument was not found once")
    mutated = source.replace(_NEEDLE, "")
    namespace: dict[str, object] = {}
    exec(
        compile(mutated, "<w1103sem-1598b-wall-flux-contract-mutation>", "exec"),
        score.__dict__,
        namespace,
    )
    score._wall_areal_rate_from_existing_flux = namespace[
        "_wall_areal_rate_from_existing_flux"
    ]
    try:
        result = pytest.main(
            ["-q", "-n", "0", "-p", "no:cacheprovider", "--timeout=300", _TEST]
        )
    finally:
        score._wall_areal_rate_from_existing_flux = original
    print("w1103sem-1598b wall-flux contract mutation exit", result)
    if result != pytest.ExitCode.TESTS_FAILED:
        raise SystemExit(f"mutation did not fail {_TEST}; pytest exit {result}")


if __name__ == "__main__":
    main()
