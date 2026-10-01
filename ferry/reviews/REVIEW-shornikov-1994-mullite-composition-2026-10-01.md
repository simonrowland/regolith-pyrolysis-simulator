# REVIEW — composition: review/shornikov-1994-mullite-composition

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `8b11008369eca0cd2833851b1d7a195384665149` (parent / green `7af7f0fb04fd2af41c62e8abe0a260a90fdd6705`, which already contains LAND `ed6ebc914`)
- **Commit:** `Record Shornikov mullite oxide composition` — only
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml`
  (extracts-v2 sibling deliberately untouched; ids are content hashes; landing regen writes it)
- **Date:** 2026-10-01 ~13:45 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — **wrong-number / composition-source guards only**; lineage completeness not a gate
- **Corpus:** PDF
  `/workspace/ferry-inbox/reviews/_req-2026-10-01/shornikov-1994-mullite-kems.pdf`
  (3 pages = journal 478–480). Formula / sample-prep / Table 2 / Eqs. (12)–(13) re-checked via `pdftotext`.
- **Prior:** `REVIEW-shornikov-1994-mullite-delta3-2026-10-01.md` (LAND `ed6ebc914`)
- **REQ:** `REQ-review-shornikov-1994-composition-8b110083-2026-10-01.md` — formula locator is Table 2 condensed sample; composition marked derived; nothing else (values/lineage/cell) changed; does paper print an analysed composition that should take precedence?

## Attack (1) — formula locator = condensed sample of Table 2 (blocking)

**Paper (p. 478):**
- Title / opening: study of / “Mullite (3Al2O3·2SiO2)”
- Experimental: “samples of mullite were prepared using the precursor method” (Ref. 8); no printed analysis, no statement that charge stoichiometry differed from the formula.
- Results: vaporization “over 3Al2O3·2SiO2”; Table 1 header column “3Al2O3·2SiO2”; Table 2 title “Partial pressures of vapour species (pi) **over mullite**” at 1833–2033 K (all below the printed incongruent melting point 2163 ± 4 K).

**Tip:** on all 33 Table 2 partial-pressure rows, `point_conditions.composition` is Al2O3=0.6, SiO2=0.4 mole fraction with locator page 478 / table “2” and note that oxide mole fractions are derived from the printed mullite formula 3Al2O3·2SiO2. Phase string unchanged (`mullite (3Al2O3·2SiO2); phase not specified by the article`) — not retagged as melt.

**PASS.** The formula is what the paper prints for the condensed sample whose vapour is Table 2; not a nominal charge the paper says differed.

## Attack (2) — composition marked derived, not printed analysis (blocking)

**Tip composition block (identical on all 33 rows):**
- `state.tag: value` with `basis: printed_oxides`, `amount_basis: mole_fraction`, components Al2O3 `'0.6'` / SiO2 `'0.4'`
- `method_class: calculated`
- `inference.relation: formula_to_oxide_mole_fraction`
- inputs explicitly: `x_Al2O3 = 3/(3+2) = 0.6`; `x_SiO2 = 2/(3+2) = 0.4`; sum 1; sanity Al2O3 wt fraction ≈ 0.7180 (71.8 wt%)

**Arithmetic check:** 3/5 = 0.6, 2/5 = 0.4; (3×101.961)/(3×101.961+2×60.084) ≈ 0.71795 → 71.8 wt%. **PASS.**

Not tagged as a printed mole-fraction / wt% analysis. Parent composition was `state.tag: unknown` (“no row-specific mole fraction or weight percent”).

## Attack (3) — nothing else changed on values / lineage / cell (blocking)

Structural diff vs green `7af7f0fb0`:
- **Files:** only `data/literature/extracts/shornikov-1994-mullite-kems.yaml` (no extracts-v2).
- **Census:** 41 observations unchanged (33 Table 2 PP + 8 Table 6 activities).
- **33 Table 2 PP rows:** composition unknown → derived 0.6/0.4; stripping composition, JSON-identical to parent (values, method_class, derived_from / lineage, locators, phase, experiment id). Values byte-identical on all 33.
- **8 Table 6 activity rows:** composition still unknown; only `standard_state` string extended to record that Eqs. (12)–(13), p. 480 define pure-oxide vapour references (`p_i°` over alumina or silica) but do not specify reference phase. Values / lineage unchanged.
- **Cell / benches / experiments:** unchanged (`shornikov-1994-mullite-mo-cell`, Mo Knudsen, 400:1).
- Targeted `python3 tools/validate_literature_extracts.py data/literature/extracts/shornikov-1994-mullite-kems.yaml --skip-priority` → **OK**.

**PASS.**

## Attack (4) — analysed composition that should take precedence? (blocking)

Full-text search of the PDF layer: no printed chemical analysis, wt%, or mole-fraction assay of the condensed sample. Only identity as mullite / 3Al2O3·2SiO2 and precursor-method prep (Ref. 8). Table 6 activities are derived thermodynamic quantities, not a sample assay. **No analysed composition takes precedence. PASS.**

## Notes (non-blocking per POLICY)

- Formula string appears on p. 478 in title/opening/Table 1/body; Table 2 header says “over mullite.” Locating the derived composition at Table 2 is correct as the condensed-sample context for those rows.
- REQ wording “33 measured Table 2 rows” includes the 8 O / O2 rows that remain `method_class: derived` (author-calc); all 33 Table 2 PP rows correctly received the same derived composition. Not a wrong number.
- Reference-phase underspecification on Eqs. (12)–(13) is documented, not invented.

## P1 / P2

- **P1 (wrong-number / composition-source):** none
- **P2:** none

VERDICT: LAND 8b11008369eca0cd2833851b1d7a195384665149
