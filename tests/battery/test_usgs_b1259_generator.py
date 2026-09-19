"""USGS B1259 source-aware generator controls and negative witnesses."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    QUANTITY_UNITS,
    EvidenceClass,
    Phase,
    Quantity,
)
from simulator.battery.generators import usgs_b1259 as generator
from simulator.battery.generators.usgs_b1259 import RawToken
from simulator.battery.identity import (
    THERMOCHEMICAL_CALORIE_J,
    quantity_token,
)

from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    COMPILATION_ROOT,
)

RECORDS_DIR = COMPILATION_ROOT / "records"
B1259_RAW = 29809
B1259_STORED = 13072
B1259_REFUSED = 4807
B1259_EXCLUDED = 11930
B1259_MERGED_PROPOSED = 4
B1259_MERGED_STORED = 2
B1259_MERGED_REFUSED = 2
B1259_MERGED_REFUSED_10X = 1
B1259_MERGED_EXCLUDED = 0
B1259_UNGUARDED_MERGED_STORED = 2
B1259_IDENTITY_10X = 118
B1259_IDENTITY_1X_10X = 27
B1259_FORMULA_UNRESOLVED_HT = 43
B1259_FORMULA_UNRESOLVED_298K_ROWS = 88
SILVER_298K = "b1259-298k-0001-silver"
AG_ION_298K = "b1259-298k-0002-ag-aqueous-ion"
AL_ION_298K = "b1259-298k-0004-al-aqueous-ion"
ACANTHITE_298K = "b1259-298k-0093-acanthite"
SILVER_HT = "b1259-ht-0001-silver-reference-state"
TABLE1 = "b1259-table-01-symbols"
UNTRANSCRIBED = "b1259-ht-0094-untranscribed"


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
        if formula is not None and observation.identity.species.formula != formula:
            continue
        if row is not None:
            suffix = f":row={row}:"
            if suffix not in observation.observation_id:
                continue
        result.append(observation)
    return result


def test_table1_states_calorie_r_and_joule_companion() -> None:
    record = _load(TABLE1)
    symbols = {
        row["symbol_as_published"]: row["definition_as_published"]
        for row in record["rows"]
    }
    assert "R" in symbols
    assert "1.98717" in symbols["R"]
    assert "8.31469" in symbols["R"]
    generated = _generation(TABLE1)
    assert generated.report["gas_constant_cal_per_mol_K"] == "1.98717"
    assert "1.98717" in generated.report["gas_constant_basis"]
    assert generated.report["gas_constant_J_printed_companion"] == "8.31469"
    assert generated.report["unit_row"]["table_298k_formation_unit"] == "cal gfw^-1"
    assert generated.report["unit_row"]["ht_formation_unit"] == "kcal gfw^-1"


def test_gas_constant_is_table1_calorie_not_modern() -> None:
    from simulator.battery.identity import R_J_PER_MOL_K

    assert generator.B1259_R_CAL == Decimal("1.98717")
    assert generator.B1259_R_J_FROM_CAL != R_J_PER_MOL_K
    assert generator.B1259_R_J_FROM_CAL != generator.B1259_R_J_PRINTED


def test_silver_298k_entropy_stays_calories_and_derives_si() -> None:
    generated = _generation(SILVER_298K)
    entropy = _observations_for(
        generated, Quantity.S, temperature="298.15", formula="Ag", row=0
    )
    assert len(entropy) == 1
    obs = entropy[0]
    assert "as_published='10.20'" in (obs.locator.note or "")
    assert "unit='cal deg^-1 gfw^-1'" in (obs.locator.note or "")
    assert obs.value.point == Decimal("10.20") * THERMOCHEMICAL_CALORIE_J
    assert obs.value.point == Decimal("42.67680")
    factor = dict(obs.derivation.parameters)["cal_th_to_J"]
    assert factor.state.value == Decimal("4.184")
    assert QUANTITY_UNITS[Quantity.S] == "J_per_declared_mol_basis_per_K"
    assert obs.derivation.output_unit == QUANTITY_UNITS[Quantity.S]


def test_standard_pressure_is_one_atmosphere() -> None:
    generated = _generation(SILVER_298K)
    obs = generated.observations[0]
    assert obs.identity.standard_pressure_Pa.value == Decimal("101325")
    assert "1 atm" in (obs.locator.note or "")
    assert generated.report["standard_pressure"]["standard_pressure_Pa"] == "101325"


def test_silver_400k_gibbs_function_sign_convention() -> None:
    generated = _generation(SILVER_HT)
    entropy = _observations_for(generated, Quantity.S, temperature="400", formula="Ag")
    enthalpy = _observations_for(
        generated, Quantity.H_MINUS_H298, temperature="400", formula="Ag"
    )
    assert len(entropy) == 1
    assert len(enthalpy) == 1
    assert "as_published='12.010'" in (entropy[0].locator.note or "")
    assert entropy[0].value.point == Decimal("12.010") * THERMOCHEMICAL_CALORIE_J
    assert enthalpy[0].value.point == Decimal("0.625") * THERMOCHEMICAL_CALORIE_J
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "planck_vs_S_minus_1000H_over_T"
        and row.get("temperature_as_published") in {"400", "400.0"}
    ]
    assert results
    assert results[0]["ok"] is True
    assert results[0]["printed"] == "10.447"
    assert "-(G_T-H_298)/T" in results[0]["sign_convention"]
    assert Decimal(results[0]["calculated"]) == Decimal("10.4475")
    gef = [
        row
        for row in generated.report["exclusions"]
        if row["as_published"] == "10.447" and row["column"] == "gibbs_function"
    ]
    assert gef
    assert "-(G_T-H_298)/T" in gef[0]["reason"]
    assert generated.report["gibbs_function_sign"]["printed_column"] == "-(G_T-H_298)/T"


def test_ag_ion_logkf_identity_uses_calorie_r() -> None:
    generated = _generation(AG_ION_298K)
    gibbs = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Ag"
    )
    logk = _observations_for(
        generated, Quantity.LOG10_KF, temperature="298.15", formula="Ag"
    )
    assert len(gibbs) == 1
    assert len(logk) == 1
    assert "as_published='18433'" in (gibbs[0].locator.note or "")
    assert gibbs[0].value.point == Decimal("18433") * THERMOCHEMICAL_CALORIE_J / Decimal(
        "1000"
    )
    assert logk[0].value.point == Decimal("-13.512")
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "log10_Kf_from_delta_fG"
    ]
    assert results
    assert results[0]["ok"] is True
    assert results[0]["gibbs_page_unit"] == "cal/gfw"
    assert results[0]["gas_constant_cal_per_mol_K"] == "1.98717"
    assert results[0]["reconstructed_gibbs"] == "18433"


def test_title_case_is_not_a_formula_source() -> None:
    source = Path(generator.__file__).read_text(encoding="utf-8")
    assert "key.title()" not in source
    generated = _generation(ACANTHITE_298K)
    resolution = generated.report["formula_resolution"]
    assert resolution["unresolved"] is True
    assert generator.FORMULA_UNRESOLVED_REASON_PREFIX in resolution["reason"]
    assert "formula_as_published=None" in resolution["reason"]
    assert "name_as_published='Acanthite'" in resolution["reason"]
    assert generated.observations == ()
    assert "Acanthite" not in {
        obs.identity.species.formula for obs in generated.observations
    }


def test_unresolved_formula_refuses_and_names_consulted_fields() -> None:
    generated = _generation(ACANTHITE_298K)
    refused = [
        row
        for row in generated.report["refusals"]
        if generator.FORMULA_UNRESOLVED_REASON_PREFIX in row["reason"]
    ]
    assert refused
    reason = refused[0]["reason"]
    assert "formula_as_published=None" in reason
    assert "name_as_published='Acanthite'" in reason
    assert "gram_formula_weight='247.804'" in reason


def test_ocr_name_is_not_a_formula() -> None:
    generated = _generation(SILVER_HT)
    resolution = generated.report["formula_resolution"]
    assert resolution["formula"] == "Ag"
    assert resolution["source"] == "printed_formula"
    formulas = {obs.identity.species.formula for obs in generated.observations}
    assert formulas == {"Ag"}
    assert "Silver" not in formulas
    assert "SILVER" not in formulas


def test_al_aqueous_ion_has_printed_charge_and_aq_phase() -> None:
    generated = _generation(AL_ION_298K)
    gibbs = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Al"
    )
    assert len(gibbs) == 1
    species = gibbs[0].identity.species
    assert species.phase.value is Phase.AQ
    assert species.charge is not None
    assert species.charge.is_value
    assert species.charge.value == 3
    assert species.polymorph is not None
    assert species.polymorph.is_not_applicable


def test_ag_aqueous_ion_does_not_invent_charge() -> None:
    generated = _generation(AG_ION_298K)
    gibbs = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Ag"
    )
    assert len(gibbs) == 1
    species = gibbs[0].identity.species
    assert species.phase.value is Phase.AQ
    assert species.charge is not None
    assert species.charge.is_unknown
    assert "formula_as_published='Ag'" in (species.charge.reason or "")


def test_identity_on_reconstructed_jammed_gibbs_passes() -> None:
    """SC-271: identity consumes the reconstructed split, not the raw jammed token.

    Ag+(aq) printed ΔfG 18433 cal glued to 18433100. Reconstructing first
    restores log Kf identity; using the raw integer would fail and discard
    the recoverable row.
    """

    payload = _load(AG_ION_298K)
    payload["delta_f_G"]["as_published"] = "18433100"
    payload["delta_f_G"]["ocr_suspect"] = False
    generated = generator.generate_record(payload)
    results = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "log10_Kf_from_delta_fG"
    ]
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["reconstructed_gibbs"] == "18433"
    stored = _observations_for(
        generated, Quantity.DELTA_FG, temperature="298.15", formula="Ag"
    )
    assert len(stored) == 1
    assert stored[0].value.point == Decimal("18433") * THERMOCHEMICAL_CALORIE_J / Decimal(
        "1000"
    )
    assert stored[0].value.point != Decimal("18433100") * THERMOCHEMICAL_CALORIE_J / Decimal(
        "1000"
    )
    assert "as_published='18433100'" in (stored[0].locator.note or "")
    assert stored[0].evidence.original_method_class == generator.MERGED_SPLIT_METHOD
    splits = generated.report["merged_cell_splits"]
    assert splits["proposed"] == 1
    assert splits["stored"] == 1
    assert splits["refused"] == 0


def test_wrong_grain_split_still_fails_identity() -> None:
    """SC-271 converse: a unique grain split that is the wrong number still fails.

    Spinel-class: gef token 2.29.48 uniquely cuts at 0.01 grain to 2.29 ± 0.48
    against printed −(G−H298)/T = 229.482 from S − 1000(H−H298)/T.
    """

    tokens = [
        RawToken(
            "synthetic",
            "p",
            value,
            False,
            (),
            "gibbs_function",
            None,
            index,
            "high_temperature",
            "value",
        )
        for index, value in enumerate(
            ("10.44", "10.45", "10.46", "11.12", "11.13", "11.14", "2.29.48")
        )
    ]
    grains = generator._column_grain_map(tokens)
    jammed = tokens[-1]
    value, reconstruction, reason = generator._candidate_value(jammed, grains)
    assert reason is None
    assert value == Decimal("2.29")
    assert reconstruction is not None
    assert reconstruction["method_class"] == generator.MERGED_SPLIT_METHOD
    residual, tolerance, calculated = generator._gibbs_function_identity(
        "374.85", "261.6624", "1800", format(value, "f")
    )
    assert calculated == Decimal("229.4820")
    assert residual > 10 * tolerance
    identity_on_raw = generator._published_decimal(jammed.as_published)
    assert identity_on_raw is None


def test_wrong_split_is_proposed_then_10x_refused() -> None:
    payload = {
        "record_id": "b1259-synthetic-wrong-split",
        "table_kind": "high_temperature",
        "page": 1,
        "pdf_page": 1,
        "name_as_published": "SPINEL",
        "formula_as_published": "MgAl2O4",
        "phase": "Crystals",
        "gram_formula_weight": {
            "as_published": "142.266",
            "ocr_suspect": False,
            "footnote_markers": [],
        },
        "rows": [],
    }
    for index, gef in enumerate(
        ("229.48", "229.49", "229.50", "229.51", "229.52", "2.29.48")
    ):
        payload["rows"].append(
            {
                "kind": "data",
                "temperature": {
                    "as_published": "1800",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "H_minus_H298": {
                    "as_published": "261.6624",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "entropy": {
                    "as_published": "374.85",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "gibbs_function": {
                    "as_published": gef,
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "delta_f_H": {
                    "as_published": "0.000",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "delta_f_G": {
                    "as_published": "0.000",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
                "log_Kf": {
                    "as_published": "0.000",
                    "ocr_suspect": False,
                    "footnote_markers": [],
                },
            }
        )
    generated = generator.generate_record(payload)
    splits = generated.report["merged_cell_splits"]
    gef = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "2.29.48" and row["column"] == "gibbs_function"
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
    identity = [
        row
        for row in generated.report["identity_results"]
        if row.get("identity") == "planck_vs_S_minus_1000H_over_T"
        and row.get("printed") == "2.29.48"
    ]
    assert identity
    assert identity[0]["ok"] is False
    assert identity[0]["reconstructed_gef"] == "2.29"
    assert Decimal(identity[0]["calculated"]) == Decimal("229.4820")
    assert Decimal(identity[0]["absolute_residual"]) > 10 * Decimal(
        identity[0]["rounding_tolerance"]
    )


def test_ocr_suspect_without_image_correction_is_refused() -> None:
    generated = _generation(SILVER_HT)
    refused = [
        row
        for row in generated.report["refusals"]
        if row["as_published"] == "10.2CO"
    ]
    assert refused
    assert "OCR-suspect" in refused[0]["reason"]
    assert "10×" not in refused[0]["reason"]


def test_cell_accounting_mutation_drops_one_token() -> None:
    payload = _load(SILVER_298K)
    generated = generator.generate_record(payload)
    accounting = generated.report["cell_accounting"]
    raw = accounting["raw_numeric_tokens"]
    assert (
        accounting["stored"] + accounting["refused"] + accounting["excluded"] == raw
    )
    del payload["entropy"]
    mutated = generator.generate_record(payload)
    assert mutated.report["cell_accounting"]["raw_numeric_tokens"] == raw - 1


def test_record_accounting_closes() -> None:
    for record_id in (TABLE1, SILVER_298K, SILVER_HT, AG_ION_298K, UNTRANSCRIBED):
        generated = _generation(record_id)
        accounting = generated.report["cell_accounting"]
        assert accounting["unexplained"] == 0
        assert (
            accounting["stored"] + accounting["refused"] + accounting["excluded"]
            == accounting["raw_numeric_tokens"]
        )


def test_captured_units_never_copied_onto_observations() -> None:
    generated = _generation(SILVER_HT)
    for observation in generated.observations:
        note = observation.locator.note or ""
        assert "kcal gfw^-1  [printed" not in note
        assert "captured units_as_published ignored" in note


def test_unguarded_merged_is_computed_not_aliased() -> None:
    source = Path(generator.__file__).read_text(encoding="utf-8")
    assert '"unguarded_stored": merged_stored' not in source
    assert '"unguarded_stored": merged_stored_total' not in source
    generated = _generation("b1259-ht-0165-sodaniter")
    splits = generated.report["merged_cell_splits"]
    assert splits["unguarded_stored"] == sum(
        int(count) for count in (splits["unguarded_by_quantity"] or {}).values()
    )
    assert splits["unguarded_stored"] <= splits["stored"]


def test_migrate_is_not_wired() -> None:
    root = Path(__file__).resolve().parents[2]
    migrate = (root / "simulator" / "battery" / "migrate.py").read_text(encoding="utf-8")
    assert "usgs_b1259" not in migrate
    assert "_lift_b1259" not in migrate


def test_full_census_closes() -> None:
    raw = stored = refused = excluded = 0
    merged_proposed = merged_stored = merged_refused = merged_refused_10x = merged_excluded = 0
    unguarded_total = 0
    unguarded_by_quantity: dict[str, int] = {}
    identity_10x = 0
    identity_1x_10x = 0
    identity_band = 0
    formula_unresolved_ht = 0
    formula_unresolved_rows = 0
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
        unguarded_total += int(splits["unguarded_stored"])
        for quantity, count in (splits.get("unguarded_by_quantity") or {}).items():
            unguarded_by_quantity[str(quantity)] = unguarded_by_quantity.get(
                str(quantity), 0
            ) + int(count)
        assert int(splits["unguarded_stored"]) <= int(splits["stored"])
        assert (
            int(splits["proposed"])
            == int(splits["stored"]) + int(splits["refused"]) + int(splits["excluded"])
        )
        resolution = generated.report.get("formula_resolution") or {}
        if generated.report["table_kind"] == "properties_298k":
            formula_unresolved_rows += int(resolution.get("rows_unresolved") or 0)
        elif resolution.get("unresolved"):
            formula_unresolved_ht += 1
        for observation in generated.observations:
            assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        refused_keys = {
            (row["row_index"], row["column"]) for row in generated.report["refusals"]
        }
        for result in generated.report["identity_results"]:
            if result.get("ok") is False:
                residual = Decimal(str(result.get("absolute_residual") or "0"))
                tolerance = Decimal(str(result.get("rounding_tolerance") or "1"))
                if tolerance > 0 and residual > 10 * tolerance:
                    identity_10x += 1
                    row_index = result.get("row_index")
                    if result.get("identity") == "log10_Kf_from_delta_fG":
                        cols = ("log_Kf", "delta_f_G")
                    else:
                        cols = ("gibbs_function", "entropy", "H_minus_H298")
                    for column in cols:
                        assert (row_index, column) in refused_keys, (
                            generated.report["record_id"],
                            row_index,
                            column,
                            result.get("identity"),
                        )
                elif tolerance > 0 and residual > tolerance:
                    identity_1x_10x += 1
        identity_band += len(generated.report.get("identity_notice_1x_10x") or [])
    assert n_records == 549
    assert stored + refused + excluded == raw
    assert raw == B1259_RAW
    assert stored == B1259_STORED
    assert refused == B1259_REFUSED
    assert excluded == B1259_EXCLUDED
    assert stored + refused + excluded == B1259_RAW
    assert merged_proposed == B1259_MERGED_PROPOSED
    assert merged_stored == B1259_MERGED_STORED
    assert merged_refused == B1259_MERGED_REFUSED
    assert merged_refused_10x == B1259_MERGED_REFUSED_10X
    assert merged_excluded == B1259_MERGED_EXCLUDED
    assert merged_proposed == merged_stored + merged_refused + merged_excluded
    assert unguarded_total == B1259_UNGUARDED_MERGED_STORED
    assert unguarded_total == sum(unguarded_by_quantity.values())
    assert identity_10x == B1259_IDENTITY_10X
    assert identity_1x_10x == B1259_IDENTITY_1X_10X
    assert identity_band == B1259_IDENTITY_1X_10X
    assert formula_unresolved_ht == B1259_FORMULA_UNRESOLVED_HT
    assert formula_unresolved_rows == B1259_FORMULA_UNRESOLVED_298K_ROWS
    print(
        "B1259_CENSUS",
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
            "unguarded_stored": unguarded_total,
        },
    )
