"""Melt thermodynamic backend abstraction layer."""

from simulator.melt_backend.base import (
    BACKEND_CAPABILITY_KEYS,
    DEFAULT_BACKEND_CAPABILITIES,
    EquilibriumResult,
    MeltBackend,
    StubBackend,
    normalize_backend_capabilities,
)
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.melt_backend.sulfsat import SulfSatGate, SulfurSaturationResult
from simulator.melt_backend.vaporock import VapoRockBackend

__all__ = [
    'BACKEND_CAPABILITY_KEYS',
    'DEFAULT_BACKEND_CAPABILITIES',
    'EquilibriumResult',
    'MAGEMinBackend',
    'MeltBackend',
    'StubBackend',
    'SulfSatGate',
    'SulfurSaturationResult',
    'VapoRockBackend',
    'normalize_backend_capabilities',
]
