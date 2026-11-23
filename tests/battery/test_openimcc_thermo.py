"""OpenIMCC formation algebra, source relation, and coverage tally.

Expected energies are the signed sum of the stubbed species_thermo row.
Pressure, per-basis rescale, and log10 Kf come from the identity owners.
Apparent G and apparent H are not the scored values. A refusal value is None.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from simulator.battery.enums import (
    Engine,
    EvidenceClass,
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
    Identity,
    PA_PER_BAR_DEC,
    atm_to_pa,
    log10K_from_delta_fG_kJ_mol,
    rescale_energy_per_basis,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.records import (
    Locator,
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

    def species_thermo(formula, phase, temperature, **_kwargs):
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

    calls = _install_thermo(monkeypatch, _ROWS, _formation_basis(identity))
    missing_pressure = _attempt(
        replace(
            identity,
            quantity=Quantity.DELTA_FG,
            standard_pressure_Pa=State.unknown("pressure not printed"),
        )
    )
    _enthalpy, gibbs = _formation_energies(
        _formation_basis(identity).reaction.value,
        _ROWS,
        Decimal("298.15"),
        PA_PER_BAR_DEC,
    )
    assert calls
    assert missing_pressure.value == gibbs
    assert missing_pressure.refusal_reason is None
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.reason.startswith("standard-pressure-assumed:")
        and "pressure_bar=1" in notice.reason
        and "term=omitted" in notice.reason
        and notice.band == "1 bar"
        for notice in missing_pressure.notices
    )


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
    tsiv_on_janaf, _tsiv_janaf_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/ZnO.yaml",
        provenance=("TSIV-1603", "TSIV-1603-LFS-CP-EXT-2250", None),
    )
    tsiv_on_ivtan, _tsiv_ivtan_flag = openimcc_thermo_source_relation(
        source_id="tsiv",
        origin="compilations-ivtan/ZnO.yaml",
        provenance=("TSIV-1603", "TSIV-1603-LFS-CP-EXT-2250", None),
    )
    p672_on_janaf, _p672_janaf_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/CdO.yaml",
        provenance=("P672-T102", "P672-T102", None),
    )
    p672_on_bulletin, _p672_flag = openimcc_thermo_source_relation(
        source_id="usbm-b672",
        origin="compilations-pankratz-b672/CdO.yaml",
        provenance=("P672-T102", "P672-T102", None),
    )
    mno_on_janaf, _mno_janaf_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/MnO.yaml",
        provenance=(
            "MnO-liquid-construction",
            "LFS[MnO-IVTAN-1435;Tf=2120]",
            None,
        ),
    )
    mno_on_ivtan, _mno_ivtan_flag = openimcc_thermo_source_relation(
        source_id="ivtan",
        origin="compilations-ivtan/MnO.yaml",
        provenance=(
            "MnO-liquid-construction",
            "LFS[MnO-IVTAN-1435;Tf=2120]",
            None,
        ),
    )
    other_pankratz, _pankratz_flag = openimcc_thermo_source_relation(
        source_id="b677",
        origin="compilations-pankratz-1984-usbm-b677/oxides.yaml",
        provenance=("B677-T1", "row", None),
    )
    missing, missing_flag = openimcc_thermo_source_relation(
        source_id="janaf-4th",
        origin="compilations-janaf/Fe.yaml",
        provenance=None,
    )
    assert exposed is SourceRelation.EXPOSED and flag is None
    assert held_out is SourceRelation.HELD_OUT and held_flag is None
    assert lamoreaux is SourceRelation.HELD_OUT and lam_flag is None
    assert tsiv_on_janaf is SourceRelation.HELD_OUT
    assert tsiv_on_ivtan is SourceRelation.EXPOSED
    assert p672_on_janaf is SourceRelation.HELD_OUT
    assert p672_on_bulletin is SourceRelation.EXPOSED
    assert mno_on_janaf is SourceRelation.HELD_OUT
    assert mno_on_ivtan is SourceRelation.EXPOSED
    assert other_pankratz is SourceRelation.HELD_OUT
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
    nasa_diagnostic, nasa_diagnostic_flag = openimcc_thermo_relation_for_prediction(
        Engine.NASA_CEA_9,
        Quantity.CP,
        refusal_reason="diagnostic_population",
        provenance=("Fe-019", "row-1", None),
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
    nasa_other, nasa_other_flag = openimcc_thermo_relation_for_prediction(
        Engine.NASA_CEA_9,
        Quantity.CP,
        refusal_reason=None,
        provenance=("Fe-019", "row-1", None),
        source_id="robie",
        origin="compilations-robie-hemingway/oxides.yaml",
        lineage_relation=SourceRelation.INDEPENDENT,
    )
    assert kept is SourceRelation.INDEPENDENT and kept_flag is None
    assert nasa_diagnostic is SourceRelation.INDEPENDENT and nasa_diagnostic_flag is None
    assert nasa is SourceRelation.EXPOSED and nasa_flag is None
    assert nasa_other is SourceRelation.INDEPENDENT and nasa_other_flag is None


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

    incomplete = _tier_cell_from_payload_fields(
        {
            "reference": "obs-incomplete",
            "status": ResidualStatus.MATCH.value,
            "source_relation": SourceRelation.HELD_OUT.value,
            "exclusions": ("source_relation_independent_complete_ancestry",),
            "candidate_request": {"engine": Engine.OPENIMCC.value},
            "numeric": {"value": "1.5", "unit": "kJ/mol"},
            "key": residual_key(
                reference_id="obs-incomplete",
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
    aggregate.add(incomplete, keep_values=True)
    assert incomplete.lineage_complete is False
    assert aggregate.same_source_count == 2
    assert aggregate.matched_same_source == 2
    assert aggregate.independent_count == 2
    assert aggregate.matched_independent == 1
    assert aggregate.numeric_count == 5


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


def _source_formula_observation(record_relative_path: str, source_id: str):
    root = Path(__file__).resolve().parents[2]
    path = root / record_relative_path
    document = json.loads(path.read_text(encoding="utf-8"))
    formula = str(document.get("formula_as_published") or document["formula"])
    identity = Identity(
        quantity=Quantity.CP,
        species=Species(formula, State.unknown("source does not state phase")),
        per=State.of(PerBasis.MOL_SPECIES),
        temperature_K=State.of(Decimal("500")),
    )
    item = replace(
        observation(
            f"openimcc-source-{document['record_id']}",
            f"exp-{source_id}",
            identity,
            Decimal("1"),
            source_id=source_id,
        ),
        locator=Locator(
            record=str(document["record_id"]), source_path=str(path)
        ),
    )
    return document, formula, item


def test_openimcc_compilation_formula_canonicalizer_uses_source_rows(monkeypatch) -> None:
    from simulator.accounting.formulas import parse_formula
    from simulator.chemistry.formula_text import fold_subscript_digits
    from simulator.battery.compilation_tier import predict_thermo_attempt

    rows = (
        (
            "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1471.json",
            "pankratz-1984-usbm-b677",
        ),
        (
            "data/literature/compilations/pankratz-1984-usbm-b677/records/table-1524.json",
            "pankratz-1984-usbm-b677",
        ),
        (
            "data/literature/compilations/pankratz-1987-usbm-b689/records/page-0147.json",
            "pankratz-1987-usbm-b689",
        ),
    )
    calls = []

    def species_thermo(formula, phase, temperature_K, **_kwargs):
        calls.append((formula, phase, temperature_K))
        return {
            "source_table_id": "source-table",
            "source_row_id": "source-row",
            "T_min": 1.0,
            "T_max": 6000.0,
            "derivatives_fit_implied": False,
            "Cp_J_molK": 25.0,
        }

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo", species_thermo
    )
    for relative_path, source_id in rows:
        _source, source_formula, item = _source_formula_observation(
            relative_path, source_id
        )
        source_counts = parse_formula(fold_subscript_digits(source_formula)).elements
        attempt = predict_thermo_attempt(Engine.OPENIMCC, item)
        assert attempt.refusal_reason is None, (source_formula, attempt.refusal_detail)
        sent_formula, _sent_phase, _temperature = calls[-1]
        assert sent_formula != source_formula
        assert parse_formula(sent_formula).elements == source_counts
        assert attempt.value == Decimal("25.0")


def test_openimcc_kelley_trihydrate_uses_janaf_h095_formula_key(monkeypatch) -> None:
    from simulator.accounting.formulas import canonicalize_formula
    from simulator.battery.compilation_tier import predict_thermo_attempt
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[2]
    source, source_formula, item = _source_formula_observation(
        "data/literature/compilations/kelley-king-1961-usbm-b592/records/table-006-0500.json",
        "kelley-king-1961-usbm-b592",
    )
    assert source_formula == "H2SO4·3H2O(l)"
    janaf = load_cached_safe_yaml(
        (root / "data/literature/compilations/janaf/tables/H-095.yaml").read_bytes()
    )
    expected_formula = janaf["table"]["index_entry"]["formula_normalised"]
    assert expected_formula == "H8O7S"
    assert canonicalize_formula(source_formula) == (expected_formula, "l", None)

    calls = []

    def species_thermo(formula, phase, temperature_K, **_kwargs):
        calls.append((formula, phase, temperature_K))
        return {
            "source_table_id": "source-table",
            "source_row_id": "source-row",
            "T_min": 1.0,
            "T_max": 6000.0,
            "derivatives_fit_implied": False,
            "Cp_J_molK": 25.0,
        }

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo", species_thermo
    )
    attempt = predict_thermo_attempt(Engine.OPENIMCC, item)
    assert attempt.refusal_reason is None, attempt.refusal_detail
    assert calls[0][:2] == (expected_formula, "l")
    assert attempt.value == Decimal("25.0")


def test_openimcc_grouped_carbon_formula_uses_janaf_hill_key() -> None:
    from simulator.accounting.formulas import canonicalize_formula
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[2]
    janaf = load_cached_safe_yaml(
        (root / "data/literature/compilations/janaf/tables/C-065.yaml").read_bytes()
    )
    expected_formula = janaf["table"]["index_entry"]["formula_normalised"]
    assert expected_formula == "CH3F"
    assert canonicalize_formula("C(F)(H3)") == (expected_formula, None, None)


def test_openimcc_formula_suffix_phases_use_the_existing_phase_map() -> None:
    from simulator.accounting.formulas import canonicalize_formula
    from simulator.battery.migrate import map_phase

    for suffix, expected_phase in (
        ("s", Phase.CR),
        ("solid", Phase.CR),
        ("liq", Phase.L),
        ("liquid", Phase.L),
        ("vapor", Phase.G),
    ):
        formula, parsed_suffix, refusal = canonicalize_formula(f"FeO({suffix})")
        mapped, _unmapped = map_phase(parsed_suffix)
        assert formula == "FeO"
        assert parsed_suffix == suffix
        assert refusal is None
        assert mapped.is_value and mapped.value is expected_phase


def test_openimcc_group_formula_matches_independent_janaf_source(monkeypatch) -> None:
    from simulator.battery.compilation_tier import predict_thermo_attempt
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[2]
    source_path = (
        root
        / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-0270.json"
    )
    source = json.loads(source_path.read_text(encoding="utf-8"))
    janaf_path = root / "data/literature/compilations/janaf/tables/Al-084.yaml"
    janaf = load_cached_safe_yaml(janaf_path.read_bytes())
    calls = []

    def species_thermo(formula, phase, temperature_K, **_kwargs):
        calls.append((formula, phase, temperature_K))
        return {
            "source_table_id": "source-table",
            "source_row_id": "source-row",
            "T_min": 1.0,
            "T_max": 6000.0,
            "derivatives_fit_implied": False,
            "Cp_J_molK": 25.0,
        }

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo", species_thermo
    )
    _document, _source_formula, item = _source_formula_observation(
        "data/literature/compilations/pankratz-1984-usbm-b677/records/table-0270.json",
        "pankratz-1984-usbm-b677",
    )
    attempt = predict_thermo_attempt(Engine.OPENIMCC, item)
    assert attempt.refusal_reason is None, attempt.refusal_detail
    assert calls[0][:2] == (
        janaf["table"]["index_entry"]["formula"],
        source["phase_as_published"].strip("()"),
    )
    assert attempt.value == Decimal("25.0")


def test_openimcc_formula_canonicalizer_preserves_real_source_subscripts() -> None:
    from simulator.accounting.formulas import canonicalize_formula
    from simulator.chemistry.formula_text import fold_subscript_digits
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[2]
    b677_records = root / "data/literature/compilations/pankratz-1984-usbm-b677/records"
    b10h14 = json.loads((b677_records / "table-0906.json").read_text(encoding="utf-8"))
    s2f10 = json.loads((b677_records / "table-0830.json").read_text(encoding="utf-8"))
    fe_curves = json.loads(
        (
            root
            / "data/literature/compilations/kelley-1960-usbm-b584/source/published-curves.json"
        ).read_text(encoding="utf-8")
    )
    wo_table = load_cached_safe_yaml(
        (
            root
            / "data/literature/compilations/janaf/tables/O-050.yaml"
        ).read_bytes()
    )
    source_formulas = (
        b10h14["formula_as_published"],
        s2f10["formula_as_published"],
        next(
            row["formula"]
            for row in fe_curves
            if row.get("source_record_id") == "table-397"
            and row.get("phase") == "c"
        ),
        wo_table["table"]["index_entry"]["formula_as_published"],
    )
    for source_formula in source_formulas:
        canonical, phase_suffix, refusal = canonicalize_formula(source_formula)
        assert canonicalize_formula(source_formula) == (
            canonical,
            phase_suffix,
            refusal,
        )
        assert (canonical, phase_suffix, refusal) == (
            fold_subscript_digits(source_formula),
            None,
            None,
        )


def test_openimcc_symbolic_hydrate_and_unstated_phase_stay_typed(
    monkeypatch, tmp_path
) -> None:
    from simulator.battery.compilation_tier import predict_thermo_attempt

    def no_engine_call(*_args, **_kwargs):
        raise AssertionError("unresolved formula or phase reached OpenIMCC")

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.species_thermo", no_engine_call
    )
    symbolic_hydrate = Identity(
        quantity=Quantity.CP,
        species=Species(
            "H2SO4·nH2O", State.unknown("source does not state phase")
        ),
        per=State.of(PerBasis.MOL_SPECIES),
        temperature_K=State.of(Decimal("500")),
    )
    hydrate_attempt = predict_thermo_attempt(
        Engine.OPENIMCC,
        observation("openimcc-symbolic-hydrate", "exp", symbolic_hydrate, Decimal("1")),
    )
    assert hydrate_attempt.value is None
    assert hydrate_attempt.refusal_detail["reason"] == (
        "species-symbolic-hydrate-unresolved"
    )

    blank_phase = replace(
        symbolic_hydrate,
        species=Species("FeO", State.unknown("source does not state phase")),
    )
    blank_attempt = predict_thermo_attempt(
        Engine.OPENIMCC,
        observation("openimcc-blank-phase", "exp", blank_phase, Decimal("1")),
    )
    assert blank_attempt.value is None
    assert blank_attempt.refusal_detail["reason"] == (
        "compilation-source-phase-unstated"
    )

    for phase in (Phase.GLASS, Phase.AQ):
        unsupported = replace(blank_phase, species=Species("FeO", phase))
        attempt = predict_thermo_attempt(
            Engine.OPENIMCC,
            observation(
                f"openimcc-{phase.value}-phase", "exp", unsupported, Decimal("1")
            ),
        )
        assert attempt.value is None
        assert attempt.refusal_detail["reason"] == "openimcc-phase-unsupported"

    title_path = tmp_path / "title-only.json"
    title_path.write_text('{"title":"ideal gas"}', encoding="utf-8")
    title_only = replace(
        observation("openimcc-title-phase", "exp", blank_phase, Decimal("1")),
        locator=Locator(record="title-only", source_path=str(title_path)),
    )
    title_attempt = predict_thermo_attempt(Engine.OPENIMCC, title_only)
    assert title_attempt.value is None
    assert title_attempt.refusal_detail["reason"] == (
        "compilation-source-phase-unstated"
    )

    for printed_phase in ("(aq)", "(glass)"):
        source_path = tmp_path / f"{printed_phase[1:-1]}.json"
        source_path.write_text(
            json.dumps({"phase": printed_phase}), encoding="utf-8"
        )
        source_row = replace(
            observation(
                f"openimcc-source-{printed_phase}",
                "exp",
                blank_phase,
                Decimal("1"),
            ),
            locator=Locator(
                record=source_path.stem, source_path=str(source_path)
            ),
        )
        source_attempt = predict_thermo_attempt(Engine.OPENIMCC, source_row)
        assert source_attempt.value is None
        assert source_attempt.refusal_detail["reason"] == (
            "openimcc-phase-unsupported"
        )


def test_openimcc_source_phase_lookup_tracks_source_path_and_revision(tmp_path) -> None:
    from simulator.battery.compilation_tier import _openimcc_source_declared_phase

    def item(path: Path, observation_id: str):
        identity = Identity(
            quantity=Quantity.CP,
            species=Species("FeO", State.unknown("source does not state phase")),
            per=State.of(PerBasis.MOL_SPECIES),
            temperature_K=State.of(Decimal("500")),
        )
        return replace(
            observation(observation_id, "exp", identity, Decimal("1")),
            locator=Locator(record=path.stem, source_path=str(path)),
        )

    gas_path = tmp_path / "gas.json"
    liquid_path = tmp_path / "liquid.json"
    gas_path.write_text('{"phase":"g"}', encoding="utf-8")
    liquid_path.write_text('{"phase":"l"}', encoding="utf-8")

    assert _openimcc_source_declared_phase(item(gas_path, "gas"))[0] is Phase.G
    assert _openimcc_source_declared_phase(item(liquid_path, "liquid"))[0] is Phase.L

    gas_path.write_text('{"phase":"aq"}', encoding="utf-8")
    assert _openimcc_source_declared_phase(item(gas_path, "gas-revised"))[0] is Phase.AQ

    liquid_path.write_text(
        '{"table":{"index_entry":{"state":"cr"}}}', encoding="utf-8"
    )
    assert _openimcc_source_declared_phase(item(liquid_path, "table-state"))[0] is Phase.CR
    liquid_path.write_text('{"table":{"phase":"g"}}', encoding="utf-8")
    assert _openimcc_source_declared_phase(item(liquid_path, "table-phase"))[0] is Phase.G
    liquid_path.write_text('{"section":{"phase":"l"}}', encoding="utf-8")
    assert _openimcc_source_declared_phase(item(liquid_path, "section-phase"))[0] is Phase.L
    liquid_path.write_text('{"series":{"state":"g"}}', encoding="utf-8")
    assert _openimcc_source_declared_phase(item(liquid_path, "series-state"))[0] is Phase.G
    liquid_path.write_text('{"table":{"state_as_published":"l"}}', encoding="utf-8")
    assert _openimcc_source_declared_phase(item(liquid_path, "table-state-note"))[0] is Phase.L


def _load_compilation_thermo_observations(root: Path):
    """Read only the compilation Cp/S/H population used by the census test."""

    from simulator.battery.migrate import (
        iter_observation_store_paths,
        load_yaml,
        observation_from_plain,
    )

    quantities = {Quantity.CP.value, Quantity.S.value, Quantity.H_MINUS_H298.value}
    observations = {}
    literature = root / "data" / "literature"
    for directory in (
        literature / "extracts-v2",
        literature / "observations-v2",
    ):
        if not directory.is_dir():
            continue
        for path in iter_observation_store_paths(directory):
            document = load_yaml(path)
            if not isinstance(document, Mapping):
                continue
            for raw in document.get("observations") or ():
                if not isinstance(raw, Mapping):
                    continue
                identity = raw.get("identity")
                quantity = (
                    identity.get("quantity")
                    if isinstance(identity, Mapping)
                    else None
                )
                evidence = raw.get("evidence")
                evidence_class = (
                    evidence.get("class")
                    if isinstance(evidence, Mapping)
                    else None
                )
                if (
                    not isinstance(quantity, Mapping)
                    or quantity.get("tag") != "value"
                    or quantity.get("value") not in quantities
                    or not isinstance(evidence_class, Mapping)
                    or evidence_class.get("tag") != "value"
                    or evidence_class.get("value")
                    != EvidenceClass.COMPILATION_ASSESSED.value
                ):
                    continue
                observation = observation_from_plain(raw)
                observations[observation.observation_id] = observation
    return observations


def _openimcc_input_available_at_8e(observation) -> bool:
    """The t-1937 scorer's formula and phase gate, before this census change."""

    from simulator.battery.score import parse_species_formula
    from simulator.battery.records import polymorph_token

    identity = observation.identity
    if not isinstance(identity, Identity) or identity.species is None:
        return False
    species = identity.species
    if parse_species_formula(species.formula, species=species) is None:
        return False
    phase = phase_token(species)
    return phase in {Phase.G, Phase.L, Phase.CR} and (
        phase is not Phase.CR or polymorph_token(species) is not None
    )


def _openimcc_compilation_input_census(context) -> dict[str, object]:
    """Measure the 8e and current source-input gates without calling engines."""

    from simulator.accounting.formulas import canonicalize_formula
    from simulator.battery.compilation_tier import (
        _compilation_series_slots,
        _openimcc_species_input,
        is_compilation_evidence,
    )
    from simulator.battery.identity import quantity_token
    from simulator.chemistry.formula_text import fold_subscript_digits

    quantities = {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
    total = requestable_before = requestable_after = 0
    cells_by_quantity: Counter[str] = Counter()
    refused_after: Counter[str] = Counter()
    classes: dict[str, dict[str, object]] = {}
    resolved_before: dict[object, bool] = {}
    resolved_after: dict[
        tuple[object, str | None],
        tuple[str | None, Phase | None, object],
    ] = {}

    def tally(name: str, before: bool, refusal, cell_count: int) -> None:
        row = classes.setdefault(
            name,
            {
                "affected_cells": 0,
                "requestable_before": 0,
                "typed_refused_before": 0,
                "requestable_after": 0,
                "typed_refused_after": 0,
                "refusal_reasons_after": Counter(),
            },
        )
        row["affected_cells"] += cell_count
        if before:
            row["requestable_before"] += cell_count
        else:
            row["typed_refused_before"] += cell_count
        if refusal is None:
            row["requestable_after"] += cell_count
        else:
            row["typed_refused_after"] += cell_count
            reasons = row["refusal_reasons_after"]
            assert isinstance(reasons, Counter)
            reasons[refusal.code] += cell_count

    for parent in context.observations.values():
        if not is_compilation_evidence(parent):
            continue
        quantity = quantity_token(parent.identity)
        if quantity not in quantities:
            continue
        origin = context.origins.get(parent.observation_id)
        slots = _compilation_series_slots(parent, origin)
        if slots is None:
            value = parent.value.point
            cell_count = int(value is not None and value.is_finite())
        else:
            cell_count = sum(
                1 for _temperature, magnitude, _occurrence in slots
                if magnitude.is_finite()
            )
        if cell_count == 0:
            continue
        total += cell_count
        cells_by_quantity[quantity.value] += cell_count

        source_path = None if parent.locator is None else parent.locator.source_path
        species = parent.identity.species if isinstance(parent.identity, Identity) else None
        before = resolved_before.get(species)
        if before is None:
            before = _openimcc_input_available_at_8e(parent)
            resolved_before[species] = before
        if before:
            requestable_before += cell_count

        input_key = (species, source_path)
        resolved = resolved_after.get(input_key)
        if resolved is None:
            resolved = _openimcc_species_input(parent)
            resolved_after[input_key] = resolved
        formula, phase, refusal = resolved
        if refusal is None:
            assert formula is not None and phase is not None
            requestable_after += cell_count
        else:
            refused_after[refusal.code] += cell_count

        raw_formula = "" if species is None else species.formula
        canonical, suffix_phase, _formula_error = canonicalize_formula(raw_formula)
        if suffix_phase is not None:
            tally("formula_phase_suffix", before, refusal, cell_count)
        if "·" in raw_formula:
            tally("hydrate_dot", before, refusal, cell_count)
        formula_text = "".join(raw_formula.strip().split())
        group_formula = formula_text
        if suffix_phase is not None:
            group_formula = formula_text[: -(len(suffix_phase) + 2)]
        if (
            canonical is not None
            and any(token in group_formula for token in "()[]{}")
            and canonical != fold_subscript_digits(group_formula)
        ):
            tally("parenthesized_group", before, refusal, cell_count)
        species_phase = phase_token(species) if species is not None else None
        if species_phase not in {Phase.G, Phase.L, Phase.CR} or suffix_phase in {
            "aq",
            "glass",
        }:
            tally("source_phase", before, refusal, cell_count)

    return {
        "numeric_cp_s_h_cells": total,
        "cells_by_quantity": dict(sorted(cells_by_quantity.items())),
        "requestable_before": requestable_before,
        "typed_refused_before": total - requestable_before,
        "requestable_after": requestable_after,
        "typed_refused_after": sum(refused_after.values()),
        "refusal_reasons_after": dict(sorted(refused_after.items())),
        "classes": {
            name: {
                **{
                    key: value
                    for key, value in row.items()
                    if key != "refusal_reasons_after"
                },
                "refusal_reasons_after": dict(
                    sorted(row["refusal_reasons_after"].items())
                ),
            }
            for name, row in sorted(classes.items())
        },
    }


def test_openimcc_source_input_gate_covers_every_compilation_cp_s_h_cell() -> None:
    from simulator.battery.score import ScoreContext

    root = Path(__file__).resolve().parents[2]
    observations = _load_compilation_thermo_observations(root)
    context = ScoreContext(
        works={},
        experiments={},
        observations=observations,
        root=root,
    )
    census = _openimcc_compilation_input_census(context)
    assert census["numeric_cp_s_h_cells"] == 328_300, census
    assert sum(census["cells_by_quantity"].values()) == census[
        "numeric_cp_s_h_cells"
    ], census
    assert (
        census["requestable_before"] + census["typed_refused_before"]
        == census["numeric_cp_s_h_cells"]
    ), census
    assert (
        census["requestable_after"] + census["typed_refused_after"]
        == census["numeric_cp_s_h_cells"]
    ), census
    classes = census["classes"]
    assert {
        "parenthesized_group",
        "formula_phase_suffix",
        "hydrate_dot",
        "source_phase",
    } <= classes.keys(), census
    for row in classes.values():
        assert row["affected_cells"] == (
            row["requestable_before"] + row["typed_refused_before"]
        ), census
        assert row["affected_cells"] == (
            row["requestable_after"] + row["typed_refused_after"]
        ), census

    measured_counts = {
        "requestable_before": census["requestable_before"],
        "typed_refused_before": census["typed_refused_before"],
        "requestable_after": census["requestable_after"],
        "typed_refused_after": census["typed_refused_after"],
        "classes": {
            name: {
                key: row[key]
                for key in (
                    "affected_cells",
                    "requestable_before",
                    "typed_refused_before",
                    "requestable_after",
                    "typed_refused_after",
                )
            }
            for name, row in classes.items()
        },
    }
    expected_counts = {
        "requestable_before": 240_209,
        "typed_refused_before": 88_091,
        "requestable_after": 306_808,
        "typed_refused_after": 21_492,
        "classes": {
            "formula_phase_suffix": {
                "affected_cells": 568,
                "requestable_before": 426,
                "typed_refused_before": 142,
                "requestable_after": 369,
                "typed_refused_after": 199,
            },
            "hydrate_dot": {
                "affected_cells": 1_146,
                "requestable_before": 371,
                "typed_refused_before": 775,
                "requestable_after": 971,
                "typed_refused_after": 175,
            },
            "parenthesized_group": {
                "affected_cells": 3_836,
                "requestable_before": 2_424,
                "typed_refused_before": 1_412,
                "requestable_after": 3_729,
                "typed_refused_after": 107,
            },
            "source_phase": {
                "affected_cells": 30_227,
                "requestable_before": 0,
                "typed_refused_before": 30_227,
                "requestable_after": 13_922,
                "typed_refused_after": 16_305,
            },
        },
    }
    assert measured_counts == expected_counts, json.dumps(
        measured_counts, sort_keys=True
    )
