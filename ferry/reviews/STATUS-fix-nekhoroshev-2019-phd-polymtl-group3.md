# STATUS: fix nekhoroshev-2019-phd-polymtl — group 3 (appendix tables A.42, A.6)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:35 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `a99679a4a92889a12bbfce6ebb078255ac7adee7`
(chain: cfaa7465 → b02bcc7b group 1 → 2a9892ad group 2 → a99679a4 group 3)

## Groups covered
- Group 1 (done): 6/6 mismatches. Group 2 (done): Table 14.1 → 53 rows, Table 21.1 → 57 rows.
- Group 3 (this push):
  - Item 3, Table A.42, p.529 / PDF586 (220 dpi image read + crops; lines taken from `pdftotext -layout` with the same rule that
    reproduces all 32 existing p.528 lines exactly): **27 lines added → 59 lines**. New `structured_parameters_p529` in the YAML binds
    each function to its parameter and interval: g°(Na2B4O7)=G(Na2B4O7), g°(K2B4O7)=G(K2B4O7), three G(Na2B4O7) functions, G(K2B4O7),
    q11(NaB2O3.5,KB2O3.5) = **−4184**, triborate CEF (Na+,K+){B3O5−}, G(NaB3O5), G(KB3O5), q11(NaB3O5,KB3O5) = **10460 J/mol**.
    The malformed `−1286.12.208` coefficient and the duplicated `298-843.5K` interval of function 2 are carried as printed and
    qualified (no correction); the 843.5–844.5 K gap between functions 1/3 is noted. All coefficients machine-checked against the printed lines.
  - Item 10, Table A.6, pp.492–493 / PDF549–550: physical CSV lines unchanged; YAML adds `structured_expressions` with an unambiguous form
    of all four molar-mass fractions, e.g. `G(Na4Al22O35) = (M(Na4Al22O35)/M(Na2Al12O19)) * G(Na2Al12O19) + 96056.8 - 11.286*T` (p.492) and
    `... + 38414.9 + 8.368*T` (p.493), plus the private-use-glyph lines (Δg°, Δq°, g^01, g^30, CEF bracket glyphs) and a
    `glyph_legend`. Printed typos are qualified, not corrected: numerator `M(NaAl11O14)` for G(NaAl11O17) (both beta and beta'');
    two T^-2 terms in G(Na2Al12O19).

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 at /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml exists there)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**.
- `validate_literature_extracts.py --check-fidelity-match <abs corpus extract>`: OK: 1 extract file(s) valid.
- Corpus `tools/test_ledgers_valid.py`: 674 passed.

## Items done so far: 6/10 (review.md items 1, 2, 3, 4, 6, 10)
Remaining: 5 Tables 1.1–1.2; 9 activity standard states + reduction lineage; 8 per-study method facts; 7 numbered equations (1)–(138) + narrative quantities.
