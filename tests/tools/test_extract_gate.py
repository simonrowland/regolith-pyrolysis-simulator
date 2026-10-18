from __future__ import annotations

import json
from pathlib import Path

import yaml

from simulator.battery.extract_gate import evaluate_extract
from simulator.yaml_cache import load_cached_safe_yaml, yaml_mapping_source_issues
from tools import validate_literature_extracts as validator
from tools import extract_gate


def _document() -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {"citation": "Fixture et al. (2026), Test Journal 1:1"},
        "extraction": {"method": "unit_test", "date": "2026-08-01", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[fe_alpha_1].values.alpha",
                "value": 0.24,
                "note": "fixture fidelity sample",
                "locator": {"page": 2, "pdf_page_index": 1, "table": "1"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fe_alpha_1",
                        "type": "alpha",
                        "locator": {"table": "1", "page": 2, "pdf_page_index": 1},
                        "T_range_K": [1700.0, 1800.0],
                        "phase": "silicate_melt",
                        "regime": "langmuir_free_evaporation",
                        "units": "dimensionless",
                        "uncertainty": {"note": "fixture ±10% band", "relative": 0.1},
                        "values": {"alpha": 0.24},
                    }
                ]
            }
        },
    }


def _run_gate(
    tmp_path: Path,
    doc: dict,
    *,
    raw: str | None = None,
    corpus: Path | None = None,
):
    path = tmp_path / "fixture-source.yaml"
    raw = raw if raw is not None else yaml.safe_dump(doc, sort_keys=False)
    path.write_text(raw, encoding="utf-8")
    parsed = load_cached_safe_yaml(raw)
    errors = validator.validate_extract_document(
        parsed,
        path=path,
        expected_source_id=path.stem,
        pre_policy_ids=set(),
    )
    errors.extend(validator.check_all_fidelity_samples_match(parsed, label=path.stem))
    return evaluate_extract(
        path,
        parsed,
        corpus=corpus or tmp_path / "corpus",
        validator_errors=errors,
        yaml_issues=yaml_mapping_source_issues(raw),
    )


def _checks(report) -> set[str]:
    return {finding.check for finding in report.findings}


def test_isolated_migration_reports_kinds_and_refusals(tmp_path: Path):
    report = _run_gate(tmp_path, _document())

    assert report.migration_error is None
    assert report.migration_hard_issues == 0
    assert report.value_kinds
    assert "isolated-migration" not in _checks(report)


def test_locator_requires_zero_based_pdf_page_index(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    report = _run_gate(tmp_path, doc)

    assert "locator" in _checks(report)
    assert any("pdf_page_index" in finding.detail for finding in report.findings)


def test_composition_rejects_unsupported_basis_and_percent_scale(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "printed_mole_percent",
        "SiO2": "50",
        "CaO": "50",
    }
    report = _run_gate(tmp_path, doc)
    assert "composition" in _checks(report)
    assert "unsupported composition basis" in next(
        item.detail for item in report.findings if item.check == "composition"
    )

    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "printed_mole_fraction",
        "SiO2": "50",
        "CaO": "50",
    }
    report = _run_gate(tmp_path, doc)
    assert "percent-as-fraction" in next(
        item.detail for item in report.findings if item.check == "composition"
    )


def test_transition_property_under_single_oxide_flags_printed_mixture(tmp_path: Path):
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row["observation_id"] = "feo_transition"
    row["type"] = "transition_point"
    row["experiment_id"] = "mixed-sample"
    row["values"] = {
        "quantity": "transition_temperature",
        "temperature_K": 1800,
    }
    doc["experiments"] = [
        {
            "experiment_id": "mixed-sample",
            "sample": {
                "printed_composition": {
                    "state": {
                        "tag": "value",
                        "value": {
                            "basis": "printed_mole_fraction",
                            "amount_basis": "mole_fraction",
                            "components": [["CaO", "0.7"], ["SiO2", "0.3"]],
                        },
                    }
                }
            },
        }
    ]
    doc["species"] = {"FeO": {"observations": [row]}}
    doc["fidelity_samples"][0]["path"] = (
        "species.FeO.observations[feo_transition].values.temperature_K"
    )
    doc["fidelity_samples"][0]["value"] = 1800
    report = _run_gate(tmp_path, doc)

    assert "transition-composition" in _checks(report)


def test_evidence_rejects_unattributed_quotes_and_scored_models(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"].update(
        {
            "method_class": "quoted_attributed",
            "model_output": {"evidence_class": "model_derived", "value": 0.25},
        }
    )
    report = _run_gate(tmp_path, doc)

    assert "evidence" in _checks(report)
    assert "attribution" in next(
        item.detail for item in report.findings if item.check == "evidence"
    )
    assert "model_derived" in next(
        item.detail for item in report.findings if item.check == "evidence"
    )


def test_zero_uncertainty_and_zero_band_width_are_hard(tmp_path: Path):
    doc = _document()
    values = doc["species"]["Fe"]["observations"][0]["values"]
    values["uncertainty"] = 0
    values["read_uncertainty_temperature_K"] = "0"
    values["read_uncertainty_pressure_Pa"] = "± 0.00"
    values["temperature_band_width_K"] = 0
    report = _run_gate(tmp_path, doc)

    assert "zero-uncertainty" in _checks(report)


def test_table_coverage_compares_mineru_cells_to_extract_values(tmp_path: Path):
    content = [
        {
            "type": "table",
            "table_caption": ["Table Ia. Measurements"],
            "table_body": "<table><tr><th>A</th><th>B</th></tr>"
            "<tr><td>10</td><td>20</td></tr><tr><td>30</td><td>40</td></tr></table>",
        }
    ]
    content_path = (
        tmp_path / "corpus/raw/fixture-source/ocr-mineru/page_content_list.json"
    )
    content_path.parent.mkdir(parents=True)
    content_path.write_text(json.dumps(content), encoding="utf-8")
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row["locator"].pop("table")
    row["values"]["points"] = [
        {
            "T_C": 10,
            "pressure_Pa": 20,
            "locator": {"table": "1a", "page": 2, "pdf_page_index": 1},
        }
    ]
    report = _run_gate(tmp_path, doc, corpus=tmp_path / "corpus")

    assert "capture" in _checks(report)
    assert report.tables[0].numeric_cells == 4
    assert report.tables[0].extract_values == 2
    assert report.tables[0].percent < 80


def test_duplicate_source_identity_is_soft(tmp_path: Path):
    doc = _document()
    doc["source"].update({"doi": "10.1234/fixture", "title": "Fixture Article"})
    (tmp_path / "other.yaml").write_text(
        "schema_version: literature_extract.v1\n"
        "source:\n  doi: 10.1234/fixture\n  title: Other Article\n",
        encoding="utf-8",
    )
    report = _run_gate(tmp_path, doc)

    duplicate = next(item for item in report.findings if item.check == "duplicate")
    assert duplicate.severity == "SOFT"


def test_duplicate_lookup_skips_companion_table_documents(tmp_path: Path):
    doc = _document()
    doc["source"]["doi"] = "10.1234/fixture"
    (tmp_path / "companion.yaml").write_text(
        "schema_version: literature_extract_table.v1\n"
        "source:\n  doi: 10.1234/fixture\n",
        encoding="utf-8",
    )
    report = _run_gate(tmp_path, doc)

    assert "duplicate" not in _checks(report)


def test_duplicate_lookup_uses_title_and_author_year(tmp_path: Path):
    doc = _document()
    doc["source"].update(
        {"title": "Distinct Title", "authors": ["A. Writer"], "year": 2026}
    )
    other = tmp_path / "other.yaml"
    other.write_text(
        "schema_version: literature_extract.v1\n"
        "source:\n  title: Distinct Title\n  doi: 10.5678/other\n",
        encoding="utf-8",
    )
    report = _run_gate(tmp_path, doc)
    assert "title" in next(
        item.detail for item in report.findings if item.check == "duplicate"
    )

    other.write_text(
        "schema_version: literature_extract.v1\n"
        "source:\n  title: Another Title\n  authors: ['A. Writer']\n  year: 2026\n",
        encoding="utf-8",
    )
    report = _run_gate(tmp_path, doc)
    assert "author_year" in next(
        item.detail for item in report.findings if item.check == "duplicate"
    )


def test_deferred_figure_with_numeric_points_is_soft(tmp_path: Path):
    doc = _document()
    doc["figure_policy"] = "Numeric figures are deferred."
    doc["figures"] = [{"figure": "1", "points": [[1, 2], [3, 4]]}]
    report = _run_gate(tmp_path, doc)

    deferred = next(item for item in report.findings if item.check == "deferred-figure")
    assert deferred.severity == "SOFT"

    doc["figures"] = [
        {"figure": "2", "points": [{"locator": {"page": 2, "pdf_page_index": 1}}]}
    ]
    report = _run_gate(tmp_path, doc)
    assert "deferred-figure" not in _checks(report)


def test_schema_fidelity_and_flow_guard_use_shared_validators(tmp_path: Path):
    doc = _document()
    doc["fidelity_samples"][0]["value"] = 9
    raw = yaml.safe_dump(doc, sort_keys=False) + "broken: {CaO, SiO2: 0.5}\n"
    report = _run_gate(tmp_path, doc, raw=raw)

    assert "schema/fidelity" in _checks(report)
    assert "yaml-source" in _checks(report)


def test_cli_audits_one_extract_by_source_id(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    extracts = repo / "data/literature/extracts"
    extracts.mkdir(parents=True)
    path = extracts / "fixture-source.yaml"
    path.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(extract_gate, "REPO_ROOT", repo)

    status = extract_gate.main(["--ids", "fixture-source", "--corpus", str(tmp_path)])

    output = capsys.readouterr().out
    assert status == 0
    assert "MIGRATION observations by ValueKind:" in output


def test_cli_audits_changed_extracts(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    extracts = repo / "data/literature/extracts"
    extracts.mkdir(parents=True)
    (extracts / "fixture-source.yaml").write_text(
        yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8"
    )
    (extracts / "_policy.yaml").write_text("ignored: true\n", encoding="utf-8")
    monkeypatch.setattr(extract_gate, "REPO_ROOT", repo)

    class Result:
        returncode = 0
        stderr = ""
        stdout = "data/literature/extracts/fixture-source.yaml\n"

    monkeypatch.setattr(
        extract_gate.subprocess, "run", lambda *args, **kwargs: Result()
    )
    status = extract_gate.main(["--changed-since", "BASE", "--corpus", str(tmp_path)])

    output = capsys.readouterr().out
    assert status == 0
    assert "EXTRACT" in output and "fixture-source.yaml" in output
