# Tatsumi et al. 2004, LPSC XXXV abstract 2013

source_id: `tatsumi-2004-iron-condensation`. Two-page abstract. PDF sha256 `74399ab05f65432d12eeeac919ad1bb12cdfc1d27f6183c5946d190aae462cae`.

## What the text prints

Recorded as printed: source heating 1170°C; iron source size 12x12mm; molybdenum substrate at 54 mm (1020°C) or 88 mm (415°C); condensation area 178 mm2; chamber pressure `10^-5~10^-4 Pa`; durations 6 to 144 hours at 415°C and 1 to 48 hours at 1020°C; direct flux 1.8% of the total evaporation flux at 54 mm and 1020°C under a point-source assumption; shielded-run reduction by 80%; condensation coefficient `0.5~0.6` at 1020°C and `~0.8` at 415°C; Fe atom velocity ~800 m/s; mean free path greater than 100 m; chamber outer diameter 30 cm.

The shielded-run sentence is printed as "The wait gain of this experimental configuration results in reduction by 80%." That spelling is kept. The shielded run has no printed substrate temperature, so 1020°C is not assigned to it.

The text prints no numeric supersaturation, deposited mass, evaporation rate, or condensation rate. It says source weight loss and condensate weight increased linearly, the evaporation rate is consistent with Tachibana et al. 2001 [5], and the condensation rate is larger at high temperature. Models [3] and [4] treated the condensation coefficient as unity. Reference [5] is quoted for an evaporation coefficient that is larger at higher temperature over 1074–1445°C. This abstract prints no evaporation-coefficient number of its own.

Figure 2 caption numbers, not digitised: 415°C for 96 hours, 1020°C for 24 hours, scales 3µm, 6µm, and 5µm. The cross-section caption identifies molybdenum substrate.

## Figure calibration

Renders are `pdftoppm -png -r 200` of the two PDF pages. Coordinates below are crop-local. Both calibrated axes are linear, so the fit is in linear pixel space.

Figure 1 crop origin on page 1 is pixel (900, 1350). Duration: left spine x=71 is 0 h, x=233 is 50 h, x=395 is 100 h, right spine x=557 is 150 h. `hours = (x_px - 71) / 3.24` with `3.24 = (395 - 233) / 50`. Change, in the printed unit ×10^-3 g: top spine y=81.5 is 6, y=181 is 4, y=280 is 2, bottom spine y=379.5 is 0. `change = (379.5 - y_px) / (298 / 6)`. Markers are grayscale at or below 70. The legend glyphs inside cx 320–500 and cy 130–250 are excluded. Circles are 1020°C. Triangles are 415°C. Reads are rounded to 0.1 h and 0.01 ×10^-3 g. Marker half-extent is about 4 px, so the read uncertainty is ±1.5 h and ±0.10 ×10^-3 g. The circle at crop pixel (75.8, 374.2) intersects the axis frame. It is stored with `frame_contact: true` and the larger uncertainty ±3 h and ±0.20 ×10^-3 g. No extra origin marker was invented. Eleven centres are stored.

Figure 3 crop origin on page 2 is pixel (80, 900). Duration: left spine x=234.5 is 0 h, x=388.5 is 50 h, right spine x=697 is 150 h. `hours = (x_px - 234.5) / 3.08` with `3.08 = (388.5 - 234.5) / 50`. Grain size: top spine y=241.5 is 1.6 µm, bottom spine y=504.5 is 0. `micrometres = (504.5 - y_px) / 164.375` with `164.375 = (504.5 - 241.5) / 1.6`. Markers are grayscale at or below 80. Reads are rounded to 0.1 h and 0.01 µm. Read uncertainty is ±1.5 h and ±0.03 µm. Six centres are stored. The caption says the vertical bars are mean standard deviations. Those lengths were not read: a column scan could not separate caps from the dotted gridlines. The gridlines are not data.

Figure 2 has no data axes and stays a figure record of the caption.

MinerU approximate tables were not copied.

## Schema gap

`simulator/battery/enums.py` has `evaporation_coefficient_alpha` and `wall_deposit_mass`. It has no condensation-coefficient quantity. `simulator/battery/migrate.py` lists `condensation_coefficient` in `_NEVER_QUALIFY_QUANTITY`, and `_quantity_contradiction` refuses `evaporation_coefficient_alpha` when the source names condensation. `map_quantity` therefore returns `unsupported quantity 'condensation_coefficient'`.

The two printed coefficients are type `alpha` observations with `values.quantity: condensation_coefficient`, `method_class: author_estimate`, and the printed strings `0.5~0.6` and `~0.8`. There is no numeric alpha field and no invented midpoint.

`wall_deposit_mass` is not used. The printed term is substrate condensate weight, "Change (×10^-3 g)" and "weight of condensates", not a process-wall deposit. The quantity unit is kg and its identity requires a wall and `sample_mass_kg`, which this abstract does not print. The figure reads stay inside `figure_record` context rows.

The 1.8% flux fraction and the 80% shielded-run reduction stay in context as derivation inputs. They are not a closed flux or mass observation.

## Migration expectation

An isolated migration should emit 2 observations, both quantity-unknown typed refusals, with temperature_K 1293.15 K and 688.15 K, and the printed point conditions on each observation. It should emit 6 context rows, including 3 figure records, and 0 hard issues. No battery store was regenerated. The expected store delta of a later regen is those 2 refused observations and 6 context rows, and no change to any other source.
