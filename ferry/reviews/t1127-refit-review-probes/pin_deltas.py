#!/usr/bin/env python3
"""t-1127 review probe: dex movement of every pin between a45236744 (pre-refit) and 3e54afda8 (post-refit)."""
import math, re, subprocess, sys
repo = sys.argv[1] if len(sys.argv) > 1 else "."
def pins(rev):
    txt = subprocess.run(["git", "show", f"{rev}:tests/chemistry/test_t1127_refit_pins.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    return {(s, float(t)): float.fromhex(h) for s, t, h in re.findall(r'\("(\w+)", ([\d.]+), "(0x[0-9a-fp.+-]+)"\)', txt)}
old, new = pins("a45236744"), pins("3e54afda8")
for k in old:
    print(f"{k[0]:2s} {k[1]:6.0f} K  old {old[k]:.6g} Pa  new {new[k]:.6g} Pa  delta {math.log10(new[k]/old[k]):+.4f} dex")
