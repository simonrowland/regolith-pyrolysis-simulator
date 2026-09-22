# Empirical reviews / sweeps mailbox 2026-09-22

Never merge. Base tip at first landing: `fbe3491b2`. Later files may reference `review/janaf-batch-2026-09-22`.

## Reviews R1–R9

| Task | Verdict |
| --- | --- |
| R1 VapoRock commissioning | LAND-WITH-FIXES P0=0 P1=0 P2=1 P3=2 |
| R2 readiness gaps | LAND-WITH-FIXES P0=0 P1=2 P2=2 P3=1 |
| R3 printed fO2 | LAND-WITH-FIXES P0=0 P1=2 P2=2 P3=2 |
| R4 migrate + d-036 FK | DO-NOT-LAND P0=1 P1=2 P2=0 P3=2 |
| R5 pure-phase G/S/Cp/H | LAND-WITH-FIXES P0=0 P1=1 P2=2 P3=2 |
| R6 JANAF provenance | LAND-WITH-FIXES P0=0 P1=1 P2=1 P3=1 |
| R7 JANAF phase labels | LAND P0=0 P1=0 P2=0 P3=1 |
| R8 alias double count | LAND-WITH-FIXES P0=0 P1=1 P2=1 P3=2 |
| R9 interval printed conditions | LAND P0=0 P1=0 P2=0 P3=0 |

## Class sweeps S1–S6

| Sweep | Result |
| --- | --- |
| S1 cross-scope id refs | sites=9 live=0 P0=5 P1=3 P2=1 P3=0 |
| S2 derived stamped printed | sites=6 live=4 P0=2 P1=2 P2=2 P3=0 |
| S3 stale derived artifacts | sites=7 live=1 P0=0 P1=5 P2=0 P3=2 |
| S4 untrue report claims | sites=11 live=2 P0=0 P1=5 P2=4 P3=2 |
| S6 absence becomes number | sites=26 live=24 P0=9 P1=7 P2=8 P3=2 |

Optional `R*-fix.patch` files sit beside write-ups. R10 / W1 / S5 / S7–S9 still in flight.
