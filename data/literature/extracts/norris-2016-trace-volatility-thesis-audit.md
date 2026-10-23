# Norris (2016) Chapter 5 extraction audit

Source: C.A. Norris, *Volatilities of trace elements in silicate melts*, DPhil thesis, University of Oxford (2016), doi:10.5287/ora-qmdvbxydr. The primary inputs were the born-digital text layer and available MinerU table reads from the source corpus. The PDF was used read-only for render checks.

## Cell and row audit

The pdftotext layer supplies exact characters for these born-digital pages. I rendered each table page once to check the row and column assignment. No machine-text disagreement, flagged sign, exponent, comma-decimal, fused reference, or stacked-cell value was found. Table 5.7 also has a MinerU table read; its digits agree with pdftotext and its two side-by-side element blocks were checked against the page render. Tables 5.2, 5.4, 5.5, and 5.6 have no MinerU table block, so their cells were checked by method (b), using the pdftotext characters plus the rendered layout assignment.

| Table and PDF page | Printed rows reconciled | Stored numeric cells and row/column assignment | Confirmation |
|---|---:|---|---|
| 5.2, p. 220 | 27: 9 major oxides, 17 trace oxides, total | 54 values and σ cells; row headings SiO₂, TiO₂, Al₂O₃, FeO, MnO, MgO, CaO, Na₂O, K₂O; Ag₂O, Bi₂O₃, CdO, Cr₂O₃, Cu₂O, Ga₂O₃, GeO₂, In₂O₃, MoO₂, Ni₂O, PbO, Sb₂O₃, SnO₂, Tl₂O, V₂O₃, WO₃, ZnO; total | (b), rendered row/column assignment |
| 5.4, p. 224 | 10 runs: F006, F007, F008, F010, F012, F013, F014, F015, F018, F019 | 30 repeated T/duration/log fO₂ numeric cells; each value assigned to its run heading | (b), rendered row/column assignment |
| 5.5, p. 226 | 10 rows: SiO₂, TiO₂, Al₂O₃, FeO, MnO, MgO, CaO, Na₂O, K₂O, total; mean over 9 experiments | 30 mean, σ, and RSD cells, plus printed Num. Points=106 and Num. Experiments=9 | (b), rendered row/column assignment |
| 5.6, p. 227 | 10 run rows × 16 elements = 160 observations | 320 concentration and σ cells; first block Ag, Bi, Cd, Cr, Cu, Ga, Ge, In; second block Mo, Pb, Sb, Sn, Tl, V, W, Zn; every row assigned to F006–F019 headings | (b), rendered row/column assignment |
| 5.7, p. 232 | 4 log fO₂ rows × 16 elements = 64 ratios | 128 ratio and σ cells, plus 4 log fO₂ row labels; first block Ag through In, second block Mo through Zn | (a), MinerU and pdftotext digits agree; rendered layout confirms both blocks and row headings |

Total: 568 numeric cells (including the two Table 5.5 footer counts). Table 5.4’s row/date, temperature, duration, and oxygen-fugacity assignment was confirmed against the render; the date labels are retained as printed text.

The required staged standalone table-draft converter and compare were used unchanged. They produced 10 MinerU tables, including Table 5.7 as p232-t09 and p232-t10; the converter also found unrelated Table 4.15 on p170, excluded here. The compare output is:

```text
compare: matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0
species,observation_id,point_index,pdf_page,value,status,matched_cell,draft_candidate_count,path
```

This compare implementation only traverses a short list of standard observation fields under `species.*.observations`; it does not recognize the extract’s `residue_ppm` concentration-series field or context-table values. Therefore the empty result is a tool coverage gap, not a claim of cell matching. The row/column render checks above are the assignment evidence. No extractor tool was added to the branch.

## Representation and migration

- Table 5.2’s nine major-oxide values are on each EBT1 experiment sample as the starting composition. The context row retains their printed standard deviations, the reported total, and Ni₂O at 98 ± 7 µg/g. The other 16 trace concentrations are admitted `residue_component_composition` observations under the printed oxide species (Ag₂O through ZnO), with the Table 5.2 locator and printed value and σ. No oxide concentration is reassigned to a bare element or converted to an elemental concentration. This keeps the starting material as one work.
- Table 5.4 supplies 10 linked experiment records, each at 1300 °C with its printed duration and absolute log fO₂. A separate EBT1 starting-glass preparation record retains the printed preparation conditions. The furnace is stated to operate at one atmosphere on PDF p. 196, §5.2; pressure locators use that page and section, with no table locator. It uses a CO/CO₂ mix; the source does not tabulate its gas flow ratio. The wetted material is nickel.
- Table 5.6 supplies 160 measured residual-glass component-composition observations, one per printed element/run cell. Each carries its printed σ (confidence level not stated), locator p. 227, T, log fO₂, 1 atm pressure, starting melt composition, and run link. Products were dropped into a water bucket on PDF p. 222, §5.4.3; whether quenching preserves trace composition is not stated, recorded on every row. The measured state is glass.
- Table 5.7 prints the dimensionless concentration ratio X_final/X_initial in mole fraction single-cation oxide, not a mass-loss fraction. All 64 cells are admitted `element_fraction_remaining` observations, with no `normalising_element`. Ratios and printed σ values remain as printed. Each author-derived row cites Equation 5.1 and links both its Table 5.6 final-concentration observation and its Table 5.2 starting oxide-concentration observation through `derived_from`. The authors’ single-cation-oxide basis conversion is recorded in the derivation; no converted duplicate ratio is stored.
- Table 5.5 is one nine-experiment major-element mean context row only; no run-level values are inferred.
- A source-relationship context at PDF p. 217 (printed p. 187; `pdf_page_index: 216`, §5.4.1) links the thesis to `norris-2017-earth-volatiles-nature`, whose extract has the ED Table 1 starting row and figure-only volatility factors. The relationship is the extract-level dedup note; no table locator is asserted. The EBT1 row is not duplicated as an independent work.
- Table 4.15, p. 170 (activity coefficients at 1923 K), is out of scope and is a follow-up candidate. Figure-only readings, including Figure 5.16 time-series values and Figure 5.17 volatility-factor curves, are not transcribed.

Isolated real-migrator result: 1 work, 0 benches, 11 experiments, 240 observations, 3 context rows, 0 hard issues, 0 identity-unknown issues, and 0 unavailable observations. This includes 160 Table 5.6 residue rows, 64 admitted Table 5.7 fraction rows, and 16 admitted Table 5.2 oxide parents. Temperature (1573.15 K), composition, and total pressure (101325 Pa) map to identity on the Table 5.6 residue rows; the pressure locator is PDF p. 196, §5.2. The migrated per-row `fO2_log` locator is Table 5.4, PDF p. 224. The source prints log(fO₂) relative to atm (PDF p. 250, “log(fO₂) of −7 atm”), stored as `log10(atm)` so a later binder does not apply the bar reference. Identity `fO2_Pa` remains unknown on this green seat because no current extract path binds absolute log fO₂ for this quantity. Controller ruling t-1360 says the existing point condition is correct and identity binding will be available after its `oxygen_identity.py` change lands; no converted twin is stored.

Identity `subtype` remains unknown on the scalar Table 5.6 `concentration_series` rows with `values.residue_ppm`; the existing `element_ppm_by_mass` subtype stamp is only applied by the migrator’s series-point path, and no scalar extract field binds this subtype. This separate gap is unchanged by the Table 5.7 retype.

Expected store delta, without regenerating or committing the battery store: +1 work, +0 benches, +11 experiments, +240 observations, and +3 context rows. The 64 fractions and 16 starting oxide observations are admitted typed observations; no Table 5.7 context duplicate remains.
