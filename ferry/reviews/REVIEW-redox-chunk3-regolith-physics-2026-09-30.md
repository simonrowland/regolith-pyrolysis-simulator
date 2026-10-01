# REVIEW — redox chunk 3 (vapour reads the committed interface)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `d43b027d106b96dc9c68f16072c8ba38f104e3ab` on `empirical/review-stack2-redox-c3` (tracks `review/stack2-redox`)
- **Range:** `2120e8ffa..d43b027d1` (two commits)
  - `301104e7c` Use committed oxygen interface for vapour
  - `d43b027d1` Keep uncommitted oxygen interface at transport pressure
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c3-from-regolith-physics-2026-09-30.md`; design r3 §6 chunk 3 “Vapour reads the committed interface” (~L856); gap G8 (~L796); E0–E5 publication rules (~L740)
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c3-report.md` (per-hour table)
- **Prior LAND:** chunk 1+2 on tip `2120e8ffa01b758252ee0e019f65f3783ec90aa9`
- **Date:** 2026-09-30 ~21:35–21:40 ET
- **Mode:** read-only for product code after mutations reverted; extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

G8: vapour must consume this hour’s committed exchange interface, not re-solve a fresh two-film root from the post-commit ledger. Zero commit publishes gas (`P_transport`); nonzero commit publishes the accepted trajectory endpoint. While no exchange is committed, writers keep the stored interface equal to the gas transport pressure (not the `1e-9` default).

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| `_interface_pO2_bar` returns stored field; no diagnostic re-entry | `core.py` ~6587–6598: returns `float(reservoir.interface_pO2_bar)`; G8 comment; no `_apply_oxygen_interface_diagnostic` / `_oxygen_interface_state` |
| Zero commit publishes gas | `core.py` ~10547–10551: `abs(transfer) ≤ NOOP` → `interface_pO2_bar = max(floor, headspace_transport_pO2_bar)` |
| Nonzero commit publishes accepted endpoint | `core.py` ~10552–10556: else → `finite_transfer['interface_pO2_bar']` (not a post-commit fresh root) |
| Post-commit diagnostic removed from publish path | `301104e7c` deleted the `_apply_oxygen_interface_diagnostic` that previously sat after gate refresh and *was* the nonzero publish |
| Uncommitted writers sync interface = transport | Construction ~8940–8949; pre-solve ~10157–10159; exchange ctor ~10233–10235; `OxygenReservoirState.__setattr__` (~520–530) re-syncs on transport / exchange writes while `\|exchange\| ≤ NOOP` |
| Nonzero endpoint survives later transport writes | Acceptance: after nonzero publish, `headspace_transport_pO2_bar *= 2` leaves endpoint unchanged |
| E0–E5 / consumer contract for SiO | Design L740 + §3: SiO reads `interface_pO2_bar`; every E regime; after precipitation still the committed interface — tip reader implements that |

## Acceptance (design L856)

| Check | Observed |
| --- | --- |
| Zero commit reads gas | `interface` / `_interface_pO2_bar()` == `headspace_transport_pO2_bar` |
| Nonzero commit reads stored endpoint | equals `shadow_oxygen_transfer['interface_pO2_bar']` |
| Neither read increments interface-root count | monkeypatch on `_oxygen_finite_interface_root`; `calls == before` |
| Uncommitted construction | `_refresh_oxygen_reservoir_without_exchange` → interface == transport |

## Targeted tests (VPS, `-o addopts=`)

| Test | Result |
| --- | --- |
| `test_zero_exchange_vapour_reads_gas_without_a_fresh_interface_root` | **passed** |
| `test_nonzero_exchange_vapour_reads_stored_endpoint_without_a_fresh_root` | **passed** |
| `test_uncommitted_reservoir_construction_publishes_transport_interface` | **passed** |
| `test_ferrous_free_sio_factor_uses_transport_pressure` | **passed** |

4 passed in ~7 s. No W3 / RH03 / full SiO-chain suite on VPS.

## Mutations (temporary local edits, both reverted)

1. **Restore diagnostic re-entry** on `_interface_pO2_bar` (pre-`301104` body).  
   - **Goes red:** zero-commit fixture — reader returns `~1.69e-4` instead of gas `1e-9` (left gas; would also increment root count if assertion reached). Exact G8 reproduction.
2. **Remove writer sync** (`d43b`): drop ctor/pre-solve/exchange `interface_pO2_bar=transport` writes and `OxygenReservoirState.__setattr__`.  
   - **Goes red:** `test_ferrous_free_sio_factor_uses_transport_pressure` — stored `1e-9` vs expected transport `1e-8`.

Both restored with `git checkout -- simulator/core.py simulator/state.py` (clean after; only `.slot-busy` untracked).

## Consequence attack — fixed-pO2 SiO 3.09× drop

Worker: total SiO `1.702e-5 → 5.513e-6 kg` (−67.6%); old fresh-root reader overstated SiO; `3.09² ≈ 9.54` cited vs `P_SiO ∝ 1/√pO₂`.

**Is the committed endpoint the right pressure for the hour’s SiO?** **Yes**, per design G8 / E0–E5 / §3 consumer contract. Alternatives fail on tip evidence:
- *Start-of-hour* would ignore the exchange this hour already accepted.
- *Post-commit fresh root* is exactly G8: an uncommitted second exchange from the post-commit ledger. `301104` comment and publish `else` branch reject that.

Within the hour, exchange commits then vapour reads; SiO must see the published interface of that commit, not a re-solved root.

**Is the 1/√pO₂ scaling argument sound given divergent histories?** **Locally yes; cumulatively only order-of-magnitude.** Per-hour table (c3-report):
- Hours 5–10: `d=0` both runs, identical pressures and SiO.
- Hour 11: same `d`, committed `1e-9` vs fresh root `1.215e-7` (121.5×); old SiO ≈11× lower ≈ `√121.5` — local scaling holds.
- Hours 12–23: `d` and interface trajectories diverge; new-run committed interface rises (~1.5e-9 → ~2.7e-8) while old-run stays nearer ~1–3e-9 after reading fresh roots, so later old SiO dominates the cumulative total.

The cumulative 3.09× is a path integral of those divergent transfer histories, not a single fixed pressure ratio whose square must equal 9.54. Directionally consistent with `1/√pO₂`; not a precise identity. Pin `PHASE3BIS_SIO_EVOLVED_KG = 1.702…e-05` needs **STUDIO-REGEN** (worker left assertion unchanged) — non-blocking for chunk-3 LAND; magnitude movement is the expected correct reading.

## RH03[24] notice hours `{21,24}` → `{22,24}`

Assertion `{21, 24} ≤ derived_hours` (active SiO rows with species transport notices). Chunk 3 changes the pO₂ SiO sees → can change which hours carry SiO flux / notices, so a one-hour shift is a plausible secondary consequence of reading the committed interface. This branch still carries the kilobar headspace bug (round 12; design G14: headspace inventory outside this plan). That failure is **pre-existing / out of scope** for chunk 3 alone; do not REVISE chunk 3 to retune headspace or RH03 pins. Pin update is STUDIO-REGEN / round-12 follow-up.

## Non-blocking notes

- Mid-solve `_apply_oxygen_interface_diagnostic` remains at `core.py` ~10366 (pre-commit path; fills regime/`k` diagnostics and temporarily writes interface). Final publish at ~10547–10556 overwrites with gas or endpoint before return. Not vapour-reader re-entry; leftover nesting cleanup for later chunks.
- Stale comment above the publish block still mentions “before evaluating its next interface root” — wording lag after `301104` removed that post-commit call.
- `test_uncommitted_reservoir_construction_publishes_transport_interface` alone is a weak discriminator when transport equals the `1e-9` default; `test_ferrous_free_sio_factor_uses_transport_pressure` (transport `1e-8`) is the sync mutation that actually goes red.
- Fixed-pO2 SiO magnitude pin and RH03[24] `{21,24}` pin await Studio regeneration — reported, not blocking LAND of the G8 reader/publish/sync change.

## Verdict

VERDICT: LAND d43b027d106b96dc9c68f16072c8ba38f104e3ab

— regolith-empirical
