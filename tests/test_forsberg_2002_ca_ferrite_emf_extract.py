from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/forsberg-2002-ca-fe-o-incongruent-melting.yaml"


def _row(document, species, section, observation_id):
    return next(
        row
        for row in document["species"][species][section]
        if row["observation_id"] == observation_id
    )


def _assert_render_pins(document):
    expected = {
        "forsberg2002_eq19_dfG_caFe2o4": (
            "CaFe2O4", "delta_fG_function_as_printed",
            "−1448.4 × 10^3 + 128.0 T + 27.95 T ln(T) ±1800 J mol−1", 4, "19",
        ),
        "forsberg2002_eq20_dfG_ca2fe2o5": (
            "Ca2Fe2O5", "delta_fG_function_as_printed",
            "−2049.9 × 10^3 − 12.84 T + 55.91 T ln(T) ±2800 J mol−1", 4, "20",
        ),
        "forsberg2002_eq24_dfH298_caFe2o4": (
            "CaFe2O4", "delta_H298_as_printed", "−17.5 ± 0.4 kJ mol−1", 6, "24",
        ),
    }
    for observation_id, (species, key, value, page, equation) in expected.items():
        row = _row(document, species, "observations", observation_id)
        assert row["values"][key] == value
        assert row["locator"]["page"] == page
        assert row["locator"]["equation"] == equation
    cell2 = _row(document, "CaFe2O4", "context", "forsberg2002_cell2_emf_fit")
    cell3 = _row(document, "Ca2Fe2O5", "context", "forsberg2002_cell3_emf_fit")
    assert cell2["values"]["fit_as_printed"] == "E(2/mV) = 163.3 + 0.01664 T ±2.2 mV"
    assert cell2["values"]["temperature_range_as_printed"] == "1030 < T/K < 1280"
    assert cell2["values"]["temperature_range_locator"] == {"page": 4, "published_page": 388}
    assert cell3["values"]["fit_as_printed"] == "E(3/mV) = −31.20 + 0.1267 T ±2.9 mV"
    assert cell3["values"]["reaction_as_printed"] == "CaO(s) + CaFe2O4(s) → Ca2Fe2O5(s)"
    assert cell2["locator"] == {"page": 3, "equation": "4", "published_page": 387}
    assert cell3["locator"] == {"page": 4, "equation": "5", "published_page": 388}
    assert all(
        row["observation_id"] != "forsberg2002_cell3_emf_fit"
        for row in document["species"]["CaFe2O4"]["context"]
    )
    assert "385–392" in document["source"]["citation"]
    eq14 = _row(document, "Ca2Fe2O5", "observations", "forsberg2002_eq14_reaction_gibbs_ca2fe2o5")
    assert eq14["values"]["reaction_as_printed"] == "2CaO(s) + Fe2O3(s) → Ca2Fe2O5(s)"
    assert "Equation (13) plus Equation (12)" in eq14["derivation"]["relation"]
    assert "cell [3] reaction [10]" in eq14["values"]["lineage_note"]
    assert "Equation (13) reaction fit" in eq14["values"]["lineage_note"]


def test_render_pins_equations_and_row_counts_fail_after_value_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    _assert_render_pins(document)
    table1 = _row(document, "CaFe2O4", "context", "forsberg2002_tableI_auxiliary_calorimetry")
    table2 = _row(document, "CaFe2O4", "context", "forsberg2002_tableII_calorimetry")
    assert len(table1["values"]["rows_as_printed"]) == 6
    assert len(table2["values"]["rows_as_printed"]) == 4
    assert table1["locator"] == {"page": 5, "table": "I", "published_page": 389}
    assert table2["locator"] == {"page": 5, "table": "II", "published_page": 389}
    assert table1["values"]["rows_as_printed"][5]["a"] == "1141.4551"
    assert table2["values"]["rows_as_printed"][3]["H_increment_this_work_J_mol"] == "178,120"

    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _row(mutated, "Ca2Fe2O5", "observations", "forsberg2002_eq20_dfG_ca2fe2o5")["values"][
        "delta_fG_function_as_printed"
    ] = "−2049.8 × 10^3 − 12.84 T + 55.91 T ln(T) ±2800 J mol−1"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_isolated_migration_keeps_only_reaction_gibbs_schema_gaps():
    migrator = migrate.Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 5
    assert result.validation.hard_issues == ()
    by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(by_id) == {
        "forsberg2002_eq13_reaction_gibbs_caFe2o4",
        "forsberg2002_eq14_reaction_gibbs_ca2fe2o5",
        "forsberg2002_eq19_dfG_caFe2o4",
        "forsberg2002_eq20_dfG_ca2fe2o5",
        "forsberg2002_eq24_dfH298_caFe2o4",
    }
    quantity_unknown = {
        observation.observation_id.rsplit("::", 1)[-1]
        for observation in by_id.values()
        if observation.identity.quantity.is_unknown
    }
    assert quantity_unknown == {
        "forsberg2002_eq13_reaction_gibbs_caFe2o4",
        "forsberg2002_eq14_reaction_gibbs_ca2fe2o5",
        "forsberg2002_eq24_dfH298_caFe2o4",
    }
    assert all(observation.identity.species.phase.is_value for observation in by_id.values())
    assert all(observation.value.kind.value == "unavailable" for observation in by_id.values())
    for observation in by_id.values():
        assert observation.point_conditions is not None
        assert observation.point_conditions["fO2_control"].state.is_value
        assert observation.point_conditions["fO2_control"].locator.page == 2
        assert observation.point_conditions["fO2_control"].state.value["oxygen_partial_pressure_Pa"] == "20000"
        assert observation.point_conditions["fO2_control"].state.value["atmosphere"] == "20 mole pct O2 in Ar"
        assert observation.identity.total_pressure_Pa is None or not observation.identity.total_pressure_Pa.is_value
    enthalpy = by_id["forsberg2002_eq24_dfH298_caFe2o4"]
    assert enthalpy.identity.quantity.is_unknown
    assert enthalpy.value.kind.value == "unavailable"
    assert enthalpy.evidence.original_method_class == "measured_reduced"
    assert enthalpy.point_conditions["temperature_K"].state.value == 298
    for local_id in (
        "forsberg2002_eq19_dfG_caFe2o4",
        "forsberg2002_eq20_dfG_ca2fe2o5",
    ):
        observation = by_id[local_id]
        assert observation.identity.quantity.is_value
        assert observation.identity.species.phase.value.value == "cr"
        assert observation.identity.temperature_K.is_unknown
        assert "no midpoint invented" in observation.identity.temperature_K.reason
        assert observation.identity.total_pressure_Pa.is_not_applicable
        assert observation.identity.fO2_Pa.is_not_applicable
    assert all(
        observation.identity.total_pressure_Pa is None
        or observation.identity.total_pressure_Pa.tag.value != "value"
        for observation in by_id.values()
    )

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for experiment in document["experiments"]:
        assert experiment["pressure_environment"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert experiment["fO2_control"]["oxygen_partial_pressure_Pa"]["state"]["value"]["point"] == "20000"
