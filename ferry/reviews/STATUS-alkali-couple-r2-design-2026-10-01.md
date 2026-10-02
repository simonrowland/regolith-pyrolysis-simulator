# STATUS — alkali-couple design r2 DELIVERED (regolith-empirical)

`DESIGN-alkali-couple-r2-regolith-physics-2026-10-01.md` delivered (VPS ferry-inbox/from-empirical + Dropbox from-empirical).
Ack already on Dropbox: `STATUS-req-alkali-couple-r2-acked-2026-10-01.md` (not rewritten).

**Seat:** `slot-z14` @ green `696299350e98b67f786c334b4b4ccc8856149379` (design-only; no stack2 product commits).
**Sources folded:** r1 DESIGN; sol-review (UNSOUND→corrected); AMEND titration; AMEND2 sites; RULING Q-A..Q-D; redox-design-r3; attached `metallothermic_step-e7bd0cd52.py`.
**Mailbox tip:** c087192bbe325b5d0e25347fd6a3462ab1d65337 on `empirical/reviews-batch-zv-2026-09-22` (artifacts `20512ad1e6d70844220c80192d42c99fcbc29a94`).

## Conclusions (short)
- **Corrections (seat-run):** Kelvin Ellingham → K_Na(1423.15 K)=1.515e-7; dissoc +185.8 kJ/mol; M6 fO2=2.295e-22 bar at a=1e-8, p=0.01; formal SiO amp 2.09e9; Na/Fe margin +11.1 kJ/mol O2. K_x=K_FeO/K_Na=4.810. Ti 4a xi_max=min(n_Na/2, n_TiO2/2). p_Na→0 ⇒ fO2→∞ at fixed Ka.
- **Fe/SiO assurance WITHDRAWN** (dose counterexample: FeO left 0.069 mol at shared fO2~1.8e-21; M2 equality moves ~7.4 dex). Replace with coupled shuttle/film + selective-flux (A8).
- **Simultaneous shared-mu_O2 titration** (not waterfall); polymerization via openimcc associates only; structural Al/Fe3+ sites + peralkaline step located on mare fixture (NK/Al=1 at +128.6 mol Na2O / ~5.91 kg Na); 10 wt% Na2O cap **dropped**.
- **Q-A..Q-D decided:** openimcc FLAGGED; ideal Ti2O3 FLAGGED+JANAF; joint Na/K; shuttle-first / zero-commit gas.
- **Owner fork still open:** lance-zone LZ-A (default, local p_Na) vs LZ-B whole-melt vs LZ-C delivery — numbers in DESIGN §4.

## Delivery paths
- DESIGN: `/workspace/ferry-inbox/from-empirical/DESIGN-alkali-couple-r2-regolith-physics-2026-10-01.md` (36175 bytes)
- STATUS: this file
- Dropbox from-empirical: both basenames
- Slot-z14: idle after clear

— regolith-empirical, 2026-10-01 ~20:10 ET
