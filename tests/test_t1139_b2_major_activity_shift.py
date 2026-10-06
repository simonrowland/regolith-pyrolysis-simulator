"""Declared golden shift: trace cations stay in the one melt projector.

``83f9b8ccb`` added the trace-parent oxides to
``MELT_OXIDE_CATIONS_PER_FORMULA``. The projector already skipped a key
that was not in that table, so the earlier major fractions are this same
function on the cleaned-melt account with those keys removed. Counting
the trace cations stays. It is one projector, not a fork.

The relative move is the same for every major oxide. It is a golden
shift of Build B, recorded here from the live ledger and from
``_internal_analytical_equilibrium`` at 1400 C on a 1000 kg batch.
Fe vapour does not move: that species reads the calphad ferrous
activity, not ``gamma * X``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import simulator.equilibrium as equilibrium
from simulator.chemistry.melt_activity import single_cation_mole_fractions
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.trace_oxide_parents import LIQUID_PARENT_OXIDE
from simulator.yaml_cache import load_cached_safe_yaml

_ROOT_DATA = Path(__file__).resolve().parents[1] / "data"
_TRACE_PARENTS = frozenset(LIQUID_PARENT_OXIDE.values())
_TEMPERATURE_C = 1400.0
_BATCH_KG = 1000.0
_FEEDSTOCKS = (
    "lunar_mare_low_ti",
    "lunar_mare_high_ti",
    "lunar_highland",
    "lunar_pkt_kreep_average",
    "lunar_spa_kreep_influenced",
    "targeted_super_kreep_ore",
)

# SiO2 single-cation fraction, before then after counting trace parents.
# Recorded from project_account_mol("process.cleaned_melt") through
# single_cation_mole_fractions.
_SIO2_X_HEX = {
    "lunar_mare_low_ti": (
        "0x1.bedb896f5b5ebp-2",
        "0x1.bebedb255656dp-2",
    ),
    "lunar_mare_high_ti": (
        "0x1.aa68ebcc311ffp-2",
        "0x1.aa4a1665bfd0cp-2",
    ),
    "lunar_highland": (
        "0x1.ac53cca5ef46cp-2",
        "0x1.ac46dd6c3ac92p-2",
    ),
    "lunar_pkt_kreep_average": (
        "0x1.e4b82f3bf33fcp-2",
        "0x1.e498a082f7071p-2",
    ),
    "lunar_spa_kreep_influenced": (
        "0x1.c3bfb660e6077p-2",
        "0x1.c3a89fa182e86p-2",
    ),
    "targeted_super_kreep_ore": (
        "0x1.d579a6fa4106ap-2",
        "0x1.d5427d38fc310p-2",
    ),
}

# Live vapour pressures at 1400 C, before then after. SiO and Na follow
# the major-X shift. Fe is the calphad path and is unchanged.
_VAPOUR_HEX = {
    "lunar_mare_low_ti": {
        "SiO": ("0x1.a13978be30600p-9", "0x1.a11eb15a56e26p-9"),
        "Na": ("0x1.58e44e06b7b2bp+1", "0x1.58ce2b214bcd8p+1"),
        "Fe": ("0x1.d58daf90a64aep-5", "0x1.d58daf90a64aep-5"),
    },
    "lunar_mare_high_ti": {
        "SiO": ("0x1.8e21fc6aa29fcp-9", "0x1.8e05327648b07p-9"),
        "Na": ("0x1.491c25061d86cp+1", "0x1.490458c57adf3p+1"),
        "Fe": ("0x1.be21c242aa484p-5", "0x1.be21c242aa484p-5"),
    },
    "lunar_highland": {
        "SiO": ("0x1.8fec4fe27dbd0p-9", "0x1.8fe03c3db6a37p-9"),
        "Na": ("0x1.98a555b0b9d85p+1", "0x1.9898fe9d5a6b3p+1"),
        "Fe": ("0x1.20647af712fbdp-6", "0x1.20647af712fbdp-6"),
    },
    "lunar_pkt_kreep_average": {
        "SiO": ("0x1.c4935b93425b8p-9", "0x1.c475e4967c889p-9"),
        "Na": ("0x1.2074f7104a209p+2", "0x1.20622f6901aaap+2"),
        "Fe": ("0x1.157fad10b71c9p-5", "0x1.157fad10b71c9p-5"),
    },
    "lunar_spa_kreep_influenced": {
        "SiO": ("0x1.a5ca9c2993727p-9", "0x1.a5b50d61d5533p-9"),
        "Na": ("0x1.dfddcf906dd5ap+1", "0x1.dfc548ec1502cp+1"),
        "Fe": ("0x1.76a9c885bef9cp-5", "0x1.76a9c885bef9cp-5"),
    },
    "targeted_super_kreep_ore": {
        "SiO": ("0x1.b6579e8cdfeb9p-9", "0x1.b6241d4530f7cp-9"),
        "Na": ("0x1.7b2a56bb58871p+2", "0x1.7afdc97c1c7ebp+2"),
        "Fe": ("0x1.28b6e2c2f0d4fp-5", "0x1.28b6e2c2f0d4fp-5"),
    },
}


def _load_yaml(name: str):
    return load_cached_safe_yaml((_ROOT_DATA / name).read_text(encoding="utf-8"))


def _omit_trace_parents(account: dict[str, float]) -> dict[str, float]:
    return {
        name: mol
        for name, mol in account.items()
        if name not in _TRACE_PARENTS
    }


def _simulator() -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        _load_yaml("setpoints.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
        materials=_load_yaml("materials.yaml"),
    )


def test_trace_cation_count_is_the_declared_major_shift() -> None:
    sim = _simulator()
    original = equilibrium.single_cation_mole_fractions

    def earlier_projector(account):
        return original(_omit_trace_parents(dict(account)))

    try:
        for feedstock_id in _FEEDSTOCKS:
            sim.load_batch(feedstock_id, mass_kg=_BATCH_KG)
            sim.melt.temperature_C = _TEMPERATURE_C
            account = dict(
                sim.atom_ledger.project_account_mol("process.cleaned_melt")
                or {}
            )
            after = original(account)
            before = original(_omit_trace_parents(account))
            sio2_before, sio2_after = _SIO2_X_HEX[feedstock_id]
            assert before["SiO2"].hex() == sio2_before, feedstock_id
            assert after["SiO2"].hex() == sio2_after, feedstock_id
            denominator = before["SiO2"]
            relative = abs(after["SiO2"] - before["SiO2"]) / denominator
            for oxide, old in before.items():
                if oxide in _TRACE_PARENTS or old <= 0.0:
                    continue
                assert oxide in after, feedstock_id
                assert abs(after[oxide] - old) / old == pytest.approx(
                    relative, rel=1.0e-6
                ), (feedstock_id, oxide)
            equilibrium.single_cation_mole_fractions = original
            after_pressure = sim._internal_analytical_equilibrium()
            equilibrium.single_cation_mole_fractions = earlier_projector
            before_pressure = sim._internal_analytical_equilibrium()
            for species, (old_hex, new_hex) in _VAPOUR_HEX[feedstock_id].items():
                assert before_pressure.vapor_pressures_Pa[species].hex() == (
                    old_hex
                ), (feedstock_id, species)
                assert after_pressure.vapor_pressures_Pa[species].hex() == (
                    new_hex
                ), (feedstock_id, species)
    finally:
        equilibrium.single_cation_mole_fractions = original
