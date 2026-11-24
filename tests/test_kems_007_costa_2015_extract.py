"""Attribution pins for Costa and Jacobson's Piacente comparator series."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.records import (
    RelativeSeriesAnchor,
    RelativeSeriesClock,
    RelativeSeriesEvidence,
    RelativeSeriesPoint,
    Value,
)
from simulator.battery.score_metrics import relative_channel_point_residuals
from tests.battery import factories as F
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-007-costa-2015.yaml"
SOURCE_ID = "kems-007-costa-2015"
ATTRIBUTION = (
    "V. Piacente, J. Matoušek, M. Spoliti, and V. Di Paolo (1975), "
    "Silikaty 19 [4], 289–299, reference 5 in Costa and Jacobson (2015)."
)
OBSERVATION_IDS = {
    "costa_2015_fe_partial_pressure_re_piacente_comparator",
    "costa_2015_mg_partial_pressure_re_piacente_comparator",
}


def test_piacente_comparator_series_name_the_cited_work() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }

    assert set(rows) >= OBSERVATION_IDS
    for observation_id in OBSERVATION_IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"] == ATTRIBUTION


def test_isolated_migration_has_no_hard_issues() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()


def test_costa_fe_equilibration_trace_is_one_comparable_channel_series() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    raw = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "costa_2015_fe_time_equilibrium_mo_1669"
    )
    samples = raw["values"]["points"]
    run_id = "costa-2015:time_mo_1669:Fe+"
    source = tuple(
        RelativeSeriesPoint(
            run_id=run_id,
            coordinate=Decimal(str(point["elapsed_time_min"])),
            value=Value(ValueKind.POINT, point=Decimal(str(point["ion_intensity"]))),
            observable="Fe+",
            boundary="cell-exit",
            coordinate_unit="min",
        )
        for point in samples
    )
    first_time = Decimal(str(samples[0]["elapsed_time_min"]))
    model = tuple(
        RelativeSeriesPoint(
            run_id=run_id,
            coordinate=Decimal(str(point["elapsed_time_min"])) * 60,
            value=Value(ValueKind.POINT, point=Decimal("1")),
            observable="Fe",
            boundary="cell-exit",
            coordinate_unit="s",
        )
        for point in samples
    )
    anchor = RelativeSeriesAnchor(
        source_coordinate=first_time,
        model_coordinate=first_time * 60,
        source_event="first-positive-sample",
        model_event="trajectory-start",
        physical_event="sampled-channel-start",
    )
    clock = RelativeSeriesClock(
        source_event=anchor.source_event,
        model_event=anchor.model_event,
        physical_event=anchor.physical_event,
        mapping_supported=False,
        locator=None,
    )
    evidence = RelativeSeriesEvidence(
        quantity=Quantity.ION_INTENSITY,
        model_quantity=Quantity.P_PARTIAL,
        channel="Fe+",
        target_species="Fe",
        boundary="cell-exit",
        target_isolated_or_interferences_corrected=None,
        background_treatment_supported=None,
        response_proportional_over_interval=None,
        locator=F.loc(page=8, figure="Time to Equilibration"),
        model_run_id=run_id,
        reference_run_id=run_id,
    )

    result = relative_channel_point_residuals(source, model, anchor, clock, evidence)

    assert result.unavailable_reason is None
    assert len(result.points) == len(samples)
    assert all(point.unavailable_reason is None for point in result.points)
