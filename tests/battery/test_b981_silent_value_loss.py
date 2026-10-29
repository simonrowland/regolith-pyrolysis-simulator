from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[2]


def _observations(source: str, observation_token: str):
    migrated = migrate_one_extract(
        ROOT, ROOT / "data/literature/extracts" / source
    )
    return [
        observation
        for observation_id, observation in migrated.result.observations.items()
        if observation_token in observation_id
    ]


def test_kambayashi_table7_activity_rows_migrate_with_temperature_and_composition():
    observations = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_table7_raoultian_a_p2o5",
    )
    assert len(observations) == 22
    assert all(
        observation.identity.quantity.value.value == Quantity.ACTIVITY.value
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
    assert sorted(observation.value.point for observation in observations) == sorted(
        Decimal(value)
        for value in (
            "1.2000000000000001e-17",
            "1.4e-17",
            "1.5e-17",
            "3.8e-17",
            "4.5000000000000004e-17",
            "5.400000000000001e-17",
            "7.200000000000001e-17",
            "8.7e-17",
            "9.2e-17",
            "1.0000000000000001e-16",
            "1.14e-16",
            "1.4e-16",
            "1.2400000000000002e-16",
            "1.4800000000000001e-16",
            "1.8100000000000003e-16",
            "2.0800000000000003e-16",
            "1.56e-16",
            "1.8000000000000002e-16",
            "2.0000000000000002e-16",
            "2.6300000000000003e-16",
            "3.2299999999999997e-16",
            "4.76e-16",
        )
    )
    for observation in observations:
        conditions = observation.point_conditions or {}
        for key in ("composition", "temperature_K"):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None


def test_ohara_table2_activity_rows_migrate_with_source_temperature():
    observations = _observations(
        "kems-058-ohara-1987.yaml",
        "ohara_1987_table2_compositions_and_raoultian_a_p2o5",
    )
    expected = {
        1: "2.5000000000000003e-17",
        2: "4.8000000000000003e-17",
        3: "8.400000000000001e-17",
        4: "6.3e-17",
        5: "4.6e-17",
        6: "4.8000000000000003e-17",
        7: "5.900000000000001e-17",
        8: "7.8e-17",
        9: "2.0e-17",
        10: "5.8e-17",
        11: "6.800000000000001e-17",
        12: "8.2e-17",
        13: "9.7e-17",
        14: "3.3e-17",
        15: "8.2e-17",
        16: "7.8e-17",
        17: "1.1000000000000001e-16",
        18: "3.3e-17",
        19: "6.1e-17",
    }
    assert len(observations) == len(expected)
    for observation in observations:
        sample = int(observation.observation_id.rsplit("_sample_", 1)[1])
        assert observation.identity.quantity.value.value == Quantity.ACTIVITY.value
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(expected[sample])
        temperature = (observation.point_conditions or {})["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1643.0")
        assert temperature.locator is not None


def test_ohara_table1_po_partial_pressures_migrate_with_sample_conditions():
    observations = _observations(
        "kems-058-ohara-1987.yaml",
        "ohara_1987_table1_partial_pressures_1643k",
    )
    expected_pa = (
        "0.04843335",
        "0.073257975",
        "0.090685875",
        "0.101831625",
        "0.074879175",
        "0.070826175",
        "0.0798441",
        "0.0838971",
        "0.100818375",
        "0.04721745",
        "0.088051425",
        "0.09869055",
        "0.09909585",
        "0.120880725",
        "0.057248625",
        "0.101021025",
        "0.1118628",
        "0.136890075",
        "0.070623525",
        "0.097272",
    )
    assert len(observations) == len(expected_pa)
    assert all(
        observation.identity.quantity.value.value == Quantity.P_PARTIAL.value
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
    assert sorted(observation.value.point for observation in observations) == sorted(
        Decimal(value) for value in expected_pa
    )
    for observation in observations:
        conditions = observation.point_conditions or {}
        for key in ("composition", "temperature_K"):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None


def test_kambayashi_table6_po_partial_pressures_migrate_with_sample_conditions():
    observations = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_table6_partial_pressures_and_dg",
    )
    expected_pa = (
        "0.028978950",
        "0.035058450",
        "0.045393600",
        "0.057653925",
        "0.070724850",
        "0.092104425",
        "0.077108325",
        "0.098791875",
        "0.1215900",
        "0.085518300",
        "0.108620400",
        "0.134964900",
        "0.109228350",
        "0.119158200",
        "0.149454375",
        "0.187755225",
        "0.121792650",
        "0.153912675",
        "0.19657050",
        "0.154216650",
        "0.193733400",
        "0.234871350",
    )
    assert len(observations) == len(expected_pa)
    assert all(
        observation.identity.quantity.value.value == Quantity.P_PARTIAL.value
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
    assert sorted(observation.value.point for observation in observations) == sorted(
        Decimal(value) for value in expected_pa
    )
    for observation in observations:
        conditions = observation.point_conditions or {}
        for key in ("composition", "temperature_K"):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None


def test_wu_reported_solidus_rows_migrate_temperature_and_composition_axis():
    observations = _observations(
        "kems-190-wu-1993.yaml",
        "wu_1993_table3_reported_solidus_points",
    )
    expected = {
        "41": "1768.15",
        "45": "1738.15",
        "47.5": "1713.15",
        "54": "1683.15",
        "70": "1591.15",
        "80": "1549.15",
    }
    assert len(observations) == len(expected)
    assert all(
        observation.identity.quantity.value.value
        == Quantity.TRANSITION_TEMPERATURE.value
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
    for observation in observations:
        conditions = observation.point_conditions or {}
        composition = conditions["Fe2SiO4_wt_pct"]
        assert composition.state.is_value
        assert composition.locator is not None
        assert observation.value.point == Decimal(
            expected[str(composition.state.value)]
        )


def test_yamada_interaction_parameters_migrate_with_solute_and_temperature():
    tokens = (
        "yamada_1983_epsilon_P_i_this_work_1600C",
        "yamada_1983_table2_quoted_interaction_coefficients",
        "yamada_1983_epsilon_P_Al_body_4p0_printed",
        "yamada_1983_epsilon_P_Si_fruehan_aFe_12p6",
    )
    candidates = _observations("kems-105-yamada-1983.yaml", "yamada_1983_")
    observations = [
        observation
        for observation in candidates
        if any(token in observation.observation_id for token in tokens)
    ]
    expected = {
        Decimal("4.6"): ("Al", Decimal("1873.0")),
        Decimal("11.9"): ("Si", Decimal("1873.0")),
        Decimal("-8.0"): ("Ti", Decimal("1873.0")),
        Decimal("-4.9"): ("V", Decimal("1873.0")),
        Decimal("-3.8"): ("Cr", Decimal("1873.0")),
        Decimal("0.9"): ("Co", Decimal("1873.0")),
        Decimal("0.7"): ("Ni", Decimal("1873.0")),
        Decimal("-5.4"): ("Nb", Decimal("1873.0")),
        Decimal("-0.4"): ("Mo", Decimal("1873.0")),
        Decimal("4.0"): ("Al", Decimal("1873.0")),
        Decimal("12.6"): ("Si", Decimal("1873.0")),
        Decimal("21.0"): ("Si", Decimal("1873.0")),
        Decimal("14.2"): ("Si", Decimal("1788.15")),
        Decimal("11.0"): ("Si", Decimal("1873.0")),
        Decimal("18.8"): ("Cr", Decimal("1873.0")),
        Decimal("-6.3"): ("Cr", Decimal("1873.0")),
        Decimal("0.0"): ("Ni", Decimal("1873.0")),
    }
    assert len(observations) == len(expected)
    for observation in observations:
        assert observation.value.kind is ValueKind.POINT
        solute = (observation.point_conditions or {})["solute"]
        temperature = (observation.point_conditions or {})["temperature_K"]
        assert solute.state.is_value and solute.locator is not None
        assert temperature.state.is_value and temperature.locator is not None
        assert (solute.state.value, temperature.state.value) == expected[
            observation.value.point
        ]


def test_nanjo_second_law_vaporization_enthalpies_migrate_in_kj_per_mol():
    observations = _observations(
        "kems-188-nanjo-1976.yaml",
        "nanjo_1976_table1_second_law_deltaHvap",
    )
    expected = {
        ("Se(g)", Decimal("82.8432")),
        ("Se2(g)", Decimal("105.8552")),
        ("Se3(g)", Decimal("73.6384")),
        ("Se4(g)", Decimal("55.6472")),
        ("Se5(g)", Decimal("57.7392")),
        ("Te(g)", Decimal("120.4992")),
        ("Te2(g)", Decimal("119.6624")),
        ("Te3(g)", Decimal("57.7392")),
    }
    assert len(observations) == 8
    assert {
        (
            observation.point_conditions["gas_species"].state.value,
            observation.value.point,
        )
        for observation in observations
    } == expected
    for observation in observations:
        assert observation.value.kind is ValueKind.POINT
        assert (
            observation.identity.quantity.value
            is Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW
        )
        assert observation.identity.temperature_K.is_unknown
        assert observation.locator is not None
        assert observation.locator.table == "1"
        conditions = observation.point_conditions or {}
        assert conditions["gas_species"].state.is_value
        assert conditions["gas_species"].locator is not None
        assert conditions["temperature_K"].state.is_unknown
        assert conditions["temperature_K"].locator is not None


def test_adamkovicova_prior_fusion_enthalpies_convert_in_kj_per_mol():
    observations = _observations(
        "adamkovicova-1980-casio3-heat-of-fusion.yaml",
        "adamkovicova_1980_prior_heat_fusion_",
    )
    expected = {
        "adamkovicova_1980_prior_heat_fusion_56_1_quote": Decimal("56.1"),
        "adamkovicova_1980_prior_heat_fusion_50_1_quote": Decimal("50.1"),
        "adamkovicova_1980_prior_heat_fusion_59_0_quote": Decimal("59.0"),
        "adamkovicova_1980_prior_heat_fusion_68_2_quote": Decimal("68.2"),
        "adamkovicova_1980_prior_heat_fusion_27_6_quote": Decimal("27.6"),
        "adamkovicova_1980_prior_heat_fusion_82_9_quote": Decimal("82.9"),
    }
    assert len(observations) == len(expected)
    for observation in observations:
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected[observation.observation_id.split("::")[-1]]
        assert observation.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
        assert observation.identity.temperature_K.is_unknown
        temperature = (observation.point_conditions or {})["temperature_K"]
        assert temperature.state.is_unknown and temperature.locator is not None


def test_ammin_76_904_quoted_fusion_enthalpies_migrate_with_temperature():
    candidates = _observations(
        "ammin-76-904.yaml",
        "ammin_76_904_quoted_",
    )
    expected = {
        "ammin_76_904_quoted_comparator_enthalpy_values": (
            Decimal("138.1"),
            Decimal("1665"),
        ),
        "ammin_76_904_quoted_richet_bottinga_diopside_fusion": (
            Decimal("137.7"),
            Decimal("1665"),
        ),
        "ammin_76_904_quoted_ferrier_diopside_fusion": (
            Decimal("128"),
            Decimal("1665"),
        ),
        "ammin_76_904_quoted_adamkovicova_wollastonite_fusion": (
            Decimal("57.3"),
            Decimal("1821"),
        ),
    }
    observations = [
        observation
        for observation in candidates
        if observation.observation_id.rsplit("::", 1)[-1] in expected
    ]
    assert len(observations) == len(expected)
    for observation in observations:
        observation_id = observation.observation_id.rsplit("::", 1)[-1]
        value, temperature = expected[observation_id]
        assert observation.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == value
        located_temperature = (observation.point_conditions or {})["temperature_K"]
        assert located_temperature.state.is_value
        assert located_temperature.state.value == temperature
        assert located_temperature.locator is not None


def test_shilov_table4_molecular_oxygen_pressures_use_printed_scale_and_conditions():
    observations = _observations(
        "shilov-1998-sno-zno-p2o5-vaporization.yaml",
        "shilov_1998_table4_molecular_oxygen_partial_pressures",
    )
    expected = {
        Decimal("40.0"): Decimal("8.79501e-11"),
        Decimal("46.7"): Decimal("6.07950e-11"),
        Decimal("53.3"): Decimal("7.031955e-11"),
        Decimal("60.0"): Decimal("8.7443475e-11"),
    }
    assert len(observations) == len(expected)
    for observation in observations:
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.value.kind is ValueKind.POINT
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        composition = conditions["xSnO_mol_pct"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1300.0")
        assert temperature.locator is not None
        assert composition.state.is_value and composition.locator is not None
        assert composition.state.value.kind is ValueKind.POINT
        assert observation.value.point == expected[composition.state.value.point]


def test_ohara_p4o10_saturation_pressure_migrates_with_janaf_conditions():
    observations = _observations(
        "kems-058-ohara-1987.yaml",
        "ohara_1987_p4o10_sat_janaf_1643k",
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.identity.quantity.value.value == Quantity.P_SAT.value
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal("70927500")
    assert observation.identity.temperature_K.value == Decimal("1643.0")
    assert observation.identity.species.formula == "P4O10"
    assert observation.identity.species.phase.value is Phase.G
    temperature = observation.point_conditions["temperature_K"]
    assert temperature.state.is_value
    assert temperature.state.value == Decimal("1643.0")
    assert temperature.locator is not None


def test_fegley_table4_hydroxide_pressures_migrate_with_conditions():
    observations = _observations(
        "fegley-2016-rock-steam-solubility.yaml",
        "fegley_2016_table4_hydroxide_partial_pressures",
    )
    expected = {
        ("SiH4O4", Decimal("1000")): Decimal("2.9e3"),
        ("SiH4O4", Decimal("1500")): Decimal("2.0e4"),
        ("SiH4O4", Decimal("2000")): Decimal("5.9e4"),
        ("MgH2O2", Decimal("1000")): Decimal("2.0e-5"),
        ("MgH2O2", Decimal("1500")): Decimal("2.7"),
        ("MgH2O2", Decimal("2000")): Decimal("1.0e3"),
        ("FeH2O2", Decimal("1000")): Decimal("0.15"),
        ("FeH2O2", Decimal("1500")): Decimal("3.5e2"),
        ("FeH2O2", Decimal("2000")): Decimal("1.1e4"),
        ("CaH2O2", Decimal("1000")): Decimal("1.7e-5"),
        ("CaH2O2", Decimal("1500")): Decimal("12"),
        ("CaH2O2", Decimal("2000")): Decimal("4.0e3"),
        ("AlH3O3", Decimal("1000")): Decimal("0.023"),
        ("AlH3O3", Decimal("1500")): Decimal("50"),
        ("AlH3O3", Decimal("2000")): Decimal("2.4e3"),
        ("NiH2O2", Decimal("1000")): Decimal("0.013"),
        ("NiH2O2", Decimal("1500")): Decimal("1.6e2"),
        ("NiH2O2", Decimal("2000")): Decimal("1.9e4"),
    }
    assert len(observations) == len(expected)
    for observation in observations:
        key = (observation.identity.species.formula, observation.identity.temperature_K.value)
        assert key in expected
        assert observation.identity.quantity.value.value == Quantity.P_PARTIAL.value
        assert observation.identity.species.phase.value is Phase.G
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected[key]
        conditions = observation.point_conditions or {}
        total_pressure = conditions["total_pressure_Pa"]
        assert total_pressure.state.is_value
        assert total_pressure.state.value.kind is ValueKind.POINT
        assert total_pressure.state.value.point == Decimal("22064000")
        assert total_pressure.locator is not None
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == key[1]
        assert temperature.locator is not None
