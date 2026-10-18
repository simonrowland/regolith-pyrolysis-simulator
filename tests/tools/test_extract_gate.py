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


def test_isolated_migration_reports_kinds_and_refusals(tmp_path: Path):
    report = _run_gate(tmp_path, _document())

    assert report.migration_error is None
    assert report.migration_hard_issues == 0
    assert report.value_kinds
    assert "isolated-migration" not in _checks(report)


def test_locator_requires_zero_based_pdf_page_index(tmp_path: Path):
    doc = _document()
    doc["extraction"]["date"] = "2026-10-18"
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    report = _run_gate(tmp_path, doc)

    assert "locator" in _checks(report)
    assert any("pdf_page_index" in finding.detail for finding in report.findings)


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


def test_locator_page_mismatch_is_soft_when_published_page_is_missing(tmp_path: Path):
    doc = _document()
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator["page"] = 3711
    locator["pdf_page_index"] = 2
    report = _run_gate(tmp_path, doc)

    assert "locator" not in _checks(report)
    assert "locator-page-offset" in _checks(report)
    assert any(item.severity == "SOFT" for item in report.findings)

    locator["published_page"] = 3711
    report = _run_gate(tmp_path, doc)
    assert "locator-page-offset" not in _checks(report)


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


def test_supported_composition_bases_match_green_extract_census():
    # Census of Composition maps and composition_mol basis fields in the 347
    # extracts committed at 6edf21cb0, filtered to basis strings migrate.py
    # preserves when reading Composition values.
    assert gate._SUPPORTED_COMPOSITION_BASES == frozenset(
        {
            "Fe pure standard",
            "Fe-Mo binary alloy",
            "Fe-Mo_alloy",
            "Fe-Sn_alloy",
            "Pd pure standard",
            "Saratov chondrule oxide composition as printed",
            "derived_from_formula",
            "elemental",
            "formula-derived composition from printed mullite stoichiometry",
            "formula-unit mole fraction calculated from printed mullite formula",
            "measured 5aN-3 composition used as the starting composition",
            "measured_as_printed",
            "normalized_mole_fraction_from_published_Fo93Fa7",
            "normalized_mole_fraction_from_published_xA_over_xC_and_xS",
            "printed Saratov chondrule oxide composition",
            "printed_formula_stoichiometry",
            "printed_initial_mole_fractions",
            "printed_metals",
            "printed_mole_fraction",
            "printed_oxides",
            "printed_quasi_binary",
            "printed_species",
            "printed_x_Mo_axis",
            "printed_x_e_Al2O3_mol_percent_axis",
            "simple_oxide_mole_fraction",
            "sossi_2020_measured_oxide_mole_fraction",
            "stoichiometric MgAl2O4 on the MgO–Al2O3 oxide-component basis",
            "stoichiometric_Ca3SiO5_oxide_mole_fractions",
            "total iron on a single-cation oxide basis: x(FeO) + x(FeO1.5), mole fraction, as compiled",
        }
    )
    assert "printed_mole_percent" not in gate._SUPPORTED_COMPOSITION_BASES


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

    row["values"]["property_kind"] = "liquidus"
    report = _run_gate(tmp_path, doc)

    assert "transition-composition" not in _checks(report)


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
