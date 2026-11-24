from __future__ import annotations

from types import SimpleNamespace

import pytest

import simulator.core as core_module
import simulator.lab_geometry as lab_geometry
from simulator.core import PyrolysisSimulator
from simulator.lab_geometry import (
    GramLabMeltAreaResolution,
    LabGeometryError,
    parse_lab_geometry,
    resolve_gram_lab_exposed_melt_area,
)


@pytest.mark.parametrize(
    ("sample", "reason"),
    [
        (
            {"exposed_melt_area_basis": "printed"},
            "lab_geometry_exposed_melt_area_basis_without_value",
        ),
        (
            {
                "exposed_melt_area_m2": 0.000123,
                "exposed_melt_area_basis": "unknown",
                "exposed_melt_area_source_ref": "source",
            },
            "invalid_lab_geometry_exposed_melt_area_basis",
        ),
        (
            {"exposed_melt_area_m2": 0.000123},
            "missing_lab_geometry_exposed_melt_area_locator",
        ),
    ],
    ids=["basis-without-area", "unknown-basis", "missing-locator"],
)
def test_area_provenance_gaps_default_to_sized_face(sample, reason) -> None:
    bridge = lab_geometry.gram_lab_exposed_melt_area_bridge(
        {"scale": "gram_lab", "sample": sample},
        crucible_face_area_m2=0.0002,
    )
    assert bridge["effective_exposed_area_m2"] == pytest.approx(0.0002)
    notice = bridge["notices"][0]
    assert notice["code"] == "knob_defaulted"
    assert notice["value"] == pytest.approx(0.0002)
    assert notice["original_reason"] == reason


@pytest.mark.parametrize(
    ("sample", "old_reason"),
    [
        (
            {"exposed_melt_area_basis": "printed"},
            "lab_geometry_exposed_melt_area_basis_without_value",
        ),
        (
            {
                "exposed_melt_area_m2": 0.000123,
                "exposed_melt_area_basis": "unknown",
                "exposed_melt_area_source_ref": "source",
            },
            "invalid_lab_geometry_exposed_melt_area_basis",
        ),
        (
            {"exposed_melt_area_m2": 0.000123},
            "missing_lab_geometry_exposed_melt_area_locator",
        ),
    ],
    ids=["basis-without-area", "unknown-basis", "missing-locator"],
)
def test_probe_restored_area_refusal_breaks_sized_face_contract(
    monkeypatch: pytest.MonkeyPatch,
    sample,
    old_reason: str,
) -> None:
    geometry = {"scale": "gram_lab", "sample": sample}

    def old_refusal(*args, **kwargs):
        raise LabGeometryError(old_reason, "restored pre-d460 refusal")

    def contract() -> None:
        try:
            bridge = lab_geometry.gram_lab_exposed_melt_area_bridge(
                geometry,
                crucible_face_area_m2=0.0002,
            )
        except LabGeometryError as exc:
            assert False, str(exc)
        assert bridge["effective_exposed_area_m2"] == pytest.approx(0.0002)
        assert bridge["notices"][0]["original_reason"]
        assert bridge["notices"][0]["value"] == pytest.approx(0.0002)

    monkeypatch.setattr(
        lab_geometry,
        "resolve_gram_lab_exposed_melt_area",
        old_refusal,
    )
    with pytest.raises(AssertionError):
        contract()


@pytest.mark.parametrize("bad_area", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_numeric_area_remains_category_a(bad_area: float) -> None:
    with pytest.raises(LabGeometryError) as caught:
        resolve_gram_lab_exposed_melt_area(
            {
                "scale": "gram_lab",
                "sample": {
                    "exposed_melt_area_m2": bad_area,
                    "exposed_melt_area_basis": "unknown",
                },
            },
            crucible_face_area_m2=0.0002,
        )
    assert caught.value.code == "invalid_lab_geometry_positive_value"


def test_probe_permissive_area_mutation_breaks_category_a_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {
            "exposed_melt_area_m2": 0,
            "exposed_melt_area_basis": "unknown",
        },
    }

    def permissive_area(*args, **kwargs):
        return GramLabMeltAreaResolution(0.0002, defaulted=True)

    def category_a_contract() -> None:
        try:
            lab_geometry.gram_lab_exposed_melt_area_bridge(
                geometry,
                crucible_face_area_m2=0.0002,
            )
        except LabGeometryError as exc:
            assert exc.code == "invalid_lab_geometry_positive_value"
            return
        assert False, "invalid numeric area was defaulted to the sized face"

    monkeypatch.setattr(
        lab_geometry,
        "resolve_gram_lab_exposed_melt_area",
        permissive_area,
    )
    with pytest.raises(AssertionError):
        category_a_contract()


def _profile_geometry() -> dict:
    return {
        "scale": "gram_lab",
        "equipment_sizing": "lab_fixed_geometry",
        "surfaces": [
            {
                "id": "holder",
                "role": "holder",
                "area_m2": 0.001,
                "temperature_profile": "holder_profile",
                "view_factor_from_melt": 0.5,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "profile-resolution-test",
                "extraction_note": "test surface",
            }
        ],
    }


def test_unresolved_profile_defaults_and_records_dp008_reason() -> None:
    geometry = parse_lab_geometry(
        _profile_geometry(),
        allow_temperature_profiles=True,
    )
    assert geometry is not None
    simulator = SimpleNamespace(
        lab_geometry=geometry,
        _dp008_surface_temperature_defaults={},
    )
    temperatures = PyrolysisSimulator._resolve_lab_surface_temperatures(
        simulator,
        {},
        sample_time_h=0.0,
    )
    assert temperatures == {"holder": 0.0}
    notice = simulator._dp008_surface_temperature_defaults["holder"]
    assert notice["code"] == "knob_defaulted"
    assert notice["value"] == pytest.approx(0.0)
    assert notice["unit"] == "C"
    assert notice["policy"]["id"] == "DP-008"
    assert notice["reason"] == (
        "lab_schedule_missing_surface_temperature: holder_profile"
    )


def test_probe_restored_profile_refusal_breaks_default_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = parse_lab_geometry(
        _profile_geometry(),
        allow_temperature_profiles=True,
    )
    assert geometry is not None
    simulator = SimpleNamespace(
        lab_geometry=geometry,
        _dp008_surface_temperature_defaults={},
    )

    def old_refusal(*args, **kwargs):
        raise ValueError("lab_schedule_missing_surface_temperature")

    def contract() -> None:
        try:
            temperatures = PyrolysisSimulator._resolve_lab_surface_temperatures(
                simulator,
                {},
                sample_time_h=0.0,
            )
        except ValueError as exc:
            assert False, str(exc)
        assert temperatures["holder"] == pytest.approx(0.0)

    monkeypatch.setattr(core_module, "_resolve_lab_surface_profile", old_refusal)
    with pytest.raises(AssertionError):
        contract()
