# bench-c3-generators: implementation evidence

## Baseline

Measured with `load_migrated_store(Path.cwd())` and `scripts.bench_readiness.report`.
211 works, 6,987 experiments, 91,460 observations; readiness aggregates 232 source IDs.

| Consumer | Ready | Partial | Gap | Not applicable |
|---|---:|---:|---:|---:|
| kems | 0 | 19 | 194 | 19 |
| rps | 0 | 11 | 221 | 0 |
| engine_point | 0 | 0 | 232 | 0 |

All 6,987 experiment temperatures unknown; 52 experiment pressures and 33 masses have values.
Point-condition counts: temperature_K 23,790; mass_kg 108; printed_composition 84;
composition 84; exposed_area_m2 50; total_pressure_Pa 16. Temperature on point-valued
observations: 20,898 across 21 source IDs. This scan includes both extracts-v2 and
observations-v2, including reference compilations, so differs from the brief's 666/14.
No store regeneration performed.

## Pending controller question

Wait ID `14f928ee235d498a89ba738673b46c5e`, posted through dispatch steer.
Existing KEMS validation requires purity, post-mass, calibration, uncertainty,
repeats and hold beyond DESIGN §5 waypoint requirements. RPS geometry requires
surface role, thermal profile, view factor, line of sight and provenance, beyond
exposed area. Current oxygen_condition may be an assumed uncontrolled label or
buffer/gas name, which cannot be converted silently to numerical fO2_log.
Asked whether to add the necessary evidence waypoints and align readiness with
actual generator requirements; also whether calculated point mass-to-moles must
remain DERIVED while printed operands remain PRINTED.

## Verification so far

- Canonical repository `.venv/bin/python` provides pytest + default plugins.
  Seat `.venv` lacks pytest; system Python lacks required xdist/timeout options.
- Existing waypoint suite + first six observation route cases: 52 passed,
  default addopts, 18 workers, 300-second timeout.
- `mutations/observation-routes.sh`: exit 0, six expected assertion failures,
  source restored by trap. Mutation removes observation route availability.
- No push. `uv.lock` untouched. Verified partial slice committed separately from
  the unresolved charge/generator work; commit SHA is supplied in the dispatch receipt.
- New observation-only temperature and pressure readiness fixture: seven focused
  cases pass with default addopts. KEMS/RPS do not become ready from point-only T.
- `mutations/engine-point-temperature.sh`: exit 0, intended assertion failed;
  source restored. Both scripts modify the same file and must run sequentially.
- Independent read-only review: no blocking finding in the scoped partial diff.
  Review identified missing observation-only pressure integration coverage; added
  that condition to the readiness test and reran all seven focused cases green.
- `tests/battery/` DEFAULT addopts: 2,760 passed, one failed in 395.43 seconds.
  Sole failure: `test_validate_corpus_zero_hard_issues_on_migrated_store`, the
  brief's known base failure. Assertion sees `referential_integrity` for the
  Okazaki 2022 fragmentation-assumption ancestry instead of `conditional_field`.

## Requirement status (partial patch, controller decision pending)

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Verify merged chunks and store premise | done | Bench/identity/schedule/scalar records, migrator, waypoints and readiness present; counts above |
| Observation thermal and pressure routes | partial | Implemented with PRINTED authority and observation-qualified inputs; seven focused cases pass |
| Observation charge route and 50-mass no-collapse regression | deferred | Pending resolution of PRINTED-versus-DERIVED authority conflict |
| Engine-point route preference | partial | Point thermal and pressure accepted explicitly; charge route and readiness CLI integration remain |
| Three waypoint-only generators and generation CLI | deferred | Actual consumer schema requirements exceed existing waypoint contracts; controller question pending |
| No defaults, bounds and provenance propagation | partial | Temperature/pressure preserve Value print form and observation reference; generators absent |
| Route guard mutation | done | `mutations/observation-routes.sh` passed; included in partial-slice commit |
| Engine-point guard mutation | done | `mutations/engine-point-temperature.sh` passed, including updated observation-only pressure fixture; included in partial-slice commit |
| Generator guards and mutation scripts | deferred | Generators not implemented |
| Battery default-addopts gate | done | 2,760 passed; sole failure exactly the brief's known base failure |
| Independent review | done | No blocking issue in scoped partial diff; suggested pressure integration coverage added and green |
| Before/after Part 1 readiness | partial | Before above; Part 1 incomplete, so no valid completed-Part-1 after result |
| Explicit-pathspec commit | partial | Verified temperature/pressure slice only; SHA supplied in dispatch receipt |
| No push, store regeneration or uv.lock edit | done | None performed |
