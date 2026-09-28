# VZ5 — independent VERIFY (ADDENDUM-B5) of Z17 kems-119 Furukawa γ°_Fe fix

**When:** 2026-09-23 ~00:52 EDT (America/Toronto)  
**Seat:** VZ5 (executor; blind to Z17 fixer reasoning — PDF + tip YAML only)  
**Verifier branch:** `empirical/vz5-kems-119-2026-09-23` @ `2e9e17c3d` (= `origin/work-v064-green`)  
**Worktree:** `/workspace/repos/wt/slot-z5`  
**Fix under verify:** `e52066ac75ba064cbacee248fe97c0c3f2c912d7` on `empirical/z17-murchison-furukawa-ueda-2026-09-23`  
**Extract (tip):** `data/literature/extracts/kems-119-furukawa-1975.yaml` (+ paired `extracts-v2/` note)  
**PDF (not in git):** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/kems-119-furukawa-1975.pdf`  
**PDF sha256:** `f2e3d5a84037dac5c18fffab90a809f99e7297854de0a4779036f8ca585202d3`

**Null hypothesis:** claimed 0.389 → 0.385 fix is wrong (misread PDF, already-correct tip, or collateral damage to other kems-119 observations) → **OVERTURN**.

## VERDICT: **PASS / CONFIRM** — tip is **I2-eligible**

| Check | Printed (PDF) | Tip after `e52066ac7` | Result |
| --- | --- | --- | --- |
| γ°_Fe as printed (p.3056 / Fig.6) | **0.38₅** (= 0.385) | `gamma0_Fe_as_printed_p3056: 0.385` | **CONFIRM** |
| Trailing digit | subscript **5** (not 9) | print_note: `0.38_5` / subscript-5 | **CONFIRM** |
| Conclusions round | p.3059 **γ°_Fe = 0.39** (液体基準) | `gamma0_Fe_conclusions_p3059: 0.39` unchanged | **CONFIRM** |
| Other kems-119 observations | — | leaf diff = **3 fields only**, same obs; 21/21 ids stable | **CONFIRM** |

No correcting commit on this branch. Did not rewrite / force-push the Z17 tip. No PDFs committed.

---

## Method (blind)

1. Recycled free slot-z5 → branch `empirical/vz5-kems-119-2026-09-23` from `origin/work-v064-green`.
2. Read fixed + parent YAML via `git show e52066ac7:…` / `e52066ac7^:…` (detached read; did not check out the fix tip onto the worktree product files).
3. Fresh `pdftotext -layout` + `pdftoppm -png -r 200` of journal pages **3055–3056** (PDF pp.6–7) and **3059** (PDF p.10). Visual re-read of Fig. 6 label and the infinite-dilution prose sentence; conclusions line for the rounded value.
4. Structured leaf-walk diff of the full extract YAML (before vs after tip).

---

## Printed evidence

### Fig. 6 (pub. **3055** / PDF p.6)

Caption: “Fig. 6. Activities for the Fe-V system at 1600 °C.”

On-plot annotation at low $N_{Fe}$ along the $a_{Fe}$ (liquid) limb:

> **$\gamma^\circ_{Fe} = 0.38_5$**

Companion label $\gamma^\circ_V = 0.163$ (prose prints $0.16_3$). Trailing Fe digit is a **subscript 5**, not 9.

### Prose (pub. **3056** / PDF p.7)

Right-column infinite-dilution sentence (pdftotext + visual):

> 無限希薄における活量係数として $\gamma^\circ_V = 0.16_3$ を，$\gamma^\circ_{Fe} = 0.38_5$ を 1600°C において得た。

OCR stream renders the Fe value as `0.385` (subscript collapsed into the third decimal), consistent with **0.38₅ → 0.385**, not 0.389.

Locator on tip: `published_page: 3056`, `pdf_page_index: 7` (matches 1-based PDF page for 3056).

### Conclusions (pub. **3059** / PDF p.10)

> また $\gamma^\circ_V = 0.16$（固体基準），$\gamma^\circ_{Fe} = 0.39$（液体基準）を得た。

Rounded **0.39** — tip correctly leaves `gamma0_Fe_conclusions_p3059: 0.39` alone.

---

## Tip delta scope (no collateral)

`git show e52066ac7` touches only:

- `data/literature/extracts/kems-119-furukawa-1975.yaml` — **3 leaf fields** on observation `furukawa_1975_gamma0_fe_1600c`:
  - `locator.note`: `0.38_9` → `0.38_5`
  - `values.gamma0_Fe_as_printed_p3056`: `0.389` → `0.385`
  - `values.gamma0_Fe_print_note`: subscript-9 → subscript-5
- `data/literature/extracts-v2/kems-119-furukawa-1975.yaml` — matching locator `note` only (same obs id `…::furukawa_1975_gamma0_fe_1600c`)

Full leaf-walk of v1 extract: **3 diffs**, observation_id set size **21 → 21**, no adds/removes.

Unrelated same-numeric field left alone (good):

- `furukawa_1975_fig6_fe_v_activities_figure_only` → `labeled_two_phase_N_Fe: 0.385` (composition label, not γ°) — **unchanged** before/after.

---

## Disposition

- **PASS** → fold tip **`e52066ac7`** into **I2**.
- No overturn product commit on `empirical/vz5-kems-119-2026-09-23`.
- No force-push; no PDFs in git.
