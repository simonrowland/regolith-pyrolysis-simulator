from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simulator.accounting.completeness import (
    CompletionContractBlocked,
    TargetExtractionCompleteness,
    aggregate_extraction_completeness,
    completion_contracts_from_setpoints,
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
    ) -> None:
        self.ledger = _FakeLedger(accounts)
        self._reagents = reagents or {}

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


class _NoReagentSurfaceQueries:
    def __init__(self, accounts: dict[str, dict[str, float]]) -> None:
        self.ledger = _FakeLedger(accounts)


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
