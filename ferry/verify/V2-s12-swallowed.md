# V2 — S12 swallowed failures

**Verifier:** adversarial re-check of `/workspace/ferry-inbox/sweeps/S12-swallowed-failures.md`  
**Code base:** `origin/review/r6-r8-fix` @ `1f8df6cbe` (worktree `/workspace/repos/wt/slot-05`)  
**Sweep tip cited:** `origin/work-v064-green` @ `fbe3491b2` — relevant sites match on review base (line numbers unchanged for the 8 P0 claims).  
**Mode:** READ-ONLY + minimal `python` probes (no `uv`/`pip`, no full suite).  
**Special bar (task):** P0 only if a swallowed failure puts a **wrong number into a live result today** (physics / ledger / deposition / redox / inventory), not merely a log/notice. Latent / diagnostic-only / status-bearing → demote.

## Verdict summary

| | Sweep claimed | Confirmed |
|---|---|---|
| **P0** | 8 | **0** |
| **P1** | 4 | **7** (5 demoted from claimed P0 + 2 confirmed claimed P1) |
| **False / overrated P0** | — | **3** hard FP/dead + **5** severity overrates (→P1) |
| Claimed P1 held | 4 | **2** held P1; **2** demoted →P2 |

## Claimed P0 table

| finding | exists? | live today? | result vs log | your sev | fix | shared-root |
|---|---|---|---|---|---|---|
| `wall_deposition.py:557-576` `_segment_wall_regime_factor` | **Yes** — `except Exception` → `model.regime_factor` or `1.0`. Probe: honest 100 mbar N₂ → `~0.0063`; `overhead_pressure_mbar=None` / bad carrier / non-numeric diameter → `1.0` (or model default). Caller `:105` feeds `wall_deposit_candidate_for_surface_kg` → condensation `wall_candidates` → `resolved_capture` (wall capture kg). | **Latent.** Healthy `CondensationModel` keeps numeric `overhead_pressure_mbar` (default `0.0`) and canonicalizes `carrier_gas` at set; Knudsen path succeeds. Swallow needs malformed attrs (None pressure, unsupported carrier string, bad diameter). | **Result** when hit (wall capture allocation), not log-only. | **P1** (not P0) | On Knudsen failure: refuse / status-bearing notice and skip wall candidate for that segment (do not invent `1.0`). | A |
| `thermoengine.py:1992-2001` `_oxide_mol` → `_fe_redox_split` | **Yes** — resolve/`molar_mass` under `except Exception: return 0.0`. Probe: patched resolve → FeO mol `0.0`; split keeps wt% keys and **omits** `Fe3Fet_Liq` when both Fe oxides fail. Healthy FeO/Fe2O3 resolve fine (`0.0718` / `0.1597` kg/mol). | **Latent.** Only FeO/Fe2O3 are passed; both resolve today. Needs registry/library fault injection. `fe_redox_split` on TE payload is largely export; core snapshot redox uses separate Kress path (`core._compute_fe_redox_split_diagnostic`). | Would corrupt TE payload redox/activities path if triggered; **not** a wrong live Fe3Fet number today. | **P1** | Re-raise / typed refusal when molar mass cannot be proven; never return measured `0.0` mol for positive wt%. | B |
| `thermoengine.py:1946-1967` `_endmember_moles_from_wt` | **Yes** — same swallow → `mol.append(0.0); continue`. Used only when component mole vector sums to ≤0 (`:1907-1908`), then feeds chem-potential activities → vapor projection. | **Latent.** Needs endmember name with wt>0 whose formula resolve raises. | Would change TE activity / vapor numbers if hit. | **P1** | Same as B: refuse the activity projection rather than zeroing the endmember. | B |
| `melt_backend/alphamelts.py:5540-5558` `_oxide_mole_fractions` | **Yes** as method body (drop + renormalize). | **No — dead.** Repo-wide: only the definition; no `self._oxide_mole_fractions(...)` callers. Benchmarks define a **separate** free function. | N/A (uncalled) | **FP / P3** | Delete or wire through a refusing helper if ever used. | B (if revived) |
| `stage0_inventory.py:706-717` `_species_mol` | **Yes** — formula present but `molar_mass_kg_per_mol()` raises → `0.0`. Probe: patched formula → `0.0` mol. | **Diagnostic-only live.** Module docstring: “instrument-first; no ledger writes.” Tests: diagnostic does not change campaign advancement; sibling JSON only with `--write-target-inventory`. | Changes **diagnostic** mol/kg rows (measured-looking zero), not ledger physics. | **P1** | Return typed unavailable / omit row with notice; never `0.0` mol for positive kg when mass call fails. | B |
| `stage0_inventory.py:500-507` `_species_kg_by_accounts` | **Yes** — both `project_account_kg` and `kg_by_account` fail → `continue` (account omitted). Probe: bad ledger drops `process.cleaned_melt`, keeps other accounts. | Same diagnostic channel as above. | Diagnostic map undercount, not ledger write. | **P1** | Surface account projection failure on the diagnostic record; do not silently omit. | C |
| `magemin/provider.py:445-451` `_ensure_backend` init-`pass` + mark initialised | **Pattern exists**, but **claimed trigger misread.** Constructor sets `_backend_initialised = (backend is not None)` (`:129`), so injected backends **skip** `initialize()` entirely. Cold path (`backend is None`) nulls on construct failure (`:457-459`). Forcing the flag + `FailInit` with `is_available=True` still hits `_run_backend` → `EquilibriumResult(status='not_converged')` or `unavailable` via `_backend_available`. Silicate intents are shadow; `GATE_LIQUID_FRACTION` is fallback-only with opt-in. | **No wrong authoritative chemistry number today.** | Status-bearing / shadow — not an unflagged success value on the live authority path. | **FP for P0** (keep as **P2** hygiene: don’t mark initialised after failed init if that branch is ever exercised) | On init failure: leave `_backend_initialised=False`, null backend or force `is_available=False`. | D |
| `vaporock/provider.py:329-336` same init-`pass` | Same constructor semantics (`:109`). `is_authoritative_for=frozenset()`; dispatch returns `non_authoritative` / raises `ProviderUnavailableError`. | **Shadow/diagnostic only.** | No authoritative result change. | **FP for P0** (**P3**) | Same as D. | D |

## Claimed P1 table

| finding | exists? | live? | your sev | fix | shared-root |
|---|---|---|---|---|---|
| `alphamelts/provider.py:428-438` `_composition_wt_pct` drop+renorm | **Yes.** Probe `{SiO2:1, BogusXYZ:0.5}` → `{SiO2: 100.0}`. Provider is diagnostic (`transition=None`, no ledger authority) but can pass domain gate on a silently leaner melt. | Live on diagnostic/shadow composition build when an unresolvable species appears alongside majors. | **P1** | Refuse / `out_of_domain` when any positive-mol species fails resolve; do not renormalize around a hole. | B |
| `magemin/provider.py:419-422` same | **Yes.** Same probe → `{SiO2: 100.0}`. Shadow (+ gate fallback only with opt-in). | Shadow composition path. | **P2** (demote; shadow parity, not live authority score) | Same refuse-on-unresolved-species. | B |
| `optimize/evaluate.py:5258-5261` `_composition_wt_pct_to_mol` | **Yes** — `continue` drops species; used for rump-terminal proof inputs (`:5221`). | Live optimizer proof path when wt% keys fail resolve. | **P1** | Fail the proof row / typed refusal instead of omitting mass. | B |
| `sulfsat.py:620-638` `_resolve_fe3fet` | **Yes** — returns `(0.0, warnings, False)`. Docstring: tagged `out_of_range`; **caller falls back to builtin** — Fe3Fet=0 only keeps PySulfSat call alive. | Flagged; SCSS from fabricated 0 does not become the honoured capacity. | **P2** (demote) | Prefer hard refuse without calling SCSS when derivation fails; drop the placeholder 0.0. | — |

## Evidence notes (probes)

- Wall Knudsen: `100 mbar N2` → regime_factor `0.006307…`; `None` pressure / `BogusGas` → `1.0`; `P=0` is honest Kn→∞ → `1.0` (not a swallow).
- `_oxide_mole_fractions` (MELTS support): `rg` only hits definition + unrelated benchmark free functions.
- MAGEMin/VapoRock: `MAGEMinShadowProvider(backend=FailInit())` starts with `_backend_initialised=True` (initialize never called on inject path).
- Stage0: `test_target_inventory_diagnostic_does_not_change_campaign_advancement`; sibling write is opt-in.

## Shared roots

- **A** — Knudsen/regime wall path: fail-closed segment candidate, not default `1.0`.
- **B** — Formula resolve at library boundaries: refuse / status-bearing, never zero-and-renormalize (covers TE mol helpers, stage0 mol, provider/optimize composition maps; dead MELTS helper if revived).
- **C** — Ledger projection failures on diagnostic inventory: surface error on the record.
- **D** — Shadow provider lazy init: don’t mark initialised after failed `initialize` (and don’t skip init semantics inconsistently).

## Counts for parent

- **Confirmed P0: 0**
- **Confirmed P1: 7** (wall regime; TE `_oxide_mol`; TE `_endmember_moles_from_wt`; stage0 `_species_mol`; stage0 `_species_kg_by_accounts`; AlphaMELTS `_composition_wt_pct`; optimize `_composition_wt_pct_to_mol`)
- **Claimed-P0 false positives / dead: 3** (MELTS `_oxide_mole_fractions` uncalled; MAGEMin init-pass trigger; VapoRock init-pass / non-authoritative)
- **Claimed-P1 demoted to P2: 2** (MAGEMin composition shadow; sulfsat Fe3Fet flagged+fallback)

TL;DR: None of the 8 claimed P0s put a wrong number into an authoritative live result today — 0 confirmed P0. Five are real latent/diagnostic contract breaks → P1; three are misread/dead/shadow. Two of four claimed P1s hold; two demote to P2.
READY: /workspace/ferry-inbox/verify/V2-s12-swallowed.md
