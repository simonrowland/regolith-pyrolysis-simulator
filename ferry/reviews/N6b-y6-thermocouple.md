# N6b Y6 — Thomas & Wood thermocouple fill

**Lane:** `empirical/n6b-y6-thermocouple-2026-09-22`
**Base:** `7d06507ef5cbd0901e58b934758118f0dbefe217`
**Tip:** `8af0fac6a` (`fix: land Thomas Wood C-type thermocouple locator`)
**Date:** 2026-09-22 (America/Toronto, EDT)

## Result

**READY — Y6 P1 fixed.**

Updated `data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml` to record the printed methods fact:

> alumina-sheathed C-type (W95Re5–W74Re26) thermocouple

The sensor locator is now physical PDF page 4, section 2 (Experimental methods). No observation numbers or units changed.

Also corrected the Thomas extract's physical PDF locators by +1 to account for the publisher “Dear author” cover at PDF page 1: Table 1/methods p.4, analytical techniques p.6, Table 2 p.7, and abstract p.2. The correction note and extraction cross-check text were updated consistently.

## Evidence

- PDF: `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/thomas-wood-2021-chlorine-silicate-melts.pdf`
- PDF SHA-256: `f190a321d757537d28ccc088ea49d7057bc4f0f5fc8347088e3a36bfaed5da60`
- Printed thermocouple text verified with `pdftotext -layout`; methods prose is on physical PDF p.4.

## Validation

- `.venv/bin/python tools/validate_literature_extracts.py data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml --check-fidelity-match` → `OK: 1 extract file(s) valid`
- N6 trio validation (Thomas, Ueshima, Jäggi) with `--check-fidelity-match` → `OK: 3 extract file(s) valid`
- `git diff --check` → pass
- Full `tests/test_literature_extracts.py` was run but fails on pre-existing unrelated corpus validation errors in other extracts; the corrected Thomas extract passes the targeted validator above.

## Delivery

Pushed to:

`origin/empirical/n6b-y6-thermocouple-2026-09-22`

Remote branch tip: `8af0fac6a`.
