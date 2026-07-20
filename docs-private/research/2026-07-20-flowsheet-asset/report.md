# Flowsheet asset pipeline (t-391) — report

**Branch:** `flowsheet-asset`  
**Status:** READY (phase 1b membership lock + bin admission)

## TL;DR

1. **Phase 1b:** membership lock (`map_version=v8`, `locked: true`) + pinned sha256 of the membership set only; layout/annotation edits do not move the hash.
2. **Bin admission predicates** on every sub-box; `--lint` evaluates PASS/FAIL/UNKNOWN per chip (36 PASS / 0 FAIL / 42 UNKNOWN today — honest until `trace_elements.yaml` lands).
3. Live majors wired now: rump BeO/ZrO2/HfO2/Sc2O3, ferroalloy Fe/Ni, alkali Na/K, C6/Ca/O2/Mg — contradiction fixture proves FAIL.
4. Per-chip `review: {map, finding?}` from v7 map findings; v8 delta chips (`CO-CH4-organics`, `CO2-CO`, `P2-PO`) tagged `map: v8`.
5. Focused tests: **29 passed** (20 prior + 9 lock/admission); SVGs **byte-identical** (no visual elements added).
6. Commit: `feat(flowsheet): membership lock + bin admission predicates`.

---

## Null hypothesis — reuse findings

| Area | Finding |
|------|---------|
| `scripts/` | Process/grid/recipe utilities only; **no** diagram/SVG/mermaid/graphviz flowsheet renderer. |
| `web/static/` | Simulator charts/controls CSS/JS; report-viewer builds small chart SVGs only (not plant topology). |
| `docs/` | Process/architecture markdown; no plant block-diagram asset. |
| `data/` | Species catalog, feedstocks, etc.; **no** `trace_elements.yaml` on base `ea9de4f`. |
| Prior SVG | Spec mentions a MAIN-session SVG; not present as a repo file on this worktree. |

**Conclusion:** greenfield pipeline is correct. Renderer is stdlib + PyYAML (project `.venv`).

---

## Deliverables

| Path | Role |
|------|------|
| `data/flowsheet.yaml` | Canonical graph (schema v1, inline-documented) |
| `scripts/render_flowsheet.py` | Procedural SVG renderer + lint + self-check |
| `tests/test_flowsheet_asset.py` | Focused asset tests |
| `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet.svg` | Default render (fill 0.0) |
| `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet-demo-fill.svg` | Demo fill 0.4 |
| this report | Closeout |

PNG skipped: no `rsvg-convert` / `cairosvg` / ImageMagick without new deps (qlmanage thumbnail only for local visual check).

---

## Schema (summary)

See full contract at top of `data/flowsheet.yaml`.

- **blocks** (role `column` \| `terminal`): title, operating_conditions, annotations, nested **sub_boxes**.
- **sub_boxes** (= bins): species chips `{symbol_or_group, status: reviewed|conditional, condition_note?}`; optional future `equipment_tag`.
- **edges**: `{id?, from, to, class: main|oxygen|reagent_return, label?, stream_tag?, phase?}`.
- **legend**, **aggregates** (member → group chip for honest lint), **notes**.
- External anchors `feed` / `sky` are layout-only (not blocks).

### Uniqueness rule

Each species **symbol appears as exactly one chip** (primary home). v7 dual listings (e.g. Li on alkali+VMT, P on cryo+Fe, residual Nb/Ta/Sr/Ba on calciothermic) are expressed via `condition_note` / box annotations, not second chips — matches “conditional is not a second default home.”

| Dual-path species | Primary chip home |
|-------------------|-------------------|
| Li | alkali_cyclone (conditional) |
| Mn, P, Cr | ferroalloy_tap (conditional) |
| Ag, Sn, Ge, Cu | volatile_metal_trap (conditional) |
| Nb, Ta | c6_magnesiothermic (reviewed) |
| Sr, Ba | ca_crown (reviewed) |

Impurity vectors on Mg crown / C6 are **annotations**, not chips (avoids false double homes for Fe, Si, …).

---

## Layout (v3 + v7)

```
EARLY BAKE·CLEANUP | BERNOULLI·OVERHEAD GAS | Mg DOME | Ca DOME | RUMP
  VMT + CRYO           alkali / Fe / SiO / LOX   Mg crown+C6  Ca crown+calc  residue
```

- Fixed 4-column + terminal-rump hand layout (not graphviz).
- Column heights equalized; sub-box heights content-driven; chips width-adaptive.
- Edge classes: main (melt spine), oxygen (orange), reagent_return (dashed).
- Parent→nested-sub offtakes are containment (not drawn as interior arrows).

---

## UI hooks (web integration seam)

Each chip:

```xml
<g class="species-chip" data-species="Zn" data-bin="volatile_metal_trap"
   data-status="reviewed|conditional"
   style="--fill-fraction: 0; --chip-h: 18px">
  <title>…</title>
  <clipPath id="chip-clip-N">…</clipPath>
  <rect class="chip-face[ conditional]"/>
  <rect class="fill-level" data-fill-fraction="0" …/>
  <text class="chip-label">Zn</text>
</g>
```

**Contract:** set one CSS var `--fill-fraction` (0..1) = fraction of that species’ initial charge now in this bin; static SVG also materializes height/y on `.fill-level`.  
**Demo:** `scripts/render_flowsheet.py --demo-fill 0.4`.

---

## Drift lint

```
$ .venv/bin/python scripts/render_flowsheet.py --lint
LINT: SKIPPED — data/trace_elements.yaml not present on this base.
```

When `data/trace_elements.yaml` lands, lint will:

1. Require every **routed** trace species to resolve to exactly one flowsheet chip (direct or via `aggregates.members`).
2. Check conditionality consistency when classification is present.
3. Flag orphans both directions (with process/aggregate chips allowed without 1:1 route rows).

---

## Tests run

```
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
# 11 passed
.venv/bin/python scripts/render_flowsheet.py --self-check
# SCHEMA PASS · DETERMINISM PASS · DEMO-FILL PASS · LINT SKIPPED
```

Repo-wide suite **not** run (controller owns machine).

---

## Layout verification (visual)

Method: render SVG → qlmanage thumbnail + geometric overflow check (chip rects vs sub-box bounds).

| Check | Result |
|-------|--------|
| Chip overflow outside sub-box | **0** after width-adaptive layout |
| All 5 top blocks present | yes |
| All 11 bins present | yes |
| 76 species chips | yes |
| Conditional → dashed stroke | yes (`chip-face conditional` + CSS dasharray) |
| Oxygen edges orange | yes |
| Reagent returns dashed | yes |
| Legend row | yes |

**Honest remaining layout notes (non-blocking):**

1. **Dense bins** (VMT 19 chips, ferroalloy 16) pack tightly; annotation text is truncated to one wrapped line — full notes live in YAML.
2. **Column headers** with long ops strings wrap to 2 lines; some ops text is dense but legible at SVG native size.
3. **O₂ lance** (cistern → early bake) routes up the right edge of the Bernoulli column then across the top — cleaner than a mid-column vertical, still crosses the top of the sheet.
4. **qlmanage** produces a square thumbnail that letterboxes the landscape SVG; use the SVG itself (viewBox `1560×~755`) for faithful viewing.
5. Process-chain edges Mg→C6 / Ca→calciothermic are short internal connectors; melt spine between top-level blocks is the primary left→right read.

No remaining chip/box collisions found by geometry audit.

---

## Self-review

### Lens 1 — Schema completeness vs v7 map

- Every v7 bin present with reviewed + conditional chips (primary-home uniqueness).
- Operating conditions strings and mode-fork captions on early bake / Bernoulli / domes / rump.
- Oxygen + reagent/oxide returns present.
- Aggregates document group chips (salts, organics, glass, REE, unreduced-residuals) for future lint honesty.
- Calciothermic residual `{Nb,Ta,Sr,Ba}` not re-chipped (would violate uniqueness); residual semantics in annotations + aggregate notes.

### Lens 2 — Renderer determinism

- Pure function of YAML + `demo_fill`; two runs byte-identical (sha256 stable for a given input).
- No timestamps, random IDs, or filesystem order in SVG.
- `--demo-fill` changes only `.fill-level` geometry and `--fill-fraction` style values (tested).

### Null hypothesis (reused tooling)

Confirmed absent; no wasted adapter layer.

---

## Follow-up seams (registered, not built)

1. **PFD mode** — stream numbers (`edges[].stream_tag` / `id`), equipment tags, stream table under diagram.
2. **Wire `trace_elements.yaml`** when it lands; re-run `--lint` and fix any real orphans.
3. **Web report card** — bind `data-species`/`data-bin` fills from rev5 `yield_disposition` (spec data-binding section).
4. **Dormant greying** for Mg/Ca phases until ballistic regime live.
5. Optional PNG export if a no-new-dep converter becomes available in CI.

---

## Commands (reproduce)

```bash
.venv/bin/python scripts/render_flowsheet.py
.venv/bin/python scripts/render_flowsheet.py --demo-fill 0.4 \
  --out docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet-demo-fill.svg
.venv/bin/python scripts/render_flowsheet.py --lint
.venv/bin/python scripts/render_flowsheet.py --self-check
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
```

---

## FIX-ROUND-1 (layout defects)

Controller visual inspection of `flowsheet.svg` / demo-fill found five defect classes. Fixed in
`scripts/render_flowsheet.py` with **general layout logic** (no per-item coordinate nudges).
`data/flowsheet.yaml` not modified — routing metadata not required.

### Per-defect fixes

| # | Defect | Fix |
|---|--------|-----|
| 1 | **Edge routing through content** (O₂→sky verticals, O₂-lance, Ca dose→calciothermic, MgO/CaO re-bake returns) | Orthogonal **gutter-lane router**: vertical segments only in inter-column gutters / left feed lane / sky & return lanes. Subs exit at rim (right/top/bottom), never mid-box diagonals. Process chains (Mg dose, Ca dose) are short verticals in the **gap between stacked subs** only. |
| 2 | **Text collision** (Ca CROWN: “alkaline-earth…” + “O₂ ↑ sky…” superimposed) | Root cause: sky oxygen edge labels were placed on the mid-vertical through column centers (on top of sub ops text). Labels now sit **beside the gutter vertical** / in the sky lane — separate from body text. |
| 3 | **Truncated annotations** (mid-sentence clip at box edge) | Replaced `[:1]` single-line wrap with **full `wrap_to_width`** (px budget from box width). Sub/block **heights grow** with wrapped line count. Annotation audit requires every YAML annotation/ops string present in SVG. |
| 4 | **Clipped edge labels** (regolith feed @ x=0; melt spine; swap-domes vs body) | `FEED_LANE_W` left margin; wider `COL_GAP` gutters; edge labels anchored in gutters/lanes; canvas pad so extents stay inside viewBox. |
| 5 | **Vertical balance** (Mg/Ca dead space) | Columns **size to content** and **top-align** (no forced `max_col_h` equalization). Bernoulli stays tall; Mg/Ca/rump shrink. |

### Self-check inventory

| Check | Where | Failure condition |
|-------|-------|-------------------|
| `geometry_self_check(layout)` | `scripts/render_flowsheet.py` | Edge segment intersects box/chip **interior** (excluding endpoint boxes + their parents); body text extent outside owner box; any obstacle / edge point / edge label outside canvas |
| Annotation audit | `_annotation_audit` | Any block/sub annotation or ops string from YAML missing (as ordered words) in SVG |
| Wired into CLI | `--self-check` | GEOMETRY + ANNOTATION-AUDIT must PASS |
| Wired into tests | `tests/test_flowsheet_asset.py` | `test_geometry_self_check_passes`, `test_annotation_strings_render_in_full`, `test_columns_top_align_content_height`, `test_oxygen_and_return_edges_use_gutters` |
| Normal render | `main()` | Geometry errors → non-zero exit (hard fail) |

### Tests / renders (this round)

```
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
# 15 passed
.venv/bin/python scripts/render_flowsheet.py --self-check
# SCHEMA · GEOMETRY · ANNOTATION-AUDIT · DETERMINISM · DEMO-FILL PASS; LINT SKIPPED
```

Re-rendered:

- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet.svg`
- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet-demo-fill.svg`

### Layout numbers (post-fix)

- Canvas ≈ 1620 × 829 (was 1560 × 765)
- Column heights (top-aligned): early-bake ~501, Bernoulli ~593, Mg ~336, Ca ~351, rump ~267
- Drawn edges: 13 (containment offtakes still skipped)
- Gutters: left feed + 4 inter-column + right margin lane x positions

---

## FIX-ROUND-2 (layout + v8 content delta)

Controller visual inspection after round 1 found four defect classes plus an owner-directed
content delta (A–D) that arrived after round 1 closed. Touched:
`scripts/render_flowsheet.py`, `data/flowsheet.yaml`, `tests/test_flowsheet_asset.py`.

### Per-defect fixes

| # | Defect | Fix |
|---|--------|-----|
| 1 | **Sub-box title ∩ subtitle** (same baseline; "ALKALI CYCLONE" over "~1150 °C…") | Title at `SUB_TITLE_Y_OFF=14`; ops start at `SUB_HEADER_H=28` (was 18). Height + chip_y0 use the same stack. |
| 2 | **Bottom return labels clip** ("CaO → re-bake" half-cut) | Return lane grows with `#reagent_return` edges; post-route canvas height = max(edge points, label bottoms) + legend + pad. |
| 3 | **Duplicate mid-span O₂ arrowheads** on top lane | Root cause: sky vents landed with horizontal segments on the lance sky-transit y, so their `marker-end` sat mid-lance. Sky edges now end with an **upward vertical stub** above the transit corridor. Legend samples use marker-free CSS classes. |
| 4 | **Self-check missed text∩text** | `geometry_self_check` pairwise body-text AABB via `_aabb_overlap`; tests `test_text_vs_text_overlap_self_check` + `test_sub_title_and_ops_are_stacked`. |

### Content delta (v8)

| ID | Change |
|----|--------|
| A | CRYO: remove bare conditional `C`; add `CO-CH4-organics` (pyrolysis gases; elemental C does not volatilize &lt;900 °C). N unchanged. |
| B | NEW Bernoulli sub-box `gas_conditioning` between SiO baffles and terminal: conditional chips `CO2-CO`, `P2-PO` + cold-trap annotation. |
| C | Terminal recast as **O₂ PUMP → FROST CISTERN** with selective-extraction / buffer-never-liquefied annotation. |
| D | New dashed edge `e_buffer_recirc`: `lox_cistern` → `bernoulli_overhead` (warm N₂/Ar recirculation), gutter-routed. |

Authority: brief A–D text (spec.md on this worktree lacks a written v8 section; delta applied from dispatch brief).

### Self-check inventory (additive)

| Check | Failure condition |
|-------|-------------------|
| text∩text | Any two `role=body` PlacedText extents overlap by &gt;0.5 px |
| sky oxygen end | Final segment vertical and upward (test) |
| canvas pad | Edge labels / return points inside viewBox |

### Tests / renders (this round)

```
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
# 19 passed
.venv/bin/python scripts/render_flowsheet.py --self-check
# SCHEMA · GEOMETRY · ANNOTATION-AUDIT · DETERMINISM · DEMO-FILL PASS; LINT SKIPPED
```

Re-rendered:

- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet.svg`
- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet-demo-fill.svg`

### Layout numbers (post round 2)

- Canvas ≈ 1620 × 1114 (taller: gas-conditioning sub + frost-cistern annot + 4 return labels)
- Sub title/ops example (alkali): title y=209, ops y=223 (14 px stack gap)
- Species chips: 78 unique
- Drawn edges include buffer recirculation; sky O₂ ends as upward stubs

**Staged, not committed** (controller owns commit).

---

## FIX-ROUND-3 (edge-label collisions)

Controller visual after round 2 found one remaining defect class: **edge labels drawn over chips / box header text / box borders**.

Instances (pre-fix):
- `Ca dose → calciothermic` across the Sc2O3 rump chip
- `O₂ ↑ sky (part gettered)` over Ca DOME / RUMP header text
- `swap domes · raise T` sitting on the Ca DOME box edge

Touched: `scripts/render_flowsheet.py`, `tests/test_flowsheet_asset.py` only (no YAML; rump oxide census unchanged).

### Fix (general, not per-label)

| Piece | Behavior |
|-------|----------|
| Initial router hints | Process-chain labels sit in the inter-sub gap with glyph box inside the gap; wide labels use gutter **end**-anchor (grow left). Sky O₂ labels pin to the sky lane with end-anchor. Wide melt-spine labels lift above block tops. |
| `resolve_edge_label_collisions` | Post-route: collect forbidden AABBs (chips, sub interiors, block border bands, body text); score candidate placements near the edge path (along-path normal offsets, inter-sub gaps, nearby gutters, sky/return lanes); pick nearest clear candidate. |
| `geometry_self_check` | Additive: each edge-label bbox must not overlap chips, subs, block borders, body text, or other edge labels (`edge-label-overlap: …`). |
| Test | `test_edge_label_no_content_collision` + aggregate gate in `test_geometry_self_check_passes`. |

### Self-check inventory (additive)

| Check | Failure condition |
|-------|-------------------|
| edge-label∩chip | Label AABB overlaps a species chip |
| edge-label∩sub | Label AABB overlaps a sub-box interior |
| edge-label∩border | Label AABB overlaps a block perimeter band (2.5 px) |
| edge-label∩body-text | Label AABB overlaps any `role=body` text |
| edge-label∩edge-label | Two edge labels overlap |

### Tests / renders (this round)

```
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
# 20 passed
.venv/bin/python scripts/render_flowsheet.py --self-check
# SCHEMA · GEOMETRY · ANNOTATION-AUDIT · DETERMINISM · DEMO-FILL PASS; LINT SKIPPED
```

Re-rendered:

- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet.svg`
- `docs-private/research/2026-07-19-plant-flowsheet/render/flowsheet-demo-fill.svg`

### Post-fix label anchors (examples)

| Edge | Label | Placement |
|------|-------|-----------|
| e_calc | Ca dose → calciothermic | Inter-sub gap inside Ca column (end-anchor, clear of Sc2O3) |
| e_melt_3_4 | swap domes · raise T | Above both column tops in sky band at gutter mid |
| e_o2_sky_* | O₂ ↑ sky (part gettered) | Sky lane, end-anchor left of source gutter |
| e_c6 | Mg dose → C6 | Inter-sub gap (end-anchor left of connector) |

Rump chips remain oxides (BeO / ZrO2 / HfO2 / Sc2O3) per census.

**Staged, not committed** (controller owns commit).

---

## Phase 1b — membership lock + bin admission predicates

**Commit message:** `feat(flowsheet): membership lock + bin admission predicates`  
**Touched:** `data/flowsheet.yaml`, `scripts/render_flowsheet.py`, `tests/test_flowsheet_asset.py`, this report.

### PART A — the lock

| Piece | Detail |
|-------|--------|
| Header | `map_version: "v8"`, `locked: true`, `locked_at: "2026-07-20"` |
| Contract comment | Membership changes require **two-touch bump**: bump `map_version` **and** re-pin `MEMBERSHIP_LOCK_HASH` in tests |
| Hash scope | Sorted `(bin_id, chip, status, condition_note, aggregate members)` only — sha256 |
| Pinned digest | `c158688a3c05c288e8db839add470130b98d048e5e73ead544cc663b5296dbc8` |
| Stability | Annotation / edge / ops / `review` / `admission` edits leave the hash unchanged (tested) |
| Provenance | Per-chip `review: {map: "v7", finding: "<bin findings from v7 map>"}`; v8 chips `CO-CH4-organics`, `CO2-CO`, `P2-PO` → `{map: "v8"}` |

### PART B — bin admission predicates

Each sub-box has an `admission` block — declarative clauses over **fact fields** (no physics values in YAML):

| Bin | Predicate (summary) |
|-----|---------------------|
| `volatile_metal_trap` | `any_of` metal@trap_band \| oxide@lance; `mode_fork: c0` |
| `cryo_train` | cryo window / gas / halogen / volatile-gas |
| `alkali_cyclone` | family alkali \| metal@alkali_band |
| `ferroalloy_tap` | siderophile \| already-native \| host metal |
| `sio_glass` | form sio_glass |
| `gas_conditioning` | gas_conditioning window \| noncondensable gas |
| `lox_cistern` | product oxygen |
| `mg_crown` | element Mg |
| `c6_magnesiothermic` | reducibility mg_reducible |
| `ca_crown` | alkaline-earth \| divalent_ree |
| `calciothermic` | ca_reducible \| REE \| actinide |
| `rump_product` | reducibility not_reducible |

Evaluator outcomes per chip: **PASS / FAIL / UNKNOWN**. FAIL → lint error; UNKNOWN → WARN (never crash).

**Live fact sources (today):** `build_live_major_facts()` from Ellingham ranks + `vapor_pressures.yaml` metals + process-map anchors (rump Be/Zr/Hf/Sc, C6/Ca crowns). `trace_elements.yaml` overlays when present (t-380 not landed).

**Coverage on this base:**

```
admission coverage: 36 PASS / 0 FAIL / 42 UNKNOWN (of 78 chips)
```

Honest: most VMT/PGE/conditional traces stay UNKNOWN until the trace table lands. Live PASS set includes BeO/ZrO2/HfO2/Sc2O3, Fe/Ni/Co, Na/K, cryo majors, glass, O2, Mg, Al–Ta C6, Ca-crown set, REE/Y/Th/U.

**Contradiction fixture:** Fe forced into `rump_product` with `not_reducible` admission + siderophile facts → FAIL (lint not ok).

### Tests / renders

```
.venv/bin/python -m pytest tests/test_flowsheet_asset.py -n0 -q
# 29 passed
.venv/bin/python scripts/render_flowsheet.py --self-check
# SCHEMA · ADMISSION · GEOMETRY · ANNOTATION-AUDIT · DETERMINISM · DEMO-FILL PASS
.venv/bin/python scripts/render_flowsheet.py --lint
# 36 PASS / 0 FAIL / 42 UNKNOWN; routing SKIPPED (no trace_elements.yaml)
```

SVGs re-rendered and **byte-identical** to pre-phase-1b (metadata-only YAML; no visual elements added).

### New test inventory (additive)

| Test | Checks |
|------|--------|
| `test_membership_lock_header` | map_version / locked / locked_at |
| `test_membership_lock_hash_pinned` | sha256 pin + two-touch comment |
| `test_membership_hash_stable_under_annotation_edit` | non-membership edits |
| `test_membership_hash_moves_when_chip_status_changes` | real membership edit moves hash |
| `test_review_provenance_fields_parse` | v7 findings + v8 chips |
| `test_every_bin_has_admission_block` | all 12 bins |
| `test_predicate_evaluator_pass_fail_unknown` | synthetic facts |
| `test_live_major_species_admission_green` | rump / Fe-Ni / Na-K / C6… |
| `test_deliberate_admission_contradiction_fails` | FAIL path |
