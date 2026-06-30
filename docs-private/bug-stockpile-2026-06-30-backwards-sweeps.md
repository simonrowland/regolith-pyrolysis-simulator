# Bug stockpile — 2026-06-30 backwards class-hunt sweeps

Rebuilt via grok-code + codex backwards sweeps (find-fast / fix-slow). Raw catalogs (full P0-P3 lists):
/private/tmp/goal-flight-501/dispatch/sweep-{f0-authority,cache-key,extrapolation,empty-filter}.tail.
Triage doctrine: FIX-SOON (important, schedule deliberately — don't let them distract the critical path) vs
EXTRAP-SIBLINGS (fix-slow chunk) vs STOCKPILE (by-design / already-cataloged — do NOT re-open as new bugs).

## FIX-SOON (important; verify-then-fix; both touch condensation.py so SEQUENCE after the α_s gate clears it)
1. **CACHE-STICKING-DIGEST [P0 grind-correctness; bundle w/ CORPUS-BUMP; needs-confirm]** — data/literature/vacuum_pyrolysis_sticking.yaml
   drives coating margins/feasibility but appears OMITTED from the EvalSpec data-digest (condensation.py:433 + evaluate.py:1529) +
   ResultStore key → a sticking-YAML change (e.g. the α_s gate just made) would NOT invalidate cached optimizer/coating evals → silent
   stale coating feasibility. Same class as CACHE-FIX (added materials.yaml + species_catalog.yaml; this one was missed). FIX: add the
   sticking YAML to REQUIRED_DATA_DIGEST_KEYS / EvalSpec data_digests. INTERIM: the mandatory pre-regrind CORPUS-BUMP invalidates everything,
   so no live corruption before the regrind — but close the root gap so future sticking edits auto-invalidate.
2. **F0-MATERIALS-CERT-GATE [P1 F0-authority, BUG-096 sibling; needs-confirm]** — materials.yaml `alpha_s_by_species` per-stage override
   sets the AUTHORITATIVE wall-deposit α WITHOUT a certification/provenance gate (condensation.py:2734-2746). Exact class of BUG-096 (the
   per-stage-alpha fail-open that was reverted+ticketed → [[golden-neutral-not-authority-safe]]). FIX: gate the override behind the
   certification check (golden-neutral if no uncertified override is configured).
3. **ALKALI-BINDING-SURFACE-T [fidelity; golden-affecting if deposit account moves; instrument-before-gate]** — C4b Na/K binding diagnostic is
   evaluated at the wall-deposit account T, which for the default run is the 1500C hot pipe (1773K) where Na is VAPOR and cannot form a condensed
   Na-silicate film; the saturation_ratio there is physically not-applicable. Pre-existing wall-deposit-account-T attribution question exposed by
   C4b-FOLLOWUPS-A. Investigate whether alkali binding should be attributed to / evaluated at the cold condensation surface (Stage 4 / cold ducts)
   rather than the hot pipe. Defer to a dedicated alkali-surface-fidelity chunk.

## EXTRAP-SIBLINGS [decomposed 2026-06-30 via codex recon; F0=NONE — no extrapolated flag consumed authoritatively]
Same class as the SiO cold-wall gate but imprecise-not-inverting (0 physics-inverting; the inverting one was the already-landed α_s gate).
Recon (scratchpad/extrap-siblings-recon brief) confirmed clean instrument-before-gate split, all golden-neutral for committed fixtures:
- **EXTRAP-SIBLINGS-A LOWs (from b1a7c15 2-lens review, non-blocking; codex found NONE, grok LOW):** condensation.py:1063-1073 Arrhenius
  LOW-side has no warning (the low-side cold-wall is the Pound unity override — out-of-scope by brief, but a warning would be tidy);
  condensation.py:3018 `_coerce_alpha_s` bare fallback; test gap — Mg/Ca/Na/K scalar boundary cases not individually asserted (Fe covers the
  mechanism, same code path). All fold into EXTRAP-SIBLINGS-B or a small test-fill chunk.
- **EXTRAP-SIBLINGS-A (golden-NEUTRAL honesty; ✅ PUSHED b1a7c15):** 3 condensation.py sites — honest `extrapolated` flag + warning,
  SAME applied value: (1) scalar α_s (`_alpha_s_spec_from_entry` drops temperature_range_K; `_alpha_s_evaluation`/`_coerce_alpha_s` hardcode
  extrapolated=False) Fe/Mg/Ca/Na/K; (2) wall HKL Antoine P_sat on the APPLIED path `_wall_deposition_driving_pressure_pa` ~3276 silent;
  (3) SiO Arrhenius high-side >1800K warning surfacing incomplete (NOT the low-side Pound unity gate). brief: extrap-siblings-A-build-brief.txt.
- **EXTRAP-SIBLINGS-B (golden-AFFECTING value-policy; DEFERRED, literature-gated):** clamp/hold-endpoint/fail-loud at each site —
  Site1 scalar α_s endpoint-hold; Site2 Antoine below-range hold/fail (goes unphysical fast); Site3 Kress91 clamp/authority-demotion;
  Site4 SiO high-side hold at α_s(1800K) or fail. Per-site physical rec in recon tail. Each needs source-bound review; do NOT bundle with A.
- **Site-3 Kress91/FeO a_FeO validity-envelope (DEFERRED → redox track):** equilibrium.py:395 / fe_redox.py — a_FeO value IS authoritative
  (`consumed_by_behavior:True`) but NO Kress91/Holzheid'97/Ban-ya'93 valid T/composition envelope is encoded. Honesty flag needs the grounded
  envelope FIRST ([[citation-convention-inline-then-factforest]]). NOT a flag-consumption F0 today (no flag exists); instrument-first-then-decide.

## STOCKPILE (by-design or already-cataloged — NOT new bugs)
- **F0 sweep (P0=0, P1=18):** mostly KNOWN/by-design — eta_CE uncertified = MRE builtin-authoritative by design (BUG-016 / CE-curve track);
  extrapolated Antoine/Ellingham authoritative WITH warnings = warn-not-fail-closed per owner directive [[project-nonoxide-warn-not-failclosed]];
  wt% γ=1 oxide-activity proxy = TEMP-OXIDE-GAMMA (cataloged); melt_surface_renewal owner-ratified = O4/R_melt (task #75); capture-budget
  regularizer floor = CAP-BUDGET (#72); unknown_species_default α=0.80 = yaml UNCERTIFIED status-bearing; allow_fallback_vapor = opt-in,
  default-False (safe). REVIEW-worthy flags (look, don't auto-fix): evaporation_flux.py:773-825 unmeasured-α=1.0 fallback DEFAULT-ON drives
  flux — confirm intended; equilibrium.py:74-79 OVERHEAD diagnostic pO2 → vapor-pressure dispatch (likely RDX-FO2-GROUND territory).
- **CACHE sweep (P1 holes):** the _SOURCE_MODULE_PATTERNS gaps (fe_redox/condensation/campaigns/electrolysis absent) + engine_version-not-keyed
  are DELIBERATE VERSION-based cache identity (commit 8d09d4f "corpus_version = sole cache lever") [[cache-identity-version-not-crypto]] —
  covered by the CORPUS-BUMP discipline, NOT new bugs.

## FAIL-LOUD-HARDENING-2 (empty-filter sweep: P0=0, P1=13, P2=11, P3=6) [fix-slow batch; condensation sites AFTER α_s gate]
Sibling batch of the fail-loud-hardening program (checklist G3 / H1-H8) — invalid/missing input silently DEFAULTS instead of
failing loud. Mostly config-validation hygiene; a few touch physics. Higher-priority subset (verify-then-fix):
- **evaporation.py:145** — empty `vapor_pressures_Pa` → empty flux (SILENT-ZERO evaporation / suppressed ledger movement).
  NEEDS-CONFIRM: is empty ever legitimate (all species sub-threshold) vs always a backend miss? If always-miss → fail-loud (mandate
  hates silent-wrong-physics).
- **carrier-gas → N2 cluster** (condensation.py:1054, 1400; core.py:1661) — invalid/blank/unrecognized carrier gas silently → N2,
  changing transport/capture physics (pN2 is a north-star lever). Fail-loud or warn on invalid gas.
- **campaigns.py:799** — empty/missing C2A_STAGED.stages → default (1750,150) recipe (invalid schedule silently runs default).
- **campaigns.py:1406** — missing na_shuttle_stage falls back to k_shuttle_stage (Na inherits K settings) — relates to task #46
  (C3_NA/C3_K config bleed).
Config-hygiene subset (lower): extraction.py:560 + campaigns.py:1249 (MRE target/voltage-cap widen — MRE default-off; verify vs
already-fixed BUG-140); condensation.py:2478/2528/3669 (condenser temp/residence defaults); worker_runtime.py:50 (blank backend→stub
BEFORE strict resolver — verify the resolver catches it; C3 contract). Full list: sweep-empty-filter.tail.
NOTE: condensation.py sites (1054/1400/2478/2528/3669) SEQUENCE after the α_s gate (shared file); a consolidated condensation.py
fix-worker can later bundle F0-MATERIALS-CERT-GATE + EXTRAP-SIBLINGS + these fail-loud sites into one reviewed chunk.

## RENAME-FOLD-LOWS (from 58da431 2-lens review, 2026-06-30) — residual fold-completeness hygiene, NON-blocking
The F0/M sites (cal_threshold_calibration.py:684/709, populate_reduced_real_cache.py:854) are FIXED in the foldgap-fix commit (canonical_backend_name
`type=` normalizer at the `--backend` args). Residual LOWs (display/diagnostic only — NOT fail-open; both reviewers agree non-blocking):
- **web/routes.py:589-604** — `_optimizer_backend_payload` `stubish` checks only `{'stub','diagnostic_stub'}`; an unfolded `internal-analytical` raw
  eval_spec dict would miss `stubish`, BUT `canonicalize_fidelity_emission(backend_name='internal-analytical')` FAIL-LOUDs via
  UnknownFidelityVocabularyTokenError (fidelity_vocabulary.py:336-340, tested test_web_optimizer.py:752-758). Fail-loud, not fail-open. Fold for tidiness.
- **fidelity.py:870-871** — `_high_arm_authority_reason` reads raw profile string; diagnostic copy only, evaluate/certification path uses folded EvalSpec.
- Operator CLIs beyond the two fixed (none other found on committed runtime surface).
Class note: the rename's "fold at every name-keyed boundary" contract held on all HOT/runtime paths; the only gaps were operator-script gates (fixed)
+ these two display/diagnostic reads. Forward lens: any new `== "stub"` / raw `backend_name` string compare must fold via canonical_backend_name first.

## Sweep status: 4/4 backwards class-hunts complete (F0-authority, cache-key, extrapolation, empty-filter). Stockpile replenished.
Fix priority (all fix-slow, critical-path stays primary): (1) CACHE-STICKING-DIGEST P0 + CORPUS-BUMP [grind, pre-regrind];
(2) F0-MATERIALS-CERT-GATE P1 [BUG-096 sibling]; (3) condensation EXTRAP-SIBLINGS + FAIL-LOUD batch [after α_s gate]; (4) RENAME-FOLD-LOWS (above);
(5) the rest stockpiled.
