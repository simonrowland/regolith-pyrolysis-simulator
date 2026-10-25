from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = REPO_ROOT / "data/literature/extracts/stebbins-carmichael-weill-1983.yaml"


def test_stebbins_fusion_values_migrate_with_printed_fields_and_conditions() -> None:
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT)
    migrator.finalize()
    observations = tuple(migrator.result.observations.values())

    enthalpy_points = {
        observation.observation_id.split("::")[-1]: observation
        for observation in observations
        if observation.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
        and observation.observation_id.split("::")[-1]
        in {
            "stebbins_1983_diopside_heat_of_fusion_table_7",
            "stebbins_1983_diopside_ferrier_table_7_fusion_quote",
            "stebbins_1983_diopside_weill_table_7_fusion_quote",
            "stebbins_1983_diopside_robie_table_7_fusion_quote",
            "stebbins_1983_albite_heat_of_fusion_table_7",
            "stebbins_1983_albite_weill_table_7_fusion_quote",
            "stebbins_1983_albite_robie_table_7_fusion_quote",
            "stebbins_1983_sanidine_heat_of_fusion_table_7",
            "stebbins_1983_sanidine_robie_table_7_fusion_quote",
            "stebbins_1983_nepheline_heat_of_fusion_table_7",
            "stebbins_1983_anorthite_table_7_fusion_quotes",
            "stebbins_1983_anorthite_robie_table_7_fusion_quote",
        }
    }
    expected_enthalpy = {
        "stebbins_1983_diopside_heat_of_fusion_table_7": Decimal("138.072"),
        "stebbins_1983_diopside_ferrier_table_7_fusion_quote": Decimal("128.4488"),
        "stebbins_1983_diopside_weill_table_7_fusion_quote": Decimal("142.6744"),
        "stebbins_1983_diopside_robie_table_7_fusion_quote": Decimal("77.404"),
        "stebbins_1983_albite_heat_of_fusion_table_7": Decimal("62.76"),
        "stebbins_1983_albite_weill_table_7_fusion_quote": Decimal("64.4336"),
        "stebbins_1983_albite_robie_table_7_fusion_quote": Decimal("58.576"),
        "stebbins_1983_sanidine_heat_of_fusion_table_7": Decimal("57.7392"),
        "stebbins_1983_sanidine_robie_table_7_fusion_quote": Decimal("49.7896"),
        "stebbins_1983_nepheline_heat_of_fusion_table_7": Decimal("48.9528"),
        "stebbins_1983_anorthite_table_7_fusion_quotes": Decimal("135.5616"),
        "stebbins_1983_anorthite_robie_table_7_fusion_quote": Decimal("81.1696"),
    }
    assert len(enthalpy_points) == len(expected_enthalpy) == 12
    model_derived_ids = {
        "stebbins_1983_diopside_heat_of_fusion_table_7",
        "stebbins_1983_albite_heat_of_fusion_table_7",
        "stebbins_1983_sanidine_heat_of_fusion_table_7",
        "stebbins_1983_nepheline_heat_of_fusion_table_7",
    }
    temperatures = {
        "stebbins_1983_diopside_heat_of_fusion_table_7": Decimal("1665"),
        "stebbins_1983_diopside_ferrier_table_7_fusion_quote": Decimal("1665"),
        "stebbins_1983_diopside_weill_table_7_fusion_quote": Decimal("1665"),
        "stebbins_1983_diopside_robie_table_7_fusion_quote": Decimal("1665"),
        "stebbins_1983_albite_heat_of_fusion_table_7": Decimal("1373"),
        "stebbins_1983_albite_weill_table_7_fusion_quote": Decimal("1373"),
        "stebbins_1983_albite_robie_table_7_fusion_quote": Decimal("1373"),
        "stebbins_1983_sanidine_heat_of_fusion_table_7": Decimal("1473"),
        "stebbins_1983_sanidine_robie_table_7_fusion_quote": Decimal("1473"),
        "stebbins_1983_nepheline_heat_of_fusion_table_7": Decimal("1750"),
        "stebbins_1983_anorthite_table_7_fusion_quotes": Decimal("1830"),
        "stebbins_1983_anorthite_robie_table_7_fusion_quote": Decimal("1830"),
    }
    assert set(enthalpy_points) == set(expected_enthalpy)
    for observation_id, observation in enthalpy_points.items():
        assert observation.value.point == expected_enthalpy[observation_id]
        expected_class = (
            EvidenceClass.MODEL_DERIVED
            if observation_id in model_derived_ids
            else EvidenceClass.QUOTED_ATTRIBUTED
        )
        assert observation.evidence.class_.value is expected_class
        assert observation.point_conditions["temperature_K"].state.value == temperatures[observation_id]
        assert observation.point_conditions["temperature_K"].state.tag is StateTag.VALUE
        assert observation.point_conditions["temperature_K"].locator == observation.locator
        assert observation.point_conditions["pressure_bar"].state.value == Decimal("1")
        assert observation.point_conditions["pressure_bar"].locator == observation.locator
        if observation_id not in model_derived_ids:
            assert not observation.derived_from
            assert observation.derivation is None

    model_derived_points = {
        observation_id: enthalpy_points[observation_id]
        for observation_id in model_derived_ids
    }
    measured_parents = {
        "stebbins_1983_diopside_heat_of_fusion_table_7": "stebbins_1983_diopside_calorimetry_measured_tables_1_3",
        "stebbins_1983_albite_heat_of_fusion_table_7": "stebbins_1983_albite_analbite_calorimetry_measured_tables_1_5",
        "stebbins_1983_sanidine_heat_of_fusion_table_7": "stebbins_1983_sanidine_calorimetry_measured_table_3",
        "stebbins_1983_nepheline_heat_of_fusion_table_7": "stebbins_1983_nepheline_calorimetry_measured_table_3",
    }
    for observation_id, observation in model_derived_points.items():
        assert observation.derived_from
        assert observation.derivation is not None
        assert "pdf:stebbins-carmichael-weill-1983" in observation.derived_from
        assert any(
            parent.endswith(f"::{measured_parents[observation_id]}")
            for parent in observation.derived_from
        )
        assert set(observation.derivation.inputs).issubset(observation.derived_from)

    entropy_ids = {
        "stebbins_1983_diopside_entropy_of_fusion_discussion": Decimal("84.5168"),
        "stebbins_1983_albite_entropy_of_fusion_abstract": Decimal("45.6056"),
        "stebbins_1983_albite_entropy_of_fusion_discussion": Decimal("44.7688"),
        "stebbins_1983_sanidine_entropy_of_fusion_abstract": Decimal("39.3296"),
        "stebbins_1983_nepheline_entropy_of_fusion_discussion": Decimal("28.0328"),
    }
    entropy_points = {
        observation.observation_id.split("::")[-1]: observation
        for observation in observations
        if observation.identity.quantity.value is Quantity.ENTROPY_OF_FUSION
        and observation.observation_id.split("::")[-1] in entropy_ids
    }
    assert len(entropy_points) == len(entropy_ids) == 5
    assert set(entropy_points) == set(entropy_ids)
    for observation_id, observation in entropy_points.items():
        assert observation.value.point == entropy_ids[observation_id]
        assert observation.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert observation.derived_from
        assert observation.derivation is not None
        assert observation.point_conditions["temperature_K"].state.tag is StateTag.VALUE
        assert observation.point_conditions["temperature_K"].locator == observation.locator

    newly_numeric_ids = set(expected_enthalpy) | set(entropy_ids)
    lineage_issues = [
        issue
        for issue in migrator.result.validation.hard_issues
        if any(observation_id in str(issue) for observation_id in newly_numeric_ids)
        and any(
            field in str(issue).lower()
            for field in ("lineage", "derived_from", "derivation")
        )
    ]
    assert lineage_issues == []

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
