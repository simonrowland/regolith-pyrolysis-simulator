"""Mutation check: removing d-144 identities breaks the Kume score tests."""

from __future__ import annotations

import pytest

from simulator.battery.generators.janaf import JANAF_SINGLE_POLYMORPH_IDENTITY_D144


def main() -> int:
    JANAF_SINGLE_POLYMORPH_IDENTITY_D144.pop("CaO")
    JANAF_SINGLE_POLYMORPH_IDENTITY_D144.pop("MgO")
    result = pytest.main(
        [
            "tests/battery/test_score.py",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            "-k",
            "kume_real_migrated_activity_scores_with_d144_identity",
        ]
    )
    if result == pytest.ExitCode.OK:
        raise SystemExit("mutation survived: removing d-144 identities stayed green")
    print("expected red: removing CaO/MgO d-144 identities fails Kume score tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
