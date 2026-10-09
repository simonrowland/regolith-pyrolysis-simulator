"""Missing scorer inputs are refusals or recorded omissions, never silent fills."""

from __future__ import annotations

import inspect
import types
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AmountBasis,
    BenchIdentityBasis,
    CellMaterial,
    Engine,
    NoticeKind,
    Phase,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Composition,
    Derivation,
    Located,
    Species,
    State,
    Value,
)
from simulator.battery.score import (
    _cell_material_class,
    cell_notices,
    predict_with_engine,
)
from simulator.diagnostic_helpers.binary_pot_battery import (
    PO2_COMMANDED,
    PO2_ENGINE_DEFAULT,
    PO2_NOT_AN_INPUT,
    PO2_OXYGEN_BALANCE_EFFUSION,
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
        seen["cell_material"] = po2.cell_material
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


def _kems_partial(*, cell_material: str | None, fO2_Pa: Decimal | None = None):
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("K2O", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    ident = replace(
        F.activity_identity(
            formula="K",
            composition=composition,
            component_basis="K2O",
            fO2_Pa=Decimal("1e-8") if fO2_Pa is None else fO2_Pa,
        ),
        quantity=State.of(Quantity.P_PARTIAL),
        species=Species("K", Phase.G),
        fO2_Pa=(
            State.unknown("no fO2 printed")
            if fO2_Pa is None
            else State.of(fO2_Pa)
        ),
    )
    experiment = F.kems_experiment()
    if cell_material is not None:
        assert experiment.apparatus is not None
        experiment = replace(
            experiment,
            apparatus=replace(
                experiment.apparatus,
                cell_material_and_liner=F.located(cell_material),
            ),
        )
    observation = F.observation("kems-potassium", experiment.experiment_id, ident, Decimal("1"))
    return observation, experiment


def _cell_material_bench(materials: tuple[CellMaterial, ...] | None) -> Bench:
    return Bench(
        id="bench-cell-material",
        work_id="work-cell-material",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="test fixture",
        ),
        cell_materials=(
            None if materials is None else tuple(F.located(material) for material in materials)
        ),
    )


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
    prediction = predict_with_engine(Engine.OPENIMCC, obs, isolated=False)
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
    prediction = predict_with_engine(Engine.OPENIMCC, obs, isolated=False)
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


@pytest.mark.parametrize(
    ("materials", "expected_class"),
    (
        ((CellMaterial.PT,), "inert"),
        ((CellMaterial.IR,), "inert"),
        ((CellMaterial.PT, CellMaterial.IR), "inert"),
        ((CellMaterial.W,), "reactive"),
        ((CellMaterial.MO,), "reactive"),
        ((CellMaterial.TA,), "reactive"),
        ((CellMaterial.NB,), "reactive"),
        ((CellMaterial.C_GRAPHITE,), "reactive"),
        ((CellMaterial.RE,), "reactive"),
        ((CellMaterial.AL2O3,), "not_inert"),
        ((CellMaterial.OTHER,), "not_inert"),
        ((), "unknown"),
    ),
)
def test_cell_material_class_uses_only_typed_materials(
    materials: tuple[CellMaterial, ...],
    expected_class: str,
) -> None:
    located = tuple(F.located(material) for material in materials)
    assert _cell_material_class(located) == expected_class


def test_cell_material_vocabulary_is_closed() -> None:
    assert {material.value for material in CellMaterial} == {
        "Pt",
        "Ir",
        "Rh",
        "W",
        "Mo",
        "Ta",
        "Nb",
        "Re",
        "Ni",
        "Fe",
        "C_graphite",
        "Al2O3",
        "SiO2",
        "MgO",
        "ZrO2",
        "Y2O3",
        "ThO2",
        "BeO",
        "BN",
        "SiC",
        "other_alloy",
        "other",
    }


@pytest.mark.parametrize(
    "materials",
    (
        (CellMaterial.PT,),
        (CellMaterial.IR,),
        (CellMaterial.PT, CellMaterial.IR),
    ),
)
def test_inert_knudsen_cell_requests_oxygen_balance_effusion(
    materials: tuple[CellMaterial, ...],
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)

    predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None
    assert seen["cell_material"] is None


@pytest.mark.parametrize(
    ("materials", "cell_material"),
    (
        ((CellMaterial.W,), "W"),
        ((CellMaterial.MO,), "Mo"),
        ((CellMaterial.W, CellMaterial.W), "W"),
    ),
)
def test_uniform_modelled_reactive_cell_requests_cell_oxide_reservoir(
    materials: tuple[CellMaterial, ...],
    cell_material: str,
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)

    predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None
    assert seen["cell_material"] == cell_material


@pytest.mark.parametrize(
    ("materials", "reason"),
    (
        ((CellMaterial.TA,), "reactive_cell_oxygen_reservoir"),
        ((CellMaterial.NB,), "reactive_cell_oxygen_reservoir"),
        ((CellMaterial.C_GRAPHITE,), "reactive_cell_oxygen_reservoir"),
        ((CellMaterial.RE,), "reactive_cell_oxygen_reservoir"),
        (
            (CellMaterial.IR, CellMaterial.C_GRAPHITE),
            "reactive_cell_oxygen_reservoir",
        ),
        (
            (CellMaterial.IR, CellMaterial.W),
            "reactive_cell_oxygen_reservoir",
        ),
        (
            (CellMaterial.W, CellMaterial.RE),
            "reactive_cell_oxygen_reservoir",
        ),
        (
            (CellMaterial.MO, CellMaterial.AL2O3),
            "reactive_cell_oxygen_reservoir",
        ),
        ((CellMaterial.AL2O3,), "cell_material_not_inert"),
        ((CellMaterial.PT, CellMaterial.AL2O3), "cell_material_not_inert"),
        ((CellMaterial.OTHER,), "cell_material_not_inert"),
    ),
)
def test_noninert_knudsen_cell_refuses_oxygen_balance(
    materials: tuple[CellMaterial, ...],
    reason: str,
    monkeypatch,
) -> None:
    opened = _no_engine(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert opened == []
    assert prediction.refusal_detail["reason"] == reason


def test_platinum_alloy_prose_without_typed_material_is_unknown(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)
    bench = replace(
        _cell_material_bench(None),
        cell_material_and_liner=F.located("platinum alloy"),
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )

    assert opened == []
    assert prediction.refusal_detail["reason"] == "cell_material_unknown"


def test_printed_fo2_leaves_knudsen_request_unchanged_even_for_reactive_cell(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(
        cell_material=None, fO2_Pa=Decimal("1e-3")
    )

    predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench((CellMaterial.W,)),
        isolated=False,
    )

    assert seen["mode"] == PO2_COMMANDED
    assert seen["po2_bar"] == pytest.approx(1e-3 / 1.0e5)


def test_derived_point_condition_fo2_is_not_sent_as_a_command(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(
        cell_material=None, fO2_Pa=Decimal("1e-3")
    )
    derived = Located(
        State.of(Value.point_of(Decimal("1e-3"))),
        inference=Derivation(
            relation="derived from measured pK",
            inputs=("measured-pK",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    observation = replace(observation, point_conditions={"fO2_Pa": derived})

    predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench((CellMaterial.PT,)),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None


def test_solved_effusion_notice_keeps_pO2_diagnostics() -> None:
    notice = cell_notices(
        Quantity.P_PARTIAL,
        Engine.OPENIMCC,
        types.SimpleNamespace(
            notices=[
                {
                    "kind": "fo2_oxygen_balance_effusion_solved",
                    "pO2_bar": 0.12,
                    "relative_residual": 1e-8,
                    "bracket_log10_bar": [-3.0, -1.0],
                    "dominant_O_carriers": ["O2"],
                }
            ]
        ),
    )[0]

    assert notice.kind is NoticeKind.SOURCE_DISAGREEMENT
    assert notice.reason.startswith("fo2_oxygen_balance_effusion_solved:")
    assert '"pO2_bar":0.12' in notice.reason
    assert '"dominant_O_carriers":["O2"]' in notice.reason


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


def test_unallowlisted_formation_quantity_refuses_before_engine(monkeypatch) -> None:
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
    assert prediction.value is None
    assert prediction.refusal_detail == {
        "reason": "quantity_not_predicted",
        "quantity": Quantity.DELTA_FG.value,
    }


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
