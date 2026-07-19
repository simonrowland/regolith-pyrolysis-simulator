"""End-to-end regression coverage for MRE electrolysis refusal semantics."""

from copy import deepcopy

import pytest

from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import (
    MRECurrentPartitionRefusal,
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
)
from simulator.run_executor import RunExecutor
from simulator.runner import DATA_DIR
from simulator.session import SimSessionConfig


def test_mre_baseline_multi_oxide_partition_is_typed_refusal_not_poisoned_hour(
    monkeypatch: pytest.MonkeyPatch,
):
    material_state_before_refusal: dict[str, float] = {}
    refusal_record = {
        "hour": 0,
        "campaign": "MRE_BASELINE",
        "diagnostic": {
            "reason_refused": MRE_MULTI_OXIDE_PARTITION_REFUSAL,
            "current_partition_certified": False,
            "certification_allowed": False,
            "reducible_oxide_targets": ("Cr2O3", "SiO2"),
            "mre_effective_voltage_margin_V_by_oxide": {
                "Cr2O3": 0.1,
                "SiO2": 0.2,
            },
        },
    }

    def _raise_partition_refusal(self: PyrolysisSimulator):
        # Inject at the hourly transaction seam. Provider-level tests exercise
        # the real multi-oxide detector; this end-to-end test owns typed
        # refusal propagation and rollback without a mutable 130-hour recipe.
        material_state_before_refusal.update(self.melt.composition_kg)
        first_oxide = next(iter(self.melt.composition_kg))
        self.melt.composition_kg[first_oxide] += 1.0
        self.melt.hour = 99
        raise MRECurrentPartitionRefusal(
            MRE_MULTI_OXIDE_PARTITION_REFUSAL,
            refusal_record,
        )

    monkeypatch.setattr(
        PyrolysisSimulator,
        "_step_one_hour",
        _raise_partition_refusal,
    )
    bundle = load_config_bundle(DATA_DIR)
    setpoints = deepcopy(bundle.setpoints)
    setpoints.setdefault("chemistry_kernel", {})[
        "allow_unmeasured_alpha_fallback"
    ] = True
    config = SimSessionConfig(
        feedstock_id="lunar_mare_low_ti",
        feedstocks=bundle.feedstocks,
        setpoints=setpoints,
        vapor_pressures=bundle.vapor_pressures,
        materials=bundle.materials,
        mass_kg=1000.0,
        hours=1,
        track="mre_baseline",
        c5_enabled=True,
        mre_target_species="Si",
        mre_max_voltage_V=1.6,
    )

    execution = RunExecutor().execute(config)

    assert execution.status == "refused"
    assert execution.reason == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert execution.simulator._poisoned_hour is None
    assert execution.error_message == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert execution.simulator.melt.hour == 0
    assert len(execution.snapshots) == 0
    assert execution.simulator.melt.composition_kg == material_state_before_refusal
    execution.simulator.atom_ledger.assert_balanced()

    refusal = execution.refusal_diagnostic
    assert refusal["hour"] == 0
    assert refusal["campaign"] == "MRE_BASELINE"
    diagnostic = refusal["diagnostic"]
    assert diagnostic["reason_refused"] == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert diagnostic["current_partition_certified"] is False
    assert diagnostic["certification_allowed"] is False
    assert diagnostic["reducible_oxide_targets"] == ("Cr2O3", "SiO2")
    assert diagnostic["mre_effective_voltage_margin_V_by_oxide"]["Cr2O3"] > 0.0
    assert diagnostic["mre_effective_voltage_margin_V_by_oxide"]["SiO2"] > 0.0
