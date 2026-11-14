from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kems-139-jacobson-2024"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }


def _assert_sc456_evidence(document: dict) -> None:
    rows = _observations(document)

    factsage = rows["jacobson_2024_t1_factsage_2500k_predicted_psat"]
    assert factsage["values"]["quantity"] == "partial_pressure"
    assert factsage["values"]["method_class"] == "model_derived"
    assert factsage["values"]["basis_as_printed"] == (
        "Lunar basalt sample 12022; chemical analysis in Table 1 (Snyder et al. [117])."
    )
    assert "semantics" not in factsage["values"]
    assert factsage["phase"] == "gas"
    assert factsage["units"] == "bar"
    assert factsage["standard_state"] == (
        "Partial pressure of gas species in bar; Table 1 reports FactSage equilibrium at 2500 K."
    )
    assert len(factsage["values"]["points"]) == 21
    assert factsage["values"]["table_csv"] == "tables/kems-139-jacobson-2024/t1.csv"
    assert factsage["values"]["quote"].startswith(
        "Predicted vapor species of a lunar basalt at 2500 K"
    )
    method = factsage["derivation"]["method"]
    assert method["state"]["value"] == (
        "Gibbs free energy minimization with the SLAG database in FactSage at 2500 K."
    )
    assert method["locator"]["page"] == 19
    assert method["locator"]["published_page"] == 18
    assert method["locator"]["section"] == (
        "2.4.2 Ion current and ion counting measurements in the mass spectrum of a vapor; "
        "item 6: Comparison to calculated/estimated vapor pressures for the condensed phase in the Knudsen cell"
    )
    assert "Gibbs free energy minimization code" in method["locator"]["note"]
    assert factsage["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"
    assert factsage["derivation"]["raw_inputs"]["locator"]["page"] == 19
    assert factsage["derivation"]["references"] == [
        "Bale et al. [115]",
        "Snyder et al. [117]",
    ]
    expected_pressures = [
        ("PO2(g)", "0.004569408"),
        ("SiO(g)", "0.004541139"),
        ("Fe(g)", "0.003317841"),
        ("Na(g)", "0.002800994"),
        ("PO(g)", "0.001925179"),
        ("K(g)", "0.000794608"),
        ("FeO(g)", "0.00031189"),
        ("O(g)", "0.000156368"),
        ("O2(g)", "0.000116427"),
        ("Mg(g)", "0.000107833"),
        ("SiO2(g)", "6.14219E-05"),
        ("TiO2(g)", "7.34285E-06"),
        ("P(g)", "3.85922E-06"),
        ("KO(g)", "1.08059E-06"),
        ("TiO(g)", "2.00284E-07"),
        ("Ca(g)", "1.0955E-07"),
        ("CaO(g)", "1.8925E-08"),
        ("Al(g)", "3.66595E-09"),
        ("Si(g)", "3.17871E-09"),
        ("K2(g)", "8.01489E-10"),
        ("Ti(g)", "1.85167E-11"),
    ]
    points = factsage["values"]["points"]
    assert [
        (point["species_as_printed"], point["value_as_printed"])
        for point in points
    ] == expected_pressures
    assert [Decimal(str(point["p_bar"])) for point in points] == [
        Decimal(value) for _, value in expected_pressures
    ]

    gold = rows["jacobson_2024_fig16_au_vant_hoff_figure_only"]
    assert gold["values"]["method_class"] == "measured_reduced"
    assert gold["derivation"]["method"]["locator"]["equation"] == "23b"
    assert gold["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"


def test_sc456_printed_points_and_method_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_sc456_evidence(document)

    reverted = deepcopy(document)
    rows = _observations(reverted)
    rows["jacobson_2024_t1_factsage_2500k_predicted_psat"]["values"][
        "method_class"
    ] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)

    reverted = deepcopy(document)
    factsage = _observations(reverted)[
        "jacobson_2024_t1_factsage_2500k_predicted_psat"
    ]
    factsage["values"]["points"][0]["value_as_printed"] = "0.004569409"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)

    reverted = deepcopy(document)
    _observations(reverted)["jacobson_2024_fig16_au_vant_hoff_figure_only"][
        "values"
    ]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_sc456_evidence(reverted)


def test_sc456_isolated_migration_preserves_model_points_and_lineage() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert result.validation is not None
    assert sum(
        observation.value.kind is ValueKind.POINT
        for observation in result.observations.values()
    ) == 21
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in result.observations.values()
    ) == 12
    factsage_parent = (
        f"{SOURCE_ID}::jacobson_2024_t1_factsage_2500k_predicted_psat"
    )
    factsage_points = [
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(factsage_parent + "::")
    ]
    assert len(factsage_points) == 21
    for observation in factsage_points:
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value.value == "p_partial"
        assert observation.evidence.class_.value.value == "model_derived"
        assert observation.derivation is not None
        assert observation.derivation.references == (
            "Bale et al. [115]",
            "Snyder et al. [117]",
        )
        assert observation.derivation.raw_inputs is not None
        assert observation.derivation.raw_inputs.state.value.value == "not_printed"
    assert not result.validation.hard_issues
