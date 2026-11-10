"""Five-row fixture for the July alphaMELTS cache converter."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from simulator.melt_backend.base import EquilibriumResult

from scripts.convert_july_alphamelts_cache import (
    _normalize_stdin,
    capture_today_melts,
    convert_database,
    today_run_menus,
)

_JULY_STDIN = "1\ninput.melts\n3\n2\nx\n"
_COMPOSITION_KG = {
    "SiO2": 50.0,
    "Al2O3": 15.0,
    "FeO": 10.0,
    "MgO": 10.0,
    "CaO": 10.0,
    "Na2O": 5.0,
}
_SETTINGS = {
    "model": "MELTSv1.0.2",
    "calc_mode": "MELTS",
    "redox_buffer": None,
    "fo2_offset": None,
    "fe3fet_ratio": None,
    "vapor_transport_pO2_bar": 1.0e-9,
    "vapor_transport_floor_bar": 1.0e-9,
    "binary_sha256": "d91bd8ba" + "0" * 56,
}


def _july_payload(stdin: str, temperature_C: float) -> str:
    melts = (
        "Title: regolith_pyrolysis_simulator\n"
        "Initial Composition: SiO2 50.0000\n"
        f"Initial Temperature: {temperature_C}\n"
        "Initial Pressure: 1.0\n"
    )
    stdout = (
        "<> Found the liquidus at T = 1317.77 (C).\n"
        "Initial alphaMELTS calculation at: P 1.000000 (bars), "
        "T 1317.773438 (C)\n"
    )
    return json.dumps({
        "captures": [{
            "argv": ["alphamelts2", "1"],
            "input_file": melts,
            "returncode": 0,
            "stderr": "",
            "stdin": stdin,
            "stdout": stdout,
        }],
        "engine_invoked": True,
        "fO2_constraint": None,
        "format": "alphamelts-subprocess-capture-v2-kress-composition",
    })


def _result() -> EquilibriumResult:
    return EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        status="ok",
        liquid_fraction=1.0,
        liquidus_T_C=1200.0,
        fO2_log=-9.0,
        liquid_density_kg_m3=2500.0,
        liquid_viscosity_Pa_s=10.0,
        phases_present=["liquid"],
        phase_masses_kg={"liquid": 100.0},
        liquid_composition_wt_pct={"SiO2": 50.0, "Al2O3": 15.0},
        activity_coefficients={"SiO2": 0.4},
        vapor_pressures_Pa={"Na": 1.25},
    )


def _insert(connection, *, row_id, status, refusal, payload, temperature_C,
            composition_kg, hot: EquilibriumResult | None):
    connection.execute(
        """
        insert into grid_keys (
            id, temperature_C, pressure_bar, fO2_log, intended_fO2_log,
            composition_mol_json, composition_kg_json,
            composition_mol_by_account_json, model, redox_buffer,
            fO2_offset, fe3fet_ratio
        ) values (?, ?, 1.0, -9.0, -8.0, NULL, ?, NULL, 'MELTSv1.0.2',
            NULL, NULL, NULL)
        """,
        (
            row_id,
            temperature_C,
            None if composition_kg is None else json.dumps(composition_kg),
        ),
    )
    connection.execute(
        """
        insert into alphamelts_outputs (
            id, grid_key_id, status, refusal_reason, raw_payload,
            generic_liquid_fraction, generic_liquidus_T_C, generic_fO2_log,
            generic_liquid_density_kg_m3, generic_liquid_viscosity_Pa_s,
            generic_phases_present_json, generic_phase_masses_kg_json,
            generic_liquid_composition_wt_pct_json,
            generic_activity_coefficients_json,
            generic_vapor_pressures_Pa_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            row_id,
            status,
            refusal,
            payload,
            None if hot is None else hot.liquid_fraction,
            None if hot is None else hot.liquidus_T_C,
            None if hot is None else hot.fO2_log,
            None if hot is None else hot.liquid_density_kg_m3,
            None if hot is None else hot.liquid_viscosity_Pa_s,
            None if hot is None else json.dumps(hot.phases_present),
            None if hot is None else json.dumps(hot.phase_masses_kg),
            None if hot is None else json.dumps(hot.liquid_composition_wt_pct),
            None if hot is None else json.dumps(hot.activity_coefficients),
            None if hot is None else json.dumps(hot.vapor_pressures_Pa),
        ),
    )


def _build_fixture(path: Path) -> dict:
    matched = capture_today_melts({
        "temperature_C": 1400.0,
        "pressure_bar": 1.0,
        "composition_kg": dict(_COMPOSITION_KG),
        "fO2_log": -9.0,
        "redox_mode": None,
        "subprocess_run_mode": "isothermal",
    })
    assert matched is not None, "today's adapter did not launch the fixture row"
    payload = json.dumps({
        "captures": [{
            "argv": matched["argv"],
            "input_file": matched["melts"],
            "returncode": 0,
            "stderr": "",
            "stdin": matched["stdin"],
            "stdout": "",
        }],
        "fO2_constraint": {"path": "Absolute", "offset": -9.0},
    })
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table grid_keys (
            id integer primary key,
            temperature_C real not null,
            pressure_bar real not null,
            fO2_log real not null,
            intended_fO2_log real,
            composition_mol_json text,
            composition_kg_json text,
            composition_mol_by_account_json text,
            model text,
            redox_buffer text,
            fO2_offset real,
            fe3fet_ratio real
        );
        create table alphamelts_outputs (
            id integer primary key,
            grid_key_id integer not null,
            status text not null,
            refusal_reason text,
            raw_payload text not null,
            generic_liquid_fraction real,
            generic_liquidus_T_C real,
            generic_fO2_log real,
            generic_liquid_density_kg_m3 real,
            generic_liquid_viscosity_Pa_s real,
            generic_phases_present_json text,
            generic_phase_masses_kg_json text,
            generic_liquid_composition_wt_pct_json text,
            generic_activity_coefficients_json text,
            generic_vapor_pressures_Pa_json text
        );
        """
    )
    july_rows = (
        (1, "ok", None, 1750.0),
        (2, "out_of_domain", "subprocess_died", 1600.0),
        (3, "timeout", "timeout", 1200.0),
        (4, "error", "LiquidFractionInvalidError", 900.0),
    )
    for row_id, status, refusal, temperature_C in july_rows:
        _insert(
            connection,
            row_id=row_id,
            status=status,
            refusal=refusal,
            payload=_july_payload(_JULY_STDIN, temperature_C),
            temperature_C=temperature_C,
            composition_kg={"SiO2": 50.0, "FeO": 10.0, "MgO": 10.0},
            hot=None,
        )
    _insert(
        connection,
        row_id=5,
        status="ok",
        refusal=None,
        payload=payload,
        temperature_C=1400.0,
        composition_kg=dict(_COMPOSITION_KG),
        hot=_result(),
    )
    connection.commit()
    connection.close()
    return matched


def test_five_row_fixture_emits_only_the_replay_equal_row(tmp_path):
    database = tmp_path / "five.db"
    matched = _build_fixture(database)
    menus = today_run_menus()
    assert _normalize_stdin(_JULY_STDIN) not in set(menus.values())
    assert _normalize_stdin(matched["stdin"]) == menus["isothermal"]
    calls = []

    def replay(kwargs):
        calls.append(kwargs)
        return _result()

    summary = convert_database(
        database,
        tmp_path / "out.jsonl",
        menus=menus,
        replay=replay,
        chem_version="fixture-chem",
        settings=_SETTINGS,
        counts_path=tmp_path / "counts.json",
    )

    assert summary["rows"] == 5
    assert summary["emitted"] == 1
    assert summary["by_reason"]["july_run_menu_not_today"] == 4
    assert summary["by_reason"]["converted"] == 1
    assert len(calls) == 1
    lines = (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["engine"] == "alphamelts"
    assert record["chem_version"] == "fixture-chem"
    assert record["provenance"] == {
        "july_row_id": 5,
        "replay_verified": True,
    }
    assert record["request"]["temperature_C"] == 1400.0
    assert record["request"]["fO2_log"] == -9.0
    assert record["request"]["subprocess_run_mode"] == "isothermal"
    assert record["settings"]["calc_mode"] == "MELTS"
    assert record["result"]["liquid_fraction"] == 1.0
    assert "key" not in record
    assert summary["executed_minus_requested_T_C"]["n"] == 4
    assert summary["executed_minus_requested_T_C"]["within_0_05_C"] == 0


def test_replay_drift_drops_the_matching_row(tmp_path):
    database = tmp_path / "five.db"
    _build_fixture(database)
    drifted = _result()
    drifted.liquid_fraction = 0.5

    summary = convert_database(
        database,
        tmp_path / "out.jsonl",
        replay=lambda _kwargs: drifted,
        chem_version="fixture-chem",
        settings=_SETTINGS,
    )

    assert summary["emitted"] == 0
    assert summary["by_reason"]["dropped_for_drift"] == 1
    assert summary["by_reason"]["july_run_menu_not_today"] == 4
    assert (tmp_path / "out.jsonl").read_text(encoding="utf-8") == ""
