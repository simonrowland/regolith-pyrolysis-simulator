"""Schema and migration pins for Hino et al. (1997)."""

from __future__ import annotations

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/hino-1997-secondary-slag-activities.yaml"


def test_figure_led_extract_migrates_cleanly_and_preserves_printed_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.observations == {}
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    figure_records = [
        record
        for body in document["species"].values()
        for record in body["context"]
        if record["type"] == "figure_record"
    ]
    assert len(figure_records) == 13
    assert {record["locator"]["figure"] for record in figure_records} == set("123456789")
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        for record in figure_records
    )
    assert all("axes_as_printed" in record["values"] for record in figure_records)

    alumina = document["species"]["Al2O3"]["context"]
    reported = next(
        record for record in alumina
        if record["observation_id"] == "hino_1997_text_reported_al2o3_activities_context"
    )
    assert [row["activity"] for row in reported["values"]["reported_values"]] == [
        "1×10^-3", "1×10^-3", "1×10^-6"
    ]
    assert "t-1382" in reported["values"]["schema_gap"]
    reactions = next(
        record for record in alumina
        if record["observation_id"] == "hino_1997_reaction_gibbs_inputs_context"
    )
    assert "1,323,700 + 573.65 T" in reactions["values"]["reactions"][0]["delta_G0_as_printed"]

    sulphur = document["species"]["S"]["context"]
    estimates = next(
        record for record in sulphur
        if record["observation_id"] == "hino_1997_text_sulphur_distribution_estimates_context"
    )
    assert estimates["values"]["reported_estimates"][0]["L_S_as_printed"] == "100–1000"
    assert estimates["values"]["reported_estimates"][1]["L_S_as_printed"] == "more than 10000"
