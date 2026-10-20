# Shornikov and Yakovlev (2019), LPSC abstract 1722: extraction audit

## Duplicate verdict

pdfinfo Title is `1722.PDF`. Page 1 header: "50th Lunar and Planetary Science Conference 2019 (LPI Contrib. No. 2132) 1722.pdf". Title on the page: Thermodynamic study of spinel evaporation by Knudsen effusion mass spectrometry. sha256 `5bb613a34c809008b1286b6d8b75162863650b2b781a79bd8cb4c1cd7d08fb48`. No extract on this base stores that hash.

This is not a full duplicate. The printed enthalpy −12.02±1.14 kJ/mol, entropy 5.03±0.56 J/(mol×K), and melting enthalpy 55.81±4.62 kJ/mol at 2420±25 K are already in `shornikov-2017-spinel-thermodynamics`. Figs. 2–4 repeat the pressure and activity campaigns in `shornikov-2017-spinel-evaporation` and `shornikov-2018-spinel-solid-solutions`. Those numbers and figures are refused below.

Fig. 5 series 1 is unique. It plots the MgO–Al2O3 mixing energy at 2200 K from the present study. The 2018 extract's mixing-function figure is Fig. 2 at 2100 K and was not digitized. Series 2 and 3 on the same figure are cited from earlier papers and were not read.

## Not stored as numeric reads

| Item | Decision | Evidence |
| --- | --- | --- |
| −12.02±1.14 kJ/mol, 5.03±0.56 J/(mol×K), 55.81±4.62 kJ/mol, 2420±25 K | refused | Already stored in `shornikov-2017-spinel-thermodynamics`. |
| Fig. 2, partial pressures over spinel | refused | Caption cites the 2017 pressure paper. Not re-read. |
| Fig. 3, partial pressures over spinel solid solutions at 2100 K | refused | Caption cites the 2018 paper. Not re-read. |
| Fig. 4, activities of MgO and Al2O3 at 2200 K | refused | Caption cites the 2018 paper. Not re-read. No activity row, so no standard state. |
| Fig. 5 dotted lines | refused | Caption: "Dotted lines correspond to heterogeneous areas." They are guides, not filled circles. Not sampled. |
| Fig. 5 connecting strokes | refused | Same colour as the circles and thinner. A 10 px erosion removes them and leaves the circles. Not sampled. |
| Fig. 5 numeral 1 | refused | Black series index, not a circle. Crop of the glyph at rough px about (1528, 916) reads "1". |
| Fig. 5 numeral 2 | refused | Red series index. Crop at rough px about (1363, 760) reads "2". |
| Fig. 5 numeral 3 | refused | Blue series index. Crop at rough px about (1681, 1006) reads "3". |
| Fig. 5 series 2, red filled circles | cited, not read | Caption series (2), Al2O3–SiO2, reference [8], Shornikov and Archakov, Russ. J. Phys. Chem. A 74, 684–688 (2000). No marker centre was stored. |
| Fig. 5 series 3, blue filled circles | cited, not read | Caption series (3), CaO–Al2O3, reference [9], Shornikov et al., Russ. J. Phys. Chem. A 71, 23–27 (1997). No marker centre was stored. |

The numerals sit beside the curves. They are the caption indices (1), (2), and (3), not a separate legend box and not data markers.

## Figure 5 calibration

600 dpi render of PDF page 2 (`pdftoppm -r 600 -f 2 -l 2`). The figure crop's origin on that page is (2500, 150); the crop matches the page pixels exactly. Both axes are linear. Calibration uses the tick line, not the glyph and not an unlabelled minor tick.

Ordinate, labelled tick midline in crop y px, value in kJ/mol: 552.0 = −10, 723.5 = −15, 895.0 = −20, 1066.0 = −25, 1238.0 = −30, 1410.0 = −35, 1581.0 = −40. The −40 tick is the bottom spine. Unlabelled minor ticks, about 12 px long against about 23 px for the labelled ticks, sit halfway between them and were not used. Least squares: kJ/mol = −0.029148·y + 6.0868. Residuals in kJ: +0.0031, +0.0021, +0.0010, −0.0146, −0.0010, +0.0125, −0.0031. rms 0.0075 kJ (0.26 px).

Abscissa, labelled tick midline in crop x px, Al2O3 mol %: 431.5 = 20, 647.0 = 30, 864.5 = 40, 1082.0 = 50, 1298.0 = 60, 1515.0 = 70, 1732.5 = 80, 1948.5 = 90. The 20 tick is the left spine. The "20" glyph pair is centred on that spine. Unlabelled minor ticks were not used. Least squares: mol % = 0.046118·x + 0.1250. Residuals in mol %: −0.025, +0.037, +0.006, −0.025, +0.014, +0.006, −0.025, +0.014. rms 0.021 mol % (0.46 px).

Glyph centres sit within a few pixels of the tick line. The fit uses the tick line.

Read 1 is the centroid of the core left after a 10 px erosion. That erosion removes the connecting stroke. Read 2 is the bounding-box centre of the high distance-transform core. The stated uncertainty is the root sum of squares of a 1 px floor, the tick-fit rms, and the read-1 versus read-2 disagreement, converted with the fit slopes, then rounded up to 0.01. Where the distance transform has two maxima more than 4 px apart, the floor is half that separation instead of 1 px.

Erosion at 10 px and at 12 px leaves 14 black circle cores. Those are series 1. No series 1 centre lies outside the labelled-tick rectangle, so none is flagged as extrapolated. The red and blue circles were not measured.

## Marker decisions

Page px = crop px + (2500, 150). Values are read 1. Temperature is 2200 K, from the Fig. 5 caption. The ordinate is ΔG^m. The abstract derives that mixing energy from oxide activities calculated from partial pressures. The figure read itself is `figure_only`. Quantity has no mixing-energy token, so the reads are context and are not stored as `delta_fG`.

Marker 0 is elongated. Its distance-transform maxima are 17 px apart, so the x uncertainty is ±0.40 mol %. The other markers are ±0.06 mol % and ±0.04 kJ/mol.

| Series | Decision | Al2O3 mol % | ΔG^m kJ/mol | Crop centre px | Page centre px | dpi | Centre note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MgO–Al2O3 0 | read | 50.41 ± 0.40 | −23.08 ± 0.04 | (1090.3, 1000.7) | (3590.3, 1150.7) | 600 | two maxima, half-separation 8.5 px in x |
| MgO–Al2O3 1 | read | 52.61 ± 0.06 | −23.14 ± 0.04 | (1138.0, 1002.8) | (3638.0, 1152.8) | 600 | single maximum |
| MgO–Al2O3 2 | read | 54.85 ± 0.06 | −23.02 ± 0.04 | (1186.7, 998.6) | (3686.7, 1148.6) | 600 | single maximum |
| MgO–Al2O3 3 | read | 56.43 ± 0.06 | −22.88 ± 0.04 | (1220.8, 993.6) | (3720.8, 1143.6) | 600 | single maximum |
| MgO–Al2O3 4 | read | 58.53 ± 0.06 | −22.55 ± 0.04 | (1266.4, 982.4) | (3766.4, 1132.4) | 600 | single maximum |
| MgO–Al2O3 5 | read | 61.73 ± 0.06 | −21.82 ± 0.04 | (1335.8, 957.4) | (3835.8, 1107.4) | 600 | single maximum |
| MgO–Al2O3 6 | read | 66.76 ± 0.06 | −20.17 ± 0.04 | (1444.9, 900.8) | (3944.9, 1050.8) | 600 | single maximum |
| MgO–Al2O3 7 | read | 71.35 ± 0.06 | −18.10 ± 0.04 | (1544.5, 829.6) | (4044.5, 979.6) | 600 | single maximum |
| MgO–Al2O3 8 | read | 72.75 ± 0.06 | −17.34 ± 0.04 | (1574.7, 803.8) | (4074.7, 953.8) | 600 | single maximum |
| MgO–Al2O3 9 | read | 75.01 ± 0.06 | −16.10 ± 0.04 | (1623.7, 761.1) | (4123.7, 911.1) | 600 | single maximum |
| MgO–Al2O3 10 | read | 76.24 ± 0.06 | −15.41 ± 0.04 | (1650.4, 737.6) | (4150.4, 887.6) | 600 | single maximum |
| MgO–Al2O3 11 | read | 78.50 ± 0.06 | −13.97 ± 0.04 | (1699.4, 688.2) | (4199.4, 838.2) | 600 | single maximum |
| MgO–Al2O3 12 | read | 81.13 ± 0.06 | −12.26 ± 0.04 | (1756.4, 629.4) | (4256.4, 779.4) | 600 | single maximum |
| MgO–Al2O3 13 | read | 84.39 ± 0.06 | −10.10 ± 0.04 | (1827.2, 555.4) | (4327.2, 705.4) | 600 | single maximum |

Series (1) is the present study, MgO–Al2O3, at 2200 K. Series (2) and (3) are listed under refused rows as cited and not read.

## Standard state

Fig. 5 is not an activity dataset. No `standard_state` is stored. Migration is not asked to assign a reference phase. The activities that the abstract says were used to obtain the mixing energy are on Fig. 4 and are refused above as already published. The stable-phase ruling was not applied.

## Schema gap

`Quantity` has no mixing-energy token. Storing ΔG^m as `delta_fG` would make migration score a formation energy. The 14 series 1 reads stay in `species.MgO-Al2O3.context` with `type: figure_mixing_energy` and `method_class: figure_only`. Scored observations: 0. The abscissa is mol % Al2O3. `AmountBasis` has no mole-percent token, so the mole percent is not stored as a mole fraction.
