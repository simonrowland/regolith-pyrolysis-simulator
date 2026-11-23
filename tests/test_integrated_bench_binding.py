"""Binding and cache contract for integrated-bench decks. No residuals."""

from __future__ import annotations

import copy
import json
import sqlite3
import zlib
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.battery.integrated_bench import (
    POMEROY_PRESET,
    ROBINOT_PRESET,
    SESKO_PRESET,
    BenchResidual,
    DeckRequest,
    IntegratedBenchContractError,
    bound_observation_ids,
    build_deck_request,
    classify_observation,
    clean_headline,
    process_timestep_s,
    project_whole_run,
    run_cached_deck,
    score_bound_decks,
)
from simulator.battery import integrated_bench


REPO = Path(__file__).resolve().parents[1]
MASS = (
    "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
    "::field:mass_yield_percent"
)
FEED = (
    "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
    "::field:fraction_of_feedstock_oxygen_percent"
)
PARENT = "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
DISTINCT = "kems-044-robinot-2026::robinot_2026_o2_comparable_run_1p17"
MODEL = "kems-044-robinot-2026::robinot_2026_o2_hsc_expected_1p37_model"
FIG4 = "kems-044-robinot-2026::robinot_2026_o2_fig4ab_figure_only"
POMEROY = (
    "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction"
)
SESKO = (
    "kems-036-sesko-2024::sesko_2024_crucible_deposit_edx",
    "kems-036-sesko-2024::sesko_2024_deposit_Fe_edx_qualitative",
    "kems-036-sesko-2024::sesko_2024_deposit_K_edx_range",
    "kems-036-sesko-2024::sesko_2024_deposit_Si_edx_range",
    "kems-036-sesko-2024::sesko_2024_fig6_16_figure_only_blocked",
    "kems-036-sesko-2024::sesko_2024_table1_eac1_simulant_composition",
    "kems-036-sesko-2024::sesko_2024_table1_literature_feedstock_compositions",
    "kems-036-sesko-2024::sesko_2024_table2_factsage_and_literature_o2_yield",
)
_MEASURED_YIELDS = ("0.0105", "0.0247", "0.0117")
_OK_DOCUMENT = {
    "status": "ok",
    "per_hour_summary": [{
        "hour": 1,
        "O2_source_side_potential_kg_cumulative": 0.001,
        "vapor_species_kg_hr": {"O2": 1.0e-4},
        "note": "should be stripped",
    }],
    "final": {
        "pump_outlet_by_species_kg": {},
        "expected_value": 0.0105,
    },
    "trajectory_comparison": {"nope": 1},
}


@pytest.fixture(autouse=True)
def _isolated_cache():
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    yield
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)


@pytest.fixture(scope="module")
def robinot_deck() -> DeckRequest:
    return build_deck_request(REPO / ROBINOT_PRESET)


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    return path


def _stored_rows(path: Path) -> list[tuple[str, str]]:
    engine_cache._close_connections()
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "engine_outputs" not in tables:
            return []
        return list(
            connection.execute(
                "SELECT engine, chem_version FROM engine_outputs"
            )
        )


def _rewrite(
    tmp_path: Path,
    old: bytes,
    new: bytes,
    *,
    count: int,
    source: Path | None = None,
    name: str = "preset.yaml",
) -> Path:
    raw = (source or (REPO / ROBINOT_PRESET)).read_bytes()
    found = raw.count(old)
    assert found == count, (old, found)
    path = tmp_path / name
    path.write_bytes(raw.replace(old, new))
    return path


def _key(request: dict) -> bytes:
    return engine_cache.engine_cache_key("simulator", 1, request)


def _echo(request: dict) -> dict:
    """Project the printed inputs the differential is allowed to see."""

    sample = request["geometry"]["sample"]
    schedule = request["schedule"]
    return {
        "status": "ok",
        "per_hour_summary": [{
            "hour": 1.0,
            "O2_source_side_potential_kg_cumulative": float(
                request["session"]["mass_kg"]
            ),
            "vapor_species_kg_hr": {
                "O2": float(sample["pellet_diameter_mm"]),
                "SiO": float(schedule["chamber_pressure_mbar"][0]["value"]),
                "Ar": float(schedule["gas_boundary"]["imposed_flow"]["value"]),
                "Fe": float(request["feedstock"]["composition_wt_pct"]["SiO2"]),
            },
        }],
        "final": {"pump_outlet_by_species_kg": {}},
    }


def _observable(request: dict) -> dict:
    hot, cacheable = project_whole_run(_echo(request))
    assert cacheable is True
    return hot


def test_simulator_is_engine_number_7_and_not_a_score_engine() -> None:
    assert list(engine_cache._ENGINE_NUMBERS) == [
        "openimcc",
        "internal-analytical",
        "vaporock",
        "magemin",
        "alphamelts",
        "thermoengine",
        "simulator",
    ]
    assert list(engine_cache._ENGINE_NUMBERS.values()) == [1, 2, 3, 4, 5, 6, 7]
    assert engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"] == 1
    from simulator.battery.score import SCORE_ENGINE_SET

    assert "simulator" not in {engine.value for engine in SCORE_ENGINE_SET}


def test_declared_cache_inputs_match_the_closed_path_set() -> None:
    from tests.test_engine_cache_input_identity import _EXPECTED_SOURCE_PATHS

    declared = {
        ref.path
        for refs in engine_cache.ENGINE_CACHE_INPUTS.values()
        for ref in refs
    }
    assert declared == _EXPECTED_SOURCE_PATHS


def test_simulator_runtime_identity_is_the_runner_source() -> None:
    runner = Path(engine_cache.__file__).resolve().parent / "runner" / "__init__.py"
    assert engine_cache._compute_runtime_identity("simulator") == (
        "source-sha256:" + engine_cache._file_sha256(runner)
    )


def test_deck_module_does_not_reown_mass_loss_area_or_scoring() -> None:
    source = Path(integrated_bench.__file__).read_text(encoding="utf-8")
    for banned in (
        "predict_mass_loss_fraction",
        "gram_lab_exposed_melt_area_bridge",
        "MELT_DENSITY",
        ".run(",
        "SCORE_ENGINE_SET",
        "NoticeKind",
        "simulator.battery.score",
    ):
        assert banned not in source


def test_binding_table_keeps_trials_and_capability_gaps_apart() -> None:
    mass = classify_observation(MASS)
    feed = classify_observation(FEED)
    assert mass.kind == feed.kind == "bound"
    assert mass.basis == "o2_mass_yield_fraction"
    assert feed.basis == "feed_oxygen_extraction_fraction"
    assert mass.trial_id == feed.trial_id == "robinot_2026_main"
    assert mass.lineage == feed.lineage == "robinot_2026_main_gas_measurement"
    assert mass.preset_relpath == feed.preset_relpath == ROBINOT_PRESET
    assert set(bound_observation_ids()) == {MASS, FEED}

    parent = classify_observation(PARENT)
    assert (parent.kind, parent.reason) == ("shared_lineage", "same_gas_measurement")
    assert PARENT not in bound_observation_ids()

    distinct = classify_observation(DISTINCT)
    assert (distinct.kind, distinct.reason) == ("no_deck", "distinct_trial")
    assert distinct.trial_id is None

    model = classify_observation(MODEL)
    assert (model.kind, model.reason) == ("not_a_measurement", "model_value")

    fig4 = classify_observation(FIG4)
    unknown = classify_observation("not-an-observation")
    assert (fig4.kind, fig4.reason) == ("no_deck", "no_deck")
    assert (unknown.kind, unknown.reason) == ("no_deck", "no_deck")
    assert fig4.trial_id is None

    pomeroy = classify_observation(POMEROY)
    assert (pomeroy.kind, pomeroy.reason) == (
        "missing_capability",
        "no_outlet_observable",
    )
    assert pomeroy.preset_relpath == POMEROY_PRESET
    assert len(SESKO) == 8
    for observation_id in SESKO:
        disposition = classify_observation(observation_id)
        assert (disposition.kind, disposition.reason) == (
            "missing_capability",
            "transport_blocked",
        )
        assert disposition.preset_relpath == SESKO_PRESET
    assert "keep" not in {
        classify_observation(observation_id).kind
        for observation_id in (
            MASS, FEED, PARENT, DISTINCT, MODEL, FIG4, POMEROY, *SESKO,
            "not-an-observation",
        )
    }


def test_pomeroy_and_sesko_are_refused_before_a_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("PyrolysisRun was constructed")

    monkeypatch.setattr("simulator.runner.PyrolysisRun", refuse)
    with pytest.raises(IntegratedBenchContractError) as pomeroy:
        build_deck_request(REPO / POMEROY_PRESET)
    assert (pomeroy.value.kind, pomeroy.value.reason) == (
        "missing_capability",
        "no_outlet_observable",
    )
    with pytest.raises(IntegratedBenchContractError) as sesko:
        build_deck_request(REPO / SESKO_PRESET)
    assert (sesko.value.kind, sesko.value.reason) == (
        "missing_capability",
        "transport_blocked",
    )

    relabelled = _rewrite(
        tmp_path,
        b"paper_id: sesko_2024_vapor_phase_pyrolysis\n",
        b"paper_id: sesko_2024_vapor_phase_pyrolysis_relabelled\n",
        count=1,
        source=REPO / SESKO_PRESET,
        name="sesko.yaml",
    )
    with pytest.raises(IntegratedBenchContractError) as by_status:
        build_deck_request(relabelled)
    assert by_status.value.reason == "transport_blocked"

    blocked = _rewrite(
        tmp_path,
        b"extraction_status: comparison_ready_assumed_inputs_named\n",
        b"extraction_status: condition_capture_p0b_transport_blocked\n",
        count=1,
        name="robinot-blocked.yaml",
    )
    with pytest.raises(IntegratedBenchContractError) as by_robinot_status:
        build_deck_request(blocked)
    assert by_robinot_status.value.reason == "transport_blocked"


def test_robinot_request_carries_printed_inputs_and_not_measurements(
    robinot_deck: DeckRequest,
) -> None:
    request = robinot_deck.cache_request
    integrated_bench.assert_cache_request_has_no_measurement(request)
    blob = json.dumps(request)
    for token in _MEASURED_YIELDS:
        assert token not in blob
    for banned in (
        "input_role",
        "source_class",
        "elemental_composition_wt_pct",
        "exposed_melt_area_m2",
        "conditional_covariate",
        "printed_input",
        "trial_id",
        "robinot_2026_main",
    ):
        assert banned not in blob
    assert robinot_deck.preset_digest.startswith("sha256:")
    assert robinot_deck.preset_digest not in blob

    session = request["session"]
    assert session["backend_name"] == "internal-analytical"
    assert session["backend_policy"] == "runner-strict"
    assert session["leg"] == "faithful"
    assert session["mitigation"] == "none"
    assert session["mass_kg"] == pytest.approx(3.38 / 1000.0)
    assert session["hours"] == 1
    assert session["effective_dt_s"] == process_timestep_s()
    assert "pO2_cover" not in request["schedule"]

    sample = request["geometry"]["sample"]
    assert sample["mass_g"] == pytest.approx(3.38)
    assert sample["pellet_diameter_mm"] == pytest.approx(13.0)
    assert sample["pellet_height_mm"] == pytest.approx(10.0)
    assert sample["pressed_at_tons"] == pytest.approx(2.5)
    assert "exposed_melt_area_m2" not in sample
    pump = request["geometry"]["pump"]
    assert pump["model"] == "Edwards NDX15"
    assert pump["vacuum_limit_mbar"] == pytest.approx(7.0e-3)
    assert request["feedstock"]["id"] == "lunar_eac_1a"
    assert request["feedstock"]["composition_wt_pct"]["SiO2"] == pytest.approx(43.7)
    pressures = request["schedule"]["chamber_pressure_mbar"]
    assert [point["value"] for point in pressures] == pytest.approx([13.0, 13.0])
    assert request["schedule"]["gas_boundary"]["imposed_flow"]["value"] == pytest.approx(0.3)

    lineage = robinot_deck.lineage
    assert all(
        point["input_role"] == "conditional_covariate"
        for point in lineage["schedule"]["chamber_pressure_mbar"]
    )
    melt = lineage["schedule"]["melt_temperature_C"]
    assert melt[0]["input_role"] == "assumption"
    assert melt[-1]["input_role"] == "conditional_covariate"
    assert lineage["schedule"]["duration_h"] == "conditional_covariate"
    assert lineage["schedule"]["furnace_ceiling_C"] == "conditional_covariate"
    assert lineage["schedule"]["interpolation"] == "assumption"
    assert lineage["schedule"]["gas_boundary"]["background_gas"] == "printed_input"
    assert lineage["schedule"]["gas_boundary"]["imposed_flow"] == "conditional_covariate"
    assert lineage["schedule"]["gas_boundary"]["pressure_control"] == "assumption"
    assert lineage["geometry"]["sample"]["mass_g"] == "printed_input"
    assert lineage["geometry"]["sample"]["pellet_diameter_mm"] == "printed_input"
    assert lineage["geometry"]["pump"]["model"] == "printed_input"
    assert lineage["feedstock"] == "printed_input"
    key = _key(request)
    assert key[0] == 7
    assert key[1:3] == (1).to_bytes(2, "big")


def test_effective_timestep_is_the_chamber_pressure_literal(
    robinot_deck: DeckRequest,
) -> None:
    step = process_timestep_s()
    assert robinot_deck.cache_request["session"]["effective_dt_s"] == step
    edited = copy.deepcopy(robinot_deck.cache_request)
    edited["session"]["effective_dt_s"] = step + 1.0
    assert _key(edited) != _key(robinot_deck.cache_request)


@pytest.mark.parametrize(
    ("old", "new", "count"),
    [
        pytest.param(
            b"grid_end_min: 59.5\n",
            b"grid_end_min: 59.6\n",
            1,
            id="grid_end",
        ),
        pytest.param(
            b"fast_tier_policy: cached_real_only_no_internal_analytical\n",
            b"fast_tier_policy: cached_real_only_edited\n",
            1,
            id="fast_tier",
        ),
        pytest.param(
            b"sticking_coefficient: 1.0\n",
            b"sticking_coefficient: 0.5\n",
            2,
            id="sticking",
        ),
        pytest.param(
            b"observation_sidecar_path: data/literature/vacuum_pyrolysis_measurements.yaml\n",
            b"observation_sidecar_path: data/literature/other_sidecar.yaml\n",
            1,
            id="sidecar",
        ),
        pytest.param(
            b"schedule_digest: robinot_2026_schedule_comparison_v2\n",
            b"schedule_digest: robinot_2026_schedule_comparison_v2_edited\n",
            1,
            id="digest",
        ),
        pytest.param(
            b"paper_id: robinot_2026_vacuum_pyrolysis\n",
            b"paper_id: robinot_2026_vacuum_pyrolysis_relabelled\n",
            1,
            id="paper_id",
        ),
        pytest.param(
            b"schema_version: vacuum_pyrolysis_preset.v1\n",
            (
                b"schema_version: vacuum_pyrolysis_preset.v1\n"
                b"observed_o2_mass_fraction: 0.0105\n"
                b"observed_feed_oxygen_fraction: 0.0247\n"
                b"other_trial_fraction: 0.0117\n"
                b"uncertainty: 0.001\n"
            ),
            1,
            id="observed_numbers",
        ),
    ],
)
def test_comparison_metadata_leaves_the_request_and_observable_unchanged(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    old: bytes,
    new: bytes,
    count: int,
) -> None:
    mutant = build_deck_request(_rewrite(tmp_path, old, new, count=count))
    assert mutant.cache_request == robinot_deck.cache_request
    assert _observable(mutant.cache_request) == _observable(robinot_deck.cache_request)
    blob = json.dumps(mutant.cache_request)
    for token in _MEASURED_YIELDS:
        assert token not in blob


@pytest.mark.parametrize(
    ("old", "new", "count"),
    [
        pytest.param(b"mass_g: 3.38\n", b"mass_g: 3.39\n", 1, id="mass"),
        pytest.param(
            b"pellet_diameter_mm: 13\n",
            b"pellet_diameter_mm: 14\n",
            1,
            id="pellet",
        ),
        pytest.param(
            b"value: 13.0, unit: mbar",
            b"value: 14.0, unit: mbar",
            2,
            id="pressure",
        ),
        pytest.param(b"value: 0.3\n", b"value: 0.4\n", 1, id="flow"),
    ],
)
def test_a_printed_input_changes_the_request_and_the_observable(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    old: bytes,
    new: bytes,
    count: int,
) -> None:
    mutant = build_deck_request(_rewrite(tmp_path, old, new, count=count))
    assert mutant.cache_request != robinot_deck.cache_request
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)
    assert _observable(mutant.cache_request) != _observable(robinot_deck.cache_request)


def test_feedstock_oxide_edit_changes_the_request(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    feedstocks = _rewrite(
        tmp_path,
        b"SiO2:  43.7",
        b"SiO2:  43.8",
        count=1,
        source=REPO / "data" / "feedstocks.yaml",
        name="feedstocks.yaml",
    )
    mutant = build_deck_request(REPO / ROBINOT_PRESET, feedstocks_path=feedstocks)
    assert mutant.cache_request["feedstock"]["composition_wt_pct"]["SiO2"] == pytest.approx(43.8)
    assert mutant.cache_request != robinot_deck.cache_request
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)
    assert "elemental_composition_wt_pct" not in json.dumps(mutant.cache_request)


def test_point_source_class_changes_lineage_only(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    old = (
        b"source_class: assumption_with_sensitivity_marker, citation_id: robinot_2026, "
        b"extraction_note: initial_temperature_not_reported_room_temperature_assumption"
    )
    new = old.replace(
        b"source_class: assumption_with_sensitivity_marker",
        b"source_class: literature_sidecar",
        1,
    )
    mutant = build_deck_request(_rewrite(tmp_path, old, new, count=1))
    assert mutant.cache_request == robinot_deck.cache_request
    assert mutant.lineage != robinot_deck.lineage
    point = mutant.lineage["schedule"]["melt_temperature_C"][0]
    assert point["source_class"] == "literature_sidecar"
    assert point["input_role"] == "printed_input"
    assert (
        robinot_deck.lineage["schedule"]["melt_temperature_C"][0]["input_role"]
        == "assumption"
    )


def test_remediation_setpoint_stays_out_of_the_faithful_request(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    path = _rewrite(
        tmp_path,
        b"setpoint_mbar: 1.0e-4",
        b"setpoint_mbar: 2.0e-4",
        count=1,
    )
    faithful = build_deck_request(path)
    assert faithful.cache_request == robinot_deck.cache_request
    remediated = build_deck_request(path, leg="remediation")
    assert remediated.cache_request["session"]["mitigation"] == "pO2_cover"
    assert remediated.cache_request["schedule"]["pO2_cover"]["setpoint_mbar"] == pytest.approx(2.0e-4)
    assert remediated.cache_request != robinot_deck.cache_request
    assert "source_class" not in json.dumps(remediated.cache_request)


def test_one_scoring_call_per_deck_then_a_warm_hit(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _OK_DOCUMENT

    first = score_bound_decks(
        [MASS, FEED, PARENT, DISTINCT, MODEL, FIG4],
        solve=solve,
    )
    assert calls["n"] == 1
    assert len(first.requests) == 1
    assert first.requests[0].cache_request == robinot_deck.cache_request
    assert first.residuals == ()
    assert first.defaults_applied == ()
    assert clean_headline(first.residuals) == ()
    by_id = {item.observation_id: item for item in first.dispositions}
    assert by_id[MASS].trial_id == by_id[FEED].trial_id == "robinot_2026_main"
    assert by_id[PARENT].reason == "same_gas_measurement"
    assert by_id[DISTINCT].reason == "distinct_trial"
    assert by_id[MODEL].kind == "not_a_measurement"
    assert by_id[FIG4].kind == "no_deck"

    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    second = score_bound_decks([FEED, MASS], solve=solve)
    assert calls["n"] == 1
    assert second.projections == first.projections
    assert _stored_rows(path) == [("simulator", "1")]


def test_unbound_rows_do_not_call_the_solver() -> None:
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        raise AssertionError("unbound row reached the solver")

    result = score_bound_decks(
        [POMEROY, *SESKO, DISTINCT, MODEL, FIG4, "not-an-observation"],
        solve=solve,
    )
    assert calls["n"] == 0
    assert result.requests == ()
    assert result.projections == ()
    assert result.residuals == ()
    assert result.defaults_applied == ()


def test_configured_cache_round_trip_survives_a_cleared_process(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _OK_DOCUMENT

    first = run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 1
    assert first["status"] == "ok"
    assert "note" not in first["per_hour_summary"][0]
    assert "expected_value" not in json.dumps(first)
    assert "trajectory_comparison" not in first
    assert "0.0105" not in json.dumps(first)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    second = run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 1
    assert second == first
    assert _stored_rows(path) == [("simulator", "1")]

    engine_cache._close_connections()
    cold_path = Path(str(path) + ".cold")
    with sqlite3.connect(cold_path) as connection:
        blob = connection.execute("SELECT value FROM engine_values").fetchone()[0]
    cold = engine_cache.unpack(zlib.decompress(blob))
    assert cold["preset_digest"] == robinot_deck.preset_digest
    assert str(cold["runtime_identity"]).startswith("source-sha256:")


def test_unconfigured_cache_solves_every_call(
    robinot_deck: DeckRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_BOX", raising=False)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _OK_DOCUMENT

    run_cached_deck(robinot_deck, solve)
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(
            {
                "status": "partial",
                "per_hour_summary": _OK_DOCUMENT["per_hour_summary"],
                "final": {"pump_outlet_by_species_kg": {}},
            },
            id="partial",
        ),
        pytest.param(
            {
                "status": "failed",
                "per_hour_summary": _OK_DOCUMENT["per_hour_summary"],
                "final": {"pump_outlet_by_species_kg": {}},
            },
            id="failed",
        ),
        pytest.param(
            {
                "status": "refused",
                "per_hour_summary": _OK_DOCUMENT["per_hour_summary"],
                "final": {"pump_outlet_by_species_kg": {}},
            },
            id="refused",
        ),
        pytest.param({"O2": 0.0105}, id="scalar"),
    ],
)
def test_partial_or_failed_runs_are_not_cached_numbers(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: dict,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return document

    run_cached_deck(robinot_deck, solve)
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2
    assert _stored_rows(path) == []
    _hot, cacheable = project_whole_run(document)
    assert cacheable is False


def test_a_successful_run_without_a_pump_outlet_is_still_a_projection() -> None:
    hot, cacheable = project_whole_run({
        "status": "ok",
        "per_hour_summary": [{
            "hour": 1,
            "O2_source_side_potential_kg_cumulative": 0.001,
        }],
        "final": {},
    })
    assert cacheable is True
    assert hot["final"] == {}
    assert hot["per_hour_summary"][0]["hour"] == 1.0


def test_chemistry_version_bump_misses_on_an_unchanged_deck(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _OK_DOCUMENT

    run_cached_deck(robinot_deck, solve)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    monkeypatch.setitem(engine_cache.ENGINE_CHEMISTRY_VERSION, "simulator", 2)
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2
    assert _key(robinot_deck.cache_request) != engine_cache.engine_cache_key(
        "simulator", 2, robinot_deck.cache_request
    )
    assert {row[1] for row in _stored_rows(path)} == {"1", "2"}


def test_a_defaulted_residual_is_flagged_and_out_of_the_clean_headline() -> None:
    flagged = BenchResidual(used_default=True, flag="d460:stated_default", value=0.01)
    measured = BenchResidual(used_default=False, flag=None, value=0.02)
    assert clean_headline((flagged, measured)) == (measured,)
    stripped = BenchResidual(
        used_default=flagged.used_default,
        flag=None,
        value=flagged.value,
    )
    with pytest.raises(IntegratedBenchContractError) as caught:
        clean_headline((stripped, measured))
    assert caught.value.reason == "missing_flag"
