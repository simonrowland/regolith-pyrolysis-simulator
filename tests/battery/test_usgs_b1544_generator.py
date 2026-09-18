"""USGS B1544 source-aware generator controls and negative witnesses."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    QUANTITY_UNITS,
    EvidenceClass,
    IdentityEqualKind,
    NoticeKind,
    Phase,
    Polymorph,
    Quantity,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.generators import usgs_b1544 as generator
from simulator.battery.identity import (
    identity_equal,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
)
from simulator.battery.migrate import (
    iter_observation_store_paths,
    make_species,
    observation_from_plain,
)
from simulator.battery.polymorph_dictionary import coerce_polymorph_token
from simulator.battery.validate import reaction_atom_balance
from simulator.battery.records import State
from simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader import (
    COMPILATION_ROOT,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
ROOT = Path(__file__).resolve().parents[2]
B1544_STORE_DIR = ROOT / "data" / "literature" / "observations-v2"
B1544_STORE_PATTERN = "compilations-hemingway-haas-robinson-1982-usgs-b1544.yaml"
B1544_STORED = 3035
B1544_REFUSED = 99
B1544_EXCLUDED = 1605
B1544_RAW = 4739
JANAF_STORE_DIR = ROOT / "data" / "literature" / "observations-v2"


def _load(record_id: str) -> dict:
    path = RECORDS_DIR / f"{record_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _generation(record_id: str) -> generator.RecordGeneration:
    return generator.generate_record(_load(record_id))


def _observations_for(
    generated: generator.RecordGeneration,
    quantity: Quantity,
    *,
    temperature: str | None = None,
    basis: str | None = None,
) -> list:
    result = []
    for observation in generated.observations:
        if quantity_token(observation.identity) is not quantity:
            continue
        if temperature is not None:
            temp = observation.identity.temperature_K
            if temp is None or not temp.is_value or str(temp.value) != temperature:
                continue
        if basis is not None:
            note = observation.locator.note if observation.locator is not None else ""
            if f"formation_basis={basis}" not in (note or ""):
                continue
        result.append(observation)
    return result


def test_corundum_298_from_the_elements_worked_row() -> None:
    generated = _generation("usgs-b1544-corundum")
    gibbs = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )
    logk = _observations_for(
        generated,
        Quantity.LOG10_KF,
        temperature="298.15",
        basis="from_the_elements",
    )
    assert len(gibbs) == 1
    assert len(logk) == 1
    assert gibbs[0].value.point == Decimal("-1582.242")
    assert logk[0].value.point == Decimal("277.203")
    assert "as_published='-1582.242'" in (gibbs[0].locator.note or "")
    assert "kJ/mol" in (gibbs[0].locator.note or "")
    calculated = log10K_from_delta_fG_kJ_mol(
        Decimal("-1582.242"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1544_R_J_PER_MOL_K,
    )
    assert abs(calculated - Decimal("277.203")) < Decimal("0.001")


def test_refuses_named_damaged_token() -> None:
    generated = _generation("usgs-b1544-sillimanite")
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "107.2J"
    ]
    assert refused
    assert "letter tail" in refused[0]["reason"] or "107.2" in refused[0]["reason"]
    cp_points = [
        observation.value.point
        for observation in _observations_for(generated, Quantity.CP)
    ]
    assert Decimal("107.2") not in cp_points
    reconstructed = [
        observation
        for observation in _observations_for(generated, Quantity.CP)
        if observation.value.point == Decimal("199.38")
    ]
    assert reconstructed
    assert reconstructed[0].evidence.original_method_class == "reconstructed_from_printed_page"
    assert "as_published='199.3R'" in (reconstructed[0].locator.note or "")
    assert Decimal("199.3") not in cp_points


def test_notice_case_calcium_olivine_reference_1120_oxides() -> None:
    generated = _generation("usgs-b1544-calcium-olivine-reference")
    logk = _observations_for(
        generated,
        Quantity.LOG10_KF,
        temperature="1120",
        basis="from_the_oxides",
    )
    assert logk
    noticed = [
        observation
        for observation in logk
        if any(
            notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            and "log10_Kf_from_delta_fG" in notice.reason
            for notice in observation.notices
        )
    ]
    assert noticed
    notice = next(
        item
        for item in noticed[0].notices
        if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    assert notice.original is not None
    assert "residual" in notice.reason
    assert noticed[0].value.point == Decimal("6.311")


def test_cell_accounting_mutation_drops_one_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generator,
        "STORED_GRID_COLUMNS",
        frozenset(column for column in generator.STORED_GRID_COLUMNS if column != "entropy"),
    )
    with pytest.raises(AssertionError, match="unexplained"):
        _generation("usgs-b1544-corundum")


def test_kj_to_j_emission() -> None:
    generated = _generation("usgs-b1544-corundum")
    gibbs = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )[0]
    assert QUANTITY_UNITS[Quantity.DELTA_FG] == "kJ_per_declared_mol_basis"
    assert gibbs.value.point == Decimal("-1582.242")
    assert gibbs.derivation.output_unit == QUANTITY_UNITS[Quantity.DELTA_FG]
    factor = dict(gibbs.derivation.parameters)["kj_to_j"]
    assert factor.state.value == Decimal("1000")
    assert gibbs.value.point * factor.state.value == Decimal("-1582242")
    entropy = _observations_for(
        generated,
        Quantity.S,
        temperature="298.15",
    )[0]
    entropy_names = {name for name, _parameter in entropy.derivation.parameters}
    assert "kj_to_j" not in entropy_names


def test_formation_basis_is_identity_axis() -> None:
    generated = _generation("usgs-b1544-kyanite")
    elements = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )
    oxides = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_oxides",
    )
    assert len(elements) == 1
    assert len(oxides) == 1
    assert elements[0].value.point == Decimal("-2444.032")
    assert oxides[0].value.point == Decimal("-5.502")
    assert elements[0].identity.reaction is not None
    assert oxides[0].identity.reaction is not None
    assert elements[0].identity.reaction.is_value
    assert oxides[0].identity.reaction.is_value
    cross = identity_equal(elements[0].identity, oxides[0].identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "reaction" in cross.fields
    same = identity_equal(elements[0].identity, elements[0].identity)
    assert same.kind is IdentityEqualKind.EQUAL
    oxides_same = identity_equal(oxides[0].identity, oxides[0].identity)
    assert oxides_same.kind is IdentityEqualKind.EQUAL


def test_al2sio5_polymorphs_are_compared_identity_values() -> None:
    kyanite = _observations_for(
        _generation("usgs-b1544-kyanite"),
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )[0]
    andalusite = _observations_for(
        _generation("usgs-b1544-andalusite"),
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )[0]
    sillimanite = _observations_for(
        _generation("usgs-b1544-sillimanite"),
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )[0]
    assert kyanite.identity.species.formula == "Al2SiO5"
    assert andalusite.identity.species.formula == "Al2SiO5"
    assert sillimanite.identity.species.formula == "Al2SiO5"
    assert kyanite.identity.species.polymorph.value == "kyanite"
    assert andalusite.identity.species.polymorph.value == "andalusite"
    assert sillimanite.identity.species.polymorph.value == "sillimanite"
    kya_and = identity_equal(kyanite.identity, andalusite.identity)
    kya_sil = identity_equal(kyanite.identity, sillimanite.identity)
    and_sil = identity_equal(andalusite.identity, sillimanite.identity)
    assert kya_and.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert kya_sil.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert and_sil.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in kya_and.fields
    assert identity_equal(kyanite.identity, kyanite.identity).kind is IdentityEqualKind.EQUAL


def test_table1_alooh_polymorphs_are_compared_identity_values() -> None:
    generated = _generation("usgs-b1544-table-1")
    by_name: dict[str, object] = {}
    for observation in generated.observations:
        if observation.identity.species.formula != "AlO(OH)":
            continue
        polymorph = observation.identity.species.polymorph
        if polymorph is None or not polymorph.is_value:
            continue
        by_name.setdefault(str(polymorph.value), observation)
    assert "diaspore" in by_name
    assert "boehmite" in by_name
    cross = identity_equal(by_name["diaspore"].identity, by_name["boehmite"].identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in cross.fields
    same = identity_equal(by_name["diaspore"].identity, by_name["diaspore"].identity)
    assert same.kind is IdentityEqualKind.EQUAL


def _formation_element_phases(observation) -> dict[str, str]:
    assert observation.identity.formation_elements.is_value
    result = {}
    for element, species in observation.identity.formation_elements.value:
        assert species.phase.is_value
        result[element] = species.phase.value.value
    return result


def test_elemental_reference_phase_follows_documented_transitions() -> None:
    generated = _generation("usgs-b1544-sillimanite")
    below = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="900",
        basis="from_the_elements",
    )[0]
    above_al = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="1200",
        basis="from_the_elements",
    )[0]
    below_si = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="1600",
        basis="from_the_elements",
    )[0]
    above_si = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="1700",
        basis="from_the_elements",
    )[0]
    assert _formation_element_phases(below) == {"Al": "cr", "Si": "cr", "O": "g"}
    assert _formation_element_phases(above_al) == {"Al": "l", "Si": "cr", "O": "g"}
    assert _formation_element_phases(below_si) == {"Al": "l", "Si": "cr", "O": "g"}
    assert _formation_element_phases(above_si) == {"Al": "l", "Si": "l", "O": "g"}
    cross = identity_equal(below.identity, above_al.identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "reaction" in cross.fields or "formation_elements" in cross.fields


def test_casio3_reference_transition_sides_are_distinct_polymorphs() -> None:
    generated = _generation("usgs-b1544-casio3-reference")
    sides = [
        observation
        for observation in generated.observations
        if ":S:shared:T=1398:" in observation.observation_id
    ]
    assert len(sides) == 2
    points = {observation.value.point for observation in sides}
    assert points == {Decimal("255.15"), Decimal("259.29")}
    by_point = {observation.value.point: observation for observation in sides}
    low = by_point[Decimal("255.15")]
    high = by_point[Decimal("259.29")]
    assert low.identity.species.polymorph.value == "wollastonite"
    assert high.identity.species.polymorph.value == "cyclowollastonite"
    cross = identity_equal(low.identity, high.identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in cross.fields
    assert identity_equal(low.identity, low.identity).kind is IdentityEqualKind.EQUAL


def test_missing_formation_basis_is_unknown_not_elements_default() -> None:
    product = make_species("Al2SiO5", Phase.CR, State.of("Kyanite"))
    missing_reaction, missing_elements = generator._basis_states(product, None)
    assert missing_reaction.is_unknown
    assert missing_elements.is_unknown
    assert missing_reaction.reason == generator.MISSING_FORMATION_BASIS_REASON
    elements_reaction, _elements = generator._basis_states(product, "from_the_elements")
    oxides_reaction, _oxides = generator._basis_states(product, "from_the_oxides")
    assert elements_reaction.is_value
    assert oxides_reaction.is_value
    assert elements_reaction.value != oxides_reaction.value


def test_named_damaged_tokens_are_the_recon_witnesses() -> None:
    refused = {item["as_published"] for item in generator.NAMED_DAMAGED_TOKENS}
    reconstructed = {item["as_published"] for item in generator.RECONSTRUCTED_TOKENS}
    assert refused == {"107.2J", "-1625.31P"}
    assert reconstructed == {
        ":198.15",
        "374.e9",
        "13!l.799",
        ":..111.855",
        "199.3R",
    }
    assert "107.2J" not in reconstructed
    assert "-1625.31P" not in reconstructed


def test_gas_constant_is_b1452_parent_not_modern() -> None:
    from simulator.battery.identity import R_J_PER_MOL_K

    assert generator.B1544_R_J_PER_MOL_K == Decimal("8.3143")
    assert generator.B1544_R_J_PER_MOL_K != R_J_PER_MOL_K
    modern = log10K_from_delta_fG_kJ_mol(Decimal("-1582.242"), Decimal("298.15"))
    source = log10K_from_delta_fG_kJ_mol(
        Decimal("-1582.242"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1544_R_J_PER_MOL_K,
    )
    assert modern != source
    assert abs(source - Decimal("277.203")) < Decimal("0.001")


def test_ocr_suspect_without_image_correction_is_refused() -> None:
    generated = _generation("usgs-b1544-corundum")
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] in {"o.ooo", "1J2.86"}
    ]
    assert {row["as_published"] for row in refused} == {"o.ooo", "1J2.86"}
    entropy = _observations_for(
        generated, Quantity.S, temperature="298.15"
    )[0]
    assert entropy.value.point == Decimal("50.92")


def test_dickite_image_verified_temperature_is_not_refused() -> None:
    generated = _generation("usgs-b1544-dickite")
    refused_t = [
        row
        for row in generated.report["refusals"]
        if row["column"] == "temperature" and row.get("row_index") == 1
    ]
    assert refused_t == []
    stored = _observations_for(generated, Quantity.S, temperature="400")
    assert stored
    assert stored[0].value.point == Decimal("274.79")


def test_kaolinite_damaged_temperature_is_reconstructed_to_298() -> None:
    generated = _generation("usgs-b1544-kaolinite")
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == ":198.15"
    ]
    assert refused == []
    assert not _observations_for(generated, Quantity.DELTA_FG, temperature="198.15")
    stored = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )
    assert stored
    assert stored[0].value.point == Decimal("-3799.611")
    assert stored[0].evidence.original_method_class == (
        "reconstructed_from_heading_and_logkf_identity"
    )
    assert "as_published=':198.15'" not in (stored[0].locator.note or "")
    assert "temperature_reconstructed=298.15" in (stored[0].locator.note or "")


def test_table1_cells_are_quoted_attributed_not_b1544_assessment() -> None:
    generated = _generation("usgs-b1544-table-1")
    haas = [
        observation
        for observation in generated.observations
        if observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        and observation.evidence.attribution is not None
        and "Hass" in observation.evidence.attribution
        and observation.value.point == Decimal("-1675.711")
    ]
    assert len(haas) == 1
    assert haas[0].identity.quantity.value is Quantity.DELTA_FH
    assessed = [
        observation
        for observation in generated.observations
        if observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    ]
    assert assessed == []
    grid = _observations_for(
        _generation("usgs-b1544-corundum"),
        Quantity.DELTA_FH,
        temperature="298.15",
        basis="from_the_elements",
    )
    assert grid
    assert grid[0].evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    # Same physical identity (formula, polymorph, T, from-the-elements ΔfH).
    # Evidence class, not identity, is the origin discriminator. A consumer
    # that keys (formula, quantity, T) and ignores evidence.class_ will mix
    # Haas 1979 with B1544's T-grid assessment; that is a consumer error.
    outcome = identity_equal(haas[0].identity, grid[0].identity)
    assert outcome.kind is IdentityEqualKind.EQUAL
    assert haas[0].evidence.attribution != grid[0].evidence.attribution


def test_table1_formula_is_last_token_and_mineral_name_is_polymorph() -> None:
    assert generator._table1_formula("Ca-Al pyroxene CaAl2SiO6") == "CaAl2SiO6"
    assert generator._table1_mineral_name("Ca-Al pyroxene CaAl2SiO6") == "Ca-Al pyroxene"
    assert generator._table1_formula("Diaspore AlO(OH)") == "AlO(OH)"
    assert generator._table1_mineral_name("Diaspore AlO(OH)") == "Diaspore"
    assert generator._table1_formula("Boehmite AlO(OH)") == "AlO(OH)"
    assert generator._table1_mineral_name("Boehmite AlO(OH)") == "Boehmite"


def test_tgrid_uncertainty_is_attached_to_stored_siblings() -> None:
    generated = _generation("usgs-b1544-corundum")
    entropy = _observations_for(generated, Quantity.S, temperature="298.15")[0]
    assert entropy.uncertainty.kind is UncertaintyKind.PRINTED
    assert entropy.uncertainty.verbatim == "0.20"
    gibbs = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_elements",
    )[0]
    assert gibbs.uncertainty.kind is UncertaintyKind.PRINTED
    assert gibbs.uncertainty.verbatim == "1.100"
    assert any(
        row["column"] == "uncertainty_entropy"
        and generator.UNCERTAINTY_EXCLUDE_REASON == row["reason"]
        for row in generated.report["exclusions"]
    )
    kyanite = _generation("usgs-b1544-kyanite")
    oxides = _observations_for(
        kyanite,
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_oxides",
    )[0]
    assert oxides.uncertainty.kind is UncertaintyKind.PRINTED
    assert oxides.uncertainty.verbatim == "0.389"


def test_planck_function_is_transcription_check_not_stored() -> None:
    generated = _generation("usgs-b1544-corundum")
    planck = [
        row
        for row in generated.report["exclusions"]
        if row["column"] == "planck_function"
    ]
    assert planck
    by_column = generated.report["cell_accounting"]["by_column"]
    assert by_column["planck_function"].get("stored", 0) == 0
    assert by_column["planck_function"]["excluded"] == by_column["planck_function"]["raw"]
    quantities = {quantity_token(observation.identity) for observation in generated.observations}
    assert Quantity.S in quantities
    assert Quantity.CP in quantities


def test_enthalpy_increment_over_t_is_a_vocabulary_gap() -> None:
    generated = _generation("usgs-b1544-corundum")
    assert any(
        row["column"] == "enthalpy_increment_over_T" for row in generated.report["vocabulary_gaps"]
    )
    assert Quantity.H_MINUS_H298 not in {
        quantity_token(observation.identity) for observation in generated.observations
    }


def test_neighbour_sign_flags_ca3sio5_oxides_delta_fh() -> None:
    generated = _generation("usgs-b1544-ca3sio5-reference")
    hits = generated.report["neighbour_sign_hits"]
    assert any(
        hit["as_published"] == ":..111.855"
        and hit["column"] == "formation_enthalpy"
        and hit["formation_basis"] == "from_the_oxides"
        for hit in hits
    )


def _rewrite_as_published(record: dict, old: str, new: str) -> None:
    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            if obj.get("as_published") == old:
                obj["as_published"] = new
                obj["value"] = new
                obj["ocr_suspect"] = False
                obj["flags"] = []
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(record)


def test_reconstructed_tokens_store_corroborated_values_and_keep_raw() -> None:
    rankinite = _generation("usgs-b1544-rankinite")
    logk = _observations_for(
        rankinite,
        Quantity.LOG10_KF,
        temperature="1200",
        basis="from_the_elements",
    )
    reconstructed_logk = [
        observation
        for observation in logk
        if observation.value.point == Decimal("138.799")
    ]
    assert reconstructed_logk
    assert reconstructed_logk[0].evidence.original_method_class == (
        "reconstructed_from_logkf_identity"
    )
    assert "as_published='13!l.799'" in (reconstructed_logk[0].locator.note or "")
    planck_excluded = [
        row
        for row in rankinite.report["exclusions"]
        if row["as_published"] == "374.e9"
    ]
    assert planck_excluded
    assert any(
        result.get("identity") == "planck_vs_S_minus_HHT"
        and result.get("ok") is True
        and "374.883" in str(result.get("calculated"))
        for result in rankinite.report["identity_results"]
        if result.get("temperature_as_published") == "1200"
    )

    oxides = _generation("usgs-b1544-ca3sio5-reference")
    delta_h = _observations_for(
        oxides,
        Quantity.DELTA_FH,
        temperature="1300",
        basis="from_the_oxides",
    )
    reconstructed_h = [
        observation
        for observation in delta_h
        if observation.value.point == Decimal("-111.855")
    ]
    assert reconstructed_h
    assert reconstructed_h[0].evidence.original_method_class == (
        "reconstructed_from_neighbour_sign"
    )
    assert "as_published=':..111.855'" in (reconstructed_h[0].locator.note or "")

    casio3 = _generation("usgs-b1544-casio3-reference")
    casio3_h = _observations_for(
        casio3,
        Quantity.DELTA_FH,
        temperature="1500",
        basis="from_the_elements",
    )
    assert all(observation.value.point != Decimal("-1625.31") for observation in casio3_h)
    refused_p = [
        row
        for row in casio3.report["refusals"]
        if row["as_published"] == "-1625.31P"
    ]
    assert refused_p
    assert "grain" in refused_p[0]["reason"]


def test_reconstruction_preserves_column_printed_grain() -> None:
    sillimanite = _load("usgs-b1544-sillimanite")
    grains = generator._column_grain_map(sillimanite)
    assert grains[("heat_capacity", None)] == Decimal("0.01")
    generated = generator.generate_record(sillimanite)
    stored_cp = next(
        observation
        for observation in _observations_for(generated, Quantity.CP)
        if observation.value.point == Decimal("199.38")
    )
    assert generator._value_grain(stored_cp.value.point) == Decimal("0.01")
    assert stored_cp.evidence.original_method_class == "reconstructed_from_printed_page"

    casio3 = _load("usgs-b1544-casio3-reference")
    casio3_grains = generator._column_grain_map(casio3)
    assert casio3_grains[("formation_enthalpy", "from_the_elements")] == Decimal("0.001")
    kaolinite = _load("usgs-b1544-kaolinite")
    kaolinite_grains = generator._column_grain_map(kaolinite)
    assert kaolinite_grains[("temperature", None)] == Decimal("1")
    rankinite = _load("usgs-b1544-rankinite")
    rankinite_grains = generator._column_grain_map(rankinite)
    assert rankinite_grains[("planck_function", None)] == Decimal("0.01")
    assert rankinite_grains[("log_kf", "from_the_elements")] == Decimal("0.001")
    oxides = _load("usgs-b1544-ca3sio5-reference")
    oxides_grains = generator._column_grain_map(oxides)
    assert oxides_grains[("formation_enthalpy", "from_the_oxides")] == Decimal("0.001")


def test_coarser_letter_tail_reconstruction_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generator,
        "RECONSTRUCTED_TOKENS",
        (
            {
                "as_published": "199.3R",
                "record_id": "usgs-b1544-sillimanite",
                "value": Decimal("199.3"),
                "method_class": "reconstructed_letter_tail_dropped",
                "evidence": "seed: remaining digits 199.3 are coarser than Cp grain 0.01",
            },
        ),
    )
    generated = _generation("usgs-b1544-sillimanite")
    cp_points = [
        observation.value.point
        for observation in _observations_for(generated, Quantity.CP)
    ]
    assert Decimal("199.3") not in cp_points
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "199.3R"
    ]
    assert refused
    assert "grain" in refused[0]["reason"]


def test_dropped_minus_refuses_clean_token_unmutated_neighbour_still_stores() -> None:
    mutated = _load("usgs-b1544-ca3sio5-reference")
    _rewrite_as_published(mutated, ":..111.855", "111.855")
    generated = generator.generate_record(mutated)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "111.855"
        and row["column"] == "formation_enthalpy"
        and row["formation_basis"] == "from_the_oxides"
    ]
    assert refused
    assert "neighbour-sign" in refused[0]["reason"]
    stored_positive = [
        observation
        for observation in generated.observations
        if observation.value.point == Decimal("111.855")
    ]
    assert stored_positive == []
    control = _generation("usgs-b1544-ca3sio5-reference")
    neighbour = _observations_for(
        control,
        Quantity.DELTA_FH,
        temperature="1200",
        basis="from_the_oxides",
    )
    assert neighbour
    assert neighbour[0].value.point == Decimal("-113.073")


def test_10x_identity_refuses_seeded_residual_unmutated_control_stores() -> None:
    mutated = _load("usgs-b1544-calcium-olivine-reference")
    changed = 0
    for row in mutated["rows"]:
        temperature = (row.get("temperature") or {}).get("as_published")
        if temperature != "1120":
            continue
        cell = row["formation"]["from_the_oxides"]["gibbs_energy"]
        # 10× the printed ΔfG: −135.418 kJ/mol → −1354.18 kJ/mol.
        # Predicted log Kf = −1000 × (−1354.18) / (8.3143 × 1120 × ln 10) ≈ 63.16
        # vs printed 6.311. Residual ≫ 10× temperature-grain tolerance.
        cell["as_published"] = "-1354.18"
        cell["value"] = "-1354.18"
        changed += 1
    assert changed == 2
    generated = generator.generate_record(mutated)
    refused = [
        row
        for row in generated.report["refusals"]
        if "10×" in row["reason"]
        and row["formation_basis"] == "from_the_oxides"
        and row.get("row_index") in {9, 10}
    ]
    columns = {(row["row_index"], row["column"]) for row in refused}
    assert (9, "formation_gibbs_energy") in columns
    assert (9, "log_kf") in columns
    stored_logk = _observations_for(
        generated,
        Quantity.LOG10_KF,
        temperature="1120",
        basis="from_the_oxides",
    )
    assert stored_logk == []
    control = _generation("usgs-b1544-calcium-olivine-reference")
    control_logk = _observations_for(
        control,
        Quantity.LOG10_KF,
        temperature="1120",
        basis="from_the_oxides",
    )
    assert control_logk
    assert all(item.value.point == Decimal("6.311") for item in control_logk)
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        for item in control_logk
        for notice in item.notices
    )


def test_10x_planck_refuses_participating_cells_before_vocab_exclusion() -> None:
    mutated = _load("usgs-b1544-corundum")
    target = None
    for row in mutated["rows"]:
        if (row.get("temperature") or {}).get("as_published") == "400":
            target = row
            break
    assert target is not None
    # Printed: S=76.72, (H−H298)/T=22.455, −(G−H298)/T=54.26.
    # 76.72 − 22.455 = 54.265 vs 54.26 (clean 1×). 10× planck 542.6.
    target["planck_function"]["as_published"] = "542.6"
    target["planck_function"]["value"] = "542.6"
    generated = generator.generate_record(mutated)
    refused = {
        row["column"]: row
        for row in generated.report["refusals"]
        if row.get("row_index") == 1 and "10×" in row["reason"]
    }
    assert "planck_function" in refused
    assert "entropy" in refused
    assert "enthalpy_increment_over_T" in refused
    stored_s = _observations_for(generated, Quantity.S, temperature="400")
    assert stored_s == []
    control = _generation("usgs-b1544-corundum")
    control_s = _observations_for(control, Quantity.S, temperature="400")
    assert control_s
    assert control_s[0].value.point == Decimal("76.72")
    assert any(
        row["column"] == "planck_function" and row.get("row_index") == 1
        for row in control.report["exclusions"]
    )


def test_record_accounting_closes_for_every_committed_record() -> None:
    paths = sorted(RECORDS_DIR.glob("*.json"))
    assert paths
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        generated = generator.generate_record(record)
        accounting = generated.report["cell_accounting"]
        raw = accounting["raw_numeric_tokens"]
        stored = accounting["stored"]
        refused = accounting["refused"]
        excluded = accounting["excluded"]
        assert stored + refused + excluded == raw, record["record_id"]
        assert accounting["unexplained"] == 0


def test_cli_writes_staging(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert (
        generator.main(["--records", str(RECORDS_DIR), "--out", str(out)]) == 0
    )
    summary = (out / "summary.yaml").read_text(encoding="utf-8")
    assert "hemingway-haas-robinson-1982-usgs-b1544" in summary
    assert list((out / "observations").glob("*.yaml"))
    assert list((out / "reports").glob("*.yaml"))


def test_healthy_corundum_accounting_is_not_unexplained() -> None:
    generated = _generation("usgs-b1544-corundum")
    accounting = generated.report["cell_accounting"]
    assert accounting["unexplained"] == 0
    assert (
        accounting["stored"] + accounting["refused"] + accounting["excluded"]
        == accounting["raw_numeric_tokens"]
    )
    # Walker-level unexplained is test_cell_accounting_mutation_drops_one_token
    # (mutates STORED_GRID_COLUMNS and re-runs generate_record). Mutating a
    # deepcopy of the report dict does not exercise the walker.


def _load_yaml(path: Path) -> dict:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=loader) or {}
    assert isinstance(payload, dict)
    return payload


def _b1544_store_paths() -> list[Path]:
    paths = iter_observation_store_paths(B1544_STORE_DIR, B1544_STORE_PATTERN)
    assert paths, "B1544 observation store is missing"
    return paths


def _load_b1544_store_observations() -> list[dict]:
    observations: list[dict] = []
    for path in _b1544_store_paths():
        payload = _load_yaml(path)
        observations.extend(payload.get("observations") or [])
    return observations


def test_b1544_lift_is_the_generator() -> None:
    src = (ROOT / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    assert "def _lift_b1544_from_generator" in src
    assert "generate_record" in src
    assert "def _is_usgs_b1544_record" in src


def test_b1544_store_is_record_sharded() -> None:
    single = B1544_STORE_DIR / "compilations-hemingway-haas-robinson-1982-usgs-b1544.yaml"
    shard_dir = B1544_STORE_DIR / "compilations-hemingway-haas-robinson-1982-usgs-b1544"
    assert not single.exists()
    assert shard_dir.is_dir()
    paths = _b1544_store_paths()
    assert all(path.parent == shard_dir for path in paths)
    assert all(path.name.startswith("usgs-b1544-") and path.name.endswith(".yaml") for path in paths)
    assert len(paths) == 33
    n = sum(path.read_text(encoding="utf-8").count("\n- observation_id:") for path in paths)
    assert n == B1544_STORED


def test_b1544_store_census_is_true_of_observations_v2() -> None:
    """Stored/refused/excluded must hold of observations-v2, not just generate_record."""

    stored_rows = _load_b1544_store_observations()
    stored_ids = {row["observation_id"] for row in stored_rows}
    assert len(stored_rows) == B1544_STORED
    assert len(stored_ids) == B1544_STORED
    assert all(row.get("value", {}).get("kind") != "unavailable" for row in stored_rows)

    generated_ids: set[str] = set()
    refused = 0
    excluded = 0
    raw = 0
    refused_as_published: set[str] = set()
    for path in sorted(RECORDS_DIR.glob("*.json")):
        generated = generator.generate_record(json.loads(path.read_text(encoding="utf-8")))
        generated_ids.update(obs.observation_id for obs in generated.observations)
        accounting = generated.report["cell_accounting"]
        raw += int(accounting["raw_numeric_tokens"])
        refused += int(accounting["refused"])
        excluded += int(accounting["excluded"])
        refused_as_published.update(
            row["as_published"] for row in generated.report["refusals"]
        )

    assert raw == B1544_RAW
    assert refused == B1544_REFUSED
    assert excluded == B1544_EXCLUDED
    assert stored_ids == generated_ids
    assert B1544_STORED + B1544_REFUSED + B1544_EXCLUDED == B1544_RAW

    notes = " ".join(str((row.get("locator") or {}).get("note") or "") for row in stored_rows)
    for token in ("107.2J", "-1625.31P"):
        assert token in refused_as_published
        assert f"as_published='{token}'" not in notes


def test_b1544_store_keeps_circularity_and_identity_fields() -> None:
    rows = _load_b1544_store_observations()
    warning = generator.CIRCULARITY_WARNING
    corundum = None
    quartz_sides: dict[str, dict] = {}
    for row in rows:
        relation = (row.get("derivation") or {}).get("relation") or ""
        assert "scoring_eligible=false" in relation
        assert warning in relation
        evidence = (row.get("evidence") or {}).get("class") or {}
        if ":usgs-b1544-table-1:" in row["observation_id"]:
            assert evidence.get("value") == "quoted_attributed"
        else:
            assert evidence.get("value") == "compilation_assessed"
        if (
            row["observation_id"].startswith(
                "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-corundum:delta_fG:"
            )
            and "T=298.15:" in row["observation_id"]
            and "from_the_elements" in row["observation_id"]
        ):
            corundum = row
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-quartz:S:shared:T="
        ):
            temp = ((row.get("identity") or {}).get("temperature_K") or {}).get("value")
            poly = (
                ((row.get("identity") or {}).get("species") or {}).get("polymorph") or {}
            ).get("value")
            if temp in {"700", "900"}:
                quartz_sides[str(temp)] = {"polymorph": poly, "phase": ((row.get("identity") or {}).get("species") or {}).get("phase")}

    assert corundum is not None
    species = (corundum.get("identity") or {}).get("species") or {}
    assert species.get("formula") == "Al2O3"
    assert species.get("formula") != "usgs-b1544-corundum"
    stored_polymorph = (species.get("polymorph") or {}).get("value")
    assert stored_polymorph == Polymorph.CORUNDUM.value
    assert coerce_polymorph_token("Corundum") is Polymorph.CORUNDUM
    assert coerce_polymorph_token("Corundum").value == stored_polymorph
    assert (
        observation_from_plain(corundum).identity.species.polymorph.value
        is Polymorph.CORUNDUM
    )
    assert (species.get("phase") or {}).get("value") == "cr"
    assert (corundum.get("identity") or {}).get("temperature_K", {}).get("value") == "298.15"
    assert (corundum.get("identity") or {}).get("reaction", {}).get("tag") == "value"
    assert corundum.get("value", {}).get("kind") == "point"
    assert Decimal(str(corundum["value"]["point"])) == Decimal("-1582.242")
    note = (corundum.get("locator") or {}).get("note") or ""
    assert "formation_basis=from_the_elements" in note

    assert quartz_sides["700"]["polymorph"] == "alpha"
    assert quartz_sides["900"]["polymorph"] == "beta"

    kyanite = next(
        row
        for row in rows
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-kyanite:delta_fG:from_the_elements:T=298.15:"
        )
    )
    oxides = next(
        row
        for row in rows
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-kyanite:delta_fG:from_the_oxides:T=298.15:"
        )
    )
    kya_obs = observation_from_plain(kyanite)
    ox_obs = observation_from_plain(oxides)
    cross = identity_equal(kya_obs.identity, ox_obs.identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "reaction" in cross.fields

    reconstructed = [
        row
        for row in rows
        if "as_published='199.3R'" in str((row.get("locator") or {}).get("note") or "")
    ]
    assert reconstructed
    assert Decimal(str(reconstructed[0]["value"]["point"])) == Decimal("199.38")

    hydrated = next(
        row
        for row in rows
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-alooh-reference:delta_fG:from_the_elements:T=298.15:"
        )
    )
    assert reaction_atom_balance(observation_from_plain(hydrated).identity.reaction.value) == {}


def _b1544_quartz_delta_fg(rows: list[dict], token: Polymorph) -> dict:
    return next(
        row
        for row in rows
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-quartz:delta_fG:from_the_elements:"
        )
        and ((row.get("identity") or {}).get("species") or {}).get("polymorph", {}).get(
            "value"
        )
        == token.value
    )


def test_store_identity_gap_vs_janaf_after_polymorph_closure() -> None:
    """Polymorph now agrees; identity_equal still cannot unify JANAF series vs B1544 points."""

    b1544_rows = _load_b1544_store_observations()
    corundum = next(
        row
        for row in b1544_rows
        if row["observation_id"].startswith(
            "hemingway-haas-robinson-1982-usgs-b1544:usgs-b1544-corundum:delta_fG:from_the_elements:T=298.15:"
        )
    )
    quartz_alpha = _b1544_quartz_delta_fg(b1544_rows, Polymorph.ALPHA)
    quartz_beta = _b1544_quartz_delta_fg(b1544_rows, Polymorph.BETA)

    janaf_al = _load_yaml(JANAF_STORE_DIR / "compilations-janaf" / "janaf-Al.yaml")
    janaf_o = _load_yaml(JANAF_STORE_DIR / "compilations-janaf" / "janaf-O.yaml")
    al096 = next(
        row
        for row in janaf_al["observations"]
        if row["observation_id"] == "nist-janaf-4th:Al-096:delta_fG:segment-0"
    )
    o037_alpha = next(
        row
        for row in janaf_o["observations"]
        if row["observation_id"] == "nist-janaf-4th:O-037:delta_fG:segment-0"
    )
    o037_beta = next(
        row
        for row in janaf_o["observations"]
        if row["observation_id"] == "nist-janaf-4th:O-037:delta_fG:segment-1"
    )

    b1544_al = observation_from_plain(corundum)
    b1544_alpha = observation_from_plain(quartz_alpha)
    b1544_beta = observation_from_plain(quartz_beta)
    janaf_al2o3 = observation_from_plain(al096)
    janaf_alpha = observation_from_plain(o037_alpha)
    janaf_beta = observation_from_plain(o037_beta)

    assert janaf_al2o3.identity.species.polymorph.value is Polymorph.CORUNDUM
    assert b1544_al.identity.species.polymorph.value is Polymorph.CORUNDUM
    assert janaf_alpha.identity.species.formula == b1544_alpha.identity.species.formula == "SiO2"
    assert janaf_alpha.identity.species.polymorph.value is Polymorph.ALPHA
    assert b1544_alpha.identity.species.polymorph.value is Polymorph.ALPHA
    assert janaf_beta.identity.species.polymorph.value is Polymorph.BETA
    assert b1544_beta.identity.species.polymorph.value is Polymorph.BETA

    remaining_unknown = ("temperature_K", "reaction", "formation_elements")
    al_gap = identity_equal(janaf_al2o3.identity, b1544_al.identity)
    sio2_alpha_gap = identity_equal(janaf_alpha.identity, b1544_alpha.identity)
    sio2_beta_gap = identity_equal(janaf_beta.identity, b1544_beta.identity)
    assert al_gap.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert al_gap.fields == remaining_unknown
    assert "species.polymorph" not in al_gap.fields
    assert sio2_alpha_gap.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert sio2_alpha_gap.fields == remaining_unknown
    assert "species.polymorph" not in sio2_alpha_gap.fields
    assert sio2_beta_gap.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert sio2_beta_gap.fields == remaining_unknown

    # Remaining gap: JANAF series leave T / reaction / formation_elements
    # unknown; B1544 stores a point with a filled formation reaction.
    # identity_equal has no series-vs-point comparison.
    assert janaf_al2o3.value.kind is ValueKind.SERIES
    assert b1544_al.value.kind is ValueKind.POINT
    assert janaf_al2o3.identity.temperature_K.is_unknown
    assert b1544_al.identity.temperature_K.is_value
    assert janaf_al2o3.identity.reaction.is_unknown
    assert b1544_al.identity.reaction.is_value
    assert janaf_al2o3.identity.formation_elements.is_unknown
    assert b1544_al.identity.formation_elements.is_value
