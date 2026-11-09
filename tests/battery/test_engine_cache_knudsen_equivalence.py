from __future__ import annotations

import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from simulator.battery.enums import AmountBasis, Engine, MethodToken, Phase, Quantity, Rail
from simulator.battery.records import (
    Admission,
    AdmissionStatus,
    Composition,
    Evidence,
    EvidenceClass,
    Experiment,
    ExperimentKind,
    FlowRegime,
    Identity,
    Located,
    Observation,
    PressureEnvironment,
    Sample,
    Species,
    State,
    ThermalSchedule,
    Uncertainty,
    UncertaintyKind,
    Value,
)
from simulator.battery.score import ScoreContext, _score_store_to_jsonl
from simulator.engine_cache import EngineCache
from tests.battery.test_engine_cache_equivalence import _canonical_residual_rows
from tests.battery import factories as F


ROOT = Path(__file__).resolve().parents[2]


def test_scored_knudsen_residue_uses_the_cache_off_cold_and_warm(
    tmp_path: Path, monkeypatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    composition = Composition(
        "synthetic",
        (("FeO", Decimal("0.5")), ("MgO", Decimal("0.5"))),
        AmountBasis.MOLE_FRACTION,
    )
    locator = F.loc(table="2", published_page=510)
    reference = Observation(
        "residue",
        "experiment",
        Identity(
            Quantity.RESIDUE_COMPONENT_COMPOSITION,
            Species("FeO", Phase.L),
            subtype=State.of("oxide_wt_percent"),
            temperature_K=State.of(Decimal(2100)),
            composition=State.of(composition),
        ),
        Value.point_of(99),
        Uncertainty(UncertaintyKind.NONE),
        Evidence(State.of(EvidenceClass.MEASURED_DIRECT)),
        Admission(AdmissionStatus.ADMITTED, "synthetic anchor"),
        (),
        source_id="synthetic",
        locator=locator,
    )
    mass_rows = tuple(
        replace(
            reference,
            observation_id=f"mass-{temperature}",
            identity=replace(
                reference.identity,
                quantity=Quantity.MASS_LOSS_FRACTION,
                temperature_K=State.of(Decimal(temperature)),
            ),
            value=Value.point_of(loss),
        )
        for temperature, loss in ((2000, "0.1"), (2100, "0.2"))
    )
    experiment = Experiment(
        "experiment",
        ExperimentKind.LITERATURE,
        State.of(MethodToken.KNUDSEN_EFFUSION),
        Sample(initial_composition=Located(State.of(composition), locator)),
        {
            "temperature_K": Located(
                State.unknown("point temperatures are printed per row"), locator
            )
        },
        PressureEnvironment(
            Located(State.unknown("not published"), locator),
            Located(State.unknown("no sweep"), locator),
            FlowRegime(State.unknown("not published")),
        ),
        thermal_schedule=ThermalSchedule(
            method=Located(State.of("70 K steps, 15 minutes each"), locator)
        ),
    )
    context = ScoreContext(
        works={},
        experiments={experiment.experiment_id: experiment},
        observations={
            row.observation_id: row for row in (*mass_rows, reference)
        },
        root=ROOT,
    )
    monkeypatch.setitem(
        sys.modules,
        "openimcc",
        SimpleNamespace(load_gas_datapack=lambda: object()),
    )
    monkeypatch.setattr(
        battery,
        "_openimcc_gas_channels_and_omission_notices",
        lambda *_args: (
            [("Fe", ("FeO", 1.0, 0.5)), ("O2", (None, 1.0, -1.0))],
            (),
        ),
    )
    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    calls = 0

    def evaluate_pressure(**_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(vapor_pressures_Pa={"Fe": 1.0e-8}, diagnostics={})

    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", evaluate_pressure
    )

    def score(label: str, cache: EngineCache | None) -> tuple[bytes, object]:
        path = tmp_path / f"{label}.jsonl"
        _row_count, metadata = _score_store_to_jsonl(
            context,
            path,
            root=ROOT,
            engines=(Engine.INTERNAL_ANALYTICAL,),
            rail=Rail.RESIDUE_COMPOSITION,
            limit=1,
            include_diagnostics=False,
            engine_cache=cache,
        )
        return (
            _canonical_residual_rows(path.with_name(path.name + ".partial")),
            metadata.engine_cache_counters,
        )

    off, _off_counts = score("off", None)
    cache_path = tmp_path / "knudsen-residue.sqlite"
    cold, cold_counts = score("cold", EngineCache(cache_path))
    calls_after_cold = calls
    warm, warm_counts = score("warm", EngineCache(cache_path))

    assert off
    assert cold == warm == off
    assert calls_after_cold > 0
    assert calls == calls_after_cold
    assert cold_counts is not None
    assert cold_counts[Engine.INTERNAL_ANALYTICAL.value]["calls_by_kind"][
        "residue_vapor_pressure"
    ] > 0
    assert warm_counts is not None
    assert not any(
        warm_counts[Engine.INTERNAL_ANALYTICAL.value]["calls_by_kind"].values()
    )
