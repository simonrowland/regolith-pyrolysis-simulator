# Shornikov 2018 MetSoc 6134 — extraction audit

Source: Shornikov, S. I. (2018). Thermodynamic Modelling of Evaporation Processes of Lunar and Meteoritic Substance. 81st Annual Meeting of The Meteoritical Society, abstract 6134. LPI Contribution No. 2067. Vernadsky Institute, Kosygin st. 19, Moscow. One page. The PDF is a Photoshop image. sha256 459fa638472e09e72fde10c2b2fc1f697dbe60693b9f1b0d53f687261a995ff3. pdftotext is empty.

## Duplicate verdict

DUPLICATE OF `shornikov-2019-lunar-meteorite-evaporation-model`.

Evidence: that extract is Shornikov (2019), "Thermodynamic modeling of evaporation processes of lunar and meteorite substance," Geochemistry International 57(8), 865–872, DOI 10.1134/S001670291908010X. Its text prints the Hertz–Knudsen equation, the statement that the partial evaporation coefficient is equal to 1 (Shornikov, 2015), the Lewis equation, the ideal-associated-solution model, and the 1600–2500 K Knudsen range. The abstract says the details and the results will be discussed during the presentation.

This is not `shornikov-2018-cai-complex-gas-species` (same meeting, abstract 6137, with Yakovlev) and not `shornikov-2018-cai-double-inversion` (LPSC 2018, abstract 1845). It is not `shornikov-2018-spinel-solid-solutions` (Russian Journal of Physical Chemistry A 92(8), 1447–1456).

## Reading

Tesseract, page-segmentation mode 6, on the 200 dpi render `sh2018-1.png`, with the working directory set to the render directory. The earlier pass that used an absolute path returned an empty file and is discarded. This pass recovered the header, the body, and references [1] through [15]. Oxide formulas in the body are garbled by the OCR (Al2O3 and SiO2). The title, the coefficient sentence, and the reference list are legible. No figure and no table is on the page.

## The coefficient

"The partial evaporation coefficient (α_i), characterizing the change in the structure of a molecule during its transition to the gas phase, in this case is equal to 1 [2]." Reference [2] is Shornikov (2015), Geochemistry International 53, 1080–1089, which is `shornikov-2015-cai-vaporization-coefficients`. The sentence has no temperature and no composition. It is not stored as an alpha observation. The 2019 paper prints the same sentence, so the abstract adds no measurement.

Reference [10], the database citation, is Shornikov and Yakovlev, LPS XLVI, abstract 1270. That is not the 2015 Geochemistry International complex-oxide paper extracted in this seat.

## Standard state

The Lewis equation is p_i = a_i p°_i, and p°_i is the pressure over the individual substance (pure component). The abstract says both solid and liquid pure oxides usually evaporate without changing the condensed-phase composition. It does not name one phase for the activity. No activity row is stored. Expected migration phase: unknown.

## Admitted

None.
