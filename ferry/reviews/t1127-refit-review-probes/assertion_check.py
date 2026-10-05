#!/usr/bin/env python3
"""t-1127 review probe: recompute the new ground-truth reference values and old/new assertion numbers from JANAF nodes."""
import math, sys
from pathlib import Path
import yaml
TAB = Path(sys.argv[1]) / "data/literature/compilations/janaf/tables"
R = 8.314462618e-3
def g(tid, T):
    for r in yaml.safe_load((TAB / f"{tid}.yaml").read_text())["table"]["values"]:
        if (r.get("temperature") or {}).get("value") == T:
            return r["formation_gibbs_energy"]["value"]
def p(gas, liq, T): return 1e5 * 10 ** (-(g(gas, T) - g(liq, T)) / (R * T * math.log(10)))
ant = lambda A,B,C,T: 10 ** (A - B / (T + C))
print("Al 2200 JANAF node Pa:", p("Al-005", "Al-003", 2200.0), "(test pins 3196.816100 rel 0.01)")
print("Si 2200 JANAF node Pa:", p("Si-005", "Si-003", 2200.0), "(test pins 37.325229 rel 0.002)")
print("Al 2200 new fit Pa:", ant(10.5215429528, 15145.174655, -42.6590481419, 2200.0), "old Stull Pa:", ant(10.73623, 13204.109, -24.306, 2200.0))
print("Si 2200 new fit Pa:", ant(10.6913003080, 19744.5887902, -34.7482634626, 2200.0), "old Stull Pa:", ant(14.56436, 23308.848, -123.133, 2200.0))
for lab, gas, liq, T, new, old in (("Al", "Al-005", "Al-003", 1700.0, (10.5215429528, 15145.174655, -42.6590481419), (10.73623, 13204.109, -24.306)),
                                   ("Si", "Si-005", "Si-003", 2200.0, (10.6913003080, 19744.5887902, -34.7482634626), (14.56436, 23308.848, -123.133))):
    j = p(gas, liq, T)
    print(f"{lab} {T:.0f} K residual new {math.log10(ant(*new, T)/j):+.6f} old {math.log10(ant(*old, T)/j):+.4f} dex")
