"""t-1596: a printed J_exp/J_max Hertz-Knudsen ratio is evaporation_coefficient_alpha.

The source may call that ratio a condensation coefficient. The printed term
stays source_term_as_printed. A bare condensation coefficient, and film
growth, stay refused. Family membership is (phase, formula, gas_system):
crystal forsterite does not join a melt pool, and an H2-reactive maximum
does not join the vacuum pool. The vacuum row is held until a second
independent source exists; that minimum is the scorer's existing
two-source rule, not a special case on the printed 0.06.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Engine, Phase, Quantity, ValueKind
from simulator.battery.migrate import (
    ALPHA_GAS_SYSTEM_H2,
    ALPHA_GAS_SYSTEM_VACUUM,
    HK_RATIO_ALPHA_DEFINITION,
    evaporation_alpha_family_key,
    evaporation_alpha_loo_member_ids,
    evaporation_alpha_pool_members,
    map_quantity,
    migrate,
)
from simulator.battery.score import (
    DERIVED_BAND_INSUFFICIENT_SOURCES,
    FORSTERITE_H2_EQUILIBRIUM_GAS_NOT_MODELLED,
    ScoreContext,
    compile_residual,
)

# Printed sentence from Nagahara & Ozawa 1996 §4.3, kept here so the fixture
# is the ratio the ruling names. 0.06 and 0.2 are the printed endpoints.
_DEFINITION = (
    "The condensation coefficient is obtained by dividing the experimentally "
    "obtained evaporation rate J_exp^Fo by the maximum evaporation rate "
    "J_vac^Fo (Fig. 6)."
)
_RATIO = "J_exp^Fo/J_vac^Fo"
_VACUUM = "nagahara_ozawa_1996_alpha_vacuum"
_VACUUM_DUP = "nagahara_ozawa_1996_alpha_vacuum_duplicate"
_H2 = "nagahara_ozawa_1996_alpha_1e-4_bar"
_SECOND_VACUUM = "second_forsterite_crystal_alpha_vacuum"
_SECOND_H2 = "second_forsterite_crystal_alpha_h2"
_MELT_FORSTERITE = "melt_forsterite_alpha_vacuum"
_MELT_MG = "melt_mg_alpha_vacuum"
_NAGAHARA_SOURCE = "synthetic-nagahara-1996"
_SECOND_VACUUM_SOURCE = "synthetic-forsterite-vacuum-2"
_SECOND_H2_SOURCE = "synthetic-forsterite-h2-2"
_MELT_SOURCE = "synthetic-melt-alphas"


def _locator(section: str) -> dict:
    return {"page": 8, "section": section, "note": "synthetic t-1596 row"}


def _alpha_row(
    *,
    observation_id: str,
    phase: str,
    alpha: str,
    gas_mix: str,
    section: str,
    hk: bool,
    pressure_as_printed: str | None = None,
    method_class: str = "quoted_attributed",
) -> dict:
    values: dict = {
        "quantity": "alpha",
        "method_class": method_class,
        "alpha": alpha,
    }
    if hk:
        values["source_term_as_printed"] = "condensation coefficient"
        values["definition_as_printed"] = _DEFINITION
        values["ratio_as_printed"] = _RATIO
        # Both Nagahara rows print this system name. It is not the gas key.
        values["gas_system_as_printed"] = "Mg2SiO4-H2"
        values["pressure_as_printed"] = pressure_as_printed
    return {
        "observation_id": observation_id,
        "type": "alpha",
        "phase": phase,
        "units": "dimensionless",
        "locator": _locator(section),
        "uncertainty": {"kind": "none", "verbatim": "No uncertainty stated."},
        "point_conditions": {
            "temperature_C": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": "1700"},
                },
                "locator": _locator(section),
            },
            "gas_mix": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": gas_mix},
                },
                "locator": _locator(section),
            },
        },
        "values": values,
    }


def _document(source_id: str, species: dict[str, list]) -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": {
            "citation": f"Synthetic {source_id} (1996), Test Journal 1:1",
            "doi": "10.1234/T1596",
        },
        "extraction": {
            "method": "unit_test",
            "date": "2026-11-03",
            "worker": "t1596",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species",
                "value": "synthetic",
                "note": "fixture",
                "locator": {"page": 1},
            }
        ],
        "species": {
            formula: {"observations": rows} for formula, rows in species.items()
        },
    }


def _nagahara_document() -> dict:
    section = "4.3. Condensation Coefficient"
    return _document(
        _NAGAHARA_SOURCE,
        {
            "Mg2SiO4": [
                _alpha_row(
                    observation_id=_VACUUM,
                    phase="cr",
                    alpha="0.06",
                    gas_mix="vacuum",
                    section=section,
                    hk=True,
                    pressure_as_printed="vacuum",
                ),
                _alpha_row(
                    observation_id=_VACUUM_DUP,
                    phase="cr",
                    alpha="0.07",
                    gas_mix="vacuum",
                    section=section,
                    hk=True,
                    pressure_as_printed="vacuum",
                ),
                _alpha_row(
                    observation_id=_H2,
                    phase="cr",
                    alpha="0.2",
                    gas_mix="H2",
                    section=section,
                    hk=True,
                    pressure_as_printed="10^-4 bar",
                ),
            ]
        },
    )


def _companion_documents() -> list[dict]:
    return [
        _document(
            _SECOND_VACUUM_SOURCE,
            {
                "Mg2SiO4": [
                    _alpha_row(
                        observation_id=_SECOND_VACUUM,
                        phase="cr",
                        alpha="0.05",
                        gas_mix="vacuum",
                        section="Table 1 evaporation coefficient",
                        hk=False,
                        method_class="measured_direct",
                    )
                ]
            },
        ),
        _document(
            _SECOND_H2_SOURCE,
            {
                "Mg2SiO4": [
                    _alpha_row(
                        observation_id=_SECOND_H2,
                        phase="cr",
                        alpha="0.15",
                        gas_mix="H2",
                        section="Table 2",
                        hk=True,
                        pressure_as_printed="1e-4 bar H2",
                    )
                ]
            },
        ),
        _document(
            _MELT_SOURCE,
            {
                "Mg2SiO4": [
                    _alpha_row(
                        observation_id=_MELT_FORSTERITE,
                        phase="l",
                        alpha="0.3",
                        gas_mix="vacuum",
                        section="melt evaporation coefficient",
                        hk=False,
                        method_class="measured_direct",
                    )
                ],
                "Mg": [
                    _alpha_row(
                        observation_id=_MELT_MG,
                        phase="l",
                        alpha="0.1",
                        gas_mix="vacuum",
                        section="melt Mg evaporation coefficient",
                        hk=False,
                        method_class="measured_direct",
                    )
                ],
            },
        ),
    ]


def _write_tree(root: Path, docs: list[dict]) -> Path:
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        "schema_version: literature_index.v1\nsources: []\n",
        encoding="utf-8",
    )
    for doc in docs:
        (extracts / f"{doc['source_id']}.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False),
            encoding="utf-8",
        )
    return root


def _one(result, raw_id: str):
    matches = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.endswith("::" + raw_id)
    ]
    assert len(matches) == 1, (
        raw_id,
        sorted(obs.observation_id for obs in result.observations.values()),
    )
    return matches[0]


def _reason(result, obs) -> str:
    context = ScoreContext(result.works, result.experiments, result.observations)
    residual, _candidate = compile_residual(
        obs, Engine.INTERNAL_ANALYTICAL, context=context
    )
    assert residual.refusal is not None
    return str(residual.refusal.detail.get("reason"))


def _hk_values(**extra) -> dict:
    values = {
        "quantity": "alpha",
        "alpha": "0.06",
        "source_term_as_printed": "condensation coefficient",
        "definition_as_printed": _DEFINITION,
        "ratio_as_printed": _RATIO,
        "gas_system_as_printed": "Mg2SiO4-H2",
    }
    values.update(extra)
    return values


def test_hk_ratio_maps_and_bare_condensation_stays_refused() -> None:
    row = {
        "phase": "cr",
        "locator": {"section": "4.3. Condensation Coefficient"},
        "point_conditions": {
            "gas_mix": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": "vacuum"},
                }
            }
        },
    }
    state, reason = map_quantity(
        "alpha", _hk_values(), units="dimensionless", row=row
    )
    assert reason is None
    assert state.is_value and state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA

    ratio_only = _hk_values()
    del ratio_only["definition_as_printed"]
    state, reason = map_quantity(
        "alpha", ratio_only, units="dimensionless", row=row
    )
    assert not state.is_value
    assert reason and "condensation" in reason

    bare = {
        "quantity": "alpha",
        "alpha": "0.06",
        "source_term_as_printed": "condensation coefficient",
    }
    state, reason = map_quantity("alpha", bare, units="dimensionless", row=row)
    assert not state.is_value
    assert reason and "condensation" in reason

    # The section title above already says condensation. This locator does not,
    # so the refusal has to come from source_term_as_printed.
    table = {"phase": "cr", "locator": {"section": "Table 1"}}
    state, reason = map_quantity("alpha", bare, units="dimensionless", row=table)
    assert not state.is_value
    assert reason and "condensation" in reason

    film_in_definition = {
        "quantity": "alpha",
        "alpha": "0.06",
        "definition_as_printed": "Growth/condensation coefficient. " + _DEFINITION,
        "ratio_as_printed": _RATIO,
    }
    state, reason = map_quantity(
        "alpha", film_in_definition, units="dimensionless", row=table
    )
    assert not state.is_value
    assert reason and "condensation" in reason

    no_maximum = _hk_values(
        definition_as_printed=(
            "The condensation coefficient is obtained by dividing the "
            "experimentally obtained evaporation rate J_exp^Fo by J_vac^Fo."
        )
    )
    state, reason = map_quantity(
        "alpha", no_maximum, units="dimensionless", row=row
    )
    assert not state.is_value

    film = _hk_values()
    state, reason = map_quantity(
        "alpha",
        film,
        units="dimensionless",
        row={
            "standard_state": "Growth/condensation coefficient for solid SiO",
            "regime": "solid_SiO_growth_coefficient",
        },
    )
    assert not state.is_value
    assert reason and "condensation" in reason

    pound, pound_reason = map_quantity(
        "alpha",
        {"quantity": "condensation_coefficient", "alpha": "0.998"},
        units="dimensionless",
    )
    assert not pound.is_value
    assert pound_reason and "unsupported quantity" in pound_reason

    ohno, ohno_reason = map_quantity(
        "alpha",
        {
            "alpha": "0.2",
            "note": (
                "Only measured condensation coefficient in corpus. "
                "Must never pool with HKL alpha_e."
            ),
        },
        units="dimensionless",
        row={
            "regime": "condensation_coefficient_ward_machlin",
            "standard_state": "Condensation coefficient alpha(cond) from Ward and Machlin",
        },
    )
    assert not ohno.is_value
    assert ohno_reason and "condensation" in ohno_reason

    costa, costa_reason = map_quantity(
        "alpha",
        {"alpha": "0.02", "gas_species": "Fe(g)"},
        units="dimensionless",
    )
    assert costa_reason is None
    assert costa.is_value and costa.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA


def test_single_source_vacuum_is_held_and_h2_refuses_typed(tmp_path: Path) -> None:
    result = migrate(_write_tree(tmp_path, [_nagahara_document()]), write=False)
    vacuum = _one(result, _VACUUM)
    duplicate = _one(result, _VACUUM_DUP)
    h2 = _one(result, _H2)

    assert vacuum.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert h2.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert vacuum.value.kind is ValueKind.POINT
    assert vacuum.value.point == Decimal("0.06")
    assert h2.value.point == Decimal("0.2")
    assert vacuum.provenance["source_term_as_printed"] == "condensation coefficient"
    assert h2.provenance["source_term_as_printed"] == "condensation coefficient"
    assert vacuum.provenance["alpha_definition"] == HK_RATIO_ALPHA_DEFINITION
    assert vacuum.provenance["gas_system"] == ALPHA_GAS_SYSTEM_VACUUM
    assert h2.provenance["gas_system"] == ALPHA_GAS_SYSTEM_H2
    assert vacuum.provenance["melt_family"] == "cr:Mg2SiO4:vacuum"
    assert h2.provenance["melt_family"] == "cr:Mg2SiO4:H2"
    assert "Mg2SiO4-H2" not in vacuum.provenance.values()
    assert "Mg2SiO4-H2" not in h2.provenance.values()

    assert _reason(result, vacuum) == DERIVED_BAND_INSUFFICIENT_SOURCES
    assert _reason(result, duplicate) == DERIVED_BAND_INSUFFICIENT_SOURCES
    assert _reason(result, h2) == FORSTERITE_H2_EQUILIBRIUM_GAS_NOT_MODELLED

    pools = evaporation_alpha_pool_members(result.observations.values())
    vacuum_ids = {vacuum.observation_id, duplicate.observation_id}
    assert pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)] == vacuum_ids
    assert pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_H2)] == {h2.observation_id}
    assert h2.observation_id not in pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)]
    assert evaporation_alpha_loo_member_ids(result.observations.values(), vacuum) == frozenset()
    assert evaporation_alpha_loo_member_ids(result.observations.values(), h2) == frozenset()


def test_pool_membership_and_second_source_lifts_only_the_vacuum_hold(
    tmp_path: Path,
) -> None:
    result = migrate(
        _write_tree(tmp_path, [_nagahara_document(), *_companion_documents()]),
        write=False,
    )
    vacuum = _one(result, _VACUUM)
    duplicate = _one(result, _VACUUM_DUP)
    h2 = _one(result, _H2)
    second_vacuum = _one(result, _SECOND_VACUUM)
    second_h2 = _one(result, _SECOND_H2)
    melt_forsterite = _one(result, _MELT_FORSTERITE)
    melt_mg = _one(result, _MELT_MG)
    rows = result.observations.values()

    assert second_vacuum.provenance is None or (
        second_vacuum.provenance.get("alpha_definition") is None
    )
    assert evaporation_alpha_family_key(second_vacuum) == (
        "cr",
        "Mg2SiO4",
        ALPHA_GAS_SYSTEM_VACUUM,
    )
    assert evaporation_alpha_family_key(melt_forsterite) == (
        "l",
        "Mg2SiO4",
        ALPHA_GAS_SYSTEM_VACUUM,
    )
    assert evaporation_alpha_family_key(melt_mg) == (
        "l",
        "Mg",
        ALPHA_GAS_SYSTEM_VACUUM,
    )
    assert evaporation_alpha_family_key(h2) == (
        "cr",
        "Mg2SiO4",
        ALPHA_GAS_SYSTEM_H2,
    )

    pools = evaporation_alpha_pool_members(rows)
    cr_vacuum = {
        vacuum.observation_id,
        duplicate.observation_id,
        second_vacuum.observation_id,
    }
    cr_h2 = {h2.observation_id, second_h2.observation_id}
    melt_forsterite_ids = {melt_forsterite.observation_id}
    melt_mg_ids = {melt_mg.observation_id}
    assert pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)] == cr_vacuum
    assert pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_H2)] == cr_h2
    assert pools[("l", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)] == melt_forsterite_ids
    assert pools[("l", "Mg", ALPHA_GAS_SYSTEM_VACUUM)] == melt_mg_ids
    assert cr_h2.isdisjoint(pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)])
    assert cr_vacuum.isdisjoint(pools[("l", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)])
    assert cr_vacuum.isdisjoint(pools[("l", "Mg", ALPHA_GAS_SYSTEM_VACUUM)])
    assert cr_h2.isdisjoint(pools[("l", "Mg2SiO4", ALPHA_GAS_SYSTEM_VACUUM)])
    assert cr_h2.isdisjoint(pools[("l", "Mg", ALPHA_GAS_SYSTEM_VACUUM)])
    assert cr_vacuum.isdisjoint(pools[("cr", "Mg2SiO4", ALPHA_GAS_SYSTEM_H2)])

    assert evaporation_alpha_loo_member_ids(rows, vacuum) == {
        second_vacuum.observation_id
    }
    assert evaporation_alpha_loo_member_ids(rows, duplicate) == {
        second_vacuum.observation_id
    }
    assert evaporation_alpha_loo_member_ids(rows, second_vacuum) == {
        vacuum.observation_id,
        duplicate.observation_id,
    }
    assert evaporation_alpha_loo_member_ids(rows, h2) == {second_h2.observation_id}
    assert evaporation_alpha_loo_member_ids(rows, second_h2) == {h2.observation_id}
    assert evaporation_alpha_loo_member_ids(rows, melt_forsterite).isdisjoint(
        cr_vacuum | cr_h2 | melt_mg_ids
    )
    assert evaporation_alpha_loo_member_ids(rows, melt_mg).isdisjoint(
        cr_vacuum | cr_h2 | melt_forsterite_ids
    )
    assert evaporation_alpha_loo_member_ids(rows, h2).isdisjoint(
        cr_vacuum | melt_forsterite_ids | melt_mg_ids
    )

    assert _reason(result, vacuum) == "non_alkali_kinetic"
    assert _reason(result, second_vacuum) == "non_alkali_kinetic"
    assert _reason(result, melt_forsterite) == "non_alkali_kinetic"
    assert _reason(result, melt_mg) == "non_alkali_kinetic"
    assert _reason(result, h2) == FORSTERITE_H2_EQUILIBRIUM_GAS_NOT_MODELLED
    assert _reason(result, second_h2) == FORSTERITE_H2_EQUILIBRIUM_GAS_NOT_MODELLED


def _sio_sample(
    components: list[list[str]],
    material: str = "amorphous SiO powder",
) -> dict:
    # Wetzel 2012 (review/x-w1103-wz12 @ 864b44277) prints amorphous SiO
    # powder as condensed_solid and a nominal pure SiO endmember. The
    # synthetic row keeps that sample shape; it is not the extract.
    return {
        "material": material,
        "form": {
            "state": {"tag": "value", "value": "condensed_solid"},
            "locator": {"page": 34, "section": "2.3.2"},
        },
        "initial_composition": {
            "state": {
                "tag": "value",
                "value": {
                    "basis": "nominal_pure_substance",
                    "amount_basis": "mole_fraction",
                    "components": components,
                },
            },
            "locator": {"page": 34, "section": "2.3.2"},
        },
    }


def _sio_alpha_observation(observation_id: str, experiment: str) -> dict:
    return {
        "observation_id": observation_id,
        "type": "alpha",
        "experiment": experiment,
        "phase": "gas",
        "units": "dimensionless",
        "locator": {"page": 34, "section": "2.3.2", "equation": "2.25"},
        "uncertainty": {"kind": "none", "verbatim": "No uncertainty stated."},
        "values": {
            "quantity": "evaporation_coefficient_alpha",
            "method_class": "measured_reduced",
            "alpha": "0.2",
        },
    }


def test_pure_sio_alpha_takes_the_sample_reservoir(tmp_path: Path) -> None:
    doc = _document(
        "synthetic-wetzel-sio-alpha",
        {
            "SiO": [
                _sio_alpha_observation("pure_knudsen_sio_alpha", "sio_knudsen"),
                _sio_alpha_observation("mixed_knudsen_sio_alpha", "sio_mixed"),
                _sio_alpha_observation("pure_langmuir_sio_alpha", "sio_langmuir"),
            ]
        },
    )
    doc["experiments"] = [
        {
            "experiment_id": "sio_knudsen",
            "method": "knudsen_effusion",
            "sample": _sio_sample([["SiO", "1"]]),
        },
        {
            "experiment_id": "sio_mixed",
            "method": "knudsen_effusion",
            "sample": _sio_sample([["SiO", "0.5"], ["SiO2", "0.5"]]),
        },
        {
            "experiment_id": "sio_langmuir",
            "method": "free_evaporation",
            "sample": _sio_sample([["SiO", "1"]]),
        },
    ]
    result = migrate(_write_tree(tmp_path, [doc]), write=False)
    pure = _one(result, "pure_knudsen_sio_alpha")
    mixed = _one(result, "mixed_knudsen_sio_alpha")
    langmuir = _one(result, "pure_langmuir_sio_alpha")

    assert pure.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert pure.identity.species is not None
    assert pure.identity.species.formula == "SiO"
    assert pure.identity.species.phase.value is Phase.G
    # Amorphous SiO powder is not a crystal. Phase.GLASS is not a substitute.
    reservoir = pure.identity.reservoir
    assert reservoir is not None and reservoir.is_unknown
    assert reservoir.value is None
    assert reservoir.reason is not None
    assert "amorphous" in reservoir.reason
    assert "Phase.CR" in reservoir.reason
    assert "crystal" in reservoir.reason

    assert mixed.identity.reservoir is None or mixed.identity.reservoir.is_unknown
    assert (
        langmuir.identity.reservoir is None
        or langmuir.identity.reservoir.is_unknown
    )


def test_glassy_vitreous_stay_unknown_and_negations_stay_crystal(
    tmp_path: Path,
) -> None:
    # b-1112. Hyphen is a non-word character, so a bare stem boundary matches
    # non-amorphous and semi-amorphous. glassy and vitreous are the same
    # printed class. None of the three becomes Phase.CR or an inferred
    # Phase.GLASS. The negated wordings stay the printed condensed_solid.
    wordings = (
        ("glassy", "glassy SiO powder", True),
        ("vitreous", "vitreous SiO powder", True),
        ("non_amorphous", "non-amorphous SiO powder", False),
        ("semi_amorphous", "semi-amorphous SiO powder", False),
    )
    experiments = []
    observations = []
    for key, material, _unknown in wordings:
        experiment_id = f"sio_{key}"
        experiments.append(
            {
                "experiment_id": experiment_id,
                "method": "knudsen_effusion",
                "sample": _sio_sample([["SiO", "1"]], material=material),
            }
        )
        observations.append(
            _sio_alpha_observation(f"pure_knudsen_{key}", experiment_id)
        )
    doc = _document("synthetic-wetzel-sio-wording", {"SiO": observations})
    doc["experiments"] = experiments
    result = migrate(_write_tree(tmp_path, [doc]), write=False)

    for key, _material, unknown in wordings:
        obs = _one(result, f"pure_knudsen_{key}")
        assert obs.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        reservoir = obs.identity.reservoir
        assert reservoir is not None
        if unknown:
            assert reservoir.is_unknown
            assert reservoir.value is None
            assert reservoir.reason is not None
            assert "Phase.CR" in reservoir.reason
            assert "crystal" in reservoir.reason
            assert "Phase.GLASS" in reservoir.reason
            assert "inferred" in reservoir.reason
            continue
        assert reservoir.is_value
        species = reservoir.value
        assert species.formula == "SiO"
        assert species.phase.value is Phase.CR
        assert species.phase.value is not Phase.GLASS
