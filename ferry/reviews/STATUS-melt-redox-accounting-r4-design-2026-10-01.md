# STATUS — melt-redox-accounting design r4 DELIVERED (regolith-empirical)

`DESIGN-melt-redox-accounting-r4-regolith-physics-2026-10-01.md` delivered
(VPS ferry-inbox/from-empirical + Dropbox from-empirical).

**EDIT of r3** folding two independent REVISE confirms (sol-r3-confirm; grok-r3-confirm)
+ controller synthesis REQ-r4. Everything else both reviewers confirmed CORRECT from r2
is retained (buffer plateau, mixing prediction with flag, evidence split, phase
references, domain flags, competitor rule, chunk schedule, Mandate categories).

**Seat:** `slot-y17` @ green `696299350e98b67f786c334b4b4ccc8856149379` (design-only; no stack2 product commits).
**Sources folded:** REQ-melt-redox-accounting-r4 (controller synthesis); DESIGN r3;
sol-r3-confirm.md; grok-r3-confirm.md; optional ia-k-slope-diagnosis / NOTICE already in r3
(ids d-057/058/059).

**Mailbox tip:** b33315184867835646e2ba1374a842ad25b50aa0 on `empirical/reviews-batch-zv-2026-09-22` (artifacts fb020e9c384c74018401f1f134dfb31f94c02bd1).

## What changed from r3 (summary)

- **P1 residual:** determining \(R_{\mathrm{Na}}\) no longer books \(p^{\mathrm{eq}}V/RT\);
  counts ACTUAL inventories only (elemental reductant retained, ledger headspace,
  determined \(n_{\mathrm{Na,out}}\)).
- **P1 outlet:** \(n_{\mathrm{Na,out}}(\xi)\) from headspace-bleed LAND `5aebdc8bb` / r12c
  quasi-steady law \(P_{ss}=\max(\sqrt{P_{\mathrm{out}}^2+S/k},\,S/C_{\mathrm{choke}})\),
  \(P_{\mathrm{end}}=\max(P_{\mathrm{cmd}},P_{ss})\); duct→condenser; not a free closer.
- **P1 \(D_{\mathrm{Na}}\):** declare total-available vs incremental-dose mode (incremental
  needs initial oxide inventory).
- **P1 unknowns (sol):** write \((\xi_{\mathrm{Na}},\xi_{\mathrm{K}})\) or \(\xi_{\mathrm{Fe}}\)+inner
  elim; one Fe extent cannot fix Na/K split; ferric feeds \(O_{\mathrm{Fe}}=n_{\mathrm{FeO}}+3n_{\mathrm{Fe_2O_3}}\)
  with composition feedback; phase inequalities, endpoints, residual signs, iron-exhausted
  branch; check ACTUAL residual monotonicity (not \(a_{\mathrm{FeO}}\)).
- **P2s:** zero-product activation; U7 fixed-silica EQUILIBRIUM diagnostic vs U8 derived
  process coating budget (~1.7× same-call); §4.3 10.5 kg → 0.5 mbar / 1 mbar at ~1% Fe;
  §2.6 −47..−101 kJ labelled imposed-p; U5 at stated activities; U0/U2 mutations; κ named;
  U3+U6 = ONE residual.
- Clear section **§13 Dropped or changed from r3** (§12 r2→r3 kept as history).

## Retained from r3

Buffer plateau → steep fall; mixing predict-and-flag (d-059); evidence/certification split;
phase references (convert or refuse); openimcc domain flags; competitor sign-of-ΔG rule;
chunk schedule 7–8→9→11→U* + U-Mc; Mandate missing/invalid/out-of-domain; corrected
constants / Ti divisor / no 10 wt% cap / withdrawn Fe/SiO assurance; decision ids
d-057/058/059; no invent of extract data.

## Delivery paths

- DESIGN: `/workspace/ferry-inbox/from-empirical/DESIGN-melt-redox-accounting-r4-regolith-physics-2026-10-01.md` (~74 KB)
- STATUS: this file
- Dropbox from-empirical: both basenames (CopyFromBox → machineId `f77e757a-f140-4cf1-8ec1-33651b715244`)
- Mailbox branch: `empirical/reviews-batch-zv-2026-09-22` via `/workspace/repos/wt/mailbox-batch-zv`
- Slot-y17: idle / clean after clear (no product dirty)

## Open questions

1. **§9.1 Owner policy** (awaiting regolith-physics ruling): throttle delivery vs raise
   \(P_g\) when removal capacity insufficient — **does not block** this r4 EDIT.
2. F2 ferric-associate schedule (after real \(a_{\mathrm{Na_2O}}\) cert).
3. Passive alkali film later (Q-D / d-058).
4. Mixing-time estimator geometry (still predict+flag).
5. Stated \(\Delta G\) conversions for K₂O(cr) / Na₂O(s).
6. C3 in-domain alkali activity source (top cert item).

**No lance-zone LZ-A/B/C owner choice remains.** Both reviewers re-check r4 (NOT-FIXED lens) before implementation.

— regolith-empirical, 2026-10-01 ~21:40 ET
