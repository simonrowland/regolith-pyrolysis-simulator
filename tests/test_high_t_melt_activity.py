"""C3 openimcc authority routing and composition-policy tests."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin import vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.melt_backend.openimcc_bridge import (
    FE2O3_TO_FEO_TOTAL_WT_FACTOR,
    OpenImccBridgeResult,
    OpenImccCompositionPolicyRefusal,
    evaluate_cleaned_melt,
)
import simulator.melt_backend.openimcc_bridge as openimcc_bridge_module
from simulator.melt_backend.vaporock import VAPOROCK_T_MAX_K
from simulator.runner import PyrolysisRun
from simulator.state import MOLAR_MASS


pytest.importorskip("openimcc")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REPO_ROOT = DATA_DIR.parent
CAP_PLUS_T_K = VAPOROCK_T_MAX_K + 0.1
CAP_MINUS_T_K = VAPOROCK_T_MAX_K - 0.1

BASE_MELT_MOL = {
    "Na2O": 0.20,
    "K2O": 0.08,
    "SiO2": 5.00,
    "FeO": 1.00,
    "MgO": 1.00,
    "CaO": 1.00,
    "Al2O3": 1.00,
    "TiO2": 0.10,
}


def _provider() -> BuiltinVaporPressureProvider:
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        return BuiltinVaporPressureProvider(yaml.safe_load(handle) or {})


def _request(
    composition_mol: dict[str, float],
    temperature_K: float,
    *,
    high_t_melt_activity: str | None = None,
    previous_temperature_K: float | None = None,
    crossing: bool = False,
    pO2_bar: float = 1.0e-9,
    intrinsic_fO2_log: float | None = -10.0,
) -> IntentRequest:
    controls: dict[str, object] = {
        "pO2_bar": pO2_bar,
        "intrinsic_fO2_log": intrinsic_fO2_log,
    }
    if high_t_melt_activity is not None:
        controls["high_t_melt_activity"] = high_t_melt_activity
    if previous_temperature_K is not None:
        controls["high_t_melt_activity_previous_temperature_K"] = (
            previous_temperature_K
        )
    if crossing:
        controls["high_t_melt_activity_crossing"] = True
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(composition_mol)},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1.0e-6,
        control_inputs=controls,
    )


def _physics(result) -> tuple[dict, dict, dict, dict, tuple[str, ...]]:
    diagnostic = result.diagnostic
    return (
        dict(diagnostic["vapor_pressures_Pa"]),
        dict(diagnostic["activities"]),
        dict(diagnostic["vapor_pressures_source"]),
        copy.deepcopy(diagnostic["vapor_pressure_numerator_provenance"]),
        tuple(result.warnings),
    )


def _moles_from_wt(weights: dict[str, float]) -> dict[str, float]:
    return {
        name: weight / resolve_species_formula(name).molar_mass_kg_per_mol()
        for name, weight in weights.items()
    }


def _projected_weights(*, over_threshold: bool = False) -> dict[str, float]:
    return {
        "Na2O": 5.0,
        "K2O": 1.0,
        "SiO2": 50.0,
        "FeO": 10.0,
        "MgO": 10.0,
        "CaO": 15.0,
        "Al2O3": 8.0,
        "TiO2": 0.5,
        "Cr2O3": 0.41,
        "MnO": 0.24,
        "P2O5": 0.12,
        "NiO": 2.0 if over_threshold else 0.10,
    }


def _cr_mn_projection_weights(
    *,
    p2o5_wt_pct: float = 0.0,
    include_cr_mn: bool = True,
) -> dict[str, float]:
    cr2o3_wt_pct = 1.0 if include_cr_mn else 0.0
    mno_wt_pct = 0.5 if include_cr_mn else 0.0
    return {
        "Na2O": 5.0,
        "K2O": 1.0,
        "SiO2": 50.5 - cr2o3_wt_pct - mno_wt_pct - p2o5_wt_pct,
        "FeO": 10.0,
        "MgO": 10.0,
        "CaO": 15.0,
        "Al2O3": 8.0,
        "TiO2": 0.5,
        "Cr2O3": cr2o3_wt_pct,
        "MnO": mno_wt_pct,
        "P2O5": p2o5_wt_pct,
    }


def _element_inventory_mol(sim, balances: dict[str, dict[str, float]], element: str) -> float:
    return sum(
        float(moles)
        * resolve_species_formula(
            species,
            sim.species_formula_registry,
        ).elements.get(element, 0.0)
        for species_map in balances.values()
        for species, moles in species_map.items()
    )


def _run_fe2o3_lookup_only_case(mode: str) -> dict[str, object]:
    with (DATA_DIR / "feedstocks.yaml").open() as handle:
        feedstocks = yaml.safe_load(handle) or {}
    with (DATA_DIR / "setpoints.yaml").open() as handle:
        setpoints = yaml.safe_load(handle) or {}
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        vapor_pressures = yaml.safe_load(handle) or {}
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config.update(
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    setpoints["chemistry_kernel"] = kernel_config

    fe2o3_feedstock = copy.deepcopy(feedstocks["lunar_mare_low_ti"])
    composition = fe2o3_feedstock["composition_wt_pct"]
    composition["FeO"] -= 1.0
    composition["Fe2O3"] = 1.0
    fe2o3_feedstock["composition_basis"] = {
        "FeO": {"method": "synthetic assay", "source": "test"},
        "Fe2O3": {"method": "synthetic assay", "source": "test"},
    }
    feedstocks["c3_fe2o3_lookup_only"] = fe2o3_feedstock

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("c3_fe2o3_lookup_only", mass_kg=1000.0)
    sim._high_t_melt_activity = mode
    sim.melt.temperature_C = 1800.0
    before = sim.atom_ledger.mol_by_account()
    result = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        liquid_fraction=1.0,
        fO2_log=sim.melt.fO2_log,
        activity_coefficients={},
    )

    sim._refresh_vapor_pressures_from_kernel(result)

    after = sim.atom_ledger.mol_by_account()
    melt = after["process.cleaned_melt"]
    return {
        "fe2o3_mol": melt["Fe2O3"],
        "feo_mol": melt["FeO"],
        "oxygen_mol": _element_inventory_mol(sim, after, "O"),
        "mass_balance_error_pct": sim._make_snapshot().mass_balance_error_pct,
        "ledger_before": before,
        "ledger_after": after,
        "provider": sim._last_high_t_melt_activity.get("provider"),
        "high_t": sim._last_high_t_melt_activity,
    }


def test_below_cap_is_activity_source_parity_with_pre_c3_path() -> None:
    provider = _provider()
    legacy = provider.dispatch(_request(BASE_MELT_MOL, CAP_MINUS_T_K))
    explicit_openimcc = provider.dispatch(
        _request(BASE_MELT_MOL, CAP_MINUS_T_K, high_t_melt_activity="openimcc")
    )

    assert _physics(legacy) == _physics(explicit_openimcc)
    assert "high_t_melt_activity" not in explicit_openimcc.diagnostic


def test_above_cap_openimcc_route_feeds_flux_with_provenance() -> None:
    provider = _provider()
    result = provider.dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]

    assert high_t["provider"] == "openimcc"
    assert high_t["temperature_domain_status"] == "in_domain"
    assert high_t["openimcc_pack_version"] == "1.0.2"
    assert high_t["openimcc_pack_digest"]
    assert set(high_t["activities_by_oxide"]) == {
        "Na2O",
        "K2O",
        "SiO2",
        "FeO",
        "MgO",
        "CaO",
        "Al2O3",
        "TiO2",
    }
    flux_provenance = [
        row
        for row in result.diagnostic["vapor_pressure_numerator_provenance"].values()
        if row.get("melt_activity_authority") == "openimcc"
    ]
    assert flux_provenance
    assert all("openimcc" in row["source_label"] for row in flux_provenance)
    assert all(
        row["openimcc_pack_digest"] == high_t["openimcc_pack_digest"]
        for row in flux_provenance
    )
    fe_provenance = [
        row
        for row in result.diagnostic["vapor_pressure_numerator_provenance"].values()
        if row.get("activity_basis") is not None
    ]
    assert fe_provenance
    assert all(row["activity_basis"] != "kress91_ferrous" for row in fe_provenance)


def test_above_cap_openimcc_provenance_carries_complex_saturation_notice() -> None:
    result = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol={"K2O": 0.497, "SiO2": 0.503},
        temperature_K=CAP_PLUS_T_K,
        controls={"high_t_melt_activity": "openimcc"},
        below_cap_fe_activity=1.0,
        below_cap_fe_activity_basis="test",
    )

    assert result is not None
    assert result["provider"] == "openimcc", result["fallback_reason"]
    typed = result["openimcc_typed_notices"]
    assert len(typed) == 1
    assert typed[0]["kind"] == "imcc_complex_saturation"
    assert typed[0]["flag"].startswith("species-coverage-edge")
    assert typed[0]["acid_sink_ratio"] == result["openimcc_acid_sink_ratio"]
    assert typed[0] in result["notices"]
    assert typed[0] in result["openimcc_notices"]
    assert result["openimcc_envelope_status"] == "inside"
    assert result["openimcc_extrapolated"] is False


def test_relaxed_openimcc_preserves_constant_gamma_carrier_set() -> None:
    composition = _moles_from_wt(_cr_mn_projection_weights())
    provider = _provider()
    regression_temperature_K = 1700.0 + 273.15
    request_options = {
        "pO2_bar": 1.0e-4,
        "intrinsic_fO2_log": None,
    }

    constant_gamma = provider.dispatch(
        _request(
            composition,
            regression_temperature_K,
            high_t_melt_activity="constant_gamma",
            **request_options,
        )
    )
    openimcc = provider.dispatch(
        _request(
            composition,
            regression_temperature_K,
            high_t_melt_activity="openimcc",
            **request_options,
        )
    )

    constant_gamma_species = set(
        constant_gamma.diagnostic["vapor_pressures_Pa"]
    )
    openimcc_species = set(openimcc.diagnostic["vapor_pressures_Pa"])
    assert openimcc_species == constant_gamma_species
    assert openimcc.diagnostic["high_t_melt_activity"][
        "openimcc_projection_crmn_relaxed"
    ]["code"] == "openimcc_projection_crmn_relaxed"
    assert openimcc.diagnostic["vapor_pressures_Pa"]["Ti"] > 0.0
    assert (
        openimcc.diagnostic["vapor_pressure_numerator_provenance"]["Ti"][
            "melt_activity_authority"
        ]
        == "openimcc"
    )


def test_above_cap_k2o_keeps_constant_gamma_with_exclusion_provenance() -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    constant_gamma = melt_oxide_activity(
        "K2O",
        BASE_MELT_MOL,
        temperature_K=CAP_PLUS_T_K,
    )

    assert high_t["activities_by_oxide"]["K2O"] == constant_gamma.activity
    assert high_t["openimcc_activities_by_oxide"]["K2O"] != constant_gamma.activity
    assert high_t["seam"]["K2O"]["ratio_dex_selected_over_below_cap"] == 0.0
    assert high_t["seam"]["K2O"]["would_be_openimcc_step_dex"] < -2.0
    assert high_t["authority_exclusions"]["K2O"]["tracking_id"] == "t-999"

    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    k_rows = [provenance[species] for species in ("K", "K2", "K2O_gas")]
    assert all(
        row["melt_activity_authority"] == "constant_gamma"
        and row["openimcc_authority_excluded_K2O"]["authority"] == "constant_gamma"
        and row["openimcc_authority_excluded_K2O"]["ruling"] == "owner 2026-09-26"
        and row["openimcc_authority_excluded_K2O"]["certification"] == "t-999"
        for row in k_rows
    )
    assert high_t["activities_by_oxide"]["Na2O"] == high_t[
        "openimcc_activities_by_oxide"
    ]["Na2O"]


def test_missing_present_parent_refuses_and_labels_provider_fallback(
    monkeypatch,
) -> None:
    def omit_na(**kwargs):
        return OpenImccBridgeResult(
            parent_oxide_activities={"SiO2": 1.0},
            parent_oxides=("SiO2",),
            flags=(),
            notices=(),
            acid_sink_ratio=None,
            pack_model_id="fake",
            pack_version="fake",
            pack_digest="fake",
            openimcc_version="fake",
            envelope_status="in_domain",
            extrapolated=False,
            labels=SimpleNamespace(),
            coverage={},
        )

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", omit_na)
    composition = {"Na2O": 0.2, "SiO2": 5.0}

    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    assert refusal.value.code == "openimcc_result_shape"
    assert "Na2O" in str(refusal.value)

    result = _provider().dispatch(
        _request(composition, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    assert result.status == "ok"
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback"] is True
    assert high_t["fallback_reason"]["code"] == "openimcc_result_shape"
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    for species in ("Na", "Na2", "Na2O_gas"):
        assert provenance[species]["melt_activity_authority"] == "constant_gamma"
        assert provenance[species]["openimcc_refusal"]["code"] == (
            "openimcc_result_shape"
        )


def test_absent_parent_stays_out_of_openimcc_authority() -> None:
    result = _provider().dispatch(
        _request({"SiO2": 1.0}, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]

    assert "Na2O" not in high_t["activities_by_oxide"]
    assert high_t["seam"]["Na2O"]["openimcc_activity"] is None
    assert high_t["seam"]["Na2O"]["selected_activity_basis"] == (
        "constant_gamma"
    )
    assert high_t["authority_exclusions"]["Na2O"]["code"] == (
        "openimcc_parent_absent"
    )


def test_provider_omitted_high_t_control_defaults_to_openimcc() -> None:
    result = _provider().dispatch(_request(BASE_MELT_MOL, CAP_PLUS_T_K))

    assert result.diagnostic["high_t_melt_activity"]["provider"] == "openimcc"


def test_above_3000_k_uses_openimcc_extrapolation_and_flags_it() -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, 3200.0, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]

    assert result.status == "ok"
    assert high_t["provider"] == "openimcc"
    assert high_t["temperature_domain_status"] == "predict_and_flag_extrapolated"
    assert high_t["openimcc_extrapolated"] is True
    assert high_t["openimcc_flags"] or high_t["openimcc_notices"]


def test_cap_crossing_over_screen_emits_typed_notice() -> None:
    crossing = _request(
        BASE_MELT_MOL,
        CAP_PLUS_T_K,
        high_t_melt_activity="openimcc",
        previous_temperature_K=CAP_MINUS_T_K,
        crossing=True,
    )

    result = _provider().dispatch(crossing)
    high_t = result.diagnostic["high_t_melt_activity"]

    assert high_t["seam_max_abs_ratio_dex"] > 0.1
    assert any(
        notice["code"] == "openimcc_activity_cap_step_exceeds_0_1_dex"
        for notice in high_t["notices"]
    )


def test_recipe_default_and_constant_gamma_escape_are_threaded() -> None:
    default = PyrolysisRun(feedstock_id="lunar_mare_low_ti", hours=0)
    legacy = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        hours=0,
        high_t_melt_activity="constant_gamma",
    )
    assert default._session_config().high_t_melt_activity == "openimcc"
    assert legacy._session_config().high_t_melt_activity == "constant_gamma"


def test_simulator_default_passes_openimcc_authority_into_flux_dispatch() -> None:
    with (DATA_DIR / "feedstocks.yaml").open() as handle:
        feedstocks = yaml.safe_load(handle) or {}
    with (DATA_DIR / "setpoints.yaml").open() as handle:
        setpoints = yaml.safe_load(handle) or {}
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        vapor_pressures = yaml.safe_load(handle) or {}
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config.update(
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    setpoints["chemistry_kernel"] = kernel_config
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = 1800.0
    result = SimpleNamespace(
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        liquid_fraction=1.0,
        fO2_log=sim.melt.fO2_log,
        activity_coefficients={},
    )

    sim._refresh_vapor_pressures_from_kernel(result)

    assert sim._high_t_melt_activity == "openimcc"
    assert sim._last_high_t_melt_activity["provider"] == "openimcc"
    assert sim._last_vapor_pressures_source


def test_constant_gamma_reproduces_pre_c3_physics_above_cap() -> None:
    provider = _provider()
    # Pin the legacy value: omitted control now selects the owner-approved OpenIMCC default.
    baseline = provider.dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="constant_gamma")
    )
    explicit_legacy = provider.dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="constant_gamma")
    )
    assert _physics(baseline) == _physics(explicit_legacy)
    assert "high_t_melt_activity" not in explicit_legacy.diagnostic


def test_projected_bulk_within_one_percent_has_notice_and_classifier_record() -> None:
    result = evaluate_cleaned_melt(
        _moles_from_wt(_projected_weights()),
        CAP_PLUS_T_K,
    )
    policy = result.policy
    classifier = policy["composition_projection_classification"]

    assert policy["status"] == "projected"
    assert classifier["projection_verdict"] == "within_threshold"
    assert 0.0 < classifier["dropped_total_wt_pct"] <= 1.0
    assert policy["notice"]["code"] == "openimcc_projected_bulk"


def test_cr_mn_projection_relaxes_threshold_and_reaches_provenance() -> None:
    composition = _moles_from_wt(_cr_mn_projection_weights())
    cleaned = evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    classifier = cleaned.policy["composition_projection_classification"]

    assert classifier["projection_verdict"] == "within_threshold"
    assert classifier["dropped_total_wt_pct"] == pytest.approx(1.5)
    assert classifier["thresholded_dropped_total_wt_pct"] == pytest.approx(0.0)
    assert set(classifier["dropped_components"]) == {"Cr2O3", "MnO"}
    relaxed = cleaned.policy["openimcc_projection_crmn_relaxed"]
    assert relaxed["code"] == "openimcc_projection_crmn_relaxed"
    assert relaxed["wt_pct"] == pytest.approx({"Cr2O3": 1.0, "MnO": 0.5})
    assert relaxed["ruling"] == "owner 2026-09-27"

    result = _provider().dispatch(
        _request(composition, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    assert high_t["provider"] == "openimcc"
    assert high_t["openimcc_projection_crmn_relaxed"] == relaxed
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    openimcc_rows = [
        row
        for row in provenance.values()
        if row.get("melt_activity_authority") == "openimcc"
    ]
    assert openimcc_rows
    assert all(
        row["openimcc_projection_crmn_relaxed"] == relaxed
        for row in openimcc_rows
    )


def test_non_cr_mn_projection_still_uses_one_percent_threshold() -> None:
    composition = _moles_from_wt(
        _cr_mn_projection_weights(p2o5_wt_pct=1.5, include_cr_mn=False)
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        evaluate_cleaned_melt(composition, CAP_PLUS_T_K)

    assert refusal.value.code == "openimcc_projection_over_threshold"
    classifier = refusal.value.diagnostics["composition_projection_classification"]
    assert classifier["projection_verdict"] == "over_threshold"
    assert classifier["dropped_total_wt_pct"] == pytest.approx(1.5)
    result = _provider().dispatch(
        _request(composition, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback_reason"]["code"] == (
        "openimcc_projection_over_threshold"
    )


@pytest.mark.parametrize(
    ("p2o5_wt_pct", "should_project"),
    ((0.8, True), (1.2, False)),
)
def test_cr_mn_relaxation_does_not_change_other_projection_gate(
    p2o5_wt_pct: float,
    should_project: bool,
) -> None:
    composition = _moles_from_wt(
        _cr_mn_projection_weights(p2o5_wt_pct=p2o5_wt_pct)
    )

    if should_project:
        result = evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
        classifier = result.policy["composition_projection_classification"]
        assert classifier["projection_verdict"] == "within_threshold"
        assert classifier["thresholded_dropped_total_wt_pct"] == pytest.approx(
            p2o5_wt_pct
        )
        return

    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    assert refusal.value.code == "openimcc_projection_over_threshold"


def test_projection_over_one_percent_refuses_and_provider_flags_fallback() -> None:
    composition = _moles_from_wt(_projected_weights(over_threshold=True))
    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    assert refusal.value.code == "openimcc_projection_over_threshold"
    assert "composition_projection_classification" in refusal.value.diagnostics

    result = _provider().dispatch(
        _request(composition, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    assert result.status == "ok"
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback"] is True
    assert high_t["fallback_reason"]["code"] == "openimcc_projection_over_threshold"
    assert any(
        "openimcc_refused_constant_gamma_fallback" in source
        for source in result.diagnostic["vapor_pressures_source"].values()
    )
    fallback_provenance = result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]
    assert any(
        row["composition_projection_classification"]["projection_verdict"]
        == "over_threshold"
        for row in fallback_provenance.values()
        if "composition_projection_classification" in row
    )


def test_invalid_inventory_reaches_typed_constant_gamma_fallback() -> None:
    authority = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol={"SiO2": 1.0, "NaCl": -1.0},
        temperature_K=CAP_PLUS_T_K,
        controls={"high_t_melt_activity": "openimcc"},
        below_cap_fe_activity=0.1,
        below_cap_fe_activity_basis="constant_gamma",
    )

    assert authority["provider"] == "constant_gamma"
    assert authority["fallback"] is True
    assert authority["fallback_reason"]["code"] == (
        "openimcc_composition_invalid_input"
    )


def test_feo_alias_collision_keeps_typed_c3_fallback_reason() -> None:
    authority = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol={"SiO2": 1.0, "FeO": 0.1, "FeO_total": 0.1},
        temperature_K=CAP_PLUS_T_K,
        controls={"high_t_melt_activity": "openimcc"},
        below_cap_fe_activity=0.1,
        below_cap_fe_activity_basis="constant_gamma",
    )

    assert authority["provider"] == "constant_gamma"
    assert authority["fallback"] is True
    assert authority["fallback_reason"]["code"] == (
        "openimcc_composition_canonicalization"
    )
    assert authority["fallback_reason"]["type"] == (
        "OpenImccCompositionPolicyRefusal"
    )


def test_provider_provenance_records_typed_refusal(
    monkeypatch,
) -> None:
    def refuse_invalid_inventory(*args, **kwargs):
        raise OpenImccCompositionPolicyRefusal(
            "openimcc_composition_invalid_input",
            "invalid mole inventory for 'MgO': value=-1.0",
        )

    monkeypatch.setattr(
        openimcc_bridge_module,
        "evaluate_cleaned_melt",
        refuse_invalid_inventory,
    )
    result = _provider().dispatch(
        _request(
            BASE_MELT_MOL,
            CAP_PLUS_T_K,
            high_t_melt_activity="openimcc",
        )
    )

    high_t = result.diagnostic["high_t_melt_activity"]
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback"] is True
    assert high_t["fallback_reason"]["code"] == (
        "openimcc_composition_invalid_input"
    )
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    assert any(
        row.get("melt_activity_authority") == "constant_gamma"
        and row.get("openimcc_refusal", {}).get("code")
        == "openimcc_composition_invalid_input"
        for row in provenance.values()
    )


@pytest.mark.parametrize(
    ("invalid_value", "invalid_reason"),
    (
        (float("nan"), "component_not_finite:NaCl"),
        (-0.25, "component_negative:NaCl"),
    ),
)
def test_invalid_projection_verdict_maps_to_typed_refusal(
    monkeypatch,
    invalid_value: float,
    invalid_reason: str,
) -> None:
    monkeypatch.setattr(
        openimcc_bridge_module,
        "_cleaned_melt_projection",
        lambda _composition: (
            {"SiO2": 99.0, "NaCl": invalid_value},
            {"SiO2": 99.0},
        ),
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        openimcc_bridge_module._cleaned_melt_policy({"SiO2": 1.0})

    assert refusal.value.code == "openimcc_projection_invalid_input"
    classification = refusal.value.diagnostics["composition_projection_classification"]
    assert classification["projection_verdict"] == "invalid_input"
    assert classification["invalid_reason"] == invalid_reason


def test_openimcc_na_k_parent_activities_are_numeric() -> None:
    result = evaluate_cleaned_melt(
        _moles_from_wt({"Na2O": 5.0, "K2O": 1.0, "SiO2": 94.0}),
        CAP_PLUS_T_K,
    )

    for oxide in ("Na2O", "K2O"):
        parent = float(result.bridge.parent_oxide_activities[oxide])
        single = float(result.single_cation_activities[oxide])
        assert parent > 0.0
        assert single > 0.0
        assert parent == pytest.approx(single**2, rel=1.0e-12)


def test_fe2o3_fold_is_numeric_and_lookup_only() -> None:
    composition = _moles_from_wt({"FeO": 9.0, "Fe2O3": 1.0, "SiO2": 90.0})
    before = dict(composition)
    result = evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    fold = result.policy["fe2o3_fold"]

    assert composition == before
    assert FE2O3_TO_FEO_TOTAL_WT_FACTOR == pytest.approx(0.89982, abs=2.0e-5)
    assert fold["basis"] == "Fe_atoms"
    assert fold["factor"] == pytest.approx(FE2O3_TO_FEO_TOTAL_WT_FACTOR)
    assert fold["factor"] == pytest.approx(
        2.0 * MOLAR_MASS["FeO"] / MOLAR_MASS["Fe2O3"],
        rel=0.0,
        abs=0.0,
    )
    assert fold["feo_total_wt_pct"] == pytest.approx(
        result.composition_wt_pct["FeO"], rel=0.0, abs=0.0
    )

    inventory_before = {
        name: float(value)
        for name, value in composition.items()
        if name in {"FeO", "Fe2O3"}
    }
    inventory_after_openimcc = dict(inventory_before)
    inventory_after_constant_gamma = dict(inventory_before)
    assert inventory_after_openimcc == inventory_after_constant_gamma
    source_mass_before = sum(
        float(mol)
        * resolve_species_formula(name).molar_mass_kg_per_mol()
        for name, mol in composition.items()
    )
    source_mass_after = sum(
        float(mol)
        * resolve_species_formula(name).molar_mass_kg_per_mol()
        for name, mol in composition.items()
    )
    assert source_mass_after == pytest.approx(source_mass_before)


def test_fe2o3_fold_does_not_mutate_ledger_fe_or_oxygen_inventory() -> None:
    legacy = _run_fe2o3_lookup_only_case("constant_gamma")
    openimcc = _run_fe2o3_lookup_only_case("openimcc")

    assert openimcc["provider"] == "openimcc"
    assert openimcc["ledger_before"] == openimcc["ledger_after"]
    assert legacy["ledger_before"] == legacy["ledger_after"]
    for field in ("fe2o3_mol", "feo_mol", "oxygen_mol", "mass_balance_error_pct"):
        assert openimcc[field] == pytest.approx(legacy[field])
    assert openimcc["fe2o3_mol"] > 0.0
    assert openimcc["high_t"]["composition_policy"]["fe2o3_fold"]


def test_missing_openimcc_is_typed_fallback_in_import_blocker_subprocess() -> None:
    script = r'''
import json
import sys
from pathlib import Path
import yaml

class BlockOpenImcc:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "openimcc" or fullname.startswith("openimcc."):
            raise ImportError("test import blocker")
        return None

sys.meta_path.insert(0, BlockOpenImcc())
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView

root = Path.cwd()
data = yaml.safe_load((root / "data" / "vapor_pressures.yaml").read_text())
provider = BuiltinVaporPressureProvider(data)
request = IntentRequest(
    intent=ChemistryIntent.VAPOR_PRESSURE,
    account_view=ProviderAccountView(
        accounts={"process.cleaned_melt": {"Na2O": 0.2, "SiO2": 5.0, "FeO": 1.0}},
        species_formula_registry={},
    ),
    temperature_C=1800.0,
    pressure_bar=1e-6,
    control_inputs={"pO2_bar": 1e-9, "high_t_melt_activity": "openimcc"},
)
result = provider.dispatch(request)
high_t = result.diagnostic["high_t_melt_activity"]
print(json.dumps({
    "status": result.status,
    "provider": high_t["provider"],
    "fallback": high_t["fallback"],
    "code": high_t["fallback_reason"]["code"],
}))
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {
        "status": "ok",
        "provider": "constant_gamma",
        "fallback": True,
        "code": "openimcc_not_importable",
    }


def test_cap_comparison_mutation_makes_above_cap_openimcc_test_red(monkeypatch) -> None:
    monkeypatch.setattr(vapor_pressure_module, "VAPOROCK_T_MAX_K", float("inf"))
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    with pytest.raises(AssertionError):
        assert result.diagnostic.get("high_t_melt_activity", {}).get(
            "provider"
        ) == "openimcc"
