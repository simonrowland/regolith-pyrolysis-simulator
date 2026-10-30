from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

from simulator.accounting.formulas import resolve_species_formula
from simulator.battery.enums import (
    Authority,
    Engine,
    MetricOperation,
    MethodToken,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
    QUANTITY_UNITS,
)
from simulator.battery.identity import quantity_token
from simulator.battery.migrate import (
    REPO_ROOT,
    Migrator,
    convert_evaporation_flux_per_area_to_kg_per_m2_s,
    load_yaml,
    map_method,
)
from simulator.battery.score import (
    ScoreContext,
    predict_with_engine,
    rail_for_quantity,
    score_store,
)
from simulator.battery.records import (
    VACUUM_LANGMUIR_EFFLUX_PO2_RELATION,
    oxygen_efflux_derived_pressure_Pa,
)
from simulator.battery.validity import underdetermined_apparatus


SOURCE_FILES = (
    "kems-037-richter-2002.yaml",
    "kems-010-richter-2007.yaml",
    "mendybaev-2021-cai-low-pressure-h2-evap.yaml",
    "wetzel-gail-2013-sio-arrhenius.yaml",
)


@pytest.fixture(scope="module")
def migrated_flux_sources():
    migrator = Migrator(root=REPO_ROOT)
    for source_file in SOURCE_FILES:
        migrator._migrate_extract(
            REPO_ROOT / "data" / "literature" / "extracts" / source_file
        )
    migrator.finalize()
    return migrator.result


def _flux_rows(result, source_id: str):
    return tuple(
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
        and quantity_token(observation.identity)
        is Quantity.EVAPORATION_FLUX_PER_AREA
    )


def _migrate_edited_extract(tmp_path, source_file: str, edit_extract):
    extract_path = REPO_ROOT / "data" / "literature" / "extracts" / source_file
    document = load_yaml(extract_path)
    edit_extract(document)
    edited_path = tmp_path / source_file
    edited_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    migrator = Migrator(root=REPO_ROOT)
    migrator._migrate_extract(edited_path)
    migrator.finalize()
    return migrator.result


def test_areal_flux_quantity_has_one_canonical_unit_conversion() -> None:
    assert QUANTITY_UNITS[Quantity.EVAPORATION_FLUX_PER_AREA] == "kg_per_m2_per_s"
    assert map_method("vacuum_pyrolysis_furnace").value is (
        MethodToken.VACUUM_CHAMBER_PYROLYSIS
    )
    assert rail_for_quantity(
        Quantity.EVAPORATION_FLUX_PER_AREA, species_formula="SiO"
    ) is Rail.SIO_EVOLUTION

    converted, trail = convert_evaporation_flux_per_area_to_kg_per_m2_s(
        Decimal("1"), "mol cm−2 s−1", "SiO"
    )
    molar_mass = Decimal(
        str(resolve_species_formula("SiO").molar_mass_kg_per_mol())
    )
    assert converted == Decimal("10000") * molar_mass
    assert trail is not None and trail.startswith("areal_molar_flux_to_kg_per_m2_per_s:SiO:")

    converted, trail = convert_evaporation_flux_per_area_to_kg_per_m2_s(
        Decimal("12"), "g m−2 s−1", "SiO"
    )
    assert converted == Decimal("0.012")
    assert trail == "g_per_m2_per_s_to_kg_per_m2_per_s"


def test_extract_flux_rows_keep_their_composition_basis_and_efflux_pressure(
    migrated_flux_sources,
) -> None:
    richter_2002 = _flux_rows(migrated_flux_sources, "kems-037-richter-2002")
    richter_2007 = _flux_rows(migrated_flux_sources, "kems-010-richter-2007")
    mendybaev_2021 = _flux_rows(
        migrated_flux_sources, "mendybaev-2021-cai-low-pressure-h2-evap"
    )
    assert (len(richter_2002), len(richter_2007), len(mendybaev_2021)) == (10, 32, 1)

    for observation in (*richter_2002, *richter_2007, *mendybaev_2021):
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition.is_value
        assert observation.identity.fO2_Pa.is_value
        condition = observation.point_conditions["fO2_Pa"]
        assert condition.inference is not None
        assert condition.inference.relation == VACUUM_LANGMUIR_EFFLUX_PO2_RELATION
        assert oxygen_efflux_derived_pressure_Pa(condition) == condition.state.value.point
        assert condition.state.value.point > Decimal("0.0001")  # above 1e-9 bar floor

    for observation in richter_2007:
        disagreements = [
            notice
            for notice in observation.notices
            if notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        ]
        assert any(
            "end-of-run residue" in notice.reason
            and "interval average" in notice.reason
            for notice in disagreements
        )
    r3_10 = next(
        observation
        for observation in richter_2007
        if "_r3-10" in observation.observation_id
    )
    final_composition = r3_10.point_conditions["printed_composition"].state.value
    assert Decimal(str(final_composition["SiO2"])) == Decimal("31.52")

    mendy = mendybaev_2021[0]
    assert mendy.point_conditions["composition"].locator.note.startswith(
        "CAI4B2 starting melt"
    )
    assert mendy.locator.paragraph == "average evaporation rates JSi"


def test_richter_2002_rows_score_against_the_study_residuals(migrated_flux_sources) -> None:
    source_id = "kems-037-richter-2002"
    references = _flux_rows(migrated_flux_sources, source_id)
    work_id = migrated_flux_sources.experiments[references[0].experiment_id].work_id
    context = ScoreContext(
        works=migrated_flux_sources.works,
        experiments=migrated_flux_sources.experiments,
        observations=migrated_flux_sources.observations,
        extract_review={source_id: "draft"},
        root=REPO_ROOT,
    )
    residuals, candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.SIO_EVOLUTION,
        work_id=work_id,
        include_diagnostics=False,
    )
    numeric = {row.reference: row for row in residuals if row.numeric is not None}
    assert len(numeric) == 10
    assert len(candidates) == 10

    # Independent values are the study's measured-flux residuals rounded to
    # 0.01 dex; the 10% area uncertainty is 0.041 dex.
    expected_dex = {
        "B-133-11": Decimal("0.13"),
        "B-133-12": Decimal("0.10"),
        "B-133-13": Decimal("0.25"),
        "B-133-15": Decimal("0.21"),
        "B-133-16": Decimal("0.20"),
        "B-133-17": Decimal("0.19"),
        "B-133-19": Decimal("0.15"),
        "B-113-8": Decimal("0.18"),
        "B-113-9": Decimal("0.13"),
        "B-113-10": Decimal("0.20"),
    }
    by_sample = {
        row.reference.rsplit("_", 1)[-1].upper(): row
        for row in numeric.values()
    }
    assert set(by_sample) == set(expected_dex)
    for sample, expected in expected_dex.items():
        actual = by_sample[sample].numeric
        assert actual.operation is MetricOperation.DEX
        assert abs(actual.value - expected) <= Decimal("0.041")


def test_score_preserves_evaporation_alpha_provenance_and_domain(
    migrated_flux_sources,
) -> None:
    reference = _flux_rows(migrated_flux_sources, "kems-037-richter-2002")[0]

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, reference)

    assert prediction.value is not None
    assert "10.1051/0004-6361/201220803" in prediction.coefficient_sources
    assert prediction.authority is Authority.EXTRAPOLATED
    proxy_notice = next(
        notice
        for notice in prediction.notices
        if notice.kind is NoticeKind.FALLBACK
        and notice.origin == "coefficient:evaporation_alpha:SiO"
    )
    assert proxy_notice.source == "Wetzel&Gail 2013 DOI 10.1051/0004-6361/201220803"
    assert "proxy_tag=solid_film_growth_proxy_not_evaporation_evidence" in proxy_notice.reason
    assert "status=UNCERTIFIED" in proxy_notice.reason
    domain_notice = next(
        notice
        for notice in prediction.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.origin == "coefficient:evaporation_alpha:SiO"
    )
    assert "extrapolated beyond valid_range_K [1000, 1800]" in domain_notice.reason
    assert domain_notice.band == "1000.0–1800.0 K"
    assert domain_notice.authority is Authority.EXTRAPOLATED


def test_missing_efflux_pressure_is_a_typed_refusal(migrated_flux_sources) -> None:
    reference = _flux_rows(migrated_flux_sources, "kems-037-richter-2002")[0]
    conditions = {
        name: value
        for name, value in reference.point_conditions.items()
        if name != "fO2_Pa"
    }
    without_derived_pressure = replace(reference, point_conditions=conditions)

    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, without_derived_pressure)

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {"reason": "missing_efflux_derived_pO2"}


def test_richter_2002_unpaired_si_flux_is_retained_and_refused(tmp_path) -> None:
    def remove_mg_flux(document):
        contexts = document["species"]["Mg"]["context"]
        context = next(
            row
            for row in contexts
            if row.get("values", {}).get("sample") == "B-133-11"
        )
        context["values"].pop("value_as_printed")

    result = _migrate_edited_extract(
        tmp_path, "kems-037-richter-2002.yaml", remove_mg_flux
    )
    rows = _flux_rows(result, "kems-037-richter-2002")
    candidate = next(row for row in rows if "b-133-11" in row.observation_id.casefold())

    assert len(rows) == 10
    assert candidate.identity.fO2_Pa.is_unknown
    assert "fO2_Pa" not in candidate.point_conditions
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate)
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {"reason": "missing_efflux_derived_pO2"}


def test_richter_2007_unpaired_series_row_does_not_inherit_parent_pressure(
    tmp_path,
) -> None:
    def remove_mg_flux(document):
        observations = document["species"]["Mg"]["observations"]
        found = False
        for observation in observations:
            values = observation.get("values", {})
            if values.get("quantity") != "published_mass_loss_and_reduced_flux_time_series":
                continue
            for item in values.get("series", ()):
                if item.get("sample") == "R3-11":
                    item.pop("J_mol_cm2_s")
                    found = True
        assert found

    result = _migrate_edited_extract(
        tmp_path, "kems-010-richter-2007.yaml", remove_mg_flux
    )
    rows = _flux_rows(result, "kems-010-richter-2007")
    candidate = next(row for row in rows if "r3-11" in row.observation_id.casefold())

    assert len(rows) == 32
    assert candidate.identity.fO2_Pa.is_unknown
    assert "fO2_Pa" not in candidate.point_conditions
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, candidate)
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail == {"reason": "missing_efflux_derived_pO2"}


def test_areal_flux_guard_does_not_require_a_second_area_division(
    migrated_flux_sources,
) -> None:
    observation = _flux_rows(migrated_flux_sources, "kems-037-richter-2002")[0]
    experiment = migrated_flux_sources.experiments[observation.experiment_id]

    outcome = underdetermined_apparatus(
        experiment, Quantity.EVAPORATION_FLUX_PER_AREA, observation=observation
    )

    assert outcome.passed
