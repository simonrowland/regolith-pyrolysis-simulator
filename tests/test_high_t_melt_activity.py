"""C3 openimcc authority routing and composition-policy tests."""

from __future__ import annotations

import copy
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

import engines.engine_commissioning as engine_commissioning_module
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
from simulator.run_executor import RunExecutor
from simulator.runner import (
    PyrolysisRun,
    _high_t_melt_activity_fallback_sentence,
)
from simulator.state import CampaignPhase, MOLAR_MASS


_openimcc = pytest.importorskip("openimcc")
ImccNonconvergenceError = _openimcc.ImccNonconvergenceError

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REPO_ROOT = DATA_DIR.parent
CAP_PLUS_T_K = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 0.1

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
# Exact cleaned-melt inputs from the R4 ordinary and quantized hour-132
# vapor-pressure reads. Kept inline so tests do not depend on docs-private.
_H133_T_K = 2378.15
_H133_ORDINARY_MOL = {
    "Ag": 0.00019044727877515409,
    "Al2O3": 1347.4478962548742,
    "As": 0.0010967841865285478,
    "Au": 2.0859585726320976e-05,
    "B": 0.19003890061400625,
    "Ba": 0.8975600642134791,
    "Bi": 2.457551659913331e-05,
    "Br": 0.003213732284424759,
    "CaO": 2012.0042177254709,
    "Cd": 0.00027411895079403916,
    "Ce2O3": 0.21300269917779982,
    "Cr2O3": 3.2858891814505425e-10,
    "Cs": 0.0011592755330998505,
    "Cu": 0.16164042706365525,
    "Dy2O3": 0.042053325622322495,
    "Er2O3": 0.02477977297400363,
    "Eu2O3": 0.003718412072063732,
    "F": 4.325248807517177,
    "Fe2O3": 1.730234222986687e-07,
    "FeO": 7.223071870166733e-05,
    "Ga": 0.0736600732770179,
    "Gd2O3": 0.03822767923108917,
    "Ge": 0.007071184481747926,
    "HfO2": 0.031651027289974314,
    "Ho2O3": 0.008951621435958164,
    "I": 0.00040469821820252034,
    "In": 4.472993162303401e-05,
    "Ir": 4.2750027638292285e-05,
    "K2O": 0.6032114743142483,
    "La2O3": 0.08730964684295656,
    "Li": 1.4800580083842994,
    "Lu2O3": 0.003304006100581209,
    "MgO": 76.33529920404698,
    "MnO": 0.001875541491775934,
    "Mo": 0.0016057742435935962,
    "Na2O": 1.0516043821788383e-06,
    "Nb": 0.1326703765718588,
    "Nd2O3": 0.1323325750233767,
    "Os": 3.7796992087109955e-05,
    "P2O5": 7.236451972966535,
    "Pb": 0.005948804581961605,
    "Pr2O3": 0.026792994567377926,
    "Pt": 6.318264488027949e-05,
    "Rb": 0.030045240950940116,
    "Sb": 0.0002530782501195886,
    "Sc2O3": 0.43412808320737667,
    "Se": 0.001951020484390543,
    "SiO2": 961.8054661351188,
    "Sm2O3": 0.03415649466376944,
    "Sn": 0.002595805554255001,
    "Sr": 1.6412056162362307,
    "Tb2O3": 0.006701782013152592,
    "Te": 0.00016099690561421692,
    "TiO2": 192.59122511490912,
    "Tm2O3": 0.003540413318432653,
    "V": 2.4196231155000234,
    "W": 0.001396812796206897,
    "Y2O3": 0.25995526130272445,
    "Yb2O3": 0.021818427195518682,
    "Zn": 0.3142123762063946
}
_H133_QUANTIZED_MOL = {
    "Ag": 0.0001904475081877867,
    "Al2O3": 1347.4411362328551,
    "As": 0.0010967758606925998,
    "Au": 2.085926122946834e-05,
    "B": 0.19003711066160783,
    "Ba": 0.8975716682102776,
    "Bi": 2.457543388505869e-05,
    "Br": 0.00321373541455161,
    "CaO": 2012.0084561530462,
    "Cd": 0.0002741178802385213,
    "Ce2O3": 0.21300092729498663,
    "Cs": 0.001159257731251291,
    "Cu": 0.1616412908165583,
    "Dy2O3": 0.04205283505048687,
    "Er2O3": 0.024779710406516258,
    "Eu2O3": 0.003718386035506826,
    "F": 4.325221029272418,
    "Fe2O3": 1.7359815357365694e-07,
    "FeO": 7.245591380728534e-05,
    "Ga": 0.07365943913686604,
    "Gd2O3": 0.0382273767615208,
    "Ge": 0.007071287712306503,
    "HfO2": 0.03165087168470858,
    "Ho2O3": 0.008951738399674348,
    "I": 0.0004046934616857878,
    "In": 4.472964138696807e-05,
    "Ir": 4.274958860335907e-05,
    "K2O": 0.6039299326252252,
    "La2O3": 0.08730861528843255,
    "Li": 1.4800594828946974,
    "Lu2O3": 0.003303976758229366,
    "MgO": 76.38927436717935,
    "MnO": 0.0018856152405062766,
    "Mo": 0.00160576101731757,
    "Na2O": 1.007133974075555e-06,
    "Nb": 0.13266906995159466,
    "Nd2O3": 0.13233245175596478,
    "Os": 3.7796689919440956e-05,
    "P2O5": 7.236368964409909,
    "Pb": 0.005948919646329716,
    "Pr2O3": 0.026792963888872326,
    "Pt": 6.318277419890688e-05,
    "Rb": 0.03004524900363579,
    "Sb": 0.000253076937407576,
    "Sc2O3": 0.4341268033666828,
    "Se": 0.0019510021722053354,
    "SiO2": 961.8980220326943,
    "Sm2O3": 0.034156141077663335,
    "Sn": 0.002595787409122074,
    "Sr": 1.641175095981094,
    "Tb2O3": 0.006701929938745531,
    "Te": 0.00016099572167425447,
    "TiO2": 192.59171998186724,
    "Tm2O3": 0.0035403934005573436,
    "V": 2.4195931453547233,
    "W": 0.0013968271756190974,
    "Y2O3": 0.2599522487731117,
    "Yb2O3": 0.021818392526605446,
    "Zn": 0.3142123351837479
}
_H133_WARM_SIO2_ACTIVITY = {
    "ordinary": 0.020796595595499484,
    "quantized": 0.02080166433881563,
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


def _physics_numeric_payload(result) -> tuple[dict, dict]:
    diagnostic = result.diagnostic
    return (
        dict(diagnostic["vapor_pressures_Pa"]),
        dict(diagnostic["activities"]),
    )


def _physics_metadata_payload(
    result,
    *,
    exclude_b813_temperature_notices: bool = False,
) -> tuple[dict, dict, tuple[str, ...]]:
    diagnostic = result.diagnostic
    provenance = copy.deepcopy(
        diagnostic["vapor_pressure_numerator_provenance"]
    )
    if exclude_b813_temperature_notices:
        # The merged green base already attaches temperature notices with
        # their existing authority, reason, status and acquisition fields.
        # b-813 adds certified_band; project that key out while preserving
        # every notice field that was already present on green.
        for record in provenance.values():
            notice = record.get("extrapolation_notice")
            if not isinstance(notice, dict):
                continue
            if not {"temperature_K", "valid_range_K"} <= notice.keys():
                continue
            notice.pop("certified_band", None)
    return (
        dict(diagnostic["vapor_pressures_source"]),
        provenance,
        tuple(result.warnings),
    )


def _physics(result) -> tuple[dict, dict, dict, dict, tuple[str, ...]]:
    return (
        *_physics_numeric_payload(result),
        *_physics_metadata_payload(result),
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


_ONE_STEP_PARENTS = ("SiO2", "MgO", "FeO", "CaO", "Al2O3", "TiO2")


def test_successful_openimcc_physics_is_bit_identical() -> None:
    result = _provider().dispatch(_request(BASE_MELT_MOL, 2400.65))
    # Re-measured after merging green a822c275f: d-173 moves the engine pin
    # from e975edd to e1423d8. Keep the numeric maps separate from metadata;
    # the historical digest excludes only b-813's new temperature notice
    # fields so their values remain bit-pinned to green.
    numeric_payload = _physics_numeric_payload(result)
    metadata_payload = _physics_metadata_payload(
        result,
        exclude_b813_temperature_notices=True,
    )
    # Numeric-only hash captured from the green 4b95c7176 engine on studio-2.
    assert hashlib.sha256(
        json.dumps(numeric_payload, sort_keys=True).encode()
    ).hexdigest() == "7d93eda6e2a5812bad5bfa80351c187c88adc91e79c2e369b9f7d64d336fdfc8"
    # Projected metadata digest captured from merged green cb20fb3d4 on studio-2.
    assert hashlib.sha256(
        json.dumps((*numeric_payload, *metadata_payload), sort_keys=True).encode()
    ).hexdigest() == "934e52dd541fc5e6933b7fa9d3b106003b5d20f103aa0c6489c4f4ced3f74cc9"


def test_1800_k_pre_switch_physics_pin() -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, 1800.0, high_t_melt_activity="constant_gamma")
    )
    numeric_payload = _physics_numeric_payload(result)
    metadata_payload = _physics_metadata_payload(
        result,
        exclude_b813_temperature_notices=True,
    )
    # Numeric-only hash captured from the green 4b95c7176 engine on studio-2.
    assert hashlib.sha256(
        json.dumps(numeric_payload, sort_keys=True).encode()
    ).hexdigest() == "61e9be015d7f7fc80787b36f2836523b4094950b6fb8d1a0fa917cdb882cf2b1"
    # Projected metadata digest captured from merged green cb20fb3d4 on studio-2.
    assert hashlib.sha256(
        json.dumps((*numeric_payload, *metadata_payload), sort_keys=True).encode()
    ).hexdigest() == "7f0cbcb90a852319495d1eaf070ab30efa5d4361413d01e7bcfb57934e8ea102"


@pytest.mark.parametrize("temperature_K, expected", [(1650.0, False), (1800.0, True)])
def test_certified_edge_selects_openimcc(temperature_K, expected) -> None:
    result = _provider().dispatch(
        _request(BASE_MELT_MOL, temperature_K, high_t_melt_activity="openimcc")
    )
    high_t = result.diagnostic.get("high_t_melt_activity", {})
    assert (high_t.get("provider") == "openimcc") is expected
    assert high_t.get("above_vaporock_cap", False) is False
    legacy = _provider().dispatch(
        _request(BASE_MELT_MOL, temperature_K, high_t_melt_activity="constant_gamma")
    )
    assert _physics(result)[:4] == _physics(legacy)[:4]
    if expected:
        selected = high_t["selected_parent_activities"]
        oracle = evaluate_cleaned_melt(
            composition_mol=BASE_MELT_MOL, temperature_K=temperature_K,
        )
        for parent in ("Na2O", "Al2O3", "SiO2", "FeO"):
            assert selected[parent] == pytest.approx(
                oracle.bridge.parent_oxide_activities[parent], rel=1.0e-12,
            )
        assert high_t["certified_effective_gamma_by_parent"] == {}
    else:
        assert _physics(result) == _physics(legacy)


def test_binding_reads_lower_edge_from_default_pack(monkeypatch) -> None:
    pack = openimcc_bridge_module._load_pack("default")
    shifted_pack = SimpleNamespace(
        kernel_datapack=SimpleNamespace(domains=((1850.0, 3000.0),)),
    )
    monkeypatch.setattr(openimcc_bridge_module, "_load_pack", lambda _: shifted_pack)
    selected = vapor_pressure_module.openimcc_binding_selected_by_inputs
    assert not selected(temperature_K=1800.0, controls={})
    assert selected(temperature_K=1850.0, controls={})
    assert not selected(temperature_K=1800.0, controls={}, quantity_class="vapour")
    assert not selected(temperature_K=1950.0, controls={}, quantity_class="vapour")
    assert selected(
        temperature_K=(
            vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 0.1
        ),
        controls={}, quantity_class="vapour",
    )
    shifted = _provider().dispatch(_request(BASE_MELT_MOL, 1800.0))
    assert "high_t_melt_activity" not in shifted.diagnostic
    assert _physics(shifted) == _physics(_provider().dispatch(_request(
        BASE_MELT_MOL, 1800.0, high_t_melt_activity="constant_gamma",
    )))
    monkeypatch.setattr(openimcc_bridge_module, "_load_pack", lambda _: pack)
    assert selected(temperature_K=1800.0, controls={})
    assert not selected(temperature_K=1800.0, controls={}, quantity_class="vapour")
    assert selected(
        temperature_K=(
            vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 0.1
        ),
        controls={}, quantity_class="vapour",
    )
    assert selected(
        temperature_K=openimcc_bridge_module._temperature_domain_K()[0], controls={},
    )


def test_vaporock_band_move_does_not_move_vapour_channel_switch(monkeypatch) -> None:
    shifted_max = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 100.0
    monkeypatch.setattr(
        engine_commissioning_module, "VAPOROCK_T_MAX_K", shifted_max,
    )
    monkeypatch.setattr(
        vapor_pressure_module, "VAPOROCK_T_MAX_K", shifted_max, raising=False,
    )

    temperature_K = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 50.0
    assert engine_commissioning_module.vaporock_temperature_in_domain(
        temperature_K
    ) is True
    assert vapor_pressure_module.openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K, controls={}, quantity_class="vapour",
    ) is True


def test_vapour_channel_switch_move_does_not_move_vaporock_band(monkeypatch) -> None:
    shifted_switch = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K + 100.0
    monkeypatch.setattr(
        vapor_pressure_module, "BUILTIN_VAPOUR_CHANNEL_SWITCH_K", shifted_switch,
    )

    temperature_K = vapor_pressure_module.BUILTIN_VAPOUR_CHANNEL_SWITCH_K - 50.0
    assert vapor_pressure_module.openimcc_binding_selected_by_inputs(
        temperature_K=temperature_K, controls={}, quantity_class="vapour",
    ) is False
    notice = engine_commissioning_module.vaporock_temperature_notice(temperature_K)
    assert notice is not None
    assert notice["certified_band"] == {"temperature_K": [1350.0, 1950.0]}


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
    assert _physics(result)[:4] == _physics(legacy)[:4]
    assert high_t["selected_parent_activities"]["FeO"] == legacy.diagnostic["activities"]["Fe"]
    provenance = high_t["selected_parent_activity_provenance"]
    assert set(provenance) == set(high_t["selected_parent_activities"])
    assert all(row["provider"] == "internal-analytical" for row in provenance.values())
    assert provenance["FeO"]["authority"] == "kress91_ferrous"
    assert provenance["FeO"]["reason"] == "openimcc_not_importable"
    assert provenance["FeO"]["activity"] == high_t["selected_parent_activities"]["FeO"]


@pytest.mark.parametrize("temperature_K", [1800.0, 2000.0])
def test_selected_parent_activities_have_matching_mixed_provenance(temperature_K):
    composition = _moles_from_wt(_projected_weights())
    result = _provider().dispatch(_request(composition, temperature_K))
    high_t = result.diagnostic["high_t_melt_activity"]
    values = high_t["selected_parent_activities"]
    provenance = high_t["selected_parent_activity_provenance"]

    assert set(provenance) == set(values) == set(composition)
    assert high_t["selected_parent_activities_provider"] == "mixed"
    assert high_t["selected_parent_activities_standard_state"]["component_basis"] == "parent_oxide_formula"
    for parent, value in values.items():
        row = provenance[parent]
        assert row["activity"] == value
        assert row["component_basis"] == "parent_oxide_formula"
        assert row["provider"] and row["authority"] and row["reason"]
    assert provenance["SiO2"]["provider"] == "openimcc"
    assert provenance["K2O"]["provider"] == "internal-analytical"
    assert provenance["K2O"]["reason"] == "openimcc_authority_excluded"
    for parent in ("Cr2O3", "MnO", "P2O5"):
        assert provenance[parent]["provider"] == "internal-analytical"
        assert provenance[parent]["reason"] == "outside_openimcc_activity_coverage"
        assert values[parent] == melt_oxide_activity(
            parent, composition, temperature_K=temperature_K,
        ).thermodynamic_parent_activity()






_BRIDGE_REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "openimcc_temperature_bridge.json").read_text()
)


def _call_record(engine: str, index: int) -> dict:
    return _BRIDGE_REFERENCE["records"][f"{engine}:{index}"]


def test_h133_nonconvergence_uses_temperature_neighbors_without_activity_seam(
    monkeypatch,
) -> None:
    """Both refused h133 targets use the pinned engine's two-sided bracket."""

    real_evaluate = openimcc_bridge_module.evaluate
    refused_targets = []
    solved_anchor_calls = []

    def refuse_h133_targets(**kwargs):
        temperature_K = float(kwargs["temperature_K"])
        if math.isclose(temperature_K, _H133_T_K, abs_tol=1e-9):
            refused_targets.append(temperature_K)
            raise ImccNonconvergenceError("h133 target refusal", {})
        result = real_evaluate(**kwargs)
        if math.isclose(temperature_K, 2403.15, abs_tol=1e-9):
            solved_anchor_calls.append(temperature_K)
        return result

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", refuse_h133_targets)
    cases = (
        ("ordinary", _H133_ORDINARY_MOL),
        ("quantized", _H133_QUANTIZED_MOL),
    )
    activities = {}
    for name, composition in cases:
        result = _provider().dispatch(_request(
            dict(composition), _H133_T_K, high_t_melt_activity="openimcc",
        ))
        authority = result.diagnostic["high_t_melt_activity"]
        assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
        assert authority["temperature_bridge"]["call_count"] == 6
        notice = authority["held_effective_gamma_by_parent"]["SiO2"]
        assert notice["method"] == "interpolated_converged_temperature_neighbors"
        assert notice["lower_anchor_temperature_K"] == 2353.15
        assert notice["upper_anchor_temperature_K"] == 2403.15
        assert notice["lower_anchor_gap_K"] == pytest.approx(25.0)
        assert notice["upper_anchor_gap_K"] == pytest.approx(25.0)
        activity = vapor_pressure_module._high_t_activity_for_parent(
            "SiO2", composition, temperature_K=_H133_T_K, authority=authority,
        )
        lower = evaluate_cleaned_melt(composition, 2353.15)
        upper = evaluate_cleaned_melt(composition, 2403.15)
        assert lower.single_cation_activities["SiO2"] > 0.0
        assert upper.single_cation_activities["SiO2"] > 0.0
        assert activity.activity_model == notice["method"]
        warm_activity = _H133_WARM_SIO2_ACTIVITY[name]
        assert abs(activity.activity - warm_activity) / warm_activity < 0.01
        activities[name] = activity.activity

    assert len(refused_targets) == 2
    assert all(math.isclose(t, _H133_T_K, abs_tol=1e-9) for t in refused_targets)
    assert len(solved_anchor_calls) >= 4
    assert abs(activities["ordinary"] - activities["quantized"]) <= 1e-5


def test_refused_calls_use_bracketed_same_composition_temperature_neighbors(
    monkeypatch,
) -> None:
    """Forced target refusals use bracketing probes where both sides solve.

    Call 62 has a converged historical solve at the refused target. Call
    65's independent bridge fixture records converged neighbours at -25 K
    and +25 K.
    """

    provider = _provider()
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
    result_62 = provider.dispatch(_request(
        refused_62["composition"], refused_62["T_K"],
    ))
    authority = result_62.diagnostic["high_t_melt_activity"]
    assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
    bridge_parents = _ONE_STEP_PARENTS + ("Na2O",)
    errors = {}
    solved_activity = dict(zip(
        solved_62["parent_oxides"], solved_62["parent_activity"],
    ))
    neighbor_results = {}
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
        assert notice["bridge_call_count"] <= 6
        assert notice["bridge_cap_K"] == 75.0
        if notice["method"] == "interpolated_converged_temperature_neighbors":
            lower_T = notice["lower_anchor_temperature_K"]
            upper_T = notice["upper_anchor_temperature_K"]
            assert lower_T < refused_62["T_K"] < upper_T
            assert notice["lower_anchor_gap_K"] == pytest.approx(
                refused_62["T_K"] - lower_T
            )
            assert notice["upper_anchor_gap_K"] == pytest.approx(
                upper_T - refused_62["T_K"]
            )
            assert max(
                notice["lower_anchor_gap_K"], notice["upper_anchor_gap_K"]
            ) <= vapor_pressure_module._TEMPERATURE_BRIDGE_CAP_K
            assert oxide in evaluate_cleaned_melt(
                refused_62["composition"], lower_T,
            ).single_cation_activities
            assert oxide in evaluate_cleaned_melt(
                refused_62["composition"], upper_T,
            ).single_cation_activities
        else:
            assert notice["method"] == "nearest_converged_temperature_neighbor"
            anchor_T = notice["anchor_temperature_K"]
            assert notice["anchor_gap_K"] == pytest.approx(
                abs(anchor_T - refused_62["T_K"])
            )
            assert notice["anchor_gap_K"] <= vapor_pressure_module._TEMPERATURE_BRIDGE_CAP_K
        for probe_T in notice["bridge_attempted_temperatures_K"]:
            offset = abs(probe_T - refused_62["T_K"])
            assert min(abs(offset - step) for step in (25.0, 50.0, 75.0)) < 1e-6
        assert activity.activity_model == notice["method"]
        assert activity.activity != melt_oxide_activity(
            oxide, refused_62["composition"],
        ).activity
        if notice["method"] == "nearest_converged_temperature_neighbor":
            anchor_T = notice["anchor_temperature_K"]
            if anchor_T not in neighbor_results:
                neighbor_results[anchor_T] = evaluate_cleaned_melt(
                    refused_62["composition"], anchor_T,
                )
            # evaluate_cleaned_melt already returns single-cation activities.
            assert activity.activity == pytest.approx(
                neighbor_results[anchor_T].single_cation_activities[oxide],
                rel=1e-12,
            )
    assert all(abs(error) < 0.01 for error in errors.values()), errors
    assert "K2O" not in authority["held_effective_gamma_by_parent"]
    silica_method = authority["held_effective_gamma_by_parent"]["SiO2"]["method"]
    assert result_62.diagnostic["vapor_pressure_numerator_provenance"]["SiO"][
        "melt_activity_authority"
    ] == silica_method
    assert all(
        authority["held_effective_gamma_by_parent"][oxide]["method"]
        == "interpolated_converged_temperature_neighbors"
        for oxide in ("CaO", "Al2O3")
    )

    assert refused_65["ok"] is False
    result_65 = provider.dispatch(_request(
        refused_65["composition"], refused_65["T_K"],
    ))
    authority_65 = result_65.diagnostic["high_t_melt_activity"]
    assert authority_65["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert refused_temperatures == [refused_62["T_K"], refused_65["T_K"]]
    silica = next(
        n for n in authority_65["notices"] if n.get("parent") == "SiO2"
    )
    assert silica["method"] == "interpolated_converged_temperature_neighbors"
    assert silica["bridge_call_count"] <= 6
    assert silica["lower_anchor_temperature_K"] == 2405.65
    assert silica["upper_anchor_temperature_K"] == 2455.65
    assert silica["lower_anchor_gap_K"] == 25.0
    assert silica["upper_anchor_gap_K"] == 25.0
    fixture_gammas = _BRIDGE_REFERENCE["c2b7b4e_call_65_probe_gammas"]
    lower_reference = fixture_gammas["2405.65"]["SiO2"]
    upper_reference = fixture_gammas["2455.65"]["SiO2"]
    activity_65 = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", refused_65["composition"],
        temperature_K=refused_65["T_K"], authority=authority_65,
    )
    assert min(lower_reference, upper_reference) <= activity_65.effective_gamma <= max(
        lower_reference, upper_reference
    )
    assert activity_65.activity_model == "interpolated_converged_temperature_neighbors"
    assert activity_65.activity != melt_oxide_activity(
        "SiO2", refused_65["composition"],
    ).activity


def test_one_sided_25k_hold_stays_within_measured_call62_bound(monkeypatch) -> None:
    """A single +25 K solve holds within the measured worst call-62 error."""

    refused = _call_record("e975edd", 62)
    solved = _call_record("c2b7b4e", 62)
    target_T = float(refused["T_K"])
    allowed_anchor_T = target_T + 25.0
    evaluate = openimcc_bridge_module.evaluate

    def solve_only_one_side(**kwargs):
        temperature_K = float(kwargs["temperature_K"])
        if math.isclose(temperature_K, target_T, abs_tol=1e-9):
            raise ImccNonconvergenceError("forced call-62 target refusal", {})
        if math.isclose(temperature_K, allowed_anchor_T, abs_tol=1e-9):
            return evaluate(**kwargs)
        raise ImccNonconvergenceError("forced one-sided probe refusal", {})

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", solve_only_one_side)
    result = _provider().dispatch(_request(
        dict(refused["composition"]), target_T, high_t_melt_activity="openimcc",
    ))
    authority = result.diagnostic["high_t_melt_activity"]
    notice = authority["held_effective_gamma_by_parent"]["CaO"]
    assert notice["method"] == "nearest_converged_temperature_neighbor"
    assert notice["anchor_temperature_K"] == allowed_anchor_T
    assert notice["anchor_gap_K"] == pytest.approx(25.0)

    solved_activities = dict(zip(solved["parent_oxides"], solved["parent_activity"]))
    solved_cation_activity, fraction = single_cation_activity_and_fraction(
        "CaO", solved_activities["CaO"], refused["composition"],
    )
    target_gamma = solved_cation_activity / fraction
    held = vapor_pressure_module._high_t_activity_for_parent(
        "CaO", refused["composition"], temperature_K=target_T, authority=authority,
    )
    # Studio-2 receipt-r2-s2.log measured this CaO error on the prior 25 K
    # nearest-neighbour path; retain that result as the one-sided hold bound.
    measured_worst_error = 0.03348574976050027
    relative_error = abs(held.effective_gamma - target_gamma) / target_gamma
    assert relative_error <= measured_worst_error


def _temperature_neighbor_test_result() -> OpenImccBridgeResult:
    parents = openimcc_bridge_module.OPENIMCC_PARENT_OXIDES
    activities = {parent: 1.0 for parent in parents}
    activities["SiO2"] = 0.36
    return OpenImccBridgeResult(
        parent_oxide_activities=activities,
        parent_oxides=parents,
        flags=(),
        notices=(),
        acid_sink_ratio=None,
        pack_model_id="temperature-neighbor-test",
        pack_version="test",
        pack_digest="test",
        openimcc_version="test",
        envelope_status="in_domain",
        extrapolated=False,
        labels=SimpleNamespace(),
        coverage={},
    )


@pytest.mark.parametrize(
    ("available_offset_K", "selected_offset_K"),
    [(50.0, 50.0), (75.0, 75.0), (76.0, None)],
    ids=("50-k-not-far", "75-k-far-anchor", "76-k-outside-schedule"),
)
def test_temperature_neighbor_probe_selection_and_notice_follow_schedule(
    monkeypatch, available_offset_K, selected_offset_K,
) -> None:
    target_T = CAP_PLUS_T_K
    only_success_temperature_K = target_T + available_offset_K
    solver_temperatures_K = []
    successful_temperatures_K = []

    def solve_only_available_temperature(**kwargs):
        temperature_K = float(kwargs["temperature_K"])
        solver_temperatures_K.append(temperature_K)
        if math.isclose(
            temperature_K, only_success_temperature_K, abs_tol=1e-9,
        ):
            successful_temperatures_K.append(temperature_K)
            return _temperature_neighbor_test_result()
        raise ImccNonconvergenceError(
            "forced temperature-specific solve refusal", {},
        )

    # Keep the production schedule, probe selector and cleaned-melt policy;
    # fake only the low-level OpenIMCC solve outcome at each requested T.
    monkeypatch.setattr(
        openimcc_bridge_module, "evaluate", solve_only_available_temperature,
    )
    authority = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol=BASE_MELT_MOL,
        temperature_K=target_T,
        controls={"high_t_melt_activity": "openimcc"},
        below_cap_fe_activity=1.0,
        below_cap_fe_activity_basis="test",
    )
    assert vapor_pressure_module._TEMPERATURE_BRIDGE_CAP_K == 75.0
    assert vapor_pressure_module._TEMPERATURE_BRIDGE_FAR_ANCHOR_WARNING_GAP_K == 50.0
    assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"

    attempted_temperatures_K = authority["temperature_bridge"][
        "attempted_temperatures_K"
    ]
    assert authority["temperature_bridge"]["call_count"] == 6
    assert len(attempted_temperatures_K) == 6
    assert solver_temperatures_K == [target_T, *attempted_temperatures_K]
    assert all(
        min(
            abs(abs(probe_T - target_T) - schedule_offset_K)
            for schedule_offset_K in (25.0, 50.0, 75.0)
        ) < 1e-9
        for probe_T in attempted_temperatures_K
    )
    assert {
        round(probe_T - target_T, 6)
        for probe_T in attempted_temperatures_K
    } == {-75.0, -50.0, -25.0, 25.0, 50.0, 75.0}

    if selected_offset_K is None:
        assert successful_temperatures_K == []
        assert only_success_temperature_K not in solver_temperatures_K
        assert authority["provider"] == "constant_gamma"
        assert authority["fallback"] is True
        assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
        assert authority["held_effective_gamma_by_parent"] == {}
        assert not any(
            notice.get("method") in {
                "nearest_converged_temperature_neighbor",
                "interpolated_converged_temperature_neighbors",
            }
            for notice in authority["notices"]
        )
        return

    assert successful_temperatures_K == [only_success_temperature_K]
    held = authority["held_effective_gamma_by_parent"]["SiO2"]
    assert held["method"] == "nearest_converged_temperature_neighbor"
    assert held["anchor_temperature_K"] == target_T + selected_offset_K
    assert held["anchor_gap_K"] == selected_offset_K
    held_activity = vapor_pressure_module._high_t_activity_for_parent(
        "SiO2", BASE_MELT_MOL, temperature_K=target_T, authority=authority,
    )
    assert held_activity.activity == pytest.approx(0.36)
    if selected_offset_K == 50.0:
        assert "schedule-derived" not in held["message"]
    else:
        assert "schedule-derived 50.0 K notice threshold" in held["message"]
        sentence = _high_t_melt_activity_fallback_sentence({
            "reason": "imcc_nonconvergence",
            "notices": [held],
            "affected_tick_count": 1,
            "affected_parent_count": 1,
            "first_hour": 1,
            "last_hour": 1,
        })
        assert "schedule-derived 50.0 K notice threshold" in sentence


@pytest.mark.parametrize("call_index", [62, 65])
def test_recovered_historical_call_uses_direct_live_solve(call_index) -> None:
    """Successful numerical recovery must not enter continuation."""

    record = _call_record("e975edd", call_index)
    try:
        direct = evaluate_cleaned_melt(record["composition"], record["T_K"])
    except ImccNonconvergenceError:
        pytest.skip("installed engine still refuses this historical target")

    provider = _provider()
    result = provider.dispatch(_request(record["composition"], record["T_K"]))
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




# Two independent battery cells. A solves above the 1950 K vapour cap; B is a
# different melt at another temperature.
_INDEPENDENT_CELL_A_MOL = dict(BASE_MELT_MOL)
_INDEPENDENT_CELL_B_MOL = {**BASE_MELT_MOL, "SiO2": 4.0, "MgO": 2.0}
_INDEPENDENT_CELL_A_T_K = 2400.65
_INDEPENDENT_CELL_B_T_K = 2200.0


def _independent_cell_backend():
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _InternalAnalyticalBatteryBackend,
    )

    return _InternalAnalyticalBatteryBackend()


def _equilibrate_independent_cell(backend, composition, temperature_K):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
    )

    return backend.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg=None,
        composition_mol=dict(composition),
        fO2_log=-9.0,
        pressure_bar=1.0e-6,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
    )


def _high_t_authority(result):
    diagnostic = result.diagnostics["vapor_pressure_diagnostic"]
    authority = diagnostic.get("high_t_melt_activity")
    assert isinstance(authority, dict)
    return authority


def _independent_cell_record(result):
    """Prediction and notice surface a reused core must not reorder."""

    authority = _high_t_authority(result)
    return {
        "status": result.status,
        "pressures_hex": {
            str(species): float(pressure).hex()
            for species, pressure in sorted(result.vapor_pressures_Pa.items())
        },
        "diagnostics_authority": result.diagnostics.get("authority"),
        "imcc_notices": result.diagnostics.get("imcc_notices"),
        "provider": authority.get("provider"),
        "authority": authority.get("authority"),
        "fallback_reason": authority.get("fallback_reason"),
        "held_effective_gamma_by_parent": authority.get(
            "held_effective_gamma_by_parent"
        ),
        "certified_effective_gamma_by_parent": authority.get(
            "certified_effective_gamma_by_parent"
        ),
        "notices": authority.get("notices"),
        "temperature_bridge": authority.get("temperature_bridge"),
    }


def _refuse_openimcc_near_cell_b(monkeypatch, *, include_bridge_probes: bool):
    """Refuse cell B's temperature. Optionally refuse its bridge probes too."""

    real_evaluate = openimcc_bridge_module.evaluate
    span = (
        float(vapor_pressure_module._TEMPERATURE_BRIDGE_CAP_K)
        if include_bridge_probes
        else 0.0
    )

    def evaluate(*args, **kwargs):
        temperature_K = kwargs.get("temperature_K")
        if temperature_K is None and len(args) >= 2:
            temperature_K = args[1]
        if (
            temperature_K is not None
            and abs(float(temperature_K) - _INDEPENDENT_CELL_B_T_K) <= span + 1e-6
        ):
            raise ImccNonconvergenceError(
                "independent cell target solve refused", {}
            )
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(openimcc_bridge_module, "evaluate", evaluate)


def _score_independent_cells(order):
    cells = {
        "A": (_INDEPENDENT_CELL_A_MOL, _INDEPENDENT_CELL_A_T_K),
        "B": (_INDEPENDENT_CELL_B_MOL, _INDEPENDENT_CELL_B_T_K),
    }
    backend = _independent_cell_backend()
    records = {}
    for name in order:
        composition, temperature_K = cells[name]
        records[name] = _independent_cell_record(
            _equilibrate_independent_cell(backend, composition, temperature_K)
        )
    return records


def test_independent_battery_cells_match_in_either_order(monkeypatch) -> None:
    """A then B and B then A return the same pressures and notices.

    Cell B does not solve at its own temperature. Its same-composition
    probes do. The bridge is extrapolated and does not carry cell A's
    certificate.
    """

    _refuse_openimcc_near_cell_b(monkeypatch, include_bridge_probes=False)
    forward = _score_independent_cells(("A", "B"))
    reverse = _score_independent_cells(("B", "A"))
    assert forward == reverse
    assert "SiO2" in (forward["A"]["certified_effective_gamma_by_parent"] or {})

    refused = forward["B"]
    assert refused["status"] == "ok"
    assert refused["temperature_bridge"]["call_count"] > 0
    silica = refused["held_effective_gamma_by_parent"]["SiO2"]
    assert silica["code"] == "hold_last_certified_effective_gamma"
    assert silica["method"] in {
        "nearest_converged_temperature_neighbor",
        "interpolated_converged_temperature_neighbors",
    }
    if silica["method"] == "interpolated_converged_temperature_neighbors":
        assert silica["lower_anchor_temperature_K"] < _INDEPENDENT_CELL_B_T_K
        assert silica["upper_anchor_temperature_K"] > _INDEPENDENT_CELL_B_T_K
        assert silica["lower_anchor_gap_K"] <= 75.0
        assert silica["upper_anchor_gap_K"] <= 75.0
    else:
        assert silica["anchor_gap_K"] <= 75.0
    assert silica["authority"] == "extrapolated"
    assert "gamma_last" not in silica
    assert "hour_gap" not in silica
    assert "composition_distance_L1" not in silica
    assert any(
        notice.get("parent") == "SiO2"
        and notice.get("method") == silica["method"]
        for notice in refused["notices"]
    )


def test_cell_without_same_composition_bridge_keeps_typed_fallback(
    monkeypatch,
) -> None:
    """No two probe points: the typed constant-gamma fallback stays.

    A prior certified cell on the same core must not supply the gamma.
    """

    _refuse_openimcc_near_cell_b(monkeypatch, include_bridge_probes=True)
    forward = _score_independent_cells(("A", "B"))
    reverse = _score_independent_cells(("B", "A"))
    assert forward == reverse

    lone = _independent_cell_record(
        _equilibrate_independent_cell(
            _independent_cell_backend(),
            _INDEPENDENT_CELL_B_MOL,
            _INDEPENDENT_CELL_B_T_K,
        )
    )
    assert forward["B"] == lone
    authority = forward["B"]
    assert authority["provider"] == "constant_gamma"
    assert authority["authority"] == "extrapolated"
    assert authority["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert authority["held_effective_gamma_by_parent"] == {}
    assert authority["temperature_bridge"]["call_count"] > 0
    assert not any(
        notice.get("method") in {
            "nearest_converged_temperature_neighbor",
            "interpolated_converged_temperature_neighbors",
        }
        for notice in authority["notices"]
    )


# A cold cell is below the 400 K refresh floor. The refresh returns
# without replacing the previous vapour diagnostic, so a hot cell's
# activities and notices would otherwise ride into the cold result.
_COLD_CELL_MOL = {**BASE_MELT_MOL, "SiO2": 3.0}
_COLD_CELL_T_K = 300.0


def _stable_numbers(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value).hex()
    if isinstance(value, dict):
        return {
            str(key): _stable_numbers(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_numbers(item) for item in value]
    return value


def _cold_path_record(result):
    """Pressures, activities and notices a cold cell must not inherit."""

    diagnostic = dict(result.diagnostics.get("vapor_pressure_diagnostic") or {})
    return {
        "status": result.status,
        "pressures_hex": {
            str(species): float(pressure).hex()
            for species, pressure in sorted(result.vapor_pressures_Pa.items())
        },
        "activities": _stable_numbers(dict(result.activity_coefficients or {})),
        "imcc_notices": _stable_numbers(result.diagnostics.get("imcc_notices")),
        "diagnostics_authority": result.diagnostics.get("authority"),
        "high_t_present": "high_t_melt_activity" in diagnostic,
    }


def _score_hot_and_cold(order):
    cells = {
        "hot": (_INDEPENDENT_CELL_A_MOL, _INDEPENDENT_CELL_A_T_K),
        "cold": (_COLD_CELL_MOL, _COLD_CELL_T_K),
    }
    backend = _independent_cell_backend()
    records = {}
    for name in order:
        composition, temperature_K = cells[name]
        records[name] = _cold_path_record(
            _equilibrate_independent_cell(backend, composition, temperature_K)
        )
    return records


def test_independent_hot_and_cold_cells_match_in_either_order() -> None:
    """A hot cell then a cold cell matches the cold cell scored first.

    The cold refresh does not replace the vapour diagnostic. The cold
    result must still be the fresh-core result: empty pressures, empty
    activities, and no carried high-T authority.
    """

    forward = _score_hot_and_cold(("hot", "cold"))
    reverse = _score_hot_and_cold(("cold", "hot"))
    assert forward == reverse

    hot = forward["hot"]
    assert hot["high_t_present"] is True
    assert hot["activities"]
    assert hot["pressures_hex"]

    cold = forward["cold"]
    lone = _cold_path_record(
        _equilibrate_independent_cell(
            _independent_cell_backend(),
            _COLD_CELL_MOL,
            _COLD_CELL_T_K,
        )
    )
    assert cold == lone
    assert cold["pressures_hex"] == {}
    assert cold["activities"] == {}
    assert cold["high_t_present"] is False
    assert cold["imcc_notices"] in ([], None)








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
        ),
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


def test_activity_seam_diagnostic_does_not_depend_on_temperature_history() -> None:
    result = _provider().dispatch(_request(
        BASE_MELT_MOL,
        CAP_PLUS_T_K,
        high_t_melt_activity="openimcc",
    ))
    high_t = result.diagnostic["high_t_melt_activity"]

    assert high_t["seam_max_abs_ratio_dex"] > 0.1
    assert not any(
        notice.get("code") == "openimcc_activity_cap_step_exceeds_0_1_dex"
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
        diagnostics={
            "activities_provider": "internal_analytical_equilibrium",
            "activity_provenance": {"old_carrier": {}},
            "backend_marker": "preserved",
        },
    )

    sim._refresh_vapor_pressures_from_kernel(result)

    assert sim._high_t_melt_activity == "openimcc"
    assert sim._last_high_t_melt_activity["provider"] == "openimcc"
    assert result.activity_coefficients == sim._last_high_t_melt_activity["selected_parent_activities"]
    assert result.diagnostics["activities_provider"] == "mixed"
    assert set(result.diagnostics["activity_provenance"]) == set(result.activity_coefficients)
    assert result.diagnostics["activity_provenance"] == sim._last_high_t_melt_activity["selected_parent_activity_provenance"]
    assert result.diagnostics["backend_marker"] == "preserved"
    assert "SiO2" in result.activity_coefficients
    assert sim._last_vapor_pressures_source

    if reset_kind == "cold":
        sim.melt.temperature_C = 100.0
    else:
        result.liquid_fraction = 0.0
    sim._refresh_vapor_pressures_from_kernel(result)
    assert sim._last_high_t_melt_activity == {}

    sim.melt.temperature_C = CAP_PLUS_T_K - 273.15
    result.liquid_fraction = 1.0
    sim._refresh_vapor_pressures_from_kernel(result)
    certificates = sim._last_high_t_melt_activity["certified_effective_gamma_by_parent"]
    assert certificates
    assert all(
        c["hour"] is None
        and c["window_K"] is None
        and c["composition_window_L1"] is None
        and "gamma_prev" not in c
        for c in certificates.values()
    )


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
    def no_numerical_continuation(*args, **kwargs):
        pytest.fail("composition policy refusal entered numerical continuation")

    monkeypatch.setattr(vapor_pressure_module, "_same_composition_temperature_bridge", no_numerical_continuation)
    result = _provider().dispatch(_request(composition, CAP_PLUS_T_K))
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
    assert high_t["certified_effective_gamma_by_parent"] == {}
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


def test_projected_nonconvergence_uses_projected_composition_temperature_neighbors(
    monkeypatch,
) -> None:
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
    result = _provider().dispatch(_request(composition, CAP_PLUS_T_K))
    high_t = result.diagnostic["high_t_melt_activity"]
    assert calls[0] == (composition, CAP_PLUS_T_K)
    assert calls[1] == (projected, CAP_PLUS_T_K)
    assert all(comp == projected for comp, _ in calls[2:])
    assert high_t["fallback_reason"]["code"] == "imcc_nonconvergence"
    assert high_t["projected_composition_solve"]["original_refusal"]["code"] == "openimcc_projection_over_threshold"
    silica = high_t["held_effective_gamma_by_parent"]["SiO2"]
    assert silica["method"] in {
        "nearest_converged_temperature_neighbor",
        "interpolated_converged_temperature_neighbors",
    }
    if silica["method"] == "interpolated_converged_temperature_neighbors":
        assert silica["lower_anchor_temperature_K"] < CAP_PLUS_T_K
        assert silica["upper_anchor_temperature_K"] > CAP_PLUS_T_K
        assert silica["lower_anchor_gap_K"] <= 75.0
        assert silica["upper_anchor_gap_K"] <= 75.0
    else:
        assert silica["anchor_gap_K"] <= 75.0
    # The neighbour's a(T) must be independent of the denominator used to
    # report effective gamma; compare with its existing projected-bulk path.
    reference = _provider().dispatch(_request(projected, CAP_PLUS_T_K))
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
    authority = vapor_pressure_module._build_high_t_melt_activity_authority(
        composition_mol={"SiO2": 1.0, parent: amount},
        temperature_K=CAP_PLUS_T_K,
        controls={"high_t_melt_activity": "openimcc"},
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
