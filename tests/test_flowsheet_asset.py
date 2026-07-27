"""Focused tests for the plant-flowsheet asset pipeline (t-391).

NEW FILES ONLY companion to data/flowsheet.yaml + scripts/render_flowsheet.py.
Does not import the rest of the simulator suite.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import re
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
YAML_PATH = REPO / "data" / "flowsheet.yaml"
TRACE_PATH = REPO / "data" / "trace_elements.yaml"
FEEDSTOCK_PATH = REPO / "data" / "feedstocks.yaml"
SPECIES_CATALOG_PATH = REPO / "data" / "species_catalog.yaml"
RENDERER_PATH = REPO / "scripts" / "render_flowsheet.py"

# v7 REVIEWED SPECIES MAP — every symbol_or_group that must appear exactly once.
# Dual-path species assigned to a single primary home (see flowsheet.yaml notes).
V7_SPECIES_PRIMARY = {
    # CRYO TRAIN (v8: elemental C → CO-CH4-organics pyrolysis gases)
    "H2O",
    "CO2",
    "S",
    "SO2",
    "F",
    "Cl",
    "Br",
    "I",
    "salts",
    "organics",
    "H",
    "He",
    "CO-CH4-organics",
    "N",
    # P primary = ferroalloy (schreibersite); cryo path is condition_note only
    # VOLATILE-METAL TRAP
    "Zn",
    "Cd",
    "Pb",
    "Tl",
    "Bi",
    "In",
    "Ag",
    "Sn",
    "Ge",
    "Se",
    "Sb",
    "As",
    "Te",
    "Cu",
    "Ga",
    "Hg",
    "MoO3",
    "WO3",
    "Re2O7",
    # Mn, Li primary elsewhere (ferroalloy / alkali)
    # ALKALI CYCLONE
    "Na",
    "K",
    "Rb",
    "Cs",
    "Li",
    # FERROALLOY
    "Fe",
    "Ni",
    "Co",
    "Ru",
    "Rh",
    "Pd",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Mo",
    "W",
    "Cr",
    "Mn",
    "P",
    # SiO-GLASS
    "glass",
    # v8 GAS CONDITIONING (non-condensables)
    "CO2-CO",
    "P2-PO",
    # O2 PUMP → FROST CISTERN (layout station; O2 product)
    "O2",
    # Mg CROWN / C6
    "Mg",
    "Al",
    "Ti",
    "V",
    "Nb",
    "Ta",
    # Ca CROWN / CALCIOTHERMIC
    "Ca",
    "Sr",
    "Ba",
    "Eu",
    "Yb",
    "REE",
    "Y",
    "Th",
    "U",
    # RUMP
    "BeO",  # v8: rump chips are oxides
    "Fe⁰",  # v9: furnace melt taps (owner 2026-07-20)
    "Si⁰",  # v9: furnace melt taps
    "ZrO2",
    "HfO2",
    "Sc2O3",
    "unreduced-residuals",
}

V7_BINS = {
    "volatile_metal_trap",
    "cryo_train",
    "alkali_cyclone",
    "ferroalloy_tap",
    "sio_glass",
    "gas_conditioning",
    "lox_cistern",
    "mg_crown",
    "c6_magnesiothermic",
    "ca_crown",
    "calciothermic",
    "rump_product",
}

V7_TOP_BLOCKS = {
    "early_bake_cleanup",
    "bernoulli_overhead",
    "mg_dome",
    "ca_dome",
    "rump",
}


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_flowsheet", RENDERER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_flowsheet"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def renderer():
    return _load_renderer()


@pytest.fixture(scope="module")
def flowsheet():
    with YAML_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def trace_doc():
    with TRACE_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_yaml_loads_and_schema_valid(renderer, flowsheet):
    assert flowsheet["schema_version"] == 1
    errs = renderer.validate_schema(flowsheet)
    assert errs == [], errs


def test_v7_top_blocks_and_bins_present(flowsheet):
    block_ids = {b["id"] for b in flowsheet["blocks"]}
    assert V7_TOP_BLOCKS <= block_ids
    sub_ids = {s["id"] for b in flowsheet["blocks"] for s in b.get("sub_boxes") or []}
    assert V7_BINS <= sub_ids


def test_every_v7_species_present_exactly_once(renderer, flowsheet):
    chips = renderer.species_index(flowsheet)
    assert set(chips) == V7_SPECIES_PRIMARY
    # exactly once enforced by species_index keys + schema validator
    assert len(chips) == len(V7_SPECIES_PRIMARY)


def test_conditional_chips_have_notes(flowsheet):
    for block in flowsheet["blocks"]:
        for sub in block.get("sub_boxes") or []:
            for sp in sub.get("species") or []:
                if sp["status"] == "conditional":
                    assert sp.get("condition_note"), sp["symbol_or_group"]


def test_edges_cover_classes(flowsheet):
    classes = {e["class"] for e in flowsheet["edges"]}
    assert classes == {"main", "oxygen", "reagent_return"}
    # spine through four siblings + rump
    spine_labels = [
        (e["from"], e["to"])
        for e in flowsheet["edges"]
        if e["class"] == "main" and e["from"] in V7_TOP_BLOCKS and e["to"] in V7_TOP_BLOCKS
    ]
    assert ("early_bake_cleanup", "bernoulli_overhead") in spine_labels
    assert ("bernoulli_overhead", "mg_dome") in spine_labels
    assert ("mg_dome", "ca_dome") in spine_labels
    assert ("ca_dome", "rump") in spine_labels


def test_renderer_byte_deterministic(renderer, flowsheet):
    a = renderer.render_svg(flowsheet, demo_fill=0.0)
    b = renderer.render_svg(flowsheet, demo_fill=0.0)
    assert a == b
    assert a.startswith("<?xml")
    assert "<svg" in a
    h1 = hashlib.sha256(a.encode()).hexdigest()
    h2 = hashlib.sha256(b.encode()).hexdigest()
    assert h1 == h2


def test_conditional_chips_render_dashed(renderer, flowsheet):
    svg = renderer.render_svg(flowsheet)
    # At least one chip with conditional class
    assert 'class="chip-face conditional"' in svg
    # Every conditional species group has the dashed class nearby
    for _bid, sid, sp in renderer.iter_species_chips(flowsheet):
        if sp["status"] != "conditional":
            continue
        # Find the species-chip group for this symbol
        pat = re.compile(
            rf'data-species="{re.escape(sp["symbol_or_group"])}"[^>]*>.*?</g>',
            re.DOTALL,
        )
        m = pat.search(svg)
        assert m, f"missing chip group for {sp['symbol_or_group']}"
        chunk = m.group(0)
        assert "conditional" in chunk, sp["symbol_or_group"]
        assert "stroke-dasharray" in svg  # CSS rule present


def test_ui_hooks_present(renderer, flowsheet):
    svg = renderer.render_svg(flowsheet, demo_fill=0.0)
    assert 'class="species-chip"' in svg
    assert 'data-species="' in svg
    assert 'data-bin="' in svg
    assert 'class="fill-level"' in svg
    assert "--fill-fraction:" in svg
    assert "<clipPath" in svg or "<clipPath" in svg.lower() or "clipPath" in svg
    assert "<title>" in svg


def test_demo_fill_changes_only_fill_level(renderer, flowsheet):
    zero = renderer.render_svg(flowsheet, demo_fill=0.0)
    demo = renderer.render_svg(flowsheet, demo_fill=0.4)
    assert zero != demo

    def strip_fill(s: str) -> str:
        s = re.sub(r'<rect class="fill-level"[^>]*/>', "<FILL/>", s)
        s = re.sub(r"--fill-fraction: [0-9.]+", "--fill-fraction: X", s)
        return s

    assert strip_fill(zero) == strip_fill(demo)
    # filled rects have positive height under demo
    assert re.search(r'class="fill-level"[^>]*height="[1-9]', demo)


def test_lint_passes_gracefully_without_trace_elements(
    renderer, flowsheet, tmp_path
):
    result = renderer.lint_against_trace_elements(
        flowsheet, trace_path=tmp_path / "intentionally-absent.yaml"
    )
    assert result.ok
    assert result.skipped
    report = result.report_text()
    assert any(
        "skip" in m.lower() or "SKIPPED" in report
        for m in result.messages + [report]
    )
    assert result.admission_results
    assert any(r.outcome == "PASS" for r in result.admission_results)
    assert any(r.outcome == "UNKNOWN" for r in result.admission_results)
    assert all(r.outcome != "FAIL" for r in result.admission_results)


def test_write_svg_roundtrip(renderer, flowsheet, tmp_path):
    out = tmp_path / "flowsheet.svg"
    svg = renderer.write_svg(flowsheet, out, demo_fill=0.0)
    assert out.is_file()
    assert out.read_text(encoding="utf-8") == svg


def test_geometry_self_check_passes(renderer, flowsheet):
    """Procedural layout self-check: no edge∩content, no text overflow, canvas bounds."""
    layout = renderer.compute_layout(flowsheet, demo_fill=0.0)
    # Populate PlacedText via render pass (geometry check uses layout.texts)
    renderer._render_from_layout(flowsheet, layout)
    errs = renderer.geometry_self_check(layout)
    assert errs == [], errs


def test_text_vs_text_overlap_self_check(renderer, flowsheet):
    """Body text bounding boxes must not pairwise-overlap (title∩subtitle was a round-2 miss)."""
    layout = renderer.compute_layout(flowsheet, demo_fill=0.0)
    renderer._render_from_layout(flowsheet, layout)
    body = [t for t in layout.texts if t.role == "body"]
    assert len(body) >= 10
    overlaps = []
    for i, ta in enumerate(body):
        ea = ta.extent()
        for tb in body[i + 1 :]:
            if renderer._aabb_overlap(ea, tb.extent(), pad=0.5):
                overlaps.append((ta.text[:40], tb.text[:40], ta.owner_id, tb.owner_id))
    assert overlaps == [], overlaps
    # Also required via the aggregate geometry gate
    errs = [e for e in renderer.geometry_self_check(layout) if "text-overlap" in e]
    assert errs == [], errs


def test_edge_label_no_content_collision(renderer, flowsheet):
    """Edge labels must not sit on chips, sub-boxes, block borders, or body text (FIX-ROUND-3)."""
    layout = renderer.compute_layout(flowsheet, demo_fill=0.0)
    renderer._render_from_layout(flowsheet, layout)

    labeled = [e for e in layout.edges if e.label]
    assert len(labeled) >= 8

    chip_rects = [
        (r.x, r.y, r.x2, r.y2)
        for r in layout.obstacles.values()
        if r.kind == "chip"
    ]
    sub_rects = [
        (r.id, (r.x, r.y, r.x2, r.y2))
        for r in layout.obstacles.values()
        if r.kind == "sub"
    ]
    body_exts = [t.extent() for t in layout.texts if t.role == "body"]
    hits = []
    for edge in labeled:
        bb = renderer.edge_label_bbox(
            edge.label, edge.label_x, edge.label_y, edge.label_anchor
        )
        for cr in chip_rects:
            if renderer._aabb_overlap(bb, cr, pad=0.5):
                hits.append((edge.edge_id, edge.label, "chip", cr))
        for sid, sr in sub_rects:
            if renderer._aabb_overlap(bb, sr, pad=0.5):
                hits.append((edge.edge_id, edge.label, f"sub:{sid}", sr))
        for be in body_exts:
            if renderer._aabb_overlap(bb, be, pad=0.5):
                hits.append((edge.edge_id, edge.label, "body-text", be))
                break
    assert hits == [], hits

    # Aggregate gate must also surface this class
    errs = [e for e in renderer.geometry_self_check(layout) if "edge-label-overlap" in e]
    assert errs == [], errs


def test_sub_title_and_ops_are_stacked(renderer, flowsheet):
    """Sub-box title baseline must sit above ops/subtitle (not co-baselined)."""
    layout = renderer.compute_layout(flowsheet, demo_fill=0.0)
    renderer._render_from_layout(flowsheet, layout)
    by_owner: dict[str, list] = {}
    for t in layout.texts:
        if t.role != "body" or not t.owner_id:
            continue
        by_owner.setdefault(t.owner_id, []).append(t)
    sub_ids = {s.id for b in layout.blocks for s in b.subs}
    for sid in sub_ids:
        texts = by_owner.get(sid) or []
        if len(texts) < 2:
            continue
        # Title is first rendered (largest font among first two); ops follow below
        title = texts[0]
        rest = texts[1:]
        for other in rest:
            assert other.y > title.y + 1.0, (
                f"{sid}: ops/content y={other.y:.1f} must be below title y={title.y:.1f}"
            )


def test_v8_content_delta(flowsheet):
    """v8: cryo carbon gases, gas conditioning, frost cistern, buffer recirculation."""
    chips = {
        sp["symbol_or_group"]
        for b in flowsheet["blocks"]
        for s in b.get("sub_boxes") or []
        for sp in s.get("species") or []
    }
    assert "C" not in chips
    assert "CO-CH4-organics" in chips
    assert "CO2-CO" in chips
    assert "P2-PO" in chips
    sub_by_id = {
        s["id"]: s for b in flowsheet["blocks"] for s in b.get("sub_boxes") or []
    }
    assert "gas_conditioning" in sub_by_id
    assert "FROST" in sub_by_id["lox_cistern"]["title"].upper() or "PUMP" in sub_by_id[
        "lox_cistern"
    ]["title"].upper()
    edges = {(e["from"], e["to"], e["class"]) for e in flowsheet["edges"]}
    assert ("lox_cistern", "bernoulli_overhead", "reagent_return") in edges


def test_annotation_strings_render_in_full(renderer, flowsheet):
    """Every YAML annotation / ops string must appear fully in the SVG (no mid-sentence clip)."""
    svg = renderer.render_svg(flowsheet)
    missing = renderer._annotation_audit(flowsheet, svg)
    assert missing == [], missing


def test_columns_top_align_content_height(renderer, flowsheet):
    """Columns size to content (not forced equal height) and share a common top."""
    layout = renderer.compute_layout(flowsheet)
    tops = {b.y for b in layout.blocks}
    assert len(tops) == 1
    heights = [b.h for b in layout.blocks]
    # At least one shorter column (Mg/Ca dome) vs tall Bernoulli/early-bake
    assert max(heights) - min(heights) > 50.0


def test_oxygen_and_return_edges_use_gutters(renderer, flowsheet):
    """Vertical oxygen/return segments must sit in gutters, not column centers."""
    layout = renderer.compute_layout(flowsheet)
    block_spans = [(b.x + 4.0, b.x + b.w - 4.0) for b in layout.blocks]

    def in_any_column_interior(x: float) -> bool:
        return any(lo < x < hi for lo, hi in block_spans)

    for edge in layout.edges:
        if edge.cls not in ("oxygen", "reagent_return"):
            continue
        pts = edge.points
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            # Long vertical segment: Δy large, Δx ~ 0
            if abs(x1 - x2) < 0.5 and abs(y2 - y1) > 40:
                assert not in_any_column_interior(x1), (
                    f"{edge.edge_id}: vertical at x={x1:.1f} runs through column interior"
                )


def test_one_marker_end_per_edge_path(renderer, flowsheet):
    """Each drawn edge is a single path with CSS marker-end (no mid-span arrow paths)."""
    svg = renderer.render_svg(flowsheet)
    # Edge paths: one <path class="edge-..."> per drawn edge
    edge_paths = re.findall(r'<path class="edge-(main|oxygen|reagent_return)"', svg)
    assert len(edge_paths) >= 10
    # Legend uses marker-free sample classes
    assert "edge-sample-oxygen" in svg
    assert re.search(
        r'class="edge-oxygen"[^>]*marker-end', svg
    ) is None  # marker is in CSS, not attr
    # Sky oxygen edges must not end with a long horizontal on the sky transit y
    layout = renderer.compute_layout(flowsheet)
    for edge in layout.edges:
        if edge.cls == "oxygen" and edge.to == "sky":
            pts = edge.points
            assert len(pts) >= 2
            # Final segment should be vertical (upward vent stub)
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            assert abs(x1 - x2) < 0.5, f"{edge.edge_id}: sky end not vertical"
            assert y2 < y1, f"{edge.edge_id}: sky end should point upward"


# ---------------------------------------------------------------------------
# Phase 1b — membership lock + bin admission predicates
# ---------------------------------------------------------------------------
#
# TWO-TOUCH BUMP RITUAL (membership changes only):
#   1. Bump data/flowsheet.yaml map_version (vN → vN+1)
#   2. Re-pin MEMBERSHIP_LOCK_HASH below to membership_lock_hash(flowsheet)
# Layout / annotation / edge edits must NOT change this hash (see stability test).
# ---------------------------------------------------------------------------

# Canonical sha256 of sorted (bin_id, chip, status, condition_note, members).
# Computed from data/flowsheet.yaml membership set only — see membership_lock_hash().
MEMBERSHIP_LOCK_HASH = (
    "41a411d42f1669c9af2e238c18df155787b70f68d4e5b5528978e620a98b9673"
)


def test_membership_lock_header(flowsheet):
    assert flowsheet.get("map_version") == "v9"
    assert flowsheet.get("locked") is True
    assert flowsheet.get("locked_at")
    assert flowsheet["schema_version"] == 1


def test_membership_lock_hash_pinned(renderer, flowsheet):
    """Pin the membership-set hash; re-pin only with map_version bump."""
    digest = renderer.membership_lock_hash(flowsheet)
    assert digest == MEMBERSHIP_LOCK_HASH, (
        f"membership hash drifted to {digest!r}. If the chip set intentionally "
        f"changed, bump map_version AND re-pin MEMBERSHIP_LOCK_HASH (two-touch)."
    )


def test_membership_hash_stable_under_annotation_edit(renderer, flowsheet):
    """Layout/annotation/edge edits MUST NOT change the membership lock hash."""
    import copy

    base = renderer.membership_lock_hash(flowsheet)
    mutated = copy.deepcopy(flowsheet)
    # Edit notes, annotations, edge labels, ops — none are membership
    mutated["notes"] = list(mutated.get("notes") or []) + ["hash-stability probe note"]
    mutated["blocks"][0]["annotations"] = list(
        mutated["blocks"][0].get("annotations") or []
    ) + ["annotation edit must not move the lock hash"]
    mutated["blocks"][0]["operating_conditions"] = "edited ops string"
    if mutated.get("edges"):
        mutated["edges"][0]["label"] = "edited edge label"
    # review provenance is also non-membership
    for block in mutated["blocks"]:
        for sub in block.get("sub_boxes") or []:
            for sp in sub.get("species") or []:
                sp["review"] = {"map": "v8", "finding": "stability-probe"}
            sub["admission"] = {
                "any_of": [{"element": "__probe__"}],
            }
    assert renderer.membership_lock_hash(mutated) == base


def test_membership_hash_moves_when_chip_status_changes(renderer, flowsheet):
    """Sanity: a real membership edit (status) MUST change the hash."""
    import copy

    base = renderer.membership_lock_hash(flowsheet)
    mutated = copy.deepcopy(flowsheet)
    # Flip first reviewed chip to conditional with a note
    for block in mutated["blocks"]:
        for sub in block.get("sub_boxes") or []:
            for sp in sub.get("species") or []:
                if sp["status"] == "reviewed":
                    sp["status"] = "conditional"
                    sp["condition_note"] = "deliberate membership edit for hash test"
                    assert renderer.membership_lock_hash(mutated) != base
                    return
    raise AssertionError("no reviewed chip found to flip")


def test_review_provenance_fields_parse(flowsheet):
    """Optional review: {map, finding?} parses; v7 bins carry findings; v8 chips map=v8."""
    v8_chips = {"CO-CH4-organics", "CO2-CO", "P2-PO"}
    seen_v7 = 0
    seen_v8 = 0
    for block in flowsheet["blocks"]:
        for sub in block.get("sub_boxes") or []:
            for sp in sub.get("species") or []:
                rev = sp.get("review")
                assert rev is not None, sp["symbol_or_group"]
                assert "map" in rev
                if sp["symbol_or_group"] in v8_chips:
                    assert rev["map"] == "v8"
                    seen_v8 += 1
                else:
                    # Most chips inherit the v7 reviewed-map finding for their bin
                    assert rev["map"] in ("v7", "v8", "v9")
                    if rev["map"] == "v7":
                        assert rev.get("finding"), sp["symbol_or_group"]
                        seen_v7 += 1
    assert seen_v7 >= 50
    assert seen_v8 == 3


def test_every_bin_has_admission_block(flowsheet):
    for block in flowsheet["blocks"]:
        for sub in block.get("sub_boxes") or []:
            adm = sub.get("admission")
            assert isinstance(adm, dict), sub["id"]
            assert adm.get("any_of") or adm.get("all_of"), sub["id"]


def test_predicate_evaluator_pass_fail_unknown(renderer):
    """Unit tests for evaluate_admission with synthetic facts."""
    # PASS: any_of matches
    adm = {
        "any_of": [
            {"volatile_as": "metal", "window": "trap_band"},
            {"volatile_as": "oxide", "mode": "lance"},
        ]
    }
    facts_pass = {"volatile_as": "metal", "window": "trap_band"}
    assert renderer.evaluate_admission(adm, facts_pass) == "PASS"

    # FAIL: all clauses fully present and mismatch
    facts_fail = {"volatile_as": "gas", "window": "cryo", "mode": "reduce"}
    # clauses need both fields — gas/cryo fails first clause; missing mode for second
    # → UNKNOWN for second (mode missing). Make both fully specified mismatches:
    facts_fail = {
        "volatile_as": "gas",
        "window": "cryo",
        "mode": "reduce",
    }
    # first clause: volatile_as gas≠metal → FAIL; window cryo≠trap_band → FAIL
    # second: volatile_as gas≠oxide → FAIL; mode reduce≠lance → FAIL
    assert renderer.evaluate_admission(adm, facts_fail) == "FAIL"

    # UNKNOWN: facts absent
    assert renderer.evaluate_admission(adm, None) == "UNKNOWN"
    assert renderer.evaluate_admission(adm, {}) == "UNKNOWN"
    # UNKNOWN: field missing
    assert (
        renderer.evaluate_admission(adm, {"volatile_as": "metal"}) == "UNKNOWN"
    )  # window missing

    # all_of PASS / FAIL / UNKNOWN
    adm_all = {"all_of": [{"reducibility": "not_reducible"}]}
    assert (
        renderer.evaluate_admission(adm_all, {"reducibility": "not_reducible"})
        == "PASS"
    )
    assert (
        renderer.evaluate_admission(adm_all, {"reducibility": "mg_reducible"})
        == "FAIL"
    )
    assert renderer.evaluate_admission(adm_all, {"family": "alkali"}) == "UNKNOWN"


def test_live_major_species_admission_green(renderer, flowsheet):
    """Majors wired from Ellingham + vapor_pressures + process anchors must PASS."""
    result = renderer.lint_against_trace_elements(flowsheet)
    by_sym = {r.symbol: r for r in result.admission_results}
    # rump irreducibles
    for sym in ("BeO", "ZrO2", "HfO2", "Sc2O3"):
        assert by_sym[sym].outcome == "PASS", (sym, by_sym[sym])
    # ferroalloy
    for sym in ("Fe", "Ni"):
        assert by_sym[sym].outcome == "PASS", (sym, by_sym[sym])
    # alkali cyclone volatility
    for sym in ("Na", "K"):
        assert by_sym[sym].outcome == "PASS", (sym, by_sym[sym])
    # C6 / calciothermic / O2 / Mg
    for sym in ("Al", "Ti", "Mg", "O2", "REE", "Y"):
        assert by_sym[sym].outcome == "PASS", (sym, by_sym[sym])
    assert result.ok
    assert all(r.outcome != "FAIL" for r in result.admission_results)


def test_deliberate_admission_contradiction_fails(renderer, flowsheet):
    """Fixture: place Fe (siderophile) under rump not_reducible → FAIL fires."""
    import copy

    mutated = copy.deepcopy(flowsheet)
    # Build a tiny synthetic sheet: Fe chip in rump_product
    # Keep rump admission as not_reducible; force Fe facts via custom fact table
    fact_table = {
        "Fe": {
            "element": "Fe",
            "goldschmidt_class": "siderophile",
            "reductant_class": "already-native",
            "host_phase": "metal",
            "reducibility": "mg_reducible",  # contradicts not_reducible
        }
    }
    # Strip all chips except a single Fe planted in rump
    for block in mutated["blocks"]:
        for sub in block.get("sub_boxes") or []:
            if sub["id"] == "rump_product":
                sub["species"] = [
                    {
                        "symbol_or_group": "Fe",
                        "status": "reviewed",
                        "review": {"map": "v8"},
                    }
                ]
                sub["admission"] = {"all_of": [{"reducibility": "not_reducible"}]}
            else:
                sub["species"] = []
    results = renderer.evaluate_all_admissions(mutated, fact_table)
    assert len(results) == 1
    assert results[0].symbol == "Fe"
    assert results[0].outcome == "FAIL"
    lint = renderer.lint_against_trace_elements(mutated, fact_table=fact_table)
    assert not lint.ok
    assert any("admission FAIL" in e for e in lint.errors)


MUST_ADD_TRACE_ELEMENTS = {
    "Zr",
    "Ru",
    "Rh",
    "Pd",
    "Re",
    "Os",
    "Pt",
    "Te",
    "I",
    "Br",
    "Hg",
    "H",
    "He",
    "C",
    "N",
}
MUST_ADD_MOLECULAR_FORMS = {
    "ClO4-",
    "ClO3-",
    "NO3-",
    "SO4-",
    "Cl-",
    "CO3-",
    "organic_C",
    "structural_H2O",
    "oldhamite_CaS",
    "niningerite_MgS",
    "daubreelite_FeCr2S4",
    "alabandite_MnS",
    "troilite_FeS",
    "kamacite_taenite",
    "surface_correlated_volatile_coatings",
}


def test_trace_production_contract_counts_and_authority(trace_doc):
    assert trace_doc["schema_version"] == 1
    assert trace_doc["authority"] == "estimated"
    assert trace_doc["basis"] == "passenger_sidecar"
    assert trace_doc["units"] == {
        "abundance": "ppm_mass",
        "condensation_temperature": "K",
        "boiling_point": "C",
    }
    assert len(trace_doc["elements"]) == 70
    assert len(trace_doc["plant_bins"]) == 11
    assert MUST_ADD_TRACE_ELEMENTS <= set(trace_doc["elements"])
    assert MUST_ADD_MOLECULAR_FORMS <= set(
        trace_doc["molecular_forms_by_feedstock_class"]["required_forms"]
    )
    assert all(
        rec["phosphate_affinity"] in {"host_phase_supported", "unknown"}
        for rec in trace_doc["elements"].values()
    )


def test_trace_routes_sources_and_abundance_triples_resolve(trace_doc):
    plant_bins = trace_doc["plant_bins"]
    references = trace_doc["references"]
    route_count = 0
    unresolved_count = 0
    for symbol, element in trace_doc["elements"].items():
        assert element["routes"], symbol
        for route in element["routes"]:
            route_count += 1
            assert route["plant_bin"] in plant_bins, (symbol, route)
            assert route["status"] in {"conditional", "estimated"}
            if route["status"] == "conditional":
                assert route["fraction"] is None
                assert isinstance(route["condition"], str) and route["condition"]
            else:
                assert route["condition"] is None
                assert math.isfinite(route["fraction"])
                assert 0.0 <= route["fraction"] <= 1.0

        for source_id in element["source_ids"]:
            assert source_id in references, (symbol, source_id)
        for class_name, abundance in element[
            "abundance_by_feedstock_class"
        ].items():
            for source_id in abundance["source_ids"]:
                assert source_id in references, (symbol, class_name, source_id)
            triple = [
                abundance["min_ppm_mass"],
                abundance["typical_ppm_mass"],
                abundance["max_ppm_mass"],
            ]
            if abundance["distribution"] == "unresolved":
                unresolved_count += 1
                assert triple == [None, None, None]
            else:
                assert abundance["distribution"] == "triangular"
                assert all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0.0
                    for value in triple
                )
                assert triple == sorted(triple)

    assert route_count == 150
    assert unresolved_count > 0
    assert (
        trace_doc["elements"]["Hg"]["abundance_by_feedstock_class"]["mars"][
            "distribution"
        ]
        == "unresolved"
    )


def test_comet_trace_basis_and_volatile_ice_pointer(trace_doc):
    comet = trace_doc["molecular_forms_by_feedstock_class"][
        "comet_dry_refractory_dust"
    ]
    assert comet["basis"] == "dry_refractory_dust"
    pointer = comet["volatile_ice_species_pointer_only"]
    assert pointer["ownership"] == "sibling_worker"
    assert {"H2O", "CO", "CO2", "NH3", "HCl", "Ar", "Kr", "Xe"} <= set(
        pointer["species"]
    )
    assert (
        trace_doc["elements"]["H"]["abundance_by_feedstock_class"][
            "comet_dry_refractory_dust"
        ]["distribution"]
        == "unresolved"
    )


def test_special_trace_flowsheet_memberships(trace_doc):
    memberships = {
        symbol: trace_doc["elements"][symbol]["flowsheet_membership"]
        for symbol in ("Be", "Sc", "Zr", "Hf", "C", "B")
    }
    assert memberships["Be"] == {
        "chip": "BeO",
        "plant_bin": "rump",
        "status": "placed",
        "reason": None,
    }
    assert memberships["Sc"]["chip"] == "Sc2O3"
    assert memberships["Zr"]["chip"] == "ZrO2"
    assert memberships["Hf"]["chip"] == "HfO2"
    assert memberships["C"] == {
        "chip": "CO-CH4-organics",
        "plant_bin": "cryo-train",
        "status": "placed",
        "reason": None,
    }
    assert memberships["B"]["status"] == "unplaced"
    assert memberships["B"]["chip"] is None
    assert memberships["B"]["plant_bin"] is None
    assert memberships["B"]["reason"]


def test_production_trace_lint_checks_nonzero_mapping_rows(
    renderer, flowsheet
):
    result = renderer.lint_against_trace_elements(flowsheet, TRACE_PATH)
    assert result.ok, result.report_text()
    assert not result.skipped
    assert any(
        "routed species checked: 70; route alternatives checked: 150; "
        "static memberships checked: 69" in message
        for message in result.messages
    )


def test_trace_fact_export_keeps_estimated_authority_and_evidence_visible(
    renderer,
):
    facts = renderer.load_trace_element_facts(TRACE_PATH)
    assert facts
    assert all(fact["trace_authority"] == "estimated" for fact in facts.values())
    assert (
        facts["B"]["evidence_status_by_feedstock_class"]["lunar"]
        == "UNSOURCED_NUMERIC_RANGE"
    )


def test_trace_extract_order_is_canonical(renderer, trace_doc):
    reordered = deepcopy(trace_doc)
    reordered["elements"] = dict(reversed(list(reordered["elements"].items())))
    extracted_symbols = [
        record["symbol"] for record in renderer._extract_routed_species(reordered)
    ]
    assert extracted_symbols == sorted(trace_doc["elements"])


@pytest.mark.parametrize(
    ("case", "error_fragment"),
    [
        ("wrong_route_target", "route targets unknown plant bin"),
        ("wrong_membership", "not declared plant bin"),
        ("unknown_route_status", "route has unknown status"),
        ("unknown_condition", "route condition must be"),
        ("unexplained_unplaced", "requires an uncertainty reason"),
        ("unknown_evidence_status", "unknown evidence_status"),
        ("unsourced_numeric", "may not carry a numeric ppm range"),
        ("inverted_abundance", "min <= typical <= max"),
        ("empty_elements", "routing cross-check empty"),
    ],
)
def test_trace_lint_rejects_malformed_contract(
    renderer, flowsheet, trace_doc, tmp_path, case, error_fragment
):
    malformed = deepcopy(trace_doc)
    if case == "wrong_route_target":
        malformed["elements"]["Li"]["routes"][0]["plant_bin"] = "not-a-bin"
    elif case == "wrong_membership":
        malformed["elements"]["Li"]["flowsheet_membership"]["plant_bin"] = "rump"
    elif case == "unknown_route_status":
        malformed["elements"]["Li"]["routes"][0]["status"] = "invented"
    elif case == "unknown_condition":
        malformed["elements"]["Li"]["routes"][0]["condition"] = {"mode": "invalid"}
    elif case == "unexplained_unplaced":
        malformed["elements"]["B"]["flowsheet_membership"]["reason"] = ""
    elif case == "unknown_evidence_status":
        malformed["elements"]["Li"]["abundance_by_feedstock_class"]["lunar"][
            "evidence_status"
        ] = "INVENTED"
    elif case == "unsourced_numeric":
        malformed["elements"]["Li"]["abundance_by_feedstock_class"]["lunar"][
            "evidence_status"
        ] = "UNSOURCED_NUMERIC_RANGE"
    elif case == "inverted_abundance":
        malformed["elements"]["Li"]["abundance_by_feedstock_class"]["lunar"][
            "min_ppm_mass"
        ] = 100.0
    elif case == "empty_elements":
        malformed["elements"] = {}
    else:  # pragma: no cover
        raise AssertionError(case)

    path = tmp_path / f"{case}.yaml"
    path.write_text(
        yaml.safe_dump(malformed, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    result = renderer.lint_against_trace_elements(flowsheet, path)
    assert not result.ok
    assert any(error_fragment in error for error in result.errors), result.report_text()


def test_trace_phase_one_preserves_locked_flowsheet_and_default_svg(
    renderer, flowsheet
):
    assert hashlib.sha256(YAML_PATH.read_bytes()).hexdigest() == (
        "3793547c62791d3dc6b8026c7d9b37d493eaee39dbdef91776012da813e7cec8"
    )
    svg = renderer.render_svg(flowsheet, demo_fill=0.0)
    assert hashlib.sha256(svg.encode()).hexdigest() == (
        "37ecb068a25951827c0874dba59ee133e1222aab019d2b1a05175604b3d8ae13"
    )


def test_trace_major_overlap_rule_is_behaviorally_formula_resolved_per_feedstock(
    renderer,
):
    owns_chromium = {
        "composition_wt_pct": {"Cr2O3": 100.0},
        "trace_ppm": {"elements": {"Cr": 1.0}},
    }
    does_not_own_chromium = {
        "composition_wt_pct": {"SiO2": 100.0},
        "trace_ppm": {"elements": {"Cr": 1.0}},
    }
    errors = renderer.validate_feedstock_trace_major_exclusion(
        "synthetic_chromite", owns_chromium, SPECIES_CATALOG_PATH
    )
    assert len(errors) == 1
    assert "synthetic_chromite" in errors[0]
    assert "'Cr'" in errors[0]
    assert "Cr2O3" in errors[0]
    assert (
        renderer.validate_feedstock_trace_major_exclusion(
            "synthetic_silica", does_not_own_chromium, SPECIES_CATALOG_PATH
        )
        == []
    )


@pytest.mark.parametrize(
    ("passenger_declaration", "error_fragment"),
    [
        ({"trace_ppm": {"elements": {"cr": 1.0}}}, "already owned"),
        ({"trace_ppm": {"elements": {"CR": 1.0}}}, "already owned"),
        ({"trace_ppm": {"Cr": 1.0}}, "only allows the 'elements' key"),
        (
            {"trace_elements": {"Cr_ppm": 1.0}},
            "only legal passenger declaration is trace_ppm.elements",
        ),
    ],
)
def test_trace_major_exclusion_rejects_case_and_schema_bypasses(
    renderer, passenger_declaration, error_fragment
):
    feedstock = {
        "composition_wt_pct": {"Cr2O3": 100.0},
        **deepcopy(passenger_declaration),
    }
    errors = renderer.validate_feedstock_trace_major_exclusion(
        "synthetic_chromite", feedstock, SPECIES_CATALOG_PATH
    )
    assert any(error_fragment in error for error in errors), errors


def test_trace_major_exclusion_explicitly_rejects_legacy_production_alias(
    renderer,
):
    feedstocks = yaml.safe_load(FEEDSTOCK_PATH.read_text(encoding="utf-8"))
    targeted = feedstocks["targeted_super_kreep_ore"]
    assert targeted["composition_wt_pct"]["ZrO2"] > 0.0
    assert "Zr_ppm" in targeted["trace_elements"]
    errors = renderer.validate_feedstock_trace_major_exclusion(
        "targeted_super_kreep_ore", targeted, SPECIES_CATALOG_PATH
    )
    assert any(
        "only legal passenger declaration is trace_ppm.elements" in error
        for error in errors
    ), errors
