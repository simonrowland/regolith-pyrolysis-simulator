from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "sack-ghiorso-2017-fassaite-thermodynamics.yaml"
EXTRACT = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME


def _contexts_by_id(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (container, row)
        for container, block in document["species"].items()
        for row in block["context"]
    }


def test_sack_ghiorso_tables_preserve_values_and_provenance() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = _contexts_by_id(document)

    table3 = contexts["sack_ghiorso_2017_table3_mixing_parameters"][1]
    assert table3["locator"]["published_page"] == 823
    assert table3["locator"]["pdf_page_index"] == 16
    rows = table3["values"]["rows"]
    assert len(rows) == 16
    assert rows[0]["value_as_published"] == "10.84 ± 1"
    assert rows[0]["method_class"] == "model_derived"
    assert rows[1]["value_as_published"] == "16.84"
    assert rows[1]["method_class"] == "quoted_attributed"
    assert rows[1]["attribution"] == (
        "§ Cohen (1986), Vinograd (2001), and Warren and others (2001); "
        "** Benisek and others (2007)."
    )
    assert rows[11]["value_as_published"] == "25 ± 10"
    assert rows[11]["footnote_marks"] == "§, §§, **"
    assert rows[11]["attribution"] == (
        "§ Cohen (1986), Vinograd (2001), and Warren and others (2001); "
        "§§ Yang (1975); ** Benisek and others (2007)."
    )
    assert rows[15]["value_as_published"] == "−55 ± 15"
    assert rows[15]["footnote_marks"] == "§, §§, **"
    assert rows[15]["attribution"] == (
        "§ Cohen (1986), Vinograd (2001), and Warren and others (2001); "
        "§§ Yang (1975); ** Benisek and others (2007)."
    )
    assert all("uncertainty" not in row for row in rows)

    diopside = contexts[
        "sack_ghiorso_2017_table4_diopside_adopted_properties"
    ]
    assert diopside[0] == "SiO2"
    assert diopside[1]["locator"]["pdf_page_index"] == 28
    assert diopside[1]["values"]["formation_enthalpy_298_15"][
        "value_as_published"
    ] == "−3200583"
    assert diopside[1]["values"]["heat_capacity"]["coefficients_as_published"][
        "k2×10^-5"
    ] == "−70.870"

    alumino_buffonite = contexts[
        "sack_ghiorso_2017_table4_alumino_buffonite_adopted_properties"
    ][1]["values"]
    assert alumino_buffonite["formula_as_published"] == "CaTi1/2AlSiO6"
    assert alumino_buffonite["formula_elsewhere_as_published"] == (
        "CaMg1/2Ti1/2AlSiO6"
    )
    assert alumino_buffonite["formation_enthalpy_298_15"]["method_class"] == (
        "quoted_attributed"
    )
    table4 = [
        observation_id
        for observation_id in contexts
        if observation_id.startswith("sack_ghiorso_2017_table4_")
    ]
    assert len(table4) == 6

    grossmanite = contexts[
        "sack_ghiorso_2017_table4_grossmanite_adopted_properties"
    ][1]["values"]
    assert grossmanite["formation_enthalpy_298_15"]["method_class"] == (
        "model_derived"
    )
    assert grossmanite["standard_entropy_298_15"]["value_as_published"] == (
        "175.3085"
    )
    spinel = contexts["sack_ghiorso_2017_table4_spinel_adopted_properties"]
    assert spinel[0] == "MgO"
    assert spinel[1]["values"]["standard_entropy_298_15"]["value_as_published"] == (
        "84.535"
    )


def test_all_figures_are_deferred_with_identity_and_series_inventory() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [
        (container, row)
        for container, block in document["species"].items()
        for row in block["context"]
        if row["type"] == "figure_record"
    ]

    assert len(figures) == 13
    assert {container for container, _ in figures} == {"SiO2"}
    for _, figure in figures:
        values = figure["values"]
        assert figure["locator"]["figure"]
        assert figure["locator"]["published_page"]
        assert figure["locator"]["pdf_page_index"] is not None
        assert figure["phase"]
        assert values["species"]
        assert values["model_system_as_published"] == (
            "Di–Gr–AlBf–CATS fassaite solid solution"
        )
        assert values["model_endmembers_as_published"]
        assert values["temperature_as_published"]
        assert values["axes_as_printed"]
        assert values["caption_summary"]
        assert figure["locator"]["panel"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["numeric_points"] == "none"
        assert values["series"]
        for series in values["series"]:
            assert series["method_class"] in {"model_derived", "quoted_attributed"}
            assert "marker_count" in series
            if series["marker_count"] == "unknown":
                assert series["marker_count_reason"]

    by_id = _contexts_by_id(document)
    fig3 = by_id["sack_ghiorso_2017_fig3_configurational_entropy"][1]
    assert fig3["values"]["quantity"] == "S"
    assert sum(series["marker_count"] for series in fig3["values"]["series"]) == 8
    fig13 = by_id["sack_ghiorso_2017_fig13_solar_gas_oxygen_fugacity"][1]
    assert fig13["values"]["printed_quantity"] == "log10 fO2"
    assert fig13["values"]["pressure_as_published"] == "Ptot = 10^-3 atm"
    assert fig13["values"]["schema_gap"]


def test_isolated_migration_keeps_this_model_paper_unscored(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert all(block["observations"] == [] for block in document["species"].values())

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert result.observations == {}
    assert result.validation is not None
    assert result.validation.hard_issues == ()
