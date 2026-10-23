# t1243 derivation: Fe(1-x)O(l, Fe-saturated) to stoichiometric FeO(l)

Phase 1 only. The scorer is not wired. Numbers come from one run of `validation-data/t1243-feo-reference/derive_feo_reference_state.py` against this seat's extracts and JANAF tables. Displayed kilojoule and factor figures are that script's `Decimal` quantize; a later test should recompute from the extract ratios and the production JANAF interpolator, not paste these rounded coefficients.

The Darken & Gurry 1946 PDF and the Distin 1971 PDF are not in the corpus. No Darken & Gurry free-energy equation and no Distin ref. 7 formula were compared. The external check is Holzheid 1997, pages 30–31.

## 1. Standard states and the 96 temperatures

Both states are the stoichiometric FeO component of the reaction Fe + 1/2 O2 = FeO. They differ in the oxygen potential at which the pure-liquid activity is defined.

**Wüstite state, called here Fe(1-x)O(l, Fe-saturated).** Activity is 1 for the liquid iron oxide in equilibrium with iron. Holzheid page 30, equation (9), gives Barin (1989) for that liquid, in J/mol with T in K:

    ΔG("FeO" l) = −244118 + 115.559 T − 8.474 T ln T

Holzheid page 30–31: the reported FeO coefficients use this wüstite(l) state, not stoichiometric FeO(l). The extracts record the same choice. Holzheid `reference_pressure_bar` is 1.01325. Distin's anchor `distin_wustite_standard_state` is the same Raoultian liquid Fe(1-x)O at 1.01325 bar, and the note says the paper's stoichiometric conversion is a later procedure on page 75. Neither extract is relabelled as FeO(l).

**Stoichiometric state, FeO(l).** JANAF Fe-019, title `Fe1O1(l)`, standard state `p° = 0.1 MPa` (1 bar). Formation ΔG is relative to the stable Fe phase: Fe(s) below the iron melting point, Fe(l) above it. The table has 49 formation nodes from 298.15 K to 5000 K. An unparsed `GLASS <-> LIQUID` marker sits at 1100 K and an unparsed `CRYSTAL <-> LIQUID` marker at 1650 K. Those two lines have no formation-Gibbs cell. The production interpolator joins the neighbouring formation nodes. Every temperature in the table below was read that way (`production_interpolator`), including 1650.00 K, which falls between the 1600 K and 1700 K formation values. 1650 K is not a missing 100 K grid node, so the interpolator does not refuse it.

**Whether each state is liquid at the row temperature.**

| Source | Objects | Temperatures | Span |
| --- | --- | --- | --- |
| Holzheid 1997 | 11 observations, type and `values.quantity` both `activity_coefficient` | 72 series temperatures. Four observations hold more than one temperature; each series item has its own `T_K` and `gamma` | 1578–1872 K (1304.85–1598.85 °C) |
| Distin 1971 | 24 observations, type `activity_coefficient`, `values.quantity` `activity`, value field `activity` | 24, one temperature each | 2058.15–2233.15 K (1785–1960 °C) |

Species block in both extracts is `FeO`. The reference endmember formula is `Fe(1-x)O`, phase `l`.

Darken & Gurry liquid-oxide gas points are 1376, 1436 and 1493 °C, so 1649.15, 1709.15 and 1766.15 K. Iron melting on JANAF Fe-003 is 1809 K. FeO crystal↔liquid marker is 1650 K.

Holzheid partition of the 72 temperatures:

- 4 are below both the first liquid-oxide gas point and the FeO melting marker. The only temperatures there are 1578 K and 1623 K. At those points the iron-saturated oxide in Darken & Gurry is below the measured liquid boundary, and stoichiometric FeO(l) is below the JANAF melting marker, so both liquids are outside their measured ranges.
- 66 run from the FeO melting marker through the iron melting point. FeO(l) is the stable oxide; the coexisting iron is still solid. 64 of the 72 temperatures lie inside the gas-ratio window 1649.15–1766.15 K.
- 2 are above the iron melting point: 1826 K and 1872 K. Both liquids, and the metal, are liquid. These two sit above the last gas point (1766.15 K).

The four temperatures above that gas window are 1780, 1795, 1826 and 1872 K, one row each.

Distin: all 24 temperatures are above both melting points and above the gas window, by 292 K at 1785 °C and by 467 K at 1960 °C. Unique temperatures are 2058.15, 2153.15 and 2233.15 K.

## 2. Algebra

Premise. At the iron boundary the metal fixes μ_Fe and the gas ratio fixes the oxygen potential. With the pure-iron convention a_Fe = 1, the partial molar Gibbs energy of the FeO component in the coexisting liquid is fixed. That liquid is the wüstite standard state, so its FeO activity on that state is 1.

JANAF defines the equilibrium constant of CO + 1/2 O2 = CO2 at p° = 1 bar. ΔfG(O2) = 0. R is the stored JANAF constant, 8.31441 J mol⁻¹ K⁻¹. T_K = T_C + 273.15.

    ΔG° = ΔfG(CO2) − ΔfG(CO)                         (C-095 and C-093, J/mol)
    ΔG_w,vs_solid = RT ln(pCO2/pCO) + ΔG°
                  = (1/2) RT ln(pO2 / p°)

ΔG_w,vs_solid is μ_FeO(at the Fe-saturated liquid) − μ_Fe(s) − 1/2 μ°_O2. JANAF Fe-019 ΔfG(FeO,l) is the same difference for pure stoichiometric FeO(l) against the stable element phase.

Below the iron melting point the stable phase is solid, which is also the metal in the Darken & Gurry liquid-oxide experiments:

    ln(a_FeO,stoich / a_Fe(1-x)O) = (ΔG_w,vs_solid − ΔfG_FeO(l)) / RT

Above 1809 K the coexisting metal is liquid and JANAF's element reference is liquid iron. The solid-iron buffer is shifted by the iron fusion energy, with ΔCp of fusion left at zero:

    ΔG_fus(T) = ΔH_fus − T ΔS_fus
    ΔG_w,vs_stable = ΔG_w,vs_solid − ΔG_fus(T)       for T > 1809 K
    ln(factor) = (ΔG_w,vs_stable − ΔfG_FeO(l)) / RT

The factor multiplies a wüstite-scale activity or activity coefficient to place it on the stoichiometric FeO(l) scale. Holzheid defines it as γ_FeO / γ_"FeO". A factor above 1 means the stoichiometric coefficient is the larger number.

The three gas ratios are not collinear, so the temperature function is the exact least-squares line through their three ΔG_w values, plus the fusion shift above 1809 K, minus the interpolated Fe-019 value. It is not a fit of the factor, and it is not a fit to Holzheid.

**Composition does not multiply this partial.** Darken & Gurry print r = (Fe²⁺ + Fe³⁺)/Fe²⁺. Charge balance gives O/Fe = (3r − 1)/(2r). The review of record attributes that identity to page 801; the PDF was not re-read here. The iron-boundary statement r = 1.067 gives O/Fe = 1.03140 and, for Fe_(1−x)O, x = 0.03044. Table III A has 13 iron-boundary liquids, r from 1.045 to 1.081, O/Fe from 1.0215 to 1.0375.

The integral reaction Fe + (y/2) O2 = FeO_y has Gibbs energy y times the partial, per mole of Fe. That equals the partial only at y = 1. Using it in place of the partial, at the stated y = 1.03140, multiplies the factor by 0.702 at 1400 °C (integral factor 1.257 against the partial factor 1.792) and by 0.740 at 1600 °C (1.092 against 1.475). Holzheid's printed factor at 1400 °C is 1.94. The integral reaction does not reproduce it. It is not the conversion.

Gibbs-Duhem integration across composition is not required. On the iron boundary, μ_Fe and μ_O are already fixed by the two-phase equilibrium, so the gas ratio is already the partial molar quantity for FeO. An integration would be how one left that composition at constant temperature. The 96 rows use the iron-saturated liquid as the standard state. Table I of the extract is a set of fixed gas mixtures, not a pO2–composition isotherm, so no such path was built.

The limit x → 0 makes the partial and the integral agree. It does not make the correction zero. The correction is zero only when the boundary oxygen potential equals JANAF ΔfG(FeO,l). That does not occur in this span: the primary factor is still 1.150 at 1960 °C.

## 3. Inputs and the temperature function

Iron-boundary gas ratios, Table III B, equilibrium boundary "iron and liquid oxide", page 803. ΔG° is JANAF. No printed uncertainty is attached to the ratios.

| Row | T | pCO2/pCO | ΔG°(CO + 1/2 O2 = CO2) | ΔG_w,vs_solid |
| --- | --- | --- | --- | --- |
| dg46_t3b_1376 | 1376 °C, 1649.15 K | 0.268 | −139.872 kJ/mol | −157.927 kJ/mol |
| dg46_t3b_1436 | 1436 °C, 1709.15 K | 0.233 | −134.787 kJ/mol | −155.488 kJ/mol |
| dg46_t3b_1493 | 1493 °C, 1766.15 K | 0.199 | −129.971 kJ/mol | −153.678 kJ/mol |

Least squares, displayed to 0.001:

    ΔG_w,vs_solid = −217.786 kJ/mol + (36.349 J mol⁻¹ K⁻¹) T

Residuals of the three points, which are the inconsistency of the printed ratios with a straight line and are not a printed ±: −84.5, +173.5, −89.0 J/mol.

Iron fusion, read from the two unparsed Fe-003 lines at 1809 K (the 8-column parser dropped them). Solid line label `DELTA <--> LIQUID`, liquid line label `TRANSITION`.

| Quantity | Value |
| --- | --- |
| ΔH_fus | 13.807 kJ/mol (72.452 − 58.645 kJ/mol) |
| ΔS_fus | 7.633 J mol⁻¹ K⁻¹ (99.823 − 92.190) |
| ΔH/Tm | 7.632 J mol⁻¹ K⁻¹ |
| ΔG_fus(1809 K) | −1.1 J/mol |

The −1.1 J/mol is the printed-digit mismatch between ΔH and Tm ΔS. The solid and liquid branches meet at the melting point for any purpose here. The evaluation uses the solid branch at exactly 1809 K and subtracts ΔG_fus only above it. ΔCp of fusion is not in the primary function. The nearest printed heat capacities are Fe-003 Cp(1800 K) = 42.468 and Fe-006 Cp(1900 K) = 46.024 J mol⁻¹ K⁻¹, so ΔCp ≈ 3.556 J mol⁻¹ K⁻¹. Held constant, the extra fusion term is −4 J/mol at 1600 °C and −164 J/mol at 1960 °C.

Primary factor at the temperatures that bound the rows. Envelope is the min/max of the three pair-fits through two of the three anchors. Digit is the largest |Δ ln(factor)| when one printed ratio is moved by ±0.001 and the line is refit. Iron phase is the branch actually used.

| Place | T/K | Factor | ln | dex | Envelope | Digit on ln | Iron |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Anchor 1376 °C, fitted | 1649.15 | 1.841 | 0.610 | 0.265 | 1.829–1.902 | 0.00315 | solid |
| Anchor 1436 °C, fitted | 1709.15 | 1.724 | 0.545 | 0.236 | 1.713–1.745 | 0.00178 | solid |
| Anchor 1493 °C, fitted | 1766.15 | 1.627 | 0.487 | 0.211 | 1.617–1.674 | 0.00415 | solid |
| FeO melting marker | 1650.00 | 1.839 | 0.609 | 0.265 | 1.828–1.900 | 0.00312 | solid |
| Fe melting | 1809.00 | 1.556 | 0.442 | 0.192 | 1.527–1.620 | 0.00584 | solid |
| Holzheid quoted 1300 °C | 1573.15 | 2.018 | 0.702 | 0.305 | 1.955–2.145 | 0.00587 | solid |
| Holzheid quoted 1400 °C | 1673.15 | 1.792 | 0.583 | 0.253 | 1.781–1.836 | 0.00234 | solid |
| Holzheid quoted 1600 °C | 1873.15 | 1.475 | 0.389 | 0.169 | 1.421–1.561 | 0.00822 | liquid |
| Holzheid lowest row | 1578.00 | 2.005 | 0.696 | 0.302 | 1.946–2.128 | 0.00568 | solid |
| Holzheid highest row | 1872.00 | 1.476 | 0.390 | 0.169 | 1.423–1.562 | 0.00818 | liquid |
| Distin 1785 °C | 2058.15 | 1.283 | 0.249 | 0.108 | 1.180–1.415 | 0.01426 | liquid |
| Distin 1880 °C | 2153.15 | 1.206 | 0.187 | 0.081 | 1.086–1.355 | 0.01696 | liquid |
| Distin 1960 °C | 2233.15 | 1.150 | 0.140 | 0.061 | 1.019–1.311 | 0.01905 | liquid |

Exact anchors, using the measured ΔG_w at that temperature and no line fit: 1.829, 1.745 and 1.617. The fit moves ln(factor) by +0.00617, −0.01221 and +0.00606. The middle residual, 173.5 J/mol, is that −0.012 on ln.

Fusion shift at the same fit, ΔH and ΔS set to zero: at 1600 °C the factor is 1.475 with fusion and 1.429 without (ΔG_fus = −0.491 kJ/mol); at 1960 °C it is 1.150 with fusion and 0.966 without (ΔG_fus = −3.239 kJ/mol). Distin crosses from above 1 to below 1 if the iron-melting change of reference is omitted.

**Uncertainty, d-128.** The gas ratios and r values have no printed ± and no stated confidence level. Confidence level not stated. The last printed digit, 0.001 in the gas ratio, is used as a resolution width, not multiplied by 1.96, and not turned into a standard deviation. The jackknife envelope is the sensitivity of a three-point line, not a confidence interval. Inside the gas window the jackknife is a few hundredths on the factor and is larger than the last digit. At Distin temperatures the extrapolation envelope is the wide term: at 1960 °C the factor spans 1.019 to 1.311 around 1.150, while the last digit moves ln by 0.019. The ΔCp term of −164 J/mol at that temperature moves ln by about 0.009, inside the last digit and far inside the envelope.

## 4. Checks against Holzheid

Holzheid's equation (9) and the three printed factors are a comparison. They are not the conversion. Barin (1989) is their source. From the layout text, page 30: the ratio is 2.02 at 1300 °C and 1.66 at 1600 °C, calculated as the equilibrium-constant ratio and ignoring small mole-fraction differences. Page 31: stoichiometric FeO(l) coefficients are higher by 1.94 at 1400 °C.

| T | Quoted factor | This factor | Eq. (9) | This ΔG_w | JANAF FeO(l) | Stoich ΔG implied by eq. (9) and the quoted factor |
| --- | --- | --- | --- | --- | --- | --- |
| 1300 °C | 2.02 | 2.018 | −160.453 | −160.605 | −169.786 | −169.649 |
| 1400 °C | 1.94 | 1.792 | −156.008 | −156.970 | −165.084 | −165.227 |
| 1600 °C | 1.66 | 1.475 | −147.268 | −149.209 | −155.262 | −155.161 |

Energies in kJ/mol. JANAF FeO(l) and the stoichiometric energy implied by equation (9) agree to about 0.15 kJ/mol. The gap is the oxygen potential of the iron-saturated liquid, not the stoichiometric endmember.

At 1400 °C, which is inside the gas window (1376–1493 °C), equation (9) is −156.008 and this ΔG_w is −156.970, a gap of 0.96 kJ/mol. The quoted factor 1.94 lies outside the jackknife envelope 1.781–1.836. At 1600 °C the gap is 1.94 kJ/mol and 1.66 lies outside 1.421–1.561. Equation (9)'s slope near 1673 K is about 44 J mol⁻¹ K⁻¹; the three gas ratios give 36.3. The 1300 °C match, 2.018 against 2.02, sits 76 K below the first gas point, where the shallower slope happens to cross Barin. Adopting 1.94 and 1.66 would replace the Darken & Gurry plus JANAF derivation with Barin.

The 1 atm versus 1 bar offset, (1/2) RT ln(1.01325), is 86 J/mol at 1300 °C, 92 J/mol at 1400 °C, 103 J/mol at 1600 °C and 122 J/mol at 1960 °C. It does not account for the gap. It is not applied.

Dissolved oxygen in the iron, from Distin's printed saturation contents, is also not applied. With atomic weights Fe 55.845 and O 15.999, 0.44 wt% O at 1785 °C is X_O = 0.0152 and would multiply the factor by 0.985; 0.63 wt% at 1880 °C multiplies by 0.978; 0.83 wt% at 1960 °C is X_O = 0.0284 and multiplies by 0.972. The pure-iron convention sets a_Fe = 1 on the Darken & Gurry side and on the JANAF element reference.

## 5. Assumptions that move the number

1. a_Fe = 1. Dropping it by Distin's saturation oxygen multiplies Distin's factor by 0.972–0.985. Holzheid's metal is pure Fe in their section 3 calculation. Not applied.
2. The oxygen potential between and beyond the three gas points is the straight line above. Inside the window the line misses the middle point by 0.17 kJ/mol. Outside it, the jackknife is the measure of that assumption. Not replaced by Barin's slope.
3. ΔCp of fusion is zero. The constant-ΔCp bound is −4 J/mol at 1600 °C and −164 J/mol at 1960 °C. Not applied.
4. Pressure standard stays JANAF 1 bar. The extracts declare 1 atm. The offset is under 0.13 kJ/mol. Not applied.
5. Mole-fraction differences between FeO and Fe(1-x)O are ignored, as Holzheid states. The composition is used only to identify the phase (O/Fe ≈ 1.03). The integral y-multiplier is rejected by the Holzheid comparison.
6. Below 1650 K the Fe-019 liquid table is still the comparison state, read by joining 1600 K and 1700 K. That is a supercooled liquid, not FeO(cr). Four Holzheid temperatures need this. Not switched to the crystal table.
7. Distin uses the same line 292–467 K above the last gas point, with the iron-fusion shift. The envelope at 1960 °C is 1.019–1.311.

## 6. Where the conversion belongs

One owner already converts a reference before the comparison: `fusion_comparison_reference` in `simulator/battery/score.py` at line 1419. Its docstring calls it the single owner of solid/liquid reference selection. `compile_residual` calls it at line 6239 and passes the returned observation to the predictor at line 6518. The diagnostic loop in `_score_store_with_decisions` calls it at line 7089. `benchmarks/melt_activity_benchmark.py` calls it at line 1458.

The comparison does not convert. `_reference_matches` in `simulator/battery/generators/bench.py` at line 300 returns whether convention, phase, basis and formula agree. The melt-activity generator calls it at line 558, and the comment at line 577 says not to convert. A miss becomes `GapReason.REFERENCE_STATE_MISMATCH` and then `RefusalReason.UNSUPPORTED` in `_activity_contract_refusal` (`score.py` line 3744, branch at line 3786). The activity-coefficient check at `score.py` lines 5370–5398 refuses `COEFFICIENT_BASIS_MISMATCH` when the reported standard state disagrees. That check sees the observation the predictor was given, so a conversion done in `fusion_comparison_reference` is what it would see.

The current guard returns the observation unchanged unless the quantity is activity, the phase is crystalline, the convention is Raoultian, the species formula equals the endmember formula, and the point value is positive. These rows fail that guard: the phase is liquid, the endmember is Fe(1-x)O while the species is FeO, and Holzheid's quantity is an activity coefficient. Distin's type is `activity_coefficient` while `values.quantity` is `activity`. No store migration was run, so the quantity token the scorer actually sees is not asserted here.

Phase 2, after a ruling, would widen this one function: multiply the wüstite-scale number by the factor at that row's temperature, and retag the reference to Raoultian stoichiometric FeO(l). Holzheid's four series observations need the factor at each series temperature. No second converter would be added in `bench.py`, and the physics would not be copied into a script.

## 7. Ruling requested

The proposed conversion is the partial-molar factor in sections 2–3: a_Fe = 1, no y multiplier, no dissolved-oxygen multiplier, JANAF 1 bar, the three-point Darken & Gurry line, JANAF Fe-019 through the production interpolator, and ΔG_fus(Fe) with ΔCp = 0 above 1809 K. It is not adjusted to Holzheid's 1.94 or 1.66.

Please rule on these points. They change the number or which rows receive it.

1. Confirm the partial-molar route. The integral reaction at O/Fe = 1.03140 gives 1.257 at 1400 °C, against Holzheid's 1.94 and against this partial factor 1.792.
2. Confirm that the conversion stays on Darken & Gurry plus JANAF where it misses Barin. At 1400 °C, inside the measured window, the factor is 1.792 with envelope 1.781–1.836; the printed Barin factor is 1.94. At 1600 °C it is 1.475 with envelope 1.421–1.561; the printed factor is 1.66.
3. Distin is entirely above the measured gas window. Score all 24 rows with the extrapolated factor (1.283, 1.206, 1.150 at 1785, 1880 and 1960 °C, envelopes reaching 1.180–1.415, 1.086–1.355 and 1.019–1.311), or refuse them as outside the measured band.
4. Four Holzheid temperatures, 1578 K and 1623 K, lie below the liquid-oxide gas measurements and below the FeO melting marker. Apply the supercooled liquid conversion, or refuse those rows.
5. Confirm a_Fe = 1 and no mole-fraction redefinition. The Distin oxygen bound is a factor multiplier of 0.972–0.985.
6. Confirm the pressure standard remains JANAF 1 bar. The 1 atm offset is 86–122 J/mol.
