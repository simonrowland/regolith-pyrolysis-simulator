"""b-686: Fe3+/Fe2+ under the existing quantity fe3_fe2_ratio (no new quantity).

The pin commit (006e13b70) captured green e702b2551: a row declaring
fe3_fe2_ratio selected no number from Fe3+/sum Fe, Kress-style Fe2O3/FeO
wt %, or a directly printed ratio, and Aithala 2026's Table S-1 Fe3+/FeT rows
migrated with an unknown quantity ('unsupported quantity Fe3+/FeT'). The
extract now declares quantity fe3_fe2_ratio on the 16 measured Table S-1 rows
(the printed Fe3_over_FeT is unchanged). Expectations flipped by the change
are marked "b-686 change".

Reductions (derivation in simulator/battery/migrate.py, b-686 block):
  ratio = r/(1 - r) from r = Fe3+/sum Fe;
  ratio = 2 (w_Fe2O3/M(Fe2O3))/(w_FeO/M(FeO)) = 0.8998 w_Fe2O3/w_FeO,
  molar masses from oxide_molar_mass (FeO 71.844, Fe2O3 159.687).
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import (
    FERRIC_FRACTION_RELATION,
    Migrator,
    fe3_fe2_ratio_from_ferric_fraction,
    fe3_fe2_ratio_from_oxide_wt_pct,
    fe3_fe2_ratio_reduction,
    kress_wt_pct_relation,
    oxide_molar_mass,
    select_declared_source,
)
from simulator.battery.records import Evidence, Locator, State

REPO = Path(__file__).resolve().parents[2]
AITHALA = REPO / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"

# Table S-1 (PDF p. 13, SI-7): run id -> printed Fe3+/FeT, as in the extract.
AITHALA_S1_FE3_OVER_FET = {
    "aithala_2026_vf271_xanes": "0.816",
    "aithala_2026_vf267_moessbauer": "0.736",
    "aithala_2026_vf231_xanes": "0.726",
    "aithala_2026_vf231_moessbauer": "0.716",
    "aithala_2026_vf268_xanes": "0.65",
    "aithala_2026_vf268_moessbauer": "0.695",
    "aithala_2026_vf270_xanes": "0.685",
    "aithala_2026_vf270_moessbauer": "0.661",
    "aithala_2026_vf274_xanes": "0.637",
    "aithala_2026_vf274_moessbauer": "0.643",
    "aithala_2026_1800_30_xanes": "0.426",
    "aithala_2026_1900_30_xanes": "0.389",
    "aithala_2026_2000_30_xanes": "0.407",
    "aithala_2026_2100_30_xanes": "0.306",
    "aithala_2026_2000_10_xanes": "0.378",
    "aithala_2026_2000_120_xanes": "0.378",
}


PREFIX = "aithala-2026-magmatic-iron-redox-to-2100c::"


def _migrate(path: Path):
    migrator = Migrator()
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _aithala_fe_rows():
    result = _migrate(AITHALA)
    return {
        oid[len(PREFIX):]: obs
        for oid, obs in result.observations.items()
        if oid[len(PREFIX):] in AITHALA_S1_FE3_OVER_FET
    }


def test_aithala_s1_rows_migrate_to_fe3_fe2_ratio_measured_reduced() -> None:
    # b-686 change. Pin (006e13b70): quantity unknown ("unsupported quantity
    # 'Fe3+/FeT'"), value UNAVAILABLE, evidence measured_direct.
    rows = _aithala_fe_rows()
    assert set(rows) == set(AITHALA_S1_FE3_OVER_FET)
    for oid, obs in rows.items():
        r = Decimal(AITHALA_S1_FE3_OVER_FET[oid])
        assert obs.identity.quantity.value is Quantity.FE3_FE2_RATIO, oid
        assert obs.value.kind is ValueKind.POINT, oid
        assert obs.value.point == r / (1 - r), oid
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED, oid
        assert obs.evidence.original_method_class == "measured_direct", oid
        assert obs.derivation.relation == FERRIC_FRACTION_RELATION, oid
        assert obs.derivation.output_unit == "dimensionless", oid
        (name, printed), = obs.derivation.parameters
        assert name == "original_Fe3_over_FeT", oid
        assert printed.state.value == r, oid
        assert printed.locator.table == "S-1", oid


def test_aithala_s3_recalculated_rows_are_not_declared_fe3_fe2_ratio() -> None:
    # Table S-3 rows (species.Fe.context[]) are the authors' model recasting
    # of the S-1 measurements to air and the S-2 reference composition
    # (model_derived). They stay labelled Fe3+/FeT and are not declared
    # fe3_fe2_ratio, so they cannot double-count the measured rows.
    doc = yaml.safe_load(AITHALA.read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    labels = [
        (row.get("type"), row["values"]["quantity"])
        for row in walk(doc["species"])
        if isinstance(row.get("values"), dict) and "quantity" in row["values"]
        and "Fe3_over_FeT" in row["values"]
    ]
    assert labels.count(("concentration_series", "fe3_fe2_ratio")) == 16
    assert labels.count(("recalculation_table", "Fe3+/FeT")) == 20
    assert len(labels) == 36
    result = _migrate(AITHALA)
    ratio_rows = [
        oid for oid, obs in result.observations.items()
        if obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    ]
    assert sorted(oid[len(PREFIX):] for oid in ratio_rows) == sorted(AITHALA_S1_FE3_OVER_FET)


def _fixture_extract(tmp_path: Path, rows: list[dict]) -> Path:
    doc = yaml.safe_load(AITHALA.read_text(encoding="utf-8"))
    template = next(
        o for o in doc["species"]["Fe"]["observations"]
        if o["observation_id"] == "aithala_2026_vf271_xanes"
    )
    built = []
    for row in rows:
        obs = copy.deepcopy(template)
        obs.update(row)
        built.append({k: v for k, v in obs.items() if v is not None})
    doc["species"]["Fe"]["observations"] = built
    out = tmp_path / AITHALA.name
    out.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return out


def test_migrator_reduces_kress_wt_pct_row(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_kress",
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "Fe2O3_wt_pct": "1.09",
            "FeO_wt_pct": "10.34",
        },
    }])
    obs = _migrate(path).observations[PREFIX + "fixture_kress"]
    assert obs.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert obs.value.point == fe3_fe2_ratio_from_oxide_wt_pct(Decimal("1.09"), Decimal("10.34"))
    assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert obs.derivation.relation == kress_wt_pct_relation()
    assert [(n, p.state.value) for n, p in obs.derivation.parameters] == [
        ("original_Fe2O3_wt_pct", Decimal("1.09")),
        ("original_FeO_wt_pct", Decimal("10.34")),
    ]


def test_migrator_reduces_ferric_fraction_series_points(tmp_path: Path) -> None:
    path = _fixture_extract(tmp_path, [{
        "observation_id": "fixture_series",
        "T_range_K": None,
        "values": {
            "method_class": "measured_direct",
            "quantity": "fe3_fe2_ratio",
            "series": [
                {"temperature_K": 1523.15, "Fe3_over_FeT": 0.5},
                {"temperature_K": 1573.15, "Fe3_over_FeT": 0.25},
            ],
        },
    }])
    points = {
        oid: obs for oid, obs in _migrate(path).observations.items()
        if oid.startswith(PREFIX + "fixture_series::")
    }
    assert sorted(obs.value.point for obs in points.values()) == [
        Decimal("0.25") / Decimal("0.75"), Decimal(1)
    ]
    for obs in points.values():
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert obs.derivation.relation == FERRIC_FRACTION_RELATION
        (name, _), = obs.derivation.parameters
        assert name == "original_Fe3_over_FeT"


def test_declared_ratio_from_ferric_fraction_is_r_over_one_minus_r() -> None:
    # b-686 change: was unavailable ("no mapped fe3_fe2_ratio field selected").
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "Fe3_over_FeT": 0.25}
    )
    assert sel.available
    assert sel.amount == Decimal("0.25") / Decimal("0.75")
    assert sel.field_name == "Fe3_over_FeT"
    assert sel.unit_trail == "ferric_fraction_to_fe3_fe2_ratio"
    assert sel.reduction == (FERRIC_FRACTION_RELATION, (("Fe3_over_FeT", Decimal("0.25")),))


def test_declared_ratio_from_kress_wt_pct_is_0_8998_w3_over_w2() -> None:
    # b-686 change: was unavailable ("no mapped fe3_fe2_ratio field selected").
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO,
        None,
        {"quantity": "fe3_fe2_ratio", "Fe2O3_wt_pct": "1.09", "FeO_wt_pct": "10.34"},
    )
    assert sel.available
    m3, m2 = oxide_molar_mass("Fe2O3"), oxide_molar_mass("FeO")
    assert (m3, m2) == (Decimal("159.687"), Decimal("71.844"))
    expected = 2 * (Decimal("1.09") / m3) / (Decimal("10.34") / m2)
    assert sel.amount == expected
    # The ruling's shorthand: 2 x 71.844/159.688 = 0.8998 (4 s.f.); the
    # oxide table's 159.687 rounds to the same factor.
    assert round(2 * m2 / m3, 4) == Decimal("0.8998")
    assert abs(sel.amount - Decimal("0.8998") * Decimal("1.09") / Decimal("10.34")) < Decimal("1e-5")
    assert sel.reduction == (
        kress_wt_pct_relation(),
        (("Fe2O3_wt_pct", Decimal("1.09")), ("FeO_wt_pct", Decimal("10.34"))),
    )
    assert "0.8998 w_Fe2O3/w_FeO" in kress_wt_pct_relation()


def test_declared_ratio_printed_directly_is_read_as_printed() -> None:
    # b-686 change: was unavailable.
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "fe3_fe2_ratio": 0.5}
    )
    assert sel.available
    assert sel.amount == Decimal("0.5")
    assert sel.unit_trail == "identity"
    assert sel.reduction is None


@pytest.mark.parametrize("r", [0, 1, "1.2", "-0.1", "n.d."])
def test_ferric_fraction_outside_open_unit_interval_is_refused(r) -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": r})
    assert not sel.available
    assert sel.amount is None
    assert "fe3_fe2_ratio = r/(1 - r) is undefined" in sel.reason


@pytest.mark.parametrize(
    "w3, w2", [("0", "10.3"), ("1.09", "0"), ("-1", "10.3"), ("b.d.l.", "10.3")]
)
def test_kress_wt_pct_needs_both_oxides_positive(w3, w2) -> None:
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"Fe2O3_wt_pct": w3, "FeO_wt_pct": w2}
    )
    assert not sel.available
    assert "fe3_fe2_ratio is undefined" in sel.reason


def test_lone_feo_wt_pct_is_not_a_redox_statement() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"FeO_wt_pct": "10.34"})
    assert not sel.available
    assert sel.reduction is None


def test_domain_functions_are_exact() -> None:
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal("0.5")) == Decimal(1)
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal("0.816")) == Decimal("0.816") / Decimal("0.184")
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal(0)) is None
    assert fe3_fe2_ratio_from_ferric_fraction(Decimal(1)) is None
    # Round trip: R = r/(1 - r)  <=>  r = R/(1 + R).
    ratio = fe3_fe2_ratio_from_ferric_fraction(Decimal("0.306"))
    assert abs(ratio / (1 + ratio) - Decimal("0.306")) < Decimal("1e-25")
    # One mole Fe2O3 per mole FeO is two Fe3+ per Fe2+.
    assert fe3_fe2_ratio_from_oxide_wt_pct(
        oxide_molar_mass("Fe2O3"), oxide_molar_mass("FeO")
    ) == Decimal(2)
    assert fe3_fe2_ratio_from_oxide_wt_pct(Decimal(0), Decimal(1)) is None


def test_reduction_makes_measured_row_measured_reduced_with_printed_parent() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": "0.25"})
    locator = Locator(page=13, table="S-1")
    evidence = Evidence(
        class_=State.of(EvidenceClass.MEASURED_DIRECT),
        original_method_class="measured_direct",
    )
    reduced, derivation = fe3_fe2_ratio_reduction(sel, evidence, locator, "pdf:x")
    assert reduced.class_.value is EvidenceClass.MEASURED_REDUCED
    assert reduced.original_method_class == "measured_direct"
    assert derivation.relation == FERRIC_FRACTION_RELATION
    assert derivation.inputs == ("pdf:x",)
    assert derivation.output_unit == "dimensionless"
    (name, printed), = derivation.parameters
    assert name == "original_Fe3_over_FeT"
    assert printed.state.value == Decimal("0.25")
    assert printed.locator == locator


def test_reduction_leaves_non_measured_class_and_as_printed_rows_alone() -> None:
    sel = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"Fe3_over_FeT": "0.25"})
    model = Evidence(class_=State.of(EvidenceClass.MODEL_DERIVED), original_method_class="model_derived")
    kept, derivation = fe3_fe2_ratio_reduction(sel, model, None, "pdf:x")
    assert kept is model
    assert derivation is not None
    direct = select_declared_source(Quantity.FE3_FE2_RATIO, None, {"fe3_fe2_ratio": "0.5"})
    measured = Evidence(class_=State.of(EvidenceClass.MEASURED_DIRECT))
    assert fe3_fe2_ratio_reduction(direct, measured, None, "pdf:x") == (measured, None)
