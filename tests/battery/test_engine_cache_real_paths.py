from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.enums import Engine
from simulator.diagnostic_helpers.binary_pot_battery import (
    Po2Request,
    close_battery_engine_handle,
    load_binary_pots,
)
import simulator.diagnostic_helpers.binary_pot_battery as battery
from tests.battery.cache_test_support import (
    CACHE_TEST_ENGINES,
    assert_real_adapter_cache_paths,
    open_available_engine,
)


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES)
def test_parent_setting_mutation_matches_real_spawned_cache_paths(
    tmp_path: Path,
    engine: Engine,
    request,
) -> None:
    handle = open_available_engine(engine, nodeid=request.node.nodeid)
    openimcc_handle = None
    try:
        backend = handle.backend
        pots, _diagnostics = load_binary_pots()
        pot = next(item for item in pots if item.pot_id == "mgo_sio2_40_60")
        pressure_bar = (
            1.0e-6 if engine in {Engine.VAPOROCK, Engine.THERMOENGINE} else 1.0
        )
        po2 = (
            Po2Request("commanded", 1.0e-8)
            if engine
            in {
                Engine.OPENIMCC,
                Engine.MAGEMIN,
                Engine.ALPHAMELTS,
            }
            else Po2Request("engine_default", None)
        )

        if engine is Engine.OPENIMCC:
            backend._pack_name = "published-ext"
            pot = battery.identity_battery_pot(
                "openimcc-loaded-pack-domain", {"SiO2": 50.0, "MgO": 50.0}
            )
        elif engine is Engine.INTERNAL_ANALYTICAL:
            openimcc_handle = open_available_engine(
                Engine.OPENIMCC, nodeid=request.node.nodeid
            )
            backend._oxygen_balance_backend = openimcc_handle.backend
            backend._oxygen_balance_pack = "review-missing-pack"
            pot = battery.identity_battery_pot(
                "cache-path-internal-analytical",
                {"SiO2": 50.0, "FeO": 50.0},
            )
            po2 = Po2Request(
                battery.PO2_OXYGEN_BALANCE_EFFUSION, None, "W"
            )
        elif engine is Engine.ALPHAMELTS:
            if backend._mode != "subprocess":
                if backend._binary_path is None:
                    pytest.skip(
                        f"{request.node.nodeid}: AlphaMELTS subprocess binary "
                        "missing; the Python API "
                        "marker witness is supplementary"
                    )
                pytest.fail(
                    f"AlphaMELTS initialized unexpected transport {backend._mode!r}"
                )
            pot = next(
                item for item in pots if item.pot_id == "feo_sio2_40_60"
            )
            po2 = Po2Request("engine_default", None)
            backend._redox_buffer = (
                "NNO" if backend._redox_buffer != "NNO" else "QFM"
            )
        elif engine is Engine.THERMOENGINE:
            backend._model = "review-nonexistent-model"
        elif engine is Engine.VAPOROCK:
            backend._temperature_units = (
                "K" if backend._temperature_units != "K" else "C"
            )
        elif engine is Engine.MAGEMIN:
            pool = getattr(backend, "_subprocess_pool", None)
            captured_buffer = (
                pool.workers[0]._bootstrap_args[2].get("fO2_buffer")
                if pool is not None and pool.workers
                else None
            )
            backend._config["fO2_buffer"] = "nno"
            vector = battery._engine_cache_request_vector(
                handle,
                pot,
                temperature_K=1700.0,
                po2=po2,
                physical_pressure_bar=pressure_bar,
                native_activity_formula=None,
            )
            assert vector is not None
            assert vector["engine_settings"]["fO2_buffer"] == (
                captured_buffer
                if pool is not None and pool.workers
                else "nno"
            )

        assert_real_adapter_cache_paths(
            handle,
            pot,
            tmp_path=tmp_path,
            cache_stem=engine.value,
            common={
                "temperature_K": (
                    2000.0 if engine is Engine.OPENIMCC else 1700.0
                ),
                "po2": po2,
                "timeout_s": {
                    Engine.INTERNAL_ANALYTICAL: 60.0,
                    Engine.THERMOENGINE: 60.0,
                    Engine.MAGEMIN: 90.0,
                }.get(engine, 60.0),
                "physical_pressure_bar": pressure_bar,
            },
        )
        if engine is Engine.OPENIMCC:
            vector = battery._engine_cache_request_vector(
                handle,
                pot,
                temperature_K=2000.0,
                po2=po2,
                physical_pressure_bar=pressure_bar,
                native_activity_formula=None,
            )
            assert vector is not None
            assert vector["engine_settings"]["pack"] == backend._identity["pack"]
    finally:
        close_battery_engine_handle(handle)
        if openimcc_handle is not None:
            close_battery_engine_handle(openimcc_handle)


def test_internal_analytical_oxygen_pack_mutation_matches_real_spawned_paths(
    tmp_path: Path,
    request,
) -> None:
    handle = open_available_engine(
        Engine.INTERNAL_ANALYTICAL, nodeid=request.node.nodeid
    )
    openimcc_handle = open_available_engine(
        Engine.OPENIMCC, nodeid=request.node.nodeid
    )
    try:
        backend = handle.backend
        backend._oxygen_balance_backend = openimcc_handle.backend
        backend._oxygen_balance_pack = "review-missing-pack"
        effective_pack = backend._oxygen_balance_backend._identity["pack"]
        backend._oxygen_balance_backend._pack_name = "published-ext"
        pot = battery.identity_battery_pot(
            "oxygen-pack-real-worker", {"SiO2": 50.0, "FeO": 50.0}
        )
        po2 = Po2Request(battery.PO2_OXYGEN_BALANCE_EFFUSION, None, "W")
        assert_real_adapter_cache_paths(
            handle,
            pot,
            tmp_path=tmp_path,
            cache_stem="internal-analytical-oxygen-pack",
            common={
                "temperature_K": 2000.0,
                "po2": po2,
                "timeout_s": 60.0,
                "physical_pressure_bar": 1.0,
            },
        )
        vector = battery._engine_cache_request_vector(
            handle,
            pot,
            temperature_K=2000.0,
            po2=po2,
            physical_pressure_bar=1.0,
            native_activity_formula=None,
        )
        assert vector is not None
        assert vector["engine_settings"]["oxygen_balance_backend"]["pack"] == effective_pack
    finally:
        close_battery_engine_handle(handle)
        close_battery_engine_handle(openimcc_handle)
