"""Source-data pins committed before adding fit-quantity migration."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    EvidenceClass,
    MELT_ACTIVITY_QUANTITIES,
    QUANTITY_UNITS,
    Quantity,
    ValueKind,
)
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from simulator.battery.records import Value
from simulator.battery.validate import _validate_activity_coefficient_temperature_fit


ROOT = Path(__file__).resolve().parents[1]


def _sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def test_fegley_2023_table_2_printed_rows_are_pinned() -> None:
    path = ROOT / "data/literature/extracts/fegley-2023-chemical-equilibrium-calculations-bu.yaml"
    doc = yaml.safe_load(path.read_text())
    rows = doc["species"]["BSE"]["observations"][0]["values"]["rows_as_printed"]

    assert len(rows) == 82
    assert _sha256(rows) == "76e809f15c6338a5db8afb761ab8d683e627c95b85601e7ca4ece62c635af3e4"


def test_sossi_fegley_2018_table_2_printed_rows_are_pinned() -> None:
    path = ROOT / "data/literature/extracts/kems-041-sossi-fegley-2018.yaml"
    doc = yaml.safe_load(path.read_text())
    rows = [
        [
            formula,
            obs["observation_id"],
            obs.get("T_range_K"),
            obs.get("standard_state"),
            {
                key: value
                for key, value in (obs.get("values") or {}).items()
                if key != "rows"
            },
        ]
        for formula, species in doc["species"].items()
        for obs in species.get("observations", [])
        if str((obs.get("locator") or {}).get("table")) == "2"
    ]

    assert len(rows) == 31
    assert _sha256(rows) == "49112e165ef0da3f7c2aa8b0287550db0dfc3ca3b441ea36aeb6dcb5cef99df3"


def test_sossi_fegley_2018_added_numeric_rows_are_pinned() -> None:
    path = ROOT / "data/literature/extracts/kems-041-sossi-fegley-2018.yaml"
    doc = yaml.safe_load(path.read_text())
    rows = [
        {
            key: value
            for key, value in row.items()
            if key
            not in {
                "method_class",
                "evidence_model",
                "standard_state_as_printed",
                "notes_as_printed",
            }
        }
        for species in doc["species"].values()
        for obs in species.get("observations", [])
        if str((obs.get("locator") or {}).get("table")) == "2"
        for row in (obs.get("values") or {}).get("rows", [])
    ]

    assert len(rows) == 44
    assert _sha256(rows) == "6d50eef7933cbb6f6b708a8cc4383eb3a93b2e5a05136772531db1b411b75ef4"
    assert all(
        not {"standard_state_as_printed", "notes_as_printed"}.intersection(row)
        for species in doc["species"].values()
        for obs in species.get("observations", [])
        if str((obs.get("locator") or {}).get("table")) == "2"
        for row in (obs.get("values") or {}).get("rows", [])
    )


def _migrate_extract(name: str):
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(ROOT / "data/literature/extracts" / f"{name}.yaml")
    migrator.finalize()
    assert migrator.result.registry_issues == []
    return migrator.result


def _quantity(observation) -> Quantity:
    quantity = observation.identity.quantity
    return quantity.value if hasattr(quantity, "is_value") else quantity


def test_fegley_2023_table_2_migrates_all_numeric_fit_rows() -> None:
    result = _migrate_extract("fegley-2023-chemical-equilibrium-calculations-bu")
    observations = [
        observation
        for observation in result.observations.values()
        if _quantity(observation) is Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT
    ]

    assert QUANTITY_UNITS[Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT] == "dimensionless"
    fit_profile = profile_for(observations[0].identity)
    assert fit_profile.required == frozenset({"per", "reference_state"})
    assert Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT not in MELT_ACTIVITY_QUANTITIES
    assert len(observations) == 82
    assert not [
        issue
        for issue in result.validation.hard_issues
        if "fegley_2023_table_02_model" in issue.path
    ]
    assert all(observation.value.kind is ValueKind.EXPRESSION for observation in observations)
    assert all(
        set(dict(observation.value.expression_parameters or ())) == {"A", "B"}
        for observation in observations
    )

    as2o3 = next(
        observation
        for observation in observations
        if observation.identity.species.formula == "As2O3"
        and dict(observation.value.expression_parameters or ()).get("A") == Decimal("3.4014")
    )
    assert dict(as2o3.value.expression_parameters or ()) == {
        "A": Decimal("3.4014"),
        "B": Decimal("-26309.08"),
    }
    domain = json.loads(as2o3.value.expression_domain)
    assert domain["input_unit"] == "K"
    assert domain["validity_range_K"] == [1800, 2200]
    assert domain["notes_as_printed"] == "FactSage 1800−2200 K, CMAS+FeO"

    ag2o = next(
        observation
        for observation in observations
        if observation.identity.species.formula == "Ag2O"
    )
    anchor_domain = json.loads(ag2o.value.expression_domain)
    assert anchor_domain["validity_range_K"] is None
    assert anchor_domain["notes_as_printed"] == "Regular solution 1673 K point, Sossi et al. (2019)"


def test_fit_validator_rejects_non_numeric_and_non_finite_coefficients() -> None:
    domain = json.dumps(
        {
            "input_unit": "K",
            "validity_range_K": None,
            "standard_state_as_printed": "not stated in Table 2 row",
            "notes_as_printed": "",
        }
    )
    invalid_parameters = (
        (("A", Decimal("NaN")), ("B", Decimal("Infinity"))),
        (("A", "bogus"), ("B", "also bogus")),
    )

    for parameters in invalid_parameters:
        value = Value(
            ValueKind.EXPRESSION,
            expression_text="log10 γ = A + B/T",
            expression_parameters=parameters,
            expression_domain=domain,
        )
        issues = _validate_activity_coefficient_temperature_fit(value, "value")
        assert {issue.path for issue in issues} >= {
            "value.expression_parameters.A",
            "value.expression_parameters.B",
        }


def test_fit_validator_reports_malformed_temperature_range() -> None:
    value = Value(
        ValueKind.EXPRESSION,
        expression_text="log10 γ = A + B/T",
        expression_parameters=(("A", Decimal("0")), ("B", Decimal("-582"))),
        expression_domain=json.dumps(
            {
                "input_unit": "K",
                "validity_range_K": ["bad", "range"],
                "standard_state_as_printed": "not stated in Table 2 row",
                "notes_as_printed": "",
            }
        ),
    )

    issues = _validate_activity_coefficient_temperature_fit(value, "value")

    assert [issue.path for issue in issues] == [
        "value.expression_domain.validity_range_K"
    ]


def test_sossi_fegley_2018_table_2_migrates_numeric_activity_rows() -> None:
    result = _migrate_extract("kems-041-sossi-fegley-2018")
    observations = [
        observation
        for observation in result.observations.values()
        if _quantity(observation) is Quantity.ACTIVITY_COEFFICIENT
        and observation.locator is not None
        and observation.locator.table == "2"
    ]

    assert len(observations) == 44
    assert result.validation.hard_issues == ()
    assert sum(observation.value.kind is ValueKind.INTERVAL for observation in observations) == 37
    assert sum(observation.value.kind is ValueKind.POINT for observation in observations) == 4
    assert sum(observation.value.kind is ValueKind.EXPRESSION for observation in observations) == 3
    assert sum(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
        for observation in observations
    ) == 38
    assert sum(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        for observation in observations
    ) == 3
    assert sum(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        for observation in observations
    ) == 3
    for observation in observations:
        printed_row = observation.provenance[
            "activity_coefficient_table_row_as_printed"
        ]
        assert not {
            "standard_state_as_printed",
            "notes_as_printed",
            "compilation_note",
        }.intersection(printed_row)
        assert observation.identity.species.formula == printed_row["oxide"]
        assert observation.evidence.class_.is_value
        if printed_row["references_as_published"] == "Ghiorso & Sack 1995":
            assert printed_row["evidence_model"] == "MELTS"
            assert observation.evidence.model == "MELTS"
            assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        elif printed_row.get("relation_parameters"):
            assert "evidence_model" not in printed_row
            assert observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        else:
            assert "evidence_model" not in printed_row
            assert observation.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
    phosphate = next(
        observation
        for observation in observations
        if observation.provenance[
            "activity_coefficient_table_row_as_printed"
        ]["oxide"]
        == "PO2.5"
    )
    assert phosphate.identity.species.formula == "PO2.5"
    assert phosphate.value.kind is ValueKind.INTERVAL
    assert (phosphate.value.interval_low, phosphate.value.interval_high) == (
        Decimal("1e-10"),
        Decimal("1e-6"),
    )


def test_sossi_ranged_temperature_condition_output_is_pinned() -> None:
    result = _migrate_extract("kems-041-sossi-fegley-2018")
    observations = [
        observation
        for observation in result.observations.values()
        if _quantity(observation) is Quantity.ACTIVITY_COEFFICIENT
        and observation.locator is not None
        and observation.locator.table == "2"
    ]
    anorthite_diopside = next(
        observation
        for observation in observations
        if observation.provenance
        and observation.provenance.get("activity_coefficient_table_row_as_printed", {}).get(
            "oxide_formula_as_published"
        )
        == "$AlO_{1.5}$"
    )
    assert anorthite_diopside.value.interval_low == Decimal(".28")
    assert anorthite_diopside.value.interval_high == Decimal(".37")
    printed_row = anorthite_diopside.provenance[
        "activity_coefficient_table_row_as_printed"
    ]
    assert printed_row["melt_composition_as_published"] == "CMAS (An–Di)"
    assert not {
        "standard_state_as_printed",
        "notes_as_printed",
    }.intersection(printed_row)
    temperature = anorthite_diopside.point_conditions["temperature_K"].state.value
    assert (temperature.interval_low, temperature.interval_high) == (
        Decimal("1573"),
        Decimal("1773"),
    )
