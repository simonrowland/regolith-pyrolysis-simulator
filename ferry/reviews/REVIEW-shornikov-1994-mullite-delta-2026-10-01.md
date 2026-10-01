# REVIEW — delta re-review: review/shornikov-1994-mullite-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `96446134627cde6ef97e5be06526436ca64a995c` (parent `9bb714cf13bbf59cb20ac9f6f5fba0dc3ace36f8`)
- **Commit:** `Add Shornikov oxygen pressure ancestry` — only
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml` and
  `data/literature/extracts-v2/shornikov-1994-mullite-kems.yaml`
- **Date:** 2026-10-01 ~08:20 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Corpus:** PDF now present —
  `/workspace/ferry-inbox/reviews/_req-2026-10-01/shornikov-1994-mullite-kems.pdf`
  (3 pages = journal 478–480). Page-checked with `pdftoppm` + tesseract on p. 479.
- **Prior:** `REVIEW-shornikov-1994-mullite-extract-2026-09-30.md` (REVISE, P2 ancestry gap)
- **REQ:** `REQ-delta-review-shornikov-1994-96446134-2026-10-01.md` — attack calc vs check parents

## Attack — from which measured quantities did the authors CALCULATE p_O?

**Page:** journal **479** (PDF page 2; Table 2 itself is on 478). Exact prose (tesseract + pdftotext):

> Partial vapour pressures of oxygen were calculated as previously, using the data available on the equilibria in the vapour phase of the following reactions:
>
> MoO₃ ⇌ MoO₂ + O (1)
> O₂ ⇌ 2O (2)
>
> The partial vapour pressures of oxygen obtained using equilibria (1) and (2) corresponded to the values calculated using the partial pressures of vapour species over mullite and equilibrium constants of the following gaseous reactions (within a relative error of about 15%):
>
> AlO ⇌ Al + O (3)
> 2AlO ⇌ Al₂O + O (4)
> Al₂O ⇌ 2Al + O (5)
> SiO₂ ⇌ SiO + O (6)

**Finding:** p_O is **calculated** from Mo-oxide / O₂ equilibria (1)–(2). Al/Si dissociation equilibria (3)–(6) are the **cross-check** (“corresponded to … within … about 15%”), not the calculation inputs.

Table 2 (p. 478) **does print** `p_MoO₂`, `p_MoO₃`, and `p_O₂` at the same four temperatures. Those Mo/O₂ rows are **not** in the extract (v1 species keys remain Al, AlO, Al2O, AlSiO, SiO, O, Al2O3, SiO2; v2 still 29 observations). No Mo ion ratios were invented by this commit.

## What the delta did

Worker claim holds structurally:

| T (K) | v1/v2 `derived_from` parents | evidence |
| --- | --- | --- |
| 1833 | Al, SiO | model_derived |
| 1933 / 1983 / 2033 | Al, AlO, Al2O, AlSiO, SiO | model_derived |

Relation text still cites reactions (1)–(6) “including MoO3 ⇌ MoO2 + O and O2 ⇌ 2O” and says p_O was “checked against” the Al/Si pressures — then links those Al/Si rows as parents. That is exactly the calc-vs-check swap the REQ flagged.

- Diff touches **only** this source’s two YAML files (+37/−4). Census unchanged: **29** observations.
- Targeted `python3 tools/validate_literature_extracts.py data/literature/extracts/shornikov-1994-mullite-kems.yaml --skip-priority` → **OK**.
- Local J02-style scan on v2: **0** `model_derived` rows missing `derived_from` (four prior `conditional_field` structurally gone). Full W3 not run (VPS constraint).

## Verdict logic

Clearing J02 by pointing `derived_from` at the **check** species is lineage that clears the hard issue **without being true**. Real calculation parents are MoO₃/MoO₂ (and O₂ via (2)); they are printed in Table 2 but not extracted. Until those parents are extracted (or the schema can say parents-not-printed / refuse false Al/Si lineage), the hard ancestry issue should **stand**.

Same open question as Stolyarova 1996 (worker stopped rather than link unverified parents) — here the page answer is Mo-calc, Al/Si-check.

## P1 / P2

- **P1:** none
- **P2 (standing, now page-proven):** Replace the four `p_O` `derived_from` graphs. Either (a) admit Table 2 `p_MoO₂` / `p_MoO₃` (and `p_O₂` if retained as an (2) input) and parent the O rows on those measured Mo/O₂ pressures with a relation that matches (1)–(2) as the calculation and (3)–(6) as the check only, or (b) keep O model_derived without false Al/Si parents and mark parents unavailable / not admitted so J02 stays hard until (a). Do not treat Al/Si check concordance as calculation ancestry. Fix the relation page cite (calculation prose is p. **479**, not 478).

VERDICT: REVISE
