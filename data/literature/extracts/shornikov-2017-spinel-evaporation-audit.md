# Shornikov 2017 spinel evaporation: extraction audit

Source: Shornikov, “Mass Spectrometric Study of the Evaporation of MgAl2O4 Spinel,” *Russian Journal of Physical Chemistry A* 91(1), 10–16 (2017), DOI 10.1134/S0036024417010241. Extract: [shornikov-2017-spinel-evaporation.yaml](shornikov-2017-spinel-evaporation.yaml).

## Machine reads and full-cell audit

Transcription used the born-digital `pdftotext-layout.txt`, MinerU markdown, and MinerU `content_list.json` table blocks. The MinerU converter mapped chunk `p001-p007`, `page_idx` 1 to physical PDF page 2 and `page_idx` 3 to physical PDF page 4. The machine reads agree on the printed values. Per the controller’s born-digital rule, each table layout was checked against its page render to confirm row and column placement. Every cell in the following row/column spans was visually confirmed on the indicated render, basis **(b)**:

| Physical PDF page | Printed page | Table | Printed row | Cells checked | Basis |
|---:|---:|---:|---:|---|---|
| 2 | 11 | 1 | 1–8 | c1–c7 | (b), page render |
| 4 | 13 | 2 | 1–12 | c1–c8 | (b), page render |
| 4 | 13 | 3 | 1–12 | c1–c6 | (b), page render |

The Table 1 columns are method, container/suspension, temperature range, MgO mol%, gas components, reported results, and reference. Its printed row count is **8**. Table 2 has **12** printed rows: c1 temperature; c2–c8 Mg, MgO, Al, AlO, Al2O, O2, MgAlO. Its 84 species cells comprise 73 numeric pressures and 11 printed dashes; including temperature, 85 numeric cells were checked. Table 3 has **12** printed rows: c1 temperature; c2–c6 Eqs. (8),(9), (14), (15), (16), and (17). Its 60 equation cells comprise 58 numeric pressures and 2 printed dashes; including temperature, 70 numeric cells were checked. No row/column assignment mismatches remained. No OCR redo was needed.

The committed [MinerU comparison table](shornikov-2017-spinel-evaporation-mineru-compare.csv) contains one row for each numeric pressure point and its matched MinerU cell. The comparator resolved both source-specific hazards: scientific notation appears as LaTeX `\times 10^{n}` in MinerU, and Tables 2 and 3 share physical page 4. It honors the extract’s table locator and reports **131 matched, 0 mismatched, 0 unmatched** numeric pressure cells. The extract retains pressures in atm and their printed notation; the comparator normalization does not alter extracted values.

## Extraction decisions and limits

- Table 2 contains the authors’ KEMS pressure series and is stored as `measured_direct`, in atm. The rows are for stoichiometric solid MgAl2O4, 50 mol% MgO and 50 mol% Al2O3, evaporated from a molybdenum Knudsen container. Its tabulated temperatures are 1851–2089 K. The paper’s broader 1850–2250 K experiment includes higher-temperature measurements on Al2O3-enriched spinel solid solutions; those are not substituted for the tabulated stoichiometric rows.
- Printed dashes remain unavailable values, never zero: 11 Table 2 cells and the two 1851/1859 K Eq. (16) cells. Table 3 Eq. (8),(9) rows are `measured_reduced`; the source does not tabulate the raw ion-count/time and apparatus inputs. Eq. (14)–(17) rows are `model_derived`, retain each equation number, cite thermochemical reference [10], and name their stored Table 2 pressure-series parents. Eq. (16)’s two unavailable rows retain its equation and the unavailable Table 2 pAl2O inputs as parents.
- No numeric total pressure or oxygen fugacity is printed for these runs, so both identity axes remain unknown. The experiment and every observation retain the printed composition and temperature conditions. The sample phase is solid spinel throughout the tabulated range, below the brief’s approximately 2378 K melting point; it is not stored as a melt.
- Table 1 is preserved as eight `quoted_attributed` metadata context rows, including the printed apparatus/container and temperature ranges, composition, gas components, results, and reference numbers. No pressure point is inferred from those study summaries.
- The paper discusses interaction of oxygen with W/Mo effusion-container materials and reports that a corundum film can slow spinel evaporation above about 2100 K. It provides no separately tabulated “Mg-crown” or “spinel-liner” series. Figure 2a’s pMg-over-MgO and pMg-over-spinel curves, the other Figure 2 spinel/simple-oxide pressure trends, and Figure 1’s composition trajectory remain figure-read candidates; no figure values were digitized.

## Verification

The real migrator was run on this extract in isolation: **144 observations**, **8 context rows**, **13 unavailable values**, **0 hard validation issues**, and **0 registry issues**. The identity profile was checked for all observations; three example rows were also printed during migration:

| Species and table cell | Composition in identity | Total pressure | fO2 | Temperature |
|---|---|---|---|---:|
| Mg, Table 2 row 1 | MgO 0.5, Al2O3 0.5 | unknown: not printed | unknown: not printed | 1851 K |
| MgAlO, Table 2 row 12 | MgO 0.5, Al2O3 0.5 | unknown: not printed | unknown: not printed | 2089 K |
| O, Table 3 row 3, Eq. (14) | MgO 0.5, Al2O3 0.5 | unknown: not printed | unknown: not printed | 1914 K |

Three independent fidelity pins target rendered cells on physical PDF page 4. Mutating each stored pressure causes its pin to fail. Verification command, using the repository `.venv`, `PYTHONPATH` set to this seat, `MPLCONFIGDIR=/private/tmp/claude-501/main-review/mplcache`, and pytest `-n 0`:

```text
python -m pytest -n 0 tests/test_shornikov_2017_spinel_evaporation_extract.py tests/tools/test_mineru_table_draft.py tests/test_yaml_flow_scalar_commas.py
27 passed
```

The expected battery store delta is **+144 observations and +8 context rows**. No store regeneration or store-file commit was performed.
