"""t-1139 first-batch generator: demand-manifest carriers, dormant, gaps."""

from __future__ import annotations

import pytest

from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.channel_generator import (
    ACTIVITY_BASIS,
    FIRST_BATCH_ELEMENTS,
    LIQUID_PARENT_OXIDE,
    PREFERRED_CARRIERS,
    catalog_payload_from_channels,
    generate_first_batch,
)
from simulator.vapour_rail.source_rail import load_source_rail


@pytest.fixture(scope="module")
def generated_batch():
    return generate_first_batch(rail=load_source_rail())


def test_first_batch_emits_every_demand_carrier_or_gap(generated_batch) -> None:
    from simulator.vapour_rail.channel_generator import (
        demand_pairs_for_element,
        load_demand_manifest,
    )

    manifest = load_demand_manifest()
    covered: dict[tuple[str, str], str] = {}
    for channel in generated_batch.channels:
        covered[(channel.element, channel.carrier)] = "channel"
    for gap in generated_batch.gaps:
        covered[(gap.element, gap.carrier)] = gap.kind
    for element in FIRST_BATCH_ELEMENTS:
        pairs = demand_pairs_for_element(element, manifest=manifest)
        assert pairs
        for pair in pairs:
            key = (element, str(pair["carrier"]))
            assert key in covered, key
    assert generated_batch.channels
    assert generated_batch.gaps


def test_preferred_oxide_carriers_compile_dormant(generated_batch) -> None:
    wanted = {
        ("Ge", "GeO"),
        ("Sn", "SnO"),
        ("B", "BO2"),
        ("B", "B2O3"),
        ("V", "VO2"),
        ("V", "VO"),
        ("Ga", "Ga"),
        ("In", "In"),
        ("Cu", "Cu"),
        ("Li", "Li"),
        ("Pb", "Pb"),
    }
    by_key = {
        (channel.element, channel.carrier): channel
        for channel in generated_batch.channels
    }
    missing = wanted - set(by_key)
    assert not missing, f"preferred carriers not evaluated: {missing}"
    payload = catalog_payload_from_channels(
        tuple(by_key[key] for key in sorted(wanted))
    )
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    for element, carrier in wanted:
        channel = by_key[(element, carrier)]
        compiled = catalog.species[channel.species_id]
        assert compiled.evaluator is not None
        assert compiled.code_metadata.request_rule == "dormant_pending_validation"
        assert compiled.code_metadata.hot_train_applicability == "not_applicable"
        assert compiled.evaluator.evaluate(1600.0, pO2_bar=1.0e-8).pressure_pa > 0.0
        assert channel.parent_oxide == LIQUID_PARENT_OXIDE[element]
        if element in ACTIVITY_BASIS:
            assert channel.activity_basis == ACTIVITY_BASIS[element]
        assert channel.selected_sources
        assert channel.native_phases
        assert channel.bands_K
        for record_id, source_id in channel.selected_sources.items():
            assert source_id
            assert record_id in channel.native_phases


def test_cu_parent_is_cu2o_with_cuo05_basis(generated_batch) -> None:
    copper = [
        channel
        for channel in generated_batch.channels
        if channel.element == "Cu"
    ]
    assert copper
    for channel in copper:
        assert channel.parent_oxide == "Cu2O"
        assert channel.activity_basis == "CuO0.5"


def test_metaborate_is_a_typed_gap(generated_batch) -> None:
    kinds = {(gap.element, gap.carrier, gap.kind) for gap in generated_batch.gaps}
    metaborate = [gap for gap in generated_batch.gaps if gap.kind == "metaborate"]
    assert metaborate
    assert any(gap.carrier.endswith("BO2") for gap in metaborate)


def test_preferred_carrier_table_matches_steer() -> None:
    assert PREFERRED_CARRIERS["Ge"] == ("GeO",)
    assert PREFERRED_CARRIERS["Sn"] == ("SnO",)
    assert PREFERRED_CARRIERS["B"] == ("BO2", "B2O3")
    assert PREFERRED_CARRIERS["V"] == ("VO2", "VO")


def _channel_domain(channel) -> list[float]:
    species = channel.family["physical_properties"]["species"][channel.species_id]
    return species["pressure_models"][0]["valid_domain"]["temperature_K"]


def test_generated_domains_contain_the_furnace_window(generated_batch) -> None:
    assert len(generated_batch.channels) == 45
    for channel in generated_batch.channels:
        low, high = _channel_domain(channel)
        assert low < 1600.0 < high, (channel.species_id, low, high)
    by_key = {
        (channel.element, channel.carrier): channel
        for channel in generated_batch.channels
    }
    ga = by_key[("Ga", "Ga")]
    assert _channel_domain(ga)[0] < 2080.0
    assert ga.family["physical_properties"]["species"][ga.species_id][
        "liquid_parent_extension"
    ] is True
    assert _channel_domain(by_key[("In", "In")])[0] < 2186.0
    assert _channel_domain(by_key[("V", "V")])[0] < 2230.0


def test_generated_channels_use_one_source_per_reaction(generated_batch) -> None:
    for channel in generated_batch.channels:
        sources = set(channel.selected_sources.values())
        assert len(sources) == 1, (
            f"{channel.species_id} mixed sources {channel.selected_sources}"
        )
