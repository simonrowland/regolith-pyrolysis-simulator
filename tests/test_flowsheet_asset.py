"""Focused tests for the plant-flowsheet asset pipeline (t-391).

NEW FILES ONLY companion to data/flowsheet.yaml + scripts/render_flowsheet.py.
Does not import the rest of the simulator suite.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
YAML_PATH = REPO / "data" / "flowsheet.yaml"
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


def test_lint_passes_gracefully_without_trace_elements(renderer, flowsheet):
    result = renderer.lint_against_trace_elements(flowsheet)
    assert result.ok
    # On this base, trace_elements.yaml is absent → skip
    if not (REPO / "data" / "trace_elements.yaml").is_file():
        assert result.skipped
        assert any("skip" in m.lower() or "SKIPPED" in result.report_text() for m in result.messages + [result.report_text()])


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
