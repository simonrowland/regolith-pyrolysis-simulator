import sys
sys.path.insert(0,'.')
from simulator.vapour_rail.source_rail import load_source_rail, _element_reference_g_j_per_mol_atom, _reference_species, _covering_records
from simulator.reference_data import nasa_glenn
rail = load_source_rail()
idx = rail._indexes["nasa-glenn"]
def chosen(el, T):
    f, gas_only = _reference_species(el)
    cands=[]
    if not gas_only:
        for st in ("condensed_liquid","condensed_solid","condensed"):
            cands += list(idx.native_records_for(f, st))
    cov = _covering_records(tuple(cands), T)
    liq=[r for r in cov if r.standard_state=="condensed_liquid"]
    c = liq[0] if liq else (cov[0] if cov else None)
    if c is None:
        g=_covering_records(idx.native_records_for(f,"gas"),T); c=g[0] if g else None
    return (c.record_id, c.native_phase, round(c.T_min_K,1), round(c.T_max_K,1)) if c else None
for el in ("B","Ga","Cu","O","Pb","Li","V","Sn","Ge","In","Rb","Cs","Si","Al","Fe","Ti","C"):
    print(el, [ (T, chosen(el,T)) for T in (1400.0,1600.0,1800.0)])
# overlap scan: elements whose L record starts below the end of a solid record (liquid chosen in solid-stable range)
import re
elements=set()
for e in nasa_glenn.load_manifest().get("entries") or []:
    f=str(e.get("formula") or "")
    if re.fullmatch(r"[A-Z][a-z]?", f): elements.add(f)
bad=[]
for el in sorted(elements):
    sol=list(idx.native_records_for(el,"condensed_solid")); liq=list(idx.native_records_for(el,"condensed_liquid"))
    if sol and liq:
        smax=max(r.T_max_K for r in sol); lmin=min(r.T_min_K for r in liq)
        if lmin < smax - 1e-6: bad.append((el, 'solid_max',smax,'liq_min',lmin))
print("liquid-overlaps-solid elements:", bad)
