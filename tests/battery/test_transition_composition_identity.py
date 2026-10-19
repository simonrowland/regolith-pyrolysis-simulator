"""Pins for transition-temperature identity before composition is widened.

Liquidus and solidus rows already compare on composition. These migrated
identities are the golden: a later profile edit must leave them unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import AmountBasis, Phase
from tests.battery.test_migrate import _migrate_real_extract


def _migrated(tmp_path: Path, name: str):
    return _migrate_real_extract(tmp_path / name, name)


def _one(result, needle: str, *, value: Decimal | None = None):
    hits = [
        row
        for row in result.observations.values()
        if needle in row.observation_id
        and (value is None or row.value.point == value)
    ]
    assert len(hits) == 1, [row.observation_id for row in hits]
    return hits[0]


def test_liquidus_and_solidus_identities_stay_as_migrated(tmp_path: Path) -> None:
    folstad = _one(
        _migrated(tmp_path, "folstad-2024-metals-cao-dissolution-cas.yaml"),
        "folstad_2024_table3_liquidus_slag1",
        value=Decimal("1763.15"),
    )
    assert folstad.identity.subtype.value == "liquidus"
    assert folstad.identity.species.formula == "SiO2"
    assert folstad.identity.species.phase.value is Phase.L
    assert folstad.identity.species.polymorph.is_not_applicable
    assert folstad.identity.temperature_K.is_not_applicable
    assert folstad.identity.total_pressure_Pa.is_unknown
    assert (
        folstad.identity.total_pressure_Pa.reason
        == "source does not state a numeric total_pressure_Pa"
    )
    composition = folstad.identity.composition
    assert composition.is_value
    assert composition.value.basis == "printed_oxides"
    assert composition.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert composition.value.components == (
        ("SiO2", Decimal("0.6280796047125404092984913694")),
        ("CaO", Decimal("0.1802539376180307659580055488")),
        ("Al2O3", Decimal("0.1916664576694288247435030820")),
    )

    rys = _one(
        _migrated(tmp_path, "rys-2007-alkali-oxide-thermodynamics.yaml"),
        "rys_2007_table7_liquidus",
        value=Decimal("1353.15"),
    )
    assert rys.identity.subtype.value == "liquidus"
    assert rys.identity.species.formula == "SiO2"
    assert rys.identity.species.phase.value is Phase.L
    assert rys.identity.composition.is_value
    assert rys.identity.composition.value.basis == "printed_mole_fraction"
    assert rys.identity.composition.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert dict(rys.identity.composition.value.components) == {
        "Na2O": Decimal("0.9598"),
        "SiO2": Decimal("0.0401"),
    }

    gornerup = _one(
        _migrated(tmp_path, "gornerup-1996-cao-corner-liquidus.yaml"),
        "gornerup_1996_t1_exp_3_4",
        value=Decimal("1873.15"),
    )
    assert gornerup.identity.subtype.value == "liquidus"
    assert gornerup.identity.species.formula == "CaO"
    assert gornerup.identity.species.phase.value is Phase.L
    assert gornerup.identity.composition.is_value
    assert gornerup.identity.composition.value.basis == "printed_oxides"
    assert gornerup.identity.composition.value.components == (
        ("Al2O3", Decimal("0.2317116332227499727427319125")),
        ("CaO", Decimal("0.7137065787467177629303214258")),
        ("SiO2", Decimal("0.05458178803053226432694666159")),
    )

    solidus = _one(
        _migrated(tmp_path, "shornikov-2020-mgo-feo-melts.yaml"),
        "shornikov_2020_fig1_s2_solidus_marker_01",
    )
    assert solidus.identity.subtype.value == "solidus"
    assert solidus.identity.species.formula == "MgO-FeO_melt"
    assert solidus.identity.species.phase.is_unknown
    assert solidus.identity.composition.is_unknown
    assert solidus.identity.composition.reason == "no composition mapped from source"
    assert solidus.identity.temperature_K.is_not_applicable
    assert solidus.value.point == Decimal("2.7E+3")
    assert solidus.identity.total_pressure_Pa.is_unknown
    assert solidus.identity.total_pressure_Pa.reason == "not_reported_in_figure"
