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
_OXYGEN_ATTRIBUTIONS = (
    "Stolyarova et al. (1996), Table 2 footnote a and Eq. (7), pp. 19–20",
    "Stolyarova et al. (1996), Table 2 footnote a and Eq. (8), pp. 19–20",
    "Stolyarova et al. (1996), Table 2 footnote a and Eq. (9), pp. 19–20",
)


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
        "quoted_attributed",
        "quoted_attributed",
        "quoted_attributed",
    ]
    assert all(
        "row ion currents and numerical constants are not printed"
        in observation["values"]["method_as_printed"]
        for observation in observations
    )
    # One attribution key per series. Each cites footnote a and that series'
    # equation. A second shared Eqs. (7)–(9) key would duplicate the mapping.
    assert tuple(
        observation["values"]["attribution"] for observation in observations
    ) == _OXYGEN_ATTRIBUTIONS
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
        assert derivation["inputs"] == [
            "pdf:stolyarova-1996-cao-alumina-silica-kems"
        ]
        assert "raw_inputs" not in derivation

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
        if observation["values"].get("evidence_class") == "quoted_attributed":
            observation["values"]["method_class"] = "model_derived"
            observation["values"]["evidence_class"] = "model_derived"
            observation["values"].pop("attribution")
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
    assert len(before_issues) == 55
    assert all(
        issue.reason is RefusalReason.CONDITIONAL_FIELD
        and issue.detail == lineage_detail
        and issue.path.startswith("observation[")
        and issue.path.endswith("].derived_from")
        for issue in before_issues
    )
    assert {
        issue.path.removesuffix(".derived_from")[12:-1]
        for issue in before_issues
    } == set(baseline_oxygen)
    assert set(oxygen_rows) == set(baseline_oxygen)
    assert {
        observation_id: to_plain(observation.value)
        for observation_id, observation in oxygen_rows.items()
    } == {
        observation_id: to_plain(observation.value)
        for observation_id, observation in baseline_oxygen.items()
    }
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        for observation in oxygen_rows.values()
    )
    assert all(observation.derived_from is None for observation in oxygen_rows.values())
    assert all(
        observation.derivation is not None
        and observation.derivation.relation == "atm_to_Pa"
        and observation.derivation.inputs
        == ("pdf:stolyarova-1996-cao-alumina-silica-kems",)
        and observation.derivation.raw_inputs is None
        and observation.derivation.method is None
        for observation in oxygen_rows.values()
    )
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
    assert all(
        not _reference_has_measured_evidence(observation)
        and _e15_residual_point(e15_probe, observation) is None
        for observation in oxygen_rows.values()
    )
    assert not migrated.validation.hard_issues
    expected_reactants = {
        "stolyarova_1996_table2_pO_1933k": {"MoO3", "MoO2", "O"},
        "stolyarova_1996_table2_pO_prime_1933k": {"AlO", "Al", "O"},
        "stolyarova_1996_table2_pO_double_prime_1933k": {"SiO2", "SiO", "O"},
    }
    for observation in oxygen_rows.values():
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
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
        if observation["values"].get("method_class") == "measured_reduced"
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
            if observation["values"].get("method_class") == "measured_reduced":
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
        if observation.evidence.class_.is_value
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
