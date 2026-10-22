# Gerasimov et al. (2005), Table 1 extraction audit

**Source:** *Geological Society of America Special Paper 384*, pp. 351–366, DOI 10.1130/0-8137-2384-1.351. Table 1 is PDF p. 4 / printed p. 354.

## Machine text and render checks

The born-digital `pdftotext-layout.txt` supplies the table transcription. Available MinerU input covers only chunk `p008-p008`; its table converter reported zero tables. The staged table-draft utility was used for the presence comparison; there were zero candidate cells. The PDF p. 4 render confirms row and column assignments and every printed Table 1 numeric cell. Confirmation method for each cell is **(b), rendered PDF p. 4 (printed p. 354)**. There are nine printed oxide rows; every shown sample and column value was checked against the render.

| Printed row | Augite starting | Augite condensate | Obsidian starting | Obsidian condensate |
|---|---:|---:|---:|---:|
| SiO2 | 49.29 ± 0.10 | 54.69 ± 0.90 | 57.90 ± 0.19 | 62.16 ± 0.86 |
| TiO2 | 1.13 ± 0.03 | 0.74 ± 0.08 | 1.32 ± 0.09 | 1.13 ± 0.55 |
| Al2O3 | 9.98 ± 0.09 | 7.57 ± 0.48 | 15.02 ± 0.15 | 12.56 ± 0.64 |
| FeO | 8.22 ± 0.10 | 8.11 ± 0.36 | 9.31 ± 0.18 | 5.27 ± 0.66 |
| MgO | 13.09 ± 0.08 | 12.06 ± 0.69 | 5.11 ± 0.10 | 6.58 ± 1.39 |
| CaO | 15.46 ± 0.11 | 12.33 ± 1.28 | 7.37 ± 0.10 | 4.23 ± 0.85 |
| MnO | 0.07 ± 0.02 | <0.10 (no sigma printed) | blank | blank |
| Na2O | 2.75 ± 0.14 | 4.51 ± 0.28 | 2.99 ± 0.09 | 7.25 ± 0.83 |
| K2O | blank | blank | 0.53 ± 0.04 | 1.08 ± 0.36 |

The extract retains 62 numeric table cells (means and sigmas) and one censored `<0.10` cell. Augite and obsidian starting compositions average 18 and 10 analyses, respectively. Blank cells are absent, not zero. Condensate Na2O/K2O are the alkali-shuttle rows; SiO2 is the SiO-evolution row. The starting-composition point-condition locators identify the corresponding starting-sample columns.

## Conditions and migration

PDF p. 3 / printed p. 353 states that the laser experiments used an atmosphere of helium kept at a pressure of 1 atm. Each Table 1 observation records `total_pressure_atm: 1`, with that sentence's page and section locator; one context record retains the printed helium-atmosphere statement. The abstract's 3000–5000 K impact-vaporization context remains recorded once. A separate methods context record captures the estimate that vaporization temperature was 4000–5000 K. Neither range is promoted to a per-row temperature, and no midpoint is inferred. Oxygen fugacity is not stated.

The isolated real migrator produced one work, one experiment, two observations, and three context rows, with zero registry issues and zero hard issues. Existing pressure conversion maps the printed 1 atm to identity/experiment total pressure of 101325 Pa. Observation temperature remains unknown because the source gives a range, not a row-specific point. Migration reported 12 advisory `identity_incomplete` issues: the condensate-composition identity profile still lacks reservoir, temperature, sweep gas, exposure, fO2, and `per` for each observation. There are six queue entries: missing phase, missing method, and unavailable value for each observation. Phase and experiment method remain unknown because the paper's wording does not justify a closed token. The two condensate oxide vectors remain unavailable because the migrator has no `condensate_composition` field mapping. No schema or migrator change was made.

The unavailable value is a schema gap, not a zero. Starting oxide compositions, uncertainties, censored MnO, and blank cells remain as printed. No battery store was regenerated or committed; the next store build is expected to add two observations and three context rows, with pressure bound from the printed atmosphere.

## Verification

The focused Gerasimov extract test and YAML comma guard passed (19 passed). A mutation probe confirmed that the focused test rejects changing the printed pressure, deleting the 4000–5000 K methods record, and restoring either observation's false temperature reason (4 mutation cases passed). The repo-wide `test_repo_extracts_validate_green` passed. The full `tests/test_literature_extracts.py` file passed 553 tests in 262.54 s before the final locator-note clarification; a rerun after that metadata-only clarification was interrupted at 148 passed after 428.99 s, so no completed post-clarification full-file result is claimed.

## Other source material

Figure-only spherule and residue compositions are not extracted and remain figure-read candidates. Tables 2–3 describe natural Logoisk crater rocks and are context only.
