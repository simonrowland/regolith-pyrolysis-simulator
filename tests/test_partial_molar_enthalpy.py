"""Partial molar enthalpy on a fixed ThermoEngine phase model.

Expected enthalpies are the closed form in this file. They are not copied
from ``partial_molar_enthalpy_j_per_mol``.

H0, S0, Cp and Tref below are synthetic. They are not a 298.15 K standard.
mu = H0 - T S0 + Cp*((T - Tref) - T ln(T/Tref))
implies H = H0 + Cp*(T - Tref). An ideal term R T ln x cancels, so Hbar is
H0 and does not depend on composition. The central difference of a linear
mu is exact aside from roundoff. The Cp term is the O(dT^2) check.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    PerBasis,
    Phase,
    Quantity,
    ReferenceStateConvention,
    RefusalReason,
)
from simulator.battery.identity import State
from simulator.battery.records import Composition, Species, StandardState
from simulator.battery.score import predict_with_engine
from tests.battery import factories as F

# Synthetic closed form. Not a literature standard state.
_H0_SIO2 = -50000.0
_H0_MGO = -10000.0
_S0 = 40.0
_CP = 80.0
_TREF = 1000.0
_T_K = 1500.0
_IDEAL_ABS_J = 1.0e-3
_CURVED_ABS_J = 1.0


def _exact_enthalpy(temperature_K: float, h0: float = _H0_SIO2) -> float:
    return h0 + _CP * (temperature_K - _TREF)


def _curved_mu(temperature_K: float, h0: float = _H0_SIO2) -> float:
    return (
        h0
        - temperature_K * _S0
        + _CP
        * (
            (temperature_K - _TREF)
            - temperature_K * math.log(temperature_K / _TREF)
        )
    )


def _ideal_mu(temperature_K: float, h0: float, mole_fraction: float) -> float:
    from simulator.physical_constants import GAS_CONSTANT

    return (
        h0
        - temperature_K * _S0
        + GAS_CONSTANT * temperature_K * math.log(mole_fraction)
    )


def _hbar(mu_at, components, temperature_K, *, dt_K=None):
    from simulator.chemistry.partial_molar_enthalpy import (
        central_difference_partial_molar_enthalpies,
    )

    kwargs = {} if dt_K is None else {"dt_K": dt_K}
    return central_difference_partial_molar_enthalpies(
        mu_at,
        lambda _temperature: ("fixed-model", tuple(components)),
        components,
        temperature_K,
        Phase.L.value,
        **kwargs,
    )


def test_ideal_hbar_is_h0_and_not_mu() -> None:
    """mu = mu0 + R T ln x has Hbar = H0, independent of composition."""

    from simulator.physical_constants import GAS_CONSTANT

    mole_fraction = 0.2
    rows = _hbar(
        lambda temperature: (
            _ideal_mu(temperature, _H0_SIO2, mole_fraction),
        ),
        ("SiO2",),
        _T_K,
    )
    value = rows[0][4]
    mu_like = _H0_SIO2 + GAS_CONSTANT * _T_K * math.log(mole_fraction)
    assert abs(value - _H0_SIO2) < _IDEAL_ABS_J
    assert abs(value - mu_like) > 1.0


def test_named_component_keeps_its_index() -> None:
    fractions = {"SiO2": 0.75, "MgO": 0.25}
    enthalpies = {"SiO2": _H0_SIO2, "MgO": _H0_MGO}
    rows = _hbar(
        lambda temperature: tuple(
            _ideal_mu(temperature, enthalpies[name], fractions[name])
            for name in ("SiO2", "MgO")
        ),
        ("SiO2", "MgO"),
        _T_K,
    )
    assert [row[0] for row in rows] == ["SiO2", "MgO"]
    assert abs(rows[0][4] - _H0_SIO2) < _IDEAL_ABS_J
    assert abs(rows[1][4] - _H0_MGO) < _IDEAL_ABS_J


def test_curved_mu_converges_when_the_step_is_halved() -> None:
    exact = _exact_enthalpy(_T_K)
    full = _hbar(lambda temperature: (_curved_mu(temperature),), ("SiO2",), _T_K)
    half = _hbar(
        lambda temperature: (_curved_mu(temperature),),
        ("SiO2",),
        _T_K,
        dt_K=0.5,
    )
    assert abs(full[0][4] - exact) < _CURVED_ABS_J
    assert abs(half[0][4] - exact) < _CURVED_ABS_J
    assert abs(full[0][4] - half[0][4]) < _CURVED_ABS_J


def test_celsius_number_is_not_the_kelvin_enthalpy() -> None:
    exact = _exact_enthalpy(_T_K)
    kelvin = _hbar(
        lambda temperature: (_curved_mu(temperature),),
        ("SiO2",),
        _T_K,
    )
    celsius_as_kelvin = _hbar(
        lambda temperature: (_curved_mu(temperature),),
        ("SiO2",),
        _T_K - 273.15,
    )
    assert abs(kelvin[0][4] - exact) < _CURVED_ABS_J
    assert abs(celsius_as_kelvin[0][4] - exact) > 1.0


def test_phase_model_change_refuses_with_no_number() -> None:
    from simulator.chemistry.partial_molar_enthalpy import (
        PartialMolarEnthalpyRefusal,
        REASON_PHASE_MODEL_CHANGED,
        central_difference_partial_molar_enthalpies,
    )

    calls = {"n": 0}

    def mu_at(temperature):
        calls["n"] += 1
        return (_curved_mu(temperature),)

    def identity_at(temperature):
        if temperature > _T_K:
            return ("other-model",)
        return ("fixed-model",)

    with pytest.raises(PartialMolarEnthalpyRefusal) as caught:
        central_difference_partial_molar_enthalpies(
            mu_at,
            identity_at,
            ("SiO2",),
            _T_K,
            Phase.L.value,
        )
    assert caught.value.reason == REASON_PHASE_MODEL_CHANGED
    assert caught.value.reason == "partial_enthalpy_phase_model_changed"
    assert calls["n"] == 0


def test_dt_is_a_code_constant_and_not_a_cache_input() -> None:
    import simulator.engine_cache as engine_cache
    from simulator.chemistry.partial_molar_enthalpy import (
        PARTIAL_MOLAR_ENTHALPY_DT_K,
        central_difference_partial_molar_enthalpies,
    )
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    assert engine_cache.ENGINE_CHEMISTRY_VERSION["thermoengine"] == 4
    projection = engine_cache._ENGINE_CACHE_SETTINGS_PROJECTIONS["thermoengine"]
    assert set(projection) == {
        "model",
        "configured_vapor_transport_pO2_bar",
        "configured_vapor_transport_floor_bar",
        "redox_buffer",
        "fO2_offset",
        "Fe3Fet_ratio",
    }
    rendered = " ".join(projection)
    assert "enthalpy" not in rendered
    assert "dt" not in rendered.casefold()
    source = Path(engine_cache.__file__).read_text(encoding="utf-8")
    assert "adds per-component partial molar enthalpy" in source
    assert "thermoengine v4:" in source
    default = inspect.signature(
        central_difference_partial_molar_enthalpies
    ).parameters["dt_K"].default
    assert default is PARTIAL_MOLAR_ENTHALPY_DT_K
    method = inspect.getsource(ThermoEngineTransport._partial_molar_enthalpy_records)
    assert "dt_K" not in method
    assert "PARTIAL_MOLAR_ENTHALPY_DT" not in method


class _Phase:
    def __init__(self, names=("SiO2", "MgO")):
        self.endmember_names = list(names)
        self.temperatures: list[float] = []
        self.pressures: list[float] = []
        self.moles: list[float] | None = None
        self.equilibrate_count = 0
        self.enthalpy_count = 0

    def chem_potential(self, temperature, pressure, mol=None):
        self.temperatures.append(float(temperature))
        self.pressures.append(float(pressure))
        self.moles = [float(value) for value in mol[0]]
        fractions = {"SiO2": 0.75, "MgO": 0.25}
        enthalpies = {"SiO2": _H0_SIO2, "MgO": _H0_MGO}
        return [
            _ideal_mu(float(temperature), enthalpies[name], fractions[name])
            for name in self.endmember_names
        ]

    def enthalpy(self, *_args, **_kwargs):
        self.enthalpy_count += 1
        raise AssertionError("whole-phase enthalpy is not Hbar")

    def equilibrate_tp(self, *_args, **_kwargs):
        self.equilibrate_count += 1


class _Entry:
    def __init__(self, phase):
        self.phaseClassInstance = phase


class _Native:
    def __init__(self, phases):
        self.dictionaryOfPhasesInSystem = {
            name: _Entry(phase) for name, phase in phases.items()
        }


class _Melts:
    def __init__(self, phases, assemblage=None):
        self.melts = _Native(phases)
        self.assemblage = list(assemblage if assemblage is not None else phases)
        self.equilibrate_count = 0

    def get_composition_of_phase(self, _root, phase_name, kind):
        assert kind == "component"
        assert phase_name == "liquid"
        return {"SiO2": 3.0, "MgO": 1.0}

    def get_list_of_phases_in_assemblage(self, _root):
        return list(self.assemblage)

    def equilibrate_tp(self, *_args, **_kwargs):
        self.equilibrate_count += 1


def _transport():
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    transport = object.__new__(ThermoEngineTransport)
    transport._liq_phase = None
    return transport


def test_adapter_stencil_does_not_reequilibrate() -> None:
    phase = _Phase()
    melts = _Melts({"liquid": phase})
    rows = _transport()._partial_molar_enthalpy_records(
        melts=melts,
        root=object(),
        phases=("liquid",),
        temperature_C=1500.0 - 273.15,
        pressure_bar=1.0e-9,
    )
    assert phase.equilibrate_count == 0
    assert melts.equilibrate_count == 0
    assert phase.enthalpy_count == 0
    assert phase.moles == [0.75, 0.25]
    assert set(phase.temperatures) == {1499.0, 1500.0, 1501.0}
    assert set(phase.pressures) == {1.0e-6}
    by_name = {row[0]: row for row in rows}
    assert abs(by_name["SiO2"][4] - _H0_SIO2) < _IDEAL_ABS_J
    assert abs(by_name["MgO"][4] - _H0_MGO) < _IDEAL_ABS_J
    assert by_name["SiO2"][1] == Phase.L.value
    assert by_name["SiO2"][2] == PerBasis.MOL_SPECIES.value
    assert (
        by_name["SiO2"][3]
        == ReferenceStateConvention.CONDENSED_ELEMENT_REFERENCE.value
    )
    assert by_name["SiO2"][5] is None


def test_assemblage_change_refuses_every_component() -> None:
    from simulator.chemistry.partial_molar_enthalpy import REASON_ASSEMBLAGE_CHANGED

    phase = _Phase()
    melts = _Melts({"liquid": phase}, assemblage=("liquid", "olivine"))
    rows = _transport()._partial_molar_enthalpy_records(
        melts=melts,
        root=object(),
        phases=("liquid",),
        temperature_C=1500.0 - 273.15,
        pressure_bar=1.0,
    )
    assert rows
    assert all(row[4] is None for row in rows)
    assert {row[5] for row in rows} == {REASON_ASSEMBLAGE_CHANGED}


def test_endmember_list_change_is_a_phase_model_refusal() -> None:
    from simulator.chemistry.partial_molar_enthalpy import REASON_PHASE_MODEL_CHANGED

    phase = _Phase()
    original = phase.chem_potential

    def mutating(temperature, pressure, mol=None):
        values = original(temperature, pressure, mol=mol)
        phase.endmember_names = ["SiO2", "MgO", "FeO"]
        return values

    phase.chem_potential = mutating
    melts = _Melts({"liquid": phase})
    rows = _transport()._partial_molar_enthalpy_records(
        melts=melts,
        root=object(),
        phases=("liquid",),
        temperature_C=1500.0 - 273.15,
        pressure_bar=1.0,
    )
    assert rows
    assert all(row[4] is None for row in rows)
    assert {row[5] for row in rows} == {REASON_PHASE_MODEL_CHANGED}


def test_missing_chem_potential_is_a_typed_refusal() -> None:
    from simulator.chemistry.partial_molar_enthalpy import REASON_NO_CHEM_POTENTIAL

    phase = _Phase(names=())
    phase.chem_potential = None
    melts = _Melts({"forsterite": phase})
    rows = _transport()._partial_molar_enthalpy_records(
        melts=melts,
        root=object(),
        phases=("forsterite",),
        temperature_C=1500.0 - 273.15,
        pressure_bar=1.0,
    )
    assert len(rows) == 1
    assert rows[0][0] == "forsterite"
    assert rows[0][1] == Phase.CR.value
    assert rows[0][4] is None
    assert rows[0][5] == REASON_NO_CHEM_POTENTIAL


def test_phase_tokens_follow_the_existing_liquid_rule() -> None:
    transport = _transport()
    assert transport._partial_enthalpy_phase_token("liquid") == Phase.L.value
    assert transport._partial_enthalpy_phase_token("Liquid_Model") == Phase.L.value
    assert transport._partial_enthalpy_phase_token("fluid") == Phase.G.value
    assert transport._partial_enthalpy_phase_token("gas") == Phase.G.value
    assert transport._partial_enthalpy_phase_token("forsterite") == Phase.CR.value
    assert transport._partial_enthalpy_phase_token("  ") is None


def _sample_rows():
    from simulator.chemistry.partial_molar_enthalpy import enthalpy_record

    return [
        enthalpy_record("O2Si", Phase.L.value, 9600.0, None),
        enthalpy_record("MgO", Phase.CR.value, -10000.0, None),
    ]


def test_hot_round_trip_keeps_partial_enthalpy() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EquilibrateCell,
        Po2Request,
    )
    from simulator.engine_cache import live_hot
    from simulator.melt_backend.base import (
        EquilibriumResult,
        equilibrium_cache_from_value,
    )
    import simulator.engine_cache as engine_cache

    rows = _sample_rows()
    result = EquilibriumResult(
        liquid_fraction=1.0,
        partial_molar_enthalpy_J_per_mol=rows,
    )
    omitted = EquilibriumResult(liquid_fraction=1.0)
    empty = EquilibriumResult(
        liquid_fraction=1.0,
        partial_molar_enthalpy_J_per_mol=[],
    )
    assert omitted.to_engine_cache_value(engine_id="magemin") == (
        empty.to_engine_cache_value(engine_id="magemin")
    )
    assert len(omitted.to_engine_cache_value(engine_id="magemin")) == 11
    hot = result.to_engine_cache_value(engine_id="thermoengine")
    assert len(hot) == 12
    raw, provenance = engine_cache._encode_hot(hot)
    restored = EquilibriumResult.from_hot(
        engine_cache._decode_hot(raw, provenance),
        engine_id="thermoengine",
    )
    assert restored.partial_molar_enthalpy_J_per_mol == rows

    base = omitted.to_engine_cache_value(engine_id="magemin")
    metadata = equilibrium_cache_from_value(
        [*base, {"imcc_model_id": "model-a"}],
        engine_id="magemin",
    )
    assert metadata["imcc_model_id"] == "model-a"
    assert "partial_molar_enthalpy_J_per_mol" not in metadata

    cell = EquilibrateCell(
        pot_id="pot",
        engine=Engine.THERMOENGINE.value,
        temperature_K=1500.0,
        po2=Po2Request(mode="not_an_input", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )
    assert "partial_molar_enthalpy_J_per_mol" not in cell.as_payload()
    assert len(cell.to_engine_cache_value()) == 12
    old = EquilibrateCell.from_engine_cache_value(cell.to_engine_cache_value())
    assert old.partial_molar_enthalpy_J_per_mol == []
    cell.partial_molar_enthalpy_J_per_mol = rows
    cell.model_id = "melts-v1.2.0"
    assert len(cell.to_engine_cache_value()) == 13
    payload = cell.as_payload()
    assert EquilibrateCell.from_payload(payload).as_payload() == payload
    hot_cell = live_hot(cell.to_hot())
    assert len(hot_cell) == 13
    restored_cell = EquilibrateCell.from_hot(hot_cell)
    assert restored_cell.partial_molar_enthalpy_J_per_mol == rows
    assert restored_cell.model_id == "melts-v1.2.0"


def _partial_identity(
    *,
    formula: str = "SiO2",
    phase: Phase = Phase.L,
    endmember_formula: str | None = None,
    endmember_phase: Phase | None = None,
    component_basis: str | None = None,
    per: PerBasis = PerBasis.MOL_SPECIES,
    convention: ReferenceStateConvention = (
        ReferenceStateConvention.CONDENSED_ELEMENT_REFERENCE
    ),
):
    endmember_formula = formula if endmember_formula is None else endmember_formula
    endmember_phase = phase if endmember_phase is None else endmember_phase
    component_basis = formula if component_basis is None else component_basis
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("SiO2", Decimal("0.5")), ("MgO", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula=formula,
        T_K=Decimal("1500"),
        component_basis=component_basis,
        composition=composition,
        endmember_phase=endmember_phase,
    )
    return replace(
        identity,
        quantity=Quantity.PARTIAL_MOLAR_ENTHALPY,
        species=Species(formula, phase),
        per=State.of(per),
        standard_pressure_Pa=State.of(Decimal("100000")),
        reference_state=State.of(
            StandardState(
                convention=convention,
                endmember=Species(endmember_formula, endmember_phase),
                component_basis=component_basis,
                reference_pressure_bar=Decimal("1"),
            )
        ),
        fO2_Pa=State.not_applicable("partial molar enthalpy does not take oxygen"),
        reaction=State.not_applicable("no reaction"),
    )


def _score(monkeypatch, identity, rows):
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        EquilibrateCell,
    )

    experiment = F.tabulation_experiment(T_K=Decimal("1500"))
    observation = F.observation(
        "partial-enthalpy-sio2",
        experiment.experiment_id,
        identity,
        Decimal("9.6"),
    )
    handle = EngineHandle(
        name=Engine.THERMOENGINE.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=True,
    )
    monkeypatch.setattr(battery, "open_battery_engine", lambda _name: handle)
    calls = {"n": 0}

    def successful_cell(engine_handle, pot, **kwargs):
        calls["n"] += 1
        return EquilibrateCell(
            pot_id=pot.pot_id,
            engine=engine_handle.name,
            temperature_K=kwargs["temperature_K"],
            po2=kwargs["po2"],
            status="ok",
            refusal_reason=None,
            engine_status="ok",
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={},
            liquid_fraction=1.0,
            wall_s=0.0,
            cpu_s=0.0,
            hostname="test",
            partial_molar_enthalpy_J_per_mol=rows,
        )

    monkeypatch.setattr(battery, "equilibrate_cell", successful_cell)
    prediction = predict_with_engine(
        Engine.THERMOENGINE,
        observation,
        experiment=experiment,
        handles={Engine.THERMOENGINE.value: handle},
        isolated=False,
    )
    return prediction, calls["n"]


def test_score_converts_joules_to_kilojoules(monkeypatch) -> None:
    from simulator.chemistry.partial_molar_enthalpy import enthalpy_record

    prediction, calls = _score(
        monkeypatch,
        _partial_identity(),
        [enthalpy_record("O2Si", Phase.L.value, 9600.0, None)],
    )
    assert calls == 1
    assert prediction.value == Decimal("9.6"), prediction.refusal_detail
    assert prediction.unit == "kJ_per_declared_mol_basis"
    assert prediction.refusal_reason is None


def test_reference_mismatch_refuses(monkeypatch) -> None:
    from simulator.chemistry.partial_molar_enthalpy import (
        REASON_REFERENCE_MISMATCH,
        enthalpy_record,
    )

    # The record is the engine's condensed-element reference. A Raoultian
    # observation is a different reference and must not receive that number.
    prediction, calls = _score(
        monkeypatch,
        _partial_identity(
            convention=ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        ),
        [enthalpy_record("SiO2", Phase.L.value, 9600.0, None)],
    )
    assert calls == 1
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert prediction.refusal_detail["reason"] == REASON_REFERENCE_MISMATCH


def test_component_phase_basis_and_duplicate_refuse(monkeypatch) -> None:
    from simulator.chemistry.partial_molar_enthalpy import (
        REASON_AMBIGUOUS,
        REASON_BASIS_MISMATCH,
        REASON_COMPONENT_MISMATCH,
        REASON_COMPONENT_NOT_REPORTED,
        REASON_PHASE_MISMATCH,
        REASON_PHASE_MODEL_CHANGED,
        enthalpy_record,
        refusal_record,
    )

    missing, calls = _score(monkeypatch, _partial_identity(), [])
    assert calls == 1
    assert missing.value is None
    assert missing.refusal_detail["reason"] == REASON_COMPONENT_NOT_REPORTED

    other, _calls = _score(
        monkeypatch,
        _partial_identity(),
        [enthalpy_record("MgO", Phase.L.value, 9600.0, None)],
    )
    assert other.value is None
    assert other.refusal_detail["reason"] == REASON_COMPONENT_NOT_REPORTED

    mismatched, _calls = _score(
        monkeypatch,
        _partial_identity(endmember_formula="MgO"),
        [enthalpy_record("SiO2", Phase.L.value, 9600.0, None)],
    )
    assert mismatched.value is None
    assert mismatched.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert mismatched.refusal_detail["reason"] == REASON_COMPONENT_MISMATCH

    phase, _calls = _score(
        monkeypatch,
        _partial_identity(),
        [enthalpy_record("SiO2", Phase.CR.value, 9600.0, None)],
    )
    assert phase.value is None
    assert phase.refusal_detail["reason"] == REASON_PHASE_MISMATCH

    species_phase, _calls = _score(
        monkeypatch,
        _partial_identity(endmember_phase=Phase.CR),
        [enthalpy_record("SiO2", Phase.L.value, 9600.0, None)],
    )
    assert species_phase.value is None
    assert species_phase.refusal_detail["reason"] == REASON_PHASE_MISMATCH

    other_basis = enthalpy_record("SiO2", Phase.L.value, 9600.0, None)
    other_basis[2] = PerBasis.MOL_O2.value
    basis, _calls = _score(
        monkeypatch,
        _partial_identity(per=PerBasis.MOL_O2),
        [other_basis],
    )
    assert basis.value is None
    assert basis.refusal_reason is RefusalReason.IDENTITY_MISMATCH
    assert basis.refusal_detail["reason"] == REASON_BASIS_MISMATCH

    duplicate = enthalpy_record("SiO2", Phase.L.value, 9600.0, None)
    ambiguous, _calls = _score(
        monkeypatch,
        _partial_identity(),
        [duplicate, list(duplicate)],
    )
    assert ambiguous.value is None
    assert ambiguous.refusal_detail["reason"] == REASON_AMBIGUOUS

    stencil, _calls = _score(
        monkeypatch,
        _partial_identity(),
        [refusal_record("SiO2", Phase.L.value, REASON_PHASE_MODEL_CHANGED)],
    )
    assert stencil.value is None
    assert stencil.refusal_reason is RefusalReason.UNSUPPORTED
    assert stencil.refusal_detail["reason"] == REASON_PHASE_MODEL_CHANGED


def test_internal_nasa_and_catalog_stay_quantity_not_predicted(monkeypatch) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    monkeypatch.setattr(
        battery,
        "open_battery_engine",
        lambda *_args, **_kwargs: pytest.fail("engine was opened"),
    )
    monkeypatch.setattr(
        battery,
        "equilibrate_cell",
        lambda *_args, **_kwargs: pytest.fail("engine was equilibrated"),
    )
    experiment = F.tabulation_experiment(T_K=Decimal("1500"))
    observation = F.observation(
        "partial-enthalpy-lock",
        experiment.experiment_id,
        _partial_identity(),
        Decimal("9.6"),
    )
    for engine in (
        Engine.INTERNAL_ANALYTICAL,
        Engine.NASA_CEA_9,
        Engine.CATALOG_EVALUATOR,
    ):
        prediction = predict_with_engine(engine, observation, experiment=experiment)
        assert prediction.value is None
        assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
        assert prediction.refusal_detail["reason"] == "quantity_not_predicted"


def test_mutation_phase_enthalpy_is_not_partial(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    monkeypatch.setattr(
        owner,
        "partial_molar_enthalpy_j_per_mol",
        lambda *_args, **_kwargs: 999.0,
    )
    with pytest.raises(AssertionError):
        test_ideal_hbar_is_h0_and_not_mu()


def test_mutation_reequilibrate_is_detected(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    original = owner.partial_molar_enthalpies_from_phase

    def wrapped(phase, **kwargs):
        phase.equilibrate_tp(kwargs["temperature_K"], kwargs["pressure_bar"])
        return original(phase, **kwargs)

    monkeypatch.setattr(owner, "partial_molar_enthalpies_from_phase", wrapped)
    with pytest.raises(AssertionError):
        test_adapter_stencil_does_not_reequilibrate()


def test_mutation_component_index_swap_is_detected(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    original = owner.central_difference_partial_molar_enthalpies

    def swapped(*args, **kwargs):
        rows = original(*args, **kwargs)
        values = [row[4] for row in rows]
        for row, value in zip(rows, reversed(values)):
            row[4] = value
        return rows

    monkeypatch.setattr(owner, "central_difference_partial_molar_enthalpies", swapped)
    with pytest.raises(AssertionError):
        test_named_component_keeps_its_index()


def test_mutation_celsius_offset_is_detected(monkeypatch) -> None:
    import simulator.physical_constants as constants

    monkeypatch.setattr(constants, "CELSIUS_TO_KELVIN_OFFSET", 0.0)
    with pytest.raises(AssertionError):
        test_adapter_stencil_does_not_reequilibrate()


def test_mutation_dropped_cache_field_is_detected(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    monkeypatch.setattr(owner, "compact_partial_molar_enthalpy_records", lambda _rows: None)
    with pytest.raises(AssertionError):
        test_hot_round_trip_keeps_partial_enthalpy()


def test_mutation_mismatched_reference_is_detected(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    original = owner.select_partial_molar_enthalpy_j_per_mol

    def forced(records, **kwargs):
        kwargs["reference"] = owner.REFERENCE_CONDENSED_ELEMENT
        return original(records, **kwargs)

    monkeypatch.setattr(owner, "select_partial_molar_enthalpy_j_per_mol", forced)
    with pytest.raises(AssertionError):
        test_reference_mismatch_refuses(monkeypatch)


def test_mutation_kilojoules_treated_as_joules_is_detected(monkeypatch) -> None:
    import simulator.chemistry.partial_molar_enthalpy as owner

    monkeypatch.setattr(
        owner,
        "partial_molar_enthalpy_kJ_per_mol",
        lambda joules: Decimal(str(joules)),
    )
    with pytest.raises(AssertionError):
        test_score_converts_joules_to_kilojoules(monkeypatch)
