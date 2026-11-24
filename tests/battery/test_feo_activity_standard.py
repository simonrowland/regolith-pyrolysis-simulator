"""FeO activities are converted once to the scorer's JANAF Fe-019 standard."""

from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from importlib import resources
from pathlib import Path

import pytest

import simulator.battery.feo_standard as feo_standard_module
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.records import Composition, Execution
from simulator.battery.migrate import REPO_ROOT, Migrator
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    dumps_residual_line,
)
import simulator.battery.score as score_module
from tests.battery import factories as F


_RESIDUAL_SHA256_BEFORE_STANDARD_STEP = {
    Engine.INTERNAL_ANALYTICAL: (
        "61b0c0bb1105c233265d2e0175899f2c28bb0db324798c8e850025c8864acac0"
    ),
    Engine.OPENIMCC: (
        # 23d00bd8a218fca557c7b060458ceaaef1494017a76ee700dcee7ee9b7d6ea74
        # -> 339615442bf590ac233418e761c08ad6540bdacc13452bb9d8d87c471aa92cb6.
        # The only serialized change is the condensate-table identity
        # coefficient source (b9c49790... -> 493c67d5...). The value is unchanged.
        "339615442bf590ac233418e761c08ad6540bdacc13452bb9d8d87c471aa92cb6"
    ),
}
# Green's OpenIMCC zero-offset row used default d066-v1 (version 1.0.3-d066-v1,
# digest 02c88c04fe09f8f494adda8a76e1f95fee536d11b2b6d66639aaea097fce5e75).
# The default d066-v4 pack has a nonzero DG46-MASSON65 FeO offset (recorded
# at 979adb2; no redox-pack file changes through bae51f7). The pinned row
# is 10,240.4790568 J/mol at 1600 K, so this zero-offset witness
# uses published-core 1.0.2 (digest
# ae979b68295f6e653fb1c37d6b381f06cb0be8d4c5b421e673420a6e4596357c), whose
# FeO standard is Fe-019 with exactly zero shift. The residual value is
# unchanged. Serialized changes already in 23d00bd8 are coefficient_sources[0]
# (pack version 1.0.3-d066-v1 -> 1.0.2), coefficient_sources[1] and [3]
# (pack/melt digest 02c88c04... -> ae979b68...), and the
# candidate_observation.provenance.feo_standard_normalisation record:
# from_standard, to_standard, melt_pack_identity, delta_g_j_mol, log10_shift,
# and temperature_K. The candidate and top-level notices remain empty; there
# is no engine-model caveat notice for a zero shift.
# b217 pinned the N_Fe2+^0 multiply:
# sha 3b1518942a5ca7524b6e4be8e2c98fd375db44c5835bd830a4acff0a54e32ce0
# dex -0.013180332034779389
# The offer frame is a_D * f from the Darken & Gurry factor.
# Three-way pin comparison: car 4018b707..., b274 700e6d2b..., merged
# a5830affe765585c7d55dcf72aaa0e388550c33e2e22df7cf287c6ac93cbc1f0.
# b274 changes the first notice from the car's d-056 admission_defaulted
# wording to its DP-106 method_unstated policy notice. The merged notices
# combine both parents' FeO provenance: the merge-only iron-saturated FeO
# calibration notice at [8] comes from b274's Fe-saturation path applied to
# the car's notice set (it appears at car notices[7]). The hematite
# notice for the FeO1.5(l) 1500-1735 K supercooled branch appears at index 9
# in both parents and index 10 in the merge because of that shifted notice
# sequence. That warning is physically appropriate for the stated branch;
# it describes the metastable interval below the hematite decomposition
# marker, not the Distin measurement at 1880 K.
# The digest covers that merged notice set, the active OpenIMCC redox-pack
# identity, and FeO standard-normalization provenance as well as the residual.
_DISTIN_1880_RESIDUAL_SHA256 = (
    "a5830affe765585c7d55dcf72aaa0e388550c33e2e22df7cf287c6ac93cbc1f0"
)
# Green (OpenIMCC 66ec4a3) pinned this residual at -0.10057837869175247 dex.
# The 9c327c2 repin (series commit 5c670398e0) moved it by -0.0047345491326921
# dex to -0.10531292782444457. At 2153.15 K, the v4 MELT expression gives
# 27778.965828 - 10.961554232*2153.15 = 4177.0953 J/mol, or +0.1013328 dex
# (unrounded +0.10133284297452116) against R*T*ln(10). The final pin is
# -0.003980084849923409 dex, a net +0.09659829384182906 dex from green.
_DISTIN_1880_RESIDUAL_DEX = Decimal("-0.003980084849923409")


def _openimcc_sources(
    *, pack_version: str, pack_digest: str, condensate_digest: str
) -> tuple[str, ...]:
    return (
        f"openimcc-pack-version:{pack_version}",
        f"openimcc-pack-digest:{pack_digest}",
        f"openimcc-condensate-table:sha256:{condensate_digest}",
        f"openimcc-melt-binding:sha256:{pack_digest}",
    )


def _melt_pack_identity(pack_name: str) -> dict[str, str]:
    from simulator.melt_backend import openimcc_bridge

    pack = openimcc_bridge._load_pack(pack_name)
    return {
        "model_id": str(pack.model_id),
        "pack_version": str(pack.version),
        "binding_digest": openimcc_bridge._pack_digest(pack),
    }


def _default_redox_pack_digest() -> str:
    from openimcc.redox_pack import load_redox_pack

    return load_redox_pack().canonical_digest


def _case(
    engine: Engine,
    coefficient_sources: tuple[str, ...],
    *,
    temperature_K: Decimal = Decimal("1643.15"),
    provenance: dict[str, object] | None = None,
):
    temperature = temperature_K
    experiment = F.tabulation_experiment(T_K=temperature)
    identity = F.activity_identity(
        formula="FeO",
        T_K=temperature,
        component_basis="FeO",
        composition=Composition(
            "oxides",
            (("FeO", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
            AmountBasis.MOLE_FRACTION,
        ),
    )
    reference = F.observation(
        "feo-standard-baseline",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="feo-standard-test",
    )
    work = F.work()
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={"feo-standard-test": "reviewed"},
        hostname="test",
    )
    prediction = EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(
            state=ExecutionState.PRODUCED,
            call_evidence="test:feo-standard",
        ),
        value=Decimal("0.2"),
        unit="dimensionless",
        authority=Authority.CERTIFIED,
        coefficient_sources=coefficient_sources,
        lineage_complete=False,
        identity=identity,
        version="test",
        provenance=provenance or {},
    )
    return context, experiment, reference, prediction


@pytest.mark.parametrize(
    ("engine", "pack_name"),
    (
        (Engine.INTERNAL_ANALYTICAL, None),
        (Engine.OPENIMCC, "published-core"),
    ),
    ids=("internal-analytical", "openimcc-published-core"),
)
def test_declared_zero_offset_feo_standards_preserve_residual_bytes(
    engine: Engine,
    pack_name: str | None,
) -> None:
    identity = None
    if engine is Engine.OPENIMCC:
        pytest.importorskip("openimcc")
        assert pack_name is not None
        identity = _melt_pack_identity(pack_name)
        sources = _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        )
    else:
        sources = ("nasa-cea-thermo",)
    expected_digest = _RESIDUAL_SHA256_BEFORE_STANDARD_STEP[engine]
    context, _experiment, reference, prediction = _case(
        engine,
        sources,
        provenance=(
            {"melt_pack_identity": identity} if identity is not None else None
        ),
    )
    resolver = getattr(feo_standard_module, "_resolve_feo_activity_standard", None)
    converter = getattr(score_module, "_canonicalize_feo_activity_prediction", None)
    assert callable(resolver)
    assert callable(converter)

    standard, refusal = resolver(
        engine,
        prediction.coefficient_sources,
        melt_pack_identity=identity,
        temperature_K=Decimal("1643.15"),
    )
    assert refusal is None
    assert standard is not None
    assert standard.name == "Fe-019"
    assert standard.offset_j_mol == Decimal(0)

    before_prediction = (
        score_module._normalize_openimcc_feo_activity_prediction(prediction)
        if engine is Engine.OPENIMCC
        else prediction
    )
    if engine is Engine.OPENIMCC:
        assert before_prediction.value is prediction.value
        assert before_prediction.value.as_tuple() == prediction.value.as_tuple()
        assert not before_prediction.notices
    canonical_prediction, refusal = converter(before_prediction)
    assert refusal is None
    assert canonical_prediction.value is prediction.value
    before, before_candidate = compile_residual(
        reference, engine, context=context, prediction=before_prediction
    )
    after, after_candidate = compile_residual(
        reference, engine, context=context, prediction=canonical_prediction
    )
    before_bytes = dumps_residual_line(before, before_candidate).encode("utf-8")
    after_bytes = dumps_residual_line(after, after_candidate).encode("utf-8")

    assert before.numeric is not None
    assert before.numeric.value.as_tuple() == Decimal("0.0").as_tuple()
    assert before_bytes == after_bytes
    assert sha256(before_bytes).hexdigest() == expected_digest


def test_openimcc_feo_row_refuses_missing_melt_binding() -> None:
    """A condensate identity without the current melt binding is refused."""
    identity = _melt_pack_identity("default")
    sources = tuple(
        source
        for source in _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        )
        if not source.startswith("openimcc-melt-binding:")
    )
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        sources,
        melt_pack_identity=identity,
        temperature_K=Decimal("1643.15"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == (
        "openimcc_feo_standard_melt_pack_identity_missing"
    )


def test_openimcc_feo_row_refuses_a_changed_condensate_digest() -> None:
    identity = _melt_pack_identity("default")
    actual_digest = "a" * 64
    # OpenIMCC bae51f7 PACKAGED_FULL_CONDENSATE_TABLE_DIGEST. The condensate
    # CSV is unchanged from 979adb2. Independent of the owner constant so
    # this refusal does not echo that assignment.
    expected_digest = (
        "493c67d5ba23b75767cc3ce4bd6e571bd2400162b3affdbbc581a7b0ae75362e"
    )
    sources = _openimcc_sources(
        pack_version=identity["pack_version"],
        pack_digest=identity["binding_digest"],
        condensate_digest=actual_digest,
    )
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        sources,
        melt_pack_identity=identity,
        temperature_K=Decimal("1643.15"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail == {
        "reason": "openimcc_feo_condensate_table_digest_mismatch",
        "expected_condensate_table_digest": expected_digest,
        "actual_condensate_table_digest": actual_digest,
    }


@pytest.mark.parametrize("pack_name", ("default", "published-core"))
def test_pinned_openimcc_package_condensate_digest_is_accepted(
    pack_name: str,
) -> None:
    """The table load_gas_datapack() serves is the identity the resolver accepts."""

    pytest.importorskip("openimcc")
    from openimcc import load_gas_datapack

    live_digest = load_gas_datapack().condensate_table_digest
    assert live_digest == (
        feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
    )
    identity = _melt_pack_identity(pack_name)
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=live_digest,
        ),
        melt_pack_identity=identity,
        temperature_K=Decimal("1643.15"),
    )

    assert refusal is None
    assert standard is not None


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_openimcc_feo_row_refuses_when_gas_load_loses_condensate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openimcc

    from simulator.diagnostic_helpers.binary_pot_battery import open_battery_engine

    def fail_gas_load():
        raise RuntimeError("test gas datapack failure")

    monkeypatch.setattr(openimcc, "load_gas_datapack", fail_gas_load)
    handle = open_battery_engine("openimcc")
    assert handle.available, handle.unavailable_reason
    assert "engine_binding_identity" not in handle.identity
    assert "condensate_table_digest" not in handle.identity
    assert handle.identity["gas_load_failure"] == (
        "RuntimeError: test gas datapack failure"
    )

    context, _experiment, reference, _prediction = _case(Engine.OPENIMCC, ())
    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        handles={"openimcc": handle},
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail["reason"] == (
        "openimcc_feo_standard_melt_pack_identity_missing"
    )


@pytest.mark.parametrize(
    ("temperature_K", "expected_delta_g_j_mol", "expected_log10_shift"),
    (
        (Decimal("1600"), Decimal("10240.4790568"), Decimal("0.3343")),
        (Decimal("1650"), Decimal("9692.4013452"), Decimal("0.3068")),
    ),
    ids=("1600K", "1650K"),
)
def test_openimcc_feo_shift_uses_the_pinned_melt_pack_at_temperature(
    temperature_K: Decimal,
    expected_delta_g_j_mol: Decimal,
    expected_log10_shift: Decimal,
) -> None:
    # Independent closed form from the pinned v4 MELT pack: c0 - c1*T.
    # At 1600 K this is 27778.965828 - 10.961554232*1600 =
    # 10240.4790568 J/mol; at 1650 K it is 9692.4013452 J/mol.
    identity = _melt_pack_identity("default")
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        ),
        melt_pack_identity=identity,
        temperature_K=temperature_K,
    )

    assert refusal is None
    assert standard is not None
    assert standard.name == "DG46-MASSON65"
    assert standard.offset_j_mol == expected_delta_g_j_mol
    shift = feo_standard_module._canonical_log10_feo_activity(
        Decimal(0), temperature_K, standard.offset_j_mol
    )
    # The source quotes these shifts rounded to four decimals, hence half of
    # the last quoted digit is the complete tolerance.
    assert float(shift) == pytest.approx(float(expected_log10_shift), abs=0.00005)


def test_melt_pack_shift_matches_condensate_gibbs_rows_as_crosscheck() -> None:
    import openimcc
    from openimcc.gas import _lamor_gibbs

    with (
        resources.files(openimcc)
        .joinpath("data", "gas", "condensate-default-v1.csv")
        .open("r", encoding="utf-8", newline="") as handle
    ):
        current_row = dict(next(
            row for row in csv.DictReader(handle) if "FeO(l)" in row.values()
        ))
    for key in ("dH298_R", "dG_A", "dG_B", "dG_C", "dG_D", "dG_E"):
        current_row[key] = float(current_row[key])
    old_janaf_row = {
        "species_name": "FeO(l)",
        "dH298_R": -30.01,
        "dG_A": 6.72,
        "dG_B": 6.588,
        "dG_C": -1.248,
        "dG_D": 0.150,
        "dG_E": -0.007697,
    }
    identity = _melt_pack_identity("default")
    for temperature_K, expected in (
        (Decimal("1600"), Decimal("10240.5")),
        (Decimal("1650"), Decimal("9692.4")),
    ):
        standard, refusal = feo_standard_module._resolve_feo_activity_standard(
            Engine.OPENIMCC,
            _openimcc_sources(
                pack_version=identity["pack_version"],
                pack_digest=identity["binding_digest"],
                condensate_digest=(
                    feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
                ),
            ),
            melt_pack_identity=identity,
            temperature_K=temperature_K,
        )

        assert refusal is None
        assert standard is not None
        condensate_difference = _lamor_gibbs(
            float(temperature_K), current_row
        ) - _lamor_gibbs(float(temperature_K), old_janaf_row)
        assert condensate_difference == pytest.approx(float(expected), abs=1)
        assert condensate_difference == pytest.approx(
            float(standard.offset_j_mol), abs=1
        )


def test_openimcc_published_v1_feo_standard_has_an_explicit_zero_record() -> None:
    identity = _melt_pack_identity("published-core")
    sources = _openimcc_sources(
        pack_version=identity["pack_version"],
        pack_digest=identity["binding_digest"],
        condensate_digest=(
            feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
        ),
    )
    _context, _experiment, _reference, prediction = _case(
        Engine.OPENIMCC,
        sources,
        temperature_K=Decimal("1600"),
        provenance={"melt_pack_identity": identity},
    )

    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )

    assert normalized.value is prediction.value
    assert normalized.value.as_tuple() == prediction.value.as_tuple()
    assert not normalized.notices
    assert normalized.provenance["feo_standard_normalisation"] == {
        "from_standard": "Fe-019",
        "to_standard": "Fe-019",
        "melt_pack_identity": identity,
        "delta_g_j_mol": "0",
        "log10_shift": "0",
        "temperature_K": "1600",
    }


def test_non_v1_pack_without_feo_liquid_candidate_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _melt_pack_identity("default")
    monkeypatch.setattr(
        feo_standard_module,
        "_openimcc_pack_identity_and_payload",
        lambda _pack_name: (identity, {}),
    )
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        ),
        melt_pack_identity=identity,
        temperature_K=Decimal("1600"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_standard_pack_candidate_missing"


def test_openimcc_closed_redox_feo_row_refuses_changed_redox_pack_digest() -> None:
    actual_digest = "c" * 64
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        (f"openimcc-redox-pack:sha256:{actual_digest}",),
        melt_pack_identity=_melt_pack_identity("default"),
        temperature_K=Decimal("1600"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail == {
        "reason": "openimcc_feo_redox_pack_unresolved",
        "redox_pack_digest": actual_digest,
        "detail": (
            "no installed OpenIMCC redox pack matches digest "
            f"{actual_digest}"
        ),
    }


def test_redox_feo_row_matches_dg46_and_mutations_refuse() -> None:
    entry = feo_standard_module._openimcc_redox_pack_feo_entry(
        _default_redox_pack_digest()
    )
    row = entry["row"]
    assert feo_standard_module._redox_pack_feo_row_refusal(row) is None

    mutated_row = dict(row)
    mutated_row["dG_A"] = str(Decimal(str(row["dG_A"])) + Decimal("0.1"))
    refusal = feo_standard_module._redox_pack_feo_row_refusal(mutated_row)

    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_redox_standard_row_mismatch"


def test_openimcc_redox_refuses_a_v1_melt_standard_with_dg46_redox_row() -> None:
    identity = _melt_pack_identity("published-core")
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        (
            f"openimcc-redox-pack:sha256:"
            f"{_default_redox_pack_digest()}",
            f"openimcc-melt-binding:sha256:{identity['binding_digest']}",
        ),
        melt_pack_identity=identity,
        temperature_K=Decimal("1600"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_redox_melt_standard_mismatch"


def test_redox_hot_round_trip_preserves_melt_binding_for_cache_off_feo_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    from simulator import engine_cache
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    assert engine_cache._configured_engine_cache_path() is None

    captured = {}
    evaluate = openimcc_bridge.evaluate_redox

    def record_result(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        captured["result"] = result
        return result

    monkeypatch.setattr(openimcc_bridge, "evaluate_redox", record_result)
    _context, _experiment, observation, seed_prediction = _case(
        Engine.OPENIMCC, (), temperature_K=Decimal("1700")
    )
    prediction = score_module._predict_distin_closed_feo_activity(
        Engine.OPENIMCC,
        observation,
        seed_prediction.identity,
        "openimcc",
        (),
    )

    from simulator.melt_backend.openimcc_bridge import OpenImccRedoxResult

    result = captured["result"]
    decoded = OpenImccRedoxResult.from_hot(
        engine_cache.live_hot(result.to_hot())
    )
    assert decoded.melt_pack_binding_digest == result.melt_pack_binding_digest
    assert decoded.melt_pack_binding_digest

    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )
    assert normalized.value is not None
    assert normalized.refusal_reason is None
    assert normalized.provenance["feo_standard_normalisation"][
        "melt_pack_identity"
    ]["binding_digest"] == decoded.melt_pack_binding_digest


def test_openimcc_redox_feo_normalisation_flags_extrapolation() -> None:
    from openimcc.redox_pack import load_redox_pack

    temperature_K = Decimal("2153.15")
    identity = _melt_pack_identity("default")
    sources = (
        *_openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        ),
        f"openimcc-redox-pack:sha256:{_default_redox_pack_digest()}",
    )
    _context, _experiment, _reference, prediction = _case(
        Engine.OPENIMCC,
        sources,
        temperature_K=temperature_K,
        provenance={"melt_pack_identity": identity},
    )

    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )
    redox_entry = load_redox_pack().content["thermo_species"]["FeO(l)"]
    expected_band = redox_entry["fit_residual"]["temperature_range"]

    assert normalized.authority is Authority.EXTRAPOLATED
    assert normalized.certified_band == {
        "temperature_K": (Decimal("1649.15"), Decimal("1766.15"))
    }
    normalisation = normalized.provenance["feo_standard_normalisation"]
    assert normalisation["authority"] == "extrapolated"
    assert normalisation["certified_band"] == {
        "temperature_K": list(expected_band)
    }
    assert normalisation["range_flags"] == list(redox_entry["range_flags"])


@pytest.fixture(scope="module")
def distin_migration():
    root = Path(REPO_ROOT)
    migrator = Migrator(root=root)
    for name in (
        "holzheid-1997-feo-nio-coo-activity-metal-saturated",
        "distin-1971-ferrous-silicate-melts",
    ):
        migrator._migrate_extract(root / "data/literature/extracts" / f"{name}.yaml")
    migrator.finalize()
    return migrator.result


def test_distin_closed_redox_residual_pins_candidate_value_and_band_metadata(
    distin_migration,
) -> None:
    observation = next(
        row
        for row in distin_migration.observations.values()
        if row.source_id == "distin-1971-ferrous-silicate-melts"
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    context = ScoreContext(
        works=distin_migration.works,
        experiments=distin_migration.experiments,
        observations=distin_migration.observations,
        benches=distin_migration.benches,
        root=Path(REPO_ROOT),
    )
    residual, candidate = compile_residual(
        observation,
        Engine.OPENIMCC,
        context=context,
        comparison_ids={observation.observation_id},
    )

    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.value == _DISTIN_1880_RESIDUAL_DEX
    assert candidate is not None
    assert candidate.authority is Authority.EXTRAPOLATED
    assert (
        residual.numeric.operation.value,
        residual.numeric.unit,
        residual.numeric.value.as_tuple(),
        residual.numeric.decision_band,
        residual.numeric.metric_uncertainty,
        residual.numeric.verdict,
    ) == (
        "dex",
        "dimensionless",
        _DISTIN_1880_RESIDUAL_DEX.as_tuple(),
        None,
        None,
        None,
    )
    assert candidate.provenance is not None
    assert residual.assumed_band is None
    assumed = residual.assumed_band_details
    assert assumed is not None
    assert "band" not in assumed
    assert assumed["eligible"] is False
    assert assumed["evidence_class"] == EvidenceClass.MEASURED_REDUCED.value
    assert assumed["refusal"]["reason"] == (
        "method_class_applicability_unknown"
    )
    assert residual.score_eligible is False
    assert candidate.provenance["selected_oxygen_condition"] == {
        "fo2_tier": 3,
        "physics_rung": None,
        "route": "openimcc_closed_fe_metal_saturation",
        "value_Pa": "0.02022084627693526699090953590",
    }
    assert candidate.provenance["feo_standard_normalisation"][
        "from_standard"
    ] == "DG46-MASSON65"
    assert candidate.provenance["feo_standard_normalisation"][
        "to_standard"
    ] == "Fe-019"
    normalisation = candidate.provenance["feo_standard_normalisation"]
    assert normalisation["melt_pack_identity"] == _melt_pack_identity("default")
    assert Decimal(normalisation["log10_shift"]) > 0
    assert normalisation["authority"] == "extrapolated"
    assert normalisation["certified_band"] == {
        "temperature_K": [1649.15, 1766.15]
    }
    # The serialized line includes the shifted residual above and the
    # feo_standard_normalisation provenance, including its extrapolated
    # authority, certified band, and pack range flags.
    # Pin-port comparison: the 38,482-value probe found no numeric changes;
    # this serialized-line digest moved only because the default redox pack
    # digest changed from b927cf95 to 4beed388. OpenIMCC bisect found no
    # numeric changes across four text/identity commits, including c75456f's
    # v5-to-v6 default redox-pack reissue (digest
    # 6e99778d67e8e4ac5ea34d58c72eab0e56e538968ffe6f9cb4b69c4208af83c6
    # -> 4018b7071882dfaca4eca46512ad7357f48e461bc4c4ed1feacedf2c3a420097).
    serialized = dumps_residual_line(residual, candidate).encode("utf-8")
    assert '"assumed_band_details"' in serialized.decode("utf-8")
    # d-443 adds only this metadata to the serialized line. Keep the existing
    # candidate-value and provenance byte pin with that additive field removed.
    serialized_without_assumption = dumps_residual_line(
        replace(residual, assumed_band_details=None), candidate
    ).encode("utf-8")
    assert (
        sha256(serialized_without_assumption).hexdigest()
        == _DISTIN_1880_RESIDUAL_SHA256
    )


def test_nondefault_openimcc_profile_refuses_an_undeclared_feo_standard() -> None:
    sources = _openimcc_sources(
        pack_version="1.0.3-d066-ext-v1",
        pack_digest="b" * 64,
        condensate_digest=(
            feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
        ),
    )
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        sources,
        melt_pack_identity={
            "model_id": "unknown-model",
            "pack_version": "1.0.3-d066-ext-v1",
            "binding_digest": "b" * 64,
        },
        temperature_K=Decimal("1643.15"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_standard_melt_pack_identity_unknown"


def test_malformed_openimcc_datapack_becomes_a_typed_feo_standard_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openimcc = pytest.importorskip("openimcc")
    from simulator.melt_backend import openimcc_bridge

    identity = _melt_pack_identity("default")
    feo_standard_module._openimcc_pack_identity_and_payload.cache_clear()

    def malformed(_pack_name: str):
        raise openimcc.ImccMalformedDatapackError("invalid liquid-standard id")

    monkeypatch.setattr(openimcc_bridge, "_load_pack", malformed)
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        (f"openimcc-melt-binding:sha256:{identity['binding_digest']}",),
        melt_pack_identity=identity,
        temperature_K=Decimal("1700"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.detail["reason"] == "openimcc_feo_standard_pack_data_unavailable"
    assert "invalid liquid-standard id" in refusal.detail["detail"]


def test_malformed_openimcc_redox_datapack_becomes_a_typed_feo_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openimcc = pytest.importorskip("openimcc")
    from openimcc import redox_pack

    def malformed(*_args, **_kwargs):
        raise openimcc.ImccMalformedDatapackError("invalid liquid-standard id")

    monkeypatch.setattr(redox_pack, "load_redox_pack", malformed)
    standard, refusal = feo_standard_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        ("openimcc-redox-pack:sha256:" + "a" * 64,),
        temperature_K=Decimal("1700"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.detail["reason"] == "openimcc_feo_redox_pack_unresolved"
    assert "invalid liquid-standard id" in refusal.detail["detail"]


def test_nonzero_feo_standard_conversion_matches_hand_value() -> None:
    converter = getattr(feo_standard_module, "_canonical_log10_feo_activity", None)
    assert callable(converter)

    canonical = converter(
        Decimal("0"),
        Decimal("1649.15"),
        Decimal("9701.7"),
    )

    # Hand calculation from the ruling: 9701.7 J/mol / (R T ln 10) = 0.3073 dex.
    # Hand calculation from the ruling: 9701.7 J/mol at 1649.15 K is 0.3073 dex.
    assert float(canonical) == pytest.approx(0.3073, abs=0.00005)


def test_compile_residual_applies_nonzero_standard_conversion_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _experiment, reference, prediction = _case(
        Engine.INTERNAL_ANALYTICAL,
        ("nasa-cea-thermo",),
        temperature_K=Decimal("1649.15"),
    )
    monkeypatch.setattr(
        feo_standard_module,
        "_resolve_feo_activity_standard",
        lambda _engine, _sources: (
            feo_standard_module._FeOActivityStandard(
                name="hand-value test",
                offset_j_mol=Decimal("9701.7"),
            ),
            None,
        ),
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )

    assert residual.numeric is not None
    assert residual.numeric.operation.value == "dex"
    assert float(residual.numeric.value) == pytest.approx(0.3073, abs=0.00005)


def test_openimcc_feo_prediction_is_normalized_and_carries_notice_and_provenance() -> None:
    identity = _melt_pack_identity("default")
    sources = _openimcc_sources(
        pack_version=identity["pack_version"],
        pack_digest=identity["binding_digest"],
        condensate_digest=(
            feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
        ),
    )
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC,
        sources,
        temperature_K=Decimal("1600"),
        provenance={"melt_pack_identity": identity},
    )
    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )

    residual, candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=normalized
    )

    assert residual.status is not ResidualStatus.REFUSED
    assert candidate.provenance is not None
    record = candidate.provenance["feo_standard_normalisation"]
    assert record["from_standard"] == "DG46-MASSON65"
    assert record["to_standard"] == "Fe-019"
    assert record["melt_pack_identity"] == identity
    assert record["temperature_K"] == "1600"
    assert residual.numeric is not None
    assert float(residual.numeric.value) == pytest.approx(
        float(record["log10_shift"]), abs=1e-12
    )
    assert any(
        "DG46-MASSON65" in notice.reason and "Fe-019" in notice.reason
        for notice in candidate.notices
    )


def test_scorer_refuses_an_openimcc_feo_prediction_without_normalisation_record() -> None:
    sources = _openimcc_sources(
        pack_version=feo_standard_module._OPENIMCC_DEFAULT_PACK_VERSION,
        pack_digest=feo_standard_module._OPENIMCC_DEFAULT_PACK_DIGEST,
        condensate_digest=(
            feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
        ),
    )
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail["reason"] == "openimcc_feo_standard_not_normalised"
