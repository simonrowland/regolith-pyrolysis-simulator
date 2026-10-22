# Sindland & Tangstad (2021) extraction audit

Source: `sindland-2021-sio-production-quartz-silicon`, DOI 10.1007/s11663-021-02143-4. The paper is born digital. Its exact-text input is `/Users/simonrowland/Repos/regolith-corpus/text/sindland-2021-sio-production-quartz-silicon/pdftotext-layout.txt`. No `mineru/` or `ocr-mineru/` directory exists in either corpus location, so the staged `/private/tmp/claude-501/main-review/tools/mineru_table_draft.py` converter and compare had no content-list JSON input. The page 6 PDF render was checked at `/private/tmp/claude-501/main-review/wave51-F61/tables/page6.png` (backup PDF only for this render). It confirms each Table IV/V row and column assignment. No PDF render was used to transcribe digits.

## Table V, PDF page 6

For each listed row, the experiment number, quartz type, size fraction, temperature, holding time, weight-loss percent, and mixed/layered assignment were read from the `pdftotext -layout` layer and the row/column association was confirmed against the page 6 render (method b). Values are preserved as printed in the extract. Table V has 31 printed rows: experiment 1 is a blank control with approximately zero loss and is retained as context; the 30 sample experiments, 2–31, are extracted as `mass_loss_fraction` observations.

| PDF row | Exp. | Quartz | Fraction (mm) | T (°C) | Hold (min) | Weight loss (wt.%) | Raw materials |
|---:|---:|:---:|:---:|---:|---:|---:|:---|
| 6 | 1 | blank | — | 1800 | 60 | ~0 | —; context only |
| 6 | 2 | A | 2 to 5 | 1670 | 120 | 3.25 | mixed |
| 6 | 3 | A | 2 to 5 | 1655 | 120 | 2.50 | layered |
| 6 | 4 | A | 2 to 5 | 1750 | 60 | 3.49 | layered |
| 6 | 5 | A | 2 to 5 | 1750 | 60 | 4.40 | layered |
| 6 | 6 | A | 2 to 5 | 1775 | 120 | 12.65 | mixed |
| 6 | 7 | A | 2 to 5 | 1756 | 120 | 7.31 | layered |
| 6 | 8 | A | 2 to 5 | 1751 | 120 | 9.25 | mixed |
| 6 | 9 | A | 2 to 5 | 1750 | 180 | 8.95 | layered |
| 6 | 10 | A | 2 to 5 | 1850 | 30 | 9.08 | layered |
| 6 | 11 | A | 2 to 5 | 1880 | 60 | 22.00 | mixed |
| 6 | 12 | A | 2 to 5 | 1850 | 60 | 19.35 | layered |
| 6 | 13 | A | 2 to 5 | 1856 | 60 | 13.81 | layered |
| 6 | 14 | A | 2 to 5 | 1854 | 60 | 16.52 | mixed |
| 6 | 15 | A | 2 to 5 | 1850 | 120 | 32.10 | layered |
| 6 | 16 | A | 2 to 5 | 1932 | 30 | 46.63 | mixed |
| 6 | 17 | A | 2 to 5 | 1985 | 60 | 71.80 | mixed |
| 6 | 18 | A | 2 to 5 | 1959 | 60 | 55.52 | mixed |
| 6 | 19 | B | 1 to 2 | 1650 | 120 | 4.40 | mixed |
| 6 | 20 | B | 1 to 2 | 1850 | 60 | 20.56 | mixed |
| 6 | 21 | C | 1 to 2 | 1650 | 120 | 5.10 | mixed |
| 6 | 22 | C | 1 to 2 | 1750 | 120 | 11.83 | mixed |
| 6 | 23 | C | 1 to 2 | 1850 | 60 | 26.45 | mixed |
| 6 | 24 | C | 1 to 2 | 1950 | 30 | 47.35 | mixed |
| 6 | 25 | D | 1 to 2 | 1650 | 120 | 5.66 | mixed |
| 6 | 26 | D | 1 to 2 | 1850 | 60 | 23.20 | mixed |
| 6 | 27 | E | 1 to 2 | 1650 | 120 | 4.45 | mixed |
| 6 | 28 | E | 1 to 2 | 1850 | 60 | 22.57 | mixed |
| 6 | 29 | E | 2 to 5 | 1850 | 60 | 17.85 | mixed |
| 6 | 30 | E | 5 to 8 | 1650 | 120 | 2.60 | mixed |
| 6 | 31 | E | 5 to 8 | 1850 | 60 | 13.54 | mixed |

## Table IV, PDF page 6

The text-layer digits and comparison operators below were checked against the page 6 render (method b). The table heading states that sample A was analyzed by ICP-OES, samples B–E by XRF, and the balance is assumed to be SiO2. For E, Na is marked `0.027*`; the rendered footnote says this is above the calibration limit and must be seen as guidance.

| PDF row | Quartz | Fraction (mm) | Al wt.% | Fe wt.% | K wt.% | Ca wt.% | Ti wt.% | Mg wt.% | Na wt.% | P wt.% |
|---:|:---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 | A | 2 to 5 | 0.031 | 0.012 | 0.009 | 0.0046 | 0.002 | 0.0017 | 0.001 | 0.0003 |
| 6 | B | 1 to 2 | 0.006 | 0.006 | 0.002 | < 0.0011 | < 0.001 | 0.0005 | < 0.0011 | < 0.0004 |
| 6 | C | 1 to 2 | 0.211 | 0.192 | 0.063 | 0.0026 | 0.017 | < 0.0005 | 0.0022 | 0.0016 |
| 6 | D | 1 to 2 | 0.038 | 0.010 | 0.017 | 0.0415 | 0.001 | < 0.0005 | 0.0125 | < 0.0004 |
| 6 | E | 1 to 2, 2 to 5 and 5 to 8 | 0.505 | 0.111 | 0.126 | 0.0324 | 0.016 | 0.0071 | 0.027* | 0.0016 |

## Experimental conditions and scope

The following condition numbers were read from the exact text layer: 20.0 g initial charge, 1:1 molar Si:SiO2, 1.5 bar argon at 99.999 pct purity, 50 K/min heating rate, and row-specific Table V hold times. Table V footnote assigns graphite crucibles except h-BN for experiment 5. Row conditions retain T_C, hold time, pressure, atmosphere, and starting-composition locators; the migrated identity receives T, total pressure, and the 1:1 starting composition. Numeric fO2 is not reported and remains unknown. The reservoir is a typed unknown because the charge has no single printed evaporating-species reservoir. The authors' SiO(g) attribution remains a note and the stored quantity is whole-charge mass loss, not an SiO species amount.

Tables II and VI report reaction constants and activation energy (`k0`, `Q`). No schema token represents those fitted quantities; they are schema gaps and are not typed. Figure-only readings are out of scope and were not digitized.

## Isolated migration

The real migrator produced 30 observations, 0 hard issues, 0 unavailable values, 180 advisory issues, and 60 queue entries. The queue has 30 missing-phase entries and 30 method entries because the source does not identify a measured phase and `isothermal_heat_treatment` is not a closed method token. No phase or method was inferred. The unknown reservoir and fO2 remain explicit; reservoir scoring may refuse these rows as ruled by the controller.

The identity axes were inspected on experiments 2–4 after migration:

| Experiment | Temperature (K) | Starting composition | Total pressure (Pa) | fO2 |
|---:|---:|:---|---:|:---|
| 2 | 1943.15 | Si 0.5, SiO2 0.5 mole fraction; printed sample recipe 1:1 molar | 150000 | unknown: not reported |
| 3 | 1928.15 | Si 0.5, SiO2 0.5 mole fraction; printed sample recipe 1:1 molar | 150000 | unknown: not reported |
| 4 | 2023.15 | Si 0.5, SiO2 0.5 mole fraction; printed sample recipe 1:1 molar | 150000 | unknown: not reported |

For each migrated observation the point conditions carry temperature, hold time, total pressure, starting composition, and Ar sweep gas with their source locators. The 30 stored loss values remain printed wt.% and the migrator converts them to mass fractions (for example 3.25% -> 0.0325).
