from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import consumer_inputs
from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import AmountBasis, Engine, NoticeKind, Phase, Quantity
from simulator.battery.generators.bench import melt_activity_requests
from simulator.battery.migrate import Migrator
from simulator.battery.score import (
    _classify_flagged_notice_facts,
    predict_with_engine,
)
from simulator.battery.waypoints import GapReason
from simulator.corpus_paths import regolith_corpus_root
from tests.literature_extract_test_utils import activity_test_bench, isolated_root
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "omori-sanbongi-jjim-25-sio2-al2o3-activity"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits" / f"{SOURCE_ID}.cells.csv"
PDF_SHA256 = "f134c3080cee153282269828867f19982a5de6903bbd76ef7c769c9084b90a26"
UNCLOSED_COMPOSITION_ID = "omori_fig3_al20_mg30_03"
UNCLOSED_COMPOSITION_REASON = (
    "The plotted N_SiO2 and nominal oxide series imply negative CaO by mass closure."
)

FIG3_READS = {
    "al10_mg00_01": ("0.4024", "-1.0677"),
    "al10_mg00_02": ("0.4538", "-0.7508"),
    "al10_mg00_03": ("0.5096", "-0.4352"),
    "al10_mg00_05": ("0.5624", "-0.2256"),
    "al10_mg10_01": ("0.3885", "-1.0656"),
    "al10_mg10_03": ("0.4879", "-0.4388"),
    "al10_mg10_04": ("0.5317", "-0.3333"),
    "al10_mg20_01": ("0.3734", "-1.0409"),
    "al10_mg20_02": ("0.4179", "-0.8235"),
    "al10_mg20_03": ("0.4671", "-0.4848"),
    "al10_mg20_04": ("0.4919", "-0.3528"),
    "al10_mg30_01": ("0.3587", "-1.0387"),
    "al10_mg30_02": ("0.4020", "-0.8267"),
    "al10_mg30_03": ("0.4500", "-0.4570"),
    "al20_mg00_01": ("0.4232", "-1.0237"),
    "al20_mg00_02": ("0.4769", "-0.7466"),
    "al20_mg00_04": ("0.5341", "-0.4497"),
    "al20_mg00_05": ("0.5731", "-0.3713"),
    "al20_mg10_01": ("0.4347", "-0.8545"),
    "al20_mg10_02": ("0.4581", "-0.7089"),
    "al20_mg10_03": ("0.5110", "-0.6157"),
    "al20_mg10_04": ("0.5641", "-0.3231"),
    "al20_mg10_05": ("0.4060", "-0.9123"),
    "al20_mg20_01": ("0.3889", "-0.8460"),
    "al20_mg20_02": ("0.4387", "-0.6681"),
    "al20_mg20_04": ("0.5266", "-0.3990"),
    "al20_mg30_01": ("0.3748", "-0.8267"),
    "al20_mg30_02": ("0.4230", "-0.6419"),
    "al20_mg30_03": ("0.4712", "-0.5200"),
}

# Fig. 5 marker centers, not the adjacent printed N_SiO2 labels. Coordinates
# are crop pixels; values are the raw log10 activity read from the calibrated
# axes. The N_SiO2 label and MgO symbol are independently pinned per sample.
FIG5_READS = {
    "omori_fig5_01": ("-3.8047", 445.0, 632.0, "38.7", 10),
    "omori_fig5_02": ("-3.6124", 445.0, 599.5, "33.6", 20),
    "omori_fig5_03": ("-3.4704", 445.0, 575.5, "43.6", 10),
    "omori_fig5_04": ("-3.2367", 438.5, 536.0, "32.3", 20),
    "omori_fig5_05": ("-3.3225", 438.5, 550.5, "41.9", 20),
    "omori_fig5_06": ("-2.8810", 445.0, 476.0, "48.6", 10),
    "omori_fig5_07": ("-2.7798", 438.5, 459.0, "46.7", 20),
    "omori_fig5_08": ("-2.6071", 445.0, 430.0, "51.5", 20),
    "omori_fig5_09": ("-2.5268", 445.0, 416.5, "53.6", 20),
    "omori_fig5_10": ("-2.6429", 650.5, 436.0, "43.9", 20),
    "omori_fig5_11": ("-2.7262", 650.5, 450.0, "33.8", 20),
    "omori_fig5_12": ("-2.6369", 665.5, 435.0, "45.7", 10),
    "omori_fig5_13": ("-2.8333", 664.0, 468.0, "35.3", 10),
    "omori_fig5_14": ("-2.9048", 663.0, 480.0, "40.5", 10),
    "omori_fig5_15": ("-2.1101", 651.0, 346.5, "54.0", 20),
    "omori_fig5_16": ("-1.9675", 668.0, 322.5, "56.3", 10),
    "omori_fig5_17": ("-2.1845", 665.5, 359.0, "50.9", 10),
    "omori_fig5_18": ("-2.3095", 651.0, 380.0, "48.9", 20),
}
FIG5_WIDENED_UNCERTAINTY = {
    "omori_fig5_04",
    "omori_fig5_10",
    "omori_fig5_11",
    "omori_fig5_13",
    "omori_fig5_14",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block["observations"]
    }


def _figure_audit_rows(document: dict) -> list[dict[str, str]]:
    with CELL_AUDIT.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 65
    assert document["extraction"]["audit_file"] == (
        f"data/literature/extracts/audits/{SOURCE_ID}.cells.csv"
    )
    by_point = {(row["printed_table"], row["printed_row"]): row for row in rows}
    assert len(by_point) == len(rows)
    for block in document["species"].values():
        for observation in block["observations"]:
            values = observation["values"]
            assert "secondary_figure_read" not in values
            assert "fig6_secondary_locator" not in values
            assert "N_CaO_plus_MgO_over_N_SiO2_as_plotted" not in values
            assert "secondary_locator" not in observation
            locator = observation["locator"]
            read = values["figure_read"]
            audit = by_point[(str(locator["figure"]), locator["row"])]
            uncertainty = read["read_uncertainty"]
            pixel = read["crop_pixel_xy"]
            assert audit["printed_column"] == locator["column"]
            assert audit["printed_value"] == ""
            assert audit["pdftotext_cell"] == "not tabular; digitized figure point"
            assert audit["render_read"] == (
                f"x={read['x_coordinate_as_read']}; "
                f"y={read['y_coordinate_as_read']}; "
                f"pixel=({pixel[0]},{pixel[1]}); "
                f"uncertainty=({uncertainty['x']} {uncertainty['unit_x']}, "
                f"{uncertainty['y']} {uncertainty['unit_y']})"
            )
            if locator["figure"] == "3":
                assert audit["audit"] == "350 dpi crop reread against calibrated ticks"
            else:
                assert "350 dpi crop and overlay checked" in audit["audit"]
    return rows


def _isolated_migration(tmp_path: Path, document: dict):
    root = isolated_root(tmp_path, document)
    path = root / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
    migrator = Migrator(root=root, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _assert_activity_consumer(inputs, formula: str, quantity: Quantity) -> None:
    assert inputs.melt_activity
    assert inputs.activity_quantity == quantity.value
    assert inputs.measured_species == formula


def _assert_omori_derivation(sample: dict, *, mole_role: str) -> None:
    assert sample["reviewed_by"] == "w1127derivatt2"
    assert sample["reviewed_on"] == "2026-11-27"
    verification = sample["verification"]
    assert verification["method"] == "derivation"
    assert verification["closure"] == "by_difference"
    parents = verification["parents"]
    roles = [parent["role"] for parent in parents]
    assert roles.count(mole_role) == 1
    assert "mass_percent" in roles
    assert any(
        parent["role"] == "copied" and str(parent["path"]).endswith(".values.T_C")
        for parent in parents
    )
    if mole_role == "mole_percent":
        assert any(
            parent["role"] == "copied"
            and str(parent["path"]).endswith("N_Al2O3_mole_fraction_as_read")
            for parent in parents
        )
    else:
        assert "mole_percent" not in roles


def test_rendered_figure_reads_standard_states_and_validator_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(regolith_corpus_root()))
    document = _document()
    _figure_audit_rows(document)
    sio2_rows = document["species"]["SiO2"]["observations"]
    alumina_rows = document["species"]["Al2O3"]["observations"]
    rows = _rows(document)

    assert document["extraction"]["worker"] == "w1126homgsi"
    assert all(
        row["values"]["evidence_class"] == "figure_only" for row in rows.values()
    )
    assert document["source"]["sha256"] == PDF_SHA256
    assert document["source"]["corpus_sha256"] == PDF_SHA256
    assert len(sio2_rows) == 29
    assert len(alumina_rows) == 18
    assert len(rows) == 47
    assert len(document["fidelity_samples"]) == 94

    for key, (x, activity) in FIG3_READS.items():
        row = rows[f"omori_fig3_{key}"]
        values = row["values"]
        read = values["figure_read"]
        assert values["activity"] == activity
        assert values["quantity"] == "activity"
        assert values["printed_form"] == "log10"
        assert values["sample_id"] == f"omori_sio2_{key}"
        assert "paired_activity_coefficient_id" not in values
        assert row["locator"]["figure"] == "3"
        assert values["composition_as_plotted"]["N_SiO2_mole_fraction"] == x
        assert read["x_coordinate_as_read"] == x
        assert read["y_coordinate_as_read"] == activity
        assert read["read_uncertainty"] == {
            "x": "0.006", "y": "0.06",
            "unit_x": "mole fraction", "unit_y": "log10 activity",
        }

    for sample in document["fidelity_samples"]:
        figure = sample["locator"]["figure"]
        assert figure in {"3", "5"}
        sample_path = str(sample["path"])
        if sample_path.endswith(".values"):
            verification = sample["verification"]
            assert verification["method"] == "render"
            assert verification["dpi"] == 350
            assert verification["page"] == sample["locator"]["page"]
            assert verification["figure"] == figure
            assert verification["covers"]
            assert len(verification["covers"]) == len(set(verification["covers"]))
            assert set(verification["locators"]) == {
                f"{sample_path}.T_C",
                f"{sample_path}.standard_state_as_printed",
                f"{sample_path}.phase_as_printed",
                f"{sample_path}.composition_closure.system_as_printed",
            }
            if figure == "3":
                # Independent render check (w1127omoriat3).
                assert sample["reviewed_by"] == "w1127omoriat3 (grok)"
                assert sample["reviewed_on"] == "2026-11-27"
            elif sample_path.split("observations[", 1)[1].split("]", 1)[0] in {
                "omori_fig5_04",
                "omori_fig5_06",
                "omori_fig5_08",
                "omori_fig5_11",
                "omori_fig5_14",
                "omori_fig5_15",
            }:
                # Independent 350 dpi re-read (w1127omorisq).
                assert sample["reviewed_by"] == "w1127omorisq (grok)"
                assert sample["reviewed_on"] == "2026-11-27"
        elif figure == "3":
            _assert_omori_derivation(sample, mole_role="mole_fraction")
        elif figure == "5":
            _assert_omori_derivation(sample, mole_role="mole_percent")

    for row_id, (activity, px, py, n_sio2, mgo_wt) in FIG5_READS.items():
        row = rows[row_id]
        values = row["values"]
        read = values["figure_read"]
        assert values["activity"] == activity
        assert values["quantity"] == "activity"
        assert row["uncertainty"]["value"] == (
            "0.08" if row_id in FIG5_WIDENED_UNCERTAINTY else "0.06"
        )
        assert values["printed_form"] == "log10"
        assert read["crop_pixel_xy"] == [px, py]
        assert Decimal(read["y_coordinate_as_read"]).quantize(Decimal("0.0001")) == Decimal(activity)
        assert values["composition_as_plotted"]["N_SiO2_percent_as_printed"] == n_sio2
        assert values["composition_as_plotted"]["MgO_mass_percent_as_series"] == mgo_wt
        assert "secondary_figure_read" not in values
        assert "fig6_secondary_locator" not in values
        assert "N_CaO_plus_MgO_over_N_SiO2_as_plotted" not in values
        assert "secondary_locator" not in row
        assert row["locator"]["figure"] == "5"
    audit_rows = {
        (row["printed_table"], row["printed_row"]): row
        for row in _figure_audit_rows(document)
    }
    for sample_id in ("33_6", "32_3"):
        audit = audit_rows[("6", f"{sample_id} same sample as Fig. 5 point {2 if sample_id == '33_6' else 4}")]
        assert "non-matching marker series" in audit["render_read"]
        assert "filled MgO 10%" in audit["render_read"]
        assert "Fig. 5 label is open MgO 20%" in audit["render_read"]
        assert "no Fig. 6 marker read retained" in audit["audit"]
    fig6_audit_rows = [row for row in audit_rows.values() if row["printed_table"] == "6"]
    assert len(fig6_audit_rows) == 18
    nonmatching_rows = [
        row for row in fig6_audit_rows if "non-matching marker series" in row["render_read"]
    ]
    crosscheck_rows = [
        row for row in fig6_audit_rows if "non-matching marker series" not in row["render_read"]
    ]
    assert len(nonmatching_rows) == 2
    assert len(crosscheck_rows) == 16
    for row in crosscheck_rows:
        assert row["render_read"].startswith("x=")
        assert "pixel=" in row["render_read"]
        assert row["audit"].endswith("unscored cross-check only")
    for row_id, expected in {
        "omori_fig5_03": (
            "34.96174689",
            "10.00000000",
            "45.03825311",
            "10.00000000",
        ),
        "omori_fig5_08": (
            "15.00796859",
            "20.00000000",
            "54.99203141",
            "10.00000000",
        ),
    }.items():
        components = rows[row_id]["point_conditions"]["composition"]["state"][
            "value"
        ]["components"]
        assert tuple(amount for _, amount in components) == expected
    assert all(row["values"]["method"] == "emf_cell" for row in rows.values())
    assert all(row["values"]["T_C"] == "1630" for row in rows.values())
    assert all(row["standard_state"]["state"]["tag"] == "unknown" for row in rows.values())
    assert all(
        "Raoultian/Henrian" in row["standard_state"]["state"]["reason"]
        and "polymorph" in row["standard_state"]["state"]["reason"]
        for row in rows.values()
    )
    for row_id, row in rows.items():
        composition = row["point_conditions"]["composition"]
        assert composition["locator"]["line_range"]
        if row_id == UNCLOSED_COMPOSITION_ID:
            assert composition["state"] == {
                "tag": "unknown",
                "reason": UNCLOSED_COMPOSITION_REASON,
            }
            assert composition["printed_coordinates"] == row["values"]["composition_as_plotted"]
        else:
            value = composition["state"]["value"]
            assert value["amount_basis"] == "mass_percent"
            assert value["basis"].endswith("by_difference_from_plot")
            assert value["components"][0][0] == "CaO"
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    fidelity_warnings = []
    errors = validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SOURCE_ID,
        check_fidelity_match=True,
        fidelity_warnings=fidelity_warnings,
        pre_policy_ids=set(),
        artifact_root=ROOT,
    )
    assert errors == []
    assert not [warning for warning in fidelity_warnings if warning.kind == "uncovered_observation"]
    assert not [warning for warning in fidelity_warnings if warning.kind == "unattested_sample"]
    assert not [warning for warning in fidelity_warnings if warning.kind == "source_blind_pin"]


def test_migration_preserves_temperatures_compositions_and_typed_activity_requests(
    tmp_path: Path,
) -> None:
    document = _document()
    source_rows = _rows(document)
    result = _isolated_migration(tmp_path, document)
    migrated = {row.observation_id.rsplit("::", 1)[-1]: row for row in result.observations.values() if row.source_id == SOURCE_ID}

    assert len(migrated) == 47
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    assert Counter(row.identity.quantity.value for row in migrated.values()) == {
        Quantity.ACTIVITY: 47,
    }

    for row_id, observation in migrated.items():
        source = source_rows[row_id]
        values = source["values"]
        formula = "SiO2" if row_id.startswith("omori_fig3_") else "Al2O3"
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.identity.species.formula == formula
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.temperature_K.value == Decimal("1903.15")
        assert observation.identity.reference_state.is_unknown
        assert observation.point_conditions["T_C"].state.value == Decimal("1630")
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1903.15")
        assert observation.point_conditions["T_C"].locator.page == 3
        if formula == "Al2O3":
            temperature_locator = source["point_conditions"]["T_C"]["locator"]
            assert temperature_locator["published_page"] == 138
            assert temperature_locator["paragraph"] == "Al2O3 activity reference discussion"
            assert temperature_locator["line_range"] == "260-262"
            assert "table" not in temperature_locator
        composition = observation.point_conditions["composition"]
        assert composition.locator.figure == source["locator"]["figure"]
        if row_id == UNCLOSED_COMPOSITION_ID:
            assert composition.state.is_unknown
            assert composition.state.reason == UNCLOSED_COMPOSITION_REASON
        else:
            assert composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
            assert set(dict(composition.state.value.components)) == {"CaO", "MgO", "SiO2", "Al2O3"}

        experiment = result.experiments[observation.experiment_id]
        if formula == "SiO2" and composition.state.is_value:
            source_x = Decimal(values["figure_read"]["x_coordinate_as_read"])
            migrated_x = dict(observation.identity.composition.value.components)["SiO2"]
            assert abs(migrated_x - source_x) <= Decimal("0.00000001")

        bench = activity_test_bench(experiment.work_id)
        inputs = collect_consumer_inputs(experiment, bench, observation)
        _assert_activity_consumer(inputs, formula, Quantity.ACTIVITY)
        requests = melt_activity_requests(inputs, experiment=experiment, bench=bench, observation=observation)
        assert {request.readiness.engine for request in requests} == {
            "alphamelts", "thermoengine", "openimcc", "internal-analytical", "magemin"
        }
        if row_id == UNCLOSED_COMPOSITION_ID:
            assert all(request.payload is None for request in requests)
            assert all(
                any(gap.waypoint == "normalized_composition" for gap in request.readiness.gaps)
                for request in requests
            )
            continue

        callable_requests = [request for request in requests if request.payload is not None]
        assert {request.readiness.engine for request in callable_requests} == {
            "alphamelts", "thermoengine", "openimcc", "internal-analytical"
        }, [
            (
                request.readiness.engine,
                request.readiness.status.value,
                tuple(
                    (gap.waypoint, gap.reason.value, gap.missing)
                    for gap in request.readiness.gaps
                ),
            )
            for request in requests
        ]
        for request in callable_requests:
            assert request.payload["reference_state"] in {
                "raoultian_pure_liquid_endmember",
                "raoultian_pure_liquid_oxide_parent",
            }
            assert any(
                flag["authority"] == "assumed"
                and flag["flag_id"] == "registered_raoultian_pure_liquid_target"
                and "phase and convention unknown" in flag["notice"]
                for flag in request.readiness.flags
            )
        magemin = next(request for request in requests if request.readiness.engine == "magemin")
        assert magemin.payload is None
        assert any(
            gap.waypoint == "reference_state"
            and gap.reason is GapReason.REFERENCE_STATE_MISMATCH
            for gap in magemin.readiness.gaps
        )

    prediction_row = migrated["omori_fig3_al10_mg30_01"]
    prediction_experiment = result.experiments[prediction_row.experiment_id]
    prediction_bench = activity_test_bench(prediction_experiment.work_id)
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        prediction_row,
        experiment=prediction_experiment,
        bench=prediction_bench,
        handles={},
        isolated=False,
    )
    assert prediction.value is not None, prediction.refusal_detail
    assert any(
        notice.kind is NoticeKind.KNOB_DEFAULTED
        and "phase and convention unknown" in notice.reason
        for notice in prediction.notices
    )
    flagged_strata = _classify_flagged_notice_facts(
        tuple((notice.kind.value, notice.reason) for notice in prediction.notices)
    )
    assert NoticeKind.KNOB_DEFAULTED.value in flagged_strata

    assert float(migrated["omori_fig3_al10_mg30_01"].value.point) == pytest.approx(0.0914745)
    for row_id, (_, _, _, _, _) in FIG5_READS.items():
        row = migrated[row_id]
        source_x = Decimal(source_rows[row_id]["values"]["figure_read"]["x_coordinate_as_read"])
        migrated_alumina_fraction = dict(row.identity.composition.value.components)["Al2O3"]
        x_uncertainty = Decimal(source_rows[row_id]["values"]["figure_read"]["read_uncertainty"]["x"])
        assert abs(migrated_alumina_fraction - source_x) <= x_uncertainty


def test_mutating_activity_classifier_rejects_every_omori_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    result = _isolated_migration(tmp_path, document)
    migrated = [row for row in result.observations.values() if row.source_id == SOURCE_ID]

    assert len(migrated) == 47
    for observation in migrated:
        experiment = result.experiments[observation.experiment_id]
        bench = activity_test_bench(experiment.work_id)
        formula = observation.identity.species.formula
        quantity = observation.identity.quantity.value
        with monkeypatch.context() as mutation:
            mutation.setattr(consumer_inputs, "is_melt_activity_observation", lambda _row: False)
            changed = collect_consumer_inputs(experiment, bench, observation)
            with pytest.raises(AssertionError):
                _assert_activity_consumer(changed, formula, quantity)
