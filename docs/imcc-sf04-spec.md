# IMCC-SF04 — independent melt-activity shadow engine (SPEC, r2.2 — CONVERGED + rung-2 erratum)

Status: r2.1 2026-08-10 — convergence closer verdict CONVERGED (imccspec-rev2-close.md; fold
verified genuine); binding errata E1–E5 + should-folds E6–E11 applied in this revision.
Chunks 1–2 CLEARED to fire. Prior: r2 2026-08-10 — kimi adversarial review folded (verdict REVISE: P0-1 rung-4 operationalization
+ 7 P1 + 5 P2; salvaged review: docs-private/reviews/2026-08-10-gatekey/imccspec-kimi-review.SALVAGED-TAIL.md).
r1 was the controller draft (t-615). Review-to-convergence required before any
implementation chunk fires. Evidence base: `T610-imcc-scout/scout.md` (codex, full-inventory
steer; GO-bounded verdict) — section refs below are into that file. Owner rulings in force:
shadow source (partial coverage fine); long-tail data beyond central databases WAITS FOR GROK.

## 1. Purpose and authority (what this is and is not)

`IMCC-SF04` is an **independent diagnostic shadow** for melt-component activities: a clean-room
implementation of the **disclosed** Ideal-Mixing-of-Complex-Components equilibrium problem as
published in Hastie–Bonnell (1985/86), Fegley–Cameron 1987, and Schaefer–Fegley 2004. Its value
is exactly its independence from MELTS: activities come from published complex-formation fits,
not from a calibration corpus — so it has no basalt-corpus cliff and provides a second opinion
where MELTS is farthest from home (dunite-class flag adjudication, H3 anchor expansion at
arbitrary compositions, Tier-C contamination screening).

**Authority boundary (permanent):**
- It reproduces the PUBLISHED model. It is NOT a MAGMA clone: numerical conventions
  (initialization, transforms, tolerances, branch/underflow policy) are undisclosed (§1.2 scout)
  and are chosen independently here; equivalence is defined by the acceptance ladder (§8), never
  by source parity. No claim of parity with post-2004 closed MAGMA (ferric/magnetite/Zn
  parameters never published — scout §1.4).
- Diagnostic only, never recipe authority, never a golden input. It never mutates the
  AtomLedger, never enters `commit_batch`, and its outputs are labeled per §7.
- Known model-form behaviour, documented not patched (r2, review-corrected): complexing strongly
  DEPRESSES the activities of heavily-complexed parents — SF04 reports measured FeO γ ≈ 2.2–4.7
  where IMCC underpredicts ~3×, a sign-known expected divergence: SIGN checked at §8 rung 2 (literature activities), OFFSET
  reproduction checked at §8 rung 4. NOTE the
  bound γ ≤ 1 is NOT a theorem of the definitions: γ_i = x_i*/x_i uses different denominators
  (46-species total vs analytical 8-oxide total), and complex formation shrinks total moles, so a
  weakly-complexed parent in a heavily-complexed melt can show γ > 1. Expected-divergence entries
  are therefore PER-SPECIES with sign checked at rung 2 — never assumed categorically.

## 2. Domain contract (hard, typed)

- Input: melt composition over EXACTLY the 8 parent oxides {SiO2, MgO, FeO, CaO, Al2O3, TiO2,
  Na2O, K2O}, mol or wt with declared basis, plus T_K. Temperature domain: declared per data-pack
  row provenance (fits carry demonstrated T domains). "The union" = the union over PER-ROW declared
  domains — a T inside the global envelope but below an individual consumed fit's domain still
  triggers that row's extrapolation handling (never silent); the per-row declaration basis
  (paper-demonstrated vs SF04-as-exercised) is recorded in the data pack. Outside a row's declared
  domain the DEFAULT is typed
  refusal (`imcc_T_outside_datapack_domain`); an `extrapolated` status result is produced ONLY
  under an explicit `allow_extrapolation=True` caller flag, and carries that status on every
  output row (V2 refusal-semantics conventions; fail-loud default per repo doctrine).
- **FeO-equivalent contract** (scout §1.4): all iron enters as FeO. Fe2O3 in input → typed
  refusal `imcc_ferric_input_unsupported` (caller converts under ITS redox model and owns that
  choice; we do not silently convert — SF04's own Fe2O3 preprocessing is undisclosed).
- Unknown/extra components (incl. P, S, halides, native metals, volatiles): typed refusal
  `imcc_component_outside_domain` naming the component — EXCEPT Tier-B/C species entering via
  their explicit screening flags (§5, §6).
- No silent renormalization: if the 8-oxide vector does not sum to the declared basis within
  tolerance (declared: |Σ − basis| ≤ 1e-6 relative), refuse (`imcc_composition_incomplete`)
  rather than renormalize.

## 3. Model definition (scout §1.2–1.3, authoritative)

Melt = ideal associated solution over **46 species = 8 unbound parents + 38 complexes**
(exact list + stoichiometry table: scout §1.3). Data-pack arithmetic: FC87 Table 3 carries 36
complexes; SF04 ADDS 2 (K2Si4O9, KCaAlSi2O7) and REVISES 2 (K2SiO3, K2Si2O5) — so the pack is
34 rows from FC87 + 4 rows printed in SF04 (K2SiO3 A=0.27 B=12735 K; K2Si2O5 0.35/14685;
K2Si4O9 −0.96/17572; KCaAlSi2O7 4.30/17037).

- All 46 species mix ideally on the total species mole-fraction basis.
- Parent activity: `a_i = x_i*` (mole fraction of UNBOUND parent among all 46).
- Reported coefficient: `γ_i = x_i* / x_i` with `x_i` the analytical (total) oxide fraction.
- Complex mass action: `x_j = K_j(T) · Π_i (x_i*)^ν_ij`, `log10 K_j = A_j + B_j/T`.
- Solve: 8 coupled nonlinear parent balances (equivalently constrained Gibbs minimization over
  nonnegative species). Derivation comments in the kernel per repo doctrine (premise → algebra →
  unit check → sanity case, e.g. a published SF04 binary).

## 4. Data pack (Chunk 1; the transcription IS the risk)

- Double-transcribe all 38 reactions + A/B fits from FC87 Table 3 + SF04 (two independent
  passes, diff, then formula/atom-balance validation of every stoichiometry row — fractional
  ν (0.5 Na2O etc.) and OCR column shifts are the known high-risk failure modes).
- Per-row provenance: source table, page, demonstrated T domain, and the provenance split
  (9 rows with independent JANAF liquid tables / 1 partial / 28 authority-by-publication — the
  9 become internal consistency checks, WITH the JANAF-table range caveat recorded per row,
  e.g. Na2SiO3/Na2Si2O5 liquid tables end at 2500 K).
- Pack is versioned data (`imcc_sf04_datapack_version`), review-gated like any physics table.

## 5. Tier B — dilute minor-oxide screens (Chunk 7; each behind a flag)

MnO, NiO, CoO, Cr2O3 enter one at a time as UNCOMPLEXED ideal components. Oxidation-state and
phase mapping is the CALLER's declared responsibility per element: Co enters only via an explicit
Co→CoO mapping (metal-vs-oxide split declared); Ni likewise — feedstock Ni is partly NATIVE METAL,
and only the oxide-hosted fraction enters the melt screen (the native fraction is outside a
silicate-melt activity model by category). Flags:
`enable_dilute_screen=[...]` flag required; each output row carries `tier=B-dilute-screen`,
`no_complexes=true`, a redox warning (Cr especially), the Gurvich/IVTAN source pin, and an
uncertainty statement. This is a screening assumption, NOT validated IMCC chemistry (scout
verdict language binds). P and S: hard refusal always — they need speciation/redox/solubility
models IMCC does not contain.

## 6. Tier C — Henrian trace screens (Chunk 8; gated)

γ=1 ideal-dilute bounds on ledger mole fraction for traces; 14 lanthanides first (condensed +
monoxide gas sources verified complete, scout §3.4); Sc2O3/Y2O3/HfO2 holes + remaining traces
ride t-616 (BLOCKED ON GROK per owner). Output labels: `tier=C-henrian-screen`,
`authority=order-of-magnitude`, `certification=denied`. Consumers: condenser-contamination /
tap-purity instrumentation only.

## 7. Outputs, identity, caching

- Result: per-parent {a_i, γ_i, x_i*}, full 46-species speciation vector, convergence
  diagnostics (residual norms, atom-balance closure), and the label block with THREE SEPARATE typed fields (identity / coverage / trust — never
  conflated): identity = `model_id=IMCC-SF04` + `datapack_version`; coverage = `tier` per species
  (A-published-imcc | B-dilute-screen | C-henrian-screen); trust = `evidence_class` using the
  repo trust vocabulary (`diagnostic-shadow`, non-authoritative, certification-denied for C).
  Evidence-class tokens route through `canonical_backend_name`; `diagnostic-shadow` and the
  Tier-B/C screen classes are added to the certification denylist STRUCTURALLY (the deny is in
  the token table, not in caller discipline). Token canonicalization is stated once, here (E5/E6).
- Typed nonconvergence: `imcc_nonconvergence` with diagnostics — never a silent partial result.
- Cache contract compliance (AGENTS §Cache identity): if cached, engine namespace is
  **`imcc-sf04`** (its own store/namespace — the two-install lesson), key = quantized melt input
  vector + T only, on the CANONICAL MOL BASIS (wt→mol conversion happens in the adapter BEFORE the
  cache boundary), REUSING the rail engine-cache quantization utility and quanta (no new
  quantization scheme — simplest-correct); datapack/model versions are METADATA, forbidden in
  the key.
- Placement (decided, not optional): new package `simulator/melt_backend/imcc_sf04/` + tests
  under `tests/`; zero imports from main-owned modules beyond stable public types; registration
  point named at chunk-3 exit. "Gas coupling" means the RESEARCH-HARNESS comparison layer of the
  H3 campaign lineage (`run_gate.py`-style, non-production), NOT any production flux path —
  chunk 5 builds its own thin gas mass-action layer in-package; production code is untouched.

## 8. Acceptance ladder (each rung blocks the next; tolerances DECLARED below, ratified at each
chunk exit — placeholders are named as placeholders)

1. **Kernel sanity**: analytic binaries + limiting cases (x→0, x→1, K→0/∞); atom balance ≤1e-12
   relative; determinism (same input → same output bit-for-bit).
2. **Melt-only literature checks** (Chunk 4): SF04 binary/activity checks + documented
   multicomponent activities. Tolerance: ±0.5 ulp of each printed literature value (last printed
   digit), per-check table at chunk-4 exit. FAILURE BLOCKS GAS COUPLING.
2b. **Gas-layer validation** (before rung 3): the in-package gas mass-action layer reproduces
   pure-component vapor pressures vs JANAF-table values within ±0.01 dex on a declared species
   sample. Attribution order for any rung-3 miss: melt kernel → gas layer → transcription.
3. **Workbook regression** (Chunk 5–6): `../VapoRock/data/Schaefer2004/Schaefer2004-MAGMA-valid
   .xlsx` — 8 sheets, 32 gas-species rows × 10 printed T (1500–2500 K); FC87 Table 4 Mercury
   fractional-vaporization endpoints (rounded). Tolerance placeholder ±0.02 dex vs workbook cells
   (covers cell rounding), ratified at chunk-5 exit. Workbook rows at 1500/1625 K that fall below
   a fit's demonstrated domain are evaluated ONLY under `allow_extrapolation` and reported as
   extrapolated fixtures, never silently. Workbook values are EXTERNAL FIXTURES stored under
   `tests/fixtures/` — never regenerated from this simulator. The nearby 101-T CSV grids are
   VapoRock comparison products: cross-model diagnostics only, never certification.
4. **Expected-offset check vs VapoRock (r2, review-corrected operationalization)**: concept — a
   correct shadow must reproduce MAGMA (rung 3) and therefore REPRODUCE the measured
   VapoRock↔MAGMA offsets; agreement with VapoRock where a real offset exists = a bug in the
   shadow. Operationalized on a FILTERED fixture set derived from `HT-C4-anchors/anchors.csv`:
   `model_model_MAGMA` class rows only (352/419), printed MAGMA cells only (the 64 no-printed-cell
   rows excluded), O2 rows excluded (fO2-pinned by protocol, not evidence), K rows excluded from
   the hard check pending the t-608 sheet-vs-paper anomaly (tracked, reported informationally),
   and the shadow evaluated under the SAME fO2-pinning protocol the H3 campaign used (polyfit of
   MAGMA O2 vs T per composition). Pass = |offset(shadow vs VapoRock) − offset(anchors.csv)| ≤
   2× the rung-3 tolerance per retained row; sign-known per-species divergences (FeO depression)
   checked explicitly. Rung-4 errata (binding, from the convergence close): (E1) retained rows
   below a fit's demonstrated domain (1500/1625 K cells) are evaluated ONLY under
   `allow_extrapolation` and reported as extrapolated fixtures — mirroring rung 3; (E2) the
   VapoRock side is the STORED anchors.csv model column (what rung 4 adds beyond rung 3 is the
   offset-reproduction check against the recorded H3 offsets; a live `../VapoRock` re-evaluation
   is optional cross-diagnostics and, if run, pins the sibling checkout SHA at rung-4 time);
   (E7) the retained-population species/count table is declared at chunk-5 exit — rung 4 covers
   the H3 anchor carriers only, NOT the workbook's 32 gas rows, stated as such. This remains the
   anti-motivated-reasoning lock — defined population, protocol, and tolerance.
   K-lineage policy pending t-608 (E3): the WORKBOOK governs rung 3 and the PAPER governs rung 2;
   if the two collide on a K check, the affected chunk exit BLOCKS until t-608 resolves.
   Rung 2b sample (E10): the pure-component species sample must cover every species retained in
   rungs 3–4.

Cross-references (E6): refusal semantics follow the V2 conventions (HT1-audit §1.4 lineage /
V2 package); two-install identity discipline per `patches/README.md` §Two-install policy; §10
mapping — rung 2b is Chunk-5 pre-work, rung 4 is the Chunk-6 exit gate; rung-3 placeholder
tolerance ratification at chunk-5 exit requires reviewer sign-off like every other chunk-exit
gate (E11). Tier-C source identity: every C-row pins its condensed + gas source identities
(Konings / IVTAN-electronic / NIST-ASD) in the row metadata (E6/P2-12).

**ERRATUM r2.2 (2026-08-11, review-converged: adversarial review imcc-r22-review.md verdict
REVISE-with-substance-upheld; the four required pins are folded here; FAIL-as-scored under r2.1
stays in the record).** The first rung-2 execution (IMCC-impl/rung2/, FAIL 0/40) exposed a class
error: the ±0.5-ulp tolerance is a reproduce-own-print instrument, but the gateable population
contained ZERO own-model prints — every gateable check was model-vs-independent-experiment.
Amended rung-2, all pins pre-scoring:

- **2-own** (ulp tolerance): population = own-model printed values; currently empty — discharged
  structurally by rung 3's workbook reproduction.
- **2-char** (independent-experiment characterization), population rule: in-domain rows only (no
  `extrapolated=true`) AND in-envelope (X_Me2O ≤ 0.5 — the boundary row is deliberately IN, see
  control below). Named checks, each with its kill target (review §3):
  (1) FeO γ underprediction sign — kills sign-flipped engines and complexing-sign inversions;
  (2) binary monotonicity sweep at fixture temperatures (∂a_Me2O/∂X > 0, ∂a_SiO2/∂X < 0 vs the
  X-ordered prints) — kills reversed engines;
  (3) Raoultian limit a_SiO2/x_SiO2 → 1 as X_SiO2 → 1 — kills denominator-confused engines
  (which check 1 alone cannot catch);
  (4) class tolerance on point rows: **±0.25 dex**, pinned BEFORE re-scoring from external
  anchors only (SF04's own claimed MAGMA–Zaitsev agreement band ~0.1–0.2 dex; Plante ±40 % ≈
  0.15 dex; KEMS lineage 0.11–0.18 dex) — NOT from the residual distribution.
- **Tier-A composition envelope**: X_Me2O ≤ 0.5. Evidence: SF04 Fig-3 validated span; the pack's
  alkali-richest associate is the 1:1 metasilicate (max Me2O:SiO2 = 1.00 over all 38 rows), so
  X > 0.5 alkali is structurally unbindable; zero-sensitivity proof for all excluded rows
  (∂a_Me2O/∂A_j = 0 for every pack row). Out-of-envelope evaluation returns typed
  `imcc_composition_outside_validated_envelope`. **Control (pre-registered): K-12 at X = 0.500
  exactly is IN-envelope, has nonzero sensitivity, and is EXPECTED TO FAIL the re-score** — the
  amendment does not rescue it; it is a standing fit-level finding at the metasilicate edge.
- F2 (K-fit sub-1700 K drift): no contract change — extrapolation labeling fired as designed;
  the sub-domain K rows are excluded by the population rule and reported informationally.
- Re-scored results are reported per-row INCLUDING everything that still fails.
- **OWNER RATIFICATION (2026-08-11) — rung-3 K/Na population:** the workbook's K and Na cells are
  **computed and reported in full, but EXCLUDED from the gate population** as closed-code-divergent
  ("calculate but exclude"). Evidence: the t-626 back-solve (IMCC-impl/t626/report.md) — reproducing
  the workbook K columns requires ~−2.0 logK vs the PUBLISHED fits at 1900 K with composition-
  dependent shifts, proving the shipped MAGMA code diverges from its published alkali parameters;
  the same shadow reproduces Mg/MgO at +0.03..+0.06 dex median. The excluded cells' residuals
  remain standing findings in every rung-3 report; the t-608 fixture adjudication is TRUST PAPER
  TABLE 9. This resolves the E3 K-lineage block for the chunk exit (workbook governs rung 3 only
  for the retained population; the paper lineage governs literature fixtures).

**OWNER RULING (2026-08-11) — extensions welcome, provenance-labeled.** Complexes beyond the
published 38 (alkali orthosilicates, Mn/Ni/Co silicates, and any compound with central-table
liquid thermochemistry) MAY be added, under two hard rules: (1) every complex row carries
`provenance_class: published-imcc | extension-compound-thermo` with its source — and extension
rows live in a SEPARATE extension pack, never mixed into the published-model pack (the published
shadow stays reproducible on its own); (2) results computed with any extension row active carry
`model_id=IMCC-SF04-EXT` (never plain IMCC-SF04), so "shadow of the published model" and "our
extended model" can never be conflated in a ledger. Extended-model validation targets are
Tsaplin-class independent data, not the MAGMA workbook.

**OWNER DIRECTION (2026-08-11) — the wide rail cross-reference:** run the IMCC machinery against
~all species where JANAF/central tables carry the compound thermochemistry ("spam the JANAF data
against the algorithm") as a rail cross-reference surface — shadow activities/pressures across the
rail catalog at screening-to-extension grade, every row provenance- and tier-labeled. This is the
t-616/t-627 lane; long-tail non-central sources still wait for grok per the standing ruling.

**OWNER DIRECTION (2026-08-11) — S/P compound extension (EXT-class parent-basis growth):**
JANAF carries liquid+gas tables for the regolith S/P compound classes (sulfides FeS/CaS/MgS/
Na2S-class, sulfates, Ca/Mg phosphates, and the P/S gas set). These enter the EXT model as NEW
PARENT COMPONENTS (S; P2O5) with compound SPECIES carrying the mass action — the same
ideal-associated formalism, extended basis. Rules: (1) the published-model P/S hard refusal in §5
STANDS for plain `IMCC-SF04` (the published model never contained them); under `IMCC-SF04-EXT`
with an explicit `enable_sp_extension` flag the refusal is replaced by the EXT species set, and
every S/P-bearing output row carries `tier=EXT-SP`, certification-denied; (2) coverage gates
become EXPLANATORY: a refusal for an S/P species names the class, the missing data or flag, and
what unlocks it; (3) sulfate/sulfide speciation is carried by the compound set itself (which
compounds exist at equilibrium IS the speciation answer in this formalism) — but redox coupling
to the Fe2+/Fe3+ couple and melt-solubility limits remain OUT of scope, stated per row; the rail's
own S-track (SulfLiq a_FeS) remains the authority for matte chemistry — EXT-SP is a screening/
cross-reference surface, never a competitor to it. Review-gated with the ext wave.

## 9. Non-goals

MAGMA source parity; full-rail coverage claims; recipe authority; replacing or retuning MELTS;
any Fe3+/fO2 modeling inside the engine; upstreaming (this is fork tooling; nothing here enters
the ENKI submission bundle).

## 10. Chunks and sequencing (adopt scout §6 estimates)

1 data pack (M, dual-transcribe + independent review) → 2 kernel (M) → 3 domain adapter (S–M) →
4 melt-only validation (M, GATE) → 5 gas-shadow coupling (M) → 6 fractional-vaporization
validation (M) → 7 Tier-B screens (M) → 8 Tier-C pipeline (L; lanthanides first) →
9 licensed-data closure (L, external; Barin/SGTE license limits recorded). Chunks 8-long-tail
and 9 are GATED ON GROK (owner) and t-616. Chunks 1–2 are parallelizable after this spec
converges.

## 11. Top risks

1. **Transcription fidelity** (fractional stoichiometry, OCR shifts) → dual-pass + atom
   validation + independent review (Chunk 1 exit gate).
2. **Under-specified closed conventions** read as "wrong result" → acceptance ladder defines
   equivalence; deviations from workbook beyond rounding are findings against OUR solver first.
3. **Model-form activity-depression behaviour misread** either direction (as our bug, or papered
   over) → §1 documents it (incl. that γ≤1 is not a theorem); the per-species sign checks land at
   rung 2 and the offset-reproduction check at rung 4 makes it a checked prediction.
