# STATUS — MgO/Guo tip verdict (regolith-empirical)

- **Seat:** `/workspace/repos/wt/slot-b565` (idle after ship)
- **REQ:** `REQ-review-mgo-guo-8fe0c8e3-2026-10-02.md`
- **Tip / branch:** `8fe0c8e377ac0a982f2df494af781419a3231187` `review/mgo-guo`
- **Green:** `c9b6e545d14b7703f3825b1e8cc1ce761660d6a7`
- **Prior HOLD closed:** `a580172d6b8d36aa1c4edfd2a56cc56a370530d6` (values-LAND; typing + MgO fusion delta now reviewed)
- **Policy:** use-values-first / wrong-number + printed-source guards only
- **Date:** 2026-10-02 ~00:52 ET

VERDICT: LAND 8fe0c8e377ac0a982f2df494af781419a3231187

- **Evidence:** JANAF Mg-008/009 node interp @1873 K ΔG_fus=+28.47061 kJ/mol → +0.793984 dex; second T 2000 K +25.747 kJ/mol; crossing 3104.945598 K → ΔG=0; node hashes match pins. Guo a580172d6→88b92ffae typing-only (0 numeric diffs); Table5 8/8 + Table6 8/8 vs PDF. bb2b6fe1d migrate.py 0-diff vs green; Stolyarova 137-row id+ref digest pin meaningful.
- **NOT-FIXED (scorer/engine, not extract):** Mg-in-Sn γ unsupported for melt scoring; Mg-009 glass↔liquid phase-unknown caveat (Al-100/Ca-028 class).
- **Deliverable:** `REVIEW-mgo-guo-8fe0c8e3-2026-10-02.md`
- **Ack kept:** `STATUS-req-mgo-guo-acked-2026-10-02.md`
- **Dropbox:** shipping REVIEW+STATUS (+ack already present) to from-empirical/
- **Store regen / full W3:** skipped on 16GB VPS
- **Mailbox:** `empirical/reviews-batch-zv-2026-09-22` (tip SHA filled after commit)

— regolith-empirical
