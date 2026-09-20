from __future__ import annotations

import collections.abc
import importlib.util
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

import pytest
import yaml

from simulator.battery.enums import (
    BenchAbsenceReason,
    BenchIdentityBasis,
    Quantity,
    ValueKind,
)
from simulator.battery.migrate import (
    _geometry_from_plain,
    _thermal_schedule_from_plain,
)
from simulator.battery.records import (
    Apparatus,
    ApparatusGeometry,
    Bench,
    BenchIdentity,
    BenchReference,
    Experiment,
    FO2Control,
    Located,
    Locator,
    PressureEnvironment,
    Sample,
    State,
    ThermalSchedule,
    Value,
)
from simulator.battery.validity import underdetermined_apparatus
from simulator.battery.validate import ValidationReport, validate_corpus
from tests.battery import factories
from tests.battery.factories import kems_experiment

ROOT = Path(__file__).resolve().parents[2]
VOCAB_PATH = ROOT / "data" / "literature" / "lab_parameter_vocabulary.yaml"
VALIDATOR_PATH = ROOT / "tools" / "validate_literature_extracts.py"


def _vocabulary() -> list[dict[str, object]]:
    payload = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))
    return list(payload["entries"])


def _unwrap_optional(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = [item for item in get_args(annotation) if item is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _target_resolves(target: str) -> bool:
    roots = {
        "apparatus": Apparatus,
        "bench": Bench,
        "experiment": Experiment,
        "pressure_environment": PressureEnvironment,
        "sample": Sample,
    }
    parts = target.split(".")
    current = roots.get(parts[0])
    if current is None:
        return False
    for index, name in enumerate(parts[1:]):
        hints = get_type_hints(current)
        if name not in hints:
            return False
        annotation = _unwrap_optional(hints[name])
        if index == len(parts) - 2:
            return True
        origin = get_origin(annotation) or annotation
        if isinstance(origin, type) and is_dataclass(origin):
            current = origin
            continue
        args = get_args(annotation)
        if origin in (tuple, list) and args:
            member = _unwrap_optional(args[0])
            if isinstance(member, type) and is_dataclass(member):
                current = member
                continue
        if isinstance(origin, type) and issubclass(origin, collections.abc.Mapping):
            return True
        return False
    return True


def _leaf_paths(record_type: type, prefix: str) -> set[str]:
    paths: set[str] = set()
    for item in fields(record_type):
        annotation = _unwrap_optional(get_type_hints(record_type)[item.name])
        origin = get_origin(annotation) or annotation
        path = f"{prefix}.{item.name}"
        if isinstance(origin, type) and is_dataclass(origin) and origin not in {
            Located,
            Locator,
            State,
            Value,
        }:
            paths.update(_leaf_paths(origin, path))
        elif origin in (tuple, list) and get_args(annotation):
            member = _unwrap_optional(get_args(annotation)[0])
            if isinstance(member, type) and is_dataclass(member):
                paths.update(_leaf_paths(member, path))
            else:
                paths.add(path)
        else:
            paths.add(path)
    return paths


def _pressure_validation_report(
    pressure: Decimal | Value, identity_pressure: Decimal
) -> ValidationReport:
    experiment = factories.tabulation_experiment()
    experiment = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=factories.located(pressure),
        ),
    )
    observation = factories.observation(
        "pressure-reconciliation",
        experiment.experiment_id,
        factories.activity_identity(total_P=identity_pressure),
        Decimal("0"),
    )
    observation = replace(
        observation,
        point_conditions={
            key: value
            for key, value in observation.point_conditions.items()
            if key != "total_pressure_Pa"
        },
    )
    return validate_corpus([factories.work()], [experiment], [observation])


def test_every_bench_field_has_a_vocabulary_row() -> None:
    targets = {str(row["field"]) for row in _vocabulary()}
    required = _leaf_paths(Bench, "bench")
    required.update(_leaf_paths(ThermalSchedule, "experiment.thermal_schedule"))
    required.update(
        {
            "experiment.bench_id",
            "experiment.fO2_control.oxygen_partial_pressure_Pa",
        }
    )
    missing_sample_fields = {
        item.name
        for item in fields(Sample)
        if not {
            f"sample.{item.name}",
            f"experiment.sample.{item.name}",
        }
        & targets
    }
    assert not missing_sample_fields, (
        "sample fields without vocabulary rows: "
        f"{sorted(missing_sample_fields)}"
    )
    assert required <= targets, f"bench fields without vocabulary rows: {sorted(required - targets)}"


def test_every_vocabulary_target_resolves_to_records_field() -> None:
    unresolved = sorted(
        str(row["field"])
        for row in _vocabulary()
        if not _target_resolves(str(row["field"]))
    )
    assert not unresolved, f"vocabulary targets absent from records.py: {unresolved}"


def test_validator_allowed_set_equals_vocabulary_printed_names() -> None:
    spec = importlib.util.spec_from_file_location("validate_literature_extracts", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    printed = frozenset(str(row["printed"]) for row in _vocabulary())
    assert module.EQUIPMENT_FIELDS == printed


def test_bench_identity_requires_citation_text() -> None:
    assert {item.value for item in BenchIdentityBasis} == {
        "described_in_this_work",
        "cited_by_author",
    }
    with pytest.raises(ValueError, match="cited_as"):
        BenchIdentity(BenchIdentityBasis.CITED_BY_AUTHOR)
    with pytest.raises(ValueError, match="cited_as"):
        BenchIdentity(
            BenchIdentityBasis.CITED_BY_AUTHOR,
            BenchReference(None, "", (), Locator(page=1)),
        )
    with pytest.raises(ValueError, match="BenchReference"):
        BenchIdentity(
            BenchIdentityBasis.CITED_BY_AUTHOR,
            object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="locator"):
        BenchReference(None, "Smith (1990)", (), None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="BenchIdentity"):
        Bench(
            id="w::bench::one",
            work_id="w",
            identity=None,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("reason", (None, 0, False, "", "   "))
def test_state_absence_requires_a_real_string_reason(reason: object) -> None:
    with pytest.raises(ValueError, match="reason"):
        State.unknown(reason)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reason"):
        State.not_applicable(reason)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            {
                "ramps": [
                    {
                        "start_temperature_K": {
                            "state": {"tag": "value", "value": "300"}
                        }
                    }
                ]
            },
            "ramp requires rate_K_s",
        ),
        (
            {
                "setpoints_and_holds": [
                    {
                        "hold_duration_s": {
                            "state": {"tag": "value", "value": "600"}
                        }
                    }
                ]
            },
            "setpoint requires temperature_K",
        ),
        (
            {
                "points": [
                    {"time_s": {"state": {"tag": "value", "value": "10"}}}
                ]
            },
            "point requires time_s and temperature_K",
        ),
    ),
)
def test_incomplete_thermal_entries_are_rejected(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _thermal_schedule_from_plain(payload)


@pytest.mark.parametrize("payload", ([], "schedule", 1))
def test_nonmapping_thermal_schedule_is_rejected(payload: object) -> None:
    with pytest.raises(TypeError, match="thermal schedule must be a mapping"):
        _thermal_schedule_from_plain(payload)


def test_point_pressure_reconciles_with_decimal_identity() -> None:
    report = _pressure_validation_report(
        Decimal("0.001"),
        Decimal("0.001"),
    )
    assert report.ok, report.hard_issues


@pytest.mark.parametrize(
    "pressure",
    (
        Value(
            ValueKind.BOUND,
            bound_operator="<",
            bound_value=Decimal("0.002"),
        ),
        Value(
            ValueKind.INTERVAL,
            interval_low=Decimal("0.0005"),
            interval_high=Decimal("0.002"),
        ),
    ),
)
def test_nonpoint_pressure_is_not_collapsed_for_identity_reconciliation(
    pressure: Value,
) -> None:
    report = _pressure_validation_report(
        pressure,
        Decimal("0.001"),
    )
    assert report.ok, report.hard_issues


@pytest.mark.parametrize(
    "pressure",
    (
        Value(
            ValueKind.BOUND,
            bound_operator="<",
            bound_value=Decimal("0.002"),
        ),
        Value(
            ValueKind.INTERVAL,
            interval_low=Decimal("0.0005"),
            interval_high=Decimal("0.002"),
        ),
    ),
)
def test_nonpoint_pressure_rejects_identity_outside_evidence(
    pressure: Value,
) -> None:
    report = _pressure_validation_report(
        pressure,
        Decimal("0.003"),
    )
    assert any(
        issue.path.endswith("identity.total_pressure_Pa")
        and issue.detail
        == "identity disagrees with Experiment conditions / point_conditions"
        for issue in report.hard_issues
    )


def test_pressure_identity_reconciliation_rejects_unknown_bound_operator() -> None:
    report = _pressure_validation_report(
        Value(
            ValueKind.BOUND,
            bound_operator="about",
            bound_value=Decimal("0.002"),
        ),
        Decimal("0.001"),
    )
    assert any(
        issue.path.endswith("identity.total_pressure_Pa")
        and issue.detail == "unsupported pressure bound operator 'about'"
        for issue in report.hard_issues
    )


def test_bench_absence_reason_is_closed() -> None:
    with pytest.raises(ValueError, match="absence reason"):
        Bench(
            id="w::bench::one",
            work_id="w",
            identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
            apparatus_family=Located(State.unknown("free text")),
        )
    Bench(
        id="w::bench::one",
        work_id="w",
        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
        apparatus_family=Located(State.unknown(BenchAbsenceReason.NOT_PUBLISHED)),
    )


def test_legacy_bare_geometry_decimal_loads_as_point() -> None:
    geometry = _geometry_from_plain(
        {"orifice_area_m2": {"state": {"tag": "value", "value": "1e-6"}}}
    )
    assert geometry is not None and geometry.orifice_area_m2 is not None
    value = geometry.orifice_area_m2.state.value
    assert isinstance(value, Value)
    assert value.kind is ValueKind.POINT
    assert value.point == Decimal("1e-6")


def test_value_approximate_defaults_false_and_records_nominal_form() -> None:
    exact = Value.point_of("2")
    approximate = Value(ValueKind.POINT, point=Decimal("2"), approximate=True)
    assert exact.approximate is False
    assert approximate.approximate is True


@pytest.mark.parametrize(
    "area",
    (
        Value(
            ValueKind.BOUND,
            bound_operator="<",
            bound_value=Decimal("1e-6"),
        ),
        Value(
            ValueKind.INTERVAL,
            interval_low=Decimal("1e-7"),
            interval_high=Decimal("1e-6"),
        ),
    ),
)
def test_effusion_nonpoint_area_is_not_collapsed_to_usable_point(area: Value) -> None:
    experiment = kems_experiment()
    assert experiment.apparatus is not None
    geometry = ApparatusGeometry(
        orifice_area_m2=Located(
            State.of(area)
        ),
        clausing_factor=Located(State.of(Value.point_of("0.9"))),
    )
    experiment = replace(
        experiment,
        apparatus=replace(experiment.apparatus, geometry=geometry),
    )
    outcome = underdetermined_apparatus(experiment, Quantity.P_SAT)
    assert not outcome.passed
    assert "orifice_area_m2" in outcome.checks[-1].detail["missing"]
