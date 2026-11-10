from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from simulator.battery.enums import Engine
from simulator.battery.score import _equilibrate_phase_assemblage
from simulator.diagnostic_helpers.binary_pot_battery import (
    close_battery_engine_handle,
    open_battery_engine,
)
from simulator.melt_backend.base import EquilibriumResult
from tests.battery.cache_test_support import CACHE_TEST_ENGINES


@dataclass(frozen=True)
class _SettingCase:
    label: str
    engine: Engine
    backend_factory: Callable[[], Any]
    mutate: Callable[[Any], None]
    output: Callable[[Any], float]


class _Worker:
    def __init__(self, bootstrap_args: tuple[Any, ...]) -> None:
        self._bootstrap_args = bootstrap_args

    def close(self) -> None:
        pass


class _Pool:
    def __init__(self, worker: _Worker) -> None:
        self.workers = [worker]

    def close(self, *, cancel_pending: bool) -> None:
        del cancel_pending


def _setting_cases() -> tuple[_SettingCase, ...]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.magemin import MAGEMinBackend
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    def alpha_backend() -> Any:
        backend = AlphaMELTSBackend()
        backend._mode = "subprocess"
        backend._model = "MELTS-v1.0"
        backend._redox_buffer = "QFM"
        backend._fo2_offset = 0.0
        backend._configured_vapor_transport_pO2_bar = 1.0e-8
        backend._configured_vapor_transport_floor_bar = 1.0e-9
        return backend

    def magemin_backend() -> Any:
        backend = MAGEMinBackend()
        backend._available = True
        backend._bridge = "subprocess"
        backend._config = {"fO2_buffer": "qfm"}
        backend._subprocess_pool = _Pool(
            _Worker(("magemin", "ig", {"fO2_buffer": "qfm"}))
        )
        return backend

    def thermoengine_backend() -> Any:
        backend = ThermoEngineBackend()
        backend._mode = "thermoengine"
        backend._model = "MELTS-v1.0"
        backend._thermoengine_transport = SimpleNamespace(
            _model_name="MELTS-v1.0", close=lambda: None
        )
        return backend

    def vaporock_backend() -> Any:
        from simulator.melt_backend.vaporock import VapoRockBackend

        backend = VapoRockBackend()
        backend._temperature_units = "C"
        return backend

    def internal_analytical_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _InternalAnalyticalBatteryBackend,
        )

        backend = object.__new__(_InternalAnalyticalBatteryBackend)
        backend._core = object()
        backend._oxygen_balance_backend = SimpleNamespace(
            _pack_name="openimcc-v1.0.2", _model="oxygen-balance-v1"
        )
        backend._oxygen_balance_pack = "openimcc-v1.0.2"
        return backend

    def openimcc_backend() -> Any:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            _OpenImccBatteryBackend,
        )

        backend = object.__new__(_OpenImccBatteryBackend)
        backend._pack_name = "openimcc-v1.0.2"
        backend._identity = {"pack": "openimcc-v1.0.2"}
        return backend

    def alpha_python_api_backend() -> Any:
        backend = alpha_backend()
        backend._mode = "python_api"
        backend._python_api_model = ("pMELTS", 1)
        backend._pet_worker = _Worker((1,))
        return backend

    return (
        _SettingCase(
            "AlphaMELTS redox buffer",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_redox_buffer", "NNO"),
            lambda backend: {"QFM": 0.25, "NNO": 0.75}[backend._redox_buffer],
        ),
        _SettingCase(
            "AlphaMELTS fO2 offset",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fo2_offset", 0.5),
            lambda backend: {0.0: 0.25, 0.5: 0.75}[backend._fo2_offset],
        ),
        _SettingCase(
            "AlphaMELTS Fe3+/Fet ratio",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(backend, "_fe3fet_ratio", 0.3),
            lambda backend: {None: 0.25, 0.3: 0.75}[backend._fe3fet_ratio],
        ),
        _SettingCase(
            "AlphaMELTS configured vapor pO2",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_pO2_bar", 1.0e-3
            ),
            lambda backend: {
                1.0e-8: 0.25,
                1.0e-3: 0.75,
            }[backend._configured_vapor_transport_pO2_bar],
        ),
        _SettingCase(
            "AlphaMELTS configured vapor floor",
            Engine.ALPHAMELTS,
            alpha_backend,
            lambda backend: setattr(
                backend, "_configured_vapor_transport_floor_bar", 1.0e-6
            ),
            lambda backend: {
                1.0e-9: 0.25,
                1.0e-6: 0.75,
            }[backend._configured_vapor_transport_floor_bar],
        ),
        _SettingCase(
            "AlphaMELTS Python API model code",
            Engine.ALPHAMELTS,
            alpha_python_api_backend,
            lambda backend: setattr(backend._pet_worker, "_bootstrap_args", (2,)),
            lambda backend: {
                1: 0.25,
                2: 0.75,
            }[backend._pet_worker._bootstrap_args[0]],
        ),
        _SettingCase(
            "MAGEMin worker database",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(
                backend._subprocess_pool.workers[0],
                "_bootstrap_args",
                ("magemin", "mp", {"fO2_buffer": "qfm"}),
            ),
            lambda backend: {"ig": 0.25, "mp": 0.75}[
                backend._effective_database()
            ],
        ),
        _SettingCase(
            "MAGEMin worker fO2 buffer",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(
                backend._subprocess_pool.workers[0],
                "_bootstrap_args",
                ("magemin", "ig", {"fO2_buffer": "nno"}),
            ),
            lambda backend: {"qfm": 0.25, "nno": 0.75}[
                backend._subprocess_pool.workers[0]._bootstrap_args[2][
                    "fO2_buffer"
                ]
            ],
        ),
        _SettingCase(
            "MAGEMin bridge selection",
            Engine.MAGEMIN,
            magemin_backend,
            lambda backend: setattr(backend, "_bridge", "julia"),
            lambda backend: {"subprocess": 0.25, "julia": 0.75}[backend._bridge],
        ),
        _SettingCase(
            "ThermoEngine transport model",
            Engine.THERMOENGINE,
            thermoengine_backend,
            lambda backend: setattr(
                backend._thermoengine_transport, "_model_name", "rhyolite-MELTS"
            ),
            lambda backend: {
                "MELTS-v1.0": 0.25,
                "rhyolite-MELTS": 0.75,
            }[backend._thermoengine_transport._model_name],
        ),
        _SettingCase(
            "OpenIMCC loaded cell pack",
            Engine.OPENIMCC,
            openimcc_backend,
            lambda backend: (
                setattr(backend, "_pack_name", "openimcc-v1.0.3-ext-v1"),
                backend._identity.__setitem__("pack", "openimcc-v1.0.3-ext-v1"),
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._effective_pack_name],
        ),
        _SettingCase(
            "VapoRock temperature units",
            Engine.VAPOROCK,
            vaporock_backend,
            lambda backend: setattr(backend, "_temperature_units", "K"),
            lambda backend: {"C": 0.25, "K": 0.75}[backend._temperature_units],
        ),
        _SettingCase(
            "Internal-analytical oxygen-balance pack",
            Engine.INTERNAL_ANALYTICAL,
            internal_analytical_backend,
            lambda backend: setattr(
                backend, "_oxygen_balance_pack", "openimcc-v1.0.3-ext-v1"
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._oxygen_balance_pack],
        ),
        _SettingCase(
            "Internal-analytical nested OpenIMCC pack",
            Engine.INTERNAL_ANALYTICAL,
            internal_analytical_backend,
            lambda backend: setattr(
                backend._oxygen_balance_backend,
                "_pack_name",
                "openimcc-v1.0.3-ext-v1",
            ),
            lambda backend: {
                "openimcc-v1.0.2": 0.25,
                "openimcc-v1.0.3-ext-v1": 0.75,
            }[backend._oxygen_balance_backend._pack_name],
        ),
    )


def _simple_setting_leaves(value: Any, path: tuple[Any, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _simple_setting_leaves(item, (*path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _simple_setting_leaves(item, (*path, index))
    elif value is None or isinstance(value, (bool, int, float, str)):
        yield path, value


def _setting_at(root: Any, path: tuple[Any, ...]) -> Any:
    value = root
    for part in path:
        value = value[part] if isinstance(value, (dict, list, tuple)) else getattr(value, part)
    return value


def _replace_setting(root: Any, path: tuple[Any, ...], value: Any) -> None:
    parent = root
    for part in path[:-1]:
        parent = parent[part] if isinstance(parent, (dict, list, tuple)) else getattr(parent, part)
    final = path[-1]
    if isinstance(parent, dict):
        parent[final] = value
    elif isinstance(parent, list):
        parent[final] = value
    elif isinstance(parent, tuple):
        replacement = list(parent)
        replacement[final] = value
        _replace_setting(root, path[:-1], tuple(replacement))
    elif final == "_effective_pack_name" and isinstance(
        getattr(parent, "_identity", None), dict
    ):
        parent._identity["pack"] = value
    else:
        setattr(parent, final, value)


def _alternate_simple_value(value: Any) -> Any:
    if value is None:
        return "cache-setting-test"
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.125
    if isinstance(value, tuple):
        return (*value, "cache-setting-test")
    if isinstance(value, list):
        return [*value, "cache-setting-test"]
    if isinstance(value, dict):
        return {**value, "cache-setting-test": "changed"}
    return value + "-cache-setting-test"


@pytest.mark.parametrize(
    "engine",
    tuple(dict.fromkeys(case.engine for case in _setting_cases())),
    ids=lambda engine: engine.value,
)
def test_scorer_cache_key_covers_every_enumerated_configuration_attribute(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    cases = {case.engine: case for case in _setting_cases()}
    case = cases[engine]
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    backend = case.backend_factory()
    backend_settings = engine_cache.engine_cache_backend_settings(backend)
    paths = tuple(path for path, _value in _simple_setting_leaves(backend_settings))
    assert paths, f"{engine.value} has no discovered configuration attributes"

    for index, path in enumerate(paths):
        backend = case.backend_factory()
        monkeypatch.setattr(
            battery,
            "_open_resolved_backend",
            lambda _name, _engine_settings=None, backend=backend: backend,
        )
        handle = open_battery_engine(engine.value)
        before = _setting_at(backend, path)
        calls = 0

        def fake_equilibrate(**_kwargs: Any) -> EquilibriumResult:
            nonlocal calls
            calls += 1
            current = _setting_at(backend, path)
            return EquilibriumResult(
                status="ok", liquid_fraction=0.25 if current == before else 0.75
            )

        backend.equilibrate = fake_equilibrate

        def score() -> EquilibriumResult:
            return _equilibrate_phase_assemblage(
                engine,
                temperature_C=1000.0 + index,
                composition_kg={"SiO2": 1.0},
                fO2_log=-8.0,
                pressure_bar=1.0,
                handles={engine.value: handle},
            )

        try:
            cold = score()
            _replace_setting(backend, path, _alternate_simple_value(before))
            warm = score()
            assert cold.liquid_fraction == 0.25, (engine.value, path)
            assert warm.liquid_fraction == 0.75, (engine.value, path)
            assert calls == 2, (engine.value, path)
        finally:
            close_battery_engine_handle(handle)


def test_cache_settings_include_unanticipated_simple_configuration_attributes() -> None:
    from simulator.engine_cache import engine_cache_backend_settings

    backend = SimpleNamespace(_new_chemistry_knob="first")
    first = engine_cache_backend_settings(backend)
    backend._new_chemistry_knob = "second"
    second = engine_cache_backend_settings(backend)

    assert first["_new_chemistry_knob"] == "first"
    assert second["_new_chemistry_knob"] == "second"
    assert first != second


def test_cache_settings_do_not_drop_names_only_because_they_contain_runtime_terms() -> None:
    from simulator.engine_cache import engine_cache_backend_settings

    backend = SimpleNamespace(
        chemistry_identity_mode="openimcc",
        custom_version_selection="v1",
        input_digest_formula="sha256",
        input_sha256_algorithm="sha256",
        commit_atom_count=4,
        hostname_is_a_component="H",
        ready_payload_composition="SiO2",
        chemistry_path="phase-a",
    )
    for name, value in vars(backend).items():
        first = engine_cache_backend_settings(backend)
        setattr(backend, name, f"changed-{value}")
        second = engine_cache_backend_settings(backend)
        assert first != second, name


def test_alpha_and_thermoengine_runtime_paths_do_not_enter_settings() -> None:
    from simulator import engine_cache

    backend = SimpleNamespace(
        _binary_path=Path("/engine/one"),
        _engine_path=Path("/engine/one"),
        _last_normalization_warnings=["prior-call diagnostic"],
        _thermoengine_transport=SimpleNamespace(_melts_version="1.0.2"),
    )
    first = engine_cache.engine_cache_backend_settings(backend)
    for path in (
        ("_binary_path",),
        ("_engine_path",),
        ("_last_normalization_warnings",),
        ("_thermoengine_transport", "_melts_version"),
    ):
        reason = engine_cache._setting_drop_reason(path)
        assert reason
        if path == ("_binary_path",):
            backend._binary_path = Path("/engine/two")
        elif path == ("_engine_path",):
            backend._engine_path = Path("/engine/two")
        elif path == ("_last_normalization_warnings",):
            backend._last_normalization_warnings = ["changed diagnostic"]
        else:
            backend._thermoengine_transport._melts_version = "9.9.9"
        assert engine_cache.engine_cache_backend_settings(backend) == first


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES, ids=lambda value: value.value)
def test_every_opened_backend_simple_attribute_changes_key_or_has_a_reason(
    engine: Engine,
) -> None:
    from collections.abc import Mapping
    from enum import Enum
    from types import ModuleType

    from simulator import engine_cache
    from tests.battery.cache_test_support import open_available_engine

    runtime_reasons = engine_cache._RUNTIME_ONLY_SETTING_REASONS
    opaque_paths: set[tuple[Any, ...]] = set()
    property_values: dict[tuple[Any, ...], Any] = {}

    def simple(value: Any) -> bool:
        return value is None or isinstance(value, (bool, int, float, str, Enum))

    def leaves(value: Any, path: tuple[Any, ...] = (), seen: set[int] | None = None):
        if seen is None:
            seen = set()
        if any(path[: len(opaque_path)] == opaque_path for opaque_path in opaque_paths):
            return
        if simple(value):
            yield path, value
            return
        if isinstance(value, (Path, ModuleType)) or callable(value):
            return
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        if isinstance(value, Mapping):
            for name, child in value.items():
                yield from leaves(child, (*path, name), seen)
        elif isinstance(value, (list, tuple, set, frozenset)):
            children = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
            for index, child in enumerate(children):
                yield from leaves(child, (*path, index), seen)
        else:
            try:
                attributes = vars(value)
            except TypeError:
                attributes = {}
            for cls in type(value).__mro__:
                slots = getattr(cls, "__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for name in slots:
                    if name not in {"__dict__", "__weakref__"} and hasattr(
                        value, name
                    ):
                        attributes.setdefault(name, getattr(value, name))
            for name, child in attributes.items():
                yield from leaves(child, (*path, name), seen)
            for cls in type(value).__mro__:
                for name, descriptor in vars(cls).items():
                    if not isinstance(descriptor, property) or name in attributes:
                        continue
                    try:
                        child = getattr(value, name)
                    except AttributeError:
                        continue
                    except Exception:
                        continue
                    child_path = (*path, name)
                    if simple(child):
                        property_values[child_path] = child
                    else:
                        yield from leaves(child, child_path, seen)

    def setting_at(settings: Any, path: tuple[Any, ...]) -> Any:
        current = settings
        for part in path:
            if isinstance(current, Mapping):
                current = current[str(part)]
            elif isinstance(current, list):
                current = current[part]
            else:
                raise AssertionError((path, current))
        return current

    def replace(value: Any, path: tuple[Any, ...], replacement: Any) -> Any:
        if not path:
            return replacement
        part, *tail = path
        if isinstance(value, Mapping):
            updated = dict(value)
            updated[part] = replace(value[part], tuple(tail), replacement)
            if isinstance(value, dict):
                value[part] = updated[part]
                return value
            return updated
        if isinstance(value, list):
            value[part] = replace(value[part], tuple(tail), replacement)
            return value
        if isinstance(value, tuple):
            updated = list(value)
            updated[part] = replace(value[part], tuple(tail), replacement)
            return tuple(updated)
        if isinstance(value, (set, frozenset)):
            updated = sorted(value, key=repr)
            updated[part] = replace(updated[part], tuple(tail), replacement)
            return type(value)(updated)
        child = getattr(value, part)
        updated_child = replace(child, tuple(tail), replacement)
        try:
            setattr(value, part, updated_child)
        except (AttributeError, TypeError):
            object.__setattr__(value, part, updated_child)
        return value

    def alternate(value: Any) -> Any:
        if isinstance(value, Enum):
            alternatives = [member for member in type(value) if member != value]
            return alternatives[0] if alternatives else f"{value.value}-changed"
        if value is None:
            return "cache-setting-test"
        if isinstance(value, bool):
            return not value
        if isinstance(value, int):
            return value + 1
        if isinstance(value, float):
            return value + 0.125
        return f"{value}-cache-setting-test"

    handle = open_available_engine(engine, nodeid="cache settings invariant")
    try:
        settings, dropped, opaque = engine_cache._engine_cache_backend_settings_with_drops(
            handle.backend
        )
        for path, reason in dropped.items():
            assert path and engine_cache._setting_drop_reason(path) == reason, path
            assert reason in runtime_reasons.values(), path
        for path, type_name in opaque.items():
            assert type_name, path
            assert engine_cache._opaque_setting_reason(path), path
        opaque_paths.update(opaque)
        for path, value in property_values.items():
            reason = engine_cache._setting_drop_reason(path)
            if reason is not None:
                assert any(
                    path[: len(dropped_path)] == dropped_path
                    for dropped_path in dropped
                ), path
                assert reason in runtime_reasons.values(), path
            else:
                expected = value.value if isinstance(value, Enum) else value
                assert setting_at(settings, path) == expected, path

        chem_version = engine_cache.ENGINE_CHEMISTRY_VERSION[engine.value]
        baseline_key = engine_cache.engine_cache_key(
            engine.value, chem_version, settings
        )
        assert engine_cache.engine_cache_key(
            engine.value, chem_version + 1, settings
        ) != baseline_key
        for path, original in tuple(leaves(handle.backend)):
            changed = alternate(original)
            if changed == original:
                continue
            replace(handle.backend, path, changed)
            try:
                changed_settings, changed_drops, changed_opaque = (
                    engine_cache._engine_cache_backend_settings_with_drops(
                        handle.backend
                    )
                )
                changed_key = engine_cache.engine_cache_key(
                    engine.value, chem_version, changed_settings
                )
                if changed_key == baseline_key:
                    dropped_prefixes = [
                        dropped_path
                        for dropped_path in changed_drops
                        if path[: len(dropped_path)] == dropped_path
                    ]
                    opaque_prefixes = [
                        opaque_path
                        for opaque_path in changed_opaque
                        if path[: len(opaque_path)] == opaque_path
                    ]
                    assert dropped_prefixes or opaque_prefixes, (engine.value, path)
                    for dropped_path in dropped_prefixes:
                        assert (
                            engine_cache._setting_drop_reason(dropped_path)
                            == changed_drops[dropped_path]
                        ), (engine.value, path, dropped_path)
                    for opaque_path in opaque_prefixes:
                        assert engine_cache._opaque_setting_reason(opaque_path), (
                            engine.value,
                            path,
                            opaque_path,
                        )
            finally:
                replace(handle.backend, path, original)
    finally:
        close_battery_engine_handle(handle)


@pytest.mark.parametrize("engine", CACHE_TEST_ENGINES, ids=lambda value: value.value)
def test_each_real_backend_equilibrate_warm_pair_matches_cache_off(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from tests.battery.cache_test_support import open_available_engine

    handle = open_available_engine(engine, nodeid="real backend cache warm pair")
    pots, _metadata = load_binary_pots()
    pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")
    common = {
        "temperature_K": 1700.0,
        "po2": Po2Request(mode="engine_default", po2_bar=None),
        "timeout_s": 90.0,
        "isolated": False,
    }

    def stable(cell: Any) -> Any:
        return cell.to_engine_cache_value().hot

    try:
        monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
        cache_off = equilibrate_cell(handle, pot, **common)
        monkeypatch.setenv(
            "REGOLITH_ENGINE_CACHE", str(tmp_path / f"{engine.value}.sqlite")
        )
        engine_cache.engine_cache_counters(reset=True)
        cold = equilibrate_cell(handle, pot, **common)
        warm = equilibrate_cell(handle, pot, **common)

        assert stable(cache_off) == stable(cold) == stable(warm)
        assert engine_cache.engine_cache_counters(reset=True)[engine.value] == {
            "hits": 1,
            "misses": 1,
            "stores": 1,
            "store_error": 0,
            "read_error": 0,
        }
    finally:
        close_battery_engine_handle(handle)


def test_disabled_cache_does_not_walk_opaque_equilibrate_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
    )

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    monkeypatch.setattr(
        engine_cache,
        "engine_cache_backend_settings",
        lambda _backend: pytest.fail("disabled cache walked backend settings"),
    )
    handle = EngineHandle(
        name="openimcc",
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    result = EquilibriumResult(status="ok", liquid_fraction=0.75)

    assert _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, lambda: result
    ) is result


def test_disabled_cache_does_not_walk_opaque_internal_analytical_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    monkeypatch.setattr(engine_cache, "record_engine_open", lambda *_args: None)
    monkeypatch.setattr(
        engine_cache,
        "engine_cache_backend_settings",
        lambda _backend: pytest.fail("disabled cache walked backend settings"),
    )
    expected = SimpleNamespace(vapor_pressures_Pa={"SiO(g)": 2.0})
    monkeypatch.setattr(
        battery,
        "_internal_analytical_vapor_pressure_adapter",
        lambda **_kwargs: expected,
    )

    result = internal_analytical_residue_vapor_pressure(
        core=object(),
        temperature_C=1500.0,
        pressure_bar=1.0,
        composition_kg=None,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        po2_request=Po2Request(PO2_COMMANDED, 1e-8),
        include_diagnostic_shadows=False,
    )

    assert result is expected


def test_openimcc_ferric_and_ferrous_requests_have_distinct_cache_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="openimcc", backend=SimpleNamespace())
    calls: list[dict[str, float]] = []

    def solve(composition: dict[str, float]) -> EquilibriumResult:
        calls.append(composition)
        return EquilibriumResult(status="ok", liquid_fraction=0.5)

    ferric = {"SiO2": 0.5, "Fe2O3": 0.1}
    ferrous = {"SiO2": 0.5, "FeO": 0.2}
    for composition in (ferric, ferrous):
        _call_cached_backend_equilibrate(
            handle,
            {"composition_mol": composition, "temperature_C": 1500.0},
            lambda composition=composition: solve(composition),
        )

    assert calls == [ferric, ferrous]
    assert engine_cache.engine_cache_counters(reset=True)["openimcc"]["misses"] == 2


def test_openimcc_unused_pressure_and_caller_timeout_do_not_replace_cached_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="openimcc", backend=SimpleNamespace())
    calls = 0

    def solve() -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return EquilibriumResult(status="ok", liquid_fraction=0.625)

    first = _call_cached_backend_equilibrate(
        handle,
        {
            "temperature_C": 1500.0,
            "pressure_bar": 1.0,
            "call_timeout_s": 30.0,
        },
        solve,
    )
    second = _call_cached_backend_equilibrate(
        handle,
        {
            "temperature_C": 1500.0,
            "pressure_bar": 40.0,
            "call_timeout_s": 0.01,
        },
        lambda: pytest.fail("pressure/timeout-only change should hit cached cell"),
    )

    assert first.liquid_fraction == second.liquid_fraction == 0.625
    assert calls == 1
    assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
    }


def test_isolated_backend_reconstruction_disables_nested_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import simulator.backends as backends
    import simulator.melt_backend.vaporock as vaporock
    from simulator.diagnostic_helpers.binary_pot_battery import _open_resolved_backend

    configs: dict[str, dict[str, Any]] = {}

    class FakeVapoRockBackend:
        def initialize(self, config: dict[str, Any]) -> bool:
            configs["vaporock"] = dict(config)
            return True

    monkeypatch.setattr(vaporock, "VapoRockBackend", FakeVapoRockBackend)

    def try_backend(_backend_type: Any, config: dict[str, Any]) -> Any:
        configs["magemin"] = dict(config)
        return SimpleNamespace()

    monkeypatch.setattr(backends, "_try_backend", try_backend)

    def resolve_backend(name: str, _policy: Any, *, backend_config: dict[str, Any]):
        configs[name] = dict(backend_config)
        return SimpleNamespace()

    monkeypatch.setattr(backends, "resolve_backend", resolve_backend)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    for engine in ("vaporock", "magemin", "alphamelts", "thermoengine"):
        _open_resolved_backend(engine, {})

    assert set(configs) == {"vaporock", "magemin", "alphamelts", "thermoengine"}
    assert all(
        config["warm_worker"] is False
        for config in configs.values()
        if "warm_worker" in config
    )
    assert "warm_worker" not in configs["thermoengine"]


def test_cache_settings_exclude_warm_pool_runtime_state() -> None:
    from simulator.engine_cache import engine_cache_backend_settings

    backend = SimpleNamespace(
        _config={"temperature_units": "C", "warm_worker": True},
        _warm_pool=SimpleNamespace(call_count=0),
        _warm_worker_enabled=True,
        _warm_pool_size=1,
        _startup_failure_disposition=None,
        _last_pressure_authority_warning=None,
        _last_empty_speciation_cause=None,
        _last_empty_speciation_stats=None,
        _last_empty_speciation_reason=None,
        _last_empty_speciation_error=None,
        _last_silent_zero_notes=[],
    )
    first = engine_cache_backend_settings(backend)
    backend._warm_pool.call_count = 1
    backend._startup_failure_disposition = "worker_died"
    backend._last_pressure_authority_warning = "diagnostic changed"
    backend._last_empty_speciation_cause = "nonfinite_pressure"
    backend._last_empty_speciation_stats = {"calls": 1}
    backend._last_empty_speciation_reason = "diagnostic changed"
    backend._last_empty_speciation_error = {"detail": "diagnostic changed"}
    backend._last_silent_zero_notes = [{"code": "diagnostic_changed"}]
    second = engine_cache_backend_settings(backend)

    assert first == second
    assert first["_config"] == {"temperature_units": "C"}
    assert first["_warm_pool"] == {}


@pytest.mark.parametrize(
    "engine",
    tuple(dict.fromkeys(case.engine for case in _setting_cases())),
    ids=lambda engine: engine.value,
)
def test_opened_backend_settings_exclude_runtime_identity_strings(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import re
    import socket

    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    case = {case.engine: case for case in _setting_cases()}[engine]
    backend = case.backend_factory()
    digest = "a" * 64
    identity = (
        f"runtime sha256:{digest} commit:{digest} path=/Users/engine/runtime "
        f"host={socket.gethostname()}"
    )
    backend.engine_version = identity
    backend.runtime_identity = {
        "sha256": digest,
        "commit": digest,
        "absolute_path": "/Users/engine/runtime",
        "hostname": socket.gethostname(),
    }
    backend.runtime_worker = SimpleNamespace(
        ready_payload={"engine_version": identity, "identity": identity}
    )
    if engine is Engine.THERMOENGINE:
        backend._thermoengine_transport.engine_version = identity
        backend._thermoengine_transport._worker = SimpleNamespace(
            ready_payload={"engine_version": identity, "identity": identity}
        )

    monkeypatch.setattr(
        battery, "_open_resolved_backend", lambda _name, _settings=None: backend
    )
    handle = open_battery_engine(engine.value)
    try:
        settings = engine_cache.engine_cache_backend_settings(handle.backend)
        serialized = json.dumps(settings, sort_keys=True)
    finally:
        close_battery_engine_handle(handle)

    assert "sha256" not in serialized.lower()
    assert "commit" not in serialized.lower()
    assert "/Users/" not in serialized
    assert socket.gethostname() not in serialized
    assert re.search(
        r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])", serialized
    ) is None


def test_magemin_bridge_selection_is_a_cache_setting() -> None:
    from simulator.engine_cache import engine_cache_backend_settings, engine_cache_key

    case = next(case for case in _setting_cases() if case.label == "MAGEMin bridge selection")
    backend = case.backend_factory()
    first = engine_cache_backend_settings(backend)
    backend._bridge = "julia"
    second = engine_cache_backend_settings(backend)

    assert first["_bridge"] == "subprocess"
    assert second["_bridge"] == "julia"
    assert first != second
    assert engine_cache_key("magemin", 1, first) != engine_cache_key("magemin", 1, second)


def test_openimcc_cache_settings_use_effective_loaded_pack() -> None:
    from simulator.engine_cache import engine_cache_backend_settings, engine_cache_key
    from simulator.diagnostic_helpers.binary_pot_battery import _OpenImccBatteryBackend

    backend = object.__new__(_OpenImccBatteryBackend)
    backend._pack_name = "default"
    backend._gas_pack_name = "default"
    backend._identity = {"pack": "published-core"}

    first = engine_cache_backend_settings(backend)
    backend._identity["pack"] = "published-ext"
    second = engine_cache_backend_settings(backend)
    backend._gas_pack_name = "published-gas-ext"
    third = engine_cache_backend_settings(backend)

    assert first["_effective_pack_name"] == "published-core"
    assert second["_effective_pack_name"] == "published-ext"
    assert first["_gas_pack_name"] == "default"
    assert third["_gas_pack_name"] == "published-gas-ext"
    assert "_pack_name" not in first
    assert first != second
    assert second != third
    assert engine_cache_key("openimcc", 1, first) != engine_cache_key("openimcc", 1, second)


def test_openimcc_gas_cache_key_tracks_pack_selection_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.melt_backend.openimcc_bridge import evaluate_gas

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "gas-cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    calls = 0

    def fake_evaluate_gas(
        _activities: dict[str, float],
        _temperature_K: float,
        _pressure_bar: float,
        gas_pack: Any,
        **_kwargs: Any,
    ) -> dict[str, float]:
        nonlocal calls
        calls += 1
        return {"CO(g)": {"gas-a.csv": 1.0, "gas-b.csv": 2.0}[gas_pack.gas_path.name]}

    monkeypatch.setitem(
        sys.modules,
        "openimcc",
        SimpleNamespace(evaluate_gas=fake_evaluate_gas),
    )
    common = ({"SiO2": 0.5}, 1700.0, 1.0e-6)
    oxide_path = Path("condensate.csv")
    first_pack = SimpleNamespace(gas_path=Path("gas-a.csv"), oxide_path=oxide_path)
    second_pack = SimpleNamespace(gas_path=Path("gas-b.csv"), oxide_path=oxide_path)

    first = evaluate_gas(*common, first_pack, gas_pack_selection="published-core")
    same_selection = evaluate_gas(
        *common, second_pack, gas_pack_selection="published-core"
    )
    other_selection = evaluate_gas(
        *common, second_pack, gas_pack_selection="published-ext"
    )

    assert first == same_selection
    assert first["CO(g)"] != other_selection["CO(g)"]
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 1,
        "misses": 2,
        "stores": 2,
        "store_error": 0,
        "read_error": 0,
    }


def test_runtime_identity_is_captured_once_when_each_engine_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    from simulator import engine_cache
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    calls: Counter[str] = Counter()

    def compute(engine: str) -> str:
        calls[engine] += 1
        return f"identity:{engine}"

    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", compute)
    monkeypatch.setattr(
        battery,
        "_open_resolved_backend",
        lambda _name, _settings=None: SimpleNamespace(
            equilibrate=lambda **_kwargs: None,
            supports_intrinsic_fO2=False,
        ),
    )
    engines = tuple(engine_cache.ENGINE_CHEMISTRY_VERSION)
    for engine in engines:
        for _ in range(3):
            battery._open_battery_engine_with_settings(engine)

    assert calls == Counter(dict.fromkeys(engines, 1))


def test_internal_analytical_residue_cache_round_trips_named_observation_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.battery.score import load_score_context, temperature_of
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    source_id = "abdeyazdan-2026-na-sn-si-phase-thermodynamics"
    observation_id = (
        source_id
        + "::abdeyazdan_2026_t1_run04_NaSi11_glass_nao0p5"
    )
    context = load_score_context(Path(__file__).resolve().parents[2], sources=(source_id,))
    observation = context.observations[observation_id]
    temperature_K = temperature_of(observation.identity)
    assert temperature_K is not None and observation.value.point is not None
    temperature_C = float(temperature_K) - 273.15
    nao_half_fraction = float(observation.value.point)
    composition_mol = {
        "NaO0.5": nao_half_fraction,
        "SiO2": 1.0 - nao_half_fraction,
    }

    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    engine_cache.engine_cache_counters(reset=True)
    calls: list[dict[str, Any]] = []

    def fake_residue_pressure(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            vapor_pressures_Pa={"Na(g)": 1.25},
            diagnostics=SimpleNamespace(unconsumed="not part of residue scoring"),
        )

    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", fake_residue_pressure
    )
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    request = {
        "core": object(),
        "temperature_C": temperature_C,
        "pressure_bar": 1.0,
        "composition_kg": None,
        "composition_mol": composition_mol,
        "fO2_log": -8.0,
        "po2_request": Po2Request(PO2_COMMANDED, 10.0**-8.0),
        "include_diagnostic_shadows": False,
    }
    uncached = internal_analytical_residue_vapor_pressure(**request)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    cold = internal_analytical_residue_vapor_pressure(**request)
    warm = internal_analytical_residue_vapor_pressure(**request)

    assert uncached == cold == warm
    assert cold.vapor_pressures_Pa == {"Na(g)": 1.25}
    assert cold.diagnostics.unconsumed == "not part of residue scoring"
    assert len(calls) == 2
    assert calls[0]["temperature_C"] == temperature_C
    assert calls[0]["composition_mol"] == composition_mol
    assert calls[1]["composition_mol"] == composition_mol
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
    }


def test_internal_analytical_residue_keys_previous_high_t_activity_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setattr(engine_cache, "record_engine_open", lambda *_args: None)
    engine_cache.engine_cache_counters(reset=True)
    core = SimpleNamespace(
        _high_t_melt_activity="openimcc",
        _last_high_t_melt_activity={},
        _last_high_t_melt_activity_temperature_K=None,
    )
    calls = 0

    def solve(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return SimpleNamespace(vapor_pressures_Pa={"SiO(g)": 1.0})

    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter", solve)
    request = {
        "temperature_C": 1726.85,
        "pressure_bar": 0.0,
        "composition_kg": None,
        "composition_mol": {"SiO2": 0.44, "FeO": 0.08, "MgO": 0.36, "CaO": 0.12},
        "fO2_log": -10.0,
        "po2_request": Po2Request(PO2_COMMANDED, 1e-10),
        "include_diagnostic_shadows": False,
    }

    first = internal_analytical_residue_vapor_pressure(core=core, **request)
    core._last_high_t_melt_activity = {
        "temperature_K": 1950.0,
        "certified_effective_gamma_by_parent": {
            "SiO2": {
                "gamma_last": 1.25,
                "temperature_K": 1950.0,
                "composition_single_cation": {"SiO2": 1.0},
            }
        },
    }
    core._last_high_t_melt_activity_temperature_K = 1950.0
    second = internal_analytical_residue_vapor_pressure(core=core, **request)

    assert first.vapor_pressures_Pa == second.vapor_pressures_Pa == {"SiO(g)": 1.0}
    assert calls == 2
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"] == {
        "hits": 0,
        "misses": 2,
        "stores": 2,
        "store_error": 0,
        "read_error": 0,
    }


def test_internal_analytical_high_temperature_mode_is_keyed_and_cache_equivalent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )
    from tests.battery.cache_test_support import open_available_engine

    cache_path = tmp_path / "internal-analytical-high-temperature.sqlite"
    composition_mol = {
        "SiO2": 0.44,
        "Al2O3": 0.05,
        "FeO": 0.08,
        "MgO": 0.36,
        "CaO": 0.07,
    }
    request = {
        "temperature_C": 1726.85,
        "pressure_bar": 0.0,
        "composition_kg": None,
        "composition_mol": composition_mol,
        "fO2_log": -10.0,
        "po2_request": Po2Request(PO2_COMMANDED, 1e-10),
        "include_diagnostic_shadows": False,
    }
    key_request = {
        "call": "residue_vapor_pressure",
        **request,
        "po2_request": request["po2_request"].as_payload(),
        "high_t_melt_activity_previous": {},
        "high_t_melt_activity_previous_temperature_K": None,
    }

    def evaluate(mode: str, *, cache: bool) -> Any:
        handle = open_available_engine(
            Engine.INTERNAL_ANALYTICAL,
            nodeid="internal analytical high-temperature cache invariant",
        )
        try:
            core = handle.backend._core
            core._high_t_melt_activity = mode
            if cache:
                monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(cache_path))
            else:
                monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
            return internal_analytical_residue_vapor_pressure(core=core, **request)
        finally:
            close_battery_engine_handle(handle)

    openimcc_backend = open_available_engine(
        Engine.INTERNAL_ANALYTICAL,
        nodeid="internal analytical high-temperature key invariant",
    )
    try:
        openimcc_backend.backend._core._high_t_melt_activity = "openimcc"
        openimcc_settings = engine_cache.engine_cache_backend_settings(
            openimcc_backend.backend._core
        )
        openimcc_key = engine_cache.engine_cache_key(
            "internal-analytical",
            engine_cache.ENGINE_CHEMISTRY_VERSION["internal-analytical"],
            {**key_request, "engine_settings": openimcc_settings},
        )
        different_composition_request = {
            **key_request,
            "composition_mol": {
                **composition_mol,
                "SiO2": 0.439,
                "FeO": 0.081,
            },
        }
        different_composition_key = engine_cache.engine_cache_key(
            "internal-analytical",
            engine_cache.ENGINE_CHEMISTRY_VERSION["internal-analytical"],
            {**different_composition_request, "engine_settings": openimcc_settings},
        )
        openimcc_backend.backend._core._high_t_melt_activity = "constant_gamma"
        constant_gamma_settings = engine_cache.engine_cache_backend_settings(
            openimcc_backend.backend._core
        )
        constant_gamma_key = engine_cache.engine_cache_key(
            "internal-analytical",
            engine_cache.ENGINE_CHEMISTRY_VERSION["internal-analytical"],
            {**key_request, "engine_settings": constant_gamma_settings},
        )
    finally:
        close_battery_engine_handle(openimcc_backend)

    assert openimcc_key != constant_gamma_key
    assert openimcc_key != different_composition_key
    engine_cache.engine_cache_counters(reset=True)
    openimcc_off = evaluate("openimcc", cache=False)
    openimcc_cold = evaluate("openimcc", cache=True)
    openimcc_warm = evaluate("openimcc", cache=True)
    constant_gamma_off = evaluate("constant_gamma", cache=False)
    constant_gamma_cold = evaluate("constant_gamma", cache=True)
    constant_gamma_warm = evaluate("constant_gamma", cache=True)

    assert openimcc_cold == openimcc_off
    assert openimcc_warm == openimcc_off
    assert constant_gamma_cold == constant_gamma_off
    assert constant_gamma_warm == constant_gamma_off
    assert openimcc_off.vapor_pressures_Pa != constant_gamma_off.vapor_pressures_Pa
    assert engine_cache.engine_cache_counters(reset=True)["internal-analytical"] == {
        "hits": 2,
        "misses": 2,
        "stores": 2,
        "store_error": 0,
        "read_error": 0,
    }


def test_hashimoto_internal_residue_path_reads_diagnostics_from_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_cache
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    calls = 0

    def fake_residue_pressure(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            vapor_pressures_Pa={"Na": 5.0},
            diagnostics={
                "vapor_pressure_diagnostic": {
                    "pO2_floor_inversion_notices_by_species": {
                        "Na": {"reason": "recorded floor inversion"}
                    }
                }
            },
        )

    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(
        battery, "_internal_analytical_vapor_pressure_adapter", fake_residue_pressure
    )
    floor_diagnostics: dict[str, dict[str, object]] = {}
    pressure = residue._make_residue_pressure_model(
        "internal-analytical",
        (("Na", (None, 1.0, -0.5)),),
        None,
        temperature_K=1800.0,
        total_pressure_Pa=100_000.0,
        engine_metadata={},
        engine_flags=set(),
        missing_channels_reason="residue_pressure_channels_missing",
        caller="Hashimoto cache diagnostics",
        floor_inversion_diagnostics=floor_diagnostics,
    )

    first = pressure({"Na2O": 1.0}, -8.0)
    second = pressure({"Na2O": 1.0}, -8.0)
    pressure._on_accepted_evaluation(-8.0)

    assert first == second == {"Na": 5.0}
    assert floor_diagnostics["Na"]["detail"] == {
        "reason": "recorded floor inversion"
    }
    assert calls == 1
