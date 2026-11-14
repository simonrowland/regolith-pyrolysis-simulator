"""Source configurations for scored vacuum Langmuir residue cohorts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


HASHIMOTO_SOURCE_ID = "kems-015-hashimoto-1983"
RICHTER_2007_SOURCE_ID = "kems-010-richter-2007"
RICHTER_2011_SOURCE_ID = (
    "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation"
)

SPHERE_CONSTANT_GEOMETRY = "sphere_constant"
SPHERE_SHRINKING_GEOMETRY = "sphere_shrinking"
DISK_4MM_CONSTANT_GEOMETRY = "disk_4mm_constant"

HASHIMOTO_PRIMARY_GEOMETRY = SPHERE_SHRINKING_GEOMETRY
HASHIMOTO_GEOMETRIES = (
    SPHERE_CONSTANT_GEOMETRY,
    SPHERE_SHRINKING_GEOMETRY,
    DISK_4MM_CONSTANT_GEOMETRY,
)


@dataclass(frozen=True)
class LangmuirResidueCohortSpec:
    """Printed input contract for one source's Langmuir residue cohort."""

    extract_path: str
    experiment_count: int
    geometry_source: Literal[
        "hashimoto_metadata_dimensions",
        "per_experiment_printed_area_mm2",
    ]
    geometry_observation_id: str | None
    geometry_policy_ids: tuple[str, ...]
    primary_geometry_policy_id: str
    initial_mass_source: Literal[
        "experiment_sample_mass_kg",
        "table1_initial_weight_mg",
        "unreported",
    ]
    missing_mass_reason: str | None
    missing_area_reason: str | None
    vacuum_upper_bound_identity: bool
    zero_time_rule: Literal[
        "none",
        "refuse_if_residue_matches_charge",
        "retain_product",
    ]
    area_uncertainty: str | None
    geometry_notice_reason: str | None


_LANGMUIR_RESIDUE_COHORTS = MappingProxyType(
    {
        HASHIMOTO_SOURCE_ID: LangmuirResidueCohortSpec(
            extract_path="data/literature/extracts/kems-015-hashimoto-1983.yaml",
            experiment_count=24,
            geometry_source="hashimoto_metadata_dimensions",
            geometry_observation_id="hashimoto_1983_fe_fcmas_free_evap_geometry_quoted",
            geometry_policy_ids=HASHIMOTO_GEOMETRIES,
            primary_geometry_policy_id=HASHIMOTO_PRIMARY_GEOMETRY,
            initial_mass_source="experiment_sample_mass_kg",
            missing_mass_reason=None,
            missing_area_reason=None,
            vacuum_upper_bound_identity=False,
            zero_time_rule="none",
            area_uncertainty=None,
            geometry_notice_reason=None,
        ),
        RICHTER_2007_SOURCE_ID: LangmuirResidueCohortSpec(
            extract_path="data/literature/extracts/kems-010-richter-2007.yaml",
            experiment_count=51,
            geometry_source="per_experiment_printed_area_mm2",
            geometry_observation_id=None,
            geometry_policy_ids=(
                SPHERE_CONSTANT_GEOMETRY,
                SPHERE_SHRINKING_GEOMETRY,
            ),
            primary_geometry_policy_id=SPHERE_SHRINKING_GEOMETRY,
            initial_mass_source="table1_initial_weight_mg",
            missing_mass_reason=None,
            missing_area_reason="initial_area_unreported",
            vacuum_upper_bound_identity=True,
            zero_time_rule="refuse_if_residue_matches_charge",
            area_uncertainty="printed_unstated",
            geometry_notice_reason="area_evolution_unreported",
        ),
        RICHTER_2011_SOURCE_ID: LangmuirResidueCohortSpec(
            extract_path=(
                "data/literature/extracts/"
                "richter-mendybaev-christensen-etal-2011-olivine-chondrule-evaporation.yaml"
            ),
            experiment_count=50,
            geometry_source="per_experiment_printed_area_mm2",
            geometry_observation_id=None,
            geometry_policy_ids=(
                SPHERE_CONSTANT_GEOMETRY,
                SPHERE_SHRINKING_GEOMETRY,
            ),
            primary_geometry_policy_id=SPHERE_SHRINKING_GEOMETRY,
            initial_mass_source="unreported",
            missing_mass_reason="initial_mass_unreported",
            missing_area_reason="initial_area_unreported",
            vacuum_upper_bound_identity=False,
            zero_time_rule="retain_product",
            area_uncertainty="printed_unstated",
            geometry_notice_reason="area_evolution_unreported",
        ),
    }
)
