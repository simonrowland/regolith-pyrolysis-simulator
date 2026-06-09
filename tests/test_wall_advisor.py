from __future__ import annotations

from textwrap import dedent

from simulator.wall_advisor import advise_wall_materials


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
    assert fused_silica.rollup == "uncharacterized"


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


def test_mixed_active_species_keep_fused_silica_uncharacterized():
    results = advise_wall_materials(["Na", "SiO"], zone_temperature_C=1000)

    fused_silica = _material(results, "fused_silica")
    alkali = fused_silica.species["alkali"]
    sio = fused_silica.species["SiO"]

    assert alkali.chemical_attack.uncharacterized is False
    assert alkali.stickiness.uncharacterized is False
    assert sio.stickiness.uncharacterized is True
    assert fused_silica.rollup == "uncharacterized"


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
