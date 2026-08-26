"""End-to-end regression coverage for MRE electrolysis refusal semantics."""

from copy import deepcopy
from types import SimpleNamespace

import math

import pytest

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.electrolysis import (
    ElectrolysisModel,
    MRECurrentPartitionRefusal,
    MREElectrolysisRefusal,
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
    MRE_RAW_MARGIN_REFUSAL,
    mre_selectivity_weight,
)
from simulator.run_executor import RunExecutor
from simulator.runner import DATA_DIR
from simulator.session import SimSessionConfig
from simulator.state import MeltState


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


def _legacy_model() -> ElectrolysisModel:
    return ElectrolysisModel()


def test_nernst_voltage_valid_anchors_unchanged():
    """Bucket A guards must not move valid Nernst results."""

    model = _legacy_model()
    feo = model.nernst_voltage("FeO", 1600.0, 1.0)
    assert feo == pytest.approx(0.8043402178324541)
    assert model.nernst_voltage("FeO", 1600.0, 0.0) == pytest.approx(
        6.379435018821934
    )
    mg_unit = model.nernst_voltage(
        "MgO", 1600.0, 1.0, metal_fugacity_bar=1.0
    )
    mg_dilute = model.nernst_voltage(
        "MgO", 1600.0, 1.0, metal_fugacity_bar=0.01
    )
    assert mg_dilute - mg_unit == pytest.approx(-0.37167298673263205)
    al_unit = model.nernst_voltage("Al2O3", 1600.0, 1.0)
    al_dilute = model.nernst_voltage("Al2O3", 1600.0, 0.01)
    assert al_dilute - al_unit == pytest.approx(0.2477819911550878)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"oxide": "FeO", "T_C": 1600.0, "activity": math.nan},
        {"oxide": "FeO", "T_C": 1600.0, "activity": math.inf},
        {"oxide": "FeO", "T_C": 1600.0, "activity": -math.inf},
        {"oxide": "FeO", "T_C": 1600.0, "activity": -1.0},
        {"oxide": "FeO", "T_C": math.nan, "activity": 1.0},
        {"oxide": "FeO", "T_C": math.inf, "activity": 1.0},
        {"oxide": "FeO", "T_C": -273.15, "activity": 1.0},
        {"oxide": "FeO", "T_C": True, "activity": 1.0},
        {"oxide": "UnobtainiumO", "T_C": 1600.0, "activity": 1.0},
    ],
)
def test_nernst_voltage_refuses_degenerate_controls(kwargs):
    with pytest.raises(ValueError):
        _legacy_model().nernst_voltage(**kwargs)


def test_nernst_voltage_degenerate_is_not_typed_physics_refusal():
    with pytest.raises(ValueError) as info:
        _legacy_model().nernst_voltage("FeO", 1600.0, activity=math.nan)
    assert not isinstance(info.value, MREElectrolysisRefusal)


def test_step_hour_valid_feo_still_reduces():
    result = _legacy_model().step_hour(
        MeltState(composition_kg={"FeO": 1.0}),
        voltage_V=3.0,
        current_A=100.0,
        T_C=1600.0,
    )
    assert result.get("reason_refused") is None
    assert result["oxides_reduced_kg"]["FeO"] == pytest.approx(
        0.07753359699551476
    )
    assert result["energy_kWh"] == pytest.approx(0.3)


def test_step_hour_zero_current_and_voltage_remain_idle():
    model = _legacy_model()
    melt = MeltState(composition_kg={"FeO": 1.0})
    zero_i = model.step_hour(melt, voltage_V=3.0, current_A=0.0, T_C=1600.0)
    zero_v = model.step_hour(melt, voltage_V=0.0, current_A=100.0, T_C=1600.0)
    assert zero_i["oxides_reduced_kg"] == {}
    assert zero_i["energy_kWh"] == 0.0
    assert zero_v["oxides_reduced_kg"] == {}
    assert zero_v["energy_kWh"] == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"voltage_V": math.nan, "current_A": 100.0, "T_C": 1600.0},
        {"voltage_V": math.inf, "current_A": 100.0, "T_C": 1600.0},
        {"voltage_V": 3.0, "current_A": -100.0, "T_C": 1600.0},
        {"voltage_V": 3.0, "current_A": math.nan, "T_C": 1600.0},
        {"voltage_V": 3.0, "current_A": 100.0, "T_C": math.nan},
        {"voltage_V": 3.0, "current_A": 100.0, "T_C": -273.15},
        {"voltage_V": True, "current_A": 100.0, "T_C": 1600.0},
    ],
)
def test_step_hour_refuses_degenerate_controls(kwargs):
    with pytest.raises(ValueError):
        _legacy_model().step_hour(
            MeltState(composition_kg={"FeO": 1.0}),
            **kwargs,
        )


def test_step_hour_existing_pO2_and_fugacity_errors_stay_value_or_type():
    model = _legacy_model()
    melt = MeltState(composition_kg={"FeO": 1.0})
    with pytest.raises(ValueError, match="pO2_bar"):
        model.step_hour(
            melt,
            voltage_V=3.0,
            current_A=100.0,
            T_C=1600.0,
            pO2_bar=0.0,
        )
    with pytest.raises(TypeError, match="mapping"):
        model.step_hour(
            melt,
            voltage_V=3.0,
            current_A=100.0,
            T_C=1600.0,
            gas_product_fugacity_bar=["not", "a", "map"],
        )


def test_selectivity_weight_refuses_degenerate_and_keeps_overvoltage_direction():
    assert mre_selectivity_weight(1.0, 0.1, 1873.15) == pytest.approx(
        1.8580342735957387
    )
    assert mre_selectivity_weight(1.0, 0.5, 1873.15) == pytest.approx(
        20.085536923187668
    )
    with pytest.raises(ValueError):
        mre_selectivity_weight(math.nan, 0.1, 1873.15)
    with pytest.raises(ValueError):
        mre_selectivity_weight(1.0, 0.1, math.nan)
    with pytest.raises(ValueError):
        mre_selectivity_weight(-1.0, 0.1, 1873.15)


def test_estimate_total_energy_valid_anchor_and_degenerate_refusal():
    model = _legacy_model()
    feo = MeltState(composition_kg={"FeO": 1.0})
    dilute = MeltState(composition_kg={"FeO": 1.0, "SiO2": 99.0})
    assert model.estimate_total_energy_kWh(feo, 0.85, T_C=1600.0) == (
        pytest.approx(1.2343067798436018)
    )
    assert model.estimate_total_energy_kWh(dilute, 0.85, T_C=1600.0) == (
        pytest.approx(1.2343067798436018)
    )
    with pytest.raises(ValueError):
        model.estimate_total_energy_kWh(feo, math.nan, T_C=1600.0)
    with pytest.raises(ValueError):
        model.estimate_total_energy_kWh(feo, 0.85, T_C=math.nan)
    with pytest.raises(ValueError):
        model.estimate_total_energy_kWh(feo, math.inf, T_C=1600.0)


def test_multi_oxide_step_still_refuses_while_sequence_ranks():
    model = _legacy_model()
    melt = MeltState(composition_kg={"FeO": 1.0, "SiO2": 1.0})
    sequence = model.get_reduction_sequence(melt, T_C=1600.0, pO2_bar=1.0)
    assert [oxide for oxide, _voltage in sequence] == ["FeO", "SiO2"]
    result = model.step_hour(
        melt,
        voltage_V=3.0,
        current_A=100.0,
        T_C=1600.0,
        pO2_bar=1.0,
    )
    assert result["reason_refused"] == MRE_MULTI_OXIDE_PARTITION_REFUSAL
    assert result["oxides_reduced_kg"] == {}
    assert result["energy_kWh"] == pytest.approx(0.3)
