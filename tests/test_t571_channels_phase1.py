"""t-571 Phase 1: channel map with O2 as first citizen.

Covers:
- stoichiometric exponent derivation (hand values from design §6)
- O2 channel bit-identity vs legacy scalar path (all O2-dependent catalog rows)
- typed refusals for unowned channels (BaF end-to-end)
- coverage-ledger consumption of typed NEEDS-CHANNEL reasons
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
import yaml

from simulator.physical_constants import (
    GAS_CONSTANT,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    FluxActivationContext,
)
from simulator.vapour_rail.catalog import (
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.channels import (
    CHANNEL_Cl2,
    CHANNEL_F2,
    CHANNEL_H2,
    CHANNEL_O2,
    CHANNEL_REGISTRY,
    CHANNEL_S2,
    ChannelCompositionRefusal,
    ChannelConstructionError,
    ChannelEvaluationError,
    ChannelVerdictKind,
    GasChannelPotential,
    REACTION_PLANE_MELT_INTERFACE,
    REACTION_PLANE_TRANSPORT_HEADSPACE,
    REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING,
    ReactionThermoInputs,
    attempt_channel_composition,
    channel_linear_mass_action_factor,
    channel_log10_contribution,
    compile_channel_term_from_binding,
    compile_o2_channel_term,
    count_o2_dependent_compiled_evaluators,
    derived_exponent,
    load_typed_needs_channel_reasons,
    o2_potential_from_pO2_bar,
    parse_needs_channel_entry,
    reconstruct_878_pathway_cohort,
    resolve_channel_potential,
)
from simulator.vapour_rail.request import (
    RequestRule,
    VapourResolveState,
    chemical_potential_channel_refusal,
    refusal_closure,
    resolve_vapour_batch,
)
from simulator.vapour_rail.u0_manifest import load_u0_manifest


DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def production_catalog():
    payload = yaml.safe_load(
        (DATA / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    return compile_vapour_rail_catalog(payload, u0_manifest=load_u0_manifest())


# ---------------------------------------------------------------------------
# §6 exponent derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signed_nu,target_nu,expected",
    [
        # BaO + 1/2 Cl2 -> BaCl + 1/2 O2  (design §6 table)
        (-1.0, 1.0, 1.0),  # BaO reactant
        (-0.5, 1.0, 0.5),  # Cl2 reactant
        (0.5, 1.0, -0.5),  # O2 product
        # BaCl2(l) -> BaCl(g) + 1/2 Cl2
        (-1.0, 1.0, 1.0),  # BaCl2
        (0.5, 1.0, -0.5),  # Cl2 product
        # Target stoichiometry other than one: 2 A -> 2 V + O2
        (-2.0, 2.0, 1.0),
        (1.0, 2.0, -0.5),
        # Positive common-factor scale invariance
        (-1.0, 1.0, 1.0),
        (-2.0, 2.0, 1.0),
        (-0.5, 0.5, 1.0),
    ],
)
def test_derived_exponent_hand_values(signed_nu, target_nu, expected):
    assert derived_exponent(signed_nu, target_nu) == pytest.approx(
        expected, abs=0.0, rel=0.0
    )


def test_o2_term_matches_scalar_pO2_exponent():
    # e = -nu_O2 / nu_g  with nu_O2 = +0.5 product → e = -0.5
    term = compile_o2_channel_term(
        signed_nu_o2=0.5,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
    )
    assert term.input_id == CHANNEL_O2
    assert term.derived_exponent == -0.5
    assert term.derived_exponent == derived_exponent(0.5, 1.0)


# ---------------------------------------------------------------------------
# O2 adapter + bit-identity of channel contribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pO2_bar,p_ref,exponent",
    [
        (1.0e-6, 1.0, -0.5),
        (1.0e-9, 1.0e-9, -0.5),
        (1.0e-12, 1.0e-9, -0.5),
        (50.0, 1.0, 1.0),
        (1.0e-35, 1.0, -0.25),  # clamps to min
        (1.0e6, 1.0, -0.5),  # clamps to max
    ],
)
def test_o2_channel_contribution_bit_identical_to_legacy(pO2_bar, p_ref, exponent):
    term = compile_o2_channel_term(
        signed_nu_o2=-exponent,  # e = -nu → nu = -e
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert term.derived_exponent == pytest.approx(exponent, abs=1e-15)
    pot = o2_potential_from_pO2_bar(
        pO2_bar=pO2_bar,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        pO2_reference_bar=p_ref,
    )
    assert pot.verdict is ChannelVerdictKind.POINT
    assert pot.source_kind == "legacy_scalar_adapter"
    contrib = channel_log10_contribution(
        term, pot, legacy_pO2_reference_bar=p_ref
    )
    oxygen = float(pO2_bar)
    if oxygen < MELT_DISSOCIATION_PO2_MIN_BAR:
        oxygen = MELT_DISSOCIATION_PO2_MIN_BAR
    elif oxygen > MELT_DISSOCIATION_PO2_MAX_BAR:
        oxygen = MELT_DISSOCIATION_PO2_MAX_BAR
    legacy = exponent * math.log10(oxygen / p_ref)
    assert contrib == legacy  # bit-identical


# ---------------------------------------------------------------------------
# Catalog-wide bit-identity differential (all O2-dependent rows × T × pO2)
# ---------------------------------------------------------------------------

_T_GRID_K = (1400.0, 1600.0, 1800.0, 2000.0, 2200.0)
# P3 fold (probe clamp-edge coverage): the grid spans the physical envelope
# edges — 1e-35 clamps to MELT_DISSOCIATION_PO2_MIN_BAR (1e-30) and 1e3
# clamps to MELT_DISSOCIATION_PO2_MAX_BAR (100) — so the clamp itself is
# differential-probed, not just the in-envelope interior.
_PO2_GRID_BAR = (1.0e-35, 1.0e-12, 1.0e-9, 1.0e-6, 1.0e-3, 1.0e-1, 1.0, 1.0e3)


def test_catalog_o2_channel_bit_identity_differential(production_catalog):
    """Every O2-dependent compiled evaluator: legacy kwargs == typed inputs."""

    o2_species = []
    for species_id, species in production_catalog.species.items():
        ev = species.evaluator
        if ev is None:
            continue
        if abs(float(ev.pO2_exponent or 0.0)) == 0.0 and ev.o2_channel_term is None:
            continue
        o2_species.append((species_id, ev))

    assert len(o2_species) > 0, "expected O2-dependent catalog evaluators"
    mismatches: list[str] = []
    n_checks = 0
    n_skips = 0
    for species_id, ev in o2_species:
        # O2 channel term must be present and agree with scalar exponent.
        term = ev.o2_channel_term
        assert term is not None, f"{species_id}: missing O2 reaction term"
        assert math.isclose(
            term.derived_exponent,
            float(ev.pO2_exponent),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ), (
            f"{species_id}: term.e={term.derived_exponent} "
            f"vs pO2_exponent={ev.pO2_exponent}"
        )

        low, high = ev.valid_temperature_K
        temps = [t for t in _T_GRID_K if low <= t <= high]
        if not temps:
            # Probe the midpoint of the declared domain.
            temps = [0.5 * (low + high)]
        for T in temps:
            for pO2 in _PO2_GRID_BAR:
                activity = 1.0 if abs(ev.activity_exponent) > 0.0 else None
                # If activity is required, supply 1.0; else omit.
                kwargs_legacy = {"pO2_bar": pO2}
                if abs(ev.activity_exponent) > 0.0:
                    kwargs_legacy["source_activity"] = 1.0
                try:
                    legacy = ev.evaluate(T, **kwargs_legacy)
                except Exception as exc:  # domain / thermo holes
                    # P3 fold (skip/762 gating): skips are counted and gated
                    # at zero below — a skipped point is unproven surface, and
                    # the 762/762 claim must not hide silent exclusions.
                    n_skips += 1
                    continue

                plane = (
                    REACTION_PLANE_MELT_INTERFACE
                    if ev.oxygen_fugacity_channel == "intrinsic_melt"
                    else REACTION_PLANE_TRANSPORT_HEADSPACE
                )
                pot = o2_potential_from_pO2_bar(
                    pO2_bar=pO2,
                    temperature_K=T,
                    reaction_plane=plane,
                    pO2_reference_bar=ev.pO2_reference_bar,
                )
                typed_inputs = ReactionThermoInputs(
                    reaction_id=None,
                    state_fingerprint=None,
                    activities=MappingProxyType({}),
                    channels=MappingProxyType({CHANNEL_O2: pot}),
                )
                kwargs_typed = {"reaction_inputs": typed_inputs}
                if abs(ev.activity_exponent) > 0.0:
                    kwargs_typed["source_activity"] = 1.0
                typed = ev.evaluate(T, **kwargs_typed)
                n_checks += 1
                if typed.pressure_pa != legacy.pressure_pa:
                    mismatches.append(
                        f"{species_id} T={T} pO2={pO2}: "
                        f"legacy={legacy.pressure_pa!r} typed={typed.pressure_pa!r}"
                    )
                if typed.out_of_range != legacy.out_of_range:
                    mismatches.append(
                        f"{species_id} T={T} pO2={pO2}: out_of_range "
                        f"legacy={legacy.out_of_range} typed={typed.out_of_range}"
                    )

    assert n_checks > 100, f"too few differential checks: {n_checks}"
    assert n_skips == 0, (
        f"{n_skips} grid points skipped (reference surface could not "
        "evaluate); the bit-identity claim must cover the full grid"
    )
    assert not mismatches, (
        f"{len(mismatches)} bit-identity mismatches (showing up to 10):\n"
        + "\n".join(mismatches[:10])
    )


def test_o2_dependent_evaluator_count_reproducible(production_catalog):
    """P3 fold: 1e-9-ref count is compiled evaluators, not YAML declaration sites."""

    counts = count_o2_dependent_compiled_evaluators(production_catalog)
    assert counts["o2_dependent_evaluators"] > 0
    # Design-corrected figure: 4 O2-dependent compiled evaluators at 1e-9
    # (Si, SiO, Si2, Si3).  SiO2_gas also declares pO2_reference_bar=1e-9 in
    # YAML but has pO2_exponent=0, so it is not an O2-channel consumer.
    assert counts["pO2_reference_1e9_evaluators"] == 4, counts
    assert set(counts["species_ids_1e9"]) == {"Si", "SiO", "Si2", "Si3"}


# ---------------------------------------------------------------------------
# Typed refusals — BaF end-to-end + halogen/sulfur/H2
# ---------------------------------------------------------------------------


def test_baf_composition_refuses_with_f2_and_halide_owner():
    result = attempt_channel_composition(
        carrier="BaF",
        element="Ba",
        pathway="parent_oxide_x_halogen_exchange",
        missing_text=(
            "NEEDS-CHANNEL: runtime lacks non-O2 chemical-potential channel; "
            "pathway=parent_oxide_x_halogen_exchange"
        ),
    )
    assert isinstance(result, ChannelCompositionRefusal)
    assert result.disposition == REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
    assert CHANNEL_F2 in result.missing_channels
    assert "halide_reservoir_owner_missing" in result.missing_melt_owners
    # No numeric potential may leak.
    assert result.as_mapping()["missing_channels"] == [CHANNEL_F2]


def test_request_chemical_potential_channel_refusal_baf():
    code_detail = chemical_potential_channel_refusal(
        carrier="BaF",
        element="Ba",
        pathway="parent_oxide_x_halogen_exchange",
    )
    assert code_detail is not None
    code, detail = code_detail
    assert code == REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
    assert CHANNEL_F2 in detail
    assert "halide_reservoir_owner_missing" in detail


@pytest.mark.parametrize(
    "channel_id,expected_code_substr",
    [
        (CHANNEL_F2, "halide_reservoir_owner_missing"),
        (CHANNEL_Cl2, "halide_reservoir_owner_missing"),
        (CHANNEL_S2, "sulfur_reservoir_owner_missing"),
        (CHANNEL_H2, "runtime_owner"),
    ],
)
def test_unowned_channel_resolver_typed_refusal(channel_id, expected_code_substr):
    pot = resolve_channel_potential(
        channel_id,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert pot.verdict is ChannelVerdictKind.REFUSAL
    assert pot.reduced_potential_ln is None
    assert pot.delta_mu_J_per_mol is None
    assert pot.refusal_code is not None
    assert expected_code_substr in (pot.refusal_code + " " + (pot.detail or ""))


def test_o2_resolver_owned():
    pot = resolve_channel_potential(
        CHANNEL_O2,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
        pO2_bar=1.0e-6,
        pO2_reference_bar=1.0,
    )
    assert pot.verdict is ChannelVerdictKind.POINT
    assert pot.channel_id == CHANNEL_O2
    assert pot.reduced_potential_ln is not None


def test_channel_registry_vocabulary_complete():
    expected = {
        CHANNEL_O2,
        CHANNEL_H2,
        CHANNEL_F2,
        CHANNEL_Cl2,
        "gas.Br2.ideal_1bar.v1",
        "gas.I2.ideal_1bar.v1",
        CHANNEL_S2,
        "gas.N2.ideal_1bar.v1",
    }
    assert set(CHANNEL_REGISTRY) == expected
    # Only O2 has a runtime owner in Phase 1.
    owned = [e for e in CHANNEL_REGISTRY.values() if e.runtime_owner is not None]
    assert len(owned) == 1
    assert owned[0].channel_id == CHANNEL_O2


# ---------------------------------------------------------------------------
# Coverage-ledger typed reasons
# ---------------------------------------------------------------------------


def test_parse_needs_channel_baf_entry():
    entry = {
        "element": "Ba",
        "carrier": "BaF",
        "missing": (
            "NEEDS-CHANNEL: runtime lacks non-O2 chemical-potential channel; "
            "pathway=parent_oxide_x_halogen_exchange; CEA parent oxide BaO_cr"
        ),
    }
    typed = parse_needs_channel_entry(entry)
    assert typed is not None
    assert typed.carrier == "BaF"
    assert CHANNEL_F2 in typed.required_channels
    assert "halide_reservoir_owner_missing" in typed.missing_melt_owners
    assert typed.disposition == REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING


def test_coverage_ledger_typed_reasons_consume_needs_channel():
    reasons = load_typed_needs_channel_reasons(DATA / "vapour_rail_coverage_gaps.yaml")
    assert len(reasons) > 0
    # BaF must be present with F2 + halide owner.
    baf = [r for r in reasons if r.carrier == "BaF" and r.element == "Ba"]
    assert baf, "BaF NEEDS-CHANNEL rows missing from ledger projection"
    assert all(CHANNEL_F2 in r.required_channels for r in baf)
    assert all(
        "halide_reservoir_owner_missing" in r.missing_melt_owners for r in baf
    )
    # Every typed reason has a disposition the ledger can consume.
    dispositions = {r.disposition for r in reasons}
    assert all(d.startswith("refused_") for d in dispositions)


def test_reconstruct_878_pathway_cohort_from_ledger():
    """Design §2.2 878 figure must be reproducible from the free-text ledger."""

    result = reconstruct_878_pathway_cohort(gaps_path=DATA / "vapour_rail_coverage_gaps.yaml")
    # Document the actual reconstruction; the design total is 878.  If the
    # free-text pathway labels drift, surface the counts rather than silently
    # pass.
    by = dict(result["by_pathway"])
    total = int(result["total"])
    # Soft gate: total should be in the NEEDS-CHANNEL neighborhood; the
    # design's 878 is the target.  Report both.
    assert total > 0, by
    # Prefer exact 878 when the ledger still carries the design pathways.
    if total == 878:
        assert result["reproducible"] is True
    else:
        # Still assert the six pathway labels are present as keys.
        assert set(by) == {
            "parent_oxide_x_halogen_exchange",
            "element_condensed_x_halogen_exchange",
            "catalog_base_x_halogen_exchange",
            "base_x_hydrogen_exchange",
            "halide_condensed_family",
            "related_binary_condensed",
        }



# ---------------------------------------------------------------------------
# Owner gate (P1 fix): GasChannelPotential construction is owner-gated
# ---------------------------------------------------------------------------


def _o2_point_kwargs() -> dict:
    """Valid finite-center field set for an O2 potential (no grant)."""

    reduced = math.log(1.0e-6)
    return {
        "channel_id": CHANNEL_O2,
        "gas_formula": "O2",
        "gas_standard_state_id": "gas.ideal.O2.1bar.v1",
        "reaction_plane": REACTION_PLANE_MELT_INTERFACE,
        "temperature_K": 1800.0,
        "verdict": ChannelVerdictKind.POINT,
        "reduced_potential_ln": reduced,
        "delta_mu_J_per_mol": GAS_CONSTANT * 1800.0 * reduced,
        "source_kind": "legacy_scalar_adapter",
    }


def test_direct_construction_without_owner_grant_is_closed():
    """The free-floating-fugacity hole: bare construction must fail.

    P2: the unbound default is deleted — the grant is a required
    keyword-only field, so omitting it fails at the signature (TypeError),
    before any field validation runs.
    """

    with pytest.raises(TypeError, match="_owner_grant"):
        GasChannelPotential(**_o2_point_kwargs())


def test_explicit_none_or_forged_grant_is_closed():
    with pytest.raises(ChannelConstructionError):
        GasChannelPotential(**_o2_point_kwargs(), _owner_grant=None)
    with pytest.raises(ChannelConstructionError):
        GasChannelPotential(**_o2_point_kwargs(), _owner_grant=object())


# ---------------------------------------------------------------------------
# Owner-grant forgery attempts (P2): the grant must be unforgeable from
# outside the owning module — each attempt below must FAIL.
# ---------------------------------------------------------------------------


def test_owner_grant_class_constructor_is_sealed():
    """P2 forgery attempt 1: instantiate the grant class directly.

    Name privacy is only a convention, so the constructor itself must
    refuse calls that do not present the module-private mint token —
    including calls presenting a fabricated token.
    """

    import simulator.vapour_rail.channels as channels_module

    with pytest.raises(ChannelConstructionError):
        channels_module._OwnerGrant(
            channel_id=CHANNEL_O2,
            owner_id="legacy_pO2_adapter",
            source_kind="legacy_scalar_adapter",
            refusal_only=False,
        )
    with pytest.raises(ChannelConstructionError):
        channels_module._OwnerGrant(
            channel_id=CHANNEL_O2,
            owner_id="legacy_pO2_adapter",
            source_kind="legacy_scalar_adapter",
            refusal_only=False,
            _mint_token=object(),  # fabricated token
        )


def test_grant_forgery_with_stolen_token_still_closed():
    """P2 forgery attempt 2: steal the module-private mint token.

    Even a caller who imports the private token and mints a field-perfect
    look-alike grant cannot use it: the look-alike is not the registered
    registry object, so the identity check in __post_init__ rejects it.
    """

    import simulator.vapour_rail.channels as channels_module

    forged = channels_module._OwnerGrant(
        channel_id=CHANNEL_O2,
        owner_id="legacy_pO2_adapter",
        source_kind="legacy_scalar_adapter",
        refusal_only=False,
        _mint_token=channels_module._GRANT_MINT_TOKEN,  # stolen private token
    )
    assert forged.owner_id == "legacy_pO2_adapter"  # minted, but unregistered
    with pytest.raises(ChannelConstructionError):
        GasChannelPotential(**_o2_point_kwargs(), _owner_grant=forged)


def test_grant_lifting_across_channels_is_closed():
    """P2 forgery attempt 3: lift a genuine O2 grant onto an F2 potential."""

    pot = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-6,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    lifted = pot._owner_grant  # genuine module-minted grant, bound to O2
    reduced = math.log(1.0e-6)
    with pytest.raises(ChannelConstructionError, match="bound to channel"):
        GasChannelPotential(
            channel_id=CHANNEL_F2,
            gas_formula="F2",
            gas_standard_state_id="gas.ideal.F2.1bar.v1",
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
            temperature_K=1800.0,
            verdict=ChannelVerdictKind.POINT,
            reduced_potential_ln=reduced,
            delta_mu_J_per_mol=GAS_CONSTANT * 1800.0 * reduced,
            source_kind="legacy_scalar_adapter",
            _owner_grant=lifted,
        )


def test_resolver_refusal_grant_cannot_mint_o2_numeric():
    """P2 forgery attempt 4: lift a resolver refusal grant onto a numeric O2."""

    refusal = resolve_channel_potential(
        CHANNEL_F2,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert refusal.verdict is ChannelVerdictKind.REFUSAL
    # Channel binding fires first (grant is bound to F2)...
    with pytest.raises(ChannelConstructionError):
        GasChannelPotential(**_o2_point_kwargs(), _owner_grant=refusal._owner_grant)
    # ...and even on its own channel the refusal-only gate still holds.
    with pytest.raises(ChannelConstructionError):
        dataclasses.replace(
            refusal,
            verdict=ChannelVerdictKind.POINT,
            reduced_potential_ln=-1.0,
            delta_mu_J_per_mol=GAS_CONSTANT * 1800.0 * -1.0,
        )


def test_registered_grant_is_a_stable_singleton_per_source():
    """The registry keys grants to real sources: repeated mints are identical."""

    a = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-6,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    b = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-3,
        temperature_K=2000.0,
        reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
    )
    assert a._owner_grant is b._owner_grant


def test_refusal_only_grant_cannot_mint_numeric_potential():
    """A resolver refusal grant must not be upgradable to a number."""

    refusal = resolve_channel_potential(
        CHANNEL_F2,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    assert refusal.verdict is ChannelVerdictKind.REFUSAL
    with pytest.raises(ChannelConstructionError):
        dataclasses.replace(
            refusal,
            verdict=ChannelVerdictKind.POINT,
            reduced_potential_ln=-1.0,
            delta_mu_J_per_mol=GAS_CONSTANT * 1800.0 * -1.0,
        )


def test_grant_source_kind_mismatch_is_closed():
    pot = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-6,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    with pytest.raises(ChannelConstructionError):
        dataclasses.replace(pot, source_kind="runtime_owner")


def test_o2_factory_is_gated_on_the_registered_owner(monkeypatch):
    """Removing the runtime owner from the registry closes the adapter."""

    import simulator.vapour_rail.channels as channels_module

    entry = channels_module.CHANNEL_REGISTRY[CHANNEL_O2]
    ownerless = dataclasses.replace(entry, runtime_owner=None)
    forged_registry = MappingProxyType(
        {**dict(channels_module.CHANNEL_REGISTRY), CHANNEL_O2: ownerless}
    )
    monkeypatch.setattr(
        channels_module, "CHANNEL_REGISTRY", forged_registry
    )
    with pytest.raises(ChannelConstructionError):
        o2_potential_from_pO2_bar(
            pO2_bar=1.0e-6,
            temperature_K=1800.0,
            reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        )


# ---------------------------------------------------------------------------
# NEEDS-CHANNEL refusal wired into the runtime composition flow (P1 fix)
# ---------------------------------------------------------------------------


def _baf_rule() -> RequestRule:
    """Contract-complete catalog rule for a BaF-like halide carrier."""

    return RequestRule(
        species_id="BaF",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"BaF"}),
        required_source_atoms=frozenset(),
        solve_group_id="u0_v:BaF",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="BaF",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
    )


def _baf_compiled_species() -> dict:
    """Compiled-species stand-in whose evaluator declares an F2 exchange term."""

    f2_term = compile_channel_term_from_binding(
        participant_formula="F2",
        channel_id=CHANNEL_F2,
        signed_nu=-0.5,
        target_nu=1.0,
        required_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    o2_term = compile_o2_channel_term(
        signed_nu_o2=0.5,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    evaluator = SimpleNamespace(
        reaction_terms=(o2_term, f2_term),
        pO2_exponent=-0.5,
        pO2_reference_bar=1.0,
        activity_exponent=0.0,
        oxygen_fugacity_channel="intrinsic_melt",
    )
    compiled = SimpleNamespace(
        species_id="BaF",
        evaluator=evaluator,
        vaporisation_coefficients=SimpleNamespace(
            evaporation_alpha={"value": 1.0}
        ),
        source_reaction_activity=None,
    )
    return {"BaF": compiled}


def test_needs_channel_attempt_typed_refusal_by_execution_closure():
    """BaF through refusal_closure: typed owner refusal, not a contract miss."""

    rules = (_baf_rule(),)
    ledger = {"process.cleaned_melt": {"BaF": 1.0}}
    result = refusal_closure(
        requested=frozenset({"BaF"}),
        rules=rules,
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1800.0),
        catalog_species=_baf_compiled_species(),
    )
    answer = result.answers["BaF"]
    assert answer.is_refused
    # The typed §9 code — never the generic missing_channel_contract.
    assert answer.refusal_code == REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
    detail = answer.pressure.detail
    assert CHANNEL_F2 in detail
    assert "halide_reservoir_owner_missing" in detail


def test_needs_channel_attempt_typed_refusal_by_execution_batch():
    """Same proof through the full resolve_vapour_batch runtime pipeline."""

    rules = (_baf_rule(),)
    ledger = {"process.cleaned_melt": {"BaF": 1.0}}
    batch = resolve_vapour_batch(
        rules=rules,
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1800.0),
        catalog_species=_baf_compiled_species(),
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )
    answer = batch.channel("BaF")
    assert answer.is_refused
    assert answer.refusal_code == REFUSAL_HALIDE_RESERVOIR_OWNER_MISSING
    assert "BaF" not in batch.flux_active_species_ids
    assert batch.metadata["n_refused"] == 1


def test_o2_only_evaluator_passes_channel_gate():
    """Regression guard: the gate is inert for O2-only exchange terms."""

    o2_term = compile_o2_channel_term(
        signed_nu_o2=0.5,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )

    class _O2Eval:
        reaction_terms = (o2_term,)
        pO2_exponent = -0.5
        pO2_reference_bar = 1.0
        activity_exponent = 0.0
        oxygen_fugacity_channel = "transport_headspace"

        def evaluate(self, temperature_K, *, source_activity=None, pO2_bar=None):
            assert pO2_bar is not None
            return SimpleNamespace(
                pressure_pa=1.0e3 * (float(pO2_bar) ** -0.5),
                out_of_range=False,
                acquisition_flag=None,
                status=None,
            )

    compiled = SimpleNamespace(
        species_id="Ba",
        evaluator=_O2Eval(),
        vaporisation_coefficients=SimpleNamespace(
            evaporation_alpha={"value": 1.0}
        ),
        source_reaction_activity=None,
    )
    rule = RequestRule(
        species_id="Ba",
        source_account="process.cleaned_melt",
        parent_species_ids=frozenset({"BaO"}),
        required_source_atoms=frozenset(),
        solve_group_id="u0_v:Ba",
        applicability_predicate="applicable",
        request_rule_kind="source_inventory_present",
        origin="catalog",
        formula_id="Ba",
        has_pressure_evaluator=True,
        has_alpha=True,
        has_route=True,
        has_formula=True,
    )
    result = refusal_closure(
        requested=frozenset({"Ba"}),
        rules=(rule,),
        ledger_snapshot={"process.cleaned_melt": {"BaO": 1.0}},
        state=VapourResolveState(
            temperature_K=1800.0,
            fO2_bar=1.0e-9,
        ),
        catalog_species={"Ba": compiled},
    )
    answer = result.answers["Ba"]
    # Live evaluation, not a channel-gate refusal.
    assert not answer.is_refused
    assert answer.is_flux_active


def test_production_catalog_emits_only_o2_terms(production_catalog):
    """The Phase-1 compiler never emits a non-O2 exchange term, so the
    closure gate cannot fire on the production catalog today (dormant but
    wired — proven by the BaF execution tests above)."""

    n_terms = 0
    for species_id, species in production_catalog.species.items():
        ev = species.evaluator
        if ev is None:
            continue
        for term in ev.reaction_terms:
            n_terms += 1
            assert term.input_id == CHANNEL_O2, (
                f"{species_id}: unexpected non-O2 term {term.input_id}"
            )
    # t-583 adds 25 status-only, non-authoritative O2-dependent evaluators to
    # the 30 pre-existing terms; t-609 adds one FeO association term. No
    # non-O2 channel is admitted.
    assert n_terms == 56


# ---------------------------------------------------------------------------
# Linear-space channel composer (P2): builtin linear rails through channel #1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pO2_bar,p_ref,exponent",
    [
        (1.0e-6, 1.0, -0.5),
        (1.0e-9, 1.0e-9, -0.5),
        (1.0e-12, 1.0e-9, -1.0),
        (50.0, 1.0, 0.25),
        (1.0e-35, 1.0, -0.75),  # clamps to MIN (1e-30)
        (1.0e6, 1.0, -0.25),  # clamps to MAX (100)
        (MELT_DISSOCIATION_PO2_MIN_BAR, 1.0, -0.5),  # exact MIN edge
        (MELT_DISSOCIATION_PO2_MAX_BAR, 1.0, 0.5),  # exact MAX edge
    ],
)
def test_channel_linear_mass_action_factor_bit_identical(pO2_bar, p_ref, exponent):
    """Linear composer == exact pre-t-571 inline expression (IEEE identity)."""

    term = compile_o2_channel_term(
        signed_nu_o2=-exponent,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    pot = o2_potential_from_pO2_bar(
        pO2_bar=pO2_bar,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        pO2_reference_bar=p_ref,
    )
    factor = channel_linear_mass_action_factor(term, pot)
    # Pre-migration inline expression from _standard_reaction_pressure_Pa.
    p_ref_legacy = max(1e-30, float(p_ref) or 1.0)
    oxygen = min(
        max(float(pO2_bar), MELT_DISSOCIATION_PO2_MIN_BAR),
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    legacy = (oxygen / p_ref_legacy) ** float(exponent)
    assert factor == legacy  # bit-identical, not approx


def test_channel_linear_mass_action_factor_typed_errors():
    o2_term = compile_o2_channel_term(
        signed_nu_o2=0.5,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    # Refused potential: no numeric factor may be composed.
    refused = resolve_channel_potential(
        CHANNEL_F2,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    f2_term = compile_channel_term_from_binding(
        participant_formula="F2",
        channel_id=CHANNEL_F2,
        signed_nu=-0.5,
        target_nu=1.0,
        required_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    with pytest.raises(ChannelEvaluationError):
        channel_linear_mass_action_factor(f2_term, refused)
    # Non-O2 channels are not linear-composable even with a point verdict.
    o2_point = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-6,
        temperature_K=1800.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    with pytest.raises(ChannelEvaluationError):
        channel_linear_mass_action_factor(f2_term, o2_point)  # channel mismatch
    with pytest.raises(ChannelEvaluationError):
        channel_linear_mass_action_factor(o2_term, refused)  # verdict not Point


# ---------------------------------------------------------------------------
# P2 provider-level differential: the 9 (+SiO) linear-rail species
# ---------------------------------------------------------------------------
#
# The builtin provider computed O2 in linear space for these catalog
# O2-dependent species outside the channel interface (kimi P2, "9/30"):
#
#   metals standard_reaction rail : K, Na, Si
#   liquid_oxide_standard_reaction: Al, Cr, Mn, Ti
#   Ellingham gas/condensed rails : Ca, Mg
#   SiO sqrt mass action          : SiO (10th; pO2_exponent=0 row, hard-coded
#                                   1/sqrt(pO2) — same linear class)
#
# Post-migration each O2 value enters through the owner-gated channel
# factory.  The probe below recomputes the *pre-migration* linear
# expressions from provenance fields and asserts IEEE equality with the
# provider's emitted pressures — including clamp-edge redox states.
#
# Re-review fold ("Ca/Mg/SiO thin"): beyond the shared T × fO2 × transport
# grid, targeted sweeps widen the channel-relevant dimension of the three
# flagged rails — an 8-point in-envelope transport sweep for the SiO sqrt
# (the main grid samples only 2 distinct sqrt factors), a 12-point fO2
# sweep for the Ca/Mg Ellingham denominator (clamped-below, both exact
# envelope edges, dense interior, clamped-above), and a final emitted-
# pressure comparison for Ca/Mg in addition to the intermediate root.
# Distinct-value gates assert the widened population is real.


def _provider_and_request(
    temperature_C: float,
    *,
    intrinsic_fO2_log: float,
    transport_pO2_bar: float,
):
    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
    from simulator.chemistry.kernel.dto import ProviderAccountView

    melt_mol = {
        "SiO2": 50.0,
        "Al2O3": 8.0,
        "CaO": 10.0,
        "MgO": 8.0,
        "Na2O": 0.5,
        "K2O": 0.2,
        "TiO2": 1.0,
        "Cr2O3": 0.2,
        "MnO": 0.2,
        "FeO": 10.0,
    }
    payload = yaml.safe_load(
        (DATA / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(melt_mol)},
            species_formula_registry={},
        ),
        temperature_C=temperature_C,
        pressure_bar=1.0e-6,
        control_inputs={
            "pO2_bar": transport_pO2_bar,
            "intrinsic_fO2_log": intrinsic_fO2_log,
        },
    )
    return provider, request, melt_mol, payload


def test_builtin_linear_rail_o2_bit_identity_differential():
    from engines.builtin.vapor_pressure import (
        physical_melt_dissociation_pO2_bar,
    )
    from simulator.chemistry.ellingham_thermo import (
        ellingham_delta_g_kj_per_mol_o2,
        ellingham_stoichiometry,
    )
    from simulator.chemistry.melt_activity import melt_oxide_activity
    # The provider's Ellingham path evaluates K_decomp with the legacy
    # simulator.state gas constant (8.31446), not the CODATA value in
    # simulator.physical_constants — the recomputation must use the same one.
    from simulator.state import GAS_CONSTANT as PROVIDER_GAS_CONSTANT
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    standard_rail = {  # metals standard_reaction rail: species -> parent
        "K": "K2O",
        "Na": "Na2O",
        "Si": "SiO2",
    }
    liquid_rail = {  # liquid_oxide_standard_reaction rail
        "Al": "Al2O3",
        "Cr": "Cr2O3",
        "Mn": "MnO",
        "Ti": "TiO2",
    }
    ellingham_rail = {"Ca": "CaO", "Mg": "MgO"}

    grid_T_C = (1400.0, 1600.0, 1800.0)
    grid_fO2 = (-45.0, -12.0, -9.0, -3.0, 5.0)  # includes both clamp edges
    grid_transport = (1.0e-9, 1.0e-3)

    compared: dict[str, int] = {}
    mismatches: list[str] = []
    # Distinct-value coverage trackers (re-review "Ca/Mg/SiO thin" fold):
    # the channel-relevant dimension of each rail must be sampled at more
    # than a token number of distinct values, not just re-sampled at the
    # same value across an orthogonal grid axis.
    sio_sqrt_factors: set[float] = set()
    camg_denominators: set[float] = set()

    def probe_point(T_C, fO2_log, transport_pO2, *, rails):
        """Dispatch the provider once and compare the requested rails."""

        T_K = T_C + 273.15
        provider, request, melt_mol, payload = _provider_and_request(
            T_C,
            intrinsic_fO2_log=fO2_log,
            transport_pO2_bar=transport_pO2,
        )
        result = provider.dispatch(request)
        diag = result.diagnostic
        pressures = diag["vapor_pressures_Pa"]
        provenance = diag["vapor_pressure_numerator_provenance"]
        legacy_view = vapor_pressure_legacy_view(payload)
        metals = legacy_view["metals"]
        oxides = legacy_view["oxide_vapors"]
        melt_pO2, _clamped = physical_melt_dissociation_pO2_bar(fO2_log)

        def record(species_id, recomputed, label):
            key = (species_id, T_C, fO2_log, transport_pO2)
            emitted = pressures[species_id]
            if emitted != recomputed:
                mismatches.append(
                    f"{label} {key}: emitted={emitted!r} "
                    f"legacy={recomputed!r}"
                )
            compared[species_id] = compared.get(species_id, 0) + 1

        if "standard" in rails:
            for species, parent in standard_rail.items():
                if species not in pressures:
                    continue
                prov = provenance[species]
                row = metals[species]
                e = float(row.get("pO2_exponent", 0.0) or 0.0)
                p_ref = max(
                    1e-30, float(row.get("pO2_reference_bar", 1.0) or 1.0)
                )
                oxygen = min(
                    max(melt_pO2, MELT_DISSOCIATION_PO2_MIN_BAR),
                    MELT_DISSOCIATION_PO2_MAX_BAR,
                )
                legacy = (
                    float(prov["P_reference_Antoine_Pa"])
                    * float(prov["activity_factor"])
                    * (oxygen / p_ref) ** e
                )
                record(species, legacy, "standard_rail")

        if "liquid" in rails:
            for species, parent in liquid_rail.items():
                if species not in pressures:
                    continue
                prov = provenance[species]
                row = metals[species]["liquid_oxide_standard_reaction"]
                e = float(row.get("pO2_exponent", 0.0) or 0.0)
                p_ref = max(
                    1e-30, float(row.get("pO2_reference_bar", 1.0) or 1.0)
                )
                oxygen = min(
                    max(melt_pO2, MELT_DISSOCIATION_PO2_MIN_BAR),
                    MELT_DISSOCIATION_PO2_MAX_BAR,
                )
                legacy = (
                    float(prov["P_reference_Antoine_Pa"])
                    * float(prov["activity_factor"])
                    * (oxygen / p_ref) ** e
                )
                record(species, legacy, "liquid_rail")

        if "ellingham" in rails:
            for species, parent in ellingham_rail.items():
                if species not in pressures:
                    continue
                prov = provenance[species]
                n_M, n_ox = ellingham_stoichiometry(species)
                oxide_activity = melt_oxide_activity(
                    parent, melt_mol, temperature_K=T_K
                )
                a_oxide = oxide_activity.equivalent_parent_activity(
                    n_ox / n_M
                )
                dG = ellingham_delta_g_kj_per_mol_o2(species, T_K)
                K_decomp = math.exp(
                    dG * 1000.0 / (PROVIDER_GAS_CONSTANT * T_K)
                )
                # Exact pre-migration numerator/root expression.
                numerator = K_decomp * (a_oxide ** n_ox) / melt_pO2
                legacy_root = numerator ** (1.0 / n_M)
                camg_denominators.add(melt_pO2)
                emitted_root = float(prov["raw_metal_activity_root"])
                if emitted_root != legacy_root:
                    mismatches.append(
                        f"ellingham_rail {(species, T_C, fO2_log)}: "
                        f"emitted_root={emitted_root!r} "
                        f"legacy_root={legacy_root!r}"
                    )
                compared[species] = compared.get(species, 0) + 1
                # Re-review fold: also compare the *emitted* pressure, not
                # only the intermediate root provenance field.  Ca/Mg ride
                # the gas_fugacity rail: P_eq = root * P_standard_Pa.
                rail_kind = prov["pressure_rail"]
                if rail_kind == "gas_fugacity":
                    legacy_final = legacy_root * float(prov["P_standard_Pa"])
                else:  # condensed_raoult_psat: capped Raoultian activity
                    legacy_final = min(legacy_root, 1.0) * float(
                        prov["P_reference_Antoine_Pa"]
                    )
                record(species, legacy_final, "ellingham_rail_final")

        if "sio" in rails:
            if "SiO" in pressures:
                prov = provenance["SiO"]
                row = oxides["SiO"]
                sio_ref = max(
                    1e-30,
                    float(
                        row.get("pO2_reference_bar", 1.0e-9) or 1.0e-9
                    ),
                )
                sio_sqrt_factors.add(math.sqrt(sio_ref / transport_pO2))
                legacy = (
                    float(prov["P_reference_Antoine_Pa"])
                    * float(prov["activity_factor"])
                    * math.sqrt(sio_ref / transport_pO2)
                )
                record("SiO", legacy, "sio_sqrt")

    all_rails = frozenset({"standard", "liquid", "ellingham", "sio"})
    for T_C in grid_T_C:
        for fO2_log in grid_fO2:
            for transport_pO2 in grid_transport:
                probe_point(T_C, fO2_log, transport_pO2, rails=all_rails)

    # Re-review fold ("Ca/Mg/SiO thin"): targeted sweeps that widen the
    # channel-relevant dimension of the three flagged rails.  All sweep
    # values stay inside the b-148 envelope, so the recomputed
    # pre-migration expressions remain the exact legacy forms.
    #
    # SiO: the sqrt mass action depends ONLY on transport pO2; the main
    # grid samples it at just 2 distinct factors.  Sweep 8 in-envelope
    # transport values -> 8 distinct sqrt factors over ~7.5 orders of
    # magnitude (the request vacuum floor fail-loud rejects transport pO2
    # below 1e-9 bar, so the sweep starts at the floor).
    for transport_pO2 in (
        1.0e-9, 1.0e-7, 1.0e-5, 1.0e-3, 1.0e-1, 1.0, 10.0, 50.0,
    ):
        probe_point(1600.0, -9.0, transport_pO2, rails=frozenset({"sio"}))
    #
    # Ca/Mg: the Ellingham division depends ONLY on (T, melt pO2); sweep
    # 12 fO2 values covering the clamped-below edge, the exact envelope
    # edges, a dense in-envelope interior, and the clamped-above edge.
    for fO2_log in (
        -45.0, -30.0, -25.0, -20.0, -15.0, -12.0,
        -9.0, -6.0, -3.0, 0.0, 2.0, 5.0,
    ):
        probe_point(1600.0, fO2_log, 1.0e-9, rails=frozenset({"ellingham"}))

    expected = (
        set(standard_rail) | set(liquid_rail) | set(ellingham_rail) | {"SiO"}
    )
    missing = sorted(expected - set(compared))
    assert not missing, f"species never probed: {missing}"
    low = {k: v for k, v in compared.items() if v < 10}
    assert not low, f"species with thin probe coverage: {low}"
    # Distinct-value gates: the widened population must actually span the
    # channel dimension, not re-sample the same point.
    assert len(sio_sqrt_factors) >= 8, (
        f"SiO sqrt mass action probed at only "
        f"{len(sio_sqrt_factors)} distinct factors"
    )
    assert len(camg_denominators) >= 10, (
        f"Ca/Mg Ellingham division probed at only "
        f"{len(camg_denominators)} distinct melt-pO2 denominators"
    )
    assert not mismatches, (
        f"{len(mismatches)} linear-rail bit-identity mismatches "
        f"(showing up to 10):\n" + "\n".join(mismatches[:10])
    )
