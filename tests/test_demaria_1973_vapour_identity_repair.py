"""Source pins for De Maria and Piacente (1973) Table I TiO rows."""

from decimal import Decimal
from pathlib import Path
import sys

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator, REPO_ROOT

TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as validator  # noqa: E402


EXTRACT = REPO_ROOT / "data/literature/extracts/kems-023-demaria-1973.yaml"


def test_tio_rows_keep_printed_identity_and_leave_unprinted_bulk_unknown():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row_ids = (
        "demaria_1973_tio_psat_table1_this_work",
        "demaria_1973_tio_psat_table1_this_work_12073",
    )
    rows = [
        next(
            row
            for row in document["species"]["TiO"]["observations"]
            if row["observation_id"] == row_id
        )
        for row_id in row_ids
    ]
    this_work = rows[0]
    assert this_work["values"]["sample"] == "10017"
    assert this_work["values"]["caveat"] == (
        "bulk composition not printed; requested on fetch list"
    )
    assert "sample_oxide_composition_wt_pct" not in this_work["values"]
    assert rows[1]["values"]["sample"] == "12073"
    assert rows[1]["values"]["caveat"] == (
        "bulk composition not printed; requested on fetch list"
    )
    assert "sample_oxide_composition_wt_pct" not in rows[1]["values"]

    # The rendered Table I prints these two 10017 TiO pressures at 2100 and 2200 K.
    assert [point["p_atm"] for point in this_work["values"]["series"]] == [
        1.10e-07,
        5.20e-07,
    ]
    assert [point["T_K"] for point in this_work["values"]["series"]] == [
        2100.0,
        2200.0,
    ]

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert not validator.validate_extract_document(
        document, expected_source_id="kems-023-demaria-1973"
    )

    migrated = sorted(
        (
            record
            for record in result.observations.values()
            if "demaria_1973_tio_psat_table1_this_work::" in record.observation_id
            and record.identity.quantity.value is Quantity.P_PARTIAL
        ),
        key=lambda record: record.identity.temperature_K.value,
    )
    assert [record.identity.temperature_K.value for record in migrated] == [
        Decimal("2100.0"),
        Decimal("2200.0"),
    ]
    assert all(
        record.identity.species.formula == "TiO"
        and record.identity.species.phase.is_value
        and record.identity.species.phase.value.value == "g"
        and record.identity.composition.is_unknown
        for record in migrated
    )
