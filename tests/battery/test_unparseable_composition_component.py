"""Decimal-subscript oxides take a JANAF molar mass. Both-fail names refuse one row."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.accounting.exceptions import (
    AccountingError,
    UnparseableCompositionComponent,
)
from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    atoms_parse_formula_else_janaf,
    parse_formula,
)
from simulator.battery.composition import composition_wt_pct
from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    Rail,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.fo2_ladder import _weight_percent
from simulator.battery.records import Composition, State
from simulator.battery.score import ScoreContext, compile_residual, score_store
from simulator.battery.validate import _term_composition
from simulator.diagnostic_helpers.binary_pot_scoring import oxide_molar_mass_g_mol
from tests.battery.factories import (
    activity_identity,
    migrate_one_extract,
    observation,
    tabulation_experiment,
    work,
)


def _moles(*pairs: tuple[str, str]) -> Composition:
    return Composition(
        "oxides",
        tuple((name, Decimal(amount)) for name, amount in pairs),
        AmountBasis.MOLE_FRACTION,
    )


def test_equal_mole_feo15_sio2_weight_percent() -> None:
    weights = composition_wt_pct(_moles(("FeO1.5", "1"), ("SiO2", "1")))
    assert weights is not None
    assert weights["FeO1.5"] == pytest.approx(57.061028, abs=1e-6)
    assert weights["SiO2"] == pytest.approx(42.938972, abs=1e-6)
    ladder = _weight_percent(_moles(("FeO1.5", "1"), ("SiO2", "1")), None, None, None)
    assert ladder is not None
    assert ladder["FeO1.5"] == pytest.approx(57.061028, abs=1e-6)
    assert ladder["SiO2"] == pytest.approx(42.938972, abs=1e-6)


def test_ko05_nao05_and_cro15_use_the_janaf_fallback() -> None:
    atomic = ATOMIC_WEIGHTS_G_PER_MOL
    silica = atomic["Si"] + 2.0 * atomic["O"]
    expected = {
        "KO0.5": atomic["K"] + 0.5 * atomic["O"],
        "NaO0.5": atomic["Na"] + 0.5 * atomic["O"],
        "CrO1.5": atomic["Cr"] + 1.5 * atomic["O"],
        "FeO1.5": atomic["Fe"] + 1.5 * atomic["O"],
    }
    for name, molar_mass in expected.items():
        assert oxide_molar_mass_g_mol(name) == pytest.approx(molar_mass, abs=1e-9)
        weights = composition_wt_pct(_moles((name, "1"), ("SiO2", "1")))
        assert weights is not None
        share = 100.0 * molar_mass / (molar_mass + silica)
        assert weights[name] == pytest.approx(share, abs=1e-6)
        assert weights["SiO2"] == pytest.approx(100.0 - share, abs=1e-6)
    assert dict(_term_composition("KO0.5") or ()) == {"K": 1.0, "O": 0.5}
    assert dict(_term_composition("FeO1.5") or ()) == {"Fe": 1.0, "O": 1.5}
    for formula in ("FeO1.5", "KO0.5", "NaO0.5", "CrO1.5", "CuSO4.5H2O"):
        assert _term_composition(formula) == atoms_parse_formula_else_janaf(formula)


def test_successful_parse_formula_wins_over_janaf_atoms() -> None:
    parsed = parse_formula("CuSO4.5H2O")
    assert oxide_molar_mass_g_mol("CuSO4.5H2O") == parsed.molar_mass_g_mol
    assert parsed.molar_mass_g_mol == pytest.approx(249.677, abs=1e-6)
    hydrate = dict(_term_composition("CuSO4.5H2O") or ())
    assert hydrate["H"] == 10.0
    assert hydrate["O"] == 9.0
    weights = composition_wt_pct(_moles(("SiO2", "1"), ("MgO", "1")))
    assert weights is not None
    assert weights["SiO2"] == pytest.approx(59.851375178060906, abs=1e-12)
    assert weights["MgO"] == pytest.approx(40.148624821939094, abs=1e-12)


def test_openimcc_printed_ko05_compile_residual_does_not_raise() -> None:
    identity = activity_identity(
        formula="SiO2",
        component_basis="SiO2",
        composition=_moles(("KO0.5", "0.21537"), ("SiO2", "0.78463")),
    )
    experiment = tabulation_experiment()
    row = observation(
        "eliezer-ko05",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_REDUCED,
        source_id="eliezer-howald-1978-potassium-silicate-vapor-pressure",
    )
    context = ScoreContext(
        works={experiment.work_id: work()},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row},
        hostname="test",
    )
    try:
        residual, _candidate = compile_residual(row, Engine.OPENIMCC, context=context)
    except AccountingError as exc:
        raise AssertionError(
            f"KO0.5 composition parse escaped compile_residual: {exc}"
        ) from exc
    assert residual is not None
    detail = {} if residual.refusal is None else residual.refusal.detail
    assert detail.get("reason") != "unparseable_composition_component"
    assert detail.get("component") != "KO0.5"


def test_score_store_refuses_one_unparseable_name_and_scores_the_other_row() -> None:
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    good = result.observations[f"{extract.stem}::borisov_2018_table2_row_4"]
    composition = good.identity.composition.value
    bad_components = (("foobar", composition.components[0][1]),) + tuple(
        composition.components[1:]
    )
    bad_id = f"{good.observation_id}::unparseable-foobar"
    bad = replace(
        good,
        observation_id=bad_id,
        identity=replace(
            good.identity,
            composition=State.of(replace(composition, components=bad_components)),
        ),
    )
    context = ScoreContext(
        result.works,
        result.experiments,
        {good.observation_id: good, bad_id: bad},
        origins={
            good.observation_id: extract.name,
            bad_id: extract.name,
        },
        benches=result.benches,
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        include_diagnostics=False,
    )
    by_reference: dict[str, list] = {}
    for residual in residuals:
        by_reference.setdefault(residual.reference, []).append(residual)
    assert set(by_reference) == {good.observation_id, bad_id}

    good_residuals = by_reference[good.observation_id]
    assert good_residuals
    assert any(residual.numeric is not None for residual in good_residuals)

    bad_residuals = by_reference[bad_id]
    assert bad_residuals
    assert all(residual.status is ResidualStatus.REFUSED for residual in bad_residuals)
    assert all(residual.numeric is None for residual in bad_residuals)
    assert any(
        residual.refusal is not None
        and residual.refusal.reason is RefusalReason.INVALID_SOURCE
        and residual.refusal.detail.get("reason") == "unparseable_composition_component"
        and residual.refusal.detail.get("component") == "foobar"
        for residual in bad_residuals
    )
    with pytest.raises(UnparseableCompositionComponent) as raised:
        _weight_percent(
            _moles(("foobar", "1"), ("SiO2", "1")),
            None,
            None,
            None,
        )
    assert raised.value.component == "foobar"
