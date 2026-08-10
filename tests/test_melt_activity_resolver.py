from __future__ import annotations

import copy
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.fe_redox import (
    kress91_ferrous_feo_activity,
    kress91_furnace_activity_pressure_bar,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    ActivityRefusalCode,
    ActivityTier,
    ActivityVerdictKind,
    SourceReactionActivity,
    StandardStateIdentity,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.melt_activity_resolver import (
    MELT_ACTIVITY_SHADOW_RECORD_LIMIT,
    PROVEN_EMPTY_COMPONENT,
    SHADOW_COMPARABLE,
    SHADOW_NOT_COMPARABLE_YET,
    MeltActivityShadow,
    MeltActivityQuery,
    MeltActivityRegistry,
    MeltActivityRegistryError,
    MeltActivityResolver,
    ShadowComparison,
    TierAEngineInput,
    build_shadow_for_vapour_batch,
    complete_inventory_identity,
    crystalline_target_standard_state,
    ownerless_nonzero_reservoir_ids,
    validate_tier_b_candidate,
)
from simulator.vapour_rail.request import VapourResolveState
from simulator.vapour_rail.u0_manifest import load_u0_manifest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _tier_a_source_state(
    resolver: MeltActivityResolver, component_id: str
) -> StandardStateIdentity:
    row = resolver.registry.row_for_component(component_id, tier=ActivityTier.A)
    assert row is not None
    return resolver.registry.row_standard_state(row, "source_standard_state")


def _query(
    component_id: str,
    *,
    fractions: dict[str, float] | None = None,
    complete: bool = True,
    temperature_K: float = 1673.15,
    out_of_domain: bool = False,
    continuation_ln_band: tuple[float, float] | None = None,
    formula_basis: str | None = None,
) -> MeltActivityQuery:
    inventory = {
        "process.cleaned_melt": dict(fractions or {component_id: 1.0})
    }
    redox_pressure_bar = kress91_furnace_activity_pressure_bar()
    registry = MeltActivityRegistry.load()
    fe_row = registry.row_for_component("FeO", tier=ActivityTier.A)
    assert fe_row is not None
    redox_model_id = str(fe_row["source"]["engine_version"])
    redox_model_digest = registry.row_digest(str(fe_row["row_id"]))
    state_fp, inventory_digest, reservoirs, inventory_complete = (
        complete_inventory_identity(
            inventory,
            temperature_K=temperature_K,
            pressure_bar=1.0,
            intrinsic_fO2_log10=None,
            redox_model_pressure_bar=redox_pressure_bar,
            redox_basis_ref="intrinsic_melt_fO2_log10",
            redox_model_id=redox_model_id,
            redox_model_digest=redox_model_digest,
        )
    )
    assert inventory_complete is True
    return MeltActivityQuery(
        component_id=component_id,
        formula_basis=(
            formula_basis
            or (
                "Li2O_on_single_cation_basis"
                if component_id == "LiO0.5"
                else component_id
            )
        ),
        target_standard_state=registry.target_standard_state(component_id),
        temperature_K=temperature_K,
        pressure_bar=1.0,
        component_mole_fractions=fractions or {},
        composition_basis="named_test_mole_basis",
        ordered_reservoirs=reservoirs,
        inventory_digest=inventory_digest,
        inventory_complete=complete,
        state_fingerprint=state_fp,
        matrix_domain_ref="matrix_domain.silicate_melt.phase1.v1",
        assemblage_ref="test:liquid_melt",
        phase_kind="liquid_melt",
        consumed_reservoir_ids=tuple(
            reservoir.component_id for reservoir in reservoirs
        ),
        redox_model_pressure_bar=redox_pressure_bar,
        redox_basis_ref="intrinsic_melt_fO2_log10",
        redox_model_id=redox_model_id,
        redox_model_digest=redox_model_digest,
        out_of_domain=out_of_domain,
        continuation_ln_band=continuation_ln_band,
    )


def test_phase0_crystalline_target_self_check_passes_real_catalog_rows() -> None:
    registry = MeltActivityRegistry.load()
    check = registry.phase0_self_check(DATA / "vapor_pressures.yaml")

    assert check.passed is True
    assert check.catalog_rows_checked == 6
    assert check.pin_impacts_checked == 6
    assert check.failures == ()
    assert "{component_id}" in check.target_standard_state_family


def test_component_qualified_standard_states_do_not_hash_collide() -> None:
    ca = crystalline_target_standard_state("CaO")
    mg = crystalline_target_standard_state("MgO")

    assert ca.identity_id != mg.identity_id
    assert ca.component_id == "CaO"
    assert mg.component_id == "MgO"
    assert ca.fingerprint() != mg.fingerprint()


def test_complete_inventory_identity_marks_invalid_entries_incomplete() -> None:
    state_fp, inventory_digest, reservoirs, inventory_complete = (
        complete_inventory_identity(
            {"process.cleaned_melt": {"CaO": "not-a-number"}},
            temperature_K=1600.0,
            pressure_bar=1.0,
            intrinsic_fO2_log10=-8.0,
        )
    )

    assert inventory_complete is False
    assert reservoirs == ()
    assert state_fp.startswith("sha256:")
    assert inventory_digest.startswith("sha256:")


def test_fe_composition_participates_in_state_fingerprint() -> None:
    common = {
        "temperature_K": 1600.0,
        "pressure_bar": 1.0,
        "intrinsic_fO2_log10": -8.0,
        "redox_model_pressure_bar": 1.0,
        "redox_basis_ref": "intrinsic_melt_fO2_log10",
        "redox_model_id": "kress91",
        "redox_model_digest": "sha256:kress91",
    }
    first = complete_inventory_identity(
        {"process.cleaned_melt": {"FeO": 1.0}},
        **common,
        composition_wt_pct={"FeO": 12.0, "SiO2": 50.0},
    )
    second = complete_inventory_identity(
        {"process.cleaned_melt": {"FeO": 1.0}},
        **common,
        composition_wt_pct={"FeO": 6.0, "SiO2": 56.0},
    )

    assert first[0] != second[0]
    assert first[1] == second[1]


def test_registry_has_55_tier_c_dispositions_and_zero_tier_b_rows() -> None:
    registry = MeltActivityRegistry.load()
    inventory = registry.payload["tier_c_inventory"]

    assert len(inventory) == 55
    assert len({row["element"] for row in inventory}) == 55
    assert all(row["tier"] != "B" for row in registry.rows_by_id.values())
    assert registry.payload["tier_b_model_version"]["coefficient_rows"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.pop("model_family"),
        lambda row: row["target_standard_state"].update({"phase": "liquid"}),
        lambda row: row["source"].update(
            {"basis_coefficients": ["not-a-number"]}
        ),
        lambda row: row["domain"].update({"T_K": "not-a-range"}),
        lambda row: row["domain"].update({"P_bar": [2.0, 1.0]}),
        lambda row: row["band"].update(
            {"lower_offset": 1.0, "upper_offset": -1.0}
        ),
    ],
)
def test_registry_rejects_incomplete_or_wrong_state_rows(
    mutation, tmp_path: Path
) -> None:
    payload = copy.deepcopy(dict(MeltActivityRegistry.load().payload))
    ca_row = next(
        row
        for row in payload["model_rows"]
        if row["rail_component"]["id"] == "CaO"
    )
    mutation(ca_row)

    with pytest.raises(MeltActivityRegistryError):
        MeltActivityRegistry(payload, source_path=tmp_path / "registry.yaml")


def test_tier_a_ln_space_transform_agrees_across_both_engine_routes() -> None:
    resolver = MeltActivityResolver()
    query = _query("CaO", fractions={"CaO": 0.1}, temperature_K=1600.0)
    ln_engine = -2.0
    mu0_source = 100_000.0
    mu0_target = 95_000.0
    mixture_mu = mu0_source + GAS_CONSTANT * query.temperature_K * ln_engine
    result = resolver.resolve_engine_basis(
        query,
        TierAEngineInput(
            engine_component_ids=("CaO",),
            basis_coefficients=(1.0,),
            target_mu0_J_per_mol=mu0_target,
            engine_ln_activities=(ln_engine,),
            source_mu0_J_per_mol=(mu0_source,),
            mixture_mu_J_per_mol=(mixture_mu,),
            source_standard_state=_tier_a_source_state(resolver, "CaO"),
            conversion_ref="test:CaO:source-to-crystalline",
        ),
    )

    expected = ln_engine + (mu0_source - mu0_target) / (
        GAS_CONSTANT * query.temperature_K
    )
    assert result.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert result.tier is ActivityTier.A
    assert result.authority is False
    assert result.target_standard_state == crystalline_target_standard_state("CaO")
    assert result.ln_value == pytest.approx(expected, rel=0.0, abs=1.0e-12)
    assert result.value == pytest.approx(math.exp(expected), rel=1.0e-15)
    assert result.derivation["routes"] == [
        "engine_activity_plus_standard_state_offset",
        "absolute_mixture_potential",
    ]


@pytest.mark.parametrize("ln_activity", [-1000.0, 1000.0])
def test_tier_a_unrepresentable_legacy_edge_preserves_finite_ln(
    ln_activity: float,
) -> None:
    resolver = MeltActivityResolver()
    query = _query("CaO", fractions={"CaO": 0.1}, temperature_K=1600.0)

    result = resolver.resolve_engine_basis(
        query,
        TierAEngineInput(
            engine_component_ids=("CaO",),
            basis_coefficients=(1.0,),
            target_mu0_J_per_mol=0.0,
            engine_ln_activities=(ln_activity,),
            source_mu0_J_per_mol=(0.0,),
            source_standard_state=_tier_a_source_state(resolver, "CaO"),
        ),
    )

    assert result.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert result.value is None
    assert result.ln_value == ln_activity
    assert result.derivation["legacy_value_edge"] == "unrepresentable"


def test_tier_a_refuses_non_row_target() -> None:
    resolver = MeltActivityResolver()
    query = _query("CaO", fractions={"CaO": 0.1})
    query = MeltActivityQuery(
        **{
            **query.__dict__,
            "target_standard_state": StandardStateIdentity(
                convention="raoultian_pure_endmember",
                phase="liquid",
                reference_pressure_bar=1.0,
                component_basis="raoultian_pure_endmember",
                component_id="CaO",
            ),
        }
    )
    result = resolver.resolve_engine_basis(
        query,
        TierAEngineInput(
            engine_component_ids=("CaO",),
            basis_coefficients=(1.0,),
            target_mu0_J_per_mol=0.0,
            mixture_mu_J_per_mol=(0.0,),
            source_standard_state=_tier_a_source_state(resolver, "CaO"),
        ),
    )

    assert result.verdict is ActivityVerdictKind.REFUSAL
    assert result.refusal_code is ActivityRefusalCode.STANDARD_STATE_MISMATCH


@pytest.mark.parametrize("component_id", ["AlO1.5", "TiO2", "CrO1.5", "MnO"])
def test_t570_target_sidecars_remain_explicitly_unresolved(
    component_id: str,
) -> None:
    resolver = MeltActivityResolver()
    row = resolver.registry.row_for_component(component_id, tier=ActivityTier.A)
    assert row is not None
    result = resolver.resolve_engine_basis(
        _query(component_id),
        TierAEngineInput(
            engine_component_ids=tuple(row["source"]["engine_component_ids"]),
            basis_coefficients=tuple(row["source"]["basis_coefficients"]),
            target_mu0_J_per_mol=0.0,
            mixture_mu_J_per_mol=tuple(
                0.0 for _ in row["source"]["engine_component_ids"]
            ),
            source_standard_state=_tier_a_source_state(resolver, component_id),
        ),
    )

    assert result.verdict is ActivityVerdictKind.REFUSAL
    assert result.refusal_code is ActivityRefusalCode.STANDARD_STATE_UNRESOLVED


def test_tier_b_admission_skeleton_has_fixed_dof_and_no_species_intercept() -> None:
    candidate = {
        "row_id": "candidate.LiO0_5.v1",
        "rail_component": {
            "id": "LiO0.5",
            "oxidation_state": 1,
            "formula_multiplier": 2,
        },
        "primary_source": {
            "review_status": "reviewed",
            "citation": "test",
            "extract_locator": "test:1",
            "digest": "sha256:test",
        },
        "matrix_composition": {
            "basis": "mole_fraction",
            "components": ["SiO2"],
            "values": [1.0],
            "digest": "sha256:matrix",
        },
        "domain": {
            "T_K": [1500.0, 1700.0],
            "P_bar": [1.0, 1.0],
            "fO2_log10": [-10.0, -6.0],
            "redox_basis": "intrinsic_melt_fO2",
            "phase_requirement": "liquid_melt",
            "concentration_range": [0.0, 0.01],
            "descriptor_hull_ref": "hull:test",
        },
        "published_convention": {
            "standard_state_id": "source:test",
            "log_base": 10,
            "concentration_scale": "mol",
            "coefficient_convention": "ln_gamma_infinity",
        },
        "coefficient_identification": {
            "kind": "published_gamma_infinity",
            "source_series_ids": ["series:test"],
            "estimator_receipt": "sha256:estimator",
        },
        "apparatus": {"cell_material": "Pt", "method": "KEMS"},
        "uncertainty": {
            "marginal_sigma_ln": 1.0,
            "covariance_ref": "test",
            "shared_error_groups": [],
        },
        "canonical_conversion": {
            "target_standard_state_id": "rail:test",
            "receipt_digest": "sha256:conversion",
        },
        "validation": {
            "holdout_ids": ["h1"],
            "calibration_family_ids": ["p1", "p2", "p3"],
            "matrix_family_ids": ["m1", "m2"],
            "independent_solute_class_count": 18,
            "fitted_scalar_dof": 6,
            "design_matrix_digest": "sha256:design",
            "residual_band_ln": [-1.0, 1.0],
            "certification_ceiling": "status_bearing",
            "publication_holdout": {
                "holdout_family_ids": ["publication-holdout-1"],
                "p95_abs_delta_ln_activity": 2.0,
                "passed": True,
            },
            "solute_class_holdout": {
                "holdout_class_ids": ["solute-class-holdout-1"],
                "pooled_p95_abs_delta_ln_activity": 2.0,
                "abs_median_signed_delta_ln_activity": 0.5,
                "empirical_95_band_coverage": 0.9,
                "max_class_median_abs_delta_ln_activity": 2.0,
                "passed": True,
            },
        },
        "interaction_basis": ["SiO2"],
        "interaction_terms": [
            {
                "component_id": "SiO2",
                "epsilon_T": 0.0,
                "covariance_row_column_ref": "covariance:test:SiO2",
                "origin": "direct_fit",
                "identifying_source_series": "series:test",
                "estimator_receipt": "sha256:estimator",
                "covariance_ref": "test",
            }
        ],
        "descriptor_model": {
            "response_family": "ln_gamma_infinity",
            "coefficient_slots": [
                "beta_0",
                "beta_F",
                "beta_chi",
                "beta_q",
                "beta_Lambda",
                "beta_T",
            ],
        },
    }
    assert validate_tier_b_candidate(candidate) == ()

    candidate["descriptor_model"]["species_intercept"] = {"Li": 0.2}
    errors = validate_tier_b_candidate(candidate)
    assert "species-name intercepts are forbidden" in errors

    malformed = copy.deepcopy(candidate)
    malformed["descriptor_model"].pop("species_intercept")
    malformed["descriptor_model"]["response_family"] = "invented"
    malformed["descriptor_model"]["coefficient_slots"] = []
    malformed["validation"]["calibration_family_ids"] = ["only-one"]
    malformed["validation"]["holdout_ids"] = []
    malformed["validation"].pop("matrix_family_ids")
    malformed["validation"].pop("independent_solute_class_count")
    malformed["validation"].pop("publication_holdout")
    malformed["validation"].pop("solute_class_holdout")
    malformed["interaction_basis"] = []
    malformed["interaction_terms"] = []
    errors = validate_tier_b_candidate(malformed)
    assert "descriptor response family is not an admitted v1 equation" in errors
    assert "frozen design/holdouts/residual band/ceiling required" in errors
    assert "interaction_basis must be a nonempty ordered component list" in errors
    assert "interaction_terms must be a nonempty sequence" in errors


def test_tier_c_three_fail_closed_categories() -> None:
    resolver = MeltActivityResolver()

    missing = resolver.resolve_tier_c(
        _query(
            "LiO0.5",
            fractions={"LiO0.5": 0.2, "SiO2": 0.8},
            complete=False,
        )
    )
    assert missing.verdict is ActivityVerdictKind.REFUSAL
    assert missing.refusal_code is ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY

    continued = resolver.resolve_tier_c(
        _query(
            "LiO0.5",
            fractions={"LiO0.5": 0.2, "SiO2": 0.8},
            out_of_domain=True,
            continuation_ln_band=(-3.0, 4.0),
        )
    )
    assert continued.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert continued.domain_status == "out_of_domain_continuation_status_bearing"
    assert continued.ln_value == pytest.approx(math.log(0.2))
    assert continued.ln_band == (-3.0, 4.0)
    assert continued.authority is False

    proven_zero = resolver.resolve_tier_c(
        _query("LiO0.5", fractions={"LiO0.5": 0.0, "SiO2": 1.0})
    )
    assert proven_zero.verdict is ActivityVerdictKind.STATUS_BEARING_VALUE
    assert proven_zero.value == 0.0
    assert proven_zero.ln_value is None
    assert proven_zero.zero_because == PROVEN_EMPTY_COMPONENT

    forged = _query(
        "LiO0.5", fractions={"LiO0.5": 0.2, "SiO2": 0.8}
    )
    forged = MeltActivityQuery(
        **{
            **forged.__dict__,
            "component_mole_fractions": {"LiO0.5": 0.0, "SiO2": 1.0},
        }
    )
    refused = resolver.resolve_tier_c(forged)
    assert refused.verdict is ActivityVerdictKind.REFUSAL
    assert refused.refusal_code is ActivityRefusalCode.INCOMPLETE_MELT_INVENTORY


def test_unsupported_reservoir_refusals_are_visible_keys() -> None:
    resolver = MeltActivityResolver()
    results = resolver.unsupported_reservoir_results(
        state_fingerprint="sha256:state",
        inventory_digest="sha256:inventory",
        temperature_K=1673.15,
        pressure_bar=1.0,
    )

    expected_codes = {
        "CrO": ActivityRefusalCode.UNSUPPORTED_VALENCE_RESERVOIR,
        "TiO1.5": ActivityRefusalCode.UNSUPPORTED_VALENCE_RESERVOIR,
        "S2-_melt": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
        "SO4_melt": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
        "S_dissolved": ActivityRefusalCode.SULFUR_RESERVOIR_OWNER_MISSING,
        "F-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
        "Cl-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
        "Br-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
        "I-_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
        "NaCl_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
        "salt_melt": ActivityRefusalCode.HALIDE_RESERVOIR_OWNER_MISSING,
    }
    assert set(results) == set(expected_codes)
    for component_id, code in expected_codes.items():
        assert results[component_id].verdict is ActivityVerdictKind.REFUSAL
        assert results[component_id].refusal_code is code


def test_reduced_valence_and_compound_halides_are_ownerless_spectators() -> None:
    _, _, reservoirs, complete = complete_inventory_identity(
        {
            "process.cleaned_melt": {
                "CaO": 1.0,
                "CrO": 0.1,
                "TiO1.5": 0.1,
                "NaCl": 0.1,
                "CaF2": 0.1,
            }
        },
        temperature_K=1600.0,
        pressure_bar=1.0,
        intrinsic_fO2_log10=-8.0,
    )
    assert complete is True

    ownerless = ownerless_nonzero_reservoir_ids(reservoirs)
    assert ownerless == (
        "process.cleaned_melt:CaF2",
        "process.cleaned_melt:CrO",
        "process.cleaned_melt:NaCl",
        "process.cleaned_melt:TiO1.5",
    )


def test_supported_shadow_refuses_nonzero_reduced_valence_spectator() -> None:
    standard = StandardStateIdentity(
        convention="raoultian_pure_endmember",
        phase="liquid",
        reference_pressure_bar=1.0,
        component_basis="raoultian_pure_endmember",
    )
    rule = SimpleNamespace(
        species_id="Ca",
        source_reaction_activity=ActivityInputDeclaration(
            component_id="CaO",
            standard_state=standard,
            activity_model="provider_reported_thermodynamic_activity",
            allow_henrian_upper_bound=False,
            require_assemblage_match=False,
        ),
    )
    shadow = build_shadow_for_vapour_batch(
        rules=(rule,),
        ledger_snapshot={"process.cleaned_melt": {"CaO": 1.0, "CrO": 0.1}},
        state=VapourResolveState(
            temperature_K=1600.0,
            total_pressure_Pa=100_000.0,
            source_reaction_activities={"Ca": 0.4},
        ),
    )

    result = shadow.results_by_component["CaO"]
    assert result.verdict is ActivityVerdictKind.REFUSAL
    assert result.refusal_code is ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT


def test_source_reaction_activity_round_trip_preserves_ln_contract() -> None:
    resolver = MeltActivityResolver()
    result = resolver.resolve_tier_c(
        _query("LiO0.5", fractions={"LiO0.5": 0.25, "SiO2": 0.75})
    )
    restored = SourceReactionActivity.from_mapping(result.as_mapping())

    assert restored == result
    assert restored.ln_value == math.log(0.25)


def test_real_file_shadow_pin_gate_types_unavailable_and_degraded_populations() -> None:
    manifest = yaml.safe_load(
        (DATA / "melt_activity_shadow_pins.yaml").read_text(encoding="utf-8")
    )
    pins = manifest["pins"]
    assert len({pin["pin_id"] for pin in pins}) == len(pins)
    assert manifest["comparison_tolerance_ln"] == 1.0e-10
    assert all(
        pin["disposition"] in {"legacy_in_domain", "legacy_degraded"}
        for pin in pins
    )
    not_comparable_count = 0
    degraded_count = 0

    for pin in pins:
        if pin["executed_legacy_path"] == "constant_table":
            legacy = melt_oxide_activity(
                pin["parent_oxide"],
                pin["account_mol"],
                temperature_K=float(pin["temperature_K"]),
            )
            assert legacy is not None and legacy.activity > 0.0
            standard = StandardStateIdentity(
                convention="raoultian_pure_endmember",
                phase=pin["target_phase"],
                reference_pressure_bar=1.0,
                component_basis="raoultian_pure_endmember",
            )
            declaration = ActivityInputDeclaration(
                component_id=pin["component_id"],
                standard_state=standard,
                activity_model="provider_reported_thermodynamic_activity",
                allow_henrian_upper_bound=False,
                require_assemblage_match=False,
            )
            rule = SimpleNamespace(
                species_id=pin["pin_id"],
                source_reaction_activity=declaration,
            )
            shadow = build_shadow_for_vapour_batch(
                rules=(rule,),
                ledger_snapshot={
                    "process.cleaned_melt": {pin["parent_oxide"]: 1.0}
                },
                state=VapourResolveState(
                    temperature_K=float(pin["temperature_K"]),
                    total_pressure_Pa=100_000.0,
                    source_reaction_activities={
                        pin["pin_id"]: legacy.activity
                    },
                    source_reaction_activity_evidence_refs={
                        pin["pin_id"]: legacy.citation
                    },
                    source_reaction_activity_provenance={
                        pin["pin_id"]: legacy.provenance()
                    },
                ),
            )
            assert shadow.results_by_component[pin["component_id"]].verdict is (
                ActivityVerdictKind.STATUS_BEARING_VALUE
            )
            comparison = next(
                item
                for item in shadow.not_comparable_population
                if item.component_id == pin["component_id"]
            )
            assert pin["comparison_status"] == SHADOW_NOT_COMPARABLE_YET
            assert pin["comparison_blocker"]
            assert comparison.comparison_status == SHADOW_NOT_COMPARABLE_YET
            assert comparison.equal is None
            assert comparison.delta_ln is None
            assert comparison.typed_ln_value is None
            not_comparable_count += 1
            continue

        composition = pin["composition_wt_pct"]
        intrinsic = pin.get("intrinsic_fO2_log10")
        legacy_value = (
            kress91_ferrous_feo_activity(
                comp_wt=composition,
                fO2_log=float(intrinsic),
                T_K=float(pin["temperature_K"]),
                pressure_bar=kress91_furnace_activity_pressure_bar(),
            )
            if intrinsic is not None
            else float(composition["FeO"]) / 100.0
        )
        shadow = build_shadow_for_vapour_batch(
            rules=(),
            ledger_snapshot={
                "process.cleaned_melt": {"FeO": 0.9, "Fe2O3": 0.1}
            },
            state=VapourResolveState(
                temperature_K=float(pin["temperature_K"]),
                total_pressure_Pa=100_000.0,
                source_reaction_activities={"Fe": legacy_value},
                source_reaction_fO2_log10=intrinsic,
                source_reaction_activity_pressure_bar=(
                    kress91_furnace_activity_pressure_bar()
                ),
                source_reaction_redox_model_id=(
                    "REF-001-kress-carmichael-1991"
                ),
                source_reaction_composition_wt_pct=composition,
            ),
        )
        typed = shadow.results_by_component["FeO"]
        if pin["disposition"] == "legacy_in_domain":
            assert typed.value is not None and typed.value > 0.0
            comparison = next(
                item
                for item in shadow.not_comparable_population
                if item.component_id == "FeO"
            )
            assert pin["comparison_status"] == SHADOW_NOT_COMPARABLE_YET
            assert pin["comparison_blocker"]
            assert comparison.equal is None
            assert comparison.delta_ln is None
            assert (
                "shares the executed legacy implementation" in comparison.detail
            )
            not_comparable_count += 1
        else:
            assert typed.verdict is ActivityVerdictKind.REFUSAL
            assert typed.refusal_code.value == pin["expected_typed_refusal"]
            assert pin["fallback_reason"] == "feo_weight_fraction"
            comparison = next(
                item
                for item in shadow.degraded_population
                if item.component_id == "FeO"
            )
            assert comparison.equal is None
            degraded_count += 1

    assert not_comparable_count == 13
    assert degraded_count == 1
    assert not_comparable_count + degraded_count == len(pins)


def test_independent_engine_comparison_reports_delta_and_fails_gate() -> None:
    resolver = MeltActivityResolver()
    standard = crystalline_target_standard_state("CaO")
    rule = SimpleNamespace(
        species_id="Ca",
        source_reaction_activity=ActivityInputDeclaration(
            component_id="CaO",
            standard_state=standard,
            activity_model="provider_reported_thermodynamic_activity",
            allow_henrian_upper_bound=False,
            require_assemblage_match=False,
        ),
    )
    legacy_value = 0.4

    def shadow_with_engine_ln(engine_ln_value: float) -> MeltActivityShadow:
        return build_shadow_for_vapour_batch(
            rules=(rule,),
            ledger_snapshot={"process.cleaned_melt": {"CaO": 1.0}},
            state=VapourResolveState(
                temperature_K=1673.0,
                total_pressure_Pa=100_000.0,
                source_reaction_activities={"Ca": legacy_value},
            ),
            engine_inputs_by_component={
                "CaO": TierAEngineInput(
                    engine_component_ids=("CaO",),
                    basis_coefficients=(1.0,),
                    engine_ln_activities=(engine_ln_value,),
                    source_mu0_J_per_mol=(0.0,),
                    target_mu0_J_per_mol=0.0,
                    source_standard_state=_tier_a_source_state(resolver, "CaO"),
                    conversion_ref="test:independent-engine-conversion",
                )
            },
        )

    engine_ln_value = math.log(0.2)
    shadow = shadow_with_engine_ln(engine_ln_value)

    assert len(shadow.equality_population) == 1
    comparison = shadow.equality_population[0]
    assert comparison.comparison_status == SHADOW_COMPARABLE
    assert comparison.comparison_method == (
        "engine_basis_plus_standard_state_conversion"
    )
    assert comparison.typed_ln_value == engine_ln_value
    assert comparison.delta_ln == pytest.approx(math.log(0.5))
    assert comparison.equal is False
    surface = shadow.as_mapping()
    assert surface["equality_mismatch_count"] == 1
    assert surface["equality_gate_status"] == "failed_divergence"

    within_tolerance = shadow_with_engine_ln(math.log(legacy_value) + 0.5e-10)
    within_comparison = within_tolerance.equality_population[0]
    assert within_comparison.equal is True
    assert within_comparison.delta_ln == pytest.approx(0.5e-10)
    assert within_tolerance.as_mapping()["equality_gate_status"] == "passed"


def test_production_compiled_liquid_declaration_can_enter_real_gate() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    catalog = compile_vapour_rail_catalog(
        payload,
        u0_manifest=load_u0_manifest(),
    )
    rule = next(item for item in catalog.request_rules if item.species_id == "K")
    declaration = rule.source_reaction_activity
    assert declaration is not None
    assert declaration.component_id == "KO0.5"
    assert declaration.standard_state.identity_id is None
    assert declaration.standard_state.component_id is None

    resolver = MeltActivityResolver()
    legacy_value = 0.4
    engine_ln_value = math.log(0.2)
    shadow = build_shadow_for_vapour_batch(
        rules=(rule,),
        ledger_snapshot={
            "process.cleaned_melt": {"K2O": 0.1, "SiO2": 0.9}
        },
        state=VapourResolveState(
            temperature_K=1500.0,
            total_pressure_Pa=100_000.0,
            source_reaction_activities={"K": legacy_value},
        ),
        engine_inputs_by_component={
            "KO0.5": TierAEngineInput(
                engine_component_ids=("K2O",),
                basis_coefficients=(0.5,),
                engine_ln_activities=(2.0 * engine_ln_value,),
                source_mu0_J_per_mol=(0.0,),
                target_mu0_J_per_mol=0.0,
                source_standard_state=_tier_a_source_state(
                    resolver, "KO0.5"
                ),
                conversion_ref="test:production-compiled-declaration",
            )
        },
    )

    comparison = shadow.equality_population[0]
    assert comparison.component_id == "KO0.5"
    assert comparison.typed_ln_value == pytest.approx(engine_ln_value)
    assert comparison.delta_ln == pytest.approx(math.log(0.5))
    assert comparison.equal is False
    assert shadow.as_mapping()["equality_gate_status"] == "failed_divergence"


def test_melt_activity_shadow_recorder_is_bounded() -> None:
    result = MeltActivityResolver().resolve_tier_c(
        _query("LiO0.5", fractions={"LiO0.5": 0.25, "SiO2": 0.75})
    )
    count = MELT_ACTIVITY_SHADOW_RECORD_LIMIT + 1
    shadow = MeltActivityShadow(
        results_by_component={f"component-{index}": result for index in range(count)},
        comparisons=tuple(
            ShadowComparison(
                component_id=f"component-{index}",
                legacy_value=0.25,
                typed_ln_value=(0.0 if index == count - 1 else None),
                delta_ln=(math.log(4.0) if index == count - 1 else None),
                equal=(False if index == count - 1 else None),
                population="legacy_in_domain",
                comparison_status=(
                    SHADOW_COMPARABLE
                    if index == count - 1
                    else SHADOW_NOT_COMPARABLE_YET
                ),
                comparison_method=("test" if index == count - 1 else None),
                tolerance_ln=(1.0e-10 if index == count - 1 else None),
            )
            for index in range(count)
        ),
        state_fingerprint="state:test",
        inventory_digest="inventory:test",
        registry_digest="registry:test",
    )

    surface = shadow.as_mapping()
    assert len(surface["results_by_component"]) == MELT_ACTIVITY_SHADOW_RECORD_LIMIT
    assert len(surface["comparisons"]) == MELT_ACTIVITY_SHADOW_RECORD_LIMIT
    assert surface["comparisons_recorded_count"] == (
        MELT_ACTIVITY_SHADOW_RECORD_LIMIT
    )
    assert surface["legacy_in_domain_population_count"] == count
    assert surface["not_comparable_yet_count"] == count - 1
    assert surface["equality_mismatch_count"] == 1
    assert surface["equality_gate_status"] == "failed_divergence"
    assert surface["dropped_component_count"] == 1
    assert surface["dropped_comparison_count"] == 1
    assert surface["record_truncated"] is True


def test_shadow_comparison_rejects_forgeable_false_green_records() -> None:
    with pytest.raises(ValueError, match="require finite"):
        ShadowComparison(
            component_id="CaO",
            legacy_value=0.4,
            typed_ln_value=None,
            delta_ln=None,
            equal=None,
            population="legacy_in_domain",
            comparison_status=SHADOW_COMPARABLE,
            comparison_method="engine_basis_plus_standard_state_conversion",
            tolerance_ln=1.0e-10,
        )
    with pytest.raises(ValueError, match="delta_ln must be derived"):
        ShadowComparison(
            component_id="CaO",
            legacy_value=0.4,
            typed_ln_value=math.log(0.2),
            delta_ln=0.0,
            equal=True,
            population="legacy_in_domain",
            comparison_status=SHADOW_COMPARABLE,
            comparison_method="engine_basis_plus_standard_state_conversion",
            tolerance_ln=1.0e-10,
        )
    with pytest.raises(ValueError, match="must be derived"):
        ShadowComparison(
            component_id="CaO",
            legacy_value=0.4,
            typed_ln_value=math.log(0.2),
            delta_ln=math.log(0.5),
            equal=True,
            population="legacy_in_domain",
            comparison_status=SHADOW_COMPARABLE,
            comparison_method="engine_basis_plus_standard_state_conversion",
            tolerance_ln=1.0e-10,
        )


def test_component_shadow_is_shared_and_degraded_fe_is_excluded() -> None:
    standard = StandardStateIdentity(
        convention="raoultian_pure_endmember",
        phase="liquid",
        reference_pressure_bar=1.0,
        component_basis="raoultian_pure_endmember",
    )
    declaration = ActivityInputDeclaration(
        component_id="KO0.5",
        standard_state=standard,
        activity_model="provider_reported_thermodynamic_activity",
        allow_henrian_upper_bound=False,
        require_assemblage_match=False,
    )
    rules = [
        SimpleNamespace(species_id="K", source_reaction_activity=declaration),
        SimpleNamespace(species_id="K_shadow_carrier", source_reaction_activity=declaration),
    ]
    state = VapourResolveState(
        temperature_K=1500.0,
        total_pressure_Pa=100_000.0,
        source_reaction_activities={"K": 0.25, "K_shadow_carrier": 0.25},
        source_reaction_activity_evidence_refs={"K": "doi:test"},
        source_reaction_activity_provenance={"K": {"tier": "legacy"}},
    )
    shadow = build_shadow_for_vapour_batch(
        rules=rules,
        ledger_snapshot={"process.cleaned_melt": {"K2O": 0.1, "SiO2": 0.9}},
        state=state,
    )

    assert shadow.results_by_component["KO0.5"].value == 0.25
    assert len(
        [item for item in shadow.comparisons if item.component_id == "KO0.5"]
    ) == 1
    assert shadow.equality_population == ()
    assert shadow.not_comparable_population[0].component_id == "KO0.5"
    assert shadow.not_comparable_population[0].equal is None
    assert shadow.degraded_population[0].component_id == "FeO"
    assert shadow.degraded_population[0].equal is None
    surface = shadow.as_mapping()
    assert surface["degraded_excluded_from_equality"] is True
    assert "CrO" in surface["results_by_component"]
    assert "NaCl_melt" in surface["results_by_component"]


def test_tier_c_unknown_component_refuses_and_ownerless_spectator_propagates() -> None:
    resolver = MeltActivityResolver()
    unknown = resolver.resolve_tier_c(
        _query(
            "DefinitelyUnknownOxide",
            fractions={"DefinitelyUnknownOxide": 0.1, "SiO2": 0.9},
        )
    )
    assert unknown.verdict is ActivityVerdictKind.REFUSAL
    assert unknown.refusal_code is ActivityRefusalCode.STANDARD_STATE_UNRESOLVED

    query = _query("CaO", fractions={"CaO": 0.1, "SiO2": 0.9})
    query = MeltActivityQuery(
        **{
            **query.__dict__,
            "unmodeled_nonzero_reservoir_ids": (
                "process.cleaned_melt:Cl",
            ),
        }
    )
    refused = resolver.adapt_legacy_value(
        query,
        0.1,
        evidence_ref="test",
        evidence_tier="test",
        provenance={},
    )
    assert refused.refusal_code is ActivityRefusalCode.UNMODELED_RESERVOIR_PRESENT


def test_tier_a_rejects_wrong_engine_identity_and_out_of_domain_temperature() -> None:
    resolver = MeltActivityResolver()
    query = _query("CaO", fractions={"CaO": 0.1}, temperature_K=1600.0)
    wrong_engine = resolver.resolve_engine_basis(
        query,
        TierAEngineInput(
            engine_component_ids=("made_up",),
            basis_coefficients=(1.0,),
            target_mu0_J_per_mol=0.0,
            mixture_mu_J_per_mol=(0.0,),
            source_standard_state=_tier_a_source_state(resolver, "CaO"),
        ),
    )
    assert wrong_engine.refusal_code is ActivityRefusalCode.BASIS_TRANSFORM_FAILED

    cold = MeltActivityQuery(**{**query.__dict__, "temperature_K": 500.0})
    out_of_domain = resolver.resolve_engine_basis(
        cold,
        TierAEngineInput(
            engine_component_ids=("CaO",),
            basis_coefficients=(1.0,),
            target_mu0_J_per_mol=0.0,
            mixture_mu_J_per_mol=(0.0,),
            source_standard_state=_tier_a_source_state(resolver, "CaO"),
        ),
    )
    assert out_of_domain.refusal_code is ActivityRefusalCode.DESCRIPTOR_HULL_EXCEEDED


def test_fe_shadow_uses_exact_pressure_and_keeps_source_standard_state() -> None:
    composition = {
        "SiO2": 50.0,
        "Al2O3": 15.0,
        "FeO": 12.0,
        "MgO": 10.0,
        "CaO": 10.0,
        "Na2O": 2.0,
        "K2O": 1.0,
    }
    pressure_bar = 3.0e-8
    intrinsic_log10 = -8.0
    state = VapourResolveState(
        temperature_K=1673.15,
        total_pressure_Pa=100_000.0,
        source_reaction_activities={"Fe": 0.1},
        source_reaction_fO2_log10=intrinsic_log10,
        source_reaction_activity_pressure_bar=pressure_bar,
        source_reaction_redox_model_id="REF-001-kress-carmichael-1991",
        source_reaction_composition_wt_pct=composition,
    )
    shadow = build_shadow_for_vapour_batch(
        rules=(),
        ledger_snapshot={"process.cleaned_melt": {"FeO": 1.0}},
        state=state,
    )
    result = shadow.results_by_component["FeO"]
    expected = kress91_ferrous_feo_activity(
        comp_wt=composition,
        fO2_log=intrinsic_log10,
        T_K=1673.15,
        pressure_bar=pressure_bar,
    )

    assert result.value == expected
    assert result.standard_state == result.source_standard_state
    assert result.standard_state != result.target_standard_state
    assert result.derivation["pressure_bar"] == pressure_bar
    assert result.derivation["target_conversion_status"] == "mu0_target_pending"
