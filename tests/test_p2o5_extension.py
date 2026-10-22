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


_BAR_PA = 100_000.0
_FLUX_PARTIAL_BAR = 0.2
_FLUX_MOL = {
    "Na2O": 0.20,
    "K2O": 0.08,
    "SiO2": 5.00,
    "FeO": 1.00,
    "MgO": 1.00,
    "CaO": 1.00,
    "Al2O3": 1.00,
    "TiO2": 0.10,
    "P2O5": 0.01,
}


class _PGas(dict):
    """Mapping-shaped gas result. Pressures are bar, as openimcc returns them."""

    domain_flags: dict = {}
    provenance_class: dict = {}
    omitted_channels: dict = {}

    def __init__(self, pressures, notices=()):
        super().__init__(pressures)
        self.notices = notices


def _patch_activity(monkeypatch, *, violated=True):
    from simulator.melt_backend import openimcc_bridge

    def evaluate_p2o5_activity(composition, temperature):
        del temperature
        axes = ("temperature_outside_1823_1923_K",) if violated else ()
        feiii = float(composition.get("Fe2O3", 0.0) or 0.0) > 0.0
        return SimpleNamespace(
            activity=0.01,
            log10_activity=-2.0,
            gamma=4.0,
            x_p2o5=0.01,
            band_log10_activity=(-2.4, -1.6),
            notice=SimpleNamespace(
                code="imcc_p2o5_activity_domain_notice",
                regression="Suito-Inoue-1984-Eq-14-no-Mn",
                violated_axes=axes,
                band_width_dex=0.8,
                limitations=_PUBLISHED_LIMITATIONS,
            ),
            additional_notices=(
                (
                    SimpleNamespace(
                        code="p2o5_feiii_phosphate_chemistry_not_modelled",
                        message="Fe(III) phosphate chemistry is not modelled.",
                    ),
                )
                if feiii
                else ()
            ),
        )

    monkeypatch.setattr(
        openimcc_bridge._openimcc,
        "evaluate_p2o5_activity",
        evaluate_p2o5_activity,
        raising=False,
    )


def test_flux_activity_authority_follows_domain_notices() -> None:
    """Extrapolated when a regression axis or an omitted component is present.

    A clean evaluation stays flagged. Gamma-domain status follows the
    regression axes only. The reconciliation names the published shift.
    """

    from simulator.melt_backend.openimcc_bridge import p2o5_flux_activity_evidence

    clean = {
        "evaluated": True,
        "activity": 0.01,
        "omitted_components": {},
        "notices": [{
            "code": "imcc_p2o5_activity_domain_notice",
            "regression": "Suito-Inoue-1984-Eq-14-no-Mn",
            "violated_axes": [],
            "limitations": list(_PUBLISHED_LIMITATIONS),
        }],
    }
    _activities, provenance = p2o5_flux_activity_evidence(clean)
    row = provenance["PO"]
    assert row["melt_oxide_activity_authority_status"] == "flagged"
    assert row["gamma_domain_authority"]["authority_status"] == "flagged"
    assert row["equivalent_parent_oxide_activity"] == pytest.approx(0.01)
    text = row["activity_standard_state_reconciliation"]
    assert "3.5" in text
    assert "1.01325" in text
    assert "hypothetical-liquid" in text
    assert "Turkdogan" in text
    assert "1 bar" in text
    assert row["melt_oxide_activity_reference_state"] == {
        "convention": "raoultian_pure_endmember",
        "phase": "liquid",
        "reference_pressure_bar": 1.0,
        "reference_temperature_K": None,
        "component_basis": "raoultian_pure_endmember",
    }

    violated = {
        **clean,
        "notices": [{
            **clean["notices"][0],
            "violated_axes": ["temperature_outside_1823_1923_K"],
        }],
    }
    _activities, provenance = p2o5_flux_activity_evidence(violated)
    assert provenance["PO2"]["melt_oxide_activity_authority_status"] == "extrapolated"
    assert (
        provenance["PO2"]["gamma_domain_authority"]["authority_status"]
        == "out_of_gamma_domain"
    )

    omitted = {**clean, "omitted_components": {"Cr2O3": 0.001}}
    _activities, provenance = p2o5_flux_activity_evidence(omitted)
    assert provenance["P2"]["melt_oxide_activity_authority_status"] == "extrapolated"
    assert provenance["P2"]["gamma_domain_authority"]["authority_status"] == "flagged"


def test_incomplete_p_gas_channel_writes_nothing(monkeypatch) -> None:
    from simulator.melt_backend import openimcc_bridge

    def evaluate_gas(activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs):
        del activities, temperature_K, fo2, datapack, parent_oxides, kwargs
        return _PGas({
            species: _FLUX_PARTIAL_BAR
            for species in ("P", "P2", "P4", "PO", "P4O6", "P4O10")
        })

    monkeypatch.setattr(
        openimcc_bridge,
        "_openimcc",
        SimpleNamespace(
            evaluate_gas=evaluate_gas,
            load_gas_datapack=lambda: object(),
        ),
    )
    record = openimcc_bridge.p2o5_vapour_pressures_pa(
        {"evaluated": True, "activity": 0.01},
        temperature_K=1873.0,
        fo2_bar=1.0e-9,
        process_phase="hot_train",
    )
    assert record["inject"] is False
    assert record["flux_pressures_Pa"] == {}
    assert record["notices"][-1]["code"] == "openimcc_p2o5_gas_incomplete"
    assert "PO2" in record["notices"][-1]["message"]


def test_p_carriers_become_flux_active_and_debit_p2o5_at_c2a(monkeypatch) -> None:
    """Injected a(P2O5) makes the existing carriers debit at C2A above the cap.

    The flux value is the injected partial, 0.2 bar * 1e5 Pa/bar. The
    ledger debit removes one P2O5 molecule for every two PO molecules,
    from the reaction P2O5 → 2 PO + 1.5 O2.
    """

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
    from simulator.config import load_config_bundle
    from simulator.core import PyrolysisSimulator
    from simulator.evaporation import _pre_rg_effective_pressure_source
    from simulator.melt_backend.base import InternalAnalyticalBackend
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend.vaporock import VAPOROCK_T_MAX_K
    from simulator.state import CampaignPhase
    from simulator.vapour_rail.instrumentation import flux_pressures_from_batch
    import yaml
    from pathlib import Path

    _patch_activity(monkeypatch, violated=True)

    def evaluate_gas(activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs):
        del activities, temperature_K, fo2, datapack, parent_oxides, kwargs
        return _PGas(
            {
                species: _FLUX_PARTIAL_BAR
                for species in ("P", "P2", "P4", "PO", "PO2", "P4O6", "P4O10")
            },
            notices=(
                SimpleNamespace(
                    code="p_gas_exceeds_one_bar_complete_volatilisation",
                    message=_ONE_BAR_MESSAGE,
                    species=("PO", "PO2", "P2", "P4", "P4O6", "P4O10"),
                    total_pressure_bar=1.4,
                ),
            ),
        )

    monkeypatch.setattr(
        openimcc_bridge._openimcc, "evaluate_gas", evaluate_gas, raising=False
    )
    temperature_K = VAPOROCK_T_MAX_K + 523.0
    assert temperature_K == pytest.approx(2473.0)
    data_path = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
    provider = BuiltinVaporPressureProvider(yaml.safe_load(data_path.read_text()) or {})
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(_FLUX_MOL)},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1.0e-6,
        control_inputs={
            "pO2_bar": 1.0e-9,
            "intrinsic_fO2_log": -10.0,
            "high_t_melt_activity": "openimcc",
            "process_phase": "hot_train",
        },
    )
    result = provider.dispatch(request)
    diagnostic = result.diagnostic
    expected_pa = _FLUX_PARTIAL_BAR * _BAR_PA
    assert diagnostic["openimcc_p2o5_gas"]["inject"] is True
    assert "P" not in diagnostic["vapor_pressures_Pa"]
    assert "P" not in diagnostic["activities"]
    for species in ("P2", "P4", "PO", "PO2", "P4O6", "P4O10"):
        assert diagnostic["vapor_pressures_Pa"][species] == pytest.approx(expected_pa)
        assert diagnostic["activities"][species] == pytest.approx(0.01)
        provenance = diagnostic["vapor_pressure_numerator_provenance"][species]
        assert provenance["equivalent_parent_oxide_activity"] == pytest.approx(0.01)
        assert provenance["melt_oxide_activity_authority_status"] == "extrapolated"
        assert "3.5" in provenance["activity_standard_state_reconciliation"]
        assert "1.01325" in provenance["activity_standard_state_reconciliation"]
    assert any(_ONE_BAR_MESSAGE in warning for warning in result.warnings)

    # load_batch builds the chemistry kernel the debit commits through.
    # The batch admits a carrier only when its parent is on the ledger.
    bundle = load_config_bundle(Path(__file__).resolve().parents[1] / "data")
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        dict(_FLUX_MOL),
        source="test seed",
        material_origin="feedstock",
    )
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.temperature_C = temperature_K - 273.15
    sim._last_vapor_pressure_diagnostic = diagnostic
    equilibrium = SimpleNamespace(
        vapor_pressures_Pa=dict(diagnostic["vapor_pressures_Pa"]),
        activity_coefficients=dict(diagnostic["activities"]),
        temperature_C=temperature_K - 273.15,
        diagnostics={},
    )
    effective = _pre_rg_effective_pressure_source(sim.vapor_pressures, equilibrium)
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=temperature_K,
        effective_pressure_source=effective,
    )
    assert batch is not None, sim._last_vapour_batch_resolve_error
    flux, report = flux_pressures_from_batch(
        batch, effective_pressure_source=effective
    )
    for species in ("P2", "P4", "PO", "PO2", "P4O6", "P4O10"):
        assert report["batch_channel_states"].get(species) == "eligible", report
        assert flux[species] == pytest.approx(expected_pa)
        assert batch.channel(species).is_flux_active
    assert "P" not in flux
    # The P-extension stage wakes stage0_only P2O5 rules. It must not
    # take the ordinary hot-train carriers out of the flux map.
    assert batch.channel("SiO").is_flux_active
    assert flux["SiO"] > 0.0

    # The credit path reads the legacy oxide-vapour row, the same object
    # evaporation passes into the transition.
    po_row = sim.vapor_pressures["oxide_vapors"]["PO"]
    rate_kg = 1.0e-3
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"P2O5": 1.0},
        source="test seed",
        material_origin="feedstock",
    )
    parent = resolve_species_formula("P2O5", sim.species_formula_registry)
    carrier = resolve_species_formula("PO", sim.species_formula_registry)
    # P2O5 → 2 PO: one phosphorus atom in PO, two in P2O5.
    assert parent.elements["P"] == 2
    assert carrier.elements["P"] == 1
    expected_oxide_mol = (
        rate_kg
        / carrier.molar_mass_kg_per_mol()
        * carrier.elements["P"]
        / parent.elements["P"]
    )
    assert expected_oxide_mol * 2.0 == pytest.approx(
        rate_kg / carrier.molar_mass_kg_per_mol()
    )
    before = sim.atom_ledger.mol_by_species("process.cleaned_melt")["P2O5"]
    _credited, transition = sim._credit_evaporation_transition(
        "PO",
        rate_kg,
        rate_kg,
        po_row,
        apply_evaporative_redox_source_terms=False,
        return_transition=True,
    )
    assert transition is not None
    after = sim.atom_ledger.mol_by_species("process.cleaned_melt").get("P2O5", 0.0)
    assert before - after == pytest.approx(expected_oxide_mol, rel=1.0e-9)


def _scoring_pot(pot_id: str):
    from simulator.diagnostic_helpers.binary_pot_scoring import load_scoring_pots

    pots = {pot.pot_id: pot for pot in load_scoring_pots()}
    return pots[pot_id]


def test_phosphorus_pots_are_assessed_before_the_parent_solve(monkeypatch) -> None:
    """The installed openimcc selects the pot assertion. Neither branch is skipped.

    With no evaluate_p2o5_activity, both pots still raise the typed 1 wt%
    refusal after assessment, not the old S/P pack. With the callable
    present, the same calls return the assessed liquid a(P2O5). The
    landing pin has the callable, so that run executes both branches:
    the refusal is forced by hiding the callable, then the real call
    is repeated.
    """

    import openimcc
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
        composition_kg_and_mol,
    )
    from simulator.melt_backend import openimcc_bridge
    from simulator.melt_backend.openimcc_bridge import (
        OpenImccCompositionPolicyRefusal,
    )

    pot_ids = (
        "kambayashi_1985_feto_p2o5_s01",
        "ohara_1987_feto_p2o5_i_s14",
    )
    real_callable = openimcc_bridge.p2o5_extension_callable()

    def equilibrate(pot_id: str):
        backend = _OpenImccBatteryBackend("openimcc")
        pot = _scoring_pot(pot_id)
        composition_kg, composition_mol = composition_kg_and_mol(
            pot.composition_wt_pct
        )
        return backend.equilibrate(
            1643.0 - 273.15,
            composition_kg=composition_kg,
            composition_mol=composition_mol,
            fO2_log=-9.0,
        )

    def assert_unavailable_refusal() -> None:
        sp_error = openimcc.ImccSPComponentRequiresExtensionError
        for pot_id in pot_ids:
            with pytest.raises(OpenImccCompositionPolicyRefusal) as caught:
                equilibrate(pot_id)
            assert not isinstance(caught.value, sp_error)
            assert caught.value.code == "openimcc_projection_over_threshold"
            extension = caught.value.diagnostics["p2o5_extension"]
            assert any(
                notice.get("code") == "openimcc_p2o5_extension_unavailable"
                for notice in extension["notices"]
            )

    def assert_visible_activity() -> None:
        for pot_id in pot_ids:
            result = equilibrate(pot_id)
            assert result.status == "ok"
            extension = result.diagnostics["p2o5_extension"]
            assert extension.get("evaluated") is True
            activity = result.activity_coefficients["P2O5"]
            assert activity == pytest.approx(float(extension["activity"]))
            assert activity > 0.0
            assert "P2O5" not in result.reported_activity_coefficients
            reference = result.activities_standard_state
            assert reference["phase"] == "liquid"
            assert reference["component"] == "P2O5"
            assert reference["convention"] == "raoultian_pure_endmember"
            assert reference["activity"] == pytest.approx(activity)
            codes = [notice.get("code") for notice in result.imcc_notices]
            assert "imcc_p2o5_activity_domain_notice" in codes
            assert "p2o5_feiii_phosphate_chemistry_not_modelled" in codes

    package = openimcc_bridge._openimcc
    monkeypatch.setattr(
        package, "evaluate_p2o5_activity", None, raising=False
    )
    assert openimcc_bridge.p2o5_extension_callable() is None
    assert_unavailable_refusal()
    monkeypatch.undo()
    if real_callable is None:
        assert openimcc_bridge.p2o5_extension_callable() is None
        return
    assert openimcc_bridge.p2o5_extension_callable() is not None
    assert_visible_activity()


def test_phosphorus_pot_exposes_liquid_p2o5_activity(monkeypatch) -> None:
    """A successful dilute call is returned as liquid-reference a(P2O5).

    Gamma stays off the activity map. The parent solve sees folded iron
    and no P2O5. The cell copies that reference when it has no vapour-rail
    standard state of its own.
    """

    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        PO2_COMMANDED,
        Po2Request,
        _OpenImccBatteryBackend,
        composition_kg_and_mol,
        equilibrate_cell,
    )
    from simulator.melt_backend import openimcc_bridge

    seen: list[dict[str, float]] = []
    original = openimcc_bridge.evaluate

    def track_evaluate(**kwargs):
        seen.append(dict(kwargs["composition_mol"]))
        return original(**kwargs)

    monkeypatch.setattr(openimcc_bridge, "evaluate", track_evaluate)
    _patch_activity(monkeypatch, violated=False)
    backend = _OpenImccBatteryBackend("openimcc")
    pot = _scoring_pot("kambayashi_1985_feto_p2o5_s01")
    composition_kg, composition_mol = composition_kg_and_mol(pot.composition_wt_pct)
    result = backend.equilibrate(
        1643.0 - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=-9.0,
    )
    assert seen
    assert "P2O5" not in seen[0]
    assert "Fe2O3" not in seen[0]
    assert "FeO" in seen[0]
    assert result.activity_coefficients["P2O5"] == pytest.approx(0.01)
    assert "P2O5" not in result.reported_activity_coefficients
    reference = result.activities_standard_state
    assert reference["phase"] == "liquid"
    assert reference["convention"] == "raoultian_pure_endmember"
    assert reference["reference_pressure_bar"] == pytest.approx(1.0)
    assert reference["component"] == "P2O5"
    assert reference["activity"] == pytest.approx(0.01)
    assert reference["gamma"] == pytest.approx(4.0)
    assert reference["authority_status"] == "flagged"
    assert "3.5" in reference["reconciliation"]
    assert "1.01325" in reference["reconciliation"]
    assert "hypothetical-liquid" in reference["reconciliation"]
    codes = [notice.get("code") for notice in result.imcc_notices]
    assert "imcc_p2o5_activity_domain_notice" in codes
    assert "p2o5_feiii_phosphate_chemistry_not_modelled" in codes

    cell = equilibrate_cell(
        EngineHandle(
            name="openimcc",
            backend=backend,
            available=True,
            unavailable_reason=None,
            takes_fo2=True,
            supports_intrinsic_fo2=False,
        ),
        pot.as_binary_pot(),
        temperature_K=1643.0,
        po2=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0e-9),
    )
    assert cell.status == "ok"
    assert cell.melt_activities["P2O5"] == pytest.approx(0.01)
    assert cell.activities_standard_state["phase"] == "liquid"
    assert cell.activities_standard_state["component"] == "P2O5"
    assert "3.5" in cell.activities_standard_state["reconciliation"]


def test_solid_p2o5_reference_is_not_scored_against_the_liquid_activity() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EquilibrateCell,
        Po2Request,
    )
    from simulator.diagnostic_helpers.binary_pot_scoring import (
        ActivityComparator,
        _comparator_envelope,
    )

    pot = _scoring_pot("kambayashi_1985_feto_p2o5_s01")
    cell = EquilibrateCell(
        pot_id=pot.pot_id,
        engine="openimcc",
        temperature_K=1643.0,
        po2=Po2Request(mode="commanded", po2_bar=1.0e-9),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason="",
        melt_activities={"P2O5": 0.01},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        activities_standard_state={
            "phase": "liquid",
            "convention": "raoultian_pure_endmember",
            "reference_pressure_bar": 1.0,
            "component": "P2O5",
        },
    )

    def comparator(standard_state: str) -> ActivityComparator:
        return ActivityComparator(
            source_id="kems-057-kambayashi-1985",
            observation_id="kambayashi_1985_table4_feto_p2o5_compositions",
            species="P2O5",
            observable="activity",
            method_class="measured",
            sample_no=1,
            temperature_K=1643.0,
            measured=1.0e-4,
            units="1",
            standard_state=standard_state,
            uncertainty=None,
            doi="10.2355/tetsutohagane1955.71.16_1911",
            row={"field": "a_P2O5"},
        )

    def envelope(**kwargs):
        return kwargs

    solid = _comparator_envelope(
        pot=pot,
        cell=cell,
        comparator=comparator("P2O5(s)"),
        envelope=envelope,
        rail_for=lambda *_args: "activity",
    )
    assert solid["score_allowed"] is False
    assert any("liquid-reference" in str(notice) for notice in solid["notices"])
    liquid = _comparator_envelope(
        pot=pot,
        cell=cell,
        comparator=comparator("P2O5(l)"),
        envelope=envelope,
        rail_for=lambda *_args: "activity",
    )
    assert liquid["score_allowed"] is True
    assert liquid["predicted"] == pytest.approx(0.01)


def test_hashimoto_assesses_before_evaluate_and_cleans_only_p_bearing_melts(
    monkeypatch,
) -> None:
    import copy

    import simulator.battery.residue as residue
    from simulator.melt_backend import openimcc_bridge
    from tests.battery.test_hashimoto_residue_predictions import _source_data

    experiments, _measured, preform = _source_data()
    catalog_path = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "data"
        / "vapor_pressures.yaml"
    )
    import yaml

    catalog = yaml.safe_load(catalog_path.read_text())
    real_assess = openimcc_bridge.assess_p2o5_extension

    def _run(experiment, *, expect_cleaned: bool):
        order: list[str] = []
        evaluated: list[dict[str, float]] = []
        inventories: list[dict[str, float]] = []

        def track_assess(composition, temperature_K=None, **kwargs):
            order.append("assess")
            return real_assess(composition, temperature_K, **kwargs)

        def track_isolated(evaluate, *, timeout_s, **kwargs):
            del timeout_s
            order.append("evaluate")
            evaluated.append(dict(kwargs["composition_mol"]))
            return evaluate(**kwargs)

        def integrate(inventory, _channels, pressure_model, **_kwargs):
            positive = {
                str(oxide): float(amount)
                for oxide, amount in inventory.items()
                if float(amount) > 0.0
            }
            inventories.append(positive)
            pressure_model(positive, -10.0)
            values = residue._hashimoto_project_oxide_wt_pct(inventory)
            return values, (1.0e-9,), (), None, {}

        monkeypatch.setattr(openimcc_bridge, "assess_p2o5_extension", track_assess)
        monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", track_isolated)
        monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", integrate)
        residue._predict_hashimoto_residue_cohort(
            [experiment],
            catalog,
            preform_dimensions_mm=preform,
            code_revision="t1430-r1-order",
            _allow_partial_cohort_for_test=True,
        )
        assert order[0] == "assess"
        assert "evaluate" in order
        assert order.index("assess") < order.index("evaluate")
        assert evaluated
        inventory = inventories[0]
        if expect_cleaned:
            assert "P2O5" in inventory
            assert "P2O5" not in evaluated[0]
            assert set(evaluated[0]) == set(inventory) - {"P2O5"}
        else:
            assert "P2O5" not in inventory
            assert set(evaluated[0]) == set(inventory)

    _run(experiments[0], expect_cleaned=False)

    phosphorus = copy.deepcopy(experiments[0])
    phosphorus["sample"]["printed_composition"]["state"]["value"]["P2O5"] = 0.2
    _patch_activity(monkeypatch, violated=False)
    _run(phosphorus, expect_cleaned=True)


def test_oxygen_balance_uses_one_parent_set_for_inert_and_reactive_cells(
    monkeypatch,
) -> None:
    """Metadata and the pressure model share one parent tuple, including a refusal."""

    import openimcc
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_OXYGEN_BALANCE_EFFUSION,
        Po2Request,
        _OpenImccBatteryBackend,
    )

    _patch_activity(monkeypatch, violated=False)
    composition_mol = {
        "SiO2": 0.50,
        "MgO": 0.20,
        "FeO": 0.10,
        "CaO": 0.10,
        "Al2O3": 0.10,
        "P2O5": 0.002,
    }
    metadata_parents: list[tuple[str, ...]] = []
    gas_parents: list[tuple[str, ...]] = []
    balance_parents: list[tuple[str, ...]] = []
    refuse = {"gas": False}

    real_channels = battery._openimcc_gas_channels_and_omission_notices

    def tracking_channels(parent_oxides, datapack, gas_result=None):
        metadata_parents.append(tuple(parent_oxides))
        return real_channels(parent_oxides, datapack, gas_result)

    def evaluate_gas(activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs):
        del activities, temperature_K, fo2, datapack, kwargs
        oxides = tuple(parent_oxides or ())
        gas_parents.append(oxides)
        if refuse["gas"] and "P2O5" in oxides:
            raise ValueError("unknown parent P2O5")
        return _PGas({"SiO": 1.0e-8, "O2": 1.0e-8, "PO": _FLUX_PARTIAL_BAR})

    def evaluate_balance(activities, temperature_K, datapack, parent_oxides=None, **kwargs):
        del activities, temperature_K, datapack, kwargs
        oxides = tuple(parent_oxides or ())
        balance_parents.append(oxides)
        if refuse["gas"] and "P2O5" in oxides:
            raise ValueError("unknown parent P2O5")
        return (
            1.0e-9,
            _PGas({"SiO": 1.0e-8, "O2": 1.0e-8, "PO": _FLUX_PARTIAL_BAR}),
            {
                "residual": 0.0,
                "bracket": [-12.0, -6.0],
                "dominant_O_carriers": [],
                "dominant_metal_carriers": [],
            },
        )

    def fake_solver(model, metadata, bracket=None):
        del bracket
        model(1.0e-9)
        return (
            1.0e-9,
            {name: 1.0e-8 for name in metadata},
            {
                "residual": 0.0,
                "bracket": [-12.0, -6.0],
                "dominant_O_carriers": [],
                "dominant_metal_carriers": [],
            },
        )

    monkeypatch.setattr(
        battery, "_openimcc_gas_channels_and_omission_notices", tracking_channels
    )
    monkeypatch.setattr(
        battery,
        "_cell_oxide_thermodynamics",
        lambda material, temperature_K: ({}, None, {}),
    )
    monkeypatch.setattr(openimcc, "evaluate_gas", evaluate_gas)
    monkeypatch.setattr(
        openimcc, "evaluate_gas_oxygen_balance", evaluate_balance, raising=False
    )
    monkeypatch.setattr(openimcc, "oxygen_balance_from_pressure_model", fake_solver)
    backend = _OpenImccBatteryBackend("openimcc")

    def equilibrate(cell_material):
        metadata_parents.clear()
        gas_parents.clear()
        balance_parents.clear()
        return backend.equilibrate(
            1400.0,
            composition_mol=composition_mol,
            po2_request=Po2Request(
                mode=PO2_OXYGEN_BALANCE_EFFUSION,
                po2_bar=None,
                cell_material=cell_material,
            ),
        )

    def gas_codes(result):
        return [
            notice.get("code")
            for notice in result.imcc_notices
            if notice.get("kind") == "openimcc_p2o5_gas"
        ]

    refuse["gas"] = False
    inert = equilibrate(None)
    assert inert.status == "ok"
    assert balance_parents
    assert all("P2O5" in oxides for oxides in balance_parents)
    assert metadata_parents
    assert all(oxides == balance_parents[-1] for oxides in metadata_parents)
    assert "openimcc_p2o5_gas_unavailable" not in gas_codes(inert)

    reactive = equilibrate("W")
    assert reactive.status == "ok"
    assert gas_parents
    assert all("P2O5" in oxides for oxides in gas_parents)
    assert metadata_parents
    assert all(oxides == gas_parents[-1] for oxides in metadata_parents)
    assert "openimcc_p2o5_gas_unavailable" not in gas_codes(reactive)

    refuse["gas"] = True
    inert_refused = equilibrate(None)
    assert inert_refused.status == "ok"
    assert any("P2O5" in oxides for oxides in balance_parents)
    assert all("P2O5" not in oxides for oxides in metadata_parents)
    assert metadata_parents[0] == balance_parents[-1]
    assert gas_codes(inert_refused).count("openimcc_p2o5_gas_unavailable") == 1

    reactive_refused = equilibrate("W")
    assert reactive_refused.status == "ok"
    assert gas_parents[0] and "P2O5" in gas_parents[0]
    assert all("P2O5" not in oxides for oxides in gas_parents[1:])
    assert metadata_parents
    assert all("P2O5" not in oxides for oxides in metadata_parents)
    assert all(oxides == gas_parents[-1] for oxides in metadata_parents)
    assert gas_codes(reactive_refused).count("openimcc_p2o5_gas_unavailable") == 1


def test_campaign_start_and_reset_record_p2o5_survival() -> None:
    from pathlib import Path

    from simulator.config import load_config_bundle
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import InternalAnalyticalBackend
    from simulator.state import CampaignPhase

    bundle = load_config_bundle(Path(__file__).resolve().parents[1] / "data")
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
    )
    mass_kg = 0.01 * _P2O5_MOLAR_MASS_G / 1000.0
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.melt.composition_kg["P2O5"] = mass_kg
    sim.start_campaign(CampaignPhase.C0)
    assert sim.p2o5_survived_stage0_cleanup() is None
    sim.melt.composition_kg["P2O5"] = mass_kg
    sim.start_campaign(CampaignPhase.C2A)
    recorded = sim.p2o5_survived_stage0_cleanup()
    assert recorded is not None
    assert recorded["code"] == "P survived Stage-0 cleanup"
    assert recorded["p2o5_mass_kg"] == pytest.approx(mass_kg)
    assert recorded["left_campaign"] == "C0"
    assert recorded["entered_campaign"] == "C2A"
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert sim.p2o5_survived_stage0_cleanup() is None


def test_p_bearing_success_path_pins_parent_solve_activity_pressures_and_gas_notice(
    monkeypatch,
) -> None:
    """Pin the 6576dfbde P-bearing success outputs.

    Kambayashi Fe-tO/P2O5 s01 at 1643 K, commanded log fO2 = -9. The
    patched activity and gas result are inputs. The cleaned parent
    moles, liquid reference, pascal pressures, and converted gas notice
    are that production resolver's outputs.
    """

    import openimcc
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
        composition_kg_and_mol,
    )
    from simulator.melt_backend import openimcc_bridge

    _patch_activity(monkeypatch, violated=False)
    species = ("P", "P2", "P4", "PO", "PO2", "P4O6", "P4O10")

    def evaluate_gas(
        activities, temperature_K, fo2, datapack, parent_oxides=None, **kwargs
    ):
        del activities, temperature_K, fo2, datapack, parent_oxides, kwargs
        return _PGas(
            {name: _FLUX_PARTIAL_BAR for name in species},
            notices=(
                SimpleNamespace(
                    code="p_gas_exceeds_one_bar_complete_volatilisation",
                    message=_ONE_BAR_MESSAGE,
                    species=species,
                    total_pressure_bar=1.4,
                ),
            ),
        )

    monkeypatch.setattr(openimcc, "evaluate_gas", evaluate_gas)
    seen: list[dict[str, float]] = []
    original = openimcc_bridge.evaluate

    def track_evaluate(**kwargs):
        seen.append(dict(kwargs["composition_mol"]))
        return original(**kwargs)

    monkeypatch.setattr(openimcc_bridge, "evaluate", track_evaluate)
    pot = _scoring_pot("kambayashi_1985_feto_p2o5_s01")
    composition_kg, composition_mol = composition_kg_and_mol(
        pot.composition_wt_pct
    )
    result = _OpenImccBatteryBackend("openimcc").equilibrate(
        1643.0 - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        fO2_log=-9.0,
    )
    assert seen == [{"FeO": 13.560375262004346}]
    assert result.status == "ok"
    assert result.activity_coefficients["P2O5"] == 0.01
    assert "P2O5" not in result.reported_activity_coefficients
    assert result.activities_standard_state == {
        "convention": "raoultian_pure_endmember",
        "phase": "liquid",
        "reference_pressure_bar": 1.0,
        "reference_temperature_K": None,
        "component_basis": "raoultian_pure_endmember",
        "component": "P2O5",
        "activity": 0.01,
        "regression": "Suito-Inoue-1984-Eq-14-no-Mn",
        "reconciliation": (
            "openimcc a(P2O5) is the Turkdogan-Pearson hypothetical-liquid "
            "activity at 1 atm after the published shift 3.5*R*T*ln(1.01325) "
            "onto the gas-pack Jung-Hudon 1 bar standard. That shifted value "
            "is the Raoultian pure-liquid P2O5 activity declared by the CEA "
            "carrier activity_input. It is not an unshifted Turkdogan gamma "
            "and not pure-endmember a=1."
        ),
        "authority_status": "flagged",
        "limitations_warning": (
            "Fe-P alloy partitioning under carbon reduction is not modelled; "
            "metallic iron belongs to the simulator metal account. "
            "Fe(III) phosphates are not modelled. "
            "Fe(III) phosphate chemistry is not modelled."
        ),
        "gamma": 4.0,
    }
    assert result.vapor_pressures_Pa == {name: 20000.0 for name in species}
    assert [
        notice
        for notice in result.imcc_notices
        if notice.get("kind") == "openimcc_p2o5_gas"
    ] == [
        {
            "kind": "openimcc_p2o5_gas",
            "authority": None,
            "code": "p_gas_exceeds_one_bar_complete_volatilisation",
            "reason": _ONE_BAR_MESSAGE,
            "notice": {
                "code": "p_gas_exceeds_one_bar_complete_volatilisation",
                "message": _ONE_BAR_MESSAGE,
                "species": list(species),
                "total_pressure_bar": 1.4,
            },
        }
    ]
