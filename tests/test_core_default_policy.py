from __future__ import annotations

from types import SimpleNamespace

import pytest

import simulator.policy.default_policy as default_policy
from simulator.core import PyrolysisSimulator


def test_core_dp007_runtime_uses_policy_registry(monkeypatch) -> None:
    class PolicyOwnerReached(Exception):
        pass

    def resolve(policy_id: str, **context: object):
        assert policy_id == "DP-007"
        assert context == {"sample_mass_kg": 0.01}
        raise PolicyOwnerReached

    monkeypatch.setattr(default_policy, "resolve", resolve)
    simulator = object.__new__(PyrolysisSimulator)
    simulator._equipment = SimpleNamespace(
        crucible=SimpleNamespace(melt_surface_area_m2=0.0001),
        pipe=SimpleNamespace(
            initial_throat_area_m2=0.0,
            stage_area_ratios=None,
            length_m=0.0,
        ),
    )
    simulator.melt = SimpleNamespace(melt_surface_area_m2=0.0)
    simulator.setpoints = {
        "lab_geometry": {
            "scale": "gram_lab",
            "sample": {"exposed_melt_area_m2": None},
        }
    }
    simulator.lab_geometry = SimpleNamespace(sample_mass_g=10.0)
    simulator._dp007_area_default = None
    simulator._overhead_condenser_geometry_config = {}
    simulator._overhead_model = SimpleNamespace(
        configure_condenser_geometry=lambda _config: None,
        pipe_length_m=0.0,
    )

    with pytest.raises(PolicyOwnerReached):
        simulator._apply_equipment_runtime_geometry()
