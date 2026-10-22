# Gerasimov et al. (2005), Table 1 audit

**Source:** *Geological Society of America Special Paper 384*, pp. 351–366, DOI 10.1130/0-8137-2384-1.351. Table 1 is PDF p. 4 / printed p. 354.

## Machine text and render checks

The source `pdftotext-layout.txt` carries the born-digital table text. The available MinerU input is only chunk `p008-p008`; its converter reported zero tables. The staged converter `/private/tmp/claude-501/main-review/tools/mineru_table_draft.py` was used as instructed by steer 1. Its compare output is committed below: zero candidate cells because no table was extracted from that input.

The p. 4 render was checked for every stored numeric cell because the available MinerU output does not cover this page. The render confirms the row and column alignment. Confirmation method for each cell below is **(b), rendered PDF p. 4 (printed p. 354)**; pdftotext supplied the transcription. There are nine printed oxide rows. Each row's species assignment and all shown sample/column values were checked against the render.

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

Thus 62 numeric table cells (means and sigmas) and one censored `<0.10` cell are retained. Augite starting composition averages 18 analyses; obsidian starting composition averages 10. Blank cells are not imputed. Condensate Na2O/K2O are the alkali-shuttle rows; SiO2 is the SiO-evolution row.

## Table draft comparison

Command used the staged standalone converter on the available MinerU input. `compare` result: `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0`; its emitted CSV header had no data rows. This means no MinerU presence evidence exists for p. 4; the render checks above resolve every numeric cell.

## Migration and schema gap

The isolated real migrator produced one work, one experiment, and two condensate-composition observations. Both have their printed starting composition bound through the existing `composition_wt_pct` path to identity and point conditions. For both rows, identity `temperature_K`, `total_pressure_Pa`, and `fO2_Pa` are unknown, consistent with the task direction not to promote the abstract's global 3000–5000 K range to a row and to leave row pressure unknown. The global temperature statement is retained once in context.

The `condensate_composition` quantity token exists, but the migrator has no value-field mapping for this paper's printed condensate oxide wt% vector. It emitted unavailable values with reason `no mapped condensate_composition field selected`; the source vector, sigmas, censored MnO value, and row-to-rail notes remain in the extract. This is the schema gap; no schema or migrator change was made. Migration reported 1 work, 1 experiment, 2 observations, 0 registry issues, 0 registry hard issues, 6 queue entries, and 2 unavailable values. Corpus validation reported 14 `identity_incomplete` issues: for each observation the condensate-composition profile requires `per`, `fO2_Pa`, `reservoir`, `temperature_K`, `sweep_gas`, `total_pressure_Pa`, and `exposure`; none of those axes is a row-specific printed condition for Table 1. Each observation's starting melt composition reaches identity and the `composition` point condition through the existing `composition_wt_pct` path. Temperature, total pressure, and fO2 are unknown, as directed. The global temperature statement is retained once in context.

Focused page-pin and YAML-comma tests passed (18 passed); the repo extract validator passed as part of the 19-test focused run. The full `tests/test_literature_extracts.py` run was stopped at 275.51 seconds to honor the 300-second cap, after 148 tests passed; it did not finish. A fidelity mutation changing augite condensate SiO2 from 54.69 to 54.70 was detected by the checked-in sample.

Figure-only spherule/residue compositions are not extracted; those remain figure-read candidates. Tables 2–3 are natural Logoisk crater-rock context and are not extracted.
