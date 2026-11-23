"""OpenIMCC formation algebra, source relation, and coverage tally.

Expected energies are the signed sum of the stubbed species_thermo row.
Pressure, per-basis rescale, and log10 Kf come from the identity owners.
Apparent G and apparent H are not the scored values. A refusal value is None.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

from simulator.battery.enums import (
    Engine,
    Fo2Mode,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.identity import (
    PA_PER_BAR_DEC,
    atm_to_pa,
    log10K_from_delta_fG_kJ_mol,
    rescale_energy_per_basis,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.records import (
    Reaction,
    ReactionTerm,
    Species,
    State,
    as_decimal,
    phase_token,
)
from tests.battery.factories import (
    formation_reaction,
    o2_identity,
    observation,
    oxide_identity,
)


def _bridged(value: float) -> Decimal:
    return as_decimal(str(value))


def _coeff(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


class _Row:
    def __init__(
        self,
        *,
        H: float,
        G: float,
        Cp: float,
        S: float,
        table: str,
        row: str,
    ) -> None:
        self.H = H
        self.G = G
        self.Cp = Cp
        self.S = S
        self.table = table
        self.row = row


def _install_thermo(monkeypatch, rows: dict[str, _Row], basis):
    calls: list[tuple[str, str, float]] = []

    def species_thermo(formula, phase, temperature):
        calls.append((formula, phase, float(temperature)))
        row = rows[formula]
        return {
            "H_app_kJ_mol": row.H,
            "G_J_mol": row.G,
            "Cp_J_molK": row.Cp,
            "S_J_molK": row.S,
            "source_table_id": row.table,
            "source_row_id": row.row,
            "T_min": 200.0,
            "T_max": 6000.0,
            "derivatives_fit_implied": False,
        }

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo",
        species_thermo,
    )
    monkeypatch.setattr(
        "simulator.battery.generators.janaf._formation_basis_at_temperature",
        lambda formula, product, temperature: basis,
    )
    return calls


def _attempt(identity):
    from simulator.battery.compilation_tier import predict_thermo_attempt

    return predict_thermo_attempt(
        Engine.OPENIMCC,
        observation("obs-openimcc", "exp-openimcc", identity, Decimal("1")),
    )


def _mgo(quantity: Quantity, *, per: PerBasis, pressure: Decimal, T: Decimal):
    return replace(
        oxide_identity(
            "MgO",
            Phase.G,
            T_K=T,
            metal_formula="Mg",
            metal_phase=Phase.CR,
            nu_metal=Fraction(1),
            nu_o2=Fraction(1, 2),
            per=per,
            p_std=pressure,
        ),
        quantity=quantity,
    )


def _formation_basis(identity, *, convention=()):
    from simulator.battery.generators.janaf import _FormationBasis

    product = identity.species
    metal = Species("Mg", Phase.CR)
    oxygen = Species("O2", Phase.G)
    reaction = formation_reaction(product, metal, Fraction(1), Fraction(1, 2))
    return _FormationBasis(
        reaction=State.of(reaction),
        formation_elements=State.of((("Mg", metal), ("O", oxygen))),
        reference_locator_key=(
            ("Mg", "Mg-001", None, None),
            ("O", "O-029", None, None),
        ),
        locator_note="test elemental schedule",
        convention_tables=convention,
    )


def _formation_energies(reaction: Reaction, rows: dict[str, _Row], temperature: Decimal, pressure: Decimal):
    enthalpy = Decimal(0)
    gibbs_J = Decimal(0)
    delta_n = Fraction(0)
    for term in reaction.terms:
        row = rows[term.species.formula]
        enthalpy += _coeff(term.coefficient) * _bridged(row.H)
        gibbs_J += _coeff(term.coefficient) * _bridged(row.G)
        if phase_token(term.species) is Phase.G:
            delta_n += term.coefficient
    gibbs = gibbs_J / Decimal(1000)
    gibbs += standard_pressure_delta_g_kJ_per_mol(
        _coeff(delta_n),
        temperature,
        PA_PER_BAR_DEC,
        pressure,
    )
    return enthalpy, gibbs


_ROWS = {
    "MgO": _Row(H=100.0, G=50000.0, Cp=37.0, S=27.0, table="Fe-019", row="mgo"),
    "Mg": _Row(H=10.0, G=1000.0, Cp=25.0, S=32.0, table="Mg-001", row="mg"),
    "O2": _Row(H=4.0, G=2000.0, Cp=29.0, S=205.0, table="O-029", row="o2"),
}


def test_formation_sum_is_not_apparent_gibbs(monkeypatch) -> None:
    temperature = Decimal("1200")
    pressure = atm_to_pa("1")
    identity = _mgo(
        Quantity.DELTA_FG,
        per=PerBasis.MOL_O2,
        pressure=pressure,
        T=temperature,
    )
    basis = _formation_basis(identity, convention=(("O", "O-029"),))
    _install_thermo(monkeypatch, _ROWS, basis)
    enthalpy, gibbs = _formation_energies(
        basis.reaction.value, _ROWS, temperature, pressure
    )
    expected_h = rescale_energy_per_basis(
        enthalpy, PerBasis.MOL_SPECIES, PerBasis.MOL_O2, basis.reaction.value
    )
    expected_g = rescale_energy_per_basis(
        gibbs, PerBasis.MOL_SPECIES, PerBasis.MOL_O2, basis.reaction.value
    )
    expected_k = log10K_from_delta_fG_kJ_mol(expected_g, temperature)

    gibbs_attempt = _attempt(identity)
    enthalpy_attempt = _attempt(replace(identity, quantity=Quantity.DELTA_FH))
    k_attempt = _attempt(replace(identity, quantity=Quantity.LOG10_KF))

    assert gibbs_attempt.value == expected_g
    assert enthalpy_attempt.value == expected_h
    assert k_attempt.value == expected_k
    assert gibbs_attempt.value != _bridged(50000.0)
    assert gibbs_attempt.value != _bridged(50000.0) / Decimal(1000)
    assert enthalpy_attempt.value != _bridged(100.0)
    assert gibbs_attempt.openimcc_provenance[0] == "Fe-019"
    assert gibbs_attempt.notices[0].kind is NoticeKind.REFERENCE_PHASE_BY_CONVENTION
    assert "O-029" in gibbs_attempt.notices[0].origin
    assert k_attempt.refusal_reason is None


def test_reference_elements_at_298_cancel_to_the_product_row(monkeypatch) -> None:
    temperature = Decimal("298.15")
    pressure = PA_PER_BAR_DEC
    rows = {
        "MgO": _Row(H=12.5, G=3400.0, Cp=37.0, S=27.0, table="Fe-019", row="mgo"),
        "Mg": _Row(H=0.0, G=0.0, Cp=0.0, S=0.0, table="Mg-001", row="mg"),
        "O2": _Row(H=0.0, G=0.0, Cp=0.0, S=0.0, table="O-029", row="o2"),
    }
    identity = _mgo(
        Quantity.DELTA_FH,
        per=PerBasis.MOL_SPECIES,
        pressure=pressure,
        T=temperature,
    )
    basis = _formation_basis(identity)
    _install_thermo(monkeypatch, rows, basis)
    enthalpy, gibbs = _formation_energies(
        basis.reaction.value, rows, temperature, pressure
    )

    assert _attempt(identity).value == enthalpy == _bridged(12.5)
    assert _attempt(replace(identity, quantity=Quantity.DELTA_FG)).value == gibbs
    assert gibbs == _bridged(3400.0) / Decimal(1000)


def test_o2_against_its_own_reference_is_zero(monkeypatch) -> None:
    from simulator.battery.generators.janaf import _FormationBasis

    identity = replace(o2_identity(Decimal("298.15")), quantity=Quantity.DELTA_FG)
    oxygen = identity.species
    reaction = Reaction(
        (ReactionTerm(oxygen, Fraction(1)), ReactionTerm(oxygen, Fraction(-1)))
    )
    basis = _FormationBasis(
        reaction=State.of(reaction),
        formation_elements=State.of((("O", oxygen),)),
        reference_locator_key=(("O", "O-029", None, None),),
        locator_note="O2 is the reference",
    )
    rows = {
        "O2": _Row(H=10.0, G=100.0, Cp=29.0, S=205.0, table="O-029", row="o2"),
    }
    _install_thermo(monkeypatch, rows, basis)

    assert _attempt(identity).value == Decimal(0)
    assert _attempt(replace(identity, quantity=Quantity.DELTA_FH)).value == Decimal(0)


def test_unresolved_basis_and_crystal_do_not_emit_apparent_enthalpy(monkeypatch) -> None:
    from simulator.battery.generators.janaf import _FormationBasis

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("species_thermo called without a reference phase")

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo",
        fail_if_called,
    )
    basis = _FormationBasis(
        reaction=State.unknown("both phases apply"),
        formation_elements=State.unknown("both phases apply"),
        reference_locator_key=(("O", "missing", None, None),),
        locator_note="both phases apply",
    )
    monkeypatch.setattr(
        "simulator.battery.generators.janaf._formation_basis_at_temperature",
        lambda formula, product, temperature: basis,
    )
    identity = _mgo(
        Quantity.DELTA_FH,
        per=PerBasis.MOL_SPECIES,
        pressure=PA_PER_BAR_DEC,
        T=Decimal("298.15"),
    )
    attempt = _attempt(identity)
    assert attempt.value is None
    assert attempt.refusal_detail["reason"] == "openimcc-element-reference-phase-unresolved"

    atom = _attempt(replace(identity, per=State.of(PerBasis.MOL_ATOM)))
    assert atom.value is None
    assert atom.refusal_detail["reason"] == "openimcc-formation-per-basis-unsupported"

    crystal = replace(
        oxide_identity(
            "MgO",
            Phase.CR,
            T_K=Decimal("298.15"),
            per=PerBasis.MOL_SPECIES,
            metal_formula="Mg",
        ),
        quantity=Quantity.DELTA_FH,
    )
    crystal_attempt = _attempt(crystal)
    assert crystal_attempt.value is None
    assert crystal_attempt.refusal_detail["reason"] == "pure-phase-polymorph-unresolved"

    monkeypatch.setattr(
        "simulator.battery.generators.janaf._formation_basis_at_temperature",
        lambda formula, product, temperature: _formation_basis(identity),
    )
    missing_pressure = _attempt(
        replace(
            identity,
            quantity=Quantity.DELTA_FG,
            standard_pressure_Pa=State.unknown("pressure not printed"),
        )
    )
    assert missing_pressure.value is None
    assert missing_pressure.refusal_detail["reason"] == "openimcc-standard-pressure-unknown"


def test_missing_species_on_a_formation_term_is_typed(monkeypatch) -> None:
    identity = _mgo(
        Quantity.DELTA_FH,
        per=PerBasis.MOL_SPECIES,
        pressure=PA_PER_BAR_DEC,
        T=Decimal("1200"),
    )
    rows = {key: value for key, value in _ROWS.items() if key != "Mg"}
    _install_thermo(monkeypatch, rows, _formation_basis(identity))
    attempt = _attempt(identity)
    assert attempt.value is None
    assert attempt.value != 0
    assert attempt.refusal_reason is RefusalReason.UNSUPPORTED
    assert attempt.refusal_detail["reason"] == "openimcc_species_missing"


def test_openimcc_source_relation_is_per_row() -> None:
    from simulator.battery.compilation_tier import (
        _OPENIMCC_PROVENANCE_DEFAULTED,
        openimcc_thermo_relation_for_prediction,
        openimcc_thermo_source_relation,
    )

    exposed, flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/Fe.yaml",
        provenance=("Fe-019", "row-1", None),
    )
    held_out, held_flag = openimcc_thermo_source_relation(
        source_id="robie",
        origin="compilations-robie-hemingway/oxides.yaml",
        provenance=("Fe-019", "row-1", None),
    )
    lamoreaux, lam_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/SiO.yaml",
        provenance=("LH87-SiO", "row-2", None),
    )
    missing, missing_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/Fe.yaml",
        provenance=None,
    )
    assert exposed is SourceRelation.EXPOSED and flag is None
    assert held_out is SourceRelation.HELD_OUT and held_flag is None
    assert lamoreaux is SourceRelation.HELD_OUT and lam_flag is None
    assert missing is SourceRelation.EXPOSED
    assert missing_flag == _OPENIMCC_PROVENANCE_DEFAULTED

    kept, kept_flag = openimcc_thermo_relation_for_prediction(
        Engine.OPENIMCC,
        Quantity.CP,
        refusal_reason="diagnostic_population",
        provenance=None,
        source_id="janaf-4th",
        origin="compilations-janaf/Fe.yaml",
        lineage_relation=SourceRelation.INDEPENDENT,
    )
    nasa, nasa_flag = openimcc_thermo_relation_for_prediction(
        Engine.NASA_CEA_9,
        Quantity.CP,
        refusal_reason=None,
        provenance=("Fe-019", "row-1", None),
        source_id="janaf-4th",
        origin="compilations-janaf/Fe.yaml",
        lineage_relation=SourceRelation.INDEPENDENT,
    )
    assert kept is SourceRelation.INDEPENDENT and kept_flag is None
    assert nasa is SourceRelation.INDEPENDENT and nasa_flag is None


def test_tier_buckets_count_exposed_with_same_source_and_held_out_as_unseen() -> None:
    from simulator.battery.compilation_tier import (
        _TierAggregate,
        _tier_cell_from_payload_fields,
    )
    from simulator.battery.score import residual_key

    def cell(relation: str, status: str):
        return _tier_cell_from_payload_fields(
            {
                "reference": "obs-1",
                "status": status,
                "source_relation": relation,
                "candidate_request": {"engine": Engine.OPENIMCC.value},
                "numeric": {"value": "1.5", "unit": "kJ/mol"},
                "key": residual_key(
                    reference_id="obs-1",
                    quantity=Quantity.DELTA_FH,
                    engine=Engine.OPENIMCC,
                    rail=Rail.THERMOCHEMISTRY,
                    temperature_K=Decimal("298.15"),
                ),
            },
            family="janaf",
            quantity=Quantity.DELTA_FH.value,
            rail=Rail.THERMOCHEMISTRY.value,
            uncertainty="none",
            derive_eligible=True,
        )

    aggregate = _TierAggregate()
    aggregate.add(cell(SourceRelation.EXPOSED.value, ResidualStatus.MATCH.value), keep_values=True)
    aggregate.add(cell(SourceRelation.HELD_OUT.value, ResidualStatus.MISMATCH.value), keep_values=True)
    aggregate.add(cell(SourceRelation.SAME_INPUT.value, ResidualStatus.MATCH.value), keep_values=False)
    aggregate.add(cell(SourceRelation.INDEPENDENT.value, ResidualStatus.MATCH.value), keep_values=False)
    assert aggregate.same_source_count == 2
    assert aggregate.matched_same_source == 2
    assert aggregate.independent_count == 2
    assert aggregate.matched_independent == 1
    assert aggregate.numeric_count == 4


def test_coverage_tally_absent_is_a_missing_openimcc_residual() -> None:
    from simulator.battery.engine_capability import openimcc_coverage_tally
    from simulator.battery.score import _key_for_fo2_mode, residual_key

    def payload(engine, quantity, rail, reference="obs-1", *, refusal=None, request=True):
        key = residual_key(
            reference_id=reference,
            quantity=quantity,
            engine=engine,
            rail=rail,
            temperature_K=Decimal("1000"),
        )
        row = {
            "key": _key_for_fo2_mode(key, Fo2Mode.ENGINE_INTRINSIC),
            "status": "refused" if refusal else "match",
            "numeric": None if refusal else {"value": "1"},
        }
        if request:
            row["candidate_request"] = {"engine": engine.value}
        if refusal:
            row["refusal"] = {"reason": refusal}
        return row

    nasa = payload(Engine.NASA_CEA_9, Quantity.CP, Rail.THERMOCHEMISTRY, request=False)
    refused = payload(
        Engine.OPENIMCC,
        Quantity.CP,
        Rail.THERMOCHEMISTRY,
        refusal="openimcc_species_missing",
    )
    other = payload(
        Engine.NASA_CEA_9,
        Quantity.CP,
        Rail.THERMOCHEMISTRY,
        reference="obs-2",
        request=False,
    )
    fusion = payload(
        Engine.INTERNAL_ANALYTICAL,
        Quantity.ENTHALPY_OF_FUSION,
        Rail.THERMOCHEMISTRY,
    )
    covered = openimcc_coverage_tally([nasa, nasa, refused])
    missing = openimcc_coverage_tally([nasa, other])
    unlisted = openimcc_coverage_tally([fusion])

    assert covered["absent_listed"] == 0
    assert missing["absent_listed"] == 2
    assert unlisted["absent_listed"] == 0
    cp_rows = [
        row
        for row in covered["rows"]
        if row["quantity"] == Quantity.CP.value and row["rail"] == Rail.THERMOCHEMISTRY.value
    ]
    assert cp_rows[0]["typed_refused"] == 1
    assert cp_rows[0]["absent"] == 0
    assert cp_rows[0]["listed"] is True
