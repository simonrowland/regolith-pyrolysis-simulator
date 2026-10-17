"""Iron-saturated Fe(1-x)O(l) rows scored on stoichiometric FeO(l).

The anchor numbers are the phase-1 derivation table. The factor itself is
recomputed here from the stored Darken & Gurry ratios and the production
JANAF interpolator, then compared with the scorer. This module does not
write the battery store.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    NoticeKind,
    Phase,
    Quantity,
    ReferenceStateConvention,
    RefusalReason,
)
from simulator.battery.generators.janaf import (
    JANAF_R_J_PER_MOL_K,
    _fusion_table_points,
    _interpolate_formation_gibbs,
    load_table_document,
)
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import REPO_ROOT, Migrator, load_yaml
from simulator.battery.records import (
    Notice,
    StandardState,
    State,
    phase_token,
)
from simulator.battery.score import (
    EnginePrediction,
    Execution,
    ScoreContext,
    compile_residual,
    fusion_comparison_reference,
    score_store,
    temperature_of,
)
from tests.battery import factories as F

ROOT = Path(REPO_ROOT)
_ANCHORS = (
    (Decimal("1649.15"), Decimal("1.841"), Decimal("1.829"), Decimal("1.902")),
    (Decimal("1709.15"), Decimal("1.724"), Decimal("1.713"), Decimal("1.745")),
    (Decimal("1766.15"), Decimal("1.627"), Decimal("1.617"), Decimal("1.674")),
)
_WINDOW = "1649.15-1766.15 K"
_EXTRAPOLATED = "D&G iron-boundary line extrapolated"
_HOLZHEID = "holzheid-1997-feo-nio-coo-activity-metal-saturated"
_DISTIN = "distin-1971-ferrous-silicate-melts"


def _formation_J(table_id: str, temperature_K: Decimal) -> Decimal:
    points, _digest = _fusion_table_points(table_id)
    return _interpolate_formation_gibbs(points, temperature_K) * Decimal(1000)


def _boundary_rows() -> tuple[tuple[Decimal, Decimal], ...]:
    document = load_yaml(
        ROOT / "data/literature/extracts/darken-gurry-1946-iron-oxygen-ii.yaml"
    )
    assert isinstance(document, dict)
    rows: list[tuple[Decimal, Decimal]] = []
    for block in document["species"].values():
        for obs in block.get("observations") or ():
            values = obs.get("values") or {}
            if "pCO2_over_pCO_as_printed" not in values:
                continue
            boundary = (
                (obs.get("point_conditions") or {})
                .get("equilibrium_boundary", {})
                .get("state", {})
                .get("value")
            )
            if boundary != "iron and liquid oxide":
                continue
            rows.append(
                (
                    Decimal(str(values["T_C"])) + Decimal("273.15"),
                    Decimal(str(values["pCO2_over_pCO_as_printed"])),
                )
            )
    rows.sort()
    assert len(rows) == 3
    return tuple(rows)


def _fusion() -> tuple[Decimal, Decimal, Decimal]:
    document = load_table_document(
        ROOT / "data/literature/compilations/janaf/tables/Fe-003.yaml"
    )
    solid = liquid = None
    for item in document["table"]["parse_ambiguities"]:
        parts = str(item.get("raw_line") or "").split("\t")
        if len(parts) < 6 or Decimal(parts[0]) != Decimal("1809"):
            continue
        pair = (Decimal(parts[2]), Decimal(parts[4]))
        if "DELTA" in parts[5] and "LIQUID" in parts[5]:
            solid = pair
        elif parts[5].startswith("TRANSITION"):
            liquid = pair
    assert solid is not None and liquid is not None
    return (liquid[1] - solid[1]) * Decimal(1000), liquid[0] - solid[0], Decimal("1809")


@lru_cache(maxsize=1)
def _line():
    rows = _boundary_rows()
    temperatures = tuple(temperature for temperature, _ratio in rows)
    delta_g = tuple(
        JANAF_R_J_PER_MOL_K * temperature * ratio.ln()
        + _formation_J("C-095", temperature)
        - _formation_J("C-093", temperature)
        for temperature, ratio in rows
    )
    count = Decimal(len(temperatures))
    sum_x = sum(temperatures, Decimal(0))
    sum_y = sum(delta_g, Decimal(0))
    sum_xx = sum((value * value for value in temperatures), Decimal(0))
    sum_xy = sum(
        (x * y for x, y in zip(temperatures, delta_g)), Decimal(0)
    )
    slope = (count * sum_xy - sum_x * sum_y) / (count * sum_xx - sum_x * sum_x)
    intercept = (sum_y - slope * sum_x) / count
    return temperatures, delta_g, intercept, slope, _fusion()


def _independent_factor(
    temperature_K: Decimal,
    *,
    intercept: Decimal | None = None,
    slope: Decimal | None = None,
) -> Decimal:
    _temperatures, _delta_g, fitted_intercept, fitted_slope, fusion = _line()
    intercept = fitted_intercept if intercept is None else intercept
    slope = fitted_slope if slope is None else slope
    delta_h, delta_s, melting = fusion
    delta_g = intercept + slope * temperature_K
    if temperature_K > melting:
        delta_g -= delta_h - temperature_K * delta_s
    return (
        (delta_g - _formation_J("Fe-019", temperature_K))
        / (JANAF_R_J_PER_MOL_K * temperature_K)
    ).exp()


def _independent_jackknife(temperature_K: Decimal) -> tuple[Decimal, Decimal]:
    temperatures, delta_g, _intercept, _slope, _fusion_terms = _line()
    factors = []
    for left in range(len(temperatures)):
        for right in range(left + 1, len(temperatures)):
            count = Decimal(2)
            xs = (temperatures[left], temperatures[right])
            ys = (delta_g[left], delta_g[right])
            sum_x = xs[0] + xs[1]
            sum_y = ys[0] + ys[1]
            sum_xx = xs[0] * xs[0] + xs[1] * xs[1]
            sum_xy = xs[0] * ys[0] + xs[1] * ys[1]
            slope = (count * sum_xy - sum_x * sum_y) / (count * sum_xx - sum_x * sum_x)
            intercept = (sum_y - slope * sum_x) / count
            factors.append(
                _independent_factor(
                    temperature_K, intercept=intercept, slope=slope
                )
            )
    return min(factors), max(factors)


def _wustite_row(temperature_K: Decimal, point: Decimal = Decimal("1")) -> object:
    identity = F.activity_identity(formula="FeO", T_K=temperature_K, component_basis="FeO")
    standard = identity.reference_state.value
    assert isinstance(standard, StandardState)
    standard = replace(
        standard,
        endmember=replace(standard.endmember, formula="Fe(1-x)O"),
        component_basis="Fe(1-x)O",
        reference_pressure_bar=Decimal("1.01325"),
    )
    identity = replace(identity, reference_state=State.of(standard))
    return F.observation(
        f"feo-{temperature_K}",
        "exp-feo",
        identity,
        point,
        evidence=EvidenceClass.MEASURED_DIRECT,
    )


def _conversion_notice(notices: tuple[Notice, ...]) -> Notice:
    found = [
        notice
        for notice in notices
        if notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        and notice.reason.startswith(
            "iron-saturated Fe(1-x)O(l) reference converted to stoichiometric FeO(l);"
        )
    ]
    assert len(found) == 1
    return found[0]


def test_anchors_match_the_derivation_and_an_independent_recompute() -> None:
    for temperature, factor, low, high in _ANCHORS:
        row = _wustite_row(temperature, Decimal("1"))
        converted = fusion_comparison_reference(row, engine=Engine.INTERNAL_ANALYTICAL)
        assert converted is not row
        assert converted.value.point == _independent_factor(temperature)
        notice = _conversion_notice(converted.notices)
        assert notice.kind is NoticeKind.DERIVATION_USES_COMPILATION
        assert (
            "source_flagged_less_reliable: D&G 1945 direct Fe/wustite "
            "pCO2/pCO ratios, flagged by D&G as less reliable on p. 811; "
            "uncertainty ±0.035 dex in log f (credible-route spread: 1.792 "
            "vs Holzheid/Barin quoted 1.94 at 1400 C, 0.069 dex "
            "end-to-end); Masson 1965 Temkin estimate 0.936 is EXCLUDED "
            "from the band (stated validity N_Fe2+ 0.7-0.9; this case lies "
            "outside it)"
        ) in notice.reason
        assert f"factor={factor}" in notice.reason
        jackknife_low, jackknife_high = _independent_jackknife(temperature)
        assert jackknife_low.quantize(Decimal("0.001")) == low
        assert jackknife_high.quantize(Decimal("0.001")) == high
        assert f"[{low}, {high}]" in notice.reason
        assert "is not a confidence interval" in notice.reason
        assert "1.792 vs 1.94" in notice.reason
        assert "1.475 vs 1.66" in notice.reason
        assert "equation (9) slope 44 vs 36.3 J/mol/K" in notice.reason
        assert "iron_phase=solid" in notice.reason
        assert not any(
            item.reason == _EXTRAPOLATED for item in converted.notices
        )
        state = converted.identity.reference_state.value
        assert isinstance(state, StandardState)
        assert state.endmember.formula == "FeO"
        assert state.component_basis == "FeO"
        assert phase_token(state.endmember) is Phase.L
        assert state.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
        assert state.reference_pressure_bar == Decimal("1.01325")
        assert not notice.reason.startswith("reference_converted_via_fusion;")

    below = fusion_comparison_reference(
        _wustite_row(Decimal("1578")), engine=Engine.INTERNAL_ANALYTICAL
    )
    assert _independent_factor(Decimal("1578")).quantize(Decimal("0.001")) == Decimal("2.005")
    assert any(item.reason == _EXTRAPOLATED and item.band == _WINDOW for item in below.notices)
    melting = fusion_comparison_reference(
        _wustite_row(Decimal("1809")), engine=Engine.INTERNAL_ANALYTICAL
    )
    assert "iron_phase=solid" in _conversion_notice(melting.notices).reason
    assert _independent_factor(Decimal("1809")).quantize(Decimal("0.001")) == Decimal("1.556")
    hot = fusion_comparison_reference(
        _wustite_row(Decimal("1873.15")), engine=Engine.INTERNAL_ANALYTICAL
    )
    hot_notice = _conversion_notice(hot.notices)
    assert "iron_phase=liquid" in hot_notice.reason
    assert "factor=1.475" in hot_notice.reason
    assert any(item.reason == _EXTRAPOLATED for item in hot.notices)

    other = F.observation(
        "nio",
        "exp-feo",
        F.activity_identity(formula="NiO", T_K=Decimal("1673")),
        Decimal("1.2"),
    )
    assert fusion_comparison_reference(other, engine=Engine.INTERNAL_ANALYTICAL) is other


def _feo_rows(result):
    rows = []
    for observation in result.observations.values():
        identity = observation.identity
        state = getattr(identity, "reference_state", None)
        if (
            state is None
            or not state.is_value
            or not isinstance(state.value, StandardState)
            or state.value.endmember.formula != "Fe(1-x)O"
        ):
            continue
        quantity = quantity_token(identity)
        if quantity not in {Quantity.ACTIVITY, Quantity.ACTIVITY_COEFFICIENT}:
            continue
        rows.append(observation)
    return rows


@pytest.fixture(scope="module")
def migration():
    migrator = Migrator(root=ROOT)
    for name in (_HOLZHEID, _DISTIN):
        migrator._migrate_extract(ROOT / "data/literature/extracts" / f"{name}.yaml")
    migrator.finalize()
    return migrator.result


def _context(result) -> ScoreContext:
    return ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
        root=ROOT,
    )


def _predict(engine, observation, **_kwargs):
    identity = observation.identity
    return EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="t1243-stub"),
        value=Decimal("1"),
        unit="dimensionless",
        authority=Authority.CERTIFIED,
        coefficient_sources=("t1243-stub",),
        lineage_complete=False,
        identity=identity if hasattr(identity, "species") else None,
    )


def test_every_iron_saturated_feo_row_is_scored_or_named(migration) -> None:
    rows = _feo_rows(migration)
    assert len(rows) == 96
    context = _context(migration)
    # The reference conversion is applied. A numeric residual still needs a
    # closed identity. These fields are unknown on the migrated rows, so the
    # existing identity check refuses them. None of the fields is the
    # reference state.
    refusal_fields: dict[tuple[str, ...], int] = {}
    extrapolated = 0
    for observation in rows:
        residual, candidate = compile_residual(
            observation,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            comparison_ids={observation.observation_id},
            predict=_predict,
        )
        notice = _conversion_notice(residual.notices)
        temperature = temperature_of(observation.identity)
        assert temperature is not None
        source, converted = notice.reason.split("source_value=", 1)[1].split(
            "; converted_value=", 1
        )
        converted_value, _rest = converted.split("; factor=", 1)
        assert Decimal(converted_value) == Decimal(source) * _independent_factor(temperature)
        band = [
            item
            for item in residual.notices
            if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        ]
        outside = temperature < Decimal("1649.15") or temperature > Decimal("1766.15")
        if outside:
            assert len(band) == 1
            assert band[0].reason == _EXTRAPOLATED
            assert band[0].band == _WINDOW
            extrapolated += 1
        else:
            assert band == []
        assert residual.numeric is None
        assert residual.refusal is not None
        assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
        fields = tuple(residual.refusal.detail.get("fields") or ())
        assert "reference_state" not in fields
        assert "reference_state_mismatch" not in json.dumps(
            residual.refusal.detail, default=str
        )
        refusal_fields[fields] = refusal_fields.get(fields, 0) + 1
        assert candidate is not None
        state = candidate.identity.reference_state.value
        assert isinstance(state, StandardState)
        assert state.endmember.formula == "FeO"
        assert state.component_basis == "FeO"
        assert phase_token(state.endmember) is Phase.L
        assert state.reference_pressure_bar == Decimal("1.01325")
    assert refusal_fields == {
        ("fO2_Pa",): 57,
        ("composition", "fO2_Pa"): 15,
        ("species.phase",): 24,
    }
    assert extrapolated == 32


def _fingerprint(residual) -> tuple:
    numeric = None if residual.numeric is None else str(residual.numeric.value)
    refusal = (
        None
        if residual.refusal is None
        else (
            residual.refusal.reason.value,
            json.dumps(residual.refusal.detail, sort_keys=True, default=str),
        )
    )
    notices = tuple(
        sorted(
            (notice.kind.value, notice.reason, notice.band or "")
            for notice in residual.notices
        )
    )
    return (residual.status.value, numeric, refusal, notices)


def _by_reference(residuals) -> dict[str, tuple]:
    grouped: dict[str, list] = {}
    for residual in residuals:
        grouped.setdefault(residual.reference, []).append(_fingerprint(residual))
    return {key: tuple(sorted(value)) for key, value in grouped.items()}


def test_score_store_changes_only_the_iron_saturated_feo_rows(migration, monkeypatch) -> None:
    context = _context(migration)
    feo_ids = {observation.observation_id for observation in _feo_rows(migration)}
    assert len(feo_ids) == 96

    def _score():
        residuals, _candidates = score_store(
            context,
            engines=(Engine.INTERNAL_ANALYTICAL,),
            predict=_predict,
        )
        return _by_reference(residuals)

    after = _score()
    import simulator.battery.score as score_module

    original = score_module._iron_saturated_feo_comparison

    def _disabled(reference, *, engine):
        return None

    monkeypatch.setattr(score_module, "_iron_saturated_feo_comparison", _disabled)
    before = _score()
    assert score_module._iron_saturated_feo_comparison is _disabled
    monkeypatch.setattr(score_module, "_iron_saturated_feo_comparison", original)

    changed = sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )
    assert set(changed) == feo_ids
    assert len(migration.observations) > 96


if __name__ == "__main__":
    raise SystemExit(
        "run via pytest; this module does not call migrate.main or write the battery store"
    )
