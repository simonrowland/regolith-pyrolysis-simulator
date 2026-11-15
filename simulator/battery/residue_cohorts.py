"""Source configurations for scored vacuum Langmuir residue cohorts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Mapping

from simulator.battery.enums import Quantity
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import State


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
    geometry_notice_reason: str | None
    thermal_schedule_notice_reasons: tuple[str, ...]


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
            geometry_notice_reason=None,
            thermal_schedule_notice_reasons=(),
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
            geometry_notice_reason="area_evolution_unreported",
            thermal_schedule_notice_reasons=(
                "thermal_preheat_path_unreported: the source says about one "
                "hour to 900 C but omits the starting temperature and ramp; "
                "this preheat segment is omitted from the point trajectory",
                "thermal_cooling_path_unresolved: the source says cooling "
                "to room temperature took a few minutes but gives no "
                "temperature path; cooling is omitted from the trajectory",
            ),
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
            geometry_notice_reason="area_evolution_unreported",
            thermal_schedule_notice_reasons=(),
        ),
    }
)


def richter_2007_table1_sample_inputs(
    doc: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Lift Richter Table 1 inputs and its shared starting charge to runs."""
    if doc.get("source_id") != RICHTER_2007_SOURCE_ID:
        return doc
    samples: dict[str, tuple[Decimal, Decimal, Mapping[str, Any]]] = {}
    species = doc.get("species") or {}
    if not isinstance(species, Mapping):
        return doc
    for block in species.values():
        if not isinstance(block, Mapping):
            continue
        for observation in block.get("observations") or ():
            if not isinstance(observation, Mapping):
                continue
            equipment = observation.get("equipment") or {}
            surface_area = (
                equipment.get("sample_surface_area")
                if isinstance(equipment, Mapping)
                else None
            )
            rows = (
                surface_area.get("value")
                if isinstance(surface_area, Mapping)
                else None
            )
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping) or not row.get("sample"):
                    continue
                try:
                    area_mm2 = Decimal(str(row["initial_area_mm2"]))
                    mass_mg = Decimal(str(row["initial_weight_mg"]))
                except (KeyError, ArithmeticError, ValueError):
                    continue
                if (
                    not area_mm2.is_finite()
                    or area_mm2 <= 0
                    or not mass_mg.is_finite()
                    or mass_mg <= 0
                ):
                    continue
                name = str(row["sample"])
                value = (mass_mg, area_mm2, row.get("locator") or {})
                if name in samples and samples[name][:2] != value[:2]:
                    raise ValueError(
                        f"Richter Table 1 has conflicting mass/area for {name}"
                    )
                samples[name] = value

    experiments = doc.get("experiments")
    if not isinstance(experiments, list):
        return doc
    starting_material_sample = next(
        (
            item.get("sample")
            for item in experiments
            if isinstance(item, Mapping)
            and item.get("experiment_id") == "type-b-cai-like-lab-melt"
            and isinstance(item.get("sample"), Mapping)
            and isinstance(
                item["sample"].get("printed_composition"), Mapping
            )
            and isinstance(
                item["sample"]["printed_composition"].get("locator"), Mapping
            )
            and "row: Starting material"
            in str(
                item["sample"]["printed_composition"]["locator"].get("note")
                or ""
            )
        ),
        None,
    )
    starting_material = (
        starting_material_sample.get("printed_composition")
        if starting_material_sample is not None
        else None
    )
    starting_initial_composition = (
        starting_material_sample.get("initial_composition")
        if starting_material_sample is not None
        else None
    )
    enriched = []
    for experiment in experiments:
        if not isinstance(experiment, Mapping):
            enriched.append(experiment)
            continue
        experiment = dict(experiment)
        experiment_locator = experiment.get("locator") or {}
        row_name = (
            experiment_locator.get("row")
            if isinstance(experiment_locator, Mapping)
            else None
        )
        measurement = samples.get(str(row_name))
        is_table1_run = (
            isinstance(experiment_locator, Mapping)
            and experiment_locator.get("table") == "1"
        )
        sample = (
            dict(experiment.get("sample") or {})
            if is_table1_run
            else None
        )
        if sample is not None and starting_material is not None:
            # Table 1 residue compositions are measurements, not the
            # shared starting charge. Correct both existing source
            # representations from that row; leave a missing typed value
            # for the normal downstream initializer to resolve.
            sample["printed_composition"] = starting_material
            if (
                starting_initial_composition is not None
                and isinstance(sample.get("initial_composition"), Mapping)
            ):
                sample["initial_composition"] = starting_initial_composition
        if measurement is not None:
            mass_mg, area_mm2, source_locator = measurement
            source_locator = dict(source_locator)
            # Exact SI conversions: 1 mg = 10^-6 kg; 1 mm^2 = 10^-6 m^2.
            mass_kg = mass_mg * Decimal("0.000001")
            area_m2 = area_mm2 * Decimal("0.000001")
            if sample is None:
                sample = dict(experiment.get("sample") or {})
            sample["mass_kg"] = {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": str(mass_kg)},
                },
                "locator": {
                    **source_locator,
                    "note": "Table 1 initial_weight_mg; exact mg-to-kg conversion",
                },
            }
            apparatus = dict(experiment.get("apparatus") or {})
            geometry = dict(apparatus.get("geometry") or {})
            geometry["exposed_area_m2"] = {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": str(area_m2)},
                },
                "locator": {
                    **source_locator,
                    "note": (
                        "Table 1 initial_area_mm2; exact mm^2-to-m^2 conversion; "
                        "exposed sample area for free evaporation"
                    ),
                },
            }
            apparatus["geometry"] = geometry
            experiment["apparatus"] = apparatus
        if sample is not None:
            experiment["sample"] = sample
        enriched.append(experiment)
    return {**doc, "experiments": enriched}


def langmuir_comparison_identity_updates(
    source_id: str,
    identity: Identity,
    quantity: Quantity | None,
    prediction_detail: object,
) -> dict[str, State]:
    """Resolve only a registered vacuum cohort's declared pressure ceiling."""
    spec = _LANGMUIR_RESIDUE_COHORTS.get(source_id)
    detail = prediction_detail if isinstance(prediction_detail, dict) else {}
    if (
        spec is None
        or not spec.vacuum_upper_bound_identity
        or quantity is not Quantity.RESIDUE_COMPONENT_COMPOSITION
        or not detail.get("total_pressure_is_upper_bound")
        or detail.get("total_pressure_Pa") is None
    ):
        return {}
    updates: dict[str, State] = {}
    if identity.fO2_Pa is None or not identity.fO2_Pa.is_value:
        updates["fO2_Pa"] = State.not_applicable(
            "unbuffered vacuum has no commanded oxygen fugacity"
        )
    if identity.total_pressure_Pa is None or not identity.total_pressure_Pa.is_value:
        updates["total_pressure_Pa"] = State.of(
            Decimal(str(detail["total_pressure_Pa"]))
        )
    return updates


def langmuir_experiment_ids_by_source(observations: object) -> dict[str, frozenset[str]]:
    """Select run ids from the registered cohorts' actual residue rows."""
    rows = observations.values() if isinstance(observations, dict) else observations
    return {
        source_id: frozenset(
            row.experiment_id
            for row in rows
            if row.source_id == source_id
            and isinstance(row.identity, Identity)
            and quantity_token(row.identity)
            is Quantity.RESIDUE_COMPONENT_COMPOSITION
        )
        for source_id in _LANGMUIR_RESIDUE_COHORTS
    }
