import sys, re
sys.path.insert(0,'.')
from simulator.vapour_rail import source_rail as sr
rail = sr.load_source_rail()
print("indexes:", list(rail._indexes))
for comp in ("nasa-glenn","burcat"):
    idx = rail._indexes.get(comp)
    if idx is None: continue
    elements=set()
    # all elements appearing in any indexed formula
    for (f, st) in list(getattr(idx, "_by_key", {}).keys()) if hasattr(idx, "_by_key") else []:
        pass
    # fallback: scan records via private attribute names
    recs = []
    for name in dir(idx):
        v = getattr(idx, name)
        if isinstance(v, dict) and v and all(isinstance(k, tuple) for k in list(v)[:3]):
            for k, lst in v.items():
                elements.update(re.findall(r"[A-Z][a-z]?", str(k[0])))
    nogas_cond=[]; above=[]
    for el in sorted(elements):
        f, gas_only = sr._reference_species(el)
        if gas_only: continue
        cond=[]
        for st in ("condensed_liquid","condensed_solid","condensed"): cond += list(idx.native_records_for(f, st))
        gas = list(idx.native_records_for(f, "gas"))
        res=[]
        for T in (1400.0,1600.0,1800.0):
            try:
                g, n = sr._element_reference_species_g(idx, el, T)
                cov=[r for r in cond if r.T_min_K<=T<=r.T_max_K]
                res.append("cond" if cov else "GAS")
            except sr.SourceCoverageGap as e: res.append("gap")
            except Exception as e: res.append("ERR:"+type(e).__name__)
        if not cond:
            nogas_cond.append((el, f, res))
        elif "GAS" in res:
            above.append((el, f, max(r.T_max_K for r in cond), res))
    print(comp, "elements:", len(elements))
    print("  no condensed record at all (gas reference used silently if gas covers):", nogas_cond)
    print("  gas used above condensed coverage end within 1400-1800 K:", above)
