# Shornikov 2017 MgO/SiO2 fugacity inversion — extraction audit

Sidecar uses the 2020 hyphen name (`-audit.md`).

Source: Shornikov, Yakovlev, and Ivanova (2017), A thermodynamic study of inversion of MgO and SiO2 relative fugacity at CAI evaporation. LPSC XLVIII, abstract 1134. Two pages. sha256 `458713037a89a71afad57c4037b5ad08be780c092709869a62f89bf28b69278b`.

## Duplicate

Not a duplicate. The PDF title, LPSC XLVIII, and abstract 1134 are not `shornikov-2018-cai-double-inversion` (LPSC 49, abstract 1845, "Double Inversion", no Ivanova). No extract on this base cites abstract 1134 as its own paper.

## Stored, admission rejected

Four calculated activities, read from the text on the 600 dpi PDF page 1, not from a curve:

- CaO–MgO–SiO2, 50 mol% CaO, MgO/SiO2 = 2/3, 2300 K: a(SiO2) = 0.0032, a(MgO) = 0.61
- MgO–Al2O3–SiO2, 50 mol% Al2O3, MgO/SiO2 = 2/3, 2300 K: a(SiO2) = 0.11, a(MgO) = 0.066

`x` beyond the printed 50 mol% is arithmetic: x(SiO2) = 0.50 / (1 + 2/3) = 0.30 and x(MgO) = 0.20. `composition_mol.basis` is `printed_mole_fraction`. The arithmetic sentence is `composition_basis_note`.

Evidence is `model_derived`. `derived_from` is the printed citation Shornikov (2009), Herald Earth Sci. Dept. RAS, 27, 1–4, reference [1], which the abstract names as the ideal associated solutions model. The input activities are not printed here and were not invented. `admission_status` is `rejected`, so these rows are not comparison candidates. `type` is `activity_coefficient` because that is the closed observation type; `values.quantity` is `activity` and the number is `values.activity`. Fidelity samples pin `field: activity` and do not label the observable as `activity_coefficient`.

No numeric fugacity is printed. The title's fugacity is a context row.

## Standard state

Quote, on every activity row: "The paper does not state the activity reference phase (reference phase not stated). It prints calculated oxide activities in the named melts at 2300 K. It does not name a pure-oxide standard, a cell reference measurement, or the temperature of a reference measurement."

`reference_state_from_extract` returns unknown because the prose says the phase is not stated. `_standard_state_from_extract_text` returns None because the prose does not say raoultian, so it does not overwrite that unknown. Expected migration phase: unknown.

`phase: l` is the melt being calculated. It is not the reference endmember.

The 2026-10-20 pure-oxide ruling was not used. These are model activities in ternary melts. The paper does not say the reference is the pure oxide measured in a cell at the experiment temperature.

## Figures

Figs. 1–3 were read on the 600 dpi pages and not digitized. Captions say the curves are calculated. Markers are series labels. Some panels are logarithmic. Reading other points would digitize a model curve. The four text numbers are the only admitted activities. No read uncertainty, because no figure point was stored.
