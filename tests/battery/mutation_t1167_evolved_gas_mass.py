"""Verify the gas-yield test rejects using hydrogen's mass for every species."""

import inspect

import pytest

import simulator.battery.migrate as migrate


source = inspect.getsource(migrate.select_declared_source)
needle = "molar_mass = Decimal(str(formula.molar_mass_g_per_mol()))"
assert source.count(needle) == 1
exec(
    compile(
        source.replace(needle, 'molar_mass = Decimal("2.016")'),
        "<t1167-fixed-h2-mass-mutant>",
        "exec",
    ),
    migrate.__dict__,
)

result = pytest.main(
    [
        "-q",
        "-n",
        "0",
        "-p",
        "no:cacheprovider",
        "tests/battery/test_t1167_evolved_gas_yield.py",
    ]
)
print(f"FIXED_H2_MASS_MUTANT_EXIT {result}")
assert result == 1, "fixed-H2-mass mutant unexpectedly survived"
