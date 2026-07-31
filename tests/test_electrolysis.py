"""End-to-end regression coverage for MRE electrolysis refusal semantics."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import (
    MRECurrentPartitionRefusal,
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
    MRE_RAW_MARGIN_REFUSAL,
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
        campaign="MRE_BASELINE",
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


def test_sc109_mre_non_partition_refusal_propagates_typed_through_executor(
    monkeypatch: pytest.MonkeyPatch,
):
    """SC-109 shape C: non-partition provider refusals must stay typed.

    Fail-open input: any status='refused' reason other than multi-oxide
    partition previously raised RuntimeError → run_executor status='failed'
    with empty refusal_diagnostic. Must now status='refused' with diagnostic.
    """
    original_dispatch = PyrolysisSimulator._dispatch_only
    material_state_before_refusal: dict[str, float] = {}

    def _dispatch_raw_margin_refusal(
        self: PyrolysisSimulator,
        intent,
        **kwargs,
    ):
        if intent != ChemistryIntent.ELECTROLYSIS_STEP:
            return original_dispatch(self, intent, **kwargs)
        material_state_before_refusal.update(self.melt.composition_kg)
        first_oxide = next(iter(self.melt.composition_kg))
        self.melt.composition_kg[first_oxide] += 1.0
        return SimpleNamespace(
            status="refused",
            transition=None,
            diagnostic={
                "reason_refused": MRE_RAW_MARGIN_REFUSAL,
                "current_partition_certified": False,
            },
        )

    monkeypatch.setattr(
        PyrolysisSimulator,
        "_dispatch_only",
        _dispatch_raw_margin_refusal,
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
        campaign="MRE_BASELINE",
        track="mre_baseline",
        c5_enabled=True,
        mre_target_species="Si",
        mre_max_voltage_V=1.6,
    )

    execution = RunExecutor().execute(config)

    assert execution.status == "refused"
    assert execution.reason == MRE_RAW_MARGIN_REFUSAL
    assert execution.refusal_diagnostic["diagnostic"]["reason_refused"] == (
        MRE_RAW_MARGIN_REFUSAL
    )
    assert execution.simulator._poisoned_hour is None
    assert execution.simulator.melt.hour == 0
    assert len(execution.snapshots) == 0
    assert execution.simulator.melt.composition_kg == material_state_before_refusal
    execution.simulator.atom_ledger.assert_balanced()


def test_sc109_unclassified_mre_provider_refusal_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    original_dispatch = PyrolysisSimulator._dispatch_only

    def _dispatch_unclassified_refusal(
        self: PyrolysisSimulator,
        intent,
        **kwargs,
    ):
        if intent != ChemistryIntent.ELECTROLYSIS_STEP:
            return original_dispatch(self, intent, **kwargs)
        return SimpleNamespace(
            status="refused",
            transition=None,
            diagnostic={"reason_refused": "provider_contract_bug"},
        )

    monkeypatch.setattr(
        PyrolysisSimulator,
        "_dispatch_only",
        _dispatch_unclassified_refusal,
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
        campaign="MRE_BASELINE",
        track="mre_baseline",
        c5_enabled=True,
        mre_target_species="Si",
        mre_max_voltage_V=1.6,
    )

    execution = RunExecutor().execute(config)

    assert execution.status == "failed"
    assert "unclassified ELECTROLYSIS_STEP refusal" in execution.error_message
    assert execution.refusal_diagnostic == {}
