import sys, json, math
from pathlib import Path
root = Path(sys.argv[1]); out = str(Path(sys.argv[2]).resolve())
sys.path.insert(0, str(root))
import os; os.chdir(root)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.yaml_cache import load_cached_safe_yaml
payload = load_cached_safe_yaml((root/'data/vapor_pressures.yaml').read_text())
cat = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
def norm(x):
    if isinstance(x, float): return x.hex()
    if isinstance(x, dict): return {str(k): norm(v) for k,v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)): return [norm(v) for v in x]
    if isinstance(x, (str,int,bool)) or x is None: return x
    return repr(x)
lv = norm(cat.legacy_view())
press = {}
for sid, sp in sorted(cat.species.items()):
    ev = sp.evaluator
    if ev is None: press[sid]=None; continue
    row=[]
    for T in (1200.0,1400.0,1600.0,1800.0,2000.0,2200.0):
        for po2 in (1e-12,1e-8,1e-4):
            kw={'pO2_bar':po2}
            if getattr(ev,'activity_exponent',0): kw['source_activity']=0.37
            try: row.append(ev.evaluate(T, **kw).pressure_pa.hex())
            except Exception as e: row.append('ERR:'+type(e).__name__)
    press[sid]=row
spec = {sid: repr(sp) for sid, sp in sorted(cat.species.items())}
json.dump({'n':len(cat.species),'legacy':lv,'press':press,'species_repr':spec}, open(out,'w'), sort_keys=True)
print(len(cat.species))
