"""Engine out-of-band flags survive the battery cell into scoring.

The cell stores the engine's own notice once. Scoring reads that cell.
Predicted numbers stay the engine's numbers: an in-band run is not flagged,
and attaching the flag does not move the value.
"""

from __future__ import annotations

import types
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest

import simulator.melt_backend.vaporock as vaporock_module
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    NoticeKind,
    Phase,
    Quantity,
)
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import (
    candidate_observation,
    cell_notices,
    composition_wt_pct,
    predict_with_engine,
)
from simulator.diagnostic_helpers.binary_pot_battery import (
    AUTHORITY_EXTRAPOLATED,
    BinaryPot,
    EngineHandle,
    PO2_COMMANDED,
    Po2Request,
    equilibrate_cell,
    extract_reported_quantities,
    composition_kg_and_mol,
    engine_flags_from_result,
    identity_battery_pot,
    open_battery_engine,
    cell_score_authority,
)
from simulator.diagnostic_helpers.binary_pot_scoring import (
    ActivityComparator,
    ScoringPot,
    _cell_envelope,
    _comparator_envelope,
)
from simulator.melt_backend.vaporock import VapoRockBackend
from tests.battery import factories as F

_HOT_K = 1773.15
_IN_BAND_K = 1673.15
_PO2 = Po2Request(mode="engine_default", po2_bar=None)


def _capture_envelope(**kwargs):
    return kwargs


def _rail(*_args, **_kwargs):
    return "melt activities"


def _install_fake_vaporock(monkeypatch):
    def calc_vapor_pressures(composition=None, T_C=None, **_kwargs):
        composition = composition or {}
        return {
            "Na": float(composition.get("Na2O", 0.0)) * (float(T_C) + 1.0) * 1e-9,
            "SiO": float(composition.get("SiO2", 0.0)) * 1e-9,
        }

    def fake_import_module(name):
        if name == "vaporock":
            return types.SimpleNamespace(calc_vapor_pressures=calc_vapor_pressures)
        raise ImportError(name)

    monkeypatch.setattr(vaporock_module.importlib, "import_module", fake_import_module)


def _vaporock_handle() -> EngineHandle:
    backend = VapoRockBackend()
    assert backend.initialize({"warm_worker": False}) is True
    return EngineHandle(
        name="vaporock",
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )


def _basalt_pot() -> BinaryPot:
    return BinaryPot(
        pot_id="sio2_47_basalt",
        kato_1993_table4_system=None,
        why="SiO2 inside the certified window; only T can flag",
        composition_wt_pct={"SiO2": 47.0, "Na2O": 53.0},
    )


def _vaporock_cell(handle: EngineHandle, temperature_K: float):
    return equilibrate_cell(
        handle,
        _basalt_pot(),
        temperature_K=temperature_K,
        po2=_PO2,
        isolated=False,
    )


def _mgo_sio2_pot() -> BinaryPot:
    return BinaryPot(
        pot_id="mgo_sio2_40_60",
        kato_1993_table4_system="MgO-SiO2",
        why="IMCC parent basis",
        composition_wt_pct={"SiO2": 60.0, "MgO": 40.0},
    )


def _openimcc_cell(handle: EngineHandle, temperature_K: float):
    return equilibrate_cell(
        handle,
        _mgo_sio2_pot(),
        temperature_K=temperature_K,
        po2=_PO2,
        isolated=False,
    )


def _scoring_pot(pot: BinaryPot, temperature_K: float) -> ScoringPot:
    return ScoringPot(
        pot_id=pot.pot_id,
        source_id="engine-flag",
        observation_id=pot.pot_id,
        sample_no=None,
        temperatures_K=(temperature_K,),
        composition_basis="wt_pct",
        composition_conversion="as_weighed",
        composition_as_printed=dict(pot.composition_wt_pct),
        composition_wt_pct=dict(pot.composition_wt_pct),
        why=pot.why,
    )


def _activity_comparator(pot: ScoringPot) -> ActivityComparator:
    return ActivityComparator(
        source_id=pot.source_id,
        observation_id=pot.observation_id,
        species="SiO2",
        observable="activity",
        method_class="measured",
        sample_no=None,
        temperature_K=pot.temperatures_K[0],
        measured=0.3,
        units="1",
        standard_state="liquid",
        uncertainty=None,
        doi=None,
        row={},
    )


def test_vaporock_1773k_cell_keeps_extrapolated_flag(monkeypatch) -> None:
    _install_fake_vaporock(monkeypatch)
    handle = _vaporock_handle()
    hot = _vaporock_cell(handle, _HOT_K)
    warm = _vaporock_cell(handle, _IN_BAND_K)

    assert hot.status == "ok"
    assert hot.authority == AUTHORITY_EXTRAPOLATED
    assert hot.certified_band is not None
    t_lo, t_hi = hot.certified_band["temperature_K"]
    assert t_lo <= _IN_BAND_K <= t_hi
    assert _HOT_K > t_hi
    commissioning = [
        row for row in hot.notices if row.get("kind") == "engine_commissioning"
    ]
    assert len(commissioning) == 1
    assert commissioning[0]["authority"] == AUTHORITY_EXTRAPOLATED
    assert "temperature_range" in commissioning[0]["failed_constraints"]
    assert hot.gas_partial_pressures_Pa["Na"] > 0.0
    assert hot.gas_partial_pressures_Pa["SiO"] > 0.0
    assert hot.gas_partial_pressures_Pa != warm.gas_partial_pressures_Pa

    assert warm.status == "ok"
    assert warm.authority is None
    assert warm.certified_band is None
    assert not any(row.get("kind") == "engine_commissioning" for row in warm.notices)

    kg, mol = composition_kg_and_mol(_basalt_pot().composition_wt_pct)
    direct = handle.backend.equilibrate(
        temperature_C=_HOT_K - 273.15,
        composition_kg=kg,
        composition_mol=mol,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
    )
    direct_activities, direct_pressures, invalid_pressures = (
        extract_reported_quantities(direct)
    )
    assert invalid_pressures == ()
    assert hot.gas_partial_pressures_Pa == direct_pressures
    assert hot.melt_activities == direct_activities

    real_assess = vaporock_module.assess_engine_commissioning
    monkeypatch.setattr(
        vaporock_module,
        "assess_engine_commissioning",
        lambda *args, **kwargs: types.SimpleNamespace(notice=None),
    )
    try:
        bare = _vaporock_cell(handle, _HOT_K)
    finally:
        monkeypatch.setattr(
            vaporock_module, "assess_engine_commissioning", real_assess
        )
    assert bare.gas_partial_pressures_Pa == hot.gas_partial_pressures_Pa
    assert bare.melt_activities == hot.melt_activities
    assert bare.authority is None
    assert not any(row.get("kind") == "engine_commissioning" for row in bare.notices)

    scored = cell_notices(Quantity.P_PARTIAL, Engine.VAPOROCK, hot)
    assert [notice.kind for notice in scored] == [NoticeKind.OUT_OF_CERTIFIED_BAND]
    assert cell_notices(Quantity.P_PARTIAL, Engine.VAPOROCK, bare) == ()
    assert cell_notices(Quantity.P_PARTIAL, Engine.VAPOROCK, warm) == ()


def test_openimcc_temperature_notice_survives_cell_notices() -> None:
    handle = open_battery_engine("openimcc")
    handle.backend._gas = None
    handle.backend._gas_error = "test-only unavailable gas tables"
    assert handle.available is True
    hot = _openimcc_cell(handle, 800.0)
    warm = _openimcc_cell(handle, 1700.0)
    kg, mol = composition_kg_and_mol(_mgo_sio2_pot().composition_wt_pct)
    direct = handle.backend.equilibrate(
        temperature_C=800.0 - 273.15,
        composition_kg=kg,
        composition_mol=mol,
        fO2_log=-9.0,
        pressure_bar=1.0e-6,
    )
    direct_activities, direct_pressures, invalid_pressures = (
        extract_reported_quantities(direct)
    )
    assert invalid_pressures == ()

    assert hot.status == "ok"
    assert hot.authority == AUTHORITY_EXTRAPOLATED
    assert hot.melt_activities == direct_activities
    assert hot.gas_partial_pressures_Pa == direct_pressures
    assert hot.melt_activities["SiO2"] > 0.0
    kinds = [row.get("kind") for row in hot.notices]
    assert kinds.count("openimcc_temperature_extrapolated") == 1
    assert len(kinds) == len(set(kinds))
    scored = cell_notices(Quantity.ACTIVITY, Engine.OPENIMCC, hot)
    band = [notice for notice in scored if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND]
    assert len(band) == 1
    assert "T outside openimcc datapack" in band[0].reason

    assert warm.status == "ok"
    assert warm.authority is None
    assert warm.melt_activities["SiO2"] != hot.melt_activities["SiO2"]
    warm_kinds = [row.get("kind") for row in warm.notices]
    assert "openimcc_gas_unavailable" in warm_kinds
    assert "openimcc_temperature_extrapolated" not in warm_kinds
    warm_cell_notices = cell_notices(Quantity.ACTIVITY, Engine.OPENIMCC, warm)
    engine_text_rows = [
        row
        for row in warm.notices
        if row.get("kind") in {"openimcc_flag", "openimcc_notice"}
    ]
    engine_caveats = [
        notice
        for notice in warm_cell_notices
        if notice.kind.value == "engine_model_caveat"
    ]
    assert engine_text_rows
    assert [notice.reason for notice in engine_caveats] == [
        row["reason"] for row in engine_text_rows
    ]
    unavailable_reason = next(
        row["reason"]
        for row in warm.notices
        if row.get("kind") == "openimcc_gas_unavailable"
    )
    assert any(
        notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        and notice.reason == unavailable_reason
        for notice in warm_cell_notices
    )
    assert not any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in warm_cell_notices
    )


def test_nested_provider_notice_stays_on_its_diagnostic_result() -> None:
    handle = open_battery_engine("internal-analytical")
    try:
        outputs = {}
        for fO2_log in (-12.0, -8.0, -6.17939589339):
            core = handle.backend._core
            core.melt.oxygen_reservoir.melt_intrinsic_fO2_log = fO2_log
            core.melt.melt_fO2_log = fO2_log
            result = handle.backend.equilibrate(
                temperature_C=1605.0,
                composition_kg={"FeO": 0.2, "Na2O": 0.2, "SiO2": 0.6},
                composition_mol={
                    "FeO": 2.7838093647,
                    "Na2O": 3.2269234585,
                    "SiO2": 9.9861857763,
                },
                fO2_log=fO2_log,
                pressure_bar=1e-6,
                po2_request=Po2Request(
                    mode=PO2_COMMANDED,
                    po2_bar=10.0**fO2_log,
                ),
            )
            diagnostic = result.diagnostics["vapor_pressure_diagnostic"][
                "a_FeO_calphad"
            ]
            flags, authority, _band = engine_flags_from_result(result)
            cell = types.SimpleNamespace(notices=flags, authority=authority)
            outputs[fO2_log] = (
                diagnostic,
                cell,
                result,
            )

        reducing_result = outputs[-12.0][2]
        from simulator import fe_redox

        original_gamma_band = fe_redox.holzheid_stoich_feo_gamma_band

        def changed_gamma_band(temperature_K):
            band = dict(original_gamma_band(temperature_K))
            band["central"] = 1.5
            return band

        core = handle.backend._core
        core.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -12.0
        core.melt.melt_fO2_log = -12.0
        with patch.object(
            fe_redox,
            "holzheid_stoich_feo_gamma_band",
            changed_gamma_band,
        ):
            mutated_result = handle.backend.equilibrate(
                temperature_C=1605.0,
                composition_kg={"FeO": 0.2, "Na2O": 0.2, "SiO2": 0.6},
                composition_mol={
                    "FeO": 2.7838093647,
                    "Na2O": 3.2269234585,
                    "SiO2": 9.9861857763,
                },
                fO2_log=-12.0,
                pressure_bar=1e-6,
                po2_request=Po2Request(
                    mode=PO2_COMMANDED,
                    po2_bar=1.0e-12,
                ),
            )
        mutated_diagnostic = mutated_result.diagnostics[
            "vapor_pressure_diagnostic"
        ]["a_FeO_calphad"]
        core.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -8.0
        core.melt.melt_fO2_log = -8.0
        with patch.object(
            fe_redox,
            "holzheid_stoich_feo_gamma_band",
            changed_gamma_band,
        ):
            mutated_eight_result = handle.backend.equilibrate(
                temperature_C=1605.0,
                composition_kg={"FeO": 0.2, "Na2O": 0.2, "SiO2": 0.6},
                composition_mol={
                    "FeO": 2.7838093647,
                    "Na2O": 3.2269234585,
                    "SiO2": 9.9861857763,
                },
                fO2_log=-8.0,
                pressure_bar=1e-6,
                po2_request=Po2Request(
                    mode=PO2_COMMANDED,
                    po2_bar=1.0e-8,
                ),
            )
    finally:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            close_battery_engine_handle,
        )

        close_battery_engine_handle(handle)

    reducing_diag, reducing_cell, reducing_result = outputs[-12.0]
    assert reducing_diag["authority"]["calphad_weight"] == 1.0
    reason = "Holzheid stoichiometric FeO gamma conversion extrapolated"
    assert reducing_diag["gamma_FeO"]["holzheid"]["notice"]["reason"] == reason
    assert reducing_diag["affected_species"] == ["FeO"]
    assert reducing_diag["affected_quantities"] == []
    assert reducing_diag["a_FeO_authoritative"] == pytest.approx(
        0.4837615049051221
    )
    assert not any(row.get("reason") == reason for row in reducing_cell.notices)
    assert mutated_diagnostic["a_FeO_authoritative"] == pytest.approx(
        0.2578487477042083
    )
    assert mutated_diagnostic["gamma_FeO"]["holzheid"]["notice"]["reason"] == reason
    assert dict(reducing_result.vapor_pressures_Pa) == dict(
        mutated_result.vapor_pressures_Pa
    )
    assert reducing_result.vapor_pressures_Pa["Fe"] == pytest.approx(
        16.38797577954487
    )
    reducing_provenance = reducing_result.diagnostics[
        "vapor_pressure_diagnostic"
    ]["vapor_pressure_numerator_provenance"]["Fe"]
    assert reducing_provenance["activity_factor"] == 1.0
    assert reducing_provenance["raw_metal_activity_root"] > 1.0
    assert not any(
        notice.reason == reason
        for notice in cell_notices(
            Quantity.P_PARTIAL,
            Engine.INTERNAL_ANALYTICAL,
            reducing_cell,
            species="Fe",
        )
    )
    assert cell_score_authority(
        reducing_cell,
        refused=False,
        quantity=Quantity.P_PARTIAL.value,
        species="Fe",
    ) != AUTHORITY_EXTRAPOLATED
    reducing_flags, reducing_authority, _ = engine_flags_from_result(
        reducing_result
    )
    assert not any(
        notice.reason
        == "Holzheid stoichiometric FeO gamma conversion extrapolated"
        for notice in cell_notices(
            Quantity.ACTIVITY,
            Engine.INTERNAL_ANALYTICAL,
            types.SimpleNamespace(
                notices=reducing_flags,
                authority=reducing_authority,
            ),
        )
    )

    oxidizing_diag, oxidizing_cell, _oxidizing_result = outputs[-6.17939589339]
    assert oxidizing_diag["authority"]["calphad_weight"] == 0.0
    assert oxidizing_diag["a_FeO_authoritative"] == pytest.approx(
        oxidizing_diag["a_FeO_kress91"]
    )
    assert not any(
        notice.reason
        == "Holzheid stoichiometric FeO gamma conversion extrapolated"
        for notice in cell_notices(
            Quantity.P_PARTIAL,
            Engine.INTERNAL_ANALYTICAL,
            oxidizing_cell,
            species="Fe",
        )
    )

    dependent_diag, dependent_cell, dependent_result = outputs[-8.0]
    dependent_provenance = dependent_result.diagnostics[
        "vapor_pressure_diagnostic"
    ]["vapor_pressure_numerator_provenance"]["Fe"]
    dependent_flags = [
        row for row in dependent_cell.notices if row.get("reason") == reason
    ]
    assert dependent_diag["authority"]["calphad_weight"] == pytest.approx(
        0.8206041066095899
    )
    assert dependent_provenance["activity_basis"] == "kress91_ferrous"
    assert dependent_provenance["activity_factor"] < 1.0
    assert dependent_flags
    assert dependent_flags[0]["affected_species"] == ["Fe"]
    assert dependent_flags[0]["affected_quantities"] == ["p_partial"]
    assert dependent_result.vapor_pressures_Pa["Fe"] == pytest.approx(
        3.238872664559105
    )
    assert mutated_eight_result.vapor_pressures_Pa["Fe"] == pytest.approx(
        1.835371971238782
    )
    assert {
        key: value
        for key, value in dependent_result.vapor_pressures_Pa.items()
        if key != "Fe"
    } == {
        key: value
        for key, value in mutated_eight_result.vapor_pressures_Pa.items()
        if key != "Fe"
    }

    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("FeO", Decimal("0.2")),
            ("Na2O", Decimal("0.2")),
            ("SiO2", Decimal("0.6")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    fe_identity = replace(
        F.activity_identity(
            formula="FeO",
            T_K=Decimal("1878.15"),
            component_basis="FeO",
            composition=composition,
            fO2_Pa=Decimal("1e-7"),
        ),
        quantity=Quantity.P_PARTIAL,
        species=Species("Fe", Phase.G),
    )
    fe_observation = F.observation(
        "fe-pressure-holzheid", "holzheid-scope", fe_identity, Decimal("0.4")
    )
    from simulator.engine_cache import _without_engine_cache

    with _without_engine_cache():
        fe_prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL, fe_observation, isolated=False
        )
        with patch.object(
            fe_redox,
            "holzheid_stoich_feo_gamma_band",
            changed_gamma_band,
        ):
            mutated_fe_prediction = predict_with_engine(
                Engine.INTERNAL_ANALYTICAL, fe_observation, isolated=False
            )
    assert fe_prediction.value == Decimal("16.38797577954487")
    assert mutated_fe_prediction.value == fe_prediction.value
    assert fe_prediction.authority is not Authority.EXTRAPOLATED
    assert mutated_fe_prediction.authority is not Authority.EXTRAPOLATED
    assert not any(notice.reason == reason for notice in fe_prediction.notices)
    assert not any(notice.reason == reason for notice in mutated_fe_prediction.notices)

    dependent_fe_identity = replace(
        fe_identity, fO2_Pa=State.of(Decimal("1e-3"))
    )
    dependent_fe_observation = F.observation(
        "fe-pressure-holzheid-dependent",
        "holzheid-scope",
        dependent_fe_identity,
        Decimal("0.4"),
    )
    with _without_engine_cache():
        dependent_prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            dependent_fe_observation,
            isolated=False,
        )
        with patch.object(
            fe_redox,
            "holzheid_stoich_feo_gamma_band",
            changed_gamma_band,
        ):
            mutated_dependent_prediction = predict_with_engine(
                Engine.INTERNAL_ANALYTICAL,
                dependent_fe_observation,
                isolated=False,
            )
    assert dependent_prediction.value == Decimal("3.736980566273724")
    assert mutated_dependent_prediction.value == Decimal("2.1176347756593503")
    assert any(notice.reason == reason for notice in dependent_prediction.notices)
    assert any(
        notice.reason == reason for notice in mutated_dependent_prediction.notices
    )
    assert dependent_prediction.authority is Authority.EXTRAPOLATED
    assert mutated_dependent_prediction.authority is Authority.EXTRAPOLATED


def test_scoped_pressure_notices_match_canonical_gas_species_keys() -> None:
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("SiO2", Decimal("0.6")),
            ("Na2O", Decimal("0.4")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    for formula, engine_key, expected in (
        ("Na2O(g)", "Na2O_gas", Decimal("2235784.686743086")),
        ("SiO2(g)", "SiO2_gas", Decimal("1526204.8526738302")),
        ("Na", "Na", None),
    ):
        identity = replace(
            F.activity_identity(
                formula=formula,
                T_K=Decimal("4500"),
                component_basis=formula.removesuffix("(g)"),
                composition=composition,
                fO2_Pa=Decimal("1e-5"),
            ),
            quantity=Quantity.P_PARTIAL,
            species=Species(formula, Phase.G),
        )
        prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            F.observation(
                f"gas-flag-{formula}", "gas-flag", identity, Decimal("1")
            ),
            isolated=False,
        )
        if expected is not None:
            assert prediction.value == expected
        assert prediction.authority is Authority.EXTRAPOLATED
        assert any(
            notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            and f":{engine_key}:" in notice.reason
            for notice in prediction.notices
        )


def test_openimcc_cell_notice_record_kinds_are_classified_separately() -> None:
    rows = (
        {"kind": "openimcc_flag", "reason": "liquid_only_model"},
        {"kind": "openimcc_notice", "reason": "metastable FeO1.5 branch"},
        {
            "kind": "openimcc_gas_unavailable",
            "reason": "openimcc gas datapack unavailable",
        },
        {
            "kind": "openimcc_flag",
            "authority": AUTHORITY_EXTRAPOLATED,
            "reason": "T outside openimcc datapack domain",
        },
    )
    scored = cell_notices(
        Quantity.ACTIVITY,
        Engine.OPENIMCC,
        types.SimpleNamespace(notices=rows),
    )

    assert [(notice.kind.value, notice.reason) for notice in scored] == [
        ("engine_model_caveat", "liquid_only_model"),
        ("engine_model_caveat", "metastable FeO1.5 branch"),
        ("source_disagreement", "openimcc gas datapack unavailable"),
        ("out_of_certified_band", "T outside openimcc datapack domain"),
    ]


def test_qualification_band_notice_is_stable_to_mapping_order() -> None:
    first_band = {
        "sio2_wt_pct": [30.0, 80.0],
        "temperature_K": [1073.15, 1700.0],
        "sio2_crash_floor_wt_pct": 34.0,
        "citations": {
            "sio2_band": "domain band",
            "temperature_K": "temperature source",
        },
    }
    reordered_band = {
        "citations": {
            "temperature_K": "temperature source",
            "sio2_band": "domain band",
        },
        "sio2_crash_floor_wt_pct": 34.0,
        "temperature_K": [1073.15, 1700.0],
        "sio2_wt_pct": [30.0, 80.0],
    }

    def scored_band(band):
        scored = cell_notices(
            Quantity.ACTIVITY,
            Engine.ALPHAMELTS,
            types.SimpleNamespace(
                notices=[
                    {
                        "kind": "melts_domain_gate",
                        "certified_band": band,
                        "gate_reason": "outside certified band",
                    }
                ]
            ),
        )
        notice = next(
            item for item in scored if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        )
        return notice.band

    assert scored_band(first_band) == scored_band(reordered_band)


def test_scoring_envelope_uses_cell_flag() -> None:
    handle = open_battery_engine("openimcc")
    handle.backend._gas = None
    handle.backend._gas_error = "test-only unavailable gas tables"
    hot = _openimcc_cell(handle, 800.0)
    warm = _openimcc_cell(handle, 1700.0)
    hot_pot = _scoring_pot(_mgo_sio2_pot(), 800.0)
    warm_pot = _scoring_pot(_mgo_sio2_pot(), 1700.0)

    def row_for(pot, cell):
        return _cell_envelope(
            pot=pot,
            cell=cell,
            envelope=_capture_envelope,
            rail_for=_rail,
            comparator=None,
        )

    hot_row = row_for(hot_pot, hot)
    warm_row = row_for(warm_pot, warm)
    assert hot_row["predicted"] == hot.melt_activities["SiO2"]
    assert hot_row["authority"] == AUTHORITY_EXTRAPOLATED
    assert "openimcc_temperature_extrapolated" in hot_row["notices"]
    assert hot_row["notices"].count("openimcc_temperature_extrapolated") == 1
    assert warm_row["predicted"] == warm.melt_activities["SiO2"]
    assert warm_row["predicted"] != hot_row["predicted"]
    assert warm_row["authority"] == "bridge"
    assert "openimcc_temperature_extrapolated" not in warm_row["notices"]
    assert "openimcc_gas_unavailable" in warm_row["notices"]

    comparator = _activity_comparator(hot_pot)
    compared = _comparator_envelope(
        pot=hot_pot,
        cell=hot,
        comparator=comparator,
        envelope=_capture_envelope,
        rail_for=_rail,
    )
    assert compared["predicted"] == hot.melt_activities["SiO2"]
    assert compared["authority"] == AUTHORITY_EXTRAPOLATED
    assert "openimcc_temperature_extrapolated" in compared["notices"]


def _partial_identity(temperature_K: str, composition: Composition):
    ident = F.activity_identity(
        formula="Na2O",
        T_K=Decimal(temperature_K),
        component_basis="Na2O",
        composition=composition,
    )
    return replace(ident, quantity=Quantity.P_PARTIAL, species=Species("Na", Phase.G))


def test_predict_keeps_engine_flag(monkeypatch) -> None:
    _install_fake_vaporock(monkeypatch)
    handle = _vaporock_handle()
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("SiO2", Decimal("0.55")), ("Na2O", Decimal("0.45"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    hot_ident = _partial_identity("1773.15", composition)
    warm_ident = _partial_identity("1673.15", composition)
    wt = composition_wt_pct(hot_ident.composition.value)
    assert wt is not None
    assert 30.0 < wt["SiO2"] < 80.0
    hot_obs = F.observation(
        "na-hot",
        "exp-flag",
        hot_ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    warm_obs = F.observation(
        "na-warm",
        "exp-flag",
        warm_ident,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    handles = {Engine.VAPOROCK.value: handle}
    hot = predict_with_engine(
        Engine.VAPOROCK, hot_obs, handles=handles, isolated=False
    )
    warm = predict_with_engine(
        Engine.VAPOROCK, warm_obs, handles=handles, isolated=False
    )
    hot_cell = equilibrate_cell(
        handle,
        identity_battery_pot(f"obs:{hot_obs.observation_id}", wt),
        temperature_K=1773.15,
        po2=Po2Request(mode="commanded", po2_bar=float(hot_ident.fO2_Pa.value) / 1.0e5),
        isolated=False,
    )
    assert hot.value is not None
    assert float(hot.value) == pytest.approx(hot_cell.gas_partial_pressures_Pa["Na"])
    assert hot.authority is Authority.EXTRAPOLATED
    assert any(notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND for notice in hot.notices)
    assert hot.certified_band is not None
    assert hot.certified_band["temperature_K"][1] < 1773.15
    candidate = candidate_observation(
        F.observation(
            "na-ref",
            "exp-flag",
            hot_ident,
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
        ),
        hot,
    )
    assert candidate.authority is Authority.EXTRAPOLATED
    assert float(candidate.value.point) == pytest.approx(
        hot_cell.gas_partial_pressures_Pa["Na"]
    )

    assert warm.value is not None
    assert warm.authority is Authority.BRIDGE
    assert not any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND for notice in warm.notices
    )
    assert float(warm.value) != float(hot.value)
