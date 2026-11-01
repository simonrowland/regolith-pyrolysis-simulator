# Shornikov and Archakov (2000) vaporization audit

## Reconciliation

Green's extract is the base. It has six admitted Figure 1(c) observations and
four Figure 3 series 1 context points. Batch4 has 35 figure reads: ten overlap
those Green records and 25 add distinct plotted points. The ten overlaps differ
slightly because the reads used different pixel calibrations; the page renders
and stated uncertainties support Green's smaller-uncertainty values. The table
records both reads and the retained Green value.

| Figure / series / point | Green value (x, y) | Batch4 read (x, y) | Retained |
|---|---|---|---|
| 1(c), Na2O, near 33 mol% | `33.24, -14.186` | `33.23, -14.17` | Green `shornikov_2000_fig1c_na2o_x33` |
| 1(c), Na2O, near 40 mol% | `39.93, -14.313` | `39.91, -14.3` | Green `shornikov_2000_fig1c_na2o_x40` |
| 1(c), ZnO, near 33 mol% | `33.2, -4.143` | `33.13, -4.11` | Green `shornikov_2000_fig1c_zno_x33` |
| 1(c), ZnO, near 40 mol% | `39.72, -3.723` | `39.75, -3.7` | Green `shornikov_2000_fig1c_zno_x40` |
| 1(c), P2O5, near 33 mol% | `33.2, -5.886` | `33.21, -5.87` | Green `shornikov_2000_fig1c_p2o5_x33` |
| 1(c), P2O5, near 40 mol% | `39.85, -5.534` | `39.81, -5.52` | Green `shornikov_2000_fig1c_p2o5_x40` |
| 3, series 1, near 0 mol% | `-0.02, -31.966` | `-0.03, -31.94` | Green `shornikov_2000_fig3_series1_x0` |
| 3, series 1, near 33 mol% | `33.22, -18.529` | `33.23, -18.51` | Green `shornikov_2000_fig3_series1_x33` |
| 3, series 1, near 40 mol% | `39.88, -16.385` | `39.88, -16.38` | Green `shornikov_2000_fig3_series1_x40` |
| 3, series 1, near 67 mol% | `66.54, -8.706` | `66.57, -8.69` | Green `shornikov_2000_fig3_series1_x67` |

The comparison table covers Figure 1(c), p. 60, and Figure 3, p. 63. The
coordinates are from the 600 dpi renders of PDF p. 3 (printed p. 60) and PDF
p. 6 (printed p. 63). Green's Figure 1(c) reads have stated
uncertainty ±0.15 mol% in x and ±0.03 in log activity; its Figure 3 reads have
±0.15 mol% in x and ±0.04 in ΔG/RT. Batch4's corresponding uncertainties are
±0.96–1.41 mol% and ±0.19–0.29 in Figure 1(c), and ±0.9 mol% and ±0.57 in
Figure 3. All ten pairs agree within the combined uncertainties. Green's
observation IDs and values are retained.

Batch4's two new Figure 1(c) points (ZnO and P2O5 near 67 mol%), all 18 Figure 2
composition points, and five Figure 3 series 2 points are added as context.
Figure counts remain 8, 18, and 9. Batch4's method and pressure details are
folded into Green's existing apparatus and partial-pressure contexts. The
single figure-inventory context row carries the candidate list also retained
under `extraction`; no duplicate method or pressure rows are added.

## Disposition and migration

The source has no numbered numeric tables and prints no rowwise Na(g) pressure,
flux, or evaporation coefficient. Figure 1(c) shows activities at 1100 K;
Figure 2 shows residue compositions without a printed temperature; Figure 3
shows dimensionless ΔG/RT for this study at 1100 K and cited prior data at
1300 K. Those 35 plotted points remain unscored context. Green's 11 observations
remain intact.

The merged extract passes validation. Its isolated migration emits 11 existing
observations, 35 context rows, and 0 hard issues. Mutation pins retain the
Batch4 Figure 1(c), Figure 2, and Figure 3 reads while checking the selected
Green values for the overlapping points.

Verification basis: controller two-read verification, 2026-10-28.

Expected store delta relative to green: 0 scored observations and 25 additional
unscored context rows. The battery store is not regenerated.
