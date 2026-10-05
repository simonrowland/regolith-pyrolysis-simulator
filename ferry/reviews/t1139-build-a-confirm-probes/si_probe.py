import sys
sys.path.insert(0,'.')
from simulator.reference_data import nasa_glenn
from simulator.vapour_rail.source_rail import compilation_standard_state, load_source_rail, _nasa_record_from_path
from pathlib import Path
root=Path('.').resolve()
for e in nasa_glenn.load_manifest()["entries"]:
    if e.get("formula") in ("Si","B","Fe","Ti","Al","V"):
        st=compilation_standard_state(str(e.get("phase")))
        rec=_nasa_record_from_path(root/e["path"]) if st else None
        print(e.get("record_id"), e.get("formula"), repr(e.get("phase")), "->", st, "materialized:", None if rec is None else (rec.T_min_K, rec.T_max_K))
rail=load_source_rail(); idx=rail._indexes["nasa-glenn"]
# Effect: ΔfG of SiO2(l)? and SiO(g) via NASA conversion vs JANAF at 1400/1600/1800
from simulator.vapour_rail.source_rail import compare_g_over_overlap
for f,st in (("SiO","gas"),("SiO2","condensed_liquid"),("Si","gas")):
    recs=rail.records_for(f,st)
    j=[r for r in recs if r.source_id=="nist-janaf-4th"]; n=[r for r in recs if r.source_id=="nasa-glenn"]
    if j and n:
        for row in compare_g_over_overlap(j[0], n[0]):
            print(f, st, row["T_K"], "JANAF-NASA kJ/mol =", round(row["delta_g_J_per_mol"]/1000,2))
