from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.engine_cache import _engine_cache_path_scope
from tests.battery.engine_cache_score_equivalence import (
    assert_residue_score_cache_equivalence,
)
from tests.battery.test_sossi_residue_predictions import ROOT, SOURCE

pytestmark = pytest.mark.engine_cache_off


def test_scored_sossi_residues_match_off_cold_warm_and_parallel_cache(
    tmp_path: Path,
) -> None:
    assert_residue_score_cache_equivalence(
        tmp_path,
        source="kems-012-sossi-2019",
        # Short 900 s runs at 1573.15 K and 1673.15 K. Each has supported Mn/Ti
        # cells and admitted Gd/La/Sc/V/Yb/Zr cells refused by the scorer.
        test_experiment_ids=(
            "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16c",
            "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-1m-ps2",
        ),
        required_refusal_reason="outside_supported_species",
    )


def test_fresh_sossi_cohorts_reuse_warm_engine_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openimcc = pytest.importorskip("openimcc")

    import simulator.battery.residue as residue
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.melt_backend import openimcc_bridge

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )

    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    experiment_id = str(experiment["experiment_id"])
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())

    def no_evaporation(inventory, *_args, **_kwargs):
        return SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    record_engine_open = engine_cache.record_engine_open
    recorded_opens: list[str] = []

    def capture_engine_open(engine_id: str, backend=None):
        recorded_opens.append(engine_id)
        return record_engine_open(engine_id, backend)

    monkeypatch.setattr(engine_cache, "record_engine_open", capture_engine_open)

    original_gas_evaluate = openimcc.evaluate_gas
    openimcc_gas_calls = 0

    def count_openimcc_gas(*args, **kwargs):
        nonlocal openimcc_gas_calls
        openimcc_gas_calls += 1
        return original_gas_evaluate(*args, **kwargs)

    monkeypatch.setattr(openimcc, "evaluate_gas", count_openimcc_gas)

    original_analytical_evaluate = (
        battery._internal_analytical_vapor_pressure_adapter
    )
    analytical_vapor_calls = 0

    def count_analytical_vapor(**kwargs):
        nonlocal analytical_vapor_calls
        analytical_vapor_calls += 1
        return original_analytical_evaluate(**kwargs)

    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", count_analytical_vapor
    )

    def predict(engine: str):
        return residue._predict_sossi_residue_cohort(
            [experiment],
            catalog,
            starting_trace_ppm_by_experiment={experiment_id: starting_trace},
            buffered_fO2_log_by_experiment={experiment_id: -10.0},
            engine=engine,
        )

    engine_cache.engine_cache_counters(reset=True)
    engine_cache.engine_cache_runtime_identities(reset=True)
    with _engine_cache_path_scope(tmp_path / "sossi-openimcc.sqlite"):
        openimcc_cold = predict("openimcc")
        openimcc_warm = predict("openimcc")
    assert openimcc_cold == openimcc_warm
    assert tuple(row.provenance["law_disclosures"] for row in openimcc_cold) == tuple(
        row.provenance["law_disclosures"] for row in openimcc_warm
    )
    assert openimcc_gas_calls == 1
    assert recorded_opens == ["openimcc", "openimcc"]
    assert "openimcc" in engine_cache.engine_cache_runtime_identities()
    openimcc_counts = engine_cache.engine_cache_counters(reset=True)["openimcc"]
    assert openimcc_counts["misses"] > 0
    assert openimcc_counts["hits"] > 0

    recorded_opens.clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    with _engine_cache_path_scope(tmp_path / "sossi-internal-analytical.sqlite"):
        analytical_cold = predict("internal-analytical")
        analytical_warm = predict("internal-analytical")
    assert analytical_cold == analytical_warm
    assert tuple(
        row.provenance["law_disclosures"] for row in analytical_cold
    ) == tuple(
        row.provenance["law_disclosures"] for row in analytical_warm
    )
    assert analytical_vapor_calls == 1
    assert recorded_opens == ["internal-analytical", "internal-analytical"]
    assert "internal-analytical" in engine_cache.engine_cache_runtime_identities()
    analytical_counts = engine_cache.engine_cache_counters(reset=True)[
        "internal-analytical"
    ]
    assert analytical_counts["misses"] > 0
    assert analytical_counts["hits"] > 0


def test_internal_analytical_engine_cache_replays_direct_pressure_disclosures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.battery.residue as residue
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )
    from simulator.melt_backend.base import equilibrium_cache_value
    from simulator.physical_constants import PA_PER_BAR
    from simulator.vapour_rail.run_disclosure import active_snapshot, disclosure_scope

    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    schedule = experiment["thermal_schedule"]
    temperature_K = residue._hashimoto_point_value(
        schedule["setpoints_and_holds"][0]["temperature_K"]
    )
    pressure_Pa = residue._hashimoto_point_value(
        experiment["pressure_environment"]["total_pressure_Pa"]
    )
    inventory, _, _ = residue._sossi_initial_inventory(experiment, starting_trace)
    core = battery._new_internal_analytical_core()
    request = {
        "temperature_C": temperature_K - 273.15,
        "pressure_bar": pressure_Pa / PA_PER_BAR,
        "composition_kg": None,
        "composition_mol": inventory,
        "fO2_log": -10.0,
        "po2_request": Po2Request(PO2_COMMANDED, 10.0**-10.0),
        "include_diagnostic_shadows": False,
    }

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache.engine_cache_counters(reset=True)
    with _engine_cache_path_scope(tmp_path / "direct-residue-pressure.sqlite"):
        with disclosure_scope(inherit=False):
            cold = internal_analytical_residue_vapor_pressure(core=core, **request)
            cold_disclosures = active_snapshot()
        with disclosure_scope(inherit=False):
            warm = internal_analytical_residue_vapor_pressure(core=core, **request)
            warm_disclosures = active_snapshot()

    assert cold_disclosures
    assert warm_disclosures == cold_disclosures
    assert equilibrium_cache_value(
        vars(cold), engine_id="internal-analytical"
    ) == equilibrium_cache_value(vars(warm), engine_id="internal-analytical")
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"][
        "hits"
    ] > 0
