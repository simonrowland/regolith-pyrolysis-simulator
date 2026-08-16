"""VR-4b: nasa7/nasa9/shomate runtime catalog evaluator families.

Fixture coverage proves generic compile/dispatch and production coverage proves
the explicitly activated analytical carrier rows and reaction algebra remain a
closed, reviewed set.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.condensation import _species_has_antoine_data
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_FIT_SEGMENTS,
    MG_NORMAL_BOILING_POINT_K,
    ellingham_metal_phase_kind,
    ellingham_segment_for_temperature,
)
from simulator.vapour_rail.catalog import (
    OUT_OF_RANGE_STATUS,
    RUNTIME_THERMO_EVALUATOR_FAMILIES,
    CatalogCompileError,
    compile_vapour_rail_catalog,
)
from simulator.vapour_rail.nasa_cea import (
    Nasa7Segment,
    Nasa9Segment,
    NasaCeaPolynomial,
    R_J_PER_MOL_K,
    ThermoState,
    reaction_equilibrium_constant,
)
from simulator.vapour_rail.shomate import (
    ShomateConventionError,
    ShomateDomainError,
    ShomatePolynomial,
    ShomateSegment,
    ShomateSegmentError,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ELLINGHAM = ROOT / "simulator" / "chemistry" / "ellingham_thermo.py"
NASA_MOD = ROOT / "simulator" / "vapour_rail" / "nasa_cea.py"
SHOMATE_MOD = ROOT / "simulator" / "vapour_rail" / "shomate.py"
CATALOG_MOD = ROOT / "simulator" / "vapour_rail" / "catalog.py"


# ---------------------------------------------------------------------------
# Hand anchors — NIST WebBook SRD 69 O2(g) 100–700 K (Chase 1998)
# ---------------------------------------------------------------------------
# Full A…H published on the NIST Chemistry WebBook O2 gas page (100–700 K).
_O2_SHOMATE_100_700 = (
    31.32234,
    -20.23531,
    57.86644,
    -36.50624,
    -0.007374,
    -8.903471,
    246.7945,
    0.0,
)
# Second published NIST value: S°(298.15 K) = 205.147 J/(mol·K) (JANAF / WebBook).
_NIST_O2_S_298_15 = 205.147
# WebBook / JANAF class Cp°(298.15 K) ≈ 29.376–29.38 J/(mol·K).
_NIST_O2_CP_298_15 = 29.376
# WebBook H°−H°298.15 at 500 K ≈ 6.088 kJ/mol (O2, 100–700 K segment).
_NIST_O2_H_MINUS_H298_500_KJ = 6.088


def _strata(
    *,
    species_id: str,
    formula: str,
    pressure_models: list,
    phase_properties: list | None = None,
    source_reactions: list | None = None,
    hot_train: str = "not_applicable",
    source_account: str = "process.fixture_pure_condensed",
) -> dict:
    """Minimal four-strata family for a single dormant thermo species."""
    reactions = source_reactions or []
    for model in pressure_models:
        pure_component = (
            model.get("pressure_kind") == "pure_component_saturation_pressure"
        )
        reaction_id = model.get("source_reaction_id")
        matched = [
            reaction
            for reaction in reactions
            if reaction.get("id") == reaction_id
        ]
        condensed_reactants = [
            str(reactant.get("formula", ""))
            for reaction in matched
            for reactant in reaction.get("reactants", [])
            if str(reactant.get("formula", "")).lower().endswith(
                ("(cr)", "(s)", "(l)", "(liq)", "(solid)")
            )
        ]
        if pure_component or condensed_reactants:
            component_id = condensed_reactants[0] if condensed_reactants else formula
            phase = (
                "condensed_liquid"
                if component_id.lower().endswith(("(l)", "(liq)"))
                else "condensed_solid"
            )
            model.setdefault("activity_semantics", "pure_condensed_phase")
            model.setdefault(
                "pure_condensed_phase_identity",
                {
                    "component_id": component_id,
                    "phase": phase,
                    "source_account": source_account,
                },
            )
    row: dict = {
        "formula": formula,
        "source_reactions": reactions,
        "pressure_models": pressure_models,
        "validation": {"status": "pending_validation", "anchor_refs": []},
        "molar_mass_g_mol": 1.0,
    }
    if phase_properties is not None:
        row["phase_properties"] = phase_properties
    return {
        "schema_version": 2,
        "families": {
            f"dormant_{species_id}_family": {
                "physical_properties": {"species": {species_id: row}},
                "fiat_routing": {
                    "plant_bin": None,
                    "engineering_capture_policy": "temperature_threshold",
                    "products_and_coproducts": [],
                    "process_or_terminal_destination": "process.condensation_train",
                },
                "vaporisation_coefficients": {
                    "evaporation_alpha": {"value": 1.0},
                    "alpha_domain_and_uncertainty": {},
                    "extrapolation_policy": "conservative_slope_continuation",
                    "out_of_range_status": OUT_OF_RANGE_STATUS,
                    "acquisition_flag": f"acquire:vr4b_dormant:{species_id}",
                },
                "code_metadata": {
                    "formula_id": species_id,
                    "source_account": source_account,
                    "request_rule": "source_inventory_present",
                    "solve_group_id": f"dormant_{species_id}_family",
                    "compatibility_projection": "metals",
                    "canonical_aliases": [species_id],
                    "hot_train_applicability": hot_train,
                    "hot_train_not_applicable_reason": (
                        "VR-4b dormant thermo row; flux activation deferred to RG epochs"
                    ),
                },
            }
        },
    }


def _nasa7_record(
    *,
    standard_state: str,
    a1: float,
    a6: float,
    a7: float,
    t_min: float = 200.0,
    t_max: float = 2000.0,
) -> dict:
    """Constant-Cp NASA-7 record (a2…a5 = 0)."""
    return {
        "evaluator_family": "nasa7",
        "standard_state": standard_state,
        "segments": [
            {
                "T_min_K": t_min,
                "T_max_K": t_max,
                "coefficients": [a1, 0.0, 0.0, 0.0, 0.0, a6, a7],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Shomate leaf — two published NIST values + loud gates
# ---------------------------------------------------------------------------


def test_shomate_matches_two_published_nist_webbook_values() -> None:
    poly = ShomatePolynomial(
        name="O2",
        standard_state="gas",
        segments=(ShomateSegment(100.0, 700.0, _O2_SHOMATE_100_700),),
        citation="NIST WebBook SRD 69 O2(g) 100-700 K; Chase 1998",
    )
    st = poly.evaluate(298.15)
    # Hand-independent algebra (not production path) for Cp / S / H−H298:
    A, B, C, D, E, F, G, H = _O2_SHOMATE_100_700
    t = 298.15 / 1000.0
    hand_cp = A + B * t + C * t**2 + D * t**3 + E / t**2
    hand_s = (
        A * math.log(t)
        + B * t
        + C * t**2 / 2.0
        + D * t**3 / 3.0
        - E / (2.0 * t**2)
        + G
    )
    hand_h_rel_kJ = (
        A * t + B * t**2 / 2.0 + C * t**3 / 3.0 + D * t**4 / 4.0 - E / t + F - H
    )
    assert st.cp_J_per_mol_K == pytest.approx(hand_cp, rel=0, abs=1e-12)
    assert st.s_J_per_mol_K == pytest.approx(hand_s, rel=0, abs=1e-12)
    # O2 elemental ΔfH°=0 (coeff H=0) → absolute H equals relative H−H298.
    assert st.h_J_per_mol == pytest.approx(1000.0 * hand_h_rel_kJ, rel=0, abs=1e-9)
    # Two published NIST WebBook / JANAF anchors (Cp, S):
    assert st.cp_J_per_mol_K == pytest.approx(_NIST_O2_CP_298_15, rel=5e-4)
    assert st.s_J_per_mol_K == pytest.approx(_NIST_O2_S_298_15, rel=1e-4)

    # P1-1 null-hypothesis: dropping the kJ→J factor in the H equation would
    # leave Cp/S green; H°−H°298 at 500 K is the anchor that goes red.
    st500 = poly.evaluate(500.0)
    t5 = 0.5
    hand_h500_kJ = (
        A * t5
        + B * t5**2 / 2.0
        + C * t5**3 / 3.0
        + D * t5**4 / 4.0
        - E / t5
        + F
        - H
    )
    assert st500.h_J_per_mol == pytest.approx(1000.0 * hand_h500_kJ, rel=0, abs=1e-9)
    assert st500.h_J_per_mol / 1000.0 == pytest.approx(
        _NIST_O2_H_MINUS_H298_500_KJ, rel=2e-3, abs=0.02
    )
    # Formation enthalpy derived from coeff H (=0 for elemental O2).
    assert poly.formation_enthalpy_J_per_mol() == pytest.approx(0.0, abs=0.0)


def test_shomate_segment_gap_and_missing_convention_fail_loudly() -> None:
    with pytest.raises(ShomateSegmentError, match="gap"):
        ShomatePolynomial(
            name="gap",
            standard_state="gas",
            segments=(
                ShomateSegment(100.0, 700.0, _O2_SHOMATE_100_700),
                ShomateSegment(800.0, 2000.0, _O2_SHOMATE_100_700),
            ),
        )
    with pytest.raises(ShomateConventionError, match="standard_state"):
        ShomatePolynomial(
            name="bad",
            standard_state="not_a_state",  # type: ignore[arg-type]
            segments=(ShomateSegment(100.0, 700.0, _O2_SHOMATE_100_700),),
        )
    poly = ShomatePolynomial(
        name="O2",
        standard_state="gas",
        segments=(ShomateSegment(100.0, 700.0, _O2_SHOMATE_100_700),),
    )
    with pytest.raises(ShomateDomainError, match="outside domain"):
        poly.evaluate(50.0)


# ---------------------------------------------------------------------------
# K(T) = exp(−ΔG_rxn / RT) — derivation + JANAF-class sanity
# ---------------------------------------------------------------------------


def test_reaction_K_from_delta_g_matches_pure_psat_and_janaf_class() -> None:
    """Source-reaction K(T) shares algebra with pure Psat; JANAF O2⇌2O anchor.

    Derivation (premise → algebra → units → sanity) is on
    :func:`reaction_equilibrium_constant`. Here we prove the pure-vaporization
    identity and a non-cancelling JANAF K for O₂ ⇌ 2 O near 3000 K.
    """
    # Constant-Cp monatomic-style gas + condensed with fixed Δ(G/RT).
    # Choose a6, a7 so that at T=1000 K, G_gas/(RT) − G_cond/(RT) = 2.0
    # ⇒ K = exp(−2) ≈ 0.1353 = Psat/P°.
    gas = NasaCeaPolynomial(
        name="M(g)",
        family="nasa_cea_7",
        standard_state="gas",
        segments=(
            Nasa7Segment(300.0, 2000.0, (2.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ),
    )
    # Shift condensed S integration constant so Δ(G/RT) = 2 at 1000 K.
    # G/(RT) = H/(RT) − S/R; for constant Cp with a6=0:
    #   H/RT = a1, S/R = a1 ln T + a7, G/RT = a1 − a1 ln T − a7
    # Δ(G/RT) = a7_c − a7_g when a1 equal. Want Δ = 2 ⇒ a7_c = 2.
    cond = NasaCeaPolynomial(
        name="M(cond)",
        family="nasa_cea_7",
        standard_state="condensed_solid",
        segments=(
            Nasa7Segment(300.0, 2000.0, (2.5, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0)),
        ),
    )
    T = 1000.0
    st_g = gas.evaluate(T)
    st_c = cond.evaluate(T)
    K = reaction_equilibrium_constant(
        [(+1.0, st_g), (-1.0, st_c)],
        T_K=T,
    )
    ratio = gas.pure_psat_over_Pstd(cond, T)
    assert K == pytest.approx(ratio, rel=0, abs=1e-12)
    assert K == pytest.approx(math.exp(-2.0), rel=0, abs=1e-12)

    # JANAF-class non-cancelling anchor: O₂ ⇌ 2 O near 3000 K.
    # NIST-JANAF: ΔfG°[O(g)] ≈ 54.327 kJ/mol, ΔfG°[O2]=0 at the table T
    # where log10 Kf[O] ≈ −0.946 ⇒ K = 10^(−1.892) ≈ 0.01282.
    # ΔG° = 108.654 kJ/mol → K = exp(−ΔG/RT) ≈ 0.012829 at T=3000 K.
    # Null-hypothesis: identity K=1 (O2=O2) stays green under G corruption.
    T_hi = 3000.0
    delta_f_g_O_J = 54_327.0
    delta_g_rxn_J = 2.0 * delta_f_g_O_J  # 108_654 J/mol for O2 → 2O
    K_janaf_dG = math.exp(-delta_g_rxn_J / (R_J_PER_MOL_K * T_hi))
    K_janaf_log10 = 10.0 ** (-1.892)
    assert K_janaf_dG == pytest.approx(0.012829, rel=1e-4)
    assert K_janaf_log10 == pytest.approx(0.012823, rel=1e-3)

    # Build constant-Cp NASA-7 states whose G match the JANAF formation values
    # so coefficient / integration regressions go red (not a tautological K=1).
    g_O_over_RT = delta_f_g_O_J / (R_J_PER_MOL_K * T_hi)
    a7_O = 2.5 * (1.0 - math.log(T_hi)) - g_O_over_RT
    a7_O2 = 3.5 * (1.0 - math.log(T_hi))  # elemental G_f = 0
    o_atom = NasaCeaPolynomial(
        name="O",
        family="nasa_cea_7",
        standard_state="gas",
        segments=(
            Nasa7Segment(200.0, 6000.0, (2.5, 0.0, 0.0, 0.0, 0.0, 0.0, a7_O)),
        ),
        citation="JANAF-class ΔfG°[O]≈54.327 kJ/mol near 3000 K",
    )
    o2 = NasaCeaPolynomial(
        name="O2",
        family="nasa_cea_7",
        standard_state="gas",
        segments=(
            Nasa7Segment(200.0, 6000.0, (3.5, 0.0, 0.0, 0.0, 0.0, 0.0, a7_O2)),
        ),
        citation="elemental standard state ΔfG°=0",
    )
    K_dissoc = reaction_equilibrium_constant(
        [(+2.0, o_atom.evaluate(T_hi)), (-1.0, o2.evaluate(T_hi))],
        T_K=T_hi,
    )
    assert K_dissoc == pytest.approx(K_janaf_dG, rel=0, abs=1e-12)
    assert K_dissoc == pytest.approx(0.012829, rel=1e-3)

    # Direct ThermoState path (same ΔG/RT algebra) for belt-and-suspenders.
    K_states = reaction_equilibrium_constant(
        [
            (+2.0, ThermoState(T_hi, 2.5, 2.5, 0.0, g_O_over_RT)),
            (-1.0, ThermoState(T_hi, 3.5, 3.5, 0.0, 0.0)),
        ],
        T_K=T_hi,
    )
    assert K_states == pytest.approx(K_janaf_dG, rel=0, abs=1e-12)


# ---------------------------------------------------------------------------
# Catalog compile + dispatch on dormant fixture rows
# ---------------------------------------------------------------------------


def test_nasa9_dormant_fixture_compiles_and_dispatches_without_flux() -> None:
    """Dormant nasa9 pure-psat row: compile + evaluate; hot train not applicable."""
    payload = _strata(
        species_id="M",
        formula="M",
        pressure_models=[
            {
                "evaluator": "nasa9",  # short alias accepted
                "pressure_kind": "pure_component_saturation_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "reference_pressure_Pa": 100_000.0,
                "gas_thermo_record": _nasa7_record(
                    standard_state="gas", a1=2.5, a6=0.0, a7=0.0
                ),
                "condensed_thermo_record": _nasa7_record(
                    standard_state="condensed_solid", a1=2.5, a6=0.0, a7=2.0
                ),
            }
        ],
        phase_properties=[
            {
                "phase": "gas",
                "evaluator": "nasa9",
                "thermo_record": _nasa7_record(
                    standard_state="gas", a1=2.5, a6=0.0, a7=0.0
                ),
            },
            {
                "phase": "condensed_solid",
                "evaluator": "nasa7",
                "thermo_record": _nasa7_record(
                    standard_state="condensed_solid", a1=2.5, a6=0.0, a7=2.0
                ),
            },
        ],
        hot_train="not_applicable",
    )
    # Force gas record family to nasa_cea_9 with a NASA-9 segment so the
    # declared evaluator: nasa9 actually lands on the NASA-9 module.
    o2_like = {
        "evaluator_family": "nasa9",
        "standard_state": "gas",
        "segments": [
            {
                "T_min_K": 200.0,
                "T_max_K": 2000.0,
                "a_coefficients": [
                    0.0,
                    0.0,
                    2.5,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "b1": 0.0,
                "b2": 0.0,
            }
        ],
    }
    cond_nasa9 = {
        "evaluator_family": "nasa9",
        "standard_state": "condensed_solid",
        "segments": [
            {
                "T_min_K": 200.0,
                "T_max_K": 2000.0,
                "a_coefficients": [
                    0.0,
                    0.0,
                    2.5,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "b1": 0.0,
                "b2": 2.0,  # Δ(G/RT)=2 relative to gas b2=0 when a equal
            }
        ],
    }
    payload["families"]["dormant_M_family"]["physical_properties"]["species"]["M"][
        "pressure_models"
    ][0]["gas_thermo_record"] = o2_like
    payload["families"]["dormant_M_family"]["physical_properties"]["species"]["M"][
        "pressure_models"
    ][0]["condensed_thermo_record"] = cond_nasa9

    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    species = catalog.species["M"]
    assert species.evaluator is not None
    assert species.evaluator.evaluator_family == "nasa_cea_9"
    assert species.code_metadata.hot_train_applicability == "not_applicable"
    # Dispatch: pressure at 1000 K = P° · exp(−2).
    ev = catalog.evaluator_for("M").evaluate(1000.0)
    expected = 100_000.0 * math.exp(-2.0)
    assert ev.pressure_pa == pytest.approx(expected, rel=1e-9)
    assert ev.validation_status.value == "pending_validation"


def test_shomate_dormant_fixture_compiles_and_dispatches() -> None:
    """Dormant shomate pure-psat row uses the Shomate family end-to-end."""
    # Relative form without ΔfH: G/RT = (H−H298)/(RT) − S/R.
    # Two segments with identical A…E, F, H but different G shift S and thus G.
    base = list(_O2_SHOMATE_100_700)
    gas_coeffs = {
        "A": base[0],
        "B": base[1],
        "C": base[2],
        "D": base[3],
        "E": base[4],
        "F": base[5],
        "G": base[6],
        "H": base[7],
    }
    # Lower condensed entropy (smaller G coeff) → lower G_cond → lower Psat.
    cond_coeffs = dict(gas_coeffs)
    cond_coeffs["G"] = base[6] - 20.0

    payload = _strata(
        species_id="X",
        formula="X",
        pressure_models=[
            {
                "evaluator_family": "shomate",
                "pressure_kind": "pure_component_saturation_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [200.0, 600.0]},
                "gas_thermo_record": {
                    "evaluator": "shomate",
                    "standard_state": "gas",
                    "segments": [
                        {
                            "T_min_K": 100.0,
                            "T_max_K": 700.0,
                            "coefficients": gas_coeffs,
                        }
                    ],
                },
                "condensed_thermo_record": {
                    "evaluator": "shomate",
                    "standard_state": "condensed_solid",
                    "segments": [
                        {
                            "T_min_K": 100.0,
                            "T_max_K": 700.0,
                            "coefficients": cond_coeffs,
                        }
                    ],
                },
            }
        ],
        hot_train="not_applicable",
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    assert catalog.species["X"].evaluator is not None
    assert catalog.species["X"].evaluator.evaluator_family == "shomate"
    T = 298.15
    ev = catalog.evaluator_for("X").evaluate(T)
    # Same A…F,H on both phases; gas G coeff is +20 vs condensed ⇒
    # S_gas − S_cond = +20 J/(mol·K), H_gas = H_cond (incl. formation from H=0)
    # ⇒ ΔG = −T·20, K = exp(20/R), P = P°·exp(20/R).
    # Null-hypothesis: finiteness-only assertion stays green under 1000× H bug.
    expected = 100_000.0 * math.exp(20.0 / R_J_PER_MOL_K)
    assert ev.pressure_pa == pytest.approx(expected, rel=1e-9)
    assert catalog.species["X"].code_metadata.hot_train_applicability == (
        "not_applicable"
    )


def test_nasa7_source_reaction_K_dispatch() -> None:
    """Source-reaction thermo path: K=exp(−dG/RT) → unit-activity partial P."""
    # Reaction: M(cr) = M(g). species_thermo keys match reaction formulas;
    # parentheses are stripped for atom balance (``M(cr)`` → element M).
    payload = _strata(
        species_id="M",
        formula="M",
        source_reactions=[
            {
                "id": "m_subl",
                "reactants": [{"formula": "M(cr)", "stoichiometry": 1.0}],
                "products": [{"formula": "M", "stoichiometry": 1.0}],
            }
        ],
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": "m_subl",
                "species_thermo": {
                    "M": _nasa7_record(
                        standard_state="gas", a1=2.5, a6=0.0, a7=0.0
                    ),
                    "M(cr)": _nasa7_record(
                        standard_state="condensed_solid", a1=2.5, a6=0.0, a7=2.0
                    ),
                },
            }
        ],
        hot_train="not_applicable",
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    ev = catalog.evaluator_for("M").evaluate(1000.0)
    expected = 100_000.0 * math.exp(-2.0)
    assert ev.pressure_pa == pytest.approx(expected, rel=1e-9)


def test_unknown_thermo_family_fails_loudly() -> None:
    payload = _strata(
        species_id="Z",
        formula="Z",
        pressure_models=[
            {
                "evaluator_family": "barin_table",
                "pressure_kind": "pure_component_saturation_pressure",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [300.0, 400.0]},
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="later chunk|unknown"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


# ---------------------------------------------------------------------------
# Phase-transition breakpoints remain Ellingham-single-home
# ---------------------------------------------------------------------------


def test_phase_transition_breakpoints_remain_ellingham_single_home() -> None:
    """NASA/Shomate supply energetics only — not physical transition locations.

    Bound: Ellingham owns multiphase breakpoints (metal boil, oxide transitions).
    Thermo polynomials may have segment joins for coefficient continuity, but
    those joins are **not** tagged or exported as physical transition authorities.
    Assert the consuming Ellingham API is unchanged under NASA/Shomate segment
    join perturbation (not just source-string bans).
    """
    ellingham_src = ELLINGHAM.read_text()
    nasa_src = NASA_MOD.read_text()
    shomate_src = SHOMATE_MOD.read_text()
    catalog_src = CATALOG_MOD.read_text()

    # Ellingham is the multiphase segment authority (phase breakpoints present).
    assert "ELLINGHAM_FIT_SEGMENTS" in ellingham_src
    assert "phase breakpoint" in ellingham_src or "phase transition" in ellingham_src

    # NASA / Shomate modules must not mint physical_transition or fit_knot tags.
    for src, label in ((nasa_src, "nasa_cea"), (shomate_src, "shomate")):
        assert "physical_transition" not in src, label
        assert "fit_knot" not in src, label

    # Catalog runtime thermo path documents the boundary (energetics only).
    assert "Ellingham remains the single home" in catalog_src
    assert "nasa_cea_7" in RUNTIME_THERMO_EVALUATOR_FAMILIES
    assert "nasa_cea_9" in RUNTIME_THERMO_EVALUATOR_FAMILIES
    assert "shomate" in RUNTIME_THERMO_EVALUATOR_FAMILIES

    # Consuming API authority: snapshot Ellingham Mg boil + segment selection
    # before building NASA/Shomate polys with non-Ellingham segment joins.
    mg_boil_before = float(MG_NORMAL_BOILING_POINT_K)
    segs_before = {
        metal: tuple(tuple(float(x) for x in s.range_K) for s in segments)
        for metal, segments in ELLINGHAM_FIT_SEGMENTS.items()
    }
    probe_T = 1800.0
    metals = list(ELLINGHAM_FIT_SEGMENTS)[:5]
    phase_before = {
        metal: ellingham_metal_phase_kind(metal, probe_T) for metal in metals
    }
    seg_at_before = {
        metal: ellingham_segment_for_temperature(metal, probe_T) for metal in metals
    }

    # Perturb NASA/Shomate segment joins away from any Ellingham breakpoint.
    # Null-hypothesis: a poly-derived transition_temperature_K API would
    # change consumer results; today no such API exists and Ellingham is stable.
    weird_join = 1234.5
    nasa = NasaCeaPolynomial(
        name="perturb",
        family="nasa_cea_7",
        standard_state="gas",
        segments=(
            Nasa7Segment(200.0, weird_join, (2.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            Nasa7Segment(weird_join, 3000.0, (2.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ),
    )
    shomate = ShomatePolynomial(
        name="perturb",
        standard_state="gas",
        segments=(
            ShomateSegment(100.0, weird_join, _O2_SHOMATE_100_700),
            ShomateSegment(weird_join, 2000.0, _O2_SHOMATE_100_700),
        ),
    )
    assert not hasattr(nasa, "transition_temperature_K")
    assert not hasattr(nasa, "boiling_point_K")
    assert not hasattr(shomate, "transition_temperature_K")
    assert not hasattr(shomate, "boiling_point_K")
    # Segment joins are coefficient-cover boundaries only — not physical boils.
    assert nasa.segments[0].T_max_K == weird_join
    assert shomate.segments[0].T_max_K == weird_join

    assert float(MG_NORMAL_BOILING_POINT_K) == mg_boil_before
    for metal, bounds in segs_before.items():
        got = tuple(
            tuple(float(x) for x in s.range_K)
            for s in ELLINGHAM_FIT_SEGMENTS[metal]
        )
        assert got == bounds
    for metal, kind in phase_before.items():
        assert ellingham_metal_phase_kind(metal, probe_T) == kind
    for metal, seg in seg_at_before.items():
        assert ellingham_segment_for_temperature(metal, probe_T) is seg


# ---------------------------------------------------------------------------
# Production activation allowlist
# ---------------------------------------------------------------------------


def test_production_vapor_pressures_has_only_reviewed_active_thermo_carriers() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    active_thermo = []
    for sp_id, sp in catalog.species.items():
        if sp.evaluator is None:
            continue
        fam = sp.evaluator.evaluator_family
        if fam in RUNTIME_THERMO_EVALUATOR_FAMILIES:
            active_thermo.append((sp_id, fam, sp.code_metadata.hot_train_applicability))
    # 2026-08-05 MC-4 wave-1 union: recomputed from the compiled catalog by
    # execution (P2O5_gas absent — tombstone restored per b-133).
    assert active_thermo == [
        ("Si", "nasa_cea_9", "applicable"),
        ("TiO", "nasa_cea_9", "applicable"),
        ("TiO2_gas", "nasa_cea_9", "applicable"),
        ("CaO_gas", "nasa_cea_9", "applicable"),
        ("AlO", "nasa_cea_9", "applicable"),
        ("Al2O", "nasa_cea_9", "applicable"),
        ("Al2", "nasa_cea_9", "applicable"),
        ("Al2O2", "nasa_cea_9", "applicable"),
        ("Al2O3_gas", "nasa_cea_9", "applicable"),
        ("AlO2", "nasa_cea_9", "applicable"),
        ("Ca2", "nasa_cea_9", "applicable"),
        ("CrO", "nasa_cea_9", "applicable"),
        ("CrO2", "nasa_cea_9", "applicable"),
        ("CrO3", "nasa_cea_9", "applicable"),
        ("PO", "nasa_cea_9", "stage0_only"),
        ("PO2", "nasa_cea_9", "stage0_only"),
        ("P4O6", "nasa_cea_9", "stage0_only"),
        ("P4O10", "nasa_cea_9", "stage0_only"),
        ("P2", "nasa_cea_9", "stage0_only"),
        ("P4", "nasa_cea_9", "stage0_only"),
        ("K2", "nasa_cea_9", "applicable"),
        ("K2O_gas", "nasa_cea_9", "applicable"),
        ("Mg2", "nasa_cea_9", "applicable"),
        ("MgO_gas", "nasa_cea_9", "applicable"),
        ("Na2", "nasa_cea_9", "applicable"),
        ("Na2O_gas", "nasa_cea_9", "applicable"),
        ("Si2", "nasa_cea_9", "applicable"),
        ("Si3", "nasa_cea_9", "applicable"),
        ("SiO2_gas", "nasa_cea_9", "applicable"),
    ]
    # Production still compiles the pre-existing Antoine / standard_reaction rows.
    assert "Na" in catalog.species
    assert catalog.species["Na"].evaluator is not None
    assert catalog.species["Na"].evaluator.evaluator_family in {
        "antoine",
        "standard_reaction_term",
        "tabulated_equilibrium",
    }


# ---------------------------------------------------------------------------
# Production phosphorus carriers: CEA-grounded source-reaction thermo
# ---------------------------------------------------------------------------


def test_production_phosphorus_thermo_matches_local_cea_extract() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    store = yaml.safe_load(
        (DATA / "literature" / "extracts" / "nasa-cea-thermo.yaml").read_text()
    )
    anchors = payload["cea_phosphorus_thermo"]
    store_to_anchor = {
        "PO": "PO",
        "PO2": "PO2",
        "P2": "P2",
        "P4": "P4",
        "P4O6": "P4O6",
        "P4O10": "P4O10",
        "P4O10_L": "P4O10_liquid",
    }
    assert len(store["species"]) >= 161
    assert set(store_to_anchor) <= set(store["species"])
    p2o5_gas_observation = store["species"]["P2O5"]["observations"][0]
    assert p2o5_gas_observation["type"] == "gibbs_table"
    assert p2o5_gas_observation["phase"] == "gas"
    assert p2o5_gas_observation["locator"]["record"] == "P2O5"
    assert store["species"]["P4O10_L"]["observations"][0]["regime"] == (
        "condensed_liquid_standard_state_thermo"
    )
    for store_id, anchor_id in store_to_anchor.items():
        observations = store["species"][store_id]["observations"]
        assert len(observations) == 1
        assert observations[0]["type"] == "gibbs_table"
        values = observations[0]["values"]
        anchor = anchors[anchor_id]
        for field, expected in values.items():
            assert anchor[field] == expected

    liquid = anchors["P4O10_liquid"]
    parent_basis = anchors["P2O5_liquid_component_basis"]
    assert parent_basis["molecular_weight_g_per_mol"] == pytest.approx(
        0.5 * liquid["molecular_weight_g_per_mol"]
    )
    assert parent_basis["delta_f_H_298_15_J_per_mol"] == pytest.approx(
        0.5 * liquid["delta_f_H_298_15_J_per_mol"]
    )
    for parent_segment, liquid_segment in zip(
        parent_basis["segments"], liquid["segments"], strict=True
    ):
        assert parent_segment["a_coefficients"] == pytest.approx(
            [0.5 * value for value in liquid_segment["a_coefficients"]]
        )
        assert parent_segment["b1"] == pytest.approx(0.5 * liquid_segment["b1"])
        assert parent_segment["b2"] == pytest.approx(0.5 * liquid_segment["b2"])


def test_production_phosphorus_carriers_use_cea_reaction_thermo() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    expected = {
        "PO": (-0.75, 0.5),
        "PO2": (-0.25, 0.5),
        "P2": (-2.5, 1.0),
        "P4": (-5.0, 2.0),
        "P4O6": (-2.0, 2.0),
        "P4O10": (0.0, 2.0),
        # The 2026-08-05 Wave-1 worklist explicitly promotes the exact-key
        # CEA P2O5(g) phase-transfer row; this supersedes the old tombstone.
    }

    for species_id, (pO2_exponent, activity_exponent) in expected.items():
        species = catalog.species[species_id]
        evaluator = species.evaluator
        assert evaluator is not None
        assert evaluator.evaluator_family == "nasa_cea_9"
        assert evaluator.pO2_exponent == pytest.approx(pO2_exponent)
        assert evaluator.activity_exponent == pytest.approx(activity_exponent)
        assert species.code_metadata.request_rule == "stage0_only"
        assert species.code_metadata.hot_train_applicability == "stage0_only"
        assert species.validation_status.value == "pending_validation"

        at_reference = evaluator.evaluate(
            1473.15,
            source_activity=1.0e-8,
            pO2_bar=evaluator.pO2_reference_bar,
        )
        at_c0b = evaluator.evaluate(
            1473.15,
            source_activity=1.0e-8,
            pO2_bar=0.009,
        )
        assert math.isfinite(at_reference.pressure_pa)
        assert at_reference.pressure_pa > 0.0
        expected_ratio = (0.009 / evaluator.pO2_reference_bar) ** pO2_exponent
        assert at_c0b.pressure_pa / at_reference.pressure_pa == pytest.approx(
            expected_ratio
        )

    # A melt source-reaction partial pressure is not a pure-gas saturation
    # curve.  Without independent condensation data P remains in overhead gas.
    legacy_oxide_vapors = catalog.legacy_view()["oxide_vapors"]
    for carrier in expected:
        assert legacy_oxide_vapors[carrier]["condensation_saturation_model"] == (
            "unavailable_source_reaction_not_psat"
        )
        assert not _species_has_antoine_data(carrier, vapor_pressure_data=payload)


def test_production_ti_carrier_ratios_reproduce_cea_exchange_algebra() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    T_K = 2000.0  # inside the shared Ti base/composite validated domain
    pO2_bar = 1.0e-6
    p_ti = catalog.evaluator_for("Ti").evaluate(
        T_K, source_activity=1.0, pO2_bar=pO2_bar
    ).pressure_pa
    p_tio = catalog.evaluator_for("TiO").evaluate(
        T_K, source_activity=1.0, pO2_bar=pO2_bar
    ).pressure_pa
    p_tio2 = catalog.evaluator_for("TiO2_gas").evaluate(
        T_K, source_activity=1.0, pO2_bar=pO2_bar
    ).pressure_pa

    # Premise: all three rows share the same liquid-TiO2 base, which cancels.
    # Gas exchange gives TiO/Ti=K1*fO2^0.5 and TiO2/Ti=K2*fO2.
    # Unit check: pressure ratios, K, and fO2/P° are dimensionless. Sanity:
    # lowering pO2 by 1e-6 must lower these ratios by 3 and 6 dex respectively.
    assert math.log10(p_tio / p_ti) == pytest.approx(5.266539, abs=1.0e-5)
    assert math.log10(p_tio2 / p_ti) == pytest.approx(7.606222, abs=1.0e-5)

    p_tio_low = catalog.evaluator_for("TiO").evaluate(
        T_K, source_activity=1.0, pO2_bar=1.0e-12
    ).pressure_pa
    p_tio2_low = catalog.evaluator_for("TiO2_gas").evaluate(
        T_K, source_activity=1.0, pO2_bar=1.0e-12
    ).pressure_pa
    p_ti_low = catalog.evaluator_for("Ti").evaluate(
        T_K, source_activity=1.0, pO2_bar=1.0e-12
    ).pressure_pa
    assert math.log10(p_tio_low / p_ti_low) == pytest.approx(
        math.log10(p_tio / p_ti) - 3.0
    )
    assert math.log10(p_tio2_low / p_ti_low) == pytest.approx(
        math.log10(p_tio2 / p_ti) - 6.0
    )

    for species_id in ("TiO", "TiO2_gas"):
        evaluator = catalog.evaluator_for(species_id)
        # Composite OOR uses the physical composite (base Antoine × CEA
        # K_ex at the actual T), not log-linear slope continuation of the
        # composed surface (b-145). Intent of this block (b-142): continuity
        # at the floor — no cliff, matching first derivative across the edge,
        # and smooth local curvature on the sub-floor side.
        assert evaluator.reference_model.physical_composite_ood is True
        samples = [
            evaluator.evaluate(T, source_activity=1.0, pO2_bar=pO2_bar)
            for T in (1922.0, 1923.0, 1924.0)
        ]
        assert [sample.out_of_range for sample in samples] == [True, True, False]
        assert all(
            math.isfinite(sample.pressure_pa) and sample.pressure_pa > 0.0
            for sample in samples
        )
        # Premise: physical composite is one smooth function of T across the
        # 1923.15 K floor (status flips; value does not). Algebra: adjacent
        # log increments are log10(P_2/P_1), so a finite, same-sign,
        # sub-0.1-decade pair rules out a zero cliff or local reversal. Units:
        # pressure ratios are dimensionless. Sanity: 1922/1923/1924 K follows
        # one smooth monotone trend while only the two sub-floor points are OOR;
        # at the floor, OOR evaluation reproduces the in-domain edge value.
        log_pressures = [math.log10(sample.pressure_pa) for sample in samples]
        increments = [
            right - left for left, right in zip(log_pressures, log_pressures[1:])
        ]
        assert increments[0] * increments[1] > 0.0
        assert max(abs(increment) for increment in increments) < 0.1

        boundary = 1923.15
        # Exact floor match: physical composite at T_low equals in-domain value.
        floor_oor = evaluator.evaluate(
            boundary - 1.0e-9, source_activity=1.0, pO2_bar=pO2_bar
        )
        floor_in = evaluator.evaluate(
            boundary, source_activity=1.0, pO2_bar=pO2_bar
        )
        assert floor_oor.out_of_range is True
        assert floor_in.out_of_range is False
        assert math.log10(floor_oor.pressure_pa) == pytest.approx(
            math.log10(floor_in.pressure_pa), abs=1.0e-9
        )

        epsilon_K = 1.0e-3
        boundary_pressures = [
            evaluator.evaluate(T, source_activity=1.0, pO2_bar=pO2_bar).pressure_pa
            for T in (boundary - epsilon_K, boundary, boundary + epsilon_K)
        ]
        below_slope = math.log10(
            boundary_pressures[1] / boundary_pressures[0]
        ) / epsilon_K
        above_slope = math.log10(
            boundary_pressures[2] / boundary_pressures[1]
        ) / epsilon_K
        assert below_slope == pytest.approx(above_slope, rel=1.0e-4, abs=1.0e-8)

        # Sub-floor local slope continuity (physical thermo curvature is C1;
        # previously this point was the cubic blend join for slope continuation).
        ramp_edge = boundary - 10.0
        ramp_epsilon_K = 1.0e-5
        ramp_edge_pressures = [
            evaluator.evaluate(T, source_activity=1.0, pO2_bar=pO2_bar).pressure_pa
            for T in (
                ramp_edge - ramp_epsilon_K,
                ramp_edge,
                ramp_edge + ramp_epsilon_K,
            )
        ]
        outside_slope = math.log10(
            ramp_edge_pressures[1] / ramp_edge_pressures[0]
        ) / ramp_epsilon_K
        blended_slope = math.log10(
            ramp_edge_pressures[2] / ramp_edge_pressures[1]
        ) / ramp_epsilon_K
        assert outside_slope == pytest.approx(
            blended_slope, rel=1.0e-4, abs=1.0e-8
        )


def test_production_al2o_activity_power_is_stoichiometric_square() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    evaluator = compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    ).evaluator_for("Al2O")
    p_unity = evaluator.evaluate(
        2000.0, source_activity=1.0, pO2_bar=1.0e-8
    ).pressure_pa
    p_half = evaluator.evaluate(
        2000.0, source_activity=0.5, pO2_bar=1.0e-8
    ).pressure_pa
    assert p_half / p_unity == pytest.approx(0.25)


def test_production_al2o_depletion_underflow_is_typed_positive_floor() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    evaluator = compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    ).evaluator_for("Al2O")

    result = evaluator.evaluate(
        2000.0, source_activity=1.0e-200, pO2_bar=1.0e-8
    )

    assert result.pressure_pa == math.nextafter(0.0, math.inf)
    assert result.status == "numerical_underflow_floor"
    assert result.acquisition_flag == "pressure_below_float_range:Al2O"


def test_production_extract_reference_fails_closed_on_unknown_observation() -> None:
    payload = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    model = payload["families"]["oxide_vapors_tio_family"]["physical_properties"][
        "species"
    ]["TiO"]["pressure_models"][0]
    model["species_thermo"]["TiO"]["extract_ref"]["observation_id"] = (
        "cea_missing_gibbs"
    )
    with pytest.raises(CatalogCompileError, match="must resolve exactly once"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


# ---------------------------------------------------------------------------
# P1/P2 catalog algebra + fail-closed gates (review-vr4b-cx / review-vr4b-km)
# ---------------------------------------------------------------------------


def _const_nasa7(
    *,
    standard_state: str,
    a1: float = 2.5,
    a6: float = 0.0,
    a7: float = 0.0,
    t_min: float = 200.0,
    t_max: float = 2000.0,
) -> dict:
    return _nasa7_record(
        standard_state=standard_state,
        a1=a1,
        a6=a6,
        a7=a7,
        t_min=t_min,
        t_max=t_max,
    )


def test_source_reaction_o2_power_algebra_nu_v_two() -> None:
    """2 MO(cr) → 2 M(g) + O2: p_M/P° = [K / (p_O2/P°)]^{1/2}.

    CX P1 / KM P2-1: using −pO2_exponent (= +0.5) then rooting by ν_v double-
    applies 1/ν_v. With K=1, pO2_ref=0.01 bar → correct P=1e6 Pa; the broken
    path yields ~3.16e5 Pa.

    K=1 construction: a1_M = a1_MO, a1_O2 = 0, a7=0 ⇒
    Δ(G/RT) = (2 a1_M + a1_O2 − 2 a1_MO)(1−ln T) = 0 at every T.
    """
    thermo = {
        "MO(cr)": _const_nasa7(standard_state="condensed_solid", a1=2.5),
        "M": _const_nasa7(standard_state="gas", a1=2.5),
        # a1_O2=0 keeps ΔG=0 when M and MO share a1 (not physical Cp; algebra fixture).
        "O2(g)": _const_nasa7(standard_state="gas", a1=0.0),
    }
    payload = _strata(
        species_id="M",
        formula="M",
        source_reactions=[
            {
                "id": "mo_subl",
                "reactants": [{"formula": "MO(cr)", "stoichiometry": 2.0}],
                "products": [
                    {"formula": "M", "stoichiometry": 2.0},
                    {"formula": "O2(g)", "stoichiometry": 1.0},
                ],
            }
        ],
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": "mo_subl",
                "pO2_reference_bar": 0.01,
                "pO2_exponent": -0.5,  # −ν_O2/ν_v; must match stoich
                "oxygen_fugacity_channel": "intrinsic_melt",
                "species_thermo": thermo,
            }
        ],
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    # K=1, p_O2/P°=0.01, ν_v=2, ν_O2=1 → p_M/P° = (1/0.01)^{1/2} = 10
    # P_M = 10 * 1e5 = 1e6 Pa (CX independent recompute).
    ev = catalog.evaluator_for("M").evaluate(1000.0, pO2_bar=0.01)
    assert ev.pressure_pa == pytest.approx(1_000_000.0, rel=1e-9)
    # Broken double-root path would give ~316228 Pa (CX probe).
    assert abs(ev.pressure_pa - 316_227.766) > 1e3
    # Outer exponent derived/validated as −0.5.
    assert catalog.species["M"].evaluator.pO2_exponent == pytest.approx(-0.5)


def test_source_reaction_o2_phase_suffix_and_derived_exponent() -> None:
    """O2(g) must count as O2 (KM P2-2); omitted pO2_exponent is derived."""
    thermo = {
        "MO(cr)": _const_nasa7(standard_state="condensed_solid", a1=2.5),
        "M(g)": _const_nasa7(standard_state="gas", a1=2.5),
        "O2(g)": _const_nasa7(standard_state="gas", a1=0.0),
    }
    payload = _strata(
        species_id="M",
        formula="M",
        source_reactions=[
            {
                "id": "mo_subl",
                "reactants": [{"formula": "MO(cr)", "stoichiometry": 2.0}],
                "products": [
                    {"formula": "M(g)", "stoichiometry": 2.0},
                    {"formula": "O2(g)", "stoichiometry": 1.0},
                ],
            }
        ],
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": "mo_subl",
                "pO2_reference_bar": 0.21,
                "oxygen_fugacity_channel": "intrinsic_melt",
                # pO2_exponent intentionally omitted → derive −0.5 from stoich
                "species_thermo": thermo,
            }
        ],
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    assert catalog.species["M"].evaluator.pO2_exponent == pytest.approx(-0.5)
    # K=1, p_O2/P°=0.21 → p_M = P° * sqrt(1/0.21)
    expected = 100_000.0 * math.sqrt(1.0 / 0.21)
    ev = catalog.evaluator_for("M").evaluate(1000.0, pO2_bar=0.21)
    assert ev.pressure_pa == pytest.approx(expected, rel=1e-9)
    # Null-hypothesis: missing O2 recognition would drop (pO2) factor →
    # p_M = P° * K^{1/2} = 1e5 (ratio wrong/correct ≈ 0.21^{0.5} wait no —
    # missing O2 gives p_M/P° = K^{1/2} = 1 → 1e5 vs correct 2.18e5).


def test_equilibrium_partial_requires_source_reaction() -> None:
    """Null source_reaction_id must not silently become pure-Psat (CX P1)."""
    payload = _strata(
        species_id="M",
        formula="M",
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": None,
                "gas_thermo_record": _const_nasa7(standard_state="gas", a7=0.0),
                "condensed_thermo_record": _const_nasa7(
                    standard_state="condensed_solid", a7=2.0
                ),
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="source_reaction_id"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_source_reaction_missing_target_vapor_fails_closed() -> None:
    """Condensed-only reaction must not invent vapor_nu=1 (CX P1 / KM P2-3)."""
    payload = _strata(
        species_id="M",
        formula="M",
        source_reactions=[
            {
                "id": "oxide_only",
                "reactants": [{"formula": "MO(cr)", "stoichiometry": 1.0}],
                "products": [{"formula": "MO(l)", "stoichiometry": 1.0}],
            }
        ],
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": "oxide_only",
                "species_thermo": {
                    "MO(cr)": _const_nasa7(standard_state="condensed_solid"),
                    "MO(l)": _const_nasa7(standard_state="condensed_liquid"),
                },
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="no gas product matching"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_pure_psat_rejects_gas_gas_pair() -> None:
    """Standard-state guard on pure-psat (KM P2-4)."""
    payload = _strata(
        species_id="M",
        formula="M",
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "pure_component_saturation_pressure",
                "activity_semantics": "pure_condensed_phase",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "gas_thermo_record": _const_nasa7(standard_state="gas"),
                "condensed_thermo_record": _const_nasa7(standard_state="gas"),
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="condensed"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_model_domain_must_be_covered_by_polynomials() -> None:
    """Model domain wider than segment cover fails at compile (CX P2)."""
    payload = _strata(
        species_id="M",
        formula="M",
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "pure_component_saturation_pressure",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "gas_thermo_record": _const_nasa7(
                    standard_state="gas", t_min=500.0, t_max=1000.0
                ),
                "condensed_thermo_record": _const_nasa7(
                    standard_state="condensed_solid", t_min=500.0, t_max=1000.0
                ),
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="must cover the full declared"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_cea_ingest_T_min_T_max_domain_shape_compiles() -> None:
    """T_min_K/T_max_K valid_domain shape wires through legacy projection (KM P2-5)."""
    payload = _strata(
        species_id="M",
        formula="M",
        pressure_models=[
                {
                    "evaluator_family": "nasa7",
                    "pressure_kind": "pure_component_saturation_pressure",
                    "activity_semantics": "pure_condensed_phase",
                    "species_basis": "monomer",
                    "valid_domain": {"T_min_K": 400.0, "T_max_K": 1500.0},
                "gas_thermo_record": _const_nasa7(
                    standard_state="gas", a7=0.0
                ),
                "condensed_thermo_record": _const_nasa7(
                    standard_state="condensed_solid", a7=2.0
                ),
            }
        ],
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    assert catalog.species["M"].evaluator is not None
    assert catalog.species["M"].evaluator.valid_temperature_K == (400.0, 1500.0)
    ev = catalog.evaluator_for("M").evaluate(1000.0)
    assert ev.pressure_pa == pytest.approx(100_000.0 * math.exp(-2.0), rel=1e-9)
    # Legacy projection must not KeyError on temperature_K (KM P2-5).
    legacy = catalog.legacy_view()
    assert isinstance(legacy, dict)
    # Species appears under the compiled catalog regardless of legacy shape.
    assert "M" in catalog.species


def test_t622_reviewed_ivtan_shomate_rows_compile_on_declared_intersections() -> None:
    extract = yaml.safe_load(
        (DATA / "literature" / "extracts" / "ivtan-mno-coo-thermo.yaml").read_text()
    )
    catalog = compile_vapour_rail_catalog(
        yaml.safe_load((DATA / "vapor_pressures.yaml").read_text()),
        emit_u0_request_rules=False,
    )
    expected = {
        "MnO": {
            "species_id": "MnO_gas",
            "observation_id": "ivtan_MnO_gibbs",
            "domain": (1519.0, 2273.15),
            "coefficients": (
                {
                    "A": 45.75783747,
                    "B": -10.89186842,
                    "C": 4.60756286,
                    "D": -0.1828805977,
                    "E": -2.424549708,
                    "F": 136.0904276,
                    "G": 285.3928578,
                    "H": 155.62,
                },
                {
                    "A": 49.23849149,
                    "B": -16.70974471,
                    "C": 7.998335629,
                    "D": -0.8471212092,
                    "E": -2.858724243,
                    "F": 134.1627361,
                    "G": 289.5513484,
                    "H": 155.62,
                },
            ),
            "sigma_G": 7531.2,
            "sigma_dex": [0.262254560, 0.196690920, 0.157352736],
            "fit_residual": 1.99,
            "cross_offset": 0.076815671,
        },
        "CoO": {
            "species_id": "CoO_gas",
            "observation_id": "ivtan_CoO_gibbs",
            "domain": (1400.0, 2000.0),
            "coefficients": (
                {
                    "A": 56.59845462,
                    "B": -30.90699531,
                    "C": 20.53920485,
                    "D": -3.470698915,
                    "E": -2.259342875,
                    "F": 260.6630346,
                    "G": 300.905494,
                    "H": 282.396,
                },
                {
                    "A": -21.72422716,
                    "B": 46.69265164,
                    "C": -8.292468372,
                    "D": 0.3368798948,
                    "E": 29.67750671,
                    "F": 339.7316432,
                    "G": 251.4972846,
                    "H": 282.396,
                },
            ),
            "sigma_G": 12543.093176,
            "sigma_dex": [0.436780776, 0.327585582, 0.262068466],
            "fit_residual": 1.60,
            "cross_offset": 0.127927663,
        },
    }

    for formula, pin in expected.items():
        observation = extract["species"][formula]["observations"][0]
        values = observation["values"]
        assert observation["observation_id"] == pin["observation_id"]
        assert observation["T_range_K"] == [1000.0, 3000.0]
        assert values["evaluator_family"] == "shomate"
        assert tuple(
            segment["coefficients"] for segment in values["segments"]
        ) == pin["coefficients"]
        assert observation["uncertainty"]["sigma_G_J_per_mol"] == pin["sigma_G"]
        assert observation["uncertainty"][
            "sigma_log10_K_at_1500_2000_2500_K"
        ] == pin["sigma_dex"]
        assert values["fit_max_abs_residual_J_per_mol"] == pin["fit_residual"]
        assert values["cross_compilation_offset_dex"] == pin["cross_offset"]

        compiled = catalog.species[pin["species_id"]]
        assert compiled.valid_temperature_K == pin["domain"]
        evaluator = compiled.evaluator
        assert evaluator is not None
        for temperature_K in pin["domain"]:
            evaluated = evaluator.evaluate(
                temperature_K, source_activity=1.0, pO2_bar=1.0
            )
            assert math.isfinite(evaluated.pressure_pa)
            assert evaluated.pressure_pa > 0.0
            assert evaluated.out_of_range is False


def test_shomate_formation_from_coefficient_H_non_cancelling() -> None:
    """Formation from coeff H must enter ΔG (CX P1 — H2O-class latent heat).

    Two phases with identical A…G but different H (= ΔfH° kJ/mol) must not
    cancel the 298.15 K vaporization enthalpy out of K.
    """
    # Minimal Shomate: flat Cp, formation differs by 44 kJ/mol (water-class).
    gas_coeffs = {
        "A": 30.0,
        "B": 0.0,
        "C": 0.0,
        "D": 0.0,
        "E": 0.0,
        "F": -10.0,
        "G": 200.0,
        "H": -241.8,  # kJ/mol ΔfH
    }
    cond_coeffs = dict(gas_coeffs)
    cond_coeffs["H"] = -285.8
    cond_coeffs["F"] = -10.0 - (285.8 - 241.8)  # keep H−H298≈0-ish structure
    # Actually for a clean test: same A–G, only H differs. Relative H−H298
    # changes by −ΔH because of the −H term; absolute H = rel + 1000*H
    # = 1000*(…+F) independent of H. ΔH_abs then comes only from F difference
    # OR from different formation. With same F and different H:
    # rel_g − rel_c = −1000*(H_g − H_c) wait in J: 1000*((F-H_g)-(F-H_c))
    # = 1000*(H_c − H_g). Absolute: rel + 1000*H → abs_g − abs_c =
    # 1000*(H_c−H_g) + 1000*(H_g−H_c) = 0. So same F+A…G with only H different
    # cancels in absolute form!
    # Water-class needs different F as well (NIST tables). Use published-style:
    # abs H ≈ 1000*(At+…+F) when formation folded from H.
    # Set F_gas and F_cond so abs enthalpies differ by ~44 kJ/mol at all t
    # (constant offset): F_g − F_c = 44 when A…E equal.
    gas_coeffs = {
        "A": 30.0,
        "B": 0.0,
        "C": 0.0,
        "D": 0.0,
        "E": 0.0,
        "F": -200.0,
        "G": 200.0,
        "H": -241.8,
    }
    cond_coeffs = {
        "A": 30.0,
        "B": 0.0,
        "C": 0.0,
        "D": 0.0,
        "E": 0.0,
        "F": -244.0,  # 44 kJ/mol more negative absolute H
        "G": 180.0,  # lower liquid entropy
        "H": -285.8,
    }
    gas = ShomatePolynomial(
        name="H2O(g)",
        standard_state="gas",
        segments=(
            ShomateSegment(
                100.0,
                700.0,
                tuple(gas_coeffs[k] for k in "ABCDEFGH"),  # type: ignore[arg-type]
            ),
        ),
    )
    cond = ShomatePolynomial(
        name="H2O(l)",
        standard_state="condensed_liquid",
        segments=(
            ShomateSegment(
                100.0,
                700.0,
                tuple(cond_coeffs[k] for k in "ABCDEFGH"),  # type: ignore[arg-type]
            ),
        ),
    )
    assert gas.formation_enthalpy_J_per_mol() == pytest.approx(-241_800.0)
    assert cond.formation_enthalpy_J_per_mol() == pytest.approx(-285_800.0)
    T = 500.0
    st_g = gas.evaluate(T)
    st_c = cond.evaluate(T)
    # Without formation fold, ΔH would miss the latent contribution carried
    # only in H; with fold, abs H = 1000*(A t+…+F) so ΔH = 1000*(F_g−F_c)=44000.
    t = T / 1000.0
    abs_h_g = 1000.0 * (gas_coeffs["A"] * t + gas_coeffs["F"])
    abs_h_c = 1000.0 * (cond_coeffs["A"] * t + cond_coeffs["F"])
    assert st_g.h_J_per_mol == pytest.approx(abs_h_g, rel=1e-9)
    assert st_c.h_J_per_mol == pytest.approx(abs_h_c, rel=1e-9)
    assert (st_g.h_J_per_mol - st_c.h_J_per_mol) == pytest.approx(44_000.0, rel=1e-9)
    K = reaction_equilibrium_constant(
        [(+1.0, st_g), (-1.0, st_c)],
        T_K=T,
    )
    # Finite positive K that depends on both ΔH and ΔS — not the relative-only path.
    assert math.isfinite(K) and K > 0.0
    delta_g_over_RT = st_g.g_over_RT - st_c.g_over_RT
    assert K == pytest.approx(math.exp(-delta_g_over_RT), rel=0, abs=1e-12)


def test_pO2_exponent_mismatch_with_stoichiometry_fails() -> None:
    thermo = {
        "MO(cr)": _const_nasa7(standard_state="condensed_solid"),
        "M": _const_nasa7(standard_state="gas"),
        "O2": _const_nasa7(standard_state="gas"),
    }
    payload = _strata(
        species_id="M",
        formula="M",
        source_reactions=[
            {
                "id": "mo_subl",
                "reactants": [{"formula": "MO(cr)", "stoichiometry": 2.0}],
                "products": [
                    {"formula": "M", "stoichiometry": 2.0},
                    {"formula": "O2", "stoichiometry": 1.0},
                ],
            }
        ],
        pressure_models=[
            {
                "evaluator_family": "nasa7",
                "pressure_kind": "equilibrium_partial_pressure",
                "species_basis": "monomer",
                "valid_domain": {"temperature_K": [400.0, 1500.0]},
                "source_reaction_id": "mo_subl",
                "pO2_reference_bar": 0.01,
                "pO2_exponent": -1.0,  # wrong; stoich says −0.5
                "species_thermo": thermo,
            }
        ],
    )
    with pytest.raises(CatalogCompileError, match="pO2_exponent"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
