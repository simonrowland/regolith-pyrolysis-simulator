"""Regression pins for Stolyarova et al. (1996) oxygen evidence and apparatus."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, MetricOperation, RefusalReason, ResidualStatus
from simulator.battery.score import (
    _e15_residual_point,
    _reference_has_measured_evidence,
)
from tests.battery.test_migrate import _migrate_real_extract, to_plain


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "stolyarova-1996-cao-alumina-silica-kems.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME

def _assert_no_hard_issues(migrated):
    assert migrated.validation is not None
    assert not migrated.validation.hard_issues


def test_table2_oxygen_routes_keep_calculation_and_apparatus_evidence(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        observation
        for observation in document["species"]["O"]["observations"]
        if observation["observation_id"].startswith("stolyarova_1996_table2_")
    ]
    assert len(observations) == 3
    # Published p. 19 calculates p(O), p'(O) and p''(O) from this study's ion
    # currents, Gurvich et al. (66) equilibrium constants, and Mann (67)
    # cross-sections, Eqs. (7)–(9). Table 2 footnote a (p. 20) says those
    # columns were calculated. The row inputs are not printed.
    method = document["extraction"]["method"]
    assert "quoted" not in method.casefold()
    assert "Eqs. (7)–(9)" in method
    assert "footnote a" in method
    assert "Gurvich" in method
    assert "Mann" in method
    assert "not printed" in method
    assert [observation["values"]["method_class"] for observation in observations] == [
        "measured_reduced",
        "measured_reduced",
        "measured_reduced",
    ]
    assert [observation["values"]["evidence_class"] for observation in observations] == [
        "measured_reduced",
        "measured_reduced",
        "measured_reduced",
    ]
    assert all("attribution" not in observation["values"] for observation in observations)
    assert all(
        "row ion currents and numerical constants are not printed"
        in observation["values"]["method_as_printed"]
        for observation in observations
    )
    assert [observation["reaction_as_printed"] for observation in observations] == [
        "(MoO3) = (MoO2) + (O)",
        "(AlO) = (Al) + (O)",
        "(SiO2) = (SiO) + (O)",
    ]
    assert [observation["values"]["points"][0][key] for observation, key in zip(
        observations,
        (
            "P_O_times_10^8_atm_as_printed",
            "P_O_prime_times_10^8_atm_as_printed",
            "P_O_double_prime_times_10^8_atm_as_printed",
        ),
    )] == ["0.36", "0.40", "0.16"]
    assert all(
        observation["standard_state"]["tag"] == "unknown"
        for observation in observations
    )
    for observation in observations:
        derivation = observation["values"]["derivation"]
        assert derivation["method"]["state"]["value"].startswith(
            "K_ref = Gurvich K°(T)"
        )
        assert derivation["method"]["locator"]["equation"] == "7–9"
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["table"] == "2"
        assert "not printed" in derivation["raw_inputs"]["locator"]["note"]

    bench = document["benches"][0]
    assert bench["cell_material_and_liner"]["state"]["tag"] == "unknown"
    assert bench["cell_materials"][0]["state"]["value"] == "Mo"
    assert bench["cell_materials"][0]["inferred"] is True
    assert "Inferred from the identified Mo+" in bench["cell_materials"][0]["inference"]
    calibration = document["experiments"][0]["apparatus"]["calibration"]
    assert calibration["method"]["state"]["value"] == (
        "ion-current comparison using silver vapor pressure as standard"
    )
    assert calibration["reference_substance"]["state"]["value"] == "silver vapor"

    focused_document = deepcopy(document)
    focused_document["species"] = {"O": focused_document["species"]["O"]}
    baseline_document = deepcopy(focused_document)
    for observation in baseline_document["species"]["O"]["observations"]:
        if observation["observation_id"].startswith("stolyarova_1996_table2_pO"):
            observation["values"]["derivation"].pop("raw_inputs")
    baseline = _migrate_real_extract(
        tmp_path / "before-lineage-repair",
        EXTRACT_NAME,
        use_repository_index_row=True,
        extract_doc=baseline_document,
    )
    migrated = _migrate_real_extract(
        tmp_path / "after-lineage-repair",
        EXTRACT_NAME,
        use_repository_index_row=True,
        extract_doc=focused_document,
    )
    assert baseline.validation is not None and migrated.validation is not None
    baseline_oxygen = {
        observation_id: observation
        for observation_id, observation in baseline.observations.items()
        if "stolyarova_1996_table2_pO" in observation_id
    }
    oxygen_rows = {
        observation_id: observation
        for observation_id, observation in migrated.observations.items()
        if "stolyarova_1996_table2_pO" in observation_id
    }
    assert len(baseline_oxygen) == len(oxygen_rows) == 55
    lineage_detail = "derived observation requires derived_from"
    before_issues = baseline.validation.hard_issues
    lineage_issues = [issue for issue in before_issues if issue.detail == lineage_detail]
    derivation_issues = [
        issue
        for issue in before_issues
        if issue.detail.startswith("invalid derivation annotation:")
    ]
    assert len(before_issues) == 58
    assert len(lineage_issues) == 55
    assert all(
        issue.reason is RefusalReason.CONDITIONAL_FIELD
        and issue.detail == lineage_detail
        and issue.path.startswith("observation[")
        and issue.path.endswith("].derived_from")
        for issue in lineage_issues
    )
    assert {
        issue.path.removesuffix(".derived_from")[12:-1]
        for issue in lineage_issues
    } == set(baseline_oxygen)
    expected_derivation_paths = {
        f"observations.stolyarova-1996-cao-alumina-silica-kems::{observation_id}.values.derivation"
        for observation_id in (
            "stolyarova_1996_table2_pO_1933k",
            "stolyarova_1996_table2_pO_prime_1933k",
            "stolyarova_1996_table2_pO_double_prime_1933k",
        )
    }
    assert len(derivation_issues) == 3
    assert {issue.path for issue in derivation_issues} == expected_derivation_paths
    assert all(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.detail
        == (
            "invalid derivation annotation: Derivation.inputs is required unless "
            "raw_inputs is located not_printed"
        )
        for issue in derivation_issues
    )
    assert len(lineage_issues) + len(derivation_issues) == len(before_issues)
    baseline_reduced = {
        observation_id: observation
        for observation_id, observation in baseline.observations.items()
        if observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    }
    assert len(baseline_reduced) == 55
    assert set(oxygen_rows) == set(baseline_oxygen)
    assert {
        observation_id: to_plain(observation.value)
        for observation_id, observation in oxygen_rows.items()
    } == {
        observation_id: to_plain(observation.value)
        for observation_id, observation in baseline_oxygen.items()
    }
    changed_ids = {
        "stolyarova_1996_table2_pO_1933k",
        "stolyarova_1996_table2_pO_prime_1933k",
        "stolyarova_1996_table2_pO_double_prime_1933k",
    }
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value
        is (
            EvidenceClass.MEASURED_REDUCED
            if observation.observation_id.split("::")[1] in changed_ids
            else EvidenceClass.QUOTED_ATTRIBUTED
        )
        for observation in oxygen_rows.values()
    )
    assert all(observation.derived_from is None for observation in oxygen_rows.values())
    for observation in oxygen_rows.values():
        assert observation.derivation is not None
        observation_id = observation.observation_id.split("::")[1]
        if observation_id in changed_ids:
            assert observation.derivation.relation.startswith("p")
            assert observation.derivation.raw_inputs is not None
            assert observation.derivation.raw_inputs.state.value.value == "not_printed"
            assert observation.derivation.method is not None
            assert observation.derivation.method.locator.equation in {"7–9"}
        else:
            assert observation.derivation.relation == "atm_to_Pa"
            assert observation.derivation.inputs == (
                "pdf:stolyarova-1996-cao-alumina-silica-kems",
            )
            assert observation.derivation.raw_inputs is None
            assert observation.derivation.method is None
    for observation in oxygen_rows.values():
        assert observation.locator is not None
        assert observation.locator.pdf_page_index is not None
        assert observation.locator.page == observation.locator.pdf_page_index + 1
        assert (observation.locator.page, observation.locator.published_page) == (
            6,
            20,
        )
        temperature = observation.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert temperature.value == Decimal("1933")
        composition = (observation.point_conditions or {}).get("composition")
        assert composition is not None and composition.state.is_value
        assert composition.locator is not None
    oxygen_paths = tuple(
        f"observation[{observation.observation_id}]"
        for observation in oxygen_rows.values()
    )
    assert not [
        issue
        for issue in migrated.validation.hard_issues
        if issue.path.startswith(oxygen_paths)
    ]

    e15_probe = {
        "status": ResidualStatus.MATCH.value,
        "numeric": {"value": "1", "operation": MetricOperation.DEX.value},
    }
    for observation in oxygen_rows.values():
        observation_id = observation.observation_id.split("::")[1]
        if observation_id in changed_ids:
            assert _reference_has_measured_evidence(observation)
        else:
            assert not _reference_has_measured_evidence(observation)
            assert _e15_residual_point(e15_probe, observation) is None
    assert not migrated.validation.hard_issues
    expected_reactants = {
        "stolyarova_1996_table2_pO_1933k": {"MoO3", "MoO2", "O"},
        "stolyarova_1996_table2_pO_prime_1933k": {"AlO", "Al", "O"},
        "stolyarova_1996_table2_pO_double_prime_1933k": {"SiO2", "SiO", "O"},
    }
    for observation in oxygen_rows.values():
        assert observation.evidence.class_.is_value
        observation_id = observation.observation_id.split("::")[1]
        assert observation.evidence.class_.value is (
            EvidenceClass.MEASURED_REDUCED
            if observation_id in changed_ids
            else EvidenceClass.QUOTED_ATTRIBUTED
        )
        assert observation.identity.reaction is not None
        assert observation.identity.reaction.is_value
        assert {
            term.species.formula
            for term in observation.identity.reaction.value.terms
        } == expected_reactants[observation.observation_id.split("::")[1]]

    missing_bench_document = deepcopy(focused_document)
    missing_bench_document["experiments"][0]["bench_id"] = "missing-bench"
    missing_bench = _migrate_real_extract(
        tmp_path / "missing-bench-mutation",
        EXTRACT_NAME,
        use_repository_index_row=True,
        extract_doc=missing_bench_document,
    )
    assert missing_bench.validation is not None
    assert any(
        "missing-bench" in issue.detail
        for issue in missing_bench.validation.hard_issues
    )
    with pytest.raises(AssertionError):
        _assert_no_hard_issues(missing_bench)


def test_table3_reductions_declare_unprinted_inputs_without_changing_values(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    reduced = [
        observation
        for entries in document["species"].values()
        for observation in entries["observations"]
        if observation["observation_id"].startswith("stolyarova_1996_table3_")
        and observation["values"].get("method_class") == "measured_reduced"
    ]
    assert len(reduced) == 5
    assert all(
        observation["values"]["derivation"]["raw_inputs"]["state"]
        == {"tag": "value", "value": "not_printed"}
        and observation["values"]["derivation"]["raw_inputs"]["locator"]["equation"]
        == "10–15"
        and observation["values"]["derivation"]["locator"]["published_page"] == 19
        and observation["values"]["derivation"]["locator"]["page"]
        == observation["values"]["derivation"]["locator"]["pdf_page_index"] + 1
        and observation["values"]["derivation"]["raw_inputs"]["locator"]["page"]
        == observation["values"]["derivation"]["raw_inputs"]["locator"]["pdf_page_index"] + 1
        for observation in reduced
    )

    baseline_document = deepcopy(document)
    for entries in baseline_document["species"].values():
        for observation in entries["observations"]:
            if (
                observation["observation_id"].startswith("stolyarova_1996_table3_")
                and observation["values"].get("method_class") == "measured_reduced"
            ):
                observation["values"].pop("derivation")
    baseline = _migrate_real_extract(
        tmp_path / "before-reduction-lineage", EXTRACT_NAME,
        extract_doc=baseline_document,
    )
    migrated = _migrate_real_extract(
        tmp_path / "after-reduction-lineage", EXTRACT_NAME,
        extract_doc=document,
    )
    assert baseline.validation is not None and migrated.validation is not None
    lineage_details = {
        "derived observation requires derived_from",
        "derived observation requires derivation",
    }
    before = [
        issue for issue in baseline.validation.hard_issues
        if issue.detail in lineage_details
        or issue.detail.startswith("printed derivation inputs do not resolve")
    ]
    after = [
        issue for issue in migrated.validation.hard_issues
        if issue.detail in lineage_details
        or issue.detail.startswith("printed derivation inputs do not resolve")
    ]
    baseline_reduced = {
        observation_id: observation
        for observation_id, observation in baseline.observations.items()
        if "stolyarova_1996_table3_" in observation_id
        and observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    }
    assert len(before) == len(baseline_reduced) == 137
    assert all(
        issue.reason is RefusalReason.CONDITIONAL_FIELD
        and issue.detail == "derived observation requires derived_from"
        and issue.path.startswith("observation[")
        and issue.path.endswith("].derived_from")
        for issue in before
    )
    assert {
        issue.path.removesuffix(".derived_from")[12:-1]
        for issue in before
    } == set(baseline_reduced)
    _assert_no_hard_issues(migrated)
    assert set(baseline.observations) == set(migrated.observations)
    assert {
        observation_id: to_plain(observation.value)
        for observation_id, observation in baseline.observations.items()
    } == {
        observation_id: to_plain(observation.value)
        for observation_id, observation in migrated.observations.items()
    }
