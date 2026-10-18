from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import UncertaintyKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/baborak-2023-alkali-silicate-levitation-evaporation.yaml"
)
def _row(document: dict, observation_id: str, section: str = "observations") -> dict:
    matches = [
        row
        for species in document["species"].values()
        for row in species.get(section, [])
        if row.get("observation_id") == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_table_page_pins(document: dict) -> None:
    # Table 1 and Table 2 cells are printed on published page 459.
    ls2_tg = _row(document, "baborak_2023_ls2_tg_table1")
    assert ls2_tg["locator"]["published_page"] == 459
    assert ls2_tg["values"]["T_K"] == 726
    assert ls2_tg["values"]["T_K_as_printed"] == "726 ± 5"
    assert ls2_tg["values"]["T_m_uncertainty_K"] == 5

    ns2_liquidus = _row(document, "baborak_2023_ns2_t_liquidus_table1")
    assert ns2_liquidus["locator"]["published_page"] == 459
    assert ns2_liquidus["values"]["T_K"] == 1148

    ks8_evaporation = _row(
        document, "baborak_2023_ks8_evaporation_tendency_table2", "context"
    )
    assert ks8_evaporation["locator"]["published_page"] == 459
    assert ks8_evaporation["values"]["delta_evap_as_printed"] == 0.52

    table1 = [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row.get("locator", {}).get("table") == "1"
    ]
    assert len(table1) == 9
    assert {row["values"]["sample_id"] for row in table1} == {"LS2", "NS2", "KS2"}
    assert {
        row["phase"]
        for row in table1
        if row["values"]["property_kind"] == "liquidus"
    } == {"l"}
    assert {
        row["phase"]
        for row in table1
        if row["values"]["property_kind"]
        in {"glass_transition", "crystallization_onset"}
    } == {"glass"}

    table2 = [
        row
        for row in document["species"]["SiO2"]["context"]
        if row["observation_id"].endswith("_evaporation_tendency_table2")
    ]
    assert len(table2) == 9

    table3 = [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row.get("locator", {}).get("table") == "3"
    ]
    assert len(table3) == 2
    assert {row["phase"] for row in table3} == {"cr"}


def _migrated_observation(migrator: Migrator, short_id: str):
    matches = [
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.endswith(f"::{short_id}")
    ]
    assert len(matches) == 1
    return matches[0]


def test_baborak_page_pins_compound_identity_and_quench_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_page_pins(document)

    mutated = deepcopy(document)
    _row(mutated, "baborak_2023_ls2_tg_table1")["values"]["T_K"] = 700
    with pytest.raises(AssertionError):
        _assert_table_page_pins(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert len(migrator.result.observations) == 11

    compounds = {
        "baborak_2023_ls2_tg_table1": "Li2Si2O5",
        "baborak_2023_ls2_tc_table1": "Li2Si2O5",
        "baborak_2023_ls2_t_liquidus_table1": "Li2Si2O5",
        "baborak_2023_ns2_tg_table1": "Na2Si2O5",
        "baborak_2023_ns2_tc_table1": "Na2Si2O5",
        "baborak_2023_ns2_t_liquidus_table1": "Na2Si2O5",
        "baborak_2023_ks2_tg_table1": "K2Si2O5",
        "baborak_2023_ks2_tc_table1": "K2Si2O5",
        "baborak_2023_ks2_t_liquidus_table1": "K2Si2O5",
        "baborak_2023_lb2_t_liquidus_table3": "Li2B4O7",
        "baborak_2023_np_t_melting_table3": "NaPO3",
    }
    for observation_id, formula in compounds.items():
        observation = _migrated_observation(migrator, observation_id)
        assert observation.identity.species.formula == formula

    expected_compositions = {
        "baborak_2023_ls2_t_liquidus_table1": {
            "Li2O": Decimal("1"),
            "SiO2": Decimal("2"),
        },
        "baborak_2023_ns2_t_liquidus_table1": {
            "Na2O": Decimal("1"),
            "SiO2": Decimal("2"),
        },
        "baborak_2023_ks2_t_liquidus_table1": {
            "K2O": Decimal("1"),
            "SiO2": Decimal("2"),
        },
    }
    for observation_id, expected_composition in expected_compositions.items():
        composition = _migrated_observation(migrator, observation_id).identity.composition
        assert composition.is_value
        assert composition.value.as_map() == expected_composition

    for observation_id in (
        "baborak_2023_ls2_tg_table1",
        "baborak_2023_ls2_tc_table1",
        "baborak_2023_ns2_tg_table1",
        "baborak_2023_ns2_tc_table1",
        "baborak_2023_ks2_tg_table1",
        "baborak_2023_ks2_tc_table1",
    ):
        observation = _migrated_observation(migrator, observation_id)
        assert observation.uncertainty.kind is UncertaintyKind.PRINTED
        assert observation.uncertainty.verbatim["T_m_uncertainty_K"] == 5
        quench = observation.point_conditions["quench_state"]
        assert quench.state.value == "not_stated"
        assert quench.locator.published_page == 456
        source_row = _row(document, observation_id)
        assert source_row["quench_statement"]["text"] == (
            "Finally, the samples were extracted from the furnace and quenched by "
            "rapidly immersing the bottom of the Pt crucible in water."
        )

    lb2_bound = _row(document, "baborak_2023_lb2_high_temperature_mass_loss", "context")
    assert lb2_bound["values"]["mass_loss_wt_pct_as_printed"] == {
        "value": 45,
        "bound": "upper",
    }
    assert lb2_bound["values"]["pyrometer_temperature_C_as_printed"] == {
        "value": 1200,
        "approximate": True,
    }
    assert not any(
        "baborak_2023_lb2_high_temperature_mass_loss" in observation.observation_id
        for observation in migrator.result.observations.values()
    )
    serialized = EXTRACT.read_text(encoding="utf-8")
    assert "LB2_high_temperature_bead_ML_36_percent" not in serialized
    assert "LB2 ICP ML=36%" not in serialized
    assert "Oxide content (mol); scale 0.00–0.30" in serialized
