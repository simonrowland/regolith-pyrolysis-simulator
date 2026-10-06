"""b-716: composition role on the existing ``Composition.proxy_flag``.

Pins, committed before the reader change:

* a series point that prints an oxide wt% map is promoted twice: the printed
  map on ``point_conditions.printed_composition`` and a typed mole-fraction
  ``Composition`` (with its wt%->mole derivation) on
  ``point_conditions.composition``;
* the existing catalogue ``proxy_flag`` paths (the four ``proxy_flag`` hits
  under ``tests/``) keep their token and their catalogue notice;
* identity drops the fixed-valence fO2 omission for any non-None
  ``proxy_flag``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import AmountBasis, NoticeKind
from simulator.battery.identity import _composition_is_fixed_valence
from simulator.battery.migrate import (
    _catalogue_composition_located_from_values,
    migrate,
    sample_from_equipment,
)
from simulator.battery.records import Composition, Located, State
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG as INITIAL_CHARGE_ONLY,
)
from simulator.battery.score import _catalogue_composition_notice
from tests.battery import factories as F
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree


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


def _fixture_points(tmp_path: Path, values_extra: dict | None = None):
    root = _write_min_tree(tmp_path, _series_extract(values_extra))
    result = migrate(root, write=False)
    points = _point_compositions(result, "fixture-source::fixture_series")
    assert len(points) == 1
    return points[0][1]


def _assert_dual_promotion(conditions) -> Composition:
    printed = conditions["printed_composition"]
    assert isinstance(printed, Located) and printed.state.is_value
    assert len(printed.state.value) >= 2
    typed = conditions["composition"]
    assert isinstance(typed, Located) and typed.state.is_value
    composition = typed.state.value
    assert isinstance(composition, Composition)
    assert composition.amount_basis is AmountBasis.MOLE_FRACTION
    assert composition.basis == "printed_oxides"
    assert typed.inference is not None
    assert typed.inference.relation == "wt_pct_to_mole_fraction"
    return composition


def test_undeclared_point_oxide_map_dual_promotes_without_proxy_flag(tmp_path) -> None:
    composition = _assert_dual_promotion(_fixture_points(tmp_path))
    assert composition.proxy_flag is None


def test_declared_initial_charge_point_map_is_stamped_and_kept(tmp_path) -> None:
    conditions = _fixture_points(
        tmp_path, {"composition_role": INITIAL_CHARGE_ONLY}
    )
    # Option (a): still on both melt channels, now carrying the role.
    composition = _assert_dual_promotion(conditions)
    assert composition.proxy_flag == INITIAL_CHARGE_ONLY


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
    )
    assert _assert_dual_promotion(conditions).proxy_flag == INITIAL_CHARGE_ONLY


def test_unknown_composition_role_is_queued_not_stamped(tmp_path) -> None:
    root = _write_min_tree(
        tmp_path, _series_extract({"composition_role": "initial_charge"})
    )
    result = migrate(root, write=False)
    points = _point_compositions(result, "fixture-source::fixture_series")
    assert len(points) == 1
    assert _assert_dual_promotion(points[0][1]).proxy_flag is None
    assert any(
        "composition_role 'initial_charge' is not one of" in entry.why
        for entry in result.queue
    )


def test_markova_1983_initial_sample_points_are_stamped(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-025-markova-1983.yaml")
    points = _point_compositions(result, "kems-025-markova-1983::")
    assert len(points) == 25
    flags = {_assert_dual_promotion(conditions).proxy_flag for _, conditions in points}
    assert flags == {INITIAL_CHARGE_ONLY}


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


def test_hastie_1981_table2_only_depleted_slag_maps_are_stamped(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-020-hastie-1981-nbsir.yaml")
    points = dict(
        _point_compositions(
            result, "kems-020-hastie-1981-nbsir::hastie_1981_table2_k_logP_coefficients"
        )
    )
    # Ids are unchanged by the row-level declaration (series_row_extra skip).
    assert set(points) == _HASTIE_SLAG_INITIAL_CHARGE_IDS | _HASTIE_ILLITE_IDS
    flags = {
        obs_id: _assert_dual_promotion(conditions).proxy_flag
        for obs_id, conditions in points.items()
    }
    for obs_id in _HASTIE_SLAG_INITIAL_CHARGE_IDS:
        assert flags[obs_id] == INITIAL_CHARGE_ONLY, obs_id
    for obs_id in _HASTIE_ILLITE_IDS:
        composition = points[obs_id]["composition"].state.value
        # x(K2O) for the printed 7.4 wt% illite analysis.
        assert abs(float(dict(composition.components)["K2O"]) - 0.055513) < 1e-6
        assert flags[obs_id] is None, obs_id


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


def _stamped_point_residual():
    from simulator.battery.enums import Engine, EvidenceClass
    from simulator.battery.score import compile_residual
    from tests.battery.test_score import _context, _predict

    experiment = F.kems_experiment()
    identity = F.activity_identity()
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
    context = _context(F.work(), experiment, reference, review="reviewed")
    residual, _ = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(Decimal("0.3"), identity),
    )
    return residual


def test_stamped_point_composition_is_predicted_and_flagged() -> None:
    from simulator.battery.score import (
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
        flagged_strata,
    )

    residual = _stamped_point_residual()
    # Predict-and-flag: the number is kept, the row leaves the headline.
    assert residual.numeric is not None
    assert residual.score_eligible is False
    assert "not_flagged_stratum" in residual.exclusions
    role_notices = [
        notice
        for notice in residual.notices
        if "composition_role=initial_charge_only" in notice.reason
    ]
    assert len(role_notices) == 1
    assert role_notices[0].kind is NoticeKind.SOURCE_DISAGREEMENT
    assert flagged_strata(residual.notices) == (
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
    )
