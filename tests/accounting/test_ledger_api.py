from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from simulator.accounting.ledger import AccountPolicy, KNOWN_LEDGER_ACCOUNTS, AtomLedger
from simulator.accounting.ledger_api import LEDGER_SCHEMA_VERSION, LedgerAPI


def _api() -> LedgerAPI:
    ledger = AtomLedger()
    ledger.load_external_mol("process.cleaned_melt", {"SiO2": 2.0, "CaO": 1.0})
    ledger.load_external_mol("process.metal_phase_float_layer", {"Al": 4.0})
    ledger.load_external_mol("process.metal_phase_bottom_pool", {"Fe": 5.0})
    ledger.load_external_mol("terminal.oxygen_melt_offgas_stored", {"O2": 6.0})
    ledger.load_external_mol("terminal.oxygen_melt_offgas_captured", {"O2": 7.0})
    ledger.load_external_mol("terminal.oxygen_melt_offgas_vented_to_vacuum", {"O2": 8.0})
    ledger.set_account_policy(
        "reservoir.fo2_buffer",
        AccountPolicy.reservoir("reservoir.fo2_buffer"),
    )
    sim = SimpleNamespace(
        atom_ledger=ledger,
        train=SimpleNamespace(stages=[]),
        _unspent_additive_reagents_kg=lambda: {},
        _terminal_rump_by_species=lambda: {"SiO2": 1.0},
        _terminal_rump_by_class=lambda: {
            "refractory_oxides": 0.0,
            "silicate_residual": 1.0,
            "unextracted_metals": 0.0,
            "other": 0.0,
        },
    )
    return LedgerAPI(sim)


def test_account_discovery_is_registry_complete_and_requires_zero_plumbing(monkeypatch):
    api = _api()
    ids = {row["id"] for row in api.accounts()["accounts"]}
    assert ids == set(KNOWN_LEDGER_ACCOUNTS)

    synthetic = frozenset(set(KNOWN_LEDGER_ACCOUNTS) | {"process.synthetic_new_account"})
    monkeypatch.setattr("simulator.accounting.ledger.KNOWN_LEDGER_ACCOUNTS", synthetic)
    synthetic_ids = {
        row["id"]
        for row in LedgerAPI(api.sim).accounts()["accounts"]
    }
    assert "process.synthetic_new_account" in synthetic_ids


def test_account_units_match_ledger_ground_truth_and_are_basis_aware():
    api = _api()
    ledger = api.ledger
    assert api.account("process.cleaned_melt", units="kg")["species"] == ledger.kg_by_account("process.cleaned_melt")
    assert api.account("process.cleaned_melt", units="mol")["species"] == ledger.mol_by_account("process.cleaned_melt")
    wt = api.account("process.cleaned_melt", units="wt_pct")
    assert wt["basis"] == "oxide"
    assert sum(wt["species"].values()) == pytest.approx(100.0)
    metal_wt = api.account("process.metal_phase_float_layer", units="wt_pct")
    assert metal_wt["basis"] == "elemental"
    assert metal_wt["species"] == {"Al": pytest.approx(100.0)}


def test_signed_accounts_omit_wt_pct_instead_of_renormalizing_mass():
    api = _api()
    signed = "reservoir.fo2_buffer"
    assert api.accounts()["accounts"]
    result = api.account(signed, units="wt_pct")
    assert result["species"] is None
    assert result["wt_pct_basis"] == "omitted_for_signed_account"
    assert api.account(signed, units="kg")["species"] == {}
    assert api.account(signed, units="mol")["species"] == {}


def test_named_views_preserve_tap_and_oxygen_account_distinctions():
    api = _api()
    assert api.view("melt_pot_upper_tap")["data"]["account"] == "process.metal_phase_float_layer"
    assert api.view("melt_pot_bottom_tap")["data"]["account"] == "process.metal_phase_bottom_pool"
    oxygen = api.view("oxygen_partition")["data"]
    assert oxygen["melt_offgas_stored"] != oxygen["melt_offgas_captured"]
    assert oxygen["melt_offgas_captured"] != oxygen["melt_offgas_vented"]
    assert api.view("terminal_ceramic")["data"]["classifier"] == "terminal_rump"
    assert api.view("condensation_train")["view"] == "condensation_train"
    assert api.view("offgas")["data"] == {"terminal": {}, "near_melt": {}}
    assert api.view("wall_deposits")["data"]["segments_kg"] == {}
    assert api.view("industrial_glass")["data"]["early_tap_mode"] is True
    assert api.view("stage_purity")["view"] == "stage_purity"


def test_wt_pct_compatibility_ignores_malformed_values():
    from simulator.accounting.ledger_api import oxide_wt_pct_from_kg

    assert oxide_wt_pct_from_kg(None) == {}
    assert oxide_wt_pct_from_kg({"SiO2": 2.0, "bad": "nope", "O2": 9.0}) == {"SiO2": 100.0}


def test_snapshot_is_versioned_attested_and_read_only():
    api = _api()
    before = json.dumps(api.ledger.close_report(), sort_keys=True)
    snapshot = api.snapshot()
    after = json.dumps(api.ledger.close_report(), sort_keys=True)
    assert before == after
    assert snapshot["ledger_schema_version"] == LEDGER_SCHEMA_VERSION
    assert snapshot["provenance"]["mass_balance_attested"] is True
    assert api.accounts()["ledger_schema_version"] == LEDGER_SCHEMA_VERSION


def _assert_byte_identical_read(api: LedgerAPI, read) -> None:
    before = json.dumps(api.ledger.close_report(), sort_keys=True).encode()
    read()
    after = json.dumps(api.ledger.close_report(), sort_keys=True).encode()
    assert after == before


def test_every_l1_resource_and_named_view_is_byte_identical_read_only():
    api = _api()
    reads = [api.accounts, api.snapshot]
    reads.extend(
        lambda account=account, units=units: api.account(account, units=units)
        for account in sorted(KNOWN_LEDGER_ACCOUNTS)
        for units in ("kg", "mol", "wt_pct")
    )
    reads.append(lambda: api.account_pattern("process.wall_deposit_segment_*"))
    reads.extend(lambda view=view: api.view(view) for view in api.view_names())
    for read in reads:
        _assert_byte_identical_read(api, read)


def test_every_http_ledger_get_is_typed_and_byte_identical_read_only():
    import app as app_module
    from web.events import _simulations, _sim_locks

    api = _api()
    sid = "ledger-rest-test"
    client_id = "ledger-browser-test"
    _simulations[sid] = {
        "session": SimpleNamespace(simulator=api.sim),
        "run_id": "ledger-rest-run",
        "ledger_client_id": client_id,
    }
    _sim_locks[sid] = threading.RLock()
    client = app_module.create_app().test_client()
    with client.session_transaction() as browser_session:
        browser_session['ledger_client_id'] = client_id
    urls = [
        "/api/ledger/accounts",
        "/api/ledger/snapshot",
        "/api/ledger/account?pattern=process.wall_deposit_segment_*",
    ]
    urls.extend(
        f"/api/ledger/account/{account}?units={units}"
        for account in sorted(KNOWN_LEDGER_ACCOUNTS)
        for units in ("kg", "mol", "wt_pct")
    )
    urls.extend(
        f"/api/ledger/views/{view}"
        for view in api.view_names()
    )
    try:
        for url in urls:
            _assert_byte_identical_read(api, lambda url=url: _assert_http_ok(client, url))
        assert client.get("/api/ledger/account/not.real").status_code == 404
        assert client.get("/api/ledger/views/not_real").status_code == 404
        assert client.get(
            "/api/ledger/account/process.cleaned_melt?units=grams"
        ).status_code == 400
    finally:
        _simulations.pop(sid, None)
        _sim_locks.pop(sid, None)


def _assert_http_ok(client, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
