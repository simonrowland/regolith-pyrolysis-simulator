"""Schema-stable, read-only web surface for the atom ledger."""

from __future__ import annotations

import re
import math
from collections.abc import Mapping
from typing import Any

from simulator.accounting import ledger as ledger_module
from simulator.accounting.queries import AccountingQueries
from simulator.three_product_report import classify_products

LEDGER_SCHEMA_VERSION = "1.0.0"
_OXIDE_FORMULA_RE = re.compile(r"^(?:[A-Z][a-z]?\d*)*O\d*$")

_ACCOUNT_BASIS = {
    "process.cleaned_melt": "oxide",
    "process.c7_al_credit": "oxide",
    "terminal.slag": "oxide",
    "process.metal_phase": "elemental",
    "process.metal_phase_bottom_pool": "elemental",
    "process.metal_phase_float_layer": "elemental",
    "terminal.drain_tap_material": "elemental",
}

_VIEW_NAMES = (
    "melt_pot_upper_tap", "melt_pot_bottom_tap", "terminal_ceramic",
    "condensation_train", "offgas", "wall_deposits", "oxygen_partition",
    "industrial_glass", "stage_purity",
)


def wt_pct_from_kg(species_kg: Mapping[str, Any] | None) -> dict[str, float]:
    """Return mass percentages for a nonnegative oxide/elemental/species basis."""
    if not isinstance(species_kg, Mapping):
        return {}
    values = _positive_finite_values(species_kg)
    total_kg = sum(values.values())
    if total_kg <= 0.0:
        return {}
    return {species: kg / total_kg * 100.0 for species, kg in sorted(values.items())}


def oxide_wt_pct_from_kg(species_kg: Mapping[str, Any] | None) -> dict[str, float]:
    """Compatibility oxide-only projection, now owned below the web layer."""
    if not isinstance(species_kg, Mapping):
        return {}
    oxides = {species: kg for species, kg in _positive_finite_values(species_kg).items() if _is_oxide_species(species)}
    return {species: round(value, 3) for species, value in wt_pct_from_kg(oxides).items()}


class LedgerAPI:
    """Pure query adapter. Every public method returns detached containers.

    ``wt_pct`` is intentionally unavailable for signed-balance accounts: their
    values are net flows, so normalizing by a signed total would misrepresent
    them as a physical composition. Those responses use ``species: null`` and
    ``wt_pct_basis: "omitted_for_signed_account"``.
    """

    def __init__(self, sim: Any) -> None:
        self.sim = sim
        self.ledger = sim.atom_ledger
        self.queries = AccountingQueries(sim)

    def accounts(self) -> dict[str, Any]:
        return {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "accounts": [self._account_metadata(account) for account in self._discovered_accounts()],
            "account_prefixes": list(ledger_module.KNOWN_LEDGER_ACCOUNT_PREFIXES),
        }

    def account(self, account_id: str, *, units: str = "kg") -> dict[str, Any]:
        """Return one account using the requested unit and basis metadata.

        Signed-balance accounts omit the ``wt_pct`` species projection because
        positive and negative net flows do not define a composition basis.
        """
        account = str(account_id)
        if account not in self._discovered_accounts():
            raise KeyError(account)
        if units == "kg":
            values = self.ledger.kg_by_account(account)
        elif units == "mol":
            values = self.ledger.mol_by_account(account)
        elif units == "wt_pct":
            values = None if self._account_can_be_signed(account) else wt_pct_from_kg(
                self.ledger.kg_by_account(account)
            )
        else:
            raise ValueError("units must be one of: kg, mol, wt_pct")
        return {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "account": account,
            "units": units,
            "basis": _basis_for_account(account),
            "provenance": True,
            "species": None if values is None else dict(sorted(values.items())),
            "wt_pct_basis": (
                "omitted_for_signed_account"
                if units == "wt_pct" and values is None
                else "nonnegative_mass"
                if units == "wt_pct"
                else None
            ),
        }

    def account_pattern(self, pattern: str, *, units: str = "kg") -> dict[str, Any]:
        value = str(pattern)
        if not value.endswith("*"):
            return self.account(value, units=units)
        matches = [name for name in self._discovered_accounts() if name.startswith(value[:-1])]
        return {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "pattern": value,
            "units": units,
            "provenance": True,
            "accounts": {name: self.account(name, units=units)["species"] for name in matches},
        }

    def snapshot(self) -> dict[str, Any]:
        report = self.ledger.close_report()
        return {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "provenance": {
                "mass_balance_attested": bool(report.get("balanced")),
                "balance_tolerance_kg": float(self.ledger.balance_tolerance_kg),
                "writer": "commit_batch",
            },
            **report,
        }

    def view(self, name: str) -> dict[str, Any]:
        view_name = str(name)
        if view_name == "melt_pot_upper_tap":
            payload = self.account("process.metal_phase_float_layer")
        elif view_name == "melt_pot_bottom_tap":
            payload = self.account("process.metal_phase_bottom_pool")
        elif view_name == "terminal_ceramic":
            payload = {"species_kg": self.queries.terminal_rump_by_species(), "class_kg": self.queries.terminal_rump_by_class(), "classifier": "terminal_rump"}
        elif view_name == "condensation_train":
            payload = {"species_kg": self.queries.condensation_totals_with_terminal_oxygen()}
        elif view_name == "offgas":
            payload = {"terminal": self.account("terminal.offgas")["species"], "near_melt": self.account("process.overhead_gas")["species"]}
        elif view_name == "wall_deposits":
            payload = {"aggregate_kg": self.account("process.wall_deposit")["species"], "segments_kg": self.account_pattern("process.wall_deposit_segment_*")["accounts"]}
        elif view_name == "oxygen_partition":
            payload = self.queries.oxygen_terminal_partition_kg()
        elif view_name == "industrial_glass":
            payload = classify_products(self.sim, early_tap_mode=True)["industrial_mixed_glass"]
        elif view_name == "stage_purity":
            from simulator.condensation import stage_purity_report
            payload = stage_purity_report(self.sim.train)
        else:
            raise KeyError(view_name)
        return {"ledger_schema_version": LEDGER_SCHEMA_VERSION, "view": view_name, "provenance": True, "data": payload}

    @staticmethod
    def view_names() -> tuple[str, ...]:
        return _VIEW_NAMES

    def _discovered_accounts(self) -> list[str]:
        accounts = {str(account) for account in ledger_module.KNOWN_LEDGER_ACCOUNTS}
        accounts.update(
            str(account) for account in self.ledger.kg_by_account()
            if any(str(account).startswith(prefix) for prefix in ledger_module.KNOWN_LEDGER_ACCOUNT_PREFIXES)
        )
        return sorted(accounts)

    def _account_metadata(self, account: str) -> dict[str, Any]:
        policy = self.ledger.account_policy(account)
        return {
            "id": account,
            "basis": _basis_for_account(account),
            "policy": {"terminal": bool(policy.terminal), "append_only": bool(policy.terminal), "allow_negative": bool(policy.allow_negative), "scope": policy.scope},
            "provenance": True,
        }

    def _account_can_be_signed(self, account: str) -> bool:
        return bool(self.ledger.account_policy(account).allow_negative)


def _basis_for_account(account: str) -> str:
    return _ACCOUNT_BASIS.get(str(account), "species")


def _is_oxide_species(species: str) -> bool:
    if species in {"O2", "H2O", "CO2"}:
        return False
    return species == "REE_oxides" or bool(_OXIDE_FORMULA_RE.fullmatch(species))


def _positive_finite_values(values: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for species, raw_value in values.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            result[str(species)] = value
    return result
