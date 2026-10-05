import sys, collections
sys.path.insert(0,'.')
from simulator.vapour_rail.channel_generator import generate_first_batch, catalog_payload_from_channels
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.source_rail import load_source_rail
rail = load_source_rail(); b = generate_first_batch(rail=rail)
print("channels", len(b.channels), "gaps", len(b.gaps), collections.Counter(g.kind for g in b.gaps))
src = collections.Counter(tuple(sorted(set(c.selected_sources.values()))) for c in b.channels)
print("sources", src)
ext=0; mn=[]; blanks=[]
for c in b.channels:
    sp = c.family["physical_properties"]["species"][c.species_id]
    m = sp["pressure_models"][0]; lo, hi = m["valid_domain"]["temperature_K"]
    ext += bool(sp.get("liquid_parent_extension")); mn.append((c.species_id, round(lo), round(hi), sp.get("liquid_parent_extension")))
    for k, st in m["species_thermo"].items():
        if st.get("evaluator_family")=="tabulated_janaf":
            recs=[r for r in rail.records_for(k.split("(")[0], {"g":"gas","l":"condensed_liquid"}.get(k[-2],"gas")) if r.source_id=="nist-janaf-4th"]
            for r in recs:
                if getattr(r.thermo,"missing_nodes",()): blanks.append((c.species_id,k,r.record_id,r.thermo.missing_nodes))
print("extended-parent channels", ext)
for row in mn: print("  ", row)
print("JANAF participants with blank nodes:", blanks)
cat = compile_vapour_rail_catalog(catalog_payload_from_channels(b.channels), emit_u0_request_rules=False)
print("compiled generated species", len(cat.species))
# all JANAF rail records with missing nodes
from simulator.vapour_rail.source_rail import _janaf_index
