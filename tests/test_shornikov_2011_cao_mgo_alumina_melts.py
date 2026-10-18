"""Render pins and migration checks for Shornikov et al. (2011)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from simulator.battery.score import _printed_uncertainty_band
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2011-cao-mgo-alumina-melts.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [row for block in document["species"].values() for row in block["observations"]]


def _observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


# Existing axis fit. y = intercept + slope × page_y.
_Y_AXIS = {
    "a": (Decimal("-4.2411374"), Decimal("0.00132274")),
    "b": (Decimal("-4.1042237"), Decimal("0.00095057")),
    "c": (Decimal("-6.8882809"), Decimal("0.00219729")),
    "d": (Decimal("73.4271845"), Decimal("-0.00970874")),
}

# species, id, panel, read A (outer-square centre), read B, value field, stored value,
# uncertainty key, width, note ordinate
_FRESH_READS = (
    ("CaO", "shornikov_2011_fig1_a_allibert_marker_01", "a", ("1401.5", "3435.5"), ("1400.26", "3435.11"), "activity", "0.498", "read_uncertainty_dex", "0.000366", "0.302878"),
    ("CaO", "shornikov_2011_fig1_a_allibert_marker_02", "a", ("1479.5", "3507.5"), ("1478.31", "3506.95"), "activity", "0.400", "read_uncertainty_dex", "0.000434", "0.398009"),
    ("CaO", "shornikov_2011_fig1_a_allibert_marker_03", "a", ("1561.5", "3603.5"), ("1560.36", "3602.74"), "activity", "0.299", "read_uncertainty_dex", "0.001028", "0.524854"),
    ("CaO", "shornikov_2011_fig1_a_allibert_marker_04", "a", ("1657.5", "3735.5"), ("1656.42", "3734.44"), "activity", "0.200", "read_uncertainty_dex", "0.000988", "0.699257"),
    ("CaO", "shornikov_2011_fig1_a_allibert_marker_05", "a", ("1841.5", "3963.0"), ("1840.53", "3961.93"), "activity", "0.100", "read_uncertainty_dex", "0.000882", "1.000174"),
    ("MgO", "shornikov_2011_fig1_b_allibert_marker_01", "b", ("1373.5", "4737.5"), ("1372.24", "4736.19"), "activity", "0.400", "read_uncertainty_dex", "0.001162", "0.398479"),
    ("MgO", "shornikov_2011_fig1_b_allibert_marker_02", "b", ("1513.5", "4869.5"), ("1512.33", "4867.9"), "activity", "0.299", "read_uncertainty_dex", "0.001273", "0.523816"),
    ("MgO", "shornikov_2011_fig1_b_allibert_marker_03", "b", ("1825.5", "5053.5"), ("1824.52", "5051.49"), "activity", "0.200", "read_uncertainty_dex", "0.001399", "0.698526"),
    ("Al2O3", "shornikov_2011_fig1_c_allibert_marker_03", "c", ("2994.5", "3599.5"), ("2993.23", "3598.75"), "activity", "0.0955", "read_uncertainty_dex", "0.000868", "1.020040"),
    ("Al2O3", "shornikov_2011_fig1_c_allibert_marker_04", "c", ("3130.5", "3463.5"), ("3129.31", "3463.05"), "activity", "0.190", "read_uncertainty_dex", "0.000787", "0.721539"),
    ("Al2O3", "shornikov_2011_fig1_c_allibert_marker_05", "c", ("3290.5", "3388.0"), ("3289.41", "3387.22"), "activity", "0.278", "read_uncertainty_dex", "0.001532", "0.555281"),
    ("Al2O3", "shornikov_2011_fig1_c_allibert_marker_06", "c", ("3482.5", "3332.5"), ("3481.52", "3331.35"), "activity", "0.369", "read_uncertainty_dex", "0.001313", "0.432925"),
    ("CaO-MgO-Al2O3_melt", "shornikov_2011_fig1_d_allibert_marker_02", "d", ("2722.5", "5065.5"), ("2721.06", "5063.46"), "value", "-24.26", "read_uncertainty_kJ_mol", "0.01244", "24.257465"),
)

_UNCHANGED_MARKERS = (
    "shornikov_2011_fig1_b_allibert_marker_04",
    "shornikov_2011_fig1_c_allibert_marker_01",
    "shornikov_2011_fig1_c_allibert_marker_02",
    "shornikov_2011_fig1_d_allibert_marker_01",
    "shornikov_2011_fig1_d_allibert_marker_03",
    "shornikov_2011_fig1_d_allibert_marker_04",
)


def _migrated() -> list:
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return list(migrator.result.observations.values())


def _ordinate_distance(panel: str, y_px: str, point: Decimal) -> Decimal:
    intercept, slope = _Y_AXIS[panel]
    plotted = intercept + slope * Decimal(str(y_px))
    if panel == "d":
        return abs((-plotted) - point)
    candidate = Decimal(10) ** (-plotted)
    return abs((candidate / point).ln() / Decimal(10).ln())


def _assert_printed_band_contains_both_reads(observation, panel: str, pixels, width: str) -> None:
    resolution = _printed_uncertainty_band(
        observation.identity.quantity.value,
        observation.uncertainty,
        observation.value.point,
        source_observation=observation,
    )
    assert resolution.refusal is None
    assert resolution.band is not None
    assert resolution.band.value == Decimal(width)
    assert resolution.band.value > 0
    # Read A is the outer-square centre. The stored width itself has to contain
    # that centre and both recorded ordinates after the value is rounded.
    for _x_px, y_px in pixels:
        distance = _ordinate_distance(panel, y_px, observation.value.point)
        assert distance <= resolution.band.value


def _render_pins(document: dict) -> None:
    observations = _observations(document)
    table1 = [row for row in observations if "_p001_t01_" in row["observation_id"]]
    assert len(table1) == 32, "Table 1 has 11 solid, 7 liquid, and 14 gas rows"
    assert _observation(
        document,
        "Al2O3",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r03_c02",
    )["values"]["value"] == "-1046.788"
    assert _observation(
        document,
        "CaO",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r04_c02",
    )["values"]["value"] == "-425.847"
    assert _observation(
        document,
        "O3",
        "shornikov_2011_cao_mgo_alumina_melts_p001_t01_r16_c06",
    )["values"]["value"] == "271.522"

    marker_pins = (
        ("CaO", "a", "0.498", "(1401.5, 3435.5)", "(1400.26, 3435.11)", "0.302878", "0.000366"),
        ("MgO", "b", "0.400", "(1373.5, 4737.5)", "(1372.24, 4736.19)", "0.398479", "0.001162"),
        ("Al2O3", "c", 0.010, "(2691, 4043)", "(2689, 4043)", "1.9954", None),
    )
    for species, panel, activity, first_pixel, second_pixel, log_ordinate, width in marker_pins:
        row = _observation(
            document,
            species,
            f"shornikov_2011_fig1_{panel}_allibert_marker_01",
        )
        assert row["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "1960"
        assert row["values"]["activity"] == activity
        assert row["values"]["method_class"] == "figure_only"
        assert first_pixel in row["locator"]["note"]
        assert second_pixel in row["locator"]["note"]
        assert log_ordinate in row["locator"]["note"]
        if width is None:
            assert "read_uncertainty_dex" not in row.get("uncertainty", {})
        else:
            assert row["uncertainty"]["read_uncertainty_dex"] == width

    minimum = _observation(
        document,
        "CaO-MgO-Al2O3_melt",
        "shornikov_2011_text_minimum_delta_fG1960_melt",
    )
    assert minimum["values"]["value_as_printed"] == "−31.5"
    assert minimum["values"]["value"] == "-31.5"
    assert minimum["values"]["method_class"] == "quoted_attributed"
    assert minimum["values"]["composition_Al2O3_mole_pct_as_printed"] == "45"
    assert minimum["values"]["T_K"] == "1960"
    assert minimum["phase"] == "l"
    assert "from oxides" in minimum["standard_state"]
    assert (
        minimum["point_conditions"]["composition"]["state"]["reason"]
        == "incomplete_melt_composition"
    )
    assert document["species"]["Ca3MgAl4O10"]["context"][0]["values"]["concentration_mole_pct"] == {
        "Al2O3": "33.3",
    }


def test_tables_and_figure_markers_match_the_render_pins() -> None:
    document = _document()
    observations = _observations(document)
    assert len(observations) == 60
    assert len(document["species"]["MgO"]["context"]) == 12
    table2 = document["species"]["MgO"]["context"]
    assert table2[0]["values"]["concentration_wt_pct"] == {
        "CaO": "52.84",
        "MgO": "7.16",
        "Al2O3": "40.00",
    }
    assert table2[-1]["values"]["concentration_mole_pct"] == {
        "CaO": "31.53",
        "MgO": "18.46",
        "Al2O3": "50.00",
    }
    markers = [row for row in observations if "_allibert_marker_" in row["observation_id"]]
    unavailable = [row for row in observations if row["values"].get("unavailable_reason")]
    assert len(markers) == 19
    assert len(observations) == 60
    assert len(unavailable) == 8
    fresh_ids = {spec[1] for spec in _FRESH_READS}
    assert fresh_ids.isdisjoint(_UNCHANGED_MARKERS)
    for row in markers:
        uncertainty = row.get("uncertainty", {})
        if row["observation_id"] in fresh_ids:
            widths = [
                uncertainty.get("read_uncertainty_dex"),
                uncertainty.get("read_uncertainty_kJ_mol"),
            ]
            present = [width for width in widths if width is not None]
            assert len(present) == 1
            assert Decimal(str(present[0])) > 0
        else:
            assert row["observation_id"] in _UNCHANGED_MARKERS
            assert "read_uncertainty_dex" not in uncertainty
            assert "read_uncertainty_kJ_mol" not in uncertainty
    assert len(document["species"]["Al2O3"]["context"]) == 1
    assert check_all_fidelity_samples_match(document) == []
    _render_pins(document)


def test_render_pins_fail_when_a_digitized_value_is_mutated() -> None:
    document = deepcopy(_document())
    row = _observation(
        document,
        "MgO",
        "shornikov_2011_fig1_b_allibert_marker_01",
    )
    row["values"]["activity"] = 0.41
    with pytest.raises(AssertionError):
        _render_pins(document)


def test_render_pins_fail_when_migrated_evidence_class_is_mutated() -> None:
    document = deepcopy(_document())
    row = _observation(
        document,
        "Al2O3",
        "shornikov_2011_fig1_c_allibert_marker_01",
    )
    row["values"]["method_class"] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _render_pins(document)


def test_fresh_reads_lie_inside_the_printed_uncertainty_band() -> None:
    document = _document()
    migrated = {
        row.observation_id.split("::")[-1]: row for row in _migrated()
    }
    with localcontext() as ctx:
        ctx.prec = 50
        for spec in _FRESH_READS:
            (
                species,
                observation_id,
                panel,
                first,
                second,
                field,
                stored,
                key,
                width,
                ordinate,
            ) = spec
            row = _observation(document, species, observation_id)
            assert row["values"][field] == stored
            assert row["values"]["method_class"] == "figure_only"
            assert row["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "1960"
            assert f"({first[0]}, {first[1]})" in row["locator"]["note"]
            assert f"({second[0]}, {second[1]})" in row["locator"]["note"]
            assert ordinate in row["locator"]["note"]
            assert str(row["uncertainty"][key]) == width
            _assert_printed_band_contains_both_reads(
                migrated[observation_id],
                panel,
                (first, second),
                width,
            )


def test_printed_band_rejects_a_1e_9_width() -> None:
    migrated = {
        row.observation_id.split("::")[-1]: row for row in _migrated()
    }
    observation = migrated["shornikov_2011_fig1_a_allibert_marker_01"]
    verbatim = dict(observation.uncertainty.verbatim)
    verbatim["read_uncertainty_dex"] = "1e-9"
    narrowed = replace(
        observation,
        uncertainty=replace(observation.uncertainty, verbatim=verbatim),
    )
    with localcontext() as ctx:
        ctx.prec = 50
        with pytest.raises(AssertionError):
            _assert_printed_band_contains_both_reads(
                narrowed,
                "a",
                (("1401.5", "3435.5"), ("1400.26", "3435.11")),
                "1e-9",
            )


def test_isolated_migration_preserves_temperature_conditions_and_unavailable_curves() -> None:
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = list(result.observations.values())

    assert len(observations) == 60
    assert sum(row.admission.status.value == "admitted" for row in observations) == 52
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 8
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    minimum = next(
        row
        for row in observations
        if row.observation_id.endswith("::shornikov_2011_text_minimum_delta_fG1960_melt")
    )
    assert minimum.admission.status.value == "admitted"
    assert minimum.value.kind is ValueKind.POINT
    assert minimum.identity.temperature_K is not None
    assert minimum.point_conditions["composition"].state.is_unknown
    assert minimum.point_conditions["composition"].locator is not None
    for row in observations:
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        condition = row.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None

    for row in observations:
        if "fig1_" in row.observation_id:
            assert {"composition", "total_pressure_Pa", "fO2_Pa"} <= set(
                row.point_conditions
            )
            assert row.point_conditions["composition"].locator is not None
