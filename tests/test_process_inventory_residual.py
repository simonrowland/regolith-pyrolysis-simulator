from __future__ import annotations

from types import SimpleNamespace

from simulator.accounting.queries import AccountingQueries


class _Ledger:
    def __init__(self, accounts: dict[str, dict[str, float]]) -> None:
        self._accounts = accounts

    def kg_by_account(self, account: str | None = None):
        if account is None:
            return {key: dict(value) for key, value in self._accounts.items()}
        return dict(self._accounts.get(account, {}))

    def project_account_kg(self, account: str) -> dict[str, float]:
        return {
            species: float(kg)
            for species, kg in self.kg_by_account(account).items()
            if float(kg) > 0.0
        }


def test_spent_reductant_residue_query_is_separate_from_native_rump():
    sim = SimpleNamespace(
        atom_ledger=_Ledger(
            {
                "process.cleaned_melt": {"CaO": 3.0},
                "terminal.slag": {},
                "process.spent_reductant_residue": {"Na2O": 2.0},
            }
        ),
        _RUMP_ELEMENT_SPECIES={"Ca": ("CaO",), "Na": ("Na2O",)},
        _RUMP_EXPECTATION_TOL_KG=1.0e-9,
    )
    queries = AccountingQueries(sim)

    assert queries.terminal_rump_by_species() == {"CaO": 3.0}
    assert queries.spent_reductant_residue_by_species() == {"Na2O": 2.0}
    assert queries.terminal_residual_buckets() == {
        "native_terminal_rump": {"CaO": 3.0},
        "process_inventory_spent_reductant": {"Na2O": 2.0},
    }
    assert queries.rump_element_kg("Na") == 0.0
