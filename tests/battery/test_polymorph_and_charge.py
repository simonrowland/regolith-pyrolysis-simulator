"""Canonical crystal-type enum + charge axis (ticket b-510)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import (
    IdentityEqualKind,
    Phase,
    Polymorph,
    PrintedQualifierKind,
    Quantity,
)
from simulator.battery.generators import janaf as janaf_generator
from simulator.battery.generators import usgs_b1544 as b1544_generator
from simulator.battery.identity import _species_equal, identity_equal, quantity_token
from simulator.battery.migrate import migrate_species_payload, to_plain
from simulator.battery.polymorph_dictionary import (
    CROSS_SOURCE_EQUIVALENCES,
    JANAF_CRYSTAL_DICTIONARY,
    JANAF_CRYSTAL_TABLE_IDS,
    POLYMORPH_ALIASES,
    canonicalize_janaf_transition,
    classify_printed_qualifier,
    coerce_polymorph_token,
    printed_qualifier_from_name,
    unrecognised_polymorph_spelling,
)
from simulator.battery.records import Reaction, ReactionTerm, Species, State
from simulator.reference_data.janaf import TABLES_DIR, load_table_document
from tests.battery.test_usgs_b1544_generator import _generation as _b1544
from tests.battery.test_usgs_b1544_generator import _load as _load_b1544


def _janaf(table_id: str):
    return janaf_generator.generate_table(load_table_document(TABLES_DIR / f"{table_id}.yaml"))


def _first_crystal_identity(generated, quantity: Quantity = Quantity.CP):
    for observation in generated.observations:
        if quantity_token(observation.identity) is not quantity:
            continue
        phase = observation.identity.species.phase
        if phase.is_value and phase.value is Phase.CR:
            return observation.identity
    raise AssertionError(f"no crystal {quantity.value} observation")


def _first_identity(generated, quantity: Quantity = Quantity.CP):
    for observation in generated.observations:
        if quantity_token(observation.identity) is quantity:
            return observation.identity
    raise AssertionError(f"no {quantity.value} observation")


def _at_T(ident, temperature: Decimal = Decimal("298.15")):
    """JANAF series store T as the coordinate, so identity.temperature_K is unknown."""

    return replace(ident, temperature_K=State.of(temperature))


def _quartz_alpha_identity():
    generated = _b1544("usgs-b1544-quartz")
    return next(
        observation.identity
        for observation in generated.observations
        if quantity_token(observation.identity) is Quantity.DELTA_FG
        and observation.identity.species.polymorph.is_value
        and observation.identity.species.polymorph.value is Polymorph.ALPHA
    )


def _crystal_species(
    formula: str,
    polymorph: Polymorph | None,
    *,
    phase: Phase = Phase.CR,
    charge: int = -1,
) -> Species:
    polymorph_state = (
        State.unknown("test polymorph unknown")
        if polymorph is None
        else State.of(polymorph)
    )
    return Species(formula, phase, polymorph_state, charge=charge)


def _identity_with_terms(
    identity,
    species_terms: tuple[tuple[Species, int], ...],
    *,
    formation_elements: tuple[tuple[str, Species], ...] | None = None,
):
    if formation_elements is None:
        formation_elements = tuple(
            (species.formula, species) for species, _ in species_terms
        )
    return replace(
        identity,
        reaction=State.of(
            Reaction(
                tuple(
                    ReactionTerm(species, coefficient)
                    for species, coefficient in species_terms
                )
            )
        ),
        formation_elements=State.of(formation_elements),
    )


def test_janaf_crystal_row_equals_itself() -> None:
    ident = _at_T(_first_crystal_identity(_janaf("Al-096")))
    outcome = identity_equal(ident, ident)
    assert outcome.kind is IdentityEqualKind.EQUAL
    assert ident.species.polymorph.is_value
    assert ident.species.polymorph.value is Polymorph.CORUNDUM


def test_janaf_same_species_and_polymorph_compare_equal() -> None:
    generated = _janaf("Al-096")
    cp = _at_T(_first_crystal_identity(generated, Quantity.CP))
    entropy = _first_crystal_identity(generated, Quantity.S)
    aligned = replace(cp, species=entropy.species)
    assert identity_equal(cp, aligned).kind is IdentityEqualKind.EQUAL


def test_formation_identity_terms_compare_as_multisets() -> None:
    identity = _quartz_alpha_identity()
    assert identity.reaction is not None and identity.reaction.is_value
    assert identity.formation_elements is not None and identity.formation_elements.is_value

    reaction = identity.reaction.value
    elements = identity.formation_elements.value
    reordered = replace(
        identity,
        reaction=State.of(Reaction(tuple(reversed(reaction.terms)))),
        formation_elements=State.of(tuple(reversed(elements))),
    )
    assert identity_equal(identity, reordered).kind is IdentityEqualKind.EQUAL

    phase_changed = replace(
        identity,
        reaction=State.of(
            Reaction(
                tuple(
                    replace(term, species=replace(term.species, phase=Phase.L))
                    if term.species.formula == "Si"
                    else term
                    for term in reaction.terms
                )
            )
        ),
        formation_elements=State.of(
            tuple(
                (element, replace(species, phase=Phase.L))
                if element == "Si"
                else (element, species)
                for element, species in elements
            )
        ),
    )
    assert identity_equal(identity, phase_changed).kind is IdentityEqualKind.IDENTITY_MISMATCH

    coefficient_changed = replace(
        identity,
        reaction=State.of(
            Reaction(
                tuple(
                    replace(term, coefficient=term.coefficient * 2)
                    if term.species.formula == "Si"
                    else term
                    for term in reaction.terms
                )
            )
        ),
    )
    assert identity_equal(identity, coefficient_changed).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_known_polymorph_mismatch_cannot_be_hidden_by_an_unknown_pairing() -> None:
    identity = _quartz_alpha_identity()
    ca_alpha = _crystal_species("Ca", Polymorph.ALPHA)
    ca_beta = _crystal_species("Ca", Polymorph.BETA)
    si_unknown = _crystal_species("Si", None)
    elements = identity.formation_elements.value
    left = _identity_with_terms(
        identity,
        ((ca_alpha, -1), (si_unknown, -1)),
        formation_elements=elements,
    )
    right = _identity_with_terms(
        identity,
        ((ca_beta, -1), (si_unknown, -1)),
        formation_elements=elements,
    )

    assert identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert identity_equal(right, left).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_unknown_pairing_is_order_insensitive_when_no_pair_mismatches() -> None:
    identity = _quartz_alpha_identity()
    ca_alpha = _crystal_species("Ca", Polymorph.ALPHA)
    si_unknown = _crystal_species("Si", None)
    elements = identity.formation_elements.value
    left = _identity_with_terms(
        identity,
        ((ca_alpha, -1), (si_unknown, -1)),
        formation_elements=elements,
    )
    aligned = _identity_with_terms(
        identity,
        ((ca_alpha, -1), (si_unknown, -1)),
        formation_elements=elements,
    )
    reordered = _identity_with_terms(
        identity,
        ((si_unknown, -1), (ca_alpha, -1)),
        formation_elements=elements,
    )

    aligned_outcome = identity_equal(left, aligned)
    reordered_outcome = identity_equal(left, reordered)
    assert aligned_outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert reordered_outcome == aligned_outcome


def test_formula_mismatch_precedes_unknown_polymorph() -> None:
    identity = _quartz_alpha_identity()
    ca_unknown = _crystal_species("Ca", None)
    si_unknown = _crystal_species("Si", None)
    assert (
        _species_equal(ca_unknown, si_unknown).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )
    assert _species_equal(
        ca_unknown,
        _crystal_species("Ca", None, phase=Phase.L),
    ).kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert _species_equal(
        ca_unknown,
        _crystal_species("Ca", None, charge=0),
    ).kind is IdentityEqualKind.IDENTITY_MISMATCH
    phase_unknown_alpha = Species(
        "Ca",
        State.unknown("test phase unknown"),
        State.of(Polymorph.ALPHA),
        charge=-1,
    )
    phase_unknown_beta = Species(
        "Ca",
        State.unknown("test phase unknown"),
        State.of(Polymorph.BETA),
        charge=-1,
    )
    assert (
        _species_equal(phase_unknown_alpha, phase_unknown_beta).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )

    # Keep the element axis equal so this assertion exercises the reaction pair.
    elements = identity.formation_elements.value
    left = _identity_with_terms(
        identity,
        ((ca_unknown, -1),),
        formation_elements=elements,
    )
    right = _identity_with_terms(
        identity,
        ((si_unknown, -1),),
        formation_elements=elements,
    )
    assert identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH

    # The product species is another non-list caller of _species_equal.
    left_product = replace(identity, species=ca_unknown)
    right_product = replace(identity, species=si_unknown)
    assert (
        identity_equal(left_product, right_product).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )


def test_reaction_terms_mismatch_on_coefficient_or_length() -> None:
    identity = _quartz_alpha_identity()
    ca_alpha = _crystal_species("Ca", Polymorph.ALPHA)
    si_unknown = _crystal_species("Si", None)
    single_left = _identity_with_terms(identity, ((ca_alpha, -1),))
    different_coefficient = _identity_with_terms(identity, ((ca_alpha, -2),))
    different_length = _identity_with_terms(
        identity,
        ((ca_alpha, -1), (si_unknown, -1)),
        formation_elements=single_left.formation_elements.value,
    )

    assert (
        identity_equal(single_left, different_coefficient).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )
    assert (
        identity_equal(single_left, different_length).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )


def test_janaf_different_polymorphs_of_one_formula_are_unequal() -> None:
    alpha = _at_T(_first_crystal_identity(_janaf("Al-096")))
    delta = _at_T(_first_crystal_identity(_janaf("Al-097")))
    assert alpha.species.formula == delta.species.formula == "Al2O3"
    outcome = identity_equal(alpha, delta)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in outcome.fields


def test_gas_ion_has_not_applicable_polymorph_and_charge_value() -> None:
    ident = _at_T(_first_identity(_janaf("Al-006")))
    species = ident.species
    assert species.formula == "Al"
    assert species.phase.value is Phase.G
    assert species.polymorph is not None
    assert species.polymorph.is_not_applicable
    assert "ionization state" in (species.polymorph.reason or "")
    assert species.charge is not None
    assert species.charge.is_value
    assert species.charge.value == 1
    assert identity_equal(ident, ident).kind is IdentityEqualKind.EQUAL


def test_no_qualifier_table_stays_unknown() -> None:
    ident = _first_crystal_identity(_janaf("Al-070"))
    assert ident.species.polymorph is not None
    assert ident.species.polymorph.is_unknown
    reason = ident.species.polymorph.reason or ""
    assert "index_entry.name" in reason
    assert "title_as_published" in reason
    assert ident.species.formula == "LiAlO2"


def test_janaf_alpha_al2o3_equals_b1544_corundum() -> None:
    janaf = _at_T(_first_crystal_identity(_janaf("Al-096")))
    b1544 = _first_crystal_identity(_b1544("usgs-b1544-corundum"), Quantity.S)
    assert janaf.species.polymorph.value is Polymorph.CORUNDUM
    assert b1544.species.polymorph.value is Polymorph.CORUNDUM
    compared = replace(janaf, species=b1544.species)
    assert identity_equal(janaf, compared).kind is IdentityEqualKind.EQUAL


def test_janaf_quartz_i_ii_equals_b1544_alpha_beta() -> None:
    """O-037 splits on I <--> II; B1544 prints Alpha/Beta quartz. Canonical is alpha/beta."""

    janaf = _janaf("O-037")
    b1544 = _b1544("usgs-b1544-quartz")
    janaf_forms = []
    for observation in janaf.observations:
        if quantity_token(observation.identity) is not Quantity.CP:
            continue
        poly = observation.identity.species.polymorph
        if poly is not None and poly.is_value:
            janaf_forms.append(poly.value)
    assert janaf_forms == [Polymorph.ALPHA, Polymorph.BETA]
    b1544_forms = []
    for observation in b1544.observations:
        if quantity_token(observation.identity) is not Quantity.S:
            continue
        poly = observation.identity.species.polymorph
        if poly is not None and poly.is_value and poly.value not in b1544_forms:
            b1544_forms.append(poly.value)
    assert Polymorph.ALPHA in b1544_forms
    assert Polymorph.BETA in b1544_forms
    janaf_alpha = _at_T(
        next(
            obs.identity
            for obs in janaf.observations
            if quantity_token(obs.identity) is Quantity.CP
            and obs.identity.species.polymorph.value is Polymorph.ALPHA
        )
    )
    b1544_alpha = next(
        obs.identity
        for obs in b1544.observations
        if quantity_token(obs.identity) is Quantity.S
        and obs.identity.species.polymorph.is_value
        and obs.identity.species.polymorph.value is Polymorph.ALPHA
    )
    compared = replace(janaf_alpha, species=b1544_alpha.species)
    assert identity_equal(janaf_alpha, compared).kind is IdentityEqualKind.EQUAL
    janaf_beta = _at_T(
        next(
            obs.identity
            for obs in janaf.observations
            if quantity_token(obs.identity) is Quantity.CP
            and obs.identity.species.polymorph.value is Polymorph.BETA
        )
    )
    b1544_beta = next(
        obs.identity
        for obs in b1544.observations
        if quantity_token(obs.identity) is Quantity.S
        and obs.identity.species.polymorph.is_value
        and obs.identity.species.polymorph.value is Polymorph.BETA
    )
    compared_beta = replace(janaf_beta, species=b1544_beta.species)
    assert identity_equal(janaf_beta, compared_beta).kind is IdentityEqualKind.EQUAL
    assert identity_equal(janaf_alpha, replace(janaf_alpha, species=b1544_beta.species)).kind is (
        IdentityEqualKind.IDENTITY_MISMATCH
    )


def test_janaf_al2sio5_equals_b1544_same_mineral() -> None:
    pairs = (
        ("Al-102", "usgs-b1544-andalusite", Polymorph.ANDALUSITE),
        ("Al-103", "usgs-b1544-kyanite", Polymorph.KYANITE),
        ("Al-104", "usgs-b1544-sillimanite", Polymorph.SILLIMANITE),
    )
    for janaf_id, b1544_id, token in pairs:
        janaf = _at_T(_first_crystal_identity(_janaf(janaf_id)))
        b1544 = _first_crystal_identity(_b1544(b1544_id), Quantity.S)
        assert janaf.species.formula == b1544.species.formula == "Al2SiO5"
        assert janaf.species.polymorph.value is token
        assert b1544.species.polymorph.value is token
        compared = replace(janaf, species=b1544.species)
        assert identity_equal(janaf, compared).kind is IdentityEqualKind.EQUAL


def test_dictionary_excludes_ion_isomer_and_pressure() -> None:
    assert classify_printed_qualifier("Ion") is PrintedQualifierKind.ION
    assert classify_printed_qualifier("Cis") is PrintedQualifierKind.ISOMER
    assert classify_printed_qualifier("Trans") is PrintedQualifierKind.ISOMER
    assert classify_printed_qualifier("1 Bar") is PrintedQualifierKind.PRESSURE
    assert classify_printed_qualifier("5000 Bar") is PrintedQualifierKind.PRESSURE
    assert classify_printed_qualifier("Alpha") is PrintedQualifierKind.CRYSTAL
    assert ("Al+", "Ion") not in JANAF_CRYSTAL_DICTIONARY
    assert ("Al", "Ion") not in JANAF_CRYSTAL_DICTIONARY
    assert ("FNNF", "Cis") not in JANAF_CRYSTAL_DICTIONARY
    assert ("H2O", "1 Bar") not in JANAF_CRYSTAL_DICTIONARY
    assert JANAF_CRYSTAL_DICTIONARY[("Al2O3", "Alpha")] is Polymorph.CORUNDUM
    assert JANAF_CRYSTAL_TABLE_IDS[("Al2O3", "Alpha")] == ("Al-096",)
    assert printed_qualifier_from_name("Barium Hydroxide, Alpha (Ba(OH)2)") == "Alpha"
    for formula, janaf_qual, b1544_name, token in CROSS_SOURCE_EQUIVALENCES:
        assert JANAF_CRYSTAL_DICTIONARY[(formula, janaf_qual)] is token
    assert canonicalize_janaf_transition("SiO2", Polymorph.I) is Polymorph.ALPHA
    assert canonicalize_janaf_transition("SiO2", Polymorph.II) is Polymorph.BETA
    assert canonicalize_janaf_transition("Na2SO4", Polymorph.I) is Polymorph.I


def test_dictionary_covers_every_census_crystal_and_not_qualifier_alone() -> None:
    assert len(JANAF_CRYSTAL_DICTIONARY) == 73
    table_count = sum(len(ids) for ids in JANAF_CRYSTAL_TABLE_IDS.values())
    assert table_count == 74
    alphas = [formula for formula, qual in JANAF_CRYSTAL_DICTIONARY if qual == "Alpha"]
    assert len(alphas) == 17
    assert "Al2O3" in alphas
    assert "Na3AlF6" in alphas
    assert JANAF_CRYSTAL_DICTIONARY[("Al2O3", "Alpha")] is not JANAF_CRYSTAL_DICTIONARY[
        ("Na3AlF6", "Alpha")
    ]


def test_periclase_is_a_closed_mgo_token() -> None:
    assert Polymorph.PERICLASE.value == "periclase"
    assert coerce_polymorph_token("periclase") is Polymorph.PERICLASE
    assert coerce_polymorph_token("PERICLASE") is Polymorph.PERICLASE
    assert coerce_polymorph_token("Periclase") is Polymorph.PERICLASE


def test_tridymite_is_a_closed_polymorph_token() -> None:
    assert Polymorph.TRIDYMITE.value == "tridymite"
    assert POLYMORPH_ALIASES["tridymite"] is Polymorph.TRIDYMITE
    assert coerce_polymorph_token("tridymite") is Polymorph.TRIDYMITE


def test_neutral_charge_is_zero_value_not_unknown() -> None:
    species = Species("O2", Phase.G)
    assert species.charge is not None
    assert species.charge.is_value
    assert species.charge.value == 0
    ion = Species("Al+", Phase.G)
    assert ion.formula == "Al"
    assert ion.charge.value == 1
    minus = Species("Al-", Phase.G)
    assert minus.formula == "Al"
    assert minus.charge.value == -1
    assert identity_equal(
        replace(_first_identity(_janaf("Al-001")), species=ion),
        replace(_first_identity(_janaf("Al-001")), species=minus),
    ).kind is IdentityEqualKind.IDENTITY_MISMATCH


def test_species_migration_is_byte_identical() -> None:
    payload = {
        "formula": "Al+",
        "phase": {"tag": "value", "value": "g"},
        "polymorph": {"tag": "not_applicable", "reason": "not crystal"},
    }
    once = to_plain(migrate_species_payload(payload))
    twice = to_plain(migrate_species_payload(once))  # type: ignore[arg-type]
    assert once == twice
    assert once["formula"] == "Al"
    assert once["charge"] == {"tag": "value", "value": 1}
    crystal = {
        "formula": "Al2SiO5",
        "phase": {"tag": "value", "value": "cr"},
        "polymorph": {"tag": "value", "value": "Kyanite"},
    }
    lifted = to_plain(migrate_species_payload(crystal))
    assert lifted["polymorph"]["value"] == Polymorph.KYANITE.value
    assert to_plain(migrate_species_payload(lifted)) == lifted  # type: ignore[arg-type]
    unknown = {
        "formula": "Fe2O3",
        "phase": {"tag": "value", "value": "cr"},
        "polymorph": {"tag": "value", "value": "not-a-real-form"},
    }
    migrated = migrate_species_payload(unknown)
    assert migrated.polymorph is not None
    assert migrated.polymorph.is_unknown
    assert "not-a-real-form" in (migrated.polymorph.reason or "")
    assert unrecognised_polymorph_spelling(migrated.polymorph.reason) == "not-a-real-form"
    assert to_plain(migrate_species_payload(to_plain(migrated))) == to_plain(migrated)  # type: ignore[arg-type]


def test_unrecognised_polymorph_degrades_rather_than_raises() -> None:
    species = Species("MgO", Phase.CR, polymorph=State.of("not-a-real-form"))
    assert species.polymorph is not None
    assert species.polymorph.is_unknown
    assert species.polymorph.value is None
    assert unrecognised_polymorph_spelling(species.polymorph.reason) == "not-a-real-form"


def test_known_polymorph_is_still_coerced_strictly() -> None:
    species = Species("Al2SiO5", Phase.CR, polymorph=State.of("Kyanite"))
    assert species.polymorph is not None
    assert species.polymorph.is_value
    assert species.polymorph.value is Polymorph.KYANITE
    periclase = Species("MgO", Phase.CR, polymorph=State.of("PERICLASE"))
    assert periclase.polymorph is not None
    assert periclase.polymorph.is_value
    assert periclase.polymorph.value is Polymorph.PERICLASE


def test_ion_and_pressure_tables_do_not_stamp_a_crystal_type() -> None:
    water = _first_identity(_janaf("H-065"), Quantity.CP)
    assert water.species.polymorph.is_not_applicable
    assert "pressure qualifier" in (water.species.polymorph.reason or "")
    cis = _first_identity(_janaf("F-079"), Quantity.CP)
    assert cis.species.formula == "FNNF"
    assert cis.species.polymorph.is_not_applicable
    assert "molecular isomer" in (cis.species.polymorph.reason or "")
    trans = _first_identity(_janaf("F-080"), Quantity.CP)
    assert trans.species.polymorph.is_not_applicable
    # Same formula/phase/charge; isomer is not a compared crystal axis.
    assert cis.species.formula == trans.species.formula


def test_b1544_loader_still_loads(record_id: str = "usgs-b1544-corundum") -> None:
    record = _load_b1544(record_id)
    generated = b1544_generator.generate_record(record)
    assert generated.observations
