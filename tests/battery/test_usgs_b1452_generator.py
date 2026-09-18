"""USGS B1452 source-aware generator controls and negative witnesses."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    QUANTITY_UNITS,
    EvidenceClass,
    IdentityEqualKind,
    Phase,
    Quantity,
    UncertaintyKind,
)
from simulator.battery.generators import usgs_b1452 as generator
from simulator.battery.identity import (
    identity_equal,
    log10K_from_delta_fG_kJ_mol,
    quantity_token,
)

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
B1452_RAW = 55707
B1452_STORED = 10456
B1452_REFUSED = 24417
B1452_EXCLUDED = 20834
B1452_MERGED_PROPOSED = 347
B1452_MERGED_STORED = 160
B1452_MERGED_REFUSED = 121
B1452_MERGED_REFUSED_10X = 113
B1452_MERGED_EXCLUDED = 66
B1452_IDENTITY_10X = 315
SPINEL_HT = "robie-hemingway-fisher-1978-usgs-b1452-0236"
B1452_FORMULA_UNRESOLVED_HT = 219
B1452_FORMULA_UNRESOLVED_298K_ROWS = 273
TABLE_298K = "robie-hemingway-fisher-1978-usgs-b1452-0003"
TABLE1 = "robie-hemingway-fisher-1978-usgs-b1452-0001"
SILVER_HT = "robie-hemingway-fisher-1978-usgs-b1452-0004"
FAYALITE_ELEMENTS = "robie-hemingway-fisher-1978-usgs-b1452-0335"
FAYALITE_OXIDES = "robie-hemingway-fisher-1978-usgs-b1452-0336"


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
    formula: str | None = None,
    row: int | None = None,
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
        if formula is not None and observation.identity.species.formula != formula:
            continue
        if row is not None:
            suffix = f":row={row}:"
            if suffix not in observation.observation_id:
                continue
        result.append(observation)
    return result


def test_table1_states_r_and_joule_formation_units() -> None:
    record = _load(TABLE1)
    symbols = {
        row["cells"]["symbol"]["as_published"]: row["cells"]["definition"]["as_published"]
        for row in record["rows"]
    }
    assert "R" in symbols
    assert "8.3143" in symbols["R"]
    assert "J" in symbols["R"]
    assert "ΔHf" in symbols
    assert "J· mol-1" in symbols["ΔHf"] or "J·mol-1" in symbols["ΔHf"].replace(" ", "")
    assert "kJ" not in symbols["ΔHf"]
    assert "ΔGf" in symbols
    assert "kJ" not in symbols["ΔGf"]
    generated = _generation(TABLE1)
    assert generated.report["gas_constant_J_per_mol_K"] == "8.3143"
    assert "Table 1" in generated.report["gas_constant_basis"]
    assert generated.report["unit_row"]["table_298k_formation_unit"] == "J/mol"
    assert generated.report["unit_row"]["ht_formation_unit"] == "kJ/mol"


def test_gas_constant_is_table1_not_modern() -> None:
    from simulator.battery.identity import R_J_PER_MOL_K

    assert generator.B1452_R_J_PER_MOL_K == Decimal("8.3143")
    assert generator.B1452_R_J_PER_MOL_K != R_J_PER_MOL_K
    modern = log10K_from_delta_fG_kJ_mol(Decimal("-2441.276"), Decimal("298.15"))
    source = log10K_from_delta_fG_kJ_mol(
        Decimal("-2441.276"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1452_R_J_PER_MOL_K,
    )
    assert modern != source
    assert abs(source - Decimal("427.703")) < Decimal("0.01")


def test_page_header_units_ignore_captured_ocr_strings() -> None:
    record = _load(SILVER_HT)
    captured = record["units_as_published"]["formation_enthalpy"]
    assert captured != "kJ/mol"
    generated = _generation(SILVER_HT)
    enthalpy = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15"
    )[0]
    note = enthalpy.locator.note or ""
    assert "unit='kJ/mol'" in note
    assert "captured units_as_published ignored" in note
    assert captured not in note


def test_table_298k_formation_is_joules_not_kilojoules() -> None:
    """Kyanite 298 K ΔfH is −2591730 J·mol⁻¹ = −2591.730 kJ·mol⁻¹.

    Reading the 298 K table with the HT kJ header would store −2591730 kJ
    and the test would still look like a number. Fail that path.
    """

    assert generator.TABLE_298K_PAGE_UNITS["formation_enthalpy"] == "J/mol"
    assert generator.HT_PAGE_UNITS["formation_enthalpy"] == "kJ/mol"
    assert (
        generator.TABLE_298K_PAGE_UNITS["formation_enthalpy"]
        != generator.HT_PAGE_UNITS["formation_enthalpy"]
    )
    generated = _generation(TABLE_298K)
    kyanite = _observations_for(
        generated,
        Quantity.DELTA_FH,
        temperature="298.15",
        formula="Al2SiO5",
        row=660,
    )
    assert len(kyanite) == 1
    obs = kyanite[0]
    assert obs.value.point == Decimal("-2591.730")
    assert obs.value.point != Decimal("-2591730")
    assert QUANTITY_UNITS[Quantity.DELTA_FH] == "kJ_per_declared_mol_basis"
    assert obs.derivation.output_unit == QUANTITY_UNITS[Quantity.DELTA_FH]
    factor = dict(obs.derivation.parameters)["j_to_kj"]
    assert factor.state.value == Decimal("0.001")
    assert Decimal("-2591730") * factor.state.value == obs.value.point
    note = obs.locator.note or ""
    assert "unit='J/mol'" in note
    assert "Table 1" in note


def test_fayalite_298k_joules_match_ht_kilojoules() -> None:
    table = _generation(TABLE_298K)
    ht = _generation(FAYALITE_ELEMENTS)
    from_298k = _observations_for(
        table,
        Quantity.DELTA_FH,
        temperature="298.15",
        formula="Fe2SiO4",
        row=674,
    )
    from_ht = _observations_for(
        ht, Quantity.DELTA_FH, temperature="298.15", basis="from_the_elements"
    )
    assert len(from_298k) == 1
    assert len(from_ht) == 1
    assert from_298k[0].identity.species.formula == "Fe2SiO4"
    assert from_ht[0].identity.species.formula == "Fe2SiO4"
    assert from_298k[0].value.point == Decimal("-1479.360")
    assert from_ht[0].value.point == Decimal("-1479.360")
    ht_note = from_ht[0].locator.note or ""
    table_note = from_298k[0].locator.note or ""
    assert "unit='kJ/mol'" in ht_note
    assert "unit='J/mol'" in table_note
    assert "kj_to_j" in dict(from_ht[0].derivation.parameters)
    assert "j_to_kj" in dict(from_298k[0].derivation.parameters)


def test_ocr_name_is_not_a_formula() -> None:
    fayalite = _generation(FAYALITE_ELEMENTS)
    resolution = fayalite.report["formula_resolution"]
    assert resolution["formula"] == "Fe2SiO4"
    assert resolution["source"] == "formula_weight"
    assert "title" not in (resolution["source"] or "")
    formulas = {obs.identity.species.formula for obs in fayalite.observations}
    assert formulas == {"Fe2SiO4"}
    assert "Flyau" not in formulas

    forsterite = generator._resolve_formula(_load("robie-hemingway-fisher-1978-usgs-b1452-0337"))
    assert forsterite.formula == "Mg2SiO4"
    assert forsterite.source == "formula_weight"
    assert forsterite.formula != "Pobst"

    sulfur = _generation("robie-hemingway-fisher-1978-usgs-b1452-0069")
    assert sulfur.report["formula_resolution"]["formula"] == "S8"
    sulfur_formulas = {obs.identity.species.formula for obs in sulfur.observations}
    assert sulfur_formulas == {"S8"}
    assert "S1" not in sulfur_formulas


def test_title_case_is_not_a_formula_source() -> None:
    source = Path(generator.__file__).read_text(encoding="utf-8")
    assert "key.title()" not in source
    quartz = generator._resolve_formula(_load("robie-hemingway-fisher-1978-usgs-b1452-0190"))
    assert quartz.formula is None
    assert quartz.reason is not None
    assert generator.FORMULA_UNRESOLVED_REASON_PREFIX in quartz.reason
    assert "formula_as_published=None" in quartz.reason
    assert "formula_weight='60.085'" in quartz.reason
    assert "name_key='QUARTZ'" in quartz.reason
    generated = _generation("robie-hemingway-fisher-1978-usgs-b1452-0190")
    assert generated.report["formula_resolution"]["unresolved"] is True
    assert generated.observations == ()
    assert "Quartz" not in {
        obs.identity.species.formula for obs in generated.observations
    }


def test_unresolved_formula_refuses_and_names_consulted_fields() -> None:
    generated = _generation(TABLE_298K)
    quartz = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 401
        and generator.FORMULA_UNRESOLVED_REASON_PREFIX in row["reason"]
    ]
    assert quartz
    reason = quartz[0]["reason"]
    assert "formula_weight='60.085'" in reason
    assert "name_key='QUARTZ'" in reason
    stored = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Quartz", row=401
    )
    assert stored == []


def test_silver_400k_gibbs_function_worked_row() -> None:
    generated = _generation(SILVER_HT)
    entropy = _observations_for(generated, Quantity.S, temperature="400")
    assert len(entropy) == 1
    assert entropy[0].value.point == Decimal("50.08")
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "planck_vs_S_minus_HHT"
        and row.get("temperature_as_published") in {"400", "400.0"}
    ]
    assert results
    assert results[0]["ok"] is True
    assert Decimal(results[0]["calculated"]) == Decimal("43.55")


def test_kyanite_298k_logkf_identity_uses_joule_delta_g() -> None:
    generated = _generation(TABLE_298K)
    gibbs = _observations_for(
        generated,
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Al2SiO5",
        row=660,
    )
    logk = _observations_for(
        generated,
        Quantity.LOG10_KF,
        temperature="298.15",
        formula="Al2SiO5",
        row=660,
    )
    assert len(gibbs) == 1
    assert len(logk) == 1
    assert gibbs[0].value.point == Decimal("-2441.276")
    assert logk[0].value.point == Decimal("427.703")
    calculated = log10K_from_delta_fG_kJ_mol(
        Decimal("-2441.276"),
        Decimal("298.15"),
        gas_constant_J_per_mol_K=generator.B1452_R_J_PER_MOL_K,
    )
    assert abs(calculated - Decimal("427.703")) < Decimal("0.01")
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "log10_Kf_from_delta_fG" and row.get("row_index") == 660
    ]
    assert results
    assert results[0]["gibbs_page_unit"] == "J/mol"
    assert results[0]["ok"] is True


def test_merged_two_dot_entropy_splits_on_grain() -> None:
    generated = _generation(TABLE_298K)
    silver = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=0
    )
    assert len(silver) == 1
    assert silver[0].value.point == Decimal("42.55")
    assert silver[0].uncertainty.kind is UncertaintyKind.PRINTED
    assert silver[0].uncertainty.verbatim == "0.21"
    assert silver[0].evidence.original_method_class == generator.MERGED_SPLIT_METHOD
    splits = generated.report["merged_cell_splits"]
    assert splits["proposed"] >= 1
    assert splits["stored"] >= 1
    assert splits["proposed"] == splits["stored"] + splits["refused"] + splits["excluded"]


def test_spinel_extra_dot_split_is_proposed_then_10x_refused() -> None:
    """Grain uniquely cuts Spinel 1800 K gef 2.29.48; the page is 229.482.

    S 374.85 − (H−H298)/T 145.368 = 229.482. The 10× Gibbs net refuses the
    wrong cut. That proposal must not count as stored.
    """

    generated = _generation(SPINEL_HT)
    splits = generated.report["merged_cell_splits"]
    gef = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "2.29.48" and row["column"] == "negative_gibbs_function"
    ]
    assert gef
    assert "10×" in gef[0]["reason"]
    stored_merged = [
        obs
        for obs in generated.observations
        if obs.evidence.original_method_class == generator.MERGED_SPLIT_METHOD
        and "2.29" in (obs.locator.note or "")
    ]
    assert stored_merged == []
    assert splits["proposed"] == splits["stored"] + splits["refused"] + splits["excluded"]
    assert splits["refused"] >= 1
    assert splits["refused_10x"] >= 1
    assert splits["stored"] == splits["proposed"] - splits["refused"] - splits["excluded"]


def test_merged_integer_without_unique_grain_split_is_refused() -> None:
    generated = _generation(TABLE_298K)
    ag_plus_h = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "10575085" and row["row_index"] == 1
    ]
    ag_plus_g = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "77077100" and row["row_index"] == 1
    ]
    assert ag_plus_h or ag_plus_g
    stored_h = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15", formula="Ag", row=1
    )
    stored_g = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Ag", row=1
    )
    assert stored_h == []
    assert stored_g == []
    for observation in (*stored_h, *stored_g):
        assert observation.value.point != Decimal("10575.085")
        assert observation.value.point != Decimal("10575085")


def test_corundum_298k_jammed_formation_refused_by_10x_or_merge() -> None:
    generated = _generation(TABLE_298K)
    stored_h = _observations_for(
        generated, Quantity.DELTA_FH, temperature="298.15", formula="Al2O3", row=261
    )
    stored_g = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2O3", row=261
    )
    assert stored_h == []
    assert stored_g == []
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 261
        and row["column"] in {"formation_enthalpy", "formation_gibbs_energy", "log_kf"}
    ]
    assert refused
    reasons = " ".join(row["reason"] for row in refused)
    assert "10×" in reasons or "merged" in reasons or "identity" in reasons


def test_neighbour_sign_disabled_on_298k_table() -> None:
    generated = _generation(TABLE_298K)
    assert generated.report["neighbour_sign"]["disabled_on_298k"] is True
    assert generated.report["neighbour_sign"]["applied"] is False
    assert generated.report["neighbour_sign_hits"] == []
    assert "garbage" in generated.report["neighbour_sign"]["reason"]
    assert "298.15" in generated.report["neighbour_sign"]["reason"]


def test_neighbour_sign_refuses_ht_seeded_dropped_minus() -> None:
    payload = _load(FAYALITE_OXIDES)
    original = payload["rows"][4]["cells"]["formation_enthalpy"]["as_published"]
    payload["rows"][4]["cells"]["formation_enthalpy"]["as_published"] = original.replace(
        "-", ""
    )
    payload["rows"][4]["cells"]["formation_enthalpy"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 4 and row["column"] == "formation_enthalpy"
    ]
    assert refused
    assert "neighbour-sign" in refused[0]["reason"]
    control = _generation(FAYALITE_OXIDES)
    stored = [
        obs
        for obs in control.observations
        if quantity_token(obs.identity) is Quantity.DELTA_FG
        and ":row=3:" in obs.observation_id
    ]
    assert stored


def test_10x_planck_refuses_participating_cells_before_vocab_exclusion() -> None:
    payload = _load(SILVER_HT)
    payload["rows"][1]["cells"]["negative_gibbs_function"]["as_published"] = "435.5"
    payload["rows"][1]["cells"]["negative_gibbs_function"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused_cols = {
        row["column"]
        for row in generated.report["refusals"]
        if row["row_index"] == 1
        and "10×" in row["reason"]
    }
    assert "negative_gibbs_function" in refused_cols
    assert "entropy" in refused_cols
    assert "enthalpy_function" in refused_cols
    stored_s = _observations_for(generated, Quantity.S, temperature="400")
    assert stored_s == []
    control = _generation(SILVER_HT)
    assert _observations_for(control, Quantity.S, temperature="400")


def test_10x_logkf_refuses_seeded_residual() -> None:
    payload = _load(TABLE_298K)
    payload["rows"][660]["cells"]["formation_gibbs_energy"]["as_published"] = "-24412760"
    payload["rows"][660]["cells"]["formation_gibbs_energy"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["row_index"] == 660
        and row["column"] in {"formation_gibbs_energy", "log_kf"}
        and "10×" in row["reason"]
    ]
    assert {row["column"] for row in refused} >= {"formation_gibbs_energy", "log_kf"}
    control = _generation(TABLE_298K)
    stored = _observations_for(
        control,
        Quantity.LOG10_KF,
        temperature="298.15",
        formula="Al2SiO5",
        row=660,
    )
    assert stored
    assert stored[0].value.point == Decimal("427.703")


def test_ocr_suspect_without_image_correction_is_refused() -> None:
    generated = _generation(SILVER_HT)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] in {"o.uoo", "o.oou"}
    ]
    assert {row["as_published"] for row in refused} >= {"o.uoo", "o.oou"}
    entropy = _observations_for(generated, Quantity.S, temperature="298.15")[0]
    assert entropy.value.point == Decimal("42.55")


def test_oxide_asterisk_is_basis_not_damage() -> None:
    generated = _generation(FAYALITE_OXIDES)
    gibbs = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", basis="from_the_oxides"
    )
    assert len(gibbs) == 1
    assert gibbs[0].value.point == Decimal("-20.775")
    note = gibbs[0].locator.note or ""
    assert "as_published='-20.775 *'" in note or "as_published='-20.775*'" in note.replace(" ", "")
    assert "formation_basis=from_the_oxides" in note
    refused_star = [
        row
        for row in generated.report["refusals"]
        if "*" in row["as_published"] and row["column"] == "formation_gibbs_energy"
    ]
    assert refused_star == []


def test_formation_basis_is_identity_axis() -> None:
    elements = _observations_for(
        _generation(TABLE_298K),
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Fe2SiO4",
        row=674,
    )
    oxides = _observations_for(
        _generation(FAYALITE_OXIDES),
        Quantity.DELTA_FG,
        temperature="298.15",
        basis="from_the_oxides",
    )
    assert len(elements) == 1
    assert len(oxides) == 1
    assert elements[0].value.point == Decimal("-1379.375")
    assert oxides[0].value.point == Decimal("-20.775")
    assert elements[0].identity.reaction is not None
    assert oxides[0].identity.reaction is not None
    assert elements[0].identity.reaction.is_value
    assert oxides[0].identity.reaction.is_value
    assert elements[0].identity.reaction.value != oxides[0].identity.reaction.value
    kyanite = _observations_for(
        _generation(TABLE_298K),
        Quantity.DELTA_FG,
        temperature="298.15",
        formula="Al2SiO5",
        row=660,
    )[0]
    assert identity_equal(kyanite.identity, kyanite.identity).kind is IdentityEqualKind.EQUAL


def test_al2sio5_polymorphs_are_compared_identity_values() -> None:
    generated = _generation(TABLE_298K)
    kyanite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", row=660
    )[0]
    andalusite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", row=661
    )[0]
    sillimanite = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al2SiO5", row=662
    )[0]
    assert kyanite.identity.species.polymorph.value == "kyanite"
    assert andalusite.identity.species.polymorph.value == "andalusite"
    assert sillimanite.identity.species.polymorph.value == "sillimanite"
    cross = identity_equal(kyanite.identity, andalusite.identity)
    assert cross.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "species.polymorph" in cross.fields
    assert identity_equal(kyanite.identity, kyanite.identity).kind is IdentityEqualKind.EQUAL


def test_aqueous_ion_has_charge_and_aq_phase() -> None:
    generated = _generation(TABLE_298K)
    entropy = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=1
    )
    assert len(entropy) == 1
    species = entropy[0].identity.species
    assert species.phase.value is Phase.AQ
    assert species.charge is not None
    assert species.charge.is_value
    assert species.charge.value == 1
    assert species.polymorph is not None
    assert species.polymorph.is_not_applicable


def test_cell_accounting_mutation_drops_one_token() -> None:
    payload = _load(SILVER_HT)
    generated = generator.generate_record(payload)
    accounting = generated.report["cell_accounting"]
    raw = accounting["raw_numeric_tokens"]
    assert (
        accounting["stored"] + accounting["refused"] + accounting["excluded"] == raw
    )
    del payload["rows"][0]["cells"]["entropy"]
    mutated = generator.generate_record(payload)
    assert mutated.report["cell_accounting"]["raw_numeric_tokens"] == raw - 1


def test_record_accounting_closes() -> None:
    for record_id in (TABLE1, TABLE_298K, SILVER_HT, FAYALITE_ELEMENTS, FAYALITE_OXIDES):
        generated = _generation(record_id)
        accounting = generated.report["cell_accounting"]
        assert accounting["unexplained"] == 0
        assert (
            accounting["stored"] + accounting["refused"] + accounting["excluded"]
            == accounting["raw_numeric_tokens"]
        )


def test_captured_units_never_copied_onto_observations() -> None:
    generated = _generation(SILVER_HT)
    damaged = ("J/aol", "kJ/1101", "Jjaol", "llol")
    for observation in generated.observations:
        note = observation.locator.note or ""
        for fragment in damaged:
            assert fragment not in note


def test_migrate_is_not_wired() -> None:
    root = Path(__file__).resolve().parents[2]
    migrate = (root / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    assert "usgs_b1452" not in migrate
    assert "_lift_b1452" not in migrate


def test_full_census_closes() -> None:
    raw = stored = refused = excluded = 0
    merged_proposed = merged_stored = merged_refused = merged_refused_10x = merged_excluded = 0
    identity_10x = 0
    identity_fail = 0
    neighbour_298k_hits = 0
    formula_unresolved_ht = 0
    formula_unresolved_rows = 0
    quantity_counts: dict[str, int] = {}
    n_records = 0
    for path in sorted(RECORDS_DIR.glob("*.json")):
        generated = generator.generate_record(json.loads(path.read_text(encoding="utf-8")))
        n_records += 1
        accounting = generated.report["cell_accounting"]
        raw += int(accounting["raw_numeric_tokens"])
        stored += int(accounting["stored"])
        refused += int(accounting["refused"])
        excluded += int(accounting["excluded"])
        splits = generated.report["merged_cell_splits"]
        merged_proposed += int(splits["proposed"])
        merged_stored += int(splits["stored"])
        merged_refused += int(splits["refused"])
        merged_refused_10x += int(splits["refused_10x"])
        merged_excluded += int(splits["excluded"])
        assert (
            int(splits["proposed"])
            == int(splits["stored"]) + int(splits["refused"]) + int(splits["excluded"])
        )
        resolution = generated.report.get("formula_resolution") or {}
        if generated.report["table_kind"] == "table_298k":
            neighbour_298k_hits += len(generated.report["neighbour_sign_hits"])
            formula_unresolved_rows += int(resolution.get("rows_unresolved") or 0)
        elif resolution.get("unresolved"):
            formula_unresolved_ht += 1
        for observation in generated.observations:
            token = quantity_token(observation.identity)
            if token is None:
                continue
            quantity_counts[token.value] = quantity_counts.get(token.value, 0) + 1
            assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        refused_keys = {
            (row["row_index"], row["column"]) for row in generated.report["refusals"]
        }
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                identity_fail += 1
                residual = Decimal(str(result.get("absolute_residual") or "0"))
                tolerance = Decimal(str(result.get("rounding_tolerance") or "1"))
                if tolerance > 0 and residual > 10 * tolerance:
                    identity_10x += 1
                    row_index = result.get("row_index")
                    if result.get("identity") == "log10_Kf_from_delta_fG":
                        cols = ("log_kf", "formation_gibbs_energy")
                    else:
                        cols = ("negative_gibbs_function", "entropy", "enthalpy_function")
                    for column in cols:
                        assert (row_index, column) in refused_keys, (
                            generated.report["record_id"],
                            row_index,
                            column,
                            result.get("identity"),
                        )
    assert n_records == 606
    assert stored + refused + excluded == raw
    assert raw == B1452_RAW
    assert stored == B1452_STORED
    assert refused == B1452_REFUSED
    assert excluded == B1452_EXCLUDED
    assert stored + refused + excluded == B1452_RAW
    assert neighbour_298k_hits == 0
    assert merged_proposed == B1452_MERGED_PROPOSED
    assert merged_stored == B1452_MERGED_STORED
    assert merged_refused == B1452_MERGED_REFUSED
    assert merged_refused_10x == B1452_MERGED_REFUSED_10X
    assert merged_excluded == B1452_MERGED_EXCLUDED
    assert merged_proposed == merged_stored + merged_refused + merged_excluded
    assert identity_10x == B1452_IDENTITY_10X
    assert formula_unresolved_ht == B1452_FORMULA_UNRESOLVED_HT
    assert formula_unresolved_rows == B1452_FORMULA_UNRESOLVED_298K_ROWS
    print(
        "B1452_CENSUS",
        {
            "raw": raw,
            "stored": stored,
            "refused": refused,
            "excluded": excluded,
            "unexplained": 0,
            "merged_proposed": merged_proposed,
            "merged_stored": merged_stored,
            "merged_refused": merged_refused,
            "merged_refused_10x": merged_refused_10x,
            "merged_excluded": merged_excluded,
            "identity_fail": identity_fail,
            "identity_10x": identity_10x,
            "formula_unresolved_ht": formula_unresolved_ht,
            "formula_unresolved_298k_rows": formula_unresolved_rows,
            "quantities": quantity_counts,
        },
    )
    assert stored > 0
    assert refused > 0
    assert "delta_fH" in quantity_counts
    assert "delta_fG" in quantity_counts
    assert "log10_Kf" in quantity_counts
