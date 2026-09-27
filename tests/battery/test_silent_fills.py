"""Missing scorer inputs are refusals or recorded omissions, never silent fills."""

from __future__ import annotations

import inspect
import types
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    NoticeKind,
    Phase,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import Composition, Species, State
from simulator.battery.score import predict_with_engine
from simulator.diagnostic_helpers.binary_pot_battery import (
    PO2_COMMANDED,
    PO2_ENGINE_DEFAULT,
    PO2_NOT_AN_INPUT,
    BinaryPot,
    EngineHandle,
    Po2Request,
    _DEFAULT_FO2_LOG,
    _DEFAULT_PRESSURE_BAR,
    _fo2_log_for_request,
    equilibrate_cell,
)
from tests.battery import factories as F


def _no_engine(monkeypatch) -> list[str]:
    opened: list[str] = []

    def _open(name: str) -> object:
        opened.append(name)
        raise AssertionError(name)

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    return opened


def _capture_cell(monkeypatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def _open(name: str) -> object:
        return types.SimpleNamespace(
            name=name,
            available=True,
            unavailable_reason=None,
            supports_intrinsic_fo2=False,
        )

    def _cell(_handle, pot, *, po2, physical_pressure_bar=None, **_kwargs):
        seen["wt"] = dict(pot.composition_wt_pct)
        seen["mode"] = po2.mode
        seen["po2_bar"] = po2.po2_bar
        seen["pressure_bar"] = physical_pressure_bar
        return types.SimpleNamespace(
            status="refusal",
            refusal_reason="census_stop",
            hostname="test",
            exit_code=0,
            exit_signal=None,
            notices=[],
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={},
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    return seen


def _melt(components: tuple[tuple[str, str], ...], species: str, *, fO2_Pa, total_P):
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=tuple((name, Decimal(amount)) for name, amount in components),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    ident = F.activity_identity(
        formula=species,
        composition=composition,
        component_basis=species,
        fO2_Pa=Decimal("1e-8") if fO2_Pa is None else fO2_Pa,
        total_P=Decimal("100000") if total_P is None else total_P,
    )
    updates = {}
    if fO2_Pa is None:
        updates["fO2_Pa"] = State.unknown("no fO2 printed")
    if total_P is None:
        updates["total_pressure_Pa"] = State.unknown("no total pressure printed")
    if updates:
        ident = replace(ident, **updates)
    return ident


def test_predict_with_engine_does_not_select_the_engine_default() -> None:
    source = inspect.getsource(predict_with_engine)
    assert "PO2_ENGINE_DEFAULT" not in source
    assert "engine_default" not in source


def test_missing_fo2_on_a_vapour_row_is_a_typed_refusal(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("K2O", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    ident = replace(
        F.activity_identity(formula="K", composition=composition, component_basis="K2O"),
        quantity=State.of(Quantity.P_PARTIAL),
        species=Species("K", Phase.G),
        fO2_Pa=State.unknown("no fO2_Pa mapped from source"),
    )
    obs = F.observation("k-partial", "exp", ident, Decimal("1"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert opened == []
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert prediction.refusal_detail["reason"] != "engine_default"
    assert "engine_default" not in str(prediction.refusal_detail)


def test_multivalent_melt_without_oxygen_is_a_typed_refusal(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = _melt((("FeO", "0.2"), ("SiO2", "0.8")), "SiO2", fO2_Pa=None, total_P=Decimal("1e5"))
    obs = F.observation("feo", "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.ALPHAMELTS, obs, isolated=False)
    assert opened == []
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert "FeO" in prediction.refusal_detail["multivalent"]


@pytest.mark.parametrize("alias", ["FeOT", "FeO*", "FeO_tot", "FeO_total"])
def test_iron_alias_without_oxygen_is_a_typed_refusal(alias, monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = _melt(((alias, "0.2"), ("SiO2", "0.8")), "SiO2", fO2_Pa=None, total_P=Decimal("1e5"))
    obs = F.observation(alias, "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.ALPHAMELTS, obs, isolated=False)
    assert opened == []
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert alias in prediction.refusal_detail["multivalent"]


def test_zero_iron_does_not_hide_another_multivalent_oxide(monkeypatch) -> None:
    _no_engine(monkeypatch)
    ident = _melt(
        (("FeO", "0"), ("TiO2", "0.2"), ("SiO2", "0.8")),
        "SiO2",
        fO2_Pa=None,
        total_P=Decimal("1e5"),
    )
    obs = F.observation("ti", "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.IMCC_SF04, obs, isolated=False)
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert "TiO2" in prediction.refusal_detail["multivalent"]
    assert "FeO" not in prediction.refusal_detail["multivalent"]


def test_measured_gallium_requires_oxygen_when_the_bulk_is_cmas(monkeypatch) -> None:
    _no_engine(monkeypatch)
    ident = _melt(
        (("CaO", "0.2"), ("MgO", "0.1"), ("Al2O3", "0.1"), ("SiO2", "0.6")),
        "Ga",
        fO2_Pa=None,
        total_P=Decimal("1e5"),
    )
    obs = F.observation("ga", "exp", ident, Decimal("0.01"))
    prediction = predict_with_engine(Engine.THERMOENGINE, obs, isolated=False)
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert "Ga" in prediction.refusal_detail["multivalent"]


def test_melt_without_a_multivalent_element_omits_oxygen(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt(
        (("Na2O", "0.4"), ("SiO2", "0.6")),
        "SiO2",
        fO2_Pa=None,
        total_P=Decimal("100000"),
    )
    obs = F.observation("cmas", "exp", ident, Decimal("0.5"))
    prediction = predict_with_engine(Engine.IMCC_SF04, obs, isolated=False)
    assert seen["mode"] == PO2_NOT_AN_INPUT
    assert seen["po2_bar"] is None
    assert seen["mode"] != PO2_ENGINE_DEFAULT
    assert seen["pressure_bar"] == pytest.approx(1.0)
    reasons = [notice.reason for notice in prediction.notices if notice.kind is NoticeKind.INPUT_OMITTED]
    assert any("no multivalent element" in reason and "omitted" in reason for reason in reasons)
    assert not any(notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN for notice in prediction.notices)


def test_printed_oxygen_is_commanded(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt(
        (("FeO", "0.2"), ("SiO2", "0.8")),
        "SiO2",
        fO2_Pa=Decimal("1e-3"),
        total_P=Decimal("100000"),
    )
    obs = F.observation("feo-printed", "exp", ident, Decimal("0.4"))
    predict_with_engine(Engine.MAGEMIN, obs, isolated=False)
    assert seen["mode"] == PO2_COMMANDED
    assert seen["po2_bar"] == pytest.approx(1e-3 / 1.0e5)
    assert seen["mode"] != PO2_ENGINE_DEFAULT


def test_unknown_vapour_composition_is_not_a_pure_pot(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = replace(
        F.psat_identity("K"),
        quantity=State.of(Quantity.P_PARTIAL),
        reservoir=State.unknown("no reservoir mapped from source"),
        composition=State.unknown("no composition mapped from source"),
        fO2_Pa=State.unknown("no fO2_Pa mapped from source"),
        total_pressure_Pa=State.unknown("no total_pressure_Pa mapped from source"),
    )
    obs = F.observation("k-melt", "exp", ident, Decimal("1"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert opened == []
    assert prediction.value is None
    assert prediction.requested_composition is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "composition_not_stated_pure_reservoir"
    assert prediction.refusal_detail["also_unfilled"]["fO2_Pa"] == "missing"
    assert prediction.refusal_detail["also_unfilled"]["total_pressure_Pa"] == "missing"


def test_formation_row_without_a_pure_reservoir_is_not_a_pure_pot(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = replace(
        F.psat_identity("Na"),
        quantity=State.of(Quantity.DELTA_FG),
        reservoir=State.not_applicable("pure standard formation"),
        composition=State.not_applicable("pure standard formation"),
    )
    obs = F.observation("na-gf", "exp", ident, Decimal("-100"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert opened == []
    assert prediction.refusal_detail["reason"] == "engine-thermo-does-not-emit"


def test_stated_pure_reservoir_is_the_pot_and_oxygen_is_omitted(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = F.psat_identity("Na")
    obs = F.observation("na-psat", "exp", ident, Decimal("1"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert seen["wt"] == {"Na": 100.0}
    assert seen["mode"] == PO2_NOT_AN_INPUT
    assert seen["po2_bar"] is None
    assert seen["pressure_bar"] == pytest.approx(_DEFAULT_PRESSURE_BAR)
    reasons = [notice.reason for notice in prediction.notices]
    assert any("pure condensed substance Na (l)" in reason for reason in reasons)
    assert any("p_sat does not take oxygen" in reason and "omitted" in reason for reason in reasons)
    assert any(
        notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and f"{_DEFAULT_PRESSURE_BAR:g} bar" in notice.reason
        for notice in prediction.notices
    )


def test_missing_total_pressure_is_a_visible_assumption(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = replace(
        _melt((("Na2O", "0.4"), ("SiO2", "0.6")), "SiO2", fO2_Pa=Decimal("1e-4"), total_P=Decimal("1")),
        total_pressure_Pa=State.unknown("no total_pressure_Pa mapped from source"),
    )
    obs = F.observation("no-p", "exp", ident, Decimal("0.2"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert seen["pressure_bar"] == pytest.approx(_DEFAULT_PRESSURE_BAR)
    assert seen["mode"] == PO2_COMMANDED
    assert any(
        notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and "does not carry total_pressure_Pa" in notice.reason
        and f"assumes {_DEFAULT_PRESSURE_BAR:g} bar" in notice.reason
        for notice in prediction.notices
    )


def test_printed_total_pressure_is_passed_without_an_assumption(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt(
        (("Na2O", "0.4"), ("SiO2", "0.6")),
        "SiO2",
        fO2_Pa=Decimal("1e-4"),
        total_P=Decimal("2500"),
    )
    obs = F.observation("printed-p", "exp", ident, Decimal("0.2"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert seen["pressure_bar"] == pytest.approx(2500 / 1.0e5)
    assert not any(
        notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN for notice in prediction.notices
    )


def test_negative_total_pressure_is_refused(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = _melt(
        (("Na2O", "0.4"), ("SiO2", "0.6")),
        "SiO2",
        fO2_Pa=Decimal("1e-4"),
        total_P=Decimal("-5"),
    )
    obs = F.observation("bad-p", "exp", ident, Decimal("0.2"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert opened == []
    assert prediction.refusal_reason is RefusalReason.INVALID_IDENTITY
    assert prediction.refusal_detail["reason"] == "total_pressure_invalid"


def test_not_an_input_does_not_become_log_fo2_minus_nine() -> None:
    handle = EngineHandle(
        name="vaporock",
        backend=None,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    assert _fo2_log_for_request(handle, Po2Request(PO2_NOT_AN_INPUT, None)) is None
    assert _fo2_log_for_request(handle, Po2Request(PO2_ENGINE_DEFAULT, None)) == _DEFAULT_FO2_LOG


def test_equilibrate_cell_forwards_omitted_oxygen_and_printed_pressure() -> None:
    seen: dict[str, object] = {}

    class _Backend:
        def equilibrate(self, temperature_C, composition_kg=None, fO2_log=-9.0, pressure_bar=1e-6, **kwargs):
            seen["fO2_log"] = fO2_log
            seen["pressure_bar"] = pressure_bar
            seen["passed_fo2"] = "fO2_log" in inspect.signature(self.equilibrate).parameters
            raise RuntimeError("stop after recording the call")

    handle = EngineHandle(
        name="vaporock",
        backend=_Backend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    pot = BinaryPot(
        pot_id="pure-na",
        kato_1993_table4_system=None,
        why="forwarding check",
        composition_wt_pct={"SiO2": 100.0},
    )
    equilibrate_cell(
        handle,
        pot,
        temperature_K=1500.0,
        po2=Po2Request(PO2_NOT_AN_INPUT, None),
        isolated=False,
        physical_pressure_bar=0.25,
    )
    assert seen["fO2_log"] is None
    assert seen["pressure_bar"] == pytest.approx(0.25)
    assert seen["fO2_log"] != _DEFAULT_FO2_LOG
