# Shornikov et al. (2020) figure audit

## Figure 1: MgO–FeO phase diagram

Page 74 / PDF page 1 plots temperature against FeO mol %. The x-axis was calibrated from 0–100 mol % at crop pixels 221–1826; the y-axis was calibrated from 1600–3000 K at pixels 1293–266. The separate fresh page crop used the same page coordinates with its own crop origin.

The 37 keyed marker centers are retained as transition-temperature observations: 14 open green series-2 circles on the I/II solidus boundary, 11 filled green series-2 circles on the II/III liquidus boundary, and 12 black series-3 triangles on the II/III liquidus boundary. Each note records “boundary identified from the printed region labels I/II/III”. Four high-FeO series-2 markers are typed-refused because their centers and boundary assignments are not separable. The unmarked series 1 and 4 curves are retained without sampled points.

## Figure 2: FeO activity coefficient

Page 75 / PDF page 2 plots the linear FeO activity coefficient, gamma_FeO, against FeO mol %. Panel a is at 1173 K and panel b at 1473 K. Panel-a x ticks 0–100 mol % span crop pixels 232–1822 and gamma 1–2.5 spans y pixels 1403–503. Panel b x ticks span pixels 2066–3655 and gamma 1–2.5 spans y pixels 1405–505.

The extract contains 73 digitized markers: 45 in panel a and 28 in panel b. Their numeric ordinates use the existing gamma field, once per marker; no logarithmic conversion is applied. Six overlap records retain 15 unreadable markers as typed-unavailable: panel-a series 1 (5), series 5 (3), series 8 (1), and series 9 (2); panel-b series 5 (3) and series 10 (1).

For both figures, pixel_read_1_page records the first 600 dpi crop read. pixel_read_2_page records a new estimate from a separately rendered page crop, using the marker-color pixel centroid in a 33-by-33 pixel window; the half-filled maroon circles use the marker bounds center. Uncertainty is half the difference between the calibrated reads. Nineteen coincident axis reads use a positive half-pixel width in that axis unit, so no stored read uncertainty is zero. The three literal Figure 2 pins in the focused test are independent checks against these audit values.

## Figures 3 and 4: refused markers and unsampled curves

Figure 3's black markers in each panel are retained in species context because the caption does not key them to a source or say whether each is a total or partial quantity. The attributed enthalpy and entropy curves also remain in species context without point samples. The combined total and partial quantities are a current schema gap.

Figure 4a's 11 black dots are typed-refused because the caption does not key them to a component or source. Its eight captioned activity curves remain without sampled points. Figure 4b's black dots lie on the calculated curve and are not data points; its captioned mixing-energy curves remain in species context without numeric samples because the schema has no mixing-energy quantity token.
