"""Production catalog insertion uses the generated reaction rows."""

from simulator.config import load_config_bundle
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.channel_generator import (
    catalog_payload_from_channels,
    generate_first_batch,
)


def test_generated_rows_are_inserted_in_the_production_catalog():
    payload = load_config_bundle().vapor_pressures.catalog_payload
    expected = catalog_payload_from_channels(generate_first_batch().channels)
    actual = {key: row for key, row in payload["families"].items()
              if key.startswith("t1139_")}
    assert actual == expected["families"]
    catalog = compile_vapour_rail_catalog(payload)
    for family in actual.values():
        species_id = family["code_metadata"]["formula_id"]
        assert catalog.species[species_id].evaluator is not None
        compiled = catalog.species[species_id]
        row = family["physical_properties"]["species"][species_id]
        if row["flux_dormant"]:
            assert compiled.code_metadata.request_rule == "dormant_pending_validation"
            assert row["dormancy_reason"]["table"] == "Cu-020"
            assert row["dormancy_reason"]["temperature_K"] == 1600.0
        else:
            assert compiled.code_metadata.request_rule == "trace_source_inventory"
        assert compiled.code_metadata.source_account == "process.cleaned_melt"
        assert compiled.source_reaction_activity.allow_henrian_upper_bound
    assert sum(not next(iter(f["physical_properties"]["species"].values()))["flux_dormant"]
               for f in actual.values()) == 42
