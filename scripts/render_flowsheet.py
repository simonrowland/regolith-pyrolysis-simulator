#!/usr/bin/env python3
"""Deterministic procedural SVG renderer for the plant flowsheet (bubble BFD).

Reads data/flowsheet.yaml, writes:
  docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet.svg

stdlib + PyYAML only (yaml from project .venv). Hand-rolled column/lane layout —
NOT graphviz. Fixed 4-column + terminal-rump geometry.

Layout rules (FIX-ROUND-1 + FIX-ROUND-2 + FIX-ROUND-3):
  - Vertical edge segments run only in gutters (between columns) or margin lanes.
  - Orthogonal elbows; edges enter boxes only at the endpoint boundary.
  - Annotation / ops text wraps to box width; boxes grow to fit full text.
  - Sub-box title and ops/subtitle stack on separate baselines (no title∩subtitle).
  - Edge labels are collision-aware: sit in gutters / inter-sub gaps / sky & return
    lanes — never over chips, sub-box interiors, box borders, or body text.
  - Columns size to content and top-align (not forced equal height).
  - One arrowhead per edge at destination only (sky vents end with upward stub).
  - geometry_self_check() fails on edge∩box interior, text overflow, canvas overflow,
    pairwise body text∩text overlap, and edge-label∩(chip|sub|border|body text).

CLI:
  python scripts/render_flowsheet.py [--yaml PATH] [--out PATH] [--demo-fill F]
  python scripts/render_flowsheet.py --lint
  python scripts/render_flowsheet.py --self-check   # schema + geometry + determinism

UI fill contract (web integration seam):
  Each species chip is:
    <g class="species-chip" data-species="..." data-bin="..."
       style="--fill-fraction: 0; --chip-h: H">
      <title>...</title>
      <clipPath id="clip-...">...</clipPath>
      <rect class="chip-face" .../>
      <rect class="fill-level" ... height computed from fraction .../>
      <text>...</text>
    </g>
  Later: set ONE CSS custom property --fill-fraction (0..1) on the <g>, and
  (for static SVG hosts without CSS height calc) the matching fill-level
  height/y attributes. Renderer --demo-fill proves the mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML required — run via project .venv: .venv/bin/python scripts/render_flowsheet.py"
    ) from exc

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = REPO_ROOT / "data" / "flowsheet.yaml"
DEFAULT_OUT = (
    REPO_ROOT
    / "docs-private"
    / "research"
    / "2026-07-19-plant-flowsheet"
    / "render"
    / "flowsheet.svg"
)
TRACE_ELEMENTS_YAML = REPO_ROOT / "data" / "trace_elements.yaml"

# ---------------------------------------------------------------------------
# Layout constants (deterministic, content-driven heights)
# ---------------------------------------------------------------------------

PAD_X = 28.0
PAD_Y = 28.0
# Left margin for feed arrow + "regolith feed" label (must clear canvas edge)
FEED_LANE_W = 92.0
# Inter-column gutter: wide enough for ≥1 vertical lane + labels
COL_GAP = 44.0
TERMINAL_WIDTH_RATIO = 0.72
HEADER_BASE = 34.0  # title + min ops; grows with wrapped ops/annots
# Sub-box title band: title baseline at SUB_TITLE_Y_OFF, ops start at SUB_HEADER_H.
# Must clear title glyph box (FS_SUB_TITLE) so title and subtitle never share a baseline.
SUB_TITLE_Y_OFF = 14.0
SUB_HEADER_H = 28.0  # y-offset from sub top to first ops / content row
SUB_PAD = 8.0
CHIP_W = 48.0
CHIP_W_MAX = 92.0
CHIP_H = 18.0
CHIP_GAP_X = 4.0
CHIP_GAP_Y = 5.0
CHIP_ROW_MAX = 5
ANNOT_LINE_H = 11.0
OPS_LINE_H = 11.0
BLOCK_PAD = 10.0
SUB_GAP = 12.0
LEGEND_H = 56.0
TITLE_H = 40.0
# Top sky lane / bottom return lane heights
SKY_LANE_H = 36.0
RETURN_LANE_H = 48.0
CORNER_R = 10.0
SUB_CORNER_R = 6.0
CHIP_CORNER_R = 4.0

# Approximate glyph widths (px at 1em) for wrap / overflow checks
CHAR_W_SANS = 0.52
CHAR_W_MONO = 0.60
# Font sizes used in CSS classes
FS_TITLE = 16.0
FS_BLOCK_TITLE = 12.0
FS_OPS = 9.0
FS_SUB_TITLE = 10.0
FS_ANNOT = 8.5
FS_EDGE_LABEL = 8.0
FS_CHIP = 9.0
FS_LEGEND = 10.0

# Colors (approved look: light fills, blue chips, orange oxygen)
C_BG = "#f8fafc"
C_BLOCK_FILL = "#ffffff"
C_BLOCK_STROKE = "#64748b"
C_SUB_FILL = "#f1f5f9"
C_SUB_STROKE = "#94a3b8"
C_TEXT = "#0f172a"
C_TEXT_MUTED = "#475569"
C_CHIP_FILL = "rgba(59,130,246,0.12)"
C_CHIP_STROKE = "#3b82f6"
C_CHIP_TEXT = "#1e3a8a"
C_FILL_LEVEL = "rgba(59,130,246,0.45)"
C_OXYGEN = "#d97706"
C_MAIN = "#334155"
C_RETURN = "#64748b"
C_LEGEND_BG = "#eef2ff"

# Epsilon for geometric tests (treat boundary as non-interior)
GEO_EPS = 0.75


# ---------------------------------------------------------------------------
# Data loading / schema
# ---------------------------------------------------------------------------


def load_flowsheet(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"flowsheet root must be a mapping: {path}")
    return data


def validate_schema(data: dict[str, Any]) -> list[str]:
    """Return list of schema errors (empty = ok)."""
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not data.get("title"):
        errors.append("title required")
    if not isinstance(data.get("blocks"), list) or not data["blocks"]:
        errors.append("blocks must be a non-empty list")
        return errors
    if not isinstance(data.get("edges"), list):
        errors.append("edges must be a list")
    if not isinstance(data.get("legend"), list):
        errors.append("legend must be a list")

    seen_block: set[str] = set()
    seen_sub: set[str] = set()
    seen_species: set[str] = set()
    for block in data["blocks"]:
        bid = block.get("id")
        if not bid:
            errors.append("block missing id")
            continue
        if bid in seen_block:
            errors.append(f"duplicate block id: {bid}")
        seen_block.add(bid)
        if not block.get("title"):
            errors.append(f"block {bid}: title required")
        if block.get("role") not in ("column", "terminal"):
            errors.append(f"block {bid}: role must be column|terminal")
        for sub in block.get("sub_boxes") or []:
            sid = sub.get("id")
            if not sid:
                errors.append(f"block {bid}: sub_box missing id")
                continue
            if sid in seen_sub or sid in seen_block:
                errors.append(f"duplicate sub_box/block id: {sid}")
            seen_sub.add(sid)
            for sp in sub.get("species") or []:
                sym = sp.get("symbol_or_group")
                status = sp.get("status")
                if not sym:
                    errors.append(f"sub {sid}: species missing symbol_or_group")
                    continue
                if sym in seen_species:
                    errors.append(f"species chip appears more than once: {sym}")
                seen_species.add(sym)
                if status not in ("reviewed", "conditional"):
                    errors.append(f"{sym}: status must be reviewed|conditional")
                if status == "conditional" and not sp.get("condition_note"):
                    errors.append(f"{sym}: conditional requires condition_note")

    for i, edge in enumerate(data.get("edges") or []):
        if edge.get("class") not in ("main", "oxygen", "reagent_return"):
            errors.append(f"edge[{i}]: class must be main|oxygen|reagent_return")
        if not edge.get("from") or not edge.get("to"):
            errors.append(f"edge[{i}]: from/to required")

    return errors


def iter_species_chips(data: dict[str, Any]):
    """Yield (block_id, sub_box_id, species_dict)."""
    for block in data.get("blocks") or []:
        for sub in block.get("sub_boxes") or []:
            for sp in sub.get("species") or []:
                yield block["id"], sub["id"], sp


def species_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map symbol_or_group -> {bin, status, condition_note}."""
    out: dict[str, dict[str, Any]] = {}
    for _bid, sid, sp in iter_species_chips(data):
        out[sp["symbol_or_group"]] = {
            "bin": sid,
            "status": sp["status"],
            "condition_note": sp.get("condition_note"),
        }
    return out


def aggregate_membership(data: dict[str, Any]) -> dict[str, str]:
    """Map member species id -> aggregate chip symbol."""
    mapping: dict[str, str] = {}
    for agg in data.get("aggregates") or []:
        chip = agg["chip"]
        for m in agg.get("members") or []:
            mapping[str(m)] = chip
    return mapping


# ---------------------------------------------------------------------------
# Drift lint vs data/trace_elements.yaml
# ---------------------------------------------------------------------------


@dataclass
class LintResult:
    ok: bool
    skipped: bool
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def report_text(self) -> str:
        lines = []
        if self.skipped:
            lines.append("LINT: SKIPPED — data/trace_elements.yaml not present on this base.")
            lines.extend(self.messages)
            return "\n".join(lines)
        status = "PASS" if self.ok else "FAIL"
        lines.append(f"LINT: {status}")
        lines.extend(self.messages)
        lines.extend(f"ERROR: {e}" for e in self.errors)
        return "\n".join(lines)


def _extract_routed_species(trace_doc: Any) -> list[dict[str, Any]]:
    """Best-effort parse of trace_elements.yaml into routed species records."""
    if not isinstance(trace_doc, dict):
        return []
    records: list[dict[str, Any]] = []

    def push(sym: str, route: str | None, classification: str | None) -> None:
        if not sym:
            return
        records.append(
            {
                "symbol": str(sym),
                "route": route,
                "classification": (classification or "").lower() or None,
            }
        )

    for key in ("species", "elements", "routing", "routes"):
        items = trace_doc.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = (
                item.get("id")
                or item.get("symbol")
                or item.get("species")
                or item.get("element")
            )
            route = (
                item.get("route")
                or item.get("bin")
                or item.get("destination")
                or item.get("flowsheet_bin")
            )
            classification = (
                item.get("classification")
                or item.get("status")
                or item.get("conditionality")
            )
            if route is None and item.get("routing"):
                route = item["routing"] if isinstance(item["routing"], str) else None
            if sym and route:
                push(str(sym), str(route), str(classification) if classification else None)
    return records


def lint_against_trace_elements(
    data: dict[str, Any],
    trace_path: Path = TRACE_ELEMENTS_YAML,
) -> LintResult:
    if not trace_path.is_file():
        return LintResult(
            ok=True,
            skipped=True,
            messages=[
                f"Missing {trace_path.relative_to(REPO_ROOT)} — drift lint skipped (graceful).",
                "When trace_elements.yaml lands, re-run: scripts/render_flowsheet.py --lint",
            ],
        )

    with trace_path.open("r", encoding="utf-8") as fh:
        trace_doc = yaml.safe_load(fh)
    routed = _extract_routed_species(trace_doc)
    if not routed:
        return LintResult(
            ok=True,
            skipped=False,
            messages=[
                f"{trace_path.name} present but no routed species entries found — nothing to cross-check.",
            ],
        )

    chips = species_index(data)
    agg_map = aggregate_membership(data)
    errors: list[str] = []
    messages: list[str] = []
    covered_chips: set[str] = set()

    for rec in routed:
        sym = rec["symbol"]
        chip_sym = sym if sym in chips else agg_map.get(sym)
        if chip_sym is None:
            errors.append(
                f"orphan (trace→flowsheet): routed species {sym!r} has no chip "
                f"and is not in any aggregate membership"
            )
            continue
        if chip_sym not in chips:
            errors.append(
                f"aggregate {chip_sym!r} claims member {sym!r} but chip is missing on flowsheet"
            )
            continue
        covered_chips.add(chip_sym)
        cls = rec.get("classification")
        chip_status = chips[chip_sym]["status"]
        if cls:
            wants_conditional = cls in (
                "conditional",
                "uncertain",
                "mode_dependent",
                "host_dependent",
            )
            wants_reviewed = cls in ("reviewed", "confirmed", "default", "primary")
            if wants_conditional and chip_status != "conditional":
                errors.append(
                    f"conditionality mismatch: {sym} classified {cls!r} in trace_elements "
                    f"but chip {chip_sym!r} is {chip_status!r}"
                )
            if wants_reviewed and chip_status != "reviewed":
                errors.append(
                    f"conditionality mismatch: {sym} classified {cls!r} in trace_elements "
                    f"but chip {chip_sym!r} is {chip_status!r}"
                )

    aggregate_chips = {a["chip"] for a in (data.get("aggregates") or [])}
    major_process_chips = {
        "H2O", "CO2", "S", "SO2", "F", "Cl", "Br", "I", "O2",
        "Mg", "Al", "Ti", "V", "Nb", "Ta", "Ca", "Sr", "Ba",
        "Eu", "Yb", "Y", "Th", "U", "Be", "Zr", "Hf", "Sc",
        "Na", "K", "Rb", "Cs", "Fe", "Ni", "Co", "Ru", "Rh", "Pd",
        "Re", "Os", "Ir", "Pt", "Au", "Mo", "W", "Zn", "Cd", "Pb",
        "Tl", "Bi", "glass", "salts", "organics", "REE", "unreduced-residuals",
    }
    for sym, info in chips.items():
        if sym in covered_chips:
            continue
        if sym in aggregate_chips or sym in major_process_chips:
            messages.append(
                f"note: flowsheet chip {sym!r} (bin={info['bin']}) has no dedicated "
                f"routed row — accepted as process/aggregate bin"
            )
            continue
        errors.append(
            f"orphan (flowsheet→trace): chip {sym!r} not covered by any routed "
            f"trace species or aggregate membership"
        )

    messages.insert(0, f"routed species checked: {len(routed)}; flowsheet chips: {len(chips)}")
    return LintResult(ok=not errors, skipped=False, messages=messages, errors=errors)


# ---------------------------------------------------------------------------
# Geometry helpers — text metrics, wrapping, rects
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def text_width(text: str, font_size: float, mono: bool = False) -> float:
    """Deterministic approximate rendered width (sans/mono)."""
    factor = CHAR_W_MONO if mono else CHAR_W_SANS
    # Unicode / multi-byte symbols slightly wider; treat each codepoint equally
    return len(text) * font_size * factor


def wrap_to_width(text: str, max_width: float, font_size: float, mono: bool = False) -> list[str]:
    """Word-wrap *text* to max_width px. Never splits mid-word unless a single
    word exceeds max_width (then the whole word is kept on its own line).
    Returns every line needed — callers must grow the container, never clip.
    """
    text = str(text or "").strip()
    if not text:
        return []
    if max_width <= 0:
        return [text]
    words = text.split()
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if text_width(trial, font_size, mono=mono) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def chip_width_for_label(symbol: str) -> float:
    est = text_width(symbol, FS_CHIP, mono=True) + 14.0
    return max(CHIP_W, min(CHIP_W_MAX, est))


def chips_per_row_for_width(inner_w: float, chip_w: float = CHIP_W) -> int:
    usable = max(chip_w, inner_w - 2 * SUB_PAD)
    n = int((usable + CHIP_GAP_X) // (chip_w + CHIP_GAP_X))
    return max(1, min(CHIP_ROW_MAX, n))


def chip_rows(n: int, per_row: int = CHIP_ROW_MAX) -> int:
    if n <= 0:
        return 0
    return int(math.ceil(n / per_row))


def sub_box_chip_layout(
    sub: dict[str, Any], inner_w: float
) -> tuple[int, float, float]:
    """Return (per_row, chip_w, chips_height) for a sub-box."""
    species = sub.get("species") or []
    if not species:
        return 1, CHIP_W, 0.0
    chip_w = max(chip_width_for_label(sp["symbol_or_group"]) for sp in species)
    chip_w = min(chip_w, max(CHIP_W, inner_w - 2 * SUB_PAD))
    per_row = chips_per_row_for_width(inner_w, chip_w=chip_w)
    n = len(species)
    rows = chip_rows(n, per_row)
    chips_h = rows * CHIP_H + max(0, rows - 1) * CHIP_GAP_Y
    return per_row, chip_w, chips_h


def _annot_text_width_budget(box_w: float) -> float:
    return max(24.0, box_w - 2 * SUB_PAD - 4.0)


def _count_wrapped_lines(strings: list[str], max_w: float, font_size: float) -> int:
    total = 0
    for s in strings:
        total += max(1, len(wrap_to_width(s, max_w, font_size)))
    return total


def sub_box_content_height(sub: dict[str, Any], inner_w: float) -> float:
    _per_row, _chip_w, chips_h = sub_box_chip_layout(sub, inner_w)
    annots = list(sub.get("annotations") or [])
    budget = _annot_text_width_budget(inner_w)
    # Prefix "· " is rendered with each annotation
    annot_lines = 0
    for a in annots:
        annot_lines += max(1, len(wrap_to_width("· " + a, budget, FS_ANNOT)))
    annot_h = annot_lines * ANNOT_LINE_H + (4.0 if annots else 0.0)
    ops_h = 0.0
    if sub.get("operating_conditions"):
        ops_lines = wrap_to_width(sub["operating_conditions"], budget, FS_OPS)
        ops_h = len(ops_lines) * OPS_LINE_H + 2.0
    # SUB_HEADER_H = title band; then ops; gap before chips; chips; annots; bottom pad
    chip_gap = 4.0 if (chips_h > 0 or annots) else 0.0
    return SUB_HEADER_H + ops_h + chip_gap + chips_h + annot_h + SUB_PAD


def block_header_height(block: dict[str, Any], col_inner_w: float) -> float:
    """Content-driven header: title + wrapped ops + wrapped block annotations."""
    budget = max(24.0, col_inner_w - 4.0)
    h = 18.0  # title baseline area
    ops = block.get("operating_conditions") or ""
    if ops:
        h += len(wrap_to_width(ops, budget, FS_OPS)) * OPS_LINE_H + 4.0
    else:
        h += 4.0
    annots = block.get("annotations") or []
    for a in annots:
        h += len(wrap_to_width("· " + a, budget, FS_ANNOT)) * ANNOT_LINE_H
    if annots:
        h += 4.0
    return max(HEADER_BASE, h)


def block_content_height(block: dict[str, Any], col_inner_w: float) -> float:
    subs = block.get("sub_boxes") or []
    h = block_header_height(block, col_inner_w)
    for i, sub in enumerate(subs):
        h += sub_box_content_height(sub, inner_w=col_inner_w)
        if i < len(subs) - 1:
            h += SUB_GAP
    h += 2 * BLOCK_PAD
    return h


# ---------------------------------------------------------------------------
# Placed geometry
# ---------------------------------------------------------------------------


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    kind: str = ""  # block | sub | chip
    id: str = ""

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def contains_point(self, px: float, py: float, eps: float = GEO_EPS) -> bool:
        """Strict interior (shrink by eps so boundary is outside)."""
        return (
            self.x + eps < px < self.x2 - eps
            and self.y + eps < py < self.y2 - eps
        )


@dataclass
class PlacedChip:
    species: str
    bin_id: str
    status: str
    condition_note: str | None
    x: float
    y: float
    w: float
    h: float
    fill_fraction: float


@dataclass
class PlacedText:
    """Tracked text for geometry self-check."""
    text: str
    x: float  # left or anchor x depending on anchor
    y: float  # baseline y
    font_size: float
    mono: bool = False
    anchor: str = "start"  # start | middle | end
    owner_id: str | None = None  # box that must contain this text
    role: str = "body"  # body | edge_label | title | legend

    def extent(self) -> tuple[float, float, float, float]:
        """Return (x0, y0, x1, y1) axis-aligned bounds (approx)."""
        tw = text_width(self.text, self.font_size, mono=self.mono)
        if self.anchor == "middle":
            x0 = self.x - tw / 2.0
        elif self.anchor == "end":
            x0 = self.x - tw
        else:
            x0 = self.x
        # baseline → rough em-box
        y0 = self.y - self.font_size * 0.85
        y1 = self.y + self.font_size * 0.25
        return x0, y0, x0 + tw, y1


@dataclass
class PlacedSub:
    id: str
    title: str
    x: float
    y: float
    w: float
    h: float
    chips: list[PlacedChip]
    annotations: list[str]
    operating_conditions: str | None
    parent_id: str = ""


@dataclass
class PlacedBlock:
    id: str
    title: str
    role: str
    operating_conditions: str
    x: float
    y: float
    w: float
    h: float
    subs: list[PlacedSub]
    annotations: list[str]


@dataclass
class EdgeRoute:
    edge_id: str
    cls: str
    frm: str
    to: str
    points: list[tuple[float, float]]  # polyline
    label: str | None = None
    label_x: float = 0.0
    label_y: float = 0.0
    label_anchor: str = "middle"


@dataclass
class Layout:
    width: float
    height: float
    blocks: list[PlacedBlock]
    anchors: dict[str, tuple[float, float]]
    title: str
    # Gutter lane x positions (between columns + left/right margins)
    gutters: list[float] = field(default_factory=list)
    # id -> Rect for obstacles (blocks + subs + chips)
    obstacles: dict[str, Rect] = field(default_factory=dict)
    edges: list[EdgeRoute] = field(default_factory=list)
    texts: list[PlacedText] = field(default_factory=list)
    # y of top sky lane and bottom return lane midlines
    sky_y: float = 0.0
    return_y: float = 0.0
    content_y0: float = 0.0
    content_y1: float = 0.0


# ---------------------------------------------------------------------------
# Layout computation
# ---------------------------------------------------------------------------


def compute_layout(data: dict[str, Any], demo_fill: float = 0.0) -> Layout:
    columns = list((data.get("layout") or {}).get("columns") or [])
    by_id = {b["id"]: b for b in data["blocks"]}
    if not columns:
        columns = [b["id"] for b in data["blocks"]]

    # Canvas width: fixed readable size; left feed lane reserved inside pad
    canvas_w = 1620.0
    # Usable area after outer pad + left feed lane
    left0 = PAD_X + FEED_LANE_W
    right0 = canvas_w - PAD_X
    inner_w = right0 - left0
    n_full = sum(1 for c in columns if by_id[c].get("role") != "terminal")
    n_term = sum(1 for c in columns if by_id[c].get("role") == "terminal")
    gap_total = COL_GAP * (len(columns) - 1)
    usable = inner_w - gap_total
    full_w = usable / (n_full + n_term * TERMINAL_WIDTH_RATIO)
    term_w = full_w * TERMINAL_WIDTH_RATIO

    def col_w(bid: str) -> float:
        return term_w if by_id[bid].get("role") == "terminal" else full_w

    heights = {
        bid: block_content_height(by_id[bid], col_inner_w=col_w(bid) - 2 * BLOCK_PAD)
        for bid in columns
    }
    # Top-align: each column keeps its own content height (no forced equalize)
    max_col_h = max(heights.values()) if heights else 200.0

    title_y0 = PAD_Y
    content_y0 = title_y0 + TITLE_H + SKY_LANE_H
    blocks_out: list[PlacedBlock] = []
    anchors: dict[str, tuple[float, float]] = {}
    obstacles: dict[str, Rect] = {}
    gutters: list[float] = []  # x mid of each inter-column gutter + side margins

    # Left margin gutter (for feed + returns climbing left side)
    gutters.append(PAD_X + FEED_LANE_W * 0.55)

    x = left0
    col_xs: list[tuple[str, float, float]] = []  # bid, x, w
    for bi, bid in enumerate(columns):
        block = by_id[bid]
        bw = col_w(bid)
        bh = heights[bid]  # content-sized, top-aligned
        by = content_y0
        col_xs.append((bid, x, bw))

        if bi > 0:
            # gutter mid between previous right and this left
            prev_right = col_xs[bi - 1][1] + col_xs[bi - 1][2]
            gutters.append((prev_right + x) / 2.0)

        placed_subs: list[PlacedSub] = []
        header_h = block_header_height(block, bw - 2 * BLOCK_PAD)
        cursor_y = by + header_h + BLOCK_PAD
        annots = list(block.get("annotations") or [])
        inner_w_block = bw - 2 * BLOCK_PAD

        for sub in block.get("sub_boxes") or []:
            per_row, chip_w, _ch = sub_box_chip_layout(sub, inner_w_block)
            sh = sub_box_content_height(sub, inner_w=inner_w_block)
            sx = x + BLOCK_PAD
            sy = cursor_y
            chips: list[PlacedChip] = []
            species = sub.get("species") or []
            budget = _annot_text_width_budget(inner_w_block)
            # Title band [sy, sy+SUB_HEADER_H); ops; gap; then chips
            chip_y0 = sy + SUB_HEADER_H
            if sub.get("operating_conditions"):
                ops_lines = wrap_to_width(sub["operating_conditions"], budget, FS_OPS)
                chip_y0 += len(ops_lines) * OPS_LINE_H + 2.0
            if species or sub.get("annotations"):
                chip_y0 += 4.0
            for i, sp in enumerate(species):
                row, col = divmod(i, per_row)
                cx = sx + SUB_PAD + col * (chip_w + CHIP_GAP_X)
                cy = chip_y0 + row * (CHIP_H + CHIP_GAP_Y)
                max_x = sx + inner_w_block - SUB_PAD - chip_w
                if cx > max_x:
                    cx = max_x
                chips.append(
                    PlacedChip(
                        species=sp["symbol_or_group"],
                        bin_id=sub["id"],
                        status=sp["status"],
                        condition_note=sp.get("condition_note"),
                        x=cx,
                        y=cy,
                        w=chip_w,
                        h=CHIP_H,
                        fill_fraction=float(demo_fill),
                    )
                )
                obstacles[f"chip:{sp['symbol_or_group']}"] = Rect(
                    cx, cy, chip_w, CHIP_H, kind="chip", id=sp["symbol_or_group"]
                )
            placed_subs.append(
                PlacedSub(
                    id=sub["id"],
                    title=sub["title"],
                    x=sx,
                    y=sy,
                    w=inner_w_block,
                    h=sh,
                    chips=chips,
                    annotations=list(sub.get("annotations") or []),
                    operating_conditions=sub.get("operating_conditions"),
                    parent_id=bid,
                )
            )
            obstacles[sub["id"]] = Rect(sx, sy, inner_w_block, sh, kind="sub", id=sub["id"])
            # Port anchors on sub-box boundary (not center — edges attach at rim)
            anchors[sub["id"]] = (sx + inner_w_block / 2.0, sy + sh / 2.0)
            anchors[f"{sub['id']}__top"] = (sx + inner_w_block / 2.0, sy)
            anchors[f"{sub['id']}__bottom"] = (sx + inner_w_block / 2.0, sy + sh)
            anchors[f"{sub['id']}__left"] = (sx, sy + sh / 2.0)
            anchors[f"{sub['id']}__right"] = (sx + inner_w_block, sy + sh / 2.0)
            cursor_y += sh + SUB_GAP

        # Trim trailing gap from measured height already in bh
        obstacles[bid] = Rect(x, by, bw, bh, kind="block", id=bid)
        anchors[bid] = (x + bw / 2.0, by + bh / 2.0)
        anchors[f"{bid}__in"] = (x, by + bh / 2.0)
        anchors[f"{bid}__out"] = (x + bw, by + bh / 2.0)
        anchors[f"{bid}__top"] = (x + bw / 2.0, by)
        anchors[f"{bid}__bottom"] = (x + bw / 2.0, by + bh)
        anchors[f"{bid}__topleft"] = (x, by)
        anchors[f"{bid}__topright"] = (x + bw, by)

        blocks_out.append(
            PlacedBlock(
                id=bid,
                title=block["title"],
                role=block.get("role", "column"),
                operating_conditions=block.get("operating_conditions") or "",
                x=x,
                y=by,
                w=bw,
                h=bh,
                subs=placed_subs,
                annotations=annots,
            )
        )
        x += bw + COL_GAP

    # Right margin gutter
    last_right = col_xs[-1][1] + col_xs[-1][2]
    gutters.append(last_right + (right0 - last_right) * 0.5 if right0 > last_right else last_right + 12)

    # External anchors
    first = blocks_out[0]
    anchors["feed"] = (first.x - FEED_LANE_W * 0.35, first.y + min(first.h, max_col_h) * 0.45)
    sky_y = content_y0 - SKY_LANE_H * 0.45
    anchors["sky"] = (canvas_w / 2.0, sky_y)

    content_y1 = content_y0 + max_col_h
    # Provisional return lane; may grow after routing if many return labels stack
    n_returns = sum(
        1 for e in (data.get("edges") or []) if e.get("class") == "reagent_return"
    )
    return_lane_h = max(RETURN_LANE_H, 20.0 + n_returns * 14.0)
    return_y = content_y1 + return_lane_h * 0.35
    total_h = content_y1 + return_lane_h + LEGEND_H + PAD_Y

    layout = Layout(
        width=canvas_w,
        height=total_h,
        blocks=blocks_out,
        anchors=anchors,
        title=data.get("title") or "Plant flowsheet",
        gutters=gutters,
        obstacles=obstacles,
        sky_y=sky_y,
        return_y=return_y,
        content_y0=content_y0,
        content_y1=content_y1,
    )
    layout.edges = route_edges(data, layout)
    # FIX-ROUND-3: nudge edge labels off chips / box text / borders into free space
    resolve_edge_label_collisions(layout)
    # Pad canvas below the lowest edge point / edge label (return labels must clear)
    max_y = content_y1 + return_lane_h
    for edge in layout.edges:
        for px, py in edge.points:
            max_y = max(max_y, py)
        if edge.label:
            max_y = max(max_y, edge.label_y + FS_EDGE_LABEL * 0.5 + 4.0)
    layout.height = max_y + 12.0 + LEGEND_H + PAD_Y
    # Re-run collision resolve once canvas height is final (return-lane labels)
    # only if any label fell outside provisional H — already constrained in resolve.
    return layout


def _block_by_id(layout: Layout, bid: str) -> PlacedBlock | None:
    for b in layout.blocks:
        if b.id == bid:
            return b
    return None


def _sub_by_id(layout: Layout, sid: str) -> PlacedSub | None:
    for b in layout.blocks:
        for s in b.subs:
            if s.id == sid:
                return s
    return None


def _sub_parent_map(layout: Layout) -> dict[str, str]:
    m: dict[str, str] = {}
    for b in layout.blocks:
        for s in b.subs:
            m[s.id] = b.id
    return m


def _gutter_right_of(layout: Layout, block_id: str) -> float:
    """X of the gutter immediately to the right of block_id."""
    for i, b in enumerate(layout.blocks):
        if b.id == block_id:
            # gutters[0] is left margin; gutters[i+1] is right of block i
            if i + 1 < len(layout.gutters):
                return layout.gutters[i + 1]
            return b.x + b.w + COL_GAP / 2.0
    return layout.gutters[-1]


def _gutter_left_of(layout: Layout, block_id: str) -> float:
    for i, b in enumerate(layout.blocks):
        if b.id == block_id:
            return layout.gutters[i] if i < len(layout.gutters) else b.x - COL_GAP / 2.0
    return layout.gutters[0]


def _lane_x(gutter_x: float, lane_index: int, spacing: float = 10.0) -> float:
    """Stagger multiple verticals inside the same gutter."""
    # Center lane 0; alternate ± around gutter mid
    if lane_index == 0:
        return gutter_x
    sign = 1 if lane_index % 2 == 1 else -1
    step = (lane_index + 1) // 2
    return gutter_x + sign * step * spacing


def _label_width(label: str) -> float:
    return text_width(label, FS_EDGE_LABEL) + 4.0


def route_edges(data: dict[str, Any], layout: Layout) -> list[EdgeRoute]:
    """Orthogonal gutter-lane routing. Verticals only in gutters / sky / return lanes."""
    routes: list[EdgeRoute] = []
    a = layout.anchors
    block_ids = {b.id for b in layout.blocks}
    sub_ids = {s.id for b in layout.blocks for s in b.subs}
    sub_parent = _sub_parent_map(layout)

    # Lane counters per gutter key for staggering
    gutter_lane_use: dict[float, int] = {}
    sky_slot = 0
    return_slot = 0

    def next_gutter_lane(gx: float) -> float:
        idx = gutter_lane_use.get(gx, 0)
        gutter_lane_use[gx] = idx + 1
        return _lane_x(gx, idx)

    for edge in data.get("edges") or []:
        frm, to, cls = edge["from"], edge["to"], edge["class"]
        label = edge.get("label")
        eid = edge.get("id") or f"{frm}->{to}"

        # Skip pure containment offtakes (block contains its own sub_box)
        if frm in block_ids and to in sub_ids and sub_parent.get(to) == frm:
            continue

        # ---- Melt spine: block → block horizontal via gutter mid ----
        if frm in block_ids and to in block_ids:
            b1 = _block_by_id(layout, frm)
            b2 = _block_by_id(layout, to)
            assert b1 and b2
            # Attach at mid-height of the shorter shared band (top-aligned content)
            y_mid = min(b1.y + b1.h * 0.35, b2.y + b2.h * 0.35)
            y_mid = max(b1.y + 24.0, y_mid)
            x1, y1 = b1.x + b1.w, y_mid
            x2, y2 = b2.x, y_mid
            gx = (x1 + x2) / 2.0
            pts = [(x1, y1), (gx, y1), (gx, y2), (x2, y2)]
            # Label above the horizontal in the gutter; prefer sky-adjacent if
            # the mid-band sits inside both column headers (collision resolver
            # will nudge further if needed).
            lx = gx
            ly = y_mid - 10.0
            # Prefer a y just above both block tops when the label is wider than
            # the gutter strip (wide labels cannot sit fully between columns).
            if label and _label_width(label) > COL_GAP - 4.0:
                ly = min(b1.y, b2.y) - 6.0
            routes.append(
                EdgeRoute(eid, cls, frm, to, pts, label, lx, ly, "middle")
            )
            continue

        # ---- Feed ----
        if frm == "feed":
            x1, y1 = a["feed"]
            b2 = _block_by_id(layout, to) if to in block_ids else None
            if b2:
                x2, y2 = b2.x, y1
            else:
                x2, y2 = a.get(f"{to}__in", a.get(to, (x1 + 40, y1)))
            pts = [(x1, y1), (x2, y2)]
            # Label above the feed segment, fully inside canvas (FEED_LANE)
            lx = (x1 + x2) / 2.0
            ly = y1 - 10.0
            routes.append(
                EdgeRoute(eid, cls, frm, to, pts, label or "regolith feed", lx, ly, "middle")
            )
            continue

        # ---- Oxygen → sky: side exit → gutter vertical → upward vent stub ----
        # End with a short vertical UP so marker-end points skyward. Do NOT land with a
        # horizontal segment on the O₂-lance sky-transit corridor (that painted mid-span
        # arrowheads on the lance lane in FIX-ROUND-1).
        if cls == "oxygen" and to == "sky":
            if frm in sub_ids:
                sx, sy = a[f"{frm}__top"]
                parent = sub_parent[frm]
            else:
                sx, sy = a.get(f"{frm}__top", a.get(frm, (0.0, 0.0)))
                parent = frm if frm in block_ids else layout.blocks[0].id
            gx = next_gutter_lane(_gutter_right_of(layout, parent))
            # Vent tip sits ABOVE the lance transit y (layout.sky_y) so arrowheads clear it
            y_vent = layout.sky_y - 10.0 - sky_slot * 8.0
            sky_slot += 1
            sub = _sub_by_id(layout, frm) if frm in sub_ids else None
            if sub is not None:
                exit_x, exit_y = sub.x + sub.w, sub.y + 8.0  # near top-right of source
                pts = [
                    (exit_x, exit_y),
                    (gx, exit_y),
                    (gx, y_vent),  # single upward arrowhead at sky vent tip
                ]
            else:
                exit_x, exit_y = sx, sy
                pts = [(sx, sy), (gx, sy), (gx, y_vent)]
            # Label in the sky lane beside the vent (avoids column header text).
            # Prefer end-anchor left of gutter so text stays in the gutter/sky
            # strip rather than spilling into the next column.
            lx = gx - 4.0
            ly = min(layout.sky_y - 2.0, (exit_y + y_vent) / 2.0)
            # If mid-vertical is still below content top, pin to sky lane
            if ly > layout.content_y0 - 4.0:
                ly = layout.sky_y - 2.0
            routes.append(
                EdgeRoute(eid, cls, frm, to, pts, label, lx, ly, "end")
            )
            continue

        # ---- Oxygen lance / other oxygen (e.g. lox → early bake) ----
        if cls == "oxygen":
            # Route: exit source right → right gutter of source parent → up to sky lane
            # → left across sky lane → down left gutter of target → into target top
            if frm in sub_ids:
                sub = _sub_by_id(layout, frm)
                parent = sub_parent[frm]
                assert sub
                exit_x, exit_y = sub.x + sub.w, sub.y + sub.h * 0.5
            else:
                parent = frm
                exit_x, exit_y = a.get(f"{frm}__out", a.get(frm, (0.0, 0.0)))
            gx_src = next_gutter_lane(_gutter_right_of(layout, parent))
            if to in block_ids:
                tgt = _block_by_id(layout, to)
                assert tgt
                enter_x, enter_y = tgt.x + tgt.w * 0.5, tgt.y
                gx_tgt = next_gutter_lane(_gutter_left_of(layout, to))
            elif to in sub_ids:
                tsub = _sub_by_id(layout, to)
                assert tsub
                enter_x, enter_y = tsub.x + tsub.w * 0.5, tsub.y
                gx_tgt = next_gutter_lane(_gutter_left_of(layout, sub_parent[to]))
            else:
                enter_x, enter_y = a.get(to, (exit_x, exit_y - 40))
                gx_tgt = layout.gutters[0]
            y_sky = layout.sky_y
            pts = [
                (exit_x, exit_y),
                (gx_src, exit_y),
                (gx_src, y_sky),
                (gx_tgt, y_sky),
                (gx_tgt, enter_y - 0.0),
                (enter_x, enter_y),
            ]
            lx = (gx_src + gx_tgt) / 2.0
            ly = y_sky - 6.0
            routes.append(
                EdgeRoute(eid, cls, frm, to, pts, label, lx, ly, "middle")
            )
            continue

        # ---- Reagent / oxide returns: bottom return lane ----
        if cls == "reagent_return":
            if frm in sub_ids:
                sub = _sub_by_id(layout, frm)
                assert sub
                exit_x, exit_y = sub.x + sub.w * 0.5, sub.y + sub.h
                parent = sub_parent[frm]
            else:
                exit_x, exit_y = a.get(f"{frm}__bottom", a.get(frm, (0.0, 0.0)))
                parent = frm
            # Drop into right gutter of source, down to return lane, left to target, up
            gx_src = next_gutter_lane(_gutter_right_of(layout, parent))
            y_ret = layout.return_y + return_slot * 11.0
            return_slot += 1
            if to in block_ids:
                tgt = _block_by_id(layout, to)
                assert tgt
                enter_x, enter_y = tgt.x + tgt.w * 0.5, tgt.y + tgt.h
                gx_tgt = next_gutter_lane(_gutter_left_of(layout, to))
            elif to in sub_ids:
                tsub = _sub_by_id(layout, to)
                assert tsub
                enter_x, enter_y = tsub.x + tsub.w * 0.5, tsub.y + tsub.h
                gx_tgt = next_gutter_lane(_gutter_left_of(layout, sub_parent[to]))
            else:
                enter_x, enter_y = a.get(to, (exit_x - 40, exit_y))
                gx_tgt = layout.gutters[0]
            pts = [
                (exit_x, exit_y),
                (exit_x, exit_y + 6.0),
                (gx_src, exit_y + 6.0),
                (gx_src, y_ret),
                (gx_tgt, y_ret),
                (gx_tgt, enter_y + 6.0),
                (enter_x, enter_y + 6.0),
                (enter_x, enter_y),
            ]
            lx = (gx_src + gx_tgt) / 2.0
            ly = y_ret + 10.0
            routes.append(
                EdgeRoute(eid, cls, frm, to, pts, label, lx, ly, "middle")
            )
            continue

        # ---- Process chain inside a column (sub → sub, e.g. Ca dose) ----
        if frm in sub_ids and to in sub_ids:
            s1 = _sub_by_id(layout, frm)
            s2 = _sub_by_id(layout, to)
            assert s1 and s2
            # Same column: short vertical in the gap between boxes only
            if s1.parent_id == s2.parent_id:
                x_mid = s1.x + s1.w * 0.5
                y1 = s1.y + s1.h  # bottom rim of upper
                y2 = s2.y  # top rim of lower
                # Offset connector slightly toward the right gutter so label clears body text
                parent = s1.parent_id
                gx = _gutter_right_of(layout, parent)
                # Prefer a mid-gap vertical at 70% width (still between box edges, not through chips)
                x_conn = s1.x + s1.w * 0.72
                # Keep connector x inside both boxes' x-range so entry is on the rim
                x_conn = min(x_conn, s1.x + s1.w - 8.0, s2.x + s2.w - 8.0)
                x_conn = max(x_conn, s1.x + 8.0, s2.x + 8.0)
                pts = [(x_conn, y1), (x_conn, y2)]
                # Prefer inter-sub gap strip (whitespace between stacked subs).
                # Baseline keeps glyph box inside the gap when gap is tall enough.
                gap_y0, gap_y1 = y1, y2
                y_lo = gap_y0 + 1.0 + FS_EDGE_LABEL * 0.85
                y_hi = gap_y1 - 1.0 - FS_EDGE_LABEL * 0.25
                if y_lo <= y_hi:
                    ly = (y_lo + y_hi) * 0.5
                else:
                    ly = (y1 + y2) / 2.0
                # Place label to the left of the connector when it fits; else gutter
                # with end-anchor so text grows left into the gutter (not into the
                # next column's chips).
                tw = _label_width(label) if label else 0.0
                left_room = x_conn - s1.x - 8.0
                right_room = s1.x + s1.w - x_conn - 8.0
                if label and tw <= left_room:
                    lx, anch = x_conn - 6.0, "end"
                elif label and tw <= right_room:
                    lx, anch = x_conn + 6.0, "start"
                else:
                    lx, anch = gx - 4.0, "end"
                routes.append(
                    EdgeRoute(eid, cls, frm, to, pts, label, lx, ly, anch)
                )
            else:
                # Cross-column sub→sub via gutters
                exit_x, exit_y = s1.x + s1.w, s1.y + s1.h * 0.5
                enter_x, enter_y = s2.x, s2.y + s2.h * 0.5
                gx1 = next_gutter_lane(_gutter_right_of(layout, s1.parent_id))
                gx2 = next_gutter_lane(_gutter_left_of(layout, s2.parent_id))
                pts = [
                    (exit_x, exit_y),
                    (gx1, exit_y),
                    (gx1, enter_y),
                    (gx2, enter_y),
                    (enter_x, enter_y),
                ]
                routes.append(
                    EdgeRoute(
                        eid, cls, frm, to, pts, label,
                        (gx1 + gx2) / 2.0, enter_y - 8.0, "middle",
                    )
                )
            continue

        # ---- Fallback orthogonal ----
        x1, y1 = a.get(frm, (PAD_X, PAD_Y))
        x2, y2 = a.get(to, (PAD_X + 40, PAD_Y))
        mx = (x1 + x2) / 2.0
        pts = [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
        routes.append(
            EdgeRoute(eid, cls, frm, to, pts, label, mx, (y1 + y2) / 2.0 - 4, "middle")
        )

    return routes


# ---------------------------------------------------------------------------
# Edge-label collision resolution (FIX-ROUND-3)
# ---------------------------------------------------------------------------


def edge_label_bbox(
    label: str, x: float, y: float, anchor: str = "middle"
) -> tuple[float, float, float, float]:
    """Axis-aligned extent of an edge label (matches PlacedText.extent metrics)."""
    tw = text_width(label, FS_EDGE_LABEL)
    if anchor == "middle":
        x0 = x - tw / 2.0
    elif anchor == "end":
        x0 = x - tw
    else:
        x0 = x
    y0 = y - FS_EDGE_LABEL * 0.85
    y1 = y + FS_EDGE_LABEL * 0.25
    return (x0, y0, x0 + tw, y1)


def _border_band_rects(
    r: Rect, thickness: float = 2.5
) -> list[tuple[float, float, float, float]]:
    """Thin AABB strips along a block perimeter (box-border collision targets)."""
    t = thickness
    return [
        (r.x, r.y, r.x2, r.y + t),  # top
        (r.x, r.y2 - t, r.x2, r.y2),  # bottom
        (r.x, r.y, r.x + t, r.y2),  # left
        (r.x2 - t, r.y, r.x2, r.y2),  # right
    ]


def body_text_obstacles_from_layout(
    layout: Layout,
) -> list[tuple[float, float, float, float]]:
    """Synthesize body-text AABBs from layout geometry (mirrors _render_from_layout).

    Used at layout time before PlacedText is populated, and as a fallback in
    geometry_self_check when texts are empty.
    """
    out: list[tuple[float, float, float, float]] = []
    for block in layout.blocks:
        tx = block.x + BLOCK_PAD
        ty = block.y + 16.0
        out.append(
            PlacedText(
                block.title, tx, ty, FS_BLOCK_TITLE, owner_id=block.id, role="body"
            ).extent()
        )
        budget = max(24.0, block.w - 2 * BLOCK_PAD - 4.0)
        ops_y = block.y + 30.0
        for line in wrap_to_width(block.operating_conditions, budget, FS_OPS):
            out.append(
                PlacedText(line, tx, ops_y, FS_OPS, owner_id=block.id, role="body").extent()
            )
            ops_y += OPS_LINE_H
        annot_y = ops_y + 4.0
        for ann in block.annotations:
            for line in wrap_to_width("· " + ann, budget, FS_ANNOT):
                out.append(
                    PlacedText(
                        line, tx, annot_y, FS_ANNOT, owner_id=block.id, role="body"
                    ).extent()
                )
                annot_y += ANNOT_LINE_H
        for sub in block.subs:
            stx = sub.x + SUB_PAD
            title_y = sub.y + SUB_TITLE_Y_OFF
            out.append(
                PlacedText(
                    sub.title, stx, title_y, FS_SUB_TITLE, owner_id=sub.id, role="body"
                ).extent()
            )
            ty2 = sub.y + SUB_HEADER_H
            sub_budget = _annot_text_width_budget(sub.w)
            if sub.operating_conditions:
                for line in wrap_to_width(sub.operating_conditions, sub_budget, FS_OPS):
                    out.append(
                        PlacedText(
                            line, stx, ty2, FS_OPS, owner_id=sub.id, role="body"
                        ).extent()
                    )
                    ty2 += OPS_LINE_H
                ty2 += 2.0
            if sub.chips:
                y1 = max(c.y + c.h for c in sub.chips)
                ay = y1 + 10.0
            else:
                ay = ty2 + 4.0
            for ann in sub.annotations:
                for line in wrap_to_width("· " + ann, sub_budget, FS_ANNOT):
                    out.append(
                        PlacedText(
                            line, stx, ay, FS_ANNOT, owner_id=sub.id, role="body"
                        ).extent()
                    )
                    ay += ANNOT_LINE_H
    return out


def label_collision_obstacles(
    layout: Layout,
) -> list[tuple[float, float, float, float]]:
    """Forbidden AABBs for edge labels: chips, sub interiors, block borders, body text."""
    obs: list[tuple[float, float, float, float]] = []
    for r in layout.obstacles.values():
        if r.kind == "chip":
            obs.append((r.x, r.y, r.x2, r.y2))
        elif r.kind == "sub":
            # Sub-box is content: chips + title/ops live here — labels stay outside
            obs.append((r.x, r.y, r.x2, r.y2))
        elif r.kind == "block":
            # Only the drawn border band; inter-sub gaps inside the block remain free
            obs.extend(_border_band_rects(r, thickness=2.5))
    # Prefer live PlacedText when present (post-render); else synthesize
    body = [t.extent() for t in layout.texts if t.role == "body"]
    if not body:
        body = body_text_obstacles_from_layout(layout)
    obs.extend(body)
    return obs


def _path_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _edge_label_candidates(
    edge: EdgeRoute, layout: Layout
) -> list[tuple[float, float, str]]:
    """Deterministic candidate (x, y, anchor) list near the edge path."""
    cands: list[tuple[float, float, str]] = [
        (edge.label_x, edge.label_y, edge.label_anchor)
    ]
    pts = edge.points
    if len(pts) >= 2:
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 1.0:
                continue
            nx, ny = -dy / length, dx / length  # unit normal
            for t in (0.2, 0.35, 0.5, 0.65, 0.8):
                mx = x1 + t * dx
                my = y1 + t * dy
                for side in (1.0, -1.0):
                    for off in (8.0, 12.0, 16.0, 22.0, 28.0, 36.0, 48.0, 60.0, 80.0):
                        lx = mx + side * nx * off
                        ly = my + side * ny * off
                        if abs(nx) >= abs(ny):
                            primary = "start" if side * nx > 0 else "end"
                        else:
                            primary = "middle"
                        cands.append((lx, ly, primary))
                        for a in ("start", "middle", "end"):
                            if a != primary:
                                cands.append((lx, ly, a))

    # Inter-sub gap strips only for blocks this edge touches (or path crosses)
    path_bb = _path_bbox(pts) if pts else (edge.label_x, edge.label_y, edge.label_x, edge.label_y)
    sub_parent = _sub_parent_map(layout)
    touch_blocks = {edge.frm, edge.to}
    if edge.frm in sub_parent:
        touch_blocks.add(sub_parent[edge.frm])
    if edge.to in sub_parent:
        touch_blocks.add(sub_parent[edge.to])
    for b in layout.blocks:
        # Path horizontally overlaps this column (spine labels sit in gutters beside it)
        near = b.id in touch_blocks or (
            path_bb[0] - 20 <= b.x + b.w and path_bb[2] + 20 >= b.x
            and path_bb[1] - 40 <= b.y + b.h and path_bb[3] + 40 >= b.y
        )
        if not near:
            continue
        for i in range(len(b.subs) - 1):
            s1, s2 = b.subs[i], b.subs[i + 1]
            gap_y0, gap_y1 = s1.y + s1.h, s2.y
            gap_h = gap_y1 - gap_y0
            if gap_h < 8.0:
                continue
            # Baseline so full glyph box stays inside the gap (pad 1px)
            # y0 = y - 0.85*FS >= gap_y0 + 1  →  y >= gap_y0 + 1 + 0.85*FS
            # y1 = y + 0.25*FS <= gap_y1 - 1  →  y <= gap_y1 - 1 - 0.25*FS
            y_lo = gap_y0 + 1.0 + FS_EDGE_LABEL * 0.85
            y_hi = gap_y1 - 1.0 - FS_EDGE_LABEL * 0.25
            if y_lo > y_hi:
                mid_y = (gap_y0 + gap_y1) * 0.5 + FS_EDGE_LABEL * 0.3
            else:
                mid_y = (y_lo + y_hi) * 0.5
            for frac, anch in (
                (0.12, "start"),
                (0.28, "start"),
                (0.45, "middle"),
                (0.55, "middle"),
                (0.72, "end"),
                (0.88, "end"),
            ):
                cands.append((s1.x + s1.w * frac, mid_y, anch))

    # Nearby gutters only (within path x-range ± one column gap)
    gx_lo, gx_hi = path_bb[0] - COL_GAP * 1.5, path_bb[2] + COL_GAP * 1.5
    near_gutters = [gx for gx in layout.gutters if gx_lo <= gx <= gx_hi]
    if not near_gutters:
        near_gutters = list(layout.gutters)
    sky_ys = (layout.sky_y - 4.0, layout.sky_y + 6.0, layout.content_y0 - 6.0)
    ret_ys = (layout.return_y - 6.0, layout.return_y + 10.0, layout.content_y1 + 12.0)
    path_mid_y = (path_bb[1] + path_bb[3]) * 0.5
    for gx in near_gutters:
        for ly in sky_ys + ret_ys + (edge.label_y, path_mid_y, path_mid_y - 12.0, path_mid_y + 12.0):
            cands.append((gx + 4.0, ly, "start"))
            cands.append((gx - 4.0, ly, "end"))
            cands.append((gx, ly, "middle"))
        # Horizontal sweep in sky lane near this gutter (wide labels)
        for dx in range(-60, 61, 10):
            cands.append((gx + dx, layout.sky_y - 4.0, "middle"))
            cands.append((gx + dx, layout.sky_y - 4.0, "start"))
            cands.append((gx + dx, layout.sky_y - 4.0, "end"))

    return cands


def resolve_edge_label_collisions(layout: Layout) -> None:
    """Nudge each edge label until its bbox is clear of content (or best-effort).

    Picks the clear candidate nearest the original router placement so labels
    stay associated with their edge. Mutates EdgeRoute label fields in place.
    """
    base_obs = label_collision_obstacles(layout)
    placed: list[tuple[float, float, float, float]] = []
    W = layout.width
    H = max(layout.height, layout.content_y1 + RETURN_LANE_H + LEGEND_H + PAD_Y)
    # Match geometry_self_check pad so resolve and check agree
    pad = 0.5

    def fits(
        bbox: tuple[float, float, float, float],
        obstacles: list[tuple[float, float, float, float]],
    ) -> bool:
        x0, y0, x1, y1 = bbox
        if x0 < 0.0 or y0 < 0.0 or x1 > W or y1 > H:
            return False
        return not any(_aabb_overlap(bbox, o, pad=pad) for o in obstacles)

    for edge in layout.edges:
        if not edge.label:
            continue
        obstacles = base_obs + placed
        # Path midpoint: secondary preference key (stay near the wire)
        pts = edge.points
        if pts:
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
        else:
            mx, my = edge.label_x, edge.label_y

        best: tuple[float, float, str, tuple[float, float, float, float]] | None = None
        best_score = float("inf")
        seen: set[tuple[float, float, str]] = set()
        for lx, ly, anch in _edge_label_candidates(edge, layout):
            key = (round(lx, 2), round(ly, 2), anch)
            if key in seen:
                continue
            seen.add(key)
            bbox = edge_label_bbox(edge.label, lx, ly, anch)
            if not fits(bbox, obstacles):
                continue
            # Prefer near original placement, then near path centroid
            d0 = math.hypot(lx - edge.label_x, ly - edge.label_y)
            d1 = math.hypot(lx - mx, ly - my)
            score = d0 + 0.25 * d1
            if score < best_score:
                best_score = score
                best = (lx, ly, anch, bbox)

        if best is None:
            # Last-resort grid near path x, in sky / return free bands
            x_center = mx
            for y in (
                layout.sky_y - 4.0,
                layout.sky_y + 8.0,
                layout.content_y0 - 8.0,
                layout.content_y1 + 14.0,
                layout.return_y + 10.0,
            ):
                for dx in range(-120, 121, 12):
                    for anch in ("middle", "start", "end"):
                        lx = x_center + dx
                        bbox = edge_label_bbox(edge.label, lx, y, anch)
                        if not fits(bbox, obstacles):
                            continue
                        d0 = math.hypot(lx - edge.label_x, y - edge.label_y)
                        if d0 < best_score:
                            best_score = d0
                            best = (lx, y, anch, bbox)

        if best is not None:
            edge.label_x, edge.label_y, edge.label_anchor = best[0], best[1], best[2]
            placed.append(best[3])
        # else: leave router default; geometry_self_check will report the collision


def _segment_intersects_rect_interior(
    p1: tuple[float, float],
    p2: tuple[float, float],
    rect: Rect,
    eps: float = GEO_EPS,
) -> bool:
    """True if the open segment (p1,p2) intersects the strict interior of rect.

    Axis-aligned segments only (our router emits orthogonal polylines).
    Endpoints exactly on the boundary do not count as interior hits.
    """
    x1, y1 = p1
    x2, y2 = p2
    # Normalize
    if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
        return rect.contains_point(x1, y1, eps=eps)

    # Sample-based with analytical clip for robustness
    # Expand interior
    ix0, iy0 = rect.x + eps, rect.y + eps
    ix1, iy1 = rect.x2 - eps, rect.y2 - eps
    if ix0 >= ix1 or iy0 >= iy1:
        return False

    # Vertical segment
    if abs(x1 - x2) < 1e-9:
        x = x1
        if x <= ix0 or x >= ix1:
            return False
        ya, yb = sorted([y1, y2])
        # Open segment: exclude pure-endpoint touch at boundary of interior span
        lo = max(ya, iy0)
        hi = min(yb, iy1)
        # Need positive-length overlap of interiors
        return hi - lo > eps

    # Horizontal segment
    if abs(y1 - y2) < 1e-9:
        y = y1
        if y <= iy0 or y >= iy1:
            return False
        xa, xb = sorted([x1, x2])
        lo = max(xa, ix0)
        hi = min(xb, ix1)
        return hi - lo > eps

    # Non-orthogonal: sample
    for t in (0.15, 0.35, 0.5, 0.65, 0.85):
        px = x1 + t * (x2 - x1)
        py = y1 + t * (y2 - y1)
        if rect.contains_point(px, py, eps=eps):
            return True
    return False


def _aabb_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 0.5,
) -> bool:
    """True if two axis-aligned boxes (x0,y0,x1,y1) overlap by more than pad px."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (
        ax1 <= bx0 + pad
        or bx1 <= ax0 + pad
        or ay1 <= by0 + pad
        or by1 <= ay0 + pad
    )


def geometry_self_check(layout: Layout) -> list[str]:
    """Return list of geometry errors (empty = pass).

    Checks:
      1. Edge path segments must not intersect any box/chip interior except
         the edge's endpoint boxes (from/to and their parent blocks for subs).
      2. Body text extents must stay inside their owner box.
      3. No tracked element (obstacles, edge points, edge labels, body text)
         may fall outside the canvas.
      4. Pairwise body-text vs body-text bounding-box overlap (title∩subtitle etc.).
      5. Edge-label bboxes must not overlap chips, sub interiors, block borders,
         body text, or other edge labels (FIX-ROUND-3).
    """
    errors: list[str] = []
    W, H = layout.width, layout.height

    # Obstacle sets for edge checks: prefer sub-boxes and chips (tight content);
    # also include top-level blocks so a diagonal through empty block body fails.
    content_obstacles = [
        r for r in layout.obstacles.values() if r.kind in ("sub", "chip", "block")
    ]

    sub_parent = _sub_parent_map(layout)

    def allowed_ids(frm: str, to: str) -> set[str]:
        allow = {frm, to}
        if frm in sub_parent:
            allow.add(sub_parent[frm])
        if to in sub_parent:
            allow.add(sub_parent[to])
        # Chips inside endpoint subs are also allowed near ports? No — edge should
        # only touch box boundary, never chip interior. Do NOT allow chips.
        return allow

    edge_label_boxes: list[tuple[EdgeRoute, tuple[float, float, float, float]]] = []

    for edge in layout.edges:
        allow = allowed_ids(edge.frm, edge.to)
        pts = edge.points
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            for rect in content_obstacles:
                if rect.id in allow:
                    continue
                # Parent block of an allowed sub: for segment checks against the
                # PARENT block we still allow (edge may travel inside parent only
                # when connecting its own subs — but we route via gutters so
                # parent should rarely be crossed). Exclude parent if it's an
                # endpoint's parent AND the other end is also in that parent.
                if rect.kind == "block" and rect.id in allow:
                    continue
                if _segment_intersects_rect_interior(p1, p2, rect):
                    errors.append(
                        f"edge {edge.edge_id!r} segment {p1}->{p2} intersects "
                        f"{rect.kind} {rect.id!r} interior"
                    )

        # Edge points on canvas
        for p in pts:
            if p[0] < -GEO_EPS or p[1] < -GEO_EPS or p[0] > W + GEO_EPS or p[1] > H + GEO_EPS:
                errors.append(
                    f"edge {edge.edge_id!r} point {p} outside canvas {W}x{H}"
                )

        if edge.label:
            bbox = edge_label_bbox(
                edge.label, edge.label_x, edge.label_y, edge.label_anchor
            )
            x0, y0, x1, y1 = bbox
            edge_label_boxes.append((edge, bbox))
            if x0 < -GEO_EPS or y0 < -GEO_EPS or x1 > W + GEO_EPS or y1 > H + GEO_EPS:
                errors.append(
                    f"edge label {edge.label!r} on {edge.edge_id!r} outside canvas "
                    f"(extent {x0:.1f}..{x1:.1f}, {y0:.1f}..{y1:.1f})"
                )

    # Text vs owner box
    owner_rects = {r.id: r for r in layout.obstacles.values() if r.kind in ("block", "sub")}
    for t in layout.texts:
        if t.role in ("edge_label", "legend", "title"):
            # canvas check only
            x0, y0, x1, y1 = t.extent()
            if x0 < -GEO_EPS or y0 < -GEO_EPS or x1 > W + GEO_EPS or y1 > H + GEO_EPS:
                errors.append(
                    f"text {t.text[:40]!r} ({t.role}) outside canvas "
                    f"(extent {x0:.1f}..{x1:.1f})"
                )
            continue
        if not t.owner_id:
            continue
        owner = owner_rects.get(t.owner_id)
        if owner is None:
            continue
        x0, y0, x1, y1 = t.extent()
        # Allow small pad; text must not exceed owner
        if x0 < owner.x - GEO_EPS or x1 > owner.x2 + GEO_EPS:
            errors.append(
                f"text {t.text[:50]!r} exceeds box {t.owner_id!r} horizontally "
                f"(text {x0:.1f}..{x1:.1f} vs box {owner.x:.1f}..{owner.x2:.1f})"
            )
        if y0 < owner.y - GEO_EPS or y1 > owner.y2 + GEO_EPS:
            errors.append(
                f"text {t.text[:50]!r} exceeds box {t.owner_id!r} vertically "
                f"(text {y0:.1f}..{y1:.1f} vs box {owner.y:.1f}..{owner.y2:.1f})"
            )

    # Obstacles on canvas
    for rect in layout.obstacles.values():
        if rect.x < -GEO_EPS or rect.y < -GEO_EPS or rect.x2 > W + GEO_EPS or rect.y2 > H + GEO_EPS:
            errors.append(
                f"{rect.kind} {rect.id!r} outside canvas "
                f"({rect.x:.1f},{rect.y:.1f} {rect.w:.1f}x{rect.h:.1f})"
            )

    # Pairwise body text ∩ body text (catches title-on-subtitle; round-1 missed this)
    body_texts = [t for t in layout.texts if t.role == "body"]
    for i, ta in enumerate(body_texts):
        ea = ta.extent()
        for tb in body_texts[i + 1 :]:
            eb = tb.extent()
            if _aabb_overlap(ea, eb, pad=0.5):
                errors.append(
                    f"text-overlap: {ta.text[:40]!r} ∩ {tb.text[:40]!r} "
                    f"(owners {ta.owner_id!r}/{tb.owner_id!r})"
                )

    # FIX-ROUND-3: edge-label ∩ chips / sub interiors / block borders / body text / labels
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
    border_rects = [
        (r.id, band)
        for r in layout.obstacles.values()
        if r.kind == "block"
        for band in _border_band_rects(r, thickness=2.5)
    ]
    body_exts = [t.extent() for t in body_texts] or body_text_obstacles_from_layout(layout)

    for edge, bbox in edge_label_boxes:
        for cr in chip_rects:
            if _aabb_overlap(bbox, cr, pad=0.5):
                errors.append(
                    f"edge-label-overlap: {edge.label!r} ({edge.edge_id}) ∩ chip "
                    f"at ({cr[0]:.0f},{cr[1]:.0f})"
                )
        for sid, sr in sub_rects:
            if _aabb_overlap(bbox, sr, pad=0.5):
                errors.append(
                    f"edge-label-overlap: {edge.label!r} ({edge.edge_id}) ∩ sub {sid!r}"
                )
        for bid, br in border_rects:
            if _aabb_overlap(bbox, br, pad=0.5):
                errors.append(
                    f"edge-label-overlap: {edge.label!r} ({edge.edge_id}) ∩ "
                    f"block-border {bid!r}"
                )
                break  # one border hit per label is enough signal
        for be in body_exts:
            if _aabb_overlap(bbox, be, pad=0.5):
                errors.append(
                    f"edge-label-overlap: {edge.label!r} ({edge.edge_id}) ∩ body-text "
                    f"at ({be[0]:.0f},{be[1]:.0f})"
                )
                break

    for i, (e1, b1) in enumerate(edge_label_boxes):
        for e2, b2 in edge_label_boxes[i + 1 :]:
            if _aabb_overlap(b1, b2, pad=0.5):
                errors.append(
                    f"edge-label-overlap: {e1.label!r} ({e1.edge_id}) ∩ "
                    f"{e2.label!r} ({e2.edge_id})"
                )

    return errors


# ---------------------------------------------------------------------------
# SVG emission
# ---------------------------------------------------------------------------


def _svg_header(w: float, h: float) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}" height="{h:.1f}" '
        f'viewBox="0 0 {w:.1f} {h:.1f}" role="img" '
        f'aria-label="Plant flowsheet block diagram">',
        "<defs>",
        f'<marker id="arrow-main" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">',
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{C_MAIN}"/>',
        "</marker>",
        f'<marker id="arrow-oxygen" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">',
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{C_OXYGEN}"/>',
        "</marker>",
        f'<marker id="arrow-return" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">',
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{C_RETURN}"/>',
        "</marker>",
        "<style><![CDATA[",
        f"  .bg {{ fill: {C_BG}; }}",
        f"  .block {{ fill: {C_BLOCK_FILL}; stroke: {C_BLOCK_STROKE}; stroke-width: 1.5; }}",
        f"  .subbox {{ fill: {C_SUB_FILL}; stroke: {C_SUB_STROKE}; stroke-width: 1; }}",
        f"  .title {{ fill: {C_TEXT}; font: 700 16px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .block-title {{ fill: {C_TEXT}; font: 700 12px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .block-ops {{ fill: {C_TEXT_MUTED}; font: 400 9px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .sub-title {{ fill: {C_TEXT}; font: 600 10px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .annot {{ fill: {C_TEXT_MUTED}; font: 400 8.5px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .chip-face {{ fill: {C_CHIP_FILL}; stroke: {C_CHIP_STROKE}; stroke-width: 1.2; }}",
        f"  .chip-face.conditional {{ stroke-dasharray: 3 2; }}",
        f"  .fill-level {{ fill: {C_FILL_LEVEL}; stroke: none; }}",
        f"  .chip-label {{ fill: {C_CHIP_TEXT}; font: 600 9px 'IBM Plex Mono', 'Menlo', monospace; "
        f"text-anchor: middle; dominant-baseline: central; pointer-events: none; }}",
        f"  .edge-main {{ fill: none; stroke: {C_MAIN}; stroke-width: 1.6; marker-end: url(#arrow-main); }}",
        f"  .edge-oxygen {{ fill: none; stroke: {C_OXYGEN}; stroke-width: 1.8; marker-end: url(#arrow-oxygen); }}",
        f"  .edge-reagent_return {{ fill: none; stroke: {C_RETURN}; stroke-width: 1.4; "
        f"stroke-dasharray: 5 3; marker-end: url(#arrow-return); }}",
        # Legend samples: same stroke colors, NO marker-end (avoids stray arrowheads)
        f"  .edge-sample-main {{ fill: none; stroke: {C_MAIN}; stroke-width: 1.6; }}",
        f"  .edge-sample-oxygen {{ fill: none; stroke: {C_OXYGEN}; stroke-width: 1.8; }}",
        f"  .edge-sample-return {{ fill: none; stroke: {C_RETURN}; stroke-width: 1.4; "
        f"stroke-dasharray: 5 3; }}",
        f"  .edge-label {{ fill: {C_TEXT_MUTED}; font: 400 8px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        f"  .legend-bg {{ fill: {C_LEGEND_BG}; stroke: {C_SUB_STROKE}; stroke-width: 1; }}",
        f"  .legend-label {{ fill: {C_TEXT}; font: 400 10px 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif; }}",
        "  .species-chip { /* --fill-fraction: 0..1 sets mass fraction in this bin */ }",
        "]]></style>",
        "</defs>",
    ]


def _render_chip(chip: PlacedChip, clip_idx: int) -> list[str]:
    lines: list[str] = []
    clip_id = f"chip-clip-{clip_idx}"
    frac = max(0.0, min(1.0, chip.fill_fraction))
    fill_h = chip.h * frac
    fill_y = chip.y + chip.h - fill_h
    cond_class = " conditional" if chip.status == "conditional" else ""
    title = chip.species
    if chip.condition_note:
        title = f"{chip.species} (conditional): {chip.condition_note}"
    lines.append(
        f'<g class="species-chip" data-species="{esc(chip.species)}" '
        f'data-bin="{esc(chip.bin_id)}" data-status="{esc(chip.status)}" '
        f'style="--fill-fraction: {frac:.4f}; --chip-h: {chip.h:.1f}px">'
    )
    lines.append(f"<title>{esc(title)}</title>")
    lines.append(
        f'<clipPath id="{clip_id}">'
        f'<rect x="{chip.x:.2f}" y="{chip.y:.2f}" width="{chip.w:.2f}" '
        f'height="{chip.h:.2f}" rx="{CHIP_CORNER_R}" ry="{CHIP_CORNER_R}"/>'
        f"</clipPath>"
    )
    lines.append(
        f'<rect class="chip-face{cond_class}" x="{chip.x:.2f}" y="{chip.y:.2f}" '
        f'width="{chip.w:.2f}" height="{chip.h:.2f}" '
        f'rx="{CHIP_CORNER_R}" ry="{CHIP_CORNER_R}"/>'
    )
    lines.append(
        f'<rect class="fill-level" clip-path="url(#{clip_id})" '
        f'x="{chip.x:.2f}" y="{fill_y:.2f}" width="{chip.w:.2f}" '
        f'height="{fill_h:.2f}" data-fill-fraction="{frac:.4f}"/>'
    )
    lines.append(
        f'<text class="chip-label" x="{chip.x + chip.w / 2:.2f}" '
        f'y="{chip.y + chip.h / 2:.2f}">{esc(chip.species)}</text>'
    )
    lines.append("</g>")
    return lines


def _path_d(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    parts = [f"M {points[0][0]:.2f},{points[0][1]:.2f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.2f},{y:.2f}")
    return " ".join(parts)


def render_with_layout(
    data: dict[str, Any], demo_fill: float = 0.0
) -> tuple[str, Layout]:
    """Render SVG and return (svg, layout) with texts populated for self-check."""
    layout = compute_layout(data, demo_fill=demo_fill)
    # render_svg recomputes layout — keep single source by inlining once:
    # Call render path that uses existing layout.
    svg = _render_from_layout(data, layout)
    return svg, layout


def _render_from_layout(data: dict[str, Any], layout: Layout) -> str:
    """Emit SVG from an already-computed layout (single layout pass)."""
    # Temporarily re-bind via mutating path used by render_svg body.
    # Simplest: set demo chips already in layout; rebuild SVG using layout.
    texts: list[PlacedText] = []
    parts = _svg_header(layout.width, layout.height)
    parts.append(f'<rect class="bg" width="{layout.width:.1f}" height="{layout.height:.1f}"/>')
    parts.append(
        f'<text class="title" x="{PAD_X:.1f}" y="{PAD_Y + 18:.1f}">{esc(layout.title)}</text>'
    )
    texts.append(PlacedText(layout.title, PAD_X, PAD_Y + 18, FS_TITLE, role="title"))
    parts.append(
        f'<text class="block-ops" x="{PAD_X:.1f}" y="{PAD_Y + 32:.1f}">'
        f"v7 reviewed species map · solid chip = reviewed · dashed chip = conditional · "
        f"orange = oxygen · dashed edge = reagent/oxide return</text>"
    )

    clip_idx = 0
    for block in layout.blocks:
        parts.append(
            f'<g class="block-group" data-block="{esc(block.id)}">'
            f'<rect class="block" x="{block.x:.2f}" y="{block.y:.2f}" '
            f'width="{block.w:.2f}" height="{block.h:.2f}" '
            f'rx="{CORNER_R}" ry="{CORNER_R}"/>'
        )
        tx = block.x + BLOCK_PAD
        ty = block.y + 16.0
        parts.append(
            f'<text class="block-title" x="{tx:.2f}" y="{ty:.2f}">{esc(block.title)}</text>'
        )
        texts.append(
            PlacedText(block.title, tx, ty, FS_BLOCK_TITLE, owner_id=block.id, role="body")
        )
        budget = max(24.0, block.w - 2 * BLOCK_PAD - 4.0)
        ops_y = block.y + 30.0
        for line in wrap_to_width(block.operating_conditions, budget, FS_OPS):
            parts.append(
                f'<text class="block-ops" x="{tx:.2f}" y="{ops_y:.2f}">{esc(line)}</text>'
            )
            texts.append(PlacedText(line, tx, ops_y, FS_OPS, owner_id=block.id, role="body"))
            ops_y += OPS_LINE_H
        annot_y = ops_y + 4.0
        for ann in block.annotations:
            for line in wrap_to_width("· " + ann, budget, FS_ANNOT):
                parts.append(
                    f'<text class="annot" x="{tx:.2f}" y="{annot_y:.2f}">{esc(line)}</text>'
                )
                texts.append(
                    PlacedText(line, tx, annot_y, FS_ANNOT, owner_id=block.id, role="body")
                )
                annot_y += ANNOT_LINE_H
        for sub in block.subs:
            parts.append(
                f'<g class="subbox-group" data-bin="{esc(sub.id)}">'
                f'<rect class="subbox" x="{sub.x:.2f}" y="{sub.y:.2f}" '
                f'width="{sub.w:.2f}" height="{sub.h:.2f}" '
                f'rx="{SUB_CORNER_R}" ry="{SUB_CORNER_R}"/>'
            )
            stx = sub.x + SUB_PAD
            title_y = sub.y + SUB_TITLE_Y_OFF
            parts.append(
                f'<text class="sub-title" x="{stx:.2f}" '
                f'y="{title_y:.2f}">{esc(sub.title)}</text>'
            )
            texts.append(
                PlacedText(sub.title, stx, title_y, FS_SUB_TITLE, owner_id=sub.id, role="body")
            )
            # Ops/subtitle stacked BELOW the title band (not on the same baseline)
            ty2 = sub.y + SUB_HEADER_H
            sub_budget = _annot_text_width_budget(sub.w)
            if sub.operating_conditions:
                for line in wrap_to_width(sub.operating_conditions, sub_budget, FS_OPS):
                    parts.append(
                        f'<text class="annot" x="{stx:.2f}" y="{ty2:.2f}">{esc(line)}</text>'
                    )
                    texts.append(
                        PlacedText(line, stx, ty2, FS_OPS, owner_id=sub.id, role="body")
                    )
                    ty2 += OPS_LINE_H
                ty2 += 2.0
            for chip in sub.chips:
                parts.extend(_render_chip(chip, clip_idx))
                clip_idx += 1
            if sub.chips:
                y1 = max(c.y + c.h for c in sub.chips)
                ay = y1 + 10
            else:
                ay = ty2 + 4
            for ann in sub.annotations:
                for line in wrap_to_width("· " + ann, sub_budget, FS_ANNOT):
                    parts.append(
                        f'<text class="annot" x="{stx:.2f}" y="{ay:.2f}">{esc(line)}</text>'
                    )
                    texts.append(
                        PlacedText(line, stx, ay, FS_ANNOT, owner_id=sub.id, role="body")
                    )
                    ay += ANNOT_LINE_H
            parts.append("</g>")
        parts.append("</g>")

    parts.append('<g class="edges">')
    for edge in layout.edges:
        d = _path_d(edge.points)
        parts.append(f'<path class="edge-{edge.cls}" d="{d}" data-edge="{esc(edge.edge_id)}"/>')
        if edge.label:
            parts.append(
                f'<text class="edge-label" x="{edge.label_x:.2f}" y="{edge.label_y:.2f}" '
                f'text-anchor="{edge.label_anchor}">{esc(edge.label)}</text>'
            )
            texts.append(
                PlacedText(
                    edge.label,
                    edge.label_x,
                    edge.label_y,
                    FS_EDGE_LABEL,
                    anchor=edge.label_anchor,
                    role="edge_label",
                )
            )
    parts.append("</g>")

    ly = layout.height - PAD_Y - LEGEND_H + 4
    parts.append(
        f'<rect class="legend-bg" x="{PAD_X:.1f}" y="{ly:.1f}" '
        f'width="{layout.width - 2 * PAD_X:.1f}" height="{LEGEND_H - 8:.1f}" rx="6" ry="6"/>'
    )
    items = data.get("legend") or []
    lx = PAD_X + 14
    item_y = ly + 22
    for item in items:
        style = item.get("style") or item.get("key")
        label = item.get("label") or ""
        if style == "chip_reviewed":
            parts.append(
                f'<rect class="chip-face" x="{lx:.1f}" y="{item_y - 10:.1f}" '
                f'width="36" height="14" rx="3" ry="3"/>'
            )
            lx += 42
        elif style == "chip_conditional":
            parts.append(
                f'<rect class="chip-face conditional" x="{lx:.1f}" y="{item_y - 10:.1f}" '
                f'width="36" height="14" rx="3" ry="3"/>'
            )
            lx += 42
        elif style == "edge_main":
            parts.append(
                f'<line x1="{lx:.1f}" y1="{item_y - 3:.1f}" x2="{lx + 28:.1f}" '
                f'y2="{item_y - 3:.1f}" class="edge-sample-main"/>'
            )
            lx += 34
        elif style == "edge_oxygen":
            parts.append(
                f'<line x1="{lx:.1f}" y1="{item_y - 3:.1f}" x2="{lx + 28:.1f}" '
                f'y2="{item_y - 3:.1f}" class="edge-sample-oxygen"/>'
            )
            lx += 34
        elif style == "edge_reagent_return":
            parts.append(
                f'<line x1="{lx:.1f}" y1="{item_y - 3:.1f}" x2="{lx + 28:.1f}" '
                f'y2="{item_y - 3:.1f}" class="edge-sample-return"/>'
            )
            lx += 34
        parts.append(
            f'<text class="legend-label" x="{lx:.1f}" y="{item_y:.1f}">{esc(label)}</text>'
        )
        texts.append(PlacedText(label, lx, item_y, FS_LEGEND, role="legend"))
        lx += min(280, 7 * len(label) + 24)

    layout.texts = texts
    parts.append("</svg>")
    parts.append("")
    return "\n".join(parts)


def render_svg(data: dict[str, Any], demo_fill: float = 0.0) -> str:
    layout = compute_layout(data, demo_fill=demo_fill)
    return _render_from_layout(data, layout)


# Keep _wrap alias for any external callers / tests
def _wrap(text: str, max_chars: int) -> list[str]:
    """Legacy char-budget wrap (tests may not use it). Prefer wrap_to_width."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= max_chars:
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def write_svg(data: dict[str, Any], out_path: Path, demo_fill: float = 0.0) -> str:
    svg = render_svg(data, demo_fill=demo_fill)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    return svg


def self_check(data: dict[str, Any]) -> int:
    """Return process exit code. Includes geometry self-check (FIX-ROUND-1)."""
    errs = validate_schema(data)
    if errs:
        print("SCHEMA: FAIL")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("SCHEMA: PASS")

    chips = species_index(data)
    print(f"SPECIES CHIPS: {len(chips)} unique")

    lint = lint_against_trace_elements(data)
    print(lint.report_text())
    if not lint.ok:
        return 1

    layout = compute_layout(data, demo_fill=0.0)
    svg_a = _render_from_layout(data, layout)
    geo_errs = geometry_self_check(layout)
    if geo_errs:
        print(f"GEOMETRY: FAIL ({len(geo_errs)} issues)")
        for e in geo_errs[:40]:
            print(f"  - {e}")
        if len(geo_errs) > 40:
            print(f"  ... +{len(geo_errs) - 40} more")
        return 1
    print(
        "GEOMETRY: PASS (edges clear of content, text in boxes, "
        "no text∩text, canvas bounds)"
    )

    # Full annotation audit: every annotation string must appear (possibly wrapped
    # across lines) in the SVG as contiguous word sequence.
    missing = _annotation_audit(data, svg_a)
    if missing:
        print(f"ANNOTATION-AUDIT: FAIL ({len(missing)} missing)")
        for m in missing:
            print(f"  - {m}")
        return 1
    print("ANNOTATION-AUDIT: PASS (all YAML annotation strings present in SVG)")

    b = render_svg(data, demo_fill=0.0)
    if svg_a != b:
        print("DETERMINISM: FAIL — two runs differ")
        return 1
    print(f"DETERMINISM: PASS (sha256 {hashlib.sha256(svg_a.encode()).hexdigest()[:16]})")

    demo = render_svg(data, demo_fill=0.4)
    if demo == svg_a:
        print("DEMO-FILL: FAIL — demo output identical to zero-fill")
        return 1

    def strip_fill(s: str) -> str:
        s = re.sub(r'<rect class="fill-level"[^>]*/>', "<FILL/>", s)
        s = re.sub(r"--fill-fraction: [0-9.]+", "--fill-fraction: X", s)
        return s

    if strip_fill(svg_a) != strip_fill(demo):
        print("DEMO-FILL: FAIL — changes beyond fill-level / --fill-fraction")
        return 1
    print("DEMO-FILL: PASS (only fill-level / --fill-fraction differ)")
    return 0


def _annotation_audit(data: dict[str, Any], svg: str) -> list[str]:
    """Every annotation / ops string from YAML must appear fully (words present in order)."""
    missing: list[str] = []
    # Collapse SVG whitespace for search
    flat = re.sub(r"\s+", " ", svg)

    def present(s: str) -> bool:
        # All words of s must appear in order in flat (handles line-wrap mid-phrase)
        words = s.split()
        if not words:
            return True
        # Try exact substring first (common case)
        if esc(s) in flat or s in flat:
            return True
        # Word-order search on escaped form
        pos = 0
        for w in words:
            ew = esc(w)
            i = flat.find(ew, pos)
            if i < 0:
                # try raw
                i = flat.find(w, pos)
            if i < 0:
                return False
            pos = i + len(w)
        return True

    for block in data.get("blocks") or []:
        for ann in block.get("annotations") or []:
            if not present(ann):
                missing.append(f"block {block['id']}: {ann!r}")
        ops = block.get("operating_conditions")
        if ops and not present(ops):
            missing.append(f"block {block['id']} ops: {ops!r}")
        for sub in block.get("sub_boxes") or []:
            for ann in sub.get("annotations") or []:
                if not present(ann):
                    missing.append(f"sub {sub['id']}: {ann!r}")
            sops = sub.get("operating_conditions")
            if sops and not present(sops):
                missing.append(f"sub {sub['id']} ops: {sops!r}")
    return missing


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render plant flowsheet SVG")
    p.add_argument("--yaml", type=Path, default=DEFAULT_YAML, help="flowsheet YAML path")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output SVG path")
    p.add_argument(
        "--demo-fill",
        type=float,
        default=0.0,
        help="fill all chips to this fraction (0..1) to demo UI hooks",
    )
    p.add_argument("--lint", action="store_true", help="run drift lint only")
    p.add_argument("--self-check", action="store_true", help="schema + geometry + determinism")
    args = p.parse_args(argv)

    data = load_flowsheet(args.yaml)

    if args.lint:
        result = lint_against_trace_elements(data)
        print(result.report_text())
        return 0 if result.ok else 1

    if args.self_check:
        return self_check(data)

    errs = validate_schema(data)
    if errs:
        for e in errs:
            print(f"schema error: {e}", file=sys.stderr)
        return 1

    # Geometry hard-fail on normal render too (catch layout regressions early)
    layout = compute_layout(data, demo_fill=args.demo_fill)
    geo_errs = geometry_self_check(layout)
    # Populate texts via render
    svg = _render_from_layout(data, layout)
    geo_errs = geometry_self_check(layout)
    if geo_errs:
        for e in geo_errs[:20]:
            print(f"geometry error: {e}", file=sys.stderr)
        if len(geo_errs) > 20:
            print(f"... +{len(geo_errs) - 20} more", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print(f"Wrote {args.out} ({len(svg)} bytes, demo_fill={args.demo_fill})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
