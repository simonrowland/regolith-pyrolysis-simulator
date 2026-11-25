"""Source-owned declarations for narrowly scoped reader behaviour."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from simulator.battery.enums import NoticeKind, Phase, Quantity
from simulator.battery.records import Notice
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
class SourceRule:
    value: bool | Phase | str
    reason: str


EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG = "external_sample_analysis"
SAMPLE_CATALOG_COMPOSITION_PROXY_FLAG = "composition_from_sample_catalog"
COMPOSITION_SOURCE_PROXY_FLAGS = frozenset(
    {
        SAMPLE_CATALOG_COMPOSITION_PROXY_FLAG,
        EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG,
    }
)
EXTERNAL_SAMPLE_ANALYSIS_LINK_FIELDS = (
    EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG,
    "sample",
    "charge_sample_type",
    "composition_source_id",
    "composition_source_observation_id",
    "composition_heterogeneity_caveat",
)


@dataclass(frozen=True)
class ExternalSampleAnalysisLink:
    source_reference: str
    sample_number: str
    heterogeneity_caveat: str
    oxide_composition_wt_pct: Mapping[str, object]


@dataclass(frozen=True)
class ExternalSampleAnalysisRefusal:
    reason: str


@dataclass(frozen=True)
class ExternalSampleAnalysisCandidate:
    source_id: str
    observation_id: str
    sample_number: str | None
    sample_type: str | None
    analysis_role: str | None
    is_post_heating_residue: bool
    locator: Mapping[str, object] | None
    oxide_composition_wt_pct: Mapping[str, object] | None


ExternalSampleAnalysisDecision = (
    ExternalSampleAnalysisLink | ExternalSampleAnalysisRefusal
)


DISTIN_SOURCE_ID = "distin-1971-ferrous-silicate-melts"
HASHIMOTO_RESIDUE_PREDICTOR = "hashimoto"
SOSSI_RESIDUE_PREDICTOR = "sossi"
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


def external_sample_analysis_source_ids_from_extracts(
    extracts: Sequence[object],
) -> tuple[str, ...]:
    """Return exact companion source IDs declared by the linked extract rows."""

    source_ids: set[str] = set()
    for extract in extracts:
        if not isinstance(extract, Mapping):
            continue
        species = extract.get("species")
        if not isinstance(species, Mapping):
            continue
        for block in species.values():
            if not isinstance(block, Mapping):
                continue
            observations = block.get("observations")
            if not isinstance(observations, Sequence) or isinstance(
                observations, (str, bytes)
            ):
                continue
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                raw_values = observation.get("values")
                value_rows = (
                    [raw_values] if isinstance(raw_values, Mapping) else []
                )
                if value_rows:
                    for row_key in ("points", "series"):
                        rows = raw_values.get(row_key)
                        if isinstance(rows, Sequence) and not isinstance(
                            rows, (str, bytes)
                        ):
                            value_rows.extend(
                                row for row in rows if isinstance(row, Mapping)
                            )
                for values in value_rows:
                    if values.get(EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG) is not True:
                        continue
                    source_id = values.get("composition_source_id")
                    if (
                        isinstance(source_id, str)
                        and source_id
                        and source_id == source_id.strip()
                        and "/" not in source_id
                        and "\\" not in source_id
                    ):
                        source_ids.add(source_id)
    return tuple(sorted(source_ids))


def external_sample_analysis_candidates_from_extracts(
    extracts: Sequence[object],
) -> tuple[ExternalSampleAnalysisCandidate, ...]:
    """Read located alternatives from the explicitly linked v1 extracts."""

    candidates: list[ExternalSampleAnalysisCandidate] = []
    for extract in extracts:
        if not isinstance(extract, Mapping):
            continue
        source_id = extract.get("source_id")
        species = extract.get("species")
        if not isinstance(source_id, str) or not isinstance(species, Mapping):
            continue
        for block in species.values():
            if not isinstance(block, Mapping):
                continue
            observations = block.get("observations")
            if not isinstance(observations, Sequence) or isinstance(
                observations, (str, bytes)
            ):
                continue
            for observation in observations:
                if not isinstance(observation, Mapping):
                    continue
                observation_id = observation.get("observation_id")
                values = observation.get("values")
                if not isinstance(observation_id, str) or not isinstance(
                    values, Mapping
                ):
                    continue
                sample = values.get("sample")
                sample_type = values.get("sample_type_as_printed")
                role = values.get("analysis_role")
                locator = observation.get("locator")
                composition = values.get("major_oxide_measurements_wt_pct")
                candidates.append(
                    ExternalSampleAnalysisCandidate(
                        source_id=source_id,
                        observation_id=observation_id,
                        sample_number=sample if isinstance(sample, str) else None,
                        sample_type=(
                            sample_type if isinstance(sample_type, str) else None
                        ),
                        analysis_role=role if isinstance(role, str) else None,
                        is_post_heating_residue=(
                            values.get("composition_is_post_heating_residue") is True
                        ),
                        locator=locator if isinstance(locator, Mapping) else None,
                        oxide_composition_wt_pct=(
                            composition if isinstance(composition, Mapping) else None
                        ),
                    )
                )
    return tuple(candidates)


def resolve_external_sample_analysis(
    values: object,
    candidates: Sequence[ExternalSampleAnalysisCandidate] = (),
) -> ExternalSampleAnalysisDecision | None:
    """Resolve a located initial bulk analysis by exact printed sample ID.

    Callers pass every located alternative from the row's declared source
    extracts. This owner requires the exact source observation, sample number,
    and analysis role; it never falls back to another sample or run residue.
    """

    if (
        not isinstance(values, Mapping)
        or values.get(EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG) is not True
    ):
        return None
    sample_number = values.get("sample")
    if not isinstance(sample_number, str):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: printed charge sample number is required"
        )
    source_id = values.get("composition_source_id")
    observation_id = values.get("composition_source_observation_id")
    if not isinstance(source_id, str) or not isinstance(observation_id, str):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: exact source and observation IDs are required"
        )
    selected = [
        candidate
        for candidate in candidates
        if candidate.source_id == source_id
        and candidate.observation_id == observation_id
    ]
    if len(selected) != 1:
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: source observation reference is missing or ambiguous"
        )
    candidate = selected[0]
    if candidate.sample_number is None:
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: analysis sample number is missing"
        )
    if sample_number != candidate.sample_number:
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: printed sample numbers do not match exactly"
        )
    if (
        candidate.analysis_role != "initial_charge_bulk"
        or candidate.is_post_heating_residue
    ):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: post-heating residue or non-charge analysis cannot stand for the charge"
        )
    if values.get("composition_is_post_heating_residue") is True:
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: post-heating residue cannot stand for the charge"
        )
    locator = candidate.locator
    if not isinstance(locator, Mapping):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: primary source locator is missing"
        )
    source_path = locator.get("source_path")
    table = locator.get("table")
    page = locator.get("published_page")
    row = locator.get("row")
    if (
        not isinstance(source_path, str)
        or not source_path.startswith("text/")
        or table in (None, "")
        or page in (None, "")
        or row in (None, "")
    ):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: source text locator needs table, published page, and row"
        )
    charge_type = values.get("charge_sample_type")
    analysis_type = candidate.sample_type
    if (
        isinstance(charge_type, str)
        and charge_type.strip()
        and isinstance(analysis_type, str)
        and analysis_type.strip()
        and charge_type.strip().casefold() != analysis_type.strip().casefold()
    ):
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: charge and analysis material types differ"
        )
    caveat = values.get("composition_heterogeneity_caveat")
    if not isinstance(caveat, str) or not caveat.strip():
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: split heterogeneity caveat is required"
        )
    composition = candidate.oxide_composition_wt_pct
    if not isinstance(composition, Mapping) or len(composition) < 2:
        return ExternalSampleAnalysisRefusal(
            "external sample analysis refused: no usable bulk oxide suite"
        )
    locator_parts = [f"Table {table}", f"p. {page}"]
    pdf_page_index = locator.get("pdf_page_index")
    if pdf_page_index not in (None, ""):
        locator_parts.append(f"PDF page index {pdf_page_index}")
    locator_parts.append(f"row {row}")
    return ExternalSampleAnalysisLink(
        source_reference=(
            f"{source_id}::{observation_id}; " + ", ".join(locator_parts)
        ),
        sample_number=sample_number,
        heterogeneity_caveat=caveat.strip(),
        oxide_composition_wt_pct=composition,
    )


def external_sample_analysis_notice(
    *,
    source_reference: str,
    heterogeneity_caveat: str,
    quantity: Quantity,
    origin: str,
) -> Notice:
    """Expose external bulk-analysis provenance without blocking scoring."""

    return Notice(
        kind=NoticeKind.ENGINE_MODEL_CAVEAT,
        affected_quantities=(quantity,),
        reason=(
            f"{EXTERNAL_SAMPLE_ANALYSIS_PROXY_FLAG}: source={source_reference}; "
            f"{heterogeneity_caveat}"
        ),
        origin=origin,
        source=source_reference,
    )
