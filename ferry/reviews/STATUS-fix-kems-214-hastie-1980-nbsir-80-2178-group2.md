# STATUS (group 2 of 5): fix kems-214-hastie-1980-nbsir-80-2178

**From:** regolith-empirical (fix seat, batch4 B)   **At:** 2026-10-05 ET
**Pushed tip after this group:** 7b6b928f2e57235d29863f1cf7d9a92b887c2b9e on hunt/kems-214-hastie-1980-nbsir-80-2178 (mirror). Parent 4103bbe5 (group 1). Not merged.
**Groups covered:** 1 (main report, PDF 11-31) and 2 (Appendix A apparatus/gas dynamics, PDF 33-56, t3). Remaining: 3 (NaCl, PDF 57-70, t4-t7), 4 (Na2SO4, PDF 71-85, t8), 5 (Appendix B, PDF 87-112, t9-t11).
**Items done:** 39 / 113 (group 1: 11; group 2: 28 = 23 page rows + 5 cross-cutting).

## Group 2 (pages read at 220 dpi: PDF 33-54, 56; PDF 55 is Figure 7, figure-only)
- Draft contexts **kept after verification**: PDF 33, 34, 35, 36, 39, 40, 42, 44, 46, 48, 51, 52, 53, 54, 56 (every number checked against the image).
- Draft contexts **corrected**: PDF 37 (chopper: 3-tooth wheel 40-270 Hz, 24-tooth for higher; draft generalised), PDF 38 (seal is 21 cm from the front of the boat carrier, not "from sample"), PDF 41 (draft assigned Figure 4 labels to "exit diameter 0.015 cm / channel 0.5 cm"; the schematic is not to scale and labels are now carried unassigned; Ta foil furnace and TC-nozzle negligible gradient added), PDF 43 (alinement statements), PDF 45 (draft silently "corrected" Wegener expansion time to 3x10^-3 s; the image prints 3 x 10^3 s, now carried as printed with a note), PDF 47 (k minor T/P variation), PDF 49 (gamma=7/5 expression used), PDF 50 (isothermal Pc/P0 = e^(-1/2) = 0.606 written out).
- A2 TMS channel length: draft's <= 0.00004 m bound for the conical nozzle (PDF 39 / 363) **kept** (verified).
- A3 sampler configurations: bench other_fact `sampler_configurations` distinguishes conical nozzle (0.006-0.009 cm, channel <= 0.004 cm, skimmer) from capillary (ratio 30, ~0.5 cm drilled depth, no skimmer); geometry fields stated to describe the conical nozzle only; `conical_nozzle_flow_regime` = viscous/continuum (Kn0 0.003 at 1 atm, 1000 K) scoped to that nozzle; stage I ~5x10^-7 atm at <10 sccm; transpiration flow < 40 sccm.
- A4 calibration facts (PDF 43 / 367): flow controllers vs mercury piston volume gage better than 2 percent; transducer sensitivity 0.002 atm, long-term accuracy +/-0.003 atm (0.15 %).
- A5 <10 K gradient over the 3 cm boat (PDF 41 / 365) as its own bound; thermocouple calibration and absolute uncertainty remain narrow typed absences with corrected locators/notes.
- A6 t3 (Appendix A Table 1, 12 rows / 72 cells re-checked on PDF 52): draft header change removing the unsupported A^2 unit **kept**; context now records column provenance (Matheson GC fractions, quoted Kieffer-Dunn cross sections, unit not printed).
- Bench also gains heating_method, detector and pumping_type located facts; slag calibration relation P0 = k I T0 relocated to PDF 46 / printed 370; NaCl sample characterization/pretreatment (Fisher lot 757984, <0.1 %, untreated) from PDF 56 / 380.
- Reader note: sample keys `purity`, `preparation`, `starting_materials`, `post_run_composition` validate but are not read by the migrator (Sample reads characterization/pretreatment/etc.); fixed for NaCl here, MgO/slag in group 5.

## Checks at 7b6b928f (green 61ec839da)
Migrator + finalize: 1 work, 3 benches, 3 experiments, 61 observations; hard issues 0. All 13 TMS bench other_facts, heating_method and detector survive migration (payload dump). Fidelity validator OK (exit 0). Corpus test_ledgers_valid.py exit 0.
