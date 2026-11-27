"""REE vapour channels on the residue rail.

Membership, the condensed-equilibrium pressure, the Raoultian notice,
and the DP-117 fallback when those channels are removed. The expected
pressure is computed from species Gibbs energies and the pack reaction
tuple. It is not read back from evaluate_gas.
"""

from decimal import Decimal
import hashlib
import math
from pathlib import Path

import pytest

from simulator.battery.enums import Engine, NoticeKind, Quantity, Rail
from simulator.battery.score import ScoreContext, flagged_strata
from simulator.physical_constants import GAS_CONSTANT
from tests.battery.wang_2001_langmuir_support import (
    ROOT,
    SOURCE_ID,
    wang_context,
    wang_residue_rows,
)

# The imported package is the pin. Directory names and direct_url.json
# are not. The studio installs pyproject's 7cf98e6 tree; the laptop
# cache oimcc-66ec4a3 is a different tree and is not required here.
_REE_ELEMENTS = (
    "La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Y", "Sc",
)
_HAND_TEMPERATURE_K = 2000.0
_HAND_PO2_BAR = 1.0e-8
_HAND_ACTIVITY = 1.0e-4


def _receipt(config, line: str) -> None:
    """Write a validation line on the pytest terminal, not only captured stdout."""

    reporter = config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)
    else:
        print(line)


def _imported_openimcc_tree_hash(config) -> str:
    import openimcc

    root = Path(openimcc.__file__).resolve().parent
    lines = []
    for path in sorted(root.rglob("*.py")):
        digest = hashlib.sha1(path.read_bytes()).hexdigest()
        relative = "./" + path.relative_to(root).as_posix()
        lines.append(f"{digest}  {relative}\n")
    digest = hashlib.sha1("".join(lines).encode()).hexdigest()
    _receipt(config, f"OPENIMCC_FILE {openimcc.__file__}")
    _receipt(config, f"OPENIMCC_TREE {digest}")
    return digest


def _expected_ree_species() -> set[str]:
    species = set()
    for element in _REE_ELEMENTS:
        species.add(element)
        species.add(f"{element}O")
    species.add("CeO2")
    species.add("ScO2")
    return species


def test_openimcc_channel_set_accepts_neutral_ree_gases_only(pytestconfig):
    """The pack's neutral REE gases join the openimcc set. Ions stay off.

    Internal-analytical does not gain them: its catalogue has no REE rows.
    """

    from simulator.battery.residue import (
        _openimcc_residue_channel_set,
        _ree_gas_element,
    )

    tree = _imported_openimcc_tree_hash(pytestconfig)
    assert len(tree) == 40 and all(character in "0123456789abcdef" for character in tree)
    raw = [("SiO", ("SiO2", 1.0, 0.5))]
    accepted, channels, omissions = _openimcc_residue_channel_set(
        raw,
        (),
        engine="openimcc",
        alpha=1.0,
        alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
    )
    accepted_species = {species for species, _reaction in accepted}
    assert accepted_species == {channel.species for channel in channels}
    ree_species = {
        species for species in accepted_species if _ree_gas_element(species)
    }
    assert ree_species == _expected_ree_species()
    assert all(
        not species.endswith(("+", "-")) for species in accepted_species
    )
    assert not any(
        str(omission.get("reason", "")).startswith("ree_pack_channel_omitted:")
        for omission in omissions
    )

    analytical, analytical_channels, _analytical_omissions = (
        _openimcc_residue_channel_set(
            raw,
            (),
            engine="internal-analytical",
            alpha=1.0,
            alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
        )
    )
    analytical_species = {species for species, _reaction in analytical}
    assert analytical_species.isdisjoint(_expected_ree_species())
    assert all(
        _ree_gas_element(channel.species) is None
        for channel in analytical_channels
    )


def test_uncarried_ree_parents_stay_off_the_channel_set():
    """A CMAS bead does not gain REE channels. La2O3 does.

    Existing channels are selected from the parents in the inventory.
    REE channels follow that rule. CeO2 stays only when Ce2O3 is carried,
    and then at zero CeO2 activity.
    """

    from simulator.battery.residue import (
        _openimcc_residue_channel_set,
        _ree_gas_element,
    )

    raw = [("SiO", ("SiO2", 1.0, 0.5))]
    cmas, _channels, omissions = _openimcc_residue_channel_set(
        raw,
        (),
        engine="openimcc",
        alpha=1.0,
        alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
        carried_parent_oxides=("SiO2", "MgO", "Al2O3", "CaO"),
    )
    assert all(_ree_gas_element(species) is None for species, _reaction in cmas)
    assert not any(
        str(omission.get("reason", "")).startswith("ree_pack_channel_omitted:")
        for omission in omissions
    )

    carried, _la_channels, _la_omissions = _openimcc_residue_channel_set(
        raw,
        (),
        engine="openimcc",
        alpha=1.0,
        alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
        carried_parent_oxides=("SiO2", "La2O3"),
    )
    carried_species = {species for species, _reaction in carried}
    assert {"La", "LaO"} <= carried_species
    assert "Ce" not in carried_species
    assert "CeO2" not in carried_species

    cerium, _ce_channels, _ce_omissions = _openimcc_residue_channel_set(
        raw,
        (),
        engine="openimcc",
        alpha=1.0,
        alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
        carried_parent_oxides=("Ce2O3",),
    )
    cerium_species = {species for species, _reaction in cerium}
    assert {"Ce", "CeO", "CeO2"} <= cerium_species


def test_lao_pressure_matches_the_condensed_equilibrium(pytestconfig):
    """La2O3(l) = 2 LaO(g) + 1/2 O2 at one T, pO2, and activity.

    ΔG° = n_gas G°(LaO) + n_O2 G°(O2) - G°(La2O3), joule per mole.
    K = exp(-ΔG° / (R T)), dimensionless, R the CODATA gas constant.
    p_LaO / bar = (K * a(La2O3) / (p_O2/bar) ** n_O2) ** (1/n_gas).
    Per metal that is MO1.5(l) = MO(g) + 1/4 O2, so the residue pO2
    exponent is -1/4. Raising pO2 lowers p(LaO).
    """

    from openimcc import evaluate_gas, load_gas_datapack, species_thermo

    from simulator.battery.residue import (
        _channel_terms,
        _openimcc_residue_channel_set,
    )

    accepted, channels, _omissions = _openimcc_residue_channel_set(
        [],
        (),
        engine="openimcc",
        alpha=1.0,
        alpha_source="DP-005 assumed common alpha=1 for a composition ratio",
    )
    parent, n_gas, n_o2 = dict(accepted)["LaO"]
    assert parent == "La2O3"
    assert n_gas == 2
    assert n_o2 == 0.5
    lao_channel = next(channel for channel in channels if channel.species == "LaO")
    exponent = _channel_terms(
        (lao_channel,), require_oxygen_channels=False
    )[0].neutral_pO2_exponent
    assert exponent == pytest.approx(-n_o2 / n_gas)
    assert exponent == pytest.approx(-0.25)

    pack = load_gas_datapack()
    g_lao = species_thermo("LaO", "g", _HAND_TEMPERATURE_K, pack).G_J_mol
    g_o2 = species_thermo("O2", "g", _HAND_TEMPERATURE_K, pack).G_J_mol
    g_oxide = species_thermo("La2O3", "l", _HAND_TEMPERATURE_K, pack).G_J_mol
    delta_g = n_gas * g_lao + n_o2 * g_o2 - g_oxide
    equilibrium = math.exp(-delta_g / (GAS_CONSTANT * _HAND_TEMPERATURE_K))
    hand_bar = (
        equilibrium * _HAND_ACTIVITY / _HAND_PO2_BAR ** n_o2
    ) ** (1.0 / n_gas)
    evaluated = evaluate_gas(
        {"La2O3": _HAND_ACTIVITY},
        _HAND_TEMPERATURE_K,
        _HAND_PO2_BAR,
        pack,
        parent_oxides=("La2O3",),
        allow_extrapolation=True,
        gas_species=("LaO",),
    )
    evaluated_bar = float(evaluated["LaO"])
    _receipt(
        pytestconfig,
        "LAO_HAND_BAR "
        + format(hand_bar, ".16g")
        + " EVAL_BAR "
        + format(evaluated_bar, ".16g"),
    )
    assert hand_bar > 0.0
    assert evaluated_bar == pytest.approx(hand_bar, rel=1.0e-8)
    higher_oxygen = (
        equilibrium * _HAND_ACTIVITY / (10.0 * _HAND_PO2_BAR) ** n_o2
    ) ** (1.0 / n_gas)
    assert higher_oxygen < hand_bar
    assert higher_oxygen / hand_bar == pytest.approx(10.0 ** exponent)


def _wang_ree_row(references, formula, row_id):
    from simulator.battery.residue import _langmuir_residue_row_id

    return next(
        row
        for row in references.values()
        if row.identity.species.formula == formula
        and _langmuir_residue_row_id(row.locator) == row_id
    )


def test_raoultian_gamma_notice_flags_proposed_policy_and_the_named_gaps():
    """Gamma = 1 is a proposed knob. Caveats do not carry that stratum."""

    from simulator.battery.residue import (
        REE_CE_ALL_AS_CE2O3_DEFAULT,
        REE_DYO_FORMATION_ENTHALPY_UNCERTAINTY,
        REE_LIQUID_REFERENCE_EXTRAPOLATED,
        REE_OXIDE_ACTIVITY_RAOULTIAN_DEFAULT,
        REE_SPECIATION_LIMITED,
        wang_ree_channel_prediction,
    )

    observations = wang_context()[1]
    references = wang_residue_rows(observations)
    cases = {
        "Ce": _wang_ree_row(references, "Ce", "sc1"),
        "Eu": _wang_ree_row(references, "Eu", "sc1"),
        "Dy": _wang_ree_row(references, "Dy", "sc1"),
    }
    for formula, reference in cases.items():
        channelled = wang_ree_channel_prediction(
            reference,
            {
                "ree_vapour_elements": (formula,),
                "element_ppm_by_mass": {formula: 10.0},
                "oxygen_condition_unprinted": True,
            },
            formula,
        )
        assert channelled is not None
        notices = channelled["notices"]
        gamma = [
            notice
            for notice in notices
            if notice.kind is NoticeKind.KNOB_DEFAULTED
            and notice.reason.startswith(
                f"{REE_OXIDE_ACTIVITY_RAOULTIAN_DEFAULT}:"
            )
        ]
        assert len(gamma) == 1
        assert gamma[0].policy is not None
        assert gamma[0].policy.id == "DP-118"
        assert gamma[0].policy.status == "proposed"
        assert "gamma=1" in gamma[0].reason
        assert "not fitted" in gamma[0].reason
        strata = flagged_strata(notices)
        assert "proposed-policy" in strata
        assert "knob_defaulted" in strata
        without_gamma = tuple(
            notice
            for notice in notices
            if not notice.reason.startswith(
                f"{REE_OXIDE_ACTIVITY_RAOULTIAN_DEFAULT}:"
            )
        )
        assert "proposed-policy" not in flagged_strata(without_gamma)
        assert any(
            notice.reason.startswith(f"{REE_LIQUID_REFERENCE_EXTRAPOLATED}:")
            and "supercooled liquid reference" in notice.reason
            for notice in notices
        )
        if formula == "Ce":
            assert REE_CE_ALL_AS_CE2O3_DEFAULT in gamma[0].reason
            assert "a(CeO2)=0" in gamma[0].reason
        if formula == "Eu":
            assert any(
                notice.reason.startswith(f"{REE_SPECIATION_LIMITED}:")
                and "not a test of gamma=1" in notice.reason
                for notice in notices
            )
        if formula == "Dy":
            from openimcc.gas import ELEMENT_STATUS

            reason = str(ELEMENT_STATUS["Dy"]["reason"])
            assert "±20 kJ/mol formation uncertainty" in reason
            temperature_K = float(reference.identity.temperature_K.value)
            dex = 20_000.0 / (GAS_CONSTANT * temperature_K * math.log(10.0))
            banded = next(
                notice
                for notice in notices
                if notice.reason.startswith(
                    f"{REE_DYO_FORMATION_ENTHALPY_UNCERTAINTY}:"
                )
            )
            assert banded.band == f"±{dex:.6g} dex"
            assert "the pressure was not adjusted" in banded.reason

    eu = cases["Eu"]
    reduced_off = wang_ree_channel_prediction(
        eu,
        {
            "ree_vapour_elements": ("Eu",),
            "element_ppm_by_mass": {"Eu": 10.0},
            "oxygen_condition_unprinted": False,
        },
        "Eu",
    )
    assert reduced_off is not None
    assert not any(
        notice.reason.startswith(f"{REE_SPECIATION_LIMITED}:")
        for notice in reduced_off["notices"]
    )


def _drop_ree_channels(monkeypatch):
    import simulator.battery.residue as residue

    real = residue._openimcc_residue_channel_set

    def without_ree(
        raw_channels, initial_notices, *, engine, alpha, alpha_source, **kwargs,
    ):
        accepted, channels, omissions = real(
            raw_channels,
            initial_notices,
            engine=engine,
            alpha=alpha,
            alpha_source=alpha_source,
            **kwargs,
        )
        accepted = [
            (species, reaction)
            for species, reaction in accepted
            if residue._ree_gas_element(str(species)) is None
        ]
        channels = tuple(
            channel
            for channel in channels
            if residue._ree_gas_element(channel.species) is None
        )
        return accepted, channels, omissions

    monkeypatch.setattr(residue, "_openimcc_residue_channel_set", without_ree)


def _isolated_ce_sc1():
    from simulator.battery.residue import _langmuir_residue_row_id

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    reference = _wang_ree_row(references, "Ce", "sc1")
    anchor = next(
        row
        for row in observations.values()
        if row.experiment_id == reference.experiment_id
        and row.source_id == SOURCE_ID
        and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and _langmuir_residue_row_id(row.locator) == "sc1"
        and row.admission.status.value == "admitted"
    )
    isolated = ScoreContext(
        {},
        {reference.experiment_id: context.experiments[reference.experiment_id]},
        {reference.observation_id: reference, anchor.observation_id: anchor},
        root=ROOT,
    )
    start_ppm = Decimal(
        str(reference.point_conditions["starting_component_ppm"].state.value)
    )
    mass_loss = Decimal(str(float(anchor.value.point)))
    retained_ppm = start_ppm / (Decimal(1) - mass_loss)
    return isolated, reference, retained_ppm


def test_dropping_ree_channels_restores_the_retention_fallback(
    monkeypatch, pytestconfig,
):
    """No accepted REE channel returns DP-117. Removing that too refuses."""

    import simulator.battery.residue as residue
    import simulator.battery.score as score_module

    _drop_ree_channels(monkeypatch)
    isolated, reference, retained_ppm = _isolated_ce_sc1()
    residuals, candidates = score_module.score_store(
        isolated,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert [residual.reference for residual in residuals] == [
        reference.observation_id
    ]
    residual = residuals[0]
    assert residual.numeric is not None
    assert residual.refusal is None
    candidate = candidates[residual.candidate]
    default = candidate.provenance["ree_retention_default"]
    assert default["missing_channel"] == "Ce"
    assert Decimal(default["predicted_ppm"]) == retained_ppm
    assert candidate.value.point == retained_ppm
    assert "ree_vapour_channel" not in candidate.provenance
    assert any(
        notice.kind is NoticeKind.KNOB_DEFAULTED
        and notice.policy is not None
        and notice.policy.id == "DP-117"
        and notice.reason.startswith(
            "ree_vapour_channel_missing_default_retained:"
        )
        for notice in residual.notices
    )
    assert not any(
        notice.reason.startswith("ree_oxide_activity_raoultian_default:")
        for notice in residual.notices
    )
    _receipt(pytestconfig, "REE_MUTATION_DP117 " + format(retained_ppm, "f"))

    monkeypatch.setattr(
        residue, "wang_ree_full_retention_prediction", lambda *_a, **_k: None
    )
    refused, _candidates = score_module.score_store(
        isolated,
        engines=(Engine.OPENIMCC,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert [row.reference for row in refused] == [reference.observation_id]
    refusal = refused[0]
    assert refusal.numeric is None
    assert refusal.refusal is not None
    assert refusal.refusal.detail["reason"] == "awaits_ree_channels_sossipar"
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and notice.reason.startswith(
            "ree_channels_absent_no_valence_selected:"
        )
        for notice in refusal.notices
    )
    assert not any(
        notice.reason.startswith("ree_vapour_channel_missing_default_retained:")
        or notice.reason.startswith("ree_oxide_activity_raoultian_default:")
        or notice.reason.startswith("initial_oxide_valence_choice:")
        for notice in refusal.notices
    )
    _receipt(pytestconfig, "REE_MUTATION_REFUSAL awaits_ree_channels_sossipar")


def _segment_solution(t_end, state, n_events, hit_index=None, hit_time=None):
    """Minimal solve_ivp result: one accepted column and optional event hits."""

    class _Column:
        def __init__(self, values):
            self._values = tuple(float(value) for value in values)

        def __getitem__(self, key):
            if isinstance(key, tuple) and key[0] == slice(None) and key[1] == -1:
                return self._values
            raise KeyError(key)

    class _Endpoint:
        def __init__(self, value):
            self._value = float(value)

        def __getitem__(self, key):
            if key == -1:
                return self._value
            raise KeyError(key)

    class _Solution:
        success = True
        message = "segment"

        def __init__(self):
            self.t = _Endpoint(t_end)
            self.y = _Column(state)
            hits = [[] for _ in range(n_events)]
            if hit_index is not None:
                hits[hit_index] = [float(hit_time)]
            self.t_events = hits

    return _Solution()


def _install_two_segment_solver(monkeypatch, pressure_calls):
    """Replace DOP853 with two scripted segments. The first exhausts MgO."""

    def solve_ivp(fun, t_span, y0, **kwargs):
        fun(float(t_span[0]), list(y0))
        pressure_calls.append(float(t_span[0]))
        n_events = len(kwargs["events"])
        state = [float(value) for value in y0]
        start = float(t_span[0])
        if len(pressure_calls) == 1:
            assert n_events == 3
            # MgO -> Mg is 1:1. The oxygen ledger is independent of the
            # oxide moles, so the cation moves into the Mg gas slot and
            # the oxygen atom stays where the integrator already tracks it.
            state[4] += state[1]
            state[1] = 0.0
            return _segment_solution(
                start + 0.1, state, n_events, hit_index=1, hit_time=start + 0.1,
            )
        return _segment_solution(float(t_span[1]), state, n_events)

    monkeypatch.setattr("scipy.integrate.solve_ivp", solve_ivp)


def _two_oxide_extent_kwargs():
    from simulator.battery.residue import ResidueChannel

    return {
        "initial_inventory_mol": {"FeO": 1.0, "MgO": 1.0e-4},
        "channels": (
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "segment-budget"),
            ResidueChannel("Mg", "Mg", "MgO", 1.0, "segment-budget"),
            ResidueChannel("O2", "O2", None, 1.0, "segment-budget"),
        ),
        "pressure_model": lambda _inventory, _logp, _temperature: {
            "Fe": 1.0,
            "Mg": 1.0,
            "O2": 0.0,
        },
        "temperature_path": ((0.5, 2000.0),),
        "measured_mass_loss_fraction": 0.5,
        "near_saturated": True,
        "source_fO2_log_by_interval": (-10.0,),
    }


def test_parent_event_restarts_the_nonlinear_rhs_budget(monkeypatch):
    """A trace-parent event must not spend the next segment's RHS budget.

    Cap 1 admits one derivative call. Without a per-segment reset the
    second segment raises knudsen_extent_evaluation_limit. Wang 2001 sc9
    is that shape at the real 1024 cap: the trace events spend the
    counter, then the last fraction of extent refuses.
    """

    from simulator.battery import residue

    calls = []
    _install_two_segment_solver(monkeypatch, calls)
    monkeypatch.setattr(residue, "_KNUDSEN_MAX_RHS_EVALUATIONS", 1)
    result = residue.integrate_knudsen_residue_inventory(**_two_oxide_extent_kwargs())
    assert calls == [0.0, 0.1]
    assert result.residue_mol["MgO"] == 0.0
    assert result.residue_mol["FeO"] == 1.0


def test_linear_extent_keeps_one_shared_rhs_budget(monkeypatch):
    """The linear ramp shares one budget across segments on purpose."""

    from simulator.battery import residue
    from simulator.battery.residue import ResidueInventoryRefusal

    calls = []
    _install_two_segment_solver(monkeypatch, calls)
    monkeypatch.setattr(residue, "_KNUDSEN_MAX_LINEAR_RHS_EVALUATIONS", 1)
    with pytest.raises(ResidueInventoryRefusal) as exc:
        residue.integrate_knudsen_residue_inventory(
            **_two_oxide_extent_kwargs(),
            linear_temperature=True,
        )
    assert exc.value.reason == "knudsen_extent_evaluation_limit"
    assert calls == [0.0]


def test_wang_sc9_extent_names_its_outcome():
    """sc9 at the printed 95% loss must finish, as sc10 at 90% already does.

    sc9 previously raised knudsen_extent_evaluation_limit at extent
    0.951545 of 0.953559 because trace-parent events shared one RHS
    budget. sc10's Ce ppm is the value that run returned, pinned so a
    later segment reset cannot move a row that already fit in one budget.
    """

    import warnings

    from simulator.battery.residue import (
        ResidueEngineNonconvergence,
        ResidueInventoryRefusal,
        _langmuir_residue_row_id,
        predict_langmuir_residue_composition,
    )

    context, observations = wang_context()
    references = wang_residue_rows(observations)
    picked = {}
    for reference in references.values():
        row_id = _langmuir_residue_row_id(reference.locator)
        if row_id not in {"sc9", "sc10"} or row_id in picked:
            continue
        if reference.identity.species.formula != "Ce":
            continue
        picked[row_id] = reference
    assert set(picked) == {"sc9", "sc10"}
    ppm_by_row = {}
    for row_id in ("sc10", "sc9"):
        reference = picked[row_id]
        experiment = context.experiments[reference.experiment_id]
        try:
            result = predict_langmuir_residue_composition(
                "openimcc", reference, experiment, observations.values(),
            )
        except (ResidueInventoryRefusal, ResidueEngineNonconvergence) as exc:
            warnings.warn(
                f"WANG_{row_id.upper()}_REFUSAL {exc.reason} {exc.detail}",
                UserWarning,
                stacklevel=1,
            )
            raise
        ppm = (result.get("element_ppm_by_mass") or {}).get("Ce")
        warnings.warn(
            f"WANG_{row_id.upper()}_CE_PPM {ppm}",
            UserWarning,
            stacklevel=1,
        )
        ppm_by_row[row_id] = float(ppm)
    assert ppm_by_row["sc10"] == pytest.approx(225.98182155362952, rel=1e-9, abs=0.0)
    assert math.isfinite(ppm_by_row["sc9"])
    assert ppm_by_row["sc9"] >= 0.0
