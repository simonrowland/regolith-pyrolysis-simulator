"""Render pins and isolated migration for Richter et al. (2011)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.enums import ValueKind


SID = "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
FIT_IDS = {
    "richter_2011_text_alpha_k_vacuum_fit",
    "richter_2011_text_beta_k_vacuum_fit",
    "richter_2011_text_alpha_k_co2_h2_measured",
    "richter_2011_text_alpha_k_he_measured",
    "richter_2011_text_alpha_k_h2_measured",
    "richter_2011_text_alpha_diff_co2_h2_theoretical",
    "richter_2011_text_alpha_diff_he_theoretical",
    "richter_2011_text_alpha_diff_h2_theoretical",
}
TYPED_REFUSAL_IDS = {"richter_2011_table1_iiab1-3_residue_na2o"}
IIAB2_STARTING_COMPOSITION_REASON = (
    "Starting assay not specified for later IIAB2 runs: IIAB2-13 or IIAB2-15 "
    "(Table 1, page 39, both zero-time); the paper does not map assay rows to individual runs."
)


def _document(path: Path = EXTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (species, observation)
        for species, body in document["species"].items()
        for observation in body.get("observations", ())
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 388

    residue_rows = {
        observation_id: observation
        for observation_id, (_species, observation) in observations.items()
        if observation.get("values", {}).get("quantity") == "residue_composition_vs_time"
    }
    assert len(residue_rows) == 325
    isotopes = {
        observation_id: observation
        for observation_id, (_species, observation) in observations.items()
        if observation.get("values", {}).get("quantity") == "isotope_delta"
    }
    assert len(isotopes) == 21
    gamma_rows = {
        observation_id: observation
        for observation_id, (_species, observation) in observations.items()
        if observation_id.startswith("richter_2011_table2_")
    }
    assert len(gamma_rows) == 34
    assert len(FIT_IDS & observations.keys()) == 8
    assert len({obs["locator"]["draft_row"] for obs in gamma_rows.values()}) == 17

    sio2 = observations["richter_2011_table1_iiab1-1_residue_sio2"][1]
    assert sio2["locator"]["page"] == 39
    assert sio2["locator"]["table"] == "1"
    assert sio2["locator"]["row"] == "IIAB1-1"
    assert sio2["values"]["series"][0]["SiO2_wt_pct"] == "62.21"

    na2o = observations["richter_2011_table1_iiab1-3_residue_na2o"][1]
    assert na2o["locator"]["page"] == 39
    assert na2o["locator"]["table"] == "1"
    assert na2o["locator"]["row"] == "IIAB1-3"
    assert na2o["values"]["series"][0]["Na2O_wt_pct"] == "<0.01"
    assert na2o["values"]["censored_value_as_printed"] == "<0.01"
    assert na2o["values"]["admission_status"] == "typed_refusal"
    assert na2o["values"]["schema_gap_symbols"] == [
        "simulator/battery/migrate.py::Quantity.RESIDUE_COMPONENT_COMPOSITION",
        "simulator/battery/migrate.py::select_declared_source",
        "simulator/battery/migrate.py::_classify_composition_cell",
    ]

    isotope = observations["richter_2011_table1_iiab5-18_delta41k"][1]
    assert isotope["locator"]["page"] == 39
    assert isotope["locator"]["table"] == "1"
    assert isotope["locator"]["row"] == "IIAB5-18"
    assert isotope["values"]["series"][0]["isotope_delta_isotope_delta"] == "10.6"
    assert isotope["values"]["series"][0]["two_sigma_permil"] == "0.53"

    gamma = observations["richter_2011_table2_iiab-1_gamma_k"][1]
    assert gamma["locator"]["page"] == 40
    assert gamma["locator"]["table"] == "2"
    assert gamma["locator"]["row"] == "IIAB-1"
    assert gamma["values"]["alpha"] == "0.051"
    assert gamma["values"]["method_class"] == "measured_reduced"

    average = observations["richter_2011_table2_iiab1-ave-std-dev_gamma_k"][1]
    assert average["locator"]["page"] == 40
    assert average["values"]["alpha"] == "0.061"
    assert average["values"]["uncertainty"]["value"] == ".014"
    assert average["values"]["uncertainty"]["basis"] == "Average row explicitly labels ± as standard deviation."

    # The two zero isotope entries are retained on their printed starting/baseline rows.
    starts = {
        row["sample"]["sample_id"]: row
        for row in document["experiments"]
        if row.get("kind") in {"starting_material", "zero_time_baseline"}
    }
    assert starts["IIAB5-start"]["sample"]["oxide_wt_pct_as_printed"]["K2O"] == "5"
    assert starts["IIAB5-start"]["sample"]["analysis_method_as_printed"]
    assert starts["50Di50Le-0"]["sample"]["delta41K_as_printed"] == "0.00"


def test_table_values_are_render_pinned_and_mutations_fail(tmp_path: Path) -> None:
    document = _document()
    _assert_render_pins(document)

    changed = deepcopy(document)
    changed_observation = next(
        row
        for row in changed["species"]["SiO2"]["observations"]
        if row["observation_id"] == "richter_2011_table1_iiab1-1_residue_sio2"
    )
    changed_observation["values"]["series"][0]["SiO2_wt_pct"] = "62.22"
    mutation_path = tmp_path / EXTRACT.name
    mutation_path.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_render_pins(_document(mutation_path))


def test_later_iiab2_starting_composition_is_unknown_and_zero_time_assays_remain() -> None:
    document = _document()
    iiab2_experiments = {
        experiment["sample"]["sample_id"]: experiment
        for experiment in document["experiments"]
        if experiment.get("sample", {}).get("sample_id", "").startswith("IIAB2-")
        and int(experiment["sample"]["sample_id"].rsplit("-", 1)[1]) >= 11
    }
    assert len(iiab2_experiments) == 14

    for sample_id, experiment in iiab2_experiments.items():
        printed_composition = experiment["sample"]["printed_composition"]
        assert printed_composition["state"] == {
            "tag": "unknown",
            "reason": IIAB2_STARTING_COMPOSITION_REASON,
        }
        assert printed_composition["locator"] == {
            "page": 39,
            "pdf_page_index": 38,
            "published_page": 38,
            "table": "1",
            "row": sample_id,
        }

    assay_ids = {"IIAB2-13", "IIAB2-15"}
    assert assay_ids <= iiab2_experiments.keys()
    assay_compositions = {sample_id: {} for sample_id in assay_ids}
    for species, body in document["species"].items():
        for observation in body.get("observations", ()):
            row = observation.get("locator", {}).get("row")
            if (
                row in assay_ids
                and observation.get("values", {}).get("quantity")
                == "residue_composition_vs_time"
            ):
                assert observation["locator"]["page"] == 39
                assert observation["locator"]["table"] == "1"
                assay_compositions[row][species] = observation["values"]["series"][0][
                    f"{species}_wt_pct"
                ]

    assert assay_compositions == {
        "IIAB2-13": {
            "SiO2": "49.94",
            "MgO": "27.85",
            "Al2O3": "4.41",
            "CaO": "3.45",
            "Na2O": "1.78",
            "K2O": "0.51",
            "FeO": "12.46",
        },
        "IIAB2-15": {
            "SiO2": "49.49",
            "MgO": "28.11",
            "Al2O3": "4.43",
            "CaO": "3.45",
            "Na2O": "1.78",
            "K2O": "0.45",
            "FeO": "12.62",
        },
    }


def test_isolated_migration_binds_conditions_and_exact_schema_gaps() -> None:
    document = _document()
    source_observations = _observations(document)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(source_observations) == 388
    assert len(result.observations) == 387
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in result.observations.values()
    ) == 8

    migrated_by_source_id = {
        source_id: [
            migrated
            for migrated in result.observations.values()
                if f"::{source_id}::" in migrated.observation_id
                or migrated.observation_id.endswith(f"::{source_id}")
        ]
        for source_id in source_observations
    }
    assert {
        source_id
        for source_id, rows in migrated_by_source_id.items()
        if not rows
    } == TYPED_REFUSAL_IDS
    assert all(
        len(rows) == 1
        for source_id, rows in migrated_by_source_id.items()
        if source_id not in TYPED_REFUSAL_IDS
    )

    for source_id, (_species, source) in source_observations.items():
        for key in ("temperature_K", "total_pressure_Pa", "fO2_Pa", "composition"):
            condition = source["point_conditions"][key]
            assert condition["locator"]
            assert condition["state"]["tag"] in {"value", "unknown"}
        if source_id in TYPED_REFUSAL_IDS:
            continue
        migrated = migrated_by_source_id[source_id][0]
        assert migrated.point_conditions is not None
        identity_axis = {
            "temperature_K": migrated.identity.temperature_K,
            "total_pressure_Pa": migrated.identity.total_pressure_Pa,
            "fO2_Pa": migrated.identity.fO2_Pa,
            "composition": migrated.identity.composition,
        }
        for key, condition in source["point_conditions"].items():
            if condition["state"]["tag"] != "value":
                continue
            mapped = (
                key in migrated.point_conditions
                and migrated.point_conditions[key].state.is_value
            )
            if key in identity_axis:
                mapped = mapped or identity_axis[key].is_value
            assert mapped, (source_id, key, condition)

    quantity_unknown = {
        source_id
        for source_id, rows in migrated_by_source_id.items()
        if rows and not rows[0].identity.quantity.is_value
    }
    assert quantity_unknown == FIT_IDS
    phase_unknown = {
        source_id
        for source_id, rows in migrated_by_source_id.items()
        if rows and not rows[0].identity.species.phase.is_value
    }
    assert phase_unknown == set()

    vacuum = migrated_by_source_id["richter_2011_table1_iiab1-1_residue_sio2"][0]
    flowing = migrated_by_source_id["richter_2011_table1_iiab5-4_residue_sio2"][0]
    helium = migrated_by_source_id["richter_2011_table1_50di50le-3_residue_sio2"][0]
    assert vacuum.value.point == Decimal("62.21")
    assert vacuum.locator.page == 39
    assert vacuum.locator.table == "1"
    for migrated in (vacuum, flowing, helium):
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.composition.is_value
    assert vacuum.identity.total_pressure_Pa.is_value
    assert vacuum.identity.fO2_Pa.is_unknown
    assert flowing.identity.total_pressure_Pa.is_value
    assert flowing.identity.fO2_Pa.is_unknown
    assert helium.identity.total_pressure_Pa.is_value
    assert helium.identity.fO2_Pa.is_unknown
    iiab2_later = migrated_by_source_id["richter_2011_table1_iiab2-14_residue_sio2"][0]
    assert iiab2_later.identity.composition.is_unknown
    later_iiab2_ids = {
        experiment["sample"]["sample_id"]
        for experiment in document["experiments"]
        if experiment.get("sample", {}).get("sample_id", "").startswith("IIAB2-")
        and int(experiment["sample"]["sample_id"].rsplit("-", 1)[1]) >= 11
    }
    later_iiab2_observations = {sample_id: [] for sample_id in later_iiab2_ids}
    for source_id, (_species, source) in source_observations.items():
        series = source.get("values", {}).get("series", ())
        run_id = series[0].get("run_id") if series else None
        if run_id in later_iiab2_ids:
            later_iiab2_observations[run_id].append(source_id)
            assert migrated_by_source_id[source_id][0].identity.composition.is_unknown, source_id
    assert all(later_iiab2_observations.values())

    average = migrated_by_source_id["richter_2011_table2_iiab1-ave-std-dev_gamma_k"][0]
    gamma = migrated_by_source_id["richter_2011_table2_iiab-1_gamma_k"][0]
    assert gamma.value.point == Decimal("0.051")
    assert gamma.locator.page == 40
    assert gamma.locator.table == "2"
    assert average.identity.temperature_K.is_unknown
    assert average.identity.total_pressure_Pa.is_unknown
    assert average.identity.fO2_Pa.is_unknown

    quantity_phase_queue = {
        item.observation_id.rsplit("::", 1)[-1]
        for item in result.queue
        if item.source == f"data/literature/extracts/{SID}.yaml"
        and ("quantity" in item.axes or "phase" in item.axes)
    }
    assert quantity_phase_queue == FIT_IDS
    assert not any(
        "phase" in item.axes
        for item in result.queue
        if item.source == f"data/literature/extracts/{SID}.yaml"
    )
