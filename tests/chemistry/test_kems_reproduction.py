from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from simulator.chemistry.langmuir_knudsen import langmuir_molar_flux
from simulator.diagnostic_helpers.kems import (
    KEMSAdapter,
    KEMSSchemaError,
    apparatus_effusion_molar_rate,
    load_kems_case,
    load_kems_observations,
    validate_kems_case,
    validate_kems_observations,
)
from simulator.diagnostic_helpers.reproduction_compare import (
    COMPARISON_STATUSES,
    compare_values,
    records_to_json,
    records_to_markdown,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
KEMS_PRESETS = REPO_ROOT / "data" / "presets" / "kems"
KEMS_OBSERVATIONS = REPO_ROOT / "data" / "literature" / "kems_measurements.yaml"
VAPOR_PRESSURES = REPO_ROOT / "data" / "vapor_pressures.yaml"


@pytest.fixture(scope="module")
def observations() -> dict:
    return load_kems_observations(KEMS_OBSERVATIONS)


@pytest.fixture(scope="module")
def adapter() -> KEMSAdapter:
    data = yaml.safe_load(VAPOR_PRESSURES.read_text(encoding="utf-8"))
    return KEMSAdapter(data)


def _comparison(**overrides):
    payload = {
        "case_id": "case",
        "source_id": "source",
        "observable_id": "p_species",
        "species": "X",
        "coordinate": {"temperature_K": 1800.0},
        "expected_value": 10.0,
        "expected_uncertainty": {"kind": "absolute", "value": 1.0},
        "actual_value": 10.5,
        "units": "Pa",
        "evidence_scope": "species-resolved",
        "source_locator": {"table": "I", "page": 1},
        "recipe": {"case_id": "case"},
        "observation": {"value": 10.0},
        "runtime": {"value": 10.5},
    }
    payload.update(overrides)
    return compare_values(**payload)


def test_comparison_record_exact_chain_schema_and_reports() -> None:
    record = _comparison()
    expected_keys = {
        "case_id",
        "source_id",
        "observable_id",
        "species",
        "temperature/time/window",
        "expected_value",
        "expected_uncertainty",
        "actual_value",
        "units",
        "residual",
        "status",
        "evidence_scope",
        "source_locator",
        "recipe_digest",
        "observation_digest",
        "runtime_digest",
    }
    assert set(record.as_dict()) == expected_keys
    assert record.status == "match"
    assert record.residual == pytest.approx(0.5)
    assert set(json.loads(records_to_json([record]))[0]) == expected_keys
    markdown = records_to_markdown([record])
    assert "| case | observable | species | temperature/time/window |" in markdown
    assert "| match |" in markdown


def test_comparison_status_vocabulary_and_precedence() -> None:
    records = [
        _comparison(actual_value=13.0),
        _comparison(expected_value=None, expected_uncertainty=None),
        _comparison(unsupported_speciation=True, actual_value=None),
        _comparison(assumed_input=True),
        _comparison(out_of_domain=True, actual_value=None),
        _comparison(
            unsupported_speciation=True,
            out_of_domain=True,
            actual_value=None,
        ),
    ]
    assert [record.status for record in records] == [
        "mismatch",
        "unsupported-observable",
        "unsupported-speciation",
        "assumed-input",
        "out-of-domain",
        "out-of-domain",
    ]
    assert {record.status for record in records} | {"match"} == COMPARISON_STATUSES


@pytest.mark.parametrize("status_flags", [{}, {"assumed_input": True}])
def test_numeric_comparison_requires_cited_uncertainty(status_flags: dict) -> None:
    with pytest.raises(ValueError, match="cited uncertainty"):
        _comparison(expected_uncertainty=None, **status_flags)
    with pytest.raises(ValueError, match="uncertainty.value"):
        _comparison(expected_uncertainty={}, **status_flags)


@pytest.mark.parametrize(
    "filename, expected_case",
    [
        ("halwax_2024_cao.yaml", "halwax_2024_cao"),
        ("halwax_2024_mgo.yaml", "halwax_2024_mgo"),
    ],
)
def test_halwax_case_schema_and_observation_independence(
    filename: str,
    expected_case: str,
) -> None:
    case = load_kems_case(KEMS_PRESETS / filename)
    assert case["case_id"] == expected_case
    assert case["cell"]["material"] == "iridium"
    assert case["cell"]["orifice_diameter_m"] == pytest.approx(0.0003)
    assert case["cell"]["transmission_factor"]["status"] == "not_reported"
    assert case["cell"]["transmission_factor"]["value"] is None

    def all_keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values():
                yield from all_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from all_keys(child)

    forbidden_observation_keys = {
        "expected",
        "expected_value",
        "partial_pressure_pa",
        "ion_intensity",
        "effusion_rate_mol_s",
        "total_pressure_pa",
    }
    assert forbidden_observation_keys.isdisjoint(set(all_keys(case)))


def test_kems_case_schema_rejects_unknown_fields() -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    case["expected_pressure_pa"] = 1.0
    with pytest.raises(KEMSSchemaError, match="unknown keys"):
        validate_kems_case(case)


@pytest.mark.parametrize(
    "filename, supported_species, temperature_K, expected_pressure_pa",
    [
        (
            "halwax_2024_cao.yaml",
            "Ca",
            2077.0,
            # MC-5 Ca (2026-08-07) option B: TE gas_rail demoted; Pref_GF path.
            # SIGN CHECK: high-T monatomic Ca pressure moves UP vs TE Pref_GR pin
            # (0.00078463 -> 0.18399 Pa at 2077 K / a=1-class Halwax conditions).
            # PROVIDER-DRIFT TRIPWIRE only; not a literature re-fit.
            # docs-private/research/2026-08-07-mc5-ca-fix/report.md
            0.18399149539451548,
        ),
        (
            "halwax_2024_mgo.yaml",
            "Mg",
            1926.0,
            # b-147 Mg (2026-08-07) option B: TE gas_rail demoted; Pref_GF path.
            # SIGN CHECK: high-T monatomic Mg pressure moves UP vs TE Pref_GR pin
            # (0.21516 -> 0.81977 Pa at 1926 K / a=1-class Halwax conditions;
            # +0.5809 dex = Pref_GF-Pref_GR at 1926 K). PROVIDER-DRIFT TRIPWIRE
            # only; not a literature re-fit.
            # docs-private/research/2026-08-07-mgfix/report.md
            0.8197707668768197,
        ),
    ],
)
def test_halwax_cases_execute_with_exact_species_gaps(
    filename: str,
    supported_species: str,
    temperature_K: float,
    expected_pressure_pa: float,
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / filename)
    run = adapter.evaluate(case, observations)
    by_species = {record.species: record for record in run.records}

    assert set(by_species) == {supported_species, "O", "O2"}
    assert by_species[supported_species].actual_value is not None
    # PROVIDER-DRIFT TRIPWIRES pin current runtime output, NOT literature values.
    # Regenerate only after a reviewed provider change.
    assert by_species[supported_species].actual_value == pytest.approx(
        expected_pressure_pa,
        rel=1e-12,
    )
    assert by_species[supported_species].status == "unsupported-observable"
    assert by_species[supported_species].expected_uncertainty == {
        "temperature_K": 5.0,
        "observable_pa": None,
        "observable_status": "not_reported",
    }
    assert by_species["O"].status == "unsupported-speciation"
    assert by_species["O2"].status == "unsupported-speciation"
    assert by_species["O"].actual_value is None
    assert by_species["O2"].actual_value is None

    supported_row = next(
        row for row in run.runtime_rows if row["species"] == supported_species
    )
    assert supported_row["provider_id"] == "builtin-vapor-pressure"
    assert supported_row["provider_status"] == "ok"
    assert supported_row["temperature_K"] == temperature_K
    assert supported_row["pO2_bar"] == pytest.approx(1.0e-8)
    assert supported_row["ideal_flux_mol_m2_s"] > 0.0
    assert supported_row["apparatus_effusion_rate_mol_s"] is None
    assert "evaporation_alpha" not in supported_row


def test_kems_runtime_is_invariant_to_evaporation_alpha(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    case["cell"]["transmission_factor"] = {
        "status": "derived",
        "value": 0.4,
        "derivation": "synthetic alpha-invariance fixture",
        "source_locator": "test fixture",
    }
    low = adapter.evaluate(
        case,
        observations,
        runtime_settings={"evaporation_alpha": 0.01},
    )
    high = adapter.evaluate(
        case,
        observations,
        runtime_settings={"evaporation_alpha": 0.99},
    )

    assert [record.actual_value for record in low.records] == [
        record.actual_value for record in high.records
    ]
    assert [
        row["ideal_flux_mol_m2_s"] for row in low.runtime_rows
    ] == [row["ideal_flux_mol_m2_s"] for row in high.runtime_rows]
    assert [
        row["apparatus_effusion_rate_mol_s"] for row in low.runtime_rows
    ] == [row["apparatus_effusion_rate_mol_s"] for row in high.runtime_rows]
    assert low.runtime_rows[0]["apparatus_effusion_rate_mol_s"] is not None
    assert low.records[0].runtime_digest != high.records[0].runtime_digest


def test_observation_digest_binds_source_and_case_package(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    baseline = adapter.evaluate(case, observations)
    changed = copy.deepcopy(observations)
    source_id = changed["cases"]["halwax_2024_cao"]["source_id"]
    changed["sources"][source_id]["citation"] += " corrected citation"
    mutated = adapter.evaluate(case, changed)

    assert baseline.records[0].observation_digest != (
        mutated.records[0].observation_digest
    )


def test_numeric_assumed_observation_rejects_missing_uncertainty(
    observations: dict,
) -> None:
    changed = copy.deepcopy(observations)
    point = changed["cases"]["halwax_2024_cao"]["points"][0]
    point["partial_pressure_pa"] = 1.0
    point["status"] = "assumed"
    point["uncertainty"] = None

    with pytest.raises(KEMSSchemaError, match="cited uncertainty"):
        validate_kems_observations(changed)


def test_numeric_observation_requires_comparator_uncertainty_shape(
    observations: dict,
) -> None:
    changed = copy.deepcopy(observations)
    point = changed["cases"]["halwax_2024_cao"]["points"][0]
    point["partial_pressure_pa"] = 1.0
    point["status"] = "reported"

    with pytest.raises(KEMSSchemaError, match="requires kind and value"):
        validate_kems_observations(changed)


def test_preset_only_total_pressure_fallback_authorization_is_refused(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    selector = case["measurement_selectors"][0]
    selector["observable"] = "total_pressure_pa"
    selector["species"] = None
    selector["allow_total_pressure_fallback"] = True
    case["measurement_selectors"] = [selector]

    with pytest.raises(KEMSSchemaError, match="unknown keys"):
        adapter.evaluate(case, observations)


def test_total_pressure_fallback_rejects_truthy_string_declaration(
    observations: dict,
) -> None:
    changed = copy.deepcopy(observations)
    changed["cases"]["halwax_2024_cao"]["points"][0][
        "allow_total_pressure_fallback"
    ] = "false"

    with pytest.raises(KEMSSchemaError, match="must be boolean"):
        validate_kems_observations(changed)


def test_sidecar_authorized_total_pressure_is_fixed_weaker_evidence(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    selector = case["measurement_selectors"][0]
    selector["observable_id"] = "cao_total_pressure_fallback"
    selector["observable"] = "total_pressure_pa"
    selector["species"] = None
    selector["evidence_scope"] = "species-resolved-clean-oxide-kems"
    case["measurement_selectors"] = [selector]

    changed = copy.deepcopy(observations)
    point = changed["cases"]["halwax_2024_cao"]["points"][0]
    point["observable_id"] = "cao_total_pressure_fallback"
    point["species"] = None
    point["allow_total_pressure_fallback"] = True
    point["total_pressure_pa"] = point.pop("partial_pressure_pa")
    changed["cases"]["halwax_2024_cao"]["points"] = [point]

    record = adapter.evaluate(case, changed).records[0]
    assert record.actual_value is not None
    assert record.species is None
    assert record.evidence_scope == "total-pressure-fallback"
    assert record.status == "unsupported-observable"


def test_provider_refusal_precedes_missing_speciation(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    class RefusingProvider:
        name = "refusing-test-provider"

        def dispatch(self, request):
            self.request = request
            return SimpleNamespace(
                status="refused",
                diagnostic={},
                warnings=("synthetic refusal",),
            )

    refusing_adapter = copy.copy(adapter)
    refusing_provider = RefusingProvider()
    refusing_adapter._provider = refusing_provider
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    run = refusing_adapter.evaluate(case, observations)

    assert {record.status for record in run.records} == {"out-of-domain"}
    assert {row["provider_status"] for row in run.runtime_rows} == {"refused"}
    assert refusing_provider.request.control_inputs == {"pO2_bar": 1.0e-8}


@pytest.mark.parametrize("provider_status", ["not_converged", "unavailable"])
def test_non_ok_provider_status_cannot_match_numeric_observation(
    provider_status: str,
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    class NonOkProvider:
        name = "non-ok-test-provider"

        def dispatch(self, request):
            return SimpleNamespace(
                status=provider_status,
                diagnostic={"vapor_pressures_Pa": {"Ca": 1.0}},
                warnings=(f"synthetic {provider_status}",),
            )

    non_ok_adapter = copy.copy(adapter)
    non_ok_adapter._provider = NonOkProvider()
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    case["provider_inputs"]["status"] = "reported"
    changed = copy.deepcopy(observations)
    point = changed["cases"]["halwax_2024_cao"]["points"][0]
    point["partial_pressure_pa"] = 1.0
    point["status"] = "reported"
    point["uncertainty"] = {"kind": "absolute", "value": 0.01}

    run = non_ok_adapter.evaluate(case, changed)

    assert run.records[0].status == "out-of-domain"
    assert run.records[0].actual_value is None
    assert run.runtime_rows[0]["provider_status"] == provider_status
    assert run.runtime_rows[0]["vapor_pressures_Pa"]["Ca"] == 1.0


def test_unknown_provider_status_is_rejected(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    class UnknownStatusProvider:
        name = "unknown-status-test-provider"

        def dispatch(self, request):
            return SimpleNamespace(
                status="invented",
                diagnostic={"vapor_pressures_Pa": {"Ca": 1.0}},
                warnings=(),
            )

    unknown_adapter = copy.copy(adapter)
    unknown_adapter._provider = UnknownStatusProvider()
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")

    with pytest.raises(KEMSSchemaError, match="unsupported provider status"):
        unknown_adapter.evaluate(case, observations)


@pytest.mark.parametrize(
    "status, derivation, source_locator, error",
    [
        ("invented", None, None, "status must be"),
        ("reported", None, None, "requires source_locator"),
        ("derived", None, "test locator", "requires derivation"),
        ("reported", None, {"page": None}, "requires source_locator"),
        (
            "derived",
            {"equation": {"number": None}},
            "test locator",
            "requires derivation",
        ),
        ("reported", None, {"page": False}, "requires source_locator"),
        ("derived", {"equation": False}, "test locator", "requires derivation"),
        (
            "reported",
            None,
            {"page": {"nested": True}},
            "requires source_locator",
        ),
        (
            "derived",
            {"equation": {"number": False}},
            "test locator",
            "requires derivation",
        ),
    ],
)
def test_transmission_factor_requires_typed_provenance(
    status: str,
    derivation: object,
    source_locator: object,
    error: str,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    case["cell"]["transmission_factor"] = {
        "status": status,
        "value": 0.5,
        "derivation": derivation,
        "source_locator": source_locator,
    }

    with pytest.raises(KEMSSchemaError, match=error):
        validate_kems_case(case)


@pytest.mark.parametrize("factor", [True, np.True_])
def test_transmission_factor_rejects_boolean_values(factor: object) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    case["cell"]["transmission_factor"] = {
        "status": "reported",
        "value": factor,
        "derivation": None,
        "source_locator": "test locator",
    }

    with pytest.raises(KEMSSchemaError, match="must be numeric"):
        validate_kems_case(case)


def test_numeric_observation_must_match_selector_observable(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    case = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    changed = copy.deepcopy(observations)
    point = changed["cases"]["halwax_2024_cao"]["points"][0]
    point["effusion_rate_mol_s"] = point.pop("partial_pressure_pa")
    point["effusion_rate_mol_s"] = 1.0
    point["status"] = "reported"
    point["uncertainty"] = {"kind": "absolute", "value": 0.01}

    with pytest.raises(KEMSSchemaError, match="selector observable mismatch"):
        adapter.evaluate(case, changed)


def test_langmuir_open_surface_flux_still_scales_with_alpha() -> None:
    low = langmuir_molar_flux(
        2000.0,
        2.0,
        0.0,
        0.2,
        molar_mass_kg_mol=0.040078,
    )
    high = langmuir_molar_flux(
        2000.0,
        2.0,
        0.0,
        0.8,
        molar_mass_kg_mol=0.040078,
    )
    assert high == pytest.approx(4.0 * low)


def test_orifice_and_transmission_scale_effusion_not_equilibrium_pressure(
    observations: dict,
    adapter: KEMSAdapter,
) -> None:
    base = load_kems_case(KEMS_PRESETS / "halwax_2024_cao.yaml")
    base["cell"]["transmission_factor"] = {
        "status": "derived",
        "value": 0.4,
        "derivation": "synthetic regression fixture",
        "source_locator": "test",
    }
    doubled_area = copy.deepcopy(base)
    doubled_area["cell"]["orifice_diameter_m"] *= math.sqrt(2.0)
    doubled_area["cell"]["orifice_area_m2"] *= 2.0
    doubled_factor = copy.deepcopy(base)
    doubled_factor["cell"]["transmission_factor"]["value"] = 0.8

    run_base = adapter.evaluate(base, observations)
    run_area = adapter.evaluate(doubled_area, observations)
    run_factor = adapter.evaluate(doubled_factor, observations)
    row_base = next(row for row in run_base.runtime_rows if row["species"] == "Ca")
    row_area = next(row for row in run_area.runtime_rows if row["species"] == "Ca")
    row_factor = next(row for row in run_factor.runtime_rows if row["species"] == "Ca")

    assert row_area["vapor_pressures_Pa"] == row_base["vapor_pressures_Pa"]
    assert row_factor["vapor_pressures_Pa"] == row_base["vapor_pressures_Pa"]
    assert row_area["apparatus_effusion_rate_mol_s"] == pytest.approx(
        2.0 * row_base["apparatus_effusion_rate_mol_s"]
    )
    assert row_factor["apparatus_effusion_rate_mol_s"] == pytest.approx(
        2.0 * row_base["apparatus_effusion_rate_mol_s"]
    )

    direct = apparatus_effusion_molar_rate(
        3.0,
        orifice_area_m2=2.0e-8,
        transmission_factor=0.5,
    )
    assert direct == pytest.approx(3.0e-8)
