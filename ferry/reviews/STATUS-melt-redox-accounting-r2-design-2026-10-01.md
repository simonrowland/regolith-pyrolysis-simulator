# STATUS — melt-redox-accounting design r2 DELIVERED (regolith-empirical)

`DESIGN-melt-redox-accounting-r2-regolith-physics-2026-10-01.md` delivered
(VPS ferry-inbox/from-empirical + Dropbox from-empirical).

Ack already on Dropbox / ferry: `STATUS-req-melt-redox-ferric-first-acked-2026-10-01.md`
(**left as-is**; not rewritten).

**Seat:** `slot-z14` @ green `696299350e98b67f786c334b4b4ccc8856149379` (design-only; no stack2 product commits).
**Sources folded:** REQ (7 sections); RULING d-058; alkali-couple DESIGN r1+r2; sol-review corrections; redox-design-r3 (+ owner-design-decisions / design-tail as needed); Q-A..Q-D / d-056 / d-057 / d-052.

**Mailbox tip:** TIP_PLACEHOLDER on `empirical/reviews-batch-zv-2026-09-22` (artifacts `888f11d782b4f66d5155a767aa85e33abe41333d`).

## What landed

- **ONE unified plan** for melt redox accounting (affinities / network modifiers included). Alkali couple is a **part of the shared-\(\mu_{\mathrm{O_2}}\) solve**, not a bolt-on M6 solver.
- **REQ §1 STATE:** element ledger only; oxidation states derived; lance/dose as delivery write + shared headspace — **no local eq volume**.
- **REQ §2 ONE solve:** shared oxygen (+ alkali) potential; M0–M5 shown as iron-only limit (equalities / bounds / absences); predict-and-flag + speciation key carry over.
- **r3 chunks:** 7–8 **stay valid / continue now (not wasted)**; 9 stays with shuttle-before-exchange ordering; 10–11 stay; 1–6 unchanged.
- **REQ §3 ONE activity model:** openimcc associate (Q-A FLAGGED) + **F1 Kress+Na₂O/K₂O ferric path** first; F2 ferric associates later; migration M-a..M-f.
- **REQ §4 EXCHANGE:** r3 film+integrator + **d-058 whole-melt**; SiO/process numbers **re-done** with \(p_{\mathrm{Na}}\) from solve (not declared 0.01).
- **REQ §5 INVARIANTS:** 1e-12 mol/tick closure; one writer; committed-interface; three flag categories.
- **REQ §6 EVIDENCE:** battery hooks for peralkaline \(a\), Fe³⁺/Fe²⁺+alkali, metal saturation, vapour, titration curve, d-058 \(p_{\mathrm{Na}}\) mutation.
- **REQ §7 PLAN:** U0–U12 after chunk 8; mare titration-curve acceptance; runnable d-058 mutation sketch (seat-verified).

## d-058 closure

| Item | Disposition |
|---|---|
| Titration / redox solve | **WHOLE-MELT** per tick |
| Separate lance-zone equilibrium volume | **CLOSED** (LZ-A/B/C fork from alkali-r2 §4 superseded) |
| \(p_{\mathrm{Na}}\) / \(p_{\mathrm{K}}\) | Equilibrium partials over whole melt from the solve; coupled to headspace/condenser via exchange; vapour loss = element-balance term |
| Validity | \(\tau_{\mathrm{mix}}\ll\Delta t\) and dose reacts within tick; flag `recipe_breaks_whole_melt_eq` |
| Worked numbers | §4.3 tables: M2 track with derived \(p_{\mathrm{Na}}\); pure-M6 with \(p\) capped at \(P_g\); declared-0.01 line superseded |

## Open questions **after** d-058 (narrow)

1. Headspace-cap policy when \(p_{\mathrm{Na}}^{\mathrm{eq}}>P_g\) (recommended default: flagged + exchange-limited interface).
2. F2 ferric-associate schedule (after Q-A battery cert).
3. Passive alkali film later (Q-D).
4. Mixing-time estimator geometry fields (conservative unmixed flag until present).

**No lance-zone LZ-A/B/C owner choice remains.**

## Delivery paths

- DESIGN: `/workspace/ferry-inbox/from-empirical/DESIGN-melt-redox-accounting-r2-regolith-physics-2026-10-01.md` (~35 KB)
- STATUS: this file
- Dropbox from-empirical: both basenames (CopyFromBox)
- Ack untouched: `STATUS-req-melt-redox-ferric-first-acked-2026-10-01.md`
- Slot-z14: idle after clear

— regolith-empirical, 2026-10-01 ~20:40 ET
