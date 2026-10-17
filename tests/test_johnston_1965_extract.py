from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import simulator.battery.migrate as battery_migrate
from simulator.battery.enums import AdmissionStatus, ValencePrintedForm, ValueKind
from simulator.battery.migrate import (
    Migrator,
    mass_fraction_pair_to_valence_ratio,
    to_plain,
)


REPO = Path(__file__).resolve().parents[1]
EXTRACT = REPO / "data/literature/extracts/johnston-1965-sodium-disilicate-redox.yaml"

RENDER_PINS = {
    "johnston_1965_t1_ti_r07": (0, 0.218),
    "johnston_1965_t1_sn_r06": (1, 1.32),
    "johnston_1965_t1_ce_r02": (1, 3.20),
    "johnston_1965_t1_v_r04": (1, 0.14),
}


def _load_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(doc: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in doc["species"].values()
        for row in block.get("observations", [])
    }


def _assert_render_pins(doc: dict) -> None:
    observations = _observations(doc)
    for observation_id, (point_index, expected) in RENDER_PINS.items():
        row = observations[observation_id]
        point = row["values"]["points"][point_index]
        assert point["value"] == expected
        assert row["locator"] == {
            "page": 3,
            "pdf_page_index": 2,
            "published_page": 186,
            "table": "I",
            "row": int(observation_id.rsplit("r", 1)[1])
            + {
                "ti": 0,
                "sn": 7,
                "ce": 13,
                "sb": 20,
                "mn": 25,
                "co": 32,
                "ni": 38,
                "v": 40,
            }[observation_id.split("_t1_", 1)[1].split("_", 1)[0]],
        }


def test_johnston_table_i_rows_conditions_and_render_pins() -> None:
    doc = _load_extract()
    observations = _observations(doc)
    table_i = [row for row in observations.values() if row["locator"].get("table") == "I"]
    table_ii = [row for row in observations.values() if row["locator"].get("table") == "II"]

    assert len(table_i) == 47
    assert len(table_ii) == 7
    assert sorted(row["locator"]["row"] for row in table_i) == list(range(1, 48))
    assert sum(len(row["values"]["points"]) for row in table_i) == 94
    for row in table_i:
        assert row["phase"] == "glass"
        assert len(row["values"]["points"]) == 2
        conditions = row["point_conditions"]
        assert {"T_C", "atmosphere", "fO2_Pa", "total_pressure_Pa", "composition"} <= set(conditions)
        assert all("state" in condition and "locator" in condition for condition in conditions.values())
        for point in row["values"]["points"]:
            assert point["T_C"] == row["values"]["T_C"]
            assert point["point_conditions"] == conditions

    for row in table_ii:
        assert len(row["values"]["points"]) == 1
        assert row["values"]["points"][0]["point_conditions"] == row["point_conditions"]

    assert sum(row["values"]["method_class"] == "model_derived" for row in table_ii) == 6
    assert sum(row["values"]["method_class"] == "quoted_attributed" for row in table_ii) == 1
    ce = observations["johnston_1965_t1_ce_r01"]
    assert "NaSbO3" not in ce["quench_statement"]
    assert "devitrified during quenching" in ce["quench_statement"]
    for observation_id in (
        "johnston_1965_t1_sb_r01",
        "johnston_1965_t1_sb_r02",
    ):
        assert "NaSbO3" in observations[observation_id]["quench_statement"]
    for observation_id in (
        "johnston_1965_t1_v_r01",
        "johnston_1965_t1_v_r02",
        "johnston_1965_t1_v_r03",
        "johnston_1965_t1_v_r04",
    ):
        assert observations[observation_id]["values"]["reason"] == (
            "no_quantity_token_for_printed_v4_and_v5_wt_percent_pair"
        )
    _assert_render_pins(doc)


def test_johnston_render_pin_fails_after_value_mutation() -> None:
    doc = _load_extract()
    observations = _observations(doc)
    observations["johnston_1965_t1_ti_r07"]["values"]["points"][0]["value"] = 0.219

    with pytest.raises(AssertionError):
        _assert_render_pins(doc)


def test_johnston_isolated_migration_preserves_point_conditions() -> None:
    migrator = Migrator(root=REPO)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = [
        to_plain(row)
        for row in result.observations.values()
        if row.source_id == "johnston-1965-sodium-disilicate-redox"
    ]

    assert len(observations) == 148
    assert len(result.validation.hard_issues) == 0
    assert sum(row["value"]["kind"] == "unavailable" for row in observations) == 101
    table_i = [row for row in observations if row["locator"]["table"] == "I"]
    table_ii = [row for row in observations if row["locator"]["table"] == "II"]
    ratios = [
        row
        for row in observations
        if row["identity"]["quantity"] == {"tag": "value", "value": "valence_ratio"}
    ]
    table_i_cells = [row for row in table_i if row not in ratios]
    assert len(table_i) == 141
    assert len(table_i_cells) == 94
    assert len(table_ii) == 7
    assert len(ratios) == 47
    assert all(row["admission"]["status"] == "admitted" for row in ratios)
    assert all(row["value"]["kind"] == "point" for row in ratios)
    assert sum(
        row["evidence"]["class"]["value"] == "measured_reduced"
        for row in ratios
    ) == 32
    assert sum(
        row["evidence"]["class"]["value"] == "author_estimate"
        for row in ratios
    ) == 15
    expected_i = {
        "T_C",
        "atmosphere",
        "fO2_Pa",
        "total_pressure_Pa",
        "composition",
        "temperature_K",
    }
    expected_ii = expected_i - {"temperature_K"}
    for row in table_i:
        assert expected_i <= set(row["point_conditions"])
    for row in table_ii:
        assert expected_ii <= set(row["point_conditions"])

    context = next(
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row["observation_id"] == "johnston_1965_alkali_shuttle_context"
    )
    assert context["species"] == "Na2O.2SiO2"
    assert context["values"]["context_rail"] == "alkali_shuttle"

    sample_ids = (
        "johnston_1965_t1_ti_r01",
        "johnston_1965_t1_sn_r06",
        "johnston_1965_t1_ce_r02",
    )
    for sample_id in sample_ids:
        row = next(
            row for row in table_i_cells if sample_id in row["observation_id"]
        )
        identity = row["identity"]
        assert identity["composition"]["tag"] == "value"
        assert identity["temperature_K"]["tag"] == "value"
        assert identity.get("total_pressure_Pa") is None
        assert identity.get("fO2_Pa") is None

    # Independent hand pins: Ti row 1 is 0.021/1.50 = 0.014; Sn row 8 is
    # 0.011/1.68 = 0.006547619...; V row 41 is 0.033/1.16 = 0.028448275... .
    ratio_by_source_row = {
        row["observation_id"].split("::")[-2]: row for row in ratios
    }
    ti = ratio_by_source_row["johnston_1965_t1_ti_r01"]
    sn = ratio_by_source_row["johnston_1965_t1_sn_r01"]
    vanadium = ratio_by_source_row["johnston_1965_t1_v_r01"]
    altered_rows = (
        "johnston_1965_t1_ti_r06",
        "johnston_1965_t1_ti_r07",
        "johnston_1965_t1_sb_r01",
        "johnston_1965_t1_sb_r02",
        "johnston_1965_t1_ce_r01",
    )
    ratio_records = {
        source_row: next(
            observation
            for observation in result.observations.values()
            if observation.observation_id.endswith(f"{source_row}::valence_ratio")
        )
        for source_row in (*altered_rows, "johnston_1965_t1_ti_r01")
    }
    for source_row in altered_rows:
        ratio_record = ratio_records[source_row]
        assert ratio_record.quench_state.value == "asserted_altered"
        expected_page = 186 if source_row.endswith("ce_r01") else 187
        assert (
            ratio_record.point_conditions["quench_state"].locator.published_page
            == expected_page
        )
        for cell_id in ratio_record.derived_from:
            assert (
                result.observations[cell_id].quench_state.value
                == "asserted_altered"
            )
    assert ratio_records["johnston_1965_t1_ti_r01"].quench_state.value == "not_stated"
    assert Decimal(ti["value"]["point"]) == Decimal("0.014")
    assert Decimal(sn["value"]["point"]).quantize(
        Decimal("0.000000000001")
    ) == Decimal("0.006547619048")
    assert Decimal(vanadium["value"]["point"]).quantize(
        Decimal("0.000000000001")
    ) == Decimal("0.028448275862")
    assert ti["evidence"]["class"]["value"] == "author_estimate"
    assert sn["evidence"]["class"]["value"] == "measured_reduced"
    assert ti["identity"]["valence_basis"]["value"]["printed_form"] == (
        ValencePrintedForm.MASS_FRACTION_PAIR.value
    )
    assert vanadium["identity"]["valence_basis"]["value"][
        "denominator_oxidation_state"
    ] == 5
    assert "V results are expressed in terms of V4+ (p. 186)" in vanadium[
        "derivation"
    ]["relation"]
    round_tripped_identity = battery_migrate._identity_from_plain(ti["identity"])
    assert round_tripped_identity.valence_basis.value.mass_fraction_basis == (
        "element",
        "element",
    )
    for row in (ti, sn, vanadium):
        assert row["identity"]["composition"]["tag"] == "value"
        components = row["identity"]["composition"]["value"]["components"]
        assert sum(
            (Decimal(amount) for _name, amount in components), Decimal("0")
        ) == Decimal("1")
        assert row["identity"]["fO2_Pa"] == {
            "tag": "unknown",
            "reason": "no_row_level_numeric_pO2_printed",
        }
        assert row["identity"]["temperature_K"]["tag"] == "value"
        assert len(row["derived_from"]) == 2
        assert row["derivation"]["inputs"] == row["derived_from"]
        assert row["uncertainty"]["kind"] == "none"


@pytest.mark.parametrize(
    ("first_state", "second_state", "expected_state"),
    (
        ("asserted_altered", "asserted_preserved", "asserted_altered"),
        ("asserted_preserved", "not_stated", "asserted_preserved"),
    ),
)
def test_mass_fraction_pair_keeps_weaker_cell_quench_claim(
    tmp_path: Path,
    first_state: str,
    second_state: str,
    expected_state: str,
) -> None:
    document = _load_extract()
    row = _observations(document)["johnston_1965_t1_ti_r06"]
    base_conditions = row["point_conditions"]
    locator = base_conditions["quench_state"]["locator"]
    for point, state in zip(row["values"]["points"], (first_state, second_state)):
        conditions = copy.deepcopy(base_conditions)
        conditions["quench_state"] = {
            "state": {"tag": "value", "value": state},
            "locator": locator,
        }
        point["point_conditions"] = conditions
    row["point_conditions"] = copy.deepcopy(
        row["values"]["points"][0]["point_conditions"]
    )

    extract = tmp_path / EXTRACT.name
    extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    migrator = Migrator(root=REPO)
    migrator._migrate_extract(extract)
    ratio = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(
            "johnston_1965_t1_ti_r06::valence_ratio"
        )
    )
    assert ratio.quench_state.value == expected_state


def test_mass_fraction_pair_uses_cation_stoichiometry() -> None:
    # Hand computation: Fe2O3 contributes 2 Fe, FeO contributes 1 Fe, so
    # (1 g / 159.687 g mol-1 * 2) / (2 g / 71.844 g mol-1 * 1)
    # = 71.844/159.687 = 0.449905... . Swapping factors changes the result.
    value = mass_fraction_pair_to_valence_ratio(
        Decimal("1"),
        Decimal("2"),
        element="Fe",
        mass_fraction_basis=("Fe2O3", "FeO"),
    )
    assert value.quantize(Decimal("0.000001")) == Decimal("0.449905")


def test_element_basis_cancels_without_molar_mass_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        battery_migrate,
        "oxide_molar_mass",
        lambda _formula: pytest.fail("element-equivalent cells cancel M directly"),
    )
    assert mass_fraction_pair_to_valence_ratio(
        Decimal("0.021"),
        Decimal("1.50"),
        element="Ti",
        mass_fraction_basis=("element", "element"),
    ) == Decimal("0.014")


def test_missing_mass_fraction_cell_is_typed_refusal_not_zero() -> None:
    with pytest.raises(battery_migrate.IdentityRefusal, match="missing input") as caught:
        mass_fraction_pair_to_valence_ratio(
            Decimal("0.021"),
            None,
            element="Ti",
            mass_fraction_basis=("element", "element"),
        )
    assert caught.value.reason.value == "invalid_source"

    doc = _load_extract()
    row = _observations(doc)["johnston_1965_t1_ti_r01"]
    row["values"]["points"].pop()
    migrator = Migrator(root=REPO)
    original_loader = battery_migrate.load_yaml

    def load_mutated_extract(path: Path) -> dict:
        if Path(path) == EXTRACT:
            return doc
        return original_loader(path)

    battery_migrate.load_yaml = load_mutated_extract
    try:
        migrator._migrate_extract(EXTRACT)
        migrator.finalize()
    finally:
        battery_migrate.load_yaml = original_loader
    refused = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "johnston-1965-sodium-disilicate-redox"
        and observation.observation_id.endswith(
            "johnston_1965_t1_ti_r01::valence_ratio"
        )
    )
    assert refused.value.kind is ValueKind.UNAVAILABLE
    assert "missing input" in refused.value.unavailable_reason
    assert refused.value.point is None
    assert refused.admission.status is AdmissionStatus.REJECTED
    assert any(
        entry.observation_id == refused.observation_id
        and "missing input" in entry.why
        for entry in migrator.result.queue
    )
