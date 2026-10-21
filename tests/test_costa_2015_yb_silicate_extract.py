from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/costa-2015-yb-silicate-silica-activity.yaml"
OBSERVATION_ID = "costa_2015_literature_delta_fH_yb2sio5"


def test_costa_2015_quoted_formation_enthalpy_migrates_as_one_point() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={EXTRACT.stem: dict(extract["source"], source_id=EXTRACT.stem)},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    matching = [
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(f"::{OBSERVATION_ID}")
    ]
    assert len(matching) == 1
    observation = matching[0]
    assert observation.value.kind.value == "point"
    assert str(observation.value.point) == "-2774.75"
    assert observation.identity.quantity.value.value == "delta_fH"
    assert observation.identity.species.formula == "Yb2SiO5"
    assert observation.identity.temperature_K.value == 298
    assert observation.point_conditions["temperature_K"].state.value == 298
    assert observation.uncertainty.kind.value == "printed"
    assert observation.uncertainty.verbatim == "±8.21 kJ/mol"
    assert extract["species"]["Yb2SiO5"]["observations"][0]["values"]["sigma"] == "8.21"
    assert observation.evidence.class_.value.value == "quoted_attributed"
    assert observation.notices == ()

    species = extract["species"]["Yb2SiO5"]
    assert len(species["observations"]) == 1
    assert not any(
        observation.value.kind.value == "unavailable"
        for observation in migrator.result.observations.values()
    )
    context = species["context"]
    flux = next(record for record in context if record["observation_id"] == "costa_2015_table_5_area_specific_flux")
    assert "no store token" not in flux["values"]["schema_gap"]
    assert "converter" in flux["values"]["schema_gap"]
    assert {record["type"] for record in context} >= {
        "measured_bulk_sample_analysis",
        "thermochemical_cycle_context",
        "model_table",
        "measured_and_model_flux_table",
        "figure_record",
    }
    assert sum(record["type"] == "figure_record" for record in context) == 9
    assert all(
        record["values"].get("digitisation") == "deferred to a figure pass"
        for record in context
        if record["type"] == "figure_record"
    )
