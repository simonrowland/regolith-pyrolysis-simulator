from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args
from unittest.mock import patch

import pytest
import yaml

from simulator import volatile_properties as runtime_properties
from simulator.volatile_properties import (
    AuthorityClass,
    CorrelationCoverageEvidence,
    CorrelationEvidence,
    NoDataReason,
    ProcessNonvolatileCoverageEvidence,
    ProcessNonvolatileEvidence,
    PropertyCoverageConflictError,
    PropertyQueryError,
    PropertyRegistryError,
    PropertyStatus,
    StaticEvidence,
    VirtualMolarMassEvidence,
    VolatilePropertyRegistry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REGISTRY_PATH = (
    ROOT / "tests" / "fixtures" / "volatile_properties" / "test_registry.yaml"
)
ANCHOR_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "literature"
    / "volatile_properties_anchors.yaml"
)


def _load_test_only_signed_registry(mapping):
    with TemporaryDirectory(
        prefix="volatile-properties-test-signed-source-"
    ) as temporary_directory:
        source_path = (
            Path(temporary_directory) / "signed-volatile-properties.json"
        ).resolve()
        source_path.write_text(json.dumps(mapping), encoding="utf-8")
        with patch.object(
            runtime_properties,
            "_SIGNED_RUNTIME_REGISTRY_PATH",
            source_path,
        ):
            return VolatilePropertyRegistry.load(source_path)


class FixtureVolatilePropertyRegistry:
    def __init__(self, test_signed_registry):
        self._test_signed_registry = test_signed_registry

    @classmethod
    def load(cls, mapping):
        return cls(_load_test_only_signed_registry(mapping))

    def property(self, *args, **kwargs):
        return self._test_signed_registry.property(*args, **kwargs)

    def property_bands(self, *args, **kwargs):
        return self._test_signed_registry.property_bands(*args, **kwargs)

    def equilibrium_pressure_bands(self, *args, **kwargs):
        return self._test_signed_registry.equilibrium_pressure_bands(
            *args, **kwargs
        )

    def condensation_T(self, *args, **kwargs):
        return self._test_signed_registry.condensation_T(*args, **kwargs)


@pytest.fixture
def fixture_mapping() -> dict:
    return yaml.safe_load(FIXTURE_REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def registry(fixture_mapping) -> FixtureVolatilePropertyRegistry:
    return FixtureVolatilePropertyRegistry.load(fixture_mapping)


def _load_fixture_mapping(mapping: dict) -> FixtureVolatilePropertyRegistry:
    return FixtureVolatilePropertyRegistry.load(mapping)


def _fixture_source(reference_id: str = "fixture-physical-declaration") -> dict:
    return {
        "reference_id": reference_id,
        "citation": "Fixture-only source used to exercise typed registry machinery.",
        "doi": None,
        "url": None,
        "locator": "test fixture",
    }


def _nonvolatile_row(
    *,
    species: str = "CO2",
    context: str = "fixture_no_release",
    bounds: tuple[float, float] = (90.0, 110.0),
    inclusive: tuple[bool, bool] = (True, True),
    row_id: str = "fixture:nonvolatile:CO2:no-release",
) -> dict:
    return {
        "row_kind": "process_nonvolatile",
        "row_id": row_id,
        "species": species,
        "process_context": context,
        "valid_process_range_K": list(bounds),
        "valid_process_range_inclusive": list(inclusive),
        "criterion": "Fixture context declares no upstream volatilized gas supply.",
        "source": _fixture_source(),
        "authority_class": "certified",
    }


def test_runtime_registry_is_machinery_only_and_molar_mass_remains_virtual():
    pressure = runtime_properties.property(
        "H2O",
        "sublimation_pressure",
        T_K=200.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert pressure.status is PropertyStatus.NO_DATA
    assert pressure.reason is NoDataReason.NO_CERTIFIED_ROW

    molar_mass = runtime_properties.property("H2O", "molar_mass")
    assert molar_mass.status is PropertyStatus.VALUE
    assert isinstance(molar_mass.evidence, VirtualMolarMassEvidence)
    assert molar_mass.evidence.value == pytest.approx(18.015, abs=1.0e-12)


def test_mapping_cannot_mint_certified_authority(fixture_mapping):
    assert get_args(AuthorityClass) == ("certified",)
    registry = VolatilePropertyRegistry.load(fixture_mapping)
    row_id = (
        "fixture:H2O:sublimation_pressure:solid_Ih:feistel-wagner-2007"
    )
    assert row_id in registry._noncertified_row_ids

    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_subclass_parser_cannot_mint_certified_authority(fixture_mapping):
    class ForgingVolatilePropertyRegistry(VolatilePropertyRegistry):
        @staticmethod
        def _parse_correlation(row, **kwargs):
            parsed = VolatilePropertyRegistry._parse_correlation(
                row,
                **kwargs,
            )
            assert "authority_class" not in parsed.__dataclass_fields__
            return replace(
                parsed,
                _declared_authority_class="certified",
                _source_token=runtime_properties._RegistrySourceToken(
                    runtime_properties._RegistrySourceClass.SIGNED_RUNTIME
                ),
            )

    registry = ForgingVolatilePropertyRegistry.load(fixture_mapping)
    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )

    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_fresh_signed_source_token_cannot_mint_certified_authority(
    fixture_mapping,
):
    signed_registry = _load_test_only_signed_registry(fixture_mapping)
    fresh_token = runtime_properties._RegistrySourceToken(
        runtime_properties._RegistrySourceClass.SIGNED_RUNTIME
    )
    assert fresh_token is not runtime_properties._SIGNED_RUNTIME_CAPABILITY

    registry = object.__new__(VolatilePropertyRegistry)
    VolatilePropertyRegistry._materialize(
        registry,
        correlations=signed_registry._correlations,
        static_rows=signed_registry._static_rows,
        nonvolatile_rows=signed_registry._nonvolatile_rows,
        routes=signed_registry._routes,
        formula_registry=signed_registry._formula_registry,
        formula_texts=signed_registry._formula_texts,
        aliases=signed_registry._aliases,
        source_token=fresh_token,
    )
    row_id = (
        "fixture:H2O:sublimation_pressure:solid_Ih:feistel-wagner-2007"
    )
    assert row_id in registry._noncertified_row_ids

    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_registry_refuses_post_init_assignment_and_rematerialization(
    fixture_mapping,
):
    class InterceptingVolatilePropertyRegistry(VolatilePropertyRegistry):
        def __setattr__(self, name, value):
            if name == "_correlations":
                object.__setattr__(self, "_intercepted_correlations", value)
            super().__setattr__(name, value)

    mapping_registry = InterceptingVolatilePropertyRegistry.load(fixture_mapping)
    assert not hasattr(mapping_registry, "_intercepted_correlations")

    registry = _load_test_only_signed_registry(fixture_mapping)
    original_correlations = registry._correlations

    with pytest.raises(AttributeError, match="immutable after construction"):
        registry._correlations = ()
    with pytest.raises(AttributeError, match="immutable after construction"):
        VolatilePropertyRegistry._materialize(
            registry,
            correlations=(),
            static_rows=(),
            nonvolatile_rows=(),
            routes=(),
            formula_registry={},
            formula_texts={},
            aliases={},
            source_token=runtime_properties._RegistrySourceToken(
                runtime_properties._RegistrySourceClass.SIGNED_RUNTIME
            ),
        )

    assert registry._correlations is original_correlations
    copied_registry = copy.copy(registry)
    with pytest.raises(AttributeError, match="immutable after construction"):
        copied_registry._correlations = ()


def test_direct_constructor_cannot_retain_certified_authority(fixture_mapping):
    signed_registry = _load_test_only_signed_registry(fixture_mapping)
    row_id = (
        "fixture:H2O:sublimation_pressure:solid_Ih:feistel-wagner-2007"
    )
    assert (
        signed_registry._correlations_by_id[row_id].authority_class
        == "certified"
    )

    registry = VolatilePropertyRegistry(
        correlations=signed_registry._correlations,
        static_rows=signed_registry._static_rows,
        nonvolatile_rows=signed_registry._nonvolatile_rows,
        routes=signed_registry._routes,
        formula_registry=signed_registry._formula_registry,
        formula_texts=signed_registry._formula_texts,
        aliases=signed_registry._aliases,
    )
    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )

    assert row_id in registry._noncertified_row_ids
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_ad_hoc_path_cannot_mint_certified_authority(
    fixture_mapping,
    tmp_path,
):
    source_path = tmp_path / "ad-hoc-volatile-properties.json"
    source_path.write_text(json.dumps(fixture_mapping), encoding="utf-8")
    registry = VolatilePropertyRegistry.load(source_path)

    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_exact_test_fixture_authority_downgrades_test_signed_source(
    fixture_mapping,
):
    row = fixture_mapping["species"]["H2O"]["correlations"][0]
    row["authority_class"] = "test-fixture"
    registry = _load_test_only_signed_registry(fixture_mapping)

    assert row["row_id"] in registry._noncertified_row_ids
    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.NO_CERTIFIED_ROW


def test_test_only_signed_source_exercises_certified_path(fixture_mapping):
    registry = _load_test_only_signed_registry(fixture_mapping)
    result = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=230.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.VALUE
    assert isinstance(result.evidence, CorrelationEvidence)
    assert result.evidence.authority_class == "certified"


def test_public_loader_rejects_test_fixture_paths():
    with pytest.raises(PropertyRegistryError) as error:
        VolatilePropertyRegistry.load(FIXTURE_REGISTRY_PATH)
    assert error.value.code == "registry_source_forbidden"


def test_literature_pressure_anchors_are_independent_sidecar_values(registry):
    anchors = yaml.safe_load(ANCHOR_PATH.read_text(encoding="utf-8"))
    for anchor in anchors["pressure_anchors"]:
        result = registry.property(
            anchor["species"],
            anchor["property_kind"],
            T_K=anchor["T_K"],
            phase_branch=anchor["phase_branch"],
            pressure_kind=anchor["pressure_kind"],
        )
        assert result.status is PropertyStatus.VALUE
        assert isinstance(result.evidence, CorrelationEvidence)
        assert result.evidence.authority_class == "certified"
        assert result.evidence.row_id == anchor["row_id"]
        assert (
            result.evidence.source_reference_id
            != anchor["source"]["reference_id"]
        )
        relative_error = abs(
            result.evidence.value - anchor["expected_P_Pa"]
        ) / anchor["expected_P_Pa"]
        assert relative_error <= anchor["relative_tolerance"]


def test_molar_mass_anchors_use_existing_atomic_weight_authority(registry):
    anchors = yaml.safe_load(ANCHOR_PATH.read_text(encoding="utf-8"))
    for anchor in anchors["molar_mass_anchors"]:
        result = registry.property(anchor["species"], "molar_mass")
        assert result.status is PropertyStatus.VALUE
        assert isinstance(result.evidence, VirtualMolarMassEvidence)
        assert result.evidence.value == pytest.approx(
            anchor["expected_g_mol"],
            abs=anchor["absolute_tolerance_g_mol"],
        )
        assert result.evidence.resolver == "resolve_species_formula"
        assert "CIAAW/NIST" in result.evidence.atomic_weight_authority


def test_catalog_alias_canonicalizes_without_phase_suffix_inference(registry):
    result = registry.property("H2O_kg_per_tonne", "molar_mass")
    assert result.status is PropertyStatus.VALUE
    assert isinstance(result.evidence, VirtualMolarMassEvidence)
    assert result.evidence.species == "H2O"

    no_pseudo_species = registry.property("H2O_ice", "molar_mass")
    assert no_pseudo_species.status is PropertyStatus.NO_DATA
    assert no_pseudo_species.reason is NoDataReason.NO_CERTIFIED_ROW

    no_phase_suffix = registry.property("H2O(g)", "molar_mass")
    assert no_phase_suffix.status is PropertyStatus.NO_DATA
    assert no_phase_suffix.reason is NoDataReason.NO_CERTIFIED_ROW

    catalog_formula = registry.property("sulfuric_acid_feedstock", "molar_mass")
    assert isinstance(catalog_formula.evidence, VirtualMolarMassEvidence)
    assert catalog_formula.evidence.formula == "H2SO4"


def test_static_properties_are_point_only_and_have_typed_null_tolerances(registry):
    result = registry.property(
        "H2O",
        "triple_point_temperature",
        phase_branch="solid_Ih__liquid__vapor",
        pressure_kind="not_applicable",
    )
    assert result.status is PropertyStatus.VALUE
    assert isinstance(result.evidence, StaticEvidence)
    assert result.evidence.value == 273.16
    assert result.evidence.evaluation_tolerance_relative is None
    assert result.evidence.evaluation_tolerance_absolute is None
    assert result.evidence.evaluation_tolerance_absolute_units is None

    with pytest.raises(PropertyQueryError) as error:
        registry.property_bands(
            "H2O",
            "triple_point_temperature",
            (200.0, 250.0),
            phase_branch="solid_Ih__liquid__vapor",
            pressure_kind="not_applicable",
        )
    assert error.value.code == "unsupported_band_query"


def test_fray_schmitt_bar_to_pascal_conversion_is_explicit(registry):
    temperature = 216.58
    expected = 1.0e5 * math.exp(
        18.61 - 4154.0 / temperature + 104100.0 / temperature**2
    )
    result = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=temperature,
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert isinstance(result.evidence, CorrelationEvidence)
    assert result.evidence.value == pytest.approx(expected, rel=1.0e-12)


def test_direct_bands_preserve_endpoint_ownership(registry):
    bands = registry.property_bands(
        "CO2",
        "sublimation_pressure",
        (40.0, 216.58),
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert [(band.temperature_range_K, band.range_inclusive) for band in bands] == [
        ((40.0, 194.7), (True, False)),
        ((194.7, 216.58), (True, True)),
    ]
    assert all(band.status is PropertyStatus.VALUE for band in bands)
    for band in bands:
        assert isinstance(band.evidence, CorrelationCoverageEvidence)
        assert (
            band.evidence.queried_temperature_range_K
            == band.temperature_range_K
        )
        assert (
            band.evidence.queried_temperature_range_inclusive
            == band.range_inclusive
        )
        assert band.evidence.equilibrium_route_id is None


def test_direct_band_query_interval_is_closed(registry):
    bands = registry.property_bands(
        "CO2",
        "sublimation_pressure",
        (40.0, 194.7),
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert [
        (band.temperature_range_K, band.range_inclusive) for band in bands
    ] == [
        ((40.0, 194.7), (True, False)),
        ((194.7, 194.7), (True, True)),
    ]


def test_equilibrium_bands_and_point_repeat_route_authorization(registry):
    bands = registry.equilibrium_pressure_bands(
        "CO2", (40.0, 216.58), process_context="C0_cryo_train"
    )
    assert len(bands) == 2
    for index, band in enumerate(bands):
        assert isinstance(band.evidence, CorrelationCoverageEvidence)
        assert (
            band.evidence.equilibrium_route_id
            == "fixture:equilibrium:CO2:C0_cryo_train"
        )
        assert band.evidence.equilibrium_route_segment_index == index
        assert band.evidence.equilibrium_process_context == "C0_cryo_train"

    point = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=200.0,
        phase_branch="solid",
        pressure_kind="saturation",
        process_context="C0_cryo_train",
    )
    assert isinstance(point.evidence, CorrelationEvidence)
    assert (
        point.evidence.equilibrium_route_id
        == "fixture:equilibrium:CO2:C0_cryo_train"
    )
    assert point.evidence.equilibrium_route_segment_index == 1
    assert point.evidence.equilibrium_process_context == "C0_cryo_train"


def test_both_exclusive_route_boundary_is_an_explicit_zero_width_gap(
    fixture_mapping,
):
    route = fixture_mapping["equilibrium_routes"][1]
    route["segments"][1]["range_inclusive"][0] = False
    registry = _load_fixture_mapping(fixture_mapping)
    bands = registry.equilibrium_pressure_bands(
        "CO2", (190.0, 200.0), process_context="C0_cryo_train"
    )
    assert len(bands) == 3
    gap = bands[1]
    assert gap.temperature_range_K == (194.7, 194.7)
    assert gap.range_inclusive == (True, True)
    assert gap.status is PropertyStatus.NO_DATA
    assert gap.reason is NoDataReason.COVERAGE_GAP
    assert gap.evidence is None


def test_both_inclusive_correlation_boundary_fails_registry_load(fixture_mapping):
    fixture_mapping["species"]["CO2"]["correlations"][0][
        "valid_range_inclusive"
    ][1] = True
    with pytest.raises(PropertyCoverageConflictError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.row_ids == (
        "fixture:CO2:sublimation_pressure:solid:fray-schmitt-2009:seg-1",
        "fixture:CO2:sublimation_pressure:solid:fray-schmitt-2009:seg-2",
    )


def test_nonvolatile_is_first_class_and_never_inferred_from_absence(
    fixture_mapping,
):
    fixture_mapping["nonvolatile_by_physics"].append(_nonvolatile_row())
    registry = _load_fixture_mapping(fixture_mapping)
    bands = registry.equilibrium_pressure_bands(
        "CO2", (80.0, 120.0), process_context="fixture_no_release"
    )
    assert [band.status for band in bands] == [
        PropertyStatus.NO_DATA,
        PropertyStatus.NONVOLATILE_BY_PHYSICS,
        PropertyStatus.NO_DATA,
    ]
    middle = bands[1]
    assert isinstance(
        middle.evidence, ProcessNonvolatileCoverageEvidence
    )
    assert middle.evidence.valid_process_range_inclusive == (True, True)

    point = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=100.0,
        phase_branch="solid",
        pressure_kind="saturation",
        process_context="fixture_no_release",
    )
    assert point.status is PropertyStatus.NONVOLATILE_BY_PHYSICS
    assert isinstance(point.evidence, ProcessNonvolatileEvidence)
    assert point.evidence.queried_T_K == 100.0

    absent_context = registry.equilibrium_pressure_bands(
        "CO2", (80.0, 120.0), process_context="not_signed"
    )
    assert all(band.status is PropertyStatus.NO_DATA for band in absent_context)
    assert all(
        band.reason is NoDataReason.PROCESS_CONTEXT_MISMATCH
        for band in absent_context
    )


def test_route_nonvolatile_overlap_fails_loudly(fixture_mapping):
    fixture_mapping["nonvolatile_by_physics"].append(
        _nonvolatile_row(
            context="C0_cryo_train",
            bounds=(100.0, 110.0),
            row_id="fixture:nonvolatile:CO2:overlap",
        )
    )
    with pytest.raises(PropertyCoverageConflictError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.row_ids == (
        "fixture:CO2:sublimation_pressure:solid:fray-schmitt-2009:seg-1",
        "fixture:nonvolatile:CO2:overlap",
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_same_rail_overlap_is_order_independent(fixture_mapping, reverse):
    rows = fixture_mapping["species"]["H2O"]["correlations"]
    duplicate = copy.deepcopy(rows[0])
    duplicate["row_id"] = "fixture:H2O:sublimation_pressure:overlap"
    duplicate["valid_range_K"] = [250.0, 260.0]
    rows.append(duplicate)
    if reverse:
        rows.reverse()
    with pytest.raises(PropertyCoverageConflictError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.row_ids == (
        "fixture:H2O:sublimation_pressure:overlap",
        "fixture:H2O:sublimation_pressure:solid_Ih:feistel-wagner-2007",
    )


def test_cross_registry_legacy_overlap_fails_loudly(fixture_mapping):
    fixture_mapping["species"]["Na"] = {
        "formula": "Na",
        "correlations": [
            {
                "row_kind": "correlation",
                "row_id": "fixture:Na:saturation_pressure:direct",
                "property_kind": "saturation_pressure",
                "correlation_family": "antoine",
                "coefficients": {
                    "A": 7.460770,
                    "B": 1873.728,
                    "C": -416.372,
                },
                "phase_branch": "liquid",
                "pressure_kind": "saturation",
                "valid_range_K": [924.0, 1118.0],
                "valid_range_inclusive": [True, True],
                "output_units": "Pa",
                "source": _fixture_source("fixture-na-direct"),
                "evaluation_tolerance": {
                    "relative": 1.0e-12,
                    "absolute_Pa": 1.0e-12,
                },
                "authority_class": "certified",
            }
        ],
    }
    fixture_mapping["legacy_adapter_rows"].append(
        {
            "adapter_id": "fixture:legacy:Na:overlap",
            "source_path": "data/vapor_pressures.yaml",
            "source_selector": "metals.Na.pure_component_antoine",
            "species": "Na",
            "property_kind": "saturation_pressure",
            "phase_branch": "liquid",
            "pressure_kind": "saturation",
        }
    )
    with pytest.raises(PropertyCoverageConflictError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.row_ids == (
        "fixture:Na:saturation_pressure:direct",
        "fixture:legacy:Na:overlap",
    )


def test_pressure_and_phase_kinds_never_substitute(registry):
    pressure_mismatch = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=200.0,
        phase_branch="solid",
        pressure_kind="monomer_partial",
    )
    assert pressure_mismatch.reason is NoDataReason.PRESSURE_KIND_MISMATCH

    phase_mismatch = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=200.0,
        phase_branch="liquid",
        pressure_kind="saturation",
    )
    assert phase_mismatch.reason is NoDataReason.PHASE_MISMATCH

    wrong_property = registry.property(
        "CO2",
        "saturation_pressure",
        T_K=200.0,
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert wrong_property.reason is NoDataReason.NO_CERTIFIED_ROW


def test_monomer_partial_row_cannot_satisfy_saturation_query(fixture_mapping):
    monomer = copy.deepcopy(
        fixture_mapping["species"]["H2O"]["correlations"][0]
    )
    monomer["row_id"] = "fixture:H2O:saturation_pressure:monomer-only"
    monomer["property_kind"] = "saturation_pressure"
    monomer["pressure_kind"] = "monomer_partial"
    monomer["valid_range_K"] = [100.0, 150.0]
    fixture_mapping["species"]["H2O"]["correlations"].append(monomer)
    registry = _load_fixture_mapping(fixture_mapping)

    result = registry.property(
        "H2O",
        "saturation_pressure",
        T_K=120.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert result.status is PropertyStatus.NO_DATA
    assert result.reason is NoDataReason.PRESSURE_KIND_MISMATCH


def test_no_extrapolation_gap_and_unknown_species_have_distinct_refusals(registry):
    outside = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=300.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert outside.reason is NoDataReason.OUT_OF_CERTIFIED_RANGE

    unknown = registry.property(
        "XeF99",
        "sublimation_pressure",
        T_K=100.0,
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert unknown.reason is NoDataReason.NO_CERTIFIED_ROW


def test_inverse_round_trip_and_out_of_range_refusal(registry):
    forward = registry.property(
        "H2O",
        "sublimation_pressure",
        T_K=200.0,
        phase_branch="solid_Ih",
        pressure_kind="saturation",
    )
    assert isinstance(forward.evidence, CorrelationEvidence)
    inverse = registry.condensation_T(
        "H2O",
        forward.evidence.value,
        kind="sublimation_pressure",
        phase_branch="solid_Ih",
    )
    assert inverse.status is PropertyStatus.VALUE
    assert isinstance(inverse.evidence, CorrelationEvidence)
    assert inverse.evidence.queried_P_Pa == forward.evidence.value
    assert inverse.evidence.value == pytest.approx(200.0, abs=1.0e-10)
    assert inverse.evidence.units == "K"

    refused = registry.condensation_T(
        "H2O",
        1.0e9,
        kind="sublimation_pressure",
        phase_branch="solid_Ih",
    )
    assert refused.reason is NoDataReason.INVERSE_PRESSURE_OUT_OF_RANGE


@pytest.mark.parametrize("temperature", [100.0, 200.0])
def test_inverse_round_trip_across_piecewise_co2_branches(registry, temperature):
    forward = registry.property(
        "CO2",
        "sublimation_pressure",
        T_K=temperature,
        phase_branch="solid",
        pressure_kind="saturation",
    )
    assert isinstance(forward.evidence, CorrelationEvidence)
    inverse = registry.condensation_T(
        "CO2",
        forward.evidence.value,
        kind="sublimation_pressure",
        phase_branch="solid",
    )
    assert inverse.status is PropertyStatus.VALUE
    assert isinstance(inverse.evidence, CorrelationEvidence)
    assert inverse.evidence.value == pytest.approx(temperature, abs=1.0e-10)


@pytest.mark.parametrize(
    "future_kind",
    ["latent_heat", "heat_capacity", "flammability_index"],
)
def test_future_property_slots_refuse_until_the_design_signs_them(
    registry, future_kind
):
    with pytest.raises(PropertyQueryError, match="unsupported property kind") as exc:
        registry.property("H2O", future_kind)
    assert exc.value.code == "unsupported_property_kind"


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                T_K=float("nan"),
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "nonfinite_temperature",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                T_K=0.0,
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "nonpositive_temperature",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                P_Pa=float("inf"),
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "nonfinite_pressure",
        ),
        (
            lambda registry: registry.condensation_T(
                "H2O",
                0.0,
                kind="sublimation_pressure",
                phase_branch="solid_Ih",
            ),
            "nonpositive_pressure",
        ),
        (
            lambda registry: registry.property_bands(
                "H2O",
                "sublimation_pressure",
                (200.0, 200.0),
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "invalid_temperature_range",
        ),
        (
            lambda registry: registry.property("H2O", "latent_heat"),
            "unsupported_property_kind",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                T_K=200.0,
                pressure_kind="saturation",
            ),
            "missing_selector",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                T_K=200.0,
                phase_branch="solid_Ih",
                pressure_kind="partial-ish",
            ),
            "unknown_selector",
        ),
        (
            lambda registry: registry.property_bands(
                "H2O",
                "melting_point",
                (200.0, 250.0),
                phase_branch="solid_Ih",
                pressure_kind="not_applicable",
            ),
            "unsupported_band_query",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "missing_independent_variable",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                T_K=200.0,
                P_Pa=1.0,
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "ambiguous_independent_variable",
        ),
        (
            lambda registry: registry.property(
                "H2O",
                "sublimation_pressure",
                P_Pa=1.0,
                phase_branch="solid_Ih",
                pressure_kind="saturation",
            ),
            "forbidden_independent_variable",
        ),
        (
            lambda registry: registry.property(
                "H2O", "molar_mass", phase_branch="gas"
            ),
            "selector_not_applicable",
        ),
        (
            lambda registry: registry.equilibrium_pressure_bands(
                "H2O", (100.0, 200.0), process_context=""
            ),
            "missing_process_context",
        ),
    ],
)
def test_query_validation_codes_are_closed(registry, call, code):
    with pytest.raises(PropertyQueryError) as error:
        call(registry)
    assert error.value.code == code


@pytest.mark.parametrize(
    "call",
    [
        lambda registry: registry.property(
            "H2O",
            "sublimation_pressure",
            T_K=200.0,
            phase_branch="solid_Ih",
            pressure_kind="saturation",
            process_context=123,
        ),
        lambda registry: registry.property_bands(
            "H2O",
            "sublimation_pressure",
            (190.0, 210.0),
            phase_branch="solid_Ih",
            pressure_kind="saturation",
            process_context=123,
        ),
        lambda registry: registry.condensation_T(
            "H2O",
            1.0,
            kind="sublimation_pressure",
            phase_branch="solid_Ih",
            process_context=123,
        ),
    ],
)
def test_non_string_process_context_uses_closed_typed_error(registry, call):
    with pytest.raises(PropertyQueryError) as error:
        call(registry)
    assert error.value.code == "missing_process_context"


def test_unknown_family_and_runtime_anchor_fail_registry_load(fixture_mapping):
    unknown_family = copy.deepcopy(fixture_mapping)
    unknown_family["species"]["H2O"]["correlations"][0][
        "correlation_family"
    ] = "generic_plugin"
    with pytest.raises(PropertyRegistryError) as family_error:
        _load_fixture_mapping(unknown_family)
    assert family_error.value.code == "unknown_correlation_family"

    anchor_leak = copy.deepcopy(fixture_mapping)
    anchor_leak["species"]["H2O"]["correlations"][0]["anchor_check"] = {
        "expected_P_Pa": 611.6577
    }
    with pytest.raises(PropertyRegistryError) as anchor_error:
        _load_fixture_mapping(anchor_leak)
    assert anchor_error.value.code == "runtime_anchor_forbidden"


def test_malformed_correlation_fails_with_typed_registry_error(fixture_mapping):
    row = fixture_mapping["species"]["H2O"]["correlations"][0]
    row["correlation_family"] = "antoine"
    row["coefficients"] = {"A": 1.0, "B": 1.0, "C": -100.0}
    row["valid_range_K"] = [90.0, 110.0]
    with pytest.raises(PropertyRegistryError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.code == "correlation_evaluation_failed"


def test_nonmonotonic_pocket_between_former_samples_fails_registry_load(
    fixture_mapping,
):
    row = fixture_mapping["species"]["CO2"]["correlations"][0]
    row["coefficients"]["A"] = [
        923.7079109855493,
        -279047.13464708475,
        27948252.000661805,
        -933061767.2607843,
    ]
    row["valid_range_K"] = [90.0, 110.0]
    with pytest.raises(PropertyRegistryError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.code == "nonmonotonic_correlation"


def test_production_legacy_adapter_is_pinned_to_runtime_sidecar():
    mapping = {
        "schema_version": "volatile-properties-v1",
        "units": {
            "temperature": "K",
            "pressure": "Pa",
            "molar_mass": "g/mol",
        },
        "species": {},
        "equilibrium_routes": [],
        "legacy_adapter_rows": [
            {
                "adapter_id": "fixture:legacy:H2O:forbidden-path",
                "source_path": (
                    "tests/fixtures/volatile_properties/"
                    "legacy_pure_component.yaml"
                ),
                "source_selector": "fixture.h2o_overlap",
                "species": "H2O",
                "property_kind": "sublimation_pressure",
                "phase_branch": "solid_Ih",
                "pressure_kind": "saturation",
            }
        ],
        "nonvolatile_by_physics": [],
    }
    with pytest.raises(PropertyRegistryError) as error:
        VolatilePropertyRegistry.load(mapping)
    assert error.value.code == "legacy_source_forbidden"


def test_yaml_local_alias_is_forbidden(fixture_mapping):
    fixture_mapping["species"]["H2O_kg_per_tonne"] = fixture_mapping[
        "species"
    ].pop("H2O")
    with pytest.raises(PropertyRegistryError) as error:
        _load_fixture_mapping(fixture_mapping)
    assert error.value.code == "yaml_local_alias_forbidden"


def test_consumer_contract_round_trips_ordered_bands_with_no_data(
    fixture_mapping,
):
    route = fixture_mapping["equilibrium_routes"][1]
    route["segments"][1]["range_inclusive"][0] = False
    registry = _load_fixture_mapping(fixture_mapping)
    bands = registry.equilibrium_pressure_bands(
        "CO2", (190.0, 200.0), process_context="C0_cryo_train"
    )
    wire = json.loads(json.dumps([asdict(band) for band in bands]))
    assert [entry["status"] for entry in wire] == ["value", "no_data", "value"]
    assert wire[1]["reason"] == "coverage_gap"
    assert wire[1]["evidence"] is None
    assert (
        wire[2]["evidence"]["equilibrium_route_id"]
        == "fixture:equilibrium:CO2:C0_cryo_train"
    )


def test_runtime_module_cannot_import_literature_fixture():
    source = (ROOT / "simulator" / "volatile_properties.py").read_text(
        encoding="utf-8"
    )
    assert "volatile_properties_anchors" not in source
    assert "tests.fixtures" not in source
    assert "tests/fixtures/literature" not in source
