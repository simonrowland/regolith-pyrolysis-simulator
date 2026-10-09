"""t-1139 first-batch generator: demand-manifest carriers, activation, gaps."""

from __future__ import annotations

import pytest

from copy import deepcopy

from simulator.vapour_rail.catalog import (
    CatalogCompileError,
    compile_vapour_rail_catalog,
)
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


def test_preferred_oxide_carriers_compile_with_declared_activation(generated_batch) -> None:
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
    compiled_planes: set[str] = set()
    for element, carrier in wanted:
        channel = by_key[(element, carrier)]
        compiled = catalog.species[channel.species_id]
        assert compiled.evaluator is not None
        model = channel.family["physical_properties"]["species"][
            channel.species_id
        ]["pressure_models"][0]
        declared_plane = model["oxygen_fugacity_channel"]
        if compiled.evaluator.pO2_exponent != 0.0:
            assert compiled.evaluator.oxygen_fugacity_channel == declared_plane
            compiled_planes.add(declared_plane)
        else:
            assert compiled.evaluator.oxygen_fugacity_channel is None
        species = channel.family["physical_properties"]["species"][channel.species_id]
        dormant = species["flux_dormant"]
        assert dormant is bool(species.get("dormancy_reason"))
        assert compiled.code_metadata.request_rule == (
            "dormant_pending_validation" if dormant else "trace_source_inventory"
        )
        assert compiled.code_metadata.hot_train_applicability == (
            "not_applicable" if dormant else "derived_from_condensation_onset"
        )
        assert compiled.evaluator.evaluate(
            1600.0, pO2_bar=1.0e-8, source_activity=1.0e-8
        ).pressure_pa > 0.0
        assert channel.parent_oxide == LIQUID_PARENT_OXIDE[element]
        if element in ACTIVITY_BASIS:
            assert channel.activity_basis == ACTIVITY_BASIS[element]
        assert channel.selected_sources
        assert channel.native_phases
        assert channel.bands_K
        for record_id, source_id in channel.selected_sources.items():
            assert source_id
            assert record_id in channel.native_phases
    assert compiled_planes == {"intrinsic_melt", "transport_headspace"}


def test_sn_parent_is_sno(generated_batch) -> None:
    assert LIQUID_PARENT_OXIDE["Sn"] == "SnO"
    tin = [
        channel
        for channel in generated_batch.channels
        if channel.element == "Sn"
    ]
    assert tin
    for channel in tin:
        assert channel.parent_oxide == "SnO"
        species = channel.family["physical_properties"]["species"][
            channel.species_id
        ]
        assert species["liquid_parent_extension"] is True
        assert _channel_domain(channel)[0] < 1250.0


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


def test_parent_table_is_the_shared_leaf() -> None:
    from simulator.trace_oxide_parents import (
        ACTIVITY_BASIS as leaf_basis,
        LIQUID_PARENT_OXIDE as leaf_parents,
        ledger_component_key,
    )
    from simulator.vapour_rail.stoich import strip_phase

    assert leaf_parents is LIQUID_PARENT_OXIDE
    assert leaf_basis is ACTIVITY_BASIS
    assert leaf_parents["Cu"] == "Cu2O"
    assert leaf_parents["Sn"] == "SnO"
    assert ledger_component_key("Ga2O3(l)") == "Ga2O3"
    assert strip_phase("Ga2O3(l)") == ledger_component_key("Ga2O3(l)")


def test_generated_fo2_plane_follows_vapor_oxygen(generated_batch) -> None:
    from simulator.vapour_rail.catalog import _formula_atoms
    from simulator.vapour_rail.stoich import oxygen_fugacity_plane

    seen: set[tuple[bool, str]] = set()
    for channel in generated_batch.channels:
        species = channel.family["physical_properties"]["species"][
            channel.species_id
        ]
        oxygen_atoms = float(_formula_atoms(species["formula"]).get("O", 0.0))
        plane = oxygen_fugacity_plane(vapor_oxygen_atoms=oxygen_atoms)
        model = species["pressure_models"][0]
        assert model["oxygen_fugacity_channel"] == plane
        seen.add((oxygen_atoms <= 0.0, plane))
    assert (True, "intrinsic_melt") in seen
    assert (False, "transport_headspace") in seen


def test_generated_alpha_is_the_shared_upper_bound(generated_batch) -> None:
    from simulator.alpha_kinetics import ANALYTICAL_UPPER_BOUND_ALPHA_STATUS

    assert generated_batch.channels
    for channel in generated_batch.channels:
        alpha = channel.family["vaporisation_coefficients"]["evaporation_alpha"]
        assert alpha == {
            "value": 1.0,
            "status": ANALYTICAL_UPPER_BOUND_ALPHA_STATUS,
        }


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


def _ga_payload(generated_batch):
    ga = next(
        channel
        for channel in generated_batch.channels
        if channel.element == "Ga" and channel.carrier == "Ga"
    )
    return catalog_payload_from_channels((ga,))


def _ga_thermo(payload):
    return payload["families"]["t1139_Ga_Ga_family"]["physical_properties"][
        "species"
    ]["Ga"]["pressure_models"][0]["species_thermo"]


def test_compiler_rejects_mixed_gibbs_basis_and_pressure(generated_batch) -> None:
    pressure_payload = deepcopy(_ga_payload(generated_batch))
    thermo = _ga_thermo(pressure_payload)
    thermo["O2(g)"]["reference_pressure_Pa"] = 101325.0
    with pytest.raises(CatalogCompileError, match="reference_pressure"):
        compile_vapour_rail_catalog(pressure_payload, emit_u0_request_rules=False)

    basis_payload = deepcopy(_ga_payload(generated_batch))
    thermo = _ga_thermo(basis_payload)
    key = next(iter(thermo))
    thermo[key]["evaluator_family"] = "tabulated_janaf"
    thermo[key]["gibbs_convention"] = "formation_gibbs"
    thermo[key].pop("segments", None)
    with pytest.raises(CatalogCompileError, match="one Gibbs basis"):
        compile_vapour_rail_catalog(basis_payload, emit_u0_request_rules=False)

    label_payload = deepcopy(_ga_payload(generated_batch))
    thermo = _ga_thermo(label_payload)
    key = next(iter(thermo))
    thermo[key]["gibbs_convention"] = "formation_gibbs"
    with pytest.raises(CatalogCompileError, match="gibbs_convention"):
        compile_vapour_rail_catalog(label_payload, emit_u0_request_rules=False)


def test_generated_channels_use_one_source_per_reaction(generated_batch) -> None:
    for channel in generated_batch.channels:
        sources = set(channel.selected_sources.values())
        assert len(sources) == 1, (
            f"{channel.species_id} mixed sources {channel.selected_sources}"
        )
