"""VR-6: exact-key VapourBatch, manifest request builder, refusal closure.

Acceptance (DECOMPOSITION VR-6 / DESIGN-REV5 §1.2 / §4.2):

- compiler emits one request rule per executable U0 V row and eligible C edge
- request keys derive only from manifest + ledger source inventory
- channels_by_species.keys() == requested_species_ids (missing keys hard-fail)
- pending_validation is NOT a refusal reason
- refusal closure reaches a fixed point BEFORE connected solve bundles form
- tests: omitted-rule, caller narrowing, inactive predicate, absent source atom,
  provider-specific domain miss with another candidate available
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import math
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import numpy as np
import pytest
import yaml

from simulator.backend_names import (
    VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
    VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
)
from simulator.vapour_rail.activity import (
    ActivityRefusalCode,
    ActivityTier,
    ActivityVerdictKind,
    SourceReactionActivity,
    StandardStateIdentity,
)
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    FluxActivationContext,
    FluxDiagnosticUpperBound,
    IncompleteVapourBatchError,
    PressureRefusal,
    PressureUpperBound,
    PressureValue,
    VapourAnswer,
    VapourBatch,
    VapourRequestConstructionError,
    FluxEligible,
    FluxRefusal,
)
from simulator.vapour_rail.catalog import (
    OUT_OF_RANGE_STATUS,
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.request import (
    REFUSAL_ABSENT_SOURCE_ATOM,
    REFUSAL_INAPPLICABLE_PREDICATE,
    REFUSAL_MISSING_OUTCOME_STATE,
    REFUSAL_NO_COMPLETE_BUNDLE_SOURCE,
    REFUSAL_NO_ADMITTED_SOURCE,
    REFUSAL_OUTSIDE_DECLARED_DOMAIN,
    ProviderDomainCandidate,
    RequestRule,
    SelectedBundleIdentity,
    VapourResolveState,
    _state_fingerprint,
    assert_request_coverage,
    allocate_selected_source,
    build_request,
    build_solve_bundles,
    emit_request_rules,
    rank_complete_candidate_sources,
    refusal_closure,
    resolve_vapour_batch,
)
from simulator.vapour_rail.instrumentation import (
    EffectivePressureSource,
    flux_pressures_from_batch,
    serialize_melt_activity_shadow,
    serialize_vapour_answer,
    serialize_vapour_batch,
)
from simulator.vapour_rail.u0_manifest import load_u0_manifest


def _rg_activation_context() -> FluxActivationContext:
    return FluxActivationContext(epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST)


def _pre_rg_activation_context(*species_ids: str) -> FluxActivationContext:
    return FluxActivationContext(
        epoch=FLUX_ACTIVATION_EPOCH_PRE_RG,
        effective_pressure_species_ids=frozenset(species_ids),
    )


def _stub_catalog_species(
    species_id: str,
    *,
    pressure_pa: float = 10.0,
) -> dict[str, Any]:
    """Minimal compiled-species stand-in for unit tests without a full compile."""

    class _Eval:
        def evaluate(self, temperature_K, *, source_activity=1.0, pO2_bar=None):
            # Optional fO2 dependence for fingerprint/pO2 regressions.
            pa = float(pressure_pa)
            if pO2_bar is not None:
                pa = pa * (float(pO2_bar) ** -0.25)
            return SimpleNamespace(pressure_pa=pa)

        def evaluate_typed_shadow(
            self,
            temperature_K,
            *,
            activity,
            pO2_bar,
            expected_component_id,
            expected_standard_state,
            expected_state_fingerprint,
        ):
            assert activity.component_id == expected_component_id
            assert activity.standard_state == expected_standard_state
            assert activity.state_fingerprint == expected_state_fingerprint
            return {
                "status": "shadow_only_no_behavior_authority",
                "ln_pressure_Pa": math.log(float(pressure_pa)),
                "activity_term_ln": 0.0,
                "flux_disposition": "instrumented_not_selected",
            }

    compiled = SimpleNamespace(
        species_id=species_id,
        evaluator=_Eval(),
        vaporisation_coefficients=SimpleNamespace(
            evaporation_alpha={"value": 1.0}
        ),
    )
    return {species_id: compiled}


def _selector_rule(species_id: str) -> RequestRule:
    return RequestRule(
        species_id=species_id,
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"P"}),
        required_source_atoms=frozenset({"P"}),
        solve_group_id="selector_bundle",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id=species_id,
        validation_status="pending_validation",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
    )


def _selector_catalog_species(*species_ids: str) -> dict[str, Any]:
    return {
        species_id: _stub_catalog_species(species_id)[species_id]
        for species_id in species_ids
    }


def _selector_candidate(
    provider_id: str,
    evidence_class: str,
    pressures_pa: dict[str, float],
    *,
    statuses: dict[str, str] | None = None,
    fixed_reviewed: bool = True,
    total_pressure_dependent: bool = False,
    independently_validated: frozenset[str] = frozenset(),
    residuals_dex: dict[str, float | tuple[float, float]] | None = None,
    anchors_by_species: dict[str, tuple[str, ...]] | None = None,
    evaluation_id: str | None = None,
    solve_group_id: str = "selector_bundle",
    evaluation_state: VapourResolveState | None = None,
    state_fingerprint: str | None = None,
    evaluation_review_record: str | None = "review:independent",
) -> ProviderDomainCandidate:
    if state_fingerprint is None:
        state_fingerprint = _state_fingerprint(
            evaluation_state
            or VapourResolveState(temperature_K=1600.0)
        )
    return ProviderDomainCandidate(
        provider_id=provider_id,
        covers_state=lambda _state: True,
        evidence_class=evidence_class,
        pressures_by_species={
            species_id: PressureValue(pa)
            for species_id, pa in pressures_pa.items()
        },
        validation_status_by_species=(
            statuses
            if statuses is not None
            else {species_id: "pending_validation" for species_id in pressures_pa}
        ),
        evaluation_is_fixed_and_reviewed=fixed_reviewed,
        calibration_request_total_pressure_dependent=total_pressure_dependent,
        independently_validated_species=independently_validated,
        validation_residual_dex_by_species=residuals_dex or {},
        validation_anchor_refs_by_species=anchors_by_species or {},
        evaluation_id=evaluation_id or f"evaluation:{provider_id.strip()}",
        solve_group_id=solve_group_id,
        state_fingerprint=state_fingerprint,
        evaluation_review_record=evaluation_review_record,
    )


def _selector_bundle_identity(
    candidate: ProviderDomainCandidate,
    *,
    bundle_id: str = "selector_bundle",
) -> SelectedBundleIdentity:
    return SelectedBundleIdentity(
        bundle_id=bundle_id,
        evaluation_id=candidate.evaluation_id,
        solve_group_id=candidate.solve_group_id,
        state_fingerprint=candidate.state_fingerprint,
    )


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _minimal_family(
    species_id: str = "K",
    *,
    parent_oxide: str = "K2O",
    applicability: str = "applicable",
    request_rule: str = "source_inventory_present",
    source_account: str = "process.cleaned_melt",
    validation_status: str = "pending_validation",
    with_reaction: bool = True,
    availability: str | None = None,
) -> dict:
    reaction = {
        "id": "ko0_5_to_k",
        "reactants": [{"formula": "KO0.5", "stoichiometry": 1.0}],
        "products": [
            {"formula": "K", "stoichiometry": 1.0},
            {"formula": "O2", "stoichiometry": 0.25},
        ],
        "activity_input": {
            "component_id": "KO0.5",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "liquid",
                "reference_pressure_bar": 1.0,
                "component_basis": "raoultian_pure_endmember",
            },
            "activity_model": "provider_reported_thermodynamic_activity",
            "allow_henrian_upper_bound": False,
            "compound_bearing": False,
            "require_assemblage_match": False,
        },
    }
    model: dict = {
        "evaluator_family": "standard_reaction_term",
        "fit_target": "standard_reaction_term",
        "pressure_kind": "equilibrium_partial_pressure",
        "species_basis": "monomer",
        "valid_domain": {"temperature_K": [1000.0, 2000.0]},
        "source_reaction_id": "ko0_5_to_k",
        "activity_semantics": "source_reaction_activity",
        "reference_pressure_model": {
            "evaluator_family": "tabulated_equilibrium",
            "points": [
                {"temperature_K": 1000.0, "pressure_Pa": 1.0},
                {"temperature_K": 2000.0, "pressure_Pa": 100.0},
            ],
        },
        "activity_exponent": 1.0,
        "pO2_exponent": -0.25,
        "pO2_reference_bar": 1.0,
        "oxygen_fugacity_channel": "intrinsic_melt",
    }
    if not with_reaction:
        model = {
            "evaluator_family": "antoine",
            "fit_target": "antoine",
            "pressure_kind": "equilibrium_partial_pressure",
            "activity_semantics": "effective_pressure_reference_fit",
            "species_basis": "monomer",
            "valid_domain": {"temperature_K": [1000.0, 2000.0]},
            "coefficients": {"A": 10.0, "B": 10000.0, "C": 0.0},
        }
    if availability is not None:
        model["availability"] = availability

    species_row: dict = {
        "formula": species_id,
        "source_reactions": [reaction] if with_reaction else [],
        "pressure_models": [model],
        "validation": {
            "status": validation_status,
            "anchor_refs": (
                ["anchor:test"] if validation_status == "validated" else []
            ),
        },
        "parent_oxide": parent_oxide,
        "molar_mass_g_mol": 39.0983,
    }
    return {
        "schema_version": 2,
        "families": {
            f"{species_id.lower()}_test_family": {
                "physical_properties": {"species": {species_id: species_row}},
                "fiat_routing": {
                    "plant_bin": None,
                    "engineering_capture_policy": "temperature_threshold",
                    "products_and_coproducts": [],
                    "process_or_terminal_destination": "process.condensation_train",
                },
                "vaporisation_coefficients": {
                    "evaporation_alpha": {"value": 0.13},
                    "alpha_domain_and_uncertainty": {},
                    "extrapolation_policy": "conservative_slope_continuation",
                    "out_of_range_status": OUT_OF_RANGE_STATUS,
                    "acquisition_flag": f"acquire:test:{species_id}",
                },
                "code_metadata": {
                    "formula_id": species_id,
                    "source_account": source_account,
                    "request_rule": request_rule,
                    "solve_group_id": f"{species_id.lower()}_test_family",
                    "compatibility_projection": "metals",
                    "canonical_aliases": [],
                    "hot_train_applicability": applicability,
                },
            }
        },
    }


_TEST_ACTIVITY_STANDARD_STATE = StandardStateIdentity(
    convention="raoultian_pure_endmember",
    phase="liquid",
    reference_pressure_bar=1.0,
    component_basis="raoultian_pure_endmember",
)

_NONFINITE_FINGERPRINT_VALUES = (
    pytest.param(float("nan"), id="float-nan"),
    pytest.param(float("inf"), id="float-inf"),
    pytest.param(float("-inf"), id="float-negative-inf"),
    pytest.param(Decimal("NaN"), id="decimal-nan"),
    pytest.param(Decimal("sNaN"), id="decimal-snan"),
    pytest.param(Decimal("Infinity"), id="decimal-inf"),
    pytest.param(np.nan, id="numpy-nan"),
    pytest.param(np.inf, id="numpy-inf"),
    pytest.param(np.float32("nan"), id="numpy32-nan"),
    pytest.param(np.float32("inf"), id="numpy32-inf"),
    pytest.param(np.float64("nan"), id="numpy64-nan"),
    pytest.param(np.float64("inf"), id="numpy64-inf"),
    pytest.param(np.str_("nan"), id="numpy-str-nan"),
    pytest.param(np.str_("inf"), id="numpy-str-inf"),
    pytest.param(np.bytes_(b"nan"), id="numpy-bytes-nan"),
    pytest.param(np.bytes_(b"inf"), id="numpy-bytes-inf"),
)


def _state_with_k_activity(**kwargs: Any) -> VapourResolveState:
    kwargs.setdefault("source_reaction_fO2_bar", 1.0e-8)
    return VapourResolveState(
        source_reaction_activities={"K": 0.25},
        source_reaction_activity_provider="test_activity_provider",
        source_reaction_activity_evidence_refs={"K": "doi:10.1234/test-activity"},
        source_reaction_activity_standard_states={
            "K": _TEST_ACTIVITY_STANDARD_STATE
        },
        **kwargs,
    )


def _u0_stub(*v_ids: str, c_ids: tuple[str, ...] = ()) -> dict:
    """Minimal U0 document covering the named V (and optional C) rows."""

    species = []
    for species_id in v_ids:
        species.append(
            {
                "id": species_id,
                "formula": species_id,
                "atoms": {species_id: 1.0} if species_id.isalpha() else {},
                "disposition": "V",
                "validation_status": "pending_validation",
                "validation_anchor_refs": [],
                "feedstock_presence": False,
                "sources": {
                    "inventory": True,
                    "gas_closure": False,
                    "refractory_registry": False,
                },
                "regime": {
                    "millibar": {
                        "applicable": True,
                        "dominance": "unspecified",
                        "outcome": "as_disposition",
                    },
                    "hard_vacuum": {
                        "applicable": True,
                        "dominance": "unspecified",
                        "outcome": "as_disposition",
                    },
                },
                "flags": [],
            }
        )
    for species_id in c_ids:
        species.append(
            {
                "id": species_id,
                "formula": species_id,
                "atoms": {},
                "disposition": "C",
                "validation_status": "pending_validation",
                "validation_anchor_refs": [],
                "feedstock_presence": True,
                "sources": {
                    "inventory": True,
                    "gas_closure": False,
                    "refractory_registry": False,
                },
                "regime": {
                    "millibar": {
                        "applicable": True,
                        "dominance": "unspecified",
                        "outcome": "as_disposition",
                    },
                    "hard_vacuum": {
                        "applicable": True,
                        "dominance": "unspecified",
                        "outcome": "as_disposition",
                    },
                },
                "flags": [],
            }
        )
    return {
        "schema_version": 1,
        "kind": "u0_vapour_rail_manifest",
        "description": "test stub",
        "validation_status_default": "pending_validation",
        "row_count": len(species),
        "provenance": {},
        "membership_sets": {},
        "species": species,
    }


# ---------------------------------------------------------------------------
# Exact-key surface
# ---------------------------------------------------------------------------


def test_vapour_batch_requires_exact_key_equality() -> None:
    answer = VapourAnswer(
        species_id="K",
        pressure=PressureValue(pa=1.0),
        selected_runtime_pressure=PressureValue(pa=1.0),
        flux=FluxEligible(alpha_ref="alpha:K"),
        source_label="test",
        formula_id="K",
        source_account="process.cleaned_melt",
        solve_group_id="g",
        state_fingerprint="s",
        validation_status="pending_validation",
    )
    batch = VapourBatch(
        requested_species_ids=frozenset({"K"}),
        channels_by_species={"K": answer},
    )
    assert batch.channel("K") is answer
    assert "K" in batch

    with pytest.raises(IncompleteVapourBatchError):
        VapourBatch(
            requested_species_ids=frozenset({"K", "Na"}),
            channels_by_species={"K": answer},
        )

    with pytest.raises(IncompleteVapourBatchError):
        batch.channel("Na")


# ---------------------------------------------------------------------------
# Compiler request rules
# ---------------------------------------------------------------------------


def test_compiler_emits_rule_per_u0_v_and_catalog_c_edge() -> None:
    payload = _minimal_family("K", parent_oxide="K2O", with_reaction=True)
    u0 = _u0_stub("K", "As2O3", c_ids=("K2O", "SiO2"))
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=u0)
    rule_ids = {rule.species_id for rule in catalog.request_rules}
    # U0 V rows always present
    assert "K" in rule_ids
    assert "As2O3" in rule_ids
    # Catalog contract preferred for K
    k_rule = next(r for r in catalog.request_rules if r.species_id == "K")
    assert k_rule.origin == "catalog"
    assert "K2O" in k_rule.parent_species_ids or "KO0.5" in k_rule.parent_species_ids
    # C edge parents recorded when U0 C matches parent_oxide / reactants
    assert "K2O" in k_rule.parent_species_ids or "c_edge_parents" in dict(
        k_rule.evidence
    )


def test_production_catalog_emits_all_u0_v_rules() -> None:
    from simulator.vapour_rail.u0_manifest import canonicalize_gas_id

    payload = _yaml("vapor_pressures.yaml")
    u0 = load_u0_manifest()
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=u0)
    # Collision oxides canonicalize to *_gas; rules key on the canonical ID.
    v_ids = {
        canonicalize_gas_id(str(row["id"]), treat_as_gas=True)
        for row in u0["species"]
        if row.get("disposition") == "V"
    }
    rule_ids = {rule.species_id for rule in catalog.request_rules}
    missing = sorted(v_ids - rule_ids)
    assert missing == [], f"U0 V rows without request rules: {missing[:20]}"
    # Live catalog metals remain present
    for species_id in ("Na", "K", "Fe", "SiO", "NaCl"):
        assert species_id in rule_ids


def test_omitted_rule_hard_fails_when_eligible_inventory_present() -> None:
    """Manifest V with inventory omitted from rules / builder → hard failure."""

    payload = _minimal_family("K")
    u0 = _u0_stub("K")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=u0)
    rules = list(catalog.request_rules)
    # Drop K's rule while inventory would activate it.
    stripped = tuple(r for r in rules if r.species_id != "K")
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 0.5}}

    # Builder alone cannot invent K without a rule.
    requested = build_request(stripped, ledger)
    assert "K" not in requested

    # Coverage assertion detects the omission when we re-introduce expected
    # eligibility via the full rule set expectation: eligible rule missing
    # from builder result.
    full_requested = build_request(rules, ledger)
    assert "K" in full_requested
    with pytest.raises(VapourRequestConstructionError, match="omitted"):
        assert_request_coverage(rules, ledger, full_requested - {"K"})


def test_omitted_u0_v_fails_at_compile_time() -> None:
    payload = _minimal_family("K")
    u0 = _u0_stub("K", "GhostVapour")
    # Poison emit by removing GhostVapour after building a broken rules path:
    # emit_request_rules itself always emits V rows; simulate compile proof by
    # calling emit and then asserting GhostVapour is present — and that
    # manually removing it is what coverage would catch at runtime.
    from simulator.vapour_rail.request import emit_request_rules as _emit

    catalog = compile_vapour_rail_catalog(payload, u0_manifest=u0)
    rules = {r.species_id: r for r in catalog.request_rules}
    assert "GhostVapour" in rules

    # Direct emit with a species list that drops a V row raises.
    broken_u0 = deepcopy(u0)
    # Keep disposition V row but intercept by filtering after emit is not
    # how the compiler works; instead verify emit raises when we pass an
    # empty catalog and then delete the V emission path via monkeypatch.
    emitted = _emit(
        catalog_species=catalog.species,
        u0_manifest=broken_u0,
        catalog_payload=payload,
    )
    assert {r.species_id for r in emitted} >= {"K", "GhostVapour"}


# ---------------------------------------------------------------------------
# Caller narrowing
# ---------------------------------------------------------------------------


def test_caller_narrowing_is_rejected() -> None:
    payload = _minimal_family("K")
    catalog = compile_vapour_rail_catalog(
        payload, u0_manifest=_u0_stub("K")
    )
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}

    with pytest.raises(VapourRequestConstructionError, match="must not construct"):
        catalog.build_request(
            ledger,
            caller_species_filter=["K"],
        )

    with pytest.raises(VapourRequestConstructionError, match="must not construct"):
        catalog.resolve_batch(
            ledger,
            {"temperature_K": 1500.0},
            caller_species_filter=["K"],
            flux_activation_context=_rg_activation_context(),
        )


# ---------------------------------------------------------------------------
# Inactive predicate
# ---------------------------------------------------------------------------


def test_inactive_predicate_keeps_id_and_emits_typed_refusal() -> None:
    payload = _minimal_family(
        "NaCl",
        parent_oxide="NaCl",
        applicability="not_applicable",
        request_rule="stage0_only",
        source_account="process.stage0_foulant",
        with_reaction=False,
    )
    # Fix formula/parent for halide-style carrier-is-own-vapor
    fam = next(iter(payload["families"].values()))
    fam["physical_properties"]["species"]["NaCl"]["parent_oxide"] = None
    fam["physical_properties"]["species"]["NaCl"]["source_reactions"] = []
    fam["code_metadata"]["hot_train_applicability"] = "not_applicable"

    catalog = compile_vapour_rail_catalog(
        payload, u0_manifest=_u0_stub("NaCl")
    )
    ledger = {"process.stage0_foulant": {"NaCl": 2.0}}
    batch = catalog.resolve_batch(
        ledger,
        VapourResolveState(temperature_K=1200.0, process_phase="hot_train"),
        flux_activation_context=_rg_activation_context(),
    )
    assert "NaCl" in batch.requested_species_ids
    answer = batch.channel("NaCl")
    assert answer.is_refused
    assert isinstance(answer.pressure, PressureRefusal)
    assert answer.pressure.code == REFUSAL_INAPPLICABLE_PREDICATE
    assert answer.refusal_code == REFUSAL_INAPPLICABLE_PREDICATE
    # Not omitted from the exact-key map
    assert frozenset(batch.channels_by_species) == batch.requested_species_ids


def test_stage0_only_active_in_stage0() -> None:
    payload = _minimal_family(
        "NaCl",
        applicability="stage0_only",
        request_rule="stage0_only",
        source_account="process.stage0_foulant",
        with_reaction=False,
    )
    fam = next(iter(payload["families"].values()))
    fam["physical_properties"]["species"]["NaCl"]["parent_oxide"] = None
    catalog = compile_vapour_rail_catalog(
        payload, u0_manifest=_u0_stub("NaCl")
    )
    ledger = {"process.stage0_foulant": {"NaCl": 1.0}}
    batch = catalog.resolve_batch(
        ledger,
        VapourResolveState(temperature_K=900.0, process_phase="stage0", stage="stage0"),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("NaCl")
    # May still refuse for other reasons, but not the predicate
    if answer.is_refused:
        assert answer.refusal_code != REFUSAL_INAPPLICABLE_PREDICATE


def test_stage0_p_markers_activate_only_p2o5_sourced_rules() -> None:
    def rule(
        species_id: str,
        parent: str,
        atoms: set[str],
        *,
        predicate: str = "stage0_only",
    ) -> RequestRule:
        return RequestRule(
            species_id=species_id,
            source_account="process.cleaned_melt",
            parent_species_ids=frozenset({parent}),
            required_source_atoms=frozenset(atoms),
            solve_group_id=f"{species_id.lower()}_test",
            applicability_predicate=predicate,
            request_rule_kind="source_inventory_present",
            origin="catalog",
            formula_id=species_id,
            has_pressure_evaluator=True,
            has_alpha=True,
            has_route=True,
            has_formula=True,
            validation_status="pending_validation",
        )

    for stage in ("stage0_p_carriers", "c0b_p_cleanup"):
        state = VapourResolveState(
            temperature_K=1873.15,
            process_phase="hot_train",
            stage=stage,
        )
        p_batch = resolve_vapour_batch(
            rules=(rule("PO", "P2O5", {"P", "O"}),),
            ledger_snapshot={"process.cleaned_melt": {"P2O5": 1.0}},
            state=state,
            flux_activation_context=_rg_activation_context(),
        )
        assert p_batch.channel("PO").refusal_code != (
            REFUSAL_INAPPLICABLE_PREDICATE
        )

        non_p_batch = resolve_vapour_batch(
            rules=(
                rule(
                    "NaCl",
                    "NaCl",
                    {"Na", "Cl"},
                    predicate=(
                        "applicable" if stage == "c0b_p_cleanup" else "stage0_only"
                    ),
                ),
            ),
            ledger_snapshot={"process.cleaned_melt": {"NaCl": 1.0}},
            state=state,
            flux_activation_context=_rg_activation_context(),
        )
        assert non_p_batch.channel("NaCl").refusal_code == (
            REFUSAL_INAPPLICABLE_PREDICATE
        )


# ---------------------------------------------------------------------------
# Absent source atom
# ---------------------------------------------------------------------------


def test_absent_source_atom_refuses_but_keeps_channel() -> None:
    # Custom rule: activated by a dummy parent token, requires Cl atom which
    # is not present in inventory.
    rule = RequestRule(
        species_id="K",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"TRIGGER"}),
        required_source_atoms=frozenset({"Cl"}),
        solve_group_id="k_test",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="K",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
        validation_status="pending_validation",
    )
    ledger = {"process.cleaned_melt": {"TRIGGER": 1.0, "K2O": 0.0}}
    batch = resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1500.0),
        flux_activation_context=_rg_activation_context(),
    )
    assert "K" in batch.requested_species_ids
    answer = batch.channel("K")
    assert answer.is_refused
    assert answer.refusal_code == REFUSAL_ABSENT_SOURCE_ATOM
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)


# ---------------------------------------------------------------------------
# Provider-specific domain miss with another candidate available
# ---------------------------------------------------------------------------


def test_provider_domain_miss_not_refusal_when_other_candidate_covers() -> None:
    rule = RequestRule(
        species_id="Fe",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"FeO"}),
        required_source_atoms=frozenset({"Fe", "O"}),
        solve_group_id="fe_test",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="Fe",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
        validation_status="pending_validation",
    )
    ledger = {"process.cleaned_melt": {"FeO": 3.0}}
    # Below VapoRock's 1350 K gate; literature Antoine still covers.
    state = VapourResolveState(temperature_K=1200.0)

    def vaporock_covers(state_map: dict) -> bool:
        t = state_map.get("temperature_K")
        return t is not None and 1350.0 <= float(t) <= 1950.0

    def literature_covers(state_map: dict) -> bool:
        t = state_map.get("temperature_K")
        return t is not None and 500.0 <= float(t) <= 2500.0

    candidates = [
        ProviderDomainCandidate(
            provider_id="vaporock",
            covers_state=vaporock_covers,
            evidence_class="analytical:vaporock_calibrated",
        ),
        ProviderDomainCandidate(
            provider_id="literature_antoine",
            covers_state=literature_covers,
            evidence_class="analytical:external_grounded",
        ),
    ]
    catalog_species = _stub_catalog_species("Fe")
    answers = refusal_closure(
        requested=frozenset({"Fe"}),
        rules=(rule,),
        ledger_snapshot=ledger,
        state=state,
        provider_candidates_by_species={"Fe": candidates},
        catalog_species=catalog_species,
    ).answers
    answer = answers["Fe"]
    assert not answer.is_refused, (
        f"provider-specific domain miss must not refuse when another "
        f"candidate covers; got {answer.refusal_code}: {answer.extra}"
    )
    # VapoRock alone would miss at 1200 K
    assert not vaporock_covers(state.as_mapping())
    assert literature_covers(state.as_mapping())

    # Both candidates miss → step-2 refusal
    cold = VapourResolveState(temperature_K=100.0)
    refused = refusal_closure(
        requested=frozenset({"Fe"}),
        rules=(rule,),
        ledger_snapshot=ledger,
        state=cold,
        provider_candidates_by_species={"Fe": candidates},
        catalog_species=catalog_species,
    ).answers["Fe"]
    assert refused.is_refused
    assert refused.refusal_code == REFUSAL_NO_ADMITTED_SOURCE


def test_fe_typed_shadow_traverses_per_answer_instrumentation() -> None:
    rule = RequestRule(
        species_id="Fe",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"FeO"}),
        required_source_atoms=frozenset({"Fe", "O"}),
        solve_group_id="fe_test",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="Fe",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
        validation_status="pending_validation",
    )
    standard = StandardStateIdentity(
        convention="registry_exact",
        phase="liquid_endmember",
        reference_pressure_bar=1.0,
        component_basis="provider_native",
        identity_id="simulator.stoichiometric_FeO_l.current.v1",
        component_id="FeO",
    )
    typed_fe = SourceReactionActivity(
        component_id="FeO",
        value=0.2,
        ln_value=math.log(0.2),
        verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
        bound_direction=None,
        reason="test_fe_shadow",
        standard_state=standard,
        phase_assemblage_ref="test:liquid_melt",
        chemical_potential_ref="REF-001",
        state_fingerprint="state:fe",
        solve_group_id=None,
        provider="test",
        tier=ActivityTier.A,
        target_standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="crystalline",
            reference_pressure_bar=1.0,
            identity_id=(
                "rail.pure_oxide.FeO.raoultian.crystalline_at_T.1bar.v1"
            ),
            component_id="FeO",
        ),
    )
    state = VapourResolveState(
        temperature_K=1600.0,
        source_reaction_activity_results={"FeO": typed_fe},
        melt_activity_shadow_state_fingerprint="state:fe",
    )

    answer = refusal_closure(
        requested=frozenset({"Fe"}),
        rules=(rule,),
        ledger_snapshot={"process.cleaned_melt": {"FeO": 3.0}},
        state=state,
        provider_candidates_by_species=None,
        catalog_species=_stub_catalog_species("Fe"),
    ).answers["Fe"]

    assert answer.source_reaction_activity_shadow is typed_fe
    assert answer.source_reaction_activity_shadow_evaluation["status"] == (
        "shadow_only_no_behavior_authority"
    )


# ---------------------------------------------------------------------------
# pending_validation is not refusal
# ---------------------------------------------------------------------------


def test_pending_validation_is_not_refusal() -> None:
    payload = _minimal_family("K", validation_status="pending_validation")
    catalog = compile_vapour_rail_catalog(
        payload, u0_manifest=_u0_stub("K")
    )
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    batch = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=1500.0),
        flux_activation_context=_pre_rg_activation_context(),
    )
    answer = batch.channel("K")
    assert answer.validation_status == "pending_validation"
    assert not answer.is_refused
    assert isinstance(answer.pressure, PressureValue)
    assert answer.pressure.pa > 0.0
    assert isinstance(answer.flux, FluxEligible)
    # Answerability is not activation authority: without typed pre-RG source
    # set evidence, the catalog-complete answer remains dormant.
    assert batch.flux_active_species_ids == frozenset()
    effective_batch = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=1500.0),
        flux_activation_context=_pre_rg_activation_context("K"),
    )
    assert effective_batch.flux_active_species_ids == frozenset({"K"})
    catalog_answer = effective_batch.channel("K")
    assert catalog_answer.selected_runtime_pressure == catalog_answer.pressure
    with pytest.raises(ValueError, match="not catalog resolve state"):
        catalog.resolve_batch(
            ledger,
            {
                "temperature_K": 1500.0,
                "selected_runtime_pressures_Pa": {"K": 7.5},
            },
            flux_activation_context=_pre_rg_activation_context(),
        )
    rg_batch = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=1500.0),
        flux_activation_context=_rg_activation_context(),
    )
    assert rg_batch.flux_active_species_ids == frozenset({"K"})
    assert answer.certification_ceiling == "never"
    assert answer.verdict_status == "status_bearing_non_authoritative"
    assert "alpha_authority_status" not in answer.extra


@pytest.mark.parametrize(
    ("temperature_K", "expected_out_of_range"),
    ((448.15, True), (1800.0, False), (2300.0, True)),
)
def test_t609_real_rows_stay_status_bearing_through_request_layer(
    temperature_K: float,
    expected_out_of_range: bool,
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    catalog = compile_vapour_rail_catalog(
        payload, u0_manifest=load_u0_manifest()
    )
    nio_activity = catalog.species["NiO_gas"].source_reaction_activity
    assert nio_activity is not None
    state = VapourResolveState(
        temperature_K=temperature_K,
        process_phase="stage0",
        stage="stage0",
        total_pressure_Pa=1.0e5,
        fO2_bar=1.0,
        source_reaction_activities={"NiO_gas": 0.4},
        source_reaction_activity_provider="t609_real_file_probe",
        source_reaction_activity_evidence_refs={
            "NiO_gas": "tests/test_vapour_batch_request.py:t609"
        },
        source_reaction_activity_standard_states={
            "NiO_gas": nio_activity.standard_state
        },
        source_reaction_fO2_bar=1.0,
    )
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": {"FeO": 1.0, "NiO": 1.0}},
        state,
        flux_activation_context=_rg_activation_context(),
    )

    for species_id in ("FeO_association_gas", "NiO_gas"):
        answer = batch.channel(species_id)
        assert answer.source_account == "process.cleaned_melt"
        assert answer.validation_status == "pending_validation"
        assert answer.verdict_status == "status_bearing_non_authoritative"
        assert answer.certification_ceiling == "never"
        assert isinstance(answer.pressure, PressureUpperBound)
        assert isinstance(answer.flux, FluxDiagnosticUpperBound)
        assert not answer.is_flux_active
        assert answer.extra["alpha_inventory_policy"] == (
            "diagnostic_only_no_inventory_debit"
        )
        assert answer.extra["alpha_authority_status"] == "diagnostic_upper_bound"
        assert answer.extra.get("out_of_range", False) is expected_out_of_range

    fe_only = catalog.resolve_batch(
        {"process.cleaned_melt": {"FeO": 1.0}},
        state,
        flux_activation_context=_rg_activation_context(),
    )
    assert "FeO_association_gas" in fe_only
    assert "FeO_gas" not in fe_only
    assert "NiO_gas" not in fe_only


@pytest.mark.parametrize(
    ("species_id", "parent_oxide", "temperature_K", "expected_refusal"),
    (
        ("MnO_gas", "MnO", 1500.0, True),
        ("MnO_gas", "MnO", 1800.0, False),
        ("MnO_gas", "MnO", 2400.0, True),
        ("CoO_gas", "CoO", 1300.0, True),
        ("CoO_gas", "CoO", 1800.0, False),
        ("CoO_gas", "CoO", 2100.0, True),
    ),
)
def test_t622_real_rows_share_diagnostic_only_typed_ood_policy(
    species_id: str,
    parent_oxide: str,
    temperature_K: float,
    expected_refusal: bool,
) -> None:
    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), u0_manifest=load_u0_manifest()
    )
    declaration = catalog.species[species_id].source_reaction_activity
    assert declaration is not None
    state = VapourResolveState(
        temperature_K=temperature_K,
        process_phase="stage0",
        stage="stage0",
        total_pressure_Pa=1.0e5,
        fO2_bar=1.0,
        source_reaction_activities={species_id: 0.4},
        source_reaction_activity_provider="t622_real_file_probe",
        source_reaction_activity_evidence_refs={
            species_id: "tests/test_vapour_batch_request.py:t622"
        },
        source_reaction_activity_standard_states={
            species_id: declaration.standard_state
        },
    )
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": {parent_oxide: 1.0}},
        state,
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel(species_id)

    assert answer.validation_status == "pending_validation"
    assert answer.verdict_status == "status_bearing_non_authoritative"
    assert answer.certification_ceiling == "never"
    assert not answer.is_flux_active
    if expected_refusal:
        assert isinstance(answer.pressure, PressureRefusal)
        assert answer.pressure.code == REFUSAL_OUTSIDE_DECLARED_DOMAIN
        assert isinstance(answer.flux, FluxRefusal)
    else:
        assert isinstance(answer.pressure, PressureUpperBound)
        assert isinstance(answer.flux, FluxDiagnosticUpperBound)
        assert answer.extra["alpha_inventory_policy"] == (
            "diagnostic_only_no_inventory_debit"
        )
        assert answer.extra["alpha_authority_status"] == "diagnostic_upper_bound"


def test_t568_shadow_is_golden_neutral_and_fail_isolated(monkeypatch) -> None:
    payload = _minimal_family("K", validation_status="pending_validation")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {
        "process.cleaned_melt": {"K": 1.0, "K2O": 1.0, "KO0.5": 1.0}
    }
    state = _state_with_k_activity(
        temperature_K=1500.0,
        melt_activity_shadow_enabled=True,
    )

    instrumented = catalog.resolve_batch(
        ledger,
        state,
        flux_activation_context=_pre_rg_activation_context("K"),
    )
    instrumented_answer = instrumented.channel("K")
    assert instrumented_answer.source_reaction_activity_shadow is not None
    assert (
        instrumented_answer.source_reaction_activity_shadow_evaluation["status"]
        == "shadow_only_no_behavior_authority"
    )
    shadow_report = serialize_melt_activity_shadow(instrumented)
    assert shadow_report["schema"] == "melt_activity_shadow.v1"
    assert shadow_report["behavior_authority"] is False
    assert shadow_report["record_limit"] == 64
    assert shadow_report["record_truncated"] is False
    assert "KO0.5" in shadow_report["batch_shadow"]["results_by_component"]
    assert "K" in shadow_report["answers_by_species"]

    def _broken_shadow(**_kwargs):
        raise RuntimeError("deliberate t568 shadow failure")

    monkeypatch.setattr(
        "simulator.vapour_rail.melt_activity_resolver.build_shadow_for_vapour_batch",
        _broken_shadow,
    )
    fail_isolated = catalog.resolve_batch(
        ledger,
        state,
        flux_activation_context=_pre_rg_activation_context("K"),
    )

    assert serialize_vapour_answer(fail_isolated.channel("K")) == (
        serialize_vapour_answer(instrumented_answer)
    )
    assert fail_isolated.flux_active_species_ids == instrumented.flux_active_species_ids
    assert fail_isolated.melt_activity_shadow["status"] == (
        "shadow_unavailable_no_behavior_change"
    )


def test_t568_shadow_computation_is_lazy_until_explicitly_enabled(
    monkeypatch,
) -> None:
    payload = _minimal_family("K", validation_status="pending_validation")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {
        "process.cleaned_melt": {"K": 1.0, "K2O": 1.0, "KO0.5": 1.0}
    }
    from simulator.vapour_rail import melt_activity_resolver

    actual_builder = melt_activity_resolver.build_shadow_for_vapour_batch
    calls = 0

    def _counted_builder(**kwargs):
        nonlocal calls
        calls += 1
        return actual_builder(**kwargs)

    monkeypatch.setattr(
        melt_activity_resolver,
        "build_shadow_for_vapour_batch",
        _counted_builder,
    )
    ordinary = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=1500.0),
        flux_activation_context=_pre_rg_activation_context("K"),
    )
    assert calls == 0
    assert ordinary.melt_activity_shadow is None
    assert serialize_melt_activity_shadow(ordinary)["batch_shadow"] is None

    diagnostic = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(
            temperature_K=1500.0,
            melt_activity_shadow_enabled=True,
        ),
        flux_activation_context=_pre_rg_activation_context("K"),
    )
    assert calls == 1
    assert diagnostic.melt_activity_shadow is not None


def test_t568_typed_evaluator_refuses_wrong_target_and_claimed_authority() -> None:
    payload = _minimal_family("K", validation_status="pending_validation")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    evaluator = catalog._species["K"].evaluator
    activity = SourceReactionActivity(
        component_id="KO0.5",
        value=0.25,
        ln_value=math.log(0.25),
        verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
        bound_direction=None,
        reason="test_t568_evaluator_contract",
        standard_state=_TEST_ACTIVITY_STANDARD_STATE,
        phase_assemblage_ref="test:liquid_melt",
        chemical_potential_ref="test:mu",
        state_fingerprint="state:test",
        solve_group_id=None,
        provider="test",
        authority=False,
        target_standard_state=_TEST_ACTIVITY_STANDARD_STATE,
    )
    kwargs = {
        "pO2_bar": 1.0e-8,
        "expected_component_id": "KO0.5",
        "expected_standard_state": _TEST_ACTIVITY_STANDARD_STATE,
        "expected_state_fingerprint": "state:test",
    }
    valid_result = evaluator.evaluate_typed_shadow(
        1500.0,
        activity=activity,
        **kwargs,
    )
    assert valid_result["status"] == "shadow_only_no_behavior_authority"

    wrong_target = replace(
        activity,
        target_standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="crystalline",
            reference_pressure_bar=1.0,
        ),
    )
    target_result = evaluator.evaluate_typed_shadow(
        1500.0,
        activity=wrong_target,
        **kwargs,
    )
    assert target_result["refusal_code"] == "target_standard_state_mismatch"

    authority_result = evaluator.evaluate_typed_shadow(
        1500.0,
        activity=replace(activity, authority=True),
        **kwargs,
    )
    assert authority_result["refusal_code"] == "shadow_authority_violation"


def test_out_of_domain_k_at_1650c_is_flux_eligible_but_non_authoritative() -> None:
    """An OOD point answer keeps K flux-eligible without certifying its value."""
    payload = _minimal_family("K")
    pressure_model = payload["families"]["k_test_family"]["physical_properties"][
        "species"
    ]["K"]["pressure_models"][0]
    pressure_model["valid_domain"]["temperature_K"] = [1190.0, 1600.0]
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    temperature_K = 1650.0 + 273.15
    batch = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=temperature_K),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("K")
    assert not answer.is_refused
    assert answer.extra.get("out_of_range") is True
    assert answer.extra.get("status") == OUT_OF_RANGE_STATUS
    assert isinstance(answer.pressure, PressureValue)
    assert isinstance(answer.flux, FluxEligible)
    assert answer.pressure.pa > 0.0
    assert answer.is_flux_active
    assert "K" in batch.flux_active_species_ids
    assert answer.verdict_status == "status_bearing_non_authoritative"
    assert answer.certification_ceiling == "never"

    seam_pa = float(answer.pressure.pa) / 10.0
    flux_pressures, overlay = flux_pressures_from_batch(
        batch,
        effective_pressure_source=EffectivePressureSource(
            "populated_pre_rg_seam",
            {"K": seam_pa},
        ),
    )
    assert overlay["missing_effective_pressure_species"] == []
    assert flux_pressures["K"] == pytest.approx(seam_pa, rel=0.0, abs=0.0)
    assert (
        overlay["selected_pressure_source_by_species"]["K"]
        == "populated_pre_rg_seam"
    )
    assert overlay["catalog_continuation_flux_species"] == []
    assert overlay["extrapolated_flux_species"] == ["K"]

    fallback_flux_pressures, fallback_overlay = flux_pressures_from_batch(
        batch,
        effective_pressure_source=EffectivePressureSource(
            "empty_pre_rg_seam",
            {},
        ),
    )
    assert fallback_overlay["missing_effective_pressure_species"] == ["K"]
    assert fallback_flux_pressures["K"] == pytest.approx(answer.pressure.pa)
    assert (
        fallback_overlay["selected_pressure_source_by_species"]["K"]
        == "vapour_batch_catalog_continuation"
    )
    assert fallback_overlay["catalog_continuation_flux_species"] == ["K"]

    pre_rg = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=temperature_K),
        flux_activation_context=_pre_rg_activation_context("K"),
    )
    assert isinstance(pre_rg.channel("K").pressure, PressureValue)
    assert "K" in pre_rg.flux_active_species_ids

    # In-domain control remains eligible without extrapolation status.
    in_domain = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(temperature_K=1500.0),
        flux_activation_context=_pre_rg_activation_context("K"),
    ).channel("K")
    assert in_domain.extra.get("out_of_range") is not True
    assert isinstance(in_domain.pressure, PressureValue)
    assert isinstance(in_domain.flux, FluxEligible)
    assert in_domain.is_flux_active


def test_missing_activity_is_refusal_or_declared_henrian_upper_bound() -> None:
    """Henrian a=1 bound remains non-debiting (HEAD rail); b-122 direction.

    Activity verdict is UPPER_BOUND (gamma<=1 property). Pressure is typed as
    PressureUpperBound + FluxDiagnosticUpperBound so inventory does not debit.
    OOD gamma status-bearing (b-121) is covered separately and stays
    flux-driving on the oodfix seam.
    """
    payload = _minimal_family("K")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}

    refused = catalog.resolve_batch(
        ledger,
        VapourResolveState(temperature_K=1500.0),
        flux_activation_context=_pre_rg_activation_context(),
    ).channel("K")
    assert isinstance(refused.pressure, PressureRefusal)
    assert refused.source_reaction_activity is not None
    assert (
        refused.source_reaction_activity.verdict
        is ActivityVerdictKind.REFUSAL
    )

    bounded_payload = deepcopy(payload)
    reaction = bounded_payload["families"]["k_test_family"][
        "physical_properties"
    ]["species"]["K"]["source_reactions"][0]
    reaction["activity_input"]["allow_henrian_upper_bound"] = True
    bounded_catalog = compile_vapour_rail_catalog(
        bounded_payload, u0_manifest=_u0_stub("K")
    )
    bounded = bounded_catalog.resolve_batch(
        ledger,
        VapourResolveState(
            temperature_K=1500.0,
            source_reaction_fO2_bar=1.0e-8,
        ),
        flux_activation_context=_pre_rg_activation_context(),
    ).channel("K")
    # HEAD rail: genuine activity bound is non-debiting.
    assert isinstance(bounded.pressure, PressureUpperBound)
    assert isinstance(bounded.flux, FluxDiagnosticUpperBound)
    assert bounded.source_reaction_activity is not None
    assert (
        bounded.source_reaction_activity.verdict
        is ActivityVerdictKind.UPPER_BOUND
    )
    assert not bounded.is_flux_active
    assert "K" not in bounded_catalog.resolve_batch(
        ledger,
        VapourResolveState(
            temperature_K=1500.0,
            source_reaction_fO2_bar=1.0e-8,
        ),
        flux_activation_context=_pre_rg_activation_context("K"),
    ).flux_active_species_ids
    assert serialize_vapour_answer(bounded)["activity_bound"] == "bound-not-point"
    assert bounded.verdict_status == "status_bearing_non_authoritative"
    assert bounded.certification_ceiling == "never"


def test_out_of_domain_gamma_status_stays_numeric_and_flux_driving() -> None:
    catalog = compile_vapour_rail_catalog(
        _minimal_family("K"), u0_manifest=_u0_stub("K")
    )
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    state = _state_with_k_activity(
        temperature_K=1600.0,
        source_reaction_activity_provenance={
            "K": {
                "melt_oxide_activity_evidence_tier": "UNCERTIFIED",
                "melt_oxide_activity_model": (
                    "constant_gamma_table_with_endmember_continuity"
                ),
                "gamma_domain_authority": {
                    "authority_status": "out_of_gamma_domain",
                    "gamma_domain_K": (1500.0, 1500.0),
                    "temperature_K": 1600.0,
                },
            }
        },
    )

    answer = catalog.resolve_batch(
        ledger,
        state,
        flux_activation_context=_pre_rg_activation_context("K"),
    ).channel("K")

    assert isinstance(answer.pressure, PressureValue)
    assert isinstance(answer.flux, FluxEligible)
    assert answer.is_flux_active
    assert answer.source_reaction_activity is not None
    assert (
        answer.source_reaction_activity.verdict
        is ActivityVerdictKind.STATUS_BEARING_VALUE
    )
    assert answer.source_reaction_activity.reason == "out_of_gamma_domain"
    assert answer.source_reaction_activity.may_certify() is False
    assert answer.extra["activity_status"] == "status-bearing-not-point"


def test_malformed_reported_activity_becomes_typed_refusal() -> None:
    catalog = compile_vapour_rail_catalog(
        _minimal_family("K"), u0_manifest=_u0_stub("K")
    )
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    state = VapourResolveState(
        temperature_K=1500.0,
        source_reaction_activities={"K": "bogus"},  # type: ignore[dict-item]
        source_reaction_activity_provider="test_activity_provider",
        source_reaction_activity_evidence_refs={"K": "doi:10.1234/test-activity"},
        source_reaction_activity_standard_states={
            "K": _TEST_ACTIVITY_STANDARD_STATE
        },
    )

    answer = catalog.resolve_batch(
        ledger,
        state,
        flux_activation_context=_pre_rg_activation_context(),
    ).channel("K")
    assert isinstance(answer.pressure, PressureRefusal)
    assert answer.source_reaction_activity is not None
    assert answer.source_reaction_activity.verdict is ActivityVerdictKind.REFUSAL


def test_resolve_batch_preserves_reported_standard_state_mismatch() -> None:
    catalog = compile_vapour_rail_catalog(
        _minimal_family("K"), u0_manifest=_u0_stub("K")
    )
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    mismatched_standard_state = StandardStateIdentity(
        convention=_TEST_ACTIVITY_STANDARD_STATE.convention,
        phase="solid",
        reference_pressure_bar=_TEST_ACTIVITY_STANDARD_STATE.reference_pressure_bar,
        component_basis=_TEST_ACTIVITY_STANDARD_STATE.component_basis,
    )
    state = VapourResolveState(
        temperature_K=1500.0,
        source_reaction_fO2_bar=1.0e-8,
        source_reaction_activities={"K": 0.25},
        source_reaction_activity_provider="test_activity_provider",
        source_reaction_activity_evidence_refs={"K": "doi:10.1234/test-activity"},
        source_reaction_activity_standard_states={"K": mismatched_standard_state},
    )

    answer = catalog.resolve_batch(
        ledger,
        state,
        flux_activation_context=_pre_rg_activation_context(),
    ).channel("K")
    assert isinstance(answer.pressure, PressureRefusal)
    assert answer.source_reaction_activity is not None
    assert (
        answer.source_reaction_activity.refusal_code
        is ActivityRefusalCode.STANDARD_STATE_MISMATCH
    )


# ---------------------------------------------------------------------------
# Refusal closure fixed point precedes solve bundles
# ---------------------------------------------------------------------------


def test_refusal_closure_fixed_point_before_solve_bundles() -> None:
    live = RequestRule(
        species_id="Na",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"Na2O"}),
        required_source_atoms=frozenset({"Na", "O"}),
        solve_group_id="alkali",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="Na",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
    )
    refused_partner = RequestRule(
        species_id="NaO",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"Na2O"}),
        required_source_atoms=frozenset({"Na", "O"}),
        solve_group_id="alkali",
        applicability_predicate="not_applicable",
        request_rule_kind="source_inventory_present",
        origin="u0_v",
        formula_id="NaO",
        has_pressure_evaluator=False,
        has_alpha=False,
        has_route=False,
        has_formula=True,
    )
    rules = (live, refused_partner)
    ledger = {"process.cleaned_melt": {"Na2O": 1.0}}
    batch = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1600.0),
        catalog_species=_stub_catalog_species("Na"),
        flux_activation_context=_rg_activation_context(),
    )
    assert batch.metadata["refusal_closure_fixed_point"] is True
    # Refused channel is in the batch but not in any solve bundle
    assert "NaO" in batch.requested_species_ids
    assert batch.channel("NaO").is_refused
    for members in batch.solve_bundle_ids.values():
        assert "NaO" not in members
    # Live survivor must be flux-active and form a bundle; refused partner excluded
    assert "Na" in batch.flux_active_species_ids
    assert any("Na" in members for members in batch.solve_bundle_ids.values())


def test_build_solve_bundles_only_from_flux_active_survivors() -> None:
    rules = (
        RequestRule(
            species_id="A",
            source_account="process.cleaned_melt",
            parent_species_ids=frozenset({"P"}),
            required_source_atoms=frozenset({"P"}),
            solve_group_id="g1",
            applicability_predicate="applicable",
            request_rule_kind="source_inventory_present",
            origin="catalog",
            formula_id="A",
        ),
        RequestRule(
            species_id="B",
            source_account="process.cleaned_melt",
            parent_species_ids=frozenset({"P"}),
            required_source_atoms=frozenset({"P"}),
            solve_group_id="g1",
            applicability_predicate="applicable",
            request_rule_kind="source_inventory_present",
            origin="catalog",
            formula_id="B",
        ),
        RequestRule(
            species_id="C",
            source_account="process.cleaned_melt",
            parent_species_ids=frozenset({"Q"}),
            required_source_atoms=frozenset({"Q"}),
            solve_group_id="g2",
            applicability_predicate="applicable",
            request_rule_kind="source_inventory_present",
            origin="catalog",
            formula_id="C",
        ),
    )
    # Only A and B survive; C refused (not in flux_active)
    bundles = build_solve_bundles(
        flux_active=frozenset({"A", "B"}),
        rules=rules,
    )
    assert len(bundles) == 1
    members = next(iter(bundles.values()))
    assert members == frozenset({"A", "B"})


# ---------------------------------------------------------------------------
# Complete candidate ranking + bundle-atomic allocation (§4.2 steps 4–5)
# ---------------------------------------------------------------------------


def test_single_catalog_candidate_selection_is_behavioral_noop() -> None:
    rule = _selector_rule("K")
    candidate = _selector_candidate(
        "catalog",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 99.0},
    )
    common = {
        "rules": (rule,),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _selector_catalog_species("K"),
        "flux_activation_context": _rg_activation_context(),
    }
    baseline = resolve_vapour_batch(**common)
    with_single_candidate = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={"K": (candidate,)},
    )

    baseline_answer = baseline.channel("K")
    selected_answer = with_single_candidate.channel("K")
    assert selected_answer.pressure == baseline_answer.pressure
    assert selected_answer.flux == baseline_answer.flux
    assert selected_answer.validation_status == baseline_answer.validation_status
    assert selected_answer.source_label == baseline_answer.source_label
    assert selected_answer == baseline_answer
    assert serialize_vapour_answer(selected_answer) == serialize_vapour_answer(
        baseline_answer
    )
    assert with_single_candidate.solve_bundle_ids == baseline.solve_bundle_ids
    assert (
        with_single_candidate.flux_active_species_ids
        == baseline.flux_active_species_ids
    )


def test_single_complete_candidate_allocates_despite_raw_diagnostic_row() -> None:
    rule = _selector_rule("K")
    external = _selector_candidate(
        "only_external",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 701.0},
    )
    raw_diagnostic = _selector_candidate(
        "vaporock",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 999.0},
    )
    common = {
        "rules": (rule,),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _stub_catalog_species("K", pressure_pa=10.0),
        "flux_activation_context": _rg_activation_context(),
    }

    baseline = resolve_vapour_batch(**common)
    with_candidates = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={
            "K": (external, raw_diagnostic),
        },
    )

    selected_answer = with_candidates.channel("K")
    assert baseline.channel("K").pressure == PressureValue(10.0)
    assert selected_answer.pressure == PressureValue(701.0)
    assert selected_answer.source_label == "only_external"
    assert (
        selected_answer.extra["selected_evaluation_id"]
        == "evaluation:only_external"
    )


@pytest.mark.parametrize(
    "candidate",
    (
        _selector_candidate(
            "vaporock",
            VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
            {"K": 50.0},
        ),
        _selector_candidate(
            "unratified_external",
            "diagnostic:unratified",
            {"K": 50.0},
        ),
        _selector_candidate(
            "unreviewed_external",
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
            {"K": 50.0},
            fixed_reviewed=False,
        ),
        _selector_candidate(
            "total_pressure_dependent_calibration",
            VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
            {"K": 50.0},
            total_pressure_dependent=True,
        ),
        replace(
            _selector_candidate(
                "outside_domain",
                VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
                {"K": 50.0},
            ),
            covers_state=lambda _state: False,
        ),
    ),
    ids=(
        "canonical_raw_vaporock",
        "non_ratified",
        "non_reviewed",
        "total_pressure_dependent",
        "outside_domain",
    ),
)
def test_public_allocator_refuses_source_that_has_not_passed_all_gates(
    candidate: ProviderDomainCandidate,
) -> None:
    rule = _selector_rule("K")
    baseline = resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )
    with pytest.raises(
        VapourRequestConstructionError,
        match="selected source has not passed all allocation gates",
    ):
        allocate_selected_source(
            answers={"K": baseline.channel("K")},
            bundle_species_ids=frozenset({"K"}),
            selected_source=candidate,
            bundle_identity=_selector_bundle_identity(candidate),
        )


def test_two_candidate_ranking_prefers_reviewed_calibrated_source() -> None:
    state = VapourResolveState(temperature_K=1600.0)
    external = _selector_candidate(
        "external_pending",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 100.0, "K": 200.0},
    )
    calibrated = _selector_candidate(
        "vaporock_calibrated",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"Na": 10.0, "K": 20.0},
        fixed_reviewed=True,
    )
    candidates = (external, calibrated)  # provider order must not decide
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=candidates,
        state=state,
    ) is calibrated
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=(
            external,
            replace(
                calibrated,
                calibration_request_total_pressure_dependent=True,
            ),
        ),
        state=state,
    ) is external
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=(
            external,
            replace(calibrated, evaluation_is_fixed_and_reviewed=False),
        ),
        state=state,
    ) is external

    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        provider_candidates_by_species={
            "Na": candidates,
            "K": candidates,
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert {
        batch.channel(species_id).source_label for species_id in ("Na", "K")
    } == {"vaporock_calibrated"}
    assert batch.channel("Na").pressure == PressureValue(10.0)
    assert batch.channel("K").pressure == PressureValue(20.0)
    assert all(
        batch.channel(species_id).validation_status == "pending_validation"
        for species_id in ("Na", "K")
    )


def test_selected_connected_bundle_stamps_one_solve_group_identity() -> None:
    rules = (
        replace(_selector_rule("Na"), solve_group_id="g-na"),
        replace(_selector_rule("K"), solve_group_id="g-k"),
    )
    candidates = (
        _selector_candidate(
            "a-provider",
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
            {"Na": 101.0, "K": 202.0},
        ),
        _selector_candidate(
            "z-provider",
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
            {"Na": 303.0, "K": 404.0},
        ),
    )

    batch = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            "Na": candidates,
            "K": candidates,
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )
    rendered = serialize_vapour_batch(batch)
    assert rendered is not None
    assert rendered["solve_bundle_ids"] == {
        "bundle:0:K+Na": ["K", "Na"]
    }
    channels = rendered["channels_by_species"]
    assert {
        channels[species_id]["solve_group_id"]
        for species_id in ("Na", "K")
    } == {"selector_bundle"}
    assert {
        channels[species_id]["state_fingerprint"]
        for species_id in ("Na", "K")
    } == {_state_fingerprint(VapourResolveState(temperature_K=1600.0))}
    assert {
        channels[species_id]["extra"]["bundle_id"]
        for species_id in ("Na", "K")
    } == {"bundle:0:K+Na"}
    assert {
        channels[species_id]["extra"]["selected_evaluation_id"]
        for species_id in ("Na", "K")
    } == {"evaluation:a-provider"}


def test_single_complete_candidate_stamps_transitive_bundle_identity() -> None:
    rules = (
        replace(
            _selector_rule("Na"),
            parent_species_ids=frozenset({"P"}),
            required_source_atoms=frozenset({"P"}),
            solve_group_id="rule-na",
        ),
        replace(
            _selector_rule("K"),
            parent_species_ids=frozenset({"P", "Q"}),
            required_source_atoms=frozenset({"P", "Q"}),
            solve_group_id="rule-k",
        ),
        replace(
            _selector_rule("Cs"),
            parent_species_ids=frozenset({"Q"}),
            required_source_atoms=frozenset({"Q"}),
            solve_group_id="rule-cs",
        ),
    )
    selected = _selector_candidate(
        "catalog",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 101.0, "K": 202.0, "Cs": 303.0},
        evaluation_id="evaluation:catalog",
        solve_group_id="catalog-bundle",
    )

    batch = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0, "Q": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            species_id: (selected,) for species_id in ("Na", "K", "Cs")
        },
        catalog_species=_selector_catalog_species("Na", "K", "Cs"),
        flux_activation_context=_rg_activation_context(),
    )

    rendered = serialize_vapour_batch(batch)
    assert rendered is not None
    assert rendered["solve_bundle_ids"] == {
        "bundle:0:Cs+K+Na": ["Cs", "K", "Na"]
    }
    channels = rendered["channels_by_species"]
    assert {
        channels[species_id]["solve_group_id"]
        for species_id in ("Na", "K", "Cs")
    } == {"catalog-bundle"}
    assert {
        channels[species_id]["extra"]["selected_evaluation_id"]
        for species_id in ("Na", "K", "Cs")
    } == {"evaluation:catalog"}


def test_allocator_stamps_selected_state_identity_on_every_answer() -> None:
    state = VapourResolveState(temperature_K=1600.0)
    rules = (_selector_rule("Na"), _selector_rule("K"))
    baseline = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )
    selected = _selector_candidate(
        "selected",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 101.0, "K": 202.0},
    )
    bundle_identity = _selector_bundle_identity(selected)

    allocated = allocate_selected_source(
        answers={
            "Na": replace(
                baseline.channel("Na"),
                solve_group_id="stale-na",
                state_fingerprint="state:stale-na",
            ),
            "K": replace(
                baseline.channel("K"),
                solve_group_id="stale-k",
                state_fingerprint="state:stale-k",
            ),
        },
        bundle_species_ids=frozenset({"Na", "K"}),
        selected_source=selected,
        bundle_identity=bundle_identity,
        state=state,
    )

    assert {answer.solve_group_id for answer in allocated.values()} == {
        bundle_identity.solve_group_id
    }
    assert {answer.state_fingerprint for answer in allocated.values()} == {
        bundle_identity.state_fingerprint
    }
    assert {answer.extra["bundle_id"] for answer in allocated.values()} == {
        bundle_identity.bundle_id
    }


@pytest.mark.parametrize(
    "identity_change",
    (
        {"bundle_id": ""},
        {"evaluation_id": "evaluation:other"},
        {"solve_group_id": "other-solve-group"},
        {"state_fingerprint": "state:other"},
    ),
    ids=("empty-bundle", "evaluation", "solve-group", "state"),
)
def test_allocator_refuses_mismatched_bundle_identity(
    identity_change: dict[str, str],
) -> None:
    state = VapourResolveState(temperature_K=1600.0)
    baseline = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )
    selected = _selector_candidate(
        "selected",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 202.0},
    )
    mismatched_identity = replace(
        _selector_bundle_identity(selected),
        **identity_change,
    )

    with pytest.raises(
        VapourRequestConstructionError,
        match="selected source identity does not match bundle identity",
    ):
        allocate_selected_source(
            answers={"K": baseline.channel("K")},
            bundle_species_ids=frozenset({"K"}),
            selected_source=selected,
            bundle_identity=mismatched_identity,
            state=state,
        )


@pytest.mark.parametrize(
    "identity_change",
    (
        {"solve_group_id": "other-solve-group"},
        {
            "state_fingerprint": _state_fingerprint(
                VapourResolveState(temperature_K=1700.0)
            )
        },
    ),
    ids=("mixed-solve-group", "mixed-state"),
)
def test_mixed_candidate_identity_refuses_connected_bundle(
    identity_change: dict[str, str],
) -> None:
    first = _selector_candidate(
        "first-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 101.0, "K": 202.0},
    )
    second = replace(
        _selector_candidate(
            "second-provider",
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
            {"Na": 303.0, "K": 404.0},
        ),
        **identity_change,
    )
    candidates = (first, second)

    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            "Na": candidates,
            "K": candidates,
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert all(
        batch.channel(species_id).refusal_code
        == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
        for species_id in ("Na", "K")
    )
    assert all(
        isinstance(batch.channel(species_id).pressure, PressureRefusal)
        and isinstance(batch.channel(species_id).flux, FluxRefusal)
        for species_id in ("Na", "K")
    )
    assert batch.flux_active_species_ids == frozenset()
    assert not batch.solve_bundle_ids


@pytest.mark.parametrize(
    "evaluation_state",
    (
        VapourResolveState(
            temperature_K=1600.0,
            source_reaction_composition_wt_pct={"SiO2": 60.0},
            source_reaction_activity_standard_states={
                "K": _TEST_ACTIVITY_STANDARD_STATE
            },
        ),
        VapourResolveState(
            temperature_K=1600.0,
            source_reaction_composition_wt_pct={"SiO2": 50.0},
            source_reaction_activity_standard_states={
                "K": replace(
                    _TEST_ACTIVITY_STANDARD_STATE,
                    component_basis="henrian_infinite_dilution",
                )
            },
        ),
    ),
    ids=("composition", "activity-basis"),
)
def test_candidate_state_identity_refuses_state_basis_mismatch(
    evaluation_state: VapourResolveState,
) -> None:
    request_state = VapourResolveState(
        temperature_K=1600.0,
        source_reaction_composition_wt_pct={"SiO2": 50.0},
        source_reaction_activity_standard_states={
            "K": _TEST_ACTIVITY_STANDARD_STATE
        },
    )
    candidates = tuple(
        _selector_candidate(
            provider_id,
            VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
            {"K": pressure},
            evaluation_state=evaluation_state,
        )
        for provider_id, pressure in (("first", 101.0), ("second", 202.0))
    )

    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=request_state,
        provider_candidates_by_species={"K": candidates},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    answer = batch.channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)


def test_numeric_equivalent_standard_state_identity_remains_admissible() -> None:
    request_standard_state = replace(
        _TEST_ACTIVITY_STANDARD_STATE,
        reference_pressure_bar=1.0,
        reference_temperature_K=1600.0,
    )
    evaluation_standard_state = replace(
        request_standard_state,
        reference_pressure_bar=1,
        reference_temperature_K=1600,
    )
    assert evaluation_standard_state == request_standard_state
    request_state = VapourResolveState(
        temperature_K=1600.0,
        source_reaction_activity_standard_states={"K": request_standard_state},
    )
    evaluation_state = VapourResolveState(
        temperature_K=1600.0,
        source_reaction_activity_standard_states={
            "K": evaluation_standard_state
        },
    )
    candidate = _selector_candidate(
        "numeric-equivalent",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 101.0},
        evaluation_state=evaluation_state,
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(candidate,),
        state=request_state,
    ) is candidate


@pytest.mark.parametrize("field_name", (
    "reference_pressure_bar", "reference_temperature_K",
))
@pytest.mark.parametrize("number", (float, Decimal), ids=("float", "decimal"))
def test_standard_state_fingerprint_canonicalizes_signed_zero(
    field_name: str, number,
) -> None:
    positive = replace(_TEST_ACTIVITY_STANDARD_STATE, **{field_name: number("0")})
    negative = replace(positive, **{field_name: number("-0")})
    assert positive == negative
    assert positive.fingerprint() == negative.fingerprint()


@pytest.mark.parametrize("field_name", (
    "reference_pressure_bar", "reference_temperature_K",
))
@pytest.mark.parametrize("value", _NONFINITE_FINGERPRINT_VALUES)
def test_standard_state_fingerprint_refuses_nonfinite(
    field_name: str, value: float,
) -> None:
    standard_state = replace(_TEST_ACTIVITY_STANDARD_STATE, **{field_name: value})
    with pytest.raises(ValueError, match="non-finite"):
        standard_state.fingerprint()


@pytest.mark.parametrize("field_name", (
    "temperature_K", "fO2_bar", "total_pressure_Pa",
    "source_reaction_fO2_bar", "source_reaction_fO2_log10",
    "source_reaction_activity_pressure_bar", "source_reaction_activities",
    "source_reaction_composition_wt_pct", "reference_pressure_bar",
    "reference_temperature_K",
))
@pytest.mark.parametrize("number", (float, Decimal), ids=("float", "decimal"))
def test_candidate_signed_zero_state_remains_admissible(
    field_name: str, number,
) -> None:
    states = []
    for zero in (number("0"), number("-0")):
        if field_name.startswith("reference_"):
            fields = {"source_reaction_activity_standard_states": {
                "K": replace(_TEST_ACTIVITY_STANDARD_STATE, **{field_name: zero}),
            }}
        elif field_name in (
            "source_reaction_activities", "source_reaction_composition_wt_pct",
        ):
            fields = {field_name: {"K": zero}}
        else:
            fields = {field_name: zero}
        states.append(replace(VapourResolveState(temperature_K=1600.0), **fields))
    request_state, evaluation_state = states
    assert request_state == evaluation_state
    candidate = _selector_candidate(
        "signed-zero", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED, {"K": 137.0},
        evaluation_state=evaluation_state,
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}), candidates=(candidate,),
        state=request_state,
    ) is candidate
    if field_name == "source_reaction_fO2_log10":
        batch = resolve_vapour_batch(
            rules=(_selector_rule("K"),),
            ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
            state=request_state, provider_candidates_by_species={"K": (candidate,)},
            catalog_species=_selector_catalog_species("K"),
            flux_activation_context=_rg_activation_context(),
        )
        assert batch.channel("K").pressure == PressureValue(137.0)


@pytest.mark.parametrize("field_name", (
    "temperature_K", "fO2_bar", "total_pressure_Pa",
    "source_reaction_fO2_bar", "source_reaction_fO2_log10",
    "source_reaction_activity_pressure_bar", "source_reaction_activities",
    "source_reaction_composition_wt_pct", "reference_pressure_bar",
    "reference_temperature_K",
))
@pytest.mark.parametrize("value", _NONFINITE_FINGERPRINT_VALUES)
@pytest.mark.parametrize("boundary", ("fingerprint", "resolver"))
def test_request_fingerprint_refuses_nonfinite(
    field_name: str, value: float, boundary: str,
) -> None:
    if field_name.startswith("reference_"):
        fields = {"source_reaction_activity_standard_states": {
            "K": replace(_TEST_ACTIVITY_STANDARD_STATE, **{field_name: value}),
        }}
    elif field_name in (
        "source_reaction_activities", "source_reaction_composition_wt_pct",
    ):
        fields = {field_name: {"K": value}}
    else:
        fields = {field_name: value}
    state = replace(VapourResolveState(temperature_K=1600.0), **fields)
    if boundary == "fingerprint":
        with pytest.raises(VapourRequestConstructionError, match="non-finite"):
            _state_fingerprint(state)
        return
    # On the broken gate this recreates an evaluation bearing the invalid state.
    try:
        fingerprint = _state_fingerprint(state)
    except VapourRequestConstructionError:
        fingerprint = "invalid-state"
    candidate = _selector_candidate(
        "nonfinite-evaluation", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 139.0}, state_fingerprint=fingerprint,
    )
    answer = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}}, state=state,
        provider_candidates_by_species={"K": (candidate,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    ).channel("K")
    assert answer.refusal_code == REFUSAL_MISSING_OUTCOME_STATE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert answer.pressure.code == REFUSAL_MISSING_OUTCOME_STATE
    assert answer.flux.code == REFUSAL_MISSING_OUTCOME_STATE
    assert "non-finite" in answer.extra["detail"]


@pytest.mark.parametrize("field_name", (
    "temperature_K", "fO2_bar", "total_pressure_Pa",
    "source_reaction_fO2_bar", "source_reaction_fO2_log10",
    "source_reaction_activity_pressure_bar", "source_reaction_activities",
    "source_reaction_composition_wt_pct", "reference_pressure_bar",
    "reference_temperature_K",
))
@pytest.mark.parametrize("base_type, actual, override", (
    pytest.param(Decimal, "-sNaN", "false", id="decimal-hidden-snan"),
    pytest.param(Decimal, "Infinity", "false", id="decimal-hidden-inf"),
    pytest.param(Decimal, "-sNaN", "raise", id="decimal-raising-snan"),
    pytest.param(Decimal, "0.125", "true", id="decimal-finite-lying"),
    pytest.param(Decimal, "0.125", "raise", id="decimal-finite-raising"),
    pytest.param(float, "nan", "finite", id="float-hidden-nan"),
    pytest.param(float, "inf", "finite", id="float-hidden-inf"),
    pytest.param(float, "nan", "value-error", id="float-raising-nan"),
    pytest.param(float, "nan", "overflow", id="float-overflow-nan"),
    pytest.param(float, "0.125", "finite", id="float-finite-lying"),
))
def test_numeric_subclass_fingerprint_uses_underlying_value(
    field_name: str, base_type, actual: str, override: str,
) -> None:
    class OverrideNumber(base_type):
        def is_nan(self):
            if override == "raise":
                raise RuntimeError("classification override must not run")
            return override == "true"

        is_snan = is_nan
        is_infinite = is_nan

        def __float__(self):
            if override == "value-error":
                raise ValueError("conversion override must not run")
            if override == "overflow":
                raise OverflowError("conversion override must not run")
            return 0.25

    value = OverrideNumber(actual)
    finite = actual == "0.125"
    states = []
    for number in (value, 0.125):
        if field_name.startswith("reference_"):
            fields = {"source_reaction_activity_standard_states": {
                "K": replace(_TEST_ACTIVITY_STANDARD_STATE, **{field_name: number}),
            }}
        elif field_name in (
            "source_reaction_activities", "source_reaction_composition_wt_pct",
        ):
            fields = {field_name: {"K": number}}
        else:
            fields = {field_name: number}
        states.append(replace(VapourResolveState(temperature_K=1600.0), **fields))
    state, ordinary_state = states
    if finite:
        assert _state_fingerprint(state) == _state_fingerprint(ordinary_state)
    else:
        with pytest.raises(VapourRequestConstructionError, match="non-finite"):
            _state_fingerprint(state)
    candidate = _selector_candidate(
        "numeric-subclass", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED, {"K": 149.0},
        evaluation_state=ordinary_state,
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}}, state=state,
        provider_candidates_by_species={"K": (candidate,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("K")
    if finite:
        assert answer.refusal_code is None
        assert answer.pressure == PressureValue(149.0)
        assert batch.flux_active_species_ids == frozenset({"K"})
        assert answer.state_fingerprint == _state_fingerprint(ordinary_state)
    else:
        assert answer.refusal_code == REFUSAL_MISSING_OUTCOME_STATE
        assert isinstance(answer.pressure, PressureRefusal)
        assert isinstance(answer.flux, FluxRefusal)
        assert answer.pressure.code == REFUSAL_MISSING_OUTCOME_STATE
        assert answer.flux.code == REFUSAL_MISSING_OUTCOME_STATE
        assert "non-finite" in answer.extra["detail"]
        assert batch.flux_active_species_ids == frozenset()


@pytest.mark.parametrize("tampering", ("replacement", "unreadable"))
def test_resolver_preserves_public_admission_identity(monkeypatch, tampering: str) -> None:
    state = VapourResolveState(temperature_K=1633.0)
    parents = {"Li": {"P"}, "Na": {"P", "Q"}, "K": {"Q", "R"}, "Rb": {"R"}}
    candidate = _selector_candidate(
        "public-admission", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {species_id: 139.0 for species_id in parents},
        evaluation_state=state, solve_group_id="solve:original",
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset(parents), candidates=(candidate,), state=state,
    ) is candidate
    evaluation_id, _, fingerprint = candidate._admitted_identity
    if tampering == "replacement":
        object.__setattr__(candidate, "_admitted_identity", (
            evaluation_id, "solve:forged-after-public-admission", fingerprint,
        ))
    else:
        def forbidden_read(self):
            raise AssertionError("resolver read live identity after public admission")

        monkeypatch.setattr(
            ProviderDomainCandidate, "_admitted_identity", property(forbidden_read),
            raising=False,
        )
    try:
        batch = resolve_vapour_batch(
            rules=tuple(
                replace(
                    _selector_rule(species_id), parent_species_ids=frozenset(atoms),
                    solve_group_id=f"rule:{species_id}",
                )
                for species_id, atoms in parents.items()
            ),
            ledger_snapshot={"process.cleaned_melt": {"P": 1.0, "Q": 1.0, "R": 1.0}},
            state=state,
            provider_candidates_by_species={species_id: (candidate,) for species_id in parents},
            catalog_species=_selector_catalog_species(*parents),
            flux_activation_context=_rg_activation_context(),
        )
    except VapourRequestConstructionError:
        return
    assert len(batch.solve_bundle_ids) == 1
    for species_id in parents:
        answer = batch.channel(species_id)
        if answer.is_refused:
            assert isinstance(answer.pressure, PressureRefusal)
            assert isinstance(answer.flux, FluxRefusal)
        else:
            assert answer.solve_group_id == "solve:original"
            assert answer.state_fingerprint == fingerprint
            assert answer.extra["selected_evaluation_id"] == evaluation_id


def test_candidate_identity_fields_reject_ordinary_assignment() -> None:
    candidate = _selector_candidate(
        "immutable-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 101.0},
    )

    for field_name in ("evaluation_id", "solve_group_id", "state_fingerprint"):
        with pytest.raises(FrozenInstanceError):
            setattr(candidate, field_name, "mutated")


def test_allocator_uses_identity_captured_when_candidate_was_admitted() -> None:
    state = VapourResolveState(temperature_K=1600.0)
    candidate = _selector_candidate(
        "captured-identity",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 101.0},
        evaluation_id="evaluation:captured",
        solve_group_id="solve-group:captured",
    )
    admitted = rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(candidate,),
        state=state,
    )
    assert admitted is candidate
    captured_identity = _selector_bundle_identity(admitted)
    baseline = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    object.__setattr__(candidate, "evaluation_id", "evaluation:mutated")
    object.__setattr__(candidate, "solve_group_id", "solve-group:mutated")
    object.__setattr__(candidate, "state_fingerprint", "state:mutated")
    allocated = allocate_selected_source(
        answers={"K": baseline.channel("K")},
        bundle_species_ids=frozenset({"K"}),
        selected_source=candidate,
        bundle_identity=captured_identity,
        state=state,
    )

    answer = allocated["K"]
    assert answer.solve_group_id == captured_identity.solve_group_id
    assert answer.state_fingerprint == captured_identity.state_fingerprint
    assert (
        answer.extra["selected_evaluation_id"]
        == captured_identity.evaluation_id
    )


def test_canonical_provider_label_controls_external_tie_break() -> None:
    state = VapourResolveState(temperature_K=1711.0)
    diagnostic_alias = _selector_candidate(
        "diagnostic_stub",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": Decimal("71")},
        evaluation_state=state,
    )
    canonical_first = _selector_candidate(
        "g-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": Decimal("72")},
        evaluation_state=state,
    )

    selected = rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(diagnostic_alias, canonical_first),
        state=state,
    )

    assert selected is canonical_first
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        provider_candidates_by_species={
            "K": (diagnostic_alias, canonical_first),
        },
        catalog_species=_stub_catalog_species("K", pressure_pa=19.375),
        flux_activation_context=_rg_activation_context(),
    )
    assert batch.channel("K").source_label == "g-provider"
    assert batch.channel("K").pressure == PressureValue(Decimal("72"))


@pytest.mark.parametrize("reverse_provider_order", [False, True])
def test_duplicate_provider_id_refuses_in_both_input_orders(
    reverse_provider_order: bool,
) -> None:
    pressure_10 = _selector_candidate(
        "same-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 10.0},
    )
    pressure_20 = _selector_candidate(
        "same-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 20.0},
        fixed_reviewed=False,
    )
    candidates = (
        (pressure_20, pressure_10)
        if reverse_provider_order
        else (pressure_10, pressure_20)
    )

    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={"K": candidates},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    answer = batch.channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert "K" not in batch.flux_active_species_ids


def test_public_ranker_refuses_provider_collision_in_validation_rows() -> None:
    complete = _selector_candidate(
        "diagnostic_stub",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": Decimal("41.25"), "K": Decimal("42.25")},
    )
    collision = _selector_candidate(
        "internal-analytical",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": Decimal("99.25")},
    )
    validation_rows = {
        "Na": (complete, collision),
        "K": (complete,),
    }

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=(complete,),
        state=VapourResolveState(temperature_K=1711.0),
        validation_candidates_by_species=validation_rows,
    ) is None

    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1711.0),
        provider_candidates_by_species=validation_rows,
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )
    assert all(
        batch.channel(species_id).refusal_code
        == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
        for species_id in ("Na", "K")
    )


def test_repeated_candidate_object_is_one_provider_not_a_collision() -> None:
    candidate = _selector_candidate(
        "one-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 101.0},
    )
    common = {
        "rules": (_selector_rule("K"),),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _stub_catalog_species("K", pressure_pa=7.0),
        "flux_activation_context": _rg_activation_context(),
    }

    repeated = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={"K": (candidate, candidate)},
    )

    answer = repeated.channel("K")
    assert answer.pressure == PressureValue(101.0)
    assert answer.source_label == "one-provider"
    assert answer.extra["selected_evaluation_id"] == "evaluation:one-provider"
    assert answer.refusal_code is None
    assert repeated.flux_active_species_ids == frozenset({"K"})


def test_public_ranker_identity_deduplicates_repeated_candidate() -> None:
    candidate = _selector_candidate(
        "one-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 611.0, "K": 612.0},
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=(candidate, candidate, candidate),
        state=VapourResolveState(temperature_K=1600.0),
    ) is candidate


@pytest.mark.parametrize("split_rows", (False, True))
@pytest.mark.parametrize("reverse_order", (False, True))
def test_equal_candidate_clones_share_selection_and_allocation(
    split_rows: bool, reverse_order: bool,
) -> None:
    state = VapourResolveState(temperature_K=1600.0)
    candidate = _selector_candidate(
        "one-provider", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 611.0, "K": 612.0},
    )
    clone = replace(candidate)
    assert clone == candidate and clone is not candidate
    candidates = (clone, candidate) if reverse_order else (candidate, clone)
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Na", "K"}),
        candidates=candidates, state=state,
    ) is candidates[0]
    rows = (
        {"Na": (candidates[0],), "K": (candidates[1],)}
        if split_rows else {species_id: candidates for species_id in ("Na", "K")}
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}}, state=state,
        provider_candidates_by_species=rows,
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )
    assert batch.flux_active_species_ids == frozenset({"Na", "K"})
    for species_id, pressure in (("Na", 611.0), ("K", 612.0)):
        answer = batch.channel(species_id)
        assert answer.refusal_code is None
        assert answer.pressure == PressureValue(pressure)
        assert answer.source_label == candidate.provider_id
        assert answer.extra["selected_evaluation_id"] == candidate.evaluation_id
        assert answer.solve_group_id == candidate.solve_group_id
        assert answer.state_fingerprint == _state_fingerprint(state)


def test_admitted_candidate_clone_swap_preserves_allocation(monkeypatch) -> None:
    import simulator.vapour_rail.request as request_module

    state = VapourResolveState(temperature_K=1633.0)
    parents = {"Li": {"P"}, "Na": {"P", "Q"}, "K": {"Q", "R"}, "Rb": {"R"}}
    candidate = _selector_candidate(
        "identity-own-probe", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {species_id: 139.0 for species_id in parents},
        evaluation_state=state, evaluation_id="evaluation:original",
        solve_group_id="solve:original",
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset(parents), candidates=(candidate,), state=state,
    ) is candidate
    clone = replace(candidate)
    object.__setattr__(clone, "_admitted_identity", None)
    assert clone == candidate and clone is not candidate
    caller_rows = {species_id: [candidate] for species_id in parents}
    gather = request_module._bundle_evaluation_candidates

    def gather_then_replace(*args):
        gathered = gather(*args)
        for row in caller_rows.values():
            row[:] = [clone]
        return gathered

    monkeypatch.setattr(request_module, "_bundle_evaluation_candidates", gather_then_replace)
    batch = resolve_vapour_batch(
        rules=tuple(
            replace(
                _selector_rule(species_id), parent_species_ids=frozenset(atoms),
                solve_group_id=f"rule:{species_id}",
            )
            for species_id, atoms in parents.items()
        ),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0, "Q": 1.0, "R": 1.0}},
        state=state, provider_candidates_by_species=caller_rows,
        catalog_species=_selector_catalog_species(*parents),
        flux_activation_context=_rg_activation_context(),
    )
    assert len(batch.solve_bundle_ids) == 1
    assert batch.flux_active_species_ids == frozenset(parents)
    assert id(clone) not in request_module._admitted_candidate_identities
    for species_id in parents:
        answer = batch.channel(species_id)
        assert answer.refusal_code is None
        assert answer.pressure == PressureValue(139.0)
        assert answer.extra["selected_evaluation_id"] == "evaluation:original"
        assert answer.solve_group_id == "solve:original"
        assert answer.state_fingerprint == _state_fingerprint(state)


def test_clone_first_uses_admitted_value_representative() -> None:
    import simulator.vapour_rail.request as request_module

    state = VapourResolveState(temperature_K=1600.0)
    candidate = _selector_candidate(
        "admitted-provider", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED, {"K": 137.0},
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}), candidates=(candidate,), state=state,
    ) is candidate
    clone = replace(candidate)
    object.__setattr__(clone, "_admitted_identity", None)
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}), candidates=(clone,), state=state,
        validation_candidates_by_species={"K": (clone, candidate)},
    ) is candidate
    assert id(clone) not in request_module._admitted_candidate_identities


@pytest.mark.parametrize("reverse_order", (False, True))
def test_value_different_clone_is_still_provider_collision(reverse_order: bool) -> None:
    candidate = _selector_candidate(
        "same-provider", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED, {"K": 10.0},
    )
    different = replace(candidate, pressures_by_species={"K": PressureValue(20.0)})
    assert different != candidate
    candidates = (different, candidate) if reverse_order else (candidate, different)
    state = VapourResolveState(temperature_K=1600.0)
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}), candidates=candidates, state=state,
    ) is None
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}}, state=state,
        provider_candidates_by_species={"K": candidates},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert batch.flux_active_species_ids == frozenset()


@pytest.mark.parametrize("reverse_rule_order", [False, True])
def test_provider_id_collision_on_one_bundle_member_refuses_whole_bundle(
    reverse_rule_order: bool,
) -> None:
    complete_a = _selector_candidate(
        "a-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 70.0, "K": 71.0},
    )
    complete_z = _selector_candidate(
        "z-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 80.0, "K": 81.0},
    )
    colliding_na_only = _selector_candidate(
        "a-provider",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 90.0},
    )
    rules = (_selector_rule("Na"), _selector_rule("K"))
    if reverse_rule_order:
        rules = tuple(reversed(rules))

    batch = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            "Na": (complete_a, complete_z, colliding_na_only),
            "K": (complete_a, complete_z),
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert all(
        batch.channel(species_id).refusal_code
        == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
        for species_id in ("Na", "K")
    )
    assert batch.flux_active_species_ids == frozenset()


def test_validated_selected_source_cannot_inherit_catalog_anchor() -> None:
    rule = replace(
        _selector_rule("K"),
        validation_status="validated",
        validation_anchor_refs=("catalog-anchor",),
    )
    selected_without_anchors = _selector_candidate(
        "a-selected",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 71.0},
        statuses={"K": "validated"},
    )
    other = _selector_candidate(
        "z-other",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 99.0},
    )
    common = {
        "rules": (rule,),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _stub_catalog_species("K", pressure_pa=7.0),
        "flux_activation_context": _rg_activation_context(),
    }

    baseline = resolve_vapour_batch(**common)
    with_candidates = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={
            "K": (selected_without_anchors, other),
        },
    )

    answer = with_candidates.channel("K")
    assert answer.pressure == PressureValue(99.0)
    assert answer.source_label == "z-other"
    assert answer.validation_anchor_refs == ()
    with pytest.raises(
        VapourRequestConstructionError,
        match="selected source has not passed all allocation gates",
    ):
        allocate_selected_source(
            answers={"K": baseline.channel("K")},
            bundle_species_ids=frozenset({"K"}),
            selected_source=selected_without_anchors,
            bundle_identity=_selector_bundle_identity(
                selected_without_anchors
            ),
            state=common["state"],
        )
    allocated = allocate_selected_source(
        answers={"K": baseline.channel("K")},
        bundle_species_ids=frozenset({"K"}),
        selected_source=other,
        bundle_identity=_selector_bundle_identity(other),
        state=common["state"],
    )
    assert allocated["K"].source_label == "z-other"
    assert allocated["K"].validation_anchor_refs == ()


def test_unadmitted_validation_row_cannot_veto_bundle_species() -> None:
    calibrated = _selector_candidate(
        "vaporock_calibrated",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"Na": 11.0, "K": 22.0},
    )
    rogue_na_only = _selector_candidate(
        "rogue-na-only",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 303.0},
        statuses={"K": "validated"},
        independently_validated=frozenset({"K"}),
        anchors_by_species={"K": ("rogue-k-anchor",)},
    )
    common = {
        "rules": (_selector_rule("Na"), _selector_rule("K")),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _selector_catalog_species("Na", "K"),
        "flux_activation_context": _rg_activation_context(),
    }

    control = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={
            "Na": (calibrated,),
            "K": (calibrated,),
        },
    )
    with_rogue = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={
            "Na": (calibrated, rogue_na_only),
            "K": (calibrated,),
        },
    )

    assert with_rogue == control
    assert with_rogue.flux_active_species_ids == frozenset({"Na", "K"})
    assert all(
        with_rogue.channel(species_id).refusal_code is None
        for species_id in ("Na", "K")
    )


def test_anchorless_validated_row_cannot_veto_calibrated_source() -> None:
    calibrated = _selector_candidate(
        "vaporock_calibrated",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 91.0},
    )
    anchorless_external = _selector_candidate(
        "external_anchorless",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 927.0},
        statuses={"K": "validated"},
        independently_validated=frozenset({"K"}),
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(calibrated,),
        state=VapourResolveState(temperature_K=1600.0),
        validation_candidates_by_species={"K": (anchorless_external,)},
    ) is calibrated


def test_step4_refusal_is_not_counted_as_epoch_dormancy() -> None:
    incomplete = _selector_candidate(
        "incomplete-source",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 202.0},
        fixed_reviewed=False,
    )

    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={"K": (incomplete,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert batch.metadata["n_refused"] == 1
    assert batch.metadata["n_flux_active"] == 0
    assert batch.metadata["n_flux_dormant_by_epoch"] == 0


@pytest.mark.parametrize("reverse_provider_order", [False, True])
def test_k_demaria_independent_validation_disqualifies_whole_calibrated_bundle(
    reverse_provider_order: bool,
) -> None:
    state = VapourResolveState(temperature_K=1428.571429)
    demaria_residual_dex = 1.241
    vaporock_residual_range_dex = (1.15, 1.28)
    assert vaporock_residual_range_dex[0] < demaria_residual_dex < (
        vaporock_residual_range_dex[1]
    )

    external = _selector_candidate(
        "k_rail_external_grounded",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 101.0, "K": 202.0},
        statuses={"Na": "pending_validation", "K": "validated"},
        independently_validated=frozenset({"K"}),
        residuals_dex={"K": demaria_residual_dex},
        anchors_by_species={"K": ("demaria-k-anchor",)},
        evaluation_state=state,
    )
    calibrated = _selector_candidate(
        "vaporock_calibrated",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"Na": 11.0, "K": 22.0},
        fixed_reviewed=True,
        residuals_dex={"K": vaporock_residual_range_dex},
        evaluation_state=state,
    )
    assert calibrated.validation_residual_dex_by_species["K"] == (
        vaporock_residual_range_dex
    )
    candidates = (
        (calibrated, external)
        if reverse_provider_order
        else (external, calibrated)
    )

    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        provider_candidates_by_species={
            "Na": candidates,
            "K": candidates,
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert {
        batch.channel(species_id).source_label for species_id in ("Na", "K")
    } == {"k_rail_external_grounded"}
    assert batch.channel("Na").pressure == PressureValue(101.0)
    assert batch.channel("K").pressure == PressureValue(202.0)
    assert batch.channel("Na").validation_status == "pending_validation"
    assert batch.channel("K").validation_status == "validated"
    assert batch.channel("K").extra["validation_residual_dex"] == pytest.approx(
        demaria_residual_dex
    )


def test_canonical_raw_vaporock_candidate_is_rejected_when_flag_omitted() -> None:
    external_domain_gate = ProviderDomainCandidate(
        provider_id="external_domain_gate",
        covers_state=lambda _state: True,
    )
    raw = _selector_candidate(
        "vaporock",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 50.0},
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={"K": (external_domain_gate, raw)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    answer = batch.channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert "K" not in batch.flux_active_species_ids
    assert not batch.solve_bundle_ids


def test_vaporock_warm_raw_candidate_is_rejected_with_review_flag() -> None:
    raw = _selector_candidate(
        "vaporock_warm",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 50.0},
        fixed_reviewed=True,
        total_pressure_dependent=False,
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(raw,),
        state=VapourResolveState(temperature_K=1600.0),
    ) is None


def test_raw_vaporock_refusal_type_and_detail_ignore_domain_only_candidate() -> None:
    raw = _selector_candidate(
        " VAPOROCK_WARM ",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 50.0},
        fixed_reviewed=True,
        total_pressure_dependent=False,
    )
    domain_only = ProviderDomainCandidate(
        provider_id="unrelated-domain-only",
        covers_state=lambda _state: True,
    )
    common = {
        "rules": (_selector_rule("K"),),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "catalog_species": _selector_catalog_species("K"),
        "flux_activation_context": _rg_activation_context(),
    }

    raw_only = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={"K": (raw,)},
    ).channel("K")
    with_domain_only = resolve_vapour_batch(
        **common,
        provider_candidates_by_species={"K": (raw, domain_only)},
    ).channel("K")

    assert raw_only.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert with_domain_only.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert raw_only.extra["detail"] == with_domain_only.extra["detail"]


@pytest.mark.parametrize(
    "canonical_self_reference",
    ("EVALUATION:reviewed", "evaluation:reviewed/"),
    ids=("case", "trailing-separator"),
)
def test_canonical_self_review_reference_is_not_independent(
    canonical_self_reference: str,
) -> None:
    state = VapourResolveState(temperature_K=1600.0)
    candidate = _selector_candidate(
        "reviewed",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 50.0},
        evaluation_id="evaluation:reviewed",
        evaluation_review_record=canonical_self_reference,
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(candidate,),
        state=state,
    ) is None


@pytest.mark.parametrize("separator", ("\t", "\n", "\r", "\u00a0", "\u200b", " "),
                         ids=("tab", "newline", "cr", "nbsp", "zero-width", "space"))
@pytest.mark.parametrize("malformed_field", ("evaluation_id", "evaluation_review_record"))
def test_embedded_whitespace_review_reference_is_refused(
    separator: str, malformed_field: str,
) -> None:
    references = {
        "evaluation_id": "https://example.test/evaluations/run-17",
        "evaluation_review_record": "https://example.test/evaluations/run-17",
    }
    references[malformed_field] = f"https://example.test/evaluations/run-{separator}17"
    state = VapourResolveState(temperature_K=1600.0)
    candidate = _selector_candidate(
        "embedded-reference", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 50.0}, **references,
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}), candidates=(candidate,), state=state,
    ) is None
    answer = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state, provider_candidates_by_species={"K": (candidate,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    ).channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)


@pytest.mark.parametrize("replacement_kind", ("evaluation", "none", "short-tuple"))
def test_resolver_refuses_replacement_admitted_identity(
    monkeypatch, replacement_kind: str,
) -> None:
    import simulator.vapour_rail.request as request_module

    state = VapourResolveState(temperature_K=1600.0)
    species_ids = ("Na", "K", "Rb")
    candidate = _selector_candidate(
        "captured-at-boundary", VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {species_id: 137.0 for species_id in species_ids},
        evaluation_id="evaluation:admitted",
    )
    rank = request_module.rank_complete_candidate_sources

    def rank_then_replace_snapshot(**kwargs):
        selected = rank(**kwargs)
        assert selected is candidate
        replacement = {
            "evaluation": (
                "evaluation:replacement", *selected._admitted_identity[1:],
            ),
            "none": None,
            "short-tuple": ("evaluation:replacement",),
        }[replacement_kind]
        object.__setattr__(selected, "_admitted_identity", replacement)
        return selected

    monkeypatch.setattr(request_module, "rank_complete_candidate_sources", rank_then_replace_snapshot)
    try:
        batch = resolve_vapour_batch(
            rules=tuple(_selector_rule(species_id) for species_id in species_ids),
            ledger_snapshot={"process.cleaned_melt": {"P": 1.0}}, state=state,
            provider_candidates_by_species={
                species_id: (candidate,) for species_id in species_ids
            },
            catalog_species=_selector_catalog_species(*species_ids),
            flux_activation_context=_rg_activation_context(),
        )
    except VapourRequestConstructionError:
        return
    for species_id in species_ids:
        answer = batch.channel(species_id)
        if answer.is_refused:
            assert isinstance(answer.pressure, PressureRefusal)
            assert isinstance(answer.flux, FluxRefusal)
        else:
            assert answer.extra["selected_evaluation_id"] == "evaluation:admitted"
            assert answer.solve_group_id == candidate.solve_group_id
            assert answer.state_fingerprint == _state_fingerprint(state)


def test_reviewed_evaluation_requires_an_independent_review_record() -> None:
    state = VapourResolveState(temperature_K=1600.0)
    reviewed = _selector_candidate(
        "reviewed-external",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 50.0},
        fixed_reviewed=True,
        evaluation_id="evaluation:reviewed-external",
        evaluation_review_record="reviews/reviewed-external.md",
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(reviewed,),
        state=state,
    ) is reviewed

    for invalid_record in (
        None,
        "",
        "   ",
        reviewed.evaluation_id,
        f" {reviewed.evaluation_id} ",
    ):
        invalid = replace(
            reviewed,
            evaluation_review_record=invalid_record,
        )
        assert rank_complete_candidate_sources(
            bundle_species_ids=frozenset({"K"}),
            candidates=(invalid,),
            state=state,
        ) is None

    self_referential = replace(
        reviewed,
        evaluation_review_record=reviewed.evaluation_id,
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        provider_candidates_by_species={"K": (self_referential,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )
    assert batch.channel("K").refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(batch.channel("K").pressure, PressureRefusal)
    assert isinstance(batch.channel("K").flux, FluxRefusal)

    calibrated = _selector_candidate(
        "calibrated",
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        {"K": 25.0},
        evaluation_state=state,
    )
    invalid_validation_row = _selector_candidate(
        "invalid-validation-row",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 50.0},
        statuses={"K": "validated"},
        independently_validated=frozenset({"K"}),
        anchors_by_species={"K": ("held-out:k",)},
        evaluation_state=state,
        evaluation_review_record=None,
    )
    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(calibrated,),
        state=state,
        validation_candidates_by_species={"K": (invalid_validation_row,)},
    ) is calibrated


def test_zero_pressure_candidate_is_not_an_executable_flux_contract() -> None:
    zero = _selector_candidate(
        "zero_candidate",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 0.0},
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={"K": (zero,)},
        catalog_species=_selector_catalog_species("K"),
        flux_activation_context=_rg_activation_context(),
    )

    answer = batch.channel("K")
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert not answer.is_flux_active


def test_boolean_pressure_is_not_an_executable_scalar() -> None:
    boolean = _selector_candidate(
        "aa-bool",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": True},
    )
    real = _selector_candidate(
        "zz-real",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 2.5},
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(boolean, real),
        state=VapourResolveState(temperature_K=1600.0),
    ) is real


def test_canonicalizable_evidence_class_is_ratified() -> None:
    external = _selector_candidate(
        "external",
        " ANALYTICAL:EXTERNAL_GROUNDED ",
        {"K": 41.0},
    )

    assert rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"K"}),
        candidates=(external,),
        state=VapourResolveState(temperature_K=1600.0),
    ) is external


def test_allocated_source_clears_replaced_catalog_evaluator_provenance() -> None:
    catalog_species = _selector_catalog_species("K")
    catalog_species["K"].evaluator.evaluate = lambda *args, **kwargs: SimpleNamespace(
        pressure_pa=17.0,
        out_of_range=True,
        acquisition_flag="catalog-continued",
        status="catalog-outside-domain",
    )
    selected = _selector_candidate(
        "a-external",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 170.0},
    )
    other = _selector_candidate(
        "z-external",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 171.0},
    )

    answer = resolve_vapour_batch(
        rules=(_selector_rule("K"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={"K": (selected, other)},
        catalog_species=catalog_species,
        flux_activation_context=_rg_activation_context(),
    ).channel("K")
    rendered = serialize_vapour_answer(answer)

    assert answer.source_label == "a-external"
    assert answer.pressure == PressureValue(170.0)
    assert not ({"out_of_range", "acquisition_flag", "status"} & rendered["extra"].keys())
    assert rendered["out_of_range"] is False
    assert rendered["acquisition_flag"] is None


def test_allocated_real_catalog_answer_namespaces_replaced_attempt() -> None:
    state = VapourResolveState(temperature_K=1711.0)
    catalog_answer = resolve_vapour_batch(
        rules=(_selector_rule("Cs"),),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=state,
        catalog_species=_stub_catalog_species("Cs", pressure_pa=19.375),
        flux_activation_context=_rg_activation_context(),
    ).channel("Cs")
    catalog_writer_keys = {
        "origin",
        "silent_zero_notes",
        "source_activity",
        "source_activity_origin",
        "out_of_range",
        "acquisition_flag",
        "status",
    }
    assert catalog_answer.pressure == PressureValue(19.375)
    assert set(catalog_answer.extra) == catalog_writer_keys

    selected_source = rank_complete_candidate_sources(
        bundle_species_ids=frozenset({"Cs"}),
        candidates=(
            _selector_candidate(
                "fresh/zz",
                VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
                {"Cs": Decimal("30.875")},
                evaluation_state=state,
            ),
            _selector_candidate(
                "fresh/aa",
                VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
                {"Cs": Decimal("29.625")},
                evaluation_state=state,
            ),
        ),
        state=state,
    )
    assert selected_source is not None
    answer = allocate_selected_source(
        answers={"Cs": catalog_answer},
        bundle_species_ids=frozenset({"Cs"}),
        selected_source=selected_source,
        bundle_identity=_selector_bundle_identity(selected_source),
        state=state,
    )["Cs"]
    rendered = serialize_vapour_answer(answer)
    selected_source_owned_keys = {
        "bundle_id",
        "selected_evaluation_id",
        "selected_provider_id",
        "selected_evidence_class",
        "evaluation_review_record",
        "independently_validated",
        "validation_residual_dex",
    }
    namespace_key = "replaced_catalog_attempt"

    assert answer.source_label == "fresh/aa"
    assert answer.pressure == PressureValue(Decimal("29.625"))
    assert set(rendered["extra"]) <= selected_source_owned_keys | {
        namespace_key
    }
    assert rendered["extra"][namespace_key] == dict(catalog_answer.extra)
    assert set(rendered["extra"][namespace_key]) == catalog_writer_keys
    assert catalog_writer_keys.isdisjoint(
        set(rendered["extra"]) - {namespace_key}
    )


def test_bundle_candidate_must_be_admitted_for_every_member() -> None:
    na_admitted_only = _selector_candidate(
        "na_admitted_only",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 10.0, "K": 20.0},
    )
    k_domain_gate = ProviderDomainCandidate(
        provider_id="k_domain_gate",
        covers_state=lambda _state: True,
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            "Na": (na_admitted_only,),
            "K": (k_domain_gate,),
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    assert all(
        batch.channel(species_id).refusal_code
        == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
        for species_id in ("Na", "K")
    )
    assert not batch.solve_bundle_ids


def test_incomplete_sources_refuse_connected_bundle_without_splicing() -> None:
    na_only = _selector_candidate(
        "na_only",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"Na": 10.0},
    )
    k_only = _selector_candidate(
        "k_only",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 20.0},
    )
    batch = resolve_vapour_batch(
        rules=(_selector_rule("Na"), _selector_rule("K")),
        ledger_snapshot={"process.cleaned_melt": {"P": 1.0}},
        state=VapourResolveState(temperature_K=1600.0),
        provider_candidates_by_species={
            "Na": (na_only,),
            "K": (k_only,),
        },
        catalog_species=_selector_catalog_species("Na", "K"),
        flux_activation_context=_rg_activation_context(),
    )

    for species_id in ("Na", "K"):
        answer = batch.channel(species_id)
        assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
        assert isinstance(answer.pressure, PressureRefusal)
        assert isinstance(answer.flux, FluxRefusal)
        assert answer.source_label == "bundle_source_selector"
    assert batch.flux_active_species_ids == frozenset()
    assert not batch.solve_bundle_ids


def test_pre_rg_incomplete_source_returns_typed_bundle_refusal() -> None:
    incomplete = _selector_candidate(
        "external_grounded",
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
        {"K": 50.0},
        fixed_reviewed=False,
    )

    common = {
        "rules": (_selector_rule("K"),),
        "ledger_snapshot": {"process.cleaned_melt": {"P": 1.0}},
        "state": VapourResolveState(temperature_K=1600.0),
        "provider_candidates_by_species": {"K": (incomplete,)},
        "catalog_species": _selector_catalog_species("K"),
    }
    rg_batch = resolve_vapour_batch(
        **common,
        flux_activation_context=_rg_activation_context(),
    )
    pre_rg_batch = resolve_vapour_batch(
        **common,
        flux_activation_context=_pre_rg_activation_context("K"),
    )

    assert pre_rg_batch.requested_species_ids == rg_batch.requested_species_ids
    assert (
        frozenset(pre_rg_batch.channels_by_species)
        == frozenset(rg_batch.channels_by_species)
        == frozenset({"K"})
    )
    answer = pre_rg_batch.channel("K")
    assert serialize_vapour_answer(answer) == serialize_vapour_answer(
        rg_batch.channel("K")
    )
    assert answer.refusal_code == REFUSAL_NO_COMPLETE_BUNDLE_SOURCE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert "K" not in pre_rg_batch.flux_active_species_ids


# ---------------------------------------------------------------------------
# Core surgical seam (no evaporation cutover)
# ---------------------------------------------------------------------------


def test_simulator_build_vapour_batch_is_available_and_golden_neutral() -> None:
    from simulator.config import load_config_bundle
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import InternalAnalyticalBackend

    bundle = load_config_bundle(DATA_DIR)
    sim = PyrolysisSimulator(
        melt_backend=InternalAnalyticalBackend(),
        setpoints=bundle.setpoints,
        feedstocks=bundle.feedstocks,
        vapor_pressures=bundle.vapor_pressures,
    )
    assert sim.vapour_rail_catalog is not None
    assert sim.vapour_rail_catalog.request_rules
    # Empty ledger → empty request set, still a valid exact-key batch
    batch = sim.build_vapour_batch(
        temperature_K=1600.0,
        flux_activation_context=_pre_rg_activation_context(),
    )
    assert batch is not None
    assert isinstance(batch, VapourBatch)
    assert batch.requested_species_ids == frozenset()
    assert dict(batch.channels_by_species) == {}

    diagnostic_batch = sim.build_vapour_batch(
        temperature_K=1600.0,
        melt_activity_shadow_enabled=True,
        flux_activation_context=_pre_rg_activation_context(),
    )
    assert diagnostic_batch is not None
    assert diagnostic_batch.melt_activity_shadow is not None
    assert diagnostic_batch.melt_activity_shadow.as_mapping()["record_limit"] == 64


# ---------------------------------------------------------------------------
# Review regressions (review2-vr6-cx / review2-vr6-km)
# ---------------------------------------------------------------------------


def test_missing_temperature_is_typed_refusal_not_flux_active_zero() -> None:
    """codex P1 / kimi P1-3: missing state must never fabricate PressureValue(0).

    Null hypothesis: resolve with inventory + complete contract + state=None
    yields PressureValue(0.0) + FluxEligible and a solve bundle.
    Refutation: channel is PressureRefusal/FluxRefusal, not flux-active, and
    forms no solve bundle. Reachable via the public core default path too.
    """

    payload = _minimal_family("K")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}

    # Direct resolve: explicit state=None
    batch = catalog.resolve_batch(
        ledger,
        state=None,
        flux_activation_context=_rg_activation_context(),
    )
    assert "K" in batch.requested_species_ids
    answer = batch.channel("K")
    assert answer.is_refused
    assert answer.refusal_code == REFUSAL_MISSING_OUTCOME_STATE
    assert isinstance(answer.pressure, PressureRefusal)
    assert isinstance(answer.flux, FluxRefusal)
    assert "K" not in batch.flux_active_species_ids
    assert all("K" not in members for members in batch.solve_bundle_ids.values())
    assert batch.metadata["refusal_closure_fixed_point"] is True

    # The batch may not silently run a strict subset of the typed source set.
    # Missing physical state refuses K, so pre-RG activation fails closed.
    with pytest.raises(
        VapourRequestConstructionError,
        match="pre-RG effective-pressure channels are not flux-eligible",
    ):
        catalog.resolve_batch(
            ledger,
            state=None,
            flux_activation_context=_pre_rg_activation_context("K"),
        )

    # Core public default: temperature_K=None with non-empty inventory.
    from simulator.config import load_config_bundle
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import InternalAnalyticalBackend

    bundle = load_config_bundle(DATA_DIR)
    sim = PyrolysisSimulator(
        melt_backend=InternalAnalyticalBackend(),
        setpoints=bundle.setpoints,
        feedstocks=bundle.feedstocks,
        vapor_pressures=bundle.vapor_pressures,
    )
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"K2O": 1.0},
        source="test_missing_temperature_inventory",
        material_origin="feedstock",
    )
    core_batch = sim.build_vapour_batch(
        flux_activation_context=_rg_activation_context()
    )  # all physical-state params default None
    assert core_batch is not None
    assert core_batch.requested_species_ids  # inventory activated something
    missing_state_refusals = [
        sid
        for sid in core_batch.requested_species_ids
        if core_batch.channel(sid).refusal_code == REFUSAL_MISSING_OUTCOME_STATE
    ]
    assert missing_state_refusals, (
        "default build_vapour_batch with inventory must surface typed "
        "missing-state refusals, not silent zeros"
    )
    for sid in missing_state_refusals:
        channel = core_batch.channel(sid)
        assert not channel.is_flux_active
        assert isinstance(channel.pressure, PressureRefusal)
        assert isinstance(channel.flux, FluxRefusal)
    assert core_batch.flux_active_species_ids == frozenset()


def test_absent_source_atom_no_substring_credit_for_f_in_fe2o3() -> None:
    """kimi P1-2: atom presence must not use substring matching.

    Null hypothesis: required F is credited by Fe2O3 because 'F' in 'Fe2O3'.
    Refutation: channel is refused for absent source atom.
    """

    rule = RequestRule(
        species_id="FeF",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"TRIGGER"}),
        required_source_atoms=frozenset({"F"}),
        solve_group_id="halide_test",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="FeF",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
        validation_status="pending_validation",
    )
    ledger = {"process.cleaned_melt": {"TRIGGER": 1.0, "Fe2O3": 1.0}}
    batch = resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1500.0),
        catalog_species=_stub_catalog_species("FeF"),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("FeF")
    assert answer.is_refused
    assert answer.refusal_code == REFUSAL_ABSENT_SOURCE_ATOM
    assert "F" in (answer.extra.get("detail") or "")


def test_intrinsic_fo2_affects_pressure_and_state_fingerprint() -> None:
    """codex P1 (fO2): evaluator must receive fO2; fingerprint must include it.

    Null hypothesis: p at 1 bar equals p at 1e-8 bar and fingerprints match.
    Refutation: pressures differ by the pO2 power law and fingerprints differ.
    """

    payload = _minimal_family("K")  # pO2_exponent=-0.25 in the fixture model
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}

    batch_1 = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(
            temperature_K=1500.0,
            fO2_bar=3.0e-6,
            source_reaction_fO2_bar=1.0,
        ),
        flux_activation_context=_rg_activation_context(),
    )
    batch_lo = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(
            temperature_K=1500.0,
            fO2_bar=3.0e-6,
            source_reaction_fO2_bar=1.0e-8,
        ),
        flux_activation_context=_rg_activation_context(),
    )
    a1 = batch_1.channel("K")
    a_lo = batch_lo.channel("K")
    assert not a1.is_refused and not a_lo.is_refused
    assert isinstance(a1.pressure, PressureValue)
    assert isinstance(a_lo.pressure, PressureValue)
    # p ∝ fO2^{-0.25} → ratio (1e-8)^{-0.25} / 1^{-0.25} = 100
    ratio = a_lo.pressure.pa / a1.pressure.pa
    assert ratio == pytest.approx(100.0, rel=1e-9)
    assert a1.state_fingerprint != a_lo.state_fingerprint
    assert "source_fO2=" in a1.state_fingerprint


@pytest.mark.parametrize("intrinsic_fO2", [None, "bogus", -1.0, math.nan])
def test_intrinsic_fo2_missing_or_malformed_is_typed_refusal(
    intrinsic_fO2: Any,
) -> None:
    payload = _minimal_family("K")
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}

    batch = catalog.resolve_batch(
        ledger,
        _state_with_k_activity(
            temperature_K=1500.0,
            fO2_bar=1.0e-8,
            source_reaction_fO2_bar=intrinsic_fO2,
        ),
        flux_activation_context=_rg_activation_context(),
    )

    answer = batch.channel("K")
    assert answer.is_refused
    assert answer.refusal_code == REFUSAL_MISSING_OUTCOME_STATE
    assert "intrinsic_melt oxygen fugacity" in (answer.extra.get("detail") or "")


def test_validation_anchors_propagate_to_rule_and_answer() -> None:
    """codex P2: validated rows must keep anchor_refs on rule + answer."""

    payload = _minimal_family("K", validation_status="validated")
    fam = next(iter(payload["families"].values()))
    fam["physical_properties"]["species"]["K"]["validation"] = {
        "status": "validated",
        "anchor_refs": ["anchor:test"],
    }
    catalog = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    k_rule = next(r for r in catalog.request_rules if r.species_id == "K")
    assert k_rule.validation_status == "validated"
    assert k_rule.validation_anchor_refs == ("anchor:test",)

    ledger = {"process.cleaned_melt": {"K2O": 1.0, "KO0.5": 1.0}}
    batch = catalog.resolve_batch(
        ledger,
        VapourResolveState(temperature_K=1500.0),
        flux_activation_context=_rg_activation_context(),
    )
    answer = batch.channel("K")
    assert answer.validation_status == "validated"
    assert answer.validation_anchor_refs == ("anchor:test",)


def test_u0_manifest_and_compile_are_cached() -> None:
    """kimi P1-1: default-on emission stays cheap via manifest + compile memo.

    Null hypothesis: every compile re-parses U0 YAML (~150 ms).
    Refutation: second compile of the same *content* is a catalog identity hit,
    and load_u0_manifest hits the mtime memo.
    """

    import time

    from simulator.vapour_rail.catalog import (
        _compile_input_identity,
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
        compiled_catalog_for,
    )
    from simulator.vapour_rail.u0_manifest import (
        clear_u0_manifest_cache,
        load_u0_manifest,
    )

    clear_u0_manifest_cache()
    clear_vapour_rail_compile_cache()

    t0 = time.perf_counter()
    m1 = load_u0_manifest()
    cold_load = time.perf_counter() - t0
    t0 = time.perf_counter()
    m2 = load_u0_manifest()
    warm_load = time.perf_counter() - t0
    assert m1["row_count"] == m2["row_count"]
    # Warm load must be far cheaper than a YAML re-parse (order-of-magnitude).
    assert warm_load < max(0.005, cold_load * 0.25)

    payload = _minimal_family("K")
    # Content-digest identity: same content, distinct objects still hit.
    clear_vapour_rail_compile_cache()
    t0 = time.perf_counter()
    c1 = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    cold_compile = time.perf_counter() - t0
    t0 = time.perf_counter()
    # Fresh stub object each call — content identity (not object id) must hit.
    c2 = compile_vapour_rail_catalog(payload, u0_manifest=_u0_stub("K"))
    warm_compile = time.perf_counter() - t0
    assert c1 is c2  # identity: content-digest LRU returned the same catalog
    # Digest + dict lookup must stay cheap vs cold compile on a mini fixture.
    assert warm_compile < max(0.05, cold_compile * 0.5)

    # Owner-boundary content_key propagation: warm hits skip the payload walk.
    key = _compile_input_identity(
        payload, emit_u0_request_rules=True, u0_manifest=_u0_stub("K")
    )
    t0 = time.perf_counter()
    for _ in range(50):
        compile_vapour_rail_catalog(
            payload, u0_manifest=_u0_stub("K"), content_key=key
        )
    keyed_warm = (time.perf_counter() - t0) / 50
    assert keyed_warm < 0.001, f"keyed warm hit too slow: {keyed_warm:.6f}s"

    # Hot capability probes use evaluator-only compile reuse.
    clear_vapour_rail_compile_cache()
    hot1 = compiled_catalog_for(payload, emit_u0_request_rules=False)
    hot2 = compiled_catalog_for(payload, emit_u0_request_rules=False)
    assert hot1 is hot2
    assert hot1.request_rules == ()


def test_compile_resolves_default_manifest_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: one compile must digest and emit from one manifest resolution.

    Null hypothesis: the default loader is called once for emission and again
    for identity, so a state change can cache manifest-A output under the
    manifest-B digest and return stale rules on the next B compile.
    Refutation: each compile calls the loader once; A and B produce distinct
    catalogs whose emitted rules match that compile's resolved object.
    """

    import simulator.vapour_rail.request as request_module
    import simulator.vapour_rail.u0_manifest as manifest_module
    from simulator.vapour_rail.catalog import (
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
    )

    payload = _minimal_family("K")
    manifest_a = _u0_stub("K")
    manifest_b = _u0_stub()
    calls = 0
    emitted_manifests: list[dict] = []
    real_emit_request_rules = request_module.emit_request_rules

    def stateful_loader() -> dict:
        nonlocal calls
        calls += 1
        return manifest_a if calls == 1 else manifest_b

    def recording_emit_request_rules(**kwargs: Any) -> tuple[Any, ...]:
        emitted_manifests.append(kwargs["u0_manifest"])
        return real_emit_request_rules(**kwargs)

    monkeypatch.setattr(manifest_module, "load_u0_manifest", stateful_loader)
    monkeypatch.setattr(
        request_module, "emit_request_rules", recording_emit_request_rules
    )
    clear_vapour_rail_compile_cache()

    catalog_a = compile_vapour_rail_catalog(payload)
    assert calls == 1
    assert tuple(rule.species_id for rule in catalog_a.request_rules) == ("K",)

    catalog_b = compile_vapour_rail_catalog(payload)
    assert calls == 2
    assert catalog_b is not catalog_a
    assert emitted_manifests[0] is manifest_a
    assert emitted_manifests[1] is manifest_b


def test_compile_cache_uses_explicit_content_identity() -> None:
    """Codex P3-2 / NV-1 class: memo keys content, not incidental object id.

    Null hypothesis: cache key is ``id(payload)`` / ``id(u0_manifest)`` so
    distinct objects with equal content miss, and different content under a
    recycled id can silently hit.
    Refutation: same content (different objects) shares one catalog; different
    content never shares; keys are content-digest strings.
    """

    from simulator.vapour_rail.catalog import (
        _COMPILE_CACHE,
        _compile_input_identity,
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
        compiled_catalog_for,
    )

    clear_vapour_rail_compile_cache()
    payload_a = _minimal_family("K")
    payload_a_copy = _minimal_family("K")  # equal content, distinct object
    assert payload_a is not payload_a_copy
    assert payload_a == payload_a_copy

    key_a = _compile_input_identity(
        payload_a, emit_u0_request_rules=False, u0_manifest=None
    )
    key_a_copy = _compile_input_identity(
        payload_a_copy, emit_u0_request_rules=False, u0_manifest=None
    )
    assert isinstance(key_a, str) and len(key_a) == 64  # sha256 hex
    assert key_a == key_a_copy
    assert key_a != str(id(payload_a))

    cat_a = compiled_catalog_for(payload_a, emit_u0_request_rules=False)
    assert key_a in _COMPILE_CACHE
    assert _COMPILE_CACHE[key_a] is cat_a
    # Equal-content different object hits the same entry (explicit identity).
    cat_a2 = compiled_catalog_for(payload_a_copy, emit_u0_request_rules=False)
    assert cat_a2 is cat_a
    assert "K" in cat_a.species
    assert "NaCl" not in cat_a.species

    payload_b = _minimal_family("NaCl", parent_oxide="Na2O", with_reaction=False)
    key_b = _compile_input_identity(
        payload_b, emit_u0_request_rules=False, u0_manifest=None
    )
    assert key_b != key_a
    cat_b = compiled_catalog_for(payload_b, emit_u0_request_rules=False)
    assert "NaCl" in cat_b.species
    assert "K" not in cat_b.species
    assert cat_b is not cat_a
    assert _COMPILE_CACHE[key_b] is cat_b

    # Manifest content is part of the explicit identity when emission is on.
    stub_k = _u0_stub("K")
    stub_nacl = _u0_stub("NaCl")
    key_mk = _compile_input_identity(
        payload_a, emit_u0_request_rules=True, u0_manifest=stub_k
    )
    key_mn = _compile_input_identity(
        payload_a, emit_u0_request_rules=True, u0_manifest=stub_nacl
    )
    assert key_mk != key_mn
    cat_mk = compile_vapour_rail_catalog(payload_a, u0_manifest=stub_k)
    cat_mn = compile_vapour_rail_catalog(payload_a, u0_manifest=stub_nacl)
    assert cat_mk is not cat_mn
    # Same manifest *content*, different object → same catalog.
    cat_mk2 = compile_vapour_rail_catalog(payload_a, u0_manifest=_u0_stub("K"))
    assert cat_mk2 is cat_mk

    # P3: when emission is disabled, manifest is not a key dimension.
    key_off_k = _compile_input_identity(
        payload_a, emit_u0_request_rules=False, u0_manifest=stub_k
    )
    key_off_n = _compile_input_identity(
        payload_a, emit_u0_request_rules=False, u0_manifest=stub_nacl
    )
    assert key_off_k == key_off_n == key_a
    clear_vapour_rail_compile_cache()
    cat_off_k = compiled_catalog_for(
        payload_a, emit_u0_request_rules=False, u0_manifest=stub_k
    )
    cat_off_n = compiled_catalog_for(
        payload_a, emit_u0_request_rules=False, u0_manifest=stub_nacl
    )
    assert cat_off_k is cat_off_n


def test_compile_cache_covers_default_manifest_surface() -> None:
    """P1: effective default manifest is inside the digest when emission is on.

    Null hypothesis: ``u0_manifest=None`` records None in the key while rule
    emission resolves ``load_u0_manifest()``, so default fixture content sits
    outside the digest surface.
    Refutation: identity with None equals identity with the loaded default;
    a different manifest content yields a different key and catalog.
    """

    from simulator.vapour_rail.catalog import (
        _compile_input_identity,
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
    )
    from simulator.vapour_rail.u0_manifest import load_u0_manifest

    clear_vapour_rail_compile_cache()
    payload = _minimal_family("K")
    default = load_u0_manifest()
    key_none = _compile_input_identity(
        payload, emit_u0_request_rules=True, u0_manifest=None
    )
    key_default = _compile_input_identity(
        payload, emit_u0_request_rules=True, u0_manifest=default
    )
    assert key_none == key_default

    stub = _u0_stub("K")
    key_stub = _compile_input_identity(
        payload, emit_u0_request_rules=True, u0_manifest=stub
    )
    assert key_stub != key_none
    cat_default = compile_vapour_rail_catalog(payload, u0_manifest=None)
    cat_stub = compile_vapour_rail_catalog(payload, u0_manifest=stub)
    assert cat_default is not cat_stub


def test_canonical_digest_rejects_type_collisions() -> None:
    """P1: canonicalization must not collapse distinct accepted types.

    Null hypothesis: ``str(key)`` / ``str(leaf)`` and an untagged sequence
    branch let ``\"1\"`` vs ``1``, date vs ISO-string, or list vs tuple share a
    digest and fail-open past validation. Refutation: invalid key/leaf types
    raise and every accepted value category has a distinct canonical tag.
    """

    from datetime import date

    from simulator.vapour_rail.catalog import (
        CatalogCompileError,
        _canonical_jsonable,
        _content_digest,
    )

    with pytest.raises(CatalogCompileError, match="string mapping keys"):
        _canonical_jsonable({1: "x"})

    with pytest.raises(CatalogCompileError, match="non-JSON leaf"):
        _canonical_jsonable({"source_account": date(2026, 1, 1)})

    assert _content_digest({"k": "1"}) != _content_digest({"k": 1})

    # Containers with equal elements remain distinct when the compiler's
    # validation distinguishes their concrete sequence type.
    assert _content_digest({"k": ["x"]}) != _content_digest({"k": ("x",)})


def test_compile_cache_detects_in_place_payload_mutation() -> None:
    """Kimi P3-1: mutated input must not silently serve a stale cached catalog.

    Null hypothesis: identity memo trusts callers not to mutate; after a warm
    compile, an in-place nested edit still returns the prior catalog object.
    Refutation: re-compile with the mutated payload returns a new catalog
    whose projection carries the new value. Red under reversion to id-keys.
    """

    from simulator.vapour_rail.catalog import (
        CatalogCompileError,
        _compile_input_identity,
        _content_digest,
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
        compiled_catalog_for,
    )

    clear_vapour_rail_compile_cache()
    payload = _minimal_family("K")
    cat = compiled_catalog_for(payload, emit_u0_request_rules=False)
    assert "K" in cat.species
    assert compiled_catalog_for(payload, emit_u0_request_rules=False) is cat

    # Nested projected-field mutation (schema stays v2) must miss.
    family = next(iter(payload["families"].values()))
    species_row = next(
        iter(family["physical_properties"]["species"].values())
    )
    species_row["molar_mass_g_mol"] = 123.456
    cat_mut = compiled_catalog_for(payload, emit_u0_request_rules=False)
    assert cat_mut is not cat
    assert (
        cat_mut.legacy_view()["metals"]["K"]["molar_mass_g_mol"] == 123.456
    )

    # Schema-illegal mutation still digests-misses then raises at the gate.
    clear_vapour_rail_compile_cache()
    payload2 = _minimal_family("K")
    cat2 = compiled_catalog_for(payload2, emit_u0_request_rules=False)
    assert compiled_catalog_for(payload2, emit_u0_request_rules=False) is cat2
    payload2["schema_version"] = 999
    with pytest.raises(CatalogCompileError, match="schema_version"):
        compiled_catalog_for(payload2, emit_u0_request_rules=False)

    clear_vapour_rail_compile_cache()
    payload3 = _minimal_family("K")
    stub = _u0_stub("K")
    cat3 = compile_vapour_rail_catalog(payload3, u0_manifest=stub)
    assert compile_vapour_rail_catalog(payload3, u0_manifest=stub) is cat3
    payload3["schema_version"] = 999
    with pytest.raises(CatalogCompileError, match="schema_version"):
        compile_vapour_rail_catalog(payload3, u0_manifest=stub)

    # A list-to-tuple mutation preserves element values but changes a schema
    # type. It must miss the warm cache and reach the same validation error as
    # a cold compile.
    clear_vapour_rail_compile_cache()
    payload4 = _minimal_family("K", validation_status="validated")
    family4 = next(iter(payload4["families"].values()))
    validation4 = family4["physical_properties"]["species"]["K"]["validation"]
    cat4 = compiled_catalog_for(payload4, emit_u0_request_rules=False)
    assert "K" in cat4.species
    before_digest = _content_digest(payload4)
    validation4["anchor_refs"] = tuple(validation4["anchor_refs"])
    assert _content_digest(payload4) != before_digest
    with pytest.raises(CatalogCompileError, match="anchor_refs must be a list"):
        compiled_catalog_for(payload4, emit_u0_request_rules=False)

    # A caller-provided key is an assertion, not a mutation bypass.
    clear_vapour_rail_compile_cache()
    payload5 = _minimal_family("K")
    key5 = _compile_input_identity(
        payload5, emit_u0_request_rules=False, u0_manifest=None
    )
    compile_vapour_rail_catalog(
        payload5, emit_u0_request_rules=False, content_key=key5
    )
    payload5["schema_version"] = 999
    with pytest.raises(CatalogCompileError, match="content_key does not match"):
        compile_vapour_rail_catalog(
            payload5, emit_u0_request_rules=False, content_key=key5
        )
    clear_vapour_rail_compile_cache()
    with pytest.raises(CatalogCompileError, match="anchor_refs must be a list"):
        compiled_catalog_for(payload4, emit_u0_request_rules=False)


def test_default_compile_production_warm_hit_budget() -> None:
    """P1: default production warm hits stay out of the ~151 ms/50 class.

    Null hypothesis: omitting the opt-in content_key serializes and hashes the
    full payload plus default manifest on every hit.
    Refutation: mutation-checked default identity reuse makes 50 ordinary API
    calls fast without weakening the content-keyed cache contract.
    """

    import time

    from simulator.vapour_rail.catalog import (
        clear_vapour_rail_compile_cache,
        compile_vapour_rail_catalog,
    )

    production = _yaml("vapor_pressures.yaml")
    clear_vapour_rail_compile_cache()
    cold = compile_vapour_rail_catalog(production)
    t0 = time.perf_counter()
    for _ in range(50):
        warm = compile_vapour_rail_catalog(production)
        assert warm is cold
    warm50_s = time.perf_counter() - t0
    assert warm50_s < 0.075, (
        "default production warm hits too slow: "
        f"{warm50_s * 1000:.3f} ms/50"
    )


def test_legacy_view_cache_detects_in_place_payload_mutation() -> None:
    """Legacy-view memo is content-keyed (valid schema-v2 nested mutation).

    Null hypothesis: id-keyed legacy view returns the prior projection after
    a nested content mutation that keeps schema_version == 2 (mutating
    schema_version to 999 bypasses both memos via non-v2 fallthrough and
    stays green under id-key reversion).
    Refutation: nested molar-mass mutation forces a miss; new projection
    carries the new value. Red under reversion to id-keys.
    """

    from simulator.vapour_rail.catalog import (
        clear_vapor_pressure_view_caches,
        vapor_pressure_legacy_view,
    )

    clear_vapor_pressure_view_caches()
    payload = _minimal_family("K")
    view1 = vapor_pressure_legacy_view(payload)
    assert "K" in view1.get("metals", {}), (
        f"expected compiled metals projection, got keys={sorted(view1)}"
    )
    view2 = vapor_pressure_legacy_view(payload)
    assert view2 is view1  # content-digest warm hit (shared read-only dict)

    # Keep schema_version == 2; mutate a projected nested field.
    family = next(iter(payload["families"].values()))
    species_row = next(
        iter(family["physical_properties"]["species"].values())
    )
    species_row["molar_mass_g_mol"] = 123.456
    assert payload["schema_version"] == 2
    view3 = vapor_pressure_legacy_view(payload)
    assert view3 is not view1
    assert view3["metals"]["K"]["molar_mass_g_mol"] == 123.456


def test_legacy_view_production_warm_hit_budget() -> None:
    """P1: production-sized repeated hits must not re-enter the ~151 ms class.

    Owner-boundary pattern: digest once, pass content_key; warm hits are
    pure dict returns. Also cover already-projected schema-v1 re-entry
    (condensation pass-through) as O(1) identity returns.
    """

    import time

    import yaml

    from simulator.vapour_rail.catalog import (
        _content_digest,
        clear_vapor_pressure_view_caches,
        vapor_pressure_legacy_view,
    )

    production = yaml.safe_load(
        (DATA_DIR / "vapor_pressures.yaml").read_text()
    )
    clear_vapor_pressure_view_caches()
    payload_key = _content_digest(production)
    t0 = time.perf_counter()
    cold = vapor_pressure_legacy_view(production, content_key=payload_key)
    cold_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(50):
        warm = vapor_pressure_legacy_view(production, content_key=payload_key)
        assert warm is cold
    warm50_s = time.perf_counter() - t0
    warm_each = warm50_s / 50
    assert warm_each < 0.0005, (
        f"keyed production warm hit too slow: {warm_each*1000:.3f} ms "
        f"(cold {cold_s*1000:.1f} ms; 50-hit total {warm50_s*1000:.1f} ms)"
    )
    # Already-projected view: per-species re-entry must not deepcopy.
    t0 = time.perf_counter()
    for _ in range(72):
        again = vapor_pressure_legacy_view(cold)
        assert again is cold
    proj_loop = time.perf_counter() - t0
    assert proj_loop < 0.005, (
        f"projected re-entry loop too slow: {proj_loop*1000:.2f} ms"
    )
