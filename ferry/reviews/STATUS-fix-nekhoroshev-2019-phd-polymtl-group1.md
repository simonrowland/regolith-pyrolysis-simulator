# STATUS: fix nekhoroshev-2019-phd-polymtl — group 1 (mismatches)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:10 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Started from:** `cfaa74654d6e307efba4c3b01a7af430e0963f26` (verified = origin tip before work)
**Pushed full tip after this group:** `b02bcc7b3cdbbe95e32490168108126a3852f022`

## Groups covered
Group 1 = the 6 mismatches of review.md (items 4 and 6).

- Item 4, Table 8.1, p.127 / PDF184 (re-read at 220 dpi): CSV and YAML rows 1,3,5,7 — 1520, 1414, `498 (metastable)`, 1120 °C moved from
  `Experimental_T_degC` to `Calculated_reference_T_degC` (they accompany 42.56, 55.42, 44.93, 57.94 kJ/mol). Experimental 1526 °C / 43.4±6 [47]
  unchanged; other experimental temperatures stay blank (print blank). The whitespace-only cell in row 1 was cleared. Per-study reduction
  provenance from the paragraph above the table added (`reduction_provenance`: [47] drop calorimetry; [314] devitrification-enthalpy
  extrapolation with heat capacities; [315] similar calculation from Navrotsky data). Provenance note updated.
- Item 6a, Table 24.1: YAML locator and `t24.1.provenance.yaml` `published_page` 300 → **320** (caption+table on p.320 / PDF377, verified).
- Item 6b, scope/method context: locator now `page: vi`, Abstract (PDF7); `experiment_method` locator → p.5 / PDF62 with the
  quoted no-experiments sentence; that sentence also carried as `no_author_experiments_statement` with its locator.

Mismatches fixed: 6/6 (4 row-binding + 2 locator).

## Checks (Mac, green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 at /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml exists there)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0** (0 observations, context-only as before).
- `tools/validate_literature_extracts.py --check-fidelity-match <abs corpus extract>`: OK: 1 extract file(s) valid.
- Corpus `tools/test_ledgers_valid.py` (pytest -q -p no:cacheprovider): 674 passed.

## Items done so far: 2/10 (review.md required changes 4 and 6)
Remaining (next groups, in order): 1 Table 14.1 continuation p.222; 2 Table 21.1 continuations pp.301–302; 3 Table A.42 p.529;
5 Tables 1.1–1.2; 10 Table A.6 fraction semantics; 9 activity standard states + reduction lineage; 8 per-study method facts;
7 numbered equations (1)–(138) + narrative quantities.
