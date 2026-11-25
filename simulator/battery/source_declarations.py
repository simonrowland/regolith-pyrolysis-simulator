"""Source-owned declarations for narrowly scoped reader behaviour."""

from dataclasses import dataclass
from typing import Literal

from simulator.battery.enums import Phase
from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    SOURCE_ID as B1452_SOURCE_ID,
)
from simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader import (
    SOURCE_ID as B1259_SOURCE_ID,
)
from simulator.source_ids import (
    FEDKIN_SOURCE_ID,
    HASHIMOTO_SOURCE_ID,
    RYS_SOURCE_ID,
    SCHAEFER_FEGLEY_SOURCE_ID,
    SF04_WORKBOOK_SOURCE_ID,
    SOSSI_FEGLEY_SOURCE_ID,
    SOSSI_SOURCE_ID,
)


@dataclass(frozen=True)
class CompositionProjectionRule:
    source_basis: Literal[
        "elemental wt %", "partial oxide analysis with Cl/S/F"
    ]
    reason: str
    applies_to_quantities: tuple[str, ...]
    source_valences: tuple[tuple[str, int], ...] = ()
    default_valences: tuple[tuple[str, int], ...] = ()
    element_aliases: tuple[tuple[str, str], ...] = ()
    required_elements: tuple[str, ...] = ()
    required_components: tuple[str, ...] = ()
    retained_components: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRule:
    value: bool | Phase | str | CompositionProjectionRule
    reason: str


DISTIN_SOURCE_ID = "distin-1971-ferrous-silicate-melts"
HASHIMOTO_RESIDUE_PREDICTOR = "hashimoto"
SOSSI_RESIDUE_PREDICTOR = "sossi"
MAVROGENES_SOURCE_ID = "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas"
KEMS_137_SOURCE_ID = "kems-137-bischof-2023"
B677_SOURCE_ID = "pankratz-1984-usbm-b677"
B592_SOURCE_ID = "kelley-king-1961-usbm-b592"
B2131_SOURCE_ID = "robie-hemingway-1995-usgs-b2131"
RUSIECKA_SOURCE_ID = "rusiecka-2026-chlorine-data"
SOURCE_DECLARATIONS: dict[str, dict[str, SourceRule]] = {
    RUSIECKA_SOURCE_ID: {
        "composition_projection": SourceRule(
            CompositionProjectionRule(
                source_basis="partial oxide analysis with Cl/S/F",
                reason=(
                    "Rusiecka 2026 reports partial quenched-glass oxide analyses; "
                    "preserve the printed oxides and identify supported Cl/S/F"
                ),
                applies_to_quantities=(
                    "equilibrium_phase_composition",
                    "chloride_capacity",
                ),
                required_components=(
                    "SiO2",
                    "TiO2",
                    "Al2O3",
                    "FeOT",
                    "MgO",
                    "CaO",
                    "Na2O",
                    "K2O",
                ),
                retained_components=("Cl", "S", "F"),
            ),
            "Project a located Rusiecka run analysis at migration",
        ),
    },
    SF04_WORKBOOK_SOURCE_ID: {
        "sf04_workbook": SourceRule(
            True,
            "The SF04 companion workbook contains model inputs, not independent observations",
        ),
    },
    DISTIN_SOURCE_ID: {
        "distin_fe_metal_saturation": SourceRule(
            True,
            "Distin's FeO activity rows require the reported Fe-metal-saturated condition",
        ),
    },
    HASHIMOTO_SOURCE_ID: {
        "residue_predictor": SourceRule(
            HASHIMOTO_RESIDUE_PREDICTOR, "Hashimoto residue inventory prediction"
        ),
        "residue_component_phase": SourceRule(
            Phase.L,
            "Hashimoto Table 3 identifies the recovered spherule as the run-end silicate melt",
        ),
    },
    SOSSI_SOURCE_ID: {
        "residue_predictor": SourceRule(
            SOSSI_RESIDUE_PREDICTOR, "Sossi residue inventory prediction"
        ),
        "residue_component_phase": SourceRule(
            Phase.GLASS,
            "Sossi Table 2 reports residue ppm in quenched glass",
        ),
    },
    RYS_SOURCE_ID: {
        "oxygen_measurement_status_required": SourceRule(
            True,
            "Ryś pO2 may be calculated from congruent effusion; only independently measured oxygen is eligible",
        ),
    },
    MAVROGENES_SOURCE_ID: {
        "relative_oxygen_notice": SourceRule(
            "delta_iw_activity_corrected_gamma_model=Holzheid1997_FeO_gamma_1.7; "
            "source definition: log fO2 = -ΔG°(11)/22.3RT + "
            "2 log a_FeO^silicate - 2 log a_Fe^metal; a_FeO=1.7 X_FeO",
            "Mavrogenes & O'Neill (1999), Eq. 12 and Table 2 footnote",
        ),
    },
    KEMS_137_SOURCE_ID: {
        "relative_oxygen_notice": SourceRule(
            "delta_iw_reference=iron_wustite_buffer; source definition: "
            "log(fO2) range ΔIW+1.5 to ΔIW+2.5 "
            "(IW = iron-wüstite buffer)",
            "Bischof et al. (2023), abstract",
        ),
        "printed_oxygen_requires_calibration": SourceRule(
            True,
            "Bischof ΔIW has no cited buffer calibration; no fO2_Pa assigned",
        ),
    },
    FEDKIN_SOURCE_ID: {
        "calibration_kind": SourceRule(
            "literature correlation", "Calibration report classification for Fedkin (2006)"
        ),
        "calibration_kind_override": SourceRule(
            "derived measurement", "Calibration report override for Fedkin (2006)"
        ),
    },
    SCHAEFER_FEGLEY_SOURCE_ID: {
        "calibration_kind": SourceRule(
            "literature correlation", "Calibration report classification for Schaefer & Fegley (2004)"
        ),
    },
    SOSSI_FEGLEY_SOURCE_ID: {
        "calibration_kind": SourceRule(
            "literature correlation", "Calibration report classification for Sossi & Fegley (2018)"
        ),
    },
    B677_SOURCE_ID: {
        "printed_basis_protocol": SourceRule(
            "b677",
            "Pankratz (1984), USBM Bulletin 677",
        ),
        "ideal_gas_enthalpy_zero": SourceRule(
            True,
            "B677 ideal-gas tables define H298 against that gas when the record heading and zero cell say ideal gas",
        ),
    },
    B592_SOURCE_ID: {
        "printed_basis_protocol": SourceRule(
            "b592",
            "Kelley & King (1961), USBM Bulletin 592",
        ),
    },
    B2131_SOURCE_ID: {
        "printed_basis_protocol": SourceRule(
            "b2131",
            "Robie & Hemingway (1995), USGS Bulletin 2131",
        ),
    },
    "janaf": {
        "compilation_family": SourceRule("janaf", "JANAF compilation"),
    },
    "janaf-4th": {
        "compilation_family": SourceRule("janaf", "JANAF compilation"),
    },
    B1259_SOURCE_ID: {
        "element_reference_phase_protocol": SourceRule(
            "b1259", "USGS Bulletin 1259 element-reference phase records"
        ),
        "reference_enthalpy_zero_protocol": SourceRule(
            "b1259", "USGS Bulletin 1259 reference enthalpy zero records"
        ),
    },
    B1452_SOURCE_ID: {
        "reference_enthalpy_zero_protocol": SourceRule(
            "b1452", "USGS Bulletin 1452 reference enthalpy zero records"
        ),
    },
    "nist-janaf-4th": {
        "compilation_family": SourceRule("janaf", "JANAF compilation"),
        "reaction_decimal_subscripts": SourceRule(
            True,
            "JANAF decimal subscripts are not dotted hydrate notation",
        ),
    },
}


def source_rule_enabled(source_id: str | None, key: str) -> bool:
    rule = SOURCE_DECLARATIONS.get(source_id or "", {}).get(key)
    return rule is not None and rule.value is True


def composition_projection_rule(
    source_id: str | None,
) -> CompositionProjectionRule | None:
    rule = SOURCE_DECLARATIONS.get(source_id or "", {}).get(
        "composition_projection"
    )
    return (
        rule.value
        if rule is not None and isinstance(rule.value, CompositionProjectionRule)
        else None
    )


def declared_residue_predictor(source_id: str | None) -> str | None:
    rule = SOURCE_DECLARATIONS.get(source_id or "", {}).get("residue_predictor")
    return rule.value if rule is not None and isinstance(rule.value, str) else None


def calibration_kinds_for_source(
    source_id: str | None,
) -> tuple[str | None, str | None]:
    """Return the declared calibration kind and its later derived-kind override."""
    rules = SOURCE_DECLARATIONS.get(source_id or "", {})
    kind = rules.get("calibration_kind")
    override = rules.get("calibration_kind_override")
    return (
        None if kind is None else str(kind.value),
        None if override is None else str(override.value),
    )
