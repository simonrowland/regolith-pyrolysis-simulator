from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Polymorph
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "schumm-1981-alkali-aluminosilicate-thermochemical-tables.yaml"
)


def test_schumm_key_phase_tables_match_rendered_cells_and_row_counts() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }
    assert all("attribution" in row["values"] for row in observations.values())
    assert all(
        "primary_sources_as_printed" not in row["values"]
        for row in observations.values()
    )

    rendered_cells = (
        ("NaAlSiO4", "schumm81_nepheline_cp", 19, 298, "cp", 26.21),
        ("NaAlSiO4", "schumm81_nepheline_delta_fH_beta", 19, 1000, "delta_fH", -502.75),
        ("NaAlSi3O8", "schumm81_albite_low_cp", 24, 300, "cp", 49.21),
        ("NaAlSi3O8", "schumm81_albite_high_delta_fG", 25, 1391, "delta_fG", -694.95),
        ("NaAlSi2O6", "schumm81_jadeite_delta_fG", 21, 800, "delta_fG", -609.47),
        ("KAlSiO4", "schumm81_kaliophilite_cp", 28, 810, "cp", 42.68),
        ("KAlSiO4", "schumm81_kaliophilite_cp_beta", 28, 810, "cp", 42.50),
        ("KAlSi2O6", "schumm81_leucite_delta_fH", 29, 955, "delta_fH", -720.07),
        ("KAlSi3O8", "schumm81_microcline_delta_fG", 30, 1400, "delta_fG", -693.91),
        ("KAlSi3O8", "schumm81_sanidine_delta_fH", 31, 1423, "delta_fH", -943.46),
    )
    for species, observation_id, page, temperature, field, value in rendered_cells:
        assert species in document["species"]
        row = observations[observation_id]
        assert row["locator"]["page"] == page
        assert any(
            point["T_K"] == temperature and point[field] == value
            for point in row["values"]["series"]
        ), (species, observation_id, temperature, field, value)

    cp_counts = {
        observation_id: sum(
            len(row["values"]["series"])
            for key, row in observations.items()
            if key == observation_id or key.startswith(f"{observation_id}_")
        )
        for observation_id in (
            "schumm81_nepheline_cp",
            "schumm81_albite_low_cp",
            "schumm81_albite_high_cp",
            "schumm81_jadeite_cp",
            "schumm81_kaliophilite_cp",
            "schumm81_leucite_cp",
            "schumm81_microcline_cp",
            "schumm81_sanidine_cp",
        )
    }
    assert cp_counts == {
        "schumm81_nepheline_cp": 18,
        "schumm81_albite_low_cp": 13,
        "schumm81_albite_high_cp": 14,
        "schumm81_jadeite_cp": 11,
        "schumm81_kaliophilite_cp": 19,
        "schumm81_leucite_cp": 21,
        "schumm81_microcline_cp": 13,
        "schumm81_sanidine_cp": 14,
    }

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind.value != "unavailable" for row in migrated)
    assert all(not row.identity.quantity.is_unknown for row in migrated)
    assert all(not row.identity.species.phase.is_unknown for row in migrated)
    assert all(row.identity.species.phase.value == "cr" for row in migrated)
    assert all(row.evidence.attribution is not None for row in migrated)
    assert all(
        row.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        for row in migrated
    )
    assert Counter(row.identity.quantity.value for row in migrated) == {
        "cp": 123,
        "H_minus_H298": 123,
        "delta_fH": 123,
        "delta_fG": 123,
        "S": 8,
    }
    assert len(migrated) == 500

    def source_points(observation_id: str):
        marker = f"{document['source_id']}::{observation_id}::T="
        return [row for row in migrated if row.observation_id.startswith(marker)]

    def cp_at(observation_id: str, temperature: str):
        return next(
            row
            for row in source_points(observation_id)
            if row.identity.quantity.value == "cp"
            and str(row.point_conditions["temperature_K"].state.value) == temperature
        )

    for stem in (
        "schumm81_nepheline_cp",
        "schumm81_nepheline_heat_content",
        "schumm81_nepheline_delta_fH",
        "schumm81_nepheline_delta_fG",
    ):
        for suffix, polymorph, count in (
            ("", Polymorph.ALPHA, 4),
            ("_beta", Polymorph.BETA, 9),
            ("_gamma", Polymorph.GAMMA, 5),
        ):
            rows = source_points(f"{stem}{suffix}")
            assert len(rows) == count
            assert all(
                row.identity.species.polymorph.value is polymorph for row in rows
            )

    for stem in (
        "schumm81_kaliophilite_cp",
        "schumm81_kaliophilite_H_minus_H298",
        "schumm81_kaliophilite_delta_fH",
        "schumm81_kaliophilite_delta_fG",
    ):
        for suffix, polymorph, count in (
            ("", Polymorph.ALPHA, 8),
            ("_beta", Polymorph.BETA, 11),
        ):
            rows = source_points(f"{stem}{suffix}")
            assert len(rows) == count
            assert all(
                row.identity.species.polymorph.value is polymorph for row in rows
            )

    for observation_id in ("schumm81_nepheline_S298", "schumm81_kaliophilite_S298"):
        assert all(
            row.identity.species.polymorph.value is Polymorph.ALPHA
            for row in source_points(observation_id)
        )

    boundaries = (
        ("schumm81_nepheline_cp", "467", "169.24280", Polymorph.ALPHA),
        ("schumm81_nepheline_cp_beta", "467", "142.71624", Polymorph.BETA),
        ("schumm81_nepheline_cp_beta", "1180", "190.07912", Polymorph.BETA),
        ("schumm81_nepheline_cp_gamma", "1180", "178.53128", Polymorph.GAMMA),
        ("schumm81_kaliophilite_cp", "810", "178.57312", Polymorph.ALPHA),
        ("schumm81_kaliophilite_cp_beta", "810", "177.8200", Polymorph.BETA),
    )
    for observation_id, temperature, value, polymorph in boundaries:
        row = cp_at(observation_id, temperature)
        assert str(row.value.point) == value
        assert row.identity.species.polymorph.value is polymorph

    high_albite = [
        row
        for row in migrated
        if "::schumm81_albite_high_" in row.observation_id
    ]
    assert high_albite
    assert all(
        row.identity.species.polymorph.value is Polymorph.ALBITE_HIGH
        for row in high_albite
    )

    annotated = source_points("schumm81_nepheline_delta_fH")
    anchor = next(
        row
        for row in annotated
        if str(row.point_conditions["temperature_K"].state.value) == "298"
    )
    assert anchor.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
    assert "53KRA/NEU" in (anchor.evidence.attribution or "")

    for observation_id in (
        "schumm81_albite_low_cp",
        "schumm81_jadeite_cp",
        "schumm81_leucite_cp",
        "schumm81_microcline_cp",
        "schumm81_sanidine_cp",
    ):
        rows = source_points(observation_id)
        assert rows
        assert all(row.identity.species.polymorph.is_unknown for row in rows)
