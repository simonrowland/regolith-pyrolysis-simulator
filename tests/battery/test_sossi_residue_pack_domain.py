from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


@pytest.mark.parametrize("pack_supports_mno", [True, False])
def test_sossi_openimcc_activity_follows_loaded_pack_domain(
    monkeypatch: pytest.MonkeyPatch, pack_supports_mno: bool
) -> None:
    pytest.importorskip("openimcc")
    import openimcc

    import simulator.battery.residue as residue
    import simulator.chemistry.melt_activity as melt_activity
    import simulator.diagnostic_helpers.binary_pot_battery as binary_pot_battery
    from simulator.melt_backend import openimcc_bridge

    source = yaml.safe_load(SOURCE.read_text())
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    initial_inventory, _, _ = residue._sossi_initial_inventory(
        experiment, starting_trace
    )
    temperature_K = residue._hashimoto_point_value(
        experiment["thermal_schedule"]["setpoints_and_holds"][0]["temperature_K"]
    )
    fallback = melt_activity.melt_oxide_activity(
        "MnO", initial_inventory, temperature_K=temperature_K
    )
    assert fallback is not None

    parent_oxides = (
        "SiO2",
        "MgO",
        "FeO",
        "CaO",
        "Al2O3",
        "TiO2",
        "Na2O",
        "K2O",
        *(('MnO',) if pack_supports_mno else ()),
    )
    pack = SimpleNamespace(
        kernel_datapack=SimpleNamespace(parent_oxides=parent_oxides)
    )
    monkeypatch.setattr(openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "fixture")
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: pack)
    monkeypatch.setattr(
        openimcc_bridge,
        "engine_binding_identity",
        lambda *_args: {
            "engine_binding_digest": "fixture",
            "melt_binding_digest": "fixture",
            "condensate_table_digest": "fixture",
            "gas_table_digest": "fixture",
        },
    )
    monkeypatch.setattr(openimcc, "load_gas_datapack", lambda: object())
    monkeypatch.setattr(
        binary_pot_battery,
        "_openimcc_gas_channels_and_omission_notices",
        lambda *_args: (
            [
                ("Mn", ("MnO", 1.0, 0.0)),
                ("Ti", ("TiO2", 1.0, 0.0)),
            ],
            (),
        ),
    )

    native_activity = 0.3141592653589793
    evaluated_compositions: list[dict[str, float]] = []
    gas_activity_inputs: list[dict[str, float]] = []

    def evaluate_melt(**kwargs):
        evaluated_compositions.append(dict(kwargs["composition_mol"]))
        activities = {oxide: 0.5 for oxide in parent_oxides}
        if pack_supports_mno:
            activities["MnO"] = native_activity
        return SimpleNamespace(
            parent_oxide_activities=activities,
            parent_oxides=parent_oxides,
            flags=(),
            extrapolated=False,
            pack_model_id="fixture-model",
            pack_version="fixture-version",
            pack_digest="fixture-digest",
            openimcc_version="fixture-version",
        )

    def evaluate_gas(activities, *_args, **_kwargs):
        gas_activity_inputs.append(dict(activities))
        return {"Mn": 1.0, "Ti": 1.0}

    monkeypatch.setattr(openimcc_bridge, "evaluate", evaluate_melt)
    monkeypatch.setattr(openimcc, "evaluate_gas", evaluate_gas)
    monkeypatch.setattr(
        residue,
        "_evaluate_openimcc_isolated",
        lambda evaluate, *, timeout_s, **kwargs: evaluate(**kwargs),
    )
    monkeypatch.setattr(residue, "_hashimoto_liquid_row_provenance", lambda *_args: ())
    monkeypatch.setattr(
        residue,
        "integrate_residue_inventory",
        lambda inventory, *_args, **_kwargs: SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        ),
    )

    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text()),
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        code_revision="sossi-pack-domain-test",
        engine="openimcc",
        sample_surface_area_m2=2.0e-5,
    )[0]

    assert prediction.primary_element_ppm["Mn"] > 0.0
    assert evaluated_compositions
    assert gas_activity_inputs
    if pack_supports_mno:
        assert "MnO" in evaluated_compositions[0]
        assert gas_activity_inputs[0]["MnO"] == native_activity
        assert "MnO" not in prediction.provenance.get("melt_activity_extensions", {})
    else:
        assert "MnO" not in evaluated_compositions[0]
        assert gas_activity_inputs[0]["MnO"] == pytest.approx(
            fallback.thermodynamic_parent_activity()
        )
        assert "MnO" in prediction.provenance["melt_activity_extensions"]


def test_sossi_openimcc_scorer_streams_numeric_flagged_manganese(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from simulator.battery.enums import Engine, Rail
    import simulator.battery.residue as residue
    import simulator.battery.score as score
    from simulator.battery.score import load_score_context
    from simulator.melt_backend import openimcc_bridge

    context = load_score_context(sources=("kems-012-sossi-2019",))
    source_rows = [
        row
        for row in context.observations.values()
        if row.source_id == "kems-012-sossi-2019"
        and row.admission.status.value == "admitted"
        and row.identity.species.formula == "Mn"
    ]
    assert source_rows

    def fallback_cohort(experiments, *_args, **_kwargs):
        return tuple(
            SimpleNamespace(
                experiment_id=experiment["experiment_id"],
                primary_element_ppm={"Mn": 900.0, "Ti": 450.0},
                sensitivity_band_ppm={
                    "Mn": (800.0, 1000.0),
                    "Ti": (400.0, 500.0),
                },
                channel_missing_elements=(),
                provenance={
                    "code_revision": "sossi-scorer-stream-test",
                    "transport_conditions": {
                        "total_pressure_pa": 101325.0,
                        "carrier_gas_mixture": None,
                        "flow_rate_sccm": None,
                        "pipe_diameter_m": None,
                        "transport_length_m": None,
                    },
                    "transport_source_locations": {
                        "total_pressure_pa": {"page": 2},
                        "sweep_gas_mixture_and_flow_rate_sccm": {"page": 55},
                        "tube_geometry": {"page": 6},
                    },
                    "melt_activity_extensions": {
                        "MnO": {
                            "reason": "MnO is outside the IMCC-SF04 melt-activity domain",
                            "authority_level": "UNCERTIFIED",
                            "certified_band_K": None,
                        }
                    },
                    "liquid_rows_used": (),
                    "integration": {"primary": {"status": "converged"}},
                },
            )
            for experiment in experiments
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", fallback_cohort)
    monkeypatch.setattr(
        openimcc_bridge,
        "openimcc_pack_identity",
        lambda: {"package_version": "fixture", "commit": "fixture", "packs": {}},
    )
    path = tmp_path / "sossi-residue.jsonl"
    written, metadata = score._score_store_to_jsonl(
        context,
        path,
        root=ROOT,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    rows = tuple(
        score._ResidualJsonlRows(path.with_name(path.name + ".partial"), metadata=metadata)
    )
    manganese_references = {row.observation_id for row in source_rows}
    manganese = next(
        row for row in rows if row["reference"] in manganese_references
    )

    assert written == len(rows)
    assert manganese["numeric"] is not None
    assert any(
        notice.get("reason") == "MnO is outside the IMCC-SF04 melt-activity domain"
        for notice in manganese["notices"]
    )
