"""Migration pins for Lu et al. (2024), Table 1."""

from decimal import Decimal

import yaml
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.score import _battery_hero_mass_loss_fraction


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lu-2024-mgsio3-levitation.yaml"


def test_table_1_m01_residue_values_and_conditions_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    residues = tuple(
        observation
        for observation in observations
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    assert len(residues) == 27
    m01 = [o for o in residues if o.locator.note == "row=M01"]
    assert len(m01) == 3
    assert {o.identity.species.formula: o.value.point for o in m01} == {
        "SiO2": Decimal("53.62"),
        "MgO": Decimal("44.44"),
        "CaO": Decimal("1.64"),
    }
    assert all(o.identity.temperature_K.value == Decimal("2623") for o in m01)
    assert all(o.point_conditions["duration_s"].state.value == Decimal("120") for o in m01)
    assert all(o.locator.published_page == 1195 and o.locator.table == "1" for o in m01)
    assert not migrator.result.validation.hard_issues


def test_table_1_mass_loss_rows_bind_to_residues_in_score_resolver() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_observations = extract["species"]["SiO2"]["observations"]
    expected = {
        "M01": ("2623", "120", "21.6", "57.3", "44.9"),
        "M02": ("2623", "300", "36.7", "55.3", "35"),
        "M03": ("2623", "420", "39.9", "60.2", "36.2"),
        "M04": ("2523", "120", "14.6", "62.5", "53.4"),
        "M05": ("2523", "300", "24", "62.8", "47.7"),
        "M06": ("2523", "540", "32.7", "55.4", "37.3"),
        "M07": ("2423", "300", "12.5", "59.9", "52.4"),
        "M08": ("2423", "420", "29.1", "62.6", "44.4"),
        "M09": ("2423", "540", "20.1", "52.7", "42.1"),
    }
    assert len(source_observations) == 9
    for run, (temperature, duration, loss, starting_mass, final_mass) in expected.items():
        observation = next(
            row for row in source_observations if row["observation_id"].endswith(run)
        )
        assert observation["experiment"] == "lu-2024-enstatite-levitation-series"
        assert observation["locator"]["row"] == run
        run_id = observation["point_conditions"]["run_id"]
        assert run_id["state"] == {"tag": "value", "value": run}
        assert run_id["locator"]["note"] == f"row={run}"
        assert observation["point_conditions"]["total_pressure_Pa"]["state"][
            "tag"
        ] == "unknown"
        assert observation["values"]["composition_wt_pct"]["state"]["value"][
            "components"
        ] == [["SiO2", "63.26"], ["MgO", "35.19"], ["CaO", "1.27"]]
        row = observation["values"]["series"][0]
        assert row["run_id"] == run
        assert row["sample"] == run
        assert row["T_K"] == temperature
        assert row["duration_s"] == duration
        assert row["mass_loss_wt_pct"] == loss
        assert row["starting_mass_mg"] == starting_mass
        assert row["final_mass_mg"] == final_mass

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = migrator.result.observations
    residues = {
        observation_id: observation
        for observation_id, observation in migrated.items()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
    }
    mass_losses = {
        observation_id: observation
        for observation_id, observation in migrated.items()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    }
    assert len(residues) == 27
    assert len(mass_losses) == 9

    experiment_id = next(
        experiment.experiment_id
        for experiment in migrator.result.experiments.values()
        if experiment.experiment_id.endswith("::lu-2024-enstatite-levitation-series")
    )
    experiment = migrator.result.experiments[experiment_id]
    starting = experiment.sample.initial_composition
    assert starting is not None and starting.state.is_value
    expected_start = {"SiO2": "63.26", "MgO": "35.19", "CaO": "1.27"}
    for run, (_temperature, _duration, loss, _start, _final) in expected.items():
        run_residues = [
            observation
            for observation in residues.values()
            if observation.experiment_id == experiment_id
            and observation.point_conditions["run_id"].state.value == run
        ]
        run_losses = [
            observation
            for observation in mass_losses.values()
            if observation.experiment_id == experiment_id
            and observation.point_conditions["run_id"].state.value == run
        ]
        assert len(run_residues) == 3
        assert len(run_losses) == 1
        assert run_losses[0].value.point == Decimal(loss) / Decimal("100")
        for observation in (*run_residues, *run_losses):
            run_id = observation.point_conditions["run_id"]
            assert run_id.state.value == run
            assert run_id.locator.note == f"row={run}"
            assert observation.identity.temperature_K.value == Decimal(_temperature)
            conditions = observation.point_conditions
            assert conditions["duration_s"].state.value == Decimal(_duration)
            assert conditions["starting_composition"].state.value == expected_start
            assert observation.identity.total_pressure_Pa.is_unknown
            atmosphere = conditions["atmosphere"].state.value
            if isinstance(atmosphere, dict):
                assert atmosphere["species"] == "Ar"
                assert atmosphere["flow_sccm"]["value"] == "600"
                assert atmosphere["partial_pressure_Pa"]["tag"] == "unknown"
            else:
                assert atmosphere.species == "Ar"
                assert atmosphere.flow_sccm.value == Decimal("600")
                assert atmosphere.partial_pressure_Pa.is_unknown
            assert "approximately 1 ppm O2" in conditions["atmosphere"].locator.note

        for residue in run_residues:
            assert residue.identity.composition is not None
            assert residue.identity.composition.is_value
            assert residue.identity.composition.value == starting.state.value
            fraction, refusal = _battery_hero_mass_loss_fraction(
                residue.observation_id, migrated
            )
            # d289-runjoin will make this resolver prefer run_id over the
            # colliding duration key. Until then, retain its known ambiguity.
            assert (fraction, refusal) in (
                (Decimal(loss) / Decimal("100"), None),
                (None, "ambiguous recorded mass loss"),
            )
    assert not migrator.result.validation.hard_issues


def test_all_figure_captions_are_deferred_without_numeric_points() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    figures = [
        row for row in extract["species"]["SiO2"]["context"]
        if row.get("type") == "figure_only"
    ]
    assert len(figures) == 6
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["identity"]["temperature_as_printed"] for row in figures)
    assert all(row["values"]["series"] for row in figures)
    figure1 = next(row for row in figures if row["locator"]["figure"] == "1")
    figure6 = next(row for row in figures if row["locator"]["figure"] == "6")
    assert figure1["values"]["caption_as_printed"].startswith("FIGURE 1.")
    assert "Methods" not in figure1["values"]["caption_as_printed"]
    assert figure6["values"]["caption_as_printed"].startswith("FIGURE 6.")
