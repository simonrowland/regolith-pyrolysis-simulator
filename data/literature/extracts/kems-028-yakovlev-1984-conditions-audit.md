# Yakovlev et al. (1984) alkali condition audit

## Current extract and printed rows

The current extract contains two measured Na/K pressure observations:

| Observation | Printed sample | Printed value | Published page | PDF page index |
| --- | --- | ---: | ---: | ---: |
| `yakovlev_1984_murchison_Na_P_1225C` | Murchison chondrite | Na: 2.7×10⁻⁴ torr at 1225 °C | 945 | 0 |
| `yakovlev_1984_murchison_K_P_1225C` | Murchison chondrite | K: 4.2×10⁻⁵ torr at 1225 °C | 945 | 0 |

The page 945 text also gives the shared maximum-temperature range, 1050–1250 °C, and says Na pressure is generally higher than K pressure. The abstract identifies the apparatus as a Knudsen effusion cell. The published page does not give an orifice size, a bulk oxide analysis for Murchison, total or ambient pressure, or an oxygen condition for these alkali observations. The cited technique and CAI references do not themselves provide an analysis in this abstract.

Page 946's unnumbered oxygen-pressure table is explicitly calculated from `AlO=Al+O` and `O2=2O` for high-temperature vaporization residues. Those calculated values do not state the oxygen condition of the 1225 °C Na/K observations and are not assigned to them.

## Scope discrepancy requiring controller ruling

The task names eight existing alkali vapor rows. The current source extract has only the two measured Na/K rows listed above. It also has an aggregate Murchison figure-only observation, but it contains no point values; the companion figure audit records other species and no Na/K points. The source's plotted Na/K traces are figure-only, and the extraction-wave rule excludes values read only from figures. No six additional Na/K observation IDs or numeric rows are present in the current extract or its generated extract view.

Please clarify which six additional rows the task refers to, or confirm that the work should cover the two printed prose values and list the figure-only traces as unavailable. Do not invent rows or values to make the count reach eight.

## Phase-label conflict requiring controller ruling

The two observations currently use the descriptive sample/phase label `Murchison_CM_chondrite_melt_knudsen`, while their gas species are recorded as `Na(g)` and `K(g)`. The dispatch-specific task says not to change the vapor phase label because b-1080 owns that class. The general extraction acceptance rule also requires no migrated phase-unknown observations. In `simulator/battery/migrate.py::PHASE_MAP`, `g` is a recognized vapor phase token, while the descriptive current label is not a `PHASE_MAP` token. Please clarify whether b-1080 requires preserving this field verbatim (accepting phase-unknown for these rows) or permits storing the closed `g` token while retaining the descriptive sample label in its own field.

## Render audit

The page 945 render confirms the Na and K digits, their shared 1225 °C temperature, and the 1050–1250 °C maximum range. The machine transcription has `I`/`1` ambiguity in the printed exponents; the render reads Na as `2.7×10⁻⁴` and K as `4.2×10⁻⁵` torr. These are the two prose values, not figure reads. Page 946's small table has seven temperature rows; its `I` characters in the Murchison PO₂ column are also `1` in the render. This audit does not assign any of those calculated PO₂ cells to the Na/K conditions.
