# Archakov et al. 1999 Table 1 audit

Source: `archakov-1999-titania-slag-evaporation`. Table 1 is on PDF page 2
(printed page 1465) and contains 17 sample rows. The MinerU content-list table
draft reports 17 body rows; pdftotext-layout lines 70–86 also contain rows
1–17. The PDF render was used once to verify the row and column assignments
for the full table, and directly to check row 14's near-equimolar composition.

Cell marks:

- **(a)** MinerU table draft and pdftotext-layout agree on the printed digits.
- **(b)** Digits read directly from the PDF page 2 render.

| PDF page / row | N | x(CaO), mole % | x(TiO2), mole % | x(SiO2), mole % | x(SiO2)/x(TiO2), printed as mole % |
|---|---:|---:|---:|---:|---:|
| 2 / 1 | 1 (a) | 34.28 (a) | 5.05 (a) | 60.67 (a) | 12.01 (a) |
| 2 / 2 | 2 (a) | 42.66 (a) | 5.48 (a) | 51.86 (a) | 9.46 (a) |
| 2 / 3 | 3 (a) | 48.84 (a) | 5.33 (a) | 45.83 (a) | 8.60 (a) |
| 2 / 4 | 4 (a) | 34.99 (a) | 6.84 (a) | 58.18 (a) | 8.51 (a) |
| 2 / 5 | 5 (a) | 40.20 (a) | 10.65 (a) | 49.15 (a) | 4.62 (a) |
| 2 / 6 | 6 (a) | 46.29 (a) | 10.52 (a) | 43.19 (a) | 4.11 (a) |
| 2 / 7 | 7 (a) | 50.60 (a) | 10.52 (a) | 38.88 (a) | 3.70 (a) |
| 2 / 8 | 8 (a) | 31.38 (a) | 16.15 (a) | 52.47 (a) | 3.25 (a) |
| 2 / 9 | 9 (a) | 37.26 (a) | 16.62 (a) | 46.13 (a) | 2.78 (a) |
| 2 / 10 | 10 (a) | 43.07 (a) | 16.67 (a) | 40.26 (a) | 2.42 (a) |
| 2 / 11 | 11 (a) | 47.64 (a) | 15.75 (a) | 36.61 (a) | 2.32 (a) |
| 2 / 12 | 12 (a) | 25.60 (a) | 25.60 (a) | 48.80 (a) | 1.91 (a) |
| 2 / 13 | 13 (a) | 27.99 (a) | 27.99 (a) | 44.02 (a) | 1.57 (a) |
| 2 / 14 | 14 (a) | 33.42 (b) | 33.41 (b) | 33.17 (b) | 0.99 (a) |
| 2 / 15 | 15 (a) | 37.61 (a) | 37.60 (a) | 24.79 (a) | 0.66 (a) |
| 2 / 16 | 16 (a) | 43.21 (a) | 43.21 (a) | 13.59 (a) | 0.31 (a) |
| 2 / 17 | 17 (a) | 50.00 (a) | 50.00 (a) | 0.00 (a) | 0.00 (a) |

The row-14 render reads **33.42 / 33.41 / 33.17**. The ratio column is kept
at its printed values; despite its header, the quotient is dimensionless. The
zero in row 17 is retained as printed.

The focused test pins TiO2 values from rows 1, 5, and 14. Mutation checks each
turned the test red: row 1 5.05→5.06, row 5 10.65→10.66, and row 14
33.41→33.40. The source values were restored after each check.

The melting-point cell on PDF page 3 (printed page 1466) is a separate text-layer
reconciliation: `pdftotext-layout` places the ± glyph on the following line and
reads 1643 ± 15 K for the composition stated as 37.60 mole % TiO2. MinerU's
1643.15 K is rejected as a decimal reading. The page render appears decimal-like;
this fix follows the review ruling to retain the text-layer value. The point is
recorded for air, with only the printed TiO2 percentage and no inferred balance.
The current composition schema cannot express a lone mole-percent component as
a complete composition, so the printed phrase is retained verbatim and the
point-condition composition remains unknown; no Table 1 balance is borrowed.

## Scope and migration

- The table is the setup composition of the 17 samples, stored once as
  unscored composition context. No row-specific run temperature, fO2, or
  pressure is printed for these initial compositions, so they are not emitted
  as temperature-dependent residue observations.
- The trajectories in Figs. 1–3 over 1700–2500 K are figure-read candidates
  only. No figure values or inferred pressures were added.
- Apparatus/setup values left out: background pressure < 1e-8 atm is the
  instrument's background-gas pressure, pyrometer uncertainty < 10 K at 2400 K
  is instrument accuracy rather than a point uncertainty, and SiO2 purity 99.90
  mass % describes a starting reagent; none is a requested residue or vapour
  observation.
- The named predominant vapour species SiO, Ca, TiO, and TiO2 are recorded as
  context, with the text's 25 eV mass-spectrometry condition and no pressures.
- The adjacent `shornikov-2000-titania-melts` companion extract in corpus
  `origin/main` gives study-wide composition ranges but no per-charge
  assignments. It does not establish whether these exact 17 samples were
  reused; the 1999 Table 1 values are retained here once and are not duplicated
  as companion observations. Their published figure windows differ: the 1999
  residue trajectories cover 1700–2500 K, while the companion assigns activity
  curves to 2100 K and liquidus interpretation to 2400–2500 K (both state
  1700–2500 K overall).
- Isolated real-migrator result after this fix: 1 work, 1 experiment, 2 context
  rows, 1 transition-temperature observation, 0 hard issues, and 1 unavailable
  queue item (`source does not state method`). The observation value is 1643 K;
  its printed uncertainty is retained as `T_m_uncertainty_K: 15` with confidence
  level not stated. Migrated point conditions carry temperature 1643 K, air,
  and the located unknown full composition.
- `mineru_table_draft.py compare` reports 0 matched, 0 no-match, 0 mismatch,
  0 total because it compares numeric table-draft cells; Table 1 is context and
  the transition point is prose rather than a MinerU table cell. The command's
  header-only CSV output remains committed alongside this audit; the full cell
  audit above records the machine-read and render checks.
- Expected regenerated store delta: one work, one experiment, two context rows,
  one transition-temperature observation, and one unavailable method item. No
  store regeneration was committed.
