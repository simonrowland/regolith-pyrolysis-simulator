"""Regression checks for extract recipe precedence and figure admission."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

import pytest

from simulator.battery import composition_roles
from simulator.battery import migrate as battery_migrate
import simulator.diagnostic_helpers.extract_reproduction as extract_reproduction
from simulator.diagnostic_helpers.extract_reproduction import (
    AdoptedObservation,
    ObservationEvaluation,
    _evaluate_rate,
    _literature_pressure_points,
    _literature_rate_points,
    _melt_recipe_mol,
    _resolve_melt_recipe,
    evaluate_observation,
    load_adopted_observations,
    observation_admission_reason,
    _pressure_standard_state_kind,
)


def _adopted_observation(source_id: str, species: str, observation_id: str):
    return next(
        observation
        for observation in load_adopted_observations()
        if observation.source_id == source_id
        and observation.species_id == species
        and observation.observation_id == observation_id
    )


def test_bischof_unknown_mixed_basis_point_keeps_parent_recipe() -> None:
    obs = _adopted_observation(
        "kems-137-bischof-2023",
        "Ga",
        "bischof_2023_ga_psat_s1_1low_polytherm",
    )
    parent_role, parent_source = _resolve_melt_recipe(obs)
    parent = parent_role.composition.value
    assert parent_role.callable
    point = obs.values["points"][0]
    assert point["point_conditions"]["composition"]["state"]["tag"] == "unknown"

    role, source = _resolve_melt_recipe(obs, point=point)

    assert role.composition.value == pytest.approx(parent)
    assert role.callable
    assert "initial_charge_only" in source
    assert "initial_charge_only" in parent_source


def test_bischof_value_point_assay_overrides_parent_and_partial_assay_refuses() -> None:
    obs = _adopted_observation(
        "kems-137-bischof-2023",
        "In",
        "bischof_2023_in_psat_s1_1low_polytherm",
    )
    point = deepcopy(obs.values["points"][0])
    wrapper = point["point_conditions"]["composition"]
    payload = {
        "basis": "oxide_weight_percent_as_published",
        "amount_basis": "mass_percent",
        "components": [["SiO2", "70.0"], ["Al2O3", "30.0"]],
    }
    wrapper["state"] = {"tag": "value", "value": payload}

    recipe, _source, error = _melt_recipe_mol(obs, point=point)
    expected = battery_migrate._composition_from_plain(payload).as_map()
    assert error is None
    assert recipe == pytest.approx(
        {name: float(amount) for name, amount in expected.items()}
    )

    point["point_conditions"]["composition"]["state"]["value"]["components"] = [
        ["Na2O", "0.23"]
    ]
    partial, _source, error = _melt_recipe_mol(obs, point=point)
    assert partial is None
    assert error is not None and "partial analysis" in error


def test_located_mole_fraction_composition_overrides_parent_recipe() -> None:
    from types import SimpleNamespace

    payload = {
        "basis": "oxide_mole_fraction_as_published",
        "amount_basis": "mole_fraction",
        "components": [["SiO2", "0.6"], ["MgO", "0.4"]],
    }
    obs = SimpleNamespace(values={"composition_mol": {"SiO2": 0.7, "MgO": 0.3}})
    point = {
        "composition_role": "datum_state",
        "point_conditions": {
            "composition": {"state": {"tag": "value", "value": payload}}
        },
    }

    recipe, source, error = _melt_recipe_mol(obs, point=point)

    expected = battery_migrate._composition_from_plain(payload)
    assert error is None
    assert source == "extract point_conditions.composition"
    assert recipe == pytest.approx(
        {name: float(amount) for name, amount in expected.components}
    )
    assert {oxide: amount.hex() for oxide, amount in recipe.items()} == {
        "SiO2": "0x1.3333333333333p-1",
        "MgO": "0x1.999999999999ap-2",
    }


@pytest.mark.parametrize("shape", ["parent", "point", "flat", "pressure"])
@pytest.mark.parametrize(
    ("token", "tier", "status", "flags"),
    [
        ("datum_state", "clean", composition_roles.RoleStatus.DECLARED, ()),
        ("unreported", "flagged", composition_roles.RoleStatus.DEFAULTED,
         (composition_roles.ROLE_DEFAULT_FLAG,)),
        ("unreported", "refused", composition_roles.RoleStatus.UNKNOWN, ()),
        (None, "clean", composition_roles.RoleStatus.UNKNOWN, ()),
        (
            composition_roles.INITIAL_CHARGE_ONLY_PROXY_FLAG,
            "flagged",
            composition_roles.RoleStatus.DECLARED,
            (composition_roles.INITIAL_CHARGE_ONLY_PROXY_FLAG,),
        ),
    ],
)
def test_role_resolution_matrix(shape, token, tier, status, flags) -> None:
    from types import SimpleNamespace

    amounts = {"SiO2": "bad" if tier == "refused" else 60, "MgO": 40}
    values = {}
    if token is not None:
        values["composition_role"] = {"value": token}
    point = None
    if shape == "parent":
        values["composition_wt_pct"] = amounts
    elif shape == "point":
        point = {"composition_wt_pct": amounts}
        if token is not None:
            point["composition_role"] = token
            values["composition_role"] = "initial_charge_only"
    elif shape == "flat":
        point = amounts
        values["composition_wt_pct"] = {"SiO2": 50, "MgO": 50}
    else:
        point = {"composition_wt_pct": amounts, "T_K": 1500, "p_Pa": 1.0}
    role, source = _resolve_melt_recipe(
        SimpleNamespace(values=values, units=None), point=point
    )
    assert role.status is status
    assert role.flags == flags
    assert ("refused" if not role.callable else "flagged" if role.flags else "clean") == tier
    if not role.callable:
        assert role.composition.is_unknown
    elif shape == "flat":
        assert source == "extract flat run composition"
    if status is composition_roles.RoleStatus.DEFAULTED:
        assert role.initial_charge_only
        assert "default=initial_charge_only" in role.reason
        assert "'unreported'" in role.original_reason
    if token is None:
        assert role.role is None
        assert role.original_reason is None
    if token == composition_roles.INITIAL_CHARGE_ONLY_PROXY_FLAG:
        assert role.initial_charge_only
        assert role.status is composition_roles.RoleStatus.DECLARED


def test_located_datum_beats_parent_charge_and_flat_columns() -> None:
    from types import SimpleNamespace

    payload = {
        "basis": "oxide_mole_fraction_as_published",
        "amount_basis": "mole_fraction",
        "components": [["SiO2", "0.6"], ["MgO", "0.4"]],
    }
    parent = {
        "composition_role": "initial_charge_only",
        "composition_wt_pct": {"SiO2": 70, "MgO": 30},
    }
    located = {
        "SiO2": 55,
        "MgO": 45,
        "composition_role": "datum_state",
        "point_conditions": {
            "composition": {"state": {"tag": "value", "value": payload}},
        },
    }
    role, source = _resolve_melt_recipe(
        SimpleNamespace(values=parent, units=None), point=located
    )
    expected = battery_migrate._composition_from_plain(payload)
    assert role.status is composition_roles.RoleStatus.DECLARED
    assert role.role == composition_roles.DATUM_STATE
    assert role.flags == ()
    assert source == "extract point_conditions.composition"
    assert role.composition.value == pytest.approx(
        {name: float(amount) for name, amount in expected.components}
    )

    flat = {"SiO2": 55, "MgO": 45, "composition_role": "datum_state"}
    flat_role, flat_source = _resolve_melt_recipe(
        SimpleNamespace(values=parent, units=None), point=flat
    )
    assert flat_role.status is composition_roles.RoleStatus.DECLARED
    assert flat_role.role == composition_roles.DATUM_STATE
    assert flat_role.flags == ()
    assert flat_source == "extract flat run composition"
    assert flat_role.callable
    parent_recipe, _parent_source, parent_error = _melt_recipe_mol(
        SimpleNamespace(values=parent, units=None)
    )
    assert parent_error is None
    assert flat_role.composition.value != parent_recipe


def test_explicit_datum_is_not_overridden_by_a_discarded_identity_proxy() -> None:
    from decimal import Decimal

    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition
    from simulator.state_types import State

    def composition(silica: str, *, proxy: str | None) -> Composition:
        return Composition(
            basis="printed_oxides",
            components=(("SiO2", Decimal(silica)), ("MgO", Decimal("0.4"))),
            amount_basis=AmountBasis.MOLE_FRACTION,
            proxy_flag=proxy,
        )

    datum = composition("0.6", proxy=None)
    proxy = composition("0.2", proxy=composition_roles.INITIAL_CHARGE_ONLY_PROXY_FLAG)
    selected = composition_roles.resolve_composition_role(
        {"composition_role": composition_roles.DATUM_STATE},
        composition=State.of(proxy),
        point_composition=State.of(datum),
    )
    assert selected.role == composition_roles.DATUM_STATE
    assert selected.status is composition_roles.RoleStatus.DECLARED
    assert selected.flags == ()
    assert selected.composition.value.proxy_flag is None
    assert selected.composition.value.components[0][1] == Decimal("0.6")

    kept = composition_roles.resolve_composition_role(
        {},
        composition=State.of(datum),
        point_composition=State.of(proxy),
    )
    assert kept.initial_charge_only
    assert kept.status is composition_roles.RoleStatus.DECLARED
    assert kept.composition.value.proxy_flag == (
        composition_roles.INITIAL_CHARGE_ONLY_PROXY_FLAG
    )


def test_explicit_gap_beats_parent_flat_columns_at_pressure_boundary(monkeypatch) -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971", "K", "demaria_1971_k_psat_figure2_digitized"
    )
    obs = replace(
        obs,
        values={
            **obs.values,
            "composition_wt_pct": {"SiO2": 70, "MgO": 30},
            "points": [{
                "T_K": 1500.0,
                "p_Pa": 1.0,
                "SiO2": 55,
                "MgO": 45,
                "composition_source_gap": "not reported",
            }],
        },
    )
    calls = []
    runtimes = []
    compare_point = extract_reproduction._compare_point

    def melt_stub(*_args, **_kwargs):
        calls.append(_kwargs.get("account_mol"))
        return 1.0, None, {"provider_status": "ok"}

    def capture_runtime(**kwargs):
        runtimes.append(dict(kwargs["runtime"]))
        return compare_point(**kwargs)

    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(extract_reproduction, "_compare_point", capture_runtime)
    monkeypatch.setattr(
        extract_reproduction, "resolve_pO2_bar", lambda _obs: (5e-9, "fixture")
    )
    assert observation_admission_reason(obs) is None
    result = evaluate_observation(obs)
    assert calls == []
    # A printed pressure with an explicit composition gap is the same
    # missing-melt refusal as the unreported 12065 points: speciation, not
    # an unsupported observable. The gap still wins over the flat columns.
    assert [record.status for record in result.records] == ["unsupported-speciation"]
    assert runtimes[0]["melt_recipe_source"] == "extract composition_source_gap"
    assert runtimes[0]["refusal"] == "missing_condition:melt_composition"


def _rate_observation() -> AdoptedObservation:
    return AdoptedObservation(
        species_id="K",
        source_id="fixture-rate",
        observation_id="point_role_rate",
        obs_type="rate_series",
        review_status="draft",
        phase="silicate_melt",
        regime="langmuir_free_evaporation",
        standard_state=None,
        T_range_K=(1500.0, 1600.0),
        units="mol m^-2 s^-1",
        uncertainty={"kind": "relative_fraction", "value": 1.0},
        locator={"note": "fixture"},
        values={
            "gas_species": "K",
            "composition_role": "datum_state",
            "composition_wt_pct": {"SiO2": 70, "MgO": 30},
            "points": [
                {
                    "T_K": 1500.0,
                    "rate_mol_m2_s": 1.0e-6,
                    "composition_wt_pct": {"SiO2": 60, "MgO": 40},
                    "composition_role": "unreported",
                },
                {
                    "T_K": 1550.0,
                    "rate_mol_m2_s": 1.0e-6,
                    "composition_wt_pct": {"SiO2": 50, "MgO": 50},
                    "composition_role": "initial_charge_only",
                },
                {
                    "T_K": 1600.0,
                    "rate_mol_m2_s": 1.0e-6,
                },
            ],
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="fixture",
    )


def test_rate_point_keeps_its_role_and_assay(monkeypatch) -> None:
    obs = _rate_observation()
    parsed, skip, _drops = _literature_rate_points(obs)
    assert skip is None
    assert parsed is not None and len(parsed) == 3
    assert parsed[0]["composition_role"] == "unreported"
    assert parsed[0]["source_point"]["composition_wt_pct"] == {"SiO2": 60, "MgO": 40}
    assert parsed[1]["composition_role"] == "initial_charge_only"
    assert "composition_role" not in parsed[2]
    point_recipe, _source, point_error = _melt_recipe_mol(obs, point=parsed[0])
    declared_recipe, _declared_source, declared_error = _melt_recipe_mol(
        obs, point=parsed[1]
    )
    parent_recipe, _parent_source, parent_error = _melt_recipe_mol(obs)
    assert point_error is None and declared_error is None and parent_error is None
    assert point_recipe != parent_recipe
    assert declared_recipe != parent_recipe
    calls = []

    def melt_stub(*_args, **kwargs):
        calls.append(kwargs.get("account_mol"))
        return 1.0, None, {"provider_status": "ok"}

    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(
        extract_reproduction, "_engine_alpha", lambda *_args, **_kwargs: (1.0, None, {})
    )
    monkeypatch.setattr(
        extract_reproduction, "species_molar_mass_kg_mol", lambda _species: 0.039
    )
    monkeypatch.setattr(extract_reproduction, "langmuir_molar_flux", lambda *_a, **_k: 1.0e-6)
    result = _evaluate_rate(obs, ObservationEvaluation(observation=obs), {}, 1.0e-8)
    assert calls == [point_recipe, declared_recipe, parent_recipe]
    assert [record.status for record in result.records] == [
        "assumed-input",
        "assumed-input",
        "match",
    ]
    assert result.records[1].status != "match"
    assert composition_roles.is_role_default_reason(result.skip_reason)


def test_restoring_flat_decode_before_the_gap_breaks_the_pressure_pin(monkeypatch) -> None:
    real = extract_reproduction._melt_recipe_mol

    def restored(obs, *, point=None, parent_fallback=True):
        if isinstance(point, Mapping) and point.get("composition_source_gap"):
            stripped = {
                key: value
                for key, value in point.items()
                if key != "composition_source_gap"
            }
            source = point.get("source_point")
            if isinstance(source, Mapping):
                stripped["source_point"] = {
                    key: value
                    for key, value in source.items()
                    if key != "composition_source_gap"
                }
            return real(obs, point=stripped, parent_fallback=parent_fallback)
        return real(obs, point=point, parent_fallback=parent_fallback)

    monkeypatch.setattr(extract_reproduction, "_melt_recipe_mol", restored)
    with pytest.raises(AssertionError):
        test_explicit_gap_beats_parent_flat_columns_at_pressure_boundary(monkeypatch)


def test_reusing_the_parent_rate_recipe_breaks_the_point_pin(monkeypatch) -> None:
    real = extract_reproduction._resolve_melt_recipe

    def parent_only(obs, *, point=None):
        return real(obs, point=None)

    monkeypatch.setattr(extract_reproduction, "_resolve_melt_recipe", parent_only)
    with pytest.raises(AssertionError):
        test_rate_point_keeps_its_role_and_assay(monkeypatch)


def test_dropping_declared_role_flags_breaks_the_rate_pin(monkeypatch) -> None:
    real = extract_reproduction._record_composition_role

    def drop_declared(runtime, role):
        if role.status is not composition_roles.RoleStatus.DEFAULTED:
            return None
        return real(runtime, role)

    monkeypatch.setattr(extract_reproduction, "_record_composition_role", drop_declared)
    with pytest.raises(AssertionError):
        test_rate_point_keeps_its_role_and_assay(monkeypatch)


@pytest.mark.parametrize("consumer", ["migration", "pressure", "scoring"])
def test_bypassing_role_resolution_breaks_the_consumer_pin(
    monkeypatch, tmp_path, consumer
) -> None:
    resolve = composition_roles.resolve_point_composition

    def bypass(row, parent=None, candidates=(), **kwargs):
        observation = isinstance(row, composition_roles.Observation)
        if consumer == "scoring" and observation:
            return replace(resolve(row, parent, candidates, **kwargs), notices=())
        if not observation and consumer != "scoring":
            if isinstance(row, Mapping):
                row = {
                    key: value
                    for key, value in row.items()
                    if key != composition_roles.COMPOSITION_ROLE_FIELD
                }
            if isinstance(parent, Mapping):
                parent = {
                    key: value
                    for key, value in parent.items()
                    if key != composition_roles.COMPOSITION_ROLE_FIELD
                }
        return resolve(row, parent, candidates, **kwargs)

    monkeypatch.setattr(composition_roles, "resolve_point_composition", bypass)
    with pytest.raises(AssertionError):
        if consumer == "migration":
            test_flat_run_unknown_role_is_flagged_at_scorer_boundary(tmp_path)
        elif consumer == "pressure":
            test_pressure_with_inherited_unknown_role_predicts_in_flagged_tier(
                monkeypatch
            )
        else:
            test_scorer_reads_a_persisted_role_default_without_a_legacy_proxy()


def test_mapping_backed_composition_role_is_resolved_after_admission() -> None:
    obs = _adopted_observation(
        "kems-007-costa-2015",
        "Fe",
        "costa_2015_fe_vaporization_coefficient_alpha_digitized",
    )
    # The role is printed on the parent of the digitized points.
    assert isinstance(obs.values["composition_role"], Mapping)
    assert obs.values["admission_status"] == "admitted"
    assert observation_admission_reason(obs) is None
    role, _source = _resolve_melt_recipe(obs, point=obs.values["points"][0])
    assert role.initial_charge_only
    assert role.status is composition_roles.RoleStatus.DECLARED


def test_pressure_with_inherited_unknown_role_predicts_in_flagged_tier(monkeypatch) -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971", "K", "demaria_1971_k_psat_figure2_digitized"
    )
    obs = replace(
        obs,
        values={
            **obs.values,
            "composition_role": {"value": "unreported"},
            "points": [{
                "T_K": 1500.0,
                "p_Pa": 1.0,
                "point_conditions": {
                    "composition": {"state": {"tag": "value", "value": {
                        "basis": "oxide_mole_fractions",
                        "amount_basis": "mole_fraction",
                        "components": [["SiO2", "0.6"], ["MgO", "0.4"]],
                    }}},
                },
            }],
        },
    )
    calls = []

    def melt_stub(_species, _temperature, _po2, _data, *, account_mol):
        calls.append(account_mol)
        return 1.0, None, {"provider_status": "ok"}

    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(extract_reproduction, "resolve_pO2_bar", lambda _obs: (5e-9, "fixture"))
    assert observation_admission_reason(obs) is None
    result = evaluate_observation(obs)
    assert calls == [{"SiO2": 0.6, "MgO": 0.4}]
    assert [record.status for record in result.records] == ["assumed-input"]
    assert composition_roles.is_role_default_reason(result.skip_reason)


def test_unknown_point_composition_role_defaults_for_usable_assays(monkeypatch) -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "K",
        "demaria_1971_k_psat_figure2_digitized",
    )
    original_point = obs.values["points"][0]
    provider_calls: list[dict[str, float]] = []
    runtimes: list[dict[str, object]] = []
    compare_point = extract_reproduction._compare_point

    def melt_stub(_species, _temperature_K, _pO2_bar, _vp_data, *, account_mol):
        provider_calls.append(dict(account_mol))
        return 1.0, None, {"provider_status": "ok"}

    def capture_runtime(**kwargs):
        runtimes.append(dict(kwargs["runtime"]))
        return compare_point(**kwargs)

    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(extract_reproduction, "_compare_point", capture_runtime)

    for field, assay in (
        ("composition_wt_pct", {"SiO2": 60.0, "MgO": 40.0}),
        ("composition_mol", {"SiO2": 0.6, "MgO": 0.4}),
    ):
        calls_before = len(provider_calls)
        point = deepcopy(original_point)
        for key in (
            "composition_mol",
            "composition_wt_pct",
            "melt_composition_mol",
            "melt_composition_wt_pct",
            "sample_oxide_composition_wt_pct",
            "sample_Na2O_wt_pct",
            "sample_K2O_wt_pct",
            "composition_source_observation_id",
            "composition_source_gap",
            "point_conditions",
        ):
            point.pop(key, None)
        point["composition_role"] = {
            "value": "unreported",
            "evidence_class": "model",
        }
        point[field] = assay
        probe = replace(
            obs,
            observation_id=f"unknown-role-{field}",
            values={**obs.values, "points": [point]},
        )

        evaluation = extract_reproduction._evaluate_psat(
            probe,
            extract_reproduction.ObservationEvaluation(probe),
            {},
            5e-9,
        )
        record = evaluation.records[0]
        coverage = extract_reproduction.coverage_summary([evaluation])[
            "entries"
        ][0]

        assert len(provider_calls) == calls_before + 1
        assert provider_calls[-1] == pytest.approx(
            _melt_recipe_mol(probe, point=point)[0]
        )
        assert record.status == "assumed-input"
        assert runtimes[-1]["input_assumption"] == {
            "flag": "composition_role_defaulted_to_initial_charge_only",
            "default_role": "initial_charge_only",
            "original_reason": (
                "composition_role 'unreported' is not one of "
                "['datum_state', 'initial_charge_only']; composition left unstamped"
            ),
        }
        assert runtimes[-1]["typed_input_reason"].startswith(
            "typed-refusal:composition_role_defaulted_to_initial_charge_only:"
        )
        assert coverage["comparable_points"] == 0
        assert coverage["gap_points"] == 1
        assert "default=initial_charge_only" in coverage["primary_skip_reason"]

    partial_point = deepcopy(original_point)
    for key in (
        "composition_mol",
        "composition_wt_pct",
        "melt_composition_mol",
        "melt_composition_wt_pct",
        "sample_oxide_composition_wt_pct",
        "sample_Na2O_wt_pct",
        "sample_K2O_wt_pct",
        "composition_source_observation_id",
        "composition_source_gap",
    ):
        partial_point.pop(key, None)
    partial_point["composition_role"] = {
        "value": "unreported",
        "evidence_class": "model",
    }
    partial_point["point_conditions"]["composition"]["state"]["value"][
        "components"
    ] = [["Na2O", "0.23"]]
    partial_probe = replace(
        obs,
        observation_id="unknown-role-partial-assay",
        values={**obs.values, "points": [partial_point]},
    )
    calls_before = len(provider_calls)
    partial_evaluation = extract_reproduction._evaluate_psat(
        partial_probe,
        extract_reproduction.ObservationEvaluation(partial_probe),
        {},
        5e-9,
    )
    partial_coverage = extract_reproduction.coverage_summary(
        [partial_evaluation]
    )["entries"][0]

    assert len(provider_calls) == calls_before
    assert partial_evaluation.records[0].status == "unsupported-speciation"
    assert partial_coverage["comparable_points"] == 0
    assert partial_coverage["gap_points"] == 1
    assert any(
        "partial analysis" in reason
        for reason in partial_evaluation.skip_reasons
    )


def test_non_value_point_wrapper_recipe_census_has_no_parent_recipe_regressions() -> None:
    observations = load_adopted_observations()
    assert len(observations) == 2340
    regressions: list[str] = []
    bischof_unknown_points: dict[str, int] = {}
    point_recipe_keys = (
        "composition_mol",
        "melt_composition_mol",
        "composition_wt_pct",
        "melt_composition_wt_pct",
        "sample_oxide_composition_wt_pct",
    )

    for obs in observations:
        parent, _source, _error = _melt_recipe_mol(obs)
        if parent is None:
            continue
        for point in obs.values.get("points") or ():
            if not isinstance(point, Mapping):
                continue
            conditions = point.get("point_conditions")
            composition = (
                conditions.get("composition")
                if isinstance(conditions, Mapping)
                else None
            )
            state = (
                composition.get("state")
                if isinstance(composition, Mapping)
                else None
            )
            if not isinstance(state, Mapping) or state.get("tag") == "value":
                continue
            if point.get("composition_source_gap") or any(
                isinstance(point.get(key), dict) and point.get(key)
                for key in point_recipe_keys
            ) or any(
                point.get(key) is not None
                for key in ("sample_Na2O_wt_pct", "sample_K2O_wt_pct")
            ):
                continue

            recipe, _source, error = _melt_recipe_mol(obs, point=point)
            if error is not None or recipe != pytest.approx(parent):
                regressions.append(f"{obs.case_id}: {error or recipe}")
            if (
                obs.source_id == "kems-137-bischof-2023"
                and obs.observation_id
                in {
                    "bischof_2023_ga_psat_s1_1low_polytherm",
                    "bischof_2023_in_psat_s1_1low_polytherm",
                }
            ):
                bischof_unknown_points[obs.observation_id] = (
                    bischof_unknown_points.get(obs.observation_id, 0) + 1
                )

    assert regressions == []
    assert bischof_unknown_points == {
        "bischof_2023_ga_psat_s1_1low_polytherm": 11,
        "bischof_2023_in_psat_s1_1low_polytherm": 11,
    }


def test_figure_only_exception_requires_parsed_digitized_points() -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "Na",
        "demaria_1971_na_psat_figure1_sample_12022_digitized",
    )
    values = {
        **obs.values,
        "points": [],
        "runtime_form": "log10(P_Pa) = A_Pa - B/(T+C)",
        "runtime_coefficients": {"A_Pa": 10.4199, "B": 7696.0, "C": 0.0},
    }
    coefficient_only = replace(obs, values=values)
    parsed, _skip, _drops = _literature_pressure_points(coefficient_only)
    assert parsed
    assert observation_admission_reason(coefficient_only) == (
        "unsupported_observable:figure_only_not_digitized"
    )

    malformed = replace(
        coefficient_only,
        values={**values, "points": [{"T_K": "bad", "p_Pa": "bad"}]},
    )
    parsed, _skip, drops = _literature_pressure_points(malformed)
    assert parsed is None
    assert drops
    assert observation_admission_reason(malformed) == (
        "unsupported_observable:figure_only_not_digitized"
    )

    model_marked = replace(
        obs, values={**obs.values, "evidence_class": "model"}
    )
    assert observation_admission_reason(model_marked) == (
        "model_output_not_measurement"
    )
    unknown_marker = replace(
        obs, values={**obs.values, "evidence_class": "model_derived"}
    )
    assert observation_admission_reason(unknown_marker) == (
        "unknown_evidence_marker:evidence_class=model_derived"
    )


def test_demaria_k_reservoir_reason_selects_melt_standard_state() -> None:
    obs = _adopted_observation(
        "kems-022-demaria-1971",
        "K",
        "demaria_1971_k_psat_figure2_digitized",
    )

    assert obs.reservoir == {
        "tag": "not_applicable",
        "reason": "p_partial over multicomponent melt; no single pure-substance reservoir",
    }
    assert _pressure_standard_state_kind(obs) == "melt"


def test_demaria_k_composition_points_use_scorer_effusion_closure(
    monkeypatch,
    tmp_path,
) -> None:
    import yaml

    from simulator.battery.enums import Engine, ExecutionState, NoticeKind, Quantity
    from simulator.battery.records import Execution, Notice
    from simulator.battery.score import (
        ENGINE_CHANNELS,
        OXYGEN_BALANCE_NOTICE_PREFIX,
        EnginePrediction,
    )

    import simulator.battery.score as score

    source_path = (
        extract_reproduction.EXTRACTS_DIR / "kems-022-demaria-1971.yaml"
    )
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    raw_observation = next(
        row
        for row in document["species"]["K"]["observations"]
        if row["observation_id"] == "demaria_1971_k_psat_figure2_digitized"
    )
    raw_point = next(
        point for point in raw_observation["values"]["points"]
        if point["T_K"] == 1366.12
    )
    raw_components = (
        raw_point["point_conditions"]["composition"]["state"]["value"][
            "components"
        ]
    )
    next(
        component for component in raw_components if component[0] == "K2O"
    )[1] = "0.61"
    alternate_dir = tmp_path / "alternate-extracts"
    alternate_dir.mkdir()
    (alternate_dir / source_path.name).write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    obs = next(
        row
        for row in load_adopted_observations(extracts_dir=alternate_dir)
        if row.source_id == "kems-022-demaria-1971"
        and row.species_id == "K"
        and row.observation_id == "demaria_1971_k_psat_figure2_digitized"
    )
    points, skip, drops = _literature_pressure_points(obs)
    assert skip is None
    assert not drops
    assert points is not None
    composition_points = [
        point for point in points if _melt_recipe_mol(obs, point=point)[0] is not None
    ]
    assert len(composition_points) == 2

    closure_calls: list[tuple[object, object]] = []

    def closure_stub(engine, observation, **kwargs):
        assert engine is Engine.INTERNAL_ANALYTICAL
        experiment = kwargs["experiment"]
        closure_calls.append((observation, experiment))
        reactive_notice = score._cell_oxygen_not_modelled_notice(
            Quantity.P_PARTIAL, kwargs["bench"].cell_materials
        )
        return EnginePrediction(
            engine=engine,
            channel=ENGINE_CHANNELS[engine],
            execution=Execution(state=ExecutionState.PRODUCED),
            notices=(
                reactive_notice,
                Notice(
                    kind=NoticeKind.SOURCE_DISAGREEMENT,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason=OXYGEN_BALANCE_NOTICE_PREFIX
                    + '{"pO2_bar":5e-09}',
                    origin=f"engine:{engine.value}",
                ),
            ),
        )

    melt_calls: list[tuple[str, float, float, dict[str, float]]] = []

    def melt_stub(species, temperature_K, pO2_bar, _vp_data, *, account_mol):
        melt_calls.append(
            (str(species), float(temperature_K), float(pO2_bar), dict(account_mol))
        )
        return 1.0, None, {"provider_status": "ok"}

    runtimes: list[dict[str, object]] = []
    compare_point = extract_reproduction._compare_point

    def capture_runtime(**kwargs):
        runtimes.append(dict(kwargs["runtime"]))
        return compare_point(**kwargs)

    monkeypatch.setattr(score, "predict_with_engine", closure_stub)
    monkeypatch.setattr(extract_reproduction, "_engine_melt_psat_pa", melt_stub)
    monkeypatch.setattr(extract_reproduction, "_compare_point", capture_runtime)

    evaluation = evaluate_observation(obs, vapor_pressure_data={})

    assert len(evaluation.records) == 10
    assert len(closure_calls) == 2
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.identity.temperature_K.is_value
        and observation.identity.composition.is_value
        and len(observation.identity.composition.value.components) == 9
        and observation.identity.composition.value.proxy_flag == "initial_charge_only"
        and experiment.experiment_id.endswith("::experiment::demaria-1971-k-figure2")
        for observation, experiment in closure_calls
    )
    assert all(
        not any(
            "composition_role_defaulted_to_initial_charge_only" in notice.reason
            for notice in observation.notices
        )
        for observation, _experiment in closure_calls
    )
    assert {
        float(observation.identity.temperature_K.value): float(observation.value.point)
        for observation, _experiment in closure_calls
    } == pytest.approx(
        {1366.12: 0.004526021577, 1314.06: 0.0025451684895}
    )
    changed_composition = next(
        observation.identity.composition.value
        for observation, _experiment in closure_calls
        if float(observation.identity.temperature_K.value) == 1366.12
    )
    assert float(dict(changed_composition.components)["K2O"]) == pytest.approx(
        0.00400546853226304
    )
    assert len(melt_calls) == 2
    assert {call[1] for call in melt_calls} == {1366.12, 1314.06}
    assert {call[2] for call in melt_calls} == {5e-9}
    assert all(len(call[3]) == 9 for call in melt_calls)
    changed_provider_recipe = next(
        call[3] for call in melt_calls if call[1] == 1366.12
    )
    assert changed_provider_recipe["K2O"] == pytest.approx(
        0.00400546853226304
    )
    assert sum(record.status == "assumed-input" for record in evaluation.records) == 2
    assert sum(record.actual_value is None for record in evaluation.records) == 8
    assert all(
        runtime.get("oxygen_closure", {}).get("owner")
        == "simulator/battery/score.py::_borrow_internal_analytical_effusion"
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )
    assert all(
        runtime["oxygen_closure"]["route"] == "effusion_oxygen_balance"
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )
    assert all(
        "melt-only oxygen balance"
        in runtime["oxygen_closure"]["melt_only_assumption"]
        and any(
            "reactive cell: oxygen balance of the cell not modelled" in reason
            for reason in runtime["oxygen_closure"]["notice_reasons"]
        )
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )
    assert "melt-only oxygen balance" in evaluation.skip_reason

    datum_document = deepcopy(document)
    datum_observation = next(
        row
        for row in datum_document["species"]["K"]["observations"]
        if row["observation_id"] == "demaria_1971_k_psat_figure2_digitized"
    )
    datum_observation["values"]["composition_wt_pct"] = {
        "SiO2": 55.0,
        "MgO": 45.0,
    }
    for point in datum_observation["values"]["points"]:
        composition_role = point.get("composition_role")
        if (
            not isinstance(composition_role, dict)
            or composition_role.get("value") != "initial_charge_only"
        ):
            continue
        located_composition = point["point_conditions"]["composition"]["state"][
            "value"
        ]
        point["composition_role"] = {
            **composition_role,
            "value": "datum_state",
        }
        mole_fraction = battery_migrate._composition_from_plain(
            located_composition
        )
        point["point_conditions"]["composition"]["state"]["value"] = {
            **located_composition,
            "basis": "oxide_mole_fraction_as_published",
            "amount_basis": "mole_fraction",
            "components": [
                [oxide, str(amount)]
                for oxide, amount in mole_fraction.components
            ],
        }
        assert "composition_mol" not in point
    datum_dir = tmp_path / "datum-extracts"
    datum_dir.mkdir()
    (datum_dir / source_path.name).write_text(
        yaml.safe_dump(datum_document, sort_keys=False), encoding="utf-8"
    )
    datum_obs = next(
        row
        for row in load_adopted_observations(extracts_dir=datum_dir)
        if row.source_id == "kems-022-demaria-1971"
        and row.species_id == "K"
        and row.observation_id == "demaria_1971_k_psat_figure2_digitized"
    )
    closure_calls.clear()
    melt_calls.clear()
    runtimes.clear()

    datum_evaluation = evaluate_observation(datum_obs, vapor_pressure_data={})
    datum_coverage = extract_reproduction.coverage_summary([datum_evaluation])
    datum_entry = datum_coverage["entries"][0]

    assert len(closure_calls) == 2
    assert len(melt_calls) == 2
    assert all(
        observation.identity.composition.is_value
        and observation.identity.composition.value.proxy_flag is None
        and observation.identity.composition.value.basis
        == "oxide_mole_fraction_as_published"
        for observation, _experiment in closure_calls
    )
    assert len(datum_evaluation.records) == 10
    assert sum(
        record.status == "assumed-input" for record in datum_evaluation.records
    ) == 2
    assert sum(
        record.status == "unsupported-speciation"
        for record in datum_evaluation.records
    ) == 8
    flagged_values = [
        record.actual_value
        for record in datum_evaluation.records
        if record.status == "assumed-input"
    ]
    assert all(value is not None and value > 0 for value in flagged_values)
    assert datum_entry["comparable_points"] == 0
    assert datum_entry["gap_points"] == 10
    assert "melt-only oxygen balance" in datum_entry["primary_skip_reason"]

    unknown_role_document = deepcopy(document)
    unknown_role_observation = next(
        row
        for row in unknown_role_document["species"]["K"]["observations"]
        if row["observation_id"] == "demaria_1971_k_psat_figure2_digitized"
    )
    unknown_role_observation["values"]["composition_wt_pct"] = {
        "SiO2": 60.0,
        "MgO": 40.0,
    }
    for point in unknown_role_observation["values"]["points"]:
        role = point.get("composition_role")
        if not isinstance(role, dict) or role.get("value") != "initial_charge_only":
            continue
        point["composition_role"] = {**role, "value": "unreported"}
        assert "composition_mol" not in point
    unknown_role_dir = tmp_path / "unknown-role-extracts"
    unknown_role_dir.mkdir()
    (unknown_role_dir / source_path.name).write_text(
        yaml.safe_dump(unknown_role_document, sort_keys=False), encoding="utf-8"
    )
    unknown_role_obs = next(
        row
        for row in load_adopted_observations(extracts_dir=unknown_role_dir)
        if row.source_id == "kems-022-demaria-1971"
        and row.species_id == "K"
        and row.observation_id == "demaria_1971_k_psat_figure2_digitized"
    )
    closure_calls.clear()
    melt_calls.clear()
    runtimes.clear()

    unknown_role_evaluation = evaluate_observation(
        unknown_role_obs, vapor_pressure_data={}
    )
    unknown_role_coverage = extract_reproduction.coverage_summary(
        [unknown_role_evaluation]
    )["entries"][0]

    assert len(closure_calls) == 2
    assert len(melt_calls) == 2
    diagnostic_recipes = {
        float(point["T_K"]): _melt_recipe_mol(unknown_role_obs, point=point)[0]
        for point in _literature_pressure_points(unknown_role_obs)[0]
        if float(point["T_K"]) in {1366.12, 1314.06}
    }
    point_locators = {
        float(point["T_K"]): battery_migrate._locator_from_plain(
            point["point_conditions"]["composition"].get("locator")
        )
        for point in _literature_pressure_points(unknown_role_obs)[0]
        if float(point["T_K"]) in {1366.12, 1314.06}
    }
    assert all(recipe is not None for recipe in diagnostic_recipes.values())
    assert all(
        observation.identity.composition.is_value
        and observation.identity.composition.value.proxy_flag == "initial_charge_only"
        and {
            oxide: float(amount)
            for oxide, amount in observation.identity.composition.value.components
        }
        == pytest.approx(
            diagnostic_recipes[float(observation.identity.temperature_K.value)]
        )
        and any(
            "typed-refusal:composition_role_defaulted_to_initial_charge_only"
            in notice.reason
            for notice in observation.notices
        )
        for observation, _experiment in closure_calls
    )
    assert all(
        observation.point_conditions["composition"].locator
        == point_locators[float(observation.identity.temperature_K.value)]
        for observation, _experiment in closure_calls
    )
    assert sum(
        record.status == "assumed-input"
        for record in unknown_role_evaluation.records
    ) == 2
    assert sum(
        record.status == "unsupported-speciation"
        for record in unknown_role_evaluation.records
    ) == 8
    assert unknown_role_coverage["comparable_points"] == 0
    assert unknown_role_coverage["gap_points"] == 10
    assert any(
        runtime.get("input_assumption", {}).get("flag")
        == "composition_role_defaulted_to_initial_charge_only"
        for runtime in runtimes
    )

    partial_document = deepcopy(unknown_role_document)
    partial_observation = next(
        row
        for row in partial_document["species"]["K"]["observations"]
        if row["observation_id"] == "demaria_1971_k_psat_figure2_digitized"
    )
    for point in partial_observation["values"]["points"]:
        role = point.get("composition_role")
        if not isinstance(role, dict) or role.get("value") != "unreported":
            continue
        composition = point["point_conditions"]["composition"]["state"]["value"]
        composition["components"] = [["Na2O", "0.23"]]
    partial_dir = tmp_path / "partial-assay-extracts"
    partial_dir.mkdir()
    (partial_dir / source_path.name).write_text(
        yaml.safe_dump(partial_document, sort_keys=False), encoding="utf-8"
    )
    partial_obs = next(
        row
        for row in load_adopted_observations(extracts_dir=partial_dir)
        if row.source_id == "kems-022-demaria-1971"
        and row.species_id == "K"
        and row.observation_id == "demaria_1971_k_psat_figure2_digitized"
    )
    closure_calls.clear()
    melt_calls.clear()
    runtimes.clear()

    partial_evaluation = evaluate_observation(partial_obs, vapor_pressure_data={})

    assert not closure_calls
    assert not melt_calls
    assert sum(
        record.status == "unsupported-speciation"
        for record in partial_evaluation.records
    ) == 10
    assert all(
        "partial analysis" in runtime.get("refusal", "")
        for runtime in runtimes
        if runtime.get("temperature_K") in {1366.12, 1314.06}
    )


def test_flat_run_unknown_role_is_flagged_at_scorer_boundary(tmp_path) -> None:
    from simulator.battery.enums import Quantity
    from simulator.battery.migrate import migrate
    from simulator.battery.score import blocking_qualifications, flagged_strata
    from tests.battery.test_b716_composition_role import _series_extract
    from tests.battery.test_migrate import _write_min_tree

    doc = _series_extract({"composition_wt_pct": {"SiO2": 50, "MgO": 50}})
    point = doc["species"]["Fe"]["observations"][0]["values"]["points"][0]
    del point["composition_wt_pct"]
    point.update(SiO2=55, MgO=45, composition_role="unreported")
    store = migrate(_write_min_tree(tmp_path, doc), write=False)
    rows = [
        row for row in store.observations.values()
        if row.observation_id.startswith("fixture-source::fixture_series::")
        and row.identity.temperature_K is not None
        and row.identity.temperature_K.is_value
        and row.identity.composition is not None
        and row.identity.composition.is_value
    ]
    assert len(rows) == 1
    (row,) = rows
    assert row.identity.composition.value.proxy_flag == "initial_charge_only"
    role_notices = tuple(
        notice for notice in row.notices
        if "composition_role_defaulted_to_initial_charge_only" in notice.reason
    )
    assert len(role_notices) == 1
    assert flagged_strata(role_notices)
    assert blocking_qualifications(Quantity.P_PARTIAL, role_notices) == role_notices


def test_scorer_reads_a_persisted_role_default_without_a_legacy_proxy() -> None:
    from decimal import Decimal

    from simulator.battery.enums import Engine, EvidenceClass, Quantity
    from simulator.battery.records import DecisionBand
    from simulator.battery.score import compile_residual
    from tests.battery import factories as F
    from tests.battery.test_score import _context, _predict

    experiment = F.kems_experiment()
    identity = F.activity_identity()
    reference = F.observation(
        "role-default", experiment.experiment_id, identity, Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT, source_id="work-1",
    )
    role = composition_roles.resolve_composition_role(
        {"composition_role": "unreported"},
        composition=identity.composition,
        quantity=Quantity.ACTIVITY,
        origin=reference.observation_id,
    )
    defaulted_reference = replace(
        reference,
        notices=tuple(
            notice for notice in role.notices
            if composition_roles.is_role_default_reason(notice.reason)
        ),
    )
    def score(row):
        context = _context(F.work(), experiment, row, review="reviewed")
        return compile_residual(
            row,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            prediction=_predict(Decimal("0.3"), identity),
            derived_band=DecisionBand(Decimal("1"), "dimensionless", "test-dex"),
        )[0]

    control = score(reference)
    assert control.numeric is not None
    assert control.numeric.decision_band is not None
    residual = score(defaulted_reference)
    assert residual.numeric is not None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    assert residual.numeric.decision_band is None
