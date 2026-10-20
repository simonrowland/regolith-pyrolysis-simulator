# Itoh, Hino and Ban-Ya (1998) extraction audit

## Duplicate and source check

The PDF title is “Thermodynamics on the Formation of Spinel (MgO·Al2O3) Nonmetallic Inclusions in Liquid Steel,” Tetsu-to-Hagané 84(2), 85–90, DOI 10.2355/tetsutohagane1955.84.2_85. The exact source ID already had an extract; no same DOI/title/author-year extract was found under another source ID. The 1997 Mg-deoxidation extract contains distinct values and was not duplicated.

## Table and data disposition

The paper has no printed measured numeric tables. Table capture: not applicable. Figure 1 and Figure 2 contain measured dissolved-element points but are deferred under this wave’s figure rule; candidate tokens are concentration/composition quantities, but values occur only as plotted markers and no figure digitisation is authorized. Figures 3–8 are phase diagrams, fits, or calculated boundaries and remain deferred model/figure context. The two text-printed solid formation equilibrium constants use the existing `log10_Kf` token and are typed as quoted-attributed solid-phase observations: MgAl2O4, −1.18 at 1873 K (Knacke et al., ref. 13); CaAl4O7, −1.79 at 1873 K (Nagata et al., ref. 15).

Other numerical items left in context have no compatible measured quantity token or are author assumptions/model outputs: equilibrium holding time and phase-presence statements; fitted liquid-metal cross-interaction coefficients; the calculated log K for MgO·Al2O3 from deoxidation relations; author-estimated phase thresholds; assumed solid-solution activities; quoted/assumed liquid-metal interaction coefficients. They are not scored as solid thermochemistry.

## Locator and render audit

The two typed values are printed in the text on physical PDF page 4 (published folio 88), in equations (12) and (28). `pdftotext -f 4 -l 4 -layout` confirms both labels and values on that page; the page 4 render was checked at 600 dpi. Figure records retain physical pages 2–6, with `page = pdf_page_index + 1`.

Figure 1 marker counts from the 600 dpi render, by legend order: 8, 8, 6, 4. Figure 2: 4, 4, 4, 3. Figures 3–8 show line/phase-boundary series without data markers, so marker count is zero. No plotted values were digitised.

## Migration

Two observations migrate as points at −1.18 and −1.79, with formulas MgAl2O4 and CaAl4O7, solid phase, and 1873 K conditions. No unavailable observation migrated. The isolated migration reported 0 hard issues.

The model-derived interaction coefficients and calculated equilibrium relations lack stored input rows. Their outputs and printed input bases are listed in `.agent-scratch/F28-tables/itoh-hino-banya-1998-spinel/derived-inputs.csv`; their context records carry `derivation pending a corpus table asset (controller, t-1382)`.
