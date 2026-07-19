"""End-to-end regression coverage for MRE electrolysis refusal semantics."""

from copy import deepcopy

from simulator.config import load_config_bundle
from simulator.electrolysis import MRE_MULTI_OXIDE_PARTITION_REFUSAL
from simulator.run_executor import RunExecutor
from simulator.runner import DATA_DIR
from simulator.session import SimSessionConfig


def test_mre_baseline_multi_oxide_partition_is_typed_refusal_not_poisoned_hour():
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
        hours=130,
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
    assert execution.simulator.melt.hour == 43
    assert len(execution.snapshots) == 43
    execution.simulator.atom_ledger.assert_balanced()

    refusal = execution.refusal_diagnostic
    assert refusal["hour"] == 43
    assert refusal["campaign"] == "MRE_BASELINE"
    diagnostic = refusal["diagnostic"]
    assert diagnostic["reason_refused"] == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert diagnostic["current_partition_certified"] is False
    assert diagnostic["certification_allowed"] is False
    assert diagnostic["reducible_oxide_targets"] == ("Cr2O3", "SiO2")
    assert diagnostic["mre_effective_voltage_margin_V_by_oxide"]["Cr2O3"] > 0.0
    assert diagnostic["mre_effective_voltage_margin_V_by_oxide"]["SiO2"] > 0.0
