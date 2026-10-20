# Shornikov 2008 ZnO–P4O10 melts — extraction audit

Source: Shornikov, S. I. (2008), Thermodynamic properties of ZnO–P4O10 melts, Experiment in Geosciences, vol. 15, no. 1, pp. 149–150. PDF page 1 is published page 149. PDF page 2 is published page 150. No DOI is printed. The PDF has no text layer.

Duplicate check: not a duplicate. No extract on this base cites this title, this venue, or pages 149–150. The figure's experimental dots are from Shornikov, Shilov and Shultz, Russ. J. Phys. Chem. 70 (1996) 447–453, and Shornikov, High Temperature Corrosion and Materials Chemistry III, ECS Proceedings 2001-12, pp. 316–321. The extracted `shornikov-2001-mgalumina-silica-vaporization` is pp. 322–329 of that volume, a different article. `shilov-1998-sno-zno-p2o5-vaporization` is the SnO comparison system, not this calculation.

## Table (PDF p. 1, published p. 149)

MinerU `p001-t01` was emitted: 30 numeric cells, 30 empty cells skipped. `pdftotext-layout.txt` is empty, so it cannot confirm digits. The 600 dpi render of PDF page 1 was read. Every stored digit and sign matches that render, including gaseous O2 `0.000` as a printed zero. No cell was hand-corrected. No `needs_render_check` flag was set. Empty condensed-phase cells under the gas-only rows were not stored.

The caption says the cells were calculated in the present study according to data [1, 6–9]. Those parents are not printed per row, so the evidence class is `quoted_attributed`. They are formation Gibbs energies from the elements at 1300 K, not oxide activities.

## Figure (PDF p. 2, published p. 150)

Axes are labelled. Panel a: x(ZnO), mole %, ticks 60, 65, 70, 75, 80, 85; lg a_1300(ZnO, P4O10), ticks −2, −4, −6, −8. Panel b: the same composition ticks; ΔfG_1300, kJ/mole, ticks −75, −80, −85, −90. Points were not calibrated and not stored.

Caption: dots are experimental activities of ZnO (1), P4O10 (2) and Gibbs energies of melt formation from oxides (3). Lines are the values calculated in the present study (4, 5, 6). The text says the comparison is with KEMS at 1300 K [6, 7].

| Marker | Decision | Evidence |
| --- | --- | --- |
| Panel a, filled triangles, legend 1 | refused | Ten data triangles on the 600 dpi crop, plus the legend glyph. Caption assigns them to the cited KEMS experiments, not to a measurement of this article. |
| Panel a, open squares, legend 2 | refused | Thirteen data squares on the 600 dpi crop, plus the legend glyph. Same caption assignment. |
| Panel a, line 4 | refused | Continuous calculated lg a(ZnO). No discrete calculated markers. |
| Panel a, line 5 | refused | Continuous calculated lg a(P4O10). No discrete calculated markers. |
| Panel b, filled circles, legend 3 | refused | Eleven data circles with error bars on the 600 dpi crop, plus the legend disk. Caption assigns them to the cited experiments. |
| Panel b, line 6 | refused | Continuous calculated ΔfG curve. No discrete calculated markers. The prose minimum, about −87.5 kJ/mol at Zn2P4O12, is stored as context. |

MinerU's invented chart tables in the markdown were ignored.

## Standard state

Activity rows quote the figure caption. The article does not name pure solid or pure liquid ZnO or P4O10 as the activity reference. `reference_state_from_extract` returns unknown because the prose says the reference phase is not stated. The introduction says liquid P4O10 is thermodynamically unstable; that sentence is not an activity standard.

The table is formation from the elements. It is not an activity standard.

The prose minimum is formation from oxides. The oxide phase is not stated.

## Prose kept as context

- Model domain 950–1500 K, ideal associated solutions. Published page 149.
- ΔmH(P4O10) = 68.286 kJ/mol, an adopted input compared with reference [1].
- Minimum Gibbs energy of melt formation from oxides about −87.5 kJ/mol at Zn2P4O12.
