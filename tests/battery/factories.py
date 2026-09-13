"""Minimal valid records for schema v2.1 tests. Not production fixtures."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    AssetRole,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    ExperimentKind,
    FO2Channel,
    MethodToken,
    MetricOperation,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    ReferenceStateConvention,
    RefusalReason,
    RegimeClass,
    ResidualStatus,
    SourceRelation,
    UncertaintyKind,
)
from simulator.battery.identity import (
    Exposure,
    Identity,
    SweepIdentity,
    WallIdentity,
    bar_to_pa,
)
from simulator.battery.records import (
    Admission,
    Apparatus,
    ApparatusGeometry,
    CandidateRequest,
    Composition,
    DecisionBand,
    Derivation,
    EngineTrace,
    Evidence,
    Execution,
    Experiment,
    FlowRegime,
    FO2Control,
    Located,
    Locator,
    Notice,
    Observation,
    PressureEnvironment,
    Reaction,
    ReactionTerm,
    Residual,
    ResidualNumeric,
    ResidualRefusal,
    Sample,
    SourceFile,
    SourceFiles,
    Species,
    StandardState,
    State,
    SweepGas,
    Uncertainty,
    Value,
    Work,
)


def loc(**kwargs: object) -> Locator:
    if not kwargs:
        return Locator(page=1, table="I")
    return Locator(**kwargs)  # type: ignore[arg-type]


def located(value: object, **locator_kwargs: object) -> Located:
    return Located(State.of(value), locator=loc(**locator_kwargs) if locator_kwargs else loc())


def work(work_id: str = "work-1") -> Work:
    return Work(
        work_id=work_id,
        citation="Chase 1998 NIST-JANAF 4th",
        source_ids=(work_id, "janaf-4th"),
        source_files=SourceFiles(
            corpus_repo="literature-corpus",
            corpus_commit=State.of("deadbeef"),
            files=(
                SourceFile(
                    asset_id="pdf-1",
                    role=AssetRole.PDF,
                    path="chase-1998.pdf",
                    sha256=State.of("abc"),
                ),
            ),
        ),
        doi="10.18434/T42S31",
    )


def formation_reaction(product: Species, metal: Species, nu_metal: Fraction, nu_o2: Fraction) -> Reaction:
    return Reaction(
        (
            ReactionTerm(product, Fraction(1)),
            ReactionTerm(metal, -nu_metal),
            ReactionTerm(Species("O2", Phase.G), -nu_o2),
        )
    )


def o2_identity(T_K: Decimal = Decimal("298.15")) -> Identity:
    o2 = Species("O2", Phase.G)
    return Identity(
        quantity=Quantity.DELTA_FG,
        species=o2,
        per=State.of(PerBasis.MOL_SPECIES),
        temperature_K=State.of(T_K),
        standard_pressure_Pa=State.of(bar_to_pa("1")),
        reaction=State.of(
            Reaction((ReactionTerm(o2, Fraction(1)), ReactionTerm(o2, Fraction(-1))))
        ),
        formation_elements=State.of((("O", o2),)),
        composition=State.not_applicable("pure standard formation"),
        fO2_Pa=State.not_applicable("pure standard formation"),
        sweep_gas=State.not_applicable("pure standard formation"),
        exposure=State.not_applicable("pure standard formation"),
        sample_mass_kg=State.not_applicable("pure standard formation"),
        wall=State.not_applicable("pure standard formation"),
        reservoir=State.not_applicable("pure standard formation"),
        reference_state=State.not_applicable("pure standard formation"),
        total_pressure_Pa=State.not_applicable("pure standard formation"),
        subtype=State.not_applicable("not a subtype observable"),
    )


def oxide_identity(
    formula: str,
    phase: Phase,
    *,
    T_K: Decimal = Decimal("1200"),
    metal_phase: Phase = Phase.L,
    metal_formula: str | None = None,
    nu_metal: Fraction = Fraction(2),
    nu_o2: Fraction = Fraction(1),
    per: PerBasis = PerBasis.MOL_O2,
    p_std: Decimal | None = None,
    polymorph: str | None = None,
) -> Identity:
    product = Species(
        formula,
        phase,
        polymorph=State.of(polymorph) if phase is Phase.CR else State.not_applicable("not crystal"),
    )
    metal = Species(metal_formula or formula.replace("O", "").replace("2", "").replace("3", "") or "M", metal_phase)
    if metal.formula == formula:
        metal = Species(formula[0:2] if formula[1:2].islower() else formula[0], metal_phase)
    o2 = Species("O2", Phase.G)
    return Identity(
        quantity=Quantity.DELTA_FG,
        species=product,
        per=State.of(per),
        temperature_K=State.of(T_K),
        standard_pressure_Pa=State.of(p_std if p_std is not None else bar_to_pa("1")),
        reaction=State.of(formation_reaction(product, metal, nu_metal, nu_o2)),
        formation_elements=State.of((("M" if False else metal.formula, metal), ("O", o2))),
        composition=State.not_applicable("pure standard formation"),
        fO2_Pa=State.not_applicable("pure standard formation"),
        sweep_gas=State.not_applicable("pure standard formation"),
        exposure=State.not_applicable("pure standard formation"),
        sample_mass_kg=State.not_applicable("pure standard formation"),
        wall=State.not_applicable("pure standard formation"),
        reservoir=State.not_applicable("pure standard formation"),
        reference_state=State.not_applicable("pure standard formation"),
        total_pressure_Pa=State.not_applicable("pure standard formation"),
        subtype=State.not_applicable("not a subtype observable"),
    )


def cao_identity(phase: Phase, **kwargs: object) -> Identity:
    return oxide_identity(
        "CaO",
        phase,
        metal_formula="Ca",
        nu_metal=Fraction(1),
        nu_o2=Fraction(1, 2),
        polymorph="lime" if phase is Phase.CR else None,
        **kwargs,  # type: ignore[arg-type]
    )


def na2o_identity(metal_phase: Phase, product_phase: Phase = Phase.L, **kwargs: object) -> Identity:
    return oxide_identity(
        "Na2O",
        product_phase,
        metal_formula="Na",
        metal_phase=metal_phase,
        nu_metal=Fraction(2),
        nu_o2=Fraction("1/2"),
        per=PerBasis.MOL_SPECIES,
        **kwargs,  # type: ignore[arg-type]
    )


def psat_identity(
    formula: str,
    *,
    T_K: Decimal = Decimal("1156"),
    reservoir_phase: Phase = Phase.L,
) -> Identity:
    gas = Species(formula, Phase.G)
    reservoir = Species(formula, reservoir_phase)
    na = "pure-component p_sat"
    return Identity(
        quantity=Quantity.P_SAT,
        species=gas,
        temperature_K=State.of(T_K),
        reservoir=State.of(reservoir),
        per=State.not_applicable(na),
        standard_pressure_Pa=State.not_applicable(na),
        composition=State.not_applicable(na),
        fO2_Pa=State.not_applicable(na),
        total_pressure_Pa=State.not_applicable(na),
        sweep_gas=State.not_applicable(na),
        exposure=State.not_applicable(na),
        sample_mass_kg=State.not_applicable(na),
        wall=State.not_applicable(na),
        reaction=State.not_applicable(na),
        formation_elements=State.not_applicable(na),
        reference_state=State.not_applicable(na),
        subtype=State.not_applicable(na),
    )


def pref_identity(
    *,
    T_K: Decimal = Decimal("1156"),
    fO2_Pa: Decimal | None = None,
    endmember_formula: str = "NaO0.5",
    endmember_phase: Phase = Phase.L,
    convention: ReferenceStateConvention = ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
    component_basis: str = "NaO0.5",
) -> Identity:
    gas = Species("Na", Phase.G)
    reservoir = Species(endmember_formula, endmember_phase)
    oxide = Species(endmember_formula, endmember_phase)
    o2 = Species("O2", Phase.G)
    reaction = Reaction(
        (
            ReactionTerm(gas, Fraction(1)),
            ReactionTerm(o2, Fraction("1/4")),
            ReactionTerm(oxide, Fraction(-1)),
        )
    )
    ref = StandardState(
        convention=convention,
        endmember=oxide,
        component_basis=component_basis,
        reference_pressure_bar=Decimal("1"),
    )
    na = "unit-endmember Pref"
    return Identity(
        quantity=Quantity.P_REFERENCE,
        species=gas,
        temperature_K=State.of(T_K),
        reservoir=State.of(reservoir),
        reaction=State.of(reaction),
        reference_state=State.of(ref),
        fO2_Pa=State.of(fO2_Pa if fO2_Pa is not None else bar_to_pa("1")),
        composition=State.not_applicable(na),
        per=State.not_applicable(na),
        standard_pressure_Pa=State.not_applicable(na),
        sweep_gas=State.not_applicable(na),
        exposure=State.not_applicable(na),
        sample_mass_kg=State.not_applicable(na),
        wall=State.not_applicable(na),
        formation_elements=State.not_applicable(na),
        subtype=State.not_applicable(na),
        total_pressure_Pa=State.not_applicable(na),
    )


def activity_identity(
    *,
    formula: str = "NaO0.5",
    T_K: Decimal = Decimal("1643.15"),
    convention: ReferenceStateConvention = ReferenceStateConvention.RAOULTIAN_PURE_ENDMEMBER,
    endmember_phase: Phase = Phase.L,
    component_basis: str = "NaO0.5",
    composition: Composition | None = None,
    fO2_Pa: Decimal = Decimal("1e-8"),
    total_P: Decimal = Decimal("100000"),
) -> Identity:
    species = Species(formula, Phase.L)
    endmember = Species(formula, endmember_phase)
    pot = composition or Composition(
        basis="ordered_complete_mole_inventory",
        components=(("SiO2", Decimal("0.5")), ("NaO0.5", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    ref = StandardState(
        convention=convention,
        endmember=endmember,
        component_basis=component_basis,
        reference_pressure_bar=Decimal("1"),
    )
    na = "melt activity"
    return Identity(
        quantity=Quantity.ACTIVITY,
        species=species,
        temperature_K=State.of(T_K),
        per=State.of(PerBasis.DIMENSIONLESS),
        reference_state=State.of(ref),
        composition=State.of(pot),
        fO2_Pa=State.of(fO2_Pa),
        total_pressure_Pa=State.of(total_P),
        standard_pressure_Pa=State.not_applicable(na),
        reaction=State.not_applicable(na),
        formation_elements=State.not_applicable(na),
        reservoir=State.not_applicable(na),
        sweep_gas=State.not_applicable(na),
        exposure=State.not_applicable(na),
        sample_mass_kg=State.not_applicable(na),
        wall=State.not_applicable(na),
        subtype=State.not_applicable(na),
    )


def wall_deposit_identity(
    *,
    formula: str = "SiO",
    T_K: Decimal = Decimal("1673.15"),
    wall_T: Decimal = Decimal("1673.15"),
    wall_material: str = "SiO2",
) -> Identity:
    species = Species(formula, Phase.G)
    reservoir = Species("SiO2", Phase.L)
    pot = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("SiO2", Decimal("0.5")), ("FeO", Decimal("0.5"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    na = "not a thermo standard-state axis"
    return Identity(
        quantity=Quantity.WALL_DEPOSIT_MASS,
        species=species,
        temperature_K=State.of(T_K),
        per=State.of(PerBasis.KG),
        reservoir=State.of(reservoir),
        composition=State.of(pot),
        fO2_Pa=State.of(Decimal("1e-4")),
        total_pressure_Pa=State.of(Decimal("100")),
        sweep_gas=State.of(
            SweepIdentity(
                species="N2",
                flow_sccm=State.of(Decimal("10")),
                partial_pressure_Pa=State.of(Decimal("100")),
            )
        ),
        exposure=State.of(
            Exposure(area_m2=State.of(Decimal("0.01")), duration_s=State.of(Decimal("3600")))
        ),
        sample_mass_kg=State.of(Decimal("0.001")),
        wall=State.of(
            WallIdentity(
                temperature_K=State.of(wall_T),
                material=State.of(wall_material),
                area_m2=State.of(Decimal("0.02")),
                location=State.of("duct"),
            )
        ),
        subtype=State.not_applicable(na),
        standard_pressure_Pa=State.not_applicable(na),
        reaction=State.not_applicable(na),
        formation_elements=State.not_applicable(na),
        reference_state=State.not_applicable(na),
    )


def tabulation_experiment(
    experiment_id: str = "exp-1",
    work_id: str = "work-1",
    *,
    method: MethodToken = MethodToken.TABULATION,
    T_K: Decimal = Decimal("298.15"),
    total_P: Decimal = Decimal("1e-6"),
    kn_orifice: Decimal | None = None,
    fO2_control: FO2Control | None = None,
) -> Experiment:
    sweep = SweepGas(
        species="none",
        flow_sccm=State.not_applicable("carrier-free"),
        partial_pressure_Pa=State.not_applicable("carrier-free"),
    )
    return Experiment(
        experiment_id=experiment_id,
        kind=ExperimentKind.LITERATURE,
        method=State.of(method),
        sample=Sample(form=located("tablet")),
        conditions={"temperature_K": located(T_K)},
        pressure_environment=PressureEnvironment(
            total_pressure_Pa=located(total_P),
            sweep_gas=located(sweep),
            regime=FlowRegime(
                regime_class=State.of(RegimeClass.MOLECULAR),
                knudsen_number_orifice=None if kn_orifice is None else located(kn_orifice),
            ),
        ),
        work_id=work_id,
        locator=loc(page=1, table="I"),
        fO2_control=fO2_control,
    )


def kems_experiment(
    experiment_id: str = "kems-1",
    work_id: str = "work-1",
    *,
    orifice_area: Decimal | None = Decimal("3.14e-7"),
    clausing: Decimal | None = Decimal("0.9"),
    kn: Decimal | None = Decimal("20"),
    total_P: Decimal = Decimal("1e-6"),
    calibrated: bool = True,
) -> Experiment:
    geometry = ApparatusGeometry(
        orifice_area_m2=None if orifice_area is None else located(orifice_area),
        clausing_factor=None if clausing is None else located(clausing),
    )
    apparatus = Apparatus(
        geometry=geometry,
        calibration={"standard": located("Ag")} if calibrated else None,
    )
    return Experiment(
        experiment_id=experiment_id,
        kind=ExperimentKind.LITERATURE,
        method=State.of(MethodToken.KNUDSEN_EFFUSION),
        sample=Sample(form=located("melt")),
        conditions={"temperature_K": located(Decimal("1500"))},
        pressure_environment=PressureEnvironment(
            total_pressure_Pa=located(total_P),
            sweep_gas=located(
                SweepGas(
                    species="none",
                    flow_sccm=State.not_applicable("carrier-free"),
                    partial_pressure_Pa=State.not_applicable("carrier-free"),
                )
            ),
            regime=FlowRegime(
                regime_class=State.of(RegimeClass.MOLECULAR),
                knudsen_number_orifice=None if kn is None else located(kn),
            ),
        ),
        work_id=work_id,
        locator=loc(page=4, figure="1b"),
        apparatus=apparatus,
    )


def observation(
    observation_id: str,
    experiment_id: str,
    identity: Identity,
    value: object,
    *,
    evidence: EvidenceClass = EvidenceClass.COMPILATION_ASSESSED,
    admission: AdmissionStatus = AdmissionStatus.ADMITTED,
    source_id: str = "janaf-4th",
    notices: tuple[Notice, ...] = (),
    derived_from: tuple[str, ...] | None = None,
    derivation: Derivation | None = None,
    engine: EngineTrace | None = None,
    authority: Authority | None = None,
    annotations=None,
    certified_band=None,
) -> Observation:
    ev_kwargs = {}
    if evidence is EvidenceClass.QUOTED_ATTRIBUTED:
        ev_kwargs["attribution"] = "prior-work"
    if evidence is EvidenceClass.MODEL_DERIVED:
        ev_kwargs["model"] = "gibbs-duhem"
        if derived_from is None:
            derived_from = ("raw-1",)
        if derivation is None:
            derivation = Derivation(
                relation="model",
                inputs=("raw-1",),
                parameters=(),
                output_unit="Pa",
            )
    if evidence is EvidenceClass.MEASURED_REDUCED:
        if derived_from is None:
            derived_from = ("raw-1",)
        if derivation is None:
            derivation = Derivation(
                relation="ion-to-pressure",
                inputs=("raw-1",),
                parameters=(),
                output_unit="Pa",
            )
    return Observation(
        observation_id=observation_id,
        experiment_id=experiment_id,
        identity=identity,
        value=Value.point_of(value),
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
        evidence=Evidence(class_=State.of(evidence), **ev_kwargs),
        admission=Admission(status=admission, reason="canonical"),
        notices=notices,
        source_id=source_id,
        locator=loc(),
        read_from="pdf-1",
        derived_from=derived_from,
        derivation=derivation,
        engine=engine,
        authority=authority,
        annotations=annotations,
        certified_band=certified_band,
    )


def engine_obs(
    observation_id: str,
    experiment_id: str,
    identity: Identity,
    value: object,
    *,
    authority: Authority = Authority.CERTIFIED,
    notices: tuple[Notice, ...] = (),
    requested=None,
    certified_band=None,
) -> Observation:
    return observation(
        observation_id,
        experiment_id,
        identity,
        value,
        evidence=EvidenceClass.ENGINE_PREDICTION,
        notices=notices,
        engine=EngineTrace(
            name=Engine.NASA_CEA_9,
            channel="cea",
            run_id="run-1",
            coefficient_sources=("nasa-cea-thermo",),
            lineage_complete=True,
            requested_composition=None if requested is None else State.of(requested),
        ),
        authority=authority,
        certified_band=certified_band,
    )


def residual(
    key: str,
    reference: str,
    *,
    candidate: str | None = None,
    status: ResidualStatus = ResidualStatus.REFUSED,
    rail: Rail = Rail.THERMOCHEMISTRY,
    execution: ExecutionState = ExecutionState.PRODUCED,
    score_eligible: bool = False,
    notices: tuple[Notice, ...] = (),
    numeric: ResidualNumeric | None = None,
    refusal: ResidualRefusal | None = None,
    source_relation: SourceRelation = SourceRelation.INDEPENDENT,
    experiment_id: str = "exp-1",
    quantity: Quantity = Quantity.DELTA_FG,
) -> Residual:
    if status is ResidualStatus.REFUSED and refusal is None:
        refusal = ResidualRefusal(RefusalReason.INVALID_SOURCE, {"gate": "table"})
    if status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH} and numeric is None:
        numeric = ResidualNumeric(
            operation=MetricOperation.ABSOLUTE,
            unit="kJ_per_declared_mol_basis",
            value=Decimal("0"),
            decision_band=DecisionBand(Decimal("1"), "kJ_per_declared_mol_basis", "independent-1kJ"),
        )
    return Residual(
        key=key,
        reference=reference,
        execution=Execution(
            state=execution,
            call_evidence="resolver:cea" if execution is ExecutionState.ATTEMPTED_UNAVAILABLE else None,
        ),
        rail=rail,
        status=status,
        source_relation=source_relation,
        score_eligible=score_eligible,
        exclusions=(),
        notices=notices,
        candidate=candidate,
        candidate_request=None
        if candidate is not None
        else CandidateRequest(experiment_id, quantity, Engine.NASA_CEA_9, "cea"),
        numeric=numeric,
        refusal=refusal,
    )


def floor_notice(origin: str = "catalog:Na") -> Notice:
    return Notice(
        kind=NoticeKind.FLOOR_INVERSION,
        affected_quantities=(Quantity.P_PARTIAL, Quantity.P_SAT),
        reason="pO2 clamped to melt-dissociation floor",
        origin=origin,
        original=Decimal("1e-40"),
        band="[1e-30, 100] bar",
    )
