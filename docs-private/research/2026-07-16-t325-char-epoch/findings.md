# t-325 char-as-reductant physics epoch findings

## TL;DR

Refractory organic carbon is now committed as mol-native solid C, consumed
first by a configured O2 lance, and otherwise retained for the
`FeO + C -> Fe + CO` melt reaction above the owner-ratified 700 C Ellingham
crossover. Every reaction is a kernel proposal committed through
`commit_batch`; the mass-closure tolerance is unchanged. Carbonaceous runner
output moves by design. Canonical organics-free lunar and Mars runner bytes
match `d770938` exactly.

## Committed mechanism

### Solid-C account

Stage-0 complete oxidation partitions each declared organic carrier's carbon
using `f_refractory_organic_C`. Labile C goes to CO2; refractory C is credited
to debitable `process.solid_char_carbon`. The account is declared centrally in
`simulator/account_ids.py`, admitted by `AtomLedger`, included in flow mass,
and declared in the builtin Stage-0 provider capability profile.

CI derivation from the configured carrier formula and Sephton floor:

- premise: `f_refractory = 0.39` (REF-024 Sephton 2004 Murchison
  hydropyrolysis; its H2/520 C regime caveat remains explicit);
- algebra: `n_char = n_C,carrier * 0.39 = 958.7221688514541 mol`;
- unit check: `mol C * 0.0120107 kg/mol = 11.515211970074814 kg C`;
- sanity: total ledger C equals independently reconstructed raw-feed C before
  and after every char transition.

### O2-lance first claim

The configured default is complete oxidation under the local O2-rich lance:

- `C + O2 -> CO2`: one mol O2 per mol C;
- optional scenario basis `C + 1/2 O2 -> CO`: one-half mol O2 per mol C.

The bubbler injection budget now includes live char O2 demand before the
melt-fO2 target demand. This matters at/above the melt target and at zero melt
redox capacity: commanded O2 still reaches char instead of being capped to
zero by the redox controller. After char consumes its first claim, only the
remaining injected O2 can respeciate FeO/Fe2O3; surplus follows the existing
overhead path. CO/CO2 credits `terminal.offgas`, the mol-native ledger behind
the Stage-0 volatiles/`co_ch4_propellant` product class.

### FeO + C third Fe exit

Premise: the owner-ratified REF-020 JANAF C/CO versus Fe/FeO crossover is about
700 C. Above it, the builtin provider proposes:

`FeO + C -> Fe + CO`

- algebra: extent `min(n_FeO, n_char)`, with 1:1 Fe and CO;
- unit check: `mol Fe * 0.055845 kg/mol = kg Fe`;
- CI worked value when FeO is in excess:
  `958.7221689 mol * 0.055845 kg/mol = 53.5398395 kg Fe/t feed`;
- the brief's approximately 80 kg Fe/t is an order-of-magnitude CI check, not
  a threshold and not forced into the model.

Kinetics choice: instantaneous process-equilibrium at the one-hour timestep,
capped by reactants. This is explicitly a modeling assumption, not a resolved
kinetic law; no unsupported rate constant or tuning parameter is introduced.

### Contamination honesty

Positive surviving char emits a WARN-only diagnostic for P/Cr/Ti oxide
reduction where those oxides are present and for carbide formation. The
threshold is thermodynamic onset (positive residual char), because no sourced
safe non-zero residual-char allowance exists. Susceptible P2O5/Cr2O3/TiO2
inventory is reported separately. No selectivity or carbide products are
committed. Vacuum `SiO2 + C -> SiO(g)` remains explicitly out of scope.

## Carbon and mass conservation

Formation, lance oxidation, and FeO reduction each use atom-balanced
`LedgerTransitionProposal` objects and kernel `commit_batch`; no provider
mutates the ledger. Tests independently reconstruct raw-feed carbon from
feedstock kg plus formula atoms, then reconcile it with carbon across every
ledger account. The focused CI tests also assert mass-balance error below the
existing `5e-12 %` bound before and after FeO reduction.

## Golden inventory and zero-delta controls

Do not regenerate goldens in this chunk.

Moved by design:

- `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`
  - base `d770938`: SHA-256
    `f01d5e298919d8a3adb2e5317357039b8fbaa697f94cadc67f000580c99818d3`,
    78,921 canonical JSON bytes;
  - t-325: SHA-256
    `1eca1bfe7c03e8cc327a34e2b238276a1c8014bff05f7d75d37531a0ae7dfce2`,
    78,973 bytes;
  - attribution: Stage-0 withholds the refractory carbon from CO2 and carries
    it in `process.solid_char_carbon`. The 12-hour C2B fixture remains below
    the FeO+C activation point, so its char survives; it does not yet receive
    Fe/CO reaction products.

Byte-identical canonical organics-free controls versus `d770938`:

- `lunar_mare_low_ti_C0_24h.json`:
  `a9b2f7b58c7e47894aca38525524a6f932e794a54a18ff3db05b157628d4ed41`,
  181,215 bytes on both trees;
- `mars_basalt_C2A_12h.json` (required process-C additive but no organic-char
  partition):
  `c4c262513e686e76f8a20d5585647edc9e7a262f8165fb9751c61056ac9edd31`,
  78,770 bytes on both trees.

Catalog-wide proof used every feedstock with no positive organic carrier in
`composition_wt_pct`: 24/24 base-vs-t-325 surfaces were identical. Seventeen
produced runner payloads; seven produced the same refusal/exception surface on
both trees. Mismatches: none.

The absolute runner-golden triplet reports Mars pass, intended CI failure,
and lunar failure. The lunar runner payload reproduces byte-for-byte on untouched
`d770938`; it is pre-existing golden drift, not a t-325 delta. Likewise, the
three `test_split_path_end_state_matches_pre_flip_account_balances` wall
deposit assertions fail identically on untouched `d770938` (3 failed,
47 deselected in 58.81 s).

## Validation receipts

- final changed/new focused gate (`test_char_reductant_epoch`, C0 diagnostics,
  Stage-0 provider, feedstock inventory): 111 passed in 25.87 s;
- accounting + molar + mass-balance gate: 106 passed, 19 warnings in 69.25 s;
- O2-bubbler focused regression selection: 17 passed;
- chemistry `-n0`, excluding the three separately proven stale condensation
  wall pins: 1,176 passed, 103 skipped, 3 failed, 3 deselected, 353 warnings in
  983.17 s. The three failures are CrO2 terminal capture and two SiO golden
  pins; the identical node IDs and values fail on untouched `d770938`
  (3 failed in 74.36 s), so none is a t-325 delta;
- the separately excluded condensation-wall parity parametrization also fails
  identically on untouched `d770938` (3 failed, 47 deselected in 58.81 s);
- runner golden triplet: Mars pass, CI intentional movement, lunar stale
  fixture; current/base canonical runner bytes prove zero t-325 delta for the
  two organics-free fixtures;
- catalog-wide runner/refusal comparison: 24/24 organics-free feedstocks
  identical, 17 payloads plus 7 identical refusal/exception surfaces;
- `git diff --check`: clean.

BLOCKED: staged for controller commit

READY: docs-private/research/2026-07-16-t325-char-epoch/findings.md
