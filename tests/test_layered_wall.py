"""Layered collector and duct wall: source split, conduction, and flags.

Expected masses and heat flows are the conduction and ideal-gas laws written
here, or a second call of the production resolver. Catalog service limits
are read from the wall-materials loader. Assumed thicknesses in this file
are test inputs; they are not catalog values.
"""

from __future__ import annotations

import copy
import math

import pytest

from simulator.condensation import (
    STAGE_AREA_KEY_BY_STAGE_KEY,
    STAGE_AREA_KEY_BY_STAGE_NUMBER,
)
from simulator.cost_parameters import (
    cost_parameters_digest,
    cost_parameters_from_mapping,
    default_cost_parameters_block,
)
from simulator.layered_wall import (
    COLLECTOR_STAGE_JOIN,
    HOT_DUCT_TRAIN_KEY,
    cited_oxide_endmember_conductivity,
    cylindrical_wall_conduction,
    equilibrium_pressure_pa,
    flat_wall_conduction,
    flue_gas_energy_balance,
    in_situ_processing_cost_per_kg,
    insulation_vapour_dew_point,
    interface_temperature_table,
    normalize_layered_wall,
    price_collector_stages,
    price_duct_wall,
    reprice_stage3_cases,
    resolve_layer_properties,
    shipped_design_report,
    stack_failure_flags,
)
from simulator.optimize.doe import PHASE_ORDER_CONTINUOUS_KNOBS
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
    MBAR_PER_BAR,
    PA_PER_MBAR,
    STEFAN_BOLTZMANN,
)
from simulator.recipe import CONDENSER_STAGE_AREA_KNOB_PATHS, RecipeSchema
from simulator.reference_data.janaf import TABLES_DIR, load_table_document
from simulator.transport_constants import CARRIER_GAS_PROPERTIES
from simulator.wall_advisor import load_wall_materials


# t-1884 screening areas above the setpoint 90% case. Inputs, not prices.
_STAGE3_SCREENING_AREAS_M2 = {
    "96.9": 706.864,
    "98.4": 836.924,
    "99.6": 1142.635,
}


def _assumed_layer(role, material_id, source, thickness, density, conductivity):
    return {
        "role": role,
        "material_id": material_id,
        "source": source,
        "thickness_m": thickness,
        "density_kg_m3": density,
        "density_status": "assumed",
        "conductivity_W_m_K": conductivity,
        "conductivity_status": "assumed",
    }


def _assumed_wall() -> dict:
    return {
        "source_tag": "test-only assumed wall; not a catalog value",
        "outer_temperature_C": 40.0,
        "layers": {
            "liner": _assumed_layer(
                "liner", "bulk_zirconia_ysz", "imported", 0.001, 6000.0, 2.0
            ),
            "felt": _assumed_layer(
                "insulation", None, "imported", 0.01, 200.0, 0.2
            ),
            "structure": _assumed_layer(
                "structure", "doloma", "in_situ", 0.02, 3300.0, 3.0
            ),
        },
        "stage_bulk": {
            "sio_stage3": {
                "kind": "in_situ_product_bulk",
                "material_id": "fused_silica",
                "source": "in_situ",
            }
        },
    }


def _areas(**overrides: float) -> dict[str, float]:
    areas = {key: 0.0 for key, _number, _train in COLLECTOR_STAGE_JOIN}
    areas.update(overrides)
    return areas


def _price(wall, areas, *, in_situ_rate, density=None, thickness=None):
    parameters = cost_parameters_from_mapping()
    return price_collector_stages(
        areas,
        layered_wall=wall,
        import_delivery_cost_per_kg=parameters.import_delivery_cost_per_kg,
        in_situ_bootstrap_cost_per_kg=in_situ_rate,
        stage_wall_thickness_m=(
            parameters.condenser_stage_wall_thickness_m
            if thickness is None
            else thickness
        ),
        stage_bulk_density_kg_m3=(
            parameters.condenser_stage_bulk_density_kg_m3
            if density is None
            else density
        ),
    )


def _flag(flags, code):
    matches = [flag for flag in flags if flag["code"] == code]
    assert len(matches) == 1
    return matches[0]


def _layer(layers, role):
    found = [layer for layer in layers if layer.get("role") == role]
    assert len(found) == 1
    return found[0]


def test_area_join_uses_the_condensation_registries() -> None:
    joined = {area_key for area_key, _number, _train in COLLECTOR_STAGE_JOIN}
    assert "terminal" not in joined
    for area_key, number, train_key in COLLECTOR_STAGE_JOIN:
        if number is None:
            assert STAGE_AREA_KEY_BY_STAGE_KEY[train_key] == area_key
        else:
            assert STAGE_AREA_KEY_BY_STAGE_NUMBER[number] == area_key


def test_liner_material_is_not_an_optimizer_knob() -> None:
    paths = [
        *PHASE_ORDER_CONTINUOUS_KNOBS,
        *CONDENSER_STAGE_AREA_KNOB_PATHS,
        *(spec.path for spec in RecipeSchema().search_allowlist),
    ]
    banned = (
        "layered_wall",
        "in_situ_bootstrap",
        "furnace_material",
        "material_id",
        "wall_material",
    )
    for path in paths:
        joined = ".".join(path)
        for token in banned:
            assert token not in joined


def _unset_property_wall() -> dict:
    wall = _assumed_wall()
    for layer in wall["layers"].values():
        layer["thickness_m"] = None
        layer["density_kg_m3"] = None
        layer["density_status"] = None
        layer["conductivity_W_m_K"] = None
        layer["conductivity_status"] = None
    wall["layers"]["felt"]["material_id"] = None
    wall["layers"]["felt"]["source"] = None
    return wall


def test_missing_conductivity_density_and_thickness_refuse() -> None:
    priced = _price(_unset_property_wall(), _areas(fe_stage1=1.0), in_situ_rate=None)
    stage = priced["collector_stage_costs"]["fe_stage1"]

    assert priced["status"] == "unavailable"
    assert stage["cost_usd"]["value"] is None
    assert stage["cost_usd"]["status"] == "unavailable"
    reason = stage["cost_usd"]["reason"]
    assert "thickness_unset:liner" in reason
    # Liner and structure density stay on the cited catalog rows when the
    # override is cleared. Only the felt, whose material id is removed,
    # is catalog_missing. The stage still refuses.
    assert "density_missing:liner" not in reason
    assert "density_missing:structure" not in reason
    assert "catalog_missing:insulation" in reason
    assert "thickness_unset:insulation" in reason
    assert "density_missing:insulation" in reason
    assert "source_unset:insulation" in reason
    assert "thickness_unset:structure" in reason
    assert stage["imported_usd"]["value"] is None

    missing_k = flat_wall_conduction(
        [{"role": "liner", "thickness_m": 0.01}],
        200.0,
        40.0,
    )
    assert missing_k["status"] == "unavailable"
    assert "thermal_conductivity_missing:liner" in missing_k["reason"]
    assert missing_k["heat_flux_W_m2"]["value"] is None

    zero_k = flat_wall_conduction(
        [{"role": "liner", "thickness_m": 0.01, "conductivity_W_m_K": 0.0}],
        200.0,
        40.0,
    )
    assert zero_k["status"] == "unavailable"
    assert "thermal_conductivity_invalid:liner" in zero_k["reason"]
    assert zero_k["heat_flux_W_m2"]["value"] is None

    negative_thickness = flat_wall_conduction(
        [{"role": "liner", "thickness_m": -0.01, "conductivity_W_m_K": 2.0}],
        200.0,
        40.0,
    )
    assert negative_thickness["status"] == "unavailable"
    assert "thickness_invalid:liner" in negative_thickness["reason"]
    assert negative_thickness["heat_flux_W_m2"]["value"] is None


def test_zero_area_is_zero_mass_when_properties_are_missing() -> None:
    shipped = cost_parameters_from_mapping().layered_wall
    priced = _price(shipped, _areas(), in_situ_rate=None)
    stage = priced["collector_stage_costs"]["fe_stage1"]

    assert priced["status"] == "ok"
    assert priced["hardware_capital_cost_usd"] == 0.0
    assert stage["imported_kg"] == 0.0
    assert stage["in_situ_kg"] == 0.0
    assert stage["imported_usd"] == 0.0
    assert stage["in_situ_usd"] == 0.0
    assert any(
        flag["code"] == "liner_gas_tightness_uncertified"
        for flag in stage["failure_flags"]
    )


def test_stage3_bulk_is_in_situ_silica_and_scales_with_area() -> None:
    parameters = cost_parameters_from_mapping()
    wall = parameters.layered_wall
    small = _price(wall, _areas(sio_stage3=2.0), in_situ_rate=None)
    large = _price(wall, _areas(sio_stage3=4.0), in_situ_rate=None)
    before = small["collector_stage_costs"]["sio_stage3"]
    after = large["collector_stage_costs"]["sio_stage3"]
    thickness = parameters.condenser_stage_wall_thickness_m
    density = parameters.condenser_stage_bulk_density_kg_m3["sio_stage3"]

    assert before["pricing"] == "in_situ_product_bulk"
    assert before["source"] == "in_situ"
    assert before["material_id"] == "fused_silica"
    assert before["imported_kg"] == 0.0
    assert before["imported_usd"] == 0.0
    silica = in_situ_processing_cost_per_kg(
        "silica", parameters.electricity_cost_per_kWh
    )
    assert before["in_situ_kg"] == pytest.approx(2.0 * thickness * density)
    assert after["in_situ_kg"] == pytest.approx(before["in_situ_kg"] * 2.0)
    assert before["in_situ_usd"] == pytest.approx(
        before["in_situ_kg"] * silica["usd_per_kg"]
    )
    assert after["in_situ_usd"] == pytest.approx(before["in_situ_usd"] * 2.0)
    assert before["wall_thickness_certification"] == "uncertified_cost_parameter_slot"
    assert before["bulk_density_certification"] == "uncertified_cost_parameter_slot"


def test_owner_set_in_situ_rate_prices_known_mass_and_zero_is_not_unset() -> None:
    parameters = cost_parameters_from_mapping()
    wall = copy.deepcopy(dict(parameters.layered_wall))
    # No stage-specific rate: the scalar is the price. The shipped wall's
    # derived silica number is a different test.
    bulk = dict(wall["stage_bulk"]["sio_stage3"])
    bulk["cost_per_kg_usd"] = None
    bulk["cost_status"] = None
    wall["stage_bulk"] = {"sio_stage3": bulk}
    area = 2.0
    thickness = parameters.condenser_stage_wall_thickness_m
    density = parameters.condenser_stage_bulk_density_kg_m3["sio_stage3"]
    mass = area * thickness * density
    priced = _price(wall, _areas(sio_stage3=area), in_situ_rate=50.0)
    zero_rate = _price(wall, _areas(sio_stage3=area), in_situ_rate=0.0)
    stage = priced["collector_stage_costs"]["sio_stage3"]
    zero_stage = zero_rate["collector_stage_costs"]["sio_stage3"]

    assert stage["in_situ_usd"] == pytest.approx(mass * 50.0)
    assert stage["imported_usd"] == 0.0
    assert zero_stage["in_situ_usd"] == 0.0
    assert zero_stage["cost_usd"] == 0.0


def test_imported_dollars_follow_imported_mass() -> None:
    parameters = cost_parameters_from_mapping()
    area = 1.0
    priced = _price(_assumed_wall(), _areas(fe_stage1=area), in_situ_rate=50.0)
    stage = priced["collector_stage_costs"]["fe_stage1"]
    liner = next(layer for layer in stage["layers"] if layer["role"] == "liner")
    felt = next(layer for layer in stage["layers"] if layer["role"] == "insulation")
    structure = next(layer for layer in stage["layers"] if layer["role"] == "structure")
    liner_mass = area * 0.001 * 6000.0
    felt_mass = area * 0.01 * 200.0
    structure_mass = area * 0.02 * 3300.0

    assert liner["mass_kg"] == pytest.approx(liner_mass)
    assert felt["mass_kg"] == pytest.approx(felt_mass)
    assert structure["mass_kg"] == pytest.approx(structure_mass)
    assert felt["mass_reason"] is None
    assert stage["imported_kg"] == pytest.approx(liner_mass + felt_mass)
    assert stage["in_situ_kg"] == pytest.approx(structure_mass)
    assert stage["imported_usd"] == pytest.approx(
        (liner_mass + felt_mass) * parameters.import_delivery_cost_per_kg
    )
    assert stage["in_situ_usd"] == pytest.approx(structure_mass * 50.0)
    assert priced["status"] == "ok"


def test_null_felt_material_keeps_catalog_missing_and_prices_an_override() -> None:
    wall = _assumed_wall()
    normalized = normalize_layered_wall(wall)
    felt = next(layer for layer in normalized["layers"] if layer["role"] == "insulation")
    resolved = resolve_layer_properties(felt)

    assert felt["material_id"] is None
    assert resolved["unknown_reason"] == "catalog_missing:insulation"
    assert resolved["density_kg_m3"] == pytest.approx(200.0)


def test_unknown_material_and_unknown_source_refuse() -> None:
    wall = _assumed_wall()
    wall["layers"]["liner"]["material_id"] = "not-a-wall-material"
    priced = _price(wall, _areas(fe_stage1=1.0), in_situ_rate=50.0)
    stage = priced["collector_stage_costs"]["fe_stage1"]

    assert stage["cost_usd"]["status"] == "unavailable"
    assert "unknown_material:not-a-wall-material" in stage["cost_usd"]["reason"]

    product = _assumed_wall()
    product["stage_bulk"]["sio_stage3"]["material_id"] = "not-a-wall-material"
    parameters = cost_parameters_from_mapping()
    refused = _price(
        product,
        _areas(sio_stage3=1.0),
        in_situ_rate=50.0,
        density=parameters.condenser_stage_bulk_density_kg_m3,
    )
    assert "unknown_material:not-a-wall-material" in (
        refused["collector_stage_costs"]["sio_stage3"]["cost_usd"]["reason"]
    )

    bad_source = _assumed_wall()
    bad_source["layers"]["liner"]["source"] = "local"
    with pytest.raises(ValueError, match="imported, in_situ, or null"):
        normalize_layered_wall(bad_source)


def test_flat_wall_matches_fouriers_law_and_two_half_layers() -> None:
    thickness_m = 0.01
    conductivity = 2.0
    hot_C = 200.0
    cold_C = 100.0
    single = flat_wall_conduction(
        [{
            "role": "liner",
            "thickness_m": thickness_m,
            "conductivity_W_m_K": conductivity,
        }],
        hot_C,
        cold_C,
    )
    # Fourier's law for one slab: q = k (T_hot - T_cold) / t.
    expected_flux = conductivity * (hot_C - cold_C) / thickness_m
    assert single["status"] == "ok"
    assert single["heat_flux_W_m2"] == pytest.approx(expected_flux)
    assert single["interface_temperatures_C"][-1]["temperature_C"] == pytest.approx(
        cold_C
    )

    halves = flat_wall_conduction(
        [
            {
                "role": "liner",
                "thickness_m": thickness_m / 2.0,
                "conductivity_W_m_K": conductivity,
            },
            {
                "role": "structure",
                "thickness_m": thickness_m / 2.0,
                "conductivity_W_m_K": conductivity,
            },
        ],
        hot_C,
        cold_C,
    )
    assert halves["heat_flux_W_m2"] == pytest.approx(single["heat_flux_W_m2"])


def test_thin_cylindrical_wall_approaches_the_flat_flux() -> None:
    radius_m = 1.0
    thickness_m = 0.001
    conductivity = 2.0
    hot_C = 200.0
    cold_C = 100.0
    layer = {
        "role": "liner",
        "thickness_m": thickness_m,
        "conductivity_W_m_K": conductivity,
    }
    cylindrical = cylindrical_wall_conduction([layer], radius_m, hot_C, cold_C)
    flat_flux = conductivity * (hot_C - cold_C) / thickness_m
    cylindrical_flux = cylindrical["heat_flow_W_per_m"] / (2.0 * math.pi * radius_m)

    assert cylindrical["status"] == "ok"
    assert abs(cylindrical_flux - flat_flux) / flat_flux < 1e-3

    length_m = 2.0
    density = 1000.0
    wall = {
        "source_tag": "test-only thin shell",
        "outer_temperature_C": cold_C,
        "layers": {
            "liner": {
                "role": "liner",
                "material_id": "bulk_zirconia_ysz",
                "source": "imported",
                "thickness_m": thickness_m,
                "density_kg_m3": density,
                "density_status": "assumed",
                "conductivity_W_m_K": conductivity,
                "conductivity_status": "assumed",
            },
            "felt": {
                "role": "insulation",
                "material_id": "bulk_zirconia_ysz",
                "source": "imported",
                "thickness_m": thickness_m,
                "density_kg_m3": density,
                "density_status": "assumed",
                "conductivity_W_m_K": conductivity,
                "conductivity_status": "assumed",
            },
            "structure": {
                "role": "structure",
                "material_id": "bulk_zirconia_ysz",
                "source": "in_situ",
                "thickness_m": thickness_m,
                "density_kg_m3": density,
                "density_status": "assumed",
                "conductivity_W_m_K": conductivity,
                "conductivity_status": "assumed",
            },
        },
        "stage_bulk": {},
    }
    # The felt and structure here are the same thin shell repeated so each
    # layer's own mass can be checked. The unrolled sheet is 2 π r L t ρ.
    duct = price_duct_wall(
        diameter_m=radius_m * 2.0,
        length_m=length_m,
        layered_wall=wall,
        import_delivery_cost_per_kg=1.0,
        in_situ_bootstrap_cost_per_kg=1.0,
    )
    unrolled = 2.0 * math.pi * radius_m * length_m * thickness_m * density
    liner_mass = duct["layers"][0]["mass_kg"]
    assert abs(liner_mass - unrolled) / unrolled < 1e-3
    assert duct["hot_duct_area_price_applied"] is False


def test_cao_recession_follows_the_gas_facing_material() -> None:
    catalog = load_wall_materials()["materials"]

    def resolved(material_id, role, *, thickness=0.001):
        layer = normalize_layered_wall({
            "source_tag": "test-only facing swap",
            "outer_temperature_C": 40.0,
            "layers": {
                "liner": _assumed_layer(
                    "liner",
                    material_id if role == "liner" else "bulk_zirconia_ysz",
                    "imported",
                    thickness,
                    6000.0,
                    2.0,
                ),
                "felt": _assumed_layer(
                    "insulation", None, "imported", 0.01, 200.0, 0.2
                ),
                "structure": _assumed_layer(
                    "structure",
                    material_id if role == "structure" else "bulk_zirconia_ysz",
                    "in_situ",
                    0.02,
                    3300.0,
                    3.0,
                ),
            },
            "stage_bulk": {},
        })["layers"]
        return [resolve_layer_properties(item, materials=catalog) for item in layer]

    behind = stack_failure_flags(
        resolved("doloma", "structure"),
        materials=catalog,
    )
    exposed = stack_failure_flags(
        resolved("doloma", "liner"),
        materials=catalog,
    )
    neither = stack_failure_flags(
        resolved("bulk_zirconia_ysz", "liner"),
        materials=catalog,
    )

    assert _flag(behind, "cao_vacuum_recession_screen_not_applied")["reason"] == (
        "shell_not_gas_facing"
    )
    assert _flag(behind, "cao_sio2_attack_if_liner_breached")["citations"] == [
        "W:S7",
        "W:S33",
    ]
    assert "cao_on_gas_path" not in {flag["code"] for flag in behind}
    assert "liner_thickness_unset" not in {flag["code"] for flag in behind}
    assert _flag(exposed, "cao_vacuum_recession_applies")["citations"] == ["t-476"]
    assert _flag(exposed, "cao_on_gas_path")["citations"] == ["t-476"]
    assert _flag(neither, "cao_vacuum_recession_not_on_gas_path")["reason"] == (
        "gas_facing_is_not_cao"
    )

    product = _assumed_wall()
    product["stage_bulk"]["sio_stage3"]["material_id"] = "doloma"
    priced = _price(product, _areas(sio_stage3=1.0), in_situ_rate=50.0)
    flags = priced["collector_stage_costs"]["sio_stage3"]["failure_flags"]
    assert _flag(flags, "cao_vacuum_recession_applies")["citations"] == ["t-476"]
    assert "cao_on_gas_path" in {flag["code"] for flag in flags}

    shipped = _price(
        cost_parameters_from_mapping().layered_wall,
        _areas(sio_stage3=1.0),
        in_situ_rate=None,
    )
    shipped_flags = shipped["collector_stage_costs"]["sio_stage3"]["failure_flags"]
    assert _flag(
        shipped_flags, "cao_vacuum_recession_not_on_gas_path"
    )["reason"] == "gas_facing_is_not_cao"
    assert _flag(
        shipped_flags, "vapour_condensation_inside_insulation_unchecked"
    )["status"] == "not_applicable_no_insulation_layer"


def test_interface_table_uses_cited_service_and_refuses_a_missing_outer_temperature() -> None:
    catalog = load_wall_materials()["materials"]
    report = shipped_design_report()
    table = report["interface_temperatures"]
    ysz = catalog["bulk_zirconia_ysz"]["service_temp"]
    silica = catalog["fused_silica"]["service_temp"]
    duct_liner = table["hot_duct"]["layers"][0]
    product = table["sio_stage3"]["layers"][0]

    assert table["hot_duct"]["hot_face_C"] < ysz["continuous_C"]
    assert "within_continuous_service" in duct_liner["hot_face_flags"]
    assert duct_liner["service"]["citations"] == list(ysz["citations"])
    assert table["hot_duct"]["shell_temperature_status"] == (
        "shell_temperature_unchecked"
    )
    assert table["hot_duct"]["conduction_status"] == "estimated_known_layers_only"
    assert table["hot_duct"]["conduction_authority"] == "estimated_known_layers_only"
    felt = next(
        layer for layer in table["hot_duct"]["layers"] if layer["role"] == "insulation"
    )
    assert felt["outer_temperature_C"] == pytest.approx(
        report["interface_temperatures"]["hot_duct"]["boundary_temperature_C"]
    )
    assert "hot_face_above_max_operating" in felt["hot_face_flags"]
    assert felt["conductivity_flag"] == "k_extended_off_cited_temperature"
    structure = next(
        layer for layer in table["hot_duct"]["layers"] if layer["role"] == "structure"
    )
    assert structure["conductivity_W_m_K"] is None
    assert structure["inner_temperature_C"]["reason"] == (
        "structure_omitted_from_temperature_walk"
    )
    assert table["sio_stage3"]["hot_face_C"] == silica["max_operating_C"]
    assert table["sio_stage3"]["hot_face_C"] > silica["continuous_C"]
    assert table["sio_stage3"]["hot_face_C"] > silica["degradation_onset_C"]
    assert "hot_face_at_max_operating" in product["hot_face_flags"]
    assert "hot_face_above_continuous_service" in product["hot_face_flags"]
    assert "hot_face_above_max_operating" not in product["hot_face_flags"]
    assert table["sio_stage3"]["shell_temperature_status"] == "not_a_cao_shell"
    assert product["service"]["citations"] == list(silica["citations"])

    bands = {HOT_DUCT_TRAIN_KEY: (1400.0, 1600.0)}
    for _area, _number, train_key in COLLECTOR_STAGE_JOIN:
        bands[train_key] = (100.0, 200.0)
    priced = interface_temperature_table(_assumed_wall(), bands, materials=catalog)
    structure = next(
        layer for layer in priced["hot_duct"]["layers"] if layer["role"] == "structure"
    )
    assert priced["hot_duct"]["conduction_status"] == "ok"
    assert priced["hot_duct"]["shell_temperature_status"] == (
        "shell_within_cited_service"
    )
    assert structure["inner_temperature_C"] < catalog["doloma"]["service_temp"][
        "max_operating_C"
    ]


def test_flue_gas_balance_uses_printed_janaf_neighbors_and_ideal_gas_density() -> None:
    document = load_table_document(TABLES_DIR / "N-023.yaml")
    printed = {}
    for row in document["table"]["values"]:
        heat = row.get("heat_capacity")
        temperature = row.get("temperature")
        if not isinstance(heat, dict) or not isinstance(temperature, dict):
            continue
        if heat.get("value") is None:
            continue
        printed[float(temperature["value"])] = float(heat["value"])

    parameters = cost_parameters_from_mapping()
    balance = flue_gas_energy_balance(
        gas_temperature_C=1670.0,
        pressure_mbar_low=5.0,
        pressure_mbar_high=15.0,
        pipe_diameter_m=0.5,
        carrier_gas="N2",
        layered_wall=parameters.layered_wall,
        campaign="C4",
    )
    below = balance["n2_cp_bounds"]["below"]
    above = balance["n2_cp_bounds"]["above"]
    temperature_K = 1670.0 + CELSIUS_TO_KELVIN_OFFSET
    molar_mass = CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol
    low_density = (5.0 * PA_PER_MBAR) * molar_mass / (GAS_CONSTANT * temperature_K)

    assert below["temperature_K"] != temperature_K
    assert above["temperature_K"] != temperature_K
    assert below["cp_J_per_mol_K"] == pytest.approx(printed[below["temperature_K"]])
    assert above["cp_J_per_mol_K"] == pytest.approx(printed[above["temperature_K"]])
    assert balance["n2_cp_bounds"]["cp_temperature_status"] == (
        "cp_temperature_is_printed_neighbor_not_the_gas_temperature"
    )
    assert balance["ideal_gas_density_kg_m3"]["low_mbar"] == pytest.approx(low_density)
    assert balance["ideal_gas_density_kg_m3"]["high_mbar"] == pytest.approx(
        low_density * 3.0
    )
    assert balance["verdict"] == "hot_face_hold_undetermined"
    assert balance["conductive_loss"]["status"] == "estimated_known_layers_only"
    assert balance["trace_heating_power_W_per_m"]["reason"] == "mass_flow_unset"
    assert balance["trace_heating_power_W_per_m"]["value"] is None
    comparison = balance["loss_vs_black_body"]
    assert comparison["not_an_upper_bound"] is True
    assert comparison["not_a_certified_deficit"] is True
    assert comparison["relation"] == "estimated_loss_below_black_body_ceiling"
    assert comparison["inner_flux_W_m2"] < comparison["black_body_flux_W_m2"]
    assert balance["radiation_delivered_W_m2"]["reason"] == "view_factor_unset"
    assert len(balance["volumetric_heat_J_per_m3_K"]["by_pressure"]["low_mbar"]) == 2

    refused = flue_gas_energy_balance(
        gas_temperature_C=1400.0,
        pressure_mbar_low=5.0,
        pressure_mbar_high=15.0,
        pipe_diameter_m=0.5,
        carrier_gas="pN2",
        layered_wall=parameters.layered_wall,
        campaign="C2A_continuous",
    )
    assert refused["n2_cp_bounds"]["reason"] == "carrier_cp_table_refused:pN2"
    assert refused["ideal_gas_density_kg_m3"]["status"] == "unavailable"


def test_shipped_design_report_names_the_sweep_and_reprices_stage3_cases() -> None:
    report = shipped_design_report()
    campaigns = {
        row["campaign"]: row for row in report["flue_gas_energy_balance"]
    }
    bare = {
        row["campaign"]: row for row in report["pressure_bands_without_flue_state"]
    }

    assert set(campaigns) == {"C4", "C6"}
    assert campaigns["C4"]["pressure_mbar"] == [5.0, 15.0]
    assert campaigns["C6"]["pressure_mbar"] == [5.0, 15.0]
    assert campaigns["C4"]["gas_temperature_C"] != campaigns["C6"]["gas_temperature_C"]
    assert bare["C2A_continuous"]["reason"] == "flue_gas_temperature_unset"
    assert bare["C2A_staged"]["carrier_gas"] == "pN2"
    doloma = report["in_situ_cost_derivation"]["doloma"]
    silica = report["in_situ_cost_derivation"]["silica"]
    assert report["in_situ_bootstrap_cost_per_kg"] == pytest.approx(doloma["usd_per_kg"])
    assert doloma["usd_per_kg"] == pytest.approx(
        doloma["specific_energy_MJ_per_kg"]
        * 1000.0
        / doloma["kj_per_kwh"]
        / doloma["thermal_efficiency"]
        * doloma["electricity_usd_per_kWh"]
    )
    assert report["hot_duct_per_metre"]["duct_price_basis"] == (
        "layered_cylindrical_mass"
    )
    assert report["hot_duct_per_metre"]["hot_duct_area_price_applied"] is False
    assert report["hot_duct_per_metre"]["length_certified"] is False

    base_area = report["collector_stages"]["collector_stage_costs"]["sio_stage3"][
        "area_m2"
    ]
    cases = reprice_stage3_cases({"90": base_area, **_STAGE3_SCREENING_AREAS_M2})
    parameters = cost_parameters_from_mapping()
    thickness = parameters.condenser_stage_wall_thickness_m
    density = parameters.condenser_stage_bulk_density_kg_m3["sio_stage3"]
    ordered = [("90", base_area), *_STAGE3_SCREENING_AREAS_M2.items()]
    for name, area in ordered:
        stage = cases[name]["collector_stage_costs"]["sio_stage3"]
        assert stage["area_m2"] == pytest.approx(area)
        assert stage["in_situ_kg"] == pytest.approx(area * thickness * density)
        assert stage["imported_kg"] == 0.0
        assert stage["imported_usd"] == 0.0
        assert stage["in_situ_usd"] == pytest.approx(
            stage["in_situ_kg"] * silica["usd_per_kg"]
        )
        fe_cost = cases[name]["collector_stage_costs"]["fe_stage1"]["cost_usd"]
        assert isinstance(fe_cost, float)
        assert fe_cost > 0.0


def test_in_situ_rate_none_zero_and_positive_split_the_cost_key() -> None:
    base = default_cost_parameters_block()
    zero = copy.deepcopy(base)
    positive = copy.deepcopy(base)
    zero["parameters"]["in_situ_bootstrap_cost_per_kg"]["value"] = 0.0
    positive["parameters"]["in_situ_bootstrap_cost_per_kg"]["value"] = 50.0
    moved = copy.deepcopy(base)
    moved["parameters"]["layered_wall"]["outer_temperature_C"] = 25.0

    electricity = base["parameters"]["electricity_cost_per_kWh"]["value"]
    derived = cost_parameters_from_mapping(base)
    assert derived.in_situ_bootstrap_cost_per_kg == pytest.approx(
        in_situ_processing_cost_per_kg("doloma", electricity)["usd_per_kg"]
    )
    assert cost_parameters_from_mapping(zero).in_situ_bootstrap_cost_per_kg == 0.0
    unset = copy.deepcopy(base)
    unset["parameters"]["in_situ_bootstrap_cost_per_kg"]["status"] = "owner_unset"
    unset["parameters"]["in_situ_bootstrap_cost_per_kg"]["value"] = None
    unset["parameters"]["layered_wall"]["stage_bulk"]["sio_stage3"]["cost_status"] = (
        "owner_unset"
    )
    unset["parameters"]["layered_wall"]["stage_bulk"]["sio_stage3"][
        "cost_per_kg_usd"
    ] = None
    unset_parameters = cost_parameters_from_mapping(unset)
    assert unset_parameters.in_situ_bootstrap_cost_per_kg is None
    assert (
        unset_parameters.layered_wall["stage_bulk"]["sio_stage3"]["cost_per_kg_usd"]
        is None
    )
    doubled = copy.deepcopy(base)
    doubled["parameters"]["electricity_cost_per_kWh"]["value"] = electricity * 2.0
    doubled_parameters = cost_parameters_from_mapping(doubled)
    assert doubled_parameters.in_situ_bootstrap_cost_per_kg == pytest.approx(
        derived.in_situ_bootstrap_cost_per_kg * 2.0
    )
    assert doubled_parameters.layered_wall["stage_bulk"]["sio_stage3"][
        "cost_per_kg_usd"
    ] == pytest.approx(
        derived.layered_wall["stage_bulk"]["sio_stage3"]["cost_per_kg_usd"] * 2.0
    )
    assert cost_parameters_digest(base) != cost_parameters_digest(zero)
    assert cost_parameters_digest(zero) != cost_parameters_digest(positive)
    assert cost_parameters_digest(base) != cost_parameters_digest(unset)
    assert cost_parameters_digest(base) != cost_parameters_digest(doubled)
    digests = {
        "setpoints": "setpoints-digest",
        "feedstocks": "feedstock-digest",
        "foulant_thermo": "foulant-thermo-digest",
        "materials": "materials-digest",
        "vapor_pressures": "vapor-digest",
        "species_catalog": "species-catalog-digest",
        "profile": "profile-digest",
    }

    def spec(block):
        return EvalSpec(
            recipe_id="recipe-id",
            feedstock_recipe_digest="feedstock-recipe-digest",
            feedstock_id="lunar_mare_low_ti",
            profile_id="profile-id",
            fidelity="internal-analytical",
            code_version=current_code_version(),
            data_digests=digests,
            cost_parameters=block,
        )

    assert cache_key(spec(base)) != cache_key(spec(zero))
    assert cache_key(spec(zero)) != cache_key(spec(positive))
    assert cache_key(spec(base)) != cache_key(spec(moved))
    assert cache_key(spec(base)) != cache_key(spec(unset))
    assert cache_key(spec(base)) != cache_key(spec(doubled))


def _printed_enthalpy_rows(table_id: str) -> list[tuple[float, float]]:
    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    rows = []
    for row in document["table"]["values"]:
        enthalpy = row.get("enthalpy_increment")
        temperature = row.get("temperature")
        if not isinstance(enthalpy, dict) or not isinstance(temperature, dict):
            continue
        if enthalpy.get("value") is None:
            continue
        rows.append((float(temperature["value"]), float(enthalpy["value"])))
    return rows


def _enthalpy_bracket(
    rows: list[tuple[float, float]],
    target_K: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    below = max((row for row in rows if row[0] < target_K), key=lambda row: row[0])
    above = min((row for row in rows if row[0] > target_K), key=lambda row: row[0])
    return below, above


def _endpoint_usd(specific_energy_MJ_per_kg: float, record: dict) -> float:
    from simulator.thermal_budget import KJ_PER_KWH

    assert record["kj_per_kwh"] == KJ_PER_KWH
    return (
        specific_energy_MJ_per_kg
        * 1000.0
        / KJ_PER_KWH
        / record["thermal_efficiency"]
        * record["electricity_usd_per_kWh"]
    )


def test_processing_cost_lies_between_printed_janaf_neighbors() -> None:
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.reference_data.janaf import table_fusion_transition

    electricity = cost_parameters_from_mapping().electricity_cost_per_kWh
    doloma = in_situ_processing_cost_per_kg("doloma", electricity)
    silica = in_situ_processing_cost_per_kg("silica", electricity)
    molar_mass = {
        species: float(resolve_species_formula(species).molar_mass_g_per_mol())
        for species in ("CaO", "MgO", "SiO2")
    }
    for table_id in ("Ca-027", "Mg-008", "O-037"):
        document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
        assert table_fusion_transition(document["table"]) is None

    cao_below, cao_above = _enthalpy_bracket(
        _printed_enthalpy_rows("Ca-027"), doloma["process_temperature_K"]
    )
    mgo_below, mgo_above = _enthalpy_bracket(
        _printed_enthalpy_rows("Mg-008"), doloma["process_temperature_K"]
    )
    assert cao_below[0] == mgo_below[0]
    assert cao_above[0] == mgo_above[0]
    assert cao_below[1] < cao_above[1]
    assert mgo_below[1] < mgo_above[1]
    mix_mass = molar_mass["CaO"] + molar_mass["MgO"]
    cool_specific = (cao_below[1] + mgo_below[1]) / mix_mass
    hot_specific = (cao_above[1] + mgo_above[1]) / mix_mass
    cool_usd = _endpoint_usd(cool_specific, doloma)
    hot_usd = _endpoint_usd(hot_specific, doloma)
    assert cool_usd < doloma["usd_per_kg"] < hot_usd

    quartz_below, quartz_above = _enthalpy_bracket(
        _printed_enthalpy_rows("O-037"), silica["process_temperature_K"]
    )
    assert quartz_below[1] < quartz_above[1]
    cool_silica = _endpoint_usd(quartz_below[1] / molar_mass["SiO2"], silica)
    hot_silica = _endpoint_usd(quartz_above[1] / molar_mass["SiO2"], silica)
    assert cool_silica < silica["usd_per_kg"] < hot_silica
    catalog_limit = load_wall_materials()["materials"]["fused_silica"]["service_temp"][
        "max_operating_C"
    ]
    assert silica["process_temperature_K"] == pytest.approx(
        float(catalog_limit) + CELSIUS_TO_KELVIN_OFFSET
    )


def test_cited_conductivity_interpolates_inside_the_grid_and_extends_outside() -> None:
    catalog = load_wall_materials()["materials"]
    felt = {
        "role": "insulation",
        "material_id": "kaowool_rcf_blanket",
        "source": "imported",
        "thickness_m": 0.025,
    }
    curve = sorted(
        (float(point["temperature_K"]), float(point["value_W_m_K"]))
        for point in catalog["kaowool_rcf_blanket"]["thermal_conductivity"]
    )
    midpoint = (curve[0][0] + curve[1][0]) / 2.0
    interpolated = resolve_layer_properties(
        felt, materials=catalog, temperature_K=midpoint
    )
    low_k, high_k = sorted((curve[0][1], curve[1][1]))
    assert interpolated["conductivity_flag"] == "k_interpolated_not_on_printed_grid"
    assert low_k < interpolated["conductivity_W_m_K"] < high_k
    exact = resolve_layer_properties(
        felt, materials=catalog, temperature_K=curve[0][0]
    )
    assert exact["conductivity_flag"] == "cited_grid"
    assert exact["conductivity_W_m_K"] == pytest.approx(curve[0][1])
    extended = resolve_layer_properties(
        felt, materials=catalog, temperature_K=1873.15
    )
    assert extended["conductivity_flag"] == "k_extended_off_cited_temperature"
    assert extended["conductivity_W_m_K"] == pytest.approx(curve[-1][1])
    unpriced = resolve_layer_properties(felt, materials=catalog)
    assert unpriced["density_kg_m3"] == pytest.approx(
        catalog["kaowool_rcf_blanket"]["density_kg_m3"]
    )
    assert unpriced["conductivity_reason"].startswith(
        "thermal_conductivity_temperature_unset"
    )

    ysz_point = catalog["bulk_zirconia_ysz"]["thermal_conductivity"][0]
    liner = {
        "role": "liner",
        "material_id": "bulk_zirconia_ysz",
        "source": "imported",
        "thickness_m": 0.005,
    }
    off_grid = resolve_layer_properties(
        liner,
        materials=catalog,
        temperature_K=float(ysz_point["temperature_K"]) + 100.0,
    )
    assert off_grid["conductivity_flag"] == "k_extended_off_cited_temperature"
    assert off_grid["conductivity_W_m_K"] == pytest.approx(ysz_point["value_W_m_K"])
    on_grid = resolve_layer_properties(
        liner,
        materials=catalog,
        temperature_K=float(ysz_point["temperature_K"]),
    )
    assert on_grid["conductivity_flag"] == "cited_grid"
    assert on_grid["conductivity_W_m_K"] == pytest.approx(ysz_point["value_W_m_K"])


def test_cited_density_that_disagrees_with_the_catalog_raises() -> None:
    catalog = load_wall_materials()["materials"]
    catalog_density = catalog["bulk_zirconia_ysz"]["density_kg_m3"]
    layer = {
        "role": "liner",
        "material_id": "bulk_zirconia_ysz",
        "density_kg_m3": float(catalog_density) + 1.0,
        "density_status": "cited",
        "density_citation": "not the catalog number",
    }
    with pytest.raises(ValueError, match="density_conflict"):
        resolve_layer_properties(layer, materials=catalog)


def test_felt_dew_point_stays_refused_because_live_pressures_are_equilibrium() -> None:
    import yaml

    refused = insulation_vapour_dew_point(100.0, 40.0)
    assert refused["status"] == "unavailable"
    assert refused["reason"] == "partial_pressure_unset"
    assert refused["felt_inner_temperature_C"] == pytest.approx(100.0)
    assert refused["felt_outer_temperature_C"] == pytest.approx(40.0)

    from pathlib import Path

    catalog = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml").read_text(
            encoding="utf-8"
        )
    )
    families = catalog["families"]
    expected = {
        "SiO": "oxide_vapors_sio_family",
        "Na": "metals_na_family",
        "K": "metals_k_family",
    }
    for species, family in expected.items():
        models = families[family]["physical_properties"]["species"][species][
            "pressure_models"
        ]
        assert "equilibrium_partial_pressure" in {
            model["pressure_kind"] for model in models
        }
        assert refused["species"][species]["family"] == family
        assert refused["species"][species]["reason"] == "equilibrium_not_pure_saturation"


def test_radiative_outer_flux_matches_stefan_boltzmann() -> None:
    report = shipped_design_report()
    duct = report["interface_temperatures"]["hot_duct"]
    boundary = duct["radiative_boundary"]
    felt = next(layer for layer in duct["layers"] if layer["role"] == "insulation")
    structure = next(layer for layer in duct["layers"] if layer["role"] == "structure")
    expected = boundary["emissivity"] * STEFAN_BOLTZMANN * (
        (felt["outer_temperature_C"] + CELSIUS_TO_KELVIN_OFFSET) ** 4
        - (boundary["environment_temperature_C"] + CELSIUS_TO_KELVIN_OFFSET) ** 4
    )

    assert boundary["emissivity"] == pytest.approx(0.51)
    assert boundary["emissivity_flag"] == "emissivity_stand_in_not_doloma_shell"
    assert "NASA TP-1190" in boundary["citation"]
    assert boundary["outer_flux_W_m2"] == pytest.approx(expected, rel=1e-6)
    assert felt["outer_temperature_C"] != pytest.approx(
        boundary["environment_temperature_C"]
    )
    assert "hot_face_above_max_operating" in felt["hot_face_flags"]
    assert structure["conductivity_W_m_K"] is None
    assert duct["conduction_status"] == "estimated_known_layers_only"


def test_felt_limit_thickness_meets_continuous_service_and_keeps_five_millimetres() -> None:
    catalog = load_wall_materials()["materials"]
    report = shipped_design_report()
    limit = catalog["kaowool_rcf_blanket"]["service_temp"]["continuous_C"]
    alternatives = report["felt_overheat_alternatives"]
    duct = alternatives["hot_duct"]
    cr = alternatives["cr_stage2"]
    parameters = cost_parameters_from_mapping()
    liner = _layer(parameters.layered_wall["layers"], "liner")

    assert duct["felt_overheats_at_shipped_thickness"] is True
    assert cr["felt_overheats_at_shipped_thickness"] is True
    assert duct["shipped_liner_thickness_m"] == pytest.approx(0.005)
    assert liner["thickness_m"] == pytest.approx(duct["shipped_liner_thickness_m"])
    for row in (duct, cr):
        assert row["felt_inner_C_at_solved_thickness"] == pytest.approx(limit, abs=0.05)
        assert row["minimum_liner_thickness_m"] > row["shipped_liner_thickness_m"]
        assert row["thicker_liner_usd_per_m2"] > row["shipped_liner_usd_per_m2"]
        assert row["cheaper_per_m2"] == "shipped_liner_is_cheaper_and_overheats"
    assert alternatives["intermediate_dense_layer"]["reason"] == (
        "doloma_endmember_bounds"
    )
    assert alternatives["intermediate_dense_layer"]["fourth_shipped_role"] is False
    assert alternatives["sio_stage3"]["status"] == "not_applicable_no_insulation_layer"
    assert alternatives["comparison"]["shipped_liner_unchanged"] is True


def test_design_vapour_stream_uses_printed_sio_neighbors_not_pipe_capacity() -> None:
    from simulator.overhead import (
        ENGINEERING_DEFAULT_PIPE_LENGTH_M,
        compressible_poiseuille_mass_flow,
        gas_dynamic_viscosity_Pa_s,
    )

    document = load_table_document(TABLES_DIR / "O-012.yaml")
    printed = {}
    for row in document["table"]["values"]:
        heat = row.get("heat_capacity")
        temperature = row.get("temperature")
        if not isinstance(heat, dict) or not isinstance(temperature, dict):
            continue
        if heat.get("value") is None:
            continue
        printed[float(temperature["value"])] = float(heat["value"])

    report = shipped_design_report()
    balance = next(
        row for row in report["flue_gas_energy_balance"] if row["campaign"] == "C4"
    )
    stream = balance["design_vapour_stream"]
    below = stream["cp_bounds"]["below"]
    above = stream["cp_bounds"]["above"]
    molar_mass = stream["cp_bounds"]["molar_mass_kg_mol"]
    mass_flow = stream["mass_flow_kg_per_h"] / 3600.0
    expected = {
        name: mass_flow * (point["cp_J_per_mol_K"] / molar_mass) * stream["dT_K"]
        for name, point in (("below", below), ("above", above))
    }
    temperature_K = 1670.0 + CELSIUS_TO_KELVIN_OFFSET
    capacity = compressible_poiseuille_mass_flow(
        0.5,
        ENGINEERING_DEFAULT_PIPE_LENGTH_M,
        CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol,
        gas_dynamic_viscosity_Pa_s(temperature_K),
        temperature_K,
        5.0 * PA_PER_MBAR,
        0.0,
    )

    assert stream["mass_flow_kg_per_h"] == pytest.approx(14.3763683197)
    assert stream["cp_bounds"]["table_id"] == "O-012"
    assert below["cp_J_per_mol_K"] == pytest.approx(printed[below["temperature_K"]])
    assert above["cp_J_per_mol_K"] == pytest.approx(printed[above["temperature_K"]])
    assert stream["dT_K"] == pytest.approx(1670.0 - 1600.0)
    assert stream["stream_power_by_neighbor_W"]["below"] == pytest.approx(expected["below"])
    assert stream["stream_power_by_neighbor_W"]["above"] == pytest.approx(expected["above"])
    assert stream["stream_power_W"]["value"] == pytest.approx(min(expected.values()))
    assert stream["verdict"] == "hot_face_not_held_by_design_vapour_stream"
    assert balance["trace_heating_power_W_per_m"] > 0.0
    assert stream["overhead_pipe_capacity"]["flag"] == "not_the_design_stream"
    assert stream["overhead_pipe_capacity"]["kg_per_s"]["low_mbar"] == pytest.approx(
        capacity
    )
    assert mass_flow != pytest.approx(capacity, rel=0.01)

    colder = next(
        row for row in report["flue_gas_energy_balance"] if row["campaign"] == "C6"
    )
    assert colder["design_vapour_stream"]["dT_K"] < 0.0
    assert set(colder["design_vapour_stream"]["stream_power_by_neighbor_W"].values()) == {0.0}
    assert colder["verdict"] == "hot_face_not_held_by_design_vapour_stream"


def test_printed_duct_sio_dew_uses_c2a_floor_and_alkali_partials_stay_unprinted() -> None:
    report = shipped_design_report()
    hot = report["interface_temperatures"]["hot_duct"]
    dew = hot["insulation_dew_point"]
    sio = dew["species"]["SiO"]
    felt = next(layer for layer in hot["layers"] if layer["role"] == "insulation")
    flags = report["collector_stages"]["collector_stage_costs"]["fe_stage1"][
        "failure_flags"
    ]
    gas = next(
        flag for flag in flags if flag["code"] == "liner_gas_tightness_uncertified"
    )
    cao = next(
        flag for flag in flags if flag["code"] == "cao_shell_not_behind_intact_liner"
    )
    again = equilibrium_pressure_pa(
        "SiO",
        sio["dew_temperature_K"],
        pO2_bar=sio["pO2_bar"],
        source_activity=1.0,
    )

    assert sio["status"] == "ok"
    assert sio["reason"] == "equilibrium_not_pure_saturation"
    assert sio["partial_pressure_Pa"] == pytest.approx(10.902171990335434)
    assert sio["printed_pO2_mbar"] == pytest.approx(0.0)
    assert sio["pO2_bar"] == pytest.approx(1.0e-8)
    assert sio["pO2_mbar_used"] == pytest.approx(1.0e-8 * MBAR_PER_BAR)
    assert sio["pO2_floor_flag"] == "setpoint_upper_bound_used_as_floor"
    assert "C2B" in sio["pO2_floor_citation"]
    assert "1e-8" in sio["pO2_floor_citation"]
    assert again == pytest.approx(sio["partial_pressure_Pa"], rel=1e-6)
    assert sio["dew_temperature_C"] > felt["inner_temperature_C"]
    assert sio["depth_reason"] == "dew_before_the_felt"
    assert sio["depth_m"] is None
    assert dew["species"]["Na"]["reason"] == "duct_inlet_partial_not_in_design_print"
    assert dew["species"]["K"]["reason"] == "duct_inlet_partial_not_in_design_print"
    assert "probe_d452_r4_sizing_v3.py" in dew["species"]["Na"]["clearing_print"]
    assert "DIAGNOSTIC_SPECIES" in dew["species"]["K"]["clearing_print"]
    assert (
        report["interface_temperatures"]["sio_stage3"]["insulation_dew_point"]["status"]
        == "not_applicable_no_insulation_layer"
    )
    assert "helium permeability" in gas["detail"]
    assert "ASTM C20" in gas["detail"]
    assert cao["status"] == "flagged"


def test_dew_temperature_reevaluates_to_the_supplied_partial() -> None:
    temperature_K = 1800.0
    oxygen_bar = 1.0e-9
    partial = equilibrium_pressure_pa(
        "SiO",
        temperature_K,
        pO2_bar=oxygen_bar,
        source_activity=1.0,
    )
    dew_C = temperature_K - CELSIUS_TO_KELVIN_OFFSET
    found = insulation_vapour_dew_point(
        dew_C + 100.0,
        dew_C - 100.0,
        species_partials={
            "SiO": {
                "printed": True,
                "partial_pressure_Pa": partial,
                "pO2_mbar": oxygen_bar * MBAR_PER_BAR,
                "source_activity": 1.0,
            },
            "Na": {"printed": False},
            "K": {"printed": False},
        },
        felt_thickness_m=0.025,
        geometry="flat",
    )
    sio = found["species"]["SiO"]
    again = equilibrium_pressure_pa(
        "SiO",
        sio["dew_temperature_K"],
        pO2_bar=oxygen_bar,
        source_activity=1.0,
    )

    assert sio["status"] == "ok"
    assert sio["dew_temperature_K"] == pytest.approx(temperature_K, abs=1e-3)
    assert again == pytest.approx(partial, rel=1e-6)
    assert sio["depth_m"] == pytest.approx(0.0125, rel=1e-3)
    assert sio["reason"] == "equilibrium_not_pure_saturation"
    assert found["species"]["Na"]["reason"] == "duct_inlet_partial_not_in_design_print"


def test_zero_pO2_without_a_floor_stays_singular() -> None:
    found = insulation_vapour_dew_point(
        1600.0,
        500.0,
        species_partials={
            "SiO": {
                "printed": True,
                "partial_pressure_Pa": 10.902171990335434,
                "pO2_mbar": 0.0,
            },
            "Na": {"printed": False},
            "K": {"printed": False},
        },
        felt_thickness_m=0.025,
        geometry="flat",
    )
    sio = found["species"]["SiO"]

    assert sio["reason"] == "equilibrium_singular_at_zero_pO2"
    assert sio["pO2_mbar"] == pytest.approx(0.0)
    assert "pO2_floor_flag" not in sio
    assert sio["dew_temperature_C"] is None


def test_endmember_conductivity_pins_the_cropped_hot_end() -> None:
    catalog = cited_oxide_endmember_conductivity()
    materials = load_wall_materials()["materials"]
    magnesia = catalog["endmembers"]["MgO"]
    calcia = catalog["endmembers"]["CaO"]
    magnesia_hot = magnesia["thermal_conductivity"][-1]
    calcia_hot = calcia["thermal_conductivity"][-1]

    assert catalog["doloma_k_stored"] is False
    assert catalog["thickness_relation"] == "higher_k_needs_more_thickness"
    assert "thermal_conductivity" not in materials["doloma"]
    assert magnesia_hot["temperature_K"] == pytest.approx(1300.0)
    assert magnesia_hot["value_W_m_K"] == pytest.approx(6.8)
    assert "Table 2" in magnesia["citation"]
    assert magnesia["touloukian_volume_opened"] is False
    assert "not opened" in magnesia["citation"]
    assert calcia_hot["value_W_m_K"] == pytest.approx(7.74)
    assert calcia_hot["temperature_K"] == pytest.approx(1000.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert "Table A.4" in calcia["citation"]
    assert "Powel" in calcia["citation"]
    assert calcia["not_touloukian"] is True
    assert calcia["density_status"] == "unprinted"


def test_doloma_interlayer_holds_the_felt_and_undercuts_thick_ysz() -> None:
    materials = load_wall_materials()["materials"]
    report = shipped_design_report()
    limit = materials["kaowool_rcf_blanket"]["service_temp"]["continuous_C"]
    layers = cost_parameters_from_mapping().layered_wall["layers"]
    liner = _layer(layers, "liner")
    felt = _layer(layers, "insulation")
    structure = _layer(layers, "structure")
    alternatives = report["felt_overheat_alternatives"]
    priced = alternatives["intermediate_dense_layer"]
    shipped_total = report["collector_stages"]["collector_stage_cost_usd"]
    totals = priced["collector_totals"]
    low_total = totals["conservative_lower_k"]["collector_stage_cost_usd"]
    high_total = totals["optimistic_higher_k"]["collector_stage_cost_usd"]

    assert priced["status"] == "priced"
    assert priced["fourth_shipped_role"] is False
    assert priced["replaces_75_mm_structure"] is False
    assert priced["shipped_layer_thickness_m"]["liner"] == pytest.approx(
        liner["thickness_m"]
    )
    assert priced["shipped_layer_thickness_m"]["insulation"] == pytest.approx(
        felt["thickness_m"]
    )
    assert priced["shipped_layer_thickness_m"]["structure"] == pytest.approx(
        structure["thickness_m"]
    )
    assert liner["thickness_m"] == pytest.approx(0.005)
    assert felt["thickness_m"] == pytest.approx(0.025)
    assert structure["thickness_m"] == pytest.approx(0.075)
    assert totals["cheaper_walk_label"] == "conservative_lower_k"
    assert totals["thinner_lower_k_does_not_hold_felt_if_material_is_higher_k"] is True
    assert low_total > shipped_total
    assert high_total > low_total
    assert set(totals["conservative_lower_k"]["stages_repriced"]) == {
        "fe_stage1",
        "cr_stage2",
    }
    for name in ("hot_duct", "fe_stage1", "cr_stage2"):
        stage = priced["stages"][name]
        magnesia = stage["bounds"]["MgO"]
        calcia = stage["bounds"]["CaO"]
        assert stage["interlayer_required"] is True
        assert stage["replaces_75_mm_structure"] is False
        assert stage["shipped_liner_thickness_m"] == pytest.approx(0.005)
        assert magnesia["k_W_m_K"] < calcia["k_W_m_K"]
        assert magnesia["walk_label"] == "conservative_lower_k"
        assert calcia["walk_label"] == "optimistic_higher_k"
        assert magnesia["build_quantity"] == "thinner"
        assert calcia["build_quantity"] == "thicker"
        assert calcia["thickness_m"] > magnesia["thickness_m"]
        assert magnesia["felt_inner_C"] == pytest.approx(limit, abs=0.05)
        assert calcia["felt_inner_C"] == pytest.approx(limit, abs=0.05)
        assert magnesia["k_flag"] == "k_extended_off_cited_temperature"
        assert calcia["k_flag"] == "k_extended_off_cited_temperature"
        assert magnesia["cao_bearing_behind_liner"] is True
        assert magnesia["doloma_k_measured"] is False
        assert magnesia["stack_usd_per_m2"] < magnesia["thicker_ysz_liner_usd_per_m2"]
        assert calcia["stack_usd_per_m2"] < calcia["thicker_ysz_liner_usd_per_m2"]
        assert magnesia["imported_usd_per_m2"] == pytest.approx(calcia["imported_usd_per_m2"])
        assert magnesia["in_situ_usd_per_m2"] < magnesia["imported_usd_per_m2"]
        share = magnesia["resistance_share"]
        assert share["felt_share"] > share["dense_interlayer_share"]
        assert share["felt_share"] > share["by_role"]["liner"]
    assert priced["stages"]["sio_stage3"]["status"] == "not_applicable_no_insulation_layer"
    assert priced["stages"]["alkali_stage4"]["interlayer_required"] is False
    assert priced["stages"]["k_stage4b"]["interlayer_required"] is False

    duct_low = priced["stages"]["hot_duct"]["bounds"]["MgO"]
    duct_high = priced["stages"]["hot_duct"]["bounds"]["CaO"]
    assert duct_low["duct_loss_versus_shipped"] == "higher_because_felt_moves_outward"
    assert duct_high["conductive_loss"] > priced["shipped_duct_conductive_loss"]
    assert duct_low["stack_usd_per_m"] < alternatives["hot_duct"]["thicker_liner_usd_per_m"]
    for name in ("fe_stage1", "cr_stage2"):
        shipped_flux = report["interface_temperatures"][name]["radiative_boundary"][
            "outer_flux_W_m2"
        ]
        assert priced["stages"][name]["bounds"]["MgO"]["conductive_loss"] < shipped_flux
    dew = duct_low["insulation_dew_point"]["species"]["SiO"]
    again = equilibrium_pressure_pa(
        "SiO",
        dew["dew_temperature_K"],
        pO2_bar=dew["pO2_bar"],
        source_activity=1.0,
    )
    flue = next(
        row for row in report["flue_gas_energy_balance"] if row["campaign"] == "C4"
    )
    trace = next(row for row in duct_low["trace_heating"] if row["campaign"] == "C4")

    assert dew["dew_temperature_C"] > duct_low["felt_inner_C"]
    assert dew["depth_reason"] == "dew_before_the_felt"
    assert again == pytest.approx(dew["partial_pressure_Pa"], rel=1e-6)
    assert trace["verdict"] == "hot_face_not_held_by_design_vapour_stream"
    assert trace["trace_heating_power_W_per_m"] > 0.0
    assert trace["stream_power_W"]["value"] == pytest.approx(
        flue["design_vapour_stream"]["stream_power_W"]["value"]
    )
    assert priced["shipped_duct_conductive_loss"] == pytest.approx(
        flue["design_vapour_stream"]["conductive_loss_W_per_m"]
    )
