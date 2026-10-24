"""Shared skip probes for live MAGEMin and ThermoEngine tests."""

from __future__ import annotations


def magemin_binary_available() -> bool:
    try:
        from simulator.engine_local_config import configured_magemin_binary_path
        from simulator.melt_backend.magemin import MAGEMinBackend

        if configured_magemin_binary_path() is not None:
            return True
        return MAGEMinBackend._locate_binary(None) is not None
    except Exception:
        return False


def thermoengine_package_available() -> bool:
    try:
        from simulator.engine_local_config import setup_thermoengine_dylib_path

        setup_thermoengine_dylib_path()
        import thermoengine  # noqa: F401

        return True
    except Exception:
        return False
