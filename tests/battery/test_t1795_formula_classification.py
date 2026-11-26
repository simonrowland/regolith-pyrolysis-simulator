import importlib
from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Engine, EvidenceClass, Phase, RefusalReason
from simulator.battery.migrate import (
    REPO_ROOT,
    Migrator,
    _formula_phase_suffix,
    migrate,
)
from simulator.battery.records import Species
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    parse_species_formula,
)
from tests.battery import factories as F
from tests.battery.test_migrate import _write_min_tree


NON_FORMULA_OR_AMBIGUOUS = (
    "unknown",
    "kems-007",
    "gehlenite",
    "quartz",
    "gibbsite",
    "Aegirine natural sample",
    "CaCO3(calcite)",
    "Fe1-xO",
    "NH.C1",
    "[OS(O)OO]-2",
    "CN-(aq)",
    "Cu++(aq)",
    "Ag+(aq)",
    "HAsO4--(aq)",
    "$I^-(aq)$",
    "$UO_2^{++}(aq)$",
    "$NO_{2}^{-}(aq)$",
    "$S(mon)$",
    "C(graphite)",
    "$Sn(white)$",
    "Al2O3·H2O(boehmite)",
    "Ca2SiO4(γ)",
    "CaTiO3(perovskite)",
    "P(red,tric.)",
    "(8e0)2",
    "(A10)2",
    "0",
    "0s",
    "11",
    "111",
    "Gele",
    "Sbullet",
    "Til",
    "cis-N2H2",
    "forallSi2",
    "v0",
    "coo",
    "caso4",
    "cs2o",
    "cu",
    "H4SiO4o",
    "ca2•",
    "s2·",
    "so4••",
    "CaTiSiO5-CaTiO3",
    "H2SO4-7H2O",
    "Na2SO4-III",
    "VO_VO2",
    "CaSO4(sol.α)",
    "C(diamond)",
    "HNO2(cis,g)",
    "Hgf",
    "Hgf+",
    "ca2Fe2  as",
    "cao2",
    "sr2",
    "sr2+",
    "zn2",
    "zn2+",
    "Fe2Tio4",
    "c",
)

NASA_GLENN_IONIC_FORMULAS = (
    "AgE-1", "Al2O2E-1", "Al2OE-1", "AlClE-1", "AlE-1", "AlFE-1", "AlOE-1", "ArE-1",
    "BCl2E-1", "BClE-1", "BE-1", "BF2E-1", "BaClE-1", "BaE-1", "BaFE-1", "BaOE-1",
    "BaOHE-1", "BeE-1", "BeE-2", "BeHE-1", "BeOHE-1", "BrE-1", "C2E-1", "CE-1",
    "CF2E-1", "CF3E-1", "CFE-1", "CH3OE-1", "CHE-1", "CNE-1", "CO2E-1", "COE-1",
    "CaClE-1", "CaE-1", "CaFE-1", "CaOE-1", "CaOHE-1", "CdE-1", "ClE-1", "CoE-1",
    "CrE-1", "Cs2OE-1", "CsE-1", "CuE-1", "D2E-1", "DE-1", "FE-1", "FeE-1",
    "GaE-1", "GeE-1", "H2E-1", "H2OE-1", "H3OE-1", "HBOE-1", "HBSE-1", "HCOE-1",
    "HDE-1", "HE-1", "HeE-1", "HgE-1", "IE-1", "InE-1", "K2E-1", "K2OE-1",
    "KE-1", "KrE-1", "Li2E-1", "Li2OE-1", "Li3E-1", "LiE-1", "MgClE-1", "MgE-1",
    "MgF2E-1", "MgFE-1", "MgOHE-1", "MnE-1", "MoE-1", "N2E-1", "N2OE-1", "NE-1",
    "NH4E-1", "NHE-1", "NOE-1", "Na2OE-1", "NaE-1", "NaOHE-1", "NbE-1", "NeE-1",
    "NiE-1", "O2E-1", "OE-1", "OHE-1", "PE-1", "PFE-1", "PbE-1", "RbE-1",
    "RnE-1", "SCl2E-1", "SE-1", "SF2E-1", "SF3E-1", "SF4E-1", "SF5E-1", "SFE-1",
    "ScE-1", "ScOE-1", "SiE-1", "SiHE-1", "SnE-1", "SrClE-1", "SrE-1", "SrFE-1",
    "SrOE-1", "SrOHE-1", "TaE-1", "TiE-1", "TiOE-1", "UF2E-1", "UF3E-1", "UF4E-1",
    "UF5E-1", "UFE-1", "UO2E-1", "UOE-1", "VE-1", "WE-1", "XeE-1", "ZnE-1",
    "ZrE-1", "ZrOE-1",
)

CLASS_B_FORMULAS = NON_FORMULA_OR_AMBIGUOUS + NASA_GLENN_IONIC_FORMULAS


@pytest.mark.parametrize(
    "formula",
    (
        "CaFe2o4",
        "CaMgSio4",
        "CuFes2",
        "Fe. 875 s",
        "Fe.94 7o",
        "MgFe2o4",
        "Na2so4·10H2o",
        "NaAlSio4",
        "Naf",
        "ZnFe2o4",
        "cawo4",
        "cao",
        "srso4",
        "u",
        "v",
        "w",
        "ws2",
    ),
)
def test_case_repair_without_source_weight_stays_unparsed(formula: str) -> None:
    assert parse_species_formula(formula) is None


@pytest.mark.parametrize(
    ("formula", "expected"),
    (
        ("Fe.875S", (("Fe", 0.875), ("S", 1.0))),
        ("Fe.90S", (("Fe", 0.90), ("S", 1.0))),
        ("Fe.95O", (("Fe", 0.95), ("O", 1.0))),
    ),
)
def test_exact_case_formula_remains_parseable_without_source_weight(
    formula: str, expected: tuple[tuple[str, float], ...]
) -> None:
    assert parse_species_formula(formula) == expected


@pytest.mark.parametrize("formula", CLASS_B_FORMULAS)
def test_non_formula_and_ambiguous_species_keep_typed_refusal(
    formula: str,
) -> None:
    experiment = F.tabulation_experiment(T_K=Decimal("1000"))
    normalized, suffix_phase = _formula_phase_suffix(formula, None)
    identity = replace(
        F.o2_identity(Decimal("1000")),
        species=Species(
            normalized,
            suffix_phase.value
            if suffix_phase is not None and suffix_phase.is_value
            else Phase.UNSPECIFIED,
        ),
    )
    observation = F.observation(
        f"t1795-refusal-{formula}",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="t1795-test-source",
    )
    context = ScoreContext(
        works={experiment.work_id: F.work(experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
    )

    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
    )

    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
    assert residual.refusal.detail == {
        "reason": "species_formula_unparsed",
        "formula": identity.species.formula,
    }


def test_ledger_phase_suffix_moves_to_species_identity(tmp_path) -> None:
    root = _write_min_tree(tmp_path)
    formulas = ("H2S2(g)", "H2SO4(l)", "H2SO4(g)", "Fe2O3[c]")
    (root / "data" / "literature" / "species_rail_differential_ledger.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "metric_units": "kJ/mol",
                "points": [
                    {
                        "key": f"t1795-phase-{index}",
                        "species": formula,
                        "comparison_quantity": "delta_fG_kJ_mol",
                        "temperature_K": 298.15,
                        "table_kJ_mol": -1.0,
                        "provenance_class": "independent_tabulation",
                    }
                    for index, formula in enumerate(formulas)
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = migrate(root, write=False)
    expected = (
        (Phase.G, "H2S2"),
        (Phase.L, "H2SO4"),
        (Phase.G, "H2SO4"),
        (Phase.CR, "Fe2O3"),
    )
    for index, (phase, formula) in enumerate(expected):
        species = result.observations[f"t1795-phase-{index}"].identity.species
        assert species.formula == formula
        assert species.phase.is_value and species.phase.value is phase
        assert parse_species_formula(species.formula, species=species) is not None


def _assert_pankratz_shard_one_phase_suffixes_lift() -> None:
    source_dir = (
        REPO_ROOT
        / "data/literature/compilations/pankratz-1987-usbm-b689"
    )
    migrator = Migrator(root=REPO_ROOT)
    migrator.migrate_compilations(source_dir)
    migrator.finalize()

    expected = {
        "page-0143": ("H2S2", Phase.G),
        "page-0144": ("H2S2", Phase.G),
        "page-0145": ("H2SO4", Phase.L),
        "page-0146": ("H2SO4", Phase.L),
        "page-0147": ("H2SO4", Phase.G),
        "page-0148": ("H2SO4", Phase.G),
    }
    for record_id, (formula, phase) in expected.items():
        rows = [
            observation
            for observation in migrator.result.observations.values()
            if f":{record_id}" in observation.observation_id
        ]
        assert rows
        assert all(
            observation.identity.species is not None
            and observation.identity.species.formula == formula
            and observation.identity.species.phase.is_value
            and observation.identity.species.phase.value is phase
            for observation in rows
        )


def test_pankratz_shard_one_phase_suffixes_lift_on_real_records() -> None:
    _assert_pankratz_shard_one_phase_suffixes_lift()


def test_phase_lift_mutation_probe_detects_old_pankratz_consumer(
    monkeypatch,
) -> None:
    migrate_module = importlib.import_module("simulator.battery.migrate")
    monkeypatch.setattr(
        migrate_module,
        "_formula_phase_suffix",
        lambda formula, printed_phase: (formula, None),
    )
    with pytest.raises(AssertionError):
        _assert_pankratz_shard_one_phase_suffixes_lift()
