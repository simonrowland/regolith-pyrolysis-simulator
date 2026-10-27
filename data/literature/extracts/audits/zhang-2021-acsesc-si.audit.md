# Zhang et al. (2021) supporting-information extraction audit

| Printed source | Printed rows | Stored cells | Audit trail |
|---|---:|---|---|
| SI Table S1, PDF p. 9 | 12 run/sample rows | sample mass before and after (mg), then calculated bulk mass-loss percentage | Born-digital pdftotext; all sample/column assignments checked on the rendered page. |
| Main article Table 1, PDF p. 71 | 12 run rows | T, time and nine oxide wt% cells per row; b.d.l. preserved | Born-digital pdftotext; row and oxide-column assignments checked on the rendered page. |
| SI Tables S2–S11 | 488 EPMA spot rows | excluded from this task by controller ruling | Recorded as future spatial-profile data; no values averaged or copied. |

For S1, each sample row's two mass cells were checked in the before/after columns. For Table 1, each of the 12 samples was matched against the printed sample heading and the T, time and Na2O, MgO, CaO, TiO2, SiO2, Al2O3, FeO, K2O and Rb2O columns. No comma-decimal, exponent, minus-sign, or fused-footnote trap was present in these stored table cells. The PDF render was used to check row/column assignment; machine-text digits are exact for these born-digital tables.

Mass loss is reduced from the printed initial and final sample weights as 100 × (initial − final) / initial. It is a whole-sample, multi-element loss, not a Na flux. Its evidence class is measured_reduced, with lineage to the printed weight record. Starting basalt composition and the temperature/time for each sample are bound to the run. The main article reports the vacuum as about 10^-6 mbar (converted to approximately 1e-4 Pa); no absolute run fO2 is printed. The zero-time samples at 1200 °C were held 5 minutes before quench; the 1400 °C zero-time samples were quenched upon reaching 1400 °C. Products are measured as quenched glass; chemical preservation through quench is not stated.

The main-article run-level oxide composition vectors are retained as measured_direct records, but remain typed-refused because the current value schema has no field for one complete multicomponent composition vector. The S1 weighing record is likewise typed-refused because no standalone mass-measurement quantity token exists. The 12 calculated mass-loss fractions migrate successfully. No Na(g)/Na pressure or Na-specific evaporation flux is inferred.

Table 1 has no printed per-run oxide uncertainty or EPMA spot count. The 488 individual analyses in SI Tables S2–S11 are spatial profiles/zoning and are not substituted for the 12 run-level summaries.
