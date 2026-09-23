# X1 — EXTRACTION AUDIT e1-simulants — 2026-09-22

Branch: `empirical/e1-simulants-2026-09-22`
Tip: `2d7dc0fb8` (unchanged; no fix commit)
Prior: `ferry-inbox/reviews/E1-simulants.md`
Worktree: `/workspace/repos/wt/slot-05`
PDFs: `/workspace/batch-z/pdfs/{britt-2019-asteroid-simulants,deguzman-2026-simulant-physicochemical,hendrix-2024-reactivity-reduced-simulants}.pdf`
Scout: `/workspace/batch-z/scout/slice-1.md` rows 2, 3, 5
Date: 2026-09-22 (America/Toronto, EDT)

## Verdict

**P0: 0** — every landed oxide cell matches the printed table cell (value, unit wt%, oxide basis, row/column mapping, locator).
**READY.** No branch change; nothing to push.

## Method

For each of the two E1 landings: open the PDF at the cited locator, read the printed cell (`pdftotext -layout` + 200 dpi page raster crop of the table), compare to every oxide in `experiments[].sample.printed_composition.state.value`. De Guzman was re-checked as still label-only (E1 BLOCKED; no oxide map to audit).

## Cell-by-cell

### britt-2019-asteroid-simulants — PASS

- **Printed:** Table 3, Meteoritics & Planetary Science p. 2075 (PDF p. 9), XRF major-element bulk oxide wt% columns CI / CM / CR Simulant.
- **Landed:** `ci-tga-ega`, `cm-tga-ega`, `cr-tga-ega` `sample.printed_composition`.

| oxide | printed CI | landed CI | printed CM | landed CM | printed CR | landed CR | match |
|-------|------------|-----------|------------|-----------|------------|-----------|-------|
| SiO2  | 25.0       | 25.0      | 32.5       | 32.5      | 19.3       | 19.3      | OK |
| TiO2  | 0.5        | 0.5       | 0.3        | 0.3       | 0.4        | 0.4       | OK |
| Al2O3 | 3.1        | 3.1       | 3.1        | 3.1       | 1.2        | 1.2       | OK |
| FeOT  | 25.8       | 25.8      | 20.2       | 20.2      | 42.9       | 42.9      | OK |
| MnO   | 0.3        | 0.3       | 0.2        | 0.2       | 0.3        | 0.3       | OK |
| MgO   | 30.2       | 30.2      | 32.1       | 32.1      | 30.1       | 30.1      | OK |
| CaO   | 3.0        | 3.0       | 3.1        | 3.1       | 0.8        | 0.8       | OK |
| Na2O  | 6.4        | 6.4       | 6.2        | 6.2       | 3.3        | 3.3       | OK |
| K2O   | 0.4        | 0.4       | 0.2        | 0.2       | 0.1        | 0.1       | OK |
| P2O5  | 0.4        | 0.4       | 0.4        | 0.4       | 0.4        | 0.4       | OK |
| Cr2O3 | 0.2        | 0.2       | 0.2        | 0.2       | 0.1        | 0.1       | OK |
| SO3   | 4.9        | 4.9       | 1.5        | 1.5       | 0.9        | 0.9       | OK |

- **Unit / basis:** XRF oxide wt% as printed; FeOT and SO3 retained; Total row omitted — OK.
- **Row/experiment mapping:** CI → `ci-tga-ega`; CM → `cm-tga-ega`; CR → `cr-tga-ega`. Species TG/XRF obs FKs retargeted to matching experiments — OK.
- **Locator:** `pdf_page_index: 9`, `table: '3'` — OK (footer on PDF p. 9 is 2075).
- **Not landed (correct):** Table 2 mineral-recipe wt% (prototype / future-update columns).

### hendrix-2024-reactivity-reduced-simulants — PASS

- **Printed:** Table 1, Meteoritics & Planetary Science p. 2489 (PDF p. 4), bulk oxide wt% columns for JSC-1A, LMS-1, LHS-1 (Apollo 11 / Apollo 16 comparison columns not experimental charges).
- **Landed:** `jsc-1a-h2-reduction`, `lms-1-h2-reduction`, `lhs-1-h2-reduction` `sample.printed_composition`.

| oxide | printed JSC-1A | landed | printed LMS-1 | landed | printed LHS-1 | landed | match |
|-------|----------------|--------|---------------|--------|---------------|--------|-------|
| SiO2  | 46.2           | 46.2   | 42.81         | 42.81  | 44.18         | 44.18  | OK |
| TiO2  | 1.85           | 1.85   | 4.62          | 4.62   | 0.79          | 0.79   | OK |
| Al2O3 | 17.9           | 17.9   | 14.13         | 14.13  | 26.24         | 26.24  | OK |
| Cr2O3 | (blank)        | omit   | 0.21          | 0.21   | 0.02          | 0.02   | OK |
| FeO   | 11.2           | 11.2   | 7.87          | 7.87   | 3.04          | 3.04   | OK |
| MnO   | 0.19           | 0.19   | 0.15          | 0.15   | 0.05          | 0.05   | OK |
| MgO   | 6.87           | 6.87   | 18.89         | 18.89  | 11.22         | 11.22  | OK |
| CaO   | 9.43           | 9.43   | 5.94          | 5.94   | 11.62         | 11.62  | OK |
| Na2O  | 3.33           | 3.33   | 4.92          | 4.92   | 2.30          | 2.3    | OK |
| K2O   | 0.85           | 0.85   | 0.57          | 0.57   | 0.46          | 0.46   | OK |
| P2O5  | 0.62           | 0.62   | 0.44          | 0.44   | (blank)       | omit   | OK |
| SO3   | (blank)        | omit   | 0.11          | 0.11   | 0.1           | 0.1    | OK |

- **Unit / basis:** bulk oxide wt% as printed — OK.
- **Row/experiment mapping:** JSC-1A → `jsc-1a-h2-reduction`; LMS-1 → `lms-1-h2-reduction`; LHS-1 → `lhs-1-h2-reduction`. Apollo columns unused — OK.
- **Locator:** `published_page: 2489`, `pdf_page_index: 4`, `table: '1'` — OK.
- **Not landed (correct):** mineral-abundance block of Table 1; Apollo 11 / Apollo 16 columns.

### deguzman-2026-simulant-physicochemical — still BLOCKED (no oxide map)

- Experiments `bet-surface-area-series` and `hydrogen-tpr-series` still carry free-text labels only (no oxide `printed_composition` map).
- Matches E1 stop: multi-material series ≠ 1:1 charge; Table 1 is manufacturer mineral wt%; Table 2 is this-paper SEM-EDX elemental at%/wt%, not oxide.
- No composition cells to audit; pre-existing validator errors on this file left untouched (same as E1).

## Non-P0 notes (no fix)

- Hendrix LHS-1 Na2O YAML stores `2.3` for printed `2.30` — float canonicalization only; magnitude matches.
- Britt locator omits `published_page: 2075` (has `pdf_page_index: 9` only). Locator hygiene; composition values correct.

## Validation

`uv run python tools/validate_literature_extracts.py` on britt + hendrix → `OK: 2 extract file(s) valid`.
De Guzman still FAILs with pre-existing equipment-field / fidelity pin errors (not in X1 scope).

## Push

None. Tip remains `origin/empirical/e1-simulants-2026-09-22` @ `2d7dc0fb8`.

## Report line

**P0: 0 · tip: `2d7dc0fb8` · READY**
