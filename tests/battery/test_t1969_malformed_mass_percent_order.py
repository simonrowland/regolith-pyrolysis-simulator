"""Focused regressions for the t-1969 mass-percent gate order."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import CodeType

import pytest

from simulator.battery import score as score_module
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import Composition, State
from simulator.melt_backend import base as backend_base
from tests.battery import factories as F
from tests.battery import mutation_t1969_restore_defects as mutation_recipes
from tests.battery.test_phase_composition_rows import _phase_identity


class _MetalAlloyAdapter:
    def capabilities(self) -> dict[str, bool]:
        return {"metal_alloy": True}


def _forbidden_conversion(_composition: Composition):
    raise AssertionError("mass-percent validation must not call composition_wt_pct")


def _mass_percent_observation(components: tuple[tuple[str, Decimal], ...]):
    identity = _phase_identity(
        "Fe", Quantity.EQUILIBRIUM_PHASE_COMPOSITION, role=PhaseRole.METAL
    )
    identity = replace(
        identity,
        composition=State.of(
            Composition(
                basis="printed_phase_composition",
                components=components,
                amount_basis=AmountBasis.MASS_PERCENT,
            )
        ),
    )
    return F.observation(
        "metal-mass-percent-map",
        "sample-1",
        identity,
        Decimal("5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="fixture-phase-source",
    )


def _assert_mass_percent_refuses_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
    components: tuple[tuple[str, Decimal], ...],
) -> None:
    monkeypatch.setattr(
        backend_base, "battery_adapter_class", lambda _engine: _MetalAlloyAdapter
    )
    monkeypatch.setattr(score_module, "composition_wt_pct", _forbidden_conversion)
    prediction = score_module.predict_with_engine(
        Engine.MAGEMIN, _mass_percent_observation(components)
    )
    assert prediction.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_malformed_metal_mass_percent_keeps_basis_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_mass_percent_refuses_before_conversion(
        monkeypatch,
        (("foobar", Decimal("50")), ("SiO2", Decimal("50"))),
    )


def test_admitted_metal_mass_percent_never_reaches_mole_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_mass_percent_refuses_before_conversion(
        monkeypatch,
        (("Fe", Decimal("50")), ("Ni", Decimal("50"))),
    )


def test_all_zero_metal_mass_percent_keeps_basis_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_mass_percent_refuses_before_conversion(
        monkeypatch,
        (("Fe", Decimal("0")), ("Ni", Decimal("0"))),
    )


def _install_predict_phase_row(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    code = compile(source, score_module.__file__, "exec")
    for name in score_module._predict_phase_row.__qualname__.split("."):
        code = next(
            item
            for item in code.co_consts
            if isinstance(item, CodeType) and item.co_name == name
        )
    monkeypatch.setattr(score_module._predict_phase_row, "__code__", code)


def test_alloy_gate_ahead_of_mass_percent_fails_the_taniguchi_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.battery.test_phase_composition_rows import (
        test_sum_near_one_mass_percent_bulk_stays_refused,
    )

    test_sum_near_one_mass_percent_bulk_stays_refused(monkeypatch)
    source = Path(score_module.__file__).read_text()
    block = (
        "    if (\n"
        "        quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION\n"
        "        and composition.amount_basis is AmountBasis.MASS_PERCENT\n"
        "        and not _equilibrium_mass_percent_map_is_usable(composition)\n"
        "    ):\n"
        "        # Fraction-shaped wt% fails migrate's closure (Taniguchi Fe 0.974).\n"
        "        # Basis mismatch precedes alloy capability only for that input.\n"
        "        return mass_percent_basis_refusal()\n"
    )
    if source.count(block) != 1:
        raise AssertionError("fraction-shaped mass-percent block was not unique")
    _install_predict_phase_row(monkeypatch, source.replace(block, "", 1))
    with pytest.raises(AssertionError) as caught:
        test_sum_near_one_mass_percent_bulk_stays_refused(monkeypatch)
    assert "phase assemblage must not be called" not in str(caught.value)


def test_blanket_mass_percent_return_fails_the_henao_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring the blanket mass-percent return hides usable Henao alloy maps."""

    from tests.test_henao_2001_ni_cr_mn_co_cu_slag_distribution_extract import (
        test_henao_tables_migrate_as_typed_compositions_with_conditions as henao,
    )

    source = Path(score_module.__file__).read_text()
    conjunct = (
        "        and not _equilibrium_mass_percent_map_is_usable(composition)\n"
    )
    if source.count(conjunct) != 1:
        raise AssertionError("usable-map conjunct was not unique")
    _install_predict_phase_row(monkeypatch, source.replace(conjunct, "", 1))
    with pytest.raises(AssertionError, match="usable alloy map must reach"):
        henao(monkeypatch)


def test_malformed_metal_mutation_anchor_fails_the_conversion_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backend_base, "battery_adapter_class", lambda _engine: _MetalAlloyAdapter
    )
    monkeypatch.setattr(score_module, "composition_wt_pct", _forbidden_conversion)
    observation = _mass_percent_observation(
        (("foobar", Decimal("50")), ("SiO2", Decimal("50")))
    )
    baseline = score_module.predict_with_engine(Engine.MAGEMIN, observation)
    assert baseline.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH

    mutant_path = tmp_path / "score.py"
    mutant_path.write_text(Path(score_module.__file__).read_text())
    monkeypatch.setattr(mutation_recipes, "SCORE", mutant_path)
    mutation_recipes.restore("malformed-metal-mass-percent")
    code = compile(mutant_path.read_text(), score_module.__file__, "exec")
    for name in score_module._predict_phase_row.__qualname__.split("."):
        code = next(
            item
            for item in code.co_consts
            if isinstance(item, CodeType) and item.co_name == name
        )
    monkeypatch.setattr(score_module._predict_phase_row, "__code__", code)

    with pytest.raises(
        AssertionError,
        match="mass-percent validation must not call composition_wt_pct",
    ):
        score_module.predict_with_engine(Engine.MAGEMIN, observation)
