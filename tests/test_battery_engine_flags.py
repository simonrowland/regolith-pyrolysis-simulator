"""Engine out-of-band flags survive the battery cell into scoring.

The cell stores the engine's own notice once. Scoring reads that cell.
Predicted numbers stay the engine's numbers: an in-band run is not flagged,
and attaching the flag does not move the value.
"""

from __future__ import annotations

import types
from dataclasses import replace
from decimal import Decimal

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
from simulator.battery.records import Composition, Species
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
    Po2Request,
    equilibrate_cell,
    extract_reported_quantities,
    composition_kg_and_mol,
    identity_battery_pot,
    open_battery_engine,
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


def _imcc_cell(handle: EngineHandle, temperature_K: float):
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
    direct_activities, direct_pressures = extract_reported_quantities(direct)
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


def test_imcc_temperature_notice_survives_cell_notices() -> None:
    handle = open_battery_engine("imcc_sf04")
    assert handle.available is True
    hot = _imcc_cell(handle, 800.0)
    warm = _imcc_cell(handle, 1700.0)
    kg, _mol = composition_kg_and_mol(_mgo_sio2_pot().composition_wt_pct)
    direct = handle.backend.equilibrate(
        temperature_C=800.0 - 273.15,
        composition_kg=kg,
        fO2_log=-9.0,
        pressure_bar=1.0e-6,
    )
    direct_activities, direct_pressures = extract_reported_quantities(direct)

    assert hot.status == "ok"
    assert hot.authority == AUTHORITY_EXTRAPOLATED
    assert hot.melt_activities == direct_activities
    assert hot.gas_partial_pressures_Pa == direct_pressures
    assert hot.melt_activities["SiO2"] > 0.0
    kinds = [row.get("kind") for row in hot.notices]
    assert kinds.count("imcc_temperature_extrapolated") == 1
    assert len(kinds) == len(set(kinds))
    scored = cell_notices(Quantity.ACTIVITY, Engine.IMCC_SF04, hot)
    band = [notice for notice in scored if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND]
    assert len(band) == 1
    assert "T outside datapack" in band[0].reason

    assert warm.status == "ok"
    assert warm.authority is None
    assert warm.melt_activities["SiO2"] != hot.melt_activities["SiO2"]
    assert [row.get("kind") for row in warm.notices] == ["imcc_gas_unavailable"]
    assert cell_notices(Quantity.ACTIVITY, Engine.IMCC_SF04, warm) == ()


def test_scoring_envelope_uses_cell_flag() -> None:
    handle = open_battery_engine("imcc_sf04")
    hot = _imcc_cell(handle, 800.0)
    warm = _imcc_cell(handle, 1700.0)
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
    assert "imcc_temperature_extrapolated" in hot_row["notices"]
    assert hot_row["notices"].count("imcc_temperature_extrapolated") == 1
    assert warm_row["predicted"] == warm.melt_activities["SiO2"]
    assert warm_row["predicted"] != hot_row["predicted"]
    assert warm_row["authority"] == "bridge"
    assert "imcc_temperature_extrapolated" not in warm_row["notices"]
    assert "imcc_gas_unavailable" in warm_row["notices"]

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
    assert "imcc_temperature_extrapolated" in compared["notices"]


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
