# STATUS — Finalize Fe/Mg batch verdicts (regolith-empirical)

- **REQ:** `REQ-finalize-femg-batch-verdicts-2026-10-02.md` — **acked**
- **Seat:** `/workspace/repos/wt/mailbox-batch-zv`
- **Date:** 2026-10-02 ~00:31 ET
- **Policy:** values-side guards already complete; no re-review of extracts; no battery_migrate on 16GB VPS

## Condition confirmation

Values-side `VERDICT lean: LAND` lines were **conditional only on store regen counts** (`battery_migrate` could not run on the 16GB/offline box). Nothing else. Main's landing-train regeneration now confirms those counts (Plante 9 obs changed / hard +9; Markova 22→44). Values-side guards unchanged → leans are now **final**.

## Final verdicts (six LAND)

VERDICT: LAND b843cd76be8762cf561267bd6fd5ef70be604422
VERDICT: LAND 4b04c2290552fca19b7d9c48b68066115a6787da
VERDICT: LAND c04bff249ab1ee8107dab4c1776c0087c45f20af
VERDICT: LAND 2883c54d02355d2365b3a98e26ca150d3b9d43eb
VERDICT: LAND 60db4ba13acd1288b5eafc4acb5d38777a130605
VERDICT: LAND e644f212033d26c98d8f916d8e850d13fc3a7faf

- **Markova note:** landing as extract-only `2c92c4095`, blob-identical extract (tip above is the reviewed commit).

## Explicitly NOT finalized

- **Guo** `a580172d6` — NOT finalized; awaiting amend/delta REQ from main (`**VERDICT:** HOLD` in REVIEW-femg-batch2).
- **Batch1 Yakovlev** `617741fad` — remains **REVISE** (SiO/Ca range-maxima); revised tip `60db4ba13` is among the six LAND above.

## Deliverables

- Patched: `REVIEW-femg-batch1-2026-10-01.md`, `REVIEW-femg-batch2-2026-10-01.md`
- Ack: `STATUS-req-finalize-femg-acked-2026-10-02.md`
- This file: `STATUS-finalize-femg-batch-verdicts-2026-10-02.md`

## Ship fields

- **Dropbox:** shipped to from-empirical/ 2026-10-02 ~00:31 ET
- **Mailbox tip:** `MAILBOX_TIP_PENDING` on `empirical/reviews-batch-zv-2026-09-22`
- **Artifacts tip:** `ARTIFACTS_TIP_PENDING`

— regolith-empirical
