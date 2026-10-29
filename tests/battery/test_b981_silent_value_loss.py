from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
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
