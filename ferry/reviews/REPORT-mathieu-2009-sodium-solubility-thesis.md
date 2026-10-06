# REPORT: mathieu-2009-sodium-solubility-thesis, batch8 item B (fixes + part B steps 1-4)

Tip `4330e0df6a5570e31382c4a26f1694c9788de884` on `hunt/mathieu-2009-sodium-solubility-thesis` (mirror). Start 92434ea18e174e0b7a925d3aef20b536a34acedc. Green 61ec839da (engines/engines.local.toml present). See STATUS-fix-mathieu-2009-sodium-solubility-thesis.md for the fix table and commit list.

**Outcome:** fixes 5/5 + 7 tables (141 rows); 421 observations, 100 context rows (11 run-condition, 80 prose claims, 9 earlier context), 1 typed bench; hard issues 0 after finalize; fidelity validator OK; ledger test 674 passed. P0: none.

## 1. Experiment facts (value, locator, migrated value)

Bench `mathieu_2009_sealed_silica_ampoule_cell` (described_in_this_work); experiment `sealed_ampoule_glass_equilibration`, method quench_equilibration, bench_id bound. Sources: PDF p61-62 (printed 38-39, II.2), p64 (printed 41, II.4.1), p77 (reprinted 2008 letter). All read on the page images. Migrated values below are printed from `to_plain` of the migrated WORK after finalize():

| fact | migrated value |
|---|---|
| apparatus_family | value:Sealed silica-ampoule thermochemical cell (closed system) heated in a muffle furnace |
| method | value:Wire-loop glass beads suspended under the lid of a Pt crucible reactor above a Na2O-xSiO2 source of known aNa2O; f |
| cell_material_and_liner | value:Sealed silica tube of 25-30 cm3 (22 mm external diameter, 120 mm high); reactor is a Pt crucible (16 mm diameter,  |
| heating_method | value:GERO HTK 8.4 muffle furnace at the working temperature; quench in water, cooling rate estimated at 50 °C/s over th |
| cell_materials | ['value:SiO2', 'value:Pt', 'value:Al2O3'] |
| ionization | ionization_energy_eV=not_applicable:not_applicable |
| geometry | orifice_diameter_m=not_applicable:not_applicable |
| other_facts.ampoule_volume (cm3) | value:{'kind': 'categorical', 'categorical': '25-30', 'approximate': False} |
| other_facts.ampoule_external_diameter (mm) | value:{'kind': 'point', 'point': '22', 'approximate': False} |
| other_facts.ampoule_height (mm) | value:{'kind': 'point', 'point': '120', 'approximate': False} |
| other_facts.reactor_crucible_diameter (mm) | value:{'kind': 'point', 'point': '16', 'approximate': False} |
| other_facts.reactor_crucible_height (mm) | value:{'kind': 'point', 'point': '20', 'approximate': False} |
| other_facts.sodium_source_mass (g) | value:{'kind': 'point', 'point': '2', 'approximate': False} |
| other_facts.sodium_source_composition (instrument) | value:{'kind': 'categorical', 'categorical': 'Na2O-xSiO2 with 1 <= x <= 9', 'approximate': False} |
| other_facts.sample_bead_mass (mg) | value:{'kind': 'categorical', 'categorical': '15-20', 'approximate': False} |
| other_facts.max_samples_per_run (count) | value:{'kind': 'point', 'point': '9', 'approximate': False} |
| other_facts.bead_premelt_temperature (°C) | value:{'kind': 'point', 'point': '1650', 'approximate': False} |
| other_facts.oxygen_buffer (instrument) | value:{'kind': 'categorical', 'categorical': 'Me/MeO solid buffer (Me = Ni, Co, Fe); 5 g of stoichio |
| other_facts.evacuation_pressure_before_sealing (mbar) | value:{'kind': 'point', 'point': '0.001', 'approximate': False} |
| other_facts.degassing_temperature_before_sealing (°C) | value:{'kind': 'point', 'point': '150', 'approximate': False} |
| other_facts.quench_cooling_rate (°C/s) | value:{'kind': 'point', 'point': '50', 'approximate': False} |
| other_facts.thermal_gradient (°C/cm) | value:{'kind': 'point', 'point': '3', 'approximate': False} |
| other_facts.temperature_uncertainty (°C) | value:{'kind': 'point', 'point': '5', 'approximate': False} |
| other_facts.maximum_usable_temperature (°C) | value:{'kind': 'point', 'point': '1450', 'approximate': False} |
| other_facts.run_temperature_range (°C) | value:{'kind': 'categorical', 'categorical': '1250-1400', 'approximate': False} |
| other_facts.background_pressure_Pa (Pa) | unknown:not_published |
| method | {'tag': 'value', 'value': 'quench_equilibration'} |
| bench_id | 10.70675/39024f29z943dz4cc7zb2f6za74b9f3e1fd8::bench::mathieu_2009_sealed_silica_ampoule_cell |
| sample.container | value:Pt wire loop hung under the Pt lid of a Pt crucible reactor inside a sealed silica ampoule |
| sample.pretreatment | value:15–20 mg glass beads suspended on Pt loops inside sealed silica ampoule; Na2O–xSiO2 source coexists with |
| apparatus.geometry.orifice_diameter_m | unknown:not_applicable_to_sealed_ampoule_method |
| apparatus.geometry.orifice_channel_length_m | unknown:not_applicable_to_sealed_ampoule_method |
| conditions.temperature_K | unknown:runs_span_multiple_temperatures_from_1250_to_1400_C |
| pressure_environment.total_pressure_Pa | unknown:closed_ampoule_equilibration; no run chamber background pressure reported |
| pressure_environment.sweep_gas | unknown:sealed_ampoule_no_sweep_gas |
| pressure_environment.pumping.base_pressure_Pa | value:0.1 |

Activity standard state: γNa2O and aNa2O refer to pure liquid Na2O, Na2O(l) = 2 Na(g) + 1/2 O2(g) (chapter II, PDF p42); source activity aNa2O(source) = aNa2O(sample) at equilibrium (eq. II.6, PDF p61). Source activities for xNa2O < 20 mol% are FactSage values, per the author (p67, claim carried).

## 2. Observation rows per table

| table | label | conc rows (measured_tabulated) | γ rows (measured_reduced) | with analysed composition (c/γ) | pending (c/γ) |
|---|---|---|---|---|---|
| t1 | III.1 | 18 | 18 | 18/18 | 0/0 |
| t2 | III.2 | 25 | 22 | 24/22 | 25/22 |
| t38 | IV.1 | 47 | 47 | 0/0 | 0/2 |
| t39 | IV.2 | 24 | 24 | 0/0 | 0/1 |
| t40 | IV.3 | 17 | 17 | 0/0 | 0/0 |
| t41 | IV.4 | 10 | 10 | 9/9 | 0/0 |
| t42 | IV.5 | 8 | 8 | 8/8 | 0/0 |
| t43 | IV.6 | 2 | 2 | 2/2 | 0/2 |
| t44 | IV.7 | 52 | 52 | 0/0 | 0/52 |
| t45 | IV.8 | 2 | 2 | 0/0 | 0/0 |
| t46 | IV.9 (caption IV.19) | 7 | 7 | 3/3 | 0/3 |
| **total** | | 212 | 209 | 64/62 | 25/82 |

Row difference c vs γ: t2 has 3 analysed rows with no printed γ. Reference rows (pôle, EDiAn) and rows without values (N.A., no liquid) are in the 11 `mathieu_2009_<t>_run_conditions` context rows, not scored. Every γ row: derived_from its concentration row; derivation.inputs = [concentration id, `mathieu_2009_<t>_run_conditions`]; relation γ = a_Na2O(source)/x_Na2O with caption source activity; printed heading scale applied and printed value kept as `gamma_as_printed`.

Identity axes after finalize: T 421/421; fO2_Pa landed for all admitted γ rows except t2/t43 (not landed by design, see P1); composition landed for 62 γ rows (t1 18, t2 22, t41 9, t42 8, t43 2, t46 3). 202 soft `identity_incomplete` issues (147 `composition is unknown`, 55 `fO2_Pa is unknown`, as counted by the validator). In the migrated identities 82 γ rows have fO2 unknown: t2 22 and t43 2 by design, plus the other 58 pending γ rows (t44 52, t46 3, t38 2, t39 1), which the migrator does not condition.

## 3. Payload survival

Observation rows (one per table; first row shown with migrated identity):

| temperature_measurement | control_thermocouple=value:Furnace temperature controlled by a Pt/PtRh thermocouple |
| temperature_calibration | calibration_procedure=value:Calibration of the furnace and the ampoules gives a gradient of 3 °C/cm; stated_uncertainty=value:Temperature uncertainty does not exceed ±5 °C; gradient on the cell about 3 °C/cm, small insid |
| thermal_schedule.cooling_or_quench | value:Ampoule removed from the furnace and quenched in water; cooling rate estimated at 50 °C/s over the first |
**observations: 421; non-numeric or missing point values: 212** (the 212 are the concentration rows; see FACTS THE READER DROPS)

| t1 | 36 rows | first mathieu_2009_t1_acms13_cms8_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=value:{'basis': 'printed_oxides', 'compo |
| t2 | 47 rows | first mathieu_2009_t2_acms29_cms1_na2o_analysed point=None unit= T=value:1673.15 fO2=None comp=value:{'basis': 'printed_oxides', 'compo |
| t38 | 94 rows | first mathieu_2009_t38_acmas5_as1_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t39 | 48 rows | first mathieu_2009_t39_acmas5_as1_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t40 | 34 rows | first mathieu_2009_t40_acmas40_cmas19_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t41 | 20 rows | first mathieu_2009_t41_acmas31_cma9s15_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t42 | 16 rows | first mathieu_2009_t42_apedian14_cms36_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=value:{'basis': 'printed_oxides', 'compo |
| t43 | 4 rows | first mathieu_2009_t43_acma16s67_cma16s1_na2o_analysed point=None unit= T=value:1523.15 fO2=None comp=value:{'basis': 'printed_oxides', 'compo |
| t44 | 104 rows | first mathieu_2009_t44_acmas5_na1_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t45 | 4 rows | first mathieu_2009_t45_acma16s48_cma16s1_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=None |
| t46 | 14 rows | first mathieu_2009_t46_acmas21_cma16s1_na2o_analysed point=None unit= T=value:1673.15 fO2=value:0.21379575 comp=value:{'basis': 'printed_oxides', 'compo |

Table files (all 68): CSV row counts and whether the path survives in migrated context:

| t1 | csv rows 23 | path in migrated context: True |
| t2 | csv rows 30 | path in migrated context: True |
| t3 | csv rows 6 | path in migrated context: True |
| t4 | csv rows 4 | path in migrated context: True |
| t5 | csv rows 7 | path in migrated context: True |
| t6 | csv rows 10 | path in migrated context: True |
| t7 | csv rows 94 | path in migrated context: True |
| t8 | csv rows 22 | path in migrated context: True |
| t9 | csv rows 3 | path in migrated context: True |
| t10 | csv rows 17 | path in migrated context: True |
| t11 | csv rows 51 | path in migrated context: True |
| t12 | csv rows 48 | path in migrated context: True |
| t13 | csv rows 30 | path in migrated context: True |
| t14 | csv rows 28 | path in migrated context: True |
| t15 | csv rows 43 | path in migrated context: True |
| t16 | csv rows 325 | path in migrated context: True |
| t17 | csv rows 325 | path in migrated context: True |
| t18 | csv rows 8 | path in migrated context: True |
| t19 | csv rows 27 | path in migrated context: True |
| t20 | csv rows 76 | path in migrated context: True |
| t21 | csv rows 9 | path in migrated context: True |
| t22 | csv rows 27 | path in migrated context: True |
| t23 | csv rows 70 | path in migrated context: True |
| t24 | csv rows 5 | path in migrated context: True |
| t25 | csv rows 8 | path in migrated context: True |
| t26 | csv rows 26 | path in migrated context: True |
| t27 | csv rows 6 | path in migrated context: True |
| t28 | csv rows 8 | path in migrated context: True |
| t29 | csv rows 23 | path in migrated context: True |
| t30 | csv rows 13 | path in migrated context: True |
| t31 | csv rows 19 | path in migrated context: True |
| t32 | csv rows 12 | path in migrated context: True |
| t33 | csv rows 20 | path in migrated context: True |
| t34 | csv rows 26 | path in migrated context: True |
| t35 | csv rows 50 | path in migrated context: True |
| t36 | csv rows 104 | path in migrated context: True |
| t37 | csv rows 128 | path in migrated context: True |
| t38 | csv rows 50 | path in migrated context: True |
| t39 | csv rows 25 | path in migrated context: True |
| t40 | csv rows 17 | path in migrated context: True |
| t41 | csv rows 10 | path in migrated context: True |
| t42 | csv rows 10 | path in migrated context: True |
| t43 | csv rows 3 | path in migrated context: True |
| t44 | csv rows 53 | path in migrated context: True |
| t45 | csv rows 3 | path in migrated context: True |
| t46 | csv rows 10 | path in migrated context: True |
| t47 | csv rows 1 | path in migrated context: True |
| t48 | csv rows 11 | path in migrated context: True |
| t49 | csv rows 9 | path in migrated context: True |
| t50 | csv rows 7 | path in migrated context: True |
| t51 | csv rows 5 | path in migrated context: True |
| t52 | csv rows 6 | path in migrated context: True |
| t53 | csv rows 7 | path in migrated context: True |
| t54 | csv rows 10 | path in migrated context: True |
| t55 | csv rows 12 | path in migrated context: True |
| t56 | csv rows 8 | path in migrated context: True |
| t57 | csv rows 6 | path in migrated context: True |
| t58 | csv rows 11 | path in migrated context: True |
| t59 | csv rows 7 | path in migrated context: True |
| t60 | csv rows 171 | path in migrated context: True |
| t61 | csv rows 7 | path in migrated context: True |
| t62 | csv rows 12 | path in migrated context: True |
| t63 | csv rows 38 | path in migrated context: True |
| t64 | csv rows 8 | path in migrated context: True |
| t65 | csv rows 14 | path in migrated context: True |
| t66 | csv rows 3 | path in migrated context: True |
| t67 | csv rows 33 | path in migrated context: True |
| t68 | csv rows 33 | path in migrated context: True |

Every γ value decodes as a number (209/209). Concentration values: 0/212 survive as numbers (unsupported quantity; see below).

## 4. FACTS THE READER DROPS

- **Analysed Na2O wt% (212 concentration_series rows):** quantity `Na2O_concentration_in_quenched_melt_as_analysed` has no closed Quantity; `map_quantity` returns `unsupported quantity` (simulator/battery/migrate.py:4808-4812) and the value becomes `unavailable` ("refusing to pick a number"). The numbers remain in the extract values and in t1/t2/t38-t46 CSVs; the corrected wt%/mol% also ride in each γ row's values. Same situation as the landed molybdenum/NiO/CoO solubility extracts. Rows are kept because they are the lineage parents of the γ rows.
- **Table rows beyond the inventory sample for every CSV (t1-t68):** `_lift_extract_context` (migrate.py:11072ff) copies the inline context mapping (path, record count, first row) and does not open `table_file`. Tables t1, t2, t38-t46 are additionally carried row by row as observations; the rest are context-only.
- **Locator key `pdf_page`:** not a Locator field; the reader keeps `published_page` and folds `pdf_page=` into `note`. All new rows carry published_page (= PDF page - 23, checked on p145, p151, p162, p188).
- Fixed in this round (no longer dropped): experiment `sample.preparation` and `thermal_schedule.temperature_measurement/temperature_uncertainty` migrated to `{}`; now `sample.container/pretreatment`, `thermal_schedule.cooling_or_quench`, and bench temperature_measurement/temperature_calibration, all surviving.

## 5. AMENDMENTS PROPOSED

1. Add a closed Quantity for dissolved-oxide concentration / solubility in a quenched melt equilibrated with a fixed vapour (wt% or mol%, with T, fO2, PNa or a_source as conditions), so the 212 analysed-Na2O values can be scored.
2. Structure the chapter IV appendix composition tables (t20, t23, t26, t29: still raw `numeric_cells_left_to_right`) so the analysed glass composition can be joined to the 147 chapter IV γ rows (identity composition axis).
3. Optional: a run-condition axis for PNa / source activity (currently only in values and run-condition context).

## 6. Questions for main (P1/P2) - source inconsistencies carried as printed

- **P1 t44 (IV.7) γ scale:** heading prints γNa2O (x 10-7); a_source/x_Na2O with the caption aNa2O 4.83e-6 reproduces the printed mantissas only at ×10^-6. All 52 γ rows pending (`source_internally_inconsistent`), printed scale kept in `gamma_scale_as_printed`.
- **P1 t2 (III.2) temperature:** caption 1400 °C with aNa2O=4.07*10-08, PNa=5.50*10-06 atm, which the text (p149) assigns to the NS2 source at 1250 °C. All 47 rows pending; fO2 not landed.
- **P1 t43 (IV.6) fO2:** caption (p180) prints fO2 = 2.11*10-06 atm at 1250 °C under Ni/NiO, identical to the 1400 °C tables; fO2 not landed for t43 (kept as `fO2_atm_caption`).
- **P2 γ = a/x > 10% off:** t43 (2), t46 CMA16S1/CMA16S2/SPM, t38 NAS3 and NCAS3, t39 NAS3; pending.
- **P2 CMA5S1 PNa:** IV.1 prints 1.27 (0.05), IV.3 prints 1.01; both as printed.
- **P2 CMS21/CMS22 naming (III.2):** ACMS32 CMS21 analysed Na2O (6.97) does not match appendix A.8.2 CMS21 (14.4); composition join skipped for that row; text p149-150 discusses the non-equilibrium naming.

## 7. Every statement that qualifies the data, and every directional statement (80; verbatim)

Found by a directional-word scan (augmente/diminue/plus.../moins/supérieur/inférieur/négligeable/corrélation/increase/decrease/higher/lower/negligible...) of the embedded text layer of chapters II-VII (PDF p58-269; PDF is born-digital, Times fonts embedded), then curated to the author's own data and method statements; literature-attributed and structural-background statements excluded. Each quote was checked against the rendered page image: p77/p79 at 220 dpi, p215 at 150 dpi, the rest as 110 dpi crops of the matching lines (body text legible at that scale; disclosed because SEAT-COMMON asks for >= 220 dpi renders). Locator: PDF page / printed page.

1. **qualifier**, p60/37, cell design: “Tout d'abord le volume limité de ce dispositif, en diminuant l'évaporation de la source, permet de fixer la pression partielle en Na sur des temps longs (plusieurs jours) et d’atteindre plus facilement les conditions d'équilibre.” _[sentence runs across the page break PDF p60-61]_
2. **qualifier**, p63/40, glass synthesis: “Ensuite le creuset est porté à 1650°C pendant 1h, ou a 100°C au dessus du liquidus pour les compositions sodiques, afin de diminuer la perte d’oxyde de sodium par volatilisation, tout en permettant l’homogénéisation.”
3. **qualifier**, p63/40, glass synthesis: “Ensuite le creuset est porté à 100°C au dessus du liquidus pendant 15 minutes, afin de limiter la perte d’oxyde de sodium par volatilisation, tout en permettant l’homogénéisation du verre.”
4. **qualifier**, p65/42, fO2 uncertainty: “La PO2 dans l'ampoule peut être évaluée grâce à la figure II.2, avec une barre d'erreur inférieure à 0.2 unité log.”
5. **qualifier**, p67/44, source activity model: “A chaque température, l’écart entre les données expérimentales et le modèle est inférieur à 0.5 unité log.”
6. **qualifier**, p67/44, source activity model: “Néanmoins nos modèles ne sont valables que pour xNa2O supérieur a 20 mol%. Pour des valeurs plus faibles, l’activité tend vers une valeur constante, et s'écarte du domaine linéaire. Ainsi pour des valeurs de xNa2O, inferieures à 20 mol%, les valeurs d’activités considérées seront celle obtenues avec FactSage@.”
7. **qualifier**, p67/44, source state: “Pour les tampons avec une concentration en SiO2 inférieur à 88 wt%, à 1400°C, la source est liquide.”
8. **qualifier**, p77/54, temperature: “Preliminary temperature measurements in the device near the reactor reveals that despite the existence of a thermal gradient on the cell (≈3 °C cm−1), gradients inside the reactor (20 mm high) are relatively small due to the ability of the alumina of the support and the platinum of the crucible to homogenize the temperature, and that temperature uncertainty do not exceed ±5 °C.” _[≈, −1 and ± are symbol-font glyphs; read from the page image]_
9. **directional**, p79/56, equilibration kinetics vs fO2: “The fact that the attainment of equilibrium is faster in Co/CoO and Fe/FeO redox conditions than at Ni/NiO, is consistent with an increase in the rate of Na evaporation with decreasing fO2 [2,17,22,23].” _[two-column page; sentence order read from the page image]_
10. **directional**, p100/77, equilibration kinetics: “Results show that Na2O contents of the EDiAn melt increase rapidly with the exposure time and reached a steady state at a value of Na2O ≈ 13 wt% (Fig. 3).”
11. **qualifier**, p100/77, composition conservation: “After experiments, Si/Mg and Ca/Al ratio (Table 2) remain constant in the studied glass samples indicating that i) Na entering the melt from the gas phase simply dilutes the Na-free primordial composition (Table 1), ii) SiO(gas) entering from the gas phase, if any, is negligible” _[sentence continues with citations (Rego et al, 1988; ...)]_
12. **qualifier**, p103/80, composition conservation: “As observed for EDiAn, Si/Mg and Ca/Al ratio remain constant in the studied glass samples indicating that partial pressures of SiO(g) in the cell are negligible, and that no sample contamination occurs from the source.”
13. **directional**, p103/80, CMS solubility range: “The normalized values Na2O (wt%) or Na2O (mol%) are presented in Table 6, showing a very large range of variation, from 4.28 up to 30 mol%, of the sodium solubility in CMS melts.”
14. **directional**, p104/81, solubility vs SiO2/NBO/T: “The increase of the SiO2 content of the melt causes a drastic increase of the Na2O-solubility in the CMS melt, by almost an order of magnitude, form 4.28 mol% for silica-poor melts up to a maximum value of ≈ 30 mol% for a pure SiO2 melt, suggestive of the significant influence of the melt bulk polymerization (NBO/T) on the Na2O solubility.”
15. **directional**, p104/81, gamma vs SiO2, Ca-Mg: “Iso-activity coefficient curves drawn in the CMS system follows (Fig. 7) the same trend depicted for Na2O solubility curves with a significant decrease of γNa2Osample as function of SiO2 content and NBO/T. γNa2Osample curves are also sensitive to Ca-Mg exchange in the melt with a decrease of a γNa2Osample values as Mg is substituted”
16. **directional**, p105/82, solubility vs polymerization: “At first, the strong positive correlation between the Na2O-solubility and the silica content of the melt (Fig. 6 and 7) suggest that the degree of polymerization of melts is one of the key parameters controlling the Na-solubility in CMS melt, with increasing solubility with increasing melt polymerization.”
17. **directional**, p106/83, solubility vs MgO, CaO: “For Na, this assumption is confirmed by the influence of MgO or CaO contents, showing that when MgO or CaO contents increase, Na-solubility decreases.”
18. **directional**, p106/83, solubility vs Ca-Mg substitution: “At constant bulk NBO/T, we have seen indeed that the substitution of CaO by MgO causes a significant increases in the Na2O solubility in CMS melt (Fig. 6 and 7).”
19. **directional**, p107/84, phase relations vs Na: “the molten domain increase strongly (until a 150-200°C decrease of liquidus for MgO-rich compositions), confirming the melting effect of sodium, ii) more polymerized minerals (enstatite, wollastonite and quartz) are destabilized, iii) less polymerized minerals (forsterite and rankinite) are stabilized, e.g. for high Na-content.” _["Na- content" is hyphenated across a line end on the page]_
20. **directional**, p111/88, gamma ordering: “Indeed, for an equivalent optical basicity and equivalent temperature modeling, ln(γMgO) is 4-5 order of magnitude higher than ln(γCaO); and ln(γCaO) is 5-6 order of magnitude higher than ln(γNa2O) (this study and Beckett, 2002).”
21. **directional**, p111/88, solubility vs polymerization: “the higher the BO or Si-O-Si fraction in the CMS melt, the higher the Na-solubility.”
22. **directional**, p144/121, source activity NS3.5: “Ce tampon sodique va imposer une activité d’un ordre de grandeur plus faible dans le réacteur.”
23. **directional**, p146/123, CMS NS3.5 solubility: “La solubilité du Na augmente en direction du pole siliceux (NBO/T la pus faible) pour atteindre une valeur d’environ 22mol%, suggérant toujours un effet important de la teneur en SiO2 et donc du degré de polymérisation du liquide.”
24. **directional**, p148/125, phase relations vs PNa: “On peut noter que sous une PNa plus faible, le domaine liquide est plus petit, avec apparition dans ce cas de la Wo.”
25. **directional**, p149/126, source activity NS2 1250 C: “Ce tampon sodique, à température plus basse, va imposer une activité plus faible.”
26. **qualifier**, p150/127, non-equilibrium crystallised starts: “En effet la cinétique de condensation va avoir un rôle d’autant plus important que les compositions de départ sont cristallisées.”
27. **directional**, p151/128, CMS 1250 C iso-solubility: “Les courbes obtenues présentent toujours un aspect linéaire, subparallèle, dont la valeur augmente en direction du pole siliceux pour atteindre une valeur d’environ 22mol%.” _[sentence runs across the page break PDF p151-152 (printed page number 128 omitted); printed "sub-parallèle" hyphenated at line end]_
28. **directional**, p154/131, generalisation to T and PNa: “Des expériences avec le même tampon à plus basse température, ou avec un tampon plus pauvre en sodium à la même température, montrent que ces résultats sont généralisables à toute température et toute pression partielle en sodium, avec une solubilité plus faible.”
29. **directional**, p154/131, solubility vs optical basicity: “Les résultats obtenus dans l’article montrent une très bonne corrélation entre la solubilité et la basicité optique.”
30. **qualifier**, p159/136, run durations IV.1: “pour des temps variant de 48h à 116h.”
31. **qualifier**, p161/138, analysis location: “Pour ces compositions, les analyses ont été effectuées dans la partie de la charge la plus riche en verre.”
32. **directional**, p162/139, CAS solubility range: “Les résultats sur la solubilité du sodium montrent une très grande variabilité, avec des concentrations en oxyde de sodium variant entre 0.9 pour les compositions pauvres en SiO2 et en Al2O3 et jusqu’à 30 mol% pour la composition la plus riche en SiO2.”
33. **directional**, p162/139, CAS iso-solubility: “Les courbes obtenues présentent un aspect linéaire, sub-parallèle, avec une augmentation de la solubilité en direction du pôle siliceux jusqu'à environ 30 mol%.” _[sentence runs across the page break PDF p162-163]_
34. **directional**, p168/145, MAS vs CAS variability: “La solubilité du sodium dans ces compositions de liquide MAS varie entre 17 et 30 mol% de Na2O. Pour une même gamme de composition, on doit noter que cette variabilité est significativement plus faible que celle observée pour le système CAS.”
35. **directional**, p169/146, MAS iso-solubility: “Les courbes obtenues présentent un aspect linéaire, sub-parallèle et la valeur de solubilité augmente en direction du pole siliceux pour atteindre une valeur d’environ 30 mol%.”
36. **qualifier**, p172/149, glassy run products: “Toutes ces compositions ont un liquidus inférieur à 1400°C, et donc, sont vitreuses après expériences.”
37. **directional**, p173/150, CMAS5 solubility range: “La solubilité du sodium varie entre 3.3 et 17.2 mol% pour les compositions les plus siliceuses.”
38. **directional**, p174/151, iso-solubility IV.3: “Les courbes d’iso-solubilité présentent un aspect linéaire, sub-parallèle et dont la valeur augmente en direction du pole SiO2 pour atteindre une valeur d’environ 28 mol%.”
39. **directional**, p174/151, iso-solubility IV.3: “L’évolution de la solubilité de Na vers le pôle SiO2 n’est pas linéaire, avec une augmentation plus lente en se rapprochant de ce pôle.”
40. **directional**, p176/153, NS3.5 vs NS2: “Les résultats montrent une grande variation de la solubilité du sodium, avec des valeurs de solubilité plus faible qu’avec le tampon Na2O-2SiO2, la concentration en oxyde de sodium variant entre 2 et 11 mol%.”
41. **directional**, p177/154, iso-solubility IV.4: “Les courbes d’iso-solubilité en Na2O en mol% (figure IV.15a) présentent un aspect linéaire, sub-parallèle avec une solubilité qui augmente en direction du pole 5mol%Al2O3-95mol%SiO2 et augmente avec la teneur en silice.”
42. **directional**, p179/156, NS1 vs NS2: “Ils montrent une grande variation, et des valeurs de solubilité plus forte qu’avec le tampon Na2O-2SiO2 . En effet la concentration en oxyde de sodium sont toutes supérieures à 14 mol% jusqu’à des valeurs de 45 mol% pour la composition la plus siliceuse.”
43. **qualifier**, p180/157, IV.6 complementary: “Ces données sont complémentaires de celles présentées dans le chapitre précédent. Il est intéressant de noter l’équilibre melilite+liquide pour la composition CMA16S2.”
44. **directional**, p180/157, IV.7 range: “La large gamme des compositions présentées dans ce paragraphe entraîne une large gamme de solubilité en sodium mesuré, avec des concentrations en oxyde de sodium variant entre 0.5 et 27 mol%.”
45. **directional**, p183/160, NS8 vs other sources: “Les résultats montrent une grande variation, avec des valeurs de solubilité plus faible qu’avec les autres sources, variant de 0.18 mol% à environ 12 mol%.”
46. **directional**, p184/161, Al2O3 effect CAS: “Dans le système CaO-Al2O3-SiO2, pour des compositions avec un rapport Ca/Si inférieur à 0.43, l’ajout d’Al2O3 semble provoquer une baisse de la solubilité en sodium et une hausse du coefficient d’activité (figure IV.16). Par exemple, pour l’ajout de 30 mol% d’Al2O3, la solubilité diminue de l’ordre de 3 mol%.”
47. **directional**, p184/161, Al2O3 effect CAS: “Pour des compositions de rapport molaire CaO/SiO2 compris entre 0.43 et 0.66, l’ajout d’Al2O3 ne semble pas provoquer de variation de solubilité ou de coefficient d’activité.”
48. **directional**, p184/161, Al2O3 effect CAS: “Enfin pour des compositions avec un rapport Ca/Si supérieur à 0.66, l’ajout d’Al2O3 semble entraîner une hausse de la solubilité en sodium et une baisse du coefficient d’activité. Par exemple, pour l’ajout de 30 mol% d’Al2O3, la solubilité augmente de 5-6 mol%.”
49. **directional**, p186/163, Al2O3 effect MAS: “Pour le système MgO-Al2O3-SiO2, l’effet de la dilution semble être négligeable pour des compositions avec des rapports Mg/Si supérieurs à 0.10 (figure IV.17). Pour des compositions avec des rapports inférieurs à 0.10, l’ajout d’Al2O3 semble impliquer une baisse de la solubilité.”
50. **directional**, p188/165, Al2O3 series: “Pour la série CMS35+Al2O3, l’influence de l’addition d’Al2O3 sur la solubilité du Na est quasi nulle, même à des teneurs d’Al2O3 de 25mol%.”
51. **directional**, p188/165, Al2O3 series: “Pour les séries, CMS36+Al2O3, source NS1 et source NS2, l’influence de l’addition d’Al2O3 est aussi négligeable jusqu’à 18mol% d’Al2O. Au delà, une légère augmentation de la solubilité et une légère diminution du coefficient d’activité sont observées. Cependant, malgré l’ajout de 25 mol% d’Al2O3, notons que l’augmentation de la solubilité reste inférieure à 2 mol%. Enfin, pour la série CMS34+Al2O3, source NS2, 1400°C ; l’influence est quasi-nulle jusqu’à une valeur de 12-13 mol% d’Al2O3, puis nous pouvons observer une hausse de la solubilité de sodium ou une diminution du coefficient d’activité. Par exemple pour un ajout de 25 mol% d’Al2O3, l’augmentation de la solubilité est supérieure à 4 mol%.” _[runs across the page break PDF p188-189; "d’Al2O." is printed so (typo for Al2O3)]_
52. **directional**, p191/168, Al2O3 summary: “Néanmoins nous pouvons suggérer que la solubilité augmente avec la polymérisation en direction du pôle Al2O3. Pour le système CaO-Al2O3-SiO2, l’influence de l’ajout d’Al2O3 sur la solubilité du sodium est variable.”
53. **directional**, p192/169, Al2O3 summary: “D’après ces résultats, pour une composition du système CaO-SiO2 avec un rapport Ca/Si jusqu’à 1.08, l’ajout d’Al2O3 ne modifie pas la valeur de solubilité en Na ; mais ces résultats sont limités à des concentrations maximales de 12 mol% d'Al2O3. En revanche, pour des rapports Ca/Si inférieurs à 0.43, l’ajout d’Al2O3 entraîne une baisse de la valeur de solubilité et une hausse des coefficients d’activité. Pour des rapports Ca/Si supérieurs à 0.66, cet ajout va entraîner inversement une hausse de la solubilité et une baisse des coefficients d’activité.”
54. **directional**, p196/173, solubility vs NBO/T: “Nos résultats montrent que la solubilité diminue globalement avec le nombre NBO/T.”
55. **qualifier**, p197/174, NBO/T scatter: “la solubilité peut varier de façon importante ; par exemple pour NBO/T=1,5, la solubilité varie entre 0 et 18mol%.”
56. **directional**, p198/175, solubility vs basicity: “Une bonne corrélation existe entre la concentration en Na2O et la basicité, avec une relation linéaire pour des valeurs de basicité entre 12 et 58.”
57. **directional**, p199/176, solubility vs Sun basicity: “Deux zones sont observables: i) pour une basicité comprise entre 2,80 et 3,30; la solubilité est constante; ii) pour une basicité supérieure à 3,30; l'augmentation est linéaire, mais avec une dispertion des valeurs, notamment pour les compositions du système CaO-Al2O3-SiO2.”
58. **directional**, p199/176, basicity scale comparison: “La corrélation observée entre la basicité de Sanderson et la solubilité du sodium dans les liquides silicatés est moins bonne que celle obtenue avce la basicité de Sun.”
59. **directional**, p206/183, optical basicity linear range: “Le domaine linéaire se situe pour des valeurs de basicité inférieures à 0.68.”
60. **directional**, p209/186, solubility vs PNa: “Pour les séries à 1400°C, pour une basicité optique donnée, la solubilité augmente avec la PNa ou l’aNa2O de la source.”
61. **directional**, p209/186, solubility vs basicity: “Pour les compositions les plus dépolymérisées, qui correspondent aux basicités les plus élevées, la solubilité est faible à très faible.”
62. **directional**, p215/192, Al coordination: “Plus cette proportion est importante, plus elle semble favoriser la solubilité.”
63. **qualifier**, p215/192, Al coordination: “Néanmoins, ces proportions restent faible au regard de l’Al total, il est difficile de dire si l’augmentation de la solubilité du sodium est directement proportionnelle à ces teneurs en VAl.” _["VAl" is printed with a superscript V (five-fold Al); read from the page image]_
64. **directional**, p217/194, Mg vs Ca aluminosilicates: “A composition équivalente dans ces deux systèmes, la solubilité est plus importante dans le liquide magnésien. Par ailleurs, l’étude des courbes d’isosolubilité dans le système CMAS, à isoteneur en Al2O3 (par exemple 5mol%, paragraphe 3.2, chapitre IV) illustre également la différence Ca/Mg ; ce système se comporte comme le système CMS pour la solubilité en sodium. En revanche, la différence de pente NBO/T-courbe d’isosolubilité est plus faible que pour le système CMS.”
65. **directional**, p219/196, solubility vs PNa: “Pour une augmentation de PNa équivalente, l’augmentation de la teneur en Na2O est beaucoup plus importante pour le pôle SiO2 que pour la composition SPM.”
66. **directional**, p219/196, solubility vs PNa: “Par ailleurs, l’évolution de la solubilité en Na en fonction de la PNa semble suivre deux types d’augmentation : i) une augmentation linéaire et rapide de la solubilité pour des pressions partielles en sodium basses ; ii) une augmentation linéaire et lente pour les pressions partielles en sodium les plus élevées.”
67. **directional**, p219/196, solubility vs PNa, Henry: “Par exemple pour les compositions ; pôle SiO2, CMA16S1, et EDiAn, le domaine de Henry se situe pour une PNa inférieur à 1*10-6 atm. Pour la composition CMA16S2, la limite est de 10-5 atm. Et pour la composition SPM, le domaine de Henry est jusqu’à une PNa de 1.5*10-4 atm. Il semble donc exister une dépendance du comportement en loi de Henry à la composition du liquide.”
68. **qualifier**, p220/197, Henry regressions: “Nous avons effectué des régressions linéaires, pour les PNa les plus faibles.”
69. **directional**, p221/198, mixing line: “Le très bon coefficient de corrélation obtenu sur une très grande gamme de composition suggère en effet que la solubilité du sodium pourrait se résumer à un mélange entre ces deux pôles, les compositions intermédiaires se répartissant sur cette droite de mélange.”
70. **directional**, p238/215, chondrule mesostasis vs basicity: “on observe que la teneur en sodium diminue lorsque la basicité optique du liquide augmente, comme pour nos expériences, avec une relation de type linéaire.”
71. **directional**, p240/217, natural samples vs basicity: “Dans cette figure, on observe que globalement la teneur en sodium diminue lorsque la basicité optique du liquide augmente.”
72. **directional**, p244/221, capacity vs Lambda: “À travers cette étude, nous avons vu que la solubilité (ou la capacité) du liquide en Na2O diminue avec l'augmentation de Λ (figure VI.7).”
73. **directional**, p245/222, capacity vs Lambda: “La capacité en Na2O du liquide est donc d'autant plus forte que celui-ci est polymérisé (faible valeur de Λ).”
74. **directional**, p246/223, phase relations vs PNa: “D’autre part, le domaine liquide est moins étendu pour des PNa plus faibles.”
75. **directional**, p248/225, SiO2 effect: “L’ajout de SiO2 dans le système CMS va augmenter le liquidus et la viscosité en même tant que sa capacité en Na.”
76. **directional**, p248/225, modifier effect: “D’autre part, l'augmentation de la quantité de cations modificateurs de réseau diminue la solubilité de Na dans un liquide.”
77. **directional**, p249/226, CAS phase relations: “L’ajout de sodium va impliquer des changements encore plus importants que pour le système CMS (figure VI.12). Les deux domaines liquides se rejoignent, et s’étendent dans le domaine peralumineux. L’extension plus importante s’explique tout simplement par l’effet polymérisant de l’Al2O3.”
78. **directional**, p255/232, olivine Na vs melt Na: “Les valeurs présentent une certaine hétérogénéité, néanmoins globalement la teneur en sodium dans l'olivine augmente avec la concentration en oxyde de sodium du liquide” _[sentence ends without a full stop in the text layer before the next sentence]_
79. **directional**, p257/234, Na incompatibility: “Notre étude montre que le Na semble avoir un comportement encore plus incompatible qu'ils ne le suggèrent; ce qui implique, en supposant leur modèle vrai, que l'enrichissement autour des chondres doit être encore plus important qu'ils le supposent.”
80. **directional**, p266/243, conclusion: “Cette étude a montré que la solubilité du sodium augmente avec la teneur en SiO2. Par ailleurs, la substitution du calcium par le magnésium favorise la solubilité du sodium. Enfin, l’ajout d’Al2O3 peut favoriser ou non la solubilité du sodium.”

Also carried earlier (unchanged): VI.4 olivine D_Na vs FeO statement (PDF p256). Caption-level qualifiers (uncertainty conventions, une mesure, N.A./no liquid, assemblages) are in each table provenance and the run-condition context rows.

## 8. Acceptance (at 4330e0df; validator pointed at the corpus worktree by absolute path: `PYTHONPATH=<green> python tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/mathieu-2009-sodium-solubility-thesis.yaml`, run from the green checkout)

```
== validator (green 61ec839da)
OK: 1 extract file(s) valid
exit 0
== migrator + finalize
works 1 experiments 1 observations 421 context 100 queue 633
validation issues 202 hard 0 Counter({'identity_incomplete': 202})
quantities Counter({'State(tag=<StateTag.UNKNOWN: \'unknown\'>, value=None, reason="unsupported quantity \'Na2O_concentration_in_quenched_melt_as_analysed\'")': 212, "State(tag=<StateTag.VALUE: 'value'>, value=<Quantity.ACTIVITY_COEFFICIENT: 'activity_coefficient'>, reason=None)": 209})
== ledger test
674 passed in 1.57s
== evidence_for per method_class string:
  measured_tabulated 212 State(tag=<StateTag.VALUE: 'value'>, value=<EvidenceClass.MEASURED_TABULATED: 'measured_tabulated'>, reason=None)
  measured_reduced 209 State(tag=<StateTag.VALUE: 'value'>, value=<EvidenceClass.MEASURED_REDUCED: 'measured_reduced'>, reason=None)
  quoted_unattributed 99 State(tag=<StateTag.VALUE: 'value'>, value=<EvidenceClass.QUOTED_UNATTRIBUTED: 'quoted_unattributed'>, reason=None)
  figure_only 1 State(tag=<StateTag.VALUE: 'value'>, value=<EvidenceClass.FIGURE_ONLY: 'figure_only'>, reason=None)
== reduced rows 209 missing lineage/derivation 0
== admission {('concentration_series', 'admitted'): 187, ('activity_coefficient', 'admitted'): 127, ('concentration_series', 'pending'): 25, ('activity_coefficient', 'pending'): 82}
abs paths: 0
?? tools/__pycache__/
4330e0df Simon Rowland <simon@simonrowland.com> mathieu-2009: typed bench record for the sealed silica-ampoule cell
c563fde0 Simon Rowland <simon@simonrowland.com> mathieu-2009: carry 80 directional claims and data qualifiers from the prose
1000872c Simon Rowland <simon@simonrowland.com> mathieu-2009: add Na2O solubility and activity-coefficient observations
bbc5306a Simon Rowland <simon@simonrowland.com> Add seven uninventoried Mathieu tables and regenerate the table inventory
a4323ed6 Simon Rowland <simon@simonrowland.com> Fix Mathieu t1, t2, t44 column shifts, t38 missing rows and t27 durations
1
 27 files changed, 32681 insertions(+), 961 deletions(-)
pdfs: 0
```

Migrator: `Migrator(root=<green>, index={}, aliases={})._migrate_extract(<worktree extract>)` then `finalize()`; hard issues read from `result.validation.hard_issues`.

## 9. Not done / limits
- No figure digitisation (figures remain figure_only context, per brief).
- Chapter IV composition axis and the concentration quantity await the amendments above.
- Mac disk: 56 GB free at the last check (floor 30 GB).

