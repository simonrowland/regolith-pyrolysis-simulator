# Melt activity model registry schema v1

`data/melt_activity_models.yaml` is the single atomic registry for t-568. Its
whole-file SHA-256 digest identifies the loaded registry; each selected row has
its own canonical SHA-256 digest for random-variable keys. The
Phase-1 registry is shadow-only: loading, validation, and diagnostics may fail;
no row owns vapor-pressure, flux, ledger, or certification behavior.

## Canonical identities and numbers

- Every runtime/persisted model quantity uses natural log. Published log bases
  belong only in extraction provenance.
- Target selection is exact and per row. The default candidate is the
  component-qualified Raoultian supercooled-liquid state at queried
  temperature and 1 bar; b-154 overrides only CaO and MgO with their
  adjudicated crystalline states.
- AlO1.5, TiO2, CrO1.5, and MnO carry exact candidate identities but
  `resolution_status: standard_state_unresolved`. They refuse Tier-A
  evaluation until the independent t-570 target sidecars land; algebra alone
  cannot promote them.
- Cache/random-variable identity is `(row_id, row_digest, state_fingerprint,
  target_standard_state_id)`; gas species never appears in that key.

## Model rows

Every `model_rows[]` entry requires:

1. immutable `row_id`, `tier`, `model_family`, status, and ceiling;
2. `rail_component.{id,parent_oxide,formula_multiplier}`;
3. provider/version/database, exact engine components, rational basis
   coefficients, and evidence references under `source`;
4. exact `source_standard_state` and per-row `target_standard_state`, including
   its resolution status and any metastable policy;
5. conversion source/target `mu0` references, formula-balance receipt, and
   pressure-correction policy;
6. T/P, matrix, redox, phase, complete-inventory, and unmodelled-reservoir
   domain policy;
7. natural-log band offsets, kind/coverage, independent/marginal sigma, signed
   correlation loadings, and correlation-basis reference;
8. primary provenance/review/digest; and
9. calibration families, holdouts, residual metric/result, and certification
   ceiling.

Unknown required fields fail load. Unknown optional metadata is inert.
Selection is exact component/state, in-domain, highest admitted evidence tier,
then newest non-superseded row. Equal-priority selection ambiguity refuses.

Phase 1 contains no Tier-B coefficient rows. `tier_b_model_version` freezes the
six `ln(gamma_infinity)` and five Wagner coefficient slots. Candidate admission
also requires a reviewed primary source, exact component/oxidation/basis,
complete matrix and T/P/fO2/phase/concentration domain, identifiable published
coefficient, source concentration/log convention, uncertainty/covariance,
apparatus metadata, canonical conversion receipt, frozen class/design matrix,
non-empty calibration families and holdouts, residual band, ceiling, an ordered
interaction basis covering every nonzero matrix component, and one origin (`direct_fit`,
`descriptor_prediction`, or `structural_zero`) for every interaction term.
Each term also carries its epsilon(T) and covariance row/column receipt.
Omission never means zero; species intercepts and unregistered response
families are forbidden.

Admission enforces the Rev-3 quantitative gates: at least three independent
publication families, two matrix families, `N_independent_solute_classes >=
3p`, the frozen six/five-slot DOF, publication-family p95 at most `ln(10)`, and
the solute-class p95/bias/95%-band-coverage/worst-class ceilings. Passing or
omitting one holdout axis cannot substitute for the other.

Registry load validates numerical meaning as well as field presence: basis
coefficients and formula multipliers are finite, T/P domains are ordered
finite intervals, and finite natural-log bands satisfy `lower <= 0 <= upper`.

## Tier C inventory and fail-closed categories

`tier_c_inventory` contains exactly the 55 demand-manifest elements covered by
neither the 14 accepted engine cation elements nor the current engine surface.
Every row carries row ID, element, component/formula identity fields, target
state, matrix receipt, and an explicit disposition. Unknown identities remain
null and explicitly refused; omission is not ideality. Phase 1 admits only the
chemically identified `LiO0.5` conditional-ideal skeleton. Its evaluation still
requires an exact component, formula basis, target state, named normalized mole
basis, matrix receipt, and complete atom-balanced inventory. The normalized
fractions are recomputed from the ordered reservoir account and must reproduce
the supplied inventory digest; a caller assertion cannot prove emptiness.

The runtime categories are exhaustive:

1. missing identity/input/owner/coefficient: visible `Refusal`;
2. declared continuation with widened band: finite `StatusBearingValue`;
3. proven empty component: `value: 0`, `ln_value: null`,
   `zero_because: proven_empty_component`.

Tier-C ideal central value is `ln(a)=ln(X)`, authority false, certification
ceiling never, with `unbounded_model_form` band. Missing data is never ideal or
zero. Cr(II), Ti(III), sulfur, fluoride, chloride, bromide, iodide, and salt
reservoirs remain owner-specific refusals.

## Bands and covariance

Finite bands are offsets `[lower, upper]` around the central natural-log value,
with `lower <= 0 <= upper`. Statistical rows require independent sigma, signed
latent-factor loadings, and a complete symmetric PSD correlation basis with
unit diagonal. Repeated random-variable keys are coefficient-aggregated before
covariance. Bounded envelopes use interval arithmetic; statistical bands use
`c^T Sigma c`; mixed bands use a Minkowski sum. Missing shared uncertainty
refuses authority rather than assuming independence.

## Phase-0 self-check and Phase-1 pins

The loader's Phase-0 check reads the real `data/vapor_pressures.yaml`, requires
all six selected CaO/MgO declarations to match the crystalline-at-T tuple, and
requires the six b-154 pin impacts to be `crystalline_coherent`. It would have
refused the pre-`7c8a7f6` liquid declarations.

`data/melt_activity_shadow_pins.yaml` assigns every Phase-1 table/Fe probe
exactly one legacy-domain `disposition` and one independent-comparison
`comparison_status`. `legacy_in_domain` says only that the executed legacy path
had its required inputs; it is not evidence of resolver equality.
`comparison_status: comparable` is reserved for a resolver result produced from
independent engine-basis evidence plus an explicit source-to-target
standard-state conversion. Its signed `delta_ln` is compared against the
manifest's `comparison_tolerance_ln`. `not_comparable_yet` records a typed
blocker and is excluded from both equality numerator and denominator; it can
never be re-logged as agreement. `legacy_degraded` requires the declared typed
refusal and remains separately excluded while legacy behavior stays unchanged.

The state fingerprint covers only the ordered `process.cleaned_melt` reservoir
account plus T, P, intrinsic fO2, the full Kress input composition, and the Fe
redox pressure/basis/model receipt.
Unrelated gas/product ledger accounts do not re-key melt activities. Negative,
non-finite, non-numeric, or missing melt entries mark the inventory incomplete.
Nonzero Cr(II), Ti(III), sulfur, elemental or compound halide, and salt
reservoirs are ownerless spectators: supported rows refuse unless a reviewed
spectator policy explicitly covers them.
