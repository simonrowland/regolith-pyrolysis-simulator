import sys, yaml
def declared(path):
    d=yaml.safe_load(open(path)); out=set()
    def walk(x, sid=None):
        if isinstance(x, dict):
            if "stoich_oxide_per_vapor" in x and sid: out.add(sid)
            for k,v in x.items():
                walk(v, k if isinstance(v, dict) and k not in ("compatibility_fields","fiat_routing","physical_properties","species") else sid)
    for fam, f in (d.get("families") or {}).items():
        for sid, row in ((f.get("physical_properties") or {}).get("species") or {}).items():
            cf=((f.get("fiat_routing") or {}).get("compatibility_fields") or {})
            if "stoich_oxide_per_vapor" in cf or "stoich_oxide_per_vapor" in (row or {}): out.add(sid)
    return out
b=declared(sys.argv[1]); h=declared(sys.argv[2])
sys.path.insert(0, sys.argv[3])
from simulator.vapour_rail.stoich import CATALOG_DERIVED_STOICH_SPECIES as C
print("base declared", len(b), "head declared", len(h), "retired", len(b-h))
print("retired == CATALOG_DERIVED_STOICH_SPECIES:", (b-h)==set(C), sorted((b-h)^set(C)))
print("still declared (kept rows):", len(h))
