"""b-716: printed composition context and catalogue composition roles.

Pins, committed before the reader change:

* a series point that prints an oxide wt% map preserves it on
  ``point_conditions.printed_composition`` and derives a typed composition
  only when b-704 accepts the kept mass (otherwise it records the refusal);
* the existing catalogue ``proxy_flag`` paths (the four ``proxy_flag`` hits
  under ``tests/``) keep their token and their catalogue notice;
* a per-run composition marked as initial-charge-only carries the printed
  starting charge as a flagged proxy;
* identity drops the fixed-valence fO2 omission for any non-None
  ``proxy_flag``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import _composition_is_fixed_valence
from simulator.battery.composition_roles import (
    is_role_default_reason,
    resolve_composition_role,
)
from simulator.battery.migrate import (
    _catalogue_composition_located_from_values,
    _initial_charge_only_composition_proxy,
    REPO_ROOT,
    migrate,
    sample_from_equipment,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG as INITIAL_CHARGE_ONLY,
    INITIAL_CHARGE_ONLY_PROXY_NOTICE,
    SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX,
    Composition,
    Located,
    State,
)
from simulator.battery.score import (
    FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
    _catalogue_composition_notice,
    _initial_charge_composition_notice,
    flagged_strata,
    predict_with_engine,
)
from tests.battery import factories as F
from tests.battery.test_migrate import (
    _extract_observations,
    _migrate_real_extract,
    _write_min_tree,
)


_INITIAL_CHARGE_ONLY_ROW_COUNTS = {
    "kems-020-hastie-1981-nbsir": {"p_partial": 12, "activity_coefficient": 19},
    "kems-025-markova-1983": {"p_partial": 25},
    "kems-027-plante-hastie-1983": {"activity": 6, "p_partial": 2},
    "kems-137-bischof-2023": {"p_partial": 22},
    "shi-2024-cr-activity-slag-polymerisation": {
        "activity": 12,
        "activity_coefficient": 12,
    },
    "shi-2025-mno-activity-eaf-spinel": {"activity": 4},
    "shornikov-1997-diopside-vaporization": {"p_partial": 6},
    "shornikov-2019-perovskite-evaporation-rjpc": {"p_partial": 68},
    "shornikov-2019-perovskite-evaporation": {"p_partial": 76},
    "shornikov-2025-saratov-alkali-evaporation": {"p_partial": 2},
    "shornikov-2026-wollastonite-kems": {"p_partial": 251},
    "wang-seetharaman-2010-cr-oxidation-original": {
        "p_partial": 20,
        "valence_ratio": 19,
    },
}


def _series_extract(values_extra: dict | None = None) -> dict:
    values = {
        "quantity": "p_partial",
        "method_class": "measured_direct",
        "points": [
            {
                "T_C": 1800.0,
                "P": 1.0e-4,
                "composition_wt_pct": {"SiO2": 45.4, "Al2O3": 28.63, "CaO": 16.39},
            }
        ],
        **(values_extra or {}),
    }
    return {
        "source_id": "fixture-source",
        "source": {"citation": "Fixture (2026)", "doi": "10.1234/FIXTURE"},
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "fixture_series",
                        "type": "p_partial",
                        "locator": {"page": 2, "figure": "1"},
                        "phase": "g",
                        "units": "Pa",
                        "values": values,
                    }
                ]
            }
        },
    }


def _point_compositions(result, prefix: str):
    out = []
    for obs_id, observation in result.observations.items():
        if not obs_id.startswith(prefix):
            continue
        conditions = observation.point_conditions or {}
        if "printed_composition" in conditions and "composition" in conditions:
            out.append((obs_id, conditions))
    return out


def _fixture_points(
    tmp_path: Path,
    values_extra: dict | None = None,
    *,
    complete_composition: bool = False,
):
    extract = _series_extract(values_extra)
    if complete_composition:
        point = extract["species"]["Fe"]["observations"][0]["values"]["points"][0]
        point["composition_wt_pct"]["CaO"] = 25.97
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    points = _point_compositions(result, "fixture-source::fixture_series")
    assert len(points) == 1
    return points[0][1]


def _raw_conditions_have_initial_charge_proxy(conditions: object) -> bool:
    if not isinstance(conditions, dict):
        return False
    composition = conditions.get("composition")
    state = composition.get("state") if isinstance(composition, dict) else None
    value = state.get("value") if isinstance(state, dict) else None
    return (
        isinstance(value, dict)
        and value.get("proxy_flag") == INITIAL_CHARGE_ONLY
    )


def test_every_usable_typed_initial_charge_only_row_gets_proxy_and_notice(
    tmp_path: Path,
) -> None:
    for source_id, expected_quantities in _INITIAL_CHARGE_ONLY_ROW_COUNTS.items():
        role_parent_ids = set()
        for raw in _extract_observations(f"{source_id}.yaml"):
            values = raw.get("values")
            role = resolve_composition_role(raw, values).role
            points = values.get("points") if isinstance(values, dict) else None
            point_role = any(
                resolve_composition_role(point, values).role == INITIAL_CHARGE_ONLY
                for point in points or ()
                if isinstance(point, dict)
            )
            point_proxy = _raw_conditions_have_initial_charge_proxy(
                raw.get("point_conditions")
            ) or any(
                isinstance(point, dict)
                and _raw_conditions_have_initial_charge_proxy(
                    point.get("point_conditions")
                )
                for point in points or ()
            )
            if (
                role == INITIAL_CHARGE_ONLY
                or point_role
                or point_proxy
            ):
                role_parent_ids.add(raw["observation_id"])
        result = _migrate_real_extract(
            tmp_path / source_id, f"{source_id}.yaml"
        )
        rows = tuple(
            row
            for row in result.observations.values()
            if row.source_id == source_id
            and any(
                row.observation_id.startswith(f"{source_id}::{parent_id}")
                for parent_id in role_parent_ids
            )
            and row.identity.quantity.is_value
            and row.identity.quantity.value.value in expected_quantities
        )
        assert Counter(row.identity.quantity.value.value for row in rows) == {
            **expected_quantities
        }, (source_id, tuple(sorted(role_parent_ids)))

        for row in rows:
            composition_states = [row.identity.composition]
            point_composition = (row.point_conditions or {}).get("composition")
            if isinstance(point_composition, Located):
                composition_states.append(point_composition.state)
            assert any(
                state is not None
                and state.is_value
                and isinstance(state.value, Composition)
                and state.value.proxy_flag == INITIAL_CHARGE_ONLY
                for state in composition_states
            ), (
                row.observation_id,
                None
                if row.identity.composition is None
                else row.identity.composition.reason,
                None
                if not isinstance(point_composition, Located)
                else point_composition.state.reason,
                None
                if not isinstance(
                    (row.point_conditions or {}).get("printed_composition"), Located
                )
                else (row.point_conditions or {})["printed_composition"].state,
                result.experiments[row.experiment_id].sample.printed_composition,
                tuple(notice.reason for notice in row.notices),
            )
            if source_id == "shi-2025-mno-activity-eaf-spinel":
                proxy = next(
                    state.value
                    for state in composition_states
                    if state is not None
                    and state.is_value
                    and isinstance(state.value, Composition)
                    and state.value.proxy_flag == INITIAL_CHARGE_ONLY
                )
                assert proxy.amount_basis is AmountBasis.MOLE_FRACTION

            stored_notices = [
                stored.kind is NoticeKind.SOURCE_DISAGREEMENT
                and INITIAL_CHARGE_ONLY_PROXY_NOTICE in stored.reason
                and stored.reason.startswith(
                    f"{SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX} "
                    "composition_role=initial_charge_only:"
                )
                for stored in row.notices
            ]
            assert sum(stored_notices) == 1, row.observation_id
            initial_charge_notice = _initial_charge_composition_notice(row)
            assert initial_charge_notice is not None
            assert initial_charge_notice in row.notices
            assert sum(notice == initial_charge_notice for notice in row.notices) == 1
            assert FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT in flagged_strata(
                row.notices
            )


def _assert_partial_analysis_refusal(conditions):
    printed = conditions["printed_composition"]
    assert isinstance(printed, Located) and printed.state.is_value
    assert len(printed.state.value) >= 2
    typed = conditions["composition"]
    assert isinstance(typed, Located) and typed.state.is_unknown
    # b-704 refuses this fixture's 90.42 wt% analysis below the 95 wt% floor.
    assert "kept sum=90.42 wt% is below 95 wt%" in typed.state.reason
    return typed.state


def _assert_initial_charge_proxy(conditions):
    printed = conditions["printed_composition"]
    assert isinstance(printed, Located) and printed.state.is_value
    typed = conditions["composition"]
    assert isinstance(typed, Located) and typed.state.is_value
    assert isinstance(typed.state.value, Composition)
    assert typed.state.value.proxy_flag == INITIAL_CHARGE_ONLY
    return typed.state.value


def test_undeclared_partial_point_oxide_map_is_refused(tmp_path) -> None:
    _assert_partial_analysis_refusal(_fixture_points(tmp_path))


def test_proxy_selects_the_assay_and_resolves_its_role_once(monkeypatch) -> None:
    calls = []
    real = resolve_composition_role

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(
        "simulator.battery.migrate.composition_roles.resolve_composition_role", spy
    )
    composition = _composition(None)
    identity = F.activity_identity(composition=composition)
    updated, _conditions, notices = _initial_charge_only_composition_proxy(
        identity,
        None,
        (),
        row={"composition_role": INITIAL_CHARGE_ONLY},
        quantity=Quantity.ACTIVITY,
        quantity_as_printed="activity",
        locator=None,
        origin="one-resolve",
    )
    assert len(calls) == 1
    assert updated.composition is not None and updated.composition.is_value
    assert updated.composition.value.proxy_flag == INITIAL_CHARGE_ONLY
    assert sum(
        INITIAL_CHARGE_ONLY_PROXY_NOTICE in notice.reason for notice in notices
    ) == 1


def test_unusable_declared_map_keeps_a_usable_located_composition(monkeypatch) -> None:
    calls = []
    real = resolve_composition_role

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(
        "simulator.battery.migrate.composition_roles.resolve_composition_role", spy
    )
    usable = _composition(None)
    other = Composition(
        basis="printed_oxides",
        components=(("SiO2", Decimal("0.1")), ("CaO", Decimal("0.9"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(composition=other)
    updated, conditions, _notices = _initial_charge_only_composition_proxy(
        identity,
        {"composition": Located(State.of(usable))},
        (),
        row={"composition_role": INITIAL_CHARGE_ONLY},
        quantity=Quantity.ACTIVITY,
        quantity_as_printed="activity",
        locator=None,
        origin="located-over-unusable-map",
        point_composition_map_declared=True,
        point_declared_component_map={"SiO2": {"left": 1}, "MgO": {"right": 2}},
    )
    assert len(calls) == 1
    assert updated.composition is not None and updated.composition.is_value
    assert updated.composition.value.components == usable.components
    assert updated.composition.value.proxy_flag == INITIAL_CHARGE_ONLY
    point = conditions["composition"]
    assert point.state.is_value
    assert point.state.value.components == usable.components


def test_declared_initial_charge_point_map_is_flagged_proxy(tmp_path) -> None:
    conditions = _fixture_points(
        tmp_path,
        {"composition_role": INITIAL_CHARGE_ONLY},
        complete_composition=True,
    )
    _assert_initial_charge_proxy(conditions)


def test_located_composition_role_declaration_is_read(tmp_path) -> None:
    conditions = _fixture_points(
        tmp_path,
        {
            "composition_role": {
                "value": INITIAL_CHARGE_ONLY,
                "locator": {"page": 2, "table": "1"},
                "quote": "The compositions of initial samples",
            }
        },
        complete_composition=True,
    )
    _assert_initial_charge_proxy(conditions)


def test_initial_charge_role_without_printed_charge_still_refuses(tmp_path) -> None:
    extract = _series_extract({"composition_role": INITIAL_CHARGE_ONLY})
    points = extract["species"]["Fe"]["observations"][0]["values"]["points"]
    points[0].pop("composition_wt_pct")
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(iter(result.observations.values()))

    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    assert "printed initial charge is unavailable" in observation.identity.composition.reason
    point_composition = observation.point_conditions["composition"]
    assert point_composition.state.is_unknown
    assert "printed initial charge is unavailable" in point_composition.state.reason


def test_malformed_point_mole_amount_refuses_before_initial_charge_fallback(
    tmp_path: Path,
) -> None:
    extract = _series_extract(
        {
            "composition_role": INITIAL_CHARGE_ONLY,
            "composition_wt_pct": {
                "SiO2": 50.0,
                "Al2O3": 30.0,
                "CaO": 20.0,
            },
        }
    )
    point = extract["species"]["Fe"]["observations"][0]["values"]["points"][0]
    point.pop("composition_wt_pct")
    point["composition_mol"] = {
        "SiO2": "0.5",
        "Al2O3": "not-a-number",
        "CaO": "0.3",
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = next(iter(result.observations.values()))
    identity_composition = observation.identity.composition
    assert identity_composition is not None and identity_composition.is_unknown
    assert "malformed declared amount: 'not-a-number'" in (
        identity_composition.reason or ""
    )
    point_composition = observation.point_conditions["composition"]
    assert point_composition.state.is_unknown
    assert "malformed declared amount: 'not-a-number'" in (
        point_composition.state.reason or ""
    )
    hard_issues = result.validation.hard_issues if result.validation is not None else ()
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "composition_mol" in (issue.path or "")
        and "malformed declared amount: 'not-a-number'" in (issue.detail or "")
        for issue in hard_issues
    )

    experiment = result.experiments[observation.experiment_id]
    for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
        prediction = predict_with_engine(engine, observation, experiment=experiment)
        assert prediction.value is None
        assert prediction.refusal_reason is not None


def test_initial_charge_only_composition_quantities_are_not_promoted_to_proxies() -> None:
    identity = replace(F.activity_identity(), composition=None)
    initial_charge = {"composition_wt_pct": {"SiO2": 50, "MgO": 50}}
    excluded_quantities = (
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "equilibrium_phase_composition"),
        (Quantity.RESIDUE_COMPONENT_COMPOSITION, "residue_component_composition"),
        (None, "composition"),
    )

    for quantity, quantity_as_printed in excluded_quantities:
        unchanged, conditions, notices = _initial_charge_only_composition_proxy(
            identity,
            None,
            (),
            row={"composition_role": INITIAL_CHARGE_ONLY},
            quantity=quantity,
            quantity_as_printed=quantity_as_printed,
            locator=None,
            origin="excluded-composition-quantity",
            initial_charge_context=initial_charge,
        )
        assert unchanged.composition is None, (quantity, quantity_as_printed)
        assert conditions is None, (quantity, quantity_as_printed)
        assert notices == [], (quantity, quantity_as_printed)


def test_unknown_composition_role_is_queued_not_stamped(tmp_path) -> None:
    root = _write_min_tree(
        tmp_path, _series_extract({"composition_role": "initial_charge"})
    )
    result = migrate(root, write=False)
    points = _point_compositions(result, "fixture-source::fixture_series")
    assert len(points) == 1
    _assert_partial_analysis_refusal(points[0][1])
    assert any(
        "composition_role 'initial_charge' is not one of" in entry.why
        for entry in result.queue
    )


def test_unknown_values_composition_role_is_queued_for_plante_row(tmp_path) -> None:
    import yaml

    source = "kems-042-plante-1979.yaml"
    document = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / source).read_text(
            encoding="utf-8"
        )
    )
    row_id = "plante1979_table2_s1104_r001_quoted"
    values = next(
        observation["values"]
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation.get("observation_id") == row_id
    )
    values["composition_role"] = "not_a_role"

    result = _migrate_real_extract(tmp_path, source, extract_doc=document)
    observation = next(
        row
        for observation_id, row in result.observations.items()
        if observation_id.endswith(row_id)
    )

    assert observation.identity.composition.is_value
    assert observation.identity.composition.value.proxy_flag == INITIAL_CHARGE_ONLY
    assert any(is_role_default_reason(notice.reason) for notice in observation.notices)
    assert any(
        entry.observation_id == observation.observation_id
        and "composition_role 'not_a_role' is not one of" in entry.why
        for entry in result.queue
    )


def test_unknown_basu_feo_row_with_partial_composition_is_queued(tmp_path) -> None:
    """A role default cannot make the incomplete Table II assay callable."""

    import yaml

    source = "basu-2008-feo-steelmaking.yaml"
    document = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / source).read_text(
            encoding="utf-8"
        )
    )
    row_id = "basu_2008_tii_s01_a_feo_1873k"
    values = next(
        observation["values"]
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation.get("observation_id") == row_id
    )
    values["composition_role"] = "not_a_role"

    result = _migrate_real_extract(tmp_path, source, extract_doc=document)
    observation = next(
        row
        for observation_id, row in result.observations.items()
        if observation_id.endswith(row_id)
    )
    point = observation.point_conditions or {}
    assert "composition" not in point or not point["composition"].state.is_value
    assert not any(is_role_default_reason(notice.reason) for notice in observation.notices)
    assert any(
        entry.observation_id == observation.observation_id
        and "composition_role 'not_a_role' is not one of" in entry.why
        for entry in result.queue
    )


def test_unknown_hastie_values_composition_role_is_queued_once(tmp_path) -> None:
    import yaml

    source = "kems-020-hastie-1981-nbsir.yaml"
    document = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / source).read_text(
            encoding="utf-8"
        )
    )
    row_id = "hastie_1981_fig11_k1_slag_k2o_gamma_digitized"
    values = next(
        observation["values"]
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation.get("observation_id") == row_id
    )
    values["composition_role"] = "not_a_role"

    result = _migrate_real_extract(tmp_path, source, extract_doc=document)
    observation = next(
        row
        for observation_id, row in result.observations.items()
        if f"::{row_id}::T=" in observation_id
    )
    entries = [
        entry
        for entry in result.queue
        if entry.observation_id == observation.observation_id
        and "composition_role 'not_a_role' is not one of" in entry.why
    ]
    assert len(entries) == 1


def test_markova_1983_initial_sample_points_use_flagged_proxy(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-025-markova-1983.yaml")
    points = _point_compositions(result, "kems-025-markova-1983::")
    assert len(points) == 25
    for _, conditions in points:
        _assert_initial_charge_proxy(conditions)


# Hastie Table 2 (NBSIR 81-2279) point ids on green 61ec839da. Footnote a says
# the System column prints initial compositions; only these four rows also
# print a run K2O below that initial value (K1 "~ 14" vs 19.5, Western
# "18.9-17.6" vs 22.7, Eastern "23.3-22.1" vs 23.6, K2 "6" vs 8.7).
_HASTIE_ORIGINAL = "kems-020-hastie-1981-nbsir::hastie_1981_table2_k_logP_coefficients::rows:"
_HASTIE_SLAG_INITIAL_CHARGE_IDS = {
    _HASTIE_ORIGINAL + "h=e3f647c078ee",  # K1
    _HASTIE_ORIGINAL + "h=c3c57e8f6757",  # synthetic Western
    _HASTIE_ORIGINAL + "h=5990a63e7c0b",  # synthetic Eastern
    _HASTIE_ORIGINAL + "h=65d014284ac8",  # K2
}
# Illite prints K2O 7.4 and no different run K2O: its map is the printed
# analysis (df0dea51b kept it for that reason), so it carries no role.
_HASTIE_ILLITE_IDS = {
    _HASTIE_ORIGINAL + "h=0ec91612e9c7",
    "kems-020-hastie-1981-nbsir::hastie_1981_table2_k_logP_coefficients_quoted_20260906"
    "::rows:h=34540a470ebb",
}


def test_hastie_1981_table2_depleted_slag_maps_use_flagged_proxy(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-020-hastie-1981-nbsir.yaml")
    points = dict(
        _point_compositions(
            result, "kems-020-hastie-1981-nbsir::hastie_1981_table2_k_logP_coefficients"
        )
    )
    # Ids are unchanged by the row-level declaration (series_row_extra skip).
    assert set(points) == _HASTIE_SLAG_INITIAL_CHARGE_IDS | _HASTIE_ILLITE_IDS
    for obs_id in _HASTIE_SLAG_INITIAL_CHARGE_IDS:
        _assert_initial_charge_proxy(points[obs_id])
    for obs_id in _HASTIE_ILLITE_IDS:
        composition = points[obs_id]["composition"].state.value
        # x(K2O) for the printed 7.4 wt% illite analysis.
        assert abs(float(dict(composition.components)["K2O"]) - 0.055513) < 1e-6
        assert composition.proxy_flag is None, obs_id


def test_composition_role_does_not_rename_a_series_row() -> None:
    from simulator.battery.stable_ids import series_row_extra

    row = {"system": "illite", "composition_wt_pct": {"K2O": 7.4, "SiO2": 60.2}}
    declared = {
        **row,
        "composition_role": {
            "value": INITIAL_CHARGE_ONLY,
            "locator": {"page": 24, "table": "2"},
            "quote": "6[K2O].",
        },
    }
    assert series_row_extra(declared) == series_row_extra(row)
    assert series_row_extra(declared, include_locator=False) == series_row_extra(
        row, include_locator=False
    )


# --- the four ``rg proxy_flag tests`` hits: catalogue token paths ---------


def test_catalogue_proxy_flag_paths_keep_their_token() -> None:
    values = {
        "composition_from_sample_catalog": True,
        "composition_source": (
            "Lunar Sample Compendium entry 10017. "
            "Selection rule: first complete named whole-sample analysis."
        ),
        "sample_oxide_composition_wt_pct": {"SiO2": 50, "CaO": 50},
    }
    located = _catalogue_composition_located_from_values(values, None)
    assert located is not None
    assert located.state.value.proxy_flag == "composition_from_sample_catalog"
    sample = sample_from_equipment({}, values=values)
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.value.proxy_flag == (
        "composition_from_sample_catalog"
    )


def _composition(proxy_flag: str | None) -> Composition:
    return Composition(
        basis="printed_oxides",
        components=(("SiO2", Decimal("0.5")), ("CaO", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag=proxy_flag,
    )


def test_catalogue_notice_is_specific_to_the_catalogue_token() -> None:
    experiment = F.kems_experiment()
    for flag, expect_notice in (
        ("composition_from_sample_catalog", True),
        (None, False),
        (INITIAL_CHARGE_ONLY, False),
    ):
        identity = F.activity_identity(composition=_composition(flag))
        reference = F.observation(
            f"catalogue-{flag}",
            experiment.experiment_id,
            identity,
            Decimal("0.2"),
            source_id="work-1",
        )
        notice = _catalogue_composition_notice(reference)
        if expect_notice:
            assert notice is not None
            assert notice.kind is NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG
        else:
            assert notice is None


# --- identity: any proxy_flag drops the fixed-valence fO2 omission --------


def test_identity_fixed_valence_requires_no_proxy_flag() -> None:
    base = F.activity_identity(composition=_composition(None))
    assert _composition_is_fixed_valence(base) is True
    for flag in ("composition_from_sample_catalog", INITIAL_CHARGE_ONLY):
        flagged = replace(base, composition=State.of(_composition(flag)))
        assert _composition_is_fixed_valence(flagged) is False


# --- score: residual stratum for a stamped point composition --------------


def _initial_charge_proxy_residual():
    from simulator.battery.enums import Engine, EvidenceClass
    from simulator.battery.score import compile_residual
    from tests.battery.test_score import _context, _predict

    experiment = F.kems_experiment()
    identity = F.activity_identity(
        composition=_composition(INITIAL_CHARGE_ONLY)
    )
    reference = F.observation(
        "initial-charge-point",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="work-1",
    )
    reference = replace(
        reference,
        point_conditions={
            "composition": Located(State.of(_composition(INITIAL_CHARGE_ONLY)))
        },
    )
    persisted_notice = _initial_charge_composition_notice(reference)
    assert persisted_notice is not None
    reference = replace(reference, notices=(persisted_notice,))
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("0.3"), identity),
    )
    return residual, persisted_notice


def test_initial_charge_proxy_prediction_stays_flagged() -> None:
    residual, persisted_notice = _initial_charge_proxy_residual()
    assert residual.numeric is not None
    assert residual.refusal is None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    role_notices = [
        notice
        for notice in residual.notices
        if "composition_role=initial_charge_only" in notice.reason
    ]
    assert len(role_notices) == 1
    assert role_notices[0] == persisted_notice
    assert role_notices[0].kind is NoticeKind.SOURCE_DISAGREEMENT
    assert FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT in flagged_strata(
        residual.notices
    )
