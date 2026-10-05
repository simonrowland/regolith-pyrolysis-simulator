import sys, copy, math
sys.path.insert(0,'.')
from simulator.vapour_rail.channel_generator import generate_first_batch, catalog_payload_from_channels
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.source_rail import load_source_rail
rail = load_source_rail()
b = generate_first_batch(rail=rail)
print("channels", len(b.channels), "gaps", len(b.gaps))
from collections import Counter
print("gap kinds", Counter(g.kind for g in b.gaps))
print("sources", Counter(tuple(sorted(set(c.selected_sources.values()))) for c in b.channels))
for c in b.channels:
    print(c.species_id, dict(c.selected_sources), c.native_phases, {k:tuple(round(x) for x in v) for k,v in c.bands_K.items()})
# P° mix probe
ch = [c for c in b.channels if c.species_id=="t1139_B_B2O3"] or [c for c in b.channels if c.element=="B"]
ch = ch[0]
fam = copy.deepcopy(ch.family)
sp = fam["physical_properties"]["species"][ch.species_id]
st = sp["pressure_models"][0]["species_thermo"]
print("B2O3 family thermo keys", list(st), [v["evaluator_family"] for v in st.values()])
base = compile_vapour_rail_catalog({"schema_version":2,"families":{"f":copy.deepcopy(ch.family)}}, emit_u0_request_rules=False)
p0 = base.species[ch.species_id].evaluator.evaluate(1600.0, pO2_bar=1e-8).pressure_pa
k = next(iter(st)); st[k]["reference_pressure_Pa"] = 101325.0
try:
    c2 = compile_vapour_rail_catalog({"schema_version":2,"families":{"f":fam}}, emit_u0_request_rules=False)
    print("mixed P° compiled; p ratio", c2.species[ch.species_id].evaluator.evaluate(1600.0, pO2_bar=1e-8).pressure_pa/p0)
except Exception as e: print("mixed P° refused:", type(e).__name__, str(e)[:200])
# basis mix probe: replace one JANAF record with NASA absolute record
fam = copy.deepcopy(ch.family)
st = fam["physical_properties"]["species"][ch.species_id]["pressure_models"][0]["species_thermo"]
gk = [k for k in st if k.endswith("(g)")][0]
nasa = [r for r in rail.records_for(gk[:-3], "gas") if r.source_id=="nasa-glenn"]
if nasa:
    st[gk] = dict(nasa[0].species_thermo)
    try:
        c3 = compile_vapour_rail_catalog({"schema_version":2,"families":{"f":fam}}, emit_u0_request_rules=False)
        print("mixed basis compiled; p ratio", c3.species[ch.species_id].evaluator.evaluate(1600.0, pO2_bar=1e-8).pressure_pa/p0)
    except Exception as e: print("mixed basis refused:", type(e).__name__, str(e)[:200])
