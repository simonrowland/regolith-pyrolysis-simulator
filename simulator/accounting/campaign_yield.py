"""Exact-or-unavailable read-side campaign yield inventories."""

from dataclasses import asdict, dataclass, field
import math
import sys

from simulator.accounting.formulas import resolve_species_formula


CONDENSER_ACCOUNT = "process.condensation_train"
# Feed atoms in these accounts have left the melt. Reagent inventory includes
# condensate actually recovered from the train; retained cathode metal counts
# as extracted but does not become captured condenser product. Raw feed,
# Stage-0 sequestration, uncharged reservoirs, and slag are not melt extraction.
EXTRACTED_ACCOUNTS = frozenset({
    CONDENSER_ACCOUNT, "process.condensation_retained_holdup",
    "process.overhead_gas", "process.metal_phase",
    "process.metal_phase_bottom_pool", "process.metal_phase_float_layer",
    "process.reagent_inventory", "process.spent_reductant_residue",
    "terminal.drain_tap_material", "terminal.chromium_condensed_oxide_stored",
    "terminal.offgas", "vent",
})


@dataclass(frozen=True)
class CapturedFeedAtoms:
    status: str
    captured_mol: float | None = None
    wrong_stage_mol: float | None = None
    reason: str | None = None
    diagnostic: dict = field(default_factory=dict)


class CampaignYieldProjection:
    def __init__(self, sim):
        self.sim = sim

    @property
    def ledger(self):
        return self.sim.atom_ledger

    def melt_feed_atoms(self):
        return {element: origins.get("feedstock", 0.0) for element, origins in
                self.ledger.origin_atom_moles_by_account().get("process.cleaned_melt", {}).items()}

    def captured_feed_atoms(self, element: str, stage: int) -> CapturedFeedAtoms:
        ledger_mass = self.ledger.project_account_kg(CONDENSER_ACCOUNT)
        stages = self.sim._stage_collection_kg_by_source
        species = set(ledger_mass) | {name for account, _, name in stages if account == CONDENSER_ACCOUNT}
        designated = []
        other = []
        for name in sorted(species):
            formula = resolve_species_formula(name, self.ledger.registry)
            if element not in formula.elements:
                continue
            values = [(destination, mass) for (account, destination, carrier), mass in stages.items()
                      if account == CONDENSER_ACCOUNT and carrier == name]
            projected = math.fsum(mass for _, mass in values)
            actual = ledger_mass.get(name, 0.0)
            # Each routed credit/debit does a split, kg<->mol conversion and
            # accumulation. Bound those by 8 rounding operations per journal
            # event/retained entry. For n operations, gamma_n=n*eps/(1-n*eps);
            # absolute roundoff <= gamma_n * sum(|operands|), in kg. No fixed
            # mass floor: scaling the inventory scales the tolerance, and a
            # genuinely missing tiny carrier must still be flagged.
            n = 8 * (len(self.ledger.external_loads) + sum(
                len(item.debits) + len(item.credits) for item in self.ledger.transitions
            ) + len(values) + 1)
            eps_n = n * sys.float_info.epsilon
            tolerance = eps_n / (1 - eps_n) * (abs(actual) + math.fsum(abs(mass) for _, mass in values))
            if any(not math.isfinite(mass) or mass < 0 for _, mass in values) or abs(projected - actual) > tolerance:
                return CapturedFeedAtoms("unavailable", reason="stage_projection_unreconciled", diagnostic={
                    "species": name, "stage_projection_kg": projected,
                    "ledger_kg": actual, "tolerance_kg": tolerance,
                })
            # kg / (kg/mol-species) * mol-element/mol-species = mol-element.
            for destination, mass in values:
                atoms = formula.atom_moles(mass / formula.molar_mass_kg_per_mol())[element]
                (designated if destination == stage else other).append(atoms)
        origins = self.ledger.origin_atom_moles_by_account().get(CONDENSER_ACCOUNT, {}).get(element, {})
        unresolved = self.ledger.unresolved_origin_atom_moles_by_account().get(CONDENSER_ACCOUNT, {}).get(element, 0.0)
        if (unresolved or any(amount != 0 for origin, amount in origins.items() if origin != "feedstock")
                or (math.fsum(designated + other) > 0 and origins.get("feedstock", 0.0) <= 0)):
            return CapturedFeedAtoms("unavailable", reason="origin_unresolved_for_stage_split", diagnostic={
                "origins_mol": origins, "unresolved_mol": unresolved,
            })
        # With exclusively feedstock origin, every reconciled atom is a feed
        # atom. An empty train is exactly zero capture; no allocation is made.
        return CapturedFeedAtoms("available", math.fsum(designated), math.fsum(other))

    def inventory(self, elements, stage):
        origins = self.ledger.origin_atom_moles_by_account()
        feed = self.ledger.external_origin_atom_moles().get("feedstock", {})
        captures = {element: self.captured_feed_atoms(element, stage) for element in elements}
        losses = {account: {element: row.get(element, {}).get("feedstock", 0.0) for element in elements}
                  for account, row in origins.items()
                  if account.startswith("process.wall_deposit") or account in ("terminal.offgas", "vent")}
        extracted_accounts = [account for account in origins
                              if account in EXTRACTED_ACCOUNTS or account.startswith("process.wall_deposit")]
        return {
            "initial_feed_element_inventory": {element: feed.get(element, 0.0) for element in elements},
            "melt": {element: self.melt_feed_atoms().get(element, 0.0) for element in elements},
            "captured": {element: result.captured_mol for element, result in captures.items()},
            "extracted": {element: math.fsum(origins[account].get(element, {}).get("feedstock", 0.0)
                                             for account in extracted_accounts) for element in elements},
            "capture": {element: asdict(result) for element, result in captures.items()},
            "losses": losses,
            "extracted_accounts": extracted_accounts,
        }
