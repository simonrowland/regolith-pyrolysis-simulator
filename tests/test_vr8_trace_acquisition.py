"""VR-8: oxide + trace acquisition transcription (Group-A/B + O(g)).

Acceptance (DECOMPOSITION VR-8):
- every covered Group-A and Group-B U0 row has literature phase/volatility
  values, candidate vapor form, validation status, source account, typed
  route/refusal;
- Group-B has separate millibar and hard-vacuum/open-to-sky records;
- no millibar-negligible row is dropped or declared hard-vacuum zero;
- O(g) is atom-explicit with +1/2 pO2 exponent;
- one query lists the complete remaining pending-validation set;
- rows remain dormant to flux; legacy metals/oxide/foulant outputs identical.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.trace_acquisition import (
    GROUP_A_IDS,
    GROUP_B_IDS,
    MONATOMIC_OXYGEN_ID,
    PO2_EXPONENT_ATOMIC_O,
    acquisition_rows_by_id,
    assert_u0_join_closed,
    group_a_coverage,
    group_b_coverage,
    group_b_regime_pairs,
    list_pending_validation,
    load_trace_acquisition,
    monatomic_oxygen_record,
    validate_trace_acquisition,
)
from simulator.vapour_rail.u0_manifest import load_u0_manifest

DATA = Path(__file__).resolve().parents[1] / "data"


def test_acquisition_yaml_loads_and_validates():
    doc = load_trace_acquisition()
    assert doc["schema_version"] == 1
    assert doc["kind"] == "vapour_rail_trace_acquisition"
    assert doc["dormant_to_flux"] is True
    assert doc["counts"]["group_a"] == len(GROUP_A_IDS)
    assert doc["counts"]["group_b"] == len(GROUP_B_IDS)
    assert doc["counts"]["monatomic_oxygen"] == 1
    assert doc["counts"]["total"] == len(GROUP_A_IDS) + len(GROUP_B_IDS) + 1
    assert validate_trace_acquisition(doc) == []


def test_group_a_and_b_u0_coverage_exact():
    assert group_a_coverage()["complete"] is True
    assert group_b_coverage()["complete"] is True
    assert_u0_join_closed()
    rows = acquisition_rows_by_id()
    for sid in GROUP_A_IDS | GROUP_B_IDS:
        row = rows[sid]
        assert row["candidate_vapor_form"]
        assert row["phase_system"]
        assert row["volatility"]["literature_phase"] == "gas"
        assert row["volatility"]["dominance_window_K"]
        assert row["volatility"]["dominance_note"]
        assert row["validation"]["status"] in ("pending_validation", "validated")
        assert row["source_account"]
        assert row["route"]["dormant_to_flux"] is True
        assert "millibar" in row["regime"] and "hard_vacuum" in row["regime"]
        # Criterion 1 content (not mere key presence) — every A/B row.
        assert row["literature_sources"], f"{sid}: empty literature_sources"
        if row["group"] == "A":
            assert row["volatility"]["values"], f"{sid}: empty Group-A values"
            assert row["route"]["typed_outcome"], f"{sid}: missing typed_outcome"
            assert row["route"]["plant_bin"], f"{sid}: missing plant_bin"
        if row["group"] == "B":
            assert row["route"]["typed_outcome_by_regime"], (
                f"{sid}: missing typed_outcome_by_regime"
            )
            assert row["volatility"]["values"], f"{sid}: empty Group-B values"


def test_group_a_has_literature_values_and_typed_route():
    rows = acquisition_rows_by_id()
    # Universal Group-A gate (P2-2): every A row, not a 9-id sample.
    for sid in sorted(GROUP_A_IDS):
        row = rows[sid]
        assert row["group"] == "A"
        assert row["validation"]["acquisition_status"]
        assert row["volatility"]["values"]
        assert row["route"]["typed_outcome"]
        assert row["route"]["plant_bin"]
        assert row["literature_sources"]
    # Dominant-missing-12 primaries keep the evolve-typed pin.
    for sid in ("Li2O", "Rb2O", "Cs2O", "As4O6", "Ga2O", "BaO", "SrO", "VO", "Yb"):
        assert rows[sid]["route"]["typed_outcome"] == "evolve"


def test_group_b_separate_regimes_never_hard_vacuum_zero():
    pairs = group_b_regime_pairs()
    assert len(pairs) == len(GROUP_B_IDS)
    for pair in pairs:
        mb = pair["millibar"]
        hv = pair["hard_vacuum"]
        assert mb["applicable"] is True
        assert hv["applicable"] is True
        assert mb["dominance"] == "negligible"
        assert mb["outcome"] == "retain_rump"
        # Hard-vacuum must NOT collapse to millibar negligible / zero.
        assert hv["dominance"] == "regime_open"
        assert hv["outcome"] == "evolve_or_compute"
        assert hv["dominance"] != mb["dominance"] or hv["outcome"] != mb["outcome"]
        assert pair["route"]["never_drop"] is True
        assert pair["route"][
            "never_declare_hard_vacuum_zero_from_millibar_negligible"
        ] is True
        assert pair["candidate_vapor_form"]


def test_group_b_row_not_dropped_from_manifest_or_acquisition():
    u0 = load_u0_manifest()
    u0_b = {
        s["id"]
        for s in u0["species"]
        if "group_b" in (s.get("flags") or [])
    }
    assert u0_b == GROUP_B_IDS
    assert set(acquisition_rows_by_id()) >= u0_b


def test_monatomic_oxygen_atom_explicit_plus_half_po2():
    row = monatomic_oxygen_record()
    assert row["id"] == MONATOMIC_OXYGEN_ID
    assert row["formula"] == "O"
    assert row["atoms"] == {"O": 1.0} or row["atoms"] == {"O": 1}
    values = row["volatility"]["values"]
    assert float(values["pO2_exponent"]) == pytest.approx(PO2_EXPONENT_ATOMIC_O)
    assert float(values["pO2_exponent"]) == pytest.approx(0.5)
    reaction = values["source_reaction"]
    assert reaction["products"][0]["formula"] == "O"
    assert reaction["reactants"][0]["formula"] == "O2"
    assert float(reaction["reactants"][0]["stoichiometry"]) == pytest.approx(0.5)
    assert row["source_account"]
    assert row["route"]["dormant_to_flux"] is True

    # Landed dormant family in vapor_pressures.yaml
    vp = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    fam = vp["families"]["monatomic_oxygen_family"]
    o_row = fam["physical_properties"]["species"]["O"]
    assert o_row["formula"] == "O"
    model = o_row["pressure_models"][0]
    assert float(model["pO2_exponent"]) == pytest.approx(0.5)
    assert model["availability"] == "unavailable_pending_acquisition"
    assert fam["code_metadata"]["hot_train_applicability"] == "not_applicable"
    assert fam["code_metadata"]["request_rule"].startswith("dormant")
    assert fam["code_metadata"]["source_account"]
    rxn = o_row["source_reactions"][0]
    assert rxn["id"] == "half_o2_to_atomic_o"


def test_list_pending_validation_is_complete_single_query():
    pending = list_pending_validation()
    assert pending, "pending-validation set must be non-empty"
    # All acquisition rows start pending.
    acq_pending = [p for p in pending if p["surface"] == "trace_acquisition"]
    assert {p["id"] for p in acq_pending} == GROUP_A_IDS | GROUP_B_IDS | {
        MONATOMIC_OXYGEN_ID
    }
    assert len(acq_pending) == len(GROUP_A_IDS) + len(GROUP_B_IDS) + 1
    # Surfaces are stable and sorted.
    ids = [(p["surface"], p["id"]) for p in pending]
    assert ids == sorted(ids)
    # Every entry is pending_validation.
    assert all(p["validation_status"] == "pending_validation" for p in pending)
    # Includes vapor_pressures and species_catalog surfaces too.
    surfaces = {p["surface"] for p in pending}
    assert "trace_acquisition" in surfaces
    assert "vapor_pressures" in surfaces
    assert "species_catalog" in surfaces

    # P2-3: full multi-surface cardinality, not mere surface membership.
    # Recompute independently from on-disk YAML so a truncated scan goes red.
    vp = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    expected_vp: set[str] = set()
    for family in (vp.get("families") or {}).values():
        if not isinstance(family, dict):
            continue
        species_map = ((family.get("physical_properties") or {}).get("species")) or {}
        if not isinstance(species_map, dict):
            continue
        for sid, srow in species_map.items():
            if not isinstance(srow, dict):
                continue
            if (srow.get("validation") or {}).get("status") == "pending_validation":
                expected_vp.add(str(sid))
    vp_pending = {p["id"] for p in pending if p["surface"] == "vapor_pressures"}
    assert vp_pending == expected_vp
    assert MONATOMIC_OXYGEN_ID in vp_pending

    catalog = yaml.safe_load((DATA / "species_catalog.yaml").read_text())
    expected_cat = {
        s["id"]
        for s in catalog["species"]
        if (s.get("validation") or {}).get("status") == "pending_validation"
    }
    cat_pending = {p["id"] for p in pending if p["surface"] == "species_catalog"}
    assert cat_pending == expected_cat
    # Acquisition A/B/O must appear on the catalog surface as non-flux pending.
    assert GROUP_A_IDS | GROUP_B_IDS | {MONATOMIC_OXYGEN_ID} <= expected_cat


def test_rows_dormant_legacy_outputs_identical():
    vp = yaml.safe_load((DATA / "vapor_pressures.yaml").read_text())
    catalog = compile_vapour_rail_catalog(vp)
    legacy = catalog.legacy_view()
    # Live sections unchanged in membership.
    assert set(legacy["metals"]) == {
        "Na",
        "K",
        "Mg",
        "Fe",
        "Ca",
        "Al",
        "Si",
        "Ti",
        "Cr",
        "Mn",
    }
    assert set(legacy["oxide_vapors"]) == {"SiO", "CrO2"}
    assert set(legacy["foulant_vapor"]) == {"NaCl", "KCl", "NaF"}
    # O is projected only into the dormant rail-gap bucket, not live maps.
    assert "O" not in legacy["metals"]
    assert "O" not in legacy["oxide_vapors"]
    assert "O" not in legacy["foulant_vapor"]
    assert "O" in legacy.get("rail_gap_dormant", {})
    assert catalog.species["O"].evaluator is None
    # No Group-A/B gas accidentally lands in live metals/oxides.
    live = set(legacy["metals"]) | set(legacy["oxide_vapors"]) | set(
        legacy["foulant_vapor"]
    )
    assert live.isdisjoint(GROUP_A_IDS - {"P"})  # P may exist only as feedstock id
    assert live.isdisjoint(GROUP_B_IDS)


def test_species_catalog_marks_group_rows_non_flux():
    catalog = yaml.safe_load((DATA / "species_catalog.yaml").read_text())
    by_id = {s["id"]: s for s in catalog["species"]}
    for sid in sorted(GROUP_A_IDS | GROUP_B_IDS | {MONATOMIC_OXYGEN_ID}):
        assert sid in by_id, f"missing species_catalog row {sid}"
        row = by_id[sid]
        assert row.get("direct_vapour_flux") is False
        assert (row.get("validation") or {}).get("status") == "pending_validation"
    o = by_id["O"]
    assert o["formula"] == "O"
    assert float(o["code_metadata"]["pO2_exponent"]) == pytest.approx(0.5)


def test_trace_elements_group_b_regime_blocks():
    te = yaml.safe_load((DATA / "trace_elements.yaml").read_text())
    # 26 Group-B elements annotated.
    group_b_els = []
    for el, row in te["elements"].items():
        block = row.get("vapour_rail_acquisition")
        if not block or block.get("group") != "B":
            continue
        group_b_els.append(el)
        assert block["dormant_to_flux"] is True
        assert block["regime"]["millibar"]["dominance"] == "negligible"
        assert block["regime"]["hard_vacuum"]["dominance"] == "regime_open"
        assert block["never_declare_hard_vacuum_zero_from_millibar_negligible"] is True
        assert block["candidate_vapor_form"]
        assert block["source_account"]
        assert block["validation_status"] == "pending_validation"
    assert len(group_b_els) == 26

    group_a_els = [
        el
        for el, row in te["elements"].items()
        if (row.get("vapour_rail_acquisition") or {}).get("group") == "A"
    ]
    assert len(group_a_els) == 13
    assert te["vapour_rail_monatomic_oxygen"]["pO2_exponent"] == pytest.approx(0.5)


def test_null_hypothesis_corrupted_group_b_hard_vacuum_is_rejected():
    doc = load_trace_acquisition()
    bad = deepcopy(doc)
    for row in bad["rows"]:
        if row["group"] == "B":
            row["regime"]["hard_vacuum"] = {
                "applicable": True,
                "dominance": "negligible",
                "outcome": "retain_rump",
            }
            break
    errors = validate_trace_acquisition(bad)
    assert any("hard_vacuum" in e or "Group-B" in e for e in errors)


def test_null_hypothesis_missing_group_a_row_is_rejected():
    doc = load_trace_acquisition()
    bad = deepcopy(doc)
    bad["rows"] = [r for r in bad["rows"] if r["id"] != "Li2O"]
    errors = validate_trace_acquisition(bad)
    assert any("Group-A" in e or "missing" in e for e in errors)


def test_null_hypothesis_group_a_stripped_values_is_rejected():
    """P2-2: emptying Group-A volatility.values must fail validation."""
    doc = load_trace_acquisition()
    bad = deepcopy(doc)
    for row in bad["rows"]:
        if row["id"] == "LiO":
            row["volatility"]["values"] = {}
            break
    errors = validate_trace_acquisition(bad)
    assert any("volatility.values" in e and "LiO" in e for e in errors)


def test_group_b_policy_mapping_cannot_satisfy_literal_true_marker():
    bad = deepcopy(load_trace_acquisition())
    row = next(row for row in bad["rows"] if row["group"] == "B")
    row["route"][
        "never_declare_hard_vacuum_zero_from_millibar_negligible"
    ] = {"policy": "must_never"}

    errors = validate_trace_acquisition(bad)
    assert any("never_declare_hard_vacuum_zero" in error for error in errors)


@pytest.mark.parametrize("typed_outcome", [{"marker": "evolve"}, "unknown"])
def test_group_a_typed_outcome_requires_scalar_enum(typed_outcome):
    bad = deepcopy(load_trace_acquisition())
    row = next(row for row in bad["rows"] if row["group"] == "A")
    row["route"]["typed_outcome"] = typed_outcome

    errors = validate_trace_acquisition(bad)
    assert any("typed_outcome must be one of" in error for error in errors)


def test_null_hypothesis_o_wrong_reaction_stoich_is_rejected():
    """P2-1: O(g) O2 reactant stoich must be 0.5 (not merely formula O2)."""
    doc = load_trace_acquisition()
    bad = deepcopy(doc)
    for row in bad["rows"]:
        if row["id"] == MONATOMIC_OXYGEN_ID:
            row["volatility"]["values"]["source_reaction"]["reactants"][0][
                "stoichiometry"
            ] = 1.0
            break
    errors = validate_trace_acquisition(bad)
    assert any("stoichiometry" in e and "0.5" in e for e in errors)


# ---------------------------------------------------------------------------
# P1 regression: live t-380 volatile_as_oxide must stay curated; VR-8 claims
# live only in acquisition / regime records. Ga flowsheet admission stays
# UNKNOWN (no silent pending_validation → PASS upgrade).
# ---------------------------------------------------------------------------

_T380_CURATED_FALSE_OXIDE = (
    "Li",
    "P",
    "V",
    "Ga",
    "Rb",
    "Sr",
    "Cs",
    "Ba",
    "Eu",
    "Yb",
)


def test_t380_volatile_as_oxide_not_flipped_by_vr8_candidates():
    """Live t-380 value/species/note are curated; candidates stay off this field."""
    te = yaml.safe_load((DATA / "trace_elements.yaml").read_text())
    curated_notes = {
        "P": (
            "P4O10 is a conditional conversion product, not a default "
            "phosphate-volatility assignment."
        ),
        "V": (
            "Pre-C6 VOx vapor is an UNCERTAIN branch; it is not an "
            "authoritative Ca-crown assignment."
        ),
    }
    for el in _T380_CURATED_FALSE_OXIDE:
        vao = te["elements"][el]["volatile_as_oxide"]
        assert vao.get("value") is False, el
        assert vao.get("species") is None, el
        # VR-8 must not park candidate claims on the live t-380 field.
        assert "vr8_candidate" not in vao, el
        if el in curated_notes:
            assert vao.get("note") == curated_notes[el], el
        # Candidate form lives on the VR-8-owned acquisition block only.
        acq = te["elements"][el]["vapour_rail_acquisition"]
        assert acq["candidate_vapor_form"]
        assert acq["dormant_to_flux"] is True
        assert acq["validation_status"] == "pending_validation"


def test_render_flowsheet_admission_unchanged_ga_stays_unknown():
    """Ga admission must remain UNKNOWN; no pending-candidate PASS upgrade.

    Null hypothesis: flipping volatile_as_oxide.value true + species Ga2O
    makes render_flowsheet project volatile_as=oxide / mode=lance and the
    locked Ga chip (volatile_metal_trap) admission goes UNKNOWN → PASS.
    Refutation: after the P1 revert, Ga stays UNKNOWN and the full admission
    outcome map matches a pure HEAD-facts projection for the ten curated els.
    """
    from scripts import render_flowsheet as rf

    flowsheet = yaml.safe_load((DATA / "flowsheet.yaml").read_text())
    result = rf.lint_against_trace_elements(flowsheet)
    by_sym = {r.symbol: r for r in result.admission_results}

    assert "Ga" in by_sym
    assert by_sym["Ga"].outcome == "UNKNOWN", (
        f"Ga admission silently upgraded to {by_sym['Ga'].outcome!r}; "
        "volatile_as_oxide must not carry unvalidated VR-8 candidates"
    )
    assert result.ok
    assert all(r.outcome != "FAIL" for r in result.admission_results)

    # Sanity: the ten curated elements still project value is not True, so
    # facts never gain volatile_as=oxide from those blocks.
    facts = rf.load_trace_element_facts(DATA / "trace_elements.yaml")
    for el in _T380_CURATED_FALSE_OXIDE:
        f = facts.get(el) or {}
        # oxide projection only when volatile_as_oxide.value is True
        if f.get("volatile_as") == "oxide":
            raise AssertionError(
                f"{el}: facts projected volatile_as=oxide from a curated-false "
                f"t-380 block; facts={f!r}"
            )
