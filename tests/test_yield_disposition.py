import math
from types import SimpleNamespace

import pytest

from simulator.accounting import (
    AccountPolicy,
    AtomLedger,
    LedgerTransition,
    MaterialLot,
    MaterialOriginError,
    OverdraftError,
    PoolWithdrawalError,
)
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.lots import allocate_pool_withdrawal
from simulator.accounting.yield_disposition import (
    MELT_RETAINED_SUBDISPOSITIONS,
    OriginUnresolvedError,
    YIELD_DISPOSITION_BINS,
    YieldDispositionError,
    build_yield_disposition,
    capture_ledger_snapshot,
    ledger_snapshots_from_sim,
)


def _sim(ledger: AtomLedger) -> SimpleNamespace:
    return SimpleNamespace(atom_ledger=ledger)


def _row(payload: dict, element: str) -> dict:
    return next(
        row
        for row in payload["fraction_table"]["rows"]
        if row["element"] == element
    )


def test_campaign_snapshot_captures_only_condensation_origin_state() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.cleaned_melt",
        {"FeO": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    ledger.load_external(
        "process.condensation_train",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)

    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )

    snapshot = ledger_snapshots_from_sim(sim)[0]
    assert set(snapshot["ledger"]) == {"process.condensation_train"}
    assert set(snapshot["material_origin_atom_moles_by_account"]) == {
        "process.condensation_train"
    }
    assert snapshot["material_origin_atom_moles_by_account"][
        "process.condensation_train"
    ]["Na"]["feedstock"] == pytest.approx(
        1.0 / resolve_species_formula("Na", {}).molar_mass_kg_per_mol()
    )
    assert set(snapshot["gross_inputs"]) == {
        "ledger",
        "material_origin_atom_moles_by_account",
        "origin_unattributed_atom_moles_by_account",
    }
    assert set(snapshot["gross_withdrawals"]) == {
        "ledger",
        "material_origin_atom_moles_by_account",
        "origin_unattributed_atom_moles_by_account",
    }
    assert [
        event["direction"] for event in snapshot["gross_events"]
    ] == ["inputs"]


def test_clean_feedstock_closes_on_element_basis_with_chart_payload() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.cleaned_melt",
        {"FeO": 1.0, "SiO2": 2.0},
        source="feedstock",
        material_origin="feedstock",
    )

    payload = build_yield_disposition(_sim(ledger))

    assert tuple(payload["destination_bins"]) == YIELD_DISPOSITION_BINS
    assert payload["closure"]["maximum_residual_fraction"] <= 5.0e-14
    assert {node["kind"] for node in payload["nodes"]} == {
        "feedstock_element",
        "destination",
    }
    fe = _row(payload, "Fe")
    assert fe["attribution_method"] == "tracked"
    assert fe["destination_fractions"]["melt_retained"] == pytest.approx(1.0)
    assert sum(fe["destination_fractions"].values()) == pytest.approx(
        1.0, abs=5.0e-14
    )
    assert any(
        link["element"] == "Fe"
        and link["target"] == "destination:melt_retained"
        for link in payload["links"]
    )
    assert tuple(
        payload["melt_retained_subdispositions"]["subdispositions"]
    ) == MELT_RETAINED_SUBDISPOSITIONS
    assert {
        stream["species"] for stream in payload["terminal_species_streams"]
    } == {"FeO", "SiO2"}


def test_origin_split_excludes_reagent_atoms_without_scaling_feedstock() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    ledger.load_external(
        "reservoir.reagent.Na",
        {"Na": 0.5},
        source="reagent",
        material_origin="reagent",
    )
    ledger.move(
        "feedstock_na_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 1.0},
    )
    ledger.move(
        "reagent_na_capture",
        "reservoir.reagent.Na",
        "process.condensation_train",
        {"Na": 0.5},
    )

    payload = build_yield_disposition(_sim(ledger))

    na = _row(payload, "Na")
    assert na["destination_fractions"]["product_condensed"] == pytest.approx(1.0)
    assert na["attribution_method"] == "tracked"
    reagent = payload["reagent_cycle"]["rows"][0]
    assert reagent["element"] == "Na"
    assert reagent["input_mol_atoms"] == pytest.approx(
        reagent["terminal_excluded_mol_atoms"]
    )
    assert reagent["closure_residual_fraction"] == pytest.approx(0.0)


def test_clean_c2a_recovered_reagent_roundoff_does_not_invent_origin() -> None:
    physical_mol = 0.22746831458806405
    molar_kg = resolve_species_formula("Na", {}).molar_mass_kg_per_mol()
    ledger = AtomLedger()
    physical_kg = physical_mol * molar_kg
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": physical_kg},
        source="clean C2A feedstock Na",
        material_origin="feedstock",
    )
    ledger.move(
        "clean_C2A_recovered_Na",
        "process.raw_feedstock",
        "process.reagent_inventory",
        {"Na": physical_kg},
    )

    payload = build_yield_disposition(_sim(ledger))

    assert payload["reagent_cycle"]["rows"] == []
    na = _row(payload, "Na")
    assert na["destination_fractions"]["product_condensed"] == pytest.approx(
        1.0,
        abs=5.0e-14,
    )


def test_origin_unattributed_is_counted_in_strict_mass_closure() -> None:
    charged_mol = 1.0e6
    relative_tolerance = AtomLedger().relative_tolerance
    boundary_mol = charged_mol * relative_tolerance
    molar_kg = resolve_species_formula("O", {}).molar_mass_kg_per_mol()

    def disposition(remainder_mol: float) -> dict:
        ledger = AtomLedger(
            initial_balances={
                "process.cleaned_melt": {
                    "O": remainder_mol * molar_kg,
                },
            }
        )
        ledger.load_external(
            "process.cleaned_melt",
            {"O": charged_mol * molar_kg},
            source="feedstock O",
            material_origin="feedstock",
        )
        return build_yield_disposition(_sim(ledger))

    payload = disposition(0.99 * boundary_mol)
    assert payload["reagent_cycle"]["rows"] == []
    assert payload["closure"]["origin_dust_limit_mol_atoms"] == pytest.approx(
        boundary_mol
    )
    assert payload["origin_unattributed"]["terminal_mol_atoms_by_element"][
        "O"
    ] == pytest.approx(0.99 * boundary_mol)
    assert payload["closure"]["maximum_residual_fraction"] <= 5.0e-14
    with pytest.raises(
        OriginUnresolvedError,
        match=r"cumulative origin_unattributed exceeds attribution limit for O",
    ):
        disposition(1.01 * boundary_mol)


def test_pure_origin_unattributed_stream_uses_declared_schema_value() -> None:
    charged_mol = 1.0e6
    unresolved_mol = (
        0.5
        * charged_mol
        * AtomLedger().relative_tolerance
    )
    sodium_molar_kg = resolve_species_formula(
        "Na",
        {},
    ).molar_mass_kg_per_mol()
    ledger = AtomLedger(
        initial_balances={
            "terminal.offgas": {
                "Na": unresolved_mol * sodium_molar_kg,
            }
        }
    )
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"O": charged_mol},
        source="feedstock O",
        material_origin="feedstock",
    )

    payload = build_yield_disposition(_sim(ledger))
    row = next(
        row
        for row in payload["terminal_species_streams"]
        if row["species"] == "Na"
    )

    assert row["origin_scope"] == "origin_unattributed"
    assert row["attribution_method"] == "origin_unattributed"


def test_sub_reportable_terminal_origin_dust_is_ignored() -> None:
    oxygen_molar_kg = resolve_species_formula(
        "O",
        {},
    ).molar_mass_kg_per_mol()
    ledger = AtomLedger(
        initial_balances={
            "process.overhead_gas": {
                "O": 1.4e-17 * oxygen_molar_kg,
            },
        }
    )
    sodium_molar_kg = resolve_species_formula(
        "Na",
        {},
    ).molar_mass_kg_per_mol()
    ledger.load_external(
        "process.cleaned_melt",
        {"Na": sodium_molar_kg},
        source="charged feedstock scale",
        material_origin="feedstock",
    )

    payload = build_yield_disposition(_sim(ledger))

    assert [row["element"] for row in payload["fraction_table"]["rows"]] == ["Na"]
    assert {
        row["species"] for row in payload["terminal_species_streams"]
    } == {"Na"}


def test_unstamped_external_producer_raises_typed_error() -> None:
    with pytest.raises(
        MaterialOriginError,
        match="external material load requires material_origin",
    ):
        AtomLedger().load_external(
            "process.cleaned_melt",
            {"FeO": 1.0},
            source="unstamped producer",
        )


def test_partial_mixed_origin_withdrawal_requires_amalgamated_pool() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        material_origin="feedstock",
    )
    ledger.load_external(
        "reservoir.reagent.Na",
        {"Na": 0.5},
        material_origin="reagent",
    )
    ledger.move(
        "feedstock_na_to_untyped_mix",
        "process.raw_feedstock",
        "process.reagent_inventory",
        {"Na": 1.0},
    )
    ledger.move(
        "reagent_na_to_untyped_mix",
        "reservoir.reagent.Na",
        "process.reagent_inventory",
        {"Na": 0.5},
    )

    with pytest.raises(
        OriginUnresolvedError,
        match="requires an explicitly amalgamated pool",
    ):
        ledger.move(
            "withdraw_unsanctioned_ratio",
            "process.reagent_inventory",
            "process.condensation_train",
            {"Na": 0.75},
        )


@pytest.mark.parametrize("element", ("Na", "Mg"))
def test_amalgamated_reagent_pool_uses_input_ratio_and_closes_exactly(
    element: str,
) -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {element: 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    ledger.load_external(
        f"reservoir.reagent.{element}",
        {element: 0.5},
        source="reagent",
        material_origin="reagent",
    )
    ledger.move(
        "feedstock_to_pool",
        "process.raw_feedstock",
        "process.reagent_inventory",
        {element: 1.0},
    )
    ledger.move(
        "reagent_to_pool",
        f"reservoir.reagent.{element}",
        "process.reagent_inventory",
        {element: 0.5},
        amalgamated_pool=True,
    )
    ledger.move(
        "withdraw_pool",
        "process.reagent_inventory",
        "process.condensation_train",
        {element: 0.75},
    )

    payload = build_yield_disposition(_sim(ledger))
    balance = ledger.origin_atom_moles_by_account()["process.condensation_train"][
        element
    ]
    assert balance["feedstock"] / sum(balance.values()) == pytest.approx(2.0 / 3.0)
    assert balance["reagent"] / sum(balance.values()) == pytest.approx(1.0 / 3.0)
    assert _row(payload, element)["attribution_method"] == "pool_ratio"
    assert payload["reagent_cycle"]["rows"][0]["attribution_method"] == "pool_ratio"
    assert {
        row["attribution_method"] for row in payload["links"]
    } == {"pool_ratio"}
    assert {
        row["attribution_method"]
        for row in payload["terminal_species_streams"]
    } == {"pool_ratio"}
    assert payload["closure"]["maximum_residual_fraction"] == pytest.approx(0.0)
    assert payload["reagent_cycle"]["closure_residual_fraction"] == pytest.approx(0.0)


def test_stage0_uses_recorded_observer_origin() -> None:
    ledger = AtomLedger()
    molar_kg = {
        species: resolve_species_formula(species, {}).molar_mass_kg_per_mol()
        for species in ("C", "CO", "SO2", "SO3")
    }
    ledger.load_external(
        "process.stage0_salt_feed",
        {"SO3": molar_kg["SO3"]},
        source="feedstock sulfate",
        material_origin="feedstock",
    )
    ledger.load_external(
        "reservoir.reagent.C",
        {"C": molar_kg["C"]},
        source="batch additive C",
        material_origin="reagent",
    )
    ledger.move(
        "draw_C_reagent_to_process",
        "reservoir.reagent.C",
        "process.reagent_inventory",
        {"C": molar_kg["C"]},
        material_origin="reagent",
    )
    ledger.apply(
        LedgerTransition(
            name="stage0_sulfate_carbon_cleanup",
            debits=(
                MaterialLot(
                    "process.stage0_salt_feed",
                    {"SO3": molar_kg["SO3"]},
                ),
                MaterialLot(
                    "process.reagent_inventory",
                    {"C": molar_kg["C"]},
                ),
            ),
            credits=(
                MaterialLot("terminal.offgas", {"CO": molar_kg["CO"]}),
                MaterialLot("terminal.offgas", {"SO2": molar_kg["SO2"]}),
            ),
        )
    )

    payload = build_yield_disposition(_sim(ledger))

    committed = ledger.transitions[-1]
    assert [lot.material_origin for lot in committed.debits] == [
        "feedstock",
        "reagent",
    ]
    assert all(
        set(lot.attribution_method_by_element.values()) == {"tracked"}
        for lot in committed.credits
    )
    assert _row(payload, "S")["destination_fractions"]["offgas_vented"] == pytest.approx(
        1.0
    )
    assert _row(payload, "O")["destination_fractions"]["offgas_vented"] == pytest.approx(
        1.0
    )
    assert {row["element"] for row in payload["reagent_cycle"]["rows"]} == {"C"}
    assert all(
        row["attribution_method"] == "tracked"
        for row in payload["fraction_table"]["rows"]
    )


def test_genuinely_unknown_origin_raises_typed_error() -> None:
    molar_kg = {
        species: resolve_species_formula(species, {}).molar_mass_kg_per_mol()
        for species in ("C", "CO", "SO2", "SO3")
    }
    ledger = AtomLedger(
        initial_balances={
            "process.reagent_inventory": {"C": molar_kg["C"]},
        }
    )
    ledger.load_external(
        "process.stage0_salt_feed",
        {"SO3": molar_kg["SO3"]},
        source="feedstock sulfate",
        material_origin="feedstock",
    )
    ledger.apply(
        LedgerTransition(
            name="stage0_missing_origin_observer",
            debits=(
                MaterialLot(
                    "process.stage0_salt_feed",
                    {"SO3": molar_kg["SO3"]},
                ),
                MaterialLot(
                    "process.reagent_inventory",
                    {"C": molar_kg["C"]},
                ),
            ),
            credits=(
                MaterialLot("terminal.offgas", {"CO": molar_kg["CO"]}),
                MaterialLot("terminal.offgas", {"SO2": molar_kg["SO2"]}),
            ),
        )
    )

    with pytest.raises(
        OriginUnresolvedError,
        match=r"cumulative origin_unattributed exceeds attribution limit for C",
    ):
        build_yield_disposition(_sim(ledger))


def test_offsetting_origins_remain_typed_without_account_inference() -> None:
    molar_kg = resolve_species_formula("Na", {}).molar_mass_kg_per_mol()
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": molar_kg},
        source="feedstock Na",
        material_origin="feedstock",
    )
    ledger.move(
        "park_feedstock_na_in_reagent_inventory",
        "process.raw_feedstock",
        "process.reagent_inventory",
        {"Na": molar_kg},
    )
    ledger.load_external(
        "reservoir.reagent.Na",
        {"Na": molar_kg},
        source="reagent Na",
        material_origin="reagent",
    )
    ledger.move(
        "vent_reagent_na",
        "reservoir.reagent.Na",
        "terminal.offgas",
        {"Na": molar_kg},
    )

    payload = build_yield_disposition(_sim(ledger))
    assert _row(payload, "Na")["destination_fractions"][
        "product_condensed"
    ] == pytest.approx(1.0)
    assert payload["reagent_cycle"]["closure_residual_fraction"] == pytest.approx(0.0)


def test_condensation_capture_is_split_by_campaign_without_account_guess() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 0.4},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 0.6},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    fractions = _row(payload, "Na")["destination_fractions"]
    assert fractions["cleanup_volatile_product"] == pytest.approx(0.4)
    assert fractions["product_condensed"] == pytest.approx(0.6)


@pytest.mark.parametrize("element", ("Na", "K"))
def test_condensation_campaign_pool_withdrawal_uses_input_ratio(
    element: str,
) -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {element: 2.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {element: 1.0},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {element: 1.0},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )
    ledger.move(
        "shared_pool_withdrawal",
        "process.condensation_train",
        "terminal.offgas",
        {element: 0.5},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=2, campaign="C2A", campaign_hour=1),
    )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    fractions = _row(payload, element)["destination_fractions"]
    assert fractions["cleanup_volatile_product"] == pytest.approx(0.375)
    assert fractions["product_condensed"] == pytest.approx(0.375)
    assert fractions["offgas_vented"] == pytest.approx(0.25)
    assert _row(payload, element)["attribution_method"] == "pool_ratio"
    assert {
        row["attribution_method"]
        for row in payload["terminal_species_streams"]
        if row["account"] == "process.condensation_train"
    } == {"pool_ratio"}
    assert payload["closure"]["maximum_residual_fraction"] == pytest.approx(0.0)


def test_condensation_dust_edge_ratio_allocation_stays_pool_ratio() -> None:
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"O": 1.0e6},
        source="feedstock oxygen",
        material_origin="feedstock",
    )
    ledger.load_external_mol(
        "process.raw_feedstock",
        {"Na": 1.0005},
        source="feedstock sodium",
        material_origin="feedstock",
    )
    attribution_band = (1.0e6 + 1.0005) * ledger.relative_tolerance
    assert 0.0005 < attribution_band < 1.0
    sim = _sim(ledger)
    sodium_molar_kg = resolve_species_formula(
        "Na",
        {},
    ).molar_mass_kg_per_mol()
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 0.0005 * sodium_molar_kg},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 1.0 * sodium_molar_kg},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )
    ledger.move(
        "dust_edge_pool_withdrawal",
        "process.condensation_train",
        "terminal.offgas",
        {"Na": 1.00025 * sodium_molar_kg},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=2, campaign="C2A", campaign_hour=1),
    )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    assert _row(payload, "Na")["attribution_method"] == "pool_ratio"
    assert {
        row["attribution_method"]
        for row in payload["terminal_species_streams"]
        if row["account"] == "process.condensation_train"
    } == {"pool_ratio"}


@pytest.mark.parametrize(
    ("missing_key", "message"),
    (
        (
            "material_origin_atom_moles_by_account",
            "missing typed-origin snapshot",
        ),
        (
            "origin_attribution_methods_by_account",
            "missing attribution snapshot",
        ),
    ),
)
def test_nonzero_condensation_snapshot_requires_typed_origin_evidence(
    missing_key: str,
    message: str,
) -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 1.0},
    )
    sim = _sim(ledger)
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    snapshot = ledger_snapshots_from_sim(sim)[0]
    snapshot.pop(missing_key)

    with pytest.raises(OriginUnresolvedError, match=message):
        build_yield_disposition(sim, (snapshot,))


def test_condensation_campaign_split_requires_origin_atoms_to_match_species() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 0.4},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 0.6},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )
    snapshots = ledger_snapshots_from_sim(sim)
    total_mol = 1.0 / resolve_species_formula(
        "Na",
        {},
    ).molar_mass_kg_per_mol()
    snapshots[1]["material_origin_atom_moles_by_account"][
        "process.condensation_train"
    ]["Na"] = {
        "feedstock": 0.2 * total_mol,
        "reagent": 0.8 * total_mol,
    }
    snapshots[0]["gross_inputs"][
        "material_origin_atom_moles_by_account"
    ]["process.condensation_train"]["Na"] = {
        "feedstock": 0.2 * total_mol,
        "reagent": 0.2 * total_mol,
    }
    snapshots[1]["gross_inputs"][
        "material_origin_atom_moles_by_account"
    ]["process.condensation_train"]["Na"] = {
        "feedstock": 0.8 * total_mol,
        "reagent": 0.2 * total_mol,
    }
    snapshots[0]["gross_events"][0]["material_origin_atom_moles"]["Na"] = {
        "feedstock": 0.2 * total_mol,
        "reagent": 0.2 * total_mol,
    }
    snapshots[1]["gross_events"][0]["material_origin_atom_moles"]["Na"] = {
        "feedstock": 0.2 * total_mol,
        "reagent": 0.2 * total_mol,
    }
    snapshots[1]["gross_events"][1]["material_origin_atom_moles"]["Na"] = {
        "feedstock": 0.6 * total_mol,
    }

    with pytest.raises(
        OriginUnresolvedError,
        match="condensation cleanup split typed origin does not close",
    ):
        build_yield_disposition(sim, snapshots)


def test_same_interval_pool_input_and_withdrawal_use_gross_counters() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 3.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 2.0},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 1.0},
    )
    ledger.move(
        "same_interval_withdrawal",
        "process.condensation_train",
        "terminal.offgas",
        {"Na": 0.6},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    fractions = _row(payload, "Na")["destination_fractions"]
    assert fractions["cleanup_volatile_product"] == pytest.approx(1.6 / 3.0)
    assert fractions["product_condensed"] == pytest.approx(0.8 / 3.0)
    assert fractions["offgas_vented"] == pytest.approx(0.6 / 3.0)
    assert _row(payload, "Na")["attribution_method"] == "pool_ratio"


def test_same_interval_withdrawal_before_input_preserves_event_order() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 3.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 2.0},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "same_interval_withdrawal",
        "process.condensation_train",
        "terminal.offgas",
        {"Na": 0.6},
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Na": 1.0},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    fractions = _row(payload, "Na")["destination_fractions"]
    assert fractions["cleanup_volatile_product"] == pytest.approx(1.4 / 3.0)
    assert fractions["product_condensed"] == pytest.approx(1.0 / 3.0)
    assert fractions["offgas_vented"] == pytest.approx(0.6 / 3.0)
    assert _row(payload, "Na")["attribution_method"] == "tracked"


@pytest.mark.parametrize(
    ("dust_factor", "should_refuse"),
    ((0.5, False), (1.0, False), (1.1, True)),
)
def test_nonpoolable_campaign_dust_liveness_uses_attribution_band(
    dust_factor: float,
    should_refuse: bool,
) -> None:
    main_mol = 1.0e6
    dust_mol = (
        dust_factor
        * main_mol
        * AtomLedger().relative_tolerance
    )
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.raw_feedstock",
        {"Fe": main_mol + dust_mol},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    iron_molar_kg = resolve_species_formula(
        "Fe",
        {},
    ).molar_mass_kg_per_mol()
    ledger.move(
        "cleanup_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Fe": dust_mol * iron_molar_kg},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    ledger.move(
        "main_capture",
        "process.raw_feedstock",
        "process.condensation_train",
        {"Fe": main_mol * iron_molar_kg},
    )
    ledger.move(
        "main_withdrawal",
        "process.condensation_train",
        "terminal.offgas",
        {"Fe": 0.5 * iron_molar_kg},
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )

    if should_refuse:
        with pytest.raises(
            OriginUnresolvedError,
            match="condensation withdrawal commingles cleanup/main Fe",
        ):
            build_yield_disposition(sim, ledger_snapshots_from_sim(sim))
    else:
        payload = build_yield_disposition(
            sim,
            ledger_snapshots_from_sim(sim),
        )
        assert payload["closure"]["maximum_residual_fraction"] <= 5.0e-14


def test_origin_unattributed_salami_sequence_refuses_cumulatively() -> None:
    charged_mol = 1000.0
    limit_mol = charged_mol * AtomLedger().relative_tolerance
    oxygen_molar_kg = resolve_species_formula(
        "O",
        {},
    ).molar_mass_kg_per_mol()
    piece_mol = 0.09 * limit_mol
    initial_balances = {
        f"process.unattributed_source_{index}": {
            "O": piece_mol * oxygen_molar_kg,
        }
        for index in range(12)
    }
    ledger = AtomLedger(initial_balances=initial_balances)
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"O": charged_mol},
        source="feedstock O",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    for index in range(12):
        ledger.move(
            f"salami_input_{index}",
            f"process.unattributed_source_{index}",
            "process.condensation_train",
            {"O": piece_mol * oxygen_molar_kg},
        )
        capture_ledger_snapshot(
            sim,
            SimpleNamespace(
                hour=2 * index,
                campaign="C0" if index < 6 else "C2A",
                campaign_hour=index,
            ),
        )
        ledger.move(
            f"salami_withdrawal_{index}",
            "process.condensation_train",
            "terminal.offgas",
            {"O": piece_mol * oxygen_molar_kg},
        )
        capture_ledger_snapshot(
            sim,
            SimpleNamespace(
                hour=2 * index + 1,
                campaign="C0" if index < 6 else "C2A",
                campaign_hour=index,
            ),
        )

    events = ledger_snapshots_from_sim(sim)[-1]["gross_events"]
    withdrawals = [
        event["origin_unattributed_atom_moles"]["O"]
        for event in events
        if event["direction"] == "withdrawals"
    ]
    assert withdrawals == pytest.approx([piece_mol] * 12)
    assert math.fsum(withdrawals) == pytest.approx(1.08 * limit_mol)

    with pytest.raises(
        OriginUnresolvedError,
        match=r"cumulative origin_unattributed exceeds attribution limit for O",
    ):
        build_yield_disposition(sim, ledger_snapshots_from_sim(sim))


def test_origin_unattributed_recirculation_counts_unique_mass_once() -> None:
    charged_mol = 1000.0
    limit_mol = charged_mol * AtomLedger().relative_tolerance
    sodium_molar_kg = resolve_species_formula(
        "Na",
        {},
    ).molar_mass_kg_per_mol()
    unresolved_mol = 0.6 * limit_mol
    ledger = AtomLedger(
        initial_balances={
            "process.unattributed_source": {
                "Na": unresolved_mol * sodium_molar_kg,
            }
        }
    )
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"O": charged_mol},
        source="feedstock O",
        material_origin="feedstock",
    )
    sim = _sim(ledger)
    for hour, (source, target) in enumerate(
        (
            ("process.unattributed_source", "process.condensation_train"),
            ("process.condensation_train", "process.recycle_buffer"),
            ("process.recycle_buffer", "process.condensation_train"),
            ("process.condensation_train", "terminal.offgas"),
        )
    ):
        ledger.move(
            f"cycle_{hour}",
            source,
            target,
            {"Na": unresolved_mol * sodium_molar_kg},
        )
        capture_ledger_snapshot(
            sim,
            SimpleNamespace(hour=hour, campaign="C0", campaign_hour=hour),
        )

    payload = build_yield_disposition(sim, ledger_snapshots_from_sim(sim))

    assert payload["origin_unattributed"][
        "cumulative_mol_atoms_by_element"
    ]["Na"] == pytest.approx(unresolved_mol)
    assert payload["origin_unattributed"][
        "terminal_mol_atoms_by_element"
    ]["Na"] == pytest.approx(unresolved_mol)


def test_pool_allocator_over_withdrawal_raises_typed() -> None:
    with pytest.raises(
        PoolWithdrawalError,
        match="withdrawal exceeds available balance",
    ):
        allocate_pool_withdrawal({"feedstock": 1.0}, 1.1)


def test_origin_withdrawal_within_ledger_atom_tolerance_preserves_all_atoms() -> None:
    atom_tolerance_mol = 1.0e-6
    shortfall_mol_atoms = 0.5 * atom_tolerance_mol
    ledger = AtomLedger(
        account_policies=(
            AccountPolicy.reservoir(
                "reservoir.fo2_buffer",
                credit_limit_kg_by_species={"O2": 1.0},
            ),
        ),
        atom_tolerance_mol=atom_tolerance_mol,
    )
    ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 0.5},
        source="feedstock oxygen buffer",
        material_origin="feedstock",
    )

    ledger.move(
        "within_origin_tolerance",
        "reservoir.fo2_buffer",
        "terminal.offgas",
        {
            "O2": (
                0.5
                + shortfall_mol_atoms / 2.0
            )
            * resolve_species_formula("O2", {}).molar_mass_kg_per_mol()
        },
    )

    destination_origins = ledger.origin_atom_moles_by_account()["terminal.offgas"][
        "O"
    ]
    destination_unattributed = ledger.unresolved_origin_atom_moles_by_account()[
        "terminal.offgas"
    ]["O"]
    assert destination_origins["feedstock"] == pytest.approx(1.0)
    assert destination_unattributed == pytest.approx(shortfall_mol_atoms)
    assert math.fsum(destination_origins.values()) + destination_unattributed == pytest.approx(
        1.0 + shortfall_mol_atoms
    )


def test_origin_withdrawal_beyond_ledger_atom_tolerance_raises_typed() -> None:
    atom_tolerance_mol = 1.0e-6
    ledger = AtomLedger(
        account_policies=(
            AccountPolicy.reservoir(
                "reservoir.fo2_buffer",
                credit_limit_kg_by_species={"O2": 1.0},
            ),
        ),
        atom_tolerance_mol=atom_tolerance_mol,
    )
    ledger.load_external_mol(
        "reservoir.fo2_buffer",
        {"O2": 0.5},
        source="feedstock oxygen buffer",
        material_origin="feedstock",
    )

    with pytest.raises(OverdraftError) as exc_info:
        ledger.move(
            "beyond_origin_tolerance",
            "reservoir.fo2_buffer",
            "terminal.offgas",
            {
                "O2": (
                    0.5
                    + 2.0 * atom_tolerance_mol / 2.0
                )
                * resolve_species_formula("O2", {}).molar_mass_kg_per_mol()
            },
        )

    assert isinstance(exc_info.value.__cause__, PoolWithdrawalError)


def test_pool_allocator_extreme_finite_inputs_remain_finite() -> None:
    allocation = allocate_pool_withdrawal(
        {"cleanup": 5.0e307, "main": 5.0e307},
        1.0e308,
    )

    assert allocation == pytest.approx(
        {"cleanup": 5.0e307, "main": 5.0e307}
    )
    assert all(math.isfinite(share) for share in allocation.values())
    assert math.fsum(allocation.values()) == pytest.approx(1.0e308)


@pytest.mark.parametrize(
    ("balances", "withdrawal"),
    (
        ({"cleanup": 1.0e308, "main": 1.0e308}, 1.0e308),
        ({"cleanup": 10**10000}, 1.0),
    ),
)
def test_pool_allocator_extreme_overflow_raises_typed(
    balances: dict[str, float],
    withdrawal: float,
) -> None:
    with pytest.raises(PoolWithdrawalError):
        allocate_pool_withdrawal(balances, withdrawal)


@pytest.mark.parametrize(
    ("balances", "withdrawal", "kwargs"),
    (
        ({"feedstock": "bad"}, 0.5, {}),
        ({"feedstock": float("nan")}, 0.5, {}),
        ({"feedstock": -1.0}, 0.5, {}),
        ({"feedstock": 1.0}, "bad", {}),
        ({"feedstock": 1.0}, float("nan"), {}),
        ({"feedstock": 1.0}, float("inf"), {}),
        ({"feedstock": 1.0}, -1.0, {}),
        ({"feedstock": 1.0}, 0.5, {"absolute_tolerance": "bad"}),
        ({"feedstock": 1.0}, 2.0, {"absolute_tolerance": float("nan")}),
        ({"feedstock": 1.0}, 2.0, {"absolute_tolerance": float("inf")}),
        ({"feedstock": 1.0}, 0.5, {"absolute_tolerance": -1.0}),
    ),
)
def test_pool_allocator_rejects_invalid_arguments_typed(
    balances: dict[str, float],
    withdrawal: float,
    kwargs: dict[str, float],
) -> None:
    with pytest.raises(PoolWithdrawalError):
        allocate_pool_withdrawal(balances, withdrawal, **kwargs)


def test_ledger_pool_overdraw_preserves_gross_counters() -> None:
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.pool",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    before = ledger.gross_account_flows()

    with pytest.raises(OverdraftError) as exc_info:
        ledger.move(
            "overdraw",
            "process.pool",
            "terminal.offgas",
            {
                "Na": (
                    1.1
                    * resolve_species_formula(
                        "Na",
                        {},
                    ).molar_mass_kg_per_mol()
                )
            },
        )

    assert isinstance(exc_info.value.__cause__, PoolWithdrawalError)
    assert ledger.gross_account_flows() == before


def test_hour_rollback_snapshot_owns_gross_and_cumulative_state() -> None:
    from simulator.run_executor import _snapshot_atom_ledger

    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    snapshot = _snapshot_atom_ledger(ledger)
    snapshot_flows = snapshot.gross_account_flows()
    snapshot_events = snapshot.gross_account_flow_events()
    snapshot_unattributed = (
        snapshot.cumulative_origin_unattributed_atom_moles()
    )

    ledger.move(
        "later_mutation",
        "process.raw_feedstock",
        "process.condensation_train",
        {
            "Na": (
                1.0
                + 0.5 * ledger.balance_relative_tolerance
            )
        },
    )

    assert (
        ledger.cumulative_origin_unattributed_atom_moles()
        != snapshot_unattributed
    )
    assert snapshot.gross_account_flows() == snapshot_flows
    assert snapshot.gross_account_flow_events() == snapshot_events
    assert (
        snapshot.cumulative_origin_unattributed_atom_moles()
        == snapshot_unattributed
    )


def _refusal_sequence_session(
    refusal_count: int,
):
    from simulator.campaigns import CampaignHoldTargetRefusal
    from simulator.core import PyrolysisSimulator
    from simulator.session import SimSession

    ledger = AtomLedger()
    ledger.load_external(
        "process.raw_feedstock",
        {"Na": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    sim = object.__new__(PyrolysisSimulator)
    sim._poisoned_hour = None
    sim._pending_shuttle_bakeout_cycle_increment = ""
    sim._chem_registry = {}
    sim._chem_kernel = object()
    sim._build_chemistry_kernel = lambda: object()
    sim.atom_ledger = ledger
    sim.melt = SimpleNamespace(hour=0)
    sim.record = SimpleNamespace(snapshots=[])
    sim.pending_decision = None
    sim.is_complete = lambda: False
    attempts = {"remaining_refusals": refusal_count}

    def step_one_hour():
        active_ledger = sim.atom_ledger
        active_ledger.move(
            "hourly_pool_capture",
            "process.raw_feedstock",
            "process.condensation_train",
            {
                "Na": (
                    1.0
                    + 0.5 * active_ledger.balance_relative_tolerance
                )
            },
            amalgamated_pool=True,
        )
        active_ledger.load_external_mol(
            "process.reagent_added",
            {"O": 1.0},
            source="hourly reagent",
            material_origin="reagent",
        )
        if attempts["remaining_refusals"]:
            attempts["remaining_refusals"] -= 1
            raise CampaignHoldTargetRefusal(
                {"detail": "synthetic C6 terminal refusal"}
            )
        sim.melt.hour += 1
        return SimpleNamespace(hour=sim.melt.hour)

    sim._step_one_hour = step_one_hour
    session = SimSession()
    session._sim = sim
    session._build_per_hour_summary = lambda _sim, _snapshot: {}
    return session, sim


def test_session_multi_refusal_then_continue_matches_unattempted_hours() -> None:
    from simulator.accounting.ledger import snapshot_atom_ledger
    from simulator.campaigns import CampaignHoldTargetRefusal

    session, sim = _refusal_sequence_session(2)
    initial_ledger = snapshot_atom_ledger(sim.atom_ledger)

    for _ in range(2):
        with pytest.raises(CampaignHoldTargetRefusal):
            session.advance()
        assert sim.atom_ledger.__dict__ == initial_ledger.__dict__

    session.advance()
    baseline_session, baseline_sim = _refusal_sequence_session(0)
    baseline_session.advance()

    assert sim.atom_ledger.__dict__ == baseline_sim.atom_ledger.__dict__
    assert len(sim.atom_ledger.transitions) == 1
    assert len(sim.atom_ledger.gross_account_flow_events()) == 4


def test_run_executor_refusal_rollback_owns_all_ledger_state() -> None:
    from simulator.accounting.ledger import snapshot_atom_ledger
    from simulator.campaigns import CampaignHoldTargetRefusal
    from simulator.run_executor import RunExecutor

    session, sim = _refusal_sequence_session(0)
    initial_ledger = snapshot_atom_ledger(sim.atom_ledger)

    def refuse_directly():
        active_ledger = sim.atom_ledger
        active_ledger.move(
            "outer_snapshot_pool_capture",
            "process.raw_feedstock",
            "process.condensation_train",
            {"Na": 1.0},
            amalgamated_pool=True,
        )
        active_ledger.load_external_mol(
            "process.reagent_added",
            {"O": 1.0},
            source="outer snapshot reagent",
            material_origin="reagent",
        )
        raise CampaignHoldTargetRefusal(
            {"detail": "synthetic executor terminal refusal"}
        )

    session.advance = refuse_directly
    execution = RunExecutor().execute_session(session, hours=1)

    assert execution.status == "refused"
    assert sim.atom_ledger.__dict__ == initial_ledger.__dict__


def test_yield_disposition_state_persists_within_run_and_resets_between_runs() -> None:
    from pathlib import Path

    import yaml

    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import InternalAnalyticalBackend

    data_dir = Path(__file__).parent.parent / "data"
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        yaml.safe_load((data_dir / "setpoints.yaml").read_text()),
        yaml.safe_load((data_dir / "feedstocks.yaml").read_text()),
        yaml.safe_load((data_dir / "vapor_pressures.yaml").read_text()),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    first_gross = sim.atom_ledger.gross_account_flows()
    first_events = sim.atom_ledger.gross_account_flow_events()
    first_unattributed = (
        sim.atom_ledger.cumulative_origin_unattributed_atom_moles()
    )
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Na": 1.0},
        source="first-run flow",
        material_origin="feedstock",
    )
    sim.atom_ledger.move(
        "first_run_unattributed_dust",
        "process.condensation_train",
        "terminal.offgas",
        {
            "Na": (
                1.0
                + 0.5 * sim.atom_ledger.balance_relative_tolerance
            )
        },
    )
    assert sim.atom_ledger.gross_account_flows() != first_gross
    assert sim.atom_ledger.gross_account_flow_events() != first_events
    assert (
        sim.atom_ledger.cumulative_origin_unattributed_atom_moles()
        != first_unattributed
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=0, campaign="C0", campaign_hour=0),
    )
    capture_ledger_snapshot(
        sim,
        SimpleNamespace(hour=1, campaign="C2A", campaign_hour=0),
    )
    assert len(ledger_snapshots_from_sim(sim)) == 2

    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)

    assert ledger_snapshots_from_sim(sim) == ()
    assert sim.atom_ledger.gross_account_flows() == first_gross
    assert sim.atom_ledger.gross_account_flow_events() == first_events
    assert (
        sim.atom_ledger.cumulative_origin_unattributed_atom_moles()
        == first_unattributed
    )


def test_native_metal_taps_remain_distinct_from_retained_metal_phase() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.metal_phase_bottom_pool",
        {"Fe": 1.0},
        source="feedstock bottom pool",
        material_origin="feedstock",
    )
    ledger.load_external(
        "process.metal_phase_float_layer",
        {"Si": 0.5},
        source="feedstock float layer",
        material_origin="feedstock",
    )
    ledger.load_external(
        "terminal.drain_tap_material",
        {"Fe": 0.25},
        source="feedstock drain tap",
        material_origin="feedstock",
    )

    payload = build_yield_disposition(_sim(ledger))

    fe = _row(payload, "Fe")["destination_fractions"]
    assert fe["metal_phase_retained"] == pytest.approx(0.8)
    assert fe["product_tapped"] == pytest.approx(0.2)
    si = _row(payload, "Si")["destination_fractions"]
    assert si["metal_phase_retained"] == pytest.approx(1.0)
    streams = payload["terminal_species_streams"]
    assert any(
        row["account"] == "process.metal_phase_bottom_pool"
        and row["species"] == "Fe"
        for row in streams
    )
    assert any(
        row["account"] == "process.metal_phase_float_layer"
        and row["species"] == "Si"
        for row in streams
    )


def test_known_metal_staging_and_condensation_holdup_accounts_are_mapped() -> None:
    ledger = AtomLedger()
    ledger.load_external(
        "process.metal_phase",
        {"Fe": 1.0},
        source="feedstock metal staging",
        material_origin="feedstock",
    )
    ledger.load_external(
        "process.condensation_retained_holdup",
        {"SiO": 1.0},
        source="feedstock condensation holdup",
        material_origin="feedstock",
    )

    payload = build_yield_disposition(_sim(ledger))

    assert _row(payload, "Fe")["destination_fractions"][
        "metal_phase_retained"
    ] == pytest.approx(1.0)
    for element in ("Si", "O"):
        assert _row(payload, element)["destination_fractions"][
            "overhead_terminal_inventory"
        ] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "account",
    ("process.future_mystery", "reservoir.reagent.future"),
)
def test_unknown_nonzero_terminal_account_fails_closed(account: str) -> None:
    ledger = AtomLedger()
    ledger.load_external(
        account,
        {"Fe": 2.0e-12},
        source="feedstock",
        material_origin="feedstock",
    )

    with pytest.raises(
        YieldDispositionError,
        match="unknown nonzero terminal account",
    ):
        build_yield_disposition(_sim(ledger))


@pytest.mark.parametrize(
    ("account", "species"),
    (
        ("reservoir.reagent.C", "C"),
        ("reservoir.reagent.K", "K"),
        ("reservoir.reagent.Mg", "Mg"),
        ("reservoir.reagent.Na", "Na"),
        ("process.c7_al_credit", "Al"),
    ),
)
def test_explicit_terminal_reagent_accounts_reconcile_outside_feedstock_yield(
    account: str,
    species: str,
) -> None:
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.raw_feedstock",
        {"Fe": 1.0},
        source="feedstock",
        material_origin="feedstock",
    )
    ledger.load_external_mol(
        account,
        {species: 1.0},
        source="reagent",
        material_origin="reagent",
    )

    payload = build_yield_disposition(_sim(ledger))

    assert [row["element"] for row in payload["fraction_table"]["rows"]] == ["Fe"]
    reagent_row = next(
        row
        for row in payload["reagent_cycle"]["rows"]
        if row["element"] == species
    )
    assert reagent_row["input_mol_atoms"] == pytest.approx(
        reagent_row["terminal_excluded_mol_atoms"]
    )
    assert reagent_row["closure_residual_fraction"] == pytest.approx(0.0)
    assert any(
        row["account"] == account
        and row["destination"] == "charge_unprocessed"
        and row["species"] == species
        for row in payload["terminal_species_streams"]
    )
