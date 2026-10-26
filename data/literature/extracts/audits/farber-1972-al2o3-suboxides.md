# Farber, Srivastava & Uy (1972), Table 2 audit

Source: `farber-1972-al2o3-suboxides`. Table 2 is PDF page 5 (printed page
253). The printed table contains eight temperature rows (1900–2600 K) and
seven species columns. This extract intentionally stores the requested three
rows, 2200, 2300, and 2400 K: 18 measured-reduced pressure cells and 3
parenthesized Al2O3 estimates. The species assignments below follow the printed
column headers. Every target pressure cell was checked against the page render;
the pdftotext layer omits several exponents, so none of these pressure cells
qualifies for a two-machine-read confirmation.

Audit marks: **(a)** MinerU and pdftotext agree on digits; **(b)** checked from
the page render. Temperatures are (a), with the render confirming row layout.
All pressure values, including estimates, are (b).

| PDF page / printed table row | T/K (a) | Al, atm (b) | O, atm (b) | AlO, atm (b) | Al2O, atm (b) | Al2O2, atm (b) | AlO2, atm (b) | Al2O3 est., atm (b) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 / 2200 | 2200 | 1.8×10^-7 | 2.1×10^-7 | 3.4×10^-8 | 7.1×10^-9 | 5.4×10^-10 | 7.6×10^-10 | (3.1×10^-8) |
| 5 / 2300 | 2300 | 7.5×10^-7 | 8.7×10^-7 | 1.8×10^-7 | 4.4×10^-8 | 3.6×10^-9 | 5.1×10^-9 | (2.1×10^-7) |
| 5 / 2400 | 2400 | 3.3×10^-6 | 3.9×10^-6 | 1.2×10^-6 | 3.9×10^-7 | 1.7×10^-8 | 4.9×10^-8 | (1.2×10^-6) |

MinerU's page-5 table block reproduces all target digits. The layout text layer
loses the exponents for several target cells (including Al, O, AlO, and AlO2
cells) and OCR-corrupts some other exponents. The render resolves every such
cell and confirms all column assignments. No OCR redo was needed because the
table was legible in the render. The Al2O3 header explicitly says “(est.)” and
its values are parenthesized; those three retained values use
`author_estimate` evidence and rejected admission, not measured-reduced status.

The KEMS method/provenance is in the extraction. The source records chopped
parent-ion intensities measured 1–3 eV above appearance potentials (PDF p. 2),
uses the second-law `I*T` van't Hoff treatment and species cross-section / gain
corrections (PDF pp. 2–3), and gives effusion-weight-loss calibration for Al
and O (PDF p. 3). The AlO pressure calculation uses the stated 17 kcal/mol formation
enthalpy (p. 6). The paper itself is not represented as a derivation input; the
unpublished raw intensity/calibration inputs are typed `not_printed`.

The MinerU draft identifies the printed Table 2 as content-list table key
`p005-t03` because it is the third table block on page 5. The comparison tool
uses that internal ordinal as its table number while the extract locator
correctly records the printed table number `2`, so its compare output marks all
21 stored pressure points `no-match` (no value mismatch). Each is resolved by
the render audit above. The exact compare output is committed beside this file.

## Conditions and migration

The experiment description is a dual-vacuum-chamber quadrupole mass
spectrometer with a 25.4 mm long, 6.8 mm inside-diameter alumina cell and a
0.986 mm by 6.6 mm collimating orifice (PDF p. 2). The table prints T and the
species partial pressures over Al2O3(c). That table-wide printed unary
composition is bound through the existing `composition_mol` path as
`pure_oxide_system: Al2O3 = 1` on every row. It does not print total experiment
pressure, fO2, a sweep gas, or a melt composition; those conditions remain
unknown or absent. No 1 atm or assumed vacuum condition is added.

Isolated migration: 21 observations from seven species records; 18 admitted
measured-reduced pressure points and 3 rejected author-estimate points; 0 hard
issues and 0 unavailable values. Each observation carries its printed
temperature in identity, a located temperature point condition, and the printed
unary Al2O3 composition in identity. The total-pressure and fO2 identity axes
remain unknown. The 18 measured-reduced rows produce ancestry queue
notices because the migrator queues all reduced rows lacking `derived_from`,
including rows that correctly declare their unpublished raw inputs as
`not_printed`; this is not a hard issue, and the paper is not fabricated as a
parent observation.

Focused regression test pins three rendered values after the migrator's atm to
Pa conversion, the 21-point count, measured/estimate evidence separation, and
absence of unprinted pressure and fO2 conditions. Mutation evidence: changing
the rendered Al / 2200 K value from 1.8×10^-7 to 1.9×10^-7 atm made that test
fail on the stored Pa value; the printed value was restored and the test passed.
Expected unregenerated store delta: +21 observations (18 admitted
measured-reduced, 3 rejected estimate), +1 source work, +1 experiment, +1
bench. No store regeneration is committed.
