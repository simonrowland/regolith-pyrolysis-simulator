# REVIEW (first review): tsuchiyama-1998-forsterite-mg2sio4-h2 @ 293f51b694971ef9b42cb2965e3a5636b3946393

**From:** regolith-empirical (corpus review seat), **To:** regolith-main, **At:** 2026-10-05 ~22:45 ET. Answers REQ-corpus-batch11 item A.
Source: Tsuchiyama, Takahashi & Tachibana (1998), "Evaporation rates of forsterite in the system Mg2SiO4-H2", Mineralogical Journal 20(3) 113–126, DOI 10.2465/minerj.20.113.

VERDICT ON COMMIT: FIX-FIRST

rows checked 25, mismatches 10, printed numbers not carried 4

## How it was reviewed
- Branch `hunt/tsuchiyama-1998-forsterite-mg2sio4-h2` fetched from mirror `mac-studio-256-1:Repos/regolith-corpus.git`. origin tip = `293f51b694971ef9b42cb2965e3a5636b3946393` (matches the REQ). Commits over origin/main: 9992c1b1 claim, 293f51b6 extract. Changed files: extracts/<sid>.yaml, ledger/<sid>.yaml, raw/<sid>/sidecar.yaml (original_path made relative), tables/<sid>/{README.md,t1.csv,t2.csv}.
- SPARSE detached worktree on Simon's MacBook under `~/Repos/regolith-corpus/worktrees/` (sparse recipe from batch8 SEAT-COMMON; no build_index.py, no migrate_pilot_extracts.py). Removed after delivery.
- All 14 PDF pages rendered with `pdftoppm -r 220` and read from the images (Table 1, Table 2, Eqs. (1)–(2) also read from zoomed crops). The OCR text was not relied on.
- Readers: `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (read-only), interpreter `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, PYTHONPATH = that clone. `engines/engines.local.toml` EXISTS in that clone.

## Mechanical checks
| Check | Result |
|---|---|
| sidecar sha256 vs PDF | `706c40ef…eaf7f` = file sha256; size 1464675; extract `corpus_sha256` matches |
| Citation / DOI | match print (Vol. 20, No. 3, pp. 113–126, July 1998). No licence field in extract or sidecar (`access: publisher`, owner-supplied) — noted, not counted |
| `rg "/Users/\|/private/"` over extract, ledger, tables/<sid>/, sidecar | no matches |
| `tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/<sid>.yaml` (run from green clone, extract given by path) | `OK: 1 extract file(s) valid` |
| `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<extract>)` then `finalize()` | completes; **hard issues 0**; 18 observations; 1 queue item (`axes=['method']`, "source does not state method", Table 1) |
| Migrated values spot-check | FoH-1 0.00174 g cm⁻² → 0.01740 kg m⁻² at 1473.15 K; FoH-17 0.03246 → 0.32460 kg m⁻² at 1723.15 K; sample mass_kg 0.00032694 — correct conversions. **Per-row duration does NOT survive migration** (see R1) |
| corpus `python3 tools/test_ledgers_valid.py` | passes (rc 0) |

## Every table, equation and numeric statement (page = printed page; PDF index = page−113)
| Page | Item | Carried? |
|---|---|---|
| 113 Abstract | 1.4×10⁻⁵ bar H2; 1200–1450 °C; j_Fo=2480 exp(−372/RT); α 0.04–0.12; α "about 0.03 to 0.2" (compilation); α ≈ 0.1 FED/HRD | yes, except "about 0.03 to 0.2" (N4) |
| 114 | CaO 0.01 wt.%, Al2O3 0.02 wt.%; crystals "about 1 cm, 0.1 cm and 1 cm"; Mo-wire suspension; 1200–1450 °C; 1.4×10⁻⁵ bar; "durations ranging from 3 to 66 hours" | impurities yes; nominal size NO (N1); Mo wire NO (R3); 3–66 h carried with wrong note/locator (R2) |
| 115 Fig. 1 + text | Mo crucible 16 mm ID × 124 mm; Ta heater, Ta shield, W walls, stainless chamber; TMP/DP; Al2O3 not within ~7 cm; W5Re-W26Re TC-1/TC-2 (caption vs body conflict); Fe 1535, Ni 1453, Au 1064 °C, ±10 °C; ~10 min heat-up; 2/10/60 min cooling to 1000/500 °C/RT; <~10⁻⁸ bar base; H2 after ~8 min | all yes except ~10 min heat-up (N2); TC conflict correctly recorded |
| 116 Table 1 | 18 runs × 11 columns; ρ_Fo 3.227 g cm⁻³ | yes (values all match, see below) |
| 116 text | drift within 6×10⁻⁷ bar; ion gauge H2/N2 correction; ±0.01 mg; ±0.05 mm; EPMA JEOL733 15 kV, 12 nA | yes |
| 117 Figs. 2–3 | figure-only; axes; Fig. 3 "Errors are hidden in the symbols", p(H2)=1.4×10⁻⁵ bar | figure-only OK; "errors hidden in symbols" not carried (R8) |
| 117 text | ρ 3.227 (Fugino 1981); c-axis rate "twice or triple faster" than a/b at 1500–1800 °C in vacuum; anisotropy ignored; b0≪a0~c0 ⇒ ≈{010}; "proceeds lineally with time" | NO (N3, R8) |
| 118 | Eq. (1) j_Fo=2480 [mole cm⁻² sec⁻¹] exp(−372 [kJ mole⁻¹]/RT); Eq. (2) R_Fo=V_Fo j_Fo=1.09×10⁻⁵ [cm⁻² sec⁻¹] exp(−372/RT); Fig. 4 caption A-5 1350 °C 12 h, FoH-12 1250 °C 24 h, {3i2}, 10 µm; ~10% contaminant coverage; contaminant chemistry | values yes; locators wrong (R6); inconsistencies unflagged (R7) |
| 118–120 model | 18 species; Eqs. (3), (4), (5); α ≡ j_Fo/j_Fo^id; pFo^e 3.8×10⁻⁶ bar (1700 °C), 9.1×10⁻¹¹ bar (1200 °C); ~20% O at 1700 °C; n≈2.0 within ~1%; p_H2^1/2 | numbers yes; Eq. (4) and (5) yes; Eq. (3) and α definition NO (R9) |
| 121 Fig. 6 + text | figure-only; α 0.04→0.12 over 1200–1450 °C; α=1 at 1898 °C "as Sata et al. (1978) proposed for other oxides" | yes (Sata attribution advisory) |
| 122 Table 2 | 7 rows × 7 columns; footnote "* //(001)" | values yes; footnote mis-transcribed (R4); header note wrong (R5); float artefact (R10) |
| 122–123 text | recondensation (Tsuchiyama 1998), Mo slightly reducing; α≈0.1 at 1.4×10⁻⁵ bar 1200–1450 °C; errors within factor ~3; α≈0.1 in H2 and vacuum | yes |
| 124–126 Appendix | Eqs. (A1)–(A13); 18 species; 3.8×10⁻⁶ / 9.1×10⁻¹¹ bar | A2, A5, A9, A10 carried; A1, A3, A4, A6–A8, A11–A13 NOT carried (R9) |

## Row-by-row check (Table 1, published p. 116, PDF index 3). Columns: T/°C, t/h, initial g, final g, ΔM g, a0, b0, c0 cm, ΔX cm, ΔM/S g cm⁻²
Every cell of every row checked digit by digit against the 220-dpi crop; csv t1.csv and extract series are identical to print (extract drops trailing zeros only, e.g. 0.00400→0.004, 0.110→0.11). Reviewer arithmetic closure (not printed): recomputing ΔX from ΔM, a0 b0 c0 and ρ=3.227 under equal recession, and ΔM/S=ρΔX, reproduces every printed ΔX and ΔM/S to ±1 in the last digit; initial−final reproduces ΔM to ≤5×10⁻⁵ g (print rounds ΔM to 0.1 mg). This independently corroborates the transcription.
| row | csv/extract | print | match |
|---|---|---|---|
| FoH-1 | 1200,24,0.32694,0.32296,0.00400,1.013,0.110,0.920,0.00054,0.00174 | same | yes |
| FoH-14 | 1200,48,0.22010,0.21320,0.00690,0.680,0.110,0.980,0.00127,0.00409 | same | yes |
| FoH-18 | 1200,66,0.32673,0.31568,0.01110,0.985,0.110,1.005,0.00142,0.00459 | same | yes |
| FoH-11 | 1250,24,0.28954,0.27960,0.00990,0.900,0.110,0.910,0.00152,0.00491 | same | yes |
| A-10 | 1250,36,0.23530,0.22106,0.01420,0.880,0.095,0.955,0.00219,0.00708 | same | yes |
| FoH-12 | 1250,48,0.31620,0.29103,0.02520,0.895,0.110,0.990,0.00361,0.01166 | same | yes |
| A-7 | 1300,12,0.24089,0.22554,0.01540,0.890,0.080,1.110,0.00209,0.00674 | same | yes |
| A-6 | 1300,18,0.25300,0.23191,0.02110,0.915,0.100,0.875,0.00338,0.01091 | same | yes |
| A-4 | 1300,24,0.28208,0.25189,0.03020,0.890,0.090,1.110,0.00406,0.01311 | same | yes |
| A-2 | 1350,4,0.26392,0.25237,0.01160,0.915,0.100,0.980,0.00166,0.00535 | same | yes |
| A-9 | 1350,8,0.24339,0.21978,0.02360,0.890,0.080,1.110,0.00322,0.01041 | same | yes |
| A-5 | 1350,12,0.19937,0.16334,0.03600,0.890,0.070,1.105,0.00506,0.01634 | same | yes |
| FoH-19 | 1400,3,0.31822,0.29818,0.02000,0.920,0.110,1.000,0.00277,0.00895 | same | yes |
| FoH-16 | 1400,6,0.21530,0.18347,0.03180,0.675,0.100,0.980,0.00612,0.01975 | same | yes |
| FoH-15 | 1400,24,0.21697,0.07545,0.14150,0.680,0.100,0.980,0.03002,0.09689 | same | yes |
| A-1 | 1450,2,0.27608,0.25528,0.02080,0.920,0.100,0.925,0.00315,0.01016 | same | yes |
| FoH-20 | 1450,3,0.27443,0.24485,0.02960,0.920,0.100,0.985,0.00424,0.01370 | same | yes |
| FoH-17 | 1450,6,0.23935,0.18897,0.05040,0.665,0.130,0.910,0.01006,0.03246 | same | yes |

## Row-by-row check (Table 2, published p. 122, PDF index 9). Columns: material, T range, atmosphere, regime, pressure /bar, α, reference
| row | csv/extract | print | match |
|---|---|---|---|
| 1 | sintered powder, 1550-1800, vacuum, FED, ≥1.3×10⁻⁹, 0.05-0.1, Hashimoto (1990) | Sintered powder 1550-1800 Vacuum FED ≥1.3×10⁻⁹ 0.05-0.1 Hashimoto(1990) | yes |
| 2 | single crystal, 1500-1800, vacuum, FED, ≥1.3×10⁻⁹, 0.03-0.1, Wang et al. (1993b) | same | yes |
| 3 | single crystal, 1700, vacuum, FED, 2.6×10⁻⁹, 0.05, Nagahara and Ozawa (1996) | same | yes |
| 4 | single crystal*, 1500-1800, vacuum, FED, 1.3×10⁻⁹–1.3×10⁻¹⁰, 0.03-0.1, Nagahara et al. (1997); numeric endpoint 2 serialized `1.3000000000000002e-10` (csv and yaml) | 1.3×10⁻⁹-1.3×10⁻¹⁰ | **no (R10, float artefact; string field correct)** |
| 5 | single crystal, 1700, hydrogen, FED-HRD, 2.0×10⁻⁷–8.6×10⁻⁵, 0.03-0.2, Nagahara and Ozawa (1996) | same | yes |
| 6 | sintered powder, 1500, hydrogen, HRD, 6.2×10⁻⁵–5.6×10⁻⁴, 0.1, Hashimoto (1998) | same | yes |
| 7 | single crystal, 1200-1450, hydrogen, HRD, 1.4×10⁻⁵, 0.04-0.2, This study | Single crystal 1200-1450 Hydrogen HRD 1.4x10-5 0.04-0.2 This study | yes (but see R7a) |

## Experiment facts and typed absences (quote relied on)
- Method: typed method absence + context wording. OK ("heated in a vacuum furnace at a constant temperature … and a constant hydrogen pressure", p.114). Amendment for a method token is reasonable.
- Cell: "The samples were heated in a molybdenum crucible (16 mm and 124 mm in inner diameter and length)" p.115; "Each sample was hung with Mo-wires" p.114. Ta/W are heater, thermal shield and chamber walls ("The heater was made of tantalum, and the walls between the two chambers near the heater were made of tungsten", p.115). → R3.
- Orifice/channel `not_applicable`: OK (no effusion orifice in a two-chamber furnace with crucible open to the sample chamber).
- Ionization/multiplier/isotope `not_applicable` on bench: OK (no mass spectrometer). Context row says `ionization_energy_eV: not published`, `multiplier_or_isotope_corrections: not published` — inconsistent with the bench typing (advisory A1).
- Temperature measurement: body "measured by a W5Re-W26Re thermocouple (TC-2) … placed on the side of the sample, and was controlled by another … (TC-1) placed under the crucible" vs caption "TC-1 … for measuring temperature, and TC-2 … for controlling temperature" — conflict correctly recorded.
- Calibration: "calibrated against the melting point of Fe (1535°C), Ni (1453°C) and Au (1064°C) with the precision better than ± 10°C" — OK.
- Base pressure "<about 10⁻⁸ bar" → <0.001 Pa approx — OK. H2 1.4×10⁻⁵ bar → 1.4 Pa — OK. Drift "within 6×10⁻⁷ bar" — OK. Ion gauge "corrected by using the ionization sensitivities of H2 and N2 gas" — OK.
- Durations: "durations ranging from 3 to 66 hours" (p.114) vs Table 1 A-1 = 2 h → R2.
- Activities / standard states: none reported; correctly asserted absent.
- method_class: Table 1 `measured_tabulated` acceptable (ΔX, ΔM/S are author arithmetic on weighed ΔM; table_provenance says so). Table 2 `model_derived` acceptable (α = measured/model-ideal). Rate fits held as context with `derived_from` Table 1 — acceptable under the standing ruling.

## Mismatches (10)
1. `experiments[0].thermal_schedule.total_duration_s` note "Table 1 durations: 3–66 h." is false: Table 1 prints A-1 = 2 h (p.116). The 3–66 h is prose on p.114 (locator says p.115). (R2)
2. Table 2 footnote carried as `“* // (001) for other oxides.”` — the footnote prints only `* //(001)`; "for other oxides." is the end of the body sentence continuing from p.121 ("…as Sata et al. (1978) proposed / for other oxides."). (R4)
3. Table 2 `notes` claims the header image reads "Starting material" and "Evaporation regime" and that "String material"/"Evaportion regime" are OCR errors. The print itself reads `Strting material (forsterite)`, `Atmospere`, `Evaportion regime`, `Refference` (source typos). (R5)
4. `benches[0].cell_materials` lists Mo, Ta, W; the printed cell (`cell_material_and_liner`) is the Mo crucible only — SCHEMA: "cell_materials records each material stated in cell_material_and_liner". Ta/W are heater/shield/walls. (R3)
5. `fidelity_samples[1]` locator published_page 117 / pdf 4 for Equation (1): Eq. (1) is on p.118 (pdf 5). (R6)
6. `tsuchiyama_1998_measured_rate_fit` locator published_page 114 / pdf 1 and note "PDF pp. 1 and 5; published pp. 113 and 117": correct is p.113 (pdf 0, abstract) and p.118 (pdf 5, Eq. (1)). (R6)
7. `tsuchiyama_1998_normal_rate_fit` locator 117/4: Eq. (2) is on p.118 (pdf 5). (R6)
8. `tsuchiyama_1998_surface_observations` locator 117/4 "Results and Fig. 4": steps/hillocks/etch pits/contaminant text and Fig. 4 are on p.118 (pdf 5). (R6)
9. `tsuchiyama_1998_figure_only_curves` locator published_page 116 / pdf 3: Figs. 2 and 3 are on p.117 (pdf 4). (R6)
10. Table 2 row 4 numeric pressure endpoint 2 serialized `1.3000000000000002e-10` in t2.csv and extract; print is 1.3×10⁻¹⁰. (R10)

## Printed numbers not carried (4)
N1. Nominal crystal size "about 1 cm, 0.1 cm and 1 cm nearly parallel to the a, b and c-axes" (p.114).
N2. "It takes about 10 min from room temperature to reach a desired heating temperature" (p.115).
N3. "the evaporation rate along c-axis is by twice or triple faster than those along a- and b-axes at 1500-1800°C in vacuum (Nagahara et al., 1997)" (p.117) — factor 2–3 and 1500–1800 °C, attributed.
N4. Abstract: "The evaporation coefficients varies from about 0.03 to 0.2 in a wide range of temperature and H2 pressure" (p.113).

## VERDICT ON COMMIT: FIX-FIRST — required changes
- **R1 (binding; scoring-critical).** Each Table 1 ΔM/S observation must carry its own printed run duration through migration. Today the migrated observations carry T and sample mass but no duration (`exposure=None`, no duration anywhere in the migrated Observation), so ΔM/S cannot be turned into a rate or compared with the fit. Follow the landed pattern in `extracts/ta-shirai-2000-lpsc.yaml` / `kems-031-halwax-2024.yaml`: per-run experiment records (or the supported per-row home) with `thermal_schedule.setpoints_and_holds: [{temperature_K, hold_duration_s}]`, Table 1 p.116 locator, h→s conversion noted; re-run migrator+finalize and show the duration on the migrated row. If no supported home binds it, say so under AMENDMENTS and keep the extras, but do not leave it unstated.
- **R2.** `total_duration_s`: either use Table 1's 7200–237600 s (2–66 h, locator Table 1 p.116) or keep the prose 3–66 h with locator p.114 (pdf 1); in both cases record `source_internally_inconsistent: prose p.114 says 3–66 h; Table 1 p.116 prints A-1 at 2 h`. Remove the false "Table 1 durations: 3–66 h".
- **R3.** `cell_materials`: Mo only (Mo crucible p.115; sample hung with Mo wires p.114). Remove Ta and W from `cell_materials` (they remain in `other_facts` as heater/shield/wall materials; add Ta thermal shield, Fig. 1 caption). Add the Mo-wire suspension to `cell_material_and_liner` or `sample.form` with p.114 locator.
- **R4.** Table 2 footnote: `* //(001)` only (meaning the Nagahara et al. 1997 crystal was //(001), as printed); drop "for other oxides." from table_provenance. Optionally attach "as Sata et al. (1978) proposed for other oxides" (pp.121–122) to the α=1 at 1898 °C claim.
- **R5.** Replace the header note with the printed header: `Strting material (forsterite) | Temperature range /°C | Atmospere | Evaportion regime | Pressure range /bar | Range of α | Refference` (source typos, not OCR errors).
- **R6.** Locators: Eq. (1) → published 118 / pdf 5 (fidelity_samples[1] and measured_rate_fit; abstract occurrence 113 / pdf 0); Eq. (2) → 118 / pdf 5; surface_observations + Fig. 4 → 118 / pdf 5; figure_only_curves Figs. 2–3 → 117 / pdf 4.
- **R7. Flag source-internal inconsistencies (carry as printed, do not correct; `reason: source_internally_inconsistent: …`):**
  (a) Table 2 "This study" α range prints 0.04-0.2 (p.122) while abstract (p.113) and p.121 print 0.04–0.12.
  (b) Fig. 4 caption (p.118) gives Run FoH-12 as "1250°C, 24 hrs"; Table 1 (p.116) prints FoH-12 at 1250 °C for 48 h. The extract's surface-observation row repeats "24 h" unflagged.
  (c) Eq. (2) prints R_Fo = V_Fo j_Fo = 1.09×10⁻⁵ [cm⁻² sec⁻¹] exp(−372/RT). Reviewer arithmetic (not printed): V_Fo = M/ρ ≈ 140.7/3.227 ≈ 43.6 cm³ mol⁻¹, so 2480 × 43.6 ≈ 1.08×10⁵ cm s⁻¹; the printed 10⁻⁵ exponent and the [cm⁻² sec⁻¹] unit are both inconsistent with Eq. (1). The extract already flags the unit; extend the flag to the exponent so nobody uses 1.09e-5 as a usable prefactor. (The extraction report also misquotes Eq. (2)'s unit as "cm s−1"; report only.)
- **R8.** Carry N1–N4 as located context, plus these printed qualifiers: "the evaporation proceeds lineally with time" (p.117); "this anisotropy was ignored for simplicity" and "Because the parallelepipeds are plates with b0<<a0~c0, the evaporation rate obtained from the experiments are nearly equal to those of {010} surfaces" (p.117); Fig. 3 caption "Errors are hidden in the symbols" (p.117).
- **R9.** Carry the remaining printed equations as context strings (model row, context only): Eq. (3) j_Fo(FED)=α j_Fo^id(FED) and the definition α (= j_Fo/j_Fo^id) (p.120); (A1) Hertz-Knudsen flux with α, (A3), (A4), (A6)–(A8), (A11), (A12), (A13) (pp.124–126), each with its equation locator. Eqs. (4), (5), (A2), (A5), (A9), (A10) are already carried.
- **R10.** Write Table 2 row 4 endpoint 2 as `1.3e-10` in t2.csv and the extract.

Advisory (not required): A1 harmonize context-row `ionization_energy_eV: not published` / `multiplier_or_isotope_corrections: not published` with the bench's `not_applicable`; A2 the extraction report places p.118 statements (steps/hillocks, contaminants, Eqs. (1)–(2)) on p.117; A3 attribute the recondensation-flux estimate on p.122 to Tsuchiyama (1998) as printed.

Hard-gate status at this tip: fidelity validator OK; migrator+finalize hard issues 0; ledgers test passes; no absolute paths; sidecar sha OK. The FIX-FIRST is for fidelity, binding and locator content, not gates.

!COMPLETE: rev-tsuchiyama-1998-forsterite-mg2sio4-h2 — FIX-FIRST, pages read 14, rows checked 25, mismatches 10, printed numbers not carried 4
