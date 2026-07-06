from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.accounting.formulas import parse_formula
from simulator.accounting.completeness import (
    CompletionContractBlocked,
    TargetExtractionCompleteness,
    aggregate_extraction_completeness,
    completion_contracts_from_setpoints,
    extraction_completeness_by_target,
    target_species_yield_by_initial_cleaned_melt,
    validate_completion_contract_coverage,
    vapor_contract_completeness,
)
from simulator.state import MOLAR_MASS


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text())


def _contracts_by_id() -> dict:
    return {
        contract.contract_id: contract
        for contract in completion_contracts_from_setpoints(_load_setpoints())
    }


class _FakeLedger:
    def __init__(self, accounts: dict[str, dict[str, float]]) -> None:
        self._accounts = accounts

    def kg_by_account(self, account: str | None = None):
        if account is None:
            return {
                name: dict(species_kg)
                for name, species_kg in self._accounts.items()
            }
        return dict(self._accounts.get(account, {}))


class _FakeQueries:
    def __init__(
        self,
        accounts: dict[str, dict[str, float]],
        reagents: dict[str, float] | None = None,
        feedstock_recovered_reagents: dict[str, float] | None = None,
        c3_credit_outstanding: dict[str, float] | None = None,
        additives_kg: dict[str, float] | None = None,
        non_feedstock_reagent_element_by_account: (
            dict[str, dict[str, float]] | None
        ) = None,
    ) -> None:
        self.ledger = _FakeLedger(accounts)
        self.sim = SimpleNamespace(
            record=SimpleNamespace(additives_kg=additives_kg or {})
        )
        self._reagents = reagents or {}
        self._feedstock_recovered_reagents = feedstock_recovered_reagents or {}
        self._c3_credit_outstanding = c3_credit_outstanding or {}
        self._non_feedstock_reagent_element_by_account = (
            non_feedstock_reagent_element_by_account or {}
        )

    def species_kg_by_accounts(self, accounts):
        values: dict[str, float] = {}
        for account in accounts:
            for species, kg in self.ledger.kg_by_account(account).items():
                values[species] = values.get(species, 0.0) + float(kg)
        return values

    def species_kg_by_account_pattern(self, account_pattern: str):
        if not account_pattern.endswith("*"):
            return self.species_kg_by_accounts((account_pattern,))
        prefix = account_pattern[:-1]
        values: dict[str, float] = {}
        for account, species_kg in self.ledger.kg_by_account().items():
            if not account.startswith(prefix):
                continue
            for species, kg in species_kg.items():
                values[species] = values.get(species, 0.0) + float(kg)
        return values

    def unspent_additive_reagents_kg(self) -> dict[str, float]:
        return dict(self._reagents)

    def feedstock_recovered_reagents_kg(self) -> dict[str, float]:
        return dict(self._feedstock_recovered_reagents)

    def c3_alkali_credit_outstanding_kg_by_species(self) -> dict[str, float]:
        return dict(self._c3_credit_outstanding)

    def non_feedstock_reagent_element_kg_by_account(
        self,
    ) -> dict[str, dict[str, float]]:
        return {
            account: dict(element_kg)
            for account, element_kg in (
                self._non_feedstock_reagent_element_by_account
            ).items()
        }


class _NoReagentSurfaceQueries:
    def __init__(
        self,
        accounts: dict[str, dict[str, float]],
        additives_kg: dict[str, float] | None = None,
    ) -> None:
        self.ledger = _FakeLedger(accounts)
        self.sim = SimpleNamespace(
            record=SimpleNamespace(additives_kg=additives_kg or {})
        )


def _mol(species: str, kg: float) -> float:
    return kg * 1000.0 / MOLAR_MASS[species]


def test_completion_contract_coverage_covers_or_defers_every_gated_step() -> None:
    validate_completion_contract_coverage(_load_setpoints())


def test_coverage_blocks_gated_target_without_contract() -> None:
    setpoints = deepcopy(_load_setpoints())
    setpoints["completion_contracts"]["gated_steps"]["C4"]["contracts"] = []

    with pytest.raises(ValueError, match="C4: no contract for Mg"):
        validate_completion_contract_coverage(setpoints)


def test_aggregate_completeness_uses_worst_target_min() -> None:
    aggregate = aggregate_extraction_completeness(
        {
            "Na": TargetExtractionCompleteness("Na", 0.95, 95.0, 5.0, 100.0),
            "SiO": TargetExtractionCompleteness("SiO", 0.42, 42.0, 58.0, 100.0),
            "Fe": TargetExtractionCompleteness("Fe", 0.8, 80.0, 20.0, 100.0),
        },
        ("Na", "SiO", "Fe"),
    )

    assert aggregate.completeness_fraction == pytest.approx(0.42)
    assert aggregate.worst_target_species == "SiO"
    assert aggregate.aggregation == "min_all_targets"


def test_aggregate_completeness_requires_explicit_target_species() -> None:
    by_target = {
        "Na": TargetExtractionCompleteness("Na", 0.95, 95.0, 5.0, 100.0),
    }

    with pytest.raises(TypeError):
        aggregate_extraction_completeness(by_target)  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="target_species is required"):
        aggregate_extraction_completeness(by_target, None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="target_species must be non-empty"):
        aggregate_extraction_completeness(by_target, ())


def test_aggregate_completeness_is_na_if_any_gated_target_is_na() -> None:
    aggregate = aggregate_extraction_completeness(
        {
            "Na": TargetExtractionCompleteness("Na", 0.95, 95.0, 5.0, 100.0),
            "SiO": TargetExtractionCompleteness(
                "SiO",
                None,
                0.0,
                0.0,
                0.0,
                "no target-equivalent mol evidence",
            ),
        },
        ("Na", "SiO"),
    )

    assert aggregate.completeness_fraction is None
    assert aggregate.worst_target_species == "SiO"
    assert aggregate.reason == "SiO: no target-equivalent mol evidence"

    missing = aggregate_extraction_completeness(
        {"Na": TargetExtractionCompleteness("Na", 0.95, 95.0, 5.0, 100.0)},
        ("Na", "Fe"),
    )
    assert missing.completeness_fraction is None
    assert missing.worst_target_species == "Fe"
    assert missing.reason == "Fe: unknown: no result"


def test_non_vapor_targets_are_deferred_not_half_implemented() -> None:
    contracts = _contracts_by_id()
    deferred_ids = {
        contract_id
        for contract_id, contract in contracts.items()
        if contract.deferred
    }

    assert "C0.CHNOPS.semantic.deferred" in deferred_ids
    assert "C0b.PO.semantic.deferred" in deferred_ids
    assert "C0b.POx.semantic.deferred" in deferred_ids
    assert "C2A_staged.sio_window.minor_Fe.semantic.deferred" in deferred_ids
    assert (
        "C2A_staged.cool_for_na_shuttle.residual_FeO.semantic.deferred"
        in deferred_ids
    )
    assert "C3.residual_FeO.semantic.deferred" in deferred_ids
    assert "C5.Vmax_reducible_set.mre.deferred" in deferred_ids
    assert "mre_baseline.Vmax_reducible_set.mre.deferred" in deferred_ids
    assert "C6.Al.thermite.deferred" in deferred_ids

    for contract in contracts.values():
        if contract.mechanism != "vaporization":
            assert contract.deferred


def test_vapor_contract_uses_narrow_product_accounts_and_includes_wall() -> None:
    contract = _contracts_by_id()["C2A_continuous.Fe.vapor"]
    accounts = {
        "process.condensation_train": {"Fe": 0.168},
        "process.cleaned_melt": {"FeO": 0.144},
        "process.wall_deposit_segment_stage_0_to_stage_1": {"Fe": 0.028},
        "process.metal_phase": {"Fe": 999.0},
    }
    reagents = {"unspent_Fe_reagent": 0.112, "unspent_Na_reagent": 0.112}

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(accounts, reagents),
    )

    product_mol = _mol("Fe", 0.168)
    residual_mol = _mol("FeO", 0.144)
    wall_mol = _mol("Fe", 0.028)
    expected = product_mol / (product_mol + residual_mol + wall_mol)
    assert result.product_target_equiv_mol == pytest.approx(product_mol)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.gross_product_target_equiv_mol == pytest.approx(
        product_mol
    )
    assert result.wall_deposit_target_equiv_mol == pytest.approx(wall_mol)
    assert result.denominator_target_equiv_mol == pytest.approx(
        product_mol + residual_mol + wall_mol
    )
    assert result.completeness_fraction == pytest.approx(expected)


def test_vapor_contract_clean_provenance_needs_no_reagent_surface() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]
    assert contract.provenance_rule == "narrow_account_feedstock_clean"

    result = vapor_contract_completeness(
        contract,
        _NoReagentSurfaceQueries({
            "process.condensation_train": {"Na": 0.01},
            "process.cleaned_melt": {"Na2O": 0.02},
        }),
    )

    assert result.product_target_equiv_mol == pytest.approx(_mol("Na", 0.01))
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)


def test_vapor_contract_counts_recovered_feedstock_reagent_not_additive() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 5.0},
                "reservoir.reagent.Na": {"Na": 7.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
            {"unspent_Na_reagent": 10.0},
            feedstock_recovered_reagents={"Na": 2.0},
        ),
    )

    recovered_mol = _mol("Na", 2.0)
    assert result.product_target_equiv_mol == pytest.approx(recovered_mol)
    assert result.reagent_target_equiv_mol == pytest.approx(recovered_mol)
    assert result.feedstock_recovered_reagent_target_equiv_mol == pytest.approx(
        recovered_mol
    )
    assert result.external_additive_reagent_target_equiv_mol == pytest.approx(
        _mol("Na", 3.0)
    )
    assert result.denominator_target_equiv_mol == pytest.approx(recovered_mol)
    assert result.completeness_fraction == pytest.approx(1.0)


def test_vapor_contract_does_not_infer_recovered_reagent_from_inventory() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 2.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
        ),
    )

    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.feedstock_recovered_reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_vapor_contract_excludes_c3_credit_line_reagent() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 5.0},
                "reservoir.reagent.Na": {"Na": -5.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
            c3_credit_outstanding={"Na": 5.0},
        ),
    )

    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.feedstock_recovered_reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.credit_line_reagent_target_equiv_mol == pytest.approx(
        _mol("Na", 5.0)
    )
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_credit_line_diagnostic_subtracts_recovered_feedstock_balance() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 8.0},
                "reservoir.reagent.Na": {"Na": -20.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
            feedstock_recovered_reagents={"Na": 6.0},
            c3_credit_outstanding={"Na": 20.0},
        ),
    )

    assert result.credit_line_reagent_target_equiv_mol == pytest.approx(
        _mol("Na", 2.0)
    )


def test_vapor_contract_preserves_harvested_then_recycled_denominator() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    condensed = vapor_contract_completeness(
        contract,
        _FakeQueries({
            "process.condensation_train": {"Na": 2.0},
            "process.cleaned_melt": {},
        }),
    )
    recycled = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 2.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
            feedstock_recovered_reagents={"Na": 2.0},
        ),
    )

    assert recycled.product_target_equiv_mol == pytest.approx(
        condensed.product_target_equiv_mol
    )
    assert recycled.denominator_target_equiv_mol == pytest.approx(
        condensed.denominator_target_equiv_mol
    )
    assert recycled.completeness_fraction == pytest.approx(
        condensed.completeness_fraction
    )


def test_vapor_contract_excludes_external_additive_reagent() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.reagent_inventory": {"Na": 5.0},
                "reservoir.reagent.Na": {"Na": 0.0},
                "process.condensation_train": {},
                "process.cleaned_melt": {},
            },
            {"unspent_Na_reagent": 5.0},
        ),
    )

    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.external_additive_reagent_target_equiv_mol == pytest.approx(
        _mol("Na", 5.0)
    )
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_vapor_contract_excludes_non_feedstock_reagent_product_atoms() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries(
            {
                "process.condensation_train": {"Na": 5.0},
                "process.cleaned_melt": {},
            },
            c3_credit_outstanding={"Na": 5.0},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {"Na": 5.0},
            },
        ),
    )

    assert result.gross_product_target_equiv_mol == pytest.approx(_mol("Na", 5.0))
    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_vapor_contract_excludes_reagent_inventory_na2o_residue() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries({
            "process.reagent_inventory": {"Na2O": 1.0},
            "process.condensation_train": {},
            "process.cleaned_melt": {},
        }),
    )

    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_vapor_contract_excludes_spent_reductant_residue_account() -> None:
    contract = _contracts_by_id()["C2A_continuous.Na.vapor"]

    result = vapor_contract_completeness(
        contract,
        _FakeQueries({
            "process.spent_reductant_residue": {"Na2O": 1.0},
            "process.reagent_inventory": {},
            "process.condensation_train": {},
            "process.cleaned_melt": {},
        }),
    )

    assert result.product_target_equiv_mol == pytest.approx(0.0)
    assert result.reagent_target_equiv_mol == pytest.approx(0.0)
    assert result.denominator_target_equiv_mol == pytest.approx(0.0)
    assert result.completeness_fraction is None


def test_extraction_completeness_counts_spent_residue_as_residual_basis() -> None:
    result = extraction_completeness_by_target(
        ("Na",),
        {"Na": ("Na2O", "Na")},
        {"Na": MOLAR_MASS["Na"] / 1000.0},
        {},
        process_inventory_residual_kg={"Na2O": MOLAR_MASS["Na2O"] / 1000.0},
        require_residual_species=True,
    )["Na"]

    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.residual_target_equiv_mol == pytest.approx(2.0)
    assert result.denominator_target_equiv_mol == pytest.approx(3.0)
    assert result.completeness_fraction == pytest.approx(1.0 / 3.0)


def test_e1b_target_yield_excludes_additive_overcredit() -> None:
    initial = {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0}
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0
    additive_product_kg = 2.5 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        initial,
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {"Na": additive_product_kg},
            },
        ),
    )["Na"]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(1.0)
    assert result.gross_product_target_equiv_mol == pytest.approx(3.0)
    assert result.excluded_non_feedstock_reagent_target_equiv_mol == pytest.approx(
        2.5
    )
    assert result.product_target_equiv_mol == pytest.approx(0.5)
    assert result.yield_fraction == pytest.approx(0.5)


@pytest.mark.parametrize(("target", "native_oxide"), (("Na", "Na2O"), ("K", "K2O")))
def test_e1b_target_yield_denominator_excludes_unspent_alkali_credit_line(
    target: str,
    native_oxide: str,
) -> None:
    native_oxide_kg = 0.5 * MOLAR_MASS[native_oxide] / 1000.0
    credit_line_kg = 2.0 * MOLAR_MASS[target] / 1000.0
    product_kg = MOLAR_MASS[target] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        (target,),
        {
            native_oxide: native_oxide_kg,
            target: credit_line_kg,
        },
        _FakeQueries(
            {
                "process.condensation_train": {target: product_kg},
                "process.reagent_inventory": {target: credit_line_kg},
            },
            c3_credit_outstanding={target: credit_line_kg},
        ),
    )[target]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(1.0)
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.yield_fraction == pytest.approx(1.0)


@pytest.mark.parametrize(("target", "native_oxide"), (("Na", "Na2O"), ("K", "K2O")))
def test_e1b_target_yield_denominator_keeps_native_oxide_when_credit_unspent(
    target: str,
    native_oxide: str,
) -> None:
    native_oxide_kg = 0.5 * MOLAR_MASS[native_oxide] / 1000.0
    credit_line_kg = 2.0 * MOLAR_MASS[target] / 1000.0
    product_kg = MOLAR_MASS[target] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        (target,),
        {native_oxide: native_oxide_kg},
        _FakeQueries(
            {
                "process.condensation_train": {target: product_kg},
                "process.reagent_inventory": {target: credit_line_kg},
            },
            c3_credit_outstanding={target: credit_line_kg},
        ),
    )[target]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(1.0)
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.yield_fraction == pytest.approx(1.0)


def test_e1b_target_yield_denominator_caps_partially_spent_credit_line() -> None:
    native_oxide_kg = 0.5 * MOLAR_MASS["Na2O"] / 1000.0
    credit_line_kg = 2.0 * MOLAR_MASS["Na"] / 1000.0
    live_credit_kg = 0.75 * MOLAR_MASS["Na"] / 1000.0
    spent_credit_product_kg = 1.25 * MOLAR_MASS["Na"] / 1000.0
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        {
            "Na2O": native_oxide_kg,
            "Na": credit_line_kg,
        },
        _FakeQueries(
            {
                "process.condensation_train": {"Na": gross_product_kg},
                "process.reagent_inventory": {"Na": live_credit_kg},
            },
            c3_credit_outstanding={"Na": credit_line_kg},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {"Na": spent_credit_product_kg},
            },
        ),
    )["Na"]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(2.25)
    assert result.excluded_non_feedstock_reagent_target_equiv_mol == pytest.approx(
        1.25
    )
    assert result.product_target_equiv_mol == pytest.approx(1.75)
    assert result.yield_fraction == pytest.approx(1.75 / 2.25)


def test_e1b_target_yield_denominator_keeps_fully_spent_credit_line() -> None:
    native_oxide_kg = 0.5 * MOLAR_MASS["Na2O"] / 1000.0
    credit_line_kg = 2.0 * MOLAR_MASS["Na"] / 1000.0
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        {
            "Na2O": native_oxide_kg,
            "Na": credit_line_kg,
        },
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            c3_credit_outstanding={"Na": credit_line_kg},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {"Na": credit_line_kg},
            },
        ),
    )["Na"]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(3.0)
    assert result.excluded_non_feedstock_reagent_target_equiv_mol == pytest.approx(
        2.0
    )
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.yield_fraction == pytest.approx(1.0 / 3.0)


def test_e1b_target_yield_denominator_excludes_tagged_cleaned_melt_reagent() -> None:
    native_oxide_kg = 0.5 * MOLAR_MASS["Na2O"] / 1000.0
    tagged_reagent_kg = 2.0 * MOLAR_MASS["Na"] / 1000.0
    product_kg = MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        {
            "Na2O": native_oxide_kg,
            "Na": tagged_reagent_kg,
        },
        _FakeQueries(
            {"process.condensation_train": {"Na": product_kg}},
            non_feedstock_reagent_element_by_account={
                "process.cleaned_melt": {"Na": tagged_reagent_kg},
            },
        ),
    )["Na"]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(1.0)
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.yield_fraction == pytest.approx(1.0)


def test_e1b_target_yield_blocks_helper_present_empty_unclean_additive_map() -> None:
    initial = {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0}
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        initial,
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            additives_kg={"Na": 12.0},
            non_feedstock_reagent_element_by_account={},
        ),
    )["Na"]

    assert result.yield_fraction is None
    assert "unclean additive/reagent provenance for Na" in result.reason
    assert "provenance map does not account" in result.reason


def test_e1b_target_yield_blocks_helper_present_undercovered_additive_map() -> None:
    initial = {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0}
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        initial,
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            additives_kg={"Na": 2.0 * MOLAR_MASS["Na"] / 1000.0},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {
                    "Na": MOLAR_MASS["Na"] / 1000.0,
                },
            },
        ),
    )["Na"]

    assert result.yield_fraction is None
    assert "unclean additive/reagent provenance for Na" in result.reason
    assert "provenance map does not account" in result.reason


def test_e1b_target_yield_accepts_helper_present_fully_covered_additive_map() -> None:
    initial = {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0}
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0
    additive_kg = 2.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        initial,
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            additives_kg={"Na": additive_kg},
            non_feedstock_reagent_element_by_account={
                "process.condensation_train": {"Na": additive_kg},
            },
        ),
    )["Na"]

    assert result.yield_fraction == pytest.approx(1.0)
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.excluded_non_feedstock_reagent_target_equiv_mol == pytest.approx(
        2.0
    )
    assert result.reason == ""


def test_e1b_target_yield_allows_clean_run_with_empty_helper_map() -> None:
    initial = {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0}
    gross_product_kg = 3.0 * MOLAR_MASS["Na"] / 1000.0

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        initial,
        _FakeQueries(
            {"process.condensation_train": {"Na": gross_product_kg}},
            non_feedstock_reagent_element_by_account={},
        ),
    )["Na"]

    assert result.yield_fraction == pytest.approx(3.0)
    assert result.excluded_non_feedstock_reagent_target_equiv_mol == pytest.approx(
        0.0
    )
    assert result.reason == ""


def test_e1b_target_yield_ci_k_zero_denominator_is_not_applicable() -> None:
    result = target_species_yield_by_initial_cleaned_melt(
        ("K",),
        {"Na2O": MOLAR_MASS["Na2O"] / 1000.0},
        _FakeQueries({}),
    )["K"]

    assert result.yield_fraction is None
    assert result.reason == "not-applicable: zero initial process.cleaned_melt basis for K"
    assert result.initial_cleaned_target_equiv_mol == pytest.approx(0.0)


def test_e1b_target_yield_uses_formula_mass_for_valid_species_not_in_static_table() -> None:
    na2co3_molar_mass = parse_formula("Na2CO3", species="Na2CO3").molar_mass_g_mol

    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        {"Na2CO3": na2co3_molar_mass / 1000.0},
        _FakeQueries({
            "process.condensation_train": {
                "Na": MOLAR_MASS["Na"] / 1000.0,
            },
        }),
    )["Na"]

    assert result.initial_cleaned_target_equiv_mol == pytest.approx(2.0)
    assert result.product_target_equiv_mol == pytest.approx(1.0)
    assert result.yield_fraction == pytest.approx(0.5)


def test_e1b_target_yield_reports_sio_exact_species_and_si_equiv_mol() -> None:
    result = target_species_yield_by_initial_cleaned_melt(
        ("SiO",),
        {"SiO2": 10.0 * MOLAR_MASS["SiO2"] / 1000.0},
        _FakeQueries({
            "process.condensation_train": {
                "Si": MOLAR_MASS["Si"] / 1000.0,
                "SiO": 2.0 * MOLAR_MASS["SiO"] / 1000.0,
                "SiO2": 3.0 * MOLAR_MASS["SiO2"] / 1000.0,
            },
        }),
    )["SiO"]

    assert result.product_species_kg == {
        "Si": pytest.approx(MOLAR_MASS["Si"] / 1000.0),
        "SiO": pytest.approx(2.0 * MOLAR_MASS["SiO"] / 1000.0),
        "SiO2": pytest.approx(3.0 * MOLAR_MASS["SiO2"] / 1000.0),
    }
    assert result.exact_product_kg == pytest.approx(
        2.0 * MOLAR_MASS["SiO"] / 1000.0
    )
    assert result.product_target_equiv_mol == pytest.approx(6.0)
    assert result.initial_cleaned_target_equiv_mol == pytest.approx(10.0)


def test_e1b_target_yield_fails_closed_on_unclean_additive_provenance() -> None:
    result = target_species_yield_by_initial_cleaned_melt(
        ("Na",),
        {"Na2O": 0.5 * MOLAR_MASS["Na2O"] / 1000.0},
        _NoReagentSurfaceQueries(
            {
                "process.condensation_train": {
                    "Na": 3.0 * MOLAR_MASS["Na"] / 1000.0,
                },
            },
            additives_kg={"Na": 12.0},
        ),
    )["Na"]

    assert result.yield_fraction is None
    assert "unclean additive/reagent provenance for Na" in result.reason


def test_vapor_contract_blocks_unsupported_provenance_rule() -> None:
    contract = replace(
        _contracts_by_id()["C2A_continuous.Na.vapor"],
        provenance_rule="reagent_balance_subtraction",
    )

    with pytest.raises(CompletionContractBlocked, match="unsupported"):
        vapor_contract_completeness(
            contract,
            _NoReagentSurfaceQueries({
                "process.condensation_train": {"Na": 0.01},
                "process.cleaned_melt": {"Na2O": 0.02},
            }),
        )


def test_vapor_contract_without_wall_account_is_not_complete() -> None:
    contract = replace(
        _contracts_by_id()["C4.Mg.vapor"],
        wall_account=None,
    )

    with pytest.raises(ValueError, match="wall_account is required"):
        vapor_contract_completeness(
            contract,
            _FakeQueries({
                "process.condensation_train": {"Mg": 0.01},
                "process.cleaned_melt": {"MgO": 0.02},
            }),
        )
