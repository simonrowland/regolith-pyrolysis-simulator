"""Source-located QMS calibration metadata and pressure gate contract."""

from __future__ import annotations

from pathlib import Path

from simulator.battery.migrate import experiment_from_plain, to_plain
from simulator.battery.records import Located
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/kems-022-demaria-1971.yaml"
CALIBRATION_ID = "demaria_1971_silver_and_o_o2_calibration"


def test_demaria_qms_calibration_migrates_with_scoped_located_fields() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()

    experiment = next(
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.experiment_id.endswith("::demaria-1971-main-kems")
    )
    assert experiment.apparatus is not None
    calibration = experiment.apparatus.calibration
    assert calibration is not None
    assert set(calibration) == {
        "standard@all_species",
        "standard@species=O",
        "standard@species=O2",
        "procedure_kind@species=O",
        "procedure_kind@species=O2",
        "method@all_species",
        "sensitivity_factor@all_species",
        "sensitivity_factor_units@all_species",
        "cross_section_source@all_species",
        "fragment_correction_state@species=O",
        "fragment_correction_state@species=O2",
        "pressure_uncertainty@all_species",
    }

    for field, located in calibration.items():
        assert isinstance(located, Located), field
        assert located.locator is not None, field
        assert located.locator.record == CALIBRATION_ID, field
        assert located.locator.page is not None, field
        assert located.locator.published_page is not None, field
        assert located.locator.pdf_page_index is not None, field

    silver = calibration["standard@all_species"]
    assert silver.state.is_value
    assert "Inghram and Drowart 1960" in str(silver.state.value)
    assert silver.locator is not None
    assert (silver.locator.page, silver.locator.published_page) == (2, 1368)
    assert silver.locator.pdf_page_index == 1

    oxygen = calibration["standard@species=O2"]
    assert oxygen.state.is_unknown
    assert "whenever possible" in str(oxygen.state.reason)
    assert "satisfactory at high temperatures" in str(oxygen.state.reason)
    assert "less reliable at lower temperatures" in str(oxygen.state.reason)
    assert oxygen.locator is not None
    assert oxygen.locator.page == 2
    assert oxygen.locator.published_page == 1368
    assert oxygen.locator.pdf_page_index == 1
    assert "Brix and Herzberg (1954)" in str(oxygen.locator.paragraph)
    assert "JANAF (1965)" in str(oxygen.locator.paragraph)
    atomic_oxygen = calibration["standard@species=O"]
    assert atomic_oxygen.state.is_unknown
    assert "whenever possible" in str(atomic_oxygen.state.reason)
    assert atomic_oxygen.locator is not None
    assert "O+/O2+ intensity ratio" in str(atomic_oxygen.locator.paragraph)
    assert atomic_oxygen.locator.page == 2
    assert atomic_oxygen.locator.published_page == 1368
    assert atomic_oxygen.locator.pdf_page_index == 1
    assert "Brix and Herzberg (1954)" in str(atomic_oxygen.locator.paragraph)
    assert "JANAF (1965)" in str(atomic_oxygen.locator.paragraph)

    factor = calibration["sensitivity_factor@all_species"]
    units = calibration["sensitivity_factor_units@all_species"]
    assert factor.state.is_unknown
    assert units.state.is_unknown
    assert factor.locator is not None and factor.locator.equation == "1"
    assert units.locator is not None and units.locator.equation == "1"
    assert not factor.state.is_value
    assert not units.state.is_value

    cross_section = calibration["cross_section_source@all_species"]
    assert cross_section.state.is_value
    assert "Mann 1967" in str(cross_section.state.value)
    assert "30 or 70 eV" in str(cross_section.state.value)
    method = calibration["method@all_species"]
    assert method.state.is_value
    assert "P = I_i^+ T / (S sigma gamma a_i)" in str(method.state.value)
    assert "fragment_correction_state@all_species" not in calibration
    for species in ("O", "O2"):
        procedure = calibration[f"procedure_kind@species={species}"]
        assert procedure.state.is_value
        assert procedure.state.value == "conditional_o_o2"
        assert procedure.locator is not None
        assert "conditional O/O2 procedure" in str(procedure.locator.paragraph)
        fragment = calibration[f"fragment_correction_state@species={species}"]
        assert fragment.state.is_unknown
        assert "conditional O/O2 procedure" in str(fragment.state.reason)
        assert "application to this grouped experiment is unknown" in str(
            fragment.state.reason
        )
        assert fragment.locator is not None
        assert "following equation 1" in str(fragment.locator.paragraph)
    uncertainty = calibration["pressure_uncertainty@all_species"]
    assert uncertainty.state.is_value
    assert "factor of 2" in str(uncertainty.state.value)
    assert uncertainty.locator is not None
    assert (uncertainty.locator.page, uncertainty.locator.published_page) == (9, 1375)
    assert uncertainty.locator.pdf_page_index == 8

    restored = experiment_from_plain(to_plain(experiment))
    assert restored == experiment
    assert restored.apparatus is not None
    assert restored.apparatus.calibration == calibration

    assert not any(
        observation.observation_id.endswith(CALIBRATION_ID)
        for observation in migrator.result.observations.values()
    )
