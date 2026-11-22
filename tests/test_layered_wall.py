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
    cylindrical_wall_conduction,
    flat_wall_conduction,
    flue_gas_energy_balance,
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
    PA_PER_MBAR,
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


def test_missing_conductivity_density_and_thickness_refuse() -> None:
    shipped = cost_parameters_from_mapping().layered_wall
    priced = _price(shipped, _areas(fe_stage1=1.0), in_situ_rate=None)
    stage = priced["collector_stage_costs"]["fe_stage1"]

    assert priced["status"] == "unavailable"
    assert stage["cost_usd"]["value"] is None
    assert stage["cost_usd"]["status"] == "unavailable"
    reason = stage["cost_usd"]["reason"]
    assert "thickness_unset:liner" in reason
    assert "density_missing:liner" in reason
    assert "catalog_missing:insulation" in reason
    assert "source_unset:insulation" in reason
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
    assert before["in_situ_kg"] == pytest.approx(2.0 * thickness * density)
    assert after["in_situ_kg"] == pytest.approx(before["in_situ_kg"] * 2.0)
    assert before["in_situ_usd"]["status"] == "unavailable"
    assert before["in_situ_usd"]["value"] is None
    assert before["in_situ_usd"]["reason"] == "in_situ_cost_unset"
    assert before["wall_thickness_certification"] == "uncertified_cost_parameter_slot"
    assert before["bulk_density_certification"] == "uncertified_cost_parameter_slot"


def test_owner_set_in_situ_rate_prices_known_mass_and_zero_is_not_unset() -> None:
    parameters = cost_parameters_from_mapping()
    wall = parameters.layered_wall
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
    assert table["hot_duct"]["conduction_status"] == "unavailable"
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
    assert balance["trace_heating_power_W_per_m"]["reason"] == (
        "conductive_loss_unavailable"
    )
    assert balance["trace_heating_power_W_per_m"]["value"] is None
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
    assert report["in_situ_bootstrap_cost_per_kg"] is None
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
        assert stage["in_situ_usd"]["value"] is None
        assert cases[name]["collector_stage_costs"]["fe_stage1"]["cost_usd"][
            "status"
        ] == "unavailable"


def test_in_situ_rate_none_zero_and_positive_split_the_cost_key() -> None:
    base = default_cost_parameters_block()
    zero = copy.deepcopy(base)
    positive = copy.deepcopy(base)
    zero["parameters"]["in_situ_bootstrap_cost_per_kg"]["value"] = 0.0
    positive["parameters"]["in_situ_bootstrap_cost_per_kg"]["value"] = 50.0
    moved = copy.deepcopy(base)
    moved["parameters"]["layered_wall"]["outer_temperature_C"] = 25.0

    assert cost_parameters_from_mapping(base).in_situ_bootstrap_cost_per_kg is None
    assert cost_parameters_digest(base) != cost_parameters_digest(zero)
    assert cost_parameters_digest(zero) != cost_parameters_digest(positive)
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
