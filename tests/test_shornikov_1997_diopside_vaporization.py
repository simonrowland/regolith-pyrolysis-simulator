from __future__ import annotations

import copy
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    Phase,
    EvidenceClass,
    Quantity,
    RawInputStatus,
    RefusalReason,
    ReferenceStateConvention,
    ValueKind,
)
from simulator.battery.migrate import convert_pressure_to_pa
from simulator.battery.records import AmountBasis
from simulator.battery.validate import validate_corpus
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-1997-diopside-vaporization.yaml"


def _migrated(extract: Path = EXTRACT):
    return migrate_one_extract(
        root=ROOT,
        path=extract,
        index={},
        aliases={},
    ).result


_TABLE1_PRESSURES_ATM = {
    "shornikov_1997_ca_pressure_table1": [
        (1566, "1.6e-10", "2e-11"),
        (1640, "6.7e-10", "5e-11"),
        (1688, "1.5e-9", "1e-10"),
        (1743, "4.0e-9", "2e-10"),
        (1778, "7.2e-9", "3e-10"),
        (1862, "2.63e-8", "1e-9"),
    ],
    "shornikov_1997_mg_pressure_table1": [
        (1566, "2.2e-9", "2e-10"),
        (1640, "8.1e-9", "7e-10"),
        (1688, "1.9e-8", "1e-9"),
        (1743, "4.9e-8", "2e-9"),
        (1778, "8.5e-8", "3e-9"),
        (1862, "2.98e-7", "1e-8"),
    ],
    "shornikov_1997_sio_pressure_table1": [
        (1566, "1.2e-7", "2e-8"),
        (1640, "5.2e-7", "5e-8"),
        (1688, "1.1e-6", "1e-7"),
        (1743, "3.0e-6", "2e-7"),
        (1778, "5.2e-6", "3e-7"),
        (1862, "1.98e-5", "1e-6"),
    ],
    "shornikov_1997_sio2_pressure_table1": [
        (1688, "1.2e-10", "3e-11"),
        (1743, "5.4e-10", "5e-11"),
        (1778, "1.1e-9", "1e-10"),
        (1862, "5.9e-9", "5e-10"),
    ],
    "shornikov_1997_moo2_pressure_table1": [
        (1566, "6.5e-9", "6e-10"),
        (1640, "1.4e-8", "2e-9"),
        (1688, "2.1e-8", "2e-9"),
        (1743, "2.2e-8", "2e-9"),
        (1778, "2.9e-8", "2e-9"),
        (1862, "3.8e-8", "3e-9"),
    ],
    "shornikov_1997_moo3_pressure_table1": [
        (1566, "1.1e-9", "2e-10"),
        (1640, "3.2e-9", "4e-10"),
        (1688, "5.7e-9", "4e-10"),
        (1743, "6.4e-9", "4e-10"),
        (1778, "8.6e-9", "5e-10"),
        (1862, "1.2e-8", "1e-9"),
    ],
    "shornikov_1997_o_atomic_pressure_table1": [
        (1566, "4.0e-13", "1e-13"),
        (1640, "3.8e-12", "4e-13"),
        (1688, "1.5e-11", "2e-12"),
        (1743, "6.3e-11", "8e-12"),
        (1778, "1.4e-10", "1.5e-11"),
        (1862, "9.2e-10", "1e-10"),
    ],
    "shornikov_1997_o_atomic_alternate_pressure_table1": [
        (1688, "1.1e-11", "3e-12"),
        (1743, "5.0e-11", "9e-12"),
        (1778, "1.2e-10", "1.8e-11"),
        (1862, "7.0e-10", "1.5e-10"),
    ],
    "shornikov_1997_o2_pressure_table1": [
        (1566, "2.0e-15", "1e-15"),
        (1640, "2.8e-14", "4e-15"),
        (1688, "1.5e-13", "2e-14"),
        (1743, "8.2e-13", "1.3e-13"),
        (1778, "1.69e-12", "1.5e-13"),
        (1862, "1.86e-11", "9e-13"),
    ],
}


def _assert_table1_migrated_pressures(result):
    rows = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table1::" in row.observation_id
    ]
    assert len(rows) == 50
    assert len(_TABLE1_PRESSURES_ATM) == 9
    assert sum(map(len, _TABLE1_PRESSURES_ATM.values())) == 50

    for observation_id, expected_points in _TABLE1_PRESSURES_ATM.items():
        matching = [
            row for row in rows if f"::{observation_id}::T=" in row.observation_id
        ]
        matching.sort(
            key=lambda row: Decimal(row.observation_id.split("::T=")[1].split(":")[0])
        )
        assert len(matching) == len(expected_points)
        for row, (temperature, pressure_atm, sigma_atm) in zip(
            matching, expected_points, strict=True
        ):
            expected_pa, relation = convert_pressure_to_pa(
                Decimal(pressure_atm), "atm"
            )
            assert relation == "atm_to_Pa"
            assert row.value.kind is ValueKind.POINT
            assert row.value.point == expected_pa, row.observation_id
            assert Decimal(str(row.uncertainty.verbatim["sigma"])) == Decimal(
                sigma_atm
            )
            temperature_condition = row.point_conditions["temperature_K"]
            composition_condition = row.point_conditions["composition"]
            assert temperature_condition.state.is_value
            assert temperature_condition.state.value == Decimal(temperature)
            assert temperature_condition.locator is not None
            assert composition_condition.locator is not None


def test_table_values_conditions_and_uncertainties_reach_migrated_records():
    result = _migrated()
    source_document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = {
        observation["observation_id"]: observation
        for block in source_document["species"].values()
        for observation in block["observations"]
    }
    records = list(result.observations.values())
    assert len(records) == 91
    quantity_unknown = {
        row.uncertainty.verbatim["parent"].rsplit("::", 1)[-1]
        for row in records
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == {
        "shornikov_1997_cao_partial_entropy_table4",
        "shornikov_1997_mgo_partial_entropy_table4",
        "shornikov_1997_sio2_partial_entropy_table4",
    }
    assert not [
        row.observation_id for row in records if row.identity.species.phase.is_unknown
    ]
    context_rows = source_document["species"]["Ca"]["context"]
    assert len(context_rows) == 6
    assert all(
        row["values"]["measurement_state_composition"]["state"]["reason"]
        == "not_published"
        for row in context_rows
    )
    assert all(
        row["values"]["run_order_index"]["state"]["reason"]
        == "not_published"
        for row in context_rows
    )
    assert all(
        row["values"]["depletion_evidence"]["state"]["reason"]
        == "not_published"
        and row["values"]["depletion_evidence"]["locator"]["table"] == "1"
        for row in context_rows
    )
    assert context_rows[0]["values"]["vaporization_statement"]["state"]["value"] == (
        "The vaporization of CaO · MgO · 2SiO2 mainly occurs as the transitions to "
        "the gas phase of pure CaO, MgO, and SiO2 oxides."
    )
    uncertainty_counts = Counter(
        (
            row.uncertainty.kind.value,
            (row.uncertainty.verbatim or {}).get("confidence_level")
            if isinstance(row.uncertainty.verbatim, dict)
            else None,
        )
        for row in records
    )
    assert uncertainty_counts == Counter(
        {("printed", "not_stated"): 63, ("printed", None): 28}
    )

    pressure_points = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table1::" in row.observation_id
    ]
    assert len(pressure_points) == 50
    for row in pressure_points:
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == "g"
        assert row.point_conditions["temperature_K"].state.is_value
        if "moo" in row.observation_id:
            assert row.point_conditions["composition"].state.is_unknown
        else:
            assert row.point_conditions["composition"].state.is_value
            composition = row.point_conditions["composition"].state.value
            # CaMgSi2O6 prints oxide mole amounts per formula unit; 1:1:2
            # are inventory amounts, not mole fractions summing to one.
            assert composition.amount_basis is AmountBasis.MOL_INVENTORY
            assert dict(composition.components) == {
                "CaO": Decimal("1"), "MgO": Decimal("1"), "SiO2": Decimal("2")
            }
        assert row.value.kind is ValueKind.POINT
        assert row.uncertainty.verbatim["sigma"] > 0
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"

    from simulator.battery.score import (
        ENGINE_CHANNELS,
        ENGINE_COEFFICIENT_SOURCES,
        ScoreContext,
        SCORE_ENGINE_SET,
        comparison_candidates,
        _initial_charge_composition_notice,
        _oxygen_input_request,
        _source_oxygen_pressure_for_score,
    )
    from simulator.battery.source_oxygen import linked_source_pressure
    from simulator.diagnostic_helpers.binary_pot_battery import PO2_COMMANDED
    from simulator.battery.identity import profile_for

    ca_points = [
        row for row in pressure_points
        if "shornikov_1997_ca_pressure_table1::T=" in row.observation_id
    ]
    o2_points = {
        row.identity.temperature_K.value: row for row in pressure_points
        if "shornikov_1997_o2_pressure_table1::T=" in row.observation_id
    }
    assert len(ca_points) == len(o2_points) == 6
    score_context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
    )
    applicable_ids = {
        candidate.observation_id for candidate in comparison_candidates(score_context)
    }
    for row in ca_points:
        assert row.observation_id in applicable_ids
        temperature = row.identity.temperature_K.value
        source = o2_points[temperature]
        linked = row.point_conditions["fO2_Pa"]
        assert linked.state.is_value
        assert linked.state.value == source.value.point
        assert linked.locator is not None
        assert linked.locator.page == 2 and linked.locator.table == "1"
        assert linked.inference is not None
        assert linked.inference.relation.startswith("source_oxygen_condition;")
        assert linked.inference.inputs == (source.observation_id,)
        assert linked_source_pressure(row) == source.value.point
        assert _source_oxygen_pressure_for_score(row) == source.value.point
        assert source.observation_id.endswith(
            {
                Decimal("1566"): "h=3342f695e6c8",
                Decimal("1640"): "h=67991e112e7e",
                Decimal("1688"): "h=a9a4e0d1ffd2",
                Decimal("1743"): "h=5a3926cb6dd8",
                Decimal("1778"): "h=8a8078e0eb42",
                Decimal("1862"): "h=0698de9b82b3",
            }[temperature]
        )
        composition = row.point_conditions["composition"].state.value
        assert composition.proxy_flag == "initial_charge_only"
        assert _initial_charge_composition_notice(row) is not None
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.reason == "not_published"
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.composition.is_value
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == source.value.point
        assert profile_for(row.identity).required == {
            "composition",
            "fO2_Pa",
            "temperature_K",
            "total_pressure_Pa",
        }
        experiment = result.experiments[row.experiment_id]
        bench = result.benches[experiment.bench_id]
        source_oxygen_bar = float(source.value.point) / 1.0e5
        for engine in SCORE_ENGINE_SET:
            request = _oxygen_input_request(
                engine=engine,
                channel=ENGINE_CHANNELS[engine],
                sources=ENGINE_COEFFICIENT_SOURCES[engine],
                identity=row.identity,
                requested=row.identity.composition,
                quantity=Quantity.P_PARTIAL,
                composition_value=composition,
                input_notices=[],
                observation=row,
                experiment=experiment,
                bench=bench,
                temperature_K=float(temperature),
                source_oxygen_bar=source_oxygen_bar,
            )
            assert request.mode == PO2_COMMANDED
            assert request.po2_bar == source_oxygen_bar

    fit_rows = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.P_PARTIAL
        and "_pressure_table2" in row.observation_id
    ]
    assert len(fit_rows) == 7
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in fit_rows)

    activity_rows = [
        row for row in records
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and "_activity_table3::" in row.observation_id
    ]
    assert len(activity_rows) == 18
    assert all(row.point_conditions["composition"].state.is_value for row in activity_rows)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in activity_rows)

    quoted_activity_rows = [
        row for row in records if "_activity_quoted_ref" in row.observation_id
    ]
    expected_quotes = [
        ("shornikov_1997_cao_activity_quoted_ref23", "CaO", 1873, "0.005"),
        ("shornikov_1997_cao_activity_quoted_ref26", "CaO", 1873, "0.02"),
        ("shornikov_1997_mgo_activity_quoted_ref23", "MgO", 1873, "0.001"),
        ("shornikov_1997_sio2_activity_quoted_ref22", "SiO2", 1823, "0.35"),
        ("shornikov_1997_sio2_activity_quoted_ref25", "SiO2", 1773, "0.48"),
        ("shornikov_1997_sio2_activity_quoted_ref25", "SiO2", 1823, "0.40"),
        ("shornikov_1997_sio2_activity_quoted_ref30", "SiO2", 1773, "0.25"),
        ("shornikov_1997_sio2_activity_quoted_ref30", "SiO2", 1823, "0.22"),
        ("shornikov_1997_sio2_activity_quoted_ref23", "SiO2", 1873, "0.50"),
        ("shornikov_1997_sio2_activity_quoted_ref29", "SiO2", 1873, "0.30"),
    ]
    assert len(quoted_activity_rows) == 10
    quoted_by_parent_and_temperature = {
        (
            row.observation_id.split("::T=", 1)[0].rsplit("::", 1)[-1],
            int(row.identity.temperature_K.value),
        ): row
        for row in quoted_activity_rows
    }
    for parent_id, formula, temperature, value in expected_quotes:
        row = quoted_by_parent_and_temperature[(parent_id, temperature)]
        assert row.identity.species.formula == formula
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.quantity.value is Quantity.ACTIVITY
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.value.point == Decimal(value)
        assert row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert row.experiment_id in result.experiments
        assert row.point_conditions["composition"].state.is_unknown
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        assert row.identity.reference_state.is_unknown

    table4_rows = [row for row in records if "partial_" in row.observation_id and "table4" in row.observation_id]
    assert len(table4_rows) == 6
    expected = {
        "cao_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("0.0"), Decimal("1.4")),
        "cao_partial_entropy": (None, Decimal("17.0"), Decimal("1.5")),
        "mgo_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("-40.2"), Decimal("2.6")),
        "mgo_partial_entropy": (None, Decimal("19.1"), Decimal("1.5")),
        "sio2_partial_enthalpy": (Quantity.PARTIAL_MOLAR_ENTHALPY, Decimal("9.6"), Decimal("2.4")),
        "sio2_partial_entropy": (None, Decimal("23.2"), Decimal("2.4")),
    }
    for name, (quantity, value, sigma) in expected.items():
        row = next(record for record in table4_rows if name in record.observation_id)
        if quantity is None:
            assert not row.identity.quantity.is_value
            assert row.value.kind is ValueKind.UNAVAILABLE
            parent_id = row.uncertainty.verbatim["parent"].rsplit("::", 1)[-1]
            assert source_rows[parent_id]["values"]["quantity"] == "partial_component_entropy"
            source_points = source_rows[parent_id]["values"]["points"]
            assert Decimal(str(source_points[0]["sigma"])) == sigma
        else:
            assert row.identity.quantity.value is quantity
            assert row.value.kind is ValueKind.POINT
            assert row.value.point == value
            assert Decimal(str(row.uncertainty.verbatim["sigma"])) == sigma
        assert row.point_conditions["composition"].state.is_value
        if quantity is Quantity.PARTIAL_MOLAR_ENTHALPY:
            assert row.identity.composition.is_value
            composition = row.identity.composition.value
            # Table 4 uses the same printed CaMgSi2O6 formula-unit amounts.
            assert composition.amount_basis is AmountBasis.MOL_INVENTORY
            assert dict(composition.components) == {
                "CaO": Decimal("1"),
                "MgO": Decimal("1"),
                "SiO2": Decimal("2"),
            }
        assert row.identity.temperature_K.is_unknown


def test_fidelity_pins_cover_table_values_and_fail_after_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    mutated = copy.deepcopy(document)
    points = mutated["species"]["Ca"]["observations"][0]["values"]["points"]
    points[0]["pressure_as_printed"] = "1.7 ± 0.2"
    assert check_all_fidelity_samples_match(mutated, label=str(EXTRACT))

    mutated_table4 = copy.deepcopy(document)
    points = mutated_table4["species"]["CaO"]["observations"][1]["values"]["points"]
    points[0]["sigma"] = 1.6
    assert check_all_fidelity_samples_match(mutated_table4, label=str(EXTRACT))


def test_context_records_pass_extract_schema_with_fidelity_matching():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))

    assert all(
        row["units"] == "K"
        for row in document["species"]["Ca"]["context"]
    )
    assert validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=EXTRACT.stem,
        check_fidelity_match=True,
    ) == []


def test_table1_preserves_temperature_widths_separately_from_pressure_widths():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_widths = {1566: 7, 1640: 3, 1688: 5, 1743: 8, 1778: 5, 1862: 4}
    table1_points = [
        point
        for entry in document["species"].values()
        for observation in entry.get("observations", [])
        if "_pressure_table1" in observation["observation_id"]
        for point in observation["values"].get("points", [])
    ]

    assert len(table1_points) == 50
    for point in table1_points:
        temperature = point["temperature_K"]
        width = expected_widths[temperature]
        assert point["temperature_uncertainty_K_as_printed"] == width
        assert point["temperature_as_printed"] == f"{temperature} ± {width} K"


def test_render_checked_values_and_printed_table_row_counts():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sample_paths = {
        "species.Ca.observations[shornikov_1997_ca_pressure_table1].values.points[0].pressure_as_printed": (
            "1.6 ± 0.2",
            2,
            "1",
        ),
        "species.O2.observations[shornikov_1997_o2_pressure_table1].values.points[5].pressure_as_printed": (
            "1860 ± 90",
            2,
            "1",
        ),
    }
    fidelity = {sample["path"]: sample for sample in document["fidelity_samples"]}
    for path, (expected, page, table) in sample_paths.items():
        assert path in fidelity
        assert str(fidelity[path]["value"]) == expected
        assert fidelity[path]["locator"]["page"] == page
        assert fidelity[path]["locator"]["table"] == table

    mgo_enthalpy = next(
        observation
        for observation in document["species"]["MgO"]["observations"]
        if observation["observation_id"]
        == "shornikov_1997_mgo_partial_enthalpy_table4"
    )
    assert mgo_enthalpy["locator"]["page"] == 3
    assert mgo_enthalpy["locator"]["table"] == "4"
    assert mgo_enthalpy["values"]["points"][0]["value_as_published"] == "−40.2 ± 2.6"

    mapping = yaml.safe_load(
        (ROOT / "data/literature/extracts/audits/shornikov-1997-diopside-vaporization.mapping.yaml").read_text(
            encoding="utf-8"
        )
    )
    row_counts = {table["table"]: table["printed_rows"] for table in mapping["tables"]}
    assert row_counts == {"1": 9, "2": 7, "3": 14, "4": 3}


def test_mgo_activity_uses_the_source_stated_pure_solid_reference():
    rows = [
        row
        for row in _migrated().observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and row.identity.species.formula == "MgO"
        and "shornikov_1997_mgo_activity_table3::" in row.observation_id
    ]

    assert len(rows) == 6
    for row in rows:
        reference = row.identity.reference_state
        assert reference is not None and reference.is_value
        standard_state = reference.value
        assert standard_state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert standard_state.endmember.formula == "MgO"
        assert standard_state.endmember.phase.is_value
        assert standard_state.endmember.phase.value is Phase.CR
        assert standard_state.endmember.polymorph is not None
        assert standard_state.endmember.polymorph.is_unknown
        assert standard_state.reference_pressure_bar is None


def test_table4_lineage_resolves_to_same_source_table3_activity_rows():
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
    migrator._retarget_exploded_parents()
    migrator.finalize()
    result = migrator.result

    table4_rows = [
        row for row in result.observations.values()
        if "partial_" in row.observation_id and "table4" in row.observation_id
    ]
    assert len(table4_rows) == 6
    for row in table4_rows:
        formula = row.identity.species.formula.lower()
        expected_prefix = f"shornikov_1997_{formula}_activity_table3"
        assert row.derived_from
        assert row.derivation is not None
        assert row.derived_from == row.derivation.inputs
        assert all(parent in result.observations for parent in row.derived_from)
        assert all(expected_prefix in parent for parent in row.derived_from)

    report = result.validation or validate_corpus(
        result.works, result.experiments, result.observations
    )
    lineage_references = [
        issue for issue in report.issues
        if issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and any(row.observation_id in issue.path for row in table4_rows)
        and (".derived_from" in issue.path or ".inputs" in issue.path)
    ]
    assert lineage_references == []

    source_issues = [
        issue for issue in report.issues
        if "shornikov-1997-diopside-vaporization" in issue.path
    ]
    assert not [
        issue for issue in source_issues
        if issue.reason in {
            RefusalReason.REFERENTIAL_INTEGRITY,
            RefusalReason.CONDITIONAL_FIELD,
        }
    ]
    source_rows = [
        row for row in result.observations.values()
        if row.observation_id.startswith(
            "shornikov-1997-diopside-vaporization::"
        )
    ]
    assets = {
        asset.asset_id
        for work in result.works.values()
        for asset in work.source_files.files
    }
    assert len(source_rows) == 91
    for row in source_rows:
        if row.derivation is None:
            continue
        if row.evidence.class_.is_value and row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED:
            assert not row.derived_from
            continue
        if not row.derived_from:
            assert row.derivation.raw_inputs is not None
            assert row.derivation.raw_inputs.state.value is RawInputStatus.NOT_PRINTED
            assert row.derivation.raw_inputs.locator is not None
            continue
        assert row.derived_from
        assert row.derived_from == row.derivation.inputs
        assert all(
            parent in result.observations or parent in assets
            for parent in row.derived_from
        )

    row = table4_rows[0]
    # tables:{source_id} is the corpus tables-directory asset once INDEX
    # records tables.exists. kems-ms2000-044 stores that asset as a real
    # derived_from parent, so it cannot be this check's unresolved token.
    phantom = "not-a-source-asset:shornikov-1997-diopside-vaporization"
    mutated_row = replace(
        row,
        derived_from=(phantom,),
        derivation=replace(row.derivation, inputs=(phantom,)),
    )
    mutated_observations = dict(result.observations)
    mutated_observations[row.observation_id] = mutated_row
    mutated_report = validate_corpus(
        result.works, result.experiments, mutated_observations
    )
    assert any(
        issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        and row.observation_id in issue.path
        and phantom in issue.detail
        for issue in mutated_report.issues
    )


def test_table1_scaled_pressure_widths_and_point_temperatures_reach_migration():
    _assert_table1_migrated_pressures(_migrated())


def test_mutating_consumed_pressure_atm_makes_numeric_regression_fail(tmp_path: Path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    ca_points = document["species"]["Ca"]["observations"][0]["values"]["points"]
    ca_points[0]["pressure_atm"] = 1.6e-9
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(AssertionError, match="shornikov_1997_ca_pressure_table1"):
        _assert_table1_migrated_pressures(_migrated(mutated_extract))
