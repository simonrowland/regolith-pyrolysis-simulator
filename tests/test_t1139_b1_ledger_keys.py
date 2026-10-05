"""t1139 request parents use the feedstock bridge's ledger key."""

from __future__ import annotations

from types import SimpleNamespace

from simulator.trace_oxide_parents import LIQUID_PARENT_OXIDE, ledger_component_key
from simulator.vapour_rail.request import emit_request_rules


def _compiled(species_id: str, formula: str, *, account: str, family_id: str):
    return SimpleNamespace(
        code_metadata=SimpleNamespace(
            source_account=account,
            solve_group_id=family_id,
            hot_train_applicability="not_applicable",
            request_rule="dormant_pending_validation",
            formula_id=species_id,
        ),
        vaporisation_coefficients=SimpleNamespace(
            evaporation_alpha={"status": "no_data"}
        ),
        fiat_routing=SimpleNamespace(
            process_or_terminal_destination="process.condensation_train",
            engineering_capture_policy="diagnostic_only",
        ),
        evaluator=None,
        validation_status=SimpleNamespace(value="pending_validation"),
        validation_anchor_refs=(),
        formula=formula,
        source_reaction_id=f"{species_id}_reaction",
        source_reaction_activity=None,
        family_id=family_id,
    )


def _payload(species: dict) -> dict:
    return {
        "families": {
            "family": {
                "code_metadata": {},
                "physical_properties": {"species": species},
            }
        }
    }


def test_t1139_parents_are_the_bridge_ledger_keys() -> None:
    ga_parent = LIQUID_PARENT_OXIDE["Ga"]
    species = {
        "Ga": {
            "chemical_family": "t1139_generated_carrier",
            "parent_oxide": ga_parent,
            "formula": "Ga",
            "source_reactions": [
                {
                    "id": "Ga_reaction",
                    "reactants": [
                        {"formula": f"{ga_parent}(l)", "stoichiometry": 0.5},
                        {"formula": "O2(g)", "stoichiometry": 0.0},
                    ],
                    "products": [{"formula": "Ga(g)", "stoichiometry": 1.0}],
                }
            ],
        },
        "NaBO": {
            "chemical_family": "t1139_generated_carrier",
            "parent_oxide": "Na2O",
            "formula": "NaBO",
            "source_reactions": [
                {
                    "id": "NaBO_reaction",
                    "reactants": [
                        {"formula": "Na2O(l)", "stoichiometry": 0.5},
                        {"formula": "B2O3(l)", "stoichiometry": 0.5},
                    ],
                    "products": [
                        {"formula": "NaBO(g)", "stoichiometry": 1.0},
                        {"formula": "O2(g)", "stoichiometry": 0.5},
                    ],
                }
            ],
        },
        "K": {
            "chemical_family": "alkali_metal",
            "parent_oxide": "K2O",
            "formula": "K",
            "source_reactions": [
                {
                    "id": "K_reaction",
                    "reactants": [{"formula": "K2O(l)", "stoichiometry": 0.5}],
                    "products": [{"formula": "K(g)", "stoichiometry": 1.0}],
                }
            ],
        },
    }
    rules = {
        rule.species_id: rule
        for rule in emit_request_rules(
            catalog_species={
                "Ga": _compiled(
                    "Ga",
                    "Ga",
                    account="process.t1139_generated_pure_condensed",
                    family_id="family",
                ),
                "NaBO": _compiled(
                    "NaBO",
                    "NaBO",
                    account="process.t1139_generated_pure_condensed",
                    family_id="family",
                ),
                "K": _compiled(
                    "K",
                    "K",
                    account="process.cleaned_melt",
                    family_id="family",
                ),
            },
            u0_manifest={"species": []},
            catalog_payload=_payload(species),
        )
    }
    assert rules["Ga"].parent_species_ids == frozenset(
        {ledger_component_key(ga_parent)}
    )
    assert "O2" not in rules["Ga"].parent_species_ids
    assert rules["NaBO"].parent_species_ids == frozenset(
        {ledger_component_key("Na2O(l)"), ledger_component_key("B2O3(l)")}
    )
    assert "K2O(l)" in rules["K"].parent_species_ids
    assert "K2O" in rules["K"].parent_species_ids
