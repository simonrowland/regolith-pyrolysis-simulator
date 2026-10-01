# REVIEW — delta re-review: review/stolyarova-1996-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z15` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `9e79f3fa1ea1e8de22fe25143a7a3ddd87041b97` (parent `e311b68b2ef30e4c32cfaa416a4ce1078ec2832b`)
- **Commit:** `Add Stolyarova oxygen derivation lineage` — only
  `data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml` and
  `data/literature/extracts-v2/stolyarova-1996-cao-alumina-silica-kems.yaml`
- **Date:** 2026-10-01 ~08:27 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Corpus:** `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1996/`
  (PDF + MinerU OCR + page images). Page-checked with `pdftoppm` on journal pp. 18–19
  + MinerU Table 1 HTML + OCR equations (7)–(9).
- **Prior:** `REVIEW-stolyarova-1996-extract-2026-09-30.md` (REVISE, P2: 55 O points missing `derived_from`)
- **REQ:** `REQ-delta-review-stolyarova-1996-9e79f3fa-2026-10-01.md` — attack list (ions, parents, scope, cell)
- **Related:** Shornikov 1994 delta REVISE (Mo-calc vs Al/Si-check) — same calc-vs-check rigor

## Attack 1 — six Mo ion intensities + three unlabeled columns

**Page:** journal **19** (PDF page 5; Table 1). Values verified against page image + MinerU:

| Ion | Col1 | Col2 | Col3 |
| --- | ---: | ---: | ---: |
| MoO₂⁺ | 0.25 | 7.3 | 0.017 |
| MoO₃⁺ | 0.05 | 1.4 | 0.003 |

Numeric admit of the six cells: **pass**.

**Column meaning:** the three columns sit under a single printed group header
`CaO · Al₂O₃ · SiO₂`, beside separate `CaO` / `Al₂O₃` / `SiO₂` pure-oxide columns.
They are **not** labeled as p_O / p′_O / p″_O, compositions, or temperatures.

Pattern on the page (and scale-invariant Mo ratio ~5):

- Col1 has Al⁺ = 1.00 and a full melt spectrum (Ca⁺ 3.41, SiO⁺ 1430, …)
- Col2 has Ca⁺ = 100 (Al/Si dashed); Mo scales Col1→Col2 by ~29× (= 100/3.41)
- Col3 has SiO⁺ = 100 (Ca/Al dashed); Mo scales Col1→Col3 by ~0.07 (= 100/1430)

So the three melt columns are **three renormalizations of one mass spectrum for one
composition** (CaO·Al₂O₃·SiO₂ at 1933 K), not three independent Mo measurements and
not a one-to-one map onto the three O blocks. Column meaning is **not printed**; the
normalization reading is inferred from the “100” bases matching the pure-oxide style.

**Should they be stored?** Storing all three as independent `ion_intensity` observations
without stating they are renormalizations of one spectrum overstates independence.
At most one column (or the MoO₃⁺/MoO₂⁺ **ratio**, which is scale-invariant) is needed
as an illustrative Eq. (7) input for that single composition. They do **not** supply
per-row Mo ion currents for the 30 Table 2 p_O composition points.

## Attack 2 — each O block’s `derived_from` vs its equation

Paper (pp. 18–19): O calculated from equilibria (4)–(6) / Eqs. (7)–(9) “using the
values of the MoO₂⁺, MoO₃⁺, Al⁺, AlO⁺, and SiO₂⁺, SiO⁺ ion currents”; mutual agreement
~15%. Unlike Shornikov 1994, here (7)/(8)/(9) are three **printed** Table 2 columns
(p_O / p′_O / p″_O), not Mo-calc + Al/Si-check on one column.

| Block | Equation parents (page) | Delta `derived_from` | Verdict |
| --- | --- | --- | --- |
| p_O (30 pts) | I_MoO₃⁺ / I_MoO₂⁺ via Eq. (7) | **all six** Table 1 Mo ion cols | **False** — Table 1 is one composition × three renormalizations; per-row Mo ions for Table 2 are **not printed** |
| p′_O (10 pts) | I_AlO⁺ / I_Al⁺ via Eq. (8) | Table 2 p_Al + p_AlO blocks | Species direction OK (Al route **is** the calc for p′_O here); Eq is in **ion currents**, not pressures; migrate fans each point to **all** Al/AlO points |
| p″_O (15 pts) | I_SiO⁺ / I_SiO₂⁺ via Eq. (9) | Table 2 p_SiO + p_SiO₂ blocks | Same as p′_O for Si |

Additional: v1 `derivation.relation` for Eq. (7) copies MinerU’s duplicated `σ(MoO₂)` typo;
page image shows `σ(MoO₃)` in the denominator. Migrated v2 `derivation` on O points is
still only `atm_to_Pa` (thermodynamic relation did not land on exploded points).

Clearing J02 by pointing every p_O point at the six Table 1 Mo cells is lineage that
clears the hard issue **without being true** (same class of defect as Shornikov 1994’s
Al/Si-as-calc swap).

## Attack 3 — diff scope

`git diff e311b68b2 9e79f3fa1` touches **only** this source’s two YAML files
(+1981 lines). Census: parent 306 → tip **312** observations (+6 ions);
`measured_direct` 108→114; `model_derived` stays 55; missing `derived_from` 55→**0**.
Targeted `python3 tools/validate_literature_extracts.py …/stolyarova-1996-cao-alumina-silica-kems.yaml --skip-priority` → **OK**. Full W3 not run (VPS constraint).
Dry-run hard-issue claim 3,235→3,180 is structurally consistent with −55 `conditional_field`
on `.derived_from`; no other source moved. **Pass** on scope.

## Attack 4 — cell material

- p. 18: “Mo⁺, MoO⁺, MoO₂⁺, MoO₃⁺ ions were identified” — no sentence naming this
  experiment’s cell/crucible.
- Extract still `benches[0].cell_material_and_liner.state = {tag: unknown, reason: not_published}`
  with note that Table 1 MoO ions do not establish a Mo cell.
- Delta does not invent cell material. **Pass** (still unknown — owner’s call).

## Verdict logic

Numeric ion cells match the page. Diff scope and cell-unknown are clean. But the
ancestry the delta added for p_O is false (illustrative single-composition Table 1
renormalizations ≠ calculation parents for 30 Table 2 rows), and p′_O / p″_O parenting
is only species-directionally right (I vs p; migrate fan-out). J02 clears without true
lineage → standing P2, same rigor bar as Shornikov 1994 delta.

## P1 / P2

- **P1:** none
- **P2 (standing):** Rebuild O `derived_from` graphs.
  - **p_O:** Do **not** parent on all three Table 1 Mo columns. Either (a) admit that
    per-composition MoO₂⁺/MoO₃⁺ ion currents used for each Table 2 p_O row are
    **not printed** (keep model_derived; mark parents unavailable / not admitted so
    J02 stays hard until real parents exist), or (b) if owner later supplies
    composition-matched Mo ion currents, parent each p_O point on that row’s Mo pair
    only. If Table 1 Mo cells are kept, store **one** renormalization (or the ratio)
    as illustrative mass-spectrum context — not as calculation parents for the full
    p_O block — and note the unlabeled columns are renormalizations of one spectrum.
  - **p′_O / p″_O:** Prefer ion-current parents matching Eq. (8)/(9), or same-row
    Al/AlO (SiO/SiO₂) parents with an explicit I↔p relation; do not fan every point
    to the entire parent series. Fix Eq. (7) `σ(MoO₃)` in the relation string to match
    the page. Do not infer cell material.

VERDICT: REVISE
