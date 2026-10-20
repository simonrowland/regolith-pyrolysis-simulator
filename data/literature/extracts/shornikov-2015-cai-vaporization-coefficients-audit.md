# Shornikov 2015 vaporization coefficients — extraction audit

Sidecar uses the 2020 hyphen name (`-audit.md`).

Source: Shornikov, S. I. (2015), Vaporization Coefficients of Oxides Contained in the Melts of Ca–Al–Inclusions in Chondrites, Geochemistry International 53(12), 1080–1089. DOI 10.1134/S0016702915100055. sha256 `6c200e01f13ab9675d1407d4f90d65af90a58bd2a53b97e0b65bebd98903c390`. PDF page n is published page 1079+n.

## Duplicate

Not a duplicate of an already extracted full paper. `shornikov-2014-ca-al-evaporation-coefficients` is MetSoc abstract 5018, the conference precursor: it has the recommended coefficients and no tables. This journal article adds Tables 1–5 and Figs. 1–2. `shornikov-2015-cai-mineral-thermodynamics` and its MetSoc 5017 twin are a different title. The 2014 file was not edited. Where the journal repeats a 2014 recommendation, the journal number is stored here.

## Tables

MinerU `convert` wrote five drafts. It tagged many data rows as `th`. Header rows that are not plain numbers are dropped by `emit`, so ± and range cells would have vanished. After the 600 dpi render confirmed every αvap cell, the scratch CSV set those data rows to `body`. Real header rows were left as headers (row 1 in Tables 1–4; rows 1–2 in Table 5). Digits were not edited.

`emit` mapped only the αvap column. Temperature was not an emit column: a printed range is not a point, and the migrator must not be handed `1283–1473` as a point temperature. `T_range_K` was attached from the temperature cell. A single printed temperature is stored as `[T, T]`.

`emit` stores this quantity on `values.value`, which migration does not read for an evaporation coefficient. An unambiguous central value was copied to `values.alpha` and `value` was removed. A printed `× 10^{-2}` inside the cell was applied to that cell; `alpha_as_printed` keeps the cell. A printed range, including a decreasing range such as Takigawa `0.10–0.01` and Sata `0.80–0.52`, is `values.alpha_range` as `[low, high]`. The printed text stays in `alpha_range_as_printed`. No midpoint was stored.

The gas-phase dash is absent, not zero. MinerU math wrappers on crucible and gas cells were unwrapped to the formulas read on the page. `Pt–Rh` and `ZrO2–CaO` keep the page en dashes. Table spellings that disagree with one prose mention were not "corrected": Sata and Lee, 1978 in Table 2 (the body once says 1974); Takigawa in Table 3 (the body once says Tokigawa).

Asterisk cells use the footnote "Calculated in the course of this research" and are `model_derived`. `derived_from` is the reference cell the table prints: Knyazeva and Vasil'ev, 1972; Claasen and Veenemans, 1933; Lukin and Poluboyarinov, 1964. The crystalline FeO evaluation on published p. 1083 is the same class, from Darken and Gurry (1946) and Glushko et al. (1978–1982). Those citations are prose. This extract does not invent parent observations for numbers the paper does not print.

## Phase

- Tables 1 and 2: `cr`. The paper says each whole table was determined only for the crystalline phase.
- Table 3: `l` only on the Burns 2318 K and Drowart 2327–2594 K cells of `1.0 ± 0.2`, which the paper names as `αvap(Al2O3, liq)`. `cr` on the Takigawa row, which the paper calls an Al2O3 crystal. Other Table 3 rows are unlabeled in a table the paper says contains both phases, so they have no phase.
- Table 4: `l` on the 2000–2100 K cell `1.00 ± 0.02`, the melting jump the paper names as `αvap(SiO2, liq)`. `cr` on rows whose upper temperature is below the stated melting temperature 1996 ± 5 K. The Hashimoto 2173–2373 K row is above that melting temperature and the paper says that work did not show the jump, so it has no phase.
- Table 5: `cr`, from the column header `αvap (cr)`. No temperature is printed for these coefficients. `Tmelt` was not copied onto them.

## Prose rows that are not table cells

- FeO crystalline `1.00 ± 0.20` at 1373–1623 K (published p. 1083). Table 5 prints `1.00 ± 0.30`. Both are kept. The Table 5 attribution does not say the abstract and the conclusions agree with `1.00 ± 0.30`.
- FeO liquid `1.00 ± 0.10` (same page). No temperature is printed for it.
- Al2O3 crystalline recommendation `0.33 ± 0.02` (same page; also the abstract and the conclusions). Table 5 prints `0.32 ± 0.02`. Both are kept. The Table 5 attribution does not say the abstract and the conclusions agree with `0.32 ± 0.02`.
- TiO2 `0.20 ± 0.04` at 1920 K, quoted from Semenov (1969). The recommended `0.23 ± 0.02` is the Table 5 cell and is not repeated. Published p. 1084 says that recommended coefficient was calculated for the dominant gas-phase component TiO. That label is on the Table 5 cell, not on the Semenov row.
- CaO, MgO, and TiO2 liquid equal to one: `author_estimate`. The body calls this a hypothesis. The conclusions say every liquid coefficient equals one. FeO, Al2O3, and SiO2 liquid values stay the printed `±` rows, not a second "equal to one".

CaO `0.66 ± 0.15`, MgO `0.50 ± 0.20`, TiO2 `0.23 ± 0.02`, and SiO2 `0.022 ± 0.003` agree with the abstract and the conclusions. Those repeats are notes on the Table 5 cells, not second observations. SiO2 prose `(2.2 ± 0.3) × 10^{-2}` is the same number as `0.022 ± 0.003`.

## Refused and context

Table 5 enthalpy, entropy, melting, sublimation, and atomization columns are out of scope. They are one context row, not observations.

Fig. 1 (published p. 1085) was re-read at 600 dpi. Open circles are on line 1 only. The caption cites that line as Hashimoto (1990), Langmuir total pressure from a graphite crucible. Panel (a) has three circles on the high-temperature limb of the step and six on the low-temperature limb. Panel (b) has three and five. Lines 2–5 are Shornikov et al. (1998, 1999) KEMS strokes for Pt, Ir, W, and Mo cells, with no open circle separable from the stroke. Line 6 is the curve calculated in this paper from Glushko et al. (1978–1982), with a step and no marker. No point was digitized. The page also prints two bounds, stored on the context row: the Mo-cell p(SiO) differs by almost 10^2 from the neutral-condition calculation, and p(SiO2) agrees within unsystematic errors no greater than 15%.

Fig. 2 (published p. 1086) is log `αvap(cr)` versus melting entropy, labeled stars and a fitted curve. The CAI stars repeat Table 5. BeO, BaO, Ga2O3, In2O3, and SnO2 were not read: the log axis is small, and a coordinate would be invented.

## Standard state

No activity dataset, so no `standard_state`. The 2026-10-20 pure-oxide reference-phase ruling was not used. Evaporation-coefficient phase is the phase the paper states for that row.

## Render checks

600 dpi pages: PDF pp. 3–8, plus page 1 text. Crops of Tables 1–5 and Figs. 1–2 were read. Every emitted αvap cell matches the page. No `needs_render_check` digit was rewritten.
