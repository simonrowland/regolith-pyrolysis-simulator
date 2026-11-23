# hsc4b — frozen composition hour, and the hsc4a P2s

Seat `worktree/s-23`, branch `worktree/hsc4b-2315`. Base `b5b3724e6a`.
The canon walk was not run. Pytest was not run, local or remote.
MAGEMin and alphaMELTS were not live: `engines/engines.local.toml` is
present and gitignored, and no engine binary was started.

## Commits

- `3633b7b18b` — opening time scales, per-RHS flux span, and the P* bucket on the refusal and the published hour.
- `3f033026fe` — frozen-composition closed form.
- `4f2043d093` — subfloor solid stays on the stage book under the headspace flag.
- `311af5b16b` — warm root accepted only when the continuity rule selects it.
- `4a90e85516` — CrO3 buffer pinned to one evaporation debit.

## Why the warm start misses on the refused hour

The walk at `b5b3724e6a` commits hours 0–21 and refuses h22
(`headspace_balance_work_cap`, cap `flux`, path `radau_fallback`,
rejected steps 0). The payload is 250001 flux evaluations and 896
integrator evaluations, about 279 flux evaluations per RHS.
`buffer_o2_mol` 0.10518, `fo2_log` −3.699, pipe binding. The previous
hour published 20.000000 Pa. dt is 448.142 s at 1180 C.

A guarded rebuild uses that inventory. The same moles at 1170 C are
20 Pa. At 1453.15 K they open at 20.138586 Pa. The manifold root of
the constant source sized to the previous hour's composition time
(1.8443305209025127e8 s) is 20.0000000046 Pa.

Warm start from the opening pressure returns None. The root is
0.139 Pa away, outside the ±1e-4 Pa kink band. A guess of exactly
20 Pa returns None with 0 flux evaluations, because a guess at
downstream is the scan's job. A guess of 20.000450 Pa also returns
None: the band around it does not contain the root. A guess of
20.000100 Pa publishes 20.0000000046 Pa.

From the opening pressure, `_open_or_shut_fixed_point` publishes that
root on the geometric Brent bracket: warm 0, Brent 1, sign-scan 0.
On this monotone pipe a band miss restores that bracket. The sign-scan
counter stays 0. The walk's 279 flux evaluations per RHS are the
Radau residual, which this constant source never enters.

Re-measured after the fold, the same probes spend 16, 0, 29 and 17
flux evaluations. Piece samples no longer call the source, so these
counts sit below the earlier 19 / 0 / 45 / 24.

## Gate

`_FROZEN_COMPOSITION_MAX_DT_OVER_TAU = 1e-3`.

The gate is dt/tau on the manifold, tau = n / Σ s(P*), not the
off-manifold flush time n/Q(P_open). Freezing s_i(y) leaves a mole
error of order n (dt/tau)^2 when ds/dy is order s. At 1e-3 that
relative error is about 1e-6. The end check is still root movement
against 1e-6 Pa. The same cut bounds the fast pressure transient:
t_fast/dt at most 1e-3. An opening composition time already past the
gate returns before a second P* solve.

On the h22 rebuild the flush time is 6.153343 s and dt/flush is
72.829. The composition time is 1.8443305209025127e8 s and
dt/composition is 2.429836e-6, inside the gate.

## Flux, before and after

The walk's before number is the refusal: 250001 flux evaluations,
896 integrator evaluations.

Synthetic hour built from the h22 payload (one species, constant
source, T = 1453.15 K, V = 0.08538380762699734 m³, D = 0.12 m,
L = 1.0 m, P_down = 20 Pa, dt = 448.142 s):

| solve | path | flux | integrator | P* warm / Brent / scan | P |
| --- | --- | ---: | ---: | --- | --- |
| gate forced off, before the piece-sample change | `fast_root` | 230 | 0 | 0 / 0 / 0 | 20.00000000 Pa |
| frozen, before the piece-sample change | `frozen_composition` | 70 | 0 | 1 / 1 / 0 | 20.00000000 Pa |
| frozen, re-measured after the fold | `frozen_composition` | 54 | 0 | 1 / 1 / 0 | 20.00000000 Pa |

The 70 → 54 drop is piece probes no longer counting as flux. The
pressure is the same. Integrator evaluations stay 0, so the published
hour has no per-RHS span. The walk's min / median / max are what the
new payload fields will carry the next time a refusal fires.

Two earlier solves, before piece samples stopped counting:

- 11-species `_commit_many` on the same hour: `frozen_composition`,
  flux 56, integrator 0, P* 1 / 1 / 0, dt/tau 2.4466726499536287e-6,
  wall 0.010 s.
- 18-species Radau, quasi-steady fired: flux 369, integrator 233,
  per-RHS min 1, median 1, max 54, over 233 RHS. Opening flush
  dt/tau 72.829. That span is not the walk's uniform 279.

The continuation hour (buffer 0.105 mol, potassium rate depends on
the potassium fraction, dt = 3600 s, manifold dt/tau about 1.9) stays
off the frozen path. Direct call of
`test_continuation_hour_with_nonzero_buffer_stays_under_the_flux_cap`
passed in 0.735 s, under the 30000 user-callback budget, with the
quasi-steady notice.

## Frozen form

When the gate passes, P* is solved once at the opening fractions and
the holdup is projected onto it. The excess drains or fills over
t_fast. The remainder is

n_i(t) = n*_i e^{−t/tau} + s_i tau (1 − e^{−t/tau}),

booked by the existing closed manifold, so A, B, R and G are the same
expressions. P* is solved again at the end. A move past 1e-6 Pa falls
through to the reduced ODE or Radau. The hour is flagged
`quasi_steady_frozen_composition` and carries dt/tau.

t ≪ tau gives n_i0 + (s_i − y_i0 Q) t. t ≫ tau gives s_i tau.
Oxygen still moves only through `solved.redox_added_mol`.

## hsc4a P2s

**Subfloor mass.** The captured-rate clip, the stage projection, and
the purity report call `headspace_commit_drops_positive_mass`. Under
the headspace flag a positive mass at or under 1e-12 kg stays in q
and in `stage.collected_kg`. Production purity callers pass
`headspace_commit_posted_subfloor_share`. The flag is set when a
headspace remaining override runs and is not cleared on a later
ordinary hour.

Left in place: the re-solve trigger at `simulator/core.py:9088`
(`remaining > rate_floor_kg_hr`) and the gross filter at
`simulator/core.py:9291` (`float(rate) > rate_floor_kg_hr`). Those
decide whether a species is solved, not whether a captured share is
posted.

**Discontinuous root.** Newton will not cross a piece. A warm root is
the commit only when `_continuous_open_root` would select it. A
uniform piece inside the kink band accepts the warm root and does not
scan. A jump inside the band is split, each smooth side is Brent's
when the residual changes sign, and the continuity rule picks. A
different attractor is published, counted as Brent, and flagged
`headspace_balance_multiple_roots`. No reachable attractor raises
that refusal. Anything else falls through to the existing bracket.

The reviewer's probe is
`test_warm_start_publishes_the_continuous_root_across_a_jump`.
Guesses 20.000051 Pa and 20.000070 Pa, piece boundary 20.00005 Pa:
`_warm_started_open_root` still returns 20.000150 Pa, and the
publisher returns 20.000020 Pa with warm 0, Brent 1, and the notice.
Guess 20.000015 Pa agrees on 20.000020 Pa, warm 1, Brent 0, no notice.
Direct call passed. The existing warm-bracket test, the hour-cost
counters, and the continuation budget passed with it.

**CrO3 buffer.** `test_cro3_band_hour_oxygen_matches_the_integrated_captured_mass`
now reads `reservoir.fo2_buffer` after every route, headspace on and
headspace off. The expected mol is the seeded 1 mol plus
`mass_kg * O2_per_product_kg / M_O2` from
`sim._evaporation_stoich("CrO3", row)`. `O2_per_product_kg` is
negative. Direct call passed in 140.948 s. A second buffer debit
inside the booking fails that identity. The booking's oxygen remains
an overhead credit through `booked_overhead_oxygen_mol`.

## Copies

Piece samples no longer evaluate q. The three samplers are
`_HeadspaceLaw.piece_at`, `_mixture_piece`, and the mole-slice
`piece_at` inside `_solve_slice`. The piece tuple does not read the
source. `outflow_kg_s` (`capacity_coupling.py:1849`) still calls
`_flows` and discards the source. It returns the mass flow, so it
was left.

The frozen hour books through `_closed_manifold`. There is no second
exponential.

`headspace_commit_drops_positive_mass` is the one subfloor predicate.
Left, because they are different rules:

- `evaporation_parent_is_below_floor` and `subfloor_legs_kg` in
  `engines/builtin/evaporation_transition.py` (the transition's own
  floor; `condensation_route` already imports `condensation`, so the
  predicate was not pulled back into that provider).
- `committed_wall_deposit_fraction` zeros a dimensionless fraction.
- `vapor_kg > 1e-15` at `evaporation.py:7399` detects a reactive
  stage. It is not a product drop.
- Other 1e-12 kilogram comparisons in `evaporation.py` outside the
  stage-book clip. They were not the omission the review named.

The scan's `_solve_open_crossing` still publishes a geometric
crossing. `_sign_change_root` publishes one smooth interval and calls
`_brent`. They take different inputs, so they were not merged.

No copy was consolidated in the instrumentation commit. The frozen
commit removed `opening_s_mol_s` rather than leaving it beside the
manifold source.

## Direct calls

- `test_warm_start_publishes_the_continuous_root_across_a_jump` — 0.000 s
- `test_warm_started_open_root_is_the_bracket_root` — 0.001 s
- `test_hour_cost_records_opening_flush_time_and_rhs_span` — 0.002 s
- `test_continuation_hour_with_nonzero_buffer_stays_under_the_flux_cap` — 0.735 s
- `test_cro3_band_hour_oxygen_matches_the_integrated_captured_mass` — 140.948 s
- h22 synthetic probe — 0.00 s, path `frozen_composition`, flux 54

The closed-form tests against Radau, inside and outside the gate,
were run before `3f033026fe` and are unchanged since that commit.
