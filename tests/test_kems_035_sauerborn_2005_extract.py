from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    Quantity,
    RelativeSeriesUnavailableReason,
    ValueKind,
)
from simulator.battery.records import (
    RelativeSeriesAnchor,
    RelativeSeriesClock,
    RelativeSeriesEvidence,
    RelativeSeriesPoint,
    Value,
)
from simulator.battery.score_metrics import relative_channel_point_residuals
from tests.battery import factories as F


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-035-sauerborn-2005.yaml"
OBSERVATION_ID = "sauerborn_2005_melts_liquidus_model_deep2"
UNKNOWN_METHOD_OBSERVATION_ID = "sauerborn_2005_melts_full_melt_model_1261_deep2"
INPUT_OBSERVATION_ID = "sauerborn_2005_jsc1_composition_figure_only_blocked_deep2"


def _assert_model_row(document: dict) -> None:
    row = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == OBSERVATION_ID
    )
    assert row["values"]["method_class"] == "model_derived"
    assert row["values"]["evidence_class"] == "model_derived"
    assert "attribution" not in row["values"]
    assert row["derived_from"] == [INPUT_OBSERVATION_ID]
    derivation = row["derivation"]
    assert "raw_inputs" not in derivation
    assert derivation["inputs"] == [INPUT_OBSERVATION_ID]
    assert "Eine Untersuchung mit dem Simulationsprogramm MELTS" in derivation["method"]["locator"]["note"]
    assert derivation["method"]["state"]["value"].startswith("MELTS simulation")
    assert derivation["method"]["locator"]["section"] == (
        "2.1.2 Mondstaub-Simulat JSC-1"
    )
    assert derivation["references"] == [
        "[melt 03] http://melts.uchicago.edu; Version ; 09/2003",
        "[MKCa 94] D. S. McKay, J. L. Carter, W. W. Boles, C. C. Allen, J. H. Allton; JSC-1: A New Lunar Soil Simulant; 1994",
    ]


def test_sauerborn_melts_liquidus_is_model_derived_and_mutation_sensitive():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_model_row(document)

    mutated = deepcopy(document)
    row = next(
        row
        for species in mutated["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == OBSERVATION_ID
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_model_row(mutated)


def _assert_1261_is_an_authors_estimate(document: dict) -> None:
    row = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == UNKNOWN_METHOD_OBSERVATION_ID
    )
    assert row["values"]["evidence_class"] == "author_estimate"
    assert row["values"]["method_class"] == "authors_estimate"
    assert row["values"]["liquidus_C"] == 1261
    assert "derived_from" not in row
    assert "derivation" not in row


def test_1261_theoretical_value_is_an_authors_estimate_not_a_quotation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_1261_is_an_authors_estimate(document)

    mutated = deepcopy(document)
    row = next(
        row
        for species in mutated["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == UNKNOWN_METHOD_OBSERVATION_ID
    )
    row["values"]["evidence_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_1261_is_an_authors_estimate(mutated)


def test_fig555_separate_oxide_curves_are_not_one_physical_run() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    raw = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "sauerborn_2005_fig555_o2_reference_normalized"
    )
    samples = raw["values"]["points_digitised"]
    assert {point["id"] for point in samples} == {
        "ohne_Probe",
        "CaO_peak",
        "SiO2_highT",
        "ZnO_plateau",
    }
    assert [point["id"] for point in samples if point["t_mmss"] == "1:20"] == [
        "SiO2_highT",
        "ZnO_plateau",
    ]
    source = tuple(
        RelativeSeriesPoint(
            run_id=point["id"],
            coordinate=Decimal(
                str(int(point["t_mmss"].split(":")[0]) * 60 + int(point["t_mmss"].split(":")[1]))
            ),
            value=Value(ValueKind.POINT, point=Decimal(str(point["p_O2_Pa"]))),
            observable="O2+",
            boundary="chamber-cross",
            coordinate_unit="s",
        )
        for point in samples
    )
    model = (
        RelativeSeriesPoint(
            run_id="oxide-reference-model",
            coordinate=Decimal(60),
            value=Value(ValueKind.POINT, point=Decimal("1e-5")),
            observable="O2",
            boundary="chamber-cross",
            coordinate_unit="s",
        ),
    )
    anchor = RelativeSeriesAnchor(
        source_coordinate=Decimal(60),
        model_coordinate=Decimal(60),
        source_event="acquisition-start",
        model_event="trajectory-start",
        physical_event="chamber-cross-sampling",
    )
    clock = RelativeSeriesClock(
        source_event=anchor.source_event,
        model_event=anchor.model_event,
        physical_event=anchor.physical_event,
        mapping_supported=True,
        locator=F.loc(page=105, figure="5.55"),
    )
    evidence = RelativeSeriesEvidence(
        quantity=Quantity.ION_INTENSITY,
        model_quantity=Quantity.P_PARTIAL,
        channel="O2+",
        target_species="O2",
        boundary="chamber-cross",
        target_isolated_or_interferences_corrected=None,
        background_treatment_supported=None,
        response_proportional_over_interval=None,
        locator=F.loc(page=105, figure="5.55"),
        oxygen_independent=True,
        model_run_id="oxide-reference-model",
        reference_run_id="oxide-reference-model",
        model_oxygen_input_observation_ids=(),
    )

    result = relative_channel_point_residuals(source, model, anchor, clock, evidence)

    assert result.unavailable_reason is RelativeSeriesUnavailableReason.MIXED_SOURCE_RUNS
    assert result.points == ()
