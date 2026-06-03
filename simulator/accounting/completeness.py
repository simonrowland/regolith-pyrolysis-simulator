"""Shared extraction-completeness math."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.formulas import parse_formula
from simulator.state import MOLAR_MASS

_EPS = 1.0e-12
_WALL_EPS = 1.0e-12
_REQUIRED_CONTRACT_FIELDS = (
    "contract_id",
    "mechanism",
    "denominator_source",
    "provenance_rule",
    "mid_run_vs_terminal",
    "aggregation",
)
_VAPOR_PRODUCT_ACCOUNTS = (
    "process.condensation_train",
    "process.overhead_gas",
    "terminal.chromium_condensed_oxide_stored",
)
_VAPOR_RESIDUAL_ACCOUNTS = (
    "process.cleaned_melt",
    "terminal.slag",
)
_VAPOR_WALL_ACCOUNT = "process.wall_deposit_segment_*"
_VAPOR_PROVENANCE_RULE = "narrow_account_feedstock_clean"

DEFAULT_RESIDUAL_SPECIES_BY_TARGET: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "SiO": ("SiO2", "SiO"),
    "Fe": ("FeO", "Fe"),
    "CrO2": ("Cr2O3", "CrO2", "Cr"),
    "Mg": ("MgO", "Mg"),
    "Na": ("Na2O", "Na"),
    "K": ("K2O", "K"),
})


@dataclass(frozen=True)
class TargetExtractionCompleteness:
    target_species: str
    completeness_fraction: float | None
    product_target_equiv_mol: float
    residual_target_equiv_mol: float
    denominator_target_equiv_mol: float
    reason: str = ""
    wall_deposit_target_equiv_mol: float = 0.0
    reagent_target_equiv_mol: float = 0.0
    gross_product_target_equiv_mol: float = 0.0
    contract_id: str = ""

    @property
    def detail(self) -> str:
        if self.completeness_fraction is None:
            return f"{self.target_species}: {self.reason}"
        return (
            f"{self.target_species}: "
            f"product_target_equiv_mol={self.product_target_equiv_mol:.6g}, "
            f"residual_target_equiv_mol={self.residual_target_equiv_mol:.6g}, "
            f"denominator_target_equiv_mol={self.denominator_target_equiv_mol:.6g}"
        )


class CompletionContractBlocked(AccountingError):
    """Raised when a completion contract cannot compute clean provenance."""


@dataclass(frozen=True)
class CompletionContract:
    contract_id: str
    campaign: str
    mechanism: str
    target_species: str | None
    target_element: str | None
    semantic_target_kind: str | None
    product_accounts: tuple[str, ...]
    residual_accounts: tuple[str, ...]
    residual_species: tuple[str, ...]
    wall_account: str | None
    element_map: Mapping[str, tuple[str, ...]]
    denominator_source: str
    provenance_rule: str
    mid_run_vs_terminal: str
    aggregation: str
    deferred: bool = False
    deferred_reason: str = ""
    stage: str | None = None

    @property
    def target_key(self) -> str:
        if self.target_species:
            return self.target_species
        if self.semantic_target_kind:
            return self.semantic_target_kind
        return self.contract_id

    @property
    def element(self) -> str:
        if self.target_element:
            return self.target_element
        if self.target_species:
            return _target_element(self.target_species)
        raise CompletionContractBlocked(
            f"{self.contract_id}: no target_element for semantic target"
        )

    @property
    def allowed_species(self) -> tuple[str, ...]:
        values = self.element_map.get(self.element, ())
        return tuple(str(species) for species in values)


def extraction_completeness_by_target(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
    *,
    require_residual_species: bool = False,
) -> dict[str, TargetExtractionCompleteness]:
    residual_map = {
        str(target): tuple(str(species) for species in residuals)
        for target, residuals in residual_species_by_target.items()
    }
    products = {str(species): kg for species, kg in product_ledger_kg.items()}
    rump = {str(species): kg for species, kg in terminal_rump_kg.items()}
    results: dict[str, TargetExtractionCompleteness] = {}
    for raw_target in target_species:
        target = str(raw_target)
        if require_residual_species and target not in residual_map:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                "unknown: no residual species map for target",
            )
            continue
        try:
            product_mol = _target_equivalent_mol(
                target, target, products.get(target, 0.0))
            residual_mol = 0.0
            for residual in residual_map.get(target, (target,)):
                residual_mol += _target_equivalent_mol(
                    target,
                    residual,
                    rump.get(residual, 0.0),
                )
            denom = product_mol + residual_mol
            if denom <= _EPS:
                results[target] = TargetExtractionCompleteness(
                    target,
                    None,
                    product_mol,
                    residual_mol,
                    denom,
                    "no target-equivalent mol evidence",
                )
                continue
            results[target] = TargetExtractionCompleteness(
                target,
                product_mol / denom,
                product_mol,
                residual_mol,
                denom,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            results[target] = TargetExtractionCompleteness(
                target,
                None,
                0.0,
                0.0,
                0.0,
                f"unknown: {exc}",
            )
    return results


def extraction_completeness_pct(
    target_species: tuple[str, ...],
    residual_species_by_target: Mapping[str, tuple[str, ...]],
    product_ledger_kg: Mapping[str, Any],
    terminal_rump_kg: Mapping[str, Any],
) -> float:
    """Return the worst target completeness fraction across target species."""

    results = extraction_completeness_by_target(
        target_species,
        residual_species_by_target,
        product_ledger_kg,
        terminal_rump_kg,
    )
    if not results:
        raise ValueError("target_species must be non-empty")
    fractions: list[float] = []
    for result in results.values():
        if result.completeness_fraction is None:
            raise ValueError(result.reason)
        fractions.append(result.completeness_fraction)
    return min(fractions)


def completion_contracts_from_setpoints(
    setpoints: Mapping[str, Any],
) -> tuple[CompletionContract, ...]:
    raw_root = setpoints.get("completion_contracts", {})
    if not isinstance(raw_root, Mapping):
        raise ValueError("completion_contracts must be a mapping")
    raw_steps = raw_root.get("gated_steps", {})
    if not isinstance(raw_steps, Mapping):
        raise ValueError("completion_contracts.gated_steps must be a mapping")

    contracts: list[CompletionContract] = []
    for campaign, raw_step in raw_steps.items():
        if not isinstance(raw_step, Mapping):
            raise ValueError(f"{campaign}: completion contract step must map")
        contracts.extend(_contracts_from_list(
            str(campaign),
            None,
            raw_step.get("contracts", []),
        ))
        stage_contracts = raw_step.get("stage_contracts", {})
        if stage_contracts is None:
            stage_contracts = {}
        if not isinstance(stage_contracts, Mapping):
            raise ValueError(
                f"{campaign}.stage_contracts must be a mapping"
            )
        for stage, raw_stage in stage_contracts.items():
            if not isinstance(raw_stage, Mapping):
                raise ValueError(
                    f"{campaign}.{stage}: stage contract must map"
                )
            contracts.extend(_contracts_from_list(
                str(campaign),
                str(stage),
                raw_stage.get("contracts", []),
            ))
    _validate_unique_contract_ids(contracts)
    return tuple(contracts)


def completion_contracts_for_campaign(
    setpoints: Mapping[str, Any],
    campaign: str,
) -> tuple[CompletionContract, ...]:
    return tuple(
        contract
        for contract in completion_contracts_from_setpoints(setpoints)
        if contract.campaign == campaign
    )


def validate_completion_contract_coverage(
    setpoints: Mapping[str, Any],
) -> None:
    campaigns = setpoints.get("campaigns", {})
    if not isinstance(campaigns, Mapping):
        raise ValueError("campaigns must be a mapping")
    contracts = completion_contracts_from_setpoints(setpoints)
    by_step: dict[tuple[str, str | None], list[CompletionContract]] = {}
    for contract in contracts:
        by_step.setdefault((contract.campaign, contract.stage), []).append(
            contract
        )
        _validate_contract(contract)

    errors: list[str] = []
    for campaign, cfg in campaigns.items():
        if not isinstance(cfg, Mapping):
            continue
        campaign_name = str(campaign)
        stages = cfg.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if not isinstance(stage, Mapping):
                    continue
                stage_name = str(stage.get("name", ""))
                if not stage_name:
                    errors.append(f"{campaign_name}: staged step has no name")
                    continue
                _check_step_targets(
                    errors,
                    campaign_name,
                    stage_name,
                    _as_targets(stage.get("target_species")),
                    by_step,
                )
        else:
            targets = _as_targets(cfg.get("target_species"))
            if targets:
                _check_step_targets(
                    errors,
                    campaign_name,
                    None,
                    targets,
                    by_step,
                )
            elif _has_endpoint(cfg):
                step_contracts = by_step.get((campaign_name, None), ())
                if not step_contracts:
                    errors.append(
                        f"{campaign_name}: endpoint campaign has no contract"
                    )

    if errors:
        raise ValueError("; ".join(errors))


def vapor_contract_completeness(
    contract: CompletionContract,
    queries: Any,
) -> TargetExtractionCompleteness:
    if contract.deferred:
        return TargetExtractionCompleteness(
            contract.target_key,
            None,
            0.0,
            0.0,
            0.0,
            f"deferred: {contract.deferred_reason or contract.mechanism}",
            contract_id=contract.contract_id,
        )
    if contract.mechanism != "vaporization":
        raise CompletionContractBlocked(
            f"{contract.contract_id}: non-vapor contract is not computable"
        )
    if contract.provenance_rule != _VAPOR_PROVENANCE_RULE:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: unsupported provenance_rule "
            f"{contract.provenance_rule!r}"
        )
    _validate_contract(contract)

    target = contract.target_species or contract.target_key
    product_kg = _species_kg_by_accounts(queries, contract.product_accounts)
    residual_kg = _species_kg_by_accounts(queries, contract.residual_accounts)
    wall_kg: Mapping[str, Any] = {}
    if contract.wall_account:
        wall_kg = _species_kg_by_account_pattern(queries, contract.wall_account)

    # OWNER 2026-06-03 fix2: dosed additives are staged to their own
    # campaigns (Na/K -> C3, Mg -> C4, C -> Stage-0), not the earlier
    # vapor-extraction stages, so these narrow product accounts are
    # feedstock-derived and need no reagent subtraction.
    product_mol = _contract_target_equivalent_mol(
        contract, product_kg, "product"
    )
    residual_mol = _contract_residual_target_equivalent_mol(
        contract, residual_kg
    )
    wall_mol = _contract_target_equivalent_mol(contract, wall_kg, "wall")
    if wall_mol > _WALL_EPS and not contract.wall_account:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: wall deposit present without wall term"
        )
    denom = product_mol + residual_mol + wall_mol
    if denom <= _EPS:
        return TargetExtractionCompleteness(
            target,
            None,
            product_mol,
            residual_mol,
            denom,
            "no target-equivalent mol evidence",
            wall_deposit_target_equiv_mol=wall_mol,
            gross_product_target_equiv_mol=product_mol,
            contract_id=contract.contract_id,
        )
    return TargetExtractionCompleteness(
        target,
        product_mol / denom,
        product_mol,
        residual_mol,
        denom,
        wall_deposit_target_equiv_mol=wall_mol,
        gross_product_target_equiv_mol=product_mol,
        contract_id=contract.contract_id,
    )


def _target_equivalent_mol(target: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    target_element = _target_element(target)
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(target_element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {target_element} for target {target}")
    return species_mol * element_count


def _target_element(target: str) -> str:
    formula = parse_formula(target, species=target)
    if len(formula.elements) == 1:
        return next(iter(formula.elements))
    non_oxygen = [element for element in formula.elements if element != "O"]
    if len(non_oxygen) == 1:
        return non_oxygen[0]
    raise ValueError(f"target {target} does not identify one target element")


def _species_mol(species: str, kg: Any) -> float:
    amount = _non_negative_number(kg, f"{species} kg")
    if amount <= _EPS:
        return 0.0
    molar_mass = MOLAR_MASS.get(species)
    if molar_mass is None:
        raise KeyError(f"missing molar mass for {species}")
    return amount * 1000.0 / float(molar_mass)


def _non_negative_number(value: Any, name: str) -> float:
    amount = _finite_number(value, name)
    if amount < -_EPS:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, amount)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    return amount


def _contracts_from_list(
    campaign: str,
    stage: str | None,
    raw_contracts: Any,
) -> list[CompletionContract]:
    if raw_contracts is None:
        raw_contracts = []
    if not isinstance(raw_contracts, list):
        raise ValueError(f"{campaign}: contracts must be a list")
    return [
        _contract_from_mapping(campaign, stage, raw)
        for raw in raw_contracts
    ]


def _contract_from_mapping(
    campaign: str,
    stage: str | None,
    raw: Any,
) -> CompletionContract:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{campaign}: contract must be a mapping")
    for field in _REQUIRED_CONTRACT_FIELDS:
        if field not in raw:
            raise ValueError(
                f"{campaign}: completion contract missing {field}"
            )
    element_map = raw.get("element_map", {}) or {}
    if not isinstance(element_map, Mapping):
        raise ValueError(f"{raw.get('contract_id')}: element_map must map")
    normalized_map = {
        str(element): tuple(str(species) for species in _as_targets(species))
        for element, species in element_map.items()
    }
    residual_species = _as_targets(raw.get("residual_species"))
    return CompletionContract(
        contract_id=str(raw["contract_id"]),
        campaign=campaign,
        mechanism=str(raw["mechanism"]),
        target_species=_optional_str(raw.get("target_species")),
        target_element=_optional_str(raw.get("target_element")),
        semantic_target_kind=_optional_str(raw.get("semantic_target_kind")),
        product_accounts=_as_targets(raw.get("product_accounts")),
        residual_accounts=_as_targets(raw.get("residual_accounts")),
        residual_species=residual_species,
        wall_account=_optional_str(raw.get("wall_account")),
        element_map=MappingProxyType(normalized_map),
        denominator_source=str(raw["denominator_source"]),
        provenance_rule=str(raw["provenance_rule"]),
        mid_run_vs_terminal=str(raw["mid_run_vs_terminal"]),
        aggregation=str(raw["aggregation"]),
        deferred=bool(raw.get("deferred", False)),
        deferred_reason=str(raw.get("deferred_reason", "")),
        stage=stage,
    )


def _validate_unique_contract_ids(
    contracts: tuple[CompletionContract, ...] | list[CompletionContract],
) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for contract in contracts:
        if contract.contract_id in seen:
            duplicates.append(contract.contract_id)
        seen.add(contract.contract_id)
    if duplicates:
        raise ValueError(
            "duplicate completion contract ids: " + ", ".join(duplicates)
        )


def _validate_contract(contract: CompletionContract) -> None:
    if contract.deferred:
        if not contract.deferred_reason:
            raise ValueError(
                f"{contract.contract_id}: deferred_reason is required"
            )
        return
    if contract.mechanism != "vaporization":
        raise ValueError(
            f"{contract.contract_id}: non-vapor contract must be deferred"
        )
    if not contract.target_element:
        raise ValueError(
            f"{contract.contract_id}: target_element is required"
        )
    if not contract.product_accounts:
        raise ValueError(
            f"{contract.contract_id}: product_accounts is required"
        )
    if not contract.residual_accounts:
        raise ValueError(
            f"{contract.contract_id}: residual_accounts is required"
        )
    if not contract.residual_species:
        raise ValueError(
            f"{contract.contract_id}: residual_species is required"
        )
    if not contract.wall_account:
        raise ValueError(
            f"{contract.contract_id}: wall_account is required"
        )
    if not contract.allowed_species:
        raise ValueError(
            f"{contract.contract_id}: element_map must include target_element"
        )
    if contract.denominator_source != "product_plus_residual_plus_wall_deposit":
        raise ValueError(
            f"{contract.contract_id}: denominator_source must include wall"
        )
    if contract.provenance_rule != _VAPOR_PROVENANCE_RULE:
        raise ValueError(
            f"{contract.contract_id}: vapor provenance must use "
            f"{_VAPOR_PROVENANCE_RULE}"
        )


def _check_step_targets(
    errors: list[str],
    campaign: str,
    stage: str | None,
    targets: tuple[str, ...],
    by_step: Mapping[tuple[str, str | None], list[CompletionContract]],
) -> None:
    step_contracts = by_step.get((campaign, stage), ())
    for target in targets:
        matches = [
            contract for contract in step_contracts
            if contract.target_species == target
            or contract.semantic_target_kind == target
        ]
        step_name = f"{campaign}.{stage}" if stage else campaign
        if not matches:
            errors.append(f"{step_name}: no contract for {target}")
            continue
        for contract in matches:
            if contract.deferred:
                continue
            try:
                _validate_contract(contract)
            except ValueError as exc:
                errors.append(str(exc))


def _has_endpoint(cfg: Mapping[str, Any]) -> bool:
    return any(
        key in cfg and cfg.get(key) is not None
        for key in ("endpoint", "soft_endpoint", "composition_endpoint")
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _as_targets(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        targets = []
        for item in value:
            if item is None:
                continue
            target = str(item)
            if target:
                targets.append(target)
        return tuple(targets)
    return ()


def _species_kg_by_accounts(
    queries: Any,
    accounts: tuple[str, ...],
) -> Mapping[str, Any]:
    helper = getattr(queries, "species_kg_by_accounts", None)
    if callable(helper):
        return helper(accounts)
    ledger = getattr(queries, "ledger", None)
    if ledger is None:
        raise CompletionContractBlocked("ledger surface is unavailable")
    values: dict[str, float] = {}
    for account in accounts:
        for species, kg in ledger.kg_by_account(str(account)).items():
            amount = float(kg)
            if amount:
                values[str(species)] = values.get(str(species), 0.0) + amount
    return values


def _species_kg_by_account_pattern(
    queries: Any,
    account_pattern: str,
) -> Mapping[str, Any]:
    helper = getattr(queries, "species_kg_by_account_pattern", None)
    if callable(helper):
        return helper(account_pattern)
    ledger = getattr(queries, "ledger", None)
    if ledger is None:
        raise CompletionContractBlocked("ledger surface is unavailable")
    pattern = str(account_pattern)
    if not pattern.endswith("*"):
        return _species_kg_by_accounts(queries, (pattern,))
    prefix = pattern[:-1]
    values: dict[str, float] = {}
    for account, species_kg in ledger.kg_by_account().items():
        if not str(account).startswith(prefix):
            continue
        for species, kg in species_kg.items():
            amount = float(kg)
            if amount:
                values[str(species)] = values.get(str(species), 0.0) + amount
    return values


def _contract_residual_target_equivalent_mol(
    contract: CompletionContract,
    species_kg: Mapping[str, Any],
) -> float:
    filtered = {
        species: species_kg.get(species, 0.0)
        for species in contract.residual_species
    }
    return _contract_target_equivalent_mol(contract, filtered, "residual")


def _contract_target_equivalent_mol(
    contract: CompletionContract,
    species_kg: Mapping[str, Any],
    source: str,
) -> float:
    total = 0.0
    allowed = set(contract.allowed_species)
    if not allowed:
        raise CompletionContractBlocked(
            f"{contract.contract_id}: no element_map species for {source}"
        )
    for species, kg in species_kg.items():
        species_name = str(species)
        if source == "reagent":
            reagent_element = _unspent_reagent_element(species_name)
            if reagent_element is not None:
                if reagent_element != contract.element:
                    continue
                total += _species_mol(reagent_element, kg)
                continue
        if species_name not in allowed:
            continue
        total += _element_equivalent_mol(
            contract.element,
            species_name,
            kg,
        )
    return total


def _unspent_reagent_element(species_name: str) -> str | None:
    prefix = "unspent_"
    suffix = "_reagent"
    if not species_name.startswith(prefix) or not species_name.endswith(suffix):
        return None
    element = species_name[len(prefix) : -len(suffix)]
    return element or None


def _element_equivalent_mol(element: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {element}")
    return species_mol * element_count
