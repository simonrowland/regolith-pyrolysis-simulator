"""F2 root C: stage0 account projection failures surface on the diagnostic."""

from __future__ import annotations

import pytest

from simulator.accounting.exceptions import AccountingError
from simulator.accounting import stage0_inventory as s0


class _BoomLedger:
    def project_account_kg(self, account):
        raise RuntimeError(f"project boom {account}")

    def kg_by_account(self, account):
        raise RuntimeError(f"kg boom {account}")


def test_account_projection_records_failures_when_list_provided() -> None:
    failures: list[str] = []
    out = s0._species_kg_by_accounts(
        _BoomLedger(),
        ("process.cleaned_melt",),
        projection_failures=failures,
    )
    assert out == {}
    assert failures and "process.cleaned_melt" in failures[0]


def test_account_projection_raises_without_collector() -> None:
    with pytest.raises(AccountingError, match="account projection failed"):
        s0._species_kg_by_accounts(_BoomLedger(), ("process.cleaned_melt",))


def test_mutation_proof_old_continue_omits_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    def _old(ledger, accounts, projection_failures=None):
        out = {}
        if ledger is None:
            return out
        for account in accounts:
            try:
                species_kg = ledger.project_account_kg(account)
            except Exception:
                try:
                    species_kg = ledger.kg_by_account(account)
                except Exception:
                    continue
            if isinstance(species_kg, dict) and species_kg:
                out[account] = species_kg
        return out

    monkeypatch.setattr(s0, "_species_kg_by_accounts", _old)
    assert s0._species_kg_by_accounts(_BoomLedger(), ("process.cleaned_melt",)) == {}
