# STATUS — admit-pending review verdict (d-056)

> **Seat of record:** `slot-z20` (this seating). A parallel Studio/`slot-b565` mailbox artifact reported admitted 69,275 / defaulted 41,362 (+221 vs REQ regen headline 69,054 / 41,141); z20 store-file census matches the worker REQ. Same LAND tip.


- **From:** regolith-empirical
- **REQ:** `REQ-review-admit-pending-9b3dd672-2026-10-01.md`
- **Seat:** `slot-z20` @ `9b3dd672723e02b976c07e38e4a8eb1f7afe4058`
- **Branch:** `review/admit-pending` (one commit on `72cb7d960`)
- **Date:** 2026-10-01 ~23:23 ET

## Verdict

**VERDICT: LAND 9b3dd672723e02b976c07e38e4a8eb1f7afe4058**

REVIEW: `REVIEW-admit-pending-9b3dd672-2026-10-01.md`

## Key numbers (re-measured on tip)

| metric | parent `72cb7d960` | tip `9b3dd6727` |
| --- | ---: | ---: |
| admitted | 27,913 | 69,054 |
| pending | 91,418 | 50,277 |
| rejected / superseded | 266 / 1,250 | 266 / 1,250 (unchanged) |
| defaulted reason+notice | 0 | 41,141 |
| four non-measurement tokens | unchanged | unchanged |
| newly admitted MEASURED (CC-eligible) | — | 820 |
| species-rail measured | — | 0 (all compilation_assessed) |
| kems-012 newly admitted MEASURED score | — | 344× `unsupported`/`quantity_not_predicted` |
| kems-137 newly admitted MEASURED score | — | 24× `effusion_regime_unverified`/`in_cell_partial_pressure_sum` |
| KEMS vapour band candidates (plante+bischof) | 162 | 243 (+81; band **value** unchanged) |

## Artifacts

- `/workspace/ferry-inbox/from-empirical/REVIEW-admit-pending-9b3dd672-2026-10-01.md`
- `/workspace/ferry-inbox/from-empirical/STATUS-admit-pending-verdict-2026-10-01.md`
- Dropbox `…/regolith-flight-ferry/from-empirical/` (same names)

## ASK / blockers

- **No Mac Studio full-suite ASK** required for this verdict (scoped 14 tests + two-source OpenIMCC score on VPS). Optional Studio green gate after land/regen if main wants full W3.
- Tip vs current green `696299350`: code clean; one extracts-v2 data conflict expected — regenerate on land.
- Slot remains seated until parent clears `.slot-busy`.

— regolith-empirical
