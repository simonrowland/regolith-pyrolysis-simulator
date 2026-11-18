"""Expected ledger quantities come from recorded lots, not the production debit helper."""

from simulator.accounting import resolve_species_formula


def _transition_account_species_mol(
    sim,
    transition,
    *,
    side: str,
    account: str,
    species: str,
) -> float:
    formula = resolve_species_formula(species, sim.species_formula_registry)
    lots = transition.debits if side == "debits" else transition.credits
    return sum(
        float(lot.species_kg.get(species, 0.0))
        / formula.molar_mass_kg_per_mol()
        for lot in lots
        if lot.account == account
    )
