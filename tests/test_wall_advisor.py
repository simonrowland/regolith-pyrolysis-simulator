from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from simulator.wall_advisor import (
    REGIME_NORMALIZATION,
    advise_wall_materials,
    normalize_regime,
    resolve_wall_operating_point,
)


def _material(results, material_id: str):
    return next(result for result in results if result.material_id == material_id)


def test_wall_material_service_temp_below_zone_temperature_is_gated_out():
    results = advise_wall_materials(["SiO"], zone_temperature_C=1300)

    fused_silica = _material(results, "fused_silica")

    assert fused_silica.temp_ok is False
    assert fused_silica.limiting_temperature_C == 1200
    assert fused_silica.rollup == "temperature-limited"


def test_uncharacterized_wall_cell_surfaces_without_substituted_rating():
    results = advise_wall_materials(["SiO"], zone_temperature_C=1000)

    fused_silica = _material(results, "fused_silica")
    sio = fused_silica.species["SiO"]

    assert sio.stickiness.uncharacterized is True
    assert sio.stickiness.value == "uncharacterized"
    assert sio.stickiness.value != "strongly-adhering"
    # Default operating point is reducing vacuum: the characterized SiO2 ->
    # SiO(g) self-volatilization hazard now outranks the stickiness data hole.
    assert sio.reactive.verdict == "hazardous"
    assert fused_silica.rollup == "risky"


def test_characterized_wall_cell_surfaces_data_value_and_evidence():
    results = advise_wall_materials(["Na"], zone_temperature_C=1600)

    dense_alumina = _material(results, "dense_alumina")
    alkali = dense_alumina.species["alkali"]

    assert dense_alumina.temp_ok is True
    assert alkali.chemical_attack.value == "high"
    assert alkali.chemical_attack.evidence == "direct"
    assert alkali.stickiness.value == "strongly-adhering"
    assert alkali.stickiness.evidence == "analogous-only"


def test_unknown_active_vapor_species_is_uncharacterized_not_inferred():
    results = advise_wall_materials(["Mg"], zone_temperature_C=1000)

    fused_silica = _material(results, "fused_silica")
    mg = fused_silica.species["Mg"]

    assert mg.chemical_attack.uncharacterized is True
    assert mg.chemical_attack.value is None
    assert mg.stickiness.uncharacterized is True
    assert mg.stickiness.value == "uncharacterized"


def test_mixed_active_species_fused_silica_flags_known_hazard_over_holes():
    results = advise_wall_materials(["Na", "SiO"], zone_temperature_C=1000)

    fused_silica = _material(results, "fused_silica")
    alkali = fused_silica.species["alkali"]
    sio = fused_silica.species["SiO"]

    assert alkali.chemical_attack.uncharacterized is False
    assert alkali.stickiness.uncharacterized is False
    # Air-analog stickiness provenance is display-only; it must not drive the verdict.
    assert alkali.stickiness.regime_raw == "air"
    assert alkali.stickiness.verdict_eligible is False
    assert sio.stickiness.uncharacterized is True
    # Chemical-attack high + reactive SiO volatilization hazard dominate the holes.
    assert fused_silica.rollup == "risky"


def test_all_uncharacterized_active_species_cells_never_roll_up_usable(tmp_path):
    data_path = tmp_path / "wall_materials.yaml"
    data_path.write_text(
        dedent(
            """
            materials:
              all_uncharacterized:
                label: "All uncharacterized test material"
                role: "test fixture"
                service_temp:
                  continuous_C: 1500
                  max_operating_C: 1500
                  peak_C: null
                  degradation_onset_C: null
                  evidence: direct
                  citations: []
                  note: "test fixture"
                chemical_attack:
                  SiO:
                    severity: null
                    evidence: uncharacterized
                    citations: []
                    note: "test fixture"
                  alkali_NaK:
                    severity: null
                    evidence: uncharacterized
                    citations: []
                    note: "test fixture"
                stickiness:
                  SiO:
                    class: uncharacterized
                    evidence: uncharacterized
                    citations: []
                    note: "test fixture"
                  alkali:
                    class: uncharacterized
                    evidence: uncharacterized
                    citations: []
                    note: "test fixture"
            """
        )
    )

    results = advise_wall_materials(
        ["Na", "SiO"],
        zone_temperature_C=1000,
        data_path=data_path,
    )

    material = _material(results, "all_uncharacterized")

    assert material.temp_ok is True
    assert material.rollup == "uncharacterized"
    assert material.rollup != "usable"
    assert material.species["alkali"].chemical_attack.uncharacterized is True
    assert material.species["alkali"].stickiness.uncharacterized is True
    assert material.species["SiO"].chemical_attack.uncharacterized is True
    assert material.species["SiO"].stickiness.uncharacterized is True
    # No reactive_exchange section at all: fail-closed, never a silent pass.
    assert material.species["SiO"].reactive.verdict == "uncharacterized"
    assert material.species["SiO"].reactive.matched is False
    assert material.species["SiO"].reactive.needs_experiment is True


def test_default_operating_point_is_low_po2_reducing_vacuum():
    operating_point = resolve_wall_operating_point()

    assert operating_point.po2_regime == "reducing"
    assert operating_point.pressure_regime == "vacuum"


def test_operating_point_regimes_follow_recipe_knobs():
    dosed_o2 = resolve_wall_operating_point(pO2_mbar=9.0)
    assert dosed_o2.po2_regime == "oxidizing"
    assert dosed_o2.pressure_regime == "vacuum"

    po2_managed = resolve_wall_operating_point(pO2_mbar=1.5)
    assert po2_managed.po2_regime == "buffered"

    sweep = resolve_wall_operating_point(pO2_mbar=0.0, p_buffer_mbar=9.0)
    assert sweep.po2_regime == "reducing"
    assert sweep.pressure_regime == "millibar_sweep"

    sub_viscous = resolve_wall_operating_point(p_buffer_mbar=1.0)
    assert sub_viscous.pressure_regime == "vacuum"

    with pytest.raises(ValueError):
        resolve_wall_operating_point(pO2_mbar=-1.0)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"pO2_mbar": float("inf")},
        {"pO2_mbar": float("nan")},
        {"p_buffer_mbar": float("inf")},
        {"p_buffer_mbar": float("nan")},
    ),
)
def test_operating_point_rejects_non_finite_knobs(kwargs):
    with pytest.raises(ValueError, match="non-negative finite"):
        resolve_wall_operating_point(**kwargs)


def test_sic_reactive_verdict_flips_on_dosed_o2_excursion():
    default_results = advise_wall_materials(["SiO"], zone_temperature_C=1000)
    sic_default = _material(default_results, "silicon_carbide_coating")
    reactive_default = sic_default.species["SiO"].reactive

    # Project default reducing vacuum: SiC active oxidation (SiO(g) + CO(g)).
    assert sic_default.operating_point.po2_regime == "reducing"
    assert reactive_default.verdict == "hazardous"
    assert reactive_default.sign == "volatile_or_revolatilizing"
    assert reactive_default.regime_raw == "reducing_vacuum"

    dosed_results = advise_wall_materials(
        ["SiO"], zone_temperature_C=1000, pO2_mbar=9.0
    )
    sic_dosed = _material(dosed_results, "silicon_carbide_coating")
    reactive_dosed = sic_dosed.species["SiO"].reactive

    # Dosed-O2 SiO2-hold excursion: SiC passivates behind a SiO2 scale.
    assert sic_dosed.operating_point.po2_regime == "oxidizing"
    assert reactive_dosed.verdict == "protective"
    assert reactive_dosed.sign == "consolidating"
    assert reactive_dosed.regime_raw == "oxidizing"


def test_reactive_hazard_is_po2_keyed_and_survives_millibar_sweep():
    # Reactive exchange is the chemical-attack half: selected by pO2 regime.
    # A redox hazard (SiC active oxidation) does not vanish when the buffer
    # gas moves the transport regime from vacuum to millibar sweep.
    results = advise_wall_materials(
        ["SiO"], zone_temperature_C=1000, pO2_mbar=0.0, p_buffer_mbar=9.0
    )

    sic = _material(results, "silicon_carbide_coating")

    assert sic.operating_point.pressure_regime == "millibar_sweep"
    assert sic.operating_point.po2_regime == "reducing"
    assert sic.species["SiO"].reactive.verdict == "hazardous"


def test_dense_alumina_alkali_spall_hazard_drives_risky_at_default_point():
    results = advise_wall_materials(["Na"], zone_temperature_C=1600)

    dense_alumina = _material(results, "dense_alumina")
    reactive = dense_alumina.species["alkali"].reactive

    assert reactive.verdict == "hazardous"
    assert reactive.sign == "expansive_spalling"
    assert reactive.net_liner_delta == "spall"
    assert dense_alumina.rollup == "risky"


def test_uncharacterized_regime_hole_yields_fail_closed_verdict():
    # mzo_coating only carries reducing_vacuum rows; the dosed-O2 operating
    # point is a material x species x regime hole and must surface as
    # needs-experiment, never as a silent pass.
    results = advise_wall_materials(["SiO"], zone_temperature_C=1000, pO2_mbar=9.0)

    mzo = _material(results, "mzo_coating")
    reactive = mzo.species["SiO"].reactive

    assert reactive.verdict == "uncharacterized"
    assert reactive.matched is False
    assert reactive.needs_experiment is True
    assert mzo.rollup != "usable"


def _write_single_material_data(tmp_path, *, reactive_regime: str) -> Path:
    data_path = tmp_path / "wall_materials.yaml"
    data_path.write_text(
        dedent(
            f"""
            materials:
              test_material:
                label: "Test material"
                role: "test fixture"
                service_temp:
                  continuous_C: 1500
                  max_operating_C: 1500
                  peak_C: null
                  degradation_onset_C: null
                  evidence: direct
                  citations: []
                  note: "test fixture"
                chemical_attack:
                  SiO:
                    severity: low
                    evidence: direct
                    citations: []
                    note: "test fixture"
                stickiness:
                  SiO:
                    class: sheds
                    evidence: direct
                    citations: []
                    note: "test fixture"
            reactive_exchange:
              test_material:
                SiO:
                  - regime: {reactive_regime}
                    product_phase: "benign product"
                    favorability: source_supported
                    sign: consolidating
                    net_liner_delta: thickening
                    wall_property_effect:
                      structural: consolidating_if_adherent
                      service_temp_shift: uncharacterized
                      magnitude: qualitative
                      basis: "test fixture"
                    evidence_tier: direct_observation
                    source_ids: []
                    needs_experiment: false
                    needs_calphad: false
            """
        )
    )
    return data_path


def _write_pressure_gated_stickiness_data(tmp_path) -> Path:
    data_path = tmp_path / "wall_materials.yaml"
    data_path.write_text(
        dedent(
            """
            materials:
              test_material:
                label: "Test material"
                role: "test fixture"
                service_temp:
                  continuous_C: 1500
                  max_operating_C: 1500
                  peak_C: null
                  degradation_onset_C: null
                  evidence: direct
                  citations: []
                  note: "test fixture"
                chemical_attack:
                  Fe_FeO:
                    severity: low
                    evidence: direct
                    citations: []
                    note: "test fixture"
                stickiness:
                  Fe:
                    class: strongly-adhering
                    evidence: direct
                    citations: []
                    note: "vacuum-only test fixture"
                    provenance:
                      regime: vacuum
            reactive_exchange:
              test_material:
                Fe_FeO:
                  - regime: low-pO2
                    product_phase: "benign product"
                    favorability: source_supported
                    sign: neutral
                    net_liner_delta: unchanged
                    wall_property_effect:
                      structural: neutral
                      service_temp_shift: uncharacterized
                      magnitude: qualitative
                      basis: "test fixture"
                    evidence_tier: direct_observation
                    source_ids: []
                    needs_experiment: false
                    needs_calphad: false
            """
        )
    )
    return data_path


def test_air_provenance_reactive_rows_never_drive_a_verdict(tmp_path):
    data_path = _write_single_material_data(tmp_path, reactive_regime="air")

    # Air row would read "consolidating/benign" -- but molten pyrolysis is
    # always pumped-down low-pO2, so it must stay provenance-only even at the
    # dosed-O2 (oxidizing) operating point it would otherwise match.
    results = advise_wall_materials(
        ["SiO"], zone_temperature_C=1000, pO2_mbar=9.0, data_path=data_path
    )
    material = _material(results, "test_material")

    assert material.species["SiO"].reactive.verdict == "uncharacterized"
    assert material.species["SiO"].reactive.matched is False
    assert material.rollup == "uncharacterized"


def test_air_provenance_stickiness_rows_never_drive_the_rollup():
    results = advise_wall_materials(["Na"], zone_temperature_C=1600)

    dense_alumina = _material(results, "dense_alumina")
    alkali = dense_alumina.species["alkali"]

    # The class stays visible as provenance but is excluded from the verdict.
    assert alkali.stickiness.value == "strongly-adhering"
    assert alkali.stickiness.regime_raw == "air"
    assert alkali.stickiness.verdict_eligible is False


def test_vacuum_only_stickiness_rows_are_pressure_gated(tmp_path):
    data_path = _write_pressure_gated_stickiness_data(tmp_path)

    vacuum_results = advise_wall_materials(
        ["Fe"], zone_temperature_C=1000, data_path=data_path
    )
    vacuum = _material(vacuum_results, "test_material")
    vacuum_fe = vacuum.species["Fe"]

    assert vacuum.operating_point.pressure_regime == "vacuum"
    assert vacuum_fe.stickiness.value == "strongly-adhering"
    assert vacuum_fe.stickiness.regime_raw == "vacuum"
    assert vacuum_fe.stickiness.verdict_eligible is True
    assert vacuum.rollup == "risky"

    sweep_results = advise_wall_materials(
        ["Fe"],
        zone_temperature_C=1000,
        p_buffer_mbar=9.0,
        data_path=data_path,
    )
    sweep = _material(sweep_results, "test_material")
    sweep_fe = sweep.species["Fe"]

    assert sweep.operating_point.pressure_regime == "millibar_sweep"
    assert sweep_fe.stickiness.value == "strongly-adhering"
    assert sweep_fe.stickiness.regime_raw == "vacuum"
    assert sweep_fe.stickiness.verdict_eligible is False
    assert sweep.rollup == "uncharacterized"


def test_unmapped_regime_value_fails_loud(tmp_path):
    data_path = _write_single_material_data(
        tmp_path, reactive_regime="mystery_regime"
    )

    with pytest.raises(ValueError, match="unmapped wall-materials regime"):
        advise_wall_materials(
            ["SiO"], zone_temperature_C=1000, data_path=data_path
        )

    with pytest.raises(ValueError, match="unmapped wall-materials regime"):
        normalize_regime("reducing")  # axis value, not a raw data value


def _collect_regime_values(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "regime":
                yield value
            else:
                yield from _collect_regime_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _collect_regime_values(item)


def test_every_regime_value_in_wall_materials_data_is_mapped():
    data_dir = Path(__file__).resolve().parents[1] / "data"
    with (data_dir / "wall_materials.yaml").open() as handle:
        data = yaml.safe_load(handle)

    regimes = set(_collect_regime_values(data))
    assert regimes  # the data must actually carry regime-gated rows
    unmapped = regimes - set(REGIME_NORMALIZATION)
    assert not unmapped, f"unmapped regime values in wall_materials.yaml: {sorted(unmapped)}"
