from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from simulator.accounting.formulas import SpeciesFormula
from simulator.state import MOLAR_MASS
from simulator.terminal_product_taxonomy import (
    MOL_BASIS,
    WT_BASIS,
    classify_terminal_product,
    load_terminal_product_taxonomy,
    taxonomy_nodes_by_id,
)


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ceramics_taxonomy.yaml"
REPO_ROOT = DATA_PATH.parents[1]

HIERARCHY_TO_CANONICAL = {
    "calcium_aluminate_refractory": None,
    "monocalcium_aluminate_CA": "calcium_aluminate_ca",
    "calcium_dialuminate_CA2": "calcium_aluminate_ca2_grossite",
    "calcium_hexaluminate_CA6": "hibonite_ca6",
    "tricalcium_aluminate_C3A": "tricalcium_aluminate_c3a",
    "mayenite_C12A7": "mayenite_c12a7",
    "aluminosilicate_ceramic": None,
    "mullite": "mullite",
    "anorthite": "anorthite_plagioclase",
    "cordierite_mullite": None,
    "sillimanite_group": None,
    "cordierite_pure": "cordierite",
    "basic_mgo_refractory": None,
    "doloma": "dolime_cao_mgo",
    "magnesium_aluminate_spinel": "spinel_mgal2o4",
    "forsterite": "forsterite_olivine",
    "periclase_mgo": "periclase_mgo",
    "enstatite": None,
    "ca_mg_silicate": None,
    "wollastonite": "wollastonite",
    "diopside": "diopside_pyroxene",
    "akermanite_melilite": "akermanite_melilite_mg",
    "merwinite": "merwinite",
    "monticellite": "monticellite",
    "alkaline_earth_silicate_cement": None,
    "dicalcium_silicate_C2S": None,
    "tricalcium_silicate_C3S": None,
    "ree_ti_cr_rump_phase": None,
    "perovskite_catito3": "perovskite_catito3",
    "ree_aluminate_silicate_family": None,
    "cr_spinel_chromite": None,
    "ree_titanate_pyrochlore_family": "ree_titanate_pyrochlore_family",
    "cas_cmas_glass_ceramic": None,
    "cmas_glass_ceramic": None,
    "gehlenite_anorthite_path": None,
}


def test_terminal_product_taxonomy_schema_and_node_coverage() -> None:
    data = load_terminal_product_taxonomy(DATA_PATH)
    assert data["taxonomy_name"] == "terminal_product_taxonomy"
    assert set(data["product_classes"]) == {
        "oxide_ceramic",
        "metallic",
        "mixed",
        "unclassified_concentrate",
    }
    assert len(data["nodes"]) == 23
    entries = data["ceramic_hierarchy"]["entries"]
    assert len(entries) == 35
    assert {
        entry_id: entry["canonical_node_id"]
        for entry_id, entry in entries.items()
    } == HIERARCHY_TO_CANONICAL
    source_ids = set(data["sources"])
    for node in data["nodes"]:
        assert node["id"]
        assert node["product_class"] in data["product_classes"]
        assert node["evidence_tier"] in {"A", "B", "C", "B/C"}
        assert "tolerances" in node
        assert "match" in node
        assert set(node["sources"]).issubset(source_ids)
        signature = node.get("oxide_signature_wt_pct")
        if signature:
            assert sum(signature.values()) == pytest.approx(100.0, abs=0.2)


def test_terminal_product_taxonomy_yaml_loads_directly() -> None:
    raw = yaml.safe_load(DATA_PATH.read_text())
    assert raw["version"] == "2026-07-27"
    assert [node["id"] for node in raw["nodes"]]


def test_hierarchy_strength_is_sourced_text_and_not_duplicated_in_datasheet() -> None:
    data = load_terminal_product_taxonomy(DATA_PATH)
    for entry in data["ceramic_hierarchy"]["entries"].values():
        strength = entry["strength"]
        assert strength["status"] == "sourced_qualitative_text"
        assert strength["text"].strip()
        assert strength["source_ids"] == ["ceramic_datasheets"]
        assert "mechanical_properties" not in entry["datasheet"]


def test_classifier_emits_grounded_strength_and_typed_null_when_unmapped() -> None:
    forsterite = classify_terminal_product(
        {"MgO": 57.3, "SiO2": 42.7},
        taxonomy_path=DATA_PATH,
    )
    properties = forsterite["matched_nodes"][0]["properties"]
    assert properties["catalog_entry_id"] == "forsterite"
    assert properties["strength"]["status"] == "sourced_qualitative_text"
    assert properties["strength"]["source_ids"] == ["ceramic_datasheets"]
    assert properties["strength"]["text"].startswith("Mohs **7**")

    gehlenite = classify_terminal_product(
        {"CaO": 40.9, "Al2O3": 37.2, "SiO2": 21.9},
        taxonomy_path=DATA_PATH,
    )
    unmapped = gehlenite["matched_nodes"][0]["properties"]
    assert unmapped["catalog_entry_id"] is None
    assert unmapped["strength"] == {
        "status": "not_classified",
        "text": None,
        "source_ids": [],
    }
    assert unmapped["liner_suitability"]["verdict"] is None


def test_empty_composition_emits_typed_not_classified_properties() -> None:
    result = classify_terminal_product({}, taxonomy_path=DATA_PATH)

    assert result["match_status"] == "no_match"
    assert result["properties_panel"] == {
        "show": False,
        "property_basis": None,
        "status": "not_classified",
        "strength": {
            "status": "not_classified",
            "text": None,
            "source_ids": [],
        },
    }


def test_classify_terminal_product_has_one_runtime_producer_and_no_web_caller() -> None:
    callers = []
    for path in (REPO_ROOT / "simulator").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            if any(
                isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "classify_terminal_product"
                    )
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "classify_terminal_product"
                    )
                )
                for node in ast.walk(function)
            ):
                callers.append(
                    (path.relative_to(REPO_ROOT).as_posix(), function.name)
                )
    assert callers == [
        (
            "simulator/terminal_product_taxonomy.py",
            "build_terminal_product_taxonomy_entity",
        ),
    ]
    for path in (REPO_ROOT / "web").rglob("*.py"):
        assert "classify_terminal_product" not in path.read_text()


def test_terminal_product_classifier_matches_cmas_assemblage() -> None:
    cmas_rump_wt_pct = {
        "CaO": 18.155,
        "Al2O3": 30.81,
        "SiO2": 38.865,
        "MgO": 12.17,
    }
    result = classify_terminal_product(
        cmas_rump_wt_pct,
        basis=WT_BASIS,
        residue_mass_kg=25.0,
        furnace_ceiling_c=1300,
        temperature_profile_id="PATH_AB_A_staged",
        feedstock_id="lunar_mare_low_ti_cmas",
        terminal_product_account_or_artifact="terminal_rump",
        taxonomy_path=DATA_PATH,
    )
    assert result["product_class"] == "oxide_ceramic"
    assert result["match_status"] == "matched_mixture"
    assert result["user_label_term"] == "terminal product"
    assert result["display_name"].startswith("terminal product at 1300 C: mixture of ")
    matched = {node["id"] for node in result["matched_nodes"]}
    assert "anorthite_plagioclase" in matched
    assert "diopside_pyroxene" in matched
    assert "spinel_mgal2o4" in matched
    assert result["provenance"]["furnace_ceiling_c"] == 1300
    assert result["evidence_tiers"]["anorthite_plagioclase"] == "A"
    assert result["grade"]["residue_mass_kg"] == pytest.approx(25.0)
    assert result["grade"]["value_buckets"] == {}
    assert "omitted buckets are not zero grades" in result["grade"]["coverage"]["note"]


def test_terminal_product_classifier_accepts_mol_native_input() -> None:
    anorthite_wt = {
        "CaO": 20.2,
        "Al2O3": 36.6,
        "SiO2": 43.2,
    }
    oxide_mol = {
        oxide: wt_pct / MOLAR_MASS[oxide]
        for oxide, wt_pct in anorthite_wt.items()
    }
    result = classify_terminal_product(
        oxide_mol,
        basis=MOL_BASIS,
        furnace_ceiling_c=1550,
        taxonomy_path=DATA_PATH,
    )
    assert result["match_status"] == "matched_single"
    assert result["matched_nodes"][0]["id"] == "anorthite_plagioclase"
    assert result["provenance"]["basis"] == "oxide_wt_pct_normalized_volatiles_free_from_oxide_mol"


def test_terminal_product_classifier_ci_crash_composition_fails_closed() -> None:
    ci_crash_wt_pct = {
        "SiO2": 37.059899899219545,
        "MgO": 24.70659359574613,
        "FeO": 12.884766628666068,
        "Na2O": 10.000108280476853,
        "K2O": 8.399940611475058,
        "Al2O3": 2.5735438320687014,
        "CaO": 2.316289448188573,
        "NiO": 2.0588577041590743,
    }
    result = classify_terminal_product(
        ci_crash_wt_pct,
        basis=WT_BASIS,
        furnace_ceiling_c=865.0000000000001,
        feedstock_id="ci_carbonaceous_chondrite",
        taxonomy_path=DATA_PATH,
    )
    assert result["product_class"] == "unclassified_concentrate"
    assert result["match_status"] == "no_match"
    assert result["composition_only"] is True
    assert "matched_nodes" not in result
    assert result["oxide_wt_pct"]["Na2O"] == pytest.approx(10.000108)
    assert result["display_name"] == "terminal product at 865 C"


def test_terminal_product_grade_bucket_math_and_omissions() -> None:
    result = classify_terminal_product(
        {
            "REE_oxides": 10.0,
            "TiO2": 5.0,
            "Cr2O3": 2.5,
            "ZrO2": 1.5,
            "SiO2": 81.0,
        },
        basis=WT_BASIS,
        residue_mass_kg=200.0,
        taxonomy_path=DATA_PATH,
    )
    grade = result["grade"]
    buckets = grade["value_buckets"]
    assert buckets["rare_earth_oxides"]["wt_pct_of_residue"] == pytest.approx(10.0)
    assert buckets["rare_earth_oxides"]["mass_kg"] == pytest.approx(20.0)
    assert buckets["titania"]["wt_pct_of_residue"] == pytest.approx(5.0)
    assert buckets["titania"]["mass_kg"] == pytest.approx(10.0)
    assert buckets["chromia"]["wt_pct_of_residue"] == pytest.approx(2.5)
    assert buckets["chromia"]["mass_kg"] == pytest.approx(5.0)
    assert buckets["zirconia"]["wt_pct_of_residue"] == pytest.approx(1.5)
    assert buckets["zirconia"]["mass_kg"] == pytest.approx(3.0)
    assert grade["coverage"]["reported_species"] == ["Cr2O3", "REE_oxides", "TiO2", "ZrO2"]
    assert "metallic_pgm" not in buckets
    omitted = {item["bucket"]: item for item in grade["coverage"]["omitted_value_buckets"]}
    assert omitted["metallic_pgm"]["status"] == "future_out_of_domain_today"


def test_terminal_product_grade_omits_absent_species_instead_of_zero_filling() -> None:
    result = classify_terminal_product(
        {"TiO2": 100.0},
        basis=WT_BASIS,
        residue_mass_kg=3.0,
        taxonomy_path=DATA_PATH,
    )
    grade = result["grade"]
    assert set(grade["value_buckets"]) == {"titania"}
    assert grade["value_buckets"]["titania"]["wt_pct_of_residue"] == pytest.approx(100.0)
    omitted = {item["bucket"]: item for item in grade["coverage"]["omitted_value_buckets"]}
    assert omitted["chromia"]["reason"] == "source_species_absent"
    assert "chromia" not in grade["value_buckets"]
    assert "wt_pct_of_residue" not in omitted["chromia"]


def test_terminal_product_classifier_kreep_ish_high_ree_leads_with_grade() -> None:
    result = classify_terminal_product(
        {"REE_oxides": 52.0, "P2O5": 18.0, "K2O": 8.0, "TiO2": 22.0},
        basis=WT_BASIS,
        residue_mass_kg=50.0,
        furnace_ceiling_c=1800,
        taxonomy_path=DATA_PATH,
    )
    assert next(iter(result)) == "grade"
    assert result["product_class"] == "unclassified_concentrate"
    assert result["match_status"] == "no_match"
    assert result["composition_only"] is True
    assert result["evidence_tiers"] == {}
    assert result["grade"]["value_buckets"]["rare_earth_oxides"]["wt_pct_of_residue"] == pytest.approx(52.0)
    assert result["grade"]["value_buckets"]["rare_earth_oxides"]["mass_kg"] == pytest.approx(26.0)


def test_terminal_product_classifier_does_not_nearest_neighbor_out_of_tolerance() -> None:
    result = classify_terminal_product(
        {
            "CaO": 20.2,
            "Al2O3": 36.6,
            "SiO2": 35.0,
            "REE_oxides": 8.2,
        },
        basis=WT_BASIS,
        furnace_ceiling_c=1300,
        taxonomy_path=DATA_PATH,
    )
    assert result["product_class"] == "unclassified_concentrate"
    assert result["match_status"] == "no_match"
    assert result["composition_only"] is True


@pytest.mark.parametrize(
    ("node_id", "formula", "oxide_coefficients"),
    [
        ("anorthite_plagioclase", "CaAl2Si2O8", {"CaO": 1, "Al2O3": 1, "SiO2": 2}),
        ("diopside_pyroxene", "CaMgSi2O6", {"CaO": 1, "MgO": 1, "SiO2": 2}),
        ("spinel_mgal2o4", "MgAl2O4", {"MgO": 1, "Al2O3": 1}),
        ("forsterite_olivine", "Mg2SiO4", {"MgO": 2, "SiO2": 1}),
        ("perovskite_catito3", "CaTiO3", {"CaO": 1, "TiO2": 1}),
    ],
)
def test_terminal_product_formula_signatures_are_stoichiometric(
    node_id: str,
    formula: str,
    oxide_coefficients: dict[str, int],
) -> None:
    node = taxonomy_nodes_by_id(DATA_PATH)[node_id]
    assert node["formula"] == formula
    phase_mass = SpeciesFormula.parse(formula).molar_mass_g_per_mol()
    expected = {
        oxide: coeff * MOLAR_MASS[oxide] / phase_mass * 100.0
        for oxide, coeff in oxide_coefficients.items()
    }
    for oxide, wt_pct in expected.items():
        assert node["oxide_signature_wt_pct"][oxide] == pytest.approx(wt_pct, abs=0.2)


def test_terminal_product_taxonomy_keeps_dolime_distinct_from_dolomite() -> None:
    node = taxonomy_nodes_by_id(DATA_PATH)["dolime_cao_mgo"]
    assert "dolomite" not in node["label"].lower()
    assert "Not dolomite" in node["properties"]["notes"]
    assert node["product_class"] == "mixed"


def test_metallic_placeholders_are_future_out_of_domain() -> None:
    nodes = taxonomy_nodes_by_id(DATA_PATH)
    for node_id in (
        "metallic_fe_ni_pgm_lump_future",
        "metallic_fe_ni_phosphide_mixed_future",
    ):
        node = nodes[node_id]
        assert node["status"] == "future_out_of_domain_today"
        assert node["match"]["oxide_only_match_allowed"] is False
        assert node["evidence_tier"] == "C"
