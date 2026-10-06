# REPORT: t-1123 proposal (phase 1). Corrected two-phase rule for Stolyarova 1991, before implementation

- from: regolith-empirical
- to: regolith-main (copy to regolith-physics: this departs from the physics E15 sub-ruling "x(SiO2) ≤ 0.40 is two-phase")
- date: 2026-10-05 ~22:05 ET (box clock)
- answers: ROR-t1123 (FIX-FIRST on `review/t1123-stolyarova-1991-two-phase` @ `5b420add8b189f96dfe775840b5804bd1c390cbc`)
- base used: green `work-v064-green` = `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. The two tip commits cherry-pick cleanly onto it (trial commits `35c9cca05` and `f094a5ba5`, local only, not pushed).
- **state: PROPOSAL ONLY. Nothing is committed or pushed. The branch on origin is still `5b420add8`.** I stopped after phase 1 because the corrected rule has open design choices that main, and for one of them physics, has to rule on (§6).

## 0. Summary

- **The ROR's findings reproduce exactly.** I ran a fresh `migrate(write=False)` and `score_store` on green and on the tip rebased onto green, with openimcc at the pyproject pin `30c51c85`.
  - The tip turns 28 numeric vapour residuals into `bulk_not_liquid_composition`: 18 openimcc and 10 internal-analytical, on 18 rows.
  - 16 of those 28 are on rows at x = 0.38–0.40. The source's own data put those rows on the liquid trend.
  - The tip leaves the 1993 K Table Ia rows at x = 0.33 and 0.25 unflagged, although they are the clearest saturation signal in the paper.
  - On the tip, identity_incomplete issues fall from 434 to 356. The drop is entirely the side effect of promoting composition: composition-unknown falls by 48 and fO2-unknown by 30.
- **Our earlier REPORT said no row scored before or after. That was wrong.** Its harness built `ScoreContext` without `benches`, so the unverified-apparatus path never ran and every vapour row refused `identity_incomplete`. With `benches` passed (as `load_score_context` does), green 1991 has 76 openimcc and 40 internal-analytical numeric residuals.
- **The proposed rule, in short:**
  - A printed row is a saturation (non-single-liquid) observation only if two things hold: it lies on the far side of a liquidus that the source itself states, or that a held diagram gives; and the printed intensive quantities at that temperature form a phase-rule plateau within 2σ of the printed uncertainty.
  - The classification goes on the condensed reservoir, never into `phase_as_printed` and never into the gas species phase. It never promotes composition.
  - For Stolyarova 1991, that classes only x = 0.33 and 0.25 as saturation rows (28 rows). x = 0.36–0.40 stays liquid with a "liquidus position contested" flag (44 rows). Every vapour residual on those 44 rows stays numeric.

## 1. What the current commit destroys and mislabels (measured, green 61ec839da vs tip-on-green)

Method:
- Targeted dry run on one source per run. A temp tree holds only that extract plus its INDEX row.
- `simulator.battery.migrate.migrate(root, write=False)`, then `score_store(ScoreContext(works, experiments, observations, origins, extract_review, benches=result.benches), engines=SCORE_ENGINE_SET, include_diagnostics=True)`.
- openimcc: pinned commit `30c51c85` in an isolated venv on the box. Engine availability: internal-analytical yes, openimcc yes; vaporock, alphamelts, thermoengine and magemin unavailable on the VPS.
- Script: `/workspace/scratch/t1123-r2/dryrun.py`; outputs in `/workspace/scratch/t1123-r2/out/`.

**Both sides migrate 145 observations (780 residuals).** This confirms the ROR's 145; our earlier 144 was on an older green.

| residual outcome (1991, 780 slots) | green | tip-on-green |
|---|---|---|
| openimcc numeric | 76 | 58 (−18) |
| internal-analytical numeric | 40 | 30 (−10) |
| `bulk_not_liquid_composition` (vaporock / openimcc / internal-analytical) | 0 / 0 / 0 | 48 / 48 / 48 |
| `effusion_regime_unverified`, per engine | 54 | 54 for alphaMELTS, MAGEMin and ThermoEngine; 24 for the three single-liquid engines |
| migration identity_incomplete issues | 434 | 356 |
| … composition unknown / fO2_Pa unknown / total_pressure unknown | 138 / 138 / 84 | 90 / 108 / 84 |

**Transitions on the 48 stamped rows (per engine):**
- **Activity rows (30).** `effusion_regime_unverified` becomes `bulk_not_liquid_composition` on the three single-liquid engines. alphaMELTS is unchanged. No numeric result is lost, because 1991 activities are effusion-gated on green.
- **Vapour rows (18).** 28 numeric residuals become refusals: 18 openimcc and 10 internal-analytical. vaporock goes from `unsupported` to `bulk_not_liquid_composition`.
- Species phase goes from `g` to `unknown: 'Ca2SiO4 + melt' is bulk_composition_in_two_phase_region` on all 18 gas rows.
- `identity.composition` goes from unknown to the row's printed point composition on all 48 rows. That promotion is what removes 48 composition and 30 fO2 advisories (§4).

Full per-row before/after table (48 rows × 4 engines): `/workspace/scratch/t1123-r2/out/table-48.md`. The ROR's spot values reproduce to the printed digits:
- SiO complete evaporation, x = 0.25: internal-analytical +0.833, openimcc −3.298, both refused on the tip;
- x = 0.40: +0.213 / −1.519, refused;
- x = 0.41: +0.063 / −1.545, unchanged;
- O(g), x = 0.25: openimcc +1.500, refused;
- SiO2(g), x = 0.25: openimcc −3.156, refused.

## 2. What the source supports (printed values from the extract, which was checked against p. 3711)

**The source text (p. 3711):**
- "The congruent character of vaporization processes in the range from 0.01 to 0.37 ± 0.02 mole fractions of SiO2 was shown … we concluded that the liquidus point on the phase diagram may be corrected compared with the earlier mentioned 0.41 mole fraction of SiO2 (30)."
- Fig. 1 (p. 3712) shows evaporation isotherms of CaSiO3, 2CaO·SiO2 and 3CaO·SiO2 samples. The CaSiO3 run passes 0.50 → 0.42 → 0.39 → 0.37 → 0.33.
- The paper prints no per-row phase.

**The phase-rule criterion.** For a binary at fixed T in a Knudsen cell, two condensed phases plus vapour give F = C − P + 2 − 1 = 2 − 3 + 2 − 1 = 0. The −1 is for fixed T; the total pressure is the vapour's own, not imposed. So across a two-condensed-phase field **every** printed intensive quantity (activities and all partial pressures) must be one constant.

**Test.** A set of compositions counts as a plateau in one series iff max over pairs of |v_i − v_j| / √(σ_i² + σ_j²) ≤ 2:
- σ is the printed ±;
- for Eq. [5]–[8] (no ± printed), σ is the paper's own "relative uncertainties … not more than 15 %" (p. 3711);
- P_SiO2 and P_O print no ± and are untestable.

Script: `setplateau.py`.

| series (T as captioned) | σ basis | {0.40, 0.39, 0.38} max z | {0.36, 0.33, 0.25} | {0.33, 0.25} | {0.41, 0.40} step | {0.38, 0.33} step |
|---|---|---|---|---|---|---|
| P_Ca, method 1 (1993 K) | printed ± | **3.89 NOT** | 0.09 | 0.04 | 7.07 NOT | 2.60 NOT |
| P_Ca, method 2 (1993 K) | printed ± | **2.73 NOT** | 0.31 | 0.24 | 2.60 NOT | 1.19 |
| P_CaO, method 1 (1993 K) | printed ± | 1.68 | **2.67 NOT** | 0.42 | 5.05 NOT | 4.09 NOT |
| P_CaO, method 2 (1993 K) | printed ± | **2.03 NOT** | **2.43 NOT** | 0.72 | 1.74 | 3.18 NOT |
| P_SiO, method 1 (1933 K) | printed ± | **2.33 NOT** | 0.57 | 0.57 | 2.95 NOT | 8.49 NOT |
| P_SiO, method 2 (1933 K) | printed ± | 1.27 | 1.88 | 1.88 | 2.02 NOT | 3.64 NOT |
| a(CaO) Eq. [2] (1933 K) | printed ± | 1.94 | 1.11 | 1.11 | 4.07 NOT | 6.60 NOT |
| a(CaO) Eq. [5] | 15 % | **2.51 NOT** | 0.24 | 0.24 | 0.66 | 1.61 |
| a(CaO) Eq. [7] | 15 % | 0.94 | 1.33 | 1.33 | 1.43 | 1.43 |
| a(SiO2) Eq. [3] | printed ± | 1.52 | 0.45 | 0.45 | 1.41 | 3.71 NOT |
| a(SiO2) Eq. [6] | 15 % | 1.85 | 0.85 | 0.85 | 0.00 | 4.47 NOT |
| a(SiO2) Eq. [8] | 15 % | 1.33 | 2.98 NOT | 2.98 NOT | 1.85 | 2.75 NOT |
| P_SiO2, P_O | none printed | untestable | untestable | untestable | untestable | untestable |

Reading:
- **{0.33, 0.25} is a plateau in 11 of 12 testable series.** The exception is Eq. [8], an integral evaluated from the pure component, tested at the assumed 15 %.
- **{0.38–0.40} is not a plateau.** It fails in 5 of 12 series, and P_Ca, P_CaO and P_SiO trend monotonically. By the phase rule, these rows are not one two-condensed-phase field. They also lie on the liquid side of the authors' corrected liquidus (0.37 ± 0.02).
- **x = 0.36** (Table Ia only) joins the 0.33/0.25 plateau in P_Ca, but not in P_CaO (z = 2.67 and 2.43). It is contested.
- This agrees with the ROR's P0 (c). The five stamped compositions span a(CaO) 0.52 → 1.00, which is not one field. The 0.96-vs-1.00 pair itself agrees within 1.1σ, but it is the lime-side plateau. It is not "Ca2SiO4 + melt".

**Corroboration from green's own residuals.** These are engine output, so this is corroboration and not the criterion. Single-liquid residuals (dex) against x(SiO2), green:

| series | engine | 0.50 | 0.49 | 0.47 | 0.44 | 0.41 | 0.40 | 0.39 | 0.38 | 0.36 | 0.33 | 0.25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P_Ca, method 1 (1993 K) | internal-analytical | −2.50 | −2.60 | −2.56 | −2.52 | −2.47 | −2.55 | −2.56 | −2.45 | −2.32 | **−1.96** | **−1.52** |
| P_Ca, method 1 (1993 K) | openimcc | −1.72 | −1.82 | −1.78 | −1.74 | −1.69 | −1.77 | −1.78 | −1.67 | −1.54 | **−1.18** | **−0.74** |
| P_CaO, method 1 (1993 K) | openimcc | −3.52 | −3.61 | −3.56 | −3.54 | −3.32 | −3.48 | −3.39 | −3.37 | −3.25 | **−2.99** | **−2.50** |
| P_SiO, method 1 (1933 K) | openimcc | −1.29 | −1.03 |  | −1.52 | −1.55 | −1.52 | −1.61 | −1.69 |  | **−1.84** | **−3.30** |
| P_SiO, method 2 (1933 K) | openimcc | −1.33 | −1.07 |  | −1.50 | −1.59 | −1.56 | −1.65 | −1.73 |  | **−2.26** | **−2.77** |
| SiO2(g) (1933 K) | openimcc | −0.33 |  |  | −0.76 |  | −1.14 |  |  |  | **−2.34** | **−3.16** |
| O(g) (1933 K) | openimcc | +1.51 | +1.50 |  | +1.43 | +1.37 | +1.52 | +1.33 | +1.44 |  | +1.43 | +1.50 |

- The residual is flat from 0.50 to 0.38. P_Ca has a shape RMS of 0.046–0.048 dex on x ≥ 0.38 against 0.318 dex on all rows (prototype in §5).
- It breaks only at x ≤ 0.36, where the measured pressures plateau and a hypothetical single liquid at bulk composition keeps moving.
- So the 0.38–0.40 vapour residuals the tip removes are liquid-consistent. The x = 0.33/0.25 residuals are the bulk-composition artefact that E16's scoring rule forbids.

**Pre-check (E15's a_printed/a_abs constancy), done internally from printed tables (`precheck.py`):**

| check | n | log10 ratio, mean ± sd | reading |
|---|---|---|---|
| a(CaO) Eq. [2] / [P_CaO(x) / P_CaO(0.25)], Table Ia method 1 | 9 | −0.006 ± 0.009 (worst −0.029 at x = 0.50, which is rounding of "0.07") | The printed a(CaO) **is** the Table Ia P_CaO normalised to the x = 0.25 row. The level is anchored to that row. The pure-oxide run is not independent, so a(CaO)(0.25) = 1.00 carries no information. |
| same, Table Ia method 2 | 9 | +0.012 ± 0.042 | consistent |
| a(SiO2) Eq. [3] / (P_SiO, method 1 · P_O) | 9 | +12.356 ± 0.030 | One constant, so the table is self-consistent. The implied reference product P°_SiO·P°_O is 4.41 × 10⁻¹³ atm² at 1933 K. Comparing it with the binding's K for SiO2(cr) = SiO(g) + O(g) gives the absolute pressure-scale offset. That comparison is engine/scorer-side and was **not** done here. |
| same, SiO method 2 | 9 | +12.343 ± 0.221 (the 0.33/0.25 cells carry ±1–2 on 0.8–5.0) | consistent within noise |

Side finding: Table IIa (captioned 1933 K) reproduces Table Ia (captioned **1993 K**) to 0.01 dex. Either one caption is a misprint, or the activities were formed from 1993 K currents. The abstract says 1933 K throughout. This bears on which T the 1993 K rows are scored at. I did not change it, because that would invent data.

## 3. The proposed corrected rule

**R1 (channel).** `phase_as_printed` carries only a phase string the source prints. A phase-field classification that the source does not print goes into a separate, typed point field, for example `phase_field_class`:
- `class: outside_single_liquid_field`;
- `basis`: criterion, locator and test values.

Its consumer:
- (a) never writes `identity.species.phase`, so a gas stays `g`;
- (b) never promotes point composition into `identity.composition`;
- (c) attaches the existing `two_phase_bulk_composition_not_liquid_composition` notice band, or a renamed `bulk_outside_single_liquid_field` band (§6 Q2).

**R2 (what qualifies; flatness criterion).** A printed row at temperature T is `outside_single_liquid_field` iff (i) and (ii) both hold.
- (i) It lies beyond a liquidus at T that is stated by the source or taken from a held diagram. For 1991: the authors' 0.37 ± 0.02 (p. 3711), and a row qualifies only on the lime side of that position.
- (ii) It belongs to a set of at least 2 compositions at the same T on which every printed series with a usable σ is a plateau: pairwise |Δv| ≤ 2√(σ_i² + σ_j²), with the printed ± or the source's stated relative uncertainty. At most one untestable or integral-derived series may fail, and it must be named.
- Alternatively, the row carries a printed unit activity of a component (saturation by the authors' own table, as with Kume's a(SiO2)(s) = 1 rows).
- A row between two liquidus positions (the source's own and an older cited or held one) is `liquid, liquidus_position_contested`. It is flagged and scored as liquid; it is not classified as two-phase.

**R3 (residuals preserved).**
- Liquid and contested rows keep species phase, composition status and every numeric residual exactly as on green.
- The contested flag puts them in b-691's all-numeric line and keeps them out of the certified line.
- `outside_single_liquid_field` rows follow E16's scoring rule ("scored against the engine's saturated liquid, or refused with a typed reason, never scored at bulk composition"):
  - single-liquid engines refuse `bulk_not_liquid_composition`, and the refusal detail keeps the bulk-composition number as a non-scored diagnostic, so nothing disappears silently;
  - bulk-resolving engines are unaffected.
  - This applies to activity and vapour rows alike. A partial pressure over a saturated assemblage is the saturated liquid's, not the bulk hypothetical liquid's. The alternative is in §6 Q3.

**R4 (unknown fO2 stays visible).**
- A phase classification never changes `identity.composition`, so the composition, fO2 and total-pressure advisories stay at green's 138 / 138 / 84 for this source.
- If composition is ever promoted on these rows for its own reasons, the fixed-valence exemption in `identity.py:814-815` must record fO2 as "not compared (fixed-valence composition), not printed". It must not drop the axis from the advisory count.

**R5 (E15 proper; scorer, separate from this extract branch).**
- Per (source, table series, T, engine), over numeric single-liquid residuals of liquid rows: report the level (mean r) on its own line with the source's level-basis flag, and the shape (r − mean). Contested rows are reported separately.
- Shape is reported as RMS, OLS slope against x and n.
- Level-basis flags:
  - 1991: "level anchored to the x(SiO2) = 0.25 row" (pre-check above). This replaces the generic "reference run not printed".
  - 1996: "level tied to the absolute p(Ca) and p(O) scales" (E16 revision of E15).
  - 1995: no numeric activity table (the activities are Fig. 1, `figure_only`). Shape applies to the Table 2 partial pressures only.
- Pre-check: a_printed/a_abs with the binding's K° per table, before shape is trusted.

## 4. Effect of the proposed rule on 1991 (from the green dry run; numbers, not estimates)

| class (rows; 1933 K activity / 1933 K vapour / 1993 K vapour) | x(SiO2) | rows | numeric single-liquid residuals on green | under the proposal | under the current tip |
|---|---|---|---|---|---|
| outside_single_liquid_field | 0.33, 0.25 | 12 / 8 / 8 = **28** | 24 (12 at 1933 K, 12 at 1993 K) | 24 become typed refusals with the diagnostic kept; 12 activity rows refuse single-liquid engines | 12 lost at 1933 K; the 12 at 1993 K stay numeric (missed) |
| liquid, liquidus_position_contested | 0.40, 0.39, 0.38 (+0.36 in Table Ia) | 18 / 10 / 16 = **44** | 40 | **40 kept, numeric**, flagged | 16 lost (1933 K); the 1993 K rows are untouched |
| liquid | ≥ 0.41 | 24 / 14 / 20 = 58 | 52 | unchanged | unchanged |
| no printed composition (figure-only / derived) | n/a | 15 | 0 | unchanged | unchanged |

- **Identity advisories:** unchanged from green under the proposal (434; composition 138, fO2 138, total pressure 84). The tip shows 356, 90 and 108.
- **Store delta at landing under the proposal (not committed):**
  - 28 point ids rehash, because the payload gains the `phase_field_class` key;
  - plus 44 more if the contested flag is stored on the extract rather than derived by the reader;
  - for the 12 class-S activity rows, the refusal token on single-liquid engines changes from `effusion_regime_unverified` to `bulk_not_liquid_composition`;
  - the 24 class-S vapour residuals change from numeric to refused-with-diagnostic (or stay numeric and flagged, if Q3 goes the other way);
  - nothing else changes.
- **Pin:** `tests/battery/test_score.py:6923-6931` reads the derived store. The landing regen would add `bulk_not_liquid_composition` to those 12 activity rows, so the pin must be updated in the same landing.

## 5. 1995 and 1996 (E15 coverage, green; nothing in the tip touches them)

- **1995 (CaO–Al2O3, 56 observations).**
  - Numeric residuals: openimcc 50, internal-analytical 24, all on Table 2 partial pressures. Activities exist only as Fig. 1 `figure_only` and are not scored.
  - The extract note (pp. 686–687) says the paper does not describe x(CaO) = 0.75 or 0.86 as two-phase or lime-saturated.
  - p(Ca) at x(CaO) = 1.00 / 0.86 / 0.75 is within 12 % (stored 0.0792 / 0.0861 / 0.0892 Pa). That would be a lime plateau, but the paper prints no ±, so R2(ii) is **untestable**. No row is classified.
  - Flagged for physics as a candidate only.
- **1996 (CaO–Al2O3–SiO2, 312 observations).**
  - Numeric residuals: openimcc 235, internal-analytical 75.
  - The binary plateau test does not apply to a ternary: two condensed phases plus vapour leave F = 1 at fixed T, so a plateau exists only along a tie-line. A held ternary diagram, or the E16 saturation-bounds route, is needed.
  - One row prints a(CaO) = 1.000, at x = (CaO 0.769, Al2O3 0.131, SiO2 0.10). That is saturation by the authors' own table and qualifies under R2's unit-activity clause. It is already refused `effusion_regime_unverified` on every engine.
- **Prototype per-table mean-removed shape statistic** on green's numeric residuals. This is report evidence only, not scorer code (`shape.py`; full 61-row table in `out/shape-prototype.md`). Selected rows:

| src | series | engine | rows | n | level (dex) | shape RMS (dex) | slope (dex per unit x) |
|---|---|---|---|---|---|---|---|
| 1991 | P_Ca, method 1 (1993 K) | openimcc | all | 11 | −1.582 | 0.318 | −3.87 |
| 1991 | P_Ca, method 1 (1993 K) | openimcc | x ≥ 0.38 | 8 | −1.744 | **0.048** | −0.44 |
| 1991 | P_SiO, method 1 (1933 K) | openimcc | all | 9 | −1.704 | 0.604 | +7.51 |
| 1991 | P_SiO, method 1 (1933 K) | openimcc | x ≥ 0.38 | 7 | −1.457 | **0.208** | +4.15 |
| 1995 | p(Ca), Table 2 | openimcc | all | 8 | −0.282 | 0.383 | +1.04 (per unit x(CaO)) |
| 1996 | a(CaO), Table 3 col. 1 | openimcc | all | 22 | −0.923 | 0.329 | −1.75 |
| 1996 | a(SiO2), Table 3 col. 5 | openimcc | all | 22 | −0.037 | 0.296 | +0.40 |
| 1996 | p(Ca), Table 2 | openimcc | all | 22 | −2.207 | 0.708 | −4.94 |

On green, 1991 has no numeric activity residual: all 54 are `effusion_regime_unverified`. So an E15 activity shape score for 1991 has n = 0 until that gate clears, whatever the phase class. Today the shape score on 1991 runs on the vapour tables only.

## 6. Why I stopped (open design choices; main's ruling needed)

1. **Q1 (physics).** Liquidus position for 1991. The physics E15 sub-ruling says x ≤ 0.40 is two-phase and only 0.50–0.41 is scoreable. The source's own text (0.37 ± 0.02) and its data (§2) say 0.38–0.40 behave as liquid. **Proposed:** class S = {0.33, 0.25}, and contested-liquid = {0.36–0.40}. This overrides a physics ruling, so physics or main must confirm.
2. **Q2 (schema).** Is a new non-printed point field (`phase_field_class` + `basis`) acceptable, or should the reader derive the class from a source-level liquidus record? Should the notice band keep the "two_phase" name or be renamed `bulk_outside_single_liquid_field`? (The class-S rows are a lime-side solid-bearing plateau, not "X + melt".) Either way this needs migrate.py code in the reader, so the branch is no longer extract-only.
3. **Q3.** Class-S **vapour** rows: refuse on single-liquid engines with the diagnostic kept (my recommendation; E16's rule), or keep them numeric with a flag (closer to the ROR's "preserve")? The difference is 24 residuals.
4. **Q4 (lane).** Is E15's shape/level statistic in `score.py` this branch's job? Task-decomposition lists t-1123 as lane main ("design with b-691's two-line headline"); BACKLOG item 27 was extract-only. **Proposed split:** t-1123a, the extract/reader fix per R1–R4 on this branch; t-1123b, the scorer statistic per R5 (seat to be named by main).
5. **Q5.** The Table Ia caption (1993 K vs 1933 K; §2 side finding): leave it as printed with a note (my default), or ask physics to rule that it is a misprint?

**What I will implement once ruled:**
- revert both tip commits (that alone restores green's 28 numeric residuals and the 434 advisories);
- the R1 reader change plus extract entries on the 28 class-S points (and the 44 contested points if Q2 says so);
- pins first, with a mutation proof for each guard:
  - gas phase stays `g`;
  - no composition promotion;
  - the class-S refusal fires;
  - contested rows stay numeric;
- no store regen; the store delta as in §4.

## D-062 checklist (proposal: no code changed)

1. Second copy of added or edited logic? **No; no code was changed.** For the planned change, `rg "_printed_point_phase_kind|two_phase_bulk_composition"` shows one owner today: migrate.py writes the marker and score.py reads it. R1 keeps one owner.
2. Rule, threshold or physics in a presentation layer? **No.** The plateau test above is report-side evidence. In the implementation the classification would be extract data with its basis, read by the migrator.
3. New import crossing a layer or creating a cycle? **No; nothing was changed.**
4. Behaviour-preserving move without a pin? **No; there was no move.**
5. Relaxed guard, baseline entry, or a move into a relaxed module? **No.**

## Artefacts (box, `/workspace/scratch/t1123-r2/`)

- `dryrun.py`, `plateau.py`, `setplateau.py`, `precheck.py`, `shape.py`, `analyze.py`
- `out/{green-1991,tip-1991,green-1995,green-1996}.json`: per-row migrate and score
- `out/table-48.md`, `out/setplateau.md`, `out/shape-prototype.md`, `out/plateau-1991.json`
- `ex/{green,tip}-kems-053-stolyarova-1991.yaml`
- Source PDF used read-only: corpus `1991Stolyarova.pdf`, sha256 prefix `daa699f8ba93eb7b`.
- No test files, store files or other branches were touched. Tests run: none, since the proposal changes no code; the dry runs are the evidence. I removed my worktree `/workspace/repos/wt/t1123-r2` after writing this.
