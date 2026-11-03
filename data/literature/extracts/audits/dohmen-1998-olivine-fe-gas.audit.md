# Dohmen et al. (1998), olivine–Fe gas-mediated reaction: extraction audit

## Scope and source

This is a new extract for the olivine–Fe vapor-transport study. It adds no
measurements from Dohmen et al. (2003); that extract covers the distinct SC1–SC6
series. The 1998 paper reports three 1-atm experiments and seven Knudsen-cell
runs. Table 1 has seven printed rows: V892, V893, V914, V929, V932, V933, V937.

The machine inputs were the corpus MinerU table/markdown and the full
`pdftotext-layout.txt`. The source PDF SHA-256 is
`d99c7803f5940fb365c74fe0c13f824d2b5654de42ef6a92e70c279d6aa5e12b`.
The PDF was used only through `pdftotext` and page renders. No supplement
directory exists for this source.

## Full-cell audit

Route **(a)** means MinerU and `pdftotext` agree on the digits, sign, exponent,
and row/column. Route **(b)** means a page render resolved absent or malformed
machine text, a flagged cell, or row/column placement. Locators below use
absolute PDF pages; the printed folios are 970 plus the PDF page minus one.

### Table 1, PDF page 4 / printed page 973

Printed count: **7 body rows**. The MinerU table draft found zero body rows and
classified the two headers plus all seven data rows as headers. Its QA therefore
had zero numeric cells; that is a layout warning, not cell verification. The
born-digital `pdftotext` layer garbled several subscript digits and row/column
associations. The page 4 render settled every row and column, so Table 1 stored
cells use route (b). The independent render transcription is pinned by the
focused test.

| Printed row | Pos. A | Pos. B | Thermal history | Experimental product | Audit |
|---|---|---|---|---|---|
| V892 | Fe + Fo100 (synthetic, polycrystalline) | — | Heat to 1400 °C twice at 5 °C/min; anneal 1400 °C for 24 h | Fa6Fo94; homogeneous grains | (b), page 4, row V892, columns 1–5 |
| V893 | Fo100 (synthetic, polycrystalline) | — | Heat to 1500 °C at 5 °C/min | Fo100; single phase | (b), page 4, row V893, columns 1–5 |
| V914 | Fe (synthetic, polycrystalline) | Fo100 (synthetic, polycrystalline) | Heat to 1400 °C twice at 5 °C/min; anneal 1400 °C for 19 h | Fa2Fo98–Fa4Fo96; individual grains unzoned | (b), page 4, row V914, columns 1–5 |
| V929 | Fe + wüstite (synthetic, polycrystalline) | Fo100 (synthetic, polycrystalline) | Heat 1400 °C at 5 °C/min; anneal 22 h; heat 1400 °C at 5 °C/min | Fa26Fo74; individual grains unzoned† | (b), page 4, row V929, columns 1–5 |
| V932 | Fe + wüstite (synthetic, polycrystalline) | San Carlos olivine (single crystal) | Heat to 1400 °C at 5 °C/min; anneal 20 h | Fayalitic films† | (b), page 4, row V932, columns 1–5 |
| V933 | Fe (synthetic, polycrystalline) | Fo90 + Fo100 (synthetic single crystals) | Heat to 1400 °C at 5 °C/min; anneal 23 h | Fayalitic films | (b), page 4, row V933, columns 1–5 |
| V937 | Fe + magnetite (synthetic, polycrystalline) | Fo100 (synthetic, polycrystalline) | Heat to 1400 °C at 5 °C/min; anneal 20 h | Molten sample: Fa74Fo26† | (b), page 4, row V937, columns 1–5 |

The table footnotes are “Always cooled at 20 °C/min” and “Spinel formation on
alumina crucible cell.” V937's product is kept in the table grid while the
actual measured phase is clarified: the table calls it molten, but says all
samples were characterized after the experiments. It does not directly state
whether this assay was on quenched glass or was an in-situ liquid value.

The starting materials are recorded per run in Table 1 positions A and B.
These are separate solids in a diffusion couple, not a printed bulk melt
composition; no single mixture or proportion is reported. They remain as
row-specific table context instead of an invented composition condition.

The Table 1 heating rates are bound into each KEMS experiment's thermal
schedule as located ramps. The printed 5 °C/min is stored as 0.083333333333333333
K/s; start temperatures and ramp durations are left absent because the paper
does not state them. The schedules retain two ramps each for V892, V914, and
V929, and one each for V893, V932, V933, and V937.

### Numeric prose and conditions

| Cell / statement | PDF page and row | Verification |
|---|---|---|
| 1-atm diffusion couple: 1300 °C, fO2 = 10^-12 bar, 115 h; CO+CO2 flowing gas; pressure stated as 1 atm | 2, section “1 atm experiments” | (b), page 2 render; the temperature, exponent, duration, and pressure were checked together |
| Thin-Fe-foil repeat uses the conditions above and gives the same result | 3, Interpretations | (b), page 3 render; this is a repeated run, not another independent composition point |
| Fe metal + forsterite powder test: 1600 °C, fO2 = 10^-11 bar; duration and gas ratio are not stated | 3, Interpretations under “1 ATM EXPERIMENTS” | (b), page 3 render; no duration or gas ratio was invented |
| KEMS cell and heater were in 10^-8–10^-10 bar chamber vacuum | 3, Knudsen-cell experiments / Setup | (b), page 3 render; this is chamber background, not a measured in-cell sample total pressure |
| Gas species become detectable above about 1100 °C; Mg signal is observable at 1100–1400 °C | 5, Results / Gas phase composition | (b), page 5 render |
| Dominant gases are SiO, O2, O, Mg; SiO is identified by isotope peaks during later 1400 °C annealing after CO2 degassing | 5, Results / Gas phase composition | (b), page 5 render; no plotted partial-pressure point was digitized |
| At 1400 °C the iron–wüstite buffer pO2 is approximately 2.0×10^-10 bar; KMS O2 sensitivity is about 10^-10 bar; O2 was not detected for V892, V914, or V929 | 6, Results / Gas phase composition | (b), page 6 render checks the exponents and run names; the buffer value is not an O2 KMS measurement |
| Single-crystal Fe-rich olivine films reach up to 300 μm | 7, Single-crystal experiments | (b), page 7 render; retained as measured numeric context because no exact thickness quantity/type exists |
| V937 is Fa = 70 mol% in prose; Table 1 prints Fa74Fo26 | 6 and 4, respectively | (b), pages 6 and 4 renders; both forms are kept, not reconciled by replacing the table cell |

The reported 60 μm diffusion profile is explicitly attributed to Chakraborty
(1997), not to this paper's experiment, so it is not attributed to Dohmen et al.
The plotted profiles, gas pressures, ion-current ratios, and model curves in
Figures 1–12 remain figure-only candidates and were not digitized. MinerU
`<details>` chart blocks were ignored. No model-fit values are stored.

## Typed rows and schema gaps

Four Table 1 product observations (V892, V893, V914, V929) preserve the printed
olivine endmember strings as measured-direct typed refusals. Their migrated
phase is `cr`, temperature is known, and quantity is unknown. The exact missing
quantity is non-equilibrium crystalline olivine endmember composition:
`composition_series` is admitted by
`tools/validate_literature_extracts.py::OBSERVATION_TYPES`, but it has no
mapping in `simulator/battery/migrate.py::TYPE_QUANTITY`, and no exact alias in
`simulator/battery/migrate.py::QUANTITY_ALIASES`. The closest candidate,
`Quantity.EQUILIBRIUM_PHASE_COMPOSITION`, describes equilibrium phase data;
these products are not asserted to have reached equilibrium. Its phase-row
reader also uses the closed `PhaseRole` set, which has no olivine role. The
`PHASE_BASIS_TOKENS` set is a basis vocabulary, not a quantity token. The
`park-2021-vanadium-thermodynamics-thesis.yaml` precedent is explicitly
equilibrated Pt–V metal and does not type these post-run olivine products.

The measured “up to 300 μm” film thickness is retained as context with a
schema-gap note: the closed observation types and quantity tokens do not
provide a thickness measurement. It is not relabelled as concentration or
pressure. V937 is preserved in the Table 1 grid but not yet emitted as an
observation until its measured phase is settled.

## Isolated migration

The real migrator on this extract produced **4 observations, 0 hard issues, and
4 unavailable values**. All four observations have known `cr` phase and the
printed temperature reaches both `identity.temperature_K` and the located
`point_conditions.temperature_K`. Their linked per-run experiments retain the
printed fO2 state, chamber pressure interval, and complete published ramp
counts/rates. The source observation rows retain located fO2 and sample-pressure
states too; the migrator exposes only temperature in `point_conditions` for
these typed refusals. The sample-pressure state stays unknown, and the
chamber-background interval is separately identified in the linked experiment.
The identity quantity,
composition, total pressure, and fO2 remain unknown because these rows are
typed-refused for a quantity with no exact schema token; the conditions are
available through their experiment references. The 1-atm experiment records
retain 1300 °C, 10^-12 bar, 1 atm, and 115 h; unknown gas ratio and the absent
duration on the 1600 °C test remain unknown.

Table 1's V937 Fa74Fo26 cell is in the full seven-row context record. The
expected store delta before its phase ruling is one work record, ten
experiment records, four context records, and four typed-refused observations;
no generated store files are committed.

For the first three migrated observations, the identity axes print as:

| Run | Composition | Total pressure (Pa) | fO2 (Pa) | Temperature (K) |
|---|---|---|---|---:|
| V892 | unknown (no single starting-mixture composition) | unknown | unknown (below Fe–FeO buffer, no absolute point) | 1673.15 |
| V893 | unknown (no single starting-mixture composition) | unknown | unknown (not reported) | 1773.15 |
| V914 | unknown (no single starting-mixture composition) | unknown | unknown (below Fe–FeO buffer, no absolute point) | 1673.15 |

These `composition_series` rows have no quantity profile because the quantity
is typed-refused, so the identity axes other than temperature are unavailable
to the profile. The run-linked experiment records retain the reported
conditions: V929 and V932 have the printed Fe–FeO buffer pressure of
approximately 2.0×10^-10 bar; V892 and V914 retain the below-buffer statement;
the remaining runs have no absolute fO2 point. All seven Table 1 positions A
and B remain tied to their run IDs in the full grid. The only reported total
pressure for the KEMS work is the chamber background interval; the extract
does not promote it to an in-cell sample pressure.

The table draft compare command completed with `matched=0`, `mismatch=0`, and
`total=0`: its seven Table 1 records are prose cells which the numeric compare
tool does not match. The render transcription and row count are therefore
verified independently in the focused test. The comma-scalar YAML guard passed
all 17 tests.

The focused migration test passed all three cases, including its deliberate
Table 1 product mutation check. The extract-specific literature validator
passed, and the repo-wide fidelity-sample parameterization passed all 637
cases. This extract's fidelity case was rerun after its final edits and passed.
The full `tests/test_literature_extracts.py`
run and the all-extract validator case exceeded their 300-second local caps;
the repository suite is **UNVERIFIED-local** beyond the passing filtered
cases. A filtered general slice recorded 157 passing cases before it was
interrupted; the remaining selected cases are unverified.
