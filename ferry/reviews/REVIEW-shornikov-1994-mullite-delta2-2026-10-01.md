# REVIEW — delta2 re-review: review/shornikov-1994-mullite-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `0b4368673add9c0ac1807c0af79b1c6690059482` (parent `96446134627cde6ef97e5be06526436ca64a995c`)
- **Commit:** `Correct Shornikov oxygen pressure lineage` — only
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml` and
  `data/literature/extracts-v2/shornikov-1994-mullite-kems.yaml`
- **Date:** 2026-10-01 ~09:35 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Corpus:** PDF
  `/workspace/ferry-inbox/reviews/_req-2026-10-01/shornikov-1994-mullite-kems.pdf`
  (3 pages = journal 478–480). Page-checked with `pdftoppm` + tesseract + pdftotext on p. 478–479.
- **Prior:** `REVIEW-shornikov-1994-mullite-delta-2026-10-01.md` (REVISE, false Al/Si parents)
- **REQ:** `REQ-delta2-review-shornikov-1994-0b436867-2026-10-01.md` — attack Mo/O2 values, O2 circularity, p_O relation, census

## Attack (1) — twelve MoO2 / MoO3 / O2 values vs Table 2 p. 478

Page image (PDF page 1 = journal 478) Table 2 matches the claimed printed coefficients and exponents exactly:

| species | scale | 1833 K | 1933 K | 1983 K | 2033 K |
| --- | --- | --- | --- | --- | --- |
| p_MoO₂ | × 10⁻⁸ atm | 0.526 ± 0.050 | 1.91 ± 0.15 | 3.40 ± 0.25 | 5.78 ± 0.30 |
| p_MoO₃ | × 10⁻⁹ atm | 1.24 ± 0.08 | 4.22 ± 0.25 | 7.64 ± 0.40 | 12.4 ± 0.6 |
| p_O₂ | × 10⁻¹¹ atm | 0.521 ± 0.050 | 4.59 ± 0.26 | 13.7 ± 0.6 | 34.3 ± 1.7 |

Extract v1/v2 admit all twelve; atm→Pa conversions check (× 101325). Locators cite Table 2 p. 478. **Values: PASS.**

## Attack (2) — measured vs calculated; O2-as-parent circularity

**Mass spectrum (Table 1 p. 478):** MoO₂⁺ and MoO₃⁺ are listed among identified ions. **O⁺ and O₂⁺ are absent.**

**Prose (p. 479, right column):**

> Partial pressures of vapour species over mullite (Table 2) were obtained by the traditional method of comparison of ion currents. … Partial vapour pressures of **oxygen were calculated** as previously, using the data available on the equilibria in the vapour phase of the following reactions:
>
> MoO₃ ⇌ MoO₂ + O (1)
> O₂ ⇌ 2O (2)
>
> The partial vapour pressures of oxygen obtained using equilibria (1) and (2) corresponded to the values calculated using … (3)–(6) (within a relative error of about 15%).

**Paper derivation direction:**

1. **MoO₂ / MoO₃** — MEASURED by ion-current comparison (Table 1 ions present; general Table 2 method).
2. **p_O** — CALCULATED from reaction **(1)** using measured Mo pressures and known K: `p_O = K₁ · p_MoO₃ / p_MoO₂`.
3. **p_O₂** — CALCULATED from reaction **(2)** once p_O is known: `p_O₂ = p_O² / K₂` (no independent O₂⁺ measurement). O and O₂ are co-products of the Mo-oxide oxygen calculation; O₂ is not an independent measured input to p_O.

**What the delta did:** each of the four `p_O` rows takes same-T `MoO2`, `MoO3`, **and `O2`** as `derived_from`. v2 tags the twelve new Mo/O2 rows `measured_direct` / `measured`, including **O2**.

**Finding:** listing O2 as a **PARENT** of p_O is **circular / backwards**. True parents of p_O are MoO₂ + MoO₃ via (1). O₂ is a **sibling product** (or child of p_O) via (2), not a parent. Tagging O2 `measured_direct` contradicts p. 479 (“oxygen were calculated”) and the absence of O₂⁺ from Table 1.

## Attack (3) — p_O relation / locator / Al/Si / model_derived

| check | result |
| --- | --- |
| Al/Si parents removed | **PASS** — all four O rows now MoO2+MoO3+O2 only |
| relation text cites (1)–(2) as calculation, (3)–(6) as ~15% check | **PASS** (v1 `derivation.relation`) |
| locator for calculation prose | **PASS** — p. **479** (not 478) |
| rows stay `model_derived` / method_class derived | **PASS** |
| relation embeds O2 as calc input of p_O | **FAIL** — same circularity as attack (2) |
| leftover v1 `method_as_printed` / `calculation_method` | still say reactions **(1)–(6)** on O rows (stale vs fixed relation) |

## Attack (4) — scope / census / cell

- Diff touches **only** this source’s two YAML files (+2498/−29).
- Census **29 → 41** (+12 = 4 MoO2 + 4 MoO3 + 4 O2), confined to this source.
- Cell / experiment material: still `shornikov-1994-mullite-mo-cell` (Mo Knudsen, 400:1); no rewrite of apparatus beyond new rows pointing at the same experiment.
- Targeted `python3 tools/validate_literature_extracts.py data/literature/extracts/shornikov-1994-mullite-kems.yaml --skip-priority` → **OK**. Full W3 / global hard-issue re-count not run (VPS constraint). No new observation class invented beyond admitting printed Table 2 Mo/O2 rows; hard-issue class set unchanged in kind (ancestry / J02 lineage still the standing issue — now with wrong O2 parent rather than wrong Al/Si parents). REQ’s “hard issues still 3180” not re-audited here.

## Verdict logic

Delta correctly: (a) extracted the printed Mo/O2 Table 2 cells, (b) removed false Al/Si parents, (c) rewrote the relation to (1)–(2) calc / (3)–(6) check with p. 479 locator. That clears the prior REVISE’s Al/Si finding.

It does **not** clear ancestry truth: O2-as-parent of p_O is the same class of error (lineage that clears structure without matching the paper’s derivation). MoO₂/MoO₃ are the measured calculation parents; O₂ must be demoted to sibling/child and must not be `measured_direct`.

## P1 / P2

- **P1:** none (printed Mo/O2 coefficients and exponents are correct)
- **P2 (standing ancestry, now O2-circularity):** On all four `p_O` rows: drop `O2` from `derived_from` / derivation inputs; keep MoO2+MoO3 as parents via reaction (1). Retag the four O2 observations as `model_derived` (sibling product via reaction (2) from p_O, or co-derived with p_O from Mo equilibria — not measured_direct). Rewrite relation so (2) is not described as a p_O **input**. Clean leftover `method_as_printed` / `calculation_method` still citing (1)–(6). Do not invent ion currents for O₂⁺.

VERDICT: REVISE
