import sys
sys.path.insert(0,'.')
from simulator.vapour_rail import source_rail as sr
rail = sr.load_source_rail(); b = rail._indexes["burcat"]
for el in ("Fe","Cu","Si","Ge","Li","Mn","Sn","V","Ni"):
    print(el, "burcat condensed:", [(r.record_id, r.native_phase, r.standard_state, r.T_min_K, r.T_max_K) for st in ("condensed_liquid","condensed_solid","condensed") for r in b.native_records_for(el, st)])
print("burcat locator keys for Fe:", [k for k in b._locators if k[0]=="Fe"])
for f in ("Fe","Cu","Si","Li","Mn","Sn","V","FeO","SiO"):
    recs = rail.records_for(f, "gas")
    j=[r for r in recs if r.source_id=="nist-janaf-4th"]; bb=[r for r in recs if r.source_id=="burcat"]
    if not (j and bb): print(f, "no JANAF+Burcat pair", [r.source_id for r in recs]); continue
    out=[]
    for T in (1400.0,1600.0,1800.0):
        try: out.append((T, round((j[0].thermo.evaluate(T).g_J_per_mol - bb[0].thermo.evaluate(T).g_J_per_mol)/1000,2)))
        except Exception as e: out.append((T, type(e).__name__))
    print(f, "JANAF-Burcat ΔfG kJ/mol:", out)
