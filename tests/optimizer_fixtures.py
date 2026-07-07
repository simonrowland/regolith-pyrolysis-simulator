from __future__ import annotations

from typing import Any

from simulator.optimize.physics import FeasibilityResult, GateMargin, ThresholdSpec


class StubSmokeConstraintSet:
    """Legacy smoke gate retained only for tests that inject constraints directly."""

    def evaluate(self, trace: Any) -> FeasibilityResult:
        threshold = ThresholdSpec(
            id="stub_smoke_feasible",
            value=1.0,
            units="boolean",
            source="engineering_envelope",
            source_ref="test-only legacy smoke constraint",
        )
        return FeasibilityResult(
            feasible=True,
            margins={
                "stub_smoke": GateMargin(
                    gate="stub_smoke",
                    feasible=True,
                    margin=1.0,
                    threshold=threshold,
                    observed=1.0,
                    detail="test-only legacy smoke gate",
                )
            },
        )
