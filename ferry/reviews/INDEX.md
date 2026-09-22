# Empirical reviews 2026-09-22

Mailbox branch only — never merge. Base: `fbe3491b2` (`work-v064-green`).

| Task | Verdict | Counts | Notes |
| --- | --- | --- | --- |
| R1 VapoRock commissioning notice (`ce8d6a7f6`, not landed) | LAND-WITH-FIXES | P0=0 P1=0 P2=1 P3=2 | notice stamped on typed forbidden-species refusals |
| R2 readiness gap reporting (`924eea91e`+`f5b422d0e`) | LAND-WITH-FIXES | P0=0 P1=2 P2=2 P3=1 | f5b422d0e sound; 924eea91e over-counts “present” |
| R3 printed fO2 waypoints (`e07ed3a92`+`5d9753eaf`) | LAND-WITH-FIXES | P0=0 P1=2 P2=2 P3=2 | `10^n` as log fO2; interval overwrite by point |
| R4 migrate stale sibling + d-036 FK (`805b8db26`+`ea9cdfb63`) | DO-NOT-LAND | P0=1 P1=2 P2=0 P3=2 | cross-work equipment FK via `::` passthrough |

Optional unapplied fixes: `R1-fix.patch` … `R4-fix.patch`.
