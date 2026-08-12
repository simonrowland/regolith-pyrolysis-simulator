# Melt-activity benchmark report

## Evidence boundary

Literal SF04 basalt empirical points: **0**. The scored experimental population is six Hastie-1981 KEMS gas-pressure points plus six Richter-2007 Type-B CAI-like CMAS gamma targets. SF04 workbook pressures are scored only as an explicitly non-empirical regression anchor.

Residual convention: `log10(predicted/measured)`; positive means overprediction. No coefficient tuning was performed.

## Per-species comparison

| Species | Observable | Engine | n | RMSE (dex) | Median residual | ok | OOD | crash | refused | unavailable |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| K | partial_pressure | alphamelts | 0 | — | — | 0 | 3 | 0 | 0 | 0 |
| K | partial_pressure | imcc-ext | 3 | 7.742 | -7.735 | 3 | 0 | 0 | 0 | 0 |
| K | partial_pressure | imcc-published | 3 | 7.743 | -7.737 | 3 | 0 | 0 | 0 | 0 |
| K | partial_pressure | vaporock | 0 | — | — | 0 | 0 | 0 | 0 | 3 |
| Mg | activity_coefficient | alphamelts | 0 | — | — | 0 | 0 | 3 | 0 | 0 |
| Mg | activity_coefficient | imcc-ext | 3 | 0.1182 | 0.05689 | 3 | 0 | 0 | 0 | 0 |
| Mg | activity_coefficient | imcc-published | 3 | 0.1103 | 0.05021 | 3 | 0 | 0 | 0 | 0 |
| Mg | activity_coefficient | vaporock | 0 | — | — | 0 | 0 | 0 | 0 | 3 |
| Mg | evaporation_flux | alphamelts | 0 | — | — | 0 | 0 | 4 | 0 | 0 |
| Mg | evaporation_flux | imcc-ext | 0 | — | — | 0 | 0 | 0 | 4 | 0 |
| Mg | evaporation_flux | imcc-published | 0 | — | — | 0 | 0 | 0 | 4 | 0 |
| Mg | evaporation_flux | vaporock | 0 | — | — | 0 | 0 | 0 | 0 | 4 |
| SiO | activity_coefficient | alphamelts | 0 | — | — | 0 | 0 | 3 | 0 | 0 |
| SiO | activity_coefficient | imcc-ext | 3 | 0.5754 | 0.5414 | 3 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | imcc-published | 3 | 0.5769 | 0.5429 | 3 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | vaporock | 0 | — | — | 0 | 0 | 0 | 0 | 3 |
| SiO | partial_pressure | alphamelts | 0 | — | — | 0 | 3 | 0 | 0 | 0 |
| SiO | partial_pressure | imcc-ext | 3 | 0.2309 | -0.2496 | 3 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | imcc-published | 3 | 0.2307 | -0.2495 | 3 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | vaporock | 0 | — | — | 0 | 0 | 0 | 0 | 3 |

## Frozen SF04 MAGMA regression anchor

The tracked source snapshots rejoin **288** identical SF04 cells on `(sheet, species, T_K)`. Their MAGMA references agree to 0.0000 dex at four decimals.

MAGMA is a model-reproduction anchor, not correctness evidence. The empirical-KEMS column is the independent measured-pressure check available in the frozen VapoRock validation snapshot.

| Species | Shared MAGMA n | IMCC RMSE vs MAGMA | VapoRock RMSE vs MAGMA | Empirical KEMS n | VapoRock RMSE vs KEMS |
|---|---:|---:|---:|---:|---:|
| SiO2 | 36 | 0.099 | 0.351 | 0 | — |
| FeO | 36 | 0.185 | 0.446 | 0 | — |
| Fe | 36 | 0.186 | 0.531 | 0 | — |
| Mg | 36 | 0.248 | 0.346 | 0 | — |
| SiO | 36 | 0.556 | 0.891 | 3 | 0.248 |
| K | 36 | 2.15 | 0.462 | 3 | 0.0725 |
| Na | 36 | 1.18 | 0.143 | 0 | — |
| O2 | 36 | 8.86e-05 | 8.95e-05 | 1 | 0.331 |

Controller regression pool (all non-alkali rows, including the O2 fO2 pin): IMCC **0.274** vs VapoRock **0.503** dex; 0.274/0.503 anchor reproduced: **yes**.

Experimental KEMS snapshot: **7 scored / 9 retained** rows. These are independent KEMS compositions, not measurements on the four SF04 basalt sheets, and therefore do not turn the MAGMA table into empirical basalt evidence.

### Installed VapoRock snapshot check

Live comparison produced 288/288 cells; maximum live-minus-frozen magnitude: 4.64992e-05 dex.
The installed VapoRock run reproduces the frozen snapshot within 0.0005 dex.

## In-domain composition probes

These are engine robustness/coverage probes, not empirical score points.

| Composition | Class | Engine | Status | Reason |
|---|---|---|---|---|
| sf04_tholeiite | literal_basalt | imcc-published | ok | — |
| sf04_tholeiite | literal_basalt | imcc-ext | ok | — |
| sf04_tholeiite | literal_basalt | alphamelts | refused | AlphaMELTS returned no canonical oxide-activity surface |
| sf04_tholeiite | literal_basalt | vaporock | unavailable | VapoRock dependency is present but exposes no public per-oxide melt-activity surface; internally coupled gas pressures are excluded |
| sf04_alkali_basalt | literal_basalt | imcc-published | ok | — |
| sf04_alkali_basalt | literal_basalt | imcc-ext | ok | — |
| sf04_alkali_basalt | literal_basalt | alphamelts | refused | AlphaMELTS returned no canonical oxide-activity surface |
| sf04_alkali_basalt | literal_basalt | vaporock | unavailable | VapoRock dependency is present but exposes no public per-oxide melt-activity surface; internally coupled gas pressures are excluded |
| sf04_komatiite | literal_basalt | imcc-published | ok | — |
| sf04_komatiite | literal_basalt | imcc-ext | ok | — |
| sf04_komatiite | literal_basalt | alphamelts | refused | AlphaMELTS returned no canonical oxide-activity surface |
| sf04_komatiite | literal_basalt | vaporock | unavailable | VapoRock dependency is present but exposes no public per-oxide melt-activity surface; internally coupled gas pressures are excluded |
| sf04_dunite | literal_basalt | imcc-published | ok | — |
| sf04_dunite | literal_basalt | imcc-ext | ok | — |
| sf04_dunite | literal_basalt | alphamelts | refused | AlphaMELTS returned no canonical oxide-activity surface |
| sf04_dunite | literal_basalt | vaporock | unavailable | VapoRock dependency is present but exposes no public per-oxide melt-activity surface; internally coupled gas pressures are excluded |
| richter_type_b_cai | type_b_cai_like_cmas | imcc-published | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | imcc-ext | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | alphamelts | crash | AlphaMELTS subprocess exited before producing a result [backend_status_reason=subprocess_died]: SIGSEGV (returncode -11) |
| richter_type_b_cai | type_b_cai_like_cmas | vaporock | unavailable | VapoRock dependency is present but exposes no public per-oxide melt-activity surface; internally coupled gas pressures are excluded |

## Cross-engine verdict

AlphaMELTS equilibrium completed on all literal SF04 basalt probes, but its provider returned no canonical per-oxide activity surface; therefore the fair melt-activity comparison was refused.

IMCC-versus-AlphaMELTS empirical verdict: **none**. No point has both a convention-valid measurement and successful canonical activities from both engine families.

## Stripping-trajectory coverage

- `alphamelts`: 112/168 accepted; 56 refused/unavailable; below 30 wt% SiO2, 40/40 refused/unavailable.
- `imcc-ext`: 159/168 accepted; 9 refused/unavailable; below 30 wt% SiO2, 3/40 refused/unavailable.
- `imcc-published`: 162/168 accepted; 6 refused/unavailable; below 30 wt% SiO2, 2/40 refused/unavailable.
- `vaporock`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 40/40 refused/unavailable.

AlphaMELTS trajectory boundaries:

- `sf04_alkali_basalt` / `remove_silica`: first refusal at step 11, SiO2=28.534 wt%.
- `sf04_alkali_basalt` / `strip_modifiers`: first refusal at step 17, SiO2=81.286 wt%.
- `sf04_dunite` / `remove_silica`: first refusal at step 8, SiO2=29.695 wt%.
- `sf04_dunite` / `strip_modifiers`: first refusal at step 18, SiO2=82.451 wt%.
- `sf04_komatiite` / `remove_silica`: first refusal at step 12, SiO2=28.265 wt%.
- `sf04_komatiite` / `strip_modifiers`: first refusal at step 17, SiO2=82.640 wt%.
- `sf04_tholeiite` / `remove_silica`: first refusal at step 13, SiO2=29.174 wt%.
- `sf04_tholeiite` / `strip_modifiers`: first refusal at step 16, SiO2=81.775 wt%.

The CSV preserves each composition step, engine status, and typed reason.
It answers the rump question as a curve: AlphaMELTS rejects every normalized step below its 30 wt% SiO2 floor.

## Honest limits

- No direct experimental activity or partial-pressure points exist for the four literal SF04 basalt sheets in the tracked source inventory.
- Richter-2007 is an in-domain Type-B CAI-like CMAS melt, not a literal basalt; its six gamma targets are reported separately.
- Four OCR-digitized Richter Mg flux points are retained but refused for scoring because no independent experimental fO2 pin closes the gas/reference-state comparison.
- KEMS-008 Table 10 values are kinetic vaporization coefficients, not basalt melt activities.
- VapoRock currently exposes no public per-oxide melt-activity surface, so its internally coupled gas results are excluded from the activity-only swap; its separate frozen MAGMA/KEMS and optional live-snapshot diagnostics remain reported.
- Where AlphaMELTS provides no canonical oxide activity or crashes, that is recorded as a first-class result; it is never replaced by a fallback model.
