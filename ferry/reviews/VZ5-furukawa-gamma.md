# VZ5 — independent VERIFY of Z17 Furukawa γ°_Fe P0 fix

**When:** 2026-09-23 ~00:45 EDT (America/Toronto)  
**Seat:** VZ5 — different agent from Z17 fixer (read-only on product tip; no PDF in git; no force-push; no rewrite of fix branch)  
**Verifier branch:** `empirical/vz5-furukawa-gamma-2026-09-23` @ `2e9e17c3d` (= `origin/work-v064-green`; review-only — no product commit)  
**Null hypothesis:** claimed fix is a false positive (misread PDF / already correct / wrong replacement) → OVERTURN  

| Claim | Tip | PDF | Verdict |
| --- | --- | --- | --- |
| kems-119-furukawa-1975 γ°_Fe was 0.389 / 0.38_9 → printed 0.38₅ → **0.385** (Fig.6 + prose) | `e52066ac75ba064cbacee248fe97c0c3f2c912d7` on `empirical/z17-murchison-furukawa-ueda-2026-09-23` | B5 `kems-119-furukawa-1975.pdf` | **CONFIRM** |

**TL;DR:** **CONFIRM.** Tip is **I2-eligible**. No correcting commit on vz5 branch.

PDF sha256 (audit copy; not in git): `f2e3d5a84037dac5c18fffab90a809f99e7297854de0a4779036f8ca585202d3`  
Fixer write-up: `/workspace/ferry-inbox/reviews/Z17-murchison-furukawa-ueda.md`  
Scratch: `/tmp/vz5-furukawa/` (fresh `pdftoppm` 350 dpi pp.6–8 + zooms; not for commit)

---

## Method

1. Fresh `pdftotext -layout` + `pdftoppm -png -r 350` of PDF pages 6–8 (= journal **3055–3057**).
2. Visual re-read of **Fig. 6** label (p.3055) and the infinite-dilution prose sentence (p.3056 right column).
3. Tip vs parent YAML for `gamma0_Fe_*` fields; note Fig.6 observation bag already held 0.385.
4. Did **not** touch `empirical/z17-…` tip; did **not** force-push.

---

## Printed evidence

### Fig. 6 (PDF p.6 / pub. **3055**)

Caption: “Fig. 6. Activities for the Fe-V system at 1 600 °C.”

Inside the plot, near the Fe activity curve at low $N_{Fe}$:

> **$\gamma^\circ_{Fe} = 0.38_5$**

Trailing digit is a **subscript 5** (flat top bar + curved belly — not a 9). Companion label $\gamma^\circ_V = 0.163$ (prose elsewhere prints $0.16_3$).

### Prose (PDF p.7 / pub. **3056**)

> 無限希薄における活量係数として $\gamma^\circ_V = 0.16_3$ を，$\gamma^\circ_{Fe} = 0.38_5$ を $1600^\circ\mathrm{C}$ において得た．

OCR/layout flattens to `γov=0.163` / `0.385` (dash before 0.385 is a line-break, not a minus sign).

### Conclusions (PDF p.10 / pub. **3059**) — unchanged

> $\gamma^\circ_V = 0.16$（固体基準），$\gamma^\circ_{Fe} = 0.39$（液体基準）

Rounding **0.385 → 0.39** is correct; tip still stores `gamma0_Fe_conclusions_p3059: 0.39`.

---

## Tip vs parent

Commit `e52066ac7` — `fix(kems-119): correct gamma0_Fe 0.38_9 → 0.38_5 (p.3056)`  
Files: `data/literature/extracts/kems-119-furukawa-1975.yaml`, `…/extracts-v2/kems-119-furukawa-1975.yaml` only.

| locus | parent (`e52066ac7^`) | tip (`e52066ac7`) |
| --- | --- | --- |
| `gamma0_Fe_as_printed_p3056` | **0.389** | **0.385** |
| `gamma0_Fe_print_note` | `0.38_9` / subscript-9 | `0.38_5` / subscript-5 |
| locator note | `0.38_9` | `0.38_5` |
| `gamma0_Fe_conclusions_p3059` | 0.39 | 0.39 (unchanged) |
| Fig.6 bag `labeled_two_phase_N_Fe` | **0.385** already | 0.385 (unchanged) |

The P0 was the dedicated γ°_Fe observation / notes (0.389); the Fig.6 observation bag already carried the printed **0.385** label. Tip aligns both with the printed cell.

---

## Verdict

**CONFIRM P0 → I2.** Printed infinite-dilution γ°_Fe is **0.38₅ → 0.385**, not 0.389. Fold tip `e52066ac7` into I2.

No overturn → no correcting commit on `empirical/vz5-furukawa-gamma-2026-09-23`.
