# REVIEW — p_partial identity: reaction / reference_state / reservoir relaxed

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z14` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `632c41a5460ad4b40abb857c8b1b36d43b83f2fd` (parent / green `696299350e98b67f786c334b4b4ccc8856149379`)
- **Branch:** `review/pp-identity`
- **Commit:** `Relax p_partial identity axes` — only
  `simulator/battery/identity.py` (+24/−7), `tests/battery/test_score.py` (+63)
- **Date:** 2026-10-01 ~21:55 ET
- **Mode:** read-only on tip; no extract edits; review tip not pushed to green
- **REQ:** `REQ-review-pp-identity-632c41a5-2026-10-01.md` — optional/un-compared reaction, reference_state, reservoir on p_partial

## Tip shape

| check | result |
| --- | --- |
| HEAD | `632c41a5460ad4b40abb857c8b1b36d43b83f2fd` |
| Parent | `696299350e98b67f786c334b4b4ccc8856149379` (exactly one commit; green is ancestor) |
| Files vs parent | **2** claimed files only (`+80 / −7`) |
| Diff scope | `_P_PARTIAL_UNCOMPARED_AXES`; P_PARTIAL `profile_for` drops three required axes; `validate_quantity_profile` retains typed metadata without equality keys; four new unit tests |

**PASS.**

## Attack (1) — consumption claim (OpenIMCC / alphaMELTS / thermoengine)

Independent of the worker write-up: traced request construction for vapour / p_partial scoring.

### Scorer dispatch (`predict_with_engine`)

| field | p_partial path |
| --- | --- |
| `identity.reaction` | **never read** in `predict_with_engine` |
| `identity.reference_state` | read **only** under `Quantity.ACTIVITY_COEFFICIENT` (standard-state match vs engine detail) — not on p_partial |
| `identity.reservoir` | not read directly; `stated_pure_substance_reservoir(identity)` is the **fallback** only when composition is absent and quantity ∈ `_VAPOUR_EQUILIBRIUM` |

Composition branch runs **before** that reservoir fallback. Tip `profile_for(P_PARTIAL)` still **requires** `composition` (plus T, fO2, total_pressure). Missing composition → `identity_equal` / `IDENTITY_UNKNOWN` on `composition` (unit test + probe). Well-gated p_partial rows therefore never reach the pure-reservoir pot synthesis.

### Engine / bridge call shapes

| surface | inputs used for vapour equilibration |
| --- | --- |
| `openimcc_bridge.evaluate` | `composition_mol`/`composition_kg`, `temperature_K`, pack flags — no reaction / reference_state / reservoir |
| `identity_battery_pot` | `pot_id`, `composition_wt_pct` |
| `equilibrate_cell` | `handle`, `pot`, `temperature_K`, `po2`, `physical_pressure_bar` — no identity axes |
| OpenIMCC gas channels | `_default_reactions(parent_oxides, datapack)` from the pack — **not** `identity.reaction` |
| alphaMELTS / thermoengine adapters | no `identity.reaction` / `.reference_state` / `.reservoir` reads on the battery pot path (rg over `simulator/melt_backend/{alphamelts,thermoengine}.py` + `engines/alphamelts/thermoengine.py`) |

**PASS.** None of the three fields is a prediction input for p_partial when composition is present (required). No REVISE trigger.

## Attack (2) — comparison semantics (collision)

With the three axes neither `required` nor `permitted_not_applicable`, `identity_equal` skips them (`_comparison_profile` + loop `continue`). Typed vs unknown metadata on those axes are EQUAL (probe: factory-typed Plante-like reaction/reservoir/reference_state vs all-unknown → `equal`).

Where identities are used:

| consumer | key / behaviour | reaction / reservoir / reference_state? |
| --- | --- | --- |
| `residual_key` | `{observation_id}:T=…::{quantity}::{rail}::{engine}` | **no** — per-observation lineage |
| pin failures | keyed by `residual.key` | **no** |
| `_kems_replicate_groups` | `(experiment_id, species.formula, T, composition.components)` | **no** (never included) |
| ref vs engine candidate | `identity_equal` after `candidate_observation` (candidate carries prediction identity, usually cloned from reference) | uncompared axes cannot refuse the match |
| empirical dedup / residual membership | by `observation_id` sets | **no** identity collapse |

So two observations that differ **only** in reaction/reservoir/reference_state metadata:

1. Would already share the same replicate-group key and the same prediction pot (T, composition, fO2, P) — treating them as the same identity for comparison is correct for mixture p_partial.
2. Still keep **distinct** residual/pin slots via `observation_id`.

They cannot collide into one residual key, overwrite a pin, or silently merge distinct observation IDs.

**PASS.**

## Attack (3) — other quantities unchanged

`git diff 696299350..632c41a54 -- identity.py` only touches the P_PARTIAL branch + `validate_quantity_profile`’s p_partial uncompared set + comments.

Live profiles on tip:

| quantity | still requires |
| --- | --- |
| `P_SAT` | `reservoir`, `temperature_K` |
| `P_REFERENCE` | `reaction`, `reference_state`, `reservoir`, `fO2_Pa`, `temperature_K` |
| melt `ACTIVITY` | `reference_state`, `composition`, `temperature_K`, `per` (+ conditional fO2 / P) |

`_reservoir_rule` remains **P_SAT-only**. delta_fG / formation profiles untouched.

**PASS.** Relaxation is strictly limited to p_partial.

## Attack (4) — NOT-FIXED lens (large residuals / artefact?)

Controller’s OpenIMCC tip medians (metals −2 to −3 dex, O +0.3 to +1.1, SiO2(g) +0.14) are **physics / model** findings once identity_unknown on omitted metadata stops blocking Stolyarova/Shornikov rows. This tip does **not**:

- invent extract reaction / reservoir / reference_state
- change OpenIMCC pack reactions or oxygen-balance effusion logic
- relax composition / T / fO2 / total_pressure requirements

Could a wrong reservoir be silently assumed? **No for gated p_partial:** composition is required and supplies the pot; reservoir is unused when composition is a value. Engine gas stoichiometry comes from the datapack, not `identity.reaction`. Plante’s typed metadata remains valid and un-compared (profile permits VALUE without making it an equality key) — median / match counts unchanged per controller.

**PASS** (residuals are not an artefact of this relaxation).

## Targeted tests (VPS, scoped)

```
.venv/bin/python -m pytest tests/battery/test_score.py \
  -k 'p_partial_optional or p_partial_without_composition or condensed_activity' \
  -o addopts='' -q
```

→ **5 passed** (no full W3).

## Verdict

All four REQ checks PASS. No P0/P1/P2 defects found.

**VERDICT: LAND 632c41a5460ad4b40abb857c8b1b36d43b83f2fd**

— regolith-empirical
