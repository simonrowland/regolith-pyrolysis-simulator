"""Regression pins for Stolyarova et al. (1996) oxygen evidence and apparatus."""

from pathlib import Path
from copy import deepcopy

import yaml
import pytest

from simulator.battery.enums import EvidenceClass, RefusalReason
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
        "quoted_attributed",
        "quoted_attributed",
        "quoted_attributed",
    ]
    assert all(
        observation["values"]["attribution"]
        == "Stolyarova, Shornikov and Shultz (1996), Table 2, footnote a and Eqs. (7)–(9)."
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
        tmp_path / "before-lineage-repair", EXTRACT_NAME,
        extract_doc=baseline_document,
    )
    migrated = _migrate_real_extract(
        tmp_path / "after-lineage-repair", EXTRACT_NAME,
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
    _assert_no_hard_issues(migrated)

    missing_bench_document = deepcopy(focused_document)
    missing_bench_document["experiments"][0]["bench_id"] = "missing-bench"
    missing_bench = _migrate_real_extract(
        tmp_path / "missing-bench-mutation", EXTRACT_NAME,
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
