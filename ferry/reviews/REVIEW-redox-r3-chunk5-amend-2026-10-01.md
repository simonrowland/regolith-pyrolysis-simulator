# REVIEW — AMEND redox design r3 Chunk 5 (speciation key)

- **Reviewer:** regolith-empirical (frontier of record; fold into r3 confirmation)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Code tip (context only; no chunk-5 commit yet):** `6ce76a9693b85924633af517475b75277beb6556` on `empirical/review-stack2-redox-c4-c3b` (tracks `origin/review/stack2-redox`)
- **AMEND:** `/workspace/ferry-inbox/AMEND-redox-r3-chunk5-from-regolith-physics-2026-10-01.md`
- **Design:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/redox-design-r3.md` (option B default §7 Q1; Chunk 5 L860; G2 L784; consumer contract L754–774)
- **Prior:** r3 confirm **CONVERGED** (`REVIEW-redox-design-r3-confirm-2026-09-30.md`); chunk 4 **LAND**; chunk 3b **REVISE** (diagnostic≠committed — physics says chunk 3c in flight)
- **Date:** 2026-10-01 ~03:12–03:40 ET
- **Mode:** design attack only; no extract invention; no Mac listen pools; no product-code edits; no full W3. Targeted Kress residual arithmetic on tip `fe_redox.py` only.

## Scope

Attack the controller AMEND to Chunk 5: equality stays absent for M1/M3/M4; a flagged **speciation key** accessor serves listed Fe3+/Fe2+ consumers; release rails / exchange / native-Fe / respeciation never read the key. Fold findings into the r3 confirmation record (option B storage preserved; consumer refusal softened under predict-and-flag).

## Verdict

**VERDICT: MINOR**

The AMEND is a coherent, owner-aligned extension of option B (not a flip to A). Equality remains absent; G2 stays closed if the accessor partition is enforced. Proceed with implementation. Two P2 notes must land in acceptance / consumer contract text (M3 sentinel scope; r3 L860 gate acceptance rewrite). No P1 that rejects the ruling.

---

## Coherence with design r3 / prior reviews

| Anchor | Relation to AMEND |
| --- | --- |
| Option B default (r3 L1052, Chunk 5 L860) | **Preserved storage:** bound is flagged lower bound, equality absent, native-Fe extent 0, zero-commit SiO = gas. AMEND does **not** store the bound as equality (that would be A, L1051). |
| Prior CONVERGED r3 confirm | Chunks 1–5 coherent; Chunk 5 “stop?” = No. AMEND does not retarget 1–4. **Fold:** Chunk 5 acceptance no longer requires liquidus/PT-0 to refuse with `none:fe_saturation_bound` for the listed consumers — they read the flagged key instead. Storage / native-Fe / SiO pins unchanged. |
| Chunk 4 LAND @ `04f9f7f93` | M3/M4 storage left for Chunk 5; mole predicates stand. AMEND does not reopen G1/G3. |
| Chunk 3b REVISE / 3c in flight | Orthogonal (diagnostic≠committed interface on ferrous-free release). Do not conflate with Chunk 5 AMEND. |
| Owner predict-and-flag | Lunar hero hour 60 M4 abort under pure-B consumer refusal is exactly the failure mode the standing ruling forbids. AMEND restores progress without inventing equality. |

Tip evidence that Chunk 5 is still needed: M4 today still returns the bound scalar through `_finite_oxygen_reservoir_fO2_log` and `_melt_fO2_from_ledger` (`core.py` ~7115–7153) with `basis='fe_saturation_bound'`, and `_ferrous_free_scalar_absent` (~6639–6642) does **not** include that basis — so tip still embodies **G2** for M4. Chunk 5 + AMEND must flip equality to `None` and expose the bound only via the new flagged accessor.

---

## Attack 1 — Is the M4 bound a defensible speciation key?

**Yes, as a flagged key (authority `bound`), not as a write target or equality.**

### Residual: Kress q at the bound vs ledger 0

At tip `simulator/fe_redox.py`, mare-like low-Ti wt% (`FeO` 18, other lunar oxides; `melt_mol_fractions_for_kress91` → `X_FeOt≈0.160`), pressure `1e-3` bar:

| T (°C) | a_FeO proxy | IW (log10 bar) | Bound = IW+2log10(a) | q_Kress(bound) | Implied n_Fe2O3 / n_Fe |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1220 | 0.160 (X_FeOt) | −11.477 | −13.070 | **1.48×10⁻²** | 7.4×10⁻³ |
| 1220 | 0.30 | −11.477 | −12.523 | **1.88×10⁻²** | 9.4×10⁻³ |
| 1300 | 0.160 | −10.655 | −12.248 | **1.42×10⁻²** | 7.1×10⁻³ |
| 1300 | 0.30 | −10.655 | −11.701 | **1.81×10⁻²** | 9.1×10⁻³ |
| 1400 | 0.160 | −9.741 | −11.334 | **1.38×10⁻²** | 6.9×10⁻³ |
| 1400 | 0.30 | −9.741 | −10.787 | **1.76×10⁻²** | 8.8×10⁻³ |

So at hero temperatures the Kress ferric fraction at the Fe–FeO saturation bound is **~1.4–1.9%**, not ε. Ledger M4 has q=0. That residual is **material for respeciation** (must never be written — r3 R-a L7, Chunk 5 native extent 0 / no Fe₂O₃ mint from the key). It is the known M4 model-limit gap under option B’s no-nucleation / ferric-absent resting state (r3 L841, L1052).

### Does it move the liquidus materially?

Liquidus/freeze-gate use fO₂ as a **curve cache key**, not as a composition rewrite (r3 L766; tip `_freeze_gate_redox_key_fO2_log` / `_freeze_gate_cache_key` in `evaporation.py`). Tip bins the key at `_FREEZE_GATE_FO2_LOG_QUANTUM = 1.0` dex and clamps to ±30 (`evaporation.py:396,2232–2241`). The composition the liquidus engine sees remains the ferric-free ledger; the key sits on the Fe–FeO edge (~IW−1 to IW−2 for realistic a_FeO).

Without Studio MAGEMin on this VPS, liquidus ΔT was not remeasured. Scale argument: literature Fe-redox liquidus swings of O(10) °C span much larger ferric changes than 0→~2%; freeze-gate hour steps and the 1-dex key quantum dominate. Residual-driven liquidus movement is expected **≲ a few °C**, far below the cost of aborting the 225 h hero. Flagging authority `bound` correctly marks the residual.

**Judgment:** Defensible speciation key under predict-and-flag. Pin: respeciation / exchange / native-Fe must not read it (AMEND already requires; tip `_fe_saturation_bound_fO2_log` docstring already says “not a metal tap”).

---

## Attack 2 — Does any listed consumer need a driving force rather than a key?

**No.**

| Consumer | What it needs | Tip / design evidence |
| --- | --- | --- |
| Liquidus + freeze gate + cache key | Discrete fO₂ key for curve identity | r3 L766; `evaporation.py` `_freeze_gate_redox_key_fO2_log` |
| PT-0 | Determinism cache key; refuse inventing 0/−9 | r3 L773; `reduced_real_determinism.py:276–291` |
| Melt/vapour activity resolver | State fO₂ for Fe-sensitive activities (not film flux) | `equilibrium.py:484–558` uses intrinsic for dissociation / a_FeO diagnostic; surface release already on `interface_pO2_bar` (L480–482) |
| Equilibrium redox input | Same state channel; not oxygen-transfer residual | Design L764–765 prohibit melt equality / bound as SiO pressure |
| SulfSat / phase context | State fO₂ for solubility / liquidus-tolerant paths | r3 L774; phase-context path today refuses M3 absence (`core.py:2571–2585`) |

Oxygen **driving force** (gas vs melt equality, film law, amount caps) is owned by exchange. AMEND correctly excludes release rails (SiO, Na, K, Fe, Mg), exchange, native-Fe, and respeciation from the key, and requires a test pinning SiO to `interface_pO2_bar` in M4 — that is the G2/G8 partition.

---

## Attack 3 — Is M3’s clamped edge sane?

**Sane as a liquidus/PT-0 sentinel after the existing freeze-gate clamp; not a physical pressure for activity models.**

M3 tip path (`core.py` ~7155–7204): equality `None`; one-sided mole-log edge
`log10 fO₂ = (ln(n_Fe₂O₃/NOOP) − b(T,P,X)) / (0.196 ln 10)` with `NOOP=1e-15`.

Independent evaluation on tip (fully ferric mare-ish Fe₂O₃ inventory, FeO=0):

| T (°C) | n_Fe₂O₃ (mol) | Raw edge log10 fO₂ | pO₂ (bar) |
| --- | ---: | ---: | ---: |
| 1220 | 1.0 | ~74.2 | ~10⁷⁴ |
| 1220 | 1e−12 | ~13.0 | ~10¹³ |
| 1400 | 1.0 | ~76.1 | ~10⁷⁶ |

Raw edge is **not** a physical furnace fO₂. AMEND’s “through the existing liquidus clamp” maps it via `_freeze_gate_liquidus_fO2_log` into **±30** (`evaporation.py:396`) — a stable extreme-oxidized cache bin, consistent with “one-sided lower bound, not an equality” (tip reason text ~7193–7201; r3 M3 row L640).

**P2:** Consumer contract must say explicitly that the M3 speciation key **after clamp** is for liquidus / freeze-gate / PT-0 identity only. Melt/vapour **a_FeO** on M3 should remain today’s unavailable path (`equilibrium.py:553–558`); do not evaluate Kress/Calphad activities or SulfSat at the +30 sentinel as if it were measured pO₂. M1 (gas pO₂, speciation moot) is fine as stated.

---

## Attack 4 — Does this reopen G2?

**Not if implemented as written; it is the fix for G2 on the reduced end.**

G2 (r3 L784): saturation bound stored as equality; SiO / liquidus / native-Fe read it as melt pressure; ~10⁴ SiO amplification from a pressure no transfer produced.

AMEND closes G2 by construction:

1. Equality reader returns `None` on M1/M3/M4 (never −9, never the bound) — matches r3 invariant L738.
2. Speciation key is a **separate** flagged accessor with authority `bound` — not `_current_melt_redox_fO2_log` equality.
3. Release rails + exchange + native-Fe (extent 0 in M4) + respeciation **never** read the key.
4. Explicit test: SiO tracks interface pO₂ in M4.

**Reopen risks (acceptance pins, not design rejects):**

- Wiring the key into `_current_melt_redox_fO2_log` or returning the bound from `_melt_fO2_from_ledger` (today’s tip M4 path ~7153) — would restore G2.
- Letting SiO / Na / K / Fe / Mg vapour fall through to the key when intrinsic is absent.
- Treating authority `bound` as numeric equality in optimizer scores / reports (r3 L771–772).

Required discriminating acceptance (extends r3 L860):

| Check | Expect |
| --- | --- |
| M4 equality reader | `None` |
| M4 speciation key | bound within 1e−6 of Fe–FeO fixed-point identity; authority `bound` |
| M4 native-Fe extent | 0 without reading the key |
| M4 zero-commit SiO / release | `interface_pO2_bar` = gas; **not** 10^(bound) |
| Mutation | restore bound-as-equality return at M4 → SiO or liquidus key must fail the pin |

---

## P1 / P2

### P1
- None. Ruling accepted.

### P2
1. **M3 key scope:** Document that the post-clamp M3 edge is a liquidus/PT-0 sentinel (±30), not a physical activity/SulfSat pressure; keep a_FeO-unavailable behavior on M3.
2. **Fold r3 L860 acceptance:** Replace “gate `none:fe_saturation_bound`” for the listed consumers with “equality absent + speciation key (authority `bound`) supplies the gate/cache key; release/SiO still interface-only.” Update the CONVERGED r3 confirmation Chunk 5 row accordingly when Chunk 5 lands.
3. **Residual invariant:** Acceptance or comment near the key accessor: Kress q(bound)−0 is O(10⁻²) at 1220–1400 °C; writing it is forbidden (R-a). Optional golden asserts respeciation Δn_Fe₂O₃ = 0 when only the key is consulted.

---

## Deliverables

- VPS: `/workspace/ferry-inbox/from-empirical/REVIEW-redox-r3-chunk5-amend-2026-10-01.md`
- VPS: `/workspace/ferry-inbox/from-empirical/STATUS-redox-r3-chunk5-amend-verdict-2026-10-01.md`
- Dropbox from-empirical: CopyFromBox attempted
- Mailbox batch: `ferry/reviews/` on `empirical/reviews-batch-zv-2026-09-22` (local commit; match prior redox mailbox pattern — **no origin push** of empirical branches)

**VERDICT: MINOR**

— regolith-empirical
