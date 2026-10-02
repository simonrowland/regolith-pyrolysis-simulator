# REVIEW — Fe/Mg extraction batch 2 (five one-paper commits)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` (`.slot-busy` cleared after STATUS ship)
- **Date:** 2026-10-01 ~23:35 ET
- **Mode:** read-only on tips; extracts not edited; tips not rebased onto green
- **Policy gate:** use-values-first / wrong-number + printed-source guards only
- **REQ:** `REQ-review-femg-batch2-2026-10-01.md`
- **Corpus:** PDFs under ferry-inbox/acquired; page images via pdftoppm; Costa van't Hoff re-digitised on 300 dpi page-9

Reviewed each tip as given (SHAs below). Parent/green tip not required for values guards.

---

## 1) Hashimoto 1983 — `c04bff249ab1ee8107dab4c1776c0087c45f20af` (kems-015-hashimoto-1983)

### Wrong-number guards (Table 3 p.116 + Table 1 p.113)
All **24 measured** residue composition rows vs printed Table 3 (SiO2/Al2O3/FeO/MgO/CaO + VF + splash parentheses). Sampled ≥5 and exhaustively matched:

| Run | VF | FeO | MgO | tip vs page |
|---|---:|---:|---:|:---:|
| 17C3(2) | 10.8 | 27.92 | 26.32 | yes |
| 17C5(1) | 18.6 | 22.74 | 27.88 | yes |
| 17C7 | 27.2 | 15.53 | 30.97 | yes |
| 17D2 | 43.3 | 2.88 | 37.81 | yes |
| 18B8(1) | 17.2 | 23.82 | 28.56 | yes |
| 19B8 | 38.7 | 11.49 | 34.77 | yes |
| 20B6 | 52.5 | 3.15 | 41.10 | yes |

**24/24 measured rows PASS** (oxides + VF + splash flags). Seven n.d. / V rows correctly left null (not zero). T mapping 17xx→1700 °C / 1973.15 K … 20xx→2000 °C / 2273.15 K consistent.

Table 1 starting Ave. (wt%): SiO2 35.43, Al2O3 3.16, FeO 35.04, MgO 23.84, CaO 2.53 — tip `printed_composition` matches; wt%→mole_fraction declared on experiments.

### Counts / admission
- 24 admitted per-run residue wrappers under Fe (5 oxides × 24 = **120** oxide cells) + parallel Mg/SiO2/CaO/Al2O3 series of 24 measured each.
- `quantity: residue_composition_vs_time`, `semantics: composition_table_not_species_rate`, free_evaporation / graphite crucible as printed.

### Nothing invented
- n.d. compositions not filled; orifice/crucible dims from printed apparatus section; no invented species rates for Table 3 residue rail.

### NOT-FIXED lens
- Cannot score residue composition yet — **ticket t-1078** (no engine predicts residue composition). Scorer/engine gap, not extract wrong-number.
- Store regen: skipped on ~16GB box (values guards complete).

**VERDICT lean:** LAND

---

## 2) Guo 2021 — `a580172d6b8d36aa1c4edfd2a56cc56a370530d6` (guo-2021-mgo-activity-cmas-slag)

### Wrong-number guards (Table 5 p.2728 + Table 6 p.2730)
Table 5 — 8 a(MgO) + slag wt% + x[Al/Si/Ca/Mg] in Sn at **1873 K**:

| No | a(MgO) page | tip | CaO | MgO | match |
|---|---:|---:|---:|---:|:---:|
| 1 | 0.4478 | 0.4478 | 35.41 | 9.26 | yes |
| 2 | 0.5121 | 0.5121 | 36.62 | 9.46 | yes |
| 4 | 0.6376 | 0.6376 | 39.32 | 9.77 | yes |
| 5 | 0.4236 | 0.4236 | 41.87 | 4.63 | yes |
| 8 | 0.6472 | 0.6472 | 38.58 | 10.22 | yes |

**8/8 Table 5 PASS** (all oxide + Sn mole-fraction cells). wt%→mole_fraction declared.

Table 6 — 8 γ(Mg in Sn) + a[Mg](R) + x[Mg]:

| x[Mg] | a[Mg](R) | γ page | tip γ | match |
|---:|---:|---:|---:|:---:|
| 0.001355 | 0.004297 | 3.1713 | 3.1713 | yes |
| 0.00155 | 0.004914 | 3.1704 | 3.1704 | yes |
| 0.001929 | 0.006118 | 3.1718 | 3.1718 | yes |
| 0.001959 | 0.006211 | 3.1703 | 3.1703 | yes |

**8/8 Table 6 PASS.**

### Method / standard state
- Experiment method: **`quench_equilibration`** (slag–metal Sn; controller; no new enum) — matches REQ.
- T_range_K [1873, 1873]; MgO standard_state pure solid MgO as printed; phase liquid melt.
- Composition conversion declared on both species.

### Nothing invented
- Iso-activity contour figures not digitised; reference-slag Table 4 x[Mg] replicates retained as printed (0.00301–0.00303, avg 0.00302, a_ref=1).

### NOT-FIXED lens
- MgO solid→liquid conversion depends on separate tip `review/mgo-fusion` `3d7a1cf69` — scoring dependency, not a wrong number in this extract.
- Mg-in-Sn γ correctly unsupported for OpenIMCC / melt-activity scoring (model_derived metal solvent γ).
- Store regen skipped (16GB).

**VERDICT lean:** LAND

---

## 3) Costa 2015 — `2883c54d02355d2365b3a98e26ca150d3b9d43eb` (kems-007-costa-2015)

### Re-digitisation (≥5 points, independent marker centers vs tip)
300 dpi page-9 van't Hoff (3300×2550). Axis least-squares from tip calibration ticks (x: 10⁴/T; y: −log10(P/kPa)). Stated tip uncertainty: ±11 px half-marker (or larger axis residual).

| Point | tip px | indep. color centroid | Δpx | ΔT (K) | Δ(−log10 P) | within ±11 px |
|---|---|---|---:|---:|---:|:---:|
| Fe IR[0] | (580,900) | (580.98,900.13) | (+0.98,+0.13) | −0.51 | +0.0007 | yes |
| Fe IR[2] | (787,1013) | (787.00,1013.89) | (+0.00,+0.89) | 0.00 | +0.0051 | yes |
| Fe MO[2] | (1155,1019) | (1154.57,1018.45) | (−0.43,−0.55) | +0.17 | −0.0031 | yes |
| Mg IR[0] | (580,955) | (581.57,956.06) | (+1.57,+1.06) | −0.82 | +0.0060 | yes |
| Mg IR[2] | (787,1144) | (786.66,1143.64) | (−0.34,−0.36) | +0.16 | −0.0021 | yes |
| Mg MO[2] | (1155,1188) | (1155.33,1187.39) | (+0.33,−0.61) | −0.13 | −0.0035 | yes |

**6/6 PASS.** Axis tick residuals ≤0.005 in displayed units. Tip conversions `T_K=10000/x` and `p_Pa=1000·10^(−display)` recompute exactly from tip pixels. Fo93Fa7 composition as printed; digitisation authorised for this paper.

### Counts
- Fe PP: Ir 4 + Mo 5 admitted (this study); Re/Piacente comparator retained separately.
- Mg PP: Ir 4 + Mo 5 admitted; plus time-equilibrium / ion-ratio / alpha series as figure-digitised.
- Tip fidelity_samples include independent color-mask repeats (e.g. Fe IR[2], Mg MO[2]).

### Nothing invented
- Fit equations beside plot not digitised; Au melting-point T calibration noted as printed; no invented pressure calibrant.

### NOT-FIXED lens
- Scorer apparatus / effusion-regime paths may still refuse some KEMS rows — not wrong-number in the digitised Fe/Mg PP set.
- Store regen skipped (16GB).

**VERDICT lean:** LAND

---

## 4) Yakovlev 1984 (REVISED) — `60db4ba13acd1288b5eafc4acb5d38777a130605` (kems-028-yakovlev-1984)

### Batch-1 REVISE focus (range-maxima)
Prior REVISE (`617741fad…`): SiO and Ca range-maxima still typed as points. This tip:

| Observation | status | semantics | PASS? |
|---|---|---|:---:|
| Fe max 1450–1725 °C | **rejected** | bound_not_point_ordering | yes |
| Mg max 1750–1825 °C | **rejected** | bound_not_point_ordering | yes |
| SiO max 1750–1825 °C (`…SiO_Mg_max_1750_1825C`) | **rejected** | bound_not_point_ordering | yes |
| Ca max 1950–2025 °C | **rejected** | bound_not_point_ordering | yes |
| CAI SiO max @ **1875 °C** (single T) | point kept (`p_torr=0.0062`) | — | yes |
| Al max @ **2100 °C** (single T) | point kept (`p_torr=0.0017`) | — | yes |

Rejected rows no longer carry `p_torr` as admitted points — correct fix class.

### Wrong-number guards (POINT pressures, p.945–946)
Sampled ≥5 against page / prior batch-1 crops:

| Point | tip p_torr | page |
|---|---:|---|
| Na @ 1225 °C | 2.7e-4 | 2.7×10⁻⁴ tor |
| K @ 1225 °C | 4.2e-5 | 4.2×10⁻⁵ tor |
| Fe troilite @ 1050 °C | 1.8e-5 | 1.8×10⁻⁵ tor |
| FeO top @ 1650 °C | 8.1e-5 | 8.1×10⁻⁵ tor |
| SiO CAI max @ 1875 °C | 6.2e-3 | 6.2×10⁻³ tor |
| Ca @ 1825 °C | 7.5e-5 | 7.5×10⁻⁵ tor |
| Al max @ 2100 °C | 1.7e-3 | 1.7×10⁻³ tor |

Torr→Pa ×133.322 consistent. Figs 1–2 figure_only.

### Nothing invented
- No numerical bulk composition printed — tip notes match. Apparatus cited to prior technique paper; cell dims / calibrant not invented.

### NOT-FIXED lens
- Remaining scoring blockers are scorer/apparatus (calibration / effusion regime), not further wrong numbers in the admitted POINT set.
- Store regen skipped (16GB).

**VERDICT lean:** LAND

---

## 5) O'Neill 2002 — `e644f212033d26c98d8f916d8e850d13fc3a7faf` (oneill-2002-feo-activity-coefficients-cmas)

### Wrong-number guards (Table 7 p.163 + Table 4 p.161)
Table 7 γ at **1400 °C** (1673.15 K): FeO 19 + NiO 19 + CoO 19 + MoO2 17 + MoO3 17 = **91** cells. Exhaustive match vs printed table (dashed Mo cells for CMAS7-G / CAS3 correctly omitted):

| Melt | γFeO page | tip | γNiO | γMoO2 | match |
|---|---:|---:|---:|---:|:---:|
| AD eutectic | 1.367 | 1.367 | 2.52 | 136 | yes |
| AD + Fo | 1.468 | 1.468 | 2.70 | 131 | yes |
| AD + En | 1.167 | 1.167 | 2.30 | 168 | yes |
| CMAS7-E | 1.450 | 1.450 | 2.70 | 114 | yes |
| CAS1 | 1.863 | 1.863 | 3.56 | 44 | yes |
| CAS3 | 2.263 | 2.263 | 4.39 | (—) | yes |

**91/91 PASS.**

FeO rows carry Table 4 EDS comps (wt% inferred; sum≈100) + printed X_FeO; wt%→mole_fraction declared. Sampled all 19 FeO composition rows (incl. CAS2–4):

| Melt | FeO wt% | XFeO | match |
|---|---:|---:|:---:|
| AD eutectic | 3.49 | 0.0269 | yes |
| AD + Wo | 3.75 | 0.0299 | yes |
| CMAS7-F | 5.42 | 0.0420 | yes |
| CAS2 | 2.53 | 0.0202 | yes |
| CAS4 | 3.91 | 0.0315 | yes |

### fO2 conversion
- Table 4 caption: log fO2 = **−12.61**; tip `log_fO2` units log10(bar); `oxygen_partial_pressure` = 10^(−12.61) bar with declared `fO2_Pa = 10^(−12.61)·100000` (→ 2.455×10⁻⁸ Pa). Conversion declared and numerically right.

### Nothing invented
- Method `quench_equilibration`; standard state pure liquid FeO (Table 6 Fe(s)+0.5O2=FeO(liq)); Mo dashed cells not fabricated; parenthetical 1σ retained in `gamma_as_printed`.

### NOT-FIXED lens
- Worker OpenIMCC score (19 FeO residuals, median −0.085 dex) is a scorer result, not an extract wrong-number gate.
- Store regen skipped (16GB).

**VERDICT lean:** LAND

---

## Store regen

VPS ~16GB RAM: skipped `battery_migrate --dry-run` this seat (targeted values guards only). Counts above are extract-side. Ask Mac Studio if a green-gate migrate census is needed.

— regolith-empirical
