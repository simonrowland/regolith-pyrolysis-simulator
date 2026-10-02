# REVIEW — DELTA: ia-battery-wiring bounce (flattering vapour-authority default)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `5e5b5ded58f43b644ce9b2d51ca8dd834811993f` on `review/ia-battery-wiring-bounce` (detached seat mirroring `origin/review/ia-battery-wiring`)
- **Parent / prior LAND:** `76b55001909ecdebc1a335ac6d45337870448075`
- **Range:** `76b55001909ecdebc1a335ac6d45337870448075..5e5b5ded58f43b644ce9b2d51ca8dd834811993f` (one commit)
  - `5e5b5ded58f43b644ce9b2d51ca8dd834811993f` Preserve missing vapor authority status
- **REQ:** `/workspace/ferry-inbox/REQ-ia-bounce-delta-from-regolith-physics-2026-10-02.md`
- **Prior REVIEW:** `REVIEW-ia-battery-wiring-regolith-physics-2026-10-01.md` (LAND `76b550019…`)
- **Date:** 2026-10-02 ~10:49–10:53 ET
- **Mode:** read-only for product code; extracts not edited; no force-push. Targeted VPS unit tests only (no full W3 / full-store Plante re-score).

## Scope

Train bounced prior LAND on `tests/chemistry/test_flattering_default_guard.py`: `str(vapor_authority.get("status") or "authoritative")` manufactured confidence from silence. Fix (`binary_pot_battery.py` +23/−8; `tests/test_binary_pot_battery.py` +53): missing vapour-authority status → `"unknown"`; backend status is `builtin_authoritative` only when the core said authoritative; `authoritative_for_requested_vapor_pressure` / authority fields follow.

Files: `simulator/diagnostic_helpers/binary_pot_battery.py`, `tests/test_binary_pot_battery.py`. Guard register file untouched.

## Attack results

### (1) No other affirmative default remains in the IA adapter — **PASS**

`git diff 76b550019..5e5b5ded` `rg`:
- `or "authoritative"` — **removed** (only the deleted line)
- `or True` — **none**
- `builtin_authoritative` — **conditional only**: `"builtin_authoritative" if authority_is_authoritative else authority_status` (and a test fixture label `builtin_authoritative:test`)

IA adapter body at tip: no `or "authoritative"`, no `or True`, no unconditional `authoritative_for_requested_vapor_pressure=True`. Remaining unconditional `authoritative_for_requested_vapor_pressure=True` hits live in the **openimcc** adapter (~2103/2122) — outside this bounce scope.

### (2) Downstream scorer does not treat `"unknown"` as certified — **PASS**

New unit test `test_missing_internal_vapor_authority_status_stays_unknown_for_scoring` (1 passed): empty `vapor_pressure_authority` → provenance/backend `"unknown"`, `authoritative_for_requested_vapor_pressure is False`, `engine_flags_from_result` authority `"unknown"`, and `cell_score_authority(…, refused=False) != "certified"`. `cell_score_authority` returns the engine string when set; `"unknown"` is not remapped to certified.

### (3) No change to the flattering-default guard's register — **PASS**

`git diff 76b550019..5e5b5ded -- tests/chemistry/test_flattering_default_guard.py` → **0 bytes**. Guard suite **307 passed** at tip (register identity intact; tip clears the site the train bounced).

### (4) Like-for-like Plante numbers unchanged — **PASS (by construction; no full re-score)**

When core supplies `status == "authoritative"` and `core_flags` empty, tip emits the same triple as prior LAND: `vapor_pressure_backend_status="builtin_authoritative"`, `authoritative_for_requested_vapor_pressure=True`, `authority="bridge"`. Behavioral change is only the missing/non-mapping status path (`→ "unknown"` / False). Plante rows that already carried core status are unaffected. Full-store Plante re-score **not** re-run on 16GB VPS (same policy as prior LAND review).

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/chemistry/test_flattering_default_guard.py` | **307 passed** (~6 s with siblings) |
| `tests/test_binary_pot_battery.py` | **31 passed**, **1 failed** (`test_openimcc_cell_round_trips_through_harness` — `OpenImccUnavailableError`; openimcc not provisioned on VPS) |
| `tests/test_binary_pot_battery.py::test_missing_internal_vapor_authority_status_stays_unknown_for_scoring` | **1 passed** |
| `tests/battery/test_internal_analytical_battery_engine.py` | **4 passed** |
| `tests/battery/test_score.py` (full file) | **122 passed**, **1 failed** (`test_allibert_solid_activity_fusion_conversion_is_diagnostic_only` — `ModuleNotFoundError: openimcc`; VPS env) |

Not run (policy/cost): full W3, full-store Plante/`score_store`, openimcc live suite, Mac Studio green gate.

**Env note (non-blocking):** both failures are openimcc-absent on this 16GB VPS seat — pre-existing provisioning gap, **not tip-introduced**. Bounce touches only the IA vapour-authority adapter + one unit test; allibert / openimcc harness paths are untouched by `5e5b5ded`.

## Verdict

**LAND `5e5b5ded58f43b644ce9b2d51ca8dd834811993f`**

Silence no longer manufactures authoritative confidence; `builtin_authoritative` is gated on the core status; scorer path rejects `"unknown"` as certified; guard register unchanged; Plante-authoritative path byte-equivalent to prior LAND.

— regolith-empirical
