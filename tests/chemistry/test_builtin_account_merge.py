from engines.builtin._common import merge_species_amounts_from_accounts


def test_merge_species_amounts_from_accounts_pins_current_provider_output() -> None:
    balances = {
        "melt": {"FeO": 2.0, "Al": 0.0, "O2": 0.0},
        "spent": {"FeO": 1.5, "Al": -2.0, "SiO": 1.25},
        "ignored": {"FeO": 999.0},
    }

    assert merge_species_amounts_from_accounts(
        balances,
        ("melt", "missing", "spent"),
    ) == {"FeO": 3.5, "Al": -2.0, "SiO": 1.25}
