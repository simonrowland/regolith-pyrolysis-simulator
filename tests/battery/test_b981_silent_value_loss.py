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
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        composition = conditions["composition"]
        assert composition.locator is not None
        sample = int(observation.observation_id.rsplit("_sample_", 1)[1])
        if sample == 20:
            assert not composition.state.is_value
            assert composition.state.reason == (
                "Table 2 has no printed composition for sample 20"
            )
        else:
            assert composition.state.is_value


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


def test_kambayashi_henry_activity_coefficients_migrate_by_temperature():
    solid_standard = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_gamma_p2o5_solid_std_henry",
    )
    liquid_standard = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_gamma_p2o5_liquid_std_turkdogan_pearson",
    )
    expected = {
        "solid": {
            Decimal("1643.15"): Decimal("2.2e-15"),
            Decimal("1653.15"): Decimal("2.5e-15"),
            Decimal("1663.15"): Decimal("2.8e-15"),
        },
        "liquid": {
            Decimal("1643.15"): Decimal("3.4e-13"),
            Decimal("1653.15"): Decimal("4.0e-13"),
            Decimal("1663.15"): Decimal("4.7e-13"),
        },
    }
    assert len(solid_standard) == len(expected["solid"])
    assert len(liquid_standard) == len(expected["liquid"])
    for group, expected_points in (
        (solid_standard, expected["solid"]),
        (liquid_standard, expected["liquid"]),
    ):
        for observation in group:
            temperature = observation.identity.temperature_K.value
            assert observation.identity.quantity.value.value == Quantity.ACTIVITY_COEFFICIENT.value
            assert observation.value.kind is ValueKind.POINT
            assert observation.value.point == expected_points[temperature]
            condition = (observation.point_conditions or {})["temperature_K"]
            assert condition.state.is_value
            assert condition.state.value == temperature
            assert condition.locator is not None


def test_kambayashi_janaf_p4o10_saturation_pressures_migrate_with_temperature():
    observations = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_p4o10_sat_janaf_extrapolated",
    )
    expected_pa = {
        Decimal("1643.0"): Decimal("70927500"),
        Decimal("1653.0"): Decimal("71839425"),
        Decimal("1663.0"): Decimal("72852675"),
    }
    assert len(observations) == len(expected_pa)
    for observation in observations:
        temperature = observation.identity.temperature_K.value
        assert observation.identity.quantity.value.value == Quantity.P_SAT.value
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected_pa[temperature]
        condition = (observation.point_conditions or {})["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == temperature
        assert condition.locator is not None


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


def test_janaf_mixed_units_recover_298k_formation_gibbs_values():
    expected = {
        "janaf_PO_298_anchors": Decimal("-47139"),
        "janaf_PO2_298_anchors": Decimal("-316748"),
        "janaf_P4O6_298_anchors": Decimal("-2084877"),
        "janaf_P2_298_anchors": Decimal("103112"),
        "janaf_P4_298_anchors": Decimal("24416"),
    }
    observations = []
    for observation_id in expected:
        observations.extend(_observations("janaf-4th.yaml", observation_id))
    assert len(observations) == len(expected)
    for observation in observations:
        assert observation.identity.quantity.value.value == Quantity.DELTA_FG.value
        assert observation.identity.temperature_K.value == Decimal("298.15")
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(
            expected[observation.observation_id.split("::")[-1]]
        ) / Decimal("1000")
        condition = observation.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == Decimal("298.15")
        assert condition.locator is not None


def test_steurer_table10_exact_pressure_cells_migrate_with_temperature():
    observations = _observations(
        "steurer-1992-vapor-phase-pyrolysis.yaml",
        "steurer_1992_table10_mixture_psat",
    )
    expected = {
        ("O2", Decimal("2000.0")): Decimal("0.32"),
        ("O2", Decimal("2500.0")): Decimal("150"),
        ("O2", Decimal("3000.0")): Decimal("8100"),
        ("AlO", Decimal("3000.0")): Decimal("1.8"),
        ("AlO2", Decimal("3000.0")): Decimal("5.8"),
        ("SiO", Decimal("2000.0")): Decimal("0.65"),
        ("SiO", Decimal("2500.0")): Decimal("300"),
        ("SiO", Decimal("3000.0")): Decimal("16000"),
        ("SiO2", Decimal("2500.0")): Decimal("14"),
        ("SiO2", Decimal("3000.0")): Decimal("1100"),
        ("TiO", Decimal("2500.0")): Decimal("0.1"),
        ("TiO", Decimal("3000.0")): Decimal("10"),
        ("TiO2", Decimal("3000.0")): Decimal("1.3"),
        ("FeO", Decimal("2000.0")): Decimal("0.1"),
        ("FeO", Decimal("2500.0")): Decimal("20"),
        ("FeO", Decimal("3000.0")): Decimal("590"),
    }
    assert len(observations) == len(expected)
    for observation in observations:
        key = (observation.identity.species.formula, observation.identity.temperature_K.value)
        assert key in expected
        assert observation.identity.quantity.value.value == Quantity.P_PARTIAL.value
        assert observation.identity.species.phase.value is Phase.G
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected[key]
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == key[1]
        assert temperature.locator is not None
        assert temperature.locator.table == "10"


def test_fegley_table1_silicate_gas_pressures_migrate_with_conditions():
    observations = _observations(
        "fegley-2016-rock-steam-solubility.yaml",
        "fegley_2016_table1_si_bearing_gas_partial_pressures",
    )
    conditions = {
        "A": (Decimal("2000"), Decimal("4"), "steam", "liquid silica"),
        "B": (Decimal("1573"), Decimal("100000"), "90% steam - 10% O2", "cristobalite"),
        "C": (Decimal("1500"), Decimal("27000000"), "steam", "cristobalite"),
        "D": (Decimal("2000"), Decimal("110000000"), "75% steam - 25% CO2", "liquid silica"),
    }
    printed_bar = {
        "SiO": ("8.6e-6", "1.6e-13", "4.4e-14", "2.9e-8"),
        "SiO2": ("6.9e-7", "6.4e-11", "7.6e-12", "6.9e-7"),
        "SiHO2": ("3.4e-11", "6.7e-15", "1.9e-14", "1.3e-8"),
        "SiH2O3": ("3.3e-11", "5.9e-9", "4.9e-7", "1.5e-3"),
        "SiH4O4": ("3.9e-15", "4.1e-6", "3e-1", "8.25"),
        "Si2H6O7": ("7.5e-26", "2e-11", "5.4e-4", "7.2e-3"),
    }
    expected = {}
    for species, pressures in printed_bar.items():
        for label, pressure in zip("ABCD", pressures, strict=True):
            expected[(species, label)] = Decimal(pressure) * Decimal("100000")
    assert len(observations) == len(expected)
    for observation in observations:
        point_conditions = observation.point_conditions or {}
        gas = point_conditions["sweep_gas_as_printed"]
        label = next(
            condition_label
            for condition_label, condition in conditions.items()
            if gas.state.value == condition[2]
            and observation.identity.temperature_K.value == condition[0]
        )
        expected_condition = conditions[label]
        key = (observation.identity.species.formula, label)
        assert key in expected
        assert observation.identity.quantity.value.value == Quantity.P_PARTIAL.value
        assert observation.identity.species.phase.value is Phase.G
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected[key]
        temperature = point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == expected_condition[0]
        assert temperature.locator is not None
        assert temperature.locator.table == "1"
        total_pressure = point_conditions["total_pressure_Pa"]
        assert total_pressure.state.is_value
        assert total_pressure.state.value.kind is ValueKind.POINT
        assert total_pressure.state.value.point == expected_condition[1]
        assert total_pressure.locator is not None
        assert total_pressure.locator.table == "1"
        condensed_phase = point_conditions["condensed_phase_as_printed"]
        assert condensed_phase.state.value == expected_condition[3]
        assert condensed_phase.locator is not None


def test_sossi_table3_log_k_star_cells_migrate_as_temperature_points():
    observations = [
        observation
        for observation in _observations(
            "kems-012-sossi-2019.yaml", "quoted_20260906"
        )
        if "_logKstar_table3_" in observation.observation_id
        and "_na_logKstar_table3_" not in observation.observation_id
        and "_k_logKstar_table3_" not in observation.observation_id
    ]
    expected = {
        ("sossi_2019_ag_logKstar_table3_quoted_20260906", "1573.15"): "-2.23",
        ("sossi_2019_cd_logKstar_table3_quoted_20260906", "1573.15"): "-2.48",
        ("sossi_2019_cr_logKstar_table3_CrO_ox_quoted_20260906", "1673.15"): "-2.7",
        ("sossi_2019_cr_logKstar_table3_CrO_ox_quoted_20260906", "1773.15"): "-2.4",
        ("sossi_2019_cr_logKstar_table3_CrO_ox_quoted_20260906", "1823.15"): "-2.29",
        ("sossi_2019_cu_logKstar_table3_quoted_20260906", "1573.15"): "-4.09",
        ("sossi_2019_cu_logKstar_table3_quoted_20260906", "1673.15"): "-3.7",
        ("sossi_2019_cu_logKstar_table3_quoted_20260906", "1773.15"): "-3.13",
        ("sossi_2019_cu_logKstar_table3_quoted_20260906", "1823.15"): "-2.9",
        ("sossi_2019_ga_logKstar_table3_GaO_g_quoted_20260906", "1673.15"): "-5.1",
        ("sossi_2019_ga_logKstar_table3_GaO_g_quoted_20260906", "1773.15"): "-4.45",
        ("sossi_2019_ga_logKstar_table3_GaO_g_quoted_20260906", "1823.15"): "-4.16",
        ("sossi_2019_ga_logKstar_table3_Ga_g_quoted_20260906", "1673.15"): "-9.92",
        ("sossi_2019_ga_logKstar_table3_Ga_g_quoted_20260906", "1773.15"): "-8.42",
        ("sossi_2019_ga_logKstar_table3_Ga_g_quoted_20260906", "1823.15"): "-7.76",
        ("sossi_2019_ge_logKstar_table3_quoted_20260906", "1573.15"): "-5.04",
        ("sossi_2019_ge_logKstar_table3_quoted_20260906", "1673.15"): "-3.7",
        ("sossi_2019_ge_logKstar_table3_quoted_20260906", "1773.15"): "-2.71",
        ("sossi_2019_ge_logKstar_table3_quoted_20260906", "1823.15"): "-2.29",
        ("sossi_2019_li_logKstar_table3_LiO_g_quoted_20260906", "1673.15"): "-3.39",
        ("sossi_2019_li_logKstar_table3_Li_g_quoted_20260906", "1673.15"): "-5.36",
        ("sossi_2019_mo_logKstar_table3_MoO2_ox_quoted_20260906", "1573.15"): "-2.87",
        ("sossi_2019_mo_logKstar_table3_MoO2_ox_quoted_20260906", "1673.15"): "-2.86",
        ("sossi_2019_mo_logKstar_table3_MoO2_ox_quoted_20260906", "1773.15"): "-2.48",
        ("sossi_2019_mo_logKstar_table3_MoO2_ox_quoted_20260906", "1823.15"): "-2.39",
        ("sossi_2019_mo_logKstar_table3_MoO3_congruent_quoted_20260906", "1573.15"): "-4.67",
        ("sossi_2019_mo_logKstar_table3_MoO3_congruent_quoted_20260906", "1673.15"): "-4.34",
        ("sossi_2019_mo_logKstar_table3_MoO3_congruent_quoted_20260906", "1773.15"): "-3.64",
        ("sossi_2019_mo_logKstar_table3_MoO3_congruent_quoted_20260906", "1823.15"): "-3.41",
        ("sossi_2019_pb_logKstar_table3_PbO_g_quoted_20260906", "1573.15"): "-2.9",
        ("sossi_2019_pb_logKstar_table3_PbO_g_quoted_20260906", "1673.15"): "-2.0",
        ("sossi_2019_pb_logKstar_table3_Pb_g_quoted_20260906", "1573.15"): "-4.47",
        ("sossi_2019_pb_logKstar_table3_Pb_g_quoted_20260906", "1673.15"): "-3.49",
        ("sossi_2019_rb_logKstar_table3_quoted_20260906", "1573.15"): "-4.15",
        ("sossi_2019_rb_logKstar_table3_quoted_20260906", "1673.15"): "-3.12",
        ("sossi_2019_rb_logKstar_table3_quoted_20260906", "1773.15"): "-2.63",
        ("sossi_2019_rb_logKstar_table3_quoted_20260906", "1823.15"): "-2.26",
        ("sossi_2019_zn_logKstar_table3_quoted_20260906", "1573.15"): "-5.06",
        ("sossi_2019_zn_logKstar_table3_quoted_20260906", "1673.15"): "-3.96",
        ("sossi_2019_zn_logKstar_table3_quoted_20260906", "1773.15"): "-2.92",
        ("sossi_2019_zn_logKstar_table3_quoted_20260906", "1823.15"): "-2.57",
    }
    assert len(observations) == len(expected)
    for observation in observations:
        root_id = observation.observation_id.split("::", 1)[1].split("::T=", 1)[0]
        temperature = observation.identity.temperature_K.value
        key = (root_id, str(temperature))
        assert key in expected
        assert observation.identity.quantity.value.value == Quantity.LOG10_K_STAR.value
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(expected[key])
        condition = (observation.point_conditions or {})["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == temperature
        assert condition.locator is not None
        assert condition.locator.table == "3"


def test_shilov_table4_atomic_oxygen_pressures_migrate_all_equilibrium_estimates():
    observations = _observations(
        "shilov-1998-sno-zno-p2o5-vaporization.yaml",
        "shilov_1998_table4_atomic_oxygen_partial_pressures",
    )
    expected_pa = {
        ("40.0", "(4)"): "5.8667175e-10",
        ("40.0", "(8)"): "4.681215e-10",
        ("40.0", "(9)"): "5.2385025e-10",
        ("40.0", "(10)"): "5.02572e-10",
        ("40.0", "(11)"): "3.28293e-10",
        ("46.7", "(4)"): "4.1441925e-10",
        ("46.7", "(8)"): "4.4886975e-10",
        ("46.7", "(9)"): "4.316445e-10",
        ("46.7", "(10)"): "4.3468425e-10",
        ("46.7", "(11)"): "4.7116125e-10",
        ("53.3", "(4)"): "4.33671e-10",
        ("53.3", "(8)"): "4.9345275e-10",
        ("53.3", "(9)"): "4.6305525e-10",
        ("53.3", "(10)"): "4.70148e-10",
        ("53.3", "(11)"): "3.627435e-10",
        ("60.0", "(4)"): "4.8636e-10",
        ("60.0", "(8)"): "5.4816825e-10",
        ("60.0", "(9)"): "5.1574425e-10",
        ("60.0", "(10)"): "5.2385025e-10",
        ("60.0", "(11)"): "3.100545e-10",
    }
    assert len(observations) == len(expected_pa)
    for observation in observations:
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        composition = conditions["xSnO_mol_pct"]
        equation = conditions["equilibrium_equation_as_printed"]
        key = (str(composition.state.value.point), equation.state.value)
        assert key in expected_pa
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.identity.species.formula == "O"
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(expected_pa[key])
        assert observation.identity.temperature_K.value == Decimal("1300.0")
        for condition in (temperature, composition, equation):
            assert condition.state.is_value
            assert condition.locator is not None
            assert condition.locator.table == "4"
        assert temperature.state.value == Decimal("1300.0")
        assert composition.state.value.kind is ValueKind.POINT


def test_shilov_table5_oxide_activities_migrate_with_method_conditions():
    observations = _observations(
        "shilov-1998-sno-zno-p2o5-vaporization.yaml",
        "shilov_1998_table5_",
    )
    expected = {
        ("SnO", "40.0", "single"): "1.42e-4",
        ("SnO", "46.7", "single"): "2.39e-4",
        ("SnO", "53.3", "single"): "2.64e-4",
        ("SnO", "60.0", "single"): "2.69e-4",
        ("SnO", "66.7", "single"): "3.46e-4",
        ("ZnO", "0.0", "single"): "4.45e-3",
        ("ZnO", "40.0", "single"): "4.11e-5",
        ("ZnO", "46.7", "single"): "2.36e-5",
        ("ZnO", "53.3", "single"): "1.46e-5",
        ("ZnO", "60.0", "single"): "1.29e-5",
        ("P2O5", "0.0", "equations 13 and 14"): "2.61e-4",
        ("P2O5", "40.0", "equations 13 and 14"): "5.20e-8",
        ("P2O5", "40.0", "equation 19"): "4.67e-8",
        ("P2O5", "46.7", "equations 13 and 14"): "4.28e-8",
        ("P2O5", "46.7", "equation 19"): "5.70e-8",
        ("P2O5", "53.3", "equations 13 and 14"): "7.84e-8",
        ("P2O5", "53.3", "equation 19"): "6.25e-8",
        ("P2O5", "60.0", "equations 13 and 14"): "3.33e-7",
        ("P2O5", "60.0", "equation 19"): "2.10e-7",
    }
    assert len(observations) == len(expected)
    for observation in observations:
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        composition = conditions["xSnO_mol_pct"]
        formula = observation.identity.species.formula
        method = (
            conditions["calculation_method_as_printed"].state.value
            if formula == "P2O5"
            else "single"
        )
        key = (formula, str(composition.state.value.point), method)
        assert key in expected
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(expected[key])
        assert observation.identity.temperature_K.value == Decimal("1300.0")
        for condition in (temperature, composition):
            assert condition.state.is_value
            assert condition.locator is not None
            assert condition.locator.table == "5"
        assert temperature.state.value == Decimal("1300.0")
        assert composition.state.value.kind is ValueKind.POINT
        if formula == "P2O5":
            method_condition = conditions["calculation_method_as_printed"]
            assert method_condition.state.is_value
            assert method_condition.locator is not None
            assert method_condition.locator.table == "5"
