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
from simulator.battery.score import _catalogue_composition_notice
from tests.battery import factories as F
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree

INITIAL_CHARGE_ONLY = "initial_charge_only"


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


def test_declared_initial_charge_point_map_dual_promotes(tmp_path) -> None:
    conditions = _fixture_points(
        tmp_path, {"composition_role": INITIAL_CHARGE_ONLY}
    )
    composition = _assert_dual_promotion(conditions)
    # Pin of the current reader: the role is not read yet.
    assert composition.proxy_flag is None


def test_markova_1983_initial_sample_points_dual_promote(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-025-markova-1983.yaml")
    points = _point_compositions(result, "kems-025-markova-1983::")
    assert len(points) == 25
    flags = {_assert_dual_promotion(conditions).proxy_flag for _, conditions in points}
    assert flags == {None}


def test_hastie_1981_table2_initial_maps_dual_promote(tmp_path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-020-hastie-1981-nbsir.yaml")
    points = _point_compositions(
        result, "kems-020-hastie-1981-nbsir::hastie_1981_table2_k_logP_coefficients"
    )
    assert len(points) == 6
    flags = {_assert_dual_promotion(conditions).proxy_flag for _, conditions in points}
    assert flags == {None}


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


def test_stamped_point_composition_residual_stratum() -> None:
    residual = _stamped_point_residual()
    assert residual.numeric is not None
    # Pin of the current scorer: no composition-role notice yet.
    assert "not_flagged_stratum" not in residual.exclusions
    assert not any(
        "initial_charge_only" in notice.reason for notice in residual.notices
    )
