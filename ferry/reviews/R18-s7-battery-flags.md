# R18 — S7 battery: carry engine extrapolation flags through scoring

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `472febdd359c1563890821a44591e3d00cf0b571` on `origin/review/s7-battery-flags` (NOT landed on green; parent `bba465959`)  
**Files:** `simulator/diagnostic_helpers/binary_pot_battery.py`, `simulator/diagnostic_helpers/binary_pot_scoring.py`, `simulator/battery/score.py`, `tests/test_battery_engine_flags.py`

**Intent:** Engine out-of-band notices (VapoRock commissioning / IMCC temperature extrapolation) are read once onto the battery `EquilibrateCell` (`authority`, `certified_band`, `notices`) and consumed by scoring instead of hard-coding envelope `authority='bridge'`. Predicted numeric values stay the engine's numbers. `imcc_gas_unavailable` stays its own kind (not remapped to out-of-band / not stamped extrapolated).

**Attack surface:** a value changed when the flag is attached; an in-band cell flagged extrapolated; a live flag still dropped between engine → cell → score / Observation notices.

**Method:** Static read of tip diff vs parent + live commissioning / IMCC emitters. Adversarial probes (flag on/off value equality, in-band T and SiO2 edges, IMCC hot/warm, gas-unavailable remapping, IMCC twin-list dedup, notice-without-authority gap, MELTS band projection). Pytest on tip worktree at `472febdd3`. Mode: **ran-tests**.

**Batch Y:** P0 only if a wrong number reaches result/score/ledger today; latent ≤ P1.

---

## Findings

### P3 — `cell_notices` drops `engine_commissioning` when the row omits `authority`

**Evidence (file:line on `472febdd3`):**

- `_row_out_of_certified_band` (`score.py:814–823`) treats a row as out-of-band only if:
  - `kind ∈ {melts_domain_gate, out_of_certified_band}`, or
  - `row['authority'] == 'extrapolated'`, or
  - `kind` starts with `imcc_` and contains `extrapolated` / `outside`.
- Bare `kind='engine_commissioning'` with no `authority` key → `_row_out_of_certified_band` is False → the row falls through every `elif` in `cell_notices` (`:872–934`) and is **omitted**.
- Probe: cell with `authority='extrapolated'`, `certified_band` set, notices=`[{kind: engine_commissioning, reason: temperature_range, ...}]` (no authority on row) → `cell_notices(...) == ()` while `_flags_from_scored_cell` still upgrades `Authority.EXTRAPOLATED` from `cell.authority`.

**Why P3 (latent):** Live VapoRock / commissioning emitters always put `authority` on the notice (`engines/engine_commissioning.py:397–406`; tip probe of hot VapoRock notice keys includes `authority`). Asymmetric trail only if a future emitter sets cell-level authority without stamping the row. Defense-in-depth; not live today.

**Fix direction:** optional `R18-fix.patch` — treat `engine_commissioning` like `melts_domain_gate` in `_row_out_of_certified_band`.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| A value changed | **Fail (safe).** VapoRock 1773 K pressures/activities equal with vs without `assess_engine_commissioning` (notice stripped). Equal to direct `backend.equilibrate` extract. IMCC 800 K activities/pressures equal to direct call. In-band vs out-of-band predicted numbers differ (T enters the model); attaching the flag does not move them. `authority_allowed` treats BRIDGE and EXTRAPOLATED the same for admission. |
| An in-band cell flagged | **Fail (safe).** VapoRock 1673.15 K / T=1700.0 K (inclusive band top): `authority is None`, no commissioning notice, `cell_notices` empty, `predict_with_engine` → `Authority.BRIDGE`. IMCC 1700 K: `authority is None`, only `imcc_gas_unavailable`; envelope `authority=='bridge'`; not stamped extrapolated. SiO2-out / T-out correctly flag. |
| A flag still dropped | **Fail (safe) on claimed live paths.** VapoRock commissioning → cell notices + `authority` + band → `cell_notices` → `OUT_OF_CERTIFIED_BAND` + `Authority.EXTRAPOLATED` + interval band on prediction/candidate. IMCC `imcc_temperature_extrapolated` survives once (twin-list dedup in `engine_flags_from_result`) into cell and `OUT_OF_CERTIFIED_BAND`. `imcc_gas_unavailable` kept as its own kind on scoring envelopes (`cell_score_notice_kinds`); deliberately **not** remapped to out-of-band; Observation `cell_notices` omits it (no `NoticeKind`; tip tests assert `cell_notices(warm)==()`). Parent hard-coded envelope `'bridge'` and never copied commissioning onto the cell — tip closes that. Residual latent gap: P3 above. |

---

## What looks sound

- Single read path `engine_flags_from_result` (`binary_pot_battery.py:866–910`): commissioning notice first, then diagnostics authority/band, then IMCC notices from diagnostics **or** attribute (not both) — fixes the prior double-append.
- `cell_score_authority` / `_envelope_notices` replace hard-coded `'bridge'` in `_cell_envelope` and `_comparator_envelope` (`binary_pot_scoring.py:1178–1293`).
- `_flags_from_scored_cell` upgrades BRIDGE→EXTRAPOLATED from the cell and projects interval-only bands via `_interval_certified_band` (drops MELTS citations / crash-floor scalars so Observation accepts the prediction).
- Gas-unavailable has `authority: None` on the IMCC emitter (`:1460–1465`) and does not match `_row_out_of_certified_band` — correct separation from temperature extrapolation.
- Qualification MELTS path still seeds `melts_domain_gate` + extrapolated on the cell; isolated worker merges notices without losing the gate.

### Residual notes (not severity)

- `predict_with_engine` still assigns `certified_band = None` on MELTS gate failure (`score.py:1132–1133`), then `_flags_from_scored_cell` fills the projected band from the cell. Harmless (intervals correct); the explicit `None` is now misleading.
- Diagnostics `authority` + `certified_band` without a notice list yields extrapolated authority and empty `cell_notices` (no live emitter does this).

---

## Tests

Tip worktree at `472febdd3`, repo `.venv` (scipy installed for IMCC on this box), `-o addopts=`:

- `tests/test_battery_engine_flags.py`: **4 passed**
- `tests/test_binary_pot_battery.py::test_qualification_cell_carries_gate_notice_and_extrapolated_authority`: **passed**
- Adversarial probes (value on/off, in-band edges, IMCC hot/warm/gas kind, dedup, notice-authority gap): **named attacks safe / P3 confirmed**

Optional fix: `ferry-inbox/reviews/R18-fix.patch` (`engine_commissioning` kind → out-of-band). Not applied to the review branch; do not push.

---

## Verdict rationale

Claim holds on the three named attacks against live emitters: numbers unchanged, in-band stays bridge, extrapolation flags reach cell and scoring (gas stays its own kind on the envelope). One latent defense-in-depth hole if `engine_commissioning` ever ships without a row-level `authority`. **LAND** — optional P3 patch only.

VERDICT: R18 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests
