"""Shared oxygen destination rule for evaporation ledger transitions."""


def reaction_oxygen_destination(oxygen_amount: float, vapor_oxygen_atoms: float,
                                explicit_destination: str) -> str:
    """Ledger destination for signed coproduct oxygen (negative means debit)."""
    if explicit_destination not in {'', 'process.overhead_gas', 'reservoir.fo2_buffer'}:
        # Import lazily: importing simulator.accounting.exceptions initializes
        # simulator.core, which imports this leaf during simulator startup.
        from simulator.accounting.exceptions import AccountingError

        raise AccountingError(f'unsupported oxygen_destination {explicit_destination!r}')
    if oxygen_amount < 0.0:
        return 'process.overhead_gas'
    if explicit_destination:
        return explicit_destination
    return ('process.overhead_gas' if vapor_oxygen_atoms > 0.0
            else 'reservoir.fo2_buffer')
