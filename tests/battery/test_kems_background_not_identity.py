"""Pin: knudsen_effusion chamber background must not enter observation identity.

REQ-knudsen-vacuum-withdrawn-new-shape / review/kems-background-not-identity.
Halwax 2024 is the real knudsen case (8 identity fills of 0.001 Pa on green).
Non-effusion controls (langmuir + transpiration) keep experiment→identity inheritance.
Plante scored population is pinned by the existing openimcc tests named in the REQ.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import MethodToken, Quantity, ValueKind
from simulator.battery.records import Value
from simulator.battery.validity import background_pressure_high
from tests.battery.test_migrate import _migrate_real_extract

REPO_ROOT = Path(__file__).resolve().parents[2]

# Local suffixes from the vacuum sweep / withdrawn demote REPORT.
HALWAX_IDENTITY_FILL_SUFFIXES = (
    "halwax_2024_geometry_not_alpha_b1",
    "halwax_2024_mgo_kems_geometry_psat_package",
    "halwax_2024_mgo_third_law_formation_enthalpy",
    "halwax_2024_table_i_mgo_sample_masses::point:0",
    "halwax_2024_table_i_mgo_sample_masses::point:1",
    "halwax_2024_table_iv_mgo_1st_measurement",
    "halwax_2024_table_iv_mgo_2nd_measurement",
    "halwax_2024_table_iv_mgo_mean",
)


def _halwax_filled_observations(result):
    rows = []
    for observation_id, observation in result.observations.items():
        if observation.source_id != "kems-031-halwax-2024":
            continue
        if any(observation_id.endswith(suffix) for suffix in HALWAX_IDENTITY_FILL_SUFFIXES):
            rows.append(observation)
    return rows


def test_knudsen_effusion_experiment_total_is_not_inherited_into_identity(tmp_path: Path) -> None:
    """Halwax: experiment keeps numeric total; the eight obs do not acquire it as identity."""

    result = _migrate_real_extract(tmp_path, "kems-031-halwax-2024.yaml")
    mgo = next(
        experiment
        for experiment_id, experiment in result.experiments.items()
        if experiment_id.endswith("::experiment::mgo-series")
    )
    assert mgo.method.is_value and mgo.method.value is MethodToken.KNUDSEN_EFFUSION
    total = mgo.pressure_environment.total_pressure_Pa
    assert total.state.is_value
    value = total.state.value
    assert isinstance(value, Value)
    # Green stores a point 0.001 Pa; after the bound extract edit the gate still
    # sees a typed upper bound. Either shape must remain numeric on the experiment.
    if value.kind is ValueKind.POINT:
        assert value.point == Decimal("0.001")
    else:
        assert value.kind is ValueKind.BOUND
        assert value.bound_operator in {"<", "<="}
        assert value.bound_value == Decimal("0.001")

    # Validity gates still read experiment.pressure_environment.total_pressure_Pa.
    gate = background_pressure_high(mgo, Quantity.P_PARTIAL)
    assert gate.passed
    assert any(check.name == "background_pressure" for check in gate.checks)

    rows = _halwax_filled_observations(result)
    assert len(rows) == 8
    for observation in rows:
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is None or identity_pressure.is_unknown, (
            f"{observation.observation_id} wrongly inherited experiment chamber "
            f"background as identity.total_pressure_Pa={identity_pressure!r}"
        )
        provenance = observation.derivation.relation if observation.derivation else ""
        trail = ()
        if observation.derivation is not None:
            trail = observation.derivation.inputs
        joined = " ".join(str(item) for item in trail) + " " + str(provenance)
        assert "identity.total_pressure_Pa=experiment.pressure_environment" not in joined


def test_langmuir_free_evaporation_still_inherits_experiment_total(tmp_path: Path) -> None:
    """CORRECT langmuir control (Fedkin 2006): identity inheritance unchanged."""

    result = _migrate_real_extract(tmp_path, "kems-005-fedkin-2006.yaml")
    expected = Decimal("0.001300000")
    inherited = []
    for experiment_id, experiment in result.experiments.items():
        if not (
            experiment.method.is_value
            and experiment.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
        ):
            continue
        total = experiment.pressure_environment.total_pressure_Pa
        if not total.state.is_value:
            continue
        value = total.state.value
        if not (isinstance(value, Value) and value.kind is ValueKind.POINT):
            continue
        if value.point != expected:
            continue
        for observation in result.observations.values():
            if observation.experiment_id != experiment_id:
                continue
            identity_pressure = observation.identity.total_pressure_Pa
            if (
                identity_pressure is not None
                and identity_pressure.is_value
                and identity_pressure.value == expected
            ):
                inherited.append(observation.observation_id)
                break
    assert inherited, "expected at least one langmuir observation to inherit experiment total"


def test_transpiration_still_inherits_experiment_total(tmp_path: Path) -> None:
    """CORRECT transpiration control (Dacko/Conradt): identity inheritance unchanged."""

    result = _migrate_real_extract(tmp_path, "ta-dacko-conradt-low-p-transpiration.yaml")
    expected = Decimal("100000")
    inherited = []
    for experiment_id, experiment in result.experiments.items():
        if not (
            experiment.method.is_value
            and experiment.method.value is MethodToken.TRANSPIRATION
        ):
            continue
        total = experiment.pressure_environment.total_pressure_Pa
        if not total.state.is_value:
            continue
        value = total.state.value
        if not (isinstance(value, Value) and value.kind is ValueKind.POINT):
            continue
        if value.point != expected:
            continue
        for observation in result.observations.values():
            if observation.experiment_id != experiment_id:
                continue
            identity_pressure = observation.identity.total_pressure_Pa
            if (
                identity_pressure is not None
                and identity_pressure.is_value
                and identity_pressure.value == expected
            ):
                inherited.append(observation.observation_id)
                break
    assert inherited, "expected at least one transpiration observation to inherit experiment total"
