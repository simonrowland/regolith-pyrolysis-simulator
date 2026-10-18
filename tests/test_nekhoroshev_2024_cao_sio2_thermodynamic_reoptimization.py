from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


EXTRACT_NAME = "nekhoroshev-2024-cao-sio2-thermodynamic-reoptimization.yaml"
SOURCE_ID = "nekhoroshev-2024-cao-sio2-thermodynamic-reoptimization"


def _walk_mappings(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def test_nekhoroshev_2024_phase_equilibria_and_review_fixes_migrate(
    tmp_path: Path,
) -> None:
    extract_path = Path("data/literature/extracts") / EXTRACT_NAME
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    source_rows = {
        row["observation_id"]: row
        for species in extract["species"].values()
        for row in species.get("observations", [])
    }

    # Every explicit PDF page index uses the zero-based locator field.
    for locator in _walk_mappings(extract):
        if "pdf_page_index" in locator and "page" in locator:
            assert locator["pdf_page_index"] == locator["page"] - 1

    # Table 3, PDF page 4 (published page 33689): literals checked against the paper.
    page_literals = {
        "nk24_cs1_liquid_cao": Decimal("36.1"),
        "nk24_cs7_alpha_c2s_sio2": Decimal("33.2"),
        "nk24_cs8_slag2_cao": Decimal("1.0"),
    }
    for observation_id, expected in page_literals.items():
        row = source_rows[observation_id]
        assert Decimal(str(row["values"]["value"])) == expected
        assert row["locator"]["published_page"] == 33689
        assert row["locator"]["page"] == 4
        assert row["locator"]["pdf_page_index"] == 3

    cs8_liquidus_literals = {
        "nk24_cs8_slag1_cristobalite_liquidus": (27.2, 72.8),
        "nk24_cs8_slag2_cristobalite_liquidus": (1.0, 99.0),
    }
    for observation_id, (cao, sio2) in cs8_liquidus_literals.items():
        row = source_rows[observation_id]
        assert row["values"]["composition_as_published"] == {
            "CaO_mol_percent": cao,
            "SiO2_mol_percent": sio2,
        }
        assert row["values"]["points"][0]["T_C"] == 1697
        assert row["values"]["points"][0]["point_conditions"][
            "saturation_phase_as_printed"
        ]["state"]["value"]["printed_phase"] == "Cristobalite"
        assert "nk24_cs8_cristobalite_composition_unavailable" in row["locator"][
            "note"
        ]

    table7 = {
        row["observation_id"]: row
        for row in extract["species"]["CaO"]["observations"]
        if row["observation_id"].startswith("nk24_t7_")
    }
    methodology = {
        "nk24_t7_kanolt_1913_melting_point": "H2 atmosphere",
        "nk24_t7_schumacher_1926_melting_point": (
            "optical pyrometer, H2 atmosphere, tungsten crucible"
        ),
        "nk24_t7_doman_1963_melting_point": (
            "optical pyrometer, N2 atmosphere, crucible material not indicated"
        ),
        "nk24_t7_panek_1979_melting_point": (
            "reducing atmosphere, optical pyrometer"
        ),
        "nk24_t7_foex_1968_melting_point": (
            "solar/plasma furnaces, optical pyrometer"
        ),
        "nk24_t7_foex_1965_melting_point": (
            "solar furnace, optical pyrometer"
        ),
        "nk24_t7_traverse_1969_melting_point": (
            "solar furnace, optical pyrometer, air atmosphere"
        ),
        "nk24_t7_yamada_1986_melting_point": "arc imaging furnace",
    }
    for observation_id, printed_method in methodology.items():
        row = table7[observation_id]
        assert row["locator"]["note"] == (
            f"Table 7 methodology as printed: {printed_method}"
        )
        assert "methodology_as_printed" not in row["values"]

    cao_context = {
        row["observation_id"]: row
        for row in extract["species"]["CaO"]["context"]
    }
    sio2_context = {
        row["observation_id"]: row
        for row in extract["species"]["SiO2"]["context"]
    }
    assert [
        row["value"]
        for row in cao_context[
            "nk24_discussion_calcium_silicate_entropies_quoted"
        ]["values"]["rows"]
    ] == [120.5, 127.65, 210.9, 81.69, 131.40]
    formation_from_oxides = cao_context[
        "nk24_discussion_formation_from_oxides_quoted"
    ]["values"]
    assert "delta_fH" not in formation_from_oxides
    assert formation_from_oxides["quantity_as_printed"] == (
        "Enthalpy of formation from CaO + SiO2"
    )
    transition_enthalpy = cao_context[
        "nk24_model_wollastonite_transition_enthalpy"
    ]
    assert transition_enthalpy["values"]["method_class"] == "model_derived"
    assert transition_enthalpy["values"]["value"] == 5.96
    assert cao_context[
        "nk24_discussion_wollastonite_transition_quoted"
    ]["values"]["rows"][2]["approximate"] is True
    assert sio2_context[
        "nk24_discussion_silica_supercooled_heat_capacity_quoted"
    ]["values"]["rows"][0]["value"] == 81.4

    migrated = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    rows = {
        row.observation_id.split("::", 1)[-1].split("::", 1)[0]: row
        for row in migrated.observations.values()
        if row.source_id == SOURCE_ID
    }
    assert len(rows) == 45
    assert migrated.validation.hard_issues == ()

    for observation_id, expected in page_literals.items():
        assert rows[observation_id].value.point == expected
        assert rows[observation_id].locator.published_page == 33689
    for row in rows.values():
        if row.identity.quantity.value.value == "transition_temperature":
            assert row.value.point is not None
        else:
            assert row.identity.temperature_K.is_value

    for observation_id, (cao, sio2) in cs8_liquidus_literals.items():
        row = rows[observation_id]
        assert row.value.point == Decimal("1970.15")
        assert row.identity.composition.is_value
        assert row.identity.composition.value.as_map() == {
            "CaO": Decimal(str(cao / 100)),
            "SiO2": Decimal(str(sio2 / 100)),
        }
        assert row.quench_state.value == "not_stated"
    assert rows["nk24_cs5_slag_cao"].quench_state.value == "asserted_altered"
    assert rows["nk24_cs7_slag_cao"].quench_state.value == "asserted_altered"
    assert rows["nk24_cs5_slag_cao"].point_conditions[
        "quench_state"
    ].locator.figure == "1"
    assert rows["nk24_cs7_slag_cao"].point_conditions[
        "quench_state"
    ].locator.figure == "1"

    for observation_id in methodology:
        assert methodology[observation_id] in rows[observation_id].locator.note
