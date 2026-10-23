from __future__ import annotations

import json
import shutil
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml

import simulator.battery.extract_gate as gate
from simulator.battery.extract_gate import evaluate_extract
from simulator.yaml_cache import load_cached_safe_yaml, yaml_mapping_source_issues
from tools import validate_literature_extracts as validator
from tools import extract_gate
import simulator.yaml_cache as yaml_cache


REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_scored_species_misfiling_and_three_non_scored_shapes(tmp_path: Path):
    doc = _document()
    doc["species"] = {
        "W": {
            "observations": [
                {
                    "observation_id": "fixture_activity_a_Fe",
                    "type": "activity",
                    "locator": {"table": "1", "page": 3, "pdf_page_index": 2},
                    "phase": "l",
                    "standard_state": "pure liquid Fe",
                    "values": {
                        "quantity": "activity",
                        "method_class": "measured_direct",
                        "activity": "0.865",
                        "a_Fe": "0.865",
                    },
                }
            ]
        },
        "SiO2": {
            "observations": [
                {
                    "observation_id": "chen22_C1_liquid_tio2",
                    "type": "equilibrium_phase_composition",
                    "locator": {
                        "table": "II",
                        "row": "C1, Liquid",
                        "column": "TiO2 equilibrium wt.%",
                        "page": 3,
                        "pdf_page_index": 2,
                    },
                    "phase": "l",
                    "units": "wt.%",
                    "values": {
                        "quantity": "equilibrium_phase_composition",
                        "component_as_published": "TiO2",
                        "value": "45.9",
                            # A printed basis keeps this row on the equilibrium-ratio
                            # reader. The phase-row rule already on this branch
                            # refuses an equilibrium composition that names neither
                            # a closed phase role nor a ratio.
                            "basis_as_published": "TiO2 equilibrium wt.%",
                    },
                }
            ]
        },
        "FeSO4_cr": {
            "observations": [
                {
                    "observation_id": "cea_FeSO4(cr)_gibbs",
                    "type": "gibbs_table",
                    "locator": {"table": "1", "page": 3, "pdf_page_index": 2},
                    "phase": "cr",
                    "units": "kJ/mol",
                    "values": {
                        "quantity": "delta_fG",
                        "formula": "FeSO4",
                        "value": "-100.0",
                    },
                }
            ]
        },
        "Al2O3": {
            "observations": [
                {
                    "observation_id": "shornikov_2008_mgo_aluminosilicate_melts_al2o3",
                    "type": "gibbs_table",
                    "locator": {"table": "1", "page": 3, "pdf_page_index": 2},
                    "phase": "cr",
                    "units": "kJ/mol",
                    "values": {"quantity": "delta_fG", "value": "-1017.742"},
                }
            ]
        },
    }

    report = _run_gate(tmp_path, doc)
    finding = next(
        item for item in report.findings if item.check == "isolated-migration"
    )
    assert finding.severity == "HARD"
    assert "migrated as W, while row names Fe" in finding.detail

    path = tmp_path / "fixture-source.yaml"
    migrator = gate.migrate.Migrator(
        root=gate.migrate.REPO_ROOT, index={}, aliases={}
    )
    migrator._migrate_extract(path)
    migrator.finalize()
    observations = migrator.result.observations
    assert (
        observations["fixture-source::fixture_activity_a_Fe"].identity.species.formula
        == "W"
    )
    assert (
        observations["fixture-source::chen22_C1_liquid_tio2"].identity.species.formula
        == "SiO2"
    )
    assert (
        observations["fixture-source::cea_FeSO4(cr)_gibbs"].identity.species.formula
        == "FeSO4"
    )
    assert observations[
        "fixture-source::shornikov_2008_mgo_aluminosilicate_melts_al2o3"
    ].identity.species.formula == "Al2O3"
    issues = gate._species_container_issues(doc, observations)
    assert len(issues) == 1

    # Mutation: repair the activity's container and verify its migrated identity.
    row = doc["species"]["W"]["observations"].pop()
    doc["species"]["Fe"] = {"observations": [row]}
    repaired = _run_gate(tmp_path, doc)
    repaired_migrator = gate.migrate.Migrator(
        root=gate.migrate.REPO_ROOT, index={}, aliases={}
    )
    repaired_migrator._migrate_extract(path)
    repaired_migrator.finalize()
    repaired_identity = repaired_migrator.result.observations[
        "fixture-source::fixture_activity_a_Fe"
    ].identity.species.formula
    assert repaired_identity == "Fe"
    assert not any(
        item.check == "isolated-migration" and "migrated as W" in item.detail
        for item in repaired.findings
    )


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


def test_locator_page_mismatch_is_soft_when_published_page_is_missing(tmp_path: Path):
    doc = _document()
    locator = doc["species"]["Fe"]["observations"][0]["locator"]
    locator["page"] = 3711
    locator["pdf_page_index"] = 2
    report = _run_gate(tmp_path, doc)

    assert "locator" not in _checks(report)
    assert "locator-page-offset" in _checks(report)
    assert next(item for item in report.findings if item.check == "locator-page-offset").severity == "SOFT"

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


def test_composition_rejects_percent_scale(tmp_path: Path):
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


def test_pure_oxide_system_basis_passes_gate(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "pure_oxide_system",
        "Na2O": "1",
    }
    report = _run_gate(tmp_path, doc)
    assert "composition" not in _checks(report)
    assert "isolated-migration" not in _checks(report)


def test_migrator_supported_descriptive_basis_passes_gate(tmp_path: Path):
    doc = _document()
    doc["species"]["Fe"]["observations"][0]["values"]["composition_mol"] = {
        "basis": "derived_mean_of_n_runs",
        "CaO": "0.45",
        "Al2O3": "0.35",
        "SiO2": "0.20",
    }
    report = _run_gate(tmp_path, doc)
    assert "composition" not in _checks(report)
    assert "isolated-migration" not in _checks(report)


def test_context_compositions_are_preserved_and_not_flagged(tmp_path: Path):
    path = (
        REPO_ROOT
        / "data/literature/extracts/tyurnina-lopatin-stolyarova-2008-ba-borosilicate-kems.yaml"
    )
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    report = evaluate_extract(path, doc, corpus=tmp_path)
    assert "composition" not in _checks(report)

    source_id = str(doc["source_id"])
    source = doc.get("source") or {}
    migrator = gate.migrate.Migrator(
        root=gate.migrate.REPO_ROOT,
        index={
            source_id: {
                "source_id": source_id,
                "citation": str(source.get("citation") or source_id),
                "doi": source.get("doi"),
            }
        },
        aliases={},
    )
    migrator._migrate_extract(path)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues

    source_context = {
        row["observation_id"]: row["values"]
        for block in doc["species"].values()
        for row in block.get("context", [])
        if isinstance(row, dict) and isinstance(row.get("values"), dict)
    }
    migrated_context = {
        row["observation_id"]: row["values"]
        for rows in result.context_by_work.values()
        for row in rows
        if row.get("observation_id") in source_context
    }
    assert migrated_context == source_context

    activity_points = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value.value
        in {"activity", "activity_coefficient", "activity_coefficient_temperature_fit"}
    ]
    assert activity_points
    assert all(
        observation.point_conditions is not None
        and "composition" in observation.point_conditions
        and observation.point_conditions["composition"].state.is_value
        and "BaO"
        in dict(observation.point_conditions["composition"].state.value.components)
        for observation in activity_points
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
    assert gate._composition_issues(doc) == []


def test_fraction_sum_uses_numeric_siblings_and_rounding_band():
    doc = {
        "composition_mole_fraction": {
            "basis": "printed_mole_fraction",
            "SiO2": "60",
            "Al2O3": "38.6",
            "note": "oxides as printed",
        }
    }
    issues = gate._composition_issues(doc)
    assert any("sum to 98.6" in issue for issue in issues)

    doc["composition_mole_fraction"].update(SiO2="50", Al2O3="50")
    assert any("sum to 100" in issue for issue in gate._composition_issues(doc))

    doc["composition_mole_fraction"].update(SiO2="0.6", Al2O3="0.4")
    assert not any("percent-as-fraction" in issue for issue in gate._composition_issues(doc))


def test_printed_zero_does_not_make_a_mixture():
    assert not gate._source_prints_mixture(
        {"composition_as_printed": {"CaO": "0", "SiO2": "0.3"}}, "FeO"
    )


@pytest.mark.parametrize(
    ("observation_id", "source_composition"),
    [
        (
            "composition_map_mixture",
            {"composition_as_printed": {"CaO": "0.7", "SiO2": "0.3"}},
        ),
        (
            "unknown_component_mixture",
            {
                "composition_as_printed": {
                    "CaO": "not_reported",
                    "SiO2": "0.3",
                }
            },
        ),
        ("x_field_mixture", {"x_SiO2_mol_percent_as_printed": "30"}),
        (
            "wt_field_mixture",
            {
                "x_CaO_wt_percent_as_printed": "70",
                "x_SiO2_wt_percent_as_printed": "30",
            },
        ),
        (
            "mass_field_mixture",
            {
                "x_CaO_mass_percent_as_printed": "70",
                "x_SiO2_mass_percent_as_printed": "30",
            },
        ),
    ],
    ids=(
        "composition-map",
        "unknown-component",
        "mol-percent",
        "wt-percent",
        "mass-percent",
    ),
)
def test_unbound_transition_composition_is_rejected_by_identity_profile(
    tmp_path: Path,
    observation_id: str,
    source_composition: dict[str, object],
):
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row["observation_id"] = observation_id
    row["type"] = "transition_point"
    row["values"] = {
        "quantity": "transition_temperature",
        "property_kind": "eutectic",
        "temperature_K": 1800,
        **source_composition,
    }
    doc["species"] = {"FeO": {"observations": [row]}}
    doc["fidelity_samples"][0]["path"] = (
        f"species.FeO.observations[{observation_id}].values.temperature_K"
    )
    doc["fidelity_samples"][0]["value"] = 1800
    report = _run_gate(tmp_path, doc)

    assert "isolated-migration" in _checks(report)
    assert report.migration_hard_issues > 0
    assert "printed mixture has no composition" in next(
        item.detail
        for item in report.findings
        if item.check == "isolated-migration"
    )


def test_mixed_series_transition_compositions_are_checked_per_point(
    tmp_path: Path,
):
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row.update(observation_id="mixed_transition_series", type="transition_point")
    row["values"] = {
        "quantity": "transition_temperature",
        "property_kind": "melting_point",
        "points": [
            {
                "T_K": 1800,
                "composition_as_printed": {"CaO": "0.7", "SiO2": "0.3"},
                "composition_mol": {"CaO": "0.7", "SiO2": "0.3"},
            },
            {
                "T_K": 1900,
                "sample": "FeO",
                "sample_as_printed": "FeO",
                "composition_as_printed": {"FeO": "1"},
                "composition_mol": {"FeO": "1"},
            },
        ],
    }
    doc["species"] = {"FeO": {"observations": [row]}}
    doc["fidelity_samples"][0]["path"] = (
        "species.FeO.observations[mixed_transition_series].values.points[0].T_K"
    )
    doc["fidelity_samples"][0]["value"] = 1800

    report = _run_gate(tmp_path, doc)

    assert not report.hard_failed
    assert report.migration_hard_issues == 0
    assert "isolated-migration" not in _checks(report)


def test_inherited_transition_composition_applies_to_unmarked_points(
    tmp_path: Path,
):
    doc = _document()
    row = doc["species"]["Fe"]["observations"][0]
    row.update(observation_id="inherited_transition_series", type="transition_point")
    row["values"] = {
        "quantity": "transition_temperature",
        "property_kind": "melting_point",
        "composition_as_printed": {"CaO": "0.7", "SiO2": "0.3"},
        "composition_mol": {"CaO": "0.7", "SiO2": "0.3"},
        "points": [
            {"T_K": 1800},
            {
                "T_K": 1900,
                "sample": "FeO",
                "sample_as_printed": "FeO",
                "composition_as_printed": {"FeO": "1"},
                "composition_mol": {"FeO": "1"},
            },
        ],
    }
    doc["species"] = {"FeO": {"observations": [row]}}
    doc["fidelity_samples"][0]["path"] = (
        "species.FeO.observations[inherited_transition_series].values.points[0].T_K"
    )
    doc["fidelity_samples"][0]["value"] = 1800

    report = _run_gate(tmp_path, doc)

    assert not report.hard_failed
    assert report.migration_hard_issues == 0
    assert "isolated-migration" not in _checks(report)


def test_profile_bound_transition_extracts_pass_migration_gate():
    extracts = Path(__file__).resolve().parents[2] / "data/literature/extracts"
    for source_id, expected_observations in (
        ("rys-2007-alkali-oxide-thermodynamics", 1001),
        ("costa-2025-ree-cmas-glass-melt-thermochemistry", 28),
    ):
        path = extracts / f"{source_id}.yaml"
        doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        report = gate.GateReport()

        assert gate._migrate_one(path, doc, report) == []
        assert report.migration_hard_issues == 0
        assert sum(report.value_kinds.values()) == expected_observations


def test_zero_components_in_shornikov_rows_do_not_make_transition_mixtures():
    path = Path(__file__).resolve().parents[2] / "data/literature/extracts/shornikov-2000-aluminosilicate-vaporization.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    rows = [
        (formula, observation)
        for formula, observation in gate.migrate.iter_extract_observations(doc)
        if observation.get("observation_id")
        in {f"shornikov_2000_table1_row_{row:02d}" for row in range(1, 6)}
    ]

    assert len(rows) == 5
    assert all(
        not gate._source_prints_mixture(observation.get("values"), formula)
        for formula, observation in rows
    )


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


def test_cli_audits_extract_with_explicit_tree(tmp_path: Path, capsys):
    path = tmp_path / "fixture-source.yaml"
    doc = _document()
    observation = doc["species"]["Fe"]["observations"][0]
    observation.update(
        {
            "type": "equilibrium_phase_composition",
            "phase": "liquid",
            "units": "wt%",
            "point_conditions": {
                "temperature_K": {
                    "state": {"tag": "value", "value": 1773},
                    "locator": {"pdf_page_index": 1, "table": "1"},
                }
            },
            "values": {
                "quantity": "equilibrium_phase_composition",
                "method_class": "measured_direct",
                "T_K": 1773,
                "composition_mass_percent": {
                    "CaO": 38.3,
                    "Al2O3": 30.5,
                    "SiO2": 18.4,
                    "MgO": 12.7,
                },
                "rows": [
                    {"analyte": "CaO", "value": 38.3},
                    {"analyte": "Al2O3", "value": 30.5},
                    {"analyte": "SiO2", "value": 18.4},
                    {"analyte": "MgO", "value": 12.7},
                ],
            },
        }
    )
    doc["fidelity_samples"][0].update(
        {
            "path": "species.Fe.observations[fe_alpha_1].values.quantity",
            "value": "equilibrium_phase_composition",
        }
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    status = extract_gate.main(
        [str(path), "--tree", str(REPO_ROOT), "--corpus", str(tmp_path)]
    )

    output = capsys.readouterr().out
    assert status == 0, output
    assert f"TREE {REPO_ROOT} HEAD " in output
    assert "MIGRATION observations by ValueKind:" in output
    assert "HARD schema/fidelity:" not in output
    assert "HARD isolated-migration:" not in output
    assert "unsupported quantity" not in output

    doc = _document()
    doc["extraction"]["date"] = "2026-10-18"
    doc["species"]["Fe"]["observations"][0]["locator"].pop("pdf_page_index")
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    status = extract_gate.main(
        [str(path), "--tree", str(REPO_ROOT), "--corpus", str(tmp_path)]
    )
    assert status == 1
    assert f"TREE {REPO_ROOT} HEAD " in capsys.readouterr().out


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
    monkeypatch.setattr(extract_gate, "REPO_ROOT", REPO_ROOT)
    monkeypatch.setattr(
        extract_gate,
        "_tree_head",
        lambda _path: (repo, "test-head"),
    )
    monkeypatch.setattr(
        extract_gate,
        "_tree_modules",
        lambda _tree: nullcontext(
            (gate, validator, yaml_cache, yaml_cache.yaml_mapping_source_issues)
        ),
    )

    class Result:
        returncode = 0
        stderr = ""
        stdout = "data/literature/extracts/fixture-source.yaml\n"

    original_run = extract_gate.subprocess.run
    monkeypatch.setattr(
        extract_gate.subprocess,
        "run",
        lambda args, **kwargs: Result()
        if args[:3] == ["git", "diff", "--name-only"]
        else original_run(args, **kwargs),
    )
    status = extract_gate.main(
        ["--changed-since", "BASE", "--tree", str(repo), "--corpus", str(tmp_path)]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "EXTRACT" in output and "fixture-source.yaml" in output


def test_cli_audits_extract_by_source_id(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    extracts = repo / "data/literature/extracts"
    extracts.mkdir(parents=True)
    (extracts / "fixture-source.yaml").write_text(
        yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(extract_gate, "REPO_ROOT", REPO_ROOT)
    monkeypatch.setattr(extract_gate, "_tree_head", lambda _path: (repo, "test-head"))
    monkeypatch.setattr(
        extract_gate,
        "_tree_modules",
        lambda _tree: nullcontext(
            (gate, validator, yaml_cache, yaml_cache.yaml_mapping_source_issues)
        ),
    )
    status = extract_gate.main(
        ["--ids", "fixture-source", "--tree", str(repo), "--corpus", str(tmp_path)]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "EXTRACT" in output and "fixture-source.yaml" in output


def test_cli_uses_distinct_foreign_tree_and_restores_runner_modules(
    tmp_path: Path, capsys
):
    tree = tmp_path / "foreign-tree"
    shutil.copytree(REPO_ROOT / "simulator", tree / "simulator")
    assert "SUPPORTED_COMPOSITION_MOL_BASES" not in (
        tree / "simulator/battery/migrate.py"
    ).read_text(encoding="utf-8")
    (tree / "data").mkdir()
    shutil.copy2(
        REPO_ROOT / "data/optimize_costs.yaml",
        tree / "data/optimize_costs.yaml",
    )
    literature = tree / "data/literature"
    literature.mkdir()
    shutil.copy2(
        REPO_ROOT / "data/literature/lab_parameter_vocabulary.yaml",
        literature / "lab_parameter_vocabulary.yaml",
    )
    (tree / "tools").mkdir(parents=True)
    (tree / "tools/__init__.py").write_text("", encoding="utf-8")
    (tree / "tools/_foreign_tree_marker.py").write_text(
        "MARKER = 'foreign tree'\n", encoding="utf-8"
    )
    validator_source = (
        REPO_ROOT / "tools/validate_literature_extracts.py"
    ).read_text(encoding="utf-8")
    assert '        "alpha",\n' in validator_source
    (tree / "tools/validate_literature_extracts.py").write_text(
        validator_source.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\nimport tools._foreign_tree_marker\n",
            1,
        ).replace('        "alpha",\n', "", 1),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", str(tree)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tree),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "add",
            "simulator",
            "tools",
            "data",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tree),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "fixture tree",
        ],
        check=True,
        capture_output=True,
    )
    runner_migrate = gate.migrate
    runner_validator = validator
    path = tmp_path / "fixture-source.yaml"
    path.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")
    runner_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "simulator"
        or name.startswith("simulator.")
        or name == "tools"
        or name.startswith("tools.")
    }

    status = extract_gate.main(
        [str(path), "--tree", str(tree), "--corpus", str(tmp_path)]
    )

    output = capsys.readouterr().out
    assert status == 1
    assert "HARD schema/fidelity:" in output
    assert "type must be one of" in output
    assert "MIGRATION observations by ValueKind:" in output
    assert f"TREE {tree} HEAD " in output
    assert all(sys.modules.get(name) is module for name, module in runner_modules.items())
    assert "tools._foreign_tree_marker" not in sys.modules
    assert sys.modules["simulator.battery.migrate"] is runner_migrate
    assert sys.modules["tools.validate_literature_extracts"] is runner_validator


def test_internal_gate_attribute_error_is_not_a_tree_import_error(
    tmp_path: Path, monkeypatch, capsys
):
    path = tmp_path / "fixture-source.yaml"
    path.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        extract_gate,
        "_tree_modules",
        lambda _tree: nullcontext(
            (gate, validator, yaml_cache, yaml_cache.yaml_mapping_source_issues)
        ),
    )

    def injected_gate_error(*_args, **_kwargs):
        raise AttributeError("INJECTED_COMPOSITION_GATE_BUG")

    monkeypatch.setattr(gate, "_composition_issues", injected_gate_error)
    with pytest.raises(AttributeError, match="INJECTED_COMPOSITION_GATE_BUG"):
        extract_gate.main(
            [str(path), "--tree", str(REPO_ROOT), "--corpus", str(tmp_path)]
        )
    assert "HARD tree-import:" not in capsys.readouterr().out


def test_tree_import_refuses_missing_migrator_without_fallback(tmp_path: Path, capsys):
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "init", str(tree)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tree),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "test",
        ],
        check=True,
        capture_output=True,
    )
    extract = tmp_path / "fixture.yaml"
    extract.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")

    status = extract_gate.main(
        [str(extract), "--tree", str(tree), "--corpus", str(tmp_path)]
    )

    assert status == 1
    output = capsys.readouterr().out
    assert "HARD tree-import:" in output
    assert "simulator/battery/migrate.py" in output
