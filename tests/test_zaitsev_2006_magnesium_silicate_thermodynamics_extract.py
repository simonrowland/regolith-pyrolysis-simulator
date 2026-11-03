from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "zaitsev-2006-magnesium-silicate-thermodynamics"
EXTRACT = (
    ROOT
    / "data/literature/extracts/zaitsev-2006-magnesium-silicate-thermodynamics.yaml"
)
EXPECTED_QUANTITY_UNKNOWN = {
    "zaitsev_2006_table1_mgo_mg2sio4_ratio_fit",
    "zaitsev_2006_table1_mgsio3_mg2sio4_ratio_fit",
    "zaitsev_2006_table1_mgsio3_sio2_ratio_fit",
    "zaitsev_2006_eq6_mgo_partial_gibbs_fit",
    "zaitsev_2006_eq7_sio2_partial_gibbs_fit",
    "zaitsev_2006_eq8_mgsio3_gibbs_fit",
    "zaitsev_2006_eq9_mg2sio4_gibbs_fit",
    "zaitsev_2006_eq16_mg2sio4_model_function",
    "zaitsev_2006_eq17_mgsio3_model_function",
}


def _source_observation(document: dict, observation_id: str) -> dict:
    return next(
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == observation_id
    )


def _fidelity_pins(document: dict) -> tuple[tuple[str, int, str], ...]:
    pins = (
        (
            "zaitsev_2006_table2_mgo_x_sio2_0p486",
            "activity",
        ),
        (
            "zaitsev_2006_table2_sio2_x_sio2_0p563",
            "activity",
        ),
        (
            "zaitsev_2006_table4_enstatite_calculated_delta_fG",
            "delta_fG",
        ),
        (
            "zaitsev_2006_eq8_mgsio3_gibbs_fit",
            "equation_as_published",
        ),
    )
    return tuple(
        (
            _source_observation(document, observation_id)["values"][field],
            _source_observation(document, observation_id)["locator"]["page"],
            _source_observation(document, observation_id)["locator"].get(
                "table",
                _source_observation(document, observation_id)["locator"].get(
                    "equation", ""
                ),
            ),
        )
        for observation_id, field in pins
    )


def test_zaitsev_2006_printed_rows_render_pins_and_isolated_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    ]
    table1 = [row for row in observations if row["locator"].get("table") == "1"]
    table2 = [row for row in observations if row["locator"].get("table") == "2"]
    table4 = [row for row in observations if row["locator"].get("table") == "4"]

    assert len(observations) == 25
    assert len(table1) == 3
    assert len(table2) == 12
    assert len(table4) == 4
    assert len(
        {
            row["values"]["x_SiO2_as_printed"]
            for row in table2
        }
    ) == 6
    assert {
        row["values"]["quantity"] for row in table4
    } == {"delta_fG"}

    partial_gibbs_parents = {
        "zaitsev_2006_eq6_mgo_partial_gibbs_fit": (
            "zaitsev_2006_table1_mgo_mg2sio4_ratio_fit",
            "zaitsev_2006_table1_mgsio3_mg2sio4_ratio_fit",
        ),
        "zaitsev_2006_eq7_sio2_partial_gibbs_fit": (
            "zaitsev_2006_table1_mgsio3_mg2sio4_ratio_fit",
            "zaitsev_2006_table1_mgsio3_sio2_ratio_fit",
        ),
    }
    for observation_id, expected_parents in partial_gibbs_parents.items():
        observation = _source_observation(document, observation_id)
        assert tuple(observation["derived_from"]) == expected_parents
        assert tuple(observation["derivation"]["inputs"]) == expected_parents
    assert _source_observation(
        document, "zaitsev_2006_eq6_mgo_partial_gibbs_fit"
    )["derivation"]["relation"] == (
        "Equation 4 subtracts the MgO + Mg2SiO4 (prime) ratio fit from the "
        "MgSiO3 + Mg2SiO4 (double-prime) ratio fit to reduce the measured "
        "KEMS ion-current ratio to the partial Gibbs energy of MgO."
    )
    eq7 = _source_observation(document, "zaitsev_2006_eq7_sio2_partial_gibbs_fit")
    assert eq7["derivation"]["relation"] == (
        "Equation 5 subtracts the MgSiO3 + Mg2SiO4 (double-prime) ratio fit "
        "from the MgSiO3 + SiO2 (triple-prime) ratio fit to reduce the measured "
        "KEMS ion-current ratio to the partial Gibbs energy of SiO2."
    )
    assert eq7["values"]["sign_consistency_note"] == (
        "The printed +(2.01 ± 0.24)T slope has the opposite sign from the "
        "Table 1 fit and Eq. 5; Eq. 8's slope 0.14 = 2.15 − 2.01. "
        "Coefficients remain exactly as printed."
    )

    table2_mgo_inputs = tuple(
        f"zaitsev_2006_table2_mgo_x_sio2_0p{x}"
        for x in ("486", "498", "511", "528", "550", "563")
    )
    table2_sio2_inputs = tuple(
        f"zaitsev_2006_table2_sio2_x_sio2_0p{x}"
        for x in ("486", "498", "511", "528", "550", "563")
    )
    model_inputs = (
        "zaitsev_2006_eq8_mgsio3_gibbs_fit",
        "zaitsev_2006_eq9_mg2sio4_gibbs_fit",
        *table2_mgo_inputs,
        *table2_sio2_inputs,
        "zaitsev_2006_table4_enstatite_experimental_delta_fG",
        "zaitsev_2006_table4_protoenstatite_experimental_delta_fG",
    )
    for model_id in (
        "zaitsev_2006_eq16_mg2sio4_model_function",
        "zaitsev_2006_eq17_mgsio3_model_function",
    ):
        model = _source_observation(document, model_id)
        assert tuple(model["derived_from"]) == model_inputs
        assert tuple(model["derivation"]["inputs"]) == model_inputs

    expected_pins = (
        ("0.181", 4, "2"),
        ("1.0", 4, "2"),
        ("-32150", 7, "4"),
        ("ΔfG(MgSiO3) = −(30842 ± 413) + (0.14 ± 0.29)T", 3, "8"),
    )
    assert _fidelity_pins(document) == expected_pins

    mutated = deepcopy(document)
    _source_observation(
        mutated, "zaitsev_2006_table2_mgo_x_sio2_0p486"
    )["values"]["activity"] = "0.182"
    with pytest.raises(AssertionError):
        assert _fidelity_pins(mutated) == expected_pins

    for row in observations:
        conditions = row["point_conditions"]
        assert set(conditions) == {
            "temperature_K",
            "composition",
            "fO2_Pa",
            "total_pressure_Pa",
        }
        for condition in conditions.values():
            assert "state" in condition
            assert "locator" in condition
        assert conditions["fO2_Pa"]["state"]["tag"] == "unknown"
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"

    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 25
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in result.observations.values()
    ) == 9

    prefix = f"{SOURCE_ID}::"
    migrated = {
        observation.observation_id.removeprefix(prefix): observation
        for observation in result.observations.values()
    }
    quantity_unknown = {
        observation_id
        for observation_id, observation in migrated.items()
        if not observation.identity.quantity.is_value
    }
    assert quantity_unknown == EXPECTED_QUANTITY_UNKNOWN
    assert all(
        observation.identity.species.phase.is_value
        for observation in result.observations.values()
    )

    for row in observations:
        observation = migrated[row["observation_id"]]
        source_temperature = row["point_conditions"]["temperature_K"]
        temperature = source_temperature["state"]
        migrated_temperature = observation.point_conditions["temperature_K"]
        assert migrated_temperature.locator.page == source_temperature["locator"]["page"]
        if temperature["tag"] == "value":
            expected_temperature = Decimal(temperature["value"]["point"])
            assert migrated_temperature.state.value == expected_temperature
        else:
            assert migrated_temperature.state.tag.value == "unknown"

    for observation_id, expected_composition in (
        (
            "zaitsev_2006_table2_mgo_x_sio2_0p486",
            {"MgO": Decimal("0.514"), "SiO2": Decimal("0.486")},
        ),
        (
            "zaitsev_2006_table2_sio2_x_sio2_0p511",
            {"MgO": Decimal("0.489"), "SiO2": Decimal("0.511")},
        ),
        (
            "zaitsev_2006_table2_sio2_x_sio2_0p563",
            {"MgO": Decimal("0.437"), "SiO2": Decimal("0.563")},
        ),
    ):
        observation = migrated[observation_id]
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K.value == Decimal("1873")
        assert dict(observation.identity.composition.value.components) == expected_composition
        assert observation.identity.total_pressure_Pa.tag.value == "unknown"
        assert observation.identity.fO2_Pa.tag.value == "unknown"

    assert migrated["zaitsev_2006_table2_mgo_x_sio2_0p486"].value.point == Decimal(
        "0.181"
    )
    assert migrated["zaitsev_2006_table2_sio2_x_sio2_0p563"].value.point == Decimal(
        "1.0"
    )
    enstatite_calculated = migrated[
        "zaitsev_2006_table4_enstatite_calculated_delta_fG"
    ]
    assert enstatite_calculated.value.point == Decimal("-32.15")
    assert enstatite_calculated.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert migrated[
        "zaitsev_2006_table4_enstatite_experimental_delta_fG"
    ].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated[
        "zaitsev_2006_eq8_mgsio3_gibbs_fit"
    ].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
