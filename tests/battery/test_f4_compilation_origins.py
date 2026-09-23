"""F4 R-comp: nested compilation shards enter diagnostic_references.

Origins must recurse like load_migrated_store. Classification is by path under
compilations-*, not source_id prefix markers (robie-/hemingway- miss those).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    Phase,
    Quantity,
)
from simulator.battery.identity import Identity
from simulator.battery.records import (
    Admission,
    Evidence,
    Locator,
    Observation,
    Species,
    State,
    Uncertainty,
    UncertaintyKind,
    Value,
)
from simulator.battery.score import (
    ScoreContext,
    diagnostic_references,
    is_compilation_source,
    load_score_context,
)

def test_nested_usgs_origin_is_compilation_without_marker_prefix() -> None:
    origin = "compilations-robie-waldbaum-1968-usgs-b1259/b1259-298k-0001.yaml"
    source_id = "robie-waldbaum-1968-usgs-b1259"
    assert is_compilation_source(source_id, None) is False
    assert is_compilation_source(source_id, origin) is True


def test_flat_compilations_yaml_still_compiles() -> None:
    assert is_compilation_source("anything", "compilations-atct.yaml") is True


def test_non_compilation_origin_does_not_use_markers() -> None:
    # Origin present and not under compilations-* wins over source_id markers.
    assert is_compilation_source("janaf-4th", "kems_measurements.yaml") is False


def test_load_score_context_recurses_nested_compilation_shards(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    obs_dir = root / "data" / "literature" / "observations-v2"
    nested = obs_dir / "compilations-robie-waldbaum-1968-usgs-b1259"
    nested.mkdir(parents=True)
    oid = "robie-waldbaum-1968-usgs-b1259:rec:cp:T=298.15:col=cp"
    source_id = "robie-waldbaum-1968-usgs-b1259"
    shard = {"observations": [{"observation_id": oid}]}
    (nested / "b1259-fixture.yaml").write_text(yaml.safe_dump(shard), encoding="utf-8")

    species = Species("SiO2", Phase.CR)
    observation = Observation(
        observation_id=oid,
        experiment_id=f"{source_id}:rec:tabulation",
        identity=Identity(
            quantity=Quantity.CP,
            species=species,
            per=State.of(__import__("simulator.battery.enums", fromlist=["PerBasis"]).PerBasis.MOL_SPECIES),
            temperature_K=State.of(__import__("decimal").Decimal("298.15")),
        ),
        value=Value.point_of(__import__("decimal").Decimal("44.6")),
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        evidence=Evidence(class_=State.of(EvidenceClass.COMPILATION_ASSESSED)),
        admission=Admission(status=AdmissionStatus.PENDING, reason="fixture"),
        notices=(),
        source_id=source_id,
        locator=Locator(table="rec"),
        read_from=f"unknown:{source_id}",
    )

    import simulator.battery.score as score_mod

    monkeypatch.setattr(score_mod, "REPO_ROOT", root)
    monkeypatch.setattr(
        score_mod,
        "load_migrated_store",
        lambda _root=None: ({}, {}, {oid: observation}),
    )

    ctx = load_score_context(root)
    origin = ctx.origins.get(oid)
    assert origin == (
        "compilations-robie-waldbaum-1968-usgs-b1259/b1259-fixture.yaml"
    )
    assert is_compilation_source(source_id, origin) is True
    diag_ids = {o.observation_id for o in diagnostic_references(ctx)}
    assert oid in diag_ids


def test_mutation_path_gate_is_what_drives_nested_classification(monkeypatch) -> None:
    """Disabling path-under-compilations restores the pre-fix miss for nested USGS."""

    import simulator.battery.score as score_mod

    origin = "compilations-robie-waldbaum-1968-usgs-b1259/shard.yaml"
    source_id = "robie-waldbaum-1968-usgs-b1259"
    assert is_compilation_source(source_id, origin) is True
    monkeypatch.setattr(score_mod, "_origin_under_compilations", lambda _o: False)
    assert is_compilation_source(source_id, origin) is False
