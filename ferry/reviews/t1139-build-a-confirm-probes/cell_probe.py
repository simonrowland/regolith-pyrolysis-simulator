import sys
sys.path.insert(0,'.')
from simulator.diagnostic_helpers.binary_pot_battery import _cell_oxide_thermodynamics
for m in ("W","Mo"):
    try:
        g, buf, src = _cell_oxide_thermodynamics(m, 1800.0)
        print(m, "OK buffer_log10_bar", buf, "gases", sorted(g))
    except Exception as e:
        print(m, "RAISED", type(e).__name__, str(e)[:200])
