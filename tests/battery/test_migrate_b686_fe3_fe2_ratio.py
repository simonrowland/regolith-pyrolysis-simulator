"""b-686: Fe3+/Fe2+ under the existing quantity fe3_fe2_ratio (no new quantity).

The pin commit (006e13b70) captured green e702b2551: a row declaring
fe3_fe2_ratio selected no number from Fe3+/sum Fe, Kress-style Fe2O3/FeO
wt %, or a directly printed ratio, and Aithala 2026's Table S-1 Fe3+/FeT rows
migrated with an unknown quantity ('unsupported quantity Fe3+/FeT'). The
extract now declares quantity fe3_fe2_ratio on the 16 measured Table S-1 rows
(the printed Fe3_over_FeT is unchanged). Expectations flipped by the change
are marked "b-686 change".

Reductions (derivation in simulator/battery/migrate.py, b-686 block):
  ratio = r/(1 - r) from r = Fe3+/sum Fe;
  ratio = 2 (w_Fe2O3/M(Fe2O3))/(w_FeO/M(FeO)) = 0.8998 w_Fe2O3/w_FeO,
  molar masses from oxide_molar_mass (FeO 71.844, Fe2O3 159.687).
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.battery.enums import (
    EvidenceClass,
    IdentityEqualKind,
    Quantity,
    ValueKind,
)
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import (
    FERRIC_FRACTION_RELATION,
    Migrator,
    fe3_fe2_ratio_from_ferric_fraction,
    fe3_fe2_ratio_from_oxide_wt_pct,
    fe3_fe2_ratio_reduction,
    kress_wt_pct_relation,
    oxide_molar_mass,
    select_declared_source,
)
from simulator.battery.records import Evidence, Locator, State

REPO = Path(__file__).resolve().parents[2]
AITHALA = REPO / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"

# Table S-1 (PDF p. 13, SI-7): run id -> printed Fe3+/FeT, as in the extract.
AITHALA_S1_FE3_OVER_FET = {
    "aithala_2026_vf271_xanes": "0.816",
    "aithala_2026_vf267_moessbauer": "0.736",
    "aithala_2026_vf231_xanes": "0.726",
    "aithala_2026_vf231_moessbauer": "0.716",
    "aithala_2026_vf268_xanes": "0.65",
    "aithala_2026_vf268_moessbauer": "0.695",
    "aithala_2026_vf270_xanes": "0.685",
    "aithala_2026_vf270_moessbauer": "0.661",
    "aithala_2026_vf274_xanes": "0.637",
    "aithala_2026_vf274_moessbauer": "0.643",
    "aithala_2026_1800_30_xanes": "0.426",
    "aithala_2026_1900_30_xanes": "0.389",
    "aithala_2026_2000_30_xanes": "0.407",
    "aithala_2026_2100_30_xanes": "0.306",
    "aithala_2026_2000_10_xanes": "0.378",
    "aithala_2026_2000_120_xanes": "0.378",
}


PREFIX = "aithala-2026-magmatic-iron-redox-to-2100c::"


def _migrate(path: Path):
    migrator = Migrator()
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _aithala_fe_rows(result):
    return {
        oid[len(PREFIX):]: obs
        for oid, obs in result.observations.items()
        if oid.startswith(PREFIX)
        and oid[len(PREFIX):] in AITHALA_S1_FE3_OVER_FET
    }


def test_aithala_s1_rows_migrate_to_fe3_fe2_ratio_measured_reduced() -> None:
    # b-686 change. Pin (006e13b70): quantity unknown ("unsupported quantity
    # 'Fe3+/FeT'"), value UNAVAILABLE, evidence measured_direct.
    result = _migrate(AITHALA)
    rows = _aithala_fe_rows(result)
    assert set(rows) == set(AITHALA_S1_FE3_OVER_FET)
    asset_ids = {
        asset.asset_id
        for work in result.works.values()
        for asset in work.source_files.files
    }
    for oid, obs in rows.items():
        r = Decimal(AITHALA_S1_FE3_OVER_FET[oid])
        assert obs.identity.quantity.value is Quantity.FE3_FE2_RATIO, oid
        assert obs.value.kind is ValueKind.POINT, oid
        assert obs.value.point == r / (1 - r), oid
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED, oid
        assert obs.evidence.original_method_class == "measured_direct", oid
        assert obs.derivation.relation == FERRIC_FRACTION_RELATION, oid
        assert obs.derivation.output_unit == "dimensionless", oid
        (name, printed), = obs.derivation.parameters
        assert name == "original_Fe3_over_FeT", oid
        assert printed.state.value == r, oid
        assert printed.locator.table == "S-1", oid
        assert obs.derived_from == obs.derivation.inputs, oid
        assert obs.derived_from and set(obs.derived_from).issubset(asset_ids), oid
        assert not [
            issue for issue in result.validation.hard_issues
            if issue.reason.value == "conditional_field"
            and issue.path.startswith(f"observation[{obs.observation_id}].")
        ], oid


def test_aithala_s3_recalculated_rows_are_not_declared_fe3_fe2_ratio() -> None:
    # Table S-3 rows (species.Fe.context[]) are the authors' model recasting
    # of the S-1 measurements to air and the S-2 reference composition
    # (model_derived). They stay labelled Fe3+/FeT and are not declared
    # fe3_fe2_ratio, so they cannot double-count the measured rows.
    doc = yaml.safe_load(AITHALA.read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    labels = [
        (row.get("type"), row["values"]["quantity"])
        for row in walk(doc["species"])
        if isinstance(row.get("values"), dict) and "quantity" in row["values"]
        and "Fe3_over_FeT" in row["values"]
    ]
    assert labels.count(("concentration_series", "fe3_fe2_ratio")) == 16
    assert labels.count(("recalculation_table", "Fe3+/FeT")) == 20
    assert len(labels) == 36
    result = _migrate(AITHALA)
    ratio_rows = [
        oid for oid, obs in result.observations.items()
        if obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    ]
    assert sorted(oid[len(PREFIX):] for oid in ratio_rows) == sorted(AITHALA_S1_FE3_OVER_FET)


def _fixture_extract(tmp_path: Path, rows: list[dict]) -> Path:
    doc = yaml.safe_load(AITHALA.read_text(encoding="utf-8"))
    template = next(
        o for o in doc["species"]["Fe"]["observations"]
        if o["observation_id"] == "aithala_2026_vf271_xanes"
    )
    built = []
    for row in rows:
        obs = copy.deepcopy(template)
        obs.update(row)
        built.append({k: v for k, v in obs.items() if v is not None})
    doc["species"]["Fe"]["observations"] = built
    out = tmp_path / AITHALA.name
    out.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out


def _ferric_fraction_row(
    observation_id: str, method_class: str, *, series: bool
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "method_class": method_class,
        "quantity": "fe3_fe2_ratio",
    }
    if series:
        values["series"] = [
            {"temperature_K": 1523.15, "Fe3_over_FeT": 0.5},
            {"temperature_K": 1573.15, "Fe3_over_FeT": 0.25},
        ]
    else:
        values["Fe3_over_FeT"] = "0.72"
    row: dict[str, Any] = {"observation_id": observation_id, "values": values}
    if series:
        row["T_range_K"] = None
    return row


def test_migrator_reduces_kress_wt_pct_row(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_kress",
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "Fe2O3_wt_pct": "1.09",
            "FeO_wt_pct": "10.34",
        },
    }])
    obs = _migrate(path).observations[PREFIX + "fixture_kress"]
    assert obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert obs.value.point == fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1.09"), Decimal("10.34"))
    assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert obs.derivation.relation == kress_wt_pct_relation()
    assert [(n, p.state.value) for n, p in obs.derivation.parameters] == [
        ("original_Fe2O3_wt_pct", Decimal("1.09")),
        ("original_FeO_wt_pct", Decimal("10.34")),
    ]


def test_migrator_reduces_ferric_fraction_series_points(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_series",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "series": [
                {"temperature_K": 1523.15, "Fe3_over_FeT": 0.5},
                {"temperature_K": 1573.15, "Fe3_over_FeT": 0.25},
            ],
        },
    }])
    result = _migrate(path)
    points = {
        oid: obs for oid, obs in result.observations.items()
        if oid.startswith(PREFIX + "fixture_series::")
    }
    assert len(points) == 2
    assert sorted(obs.value.point for obs in points.values()) == [
        Decimal("0.25") / Decimal("0.75"), Decimal(1)
    ]
    asset_ids = {
        asset.asset_id
        for work in result.works.values()
        for asset in work.source_files.files
    }
    for obs in points.values():
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert obs.derivation.relation == FERRIC_FRACTION_RELATION
        (name, _), = obs.derivation.parameters
        assert name == "original_Fe3_over_FeT"
        assert obs.derived_from
        assert obs.derived_from == obs.derivation.inputs
        assert set(obs.derived_from).issubset(asset_ids)
        assert not [
            issue for issue in result.validation.hard_issues
            if issue.reason.value == "conditional_field"
            and issue.path.startswith(f"observation[{obs.observation_id}].")
        ]


def test_migrated_ferric_fraction_has_resolvable_lineage_and_derivation(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_lineage",
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "Fe3_over_FeT": "0.72",
        },
    }])
    result = _migrate(path)
    obs = result.observations[PREFIX + "fixture_lineage"]
    assert obs.value.point == Decimal("2.571428571428571428571428571")
    assert obs.derivation is not None
    assert obs.derivation.relation == FERRIC_FRACTION_RELATION
    assert obs.derived_from == obs.derivation.inputs
    assert set(obs.derived_from).issubset({
        asset.asset_id
        for work in result.works.values()
        for asset in work.source_files.files
    })
    assert not [
        issue for issue in result.validation.hard_issues
        if issue.reason.value == "conditional_field"
        and issue.path.startswith(f"observation[{obs.observation_id}].")
    ]


@pytest.mark.parametrize("method_class", ["model_derived", "measured_reduced"])
@pytest.mark.parametrize("series", [False, True], ids=["scalar", "series"])
def test_already_derived_ferric_fraction_keeps_missing_lineage_diagnostic(
    tmp_path: Path, method_class: str, series: bool
) -> None:
    child_id = f"fixture_{method_class}_{'series' if series else 'scalar'}"
    path = _fixture_extract(
        tmp_path,
        [_ferric_fraction_row(child_id, method_class, series=series)],
    )
    result = _migrate(path)
    child_prefix = PREFIX + child_id
    observations = [
        obs for oid, obs in result.observations.items()
        if oid == child_prefix or oid.startswith(child_prefix + "::")
    ]
    assert len(observations) == (2 if series else 1)
    for obs in observations:
        assert obs.derived_from is None
        assert any(
            issue.reason.value == "conditional_field"
            and issue.path == f"observation[{obs.observation_id}].derived_from"
            for issue in result.validation.hard_issues
        )


@pytest.mark.parametrize("method_class", ["model_derived", "measured_reduced"])
@pytest.mark.parametrize("series", [False, True], ids=["scalar", "series"])
def test_already_derived_ferric_fraction_preserves_author_inputs(
    tmp_path: Path, method_class: str, series: bool
) -> None:
    parent_id = "fixture_parent"
    child_id = f"fixture_{method_class}_{'series' if series else 'scalar'}"
    parent = {
        "observation_id": parent_id,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "fe3_fe2_ratio": "1",
        },
    }
    child = _ferric_fraction_row(child_id, method_class, series=series)
    child["derived_from"] = [parent_id]
    child["derivation"] = {
        "relation": "author reduction from the parent observation",
        "inputs": [parent_id],
        "output_unit": "dimensionless",
    }
    path = _fixture_extract(tmp_path, [parent, child])
    result = _migrate(path)
    child_prefix = PREFIX + child_id
    observations = [
        obs for oid, obs in result.observations.items()
        if oid == child_prefix or oid.startswith(child_prefix + "::")
    ]
    assert len(observations) == (2 if series else 1)
    expected_parent = (PREFIX + parent_id,)
    for obs in observations:
        assert obs.derived_from == expected_parent
        assert obs.derivation is not None
        assert obs.derivation.inputs == expected_parent
        assert not [
            issue for issue in result.validation.hard_issues
            if issue.reason.value == "conditional_field"
            and issue.path == f"observation[{obs.observation_id}].derived_from"
        ]


def test_migrator_refuses_ferric_fraction_one_without_infinity(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_no_fe2",
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "Fe3_over_FeT": "1",
        },
    }])
    obs = _migrate(path).observations[PREFIX + "fixture_no_fe2"]
    assert obs.value.kind is ValueKind.UNAVAILABLE
    assert "undefined" in obs.value.unavailable_reason
    assert obs.value.point is None


def test_redox_row_log_overrides_series_oxygen_and_inherits_when_absent(
    tmp_path: Path,
) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_row_log",
        "experiment": "aithala-2026-vf271",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "admission_status": "admitted",
            "oxygen_partial_pressure": {"value": "1e-7", "units": "bar"},
            "series": [
                {"T_C": 1250, "Fe3_over_FeT": 0.5},
                {"T_C": 1260, "Fe3_over_FeT": 0.25, "log10_fO2": -8},
            ],
        },
    }])
    rows = [
        obs
        for obs in _migrate(path).observations.values()
        if "fixture_row_log::" in obs.observation_id
    ]
    assert len(rows) == 2
    assert rows[0].identity.fO2_Pa.value == Decimal("0.01")
    assert rows[1].identity.fO2_Pa.value == Decimal("0.001")


def test_valence_rows_stamp_printed_po2_and_absolute_log(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [
        {
            "observation_id": "fixture_valence_po2_1",
            "experiment": "aithala-2026-vf271",
            "T_range_K": None,
            "phase": "glass",
            "values": {
                "method_class": "measured_direct",
                "quantity": "average_oxidation_state",
                "admission_status": "admitted",
                "series": [{
                    "T_C": 1250,
                    "value": "2.5",
                    "oxygen_partial_pressure": {"value": "1e-8", "units": "atm"},
                }],
            },
        },
        {
            "observation_id": "fixture_valence_po2_2",
            "experiment": "aithala-2026-vf271",
            "T_range_K": None,
            "phase": "glass",
            "values": {
                "method_class": "measured_direct",
                "quantity": "average_oxidation_state",
                "admission_status": "admitted",
                "series": [{
                    "T_C": 1250,
                    "value": "2.5",
                    "oxygen_partial_pressure": {"value": "1e-9", "units": "atm"},
                }],
            },
        },
        {
            "observation_id": "fixture_valence_log",
            "experiment": "aithala-2026-vf271",
            "T_range_K": None,
            "phase": "glass",
            "values": {
                "method_class": "measured_direct",
                "quantity": "average_oxidation_state",
                "admission_status": "admitted",
                "series": [{
                    "T_C": 1250,
                    "value": "2.5",
                    "log_fO2": {"value": "-8", "units": "log10(atm)"},
                }],
            },
        },
        {
            "observation_id": "fixture_valence_delta_iw",
            "experiment": "aithala-2026-vf271",
            "T_range_K": None,
            "phase": "glass",
            "values": {
                "method_class": "measured_direct",
                "quantity": "average_oxidation_state",
                "admission_status": "admitted",
                "series": [{
                    "T_C": 1250,
                    "value": "2.5",
                    "log_fO2": {"value": "-1", "units": "log10(IW)"},
                    "point_conditions": {
                        "fO2_control": {
                            "state": {"tag": "value", "value": "delta-IW"},
                            "locator": {"published_page": 1},
                        }
                    },
                }],
            },
        },
    ])
    result = _migrate(path)
    rows = {
        obs.observation_id.split("::", 1)[1].split("::", 1)[0]: obs
        for obs in result.observations.values()
        if "fixture_valence_" in obs.observation_id
    }

    first = rows["fixture_valence_po2_1"]
    second = rows["fixture_valence_po2_2"]
    logged = rows["fixture_valence_log"]
    delta_iw = rows["fixture_valence_delta_iw"]
    assert first.identity.quantity.value is Quantity.VALENCE_RATIO
    assert first.identity.fO2_Pa.value == Decimal("0.00101325")
    assert first.point_conditions["fO2_Pa"].state.value.point == Decimal("0.00101325")
    assert second.identity.fO2_Pa.value == Decimal("0.000101325")
    assert logged.identity.fO2_Pa.value == Decimal("0.00101325")
    outcome = identity_equal(first.identity, second.identity)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "fO2_Pa" in outcome.fields
    assert delta_iw.identity.fO2_Pa.is_unknown
    assert "buffer-relative fO2" in delta_iw.identity.fO2_Pa.reason
    assert "delta-IW" in delta_iw.identity.fO2_Pa.reason
    assert delta_iw.point_conditions["fO2_control"].state.value == "delta-IW"


def test_valence_rows_stamp_printed_pressure_and_keep_missing_unknown(
    tmp_path: Path,
) -> None:
    source_experiment = "aithala-2026-vf271"
    fixture_experiment = "aithala-2026-vf271-no-pressure-fixture"
    base_point = {
        "T_C": 1250,
        "value": "2.5",
        "log_fO2": {"value": "-8", "units": "log10(bar)"},
        "composition_mol": {"SiO2": "0.5", "MgO": "0.5"},
    }
    rows = []
    for suffix, pressure in (
        ("1gpa", "1000000000"),
        ("2gpa", "2000000000"),
        ("no_pressure", None),
    ):
        point = copy.deepcopy(base_point)
        if pressure is not None:
            point["point_conditions"] = {
                "total_pressure_Pa": {
                    "state": {
                        "tag": "value",
                        "value": {"kind": "point", "point": pressure},
                    },
                    "locator": {"page": 13, "table": "3"},
                }
            }
        rows.append(
            {
                "observation_id": f"fixture_valence_pressure_{suffix}",
                "experiment": fixture_experiment,
                "T_range_K": None,
                "phase": "glass",
                "values": {
                    "method_class": "measured_direct",
                    "quantity": "average_oxidation_state",
                    "admission_status": "admitted",
                    "series": [point],
                },
            }
        )

    path = _fixture_extract(tmp_path, rows)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    fixture = copy.deepcopy(
        next(
            experiment
            for experiment in document["experiments"]
            if experiment["experiment_id"] == source_experiment
        )
    )
    fixture["experiment_id"] = fixture_experiment
    fixture["pressure_environment"]["total_pressure_Pa"]["state"] = {
        "tag": "unknown",
        "reason": "not_published",
    }
    document["experiments"].append(fixture)
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    migrated = _migrate(path)
    stamped = {
        observation.observation_id.split("::", 2)[1]: observation
        for observation in migrated.observations.values()
        if "fixture_valence_pressure_" in observation.observation_id
    }
    one_gpa = stamped["fixture_valence_pressure_1gpa"]
    two_gpa = stamped["fixture_valence_pressure_2gpa"]
    no_pressure = stamped["fixture_valence_pressure_no_pressure"]

    assert len(stamped) == 3
    assert all(
        observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value == Decimal("1523.15")
        for observation in stamped.values()
    )
    assert one_gpa.identity.quantity.value is Quantity.VALENCE_RATIO
    assert one_gpa.identity.total_pressure_Pa.is_value
    assert one_gpa.identity.total_pressure_Pa.value == Decimal("1000000000")
    pressure_condition = one_gpa.point_conditions["total_pressure_Pa"]
    assert pressure_condition.state.value.kind is ValueKind.POINT
    assert pressure_condition.state.value.point == Decimal("1000000000")
    assert pressure_condition.locator is not None
    assert pressure_condition.locator.table == "3"
    assert two_gpa.identity.total_pressure_Pa.is_value
    assert two_gpa.identity.total_pressure_Pa.value == Decimal("2000000000")
    assert one_gpa.identity.composition is not None
    assert one_gpa.identity.composition.is_value
    assert dict(one_gpa.identity.composition.value.components) == {
        "SiO2": Decimal("0.5"),
        "MgO": Decimal("0.5"),
    }
    composition_condition = one_gpa.point_conditions["composition"]
    assert composition_condition.state == one_gpa.identity.composition
    pressure_difference = identity_equal(one_gpa.identity, two_gpa.identity)
    assert pressure_difference.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "total_pressure_Pa" in pressure_difference.fields
    assert no_pressure.identity.total_pressure_Pa.is_unknown
    assert no_pressure.identity.total_pressure_Pa.reason == (
        "no total_pressure_Pa mapped from source"
    )


def test_fe_ratio_identity_uses_printed_absolute_po2(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_fe_row_po2",
        "experiment": "aithala-2026-vf271",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "admission_status": "admitted",
            "series": [{
                "T_C": 1250,
                "Fe3_over_FeT": 0.5,
                "oxygen_partial_pressure": {"value": "1e-8", "units": "atm"},
            }],
        },
    }])
    obs = next(
        obs for obs in _migrate(path).observations.values()
        if "fixture_fe_row_po2::" in obs.observation_id
    )

    assert obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert obs.identity.fO2_Pa.value == Decimal("0.00101325")


@pytest.mark.parametrize(
    "row_fields,expected",
    [
        ({"log_fO2": {"value": -8, "units": "log10(bar)"}}, Decimal("0.001")),
        ({"log_fO2": {"value": -8, "units": "log10(atm)"}}, Decimal("0.00101325")),
    ],
)
def test_redox_row_log_identity_uses_declared_pressure_frame(
    tmp_path: Path, row_fields: dict, expected: Decimal
) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_row_log_frame",
        "experiment": "aithala-2026-vf271",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "admission_status": "admitted",
            "oxygen_partial_pressure": {"value": "1e-7", "units": "bar"},
            "series": [{"T_C": 1250, "Fe3_over_FeT": 0.5, **row_fields}],
        },
    }])
    rows = [
        obs for obs in _migrate(path).observations.values()
        if "fixture_row_log_frame::" in obs.observation_id
    ]
    assert len(rows) == 1
    assert rows[0].identity.fO2_Pa.value == expected


@pytest.mark.parametrize("with_series_pressure", [False, True])
@pytest.mark.parametrize(
    "typed_log",
    [
        "-8",
        {"kind": "point", "point": "-8"},
    ],
    ids=["typed-scalar", "typed-point-value"],
)
def test_absolute_typed_row_log_overrides_series_oxygen(
    tmp_path: Path, with_series_pressure: bool, typed_log: object
) -> None:
    values = {
        "method_class": "measured_direct",
        "quantity": "fe3_fe2_ratio",
        "admission_status": "admitted",
        "series": [{
            "T_C": 1250,
            "Fe3_over_FeT": 0.5,
            "point_conditions": {
                "fO2_log": {
                    "state": {"tag": "value", "value": typed_log},
                    "locator": {"published_page": 1},
                }
            },
        }],
    }
    if with_series_pressure:
        values["oxygen_partial_pressure"] = {"value": "1e-7", "units": "bar"}
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_typed_row_log",
        "experiment": "aithala-2026-vf271",
        "T_range_K": None,
        "values": values,
    }])
    rows = [
        obs for obs in _migrate(path).observations.values()
        if "fixture_typed_row_log::" in obs.observation_id
    ]
    assert len(rows) == 1
    obs = rows[0]
    assert obs.identity.temperature_K.value == Decimal("1523.15")
    assert obs.identity.fO2_Pa.value == Decimal("0.001")
    located = obs.point_conditions["fO2_log"]
    assert located.locator is not None
    assert located.locator.published_page == 1
    if isinstance(typed_log, str):
        assert located.state.value == Decimal("-8")
    else:
        assert located.state.value.kind is ValueKind.POINT
        assert located.state.value.point == Decimal("-8")


@pytest.mark.parametrize(
    "row_fields",
    [
        {
            "point_conditions": {
                "fO2_log": {
                    "state": {
                        "tag": "value",
                        "value": {
                            "kind": "bound",
                            "bound_operator": "<",
                            "bound_value": "-8",
                        },
                    },
                    "locator": {"published_page": 1},
                }
            }
        },
        {
            "point_conditions": {
                "fO2_log": {
                    "state": {"tag": "value", "value": "-8"},
                    "locator": {"published_page": 1},
                }
            },
            "oxygen_buffer_as_published": "IW",
        },
        {"log_fO2": {"value": -2, "units": "log10(IW)"}},
        {"log_fO2": {"value": -8, "units": "log10(Pa)"}},
        {"log_fO2": -10.5, "oxygen_buffer_as_published": "IW"},
    ],
)
def test_bounded_or_buffer_relative_redox_row_does_not_inherit_series_fO2_point(
    tmp_path: Path, row_fields: dict
) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_bounded_row",
        "experiment": "aithala-2026-vf271",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "admission_status": "admitted",
            "oxygen_partial_pressure": {"value": "1e-7", "units": "bar"},
            "series": [{
                "T_C": 1250,
                "Fe3_over_FeT": 0.5,
                **row_fields,
            }],
        },
    }])
    rows = [
        obs for obs in _migrate(path).observations.values()
        if "fixture_bounded_row::" in obs.observation_id
    ]
    assert len(rows) == 1
    assert rows[0].identity.fO2_Pa.is_unknown


def test_declared_ratio_from_ferric_fraction_is_r_over_one_minus_r() -> None:
    # b-686 change: was unavailable ("no mapped fe3_fe2_ratio field selected").
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "Fe3_over_FeT": 0.25}
    )
    assert sel.available
    assert sel.amount == Decimal("0.25") / Decimal("0.75")
    assert sel.field_name == "Fe3_over_FeT"
    assert sel.unit_trail == "ferric_fraction_to_fe3_fe2_ratio"
    assert sel.reduction == (FERRIC_FRACTION_RELATION, (("Fe3_over_FeT", Decimal("0.25")),))


def test_declared_ratio_from_kress_wt_pct_is_0_8998_w3_over_w2() -> None:
    # b-686 change: was unavailable ("no mapped fe3_fe2_ratio field selected").
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO,
        None,
        {"quantity": "fe3_fe2_ratio", "Fe2O3_wt_pct": "1.09", "FeO_wt_pct": "10.34"},
    )
    assert sel.available
    m3, m2 = oxide_molar_mass("Fe2O3"), oxide_molar_mass("FeO")
    assert (m3, m2) == (Decimal("159.687"), Decimal("71.844"))
    expected = 2 * (Decimal("1.09") / m3) / (Decimal("10.34") / m2)
    assert sel.amount == expected
    # The ruling's shorthand: 2 x 71.844/159.688 = 0.8998 (4 s.f.); the
    # oxide table's 159.687 rounds to the same factor.
    assert round(2 * m2 / m3, 4) == Decimal("0.8998")
    assert abs(sel.amount - Decimal("0.8998") * Decimal("1.09") / Decimal("10.34")) < Decimal("1e-5")
    assert sel.reduction == (
        kress_wt_pct_relation(),
        (("Fe2O3_wt_pct", Decimal("1.09")), ("FeO_wt_pct", Decimal("10.34"))),
    )
    assert "0.8998 w_Fe2O3/w_FeO" in kress_wt_pct_relation()


def test_declared_ratio_printed_directly_is_read_as_printed() -> None:
    # b-686 change: was unavailable.
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "fe3_fe2_ratio": 0.5}
    )
    assert sel.available
    assert sel.amount == Decimal("0.5")
    assert sel.unit_trail == "identity"
    assert sel.reduction is None


@pytest.mark.parametrize("r", [0, 1, "1.2", "-0.1", "n.d."])
def test_ferric_fraction_outside_open_unit_interval_is_refused(r) -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": r})
    assert not sel.available
    assert sel.amount is None
    assert "fe3_fe2_ratio = r/(1 - r) is undefined" in sel.reason


@pytest.mark.parametrize(
    "w3, w2", [("0", "10.3"), ("1.09", "0"), ("-1", "10.3"), ("b.d.l.", "10.3")]
)
def test_kress_wt_pct_needs_both_oxides_positive(w3, w2) -> None:
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"Fe2O3_wt_pct": w3, "FeO_wt_pct": w2}
    )
    assert not sel.available
    assert "fe3_fe2_ratio is undefined" in sel.reason


def test_lone_feo_wt_pct_is_not_a_redox_statement() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"FeO_wt_pct": "10.34"})
    assert not sel.available
    assert sel.reduction is None


def test_domain_functions_are_exact() -> None:
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal("0.5")) == Decimal(1)
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal("0.816")) == Decimal("0.816") / Decimal("0.184")
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal(0)) is None
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal(1)) is None
    # Round trip: R = r/(1 - r)  <=>  r = R/(1 + R).
    ratio = fe3_fe2_ratio_from_ferric_fraction(Decimal("0.306"))
    assert abs(ratio / (1 + ratio) - Decimal("0.306")) < Decimal("1e-25")
    # One mole Fe2O3 per mole FeO is two Fe3+ per Fe2+.
    assert fe3_fe2_ratio_from_oxide_wt_pct(
        oxide_molar_mass("Fe2O3"), oxide_molar_mass("FeO")
    ) == Decimal(2)
    assert fe3_fe2_ratio_from_oxide_wt_pct(Decimal(0), Decimal(1)) is None


def test_reduction_makes_measured_row_measured_reduced_with_printed_parent() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": "0.25"})
    locator = Locator(page=13, table="S-1")
    evidence = Evidence(
        class_=State.of(EvidenceClass.MEASURED_DIRECT),
        original_method_class="measured_direct",
    )
    reduced, derivation = fe3_fe2_ratio_reduction(sel, evidence, locator, "pdf:x")
    assert reduced.class_.value is EvidenceClass.MEASURED_REDUCED
    assert reduced.original_method_class == "measured_direct"
    assert derivation.relation == FERRIC_FRACTION_RELATION
    assert derivation.inputs == ("pdf:x",)
    assert derivation.output_unit == "dimensionless"
    (name, printed), = derivation.parameters
    assert name == "original_Fe3_over_FeT"
    assert printed.state.value == Decimal("0.25")
    assert printed.locator == locator


def test_reduction_leaves_non_measured_class_and_as_printed_rows_alone() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": "0.25"})
    model = Evidence(class_=State.of(EvidenceClass.MODEL_DERIVED), original_method_class="model_derived")
    kept, derivation = fe3_fe2_ratio_reduction(sel, model, None, "pdf:x")
    assert kept is model
    assert derivation is not None
    direct = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"fe3_fe2_ratio": "0.5"})
    measured = Evidence(class_=State.of(EvidenceClass.MEASURED_DIRECT))
    assert fe3_fe2_ratio_reduction(direct, measured, None, "pdf:x") == (measured, None)
