# A4 — `identity_incomplete` advisories for `regolith-empirical`

Generated 2026-09-22 21:27 EDT from the derived v2.1 store + `data/battery/migration-report.md` at detached `origin/review/r6-r8-fix` (`1f8df6cbe`). Method: `load_migrated_store` → `validate_corpus`; census of issues with `RefusalReason.IDENTITY_INCOMPLETE` (the only advisory reason). No product files were changed and no product commit was made.

## Executive readout

| Measure | Count |
|---|---:|
| Advisory issues (`identity_incomplete`) | **134,518** |
| Observations in derived store | **91,588** |
| Observations with ≥1 advisory | **41,466 (45.3%)** |
| Observations with zero advisories | **50,122** |
| Hard issues (separate; do fail `ValidationReport.ok`) | **1,468** |
| `ValidationReport.ok` | **False** (because of hard issues only) |

Every advisory detail string is of the form `required axis <name> is unknown` (or the charge-absent variant). On this tree **zero** advisories were `species.charge` absences — charge is populated; the holes are quantity-profile **required axes left as `State.unknown`**.

**Does it block any consumer?**

| Consumer | Blocked by advisory? | Notes |
|---|---|---|
| `battery_migrate` / store write | **No** | `IDENTITY_INCOMPLETE` is in `_ADVISORY_REASONS`; exit status keys off `hard_issues` only. |
| `ValidationReport.ok` | **No** (for these) | Advisories do not flip `ok`; hard issues do. |
| Identity equality (`identity_equal`) | **Yes (soft)** | Required-axis unknown → `IdentityEqualKind.IDENTITY_UNKNOWN`; sample of 5,000 incomplete obs: **100%** self-compare as `identity_unknown`. |
| `battery_score` / engine probe | **Yes (soft)** | Score refuses with `IDENTITY_UNKNOWN` when quantity/axes cannot support a comparable identity (score-report already shows large `identity_unknown:*` refusal mass). Clearing advisories unblocks **scoring equality**, not engine physics. |
| `bench_readiness` / `engine_point` | **No (directly)** | Waypoints (`composition` / `fO2` / `T` / `P` / bench) come from experiment/consumer inputs, not from observation identity-profile completeness. A3 gaps remain the readiness blockers. |

**Bulk-fixable?** Mostly **yes, ENGINEERING**, concentrated in formation / standard-thermo compilation lifts (species-rail + JANAF + USGS). A minority of `temperature_K` / melt-experiment axes need DATA or must stay PERMANENT unknowns.

## Missing fields (advisory issue counts)

| Rank | Axis | Advisory count | Share of 134,518 | Bulk fixability |
|---:|---|---:|---:|---|
| 1 | `reaction` | 31,222 | 23.2% | **ENGINEERING** — lift formation/vaporization reaction already implied by quantity + formula (esp. `delta_fG` / `delta_fH` / `log10_Kf`) |
| 2 | `formation_elements` | 30,905 | 23.0% | **ENGINEERING** — same formation-identity cluster as `reaction` |
| 3 | `per` | 24,622 | 18.3% | **ENGINEERING** — default / map molar basis for standard formation & activity rows |
| 4 | `standard_pressure_Pa` | 22,714 | 16.9% | **ENGINEERING** — stamp declared standard (1 bar / 1 atm) from compilation metadata; do not invent where source is silent |
| 5 | `temperature_K` | 17,176 | 12.8% | **MIXED** — range-only / unstated T: keep unknown or range-aware; point T already printed → mapper fill |
| 6 | `subtype` | 3,180 | 2.4% | **ENGINEERING** — mostly `H_minus_H298` reference-T subtype |
| 7 | `total_pressure_Pa` | 1,041 | 0.8% | **MIXED** — activity / partial-pressure / kinetics rows |
| 8 | `fO2_Pa` | 977 | 0.7% | **DATA / PERMANENT** — often truly unstated on melt/activity rows |
| 9 | `composition` | 784 | 0.6% | **DATA** — extract printed melt composition (overlaps Ferry Z extract lanes) |
| 10 | `reference_state` | 775 | 0.6% | **ENGINEERING** — activity / partial-pressure reference-state vocabulary |
| 11 | `reservoir` | 612 | 0.5% | **MIXED** — vapour / evaporation identity |
| 12 | `exposure` | 202 | 0.2% | **DATA** — kinetics / pyrolysis |
| 13 | `sweep_gas` | 202 | 0.2% | **DATA** — kinetics / pyrolysis |
| 14 | `sample_mass_kg` | 106 | 0.1% | **DATA** — extensive rates |

All 134,518 details are “required axis … is unknown” (no absent-axis variant in this census).

## Axes per incomplete observation

| Distinct incomplete axes on one obs | Observations |
|---:|---:|
| 1 | 9,515 |
| 2 | 281 |
| 3 | 6,745 |
| 4 | 21,621 |
| 5 | 2,638 |
| 6 | 403 |
| 7–9 | 263 |

Most incomplete rows carry **3–4** holes together (formation cluster: `reaction` + `formation_elements` + `per` + `standard_pressure_Pa`).

## By quantity (incomplete observations)

| Quantity | Incomplete obs | Dominant missing axes |
|---|---:|---|
| `delta_fG` | 26,510 | `formation_elements`, `reaction`, `per`, `standard_pressure_Pa`, (`temperature_K`) |
| `H_minus_H298` | 5,146 | `subtype`, `temperature_K` |
| `log10_Kf` | 2,204 | `formation_elements`, `reaction`, `temperature_K` |
| `delta_fH` | 2,191 | `formation_elements`, `reaction`, `temperature_K` |
| `cp` | 2,122 | `temperature_K` |
| `S` | 2,117 | `temperature_K` |
| `p_partial` | 317 | reservoir / reference_state / reaction / composition / fO2 / P |
| `activity` | 284 | fO2 / P / per / reference_state / composition |
| `activity_coefficient` | 160 | reference_state / composition / fO2 / P / per |
| Other (kinetics, p_sat, …) | ~1,418 | reservoir / exposure / sweep_gas / composition / … |

## By source (incomplete observations, top)

| Source | Incomplete obs | Likely cluster |
|---|---:|---|
| `species-rail-differential` | 21,167 | Formation ΔfG / rail ledger lift |
| `nist-janaf-4th` | 12,702 | JANAF standard-thermo / formation axes |
| `robie-waldbaum-1968-usgs-b1259` | 3,029 | USGS compilation (`H_minus_H298` subtype heavy) |
| `nasa-cea-thermo` | 1,618 | CEA formation / thermo |
| `robie-hemingway-1995-usgs-b2131` | 824 | USGS |
| `pankratz-1987-usbm-b689` | 626 | USBM |
| KEMS / melt papers (rest) | ~1,500 | composition / fO2 / P / kinetics axes |

Top-3 sources alone = **36,898 / 41,466 (89%)** of incomplete observations → bulk ENGINEERING on compilation generators, not per-paper extraction.

## Unblock estimates (ranked)

Counts below are **observation** unblocks if the named axes are filled without introducing new holes. Advisory-issue unblocks ≈ sum of those axis counts (one obs can clear multiple issues).

| Priority | Action | Approx. obs unblocked | Approx. advisory issues cleared | Class |
|---:|---|---:|---:|---|
| 1 | Formation identity pack: emit `reaction` + `formation_elements` (+ `per` + `standard_pressure_Pa` where compilation declares them) for `delta_fG` / `delta_fH` / `log10_Kf` on species-rail + JANAF + CEA | ~30,000 | ~100,000+ | ENGINEERING |
| 2 | `H_minus_H298` `subtype` (reference T) on USGS/JANAF tables that already print the reference | ~3,000 | ~3,180 | ENGINEERING |
| 3 | Point-`temperature_K` mapper where the source already has a single T (leave range-only as unknown/range) | subset of 17,176 | subset of 17,176 | ENGINEERING / PERMANENT |
| 4 | Activity / partial-pressure reference_state + total_pressure defaults where source states them | ~500–800 | ~2,000 | ENGINEERING / DATA |
| 5 | Melt `composition` / `fO2_Pa` via extract lanes (Ferry Z E*) | ~800 | ~1,500 | DATA |
| — | Do **not** treat advisory clearance as `engine_point` READY | 0 readiness | — | readiness is A3 |

Clearing priority 1 alone removes the majority of the 134k advisory pile and is the only bulk lever that moves scoring identity comparability for thermochemistry rails.

## Ranked recommendations

1. **Close formation-identity in compilation generators first (species-rail, JANAF, CEA).** Populate structured `reaction` + `formation_elements`, and map `per` / `standard_pressure_Pa` from the compilation’s declared standard. This is the bulk ENGINEERING fix (~89% of incomplete obs live in three compilation sources).
2. **Fill `H_minus_H298` subtype from printed reference temperatures** on USGS/JANAF lifts; stop leaving subtype unknown when the table banner states it.
3. **Keep archival unknowns for true absences** (range-only T, unstated fO2, unstated melt composition). Do not invent midpoints or buffer fO2; those stay advisory/PERMANENT until extract DATA lands.
4. **Do not prioritize advisories for `engine_point` readiness.** A3 composition/oxygen/pressure/bench gaps are the consumer blockers; identity_incomplete is a scoring/equality completeness signal.
5. **Re-run migrate validation census after generator fixes** and expect advisory count to collapse far faster than hard_issues (hard_issues remain the separate A1 derived_from/derivation queue).

## Method and scope

- Tree: `/workspace/repos/wt/slot-05` @ `origin/review/r6-r8-fix` (`1f8df6cbe`).
- Inputs: `data/literature/observations-v2` + works/experiments (`load_migrated_store`), `data/battery/migration-report.md` advisory census cross-check (134,518).
- Readiness: cross-read against A3 / `consumer_inputs.REQUIREMENTS` — engine_point does not list observation identity-profile axes.
- Analysis-only; worktree product state unchanged.
