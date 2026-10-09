"""Same-table JANAF formation enthalpy, including blank-node refusals."""

import math
from decimal import Decimal

import pytest

from simulator.reference_data import janaf
from simulator.accounting.formulas import parse_formula
from simulator.config import load_config_bundle
from simulator.thermal_budget import evaporation_enthalpy_budget
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.catalog import _polynomial_from_thermo_record
from simulator.vapour_rail.channel_generator import generate_first_batch
from simulator.vapour_rail.tabulated_gibbs import (
    TabulatedDomainError, TabulatedMissingNodeError, TabulatedThermo,
)


def test_generated_janaf_enthalpy_is_the_same_printed_table():
    seen = set()
    for channel in generate_first_batch().channels:
        row = next(iter(channel.family["physical_properties"]["species"].values()))
        model = row["pressure_models"][0]
        for formula, record in model["species_thermo"].items():
            if record["evaluator_family"] != "tabulated_janaf":
                continue
            seen.add(record["record_id"])
            document = janaf.load_table_document(
                janaf.TABLES_DIR / (record["record_id"] + ".yaml")
            )
            printed = {}
            for item in document["table"]["values"]:
                T = item["temperature"]["value"]
                H = item["formation_enthalpy"].get("value")
                if T > 0 and H is not None:
                    printed.setdefault(T, H * 1000.0)
            assert record["formation_enthalpy_points"] == [
                {"T_K": T, "delta_f_H_J_per_mol": H}
                for T, H in sorted(printed.items())
            ]
            polynomial = _polynomial_from_thermo_record(
                name=formula, family="tabulated_janaf", record=record,
            )
            for T, H in printed.items():
                if polynomial.T_min_K <= T <= polynomial.T_max_K:
                    assert polynomial.evaluate(T).h_J_per_mol == pytest.approx(H)
    assert seen


def test_enthalpy_interpolation_and_extrapolation_keep_blank_node_guard():
    polynomial = TabulatedThermo(
        name="printed-grid", standard_state="gas",
        formation_gibbs_J_per_mol=((1000.0, 20.0), (2000.0, 40.0)),
        formation_enthalpy_J_per_mol=((1000.0, 100.0), (2000.0, 300.0)),
    )
    assert polynomial.evaluate(1500.0).h_J_per_mol == pytest.approx(200.0)
    with pytest.raises(TabulatedDomainError):
        polynomial.evaluate(2500.0)
    assert polynomial.evaluate(2500.0, extrapolate=True).h_J_per_mol == pytest.approx(400.0)
    assert math.isfinite(polynomial.evaluate(2500.0, extrapolate=True).g_J_per_mol)
    blank = TabulatedThermo(
        name="blank-grid", standard_state="gas",
        formation_gibbs_J_per_mol=polynomial.formation_gibbs_J_per_mol,
        formation_enthalpy_J_per_mol=polynomial.formation_enthalpy_J_per_mol,
        missing_enthalpy_nodes=(1500.0,),
    )
    with pytest.raises(TabulatedMissingNodeError):
        blank.evaluate(1500.0)
    with pytest.raises(TabulatedMissingNodeError):
        blank.evaluate(2500.0, extrapolate=True)
    assert blank.evaluate(1500.0, include_enthalpy=False).g_J_per_mol == pytest.approx(30.0)


@pytest.mark.parametrize("channel", generate_first_batch().channels,
                         ids=lambda channel: channel.species_id)
def test_each_generated_reaction_satisfies_gibbs_helmholtz(channel):
    row = next(iter(channel.family["physical_properties"]["species"].values()))
    model = row["pressure_models"][0]
    reaction = row["source_reactions"][0]
    terms = [(sign * part["stoichiometry"], part["formula"])
             for sign, side in ((-1, "reactants"), (1, "products"))
             for part in reaction[side]]
    records = model["species_thermo"]
    polynomials = {formula: _polynomial_from_thermo_record(
        name=formula, family=model["evaluator_family"], record=record,
    ) for formula, record in records.items()}
    if model["evaluator_family"] != "tabulated_janaf":
        low = max(poly.T_min_K for poly in polynomials.values())
        high = min(poly.T_max_K for poly in polynomials.values())
        T = (low + high) / 2.0
        step = 0.01
        def gibbs_over_T(temperature):
            return sum(nu * polynomials[formula].evaluate(temperature).g_J_per_mol
                       for nu, formula in terms) / temperature
        estimate = -T**2 * (gibbs_over_T(T + step) - gibbs_over_T(T - step)) / (2 * step)
        H = sum(nu * polynomials[formula].evaluate(T).h_J_per_mol
                for nu, formula in terms)
        assert H == pytest.approx(estimate, rel=1e-7, abs=0.01)
        return

    tables = {}
    for formula, record in records.items():
        document = janaf.load_table_document(janaf.TABLES_DIR / (record["record_id"] + ".yaml"))
        tables[formula] = {item["temperature"]["value"]: item
                           for item in document["table"]["values"]
                           if item["temperature"]["value"] > 0
                           and item["formation_gibbs_energy"].get("value") is not None}
    grid = set.intersection(*(set(table) for table in tables.values()))
    centers = [T for T in grid if all(T + offset * 100 in grid for offset in range(-3, 4))
               and all(table[T]["formation_enthalpy"].get("value") is not None
                       for table in tables.values())]
    assert centers, "No complete printed-grid stencil with same-table enthalpy"
    T = max(centers)  # Grid coverage alone selects the check before reading residuals.

    def printed_sum_and_rounding(temperature, field):
        value = uncertainty = 0.0
        for nu, formula in terms:
            item = tables[formula][temperature]
            cell = item[field]
            value += nu * cell["value"] * 1000.0
            elemental = len(parse_formula(formula.removesuffix("(g)").removesuffix("(l)")).elements) == 1
            exact_reference_zero = (elemental
                and item["formation_enthalpy"].get("value") == 0
                and item["formation_gibbs_energy"].get("value") == 0)
            if not exact_reference_zero:
                last_unit = 10.0 ** Decimal(cell["as_published"]).as_tuple().exponent
                uncertainty += abs(nu) * last_unit * 1000.0 / 2.0
        return value, uncertainty

    # ΔH_rxn = Σν ΔfH(products) − Σν ΔfH(reactants), J/mol reaction.
    H, tolerance = printed_sum_and_rounding(T, "formation_enthalpy")
    weights = (-1/60, 3/20, -3/4, 0, 3/4, -3/20, 1/60)
    derivative = 0.0
    for offset, weight in zip(range(-3, 4), weights):
        node = T + offset * 100.0
        G, rounding = printed_sum_and_rounding(node, "formation_gibbs_energy")
        derivative += weight * G / node / 100.0
        tolerance += T**2 * abs(weight) * rounding / node / 100.0
    assert abs(H + T**2 * derivative) <= tolerance


@pytest.mark.parametrize("temperature_K", [2000.0, 50.0])
def test_generated_heat_uses_one_source_reaction_and_flags_extrapolation(temperature_K):
    catalog = compile_vapour_rail_catalog(
        load_config_bundle().vapor_pressures.catalog_payload, emit_u0_request_rules=False,
    )
    legacy = catalog.legacy_view()
    rows = legacy["t1139_generated_carriers"]
    result = evaporation_enthalpy_budget(
        {species: 1e-9 for species in rows}, vapor_pressures=legacy,
        temperature_K=temperature_K,
    )
    assert len(result["dissociation_by_species_kWh"]) == 45
    assert set(result["latent_by_species_kWh"].values()) == {0.0}
    assert all(math.isfinite(value) for value in result["dissociation_by_species_kWh"].values())
    for species, row in rows.items():
        source = result["sources"]["analytical_source_reaction:" + species]
        assert "Same-record" in source
        if temperature_K == 50.0:
            assert "out_of_range_source_function_extrapolation" in source


def test_generated_heat_preserves_missing_enthalpy_guard():
    catalog = compile_vapour_rail_catalog(
        load_config_bundle().vapor_pressures.catalog_payload, emit_u0_request_rules=False,
    )
    legacy = catalog.legacy_view()
    with pytest.raises(TabulatedMissingNodeError) as caught:
        evaporation_enthalpy_budget(
            {"Cu": 1e-9}, vapor_pressures=legacy, temperature_K=1523.15,
        )
    assert caught.value.missing_node == 1600.0
    row = dict(legacy["t1139_generated_carriers"]["Cu"])
    from copy import deepcopy
    row = deepcopy(row)
    for record in row["reference_pressure_model"]["species_thermo"].values():
        record.pop("formation_enthalpy_points", None)
    with pytest.raises(ValueError, match="missing printed formation enthalpy"):
        evaporation_enthalpy_budget(
            {"Cu": 1e-9}, vapor_pressures={"Cu": row}, temperature_K=2000.0,
        )


def test_generator_types_dormancy_when_source_prints_no_enthalpy(monkeypatch):
    from dataclasses import replace
    from simulator.vapour_rail.source_rail import load_source_rail
    from simulator.vapour_rail.channel_generator import generate_element_channels

    rail = load_source_rail()
    original = rail.select_common_source
    def missing_heat(participants):
        selected = original(participants)
        if selected is None:
            return None
        source, records = selected
        return source, {key: replace(record, species_thermo={
            name: value for name, value in record.species_thermo.items()
            if name != "formation_enthalpy_points"
        }) for key, record in records.items()}
    monkeypatch.setattr(rail, "select_common_source", missing_heat)
    channels, _gaps = generate_element_channels("Cu", rail=rail)
    assert channels
    for channel in channels:
        row = next(iter(channel.family["physical_properties"]["species"].values()))
        assert row["flux_dormant"]
        assert row["dormancy_reason"]["kind"] == "missing_printed_formation_enthalpy"
        assert row["dormancy_reason"]["participants"]


@pytest.mark.parametrize("column,missing_key,kind", [
    ("formation_enthalpy_points", "missing_enthalpy_nodes",
     "missing_printed_formation_enthalpy"),
    ("formation_gibbs_points", "missing_nodes", "missing_printed_formation_gibbs"),
])
def test_generated_activation_follows_repaired_printed_nodes(column, missing_key, kind):
    from copy import deepcopy
    from simulator.vapour_rail.channel_generator import _four_strata_family

    channel = next(c for c in generate_first_batch().channels if c.species_id == "Pb")
    row = channel.family["physical_properties"]["species"][channel.species_id]
    model = row["pressure_models"][0]
    thermo = deepcopy(model["species_thermo"])
    # Remove an actually printed node, then restore that exact source value.
    # Clear the unrelated existing H sign hole to isolate the column under test.
    parent = thermo["PbO(l)"]
    parent["missing_enthalpy_nodes"] = []
    original_points = deepcopy(parent[column])
    point = next(p for p in original_points if p["T_K"] == 1400.0)
    parent[column] = [p for p in original_points if p["T_K"] != point["T_K"]]
    parent[missing_key] = [100.0, 200.0, point["T_K"]]

    def generate():
        return _four_strata_family(
            species_id=channel.species_id, element=channel.element,
            carrier=channel.carrier, formula=row["formula"],
            parent_oxide=channel.parent_oxide, activity_basis=channel.activity_basis,
            reaction=row["source_reactions"][0], species_thermo=thermo,
            evaluator_family=model["evaluator_family"],
            domain=tuple(model["valid_domain"]["temperature_K"]),
            selected_sources=channel.selected_sources, native_phases=channel.native_phases,
            bands_K=channel.bands_K,
            oxide_per_vapor=channel.family["fiat_routing"]["compatibility_fields"]["stoich_oxide_per_vapor"],
            o2_per_vapor=channel.family["fiat_routing"]["compatibility_fields"]["stoich_O2_per_vapor"],
            vapor_oxygen_atoms=0.0,
        )["physical_properties"]["species"][channel.species_id]

    dormant = generate()
    assert dormant["flux_dormant"]
    assert dormant["dormancy_reason"] == {
        "kind": kind, "detail": "missing_or_ambiguous_printed_node",
        "participant": "PbO(l)", "table": "O-007", "temperature_K": 1400.0,
    }
    parent[column] = original_points
    parent[missing_key] = [100.0, 200.0]
    assert not generate()["flux_dormant"]
