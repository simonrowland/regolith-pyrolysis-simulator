# S2 rev5 yield-disposition backend report

## TL;DR

- Added rev5 origin-first, 12-bin yield disposition to success/projectable partial outcomes; failed/refused envelopes preserve their primary failure when projection is unavailable.
- Feedstock-element fractions, chart-ready nodes/links, melt subdispositions, physical stream rows, and a separate reagent reconciliation ship in one payload.
- Lunar, Mars, and CI live runs close at `2.220446049250313e-16`; reagent closure is `0.0`.
- Stage 0 replay now typed-crashes on every missing origin; account membership never manufactures reagent lineage.
- Focused fix gate: 15 passed; mol-native artifact guards: 2 passed.
- Two live clean-C2A trace retries were inconclusive at the 300-second MAGEMin timeout; the exact `2.78e-17 mol` roundoff regression passes.
- Five golden nodes across three fixtures remain expected churn; no golden was regenerated.

## Payload schema

Runner top-level key: `yield_disposition`.

`schema_version = "5.0"` contains:

- `basis = "feedstock_element_atom_fraction"`.
- `destination_bins`: the frozen 12-bin rev5 taxonomy, including
  `metal_phase_retained`.
- `fraction_table.rows`: one row per emitted feedstock element. Each row has
  charged feedstock mol-atoms, mol-atoms and fractions for all 12 bins, and
  normalized closure residual.
- `melt_retained_subdispositions`: element allocations across
  `oxide_unextracted`, `refractory_rump`, `residual_reductant`, and
  `stage0_slag`.
- `nodes` and `links`: chart-ready feedstock-element to destination edges;
  link values are mol-atoms and feedstock-element fractions. Source ledger
  accounts are attached.
- `terminal_species_streams`: terminal account, destination, campaign scope,
  melt subdisposition, species, mol, kg, and origin scope.
- `reagent_cycle.rows`: input and terminal-excluded mol-atoms by element,
  reconciled outside the feedstock denominator.
- `closure`: frozen `5e-14` limit and maximum observed residual.

The builder is read-only over `AtomLedger`; all simulator transitions remain
under the existing validated commit paths. Hourly capture writes a
condensation-only observer sidecar on the simulator in mol-atoms, never kg.

## Origin-resolution findings

The runtime observer is sufficient for the exercised reagent-bearing C3
shuttle path:

```text
tests/test_runner_smoke.py::test_c3_alkali_recipe_dose_routes_to_credit_line_not_additives
1 passed in 34.63s
```

Mars Stage 0 is the important exception. Stage 0 reagent provenance is
observable during pretreatment but the live map is subsequently reset. The
producer exactly replays only the leading Stage 0 transition prefix, carries
feedstock/reagent element shares through conserved credits, then cross-checks
any overlapping live observer value. Replay activates only when the prefix
actually debits a Stage 0 process/reservoir account, so a later C3 reagent draw
cannot be misidentified as Stage 0.

Synthetic coverage proves:

- resolved mixed reagent atoms are excluded without rescaling feedstock;
- unresolved commingling raises `OriginUnresolvedError`;
- unknown positive terminal accounts raise `YieldDispositionError`, including
  balances just above the ledger storage floor;
- cleanup/main condensation is split from campaign snapshots and ambiguous
  withdrawals fail closed;
- feedstock-recovered reagent inventory remains feedstock origin;
- Fe/Si retained pools remain distinct from drain-tapped product.

Not-fixed null hypothesis: the observer may still lack enough information for
an unexercised future reagent-bearing transition topology. C3 and current
Stage 0 reject the hypothesis for their paths; any new unresolved topology
will intentionally typed-crash rather than guess.

## Closure receipts

Live current-tree runner payloads:

| Feedstock / outcome | Status | Emitted elements | Max feedstock residual | Reagent residual |
|---|---:|---:|---:|---:|
| lunar mare low-Ti, C0 24 h | ok | 12 | `2.220446049250313e-16` | `0.0` |
| Mars basalt, C2A 12 h + 30 kg C | ok | 13 | `2.220446049250313e-16` | `0.0` |
| CI carbonaceous chondrite, C2B 12 h | ok | 12 | `2.220446049250313e-16` | `0.0` |

The normalized derivation is encoded beside the arithmetic:
`destination mol-atoms / charged feedstock mol-atoms`; the reported residual
is `fsum(fractions) - 1`. No scale-to-close operation exists.

## Plant-flowsheet v9 field coverage

| v9 need | Payload source | Coverage |
|---|---|---|
| Sankey origin → destination | `nodes`, `links` | Complete; feedstock element and destination are backend-authored. |
| Normative yield fractions | `fraction_table.rows` | Complete on feedstock-element atom basis. |
| Terminal stream account / destination | `terminal_species_streams.account`, `.destination` | Complete for mapped nonzero accounts; unknowns fail. |
| Per-species mol and kg | `terminal_species_streams` | Complete for physical terminal inventory. |
| Total stream kg | Sum `terminal_kg` by account/campaign scope | Derivable without chemistry inference. |
| Cleanup vs main condensed product | `campaign_scope` plus destination | Complete from retained-inventory deltas. |
| Fe0 bottom tap / Si0 top skim | bottom-pool and float-layer account rows, both `metal_phase_retained` | Complete and distinct from drain-tapped product. |
| Drain product | `terminal.drain_tap_material` → `product_tapped` | Complete. |
| Melt/rump detail | `melt_retained_subdispositions` and stream `subdisposition` | Complete for rev5 taxonomy. |
| Stream temperature and pressure | none | Gap; cannot be derived from this payload. |
| Phase label | none | Gap; account/species must not be stretched into a phase inference. |
| Equipment from/to stream identifiers | none | Gap; destination/account is not a flowsheet equipment edge. |
| Feedstock-only species split inside a mixed-origin species stream | none | Gap by design; physical species totals remain available, while normative origin closure stays per element. |

The missing T/P/phase/equipment fields belong to a flowsheet stream-state
surface, not the 12-bin disposition taxonomy.

## Verification and full-suite delta

The pre-fix review state was at least nine patch-related failures: five
golden-derived nodes plus four integration regressions (two failure-envelope
paths and two clean-C2A trace paths). The prior version of this report omitted
those four integration failures.

Current focused fix gate:

```text
python -m pytest -q -n 0 tests/test_yield_disposition.py \
  tests/test_runner_smoke.py::test_runner_preserves_primary_failure_when_poison_enrichment_fails \
  tests/test_runner_smoke.py::test_runner_failure_preserves_primary_when_yield_disposition_raises \
  tests/test_runner_smoke.py::test_runner_detail_fallback_preserves_refused_status_and_live_rows
14 passed in 5.95s

MPLCONFIGDIR=/private/tmp/rps-yielddisp-mpl \
  python -m pytest -q -n 0 \
  tests/test_runner_smoke.py::test_runner_schema_shape_contract
1 passed, 7 warnings in 233.74s
```

The 15 green checks cover the original eight producer tests, three new
accounting pins (closure-tolerance roundoff, Stage-0 no-guess, known-account
mapping), the new disposition-failure envelope pin, both existing hostile
failure-envelope cases, and the live success schema node.

Additional:

```text
mol-native ripgrep guards: 2 passed in 1.25s
py_compile: pass
git diff --check: pass
```

The full `tests/test_artifact_guards.py` file is not green:

```text
7 passed, 1 failed in 17.37s
test_data_yaml_survives_latin1_misdecode:
data/flowsheet.yaml:1 byte=0x80
```

That data-file failure is outside this staged change; the two mol-native guards
required by this patch pass independently.

The two real clean-C2A VPR trace nodes were retried after the fix. The serial
pair timed out in the CLI subprocess, and the isolated sentinel node timed out
waiting for MAGEMin through `engine_pool` at the configured 300-second ceiling.
Neither retry reached a disposition assertion, so they are inconclusive and
are not claimed green. The exact observed receipt
(`physical=0.22746831458806405 mol`,
`recovered=0.22746831458806402 mol`) is pinned in the green focused suite.

Golden churn remains enumerated as five nodes:

1. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[lunar_mare_low_ti_C0_24h]`;
2. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[mars_basalt_C2A_12h]`;
3. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[ci_carbonaceous_chondrite_C2B_12h]`;
4. `tests/test_recipe_io.py::test_no_recipe_run_matches_committed_golden_text`;
5. `tests/test_cost_ledger.py::test_cost_rollup_metadata_is_golden_neutral_for_runner_fixture`.

The controller must regenerate exactly:

- `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`
- `tests/fixtures/runner/mars_basalt_C2A_12h.json`
- `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`

No golden was edited here.

The isolated perf gate was red on both trees:

| Tree | `internal_analytical_equilibrium` | Threshold |
|---|---:|---:|
| detached HEAD | `4730.41/s` | `5492.76/s` |
| current | `4712.94/s` | `5492.76/s` |

Result: pre-existing perf red, approximately unchanged. No ratchet margin,
baseline, or hot path was modified.

## Self-review

Two lenses were applied: (A) origin/account conservation and failure behavior;
(B) consumer schema, flowsheet derivability, and runtime cost.

| Category | Verdict |
|---|---|
| Taxonomy / authority | Pass: exactly 12 rev5 bins; retained metal pools and drain product stay distinct. |
| Origin / data integrity | Pass for synthetic, C3, and current Stage 0; future unresolved topology remains typed fail-closed. |
| Numerical closure | Pass: all live receipts are `2.22e-16`, below `5e-14`; no scale-to-close. |
| API / serialization | Pass: normative table and chart structures are backend-authored on success and projectable partial outcomes. |
| Error / security boundary | Pass: non-finite balances, unknown positive accounts, over-allocation, and ambiguity fail explicitly; no dynamic execution or external I/O. |
| Performance / concurrency | Pass with caveat: campaign snapshots were narrowed during review from the full ledger to condensation-only state; existing perf ratchet remains red on baseline and current. |
| Tests / operability | Fifteen focused checks green; exact golden churn enumerated; two real C2A retries inconclusive at the MAGEMin timeout; full artifact guard has one unrelated data-byte failure. |

Review fixes made before staging:

1. removed the `1e-12` projection cutoff so every stored positive unknown
   account is rejected and tiny stored element inventories participate;
2. reduced per-hour snapshot memory from full-ledger copies to the only state
   required for campaign attribution.
