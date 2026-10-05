#!/usr/bin/env python3
"""t-1127 review probe: which coefficient block each production selector returns for Al and Si,
the pressure it implies, and the valid range reported (run from a repo root; compares to base via argv)."""
import sys, math
sys.path.insert(0, ".")
from simulator.vapour_rail.catalog import vapor_pressure_compatibility_view
from engines.builtin import vapor_pressure as vp
from engines.antoine import _antoine_log10_pressure
import yaml
view = vapor_pressure_compatibility_view(yaml.safe_load(open("data/vapor_pressures.yaml").read()))
metals = view["metals"]
for sym in ("Al", "Si"):
    row = metals[sym]
    print(f"== {sym}: fit_target={row.get('fit_target')!r} interval_required={row.get('interval_required')!r} keys={sorted(k for k in row if 'antoine' in k or 'reaction' in k)}")
    for T in (1000.0, 1200.0, 1400.0, 1600.0, 1800.0, 2000.0, 2200.0, 2600.0):
        out = []
        for name in ("vapor_pressure_antoine_coefficients", "wall_condensation_antoine_coefficients"):
            c, blk = getattr(vp, name)(row, T)
            if c and "A" in c:
                lp = _antoine_log10_pressure(float(c["A"]), float(c["B"]), float(c.get("C", 0.0)), T)
                rng = vp.vapor_pressure_valid_range_K(row, blk, T)
                out.append(f"{name.split('_antoine')[0]}: {blk} log10P={lp:+.4f} range={rng}")
            else:
                out.append(f"{name.split('_antoine')[0]}: {blk} (empty)")
        print(f"  T={T:.0f}: " + " | ".join(out))
