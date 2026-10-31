from __future__ import annotations

from pathlib import Path

from simulator.battery.migrate import load_yaml, to_plain
from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = (
    REPO_ROOT / "data/literature/extracts/kems-051-allibert-1981.yaml"
)


def test_allibert_table_ii_activity_cells_migrate_from_printed_rows(tmp_path):
    extract = load_yaml(EXTRACT_PATH)
    expected_compositions = [
        ("64.5", "0.645", "0.355"),
        ("57.8", "0.578", "0.422"),
        ("54.8", "0.548", "0.452"),
        ("49.5", "0.495", "0.505"),
        ("43.8", "0.438", "0.562"),
        ("41.4", "0.414", "0.586"),
        ("35.2", "0.352", "0.648"),
        ("80", "0.8", "0.2"),
    ]
    expected_values = {
        "CaO": [
            ("0.42", "0.42"),
            ("0.2", "0.20"),
            ("0.14", "0.14"),
            ("0.082", "0.082"),
            ("0.048", "0.048"),
            ("0.04", "0.040"),
            ("0.025", "0.025"),
            ("1", "1"),
        ],
        "Al2O3": [
            ("0.02", "0.02"),
            ("0.05", ".05"),
            ("0.06", ".06"),
            ("0.2", ".20"),
            ("0.41", ".41"),
            ("0.25", ".25"),
            ("0.5", ".50"),
            ("0.005", ".005"),
        ],
    }
    expected_by_composition = {
        printed_ca: (ca, al)
        for printed_ca, ca, al in expected_compositions
    }
    expected_printed_tokens = {
        formula: [token for _normalized, token in values]
        for formula, values in expected_values.items()
    }
    for formula, expected_tokens in expected_printed_tokens.items():
        points = extract["species"][formula]["observations"][0]["values"]["points"]
        assert [point["activity_as_published"] for point in points] == expected_tokens

    migration_result = _migrate_real_extract(
        tmp_path / "allibert-isolated", EXTRACT_PATH.name
    )
    table_ii = [
        to_plain(observation)
        for observation in migration_result.observations.values()
        if observation.source_id == "kems-051-allibert-1981"
        and observation.locator is not None
        and observation.locator.table == "II"
    ]

    assert len(table_ii) == 16  # 8 printed rows × 2 activity columns.
    assert {row["identity"]["species"]["formula"] for row in table_ii} == {
        "CaO",
        "Al2O3",
    }
    assert all(row["identity"]["quantity"]["value"] == "activity" for row in table_ii)
    assert all(row["identity"]["temperature_K"]["value"] == "2060" for row in table_ii)
    assert all(row["locator"]["pdf_page_index"] == 2 for row in table_ii)

    observed = {"CaO": [], "Al2O3": []}
    for row in table_ii:
        formula = row["identity"]["species"]["formula"]
        composition = row["identity"]["composition"]["value"]["components"]
        composition_by_formula = dict(composition)
        printed_ca = next(
            printed for printed, (ca, _al) in expected_by_composition.items()
            if composition_by_formula == {"CaO": ca, "Al2O3": _al}
        )
        row_index = [entry[0] for entry in expected_compositions].index(printed_ca)
        normalized_value, _printed_token = expected_values[formula][row_index]
        assert row["value"]["point"] == normalized_value
        phase = row["identity"]["species"]["phase"]
        if printed_ca == "80":
            assert phase["tag"] == "unknown"
            assert "bulk_composition_in_two_phase_region" in phase["reason"]
        else:
            assert phase == {"tag": "value", "value": "l"}

        conditions = row["point_conditions"]
        assert set(conditions) == {"composition", "fO2_Pa", "temperature_K"}
        assert all(
            "state" in condition and "locator" in condition
            for condition in conditions.values()
        )
        assert row["point_conditions"]["composition"]["state"]["tag"] == "value"
        assert row["point_conditions"]["temperature_K"]["state"]["value"] == "2060"
        assert row["point_conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"
        ref = row["identity"]["reference_state"]["value"]
        assert ref["endmember"]["phase"]["value"] == "cr"
        if formula == "CaO":
            assert ref["endmember"]["polymorph"]["tag"] == "unknown"
        else:
            assert ref["endmember"]["polymorph"]["value"] == "corundum"
        observed[formula].append((printed_ca, row["value"]["point"]))

    for formula in expected_values:
        assert observed[formula] == list(
            zip(
                [entry[0] for entry in expected_compositions],
                [normalized for normalized, _printed in expected_values[formula]],
            )
        )

    assert sorted(
        (issue.path, issue.reason.value, issue.detail)
        for issue in migration_result.validation.hard_issues
    ) == [
        (
            "observation[kems-051-allibert-1981::allibert_1981_table2_alumina_activity_kems].derivation.inputs",
            "conditional_field",
            "derived_from 'melt/reference Al+ and O+ ion-intensity ratios; input intensities not published' is prose, not a resolvable observation id; retained here, never minted as a pointer",
        ),
        (
            "observation[kems-051-allibert-1981::allibert_1981_table2_cao_activity_kems].derivation.inputs",
            "conditional_field",
            "derived_from 'melt/reference Ca+ and O+ ion-intensity ratios; input intensities not published' is prose, not a resolvable observation id; retained here, never minted as a pointer",
        ),
    ]
    assert extract["species"]["CaO"]["observations"][0]["values"][
        "standard_state_as_printed"
    ] == "Activities are referred to solid standard states."
    alumina = extract["species"]["Al2O3"]["observations"][0]["values"]
    assert alumina["standard_state_reference_as_printed"] == "pure alpha-Al2O3"
    assert alumina["mass_spectrometric_alumina_scale_caveat"]["text"] == (
        "A critical evaluation of these mass-spectrometric data in the light of emf "
        "measurements reported in the next section and the other data reported in "
        "the literature (Section IV) also indicate that the activities of CaO "
        "obtained in this study are more reliable than the values for Al2O3."
    )
    assert alumina["mass_spectrometric_alumina_scale_caveat"]["locator"][
        "published_page"
    ] == 309
    caveat_locator = alumina["mass_spectrometric_alumina_scale_caveat"]["locator"]
    assert caveat_locator["table"] == "II"
    assert caveat_locator["section"] == "(3) Analysis and Results"
