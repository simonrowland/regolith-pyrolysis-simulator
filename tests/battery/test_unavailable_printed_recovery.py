"""Printed numbers the migrator used to leave UNAVAILABLE (b-981).

Expected amounts are the PDF table cells, not a copy of the extract YAML.
Ichise Table 2 is the MinerU decode of the paper (published p. 555).
Sossi Table 1 is the pdftotext of Geochimica et Cosmochimica Acta 288 (2020).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import (
    AmountBasis,
    EvidenceClass,
    Phase,
    Quantity,
    ReferenceStateConvention,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.migrate import select_declared_source
from simulator.yaml_cache import load_cached_safe_yaml
from tests.battery.test_migrate import _write_min_tree
from tests.battery.test_printed_point_conditions import (
    _EXTRACTS,
    _migrate_real_extract,
)

# Ichise, Maruo, Sasho, Ueshima and Mori, Trans. ISIJ 22 (1982) 552.
# Table 2, "Result of activity measurements at 1 823 K."
# (x_Mo, a_Fe) as printed. The starred 0.528 is still a Table 2 cell.
_ICHISE_TABLE2 = (
    (Decimal("0.096"), Decimal("0.897")),
    (Decimal("0.154"), Decimal("0.810")),
    (Decimal("0.200"), Decimal("0.800")),
    (Decimal("0.201"), Decimal("0.767")),
    (Decimal("0.240"), Decimal("0.692")),
    (Decimal("0.251"), Decimal("0.737")),
    (Decimal("0.254"), Decimal("0.741")),
    (Decimal("0.300"), Decimal("0.690")),
    (Decimal("0.302"), Decimal("0.673")),
    (Decimal("0.333"), Decimal("0.677")),
    (Decimal("0.339"), Decimal("0.652")),
    (Decimal("0.348"), Decimal("0.652")),
    (Decimal("0.434"), Decimal("0.621")),
    (Decimal("0.434"), Decimal("0.656")),
    (Decimal("0.434"), Decimal("0.528")),
    (Decimal("0.556"), Decimal("0.585")),
    (Decimal("0.613"), Decimal("0.552")),
    (Decimal("0.666"), Decimal("0.526")),
    (Decimal("0.778"), Decimal("0.556")),
    (Decimal("0.793"), Decimal("0.538")),
    (Decimal("0.842"), Decimal("0.518")),
    (Decimal("0.912"), Decimal("0.360")),
    (Decimal("0.912"), Decimal("0.383")),
)

# Sossi et al., GCA 288 (2020) Table 1. d65Cu relative to the starting material.
# (run slug as the migrator prints it, T_C, d65Cu, 2SD)
_SOSSI_CU = (
    ("p-5/04/17b", Decimal("1500"), Decimal("2.15"), Decimal("0.02")),
    ("p-10/04/17a", Decimal("1500"), Decimal("1.03"), Decimal("0.02")),
    ("p-7/04/17a", Decimal("1300"), Decimal("0.11"), Decimal("0.05")),
    ("p-01/08/18d", Decimal("1396.6"), Decimal("11.96"), Decimal("0.09")),
)


def _ichise_rows(result):
    prefix = "kems-111-ichise-1982::ichise_1982_table2_a_Fe_1823K::"
    return [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(prefix)
    ]


def _mo_amount(obs) -> Decimal:
    located = obs.point_conditions["composition"]
    assert located.state.is_value
    return dict(located.state.value.components)["Mo"]


def _pair(obs) -> tuple[Decimal, Decimal]:
    assert obs.value.kind is ValueKind.POINT
    return (_mo_amount(obs), obs.value.point)


def test_ichise_table2_activities_are_points_at_printed_values(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-111-ichise-1982")
    rows = _ichise_rows(result)
    assert len(rows) == 23
    assert sorted(_pair(row) for row in rows) == sorted(_ICHISE_TABLE2)
    for row in rows:
        assert row.identity.species.formula == "Fe"
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1823")
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1823")
        assert temperature.locator is not None
        assert temperature.locator.table == "2"
        composition = row.point_conditions["composition"]
        assert composition.locator is not None
        assert composition.locator.table == "2"
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.total_pressure_Pa.reason == (
            "no total_pressure_Pa mapped from source"
        )
        assert row.value.approximate is False


_ICHISE_TABLE4_X_MO = tuple(
    Decimal(value)
    for value in ("0.0", "0.1", "0.2", "0.3", "0.35", "0.484", "0.5", "0.554", "0.81", "0.9", "1.0")
)
_ICHISE_TABLE4 = {
    ("Fe", Quantity.ACTIVITY): tuple(
        Decimal(value)
        for value in ("1.0", "0.887", "0.774", "0.685", "0.645", "0.645", "0.624", "0.551", "0.551", "0.403", "0.0")
    ),
    ("Mo", Quantity.ACTIVITY): tuple(
        Decimal(value)
        for value in ("0.0", "0.209", "0.472", "0.689", "0.757", "0.757", "0.784", "0.875", "0.875", "0.919", "1.0")
    ),
    ("Fe", Quantity.ACTIVITY_COEFFICIENT): tuple(
        Decimal(value)
        for value in ("1.0", "0.986", "0.967", "0.979", "0.993", "1.25", "1.247", "1.236", "2.9", "4.03", "6.0")
    ),
    ("Mo", Quantity.ACTIVITY_COEFFICIENT): tuple(
        Decimal(value)
        for value in ("1.54", "2.09", "2.36", "2.296", "2.163", "1.564", "1.568", "1.579", "1.08", "1.022", "1.0")
    ),
}


def test_ichise_table4_quantities_keep_values_and_located_conditions(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, "kems-111-ichise-1982")
    table4 = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "kems-111-ichise-1982"
        and observation.locator is not None
        and observation.locator.table == "4"
    ]
    assert len(table4) == 88

    for (formula, quantity), expected_values in _ICHISE_TABLE4.items():
        matching = [
            observation
            for observation in table4
            if observation.identity.species.formula == formula
            and observation.identity.quantity.is_value
            and observation.identity.quantity.value is quantity
        ]
        assert len(matching) == len(_ICHISE_TABLE4_X_MO)
        assert all(observation.value.kind is ValueKind.POINT for observation in matching)
        actual = sorted(
            (
                dict(observation.point_conditions["composition"].state.value.components)["Mo"],
                observation.value.point,
            )
            for observation in matching
        )
        expected = sorted(zip(_ICHISE_TABLE4_X_MO, expected_values))
        assert actual == expected

        reference_phase = Phase.L if formula == "Fe" else Phase.CR
        for observation in matching:
            assert observation.identity.temperature_K.is_value
            assert observation.identity.temperature_K.value == Decimal("1823")
            reference_state = observation.identity.reference_state
            assert reference_state is not None and reference_state.is_value
            assert reference_state.value.convention is ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER
            assert reference_state.value.endmember.formula == formula
            assert reference_state.value.endmember.phase.is_value
            assert reference_state.value.endmember.phase.value is reference_phase

    sigma_activity = [
        observation
        for observation in table4
        if observation.identity.species.formula == "Fe"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and dict(observation.point_conditions["composition"].state.value.components)["Mo"]
        in {Decimal("0.484"), Decimal("0.5"), Decimal("0.554")}
    ]
    assert len(sigma_activity) == 3
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        and observation.derived_from
        and observation.derivation is not None
        for observation in sigma_activity
    )

    gibbs_parents = (
        "ichise_1982_table4_GmixFe_1823K",
        "ichise_1982_table4_GexFe_1823K",
        "ichise_1982_table4_GmixMo_1823K",
        "ichise_1982_table4_GexMo_1823K",
    )
    for parent in gibbs_parents:
        unavailable = [
            observation
            for observation in table4
            if f"::{parent}::" in observation.observation_id
        ]
        assert len(unavailable) == len(_ICHISE_TABLE4_X_MO)
        assert all(observation.value.kind is ValueKind.UNAVAILABLE for observation in unavailable)

    for observation in table4:
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        composition = conditions["composition"]
        assert temperature.state.is_value and temperature.state.value == Decimal("1823")
        assert temperature.locator is not None and temperature.locator.table == "4"
        assert composition.state.is_value
        assert composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
        assert composition.locator is not None and composition.locator.table == "4"

    table4_ids = {observation.observation_id for observation in table4}
    assert result.validation is not None
    assert not any(
        any(observation_id in issue.path for observation_id in table4_ids)
        for issue in result.validation.hard_issues
    )

    table7 = [
        observation
        for observation in result.observations.values()
        if "::ichise_1982_interaction_parameters_liquid" in observation.observation_id
    ]
    assert len(table7) == 1
    assert table7[0].value.kind is ValueKind.UNAVAILABLE


def test_ichise_bare_a_fe_column_stays_unavailable_and_activity_is_the_point() -> None:
    """The reader takes `activity`. A bare a_Fe cell is not that field."""
    units = "dimensionless a_Fe"
    bare = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"no": "Fe-Mo 6", "x_Mo": "0.096", "a_Fe": "0.897"},
    )
    assert bare.value.kind is ValueKind.UNAVAILABLE
    taken = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"no": "Fe-Mo 6", "x_Mo": "0.096", "activity": "0.897"},
    )
    assert taken.value.kind is ValueKind.POINT
    assert taken.amount == Decimal("0.897")
    both = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"a_Fe": "0.897", "a_Mo": "0.2"},
    )
    assert both.value.kind is ValueKind.UNAVAILABLE
    quoted_shape = select_declared_source(
        Quantity.ACTIVITY,
        units,
        {"a_Fe": "0.865", "W_at_percent": "0.345"},
    )
    assert quoted_shape.value.kind is ValueKind.UNAVAILABLE


def test_renaming_ichise_activity_back_to_a_fe_drops_the_points(tmp_path: Path) -> None:
    path = _EXTRACTS / "kems-111-ichise-1982.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    table = next(
        obs
        for obs in doc["species"]["Fe"]["observations"]
        if obs["observation_id"] == "ichise_1982_table2_a_Fe_1823K"
    )
    for row in table["values"]["rows"]:
        row["a_Fe"] = row.pop("activity")
    result = _migrate_real_extract_doc(tmp_path, doc)
    rows = _ichise_rows(result)
    assert len(rows) == 23
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows)


def _sossi_run(result, parent: str, slug: str):
    stem = "sossi-2020-cu-zn-isotope-evap-formalism"
    needle = f"{stem}::{parent}::"
    row_bit = f"row={slug}:"
    matches = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(needle) and row_bit in oid
    ]
    assert len(matches) == 1, slug
    return matches[0]


def _printed_two_sd(obs) -> Decimal:
    assert obs.uncertainty.kind is UncertaintyKind.PRINTED
    verbatim = obs.uncertainty.verbatim
    assert isinstance(verbatim, dict)
    assert verbatim["units"] == "per mil"
    return Decimal(str(verbatim["two_sd_per_mil"]))


def test_sossi_cu_table1_isotope_deltas_are_points(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "sossi-2020-cu-zn-isotope-evap-formalism")
    parent = "sossi_2020_cu_table1_measured_runs"
    stem = "sossi-2020-cu-zn-isotope-evap-formalism"
    cu = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(f"{stem}::{parent}::")
    ]
    assert len(cu) == 34
    points = [obs for obs in cu if obs.value.kind is ValueKind.POINT]
    unavailable = [obs for obs in cu if obs.value.kind is ValueKind.UNAVAILABLE]
    assert len(points) == 22
    assert len(unavailable) == 12
    for slug, t_c, delta, two_sd in _SOSSI_CU:
        obs = _sossi_run(result, parent, slug)
        assert obs.value.kind is ValueKind.POINT
        assert obs.value.point == delta
        assert obs.value.approximate is False
        assert obs.identity.species.formula == "Cu"
        temperature = obs.point_conditions["temperature_K"]
        assert temperature.state.value == t_c + Decimal("273.15")
        assert temperature.inference is not None
        assert obs.identity.temperature_K.value == t_c + Decimal("273.15")
        assert _printed_two_sd(obs) == two_sd
    # Table 1 leaves d65Cu blank for P 21/06/18a. That blank stays unknown.
    blank = _sossi_run(result, parent, "p-21/06/18a")
    assert blank.value.kind is ValueKind.UNAVAILABLE
    # Zn rows print two isotope systems, or a contaminated d68 only.
    # Neither becomes the observation's one isotope_delta.
    zn_both = _sossi_run(result, "sossi_2020_zn_table1_measured_runs", "p-5/04/17b")
    zn_68 = _sossi_run(result, "sossi_2020_zn_table1_measured_runs", "p-01/08/18c")
    assert zn_both.value.kind is ValueKind.UNAVAILABLE
    assert zn_68.value.kind is ValueKind.UNAVAILABLE


def test_one_isotope_delta_key_is_a_point_and_two_are_not() -> None:
    one = select_declared_source(
        Quantity.ISOTOPE_DELTA,
        "permil",
        {"isotope_delta_isotope_delta": "2.15", "T_C": 1500},
    )
    assert one.value.kind is ValueKind.POINT
    assert one.amount == Decimal("2.15")
    printed_name = select_declared_source(
        Quantity.ISOTOPE_DELTA,
        "permil",
        {"delta65Cu_SM_per_mil": "2.15", "T_C": 1500},
    )
    assert printed_name.value.kind is ValueKind.UNAVAILABLE
    both = select_declared_source(
        Quantity.ISOTOPE_DELTA,
        "permil",
        {
            "isotope_delta_delta66": "5.54",
            "isotope_delta_delta68": "10.79",
        },
    )
    assert both.value.kind is ValueKind.UNAVAILABLE
    # Ion-current ratios, log gamma, and a pressure-fit dict are not points
    # under an isotope or activity read.
    ratio = select_declared_source(
        Quantity.ION_INTENSITY_RATIO,
        None,
        {"I_PbO_over_I_Pd": "1.2", "I_PO_over_I_Pd": "0.4"},
    )
    assert ratio.value.kind is ValueKind.UNAVAILABLE
    log_gamma = select_declared_source(
        Quantity.ACTIVITY_COEFFICIENT,
        None,
        {"log10_gamma_Si": "-0.3"},
    )
    assert log_gamma.value.kind is ValueKind.UNAVAILABLE


def test_renaming_sossi_isotope_field_back_drops_the_points(tmp_path: Path) -> None:
    path = _EXTRACTS / "sossi-2020-cu-zn-isotope-evap-formalism.yaml"
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    cu = [
        obs
        for obs in doc["species"]["Cu"]["observations"]
        if str(obs["observation_id"]).startswith("sossi_2020_cu_table1_measured_runs::")
    ]
    touched = 0
    for obs in cu:
        item = obs["values"]["series"][0]
        if "isotope_delta_isotope_delta" in item:
            item["delta65Cu_SM_per_mil"] = item.pop("isotope_delta_isotope_delta")
            touched += 1
    assert touched == 22
    result = _migrate_real_extract_doc(tmp_path, doc)
    stem = "sossi-2020-cu-zn-isotope-evap-formalism"
    parent = "sossi_2020_cu_table1_measured_runs"
    points = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(f"{stem}::{parent}::")
        and obs.value.kind is ValueKind.POINT
    ]
    assert points == []


def _migrate_real_extract_doc(tmp_path: Path, doc: dict):
    from simulator.battery.migrate import migrate

    root = _write_min_tree(tmp_path, doc)
    return migrate(root, write=False)
