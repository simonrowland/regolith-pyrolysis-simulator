"""IMCC-SF04 resolve_backend shadow-tier registration."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.backend_names import (
    IMCC_SF04_BACKEND_NAME,
    IMCC_SF04_EXT_BACKEND_NAME,
    canonical_backend_name,
)
from simulator.backends import (
    INELIGIBLE_ACTIVE_BACKENDS,
    REAL_MELT_BACKEND_NAMES,
    BackendSelectionPolicy,
    BackendUnavailableError,
    resolve_backend,
)
from simulator.engine_local_config import identity_for
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.melt_backend.imcc_sf04.backend import (
    ImccSf04Backend,
    ImccSf04ExtBackend,
)
from simulator.melt_backend.imcc_sf04.kernel import _PUBLISHED_DATAPACK_SHA256


_POTS_PATH = Path("data/binary_pots.yaml")
_FEO_MGO_SIO2_POT_ID = "feo_mgo_sio2_30_20_50"


class _FakeAlphaMELTS:
    name = "alphamelts"

    def __init__(self, *, available: bool):
        self._available = available
        self.init_calls: list[dict] = []

    def initialize(self, config):
        self.init_calls.append(dict(config or {}))
        return self._available

    def is_available(self) -> bool:
        return self._available

    def capabilities(self):
        return {
            "silicate_melt": True,
            "gas_volatiles": False,
            "salt_phase": False,
            "sulfide_matte": False,
            "metal_alloy": False,
        }


def _feo_mgo_sio2_wt_pct() -> dict[str, float]:
    payload = yaml.safe_load(_POTS_PATH.read_text())
    pots = payload["pots"]
    return dict(pots[_FEO_MGO_SIO2_POT_ID]["composition_wt_pct"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("imcc-sf04", IMCC_SF04_BACKEND_NAME),
        ("imcc_sf04", IMCC_SF04_BACKEND_NAME),
        ("IMCC-SF04", IMCC_SF04_BACKEND_NAME),
        (" IMCC_SF04 ", IMCC_SF04_BACKEND_NAME),
        ("imcc-sf04-ext", IMCC_SF04_EXT_BACKEND_NAME),
        ("imcc_sf04_ext", IMCC_SF04_EXT_BACKEND_NAME),
        ("IMCC-SF04-EXT", IMCC_SF04_EXT_BACKEND_NAME),
        (" IMCC_SF04_EXT ", IMCC_SF04_EXT_BACKEND_NAME),
    ],
)
def test_canonical_backend_name_accepts_imcc_hyphen_and_underscore(
    raw: str, expected: str
) -> None:
    assert canonical_backend_name(raw) == expected


def test_imcc_names_are_ineligible_active_not_real_melt_backends() -> None:
    assert IMCC_SF04_BACKEND_NAME in INELIGIBLE_ACTIVE_BACKENDS
    assert IMCC_SF04_EXT_BACKEND_NAME in INELIGIBLE_ACTIVE_BACKENDS
    assert "vaporock" in INELIGIBLE_ACTIVE_BACKENDS
    assert "magemin" in INELIGIBLE_ACTIVE_BACKENDS
    assert IMCC_SF04_BACKEND_NAME not in REAL_MELT_BACKEND_NAMES
    assert IMCC_SF04_EXT_BACKEND_NAME not in REAL_MELT_BACKEND_NAMES


def test_resolve_backend_imcc_sf04_returns_adapter_on_laptop() -> None:
    backend = resolve_backend(
        "imcc-sf04", BackendSelectionPolicy.RUNNER_STRICT
    )

    assert isinstance(backend, ImccSf04Backend)
    assert backend.is_available() is True
    assert backend.initialize({}) is True
    assert backend.backend_authoritative is False


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("imcc-sf04", ImccSf04Backend),
        ("imcc_sf04", ImccSf04Backend),
        ("imcc-sf04-ext", ImccSf04ExtBackend),
        ("imcc_sf04_ext", ImccSf04ExtBackend),
    ],
)
def test_resolve_backend_imcc_names_and_underscores(name: str, cls: type) -> None:
    backend = resolve_backend(name, BackendSelectionPolicy.RUNNER_STRICT)
    assert isinstance(backend, cls)
    assert backend.is_available() is True


@pytest.mark.parametrize("name", ["imcc-sf04", "imcc-sf04-ext", "imcc_sf04"])
def test_resolve_imcc_as_active_is_typed_ineligible_refusal(name: str) -> None:
    with pytest.raises(BackendUnavailableError, match="pending battery qualification") as excinfo:
        resolve_backend(name, BackendSelectionPolicy.WEB_AUTODETECT)

    message = str(excinfo.value)
    assert "not eligible as the active melt backend" in message
    assert getattr(excinfo.value, "reason_code", None) == "backend_unavailable"


def test_vaporock_active_refusal_message_is_unchanged() -> None:
    with pytest.raises(BackendUnavailableError) as excinfo:
        resolve_backend("vaporock", BackendSelectionPolicy.WEB_AUTODETECT)
    assert "CHEMISTRY-KERNEL-CARVE-OUT" in str(excinfo.value)
    assert "pending battery qualification" not in str(excinfo.value)


def test_default_web_autodetect_order_does_not_probe_imcc() -> None:
    calls: list[str] = []

    def make_alphamelts():
        calls.append("alphamelts")
        return _FakeAlphaMELTS(available=False)

    def make_internal_analytical():
        calls.append("internal-analytical")
        return InternalAnalyticalBackend()

    backend = resolve_backend(
        "auto",
        BackendSelectionPolicy.WEB_AUTODETECT,
        alphamelts_backend_cls=make_alphamelts,
        internal_analytical_backend_cls=make_internal_analytical,
        log_selection=lambda selected: None,
    )

    assert isinstance(backend, InternalAnalyticalBackend)
    assert calls == ["alphamelts", "internal-analytical"]


def test_runner_strict_auto_and_unknown_are_unchanged() -> None:
    with pytest.raises(BackendUnavailableError, match="auto backend selection"):
        resolve_backend("auto", BackendSelectionPolicy.RUNNER_STRICT)
    with pytest.raises(BackendUnavailableError, match="unknown backend 'factsage'"):
        resolve_backend("factsage", BackendSelectionPolicy.RUNNER_STRICT)


def test_one_pot_equilibrate_returns_finite_activities() -> None:
    backend = resolve_backend(
        "imcc-sf04", BackendSelectionPolicy.RUNNER_STRICT
    )
    composition_wt_pct = _feo_mgo_sio2_wt_pct()
    composition_kg = {
        oxide: float(wt) / 100.0 for oxide, wt in composition_wt_pct.items()
    }

    result = backend.equilibrate(
        temperature_C=1700.0 - 273.15,
        composition_kg=composition_kg,
    )

    assert result.status == "ok"
    assert result.activity_coefficients
    for oxide in ("FeO", "MgO", "SiO2"):
        value = result.activity_coefficients[oxide]
        assert math.isfinite(value)


def test_engine_identity_digest_is_datapack_hash_not_engines_local_toml() -> None:
    # identity_for reads compiled-engine receipts from engines.local.toml
    # (alphamelts / magemin / thermoengine binaries). IMCC-SF04 is in-tree
    # Python plus a hashed JSON datapack, so that table has no IMCC row.
    # The published digest lives on the kernel constant.
    assert identity_for("imcc-sf04") is None
    assert identity_for("imcc-sf04-ext") is None
    assert len(_PUBLISHED_DATAPACK_SHA256) == 64
    int(_PUBLISHED_DATAPACK_SHA256, 16)
