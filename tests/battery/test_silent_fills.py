"""Missing scorer inputs are refusals or recorded omissions, never silent fills."""

from __future__ import annotations

import inspect
import json
import math
import types
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AmountBasis,
    BenchIdentityBasis,
    CellMaterial,
    Engine,
    MethodToken,
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


def _has_ambient_air_fo2_default(notices) -> bool:
    return any(
        notice.kind
        is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
        and notice.policy is not None
        and notice.policy.id == "DP-010"
        and "pO2=0.21 atm" in notice.reason
        and "log10(fO2/bar)=" in notice.reason
        for notice in notices
    )


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
    from simulator.battery.score import _predict_with_engine

    source = inspect.getsource(_predict_with_engine)
    assert "PO2_ENGINE_DEFAULT" not in source
    assert "engine_default" not in source


def test_vapour_row_without_printed_fo2_commands_borrowed_ladder_fo2(
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
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
    assert seen["mode"] == PO2_COMMANDED
    assert seen["po2_bar"] is not None
    assert seen["mode"] != PO2_ENGINE_DEFAULT
    assert prediction.refusal_detail.get("reason") != "missing_fO2"
    assert prediction.refusal_detail.get("reason") != "engine_default"
    assert _has_ambient_air_fo2_default(prediction.notices)


def test_multivalent_melt_without_printed_fo2_commands_borrowed_ladder_fo2(
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt((("FeO", "0.2"), ("SiO2", "0.8")), "SiO2", fO2_Pa=None, total_P=Decimal("1e5"))
    obs = F.observation("feo", "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.ALPHAMELTS, obs, isolated=False)
    assert seen["mode"] == PO2_COMMANDED
    assert seen["po2_bar"] is not None
    assert seen["mode"] != PO2_ENGINE_DEFAULT
    assert prediction.refusal_detail.get("reason") != "missing_fO2"
    assert _has_ambient_air_fo2_default(prediction.notices)


@pytest.mark.parametrize("alias", ["FeOT", "FeO*", "FeO_tot", "FeO_total"])
def test_iron_alias_without_oxygen_is_a_typed_refusal(alias, monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = _melt(((alias, "0.2"), ("SiO2", "0.8")), "SiO2", fO2_Pa=None, total_P=Decimal("1e5"))
    obs = F.observation(alias, "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.ALPHAMELTS, obs, isolated=False)
    assert opened == []
    assert prediction.refusal_detail["reason"] == "missing_fO2"
    assert alias in prediction.refusal_detail["multivalent"]


def test_iron_alias_does_not_hide_an_unparseable_sibling(monkeypatch) -> None:
    opened = _no_engine(monkeypatch)
    ident = _melt(
        (("FeOt", "0.2"), ("foobar", "0.1"), ("SiO2", "0.7")),
        "SiO2",
        fO2_Pa=None,
        total_P=Decimal("1e5"),
    )
    obs = F.observation("feot-foobar", "exp", ident, Decimal("0.4"))

    prediction = predict_with_engine(Engine.ALPHAMELTS, obs, isolated=False)

    assert opened == []
    assert prediction.refusal_detail["reason"] == "unparseable_composition_component"
    assert prediction.refusal_detail["component"] == "foobar"


def test_zero_iron_does_not_hide_another_multivalent_oxide(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt(
        (("FeO", "0"), ("TiO2", "0.2"), ("SiO2", "0.8")),
        "SiO2",
        fO2_Pa=None,
        total_P=Decimal("1e5"),
    )
    obs = F.observation("ti", "exp", ident, Decimal("0.4"))
    prediction = predict_with_engine(Engine.OPENIMCC, obs, isolated=False)
    assert seen["mode"] == PO2_COMMANDED
    assert prediction.refusal_detail.get("reason") != "missing_fO2"
    assert _has_ambient_air_fo2_default(prediction.notices)


def test_measured_gallium_requires_oxygen_when_the_bulk_is_cmas(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = _melt(
        (("CaO", "0.2"), ("MgO", "0.1"), ("Al2O3", "0.1"), ("SiO2", "0.6")),
        "Ga",
        fO2_Pa=None,
        total_P=Decimal("1e5"),
    )
    obs = F.observation("ga", "exp", ident, Decimal("0.01"))
    prediction = predict_with_engine(Engine.THERMOENGINE, obs, isolated=False)
    assert seen["mode"] == PO2_COMMANDED
    assert prediction.refusal_detail.get("reason") != "missing_fO2"
    assert _has_ambient_air_fo2_default(prediction.notices)


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
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in prediction.notices
    )
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
    assert seen["po2_bar"] == float(Decimal("1e-3")) / 1.0e5
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

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None
    assert seen["cell_material"] is None
    assert any(
        notice.reason == (
            "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
            "route=effusion_oxygen_balance"
        )
        for notice in prediction.notices
    )


@pytest.mark.parametrize("missing_input", ("composition", "temperature"))
def test_knudsen_effusion_balance_requires_composition_and_temperature(
    missing_input: str,
) -> None:
    from simulator.battery.fo2_ladder import resolve_row_fo2

    observation, experiment = _kems_partial(cell_material=None)
    identity = observation.identity
    assert identity.composition is not None and identity.composition.is_value
    composition = identity.composition.value
    temperature = (
        float(identity.temperature_K.value)
        if identity.temperature_K is not None and identity.temperature_K.is_value
        else None
    )
    if missing_input == "composition":
        observation = replace(
            observation,
            identity=replace(
                identity,
                composition=State.unknown("no melt composition"),
            ),
        )
        composition = None
    else:
        temperature = None

    resolved = resolve_row_fo2(
        experiment,
        _cell_material_bench((CellMaterial.PT,)),
        observation,
        composition=composition,
        temperature_K=temperature,
        quantity=Quantity.P_PARTIAL,
    )

    assert resolved is None


def test_unprinted_knudsen_fo2_prefers_effusion_to_kress_composition_inversion() -> None:
    from simulator.battery.fo2_ladder import resolve_row_fo2

    observation, experiment = _kems_partial(cell_material=None)
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("FeO", Decimal("0.2")),
            ("Fe2O3", Decimal("0.2")),
            ("SiO2", Decimal("0.6")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = replace(observation.identity, composition=State.of(composition))
    observation = replace(observation, identity=identity)
    temperature = float(identity.temperature_K.value)

    resolved = resolve_row_fo2(
        experiment,
        _cell_material_bench((CellMaterial.PT,)),
        observation,
        composition=composition,
        temperature_K=temperature,
        quantity=Quantity.P_PARTIAL,
    )

    assert resolved is not None
    assert resolved.closure
    assert resolved.route == "effusion_oxygen_balance"


def test_printed_knudsen_buffer_precedes_effusion_balance() -> None:
    from simulator.battery.fo2_ladder import resolve_row_fo2
    from simulator.battery.waypoints import Waypoint, WaypointAuthority, WaypointResult

    observation, experiment = _kems_partial(cell_material=None)
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("FeO", Decimal("0.2")),
            ("Fe2O3", Decimal("0.2")),
            ("SiO2", Decimal("0.6")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = replace(observation.identity, composition=State.of(composition))
    observation = replace(observation, identity=identity)
    printed_buffer = Waypoint(
        name="oxygen_condition",
        value=Value.point_of(Decimal("-12")),
        route="buffer_relation",
        authority=WaypointAuthority.DERIVED,
        inputs=("experiment.fO2_control.buffer",),
    )
    oxygen = WaypointResult(
        name="oxygen_condition",
        selected=printed_buffer,
        routes=(printed_buffer,),
    )

    resolved = resolve_row_fo2(
        experiment,
        _cell_material_bench((CellMaterial.PT,)),
        observation,
        composition=composition,
        temperature_K=float(identity.temperature_K.value),
        oxygen=oxygen,
        quantity=Quantity.P_PARTIAL,
    )

    assert resolved is not None
    assert not resolved.closure
    assert resolved.route == "buffer_relation"


def test_knudsen_effusion_balance_uses_hertz_knudsen_flux_weighting() -> None:
    from openimcc import oxygen_balance_from_pressure_model, oxygen_balance_species_metadata
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _solve_cell_oxygen_balance,
    )

    species = oxygen_balance_species_metadata({"SiO": "SiO2", "O2": None})
    root_logp = -5.0
    target_p_sio_bar = 1e-3
    # SiO2(l) -> SiO(g) + 1/2 O2(g). Its gas O balance is
    # 2 J_O2 = J_SiO; J_i ∝ p_i/sqrt(M_i T), so pO2/pSiO is
    # 0.5*sqrt(M_O2/M_SiO), not the pressure-balance value 0.5.
    expected_ratio = 0.5 * (
        species["O2"].molar_mass / species["SiO"].molar_mass
    ) ** 0.5

    def pressure_model(pO2_bar: float) -> dict[str, float]:
        delta_logp = math.log10(pO2_bar) - root_logp
        return {
            "SiO": target_p_sio_bar * 10.0 ** (-0.5 * delta_logp),
            "O2": target_p_sio_bar * expected_ratio * 10.0**delta_logp,
        }

    pO2_bar, pressures, _balance, _cell_fraction, _cell_info = (
        _solve_cell_oxygen_balance(
            pressure_model,
            species,
            temperature_K=2000.0,
            cell_material=None,
            oxygen_balance_from_pressure_model=oxygen_balance_from_pressure_model,
        )
    )

    assert pO2_bar == pytest.approx(10.0**root_logp, rel=1e-10)
    assert pressures["O2"] / pressures["SiO"] == pytest.approx(expected_ratio)
    assert pressures["O2"] / pressures["SiO"] != pytest.approx(0.5)


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

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None
    assert seen["cell_material"] == cell_material
    assert any(
        notice.reason == (
            "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
            "route=effusion_oxygen_balance"
        )
        for notice in prediction.notices
    )


@pytest.mark.parametrize(
    "materials",
    (
        None,
        (CellMaterial.AL2O3,),
        (CellMaterial.PT, CellMaterial.AL2O3),
        (CellMaterial.OTHER,),
    ),
)
def test_unmodelled_knudsen_cell_uses_flagged_melt_effusion_balance(
    materials: tuple[CellMaterial, ...] | None,
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["cell_material"] is None
    assert prediction.refusal_detail.get("refusal_reason") == "census_stop"
    assert any(
        notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED
        and "cell oxygen balance not modelled" in notice.reason
        for notice in prediction.notices
    )


@pytest.mark.parametrize(
    "materials",
    (
        (CellMaterial.TA,),
        (CellMaterial.NB,),
        (CellMaterial.C_GRAPHITE,),
        (CellMaterial.RE,),
        (CellMaterial.IR, CellMaterial.C_GRAPHITE),
        (CellMaterial.IR, CellMaterial.W),
        (CellMaterial.W, CellMaterial.RE),
        (CellMaterial.MO, CellMaterial.AL2O3),
    ),
)
def test_unmodelled_reactive_knudsen_cell_predicts_with_notice(
    materials: tuple[CellMaterial, ...],
    monkeypatch,
) -> None:
    seen = _capture_cell(monkeypatch)
    observation, experiment = _kems_partial(cell_material=None)

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench(materials),
        isolated=False,
    )

    assert prediction.refusal_detail.get("reason") != "reactive_cell_oxygen_reservoir"
    assert any(
        notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED
        and "reactive cell: oxygen balance of the cell not modelled" in notice.reason
        for notice in prediction.notices
    )
    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION


def test_platinum_alloy_prose_stays_untyped_and_uses_flagged_melt_balance(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
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

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["cell_material"] is None
    assert any(
        notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED
        and "cell oxygen balance not modelled" in notice.reason
        for notice in prediction.notices
    )


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

    assert seen["po2_bar"] == float(Decimal("1e-3")) / 1.0e5


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

    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=experiment,
        bench=_cell_material_bench((CellMaterial.PT,)),
        isolated=False,
    )

    assert seen["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
    assert seen["po2_bar"] is None
    assert any(
        notice.reason == (
            "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
            "route=effusion_oxygen_balance"
        )
        for notice in prediction.notices
    )


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
    ident = F.psat_identity("Na")
    obs = F.observation("na-psat", "exp", ident, Decimal("1"))
    with monkeypatch.context() as capture:
        seen = _capture_cell(capture)
        request_prediction = predict_with_engine(
            Engine.VAPOROCK, obs, isolated=False
        )
    assert seen["wt"] == {"Na": 100.0}
    assert seen["mode"] == PO2_NOT_AN_INPUT
    assert seen["po2_bar"] is None
    assert seen["pressure_bar"] is None
    reasons = [notice.reason for notice in request_prediction.notices]
    assert any("pure condensed substance Na (l)" in reason for reason in reasons)
    assert any("p_sat does not take oxygen" in reason and "omitted" in reason for reason in reasons)
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-001"
        for notice in request_prediction.notices
    )

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, obs, isolated=False
    )
    assert prediction.value is not None and prediction.value.is_finite()
    assert prediction.refusal_reason is None, prediction.refusal_detail
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-001"
        for notice in prediction.notices
    )


def test_pure_sodium_reference_pressure_uses_the_saturation_owner() -> None:
    from engines.builtin.sodium_saturation import (
        sodium_saturation_block,
        sodium_saturation_pressure,
    )
    from simulator.battery.records import as_decimal
    from simulator.config import load_config_bundle
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    temperature = Decimal("500")
    ident = replace(
        F.psat_identity("Na", T_K=temperature),
        quantity=State.of(Quantity.P_REFERENCE),
    )
    observation = F.observation("na-pref", "exp", ident, Decimal("1"))
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, observation, isolated=False
    )
    view = vapor_pressure_legacy_view(load_config_bundle().vapor_pressures)
    block = sodium_saturation_block(view["metals"]["Na"])
    assert block is not None
    saturation = sodium_saturation_pressure(block, float(temperature))
    assert prediction.value == as_decimal(str(saturation.pressure_pa))
    assert prediction.refusal_reason is None, prediction.refusal_detail
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.affected_quantities == (Quantity.P_REFERENCE,)
        for notice in prediction.notices
    )
    assert not any(
        notice.policy is not None and notice.policy.id == "DP-001"
        for notice in prediction.notices
    )
    saturation_row = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        F.observation(
            "na-psat-500",
            "exp",
            F.psat_identity("Na", T_K=temperature),
            Decimal("1"),
        ),
        isolated=False,
    )
    assert saturation_row.value == prediction.value
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.affected_quantities == (Quantity.P_SAT,)
        for notice in saturation_row.notices
    )


def test_oxide_reference_pressure_does_not_use_metal_saturation(monkeypatch) -> None:
    def _refuse(_row):
        raise AssertionError(
            "oxide P_REFERENCE used the sodium saturation owner"
        )

    monkeypatch.setattr(
        "engines.builtin.sodium_saturation.sodium_saturation_block",
        _refuse,
    )
    observation = F.observation("na-oxide-pref", "exp", F.pref_identity(), Decimal("1"))
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, observation, isolated=False
    )
    assert prediction.value is not None, prediction.refusal_detail
    assert prediction.value.is_finite() and prediction.value > 0
    assert prediction.refusal_reason is None
    assert prediction.coefficient_basis != "pure_component_saturation"
    assert prediction.identity.total_pressure_Pa == State.of(Decimal("100000"))
    assert not any(notice.policy and notice.policy.id == "DP-001" for notice in prediction.notices)


@pytest.mark.parametrize("quantity", (Quantity.P_SAT, Quantity.P_REFERENCE))
def test_non_sodium_antoine_notices_name_the_actual_quantity(quantity) -> None:
    identity = replace(F.psat_identity("Mg", T_K=Decimal("1500")), quantity=State.of(quantity))
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        F.observation("mg-antoine", "exp", identity, Decimal("1")),
    )
    assert prediction.value is not None, prediction.refusal_detail
    for reason in ("pure_component_antoine_extrapolated", "antoine-segment-phase-undeclared"):
        notice = next(notice for notice in prediction.notices if notice.reason == reason)
        assert notice.affected_quantities == (quantity,)


def _partial_without_total_pressure(method, *, fO2_Pa):
    observation, experiment = _kems_partial(cell_material=None, fO2_Pa=fO2_Pa)
    experiment = replace(
        experiment,
        method=State.unknown("method is not printed") if method is None else State.of(method),
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=replace(
                experiment.pressure_environment.total_pressure_Pa,
                state=State.unknown("total pressure is not printed"),
            ),
        ),
    )
    point_conditions = dict(observation.point_conditions or {})
    point_conditions.pop("total_pressure_Pa", None)
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            total_pressure_Pa=State.unknown("total pressure is not printed"),
        ),
        point_conditions=point_conditions,
    )
    return observation, experiment


@pytest.mark.parametrize("method", (None, MethodToken.TABULATION, MethodToken.KNUDSEN_EFFUSION))
def test_internal_partial_with_printed_oxygen_runs_without_total_pressure(monkeypatch, method):
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.state import MeltState

    class Core:
        def __init__(self):
            self.melt = MeltState()
            self._last_vapor_pressure_diagnostic = {}

        def _new_atom_ledger(self):
            return object()

        def _load_ledger_account(self, *_args, **_kwargs):
            pass

        def _build_chemistry_kernel(self):
            return None

        def _refresh_vapor_pressures_from_kernel(self, equilibrium, *, observation_pO2_bar):
            assert observation_pO2_bar == 0.01
            assert self.melt.p_total_mbar == 0.0  # reset state, no supplied boundary
            equilibrium.vapor_pressures_Pa = {"K": 125.0, "KO": 75.0}

    monkeypatch.setattr(battery, "_new_internal_analytical_core", Core)
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    row, experiment = _partial_without_total_pressure(method, fO2_Pa=Decimal("1000"))
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, row, experiment=experiment, isolated=False,
    )

    assert prediction.value == Decimal("125"), prediction.refusal_detail
    assert prediction.identity.fO2_Pa == State.of(Decimal("1000"))
    policies = {n.policy.id for n in prediction.notices if n.policy}
    if method is None:
        assert policies == {"DP-108"}
        assert prediction.identity.total_pressure_Pa == State.of(Decimal("200"))
    else:
        assert "DP-001" not in policies and "DP-108" not in policies
        assert prediction.identity.total_pressure_Pa.is_unknown


def test_internal_oxide_reference_without_defined_pressure_keeps_pressure_guard(monkeypatch):
    identity = F.pref_identity()
    identity = replace(
        identity,
        reference_state=State.of(replace(identity.reference_state.value, reference_pressure_bar=None)),
    )
    opened = _no_engine(monkeypatch)
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, F.observation("pref", "exp", identity, Decimal("1")),
        isolated=False,
    )
    assert opened == []
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "internal_analytical_missing_total_pressure"


@pytest.mark.parametrize("oxygen_log", (float("nan"), -math.inf, -5.0), ids=("nan", "unproven-zero", "complete"))
def test_dp108_checks_vaporock_channels_from_the_warm_payload(monkeypatch, oxygen_log):
    from simulator.melt_backend.vaporock import VapoRockBackend, _serialize_log10_bar_pressures

    backend = VapoRockBackend()
    backend._available = True
    log10_bar, sidecar = _serialize_log10_bar_pressures(
        {"K(g)": -3.0, "O2(g)": oxygen_log, "Na(g)": -math.inf}
    )
    backend._warm_pool = types.SimpleNamespace(
        submit=lambda *_args, **_kwargs: types.SimpleNamespace(
            result=lambda: {"log10_bar": log10_bar, **sidecar}
        )
    )
    handle = EngineHandle("vaporock", backend, True, None, True, False)
    # The warm worker here is a raw-output fixture and has no lifecycle to close.
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    row, experiment = _partial_without_total_pressure(None, fO2_Pa=Decimal("1000"))
    prediction = predict_with_engine(
        Engine.VAPOROCK, row, experiment=experiment, handles={"vaporock": handle},
        isolated=False,
    )
    if math.isfinite(oxygen_log):
        assert prediction.value == Decimal("100")
        assert prediction.identity.total_pressure_Pa == State.of(Decimal("101"))
        return
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {
        "reason": "partial_pressure_sum_incomplete_gas_map",
        "policy": "DP-108",
        "invalid_components": ["O2"],
        "target_partial_pressure_Pa": 100.0,
    }
    assert prediction.identity.total_pressure_Pa.is_unknown


@pytest.mark.parametrize("commanded", (True, False), ids=("commanded", "effusion"))
@pytest.mark.parametrize("channel", ("nonfinite", "reported-omission", "missing", "proven-zero", "complete"))
def test_dp108_checks_openimcc_channels_from_the_gas_evaluator(monkeypatch, channel, commanded):
    import openimcc
    from openimcc import gas
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.melt_backend import openimcc_bridge

    class Gas(dict):
        domain_flags = {}
        provenance_class = {}
        omitted_channels = {"O2": "missing from active table"} if channel == "reported-omission" else {}

    raw = Gas(K=0.001)
    if channel in {"nonfinite", "proven-zero", "complete"}:
        raw["O2"] = {"nonfinite": math.nan, "proven-zero": 0.0, "complete": 1e-5}[channel]
    monkeypatch.setattr(gas, "_default_reactions", lambda *_args: ((("K", ()), ("O2", ())), {}))
    calls = []

    def gas_at_commanded_oxygen(*_args, **_kwargs):
        calls.append("commanded")
        return raw

    def gas_at_solved_oxygen(*_args, **_kwargs):
        calls.append("effusion")
        return 1e-5, raw, {
            "residual": 0.0, "bracket": (-9.0, -3.0),
            "dominant_O_carriers": ("O2",), "dominant_metal_carriers": ("K",),
        }

    monkeypatch.setattr(openimcc, "evaluate_gas", gas_at_commanded_oxygen)
    monkeypatch.setattr(openimcc_bridge, "evaluate_gas_oxygen_balance", gas_at_solved_oxygen)
    row, experiment = _partial_without_total_pressure(
        None, fO2_Pa=Decimal("1000") if commanded else None,
    )
    handle = battery.open_battery_engine("openimcc")
    if not handle.available or handle.backend._gas is None:
        battery.close_battery_engine_handle(handle)
        pytest.skip("OpenIMCC melt and gas packs required for the producer path")
    try:
        prediction = predict_with_engine(
            Engine.OPENIMCC, row, experiment=experiment, handles={"openimcc": handle},
            isolated=False,
        )
    finally:
        battery.close_battery_engine_handle(handle)

    assert calls == ["commanded" if commanded else "effusion"]
    assert prediction.value == Decimal("100"), prediction.refusal_detail
    if channel in {"proven-zero", "complete"}:
        expected_total = Decimal("100") if channel == "proven-zero" else Decimal("101")
        assert prediction.identity.total_pressure_Pa == State.of(expected_total)
        assert any(n.policy and n.policy.id == "DP-108" for n in prediction.notices)
        return
    assert prediction.identity.total_pressure_Pa.is_unknown
    assert all(not n.policy or n.policy.id != "DP-108" for n in prediction.notices)
    assert any(
        n.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and "partial_pressure_sum_incomplete_gas_map: ['O2']" in n.reason
        for n in prediction.notices
    )


@pytest.mark.parametrize("point_charge", (False, True), ids=("identity-charge", "point-charge"))
@pytest.mark.parametrize(
    ("engine", "method", "invalid_components"),
    (
        (Engine.OPENIMCC, None, ()),
        (Engine.OPENIMCC, MethodToken.TABULATION, ()),
        (Engine.OPENIMCC, None, ("O2",)),
        (Engine.OPENIMCC, None, ("K",)),
        (Engine.VAPOROCK, None, ("O2",)),
    ),
    ids=(
        "complete-methodless", "tabulation", "invalid-oxygen-channel",
        "invalid-target-channel", "pressure-consuming",
    ),
)
def test_unstated_vapour_pressure_uses_dp108_partial_pressure_sum(
    monkeypatch, engine, method, invalid_components, point_charge,
) -> None:
    observation, experiment = _partial_without_total_pressure(
        method, fO2_Pa=Decimal("1000") if engine is Engine.VAPOROCK else None,
    )
    if point_charge:
        point_conditions = {
            **observation.point_conditions,
            "composition": F.located(observation.identity.composition.value),
        }
        observation = replace(
            observation,
            identity=replace(observation.identity, composition=State.unknown("on the point")),
            point_conditions=point_conditions,
        )
    partials = {
        name: pressure
        for name, pressure in {"K": 125.0, "KO": 75.0}.items()
        if name not in invalid_components
    }

    def _open(name):
        return types.SimpleNamespace(
            name=name,
            available=True,
            unavailable_reason=None,
            supports_intrinsic_fo2=False,
            backend=object(),
        )

    def _cell(_handle, _pot, *, po2, physical_pressure_bar=None, **_kwargs):
        expected_oxygen_mode = (
            PO2_COMMANDED if engine is Engine.VAPOROCK else PO2_OXYGEN_BALANCE_EFFUSION
        )
        assert po2.mode == expected_oxygen_mode
        assert physical_pressure_bar is None
        return types.SimpleNamespace(
            status="ok",
            refusal_reason=None,
            engine_reason=None,
            hostname="test",
            exit_code=0,
            notices=[],
            authority=None,
            certified_band=None,
            melt_activities={},
            melt_activity_coefficients={},
            gas_partial_pressures_Pa=partials,
            invalid_gas_partial_pressure_components=invalid_components,
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    prediction = predict_with_engine(
        engine,
        observation,
        experiment=experiment,
        bench=_cell_material_bench((CellMaterial.PT,)),
        isolated=False,
    )

    if engine is Engine.VAPOROCK or "K" in invalid_components:
        assert prediction.value is None
        assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
        assert prediction.refusal_detail == {
            "reason": "partial_pressure_sum_incomplete_gas_map",
            "policy": "DP-108",
            "invalid_components": list(invalid_components),
            "target_partial_pressure_Pa": partials.get("K"),
        }
        return

    assert prediction.value == Decimal(str(partials["K"]))
    dp108_notices = [notice for notice in prediction.notices if notice.policy and notice.policy.id == "DP-108"]
    if method is not None or invalid_components:
        assert not dp108_notices
        assert not prediction.identity.total_pressure_Pa.is_value
        incomplete = [
            notice for notice in prediction.notices
            if notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
            and "partial_pressure_sum_incomplete_gas_map" in notice.reason
        ]
        assert bool(incomplete) is bool(invalid_components)
        if incomplete:
            assert "O2" in incomplete[0].reason
        return

    assert prediction.identity.total_pressure_Pa == State.of(Decimal("200"))
    from simulator.battery.default_policy import load_policy_table, resolve

    policy = load_policy_table()["DP-108"]
    assert policy["condition"] == (
        "vapour equilibrium row with condensed phase; "
        "method unstated and total pressure unprinted"
    )
    assert policy["formula_owner"] == (
        "simulator.battery.score._sum_gas_partial_pressures_pa"
    )
    assert policy["status"] == "proposed"
    assert resolve(
        "DP-108", predicted_total_pressure_pa=Decimal("200")
    ).value == Decimal("200")
    (notice,) = dp108_notices
    assert notice.kind is NoticeKind.ASSUMED_PRESSURE
    assert notice.policy.status == "proposed"
    assert notice.policy.value == "200"
    assert "closed equilibrium contains no background gas" in notice.reason


def test_missing_total_pressure_is_a_visible_assumption(monkeypatch) -> None:
    seen = _capture_cell(monkeypatch)
    ident = replace(
        _melt((("Na2O", "0.4"), ("SiO2", "0.6")), "SiO2", fO2_Pa=Decimal("1e-4"), total_P=Decimal("1")),
        total_pressure_Pa=State.unknown("no total_pressure_Pa mapped from source"),
    )
    obs = F.observation("no-p", "exp", ident, Decimal("0.2"))
    prediction = predict_with_engine(Engine.VAPOROCK, obs, isolated=False)
    assert seen["pressure_bar"] == pytest.approx(1.01325)
    assert seen["mode"] == PO2_NOT_AN_INPUT
    assert seen["po2_bar"] is None
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "melt has no multivalent element" in notice.reason
        and "oxygen is not an input and was omitted" in notice.reason
        for notice in prediction.notices
    )
    assert any(
        notice.kind is NoticeKind.ASSUMED_PRESSURE
        and "original_reason=no total_pressure_Pa mapped from source" in notice.reason
        and notice.policy is not None
        and notice.policy.id == "DP-001"
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


@pytest.mark.parametrize("engine", [Engine.VAPOROCK, Engine.ALPHAMELTS])
def test_oxygen_balance_vapour_rows_use_engine_pressures_at_commanded_po2(
    engine, monkeypatch
) -> None:
    observation, experiment = _kems_partial(cell_material=None)
    bench = _cell_material_bench((CellMaterial.PT,))
    calls: list[tuple[float, dict[str, float]]] = []
    handle = types.SimpleNamespace(
        name=engine.value,
        available=True,
        unavailable_reason=None,
        supports_intrinsic_fo2=False,
        backend=types.SimpleNamespace(get_vapor_species=lambda: ["K", "O2"]),
    )

    def _open(_name):
        return handle

    def _cell(_handle, _pot, *, po2, **_kwargs):
        assert po2.mode == PO2_COMMANDED
        assert po2.po2_bar is not None and po2.po2_bar > 0.0
        pressures = {
            "K": 1e-6 * po2.po2_bar ** -0.25 * 1e5,
            "O2": 0.02264 * po2.po2_bar * 1e5,
        }
        calls.append((po2.po2_bar, pressures))
        return types.SimpleNamespace(
            status="ok",
            refusal_reason=None,
            engine_reason=None,
            hostname="test",
            exit_code=0,
            exit_signal=None,
            notices=[],
            authority=None,
            certified_band=None,
            melt_activities={},
            melt_activity_coefficients={},
            gas_partial_pressures_Pa=pressures,
            invalid_gas_partial_pressure_components=(),
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )

    prediction = predict_with_engine(
        engine,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )

    assert prediction.value is not None
    assert any(
        notice.reason == (
            "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
            "route=effusion_oxygen_balance"
        )
        for notice in prediction.notices
    )
    solved_notice = next(
        notice
        for notice in prediction.notices
        if notice.reason.startswith("fo2_oxygen_balance_effusion_solved:")
    )
    solved = json.loads(solved_notice.reason.split(":", 1)[1])
    score_pO2_bar, score_pressures = calls[-1]
    direct_pressures = _cell(
        handle,
        None,
        po2=Po2Request(PO2_COMMANDED, solved["pO2_bar"]),
    ).gas_partial_pressures_Pa
    assert score_pO2_bar == pytest.approx(solved["pO2_bar"], rel=1e-12)
    assert math.isclose(
        float(prediction.value), direct_pressures["K"], rel_tol=1e-9
    )
    assert math.isclose(
        score_pressures["K"], direct_pressures["K"], rel_tol=1e-9
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
