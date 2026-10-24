"""alphaMELTS compilation thermo uses the reported phase and species basis."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from simulator.accounting.formulas import resolve_species_formula
import simulator.battery.compilation_tier as compilation_tier
from simulator.battery.compilation_tier import predict_thermo_attempt
from simulator.battery.enums import (
    Authority,
    Engine,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    QUANTITY_UNITS,
    RefusalReason,
)
from simulator.battery.records import Species, State
from tests.battery import factories as F


_FORMULA = "SiO2"
_FORMULA_MOLAR_MASS_KG = resolve_species_formula(_FORMULA).molar_mass_kg_per_mol()
_TWO_FORMULA_MASS_KG = 2.0 * _FORMULA_MOLAR_MASS_KG


def _observation(
    quantity: Quantity,
    *,
    formula: str = _FORMULA,
    phase: Phase = Phase.CR,
    temperature_K: str = "298.15",
    polymorph: Polymorph | None = Polymorph.QUARTZ,
):
    identity = F.o2_identity(Decimal(temperature_K))
    identity = replace(
        identity,
        quantity=quantity,
        per=State.of(PerBasis.MOL_SPECIES),
        standard_pressure_Pa=State.of(Decimal("100000")),
        species=Species(
            formula,
            phase,
            polymorph=(
                State.of(polymorph)
                if phase is Phase.CR and polymorph is not None
                else None
            ),
        ),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
        subtype=(
            State.of("H(T)-H(298.15 K)")
            if quantity is Quantity.H_MINUS_H298
            else State.not_applicable("not an enthalpy increment")
        ),
    )
    return F.observation(
        f"alpha-{formula}-{phase.value}-{quantity.value}",
        "alpha-thermo-experiment",
        identity,
        Decimal("0"),
    )


def _quartz_result(
    enthalpy_J: float,
    *,
    diagnostics: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status="ok",
        diagnostics=diagnostics or {},
        phase_thermo={
            "quartz": {
                "enthalpy_J": enthalpy_J,
                "entropy_J_K": 2.0 * 41.463,
                "heat_capacity_J_K": 2.0 * 44.589,
                "reference_mass_kg": _TWO_FORMULA_MASS_KG,
            }
        },
        phase_compositions={"quartz": {"SiO2": 100.0}},
        phase_instances=[
            {"phase": "quartz", "formula_or_endmember_token": "SiO2"}
        ],
    )


@pytest.fixture
def quartz_phase_result() -> SimpleNamespace:
    return _quartz_result(0.0)


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [(Quantity.S, 41.463), (Quantity.CP, 44.589)],
)
def test_alpha_phase_extensive_values_normalize_to_formula_moles(
    quantity: Quantity, expected: float, quartz_phase_result: SimpleNamespace
) -> None:
    observation = _observation(quantity)

    attempt = predict_thermo_attempt(
        Engine.ALPHAMELTS,
        observation,
        pure_phase=lambda *_args: quartz_phase_result,
    )

    assert attempt.value is not None
    assert float(attempt.value) == pytest.approx(expected)
    assert attempt.unit == QUANTITY_UNITS[quantity]
    # JANAF O-037, quartz at 298.15 K: S=41.463 and Cp=44.589 J/(mol K).


def test_alpha_enthalpy_increment_uses_same_quartz_at_t_and_h298() -> None:
    observation = _observation(Quantity.H_MINUS_H298, temperature_K="600")
    out_of_band = {
        "commissioning_notice": {
            "certified_band": {"temperature_K": [1073.15, 1700.0]},
            "warnings": ["temperature 298.15 K outside certified band"],
        }
    }
    results = {
        600.0: _quartz_result(2.0 * -893_961.0),
        298.15: _quartz_result(2.0 * -910_857.0, diagnostics=out_of_band),
    }
    calls: list[float] = []

    def pure_phase(_engine, _formula, temperature_K, _pressure_bar):
        calls.append(temperature_K)
        return results[temperature_K]

    attempt = predict_thermo_attempt(
        Engine.ALPHAMELTS, observation, pure_phase=pure_phase
    )

    assert attempt.value == Decimal("16.896")
    assert calls == [600.0, 298.15]
    assert attempt.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in attempt.notices
    )
    # JANAF O-037, quartz at 600 K: H-H(Tr)=16.896 kJ/mol.


def test_alpha_refuses_species_outside_its_pure_phase_oxide_model() -> None:
    observation = _observation(
        Quantity.CP, formula="S", phase=Phase.CR, polymorph=None
    )

    attempt = predict_thermo_attempt(
        Engine.ALPHAMELTS,
        observation,
        pure_phase=lambda *_args: pytest.fail("unsupported phase must not run"),
    )

    assert attempt.value is None
    assert (
        attempt.refusal_reason
        is RefusalReason.ENGINE_DOES_NOT_REPORT_PHASE_COMPOSITION
    )
    assert attempt.refusal_detail["reason"] == (
        "alphamelts-engine-does-not-report-phase-thermo"
    )


@pytest.mark.live_engine
def test_live_alpha_quartz_h_increment_flags_out_of_band_h298() -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({"mode": "subprocess"})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    previous = compilation_tier._BACKENDS.get(Engine.ALPHAMELTS)
    compilation_tier._BACKENDS[Engine.ALPHAMELTS] = backend
    try:
        attempt = predict_thermo_attempt(
            Engine.ALPHAMELTS,
            _observation(Quantity.H_MINUS_H298, temperature_K="400"),
        )
    finally:
        backend.close()
        if previous is None:
            compilation_tier._BACKENDS.pop(Engine.ALPHAMELTS, None)
        else:
            compilation_tier._BACKENDS[Engine.ALPHAMELTS] = previous

    assert attempt.value is not None
    assert attempt.authority is Authority.EXTRAPOLATED
    assert "phase=quartz" in attempt.call_evidence
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for notice in attempt.notices
    )
