from __future__ import annotations

import json
import math
import os
import pathlib
import sqlite3
import struct

import pytest

from scripts import cache_convert


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEGACY_DB = ROOT / "docs-private" / "recipe-db" / "reduced-real.db"
REVIEWED_DESIGN = (
    ROOT / "docs-private" / "research" / "2026-07-10-t171-schema" / "design.md"
)


def test_encoder_default_materialization_matches_explicit_defaults():
    omitted = cache_convert.materialize_alphamelts_engine_config(
        {"model": "MELTSv1.0.2"}, resolved_mode="subprocess"
    )
    explicit = cache_convert.materialize_alphamelts_engine_config(
        {
            "model": "MELTSv1.0.2",
            "mode": "subprocess",
            "redox_buffer": None,
            "fO2_offset": None,
            "Fe3Fet_Liq": None,
            "require_petthermotools": False,
        }
    )

    assert omitted == explicit
    assert cache_convert.encode("rr-engine-config-v1", omitted) == cache_convert.encode(
        "rr-engine-config-v1", explicit
    )


def test_encoder_dual_rail_float_coherence():
    value = 0.1
    identity = cache_convert.encode("rr-test-v1", {"value": value})
    display = cache_convert.display_json({"value": value})
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE rail(value REAL) STRICT")
    connection.execute("INSERT INTO rail(value) VALUES (?)", (value,))
    scalar = connection.execute("SELECT value FROM rail").fetchone()[0]

    assert json.loads(identity)["value"]["value"] == {"$f64": value.hex()}
    assert display == '{"value":0.1}'
    assert struct.pack(">d", scalar) == struct.pack(">d", value)


def test_encoder_normalizes_negative_zero_to_positive_zero():
    negative = cache_convert.encode("rr-test-v1", {"value": -0.0})
    positive = cache_convert.encode("rr-test-v1", {"value": 0.0})

    assert negative == positive
    assert cache_convert.display_json({"value": -0.0}) == '{"value":0.0}'
    assert struct.pack(">d", cache_convert._f64(-0.0, "test")) == struct.pack(
        ">d", 0.0
    )
    assert math.copysign(1.0, cache_convert._f64(-0.0, "test")) == 1.0


def test_account_vector_uses_exact_builtin_tuple_and_sorted_extensions():
    vector = cache_convert.ordered_account_species_vector(
        {
            "process.cleaned_melt": {"SiO2": 1.0},
            "z.extension": {"XeO": 2.0},
            "a.extension": {},
        }
    )
    accounts = tuple(row[0] for row in vector)

    assert accounts[:4] == cache_convert.BACKEND_REACTIVE_ACCOUNTS
    assert accounts[4:] == ("a.extension", "z.extension")
    assert tuple(cache_convert.BACKEND_REACTIVE_ACCOUNTS) == (
        "process.cleaned_melt",
        "process.spent_reductant_residue",
        "process.metal_phase",
        "process.overhead_gas",
    )
    assert all(row[1] for row in vector[:4])


@pytest.mark.skipif(not REVIEWED_DESIGN.exists(), reason="private reviewed design absent")
def test_embedded_ddl_matches_reviewed_design_byte_for_byte():
    design = REVIEWED_DESIGN.read_text(encoding="utf-8")
    reviewed_sql = design.split("```sql", 1)[1].split("```", 1)[0].strip()

    assert cache_convert.DDL.strip() == reviewed_sql


def test_path_gate_rejects_database_and_report_aliases(tmp_path):
    source = tmp_path / "source.db"
    source.write_bytes(b"immutable-source")
    destination = tmp_path / "destination.db"
    report = tmp_path / "report.json"

    with pytest.raises(cache_convert.ConversionError, match="must all differ"):
        cache_convert.convert_database(source, destination, source)
    with pytest.raises(cache_convert.ConversionError, match="must all differ"):
        cache_convert.convert_database(source, destination, destination)

    os.link(source, destination)
    with pytest.raises(cache_convert.ConversionError, match="same file as the source"):
        cache_convert.convert_database(source, destination, report)
    assert source.read_bytes() == b"immutable-source"


def test_path_gate_rejects_source_and_destination_sidecars(tmp_path):
    source = tmp_path / "source.db"
    source.write_bytes(b"immutable-source")
    source_wal = pathlib.Path(str(source) + "-wal")
    source_wal.write_bytes(b"live-wal")
    destination = tmp_path / "destination.db"

    with pytest.raises(cache_convert.ConversionError, match="source SQLite sidecar"):
        cache_convert.convert_database(source, destination, source_wal)
    with pytest.raises(cache_convert.ConversionError, match="source SQLite sidecar"):
        cache_convert.convert_database(
            source, pathlib.Path(str(source) + "-shm"), tmp_path / "report.json"
        )

    assert source.read_bytes() == b"immutable-source"
    assert source_wal.read_bytes() == b"live-wal"


def test_source_snapshot_identity_includes_committed_wal_state(tmp_path):
    source = tmp_path / "source.db"
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE values_table(value TEXT)")
        writer.execute("INSERT INTO values_table VALUES ('before')")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = cache_convert._source_snapshot(source)
        main_before = cache_convert.file_sha256(source)

        writer.execute("INSERT INTO values_table VALUES ('wal-only')")
        writer.commit()

        assert cache_convert.file_sha256(source) == main_before
        after = cache_convert._source_snapshot(source)
        assert after["sha256"] != before["sha256"]
    finally:
        writer.close()


def test_destination_checkpoint_reports_blocking_reader(tmp_path):
    destination = tmp_path / "destination.db"
    writer = sqlite3.connect(destination)
    reader = sqlite3.connect(destination)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE values_table(value TEXT)")
        writer.execute("INSERT INTO values_table VALUES ('before')")
        writer.commit()
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM values_table").fetchall()
        writer.execute("INSERT INTO values_table VALUES ('after')")
        writer.commit()

        checkpoint = cache_convert._checkpoint_destination(writer, destination)

        assert checkpoint["verified"] is False
        assert checkpoint["busy"] == 1
        assert checkpoint["wal_size"] > 0
    finally:
        reader.close()
        writer.close()


def test_conversion_cannot_report_complete_when_checkpoint_is_unverified(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE reduced_real_metadata(key TEXT PRIMARY KEY, value TEXT)"
        )
        connection.execute(
            "INSERT INTO reduced_real_metadata VALUES "
            "('store_schema_version', 'test-v1')"
        )
        connection.execute(
            "CREATE TABLE reduced_real_equilibrium_payloads(value TEXT)"
        )
    checkpoint = {
        "busy": 1,
        "log_frames": 2,
        "checkpointed_frames": 1,
        "wal_size": 4096,
        "verified": False,
    }
    monkeypatch.setattr(
        cache_convert,
        "_checkpoint_destination",
        lambda connection, path: dict(checkpoint),
    )

    report = cache_convert.convert_database(
        source,
        tmp_path / "destination.db",
        tmp_path / "report.json",
        enforce_expected_counts=False,
    )

    assert report["status"] == "failed"
    assert report["destination_checkpoint"] == checkpoint
    assert any(
        failure["error_type"] == "DestinationCheckpointError"
        for failure in report["failures"]
    )


@pytest.mark.skipif(not LEGACY_DB.exists(), reason="private reduced-real corpus absent")
def test_source_connection_is_uri_readonly_and_query_only():
    connection = cache_convert.open_source_readonly(LEGACY_DB)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden_write(value TEXT)")
    finally:
        connection.close()


@pytest.mark.skipif(not LEGACY_DB.exists(), reason="private reduced-real corpus absent")
def test_alpha_preflight_is_independent_of_follower_validation():
    connection = cache_convert.open_source_readonly(LEGACY_DB)
    try:
        source = dict(
            connection.execute(
                "SELECT rowid AS legacy_rowid, * FROM reduced_real_equilibrium_payloads WHERE rowid=1"
            ).fetchone()
        )
    finally:
        connection.close()
    payload = json.loads(source["payload_bytes"])
    payload["last_vapor_pressure_diagnostic"]["future_unknown"] = "preserve-me"
    payload_bytes = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    source["payload_bytes"] = payload_bytes
    source["payload_sha256"] = cache_convert.sha256_bytes(payload_bytes)

    alpha = cache_convert.materialize_legacy_row(
        source, "0" * 64, include_followers=False
    )
    assert alpha.vaporock is None
    assert alpha.sulfsat is None
    with pytest.raises(cache_convert.UnknownFieldError, match="future_unknown"):
        cache_convert.materialize_legacy_row(source, "0" * 64)

    incomplete = dict(source)
    incomplete_payload = json.loads(source["payload_bytes"])
    incomplete_payload["last_vapor_pressure_diagnostic"].pop("future_unknown")
    incomplete_payload["last_vapor_pressure_diagnostic"].pop(
        "vapor_pressures_Pa"
    )
    incomplete_bytes = json.dumps(
        incomplete_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    incomplete["payload_bytes"] = incomplete_bytes
    incomplete["payload_sha256"] = cache_convert.sha256_bytes(incomplete_bytes)
    assert cache_convert.materialize_legacy_row(
        incomplete, "0" * 64, include_followers=False
    ).vaporock is None
    with pytest.raises(cache_convert.ConversionError, match="missing fields"):
        cache_convert.materialize_legacy_row(incomplete, "0" * 64)


@pytest.mark.skipif(not LEGACY_DB.exists(), reason="private reduced-real corpus absent")
def test_future_vaporock_species_is_retained_generically_with_byte_parity(tmp_path):
    connection = cache_convert.open_source_readonly(LEGACY_DB)
    try:
        source = dict(
            connection.execute(
                "SELECT rowid AS legacy_rowid, * FROM reduced_real_equilibrium_payloads WHERE rowid=1"
            ).fetchone()
        )
    finally:
        connection.close()
    payload = json.loads(source["payload_bytes"])
    payload["last_vapor_pressure_diagnostic"]["vaporock_full_speciation_Pa"][
        "XeFuture"
    ] = 1.25
    payload_bytes = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    source["payload_bytes"] = payload_bytes
    source["payload_sha256"] = cache_convert.sha256_bytes(payload_bytes)
    materialized = cache_convert.materialize_legacy_row(source, "0" * 64)

    assert "XeFuture" in json.loads(
        materialized.vaporock["vaporock_full_speciation_Pa_json"]
    )
    assert [
        "/last_vapor_pressure_diagnostic/vaporock_full_speciation_Pa/XeFuture",
        "present",
    ] in materialized.compatibility_shape["paths"]
    destination = cache_convert._open_destination(
        tmp_path / "cache-v2.db", source_sha256="0" * 64, created_at="2026-07-10T00:00:00Z"
    )
    try:
        destination.execute("BEGIN IMMEDIATE")
        cache_convert._insert_materialized(destination, materialized)
        assert cache_convert.verify_inserted_row(destination, materialized) == {
            "key": True,
            "payload": True,
        }
        destination.commit()
    finally:
        destination.close()


def test_follower_rejection_tally_names_the_failing_engine():
    report = cache_convert._new_report({"sha256": "0" * 64}, 1)
    cache_convert._mark_failed_tables(
        report,
        None,
        cache_convert.UnknownFieldError(
            "VapoRock confirmation/zero-reason alternatives invalid"
        ),
        phase="followers",
    )

    assert report["tables"]["rr_vaporock_outputs"]["rejected"] == 1
    assert report["tables"]["rr_alphamelts_outputs"]["rejected"] == 0
    assert report["result_class_counts"]["vaporock"]["success"] == 0


@pytest.mark.skipif(not LEGACY_DB.exists(), reason="private reduced-real corpus absent")
def test_writer_gate_rejects_independent_projection_mutations():
    connection = cache_convert.open_source_readonly(LEGACY_DB)
    try:
        source = connection.execute(
            "SELECT rowid AS legacy_rowid, * FROM reduced_real_equilibrium_payloads WHERE rowid=1"
        ).fetchone()
    finally:
        connection.close()

    typed = cache_convert.materialize_legacy_row(source, "0" * 64)
    typed.alpha["temperature_C"] += 1.0
    typed.projection_snapshot["alphamelts"]["temperature_C"] = typed.alpha[
        "temperature_C"
    ]
    with pytest.raises(cache_convert.CanonicalizationError, match="typed scalar disagrees"):
        cache_convert._writer_gate(typed)

    structured = cache_convert.materialize_legacy_row(source, "0" * 64)
    structured.alpha["native_input_json"] = "{}"
    structured.projection_snapshot["alphamelts"]["native_input_json"] = "{}"
    with pytest.raises(cache_convert.CanonicalizationError, match="display projection"):
        cache_convert._writer_gate(structured)

    hashed = cache_convert.materialize_legacy_row(source, "0" * 64)
    hashed.alpha["engine_config_sha256"] = "0" * 64
    hashed.projection_snapshot["alphamelts"]["engine_config_sha256"] = "0" * 64
    with pytest.raises(cache_convert.CanonicalizationError, match="config hash mismatch"):
        cache_convert._writer_gate(hashed)

    canonical_null = cache_convert.materialize_legacy_row(source, "0" * 64)
    canonical_null.hub["requested_temperature_C"] = 1000.0
    canonical_null.projection_snapshot["hub"]["requested_temperature_C"] = 1000.0
    with pytest.raises(cache_convert.CanonicalizationError, match="typed scalar disagrees"):
        cache_convert._writer_gate(canonical_null)


@pytest.mark.skipif(not LEGACY_DB.exists(), reason="private reduced-real corpus absent")
def test_twenty_row_golden_byte_parity_and_idempotence(tmp_path):
    destination = tmp_path / "cache-v2.db"
    report_path = tmp_path / "cache-v2.report.json"
    stratified_rowids = (
        1,
        3,
        25,
        100,
        981,
        982,
        987,
        988,
        1200,
        1500,
        2000,
        2331,
        2332,
        2342,
        2343,
        2444,
        2445,
        2500,
        2520,
        2531,
    )

    first = cache_convert.convert_database(
        LEGACY_DB,
        destination,
        report_path,
        row_ids=stratified_rowids,
        enforce_expected_counts=False,
        require_sibling=False,
        batch_size=7,
    )

    assert first["status"] == "complete", first["failures"][:1]
    assert first["alpha_preflight"] == {
        "checked": 20,
        "failed": 0,
        "fully_green": True,
        "runtime_s": first["alpha_preflight"]["runtime_s"],
    }
    assert first["tables"]["rr_input_states"]["converted"] == 20
    assert first["tables"]["rr_alphamelts_outputs"]["converted"] == 20
    assert first["tables"]["rr_vaporock_outputs"]["converted"] > 0
    assert first["tables"]["rr_sulfsat_outputs"]["converted"] > 0
    assert first["tables"]["rr_input_states"]["parity_failed"] == 0
    assert len(first["parity_rows"]) == 20
    assert all(
        row["key_byte_equal"] and row["payload_byte_equal"]
        for row in first["parity_rows"]
    )

    second = cache_convert.convert_database(
        LEGACY_DB,
        destination,
        report_path,
        row_ids=stratified_rowids,
        enforce_expected_counts=False,
        require_sibling=False,
        batch_size=7,
    )

    assert second["status"] == "complete", second["failures"][:1]
    assert second["tables"]["rr_input_states"]["converted"] == 0
    assert second["tables"]["rr_alphamelts_outputs"]["converted"] == 0
    assert second["tables"]["rr_input_states"]["skipped"] == 20
    assert second["tables"]["rr_alphamelts_outputs"]["skipped"] == 20
    assert second["tables"]["rr_vaporock_outputs"]["skipped"] == first["tables"]["rr_vaporock_outputs"]["converted"]
    assert second["tables"]["rr_sulfsat_outputs"]["skipped"] == first["tables"]["rr_sulfsat_outputs"]["converted"]
    assert len(second["parity_rows"]) == 20
