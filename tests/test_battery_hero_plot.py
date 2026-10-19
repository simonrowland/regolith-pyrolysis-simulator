from __future__ import annotations

import gzip
import io
import json
import re
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import NoticeKind
from scripts.battery_hero_plot import (
    HERO_MINOR_FACETS,
    HERO_RAILS,
    _draw_hero_current,
    _draw_hero_sweet_spot,
    _history_record,
    _hero_engine_colors,
    _hero_facets,
    _hero_legend_handles,
    _hero_refusal_entries,
    _refusal_coverage_from_summaries,
    _parser,
    append_history,
    draw_panel,
    element_tier,
    element_view_series,
    history_record_from_archive,
    load_element_abundance,
    read_scores,
    render_hero_charts,
    render_plots,
    species_tier,
    stamp_text,
    style_for_tier,
    TIER_MAJOR,
    TIER_MINOR,
    TIER_TRACE,
)
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from matplotlib.text import Text
import matplotlib.pyplot as plt


def _record(
    *,
    engine: str,
    experiment: str,
    formula: str,
    temperature_K: int,
    residual: float | None,
    rail: str = "vapour",
    authority: str = "certified",
    status: str = "match",
    version: str = "1.2.3",
    source_relation: str = "independent",
) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_request": {"engine": engine, "experiment_id": experiment},
        "candidate_observation": {
            "authority": authority,
            "engine": {"name": engine, "version": version},
            "experiment_id": experiment,
            "identity": {
                "species": {"formula": formula},
                "temperature_K": {"tag": "value", "value": str(temperature_K)},
            },
            "notices": [],
        },
        "rail": rail,
        "refusal": None,
        "source_relation": source_relation,
        "status": status,
    }
    if residual is not None:
        row["numeric"] = {"operation": "dex", "unit": "dimensionless", "value": residual}
    if status == "refused":
        row["refusal"] = {"reason": "unsupported_species"}
    return row


def _score_file(path: Path) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"},
        _record(engine="openimcc", experiment="e1", formula="SiO2", temperature_K=1001, residual=0.1),
        _record(engine="openimcc", experiment="e1", formula="SiO2", temperature_K=1001, residual=0.3),
        _record(engine="openimcc", experiment="e2", formula="SiO2", temperature_K=1040, residual=0.4),
        _record(engine="openimcc", experiment="e3", formula="SiO2", temperature_K=1110, residual=0.5),
        _record(engine="openimcc", experiment="e4", formula="SiO2", temperature_K=1120, residual=0.7),
        _record(engine="openimcc", experiment="e5", formula="SiO2", temperature_K=1140, residual=0.9),
        _record(engine="openimcc", experiment="e6", formula="SiO2", temperature_K=1200, residual=0.8),
        _record(engine="openimcc", experiment="e7", formula="SiO2", temperature_K=1220, residual=1.0),
        _record(engine="openimcc", experiment="e8", formula="SiO2", temperature_K=1240, residual=1.2),
        _record(engine="openimcc", experiment="ea3", formula="SiO2", temperature_K=1110, residual=0.2, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea4", formula="SiO2", temperature_K=1120, residual=0.4, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea5", formula="SiO2", temperature_K=1140, residual=0.6, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea6", formula="SiO2", temperature_K=1200, residual=0.6, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea7", formula="SiO2", temperature_K=1220, residual=0.8, rail="melt_activity"),
        _record(engine="openimcc", experiment="ea8", formula="SiO2", temperature_K=1240, residual=1.0, rail="melt_activity"),
        _record(
            engine="openimcc",
            experiment="ea6",
            formula="SiO2",
            temperature_K=1200,
            residual=0.2,
            rail="melt_activity",
            authority="extrapolated",
        ),
        _record(engine="openimcc", experiment="ea9", formula="SiO2", temperature_K=1260, residual=1.2, rail="melt_activity"),
        _record(
            engine="openimcc",
            experiment="e9",
            formula="P2O5",
            temperature_K=1200,
            residual=0.4,
            rail="melt_activity",
            source_relation="quoted_overlap",
        ),
        _record(
            engine="openimcc",
            experiment="e10",
            formula="Ag",
            temperature_K=1220,
            residual=0.6,
        ),
        _record(
            engine="magemin",
            experiment="e-refused",
            formula="SiO2",
            temperature_K=1000,
            residual=None,
            status="refused",
        ),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_binning_averages_same_experiment_duplicates_and_excludes_refusals(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)

    data = read_scores(residuals)

    assert data.engines == ("openimcc", "magemin")
    series = data.series[("openimcc", "SiO2", "vapour")]
    points = {point.bin_index: point for point in series}
    assert points[10].residual_dex == pytest.approx(0.3)
    assert points[10].n == 2  # two experiments; the same-experiment copy counts once
    assert points[11].residual_dex == pytest.approx(0.7)
    assert points[11].n == 3
    assert points[12].residual_dex == pytest.approx(1.0)
    assert points[12].n == 3
    assert data.summaries["openimcc"].scored_rows == 19
    assert data.summaries["openimcc"].unique_points == 17
    assert data.summaries["openimcc"].refusals == {}
    assert data.summaries["magemin"].scored_rows == 0
    assert data.summaries["magemin"].refusals == {}
    assert data.summaries["magemin"].not_observable_by_rail["vapour"] == 1
    assert data.summaries["openimcc"].source_relations["quoted_overlap"] == 1
    activity_bin = {
        point.bin_index: point
        for point in data.series[("openimcc", "SiO2", "melt_activity")]
    }[12]
    assert activity_bin.residual_dex == pytest.approx(0.85)
    assert activity_bin.n == 4
    assert activity_bin.flagged
    element_series = element_view_series(data)
    element_points = {point.bin_index: point for point in element_series[("openimcc", "Si")]}
    assert element_points[10].residual_dex == pytest.approx(0.3)
    assert element_points[10].n == 2
    assert element_points[11].residual_dex == pytest.approx(0.55)
    assert element_points[11].n == 6
    assert element_points[12].residual_dex == pytest.approx(6.4 / 7)
    assert element_points[12].n == 7
    assert element_points[12].flagged


def test_refusal_status_legacy_spellings_are_counted(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, status in enumerate(("refused", "typed-refusal", "typed_refusal")):
        row = _record(
            engine="openimcc",
            experiment=f"refused-{index}",
            formula="SiO2",
            temperature_K=1000,
            residual=None,
        )
        row["status"] = status
        row["refusal"] = {"reason": f"reason-{index}"}
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    data = read_scores(residuals)

    assert data.summaries["openimcc"].refusals == {
        "reason-0": 1,
        "reason-1": 1,
        "reason-2": 1,
    }


def test_low_count_bins_are_markers_and_flagged_rows_are_hollow(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    abundance = load_element_abundance("lunar_mare_low_ti")
    figure, axis = plt.subplots()
    try:
        right_axis = draw_panel(axis, data, "openimcc", abundance)
        series_gid = "series:openimcc:SiO2:vapour"
        series_lines = [line for line in axis.lines if line.get_gid() == series_gid]
        assert len(series_lines) == 1
        assert len(series_lines[0].get_xdata()) == 2
        assert list(series_lines[0].get_xdata()) == pytest.approx([1123.3333333333, 1220.0])
        activity_gid = "series:openimcc:SiO2:melt_activity"
        activity_lines = [line for line in axis.lines if line.get_gid() == activity_gid]
        assert len(activity_lines) == 1
        assert activity_lines[0].get_linestyle() == "--"
        assert not right_axis.get_yticklabels()
        assert any(
            artist.get_gid() == f"low-count-marker:{series_gid}"
            for artist in axis.collections
        )
        flagged_gid = "binned-marker:series:openimcc:SiO2:melt_activity"
        flagged = next(
            artist
            for artist in axis.collections
            if artist.get_gid() == flagged_gid
            and float(artist.get_offsets()[0][0]) > 1200
        )
        assert len(flagged.get_facecolors()) == 0
    finally:
        plt.close(figure)

    element_figure, element_axis = plt.subplots()
    try:
        draw_panel(element_axis, data, "openimcc", abundance, view="element")
        si_gid = "series:openimcc:Si:pooled:element"
        si_lines = [line for line in element_axis.lines if line.get_gid() == si_gid]
        assert len(si_lines) == 1
        assert si_lines[0].get_linestyle() == "--"
        flagged_element_markers = [
            artist
            for artist in element_axis.collections
            if artist.get_gid() == f"binned-marker:{si_gid}"
            and float(artist.get_offsets()[0][0]) > 1200
        ]
        assert len(flagged_element_markers) == 1
        assert len(flagged_element_markers[0].get_facecolors()) == 0
    finally:
        plt.close(element_figure)


@pytest.mark.parametrize("view", ("species", "element"))
def test_clipped_flagged_points_keep_a_hollow_marker(tmp_path: Path, view: str) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, (authority, residual) in enumerate(
        (
            ("certified", 4.5),
            ("extrapolated", 4.5),
            ("certified", -4.5),
            ("extrapolated", -4.5),
        )
    ):
        rows.append(
            _record(
                engine="openimcc",
                experiment=f"clipped-{index}",
                formula="SiO2",
                temperature_K=1000 + index * 100,
                residual=residual,
                authority=authority,
            )
        )
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)
    figure, axis = plt.subplots()
    try:
        draw_panel(axis, data, "openimcc", load_element_abundance(), view=view)
        clipped_arrows = [
            artist
            for artist in axis.texts
            if (artist.get_gid() or "").startswith("clipped-arrow:")
        ]
        flagged_markers = [
            artist
            for artist in axis.collections
            if (artist.get_gid() or "").startswith("clipped-flag-marker:")
        ]
        assert len(clipped_arrows) == (4 if view == "species" else 8)
        assert len(flagged_markers) == (2 if view == "species" else 4)
        assert all(len(marker.get_facecolors()) == 0 for marker in flagged_markers)
    finally:
        plt.close(figure)


def test_species_view_labels_major_curves_and_element_view_labels_elements(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    for index, (formula, rail, residual) in enumerate(
        (
            ("Al", "vapour", -1.5),
            ("Al2O", "vapour", -1.0),
            ("SiO", "vapour", 0.5),
            ("MgO", "melt_activity", 1.5),
        )
    ):
        rows.append(
            _record(
                engine="openimcc",
                experiment=f"major-{index}",
                formula=formula,
                temperature_K=1000 + index * 100,
                residual=residual,
                rail=rail,
            )
        )
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)
    abundance = load_element_abundance()

    species_figure, species_axis = plt.subplots()
    try:
        species_right_axis = draw_panel(
            species_axis, data, "openimcc", abundance, view="species"
        )
        labels = [
            artist
            for artist in species_axis.texts
            if (artist.get_gid() or "").startswith("major-label:")
        ]
        assert {artist.get_text() for artist in labels} == {
            "Al",
            "Al2O",
            "SiO",
            "MgO activity",
        }
        assert all(artist.arrow_patch is not None for artist in labels)
        positions = sorted(float(artist.get_position()[1]) for artist in labels)
        assert all(right - left >= 0.35 for left, right in zip(positions, positions[1:]))
        assert not species_right_axis.get_yticklabels()
    finally:
        plt.close(species_figure)

    element_figure, element_axis = plt.subplots()
    try:
        element_right_axis = draw_panel(
            element_axis, data, "openimcc", abundance, view="element"
        )
        assert {label.get_text() for label in element_right_axis.get_yticklabels()} == {
            "Al",
            "Mg",
            "O",
            "Si",
        }
        assert not any(
            (artist.get_gid() or "").startswith("major-label:")
            for artist in element_axis.texts
        )
    finally:
        plt.close(element_figure)


def test_d139_rows_are_excluded_from_panels_and_reported_in_footer(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "test-store"}
    ]
    rows.append(
        _record(
            engine="openimcc",
            experiment="ordinary-1",
            formula="SiO2",
            temperature_K=1000,
            residual=1.0,
        )
    )
    uncalibrated = _record(
        engine="openimcc",
        experiment="d139-openimcc",
        formula="SiO2",
        temperature_K=1100,
        residual=4.5,
        status="no_band",
    )
    uncalibrated["candidate_observation"]["identity"]["quantity"] = "p_partial"
    uncalibrated["candidate_observation"]["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "calibration_not_grounded: No calibration entry has been recorded yet.",
        }
    ]
    uncalibrated["numeric"]["decision_band"] = None
    rows.append(uncalibrated)

    unmarked_no_band = _record(
        engine="openimcc",
        experiment="ordinary-no-band",
        formula="SiO2",
        temperature_K=1200,
        residual=3.0,
        status="no_band",
    )
    unmarked_no_band["numeric"]["decision_band"] = None
    rows.append(unmarked_no_band)

    other_engine = _record(
        engine="thermoengine",
        experiment="d139-thermoengine",
        formula="Al2O",
        temperature_K=1100,
        residual=-4.5,
        status="no_band",
    )
    other_engine["candidate_observation"]["identity"]["quantity"] = "p_partial"
    other_engine["candidate_observation"]["notices"] = [
        {
            "kind": NoticeKind.UNVERIFIED_APPARATUS.value,
            "reason": "calibration_not_grounded: No calibration entry has been recorded yet.",
        }
    ]
    other_engine["numeric"]["decision_band"] = None
    rows.append(other_engine)

    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals)

    openimcc = data.summaries["openimcc"]
    assert openimcc.excluded_d139_rows == 1
    assert openimcc.scored_rows == 2
    assert openimcc.unique_points == 2
    assert openimcc.rms_dex == pytest.approx(5**0.5)
    assert [
        point.bin_index for point in data.series["openimcc", "SiO2", "vapour"]
    ] == [10, 12]
    assert data.summaries["thermoengine"].excluded_d139_rows == 1
    assert data.summaries["thermoengine"].scored_rows == 0

    files = render_plots(
        data,
        tmp_path / "images",
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
        group_by=("species",),
        omit_empty_engines=True,
    )
    footer = files["species:tile_svg"].read_text(encoding="utf-8")
    assert (
        "excluded under d-139: openimcc 1 uncalibrated KEMS p_partial, "
        "thermoengine 1 uncalibrated KEMS p_partial"
    ) in footer


def test_lunar_feedstock_abundance_drives_plot_styling() -> None:
    abundance = load_element_abundance("lunar_mare_low_ti")

    assert abundance.oxide_wt_pct["Si"] == pytest.approx(44.5)
    assert abundance.oxide_wt_pct["Ti"] == pytest.approx(1.5)
    for element in ("Si", "Fe", "Mg", "Ca", "Al", "Ti"):
        assert element_tier(element, abundance) == TIER_MAJOR
    assert element_tier("O", abundance) == TIER_MAJOR
    for element in ("Na", "K", "Cr", "Mn", "P"):
        assert element_tier(element, abundance) == TIER_MINOR
    assert species_tier("Na2O", abundance) == TIER_MINOR
    assert species_tier("P2O5", abundance) == TIER_MINOR
    assert species_tier("Ag", abundance) == TIER_TRACE
    assert style_for_tier(TIER_MAJOR)["color"] == "#111111"
    assert style_for_tier(TIER_MAJOR)["linewidth"] == 2.0
    assert style_for_tier(TIER_MINOR)["color"] == "#777777"
    assert style_for_tier(TIER_MINOR)["linewidth"] == 1.2
    assert style_for_tier(TIER_TRACE)["color"] == "#b9b9b9"
    assert style_for_tier(TIER_TRACE)["linewidth"] == 0.5


def test_hero_series_keep_composition_and_count_refusals_by_reason(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    first = _record(
        engine="internal-analytical",
        experiment="same-a",
        formula="SiO",
        temperature_K=1200,
        residual=0.2,
        rail="vapour",
    )
    second = _record(
        engine="internal-analytical",
        experiment="same-b",
        formula="SiO",
        temperature_K=1200,
        residual=0.4,
        rail="vapour",
    )
    first["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {"amount_basis": "mole_fraction", "components": [["CaO", "0.4"]]},
    }
    second["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {"amount_basis": "mole_fraction", "components": [["CaO", "0.7"]]},
    }
    refused = _record(
        engine="vaporock",
        experiment="refused-P",
        formula="P2O5",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    refused["refusal"] = {"reason": "identity_incomplete"}
    residuals.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"kind": "battery_store_stamp", "revision": "hero-test"},
                first,
                second,
                refused,
            )
        ),
        encoding="utf-8",
    )

    data = read_scores(residuals, rails=HERO_RAILS)

    composition_keys = {
        composition
        for rail, facet, engine, composition in data.hero_series
        if (rail, facet, engine) == ("vapour", "SiO", "internal-analytical")
    }
    assert len(composition_keys) == 2
    assert all(key != "unknown" for key in composition_keys)
    assert {
        point.composition_sio2_mole_fraction
        for (rail, facet, engine, _composition), points in data.hero_series.items()
        if (rail, facet, engine) == ("vapour", "SiO", "internal-analytical")
        for point in points
    } == {0.0}
    element_points = element_view_series(data)[
        ("internal-analytical", "Si")
    ]
    assert {point.composition_key for point in element_points} == composition_keys
    assert data.hero_refusal_totals[("vapour", "vaporock", "identity_incomplete")] == 1
    assert data.hero_diagnostics[("vapour", "vaporock")]["refused_count"] == 1
    assert data.hero_diagnostics[("vapour", "vaporock")]["refused_by_reason"] == {
        "identity_incomplete": 1
    }
    assert _hero_refusal_entries(data, "vapour", "minor:P") == (
        ("vaporock", "identity_incomplete", 1),
    )
    assert set(_hero_facets(data, "vapour")) >= {
        "SiO",
        "P2O5",
        *(facet for facet, _label in HERO_MINOR_FACETS),
    }
    legend_handles = _hero_legend_handles(
        data, "vapour", _hero_engine_colors(data.engines)
    )
    assert len(legend_handles) <= len(data.engines) + 2
    assert [handle.get_label() for handle in legend_handles].count(
        "internal-analytical"
    ) == 1

    files = render_hero_charts(data, tmp_path / "collapsed-facets")
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8")
    assert svg.count("No numeric predictions yet:") == 1
    assert svg.count("Minor species / foulants (no numeric predictions yet):") == 1
    assert "P2O5 (refused 1: identity_incomplete 1" in svg
    assert "P (refused 1: identity_incomplete 1" in svg
    assert "minor:P" not in svg


def test_non_observable_rows_are_separate_from_refusals_and_history(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    not_observable = _record(
        engine="magemin",
        experiment="magemin-vapour",
        formula="P2O5",
        temperature_K=1400,
        residual=None,
        rail="vapour",
        status="refused",
    )
    not_observable["refusal"] = {"reason": "unsupported"}
    refused = _record(
        engine="vaporock",
        experiment="vaporock-refusal",
        formula="SiO2",
        temperature_K=1400,
        residual=None,
        rail="vapour",
        status="refused",
    )
    refused["refusal"] = {"reason": "outside_supported_species"}
    residuals.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"kind": "battery_store_stamp", "revision": "non-observable-test"},
                not_observable,
                refused,
            )
        ),
        encoding="utf-8",
    )

    data = read_scores(residuals, rails=HERO_RAILS)

    assert data.hero_refusal_totals == {
        ("vapour", "vaporock", "outside_supported_species"): 1
    }
    assert data.hero_diagnostics[("vapour", "magemin")]["refused_count"] == 0
    assert data.hero_diagnostics[("vapour", "magemin")]["not_observable_count"] == 1
    assert data.hero_diagnostics[("vapour", "magemin")]["refused_by_reason"] == {}
    assert data.summaries["magemin"].refusals == {}
    assert data.summaries["magemin"].not_observable_by_rail["vapour"] == 1

    record = _history_record(
        {"store": {"revision": "non-observable-test"}, "records": []},
        landing_sha="non-observable-test",
        commit_date="2026-10-19T00:00:00Z",
        benchmarks=data.hero_benchmarks,
        diagnostics=data.hero_diagnostics,
        refusal_coverage=_refusal_coverage_from_summaries(data.summaries),
    )
    metric = next(
        item
        for item in record["metrics"]
        if item["rail"] == "vapour" and item["engine"] == "magemin"
    )
    assert metric["not_observable_count"] == 1
    assert metric["refused_count"] == 0
    assert metric["refused_by_reason"] == {}
    coverage = next(
        item
        for item in record["refusal_coverage"]
        if item["rail"] == "vapour" and item["engine"] == "magemin"
    )
    assert coverage["not_observable_count"] == 1
    assert coverage["refused_count"] == 0
    assert any(
        item["rail"] == "thermochemistry" and item["engine"] == "magemin"
        for item in record["refusal_coverage"]
    )

    files = render_hero_charts(data, tmp_path / "rendered")
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert "not an observable of this engine" in svg
    assert "magemin 1" in svg


def test_sweet_spot_panels_show_shared_cell_counts_and_hatch_sparse_cells(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "sweet-spot-test"}
    ]
    for engine, experiment, residual in (
        ("internal-analytical", "ia-1", 0.2),
        ("internal-analytical", "ia-2", -0.8),
        ("internal-analytical", "ia-3", 1.0),
        ("openimcc", "open-1", 0.4),
    ):
        row = _record(
            engine=engine,
            experiment=experiment,
            formula="SiO",
            temperature_K=1200,
            residual=residual,
            rail="vapour",
        )
        row["candidate_observation"]["identity"]["composition"] = {
            "tag": "value",
            "value": {
                "amount_basis": "mole_fraction",
                "components": [["SiO2", "0.25"], ["FeO", "0.75"]],
            },
        }
        rows.append(row)

    residuals = tmp_path / "sweet-spot.jsonl"
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    internal_cells = data.hero_sweet_spot_cells[("vapour", "internal-analytical")]
    openimcc_cells = data.hero_sweet_spot_cells[("vapour", "openimcc")]
    internal_cell = next(cell for cell in internal_cells if cell["n"] == 3)
    openimcc_cell = next(cell for cell in openimcc_cells if cell["n"] == 1)
    assert internal_cell["median_abs_dex"] == "0.8"
    assert internal_cell["sio2_lower"] == openimcc_cell["sio2_lower"] == "0.25"
    assert (
        internal_cell["temperature_lower_K"]
        == openimcc_cell["temperature_lower_K"]
    )

    fig, axis = plt.subplots()
    _draw_hero_sweet_spot(
        axis,
        "openimcc",
        openimcc_cells,
        colormap=plt.get_cmap("viridis"),
        normalization=Normalize(vmin=0, vmax=1),
    )
    assert any(patch.get_hatch() == "///" for patch in axis.patches)
    assert "1" in {text.get_text() for text in axis.texts}
    assert "0" in {text.get_text() for text in axis.texts}
    plt.close(fig)

    files = render_hero_charts(data, tmp_path / "sweet-spot-render")
    svg = files["hero:vapour:svg"].read_text(encoding="utf-8").casefold()
    assert "cell label=n" in svg
    assert "sweet-spot fill=median" in svg
    assert files["hero:vapour:png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    melt_svg = files["hero:melt_activity:svg"].read_text(encoding="utf-8").casefold()
    assert "140/147 rows" in melt_svg
    assert "main's fix is pending" in melt_svg


def test_hero_flagged_points_are_hollow_and_benchmark_uses_shared_rows(
    tmp_path: Path,
) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "hero-test"}
    ]
    for engine, experiment, value, authority in (
        ("internal-analytical", "shared", 3.0, "extrapolated"),
        ("internal-analytical", "internal-only", 100.0, "certified"),
        ("openimcc", "shared", 1.0, "certified"),
        ("openimcc", "comparison-only", 100.0, "certified"),
        ("thermoengine", "shared", 2.0, "certified"),
    ):
        row = _record(
            engine=engine,
            experiment=experiment,
            formula="FeO",
            temperature_K=1300,
            residual=value,
            rail="redox",
            authority=authority,
        )
        row["reference"] = experiment
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        if engine == "internal-analytical" and authority == "extrapolated":
            row["notices"] = [
                {
                    "kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value,
                    "reason": "extrapolated beyond certified band",
                }
            ]
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)

    benchmark = data.hero_benchmarks[("redox", "internal-analytical")]
    assert benchmark["benchmark_engine"] == "thermoengine"
    assert benchmark["n"] == 1
    assert benchmark["primary_rms_dex"] == "3.0"
    assert benchmark["benchmark_rms_dex"] == "2.0"
    assert data.hero_benchmarks[("redox", "openimcc")]["benchmark_engine"] == "thermoengine"
    assert data.hero_diagnostics[("redox", "internal-analytical")][
        "open_predict_and_flag_notice_counts_by_kind"
    ] == {NoticeKind.OUT_OF_CERTIFIED_BAND.value: 1}
    assert "Fe3+/Fe2+ ratio" in _hero_facets(data, "redox")

    figure, axis = plt.subplots()
    try:
        _draw_hero_current(
            axis,
            data,
            "redox",
            "Fe3+/Fe2+ ratio",
            engine_colors={"internal-analytical": "#111111"},
        )
        flagged = [
            artist
            for artist in axis.collections
            if (artist.get_gid() or "").endswith(":flagged")
        ]
        assert len(flagged) == 1
        assert len(flagged[0].get_facecolors()) == 0
    finally:
        plt.close(figure)


def test_history_appends_without_rewriting_existing_bytes(tmp_path: Path) -> None:
    history = tmp_path / "hero-history.jsonl"
    history.write_text('{"store_sha":"old"}\n', encoding="utf-8")
    previous = history.read_bytes()
    record = {
        "kind": "battery_hero_history",
        "store_sha": "new",
        "commit_date": "2026-10-18T10:27:53-04:00",
        "metrics": [],
        "benchmarks": [],
    }

    assert append_history(history, record)
    assert history.read_bytes().startswith(previous)
    assert not append_history(history, record)
    assert len(history.read_text(encoding="utf-8").splitlines()) == 2


def test_history_schema_keeps_scorer_metrics_and_per_rail_counts() -> None:
    record = _history_record(
        {
            "store": {"revision": "redox-test"},
            "records": [
                {
                    "tier": "all_numeric",
                    "rail": "redox",
                    "engine": "internal-analytical",
                    "n": 7,
                    "rms_dex": "0.4",
                    "median_abs_dex": "0.3",
                }
            ],
        },
        landing_sha="green-test",
        commit_date="2026-10-18T10:27:53-04:00",
        benchmarks={
            ("redox", "internal-analytical"): {
                "rail": "redox",
                "primary_engine": "internal-analytical",
                "benchmark_engine": None,
                "n": 0,
                "primary_rms_dex": None,
                "benchmark_rms_dex": None,
            },
            ("redox", "openimcc"): {
                "rail": "redox",
                "primary_engine": "openimcc",
                "benchmark_engine": None,
                "n": 0,
                "primary_rms_dex": None,
                "benchmark_rms_dex": None,
            },
        },
        diagnostics={
            ("redox", "internal-analytical"): {
                "refused_count": 2,
                "not_observable_count": 4,
                "refused_by_reason": {"unsupported": 2},
                "open_predict_and_flag_notice_counts_by_kind": {
                    "out_of_certified_band": 1
                },
            }
        },
        refusal_coverage=[
            {
                "rail": "redox",
                "engine": "internal-analytical",
                "refused_count": 2,
                "not_observable_count": 4,
            }
        ],
    )

    assert record["kind"] == "battery_hero_history"
    assert record["schema_version"] == 1
    assert [entry["primary_engine"] for entry in record["benchmarks"]] == [
        "internal-analytical",
        "openimcc",
    ]
    assert all(entry["benchmark_engine"] is None for entry in record["benchmarks"])
    metric = record["metrics"][0]
    assert metric["numeric_count"] == 7
    assert metric["rms_dex"] == "0.4"
    assert metric["median_abs_dex"] == "0.3"
    assert metric["refused_count"] == 2
    assert metric["not_observable_count"] == 4
    assert metric["refused_by_reason"] == {"unsupported": 2}
    assert metric["open_predict_and_flag_notice_counts_by_kind"] == {
        "out_of_certified_band": 1
    }
    assert record["refusal_coverage"] == [
        {
            "rail": "redox",
            "engine": "internal-analytical",
            "refused_count": 2,
            "not_observable_count": 4,
        }
    ]


def test_cli_requires_explicit_history_path() -> None:
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(
            [
                "--input",
                "residuals.jsonl",
                "--output-dir",
                "rendered",
                "--simulator-commit",
                "simulator-pin",
                "--openimcc-commit",
                "openimcc-pin",
            ]
        )
    assert error.value.code == 2


def test_missing_comparison_engine_is_reported_as_unavailable(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "unavailable-test"}
    ]
    for engine, value in (("openimcc", 0.2), ("internal-analytical", 0.3)):
        row = _record(
            engine=engine,
            experiment="shared-redox",
            formula="FeO",
            temperature_K=1400,
            residual=value,
            rail="redox",
        )
        row["reference"] = "shared-redox"
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    data = read_scores(residuals, rails=HERO_RAILS)
    for engine in ("openimcc", "internal-analytical"):
        assert data.hero_benchmarks[("redox", engine)]["benchmark_engine"] is None

    files = render_hero_charts(data, tmp_path / "rendered")
    svg = files["hero:redox:svg"].read_text(encoding="utf-8").casefold()
    assert "benchmark unavailable" in svg


def test_read_scores_streams_residuals_from_archive_member(tmp_path: Path) -> None:
    magemin_vapour = _record(
        engine="magemin",
        experiment="archive-magemin-vapour",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    magemin_vapour["refusal"] = {"reason": "unsupported"}
    attempted_vapour_refusal = _record(
        engine="vaporock",
        experiment="archive-vaporock-refusal",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="vapour",
        status="refused",
    )
    attempted_vapour_refusal["refusal"] = {
        "reason": "outside_supported_species"
    }
    compilation_refusal = _record(
        engine="alphamelts",
        experiment="archive-thermo-refusal",
        formula="SiO",
        temperature_K=1200,
        residual=None,
        rail="thermochemistry",
        status="refused",
    )
    compilation_refusal["candidate_request"]["quantity"] = "H_minus_H298"
    compilation_refusal["refusal"] = {
        "reason": "unsupported",
        "detail": {"reason": "quantity_not_predicted"},
    }
    rows = [
        {"kind": "battery_store_stamp", "revision": "archive-test"},
        _record(
            engine="internal-analytical",
            experiment="archive-row",
            formula="SiO",
            temperature_K=1200,
            residual=0.25,
            rail="vapour",
        ),
        magemin_vapour,
        attempted_vapour_refusal,
        compilation_refusal,
    ]
    payload = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")
    summary_payload = json.dumps(
        {
            "store": {"revision": "archive-test"},
            "records": [
                {
                    "tier": "all_numeric",
                    "rail": "vapour",
                    "engine": "internal-analytical",
                    "n": 1,
                    "rms_dex": "0.25",
                    "median_abs_dex": "0.25",
                }
            ],
        }
    ).encode("utf-8")
    archive_path = tmp_path / "battery.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("data/battery/residuals.jsonl")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
        summary_member = tarfile.TarInfo("data/battery/score-summary.json")
        summary_member.size = len(summary_payload)
        archive.addfile(summary_member, io.BytesIO(summary_payload))

    data = read_scores(archive_path, rails=HERO_RAILS)

    assert data.hero_series[("vapour", "SiO", "internal-analytical", "unknown")][0].residual_dex == 0.25

    compact_path = tmp_path / "compact-residuals.jsonl.gz"
    with gzip.open(compact_path, "wb") as compact:
        compact.write(payload)
    compact_data = read_scores(compact_path, rails=HERO_RAILS)
    assert compact_data.hero_series == data.hero_series

    history = history_record_from_archive(archive_path, landing_sha="HEAD")
    coverage = {
        (item["rail"], item["engine"]): item
        for item in history["refusal_coverage"]
    }
    assert coverage[("vapour", "magemin")] == {
        "rail": "vapour",
        "engine": "magemin",
        "refused_count": 0,
        "not_observable_count": 1,
    }
    assert coverage[("vapour", "vaporock")] == {
        "rail": "vapour",
        "engine": "vaporock",
        "refused_count": 1,
        "not_observable_count": 0,
    }
    assert coverage[("thermochemistry", "alphamelts")] == {
        "rail": "thermochemistry",
        "engine": "alphamelts",
        "refused_count": 0,
        "not_observable_count": 1,
    }


def test_hero_svg_and_png_are_byte_identical_for_the_same_input(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    row = _record(
        engine="internal-analytical",
        experiment="hero-1",
        formula="SiO",
        temperature_K=1200,
        residual=0.2,
        rail="vapour",
    )
    row["candidate_observation"]["identity"]["composition"] = {
        "tag": "value",
        "value": {
            "amount_basis": "mole_fraction",
            "components": [["SiO2", "0.25"], ["FeO", "0.75"]],
        },
    }
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "hero-test"},
        row,
    ]
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    history = tmp_path / "hero-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "store_sha": "hero-test",
                "store_revision": "hero-test",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "vapour",
                        "engine": "internal-analytical",
                        "rms_dex": "0.2",
                        "median_abs_dex": "0.2",
                    }
                ],
                "benchmarks": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    data = read_scores(residuals, rails=HERO_RAILS)

    first = render_hero_charts(data, tmp_path / "first", history_path=history)
    second = render_hero_charts(data, tmp_path / "second", history_path=history)

    for rail in HERO_RAILS:
        assert first[f"hero:{rail}:svg"].read_bytes() == second[f"hero:{rail}:svg"].read_bytes()
        assert first[f"hero:{rail}:png"].read_bytes() == second[f"hero:{rail}:png"].read_bytes()


def test_hero_legend_is_bounded_and_numeric_labels_use_three_significant_figures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residuals = tmp_path / "formatted-residuals.jsonl"
    rows: list[dict[str, object]] = [
        {"kind": "battery_store_stamp", "revision": "formatted-store"}
    ]
    for engine, value in (
        ("internal-analytical", 0.3333333333333333),
        ("openimcc", 0.5555555555555555),
        ("thermoengine", 0.2222222222222222),
    ):
        row = _record(
            engine=engine,
            experiment="shared",
            formula="FeO",
            temperature_K=1300,
            residual=value,
            rail="redox",
        )
        row["reference"] = "shared"
        row["candidate_observation"]["identity"]["quantity"] = {
            "tag": "value",
            "value": "fe3_fe2_ratio",
        }
        row["candidate_observation"]["identity"]["composition"] = {
            "tag": "value",
            "value": {
                "amount_basis": "mole_fraction",
                "components": [["SiO2", "0.37"], ["FeO", "0.63"]],
            },
        }
        rows.append(row)
    residuals.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    data = read_scores(residuals, rails=HERO_RAILS)
    history = tmp_path / "formatted-history.jsonl"
    history.write_text(
        json.dumps(
            {
                "kind": "battery_hero_history",
                "store_revision": "formatted-store",
                "commit_date": "2026-10-18T10:27:53-04:00",
                "metrics": [
                    {
                        "rail": "redox",
                        "engine": "internal-analytical",
                        "n": 147,
                        "numeric_count": 147,
                        "rms_dex": "0.30636297707166455257",
                        "median_abs_dex": "0.30636297707166455257",
                    }
                ],
                "benchmarks": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    legend_labels: list[list[str]] = []
    figures: list[Figure] = []
    original_legend = Figure.legend
    original_savefig = Figure.savefig

    def record_legend(self: Figure, *args: object, **kwargs: object) -> object:
        handles = kwargs.get("handles", args[0] if args else ())
        legend_labels.append([handle.get_label() for handle in handles])
        return original_legend(self, *args, **kwargs)

    def record_figure(self: Figure, *args: object, **kwargs: object) -> object:
        result = original_savefig(self, *args, **kwargs)
        if args and Path(str(args[0])).suffix == ".svg":
            figures.append(self)
        return result

    monkeypatch.setattr(Figure, "legend", record_legend)
    monkeypatch.setattr(Figure, "savefig", record_figure)
    render_hero_charts(data, tmp_path / "formatted-render", history_path=history)

    assert legend_labels
    assert all(len(labels) <= len(data.engines) + 2 for labels in legend_labels)
    labels = [
        artist.get_text()
        for figure in figures
        for artist in figure.findobj(match=Text)
        if artist.get_text()
    ]
    assert any("0.31 dex" in label and "n=147" in label for label in labels)
    assert all("0.30636297707166455257" not in label for label in labels)
    for label in labels:
        without_dates = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b", "", label)
        for number in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:[kM])?", without_dates):
            value = number[:-1] if number.endswith(("k", "M")) else number
            digits = re.sub(r"\D", "", value)
            significant = len(digits.lstrip("0")) if "." in value else len(
                digits.lstrip("0").rstrip("0")
            )
            assert significant <= 3, f"too many significant figures in {label!r}"


def test_stamp_contents_and_png_output(tmp_path: Path) -> None:
    residuals = tmp_path / "residuals.jsonl"
    _score_file(residuals)
    data = read_scores(residuals)
    stamp = stamp_text(
        data,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "OpenIMCC 1.2.3 · openimcc-test" in stamp
    assert "simulator sim-test" in stamp
    assert "store test-store" in stamp
    assert "rendered 2026-10-15 UTC" in stamp
    assert "patch" not in stamp
    patched = replace(
        data,
        stamp={
            **data.stamp,
            "simulator_modified": True,
            "simulator_patch_sha256": "0123456789abcdef",
            "openimcc_patch_sha256": "abcdef0123456789",
        },
    )
    patched_stamp = stamp_text(
        patched,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch 0123456789ab" in patched_stamp
    assert "openimcc patch abcdef012345" in patched_stamp
    dirty_equal_commits = replace(
        patched,
        stamp={
            **patched.stamp,
            "simulator_tree_commit": "sim-test",
            "openimcc_installed_commit": "openimcc-test",
            "openimcc_reported_commit": "openimcc-test",
            "simulator_modified": True,
            "openimcc_modified": True,
        },
    )
    dirty_equal_stamp = stamp_text(
        dirty_equal_commits,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch 0123456789ab" in dirty_equal_stamp
    assert "openimcc patch abcdef012345" in dirty_equal_stamp
    clean = replace(
        data,
        stamp={
            **data.stamp,
            "simulator_tree_commit": "sim-test",
            "simulator_modified": False,
            "simulator_patch_sha256": "0123456789abcdef",
            "openimcc_installed_commit": "openimcc-test",
            "openimcc_modified": False,
            "openimcc_patch_sha256": "abcdef0123456789",
        },
    )
    clean_stamp = stamp_text(
        clean,
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert "patch" not in clean_stamp

    files = render_plots(
        data,
        tmp_path / "images",
        simulator_commit="sim-test",
        openimcc_commit="openimcc-test",
        render_date="2026-10-15",
    )
    assert files["species:tile_png"].is_file()
    assert files["species:tile_svg"].is_file()
    assert files["species:engine:openimcc"].is_file()
    assert files["species:engine:magemin"].is_file()
    assert files["element:tile_png"].is_file()
    assert files["element:tile_svg"].is_file()
    assert files["element:engine:openimcc"].is_file()
    assert files["species:tile_png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "test-store-2026-10-15-species" in files["species:tile_png"].name
    assert "test-store-2026-10-15-element" in files["element:tile_png"].name
    assert "no scored rows: magemin (not an observable of this engine ×1)" in files["species:tile_svg"].read_text(encoding="utf-8")
