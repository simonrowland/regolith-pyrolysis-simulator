from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    MEASURED_EVIDENCE,
    Phase,
    PhaseRole,
    Quantity,
    ValueKind,
)
from simulator.battery.migrate import REPO_ROOT, select_declared_source
from tests.battery.factories import migrate_one_extract
from tests.battery.test_migrate import assert_extract_source_mutation_fails


SOURCE_ID = "saito-2024-fe-phosphate-solid-solution"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, body in document["species"].items()
        for row in body.get("observations", [])
    }


def _assert_render_pins() -> None:
    document = _document()
    rows = _rows(document)
    assert len(rows) == 76
    assert sum(row["locator"].get("table") == "2" for _, row in rows.values()) == 4
    assert sum(row["locator"].get("table") == "3" for _, row in rows.values()) == 45
    assert sum(row["locator"].get("table") == "4" for _, row in rows.values()) == 27

    expected = {
        "saito24_t2_b_a_feo": ("FeO", "activity", "0.058", 4, "2", "B"),
        "saito24_t3_p_b_9p57_average": (
            "P", "value", "0.00421", 5, "3", "B, pH2/pH2O=9.57, average"
        ),
        "saito24_t4_b_a_p2o5": (
            "P2O5", "activity", "-21.09", 7, "4", "B"
        ),
        "saito24_t4_d_a_c3p": (
            "Ca3P2O8", "activity", "-2.67", 7, "4", "D"
        ),
    }
    for observation_id, (species, field, value, page, table, row_label) in expected.items():
        container, row = rows[observation_id]
        assert container == species
        assert row["values"][field] == value
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["locator"]["row"] == row_label

    # These are four independent printed cells: Table 2 activity, Table 3
    # mean phosphorus, and Table 4 activities. The table layouts were checked
    # against the page renders; Table 3 and Table 4 values are on physical
    # pages 5 and 7, respectively.
    fidelity = {
        (row["observation_id"], row["field"], row["value"], row["locator"]["page"])
        for row in document["fidelity_samples"]
    }
    assert {
        ("saito24_t2_b_a_feo", "activity", "0.058", 4),
        ("saito24_t3_p_b_9p57_average", "value", "0.00421", 5),
        ("saito24_t4_b_a_p2o5", "activity", "-21.09", 7),
        ("saito24_t4_d_a_c3p", "activity", "-2.67", 7),
    } <= fidelity


def test_render_pins_and_printed_table_row_counts() -> None:
    _assert_render_pins()


def test_render_pin_fails_if_a_stored_source_cell_changes(
    tmp_path, monkeypatch
) -> None:
    assert_extract_source_mutation_fails(
        EXTRACT,
        "activity: '0.058'",
        "activity: '0.059'",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=_assert_render_pins,
    )


def test_isolated_migration_preserves_values_conditions_and_identity_axes() -> None:
    document = _document()
    source_rows = _rows(document)
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if row.source_id == SOURCE_ID
    }

    assert len(migrated) == 76
    assert migrator.result.validation.hard_issues == ()
    assert migrator.result.registry_issues == []
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 0

    # Every row receives the printed temperature and its table locator. No
    # absolute fO2 or total pressure is printed in this work; gas ratios stay
    # on fO2_control and the identity axes remain unknown.
    for observation_id, (_species, raw) in source_rows.items():
        row = migrated[observation_id]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1573")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1573")
        assert temperature.locator is not None
        assert temperature.locator.page == raw["locator"]["page"]
        assert temperature.locator.table == raw["locator"]["table"]
        assert row.identity.total_pressure_Pa is not None
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is not None
        assert row.identity.fO2_Pa.is_unknown
        assert "fO2_control" in row.point_conditions
        total_pressure = row.point_conditions.get("total_pressure_Pa")
        if total_pressure is not None:
            assert total_pressure.state.is_unknown

        table = raw["locator"]["table"]
        if table == "2":
            assert row.point_conditions["fO2_control"].state.is_unknown
            assert row.identity.species.phase.is_value
            assert row.identity.species.phase.value is Phase.CR
        elif table == "3":
            printed = row.point_conditions["printed_composition"]
            assert printed.state.is_value
            assert printed.locator is not None
            assert printed.locator.page == 5 and printed.locator.table == "3"
            assert row.identity.composition is not None
            assert row.identity.composition.is_unknown
            control = row.point_conditions["fO2_control"].state.value
            if "gas_ratio" in control:
                assert control["gas_ratio"]["numerator_species"] == "H2"
                assert control["gas_ratio"]["denominator_species"] == "H2O"
                assert control["gas_ratio"]["value"] == raw["point_conditions"]["fO2_control"]["state"]["value"]["gas_ratio"]["value"]
        else:
            assert row.identity.species.phase.is_value
            assert row.identity.species.phase.value is Phase.CR
            gas = row.point_conditions["fO2_control"].state.value
            assert gas["channel"]["value"] == "gas_mix"
            assert gas["gas_ratio_values"]

    # Pin Table 2 and Table 4 compositions to their printed solid-solution
    # mole fractions, through the identity path used by the activity profile.
    for observation_id, expected_composition, page, table in (
        (
            "saito24_t2_b_a_feo",
            {"CaO": "0.624", "FeO": "0.016", "SiO2": "0.238", "PO2.5": "0.122"},
            4,
            "2",
        ),
        (
            "saito24_t4_b_a_p2o5",
            {"CaO": "0.624", "FeO": "0.016", "SiO2": "0.238", "PO2.5": "0.122"},
            7,
            "4",
        ),
        (
            "saito24_t4_d_a_c3p",
            {"CaO": "0.603", "FeO": "0.033", "SiO2": "0.235", "PO2.5": "0.129"},
            7,
            "4",
        ),
    ):
        row = migrated[observation_id]
        composition = row.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.as_map() == {
            formula: Decimal(value) for formula, value in expected_composition.items()
        }
        locator = row.point_conditions["composition"].locator
        assert locator is not None
        assert locator.page == 4 and locator.table == "2"
        assert row.locator.page == page and row.locator.table == table

    # Table 3 values are mass percentages, so their migrated values remain on
    # the wt_percent scale; the canonical unit token is still exactly "%".
    p_average_raw = source_rows["saito24_t3_p_b_9p57_average"][1]
    p_average = migrated["saito24_t3_p_b_9p57_average"]
    assert p_average.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    assert p_average.identity.phase_role.value is PhaseRole.MELT
    assert p_average.identity.species.phase.value is Phase.L
    assert p_average.identity.subtype.value == "wt_percent"
    assert p_average_raw["units"] == "%"
    assert p_average_raw["values"]["component_amount_as_published"] == "0.00421 [%P]Cu"
    assert p_average.value.kind is ValueKind.POINT
    assert p_average.value.point == Decimal("0.00421")
    assert p_average.point_conditions["printed_composition"].state.value["components"] == [
        ["Fe", "3.12"], ["P", "0.00421"]
    ]

    # The log-form activity is normalized by the production resolver, while
    # the extract pins the exact printed log value and page/table locator.
    for observation_id in ("saito24_t4_b_a_p2o5", "saito24_t4_d_a_c3p"):
        _species, raw = source_rows[observation_id]
        row = migrated[observation_id]
        selected = select_declared_source(Quantity.ACTIVITY, raw["units"], raw["values"])
        assert selected.value.kind is ValueKind.POINT
        assert row.value == selected.value
        assert row.locator.page == 7 and row.locator.table == "4"

    assert migrated["saito24_t2_b_a_feo"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t3_p_b_9p57_run1"].evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    assert migrated["saito24_t3_p_b_9p57_average"].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert migrated["saito24_t3_fe_b_9p57_equilibrium"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t3_hp_b_9p57"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t4_a_gamma_feo"].evidence.class_.value is EvidenceClass.AUTHOR_ESTIMATE
    assert migrated["saito24_t4_a_a_cao"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t4_b_a_p2o5"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t4_b_gamma_feo"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert migrated["saito24_t4_b_a_cao"].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED

    assert migrated["saito24_t3_p_b_9p57_average"].evidence.class_.value in MEASURED_EVIDENCE
    for observation_id in (
        "saito24_t3_fe_b_9p57_equilibrium",
        "saito24_t3_hp_b_9p57",
        "saito24_t4_a_gamma_feo",
        "saito24_t4_b_a_p2o5",
        "saito24_t4_b_gamma_feo",
        "saito24_t4_b_a_cao",
    ):
        assert migrated[observation_id].evidence.class_.value not in MEASURED_EVIDENCE

    # Every lineage parent is one of the stored observations. The unsupported
    # alpha-prime C2S standard state is retained as a rejected typed refusal.
    for observation_id, (_species, raw) in source_rows.items():
        for parent in raw.get("derived_from", []):
            assert parent in source_rows, (observation_id, parent)
    c2s = [
        row for row in migrated.values()
        if row.identity.species.formula == "Ca2SiO4"
    ]
    assert len(c2s) == 4
    assert all(row.admission.status is AdmissionStatus.REJECTED for row in c2s)
    assert all("alpha-prime C2S" in row.admission.reason for row in c2s)


def test_measured_reduced_rows_have_only_measured_ancestors() -> None:
    rows = _rows(_document())
    evidence_classes = {
        observation_id: row["values"]["evidence_class"]
        for observation_id, (_species, row) in rows.items()
    }
    assert all(
        row["values"]["method_class"] == evidence_classes[observation_id]
        for observation_id, (_species, row) in rows.items()
    )
    assert Counter(evidence_classes.values()) == Counter(
        {
            "measured_direct": 24,
            "measured_reduced": 7,
            "quoted_attributed": 44,
            "author_estimate": 1,
        }
    )

    measured_classes = {evidence.value for evidence in MEASURED_EVIDENCE}
    for observation_id, (_species, row) in rows.items():
        if evidence_classes[observation_id] != EvidenceClass.MEASURED_REDUCED.value:
            continue
        seen: set[str] = set()
        pending = list(row.get("derived_from", []))
        while pending:
            ancestor_id = pending.pop()
            assert ancestor_id in rows, (observation_id, ancestor_id)
            if ancestor_id in seen:
                continue
            seen.add(ancestor_id)
            assert evidence_classes[ancestor_id] in measured_classes, (
                observation_id,
                ancestor_id,
                evidence_classes[ancestor_id],
            )
            pending.extend(rows[ancestor_id][1].get("derived_from", []))

    assert EvidenceClass.QUOTED_ATTRIBUTED not in MEASURED_EVIDENCE
    assert EvidenceClass.AUTHOR_ESTIMATE not in MEASURED_EVIDENCE
