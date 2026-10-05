#!/usr/bin/env python3
"""t-1127 review probe: independent check of the Al/Si/Mn(l) sidecar refits
against JANAF node data in the repo.  Usage: fit_check.py <repo_root>
Two independent JANAF routes:
  F (formation): log10 P/Pa = 5 - (dfG(g) - dfG(l)) / (R T ln10)
  A (absolute) : G = dfH(298.15) + [H-H298] - T*S per phase, same formula.
"""
import math, sys
from pathlib import Path
import yaml
import numpy as np
from scipy.optimize import least_squares, brentq

ROOT = Path(sys.argv[1])
TAB = ROOT / "data/literature/compilations/janaf/tables"
R = 8.314462618e-3
LN10 = math.log(10.0)
LOG_ATM = math.log10(101325.0)

def load(tid):
    t = yaml.safe_load((TAB / f"{tid}.yaml").read_text())["table"]
    out = {}
    for r in t["values"]:
        T = (r.get("temperature") or {}).get("value")
        if T is None: continue
        g = lambda k: (r.get(k) or {}).get("value")
        out[float(T)] = dict(G=g("formation_gibbs_energy"), H=g("enthalpy_increment"),
                             S=g("entropy"), Hf=g("formation_enthalpy"))
    return out

def hf298(tab):
    return tab[298.15]["Hf"]

def logp_F(gas, liq, T):
    return 5.0 - (gas[T]["G"] - liq[T]["G"]) / (R * T * LN10)

def logp_A(gas, liq, T):
    Gg = hf298(gas) + gas[T]["H"] - T * gas[T]["S"] / 1000.0
    Gl = hf298(liq) + liq[T]["H"] - T * liq[T]["S"] / 1000.0
    return 5.0 - (Gg - Gl) / (R * T * LN10)

def ant(A, B, C, T):
    return A - B / (T + C)

vp = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
def find_row(sym):
    for fam in vp["families"].values():
        sp = ((fam or {}).get("physical_properties") or {}).get("species") or {}
        if sym in sp and "pure_component_antoine" in sp[sym]:
            return sp[sym]
    raise KeyError(sym)

OLD = {  # base 69c0ef23a values (data/vapor_pressures.yaml)
    "Al": (10.73623, 13204.109, -24.306, (1557, 2329)),
    "Si": (14.56436, 23308.848, -123.133, (1997, 2560)),
    "Mn": (9.79086818966, 10402.946653439165, -160.520331526149, (1519, 2334.526)),
}
CASES = {"Al": ("Al-003", "Al-005", "Al-002"), "Si": ("Si-003", "Si-005", "Si-002"),
         "Mn": ("Mn-003", "Mn-005", None)}
JANAF_TB_1BAR = {"Al": 2790.812, "Si": 3504.616, "Mn": 2334.526}  # FUGACITY = 1 bar lines

for sym, (lid, gid, crid) in CASES.items():
    liq, gas = load(lid), load(gid)
    row = find_row(sym)
    pca = row["pure_component_antoine"]
    seg = pca["segments"][1] if sym == "Mn" else pca
    A, B, C = float(seg["A"]), float(seg["B"]), float(seg["C"])
    lo, hi = seg["valid_range_K"]
    if sym == "Mn": lo, hi = 1519.0, 2334.526
    nodes = sorted(T for T in liq.keys() & gas.keys() if lo <= T <= hi
                   and liq[T]["G"] is not None and gas[T]["G"] is not None)
    rF = [ant(A, B, C, T) - logp_F(gas, liq, T) for T in nodes]
    rA = [ant(A, B, C, T) - logp_A(gas, liq, T) for T in nodes]
    dFA = [logp_F(gas, liq, T) - logp_A(gas, liq, T) for T in nodes]
    print(f"== {sym}: new A={A} B={B} C={C} range {lo}-{hi}; nodes={len(nodes)} ({nodes[0]:.0f}..{nodes[-1]:.0f})")
    print(f"   max|resid| vs route F = {max(map(abs, rF)):.9f} dex (yaml says {seg.get('max_abs_log10_residual_vs_source')}, count {seg.get('fit_node_count')})")
    print(f"   rms resid vs F = {math.sqrt(sum(x*x for x in rF)/len(rF)):.9f}")
    print(f"   max|resid| vs route A = {max(map(abs, rA)):.6f}; max|F-A| = {max(map(abs, dFA)):.6f} dex")
    # independent least-squares refit on route F nodes
    Ts = np.array(nodes); ys = np.array([logp_F(gas, liq, T) for T in nodes])
    fit = least_squares(lambda p: p[0] - p[1] / (Ts + p[2]) - ys, x0=[A, B, C], xtol=1e-15, ftol=1e-15, gtol=1e-15)
    a, b, c = fit.x
    print(f"   my LSQ refit: A={a:.10f} B={b:.6f} C={c:.10f}; max|resid|={np.max(np.abs(fit.fun)):.9f}")
    # old fit vs JANAF (within its own valid range and over the new range)
    oA, oB, oC, (olo, ohi) = OLD[sym]
    onodes = [T for T in nodes if olo <= T <= ohi]
    ro = [ant(oA, oB, oC, T) - logp_F(gas, liq, T) for T in onodes]
    if ro:
        print(f"   OLD fit residual vs JANAF inside OLD range {olo}-{ohi}: min {min(ro):+.4f} max {max(ro):+.4f} dex at T={onodes[0]:.0f}..{onodes[-1]:.0f}")
    for T in [1000.0, 1300.0, 1500.0, 1700.0, 2000.0, 2200.0, 2500.0, 2700.0, 3000.0, 3500.0]:
        if T in nodes or T in liq.keys() & gas.keys():
            try: j = logp_F(gas, liq, T)
            except Exception: continue
            if j is None: continue
            print(f"   T={T:6.0f}: JANAF(l/g) {j:+.4f}  new {ant(A,B,C,T):+.4f} ({ant(A,B,C,T)-j:+.4f})  old {ant(oA,oB,oC,T):+.4f} ({ant(oA,oB,oC,T)-j:+.4f})")
    # boiling points
    tb = JANAF_TB_1BAR[sym]
    # interpolate dvapG(T) linearly between bracketing nodes for 1 atm
    allT = sorted(T for T in liq.keys() & gas.keys() if liq[T]["G"] is not None and gas[T]["G"] is not None)
    def logp_interp(T):
        i = max(k for k in range(len(allT)) if allT[k] <= T)
        T0, T1 = allT[i], allT[i + 1]
        d0 = gas[T0]["G"] - liq[T0]["G"]; d1 = gas[T1]["G"] - liq[T1]["G"]
        d = d0 + (d1 - d0) * (T - T0) / (T1 - T0)
        return 5.0 - d / (R * T * LN10)
    tb_atm = brentq(lambda T: logp_interp(T) - LOG_ATM, tb - 50, tb + 50)
    tb_bar_chk = brentq(lambda T: logp_interp(T) - 5.0, tb - 50, tb + 50)
    new_tb_atm = brentq(lambda T: ant(A, B, C, T) - LOG_ATM, 500, 6000)
    old_tb_atm = brentq(lambda T: ant(oA, oB, oC, T) - LOG_ATM, 500, 6000)
    bpC = row.get("boiling_point_C")
    print(f"   JANAF Tb(1 bar) published {tb} K; node-interp Tb(1 bar) {tb_bar_chk:.2f} K; Tb(1 atm) {tb_atm:.2f} K")
    print(f"   new fit: log10P at JANAF Tb(1bar)={ant(A,B,C,tb):.4f}, at Tb(1atm)={ant(A,B,C,tb_atm):.4f} (target {LOG_ATM:.4f}); new-fit T(1 atm)={new_tb_atm:.2f} K")
    print(f"   old fit: log10P at JANAF Tb(1atm)={ant(oA,oB,oC,tb_atm):.4f}; old-fit T(1 atm)={old_tb_atm:.2f} K = {old_tb_atm-273.15:.1f} C")
    if bpC is not None:
        T_b = bpC + 273.15
        print(f"   yaml boiling_point_C={bpC} -> {T_b:.2f} K; new log10P there {ant(A,B,C,T_b):.4f}; old {ant(oA,oB,oC,T_b):.4f}")
    # effective dH_vap slope at 2000 K: dlog10P/d(1/T) * -R ln10
    for lab, (a_, b_, c_) in (("new", (A, B, C)), ("old", (oA, oB, oC))):
        T = 2000.0
        print(f"   {lab} effective dHvap at {T:.0f} K = {b_*R*LN10*(T/(T+c_))**2:.1f} kJ/mol")
    T0, T1 = 1900.0, 2100.0
    if T0 in allT and T1 in allT:
        s = -(logp_F(gas, liq, T1) - logp_F(gas, liq, T0)) / (1/T1 - 1/T0) * R * LN10
        print(f"   JANAF secant dHvap 1900-2100 K = {s:.1f} kJ/mol")
    # below-range extrapolation vs crystal (sublimation) where relevant
    if crid:
        cr = load(crid)
        for T in [900.0, 1000.0, 1300.0, 1500.0, 1600.0]:
            if T in cr and T in gas and cr[T]["G"] is not None:
                j = logp_F(gas, cr, T)
                print(f"   below-melt check T={T:.0f}: JANAF cr/g {j:+.4f}; new fit {ant(A,B,C,T):+.4f} ({ant(A,B,C,T)-j:+.4f})")
