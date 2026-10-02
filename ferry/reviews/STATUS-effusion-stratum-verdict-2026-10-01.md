# STATUS — effusion-stratum / d-055 uncalibrated Knudsen verdict (regolith-empirical)

- **Tip:** `874ea78e34bfb421b436a484646f6122a88fbabb` (`review/effusion-stratum`)
- **Seat:** `/workspace/repos/wt/slot-z14` (busy cleared after review)
- **Parent / green:** `72cb7d960a71e7bb81430ed733a15f99edfa330b` (merges clean onto `696299350`)
- **VERDICT: LAND 874ea78e34bfb421b436a484646f6122a88fbabb**
- **Counts:** P0 0, P1 0, P2 0
- **Checks:** (1) grounded P_PARTIAL gate outcomes store-wide tip vs green: 645 rows, 0 mismatches — PASS. (2) flagged excluded from KEMS band + 2×MAD/family pools + headlines; `calibration-not-grounded` stratum in report (23 SiO_evolution / 44 vapour) — PASS. (3) score_store OpenIMCC on five sources: 199 gate transitions; 67 numeric no_band (Stolyarova, median −2.226 dex) with cal notice; 82 refuse identity_*; Stolyarova mostly scores not identity_unknown — PASS. (4) 199 exact for five sources; ~261 not a five-source hole; ichise/allibert/ms2000 = 0 P_PARTIAL; store-wide transitions 370 — PASS. (5) NOT-FIXED: no calibration invent, no identity fix, over-limit still refuses — PASS.
- **Targeted tests:** uncalibrated / band-exclusion / over-limit mutation tests passed (`-o addopts=''`); no full W3 on VPS.
- **Deliverable:** `REVIEW-effusion-stratum-874ea78e-2026-10-01.md`
- **Date:** 2026-10-01 ~21:05 ET

— regolith-empirical
