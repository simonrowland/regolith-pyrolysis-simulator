# OpenIMCC fold 13 — one composition resolver

Seat tip at design time: `7f75d9f6fa1789efb3832f415fa3ccab5b5830d2`.
The fold-12 review of record (FIX-FIRST) is the source for the defects below.

The failure this design prevents: a caller stamps `proxy_flag=initial_charge_only` and `input_provenance=printed_starting_bulk` onto an ordinary assay, then asks the role owner whether that assay is a starting charge. The owner treats the stamp it was just handed as a declaration (`composition_roles.py`, the `role is None and has_proxy` branch). The stamp authorizes itself. Twelve rounds of per-call-site patches left that loop, and left a second resolution of the same point before its assays exist.

## (a) Census

Decision means the site chooses a role, a proxy flag, or which assay is the engine composition. A field read returns the printed `composition_role` token and does not default, stamp, or choose. A consumer reads the resolver output or a stamp the resolver already applied.

| Site | What it does today |
|---|---|
| `simulator/battery/composition_roles.py::resolve_composition_role` | Decides. Reads `composition_role` from the row then the parent. Defaults an unrecognized token to `initial_charge_only` when the chosen assay is usable. Treats an existing `proxy_flag` as a declaration when the field is absent. That last step is the self-authorization. |
| `simulator/battery/migrate.py::_initial_charge_only_composition_proxy` | Decides, then asks the owner. The non-flat path stamps `initial_charge_only` and `printed_starting_bulk` on whatever assay it found before `resolve_composition_role`. An absent role then comes back declared. A `datum_state` result takes the outside-scope return and drops the selected datum. |
| `simulator/battery/migrate.py::_emit_exploded_point` | Field read before any candidate assay exists. The printed token directs located-composition parsing, the omitted-component issue, and the missing-major gate. Those gates stay field reads: they run before an assay exists. The same row and parent are resolved once inside the proxy. |
| `simulator/battery/migrate.py::_migrate_extract_observation` | Two decisions for one parent. The second reads `equipment` only when `values` has no token and no error and no declared wt% map. Neither call has an assay yet. The recognized token is what the later branches use; defaulting cannot fire because no assay was passed. |
| `simulator/battery/migrate.py::_composition_located_from_values` | Decides from `values` with no assay, then stamps `proxy_flag` itself when the token is `initial_charge_only`. Provenance is left to `_finalize_input_provenance`. |
| `simulator/battery/migrate.py` sample parse | Decides from the sample payload, then stamps `printed_starting_bulk` when the role is the charge token or the composition already carries the flag. |
| `simulator/battery/migrate.py::_printed_map_is_typed_initial` | Decides. Uses `.role` with no assay, so the result is the printed token. |
| `simulator/battery/migrate.py::sample_from_equipment` | Same token read, through the full resolver. |
| `simulator/diagnostic_helpers/extract_reproduction.py::_resolve_melt_recipe` | Decides the assay in `_melt_recipe_mol` (point, then flat, then parent), then asks the role owner about that one recipe. |
| `simulator/diagnostic_helpers/extract_reproduction.py::_record_composition_role` | Consumer with a knob that drops the decision. `include_declared` defaults false, so a declared charge flag is discarded unless the caller opts in. |
| `simulator/diagnostic_helpers/extract_reproduction.py::_evaluate_rate` | HKL reader. Calls `_record_composition_role` without `include_declared`. A declared starting-charge point with a numeric flux is scored clean. The pressure reader already passes `include_declared=True`. Ordering, relative volatility, and activity use the default and drop declared flags too. |
| `simulator/battery/score.py::_initial_charge_composition_notice` and `simulator/battery/score.py::_flagged_stratum_notices` | Consumers. They read `resolve_composition_role(observation).notices`. |
| `simulator/battery/score.py::is_initial_charge_composition` | Consumer of the persisted `proxy_flag` on a stored identity. |
| `simulator/battery/migrate.py::effective_run_composition` | Consumer of the persisted flag. An unknown point composition outranks a charge proxy. |
| `simulator/battery/migrate.py::_finalize_input_provenance` | Consumer of a flag already on the composition: a charge flag selects `printed_starting_bulk` instead of the locator class. It does not choose the role. |
| `simulator/battery/migrate.py::_apply_equilibrium_starting_bulk` | Different rule. Closed-system equilibrium rows copy the experiment's printed start. Not a `composition_role` decision. Left. |
| `simulator/battery/input_provenance.py::phase_map_provenance` | Different rule. Locator and phase text choose `printed_starting_bulk`. Left. |
| `simulator/battery/composition.py` charge-provenance writer | Different rule. Equilibrium bulk provenance, not the role token. Left. |

`rg` of `resolve_composition_role(` and `resolve_point_composition(` after the change is the handoff census. Decision logic stays in `resolve_point_composition` only.

## (b) The one resolver

```python
def resolve_point_composition(
    row: object,
    parent: object = None,
    candidates: Sequence[AssayCandidate] = (),
    *,
    quantity: Quantity | None = None,
    origin: str = "",
    notices: Sequence[Notice] = (),
) -> RoleResolution:
    ...
```

`AssayCandidate` is the decoded assay (`State`), a source tag (`point`, `identity`, `parent`), an optional label and locator, and `terminal` for an explicit gap or a hard refusal.

Output is the existing `RoleResolution`: `role`, chosen `composition`, `status`, `flags`, `reason`, `original_reason`, `notices`. The proxy flag and the starting-bulk provenance are on `composition.value` when the decision is a starting charge, and absent otherwise. `source_label` and `locator` say which candidate was chosen.

Choice rule, in the resolver, not in the callers:

1. Walk candidates in order. A `terminal` candidate is the result. Later candidates are not used.
2. Otherwise the first usable candidate wins. Usable means a value that is a `Composition`, or a non-empty mapping recipe.
3. A `point` candidate that is a `Composition` beats a later `identity` candidate. A non-composition point does not beat a usable identity. This is the fold-12 discarded-identity rule, with the choice made here.
4. If nothing is usable, the result is the first candidate, or `missing_condition:melt_composition`.

Role rule, from the printed field only:

1. Read `composition_role` on the row, then the parent. The first context that has the field wins, including an unrecognized token. Do not fall through.
2. An unrecognized token on a usable chosen assay takes the stated default `initial_charge_only`, with `RoleStatus.DEFAULTED`, `ROLE_DEFAULT_FLAG`, and the existing typed notice. That default is unchanged (predict and flag).
3. A recognized `initial_charge_only` is `RoleStatus.DECLARED`. The chosen `Composition` is returned with `proxy_flag=initial_charge_only` and `input_provenance=printed_starting_bulk`, and the canonical charge notice when a quantity is present.
4. `datum_state` is declared, carries no charge flag, and does not rewrite provenance.
5. An absent field does not invent a role from a stamp this call created. A `proxy_flag` that already arrived on the chosen candidate is a previous decision's persisted output (the store keeps the flag, not a second copy of the field). The resolver may still treat that arrived flag as declared. It must not be shown a flag the caller wrote on the way in.

`resolve_composition_role(row, parent, *, composition, point_composition, ...)` remains the wrapper the scorer and the existing tests call. It only packs `point` and `identity` candidates and calls `resolve_point_composition`.

`read_composition_role_field(*contexts)` returns the printed token and the unrecognized-token reason. It does not default, stamp, or choose. Callers that must parse a map before an assay exists use this. They do not call the resolver twice.

## (c) Nothing is stamped before the resolver runs

`_initial_charge_only_composition_proxy` decodes candidate assays and does not set `proxy_flag` or `input_provenance`. It calls `resolve_point_composition` once. The composition it stores is `resolution.composition`.

- Starting charge: the stored assay is the stamped output.
- `datum_state`: the selected datum is written onto the identity and the point. It is not given a charge flag. This is the bind the outside-scope return currently drops, so migration and reproduction use the same located datum.
- No role and no arrived flag: the original identity is returned. Provenance and tier stay as they were. An ordinary non-flat activity assay is not a starting charge.
- Composition quantities (`equilibrium_phase_composition`, `residue_component_composition`, a printed quantity that says composition) are still not promoted to charge proxies.

A caller that stamps a candidate and then calls the resolver has reintroduced the bug. The ordinary-assay pin plus that mutation is the guard.

## (d) Call sites to delete or reroute

Delete:

- The pre-resolver `replace(..., proxy_flag=initial_charge_only, input_provenance=printed_starting_bulk)` in `_initial_charge_only_composition_proxy`.
- The full `resolve_composition_role` in `migrate.py::_emit_exploded_point`. The printed token comes from `read_composition_role_field`.
- The pair of parent resolves in `migrate.py::_migrate_extract_observation`. One field read, with the equipment context only under the existing wt%-map gate.
- The `include_declared` knob on `_record_composition_role`. Every reader records declared flags. HKL included. A declared starting-charge rate point stays `assumed-input`, never `match`.

Reroute:

- `_initial_charge_only_composition_proxy` calls the resolver once and stores its composition. `datum_state` binds the datum.
- `_composition_located_from_values` calls the resolver once, without handing it a usable assay (so an unrecognized token is not defaulted here). It stamps only when the output role is the charge token.
- `_resolve_melt_recipe` decodes the point with parent fallback off, decodes the parent separately, and passes both candidates. The resolver chooses. `_melt_recipe_mol` stays the decoder.
- Sample parse, `_printed_map_is_typed_initial`, and `sample_from_equipment` read the token through the resolver or the field reader. They do not grow a second role rule.
- Score notices and `is_initial_charge_composition` stay consumers.

Left, with a reason:

- `_finalize_input_provenance`, `effective_run_composition`, and the equilibrium starting-bulk writer. They classify provenance or read a flag the resolver already applied. They do not choose `composition_role`.
- `phase_map_provenance`. Locator text is a different rule from the role token.

## Other fixes in this fold

- P1 HKL. Removing `include_declared` is the fix. The rate regression carries a declared charge point between the defaulted point and the measured point.
- P1 De Maria Figure 2 closure. The identity match and the effusion handoff are the production fix. The two callable potassium points stay flagged predictions; the eight points with no composition stay refusals.
- P2 three red tests. The De Maria notice constant is read from `simulator.battery.records`. The ordering hash-seed fixture gains `values`. The Fe main-cell row is pinned as `out-of-domain` with `actual is None`, the missing-composition reason, and zero provider calls. The complete fixture still expects one `FeO` call and `assumed-input`.
- P2 rollup. Not regenerated. The committed gap pin stays 2029 until the train runs `RPS_T512_REGEN_ROLLUP`. A Studio run of the self-authorizing tree measured 2056. The corrected count after the closure and ownership fixes is unmeasured. It is not that 2056 measurement.

## Store delta this fold expects

No store is regenerated and none is committed.

- Costa's declared starting-material role stays a starting-charge qualification on the existing rows. The prior census was 23 qualifications across 113 rows. Those are declared, so they remain.
- De Maria 1971 and 1973 keep a linked assay only where the source declares a role or the token is a genuine unrecognized default on a usable assay. The prior 34 (of 117) and 16 (of 75) counted the self-authorized stamps. They are an upper bound, not the corrected delta.
- An ordinary non-flat assay with no role keeps its input provenance and is not a new starting-charge row.
- A point whose role is `datum_state` stores the located datum on the identity, with no charge flag. Reproduction already uses that datum.

## Mutations

One mutation per fix, in the test file, each expected to fail the pin:

- Pre-resolver stamping: wrap the resolver so the ordinary assay is stamped before the decision. The provenance pin must fail.
- Dropping declared HKL flags: `_record_composition_role` ignores non-defaulted roles. The declared rate point must fall into the clean tier and the rate pin must fail.
- Skipping the datum write: the resolver result's `datum_state` is cleared before the proxy binds. Migration's identity must diverge from the reproduction recipe and the pin must fail.

## Fold 14

The resolver still chooses. Callers decode and apply `RoleResolution`.

- `composition_roles.py::choose_stored_run` is the stored-observation rule shared by `migrate.py::effective_run_composition` and the empty-candidate `Observation` path inside `resolve_point_composition`. A usable identity outranks a usable point. An unknown point outranks a charge identity, so a refused run is not scored as the printed charge. Explicit candidates stay point-first in `_choose_assay`.
- A flat-run candidate sets `inherit_parent_role` false. The parent's token does not describe those columns. An explicit token on the point is still read.
- An invalid printed point (unsupported component, malformed amount, unusable mass-percent map) is a terminal candidate. A later starting charge does not replace it. A map that is not a composition, such as multiple untyped columns, still leaves a decoded point available.
- `datum_state` stores the chosen assay's `Located`, including its inference. Migration does not build a fresh state that drops the derivation.
- `_emit_exploded_point` still uses `read_composition_role_field` for the partial-pressure decode gate and the missing-major identity hold. Those run before an assay exists. They are field reads, not a second role decision.
- No store is regenerated. Costa's declared charge qualifications stay. De Maria 1971 and 1973 lose the self-authorized stamps; declared roles and the retained catalogue proxies stay. Ordinary non-flat assays with no role are not new starting-charge rows. A `datum_state` point stores the located datum with no charge flag.
