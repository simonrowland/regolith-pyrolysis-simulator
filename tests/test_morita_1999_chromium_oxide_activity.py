"""Render-anchored extraction and migration guards for Morita et al. (1999)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/morita-1999-chromium-oxide-activity.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _render_pins(document: dict) -> None:
    # Table 1, printed p. 320 / PDF p. 2, checked against the page render.
    raw = document["species"]["Cr"]["observations"]
    rows = [row for row in raw if row["observation_id"].startswith("morita_1999_t1_row_r")]
    assert len(rows) == 35, "Table 1 render contains runs 1 through 35"
    assert [int(row["values"]["run_number_as_published"]) for row in rows] == list(range(1, 36))

    row22 = _observation(document, "Cr", "morita_1999_t1_row_r22")["values"]
    row35 = _observation(document, "Cr", "morita_1999_t1_row_r35")["values"]
    assert row22["CrOx_mass_percent_as_published"]["value"] == "28.1"
    assert row35["Cr3plus_mass_percent_as_published"]["value"] == "3.63"
    assert row35["Cr3plus_Cr2plus_ratio_as_published"] == "12.3"
    assert (
        _observation(document, "CrO", "morita_1999_t1_gamma_cro_r14")["values"]["points"][0]["gamma"]
        == "17.7"
    )
    assert (
        _observation(document, "CrO", "morita_1999_t1_gamma_cro_r22")["values"]["points"][0]["gamma"]
        == "76.8"
    )
    assert (
        _observation(document, "CrO1.5", "morita_1999_t1_gamma_cro15_r22")["values"]["points"][0]["gamma"]
        == "3.71"
    )
    assert (
        _observation(document, "Cr", "morita_1999_t1_activity_cr_cu_r35")["values"]["points"][0]["activity"]
        == "0.0147"
    )


def test_table1_render_pins_cover_row_count_and_detect_a_mutated_cell():
    document = _document()
    assert check_all_fidelity_samples_match(document) == []
    _render_pins(document)

    mutated = deepcopy(document)
    _observation(mutated, "CrO", "morita_1999_t1_gamma_cro_r22")["values"]["points"][0]["gamma"] = "76.9"
    with pytest.raises(AssertionError, match="assert"):
        _render_pins(mutated)


def test_isolated_migration_keeps_activity_axes_and_refuses_schema_gaps():
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 210
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert sum(o.admission.status.value == "admitted" for o in result.observations.values()) == 105
    assert sum(o.value.kind is ValueKind.UNAVAILABLE for o in result.observations.values()) == 105

    required_conditions = {
        "temperature_K",
        "composition",
        "fO2_Pa",
        "fO2_control",
    }
    for observation in result.observations.values():
        conditions = observation.point_conditions or {}
        assert required_conditions <= set(conditions)
        assert "total_pressure_Pa" not in conditions

    activities = [o for o in result.observations.values() if o.admission.status.value == "admitted"]
    assert len(activities) == 105
    for observation in activities:
        identity = observation.identity
        method = result.experiments[observation.experiment_id].method
        assert method.is_value and method.value.value == "quench_equilibration"
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        assert identity.composition is not None and identity.composition.is_value
        assert identity.fO2_Pa is not None and identity.fO2_Pa.is_value
        assert identity.total_pressure_Pa is not None and identity.total_pressure_Pa.is_unknown
        required = profile_for(identity).required
        assert {"temperature_K", "composition", "fO2_Pa"} <= required

    # These three rows exercise both isothermal and temperature-series conditions.
    for observation_id in (
        "gamma_cro_r14",
        "gamma_cro15_r25",
        "activity_cr_cu_r35",
    ):
        observation = next(o for o in activities if observation_id in o.observation_id)
        assert observation.identity.temperature_K is not None
        assert observation.identity.composition is not None
        assert observation.identity.fO2_Pa is not None
        assert observation.identity.total_pressure_Pa is not None
        print(
            observation_id,
            "required=",
            sorted(profile_for(observation.identity).required),
            "temperature_K=",
            observation.identity.temperature_K,
            "composition=",
            observation.identity.composition,
            "fO2_Pa=",
            observation.identity.fO2_Pa,
            "total_pressure_Pa=",
            observation.identity.total_pressure_Pa,
        )
