"""Relative shift of major single-cation fractions in each feedstock's bridged melt:
tip MELT_OXIDE_CATIONS_PER_FORMULA vs that table without the 11 trace parents B2 added
(In2O3/Ga2O3/Cu2O in 075dffb67; Li2O/Rb2O/Cs2O/B2O3/V2O3/PbO/GeO2/SnO in 83f9b8ccb). Probe only."""
import re, yaml
from simulator.chemistry import melt_activity as ma
from simulator.chemistry.melt_activity import single_cation_mole_fractions
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.state import MOLAR_MASS
AW = {"In":114.818,"Ga":69.723,"Cu":63.546,"Li":6.94,"Rb":85.468,"Cs":132.905,"B":10.81,"V":50.942,"Pb":207.2,"Ge":72.63,"Sn":118.71,"O":15.999}
def mw(f):
    tot=0.0
    for el,n in re.findall(r"([A-Z][a-z]?)(\d*)", f):
        tot += AW[el]*(int(n) if n else 1)
    return tot
added = ["In2O3","Ga2O3","Cu2O","Li2O","Rb2O","Cs2O","B2O3","V2O3","PbO","GeO2","SnO"]
full = dict(ma.MELT_OXIDE_CATIONS_PER_FORMULA)
base = {k: v for k, v in full.items() if k not in added}
d = yaml.safe_load(open('data/feedstocks.yaml'))
worst = (0.0, None)
for name, fs in d.items():
    if not isinstance(fs, dict) or "composition_wt_pct" not in fs:
        continue
    masses = normalized_feedstock_component_masses_kg(fs, 1000.0)
    mol = {}
    for k, kg in masses.items():
        if k in added: mol[k] = kg*1000.0/mw(k)
        elif MOLAR_MASS.get(k): mol[k] = kg*1000.0/MOLAR_MASS[k]
    def run(tab):
        ma.MELT_OXIDE_CATIONS_PER_FORMULA.clear(); ma.MELT_OXIDE_CATIONS_PER_FORMULA.update(tab)
        return single_cation_mole_fractions(mol)
    tip = run(full); old = run(base); run(full)
    shifts = {k: tip[k]/old[k]-1.0 for k in old}
    if not shifts:
        print(f"{name:40s} (no projectable oxides)"); continue
    m = max(abs(v) for v in shifts.values())
    if m > worst[0]: worst = (m, name)
    print(f"{name:40s} n_trace_parents={sum(1 for k in mol if k in added):2d} max|dX/X| majors={m:.3e}")
print("worst", worst)
