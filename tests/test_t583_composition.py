"""Conformance gates for corrected t-583 carrier composition."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.channels import (
    CHANNEL_Br2,
    CHANNEL_Cl2,
    CHANNEL_F2,
    CHANNEL_H2,
    CHANNEL_I2,
    ChannelCompositionRefusal,
    REFUSAL_CARBON_SIDE_OWNER_MISSING,
    attempt_channel_composition,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STRICT = "COMPOSABLE-NOW-STRICT"
CLIPPED = "COMPOSABLE-NOW-CLIPPED"


def _payload(name: str) -> Mapping:
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


def _raw_status_rows(payload: Mapping) -> dict[str, tuple[Mapping, Mapping]]:
    rows = {}
    for family in payload["families"].values():
        metadata = family.get("code_metadata", {})
        if metadata.get("t583_status_only_composed") is not True:
            continue
        for species_id, row in family["physical_properties"]["species"].items():
            rows[str(species_id)] = (row, metadata)
    return rows


def _coverage(row: Mapping) -> Mapping:
    nested = row.get("t583_composition")
    return nested if isinstance(nested, Mapping) else row


def _strip_phase(formula: str) -> str:
    return re.sub(r"\([^)]*\)$", "", formula)


def test_all_878_needs_channel_rows_execute_typed_refusal() -> None:
    entries = _payload("vapour_rail_coverage_gaps.yaml")["entries"]
    needs = [row for row in entries if str(row.get("missing", "")).startswith("NEEDS-CHANNEL")]
    assert len(needs) == 878
    for row in needs:
        refusal = attempt_channel_composition(
            carrier=str(row["carrier"]),
            element=str(row["element"]),
            missing_text=str(row["missing"]),
        )
        assert isinstance(refusal, ChannelCompositionRefusal), row
        assert refusal.ledger_missing == row["missing"]
        if refusal.disposition != REFUSAL_CARBON_SIDE_OWNER_MISSING:
            assert refusal.missing_channels, row
            assert all(channel in refusal.detail for channel in refusal.missing_channels)
        assert all(owner in refusal.detail for owner in refusal.missing_melt_owners)


def test_ba_pilot_three_way_split_and_owner_specific_refusals() -> None:
    payload = _payload("vapor_pressures.yaml")
    catalog = compile_vapour_rail_catalog(payload)
    rows = _raw_status_rows(payload)
    strict = {"BaCl2", "BaO2H2", "BaS"}
    clipped = {"Ba", "Ba2", "BaO"}
    assert {species_id for species_id in rows if species_id.startswith("Ba")} == strict | clipped
    assert all(_coverage(rows[species_id][0])["coverage_tier"] == STRICT for species_id in strict)
    assert all(_coverage(rows[species_id][0])["coverage_tier"] == CLIPPED for species_id in clipped)
    assert all(catalog.species[species_id].valid_temperature_K == (1300.0, 2246.0) for species_id in clipped)
    assert all(
        math.isclose(float(_coverage(rows[species_id][0])["coverage_overlap_fraction"]), 0.946)
        for species_id in clipped
    )

    entries = _payload("vapour_rail_coverage_gaps.yaml")["entries"]
    ba_needs = {
        str(row["carrier"]): row
        for row in entries
        if row.get("element") == "Ba"
        and str(row.get("missing", "")).startswith("NEEDS-CHANNEL")
    }
    expected = {
        "BaBr": (CHANNEL_Br2, "halide_reservoir_owner_missing"),
        "BaBr2": (CHANNEL_Br2, "halide_reservoir_owner_missing"),
        "BaCl": (CHANNEL_Cl2, "halide_reservoir_owner_missing"),
        "BaF": (CHANNEL_F2, "halide_reservoir_owner_missing"),
        "BaF2": (CHANNEL_F2, "halide_reservoir_owner_missing"),
        "BaI": (CHANNEL_I2, "halide_reservoir_owner_missing"),
        "BaI2": (CHANNEL_I2, "halide_reservoir_owner_missing"),
        "BaH": (CHANNEL_H2, "hydrogen_reservoir_owner_missing"),
        "BaOH": (CHANNEL_H2, "hydrogen_reservoir_owner_missing"),
    }
    assert set(ba_needs) == set(expected)
    for carrier, (channel, owner) in expected.items():
        row = ba_needs[carrier]
        refusal = attempt_channel_composition(
            carrier=carrier,
            element="Ba",
            missing_text=str(row["missing"]),
        )
        assert isinstance(refusal, ChannelCompositionRefusal)
        assert refusal.missing_channels == (channel,)
        assert owner in refusal.missing_melt_owners
        assert channel in refusal.detail and owner in refusal.detail


def test_corrected_composable_rows_are_status_only_and_stoichiometric() -> None:
    gaps = _payload("vapour_rail_coverage_gaps.yaml")
    assert not [
        row
        for row in gaps["entries"]
        if str(row.get("missing", "")).startswith((STRICT, CLIPPED))
    ]

    payload = _payload("vapor_pressures.yaml")
    catalog = compile_vapour_rail_catalog(payload)
    rows = _raw_status_rows(payload)
    assert len(rows) == 151
    tiers = Counter(str(_coverage(row)["coverage_tier"]) for row, _metadata in rows.values())
    assert tiers == Counter({STRICT: 109, CLIPPED: 42})
    assert sum(int(_coverage(row)["coverage_ledger_pair_count"]) for row, _ in rows.values()) == 250
    assert catalog.species["H2S"].code_metadata.raw[
        "t583_existing_executable_composed"
    ] == {
        "status": "existing_evaluator_wiring_receipt",
        "coverage_tier": STRICT,
        "coverage_elements": ["S"],
        "coverage_ledger_pair_count": 1,
    }

    for species_id, (row, metadata) in rows.items():
        species = catalog.species[species_id]
        model = row["pressure_models"][0]
        alpha = species.vaporisation_coefficients.evaporation_alpha
        assert species.evaluator is not None
        assert _coverage(row)["flux_dormant"] is True
        if row.get("chemical_family") == "t583_composed_carrier":
            assert metadata["compatibility_projection"] == "t583_status_only_carriers"
        assert metadata["hot_train_applicability"] == "not_applicable"
        assert alpha["status"] == "no_data"
        assert alpha["policy"] == "refuse_nonzero_flux"

        reaction_id = model.get("source_reaction_id")
        if reaction_id is None:
            assert species.evaluator.pO2_exponent == 0.0
            continue
        reaction = next(item for item in row["source_reactions"] if item["id"] == reaction_id)
        signed = {}
        for side, sign in (("reactants", -1.0), ("products", 1.0)):
            for participant in reaction[side]:
                formula = str(participant["formula"])
                signed[formula] = signed.get(formula, 0.0) + sign * float(
                    participant["stoichiometry"]
                )
        target_nu = sum(
            nu
            for formula, nu in signed.items()
            if nu > 0.0 and _strip_phase(formula) == str(row["formula"])
        )
        assert target_nu > 0.0, species_id
        expected = -signed.get("O2", 0.0) / target_nu
        assert species.evaluator.pO2_exponent == pytest.approx(expected, abs=1.0e-12)
        if model["evaluator_family"] in {"nasa_cea_7", "nasa_cea_9", "shomate"}:
            assert "pO2_exponent" not in model
