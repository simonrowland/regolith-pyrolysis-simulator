"""Binding and cache contract for integrated-bench decks. No residuals."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import zlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.melt_backend import openimcc_bridge
from simulator.battery.integrated_bench import (
    POMEROY_PRESET,
    ROBINOT_PRESET,
    SESKO_PRESET,
    DeckRequest,
    IntegratedBenchContractError,
    bound_observation_ids,
    build_deck_request,
    classify_observation,
    consumed_chamber_pressure_dt_s,
    deck_cache_request,
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
def _ok_document(steps: int) -> dict:
    """A complete runner document for ``steps`` executed hours."""

    rows = []
    for hour in range(1, steps + 1):
        rows.append({
            "hour": hour,
            "O2_source_side_potential_kg_cumulative": 0.001 * hour,
            "P_total_bar": 0.013,
            "pO2_bar": 1.0e-6,
            "vapor_species_kg_hr": {"O2": 1.0e-4},
            "condensation_refusals_by_species": {"SiO": {"refused": True}},
            "vapour_batch_summary": {
                "metadata": {
                    "extrapolation_notices_by_species": {
                        "SiO": {"kind": "pressure"},
                    },
                    "wall_deposit_sticking_authority": {
                        "out_of_domain_alpha_count": 0,
                        "status_bearing_alpha_count": 1,
                    },
                    "note": "should be stripped",
                },
                "expected_value": 0.0105,
            },
            "note": "should be stripped",
        })
    return {
        "status": "ok",
        "per_hour_summary": rows,
        "final_state": {"process.cleaned_melt": {"SiO2": 1.0}},
        "final": {
            "pump_outlet_by_species_kg": {},
            "expected_value": 0.0105,
        },
        "degraded_path_engagement": {"evaporation": {"engaged": False}},
        "melt_redox_gate_floor_fallback_engagement": {"engaged": False},
        "condensation_refusals_by_species": {"SiO": {"n": 0}},
        "note": "should be stripped",
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


def test_one_preset_snapshot_feeds_admission_spec_and_cache_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, tmp_path)
    original = (REPO / ROBINOT_PRESET).read_bytes()
    edited = original.replace(b"mass_g: 3.38", b"mass_g: 3.39")
    assert edited != original
    path = tmp_path / "robinot.yaml"
    path.write_bytes(original)
    read_preset = integrated_bench._read_preset_bytes
    read_count = {"n": 0}

    def read_then_edit(target: Path) -> bytes:
        read_count["n"] += 1
        snapshot = read_preset(target)
        target.write_bytes(edited)
        return snapshot

    monkeypatch.setattr(integrated_bench, "_read_preset_bytes", read_then_edit)
    deck = build_deck_request(path)
    assert read_count["n"] == 1
    assert deck.run.mass_kg == pytest.approx(0.00338)
    assert deck.cache_request["preset_sha256"] == hashlib.sha256(original).hexdigest()
    assert deck.preset_digest == "sha256:" + hashlib.sha256(original).hexdigest()
    solver_masses: list[float] = []

    def solve(_request):
        solver_masses.append(deck.run.mass_kg)
        return _ok_document(deck.requested_steps)

    run_cached_deck(deck, solve)
    assert solver_masses == [pytest.approx(0.00338)]


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _blob(value) -> str:
    return json.dumps(_plain(value))


def _key(request) -> bytes:
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    return engine_cache.engine_cache_key("simulator", version, request)


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
    assert engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"] == 4
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


def test_simulator_runner_hash_is_not_a_declared_cache_pin(
    robinot_deck: DeckRequest,
) -> None:
    assert "simulator" not in robinot_deck.cache_request[
        "external_engines"
    ]["engine_identities"]


def test_deck_module_does_not_reown_mass_loss_area_or_scoring() -> None:
    source = Path(integrated_bench.__file__).read_text(encoding="utf-8")
    for banned in (
        "predict_mass_loss_fraction",
        "gram_lab_exposed_melt_area_bridge",
        "MELT_DENSITY",
        "SCORE_ENGINE_SET",
        "NoticeKind",
        "simulator.battery.score",
        "BenchResidual",
        "clean_headline",
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
    resolved = robinot_deck.resolved_configuration
    assert robinot_deck.cache_bypass is None
    assert set(request) == {
        "trees",
        "preset_sha256",
        "leg",
        "external_engines",
        "python_version",
    }
    assert request["trees"] == integrated_bench._tree_ids(
        robinot_deck.source_commit
    )
    raw = (REPO / ROBINOT_PRESET).read_bytes()
    assert request["preset_sha256"] == hashlib.sha256(raw).hexdigest()
    assert request["leg"] == "faithful"
    assert request["python_version"] == platform.python_version()
    external = request["external_engines"]
    assert set(external) == {"engines_local_toml_sha256", "engine_identities"}
    local_hash = external["engines_local_toml_sha256"]
    assert local_hash == "absent" or len(local_hash) == 64
    assert external["engine_identities"]["openimcc"].startswith("commit:66ec4a3")
    blob = _blob(request)
    for token in _MEASURED_YIELDS:
        assert token not in blob
    for banned in (
        "input_role",
        "elemental_composition_wt_pct",
        "conditional_covariate",
        "printed_input",
        "trial_id",
        "robinot_2026_main",
        "expected_value",
        "uncertainty",
        "paper_id",
        "trajectory_comparison",
        "comparison_contract",
    ):
        assert banned not in blob
    holder_curve = resolved["schedule"]["surface_temperature_C"]["holder"]
    assert holder_curve[-1]["value"] == pytest.approx(1500.0)
    assert robinot_deck.preset_digest.startswith("sha256:")
    assert robinot_deck.preset_digest not in blob

    session = resolved["session"]
    assert session["backend_name"] == "internal-analytical"
    assert session["backend_policy"] == "runner-strict"
    assert session["leg"] == "faithful"
    assert session["mitigation"] == "none"
    assert session["mass_kg"] == pytest.approx(3.38 / 1000.0)
    # The printed schedule hour stays 1.0. The executed window is 59 steps
    # of 60 s because the solar-power trace ends at 59.5 min.
    assert session["hours"] == 59
    assert session["effective_dt_s"] == 60.0
    assert session["effective_dt_s"] == consumed_chamber_pressure_dt_s(
        robinot_deck.run
    )
    assert resolved["schedule"]["duration_h"] == pytest.approx(1.0)
    assert "pO2_cover" not in resolved["schedule"]
    assert "furnace_power_driver" in resolved["schedule"]
    assert "expected_value" not in resolved["schedule"]["furnace_power_driver"]

    power = resolved["schedule"]["furnace_power_driver"]
    assert power["pellet_diameter_m"] == pytest.approx(0.013)
    assert power["pellet_height_m"] == pytest.approx(0.010)
    assert power["mass_kg"] == pytest.approx(3.38 / 1000.0)
    from simulator.condensation import read_gram_lab_exposed_melt_area

    area = read_gram_lab_exposed_melt_area(
        robinot_deck.run.setpoints_patch["lab_geometry"]
    )
    assert area is not None
    assert resolved["exposed_area_m2"] == pytest.approx(area)
    holder = next(
        surface
        for surface in resolved["preset"]["lab_geometry"]["surfaces"]
        if surface["id"] == "holder"
    )
    assert holder["equivalent_diameter_m"] == pytest.approx(0.02)
    assert holder["area_m2"] == pytest.approx(0.000314)
    assert holder["temperature_profile"] == "holder"
    assert resolved["preset"]["lab_geometry"]["sample"]["pressed_at_tons"] == (
        pytest.approx(2.5)
    )
    assert resolved["preset"]["lab_geometry"]["pump"]["vacuum_limit_mbar"] == (
        pytest.approx(7.0e-3)
    )
    assert "Edwards" not in blob
    assert resolved["feedstock"]["id"] == "lunar_eac_1a"
    assert "SiO2" in resolved["inventory"]["raw_components_kg"]
    assert "SiO2" in resolved["feedstock"]["entry"]["composition_wt_pct"]
    pressures = resolved["schedule"]["chamber_pressure_mbar"]
    assert [point["value"] for point in pressures] == pytest.approx([13.0, 13.0])
    assert resolved["schedule"]["gas_boundary"]["imposed_flow"]["value"] == pytest.approx(0.3)

    lineage = robinot_deck.lineage
    assert all(
        point["input_role"] == "conditional_covariate"
        for point in lineage["schedule"]["chamber_pressure_mbar"]
    )
    melt = lineage["schedule"]["melt_temperature_C"]
    assert melt[0]["input_role"] == "assumption"
    assert melt[-1]["input_role"] == "conditional_covariate"
    assert lineage["schedule"]["duration_h"] == {
        "input_role": "outcome_conditioned",
        "reason": "stop_selected_when_o2_production_ceased",
    }
    assert (
        lineage["schedule"]["chamber_pressure_history"]
        == "assumed_constant_between_knots"
    )
    assert lineage["schedule"]["furnace_ceiling_C"] == "conditional_covariate"
    assert lineage["schedule"]["interpolation"] == "assumption"
    assert lineage["schedule"]["gas_boundary"]["background_gas"] == "printed_input"
    assert lineage["schedule"]["gas_boundary"]["imposed_flow"] == "conditional_covariate"
    assert lineage["schedule"]["gas_boundary"]["pressure_control"] == "assumption"
    assert lineage["schedule"]["furnace_power_driver"] == "measured_driver"
    notice = lineage["exposed_area_notice"]
    assert notice["reason"] == "liquid_footprint_not_printed"
    assert notice["ruling_ref"] == "t-1892_2026-11-22"
    assert notice["derivation"] == "pi*(D/2)^2 from the reported D=13 mm"
    assert notice["value"] == pytest.approx(area)
    assert notice["unit"] == "m2"
    assert notice["kind"] == "deck_declared_assumption"
    assert "liquid_footprint_not_printed" not in blob
    assert "deck_declared_assumption" not in blob
    assert "t-1892_2026-11-22" not in blob
    assert lineage["feedstock"] == {
        "input_role": "proxy",
        "reason": "eac_1a_proxy_for_robinot_charge",
        "registered_id": "lunar_eac_1a",
    }
    key = _key(request)
    assert key[0] == 7
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    assert key[1:3] == version.to_bytes(2, "big")


def test_robinot_lineage_is_on_the_returned_request(
    robinot_deck: DeckRequest,
) -> None:
    """The three boundary roles are on the request, not inside the cache key."""

    lineage = robinot_deck.lineage
    assert lineage["schedule"]["duration_h"] == {
        "input_role": "outcome_conditioned",
        "reason": "stop_selected_when_o2_production_ceased",
    }
    assert (
        lineage["schedule"]["chamber_pressure_history"]
        == "assumed_constant_between_knots"
    )
    assert lineage["feedstock"]["input_role"] == "proxy"
    assert lineage["feedstock"]["reason"] == "eac_1a_proxy_for_robinot_charge"
    assert lineage["feedstock"]["registered_id"] == "lunar_eac_1a"
    notice = lineage["exposed_area_notice"]
    assert notice["reason"] == "liquid_footprint_not_printed"
    assert notice["ruling_ref"] == "t-1892_2026-11-22"
    assert "derivation" in notice
    assert notice["unit"] == "m2"
    blob = _blob(robinot_deck.cache_request)
    assert "outcome_conditioned" not in blob
    assert "eac_1a_proxy_for_robinot_charge" not in blob
    assert "assumed_constant_between_knots" not in blob
    assert robinot_deck.resolved_configuration["session"]["hours"] == 59
    assert robinot_deck.resolved_configuration["session"]["effective_dt_s"] == 60.0


def test_effective_timestep_is_the_chamber_pressure_step(
    robinot_deck: DeckRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packed step is the dt the real chamber-pressure call receives."""

    from simulator import teststand

    step = consumed_chamber_pressure_dt_s(robinot_deck.run)
    assert step == 60.0
    assert robinot_deck.resolved_configuration["session"]["effective_dt_s"] == step

    received: list[float] = []
    original = teststand.apply_process_chamber_pressure

    def capture(sim, *, dt_s):
        received.append(dt_s)
        return original(sim, dt_s=dt_s)

    monkeypatch.setattr(teststand, "apply_process_chamber_pressure", capture)
    session = robinot_deck.run._start_session()
    robinot_deck.run._apply_lab_area_bridge(
        session.simulator,
        robinot_deck.run._lab_area_bridge(),
    )
    session.advance()
    assert received
    assert set(received) == {robinot_deck.resolved_configuration["session"]["effective_dt_s"]}
    assert set(received) == {60.0}


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
    ],
)
def test_comparison_metadata_changes_the_preset_bytes_and_keeps_the_trees(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    old: bytes,
    new: bytes,
    count: int,
) -> None:
    """A preset byte edit changes the preset digest. The git trees stay."""

    mutant = build_deck_request(_rewrite(tmp_path, old, new, count=count))
    assert mutant.cache_request["trees"] == robinot_deck.cache_request["trees"]
    assert mutant.cache_request["preset_sha256"] != (
        robinot_deck.cache_request["preset_sha256"]
    )
    assert mutant.cache_request["leg"] == robinot_deck.cache_request["leg"]
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)
    blob = _blob(mutant.cache_request)
    for token in _MEASURED_YIELDS:
        assert token not in blob


def test_sticking_provenance_coefficient_is_over_keyed(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    """A sticking-coefficient byte changes the preset digest and the key.

    The resolved pairs still show the coefficient the runner copied.
    """

    mutant = build_deck_request(_rewrite(
        tmp_path,
        b"sticking_coefficient: 1.0\n",
        b"sticking_coefficient: 0.5\n",
        count=2,
    ))
    base_pairs = robinot_deck.resolved_configuration["preset"]["sticking_provenance"][
        "species_surface_pairs"
    ]
    mutant_pairs = mutant.resolved_configuration["preset"]["sticking_provenance"][
        "species_surface_pairs"
    ]
    assert [pair["sticking_coefficient"] for pair in base_pairs] == [1.0, 1.0]
    assert [pair["sticking_coefficient"] for pair in mutant_pairs] == [0.5, 0.5]
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)


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
    assert mutant.resolved_configuration != robinot_deck.resolved_configuration
    assert mutant.cache_request["trees"] == robinot_deck.cache_request["trees"]
    assert mutant.cache_request["preset_sha256"] != (
        robinot_deck.cache_request["preset_sha256"]
    )
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)


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
    assert mutant.resolved_configuration["feedstock"]["id"] == "lunar_eac_1a"
    base_sio2 = robinot_deck.resolved_configuration["inventory"]["raw_components_kg"]["SiO2"]
    mutant_sio2 = mutant.resolved_configuration["inventory"]["raw_components_kg"]["SiO2"]
    assert mutant_sio2 != pytest.approx(base_sio2)
    assert mutant.cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert _key(mutant.cache_request) == _key(robinot_deck.cache_request)
    assert "elemental_composition_wt_pct" not in _blob(mutant.cache_request)
    assert "SiO2" in mutant.resolved_configuration["feedstock"]["entry"]["composition_wt_pct"]


def test_point_source_class_changes_lineage_and_the_preset_digest(
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
    assert mutant.cache_request["trees"] == robinot_deck.cache_request["trees"]
    assert mutant.cache_request["preset_sha256"] != (
        robinot_deck.cache_request["preset_sha256"]
    )
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)
    assert mutant.lineage != robinot_deck.lineage
    point = mutant.lineage["schedule"]["melt_temperature_C"][0]
    assert point["source_class"] == "literature_sidecar"
    assert point["input_role"] == "printed_input"
    assert (
        robinot_deck.lineage["schedule"]["melt_temperature_C"][0]["input_role"]
        == "assumption"
    )


def test_remediation_setpoint_is_in_the_resolved_key(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    """The other leg lives in the resolved preset, so its setpoint is keyed.

    The faithful mitigation token stays ``none``. The two legs still do
    not share a key.
    """

    path = _rewrite(
        tmp_path,
        b"setpoint_mbar: 1.0e-4",
        b"setpoint_mbar: 2.0e-4",
        count=1,
    )
    faithful = build_deck_request(path)
    assert faithful.resolved_configuration["session"]["mitigation"] == "none"
    assert faithful.cache_request != robinot_deck.cache_request
    assert _key(faithful.cache_request) != _key(robinot_deck.cache_request)
    remediated = build_deck_request(path, leg="remediation")
    assert remediated.resolved_configuration["session"]["mitigation"] == "pO2_cover"
    assert remediated.resolved_configuration["schedule"]["pO2_cover"]["setpoint_mbar"] == pytest.approx(2.0e-4)
    assert faithful.cache_request["preset_sha256"] == (
        remediated.cache_request["preset_sha256"]
    )
    assert faithful.cache_request["leg"] == "faithful"
    assert remediated.cache_request["leg"] == "remediation"
    assert _key(remediated.cache_request) != _key(faithful.cache_request)
    assert "source_class" not in _blob(remediated.cache_request)


def test_one_scoring_call_per_deck_then_a_warm_hit(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _ok_document(robinot_deck.requested_steps)

    first = score_bound_decks(
        [MASS, FEED, PARENT, DISTINCT, MODEL, FIG4],
        solve=solve,
    )
    assert calls["n"] == 1
    assert len(first.requests) == 1
    assert first.requests[0].cache_request == robinot_deck.cache_request
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
    assert _stored_rows(path) == [("simulator", "2")]


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


def test_configured_cache_round_trip_survives_a_cleared_process(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _ok_document(robinot_deck.requested_steps)

    first = run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 1
    assert first["status"] == "ok"
    assert len(first["per_hour_summary"]) == robinot_deck.requested_steps
    assert "note" not in first["per_hour_summary"][0]
    assert "note" not in first["per_hour_summary"][0]["vapour_batch_summary"]["metadata"]
    assert "expected_value" not in json.dumps(first)
    assert "trajectory_comparison" not in first
    assert "0.0105" not in json.dumps(first)
    assert first["degraded_path_engagement"]["evaporation"]["engaged"] is False
    assert first["melt_redox_gate_floor_fallback_engagement"]["engaged"] is False
    assert first["condensation_refusals_by_species"]["SiO"]["n"] == 0.0
    row = first["per_hour_summary"][0]
    assert row["P_total_bar"] == pytest.approx(0.013)
    assert row["pO2_bar"] == pytest.approx(1.0e-6)
    assert row["condensation_refusals_by_species"]["SiO"]["refused"] is True
    metadata = row["vapour_batch_summary"]["metadata"]
    assert metadata["extrapolation_notices_by_species"]["SiO"]["kind"] == "pressure"
    assert metadata["wall_deposit_sticking_authority"]["status_bearing_alpha_count"] == (
        pytest.approx(1.0)
    )
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    second = run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 1
    assert second == first
    assert _stored_rows(path) == [("simulator", "2")]

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
        return _ok_document(robinot_deck.requested_steps)

    run_cached_deck(robinot_deck, solve)
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(
            {
                "status": "partial",
                "per_hour_summary": _ok_document(1)["per_hour_summary"],
                "final": {"pump_outlet_by_species_kg": {}},
            },
            id="partial",
        ),
        pytest.param(
            {
                "status": "failed",
                "per_hour_summary": _ok_document(1)["per_hour_summary"],
                "final": {"pump_outlet_by_species_kg": {}},
            },
            id="failed",
        ),
        pytest.param(
            {
                "status": "refused",
                "per_hour_summary": _ok_document(1)["per_hour_summary"],
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
    _hot, cacheable = project_whole_run(document, requested_steps=1)
    assert cacheable is False


def test_a_successful_run_without_a_pump_outlet_is_still_a_projection() -> None:
    """An empty species map and the unavailable pump token stay cacheable.

    ``final: {}`` is not a report. An index-only hour is not a step.
    """

    hot, cacheable = project_whole_run({
        "status": "ok",
        "per_hour_summary": [{
            "hour": 1,
            "O2_source_side_potential_kg_cumulative": 0.001,
            "P_total_bar": 0.013,
            "pO2_bar": 1.0e-6,
        }],
        "final_state": {"process.cleaned_melt": {"SiO2": 1.0}},
        "final": {
            "pump_outlet_by_species_kg": "not_applicable_until_p0",
            "wall_deposit_by_species_kg": {},
            "deposit_by_surface_species_kg": {},
        },
    }, requested_steps=1)
    assert cacheable is True
    assert hot["final"]["pump_outlet_by_species_kg"] == "not_applicable_until_p0"
    assert hot["final"]["wall_deposit_by_species_kg"] == {}
    assert hot["final_state"]["process.cleaned_melt"]["SiO2"] == pytest.approx(1.0)
    again, again_cacheable = project_whole_run(hot, requested_steps=1)
    assert again_cacheable is True
    assert again["status"] == "ok"

    with_empty_species = {
        "status": "ok",
        "per_hour_summary": [{
            "hour": 1,
            "P_total_bar": 0.013,
            "vapor_species_kg_hr": {},
        }],
        "final_state": {"process.cleaned_melt": {"SiO2": 1.0}},
        "final": {
            "pump_outlet_by_species_kg": "not_applicable_until_p0",
            "wall_deposit_by_species_kg": {},
        },
    }
    empty_species, empty_species_cacheable = project_whole_run(
        with_empty_species,
        requested_steps=1,
    )
    assert empty_species_cacheable is True
    assert empty_species["per_hour_summary"][0]["vapor_species_kg_hr"] == {}
    assert empty_species["final"]["pump_outlet_by_species_kg"] == (
        "not_applicable_until_p0"
    )

    empty_final, empty_cacheable = project_whole_run({
        "status": "ok",
        "per_hour_summary": [{
            "hour": 1,
            "P_total_bar": 0.013,
        }],
        "final_state": {"process.cleaned_melt": {"SiO2": 1.0}},
        "final": {},
    }, requested_steps=1)
    assert empty_cacheable is False
    assert empty_final["status"] == "partial"
    assert empty_final["reason"] == "missing_final_report"

    index_only, index_cacheable = project_whole_run(
        {"status": "ok", "per_hour_summary": [{"hour": 1}], "final": {}},
        requested_steps=1,
    )
    assert index_cacheable is False
    assert index_only["status"] == "partial"
    assert index_only["reason"] == "index_only_row"
    assert index_only["per_hour_summary"][0]["hour"] == 1.0


def test_chemistry_version_bump_misses_on_an_unchanged_deck(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _ok_document(robinot_deck.requested_steps)

    run_cached_deck(robinot_deck, solve)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._close_connections()
    current_version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    previous_version = current_version - 1
    monkeypatch.setitem(
        engine_cache.ENGINE_CHEMISTRY_VERSION,
        "simulator",
        previous_version,
    )
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2
    assert engine_cache.engine_cache_key(
        "simulator", previous_version, robinot_deck.cache_request
    ) != engine_cache.engine_cache_key(
        "simulator", current_version, robinot_deck.cache_request
    )
    assert {row[1] for row in _stored_rows(path)} == {
        str(previous_version),
        str(current_version),
    }


def test_bulk_additions_fe_changes_the_cache_key(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    """Fe bulk addition changes the key while the id and oxide vector stay."""

    feedstocks = _rewrite(
        tmp_path,
        b'  sum_check: 98.4\n  composition_status: "primary_xrf_loadable"\n',
        (
            b"  sum_check: 98.4\n"
            b"  bulk_additions: {Fe: 1.0}\n"
            b'  composition_status: "primary_xrf_loadable"\n'
        ),
        count=1,
        source=REPO / "data" / "feedstocks.yaml",
        name="feedstocks.yaml",
    )
    mutant = build_deck_request(REPO / ROBINOT_PRESET, feedstocks_path=feedstocks)
    assert mutant.resolved_configuration["feedstock"]["id"] == (
        robinot_deck.resolved_configuration["feedstock"]["id"]
    )
    assert b"SiO2:  43.7" in feedstocks.read_bytes()
    base = robinot_deck.resolved_configuration["inventory"]["raw_components_kg"]
    changed = mutant.resolved_configuration["inventory"]["raw_components_kg"]
    assert "Fe" not in base
    assert changed["Fe"] > 0.0
    assert mutant.cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert _key(mutant.cache_request) == _key(robinot_deck.cache_request)
    assert "elemental_composition_wt_pct" not in _blob(mutant.cache_request)


def test_quoted_exposed_area_changes_the_cache_key(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    """Quoted areas the runner admits must not share a packed key."""

    low = build_deck_request(_rewrite(
        tmp_path,
        b"exposed_melt_area_m2: 0.00013273228961416876",
        b'exposed_melt_area_m2: "0.000123"',
        count=1,
        name="area-low.yaml",
    ))
    high = build_deck_request(_rewrite(
        tmp_path,
        b"exposed_melt_area_m2: 0.00013273228961416876",
        b'exposed_melt_area_m2: "0.000234"',
        count=1,
        name="area-high.yaml",
    ))
    assert low.resolved_configuration["feedstock"] == high.resolved_configuration["feedstock"]
    assert low.resolved_configuration["feedstock"]["id"] == (
        robinot_deck.resolved_configuration["feedstock"]["id"]
    )
    assert low.resolved_configuration["exposed_area_m2"] == pytest.approx(0.000123)
    assert high.resolved_configuration["exposed_area_m2"] == pytest.approx(0.000234)
    assert low.lineage["exposed_area_notice"]["reason"] == (
        "liquid_footprint_not_printed"
    )
    assert low.lineage["exposed_area_notice"]["value"] == pytest.approx(0.000123)
    assert "liquid_footprint_not_printed" not in _blob(low.cache_request)
    assert _key(low.cache_request) != _key(high.cache_request)


def test_unreviewed_fields_are_keyed_deny_by_default(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    """A dynamic id is legal. ``uncertainty`` and ``target`` are not slots.

    The preset bytes change, so the key changes. The deck still builds.
    """

    target = build_deck_request(_rewrite(
        tmp_path,
        b"schema_version: vacuum_pyrolysis_preset.v1\n",
        (
            b"schema_version: vacuum_pyrolysis_preset.v1\n"
            b"target: 0.0105\n"
            b"observed_o2_mass_fraction: 0.0105\n"
        ),
        count=1,
        name="target.yaml",
    ))
    assert target.resolved_configuration["preset"]["target"] == pytest.approx(0.0105)
    assert target.resolved_configuration["preset"]["observed_o2_mass_fraction"] == (
        pytest.approx(0.0105)
    )
    assert _key(target.cache_request) != _key(robinot_deck.cache_request)

    uncertain = build_deck_request(_rewrite(
        tmp_path,
        b"schema_version: vacuum_pyrolysis_preset.v1\n",
        (
            b"schema_version: vacuum_pyrolysis_preset.v1\n"
            b"uncertainty: 0.001\n"
        ),
        count=1,
        name="uncertainty.yaml",
    ))
    assert uncertain.resolved_configuration["preset"]["uncertainty"] == (
        pytest.approx(0.001)
    )
    assert uncertain.cache_bypass is None
    assert _key(uncertain.cache_request) != _key(robinot_deck.cache_request)
    assert _key(uncertain.cache_request) != _key(target.cache_request)


def test_checked_in_robinot_deck_starts_the_real_runner() -> None:
    """Build the checked-in deck and apply the runner's area bridge.

    This does not execute the 59 physics steps. ``run`` is the production
    solve; this test stops once the melt face the step would consume is set.
    """

    from simulator.condensation import read_gram_lab_exposed_melt_area
    from simulator.runner import PyrolysisRun

    deck = build_deck_request(REPO / ROBINOT_PRESET)
    assert isinstance(deck.run, PyrolysisRun)
    assert deck.requested_steps == 59
    assert deck.run.process_dt_s == 60.0
    assert deck.run.hours == 59
    session = deck.run._start_session()
    bridge = deck.run._lab_area_bridge()
    deck.run._apply_lab_area_bridge(session.simulator, bridge)
    geometry = deck.run.setpoints_patch["lab_geometry"]
    area = read_gram_lab_exposed_melt_area(geometry)
    assert area is not None
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(area)
    assert deck.resolved_configuration["exposed_area_m2"] == pytest.approx(area)
    assert consumed_chamber_pressure_dt_s(deck.run) == 60.0


def test_projection_keeps_runner_diagnostics() -> None:
    document = _ok_document(2)
    hot, cacheable = project_whole_run(document, requested_steps=2)
    assert cacheable is True
    assert hot["status"] == "ok"
    assert hot["degraded_path_engagement"]["evaporation"]["engaged"] is False
    assert hot["melt_redox_gate_floor_fallback_engagement"]["engaged"] is False
    assert hot["condensation_refusals_by_species"]["SiO"]["n"] == 0.0
    row = hot["per_hour_summary"][0]
    assert row["P_total_bar"] == pytest.approx(0.013)
    assert row["pO2_bar"] == pytest.approx(1.0e-6)
    metadata = row["vapour_batch_summary"]["metadata"]
    assert metadata["extrapolation_notices_by_species"]["SiO"]["kind"] == "pressure"
    assert "wall_deposit_sticking_authority" in metadata
    assert "note" not in metadata
    assert "expected_value" not in json.dumps(hot)
    assert "trajectory_comparison" not in hot
    assert "note" not in json.dumps(hot)


def test_incomplete_or_malformed_ok_is_partial_and_not_cached(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _configure(monkeypatch, tmp_path)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        return _ok_document(1)

    first = run_cached_deck(robinot_deck, solve)
    run_cached_deck(robinot_deck, solve)
    assert calls["n"] == 2
    assert first["status"] == "partial"
    assert first["reason"] == "incomplete_steps"
    assert len(first["per_hour_summary"]) == 1
    assert _stored_rows(path) == []

    broken = _ok_document(robinot_deck.requested_steps)
    broken["per_hour_summary"][4] = "not-a-row"
    hot, cacheable = project_whole_run(
        broken,
        requested_steps=robinot_deck.requested_steps,
    )
    assert cacheable is False
    assert hot["status"] == "partial"
    assert hot["reason"] == "malformed_row"
    assert hot["per_hour_summary"][4] == {"status": "malformed_row"}
    assert len(hot["per_hour_summary"]) == robinot_deck.requested_steps

    no_final = _ok_document(1)
    del no_final["final"]
    missing, missing_cacheable = project_whole_run(no_final, requested_steps=1)
    assert missing_cacheable is False
    assert missing["status"] == "partial"
    assert missing["reason"] == "missing_final_report"

    no_state = _ok_document(1)
    del no_state["final_state"]
    missing_state, state_cacheable = project_whole_run(no_state, requested_steps=1)
    assert state_cacheable is False
    assert missing_state["status"] == "partial"
    assert missing_state["reason"] == "missing_final_state"

    poisoned = _ok_document(1)
    poisoned["per_hour_summary"][0]["P_total_bar"] = float("nan")
    bad_number, bad_cacheable = project_whole_run(poisoned, requested_steps=1)
    assert bad_cacheable is False
    assert bad_number["reason"] == "malformed_row"
    assert bad_number["per_hour_summary"][0]["status"] == "malformed_row"


def test_cache_hit_must_be_a_complete_ok(
    robinot_deck: DeckRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call(_engine, _request, _solve):
        return {
            "status": "ok",
            "per_hour_summary": [{"hour": 1.0}],
            "final": {},
        }

    monkeypatch.setattr(integrated_bench, "cached_engine_call", fake_call)
    with pytest.raises(IntegratedBenchContractError) as caught:
        run_cached_deck(
            robinot_deck,
            lambda _request: _ok_document(robinot_deck.requested_steps),
        )
    assert caught.value.reason == "cache_hit_not_ok"


def test_quoted_holder_diameters_change_the_resolved_surface_key(
    tmp_path: Path,
) -> None:
    """Quoted holder diameters the runner admits pack as the same number.

    The file bytes still differ, so the deck keys differ. A different
    number is a different packed value and a different key. The packed
    value is the surface's ``equivalent_diameter_m``.
    """

    from simulator.lab_geometry import parse_lab_geometry

    source = (REPO / ROBINOT_PRESET).read_bytes()
    original = b"equivalent_diameter_m: 0.02\n"
    assert source.count(original) == 1
    same = tmp_path / "deck-quoted-same.yaml"
    same.write_bytes(source.replace(
        original,
        b'equivalent_diameter_m: "0.02"\n',
    ))
    quoted_same = build_deck_request(same)
    checked_in = build_deck_request(REPO / ROBINOT_PRESET)
    assert quoted_same.cache_request["trees"] == checked_in.cache_request["trees"]
    assert quoted_same.cache_request["preset_sha256"] != (
        checked_in.cache_request["preset_sha256"]
    )
    assert _key(quoted_same.cache_request) != _key(checked_in.cache_request)
    quoted_holder = next(
        surface
        for surface in quoted_same.resolved_configuration["preset"]["lab_geometry"]["surfaces"]
        if surface["id"] == "holder"
    )
    checked_holder = next(
        surface
        for surface in checked_in.resolved_configuration["preset"]["lab_geometry"]["surfaces"]
        if surface["id"] == "holder"
    )
    assert quoted_holder["equivalent_diameter_m"] == pytest.approx(
        checked_holder["equivalent_diameter_m"]
    )
    decks = []
    diameters = []
    packed = []
    for diameter in (0.02, 0.03):
        path = tmp_path / f"deck-{diameter}.yaml"
        path.write_bytes(source.replace(
            original,
            f'equivalent_diameter_m: "{diameter}"\n'.encode(),
        ))
        deck = build_deck_request(path)
        decks.append(deck)
        geometry = parse_lab_geometry(
            deck.run.setpoints_patch["lab_geometry"],
            allow_temperature_profiles=True,
        )
        holder = next(
            surface for surface in geometry.surfaces if surface.surface_id == "holder"
        )
        diameters.append(holder.equivalent_diameter_m)
        packed_holder = next(
            surface
            for surface in deck.resolved_configuration["preset"]["lab_geometry"]["surfaces"]
            if surface["id"] == "holder"
        )
        packed.append(packed_holder["equivalent_diameter_m"])
    assert diameters == [0.02, 0.03]
    assert packed == pytest.approx([0.02, 0.03])
    assert _key(decks[0].cache_request) != _key(decks[1].cache_request)


def test_feedstock_override_is_loaded_once_for_the_key_and_the_solve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feedstock edit after the key is built cannot change the solve."""

    from simulator.core import PyrolysisSimulator

    feedstocks = _rewrite(
        tmp_path,
        b'  sum_check: 98.4\n  composition_status: "primary_xrf_loadable"\n',
        (
            b"  sum_check: 98.4\n"
            b"  bulk_additions: {Fe: 1.0}\n"
            b'  composition_status: "primary_xrf_loadable"\n'
        ),
        count=1,
        source=REPO / "data" / "feedstocks.yaml",
        name="feedstocks.yaml",
    )
    deck = build_deck_request(REPO / ROBINOT_PRESET, feedstocks_path=feedstocks)
    packed_fe = deck.resolved_configuration["inventory"]["raw_components_kg"]["Fe"]
    assert packed_fe > 0.0
    rewritten = feedstocks.read_bytes().replace(
        b"bulk_additions: {Fe: 1.0}",
        b"bulk_additions: {Fe: 2.0}",
    )
    assert rewritten != feedstocks.read_bytes()
    feedstocks.write_bytes(rewritten)
    captured: dict = {}

    def spy_start(self, config, *, backend=None):
        captured["entry"] = config.feedstocks[deck.run.feedstock_id]
        captured["mass_kg"] = config.mass_kg
        raise RuntimeError("stop before physics")

    monkeypatch.setattr("simulator.session.SimSession.start", spy_start)
    with pytest.raises(RuntimeError, match="stop before physics"):
        run_cached_deck(deck)
    consumed = PyrolysisSimulator.resolved_prediction_driving_feedstock_kg(
        captured["entry"],
        float(captured["mass_kg"]),
    )
    assert consumed["Fe"] == pytest.approx(packed_fe)
    fresh = build_deck_request(REPO / ROBINOT_PRESET, feedstocks_path=feedstocks)
    assert fresh.resolved_configuration["inventory"]["raw_components_kg"]["Fe"] != (
        pytest.approx(packed_fe)
    )


@pytest.mark.parametrize("status", ["partial", "ok"])
def test_loaded_hit_of_any_status_must_be_a_complete_ok(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """Rows inserted by ``load_cache_rows`` use the same completeness rule."""

    db = _configure(monkeypatch, tmp_path)
    key = _key(robinot_deck.cache_request)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_s2_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    document = {
        "status": status,
        "per_hour_summary": [{"hour": step} for step in range(1, 60)],
        "final": {},
    }
    assert engine_cache.load_cache_rows(
        db,
        [(key, document, "simulator", version, "s2", first_s2_row)],
    ) == 1

    def unexpected_solve(*_args, **_kwargs):
        pytest.fail("invalid-hit validation did not reach the stored row")

    with pytest.raises(IntegratedBenchContractError) as caught:
        run_cached_deck(robinot_deck, unexpected_solve)
    assert caught.value.reason == "cache_hit_not_ok"


def _identity(**overrides):
    """One coarse key. Each override changes a single identity part."""

    trees = {"simulator": "a" * 40, "engines": "b" * 40, "data": "c" * 40}
    identities = {"openimcc": "commit:66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84"}
    document = {
        "tree_ids": trees,
        "preset_sha256": "d" * 64,
        "leg": "faithful",
        "engines_local_toml_sha256": "e" * 64,
        "external_engine_identities": identities,
        "python_version": "3.12.0",
    }
    document.update(overrides)
    return deck_cache_request(**document)


def _write_openimcc_declaration(root: Path, commit: str) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "regolith-test"\n'
        'version = "0.0.0"\n'
        "dependencies = [\n"
        '  "openimcc @ git+https://github.com/simonrowland/openimcc@'
        f'{commit}"\n'
        "]\n",
        encoding="utf-8",
    )


def test_deck_key_uses_matching_active_openimcc_identity_over_dist_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", tmp_path)
    active = {"commit": "1" * 40}
    monkeypatch.setattr(
        engine_cache,
        "_compute_runtime_identity",
        lambda _engine: f"commit:{'9' * 40}",
    )
    monkeypatch.setattr(
        openimcc_bridge,
        "imported_openimcc_commit",
        lambda: active["commit"],
    )
    _write_openimcc_declaration(tmp_path, active["commit"])
    engine_cache.runtime_identity.cache_clear()
    assert isinstance(
        engine_cache.active_external_engine_identities("simulator"),
        engine_cache.RuntimeIdentityUnknown,
    )
    first = build_deck_request(REPO / ROBINOT_PRESET)

    active["commit"] = "2" * 40
    _write_openimcc_declaration(tmp_path, active["commit"])
    engine_cache.runtime_identity.cache_clear()
    second = build_deck_request(REPO / ROBINOT_PRESET)

    first_identity = first.cache_request["external_engines"]["engine_identities"]
    second_identity = second.cache_request["external_engines"]["engine_identities"]
    assert first_identity == {"openimcc": "commit:" + "1" * 40}
    assert second_identity == {"openimcc": "commit:" + "2" * 40}
    assert first.cache_bypass is second.cache_bypass is None
    assert _key(first.cache_request) != _key(second.cache_request)

    active["commit"] = "3" * 40
    engine_cache.runtime_identity.cache_clear()
    mismatched = build_deck_request(REPO / ROBINOT_PRESET)
    assert mismatched.cache_bypass == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY


def test_missing_active_openimcc_bypasses_matching_distribution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", tmp_path)
    pin = "1" * 40
    _write_openimcc_declaration(tmp_path, pin)
    monkeypatch.setattr(
        engine_cache,
        "_compute_runtime_identity",
        lambda _engine: f"commit:{pin}",
    )
    monkeypatch.setattr(openimcc_bridge, "_openimcc", None)
    engine_cache.runtime_identity.cache_clear()

    assert openimcc_bridge.imported_openimcc_commit() is None
    assert engine_cache.active_external_engine_identities("simulator") == {
        "openimcc": f"commit:{pin}"
    }
    deck = build_deck_request(REPO / ROBINOT_PRESET)
    assert deck.cache_bypass == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY
    monkeypatch.setattr(
        integrated_bench,
        "cached_engine_call",
        lambda *_args, **_kwargs: pytest.fail("missing import used the cache"),
    )
    hot = run_cached_deck(
        deck,
        lambda _request: _ok_document(deck.requested_steps),
    )
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY


def test_shadowed_openimcc_module_with_wrong_pin_bypasses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", tmp_path)
    declared = "1" * 40
    imported = "2" * 40
    _write_openimcc_declaration(tmp_path, declared)
    monkeypatch.setattr(
        engine_cache,
        "_compute_runtime_identity",
        lambda _engine: f"commit:{declared}",
    )
    monkeypatch.setattr(openimcc_bridge, "imported_openimcc_commit", lambda: imported)
    engine_cache.runtime_identity.cache_clear()

    assert engine_cache.active_external_engine_identities("simulator") == {
        "openimcc": f"commit:{declared}"
    }
    deck = build_deck_request(REPO / ROBINOT_PRESET)
    assert deck.cache_bypass == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY


def test_unavailable_active_engine_identity_bypasses_cache_with_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", tmp_path)
    _write_openimcc_declaration(tmp_path, "1" * 40)
    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", lambda _engine: "unavailable")
    engine_cache.runtime_identity.cache_clear()
    deck = build_deck_request(REPO / ROBINOT_PRESET)
    assert deck.cache_bypass == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY
    monkeypatch.setattr(
        integrated_bench,
        "cached_engine_call",
        lambda *_args, **_kwargs: pytest.fail("identity-unknown deck used cache"),
    )
    hot = run_cached_deck(
        deck,
        lambda _request: _ok_document(deck.requested_steps),
    )
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_ENGINE_IDENTITY


def test_cache_key_includes_trees_preset_engines_and_python() -> None:
    """Each of the four identity parts, and the selected leg, changes the key."""

    base = _identity()
    trees = _plain(base)["trees"]
    assert _key(base) != _key(_identity(tree_ids={**trees, "simulator": "1" * 40}))
    assert _key(base) != _key(_identity(tree_ids={**trees, "engines": "2" * 40}))
    assert _key(base) != _key(_identity(tree_ids={**trees, "data": "3" * 40}))
    assert _key(base) != _key(_identity(preset_sha256="4" * 64))
    assert _key(base) != _key(_identity(leg="remediation"))
    assert _key(base) != _key(_identity(engines_local_toml_sha256="absent"))
    assert _key(base) != _key(_identity(
        external_engine_identities={
            "openimcc": "commit:0000000000000000000000000000000000000000"
        },
    ))
    assert _key(base) != _key(_identity(python_version="3.11.9"))


def test_cache_request_cannot_be_mutated(robinot_deck: DeckRequest) -> None:
    request = robinot_deck.cache_request
    with pytest.raises(TypeError):
        request["leg"] = "remediation"
    with pytest.raises(TypeError):
        request["trees"]["data"] = "0" * 40
    with pytest.raises(TypeError):
        request["external_engines"]["engine_identities"]["openimcc"] = "commit:0"


def test_dirty_tree_runs_uncached_with_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        integrated_bench, "_working_tree_clean_against", lambda _commit: False
    )
    deck = build_deck_request(REPO / ROBINOT_PRESET)
    assert deck.cache_bypass is None
    seeded = _ok_document(deck.requested_steps)
    seeded["per_hour_summary"][0]["P_total_bar"] = 0.021
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        database,
        [(
            _key(deck.cache_request),
            seeded,
            "simulator",
            version,
            "s2",
            first_row,
        )],
    ) == 1
    before = _stored_rows(database)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        document = _ok_document(deck.requested_steps)
        document["per_hour_summary"][0]["P_total_bar"] = 0.033
        return document

    hot = run_cached_deck(deck, solve)
    assert calls["n"] == 1
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_DIRTY_TREE
    assert hot["per_hour_summary"][0]["P_total_bar"] == pytest.approx(0.033)
    assert _stored_rows(database) == before


def test_dirty_build_snapshot_stays_uncached_after_inputs_are_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _configure(monkeypatch, tmp_path)
    pin = "66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84"
    monkeypatch.setattr(
        engine_cache,
        "_compute_runtime_identity",
        lambda _engine: f"commit:{pin}",
    )
    monkeypatch.setattr(
        openimcc_bridge,
        "imported_openimcc_commit",
        lambda: pin,
    )
    engine_cache.runtime_identity.cache_clear()

    feedstocks_path = (REPO / "data" / "feedstocks.yaml").resolve()
    original_feedstocks = feedstocks_path.read_bytes()
    edited_feedstocks = original_feedstocks.replace(
        b'  sum_check: 98.4\n  composition_status: "primary_xrf_loadable"\n',
        b"  sum_check: 98.4\n  bulk_additions: {Fe: 1.0}\n"
        b'  composition_status: "primary_xrf_loadable"\n',
        1,
    )
    assert edited_feedstocks != original_feedstocks
    real_read_bytes = Path.read_bytes
    edited_snapshot = {"active": True}
    events: list[str] = []

    def read_feedstocks_during_build(path: Path) -> bytes:
        if path.resolve() == feedstocks_path:
            events.append("feedstocks-read")
            if edited_snapshot["active"]:
                return edited_feedstocks
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_feedstocks_during_build)
    clean_checks = {"count": 0}

    def dirty_first_build(_commit: str) -> bool:
        clean_checks["count"] += 1
        events.append("tree-check")
        return clean_checks["count"] > 1

    monkeypatch.setattr(
        integrated_bench,
        "_working_tree_clean_against",
        dirty_first_build,
    )
    dirty_deck = build_deck_request(REPO / ROBINOT_PRESET)
    edited_snapshot["active"] = False
    clean_deck = build_deck_request(REPO / ROBINOT_PRESET)

    assert events[:2] == ["tree-check", "feedstocks-read"]
    assert dirty_deck.cache_bypass == integrated_bench.DECK_UNCACHED_DIRTY_TREE
    assert clean_deck.cache_bypass is None
    assert _key(dirty_deck.cache_request) == _key(clean_deck.cache_request)
    dirty_components = dirty_deck.resolved_configuration["inventory"][
        "raw_components_kg"
    ]
    clean_components = clean_deck.resolved_configuration["inventory"][
        "raw_components_kg"
    ]
    assert dirty_components.get("Fe", 0.0) > clean_components.get("Fe", 0.0)

    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        database,
        [(
            _key(clean_deck.cache_request),
            _ok_document(clean_deck.requested_steps),
            "simulator",
            version,
            "s2",
            first_row,
        )],
    ) == 1
    before = _stored_rows(database)
    calls = {"count": 0}

    def solve(_request):
        calls["count"] += 1
        snapshot = dirty_deck.run._load_config_bundle()
        assert snapshot.feedstocks["lunar_eac_1a"]["bulk_additions"]["Fe"] == 1.0
        return _ok_document(dirty_deck.requested_steps)

    hot = run_cached_deck(dirty_deck, solve)
    assert calls["count"] == 1
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_DIRTY_TREE
    assert _stored_rows(database) == before


def test_tree_that_becomes_dirty_during_miss_is_not_inserted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _configure(monkeypatch, tmp_path)
    deck = build_deck_request(REPO / ROBINOT_PRESET)
    checks = {"n": 0}

    def clean_once(_commit: str) -> bool:
        checks["n"] += 1
        return checks["n"] == 1

    monkeypatch.setattr(integrated_bench, "_working_tree_clean_against", clean_once)
    hot = run_cached_deck(
        deck,
        lambda _request: _ok_document(deck.requested_steps),
    )
    assert checks["n"] == 2
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_DIRTY_TREE
    assert _stored_rows(database) == []


def _git(args: list[str], *, env: dict[str, str] | None = None, data: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO,
        input=data,
        capture_output=True,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode() or completed.stdout.decode()
        raise AssertionError(detail)
    return completed.stdout.decode().strip()


def test_a_vapour_table_byte_changes_the_data_tree_id(tmp_path: Path) -> None:
    """One tracked data byte changes the data tree id, and therefore the key.

    The swapped tree is written only under ``tmp_path``. The repository
    object store does not receive the new blob or the new tree.
    """

    source = REPO / "data" / "vapor_pressures.yaml"
    original = source.read_bytes()
    committed = _git(["rev-parse", "HEAD:data/vapor_pressures.yaml"])
    assert _git(["hash-object", "--stdin"], data=original) == committed
    mutated = _git(["hash-object", "--stdin"], data=original + b"\n# byte\n")
    assert mutated != committed
    objects = tmp_path / "objects"
    objects.mkdir()
    private = os.environ.copy()
    private["GIT_OBJECT_DIRECTORY"] = str(objects)
    git_objects = _git(["rev-parse", "--git-path", "objects"])
    private["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = (
        git_objects if os.path.isabs(git_objects) else str((REPO / git_objects).resolve())
    )
    written = _git(["hash-object", "-w", "--stdin"], env=private, data=original + b"\n# byte\n")
    assert written == mutated
    lines = []
    replaced = False
    for line in _git(["ls-tree", "HEAD:data"]).splitlines():
        meta, name = line.split("\t", 1)
        if name == "vapor_pressures.yaml":
            mode, kind, _old = meta.split()
            assert kind == "blob"
            line = f"{mode} blob {mutated}\t{name}"
            replaced = True
        lines.append(line)
    assert replaced
    new_tree = _git(["mktree"], env=private, data=("\n".join(lines) + "\n").encode())
    old_tree = _git(["rev-parse", "HEAD:data"])
    assert new_tree != old_tree
    for oid in (mutated, new_tree):
        missing = subprocess.run(
            ["git", "cat-file", "-t", oid],
            cwd=REPO,
            capture_output=True,
            check=False,
        )
        assert missing.returncode != 0
    commit = integrated_bench._git_text(["rev-parse", "HEAD"])
    base_ids = integrated_bench._tree_ids(commit)
    changed = dict(base_ids)
    changed["data"] = new_tree
    identities = {"openimcc": "commit:66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84"}
    left = deck_cache_request(
        tree_ids=base_ids,
        preset_sha256="ab" * 32,
        leg="faithful",
        engines_local_toml_sha256="absent",
        external_engine_identities=identities,
        python_version=platform.python_version(),
    )
    right = deck_cache_request(
        tree_ids=changed,
        preset_sha256="ab" * 32,
        leg="faithful",
        engines_local_toml_sha256="absent",
        external_engine_identities=identities,
        python_version=platform.python_version(),
    )
    assert _key(left) != _key(right)


def test_holder_profile_binding_changes_the_cache_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holder bound to the window curve is not the holder curve."""

    _configure(monkeypatch, tmp_path)
    source = REPO / ROBINOT_PRESET
    edited = _rewrite(
        tmp_path,
        b"temperature_profile: holder",
        b"temperature_profile: window",
        count=1,
        source=source,
        name="holder-window.yaml",
    )
    decks = [build_deck_request(path) for path in (source, edited)]
    holder = []
    for deck in decks:
        sim = deck.run._start_session().simulator
        actual = sim._resolve_lab_surface_temperatures(
            sim._active_surface_temperature_schedule(),
            sample_time_h=1.0,
        )
        holder.append(actual["holder"])
    assert holder == [1500.0, 80.0]
    solved = []

    def solve_at(temperature):
        def solve(_request):
            solved.append(temperature)
            return _ok_document(decks[0].requested_steps)
        return solve

    for deck, temperature in zip(decks, holder):
        run_cached_deck(deck, solve_at(temperature))
    assert solved == holder
    assert _key(decks[0].cache_request) != _key(decks[1].cache_request)


@pytest.mark.parametrize(
    "profile",
    ["observed_holder", "holder_reason", "note", "uncertainty", "target"],
)
def test_renamed_holder_curve_changes_the_cache_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    """A dynamic profile id is keyed, including names that look like notes.

    The holder surface still reads that curve. Two decks that differ only
    in its terminal temperature do not share a key, and each miss solves.
    """

    _configure(monkeypatch, tmp_path)
    source = (REPO / ROBINOT_PRESET).read_text(encoding="utf-8")
    assert source.count("    holder:\n") == 1
    assert source.count("temperature_profile: holder") == 1
    assert source.count("value: 1500, unit: C") == 1
    renamed = source.replace("    holder:\n", f"    {profile}:\n", 1)
    renamed = renamed.replace(
        "temperature_profile: holder",
        f"temperature_profile: {profile}",
        1,
    )
    decks = []
    temperatures = []
    for terminal in (1500, 1400):
        path = tmp_path / f"{profile}-{terminal}.yaml"
        path.write_text(
            renamed.replace(
                "value: 1500, unit: C",
                f"value: {terminal}, unit: C",
                1,
            ),
            encoding="utf-8",
        )
        deck = build_deck_request(path)
        sim = deck.run._start_session().simulator
        temperatures.append(sim._resolve_lab_surface_temperatures(
            sim._active_surface_temperature_schedule(),
            sample_time_h=1.0,
        )["holder"])
        decks.append(deck)
    assert temperatures == [1500.0, 1400.0]
    solved = []

    def solve_at(temperature):
        def solve(_request):
            solved.append(temperature)
            return _ok_document(decks[0].requested_steps)
        return solve

    for deck, temperature in zip(decks, temperatures):
        run_cached_deck(deck, solve_at(temperature))
    assert solved == temperatures
    assert _key(decks[0].cache_request) != _key(decks[1].cache_request)


def test_gas_reported_status_changes_the_cache_key(tmp_path: Path) -> None:
    """``reported_status: not_reported`` drops the carrier and changes the key."""

    from simulator import teststand

    source = REPO / ROBINOT_PRESET
    edited = _rewrite(
        tmp_path,
        b"      mole_fraction: 1.0\n",
        b"      mole_fraction: 1.0\n      reported_status: not_reported\n"
        b"      reason: status mutation\n",
        count=1,
        source=source,
        name="gas-status.yaml",
    )
    decks = [build_deck_request(path) for path in (source, edited)]
    carrier = [
        teststand.parse_teststand(
            deck.run.lab_schedule,
            deck.run.setpoints_patch["lab_geometry"],
        ).carrier_species
        for deck in decks
    ]
    assert carrier == ["Ar", None]
    assert decks[1].resolved_configuration["schedule"]["gas_boundary"]["background_gas"][
        "reported_status"
    ] == "not_reported"
    assert _key(decks[0].cache_request) != _key(decks[1].cache_request)


def test_dry_surrogate_anhydrous_composition_changes_the_cache_key(
    tmp_path: Path,
) -> None:
    """Same id and raw recipe, different anhydrous melt, different key."""

    import yaml

    from tests.test_feedstock_inventory import _sim

    raw = {"SiO2": 69.0, "Al2O3": 10.0, "H2O": 20.0, "Na2O": 1.0}
    source = REPO / ROBINOT_PRESET
    decks = []
    dry_alumina = []
    for alumina in (12.5, 25.0):
        recipe = {
            "composition_wt_pct": raw,
            "stage0_profile": "carbonaceous_degas_cleanup",
            "stage0_temp_range_C": [20.0, 1050.0],
            "anhydrous_silicate_after_degassing": {
                "composition_wt_pct": {"SiO2": 100.0 - alumina, "Al2O3": alumina},
            },
        }
        feed = {"lunar_eac_1a": recipe}
        override = tmp_path / f"feed-{alumina}.yaml"
        override.write_text(yaml.safe_dump(feed))
        deck = build_deck_request(source, feedstocks_path=override)
        decks.append(deck)
        sim = _sim(feed)
        sim.load_batch(
            "lunar_eac_1a",
            mass_kg=deck.resolved_configuration["session"]["mass_kg"],
        )
        dry_alumina.append(sim.inventory.melt_oxide_kg["Al2O3"])
    assert dry_alumina[0] < dry_alumina[1]
    packed = [
        deck.resolved_configuration["inventory"]["melt_oxide_kg"]["Al2O3"]
        for deck in decks
    ]
    assert packed[0] < packed[1]
    assert (
        decks[0].resolved_configuration["feedstock"]["id"]
        == decks[1].resolved_configuration["feedstock"]["id"]
    )
    assert (
        decks[0].resolved_configuration["inventory"]["raw_components_kg"]
        == decks[1].resolved_configuration["inventory"]["raw_components_kg"]
    )
    assert decks[0].cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert decks[1].cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert _key(decks[0].cache_request) == _key(decks[1].cache_request)
    solved = []

    def solve_at(alumina):
        def solve(_request):
            solved.append(alumina)
            return _ok_document(decks[0].requested_steps)
        return solve

    for deck, alumina in zip(decks, (12.5, 25.0)):
        hot = run_cached_deck(deck, solve_at(alumina))
        assert hot["notice"] == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert solved == [12.5, 25.0]


def test_liner_material_changes_the_cache_key(tmp_path: Path) -> None:
    mutant = build_deck_request(_rewrite(
        tmp_path,
        b"    - id: holder\n      role: sample_holder\n",
        b"    - id: holder\n      role: sample_holder\n      liner_material: steel\n",
        count=1,
    ))
    holder = next(
        surface
        for surface in mutant.resolved_configuration["preset"]["lab_geometry"]["surfaces"]
        if surface["id"] == "holder"
    )
    assert holder["liner_material"] == "steel"
    checked_in = build_deck_request(REPO / ROBINOT_PRESET)
    assert _key(mutant.cache_request) != _key(checked_in.cache_request)


def test_pump_speed_changes_the_cache_key(
    robinot_deck: DeckRequest,
    tmp_path: Path,
) -> None:
    mutant = build_deck_request(_rewrite(
        tmp_path,
        b"pumping_speed_m3_s: null\n",
        b"pumping_speed_m3_s: 0.003\n",
        count=1,
    ))
    assert mutant.resolved_configuration["apparatus"]["pump"]["nominal_speed_m3_s"] == (
        pytest.approx(0.003)
    )
    assert _key(mutant.cache_request) != _key(robinot_deck.cache_request)


def test_edited_vapor_override_does_not_share_a_hit(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An override file is outside the committed tree, so the deck is not cached.

    The lookup identity can match the checked-in deck. The bypass is what
    stops that identity from returning or storing a row.
    """

    database = _configure(monkeypatch, tmp_path)
    source = REPO / "data" / "vapor_pressures.yaml"
    edited = tmp_path / "vapor_pressures.yaml"
    edited.write_bytes(source.read_bytes() + b"\n# byte-identity probe\n")
    mutant = build_deck_request(
        REPO / ROBINOT_PRESET,
        vapor_pressures_path=edited,
    )
    assert mutant.cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert _key(mutant.cache_request) == _key(robinot_deck.cache_request)
    seeded = _ok_document(mutant.requested_steps)
    seeded["per_hour_summary"][0]["P_total_bar"] = 0.021
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        database,
        [(
            _key(mutant.cache_request),
            seeded,
            "simulator",
            version,
            "s2",
            first_row,
        )],
    ) == 1
    before = _stored_rows(database)
    calls = {"n": 0}

    def solve(_request):
        calls["n"] += 1
        document = _ok_document(mutant.requested_steps)
        document["per_hour_summary"][0]["P_total_bar"] = 0.033
        return document

    hot = run_cached_deck(mutant, solve)
    assert calls["n"] == 1
    assert hot["notice"] == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE
    assert hot["per_hour_summary"][0]["P_total_bar"] == pytest.approx(0.033)
    assert _stored_rows(database) == before


def test_deck_configuration_is_frozen_at_key_time(
    robinot_deck: DeckRequest,
) -> None:
    """The object the key read cannot be replaced or edited afterwards."""

    with pytest.raises(TypeError):
        robinot_deck.run.lab_schedule = {}
    with pytest.raises(TypeError):
        robinot_deck.run.mass_kg = 1.0
    with pytest.raises(TypeError):
        robinot_deck.run.lab_schedule["duration_h"] = 2.0
    with pytest.raises(TypeError):
        robinot_deck.run.lab_schedule["melt_temperature_C"][0]["value"] = 30
    with pytest.raises(TypeError):
        robinot_deck.run.lab_schedule["melt_temperature_C"].append({})
    with pytest.raises(TypeError):
        robinot_deck.run.feedstocks_path = Path("other-feedstocks.yaml")
    with pytest.raises(TypeError):
        robinot_deck.run.setpoints_path = Path("other-setpoints.yaml")
    with pytest.raises(TypeError):
        robinot_deck.run.vapor_pressures_path = Path("other-vapor.yaml")


def test_species_poison_is_partial_and_keeps_the_bad_rate(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonnumeric species rate is malformed evidence, not a silent drop."""

    payload = _ok_document(1)
    payload["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] = "bad"
    hot, cacheable = project_whole_run(payload, requested_steps=1)
    assert cacheable is False
    assert hot["status"] == "partial"
    assert hot["reason"] == "malformed_row"
    assert hot["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] == "bad"

    poisoned_final = _ok_document(1)
    poisoned_final["final"]["pump_outlet_by_species_kg"] = {"O2": "bad"}
    final_hot, final_cacheable = project_whole_run(
        poisoned_final,
        requested_steps=1,
    )
    assert final_cacheable is False
    assert final_hot["reason"] == "malformed_row"
    assert final_hot["final"]["pump_outlet_by_species_kg"]["O2"] == "bad"

    poisoned_state = _ok_document(1)
    poisoned_state["final_state"] = {"process.cleaned_melt": {"SiO2": "bad"}}
    state_hot, state_cacheable = project_whole_run(
        poisoned_state,
        requested_steps=1,
    )
    assert state_cacheable is False
    assert state_hot["reason"] == "malformed_row"
    assert state_hot["final_state"]["process.cleaned_melt"]["SiO2"] == "bad"

    path = _configure(monkeypatch, tmp_path)

    def solve(_request):
        document = _ok_document(robinot_deck.requested_steps)
        document["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] = "bad"
        return document

    stored = run_cached_deck(robinot_deck, solve)
    assert stored["status"] == "partial"
    assert stored["reason"] == "malformed_row"
    assert stored["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] == "bad"
    assert _stored_rows(path) == []


def test_loaded_species_poison_is_rejected_like_a_cold_row(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disk row from ``load_cache_rows`` uses the same completeness rule."""

    db_path = _configure(monkeypatch, tmp_path)
    deck = robinot_deck
    payload = _ok_document(deck.requested_steps)
    payload["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] = "bad"
    key = _key(deck.cache_request)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        db_path,
        [(key, payload, "simulator", version, "s2", first_row)],
    ) == 1
    with pytest.raises(IntegratedBenchContractError) as error:
        run_cached_deck(deck, lambda *_args: pytest.fail("cache miss"))
    assert error.value.reason == "cache_hit_not_ok"


def _apply_shape_defect(payload: dict, defect: str) -> bool:
    """Mutate one document. Return whether that shape is still a complete ok."""

    hour = payload["per_hour_summary"][0]
    token = integrated_bench.NOT_APPLICABLE_UNTIL_P0
    if defect == "null":
        hour["vapor_species_kg_hr"] = None
        return False
    if defect == "invalid_name":
        hour["vapor_species_kg_hr"] = {"bad species": "bad"}
        return False
    if defect == "nested":
        hour["vapor_species_kg_hr"] = {"O2": {"unexpected": 1.0}}
        return False
    if defect == "reason":
        payload["reason"] = "failed"
        return False
    if defect == "wall_token":
        payload["final"]["wall_deposit_by_species_kg"] = token
        return False
    if defect == "empty_map":
        hour["vapor_species_kg_hr"] = {}
        return True
    if defect == "pump_token":
        payload["final"]["pump_outlet_by_species_kg"] = token
        return True
    if defect == "surface_number":
        payload["final"]["deposit_by_surface_species_kg"] = {"holder": 1.0}
        return False
    if defect == "surface_species":
        payload["final"]["deposit_by_surface_species_kg"] = {"holder": {"Fe": 0.25}}
        return True
    if defect == "surface_empty":
        payload["final"]["deposit_by_surface_species_kg"] = {"holder": {}}
        return True
    raise AssertionError(defect)


@pytest.mark.parametrize(
    "defect",
    [
        "null",
        "invalid_name",
        "nested",
        "reason",
        "wall_token",
        "empty_map",
        "pump_token",
        "surface_number",
        "surface_species",
        "surface_empty",
    ],
)
def test_cold_projection_shape(defect: str) -> None:
    """The store projection applies one shape judgment, including null maps."""

    payload = _ok_document(1)
    expect = _apply_shape_defect(payload, defect)
    hot, cacheable = project_whole_run(payload, requested_steps=1)
    assert cacheable is expect
    if defect == "null":
        assert hot["per_hour_summary"][0]["vapor_species_kg_hr"] is None
    elif defect == "invalid_name":
        assert hot["per_hour_summary"][0]["vapor_species_kg_hr"]["bad species"] == (
            "bad"
        )
    elif defect == "nested":
        assert hot["per_hour_summary"][0]["vapor_species_kg_hr"]["O2"] == {
            "unexpected": 1.0,
        }
    elif defect == "surface_number":
        assert hot["final"]["deposit_by_surface_species_kg"]["holder"] == (
            pytest.approx(1.0)
        )
    elif defect == "surface_species":
        assert hot["final"]["deposit_by_surface_species_kg"]["holder"]["Fe"] == (
            pytest.approx(0.25)
        )
    elif defect == "surface_empty":
        assert hot["final"]["deposit_by_surface_species_kg"]["holder"] == {}
    if not expect:
        assert hot["status"] == "partial"
        assert hot["reason"]


@pytest.mark.parametrize(
    "defect",
    ["null", "invalid_name", "nested", "reason", "wall_token", "surface_number"],
)
def test_loaded_shape_defect_is_rejected_like_the_cold_row(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    payload = _ok_document(robinot_deck.requested_steps)
    assert _apply_shape_defect(payload, defect) is False
    assert project_whole_run(
        payload,
        requested_steps=robinot_deck.requested_steps,
    )[1] is False
    db_path = _configure(monkeypatch, tmp_path)
    key = _key(robinot_deck.cache_request)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        db_path,
        [(key, payload, "simulator", version, "s2", first_row)],
    ) == 1
    with pytest.raises(IntegratedBenchContractError) as error:
        run_cached_deck(robinot_deck, lambda *_args: pytest.fail("cache miss"))
    assert error.value.reason == "cache_hit_not_ok"


def test_loaded_measurement_slot_is_rejected_on_cache_hit(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _ok_document(robinot_deck.requested_steps)
    payload["measurement_selectors"] = {"id": "uncertainty"}
    assert project_whole_run(
        payload, requested_steps=robinot_deck.requested_steps
    )[1] is False
    db_path = _configure(monkeypatch, tmp_path)
    key = _key(robinot_deck.cache_request)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        db_path,
        [(key, payload, "simulator", version, "s2", first_row)],
    ) == 1
    with pytest.raises(IntegratedBenchContractError) as error:
        run_cached_deck(
            robinot_deck,
            lambda *_args: pytest.fail("measurement payload should be rejected"),
        )
    assert error.value.reason == "cache_hit_not_ok"


@pytest.mark.parametrize(
    "defect",
    ["empty_map", "pump_token", "surface_species", "surface_empty"],
)
def test_loaded_clean_shape_is_a_hit(
    robinot_deck: DeckRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    payload = _ok_document(robinot_deck.requested_steps)
    assert _apply_shape_defect(payload, defect) is True
    assert project_whole_run(
        payload,
        requested_steps=robinot_deck.requested_steps,
    )[1] is True
    db_path = _configure(monkeypatch, tmp_path)
    key = _key(robinot_deck.cache_request)
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["simulator"]
    first_row = engine_cache.ENGINE_CACHE_BOX_RANGES["s2"][0]
    assert engine_cache.load_cache_rows(
        db_path,
        [(key, payload, "simulator", version, "s2", first_row)],
    ) == 1

    def unexpected(*_args, **_kwargs):
        pytest.fail("a clean loaded row called the solver")

    hit = run_cached_deck(robinot_deck, unexpected)
    assert hit["status"] == "ok"
    assert "reason" not in hit
    assert "note" not in hit


@pytest.mark.parametrize("status", ["failed", "refused"])
def test_failed_or_refused_keeps_the_incoming_reason(status: str) -> None:
    payload = _ok_document(1)
    payload["status"] = status
    payload["reason"] = "solver_stopped"
    hot, cacheable = project_whole_run(payload, requested_steps=1)
    assert cacheable is False
    assert hot["status"] == status
    assert hot["reason"] == "solver_stopped"


@pytest.mark.parametrize("slot", integrated_bench.MEASUREMENT_SLOT_KEYS)
def test_prediction_projector_rejects_each_measurement_slot(slot: str) -> None:
    malformed = _ok_document(1)
    malformed[slot] = {"id": "uncertainty"}
    projected, cacheable = project_whole_run(malformed, requested_steps=1)
    assert cacheable is False
    assert projected["status"] == "partial"
    assert projected["reason"] == "measurement_in_prediction"


def test_dynamic_species_id_named_uncertainty_is_legal() -> None:
    payload = _ok_document(1)
    payload["per_hour_summary"][0]["vapor_species_kg_hr"]["uncertainty"] = 1.0
    payload["per_hour_summary"][0]["vapor_species_kg_hr"]["target"] = 2.0
    hot, cacheable = project_whole_run(payload, requested_steps=1)
    assert cacheable is True
    rates = hot["per_hour_summary"][0]["vapor_species_kg_hr"]
    assert rates["uncertainty"] == pytest.approx(1.0)
    assert rates["target"] == pytest.approx(2.0)


def test_setpoints_path_override_is_not_cached(tmp_path: Path) -> None:
    edited = tmp_path / "setpoints.yaml"
    edited.write_bytes((REPO / "data" / "setpoints.yaml").read_bytes())
    deck = build_deck_request(REPO / ROBINOT_PRESET, setpoints_path=edited)
    assert deck.cache_bypass == integrated_bench.DECK_UNCACHED_PATH_OVERRIDE


def test_config_bundle_reloads_when_any_path_changes(tmp_path: Path) -> None:
    """Unfrozen runs reload in place; frozen decks retain their first snapshot."""

    from simulator.runner import PyrolysisRun

    low = _rewrite(
        tmp_path,
        b'  sum_check: 98.4\n  composition_status: "primary_xrf_loadable"\n',
        (
            b"  sum_check: 98.4\n"
            b"  bulk_additions: {Fe: 1.0}\n"
            b'  composition_status: "primary_xrf_loadable"\n'
        ),
        count=1,
        source=REPO / "data" / "feedstocks.yaml",
        name="feed-low.yaml",
    )
    high = tmp_path / "feed-high.yaml"
    high.write_bytes(low.read_bytes().replace(b"{Fe: 1.0}", b"{Fe: 2.0}"))
    setpoints = tmp_path / "setpoints.yaml"
    setpoints.write_bytes((REPO / "data" / "setpoints.yaml").read_bytes())
    vapor = tmp_path / "vapor_pressures.yaml"
    vapor.write_bytes((REPO / "data" / "vapor_pressures.yaml").read_bytes())
    run = PyrolysisRun(
        feedstock_id="lunar_eac_1a",
        campaign="C0",
        hours=1,
        mass_kg=0.00338,
        feedstocks_path=low,
    )
    first = run._load_config_bundle()
    assert first.feedstocks["lunar_eac_1a"]["bulk_additions"]["Fe"] == pytest.approx(1.0)
    run.feedstocks_path = high
    second = run._load_config_bundle()
    assert second.feedstocks["lunar_eac_1a"]["bulk_additions"]["Fe"] == pytest.approx(2.0)
    high.write_bytes(high.read_bytes().replace(b"{Fe: 2.0}", b"{Fe: 3.0}"))
    reloaded = run._load_config_bundle()
    assert reloaded.feedstocks["lunar_eac_1a"]["bulk_additions"]["Fe"] == pytest.approx(3.0)
    run.setpoints_path = setpoints
    assert run._load_config_bundle().source_paths["setpoints"] == setpoints
    run.vapor_pressures_path = vapor
    assert run._load_config_bundle().source_paths["vapor_pressures"] == vapor

    frozen = PyrolysisRun(
        feedstock_id="lunar_eac_1a",
        campaign="C0",
        hours=1,
        mass_kg=0.00338,
        feedstocks_path=high,
    )
    frozen.freeze_configuration()
    frozen_snapshot = frozen._load_config_bundle()
    high.write_bytes(high.read_bytes().replace(b"{Fe: 3.0}", b"{Fe: 4.0}"))
    assert frozen._load_config_bundle().feedstocks["lunar_eac_1a"][
        "bulk_additions"
    ]["Fe"] == pytest.approx(3.0)
    assert frozen_snapshot.feedstocks["lunar_eac_1a"]["bulk_additions"][
        "Fe"
    ] == pytest.approx(3.0)
