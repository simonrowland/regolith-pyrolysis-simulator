"""SC-458 echo guard, d-323 analysed-melt input, and the d-325 bulk hook.

Tokens and refusal enums are the expected values. Amounts are hand literals
or the printed extract. The FeO_T factor is the ruling's masses, computed
here and not imported from the owner.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    Phase,
    PhaseRole,
    Quantity,
    RefusalReason,
)
from simulator.battery.identity import Identity
from simulator.battery.input_provenance import (
    phase_map_provenance,
    printed_single_melt_evidence,
    single_phase_printed_bulk_policy,
)
from simulator.battery.records import Composition, Derivation, Locator, Species
from simulator.battery.score import (
    _fe_redox_material,
    _input_composition_echo_detail,
    _predict_fe_redox,
    _predict_phase_row,
)
from simulator.state_types import State
from simulator.melt_backend.base import EquilibriumResult
from tests.battery import factories as F

SEAT = Path(__file__).resolve().parents[2]
_FEO_MASS_TIMES_TWO = Decimal("143.69")
_FE2O3_MASS = Decimal("159.69")
SULFIDE_EQUATION = (
    "log10 C_S = log10(wt% S) + 0.5 log10(pO2/pS2), Fincham and Richardson"
)
GOROJOVSKY_CRYSTALLINE = (
    "Many products contain quenched glass and crystalline phases. "
    "Electron dispersive spectroscopy identified the crystalline phases "
    "as predominantly plagioclase, olivine, and clinopyroxene."
)

_MIGRATOR: dict[str, object] = {}


def _glass(provenance: str | None, **oxides: str) -> Composition:
    kwargs: dict[str, object] = {
        "basis": "printed_oxides",
        "components": tuple((name, Decimal(amount)) for name, amount in oxides.items()),
        "amount_basis": AmountBasis.MOLE_FRACTION,
    }
    if provenance is not None:
        kwargs["input_provenance"] = provenance
    return Composition(**kwargs)  # type: ignore[arg-type]


def _identity(
    formula: str,
    composition: Composition,
    quantity: Quantity = Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
    *,
    role: PhaseRole = PhaseRole.GLASS,
    temperature_K: Decimal | None = None,
    fO2_Pa: Decimal | None = None,
    total_pressure_Pa: Decimal | None = None,
    subtype: str | None = None,
) -> Identity:
    phase = Phase.GLASS if role is PhaseRole.GLASS else Phase.L
    kwargs: dict[str, object] = {
        "quantity": quantity,
        "species": Species(formula, phase),
        "composition": State.of(composition),
        "phase_role": State.of(role),
    }
    if temperature_K is not None:
        kwargs["temperature_K"] = State.of(temperature_K)
    if fO2_Pa is not None:
        kwargs["fO2_Pa"] = State.of(fO2_Pa)
    if total_pressure_Pa is not None:
        kwargs["total_pressure_Pa"] = State.of(total_pressure_Pa)
    if subtype is not None:
        kwargs["subtype"] = State.of(subtype)
    return Identity(**kwargs)  # type: ignore[arg-type]


def _block_equilibrate(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("equilibrate must not run")

    monkeypatch.setattr("simulator.battery.score._equilibrate_phase_assemblage", boom)


def _liquid(
    fraction: float,
    components: dict[str, float] | None = None,
) -> EquilibriumResult:
    return EquilibriumResult(
        status="ok",
        liquid_fraction=fraction,
        phase_compositions={"liquid": components or {"SiO2": 42.0, "Al2O3": 17.0}},
        phases_present=["liquid"],
    )


def _experiment(characterization: str | None = None):
    experiment = F.tabulation_experiment()
    if characterization is None:
        return experiment
    return replace(
        experiment,
        sample=replace(
            experiment.sample,
            characterization=F.located(characterization),
        ),
    )


def _predict(observation, identity, experiment, result=None):
    return _predict_phase_row(
        Engine.ALPHAMELTS,
        observation,
        identity,
        "alphamelts",
        ("test",),
        experiment=experiment,
    )


def _load(filename: str):
    from simulator.battery.migrate import Migrator

    migrator = _MIGRATOR.get("migrator")
    done = _MIGRATOR.setdefault("done", set())
    if migrator is None:
        migrator = Migrator(SEAT)
        _MIGRATOR["migrator"] = migrator
    assert isinstance(done, set)
    if filename not in done:
        migrator._migrate_extract(SEAT / "data/literature/extracts" / filename)
        done.add(filename)
    return migrator


def _observation(filename: str, observation_id: str):
    migrator = _load(filename)
    matches = [
        item
        for item in migrator.result.observations.values()
        if item.observation_id == observation_id
        or item.observation_id.endswith("::" + observation_id)
    ]
    assert len(matches) == 1, observation_id
    observation = matches[0]
    experiment = migrator.result.experiments.get(observation.experiment_id)
    if experiment is None:
        experiment = next(
            (
                item
                for item in migrator.result.experiments.values()
                if item.experiment_id == observation.experiment_id
            ),
            None,
        )
    return observation, experiment


def _located_text(located: object) -> str:
    state = getattr(located, "state", None)
    if state is not None and getattr(state, "is_value", False):
        raw = state.value
        if isinstance(raw, str):
            return raw
    return ""


def test_analysed_glass_major_is_refused() -> None:
    composition = _glass(
        "scored_phase_analysis",
        MgO="0.179",
        Al2O3="0.092",
        SiO2="0.478",
        FeO="0.057",
        CaO="0.190",
    )
    detail = _input_composition_echo_detail(_identity("MgO", composition))
    assert detail is not None
    assert detail["detail"] == "input_composition_is_scored_phase_analysis"
    assert detail["reason"] == RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
    assert detail["input_provenance"] == "scored_phase_analysis"
    assert detail["formula"] == "MgO"


def test_printed_starting_bulk_is_not_refused() -> None:
    composition = _glass(
        "printed_starting_bulk",
        SiO2="0.64",
        Al2O3="0.17",
        MgO="0.02",
    )
    assert _input_composition_echo_detail(_identity("SiO2", composition)) is None


def test_named_material_mixture_of_printed_starts_is_not_refused() -> None:
    composition = _glass(
        "named_material_mixture_of_printed_starts",
        SiO2="0.50",
        MgO="0.50",
    )
    assert _input_composition_echo_detail(_identity("SiO2", composition)) is None


def test_mass_percent_phase_stamp_of_the_analysis_is_refused_before_basis_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    composition = Composition(
        basis="printed_phase_composition",
        components=(("SiO2", Decimal("62.2")), ("Al2O3", Decimal("22.5"))),
        amount_basis=AmountBasis.MASS_PERCENT,
        input_provenance="scored_phase_analysis",
    )
    identity = _identity("SiO2", composition)
    detail = _input_composition_echo_detail(identity)
    assert detail is not None
    assert detail["reason"] == "input_composition_is_scored_phase_analysis"
    observation = F.observation(
        "mass-percent-stamp",
        "exp-1",
        identity,
        Decimal("62.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, None)
    assert prediction.refusal_reason is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS
    assert prediction.refusal_reason is not RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_d323_dissolved_sulfur_minus_scored_component_is_not_refused() -> None:
    composition = _glass(
        "analysed_melt_minus_scored",
        SiO2="0.50",
        Al2O3="0.10",
        MgO="0.10",
        FeOT="0.10",
    )
    assert _input_composition_echo_detail(_identity("S", composition)) is None


def test_d323_refuses_when_the_scored_sulfur_is_still_a_component() -> None:
    composition = _glass("scored_phase_analysis", SiO2="0.50", S="0.01")
    detail = _input_composition_echo_detail(_identity("S", composition))
    assert detail is not None


def test_redox_split_is_refused_and_feo_t_is_not() -> None:
    split = _glass("scored_phase_analysis", SiO2="0.50", FeO="0.05", Fe2O3="0.02")
    total = _glass("scored_phase_analysis", SiO2="0.50", FeOT="0.07")
    assert (
        _input_composition_echo_detail(_identity("Fe", split, Quantity.FE3_FE2_RATIO))
        is not None
    )
    assert (
        _input_composition_echo_detail(_identity("Fe", total, Quantity.FE3_FE2_RATIO))
        is None
    )


def test_same_run_initial_melt_uses_the_typed_reason() -> None:
    composition = _glass(
        "same_run_initial_analysed_melt",
        SiO2="0.50",
        Al2O3="0.17",
        MgO="0.08",
    )
    detail = _input_composition_echo_detail(_identity("SiO2", composition))
    assert detail is not None
    assert detail["detail"] == "input_is_same_run_analysed_melt"
    assert detail["reason"] == RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
    assert detail["formula"] == "SiO2"


def test_unclassified_composition_is_not_refused_by_this_guard() -> None:
    composition = _glass(None, SiO2="0.50", MgO="0.50")
    assert composition.input_provenance is None
    assert _input_composition_echo_detail(_identity("SiO2", composition)) is None


def test_echo_precedes_missing_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_equilibrate(monkeypatch)
    composition = _glass("scored_phase_analysis", SiO2="0.5", MgO="0.5")
    identity = _identity("MgO", composition)
    observation = F.observation(
        "echo-no-temperature",
        "exp-1",
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, None)
    assert prediction.refusal_reason is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS
    assert prediction.refusal_detail["formula"] == "MgO"


def test_major_oxide_of_an_analysed_melt_stays_an_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    composition = _glass("scored_phase_analysis", SiO2="0.60", Al2O3="0.20", MgO="0.20")
    identity = _identity(
        "SiO2",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "major-oxide",
        "exp-1",
        identity,
        Decimal("60"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(
        observation,
        identity,
        _experiment("The run was crystal-free and glass-only."),
    )
    assert prediction.refusal_reason is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS


def test_crystal_silent_dissolved_sulfur_refuses_unstated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    composition = _glass("scored_phase_analysis", SiO2="0.70", Al2O3="0.20", MgO="0.10")
    identity = _identity(
        "S",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "silent-sulfur",
        "exp-1",
        identity,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, _experiment())
    assert prediction.refusal_reason is RefusalReason.INPUT_PHASE_ASSEMBLAGE_UNSTATED
    assert prediction.refusal_detail["detail"] == "input_phase_assemblage_unstated"


def test_crystalline_analysed_melt_refuses_before_equilibrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    composition = _glass("scored_phase_analysis", SiO2="0.70", Al2O3="0.20", MgO="0.10")
    identity = _identity(
        "S",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "crystalline-sulfur",
        "exp-1",
        identity,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, _experiment(GOROJOVSKY_CRYSTALLINE))
    assert prediction.refusal_reason is RefusalReason.INPUT_PHASE_ASSEMBLAGE_CRYSTALLINE
    assert prediction.refusal_detail["detail"] == "input_phase_assemblage_crystalline"


def test_fe3_row_feeds_feo_t_not_the_split(monkeypatch: pytest.MonkeyPatch) -> None:
    feo = Decimal("6.77")
    fe2o3 = Decimal("2.10")
    expected = float(feo + (_FEO_MASS_TIMES_TWO / _FE2O3_MASS) * fe2o3)
    composition = Composition(
        basis="printed_oxides",
        components=(
            ("SiO2", Decimal("50")),
            ("FeO", feo),
            ("Fe2O3", fe2o3),
            ("CaO", Decimal("17")),
        ),
        amount_basis=AmountBasis.MASS_PERCENT,
        input_provenance="scored_phase_analysis",
    )
    identity = _identity(
        "Fe",
        composition,
        Quantity.FE3_FE2_RATIO,
        role=PhaseRole.GLASS,
        temperature_K=Decimal("1673.15"),
        fO2_Pa=Decimal("10"),
        total_pressure_Pa=Decimal("100000"),
    )
    observation = F.observation(
        "fe3-collapse",
        "exp-1",
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    experiment = _experiment("The charge remained crystal-free.")
    material = _fe_redox_material(observation, identity, experiment)
    assert not hasattr(material, "reason")
    assert "Fe2O3" not in material.composition_wt
    assert "FeO_T" not in material.composition_wt
    assert material.composition_wt["FeO"] == pytest.approx(expected, abs=1e-9)
    assert any(
        notice.reason.startswith("input_composition_from_analysed_melt:")
        and "FeO_T" in notice.reason
        for notice in material.notices
    )
    from simulator.diagnostic_helpers.binary_pot_battery import composition_kg_and_mol

    kg, _mol = composition_kg_and_mol(material.composition_wt)
    assert kg["FeO"] == pytest.approx(expected / 100.0, abs=1e-9)
    assert kg["FeO"] > 0.0

    seen: dict[str, dict[str, float]] = {}
    import simulator.diagnostic_helpers.binary_pot_battery as pot

    original_pot = pot.identity_battery_pot

    def fake_pot(pot_id: str, composition_wt_pct: dict[str, float], **kwargs: object):
        seen["wt"] = dict(composition_wt_pct)
        return original_pot(pot_id, composition_wt_pct, **kwargs)

    class _Cell:
        status = "ok"
        fe_redox_split = {"FeO_wt_pct": 5.0, "Fe2O3_wt_pct": 2.0}
        notices: list[object] = []
        authority = None
        hostname = "test"
        exit_code = 0
        model_id = "test"

    class _Handle:
        available = True
        backend = object()
        name = "alphamelts"
        supports_intrinsic_fo2 = False

    monkeypatch.setattr(pot, "identity_battery_pot", fake_pot)
    monkeypatch.setattr(pot, "equilibrate_cell", lambda *_a, **_k: _Cell())
    monkeypatch.setattr(
        "simulator.battery.score._open_cached_battery_handle",
        lambda _engine, _handles: (_Handle(), False),
    )
    prediction = _predict_fe_redox(
        Engine.ALPHAMELTS,
        observation,
        identity,
        "alphamelts",
        experiment=experiment,
    )
    assert prediction.refusal_reason is None
    assert "Fe2O3" not in seen["wt"]
    assert "FeO_T" not in seen["wt"]
    assert seen["wt"]["FeO"] == pytest.approx(expected, abs=1e-9)


def test_scss_with_sulfide_passes_and_keeps_a_crystal_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    monkeypatch.setattr(AlphaMELTSBackend, "models_dissolved_sulfur", True)
    calls: list[dict[str, float]] = []

    def hook(*_args: object, **kwargs: object) -> EquilibriumResult:
        composition_kg = kwargs["composition_kg"]
        assert isinstance(composition_kg, dict)
        calls.append(composition_kg)
        return _liquid(0.4, {"S": 0.12, "SiO2": 50.0})

    monkeypatch.setattr("simulator.battery.score._equilibrate_phase_assemblage", hook)
    composition = Composition(
        basis="printed_oxides",
        components=(
            ("SiO2", Decimal("0.50")),
            ("Al2O3", Decimal("0.20")),
            ("MgO", Decimal("0.20")),
            ("FeO", Decimal("0.09")),
            ("S", Decimal("0.01")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
        input_provenance="scored_phase_analysis",
    )
    identity = _identity(
        "S",
        composition,
        Quantity.SULFIDE_CAPACITY,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        fO2_Pa=Decimal("10"),
        total_pressure_Pa=Decimal("100000"),
    )
    observation = F.observation(
        "scss-sulfide",
        "exp-1",
        identity,
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        derivation=Derivation(
            relation=SULFIDE_EQUATION,
            inputs=(),
            parameters=(),
            output_unit="dimensionless",
        ),
    )
    conditions = dict(observation.point_conditions or {})
    conditions["pS2_Pa"] = F.located(Decimal("100"))
    observation = replace(observation, point_conditions=conditions)
    prediction = _predict(
        observation,
        identity,
        _experiment("The melt was held saturated with FeS sulfide."),
    )
    assert prediction.refusal_reason is None
    assert prediction.value is not None
    assert calls and "S" not in calls[0]
    assert any(
        notice.reason == "engine_predicts_crystals_from_analysed_liquid"
        for notice in prediction.notices
    )
    assert any(
        "input_composition_from_analysed_melt:" in notice.reason
        and "S" in notice.reason
        for notice in prediction.notices
    )


def test_missing_liquid_fraction_refuses_an_unstated_printed_bulk() -> None:
    unstated = printed_single_melt_evidence(())
    for fraction in (None, float("nan")):
        policy = single_phase_printed_bulk_policy(
            provenance="printed_starting_bulk",
            quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
            liquid_fraction=fraction,
            evidence=unstated,
        )
        assert policy is not None
        assert (
            policy.refusal
            is RefusalReason.SINGLE_PHASE_BULK_IDENTITY_ASSEMBLAGE_UNSTATED
        )
    crystalline = printed_single_melt_evidence(
        (("sample.characterization", GOROJOVSKY_CRYSTALLINE),)
    )
    assert crystalline.crystalline
    assert (
        single_phase_printed_bulk_policy(
            provenance="printed_starting_bulk",
            quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
            liquid_fraction=None,
            evidence=crystalline,
        )
        is None
    )


def test_printed_bulk_scores_when_the_engine_predicts_crystals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_a, **_k: _liquid(0.4, {"SiO2": 42.0}),
    )
    composition = _glass("printed_starting_bulk", SiO2="0.64", Al2O3="0.17", MgO="0.02")
    identity = _identity(
        "SiO2",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        fO2_Pa=Decimal("10"),
        total_pressure_Pa=Decimal("100000"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "bulk-crystals",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, _experiment())
    assert prediction.refusal_reason is None
    assert prediction.value == Decimal("42.0")
    assert not any(
        "single_phase_bulk_identity" in notice.reason for notice in prediction.notices
    )


def test_printed_bulk_refuses_a_single_liquid_with_positive_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_a, **_k: _liquid(1.0),
    )
    composition = _glass("printed_starting_bulk", SiO2="0.64", Al2O3="0.17", MgO="0.02")
    identity = _identity(
        "SiO2",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        fO2_Pa=Decimal("10"),
        total_pressure_Pa=Decimal("100000"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "bulk-liquid",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(
        observation,
        identity,
        _experiment("The products were crystal-free glass-only."),
    )
    assert prediction.refusal_reason is RefusalReason.SINGLE_PHASE_BULK_IDENTITY


def test_printed_bulk_refuses_a_single_liquid_when_assemblage_is_unstated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_a, **_k: _liquid(1.0),
    )
    composition = _glass(
        "named_material_mixture_of_printed_starts",
        SiO2="0.50",
        MgO="0.50",
    )
    identity = _identity(
        "SiO2",
        composition,
        role=PhaseRole.MELT,
        temperature_K=Decimal("1673.15"),
        fO2_Pa=Decimal("10"),
        total_pressure_Pa=Decimal("100000"),
        subtype="wt_percent",
    )
    observation = F.observation(
        "bulk-unstated",
        "exp-1",
        identity,
        Decimal("40"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = _predict(observation, identity, _experiment())
    assert (
        prediction.refusal_reason
        is RefusalReason.SINGLE_PHASE_BULK_IDENTITY_ASSEMBLAGE_UNSTATED
    )


def test_migrated_bonnand_bare_glass_is_not_stamped_and_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 'glass' is not printed evidence that the map is analysed glass.

    Provenance stays unset, so the echo guard does not fire and the pre-d-323
    path scores a mocked liquid.
    """

    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_a, **_k: _liquid(1.0, {"MgO": 10.0}),
    )
    observation, experiment = _observation(
        "bonnand-bruand-matzen-et-al-2020-redox-chromium-isotope-magnesiochromite.yaml",
        "bonnand_2020_A1_mgo",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance is None
    assert _input_composition_echo_detail(observation.identity) is None
    prediction = _predict(observation, observation.identity, experiment)
    assert prediction.refusal_reason is None
    assert prediction.value == Decimal("10.0")


def test_migrated_borisov_2004_row_refuses_the_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    observation, experiment = _observation(
        "borisov-2004-tio2-pd-ni-fe-solubilities.yaml",
        "borisov2004_t2_nio_row01_composition",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance == "scored_phase_analysis"
    prediction = _predict(observation, observation.identity, experiment)
    assert prediction.refusal_reason is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS
    assert prediction.refusal_detail["formula"] == "NiO"


def test_taniguchi_metal_fe_reaches_the_basis_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metal Fe assay is not analysed glass, so the basis gate runs.

    Equilibrate must not run: the printed mass-percent identity is refused
    first.
    """

    _block_equilibrate(monkeypatch)
    observation, experiment = _observation(
        "taniguchi-1997-feo-cas.yaml",
        "taniguchi_1997_table1_sample_101_metal_fe_composition",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance is None
    prediction = _predict(observation, observation.identity, experiment)
    assert prediction.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_kunisada_final_slag_refuses_before_the_basis_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_equilibrate(monkeypatch)
    observation, experiment = _observation(
        "kunisada-1986-na-silicate-dephosphorization.yaml",
        "kunisada_1986_t1ii_e_equilibrium_SiO2_in_slag",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance == "scored_phase_analysis"
    prediction = _predict(observation, observation.identity, experiment)
    assert prediction.refusal_reason is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS
    assert prediction.refusal_reason is not RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_kim_2014_measured_v_row_is_not_stamped_analysed_glass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 'l' and a V/Mo/Ni mass-percent map are not analysed glass."""

    _block_equilibrate(monkeypatch)
    observation, experiment = _observation(
        "kim-2014-carbon-fe-v-mo-ni.yaml",
        "kim_2014_carbon_fe_v_mo_ni_p003_t01_r02_c02",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance is None
    prediction = _predict(observation, observation.identity, experiment)
    assert prediction.refusal_reason is RefusalReason.COEFFICIENT_BASIS_MISMATCH


def test_gorojovsky_starting_bulk_passes_the_guard() -> None:
    observation, _experiment_row = _observation(
        "gorojovsky-wood-2026-sulfur-crustal.yaml",
        "t2-r002-sio2",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance == "printed_starting_bulk"
    assert _input_composition_echo_detail(observation.identity) is None


def test_gorojovsky_crystal_bearing_products_score_with_the_liquid_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "simulator.battery.score._equilibrate_phase_assemblage",
        lambda *_a, **_k: _liquid(1.0, {"SiO2": 64.2}),
    )
    observation, experiment = _observation(
        "gorojovsky-wood-2026-sulfur-crustal.yaml",
        "t2-r002-sio2",
    )
    assert experiment is not None
    assert "olivine" not in _located_text(experiment.sample.characterization).casefold()
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            characterization=F.located(GOROJOVSKY_CRYSTALLINE),
        ),
    )
    identity = observation.identity
    if identity.fO2_Pa is None or not identity.fO2_Pa.is_value:
        identity = replace(identity, fO2_Pa=State.of(Decimal("10")))
    if identity.total_pressure_Pa is None or not identity.total_pressure_Pa.is_value:
        identity = replace(identity, total_pressure_Pa=State.of(Decimal("100000")))
    prediction = _predict(observation, identity, experiment)
    assert prediction.refusal_reason is None
    assert prediction.value == Decimal("64.2")
    assert any(
        notice.reason.startswith("engine_predicts_liquid_experiment_crystalline")
        for notice in prediction.notices
    )


def test_kc91_table3_final_refuses_same_run_initial_melt() -> None:
    observation, _experiment = _observation(
        "kress-carmichael-1991-iron-redox-calibration.yaml",
        "kc91_t3_0011_0014_final_SiO2",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_unknown
    assert composition.reason == "input_is_same_run_analysed_melt"


def test_t1593_mixture_token_passes_the_guard(tmp_path: Path) -> None:
    from simulator.battery.migrate import migrate
    from tests.battery.test_named_material_mixture_composition import (
        _document,
        _write_tree,
    )

    result = migrate(_write_tree(tmp_path, _document()), write=False)
    mixture = result.observations["fixture-source::fig3_mg_jsc1a"]
    composition = mixture.identity.composition.value
    assert composition.input_provenance == "named_material_mixture_of_printed_starts"
    identity = _identity("SiO2", composition, role=PhaseRole.MELT)
    assert _input_composition_echo_detail(identity) is None


def test_batched_and_nominal_match_as_words() -> None:
    assert (
        phase_map_provenance(Locator(table="1", note="batched charge"), None)
        == "printed_starting_bulk"
    )
    assert (
        phase_map_provenance(Locator(table="1", note="nominal bulk"), None)
        == "printed_starting_bulk"
    )
    assert (
        phase_map_provenance(
            Locator(table="1", note="unbatched and nominally dry"),
            None,
        )
        is None
    )
    assert (
        phase_map_provenance(None, None, phase_text="quenched peridotite glass")
        == "scored_phase_analysis"
    )
    assert (
        phase_map_provenance(
            None, None, phase_text="l quenched silicate glass"
        )
        == "scored_phase_analysis"
    )
    assert (
        phase_map_provenance(
            None,
            None,
            phase_text="liquid slag at 1773 K, analyzed after quench",
        )
        == "scored_phase_analysis"
    )
    assert phase_map_provenance(None, None, phase_text="glass") is None
    assert phase_map_provenance(None, None, phase_text="quench crystals") is None
    assert (
        phase_map_provenance(
            None,
            None,
            phase_text="quenched slag; whether it formed glass is not stated",
        )
        is None
    )
    assert (
        phase_map_provenance(
            None,
            None,
            phase_text="quenched slag; whether the sample is glass is not stated",
        )
        is None
    )
    assert (
        phase_map_provenance(None, None, phase_text="glass + quench crystals")
        == "scored_phase_analysis"
    )
    assert (
        phase_map_provenance(
            Locator(table="1", note="starting composition"),
            None,
            phase_text="quenched silicate glass",
        )
        == "printed_starting_bulk"
    )


def test_phase_as_analyzed_is_quench_glass_only() -> None:
    """phase_as_analyzed is not scanned for the word analyzed.

    Quenched glass stamps. Quenched copper whose strontium was analyzed
    does not. That copper sentence in the ordinary phase text does stamp,
    which is why this field stays on its own argument.
    """

    glass = "quenched glass by electron microprobe analysis"
    metal = "quenched Cu metal; Sr analyzed by ICP-OES"
    assert (
        phase_map_provenance(None, None, analysed_phase_text=glass)
        == "scored_phase_analysis"
    )
    assert phase_map_provenance(None, None, analysed_phase_text=metal) is None
    assert (
        phase_map_provenance(None, None, phase_text=metal) == "scored_phase_analysis"
    )


def test_migrated_kirschen_analysed_glass_keeps_the_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Table 1 phase_as_analyzed is quenched glass by electron microprobe.

    The printed assemblage is ``Two liquids+crist``. CaO is a component of
    that glass, so the echo guard refuses the scored composition.
    """

    _block_equilibrate(monkeypatch)
    observation, experiment = _observation(
        "kirschen-decapitani-1999-liquid-miscibility.yaml",
        "kirschen_decapitani_1999_t1_stc_2_1_phase1_cao",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance == "scored_phase_analysis"
    prediction = _predict(observation, observation.identity, experiment)
    assert (
        prediction.refusal_reason
        is RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS
    )
    assert prediction.refusal_detail["formula"] == "CaO"


def test_migrated_terashima_analysed_copper_is_not_stamped() -> None:
    """phase_as_analyzed names quenched copper and an ICP-OES analysis.

    This row has no oxide map, so there is no composition to stamp. A map
    that did exist would stay unset: the field is not an analysis-word hit.
    """

    observation, _experiment = _observation(
        "terashima-2026-sro-slag-activity-preprint.yaml",
        "terashima_2026_sro_t04_r02_c05",
    )
    composition = observation.identity.composition
    assert composition is None or not composition.is_value or (
        composition.value.input_provenance is None
    )
    point = (observation.point_conditions or {}).get("composition")
    point_state = getattr(point, "state", None)
    if point_state is not None and point_state.is_value:
        assert point_state.value.input_provenance is None


def test_sossi_quench_glass_phase_is_single_melt_evidence() -> None:
    """The phase field 'quenched peridotite glass' is the analysed melt.

    The notice carries that field and the printed quench sentence. The
    quench-crystal regex does not match either string on its own.
    """

    from simulator.battery.input_provenance import (
        analysed_melt_decision,
        collect_run_texts,
    )
    from simulator.battery.identity import quantity_token

    observation, experiment = _observation(
        "sossi-2020-magma-ocean-redox.yaml",
        "sossi_2020_per_f_o2_1_xanes",
    )
    composition = observation.identity.composition
    assert composition is not None and composition.is_value
    assert composition.value.input_provenance == "scored_phase_analysis"
    assert (observation.provenance or {}).get("printed_phase") == (
        "quenched peridotite glass"
    )
    decision = analysed_melt_decision(
        provenance=composition.value.input_provenance,
        quantity=quantity_token(observation.identity),
        formula=observation.identity.species.formula,
        phase_role=None,
        texts=collect_run_texts(experiment, observation),
        point_conditions=observation.point_conditions,
    )
    assert decision.action == "rewrite"
    assert decision.evidence_reason == "single_melt_evidence_quench_glass"
    assert decision.quote is not None
    assert "observation.phase: quenched peridotite glass" in decision.quote
    assert (
        "experiment.thermal_schedule.cooling_or_quench: "
        "Laser switched off; reported cooling rate approximately 800 °C/s"
    ) in decision.quote


def test_pt_loop_hyphen_and_quench_crystals_are_not_crystal_bearing() -> None:
    sampled = printed_single_melt_evidence(
        (("sample.characterization", "The melt was sampled on a Pt-loop."),)
    )
    assert sampled.kind == "positive_sampled"
    formed = printed_single_melt_evidence(
        (("sample.characterization", "Crystals formed on quench."),)
    )
    assert formed.kind == "positive_quench"
    assert not formed.crystalline
    bare = printed_single_melt_evidence(
        (("sample.characterization", "quench crystals"),)
    )
    assert bare.kind == "positive_quench"
    crystalline = printed_single_melt_evidence(
        (("sample.characterization", GOROJOVSKY_CRYSTALLINE),)
    )
    assert crystalline.crystalline


def test_jin_table4_bare_glass_is_not_stamped_analysed_glass() -> None:
    """Phase 'glass' and a silent composition note are not analysed-glass evidence.

    Table 4 is Cr(VI)/Cr(total) on a water-quenched glass. The phase field is
    the word glass, and the Table 2 composition note does not say the map is
    that run's analysed glass. Provenance stays unset, so the d-323 silence
    refusal does not run. The printed Fe2O3 cell stays on the oxide map.
    """

    from simulator.battery.identity import quantity_token
    from simulator.battery.score import _FeRedoxMaterial

    migrator = _load("jin-2022-cl-cr-sulfur-glass-solubility.yaml")
    rows = [
        item
        for item in migrator.result.observations.values()
        if "jin_2022_table4_cr_vi_fraction" in item.observation_id
        and quantity_token(item.identity) is Quantity.VALENCE_RATIO
    ]
    assert len(rows) == 4
    for observation in rows:
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.input_provenance is None
        names = {name for name, _amount in composition.value.components}
        assert "Fe2O3" in names
        assert "FeO" not in names
        experiment = migrator.result.experiments.get(observation.experiment_id)
        material = _fe_redox_material(observation, observation.identity, experiment)
        assert isinstance(material, _FeRedoxMaterial)
        assert material.notices == ()
