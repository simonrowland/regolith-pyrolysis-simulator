import sys, copy
sys.path.insert(0,'.')
from simulator.vapour_rail.channel_generator import generate_first_batch
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
b = generate_first_batch()
ch = [c for c in b.channels if c.species_id=="t1139_B_B2O3"][0]
def run(label, key, p):
    fam = copy.deepcopy(ch.family); st = fam["physical_properties"]["species"][ch.species_id]["pressure_models"][0]["species_thermo"]
    st[key]["reference_pressure_Pa"] = p
    try:
        compile_vapour_rail_catalog({"schema_version":2,"families":{"f":fam}}, emit_u0_request_rules=False); print(label, "COMPILED")
    except Exception as e: print(label, "refused:", type(e).__name__, str(e)[:140])
run("gas B2O3(g) @101325", "B2O3(g)", 101325.0)
run("liquid B2O3(l) @101325", "B2O3(l)", 101325.0)
run("liquid B2O3(l) @2e5", "B2O3(l)", 2e5)
print("standard_state labels:", {k: v.get("standard_state") for k, v in ch.family["physical_properties"]["species"][ch.species_id]["pressure_models"][0]["species_thermo"].items()})
