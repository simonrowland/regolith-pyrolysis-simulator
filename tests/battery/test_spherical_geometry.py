from __future__ import annotations

from simulator.battery.residue import (
    _hashimoto_geometry_areas,
    _sossi_geometry_areas,
)


def test_spherical_geometry_matches_pre_consolidation_outputs() -> None:
    # Golden values captured from the current production geometry owners before
    # consolidating their shared sphere radius and area calculation.
    hashimoto = _hashimoto_geometry_areas(25.0e-6, {"diameter": 4.0})
    sossi = _sossi_geometry_areas(25.0e-6)

    assert hashimoto["sphere_constant"] == 2.132398102975016e-05
    assert hashimoto["sphere_shrinking"] == 2.132398102975016e-05
    assert hashimoto["disk_4mm_constant"] == 1.2566370614359172e-05
    assert sossi["pt_loop_bead_sphere_constant"] == 2.132398102975016e-05
    assert sossi["pt_loop_bead_sphere_shrinking"] == 2.132398102975016e-05
    assert sossi["pt_loop_bead_disk_equivalent_volume"] == 3.5539968382916893e-06
