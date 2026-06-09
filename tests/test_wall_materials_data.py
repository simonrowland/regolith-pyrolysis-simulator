from __future__ import annotations

from pathlib import Path

import yaml


DATA_DIR = Path(__file__).resolve().parents[1] / "data"

WALL_MATERIALS = {
    "fused_silica",
    "dense_alumina",
    "doloma",
    "magnesia",
    "bulk_zirconia_ysz",
    "plasma_sprayed_alumina",
    "plasma_sprayed_ysz",
    "plasma_sprayed_mullite",
}
WALL_ATTACK_SPECIES = {"SiO", "alkali_NaK", "Fe_FeO"}
WALL_STICKINESS_SPECIES = {"SiO", "alkali", "Fe"}
EVIDENCE_TAGS = {"direct", "analogous-only", "uncharacterized"}
STICKINESS_CLASSES = {
    "sheds",
    "moderate",
    "strongly-adhering",
    "uncharacterized",
}

CERAMIC_ANCHORS = {
    "anorthite",
    "mullite",
    "doloma",
    "monocalcium_aluminate_CA",
    "calcium_dialuminate_CA2",
    "calcium_hexaluminate_CA6",
    "dicalcium_silicate_C2S",
    "tricalcium_silicate_C3S",
    "wollastonite",
    "magnesium_aluminate_spinel",
    "forsterite",
    "cordierite_mullite",
    "cmas_glass_ceramic",
}
COMPOSITION_KINDS = {"point-anchor", "window"}
SERVICE_TEMP_KINDS = {"service", "melting-only", "uncharacterized"}


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open() as handle:
        return yaml.safe_load(handle)


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _assert_citations(citations):
    assert isinstance(citations, list)
    assert citations
    assert all(isinstance(citation, str) and citation for citation in citations)


def test_wall_materials_schema_is_fail_closed():
    data = _load_yaml("wall_materials.yaml")
    assert data["schema_version"] == 1
    assert set(data["materials"]) == WALL_MATERIALS

    for entry in data["materials"].values():
        assert {"label", "role", "service_temp", "chemical_attack", "stickiness", "service_life"} <= set(entry)
        service_temp = entry["service_temp"]
        assert {"continuous_C", "max_operating_C", "peak_C", "degradation_onset_C", "evidence", "citations", "note"} <= set(service_temp)
        assert service_temp["evidence"] in EVIDENCE_TAGS

        assert set(entry["chemical_attack"]) == WALL_ATTACK_SPECIES
        for cell in entry["chemical_attack"].values():
            assert {"severity", "evidence", "citations", "note"} <= set(cell)
            assert cell["evidence"] in EVIDENCE_TAGS
            if cell["evidence"] == "uncharacterized":
                assert cell["severity"] is None

        assert set(entry["stickiness"]) == WALL_STICKINESS_SPECIES
        for cell in entry["stickiness"].values():
            assert {"class", "evidence", "citations", "note"} <= set(cell)
            assert cell["class"] in STICKINESS_CLASSES
            assert cell["evidence"] in EVIDENCE_TAGS
            if cell["evidence"] == "uncharacterized":
                assert cell["class"] == "uncharacterized"

        assert entry["service_life"]["evidence"] in EVIDENCE_TAGS


def test_ceramic_types_schema_is_fail_closed():
    data = _load_yaml("ceramic_types.yaml")
    assert data["schema_version"] == 1
    assert set(data["ceramics"]) == CERAMIC_ANCHORS

    for entry in data["ceramics"].values():
        assert {"label", "composition", "service_temp", "liner_suitability"} <= set(entry)

        composition = entry["composition"]
        assert composition["kind"] in COMPOSITION_KINDS
        assert composition["defining_oxides"]
        assert "citations" in composition
        if composition["kind"] == "point-anchor":
            assert "wt_pct" in composition
        else:
            assert "wt_pct_window" in composition

        service_temp = entry["service_temp"]
        assert {"value_C", "kind", "citations", "note"} <= set(service_temp)
        assert service_temp["kind"] in SERVICE_TEMP_KINDS
        if service_temp["kind"] == "uncharacterized":
            assert service_temp["value_C"] is None

        suitability = entry["liner_suitability"]
        assert {"verdict", "citations", "note"} <= set(suitability)
        assert isinstance(suitability["citations"], list)


def test_direct_and_service_cells_have_citations():
    for file_name in ("wall_materials.yaml", "ceramic_types.yaml"):
        data = _load_yaml(file_name)
        for cell in _walk_dicts(data):
            if cell.get("evidence") == "direct":
                _assert_citations(cell.get("citations"))
            if cell.get("kind") == "service":
                _assert_citations(cell.get("citations"))


def test_wall_audit_must_fixes_are_encoded():
    materials = _load_yaml("wall_materials.yaml")["materials"]

    assert materials["fused_silica"]["chemical_attack"]["SiO"]["evidence"] != "direct"
    assert materials["fused_silica"]["stickiness"]["SiO"]["evidence"] != "direct"
    # Audit must-fix: vapor-deposit adhesion class (not just evidence) is uncharacterized
    assert materials["fused_silica"]["stickiness"]["SiO"]["class"] == "uncharacterized"
    assert materials["fused_silica"]["stickiness"]["Fe"]["class"] == "uncharacterized"
    assert materials["doloma"]["stickiness"]["SiO"]["class"] == "uncharacterized"

    assert materials["magnesia"]["stickiness"]["alkali"]["evidence"] != "direct"
    assert materials["magnesia"]["stickiness"]["alkali"]["class"] == "uncharacterized"

    assert materials["bulk_zirconia_ysz"]["stickiness"]["alkali"]["evidence"] != "direct"
    assert materials["bulk_zirconia_ysz"]["stickiness"]["alkali"]["class"] == "uncharacterized"

    assert materials["plasma_sprayed_ysz"]["stickiness"]["SiO"]["evidence"] != "direct"
    assert materials["plasma_sprayed_mullite"]["chemical_attack"]["alkali_NaK"]["evidence"] != "direct"
    assert materials["plasma_sprayed_mullite"]["stickiness"]["alkali"]["evidence"] != "direct"


def test_ceramic_audit_must_fixes_are_encoded():
    ceramics = _load_yaml("ceramic_types.yaml")["ceramics"]

    assert ceramics["forsterite"]["service_temp"]["kind"] != "service"
    assert ceramics["forsterite"]["service_temp"]["kind"] in {"melting-only", "uncharacterized"}

    ca_service = ceramics["monocalcium_aluminate_CA"]["service_temp"]
    assert ca_service["kind"] == "uncharacterized"
    assert ca_service["value_C"] is None
    assert "castable" in ca_service["note"]

    ca2_service = ceramics["calcium_dialuminate_CA2"]["service_temp"]
    assert ca2_service["kind"] == "uncharacterized"
    assert ca2_service["value_C"] is None
    assert "castable" in ca2_service["note"]

    cmas = ceramics["cmas_glass_ceramic"]
    assert cmas["service_temp"]["kind"] == "uncharacterized"
    assert cmas["service_temp"]["value_C"] is None
    assert cmas["liner_suitability"]["verdict"] == "limited-scope"
    assert "not a refractory-liner" in cmas["service_temp"]["note"]
