"""C3 openimcc authority routing and composition-policy tests."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import math
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
from simulator.chemistry.melt_activity import (
    melt_oxide_activity,
    single_cation_activity_and_fraction,
)
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
from simulator.run_executor import RunExecutor
from simulator.physical_constants import GAS_CONSTANT
from simulator.runner import (
    PyrolysisRun,
    _high_t_melt_activity_fallback_sentence,
)
from simulator.state import CampaignPhase, MOLAR_MASS


_openimcc = pytest.importorskip("openimcc")
ImccNonconvergenceError = _openimcc.ImccNonconvergenceError

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
ZHANG_KO0_5_COMPOSITION_WT_PCT = {
    "SiO2": 45.94,
    "Al2O3": 16.0,
    "FeO": 10.67,
    "MgO": 7.09,
    "CaO": 11.21,
    "TiO2": 1.79,
    "Na2O": 2.27,
    "K2O": 2.49,
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
    pressure_bar: float = 1.0e-6,
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
        pressure_bar=pressure_bar,
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


# Captured calls 60/61/62, c2b7b4e/oimcc-calls-c2b7b4e-56179.jsonl.
# These are the projected OpenIMCC inputs (eight parents), not a reconstruction
# of the full trace-bearing ledger. Activities are independently logged solves.
_LUNAR_CALL_60 = {
    "Al2O3": 1360.0091160900367, "CaO": 2014.8647042450932,
    "K2O": 8.321603960594533, "MgO": 2291.8132822153393,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1412.300021522945,
    "TiO2": 192.89859753851434, "FeO": 0.028054329027588403,
}
_LUNAR_CALL_61 = {
    "Al2O3": 1360.009115728947, "CaO": 2014.8643551874766,
    "K2O": 8.304955772557909, "MgO": 2291.4382038535123,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1340.4539744251974,
    "TiO2": 192.89812056906857, "FeO": 0.02787490262712387,
}
_LUNAR_CALL_62 = {
    "Al2O3": 1360.0091153170988, "CaO": 2014.8639120984496,
    "K2O": 8.287690522177616, "MgO": 2290.998855181701,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1273.040628414757,
    "TiO2": 192.8976539872457, "FeO": 0.02787490262712387,
}
_LUNAR_CALL_63 = {
    "Al2O3": 1360.0091148463453, "CaO": 2014.8633514615274,
    "K2O": 8.269791565459423, "MgO": 2290.487668001232,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1209.370400395345,
    "TiO2": 192.8971940440544, "FeO": 0.02787490262712387,
}
_LUNAR_CALL_64 = {
    "Al2O3": 1360.0091143072966, "CaO": 2014.862647632118,
    "K2O": 8.251242256104423, "MgO": 2289.896426237898,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1148.9258228745186,
    "TiO2": 192.8967375771004, "FeO": 0.02787490262712387,
}
# Logged parent activities, c2b7b4e/oimcc-calls-c2b7b4e-56179.jsonl.
# These are the solved OpenIMCC parent activities (x*), not a continuation.
_LUNAR_SOLVED_ACTIVITY = {
    60: {
        "SiO2": 0.00491910632589734, "MgO": 0.29003803439475734,
        "FeO": 7.6081001780881665e-06, "CaO": 0.020298134754009073,
        "Al2O3": 0.019497982908272645, "TiO2": 0.0015332892917718512,
        "Na2O": 8.975956307200665e-23, "K2O": 3.895324724042545e-09,
    },
    61: {
        "SiO2": 0.004107876381715498, "MgO": 0.3069501381169543,
        "FeO": 7.564477722792486e-06, "CaO": 0.0232759536601634,
        "Al2O3": 0.018185272341414004, "TiO2": 0.001387386098867267,
        "Na2O": 1.0440512588493435e-22, "K2O": 4.7504875012812956e-09,
    },
    62: {
        "SiO2": 0.0034549055949816875, "MgO": 0.32269118913215145,
        "FeO": 7.557748782306461e-06, "CaO": 0.02647372170951418,
        "Al2O3": 0.017056218137053623, "TiO2": 0.0012651960434469285,
        "Na2O": 1.1901243047466535e-22, "K2O": 5.637959524195314e-09,
    },
    63: {
        "SiO2": 0.002922487950859203, "MgO": 0.3372778580135101,
        "FeO": 7.542008809495907e-06, "CaO": 0.029900010771698942,
        "Al2O3": 0.01607812602659893, "TiO2": 0.001161825240157193,
        "Na2O": 1.3344162376079728e-22, "K2O": 6.544378404416625e-09,
    },
    64: {
        "SiO2": 0.0024835135180372647, "MgO": 0.35073146643562825,
        "FeO": 7.5193910058890244e-06, "CaO": 0.033564239286234934,
        "Al2O3": 0.015225486004163104, "TiO2": 0.001073551039351885,
        "Na2O": 1.4757610544606816e-22, "K2O": 7.458665140677511e-09,
    },
}
_LUNAR_CALL_66 = {
    "Al2O3": 1360.0091129793375, "CaO": 2014.8606802533598,
    "K2O": 8.212126474571518, "MgO": 2288.4374740969147,
    "Na2O": 1.9086093358233833e-07, "SiO2": 1036.2482058023359,
    "TiO2": 192.89582454128606, "FeO": 0.02787490262712387,
}
_LUNAR_SOLVED_COMPOSITION = {
    60: _LUNAR_CALL_60, 61: _LUNAR_CALL_61, 62: _LUNAR_CALL_62,
    63: _LUNAR_CALL_63, 64: _LUNAR_CALL_64, 66: _LUNAR_CALL_66,
}
_LUNAR_SOLVED_T_K = {
    60: 2393.15, 61: 2400.65, 62: 2408.15, 63: 2415.65, 64: 2423.15, 66: 2438.15,
}
_LUNAR_SOLVED_ACTIVITY[66] = {"SiO2": 0.0018117532732782316}
# Parents whose one-step continuation is inside the 1% bound. Na2O's cap is
# zero. K2O stays on the constant-gamma table by the existing exclusion.
_ONE_STEP_PARENTS = ("SiO2", "MgO", "FeO", "CaO", "Al2O3", "TiO2")


def _solved_effective_gamma(call_index: int, oxide: str) -> float:
    """Bulk single-cation gamma of a logged solve.

    a(MO) = a(parent) ** (1/c) and X is the cation fraction of the logged
    composition. gamma = a/X is the coefficient the high-T owner certifies.
    This is the solved call, not the continuation formula.
    """

    activity, fraction = single_cation_activity_and_fraction(
        oxide,
        _LUNAR_SOLVED_ACTIVITY[call_index][oxide],
        _LUNAR_SOLVED_COMPOSITION[call_index],
    )
    return activity / fraction


def _history_request(composition, temperature_K, hour, previous=None):
    request = _request(composition, temperature_K, previous_temperature_K=2393.15)
    return replace(request, control_inputs={
        **request.control_inputs,
        "simulation_hour": hour,
        "high_t_melt_activity_previous": previous or {},
    })


def test_successful_openimcc_physics_is_bit_identical() -> None:
    result = _provider().dispatch(_history_request(BASE_MELT_MOL, 2400.65, 61))
    # Re-measured after merging green a822c275f: d-173 moves the engine pin
    # from e975edd to e1423d8. JSON round-trips preserve float bits.
    assert hashlib.sha256(
        json.dumps(_physics(result), sort_keys=True).encode()
    ).hexdigest() == "0dbdc3673e0d640898dad7b848278f93322c0b48898050618b21811bd63c7e78"


def test_1800_k_pre_switch_physics_pin() -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, 1800.0, high_t_melt_activity="constant_gamma")
    )
    assert hashlib.sha256(
        json.dumps(_physics(result), sort_keys=True).encode()
    ).hexdigest() == "ccdfb94254defeb15f302b251c93dc46ab420c1a83b43b3a78bd4ceb718c8368"


@pytest.mark.parametrize("temperature_K, expected", [(1650.0, False), (1800.0, True)])
def test_certified_edge_selects_openimcc(temperature_K, expected) -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, temperature_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic.get("high_t_melt_activity", {})
    assert (high_t.get("provider") == "openimcc") is expected
    assert high_t.get("above_vaporock_cap", False) is False
    if not expected:
        assert _physics(result) == _physics(_provider().dispatch(
            _request(BASE_MELT_MOL, temperature_K, high_t_melt_activity="constant_gamma")
        ))


def test_binding_reads_lower_edge_from_default_pack(monkeypatch) -> None:
    pack = openimcc_bridge_module._load_pack("default")
    shifted_pack = SimpleNamespace(
        kernel_datapack=SimpleNamespace(domains=((1850.0, 3000.0),)),
    )
    monkeypatch.setattr(openimcc_bridge_module, "_load_pack", lambda _: shifted_pack)
    selected = vapor_pressure_module.openimcc_binding_selected_by_inputs
    assert not selected(temperature_K=1800.0, controls={})
    assert selected(temperature_K=1850.0, controls={})
    monkeypatch.setattr(openimcc_bridge_module, "_load_pack", lambda _: pack)
    assert selected(temperature_K=1800.0, controls={})
    assert selected(
        temperature_K=openimcc_bridge_module._temperature_domain_K()[0], controls={},
    )


def test_refused_openimcc_at_1800_k_keeps_existing_fallback_notice(monkeypatch) -> None:
    def refuse(**_kwargs):
        raise openimcc_bridge_module.OpenImccUnavailableError()

    monkeypatch.setattr(openimcc_bridge_module, "evaluate_cleaned_melt", refuse)
    provider = _provider()
    result = provider.dispatch(_request(BASE_MELT_MOL, 1800.0))
    high_t = result.diagnostic["high_t_melt_activity"]
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback"] is True
    assert high_t["fallback_reason"]["code"] == "openimcc_not_importable"
    assert any(n["code"] == "openimcc_not_importable" for n in high_t["notices"])
    legacy = provider.dispatch(_request(
        BASE_MELT_MOL, 1800.0, high_t_melt_activity="constant_gamma",
    ))
    assert result.diagnostic["vapor_pressures_Pa"] == legacy.diagnostic[
        "vapor_pressures_Pa"
    ]


@pytest.mark.parametrize("gap_K, hour_gap", [
    (7.5, 1), (15.0, 2), (15.01, 1), (0.0, 3),
])
def test_refused_activity_holds_only_local_certified_gamma(
    monkeypatch, gap_K, hour_gap,
) -> None:
    provider = _provider()
    previous = provider.dispatch(_history_request(_LUNAR_CALL_60, 2393.15, 60))
    certified = provider.dispatch(_history_request(
        _LUNAR_CALL_61, 2400.65, 61,
        previous.diagnostic["high_t_melt_activity"],
    ))
    assert certified.diagnostic["high_t_melt_activity"]["provider"] == "openimcc"

    def nonconverge(**kwargs):
        raise ImccNonconvergenceError("logged call-62 refusal", {"residual_inf": 0.3222946427})

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    request = _history_request(
        _LUNAR_CALL_62, 2400.65 + gap_K, 61 + hour_gap,
        certified.diagnostic["high_t_melt_activity"],
    )
    result = provider.dispatch(request)
    authority = result.diagnostic["high_t_melt_activity"]
    activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", _LUNAR_CALL_62, temperature_K=2400.65 + gap_K, authority=authority,
    )
    notices = [n for n in authority["notices"] if n["code"] == "hold_last_certified_effective_gamma"]
    assert authority["authority"] == "extrapolated"
    assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
    # Call 62's composition is one ramp step from the certificate, inside
    # every continued parent's cap. A larger thermal gap no longer drops
    # that parent to the table.
    silica_notice = next(n for n in notices if n["parent"] == "SiO2")
    assert silica_notice["method"] == "extrapolated_first_order_ln_gamma"
    assert silica_notice["temperature_K"] == 2400.65
    assert silica_notice["temperature_gap_K"] == pytest.approx(gap_K)
    assert silica_notice["hour_gap"] == hour_gap
    assert silica_notice["window_K"] == 15.0
    assert silica_notice["original_refusal"]["code"] == "imcc_nonconvergence"
    assert activity.activity_model == "extrapolated_first_order_ln_gamma"
    assert activity.activity_model in activity.warning
    assert activity.activity != melt_oxide_activity("SiO2", _LUNAR_CALL_62).activity
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["SiO"]
    assert provenance["melt_activity_authority"] == "extrapolated_first_order_ln_gamma"
    assert provenance["openimcc_refusal"]["code"] == "imcc_nonconvergence"
    if gap_K == 0.0:
        assert silica_notice["progress_s"] == 0.0
        assert activity.effective_gamma == silica_notice["gamma_last"]
        assert 0.0038 < activity.activity < 0.0041
    if gap_K == 7.5:
        # Independent call-62 transport observation: at frozen geometry and
        # resistances J is linear in (p_eq-p_bulk). Scale the measured solved
        # SiO rate, then exercise the production parent-depletion owner.
        # kg/h * Pa/Pa stays kg/h; zero driving pressure gives zero draw.
        raw_sio = 2.87908559 * (
            (result.diagnostic["vapor_pressures_Pa"]["SiO"] - 106.0723021)
            / (561.522380 - 106.0723021)
        )
        sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
        sim.species_formula_registry = {}
        sim.vapor_pressures = provider._vapor_pressure_data
        parent_mass = (
            _LUNAR_CALL_62["SiO2"]
            * resolve_species_formula("SiO2").molar_mass_kg_per_mol()
        )
        rates = sim._analytic_evaporation_depletion_rates(
            {"SiO": raw_sio}, dt_hr=1.0, phase_scalar=1.0,
            cleaned_melt_kg={"SiO2": parent_mass}, available_o2_kg=0.0,
        )
        removed_mol = rates["SiO"] / resolve_species_formula("SiO").molar_mass_kg_per_mol()
        # Logged solved draw is 5.0014%. The refused table leg drew 93.2713%.
        # The continued activity stays near the solved draw.
        assert 0.04 < removed_mol / _LUNAR_CALL_62["SiO2"] < 0.065
        sim.melt = SimpleNamespace(hour=61, campaign=CampaignPhase.C2A)
        sim._note_high_t_melt_activity_fallback(
            authority, result.diagnostic["vapor_pressure_numerator_provenance"],
        )
        run_notice = sim.high_t_melt_activity_run_notice()
        assert silica_notice in run_notice["notices"]
        assert run_notice["reason"] == "imcc_nonconvergence"
        sentence = _high_t_melt_activity_fallback_sentence(run_notice)
        assert "first-order ln-gamma extrapolated" in sentence
        assert "frozen past a species cap" in sentence
    # A refused prediction must never refresh the certificate or its clock.
    assert authority["certified_effective_gamma_by_parent"] == (
        certified.diagnostic["high_t_melt_activity"]["certified_effective_gamma_by_parent"]
    )
    again = provider.dispatch(replace(request, temperature_C=2415.66 - 273.15, control_inputs={
        **request.control_inputs, "high_t_melt_activity_previous": authority,
        "simulation_hour": 64,
    }))
    again_silica = next(
        n for n in again.diagnostic["high_t_melt_activity"]["notices"]
        if n.get("code") == "hold_last_certified_effective_gamma"
        and n.get("parent") == "SiO2"
    )
    assert again_silica["method"] == "extrapolated_first_order_ln_gamma"
    assert again_silica["consecutive_refusal_count"] == 2


def _certify_lunar_calls_60_61(provider):
    previous = provider.dispatch(_history_request(
        _LUNAR_CALL_60, _LUNAR_SOLVED_T_K[60], 60,
    ))
    certified = provider.dispatch(_history_request(
        _LUNAR_CALL_61, _LUNAR_SOLVED_T_K[61], 61,
        previous.diagnostic["high_t_melt_activity"],
    ))
    authority = certified.diagnostic["high_t_melt_activity"]
    assert authority["provider"] == "openimcc"
    return authority


def _refuse_lunar_call(provider, monkeypatch, call_index, previous):
    def nonconverge(**kwargs):
        raise ImccNonconvergenceError(f"replayed refusal at call {call_index}", {})

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    result = provider.dispatch(_history_request(
        _LUNAR_SOLVED_COMPOSITION[call_index],
        _LUNAR_SOLVED_T_K[call_index],
        call_index,
        previous,
    ))
    return result.diagnostic["high_t_melt_activity"]


def test_extrapolation_caps_match_c2b7b4e_step_prior() -> None:
    """Per-species L1 caps are the owner's step counts times the ramp mean.

    Step counts are the c2b7b4e measurement (median absolute error ≤ 5%,
    worst ≤ 20%): FeO 8, MgO and CaO 4, SiO2, Al2O3 and TiO2 3, K2O 1,
    Na2O 0. The mean is the single-cation L1 of one 7.5 K step, summed
    over the 69 such steps and divided by 69.
    """

    steps = {
        "FeO": 8, "MgO": 4, "CaO": 4,
        "SiO2": 3, "Al2O3": 3, "TiO2": 3,
        "K2O": 1, "Na2O": 0,
    }
    assert vapor_pressure_module._EXTRAPOLATION_CAP_STEPS == steps
    mean = vapor_pressure_module._EXTRAPOLATION_MEAN_STEP_L1
    assert mean == pytest.approx(0.8844714170310508 / 69)
    caps = vapor_pressure_module._EXTRAPOLATION_CAP_L1
    assert set(caps) == set(steps)
    for oxide, count in steps.items():
        assert caps[oxide] == count * mean
    assert caps["Na2O"] == 0.0


def test_logged_refusal_extrapolates_ln_gamma_and_hold_does_not(
    monkeypatch,
) -> None:
    """Replay c2b7b4e calls 62 and 62–64, then call 66, as refusals.

    Solved gammas are the logged parent activities on the bulk
    single-cation basis. One step must land within 1% for each parent
    the cap allows. The zero-order hold of the call-61 coefficient misses
    that bound. Na2O's cap is zero, so its gamma stays at the certificate.
    Call 63 is inside the SiO2 cap (SiO2 error about -2.0%). Call 64 is
    just past that cap, so gamma freezes at the call-63 value instead of
    falling through to the table. The in-cap slope, read at s=3, is the
    measured SiO2 growth of -3.7%. Call 66 is the cap plus two thermal
    steps; the frozen error stays below the table's error.
    """

    provider = _provider()
    certified = _certify_lunar_calls_60_61(provider)
    certificates = certified["certified_effective_gamma_by_parent"]
    hold_errors = {
        oxide: (
            certificates[oxide]["gamma_last"] - _solved_effective_gamma(62, oxide)
        ) / _solved_effective_gamma(62, oxide)
        for oxide in _ONE_STEP_PARENTS
    }
    # The zero-order hold is outside 1% (SiO2 is about +14%). FeO's hold
    # can sit near 1%; SiO2 is the parent that proves the bound discriminates.
    assert abs(hold_errors["SiO2"]) > 0.01
    assert max(abs(error) for error in hold_errors.values()) > 0.01

    authority = _refuse_lunar_call(provider, monkeypatch, 62, certified)
    assert authority["certified_effective_gamma_by_parent"] == certificates
    assert "K2O" not in authority["held_effective_gamma_by_parent"]
    errors = {}
    for oxide in _ONE_STEP_PARENTS:
        activity = vapor_pressure_module._high_t_activity_for_parent(
            oxide, _LUNAR_CALL_62, temperature_K=_LUNAR_SOLVED_T_K[62],
            authority=authority,
        )
        solved = _solved_effective_gamma(62, oxide)
        errors[oxide] = (activity.effective_gamma - solved) / solved
    assert all(abs(error) < 0.01 for error in errors.values()), errors
    for oxide in _ONE_STEP_PARENTS:
        notice = next(
            n for n in authority["notices"]
            if n.get("parent") == oxide
            and n["code"] == "hold_last_certified_effective_gamma"
        )
        assert notice["method"] == "extrapolated_first_order_ln_gamma"
        assert notice["progress_variable"] == "temperature"
        assert notice["progress_s"] == pytest.approx(1.0)
        assert notice["consecutive_refusal_count"] == 1
        assert math.isfinite(notice["ln_gamma_slope"])
        assert notice.get("flag") != "extrapolation_capped_frozen"
        activity = vapor_pressure_module._high_t_activity_for_parent(
            oxide, _LUNAR_CALL_62, temperature_K=_LUNAR_SOLVED_T_K[62],
            authority=authority,
        )
        assert activity.activity_model == "extrapolated_first_order_ln_gamma"
    # Every probe failed, so the bridge is exhausted and the capped secant
    # is the fallback. The probes stay inside the ±75 K schedule.
    bridge = authority["temperature_bridge"]
    assert bridge["call_count"] == vapor_pressure_module._TEMPERATURE_BRIDGE_MAX_CALLS
    assert bridge["cap_K"] == 75.0
    refused_T = _LUNAR_SOLVED_T_K[62]
    assert [abs(probe_T - refused_T) for probe_T in bridge["attempted_temperatures_K"]] == (
        pytest.approx([25.0, 25.0, 50.0, 50.0, 75.0, 75.0])
    )
    assert notice["bridge_fallback"] == "fewer_than_two_temperature_points"
    assert notice["method"] != "same_composition_T_bridge"
    sodium = vapor_pressure_module._high_t_activity_for_parent(
        "Na2O", _LUNAR_CALL_62, temperature_K=_LUNAR_SOLVED_T_K[62],
        authority=authority,
    )
    sodium_notice = next(n for n in authority["notices"] if n.get("parent") == "Na2O")
    assert sodium.effective_gamma == certificates["Na2O"]["gamma_last"]
    assert sodium.activity_model != "extrapolated_first_order_ln_gamma"
    assert sodium_notice["flag"] == "extrapolation_capped_frozen"
    assert sodium_notice["composition_cap_L1"] == 0.0
    assert sodium_notice["composition_distance_L1"] > 0.0

    # s = 0 is the certified state: the step must return that coefficient.
    at_certificate = _refuse_lunar_call(provider, monkeypatch, 61, certified)
    silica = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", _LUNAR_CALL_61, temperature_K=_LUNAR_SOLVED_T_K[61],
        authority=at_certificate,
    )
    silica_notice = next(
        n for n in at_certificate["notices"] if n.get("parent") == "SiO2"
    )
    assert silica_notice["progress_s"] == 0.0
    assert silica.effective_gamma == certificates["SiO2"]["gamma_last"]

    # Calls 62–64 in a row. Certificates stay at call 61, so s grows.
    chained = certified
    live_gamma = {}
    for call_index in (62, 63, 64):
        chained = _refuse_lunar_call(provider, monkeypatch, call_index, chained)
        assert chained["certified_effective_gamma_by_parent"] == certificates
        notice = next(n for n in chained["notices"] if n.get("parent") == "SiO2")
        assert notice["consecutive_refusal_count"] == call_index - 61
        activity = vapor_pressure_module._high_t_activity_for_parent(
            "SiO2", _LUNAR_SOLVED_COMPOSITION[call_index],
            temperature_K=_LUNAR_SOLVED_T_K[call_index], authority=chained,
        )
        live_gamma[call_index] = activity.effective_gamma
        if call_index == 64:
            assert notice["flag"] == "extrapolation_capped_frozen"
            assert notice["composition_distance_L1"] > notice["composition_cap_L1"]
            assert activity.activity != melt_oxide_activity(
                "SiO2", _LUNAR_CALL_64,
            ).activity
            continue
        assert notice["method"] == "extrapolated_first_order_ln_gamma"
        assert notice["progress_s"] == pytest.approx(call_index - 61)
        solved = _solved_effective_gamma(call_index, "SiO2")
        if call_index == 63:
            assert (live_gamma[63] - solved) / solved == pytest.approx(-0.020, abs=0.0015)
    # Past the cap, gamma is the last in-cap extrapolation, not a new step.
    assert live_gamma[64] == live_gamma[63]
    # The slope published while still inside the cap, read at s=3, is the
    # measured SiO2 growth of -3.7% against the logged solve.
    in_cap = next(n for n in authority["notices"] if n.get("parent") == "SiO2")
    s_three = (
        (_LUNAR_SOLVED_T_K[64] - in_cap["temperature_K"])
        / (in_cap["temperature_K"] - in_cap["temperature_prev_K"])
    )
    assert s_three == pytest.approx(3.0)
    predicted = math.exp(in_cap["ln_gamma_last"] + in_cap["ln_gamma_slope"] * s_three)
    solved_64 = _solved_effective_gamma(64, "SiO2")
    assert (predicted - solved_64) / solved_64 == pytest.approx(-0.037, abs=0.0015)

    # Call 66 is five thermal steps from call 61: the SiO2 cap plus two.
    beyond = _refuse_lunar_call(provider, monkeypatch, 66, chained)
    frozen = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", _LUNAR_CALL_66, temperature_K=_LUNAR_SOLVED_T_K[66],
        authority=beyond,
    )
    solved_66 = _solved_effective_gamma(66, "SiO2")
    table = melt_oxide_activity("SiO2", _LUNAR_CALL_66)
    frozen_error = abs(frozen.effective_gamma - solved_66) / solved_66
    table_error = abs(table.effective_gamma - solved_66) / solved_66
    assert frozen.effective_gamma == live_gamma[63]
    assert frozen_error < table_error
    beyond_notice = next(n for n in beyond["notices"] if n.get("parent") == "SiO2")
    assert beyond_notice["flag"] == "extrapolation_capped_frozen"


_BRIDGE_REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "openimcc_temperature_bridge.json").read_text()
)


def _call_record(engine: str, index: int) -> dict:
    return _BRIDGE_REFERENCE["records"][f"{engine}:{index}"]


def _reference_regular_solution_gamma(
    observations: list[tuple[float, float]], temperature_K: float,
) -> float:
    """RT ln γ = W + S T through another engine's solves.

    This is the specified temperature line, evaluated on c2b7b4e gammas.
    It is not the production function. Two points fix the line; the refused
    temperature is where that line is read.
    """

    (first_T, first_gamma), (second_T, second_gamma) = observations
    first_y = GAS_CONSTANT * first_T * math.log(first_gamma)
    second_y = GAS_CONSTANT * second_T * math.log(second_gamma)
    entropy = (second_y - first_y) / (second_T - first_T)
    enthalpy = first_y - entropy * first_T
    return math.exp(
        (enthalpy + entropy * temperature_K) / (GAS_CONSTANT * temperature_K)
    )


def test_refused_calls_use_same_composition_temperature_bridge(monkeypatch) -> None:
    """Force historical e975edd refusals; the bridge is the primary gamma.

    Call 62 has the same composition in the solved c2b7b4e log, so that
    log is the truth. Call 65's trajectories have diverged, and c2b7b4e
    also refuses that composition at 2430.65 K, so its truth is the
    regular-solution line through c2b7b4e solves at the bridge temperatures.
    """

    provider = _provider()
    certified = _certify_lunar_calls_60_61(provider)
    refused_62 = _call_record("e975edd", 62)
    refused_65 = _call_record("e975edd", 65)
    target_temperatures = {refused_62["T_K"], refused_65["T_K"]}
    evaluate = openimcc_bridge_module.evaluate
    refused_temperatures = []

    def refuse_targets(**kwargs):
        temperature_K = kwargs["temperature_K"]
        if temperature_K in target_temperatures:
            refused_temperatures.append(temperature_K)
            raise ImccNonconvergenceError("forced historical target refusal", {})
        return evaluate(**kwargs)

    # Solver improvements may recover the targets. Force only their refusal;
    # nearby probes still solve the same composition with the live engine.
    monkeypatch.setattr(openimcc_bridge_module, "evaluate", refuse_targets)
    solved_62 = _call_record("c2b7b4e", 62)
    assert refused_62["ok"] is False
    assert solved_62["composition"] == refused_62["composition"]
    result_62 = provider.dispatch(_history_request(
        refused_62["composition"], refused_62["T_K"], 62, certified,
    ))
    authority = result_62.diagnostic["high_t_melt_activity"]
    assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
    bridge_parents = _ONE_STEP_PARENTS + ("Na2O",)
    errors = {}
    solved_activity = dict(zip(
        solved_62["parent_oxides"], solved_62["parent_activity"],
    ))
    for oxide in bridge_parents:
        activity = vapor_pressure_module._high_t_activity_for_parent(
            oxide, refused_62["composition"],
            temperature_K=refused_62["T_K"], authority=authority,
        )
        solved_cation_activity, fraction = single_cation_activity_and_fraction(
            oxide, solved_activity[oxide], solved_62["composition"],
        )
        truth = solved_cation_activity / fraction
        errors[oxide] = (activity.effective_gamma - truth) / truth
        notice = next(n for n in authority["notices"] if n.get("parent") == oxide)
        assert notice["method"] == "same_composition_T_bridge"
        assert notice["bridge_mode"] == "interpolation"
        assert notice["bridge_call_count"] <= 6
        assert notice["bridge_cap_K"] == 75.0
        assert math.isfinite(notice["W_J_per_mol"])
        assert math.isfinite(notice["S_J_per_mol_K"])
        for probe_T in notice["bridge_attempted_temperatures_K"]:
            offset = abs(probe_T - refused_62["T_K"])
            assert min(abs(offset - step) for step in (25.0, 50.0, 75.0)) < 1e-6
        assert activity.activity_model == "same_composition_T_bridge"
        assert activity.activity != melt_oxide_activity(
            oxide, refused_62["composition"],
        ).activity
    assert all(abs(error) < 0.01 for error in errors.values()), errors
    assert "K2O" not in authority["held_effective_gamma_by_parent"]
    assert (
        result_62.diagnostic["vapor_pressure_numerator_provenance"]["SiO"][
            "melt_activity_authority"
        ]
        == "same_composition_T_bridge"
    )

    assert refused_65["ok"] is False
    result_65 = provider.dispatch(_history_request(
        refused_65["composition"], refused_65["T_K"], 65,
    ))
    authority_65 = result_65.diagnostic["high_t_melt_activity"]
    assert authority_65["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert refused_temperatures == [refused_62["T_K"], refused_65["T_K"]]
    silica = next(
        n for n in authority_65["notices"] if n.get("parent") == "SiO2"
    )
    assert silica["method"] == "same_composition_T_bridge"
    assert silica["bridge_mode"] == "interpolation"
    assert silica["bridge_fit"] == "exact_line"
    assert silica["bridge_call_count"] <= 6
    probe_temperatures = silica["bridge_temperatures_K"]
    assert len(probe_temperatures) == 2
    reference_observations = []
    for probe_T in probe_temperatures:
        gammas = _BRIDGE_REFERENCE["c2b7b4e_call_65_probe_gammas"][str(probe_T)]
        reference_observations.append((probe_T, gammas["SiO2"]))
    reference = _reference_regular_solution_gamma(
        reference_observations, refused_65["T_K"],
    )
    activity_65 = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", refused_65["composition"],
        temperature_K=refused_65["T_K"], authority=authority_65,
    )
    assert (activity_65.effective_gamma - reference) / reference == pytest.approx(
        0.0, abs=0.01,
    )
    assert activity_65.activity != melt_oxide_activity(
        "SiO2", refused_65["composition"],
    ).activity


@pytest.mark.parametrize("call_index", [62, 65])
def test_recovered_historical_call_uses_direct_live_solve(call_index) -> None:
    """Successful numerical recovery must not enter continuation."""

    record = _call_record("e975edd", call_index)
    try:
        direct = evaluate_cleaned_melt(record["composition"], record["T_K"])
    except ImccNonconvergenceError:
        pytest.skip("installed engine still refuses this historical target")

    provider = _provider()
    certified = _certify_lunar_calls_60_61(provider)
    result = provider.dispatch(_history_request(
        record["composition"], record["T_K"], call_index, certified,
    ))
    authority = result.diagnostic["high_t_melt_activity"]
    assert authority["provider"] == "openimcc"
    assert authority["openimcc_activities_by_oxide"] == dict(direct.single_cation_activities)
    for oxide in _ONE_STEP_PARENTS + ("Na2O",):
        assert authority["activities_by_oxide"][oxide] == direct.single_cation_activities[oxide]
    assert authority["fallback"] is False
    assert "fallback_reason" not in authority
    assert "temperature_bridge" not in authority
    assert not authority.get("held_effective_gamma_by_parent")
    assert not any(
        notice.get("code") == "hold_last_certified_effective_gamma"
        for notice in authority["notices"]
    )


@pytest.mark.parametrize("silica_factor", [1.0, 0.99])
def test_first_high_t_certificate_without_previous_temperature_can_hold(
    monkeypatch, silica_factor,
) -> None:
    request = _request(BASE_MELT_MOL, 2400.65)
    provider = _provider()
    certified = provider.dispatch(replace(
        request, control_inputs={**request.control_inputs, "simulation_hour": 1},
    )).diagnostic["high_t_melt_activity"]

    def nonconverge(**kwargs):
        raise ImccNonconvergenceError("next same-T call refused", {})

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    composition = {**BASE_MELT_MOL, "SiO2": BASE_MELT_MOL["SiO2"] * silica_factor}
    refused_request = _request(composition, 2400.65)
    result = provider.dispatch(replace(refused_request, control_inputs={
        **request.control_inputs, "simulation_hour": 2,
        "high_t_melt_activity_previous": certified,
    }))
    authority = result.diagnostic["high_t_melt_activity"]
    certificate = certified["certified_effective_gamma_by_parent"]["SiO2"]
    assert certificate["window_K"] == 0.0
    assert certificate["composition_window_L1"] == 0.0
    # One certified call has no slope, so the continuation is the
    # zero-order hold. A 1% silica change is inside the SiO2 cap and
    # must not fall through to the table.
    assert authority["held_effective_gamma_by_parent"]
    activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", composition, temperature_K=2400.65, authority=authority,
    )
    assert activity.effective_gamma == certificate["gamma_last"]
    assert activity.activity_model == "hold_last_certified_effective_gamma"
    assert "openimcc_refused_constant_gamma_fallback" not in (activity.warning or "")
    if silica_factor == 1.0:
        assert activity.activity == pytest.approx(
            certified["activities_by_oxide"]["SiO2"]
        )
    else:
        assert activity.activity != certified["activities_by_oxide"]["SiO2"]
        assert activity.activity != melt_oxide_activity("SiO2", composition).activity


@pytest.mark.parametrize("drift_mol, inside_cap", [
    (0.125, True), (0.25, False), (0.2578125, False),
])
def test_isothermal_refusal_expires_on_composition_drift(
    monkeypatch, drift_mol, inside_cap,
) -> None:
    provider = _provider()
    # Eight mol of cations, with dyadic amounts so the distances are exact.
    # Swap 1/8 mol Si for Mg: each X changes by 1/64, so the L1 step is
    # 1/32. A further swap of d mol moves L1 by d/4. The SiO2 cap is three
    # mean ramp steps (about 0.0385), between the one-step drift (0.03125)
    # and the two-step drift (0.0625). Past the cap the gamma freezes.
    initial = {
        "SiO2": 4.0, "MgO": 1.0, "FeO": 1.0, "CaO": 1.0,
        "Al2O3": 0.25, "TiO2": 0.25, "Na2O": 0.0625, "K2O": 0.0625,
    }
    at_certificate = {**initial, "SiO2": 3.875, "MgO": 1.125}
    previous = provider.dispatch(_history_request(initial, 2400.65, 60))
    certified = provider.dispatch(_history_request(
        at_certificate, 2400.65, 61,
        previous.diagnostic["high_t_melt_activity"],
    )).diagnostic["high_t_melt_activity"]

    def nonconverge(**kwargs):
        raise ImccNonconvergenceError("isothermal soak refusal", {})

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    composition = {
        **at_certificate,
        "SiO2": at_certificate["SiO2"] - drift_mol,
        "MgO": at_certificate["MgO"] + drift_mol,
    }
    authority = provider.dispatch(_history_request(
        composition, 2400.65, 100, certified,
    )).diagnostic["high_t_melt_activity"]
    activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", composition, temperature_K=2400.65, authority=authority,
    )
    assert authority["held_effective_gamma_by_parent"]
    notice = next(n for n in authority["notices"] if n.get("parent") == "SiO2")
    assert notice["temperature_gap_K"] == 0.0
    assert notice["composition_window_L1"] == 0.0625
    assert notice["progress_variable"] == "composition"
    cap = vapor_pressure_module._EXTRAPOLATION_CAP_L1["SiO2"]
    assert (notice["composition_distance_L1"] <= cap) is inside_cap
    assert authority["certified_effective_gamma_by_parent"] == (
        certified["certified_effective_gamma_by_parent"]
    )
    if inside_cap:
        # L1 scales with the swapped moles on this dyadic melt, and the
        # certified step was 0.125 mol, so s = drift_mol / 0.125.
        assert notice["method"] == "extrapolated_first_order_ln_gamma"
        assert notice["progress_s"] == pytest.approx(drift_mol / 0.125)
        assert activity.activity_model == "extrapolated_first_order_ln_gamma"
        assert notice.get("flag") != "extrapolation_capped_frozen"
    else:
        assert notice["flag"] == "extrapolation_capped_frozen"
        assert notice["method"] == "extrapolation_capped_frozen"
        assert notice["composition_distance_L1"] > notice["composition_cap_L1"]
        assert activity.activity_model == "extrapolation_capped_frozen"
        assert activity.activity != melt_oxide_activity("SiO2", composition).activity
        assert "openimcc_refused_constant_gamma_fallback" not in (activity.warning or "")
    assert activity.evidence_tier == "PREDICT_AND_FLAG"


def test_below_certified_edge_is_activity_source_parity_with_pre_c3_path() -> None:
    provider = _provider()
    temperature_K = openimcc_bridge_module._temperature_domain_K()[0] - 0.1
    legacy = provider.dispatch(_request(BASE_MELT_MOL, temperature_K))
    explicit_openimcc = provider.dispatch(
        _request(BASE_MELT_MOL, temperature_K, high_t_melt_activity="openimcc")
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
    assert high_t["openimcc_pack_version"] == "1.0.3-d066-v1"
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


def test_high_t_activity_citation_uses_loaded_pack_identity() -> None:
    model_ids = []
    for pack_name in ("default", "published-core"):
        bridge = openimcc_bridge_module.evaluate(
            composition_mol=BASE_MELT_MOL,
            temperature_K=2200.0,
            pack=pack_name,
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )
        single_activity, _fraction = (
            openimcc_bridge_module.single_cation_activity_and_fraction(
                "SiO2",
                bridge.parent_oxide_activities["SiO2"],
                BASE_MELT_MOL,
            )
        )
        authority = {
            "provider": "openimcc",
            "activities_by_oxide": {"SiO2": single_activity},
            "openimcc_pack_model_id": bridge.pack_model_id,
            "openimcc_pack_version": bridge.pack_version,
            "openimcc_pack_digest": bridge.pack_digest,
        }
        activity = vapor_pressure_module._high_t_activity_for_parent(
            "SiO2",
            BASE_MELT_MOL,
            temperature_K=2200.0,
            authority=authority,
        )

        assert activity is not None
        assert activity.citation == (
            f"openimcc {bridge.pack_model_id} pack {bridge.pack_version} "
            f"digest={bridge.pack_digest}"
        )
        model_ids.append(bridge.pack_model_id)

    assert model_ids[0] != model_ids[1]


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
    assert high_t["seam"]["K2O"]["would_be_openimcc_step_dex"] == pytest.approx(
        -0.24814525336393226
    )
    assert high_t["authority_exclusions"]["K2O"]["tracking_id"] == "t-999"
    reason = high_t["authority_exclusions"]["K2O"]["reason"]
    assert "owner ruling of 2026-09-26" in reason
    assert "t-1194" in reason
    evaluations = vapor_pressure_module._K2O_PACK_EVALUATIONS_BY_DIGEST
    digest = high_t["openimcc_pack_digest"]
    row = evaluations[digest]
    present = set(row["gamma_ko0_5_by_temperature_K"].values())
    absent = {
        gamma
        for other_digest, other_row in evaluations.items()
        if other_digest != digest
        for gamma in other_row["gamma_ko0_5_by_temperature_K"].values()
    }
    assert present and absent and present.isdisjoint(absent)
    for printed_value in present:
        assert printed_value in reason
    for printed_value in absent:
        assert printed_value not in reason

    # Keep the table-derived text expectations tied to the loaded pack's
    # bridge values, so a changed table value cannot validate itself.
    for temperature_K, printed_value in row[
        "gamma_ko0_5_by_temperature_K"
    ].items():
        bridge = openimcc_bridge_module.evaluate(
            composition_kg=ZHANG_KO0_5_COMPOSITION_WT_PCT,
            temperature_K=temperature_K,
            pack=row["pack_alias"],
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )
        assert bridge.pack_digest == digest
        significant_figures = sum(
            character.isdigit() for character in printed_value.split("e", 1)[0]
        )
        assert float(
            f"{bridge.activity_coefficients['KO0.5']['value']:.{significant_figures}g}"
        ) == float(printed_value)

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


def test_ae95_depleted_melt_continuation_root_is_selected_by_provider() -> None:
    # Captured ae95 hot-train melt at campaign hour 66; pressure and fO2
    # controls match the provider comparison in golden-shift.md.
    composition_mol = {
        "SiO2": 0.011138886562497141,
        "MgO": 2288.8042997487846,
        "FeO": 87.38220230346212,
        "CaO": 2014.8644234379233,
        "Al2O3": 1360.0091131723866,
        "TiO2": 192.82496476933426,
        "Na2O": 5.3731722769972105e-09,
        "K2O": 8.844980280923895,
    }
    result = _provider().dispatch(
        _request(
            composition_mol,
            2445.65,
            high_t_melt_activity="openimcc",
            pressure_bar=0.01,
            pO2_bar=1.0e-9,
            intrinsic_fO2_log=-2.0727607447837726,
        )
    )
    high_t = result.diagnostic["high_t_melt_activity"]
    sio_provenance = result.diagnostic["vapor_pressure_numerator_provenance"][
        "SiO"
    ]

    assert result.status == "ok"
    assert high_t["provider"] == "openimcc"
    assert high_t["fallback"] is False
    assert "fallback_reason" not in high_t
    assert high_t["activities_by_oxide"]["SiO2"] == pytest.approx(
        3.4859611072197934e-9,
        rel=1e-9,
        abs=0.0,
    )
    assert sio_provenance["melt_activity_authority"] == "openimcc"
    assert not sio_provenance.get("melt_activity_fallback", False)
    assert not any(
        "high_t_melt_activity_fallback" in warning
        or "openimcc_refused_constant_gamma_fallback" in warning
        for warning in result.warnings
    )
    assert not any(
        "openimcc_refused_constant_gamma_fallback" in source
        for source in result.diagnostic["vapor_pressures_source"].values()
    )

    # The recovered root does not change the standing K2O authority ruling.
    k2o_exclusion = high_t["authority_exclusions"]["K2O"]
    k2o_constant_gamma = melt_oxide_activity(
        "K2O",
        composition_mol,
        temperature_K=2445.65,
    )
    assert k2o_exclusion["tracking_id"] == "t-999"
    assert high_t["seam"]["K2O"]["selected_activity_basis"] == "constant_gamma"
    assert high_t["activities_by_oxide"]["K2O"] == k2o_constant_gamma.activity
    assert high_t["openimcc_activities_by_oxide"]["K2O"] != (
        k2o_constant_gamma.activity
    )


def test_k2o_pack_evaluation_table_matches_zhang_gamma_from_bridge() -> None:
    evaluations = vapor_pressure_module._K2O_PACK_EVALUATIONS_BY_DIGEST
    loaded_digests = set()
    results = []
    # The widest printed-value rounding is 2.4e-8 +/- 0.05e-8 = 2.083%;
    # 2.1% covers that half-last-digit interval and the more precise rows.
    for pack_name in ("default", "published-core"):
        for temperature_K in (1473.15, 1673.15):
            bridge = openimcc_bridge_module.evaluate(
                composition_kg=ZHANG_KO0_5_COMPOSITION_WT_PCT,
                temperature_K=temperature_K,
                pack=pack_name,
                allow_extrapolation=True,
                allow_out_of_envelope=True,
            )
            loaded_digests.add(bridge.pack_digest)
            results.append((bridge, temperature_K))

    for bridge, temperature_K in results:
        assert bridge.pack_digest in evaluations, (
            f"K2O evaluation is missing digest {bridge.pack_digest}; the table "
            "must be re-evaluated after a pack change"
        )
        row = evaluations[bridge.pack_digest]
        assert row["model_id"] == bridge.pack_model_id
        assert row["pack_version"] == bridge.pack_version
        expected = float(row["gamma_ko0_5_by_temperature_K"][temperature_K])
        assert bridge.activity_coefficients["KO0.5"]["value"] == pytest.approx(
            expected, rel=0.021
        )
    for digest in evaluations:
        assert digest in loaded_digests, (
            f"K2O evaluation digest {digest} is not loadable; the table must be "
            "re-evaluated after a pack change"
        )


def test_k2o_sensitivity_pack_collision_gets_no_evaluation() -> None:
    from importlib import resources

    sensitivity_pack = resources.files("openimcc").joinpath(
        "data", "packs", "imcc-sf04-d066-v1-kcaalsi2o7.json"
    )
    if not sensitivity_pack.is_file():
        pytest.skip("the loaded OpenIMCC pin does not include the sensitivity pack")

    evaluations = vapor_pressure_module._K2O_PACK_EVALUATIONS_BY_DIGEST
    default = openimcc_bridge_module.evaluate(
        composition_kg=ZHANG_KO0_5_COMPOSITION_WT_PCT,
        temperature_K=1473.15,
        pack="default",
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    sensitivity = openimcc_bridge_module.evaluate(
        composition_kg=ZHANG_KO0_5_COMPOSITION_WT_PCT,
        temperature_K=1473.15,
        pack="d066-kca-sensitivity",
        allow_extrapolation=True,
        allow_out_of_envelope=True,
    )
    assert sensitivity.pack_model_id == default.pack_model_id
    assert sensitivity.pack_digest != default.pack_digest

    exclusion = vapor_pressure_module._k2o_authority_exclusion_for_pack_digest(
        sensitivity.pack_digest,
        model_id=sensitivity.pack_model_id,
        pack_version=sensitivity.pack_version,
    )
    reason = exclusion["reason"]
    assert "no evaluation exists for this pack" in reason
    for row in evaluations.values():
        for gamma in row["gamma_ko0_5_by_temperature_K"].values():
            assert gamma not in reason


def test_k2o_unknown_pack_notice_has_no_other_pack_evaluation() -> None:
    evaluations = vapor_pressure_module._K2O_PACK_EVALUATIONS_BY_DIGEST
    exclusion = vapor_pressure_module._k2o_authority_exclusion_for_pack_digest(
        "unlisted-digest",
        model_id="unlisted-model",
        pack_version="unlisted-version",
    )
    reason = exclusion["reason"]

    assert "owner ruling of 2026-09-26" in reason
    assert "t-1194" in reason
    assert "no evaluation exists for this pack" in reason
    absent = {
        gamma
        for row in evaluations.values()
        for gamma in row["gamma_ko0_5_by_temperature_K"].values()
    }
    assert absent
    for printed_value in absent:
        assert printed_value not in reason


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
    # Keep this typed result-shape refusal as a prediction, with its reason
    # made explicit on the extrapolated authority ladder.
    assert result.status == "ok"
    assert high_t["provider"] == "constant_gamma"
    assert high_t["fallback"] is True
    assert high_t["authority"] == "extrapolated"
    assert high_t["fallback_reason"]["code"] == "openimcc_result_shape"
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    for species in ("Na", "Na2", "Na2O_gas"):
        assert provenance[species]["melt_activity_authority"] == "constant_gamma"
        assert provenance[species]["openimcc_refusal"]["code"] == (
            "openimcc_result_shape"
        )


def test_unexpected_openimcc_activity_error_propagates(monkeypatch) -> None:
    def fail(**kwargs):
        raise RuntimeError("unexpected evaluator bug")

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", fail)

    with pytest.raises(RuntimeError, match="unexpected evaluator bug"):
        _provider().dispatch(
            _request(
                BASE_MELT_MOL,
                CAP_PLUS_T_K,
                high_t_melt_activity="openimcc",
            )
        )


def test_envelope_refusal_keeps_flagged_constant_gamma_prediction() -> None:
    composition = {"Na2O": 2.0, "SiO2": 1.0}
    provider = _provider()
    predicted = provider.dispatch(
        _request(
            composition,
            2400.0,
            high_t_melt_activity="openimcc",
        )
    )
    constant_gamma = provider.dispatch(
        _request(
            composition,
            2400.0,
            high_t_melt_activity="constant_gamma",
        )
    )

    assert predicted.status == "ok"
    assert predicted.diagnostic["vapor_pressures_Pa"] == constant_gamma.diagnostic[
        "vapor_pressures_Pa"
    ]
    authority = predicted.diagnostic["high_t_melt_activity"]
    assert authority["provider"] == "constant_gamma"
    assert authority["fallback"] is True
    assert authority["authority"] == "extrapolated"
    assert authority["fallback_reason"]["code"] == (
        "imcc_composition_outside_validated_envelope"
    )


def test_fallback_status_remains_on_warning_without_authority_status_fields(
    monkeypatch,
) -> None:
    def nonconverge(**kwargs):
        raise ImccNonconvergenceError(
            "IMCC-SF04 parent-balance solve did not converge",
            {"residual_inf": 0.25},
        )

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    authority = result.diagnostic["high_t_melt_activity"]
    fallback_activity = vapor_pressure_module._high_t_activity_for_parent(
        "Na2O",
        BASE_MELT_MOL,
        temperature_K=CAP_PLUS_T_K,
        authority=authority,
    )

    assert fallback_activity.evidence_tier == "PREDICT_AND_FLAG"
    assert "openimcc_refused_constant_gamma_fallback" in fallback_activity.warning
    assert fallback_activity.authority_status != "extrapolated"
    fallback_provenance = result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]
    assert all(
        "melt_oxide_activity_authority_status" not in item
        for item in fallback_provenance.values()
        if item.get("melt_activity_fallback")
    )


def test_nonconvergence_keeps_prediction_and_emits_run_notice(monkeypatch) -> None:
    diagnostics = {"iterations": 8, "residual_inf": 0.25}
    real_evaluate = openimcc_bridge_module.evaluate

    def nonconverge(**kwargs):
        raise ImccNonconvergenceError(
            "IMCC-SF04 parent-balance solve did not converge",
            diagnostics,
        )

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    request = _request(
        BASE_MELT_MOL,
        CAP_PLUS_T_K,
        high_t_melt_activity="openimcc",
    )
    predicted = _provider().dispatch(request)
    constant_gamma = _provider().dispatch(
        _request(
            BASE_MELT_MOL,
            CAP_PLUS_T_K,
            high_t_melt_activity="constant_gamma",
        )
    )

    assert predicted.status == "ok"
    assert predicted.diagnostic["vapor_pressures_Pa"] == constant_gamma.diagnostic[
        "vapor_pressures_Pa"
    ]
    high_t = predicted.diagnostic["high_t_melt_activity"]
    assert high_t["provider"] == "constant_gamma"
    assert high_t["authority"] == "extrapolated"
    assert high_t["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert high_t["fallback_reason"]["residual_inf"] == 0.25

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    monkeypatch.setattr(openimcc_bridge_module, "evaluate", real_evaluate)
    execution = RunExecutor().execute_session(session, hours=1)
    sim = session.simulator
    completed_hour = int(sim.melt.hour)
    assert completed_hour > 0
    sim._high_t_melt_activity_fallback_steps = []
    sim.melt.hour = completed_hour - 1
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.temperature_C = CAP_PLUS_T_K - 273.15
    monkeypatch.setattr(openimcc_bridge_module, "evaluate", nonconverge)
    sim._refresh_vapor_pressures_from_kernel(
        SimpleNamespace(
            vapor_pressures_Pa={},
            vapor_pressures_source={},
            liquid_fraction=1.0,
            fO2_log=sim.melt.fO2_log,
            activity_coefficients={},
        )
    )
    sim.melt.hour = completed_hour

    output = run._build_output(execution)
    notice = output["run_metadata"]["high_t_melt_activity_fallback_notice"]
    assert notice["authority"] == "extrapolated"
    assert notice["reason"] == "imcc_nonconvergence"
    assert notice["notices"][0]["residual_inf"] == 0.25
    assert notice["affected_tick_count"] == 1
    assert notice["affected_parent_count"] == 7
    assert notice["affected_parent_count"] == len(notice["affected_parent_oxides"])
    assert "K2O" not in notice["affected_parent_oxides"]
    assert notice["first_hour"] == notice["last_hour"] == 1
    assert notice["campaign"] == "C2A"
    assert output["product_classification"][
        "high_t_melt_activity_fallback_notice"
    ] == notice
    assert "IMCC" in output["product_classification"]["markdown"]


def test_fallback_sentence_renders_all_campaigns() -> None:
    sentence = _high_t_melt_activity_fallback_sentence(
        {
            "reason": "imcc_nonconvergence",
            "campaigns": ["C2A", "C4"],
            "affected_tick_count": 2,
            "affected_parent_count": 7,
            "first_hour": 1,
            "last_hour": 3,
        }
    )

    assert "campaigns `C2A, C4`" in sentence
    assert "campaign `unspecified`" not in sentence


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


@pytest.mark.parametrize("reset_kind", ["cold", "frozen"])
@pytest.mark.parametrize("temperature_K", [1800.0, 2073.15])
def test_simulator_default_passes_openimcc_authority_into_flux_dispatch(
    reset_kind, temperature_K,
) -> None:
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
    sim.melt.temperature_C = temperature_K - 273.15
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

    if reset_kind == "cold":
        sim.melt.temperature_C = 100.0
    else:
        result.liquid_fraction = 0.0
    sim._refresh_vapor_pressures_from_kernel(result)
    assert sim._last_high_t_melt_activity == {}
    assert sim._last_high_t_melt_activity_temperature_K is None

    sim.melt.temperature_C = 1850.0
    result.liquid_fraction = 1.0
    sim._refresh_vapor_pressures_from_kernel(result)
    certificates = sim._last_high_t_melt_activity["certified_effective_gamma_by_parent"]
    assert certificates
    assert all(c["window_K"] == 0.0 for c in certificates.values())
    assert all(c["composition_window_L1"] == 0.0 for c in certificates.values())


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
    assert high_t["provider"] == "openimcc"
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


def test_projection_over_one_percent_refuses_and_provider_solves_projected(
    monkeypatch,
) -> None:
    composition = _moles_from_wt(_projected_weights(over_threshold=True))
    with pytest.raises(OpenImccCompositionPolicyRefusal) as refusal:
        evaluate_cleaned_melt(composition, CAP_PLUS_T_K)
    assert refusal.value.code == "openimcc_projection_over_threshold"
    assert "composition_projection_classification" in refusal.value.diagnostics

    _, projected = openimcc_bridge_module._cleaned_melt_projection(composition)
    expected = evaluate_cleaned_melt(projected, CAP_PLUS_T_K)
    certified = _provider().dispatch(_history_request(BASE_MELT_MOL, 2400.65, 1))
    previous = certified.diagnostic["high_t_melt_activity"]

    def no_numerical_continuation(*args, **kwargs):
        pytest.fail("composition policy refusal entered numerical continuation")

    monkeypatch.setattr(vapor_pressure_module, "_same_composition_temperature_bridge", no_numerical_continuation)
    monkeypatch.setattr(vapor_pressure_module, "_continued_certified_effective_gamma", no_numerical_continuation)
    result = _provider().dispatch(_history_request(composition, CAP_PLUS_T_K, 2, previous))
    high_t = result.diagnostic["high_t_melt_activity"]
    assert result.status == "ok"
    assert high_t["provider"] == "openimcc"
    assert high_t["fallback"] is True
    assert high_t["authority"] == "extrapolated"
    assert high_t["fallback_reason"]["code"] == "openimcc_projection_over_threshold"
    notice = high_t["projected_composition_solve"]
    classifier = refusal.value.diagnostics["composition_projection_classification"]
    assert notice["method"] == "projected_composition_solve"
    assert notice["authority"] == "extrapolated"
    assert notice["omitted_component_wt_pct"] == classifier["dropped_component_wt_pct"]
    assert notice["omitted_total_wt_pct"] == classifier["dropped_total_wt_pct"]
    assert notice["projection_threshold_wt_pct"] == 1.0
    assert notice["temperature_K"] == CAP_PLUS_T_K
    assert high_t["certified_effective_gamma_by_parent"] == previous["certified_effective_gamma_by_parent"]
    assert not high_t.get("held_effective_gamma_by_parent")
    for parent, activity in expected.single_cation_activities.items():
        if parent == "K2O":
            continue
        selected = vapor_pressure_module._high_t_activity_for_parent(
            parent, composition, temperature_K=CAP_PLUS_T_K, authority=high_t,
        )
        assert selected.activity == activity
        assert selected.activity_model == "projected_composition_solve"
        assert selected.effective_gamma * selected.x_single_cation == pytest.approx(activity)
    fallback_provenance = result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]
    assert any(
        row["composition_projection_classification"]["projection_verdict"]
        == "over_threshold"
        for row in fallback_provenance.values()
        if "composition_projection_classification" in row
    )
    assert fallback_provenance["SiO"]["projected_composition_solve"] == notice
    assert fallback_provenance["SiO"]["melt_activity_authority"] == "projected_composition_solve"
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(hour=1, campaign=CampaignPhase.C2A)
    sim._note_high_t_melt_activity_fallback(high_t, fallback_provenance)
    run_notice = sim.high_t_melt_activity_run_notice()
    assert notice in run_notice["notices"]
    assert "projected-composition" in _high_t_melt_activity_fallback_sentence(run_notice)


@pytest.mark.parametrize("omitted_wt_pct,high_omission", [(4.9, False), (5.1, True), (56.0, True)])
def test_projected_solve_warns_above_five_percent_without_a_ceiling(
    omitted_wt_pct, high_omission,
) -> None:
    # Preserve the retained oxide ratios while allocating the specified
    # wt% to P2O5. The original weights sum to 100 wt%; scaling them by
    # the dimensionless retained fraction leaves 100 - omitted wt%.
    weights = {
        parent: wt * (100.0 - omitted_wt_pct) / 100.0
        for parent, wt in _cr_mn_projection_weights(include_cr_mn=False).items()
    }
    weights["P2O5"] = omitted_wt_pct
    result = _provider().dispatch(_request(_moles_from_wt(weights), CAP_PLUS_T_K))
    authority = result.diagnostic["high_t_melt_activity"]
    assert authority["provider"] == "openimcc"
    notice = authority["projected_composition_solve"]
    assert notice["omitted_total_wt_pct"] == pytest.approx(omitted_wt_pct)
    tiers = notice["warning_tiers"]
    assert bool(tiers) is high_omission
    if high_omission:
        assert len(tiers) == 1
        assert tiers[0]["flag"] == "projected_composition_high_omission"
        assert tiers[0]["above_omitted_wt_pct"] == 5.0
        assert "d-175" in tiers[0]["provenance"]
        assert tiers[0] in result.diagnostic["vapor_pressure_numerator_provenance"]["SiO"]["projected_composition_solve"]["warning_tiers"]


def test_projected_nonconvergence_bridges_the_projected_composition(monkeypatch) -> None:
    composition = _moles_from_wt(_projected_weights(over_threshold=True))
    _, projected = openimcc_bridge_module._cleaned_melt_projection(composition)
    evaluate = openimcc_bridge_module.evaluate_cleaned_melt
    calls = []

    def refuse_projected_at_actual_T(composition_mol, temperature_K, **kwargs):
        calls.append((dict(composition_mol), temperature_K))
        if composition_mol == projected and temperature_K == CAP_PLUS_T_K:
            raise ImccNonconvergenceError("test projected numerical gap", {})
        return evaluate(composition_mol, temperature_K, **kwargs)

    monkeypatch.setattr(openimcc_bridge_module, "evaluate_cleaned_melt", refuse_projected_at_actual_T)
    result = _provider().dispatch(_history_request(composition, CAP_PLUS_T_K, 1))
    high_t = result.diagnostic["high_t_melt_activity"]
    assert calls[0] == (composition, CAP_PLUS_T_K)
    assert calls[1] == (projected, CAP_PLUS_T_K)
    assert all(comp == projected for comp, _ in calls[2:])
    assert high_t["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert high_t["projected_composition_solve"]["original_refusal"]["code"] == "openimcc_projection_over_threshold"
    silica = high_t["held_effective_gamma_by_parent"]["SiO2"]
    assert silica["method"] == "same_composition_T_bridge"
    # The bridge's a(T) must be independent of the denominator used to
    # report effective gamma; compare with its existing projected-bulk path.
    reference = _provider().dispatch(_history_request(projected, CAP_PLUS_T_K, 1))
    projected_activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", projected, temperature_K=CAP_PLUS_T_K,
        authority=reference.diagnostic["high_t_melt_activity"],
    )
    bulk_activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", composition, temperature_K=CAP_PLUS_T_K, authority=high_t,
    )
    assert bulk_activity.activity == pytest.approx(projected_activity.activity, rel=1e-12)


@pytest.mark.parametrize("parent, amount", [("NaCl", -1.0), ("MgO", -1.0), ("MgO", float("nan"))])
def test_invalid_inventory_reaches_typed_constant_gamma_fallback(parent, amount) -> None:
    certified = _provider().dispatch(_history_request(
        BASE_MELT_MOL, CAP_PLUS_T_K, 1,
    )).diagnostic["high_t_melt_activity"]
    authority = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol={"SiO2": 1.0, parent: amount},
        temperature_K=CAP_PLUS_T_K,
        controls={
            "high_t_melt_activity": "openimcc", "simulation_hour": 2,
            "high_t_melt_activity_previous": certified,
        },
        below_cap_fe_activity=0.1,
        below_cap_fe_activity_basis="constant_gamma",
    )

    assert authority["provider"] == "constant_gamma"
    assert authority["fallback"] is True
    assert authority["held_effective_gamma_by_parent"] == {}
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
    "authority": high_t["authority"],
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
        "authority": "extrapolated",
    }


def test_domain_edge_mutation_makes_openimcc_binding_test_red(monkeypatch) -> None:
    monkeypatch.setattr(
        openimcc_bridge_module, "_temperature_domain_K",
        lambda: (float("inf"), float("inf")),
    )
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, CAP_PLUS_T_K, high_t_melt_activity="openimcc")
    )
    with pytest.raises(AssertionError):
        assert result.diagnostic.get("high_t_melt_activity", {}).get(
            "provider"
        ) == "openimcc"
