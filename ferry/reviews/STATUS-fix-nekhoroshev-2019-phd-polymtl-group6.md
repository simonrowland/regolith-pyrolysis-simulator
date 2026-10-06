# STATUS: fix nekhoroshev-2019-phd-polymtl — group 6 (per-study method facts, item 8)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:26 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `30349a12c10ac8d5193046767d35936ee8daaf90`
(chain: cfaa7465 → b02bcc7b g1 → 2a9892ad g2 → a99679a4 g3 → 6c47c26b g4 → 9fb4916a g5 → 30349a12 g6)

## Groups covered
- g1–g5 as previously reported (6/6 mismatches; Tables 14.1/21.1/A.42/A.6/1.1/1.2; activity reference states + reduction lineage).
- g6 (this push), item 8. Extract only, one new context row `nekhoroshev_2019_per_study_method_facts` with 8 entries. Each entry has a verbatim statement, page locator and typed absences:
  - p.144 Meshalkin & Kaplun [334]: oscillation method + thermal analysis; sample preparation/reagents = unknown ("not described").
  - p.159 Roth: sealed iridium crucible (KAlO2 m.p.); Mo sealed crucible, K2O vapour rupture pressure reached at around 2260°C; the rupture-pressure value itself is not printed (unknown).
  - p.216 Zhang et al. [428]: starting materials SiO2, CaO, Na2CO3; carbonate-decomposition control = unknown (not mentioned by authors).
  - p.256 Greene & Bogue [443]: thermocouples frequently recalibrated (corrosion); short annealing times; calibration uncertainty and annealing time = unknown (not printed).
  - p.302 Rego et al. [435]: graphite crucible sharing gas atmosphere with a graphite-equilibrated Na2O-SiO2 reference melt; log a(Na2O(liq)) about −8.0 to −7.0; Na(g) via reaction (133). The lineage [435] paraphrase was re-verified and reworded to match the printed text.
  - p.385 Julsrud & Kleppa [610]: N2 atmosphere, 975±2°C, calibration reproducibility ±1%, NaBO2/B2O3 from Na2B2O4·2H2O/H3BO3 by calcining; calcining temperature = unknown ("high temperatures").
  - p.400 Rockett & Foster [649]: sealed Pt crucibles, equilibration 10 min–200 hr, X-ray + optical microscopy.
  - Figs 16.11 (p.250) and 22.7/22.8 (p.311): no reference state printed in the caption (typed unknown).
- Scope context: the shared `secondary_review…` anchor is replaced by per-field typed unknowns. These point to the new row, and orifice/channel length, ionization/cross-section/isotope corrections and chamber background pressure are `not_found_in_source` (text search: orifice 0, ionization 0, background/residual pressure 0 hits).
- Every statement and value was re-checked on 220 dpi page-image crops (PDF 201, 216, 273, 313, 307, 359, 368, 442, 457).

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**. Fidelity validator: OK: 1 extract file(s) valid. `tools/test_ledgers_valid.py`: 674 passed.

## Items done so far: 9/10 (review.md items 1, 2, 3, 4, 5, 6, 8, 9, 10)
Remaining: item 7, narrative quantities (pp.80, 159, 166, 172, 216, 400, 408, 431) and the numbered equations not yet carried. Ledger/coverage text update to follow in the final group.
