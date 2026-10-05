import sys, json
sys.path.insert(0,'.')
from simulator.vapour_rail.source_rail import load_source_rail
rail = load_source_rail(); out={}
forms = ["Ga","Cu","B2O3","SiO","Si","Al","AlO","Al2O","FeO","Fe","Mg","MgO","Na","K","Ca","CaO","TiO","TiO2","Cr","CrO","Mn","Ni","Pb","PbO","Ge","GeO","Sn","SnO","Li","LiO","V","VO","VO2","Rb","Cs","In","In2O","B","BO2","Cu2","Na2","K2","H2O","CO2","SO2","NaO","P4O6","PO"]
for f in forms:
    for st in ("gas","condensed_liquid","condensed_solid"):
        for r in rail.records_for(f, st):
            if r.source_id not in ("nasa-glenn","burcat"): continue
            row=[]
            for T in (1000.0,1400.0,1600.0,1800.0,2200.0):
                try: row.append(float(r.thermo.evaluate(T).g_J_per_mol).hex())
                except Exception as e: row.append(type(e).__name__)
            out[f"{r.source_id}|{r.record_id}|{f}|{st}"]=row
json.dump(out, open(sys.argv[1],"w"), sort_keys=True); print(len(out))
