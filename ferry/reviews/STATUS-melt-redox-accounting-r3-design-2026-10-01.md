# STATUS — melt-redox-accounting design r3 DELIVERED (regolith-empirical)

`DESIGN-melt-redox-accounting-r3-regolith-physics-2026-10-01.md` delivered
(VPS ferry-inbox/from-empirical + Dropbox from-empirical).

**EDIT of r2** folding two independent UNSOUND reviews (sol; local grok) + controller
synthesis REQ + NOTICE decision-id renumber.

**Seat:** `slot-y17` @ green `696299350e98b67f786c334b4b4ccc8856149379` (design-only; no stack2 product commits).
**Sources folded:** REQ-melt-redox-accounting-r3 (controller synthesis); NOTICE-decision-ids-renumbered; DESIGN r2; sol-review-of-plan-r2; grok-review-of-plan-r2.

**Mailbox tip:** 6c705c3f5465d2fe6d2dd4e027e771da81acac54 on `empirical/reviews-batch-zv-2026-09-22` (artifacts c36f073a3c0444b550fac27d8fb1cd714cb7a903).

## What changed from r2 (summary)

- **Decision ids corrected throughout:** alkali couple = **d-057**; Q-A..Q-D = **d-058**; stirred whole-melt = **d-059**. Real **d-056** (regolith-main admission-default) unused.
- **Peralkaline step is not a stop;** remove interior \(\Delta G=0\) acceptance; extent runs until FeO / dose / headspace policy binds.
- **Element-balance residual;** extent is the M2 unknown; \(f\mathrm{O_2}\) and \(p_{\mathrm{Na}}\) are outputs; residual vector / brackets / endpoints / failures written.
- **Headspace split:** eq target vs inventory; film only writer of interface + condenser debit; \(\sum p_i\le P_g\); explicit condenser sink.
- **Fe–FeO disagreement resolved** as titration **plateau → steep fall near FeO exhaustion**; coating-budget framing (not preserved/lost).
- **Evidence/certification split:** kems-019/016 cannot score; Tsaplin/Yamaguchi Na₂O held; identity / synthetic / empirical layers; cert unavailable until admitted series.
- **Phase refs tightened** (K₂O(cr), Na₂O(s)<1405.2 K, Si/Mg solids, `ellingham("Fe")` 0.96 dex below liquid-IW).
- **Refusal taxonomy:** Mandate missing/invalid/out-of-domain + d-059 **predict-and-flag** for mixing (not withhold equality).
- **Competitors:** Si/Mg/TiO/Ti₃O₅ tied to sign of \(\Delta G\) with stated refs.
- **Schedule:** chunks 7–8 land first; chunk 9 before unified writer; chunk 11 before U2; **U-Mc** activity-map chunk added.
- **Numerics:** executable \(K_{\mathrm{Na}}\) pin; scale-aware closure (not 1e-12 absolute at large N).
- **openimcc [1700,3000] K domain gap at C3 ~1423 K** recorded as **top alkali-couple certification item** (predict+flag extrapolated).
- Clear section **§12 Dropped or changed from r2**.

## Retained from r2

Corrected constants (\(K_{\mathrm{Na}}\), \(K_{\mathrm{FeO}}\), \(K_\times\)); Ti divisor; dropped 10 wt% cap; withdrawn regime-label Fe/SiO assurance; whole-melt / lance-zone CLOSED; r3 chunks 7–8 continue (not wasted); F1 then F2 ferric path; no invent of extract data.

## Delivery paths

- DESIGN: `/workspace/ferry-inbox/from-empirical/DESIGN-melt-redox-accounting-r3-regolith-physics-2026-10-01.md` (~56 KB)
- STATUS: this file
- Dropbox from-empirical: both basenames (CopyFromBox → machineId `f77e757a-f140-4cf1-8ec1-33651b715244`)
- Mailbox branch: `empirical/reviews-batch-zv-2026-09-22` via `/workspace/repos/wt/mailbox-batch-zv`
- Slot-y17: idle / clean after clear (no product dirty)

## Open questions after d-059 (narrow)

1. Headspace/outlet policy when \(p^{\mathrm{eq}}>P_g\) or delivery exceeds removal (throttle vs raise \(P_g\) — owner policy).
2. F2 ferric-associate schedule (after real \(a_{\mathrm{Na_2O}}\) cert).
3. Passive alkali film later (Q-D / d-058).
4. Mixing-time estimator geometry (still predict+flag).
5. Stated \(\Delta G\) conversions for K₂O(cr) / Na₂O(s).
6. C3 in-domain alkali activity source (top cert item).

**No lance-zone LZ-A/B/C owner choice remains.** Both reviewers re-check r3 (NOT-FIXED lens) before implementation.

— regolith-empirical, 2026-10-01 ~21:05 ET
