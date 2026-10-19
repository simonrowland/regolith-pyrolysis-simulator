"""Pins for transition-temperature identity before composition is widened.

Liquidus and solidus rows already compare on composition. These migrated
identities are the golden: a later profile edit must leave them unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import AmountBasis, IdentityEqualKind, Phase, Quantity
from simulator.battery.identity import identity_equal, profile_for
from simulator.battery.migrate import fill_identity
from simulator.battery.records import Composition, Species, State
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


def _mixture(*parts: tuple[str, str]) -> State:
    return State.of(
        Composition(
            "printed_mole_fraction",
            tuple((name, Decimal(amount)) for name, amount in parts),
            AmountBasis.MOLE_FRACTION,
        )
    )


def _transition(subtype: str, composition: State | None = None):
    known = {
        "subtype": State.of(subtype),
        "total_pressure_Pa": State.of(Decimal("101325")),
    }
    if composition is not None:
        known["composition"] = composition
    return fill_identity(
        Quantity.TRANSITION_TEMPERATURE,
        Species("SiO2", Phase.L),
        **known,
    )


def _fusion(composition: State | None = None, *, formula: str = "SiO2"):
    known = {
        "subtype": State.of("solid_to_liquid"),
        "temperature_K": State.of(Decimal("1462")),
    }
    if composition is not None:
        known["composition"] = composition
    return fill_identity(
        Quantity.ENTHALPY_OF_FUSION,
        Species(formula, Phase.L),
        **known,
    )


def test_different_mixture_transitions_at_the_same_temperature_do_not_compare_equal() -> None:
    soda = _mixture(("Na2O", "0.50"), ("SiO2", "0.50"))
    potash = _mixture(("K2O", "0.50"), ("SiO2", "0.50"))
    for subtype in ("eutectic", "peritectic", "polymorphic", "thermal_effect"):
        left = _transition(subtype, soda)
        right = _transition(subtype, potash)
        assert left.composition.is_value
        assert "composition" in profile_for(left).required
        assert identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH
        assert identity_equal(left, _transition(subtype, soda)).kind is IdentityEqualKind.EQUAL


def test_pure_phase_fusion_has_no_composition() -> None:
    bare = _fusion()
    single = _fusion(_mixture(("SiO2", "1")))
    assert bare.composition.is_not_applicable
    assert single.composition.is_not_applicable
    profile = profile_for(bare)
    assert "composition" not in profile.required
    assert "composition" in profile.permitted_not_applicable
    assert identity_equal(bare, single).kind is IdentityEqualKind.EQUAL


def test_mixture_fusion_enthalpies_compare_on_composition() -> None:
    cmas = _mixture(("CaO", "0.32"), ("SiO2", "0.68"))
    ree = _mixture(("CaO", "0.30"), ("SiO2", "0.65"), ("Y2O3", "0.05"))
    left = _fusion(cmas, formula="CaO")
    right = _fusion(ree, formula="CaO")
    assert "composition" in profile_for(left).required
    assert identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(left, _fusion(cmas, formula="CaO")).kind is IdentityEqualKind.EQUAL


def test_single_component_transition_keeps_composition_not_applicable() -> None:
    melting = _transition("melting_point")
    assert melting.composition.is_not_applicable
    assert identity_equal(melting, _transition("melting_point")).kind is IdentityEqualKind.EQUAL
    glass = _transition("glass")
    assert glass.composition.is_unknown
    assert glass.composition.reason == "no composition mapped from source"


def test_dropping_mixture_composition_from_the_profile_fails() -> None:
    """Mutation: composition must stay required for a printed mixture.

    fill_identity strips an axis the profile marks not_applicable. Dropping
    composition from the eutectic and heat-of-fusion branches makes the two
    mixtures above compare equal, and this requirement check fails.
    """

    eutectic = _transition(
        "eutectic", _mixture(("Na2O", "0.80"), ("SiO2", "0.20"))
    )
    fusion = _fusion(
        _mixture(("CaO", "0.32"), ("MgO", "0.11"), ("SiO2", "0.57")),
        formula="CaO",
    )
    assert "composition" in profile_for(eutectic).required
    assert "composition" in profile_for(fusion).required
    assert "composition" not in profile_for(_fusion()).required
