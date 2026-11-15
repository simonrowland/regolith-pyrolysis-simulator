"""Source-owned declarations for narrowly scoped reader behaviour."""

from dataclasses import dataclass

from simulator.battery.enums import Phase


@dataclass(frozen=True)
class SourceRule:
    value: bool | Phase | str
    reason: str


SF04_WORKBOOK_SOURCE_ID = "sf04-magma-companion-workbook"
DISTIN_SOURCE_ID = "distin-1971-ferrous-silicate-melts"
HASHIMOTO_SOURCE_ID = "kems-015-hashimoto-1983"
SOSSI_SOURCE_ID = "kems-012-sossi-2019"
MAVROGENES_SOURCE_ID = "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas"
KEMS_137_SOURCE_ID = "kems-137-bischof-2023"
B677_SOURCE_ID = "pankratz-1984-usbm-b677"
B592_SOURCE_ID = "kelley-king-1961-usbm-b592"
B2131_SOURCE_ID = "robie-hemingway-1995-usgs-b2131"


SOURCE_DECLARATIONS: dict[str, dict[str, SourceRule]] = {
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
            "hashimoto", "Hashimoto residue inventory prediction"
        ),
        "residue_component_phase": SourceRule(
            Phase.L,
            "Hashimoto Table 3 identifies the recovered spherule as the run-end silicate melt",
        ),
    },
    SOSSI_SOURCE_ID: {
        "residue_predictor": SourceRule(
            "sossi", "Sossi residue inventory prediction"
        ),
        "residue_component_phase": SourceRule(
            Phase.GLASS,
            "Sossi Table 2 reports residue ppm in quenched glass",
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
    "robie-waldbaum-1968-usgs-b1259": {
        "element_reference_phase_protocol": SourceRule(
            "b1259", "USGS Bulletin 1259 element-reference phase records"
        ),
        "reference_enthalpy_zero_protocol": SourceRule(
            "b1259", "USGS Bulletin 1259 reference enthalpy zero records"
        ),
    },
    "robie-hemingway-fisher-1978-usgs-b1452": {
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
