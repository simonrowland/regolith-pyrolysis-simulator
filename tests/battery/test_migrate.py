"""Migration lift: identity, conservation, no-default, corpus validation."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    PerBasis,
    AdmissionStatus,
    BenchIdentityBasis,
    AssetRole,
    EvidenceClass,
    Engine,
    ExecutionState,
    FO2Channel,
    IdentityEqualKind,
    MethodToken,
    MEASURED_EVIDENCE,
    NoticeKind,
    Phase,
    Polymorph,
    PolymorphRejectionKind,
    Quantity,
    MetricOperation,
    Rail,
    ResidualStatus,
    ReferenceStateConvention,
    RefusalReason,
    StateTag,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import atm_to_pa, identity_equal, quantity_token
from simulator.battery.score import predict_with_engine
from simulator.battery.migrate import (
    REPO_ROOT,
    _notice_from_plain,
    DuplicateContextIdError,
    QUEUE_SCHEMA_VERSION,
    DuplicateObservationIdError,
    MigrationResult,
    Migrator,
    UnknownRailSpellingError,
    UnresolvedEquipmentContextError,
    canonicalize_doi,
    canonicalize_rail,
    citation_hash,
    compilation_family_from_store_path,
    convert_area_to_m2,
    convert_areal_mass_to_kg_per_m2,
    convert_mass_to_kg,
    convert_pressure_to_pa,
    convert_temperature_to_k,
    wt_pct_to_mole_fraction,
    dump_yaml,
    iter_observation_store_paths,
    lineage_parents_from_source,
    source_derivation_from_source,
    load_migrated_context,
    map_phase,
    map_quantity,
    parse_quantity_suffix,
    split_qualified_quantity,
    QUANTITY_ALIASES,
    QUANTITY_SOURCE_FIELDS,
    residue_component_source_fields,
    residue_oxide_names,
    residue_point_component_items,
    _HISTORICAL_PREFIX_STEMS,
    compilation_quantity_from_record,
    choose_read_from,
    is_compilation_record_path,
    compilation_record_asset_id,
    compilation_column_series_from_record,
    _provenance_from_extract,
    _initial_oxide_map_from_values,
    _located_printed_and_initial,
    load_migrated_store,
    expand_queue_entries,
    group_queue_entries,
    migrate,
    observation_from_plain,
    migration_queue_document,
    reference_state_from_extract,
    _mole_fraction_composition_from_values,
    _standard_state_from_plain,
    lift_vaporization_reaction_from_ledger_note,
    pressure_from_equipment,
    resolve_equipment_context,
    select_declared_source,
    to_plain,
    observation_from_plain,
    work_id_for,
    write_outputs,

)


def test_ion_current_comparison_provenance_assumes_unreported_pairing() -> None:
    from simulator.battery.validity import comparison_method_cell_constant_cancels
    from simulator.battery.migrate import (
        _comparison_method_cancellation_notice_reason,
    )

    values = {
        "method": "ion current comparison with thermodynamic activity reduction",
        "method_as_printed": "comparison against the individual pure-oxide standard",
    }
    provenance = _provenance_from_extract(
        {}, values, None, quantity=Quantity.ACTIVITY
    )
    assert provenance is not None
    assert provenance["comparison_method"]["kind"] == "comparison_ratio"
    assert provenance["melt_reference_pairing"]["kind"] == (
        "same_effective_setup_assumed"
    )
    assert comparison_method_cell_constant_cancels(provenance)
    reason = _comparison_method_cancellation_notice_reason(provenance)
    assert "assumes cancellation" in reason
    assert "same instrument" in reason
    assert "same-cell pairing not printed" in reason

    coefficient = _provenance_from_extract(
        {}, values, None, quantity=Quantity.ACTIVITY_COEFFICIENT
    )
    assert coefficient is not None
    assert coefficient["comparison_method"]["kind"] == "comparison_ratio"
    assert _provenance_from_extract(
        {}, values, None, quantity=Quantity.P_PARTIAL
    ) is None

    integrated = _provenance_from_extract(
        {},
        {"method": "analytical integration of measured ion-current ratios"},
        None,
        quantity=Quantity.ACTIVITY,
    )
    assert integrated is not None
    assert integrated["comparison_method"]["kind"] == "comparison_ratio"
    assert integrated["melt_reference_pairing"]["kind"] == (
        "same_effective_setup_assumed"
    )

    different = _provenance_from_extract(
        {},
        {
            **values,
            "method_as_printed": (
                "sample and standard measured in different cell geometries"
            ),
        },
        None,
        quantity=Quantity.ACTIVITY,
    )
    assert different is not None
    assert different["melt_reference_pairing"]["kind"] == (
        "different_cells_or_geometry"
    )
    assert not comparison_method_cell_constant_cancels(different)


@pytest.mark.parametrize(
    ("pairing_kind", "expected_pairing"),
    (
        (None, "same_effective_setup_assumed"),
        ("not_printed", "same_effective_setup_assumed"),
        ("not_reported", "same_effective_setup_assumed"),
        ("unknown", "same_effective_setup_assumed"),
        ("same_cell", "same_cell"),
    ),
)
def test_comparison_pairing_migration_golden_pin(
    pairing_kind: str | None, expected_pairing: str
) -> None:
    provenance = _provenance_from_extract(
        {},
        {
            "method": "ion current comparison",
            "provenance": {
                "melt_reference_pairing": {"kind": pairing_kind},
            },
        },
        None,
        quantity=Quantity.ACTIVITY,
    )

    assert provenance is not None
    assert provenance["melt_reference_pairing"]["kind"] == expected_pairing


@pytest.mark.parametrize(
    ("comparison_kind", "expected_kind"),
    (("ratio", "ratio"), ("comparison_ratio", "comparison_ratio")),
)
def test_comparison_ratio_kind_migration_golden_pin(
    comparison_kind: str, expected_kind: str
) -> None:
    provenance = _provenance_from_extract(
        {},
        {
            "method": "ion current comparison",
            "provenance": {"comparison_method": {"kind": comparison_kind}},
        },
        None,
        quantity=Quantity.ACTIVITY,
    )

    assert provenance is not None
    assert provenance["comparison_method"]["kind"] == expected_kind


def test_persisted_kems_activity_recovers_comparison_method_from_calibration(
    tmp_path: Path,
) -> None:
    from tests.battery import factories as F
    from simulator.battery.records import State

    work = F.work()
    experiment = F.kems_experiment(
        "persisted-comparison-activity", work.work_id, kn=Decimal("20")
    )
    assert experiment.apparatus is not None
    experiment = replace(
        experiment,
        apparatus=replace(
            experiment.apparatus,
            calibration={
                "method": F.located(
                    "ion-current comparison using silver vapor pressure as standard"
                ),
                "reference_substance": F.located("silver vapor"),
            },
        ),
    )
    phase_text = "CaO-Al2O3-SiO2 liquid oxide melt"
    activity_identity = F.activity_identity()
    identity = replace(
        activity_identity,
        species=replace(
            activity_identity.species,
            phase=State.unknown(
                f"phase string {phase_text!r} is not in the closed automatic map"
            ),
        ),
    )
    observation = F.observation(
        "persisted-comparison-activity",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="stolyarova-1996-cao-alumina-silica-kems",
    )
    works_dir = tmp_path / "data" / "literature" / "works"
    extracts_dir = tmp_path / "data" / "literature" / "extracts-v2"
    works_dir.mkdir(parents=True)
    extracts_dir.mkdir(parents=True)
    dump_yaml(
        {
            "work": to_plain(work),
            "experiments": [to_plain(experiment)],
        },
        works_dir / "persisted-comparison.yaml",
    )
    dump_yaml(
        {"observations": [to_plain(observation)]},
        extracts_dir / "persisted-comparison.yaml",
    )

    _works, _experiments, observations = load_migrated_store(tmp_path)
    migrated = observations[observation.observation_id]
    assert migrated.identity.species.phase.is_value
    assert migrated.identity.species.phase.value is Phase.L
    assert migrated.provenance is not None
    assert migrated.provenance["melt_reference_pairing"]["kind"] == (
        "same_effective_setup_assumed"
    )
    notice = next(
        notice
        for notice in migrated.notices
        if notice.kind is NoticeKind.COMPARISON_METHOD_CELL_CONSTANT_CANCELS
    )
    assert "assumes cancellation" in notice.reason
    assert "same-cell pairing not printed" in notice.reason


def test_reference_phase_convention_notice_round_trips_and_legacy_notice_stays_valid() -> None:
    from simulator.battery.enums import Authority
    from simulator.battery.records import Notice

    notice = Notice(
        kind=NoticeKind.REFERENCE_PHASE_BY_CONVENTION,
        affected_quantities=(Quantity.DELTA_FG,),
        reason=(
            "JANAF web table prints no phase; ideal-gas reference state per the "
            "monograph convention, page not held"
        ),
        origin="O-029 (O) reference-element table",
        authority=Authority.CONVENTION,
        certification="fetch the JANAF 4th ed. printed page for the table",
    )
    payload = to_plain(notice)
    assert payload["kind"] == "reference_phase_by_convention"
    assert payload["authority"] == "convention"
    assert payload["certification"] == "fetch the JANAF 4th ed. printed page for the table"
    assert _notice_from_plain(payload) == notice

    legacy = {
        "kind": "source_disagreement",
        "affected_quantities": ["delta_fG"],
        "reason": "legacy notice",
        "origin": "legacy-source",
    }
    decoded_legacy = _notice_from_plain(legacy)
    assert decoded_legacy.authority is None
    assert decoded_legacy.certification is None
    assert to_plain(decoded_legacy) == legacy
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Notice,
    Reaction,
    Species,
    State,
    Value,
    as_decimal,
)
from tests.battery import load_observation_store_summary
from simulator.battery.validate import validate_corpus
from simulator.yaml_cache import YAML12SafeLoader, load_cached_safe_yaml
from tests.battery import factories as F

FIXTURE_EXTRACT = {
    "schema_version": "literature_extract.v1",
    "source_id": "fixture-source",
    "source": {
        "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
        "doi": "10.1234/FIXTURE",
    },
    "extraction": {"method": "unit_test", "date": "2026-09-13", "worker": "pytest"},
    "review_status": "draft",
    "fidelity_samples": [
        {
            "path": "species.Na.observations[na_psat].values.series",
            "value": [{"T_K": 1200.0, "pressure_atm": 1.0}],
            "note": "fixture",
            "locator": {"table": "I", "page": 2},
        }
    ],
    "species": {
        "Na": {
            "observations": [
                {
                    "observation_id": "na_psat",
                    "type": "psat_series",
                    "locator": {"table": "I", "page": 2},
                    "phase": "gas",
                    "regime": "knudsen_effusion",
                    "units": "atm",
                    "uncertainty": {"note": "printed ±"},
                    "values": {
                        "quantity": "pure_Psat",
                        "method_class": "measured_direct",
                        "admission_status": "admitted",
                        "series": [
                            {"T_K": 1200.0, "pressure_atm": 1.0},
                            {"T_K": 1300.0, "pressure_atm": 2.0},
                        ],
                    },
                }
            ]
        }
    },
}


def _write_min_tree(root: Path, extract: dict | None = None) -> Path:
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": [
                    {
                        "source_id": "fixture-source",
                        "citation": "Fixture, A. (2026), Test Journal 1:1",
                        "doi": "10.1234/FIXTURE",
                        "corpus": {
                            "commit": "abc123",
                            "raw": {
                                "path": "raw/fixture.pdf",
                                "sha256": "deadbeef",
                            },
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    doc = extract if extract is not None else FIXTURE_EXTRACT
    (extracts / "fixture-source.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return root


def _migrate_real_extract(
    tmp_path: Path,
    name: str,
    *,
    write: bool = False,
    use_repository_index_row: bool = False,
    extract_doc: dict | None = None,
):
    src = REPO_ROOT / "data" / "literature" / "extracts" / name
    doc = (
        extract_doc
        if extract_doc is not None
        else yaml.safe_load(src.read_text(encoding="utf-8"))
    )
    assert isinstance(doc, dict)
    root = tmp_path / "tree"
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    source_id = doc.get("source_id") or src.stem
    if use_repository_index_row:
        repository_index = yaml.safe_load(
            (REPO_ROOT / "data" / "literature" / "INDEX.yaml").read_text(
                encoding="utf-8"
            )
        )
        index_row = next(
            row for row in repository_index["sources"] if row["source_id"] == source_id
        )
    else:
        index_row = {
            "source_id": source_id,
            "citation": source.get("citation") or src.stem,
            "doi": source.get("doi"),
        }
    index = {"schema_version": "literature_index.v1", "sources": [index_row]}
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(index, sort_keys=False),
        encoding="utf-8",
    )
    extract_text = (
        yaml.safe_dump(doc, sort_keys=False)
        if extract_doc is not None
        else src.read_text(encoding="utf-8")
    )
    (extracts / name).write_text(extract_text, encoding="utf-8")
    return migrate(root, write=write)


def test_holzheid_1997_typed_wustite_reference_and_table2_compositions(
    tmp_path: Path,
) -> None:
    name = "holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml"
    result = _migrate_real_extract(tmp_path / "baseline", name)
    table2_ids = (
        "holzheid_1997_table2_page23_feo",
        "holzheid_1997_table2_page24_feo",
        "holzheid_1997_table2_page25_feo",
    )
    table3_ids = (
        "holzheid_1997_table3_pages27_bk_feo",
        *(f"holzheid_1997_table3b_ad_fe_nico_variable_mgo_1_{i}" for i in range(1, 8)),
    )

    def rows_for(ids: tuple[str, ...]):
        return [
            observation
            for observation in result.observations.values()
            if any(f"::{identifier}::" in observation.observation_id for identifier in ids)
        ]

    table2 = rows_for(table2_ids)
    table3 = rows_for(table3_ids)
    all_rows = [*table2, *table3]
    assert (len(table2), len(table3), len(all_rows)) == (57, 15, 72)

    source_path = REPO_ROOT / "data" / "literature" / "extracts" / name
    source_doc = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    activity_sources = [
        observation
        for observation in source_doc["species"]["FeO"]["observations"]
        if observation.get("type") == "activity_coefficient"
    ]
    assert len(activity_sources) == 11
    for source_observation in activity_sources:
        standard_state = source_observation["standard_state"]
        assert standard_state["locator"] == {
            "published_page": 31,
            "section": "4.1 Activity coefficients",
        }
        assert (
            'For FeO we have chosen an "FeO"-wüstite(l) standard state.'
            in standard_state["note"]
        )

    for observation in all_rows:
        standard_state = observation.identity.reference_state
        assert standard_state.is_value
        reference = standard_state.value
        assert reference.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference.endmember.formula == "Fe(1-x)O"
        assert reference.endmember.phase.value is Phase.L
        assert reference.endmember.charge.is_value
        assert reference.endmember.charge.value == 0
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert reference.component_basis == "Fe(1-x)O"
        assert reference.reference_pressure_bar == Decimal("1.01325")
        assert identity_equal(observation.identity, observation.identity).kind is (
            IdentityEqualKind.IDENTITY_UNKNOWN
        )

    assert all(observation.identity.composition.is_value for observation in table2)
    assert all(observation.identity.composition.is_unknown for observation in table3)
    for observation in table2:
        temperature = observation.point_conditions["temperature_K"]
        composition = observation.point_conditions["composition"]
        assert temperature.state.is_value and temperature.locator is not None
        assert composition.state.is_value and composition.locator is not None
        assert composition.state.value == observation.identity.composition.value
        assert composition.inference is not None
        assert len(composition.inference.inputs) == 3
        assert "Table 1" in composition.inference.inputs[0]
        assert "Table 2 run" in composition.inference.inputs[1]
        assert "other oxide ratios assumed to persist" in composition.inference.inputs[2]
        assert "not a renormalisation of a partial analysis (d-131)" in (
            composition.inference.inputs[2]
        )
        assert abs(
            sum(
                (value for _, value in composition.state.value.components),
                Decimal("0"),
            )
            - 1
        ) < Decimal("1e-24")
    assert Counter(issue.reason.value for issue in result.validation.hard_issues) == Counter(
        {"conditional_field": 4}
    )

    expected = {
        "::T=1677:row=ad-10:": {
            "SiO2": "0.4735",
            "CaO": "0.2482",
            "MgO": "0.1442",
            "Al2O3": "0.0757",
            "FeO": "0.0556",
            "NiO": "0.0003",
            "CoO": "0.0025",
        },
        "::T=1672:row=bk-59-a:": {
            "SiO2": "0.4686",
            "CaO": "0.1963",
            "MgO": "0.1508",
            "Al2O3": "0.0793",
            "FeO": "0.0437",
            "NiO": "0.0000",
            "CoO": "0.0612",
        },
        "::T=1672:row=bk-59-b:": {
            "SiO2": "0.4990",
            "CaO": "0.2091",
            "MgO": "0.1606",
            "Al2O3": "0.0844",
            "FeO": "0.0407",
            "NiO": "0.0062",
            "CoO": "0.0000",
        },
    }
    for key_fragment, expected_components in expected.items():
        observation = next(row for row in table2 if key_fragment in row.observation_id)
        composition = observation.identity.composition.value
        assert {
            name: f"{value:.4f}" for name, value in composition.components
        } == expected_components
        condition = observation.point_conditions["composition"]
        assert condition.inference.relation == (
            "calculated_table1_starting_bulk_with_table2_run_FeO_NiO_CoO_mole_fractions_and_remaining_oxide_ratios"
        )

    # Mutating a persisted run composition must fail the three-run expected-value pin.
    mutated_doc = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    mutated_doc["species"]["FeO"]["observations"][0]["values"]["series"][0][
        "composition_mol"
    ]["SiO2"] = "0.9999"
    mutated = _migrate_real_extract(
        tmp_path / "mutation", name, extract_doc=mutated_doc
    )
    mutated_row = next(
        observation
        for observation in mutated.observations.values()
        if "::holzheid_1997_table2_page23_feo::T=1677:row=ad-10:" in observation.observation_id
    )
    assert mutated_row.identity.composition.value.components[0][1] == Decimal("0.9999")
    assert f"{mutated_row.identity.composition.value.components[0][1]:.4f}" != expected[
        "::T=1677:row=ad-10:"
    ]["SiO2"]


def _extract_observations(name: str) -> list[dict]:
    src = REPO_ROOT / "data" / "literature" / "extracts" / name
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    found: list[dict] = []
    species = doc.get("species") if isinstance(doc, dict) else None
    if not isinstance(species, dict):
        return found
    for block in species.values():
        rows = block.get("observations") if isinstance(block, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                found.append(row)
    return found


def _raw_inputs_extract(method_class: str, *, include_locator: bool = True) -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    observation = extract["species"]["Na"]["observations"][0]
    observation["values"]["method_class"] = method_class
    raw_inputs = {"state": {"tag": "value", "value": "not_printed"}}
    if include_locator:
        raw_inputs["locator"] = {"page": 2, "section": "3.3"}
    observation["values"]["derivation"] = {
        "relation": "calculated from measured quantities",
        "output_unit": "Pa",
        "raw_inputs": raw_inputs,
    }
    return extract


def test_production_migration_keeps_located_unprinted_raw_input_declaration(
    tmp_path: Path,
) -> None:
    result = migrate(
        _write_min_tree(tmp_path, _raw_inputs_extract("measured_reduced")),
        write=False,
    )
    observation = next(iter(result.observations.values()))

    assert observation.derivation is not None
    assert observation.derivation.inputs == ()
    assert observation.derivation.raw_inputs is not None
    assert observation.derivation.raw_inputs.state.value.value == "not_printed"
    assert observation.derivation.raw_inputs.locator is not None
    assert result.validation.hard_issues == ()

    reloaded = observation_from_plain(
        yaml.safe_load(yaml.safe_dump(to_plain(observation)))
    )
    assert reloaded.derivation == observation.derivation


def test_null_derivation_inputs_are_invalid_even_with_raw_input_declaration(
    tmp_path: Path,
) -> None:
    extract = _raw_inputs_extract("measured_reduced")
    extract["species"]["Na"]["observations"][0]["values"]["derivation"][
        "inputs"
    ] = None

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    assert any(
        issue.path.endswith(".values.derivation")
        and issue.reason is RefusalReason.INVALID_SOURCE
        and "Derivation.inputs" in issue.detail
        for issue in result.validation.hard_issues
    )


def test_production_migration_rejects_unlocated_unprinted_raw_input_declaration(
    tmp_path: Path,
) -> None:
    result = migrate(
        _write_min_tree(
            tmp_path, _raw_inputs_extract("measured_reduced", include_locator=False)
        ),
        write=False,
    )
    assert any(
        issue.path.endswith(".derived_from")
        and issue.detail == "derived observation requires derived_from"
        for issue in result.validation.hard_issues
    )


@pytest.mark.parametrize(
    ("inputs", "raw_inputs", "expected_path", "expected_detail"),
    [
        (
            ["pdf:fixture-source"],
            {"state": {"tag": "value", "value": "not_printed"}},
            ".derivation.raw_inputs",
            "requires a locator",
        ),
        (
            [],
            {"state": {"tag": "value", "value": "not_printed"}},
            ".values.derivation",
            "requires a locator",
        ),
        (
            ["pdf:fixture-source"],
            {
                "state": {"tag": "unknown", "reason": "not known"},
                "locator": {"page": 2},
            },
            ".derivation.raw_inputs",
            "must be not_printed",
        ),
        (
            None,
            {"state": {"tag": "value", "value": "not_printed"}},
            ".values.derivation",
            "requires a locator",
        ),
    ],
    ids=["unlocated-pdf-input", "unlocated-empty", "located-unknown", "omitted-input"],
)
def test_invalid_raw_input_declarations_return_typed_issues(
    tmp_path: Path,
    inputs: object,
    raw_inputs: dict,
    expected_path: str,
    expected_detail: str,
) -> None:
    extract = _raw_inputs_extract("measured_reduced", include_locator=False)
    annotation = extract["species"]["Na"]["observations"][0]["values"][
        "derivation"
    ]
    if inputs is not None:
        annotation["inputs"] = inputs
    annotation["raw_inputs"] = raw_inputs

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    assert result.validation.hard_issues
    assert any(
        issue.path.endswith(expected_path) and expected_detail in issue.detail
        for issue in result.validation.hard_issues
    )


@pytest.mark.parametrize("location", ["values.derivation", "derivation"])
@pytest.mark.parametrize(
    ("raw_inputs", "detail"),
    [
        (
            {"state": {"tag": "value", "value": "printed"}, "locator": {"page": 2}},
            "printed",
        ),
        (
            {"state": {"tag": "value", "value": 7}, "locator": {"page": 2}},
            "RawInputStatus",
        ),
        ({"locator": {"page": 2}}, "contain a state"),
    ],
    ids=["unknown-literal", "wrong-type", "missing-state"],
)
def test_invalid_raw_input_parse_is_a_typed_issue_at_each_annotation_location(
    tmp_path: Path, location: str, raw_inputs: dict, detail: str
) -> None:
    extract = _raw_inputs_extract("measured_reduced")
    observation = extract["species"]["Na"]["observations"][0]
    if location == "values.derivation":
        annotation = observation["values"]["derivation"]
    else:
        annotation = dict(observation["values"]["derivation"])
        observation["derivation"] = annotation
    annotation["raw_inputs"] = raw_inputs

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(f".{location}")
        and detail in issue.detail
        for issue in result.registry_issues
    )


def _malformed_derivation_annotation(shape: str) -> object:
    annotation: dict = {
        "relation": "author_calculation",
        "inputs": ["pdf:fixture-source"],
        "output_unit": "Pa",
    }
    valid_raw_inputs = {
        "state": {"tag": "value", "value": "not_printed"},
        "locator": {"page": 2},
    }
    malformed_raw_inputs = {
        "state-tag": {
            "state": {"tag": "printed", "value": "not_printed"},
            "locator": {"page": 2},
        },
        "empty-locator": {
            "state": {"tag": "value", "value": "not_printed"},
            "locator": {"page": ""},
        },
        "enum-literal": {
            "state": {"tag": "value", "value": "printed"},
            "locator": {"page": 2},
        },
        "missing-state": {"locator": {"page": 2}},
        "missing-value": {
            "state": {"tag": "value"},
            "locator": {"page": 2},
        },
        "missing-reason": {
            "state": {"tag": "unknown"},
            "locator": {"page": 2},
        },
    }
    if shape in malformed_raw_inputs:
        annotation["raw_inputs"] = malformed_raw_inputs[shape]
        if shape == "empty-locator":
            annotation["inputs"] = []
        return annotation
    if shape.startswith("state-type-"):
        annotation["raw_inputs"] = {
            "state": {"int": 7, "list": [], "none": None}[shape.removeprefix("state-type-")],
            "locator": {"page": 2},
        }
        return annotation
    if shape.startswith("locator-type-"):
        annotation["raw_inputs"] = {
            "state": valid_raw_inputs["state"],
            "locator": {"int": 7, "list": [], "none": None}[shape.removeprefix("locator-type-")],
        }
        return annotation
    if shape.startswith("value-type-"):
        annotation["raw_inputs"] = {
            "state": {
                "tag": "value",
                "value": {"int": 7, "list": [], "none": None}[shape.removeprefix("value-type-")],
            },
            "locator": {"page": 2},
        }
        return annotation
    if shape.startswith("tag-type-"):
        annotation["raw_inputs"] = {
            "state": {
                "tag": {"int": 7, "list": [], "none": None}[shape.removeprefix("tag-type-")],
                "value": "not_printed",
            },
            "locator": {"page": 2},
        }
        return annotation
    if shape.startswith("reason-type-"):
        annotation["raw_inputs"] = {
            "state": {
                "tag": "unknown",
                "reason": {"int": 7, "list": [], "none": None}[shape.removeprefix("reason-type-")],
            },
            "locator": {"page": 2},
        }
        return annotation
    if shape in {"inputs-int", "inputs-list", "inputs-none", "inputs-mapping"}:
        annotation["inputs"] = {
            "inputs-int": 7,
            "inputs-list": [7],
            "inputs-none": None,
            "inputs-mapping": {"source": "pdf:fixture-source"},
        }[shape]
        return annotation
    if shape in {"parameters-int", "parameters-list", "parameters-none"}:
        annotation["parameters"] = {
            "parameters-int": 7,
            "parameters-list": ["not-a-pair"],
            "parameters-none": None,
        }[shape]
        return annotation
    if shape in {"relation-int", "relation-list", "relation-none"}:
        annotation["relation"] = {
            "relation-int": 7,
            "relation-list": [],
            "relation-none": None,
        }[shape]
        return annotation
    if shape in {"output-unit-int", "output-unit-list", "output-unit-none"}:
        annotation["output_unit"] = {
            "output-unit-int": 7,
            "output-unit-list": [],
            "output-unit-none": None,
        }[shape]
        return annotation
    if shape in {"pure-reference-int", "pure-reference-list"}:
        annotation["pure_substance_reference"] = {
            "pure-reference-int": 7,
            "pure-reference-list": [],
        }[shape]
        return annotation
    if shape == "missing-inputs":
        annotation.pop("inputs")
        return annotation
    if shape in {"annotation-int", "annotation-list", "annotation-none", "annotation-string"}:
        return {
            "annotation-int": 7,
            "annotation-list": [],
            "annotation-none": None,
            "annotation-string": "not a mapping",
        }[shape]
    raise AssertionError(shape)


def _fixture_registry_experiment(inference: object) -> dict:
    return {
        "experiment_id": "fixture-experiment",
        "sample": {
            "initial_composition": {
                "state": {
                    "tag": "value",
                    "value": {
                        "basis": "printed_oxides",
                        "amount_basis": "mole_fraction",
                        "components": [["SiO2", "1"]],
                    },
                },
                "locator": {"page": 2},
                "inference": inference,
            }
        },
    }


_MALFORMED_ANNOTATION_SHAPES = [
    "state-tag",
    "empty-locator",
    "enum-literal",
    "state-type-int",
    "state-type-list",
    "state-type-none",
    "locator-type-int",
    "locator-type-list",
    "locator-type-none",
    "tag-type-int",
    "tag-type-list",
    "tag-type-none",
    "value-type-int",
    "value-type-list",
    "value-type-none",
    "reason-type-int",
    "reason-type-list",
    "reason-type-none",
    "missing-state",
    "missing-value",
    "missing-reason",
    "inputs-int",
    "inputs-list",
    "inputs-none",
    "inputs-mapping",
    "parameters-int",
    "parameters-list",
    "parameters-none",
    "relation-int",
    "relation-list",
    "relation-none",
    "output-unit-int",
    "output-unit-list",
    "output-unit-none",
    "pure-reference-int",
    "pure-reference-list",
    "missing-inputs",
    "annotation-int",
    "annotation-list",
    "annotation-none",
    "annotation-string",
]


def test_production_annotation_boundary_reports_malformed_shapes(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row_template = extract["species"]["Na"]["observations"][0]
    locations = (
        "values.derivation",
        "derivation",
        "values.inference",
        "inference",
        "registry.inference",
    )
    expected_paths = []
    observation_rows = []
    registry_experiments = []
    ordinal = 0
    for location in locations:
        for shape in _MALFORMED_ANNOTATION_SHAPES:
            annotation = _malformed_derivation_annotation(shape)
            if location == "registry.inference":
                experiment = _fixture_registry_experiment(annotation)
                local_experiment_id = f"fixture-experiment-{ordinal}"
                experiment["experiment_id"] = local_experiment_id
                expected_paths.append(
                    (
                        location,
                        shape,
                        f"::experiment::{local_experiment_id}].sample.initial_composition",
                    )
                )
                registry_experiments.append(experiment)
            else:
                row = deepcopy(row_template)
                observation_id = f"annotation-{ordinal}"
                row["observation_id"] = observation_id
                field = location.rsplit(".", 1)[-1]
                target = row["values"] if location.startswith("values.") else row
                target[field] = annotation
                expected_paths.append(
                    (
                        location,
                        shape,
                        f"observations.fixture-source::{observation_id}.{location}",
                    )
                )
                observation_rows.append(row)
            ordinal += 1

    extract["species"]["Na"]["observations"] = observation_rows
    extract["experiments"] = registry_experiments

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    issues = [*result.registry_issues, *result.validation.hard_issues]
    for location, shape, expected_path in expected_paths:
        matching = [
            issue
            for issue in issues
            if (
                issue.path.endswith(expected_path)
                if location == "registry.inference"
                else issue.path == expected_path
            )
            and issue.reason is RefusalReason.INVALID_SOURCE
        ]
        assert matching, (location, shape)
        if shape == "state-tag":
            assert any("not a valid StateTag" in issue.detail for issue in matching)
        if shape == "empty-locator":
            assert any(
                "Locator requires at least one nonempty location key" in issue.detail
                for issue in matching
            )
        if shape == "enum-literal":
            assert any(
                "not a valid RawInputStatus" in issue.detail for issue in matching
            )
        if location == "registry.inference":
            local_experiment_id = expected_path.split("::experiment::", 1)[1].split("]", 1)[0]
            experiment_id = next(
                identifier
                for identifier in result.experiments
                if identifier.endswith(f"::experiment::{local_experiment_id}")
            )
            sample = result.experiments[experiment_id].sample
            assert sample.initial_composition is not None
            assert sample.initial_composition.state.is_value
        else:
            assert result.observations


@pytest.mark.parametrize("location", ["values.derivation", "registry.inference"])
def test_string_derivation_inputs_keep_their_existing_meaning(
    tmp_path: Path, location: str
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    annotation = {
        "relation": "author_calculation",
        "inputs": "pdf:fixture-source",
        "output_unit": "Pa",
    }
    if location == "registry.inference":
        extract["experiments"] = [_fixture_registry_experiment(annotation)]
        expected_path = "experiment[fixture-experiment].sample.initial_composition"
    else:
        extract["species"]["Na"]["observations"][0]["values"]["derivation"] = annotation
        expected_path = "observations.fixture-source::na_psat.values.derivation"

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    assert not any(
        issue.path == expected_path and issue.reason is RefusalReason.INVALID_SOURCE
        for issue in [*result.registry_issues, *result.validation.hard_issues]
    )


def test_not_printed_declaration_conflicts_with_listed_inputs_and_preserves_them(
    tmp_path: Path,
) -> None:
    extract = _raw_inputs_extract("measured_reduced")
    row = extract["species"]["Na"]["observations"][0]
    row["values"]["derivation"]["inputs"] = ["pdf:fixture-source"]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    observation = next(iter(result.observations.values()))
    assert observation.derivation is not None
    assert observation.derivation.inputs == ("pdf:fixture-source",)
    assert any(
        issue.path.endswith(".derivation.raw_inputs")
        and "conflicts with listed derivation inputs" in issue.detail
        for issue in result.validation.hard_issues
    )


def test_registry_composition_accepts_existing_mapping_inference_inputs() -> None:
    from simulator.battery.migrate import _experiment_from_plain

    extract_path = (
        Path(__file__).parents[2]
        / "data/literature/extracts/ta-badro-2021.yaml"
    )
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    registry_experiment = extract["experiments"][0]
    experiment = _experiment_from_plain(
        registry_experiment, registry_issues=[]
    )

    assert experiment.sample.initial_composition is not None
    assert experiment.sample.initial_composition.inference is not None
    assert experiment.sample.initial_composition.inference.inputs[0] == str(
        extract["experiments"][0]["sample"]["initial_composition"]["inference"][
            "inputs"
        ][0]
    )


def test_malformed_registry_inference_records_issue_without_dropping_value() -> None:
    from simulator.battery.migrate import _experiment_from_plain

    extract_path = (
        Path(__file__).parents[2]
        / "data/literature/extracts/ta-badro-2021.yaml"
    )
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    registry_experiment = extract["experiments"][0]
    registry_experiment["sample"]["initial_composition"]["inference"][
        "inputs"
    ] = 42
    issues = []
    experiment = _experiment_from_plain(
        registry_experiment, registry_issues=issues
    )

    assert experiment.sample.initial_composition is not None
    assert experiment.sample.initial_composition.state.is_value
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert issues[0].path == (
        "experiment[vp-series].sample.initial_composition"
    )


def test_production_migration_rejects_model_derived_raw_input_declaration(
    tmp_path: Path,
) -> None:
    result = migrate(
        _write_min_tree(tmp_path, _raw_inputs_extract("model_derived")),
        write=False,
    )
    assert any(
        issue.path.endswith(".derivation.raw_inputs")
        and "invalid for model_derived" in issue.detail
        for issue in result.validation.hard_issues
    )


def test_hastie_initial_charges_bind_to_figure_points_as_proxies(
    tmp_path: Path,
) -> None:
    from simulator.battery.migrate import wt_pct_to_mole_fraction

    result = _migrate_real_extract(tmp_path, "kems-020-hastie-1981-nbsir.yaml")
    expected_wt_pct = {
        "fig4_illite_K_SiO_digitized": {
            "K2O": Decimal("7.4"),
            "Al2O3": Decimal("26.0"),
            "Fe2O3": Decimal("4.4"),
            "MgO": Decimal("2.1"),
            "SiO2": Decimal("60.2"),
            "Na2O": Decimal("0.2"),
        },
        "fig5_glass_Na_this_work_digitized": {
            "Na2O": Decimal("17"),
            "CaO": Decimal("12"),
            "SiO2": Decimal("71"),
        },
        "fig11_k1_slag_k2o_gamma_digitized": {
            "K2O": Decimal("19.5"),
            "Al2O3": Decimal("12.1"),
            "Fe2O3": Decimal("14.3"),
            "CaO": Decimal("3.8"),
            "MgO": Decimal("1.0"),
            "SiO2": Decimal("46.8"),
            "Na2O": Decimal("0.5"),
        },
    }
    expected_counts = {
        "fig4_illite_K_SiO_digitized": 6,
        "fig5_glass_Na_this_work_digitized": 6,
        # The Figure 11 caption moves one point to TMS: 19 - 1 = 18 KMS.
        "fig11_k1_slag_k2o_gamma_digitized": 18,
    }
    for series, composition_wt_pct in expected_wt_pct.items():
        rows = [
            observation
            for observation in result.observations.values()
            if series in observation.observation_id
            and observation.identity.quantity.is_value
            and observation.identity.quantity.value
            in {Quantity.P_PARTIAL, Quantity.ACTIVITY_COEFFICIENT}
        ]
        assert len(rows) == expected_counts[series]
        if series == "fig5_glass_Na_this_work_digitized":
            assert all(
                observation.evidence.class_.is_value
                and observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
                for observation in rows
            )
        expected = wt_pct_to_mole_fraction(composition_wt_pct)
        for observation in rows:
            composition = observation.identity.composition
            assert composition is not None and composition.is_value
            assert composition.value.as_map() == expected.as_map()
            assert composition.value.proxy_flag == "initial_charge_only"

    k1 = next(
        row
        for row in _extract_observations("kems-020-hastie-1981-nbsir.yaml")
        if row.get("observation_id") == "hastie_1981_fig11_k1_slag_k2o_gamma_digitized"
    )
    k1_experiment = result.experiments[
        next(
            observation.experiment_id
            for observation in result.observations.values()
            if "::hastie_1981_fig11_k1_slag_k2o_gamma_digitized::T="
            in observation.observation_id
        )
    ]
    assert k1["experiment"] == "hastie-1981-k1-kms"
    assert k1_experiment.experiment_id.endswith("::experiment::hastie-1981-k1-kms")
    assert k1_experiment.bench_id is None
    assert k1_experiment.apparatus is None
    assert k1_experiment.locator is not None
    assert k1_experiment.locator.page == 10
    assert k1_experiment.locator.section == "6.2 Partial Pressure Determination"

    k2_experiment = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("::experiment::hastie-1981-pt-kms")
    )
    assert k2_experiment.apparatus is not None
    assert k2_experiment.apparatus.calibration is not None
    k2_calibration = k2_experiment.apparatus.calibration["pressure_method"]
    assert k2_calibration.locator is not None
    assert k2_calibration.locator.page == 32
    assert k2_calibration.locator.section == "discussion of Figure 12 KMS data"
    assert k2_experiment.bench_id is not None
    k2_bench = result.benches[k2_experiment.bench_id]
    assert k2_bench.cell_material_and_liner is not None
    assert k2_bench.cell_material_and_liner.state.is_value
    assert k2_bench.cell_material_and_liner.state.value == "platinum KMS cell"

    moved = next(
        row
        for row in _extract_observations("kems-020-hastie-1981-nbsir.yaml")
        if row.get("observation_id")
        == "hastie_1981_fig11_k1_slag_k2o_gamma_tms_open_square"
    )
    (moved_point,) = moved["values"]["points"]
    assert moved["experiment"] == "hastie-1981-pt-tms"
    assert moved["regime"] == "transpiration_mass_spectrometry"
    assert moved_point["run"] == 1
    assert moved_point["T_K"] == 1575.0
    assert "Digitization read 1559.9 K" in moved_point["note"]
    assert "caption prints 1575 K" in moved_point["note"]
    moved_migrated = [
        observation
        for observation in result.observations.values()
        if "::hastie_1981_fig11_k1_slag_k2o_gamma_tms_open_square::T=1575.0:"
        in observation.observation_id
    ]
    assert len(moved_migrated) == 1
    moved_observation = moved_migrated[0]
    assert result.experiments[moved_observation.experiment_id].experiment_id.endswith(
        "::experiment::hastie-1981-pt-tms"
    )
    assert moved_observation.identity.temperature_K.is_value
    assert moved_observation.identity.temperature_K.value == Decimal("1575.0")
    moved_composition = moved_observation.identity.composition
    assert moved_composition is not None and moved_composition.is_value
    assert moved_composition.value.proxy_flag == "initial_charge_only"
    moved_prediction = predict_with_engine(
        Engine.OPENIMCC,
        moved_observation,
        experiment=result.experiments[moved_observation.experiment_id],
    )
    assert moved_prediction.value is None
    assert moved_prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert moved_prediction.refusal_detail["reason"] == "missing_evidence"
    (oxygen_gap,) = moved_prediction.refusal_detail["gaps"]
    assert oxygen_gap["waypoint"] == "oxygen_condition"
    assert "fO2_log" in oxygen_gap["missing"]

    na_kms = next(
        row
        for row in _extract_observations("kems-020-hastie-1981-nbsir.yaml")
        if row.get("observation_id") == "hastie_1981_fig5_glass_Na_this_work_digitized"
    )
    assert na_kms["experiment"] == "hastie-1981-measured-soda-lime-glass-kms"
    assert na_kms["phase"] == "gas"
    assert [point["series"] for point in na_kms["values"]["points"]] == [
        "KMS_1",
        "KMS_1",
        "KMS_3",
    ]
    assert [point["T_K"] for point in na_kms["values"]["points"]] == [
        1488.8,
        1333.3,
        1428.6,
    ]
    na_kms_migrated = [
        observation
        for observation in result.observations.values()
        if "::hastie_1981_fig5_glass_Na_this_work_digitized::T="
        in observation.observation_id
        and result.experiments[observation.experiment_id].experiment_id.endswith(
            "::experiment::hastie-1981-measured-soda-lime-glass-kms"
        )
    ]
    assert len(na_kms_migrated) == 3
    assert {
        observation.identity.temperature_K.value
        for observation in na_kms_migrated
    } == {Decimal("1488.8"), Decimal("1333.3"), Decimal("1428.6")}
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in na_kms_migrated
    )


def test_repaired_figure_read_sources_keep_figure_only_evidence(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "kems-025-markova-1983.yaml",
            (
                "markova_1983_fe_psat_figs",
                "markova_1983_mg_psat_figs",
                "markova_1983_feo_psat_figs",
            ),
        ),
        (
            "stolyarova-1996-cao-alumina-silica-kems.yaml",
            (
                "stolyarova_1996_fig1_sio2_activity_figure_only",
                "stolyarova_1996_fig2_sio2_activity_figure_only",
                "stolyarova_1996_fig1_cao_activity_figure_only",
                "stolyarova_1996_fig2_cao_activity_figure_only",
                "stolyarova_1996_fig1_al2o3_activity_figure_only",
                "stolyarova_1996_fig2_al2o3_activity_figure_only",
            ),
        ),
        (
            "kems-035-sauerborn-2005.yaml",
            (
                "sauerborn_2005_jsc1_composition_figure_only_blocked_deep2",
                "sauerborn_2005_fig523_ms1_epma_glass_deep2",
                "sauerborn_2005_fig532_rfa_oxide_ratio_deep2",
                "sauerborn_2005_qms_traces_figure_only_blocked_deep2",
                "sauerborn_2005_fig555_o2_reference_normalized_deep2",
                "sauerborn_2005_fig556_o2_vs_T_normalized_deep2",
            ),
        ),
    )
    for name, identifiers in cases:
        migrated = _migrate_real_extract(tmp_path / name, name)
        for identifier in identifiers:
            matching = [
                observation
                for observation in migrated.observations.values()
                if f"::{identifier}" in observation.observation_id
            ]
            assert matching, (name, identifier)
            assert all(
                observation.evidence.class_.is_value
                and observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
                for observation in matching
            ), (name, identifier)


def test_printed_value_exclusions_keep_their_migrated_refusals(
    tmp_path: Path,
) -> None:
    tsukihashi = _migrate_real_extract(tmp_path / "ts1985", "ts1985.yaml")
    coefficients = [
        observation
        for observation in tsukihashi.observations.values()
        if "::ts1985_na2o_table2_log10_a_AT_B::" in observation.observation_id
    ]
    assert len(coefficients) == 4
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for observation in coefficients
    )

    mendybaev = _migrate_real_extract(
        tmp_path / "mendybaev",
        "mendybaev-2021-cai-low-pressure-h2-evap.yaml",
    )
    rates = [
        observation
        for observation in mendybaev.observations.values()
        if "::mendybaev_2021_weight_loss_rates_by_arm" in observation.observation_id
    ]
    assert len(rates) == 1
    assert rates[0].evidence.class_.is_unknown
    assert rates[0].value.kind is ValueKind.UNAVAILABLE


def _run_migrate_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_kor_sulfur_tables_are_typed_refusals_and_keep_24_hour_footnote() -> None:
    extract = REPO_ROOT / "data/literature/extracts/kor-1967-thesis-sulphur-oxides-slags.yaml"
    tables = {
        row["observation_id"]: row
        for row in _extract_observations(extract.name)
        if row["observation_id"] in {
            "kor_1967_table_xxii_sulphur_melt_values",
            "kor_1967_table_xxiii_sulphur_melt_values",
            "kor_1967_table_xxv_sulphur_solid_aluminates",
        }
    }
    expected_rows = {
        "kor_1967_table_xxii_sulphur_melt_values": 12,
        "kor_1967_table_xxiii_sulphur_melt_values": 24,
        "kor_1967_table_xxv_sulphur_solid_aluminates": 18,
    }
    assert set(tables) == set(expected_rows)
    for observation_id, row_count in expected_rows.items():
        table = tables[observation_id]
        assert table["type"] == "sulfur_solubility_and_sulfate_capacity_series"
        assert table["values"]["quantity"] == "sulfur_solubility_wt_percent"
        assert len(table["values"]["rows"]) == row_count

    xxii = tables["kor_1967_table_xxii_sulphur_melt_values"]["values"]
    average_row = next(row for row in xxii["rows"] if row[0] == "150")
    assert average_row[3] == "0.268"

    xxiii = tables["kor_1967_table_xxiii_sulphur_melt_values"]["values"]
    assert xxiii["columns"][-1] == "treatment_time_hours"
    assert xxiii["rows"][-1][0] == "8800^2"
    assert xxiii["rows"][-1][-1] == 24

    migrator = F.migrate_one_extract(REPO_ROOT, extract, index=None, aliases={})
    migrator.finalize()
    observations = migrator.result.observations.values()
    table_rows = [obs for obs in observations if "::rows:" in obs.observation_id]
    assert len(table_rows) == 54
    assert all(
        obs.identity.quantity.is_unknown and obs.value.kind is ValueKind.UNAVAILABLE
        for obs in table_rows
    )
    activities = [
        obs
        for obs in observations
        if obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(activities) == 2
    assert all(obs.admission.status is AdmissionStatus.ADMITTED for obs in activities)
    assert migrator.result.validation is not None
    activity_ids = {
        "kor-1967-thesis-sulphur-oxides-slags::"
        "kor_1967_table_xxiv_cas_sat_lime_activities::T=1773:"
        f"{suffix}"
        for suffix in ("h=496a7a098edd", "h=b5bde09ebf68")
    }
    migrated_activity_ids = {obs.observation_id for obs in activities}
    assert migrated_activity_ids == activity_ids
    expected = set()
    assert {
        (issue.path, issue.reason, issue.detail)
        for issue in migrator.result.validation.hard_issues
    } == expected


def test_identity_complete_compilation_cells_are_admitted_at_source_level() -> None:
    migrator = Migrator()
    source = "data/literature/compilations/janaf/tables/O-029.yaml"
    point = F.observation(
        "compiled-point",
        "compiled-work",
        F.o2_identity(),
        "0",
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        admission=AdmissionStatus.PENDING,
    )
    migrator._add_observation(point, source)
    assert (
        migrator.result.observations["compiled-point"].admission.status
        is AdmissionStatus.ADMITTED
    )
    assert (
        migrator.result.observations["compiled-point"].evidence.class_.value
        is EvidenceClass.COMPILATION_ASSESSED
    )
    assert (
        migrator.result.observations["compiled-point"].admission.decided_by.evidence
        == point.locator
    )

    series_identity = replace(
        F.o2_identity(),
        temperature_K=State.unknown("temperature is carried by the series T coordinate"),
    )
    series = replace(
        F.observation(
            "compiled-series",
            "compiled-work",
            series_identity,
            "0",
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            admission=AdmissionStatus.PENDING,
        ),
        value=Value(
            ValueKind.SERIES,
            series=((Decimal("298.15"), Decimal("0")),),
        ),
        point_conditions=None,
    )
    migrator._add_observation(series, source)
    assert (
        migrator.result.observations["compiled-series"].admission.status
        is AdmissionStatus.ADMITTED
    )

    incomplete = replace(
        point,
        observation_id="compiled-incomplete",
        identity=replace(
            point.identity,
            formation_elements=State.unknown("reference phase not printed"),
        ),
    )
    migrator._add_observation(incomplete, source)
    assert (
        migrator.result.observations["compiled-incomplete"].admission.status
        is AdmissionStatus.PENDING
    )

    outside_compilation = replace(point, observation_id="extract-point")
    migrator._add_observation(
        outside_compilation, "data/literature/extracts/fixture.yaml"
    )
    assert (
        migrator.result.observations["extract-point"].admission.status
        is AdmissionStatus.PENDING
    )


def test_module_form_is_not_a_silent_success(tmp_path: Path) -> None:
    """python -m simulator.battery.migrate must actually run the lift.

    The unguarded module used to import and exit 0 with empty stdout, which
    made ``git status`` after a no-op look like a current store.
    """

    root = _write_min_tree(tmp_path)
    module = _run_migrate_cli(
        [
            sys.executable,
            "-m",
            "simulator.battery.migrate",
            "--dry-run",
            "--root",
            str(root),
        ]
    )
    script = _run_migrate_cli(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "battery_migrate.py"),
            "--dry-run",
            "--root",
            str(root),
        ]
    )
    assert "rows_in=" in module.stdout, (
        "module form must run the lift (or fail loud); "
        f"stdout={module.stdout!r} stderr={module.stderr!r} rc={module.returncode}"
    )
    assert module.stdout == script.stdout
    assert module.returncode == script.returncode
    assert not (root / "data" / "literature" / "observations-v2").exists()
    assert not (root / "data" / "literature" / "works").exists()


def test_citation_hash_nfc_and_whitespace() -> None:
    a = "Cafe\u0301  source"
    b = "Caf\u00e9   source"
    assert unicodedata.normalize("NFC", a) != a
    assert citation_hash(a) == citation_hash(b)
    assert citation_hash("exactly this") == hashlib.sha256(
        unicodedata.normalize("NFC", "exactly this").encode("utf-8")
    ).hexdigest()
    # Exact original is not hashed; extra inner space collapses.
    assert citation_hash("a  b") == citation_hash("a b")
    assert citation_hash("a b") != citation_hash("ab")


def test_h12_costa_2015_reviewed_alias_collapses_three_ids() -> None:
    from simulator.battery.migrate import REVIEWED_ALIASES

    sources = [
        (
            "costa-jacobson-2015",
            "Costa, G. C. C. & Jacobson, N. S. (2015), Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry, NASA NTRS 20150002321",
        ),
        (
            "kems-007-costa-2015",
            'Costa, G. C. C. & Jacobson, N. S. (2015), "Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry", NASA NTRS 20150002321',
        ),
        (
            "REF-016",
            "Costa & Jacobson (2015), Vaporization Studies of Olivine via Knudsen Effusion Mass Spectrometry, NASA NTRS 20150002321",
        ),
    ]
    without = {
        work_id_for(citation=c, doi=None, source_id=s, aliases={})[0] for s, c in sources
    }
    assert len(without) == 3
    with_alias = {
        work_id_for(citation=c, doi=None, source_id=s, aliases=REVIEWED_ALIASES)[0]
        for s, c in sources
    }
    assert len(with_alias) == 1
    steurer_85, _ = work_id_for(
        citation="Lunar Oxygen Production by Vapor Phase Pyrolysis",
        doi=None,
        source_id="steurer-1985",
        aliases=REVIEWED_ALIASES,
    )
    steurer_92, _ = work_id_for(
        citation="Vapor Phase Pyrolysis",
        doi=None,
        source_id="steurer-1992",
        aliases=REVIEWED_ALIASES,
    )
    assert steurer_85 != steurer_92


def test_costa_2015_fo93fa7_composition_reaches_migrated_observations(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-007-costa-2015.yaml")
    expected = {
        "MgO": Decimal("0.62"),
        "FeO": Decimal("0.046666666667"),
        "SiO2": Decimal("0.333333333333"),
    }
    identity_rows = {
        "costa_2015_fe_partial_pressure_ir_digitized",
        "costa_2015_fe_partial_pressure_mo_digitized",
        "costa_2015_fe_vaporization_coefficient_alpha_digitized",
        "costa_2015_mg_partial_pressure_ir_digitized",
        "costa_2015_mg_partial_pressure_mo_digitized",
    }
    mapped = [
        observation
        for observation in result.observations.values()
        if any(f"::{row_id}::" in observation.observation_id for row_id in identity_rows)
    ]
    assert len(mapped) == 23
    for observation in mapped:
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.basis == "normalized_mole_fraction_from_published_Fo93Fa7"
        assert composition.value.amount_basis.value == "mole_fraction"
        assert dict(composition.value.components) == expected
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.G
        assert observation.identity.temperature_K.is_value
        conditions = observation.point_conditions
        assert conditions is not None
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == observation.identity.temperature_K.value
        assert temperature.inference is None
        located_composition = conditions["composition"]
        assert located_composition.state.is_value
        assert located_composition.state.value == composition.value
        assert located_composition.locator.page == 1
        assert located_composition.locator.pdf_page_index == 0

    first_digitized_point = next(
        observation
        for observation in mapped
        if observation.identity.temperature_K.value == Decimal("2014.83")
    )
    assert first_digitized_point.identity.temperature_K.value == Decimal("2014.83")
    assert first_digitized_point.point_conditions["temperature_K"].state.value == Decimal(
        "2014.83"
    )

    measured_points = [
        observation
        for observation in result.observations.values()
        if observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
    ]
    assert len(measured_points) == 109
    assert all(
        observation.point_conditions is not None
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].state.value
        == observation.identity.temperature_K.value
        for observation in measured_points
    )

    comparator_rows = [
        observation
        for observation in result.observations.values()
        if "costa_2015_fe_partial_pressure_re_piacente_comparator" in observation.observation_id
        or "costa_2015_mg_partial_pressure_re_piacente_comparator" in observation.observation_id
    ]
    assert len(comparator_rows) == 30
    assert all(
        observation.identity.composition is not None
        and observation.identity.composition.is_unknown
        and observation.identity.composition.reason == "no composition mapped from source"
        for observation in comparator_rows
    )

    closed_cell_pressure_rows = [
        observation
        for observation in result.observations.values()
        if any(
            f"::{row_id}::" in observation.observation_id
            for row_id in {
                "costa_2015_fe_partial_pressure_ir_digitized",
                "costa_2015_fe_partial_pressure_mo_digitized",
                "costa_2015_mg_partial_pressure_ir_digitized",
                "costa_2015_mg_partial_pressure_mo_digitized",
            }
        )
    ]
    assert len(closed_cell_pressure_rows) == 18
    assert all(observation.identity.fO2_Pa.is_unknown for observation in closed_cell_pressure_rows)
    assert all(
        observation.identity.total_pressure_Pa.is_unknown
        and observation.identity.total_pressure_Pa.reason == "in_cell_total_pressure_not_derivable"
        for observation in closed_cell_pressure_rows
    )

    ion_current_rows = [
        observation
        for observation in result.observations.values()
        if any(
            f"::{row_id}::" in observation.observation_id
            for row_id in {
                "costa_2015_fe_time_equilibrium_mo_1669",
                "costa_2015_fe_time_equilibrium_ir_1851",
                "costa_2015_fe_siO_ion_intensity_ratio_digitized",
                "costa_2015_mg_time_equilibrium_mo_1669",
                "costa_2015_mg_time_equilibrium_ir_1851",
                "costa_2015_mg_siO_ion_intensity_ratio_digitized",
            }
        )
    ]
    assert len(ion_current_rows) == 56
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in ion_current_rows
    )

    placeholders = {
        "costa_2015_fe_olivine_kems_alpha_multicell",
        "costa_2015_fe_olivine_class_and_motzfeldt_absence_b1",
        "costa_2015_sio_olivine_kems_alpha_multicell",
        "costa_2015_sio_olivine_class_and_motzfeldt_absence_b1",
    }
    for row_id in placeholders:
        observation = next(
            item
            for item in result.observations.values()
            if item.observation_id.endswith(f"::{row_id}")
        )
        assert quantity_token(observation.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert observation.value.unavailable_reason == "figure_not_digitized"
        assert observation.value.point is None
        assert observation.value.series is None
        assert observation.admission.status is AdmissionStatus.REJECTED
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.G
        assert observation.identity.temperature_K.is_unknown
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        assert dict(observation.identity.composition.value.components) == expected


def test_doi_canonicalisation_and_alias_registry() -> None:
    assert canonicalize_doi("https://doi.org/10.1063/1.555991") == "10.1063/1.555991"
    assert canonicalize_doi("DOI: 10.1063/1.555991") == "10.1063/1.555991"
    assert canonicalize_doi("10.1063/1.555991") == "10.1063/1.555991"
    aliases = {"old-id": "10.1234/x", "10.1234/x": "10.1234/x"}
    work_id, doi = work_id_for(
        citation="Anything",
        doi="https://dx.doi.org/10.1234/X",
        source_id="old-id",
        aliases=aliases,
    )
    assert work_id == "10.1234/x"
    assert doi == "10.1234/x"
    hashed, missing = work_id_for(
        citation="No DOI here",
        doi=None,
        source_id="other",
        aliases={},
    )
    assert missing is None
    assert hashed == citation_hash("No DOI here")


def test_unknown_rail_spelling_raises() -> None:
    from simulator.battery.migrate import RAIL_MAP

    assert canonicalize_rail("melt activities") is Rail.MELT_ACTIVITY
    assert canonicalize_rail("SiO_evolution") is Rail.SIO_EVOLUTION
    assert canonicalize_rail("integrated_bench") is Rail.PYROLYSIS_YIELD
    assert RAIL_MAP.get("vapour") is Rail.VAPOUR
    assert canonicalize_rail("vapour") is Rail.VAPOUR
    with pytest.raises(UnknownRailSpellingError):
        canonicalize_rail("gibbs_thermochemistry")


def test_j05_vapour_spec_spelling_is_in_rail_map() -> None:
    from simulator.battery.migrate import RAIL_MAP

    assert "vapour" in RAIL_MAP
    assert RAIL_MAP["vapour"] is Rail.VAPOUR
    assert canonicalize_rail("vapour") is Rail.VAPOUR


def test_h11_missing_extraction_date_is_unspecified(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["extraction"].pop("date", None)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    decided = obs.admission.decided_by
    assert decided is not None
    assert decided.date != "1970-01-01"
    assert decided.date in {"unspecified", "unknown"}


def test_h10_migrate_has_no_validation_bypass() -> None:
    import inspect

    assert "validate" not in inspect.signature(migrate).parameters
    assert "validate" not in inspect.signature(Migrator.run).parameters
    assert "validate" not in inspect.signature(Migrator.finalize).parameters


def test_h06_ledger_unknown_rail_spelling_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "gibbs-rail-probe",
                        "source_id": "janaf",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 298.15,
                        "table_kJ_mol": 0.0,
                        "rail": "gibbs_thermochemistry",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_g13_unknown_rail_spelling_raises_during_migrate(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["rail"] = "gibbs_thermochemistry"
    root = _write_min_tree(tmp_path, extract)
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def _write_janaf_table_compilation(
    root: Path,
    *,
    doc_rail: str | None = None,
    table_rail: str | None = None,
    row_rail: str | None = None,
) -> Path:
    dest = root / "data" / "literature" / "compilations" / "janaf-fixture"
    dest.mkdir(parents=True, exist_ok=True)
    row: dict = {
        "temperature": {"value": 298.15},
        "formation_gibbs_energy": {"value": 0.0},
        "log10_formation_equilibrium_constant": {"value": 0.0},
    }
    if row_rail is not None:
        row["rail"] = row_rail
    table: dict = {
        "table_id": "Na-001",
        "index_entry": {"formula": "Na", "state": "g"},
        "standard_state_as_published": "p° = 0.1 MPa",
        "values": [row],
    }
    if table_rail is not None:
        table["rail"] = table_rail
    doc: dict = {
        "schema_version": "literature_compilation.v1",
        "source_id": "janaf-fixture",
        "source": {"citation": "JANAF fixture"},
        "table": table,
    }
    if doc_rail is not None:
        doc["rail"] = doc_rail
    (dest / "Na-001.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    return root


def test_j03_compilation_document_unknown_rail_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, doc_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j03_compilation_table_unknown_rail_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, table_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j03_compilation_nested_row_unknown_rail_still_raises(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_janaf_table_compilation(root, row_rail="gibbs_thermochemistry")
    with pytest.raises(UnknownRailSpellingError):
        migrate(root, write=False)


def test_j04_named_source_fallthrough_includes_unmapped_token(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "named-source-fallthrough",
                        "source_id": "janaf",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 298.15,
                        "table_kJ_mol": 0.0,
                        "method_class": "independent_tabulation",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["named-source-fallthrough"]
    from simulator.battery.enums import EvidenceClass

    assert obs.evidence.class_.is_value
    assert obs.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    assert obs.evidence.original_method_class == "independent_tabulation"


def test_series_explosion_keeps_conversion_trail(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    values = sorted(p.value.point for p in points)
    assert values[0] == atm_to_pa("1")
    assert values[1] == atm_to_pa("2")
    assert all(
        p.derivation is not None and "atm_to_Pa" in p.derivation.relation
        for p in points
    )
    assert all(p.derivation.output_unit == "Pa" for p in points)


def test_series_explosion_keeps_parent_confidence_level(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["uncertainty"] = {
        "note": "Table I standard deviation",
        "confidence_level": "one_sigma",
    }
    for point in row["values"]["series"]:
        point["sigma"] = 0.1

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    points = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.startswith("fixture-source::na_psat")
    ]

    assert len(points) == 2
    assert all(
        observation.uncertainty.verbatim["confidence_level"] == "one_sigma"
        for observation in points
    )


@pytest.mark.parametrize("phase", ["", "not_a_phase", "condensed_solid"])
def test_pressure_phase_provenance_matches_resulting_phase(
    tmp_path: Path, phase: str
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["phase"] = phase
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.startswith("fixture-source::na_psat")
    ]

    assert len(points) == 2
    assert all(
        "species.phase=gas"
        not in (point.derivation.relation if point.derivation else "")
        for point in points
    )
    if phase == "condensed_solid":
        assert all(point.identity.species.phase.value is Phase.CR for point in points)
    else:
        assert all(point.identity.species.phase.is_unknown for point in points)


def test_series_row_point_conditions_preserve_pressure_interval(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"]["series"][0][
        "point_conditions"
    ] = {
        "total_pressure_Pa": {
            "state": {
                "tag": "value",
                "value": {
                    "kind": "interval",
                    "interval_low": "10",
                    "interval_high": "20",
                },
            }
        }
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    point = next(
        observation
        for observation in result.observations.values()
        if observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == as_decimal("1200")
    )
    pressure = point.point_conditions["total_pressure_Pa"]
    assert pressure.state.is_value
    assert pressure.state.value.kind is ValueKind.INTERVAL
    assert pressure.state.value.interval_low == as_decimal("10")
    assert pressure.state.value.interval_high == as_decimal("20")


def test_missing_admission_status_defaults_with_notice_even_when_class_unknown(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"].pop("admission_status")
    extract["species"]["Na"]["observations"][0]["values"].pop("method_class")
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.admission.status is AdmissionStatus.ADMITTED
    admission_notice = next(
        notice
        for notice in obs.notices
        if notice.kind is NoticeKind.ADMISSION_DEFAULTED
    )
    assert "not a reviewer decision" in admission_notice.reason
    assert admission_notice.origin == obs.observation_id
    assert obs.evidence.class_.tag is StateTag.UNKNOWN
    # Phase was stated as gas — that is a lift, not a default.
    assert obs.identity.species.phase.is_value
    assert obs.identity.species.phase.value is Phase.G


def test_to_plain_state_serialization_omits_absent_keys() -> None:
    from simulator.battery.records import State

    unknown_bytes = json.dumps(
        to_plain(State.unknown("not recorded")),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    value_bytes = json.dumps(
        to_plain(State.of("fixture")),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert unknown_bytes == b'{"reason":"not recorded","tag":"unknown"}'
    assert value_bytes == b'{"tag":"value","value":"fixture"}'


def test_polymorph_rejection_kind_round_trips_and_loads_legacy_states() -> None:
    from simulator.battery.migrate import _species_from_plain
    from simulator.battery.records import Species, State

    rejected = Species(
        "MgO",
        State.of(Phase.CR),
        State.unknown(
            "unrecognised polymorph 'not-a-form' is not a closed Polymorph token",
            rejection_kind=PolymorphRejectionKind.UNRECOGNISED,
        ),
    )
    payload = to_plain(rejected)
    assert payload["polymorph"]["rejection_kind"] == "unrecognised"
    assert _species_from_plain(payload) == rejected

    for reason, expected in (
        ("source does not state polymorph", PolymorphRejectionKind.OMITTED),
        (
            "unrecognised polymorph 'not-a-form' is not a closed Polymorph token",
            PolymorphRejectionKind.UNRECOGNISED,
        ),
        (
            "polymorph clinoenstatite does not denote formula MgO",
            PolymorphRejectionKind.CONTRADICTS_FORMULA,
        ),
    ):
        legacy = {
            "formula": "MgO",
            "phase": {"tag": "value", "value": "cr"},
            "polymorph": {"tag": "unknown", "reason": reason},
        }
        assert _species_from_plain(legacy).polymorph.rejection_kind is expected


@pytest.mark.parametrize(
    ("value_payload", "expected_status"),
    [
        ({"kind": "point", "point": "0"}, AdmissionStatus.ADMITTED),
        ({"kind": "series", "series": [["1", "0"]]}, AdmissionStatus.ADMITTED),
        ({"kind": "bound", "bound_operator": "<", "bound_value": "0"}, AdmissionStatus.ADMITTED),
        ({"kind": "interval", "interval_low": "0", "interval_high": "1"}, AdmissionStatus.ADMITTED),
        ({"kind": "relative_series", "relative_series": [["1", "0"]], "relative_normalization": "initial"}, AdmissionStatus.ADMITTED),
        ({"kind": "ordering", "ordering": ["a", "b"]}, AdmissionStatus.PENDING),
        ({"kind": "categorical", "categorical": "present"}, AdmissionStatus.PENDING),
        ({"kind": "expression", "expression_text": "x"}, AdmissionStatus.PENDING),
        ({"kind": "unavailable", "unavailable_reason": "not reported"}, AdmissionStatus.PENDING),
    ],
)
def test_numeric_value_kind_plain_admission_pin(
    tmp_path: Path,
    value_payload: dict[str, object],
    expected_status: AdmissionStatus,
) -> None:
    observation = next(iter(migrate(_write_min_tree(tmp_path), write=False).observations.values()))
    payload = to_plain(observation)
    payload["value"] = value_payload
    payload["admission"] = {"status": "pending"}

    decoded = observation_from_plain(payload)

    assert decoded.value.kind.value == value_payload["kind"]
    assert to_plain(decoded)["value"] == {**value_payload, "approximate": False}
    assert decoded.admission.status is expected_status
    assert any(notice.kind is NoticeKind.ADMISSION_DEFAULTED for notice in decoded.notices) is (
        expected_status is AdmissionStatus.ADMITTED
    )


def test_missing_admission_status_keeps_unmeasured_row_pending(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    values = extract["species"]["Na"]["observations"][0]["values"]
    values.pop("admission_status")
    values.pop("series")
    values["method_class"] = "figure_only"
    root = _write_min_tree(tmp_path, extract)

    observation = next(iter(migrate(root, write=False).observations.values()))

    assert observation.value.kind is ValueKind.UNAVAILABLE
    assert observation.admission.status is AdmissionStatus.PENDING
    assert not any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED
        for notice in observation.notices
    )


@pytest.mark.parametrize(
    ("source_status", "expected_status", "defaulted"),
    [
        ("pending", AdmissionStatus.ADMITTED, True),
        ("pending_validation", AdmissionStatus.ADMITTED, True),
        ("figure_only", AdmissionStatus.PENDING, False),
        ("model_output_not_measurement", AdmissionStatus.PENDING, False),
        (
            "rejected_model_output_not_measurement",
            AdmissionStatus.REJECTED,
            False,
        ),
        ("admitted", AdmissionStatus.ADMITTED, False),
    ],
)
def test_source_admission_default_scope(
    tmp_path: Path,
    source_status: str,
    expected_status: AdmissionStatus,
    defaulted: bool,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"][
        "admission_status"
    ] = source_status
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))

    assert obs.admission.status is expected_status
    assert any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED for notice in obs.notices
    ) is defaulted


def test_plain_pending_without_source_decision_defaults_and_adds_notice(
    tmp_path: Path,
) -> None:
    from simulator.battery.migrate import observation_from_plain

    result = migrate(_write_min_tree(tmp_path), write=False)
    observation = next(iter(result.observations.values()))
    payload = to_plain(observation)
    payload["admission"] = {"reason": "no observation admission_status mapped from source"}
    payload["notices"] = []

    decoded = observation_from_plain(payload)

    assert decoded.admission.status is AdmissionStatus.ADMITTED
    assert any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED for notice in decoded.notices
    )


def test_h07_write_outputs_prunes_stale_work_files(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works_dir = root / "data" / "literature" / "works"
    stale = works_dir / "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef.yaml"
    stale.write_text(
        "schema_version: battery_work.v2.1\nwork:\n  citation: stale\n  source_ids: [stale]\n",
        encoding="utf-8",
    )
    aliases_path = works_dir / "ALIASES.yaml"
    doc = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
    doc["aliases"]["stale-source"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    aliases_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    migrate(root, write=True)
    assert not stale.is_file()
    aliases = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))["aliases"]
    assert "stale-source" not in aliases


def test_row_conservation_and_idempotency(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    first = migrate(root, write=True)
    extract_rows = 1
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].rows_in == extract_rows
    assert first.source_counts["data/literature/extracts/fixture-source.yaml"].observations_out >= extract_rows
    first_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    second = migrate(root, write=True)
    second_bytes = {
        p.relative_to(root): p.read_bytes()
        for p in (root / "data").rglob("*")
        if p.is_file() and "extracts/fixture-source.yaml" not in p.as_posix()
    }
    assert first_bytes.keys() == second_bytes.keys()
    for key in first_bytes:
        assert first_bytes[key] == second_bytes[key], key
    original = (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes()
    migrate(root, write=True)
    assert (root / "data" / "literature" / "extracts" / "fixture-source.yaml").read_bytes() == original
    assert (root / "data" / "literature" / "works" / "ALIASES.yaml").is_file()


def test_merge_located_mapping_order_is_hash_seed_stable() -> None:
    """b-549: set(old) | set(new) leaked PYTHONHASHSEED-dependent key order
    into serialized output (dump_yaml writes with sort_keys=False). The merge
    must be insertion-ordered: existing keys keep position, new keys append.
    Subprocesses are required — the hash seed is fixed at interpreter start.
    """

    probe = (
        "from simulator.battery.migrate import _merge_located_mapping\n"
        "from simulator.battery.records import Located, State\n"
        "old = {k: Located(State.of(k)) for k in ('alpha', 'beta', 'gamma')}\n"
        "new = {k: Located(State.of(k)) for k in ('delta', 'epsilon', 'zeta')}\n"
        "print(','.join(_merge_located_mapping(old, new).keys()))\n"
    )
    orders = set()
    for seed in ("0", "1", "2", "42"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(REPO_ROOT)
        out = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()
        orders.add(out)
    assert orders == {"alpha,beta,gamma,delta,epsilon,zeta"}


def test_lineage_parents_from_source_never_invents_pointer() -> None:
    """b-549: bare prose in derived_from must not get a source prefix slapped
    on — it can never resolve by construction. It is returned as prose so the
    caller can route it to the queue instead of minting a dangling pointer.
    """

    parents, prose = lineage_parents_from_source(
        {"derived_from": "author geometric calculation from assumed cube dimensions"},
        {},
        "src",
        set(),
    )
    assert parents == ()
    assert prose == ("author geometric calculation from assumed cube dimensions",)

    parents, prose = lineage_parents_from_source(
        {"derived_from": ["local_a", "other::obs", "prose note", "src::local_b"]},
        {},
        "src",
        {"local_a", "local_b"},
    )
    assert parents == ("src::local_a", "other::obs", "src::local_b")
    assert prose == ("prose note",)

    parents, prose = lineage_parents_from_source(
        {"derived_from": ["tables:src", "tables:src/t2.csv"]},
        {},
        "src",
        set(),
    )
    assert parents == ("tables:src", "tables:src/t2.csv")
    assert prose == ()

    parents, prose = lineage_parents_from_source(
        {"derived_from": ["tables:src/t2.csv"]},
        {},
        "src",
        set(),
        asset_ids={"tables:src/t2.csv"},
    )
    assert parents == ("tables:src/t2.csv",)
    assert prose == ()

    assert lineage_parents_from_source({}, {}, "src", {"local_a"}) == ((), ())


def test_prose_derived_from_is_queued_never_pointed(tmp_path: Path) -> None:
    """b-549 end-to-end: a prose derived_from on a derived observation yields
    no pointer on the record, no referential_integrity hard issue, and a queue
    entry carrying the prose plus locator (the text is never silently dropped).
    """

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    values = extract["species"]["Na"]["observations"][0]["values"]
    values["method_class"] = "model_derived"
    values["derived_from"] = "author estimate from assumed grain population"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    assert result.observations
    for obs in result.observations.values():
        assert not obs.derived_from
    queued = [
        e
        for e in result.queue
        if "author estimate from assumed grain population" in e.why
    ]
    assert len(queued) == 1
    # _resolve_queue_ids re-points entries to the realised observation id.
    assert queued[0].observation_id.startswith("fixture-source::na_psat")
    assert queued[0].axes == ["derived_from"]
    assert queued[0].locator  # locator rides along — the text keeps its address
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    reasons = {(i.path.rsplit(".", 1)[-1], i.reason.value) for i in report.hard_issues}
    assert ("derived_from", "referential_integrity") not in reasons
    # Unstated ancestry is still a hard conditional_field — surfaced, not hidden.
    assert ("derived_from", "conditional_field") in reasons


def test_validate_corpus_zero_hard_issues_on_fixture(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=True)
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()
    assert result.validation is not None
    assert result.validation.hard_issues == ()


def test_validate_corpus_zero_hard_issues_on_migrated_store() -> None:
    report_path = REPO_ROOT / "data" / "battery" / "migration-report.md"
    works_dir = REPO_ROOT / "data" / "literature" / "works"
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not report_path.is_file() or not any(works_dir.glob("*.yaml")):
        pytest.skip("migrated store not generated yet")
    works, experiments, observations = load_migrated_store(REPO_ROOT)
    assert works
    assert observations
    figure_only = observations[
        "1997jonesthermo::jones_1997_imcc_mgo_activity_comparison"
    ]
    assert figure_only.admission.status is AdmissionStatus.PENDING
    assert not any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED
        for notice in figure_only.notices
    )
    report = validate_corpus(works, experiments, observations, residuals=None)
    # J02 restored C(derived) derived_from+derivation. Unstated ancestry is a
    # hard conditional_field, not a silent pass. The same holds for a row that
    # declares quoted_attributed but states no attribution: the canonical
    # token now maps to its class, so the missing field is reported instead of
    # the row falling to unknown. Other reasons must stay zero.
    lineage_fields = (".derived_from", ".derivation", ".attribution")
    for issue in report.hard_issues:
        assert issue.reason.value == "conditional_field", issue
        assert issue.path.endswith(lineage_fields), issue
    assert extracts_v2.is_dir() or obs_dir.is_dir()


def test_h05_corrupted_extracts_v2_yaml_fails_store_load(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    report = validate_corpus(works, experiments, observations, residuals=None)
    assert report.hard_issues == ()
    dest = next((root / "data" / "literature" / "extracts-v2").glob("*.yaml"))
    dest.write_text("invalid: [yaml\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_migrated_store(root)


def _phase_state(obs):
    return obs.identity.species.phase


@pytest.mark.parametrize(
    ("spelling", "printed_name", "expected"),
    (
        (" CLINOENSTATITE ", "clinoenstatite", Polymorph.CLINOENSTATITE),
        ("  Diopside  ", "Diopside", Polymorph.DIOPSIDE),
        ("High   albite", "High albite", Polymorph.ALBITE_HIGH),
        ("HIGH SANIDINE", "High sanidine", Polymorph.SANIDINE_HIGH),
    ),
)
def test_extract_named_polymorphs_are_formula_scoped(
    spelling: str, printed_name: str, expected: Polymorph
) -> None:
    from simulator.battery.migrate import polymorph_from_extract

    source_path = (
        REPO_ROOT
        / "data/literature/extracts/richet-bottinga-1986-silicate-thermochemistry.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    matching_formulas = {
        formula
        for formula, body in source["species"].items()
        for row in body.get("observations", ())
        if row.get("condensed_form", {}).get("polymorph") == printed_name
    }
    assert len(matching_formulas) == 1
    formula = matching_formulas.pop()
    unrelated_formula = next(
        candidate for candidate in source["species"] if candidate != formula
    )

    observation = {"condensed_form": {"polymorph": spelling}}
    resolved = polymorph_from_extract(observation, formula)
    assert resolved is not None and resolved.is_value
    assert resolved.value is expected

    typed = polymorph_from_extract(
        {"condensed_form": {"polymorph": {"tag": "value", "value": spelling}}},
        formula,
    )
    assert typed is not None and typed.is_value
    assert typed.value is expected

    unrelated = polymorph_from_extract(observation, unrelated_formula)
    assert unrelated is not None and unrelated.is_unknown
    assert unrelated_formula in (unrelated.reason or "")


def test_unqualified_cristobalite_is_not_assigned_a_structural_state() -> None:
    from simulator.battery.migrate import make_species, polymorph_from_extract

    raw = polymorph_from_extract(
        {"condensed_form": {"polymorph": "Cristobalite"}}, "SiO2"
    )
    assert raw is not None and raw.is_value
    assert raw.value == "Cristobalite"
    species = make_species("SiO2", Phase.CR, polymorph=raw)
    assert species.polymorph is not None and species.polymorph.is_unknown


def test_unrecognised_polymorph_is_counted_and_queued(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    observations = extract["species"]["Na"]["observations"]
    observations[0]["phase"] = "condensed_solid"
    observations[0]["condensed_form"] = {"polymorph": "not-a-real-form"}
    observations[0]["values"]["series"] = [{"T_K": 1200.0, "pressure_atm": 1.0}]
    observations.append(
        {
            "observation_id": "na_kyanite",
            "type": "psat_series",
            "locator": {"table": "I", "page": 3},
            "phase": "condensed_solid",
            "condensed_form": {"polymorph": "Kyanite"},
            "regime": "knudsen_effusion",
            "units": "atm",
            "values": {
                "quantity": "pure_Psat",
                "method_class": "measured_direct",
                "admission_status": "admitted",
                "series": [{"T_K": 1400.0, "pressure_atm": 3.0}],
            },
        }
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    unknown_rows = [
        o
        for o in result.observations.values()
        if "na_psat" in o.observation_id
    ]
    known_rows = [
        o
        for o in result.observations.values()
        if "na_kyanite" in o.observation_id
    ]
    assert unknown_rows
    assert known_rows
    for row in unknown_rows:
        assert row.identity.species.polymorph.is_unknown
        assert "not-a-real-form" in (row.identity.species.polymorph.reason or "")
    for row in known_rows:
        assert row.identity.species.polymorph.is_value
        assert row.identity.species.polymorph.value is Polymorph.KYANITE
    assert result.unrecognised_polymorphs == {"not-a-real-form": len(unknown_rows)}
    queued = [
        e
        for e in result.queue
        if e.observation_id is not None
        and "na_psat" in e.observation_id
        and "species.polymorph" in (e.axes or ())
    ]
    assert len(queued) == len(unknown_rows)
    assert all("not-a-real-form" in e.why for e in queued)
    report = (root / "data" / "battery" / "migration-report.md").read_text(encoding="utf-8")
    assert "## Unrecognised polymorph tokens" in report
    assert f"degradations: {len(unknown_rows)}" in report
    assert "`not-a-real-form`" in report
    known_queued = [
        e
        for e in result.queue
        if e.observation_id is not None
        and "na_kyanite" in e.observation_id
        and "species.polymorph" in (e.axes or ())
    ]
    assert known_queued == []


def test_g01_blank_phase_is_unknown_not_gas(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = ""
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    phase = _phase_state(obs)
    assert phase.is_unknown, phase
    assert phase.value is None
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()


def test_g01_unmapped_and_sidecar_phases_are_unknown(tmp_path: Path) -> None:
    """Grok P0-1 table: unmapped/missing source phases must not become g."""

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    rows = extract["species"]["Na"]["observations"]
    base = rows[0]
    mislifts = [
        ("solid_arsenolite", {"condensed_form": {"polymorph": "arsenolite"}}),
        ("silicate_melt", {}),
        ("liquid_Fe_Mn_alloy", {}),
        ("solid_metal", {}),
        ("liquid_H2O_to_H2O_g", {}),
        ("solid_O2_to_O2_g", {}),
        ("", {}),
        ("steelmaking_silicate_slag", {}),
        ("graphite", {}),
        ("ref", {}),
        ("cr,l", {}),
    ]
    rows.clear()
    for i, (phase, extra) in enumerate(mislifts):
        row = yaml.safe_load(yaml.safe_dump(base))
        row["observation_id"] = f"mislift_{i}"
        row["phase"] = phase
        row.update(extra)
        rows.append(row)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)

    def by_prefix(suffix: str):
        matches = [o for o in result.observations.values() if suffix in o.observation_id]
        assert matches, sorted(result.observations)
        return matches[0]

    arsenolite = by_prefix("mislift_0")
    assert arsenolite.identity.species.phase.is_value
    assert arsenolite.identity.species.phase.value is Phase.CR
    assert arsenolite.identity.species.polymorph is not None
    assert arsenolite.identity.species.polymorph.is_value
    assert arsenolite.identity.species.polymorph.value == "arsenolite"
    for i, (phase, _extra) in enumerate(mislifts[1:], start=1):
        obs = by_prefix(f"mislift_{i}")
        stored = _phase_state(obs)
        assert stored.is_unknown, (phase, stored)
        assert stored.value is not Phase.G
        assert stored.value is not Phase.CR


def test_g01_sidecar_without_phase_is_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    lit = root / "data" / "literature"
    (lit / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {
                    "kems-src": {
                        "citation": "KEMS sidecar fixture",
                        "doi": "10.1234/KEMS",
                    }
                },
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "kems_no_phase",
                                "species": "Ca",
                                "coordinate": {"temperature_K": 2000.0},
                                "partial_pressure_pa": 1.0,
                                "source_locator": {"figure": 1},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    kems = result.observations["kems_no_phase"]
    assert kems.identity.species.phase.is_unknown


def test_effusion_pressure_reservoir_requires_stated_phase_and_pressure() -> None:
    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition, Located, Sample

    composition = Composition(
        basis="mole_fraction",
        components=(("Fe", Decimal("1")),),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    mixed_composition = Composition(
        basis="mole_fraction",
        components=(("Fe", Decimal("0.5")), ("Ni", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    mismatched_composition = Composition(
        basis="mole_fraction",
        components=(("Ni", Decimal("1")),),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    cases = (
        ("condensed_solid", composition, Quantity.P_SAT, Phase.CR),
        (None, composition, Quantity.P_SAT, None),
        ("condensed_solid", composition, Quantity.EVAPORATION_RATE, None),
        ("condensed_solid", mixed_composition, Quantity.P_SAT, None),
        ("condensed_solid", mismatched_composition, Quantity.P_SAT, None),
        (
            "condensed_solid",
            Located(State.unknown("purity not reported")),
            Quantity.P_SAT,
            None,
        ),
    )
    for index, (form, initial_composition, quantity, expected_phase) in enumerate(cases):
        experiment = F.kems_experiment(experiment_id=f"effusion-{index}")
        experiment = replace(
            experiment,
            sample=Sample(
                form=F.located(form) if form is not None else None,
                initial_composition=(
                    initial_composition
                    if isinstance(initial_composition, Located)
                    else F.located(initial_composition)
                ),
            ),
        )
        migrator = Migrator()
        migrator.result.experiments[experiment.experiment_id] = experiment
        missing_reservoir = State.unknown("source condensed reservoir not grounded")
        inferred, _ = migrator._experiment_identity_fields(
            experiment.experiment_id,
            {"reservoir": missing_reservoir},
            method=experiment.method,
            quantity=quantity,
            species_formula="Fe",
        )

        identity = replace(
            F.psat_identity("Fe"),
            reservoir=missing_reservoir,
        )
        if "reservoir" in inferred:
            identity = replace(identity, reservoir=inferred["reservoir"])
        if expected_phase is None:
            assert identity.reservoir.is_unknown
        else:
            assert identity.reservoir.is_value
            assert identity.reservoir.value.phase.is_value
            assert identity.reservoir.value.phase.value is expected_phase


def _ferguson_effusion_extract(points):
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Ferguson et al. (2004), iron vapor pressure",
            "doi": "10.1234/FIXTURE",
        },
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "ferguson_2004_table1_iron_vapor_pressure",
                        "type": "psat_series",
                        "regime": "knudsen_effusion",
                        "units": "Pa",
                        "locator": {"table": "1", "page": 3},
                        "values": {
                            "quantity": "vapor_pressure",
                            "method_class": "measured_tabulated",
                            "gas_species": "Fe(g)",
                            "phase_assignment_note": (
                                "phase_as_published is inferred from each row's "
                                "temperature and the accepted iron melting point."
                            ),
                            "points": points,
                        },
                    }
                ]
            }
        },
    }


def _typed_point_composition(components):
    return {
        "state": {
            "tag": "value",
            "value": {
                "basis": "mole_fraction",
                "components": [[element, fraction] for element, fraction in components],
                "amount_basis": "mole_fraction",
            },
        }
    }


@pytest.mark.parametrize(
    ("starting_composition", "expected_phase"),
    (
        (_typed_point_composition((("Fe", "1"),)), Phase.L),
        (
            {"state": {"tag": "unknown", "reason": "purity not reported"}},
            None,
        ),
        (_typed_point_composition((("Fe", "0.5"), ("Ni", "0.5"))), None),
        (
            {
                "state": {
                    "tag": "value",
                    "value": {
                        "kind": "categorical",
                        "categorical": "99.99% purity iron",
                    },
                }
            },
            None,
        ),
        (_typed_point_composition((("Ni", "1"),)), None),
    ),
)
def test_effusion_point_reservoir_requires_typed_matching_composition(
    tmp_path: Path, starting_composition, expected_phase
) -> None:
    point = {
        "T_C": 1600,
        "P_Pa": 0.1,
        "phase_as_published": "Fe(l)",
        "locator": {"table": "1", "row": 1, "page": 3},
        "point_conditions": {"starting_composition": starting_composition},
    }
    root = _write_min_tree(tmp_path, _ferguson_effusion_extract([point]))
    result = migrate(root, write=False)
    row = next(
        observation
        for observation in result.observations.values()
        if "ferguson_2004_table1_iron_vapor_pressure"
        in observation.observation_id
    )

    if expected_phase is None:
        assert row.identity.reservoir.is_unknown
    else:
        assert row.identity.reservoir.is_value
        assert row.identity.reservoir.value.formula == "Fe"
        assert row.identity.reservoir.value.phase.is_value
        assert row.identity.reservoir.value.phase.value is expected_phase
    point_starting_composition = row.point_conditions["starting_composition"]
    if starting_composition["state"]["tag"] == "unknown":
        assert point_starting_composition.state.is_unknown
        assert point_starting_composition.state.reason == "purity not reported"
    elif starting_composition["state"]["value"].get("kind") == "categorical":
        assert point_starting_composition.state.is_value
        assert (
            point_starting_composition.state.value.categorical
            == "99.99% purity iron"
        )


def test_ferguson_effusion_pressure_migrates_point_phase_reservoirs(
    tmp_path: Path,
) -> None:
    phase_labels = ["Fe(s)"] * 18 + ["Fe(l)"] * 26 + ["Fe(s)/Fe(l)"]
    points = []
    expected_temperatures = []
    for index, phase_as_published in enumerate(phase_labels):
        temperature_C = (
            Decimal("1300") + index
            if index < 18
            else Decimal("1550") + index - 18
            if index < 44
            else Decimal("1537.85")
        )
        expected_temperatures.append(temperature_C + Decimal("273.15"))
        points.append(
            {
                "T_C": float(temperature_C),
                "P_Pa": float(Decimal("0.1") + Decimal(index) / Decimal("100")),
                "phase_as_published": phase_as_published,
                "locator": {"table": "1", "row": index + 1, "page": 3},
                "point_conditions": {
                    "starting_composition": {
                        "state": {
                            "tag": "value",
                            "value": {
                                "kind": "categorical",
                                "categorical": "99.97% purity iron",
                            },
                        },
                        "locator": {"page": 2, "section": "Experimental Procedure"},
                    }
                },
            }
        )

    extract = _ferguson_effusion_extract(points)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    rows = [
        observation
        for observation in result.observations.values()
        if "ferguson_2004_table1_iron_vapor_pressure"
        in observation.observation_id
    ]

    assert len(rows) == 45
    assert all(row.identity.species.phase.is_value for row in rows)
    assert all(row.identity.species.phase.value is Phase.G for row in rows)
    assert all(row.identity.reservoir.is_unknown for row in rows)
    assert all(
        identity_equal(row.identity, row.identity).kind
        is IdentityEqualKind.IDENTITY_UNKNOWN
        for row in rows
    )
    for index, row in enumerate(rows):
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == expected_temperatures[index]
        starting_composition = (row.point_conditions or {}).get(
            "starting_composition"
        )
        assert starting_composition is not None
        assert starting_composition.state.is_value
        assert starting_composition.state.value.categorical == (
            "99.97% purity iron"
        )
        assert starting_composition.locator is not None


def test_ferguson_unknown_composition_never_builds_pure_engine_pot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery.enums import CellMaterial
    from tests.battery.test_silent_fills import (
        _capture_cell,
        _cell_material_bench,
    )

    point = {
        "T_C": 1600,
        "P_Pa": 0.1,
        "phase_as_published": "Fe(l)",
        "locator": {"table": "1", "row": 1, "page": 3},
        "point_conditions": {
            "starting_composition": {
                "state": {"tag": "unknown", "reason": "purity not reported"},
                "locator": {"page": 2, "section": "Experimental Procedure"},
            }
        },
    }
    result = migrate(
        _write_min_tree(tmp_path, _ferguson_effusion_extract([point])),
        write=False,
    )
    observation = next(iter(result.observations.values()))
    seen = _capture_cell(monkeypatch)
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        observation,
        experiment=result.experiments[observation.experiment_id],
        bench=_cell_material_bench((CellMaterial.PT,)),
        isolated=False,
    )

    assert seen.get("wt") != {"Fe": 100.0}
    assert observation.identity.reservoir.is_unknown
    starting_composition = observation.point_conditions["starting_composition"]
    assert starting_composition.state.is_unknown
    assert starting_composition.state.reason == "purity not reported"
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert (
        prediction.refusal_detail["reason"]
        == "composition_not_stated_pure_reservoir"
    )


@pytest.mark.parametrize(
    ("starting_composition", "expected_phase"),
    (
        (
            {
                "state": {
                    "tag": "value",
                    "value": {
                        "kind": "categorical",
                        "categorical": "Fe-Ni alloy",
                    },
                }
            },
            None,
        ),
        (
            {"state": {"tag": "unknown", "reason": "purity not reported"}},
            None,
        ),
        (_typed_point_composition((("Fe", "1"),)), Phase.L),
        (None, Phase.L),
    ),
    ids=(
        "categorical-overrides-parent",
        "unknown-overrides-parent",
        "typed-pure",
        "absent-uses-parent",
    ),
)
def test_point_composition_is_the_purity_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    starting_composition,
    expected_phase,
) -> None:
    from simulator.battery.enums import AmountBasis, CellMaterial
    from simulator.battery.records import Composition
    from tests.battery.test_silent_fills import _capture_cell, _cell_material_bench

    experiment = F.kems_experiment(experiment_id="pure-parent")
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=F.located(
                Composition(
                    basis="mole_fraction",
                    components=(("Fe", Decimal("1")),),
                    amount_basis=AmountBasis.MOLE_FRACTION,
                )
            ),
        ),
    )
    point = {
        "T_C": 1600,
        "P_Pa": 0.1,
        "phase_as_published": "Fe(l)",
        "locator": {"table": "1", "row": 1, "page": 3},
    }
    if starting_composition is not None:
        point["point_conditions"] = {"starting_composition": starting_composition}
    extract = _ferguson_effusion_extract([point])
    extract["experiments"] = [to_plain(experiment)]
    extract["species"]["Fe"]["observations"][0]["experiment"] = "pure-parent"
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(iter(result.observations.values()))

    if expected_phase is None:
        assert observation.identity.reservoir.is_unknown
    else:
        assert observation.identity.reservoir.is_value
        assert observation.identity.reservoir.value.phase.value is expected_phase

    if (
        starting_composition is not None
        and starting_composition["state"].get("value", {}).get("kind")
        == "categorical"
    ):
        seen = _capture_cell(monkeypatch)
        prediction = predict_with_engine(
            Engine.OPENIMCC,
            observation,
            experiment=result.experiments[observation.experiment_id],
            bench=_cell_material_bench((CellMaterial.PT,)),
            isolated=False,
        )
        assert seen.get("wt") != {"Fe": 100.0}
        assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
        assert (
            prediction.refusal_detail["reason"]
            == "composition_not_stated_pure_reservoir"
        )


def test_h09_quantity_not_applicable_is_not_unknown() -> None:
    from dataclasses import replace

    known = F.psat_identity("Na")
    na = replace(known, quantity=State.not_applicable("test"))
    both_na = identity_equal(na, na)
    assert both_na.kind is IdentityEqualKind.INVALID_IDENTITY
    vs_known = identity_equal(known, na)
    assert vs_known.kind is IdentityEqualKind.IDENTITY_MISMATCH
    unk = replace(known, quantity=State.unknown("missing quantity"))
    assert identity_equal(unk, unk).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert identity_equal(known, unk).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    exp = F.tabulation_experiment()
    report = validate_corpus(
        [F.work()],
        [exp],
        [F.observation("na-qty", exp.experiment_id, na, 1)],
    )
    assert any(i.reason.value == "invalid_identity" for i in report.hard_issues)


def test_g01_identity_unknown_phase_never_equals() -> None:
    from dataclasses import replace

    known = F.psat_identity("Na")
    unknown_species = Species(
        "Na",
        State.unknown("source does not state phase"),
        polymorph=State.unknown("phase unknown"),
    )
    unknown = replace(known, species=unknown_species)
    outcome = identity_equal(known, unknown)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "species.phase" in outcome.fields
    both_unknown = identity_equal(unknown, unknown)
    assert both_unknown.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    exp = F.tabulation_experiment()
    report = validate_corpus(
        [F.work()],
        [exp],
        [F.observation("unk-phase", exp.experiment_id, unknown, 1)],
    )
    assert report.hard_issues == ()


def test_g02_iron_olivine_kems_is_knudsen_not_langmuir(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "langmuir_knudsen_flux_validation.v1",
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "material": "Fo93Fa7_olivine",
                        "regime": "knudsen_effusion_mass_spectrometry",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {"range": [0.011, 0.020]},
                        "source": {
                            "citation_id": "REF-016",
                            "citation": "Costa & Jacobson (2015)",
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["iron_olivine_kems"]
    exp = result.experiments[obs.experiment_id]
    assert exp.method.is_value
    assert exp.method.value is MethodToken.KNUDSEN_EFFUSION
    assert exp.method.value is not MethodToken.LANGMUIR_FREE_EVAPORATION
    assert obs.evidence.class_.is_unknown
    assert obs.evidence.class_.value is not EvidenceClass.MEASURED_DIRECT


def test_g02_langmuir_ledger_keeps_qualified_derived_from(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    parent = "10.1016/j.gca.2005.08.014::kems-005-fedkin-2006::fedkin_2006_k_yu_langmuir"
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "potassium_silicate_vacuum": {
                        "species": "K",
                        "temperature_range_k": [1723.15, 1723.15],
                        "measured_langmuir_to_effusion_flux_ratio": {"value": 0.13},
                        "derived_from": [parent],
                        "source": {
                            "citation_id": "REF-014",
                            "citation": "Fedkin et al. (2006), DOI 10.1016/j.gca.2005.08.014",
                            "doi": "10.1016/j.gca.2005.08.014",
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    observation = migrate(root, write=False).observations["potassium_silicate_vacuum"]
    assert observation.derived_from == (parent,)


def test_g02_kems_row_without_method_is_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {"kems-src": {"citation": "KEMS sidecar fixture"}},
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "kems_no_method",
                                "species": "Ca",
                                "status": "absent",
                                "coordinate": {"temperature_K": 2000.0},
                                "partial_pressure_pa": None,
                                "source_locator": {"figure": 1},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["kems_no_method"]
    exp = result.experiments[obs.experiment_id]
    assert exp.method.is_unknown
    assert obs.evidence.class_.is_unknown
    assert obs.evidence.class_.value is not EvidenceClass.FIGURE_ONLY


def test_g09_queue_ids_resolve_in_store(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["phase"] = "silicate_melt"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    for entry in result.queue:
        assert entry.work_id in result.works, entry
        if entry.observation_id is not None:
            assert entry.observation_id in result.observations, entry


def test_g10_kems_uncertainty_is_retained(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": {"kems-src": {"citation": "KEMS sidecar fixture"}},
                "cases": {
                    "case": {
                        "source_id": "kems-src",
                        "points": [
                            {
                                "observable_id": "cao_p_ca_isothermal",
                                "species": "Ca",
                                "coordinate": {"temperature_K": 2077.0},
                                "partial_pressure_pa": None,
                                "uncertainty": {"temperature_K": 5, "observable_status": "not_reported"},
                                "source_locator": {"figure": 7},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["cao_p_ca_isothermal"]
    assert obs.uncertainty.kind.value == "printed"
    assert obs.uncertainty.verbatim is not None


_WIMPENNY_EXTRACT = (
    REPO_ROOT
    / "data"
    / "literature"
    / "extracts"
    / "wimpenny-2019-zn-isotope-evaporation-extreme-t.yaml"
)


def test_wimpenny_arkose_row_keeps_printed_two_sigma_uncertainty() -> None:
    migrator = F.migrate_one_extract(REPO_ROOT, _WIMPENNY_EXTRACT)
    observation = next(
        observation
        for observation_id, observation in migrator.result.observations.items()
        if ":rows:row=arkose-soil:" in observation_id
    )

    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == as_decimal("0.22")
    assert observation.locator is not None
    assert observation.locator.published_page == 33
    assert observation.uncertainty.kind is UncertaintyKind.PRINTED
    assert observation.uncertainty.value == as_decimal("0.02")
    assert observation.uncertainty.basis == "2sigma"
    assert observation.uncertainty.verbatim is not None
    assert (
        as_decimal(observation.uncertainty.verbatim["sigma"]) == as_decimal("0.02")
    )
    assert observation.uncertainty.verbatim["k"] == 2


def test_wimpenny_unknown_uncertainty_column_is_not_guessed(tmp_path: Path) -> None:
    doc = yaml.safe_load(_WIMPENNY_EXTRACT.read_text(encoding="utf-8"))
    row = next(
        row
        for species in doc["species"].values()
        for observation in species.get("observations", [])
        for row in observation.get("values", {}).get("rows", [])
        if row.get("sample") == "Arkose soil"
    )
    row["delta66Zn_1sigma_permil"] = row.pop("delta66Zn_2sigma_permil")
    doc["source_id"] = "fixture-source"

    result = migrate(_write_min_tree(tmp_path, doc), write=False)
    observation = next(
        observation
        for observation_id, observation in result.observations.items()
        if ":rows:row=arkose-soil:" in observation_id
    )
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == as_decimal("0.22")
    assert observation.uncertainty.value is None
    assert observation.uncertainty.basis is None
    assert "delta66Zn_1sigma_permil" not in str(observation.uncertainty.verbatim)


def test_h04_ocr_locator_does_not_fall_through_to_pdf(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["locator"] = {
        "source_path": "ocr/missing.md",
        "table": "I",
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert not str(obs.read_from).startswith("pdf:")
    assert "unknown" in str(obs.read_from)
    assert any("ocr" in (e.why or "").lower() or "source_path" in (e.why or "")
               for e in result.queue)
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert report.hard_issues == ()


def test_g11_read_from_is_unknown_without_index_asset(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": "fixture-source",
                        "citation": "Fixture, A. (2026), Test Journal 1:1",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert not str(obs.read_from).startswith("pdf:")
    assert "unknown" in str(obs.read_from)


def test_g12_metadata_files_are_not_observation_rows(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    comp = root / "data" / "literature" / "compilations" / "janaf-4th"
    comp.mkdir(parents=True)
    (comp / "manifest.yaml").write_text(
        yaml.safe_dump({"source_id": "janaf-4th", "source": {"citation": "JANAF"}}, sort_keys=False),
        encoding="utf-8",
    )
    (comp / "sidecar.yaml").write_text("schema_version: sidecar\n", encoding="utf-8")
    result = migrate(root, write=False)
    manifest = result.source_counts["data/literature/compilations/janaf-4th/manifest.yaml"]
    sidecar = result.source_counts["data/literature/compilations/janaf-4th/sidecar.yaml"]
    assert manifest.metadata_in == 1
    assert manifest.rows_in == 0
    assert sidecar.metadata_in == 1
    assert sidecar.rows_in == 0
    index = result.source_counts["data/literature/INDEX.yaml"]
    assert index.index_works_in >= 1
    assert index.rows_in == 0


def test_g08_model_derived_keeps_table_destination(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"]["method_class"] = "model_derived"
    extract["species"]["Na"]["observations"][0]["values"].pop("series", None)
    extract["species"]["Na"]["observations"][0]["values"]["quantity"] = "pure_Psat"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.evidence.class_.is_value
    assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert not obs.derived_from


def test_j02_model_derived_without_parent_is_conditional_field(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["values"]["method_class"] = "model_derived"
    extract["species"]["Na"]["observations"][0]["values"].pop("series", None)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert not obs.derived_from
    assert obs.derivation is None
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    paths = [i.path for i in report.hard_issues]
    assert any(p.endswith(".derived_from") for p in paths), report.hard_issues
    assert any(p.endswith(".derivation") for p in paths), report.hard_issues
    assert all(
        i.reason.value == "conditional_field"
        for i in report.hard_issues
        if i.path.endswith(".derived_from") or i.path.endswith(".derivation")
    )
    assert any(
        "derived_from" in (e.axes or ()) or "derivation" in (e.axes or ())
        for e in result.queue
    )


@pytest.mark.parametrize(
    ("source_status", "has_value", "categorical", "expects_lineage_issue"),
    [
        ("figure_only", False, False, False),
        ("figure_only", False, True, False),
        ("figure_only", True, False, True),
        ("admitted", False, False, True),
    ],
)
def test_b879_caption_only_figure_model_lineage_requirement(
    tmp_path: Path,
    source_status: str,
    has_value: bool,
    categorical: bool,
    expects_lineage_issue: bool,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    values = extract["species"]["Na"]["observations"][0]["values"]
    values["method_class"] = "model_derived"
    values["admission_status"] = source_status
    if categorical:
        values["semantics"] = "bound_not_point_ordering"
    if not has_value:
        values.pop("series", None)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    observation = next(iter(result.observations.values()))
    assert observation.value.kind is (
        ValueKind.POINT
        if has_value
        else ValueKind.CATEGORICAL
        if categorical
        else ValueKind.UNAVAILABLE
    )
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    lineage_issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith((".derived_from", ".derivation"))
    ]
    assert bool(lineage_issues) is expects_lineage_issue
    assert all(
        issue.reason is RefusalReason.CONDITIONAL_FIELD for issue in lineage_issues
    )


def test_dump_hard_issues_writes_complete_source_labeled_census(tmp_path: Path) -> None:
    from tools.dump_hard_issues import dump_hard_issues

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    values = extract["species"]["Na"]["observations"][0]["values"]
    values["method_class"] = "model_derived"
    values.pop("series", None)
    root = _write_min_tree(tmp_path / "tree", extract)
    result = migrate(root, write=False)
    out = tmp_path / "issues.tsv"

    written = dump_hard_issues(root, out)
    with out.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))

    assert written == len(rows) == len(result.validation.hard_issues)
    assert any(
        row["source_id"] == "fixture-source"
        and row["field"] == "derived_from"
        and row["issue_code"] == "conditional_field"
        for row in rows
    )

    filtered = tmp_path / "filtered.tsv"
    assert dump_hard_issues(root, filtered, "fixture-source") == len(rows)
    assert dump_hard_issues(root, tmp_path / "empty.tsv", "other-source") == 0


def test_dump_hard_issues_preserves_indexed_notice_path(tmp_path: Path) -> None:
    from unittest.mock import patch

    import tools.dump_hard_issues as exporter

    root = _write_min_tree(tmp_path / "tree")
    result = migrate(root, write=False)
    observation_id = "fixture-source::na_psat::T=1200.0:h=8f69f8b68978"
    observation = result.observations[observation_id]
    result.observations[observation_id] = replace(
        observation,
        notices=(
            Notice(
                kind=NoticeKind.FALLBACK,
                affected_quantities=(Quantity.P_SAT,),
                reason="validator regression fixture",
                origin=observation_id,
            ),
        ),
    )
    result.validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        benches=result.benches,
    )
    assert any(
        issue.path == f"observation[{observation_id}].notices[0]"
        for issue in result.validation.hard_issues
    )

    out = tmp_path / "indexed-issues.tsv"
    with patch.object(exporter, "migrate", return_value=result):
        assert exporter.dump_hard_issues(root, out, "fixture-source") == 1
    with out.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))

    assert rows == [
        {
            "source_id": "fixture-source",
            "observation_id": observation_id,
            "field": "notices[0]",
            "issue_code": "conditional_field",
            "message": "fallback notice requires from/to",
            "evidence_class": "measured_direct",
            "method_class": "measured_direct",
            "quantity": "p_sat",
        }
    ]


def test_j02_source_stated_derived_from_is_stored(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    parent = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    parent["observation_id"] = "raw_parent"
    parent["values"]["method_class"] = "measured_direct"
    parent["values"].pop("series", None)
    child = yaml.safe_load(yaml.safe_dump(parent))
    child["observation_id"] = "derived_child"
    child["values"]["method_class"] = "model_derived"
    child["values"]["derived_from"] = "raw_parent"
    extract["species"]["Na"]["observations"] = [parent, child]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    child_obs = next(
        o for o in result.observations.values() if "derived_child" in o.observation_id
    )
    assert child_obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert child_obs.derived_from == ("fixture-source::raw_parent",)


@pytest.mark.parametrize("annotation", ["derivation", "inference"])
@pytest.mark.parametrize("location", ["values", "observation"])
@pytest.mark.parametrize(
    ("inputs", "invalid"),
    [([], True), (None, True), (7, True), (["source measurement"], False)],
)
def test_source_derivation_and_inference_inputs_are_validated(
    tmp_path: Path,
    annotation: str,
    location: str,
    inputs: object,
    invalid: bool,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    target = row["values"] if location == "values" else row
    field = "values." + annotation if location == "values" else annotation
    target[annotation] = {
        "relation": "author_calculation",
        "inputs": inputs,
        "output_unit": "Pa",
    }
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)

    path = f"observations.fixture-source::na_psat.{field}"
    issues = [issue for issue in result.validation.hard_issues if issue.path == path]
    if invalid:
        assert len(issues) == 1
        assert issues[0].reason is RefusalReason.INVALID_SOURCE
        assert "Derivation.inputs" in issues[0].detail
    else:
        assert not issues


def test_source_derivation_reports_relation_and_inputs_independently() -> None:
    _, missing_both = source_derivation_from_source(
        {}, {"derivation": {"output_unit": "Pa"}}
    )
    assert {detail for _, detail in missing_both} == {
        "Derivation.inputs is required unless raw_inputs is located not_printed",
        "derivation relation (author method) is required",
    }

    _, missing_relation = source_derivation_from_source(
        {}, {"derivation": {"inputs": ["pdf:fixture"], "output_unit": "Pa"}}
    )
    assert [detail for _, detail in missing_relation] == [
        "derivation relation (author method) is required"
    ]

    _, missing_inputs = source_derivation_from_source(
        {},
        {
            "derivation": {
                "relation": "author calculation",
                "output_unit": "Pa",
            }
        },
    )
    assert [detail for _, detail in missing_inputs] == [
        "Derivation.inputs is required unless raw_inputs is located not_printed"
    ]

    accepted_with_lineage, no_lineage_errors = source_derivation_from_source(
        {
            "derivation": {
                "relation": "author calculation",
                "output_unit": "Pa",
            }
        },
        {},
        has_resolved_lineage=True,
    )
    assert accepted_with_lineage is not None
    assert accepted_with_lineage.inputs == ()
    assert no_lineage_errors == ()


@pytest.mark.parametrize(
    ("extract_name", "observation_id", "expected_details"),
    [
        (
            "shornikov-1994-mullite-kems.yaml",
            "shornikov_1994_al2o3_activity_1833k",
            (),
        ),
        (
            "wimpenny-2019-zn-isotope-evaporation-extreme-t.yaml",
            "wimpenny_2019_zn_fractionation_summary",
            (),
        ),
        (
            "no27-fegley-cameron-1987-epsl.yaml",
            "fegley_cameron_1987_model_setup_initial_oxide_magma",
            (),
        ),
        (
            "synthetic-offer7-derivation.yaml",
            "sossi_2019_k_table5_experimental",
            (
                "invalid derivation annotation: derivation relation (author method) is required",
                "invalid derivation annotation: Derivation.inputs is required unless raw_inputs is located not_printed",
            ),
        ),
        (
            "synthetic-offer7-derivation.yaml",
            "ms2000_044_sio2_activity_na_system_xsio2_0805_t1473_class_quote_r2",
            (
                "invalid derivation annotation: derivation relation (author method) is required",
                "invalid derivation annotation: Derivation.inputs is required unless raw_inputs is located not_printed",
            ),
        ),
        (
            "synthetic-offer7-derivation.yaml",
            "okazaki_2022_fragmentation_assumptions_and_model_output",
            (
                "invalid derivation annotation: derivation relation (author method) is required",
                "invalid derivation annotation: Derivation.inputs is required unless raw_inputs is located not_printed",
            ),
        ),
        (
            "synthetic-offer7-derivation.yaml",
            "busemann_2024_preliminary_ryugu_exposure_ages",
            (
                "invalid derivation annotation: Derivation.inputs is required unless raw_inputs is located not_printed",
            ),
        ),
    ],
)
def test_offer7_derivation_issue_classification_is_pinned(
    tmp_path: Path,
    extract_name: str,
    observation_id: str,
    expected_details: tuple[str, ...],
) -> None:
    if extract_name == "synthetic-offer7-derivation.yaml":
        extract = _raw_inputs_extract("calculated")
        child = extract["species"]["Na"]["observations"][0]
        child["observation_id"] = observation_id
        child["values"].pop("series", None)
        child["values"]["pressure_atm"] = 1.0
        derivation = child["values"].pop("derivation")
        derivation.pop("raw_inputs")
        if len(expected_details) == 1:
            derivation["relation"] = "author calculation"
        else:
            derivation.pop("relation")
            derivation.pop("inputs", None)
        child["values"]["derivation"] = derivation
        result = migrate(_write_min_tree(tmp_path, extract), write=False)
        source_id = "fixture-source"
    else:
        result = _migrate_real_extract(tmp_path, extract_name)
        source_id = extract_name.removesuffix(".yaml")
    path_prefix = f"observations.{source_id}::{observation_id}."
    details = tuple(
        issue.detail
        for issue in result.validation.hard_issues
        if issue.path.startswith(path_prefix)
        and issue.reason is RefusalReason.INVALID_SOURCE
    )

    assert details == expected_details


def test_t1235_real_derivation_rows_are_valid_after_repair(tmp_path: Path) -> None:
    target_rows = {
        "kems-012-sossi-2019": {
            "sossi_2019_k_table5_experimental",
            "sossi_2019_mn_table5_pure_system_not_experimental",
            "sossi_2019_mn_table6_te1_pure_system_derived",
            "sossi_2019_li_logKstar_table3_Li_g",
            "sossi_2019_li_logKstar_table3_LiO_g",
            "sossi_2019_li_table4_gamma_this_work",
            "sossi_2019_li_table5_experimental_Li_g",
            "sossi_2019_rb_logKstar_table3",
            "sossi_2019_rb_table4_gamma_this_work",
            "sossi_2019_rb_table5_experimental",
            "sossi_2019_cu_logKstar_table3",
            "sossi_2019_cu_table4_gamma_this_work",
            "sossi_2019_cu_table5_experimental",
            "sossi_2019_ag_logKstar_table3",
            "sossi_2019_ag_table4_gamma_this_work",
            "sossi_2019_zn_logKstar_table3",
            "sossi_2019_zn_table4_gamma_this_work",
            "sossi_2019_zn_table5_experimental",
            "sossi_2019_ge_logKstar_table3",
            "sossi_2019_ge_table4_gamma_this_work",
            "sossi_2019_ge_table5_experimental",
            "sossi_2019_cd_logKstar_table3",
            "sossi_2019_cd_table4_gamma_this_work",
            "sossi_2019_ga_logKstar_table3_Ga_g",
            "sossi_2019_ga_logKstar_table3_GaO_g",
            "sossi_2019_ga_table5_experimental_Ga_g",
            "sossi_2019_pb_logKstar_table3_PbO_g",
            "sossi_2019_pb_logKstar_table3_Pb_g",
            "sossi_2019_pb_table4_gamma_this_work",
            "sossi_2019_pb_table5_experimental_PbO_g",
            "sossi_2019_mo_logKstar_table3_MoO3_congruent",
            "sossi_2019_mo_logKstar_table3_MoO2_ox",
            "sossi_2019_mo_table5_experimental_MoO3",
            "sossi_2019_cr_logKstar_table3_CrO_ox",
            "sossi_2019_cr_table5_experimental_CrO_ox",
        },
        "kems-ms2000-044": {
            "ms2000_044_sio2_activity_na_system_xsio2_0805_t1473_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0753_t1273_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0709_t1673_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0671_t1173_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0625_t1573_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0573_t1473_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0524_t1573_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0477_t1373_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0430_t1473_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0405_t1423_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0382_t1383_class_quote_r2",
            "ms2000_044_sio2_activity_na_system_xsio2_0349_t1373_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0892_t1723_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0848_t1573_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0811_t1173_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0770_t1073_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0722_t1323_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0722_t1673_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0674_t1373_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0630_t1323_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0630_t1523_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0591_t1323_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0543_t1473_class_quote_r2",
            "ms2000_044_sio2_activity_k_system_xsio2_0500_t1473_class_quote_r2",
        },
        "okazaki-2022-ryugu-gas-treasure-box": {
            "okazaki_2022_fragmentation_assumptions_and_model_output",
            "okazaki_2022_surface_pulverization_model",
        },
        "busemann-2024-noble-gases-ryugu-bennu": {
            "busemann_2024_preliminary_ryugu_exposure_ages",
        },
    }
    assert sum(map(len, target_rows.values())) == 62

    extract_dir = REPO_ROOT / "data" / "literature" / "extracts"
    root = tmp_path / "t1235-real-index"
    copied_extracts = root / "data" / "literature" / "extracts"
    copied_extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir()
    index_path = REPO_ROOT / "data" / "literature" / "INDEX.yaml"
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        index_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    index_doc = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index_rows = {row["source_id"]: row for row in index_doc["sources"]}
    source_rows: dict[str, dict[str, dict]] = {}
    for source_id in target_rows:
        path = extract_dir / f"{source_id}.yaml"
        (copied_extracts / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        source_rows[source_id] = {
            row["observation_id"]: row
            for block in document["species"].values()
            for row in block.get("observations", [])
        }
        assert target_rows[source_id] <= source_rows[source_id].keys()

    def assert_registered_assets(rows: list[tuple[str, dict]]) -> None:
        for source_id, row in rows:
            values = row.get("values") or {}
            for payload in (row, values):
                derivation = payload.get("derivation") or {}
                refs = payload.get("derived_from") or ()
                if isinstance(refs, str):
                    refs = (refs,)
                refs = (*refs, *(derivation.get("inputs") or ()))
                for ref in refs:
                    if not isinstance(ref, str) or ":" not in ref:
                        continue
                    asset_kind, asset_source = ref.split(":", 1)
                    if asset_kind not in {"pdf", "tables"}:
                        continue
                    asset_index = index_rows.get(asset_source) or {}
                    corpus = asset_index.get("corpus") or {}
                    if asset_kind == "pdf":
                        assert (corpus.get("raw") or {}).get("path") or asset_index.get(
                            "pdf_path"
                        ), (source_id, row["observation_id"], ref)
                    else:
                        # source_files_for may register a table path despite exists=false.
                        table_asset = corpus.get("tables") or {}
                        assert table_asset.get("exists") is True and int(
                            table_asset.get("file_count") or 0
                        ) > 0, (source_id, row["observation_id"], ref)

    def assert_target_rows_clean(issues: tuple) -> None:
        qualified_ids = {
            f"{source_id}::{observation_id}"
            for source_id, observation_ids in target_rows.items()
            for observation_id in observation_ids
        }
        failures = []
        for issue in issues:
            if not any(
                f"{qualified_id}]" in issue.path
                or f"{qualified_id}." in issue.path
                for qualified_id in qualified_ids
            ):
                continue
            if issue.reason in {
                RefusalReason.INVALID_SOURCE,
                RefusalReason.REFERENTIAL_INTEGRITY,
            } or (
                issue.reason is RefusalReason.CONDITIONAL_FIELD
                and (".derived_from" in issue.path or ".derivation" in issue.path)
            ):
                failures.append(issue)
        assert failures == [], failures

    rows_to_check = [
        (source_id, source_rows[source_id][observation_id])
        for source_id, observation_ids in target_rows.items()
        for observation_id in observation_ids
    ]
    assert_registered_assets(rows_to_check)

    sossi_observation_parents = {
        "sossi_2019_k_table5_experimental": ("sossi_2019_k_logKstar_table3",),
        "sossi_2019_mn_table6_te1_pure_system_derived": (
            "sossi_2019_mn_table5_pure_system_not_experimental",
        ),
        "sossi_2019_li_table4_gamma_this_work": (
            "sossi_2019_li_logKstar_table3_Li_g",
        ),
        "sossi_2019_li_table5_experimental_Li_g": (
            "sossi_2019_li_logKstar_table3_Li_g",
        ),
        "sossi_2019_rb_table4_gamma_this_work": (
            "sossi_2019_rb_logKstar_table3",
        ),
        "sossi_2019_rb_table5_experimental": (
            "sossi_2019_rb_logKstar_table3",
        ),
        "sossi_2019_cu_table4_gamma_this_work": (
            "sossi_2019_cu_logKstar_table3",
        ),
        "sossi_2019_cu_table5_experimental": (
            "sossi_2019_cu_logKstar_table3",
        ),
        "sossi_2019_ag_table4_gamma_this_work": (
            "sossi_2019_ag_logKstar_table3",
        ),
        "sossi_2019_zn_table4_gamma_this_work": (
            "sossi_2019_zn_logKstar_table3",
        ),
        "sossi_2019_zn_table5_experimental": (
            "sossi_2019_zn_logKstar_table3",
        ),
        "sossi_2019_ge_table4_gamma_this_work": (
            "sossi_2019_ge_logKstar_table3",
        ),
        "sossi_2019_ge_table5_experimental": (
            "sossi_2019_ge_logKstar_table3",
        ),
        "sossi_2019_cd_table4_gamma_this_work": (
            "sossi_2019_cd_logKstar_table3",
        ),
        "sossi_2019_ga_table5_experimental_Ga_g": (
            "sossi_2019_ga_logKstar_table3_Ga_g",
        ),
        "sossi_2019_pb_table4_gamma_this_work": (
            "sossi_2019_pb_logKstar_table3_Pb_g",
        ),
        "sossi_2019_pb_table5_experimental_PbO_g": (
            "sossi_2019_pb_logKstar_table3_PbO_g",
        ),
        "sossi_2019_mo_table5_experimental_MoO3": (
            "sossi_2019_mo_logKstar_table3_MoO3_congruent",
        ),
        "sossi_2019_cr_table5_experimental_CrO_ox": (
            "sossi_2019_cr_logKstar_table3_CrO_ox",
        ),
    }
    sossi_footnote = (
        "* denotes elements for which logK* was measured at only 1 temperature, "
        "∆S* and ∆H* determined by assuming γi remains constant over all temperatures."
    )
    for observation_id in (
        "sossi_2019_k_table5_experimental",
        "sossi_2019_li_table5_experimental_Li_g",
        "sossi_2019_mo_table5_experimental_MoO3",
        "sossi_2019_cr_table5_experimental_CrO_ox",
    ):
        row = source_rows["kems-012-sossi-2019"][observation_id]
        derivation = row["values"]["derivation"]
        assert derivation["relation"] == sossi_footnote
        assert row["locator"]["table"] == "5"
        assert row["locator"]["line_range"] == "1121-1127"

    for observation_id in target_rows["kems-ms2000-044"]:
        row = source_rows["kems-ms2000-044"][observation_id]
        assert row["derived_from"] == ["tables:kems-ms2000-044"]
        assert row["derivation"]["inputs"] == ["tables:kems-ms2000-044"]
    okazaki_id = "okazaki-2022-ryugu-gas-treasure-box"
    assert source_rows[okazaki_id][
        "okazaki_2022_fragmentation_assumptions_and_model_output"
    ]["derived_from"] == ["pdf:okazaki-2022-ryugu-gas-treasure-box"]
    assert source_rows[okazaki_id]["okazaki_2022_surface_pulverization_model"][
        "derived_from"
    ] == [
        "pdf:okazaki-2022-ryugu-gas-treasure-box",
        "okazaki_2022_solar_wind_helium_abundance",
    ]
    busemann_id = "busemann-2024-noble-gases-ryugu-bennu"
    assert source_rows[busemann_id][
        "busemann_2024_preliminary_ryugu_exposure_ages"
    ]["derived_from"] == ["pdf:busemann-2024-noble-gases-ryugu-bennu"]

    result = migrate(root, write=False)
    assert result.validation is not None
    assert_target_rows_clean(result.validation.hard_issues)
    sossi_source_id = "kems-012-sossi-2019"
    for observation_id, expected_parents in sossi_observation_parents.items():
        observation = result.observations[f"{sossi_source_id}::{observation_id}"]
        qualified_parents = (
            f"tables:{sossi_source_id}",
            *(f"{sossi_source_id}::{parent_id}" for parent_id in expected_parents),
        )
        assert observation.derived_from == qualified_parents
        assert observation.derivation is not None
        assert observation.derivation.inputs == qualified_parents

    fragmentation = result.observations[
        f"{okazaki_id}::okazaki_2022_fragmentation_assumptions_and_model_output"
    ]
    assert fragmentation.derived_from == (
        f"pdf:{okazaki_id}",
    )
    pulverization = result.observations[
        f"{okazaki_id}::okazaki_2022_surface_pulverization_model"
    ]
    assert pulverization.derived_from == (
        f"pdf:{okazaki_id}",
        f"{okazaki_id}::okazaki_2022_solar_wind_helium_abundance",
    )
    exposure_ages = result.observations[
        f"{busemann_id}::busemann_2024_preliminary_ryugu_exposure_ages"
    ]
    assert exposure_ages.derived_from == (
        f"pdf:{busemann_id}",
    )

    from simulator.battery.validate import validate_corpus

    mutated_observations = dict(result.observations)
    mutated_id = (
        "busemann-2024-noble-gases-ryugu-bennu::"
        "busemann_2024_preliminary_ryugu_exposure_ages"
    )
    mutated_observations[mutated_id] = replace(
        mutated_observations[mutated_id], derived_from=("pdf:not-registered",)
    )
    mutated_report = validate_corpus(
        result.works, result.experiments, mutated_observations
    )
    assert any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and mutated_id in issue.path
        for issue in mutated_report.hard_issues
    )
    with pytest.raises(AssertionError):
        assert_target_rows_clean(mutated_report.hard_issues)

    mutant_row = deepcopy(
        source_rows[busemann_id]["busemann_2024_preliminary_ryugu_exposure_ages"]
    )
    mutant_row["derived_from"] = ["tables:not-registered"]
    with pytest.raises(AssertionError):
        assert_registered_assets([(busemann_id, mutant_row)])


@pytest.mark.parametrize("location", ["values", "observation"])
@pytest.mark.parametrize("relation", ["missing", None, ""])
def test_calculated_child_without_author_relation_stays_unknown(
    tmp_path: Path, location: str, relation: object
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    template = extract["species"]["Na"]["observations"][0]
    parent = yaml.safe_load(yaml.safe_dump(template))
    parent["observation_id"] = "measured_parent"
    parent["values"]["pressure_atm"] = 1.0
    parent["values"]["method_class"] = "measured_direct"
    parent["values"].pop("series", None)

    child = yaml.safe_load(yaml.safe_dump(template))
    child["observation_id"] = "calculated_child"
    child["values"]["method_class"] = "calculated"
    child["values"]["derived_from"] = "measured_parent"
    child["values"].pop("series", None)
    child["values"]["pressure_atm"] = 1.0
    derivation = {
        "inputs": ["measured_parent"],
        "output_unit": "Pa",
    }
    if relation != "missing":
        derivation["relation"] = relation
    target = child["values"] if location == "values" else child
    target["derivation"] = derivation
    extract["species"]["Na"]["observations"] = [parent, child]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    observation = result.observations["fixture-source::calculated_child"]
    assert observation.evidence.class_.is_unknown
    path = (
        "observations.fixture-source::calculated_child."
        + ("values.derivation" if location == "values" else "derivation")
    )
    issues = [issue for issue in result.validation.hard_issues if issue.path == path]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert issues[0].detail.endswith(
        "derivation relation (author method) is required"
    )


@pytest.mark.parametrize("location", ["values", "observation"])
def test_located_not_printed_does_not_replace_author_relation(
    tmp_path: Path, location: str
) -> None:
    extract = _raw_inputs_extract("calculated")
    row = extract["species"]["Na"]["observations"][0]
    row["values"].pop("series", None)
    row["values"]["pressure_atm"] = 1.0
    derivation = row["values"].pop("derivation")
    derivation.pop("relation")
    derivation["inputs"] = []
    target = row["values"] if location == "values" else row
    target["derivation"] = derivation

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    observation = result.observations["fixture-source::na_psat"]
    assert observation.evidence.class_.is_unknown
    path = (
        "observations.fixture-source::na_psat."
        + ("values.derivation" if location == "values" else "derivation")
    )
    issues = [issue for issue in result.validation.hard_issues if issue.path == path]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert issues[0].detail.endswith(
        "derivation relation (author method) is required"
    )


def test_calculated_composition_requires_measured_lineage(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]

    measured = yaml.safe_load(yaml.safe_dump(base))
    measured["observation_id"] = "measured_parent"
    measured["values"].pop("series", None)
    measured["values"]["method_class"] = "measured_direct"
    measured["values"]["pressure_atm"] = 1.0

    recipe = yaml.safe_load(yaml.safe_dump(measured))
    recipe["observation_id"] = "recipe_composition"
    recipe["values"]["method_class"] = "calculated"
    recipe["values"]["derived_from"] = "measured_parent"
    recipe["values"]["composition_wt_pct"] = {"SiO2": 60.0, "MgO": 40.0}
    recipe["values"]["inference"] = {
        "relation": "recipe_to_mole_fraction",
        "inputs": ["measured_parent"],
        "output_unit": "mole_fraction",
    }

    author = yaml.safe_load(yaml.safe_dump(measured))
    author["observation_id"] = "author_calculation"
    author["values"]["method_class"] = "calculated"
    author["values"]["derived_from"] = "measured_parent"
    author["values"]["composition_wt_pct"] = {"SiO2": 60.0, "MgO": 40.0}
    author["values"]["derivation"] = {
        "relation": "author_mass_balance_reduction",
        "inputs": ["measured_parent"],
        "output_unit": "Pa",
    }

    extract["species"]["Na"]["observations"] = [measured, recipe, author]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)

    recipe_obs = result.observations["fixture-source::recipe_composition"]
    author_obs = result.observations["fixture-source::author_calculation"]
    assert recipe_obs.evidence.class_.is_unknown
    assert author_obs.evidence.class_.is_value
    assert author_obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED


def test_calculated_regime_fallback_still_requires_lineage(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["regime"] = "calculated"
    row["values"].pop("method_class", None)
    row["values"].pop("series", None)
    row["values"]["pressure_atm"] = 1.0
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    observation = next(iter(result.observations.values()))

    assert observation.evidence.class_.is_unknown
    assert not any(
        issue.reason is RefusalReason.CONDITIONAL_FIELD
        for issue in result.validation.hard_issues
    )


def test_calculated_rejected_parent_cannot_be_measured_ancestry(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    parent = yaml.safe_load(yaml.safe_dump(base))
    parent["observation_id"] = "rejected_parent"
    parent["values"].pop("series", None)
    parent["values"]["method_class"] = "measured_direct"
    parent["values"]["admission_status"] = "typed_refusal"
    parent["values"]["reason"] = "input measurement invalid: calibration failed"
    child = yaml.safe_load(yaml.safe_dump(parent))
    child["observation_id"] = "calculated_child"
    child["values"]["method_class"] = "calculated"
    child["values"]["admission_status"] = "admitted"
    child["values"]["derived_from"] = "rejected_parent"
    child["values"]["derivation"] = {
        "relation": "author_mass_balance_reduction",
        "inputs": ["rejected_parent"],
        "output_unit": "Pa",
    }
    extract["species"]["Na"]["observations"] = [parent, child]
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    parent_obs = result.observations["fixture-source::rejected_parent"]
    child_obs = result.observations["fixture-source::calculated_child"]

    assert parent_obs.admission.status is AdmissionStatus.REJECTED
    assert child_obs.evidence.class_.is_unknown
    assert not any(
        issue.reason is RefusalReason.CONDITIONAL_FIELD
        for issue in result.validation.hard_issues
    )


def test_calculated_series_conversion_is_not_author_derivation(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    parent = yaml.safe_load(yaml.safe_dump(base))
    parent["observation_id"] = "measured_parent"
    parent["values"].pop("series", None)
    parent["values"]["method_class"] = "measured_direct"
    parent["values"]["pressure_atm"] = 1.0
    child = yaml.safe_load(yaml.safe_dump(parent))
    child["observation_id"] = "calculated_series"
    child["values"]["method_class"] = "calculated"
    child["values"]["derived_from"] = "measured_parent"
    child["values"].pop("pressure_atm", None)
    child["values"]["series"] = [{"T_K": 1200.0, "pressure_atm": 1.0}]
    extract["species"]["Na"]["observations"] = [parent, child]
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    points = [
        observation
        for observation in result.observations.values()
        if "calculated_series" in observation.observation_id
    ]

    assert len(points) == 1
    assert points[0].derivation is not None
    assert points[0].derivation.relation == (
        "atm_to_Pa; species.phase=gas derived from quantity=p_sat "
        "(p_sat identity names the gas species)"
    )
    assert points[0].evidence.class_.is_unknown


def test_calculated_series_as_published_is_not_author_derivation(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    parent = yaml.safe_load(yaml.safe_dump(base))
    parent["observation_id"] = "measured_parent"
    parent["values"] = {
        "quantity": "activity_coefficient",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "gamma": 0.8,
    }
    child = yaml.safe_load(yaml.safe_dump(base))
    child.update({"observation_id": "calculated_as_published", "type": "activity_coefficient"})
    child["values"] = {
        "quantity": "activity_coefficient",
        "method_class": "calculated",
        "admission_status": "admitted",
        "derived_from": ["measured_parent"],
        "series": [{"T_K": 1200.0, "gamma": 0.5}],
    }
    extract["species"]["Na"]["observations"] = [parent, child]
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    points = [
        observation
        for observation in result.observations.values()
        if "calculated_as_published" in observation.observation_id
    ]

    assert len(points) == 1
    assert points[0].derivation is not None
    assert points[0].derivation.relation == "as_published"
    assert points[0].evidence.class_.is_unknown


def test_calculated_areal_conversion_keeps_author_derivation(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="mass_loss_areal_density",
        units="mg/cm2",
        values={
            "quantity": "mass_loss_areal_density",
            "method_class": "calculated",
            "admission_status": "admitted",
            "derived_from": ["measured_parent"],
            "derivation": {
                "relation": "author_mass_balance_reduction",
                "inputs": ["measured_parent"],
                "output_unit": "kg_per_m2",
            },
            "delta_q": "5.55184",
        },
        obs_type="mass_loss",
    )
    parent = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    parent["observation_id"] = "measured_parent"
    parent["values"] = {
        "quantity": "mass_loss_areal_density",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "delta_q": "1.0",
    }
    extract["species"]["Na"]["observations"].insert(0, parent)
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    observation = result.observations["fixture-source::na_psat"]

    assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert observation.derivation is not None
    assert observation.derivation.relation == "author_mass_balance_reduction"
    assert observation.derivation.output_unit == "kg_per_m2"
    assert not result.validation.hard_issues


def test_calculated_lineage_closes_independent_of_row_order(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]

    def derived_row(observation_id: str, parent_id: str) -> dict:
        row = yaml.safe_load(yaml.safe_dump(base))
        row["observation_id"] = observation_id
        row["values"].pop("series", None)
        row["values"]["method_class"] = "calculated"
        row["values"]["derived_from"] = parent_id
        row["values"]["derivation"] = {
            "relation": "author_mass_balance_reduction",
            "inputs": [parent_id],
            "output_unit": "Pa",
        }
        return row

    parent = yaml.safe_load(yaml.safe_dump(base))
    parent["observation_id"] = "measured_parent"
    parent["values"].pop("series", None)
    parent["values"]["method_class"] = "measured_direct"
    middle = derived_row("calculated_middle", "measured_parent")
    child = derived_row("calculated_child", "calculated_middle")
    extract["species"]["Na"]["observations"] = [child, middle, parent]
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)

    for observation_id in ("calculated_middle", "calculated_child"):
        observation = result.observations[f"fixture-source::{observation_id}"]
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED


def test_typed_refusal_preserves_admission_reason(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["values"].pop("series", None)
    row["values"]["admission_status"] = "typed_refusal"
    row["values"]["reason"] = "curves have no printed numeric table"
    root = _write_min_tree(tmp_path, extract)

    observation = next(iter(migrate(root, write=False).observations.values()))

    assert observation.admission.status is AdmissionStatus.REJECTED
    assert observation.admission.reason == "curves have no printed numeric table"


def test_typed_refusal_preserves_refusal_reason_field(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["values"].pop("series", None)
    row["values"]["admission_status"] = "typed_refusal"
    row["values"]["refusal_reason"] = "LiquidFractionInvalidError"
    root = _write_min_tree(tmp_path, extract)

    observation = next(iter(migrate(root, write=False).observations.values()))

    assert observation.admission.status is AdmissionStatus.REJECTED
    assert observation.admission.reason == "LiquidFractionInvalidError"


def test_bare_typed_refusal_method_is_rejected(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["values"].pop("series", None)
    row["values"].pop("admission_status", None)
    row["values"]["method_class"] = "typed_refusal"
    row["values"]["reason"] = "no printed numeric value"
    root = _write_min_tree(tmp_path, extract)

    observation = next(iter(migrate(root, write=False).observations.values()))

    assert observation.admission.status is AdmissionStatus.REJECTED
    assert observation.admission.reason == "no printed numeric value"


def test_h08_fourteen_token_table_destinations_are_stored(tmp_path: Path) -> None:
    from simulator.battery.migrate import METHOD_CLASS_MAP, evidence_for

    tokens = [
        "model_derived",
        "model_derived_inverse_fit",
        "model_derived_from_Kstar_and_external_gamma",
        "model_derived_from_Kstar_with_alpha_e_adopted_unity",
        "model_derived_assumption",
        "model",
        "derived_from_measured_kems_hertz_knudsen",
        "derived_third_law_from_measured_kems_and_janaf_fef",
        "derived_least_squares",
        "derived_gibbs_duhem_integration",
        "authors_preferred_average_of_kems_derived_gammas",
        "directly_reduced_measurement",
        "model_derived_second_law_fit",
        "derived_gibbs_duhem",
    ]
    assert len(tokens) == 14
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    rows = []
    for i, token in enumerate(tokens):
        row = yaml.safe_load(yaml.safe_dump(base))
        row["observation_id"] = f"token_{i}"
        row["values"]["method_class"] = token
        row["values"].pop("series", None)
        rows.append(row)
    extract["species"]["Na"]["observations"] = rows
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    for i, token in enumerate(tokens):
        dest = METHOD_CLASS_MAP[token]
        helper, _reason = evidence_for(token)
        assert helper.class_.is_value, token
        assert helper.class_.value is dest, token
        matches = [o for o in result.observations.values() if f"token_{i}" in o.observation_id]
        assert matches, token
        assert matches[0].evidence.class_.is_value, token
        assert matches[0].evidence.class_.value is dest, token


@pytest.mark.parametrize(
    ("method_class", "expected_evidence"),
    (
        ("measured_direct", "measured_direct"),
        ("measured_tabulated", "measured_tabulated"),
        ("measured_reduced", "measured_reduced"),
        ("quoted_attributed", "quoted_attributed"),
        ("quoted_unattributed", "quoted_unattributed"),
        ("model_derived", "model_derived"),
        ("author_estimate", "author_estimate"),
        ("figure_only", "figure_only"),
        ("compilation_assessed", "compilation_assessed"),
        ("engine_prediction", "engine_prediction"),
    ),
)
def test_canonical_evidence_method_class_mapping(
    method_class: str, expected_evidence: str
) -> None:
    from simulator.battery.migrate import evidence_for

    evidence, _reason = evidence_for(method_class)

    actual = (
        evidence.class_.value.value
        if evidence.class_.is_value
        else f"unknown: {evidence.class_.reason}"
    )
    assert actual == expected_evidence


def test_g07_unsupported_quantity_is_unknown_not_relabeled(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "mre_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mre_measurements.v1",
                "measurements": {
                    "yu_2025_hollow_anode_measurements": {
                        "paper_citation": {"title": "MRE fixture", "doi": "10.1234/MRE"},
                        "cases": {
                            "one_hour": {
                                "comparison_points": [
                                    {
                                        "observable_id": "mre_applied_charge_C",
                                        "expected_value": 1800.0,
                                        "units": "C",
                                        "source_locator": {"section": "2.2.2"},
                                    }
                                ]
                            }
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "janaf::Mg-nbp-sanity:T=1363::vapour_rail_psat::log10_Psat_over_P0",
                        "source_id": "janaf",
                        "species": "Mg",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 1363,
                        "table_kJ_mol": 0.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    charge = result.observations[
        "yu_2025_hollow_anode_measurements:one_hour:mre_applied_charge_C"
    ]
    assert charge.identity.quantity.is_unknown
    assert quantity_token(charge.identity) is not Quantity.MASS_LOSS_FRACTION
    psat = result.observations[
        "janaf::Mg-nbp-sanity:T=1363::vapour_rail_psat::log10_Psat_over_P0"
    ]
    assert psat.identity.quantity.is_unknown
    assert quantity_token(psat.identity) is not Quantity.DELTA_FG
    assert quantity_token(psat.identity) is not Quantity.P_SAT
    assert (psat.identity.quantity.reason or "") == (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )


def test_g06_p_atm_and_unliftable_series_explode(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "behrens_p_atm",
            "type": "psat_series",
            "locator": {"table": "2"},
            "phase": "gas",
            "units": "atm",
            "values": {
                "quantity": "pure_Psat",
                "method_class": "measured_direct",
                "series": [
                    {"T_K": 1200.0, "p_atm": 1.0},
                    {"T_K": 1300.0, "p_atm": 2.0},
                ],
            },
        },
        {
            "observation_id": "unliftable_row",
            "type": "psat_series",
            "locator": {"table": "3"},
            "phase": "gas",
            "values": {
                "quantity": "pure_Psat",
                "series": [{"quote": "text only", "locator": {"line_range": "4-5"}}],
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    atm_points = [
        o
        for o in result.observations.values()
        if "behrens_p_atm" in o.observation_id
    ]
    assert len(atm_points) == 2
    assert all(
        p.derivation is not None
        and p.derivation.relation == (
            "atm_to_Pa; species.phase=gas derived from quantity=p_sat "
            "(p_sat identity names the gas species)"
        )
        for p in atm_points
    )
    unlift = [
        o
        for o in result.observations.values()
        if "unliftable_row" in o.observation_id
    ]
    assert len(unlift) == 1
    assert unlift[0].value.kind.value == "unavailable"
    assert unlift[0].derivation is None
    assert result.measured.series == 2
    assert result.measured.tabulated_lists == 0


def test_g05_identical_duplicate_keys_are_aliased(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    point = {
        "key": "pankratz-dup::T=336.35::delta_fG_kJ_mol",
        "source_id": "pankratz-1987-usbm-b689",
        "species": "AgS",
        "temperature_K": 336.35,
        "table_kJ_mol": 1.0,
    }
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "points": [point, dict(point)]}, sort_keys=False),
        encoding="utf-8",
    )
    result = migrate(root, write=True)
    oid = point["key"]
    assert oid in result.observations
    assert len(result.observations) == 3  # fixture extract 2 points + 1 unique ledger
    aliases = [a for a in result.dedupe_aliases if a.observation_id == oid]
    assert aliases
    assert aliases[0].row_indices == (0, 1)
    ledger_count = result.source_counts["data/literature/gibbs_battery_residual_ledger.yaml"]
    assert ledger_count.rows_in == 2
    assert ledger_count.observations_out == 1
    store_obs = sum(c.observations_out for c in result.source_counts.values())
    assert store_obs == len(result.observations)


def test_g05_conflicting_duplicate_key_is_hard_error(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    a = {
        "key": "conflict-key",
        "source_id": "pankratz-1987-usbm-b689",
        "species": "AgS",
        "temperature_K": 300,
        "table_kJ_mol": 1.0,
    }
    b = dict(a)
    b["table_kJ_mol"] = 2.0
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "points": [a, b]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(DuplicateObservationIdError):
        migrate(root, write=False)


def test_g04_supersedes_marks_the_old_row(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "homma_1966_mn_olette_alpha_exp_table1",
            "type": "alpha",
            "locator": {"table": "1"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        },
        {
            "observation_id": "homma_1966_mn_olette_experimental_quoted_deep",
            "supersedes": "homma_1966_mn_olette_alpha_exp_table1",
            "type": "alpha",
            "locator": {"table": "1", "page": 517},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        },
        {
            "observation_id": "newer_list",
            "supersedes": [
                "homma_1966_mn_olette_experimental_quoted_deep",
                "missing_target",
            ],
            "type": "alpha",
            "locator": {"table": "2"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "admission_status": "admitted",
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    old = result.observations["fixture-source::homma_1966_mn_olette_alpha_exp_table1"]
    new = result.observations["fixture-source::homma_1966_mn_olette_experimental_quoted_deep"]
    newer = result.observations["fixture-source::newer_list"]
    assert old.admission.status is AdmissionStatus.SUPERSEDED
    assert old.admission.superseded_by == newer.observation_id
    assert new.admission.status is AdmissionStatus.SUPERSEDED
    assert new.admission.superseded_by == newer.observation_id
    assert newer.admission.status is AdmissionStatus.ADMITTED
    assert newer.admission.superseded_by is None
    assert any("missing_target" in (e.why or "") for e in result.queue)


def _migrate_supersedes_rows(
    tmp_path: Path, rows: list[tuple[str, str | None, str]]
) -> MigrationResult:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = []
    for observation_id, supersedes, status in rows:
        row = {
            "observation_id": observation_id,
            "type": "alpha",
            "locator": {"table": "1"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": status,
            },
        }
        if supersedes is not None:
            row["supersedes"] = supersedes
        extract["species"]["Na"]["observations"].append(row)
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    _assert_superseded_targets_are_live(result)
    return result


def _assert_superseded_targets_are_live(result: MigrationResult) -> None:
    observations = result.observations
    for observation in observations.values():
        target_id = observation.admission.superseded_by
        if target_id is None:
            continue
        assert target_id in observations
        assert observations[target_id].admission.status in {
            AdmissionStatus.ADMITTED, AdmissionStatus.PENDING
        }


@pytest.mark.parametrize("order", list(itertools.permutations(("A", "B", "C"))))
def test_g04_supersedes_chain_is_order_independent_and_flattened(
    tmp_path: Path, order: tuple[str, ...]
) -> None:
    specs = {
        "A": (None, "admitted"),
        "B": ("A", "admitted"),
        "C": ("B", "admitted"),
    }
    result = _migrate_supersedes_rows(
        tmp_path, [(name, *specs[name]) for name in order]
    )
    assert (
        result.observations["fixture-source::A"].admission.superseded_by
        == "fixture-source::C"
    )
    assert (
        result.observations["fixture-source::B"].admission.superseded_by
        == "fixture-source::C"
    )
    assert (
        result.observations["fixture-source::C"].admission.status
        is AdmissionStatus.ADMITTED
    )


def test_g04_supersedes_cycle_keeps_rows_live_and_emits_hard_issue(
    tmp_path: Path,
) -> None:
    result = _migrate_supersedes_rows(
        tmp_path, [("A", "B", "admitted"), ("B", "A", "admitted")]
    )
    for name in ("A", "B"):
        row = result.observations[f"fixture-source::{name}"]
        assert row.admission.status is AdmissionStatus.ADMITTED
        assert row.admission.superseded_by is None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "supersedes_cycle" in issue.detail
        and "fixture-source::A" in issue.detail
        and "fixture-source::B" in issue.detail
        for issue in result.validation.hard_issues
    )


def test_g04_competing_superseders_keep_green_precedence(tmp_path: Path) -> None:
    result = _migrate_supersedes_rows(
        tmp_path,
        [("A", None, "admitted"), ("B", "A", "admitted"), ("C", "A", "admitted")],
    )
    assert (
        result.observations["fixture-source::A"].admission.superseded_by
        == "fixture-source::C"
    )
    assert (
        result.observations["fixture-source::B"].admission.status
        is AdmissionStatus.ADMITTED
    )
    assert (
        result.observations["fixture-source::C"].admission.status
        is AdmissionStatus.ADMITTED
    )


def _stage_keyed_children(
    tmp_path: Path,
    specs: list[tuple[str, str, str | None]],
) -> Migrator:
    """Build admitted rows, then give each a temperature and a ``:row=`` id.

    ``specs`` entries are ``(stored_id, row_label, temperature_K or None)``.
    """

    from simulator.battery.records import State

    result = _migrate_supersedes_rows(
        tmp_path, [(stored_id, None, "admitted") for stored_id, _row, _temp in specs]
    )
    staged = {}
    for stored_id, row_label, temperature in specs:
        observation = result.observations.pop(f"fixture-source::{stored_id}")
        identity = observation.identity
        if temperature is None:
            identity = replace(identity, temperature_K=None)
        else:
            identity = replace(identity, temperature_K=State.of(Decimal(temperature)))
        observation_id = f"fixture-source::{stored_id}::T={temperature}:row={row_label}"
        staged[observation_id] = replace(
            observation, observation_id=observation_id, identity=identity
        )
    result.observations = staged
    migrator = Migrator(tmp_path)
    migrator.result = result
    return migrator


def test_g04_singleton_explosion_requires_same_measurement_key(tmp_path: Path) -> None:
    migrator = _stage_keyed_children(
        tmp_path,
        [("A-child", "a", "1000"), ("B-child", "b", "2000")],
    )
    parent_a = "fixture-source::A"
    parent_b = "fixture-source::B"
    child_a = "fixture-source::A-child::T=1000:row=a"
    child_b = "fixture-source::B-child::T=2000:row=b"
    migrator._exploded_children = {parent_a: [child_a], parent_b: [child_b]}
    sample = migrator.result.observations[child_a]
    migrator._pending_supersedes = [
        (parent_a, parent_b, sample.experiment_id, sample.locator, "fixture-source.yaml")
    ]

    migrator._apply_supersedes()

    kept = migrator.result.observations[child_a]
    assert kept.admission.status is AdmissionStatus.ADMITTED
    assert kept.admission.superseded_by is None
    assert any(
        "no unique matching row" in (entry.why or "") and "row left live" in (entry.why or "")
        for entry in migrator.result.queue
    )
    _assert_superseded_targets_are_live(migrator.result)


def test_g04_exploded_row_without_correspondence_stays_live(tmp_path: Path) -> None:
    migrator = _stage_keyed_children(
        tmp_path,
        [("A-a", "a", "1000"), ("A-b", "b", "2000"), ("B-a", "a", "1000")],
    )
    parent_a = "fixture-source::A"
    parent_b = "fixture-source::B"
    row_a = "fixture-source::A-a::T=1000:row=a"
    row_b = "fixture-source::A-b::T=2000:row=b"
    successor = "fixture-source::B-a::T=1000:row=a"
    migrator._exploded_children = {
        parent_a: [row_a, row_b],
        parent_b: [successor],
    }
    sample = migrator.result.observations[row_a]
    migrator._pending_supersedes = [
        (parent_a, parent_b, sample.experiment_id, sample.locator, "fixture-source.yaml")
    ]

    migrator._apply_supersedes()

    matched = migrator.result.observations[row_a]
    unmatched = migrator.result.observations[row_b]
    assert matched.admission.status is AdmissionStatus.SUPERSEDED
    assert matched.admission.superseded_by == successor
    assert unmatched.admission.status is AdmissionStatus.ADMITTED
    assert unmatched.admission.superseded_by is None
    assert any(
        row_b in (entry.why or "") and "row left live" in (entry.why or "")
        for entry in migrator.result.queue
    )
    _assert_superseded_targets_are_live(migrator.result)


def test_g04_unexpanded_shell_without_key_follows_exploded_series(
    tmp_path: Path,
) -> None:
    migrator = _stage_keyed_children(
        tmp_path,
        [("shell", "shell", None), ("B-b", "b", "2000"), ("B-a", "a", "1000")],
    )
    shell_id = "fixture-source::shell::T=None:row=shell"
    child_a = "fixture-source::B-a::T=1000:row=a"
    child_b = "fixture-source::B-b::T=2000:row=b"
    parent_b = "fixture-source::B"
    migrator._exploded_children = {parent_b: [child_b, child_a]}
    sample = migrator.result.observations[shell_id]
    migrator._pending_supersedes = [
        (shell_id, parent_b, sample.experiment_id, sample.locator, "fixture-source.yaml")
    ]

    migrator._apply_supersedes()

    shell = migrator.result.observations[shell_id]
    assert shell.admission.status is AdmissionStatus.SUPERSEDED
    assert shell.admission.superseded_by == min(child_a, child_b)
    _assert_superseded_targets_are_live(migrator.result)


@pytest.mark.parametrize("temperature_kind", ["absent", "unknown"])
def test_g04_numeric_row_without_temperature_stays_live(
    tmp_path: Path, temperature_kind: str
) -> None:
    from types import SimpleNamespace

    from simulator.battery.score import comparison_candidates

    migrator = _stage_keyed_children(
        tmp_path,
        [("A", "a", None), ("B-a", "a", "1000"), ("B-b", "b", "2000")],
    )
    staged_id = "fixture-source::A::T=None:row=a"
    old = migrator.result.observations.pop(staged_id)
    temperature = (
        None if temperature_kind == "absent" else State.unknown("not printed")
    )
    old = replace(
        old,
        observation_id="fixture-source::A",
        identity=replace(old.identity, temperature_K=temperature),
        value=Value(ValueKind.POINT, point=Decimal("0.7")),
    )
    migrator.result.observations[old.observation_id] = old
    children = sorted(
        oid for oid in migrator.result.observations if oid != old.observation_id
    )
    for child_id in children:
        migrator.result.observations[child_id] = replace(
            migrator.result.observations[child_id],
            value=Value(ValueKind.POINT, point=Decimal("0.8")),
        )
    migrator._exploded_children = {"fixture-source::B": children}
    migrator._pending_supersedes = [
        (
            old.observation_id,
            "fixture-source::B",
            old.experiment_id,
            old.locator,
            "fixture-source.yaml",
        )
    ]
    before = comparison_candidates(
        SimpleNamespace(observations=migrator.result.observations)
    )

    migrator._apply_supersedes()

    kept = migrator.result.observations[old.observation_id]
    after = comparison_candidates(
        SimpleNamespace(observations=migrator.result.observations)
    )
    diagnostics = [
        entry
        for entry in migrator.result.queue
        if entry.why
        and "no unique matching row" in entry.why
        and "row left live" in entry.why
    ]
    assert kept.admission.status is AdmissionStatus.ADMITTED
    assert kept.admission.superseded_by is None
    assert [row.observation_id for row in after] == [
        row.observation_id for row in before
    ]
    assert len(diagnostics) == 1
    assert old.observation_id in (diagnostics[0].why or "")
    _assert_superseded_targets_are_live(migrator.result)


def test_g04_correspondence_diagnostic_follows_the_final_successor_set(
    tmp_path: Path,
) -> None:
    migrator = _stage_keyed_children(
        tmp_path,
        [
            ("A-a", "a", "1000"),
            ("A-b", "b", "2000"),
            ("A-c", "c", "1800"),
            ("E-b", "b", "2000"),
            ("B-a", "a", "1000"),
            ("C-x", "x", "1500"),
            ("D-a", "a", "1000"),
        ],
    )
    ids = {
        "A-a": "fixture-source::A-a::T=1000:row=a",
        "A-b": "fixture-source::A-b::T=2000:row=b",
        "A-c": "fixture-source::A-c::T=1800:row=c",
        "E-b": "fixture-source::E-b::T=2000:row=b",
        "B-a": "fixture-source::B-a::T=1000:row=a",
        "C-x": "fixture-source::C-x::T=1500:row=x",
        "D-a": "fixture-source::D-a::T=1000:row=a",
    }
    migrator._exploded_children = {
        "fixture-source::A": [ids["A-a"], ids["A-b"], ids["A-c"]],
        "fixture-source::E": [ids["E-b"]],
        "fixture-source::B": [ids["B-a"]],
        "fixture-source::C": [ids["C-x"]],
        "fixture-source::D": [ids["D-a"]],
    }
    sample = migrator.result.observations[ids["A-a"]]
    migrator._pending_supersedes = [
        (
            "fixture-source::A",
            successor,
            sample.experiment_id,
            sample.locator,
            "fixture-source.yaml",
        )
        for successor in (
            "fixture-source::E",
            "fixture-source::B",
            "fixture-source::C",
            "fixture-source::D",
        )
    ]

    migrator._apply_supersedes()

    matched_later = migrator.result.observations[ids["A-a"]]
    matched_earlier = migrator.result.observations[ids["A-b"]]
    unmatched = migrator.result.observations[ids["A-c"]]
    assert matched_later.admission.status is AdmissionStatus.SUPERSEDED
    assert matched_later.admission.superseded_by == ids["D-a"]
    assert matched_earlier.admission.status is AdmissionStatus.SUPERSEDED
    assert matched_earlier.admission.superseded_by == ids["E-b"]
    assert unmatched.admission.status is AdmissionStatus.ADMITTED
    assert unmatched.admission.superseded_by is None
    live_notes = [
        entry
        for entry in migrator.result.queue
        if entry.why and "row left live" in entry.why
    ]
    assert len(live_notes) == 1
    assert ids["A-c"] in (live_notes[0].why or "")
    assert ids["A-a"] not in (live_notes[0].why or "")
    assert ids["A-b"] not in (live_notes[0].why or "")
    _assert_superseded_targets_are_live(migrator.result)


def test_g04_rejected_superseder_does_not_supersede_measured_row(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "measured_row",
            "type": "alpha",
            "locator": {"table": "1"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        },
        {
            "observation_id": "rejected_figure_row",
            "supersedes": "measured_row",
            "type": "alpha",
            "locator": {"figure": "1"},
            "phase": "gas",
            "values": {
                "quantity": "unknown_figure_quantity",
                "method_class": "figure_only",
                "admission_status": "rejected",
                "reason": "figure digitisation has no numeric point",
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)

    measured = result.observations["fixture-source::measured_row"]
    assert measured.admission.status in {
        AdmissionStatus.ADMITTED,
        AdmissionStatus.PENDING,
    }
    assert measured.admission.superseded_by is None


def test_g04_dangling_superseder_chain_keeps_measured_row_live(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "measured_row",
            "type": "alpha",
            "locator": {"table": "1"},
            "phase": "gas",
            "values": {
                "quantity": "literature_vaporization_coefficient",
                "method_class": "measured_direct",
                "admission_status": "admitted",
            },
        }
    ]
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    measured = result.observations["fixture-source::measured_row"]
    dangling = replace(
        measured,
        observation_id="fixture-source::dangling_intermediate",
        admission=replace(
            measured.admission,
            status=AdmissionStatus.SUPERSEDED,
            reason="superseded test fixture",
            superseded_by="fixture-source::missing_terminal",
        ),
    )
    result.observations[dangling.observation_id] = dangling
    migrator = Migrator(tmp_path)
    migrator.result = result
    migrator._pending_supersedes = [
        (
            measured.observation_id,
            dangling.observation_id,
            measured.experiment_id,
            measured.locator,
            "data/literature/extracts/fixture-source.yaml",
        )
    ]

    migrator._apply_supersedes()

    assert measured.admission.status is AdmissionStatus.ADMITTED
    assert measured.admission.superseded_by is None


def test_h03_pressure_conversion_keeps_derivation_trail() -> None:
    env = pressure_from_equipment(
        {"chamber_pressure": {"value": 1, "units": "Torr", "locator": {"table": "1"}}}
    )
    located = env.total_pressure_Pa
    assert located.state.is_value
    assert located.inference is not None
    assert located.inference.relation == "Torr_to_Pa"
    joined = " ".join(located.inference.inputs)
    assert "101325" in joined and "760" in joined
    params = dict(located.inference.parameters)
    assert "factor" in params
    assert params["factor"].state.is_value
    tiny = pressure_from_equipment(
        {
            "chamber_pressure": {
                "value": "1.0e-05",
                "units": "Torr",
                "locator": {"figure": "8"},
            }
        }
    )
    assert tiny.total_pressure_Pa.inference is not None
    assert tiny.total_pressure_Pa.inference.relation == "Torr_to_Pa"
    assert tiny.total_pressure_Pa.state.value.point == as_decimal("1.0e-05") * as_decimal(
        "101325"
    ) / as_decimal("760")


def test_g03_blank_pressure_unit_is_unknown() -> None:
    pa, why = convert_pressure_to_pa(1, "")
    assert pa is None
    assert why is not None and "unit" in why.lower()
    env = pressure_from_equipment(
        {"chamber_pressure": {"value": 1, "units": "", "locator": {"table": "1"}}}
    )
    assert env.total_pressure_Pa.state.is_unknown
    pa_ok, trail = convert_pressure_to_pa(1, "Pa")
    assert pa_ok == 1
    assert trail == "identity:Pa"
    t, why_t = convert_temperature_to_k(1200, "")
    assert t is None
    assert why_t is not None and "unit" in why_t.lower()
    area, why_a = convert_area_to_m2(1, "")
    assert area is None and why_a is not None
    mass, why_m = convert_mass_to_kg(1, "")
    assert mass is None and why_m is not None


def test_h13_canonical_phase_tokens_are_reviewed_identity() -> None:
    from simulator.battery.migrate import PHASE_MAP

    for spelling, token in (
        ("aq", Phase.AQ),
        ("glass", Phase.GLASS),
        ("supercooled_l", Phase.SUPERCOOLED_L),
        ("g", Phase.G),
        ("cr", Phase.CR),
        ("l", Phase.L),
    ):
        assert PHASE_MAP[spelling] is token
        mapped, why = map_phase(spelling)
        assert mapped.is_value and mapped.value is token and why is None
    mapped, why = map_phase("silicate_melt")
    assert mapped.is_unknown and why is not None


def test_g01_map_phase_refuses_heuristics() -> None:
    mapped, why = map_phase("gas")
    assert mapped.is_value and mapped.value is Phase.G and why is None
    mapped, why = map_phase("condensed_solid")
    assert mapped.is_value and mapped.value is Phase.CR and why is None
    mapped, why = map_phase("")
    assert mapped.is_unknown and why is not None
    mapped, why = map_phase("ref")
    assert mapped.is_unknown
    mapped, why = map_phase("cr,l")
    assert mapped.is_unknown
    mapped, why = map_phase("silicate_melt")
    assert mapped.is_unknown


def test_t998_alkali_source_tokens_are_closed() -> None:
    from simulator.battery.migrate import PHASE_MAP, REGIME_TO_METHOD

    assert (
        QUANTITY_ALIASES["evaporation_coefficient_gamma_equals_alpha"]
        is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    )
    assert QUANTITY_ALIASES["log10_K_star_equilibrium_fit"] is Quantity.LOG10_K_STAR
    assert PHASE_MAP["basaltic_silicate_melt"] is Phase.L
    assert PHASE_MAP["silicate_melt_ferrobasalt_FCMAS"] is Phase.L
    assert (
        REGIME_TO_METHOD["langmuir_free_evaporation"]
        is MethodToken.LANGMUIR_FREE_EVAPORATION
    )
    assert (
        REGIME_TO_METHOD["open_furnace_apparent"]
        is MethodToken.LANGMUIR_FREE_EVAPORATION
    )


def test_t998_admitted_source_rows_survive_migration(tmp_path: Path) -> None:
    zhang = _migrate_real_extract(tmp_path / "zhang", "kems-006-zhang-2021.yaml")
    zhang_rows = [
        observation
        for observation in zhang.observations.values()
        if "zhang_2021_table4_" in observation.observation_id
    ]
    products = sorted(
        (str(observation.identity.species.formula), str(observation.identity.temperature_K.value), str(observation.value.point))
        for observation in zhang_rows
        if observation.value.kind is ValueKind.POINT
    )
    assert products == [
        ("K", "1473.15", "6.9E-8"),
        ("K", "1673.15", "0.00000111"),
        ("Na", "1473.15", "0.0000032"),
        ("Na", "1673.15", "0.0000262"),
    ]
    assert {row.provenance["scoring"]["oxide_formula"] for row in zhang_rows} == {
        "K2O",
        "Na2O",
    }
    assert {row.provenance["scoring"]["gamma_basis"] for row in zhang_rows} == {
        "single_cation",
    }
    assert {
        row.provenance["scoring"]["gamma_basis_quote"] for row in zhang_rows
    } == {
        "Table 4 p. 74: ΓNaO0.5",
        "Table 4 p. 74: ΓKO0.5",
    }

    sossi = _migrate_real_extract(tmp_path / "sossi", "kems-012-sossi-2019.yaml")
    target = [
        observation
        for observation in sossi.observations.values()
        if observation.observation_id.split("::", 1)[-1]
        in {
            "sossi_2019_na_logKstar_table3",
            "sossi_2019_k_logKstar_table3",
            "sossi_2019_na_table4_gamma_this_work",
            "sossi_2019_k_table4_gamma_this_work",
        }
    ]
    assert len(target) == 4
    assert {quantity_token(row.identity) for row in target} == {
        Quantity.LOG10_K_STAR,
        Quantity.ACTIVITY_COEFFICIENT,
    }
    assert all(row.derived_from == ("tables:kems-012-sossi-2019",) for row in target)
    assert {row.derivation.relation for row in target if row.derivation} == {
        "nonlinear_least_squares_K_star_fit",
        "K_star_over_alpha_e_and_pure_system_equilibrium_constant",
    }


def test_t998_plante_reduced_derivation_keeps_registered_table_input(
    tmp_path: Path,
) -> None:
    plante = _migrate_real_extract(
        tmp_path / "plante",
        "kems-042-plante-1979.yaml",
        use_repository_index_row=True,
    )
    row = plante.observations[
        "kems-042-plante-1979::plante1979_table2_k2o_s1104_000_1302K"
    ]

    assert row.evidence.original_method_class == "directly_reduced_measurement"
    assert row.derivation is not None
    assert row.derivation.relation.startswith("Plante eq. (2)")
    assert "eq. (3)" in row.derivation.relation
    assert "eq. (5)" in row.derivation.relation
    assert row.derivation.inputs == ("tables:kems-042-plante-1979",)

    work = plante.works[plante.experiments[row.experiment_id].work_id]
    assert row.derivation.inputs[0] in {
        asset.asset_id for asset in work.source_files.files
    }


def test_g2_paren_phase_aliases_map_gas_and_condensed() -> None:
    mapped, why = map_phase("(g)")
    assert mapped.is_value and mapped.value is Phase.G and why is None
    mapped, why = map_phase("(c)")
    assert mapped.is_value and mapped.value is Phase.CR and why is None
    # Multi-phase spans stay unknown in this lane.
    mapped, why = map_phase("(c,l)")
    assert mapped.is_unknown and why == "(c,l)"
    mapped, why = map_phase("(c, l)")
    assert mapped.is_unknown and why == "(c, l)"


def test_g2_paren_phase_alias_mutation_proof() -> None:
    from simulator.battery import migrate as migrate_mod

    live_g, _ = map_phase("(g)")
    live_c, _ = map_phase("(c)")
    assert live_g.is_value and live_g.value is Phase.G
    assert live_c.is_value and live_c.value is Phase.CR

    saved_g = migrate_mod.PHASE_MAP.pop("(g)", None)
    saved_c = migrate_mod.PHASE_MAP.pop("(c)", None)
    try:
        mutant_g, why_g = map_phase("(g)")
        mutant_c, why_c = map_phase("(c)")
        assert mutant_g.is_unknown and "not in the closed automatic map" in (mutant_g.reason or "")
        assert mutant_c.is_unknown and "not in the closed automatic map" in (mutant_c.reason or "")
        assert why_g == "(g)" and why_c == "(c)"
    finally:
        if saved_g is not None:
            migrate_mod.PHASE_MAP["(g)"] = saved_g
        if saved_c is not None:
            migrate_mod.PHASE_MAP["(c)"] = saved_c

    restored_g, _ = map_phase("(g)")
    restored_c, _ = map_phase("(c)")
    assert restored_g.is_value and restored_g.value is Phase.G
    assert restored_c.is_value and restored_c.value is Phase.CR


def test_g2_paren_phase_migrate_kelley_record(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "kelley-1960-usbm-b584", "table-332.json")
    result = migrate(root, write=False)
    obs_list = [
        obs
        for oid, obs in result.observations.items()
        if "table-332" in oid or "kelley-1960-usbm-b584" in oid
    ]
    assert obs_list
    phases = [obs.identity.species.phase for obs in obs_list]
    assert all(phase.is_value and phase.value is Phase.G for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )


def test_h01_bare_series_T_P_without_units_stay_unknown(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = ""
    row["values"]["series"] = [{"T": 1200, "P": 1}]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    temp = obs.identity.temperature_K
    assert temp is not None and temp.is_unknown, temp
    assert temp.value != 1200
    assert obs.value.kind is not ValueKind.POINT or obs.value.point != 1
    assert obs.value.kind.value in {"unavailable", "unknown"} or (
        obs.value.kind is ValueKind.POINT and obs.derivation is None
    )
    assert obs.value.kind is ValueKind.UNAVAILABLE
    axes = {(e.observation_id, tuple(e.axes), e.why) for e in result.queue}
    assert any("temperature" in (why or "").lower() or "temperature_K" in axes_
               for _oid, axes_, why in axes)
    assert any("value" in axes_ or "unit" in (why or "").lower()
               for _oid, axes_, why in axes)


def test_h01_explicit_T_K_pressure_atm_still_converts(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    assert all(p.identity.temperature_K is not None and p.identity.temperature_K.is_value
               for p in points)
    assert {p.identity.temperature_K.value for p in points} == {1200, 1300} or (
        {float(p.identity.temperature_K.value) for p in points} == {1200.0, 1300.0}
    )
    assert all(p.value.kind is ValueKind.POINT for p in points)
    assert all(
        p.derivation is not None
        and p.derivation.relation == (
            "atm_to_Pa; species.phase=gas derived from quantity=p_sat "
            "(p_sat identity names the gas species)"
        )
        for p in points
    )


def test_point_selection_preserves_approximate_flag(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["values"].pop("series")
    row["values"].update(
        {"T_K": 1200, "pressure_atm": 1.0, "approximate": True}
    )

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    obs = result.observations["fixture-source::na_psat"]

    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == 101325
    assert obs.value.approximate is True
    assert obs.identity.temperature_K is not None
    assert obs.identity.temperature_K.is_value
    assert obs.identity.temperature_K.value == 1200


def test_h01_blank_sample_area_units_queued_and_sample_transferred(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "sample_surface_area": {"value": 1, "units": "", "locator": {"table": "I"}},
        "sample": {
            "mass": {"value": 10, "units": "g", "locator": {"table": "I"}},
            "form": "powder",
        },
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    exp = result.experiments[obs.experiment_id]
    area = exp.apparatus.geometry.exposed_area_m2 if exp.apparatus and exp.apparatus.geometry else None
    assert area is not None and area.state.is_unknown
    assert any(
        "exposed_area" in (e.axes or ()) or "area" in (e.why or "").lower()
        for e in result.queue
    )
    assert exp.sample.mass_kg is not None
    assert exp.sample.mass_kg.state.is_value
    assert exp.sample.form is not None
    assert exp.sample.form.state.is_value
    assert exp.sample.form.state.value == "powder"


def test_h01_sample_volume_cm3_is_stored_as_m3(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "sample_volume_cm3": {"value": 0.25, "locator": {"table": "2"}},
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    volume = result.experiments[obs.experiment_id].sample.volume_m3
    assert volume is not None and volume.state.is_value
    assert volume.state.value.kind is ValueKind.POINT
    assert volume.state.value.point == as_decimal("0.00000025")
    assert volume.inference is not None
    assert volume.inference.relation == "cm3_to_m3"
    assert volume.locator is not None and volume.locator.table == "2"


def test_takeda_single_printed_gamma_values_migrate_as_points(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "takeda-1990-sn-slag-matertrans.yaml"
    )
    expected = {
        "takeda_1990_table1_gamma_10": Decimal("1.8"),
        "takeda_1990_table1_gamma_13": Decimal("1.7"),
        "takeda_1990_table1_gamma_14": Decimal("0.9"),
    }

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    for observation_id, gamma in expected.items():
        matches = [
            observation
            for key, observation in result.observations.items()
            if key.endswith(f"::{observation_id}")
        ]
        assert len(matches) == 1, observation_id
        observation = matches[0]
        assert observation.admission.status is AdmissionStatus.ADMITTED
        assert observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == gamma


def test_h02_activity_coefficient_uses_gamma_not_pressure(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["observations"] = [
        {
            "observation_id": "gao15_gamma_s1_1low_polytherm",
            "type": "activity_coefficient",
            "locator": {"table": "S1"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "method_class": "measured_direct",
                "series": [
                    {
                        "T_K": 1586.4,
                        "gamma": 0.0632,
                        "gamma_SD": 0.0558,
                        "p_Ga_Pa": 4.04e-05,
                        "p_O2_calc_Pa": 0.000112,
                        "K_Ga": 4.54e-15,
                        "delta_IW": 1.66,
                    }
                ],
            },
        },
        {
            "observation_id": "gao15_gamma_s2_1low_isotherm",
            "type": "activity_coefficient",
            "locator": {"table": "S2"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1741.9, "gamma": 0.0353, "p_Ga_Pa": 0.000881}],
            },
        },
        {
            "observation_id": "ino15_gamma_s1_1low_polytherm",
            "type": "activity_coefficient",
            "locator": {"table": "S1b"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1586.4, "gamma": 0.0527, "p_In_Pa": 6.69e-05}],
            },
        },
        {
            "observation_id": "ino15_gamma_s2_1low_isotherm",
            "type": "activity_coefficient",
            "locator": {"table": "S2b"},
            "phase": "gas",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_coefficient",
                "series": [{"T_K": 1741.9, "gamma": 0.0211, "p_In_Pa": 0.00032}],
            },
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    expected = {
        "gao15_gamma_s1_1low_polytherm": "0.0632",
        "gao15_gamma_s2_1low_isotherm": "0.0353",
        "ino15_gamma_s1_1low_polytherm": "0.0527",
        "ino15_gamma_s2_1low_isotherm": "0.0211",
    }
    for suffix, gamma in expected.items():
        points = [
            o
            for o in result.observations.values()
            if o.observation_id.startswith(f"fixture-source::{suffix}::")
        ]
        assert len(points) == 1, suffix
        obs = points[0]
        assert quantity_token(obs.identity) is Quantity.ACTIVITY_COEFFICIENT
        assert obs.value.kind is ValueKind.POINT
        assert obs.value.point == as_decimal(gamma)
        pressures = []
        # The stored coefficient must not equal a pressure column from its row.
        assert obs.value.point != as_decimal("4.04e-05")
        assert obs.value.point != as_decimal("0.000881")
        assert obs.value.point != as_decimal("6.69e-05")
        assert obs.value.point != as_decimal("0.00032")
        del pressures
    queued_axes = " ".join(e.why or "" for e in result.queue)
    assert "p_Ga_Pa" in queued_axes or "ancillary" in queued_axes.lower()


def test_h02_bischof_stored_gammas_match_source() -> None:
    from decimal import Decimal

    src_path = REPO_ROOT / "data" / "literature" / "extracts" / "kems-137-bischof-2023.yaml"
    store_path = (
        REPO_ROOT / "data" / "literature" / "extracts-v2" / "kems-137-bischof-2023.yaml"
    )
    if not src_path.is_file() or not store_path.is_file():
        pytest.skip("Bischof extract or v2 store not present")
    source = yaml.safe_load(src_path.read_text(encoding="utf-8"))
    stored = yaml.safe_load(store_path.read_text(encoding="utf-8"))
    first = {
        "bischof_2023_gao15_gamma_s1_1low_polytherm": ("1586.4", Decimal("0.0632")),
        "bischof_2023_gao15_gamma_s2_1low_isotherm": ("1741.9", Decimal("0.0353")),
        "bischof_2023_ino15_gamma_s1_1low_polytherm": ("1586.4", Decimal("0.0527")),
        "bischof_2023_ino15_gamma_s2_1low_isotherm": ("1741.9", Decimal("0.0211")),
    }
    by_id = {o["observation_id"]: o for o in stored["observations"]}
    for suffix, (temperature, gamma) in first.items():
        prefix = f"kems-137-bischof-2023::{suffix}::T={temperature}:"
        matches = [
            obs
            for oid, obs in by_id.items()
            if oid.startswith(prefix)
            and (obs.get("identity") or {}).get("quantity", {}).get("value")
            == "activity_coefficient"
            and Decimal(str((obs.get("value") or {}).get("point"))) == gamma
        ]
        assert len(matches) == 1, (suffix, temperature, gamma, matches)
        obs = matches[0]
        q = obs["identity"]["quantity"]
        assert q.get("value") == "activity_coefficient"
        assert Decimal(str(obs["value"]["point"])) == gamma
    source_gammas: list[Decimal] = []
    source_pressures: set[Decimal] = set()
    n_series_gamma = 0
    species = source.get("species") or {}
    for body in species.values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations") or []:
            values = row.get("values") or {}
            if values.get("quantity") != "activity_coefficient":
                continue
            if "gamma" in values:
                source_gammas.append(Decimal(str(values["gamma"])))
            for item in values.get("series") or []:
                if not isinstance(item, dict):
                    continue
                if "gamma" in item:
                    source_gammas.append(Decimal(str(item["gamma"])))
                    n_series_gamma += 1
                for pk in ("p_Ga_Pa", "p_In_Pa"):
                    if pk in item:
                        source_pressures.add(Decimal(str(item[pk])))
    stored_vals = []
    stored_series = 0
    for obs in stored["observations"]:
        q = obs.get("identity", {}).get("quantity", {})
        if q.get("value") != "activity_coefficient":
            continue
        if obs.get("value", {}).get("kind") != "point":
            continue
        stored_vals.append(Decimal(str(obs["value"]["point"])))
        if "::T=" in str(obs.get("observation_id") or ""):
            stored_series += 1
    assert n_series_gamma == 128
    assert stored_series == 128
    # Two preferred scalar γ averages were previously stored as T_range intervals.
    assert len(stored_vals) == 130
    gamma_set = set(source_gammas)
    for val in stored_vals:
        assert val in gamma_set
        assert val not in source_pressures


def _j01_series_fixture(quantity: str, series: list[dict]) -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["units"] = ""
    row["values"]["quantity"] = quantity
    row["values"]["series"] = series
    return extract


def test_j01_missing_declared_psat_does_not_take_gamma_or_value(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "pure_Psat", [{"T_K": 1200, "gamma": 0.06, "value": 0.7}]
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.point is None or obs.value.point != as_decimal("0.06")
    assert obs.value.point != as_decimal("0.7")
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_missing_declared_activity_does_not_take_gamma(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "activity", [{"T_K": 1200, "gamma": 0.06, "value": 0.7}]
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_missing_declared_alpha_does_not_take_gamma(tmp_path: Path) -> None:
    extract = _j01_series_fixture(
        "evaporation_coefficient_alpha",
        [{"T_K": 1200, "gamma": 0.06, "value": 0.7}],
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_j01_explicit_pressure_atm_control_still_lifts(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if o.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(points) == 2
    assert all(quantity_token(p.identity) is Quantity.P_SAT for p in points)
    assert all(p.value.kind is ValueKind.POINT for p in points)
    values = sorted(p.value.point for p in points)
    assert values[0] == atm_to_pa("1")
    assert values[1] == atm_to_pa("2")


def test_j01_fedkin_alpha_series_not_mass_loss_rate(tmp_path: Path) -> None:
    src = REPO_ROOT / "data" / "literature" / "extracts" / "kems-005-fedkin-2006.yaml"
    extract = load_cached_safe_yaml(src.read_text(encoding="utf-8"))
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "extracts" / "kems-005-fedkin-2006.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "data" / "literature" / "extracts" / "fixture-source.yaml").unlink()
    result = migrate(root, write=False)
    alpha_points = [
        o
        for o in result.observations.values()
        if "_table3::T=" in o.observation_id
        or "sio_hashimoto_table3_complete_b1::T=" in o.observation_id
    ]
    assert len(alpha_points) == 12
    for obs in alpha_points:
        assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        assert quantity_token(obs.identity) is not Quantity.MASS_LOSS_RATE
        assert obs.value.kind is ValueKind.POINT
    fe0 = next(
        o
        for o in alpha_points
        if "fe_hashimoto_langmuir_table3::T=1973" in o.observation_id
    )
    assert fe0.value.point == as_decimal("0.23")
    assert float(fe0.identity.temperature_K.value) == 1973.0


# Source-contract tables for the series census. Duplicated here on purpose:
# the test must not import the production selector as its expected-value oracle.
_CENSUS_QUANTITY_ALIASES = {
    "pure_Psat": "p_sat",
    "vapor_pressure": "p_sat",
    "partial_pressure": "p_partial",
    "partial_pressure_O2": "p_partial",
    "potassium_partial_pressure_as_published": "p_partial",
    "deltafG": "delta_fG",
    "delta_fG": "delta_fG",
    "delta_fG_kJ_mol": "delta_fG",
    "log10_Kf": "log10_Kf",
    "log10_kf": "log10_Kf",
    "log10_K_star_equilibrium_fit": "log10_K_star",
    "activity": "activity",
    "activity_coefficient": "activity_coefficient",
    "activity_coefficient_this_work": "activity_coefficient",
    "wagner_interaction_parameter": "interaction_parameter",
    "literature_vaporization_coefficient": "evaporation_coefficient_alpha",
    "alpha": "evaporation_coefficient_alpha",
    "evaporation_coefficient_alpha": "evaporation_coefficient_alpha",
    "evaporation_coefficient_gamma_equals_alpha": "evaporation_coefficient_alpha",
    "o2_yield": "o2_yield",
    "mass_loss_fraction": "mass_loss_fraction",
    "bulk_mass_loss_wt_pct": "mass_loss_fraction",
    "non_condensed_mass_loss_fraction": "mass_loss_fraction",
    "ion_current_ratio": "ion_intensity_ratio",
    "ion_intensity_ratio": "ion_intensity_ratio",
    "open_furnace_residue_composition_vs_time": "residue_component_composition",
    "residue_composition_vs_time": "residue_component_composition",
    "epma_residue_composition_vs_time": "residue_component_composition",
}
_CENSUS_CLOSED_QUANTITIES = {
    "p_sat",
    "p_partial",
    "p_reference",
    "fugacity",
    "log10_Kf",
    "log10_K_star",
    "activity",
    "activity_coefficient",
    "evaporation_coefficient_alpha",
    "mass_loss_areal_density",
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
    "fe3_fe2_ratio",
    "ion_intensity_ratio",
    "delta_fG",
    "H_minus_H298",
    "partial_molar_enthalpy",
    "enthalpy_of_vaporization_2nd_law",
    "enthalpy_of_vaporization_3rd_law",
    "cp",
    "S",
    "evaporation_rate",
    "mass_loss_rate",
    "wall_deposit_mass",
    "transition_temperature",
    "viscosity",
    "density",
    "electrical_conductivity",
    "isotope_delta",
    "condensate_composition",
    "liquidus_composition",
    "evolved_gas_yield",
    "ion_intensity",
    "interaction_parameter",
    "residue_component_composition",
}
_CENSUS_TYPE_QUANTITY = {
    "psat_series": "p_sat",
    "gibbs_table": "delta_fG",
    "activity_coefficient": "activity_coefficient",
    "alpha": "evaporation_coefficient_alpha",
    "rate_series": "mass_loss_rate",
    "transition_point": "transition_temperature",
}
_CENSUS_UNIT_QUANTITY = {
    "dimensionless alpha vs t_k": "evaporation_coefficient_alpha",
    "dimensionless activity": "activity",
}
_CENSUS_PERCENT_FRACTION_QUANTITIES = {
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
}
_CENSUS_PRESSURE_FIELDS = (
    ("pressure_atm", "atm"),
    ("p_atm", "atm"),
    ("P_atm", "atm"),
    ("pressure_bar", "bar"),
    ("P_bar", "bar"),
    ("p_bar", "bar"),
    ("P_Pa", "Pa"),
    ("pressure_Pa", "Pa"),
    ("p_Pa", "Pa"),
    ("Pb_Torr", "Torr"),
    ("Pbar_Torr", "Torr"),
    ("Po_Torr", "Torr"),
)
_ATM = as_decimal("101325")
_BAR = as_decimal("100000")
_TORR = _ATM / as_decimal("760")


def _census_declared_quantity(obs_type: str | None, values: dict, units: str) -> str | None:
    raw = values.get("quantity") if isinstance(values, dict) else None
    if isinstance(raw, str) and raw in _CENSUS_QUANTITY_ALIASES:
        return _CENSUS_QUANTITY_ALIASES[raw]
    if isinstance(raw, str) and raw in _CENSUS_CLOSED_QUANTITIES:
        return raw
    if raw is None or raw == "":
        unit_key = str(units or "").strip().lower()
        if unit_key in _CENSUS_UNIT_QUANTITY:
            return _CENSUS_UNIT_QUANTITY[unit_key]
        if obs_type in _CENSUS_TYPE_QUANTITY:
            return _CENSUS_TYPE_QUANTITY[obs_type]
        return None
    return None


def _census_expected_point(item: dict, q_token: str | None, units: str):
    from decimal import Decimal, InvalidOperation

    def _num(raw):
        if raw is None or raw == "" or isinstance(raw, bool):
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            return None

    if q_token is None:
        return None
    if q_token == "activity_coefficient":
        if "gamma" in item:
            return _num(item.get("gamma"))
        if "activity_coefficient" in item:
            return _num(item.get("activity_coefficient"))
        return None
    if q_token == "activity":
        return _num(item["activity"]) if "activity" in item else None
    if q_token == "evaporation_coefficient_alpha":
        if "alpha" in item:
            return _num(item["alpha"])
        # Zhang Table 4 prints alpha and MELTS Gamma separately; this task
        # admits the measured alpha·Gamma product for implied-alpha scoring.
        if "alpha_times_Gamma" in item:
            return _num(item["alpha_times_Gamma"])
        return None
    if q_token == "evaporation_rate":
        candidates = [
            (key, value)
            for key, value in item.items()
            if isinstance(key, str)
            and (key == q_token or key.startswith(f"{q_token}_"))
        ]
        if len(candidates) != 1:
            return None
        return _num(candidates[0][1])
    if q_token == "mass_loss_areal_density":
        amount = _num(item.get("mass_loss_areal_density"))
        converted, _relation = convert_areal_mass_to_kg_per_m2(amount, units)
        return converted
    if q_token == "transition_temperature":
        if "T_K" in item:
            return _num(item.get("T_K"))
        if "T_C" in item:
            amount = _num(item.get("T_C"))
            return None if amount is None else amount + as_decimal("273.15")
        return None
    if q_token in _CENSUS_PERCENT_FRACTION_QUANTITIES:
        candidates = [
            (key, value)
            for key, value in item.items()
            if isinstance(key, str)
            and (
                (
                    q_token in {"mass_loss_fraction", "mass_loss_fraction_vs_T"}
                    and "mass_loss" in key.lower()
                )
                or (
                    q_token == "yield_fraction"
                    and key in {"yield_fraction", "mass_yield_percent"}
                )
                or (
                    q_token == "o2_yield"
                    and key in {"o2_yield", "fraction_of_feedstock_oxygen_percent"}
                )
            )
        ]
        if not candidates:
            return None
        key, raw = candidates[0]
        amount = _num(raw)
        if amount is None:
            return None
        key_lower = key.lower()
        unit_lower = str(units or "").strip().lower().replace(" ", "")
        if key_lower.endswith(("_pct", "_percent", "_wt_pct")) or unit_lower in {
            "percent",
            "pct",
            "wt_percent",
            "wt%",
            "wt_pct",
            "%",
        }:
            return amount / as_decimal("100")
        return amount
    if q_token == "H_minus_H298":
        # Heat-content series (first adopter: abdul-2023-cao-sio2-calphad Tables 3-4,
        # printed in J per mol of formula unit). The store keeps H(T)-H(298.15) in kJ/mol
        # (migrate derivation J_to_kJ_exact), so a J/mol cell is divided by exactly 1000:
        # 147,433.0 J/mol / 1000 = 147.433 kJ/mol. Any other unit is not inferred.
        for key in (
            "enthalpy_increment",
            "H_minus_H298",
            "minus_delta_H_cool_kJ_mol-1",
            "value",
        ):
            if key not in item:
                continue
            amount = _num(item.get(key))
            if amount is None:
                return None
            unit = str(units or "").strip().lower().replace(" ", "")
            if unit in {"j/mol", "jmol-1", "jmol^-1", "j_per_mol"}:
                return amount / as_decimal("1000")
            if unit in {"kj/mol", "kjmol-1", "kjmol^-1", "kj_per_mol"}:
                return amount
            return None
        return None
    if q_token == "delta_fG":
        if "delta_fG" in item:
            return _num(item.get("delta_fG"))
        if "value" in item:
            return _num(item.get("value"))
        return None
    if q_token == "fugacity":
        return _num(item.get("fugacity_Pa")) if "fugacity_Pa" in item else None
    if q_token in {"p_sat", "p_partial"}:
        for key, unit in _CENSUS_PRESSURE_FIELDS:
            if key not in item:
                continue
            amount = _num(item.get(key))
            if amount is None:
                return None
            if unit == "atm":
                return amount * _ATM
            if unit == "bar":
                return amount * _BAR
            if unit == "Torr":
                return amount * _TORR
            return amount
        if q_token == "p_partial":
            for key in ("p_Ga_Pa", "p_In_Pa", "p_O2_calc_Pa"):
                if key in item:
                    return _num(item.get(key))
        if "P" in item or "p" in item:
            amount = _num(item.get("P", item.get("p")))
            unit = str(units or "").strip().lower().replace(" ", "")
            if amount is None or not unit:
                return None
            if unit in {"pa", "pascal", "pascals"}:
                return amount
            if unit in {"atm", "atmosphere", "atmospheres"}:
                return amount * _ATM
            if unit in {"bar"}:
                return amount * _BAR
            if unit in {"torr", "mmhg"}:
                return amount * _TORR
            return None
        return None
    try:
        quantity = Quantity(q_token)
    except ValueError:
        return None
    for field in QUANTITY_SOURCE_FIELDS.get(quantity, ()):
        if field in item:
            return _num(item.get(field))
    return None


def _series_census(
    extracts: Path, extracts_v2: Path
) -> tuple[dict[str, int], list[str], int, int, dict[str, dict[str, int]]]:
    from decimal import Decimal, InvalidOperation

    census: dict[str, int] = {}
    per_source: dict[str, dict[str, int]] = {}
    mismatches: list[str] = []
    n_numeric = 0
    n_unavailable = 0
    for src_path in sorted(extracts.glob("*.yaml")):
        source = yaml.safe_load(src_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            continue
        store_path = extracts_v2 / src_path.name
        if not store_path.is_file():
            continue
        stored = yaml.safe_load(store_path.read_text(encoding="utf-8"))
        stored_observations = [
            o
            for o in (stored.get("observations") or [])
            if isinstance(o, dict)
        ]
        by_id = {
            o["observation_id"]: o
            for o in stored_observations
        }
        source_id = str(source.get("source_id") or src_path.stem)
        used_stable_ids: set[str] = set()
        for source_species, body in (source.get("species") or {}).items():
            if not isinstance(body, dict):
                continue
            for row in body.get("observations") or []:
                values = row.get("values") or {}
                series = values.get("series") if isinstance(values, dict) else None
                if not isinstance(series, list) or not series:
                    continue
                obs_type = row.get("type") if isinstance(row.get("type"), str) else None
                units = str(row.get("units") or "")
                q_token = _census_declared_quantity(obs_type, values, units)
                raw_id = str(row.get("observation_id") or "")
                for index, item in enumerate(series):
                    if not isinstance(item, dict):
                        continue
                    oid = f"{source_id}::{raw_id}::point:{index}"
                    if q_token == "residue_component_composition":
                        residue_fields = residue_component_source_fields()
                        for field in residue_fields:
                            if field not in item:
                                continue
                            raw_amount = item[field]
                            expected_amount = None
                            if raw_amount is not None and raw_amount != "":
                                try:
                                    expected_amount = Decimal(str(raw_amount))
                                except (InvalidOperation, ValueError, TypeError):
                                    expected_amount = None
                            if expected_amount is None:
                                n_unavailable += 1
                                continue
                            component = (
                                source_species
                                if field == "residue_ppm"
                                else field.removesuffix("_wt_pct")
                            )
                            stable_points = [
                                observation
                                for observation in stored_observations
                                if str(observation.get("observation_id") or "").startswith(
                                    f"{source_id}::{raw_id}::"
                                )
                                and str(observation.get("observation_id") or "")
                                not in used_stable_ids
                                and ((observation.get("identity") or {}).get("quantity") or {}).get(
                                    "value"
                                )
                                == q_token
                                and ((observation.get("identity") or {}).get("species") or {}).get(
                                    "formula"
                                )
                                == component
                            ]
                            stored_obs = next(
                                (
                                    observation
                                    for observation in stable_points
                                    if (observation.get("value") or {}).get("kind")
                                    == "point"
                                    and Decimal(
                                        str((observation.get("value") or {}).get("point"))
                                    )
                                    == expected_amount
                                ),
                                None,
                            )
                            if stored_obs is None:
                                mismatches.append(
                                    f"{oid} source field {field}={expected_amount} "
                                    "has no matching residue component cell"
                                )
                                continue
                            used_stable_ids.add(str(stored_obs["observation_id"]))
                            n_numeric += 1
                            census[q_token] = census.get(q_token, 0) + 1
                            source_census = per_source.setdefault(source_id, {})
                            source_census[q_token] = source_census.get(q_token, 0) + 1
                        continue
                    expected = _census_expected_point(item, q_token, units)
                    stored_obs = by_id.get(oid)
                    if stored_obs is None:
                        stable_points = [
                            observation
                            for observation in stored_observations
                            if str(observation.get("observation_id") or "").startswith(
                                f"{source_id}::{raw_id}::"
                            )
                            and str(observation.get("observation_id") or "")
                            not in used_stable_ids
                        ]
                        if expected is not None:
                            stored_obs = next(
                                (
                                    observation
                                    for observation in stable_points
                                    if (observation.get("value") or {}).get("kind")
                                    == "point"
                                    and Decimal(
                                        str((observation.get("value") or {}).get("point"))
                                    )
                                    == expected
                                ),
                                None,
                            )
                        elif stable_points:
                            stored_obs = stable_points[0]
                    if stored_obs is None:
                        mismatches.append(f"missing stored point {oid}")
                        continue
                    used_stable_ids.add(str(stored_obs["observation_id"]))
                    stored_q = (stored_obs.get("identity") or {}).get("quantity") or {}
                    stored_val = stored_obs.get("value") or {}
                    if expected is None:
                        n_unavailable += 1
                        if stored_val.get("kind") == "point":
                            mismatches.append(
                                f"{oid} stored numeric {stored_val.get('point')} "
                                f"but declared quantity {q_token} has no source field"
                            )
                        continue
                    n_numeric += 1
                    label = q_token if q_token is not None else "unknown"
                    census[label] = census.get(label, 0) + 1
                    source_census = per_source.setdefault(source_id, {})
                    source_census[label] = source_census.get(label, 0) + 1
                    if stored_q.get("value") != label:
                        mismatches.append(
                            f"{oid} stored quantity {stored_q.get('value')!r} != declared {label}"
                        )
                    if stored_val.get("kind") != "point":
                        mismatches.append(
                            f"{oid} declared field present but stored {stored_val.get('kind')}"
                        )
                        continue
                    got = Decimal(str(stored_val.get("point")))
                    if got != expected:
                        mismatches.append(f"{oid} stored {got} != source {expected} for {label}")
    return census, mismatches, n_numeric, n_unavailable, per_source


def test_j01_store_census_series_numeric_matches_declared_field(tmp_path: Path) -> None:
    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not extracts_v2.is_dir():
        pytest.skip("migrated store not generated yet")
    census, mismatches, n_numeric, n_unavailable, per_source = _series_census(
        extracts, extracts_v2
    )
    sato = _migrate_real_extract(
        tmp_path,
        "sato-1976-lunar-fo2-apollo-17.yaml",
        write=True,
    )
    sato_literature = tmp_path / "tree" / "data" / "literature"
    (
        sato_census,
        sato_mismatches,
        sato_numeric,
        _sato_unavailable,
        sato_per_source,
    ) = _series_census(
        sato_literature / "extracts",
        sato_literature / "extracts-v2",
    )
    assert len(sato.observations) == 115
    assert sato_census == {"activity": 54, "fugacity": 55}
    assert sato_numeric == 109
    assert _sato_unavailable == 6
    assert sato_mismatches == []
    assert sato_per_source == {
        "sato-1976-lunar-fo2-apollo-17": {"activity": 54, "fugacity": 55}
    }
    assert n_numeric == sum(census.values())
    assert per_source.get("sato-1976-lunar-fo2-apollo-17") == sato_per_source[
        "sato-1976-lunar-fo2-apollo-17"
    ]
    assert not mismatches, mismatches[:20]


    # Re-pinned for the reviewed Holzheid 1997 correction: its 33 Table 3 gamma cells
    # now migrate as model_derived numeric activity_coefficient series (author
    # calculations, not measurements). Per-source delta: holzheid-1997 0->33;
    # activity_coefficient 128->161, n_numeric 356->389. This census counts printed
    # cells that survive migration regardless of evidence class (as for Ueshima
    # model_derived above) - mismatches stays 0.
    # Re-pinned for the O'Neill & Eggins 2002 extract (Chem. Geol. 186, Table 7): 91 printed
    # gamma cells for FeO, NiO, CoO, MoO2, MoO3 in CMAS melts at 1400 C. Per-source delta:
    # oneill-2002-feo-activity-coefficients-cmas 0->91; activity_coefficient 161->252.
    # The count moved because data became visible, not because a check was relaxed -
    # mismatches stays 0.
    # Offer 7 adds 65 printed Holzheid gamma points to the 33 already present:
    # the source now contributes 98 points and the store count moves 252->317.
    assert per_source.get("holzheid-1997-feo-nio-coo-activity-metal-saturated") == {
        "activity_coefficient": 98
    }
    assert census.get("activity_coefficient") == 317
    # Re-pinned with data/literature/extracts/pahlevan-2026-protolunar-volatile-outflows.yaml
    # (51 p_partial points, 2026-09-24): p_partial 18->69 and n_numeric 223->274.
    # The counts moved because data became visible, not because a check was relaxed - mismatches stays 0.
    # Re-pinned once on this tree with _series_census (mismatches 0) against
    # 7923cfb59. Numeric series counts moved only for:
    #   jaggi-2021-mercury-atmosphere p_partial +6
    #   ta-shirai-2000-lpsc evaporation_coefficient_alpha +6
    #   kems-005-fedkin-2006 evaporation_coefficient_alpha +12
    # p_partial 69->75, evaporation_coefficient_alpha 12->30, n_numeric 274->298.
    # The lunar DeMaria 1973 merge adds its 61 Table I p_partial cells as
    # run-labelled series: p_partial 75->136 and n_numeric 356->417.
    # Suzuki 2026 adds 13 printed oxygen partial-pressure points via the
    # independent partial_pressure_O2 alias: p_partial 136->149.
    # Re-pinned for t998: Zhang Table 4 Na/K alpha points add four printed
    # cells, evaporation_coefficient_alpha 30->34 and n_numeric 450->454.
    assert census.get("p_partial") == 149
    assert census.get("p_sat") == 21
    # Sato (1976) contributes 54 numeric Table 2/3 activity cells and 55 numeric
    # Table 1 oxygen-fugacity cells (109 total), already included by the fresh store.
    assert census.get("fugacity") == 55
    # Twelve repeated Fedkin table cells are no longer counted through the retired
    # mirror; the KEMS owner extract retains the cells (Fedkin et al. 2006, Table 3,
    # p. 212, DOI 10.1016/j.gca.2005.08.014).
    assert census.get("evaporation_coefficient_alpha") == 22
    # Re-pinned with the d-032 store regen. _series_census skips a source with no
    # extracts-v2 sibling, and the pre-regen store was missing 26 sources' derived
    # files, so their series went uncounted: evaporation_rate 21->25,
    # mass_loss_fraction 9->19, n_numeric 209->223. The counts moved because the
    # data became visible, not because a check was relaxed - mismatches stays 0.
    assert census.get("evaporation_rate") == 25
    # Re-pinned after the loader-survival regeneration: Richter +50 measured
    # mass_loss_pct / 100 singleton series; mismatches remains 0.
    assert census.get("mass_loss_fraction") == 69
    assert census.get("mass_loss_rate", 0) == 0
    # Re-pinned once for the reviewed Ueshima replacement: its Fe-Mo Table 4
    # adds 58 printed T_C points, routed as transition_temperature. The
    # Ueshima per-source delta is numeric 0->58 and model_derived 0->58;
    # mismatches remains 0.
    assert census.get("transition_temperature") == 58
    assert per_source.get("ueshima-1982-fe-mo-thermal") == {
        "transition_temperature": 58
    }
    # The reviewed residue-composition rail adds 516 Sossi ppm and 648 Hashimoto
    # oxide cells. t-1152 adds 110 printed Markova 1984 Table 2 residue cells.
    # The independent source census validates each typed component against its
    # own printed field; the seven all-null Hashimoto rows stay absent.
    # t-1122 adds four 14163 1050°C residue-ppm cells each for K and Na from
    # Gibson & Hubbard (1972), Table 2: K 4220/3027/1437/88 and Na
    # 4660/4110/3140/1730. These are residual component abundances, so all eight
    # are residue_component_composition points.
    # Ivanova (2018), Table 1, p. 1 adds 32 printed residue oxide-abundance
    # cells: four components across eight evaporated samples. The count moved
    # because the data was added, not because a check was relaxed; mismatches
    # stays 0.
    assert census.get("residue_component_composition") == 1314
    assert per_source.get("kems-012-sossi-2019") == {"residue_component_composition": 516}
    assert per_source.get("kems-015-hashimoto-1983") == {"residue_component_composition": 648}
    assert per_source.get("kems-024-gibson-hubbard-1972") == {
        "residue_component_composition": 8
    }
    assert per_source.get("kems-026-markova-1984") == {
        "residue_component_composition": 110
    }
    assert per_source.get("ivanova-2018-spinel-rich-cai-evaporation") == {
        "residue_component_composition": 32
    }
    # Current total includes the 110 Markova 1984 residue components and omits
    # the 12 duplicate Fedkin mirror cells (b677: the pre-item24 total 1877->1865).
    # The 13 Suzuki pO2 points now join p_partial, moving 1878->1891.
    # Item 24 adds 13 Abdul 2023 H_minus_H298 cells (Table 3 rankinite 7 + Table 4
    # belite 6); Sugawara & Akaogi adds six measured heat-content cells, including
    # the Table 1 series. Per-source deltas: Abdul 0->13, Sugawara 0->6.
    # The independent census checks all six source cells; mismatches stay 0.
    assert census.get("H_minus_H298") == 19
    assert per_source.get("abdul-2023-cao-sio2-calphad") == {"H_minus_H298": 13}
    assert per_source.get("sugawara-akaogi-2004-liquid-calorimetry") == {
        "H_minus_H298": 6
    }
    # Fresh regeneration restores 33 missing extract siblings. Three add numeric
    # series cells: Gaillard (82 activity), Ivanova (32 residue oxides), and
    # Tsuchiyama (18 areal mass-loss cells). Their 132 cells have zero mismatches;
    # the merged numeric-series count moves 2071->2203 because the data became
    # visible, not because a check was relaxed.
    # Sossi 2020 adds 22 isotope_delta rows under the field shared with the
    # migrator's Quantity.ISOTOPE_DELTA source map.
    assert census.get("isotope_delta") == 22
    assert per_source.get("sossi-2020-cu-zn-isotope-evap-formalism") == {
        "isotope_delta": 22
    }
    assert n_numeric == 2225, (n_numeric, census, n_unavailable)


def test_j01_isotope_delta_census_uses_migrator_source_field() -> None:
    fields = QUANTITY_SOURCE_FIELDS[Quantity.ISOTOPE_DELTA]
    assert fields == ("isotope_delta_isotope_delta",)
    item = {fields[0]: "2.15"}
    selected = select_declared_source(Quantity.ISOTOPE_DELTA, "per mil", item)
    assert selected.amount is not None
    assert _census_expected_point(
        item, Quantity.ISOTOPE_DELTA.value, "per mil"
    ) == selected.amount


def test_residue_point_condition_values_keep_their_printed_types() -> None:
    from simulator.battery.migrate import _point_condition_from_plain

    for key, value in (
        ("fO2_control", "none"),
        (
            "atmosphere",
            "No buffer gas or gas mix during the vacuum runs.",
        ),
    ):
        condition = _point_condition_from_plain(
            {"state": {"tag": "value", "value": value}},
            key=key,
        )
        assert condition.state.is_value
        assert condition.state.value == value

    nominal_pressure = {
        "value": "0.00015",
        "units": "Torr",
        "kind": "about_nominal_by_temperature",
        "as_printed": "~1.5 × 10^{-4} Torr",
    }
    condition = _point_condition_from_plain(
        {"state": {"tag": "value", "value": nominal_pressure}},
        key="pressure_on_throw_Torr",
    )
    assert condition.state.value == nominal_pressure


def test_j01_declared_quantity_accepts_one_decorated_source_field() -> None:
    selection = select_declared_source(
        Quantity.EVAPORATION_RATE,
        "mol/s/m2",
        {"evaporation_rate_1200C": "4.6e-06"},
    )
    assert selection.amount == as_decimal("4.6e-06")
    assert selection.field_name == "evaporation_rate_1200C"


def test_equal_temperature_range_is_a_point_but_true_range_is_not() -> None:
    point = select_declared_source(
        "temperature_K", None, {"T_range_K": [1673.15, 1673.15]}
    )
    assert point.value.kind is ValueKind.POINT
    assert point.amount == as_decimal("1673.15")
    assert point.field_name == "T_range_K"

    interval = select_declared_source(
        "temperature_K", None, {"T_range_K": [1300, 1800]}
    )
    assert interval.value.kind is ValueKind.UNAVAILABLE
    assert interval.condition_ranges == (("T_range_K", as_decimal("1300"), as_decimal("1800")),)
    assert "temperature domain" in (interval.reason or "")


def test_bencze_2016_reference_temperature_is_bound(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "bencze-yazhenskikh-2016.yaml")
    observations = list(result.observations.values())
    assert len(observations) == 70
    assert all(
        observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == as_decimal("1273")
        for observation in observations
    )


@pytest.mark.parametrize(
    "extra, expected",
    [
        ({"X_Na2O_as_published": 0.4}, {"Na2O": as_decimal("0.4"), "SiO2": as_decimal("0.6")}),
        (
            {"composition_mol": {"SiO2": 0.79, "Na2O": 0.07, "B2O3": 0.10, "Al2O3": 0.03, "minor constituents": 0.01}},
            {"SiO2": as_decimal("0.79"), "Na2O": as_decimal("0.07"), "B2O3": as_decimal("0.10"), "Al2O3": as_decimal("0.03")},
        ),
    ],
)
def test_printed_mole_fraction_composition_maps_without_wt_conversion(
    tmp_path: Path, extra: dict, expected: dict
) -> None:
    values = {
        "quantity": "activity",
        "activity": 0.2,
        "method_class": "measured_direct",
        **extra,
    }
    root = _write_min_tree(
        tmp_path,
        _scalar_extract(
            quantity="activity",
            units="dimensionless",
            values=values,
            obs_type="activity_coefficient",
        ),
    )
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert obs.identity.composition is not None
    if "minor constituents" in extra.get("composition_mol", {}):
        assert obs.identity.composition.is_unknown
        assert "partial_composition" in (obs.identity.composition.reason or "")
        assert any(
            "minor constituents" in (entry.why or "")
            and "composition" in (entry.axes or [])
            for entry in result.queue
        )
    else:
        assert obs.identity.composition.is_value
        assert obs.identity.composition.value.as_map() == expected


def test_borisov_combined_iron_composition_migrates_without_a_redox_split(
    tmp_path: Path,
) -> None:
    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/borisov-2018-compilation.yaml")
        .read_text(encoding="utf-8")
    )
    rows = source["species"]["Fe"]["observations"]
    assert len(rows) == 435

    # Bound closure by half a unit of the last printed decimal place for each
    # component, rather than imposing a tolerance unrelated to row precision.
    for row in rows:
        printed = row["values"]["composition_mol"]
        amounts = [as_decimal(amount) for amount in printed.values()]
        rounding_bound = sum(
            (Decimal("0.5").scaleb(amount.as_tuple().exponent) for amount in amounts),
            Decimal("0"),
        )
        assert abs(
            sum(amounts, Decimal("0")) - Decimal("1")
        ) <= rounding_bound
        converted, omitted = _mole_fraction_composition_from_values(row["values"])
        assert omitted == ()
        assert converted is not None
        assert converted.as_map()["FeO"] == as_decimal(printed["FeO+FeO1.5"])
        assert abs(
            sum(converted.as_map().values(), Decimal("0"))
            - sum(amounts, Decimal("0"))
        ) == 0

    result = _migrate_real_extract(tmp_path, "borisov-2018-compilation.yaml")
    assert len(result.observations) == 435
    for observation in result.observations.values():
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert observation.point_conditions is not None
        assert {"temperature_K", "fO2_log"} <= set(observation.point_conditions)
    by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    for row_id, expected_total_iron, expected_temperature in (
        ("borisov_2018_table2_row_4", "0.0753203", "1775.15"),
        ("borisov_2018_table2_row_155", "0.0428978", "1574.15"),
        ("borisov_2018_table2_row_438", "0.0342061", "1523.15"),
    ):
        observation = by_id[row_id]
        composition = observation.identity.composition.value
        assert composition.as_map()["FeO"] == as_decimal(expected_total_iron)
        assert observation.identity.species.phase.is_unknown
        assert (
            observation.identity.species.phase.reason
            == "source does not state phase"
        )
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert observation.evidence.original_method_class == "measured_reduced"
        assert observation.identity.temperature_K.value == as_decimal(expected_temperature)
        assert observation.point_conditions is not None
        assert "fO2_log" in observation.point_conditions


def test_unknown_mole_fraction_component_is_queued_and_withholds_composition() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": {
                "SiO2": "0.6",
                "MgO": "0.3",
                "unrecognized oxide": "0.1",
            }
        }
    )
    assert composition is None
    assert omitted == ("unrecognized oxide",)


def test_dacko_partial_composition_is_typed_refusal_not_scored(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "ta-dacko-conradt-low-p-transpiration.yaml"
    )
    rows = [
        obs
        for obs in result.observations.values()
        if "table2_activity_" in obs.observation_id
    ]
    assert len(rows) == 6
    for obs in rows:
        composition = obs.identity.composition
        assert composition is not None and composition.is_unknown
        assert "partial_composition" in (composition.reason or "")
        prediction = predict_with_engine(Engine.OPENIMCC, obs)
        assert prediction.execution.state is ExecutionState.NOT_PROBED
        assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
        assert prediction.requested_composition == composition


def test_k04_census_goes_red_when_stored_alpha_is_corrupted(tmp_path: Path) -> None:
    import shutil
    from decimal import Decimal

    extracts = REPO_ROOT / "data" / "literature" / "extracts"
    extracts_v2 = REPO_ROOT / "data" / "literature" / "extracts-v2"
    if not extracts_v2.is_dir():
        pytest.skip("migrated store not generated yet")
    dest = tmp_path / "extracts-v2"
    shutil.copytree(extracts_v2, dest)
    fedkin = dest / "kems-005-fedkin-2006.yaml"
    stored = yaml.safe_load(fedkin.read_text(encoding="utf-8"))
    n_mutated = 0
    for obs in stored.get("observations") or []:
        ident = obs.get("identity") or {}
        q = ident.get("quantity") or {}
        if q.get("value") != "evaporation_coefficient_alpha":
            continue
        t_state = ident.get("temperature_K") or {}
        try:
            t = Decimal(str(t_state.get("value")))
        except Exception:
            continue
        if t <= Decimal("1973"):
            continue
        val = obs.get("value") or {}
        if val.get("kind") != "point":
            continue
        val["point"] = str(Decimal(str(val["point"])) + 1)
        n_mutated += 1
    assert n_mutated == 9, n_mutated
    fedkin.write_text(yaml.safe_dump(stored, sort_keys=False), encoding="utf-8")
    _census, mismatches, _n_numeric, _n_unavailable, _per_source = _series_census(
        extracts, dest
    )
    assert mismatches, "census must go red when stored alpha points are corrupted"


def _scalar_extract(*, quantity: str, units: str, values: dict, obs_type: str = "psat_series") -> dict:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    row["type"] = obs_type
    row["units"] = units
    row["values"] = values
    return extract


def test_located_printed_and_initial_pins_numeric_composition() -> None:
    _printed, initial = _located_printed_and_initial(
        {"SiO2": Decimal("60"), "MgO": Decimal("40")}, None
    )

    assert initial is not None
    assert to_plain(initial.state.value)["components"] == [
        ["SiO2", "0.5015472170832676561113000772"],
        ["MgO", "0.4984527829167323438886999229"],
    ]


@pytest.mark.parametrize("container_name", ("points", "tests"))
def test_b718_partial_pressure_point_composition_stays_on_own_point(
    tmp_path: Path, container_name: str,
) -> None:
    point_composition = {"K2O": 7.4, "Al2O3": 26, "SiO2": 66.6}
    extract = _scalar_extract(
        quantity="partial_pressure",
        units="atm",
        values={
            "quantity": "partial_pressure",
            "method_class": "measured_direct",
            container_name: [
                {
                    "T_K": 1300,
                    "pressure_atm": 1,
                    "locator": {"table": "2", "note": "1"},
                },
                {
                    "T_K": 1400,
                    "pressure_atm": 2,
                    "T_C_is_initial_composition": True,
                    "locator": {"table": "2", "note": "2"},
                    "composition_wt_pct": point_composition,
                },
                {
                    "T_K": 1500,
                    "pressure_atm": 3,
                    "locator": {"table": "2", "note": "3"},
                },
            ],
        },
    )

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    rows = sorted(
        (
            observation
            for observation in result.observations.values()
            if quantity_token(observation.identity) is Quantity.P_PARTIAL
        ),
        key=lambda observation: observation.identity.temperature_K.value,
    )

    assert len(rows) == 3
    sibling_compositions = (
        rows[0].identity.composition,
        rows[2].identity.composition,
    )
    assert all(
        composition is None or not composition.is_value
        for composition in sibling_compositions
    )
    assert rows[1].identity.composition is not None
    assert rows[1].identity.composition.value == wt_pct_to_mole_fraction(
        {name: Decimal(str(value)) for name, value in point_composition.items()}
    )


def test_nonyield_point_without_numeric_value_is_queued_not_emitted(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="partial_pressure",
        units="atm",
        values={
            "quantity": "partial_pressure",
            "method_class": "measured_direct",
            "points": [
                {"T_K": 1300, "pressure_atm": 1},
                {
                    "T_K": 1400,
                    "omitted_reason": "source omitted numeric cell",
                },
            ],
        },
    )
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    parent_id = "fixture-source::na_psat"
    points = [
        obs for oid, obs in result.observations.items()
        if oid.startswith(parent_id + "::")
    ]

    assert len(points) == 1
    assert points[0].value.kind is ValueKind.POINT
    assert points[0].value.point == Decimal("101325")
    assert any(
        entry.why == "source omitted numeric cell"
        and entry.observation_id == f"{parent_id}::point:points:1"
        for entry in result.queue
    )


def test_b718_zero_mass_loss_row_is_initial_without_optional_flag(
    tmp_path: Path,
) -> None:
    initial_wt_pct = {"SiO2": 60, "MgO": 40}
    extract = _scalar_extract(
        quantity="mass_loss_fraction",
        units="wt_pct",
        values={
            "quantity": "mass_loss_fraction",
            "method_class": "measured_direct",
            "points": [
                {
                    "T_C": 0,
                    "mass_loss_pct": 0,
                    "composition_wt_pct": initial_wt_pct,
                },
                {"T_C": 1300, "mass_loss_pct": 10},
            ],
        },
        obs_type="mass_loss_series",
    )

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(iter(result.observations.values()))
    sample = result.experiments[observation.experiment_id].sample

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.is_value
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_value
    expected = wt_pct_to_mole_fraction(
        {name: Decimal(str(value)) for name, value in initial_wt_pct.items()}
    )
    assert dict(sample.initial_composition.state.value.components) == dict(
        expected.components
    )


def test_k01_scalar_psat_does_not_take_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="",
        values={"quantity": "pure_Psat", "alpha": 0.23, "method_class": "measured_direct"},
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(o for o in result.observations.values() if "na_psat" in o.observation_id)
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.point is None
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_scalar_activity_does_not_take_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="",
        values={"quantity": "activity", "alpha": 0.23, "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_scalar_alpha_does_not_take_coefficient(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="evaporation_coefficient_alpha",
        units="",
        values={
            "quantity": "evaporation_coefficient_alpha",
            "activity_coefficient": 0.06,
            "method_class": "measured_direct",
        },
        obs_type="alpha",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("value" in (e.axes or ()) for e in result.queue)


def test_k01_source_activity_is_quantity_activity(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="dimensionless activity",
        values={"activity": "7.19e-10", "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert quantity_token(obs.identity) is not Quantity.ACTIVITY_COEFFICIENT
    assert obs.identity.per is not None and obs.identity.per.is_value
    assert obs.identity.per.value is PerBasis.DIMENSIONLESS
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("7.19e-10")


@pytest.mark.parametrize(
    ("per", "expected", "reason", "invalid"),
    [
        ("mol_species", PerBasis.MOL_SPECIES, None, False),
        (
            {"tag": "unknown", "reason": "basis not printed"},
            None,
            "basis not printed",
            False,
        ),
        ("widgets", None, "unrecognised per basis 'widgets'", True),
    ],
)
def test_extract_nonactivity_per_preserves_typed_state(
    tmp_path: Path,
    per: object,
    expected: PerBasis | None,
    reason: str | None,
    invalid: bool,
) -> None:
    extract = _scalar_extract(
        quantity="delta_fG",
        units="kJ/mol",
        values={
            "quantity": "delta_fG",
            "delta_fG_kJ_mol": -1.2,
            "method_class": "measured_direct",
        },
    )
    extract["species"]["Na"]["observations"][0]["per"] = per

    baseline_extract = _scalar_extract(
        quantity="delta_fG",
        units="kJ/mol",
        values={
            "quantity": "delta_fG",
            "delta_fG_kJ_mol": -1.2,
            "method_class": "measured_direct",
        },
    )
    baseline = migrate(
        _write_min_tree(tmp_path / "before", baseline_extract), write=False
    )
    result = migrate(_write_min_tree(tmp_path / "after", extract), write=False)

    obs = next(iter(result.observations.values()))
    before = next(iter(baseline.observations.values()))
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert obs.identity.per is not None
    assert before.identity.per is not None and before.identity.per.is_unknown
    per_outcome = identity_equal(before.identity, obs.identity)
    assert per_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "per" in per_outcome.fields
    from dataclasses import fields

    changed_axes = [
        field.name
        for field in fields(before.identity)
        if getattr(before.identity, field.name) != getattr(obs.identity, field.name)
    ]
    assert changed_axes == ["per"]
    assert before.value == obs.value
    invalid_per_issues = [
        issue
        for issue in result.validation.issues
        if issue.path == f"observations.{obs.observation_id}.identity.per"
        and issue.reason is RefusalReason.INVALID_IDENTITY
    ]
    assert bool(invalid_per_issues) is invalid
    if expected is not None:
        assert obs.identity.per.is_value
        assert obs.identity.per.value is expected
    else:
        assert obs.identity.per.is_unknown
        assert obs.identity.per.reason == reason


def test_k01_dimensionless_activity_field_without_activity_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity",
        units="dimensionless",
        values={"activity": 1.0, "method_class": "measured_direct"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("1")


def test_k01_tsaplin_store_activity_not_coefficient() -> None:
    path = REPO_ROOT / "data" / "literature" / "extracts-v2" / "kems-ms2000-044.yaml"
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    stored = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    rows = [
        o
        for o in stored.get("observations") or []
        if o.get("observation_id", "").endswith("ms2000_044_na2o_activity_xsio2_0805_t1473")
    ]
    assert len(rows) == 1
    obs = rows[0]
    q = (obs.get("identity") or {}).get("quantity") or {}
    assert q.get("value") == "activity"
    assert obs.get("value", {}).get("kind") == "point"
    assert as_decimal(obs["value"]["point"]) == as_decimal("7.19e-10")


def test_k01_value_constructions_live_inside_the_boundary() -> None:
    import ast

    from simulator.battery.migrate import BOUNDARY_INGEST_CALLERS

    src = (REPO_ROOT / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed_value = {
        "select_declared_source",
        "_unavailable_selection",
        "_point_selection",
        "_interval_selection",
        "_series_selection_from_items",
        "_emit_series_or_point",
        "_value_from_plain",
    }
    func_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            func_stack.append(node.name)
            self.generic_visit(node)
            func_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Name):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if node.value.id in {"Value", "select_declared_source"}:
                        assert False, (
                            f"alias {target.id} = {node.value.id} at line {node.lineno}"
                        )
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.value, ast.Name):
                assert node.value.id not in {"Value", "select_declared_source"}, (
                    f"annotated boundary alias at line {node.lineno}"
                )
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "Value":
                    name = f"Value.{node.func.attr}"
            elif isinstance(node.func, ast.Call):
                inner = node.func
                inner_name = None
                if isinstance(inner.func, ast.Name):
                    inner_name = inner.func.id
                elif isinstance(inner.func, ast.Attribute):
                    inner_name = inner.func.attr
                if inner_name in {"getattr", "globals"}:
                    assert False, (
                        f"getattr/globals constructor bypass at line {node.lineno}"
                    )
                if isinstance(inner.func, ast.Attribute) and inner.func.attr == "get":
                    receiver = inner.func.value
                    assert not (isinstance(receiver, ast.Call)
                                and isinstance(receiver.func, ast.Name)
                                and receiver.func.id == "globals"), (
                        f"globals().get constructor bypass at line {node.lineno}"
                    )
            elif isinstance(node.func, ast.Subscript):
                receiver = node.func.value
                assert not (isinstance(receiver, ast.Call)
                            and isinstance(receiver.func, ast.Name)
                            and receiver.func.id == "globals"), (
                    f"globals subscript constructor bypass at line {node.lineno}"
                )
            owner = func_stack[-1] if func_stack else "<module>"
            if name in {"Value", "point_of", "Value.point_of"}:
                assert owner in allowed_value, (
                    f"Value constructed in {owner}:{node.lineno} outside the boundary"
                )
            if name == "select_declared_source":
                from simulator.battery.migrate import _BOUNDARY_WRAPPERS

                assert owner in BOUNDARY_INGEST_CALLERS or owner in _BOUNDARY_WRAPPERS, (
                    f"select_declared_source called from unlisted {owner}:{node.lineno}"
                )
            self.generic_visit(node)

    Visitor().visit(tree)


def test_k01_boundary_records_ingest_callers(tmp_path: Path) -> None:
    from simulator.battery.migrate import (
        BOUNDARY_INGEST_CALLERS,
        reset_boundary_served,
        boundary_served_callers,
    )

    reset_boundary_served()
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="atm",
        values={
            "quantity": "pure_Psat",
            "series": [{"T_K": 1200, "pressure_atm": 1.0}],
            "method_class": "measured_direct",
        },
    )
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "ledger-unknown-phase",
                        "source_id": "fixture-source",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 1200,
                        "table_kJ_mol": 1,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "kems_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {"fixture-source": {"citation": "Fixture"}},
                "cases": {
                    "c1": {
                        "source_id": "fixture-source",
                        "points": [
                            {
                                "species": "Na",
                                "coordinate": {"temperature_K": 1200},
                                "partial_pressure_pa": 1.0,
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "mre_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "m1": {
                        "paper_citation": {"title": "MRE"},
                        "cases": {
                            "one": {
                                "comparison_points": [
                                    {
                                        "observable_id": "mre_applied_charge_C",
                                        "expected_value": 1.0,
                                        "species": "O2",
                                    }
                                ]
                            }
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {"range": [0.011, 0.02]},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "vacuum_pyrolysis_measurements.v1",
                "measurements": {
                    "pomeroy_cardiff_2006_measurements": {
                        "evidence_class": "experiment-grade",
                        "paper_citation": {"label": "Pomeroy fixture"},
                        "conditions_reported": {
                            "feedstock": {"species": "MLS-1A"},
                            "peak_hold_temperature_C": {"reported_value": 1400},
                        },
                        "comparison_points": [
                            {
                                "observable_id": "pomeroy_non_condensed_mass_loss_fraction",
                                "expected_value": 0.0117,
                                "units": "mass_fraction",
                                "source_locator": {"source_location": "results"},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data" / "literature" / "refractory_vaporization_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "nist_janaf_named_nodes": {
                    "temperature_K": 1800,
                    "log10_kf": {"Al": {"value": 1.0, "table": "Al-005"}},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_compilation(
        root,
        "janaf-comp",
        "table-1",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "janaf-comp",
            "record_id": "table-1",
            "table": {
                "table_id": "Al-005",
                "standard_state_as_published": "0.1 MPa",
                "index_entry": {"formula": "Al", "state": "g"},
                "values": [
                    {
                        "temperature": {"value": 298.15},
                        "delta_fG": 0.0,
                        "log10_Kf": 0.0,
                    }
                ],
            },
        },
    )
    _write_compilation(
        root,
        "robie-comp",
        "atomic-weight-001",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-comp",
            "record_id": "atomic-weight-001",
            "record_kind": "atomic_weight",
            "rows": [{"atomic_weight": {"value": 227}}],
        },
    )
    migrate(root, write=False)
    served = {name for _file, _line, name in boundary_served_callers()}
    assert served
    unexpected = served - BOUNDARY_INGEST_CALLERS
    assert not unexpected, unexpected
    missing = BOUNDARY_INGEST_CALLERS - served
    assert not missing, missing


def test_k02_t_range_is_domain_not_value(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="pure_Psat",
        units="",
        values={
            "quantity": "pure_Psat",
            "T_range_K": [1200, 1300],
            "method_class": "measured_direct",
        },
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.P_SAT
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert obs.value.interval_low is None
    reason_blob = " ".join(
        [
            obs.value.unavailable_reason or "",
            getattr(obs.identity.temperature_K, "reason", None) or "",
            " ".join(e.why or "" for e in result.queue),
        ]
    )
    assert "1200" in reason_blob and "1300" in reason_blob
    assert any("value" in (e.axes or ()) for e in result.queue)
    assert any("temperature_K" in (e.axes or ()) for e in result.queue)


def test_k02_zr_th_and_pending_anchors_are_unavailable_domains() -> None:
    cases = [
        ("ref-032-zr.yaml", "anchor_Zr_pure_Psat", "1800", "2500"),
        ("ref-032-th.yaml", "anchor_Th_pure_Psat", "1800", "2500"),
        ("pending-bi2o3-kems.yaml", "anchor_Bi2O3_pure_Psat", "1163", "1400"),
        ("pending-mgcl2-measured-vp.yaml", "anchor_MgCl2_pure_Psat", "1100", "1700"),
        ("pending-dual-primary-oso4.yaml", "anchor_OsO4_pure_Psat", "298", "400"),
        ("pending-teo2-vp.yaml", "anchor_TeO2_pure_Psat", "884", "987"),
    ]
    for fname, local_id, lo, hi in cases:
        path = REPO_ROOT / "data" / "literature" / "extracts-v2" / fname
        if not path.is_file():
            pytest.skip("migrated store not generated yet")
        stored = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        rows = [
            o
            for o in stored.get("observations") or []
            if o.get("observation_id", "").endswith(local_id)
        ]
        assert len(rows) == 1, fname
        obs = rows[0]
        assert obs.get("value", {}).get("kind") == "unavailable", fname
        blob = yaml.safe_dump(obs)
        assert lo in blob and hi in blob, fname


def test_k03_ledger_missing_phase_is_queued(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "points": [
                    {
                        "key": "ledger-unknown-phase",
                        "source_id": "fixture-source",
                        "species": "Na",
                        "comparison_quantity": "delta_fG",
                        "temperature_K": 1200,
                        "table_kJ_mol": 1,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["ledger-unknown-phase"]
    assert obs.identity.species.phase.is_unknown
    phase_entries = [
        e
        for e in result.queue
        if e.observation_id == "ledger-unknown-phase" and "phase" in (e.axes or ())
    ]
    assert phase_entries
    assert any("phase" in (e.why or "").lower() for e in phase_entries)


def _quantity_state(obs) -> tuple[object, str | None]:
    ident = obs.identity
    token = quantity_token(ident)
    reason = None
    q = ident.quantity
    if hasattr(q, "reason"):
        reason = q.reason
    return token, reason


def _queue_blob(result, obs_id: str) -> str:
    return " ".join(
        e.why or ""
        for e in result.queue
        if e.observation_id == obs_id or (e.observation_id or "").endswith(obs_id)
    )


def test_l01_type_does_not_assign_contradicted_alpha(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={
            "method_class": "model_derived",
            "olette_alpha_theoretical": 1095,
            "not_hkl_langmuir_coefficient": True,
            "note": "Not an experimental HKL coefficient",
        },
        obs_type="alpha",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = (
        "homma_1966_mn_olette_theoretical_quoted_deep"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, reason = _quantity_state(obs)
    assert token is None
    blob = " ".join(filter(None, [reason, _queue_blob(result, obs.observation_id)]))
    assert "not_hkl_langmuir_coefficient" in blob or "HKL" in blob
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_gibbs_type_does_not_assign_dissociation_note(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="kJ/mol and J/(mol·K) for formation; over_R in kK as published",
        values={
            "gas_species": "EuO",
            "note": "dissociation energies of gaseous REE monoxides; numeric D0 not transcribed",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, reason = _quantity_state(obs)
    assert token is None
    blob = " ".join(filter(None, [reason, _queue_blob(result, obs.observation_id)]))
    assert "dissociation" in blob.lower()
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_gibbs_type_does_not_assign_vapor_pressure_equations(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="kJ/mol and J/(mol·K) for formation; over_R in kK as published",
        values={
            "note": "equations for partial vapor pressures over VO; numeric A,B not recovered",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_activity_type_does_not_assign_ordering_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless ordering (not a numeric gamma)",
        values={"semantics": "bound_not_point_ordering", "speciation_note": "VO(g) > V(g)"},
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_rate_type_does_not_assign_partial_pressure_units(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="as published (partial pressure; Fig. 5 lg P scale)",
        values={"gas_species": "SiO(g)", "semantics": "bound_not_point_ordering"},
        obs_type="rate_series",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_alpha_field_is_not_hkl_when_flagged(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={
            "alpha": 115,
            "not_hkl_langmuir_coefficient": True,
            "method_class": "measured_direct",
        },
        obs_type="alpha",
    )
    extract["species"]["Na"]["observations"][0]["regime"] = (
        "olette_relative_evaporation_coefficient"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is None
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any("quantity" in (e.axes or ()) for e in result.queue)


def test_l01_janaf_evaluator_gibbs_table_stays_delta_fg(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="NASA CEA polynomial",
        values={
            "evaluator_family": "nasa_cea_9",
            "reference_pressure_Pa": 100000.0,
            "segments": [{"T_min_K": 300.0, "T_max_K": 1000.0}],
            "method_class": "compilation_calculated_table",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is Quantity.DELTA_FG


def test_l01_costa_olivine_alpha_stays_evaporation_coefficient(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="dimensionless",
        values={"alpha": 0.02, "method_class": "measured_direct", "gas_species": "Fe(g)"},
        obs_type="alpha",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    token, _reason = _quantity_state(obs)
    assert token is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("0.02")


def test_l01_map_quantity_direct_witnesses() -> None:
    cases = [
        (
            "alpha",
            {"not_hkl_langmuir_coefficient": True, "note": "Not an experimental HKL coefficient"},
            "dimensionless",
            None,
        ),
        (
            "gibbs_table",
            {"note": "dissociation energies of gaseous REE monoxides including EuO"},
            "kJ/mol and J/(mol·K) for formation; over_R in kK as published",
            None,
        ),
        (
            "gibbs_table",
            {"note": "equations for partial vapor pressures over VO"},
            "kJ/mol and J/(mol·K) for formation; over_R in kK as published",
            None,
        ),
        (
            "activity_coefficient",
            {"semantics": "bound_not_point_ordering"},
            "dimensionless ordering (not a numeric gamma)",
            None,
        ),
        (
            "rate_series",
            {"semantics": "bound_not_point_ordering"},
            "partial pressure; Fig. 5 lg P scale",
            None,
        ),
    ]
    for obs_type, values, units, expected in cases:
        state, reason = map_quantity(obs_type, values, units=units)
        assert not state.is_value, (obs_type, state, reason)
        assert reason

    ok, _reason = map_quantity(
        "gibbs_table",
        {"evaluator_family": "nasa_cea_9", "reference_pressure_Pa": 100000, "segments": [{}]},
        units="NASA CEA polynomial",
    )
    assert ok.is_value and ok.value is Quantity.DELTA_FG
    ok, _reason = map_quantity("alpha", {"alpha": 0.02}, units="dimensionless")
    assert ok.is_value and ok.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    bad, reason = map_quantity(
        "alpha",
        {"alpha": 115, "not_hkl_langmuir_coefficient": True},
        units="dimensionless",
        row={"regime": "olette_relative_evaporation_coefficient"},
    )
    assert not bad.is_value
    assert reason and "not_hkl" in reason or "Olette" in (reason or "") or "outside" in (reason or "")


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("pure_vapor_pressure", Quantity.P_SAT),
        ("partial_pressure_overlay_figure_only", Quantity.P_PARTIAL),
        ("partial_pressure_O2", Quantity.P_PARTIAL),
        ("partial_pressure_CsBO2", Quantity.P_PARTIAL),
        ("partial_pressure_LiBO2", Quantity.P_PARTIAL),
        ("partial_pressure_NaBO2", Quantity.P_PARTIAL),
        ("partial_pressure_Mg", Quantity.P_PARTIAL),
        ("partial_pressure_O", Quantity.P_PARTIAL),
        ("partial_pressure_SiO", Quantity.P_PARTIAL),
        ("partial_pressure_over_illite", Quantity.P_PARTIAL),
        ("partial_pressure_series", Quantity.P_PARTIAL),
        ("partial_pressure_vs_setpoint_T", Quantity.P_PARTIAL),
        ("P_Na_over_soda_lime_glass", Quantity.P_PARTIAL),
        ("undetected_radionuclide_partial_pressure_limit", Quantity.P_PARTIAL),
        ("undetected_radionuclide_simulant_partial_pressure", Quantity.P_PARTIAL),
        ("raoultian_activity", Quantity.ACTIVITY),
        ("apparent_gamma_K2O", Quantity.ACTIVITY_COEFFICIENT),
        ("henrian_activity_coefficient", Quantity.ACTIVITY_COEFFICIENT),
        ("evaporation_coefficient_gamma_Si", Quantity.EVAPORATION_COEFFICIENT_ALPHA),
        ("mass_loss", Quantity.MASS_LOSS_FRACTION),
        ("total_integrated_mass_loss", Quantity.MASS_LOSS_FRACTION),
        ("total_gas_evolution_mass_loss", Quantity.MASS_LOSS_FRACTION),
        ("isothermal_hold_mass_loss", Quantity.MASS_LOSS_FRACTION),
        ("water_released_during_drying", Quantity.MASS_LOSS_FRACTION),
        ("dta_transition_temperatures", Quantity.TRANSITION_TEMPERATURE),
        ("invariant_transformation_temperature", Quantity.TRANSITION_TEMPERATURE),
        ("invariant_transformation_temperature_range", Quantity.TRANSITION_TEMPERATURE),
        ("pure_Fe_melting_onset", Quantity.TRANSITION_TEMPERATURE),
        ("solidus", Quantity.TRANSITION_TEMPERATURE),
        ("composition_dependent_solidus_points", Quantity.TRANSITION_TEMPERATURE),
        ("miscibility_gap_temperature", Quantity.TRANSITION_TEMPERATURE),
        ("measured_KEMS_ion_intensities", Quantity.ION_INTENSITY),
        ("ion_count_rate", Quantity.ION_INTENSITY),
        ("ion_intensity_isotherm", Quantity.ION_INTENSITY),
        ("ion_intensity_arrest_curve", Quantity.ION_INTENSITY),
        ("ion_intensity_monovariant_solidus_liquidus", Quantity.ION_INTENSITY),
        ("ion_intensity_vs_time_cooling", Quantity.ION_INTENSITY),
        ("ion_intensity_vs_time_heating", Quantity.ION_INTENSITY),
        ("I_T_vs_time_figure_only", Quantity.ION_INTENSITY),
        ("I+_Al / I+_Fe vs chamber voltage", Quantity.ION_INTENSITY_RATIO),
        ("ion_current_ratio_vs_time", Quantity.ION_INTENSITY_RATIO),
        ("ion_current_ratio_vs_T", Quantity.ION_INTENSITY_RATIO),
        ("ion_intensity_ratio_Mg_Fe_figure_only", Quantity.ION_INTENSITY_RATIO),
        (
            "Fig. 1. Experimental values of the ion current ratio for the Fe-Ti system",
            Quantity.ION_INTENSITY_RATIO,
        ),
        (
            "Fig. 3. Experimental values of the ion current ratio for the Fe-S system",
            Quantity.ION_INTENSITY_RATIO,
        ),
        ("Fig. 3. Temperature dependence of the ion current ratio", Quantity.ION_INTENSITY_RATIO),
        ("Fig. 4. Ion current ratios for the Fe-P system at 1600 C", Quantity.ION_INTENSITY_RATIO),
        ("Fig. 5 Experimental intensity ratios for the liquid Ti-Co alloys.", Quantity.ION_INTENSITY_RATIO),
        ("second_law_enthalpy_of_vaporization", Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW),
        ("stable_isotope_delta", Quantity.ISOTOPE_DELTA),
        ("delta_53Cr_average", Quantity.ISOTOPE_DELTA),
        ("delta_53Cr_reference_material", Quantity.ISOTOPE_DELTA),
    ],
)
def test_l02_empirical_quantity_aliases_are_closed(alias: str, expected: Quantity) -> None:
    assert QUANTITY_ALIASES[alias] is expected


def test_l02_empirical_quantity_aliases_map_numeric_witnesses() -> None:
    cases = [
        (
            "pure_vapor_pressure",
            {"quantity": "pure_vapor_pressure", "points": [{"T_K": 1400, "p_atm": 1.0}]},
            None,
            Quantity.P_SAT,
        ),
        (
            "raoultian_activity",
            {"quantity": "raoultian_activity", "activity": 0.2},
            "dimensionless",
            Quantity.ACTIVITY,
        ),
        (
            "solidus",
            {"quantity": "solidus", "T_K": 2050},
            "K",
            Quantity.TRANSITION_TEMPERATURE,
        ),
        (
            "ion_count_rate",
            {"quantity": "ion_count_rate", "count_rate": 12.0},
            "counts/s",
            Quantity.ION_INTENSITY,
        ),
        (
            "second_law_enthalpy_of_vaporization",
            {"quantity": "second_law_enthalpy_of_vaporization", "value": 42.0},
            "kcal/mol",
            Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW,
        ),
        (
            "stable_isotope_delta",
            {"quantity": "stable_isotope_delta"},
            "permil",
            Quantity.ISOTOPE_DELTA,
        ),
        (
            "delta_53Cr_average",
            {"quantity": "delta_53Cr_average"},
            "permil vs SRM 979",
            Quantity.ISOTOPE_DELTA,
        ),
        (
            "delta_53Cr_reference_material",
            {"quantity": "delta_53Cr_reference_material"},
            "permil vs SRM 979",
            Quantity.ISOTOPE_DELTA,
        ),
    ]
    for alias, values, units, expected in cases:
        state, reason = map_quantity(None, values, units=units)
        assert state.is_value and state.value is expected, (alias, state, reason)


_BLOCKED_QUANTITY_ALIASES = (
    "ion_current",
    "henrian_activity",
    "relative_ion_intensity",
    "partial_pressure_figure_only",
)

_QUANTITY_FIELD_RE = re.compile(
    r"(?m)(?:^|\s)quantity:\s*(?:\"([^\"]+)\"|'([^']+)'|([^\"'\n#,}]+))"
)


def _quantity_token(raw: str) -> Quantity | None:
    state, _reason = map_quantity(None, {"quantity": raw})
    if state.is_value:
        return state.value
    return None


def _alias_extends_shorter(alias: str) -> bool:
    return any(
        other != alias and alias.startswith(other + "_") for other in QUANTITY_ALIASES
    )


def _parse_pair(raw: str) -> tuple[tuple[Quantity | None, str | None], Quantity | None]:
    return split_qualified_quantity(raw), _quantity_token(raw)


def test_l02_blocked_quantity_aliases_are_absent() -> None:
    """Wrong observables stay unmapped. Henrian activity is not Raoultian activity."""

    for alias in _BLOCKED_QUANTITY_ALIASES:
        assert alias not in QUANTITY_ALIASES
        state, reason = map_quantity(None, {"quantity": alias})
        assert not state.is_value, (alias, state, reason)
        assert reason and alias in reason


def test_l02_alias_prefix_does_not_retarget_closed_names() -> None:
    """A label must not change how a longer closed quantity or alias parses.

    A species-formula suffix from a stem that is not itself `shorter_formula`
    may still split (`partial_pressure` + `NaBO2`). A bare remainder
    (`fraction`, `rate`, `ratio`, `fit`, `coefficient`) may not.
    """

    protected = [quantity.value for quantity in Quantity] + list(QUANTITY_ALIASES)
    for alias in list(QUANTITY_ALIASES):
        victims = [name for name in protected if name != alias and name.startswith(alias + "_")]
        saved = QUANTITY_ALIASES.pop(alias)
        try:
            without = {name: _parse_pair(name) for name in victims}
        finally:
            QUANTITY_ALIASES[alias] = saved
        for name, baseline in without.items():
            suffix = name[len(alias) + 1 :]
            got = _parse_pair(name)
            formula, derivation, _reference = parse_quantity_suffix(suffix)
            assert got[1] == baseline[1], (alias, name, got, baseline)
            if formula is None and derivation is None:
                assert got[0] == baseline[0], (alias, name, got, baseline)
            elif _alias_extends_shorter(alias):
                assert got[0] == baseline[0], (alias, name, got, baseline)


def _extract_quantity_strings() -> set[str]:
    found: set[str] = set()
    root = REPO_ROOT / "data" / "literature" / "extracts"
    for path in root.iterdir():
        if path.suffix != ".yaml":
            continue
        text = path.read_text(encoding="utf-8")
        for match in _QUANTITY_FIELD_RE.finditer(text):
            raw = next(group for group in match.groups() if group)
            found.add(raw.strip())
    return found


def test_l02_alias_prefix_does_not_retarget_extract_quantity_strings() -> None:
    """No alias changes the parse of another quantity string in the extracts.

    Historical stems may still prefix an unqualified string that is not itself
    a closed quantity or alias (`partial_pressure_atomic_oxygen`). They may
    not change a string that is already a closed name.
    """

    strings = _extract_quantity_strings()
    assert strings
    protected = {quantity.value for quantity in Quantity} | set(QUANTITY_ALIASES)
    for alias in list(QUANTITY_ALIASES):
        victims = [raw for raw in strings if raw.startswith(alias + "_")]
        saved = QUANTITY_ALIASES.pop(alias)
        try:
            without = {raw: _parse_pair(raw) for raw in victims}
        finally:
            QUANTITY_ALIASES[alias] = saved
        extends = _alias_extends_shorter(alias)
        for raw, baseline in without.items():
            suffix = raw[len(alias) + 1 :]
            got = _parse_pair(raw)
            formula, derivation, _reference = parse_quantity_suffix(suffix)
            cuts_closed = raw in protected or any(
                name != alias and name.startswith(alias + "_") and raw.startswith(name + "_")
                for name in protected
            )
            if formula is None and derivation is None:
                if cuts_closed or alias not in _HISTORICAL_PREFIX_STEMS:
                    assert got == baseline, (alias, raw, got, baseline)
            elif extends:
                assert got == baseline, (alias, raw, got, baseline)
            else:
                assert got[1] == baseline[1], (alias, raw, got, baseline)


def test_l02_reviewed_prefix_collisions_keep_the_unaliased_parse() -> None:
    assert split_qualified_quantity("activity_CsBO2") == (Quantity.ACTIVITY, "CsBO2")
    assert split_qualified_quantity("mass_loss_fraction") == (None, None)
    assert split_qualified_quantity("mass_loss_rate") == (None, None)
    assert _quantity_token("mass_loss_fraction") is Quantity.MASS_LOSS_FRACTION
    assert _quantity_token("mass_loss_rate") is Quantity.MASS_LOSS_RATE
    assert split_qualified_quantity("ion_current_ratio") == (None, None)
    assert _quantity_token("ion_current_ratio") is Quantity.ION_INTENSITY_RATIO
    assert split_qualified_quantity("henrian_activity_coefficient") == (None, None)
    assert split_qualified_quantity("partial_pressure_NaBO2_fit") == (
        Quantity.P_PARTIAL,
        "NaBO2_fit",
    )
    assert split_qualified_quantity("partial_pressure_LiBO2_fit") == (
        Quantity.P_PARTIAL,
        "LiBO2_fit",
    )
    assert split_qualified_quantity("partial_pressure_CsBO2_fit") == (
        Quantity.P_PARTIAL,
        "CsBO2_fit",
    )
    assert split_qualified_quantity("partial_pressure_NaBO2") == (
        Quantity.P_PARTIAL,
        "NaBO2",
    )
    assert _quantity_token("partial_pressure_NaBO2") is Quantity.P_PARTIAL
    assert _quantity_token("mass_loss") is Quantity.MASS_LOSS_FRACTION
    assert split_qualified_quantity("pure_vapor_pressure_fit") == (None, None)
    assert split_qualified_quantity("raoultian_activity_coefficient") == (None, None)
    assert split_qualified_quantity("relative_ion_intensity_comparison") == (None, None)
    for label in (
        "mass_loss",
        "pure_vapor_pressure",
        "partial_pressure_NaBO2",
        "partial_pressure_LiBO2",
        "partial_pressure_CsBO2",
        "raoultian_activity",
        "solidus",
        "henrian_activity",
        "ion_current",
        "relative_ion_intensity",
        "partial_pressure_figure_only",
        "second_law_enthalpy_of_vaporization",
        "invariant_transformation_temperature",
    ):
        assert label not in _HISTORICAL_PREFIX_STEMS


_TYPE_CONTRADICTIONS = [
    # These current rows pin quantity refusals. Four Stebbins replacements
    # now exercise missing-quantity handling, not the former calorimetry guard.
    ("ames-walsh-white-1967.yaml", "Ames67_EuO_dissociation"),
    ("ames-walsh-white-1967.yaml", "Ames67_YbO_dissociation"),
    ("banchor-matsui-naito-1986.yaml", "Ban86_equations"),
    ("datz-and-smith-1961.yaml", "Datz1961_TableII_Kd"),
    ("datz-and-smith-1961.yaml", "JANAF1998_WebBook_Shomate"),
    ("datz-and-smith-1961.yaml", "Datz1961_TableII_Kd_nacl_side"),
    ("habermann-daane-1964.yaml", "Hab64_Eu_metal_third_law"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_theoretical_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_theoretical_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_theoretical_quoted_deep"),
    ("kems-011-wetzel-gail-2013.yaml", "wetzel_gail_2013_sio_growth_alpha_arrhenius"),
    ("kems-011-wetzel-gail-2013.yaml", "wetzel_gail_2013_sio_growth_class_b1"),
    ("kems-022-demaria-1971.yaml", "demaria_1971_sio_lunar_basalt_kems_main_cell"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_T1_P4O10_KEMS_dominance"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_sf18_speciation_VO_VO2"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_Eu2O3_window"),
    ("kems-041-sossi-fegley-2018.yaml", "sossi_fegley_2018_alias_SF18_Yb2O3_window"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si_activation_enthalpy"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si_equilibrium_enthalpy_and_barrier"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_si2c_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_si2c_formation_enthalpies"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_second_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_second_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_third_law_this_work"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_table1_sic2_third_law_reference_10"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_activation_enthalpy"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_equilibrium_enthalpy_and_barrier"),
    ("kems-184-behrens-1979.yaml", "behrens_1979_sic2_formation_enthalpies"),
    ("nist-webbook.yaml", "Rau74_critical_constants"),
    # The four current series rows return missing quantity; inline fixtures
    # below retain the former calorimetric gibbs_table guard probes. Anorthite
    # still exercises that guard from the current extract.
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_diopside_calorimetry_measured_tables_1_3"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_albite_analbite_calorimetry_measured_tables_1_5"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_sanidine_calorimetry_measured_table_3"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_nepheline_calorimetry_measured_table_3"),
    ("stebbins-carmichael-weill-1983.yaml", "stebbins_1983_anorthite_calorimetry_measured_table_1"),
    ("wetzel-gail-2013-sio-arrhenius.yaml", "wetzel_gail_2013_sio_arrhenius"),
]
_L05_CALORIMETRIC_GIBBS_GUARD_REASON = (
    "source is calorimetric enthalpy/heat content, not delta_fG"
)
_L05_CALORIMETRIC_GIBBS_FIXTURES = [
    (
        "fixture:stebbins_1983_diopside_calorimetry_tables_1_7",
        {
            "observation_id": "fixture:stebbins_1983_diopside_calorimetry_tables_1_7",
            "type": "gibbs_table",
            "units": "as published",
            "standard_state": "heat content referred to 300 K; heat of fusion at 1 bar",
            "regime": "drop calorimetry",
            "values": {
                "composition": "CaMgSi2O6",
                "system": "diopside calorimetric heat content",
            },
        },
        _L05_CALORIMETRIC_GIBBS_GUARD_REASON,
    ),
    (
        "fixture:stebbins_1983_albite_analbite_calorimetry_tables_1_7",
        {
            "observation_id": "fixture:stebbins_1983_albite_analbite_calorimetry_tables_1_7",
            "type": "gibbs_table",
            "units": "as published",
            "standard_state": "heat content referred to 300 K; heat of fusion at 1 bar",
            "regime": "drop calorimetry",
            "values": {
                "composition": "NaAlSi3O8",
                "system": "albite and analbite calorimetric heat content",
            },
        },
        _L05_CALORIMETRIC_GIBBS_GUARD_REASON,
    ),
    (
        "fixture:stebbins_1983_sanidine_calorimetry_tables_3_7",
        {
            "observation_id": "fixture:stebbins_1983_sanidine_calorimetry_tables_3_7",
            "type": "gibbs_table",
            "units": "as published",
            "standard_state": "heat content referred to 300 K; heat of fusion at 1 bar",
            "regime": "drop calorimetry",
            "values": {
                "composition": "KAlSi3O8",
                "system": "sanidine calorimetric heat content",
            },
        },
        _L05_CALORIMETRIC_GIBBS_GUARD_REASON,
    ),
    (
        "fixture:stebbins_1983_nepheline_calorimetry_tables_3_7",
        {
            "observation_id": "fixture:stebbins_1983_nepheline_calorimetry_tables_3_7",
            "type": "gibbs_table",
            "units": "as published",
            "standard_state": "heat content referred to 300 K; heat of fusion at 1 bar",
            "regime": "drop calorimetry",
            "values": {
                "composition": "NaAlSiO4",
                "system": "nepheline calorimetric heat content",
            },
        },
        _L05_CALORIMETRIC_GIBBS_GUARD_REASON,
    ),
]
_L05_CALORIMETRIC_REPLACEMENT_REASONS = [
    ("stebbins_1983_diopside_calorimetry_measured_tables_1_3", "missing quantity"),
    ("stebbins_1983_albite_analbite_calorimetry_measured_tables_1_5", "missing quantity"),
    ("stebbins_1983_sanidine_calorimetry_measured_table_3", "missing quantity"),
    ("stebbins_1983_nepheline_calorimetry_measured_table_3", "missing quantity"),
    (
        "stebbins_1983_anorthite_calorimetry_measured_table_1",
        _L05_CALORIMETRIC_GIBBS_GUARD_REASON,
    ),
]
_FIELD_ALPHA_CONTRADICTIONS = [
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_alpha_exp_table1"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_mn_olette_class_fence_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_alpha_exp_table2"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_cu_olette_class_fence_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_alpha_exp_table3"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_class_fence_b1"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_experimental_quoted_deep"),
    ("kems-001-homma-1966.yaml", "homma_1966_sn_olette_class_fence_quoted_deep"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_mn_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_cu_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_sn_olette_alpha_table3"),
    ("kems-002-ohno-1967.yaml", "ohno_1967_cr_olette_alpha_table3"),
]


def _extract_observation(fname: str, observation_id: str) -> dict:
    source = load_cached_safe_yaml(
        (REPO_ROOT / "data" / "literature" / "extracts" / fname).read_text(encoding="utf-8")
    )
    for body in (source.get("species") or {}).values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations") or []:
            if isinstance(row, dict) and row.get("observation_id") == observation_id:
                return row
    raise AssertionError(f"missing extract row {fname}::{observation_id}")


def test_l05c1_type_contradictions_are_quantity_unknown() -> None:
    assert len(_TYPE_CONTRADICTIONS) == 42
    for fname, oid in _TYPE_CONTRADICTIONS:
        row = _extract_observation(fname, oid)
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        state, reason = map_quantity(
            row.get("type"), values, units=row.get("units"), row=row
        )
        assert not state.is_value, (oid, state, reason)
        assert reason


def test_l05c1_calorimetric_gibbs_guard_reasons_are_pinned() -> None:
    for fixture_id, row, expected_reason in _L05_CALORIMETRIC_GIBBS_FIXTURES:
        assert fixture_id.startswith("fixture:")
        state, reason = map_quantity(
            row["type"], row["values"], units=row["units"], row=row
        )
        assert not state.is_value
        assert reason == expected_reason, (fixture_id, state, reason)

    for observation_id, expected_reason in _L05_CALORIMETRIC_REPLACEMENT_REASONS:
        row = _extract_observation(
            "stebbins-carmichael-weill-1983.yaml", observation_id
        )
        state, reason = map_quantity(
            row.get("type"), row.get("values"), units=row.get("units"), row=row
        )
        assert not state.is_value
        assert reason == expected_reason, (observation_id, state, reason)


def test_l05c1_olette_alpha_fields_are_quantity_unknown() -> None:
    assert len(_FIELD_ALPHA_CONTRADICTIONS) == 16
    for fname, oid in _FIELD_ALPHA_CONTRADICTIONS:
        row = _extract_observation(fname, oid)
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        state, reason = map_quantity(
            row.get("type"), values, units=row.get("units"), row=row
        )
        assert not state.is_value, (oid, state, reason)
        assert reason


def test_l05c1_alpha_outside_unit_interval_is_unknown() -> None:
    state, reason = map_quantity("alpha", {"alpha": 115}, units="dimensionless")
    assert not state.is_value
    assert reason and "outside" in reason
    state, _reason = map_quantity("alpha", {"alpha": 0.02}, units="dimensionless")
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA


def _write_compilation(root: Path, source_id: str, record_id: str, doc: dict) -> Path:
    path = (
        root
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "auxiliary"
        / f"{record_id}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_l05g0_atomic_weight_rows_are_not_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_compilation(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "atomic-weight-001",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-hemingway-1995-usgs-b2131",
            "record_id": "atomic-weight-001",
            "record_kind": "atomic_weight",
            "formula": "Ac",
            "rows": [{"atomic_weight": {"value": 227}}],
        },
    )
    result = migrate(root, write=False)
    obs = result.observations["robie-hemingway-1995-usgs-b2131:atomic-weight-001"]
    token, reason = _quantity_state(obs)
    assert token is None
    assert reason and "closed quantity" in reason
    assert any(
        e.observation_id == obs.observation_id and "quantity" in (e.axes or ())
        for e in result.queue
    )


def test_l05g0_janaf_style_delta_fg_cells_stay_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _write_compilation(
        root,
        "robie-hemingway-1995-usgs-b2131",
        "al-ht",
        {
            "schema_version": "literature_compilation.v1",
            "source_id": "robie-hemingway-1995-usgs-b2131",
            "record_id": "al-ht",
            "table_kind": "high_temperature",
            "formula": "Al",
            "rows": [
                {
                    "temperature": {"value": 298.15},
                    "delta_f_G": {"value": 0.0},
                    "delta_fG": 0.0,
                }
            ],
        },
    )
    result = migrate(root, write=False)
    obs = next(
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith("robie-hemingway-1995-usgs-b2131:al-ht")
    )
    token, _reason = _quantity_state(obs)
    assert token is Quantity.DELTA_FG


def test_l05g1a_qualified_activity_token_lifts_activity(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity_CsBO2",
        units="dimensionless",
        values={
            "quantity": "activity_CsBO2",
            "activity": 4.0e-6,
            "T_K": 1200.0,
            "method_class": "measured_direct",
        },
        obs_type="activity_coefficient",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = (
        "plante_hastie_1983_csbo2_activity_1200K"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert quantity_token(obs.identity) is not Quantity.ACTIVITY_COEFFICIENT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("4e-06")
    assert obs.identity.species.formula == "CsBO2"


def test_activity_standard_state_source_prose_lifts_typed_reference(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity_from_table2_AT_B",
        units="dimensionless",
        values={
            "quantity": "activity_from_table2_AT_B",
            "activity": 0.12,
            "method_class": "measured_direct",
        },
        obs_type="activity_coefficient",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["phase"] = "condensed_liquid"
    row["standard_state"] = (
        "raoultian_pure_endmember; pure liquid Na2O endmember=Na2O"
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    reference_state = obs.identity.reference_state
    assert reference_state is not None and reference_state.is_value
    assert reference_state.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert reference_state.value.endmember.formula == "Na2O"
    assert reference_state.value.endmember.phase.value is Phase.L
    assert reference_state.value.reference_pressure_bar is None


def test_guo_structured_standard_state_lifts_periclase_reference(tmp_path: Path) -> None:
    source = REPO_ROOT / "data/literature/extracts/guo-2021-mgo-activity-cmas-slag.yaml"
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    row = extract["species"]["MgO"]["observations"][0]
    row["standard_state"] = {
        "state": {
            "tag": "value",
            "value": {
                "convention": "raoultian_pure_endmember",
                "endmember": {
                    "formula": "MgO",
                    "phase": {"tag": "value", "value": "cr"},
                    "polymorph": {"tag": "value", "value": "periclase"},
                },
                "component_basis": "MgO",
            },
        },
        "inferred": False,
        "note": "Typed crystal MgO as periclase; the extract states pure solid MgO.",
        "locator": {
            "published_page": 2728,
            "pdf_page_index": 4,
            "table": "5",
            "note": "Table 5 activity standard state",
        },
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    observations = [
        obs
        for obs in result.observations.values()
        if "guo_2021_table5_mgo_activities_1873k" in obs.observation_id
    ]
    assert len(observations) == 8
    for obs in observations:
        reference_state = obs.identity.reference_state
        assert reference_state is not None and reference_state.is_value
        assert reference_state.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference_state.value.endmember.formula == "MgO"
        assert reference_state.value.endmember.phase.value is Phase.CR
        assert reference_state.value.endmember.polymorph.value is Polymorph.PERICLASE


def test_ohta_feto_standard_state_lifts_liquid_reference(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "ohta-1996-cao-mgo-al2o3-activities.yaml",
        use_repository_index_row=True,
    )
    observation = next(
        obs
        for obs in result.observations.values()
        if "ohta_1996_feto_activity_coefficient_spinel_line" in obs.observation_id
    )
    reference_state = observation.identity.reference_state
    assert reference_state is not None and reference_state.is_value
    assert (
        reference_state.value.convention
        is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    )
    assert reference_state.value.component_basis == "Fe_tO"
    assert reference_state.value.endmember.formula == "Fe_tO"
    assert reference_state.value.endmember.phase.value is Phase.L


def test_stolyarova_table3_137_content_ids_and_reference_states_are_stable(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path, "stolyarova-1996-cao-alumina-silica-kems.yaml"
    )
    rows = sorted(
        (oid, obs)
        for oid, obs in result.observations.items()
        if "stolyarova_1996_table3_" in oid
    )
    assert len(rows) == 137
    payload = [(oid, to_plain(obs.identity.reference_state)) for oid, obs in rows]
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    # Adding the published point compositions changes content-stable point ids. This pin
    # keeps their migrated ids and the existing reference states stable from this extract shape.
    assert digest == "1c09862823fb9dd8daebaf6ed44053644f4330d5fec0dd0023cba7a32d564259"


def test_schaefer_photochemical_lifetimes_retain_table4_rate_ancestry(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "schaefer-fegley-2011-vaporization-earth.yaml",
        use_repository_index_row=True,
    )
    lifetime_rows = [
        obs
        for obs in result.observations.values()
        if "schaefer_2012_table4_photochemical_lifetimes" in obs.observation_id
    ]

    assert len(lifetime_rows) == 33
    for obs in lifetime_rows:
        gas = obs.observation_id.rsplit("::rows:row=", 1)[1].split(":", 1)[0]
        rate_record = (
            "schaefer_2012_table4_sio_j1_photolysis_rate"
            if gas == "sio"
            else (
                "schaefer_2012_table4_j1_photolysis_rates"
                if gas == "h2o"
                else f"schaefer_2012_table4_j1_photolysis_rates_{gas}"
            )
        )
        assert obs.derived_from is not None and len(obs.derived_from) == 1
        assert obs.derived_from[0].startswith(
            "schaefer-fegley-2011-vaporization-earth::"
            f"{rate_record}::rows:row={gas}:"
        )
        assert obs.derivation is not None
        assert obs.derivation.inputs == obs.derived_from
        assert "§4.3" in obs.derivation.relation
        assert "PDF page index 24" in obs.derivation.relation
        assert (
            "We then scaled the solar flux as a function of radial distance"
            in obs.derivation.relation
        )
        assert all("table2" not in parent for parent in obs.derived_from)


def test_stolyarova_1996_comparison_provenance_is_activity_only(
    tmp_path: Path,
) -> None:
    from simulator.battery.identity import Identity

    result = _migrate_real_extract(
        tmp_path, "stolyarova-1996-cao-alumina-silica-kems.yaml"
    )
    activities = [
        obs
        for obs in result.observations.values()
        if "stolyarova_1996_table3_" in obs.observation_id
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.ACTIVITY
    ]
    pressures = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "stolyarova-1996-cao-alumina-silica-kems"
        and isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.P_PARTIAL
        and obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    ]

    assert len(activities) == 137
    assert all(
        obs.provenance is not None
        and obs.provenance.get("comparison_method", {}).get("kind")
        == "comparison_ratio"
        for obs in activities
    )
    assert len(pressures) == 108
    assert all(
        not obs.provenance
        or not {
            "comparison_method",
            "common_knudsen_cell_constant",
            "melt_reference_pairing",
        }.intersection(obs.provenance)
        for obs in pressures
    )


def test_stolyarova_1996_printed_compositions_reach_migrated_observations(
    tmp_path: Path,
) -> None:
    from collections import Counter

    from simulator.battery.identity import Identity
    from simulator.battery.score import (
        ScoreContext,
        comparison_candidates,
        rail_for_quantity,
    )

    result = _migrate_real_extract(
        tmp_path, "stolyarova-1996-cao-alumina-silica-kems.yaml"
    )
    rows = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "stolyarova-1996-cao-alumina-silica-kems"
    ]
    composition_rows = [
        obs
        for obs in rows
        if isinstance(obs.identity, Identity)
        and obs.identity.composition is not None
        and obs.identity.composition.is_value
    ]
    assert len(composition_rows) == 300
    for obs in composition_rows:
        assert obs.identity.temperature_K.is_value
        assert obs.identity.temperature_K.value == Decimal("1933")
        condition = obs.point_conditions["composition"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert condition.locator.page in {20, 23}
        assert condition.locator.table in {"2", "3"}

    table2_cao = next(
        obs
        for obs in composition_rows
        if "stolyarova_1996_table2_pCa_1933k" in obs.observation_id
        and "xA/xC=0.11, xS=0.44"
        in obs.point_conditions["composition"].locator.note
    )
    assert dict(table2_cao.identity.composition.value.components) == {
        "CaO": Decimal("0.504504504505"),
        "Al2O3": Decimal("0.055495495495"),
        "SiO2": Decimal("0.44"),
    }

    table3_alumina = next(
        obs
        for obs in composition_rows
        if "stolyarova_1996_table3_al2o3_activity_column_2_1933k"
        in obs.observation_id
        and "xA/xC=0.16, xS=0.50"
        in obs.point_conditions["composition"].locator.note
    )
    assert dict(table3_alumina.identity.composition.value.components) == {
        "CaO": Decimal("0.431034482759"),
        "Al2O3": Decimal("0.068965517241"),
        "SiO2": Decimal("0.50"),
    }

    ion_currents = [obs for obs in rows if "table1_" in obs.observation_id]
    assert len(ion_currents) == 6
    assert all(obs.identity.species.phase.value is Phase.G for obs in ion_currents)
    assert all(quantity_token(obs.identity) is Quantity.ION_INTENSITY for obs in ion_currents)
    figure_rows = [obs for obs in rows if "figure_only" in obs.observation_id]
    assert len(figure_rows) == 6
    assert all(obs.identity.quantity.is_value for obs in figure_rows)
    assert all(obs.identity.quantity.value is Quantity.ACTIVITY for obs in figure_rows)
    assert all(obs.value.kind is ValueKind.UNAVAILABLE for obs in figure_rows)
    assert all(obs.value.unavailable_reason == "figure_not_digitized" for obs in figure_rows)
    assert all(obs.admission.status is AdmissionStatus.REJECTED for obs in figure_rows)
    assert all(obs.locator is not None and obs.locator.figure in {"1", "2"} for obs in figure_rows)
    assert all(
        "bound_not_point_ordering" in obs.locator.note
        and "never a point value" in obs.locator.note
        for obs in figure_rows
    )
    assert all(obs.value.kind is not ValueKind.POINT for obs in figure_rows)
    assert all(obs.identity.species.phase.value is Phase.L for obs in figure_rows)
    candidates = comparison_candidates(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
        )
    )
    assert {obs.observation_id for obs in figure_rows}.isdisjoint(
        obs.observation_id for obs in candidates
    )
    candidates = [
        obs
        for obs in candidates
        if obs.source_id == "stolyarova-1996-cao-alumina-silica-kems"
    ]
    candidate_rails = Counter(
        str(
            rail_for_quantity(
                obs.identity.quantity.value if obs.identity.quantity.is_value else None,
                species_formula=obs.identity.species.formula,
            )
        )
        for obs in candidates
    )
    assert candidate_rails == {
        "None": 6,
        "SiO_evolution": 45,
        "melt_activity": 137,
        "vapour": 118,
    }


def test_comparison_provenance_is_not_inherited_by_unknown_quantity_points(
    tmp_path: Path,
) -> None:
    from simulator.battery.identity import Identity

    result = _migrate_real_extract(tmp_path, "kems-ms2000-044.yaml")
    comparison_rows = [
        obs
        for obs in result.observations.values()
        if obs.provenance and "comparison_method" in obs.provenance
    ]
    assert comparison_rows
    assert all(
        isinstance(obs.identity, Identity)
        and quantity_token(obs.identity)
        in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}
        for obs in comparison_rows
    )


def test_reference_prose_keeps_printed_endmember_and_does_not_stamp_one_bar() -> None:
    feo = reference_state_from_extract(
        "raoultian_pure_endmember; pure liquid FeO endmember=FeO. "
        "The pure condensed solid/liquid activity is unity.",
        species_formula="Fe",
        values={},
    )
    assert feo is not None and feo.is_value
    assert feo.value.endmember.formula == "FeO"
    assert feo.value.endmember.phase.is_value
    assert feo.value.endmember.phase.value is Phase.L
    assert feo.value.reference_pressure_bar is None

    ichise = reference_state_from_extract(
        "Fe(l)=Fe (in alloy), Raoultian liquid Fe; "
        "Mo(s)=Mo (in alloy), Raoultian solid Mo",
        species_formula="Fe",
        values={},
    )
    assert ichise is not None and ichise.is_value
    assert ichise.value.endmember.formula == "Fe"
    assert ichise.value.endmember.phase.value is Phase.L

    ueshima = reference_state_from_extract(
        "a_Fe liquid Fe; a_W solid W",
        species_formula="Fe",
        values={},
    )
    assert ueshima is not None and ueshima.is_value
    assert ueshima.value.endmember.formula == "Fe"
    assert ueshima.value.endmember.phase.value is Phase.L

    ambiguous = reference_state_from_extract(
        "Fe(l)=Fe (in alloy), Raoultian liquid Fe; "
        "Mo(s)=Mo (in alloy), Raoultian solid Mo",
        species_formula="not-either",
        values={},
    )
    assert ambiguous is not None and ambiguous.is_unknown

    sossi = reference_state_from_extract(
        "raoultian_pure_endmember; pure liquid oxide at the temperature and "
        "pressure of interest; endmember=NaO0.5; the coefficient is the "
        "Henry/infinite-dilution limit",
        species_formula="Na",
        values={},
    )
    assert sossi is not None and sossi.is_value
    assert sossi.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert sossi.value.endmember.formula == "NaO0.5"
    assert sossi.value.endmember.phase.value is Phase.L
    assert sossi.value.reference_pressure_bar is None
    plain = to_plain(sossi.value)
    assert isinstance(plain, dict)
    assert "reference_pressure_bar" not in plain
    assert _standard_state_from_plain(plain).reference_pressure_bar is None

    printed_bar = reference_state_from_extract(
        "pure liquid Na2O at 1 bar endmember=Na2O",
        species_formula="Na2O",
        values={},
    )
    assert printed_bar is not None and printed_bar.is_value
    assert printed_bar.value.reference_pressure_bar == as_decimal("1")

    sodium = reference_state_from_extract(
        "γ°_Na is the infinite-dilution (Henry) coefficient in Pb such that "
        "Raoultian a_Na = γ°_Na · X_Na. "
        "「a_Na: ラウール基準のナトリウムの活量」.",
        species_formula="Na",
        values={},
    )
    assert sodium is not None and sodium.is_value
    assert sodium.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert sodium.value.endmember.formula == "Na"

    henry = reference_state_from_extract(
        "infinite-dilution activity coefficient of Al in liquid Fe",
        species_formula="Al",
        values={},
    )
    assert henry is not None and henry.is_value
    assert henry.value.convention is ReferenceStateConvention.HENRIAN_LIQUID
    assert henry.value.endmember.phase.value is Phase.L


def test_ts1985_and_yam1983_tridymite_reference_state_baseline() -> None:
    ts1985 = next(
        row
        for row in _extract_observations("ts1985.yaml")
        if row.get("observation_id") == "ts1985_sio2_gibbs_duhem_1200C_X0500"
    )
    ts_state = reference_state_from_extract(
        ts1985["standard_state"], species_formula="SiO2", values=ts1985["values"]
    )
    assert ts_state is not None and ts_state.is_value
    assert ts_state.value.endmember.polymorph is not None
    assert ts_state.value.endmember.polymorph.is_value
    assert ts_state.value.endmember.polymorph.value is Polymorph.TRIDYMITE

    allibert = next(
        row
        for row in _extract_observations("kems-051-allibert-1981.yaml")
        if row.get("observation_id")
        == "allibert_1981_table2_alumina_activity_kems"
    )
    allibert_state = reference_state_from_extract(
        allibert["standard_state"],
        species_formula="Al2O3",
        values=allibert["values"],
    )
    assert allibert_state is not None and allibert_state.is_value
    allibert_polymorph = allibert_state.value.endmember.polymorph
    assert allibert_polymorph is not None and allibert_polymorph.is_value
    assert allibert_polymorph.value is Polymorph.CORUNDUM

    without_printed_form = reference_state_from_extract(
        allibert["standard_state"].replace("alpha-Al2O3", "Al2O3"),
        species_formula="Al2O3",
        values=allibert["values"],
    )
    assert without_printed_form is not None and without_printed_form.is_value
    missing_polymorph = without_printed_form.value.endmember.polymorph
    assert missing_polymorph is not None and missing_polymorph.is_unknown
    assert missing_polymorph.reason == "source does not state polymorph"

    ca2sio4_alpha = next(
        row
        for row in _extract_observations(
            "robie-hemingway-wilson-1980-usgs-of-80-908.yaml"
        )
        if row.get("observation_id")
        == "t1101_robie1980_ca2sio4_alpha_summary_29815_absent"
    )
    ca2sio4_state = reference_state_from_extract(
        ca2sio4_alpha["standard_state"],
        species_formula="Ca2SiO4_alpha",
        values=ca2sio4_alpha["values"],
    )
    assert ca2sio4_state is not None and ca2sio4_state.is_value
    ca2sio4_polymorph = ca2sio4_state.value.endmember.polymorph
    assert ca2sio4_polymorph is not None and ca2sio4_polymorph.is_value
    assert ca2sio4_polymorph.value is Polymorph.ALPHA

    ambiguous = reference_state_from_extract(
        "Pure solid tridymite or quartz; endmember=SiO2",
        species_formula="SiO2",
        values={},
    )
    assert ambiguous is not None and ambiguous.is_value
    ambiguous_polymorph = ambiguous.value.endmember.polymorph
    assert ambiguous_polymorph is not None and ambiguous_polymorph.is_unknown
    assert ambiguous_polymorph.reason == "source names more than one polymorph"

    coefficient = reference_state_from_extract(
        "Raoultian pure solid SiO2; gamma_SiO2 relative to solid SiO2",
        species_formula="SiO2",
        values={},
    )
    assert coefficient is not None and coefficient.is_value
    coefficient_polymorph = coefficient.value.endmember.polymorph
    assert coefficient_polymorph is not None and coefficient_polymorph.is_unknown
    assert coefficient_polymorph.reason == "source does not state polymorph"

    table_number = reference_state_from_extract(
        "solid CaO standard state; Table II footnote",
        species_formula="CaO",
        values={},
    )
    assert table_number is not None and table_number.is_value
    table_polymorph = table_number.value.endmember.polymorph
    assert table_polymorph is not None and table_polymorph.is_unknown
    assert table_polymorph.reason == "source does not state polymorph"

    yam1983 = next(
        row
        for row in _extract_observations("yam1983.yaml")
        if row.get("observation_id") == "yam1983_sio2_table2_minus_log10_a_AT_B"
    )
    yam_state = reference_state_from_extract(
        yam1983["standard_state"], species_formula="SiO2", values=yam1983["values"]
    )
    assert yam_state is not None and yam_state.is_unknown
    assert yam_state.reason == (
        "source standard_state does not name one reference endmember"
    )


def test_reference_prose_binds_polymorphs_to_their_own_endmember() -> None:
    ms2000_text = (
        "raoultian pure solid; beta-Na2O, solid K2O, and quartz as published; "
        "endmember=K2O"
    )
    k2o_state = reference_state_from_extract(
        ms2000_text, species_formula="K2O", values={}
    )
    assert k2o_state is not None and k2o_state.is_value
    assert k2o_state.value.endmember.phase.value is Phase.CR
    assert k2o_state.value.endmember.polymorph.is_unknown

    na2o_state = reference_state_from_extract(
        ms2000_text.replace("endmember=K2O", "endmember=Na2O"),
        species_formula="Na2O",
        values={},
    )
    assert na2o_state is not None and na2o_state.is_value
    assert na2o_state.value.endmember.polymorph.is_value
    assert na2o_state.value.endmember.polymorph.value is Polymorph.BETA

    multi_endmember_text = (
        "raoultian pure solid; quartz for SiO2 and corundum for Al2O3; "
        "endmember=SiO2"
    )
    quartz_state = reference_state_from_extract(
        multi_endmember_text, species_formula="SiO2", values={}
    )
    corundum_state = reference_state_from_extract(
        multi_endmember_text.replace("endmember=SiO2", "endmember=Al2O3"),
        species_formula="Al2O3",
        values={},
    )
    assert quartz_state is not None and quartz_state.is_value
    assert quartz_state.value.endmember.polymorph.value is Polymorph.QUARTZ
    assert corundum_state is not None and corundum_state.is_value
    assert corundum_state.value.endmember.polymorph.value is Polymorph.CORUNDUM


@pytest.mark.parametrize(
    ("filename", "experiment_id"),
    (
        ("kems-010-richter-2007.yaml", "type-b-cai-like-lab-melt"),
        ("kems-015-hashimoto-1983.yaml", "fcmas-free-evap-series"),
    ),
)
def test_richter_hashimoto_declared_experiments_resolve_langmuir_method(
    tmp_path: Path, filename: str, experiment_id: str
) -> None:
    result = _migrate_real_extract(tmp_path / filename, filename)
    experiment = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith(f"::experiment::{experiment_id}")
    )
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION


def test_allibert_alumina_polymorph_binds_in_isolated_migration(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-051-allibert-1981.yaml")
    rows = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.startswith(
            "kems-051-allibert-1981::allibert_1981_table2_alumina_activity_kems"
        )
    ]

    assert len(rows) == 8
    for observation in rows:
        reference_state = observation.identity.reference_state
        assert reference_state is not None and reference_state.is_value
        polymorph = reference_state.value.endmember.polymorph
        assert polymorph is not None and polymorph.is_value
        assert polymorph.value is Polymorph.CORUNDUM


def test_reference_prose_rejects_ambiguous_or_negated_raoult_conventions() -> None:
    furukawa = next(
        row
        for row in _extract_observations("kems-119-furukawa-1975.yaml")
        if row.get("observation_id") == "furukawa_1975_quoted_fruehan_gamma_v"
    )
    mixed = reference_state_from_extract(
        furukawa["standard_state"],
        species_formula="V",
        values=furukawa["values"],
    )
    assert mixed is not None and mixed.is_unknown

    plante = next(
        row
        for row in _extract_observations("kems-027-plante-hastie-1983.yaml")
        if row.get("observation_id") == "plante_hastie_1983_nabo2_activity_approx"
    )
    negated = reference_state_from_extract(
        plante["standard_state"]["note"],
        species_formula="NaBO2",
        values=plante["values"],
    )
    assert negated is not None and negated.is_unknown
    typed = reference_state_from_extract(
        plante["standard_state"],
        species_formula="NaBO2",
        values=plante["values"],
    )
    assert typed is not None and typed.is_value
    assert typed.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
    assert typed.value.endmember.formula == "NaBO2"
    assert typed.value.endmember.phase.value is Phase.L


def test_demaria_fe_rows_use_the_pure_iron_reference_state(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-022-demaria-1971.yaml")
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    expected = {
        "demaria_1971_fe_lunar_basalt_kems_main_cell",
        "demaria_1971_fe_activity_multi_rotating_cell",
    }
    loaded = {
        obs.observation_id.split("::", 1)[1]: obs
        for obs in result.observations.values()
        if obs.observation_id.split("::", 1)[-1] in expected
    }
    assert set(loaded) == expected
    for obs in loaded.values():
        reference = obs.identity.reference_state
        assert reference is not None and reference.is_value
        assert reference.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert reference.value.endmember.formula == "Fe"
        assert reference.value.endmember.phase.value is Phase.CR
        assert reference.value.component_basis == "Fe"
        polymorph = reference.value.endmember.polymorph
        assert polymorph is not None and polymorph.is_unknown
        assert polymorph.reason == "source does not state polymorph"


def test_demaria_partial_alkalis_do_not_become_a_complete_composition(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-022-demaria-1971.yaml")
    rows = {
        obs.observation_id.split("::", 1)[1]: obs
        for obs in result.observations.values()
        if obs.source_id == "kems-022-demaria-1971"
    }
    for prefix in (
        "demaria_1971_na_psat_figure1_sample_12022_digitized",
        "demaria_1971_na_psat_figure1_sample_12065_digitized",
    ):
        related = [obs for key, obs in rows.items() if key.startswith(prefix)]
        assert related
        assert all(obs.identity.composition is not None for obs in related)
        assert all(obs.identity.composition.is_unknown for obs in related)
        assert all(
            obs.identity.composition.reason == "no composition mapped from source"
            for obs in related
        )

    extract_rows = _extract_observations("kems-022-demaria-1971.yaml")
    sample_12022 = next(
        row
        for row in extract_rows
        if row.get("observation_id")
        == "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    values = sample_12022["values"]
    assert values["sample_alkali_content_wt_pct"] == {
        "Na2O": 0.36,
        "K2O": 0.068,
    }
    assert "reported elsewhere (SMALES, 1971)" in values["composition_source_gap"]
    assert "composition_from_sample_catalog" not in values
    assert "sample_oxide_composition_wt_pct" not in values
    assert "printed_composition" not in values
    sample_12065 = next(
        row
        for row in extract_rows
        if row.get("observation_id")
        == "demaria_1971_na_psat_figure1_sample_12065_digitized"
    )
    assert "does not print a bulk composition" in sample_12065["values"][
        "composition_source_gap"
    ]


def test_demaria_undigitized_pressure_figures_are_unavailable_not_points(
    tmp_path: Path,
) -> None:
    from simulator.battery.score import ScoreContext, comparison_candidates

    result = _migrate_real_extract(tmp_path, "kems-022-demaria-1971.yaml")
    expected = {
        "demaria_1971_ca_figure_only_and_measurement_difficulty",
        "demaria_1971_tio_tio2_figure6_comparable_pressures",
        "demaria_1971_tio2_figure6_comparable_to_tio",
        "demaria_1971_al_monatomic_figure7",
    }
    rows = {
        obs.observation_id.split("::", 1)[1]: obs
        for obs in result.observations.values()
        if obs.source_id == "kems-022-demaria-1971"
    }
    assert expected <= rows.keys()
    extract_rows = {
        row["observation_id"]: row
        for row in _extract_observations("kems-022-demaria-1971.yaml")
    }
    for observation_id in expected:
        obs = rows[observation_id]
        source_values = extract_rows[observation_id]["values"]
        assert "points" not in source_values
        assert source_values["pressure_atm"] is None
        assert quantity_token(obs.identity) is Quantity.P_PARTIAL
        assert obs.identity.species.phase.is_value
        assert obs.identity.species.phase.value is Phase.G
        assert obs.evidence.class_.is_value
        assert obs.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert obs.value.kind is ValueKind.UNAVAILABLE
        assert obs.value.unavailable_reason
        assert obs.admission.status is AdmissionStatus.REJECTED

    candidates = comparison_candidates(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
        )
    )
    assert expected.isdisjoint(
        obs.observation_id.split("::", 1)[1] for obs in candidates
    )


def test_tsaplin_gibbs_duhem_sio2_is_not_measured_direct(tmp_path: Path) -> None:
    from simulator.battery.score import (
        ScoreContext,
        comparison_candidates,
        score_store,
    )

    result = _migrate_real_extract(tmp_path, "kems-ms2000-044.yaml")
    r2 = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith("_class_quote_r2")
        and "sio2_activity" in obs.observation_id
    ]
    assert len(r2) == 24
    r2_ids = {obs.observation_id for obs in r2}
    for obs in r2:
        evidence = obs.evidence.class_
        assert obs.evidence.original_method_class == "derived"
        assert not (evidence.is_value and evidence.value in MEASURED_EVIDENCE)
        reference = obs.identity.reference_state
        assert reference is not None and reference.is_value
        assert reference.value.endmember.formula == "SiO2"
        assert reference.value.endmember.phase.value is Phase.L
        assert reference.value.reference_pressure_bar is None
        species_phase = obs.identity.species.phase
        assert species_phase is not None and species_phase.is_value
        assert species_phase.value is Phase.L
    superseded = [
        obs
        for obs in result.observations.values()
        if obs.admission.superseded_by in r2_ids
    ]
    assert len(superseded) == 24
    for obs in superseded:
        assert obs.admission.status is AdmissionStatus.SUPERSEDED
        assert obs.evidence.class_.is_value
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    candidates = comparison_candidates(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
        )
    )
    candidate_ids = {obs.observation_id for obs in candidates}
    assert r2_ids <= candidate_ids
    assert {obs.observation_id for obs in superseded}.isdisjoint(candidate_ids)
    residuals, _ = score_store(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
            benches=result.benches,
        ),
        engines=(Engine.OPENIMCC,),
        rail=Rail.MELT_ACTIVITY,
    )
    silica_residuals = [row for row in residuals if row.reference in r2_ids]
    assert len(silica_residuals) == 24
    assert all(row.status is ResidualStatus.NO_BAND for row in silica_residuals)
    assert all(row.numeric is not None for row in silica_residuals)
    assert all(row.numeric.value.is_finite() for row in silica_residuals if row.numeric)
    assert all(
        any(
            notice.kind is NoticeKind.FIGURE_ONLY
            and notice.reason == "evidence_class:model_derived"
            for notice in row.notices
        )
        for row in silica_residuals
    )


def test_unprinted_temperature_envelopes_are_not_loaded(tmp_path: Path) -> None:
    arxiv_rows = _extract_observations("arxiv-1902-05005.yaml")
    table2 = next(
        row
        for row in arxiv_rows
        if row.get("observation_id") == "sossi_fegley_2018_table2_activity_coefficients"
    )
    assert "T_range_K" not in table2
    assert "standard_state" not in table2
    arxiv = _migrate_real_extract(tmp_path, "arxiv-1902-05005.yaml")
    loaded = next(
        obs
        for obs in arxiv.observations.values()
        if "sossi_fegley_2018_table2_activity_coefficients" in obs.observation_id
    )
    reference = loaded.identity.reference_state
    assert reference is None or not reference.is_value or (
        reference.value.endmember.formula not in {"FeO", "FeO."}
    )
    temperature = loaded.identity.temperature_K
    if temperature is not None and temperature.is_unknown:
        assert "1573" not in (temperature.reason or "")
        assert "1923" not in (temperature.reason or "")

    demaria_rows = _extract_observations("kems-022-demaria-1971.yaml")
    rotating = next(
        row
        for row in demaria_rows
        if row.get("observation_id") == "demaria_1971_fe_activity_multi_rotating_cell"
    )
    assert "T_range_K" not in rotating


def test_ts1985_printed_binary_complement_is_not_a_float_residue() -> None:
    rows = [
        row
        for row in _extract_observations("ts1985.yaml")
        if isinstance(row.get("values"), dict)
        and row["values"].get("X_Na2O_as_published") == 0.55
    ]
    assert len(rows) == 3
    for row in rows:
        composition, _omitted = _mole_fraction_composition_from_values(row["values"])
        assert composition is not None
        assert composition.as_map() == {
            "Na2O": as_decimal("0.55"),
            "SiO2": as_decimal("0.45"),
        }


def test_dacko_minor_constituents_are_omitted_from_activity_composition() -> None:
    rows = [
        row
        for row in _extract_observations("ta-dacko-conradt-low-p-transpiration.yaml")
        if str(row.get("observation_id") or "").startswith("dacko_2004_table2_activity_")
    ]
    assert len(rows) == 6
    for row in rows:
        composition, omitted = _mole_fraction_composition_from_values(row["values"])
        assert composition is None
        assert "minor constituents" in omitted


def test_kume_activity_values_use_schema_type_and_map_to_activity() -> None:
    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/kume-2000-cao-activities.yaml")
        .read_text(encoding="utf-8")
    )
    rows = []
    for species in ("CaO", "SiO2"):
        block = extract["species"][species]
        rows.extend(
            row
            for row in block["observations"]
            if "activity" in (row.get("values") or {})
        )

    assert len(rows) == 208
    for row in rows:
        assert row["type"] == "activity_coefficient", row["observation_id"]
        quantity, reason = map_quantity(
            row.get("type"), row.get("values"), units=row.get("units"), row=row
        )
        assert quantity.is_value and quantity.value is Quantity.ACTIVITY
        assert reason is None

    expected_observables = {
        "kume_2000_table1_sample_1": "activity_coefficient",
        "kume_2000_table2_sample_101": "activity_coefficient",
        "kume_2000_table3_sample_201": "activity_coefficient",
        "kume_2000_table4_sample_301": "activity_coefficient",
        "kume_2000_table3_sample_207_sio2_activity": "activity_coefficient",
        "kume_2000_table3_sample_207_mgo_activity": "activity_coefficient",
        "kume_2000_table4_sample_306_mgo_activity": "activity_coefficient",
        "kume_2000_table4_sample_306_alo1_5_activity": "activity_coefficient",
        "kume_2000_table3_sample_207_metal_composition": "concentration_series",
        "kume_2000_table4_sample_306_metal_composition": "concentration_series",
    }
    assert {
        sample["observation_id"]: sample["observable"]
        for sample in extract["fidelity_samples"]
    } == expected_observables


def test_kume_experiment_temperatures_have_table_locators(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path, "kume-2000-cao-activities.yaml", write=False
    )
    expected = {
        "kume-2000-table1-1823k": (Decimal("1823"), "1", 562),
        "kume-2000-table1-1873k": (Decimal("1873"), "1", 562),
        "kume-2000-table2-1823k": (Decimal("1823"), "2", 563),
        "kume-2000-table3-1873k": (Decimal("1873"), "3", 564),
        "kume-2000-table4-1873k": (Decimal("1873"), "4", 565),
        "kume-2000-table4-rein-chipman-1873k": (Decimal("1873"), "4", 565),
    }
    seen = set()
    for experiment in result.experiments.values():
        local_id = experiment.experiment_id.rsplit("::", 1)[-1]
        if local_id not in expected:
            continue
        temperature, table, page = expected[local_id]
        condition = experiment.conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == temperature
        assert condition.locator is not None
        assert str(condition.locator.table) == table
        assert condition.locator.page == page
        seen.add(local_id)

    assert seen == set(expected)


def test_kume_activity_compositions_map_to_parent_oxide_basis(tmp_path: Path) -> None:
    name = "kume-2000-cao-activities.yaml"
    result = _migrate_real_extract(tmp_path / "real", name)
    observations = {
        obs.observation_id.rsplit("::", 1)[-1]: obs
        for obs in result.observations.values()
    }

    table2 = observations["kume_2000_table2_sample_101"]
    assert table2.identity.composition is not None
    assert table2.identity.composition.is_value
    # Hand conversion: 0.085 mol AlO1.5 is 0.0425 mol Al2O3;
    # the renormalised total is 0.876 + 0.039 + 0.0425 = 0.9575.
    assert table2.identity.composition.value.as_map() == {
        "SiO2": Decimal("0.876") / Decimal("0.9575"),
        "CaO": Decimal("0.039") / Decimal("0.9575"),
        "Al2O3": Decimal("0.0425") / Decimal("0.9575"),
    }

    table4 = observations["kume_2000_table4_sample_301"]
    assert table4.identity.composition is not None
    assert table4.identity.composition.is_value
    raw_table4 = next(
        row
        for row in _extract_observations(name)
        if row.get("observation_id") == "kume_2000_table4_sample_301"
    )
    table4_mass = _initial_oxide_map_from_values(raw_table4["values"])
    assert table4_mass is not None
    assert table4_mass["Al2O3"] == Decimal("27.8")
    assert "AlO1.5" not in table4_mass

    # A chemically parseable but unsupported oxide must invalidate the whole
    # composition; mapping only the components we happen to recognise is lossy.
    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    extract["source_id"] = "fixture-source"
    raw_table2 = next(
        row
        for row in _extract_observations(name)
        if row.get("observation_id") == "kume_2000_table2_sample_101"
    )
    raw_table2["values"]["composition_mole_fraction"]["ZnO"] = 0.001
    for block in extract["species"].values():
        block["observations"] = [
            raw_table2
            if row.get("observation_id") == "kume_2000_table2_sample_101"
            else row
            for row in block.get("observations", [])
        ]
    unknown_result = migrate(_write_min_tree(tmp_path / "unknown", extract), write=False)
    unknown_observation = next(
        obs
        for obs in unknown_result.observations.values()
        if obs.observation_id.endswith("::kume_2000_table2_sample_101")
    )
    assert unknown_observation.identity.composition is not None
    assert unknown_observation.identity.composition.is_unknown


def test_kume_tables_3_and_4_printed_compositions_and_activities_bind(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path / "real", "kume-2000-cao-activities.yaml"
    )
    observations = {
        obs.observation_id.rsplit("::", 1)[-1]: obs
        for obs in result.observations.values()
    }

    sio2_rows = [
        obs for obs in observations.values()
        if obs.observation_id.endswith("_sio2_activity")
    ]
    assert len(sio2_rows) == 104
    assert all(
        obs.identity.composition is not None
        and obs.identity.composition.is_value
        for obs in sio2_rows
    )
    for sample_id in (
        "kume_2000_table3_sample_207",
        "kume_2000_table4_sample_306",
    ):
        assert (
            observations[f"{sample_id}_sio2_activity"].identity.composition
            == observations[sample_id].identity.composition
        )

    mgo_rows = [
        obs for obs in observations.values()
        if obs.observation_id.endswith("_mgo_activity")
    ]
    alo_rows = [
        obs for obs in observations.values()
        if obs.observation_id.endswith("_alo1_5_activity")
    ]
    assert len(mgo_rows) == 45
    assert len(alo_rows) == 18

    table3_mgo = observations["kume_2000_table3_sample_207_mgo_activity"]
    assert table3_mgo.value.point == Decimal("0.103")
    assert (
        table3_mgo.identity.composition
        == observations["kume_2000_table3_sample_207"].identity.composition
    )
    table4_mgo = observations["kume_2000_table4_sample_306_mgo_activity"]
    table4_al = observations["kume_2000_table4_sample_306_alo1_5_activity"]
    assert table4_mgo.value.point == Decimal("0.362")
    assert table4_al.value.point == Decimal("0.0076")
    assert table4_al.identity.composition.value.as_map()["Al2O3"] > 0
    assert "AlO1.5" not in table4_al.identity.composition.value.as_map()


@pytest.mark.parametrize(
    ("composition_key", "observation_id"),
    [
        ("composition_mole_fraction", "kume_2000_table2_sample_101"),
        ("composition_mass_percent", "kume_2000_table4_sample_301"),
    ],
)
def test_kume_malformed_declared_composition_amount_makes_whole_composition_unknown(
    tmp_path: Path, composition_key: str, observation_id: str
) -> None:
    name = "kume-2000-cao-activities.yaml"
    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    extract["source_id"] = "fixture-source"
    row = next(
        row
        for row in extract["species"]["CaO"]["observations"]
        if row.get("observation_id") == observation_id
    )
    composition = row["values"][composition_key]
    component = "CaO"
    assert component in composition and len(composition) > 2
    composition[component] = "not-a-number"

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(
        item
        for item in result.observations.values()
        if item.observation_id.endswith(f"::{observation_id}")
    )
    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    hard = result.validation.hard_issues if result.validation is not None else ()
    key_matches = [
        issue
        for issue in hard
        if issue.reason is RefusalReason.INVALID_SOURCE
        and composition_key in (issue.path or "")
        and "malformed declared amount" in (issue.detail or "")
    ]
    if composition_key == "composition_mole_fraction":
        assert len(key_matches) == 1
    else:
        assert key_matches == []


def _score_activity_composition_case(
    tmp_path: Path, *, malformed: bool, exploded: bool
):
    values = {
        "quantity": "activity",
        "activity": 0.2,
        "method_class": "measured_direct",
    }
    if malformed:
        values["composition_mol"] = {
            "SiO2": 0.6,
            "MgO": "not-a-number",
            "CaO": 0.4,
        }
    if exploded:
        values["points"] = [{"T_K": 1773.15, "activity": 0.2}]
    extract = _scalar_extract(
        quantity="activity",
        units="dimensionless",
        values=values,
        obs_type="activity_coefficient",
    )
    row = extract["species"]["Na"]["observations"][0]
    row.update(
        {
            "experiment": "sample",
            "T_K": 1773.15,
            "standard_state": "Raoultian pure liquid MgO",
        }
    )
    extract["species"] = {"MgO": {"observations": [row]}}
    extract["experiments"] = [
        {
            "experiment_id": "sample",
            "sample": {
                "printed_composition": {
                    "state": {"tag": "value", "value": {"SiO2": 50, "MgO": 50}},
                    "locator": {"table": "1", "page": 1},
                }
            },
        }
    ]
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(iter(result.observations.values()))
    assert ("::T=" in observation.observation_id) is exploded
    experiment = result.experiments[observation.experiment_id]
    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )
    return observation, experiment, prediction


@pytest.mark.parametrize("exploded", [False, True], ids=["row", "exploded-child"])
def test_malformed_activity_composition_is_terminal_refusal(
    tmp_path: Path, exploded: bool
) -> None:
    observation, _experiment, prediction = _score_activity_composition_case(
        tmp_path, malformed=True, exploded=exploded
    )

    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.INVALID_SOURCE
    assert prediction.refusal_detail["reason"] == RefusalReason.INVALID_SOURCE.value
    (gap,) = prediction.refusal_detail["gaps"]
    assert gap["waypoint"] == "normalized_composition"
    assert gap["reason"] == "unsupported_print_form"
    assert gap["missing"] == [
        f"observation[{observation.observation_id}].point_conditions.composition"
    ]


def test_activity_without_row_composition_uses_sample_numeric_pin(tmp_path: Path) -> None:
    _observation, _experiment, prediction = _score_activity_composition_case(
        tmp_path, malformed=False, exploded=False
    )

    assert prediction.value is not None
    assert float(prediction.value).hex() == "0x1.a4079127a495dp-5"
    assert prediction.refusal_reason is None
    assert prediction.requested_composition is not None
    assert prediction.requested_composition.is_value
    assert prediction.requested_composition.value.as_map() == {
        "SiO2": Decimal("0.40148624821939094"),
        "MgO": Decimal("0.5985137517806091"),
    }


@pytest.mark.parametrize("printed_marker", ["<0.01", "tr."])
def test_kume_bound_and_trace_composition_amounts_are_typed_absence(
    tmp_path: Path, printed_marker: str
) -> None:
    name = "kume-2000-cao-activities.yaml"
    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    extract["source_id"] = "fixture-source"
    observation_id = "kume_2000_table2_sample_101"
    row = next(
        row
        for row in extract["species"]["CaO"]["observations"]
        if row.get("observation_id") == observation_id
    )
    row["values"]["composition_mole_fraction"]["CaO"] = printed_marker

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(
        item
        for item in result.observations.values()
        if item.observation_id.endswith(f"::{observation_id}")
    )
    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    hard = result.validation.hard_issues if result.validation is not None else ()
    assert not any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "composition_mole_fraction" in (issue.path or "")
        for issue in hard
    )


def test_kume_measured_reduced_activity_preserves_structured_derivation(
    tmp_path: Path,
) -> None:
    name = "kume-2000-cao-activities.yaml"
    result = _migrate_real_extract(tmp_path, name)
    observation_id = "kume_2000_table2_sample_101"
    observation = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith(f"::{observation_id}")
    )
    raw = next(
        row
        for row in _extract_observations(name)
        if row.get("observation_id") == observation_id
    )
    raw_derivation = raw["derivation"]

    assert observation.derivation is not None
    assert observation.derivation.relation == raw_derivation["relation"]
    assert observation.derivation.inputs == tuple(
        f"kume-2000-cao-activities::{item}"
        for item in raw_derivation["inputs"]
    )
    assert not any(
        entry.observation_id == observation.observation_id
        and entry.axes in (["derivation"], ["derived_from"])
        for entry in result.queue
    )


def test_kume_tables3_and4_derivation_evidence_fresh_migration_and_mutations(
    tmp_path: Path,
) -> None:
    name = "kume-2000-cao-activities.yaml"
    result = _migrate_real_extract(tmp_path / "fresh", name)
    assert len(result.observations) == 375
    assert result.validation is not None
    assert not result.validation.hard_issues

    table3_id = "kume_2000_table3_sample_207_mgo_activity"
    table4_id = "kume_2000_table4_sample_306_mgo_activity"
    table3 = next(
        obs for obs in result.observations.values()
        if obs.observation_id.endswith(f"::{table3_id}")
    )
    table4 = next(
        obs for obs in result.observations.values()
        if obs.observation_id.endswith(f"::{table4_id}")
    )
    assert table3.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert table3.derivation is not None
    assert "exchange reaction (3) and Eq. (4)" in table3.derivation.relation
    assert table3.derivation.inputs == table3.derived_from
    assert table4.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert table4.derived_from is None
    assert table4.derivation is None

    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    extract["source_id"] = "fixture-source"
    rows = {
        row["observation_id"]: row
        for block in extract["species"].values()
        for row in block["observations"]
    }
    rows[table3_id].pop("derivation")
    mutated_table4 = rows[table4_id]
    mutated_table4["values"]["method_class"] = "measured_reduced"
    mutated_table4["values"]["evidence_class"] = "measured_reduced"
    mutated_table4["derived_from"] = ["missing-parent"]
    mutated = migrate(_write_min_tree(tmp_path / "mutated", extract), write=False)
    issues = {
        issue.path: issue.detail
        for issue in mutated.validation.hard_issues
    }
    assert issues[
        f"observation[fixture-source::{table3_id}].derivation"
    ] == "derived observation requires derivation"
    assert issues[
        f"observation[fixture-source::{table4_id}].derivation"
    ] == "derived observation requires derivation"


def test_unregistered_table_assets_are_referential_issues(
    tmp_path: Path,
) -> None:
    name = "kume-2000-cao-activities.yaml"
    extract = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / name).read_text(
            encoding="utf-8"
        )
    )
    extract["source_id"] = "fixture-source"
    observation_id = "kume_2000_table2_sample_101"
    unregistered_parent = "tables:fixture-source"
    unregistered_input = "tables:fixture-source/not-registered.csv"
    unregistered_placeholder = "unknown:unregistered-fixture"
    row = next(
        row
        for block in extract["species"].values()
        for row in block["observations"]
        if row.get("observation_id") == observation_id
    )
    row["values"]["method_class"] = "calculated"
    row["derived_from"] = [unregistered_parent]
    row["derivation"]["inputs"] = [unregistered_input, unregistered_placeholder]
    no_inputs_row = deepcopy(row)
    no_inputs_row["observation_id"] = f"{observation_id}_without_inputs"
    no_inputs_row["derivation"]["inputs"] = []
    next(
        block
        for block in extract["species"].values()
        if row in block["observations"]
    )["observations"].append(no_inputs_row)

    root = _write_min_tree(tmp_path, extract)
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index["sources"][0]["corpus"]["tables"] = {
        "exists": False,
        "path": "tables/fixture-source",
    }
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    result = migrate(root, write=False)
    observation = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith(f"::{observation_id}")
    )
    work = result.works[result.experiments[observation.experiment_id].work_id]
    registered_assets = {asset.asset_id for asset in work.source_files.files}
    assert "unknown:fixture-source" in registered_assets
    assert unregistered_placeholder not in registered_assets
    assert observation.derived_from == (unregistered_parent,)
    assert observation.derivation is not None
    assert observation.derivation.inputs == (
        unregistered_input,
        unregistered_placeholder,
    )
    assert observation.evidence.class_.is_unknown
    issues = [
        issue
        for issue in result.validation.hard_issues
        if issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
    ]
    assert {
        (issue.path.rsplit(".", 1)[-1], issue.detail)
        for issue in issues
    } >= {
        ("derived_from", f"derived_from {unregistered_parent!r} does not resolve"),
        (
            "inputs",
            f"derivation input {unregistered_input!r} does not resolve",
        ),
        (
            "inputs",
            f"derivation input {unregistered_placeholder!r} does not resolve",
        ),
    }
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(f"::{observation_id}_without_inputs.derivation")
        and issue.detail
        == (
            "invalid derivation annotation: Derivation.inputs is required unless "
            "raw_inputs is located not_printed"
        )
        for issue in result.validation.hard_issues
    )


def test_registered_table_asset_resolves_as_lineage(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    table_asset = "tables:fixture-source"
    row["values"]["method_class"] = "measured_reduced"
    row["derived_from"] = [table_asset]
    row["derivation"] = {
        "relation": "author_mass_balance_reduction",
        "inputs": [table_asset],
        "output_unit": "Pa",
    }
    root = _write_min_tree(tmp_path, extract)
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index["sources"][0]["corpus"]["tables"] = {
        "exists": True,
        "path": "tables/fixture-source",
    }
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")

    result = migrate(root, write=False)

    observation = next(iter(result.observations.values()))
    work = result.works[result.experiments[observation.experiment_id].work_id]
    assert table_asset in {asset.asset_id for asset in work.source_files.files}
    assert observation.derived_from == (table_asset,)
    assert observation.derivation is not None
    assert observation.derivation.inputs == (table_asset,)
    assert not any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and table_asset in issue.detail
        for issue in result.validation.hard_issues
    )


def test_registered_unknown_placeholder_resolves_as_lineage(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    row = extract["species"]["Na"]["observations"][0]
    placeholder_asset = "unknown:fixture-source"
    row["values"]["method_class"] = "measured_reduced"
    row["derived_from"] = [placeholder_asset]
    row["derivation"] = {
        "relation": "author_mass_balance_reduction",
        "inputs": [placeholder_asset],
        "output_unit": "Pa",
    }
    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    observation = next(iter(result.observations.values()))
    work = result.works[result.experiments[observation.experiment_id].work_id]
    assert placeholder_asset in {asset.asset_id for asset in work.source_files.files}
    assert observation.derived_from == (placeholder_asset,)
    assert observation.derivation is not None
    assert observation.derivation.inputs == (placeholder_asset,)
    lineage_paths = {
        f"observation[{observation.observation_id}].derived_from",
        f"observation[{observation.observation_id}].derivation.inputs",
    }
    assert not any(
        issue.path in lineage_paths
        and issue.reason
        in {RefusalReason.CONDITIONAL_FIELD, RefusalReason.REFERENTIAL_INTEGRITY}
        for issue in result.validation.hard_issues
    )


def test_measured_reduced_derivation_prose_input_is_queued_not_pointed(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    raw = extract["species"]["Na"]["observations"][0]
    raw["values"]["method_class"] = "measured_reduced"
    raw["values"]["derivation"] = {
        "relation": "calculated from measured quantities",
        "inputs": [
            "Table 2 DSC Cp points",
            "Table 3 drop-calorimetric heat contents",
        ],
        "output_unit": "atm",
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=True)
    observation = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith("fixture-source::na_psat::T=1200.0")
    )
    assert observation.derived_from is None
    queued = [
        entry
        for entry in result.queue
        if entry.observation_id == observation.observation_id
        and "Table 2 DSC Cp points" in (entry.why or "")
    ]
    assert len(queued) == 1
    assert queued[0].axes == ["derived_from"]
    assert queued[0].locator
    report = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert not any(
        issue.path.startswith(f"observation[{observation.observation_id}].")
        and issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        for issue in report.hard_issues
    )


def test_kume_measured_reduced_activity_without_derivation_stays_queued(
    tmp_path: Path,
) -> None:
    name = "kume-2000-cao-activities.yaml"
    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    extract["source_id"] = "fixture-source"
    observation_id = "kume_2000_table2_sample_101"
    for block in extract["species"].values():
        for row in block.get("observations", []):
            if row.get("observation_id") == observation_id:
                row.pop("derivation")

    root = _write_min_tree(tmp_path, extract)
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index["sources"][0]["corpus"]["tables"] = {
        "exists": True,
        "path": "tables/fixture-source",
    }
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    result = migrate(root, write=False)
    observation = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith(f"::{observation_id}")
    )
    assert observation.derivation is None
    derivation_queue = [
        entry
        for entry in result.queue
        if entry.observation_id == observation.observation_id
        and entry.axes == ["derivation"]
    ]
    assert len(derivation_queue) == 1
    assert derivation_queue[0].why == (
        "derived evidence class with unstated derivation; queued for page-grounding"
    )


def test_l05g1a_table_qualifier_leaves_reference_state_unknown(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="activity_vapor_reference_eq7",
        units="dimensionless",
        values={
            "quantity": "activity_vapor_reference_eq7",
            "activity": 0.12,
            "method_class": "measured_direct",
        },
        obs_type="activity_coefficient",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.ACTIVITY
    assert obs.value.kind is ValueKind.POINT
    assert obs.identity.reference_state is not None
    assert obs.identity.reference_state.is_unknown
    assert "vapor_reference_eq7" in (obs.identity.reference_state.reason or "")
    assert any("reference_state" in (e.axes or ()) for e in result.queue)


def test_l05g1a_does_not_cross_condensation_or_log_pressure() -> None:
    state, reason = map_quantity(
        None, {"quantity": "condensation_coefficient", "activity": 0.2}
    )
    assert not state.is_value
    state, reason = map_quantity(
        None,
        {"quantity": "log10_Psat_over_P0", "table_kJ_mol": 316.034, "activity": 1},
    )
    assert not state.is_value
    assert "unsupported" in (reason or "")


def test_l02_value_k_lifts_transition_temperature(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="K",
        values={
            "property_kind": "melting_point",
            "value_K": 370.96,
            "phase_from": "solid",
            "phase_to": "liquid",
            "pressure_basis": "near 1 atm",
            "method_class": "compilation_calculated_table",
        },
        obs_type="transition_point",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = "Na_melting_point"
    boil = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    boil["observation_id"] = "Na_normal_boiling_point"
    boil["values"] = {
        "property_kind": "normal_boiling_point",
        "value_K": 1156.0,
        "pressure_basis_Pa": 101325,
        "method_class": "compilation_calculated_table",
    }
    extract["species"]["Na"]["observations"].append(boil)
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    melt = result.observations["fixture-source::Na_melting_point"]
    assert quantity_token(melt.identity) is Quantity.TRANSITION_TEMPERATURE
    assert melt.value.kind is ValueKind.POINT
    assert melt.value.point == as_decimal("370.96")
    assert melt.identity.subtype.is_value and melt.identity.subtype.value == "melting_point"
    assert melt.identity.temperature_K is None or melt.identity.temperature_K.is_not_applicable
    assert melt.identity.total_pressure_Pa.is_unknown
    assert any(
        e.observation_id == melt.observation_id and "total_pressure_Pa" in (e.axes or ())
        for e in result.queue
    )
    nbp = result.observations["fixture-source::Na_normal_boiling_point"]
    assert nbp.value.kind is ValueKind.POINT
    assert nbp.value.point == as_decimal("1156.0")
    assert nbp.identity.subtype.value == "normal_boiling_point"
    assert nbp.identity.total_pressure_Pa.is_value
    assert nbp.identity.total_pressure_Pa.value == as_decimal("101325")


def test_gornerup_liquidus_rows_preserve_printed_temperature_and_composition(
    tmp_path: Path,
) -> None:
    filename = "gornerup-1996-cao-corner-liquidus.yaml"
    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts" / filename).read_text(
            encoding="utf-8"
        )
    )
    printed_row = source["species"]["CaO"]["observations"][0]
    printed_values = printed_row["values"]
    printed_composition = {
        key: as_decimal(value)
        for key, value in printed_values["composition_mass_percent"].items()
    }
    assert printed_values["property_kind"] == "liquidus"
    assert printed_values["points"][0]["T_C"] == 1600
    assert printed_composition == {
        "Al2O3": as_decimal("35.3"),
        "CaO": as_decimal("59.8"),
        "SiO2": as_decimal("4.9"),
    }

    result = _migrate_real_extract(
        tmp_path, filename, use_repository_index_row=True
    )
    liquidus_rows = [
        observation
        for observation in result.observations.values()
        if quantity_token(observation.identity) is Quantity.TRANSITION_TEMPERATURE
        and observation.identity.subtype.is_value
        and observation.identity.subtype.value == "liquidus"
    ]
    assert len(liquidus_rows) == 39
    assert result.validation is not None
    assert not result.validation.hard_issues

    observation = next(
        observation
        for observation in liquidus_rows
        if observation.observation_id.split("::")[-2]
        == "gornerup_1996_t1_exp_3_4"
    )
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == as_decimal("1873.15")
    assert observation.identity.composition is not None
    assert observation.identity.composition.is_value
    assert observation.identity.composition.value == wt_pct_to_mole_fraction(
        printed_composition
    )


def test_l05g0_rows_list_alone_does_not_name_delta_fg() -> None:
    state, reason = compilation_quantity_from_record(
        {"record_kind": "atomic_weight", "rows": [{"atomic_weight": {"value": 227}}]}
    )
    assert not state.is_value
    assert reason == "printed compilation columns are not mapped to a closed quantity"
    state, reason = compilation_quantity_from_record(
        {
            "table_kind": "high_temperature",
            "rows": [{"temperature": {"value": 1100}, "delta_f_G": {"value": -100.0}}],
        }
    )
    assert state.is_value and state.value is Quantity.DELTA_FG


def test_g1_formation_enthalpy_maps_to_delta_fh_quantity() -> None:
    """Printed ATcT / NASA-Glenn formation enthalpy is Quantity.DELTA_FH."""
    atct = {
        "record_id": "atct-fixture",
        "formula": "H2",
        "phase": "g",
        "formation_enthalpy_298_15_K_as_published": "0",
        "units_as_published": "kJ/mol",
    }
    state, reason = compilation_quantity_from_record(atct)
    assert state.is_value and state.value is Quantity.DELTA_FH and reason is None
    sel = select_declared_source(state, None, atct)
    assert sel.amount == as_decimal("0")
    assert sel.value.kind is ValueKind.POINT
    assert sel.field_name == "formation_enthalpy_298_15_K_as_published"
    named = select_declared_source(
        Quantity.DELTA_FH,
        None,
        {"delta_h": "94", "units": {"delta_h": "kcal/mol"}},
    )
    assert named.value.kind is ValueKind.POINT
    assert named.amount == as_decimal("393.296")
    assert named.unit_trail == "thermochemical_kcal_to_kJ_exact"

    nasa = {
        "record_id": "NG-fixture",
        "formula": "CO2",
        "phase": "gas",
        "delta_f_H_298_15": {
            "as_published": "-393510.000",
            "value": -393510.0,
            "units_as_published": "J/mol",
        },
        "intervals": [{"range_as_published": "200 1000"}],
    }
    state, reason = compilation_quantity_from_record(nasa)
    assert state.is_value and state.value is Quantity.DELTA_FH and reason is None
    sel = select_declared_source(state, None, nasa)
    assert sel.amount == as_decimal("-393.51")
    assert sel.value.kind is ValueKind.POINT
    assert sel.field_name == "delta_f_H_298_15"


def test_g1_delta_fh_migrate_refuses_blank_units_and_admits_declared_point(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "atct", "atct-1.222-0001.json")
    _copy_compilation_record(root, "nasa-glenn", "NG-0467.json")
    result = migrate(root, write=False)

    atct_obs = [
        obs
        for oid, obs in result.observations.items()
        if "atct-1.222-0001" in oid
    ]
    assert atct_obs
    assert all(quantity_token(obs.identity) is Quantity.DELTA_FH for obs in atct_obs)
    assert all(obs.value.kind is ValueKind.UNAVAILABLE for obs in atct_obs)
    assert any(
        "missing printed unit" in (entry.why or "")
        for entry in result.queue
        if "atct-1.222-0001" in (entry.observation_id or "")
    )

    ng_obs = [
        obs
        for oid, obs in result.observations.items()
        if "NG-0467" in oid
    ]
    assert ng_obs
    assert all(quantity_token(obs.identity) is Quantity.DELTA_FH for obs in ng_obs)
    assert all(obs.value.kind is ValueKind.POINT for obs in ng_obs)
    assert all(obs.value.point == as_decimal("-103.772885") for obs in ng_obs)
    assert all(
        obs.derivation is not None
        and obs.derivation.relation == "J_to_kJ_exact"
        for obs in ng_obs
    )

    for entry in result.queue:
        assert "not a v2.1 Quantity token" not in (entry.why or "")


def test_g1_scalar_units_guard_mutation_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulator.battery import migrate as migrate_mod

    payload = {
        "formation_enthalpy_298_15_K_as_published": "12.5",
        "units_as_published": "kJ/mol",
    }
    live = select_declared_source(Quantity.DELTA_FH, None, payload)
    assert live.value.kind is ValueKind.POINT
    monkeypatch.setattr(
        migrate_mod,
        "_printed_unit_for_field",
        lambda _payload, _key: None,
    )
    mutant = select_declared_source(Quantity.DELTA_FH, None, payload)
    assert mutant.value.kind is ValueKind.UNAVAILABLE


def test_g1_delta_h_named_field_mutation_proof() -> None:
    from simulator.battery import migrate as migrate_mod

    payload = {"delta_h": "94", "units": {"delta_h": "kcal/mol"}}
    live = select_declared_source(Quantity.DELTA_FH, None, payload)
    assert live.amount == as_decimal("393.296")

    saved = migrate_mod.QUANTITY_SOURCE_FIELDS[Quantity.DELTA_FH]
    migrate_mod.QUANTITY_SOURCE_FIELDS[Quantity.DELTA_FH] = tuple(
        key for key in saved if key != "delta_h"
    )
    try:
        mutant = select_declared_source(Quantity.DELTA_FH, None, payload)
    finally:
        migrate_mod.QUANTITY_SOURCE_FIELDS[Quantity.DELTA_FH] = saved
    assert mutant.value.kind is ValueKind.UNAVAILABLE
    assert "no mapped delta_fH field" in (mutant.value.unavailable_reason or "")


def test_g1_delta_fh_mapping_mutation_proof() -> None:
    """Removing formation-enthalpy cell maps restores the pre-fix unknown reason."""
    from simulator.battery import migrate as migrate_mod

    doc = {
        "record_id": "atct-mut",
        "formation_enthalpy_298_15_K_as_published": "12.5",
    }
    live, live_reason = compilation_quantity_from_record(doc)
    assert live.is_value and live.value is Quantity.DELTA_FH and live_reason is None

    saved = dict(migrate_mod._COMPILATION_CELL_QUANTITY)
    try:
        for key in (
            "formation_enthalpy_298_15_K_as_published",
            "delta_f_H_298_15",
            "delta_f_H",
            "delta_fH",
            "deltafH",
            "formation_enthalpy",
        ):
            migrate_mod._COMPILATION_CELL_QUANTITY.pop(key, None)
        mutant, mutant_reason = compilation_quantity_from_record(doc)
        assert not mutant.is_value
        assert mutant_reason == "printed compilation columns are not mapped to a closed quantity"
    finally:
        migrate_mod._COMPILATION_CELL_QUANTITY.clear()
        migrate_mod._COMPILATION_CELL_QUANTITY.update(saved)

    restored, restored_reason = compilation_quantity_from_record(doc)
    assert restored.is_value and restored.value is Quantity.DELTA_FH and restored_reason is None


def test_registry_extracts_migrate_and_load_typed_observations(tmp_path: Path) -> None:
    names = (
        "jaggi-2021-mercury-atmosphere.yaml",
        "ta-shirai-2000-lpsc.yaml",
        "thomas-wood-2021-chlorine-silicate-melts.yaml",
        "ueshima-1982-fe-mo-thermal.yaml",
        "ta-mendybaev-2002-lpsc.yaml",
        "ta-mendybaev-2020-lpsc.yaml",
        "lpsc-2024-bennu-pyrolysis-vandam.yaml",
    )
    root = _write_min_tree(tmp_path)
    for name in names:
        _copy_extract(root, name)

    result = migrate(root, write=True)
    assert not result.registry_issues
    for name in names:
        source_id = name.removesuffix(".yaml")
        assert any(obs.source_id == source_id for obs in result.observations.values())

    experiments = result.experiments
    shirai_work_id = "c31f435ac3571abcf67819bf20c1353e15212bf08549e6c62bc389b6c5667bbf"
    canonical_experiment_ids = {
        f"{shirai_work_id}::experiment::{experiment_id}"
        for experiment_id in (
            "shirai-fig1a-1300-pO2-1e-8",
            "shirai-fig1a-1300-pO2-1e-9",
            "shirai-fig1a-1300-pO2-1e-10",
            "shirai-fig1b-1400-pO2-1e-8",
            "shirai-fig1b-1400-pO2-1e-9",
            "shirai-fig1b-1400-pO2-1e-10",
            "shirai-flow-check-1400-pO2-1e-9",
        )
    }
    canonical_experiment_ids.update(
        {
            "10.1016/j.gca.2020.11.018::experiment::table2_anhydrous_chlorine_series",
            "10.2355/tetsutohagane1955.68.16_2569::experiment::femo-kems-series",
            "10.2355/tetsutohagane1955.68.16_2569::experiment::pure-metal-calibration-series",
            "902510908415be31627b6455b1c2e313e9b13235dd467f9309dc5d4f7cc8425d::experiment::b133-vacuum-1800C-loop-series",
            "7ec3fe9fa2b6061b03cb4605140886a7244d4b260743cae7b2f7fc6336d49fe0::experiment::sio2-langmuir-ir-loop-1800C",
            "8b79bdb9022f5df4da0a30d1c4bc731d1e2bf5895fbebf188091571818833877::experiment::standard-pyrolysis-600C",
        }
    )
    assert canonical_experiment_ids <= set(experiments)

    shirai = experiments[
        f"{shirai_work_id}::experiment::shirai-fig1a-1300-pO2-1e-8"
    ]
    assert shirai.fO2_control is not None
    assert shirai.fO2_control.channel.is_value
    assert shirai.fO2_control.channel.value is FO2Channel.GAS_MIX

    ueshima = experiments[
        "10.2355/tetsutohagane1955.68.16_2569::experiment::femo-kems-series"
    ]
    assert ueshima.thermal_schedule is not None
    assert ueshima.thermal_schedule.setpoints_and_holds
    temperature = ueshima.thermal_schedule.setpoints_and_holds[0].temperature_K
    assert temperature.state.is_value
    assert temperature.state.value.kind is ValueKind.INTERVAL

    # Table 2 prints no operating pressure. The extract records that as
    # point_conditions.total_pressure_Pa unknown/not_published, and the
    # identity must carry that state rather than a contradictory absence claim.
    standards = (
        "ueshima-1982-fe-mo-thermal::ueshima_1982_table2_fe_a4_standard",
        "ueshima-1982-fe-mo-thermal::ueshima_1982_table2_fe_melting_standard",
        "ueshima-1982-fe-mo-thermal::ueshima_1982_table2_pd_melting_standard",
    )
    for oid in standards:
        pressure = result.observations[oid].identity.total_pressure_Pa
        assert pressure is not None and pressure.is_unknown
        assert pressure.reason == "not_published"
        assert pressure.value is None
        assert not any(
            entry.observation_id == oid
            and entry.why == "source does not state a numeric total_pressure_Pa"
            for entry in result.queue
        )
    table4 = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith(
            "ueshima-1982-fe-mo-thermal::ueshima_1982_table4_thermal_analysis"
        )
    ]
    assert table4
    assert all(
        obs.identity.total_pressure_Pa is not None
        and obs.identity.total_pressure_Pa.is_unknown
        and obs.identity.total_pressure_Pa.reason
        == "source does not state a numeric total_pressure_Pa"
        for obs in table4
    )

    works, loaded_experiments, loaded_observations = load_migrated_store(root)
    assert works and loaded_experiments and loaded_observations
    assert any(
        obs.source_id == "ueshima-1982-fe-mo-thermal"
        for obs in loaded_observations.values()
    )
    for oid in standards:
        loaded = loaded_observations[oid].identity.total_pressure_Pa
        assert loaded is not None and loaded.is_unknown
        assert loaded.reason == "not_published"
    assert (
        "10.2355/tetsutohagane1955.68.16_2569::experiment::femo-kems-series"
        in loaded_experiments
    )
    assert (
        "10.2355/tetsutohagane1955.68.16_2569::experiment::pure-metal-calibration-series"
        in loaded_experiments
    )


def test_registry_tagged_condition_mutation_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulator.battery import migrate as migrate_mod
    from simulator.battery.migrate import experiment_from_plain

    raw = yaml.safe_load(
        (
            REPO_ROOT
            / "data/literature/extracts/ueshima-1982-fe-mo-thermal.yaml"
        ).read_text(encoding="utf-8")
    )["experiments"][0]
    raw = dict(raw)
    raw["conditions"] = {
        "temperature_K": raw["thermal_schedule"]["setpoints_and_holds"][0][
            "temperature_K"
        ]
    }
    raw["method"] = "knudsen_effusion"
    live = experiment_from_plain(raw)
    assert live.conditions["temperature_K"].state.value.kind is ValueKind.INTERVAL

    monkeypatch.setattr(migrate_mod, "_condition_value_from_plain", as_decimal)
    mutant = experiment_from_plain(raw)
    assert mutant.conditions["temperature_K"].state.is_unknown


def test_heck_inferred_pressure_survives_registry_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery import migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    _copy_extract(root, "kems-140-heck-2025.yaml")
    result = migrate(root, write=True)
    experiment = next(
        exp
        for eid, exp in result.experiments.items()
        if eid.endswith("::experiment::open-furnace-mvce-degassing-series")
    )
    pressure = experiment.pressure_environment.total_pressure_Pa
    assert pressure.inference is not None
    assert pressure.inference.relation == "extract_inference"
    assert "inferred=true" in pressure.inference.inputs
    assert any("not printed" in item for item in pressure.inference.inputs)

    saved = migrate_mod._inferred_derivation_from_plain
    monkeypatch.setattr(
        migrate_mod,
        "_inferred_derivation_from_plain",
        lambda _payload: None,
    )
    mutated = migrate_mod._pressure_env_from_plain(
        yaml.safe_load(
            """
            total_pressure_Pa:
              state: {tag: value, value: {kind: point, point: '101325'}}
              inferred: true
            sweep_gas: {state: {tag: not_applicable, reason: not_published}}
            regime:
              regime_class: {tag: unknown, reason: not_published}
            """
        )
    )
    monkeypatch.setattr(migrate_mod, "_inferred_derivation_from_plain", saved)
    assert mutated.total_pressure_Pa.inference is None


def test_g5_compilation_column_series_maps_pankratz_and_kelley() -> None:
    """Multi-column Cp/S/H/ΔHf/ΔGf censuses explode to one series per Quantity."""
    pankratz = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1447.json"
        ).read_text(encoding="utf-8")
    )
    series = compilation_column_series_from_record(pankratz)
    quantities = [item.quantity for item in series]
    assert quantities == [
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    ]
    assert all(item.value.kind is ValueKind.SERIES for item in series)
    assert all(len(item.value.series or ()) == 4 for item in series)
    # Never first-numeric-cell: T column stays out of the emitted quantities.
    assert Quantity.TRANSITION_TEMPERATURE not in quantities
    cp = next(item for item in series if item.quantity is Quantity.CP)
    assert cp.value.series[0] == (as_decimal("298"), as_decimal("317.984"))

    kelley = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0007.json"
        ).read_text(encoding="utf-8")
    )
    k_series = compilation_column_series_from_record(kelley)
    k_by_key = {item.series_key: item for item in k_series}
    assert "cp_temperature_grid" in k_by_key
    assert k_by_key["cp_temperature_grid"].quantity is Quantity.CP
    assert k_by_key["cp_temperature_grid"].value.kind is ValueKind.SERIES
    assert len(k_by_key["cp_temperature_grid"].value.series or ()) == 7
    assert k_by_key["entropy_third_law"].quantity is Quantity.S
    assert k_by_key["entropy_third_law"].value.kind is ValueKind.POINT
    assert k_by_key["entropy_third_law"].value.point == as_decimal("111.21072")
    assert k_by_key["entropy_recommended"].value.point == as_decimal("111.21072")


def test_pankratz_source_units_convert_and_missing_units_refuse() -> None:
    page = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1987-usbm-b689/records/page-0003.json"
        ).read_text(encoding="utf-8")
    )
    by_quantity = {
        item.quantity: item.value.series[0][1]
        for item in compilation_column_series_from_record(page)
    }
    assert by_quantity[Quantity.CP] == as_decimal("35.9824")
    assert by_quantity[Quantity.DELTA_FH] == as_decimal("393.296")
    trails = {
        item.quantity: item.unit_trail
        for item in compilation_column_series_from_record(page)
    }
    assert trails[Quantity.CP] == "thermochemical_calorie_to_J_exact"
    assert trails[Quantity.DELTA_FH] == "thermochemical_kcal_to_kJ_exact"

    for record in ("table-0001.json", "table-0002.json"):
        doc = json.loads(
            (
                REPO_ROOT
                / "data/literature/compilations/pankratz-1984-usbm-b677/records"
                / record
            ).read_text(encoding="utf-8")
        )
        assert compilation_column_series_from_record(doc) == ()

    selection = select_declared_source(
        Quantity.CP, None, {"cp": 25}
    )
    assert selection.value.kind is ValueKind.UNAVAILABLE
    assert selection.value.unavailable_reason == (
        "missing printed unit for cp; refusing ungrounded numeric value"
    )


def test_g5_compilation_conversion_trail_mutation_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery import migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "pankratz-1987-usbm-b689", "page-0003.json")
    live = migrate(root, write=False)
    live_cp = next(
        obs for oid, obs in live.observations.items() if oid.endswith(":page-0003:cp")
    )
    assert live_cp.derivation is not None
    assert live_cp.derivation.relation == "thermochemical_calorie_to_J_exact"

    saved = dict(migrate_mod._CONVERSION_META)
    try:
        for trail in (
            "thermochemical_calorie_to_J_exact",
            "thermochemical_kcal_to_kJ_exact",
            "J_to_kJ_exact",
        ):
            migrate_mod._CONVERSION_META.pop(trail, None)
        mutant = migrate(root, write=False)
    finally:
        migrate_mod._CONVERSION_META.clear()
        migrate_mod._CONVERSION_META.update(saved)
    mutant_cp = next(
        obs
        for oid, obs in mutant.observations.items()
        if oid.endswith(":page-0003:cp")
    )
    assert mutant_cp.derivation is None


def test_joule_heat_capacity_unit_is_identity() -> None:
    selection = select_declared_source(Quantity.CP, "J/mol/K", {"cp": 25})
    assert selection.value.kind is ValueKind.POINT
    assert selection.value.point == as_decimal("25")


def test_g5_compilation_column_explode_migrate(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(root, "pankratz-1984-usbm-b677", "table-1447.json")
    _copy_compilation_record(root, "pankratz-1987-usbm-b689", "page-0003.json")
    _copy_compilation_record(root, "kelley-king-1961-usbm-b592", "table-006-0007.json")
    _copy_compilation_record(root, "kelley-king-1961-usbm-b592", "table-006-0923.json")
    result = migrate(root, write=False)

    pank = [
        obs
        for oid, obs in result.observations.items()
        if "table-1447" in oid
    ]
    pank_q = {quantity_token(obs.identity) for obs in pank}
    assert pank_q == {
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    }
    assert all(obs.value.kind is ValueKind.SERIES for obs in pank)
    # Identity assertion: ΔHf series is not the first numeric column (T or Cp).
    dh = next(obs for obs in pank if quantity_token(obs.identity) is Quantity.DELTA_FH)
    assert dh.value.series is not None
    # table-1447 row0 ΔHf is column index 4, not the first numeric cell (T=298 or Cp=76).
    assert dh.value.series[0] == (as_decimal("298"), as_decimal("-2895.516280"))

    kel = [
        obs
        for oid, obs in result.observations.items()
        if "table-006-0007" in oid
    ]
    kel_q = {quantity_token(obs.identity) for obs in kel}
    assert Quantity.CP in kel_q and Quantity.S in kel_q
    assert any(
        obs.value.kind is ValueKind.SERIES and quantity_token(obs.identity) is Quantity.CP
        for obs in kel
    )
    assert sum(1 for obs in kel if quantity_token(obs.identity) is Quantity.S) == 2

    kel19 = [
        obs
        for oid, obs in result.observations.items()
        if "table-006-0923" in oid
    ]
    assert any(quantity_token(obs.identity) is Quantity.CP for obs in kel19)
    assert any(
        quantity_token(obs.identity) is Quantity.S and "entropy_spectrographic" in oid
        for oid, obs in result.observations.items()
        if "table-006-0923" in oid
    )

    for entry in result.queue:
        why = entry.why or ""
        if "table-1447" in (entry.observation_id or "") or "table-006-0007" in (
            entry.observation_id or ""
        ):
            assert "source column census" not in why
            assert "printed compilation columns are not mapped" not in why
    assert any(
        "gibbs_function" in (entry.why or "")
        for entry in result.queue
        if "page-0003" in (entry.observation_id or "")
    )


def test_g5_compilation_column_mapping_mutation_proof() -> None:
    """Clearing label maps restores the pre-fix empty explode / census path."""
    from simulator.battery import migrate as migrate_mod

    doc = json.loads(
        (
            REPO_ROOT
            / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1447.json"
        ).read_text(encoding="utf-8")
    )
    live = compilation_column_series_from_record(doc)
    assert len(live) == 5
    assert {item.quantity for item in live} == {
        Quantity.CP,
        Quantity.S,
        Quantity.H_MINUS_H298,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
    }

    saved_heading = dict(migrate_mod._COMPILATION_HEADING_QUANTITY)
    saved_cell = dict(migrate_mod._COMPILATION_CELL_QUANTITY)
    try:
        migrate_mod._COMPILATION_HEADING_QUANTITY.clear()
        # Keep non-G5 cell keys so unrelated paths stay intact; drop multi-column keys.
        for key in list(migrate_mod._COMPILATION_CELL_QUANTITY):
            if key in {
                "cp",
                "heat_capacity",
                "entropy",
                "enthalpy_increment",
                "delta_h",
                "delta_g",
                "log_k",
                "cp_10_k",
                "cp_25_k",
                "cp_50_k",
                "cp_100_k",
                "cp_150_k",
                "cp_200_k",
                "cp_298_15_k",
                "entropy_third_law",
                "entropy_spectrographic_or_molecular_constants",
                "entropy_other_sources",
                "entropy_recommended",
                "delta_f_H",
                "delta_fH",
                "deltafH",
                "formation_enthalpy",
            }:
                migrate_mod._COMPILATION_CELL_QUANTITY.pop(key, None)
        mutant = compilation_column_series_from_record(doc)
        assert mutant == ()
        # First-numeric-cell mutant would invent a quantity from T/Cp; we require empty.
        assert not any(item.quantity is Quantity.CP for item in mutant)
    finally:
        migrate_mod._COMPILATION_HEADING_QUANTITY.clear()
        migrate_mod._COMPILATION_HEADING_QUANTITY.update(saved_heading)
        migrate_mod._COMPILATION_CELL_QUANTITY.clear()
        migrate_mod._COMPILATION_CELL_QUANTITY.update(saved_cell)

    restored = compilation_column_series_from_record(doc)
    assert len(restored) == 5




def test_l05c1_costa_owner_fe_control_is_not_condensation() -> None:
    row = _extract_observation(
        "kems-007-costa-2015.yaml",
        "costa_2015_fe_vaporization_coefficient_alpha_digitized",
    )
    values = row.get("values") if isinstance(row.get("values"), dict) else {}
    state, reason = map_quantity(
        row.get("type"), values, units=row.get("units"), row=row
    )
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert reason is None
    assert [point["alpha"] for point in values["points"]] == [
        0.016269, 0.015517, 0.01978, 0.011505, 0.013045
    ]


def test_l03_per_mol_o2_ledger_lifts_delta_fg(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "comparison_quantity": "delta_fG_kJ_mol",
                "points": [
                    {
                        "key": "janaf::Al-096:T=1100::ellingham::delta_fG_kJ_per_mol_O2",
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "delta_fG_kJ_per_mol_O2",
                        "temperature_K": 1100,
                        "table_kJ_mol": -885.524,
                        "note": "rescaled 2*dfG/n_O with n_O=3.0 via OXIDE_TO_METAL['Al2O3'] -> Al",
                        "provenance_class": "independent_tabulation",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations[
        "janaf::Al-096:T=1100::ellingham::delta_fG_kJ_per_mol_O2"
    ]
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert obs.identity.per.is_value
    from simulator.battery.enums import PerBasis

    assert obs.identity.per.value is PerBasis.MOL_O2
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("-885.524")
    assert obs.derivation is not None
    assert "rescaled" in obs.derivation.relation
    assert obs.evidence.class_.is_value
    from simulator.battery.enums import EvidenceClass

    assert obs.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED


def test_l04_log10_psat_over_p0_is_not_a_pressure(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::Al-005:T=100::log10_Psat_over_P0",
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 100,
                        "table_kJ_mol": 316.034,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["janaf::Al-005:T=100::log10_Psat_over_P0"]
    assert quantity_token(obs.identity) is not Quantity.P_SAT
    assert quantity_token(obs.identity) is not Quantity.P_PARTIAL
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert any(reason == (e.why or "") for e in result.queue if e.observation_id == obs.observation_id)




def test_g3_vaporization_note_lifts_reaction_and_delta_fg() -> None:
    """Printed dfG(g)-dfG(cr|l) note lifts reaction identity onto delta_fG."""
    note_cr = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(cr)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-002"
    )
    lifted = lift_vaporization_reaction_from_ledger_note("Al", note_cr)
    assert lifted is not None
    reaction, p0 = lifted
    assert p0 == as_decimal("100000")
    assert isinstance(reaction, Reaction)
    phases = {term.species.phase.value for term in reaction.terms}
    assert phases == {Phase.G, Phase.CR}
    coeffs = {
        term.species.phase.value: term.coefficient for term in reaction.terms
    }
    assert coeffs[Phase.G] == 1
    assert coeffs[Phase.CR] == -1

    note_l = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(l)]/(R T); P0=100000 Pa"
    )
    lifted_l = lift_vaporization_reaction_from_ledger_note("Al", note_l)
    assert lifted_l is not None
    phases_l = {term.species.phase.value for term in lifted_l[0].terms}
    assert phases_l == {Phase.G, Phase.L}

    assert lift_vaporization_reaction_from_ledger_note("Al", "janaf p°=0.1 MPa") is None
    assert lift_vaporization_reaction_from_ledger_note("Al", "") is None


def test_g3_vaporization_ledger_admits_delta_fg_with_reaction(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    note = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(cr)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-002; "
        "a_melt=1; mechanism=compilation_disagreement"
    )
    oid = "janaf::Al-005:T=100::vapour_rail_psat::log10_Psat_over_P0"
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": oid,
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 100,
                        "table_kJ_mol": 316.034,
                        "note": note,
                        "provenance_class": "independent_tabulation",
                        "engine_channel": "vapour_rail_psat",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations[oid]
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert quantity_token(obs.identity) is not Quantity.P_SAT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("316.034")
    assert obs.identity.species.phase.is_value
    assert obs.identity.species.phase.value is Phase.G
    assert obs.identity.reaction is not None and obs.identity.reaction.is_value
    reaction = obs.identity.reaction.value
    assert reaction is not None
    phases = {term.species.phase.value for term in reaction.terms}
    assert phases == {Phase.G, Phase.CR}
    assert obs.identity.per is not None and obs.identity.per.is_value
    assert obs.identity.per.value is PerBasis.MOL_SPECIES
    assert obs.identity.standard_pressure_Pa is not None
    assert obs.identity.standard_pressure_Pa.is_value
    assert obs.identity.standard_pressure_Pa.value == as_decimal("100000")
    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    assert not any(
        reason == (e.why or "")
        for e in result.queue
        if e.observation_id == obs.observation_id
    )
    assert not any(
        set(e.axes or ()) >= {"quantity", "value"}
        and reason in (e.why or "")
        for e in result.queue
        if e.observation_id == obs.observation_id
    )


def test_g3_vaporization_lift_mutation_proof(tmp_path: Path) -> None:
    """Stripping the reaction lift restores the paired quantity/value refusal."""
    import simulator.battery.migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    note = (
        "janaf p°=0.1 MPa; ln(P_sat/P0)=-[dfG(g)-dfG(l)]/(R T); "
        "P0=100000 Pa; rail_id=Al; condensed_record=Al-003"
    )
    oid = "janaf::Al-005:T=1200::vapour_rail_psat::log10_Psat_over_P0"
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": oid,
                        "source_id": "fixture-source",
                        "species": "Al",
                        "comparison_quantity": "log10_Psat_over_P0",
                        "temperature_K": 1200,
                        "table_kJ_mol": 250.0,
                        "note": note,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    live = migrate(root, write=False)
    live_obs = live.observations[oid]
    assert quantity_token(live_obs.identity) is Quantity.DELTA_FG
    assert live_obs.value.kind is ValueKind.POINT

    reason = (
        "the table value is the Gibbs energy of the vaporization "
        "reaction and the reaction identity is not lifted"
    )
    saved = migrate_mod.lift_vaporization_reaction_from_ledger_note
    try:
        migrate_mod.lift_vaporization_reaction_from_ledger_note = (
            lambda formula, note: None  # type: ignore[assignment]
        )
        mutant = migrate(root, write=False)
    finally:
        migrate_mod.lift_vaporization_reaction_from_ledger_note = saved

    mutant_obs = mutant.observations[oid]
    assert mutant_obs.identity.quantity.is_unknown
    assert quantity_token(mutant_obs.identity) is not Quantity.DELTA_FG
    assert mutant_obs.value.kind is ValueKind.UNAVAILABLE
    assert any(
        reason == (e.why or "")
        for e in mutant.queue
        if e.observation_id == mutant_obs.observation_id
    )

    restored = migrate(root, write=False)
    restored_obs = restored.observations[oid]
    assert quantity_token(restored_obs.identity) is Quantity.DELTA_FG
    assert restored_obs.value.kind is ValueKind.POINT



def test_l05c3_tm_k_and_delta_f_g_298_and_table_log10_kf(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="",
        units="K and kK as published",
        values={
            "property_kind": "melting_point_and_enthalpy_of_fusion",
            "T_m_K": 1405.0,
            "T_m_uncertainty_K": 100.0,
            "T_range_K": [1405.0, 1405.0],
            "method_class": "compilation_calculated_table",
        },
        obs_type="transition_point",
    )
    extract["species"]["Na"]["observations"][0]["observation_id"] = "LH84_Na2O_fusion"
    se = yaml.safe_load(yaml.safe_dump(extract["species"]["Na"]["observations"][0]))
    se["observation_id"] = "NEA05_Se2_g"
    se["type"] = "gibbs_table"
    se["units"] = "kJ/mol"
    se["values"] = {
        "quantity": "delta_fG",
        "Delta_f_G_298_kJ_mol": 92.4,
        "method_class": "compilation_calculated_table",
    }
    extract["species"]["Na"]["observations"].append(se)
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::B-133:T=300::table_self_check::log10_Kf",
                        "source_id": "fixture-source",
                        "species": "B",
                        "comparison_quantity": "log10_Kf",
                        "temperature_K": 300,
                        "table_kJ_mol": -5516.922,
                        "table_log10_Kf": 966.926,
                        "provenance_class": "engine_own_input",
                        "status": "mismatch",
                        "finding_class": "compilation_table_self_check",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    fusion = result.observations["fixture-source::LH84_Na2O_fusion"]
    assert quantity_token(fusion.identity) is Quantity.TRANSITION_TEMPERATURE
    assert fusion.value.kind is ValueKind.POINT
    assert fusion.value.point == as_decimal("1405.0")
    assert fusion.identity.subtype.value == "melting_point_and_enthalpy_of_fusion"
    assert fusion.identity.temperature_K is None or fusion.identity.temperature_K.is_not_applicable
    assert fusion.identity.total_pressure_Pa.is_unknown
    assert fusion.uncertainty.kind.value == "printed"
    assert "100" in str(fusion.uncertainty.verbatim)
    se2 = result.observations["fixture-source::NEA05_Se2_g"]
    assert quantity_token(se2.identity) is Quantity.DELTA_FG
    assert se2.value.kind is ValueKind.POINT
    assert se2.value.point == as_decimal("92.4")
    assert se2.identity.temperature_K.is_value
    assert se2.identity.temperature_K.value == as_decimal("298.15")
    logk = result.observations["janaf::B-133:T=300::table_self_check::log10_Kf"]
    assert quantity_token(logk.identity) is Quantity.LOG10_KF
    assert logk.value.kind is ValueKind.POINT
    assert logk.value.point == as_decimal("966.926")
    assert logk.value.point != as_decimal("-5516.922")
    assert logk.notices
    assert "compilation_table_self_check" in logk.notices[0].reason


def test_l05g2b_langmuir_range_is_in_temperature_reason(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "langmuir_knudsen_flux_validation.yaml").write_text(
        yaml.safe_dump(
            {
                "measurements": {
                    "iron_olivine_kems": {
                        "species": "Fe",
                        "temperature_range_k": [1700, 1800],
                        "measured_langmuir_to_effusion_flux_ratio": {
                            "range": [0.011, 0.02]
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    obs = result.observations["iron_olivine_kems"]
    reason = getattr(obs.identity.temperature_K, "reason", "") or ""
    assert "1700" in reason and "1800" in reason


def test_l05g3_expected_value_is_not_a_generic_closed_quantity_field(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="delta_fG",
        units="kJ_per_mol",
        values={
            "quantity": "delta_fG",
            "expected_value": -100.0,
            "method_class": "measured_direct",
        },
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(iter(result.observations.values()))
    assert quantity_token(obs.identity) is Quantity.DELTA_FG
    assert obs.value.kind is ValueKind.UNAVAILABLE


def test_l05g0_store_atomic_weight_is_not_delta_fg() -> None:
    path = (
        REPO_ROOT
        / "data"
        / "literature"
        / "observations-v2"
        / "compilations-robie-hemingway-1995-usgs-b2131.yaml"
    )
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    stored = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    rows = [
        o
        for o in stored.get("observations") or []
        if str(o.get("observation_id") or "").endswith("atomic-weight-001")
    ]
    assert rows
    q = (rows[0].get("identity") or {}).get("quantity") or {}
    assert q.get("tag") == "unknown"
    assert q.get("value") != "delta_fG"


def test_observation_store_reader_unions_file_and_shard_directory(tmp_path: Path) -> None:
    obs_dir = tmp_path / "observations-v2"
    obs_dir.mkdir()
    (obs_dir / "compilations-janaf.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    shard_dir = obs_dir / "compilations-janaf"
    shard_dir.mkdir()
    (shard_dir / "janaf-Al.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    (obs_dir / "compilations-janaf-reports").mkdir()
    (obs_dir / "compilations-janaf-reports" / "janaf-Al.yaml").write_text("audit: true\n", encoding="utf-8")
    (obs_dir / "kems_measurements.yaml").write_text("schema_version: battery_observations.v2.1\n", encoding="utf-8")
    names = {path.name for path in iter_observation_store_paths(obs_dir)}
    assert names == {"compilations-janaf.yaml", "janaf-Al.yaml", "kems_measurements.yaml"}
    janaf = iter_observation_store_paths(obs_dir, "compilations-janaf.yaml")
    assert {path.name for path in janaf} == {"compilations-janaf.yaml", "janaf-Al.yaml"}
    families = {compilation_family_from_store_path(path) for path in janaf}
    assert families == {"janaf"}
    assert compilation_family_from_store_path(obs_dir / "kems_measurements.yaml") is None
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "literature_index", REPO_ROOT / "data" / "literature" / "build_index.py"
    )
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    assert builder.iter_observation_store_paths(obs_dir) == iter_observation_store_paths(obs_dir)
    assert builder.compilation_family_from_store_path(janaf[0]) == compilation_family_from_store_path(
        janaf[0]
    )


_OBS_V2 = REPO_ROOT / "data" / "literature" / "observations-v2"
_YAML_LOADER = YAML12SafeLoader


def _account_compilation_shard(path: Path, summary: dict[str, dict]) -> None:
    rel = path.relative_to(_OBS_V2).as_posix()
    cached = summary.get(rel)
    assert cached is not None, f"observation_store_summary missing {rel}"
    assert cached["size"] == path.stat().st_size, rel


def _load_store_if_needle(path: Path, needle: bytes, summary: dict[str, dict]) -> dict | None:
    """YAML-load a store file only when the needle is present.

    Compilation shards without the needle are not parsed; the derived summary
    accounts for them (size-matched). Same observations are compared when the
    needle is present.
    """
    raw = path.read_bytes()
    family = compilation_family_from_store_path(path)
    if family is not None:
        _account_compilation_shard(path, summary)
        if needle not in raw:
            return None
    elif needle not in raw:
        return None
    stored = yaml.load(raw.decode("utf-8"), Loader=_YAML_LOADER)
    return stored if isinstance(stored, dict) else {}


def test_l04_store_never_lifts_log10_psat_as_pressure() -> None:
    obs_dir = _OBS_V2
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    bad = []
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"log10_Psat_over_P0"
    for path in iter_observation_store_paths(obs_dir):
        stored = _load_store_if_needle(path, needle, summary)
        if stored is None:
            continue
        for obs in stored.get("observations") or []:
            oid = str(obs.get("observation_id") or "")
            if "log10_Psat_over_P0" not in oid:
                continue
            q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
            if q in {"p_sat", "p_partial"}:
                bad.append(f"{oid} stored {q}")
    assert not bad, bad[:10]


def test_l05c1_store_alpha_values_lie_in_unit_interval() -> None:
    roots = [
        REPO_ROOT / "data" / "literature" / "extracts-v2",
        REPO_ROOT / "data" / "literature" / "observations-v2",
    ]
    if not roots[0].is_dir():
        pytest.skip("migrated store not generated yet")
    bad = []
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"evaporation_coefficient_alpha"
    for folder in roots:
        for path in iter_observation_store_paths(folder):
            stored = _load_store_if_needle(path, needle, summary)
            if stored is None:
                continue
            for obs in stored.get("observations") or []:
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
                if q != "evaporation_coefficient_alpha":
                    continue
                val = obs.get("value") or {}
                if val.get("kind") != "point":
                    continue
                amount = as_decimal(val.get("point"))
                if not (as_decimal("0") < amount <= as_decimal("1")):
                    bad.append(f"{obs.get('observation_id')} alpha={amount}")
    assert not bad, bad[:20]


def test_l05c5_store_unavailable_values_are_queued() -> None:
    queue_path = REPO_ROOT / "data" / "battery" / "migration-queue.yaml"
    roots = [
        REPO_ROOT / "data" / "literature" / "extracts-v2",
        REPO_ROOT / "data" / "literature" / "observations-v2",
    ]
    if not queue_path.is_file() or not roots[0].is_dir():
        pytest.skip("migrated store not generated yet")
    queued = yaml.load(queue_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}
    entries = queued.get("entries") or queued.get("queue") or queued
    if isinstance(entries, dict):
        entries = entries.get("items") or []
    by_id: dict[str, list[str]] = {}
    # Grouped files keep observation_id under each group's observations list.
    flat_entries = expand_queue_entries(entries) if isinstance(entries, list) else []
    for e in flat_entries:
        oid = str(e.get("observation_id") or "")
        why = str(e.get("why") or "")
        axes = e.get("axes") or []
        by_id.setdefault(oid, []).append(why + " " + " ".join(str(a) for a in axes))
    missing = []
    scanned = 0
    summary = load_observation_store_summary(REPO_ROOT)
    needle = b"kind: unavailable"
    for folder in roots:
        for path in iter_observation_store_paths(folder):
            stored = _load_store_if_needle(path, needle, summary)
            if stored is None:
                continue
            for obs in stored.get("observations") or []:
                val = obs.get("value") or {}
                if val.get("kind") != "unavailable":
                    continue
                scanned += 1
                oid = str(obs.get("observation_id") or "")
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value") or (
                    ((obs.get("identity") or {}).get("quantity") or {}).get("reason") or ""
                )
                blob = " ".join(by_id.get(oid) or [])
                if not blob:
                    missing.append(oid)
                    continue
                token = str(q)
                if token and token not in blob and "field" not in blob.lower() and "value" not in blob.lower():
                    missing.append(f"{oid} queue={blob!r} token={token!r}")
    assert scanned > 0
    assert not missing, missing[:20]


def _queue_fingerprints(entries: list[dict]) -> list[str]:
    return [
        json.dumps(
            {
                "work_id": entry["work_id"],
                "source": entry["source"],
                "why": entry["why"],
                "axes": list(entry["axes"]),
                "observation_id": entry["observation_id"],
                "locator": entry["locator"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        for entry in entries
    ]


def _reason_and_axis_counts(entries: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    reasons: dict[str, int] = {}
    axes: dict[str, int] = {}
    for entry in entries:
        why = entry["why"]
        reasons[why] = reasons.get(why, 0) + 1
        for axis in entry["axes"]:
            axes[axis] = axes.get(axis, 0) + 1
    return reasons, axes


def test_queue_group_roundtrip_is_lossless(tmp_path: Path) -> None:
    """Grouping then expanding returns the same entries, reasons, and axes.

    Two writes of the same entries, in different input order, are the same bytes.
    A long extract path stays one contiguous substring so the freshness check
    can still see it.
    """

    long_source = "data/literature/extracts/" + ("long-extract-stem-" * 6) + "tail.yaml"
    assert len(long_source) > 120
    shared = {
        "work_id": "w-shared",
        "source": long_source,
        "why": "source does not state phase",
        "axes": ["phase"],
    }
    locator_page = {"figure": "10", "published_page": 18, "note": "same page"}
    locator_other = {"note": "other page", "published_page": 19}
    entries = [
        {
            "work_id": "w-other",
            "source": "data/literature/extracts/other.yaml",
            "why": "declared quantity is unknown",
            "axes": ["quantity", "value"],
            "observation_id": "obs-two-axes",
            "locator": {"table": "2", "page": "4"},
        },
        {**shared, "observation_id": "obs-b", "locator": dict(locator_other)},
        {
            **shared,
            "observation_id": "obs-a",
            "locator": {"note": "same page", "published_page": 18, "figure": "10"},
        },
        {**shared, "observation_id": "obs-a", "locator": dict(locator_page)},
        {**shared, "observation_id": "obs-a", "locator": dict(locator_other)},
    ]
    expected = expand_queue_entries(entries)
    grouped = group_queue_entries(entries)
    expanded = expand_queue_entries(grouped)

    assert all("observations" in group and "observation_id" not in group for group in grouped)
    phase_groups = [group for group in grouped if group["why"] == shared["why"]]
    assert len(phase_groups) == 1
    assert len(phase_groups[0]["observations"]) == 4
    assert {item["observation_id"] for item in phase_groups[0]["observations"]} == {
        "obs-a",
        "obs-b",
    }

    assert set(_queue_fingerprints(expanded)) == set(_queue_fingerprints(expected))
    assert sorted(_queue_fingerprints(expanded)) == sorted(_queue_fingerprints(expected))
    assert _reason_and_axis_counts(expanded) == _reason_and_axis_counts(expected)
    page_fingerprint = _queue_fingerprints(
        [{**shared, "observation_id": "obs-a", "locator": locator_page}]
    )[0]
    # Same observation and locator twice must stay two entries.
    assert _queue_fingerprints(expanded).count(page_fingerprint) == 2

    legacy = expand_queue_entries(
        [
            {
                "work_id": "w-legacy",
                "locator": {"page": "1"},
                "axes": ["value"],
                "why": "missing value",
                "source": "data/literature/extracts/legacy.yaml",
                "observation_id": "obs-legacy",
            }
        ]
    )
    assert legacy[0]["observation_id"] == "obs-legacy"
    assert legacy[0]["why"] == "missing value"
    assert legacy[0]["axes"] == ["value"]

    aliases = [
        {
            "observation_id": "alias-1",
            "source": long_source,
            "row_indices": [3, 1],
        }
    ]
    first = tmp_path / "queue-a.yaml"
    second = tmp_path / "queue-b.yaml"
    dump_yaml(migration_queue_document(entries, aliases), first)
    dump_yaml(migration_queue_document(list(reversed(entries)), aliases), second)
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    assert text.startswith(f"schema_version: {QUEUE_SCHEMA_VERSION}\n")
    filename = long_source.split("extracts/", 1)[1]
    assert f"extracts/{filename}" in text


def test_committed_migration_queue_is_canonical_grouped_form() -> None:
    path = REPO_ROOT / "data" / "battery" / "migration-queue.yaml"
    if not path.is_file():
        pytest.skip("migrated store not generated yet")
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    assert isinstance(document, dict)
    assert document.get("schema_version") == QUEUE_SCHEMA_VERSION
    entries = document.get("entries") or []
    expanded = expand_queue_entries(entries)
    rebuilt = migration_queue_document(expanded, document.get("dedupe_aliases") or [])
    assert rebuilt["entries"] == entries
    assert rebuilt["dedupe_aliases"] == document.get("dedupe_aliases")
    assert expanded
    assert len(expanded) == sum(len(group["observations"]) for group in entries)


def test_l05c5_unavailable_value_is_queued(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="delta_fG",
        units="",
        values={"quantity": "delta_fG", "method_class": "measured_direct"},
        obs_type="gibbs_table",
    )
    root = _write_min_tree(tmp_path, extract)
    (root / "data" / "literature" / "gibbs_battery_residual_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "ledger-logk",
                        "source_id": "fixture-source",
                        "species": "B",
                        "comparison_quantity": "log10_Kf",
                        "temperature_K": 300,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    logk = result.observations["ledger-logk"]
    assert logk.value.kind is ValueKind.UNAVAILABLE
    assert any(
        e.observation_id == "ledger-logk"
        and "value" in (e.axes or ())
        and ("log10_Kf" in (e.why or "") or "field" in (e.why or ""))
        for e in result.queue
    )


_USGS_F1_FAMILIES = (
    "robie-hemingway-fisher-1978-usgs-b1452",
    "hemingway-haas-robinson-1982-usgs-b1544",
    "robie-waldbaum-1968-usgs-b1259",
)
_ABSENT_DELTA_FG_CLAIM = re.compile(
    r"does not name a delta_fG field|series list had no numeric coordinate/value pairs|"
    r"source does not name a delta_fG",
    re.I,
)


def _copy_compilation_record(root: Path, source_id: str, filename: str) -> Path:
    src = (
        REPO_ROOT
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "records"
        / filename
    )
    dest = (
        root
        / "data"
        / "literature"
        / "compilations"
        / source_id
        / "records"
        / filename
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def _copy_extract(root: Path, filename: str) -> Path:
    src = REPO_ROOT / "data" / "literature" / "extracts" / filename
    dest = root / "data" / "literature" / "extracts" / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def _record_has_numeric_gibbs(doc: dict) -> bool:
    from simulator.battery.migrate import _census_formation_gibbs

    census = _census_formation_gibbs(doc)
    return any(info["numeric"] for info in census.values())


def test_f1_usgs_unavailable_reasons_match_printed_gibbs_cells(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    copies = [
        (
            "robie-hemingway-fisher-1978-usgs-b1452",
            "robie-hemingway-fisher-1978-usgs-b1452-0004.json",
        ),
        (
            "robie-hemingway-fisher-1978-usgs-b1452",
            "robie-hemingway-fisher-1978-usgs-b1452-0011-phase-02.json",
        ),
        (
            "hemingway-haas-robinson-1982-usgs-b1544",
            "usgs-b1544-al2sio5-reference.json",
        ),
        (
            "robie-waldbaum-1968-usgs-b1259",
            "b1259-ht-0001-silver-reference-state.json",
        ),
        (
            "robie-waldbaum-1968-usgs-b1259",
            "b1259-298k-0001-silver.json",
        ),
    ]
    for source_id, filename in copies:
        _copy_compilation_record(root, source_id, filename)
    result = migrate(root, write=False)

    b1452_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0004:"
    )
    b1452 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1452_prefix)
    ]
    assert b1452
    assert f"{b1452_prefix.rstrip(':')}" not in result.observations
    gibbs_0004 = [
        obs for obs in b1452 if quantity_token(obs.identity) is Quantity.DELTA_FG
    ]
    assert gibbs_0004
    assert all(obs.value.kind is ValueKind.POINT for obs in gibbs_0004)
    assert all(obs.identity.species.formula == "Ag" for obs in gibbs_0004)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in gibbs_0004
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in gibbs_0004
    )

    ocr_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0011-phase-02:"
    )
    ocr = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(ocr_prefix)
    ]
    # Generator stored 0: every numeric cell is OCR-suspect or excluded.
    # The generic placeholder must not remain.
    assert ocr == []
    assert f"{ocr_prefix.rstrip(':')}" not in result.observations

    b1544_prefix = (
        "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-al2sio5-reference:"
    )
    b1544 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1544_prefix)
    ]
    assert b1544
    assert f"{b1544_prefix.rstrip(':')}" not in result.observations
    gibbs = [obs for obs in b1544 if quantity_token(obs.identity) is Quantity.DELTA_FG]
    assert gibbs
    assert all(obs.value.kind is ValueKind.POINT for obs in gibbs)
    assert all(obs.identity.species.formula == "Al2SiO5" for obs in gibbs)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in gibbs
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in gibbs
    )

    ht_prefix = (
        "robie-waldbaum-1968-usgs-b1259:b1259-ht-0001-silver-reference-state:"
    )
    ht = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(ht_prefix)
    ]
    assert ht
    assert f"{ht_prefix.rstrip(':')}" not in result.observations
    assert all(obs.value.kind is ValueKind.POINT for obs in ht)
    assert all(obs.identity.species.formula == "Ag" for obs in ht)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in ht
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in ht
    )

    silver_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(silver_prefix)
    ]
    assert silver
    assert f"{silver_prefix.rstrip(':')}" not in result.observations
    assert all(obs.value.kind is ValueKind.POINT for obs in silver)
    assert all(obs.identity.species.formula == "Ag" for obs in silver)
    assert all(
        "not imported by the generic migrator" not in (obs.value.unavailable_reason or "")
        for obs in silver
    )
    assert all(
        _ABSENT_DELTA_FG_CLAIM.search(obs.value.unavailable_reason or "") is None
        for obs in silver
    )
    assert all(
        "as_published=" in (obs.locator.note or "")
        and "calories stay as published" in (obs.locator.note or "")
        for obs in silver
    )


def test_f1_store_usgs_reasons_do_not_deny_printed_gibbs() -> None:
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    bad: list[str] = []
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for source_id in _USGS_F1_FAMILIES:
        paths = iter_observation_store_paths(obs_dir, f"compilations-{source_id}.yaml")
        records_dir = (
            REPO_ROOT / "data" / "literature" / "compilations" / source_id / "records"
        )
        for path in paths:
            stored = yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}
            for obs in stored.get("observations") or []:
                q = ((obs.get("identity") or {}).get("quantity") or {}).get("value")
                if q != "delta_fG":
                    continue
                val = obs.get("value") or {}
                if val.get("kind") != "unavailable":
                    continue
                reason = str(val.get("unavailable_reason") or "")
                loc = obs.get("locator") or {}
                record_id = loc.get("record") or str(obs.get("observation_id") or "").split(":", 1)[-1]
                rec_path = records_dir / f"{record_id}.json"
                if not rec_path.is_file():
                    continue
                doc = json.loads(rec_path.read_text(encoding="utf-8"))
                if not _record_has_numeric_gibbs(doc):
                    continue
                if _ABSENT_DELTA_FG_CLAIM.search(reason):
                    bad.append(f"{obs.get('observation_id')} reason={reason!r}")
    assert not bad, bad[:12]


def test_f2_phase_quotes_printed_text_and_keeps_unknown(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0004.json",
    )
    _copy_compilation_record(
        root,
        "hemingway-haas-robinson-1982-usgs-b1544",
        "usgs-b1544-al2sio5-reference.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0001-silver.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0002-ag-aqueous-ion.json",
    )
    _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0048-li-aqueous-ion.json",
    )
    result = migrate(root, write=False)

    b1452_prefix = (
        "robie-hemingway-fisher-1978-usgs-b1452:"
        "robie-hemingway-fisher-1978-usgs-b1452-0004:"
    )
    b1452 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1452_prefix)
    ]
    assert b1452
    phases = [obs.identity.species.phase for obs in b1452]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Ag" for obs in b1452)

    b1544_prefix = (
        "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-al2sio5-reference:"
    )
    b1544 = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(b1544_prefix)
    ]
    assert b1544
    phases = [obs.identity.species.phase for obs in b1544]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Al2SiO5" for obs in b1544)

    silver_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(silver_prefix)
    ]
    assert silver
    phases = [obs.identity.species.phase for obs in silver]
    assert all(phase.is_value and phase.value is Phase.CR for phase in phases)
    assert all(
        "not in the closed automatic map" not in (phase.reason or "")
        for phase in phases
    )
    assert all(obs.identity.species.formula == "Ag" for obs in silver)

    aqueous_prefix = (
        "robie-waldbaum-1968-usgs-b1259:b1259-298k-0002-ag-aqueous-ion:"
    )
    aqueous = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(aqueous_prefix)
    ]
    assert aqueous
    phases = [obs.identity.species.phase for obs in aqueous]
    assert all(phase.is_value and phase.value is Phase.AQ for phase in phases)
    assert all(obs.identity.species.formula == "Ag" for obs in aqueous)

    li_prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0048-li-aqueous-ion:"
    lithium = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(li_prefix)
    ]
    assert lithium
    assert all(obs.identity.species.formula == "Li" for obs in lithium)
    assert all(
        obs.identity.species.phase.is_value
        and obs.identity.species.phase.value is Phase.AQ
        for obs in lithium
    )
    assert "Li aqueous" not in {
        obs.identity.species.formula for obs in lithium
    }


def test_f3_compilation_locator_names_the_record_file(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-waldbaum-1968-usgs-b1259",
        "b1259-298k-0001-silver.json",
    )
    result = migrate(root, write=False)
    prefix = "robie-waldbaum-1968-usgs-b1259:b1259-298k-0001-silver:"
    silver = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(prefix)
    ]
    assert silver
    rel = dest.relative_to(root).as_posix()
    assert all(obs.locator.source_path == rel for obs in silver)
    assert dest.is_file()


def test_f3_store_every_compilation_observation_has_existing_source_path() -> None:
    obs_dir = REPO_ROOT / "data" / "literature" / "observations-v2"
    if not obs_dir.is_dir():
        pytest.skip("migrated store not generated yet")
    missing: list[str] = []
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for path in iter_observation_store_paths(obs_dir, "compilations-*.yaml"):
        stored = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
        for obs in stored.get("observations") or []:
            loc = obs.get("locator") or {}
            rel = loc.get("source_path")
            oid = obs.get("observation_id")
            if not rel:
                missing.append(f"{oid} missing source_path")
                continue
            if not (REPO_ROOT / str(rel)).is_file():
                missing.append(f"{oid} source_path {rel!r} does not exist")
    assert not missing, missing[:20]


def test_f4_antoine_and_points_and_range_restore_corroborated_quantity(
    tmp_path: Path,
) -> None:
    br72 = _extract_observation(
        "behrens-rosenblatt-1972.yaml", "NIST_BR72_arsenolite_As4O6"
    )
    state, _reason = map_quantity(
        br72.get("type"), br72.get("values"), units=br72.get("units"), row=br72
    )
    assert state.is_value and state.value is Quantity.P_SAT

    hab = _extract_observation("habermann-daane-1964.yaml", "Hab64_Yb_metal_antoine")
    state, _reason = map_quantity(
        hab.get("type"), hab.get("values"), units=hab.get("units"), row=hab
    )
    assert state.is_value and state.value is Quantity.P_SAT

    stull = _extract_observation("nist-webbook.yaml", "NIST_Stull47_As2O3_highT")
    state, _reason = map_quantity(
        stull.get("type"), stull.get("values"), units=stull.get("units"), row=stull
    )
    assert state.is_value and state.value is Quantity.P_SAT

    se = _extract_observation("nist-webbook.yaml", "NIST_Stull_Se_total_P")
    state, _reason = map_quantity(
        se.get("type"), se.get("values"), units=se.get("units"), row=se
    )
    assert state.is_unknown
    assert "total vapour pressure over a multi-species vapour" in state.reason
    assert (se.get("values") or {}).get("gas_basis") == "TOTAL_PRESSURE_not_species"

    bic = _extract_observation(
        "berkowitz-chupka-inghram-1957.yaml", "BIC57_VO_absolute_points"
    )
    state, _reason = map_quantity(
        bic.get("type"), bic.get("values"), units=bic.get("units"), row=bic
    )
    assert state.is_value and state.value is Quantity.P_SAT

    costa = _extract_observation(
        "costa-jacobson-2015.yaml", "costa_jacobson_2015_sio_olivine_kems"
    )
    state, _reason = map_quantity(
        costa.get("type"), costa.get("values"), units=costa.get("units"), row=costa
    )
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA

    sf04 = _extract_observation(
        "sf04-magma-companion-workbook.yaml", "sf04_workbook_tho_fe_pressure_series"
    )
    state, _reason = map_quantity(
        sf04.get("type"), sf04.get("values"), units=sf04.get("units"), row=sf04
    )
    assert state.is_value and state.value is Quantity.P_PARTIAL

    root = _write_min_tree(tmp_path)
    for fname in (
        "behrens-rosenblatt-1972.yaml",
        "habermann-daane-1964.yaml",
        "nist-webbook.yaml",
        "berkowitz-chupka-inghram-1957.yaml",
        "costa-jacobson-2015.yaml",
        "sf04-magma-companion-workbook.yaml",
    ):
        _copy_extract(root, fname)
    result = migrate(root, write=False)

    br72_obs = result.observations[
        "behrens-rosenblatt-1972::NIST_BR72_arsenolite_As4O6"
    ]
    assert quantity_token(br72_obs.identity) is Quantity.P_SAT
    assert br72_obs.identity.species.phase.value is Phase.G
    assert br72_obs.identity.species.polymorph.is_not_applicable
    assert br72_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_bar) = A" in (br72_obs.value.unavailable_reason or "")

    hab_obs = result.observations["habermann-daane-1964::Hab64_Yb_metal_antoine"]
    assert quantity_token(hab_obs.identity) is Quantity.P_SAT
    assert hab_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_mmHg) = A" in (hab_obs.value.unavailable_reason or "")

    stull_obs = result.observations["nist-webbook::NIST_Stull47_As2O3_highT"]
    assert quantity_token(stull_obs.identity) is Quantity.P_SAT
    assert stull_obs.value.kind is ValueKind.UNAVAILABLE
    assert "log10(P_bar) = A" in (stull_obs.value.unavailable_reason or "")

    se_obs = result.observations["nist-webbook::NIST_Stull_Se_total_P"]
    assert se_obs.identity.quantity.is_unknown
    assert "no total-vapour-pressure identity" in se_obs.identity.quantity.reason
    assert se_obs.value.kind is ValueKind.UNAVAILABLE
    assert "total vapour pressure" in (se_obs.value.unavailable_reason or "")

    bic_rows = [
        o
        for o in result.observations.values()
        if "BIC57_VO_absolute_points" in o.observation_id
    ]
    assert bic_rows
    assert all(quantity_token(o.identity) is Quantity.P_SAT for o in bic_rows)
    amounts = sorted(
        float(o.value.point)
        for o in bic_rows
        if o.value.kind is ValueKind.POINT and o.value.point is not None
    )
    series_parent = [
        o for o in bic_rows if o.value.kind is ValueKind.SERIES and o.value.series
    ]
    assert amounts == [0.116, 0.155] or (
        series_parent
        and {float(p) for _t, p in series_parent[0].value.series} == {0.116, 0.155}
    )

    costa_obs = result.observations[
        "costa-jacobson-2015::costa_jacobson_2015_sio_olivine_kems"
    ]
    assert quantity_token(costa_obs.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert costa_obs.value.kind is ValueKind.INTERVAL
    assert costa_obs.value.interval_low == as_decimal("0.003")
    assert costa_obs.value.interval_high == as_decimal("0.036")

    sf04_rows = [
        o
        for o in result.observations.values()
        if "sf04_workbook_tho_fe_pressure_series" in o.observation_id
    ]
    assert sf04_rows
    assert all(quantity_token(o.identity) is Quantity.P_PARTIAL for o in sf04_rows)
    assert any(
        o.value.kind is ValueKind.SERIES
        or (o.value.kind is ValueKind.POINT and o.value.point is not None)
        for o in sf04_rows
    )
    assert all(
        o.evidence.class_.is_value
        and o.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        for o in sf04_rows
    )
    assert all(
        o.evidence.class_.value is not EvidenceClass.MEASURED_DIRECT
        and o.evidence.class_.value is not EvidenceClass.MEASURED_REDUCED
        and o.evidence.class_.value is not EvidenceClass.MEASURED_TABULATED
        for o in sf04_rows
    )


def test_pyrolysis_yield_quantities_are_not_collapsed() -> None:
    """O2/feedstock, O2/sample, and bulk mass loss stay distinct closed tokens."""

    bulk, _reason = map_quantity(
        "rate_series",
        {"quantity": "bulk_mass_loss_wt_pct", "mass_loss_wt_pct": 1.1},
        units="wt_percent",
    )
    assert bulk.is_value and bulk.value is Quantity.MASS_LOSS_FRACTION

    sidecar, _reason = map_quantity(
        "rate_series",
        {
            "quantity": "non_condensed_mass_loss_fraction",
            "non_condensed_mass_loss_fraction": 0.0117,
        },
        units="mass_fraction",
    )
    assert sidecar.is_value and sidecar.value is Quantity.MASS_LOSS_FRACTION

    mixed, reason = map_quantity(
        "rate_series",
        {
            "quantity": "measured_oxygen_yield",
            "oxygen_mass_mg": 35,
            "mass_yield_percent": 1.05,
            "fraction_of_feedstock_oxygen_percent": 2.47,
        },
        units="as published",
    )
    assert not mixed.is_value
    assert reason and "measured_oxygen_yield" in reason

    outgassing, reason = map_quantity(
        "rate_series",
        {"quantity": "total_mass_loss", "total_mass_loss_wt_pct": 0.40},
        units="wt_percent",
    )
    assert not outgassing.is_value
    assert reason and "total_mass_loss" in reason

    summary, reason = map_quantity(
        "rate_series",
        {"quantity": "vacuum_pyrolysis_experiment_summary", "tests": []},
        units="as_published",
    )
    assert not summary.is_value
    assert reason and "vacuum_pyrolysis_experiment_summary" in reason

    model, reason = map_quantity(
        "rate_series",
        {"quantity": "oxygen_yield_wt_pct", "O2_yield_pct_of_oxide": 19.3},
        units="wt_percent",
    )
    assert not model.is_value


def test_areal_mass_loss_routes_delta_q_to_kg_per_m2(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="mass_loss_areal_density",
        units="mg/cm2; T C; P hPa",
        values={
            "quantity_as_printed": "delta_q",
            "method_class": "measured_direct",
            "delta_q": "5.55184",
        },
        obs_type="mass_loss",
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = result.observations["fixture-source::na_psat"]
    assert quantity_token(obs.identity) is Quantity.MASS_LOSS_AREAL_DENSITY
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("0.0555184")
    assert obs.derivation is not None
    assert obs.derivation.relation == "mg_per_cm2_to_kg_per_m2"
    assert obs.derivation.output_unit == "kg_per_m2"


@pytest.mark.parametrize(
    ("units", "expected", "relation"),
    [
        ("g/m2", "0.00555184", "g_per_m2_to_kg_per_m2"),
        ("mg/m2", "0.00000555184", "mg_per_m2_to_kg_per_m2"),
    ],
)
def test_areal_mass_loss_contradiction_matches_converter(
    tmp_path: Path, units: str, expected: str, relation: str
) -> None:
    extract = _scalar_extract(
        quantity="mass_loss_areal_density",
        units=units,
        values={
            "quantity_as_printed": "delta_q",
            "method_class": "measured_direct",
            "delta_q": "5.55184",
        },
        obs_type="mass_loss",
    )
    root = _write_min_tree(tmp_path, extract)

    result = migrate(root, write=False)
    obs = result.observations["fixture-source::na_psat"]

    assert quantity_token(obs.identity) is Quantity.MASS_LOSS_AREAL_DENSITY
    assert obs.value.point == as_decimal(expected)
    assert obs.derivation is not None
    assert obs.derivation.relation == relation


def test_fugacity_series_routes_bar_to_pa_without_partial_pressure(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    base = extract["species"]["Na"]["observations"][0]
    measured = yaml.safe_load(yaml.safe_dump(base))
    measured["observation_id"] = "measured_parent"
    measured["values"] = {
        "quantity": "pure_Psat",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "pressure_atm": 1.0,
    }
    fugacity = yaml.safe_load(yaml.safe_dump(base))
    fugacity.update(
        {
            "observation_id": "agI_cl_007_fugacity",
            "type": "fugacity_series",
            "units": "bar",
            "values": {
                "method_class": "calculated",
                "admission_status": "admitted",
                "derived_from": ["measured_parent"],
                "derivation": {
                    "relation": "agcl_agi_mass_balance",
                    "inputs": ["fixture-source::measured_parent"],
                    "output_unit": "Pa",
                },
                "series": [{"T_K": 1200.0, "fCl2_bar": "3.37E-04"}],
            },
        }
    )
    extract["species"]["Na"]["observations"] = [measured, fugacity]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(
        item
        for item in result.observations.values()
        if "agI_cl_007_fugacity" in item.observation_id
    )
    assert quantity_token(obs.identity) is Quantity.FUGACITY
    assert quantity_token(obs.identity) is not Quantity.P_PARTIAL
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == as_decimal("33.7")
    assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert obs.derivation is not None
    assert obs.derivation.relation == "agcl_agi_mass_balance"
    assert obs.derivation.output_unit == "Pa"
    original = dict(obs.derivation.parameters)["original"]
    assert original.state.value == as_decimal("3.37E-04")
    assert original.locator == obs.locator
    assert not result.validation.hard_issues


def test_sauerborn_mass_loss_points_explode_with_point_t(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="bulk_mass_loss_wt_pct",
        units="wt_percent",
        values={
            "quantity": "bulk_mass_loss_wt_pct",
            "method_class": "measured_direct",
            "points": [
                {
                    "id": "SiO2",
                    "mass_loss_wt_pct": 1.1,
                    "Tmax_C": 1400,
                    "Tmax_K": 1673.15,
                    "mass_g": 0.6719,
                    "locator": {"page": 76},
                },
                {
                    "id": "MS2",
                    "mass_loss_wt_pct": 3.2,
                    "Tmax_C": 1563,
                    "Tmax_K": 1836.15,
                    "mass_g": 0.991,
                    "locator": {"page": 88},
                },
            ],
        },
        obs_type="rate_series",
    )
    extract["species"]["Na"]["observations"][0]["regime"] = "solar_vacuum_pyrolysis"
    extract["species"]["Na"]["observations"][0]["phase"] = "l"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if "::T=" in o.observation_id
    ]
    assert len(points) == 2
    by_value = {o.value.point: o for o in points}
    sio2 = by_value[as_decimal("0.011")]
    assert sio2.identity.species.formula == "SiO2"
    assert quantity_token(sio2.identity) is Quantity.MASS_LOSS_FRACTION
    assert sio2.value.kind is ValueKind.POINT
    assert float(sio2.identity.temperature_K.value) == 1673.15
    assert sio2.admission.status is AdmissionStatus.ADMITTED
    ms2 = by_value[as_decimal("0.032")]
    assert ms2.identity.species.formula == "unknown"
    assert float(ms2.identity.temperature_K.value) == 1836.15
    assert ms2.admission.status is AdmissionStatus.ADMITTED


def test_robinot_measured_oxygen_yield_splits_and_keeps_t_range(
    tmp_path: Path,
) -> None:
    extract = _scalar_extract(
        quantity="measured_oxygen_yield",
        units="percent",
        values={
            "quantity": "measured_oxygen_yield",
            "method_class": "measured_direct",
            "oxygen_mass_mg": 35,
            "mass_yield_percent": 1.05,
            "fraction_of_feedstock_oxygen_percent": 2.47,
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [1473.15, 2073.15]
    row["regime"] = "solar_vacuum_pyrolysis_free_evaporation"
    row["phase"] = "g"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    rows = list(result.observations.values())
    tokens = {quantity_token(o.identity): o for o in rows}
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    assert Quantity.MASS_LOSS_FRACTION not in tokens
    yield_frac = tokens[Quantity.YIELD_FRACTION]
    assert yield_frac.value.kind is ValueKind.POINT
    assert yield_frac.value.point == as_decimal("0.0105")
    o2_yield = tokens[Quantity.O2_YIELD]
    assert o2_yield.value.point == as_decimal("0.0247")
    for obs in (yield_frac, o2_yield):
        assert obs.identity.temperature_K.is_unknown
        assert "no midpoint invented" in (obs.identity.temperature_K.reason or "")
        assert "1473.15" in (obs.identity.temperature_K.reason or "")
        assert obs.admission.status is AdmissionStatus.ADMITTED
        local = obs.observation_id.split("::", 1)[1]
        if "::field:" in local:
            local = local.split("::field:")[0]
        elif "::point:" in local:
            local = local.split("::point:")[0]
        assert local == "na_psat"


def test_cardiff_tests_explode_without_inventing_bound_t(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="vacuum_pyrolysis_experiment_summary",
        units="as_published",
        values={
            "quantity": "vacuum_pyrolysis_experiment_summary",
            "method_class": "measured_direct",
            "contradiction_vs_matchett_2006_table3": {
                "test_2b_mass_loss": {"cardiff_table1": "16.00%", "matchett_table3": "0.16%"},
                "resolution": "none_both_printed_this_extract_keeps_cardiff_as_printed",
            },
            "tests": [
                {
                    "test": "2b",
                    "sample": "FeTiO3",
                    "Tmax_C": None,
                    "Tmax_C_as_printed": ">800",
                    "mass_loss_pct": 16.0,
                },
                {
                    "test": 3,
                    "sample": "FeTiO3",
                    "Tmax_C": 700.0,
                    "mass_loss_pct": 37.0,
                },
                {
                    "test": 11,
                    "sample": "MLS-1a",
                    "Tmax_C": 1474.0,
                    "mass_loss_pct": 10.1,
                },
                {
                    "test": 12,
                    "sample": "MLS-1a",
                    "Tmax_C": 684.0,
                    "mass_loss_pct": None,
                    "mass_loss_as_printed": "-",
                },
            ],
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [821.15, 2140.15]
    row["regime"] = "solar_fresnel_continuously_pumped_vacuum_pyrolysis"
    row["phase"] = "l"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = [
        o
        for o in result.observations.values()
        if quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
    ]
    assert len(points) == 3
    by_value = {float(o.value.point): o for o in points}
    mls = by_value[0.101]
    assert mls.identity.species.formula == "unknown"
    assert float(mls.identity.temperature_K.value) == 1474.0 + 273.15
    assert mls.admission.status is AdmissionStatus.ADMITTED
    bound = by_value[0.16]
    assert bound.identity.species.formula == "FeTiO3"
    assert bound.identity.temperature_K.is_unknown
    assert bound.admission.status is AdmissionStatus.PENDING
    disagreed = by_value[0.37]
    assert disagreed.admission.status is AdmissionStatus.PENDING
    assert "16.00/37.00" in disagreed.admission.reason
    assert any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in disagreed.notices)
    assert any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in bound.notices)
    assert not any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in mls.notices)


def test_partial_pressure_point_sample_is_not_species_formula(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="partial_pressure",
        units="Pa",
        values={
            "quantity": "partial_pressure",
            "method_class": "measured_direct",
            "admission_status": "admitted",
            "points": [{"T_K": 1200.0, "p_Pa": 2.0, "sample": "12022"}],
        },
    )
    row = extract["species"]["Na"]["observations"][0]
    row["phase"] = "basaltic_silicate_melt"
    extract["species"] = {"O2": {"observations": [row]}}
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)

    observation = next(
        o
        for o in result.observations.values()
        if quantity_token(o.identity) is Quantity.P_PARTIAL
    )
    assert observation.identity.species.formula == "O2"


def test_live_pyrolysis_extracts_map_distinct_yield_quantities(tmp_path: Path) -> None:
    extracts_src = REPO_ROOT / "data" / "literature" / "extracts"
    names = [
        "kems-044-robinot-2026.yaml",
        "kems-035-sauerborn-2005.yaml",
        "cardiff-2007-vacuum-pyrolysis-gsfc.yaml",
        "kems-038-matchett-2006.yaml",
        "wilkerson-2023-jsc1a-outgassing.yaml",
        "steurer-1985-vapor-phase-pyrolysis.yaml",
    ]
    dest = tmp_path / "data" / "literature" / "extracts"
    dest.mkdir(parents=True)
    (tmp_path / "data" / "literature" / "compilations").mkdir(parents=True)
    sources = []
    for name in names:
        src = extracts_src / name
        (dest / name).write_bytes(src.read_bytes())
        sources.append({"source_id": src.stem, "citation": src.stem})
    (tmp_path / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "scan": {"corpus_root": "regolith-corpus"},
                "sources": sources,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(tmp_path, write=False)
    robinot = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-044-robinot-2026"
        and quantity_token(o.identity) in {Quantity.YIELD_FRACTION, Quantity.O2_YIELD}
    ]
    tokens = {quantity_token(o.identity) for o in robinot}
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    assert all(
        o.identity.temperature_K.is_unknown
        and "no midpoint invented" in (o.identity.temperature_K.reason or "")
        for o in robinot
    )
    sauerborn = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-035-sauerborn-2005"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    assert {float(o.value.point) for o in sauerborn} >= {0.011, 0.026, 0.032, 0.029}
    assert all(
        o.evidence.class_.is_value
        and o.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for o in sauerborn
        if o.admission.status is not AdmissionStatus.SUPERSEDED
    )
    cardiff = [
        o
        for o in result.observations.values()
        if o.source_id == "cardiff-2007-vacuum-pyrolysis-gsfc"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    cardiff_vals = {float(o.value.point) for o in cardiff}
    assert 0.101 in cardiff_vals
    assert 0.16 in cardiff_vals
    assert 0.37 in cardiff_vals
    matchett = [
        o
        for o in result.observations.values()
        if o.source_id == "kems-038-matchett-2006"
        and quantity_token(o.identity) is Quantity.MASS_LOSS_FRACTION
        and o.value.kind is ValueKind.POINT
    ]
    matchett_vals = {float(o.value.point) for o in matchett}
    assert 0.101 in matchett_vals
    assert 0.0016 in matchett_vals
    assert 0.0037 in matchett_vals
    forbidden = {
        o.source_id
        for o in result.observations.values()
        if o.source_id
        in {"wilkerson-2023-jsc1a-outgassing", "steurer-1985-vapor-phase-pyrolysis"}
        and quantity_token(o.identity)
        in {
            Quantity.MASS_LOSS_FRACTION,
            Quantity.YIELD_FRACTION,
            Quantity.O2_YIELD,
        }
    }
    assert not forbidden


def test_robinot_peak_about_is_not_invented_as_point_t(tmp_path: Path) -> None:
    """Robinot prints 'around 1800 °C' as a punctual pyrometer peak, not a hold."""

    extract = _scalar_extract(
        quantity="yield_fraction",
        units="percent",
        values={
            "quantity": "yield_fraction",
            "method_class": "measured_direct",
            "mass_yield_percent": 1.05,
            "peak_T_C_about": 1800,
            "peak_T_K_converted": 2073.15,
        },
        obs_type="rate_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_range_K"] = [1473.15, 2073.15]
    row["phase"] = "g"
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    obs = next(
        o
        for o in result.observations.values()
        if quantity_token(o.identity) is Quantity.YIELD_FRACTION
    )
    assert obs.identity.temperature_K.is_unknown
    reason = obs.identity.temperature_K.reason or ""
    assert "source T_range_K [1473.15, 2073.15]; no midpoint invented" == reason
    assert obs.identity.temperature_K.value is None


def test_store_pyrolysis_yield_census_is_honest() -> None:
    from simulator.battery.migrate import observation_from_plain
    from simulator.battery.score import MEASURED_EVIDENCE, rail_for_quantity

    files = [
        REPO_ROOT / "data/literature/extracts-v2/kems-044-robinot-2026.yaml",
        REPO_ROOT / "data/literature/extracts-v2/kems-035-sauerborn-2005.yaml",
        REPO_ROOT / "data/literature/extracts-v2/cardiff-2007-vacuum-pyrolysis-gsfc.yaml",
        REPO_ROOT / "data/literature/extracts-v2/kems-038-matchett-2006.yaml",
        REPO_ROOT / "data/literature/observations-v2/vacuum_pyrolysis_measurements.yaml",
    ]
    rows = []
    for path in files:
        stored = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
        for raw in stored.get("observations") or []:
            obs = observation_from_plain(raw)
            q = quantity_token(obs.identity)
            formula = obs.identity.species.formula
            if q is None:
                continue
            if rail_for_quantity(q, species_formula=formula) is Rail.PYROLYSIS_YIELD:
                rows.append(obs)
    assert len(rows) == 30
    tokens = {quantity_token(o.identity) for o in rows}
    assert Quantity.MASS_LOSS_FRACTION in tokens
    assert Quantity.YIELD_FRACTION in tokens
    assert Quantity.O2_YIELD in tokens
    candidates = [
        o
        for o in rows
        if o.evidence.class_.is_value
        and o.evidence.class_.value in MEASURED_EVIDENCE
        and o.admission.status in {AdmissionStatus.ADMITTED, AdmissionStatus.PENDING}
    ]
    assert len(candidates) == 26
    admitted = [o for o in candidates if o.admission.status is AdmissionStatus.ADMITTED]
    pending = [o for o in candidates if o.admission.status is AdmissionStatus.PENDING]
    assert len(admitted) == 22
    assert len(pending) == 4
    disagreed = [
        o
        for o in pending
        if any(n.kind is NoticeKind.SOURCE_DISAGREEMENT for n in o.notices)
    ]
    assert len(disagreed) == 4
    from simulator.battery.score import decision_band_for
    from simulator.battery.enums import SourceRelation

    assert decision_band_for(Quantity.MASS_LOSS_FRACTION, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.O2_YIELD, SourceRelation.INDEPENDENT) is None
    assert decision_band_for(Quantity.YIELD_FRACTION, SourceRelation.INDEPENDENT) is None


def test_vacuum_pyrolysis_sidecar_loads_pomeroy_not_robinot_duplicates(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(tmp_path)
    sidecar = load_cached_safe_yaml(
        (REPO_ROOT / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").read_text(
            encoding="utf-8"
        )
    )
    (root / "data" / "literature" / "vacuum_pyrolysis_measurements.yaml").write_text(
        yaml.safe_dump(sidecar, sort_keys=False),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    pomeroy = result.observations[
        "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction"
    ]
    assert quantity_token(pomeroy.identity) is Quantity.MASS_LOSS_FRACTION
    assert pomeroy.value.kind is ValueKind.POINT
    assert pomeroy.value.point == as_decimal("0.0117")
    assert float(pomeroy.identity.temperature_K.value) == 1400.0 + 273.15
    assert pomeroy.admission.status is AdmissionStatus.ADMITTED
    assert any(
        notice.kind is NoticeKind.ADMISSION_DEFAULTED for notice in pomeroy.notices
    )
    work = result.works[result.experiments[pomeroy.experiment_id].work_id]
    asset_ids = {f.asset_id for f in work.source_files.files}
    assert pomeroy.read_from in asset_ids
    assert not any(
        oid.startswith("robinot_2026_deposit_measurements:")
        for oid in result.observations
    )


def test_d032_context_rows_carried_not_observed(tmp_path: Path) -> None:
    """d-032 ruling: extract ``context`` rows reach the work record verbatim
    and never become Observation records in the v2.1 store."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    context_row = {
        "observation_id": "na_bench_note",
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "phase": "gas",
        "regime": "knudsen_effusion",
        "units": "as printed",
        "values": {
            "quantity": "apparatus_note",
            "method_class": "method_only",
            "furnace": "muffle",
            "sample_mass_g": 90,
        },
    }
    extract["species"]["Na"]["context"] = [context_row]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    qualified = "fixture-source::context::na_bench_note"
    assert qualified not in result.observations
    assert not any(
        oid.endswith("::na_bench_note") for oid in result.observations
    )
    work_id = next(iter(result.context_by_work))
    carried = result.context_by_work[work_id]
    assert len(carried) == 1
    record = carried[0]
    assert record["context_id"] == qualified
    assert record["work_id"] == work_id
    assert record["source_id"] == "fixture-source"
    assert record["species"] == "Na"
    assert record["type"] == "apparatus"
    assert record["values"]["sample_mass_g"] == 90
    assert record["locator"]["page"] == 3
    # The scored row still migrates exactly as before (series explodes to points).
    assert any(
        oid.startswith("fixture-source::na_psat") for oid in result.observations
    )
    # Works payload carries the container; the store holds no observation for it.
    works_docs = [
        d
        for d in (
            load_cached_safe_yaml(p.read_text(encoding="utf-8"))
            for p in (root / "data" / "literature" / "works").glob("*.yaml")
        )
        if isinstance(d, dict)
    ]
    context_entries = [
        row for doc in works_docs for row in (doc.get("context") or [])
    ]
    assert [row["context_id"] for row in context_entries] == [qualified]
    works, experiments, observations = load_migrated_store(root)
    assert qualified not in observations
    assert not any(oid.endswith("::na_bench_note") for oid in observations)


def test_d036_equipment_fk_resolves_through_context_row(tmp_path: Path) -> None:
    """d-036 ruling: boulliung's declared experiment reaches
    ``boulliung_2025_ssas_apparatus_and_run_conditions`` BY REFERENCE.
    Equipment values are read through the FK and never copied onto the
    experiment record."""
    extract = load_cached_safe_yaml(
        (
            REPO_ROOT
            / "data/literature/extracts/boulliung-2025-mercury-volatile-metals-magmatic.yaml"
        ).read_text(encoding="utf-8")
    )
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    experiment_id = "10.1016/j.chemgeo.2025.123018::experiment::ssas-hg-volatile-series"
    context_id = (
        "boulliung-2025-mercury-volatile-metals-magmatic"
        "::context::boulliung_2025_ssas_apparatus_and_run_conditions"
    )
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == context_id

    # The reference resolves against the work record's context rows, and the
    # equipment values are readable through it.
    context = load_migrated_context(root)
    row = resolve_equipment_context(experiment, context)
    assert row is not None
    assert row["context_id"] == context_id
    assert row["type"] == "laboratory_conditions"
    assert row["equipment"]["cell_material"]["value"] == "silica tube"
    assert row["equipment"]["cell_material"]["locator"] == {
        "pdf_page_index": 8,
        "section": "2.1 Experiments",
    }
    assert row["equipment"]["crucible_material"]["value"] == "alumina"
    assert row["equipment"]["crucible_material"]["locator"] == {
        "pdf_page_index": 8,
        "section": "2.1 Experiments",
    }
    assert row["equipment"]["sample_container"]["value"] == "alumina crucible"
    ampoule_volume = next(
        fact
        for fact in row["equipment"]["other_facts"]["value"]
        if fact["name"] == "ampoule_volume_m3"
    )
    assert ampoule_volume["value"] == "4.4 x 10^-6"
    assert ampoule_volume["locator"] == {
        "pdf_page_index": 9,
        "section": "2.1 Experiments",
    }
    assert row["values"]["apparatus"]["furnace"] == "muffle furnace"

    # No duplication: the experiment record carries none of the row's
    # equipment keys or values; they exist in exactly one place.
    assert experiment.apparatus is not None
    assert experiment.apparatus.cell_material_and_liner is None
    experiment_text = yaml.safe_dump(to_plain(experiment))
    for key in (
        "cell_material",
        "crucible_material",
        "sample_container",
        "ampoule_volume_m3",
        "chamber_length_cm",
        "thermocouple",
        "silica tube",
    ):
        assert key not in experiment_text
    work_file = root / "data/literature/works/10.1016_j.chemgeo.2025.123018.yaml"
    work_text = work_file.read_text(encoding="utf-8")
    assert work_text.count("ampoule_volume_m3") == 1
    work_doc = yaml.safe_load(work_text)
    assert [row["context_id"] for row in work_doc["context"]] == [
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_ag_trace_doping_and_comparator",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_hg_oxidation_state_and_fugacity",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_ssas_apparatus_and_run_conditions",
        "boulliung-2025-mercury-volatile-metals-magmatic::context::boulliung_2025_starting_material_hg_and_trace_doping",
    ]

    # The FK round-trips through the persisted store.
    _, experiments, _ = load_migrated_store(root)
    assert experiments[experiment_id].equipment_context_id == context_id
    assert experiments[experiment_id].apparatus.cell_material_and_liner is None


def test_d036_migrator_links_equipment_context_rows(tmp_path: Path) -> None:
    """Fixture-level link rules: an equipment-bearing context row that names a
    declared experiment sets the FK; a context row without equipment evidence
    does not; a second equipment row for the same experiment is a typed
    registry issue, never a silent overwrite."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["experiments"] = [
        {
            "experiment_id": "fixture-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    extract["species"]["Na"]["context"] = [
        {
            "observation_id": "apparatus_row",
            "experiment": "fixture-series",
            "type": "laboratory_conditions",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "alumina"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        },
        {
            "observation_id": "characterization_row",
            "experiment": "fixture-series",
            "type": "characterization",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "values": {"quantity": "sample_note", "method_class": "method_only"},
        },
        {
            "observation_id": "second_apparatus_row",
            "experiment": "fixture-series",
            "type": "apparatus",
            "locator": {"page": 4, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "platinum"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        },
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=True)
    experiment_id = next(
        eid for eid in result.experiments if eid.endswith("::experiment::fixture-series")
    )
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == "fixture-source::context::apparatus_row"
    # The row without equipment evidence is context, not an equipment record.
    assert experiment.equipment_context_id != "fixture-source::context::characterization_row"
    # The second equipment row is a typed conflict, not a silent overwrite.
    conflicts = [
        issue
        for issue in result.registry_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(conflicts) == 1
    assert conflicts[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "second_apparatus_row" in conflicts[0].detail
    assert "apparatus_row" in conflicts[0].detail
    row = resolve_equipment_context(experiment, load_migrated_context(root))
    assert row["equipment"]["cell_material"] == "alumina"


def test_d036_equipment_context_row_naming_undeclared_experiment_refuses(
    tmp_path: Path,
) -> None:
    """A context row whose ``experiment:`` names no declared experiment is a
    typed registry refusal naming the missing id — never a manufactured
    pointer."""
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["Na"]["context"] = [
        {
            "observation_id": "apparatus_row",
            "experiment": "ghost-series",
            "type": "laboratory_conditions",
            "locator": {"page": 3, "section": "experimental"},
            "units": "as printed",
            "equipment": {"cell_material": "alumina"},
            "values": {"quantity": "apparatus_note", "method_class": "method_only"},
        }
    ]
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    assert not any(
        experiment.equipment_context_id is not None
        for experiment in result.experiments.values()
    )
    issues = [
        issue
        for issue in result.registry_issues
        if issue.path == "context[fixture-source::context::apparatus_row].experiment"
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "ghost-series" in issues[0].detail


def test_d036_dangling_equipment_context_fk_refuses_typed(tmp_path: Path) -> None:
    """A stored equipment_context_id that names no context row is a typed
    refusal naming the missing id — at resolution and at validation."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    assert experiment.equipment_context_id is None
    # Absence is not an error: no reference resolves to None.
    assert resolve_equipment_context(experiment, {}) is None

    dangling = "fixture-source::context::absent_equipment_row"
    ghost = replace(experiment, equipment_context_id=dangling)
    with pytest.raises(UnresolvedEquipmentContextError, match=re.escape(dangling)):
        resolve_equipment_context(ghost, {})
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={}
    )
    issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert dangling in issues[0].detail
    # A store carrying the row resolves clean.
    row = {"context_id": dangling, "equipment": {"cell_material": "alumina"}}
    assert resolve_equipment_context(ghost, {dangling: row}) is row
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={dangling: row}
    )
    assert not any(
        issue.path.endswith(".equipment_context_id") for issue in report.issues
    )


def test_species_rail_janaf_token_migrates_under_ledger_label(tmp_path: Path) -> None:
    """The species-rail ledger's ``source_id: janaf`` names the compilation
    compared against, not a battery work; the migrated work is the ledger
    itself, labelled ``species-rail-differential``."""
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "janaf::Mg-001:T=1363::nasa_cea_9::delta_fG_kJ_mol",
                        "source_id": "janaf",
                        "observation_id": "Mg-001",
                        "species": "Mg",
                        "comparison_quantity": "delta_fG_kJ_mol",
                        "temperature_K": 1363,
                        "table_kJ_mol": -500.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    ledger_works = [
        work for work in result.works.values() if "species-rail-differential" in work.source_ids
    ]
    assert len(ledger_works) == 1
    work = ledger_works[0]
    assert work.source_ids == ("species-rail-differential",)
    assert work.citation == "species-rail-differential"
    assert all("janaf" not in candidate.source_ids for candidate in result.works.values())
    assert "janaf" not in result.aliases
    assert result.aliases["species-rail-differential"] == work.work_id
    observation = result.observations["janaf::Mg-001:T=1363::nasa_cea_9::delta_fG_kJ_mol"]
    assert observation.source_id == "species-rail-differential"
    experiment_ids = result.experiments_by_work[work.work_id]
    assert experiment_ids == [f"{work.work_id}::species-rail-differential"]


def test_committed_aliases_pin_species_rail_ledger_not_janaf() -> None:
    """The alias registry resolves the ledger label to the historical work id
    (keeping experiment ids stable) and no longer claims a ``janaf`` work."""
    from simulator.battery.migrate import load_aliases

    aliases = load_aliases()
    assert "janaf" not in aliases
    assert aliases["species-rail-differential"] == citation_hash("janaf")
    assert aliases["janaf-4th"] == aliases["nist-janaf-4th"]

def test_species_rail_pankratz_token_also_files_under_ledger_label(tmp_path: Path) -> None:
    """Comparison-compilation tokens other than janaf must not land ledger
    residuals on the real compilation work (USBM B689)."""
    root = _write_min_tree(tmp_path)
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": "pankratz-1987-usbm-b689::page-0003:T=298.15::nasa_cea_9::delta_fG_kJ_mol",
                        "source_id": "pankratz-1987-usbm-b689",
                        "observation_id": "page-0003",
                        "species": "MgO",
                        "comparison_quantity": "delta_fG_kJ_mol",
                        "temperature_K": 298.15,
                        "table_kJ_mol": -500.0,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    ledger_works = [
        work for work in result.works.values()
        if "species-rail-differential" in work.source_ids
    ]
    assert len(ledger_works) == 1
    work = ledger_works[0]
    assert all(
        "pankratz-1987-usbm-b689" not in candidate.source_ids
        for candidate in result.works.values()
    )
    observation = result.observations[
        "pankratz-1987-usbm-b689::page-0003:T=298.15::nasa_cea_9::delta_fG_kJ_mol"
    ]
    assert observation.source_id == "species-rail-differential"
    assert result.experiments_by_work[work.work_id] == [
        f"{work.work_id}::species-rail-differential"
    ]

# ---------------------------------------------------------------------------
# 2026-09-22 hardening (latent, none live in the corpus): cross-work
# equipment FK refusal, extracts-v2 orphan siblings on delete/rename, and a
# typed error for duplicate context ids.
# ---------------------------------------------------------------------------


def _named_extract(source_id: str, doi: str) -> dict:
    doc = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    doc["source_id"] = source_id
    doc["source"]["doi"] = doi
    doc["source"]["citation"] = f"{source_id}, A. (2026), J 1:1, DOI {doi}"
    return doc


def _write_multi_extract_tree(root: Path, extracts: list[tuple[str, dict]]) -> Path:
    """_write_min_tree with several named extracts, each declared in INDEX."""
    _write_min_tree(root, extracts[0][1])
    extracts_dir = root / "data" / "literature" / "extracts"
    (extracts_dir / "fixture-source.yaml").unlink()
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    template = index["sources"].pop(0)
    index["sources"] = []
    for stem, doc in extracts:
        (extracts_dir / f"{stem}.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
        )
        entry = yaml.safe_load(yaml.safe_dump(template))
        entry["source_id"] = doc["source_id"]
        entry["doi"] = doc["source"]["doi"]
        entry["citation"] = doc["source"]["citation"]
        index["sources"].append(entry)
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    return root


def test_stale_doi_alias_is_reassigned_in_one_pass_and_then_idempotent(
    tmp_path: Path,
) -> None:
    doi = "10.1234/shared"
    owner = _named_extract("aaa-owner", doi)
    survivor = _named_extract("bbb-survivor", doi)
    root = _write_multi_extract_tree(
        tmp_path, [("aaa-owner", owner), ("bbb-survivor", survivor)]
    )
    works_dir = root / "data" / "literature" / "works"
    aliases_path = works_dir / "ALIASES.yaml"
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": {
                "aaa-owner": "owner-work",
                "bbb-survivor": "survivor-work",
            },
        },
        aliases_path,
    )

    first = migrate(root, write=True)
    owner_work = next(
        work for work in first.works.values() if "aaa-owner" in work.source_ids
    )
    survivor_work = next(
        work for work in first.works.values() if "bbb-survivor" in work.source_ids
    )
    assert owner_work.work_id != survivor_work.work_id
    assert first.aliases[doi] == owner_work.work_id

    aliases_doc = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
    aliases_doc["aliases"]["manual-fixture-alias"] = survivor_work.work_id
    aliases_path.write_text(
        yaml.safe_dump(aliases_doc, sort_keys=False), encoding="utf-8"
    )
    (root / "data" / "literature" / "extracts" / "aaa-owner.yaml").unlink()
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index_doc = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index_doc["sources"] = [
        row for row in index_doc["sources"] if row["source_id"] != "aaa-owner"
    ]
    index_path.write_text(
        yaml.safe_dump(index_doc, sort_keys=False), encoding="utf-8"
    )

    second = migrate(root, write=True)
    surviving_work_id = next(
        work.work_id
        for work in second.works.values()
        if "bbb-survivor" in work.source_ids
    )
    second_aliases = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))[
        "aliases"
    ]
    assert second_aliases[doi] == surviving_work_id
    assert second_aliases["manual-fixture-alias"] == surviving_work_id

    output_dirs = (
        works_dir,
        root / "data" / "literature" / "extracts-v2",
        root / "data" / "literature" / "observations-v2",
        root / "data" / "battery",
    )

    def output_bytes() -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for directory in output_dirs
            for path in directory.rglob("*")
            if path.is_file()
        }

    second_bytes = output_bytes()
    migrate(root, write=True)
    assert output_bytes() == second_bytes


def test_live_target_doi_alias_is_preserved_for_later_sources(tmp_path: Path) -> None:
    doi = "10.1234/shared"
    extracts = [
        ("a", _named_extract("a", doi)),
        ("b", _named_extract("b", doi)),
        ("c", _named_extract("c", "10.1234/primary")),
    ]
    root = _write_multi_extract_tree(tmp_path, extracts)
    aliases_path = root / "data" / "literature" / "works" / "ALIASES.yaml"
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": {"a": "A", "b": "B", "c": "C", doi: "C"},
        },
        aliases_path,
    )

    result = migrate(root, write=True)
    assert set(result.works) == {"A", "B", "C"}
    assert result.aliases[doi] == "C"
    resolved, _ = work_id_for(
        citation="New source sharing historical DOI",
        doi=doi,
        source_id="new-source",
        aliases=result.aliases,
    )
    assert resolved == "C"


def test_late_live_extract_keeps_carried_doi_alias(tmp_path: Path) -> None:
    doi = "10.1234/shared"
    extracts = [
        ("a", _named_extract("a", doi)),
        ("b", _named_extract("b", doi)),
        ("c", _named_extract("c", "10.1234/primary")),
    ]
    root = _write_multi_extract_tree(tmp_path, extracts)
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index_doc = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index_doc["sources"] = [
        row for row in index_doc["sources"] if row["source_id"] != "c"
    ]
    index_path.write_text(yaml.safe_dump(index_doc, sort_keys=False), encoding="utf-8")
    aliases_path = root / "data" / "literature" / "works" / "ALIASES.yaml"
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": {"a": "A", "b": "B", "c": "C", doi: "C"},
        },
        aliases_path,
    )

    result = migrate(root, write=False)

    assert "C" in result.works
    assert result.aliases[doi] == "C"


def test_late_compilation_work_keeps_carried_doi_alias(tmp_path: Path) -> None:
    doi = "10.1234/shared"
    root = _write_multi_extract_tree(
        tmp_path, [("a", _named_extract("a", "10.1234/primary"))]
    )
    manifest = (
        root
        / "data"
        / "literature"
        / "compilations"
        / "fixture"
        / "manifest.yaml"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        yaml.safe_dump(
            {
                "source_id": "compilation-source",
                "source": {"citation": "Compilation fixture"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    aliases_path = root / "data" / "literature" / "works" / "ALIASES.yaml"
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": {"a": "A", "compilation-source": "COMP", doi: "COMP"},
        },
        aliases_path,
    )

    result = migrate(root, write=False)

    assert "COMP" in result.works
    assert result.aliases[doi] == "COMP"


def test_deleted_index_only_survivor_reclaims_doi_in_first_pass(tmp_path: Path) -> None:
    doi = "10.1234/shared"
    root = _write_multi_extract_tree(
        tmp_path, [("bbb-survivor", _named_extract("bbb-survivor", doi))]
    )
    (root / "data" / "literature" / "extracts" / "bbb-survivor.yaml").unlink()
    aliases_path = root / "data" / "literature" / "works" / "ALIASES.yaml"
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": {
                "aaa-deleted-owner": "owner-work",
                "bbb-survivor": "survivor-work",
                doi: "owner-work",
            },
        },
        aliases_path,
    )

    result = migrate(root, write=False)

    assert set(result.works) == {"survivor-work"}
    assert result.aliases[doi] == "survivor-work"


def test_shared_doi_claimant_is_independent_of_index_order(tmp_path: Path) -> None:
    doi = "10.1234/shared"
    for carried_doi_owner in (None, "deleted-owner"):
        claimants = []
        for order in (("a", "b"), ("b", "a")):
            root = _write_multi_extract_tree(
                tmp_path / f"{carried_doi_owner}-{''.join(order)}",
                [(source_id, _named_extract(source_id, doi)) for source_id in order],
            )
            aliases_path = root / "data" / "literature" / "works" / "ALIASES.yaml"
            aliases = {"a": "A", "b": "B"}
            if carried_doi_owner is not None:
                aliases[doi] = carried_doi_owner
            dump_yaml(
                {"schema_version": "battery_work_aliases.v1", "aliases": aliases},
                aliases_path,
            )

            result = migrate(root, write=False)

            claimants.append(result.aliases[doi])

        assert claimants == ["A", "A"]


def _equipment_context_row(observation_id: str, experiment: object) -> dict:
    return {
        "observation_id": observation_id,
        "experiment": experiment,
        "type": "apparatus",
        "locator": {"page": 3, "section": "experimental"},
        "units": "as printed",
        "equipment": {"cell_material": "alumina"},
        "values": {"quantity": "apparatus_note", "method_class": "method_only"},
    }


def test_equipment_fk_refuses_a_cross_work_context_row(tmp_path: Path) -> None:
    """An equipment-bearing context row naming another work's experiment
    through a qualified ``::`` id must not set that experiment's FK. The
    registry records a typed referential_integrity refusal instead."""
    victim = _named_extract("aaa-victim", "10.1234/VICTIM")
    victim["experiments"] = [
        {
            "experiment_id": "vic-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    root = _write_multi_extract_tree(tmp_path, [("aaa-victim", victim)])
    victim_experiment_id = next(
        eid
        for eid in migrate(root, write=False).experiments
        if eid.endswith("::experiment::vic-series")
    )
    attacker = _named_extract("zzz-attacker", "10.1234/ATTACK")
    attacker["species"]["Na"]["context"] = [
        _equipment_context_row("evil_eq", victim_experiment_id)
    ]
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-victim", victim), ("zzz-attacker", attacker)]
    )
    result = migrate(root, write=True)
    experiment = result.experiments[victim_experiment_id]
    assert experiment.equipment_context_id is None
    issues = [
        issue
        for issue in result.registry_issues
        if issue.path == "context[zzz-attacker::context::evil_eq].experiment"
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert victim_experiment_id in issues[0].detail
    assert experiment.work_id in issues[0].detail
    # No FK means nothing resolves to the foreign row.
    assert resolve_equipment_context(experiment, load_migrated_context(root)) is None


def test_equipment_fk_allows_a_same_work_qualified_reference(tmp_path: Path) -> None:
    """The cross-work refusal must not over-fire: two extracts aliasing one
    work (same DOI) may share an experiment through its qualified id."""
    first = _named_extract("aaa-first", "10.1234/SHARED")
    first["experiments"] = [
        {
            "experiment_id": "shared-series",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    root = _write_multi_extract_tree(tmp_path, [("aaa-first", first)])
    experiment_id = next(
        eid
        for eid in migrate(root, write=False).experiments
        if eid.endswith("::experiment::shared-series")
    )
    second = _named_extract("bbb-second", "10.1234/SHARED")
    second["species"]["Na"]["context"] = [
        _equipment_context_row("shared_apparatus", experiment_id)
    ]
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-first", first), ("bbb-second", second)]
    )
    result = migrate(root, write=True)
    experiment = result.experiments[experiment_id]
    assert experiment.equipment_context_id == "bbb-second::context::shared_apparatus"
    row = resolve_equipment_context(experiment, load_migrated_context(root))
    assert row["equipment"]["cell_material"] == "alumina"


def test_validate_flags_equipment_fk_work_mismatch(tmp_path: Path) -> None:
    """validate_corpus reports a stored FK whose context row belongs to a
    different work — the same invariant the migrator refuses to write."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    fk = "other-source::context::foreign_equipment"
    ghost = replace(experiment, equipment_context_id=fk)
    foreign_row = {
        "context_id": fk,
        "work_id": "10.9999/OTHER",
        "equipment": {"cell_material": "alumina"},
    }
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={fk: foreign_row}
    )
    issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".equipment_context_id")
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "10.9999/OTHER" in issues[0].detail
    assert experiment.work_id in issues[0].detail
    # A same-work row stays clean.
    own_row = dict(foreign_row, work_id=experiment.work_id)
    report = validate_corpus(
        works, [ghost], observations, residuals=None, context_rows={fk: own_row}
    )
    assert not any(
        issue.path.endswith(".equipment_context_id") for issue in report.issues
    )


def test_write_outputs_unlinks_extracts_v2_orphan_on_delete(tmp_path: Path) -> None:
    """Deleting an extract must remove its extracts-v2 sibling: extracts-v2
    has no wipe-all path, so a stale sibling would re-enter the store on
    every load."""
    keep = _named_extract("aaa-keep", "10.1234/KEEP")
    gone = _named_extract("bbb-gone", "10.1234/GONE")
    root = _write_multi_extract_tree(
        tmp_path, [("aaa-keep", keep), ("bbb-gone", gone)]
    )
    migrate(root, write=True)
    extracts_v2 = root / "data" / "literature" / "extracts-v2"
    assert (extracts_v2 / "aaa-keep.yaml").is_file()
    assert (extracts_v2 / "bbb-gone.yaml").is_file()
    (root / "data" / "literature" / "extracts" / "bbb-gone.yaml").unlink()
    migrate(root, write=True)
    assert not (extracts_v2 / "bbb-gone.yaml").exists()
    assert (extracts_v2 / "aaa-keep.yaml").is_file()
    _, _, observations = load_migrated_store(root)
    assert not any(oid.startswith("bbb-gone") for oid in observations)
    assert sum(1 for oid in observations if oid.startswith("aaa-keep")) == 2


def test_write_outputs_unlinks_extracts_v2_orphan_on_rename(tmp_path: Path) -> None:
    """Renaming an extract (same DOI, new stem and source_id) must remove the
    old extracts-v2 sibling. Experiment ids are work-scoped, so a surviving
    sibling would load the same printed data twice under two source ids."""
    old = _named_extract("bbb-old", "10.1234/SAME")
    root = _write_multi_extract_tree(tmp_path, [("bbb-old", old)])
    migrate(root, write=True)
    extracts_v2 = root / "data" / "literature" / "extracts-v2"
    assert (extracts_v2 / "bbb-old.yaml").is_file()
    (root / "data" / "literature" / "extracts" / "bbb-old.yaml").unlink()
    new = _named_extract("ccc-new", "10.1234/SAME")
    (root / "data" / "literature" / "extracts" / "ccc-new.yaml").write_text(
        yaml.safe_dump(new, sort_keys=False), encoding="utf-8"
    )
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    for source in index["sources"]:
        if source["source_id"] == "bbb-old":
            source["source_id"] = "ccc-new"
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    migrate(root, write=True)
    assert not (extracts_v2 / "bbb-old.yaml").exists()
    assert (extracts_v2 / "ccc-new.yaml").is_file()
    _, experiments, observations = load_migrated_store(root)
    old_obs = [oid for oid in observations if oid.startswith("bbb-old")]
    new_obs = [oid for oid in observations if oid.startswith("ccc-new")]
    assert old_obs == []
    assert len(new_obs) == 2
    # No double count: each loaded row resolves to a live experiment.
    assert all(observations[oid].experiment_id in experiments for oid in new_obs)


def test_duplicate_context_id_raises_typed(tmp_path: Path) -> None:
    """Two context rows under one work reusing an observation_id abort the
    run with a typed DuplicateContextIdError — never a silent last-wins row
    loss that could point an equipment FK at the wrong row."""
    extract = _named_extract("dup-ctx", "10.1234/DUP")
    extract["experiments"] = [
        {
            "experiment_id": "s",
            "method": "knudsen_effusion",
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    extract["species"]["Na"]["context"] = [_equipment_context_row("same_id", "s")]
    extract["species"]["K"] = {
        "context": [
            {
                "observation_id": "same_id",
                "type": "characterization",
                "locator": {"page": 4, "section": "experimental"},
                "units": "as printed",
                "values": {"quantity": "sample_note", "method_class": "method_only"},
            }
        ]
    }
    root = _write_multi_extract_tree(tmp_path, [("dup-ctx", extract)])
    with pytest.raises(DuplicateContextIdError, match="dup-ctx::context::same_id"):
        migrate(root, write=False)


def _bench_experiment_extract(source_id: str, doi: str, bench: str, experiment: str) -> dict:
    doc = _named_extract(source_id, doi)
    doc["benches"] = [
        {"id": bench, "identity": {"basis": "described_in_this_work"}}
    ]
    doc["experiments"] = [
        {
            "experiment_id": experiment,
            "method": "knudsen_effusion",
            "bench_id": bench,
            "locator": {"page": 2, "section": "experimental"},
        }
    ]
    return doc


def test_cross_work_bench_and_experiment_ids_are_refused(tmp_path: Path) -> None:
    """A qualified bench or experiment id from another work is not a link.

    The registry records referential_integrity and writes neither the bench
    entry nor the foreign key. The victim records stay owned by the victim.
    """
    victim = _bench_experiment_extract(
        "aaa-victim", "10.1234/VICTIM", "vic-bench", "vic-series"
    )
    root = _write_multi_extract_tree(tmp_path, [("aaa-victim", victim)])
    lifted = migrate(root, write=False)
    victim_bench_id = next(bid for bid in lifted.benches if bid.endswith("::bench::vic-bench"))
    victim_experiment_id = next(
        eid for eid in lifted.experiments if eid.endswith("::experiment::vic-series")
    )
    stolen = "10.1234/victim::bench::stolen"
    attacker = _named_extract("zzz-attacker", "10.1234/ATTACK")
    attacker["benches"] = [
        {"id": stolen, "identity": {"basis": "described_in_this_work"}}
    ]
    attacker["experiments"] = [
        {
            "experiment_id": victim_experiment_id,
            "method": "knudsen_effusion",
            "locator": {"page": 1, "section": "experimental"},
        },
        {
            "experiment_id": "atk",
            "method": "knudsen_effusion",
            "bench_id": victim_bench_id,
            "locator": {"page": 2, "section": "experimental"},
        },
    ]
    attacker["species"]["Na"]["observations"][0]["experiment"] = victim_experiment_id
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-victim", victim), ("zzz-attacker", attacker)]
    )
    result = migrate(root, write=False)
    assert stolen not in result.benches
    assert result.benches[victim_bench_id].work_id != next(
        exp.work_id
        for eid, exp in result.experiments.items()
        if eid.endswith("::experiment::atk")
    )
    attacker_experiment = next(
        exp for eid, exp in result.experiments.items() if eid.endswith("::experiment::atk")
    )
    assert attacker_experiment.bench_id is None
    assert victim_experiment_id not in result.experiments_by_work[attacker_experiment.work_id]
    attacker_obs = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "zzz-attacker"
    ]
    assert attacker_obs
    assert all(obs.experiment_id != victim_experiment_id for obs in attacker_obs)
    issues = [
        issue
        for issue in result.registry_issues
        if issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and "outside work" in issue.detail
    ]
    paths = {issue.path for issue in issues}
    assert f"bench[{stolen}]" in paths
    assert any(path.endswith(".bench_id") and "atk" in path for path in paths)
    assert any(path.startswith("observation[zzz-attacker::") and path.endswith(".experiment") for path in paths)
    assert any(path == f"experiment[{victim_experiment_id}]" for path in paths)
    assert all(victim_bench_id in issue.detail or stolen in issue.detail or victim_experiment_id in issue.detail for issue in issues)


def test_same_work_qualified_bench_and_experiment_resolve(tmp_path: Path) -> None:
    """Two extracts of one work may name that work's bench and experiment
    by the qualified id. The cross-work refusal must not fire."""
    first = _bench_experiment_extract(
        "aaa-first", "10.1234/SHARED", "shared-bench", "shared-series"
    )
    root = _write_multi_extract_tree(tmp_path, [("aaa-first", first)])
    lifted = migrate(root, write=False)
    bench_id = next(bid for bid in lifted.benches if bid.endswith("::bench::shared-bench"))
    experiment_id = next(
        eid for eid in lifted.experiments if eid.endswith("::experiment::shared-series")
    )
    second = _named_extract("bbb-second", "10.1234/SHARED")
    second["experiments"] = [
        {
            "experiment_id": "second-series",
            "method": "knudsen_effusion",
            "bench_id": bench_id,
            "locator": {"page": 3, "section": "experimental"},
        }
    ]
    second["species"]["Na"]["observations"][0]["experiment"] = experiment_id
    root = _write_multi_extract_tree(
        tmp_path / "run", [("aaa-first", first), ("bbb-second", second)]
    )
    result = migrate(root, write=False)
    second_experiment = next(
        exp for eid, exp in result.experiments.items() if eid.endswith("::experiment::second-series")
    )
    assert second_experiment.bench_id == bench_id
    assert result.benches[bench_id].work_id == second_experiment.work_id
    second_obs = [
        obs for obs in result.observations.values() if obs.source_id == "bbb-second"
    ]
    assert second_obs
    assert all(obs.experiment_id == experiment_id for obs in second_obs)
    assert not any("outside work" in issue.detail for issue in result.registry_issues)


def test_validate_flags_cross_work_bench_and_observation(tmp_path: Path) -> None:
    """A hand-edited store whose bench or experiment belongs to another work
    is the same referential_integrity failure the migrator refuses to write."""
    root = _write_min_tree(tmp_path)
    migrate(root, write=True)
    works, experiments, observations = load_migrated_store(root)
    experiment = next(iter(experiments.values()))
    related = [
        obs for obs in observations.values() if obs.experiment_id == experiment.experiment_id
    ]
    obs = related[0]
    foreign_bench = Bench(
        id="10.9999/OTHER::bench::foreign",
        work_id="10.9999/OTHER",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )
    mismatched = replace(experiment, bench_id=foreign_bench.id)
    report = validate_corpus(
        works,
        [mismatched],
        related,
        benches={foreign_bench.id: foreign_bench},
    )
    bench_issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".bench_id") and "belongs to work" in issue.detail
    ]
    assert len(bench_issues) == 1
    assert bench_issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert "10.9999/OTHER" in bench_issues[0].detail
    assert experiment.work_id in bench_issues[0].detail
    own_bench = replace(foreign_bench, id="own-bench", work_id=experiment.work_id)
    report = validate_corpus(
        works,
        [replace(experiment, bench_id=own_bench.id)],
        related,
        benches={own_bench.id: own_bench},
    )
    assert not any(
        issue.path.endswith(".bench_id") and "belongs to work" in issue.detail
        for issue in report.issues
    )

    own = next(work for work in works.values() if obs.source_id in work.source_ids)
    foreign_work = replace(
        own, work_id="10.9999/OTHER", source_ids=("other-source",), doi="10.9999/OTHER"
    )
    foreign_exp = replace(
        experiment,
        experiment_id="10.9999/OTHER::experiment::foreign",
        work_id=foreign_work.work_id,
    )
    ghost = replace(obs, experiment_id=foreign_exp.experiment_id)
    report = validate_corpus(
        [own, foreign_work], [experiment, foreign_exp], [ghost]
    )
    obs_issues = [
        issue
        for issue in report.hard_issues
        if issue.path.endswith(".experiment_id") and "belongs to work" in issue.detail
    ]
    assert len(obs_issues) == 1
    assert obs_issues[0].reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert foreign_work.work_id in obs_issues[0].detail
    assert own.work_id in obs_issues[0].detail
    report = validate_corpus(works, experiments, observations)
    assert not any("belongs to work" in issue.detail for issue in report.issues)
def test_g4_compilation_record_path_predicate() -> None:
    assert is_compilation_record_path(
        "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
        "records/robie-hemingway-fisher-1978-usgs-b1452-0003.json"
    )
    assert not is_compilation_record_path(
        "data/literature/compilations/janaf/tables/Al-001.yaml"
    )
    assert not is_compilation_record_path("ocr/missing.md")
    assert not is_compilation_record_path("raw/source/file.pdf")


def test_g4_compilation_record_registers_index_asset(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0003.json",
    )
    result = migrate(root, write=False)
    rel = dest.relative_to(root).as_posix()
    asset_id = compilation_record_asset_id(rel)
    obs_list = [
        obs
        for oid, obs in result.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    ]
    assert obs_list
    assert all(obs.read_from == asset_id for obs in obs_list)
    assert all(obs.locator.source_path == rel for obs in obs_list)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in result.queue
    )
    works = [
        work
        for work in result.works.values()
        if "robie-hemingway-fisher-1978-usgs-b1452" in work.source_ids
    ]
    assert len(works) == 1
    assets = {f.asset_id: f for f in works[0].source_files.files}
    assert asset_id in assets
    assert assets[asset_id].role is AssetRole.COMPILATION_RECORD
    assert assets[asset_id].path == rel
    assert all(obs.read_from in assets for obs in obs_list)


def test_g4_compilation_record_asset_mutation_proof(tmp_path: Path) -> None:
    from simulator.battery import migrate as migrate_mod

    root = _write_min_tree(tmp_path)
    dest = _copy_compilation_record(
        root,
        "robie-hemingway-fisher-1978-usgs-b1452",
        "robie-hemingway-fisher-1978-usgs-b1452-0003.json",
    )
    rel = dest.relative_to(root).as_posix()

    live = migrate(root, write=False)
    live_obs = next(
        obs
        for oid, obs in live.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    )
    assert live_obs.read_from == compilation_record_asset_id(rel)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in live.queue
    )

    saved = migrate_mod.is_compilation_record_path
    migrate_mod.is_compilation_record_path = lambda _path: False  # type: ignore[assignment]
    try:
        mutant = migrate(root, write=False)
        assert any(
            "has no matching INDEX asset" in (entry.why or "") for entry in mutant.queue
        )
        mutant_obs = next(
            obs
            for oid, obs in mutant.observations.items()
            if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
        )
        assert str(mutant_obs.read_from).startswith("unknown")
    finally:
        migrate_mod.is_compilation_record_path = saved

    restored = migrate(root, write=False)
    restored_obs = next(
        obs
        for oid, obs in restored.observations.items()
        if "robie-hemingway-fisher-1978-usgs-b1452-0003" in oid
    )
    assert restored_obs.read_from == compilation_record_asset_id(rel)
    assert not any(
        "has no matching INDEX asset" in (entry.why or "") for entry in restored.queue
    )


def test_g4_choose_read_from_compilation_does_not_fall_through_to_pdf() -> None:
    from simulator.battery.records import Locator, SourceFile, SourceFiles, Work, State

    work = Work(
        work_id="w",
        citation="c",
        source_ids=("robie-hemingway-fisher-1978-usgs-b1452",),
        source_files=SourceFiles(
            corpus_repo="regolith-corpus",
            corpus_commit=State.unknown("test"),
            files=(
                SourceFile(
                    asset_id="pdf:robie-hemingway-fisher-1978-usgs-b1452",
                    role=AssetRole.PDF,
                    path="raw/robie-hemingway-fisher-1978-usgs-b1452/file.pdf",
                    sha256=State.unknown("test"),
                ),
                SourceFile(
                    asset_id="unknown:robie-hemingway-fisher-1978-usgs-b1452",
                    role=AssetRole.PDFTOTEXT,
                    path="unknown",
                    sha256=State.unknown("test"),
                ),
            ),
        ),
    )
    locator = Locator(
        source_path=(
            "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
            "records/robie-hemingway-fisher-1978-usgs-b1452-0003.json"
        )
    )
    read_from = choose_read_from(work, locator)
    assert str(read_from).startswith("unknown")
    assert not str(read_from).startswith("pdf:")


def test_residue_component_quantity_aliases_keep_printed_values_and_missing_cells() -> None:
    from decimal import Decimal

    from simulator.battery.enums import QUANTITY_UNITS, Quantity
    from simulator.battery.migrate import map_quantity, select_declared_source

    cases = (
        (
            "open_furnace_residue_composition_vs_time",
            "residue_ppm",
            "ppm by mass in quenched glass",
            "759.6",
        ),
        (
            "residue_composition_vs_time",
            "Al2O3_wt_pct",
            "wt % (100% normalized) as published",
            "3.57",
        ),
        (
            "epma_residue_composition_vs_time",
            "K2O_wt_pct",
            "wt_percent",
            "0.28",
        ),
    )
    assert QUANTITY_UNITS[Quantity.RESIDUE_COMPONENT_COMPOSITION] == "subtype_defined"
    for legacy_quantity, field, units, printed_value in cases:
        payload = {"quantity": legacy_quantity, field: printed_value}
        quantity, reason = map_quantity("rate_series", payload, units=units)
        assert quantity.is_value and quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        assert reason is None

        selected = select_declared_source(quantity.value, units, payload)
        assert selected.available
        assert selected.field_name == field
        assert selected.amount == Decimal(printed_value)

        missing = select_declared_source(quantity.value, units, {field: None})
        assert not missing.available
        assert "null" in (missing.reason or "")


def test_hashimoto_row_left_live_diagnostics_are_final(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-015-hashimoto-1983.yaml")
    left_live = [
        entry
        for entry in result.queue
        if entry.why and "row left live" in entry.why
    ]
    contradicted = [
        entry
        for entry in left_live
        if entry.observation_id in result.observations
        and result.observations[entry.observation_id].admission.status
        is AdmissionStatus.SUPERSEDED
    ]
    assert contradicted == []
    assert len(left_live) == 0


def test_residue_store_write_files_cells_under_their_extract_sources(
    tmp_path: Path,
) -> None:
    expected_counts = {
        "kems-012-sossi-2019": 344,
        "kems-015-hashimoto-1983": 120,
    }
    for source_id, expected_count in expected_counts.items():
        _migrate_real_extract(
            tmp_path / source_id,
            f"{source_id}.yaml",
            write=True,
        )
        store = tmp_path / source_id / "tree" / "data" / "literature"
        source_file = store / "extracts-v2" / f"{source_id}.yaml"
        payload = yaml.safe_load(source_file.read_text(encoding="utf-8"))
        cells = [
            row
            for row in payload["observations"]
            if row["source_id"] == source_id
            and row["identity"]["quantity"].get("value")
            == "residue_component_composition"
            and row["value"].get("point") is not None
            and row["admission"]["status"] != "superseded"
        ]

        assert len(cells) == expected_count
        assert all(row["source_id"] == source_id for row in cells)
        assert not (store / "observations-v2" / "CO2_sccm.yaml").exists()


def test_residue_battery_ready_counts_one_cell_per_printed_component(
    tmp_path: Path,
) -> None:
    from collections import Counter

    from simulator.battery.enums import AdmissionStatus, Phase, Quantity
    from simulator.battery.score import ScoreContext, comparison_candidates

    expected = {
        "kems-012-sossi-2019": {
            "Mn": 43,
            "Ti": 43,
            "Sc": 43,
            "V": 43,
            "Zr": 43,
            "La": 43,
            "Gd": 43,
            "Yb": 43,
        },
        "kems-015-hashimoto-1983": {
            "SiO2": 24,
            "Al2O3": 24,
            "FeO": 24,
            "MgO": 24,
            "CaO": 24,
        },
    }
    for source_id, component_counts in expected.items():
        result = _migrate_real_extract(tmp_path / source_id, f"{source_id}.yaml")
        live = [
            obs
            for obs in result.observations.values()
            if obs.source_id == source_id
            and obs.identity.quantity.is_value
            and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and obs.value.point is not None
            and obs.admission.status is not AdmissionStatus.SUPERSEDED
        ]
        assert Counter(obs.identity.species.formula for obs in live) == component_counts

        context = ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
        )
        ready = [
            obs
            for obs in comparison_candidates(context)
            if obs.source_id == source_id
            and obs.identity.quantity.is_value
            and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        ]
        assert Counter(obs.identity.species.formula for obs in ready) == component_counts
        assert len(ready) == sum(component_counts.values())


def test_residue_cells_keep_run_identity_and_printed_conditions(
    tmp_path: Path,
) -> None:
    from collections import Counter
    from decimal import Decimal

    from simulator.battery.enums import AdmissionStatus, Phase, Quantity
    from simulator.battery.identity import profile_for
    from simulator.battery.migrate import wt_pct_to_mole_fraction

    sossi_id = "kems-012-sossi-2019"
    sossi = _migrate_real_extract(tmp_path / "sossi", f"{sossi_id}.yaml")
    sossi_source = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / f"{sossi_id}.yaml")
        .read_text(encoding="utf-8")
    )
    expected_sossi = Counter()
    expected_sossi_conditions = {}
    for formula, species in sossi_source["species"].items():
        for source_obs in species.get("observations", []):
            if not source_obs.get("observation_id", "").endswith("_quoted_20260906"):
                continue
            values = source_obs.get("values", {})
            series = values.get("series") if isinstance(values, dict) else None
            if not isinstance(series, list):
                continue
            for item in series:
                if not isinstance(item, dict) or item.get("residue_ppm") is None:
                    continue
                run_id = str(item["run_id"]).strip().removesuffix("*").strip()
                run_id = run_id.casefold().replace("/", "-")
                temperature = (
                    Decimal(str(item["T_K"]))
                    if item.get("T_K") is not None
                    else Decimal(str(item["T_C"])) + Decimal("273.15")
                )
                time_min = Decimal(str(item["t_min"]))
                run_key = (formula, temperature, time_min, f"sossi-2019-run-{run_id}")
                expected_sossi[run_key] += 1
                expected_sossi_conditions[run_key] = (
                    Decimal(str(item["log10_fO2"])),
                    Decimal(str(values["starting_measured_ppm"])),
                )

    sossi_rows = [
        obs
        for obs in sossi.observations.values()
        if obs.source_id == sossi_id
        and obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and obs.value.point is not None
        and obs.admission.status is not AdmissionStatus.SUPERSEDED
    ]
    actual_sossi = Counter()
    for obs in sossi_rows:
        assert obs.identity.species.phase.is_value
        assert obs.identity.species.phase.value is Phase.GLASS
        conditions = obs.point_conditions or {}
        actual_sossi[
            (
                obs.identity.species.formula,
                conditions["temperature_K"].state.value,
                conditions["time_min"].state.value,
                obs.experiment_id.split("::experiment::")[-1],
            )
        ] += 1
        run_key = (
            obs.identity.species.formula,
            conditions["temperature_K"].state.value,
            conditions["time_min"].state.value,
            obs.experiment_id.split("::experiment::")[-1],
        )
        expected_fO2, expected_starting = expected_sossi_conditions[run_key]
        assert conditions["fO2_log"].state.value == expected_fO2
        assert conditions["starting_component_ppm"].state.value == expected_starting
        expected_pressure = sossi.experiments[obs.experiment_id].pressure_environment.total_pressure_Pa
        assert conditions["total_pressure_Pa"].state == expected_pressure.state
        identity = obs.identity
        source_experiment = next(
            row
            for row in sossi_source["experiments"]
            if row["experiment_id"].endswith(run_key[3])
        )
        expected_start_composition = {
            oxide: Decimal(str(amount))
            for oxide, amount in source_experiment["sample"]["printed_composition"]["state"]["value"].items()
        }
        assert identity.composition.value.amount_basis.value == "mass_percent"
        assert identity.composition.value.basis == "printed_oxides"
        assert dict(identity.composition.value.components) == expected_start_composition
        assert identity.temperature_K.value == run_key[1]
        assert identity.sample_mass_kg.value == Decimal(
            source_experiment["sample"]["mass_kg"]["state"]["value"]["point"]
        )
        assert identity.exposure.value.duration_s.value == run_key[2] * Decimal("60")
        assert identity.total_pressure_Pa.value == Decimal("101325")
        # Golden pin for the existing printed-log-to-Pa conversion before it
        # is shared with redox identity binding.  -0.68 log10(bar) is the
        # published value for the first Sossi 2019 table-2 residue row.
        if expected_fO2 == Decimal("-0.68"):
            assert identity.fO2_Pa.value == Decimal("20892.96130854039483122233736")
        else:
            assert identity.fO2_Pa.value == Decimal("100000") * (
                Decimal(10) ** expected_fO2
            )
        assert "experiment_id" not in profile_for(identity).required
        assert obs.experiment_id.endswith(f"::experiment::{run_key[3]}")
        assert profile_for(identity).required == frozenset(
            {
                "temperature_K",
                "subtype",
                "composition",
                "exposure",
                "total_pressure_Pa",
                "sample_mass_kg",
                "fO2_Pa",
            }
        )
        for key in (
            "temperature_K",
            "time_min",
            "fO2_log",
            "starting_component_ppm",
            "total_pressure_Pa",
            "atmosphere",
        ):
            assert key in conditions
            assert conditions[key].locator is not None
        atmosphere = conditions["atmosphere"]
        assert atmosphere.state.is_unknown
        assert "CO" in atmosphere.locator.note and "CO2" in atmosphere.locator.note
    assert actual_sossi == expected_sossi
    assert len(sossi_rows) == 344

    hashimoto_id = "kems-015-hashimoto-1983"
    hashimoto = _migrate_real_extract(
        tmp_path / "hashimoto", f"{hashimoto_id}.yaml"
    )
    _assert_superseded_targets_are_live(hashimoto)
    for row_label, run_id in (("c3-(2)", "17c3-2"), ("c5-(1)", "17c5-1")):
        old = next(
            obs
            for obs in hashimoto.observations.values()
            if obs.observation_id.startswith(
                f"{hashimoto_id}::hashimoto_1983_table3_cao_residue_series::T=1973.15:row={row_label}:"
            )
        )
        target = hashimoto.observations[old.admission.superseded_by]
        assert target.observation_id.startswith(
            f"{hashimoto_id}::hashimoto_1983_table3_residue_composition_series_quoted__run_{run_id}::T=1973.15:row={row_label}:"
        )
        assert target.admission.status in {
            AdmissionStatus.ADMITTED, AdmissionStatus.PENDING
        }
    hashimoto_source = _extract_observations(f"{hashimoto_id}.yaml")
    run_sources = {}
    for source_obs in hashimoto_source:
        source_id = source_obs.get("observation_id", "")
        run_id = source_obs.get("experiment")
        if not source_id.startswith(
            "hashimoto_1983_table3_residue_composition_series_quoted__run_"
        ) or not isinstance(run_id, str):
            continue
        values = source_obs.get("values", {})
        series = values.get("series") if isinstance(values, dict) else None
        if isinstance(series, list) and series:
            run_sources[run_id] = (values, series[0])

    hashimoto_rows = [
        obs
        for obs in hashimoto.observations.values()
        if obs.source_id == hashimoto_id
        and obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and obs.value.point is not None
        and obs.admission.status is not AdmissionStatus.SUPERSEDED
    ]
    expected_hashimoto = Counter()
    for run_id, (values, item) in run_sources.items():
        temperature = Decimal(str(item["T_K"]))
        time_min = Decimal(str(item["t_min"]))
        for field in ("SiO2_wt_pct", "Al2O3_wt_pct", "FeO_wt_pct", "MgO_wt_pct", "CaO_wt_pct"):
            if item.get(field) is not None:
                expected_hashimoto[
                    (field.removesuffix("_wt_pct"), run_id, temperature, time_min)
                ] += 1
    actual_hashimoto = Counter()
    for obs in hashimoto_rows:
        assert obs.identity.species.phase.is_value
        assert obs.identity.species.phase.value is Phase.L
        conditions = obs.point_conditions or {}
        run_id = obs.experiment_id.split("::experiment::")[-1]
        actual_hashimoto[
            (
                obs.identity.species.formula,
                run_id,
                conditions["temperature_K"].state.value,
                conditions["time_min"].state.value,
            )
        ] += 1
        values, item = run_sources[run_id]
        for key in (
            "starting_composition",
            "fO2_control",
            "pressure_on_throw_Torr",
            "total_pressure_Pa",
            "atmosphere",
        ):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
        starting = values["composition_wt_pct"]
        assert conditions["starting_composition"].state.value == {
            oxide: format(Decimal(str(amount)), "f")
            for oxide, amount in starting.items()
            if oxide != "locator"
        }
        fO2 = values["fO2_control"]
        assert conditions["fO2_control"].state.value == fO2["during_run"]
        assert conditions["atmosphere"].state.value == fO2["note"]
        expected_pressure = hashimoto.experiments[obs.experiment_id].pressure_environment.total_pressure_Pa
        assert conditions["total_pressure_Pa"].state == expected_pressure.state
        identity = obs.identity
        experiment = hashimoto.experiments[obs.experiment_id]
        expected_start = wt_pct_to_mole_fraction(
            {
                oxide: Decimal(str(amount))
                for oxide, amount in starting.items()
                if oxide != "locator"
            }
        )
        assert identity.composition.value.components == expected_start.components
        assert identity.temperature_K.value == conditions["temperature_K"].state.value
        assert identity.sample_mass_kg.value == Decimal("0.0001")
        assert identity.exposure.value.duration_s.value == conditions["time_min"].state.value * Decimal("60")
        assert identity.total_pressure_Pa.value == experiment.pressure_environment.total_pressure_Pa.state.value.point
        assert identity.fO2_Pa.is_not_applicable
        assert identity.fO2_Pa.reason == "oxygen_unbuffered_vacuum"
        assert run_id == obs.experiment_id.split("::experiment::")[-1]
        source_vf = item.get("VF_wt_pct")
        if source_vf is None:
            assert "VF_wt_pct" not in conditions
        else:
            assert conditions["VF_wt_pct"].state.value == Decimal(str(source_vf))
            assert conditions["VF_wt_pct"].locator == obs.locator
        pressure = values["pressure_on_throw_Torr"]
        temperature_c = item["T_C"]
        source_pressure = pressure["by_T_C"].get(temperature_c)
        if source_pressure is None:
            source_pressure = pressure["by_T_C"].get(str(temperature_c))
        assert conditions["pressure_on_throw_Torr"].state.value["value"] == format(
            Decimal(str(source_pressure)), "f"
        )
    assert actual_hashimoto == expected_hashimoto
    assert len(hashimoto_rows) == 120


def test_residue_identity_subtypes_preserve_printed_basis(tmp_path: Path) -> None:
    from simulator.battery.enums import AdmissionStatus, Quantity
    from simulator.battery.identity import profile_for

    expected_subtypes = {
        "kems-012-sossi-2019": "element_ppm_by_mass",
        "kems-015-hashimoto-1983": "oxide_wt_percent",
    }
    for source_id, expected_subtype in expected_subtypes.items():
        result = _migrate_real_extract(tmp_path / source_id, f"{source_id}.yaml")
        cells = [
            obs
            for obs in result.observations.values()
            if obs.source_id == source_id
            and obs.identity.quantity.is_value
            and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
            and obs.value.point is not None
            and obs.admission.status is not AdmissionStatus.SUPERSEDED
        ]
        assert cells
        assert all(
            obs.identity.subtype is not None
            and obs.identity.subtype.is_value
            and obs.identity.subtype.value == expected_subtype
            and "subtype" in profile_for(obs.identity).required
            for obs in cells
        )


def test_residue_series_emits_numeric_component_cells_with_component_identity(
    tmp_path: Path,
) -> None:
    from decimal import Decimal

    from simulator.battery.enums import Quantity, Rail
    from simulator.battery.score import ScoreContext, comparison_candidates, rail_for_quantity

    def rows(result, source_id: str):
        return [
            obs
            for obs in result.observations.values()
            if obs.source_id == source_id
            and obs.identity.quantity.is_value
            and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        ]

    sossi_id = "kems-012-sossi-2019"
    sossi = _migrate_real_extract(tmp_path / "sossi", f"{sossi_id}.yaml")
    sossi_rows = rows(sossi, sossi_id)
    assert len(sossi_rows) == 522
    sossi_series_rows = [
        obs
        for obs in sossi_rows
        if obs.point_conditions is not None and "time_min" in obs.point_conditions
    ]
    assert len(sossi_series_rows) == 516
    assert all(
        obs.point_conditions is not None
        and {"total_pressure_Pa", "atmosphere"} <= set(obs.point_conditions)
        and obs.point_conditions["total_pressure_Pa"].state.is_value
        and obs.point_conditions["total_pressure_Pa"].state.value.point
        == Decimal("101325")
        for obs in sossi_series_rows
    )
    sossi_context = ScoreContext(
        works=sossi.works,
        experiments=sossi.experiments,
        observations=sossi.observations,
    )
    sossi_candidates = [
        obs
        for obs in comparison_candidates(sossi_context)
        if obs.source_id == sossi_id
        and obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert sossi_candidates
    assert all(
        rail_for_quantity(obs.identity.quantity.value, species_formula=obs.identity.species.formula)
        is Rail.RESIDUE_COMPOSITION
        for obs in sossi_candidates
    )
    sossi_mn = next(
        obs
        for obs in sossi_rows
        if obs.identity.species.formula == "Mn"
        and obs.value.point == Decimal("759.6")
        and "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906" in obs.observation_id
        and obs.point_conditions is not None
        and obs.point_conditions["time_min"].state.value == Decimal("15")
        and obs.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    )
    assert sossi_mn.derivation is not None
    assert sossi_mn.derivation.output_unit == "ppm by mass in quenched glass (as published Table 2)"
    assert sossi_mn.locator is not None
    assert {"temperature_K", "time_min", "fO2_log", "starting_component_ppm"} <= set(
        sossi_mn.point_conditions or {}
    )
    assert sossi_mn.point_conditions is not None
    assert sossi_mn.point_conditions["total_pressure_Pa"].state.value.point == Decimal("101325")
    atmosphere = sossi_mn.point_conditions["atmosphere"]
    assert atmosphere.state.is_unknown
    assert atmosphere.locator is not None
    assert "CO" in atmosphere.locator.note and "CO2" in atmosphere.locator.note

    hashimoto_id = "kems-015-hashimoto-1983"
    hashimoto = _migrate_real_extract(
        tmp_path / "hashimoto", f"{hashimoto_id}.yaml"
    )
    hashimoto_rows = rows(hashimoto, hashimoto_id)
    from collections import Counter

    assert Counter(obs.identity.species.formula for obs in hashimoto_rows) == {
        # Seven series rows explicitly have null for every oxide; absence
        # stays absent rather than becoming a component observation.
        "SiO2": 144,
        "Al2O3": 144,
        "FeO": 72,
        "MgO": 144,
        "CaO": 144,
    }
    assert all(
        obs.point_conditions is not None
        and {"total_pressure_Pa", "atmosphere"} <= set(obs.point_conditions)
        and obs.point_conditions["total_pressure_Pa"].state.is_value
        for obs in hashimoto_rows
    )
    hashimoto_context = ScoreContext(
        works=hashimoto.works,
        experiments=hashimoto.experiments,
        observations=hashimoto.observations,
    )
    hashimoto_candidates = [
        obs
        for obs in comparison_candidates(hashimoto_context)
        if obs.source_id == hashimoto_id
        and obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert hashimoto_candidates
    assert all(
        rail_for_quantity(obs.identity.quantity.value, species_formula=obs.identity.species.formula)
        is Rail.RESIDUE_COMPOSITION
        for obs in hashimoto_candidates
    )
    row_cells = {
        obs.identity.species.formula: obs.value.point
        for obs in hashimoto_rows
        if obs.value.point is not None
        and "row=c3-(2)" in obs.observation_id
        and obs.point_conditions is not None
        and obs.point_conditions.get("temperature_K") is not None
        and obs.point_conditions["temperature_K"].state.value == Decimal("1973.15")
        and obs.point_conditions.get("time_min") is not None
        and obs.point_conditions["time_min"].state.value == Decimal("16.7")
    }
    assert row_cells == {
        "SiO2": Decimal("39.09"),
        "Al2O3": Decimal("3.57"),
        "FeO": Decimal("27.92"),
        "MgO": Decimal("26.32"),
        "CaO": Decimal("3.09"),
    }
    for obs in hashimoto_rows:
        assert obs.derivation is not None
        assert obs.derivation.output_unit == "wt % (100% normalized) as published"
        assert obs.locator is not None
        assert {"temperature_K", "time_min"} <= set(obs.point_conditions or {})
    hashimoto_start = next(
        obs
        for obs in hashimoto_rows
        if obs.identity.species.formula == "SiO2"
        and "row=c3-(2)" in obs.observation_id
        and obs.point_conditions is not None
        and obs.point_conditions["temperature_K"].state.value == Decimal("1973.15")
        and obs.point_conditions["time_min"].state.value == Decimal("16.7")
    )
    assert {
        "temperature_K",
        "time_min",
        "starting_composition",
        "atmosphere",
        "fO2_control",
        "pressure_on_throw_Torr",
    } <= set(hashimoto_start.point_conditions or {})
    assert hashimoto_start.point_conditions["starting_composition"].state.value == {
        "SiO2": "35.43",
        "Al2O3": "3.16",
        "FeO": "35.04",
        "MgO": "23.84",
        "CaO": "2.53",
    }
    assert hashimoto_start.point_conditions["fO2_control"].state.value == "none"
    assert "vacuum runs" in hashimoto_start.point_conditions["atmosphere"].state.value
    pressure = hashimoto_start.point_conditions["pressure_on_throw_Torr"].state.value
    assert pressure["value"] == "0.00015"
    assert pressure["units"] == "Torr"
    assert pressure["kind"] == "about_nominal_by_temperature"


def _same_work_observation_duplicate_pairs(observations: list[dict]) -> list[tuple[str, str, str]]:
    """Return equal admitted values for one work that came from separate extracts."""
    grouped: dict[tuple[object, ...], list[dict]] = {}

    def canonical(value: object) -> object:
        if isinstance(value, dict):
            if value.get("tag") == "value":
                return canonical(value.get("value"))
            return tuple(sorted((key, canonical(item)) for key, item in value.items()))
        if isinstance(value, list):
            return tuple(canonical(item) for item in value)
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return value

    for obs in observations:
        if (obs.get("admission") or {}).get("status") not in {"admitted", "pending"}:
            continue
        identity = obs.get("identity") or {}
        quantity = canonical(identity.get("quantity"))
        species = (identity.get("species") or {}).get("formula")
        temperature_state = identity.get("temperature_K") or {}
        if isinstance(temperature_state, dict) and temperature_state.get("tag") != "value":
            continue
        temperature = canonical(temperature_state)
        value = obs.get("value") or {}
        value_payload = {key: item for key, item in value.items() if key not in {"kind", "approximate"}}
        experiment_id = str(obs.get("experiment_id") or "")
        work_id = experiment_id.split("::", 1)[0]
        source_id = str(obs.get("source_id") or "")
        if not all((quantity, species, temperature is not None, value_payload, work_id, source_id)):
            continue
        grouped.setdefault(
            (work_id, quantity, species, temperature, canonical(value_payload)), []
        ).append(obs)

    pairs: list[tuple[str, str, str]] = []
    for (work_id, *_), members in grouped.items():
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if left.get("source_id") == right.get("source_id"):
                    continue
                left_id = str(left.get("observation_id") or "")
                right_id = str(right.get("observation_id") or "")
                left_parents = left.get("derived_from") or []
                right_parents = right.get("derived_from") or []
                if isinstance(left_parents, str):
                    left_parents = [left_parents]
                if isinstance(right_parents, str):
                    right_parents = [right_parents]
                if left_id in right_parents or right_id in left_parents:
                    continue
                pairs.append((work_id, left_id, right_id))
    return sorted(pairs)


def test_literature_store_has_no_duplicate_values_across_extracts() -> None:
    roots = (
        REPO_ROOT / "data" / "literature" / "extracts-v2",
        REPO_ROOT / "data" / "literature" / "observations-v2",
    )
    observations: list[dict] = []
    summary = load_observation_store_summary(REPO_ROOT)
    for root in roots:
        for path in iter_observation_store_paths(root):
            if compilation_family_from_store_path(path) is not None:
                _account_compilation_shard(path, summary)
                continue
            stored = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
            if isinstance(stored, dict):
                observations.extend(stored.get("observations") or [])
    duplicates = _same_work_observation_duplicate_pairs(observations)
    # b-677 retired the 17-row secondary extract and linked its two remaining
    # KEMS/REF-014 overlaps as derived, so no independent duplicate pairs remain.
    expected_fedkin_duplicates = []
    assert duplicates == expected_fedkin_duplicates, duplicates


def test_duplicate_guard_catches_measured_copy_but_not_ulp_or_different_value() -> None:
    owner = {
        "observation_id": "owner::row",
        "experiment_id": "10.1234/example::owner",
        "source_id": "owner",
        "identity": {
            "quantity": {"tag": "value", "value": "evaporation_coefficient_alpha"},
            "species": {"formula": "Fe"},
            "temperature_K": {"tag": "value", "value": "2073"},
        },
        "value": {"kind": "point", "point": "0.25", "approximate": False},
        "admission": {"status": "admitted"},
    }
    duplicate = {
        **owner,
        "observation_id": "secondary::row",
        "experiment_id": "10.1234/example::secondary",
        "source_id": "secondary",
        "evidence": {"class": {"tag": "value", "value": "measured_direct"}},
    }
    assert _same_work_observation_duplicate_pairs([owner, duplicate]) == [
        ("10.1234/example", "owner::row", "secondary::row")
    ]

    duplicate["derived_from"] = ["owner::row"]
    assert _same_work_observation_duplicate_pairs([owner, duplicate]) == []
    duplicate.pop("derived_from")

    # A precise decimal difference well below displayed precision is not equality.
    duplicate["value"] = {
        "kind": "point",
        "point": "0.2500000000000000000000000001",
        "approximate": False,
    }
    assert _same_work_observation_duplicate_pairs([owner, duplicate]) == []

    duplicate["value"] = {"kind": "point", "point": "0.3", "approximate": False}
    assert _same_work_observation_duplicate_pairs([owner, duplicate]) == []


def test_fusion_quantities_migrate_with_phase_transition_identity(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    observations = extract["species"]["Na"]["observations"]
    observations.extend(
        [
            {
                "observation_id": "na_fusion_temperature",
                "type": "transition_point",
                "locator": {"table": "4", "row": "Na"},
                "phase": "cr",
                "units": "K",
                "values": {"quantity": "transition_temperature", "T_m_K": 1200},
            },
            {
                "observation_id": "na_heat_of_fusion",
                "type": "heat_capacity_and_heat_content_series",
                "locator": {"table": "4", "row": "Na", "column": "ΔH_f"},
                "phase": "cr",
                "units": "kJ mol−1",
                "values": {
                    "quantity": "heat_of_fusion",
                    "delta_H_fus_kJ_mol-1": 12.5,
                },
            },
            {
                "observation_id": "na_entropy_of_fusion",
                "type": "heat_capacity_and_heat_content_series",
                "locator": {"table": "4", "row": "Na", "column": "ΔS_f"},
                "phase": "cr",
                "units": "J mol−1 K−1",
                "values": {
                    "quantity": "entropy_of_fusion",
                    "delta_S_fus_J_mol-1_K-1": 18.0,
                },
            },
            {
                "observation_id": "na_heat_content",
                "type": "heat_capacity_and_heat_content_series",
                "locator": {"table": "3", "row": "Na"},
                "phase": "cr",
                "units": "kJ/mol",
                "values": {
                    "quantity": "H_minus_H298",
                    "enthalpy_increment": 2.0,
                    "T_K": 500,
                },
            },
        ]
    )
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }

    for observation_id, quantity, expected_value in (
        ("na_heat_of_fusion", Quantity.ENTHALPY_OF_FUSION, Decimal("12.5")),
        ("na_entropy_of_fusion", Quantity.ENTROPY_OF_FUSION, Decimal("18.0")),
    ):
        observation = by_id[observation_id]
        identity = observation.identity
        assert quantity_token(identity) is quantity
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == expected_value
        assert identity.species.formula == "Na"
        assert identity.species.phase.is_value
        assert identity.species.phase.value is Phase.CR
        assert identity.temperature_K is not None
        assert identity.temperature_K.is_unknown
        assert identity.subtype is not None
        assert identity.subtype.value == "solid_to_liquid"
        assert identity.species.polymorph is not None
        assert identity.species.polymorph.is_unknown
        assert identity.species.polymorph.reason == "source does not state polymorph"
        identity_outcome = identity_equal(identity, identity)
        assert identity_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
        if observation.point_conditions is not None:
            temperature = observation.point_conditions.get("temperature_K")
            assert temperature is None or temperature.state.is_unknown

    heat_content = by_id["na_heat_content"]
    assert quantity_token(heat_content.identity) is Quantity.H_MINUS_H298
    assert heat_content.value.kind is ValueKind.POINT
    assert heat_content.value.point == Decimal("2.0")


def test_fusion_temperature_join_preserves_matching_melting_point(tmp_path: Path) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["SiO2"] = {
        "observations": [
            {
                "observation_id": "alpha_quartz_melting_point",
                "type": "transition_point",
                "locator": {"table": "7", "row": "SiO2"},
                "phase": "cr",
                "condensed_form": {"polymorph": "alpha_quartz"},
                "units": "K",
                "values": {
                    "quantity": "transition_temperature",
                    "property_kind": "melting_point",
                    "phase_from": "alpha quartz",
                    "phase_to": "liquid",
                    "value_K": 1200,
                },
            },
            {
                "observation_id": "alpha_quartz_heat_of_fusion",
                "type": "heat_capacity_and_heat_content_series",
                "locator": {"table": "7", "row": "SiO2", "column": "delta_H_f"},
                "phase": "cr",
                "crystal_phase_as_stated": "alpha quartz",
                "condensed_form": {"polymorph": "alpha_quartz"},
                "units": "kJ/mol",
                "values": {
                    "quantity": "heat_of_fusion",
                    "delta_H_fus_kJ_mol-1": 12.5,
                },
            },
        ]
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    fusion = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith("::alpha_quartz_heat_of_fusion")
    )

    assert fusion.identity.temperature_K is not None
    assert fusion.identity.temperature_K.value == Decimal("1200")
    temperature = fusion.point_conditions["temperature_K"]
    assert temperature.state.value == Decimal("1200")
    assert temperature.locator is not None
    assert temperature.locator.table == "7"


@pytest.mark.parametrize(
    "property_kind,phase_from,phase_to,transition_polymorph,fusion_polymorph,temperature_K",
    [
        (
            "solid_solid_transition",
            "alpha quartz",
            "beta quartz",
            "alpha_quartz",
            "alpha_quartz",
            846,
        ),
        (
            "melting_point",
            "alpha quartz",
            "liquid",
            "alpha_quartz",
            "tridymite",
            1200,
        ),
    ],
)
def test_fusion_temperature_requires_melting_and_matching_crystal_identity(
    tmp_path: Path,
    property_kind: str,
    phase_from: str,
    phase_to: str,
    transition_polymorph: str,
    fusion_polymorph: str,
    temperature_K: int,
) -> None:
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["species"]["SiO2"] = {
        "observations": [
            {
                "observation_id": "alpha_to_beta_quartz_transition",
                "type": "transition_point",
                "locator": {"table": "7", "row": "SiO2"},
                "phase": "cr",
                "condensed_form": {"polymorph": transition_polymorph},
                "units": "K",
                "values": {
                    "quantity": "transition_temperature",
                    "property_kind": property_kind,
                    "phase_from": phase_from,
                    "phase_to": phase_to,
                    "value_K": temperature_K,
                },
            },
            {
                "observation_id": "alpha_quartz_heat_of_fusion",
                "type": "heat_capacity_and_heat_content_series",
                "locator": {"table": "7", "row": "SiO2", "column": "delta_H_f"},
                "phase": "cr",
                "crystal_phase_as_stated": fusion_polymorph,
                "condensed_form": {"polymorph": fusion_polymorph},
                "units": "kJ/mol",
                "values": {
                    "quantity": "heat_of_fusion",
                    "delta_H_fus_kJ_mol-1": 12.5,
                },
            },
        ]
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    fusion = next(
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith("::alpha_quartz_heat_of_fusion")
    )

    assert fusion.identity.temperature_K is not None
    assert fusion.identity.temperature_K.is_unknown
    if fusion.point_conditions is not None:
        temperature = fusion.point_conditions.get("temperature_K")
        assert temperature is None or temperature.state.is_unknown
def test_stolyarova_1995_printed_compositions_reach_migrated_observations(
    tmp_path: Path,
) -> None:
    from collections import Counter

    from simulator.battery.identity import Identity
    from simulator.battery.score import (
        ScoreContext,
        comparison_candidates,
        rail_for_quantity,
    )

    source_id = "stolyarova-1995-cao-alumina-kems"
    result = _migrate_real_extract(
        tmp_path, "stolyarova-1995-cao-alumina-kems.yaml"
    )
    rows = [obs for obs in result.observations.values() if obs.source_id == source_id]
    pressure_rows = [
        obs
        for obs in rows
        if isinstance(obs.identity, Identity)
        and quantity_token(obs.identity) is Quantity.P_PARTIAL
    ]

    assert len(rows) == 56
    assert len(pressure_rows) == 50
    for obs in pressure_rows:
        assert obs.identity.species.phase.is_value
        assert obs.identity.species.phase.value is Phase.G
        assert obs.identity.temperature_K.is_value
        assert obs.identity.temperature_K.value == Decimal("1933")
        assert obs.identity.composition is not None
        assert obs.identity.composition.is_value
        condition = obs.point_conditions["composition"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert condition.locator.page == 687
        assert condition.locator.table == "2"
        assert "binary complement" in condition.locator.note
        assert condition.state.value == obs.identity.composition.value
        assert obs.identity.fO2_Pa.is_unknown
        assert obs.identity.total_pressure_Pa.is_unknown
        assert (
            obs.identity.total_pressure_Pa.reason
            == "in_cell_total_pressure_not_derivable"
        )

    ca_0143 = next(
        obs
        for obs in pressure_rows
        if "stolyarova_1995_ca_partial_pressure_table2" in obs.observation_id
        and obs.point_conditions["composition"].state.value.components[0][1]
        == Decimal("0.143")
    )
    assert dict(ca_0143.identity.composition.value.components) == {
        "CaO": Decimal("0.143"),
        "Al2O3": Decimal("0.857"),
    }

    al_0000 = next(
        obs
        for obs in pressure_rows
        if "stolyarova_1995_al_partial_pressure_table2" in obs.observation_id
        and obs.point_conditions["composition"].state.value.components[0][1]
        == Decimal("0.000")
    )
    assert dict(al_0000.identity.composition.value.components) == {
        "CaO": Decimal("0.000"),
        "Al2O3": Decimal("1.000"),
    }

    figure_rows = [obs for obs in rows if "figure_only" in obs.observation_id]
    activities = [
        obs
        for obs in figure_rows
        if "activity_fig1" in obs.observation_id
    ]
    assert len(figure_rows) == 6
    assert all(obs.value.kind is ValueKind.UNAVAILABLE for obs in figure_rows)
    assert len(activities) == 2

    candidates = [
        obs
        for obs in comparison_candidates(
            ScoreContext(
                works=result.works,
                experiments=result.experiments,
                observations=result.observations,
            )
        )
        if obs.source_id == source_id
    ]
    rails = Counter(
        str(
            rail_for_quantity(
                quantity_token(obs.identity),
                species_formula=obs.identity.species.formula,
            )
        )
        for obs in candidates
    )
    assert len(candidates) == 50
    assert rails == {"vapour": 50}
    assert sum(
        obs.evidence.class_.is_value
        and obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        for obs in candidates
    ) == 26


def test_stolyarova_1995_composition_mutation_removes_identity_map(
    tmp_path: Path,
) -> None:
    source_id = "stolyarova-1995-cao-alumina-kems"
    name = "stolyarova-1995-cao-alumina-kems.yaml"
    source = REPO_ROOT / "data" / "literature" / "extracts" / name
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    first = extract["species"]["Ca"]["observations"][0]["values"]["points"][0]
    del first["composition_mol"]

    root = tmp_path / "mutant"
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (extracts / name).write_text(yaml.safe_dump(extract, sort_keys=False), encoding="utf-8")
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": source_id,
                        "citation": extract["source"]["citation"],
                        "doi": extract["source"].get("doi"),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    mutated = next(
        obs
        for obs in result.observations.values()
        if obs.source_id == source_id
        and "stolyarova_1995_ca_partial_pressure_table2::T=1933" in obs.observation_id
        and obs.point_conditions["composition"].state.value.components[0][1]
        == Decimal("0.143")
    )
    assert mutated.point_conditions["composition"].state.is_value
    assert mutated.identity.composition.is_unknown


def test_stolyarova_1991_printed_compositions_reach_migrated_observations(
    tmp_path: Path,
) -> None:
    from simulator.battery.identity import Identity

    result = _migrate_real_extract(
        tmp_path, "kems-053-stolyarova-1991.yaml", write=False
    )
    rows = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "kems-053-stolyarova-1991"
    ]
    table_rows = [
        obs
        for obs in rows
        if obs.locator is not None
        and obs.locator.table in {"Ia", "Ib", "IIa", "IIb"}
        and obs.point_conditions
        and "composition" in obs.point_conditions
    ]
    assert len(table_rows) == 130
    for obs in table_rows:
        assert isinstance(obs.identity, Identity)
        assert obs.identity.composition is not None
        assert obs.identity.composition.is_value
        assert obs.identity.temperature_K.is_value
        condition = obs.point_conditions["composition"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert "CaO=1-X(SiO2)" in condition.locator.note
        assert "T_C is derived as T_K−273.15" in condition.locator.note
        assert dict(obs.identity.composition.value.components) == dict(
            condition.state.value.components
        )
        expected_temperature = Decimal(
            "1993" if obs.locator.table == "Ia" else "1933"
        )
        assert obs.identity.temperature_K.value == expected_temperature

    ca = next(
        obs
        for obs in table_rows
        if "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation"
        in obs.observation_id
        and "Printed X(SiO2)=0.44" in obs.point_conditions["composition"].locator.note
    )
    assert dict(ca.identity.composition.value.components) == {
        "CaO": Decimal("0.56"),
        "SiO2": Decimal("0.44"),
    }

    ion_current_rows = [obs for obs in rows if "_ion_comparison" in obs.observation_id]
    assert len(ion_current_rows) == 36
    assert all(obs.identity.species.phase.value is Phase.G for obs in ion_current_rows)

    pressure_rows = [
        obs
        for obs in table_rows
        if obs.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert pressure_rows
    assert all(obs.identity.fO2_Pa.is_unknown for obs in pressure_rows)
    assert all(obs.identity.total_pressure_Pa.is_unknown for obs in pressure_rows)


def test_stolyarova_1991_atomic_oxygen_is_quoted_attributed_with_relation(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path, "kems-053-stolyarova-1991.yaml", write=False
    )
    rows = [
        obs
        for obs in result.observations.values()
        if obs.source_id == "kems-053-stolyarova-1991"
        and "stolyarova_1991_o_atomic_partial_pressure_1933k_equilibrium"
        in obs.observation_id
    ]

    assert len(rows) == 9
    work_id = next(
        work_id
        for work_id, work in result.works.items()
        if "kems-053-stolyarova-1991" in work.source_ids
    )
    assert any(
        row.get("observation_id") == "stolyarova_1991_printed_equilibrium_1"
        for row in result.context_by_work[work_id]
    )
    context = next(
        row
        for row in result.context_by_work[work_id]
        if row.get("observation_id") == "stolyarova_1991_printed_equilibrium_1"
    )
    assert context["values"]["equation_as_printed"] == "(WO3) = (WO2) + (O)"
    assert context["values"]["description"] == (
        "P_O is calculated from measured WO2 and WO3 partial pressures and the "
        "equilibrium constant for the printed gas reaction (WO3) = (WO2) + "
        "(O), Eq. [1]; numerical WO2/WO3 input pressures are not tabulated."
    )
    assert Counter(row.value.point for row in rows) == Counter(
        Decimal(value)
        for value in (
            "0.000101325",
            "0.000101325",
            "0.000101325",
            "0.000101325",
            "0.000101325",
            "0.00006890100",
            "0.00006890100",
            "0.00007498050",
            "0.0000678877500000000101325",
        )
    )
    for obs in rows:
        assert obs.derivation is not None
        assert obs.derivation.relation == "atm_to_Pa"
        assert "WO3" not in obs.derivation.relation
        assert obs.derived_from is None
        assert obs.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert obs.evidence.attribution == "Stolyarova et al. (1991), Table Ib, Eq. [1]"


def test_stolyarova_1991_composition_map_mutation_removes_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    module = sys.modules[__name__]
    mutated_root = tmp_path / "mutated_repo"
    extracts = mutated_root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    original = REPO_ROOT / "data" / "literature" / "extracts" / (
        "kems-053-stolyarova-1991.yaml"
    )
    shutil.copyfile(original, extracts / original.name)
    document = yaml.safe_load(original.read_text(encoding="utf-8"))
    point = document["species"]["Ca"]["observations"][0]["values"]["points"][0]
    point.pop("composition_mol")
    (extracts / original.name).write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", mutated_root)

    result = _migrate_real_extract(tmp_path / "migration", original.name, write=False)
    mutated = next(
        obs
        for obs in result.observations.values()
        if obs.source_id == "kems-053-stolyarova-1991"
        and "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation"
        in obs.observation_id
        and "T=1993.00" in obs.observation_id
        and obs.identity.composition.is_unknown
        and obs.point_conditions["composition"].state.is_value
    )
    assert mutated.identity.composition.reason == "no composition mapped from source"

def test_shornikov_1994_printed_mullite_composition_reaches_every_point(
    tmp_path: Path,
) -> None:
    from simulator.battery.enums import Phase, Quantity

    source_id = "shornikov-1994-mullite-kems"
    result = _migrate_real_extract(
        tmp_path / source_id, f"{source_id}.yaml"
    )
    rows = [obs for obs in result.observations.values() if obs.source_id == source_id]
    assert len(rows) == 41
    assert all("::T=" in obs.observation_id for obs in rows)

    named = {
        "shornikov_1994_al2o3_activity_1833k": Decimal("1833"),
        "shornikov_1994_sio2_activity_2033k": Decimal("2033"),
    }
    found_named: set[str] = set()
    for obs in rows:
        identity = obs.identity
        composition = identity.composition
        assert composition is not None and composition.is_value, obs.observation_id
        assert composition.value.basis == "derived_from_formula"
        assert composition.value.amount_basis.value == "mole_fraction"
        assert composition.value.components == (
            ("Al2O3", Decimal("0.6")),
            ("SiO2", Decimal("0.4")),
        )
        conditions = obs.point_conditions or {}
        assert conditions["composition"].state.value == composition.value
        assert conditions["composition"].locator is not None
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        assert identity.temperature_K.value == conditions["temperature_K"].state.value
        assert conditions["temperature_K"].locator is not None
        for row_id, temperature in named.items():
            if row_id in obs.observation_id:
                found_named.add(row_id)
                assert identity.temperature_K.value == temperature
        if identity.quantity.value is Quantity.P_PARTIAL:
            assert identity.species.phase.is_value
            assert identity.species.phase.value is Phase.G
            assert identity.fO2_Pa.is_unknown
            assert identity.total_pressure_Pa.is_unknown

    assert found_named == set(named)

def test_ohara_1987_printed_compositions_reach_migrated_observations(
    tmp_path: Path,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_scoring import (
        feto_fe2_fe3_to_feo_fe2o3_moles,
    )
    from simulator.battery.stable_ids import series_row_extra

    source_id = "kems-058-ohara-1987"
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
    ]
    table1 = [
        observation
        for observation in rows
        if observation.locator is not None and observation.locator.table == "1"
    ]
    pressures = [
        observation
        for observation in table1
        if quantity_token(observation.identity) is Quantity.P_PARTIAL
    ]
    ion_currents = [
        observation
        for observation in table1
        if quantity_token(observation.identity) is Quantity.ION_INTENSITY_RATIO
    ]
    for observation in pressures + ion_currents:
        temperature = observation.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert temperature.value == Decimal("1643.0")
        assert observation.point_conditions is not None
        point_temperature = observation.point_conditions["temperature_K"]
        assert point_temperature.state.is_value
        assert point_temperature.state.value == Decimal("1643.0")
        assert point_temperature.inference is None

    assert all(
        observation.identity.fO2_Pa is not None
        and observation.identity.fO2_Pa.is_unknown
        and observation.identity.total_pressure_Pa is not None
        and observation.identity.total_pressure_Pa.is_unknown
        for observation in pressures
    )
    for observation in pressures:
        condition = observation.point_conditions["composition"]
        assert condition.locator is not None
        if observation.identity.composition.is_value:
            assert condition.state.is_value
            assert condition.locator.table == "2"
            assert "sample " in (condition.locator.note or "")
        else:
            assert condition.state.is_unknown
            assert condition.locator.table == "1"
    sample20_pressures = [
        observation
        for observation in pressures
        if observation.identity.composition is not None
        and observation.identity.composition.is_unknown
    ]
    for observation in sample20_pressures:
        condition = observation.point_conditions["composition"]
        assert condition.state.is_unknown
        assert "sample 20" in (condition.state.reason or "")
        assert condition.locator is not None and condition.locator.table == "1"

    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in ion_currents
    )
    current_conditions = [
        observation.point_conditions["composition"] for observation in ion_currents
    ]
    assert all(
        condition.locator is not None and condition.locator.table == "2"
        for condition in current_conditions
        if condition.state.is_value
    )
    current_sample20 = [
        condition for condition in current_conditions if condition.state.is_unknown
    ]
    assert "sample 20" in (current_sample20[0].state.reason or "")

    table2 = [
        observation
        for observation in rows
        if observation.locator is not None and observation.locator.table == "2"
    ]
    assert all(
        observation.identity.composition is not None
        and observation.identity.composition.is_value
        and observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == Decimal("1643.0")
        for observation in table2
    )
    assert all(
        observation.point_conditions is not None
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].state.value
        == Decimal("1643.0")
        and observation.point_conditions["temperature_K"].inference is None
        for observation in table2
    )
    assert all(
        sum(observation.identity.composition.value.as_map().values())
        == pytest.approx(Decimal("1"))
        for observation in table2
    )
    table2_compositions = {
        int(observation.observation_id.rsplit("_", 1)[-1]):
        observation.identity.composition.value
        for observation in table2
    }
    source_observations = _extract_observations(f"{source_id}.yaml")
    table2_source_rows = [
        source["values"]["row"]
        for source in source_observations
        if source.get("locator", {}).get("table") == "2"
        and isinstance(source.get("values", {}).get("row"), dict)
        and "sample_no" in source["values"]["row"]
    ]
    assert sorted(table2_compositions) == sorted(
        int(source_row["sample_no"]) for source_row in table2_source_rows
    )

    expected_table2_compositions = {}
    for source_row in table2_source_rows:
        sample_number = int(source_row["sample_no"])
        printed_moles = {
            key.removesuffix("_mol_percent"): as_decimal(value)
            for key, value in source_row.items()
            if key.endswith("_mol_percent") and key != "FetO_mol_percent"
        }
        n_feo, n_fe2o3 = feto_fe2_fe3_to_feo_fe2o3_moles(
            source_row["FetO_mol_percent"], source_row["Fe2_over_Fe3"]
        )
        printed_moles["FeO"] = as_decimal(n_feo)
        printed_moles["Fe2O3"] = as_decimal(n_fe2o3)
        total_moles = sum(printed_moles.values())
        expected_table2_compositions[sample_number] = {
            component: amount / total_moles
            for component, amount in printed_moles.items()
        }
    for sample_number, expected_composition in expected_table2_compositions.items():
        assert table2_compositions[sample_number].as_map() == pytest.approx(
            expected_composition
        ), f"Table 2 composition does not match printed sample {sample_number}"
    pressure_rows_by_species = {
        "PO": "ohara_1987_table1_partial_pressures_1643k",
        "PO2": "ohara_1987_po2_table1_partial_pressure",
        "P2": "ohara_1987_p2_table1_partial_pressure",
    }
    pressure_source_rows = {
        formula: next(
            row["values"]["rows"]
            for row in source_observations
            if row.get("observation_id") == source_observation_id
        )
        for formula, source_observation_id in pressure_rows_by_species.items()
    }
    current_source_rows = next(
        row["values"]["rows"]
        for row in source_observations
        if row.get("observation_id") == "ohara_1987_table1_ion_current_ratios_1643k"
    )

    def assert_row_composition_matches_sample(observations, source_rows) -> None:
        matched_sample_numbers = []
        for observation in observations:
            matching_rows = [
                row
                for row in source_rows
                if observation.observation_id.endswith(
                    series_row_extra(row, include_locator=False)
                )
            ]
            assert len(matching_rows) == 1
            sample_number = int(matching_rows[0]["sample_no"])
            matched_sample_numbers.append(sample_number)
            condition = observation.point_conditions["composition"]
            if sample_number in table2_compositions:
                expected_composition = table2_compositions[sample_number]
                if quantity_token(observation.identity) is Quantity.P_PARTIAL:
                    assert observation.identity.composition.value == expected_composition, (
                        f"pressure composition does not match source sample {sample_number}"
                    )
                else:
                    assert observation.identity.composition.is_not_applicable
                assert condition.state.value == expected_composition
                assert condition.locator.table == "2"
                assert f"sample {sample_number}" in condition.locator.note
            else:
                assert sample_number == 20
                if quantity_token(observation.identity) is Quantity.P_PARTIAL:
                    assert observation.identity.composition.is_unknown
                else:
                    assert observation.identity.composition.is_not_applicable
                assert condition.state.is_unknown
                assert condition.locator.table == "1"
                assert "sample 20" in condition.locator.note
        assert sorted(matched_sample_numbers) == sorted(
            int(row["sample_no"]) for row in source_rows
        )

    for formula, source_rows in pressure_source_rows.items():
        assert_row_composition_matches_sample(
            [
                observation
                for observation in pressures
                if observation.identity.species.formula == formula
            ],
            source_rows,
        )
    assert_row_composition_matches_sample(ion_currents, current_source_rows)

    sample19 = next(
        observation
        for observation in table2
        if observation.observation_id.endswith("sample_19")
    )
    assert set(sample19.identity.composition.value.as_map()) == {
        "P2O5",
        "FeO",
        "Fe2O3",
        "CaO",
        "SiO2",
    }

    figure_rows = [
        observation
        for observation in rows
        if "figure_only" in observation.observation_id
    ]
    assert len(figure_rows) == 3
    assert all(observation.value.kind is ValueKind.UNAVAILABLE for observation in figure_rows)
    assert all(
        observation.admission.status is AdmissionStatus.REJECTED
        for observation in figure_rows
    )
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        for observation in figure_rows
    )
    assert all(
        observation.admission.reason == "figure_not_digitized"
        for observation in figure_rows
    )
    assert all(observation.value.kind is not ValueKind.POINT for observation in figure_rows)
    # No closed quantity token represents L_P; preserve its typed unavailable refusal.
    figure3 = next(
        observation for observation in figure_rows if "fig3" in observation.observation_id
    )
    assert figure3.identity.quantity.is_unknown


def test_ohara_1987_sample_swap_mutation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = "kems-058-ohara-1987"
    name = f"{source_id}.yaml"
    mutated = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / name).read_text(
            encoding="utf-8"
        )
    )
    pressure_parent = next(
        row
        for block in mutated["species"].values()
        for row in block.get("observations", [])
        if row.get("observation_id") == "ohara_1987_table1_partial_pressures_1643k"
    )
    sample2, sample3 = pressure_parent["values"]["rows"][1:3]
    for key in ("composition_mol", "point_conditions"):
        sample2[key], sample3[key] = sample3[key], sample2[key]

    mutation_root = tmp_path / "mutation-repository"
    extract_dir = mutation_root / "data" / "literature" / "extracts"
    extract_dir.mkdir(parents=True)
    (extract_dir / name).write_text(
        yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", mutation_root)

    with pytest.raises(AssertionError, match="source sample 2"):
        test_ohara_1987_printed_compositions_reach_migrated_observations(
            tmp_path / "mutated-migration"
        )


def test_ohara_1987_point_condition_locator_mutation_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = "kems-058-ohara-1987"
    name = f"{source_id}.yaml"
    mutation_note = "mutation locator proves explicit row condition is read"
    source_path = REPO_ROOT / "data" / "literature" / "extracts" / name
    mutated = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source_parent = next(
        row
        for block in mutated["species"].values()
        for row in block.get("observations", [])
        if row.get("observation_id") == "ohara_1987_table1_partial_pressures_1643k"
    )
    for key in ("T_range_K", "T_K", "temperature_K"):
        source_parent.pop(key, None)
    source_parent["values"].pop("T_K", None)
    source_parent["values"].pop("T_C_as_printed", None)
    target_row = next(
        row
        for row in source_parent["values"]["rows"]
        if row.get("sample_no") == 1
    )
    target_row.pop("T_K", None)
    target_row["point_conditions"]["composition"]["locator"]["note"] = mutation_note

    mutation_root = tmp_path / "mutation-repository"
    extract_dir = mutation_root / "data" / "literature" / "extracts"
    extract_dir.mkdir(parents=True)
    (extract_dir / name).write_text(
        yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", mutation_root)
    result = _migrate_real_extract(tmp_path / "migration", name, write=False)
    row_observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
        and observation.observation_id.startswith(
            f"{source_id}::ohara_1987_table1_partial_pressures_1643k::T="
        )
    ]
    assert len(row_observations) == 20
    converted_row = next(
        observation
        for observation in row_observations
        if observation.point_conditions is not None
        and observation.point_conditions["composition"].locator is not None
        and observation.point_conditions["composition"].locator.note == mutation_note
    )
    assert converted_row.identity.temperature_K.value == Decimal("1643.15")
    assert all(
        observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == Decimal("1643.0")
        for observation in row_observations
        if observation is not converted_row
    )
    mutated_conditions = [
        observation
        for observation in row_observations
        if quantity_token(observation.identity) is Quantity.P_PARTIAL
        and observation.point_conditions is not None
        and observation.point_conditions["composition"].locator is not None
        and observation.point_conditions["composition"].locator.note == mutation_note
    ]
    assert len(mutated_conditions) == 1
    assert mutated_conditions[0].point_conditions["composition"].state.is_value
def _kambayashi_table4_compositions(doc: dict) -> dict[int, dict[str, Decimal]]:
    from decimal import ROUND_HALF_UP

    from simulator.accounting.formulas import parse_formula

    table4 = next(
        observation
        for observation in doc["species"]["P2O5"]["observations"]
        if observation["observation_id"]
        == "kambayashi_1985_table4_feto_p2o5_compositions"
    )
    oxides = ("P2O5", "FeO", "Fe2O3")
    molar_masses = {
        oxide: Decimal(str(parse_formula(oxide).molar_mass_g_per_mol()))
        for oxide in oxides
    }
    expected = {}
    for row in table4["values"]["rows"]:
        moles = {
            oxide: Decimal(str(row[f"{oxide}_wt_percent"])) / molar_masses[oxide]
            for oxide in oxides
        }
        total = sum(moles.values())
        p2o5 = (moles["P2O5"] / total).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
        feo = (moles["FeO"] / total).quantize(
            Decimal("0.000000000001"), rounding=ROUND_HALF_UP
        )
        expected[int(row["sample_no"])] = {
            "P2O5": p2o5,
            "FeO": feo,
            "Fe2O3": Decimal("1") - p2o5 - feo,
        }
    return expected


def _assert_kambayashi_sample_associations(doc: dict, result) -> None:
    source_id = "kems-057-kambayashi-1985"
    samples = _kambayashi_table4_compositions(doc)

    def source_observation(observation_id: str) -> dict:
        return next(
            observation
            for species in doc["species"].values()
            for observation in species["observations"]
            if observation["observation_id"] == observation_id
        )

    def migrated_rows(observation_id: str):
        prefix = f"{source_id}::{observation_id}::"
        return [
            observation
            for key, observation in result.observations.items()
            if key.startswith(prefix)
        ]

    def assert_composition_condition(observation, expected, source_table):
        located = (observation.point_conditions or {}).get("composition")
        assert located is not None and located.state.is_value
        assert dict(located.state.value.components) == expected
        assert located.locator is not None
        assert located.locator.table == source_table

    def assert_temperature(observation, row, table):
        expected = Decimal(str(row["T_C"])) + Decimal("273.15")
        located = (observation.point_conditions or {}).get("temperature_K")
        assert located is not None and located.state.is_value
        assert located.state.value == expected
        assert located.locator is not None
        assert located.locator.table == table

    # Table 3 has no sample number: its printed X_P2O5 cell identifies each
    # binary composition and is independent of the composition locator.
    for observation_id in (
        "kambayashi_1985_table3_pbo_p2o5_ion_current_ratios_1300c",
        "kambayashi_1985_pbo_table3_ion_current_ratios_1300c",
    ):
        source = source_observation(observation_id)
        rows = source["values"]["rows"]
        migrated = migrated_rows(observation_id)
        assert len(migrated) == len(rows) == 3
        for observation, row in zip(migrated, rows, strict=True):
            x_p2o5 = Decimal(str(row["X_P2O5"]))
            expected = {"PbO": Decimal("1") - x_p2o5, "P2O5": x_p2o5}
            assert_composition_condition(observation, expected, "3")
            assert_temperature(observation, {"T_C": 1300}, "3")
            assert observation.identity.species.phase.is_value
            assert observation.identity.species.phase.value is Phase.G

    table4_id = "kambayashi_1985_table4_feto_p2o5_compositions"
    table4_rows = source_observation(table4_id)["values"]["rows"]
    migrated = migrated_rows(table4_id)
    assert len(migrated) == len(table4_rows) == 8
    for observation, row in zip(migrated, table4_rows, strict=True):
        expected = samples[int(row["sample_no"])]
        assert_composition_condition(observation, expected, "4")
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        assert dict(observation.identity.composition.value.components) == expected

    sample_linked_series = (
        ("kambayashi_1985_table5_feto_p2o5_ion_current_ratios", "5", False),
        ("kambayashi_1985_table6_partial_pressures_and_dg", "6", True),
        ("kambayashi_1985_po2_table6_partial_pressure", "6", True),
        ("kambayashi_1985_p2_table6_partial_pressure", "6", True),
        ("kambayashi_1985_table7_raoultian_a_p2o5", "7", True),
    )
    for observation_id, table, composition_is_identity in sample_linked_series:
        source_rows = source_observation(observation_id)["values"]["rows"]
        migrated = migrated_rows(observation_id)
        assert len(migrated) == len(source_rows)
        for observation, row in zip(migrated, source_rows, strict=True):
            # Bind by the printed sample_no and row order; do not use the
            # composition locator under test to choose the expected sample.
            expected = samples[int(row["sample_no"])]
            assert_composition_condition(observation, expected, "4")
            assert_temperature(observation, row, table)
            if composition_is_identity:
                assert observation.identity.composition is not None
                assert observation.identity.composition.is_value
                assert dict(observation.identity.composition.value.components) == expected
            if observation_id in {
                "kambayashi_1985_po2_table6_partial_pressure",
                "kambayashi_1985_p2_table6_partial_pressure",
            }:
                assert observation.identity.fO2_Pa is not None
                assert observation.identity.fO2_Pa.is_unknown
                assert observation.identity.fO2_Pa.reason == "no fO2_Pa mapped from source"
                assert observation.identity.total_pressure_Pa is not None
                assert observation.identity.total_pressure_Pa.is_unknown
                assert observation.identity.total_pressure_Pa.reason == (
                    "in_cell_total_pressure_not_derivable"
                )


def test_kambayashi_printed_compositions_and_conditions_reach_observations(
    tmp_path: Path,
) -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-057-kambayashi-1985.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    result = _migrate_real_extract(
        tmp_path, "kems-057-kambayashi-1985.yaml", write=False
    )
    _assert_kambayashi_sample_associations(doc, result)

    figure_quantities = {
        "kambayashi_1985_fig2_pbo_p2o5_ion_ratios_figure_only": Quantity.ION_INTENSITY_RATIO,
        "kambayashi_1985_fig3_time_dependence_figure_only": Quantity.ION_INTENSITY_RATIO,
        "kambayashi_1985_fig4_time_dependence_figure_only": Quantity.ION_INTENSITY_RATIO,
        "kambayashi_1985_fig5_temperature_dependence_figure_only": Quantity.ION_INTENSITY_RATIO,
        "kambayashi_1985_fig6_henrian_a_p2o5_figure_only": Quantity.ACTIVITY,
        "kambayashi_1985_fig7_raoultian_activities_figure_only": Quantity.ACTIVITY,
        "kambayashi_1985_feto_raoultian_gibbs_duhem_fig7_figure_only": Quantity.ACTIVITY,
        "kambayashi_1985_fig8_ion_current_vs_1T_figure_only": Quantity.ION_INTENSITY,
    }
    for observation_id, quantity in figure_quantities.items():
        observation = result.observations[
            f"kems-057-kambayashi-1985::{observation_id}"
        ]
        assert quantity_token(observation.identity) is quantity
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert observation.value.unavailable_reason == "figure_not_digitized"
        assert observation.value.point is None
        assert observation.admission.status is AdmissionStatus.REJECTED
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.locator is not None
        assert "bound_not_point_ordering" in observation.locator.note


@pytest.mark.parametrize(
    ("filename", "observation_ids", "expected_class", "expected_count"),
    [
        (
            "busemann-2000-phase-q-noble-gases.yaml",
            ("busemann_2000_table3_he_quoted_prior_work",),
            EvidenceClass.QUOTED_ATTRIBUTED,
            8,
        ),
        (
            "kems-020-hastie-1981-nbsir.yaml",
            ("hastie_1981_table2_k_logP_coefficients_quoted_20260906",),
            EvidenceClass.MODEL_DERIVED,
            17,
        ),
        (
            "kems-118-yamamoto-1983.yaml",
            ("yamamoto_1983_table6_comparison",),
            EvidenceClass.QUOTED_UNATTRIBUTED,
            7,
        ),
        (
            "kems-190-wu-1993.yaml",
            (
                "wu_1993_table1_mgo_compiled_thermochemistry",
                "wu_1993_table1_sio2_compiled_thermochemistry",
            ),
            EvidenceClass.COMPILATION_ASSESSED,
            6,
        ),
    ],
)
def test_exploded_rows_keep_parent_evidence_outside_author_estimates(
    tmp_path: Path,
    filename: str,
    observation_ids: tuple[str, ...],
    expected_class: EvidenceClass,
    expected_count: int,
) -> None:
    result = _migrate_real_extract(tmp_path / Path(filename).stem, filename)
    observations = [
        observation
        for observation in result.observations.values()
        if any(
            f"::{observation_id}::" in observation.observation_id
            for observation_id in observation_ids
        )
    ]

    assert len(observations) == expected_count
    assert {observation.evidence.class_.value for observation in observations} == {
        expected_class
    }

    if filename == "busemann-2000-phase-q-noble-gases.yaml":
        source_row = next(
            row
            for row in _extract_observations(filename)
            if row.get("observation_id") == observation_ids[0]
        )
        expected_attribution = source_row["values"]["attribution"]
        assert expected_attribution
        assert {
            observation.evidence.attribution for observation in observations
        } == {expected_attribution}


def _zhang_residue_points(result):
    return [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-006-zhang-2021"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.value.kind is ValueKind.POINT
    ]


def test_residue_allowlist_carries_every_identity_oxide_except_total_iron() -> None:
    """FeOT is total iron as FeO. Admitting it would collapse iron into FeO."""

    names = residue_oxide_names()
    fields = residue_component_source_fields()
    assert fields[0] == "residue_ppm"
    assert "FeOT" not in names
    assert "S" not in names
    assert "FeO" in names and "Fe2O3" in names
    for oxide in ("Na2O", "K2O", "TiO2", "Cr2O3", "MnO", "Fe2O3", "P2O5", "Rb2O"):
        assert oxide in names
        assert f"{oxide}_wt_pct" in fields
    both = residue_point_component_items(
        {"FeO_wt_pct": "11", "Fe2O3_wt_pct": "22", "FeOT_wt_pct": "99"}
    )
    assert [field for field, _payload in both if field.endswith("_wt_pct") and "Fe" in field] == [
        "FeO_wt_pct",
        "Fe2O3_wt_pct",
    ]
    assert residue_point_component_items({"FeOT_wt_pct": "99", "T_C": 1400}) == ()
    bare = residue_point_component_items({"FeO": "11", "Fe2O3": "22"})
    assert [field for field, _payload in bare] == ["FeO_wt_pct", "Fe2O3_wt_pct"]
    assert bare[0][1]["FeO_wt_pct"] == "11"
    assert bare[1][1]["Fe2O3_wt_pct"] == "22"


def test_zhang_2021_table1_alkali_residue_pins(tmp_path: Path) -> None:
    """Page-checked Zhang 2021 Table 1 (PDF p. 71), row Bas1400C60m.

    The printed row is 1400 C, 60 min, Na2O 0.01 wt%, K2O 0.28 wt%.
    The extract stores those two oxides as bare keys on values.runs under
    epma_residue_composition_vs_time, not as Na2O_wt_pct / K2O_wt_pct.
    Bas1400C90m prints Na2O as b.d.l.; that cell is absent, not zero.
    initial_glass_Na2O_wt_pct 2.27 is the starting glass, not a residue cell.
    """

    from simulator.battery.score import metric_operation_for_identity

    result = _migrate_real_extract(tmp_path, "kems-006-zhang-2021.yaml")
    points = _zhang_residue_points(result)
    pinned = [
        observation
        for observation in points
        if "Bas1400C60m" in (observation.locator.note or "")
    ]
    by_formula = {observation.identity.species.formula: observation for observation in pinned}
    assert set(by_formula) == {"Na2O", "K2O"}
    sodium = by_formula["Na2O"]
    potassium = by_formula["K2O"]
    assert sodium.value.point == Decimal("0.01")
    assert potassium.value.point == Decimal("0.28")
    for observation in (sodium, potassium):
        assert observation.locator.page == 71
        assert observation.locator.table == "1"
        assert observation.identity.temperature_K.value == Decimal("1673.15")
        assert observation.identity.subtype.is_value
        assert observation.identity.subtype.value == "oxide_wt_percent"
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert observation.point_conditions["time_min"].state.value == Decimal("60")
        assert (
            metric_operation_for_identity(observation.identity)
            is MetricOperation.ABSOLUTE
        )
    assert sodium.identity.species.formula != potassium.identity.species.formula
    below_detection = [
        observation
        for observation in points
        if "Bas1400C90m" in (observation.locator.note or "")
        and observation.identity.species.formula == "Na2O"
    ]
    assert below_detection == []
    assert all(observation.value.point != Decimal("0") for observation in points)
    assert all(observation.value.point != Decimal("2.27") for observation in points if observation.identity.species.formula == "Na2O")


def test_removing_k2o_from_the_residue_allowlist_drops_the_zhang_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: the Bas1400C60m K2O pin is red without K2O on the allowlist.

    residue_component_source_fields() is the only admission list. Dropping
    K2O_wt_pct there (this patch) stops the 0.28 wt% cell. Na2O 0.01 stays.
    """

    fields = tuple(
        field
        for field in residue_component_source_fields()
        if field != "K2O_wt_pct"
    )
    assert "K2O_wt_pct" not in fields
    monkeypatch.setitem(
        QUANTITY_SOURCE_FIELDS,
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
        fields,
    )
    result = _migrate_real_extract(tmp_path, "kems-006-zhang-2021.yaml")
    points = _zhang_residue_points(result)
    pinned = [
        observation
        for observation in points
        if "Bas1400C60m" in (observation.locator.note or "")
    ]
    assert [observation.identity.species.formula for observation in pinned] == ["Na2O"]
    assert pinned[0].value.point == Decimal("0.01")
    assert all(observation.identity.species.formula != "K2O" for observation in points)


def test_residue_migration_keeps_feo_and_fe2o3_distinct(tmp_path: Path) -> None:
    """A row that prints both oxides emits both. FeOT_wt_pct does not become FeO."""

    doc = yaml.safe_load(
        (
            REPO_ROOT / "data/literature/extracts/kems-006-zhang-2021.yaml"
        ).read_text(encoding="utf-8")
    )
    run = next(
        item
        for body in doc["species"].values()
        for observation in body["observations"]
        for item in observation["values"].get("runs") or []
        if isinstance(item, dict) and item.get("sample") == "Bas1400C60m"
    )
    run["FeO_wt_pct"] = "11"
    run["Fe2O3_wt_pct"] = "22"
    run["FeOT_wt_pct"] = "99"
    result = _migrate_real_extract(
        tmp_path,
        "kems-006-zhang-2021.yaml",
        extract_doc=doc,
    )
    pinned = [
        observation
        for observation in _zhang_residue_points(result)
        if "Bas1400C60m" in (observation.locator.note or "")
    ]
    by_formula = {
        observation.identity.species.formula: observation.value.point
        for observation in pinned
    }
    assert by_formula == {"FeO": Decimal("11"), "Fe2O3": Decimal("22")}
    assert Decimal("99") not in by_formula.values()
