# `engines/builtin/` — builtin chemistry provider plane

Kernel-registered builtin chemistry. See the
[binding spec §3 (authority matrix)](../../docs-private/chemistry-engine-binding-spec-2026-05-14.md)
for which intents the builtin owns. Source for the seven-intent extraction
goal `\goal BUILTIN-ENGINE-EXTRACTION` (#7) in
[codex-goal-queue-2026-05-14.md](../../docs-private/codex-goal-queue-2026-05-14.md).

## Intent-by-intent migration plan

| # | Intent                   | Status this commit          | Source path                              |
|---|--------------------------|------------------------------|-------------------------------------------|
| 1 | `VAPOR_PRESSURE`         | **Wired (this commit)**      | `engines/builtin/vapor_pressure.py`       |
| 2 | `EVAPORATION_FLUX`       | Pending                      | `simulator/evaporation.py`                |
| 3 | `EVAPORATION_TRANSITION` | Pending                      | `simulator/evaporation.py`                |
| 4 | `CONDENSATION_ROUTE`     | Pending                      | `simulator/condensation.py`               |
| 5 | `ELECTROLYSIS_STEP`      | Pending                      | `simulator/electrolysis.py`               |
| 6 | `METALLOTHERMIC_STEP`    | Pending                      | `simulator/extraction.py`                 |
| 7 | `STAGE0_PRETREATMENT`    | Pending                      | `simulator/core.py` (`_stage0_*`)         |

Each flip lands per the per-intent rule: ship the provider, run shadow
parity (1e-9 mol per species on lunar + Mars + asteroid feedstocks),
flip the legacy call site, remove the shadow comparison.

## Authority

VAPOR_PRESSURE: **authoritative** (builtin Antoine/Ellingham). Result is
diagnostic — `transition=None` — because VAPOR_PRESSURE owns no ledger
mutation (vapor pressures are an input to `EVAPORATION_FLUX`, which is the
intent that emits the LedgerTransition). VapoRock is a diagnostic-only
shadow; `\goal VAPOROCK-AUTHORITY-PROMOTION` (#10) is a historical name only.
