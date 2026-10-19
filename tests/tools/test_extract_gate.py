from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

import simulator.battery.extract_gate as gate
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


def test_isolated_migration_reports_kinds_and_refusals(tmp_path: Path, monkeypatch):
    report = _run_gate(tmp_path, _document())

    assert report.migration_error is None
    assert report.migration_hard_issues == 0
    assert report.value_kinds
    assert "isolated-migration" not in _checks(report)
    monkeypatch.setattr(gate, "_migrate_one", lambda *_args: ["forced refusal"])
    report = _run_gate(tmp_path, _document())
    assert next(item for item in report.findings if item.check == "isolated-migration").severity == "HARD"


def test_locator_requires_zero_based_pdf_page_index(tmp_path: Path):
    doc = _document()
    doc["extraction"]["date"] = "2026-10-18"
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    report = _run_gate(tmp_path, doc)

    assert "locator" in _checks(report)
    assert next(item for item in report.findings if item.check == "locator").severity == "HARD"
    assert any("pdf_page_index" in finding.detail for finding in report.findings)


def test_datetime_extraction_date_does_not_raise(tmp_path: Path):
    from datetime import datetime

    doc = _document()
    doc["extraction"]["date"] = datetime(2026, 10, 18)
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    report = _run_gate(tmp_path, doc)
    assert "locator" in _checks(report)


def test_missing_legacy_locator_index_is_soft(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    report = _run_gate(tmp_path, doc)

    assert "locator" not in _checks(report)
    assert "locator-legacy" in _checks(report)
    assert next(
        finding
        for finding in report.findings
        if finding.check == "locator-legacy"
    ).severity == "SOFT"


def test_locator_page_index_is_checked_against_corpus_pdf(tmp_path: Path, monkeypatch):
    corpus = tmp_path / "corpus"
    pdf = corpus / "raw" / "fixture-source" / "fixture-source.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"fixture PDF")
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Pages:          3\n", stderr=""
        ),
    )

    doc = _document()
    report = _run_gate(tmp_path, doc, corpus=corpus)
    assert "locator" not in _checks(report)

    doc["species"]["Fe"]["observations"][0]["locator"]["pdf_page_index"] = 3
    report = _run_gate(tmp_path, doc, corpus=corpus)
    assert "locator" in _checks(report)
    assert "outside the PDF's 0..2 page range" in next(
        item.detail for item in report.findings if item.check == "locator"
    )


def test_locator_page_mismatch_is_hard_even_with_published_page(tmp_path: Path):
    doc = _document()
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator["page"] = 3711
    locator["pdf_page_index"] = 2
    report = _run_gate(tmp_path, doc)

    assert "locator" not in _checks(report)
    assert "locator-page-offset" in _checks(report)
    assert next(item for item in report.findings if item.check == "locator-page-offset").severity == "HARD"

    locator["published_page"] = 3711
    report = _run_gate(tmp_path, doc)
    assert "locator-page-offset" in _checks(report)
    assert next(item for item in report.findings if item.check == "locator-page-offset").severity == "HARD"


def test_typed_missing_pdf_reason_skips_page_rule_with_counted_notice(tmp_path: Path):
    doc = _document()
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator.update(page=3711, pdf_page_index=2, page_verification="unverified_pdf_absent")
    report = _run_gate(tmp_path, doc)

    assert "locator-page-offset" not in _checks(report)
    notice = next(item for item in report.findings if item.check == "locator-verification")
    assert notice.severity == "SOFT"
    assert "1 issue(s)" in notice.detail
    locator["page_verification"] = "unverified"
    report = _run_gate(tmp_path, doc)
    assert next(item for item in report.findings if item.check == "locator").severity == "HARD"


def test_typed_missing_pdf_reason_does_not_skip_when_corpus_pdf_exists(
    tmp_path: Path, monkeypatch
):
    corpus = tmp_path / "corpus"
    pdf = corpus / "raw" / "fixture-original" / "fixture-original.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"fixture")
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Pages:          3\n", stderr=""
        ),
    )
    doc = _document()
    doc["source"]["corpus_source_id"] = "fixture-original"
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator.update(page=2, pdf_page_index=1, page_verification="unverified_pdf_absent")

    report = _run_gate(tmp_path, doc, corpus=corpus)

    finding = next(
        item for item in report.findings if item.check == "locator-page-offset"
    )
    assert finding.severity == "HARD"
    assert "page_verification is invalid" in finding.detail


def test_locator_index_without_page_is_not_verified(tmp_path: Path):
    doc = _document()
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator.pop("page")
    report = _run_gate(tmp_path, doc)

    finding = next(
        item for item in report.findings if item.check == "locator-verification"
    )
    assert finding.severity == "SOFT"
    assert ".page is missing or invalid; physical page is unverified" in finding.detail


def test_page_verification_migrates_as_locator_field_not_note():
    from simulator.battery.migrate import locator_from_mapping

    locator = locator_from_mapping(
        {
            "page": 2,
            "pdf_page_index": 1,
            "page_verification": "unverified_pdf_absent",
            "table": "I",
        }
    )

    assert locator is not None
    assert locator.page_verification == "unverified_pdf_absent"
    assert locator.note is None
    assert not validator._locator_ok(
        {"page_verification": "unverified_pdf_absent"}
    )


def test_table_continued_migrates_as_locator_field_not_note():
    from simulator.battery.migrate import locator_from_mapping

    locator = locator_from_mapping(
        {
            "page": 4,
            "pdf_page_index": 3,
            "table": "877",
            "table_continued": True,
            "record": "3300.000 64.806",
        }
    )

    assert locator is not None
    assert locator.table_continued is True
    assert locator.note is None


def test_absent_corpus_pdf_only_warns_about_page_count(tmp_path: Path):
    report = _run_gate(tmp_path, _document())
    assert "locator" not in _checks(report)
    assert "locator-pdf" in _checks(report)
    assert (
        next(
            item for item in report.findings if item.check == "locator-pdf"
        ).severity
        == "SOFT"
    )


def test_composition_rejects_unsupported_basis_and_percent_scale(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "printed_mole_percent",
        "SiO2": "50",
        "CaO": "50",
    }
    report = _run_gate(tmp_path, doc)
    assert "composition" in _checks(report)
    assert next(item for item in report.findings if item.check == "composition").severity == "HARD"
    detail = next(
        item.detail for item in report.findings if item.check == "composition"
    )
    assert "unsupported composition basis" in detail
    assert "percent-as-fraction" in detail

    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "printed_mole_fraction",
        "SiO2": "50",
        "CaO": "50",
    }
    report = _run_gate(tmp_path, doc)
    assert next(item for item in report.findings if item.check == "composition").severity == "HARD"
    assert "percent-as-fraction" in next(
        item.detail for item in report.findings if item.check == "composition"
    )


def test_explicit_inventory_basis_allows_descriptive_composition_label():
    doc = {
        "experiments": [
            {
                "sample": {
                    "composition": {
                        "basis": "nominal formula-unit ratio as printed",
                        "amount_basis": "mol_inventory",
                        "components": [["Li2O", "1"], ["SiO2", "2"]],
                    }
                }
            }
        ]
    }
    assert gate._composition_basis_issues(doc) == []


def test_fraction_sum_uses_numeric_siblings_and_rounding_band():
    doc = {
        "composition_mole_fraction": {
            "basis": "printed_mole_fraction",
            "SiO2": "60",
            "Al2O3": "38.6",
            "note": "oxides as printed",
        }
    }
    issues = gate._composition_basis_issues(doc)
    assert any("sum to 98.6" in issue for issue in issues)

    doc["composition_mole_fraction"].update(SiO2="50", Al2O3="50")
    assert any("sum to 100" in issue for issue in gate._composition_basis_issues(doc))

    doc["composition_mole_fraction"].update(SiO2="0.6", Al2O3="0.4")
    assert not any("percent-as-fraction" in issue for issue in gate._composition_basis_issues(doc))


def test_transition_composition_flags_nonliquidus_but_allows_liquidus(
    tmp_path: Path,
):
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row["observation_id"] = "feo_transition"
    row["type"] = "transition_point"
    row["experiment_id"] = "mixed-sample"
    row["values"] = {
        "quantity": "transition_temperature",
        "property_kind": "eutectic",
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
    assert next(item for item in report.findings if item.check == "transition-composition").severity == "HARD"

    doc["experiments"][0]["sample"]["printed_composition"]["state"]["value"]["components"][0][1] = "0"
    assert "transition-composition" not in _checks(_run_gate(tmp_path, doc))
    doc["experiments"][0]["sample"]["printed_composition"]["state"]["value"]["components"][0][1] = "0.7"

    row["values"]["property_kind"] = "liquidus"
    report = _run_gate(tmp_path, doc)

    assert "transition-composition" not in _checks(report)


def test_zero_components_in_shornikov_rows_do_not_make_transition_mixtures():
    path = Path(__file__).resolve().parents[2] / "data/literature/extracts/shornikov-2000-aluminosilicate-vaporization.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    issues = gate._transition_composition_issues(doc)
    assert not any(f"shornikov_2000_table1_row_{row:02d}" in issue for issue in issues for row in range(1, 6))


def test_evidence_classification_is_owned_by_migration_and_validation(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"].update(
        {
            "method_class": "quoted_attributed",
            "model_output": {"evidence_class": "model_derived", "value": 0.25},
        }
    )
    report = _run_gate(tmp_path, doc)

    assert "evidence" not in _checks(report)
    assert report.migration_hard_issues > 0

    evidence, queue_reason = gate.migrate.evidence_for(
        "model_derived", model="FactSage 8.2"
    )
    assert evidence.class_.value.value == "model_derived"
    assert queue_reason is None


def test_zero_uncertainty_and_zero_band_width_are_hard(tmp_path: Path):
    doc = _document()
    values = doc["species"]["Fe"]["observations"][0]["values"]
    values["uncertainty"] = 0
    values["read_uncertainty_temperature_K"] = "0"
    values["read_uncertainty_pressure_Pa"] = "± 0.00"
    values["temperature_band_width_K"] = 0
    report = _run_gate(tmp_path, doc)

    assert "zero-uncertainty" in _checks(report)
    assert next(item for item in report.findings if item.check == "zero-uncertainty").severity == "HARD"


def test_asymmetric_uncertainty_zero_arm_is_not_zero_uncertainty(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"]["uncertainty"] = {
        "delta_Hstar_sigma_plus_kJ_mol": 357,
        "delta_Hstar_sigma_minus_kJ_mol": 0,
        "delta_Sstar_sigma_plus_J_mol_K": 230,
        "delta_Sstar_sigma_minus_J_mol_K": 0,
    }
    assert "zero-uncertainty" not in _checks(_run_gate(tmp_path, doc))
    doc["species"]["Fe"]["observations"][0]["values"]["uncertainty"] = {
        "sigma_plus": 0,
        "sigma_minus": 0,
    }
    report = _run_gate(tmp_path, doc)
    assert "zero-uncertainty" in _checks(report)
    assert next(item for item in report.findings if item.check == "zero-uncertainty").severity == "HARD"


def test_heck_asymmetric_uncertainty_rows_keep_zero_lower_arm():
    path = Path(__file__).resolve().parents[2] / "data/literature/extracts/kems-140-heck-2025.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    issues = gate._uncertainty_issues(doc)
    assert not any("sigma_minus" in issue and "delta_Hstar" in issue for issue in issues)
    assert not any("sigma_minus" in issue and "delta_Sstar" in issue for issue in issues)


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
    assert next(item for item in report.findings if item.check == "schema/fidelity").severity == "HARD"
    assert next(item for item in report.findings if item.check == "yaml-source").severity == "HARD"


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

    doc = _document()
    doc["extraction"]["date"] = "2026-10-18"
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    status = extract_gate.main(["--ids", "fixture-source", "--corpus", str(tmp_path)])
    assert status == 1


def test_cli_requires_corpus_or_environment_and_explains_option(capsys):
    import pytest

    with pytest.raises(SystemExit) as error:
        extract_gate.main([])
    assert error.value.code == 2
    assert "--corpus or REGOLITH_CORPUS is required" in capsys.readouterr().err


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
