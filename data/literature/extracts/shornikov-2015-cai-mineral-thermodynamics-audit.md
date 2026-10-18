# Shornikov and Yakovlev (2015) extraction audit

## Source and duplicate check

- Citation: Shornikov and Yakovlev (2015), “High-Temperature Thermodynamic Properties of CAI Minerals,” 46th Lunar and Planetary Science Conference, abstract 1270. No DOI is printed.
- PDF: `/Users/simonrowland/Repos/.regolith-main-review/corpus-main/raw/shornikov-2015-cai-mineral-thermodynamics/shornikov-2015-cai-mineral-thermodynamics.pdf` (the preferred raw path was absent; this older corpus location contains the source).
- Authoritative layout text: `/Users/simonrowland/Repos/regolith-corpus/text/shornikov-2015-cai-mineral-thermodynamics/pdftotext-layout.txt`. MinerU table draft: page 2, Table 1.
- The duplicate search covered the title and author/year across `data/literature` and found no extraction of this paper under another source ID. Existing Shornikov 2019 records cite it as reference [26].

## Table 1 transcription

Table 1 is on PDF page 2. The final extract contains 51 numeric observations: 12 each for ΔH_T, ΔS_T and ΔH_melt, plus 15 transition temperatures. Values are quoted from references [11–17], as printed. The ΔH_T and ΔS_T entries are the parameters used in ΔG_T = ΔH_T − TΔS_T under the stated limited-interval constancy assumption. The source describes the underlying high-temperature data as above 1000 K; that lower bound is recorded on the 24 ΔH_T/ΔS_T rows. No upper bound for the coefficient interval is printed.

The printed entropy unit `J/(mole·K)` is normalized to the migrator-supported equivalent `J/mol K`; the exact printed header remains in each observation locator. Numeric values are unchanged. The table's CAI mineral shorthand is retained in `row_label`; formula keys are cross-checked against the source's Figure 1 mineral names and Shornikov's formula-based CAI extraction in `shornikov-2019-lunar-meteorite-evaporation-model.yaml`.

| Table row | Mineral (Figure 1) | Formula key | ΔH_T (kJ/mole) | ΔS_T (J/(mole·K)) | T_melt (K) | ΔH_melt (kJ/mole) |
|---|---|---|---:|---:|---:|---:|
| CA | calcium aluminate | CaAl2O4 | −8.91 | 11.10 | 1877 | 31.90 |
| CA2 | grossite | CaAl4O7 | −6.34 | 9.94 | 2035 | 27.00 |
| CA6 | hibonite | CaAl12O19 | −2.88 | 5.00 | 2123*; 2148** | 16.80 |
| CT | perovskite | CaTiO3 | −39.51 | 2.87 | 2243 | 96.68 |
| MA | magnesium spinel | MgAl2O4 | −13.29 | 4.38 | 2408 | 100.30 |
| MS | enstatite | MgSiO3 | −10.28 | −0.07 | 1836*; 1850** | 25.93 |
| M2S | forsterite | Mg2SiO4 | −21.76 | −0.92 | 2171 | 38.00 |
| CMS2 | diopside | CaMgSi2O6 | −36.55 | −3.15 | 1665 | 34.43 |
| C2MS2 | akermanite | Ca2MgSi2O7 | −36.43 | −2.41 | 1727 | 24.78 |
| C2AS | gehlenite | Ca2Al2SiO7 | −37.33 | 2.11 | 1863 | 43.03 |
| CAS2 | anorthite | CaAl2Si2O8 | −26.70 | −1.10 | 1830 | 33.30 |
| C3AS3 | grossular | Ca3Al2Si3O12 | −47.70 | −10.98 | 1373*; 1673** | 71.00 |

`*` is the dissociation temperature and is assigned the crystal phase; `**` is the liquid-phase temperature and is assigned the liquid phase. Both values from each composite T_melt cell are separate quoted transition observations. For ΔH_melt, the liquid-phase temperature is carried as T_fus_K when the table gives two temperatures.

## Cell evidence and comparison

The page is born-digital. For the 45 unflagged numeric cells, MinerU and pdftotext agree on the digits and signs. Their row and column assignments were checked against the full page render at 600 dpi. The remaining three T_melt cells were flagged because MinerU concatenated each pair (`2123*2148**`, `1836*1850**`, `1373*1673**`); each cell was checked against that render, which confirms all six values and footnote marks.

`shornikov-2015-cai-mineral-thermodynamics-mineru-compare.csv` records 45 matched values and six mismatches. Those six compare mismatches are the two values per flagged composite cell that the draft CSV stores as one nonnumeric string; the render checks above resolve them. There are no unmatched observations.

The table mapping emitted 45 observations with zero skipped blank/dash cells. Six transition values from the three composite T_melt cells were hand-transcribed after the render checks. Render checks: 3 cells.

## Figure 1 context

Figure 1 is a single panel on PDF page 2 with twelve numbered, unmarked curves. Its caption lists the same mineral series as the table. The x-axis is `T, K` (ticks 1000, 1200, 1400, 1600, 1800, 2000, 2200); the y-axis is `ΔG_T, kJ/mole` (ticks −10, −20, −30, −40). The plotted range is 1000–2200 K. The figure does not identify a phase convention or an external attribution in the caption. It is represented once in each mineral's sibling `context[]`; every record has zero markers and `digitisation: deferred to a figure pass`. No numeric figure points were digitised.

## Isolated migration evidence

The isolated migration admitted 51 observations and 12 context rows with zero hard issues. All 51 observations carry a located `point_conditions.temperature_K`: the 24 ΔH_T/ΔS_T records carry the printed `>1000 K` lower bound, the 15 transition records carry their reported transition temperature, and the 12 ΔH_melt records carry the corresponding liquid-phase temperature. The migration reported no unsupported units and no unmapped phase strings.

| Observation | Quantity | composition | total_pressure_Pa | fO2_Pa | identity temperature_K | point temperature condition |
|---|---|---|---|---|---|---|
| CaAl2O4, Table 1 ΔH_T | delta_fH | not applicable | not applicable | not applicable | unknown: no single T | >1000 K, page 1 Introduction |
| CaAl12O19, dissociation | transition_temperature | not applicable | unknown: no numeric pressure printed | not applicable | not applicable | 2123 K, page 2 Table 1 |
| Ca3Al2Si3O12, ΔH_melt | heat_of_fusion | not applicable | not applicable | not applicable | 1673 K | 1673 K, page 2 Table 1 |

The source prints no numeric total pressure, fO2, or melt composition for these table rows. The oxide/mineral identity is carried by each formula key. Transition temperature itself is the observation, so the identity profile marks its temperature axis not applicable while the located point condition carries the printed T_melt value.

## Verification and expected store delta

After restoring the mutation, the focused transcription, YAML flow-scalar, and table-draft suites passed: **45 passed**. A deliberate mutation from `-8.91` to `-8.92` made the focused transcription test fail on its literal source-value pin, then the original value was restored.

The full combined run collected 624 tests: **621 passed, 3 failed**. The three failures are global validators blocked by two `recalculation_table` types already present in the unchanged base revision's `cottrell-2009-iron-xanes.yaml`; the failure report names no row in this Shornikov extract. The two Cottrell types are present at the starting commit and that file is untouched.

No battery store regeneration was committed. Expected store delta on regeneration: **+51 observations and +12 context rows**.
