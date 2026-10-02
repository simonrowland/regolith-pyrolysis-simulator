# STATUS — evap-batch-reuse REVIEW OF RECORD verdict

**LAND** `8a84da9f43725e05dd363c4f6d7c851831ca5b75`

Branch `review/evap-batch-reuse`. Seat `slot-z14`. Parent `910ec1779330c2478f8418eff8ede5c8f39fc8d6`.

## Attacks
1. **Invalidation key** — PASS. Full resolve-path inventory keyed or provably constant within one solve (ledger mols via read-only EVAPORATION_FLUX + `len(transitions)`); overhead overlay post-resolve.
2. **Cache lifetime** — PASS. Local dict per headspace solve; no instance cache; no cross-tick/cross-solve leakage.
3. **Bit-identity** — PASS (targeted). Cache-vs-fresh unit maps equal; RH03 24h / SiO wall-T **not re-run on VPS** (heavy; worker Mac BIT-IDENTICAL + 96/1012 + CPU 1.06× green cited). Optional Studio ASK non-blocking.
4. **Mutations** — PASS. Ignore-key and skip-write both RED against new unit test; strengthen probes invalidate T/pO2/campaign/activities/engine inputs.

## Artifacts
- `REVIEW-evap-batch-reuse-8a84da9f4-2026-10-02.md`
- `STATUS-evap-batch-reuse-verdict-2026-10-02.md` (this file)

Targeted: 5 overhead/same-tick nodes passed (6.41s). Seat idle on tip. Mailbox → `empirical/reviews-batch-zv-2026-09-22`.

— regolith-empirical, 2026-10-02 19:50 EDT
