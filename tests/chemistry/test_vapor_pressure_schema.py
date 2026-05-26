"""Schema guard for fallback vapor-pressure convention metadata."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"

VALID_FIT_TARGETS = {
    "pure_component_psat",
    "pseudo_psat_backsolved_from_vaporock",
    "standard_reaction_term",
}
BACKSOLVE_FIELDS = {
    "feedstock_grid",
    "fO2_convention",
    "activity_formula",
    "target",
    "residual_dex",
}
REACTION_FIELDS = {
    "formula",
    "exponent_oxide",
    "exponent_pO2",
    "basis",
}


def _species_rows() -> tuple[tuple[str, str, dict], ...]:
    data = yaml.safe_load(VAPOR_PRESSURES_PATH.read_text()) or {}
    rows: list[tuple[str, str, dict]] = []
    for section in ("metals", "oxide_vapors"):
        for species, row in (data.get(section) or {}).items():
            rows.append((section, species, row or {}))
    return tuple(rows)


SPECIES_ROWS = _species_rows()


@pytest.mark.parametrize(
    ("section", "species", "row"),
    SPECIES_ROWS,
    ids=[f"{section}:{species}" for section, species, _ in SPECIES_ROWS],
)
def test_vapor_pressure_rows_declare_fit_target_schema(section, species, row):
    fit_target = row.get("fit_target")

    assert fit_target in VALID_FIT_TARGETS

    if fit_target == "pure_component_psat":
        assert row.get("source")
        assert not row.get("backsolve")
        assert not row.get("reaction")
        if row.get("consumer_status") is not None:
            assert row["consumer_status"] == "inactive"
        return

    if fit_target == "pseudo_psat_backsolved_from_vaporock":
        backsolve = row.get("backsolve") or {}
        assert set(backsolve) >= BACKSOLVE_FIELDS
        for field in BACKSOLVE_FIELDS - {"residual_dex"}:
            assert backsolve[field]
        assert isinstance(backsolve["residual_dex"], (int, float))
        assert backsolve["residual_dex"] >= 0.0
        return

    reaction = row.get("reaction") or {}
    assert fit_target == "standard_reaction_term"
    assert set(reaction) >= REACTION_FIELDS
    for field in REACTION_FIELDS - {"exponent_oxide", "exponent_pO2"}:
        assert reaction[field]
    assert reaction["exponent_oxide"] == pytest.approx(
        float(row["oxide_activity_exponent"])
    )
    assert reaction["exponent_pO2"] == pytest.approx(float(row["pO2_exponent"]))
