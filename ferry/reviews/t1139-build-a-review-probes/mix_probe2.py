import sys, copy
sys.path.insert(0,'.')
from simulator.vapour_rail.channel_generator import generate_first_batch, LIQUID_PARENT_OXIDE
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.source_rail import load_source_rail, _janaf_index
rail = load_source_rail(); b = generate_first_batch(rail=rail)
ch = [c for c in b.channels if c.species_id=="t1139_B_B2O3"][0]
def comp(fam):
    return compile_vapour_rail_catalog({"schema_version":2,"families":{"f":fam}}, emit_u0_request_rules=False).species[ch.species_id].evaluator
fam = copy.deepcopy(ch.family); m = fam["physical_properties"]["species"][ch.species_id]["pressure_models"][0]
m["valid_domain"]["temperature_K"] = [1400.0, 1800.0]
p0 = comp(copy.deepcopy(fam)).evaluate(1600.0, pO2_bar=1e-8).pressure_pa
st = m["species_thermo"]
nasa = [r for r in rail.records_for("B2O3", "gas") if r.source_id=="nasa-glenn"][0]
st["B2O3(g)"] = dict(nasa.species_thermo)
try:
    p = comp(fam).evaluate(1600.0, pO2_bar=1e-8).pressure_pa
    print("MIXED BASIS (JANAF ΔfG liquid + NASA absolute gas) COMPILED: p/p0 =", p/p0, "p0=",p0, "p=",p)
except Exception as e: print("mixed basis refused:", type(e).__name__, str(e)[:300])
ji = _janaf_index()
for el, ox in LIQUID_PARENT_OXIDE.items():
    print(el, ox, "JANAF l:", [ (r.record_id, round(r.T_min_K), round(r.T_max_K)) for r in ji.native_records_for(ox, "condensed_liquid")],
          "JANAF O2 gas:", len(ji.native_records_for("O2","gas")))
