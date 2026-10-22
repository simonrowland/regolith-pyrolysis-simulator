"""Openimcc opt-in P2O5 extension: projection count, notices, and Stage-0 survival.

Expected masses and the 1873 K activity use CIAAW weights, stoichiometry, and
the published Suito-Inoue constants. They do not import the production
atomic-weight table or paste the simulator's policy formula.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from types import SimpleNamespace

import pytest

# CIAAW standard atomic weights (g/mol).
_CIAAW_P = 30.973761998
_CIAAW_O = 15.999
_CIAAW_SI = 28.085
_CIAAW_NI = 58.6934
_P2O5_MOLAR_MASS_G = 2.0 * _CIAAW_P + 5.0 * _CIAAW_O
_SIO2_MOLAR_MASS_G = _CIAAW_SI + 2.0 * _CIAAW_O
_NIO_MOLAR_MASS_G = _CIAAW_NI + _CIAAW_O
_R_J_MOL_K = 8.31446261815324
_PUBLISHED_LIMITATIONS = (
    "Fe-P alloy partitioning under carbon reduction is not modelled; "
    "metallic iron belongs to the simulator metal account.",
    "Fe(III) phosphates are not modelled.",
)
_ONE_BAR_MESSAGE = (
    "P would leave essentially completely; the fixed-composition dilute "
    "equilibrium is not physical above 1 bar."
)


def _ciaaw_p2o5_dropped_wt_pct() -> float:
    """Wt% of 0.02 mol P2O5 in 1.00 mol SiO2."""

    mass_sio2_g = 1.0 * _SIO2_MOLAR_MASS_G
    mass_p2o5_g = 0.02 * _P2O5_MOLAR_MASS_G
    return mass_p2o5_g / (mass_sio2_g + mass_p2o5_g) * 100.0


def _moles_at_wt_pct(wt_by_oxide: dict[str, float]) -> dict[str, float]:
    """Moles in 100 g of bulk from CIAAW molar masses."""

    molar = {
        "SiO2": _SIO2_MOLAR_MASS_G,
        "P2O5": _P2O5_MOLAR_MASS_G,
        "NiO": _NIO_MOLAR_MASS_G,
    }
    return {
        name: wt_pct / molar[name]
        for name, wt_pct in wt_by_oxide.items()
    }


def _fake_activity(composition, temperature, *, feiii=False):
    del composition, temperature
    extra = ()
    if feiii:
        extra = (
            SimpleNamespace(
                code="p2o5_feiii_phosphate_chemistry_not_modelled",
                message="Fe(III) phosphate chemistry is not modelled.",
            ),
        )
    return SimpleNamespace(
        activity=0.01,
        log10_activity=-2.0,
        gamma=1.0,
        x_p2o5=0.01,
        band_log10_activity=(-2.4, -1.6),
        notice=SimpleNamespace(
            code="imcc_p2o5_activity_domain_notice",
            regression="Suito-Inoue-1984-Eq-14-no-Mn",
            violated_axes=("temperature_outside_1823_1923_K",),
            band_width_dex=0.8,
            limitations=_PUBLISHED_LIMITATIONS,
        ),
        additional_notices=extra,
    )


def _load_evaluate_p2o5_activity():
    try:
        import openimcc
    except ImportError:
        openimcc = None
    if openimcc is not None:
        function = getattr(openimcc, "evaluate_p2o5_activity", None)
        if callable(function):
            return function
    path = "/private/tmp/claude-501/oimcc-eeea7b2/src/openimcc/p2o5.py"
    spec = importlib.util.spec_from_file_location(
        "openimcc_p2o5_documented", path
    )
    if spec is None or spec.loader is None:
        pytest.skip("openimcc P2O5 extension is not importable")
    module = importlib.util.module_from_spec(spec)
    # dataclass resolves the class module in sys.modules during decoration.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError:
        sys.modules.pop(spec.name, None)
        pytest.skip("openimcc P2O5 extension is not importable")
    return module.evaluate_p2o5_activity


def test_p2o5_projection_count_pin_over_threshold() -> None:
    """Disabled extension keeps today's refusal for 0.02 mol P2O5."""

    from simulator.melt_backend.openimcc_bridge import (
        OpenImccCompositionPolicyRefusal,
        _cleaned_melt_policy,
    )

    with pytest.raises(OpenImccCompositionPolicyRefusal) as caught:
        _cleaned_melt_policy(
            {"SiO2": 1.0, "P2O5": 0.02},
            enable_p2o5_extension=False,
        )
    assert caught.value.code == "openimcc_projection_over_threshold"
    assert "p2o5_extension" not in caught.value.diagnostics
    classification = caught.value.diagnostics[
        "composition_projection_classification"
    ]
    assert classification["dropped_components"] == ["P2O5"]
    assert classification["dropped_total_wt_pct"] == pytest.approx(
        _ciaaw_p2o5_dropped_wt_pct(),
        rel=0.0,
        abs=1e-9,
    )
    assert classification["dropped_total_wt_pct"] > 1.0


def test_melt_without_p2o5_policy_is_unchanged() -> None:
    from simulator.melt_backend.openimcc_bridge import _cleaned_melt_policy

    disabled = _cleaned_melt_policy(
        {"SiO2": 1.0, "MgO": 0.1},
        enable_p2o5_extension=False,
        temperature_K=1873.0,
    )
    enabled = _cleaned_melt_policy(
        {"SiO2": 1.0, "MgO": 0.1},
        temperature_K=1873.0,
    )
    assert enabled == disabled


def test_unavailable_extension_keeps_the_projection_refusal(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setattr(openimcc_bridge, "p2o5_extension_callable", lambda: None)
    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        openimcc_bridge._cleaned_melt_policy(
            {"SiO2": 1.0, "P2O5": 0.02},
            temperature_K=1873.0,
        )
    assert caught.value.code == "openimcc_projection_over_threshold"
    extension = caught.value.diagnostics["p2o5_extension"]
    assert extension["excluded_from_projection_count"] is False
    assert extension["notices"] == [{
        "code": "openimcc_p2o5_extension_unavailable",
        "message": (
            "installed openimcc has no evaluate_p2o5_activity; "
            "P2O5 stays in the 1 wt% unsupported-projection count"
        ),
    }]
    classification = caught.value.diagnostics[
        "composition_projection_classification"
    ]
    assert classification["dropped_total_wt_pct"] == pytest.approx(
        _ciaaw_p2o5_dropped_wt_pct(),
        rel=0.0,
        abs=1e-9,
    )


def test_activity_refusal_does_not_exclude_p2o5(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    def refuse(composition, temperature):
        del composition, temperature
        raise ValueError("no positive P2O5")

    monkeypatch.setattr(
        openimcc_bridge, "p2o5_extension_callable", lambda: refuse
    )
    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        openimcc_bridge._cleaned_melt_policy(
            {"SiO2": 1.0, "P2O5": 0.02},
            temperature_K=1873.0,
        )
    extension = caught.value.diagnostics["p2o5_extension"]
    assert extension["excluded_from_projection_count"] is False
    assert extension["notices"][-1]["code"] == "openimcc_p2o5_extension_refused"


def test_successful_extension_removes_only_p2o5_from_the_count(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setattr(
        openimcc_bridge,
        "p2o5_extension_callable",
        lambda: lambda composition, temperature: _fake_activity(
            composition, temperature
        ),
    )
    cleaned, policy = openimcc_bridge._cleaned_melt_policy(
        {"SiO2": 1.0, "P2O5": 0.02},
        temperature_K=1873.0,
    )
    assert "P2O5" not in cleaned
    extension = policy["p2o5_extension"]
    assert extension["excluded_from_projection_count"] is True
    assert extension["activity"] == pytest.approx(0.01)
    codes = [notice["code"] for notice in extension["notices"]]
    assert "imcc_p2o5_activity_domain_notice" in codes
    domain = next(
        notice for notice in extension["notices"]
        if notice["code"] == "imcc_p2o5_activity_domain_notice"
    )
    assert domain["limitations"] == list(_PUBLISHED_LIMITATIONS)
    classification = policy["composition_projection_classification"]
    assert classification["projection_verdict"] == "within_threshold"
    assert classification["dropped_components"] == ["P2O5"]
    assert classification["thresholded_dropped_total_wt_pct"] == pytest.approx(0.0)
    assert policy["openimcc_projection_p2o5_excluded"]["wt_pct"]["P2O5"] == (
        pytest.approx(_ciaaw_p2o5_dropped_wt_pct())
    )


def test_missing_temperature_does_not_exclude_p2o5(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setattr(
        openimcc_bridge,
        "p2o5_extension_callable",
        lambda: lambda composition, temperature: _fake_activity(
            composition, temperature
        ),
    )
    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        openimcc_bridge._cleaned_melt_policy({"SiO2": 1.0, "P2O5": 0.02})
    extension = caught.value.diagnostics["p2o5_extension"]
    assert extension["excluded_from_projection_count"] is False
    assert extension["notices"][0]["code"] == (
        "openimcc_p2o5_extension_temperature_missing"
    )


def test_omitted_components_still_model_p2o5(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    seen = {}

    def fake(composition, temperature):
        seen["composition"] = dict(composition)
        return _fake_activity(composition, temperature)

    monkeypatch.setattr(openimcc_bridge, "p2o5_extension_callable", lambda: fake)
    _cleaned, policy = openimcc_bridge._cleaned_melt_policy(
        {
            "SiO2": 1.0,
            "P2O5": 0.01,
            "Cr2O3": 0.02,
            # NiO and CoO stay in the 1 wt% count. These amounts keep their
            # combined CIAAW mass under that gate so the activity call returns.
            "NiO": 0.001,
            "CoO": 0.001,
        },
        temperature_K=1873.0,
    )
    assert set(seen["composition"]) <= openimcc_bridge._P2O5_ACTIVITY_COMPONENTS
    assert "Cr2O3" not in seen["composition"]
    omitted = policy["p2o5_extension"]["omitted_components"]
    assert set(omitted) == {"Cr2O3", "NiO", "CoO"}
    assert policy["p2o5_extension"]["excluded_from_projection_count"] is True
    notice = next(
        item for item in policy["p2o5_extension"]["notices"]
        if item["code"] == "p2o5_activity_basis_omitted_components"
    )
    assert set(notice["components"]) == {"Cr2O3", "NiO", "CoO"}


def test_fe2o3_is_passed_unfolded_with_the_feiii_notice(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    seen = {}

    def fake(composition, temperature):
        seen["composition"] = dict(composition)
        return _fake_activity(composition, temperature, feiii=True)

    monkeypatch.setattr(openimcc_bridge, "p2o5_extension_callable", lambda: fake)
    _cleaned, policy = openimcc_bridge._cleaned_melt_policy(
        {"SiO2": 1.0, "Fe2O3": 0.2, "P2O5": 0.01},
        temperature_K=1873.0,
    )
    assert seen["composition"]["Fe2O3"] == pytest.approx(0.2)
    assert "FeO" not in seen["composition"]
    codes = [notice["code"] for notice in policy["p2o5_extension"]["notices"]]
    assert "p2o5_feiii_phosphate_chemistry_not_modelled" in codes


def test_one_wt_pct_count_changes_only_after_a_successful_call(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    p2o5_only = _moles_at_wt_pct({"SiO2": 98.5, "P2O5": 1.5})
    both = _moles_at_wt_pct({"SiO2": 98.8, "NiO": 0.6, "P2O5": 0.6})

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        openimcc_bridge._cleaned_melt_policy(
            p2o5_only,
            enable_p2o5_extension=False,
            temperature_K=1873.0,
        )
    assert caught.value.code == "openimcc_projection_over_threshold"
    assert caught.value.diagnostics[
        "composition_projection_classification"
    ]["dropped_total_wt_pct"] == pytest.approx(1.5)

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        openimcc_bridge._cleaned_melt_policy(
            both,
            enable_p2o5_extension=False,
            temperature_K=1873.0,
        )
    assert caught.value.diagnostics[
        "composition_projection_classification"
    ]["dropped_total_wt_pct"] == pytest.approx(1.2)

    monkeypatch.setattr(
        openimcc_bridge,
        "p2o5_extension_callable",
        lambda: lambda composition, temperature: _fake_activity(
            composition, temperature
        ),
    )
    _cleaned, policy = openimcc_bridge._cleaned_melt_policy(
        p2o5_only, temperature_K=1873.0
    )
    assert policy["composition_projection_classification"][
        "thresholded_dropped_total_wt_pct"
    ] == pytest.approx(0.0)
    _cleaned, policy = openimcc_bridge._cleaned_melt_policy(
        both, temperature_K=1873.0
    )
    classification = policy["composition_projection_classification"]
    assert classification["projection_verdict"] == "within_threshold"
    assert classification["thresholded_dropped_total_wt_pct"] == pytest.approx(0.6)
    assert classification["thresholded_dropped_components"] == ["NiO"]


def test_published_1873_k_central_value_and_limitations() -> None:
    evaluate = _load_evaluate_p2o5_activity()
    composition = {"CaO": 40, "MgO": 8, "FeO": 12, "SiO2": 39, "P2O5": 1}
    result = evaluate(composition, 1873)
    # Suito & Inoue 1984 Eq. 14, no Mn, on these mole fractions, shifted from
    # the Turkdogan-Pearson 1 atm liquid standard to the 1 bar gas-pack standard.
    x_p = 1.0 / 100.0
    log_gamma = (
        -1.02 * (23 * 0.40 + 17 * 0.08 + 8 * 0.12 - 26 * 0.01)
        - 22990.0 / 1873.0
        + 9.490
    )
    source_g = -1534480.0 + 506.26 * 1873.0
    pack_g = (
        -1616372.0
        + 539.0 * 1873.0
        + 3.5 * _R_J_MOL_K * 1873.0 * math.log(1.01325)
    )
    expected = (
        log_gamma
        + math.log10(x_p)
        + (source_g - pack_g) / (_R_J_MOL_K * 1873.0 * math.log(10.0))
    )
    assert result.log10_activity == pytest.approx(expected)
    assert result.activity == pytest.approx(10.0 ** expected)
    assert result.notice.limitations == _PUBLISHED_LIMITATIONS
    folded = evaluate(
        {"CaO": 40, "MgO": 8, "Fe2O3": 6, "SiO2": 39, "P2O5": 1},
        1873,
    )
    equivalent = evaluate(
        {"CaO": 40, "MgO": 8, "FeOeq": 12, "SiO2": 39, "P2O5": 1},
        1873,
    )
    assert folded.log10_activity == pytest.approx(equivalent.log10_activity)
    assert folded.additional_notices[0].code == (
        "p2o5_feiii_phosphate_chemistry_not_modelled"
    )


def test_gas_notice_and_retry_keep_the_one_bar_message() -> None:
    from simulator.melt_backend.openimcc_bridge import (
        evaluate_openimcc_gas_with_p2o5,
        p_gas_notices,
    )

    notice = SimpleNamespace(
        code="p_gas_exceeds_one_bar_complete_volatilisation",
        message=_ONE_BAR_MESSAGE,
        species=("PO", "PO2"),
        total_pressure_bar=1.4,
    )
    extracted = p_gas_notices(SimpleNamespace(notices=(notice,)))
    assert extracted == [{
        "code": "p_gas_exceeds_one_bar_complete_volatilisation",
        "message": _ONE_BAR_MESSAGE,
        "species": ["PO", "PO2"],
        "total_pressure_bar": 1.4,
    }]

    def evaluate_gas(activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs):
        del temperature_K, fo2, datapack, kwargs
        if "P2O5" in parent_oxides:
            raise ValueError("unknown parent P2O5")
        assert "P2O5" not in activities
        return SimpleNamespace(notices=())

    _result, notices = evaluate_openimcc_gas_with_p2o5(
        evaluate_gas,
        {"SiO2": 0.4},
        1873.0,
        1.0e-9,
        object(),
        ("SiO2",),
        {"evaluated": True, "activity": 0.02},
    )
    assert notices[0]["code"] == "openimcc_p2o5_gas_unavailable"
    assert "phosphorus partial pressures were not produced" in notices[0]["message"]


def test_vapour_injection_uses_existing_channels_and_skips_stage0(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    notice = SimpleNamespace(
        code="p_gas_exceeds_one_bar_complete_volatilisation",
        message=_ONE_BAR_MESSAGE,
        species=("P", "PO"),
        total_pressure_bar=2.0,
    )

    class _Gas(dict):
        notices = (notice,)

    def evaluate_gas(activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs):
        del temperature_K, datapack, kwargs
        assert activities["P2O5"] == pytest.approx(0.01)
        assert parent_oxides == ("P2O5",)
        assert fo2 == pytest.approx(1.0e-9)
        return _Gas({
            species: 0.1
            for species in ("P", "P2", "P4", "PO", "PO2", "P4O6", "P4O10")
        })

    monkeypatch.setattr(
        openimcc_bridge,
        "_openimcc",
        SimpleNamespace(
            evaluate_gas=evaluate_gas,
            load_gas_datapack=lambda: object(),
        ),
    )
    extension = {"evaluated": True, "activity": 0.01}
    record = openimcc_bridge.p2o5_vapour_pressures_pa(
        extension,
        temperature_K=1873.0,
        fo2_bar=1.0e-9,
        process_phase="hot_train",
    )
    assert record["inject"] is True
    assert tuple(record["flux_species"]) == openimcc_bridge.P2O5_FLUX_SPECIES
    assert "P" not in record["flux_pressures_Pa"]
    assert record["diagnostic_pressures_Pa"]["P"] == pytest.approx(0.1 * 1.0e5)
    assert record["flux_pressures_Pa"]["PO"] == pytest.approx(0.1 * 1.0e5)
    assert record["notices"][0]["message"] == _ONE_BAR_MESSAGE
    stage0 = openimcc_bridge.p2o5_vapour_pressures_pa(
        extension,
        temperature_K=1873.0,
        fo2_bar=1.0e-9,
        process_phase="stage0",
    )
    assert stage0["inject"] is False
    assert stage0["flux_pressures_Pa"] == {}


def test_authority_copies_extension_notices_once() -> None:
    from engines.builtin.vapor_pressure import _remember_p2o5_extension

    base = {"notices": []}
    source = {
        "p2o5_extension": {
            "enabled": True,
            "p2o5_moles": 0.02,
            "notices": [{
                "code": "openimcc_p2o5_extension_unavailable",
                "message": "installed openimcc has no evaluate_p2o5_activity",
            }],
        }
    }
    _remember_p2o5_extension(base, source)
    _remember_p2o5_extension(base, source)
    assert base["notices"] == source["p2o5_extension"]["notices"]
    assert base["p2o5_extension"]["notices"] == base["notices"]


def test_main_sequence_stage_wakes_only_existing_p2o5_rules() -> None:
    from simulator.vapour_rail.request import (
        MAIN_SEQUENCE_P_EXTENSION_STAGE,
        _predicate_active,
        request_stage_for_campaign,
    )

    state = SimpleNamespace(
        stage=MAIN_SEQUENCE_P_EXTENSION_STAGE,
        process_phase="hot_train",
    )
    phosphorus = SimpleNamespace(
        applicability_predicate="stage0_only",
        parent_species_ids=("P2O5",),
    )
    sodium = SimpleNamespace(
        applicability_predicate="stage0_only",
        parent_species_ids=("Na2O",),
    )
    assert _predicate_active(phosphorus, state) == (True, "")
    assert _predicate_active(sodium, state)[0] is False
    assert request_stage_for_campaign("C0") == "stage0_p_carriers"
    assert request_stage_for_campaign("C0B") == "c0b_p_cleanup"
    assert request_stage_for_campaign("C2A") == "evaporation"
    species = ("PO", "PO2", "P2", "P4", "P4O6", "P4O10")
    assert request_stage_for_campaign(
        "C2A",
        p_extension_flux_species=species,
        effective_pressure_species_ids=frozenset(species),
    ) == MAIN_SEQUENCE_P_EXTENSION_STAGE
    assert request_stage_for_campaign(
        "C0",
        p_extension_flux_species=species,
        effective_pressure_species_ids=frozenset(species),
    ) == "stage0_p_carriers"
    assert request_stage_for_campaign(
        "C2A",
        p_extension_flux_species=species,
        effective_pressure_species_ids=frozenset(("PO",)),
    ) == "evaporation"


def test_p2o5_survived_stage0_cleanup_records_once() -> None:
    from simulator.core import (
        note_p2o5_survived_stage0_cleanup,
        p2o5_survived_stage0_cleanup_record,
    )
    from simulator.state import CampaignPhase

    mass_kg = 0.01 * _P2O5_MOLAR_MASS_G / 1000.0
    assert p2o5_survived_stage0_cleanup_record(
        CampaignPhase.C0, CampaignPhase.C0B, {"P2O5": mass_kg}
    ) is None
    assert p2o5_survived_stage0_cleanup_record(
        CampaignPhase.IDLE, CampaignPhase.C2A, {"P2O5": mass_kg}
    ) is None
    recorded = p2o5_survived_stage0_cleanup_record(
        CampaignPhase.C0B,
        CampaignPhase.C2A,
        {"P2O5": mass_kg, "SiO2": 1.0},
    )
    assert recorded == {
        "code": "P survived Stage-0 cleanup",
        "p2o5_mass_kg": mass_kg,
        "left_campaign": "C0B",
        "entered_campaign": "C2A",
    }
    empty = p2o5_survived_stage0_cleanup_record(
        CampaignPhase.C0, CampaignPhase.C2A, {}
    )
    assert empty["p2o5_mass_kg"] == 0.0
    assert empty["code"] == "P survived Stage-0 cleanup"
    host = SimpleNamespace()
    note_p2o5_survived_stage0_cleanup(
        host, CampaignPhase.C0B, CampaignPhase.C2A, {"P2O5": mass_kg}
    )
    note_p2o5_survived_stage0_cleanup(
        host, CampaignPhase.C2A, CampaignPhase.C4, {"P2O5": mass_kg * 2.0}
    )
    assert host._p2o5_survived_stage0_cleanup["p2o5_mass_kg"] == mass_kg
    assert host._p2o5_survived_stage0_cleanup["entered_campaign"] == "C2A"
