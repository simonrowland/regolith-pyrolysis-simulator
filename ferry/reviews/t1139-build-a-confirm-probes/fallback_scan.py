import sys, re, json
sys.path.insert(0,'.')
from pathlib import Path
from simulator.reference_data import nasa_glenn
from simulator.vapour_rail.source_rail import load_source_rail, compilation_standard_state, _reference_species, _covering_records
rail=load_source_rail(); idx=rail._indexes["nasa-glenn"]; root=Path('.').resolve()
man=nasa_glenn.load_manifest()["entries"]
bad=[]
unmapped=[]
for el in sorted({e["formula"] for e in man if re.fullmatch(r"[A-Z][a-z]?", str(e.get("formula") or ""))}):
    f,gas_only=_reference_species(el)
    if gas_only: continue
    cond=[e for e in man if e.get("formula")==f and str(e.get("phase"))!="gas"]
    for e in cond:
        if compilation_standard_state(str(e.get("phase"))) is None: unmapped.append((el,e.get("record_id"),e.get("phase")))
    for T in (1000.0,1400.0,1600.0,1800.0,2000.0):
        mats=[]
        for st in ("condensed_liquid","condensed_solid","condensed"): mats+=list(idx.native_records_for(f,st))
        if _covering_records(tuple(mats),T): continue
        # published bands of manifest condensed records
        cover=[]
        for e in cond:
            d=nasa_glenn.load_record_document(root/e["path"])
            ivs=d.get("intervals") or []
            lo=min(iv["T_min_K"]["value"] for iv in ivs) if ivs else None; hi=max(iv["T_max_K"]["value"] for iv in ivs) if ivs else None
            if lo is not None and lo<=T<=hi: cover.append((e["record_id"],e.get("phase"),lo,hi))
        if cover: bad.append((el,T,cover))
print("silent gas-reference fallbacks where a published condensed record covers T:")
for b in bad: print(" ",b)
print("unmapped condensed phase tokens:", unmapped)
