from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import _mole_fraction_composition_from_values
from tests.battery.test_migrate import _migrate_real_extract
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/cheng-2021-fe-ca-si-o-phase-equilibria.yaml"


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _observation(document: dict, observation_id: str) -> dict:
    matches = [
        observation
        for block in document["species"].values()
        for observation in block.get("observations", [])
        if observation.get("observation_id") == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


RENDER_PINS = (
    # 600 dpi PDF p. 16, Table 5, experiment 1 liquid.
    ("cheng21_t5_e001_liq_feo1p5", 87.5, 16),
    # 600 dpi PDF p. 20, Table 5, experiment 98 C2F solid.
    ("cheng21_t5_e098_c2f_feo1p5", 49.5, 20),
    # 600 dpi PDF p. 22, Table 5, experiment 119 Liq 1.
    ("cheng21_t5_e119_liq_1_sio2", 65.5, 22),
    # 600 dpi PDF p. 11, Table 3, initial mixture A.
    ("cheng21_table3_a_liq_cao", 27.6, 11),
)


def _assert_render_pins(document: dict) -> None:
    for observation_id, expected, pdf_page in RENDER_PINS:
        observation = _observation(document, observation_id)
        assert observation["values"]["value"] == expected
        assert observation["locator"]["page"] == pdf_page
        assert observation["locator"]["published_page"] == pdf_page
        assert observation["locator"]["pdf_page_index"] == pdf_page - 1


def test_cheng_table_rows_and_600_dpi_render_pins() -> None:
    document = _document()
    _assert_render_pins(document)

    table5_rows = {
        observation["values"]["phase_row_id"]
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_")
        and "phase_row_id" in observation["values"]
    }
    assert len(table5_rows) == 309  # 306 numeric phase rows and 3 printed n/a rows.
    numeric_table5_rows = {
        observation["values"]["phase_row_id"]
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_")
        and "phase_row_id" in observation["values"]
        and "composition_mol" in observation["values"]
    }
    assert len(numeric_table5_rows) == 306

    for block in document["species"].values():
        for observation in block["observations"]:
            if observation.get("uncertainty"):
                assert float(observation["uncertainty"]["value"]) > 0

    paired = [
        observation
        for block in document["species"].values()
        for observation in block["observations"]
        if observation["observation_id"].startswith("cheng21_t5_e119_")
        and observation["phase"] in {"Liq 1", "Liq 2"}
    ]
    assert {observation["phase"] for observation in paired} == {"Liq 1", "Liq 2"}
    assert {
        observation["point_conditions"]["tie_line_id"]["state"]["value"]
        for observation in paired
    } == {"cheng21_t5_e119::tie-line"}


def test_cheng_render_pin_rejects_a_changed_cell() -> None:
    document = deepcopy(_document())
    _observation(document, "cheng21_t5_e001_liq_feo1p5")["values"]["value"] = 87.4
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_cheng_all_locators_use_printed_pages_and_zero_based_pdf_indices() -> None:
    document = _document()
    locators = list(iter_locators(document))
    assert len(locators) == 9172
    assert all(
        locator["published_page"] == locator["page"]
        and locator["pdf_page_index"] == locator["page"] - 1
        for locator in locators
    )
    experiments = {
        row["experiment_id"]: row["locator"]
        for row in document["experiments"]
        if row["experiment_id"] in {"cheng21_table3_a", "cheng21_table3_b"}
    }
    assert set(experiments) == {"cheng21_table3_a", "cheng21_table3_b"}
    assert all(locator["page"] == 11 for locator in experiments.values())


def test_cheng_isolated_migration_carries_temperature_and_conditions(tmp_path: Path) -> None:
    """Printed-total histogram: 99.7: 3, 99.9: 102, 100.0: 765, 100.1: 54."""
    result = _migrate_real_extract(
        tmp_path, "cheng-2021-fe-ca-si-o-phase-equilibria.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 990
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in result.observations.values()
    ) == 3

    document = _document()
    composition_rows = {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block["observations"]
        if row.get("type") == "equilibrium_phase_composition"
        and isinstance(row.get("values", {}).get("composition_mol"), dict)
    }
    rows_by_phase = {}
    for row in composition_rows.values():
        phase_key = row["values"].get("phase_row_id") or row["experiment"]
        rows_by_phase.setdefault(phase_key, []).append(row)
    migrated_by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert len(composition_rows) == 924
    for observation_id, source_row in composition_rows.items():
        printed = source_row["values"]["composition_mol"]
        phase_key = source_row["values"].get("phase_row_id") or source_row["experiment"]
        total_pct = sum(
            Decimal(str(row["values"]["value"]))
            for row in rows_by_phase[phase_key]
        )
        assert Decimal("99.7") <= total_pct <= Decimal("100.3")
        assert printed["basis"] == "printed_mole_fraction"
        observation = migrated_by_id[observation_id]
        assert observation.identity.composition.is_not_applicable
        composition, omitted = _mole_fraction_composition_from_values(source_row["values"])
        assert composition is not None
        assert not omitted
        migrated = composition.as_map()
        assert abs(sum(migrated.values()) - total_pct / Decimal("100")) <= Decimal("1e-9")

    feo_pin, omitted = _mole_fraction_composition_from_values(
        composition_rows["cheng21_t5_e001_liq_feo1p5"]["values"]
    )
    assert not omitted
    assert feo_pin is not None
    assert feo_pin.as_map()["FeO1.5"] == Decimal("0.875")

    liquidus_rows = [
        row
        for block in document["species"].values()
        for row in block["observations"]
        if row.get("type") == "transition_point"
        and isinstance(row.get("values", {}).get("composition_mol"), dict)
    ]
    assert len(liquidus_rows) == 63
    for row in liquidus_rows:
        composition = row["values"]["composition_mol"]
        assert composition["basis"] == "printed_mole_fraction"
        amounts = [
            Decimal(str(amount))
            for key, amount in composition.items()
            if key != "basis"
        ]
        assert amounts
        assert all(Decimal("0") <= amount <= Decimal("1.003") for amount in amounts)
    assert (
        _observation(document, "cheng21_t5_e001_liquidus")["values"]["composition_mol"]
        == composition_rows["cheng21_t5_e001_liq_feo1p5"]["values"]["composition_mol"]
    )
    liquidus_migrated = [
        observation
        for observation in result.observations.values()
        if "cheng21_t5_e001_liquidus" in observation.observation_id
    ]
    assert len(liquidus_migrated) == 1
    identity = liquidus_migrated[0].identity.composition
    assert identity.is_value
    assert identity.value.amount_basis.value == "mole_fraction"
    assert identity.value.as_map() == {
        "CaO": Decimal("0"),
        "FeO1.5": Decimal("0.875"),
        "SiO2": Decimal("0.125"),
    }

    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions is not None
        for key in ("temperature_K", "fO2_Pa"):
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
