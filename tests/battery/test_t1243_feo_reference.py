"""Iron-saturated Fe(1-x)O(l) rows scored on stoichiometric FeO(l).

The anchor numbers are the phase-1 derivation table. The factor itself is
recomputed here from the stored Darken & Gurry ratios and the production
JANAF interpolator, then compared with the scorer. This module does not
write the battery store.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    IdentityEqualKind,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    RefusalReason,
    ReferenceStateConvention,
    ResidualStatus,
)
from simulator.battery.generators.janaf import (
    JANAF_R_J_PER_MOL_K,
    _fusion_table_points,
    _interpolate_formation_gibbs,
    load_table_document,
)
from simulator.battery.identity import identity_equal, quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    Migrator,
    _BARE_LOG_BAR_NOTICE,
    load_yaml,
)
from simulator.battery.records import (
    IronSaturationComposition,
    Located,
    Locator,
    Notice,
    StandardState,
    State,
    phase_token,
)
from simulator.battery.score import (
    EnginePrediction,
    Execution,
    FLAGGED_STRATUM_REFERENCE_CONVERTED_VIA_FUSION,
    FLAGGED_STRATUM_SOURCE_FLAGGED_LESS_RELIABLE,
    ScoreContext,
    compile_residual,
    flagged_strata,
    fusion_comparison_reference,
    iron_saturation_composition,
    predict_with_engine,
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
            "source_flagged_less_reliable: basis=Darken & Gurry 1946"
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
            "Table III B, p. 803 direct Fe/wustite pCO2/pCO ratios"
            in notice.reason
        )
        assert "Darken & Gurry 1946 (JACS 68:798-816)" in notice.reason
        assert "which the authors flag as less reliable on p. 811" in notice.reason
        log_f_spread_dex = (Decimal("1.94") / Decimal("1.792")).log10()
        log_f_spread_text = format(
            log_f_spread_dex.quantize(Decimal("0.0001")), "f"
        )
        iw_shift_text = format(
            (2 * log_f_spread_dex).quantize(Decimal("0.001")), "f"
        )
        assert (
            "uncertainty ±0.035 dex in log f centred on 1.792, reaching "
            "Holzheid/Barin 1.94 at 1400 C"
        ) in notice.reason
        assert (
            f"log-f spread log10(1.94/1.792) = {log_f_spread_text} dex"
            in notice.reason
        )
        assert (
            "IW shift Δlog10(fO2) = 2 Δlog10(f) = "
            f"{iw_shift_text} dex"
        ) in notice.reason
        assert (
            "Masson 1965 Temkin estimate 0.936 is EXCLUDED from the band "
            "(stated validity N_Fe2+ 0.7-0.9; this case lies outside it)"
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


def test_complete_measured_direct_feo_probe_is_excluded_from_headline() -> None:
    temperature = Decimal("1673.15")
    work = F.work()
    experiment = F.tabulation_experiment(
        experiment_id="t1243-eligibility-probe",
        work_id=work.work_id,
        T_K=temperature,
    )
    reference = replace(
        _wustite_row(temperature),
        experiment_id=experiment.experiment_id,
        source_id=work.work_id,
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={work.work_id: "reviewed"},
        hostname="test",
    )

    def predict(engine, observation, **_kwargs):
        assert observation.value.point is not None
        return EnginePrediction(
            engine=engine,
            channel=engine.value,
            execution=Execution(
                state=ExecutionState.PRODUCED,
                call_evidence="t1243-complete-probe",
            ),
            value=observation.value.point,
            unit="dimensionless",
            authority=Authority.CERTIFIED,
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            identity=observation.identity,
        )

    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={reference.observation_id},
        predict=predict,
    )

    assert reference.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert residual.numeric.value == Decimal(0)
    assert residual.numeric.decision_band is None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_SOURCE_FLAGGED_LESS_RELIABLE,
    )


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


def _test_oxide_inventory(composition) -> dict[str, float]:
    from simulator.accounting.formulas import parse_formula

    inventory: dict[str, float] = {}
    for oxide, amount in composition.components:
        formula = parse_formula(oxide, species=oxide)
        for element, atom_moles in formula.atom_moles(float(amount)).items():
            inventory[element] = inventory.get(element, 0.0) + atom_moles
    return inventory


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
    # Holzheid takes the Darken & Gurry gas-line factor. Table 2 prints log fO2
    # on each FeO row (AD 10 at 1677 K is -10.31). The bare-log converter stamps
    # identity.fO2_Pa as 1e5 Pa times 10 to that power. Those 57 rows now
    # match the stub's copied identity, so they carry a dex residual. Table 2
    # prints no per-row gamma uncertainty, so the decision rule stays missing.
    # Composition remains unknown on 15 rows. Distin's tabulated activity is
    # the same iron-saturated oxygen ratio, so it takes the same gas-line
    # factor; its missing independent fO2 is still the refusal. Neither
    # refusal is the reference state.
    refusal_fields: dict[tuple[str, ...], int] = {}
    scored = 0
    extrapolated = 0
    ad10 = 0
    ad10_scored = 0
    for observation in rows:
        residual, candidate = compile_residual(
            observation,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            comparison_ids={observation.observation_id},
            predict=_predict,
        )
        temperature = temperature_of(observation.identity)
        assert temperature is not None
        notice = _conversion_notice(residual.notices)
        source, converted = notice.reason.split("source_value=", 1)[1].split(
            "; converted_value=", 1
        )
        converted_value, _rest = converted.split("; factor=", 1)
        assert Decimal(converted_value) == Decimal(source) * _independent_factor(
            temperature
        )
        band = [
            item
            for item in residual.notices
            if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        ]
        outside = temperature < Decimal("1649.15") or temperature > Decimal("1766.15")
        composition = next(
            item
            for item in residual.notices
            if item.reason.startswith("fe_saturation_composition_owner_supplied")
        )
        if outside:
            assert len(band) == 1
            assert band[0].reason == _EXTRAPOLATED
            assert band[0].band == _WINDOW
            assert composition.authority is Authority.EXTRAPOLATED
            extrapolated += 1
        else:
            assert band == []
            assert composition.authority is Authority.DERIVED
        assert residual.refusal is not None
        assert "reference_state_mismatch" not in json.dumps(
            residual.refusal.detail, default=str
        )
        located_log = (observation.point_conditions or {}).get("fO2_log")
        printed_log = (
            located_log.state.value
            if isinstance(located_log, Located)
            and located_log.state.is_value
            and isinstance(located_log.state.value, Decimal)
            else None
        )
        # Table 2 AD 10 scores. Table 3(b) AD 10 prints the same log and T
        # and stays identity-unknown on composition. Both took the 1 bar notice.
        if temperature == Decimal("1677") and printed_log == Decimal("-10.31"):
            ad10 += 1
            assert any(
                item.reason == _BARE_LOG_BAR_NOTICE for item in residual.notices
            )
            if residual.numeric is not None:
                ad10_scored += 1
        if residual.numeric is not None:
            assert residual.numeric.operation is MetricOperation.DEX
            assert residual.refusal.reason is RefusalReason.DECISION_RULE_MISSING
            assert residual.refusal.detail["reason"] == "unparseable_printed_uncertainty"
            assert "FeO uncertainty" in str(residual.refusal.detail.get("original"))
            scored += 1
        else:
            assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
            fields = tuple(residual.refusal.detail.get("fields") or ())
            assert "reference_state" not in fields
            refusal_fields[fields] = refusal_fields.get(fields, 0) + 1
        assert candidate is not None
        state = candidate.identity.reference_state.value
        assert isinstance(state, StandardState)
        assert phase_token(state.endmember) is Phase.L
        assert state.reference_pressure_bar == Decimal("1.01325")
        assert state.endmember.formula == "FeO"
        assert state.component_basis == "FeO"
    assert scored == 57
    assert ad10 == 2
    assert ad10_scored == 1
    assert refusal_fields == {
        ("composition",): 15,
        ("fO2_Pa",): 24,
    }
    assert extrapolated == 32


def test_distin_scored_refusal_by_engine_without_typed_fe_saturation(migration) -> None:
    """Pin d-305: without the typed Fe condition, every Distin row refuses.

    Table I prints FeO, Fe2O3, SiO2 and the oxygen-in-metal activity ratio,
    not fO2. A fixed fO2 or dropping Fe2O3 would be an unruled derivation;
    t-1571 supplies the Fe-metal-saturated closed-solve route.
    """
    context = _context(migration)
    context = replace(
        context,
        experiments={
            experiment_id: replace(
                experiment, fO2_control=None, fe_metal_saturated=None
            )
            for experiment_id, experiment in context.experiments.items()
        },
    )
    rows = tuple(row for row in _feo_rows(migration) if row.source_id == _DISTIN)
    assert len(rows) == 24
    expected = {
        Engine.INTERNAL_ANALYTICAL: RefusalReason.IDENTITY_INCOMPLETE,
        Engine.OPENIMCC: RefusalReason.IDENTITY_INCOMPLETE,
        Engine.ALPHAMELTS: RefusalReason.UNSUPPORTED,
        Engine.MAGEMIN: RefusalReason.UNSUPPORTED,
        Engine.THERMOENGINE: RefusalReason.UNSUPPORTED,
        Engine.VAPOROCK: RefusalReason.UNSUPPORTED,
    }
    for observation in rows:
        for engine, reason in expected.items():
            residual, _candidate = compile_residual(
                observation,
                engine,
                context=context,
                comparison_ids={observation.observation_id},
            )
            assert residual.status is ResidualStatus.REFUSED
            assert residual.refusal is not None
            assert residual.refusal.reason is reason
            if engine is Engine.INTERNAL_ANALYTICAL:
                assert (
                    residual.refusal.detail["reason"]
                    == "distin_activity_requires_joint_fe_metal_solve"
                )
            elif engine is Engine.OPENIMCC:
                assert (
                    residual.refusal.detail["reason"]
                    == "distin_fe_metal_saturation_not_established"
                )


def test_distin_closed_fe_saturated_solve_scores_all_rows(migration) -> None:
    """An inferred typed source condition enables the d-305 closed solve."""
    rows = tuple(row for row in _feo_rows(migration) if row.source_id == _DISTIN)
    assert len(rows) == 24
    assert all(
        row.identity.species.phase.is_value
        and row.identity.species.phase.value is Phase.L
        for row in rows
    )
    context = _context(migration)
    for experiment_id in {row.experiment_id for row in rows}:
        experiment = context.experiments[experiment_id]
        condition = experiment.fe_metal_saturated
        assert condition is not None
        assert condition.state.is_value and condition.state.value is True
        assert condition.locator is not None and condition.locator.page == 73
        assert condition.inference is not None
        assert condition.inference.relation == "extract_inference"

    residuals = []
    for observation in rows:
        residual, _candidate = compile_residual(
            observation,
            Engine.OPENIMCC,
            context=context,
            comparison_ids={observation.observation_id},
        )
        residuals.append(residual)
        assert residual.status is ResidualStatus.NO_BAND
        assert residual.numeric is not None
        assert not any(
            notice.reason.startswith("reference_converted_via_iron_saturation;")
            or "distin_masson_temkin_frame" in notice.reason
            for notice in residual.notices
        )
        conversion = _conversion_notice(residual.notices)
        if observation.observation_id.endswith("distin1971_t1_1880_1_4"):
            assert observation.value.point == Decimal("0.952")
            assert (Decimal("0.60") / Decimal("0.63")).quantize(
                Decimal("0.001")
            ) == Decimal("0.952")
            temperature = temperature_of(observation.identity)
            assert temperature == Decimal("2153.15")
            source, converted = conversion.reason.split("source_value=", 1)[1].split(
                "; converted_value=", 1
            )
            converted_value, _rest = converted.split("; factor=", 1)
            assert Decimal(source) == Decimal("0.952")
            assert Decimal(converted_value) == Decimal("0.952") * _independent_factor(
                temperature
            )
        for engine, reason in {
            Engine.INTERNAL_ANALYTICAL: RefusalReason.IDENTITY_INCOMPLETE,
            Engine.ALPHAMELTS: RefusalReason.UNSUPPORTED,
            Engine.MAGEMIN: RefusalReason.UNSUPPORTED,
            Engine.THERMOENGINE: RefusalReason.UNSUPPORTED,
            Engine.VAPOROCK: RefusalReason.UNSUPPORTED,
        }.items():
            refusal, _candidate = compile_residual(
                observation,
                engine,
                context=context,
                comparison_ids={observation.observation_id},
            )
            assert refusal.status is ResidualStatus.REFUSED
            assert refusal.refusal is not None
            assert refusal.refusal.reason is reason
    assert len(residuals) == 24


def test_distin_printed_facts_and_inferred_fe_saturation_are_separate(migration) -> None:
    extract = load_yaml(
        ROOT / "data/literature/extracts" / f"{_DISTIN}.yaml"
    )
    assert isinstance(extract, dict)
    assert extract["source_evidence"]["liquid_iron_contact"] == {
        "text": (
            "In this method, liquid iron was levitated in contact with liquid "
            "'FeO'-SiO2 slags and samples of slag and metal at equilibrium were "
            "withdrawn for analysis."
        ),
        "locator": {"page": 73},
    }
    assert extract["source_evidence"]["atmosphere"]["text"] == (
        "Melts were equilibrated in helium or argon + helium atmospheres of the "
        "same composition and flow rate as previously employed, so that the "
        "temperatures of equilibration were 1785°, 1880° and 1960°C."
    )
    assert extract["source_evidence"]["atmosphere"]["locator"] == {
        "page": 74,
        "section": "Experimental",
    }
    assert extract["source_tables"]["experimental_results"]["standard_state"][
        "text"
    ] == (
        "Activities of 'FeO' were obtained as the ratio of the oxygen content "
        "of the iron in equilibrium with the slag to that in equilibrium with "
        "pure 'FeO' at the same temperature."
    )
    assert extract["source_tables"]["experimental_results"]["standard_state"][
        "locator"
    ] == {"page": 74, "section": "Experimental"}

    result = migration
    observation = next(
        row
        for row in _feo_rows(result)
        if row.source_id == _DISTIN
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    experiment = result.experiments[observation.experiment_id]
    condition = getattr(experiment, "fe_metal_saturated", None)
    assert condition is not None
    assert condition.state.is_value and condition.state.value is True
    assert condition.locator is not None and condition.locator.page == 73
    assert condition.inference is not None
    assert condition.inference.relation == "extract_inference"
    assert "not printed" in " ".join(condition.inference.inputs).casefold()


def test_distin_scorer_sends_one_mole_excess_fe_on_closed_route(
    migration, monkeypatch
) -> None:
    redox = pytest.importorskip("openimcc.redox")
    observation = next(
        row
        for row in _feo_rows(migration)
        if row.source_id == _DISTIN
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    context = _context(migration)
    calls = []
    original_evaluate_redox = redox.evaluate_redox

    def capture_redox_call(inventory, temperature_K, mode):
        calls.append((dict(inventory), temperature_K, mode))
        return original_evaluate_redox(inventory, temperature_K, mode)

    monkeypatch.setattr(redox, "evaluate_redox", capture_redox_call)
    residual, _candidate = compile_residual(
        observation,
        Engine.OPENIMCC,
        context=context,
        comparison_ids={observation.observation_id},
    )

    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert len(calls) == 1
    closed_inventory, _temperature_K, mode = calls[0]
    composition_state = observation.identity.composition
    assert composition_state is not None and composition_state.is_value
    expected_inventory = _test_oxide_inventory(composition_state.value)
    expected_inventory["Fe"] = expected_inventory.get("Fe", 0.0) + 1.0
    assert mode == "closed"
    assert closed_inventory == pytest.approx(expected_inventory, abs=1e-12)


def test_distin_closed_solver_refuses_off_fe_metal_segment(migration, monkeypatch) -> None:
    redox = pytest.importorskip("openimcc.redox")
    observation = next(
        row
        for row in _feo_rows(migration)
        if row.source_id == _DISTIN
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    experiment = migration.experiments[observation.experiment_id]
    monkeypatch.setattr(
        redox,
        "evaluate_redox",
        lambda *_args, **_kwargs: SimpleNamespace(
            metal_buffered=False, flags=("not_on_fe_metal_segment",)
        ),
    )

    prediction = predict_with_engine(
        Engine.OPENIMCC, observation, experiment=experiment
    )

    assert prediction.execution.state is ExecutionState.NOT_PROBED
    assert prediction.refusal_reason is RefusalReason.METRIC_DOMAIN
    assert prediction.refusal_detail["reason"] == "openimcc_fe_metal_saturation_inactive"


def test_pinned_openimcc_closed_redox_is_invariant_to_added_fe(migration) -> None:
    """Engine-level +0.5/+1/+2 invariance; the scorer route pins fixed +1 mol."""
    redox = pytest.importorskip("openimcc.redox")
    from simulator.melt_backend.openimcc_bridge import imported_openimcc_commit

    assert imported_openimcc_commit() == "66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84"
    observation = next(
        row
        for row in _feo_rows(migration)
        if row.source_id == _DISTIN
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    identity = observation.identity
    assert identity.composition is not None and identity.composition.is_value
    inventory = _test_oxide_inventory(identity.composition.value)

    activities = []
    for excess_fe_mol in (0.5, 1.0, 2.0):
        closed_inventory = dict(inventory)
        closed_inventory["Fe"] = closed_inventory.get("Fe", 0.0) + excess_fe_mol
        result = redox.evaluate_redox(
            closed_inventory, float(temperature_of(identity)), "closed"
        )
        assert result.metal_buffered is True
        activity = float(result.parent_activity["FeO"])
        assert math.isfinite(activity) and activity > 0.0
        activities.append(activity)

    assert max(activities) - min(activities) <= 1e-10


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


def _saturation_locator() -> Locator:
    return Locator(page=73, table="I", note="fixture")


def _cited_saturation(composition_as_printed: str) -> IronSaturationComposition:
    return IronSaturationComposition(
        method="cited_standard",
        composition_as_printed=composition_as_printed,
        quote="Coughlin Fe0.947O, fixture",
        locator=_saturation_locator(),
    )


def _derived_saturation(
    n_fe2_plus: Decimal, band_dex: Decimal
) -> IronSaturationComposition:
    return IronSaturationComposition(
        method="derived_from_source_analyses",
        composition_as_printed="zero-silica Fe2+ ion fraction",
        quote="Table I analyses extrapolated to zero silica, fixture",
        locator=_saturation_locator(),
        n_fe2_plus=n_fe2_plus,
        provenance=(
            "fixture reduction of printed analyses; linear versus quadratic "
            "and lowest-3 versus lowest-4; not an openimcc constant"
        ),
        band_dex=band_dex,
    )


def _with_saturation(row: object, recorded: IronSaturationComposition) -> object:
    identity = row.identity
    standard = identity.reference_state.value
    assert isinstance(standard, StandardState)
    standard = replace(standard, iron_saturation_composition=recorded)
    return replace(
        row, identity=replace(identity, reference_state=State.of(standard))
    )


def _ruled_segment(
    temperature_K: Decimal,
    left: tuple[Decimal, Decimal],
    right: tuple[Decimal, Decimal],
) -> Decimal:
    """End-segment continuation of two ruled (T, O/Fe) anchors."""

    t0, y0 = left
    t1, y1 = right
    return y0 + (y1 - y0) * (temperature_K - t0) / (t1 - t0)


def test_owner_supplies_darken_gurry_composition_inside_the_window() -> None:
    """Ruled anchors, not a refit: 3 - 2*1.0394 = 0.9212 at 1649.15 K."""

    decision = iron_saturation_composition(_wustite_row(Decimal("1649.15")))
    assert decision.failure is None
    assert decision.notice == "fe_saturation_composition_owner_supplied"
    assert decision.o_over_fe == Decimal("1.0394")
    assert decision.n_fe2_plus is not None
    assert decision.n_fe2_plus.quantize(Decimal("0.0001")) == Decimal("0.9212")
    assert decision.authority is Authority.DERIVED
    assert decision.extrapolated is False
    # |log10(N0(1.0394) / N0(Fe0.947O))|. The review prints this through
    # 17 decimal places as 0.01590791249755476. A constant band fails it.
    assert decision.band_dex.quantize(Decimal("1e-17")) == Decimal(
        "0.01590791249755476"
    )
    assert decision.band_dex != Decimal("1")
    midpoint = iron_saturation_composition(
        _wustite_row((Decimal("1649.15") + Decimal("1709.15")) / 2)
    )
    assert midpoint.o_over_fe == (Decimal("1.0394") + Decimal("1.0287")) / 2
    assert midpoint.authority is Authority.DERIVED
    endpoint = iron_saturation_composition(_wustite_row(Decimal("1766.15")))
    assert endpoint.o_over_fe == Decimal("1.0265")
    assert endpoint.authority is Authority.DERIVED
    assert endpoint.extrapolated is False


def test_cited_coughlin_composition_is_used_exactly() -> None:
    row = _with_saturation(
        _wustite_row(Decimal("1649.15"), Decimal("1")),
        _cited_saturation("Fe0.947O"),
    )
    decision = iron_saturation_composition(row)
    owner = iron_saturation_composition(_wustite_row(Decimal("1649.15")))
    assert decision.notice == "fe_saturation_composition_from_cited_standard"
    assert decision.failure is None
    assert decision.band_dex is None
    assert decision.authority is None
    # Review digits, not the 0.001 rounding 0.888. The owner's full
    # precision continues past this prefix.
    assert str(decision.n_fe2_plus).startswith("0.8880675818373812")
    assert decision.n_fe2_plus.quantize(Decimal("1e-16")) == Decimal(
        "0.8880675818373812"
    )
    assert decision.n_fe2_plus != decision.n_fe2_plus.quantize(Decimal("0.001"))
    assert decision.n_fe2_plus != owner.n_fe2_plus
    converted = fusion_comparison_reference(row, engine=Engine.INTERNAL_ANALYTICAL)
    assert converted.value.point == _independent_factor(Decimal("1649.15"))
    cited = next(
        notice
        for notice in converted.notices
        if notice.reason.startswith("fe_saturation_composition_from_cited_standard")
    )
    assert cited.band is None
    assert "N0=" in cited.reason


def test_unparseable_cited_composition_does_not_fall_through() -> None:
    row = _with_saturation(
        replace(_wustite_row(Decimal("1649.15")), source_id=_DISTIN),
        _cited_saturation("not-a-formula"),
    )
    decision = iron_saturation_composition(row)
    assert decision.notice == "fe_saturation_composition_from_cited_standard"
    assert decision.failure is not None
    assert decision.n_fe2_plus is None
    converted = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
    # The activity step is f and does not read the failed formula.
    assert converted.value.point == _independent_factor(Decimal("1649.15"))
    standard = converted.identity.reference_state.value
    assert isinstance(standard, StandardState)
    assert standard.endmember.formula == "FeO"
    assert any(
        notice.reason.startswith("fe_saturation_composition_from_cited_standard")
        and "not-a-formula" in notice.reason
        for notice in converted.notices
    )
    assert not any(
        "fe_saturation_composition_owner_supplied" in notice.reason
        for notice in converted.notices
    )


def test_owner_extrapolates_the_end_segment_and_does_not_clamp() -> None:
    anchor = iron_saturation_composition(_wustite_row(Decimal("1649.15")))
    cold = iron_saturation_composition(_wustite_row(Decimal("1600")))
    assert cold.authority is Authority.EXTRAPOLATED
    assert cold.extrapolated is True
    assert cold.o_over_fe != Decimal("1.0394")
    assert cold.o_over_fe == _ruled_segment(
        Decimal("1600"),
        (Decimal("1649.15"), Decimal("1.0394")),
        (Decimal("1709.15"), Decimal("1.0287")),
    )
    assert cold.o_over_fe > anchor.o_over_fe
    assert cold.n_fe2_plus != anchor.n_fe2_plus
    assert cold.band_dex == anchor.band_dex
    hot = iron_saturation_composition(_wustite_row(Decimal("1800")))
    assert hot.authority is Authority.EXTRAPOLATED
    assert hot.o_over_fe != Decimal("1.0265")
    assert hot.o_over_fe == _ruled_segment(
        Decimal("1800"),
        (Decimal("1709.15"), Decimal("1.0287")),
        (Decimal("1766.15"), Decimal("1.0265")),
    )
    # Local spread at 1800 K, above the 1766.15 K anchor spread. A band
    # clamped to the window value, or a constant, fails this.
    assert hot.band_dex.quantize(Decimal("1e-17")) == Decimal(
        "0.02910062656820004"
    )
    assert hot.band_dex != anchor.band_dex
    assert hot.band_dex > anchor.band_dex


def test_recorded_source_analysis_composition_precedes_the_owner_line() -> None:
    temperature = Decimal("1709.15")
    n0 = Decimal("0.91")
    row = _with_saturation(
        _wustite_row(temperature, Decimal("1.2")),
        _derived_saturation(n0, Decimal("0.03")),
    )
    decision = iron_saturation_composition(row)
    assert decision.notice == "composition_derived_from_source_analyses"
    assert decision.n_fe2_plus == n0
    assert decision.authority is Authority.DERIVED
    assert decision.band_dex == Decimal("0.03")
    assert decision.source is not None and "openimcc constant" in decision.source
    converted = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
    assert converted.value.point == Decimal("1.2") * _independent_factor(temperature)
    assert any(
        notice.reason.startswith("composition_derived_from_source_analyses")
        for notice in converted.notices
    )


def test_distin_oxygen_ratio_converts_by_the_gibbs_factor() -> None:
    """Stored a_FeO is O_metal/O_sat. a_F019 = a_D * f. a_D = 1 maps to f."""

    assert (Decimal("0.43") / Decimal("0.44")).quantize(Decimal("0.001")) == Decimal(
        "0.977"
    )
    assert (Decimal("0.60") / Decimal("0.63")).quantize(Decimal("0.001")) == Decimal(
        "0.952"
    )
    assert (Decimal("0.78") / Decimal("0.84")).quantize(Decimal("0.001")) == Decimal(
        "0.929"
    )
    temperature = Decimal("2153.15")
    point = Decimal("0.952")
    row = replace(_wustite_row(temperature, point), source_id=_DISTIN)
    factor = _independent_factor(temperature)
    for engine in (
        None,
        Engine.OPENIMCC,
        Engine.INTERNAL_ANALYTICAL,
        Engine.ALPHAMELTS,
        Engine.MAGEMIN,
        Engine.THERMOENGINE,
    ):
        converted = fusion_comparison_reference(row, engine=engine)
        assert converted.value.point == point * factor
        standard = converted.identity.reference_state.value
        assert isinstance(standard, StandardState)
        assert standard.endmember.formula == "FeO"
        composition = next(
            notice
            for notice in converted.notices
            if notice.reason.startswith("fe_saturation_composition_owner_supplied")
        )
        assert composition.authority is Authority.EXTRAPOLATED
        assert any(notice.reason == _EXTRAPOLATED for notice in converted.notices)
    saturated = replace(_wustite_row(temperature, Decimal("1")), source_id=_DISTIN)
    assert (
        fusion_comparison_reference(saturated, engine=Engine.OPENIMCC).value.point
        == factor
    )
    vaporock = fusion_comparison_reference(row, engine=Engine.VAPOROCK)
    assert vaporock.value.point == point


def test_recorded_n0_does_not_scale_a_distin_oxygen_ratio() -> None:
    temperature = Decimal("2153.15")
    point = Decimal("0.952")
    n0 = Decimal("0.90")
    row = _with_saturation(
        replace(_wustite_row(temperature, point), source_id=_DISTIN),
        _derived_saturation(n0, Decimal("0.02")),
    )
    converted = fusion_comparison_reference(row, engine=Engine.OPENIMCC)
    assert converted.value.point == point * _independent_factor(temperature)
    assert converted.value.point != point * _independent_factor(temperature) / n0
    decision = iron_saturation_composition(row)
    assert decision.n_fe2_plus == n0
    assert decision.notice == "composition_derived_from_source_analyses"
    assert any(
        notice.reason.startswith("composition_derived_from_source_analyses")
        and "N0=0.90" in notice.reason
        for notice in converted.notices
    )


def test_openimcc_branch_has_no_hardcoded_distin_ion_fraction() -> None:
    import simulator.battery.score as feo_score

    source = Path(feo_score.__file__).read_text(encoding="utf-8")
    assert "_DISTIN_N_FE2_PLUS_FACTORS" not in source
    for forbidden in ("0.9787", "0.9861", "0.9968"):
        assert forbidden not in source


def test_recorded_saturation_composition_is_part_of_identity() -> None:
    plain = _wustite_row(Decimal("1649.15"))
    cited = _with_saturation(plain, _cited_saturation("Fe0.947O"))
    again = _with_saturation(plain, _cited_saturation("Fe0.947O"))
    assert identity_equal(plain.identity, plain.identity).kind is IdentityEqualKind.EQUAL
    assert identity_equal(cited.identity, again.identity).kind is IdentityEqualKind.EQUAL
    differed = identity_equal(plain.identity, cited.identity)
    assert differed.kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_cited_and_derived_saturation_round_trip_through_plain_standard_state() -> None:
    from simulator.battery.migrate import _standard_state_from_plain

    absent = _standard_state_from_plain(
        {
            "convention": "raoultian_pure_endmember",
            "endmember": {"formula": "Fe(1-x)O", "phase": "l"},
            "component_basis": "Fe(1-x)O",
        }
    )
    assert absent.iron_saturation_composition is None
    cited = _standard_state_from_plain(
        {
            "convention": "raoultian_pure_endmember",
            "endmember": {"formula": "Fe(1-x)O", "phase": "l"},
            "component_basis": "Fe(1-x)O",
            "reference_pressure_bar": "1.01325",
            "iron_saturation_composition": {
                "method": "cited_standard",
                "composition_as_printed": "Fe0.947O",
                "quote": "Coughlin's Fe0.947O",
                "locator": {"page": 12, "note": "fixture"},
            },
        }
    )
    recorded = cited.iron_saturation_composition
    assert recorded is not None
    assert recorded.method == "cited_standard"
    assert recorded.composition_as_printed == "Fe0.947O"
    assert recorded.n_fe2_plus is None
    assert recorded.band_dex is None
    assert recorded.locator.page == 12
    derived = _standard_state_from_plain(
        {
            "convention": "raoultian_pure_endmember",
            "endmember": {"formula": "Fe(1-x)O", "phase": "l"},
            "component_basis": "Fe(1-x)O",
            "iron_saturation_composition": {
                "method": "derived_from_source_analyses",
                "composition_as_printed": "zero-silica Fe2+ ion fraction",
                "quote": "Table I",
                "locator": {"page": 73, "table": "I"},
                "n_fe2_plus": "0.90",
                "provenance": "fixture reduction",
                "band_dex": "0.02",
            },
        }
    )
    derived_recorded = derived.iron_saturation_composition
    assert derived_recorded is not None
    assert derived_recorded.n_fe2_plus == Decimal("0.90")
    assert derived_recorded.band_dex == Decimal("0.02")
    assert derived_recorded.provenance == "fixture reduction"


if __name__ == "__main__":
    raise SystemExit(
        "run via pytest; this module does not call migrate.main or write the battery store"
    )
