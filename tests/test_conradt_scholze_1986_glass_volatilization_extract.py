from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Engine
from simulator.battery.score import _evaporation_flux_prediction, evaporation_flux_rail
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/conradt-scholze-1986-glass-volatilization.yaml"
)


def test_conradt_scholze_tables_5_and_6_flux_cells_and_conditions(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        obs
        for species in doc["species"].values()
        for obs in species["observations"]
    ]
    assert len(observations) == 39
    na = doc["species"]["Na2O"]["observations"]
    k = doc["species"]["K2O"]["observations"]
    assert len(na) == 27
    assert len(k) == 12
    assert all("gas_species" not in obs["values"] for obs in observations)
    assert all("flow" not in obs["values"] for obs in observations)
    assert all("P_total" not in obs["values"] for obs in observations)

    # Three table-render anchors: PDF p. 9 Table 5 and PDF p. 10 Table 6.
    anchors = {
        "conradt_scholze_t5_na2o_01": ("0.2", "0.0000002", "P_H2O=0 bar; P_SO2=0 mbar; SO3=0.07 mass %"),
        "conradt_scholze_t5_na2o_13": ("0.4", "0.0000004", "P_H2O=0.1 bar; P_SO2=0 mbar; SO3=0 mass %"),
        "conradt_scholze_t6_k2o_28": ("1", "0.000001", "P_H2O=0.1 bar"),
    }
    by_id = {obs["observation_id"]: obs for obs in observations}
    for observation_id, (printed, scaled, condition) in anchors.items():
        row = by_id[observation_id]
        values = row["values"]
        assert values["evaporation_flux_as_printed"] == printed
        assert Decimal(values["evaporation_flux"]) == Decimal(scaled)
        assert values["units_as_printed"] == "10^-6 kg m^-2 s^-1"
        assert values["condition_as_printed"] == condition
        assert row["units"] == "kg m^-2 s^-1"

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert len(migrated.observations) == 39
    assert len(migrated.validation.issues) == 117
    assert {issue.reason.value for issue in migrated.validation.issues} == {
        "identity_incomplete"
    }
    for obs in migrated.observations.values():
        assert obs.identity.temperature_K.is_value
        assert obs.point_conditions["atmosphere"].state.is_value
        assert obs.point_conditions["composition"].state.is_value
        assert obs.identity.total_pressure_Pa.is_unknown
        assert obs.identity.sweep_gas.is_unknown
        assert obs.identity.composition.is_unknown
        assert obs.value.kind.value == "point"
        prediction = _evaporation_flux_prediction(
            Engine.INTERNAL_ANALYTICAL, obs, obs.identity, "test", ()
        )
        assert prediction.refusal_detail["reason"] == "missing_input"
    assert evaporation_flux_rail("Na2O") is None
    assert evaporation_flux_rail("K2O") is None
