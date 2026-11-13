"""Pins for the numeric residual transformation.

Enumerated from the scorer before the move into ``score_metrics``:

* metric operations: ``MetricOperation`` (absolute, relative, dex) and the
  quantity table ``QUANTITY_METRIC`` (every ``Quantity``, including those
  the table leaves unmapped);
* band kinds: ``_decision_band_kind`` tokens
  ``printed_1sigma``, ``printed_2sigma``, ``printed_range``,
  ``printed_unstated``, ``derived_scatter``, ``other``, ``no_band``;
* band bases: the closed ``DecisionBand.band_basis`` set.

Expected metric values are the SCHEMA algebra, not a second call to
``compute_metric``. Serialized bytes are ``to_plain`` of ``ResidualNumeric``
with the same JSON settings as ``dumps_residual_line``.
"""

from __future__ import annotations

import ast
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    MetricOperation,
    Quantity,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.migrate import to_plain
from simulator.battery.records import DecisionBand, ResidualNumeric
from simulator.battery.score import (
    _decision_band_kind,
    _reference_from_metric,
    band_dimension_matches,
    compute_metric,
    decision_band_for,
    match_status,
    metric_operation,
    populate_numeric,
)


# Quantity token → operation. Read off QUANTITY_METRIC. A quantity absent
# from that table is None: fugacity, log10_K_reaction,
# activity_coefficient_temperature_fit, mass_loss_areal_density,
# valence_ratio, delta_fH. valence_ratio is chosen by identity, not here.
EXPECTED_QUANTITY_METRIC: dict[str, str | None] = {
    "p_sat": "dex",
    "p_partial": "dex",
    "fugacity": None,
    "p_reference": "dex",
    "log10_Kf": "absolute",
    "log10_K_reaction": None,
    "log10_K_star": "absolute",
    "activity": "dex",
    "activity_coefficient": "dex",
    "activity_coefficient_temperature_fit": None,
    "evaporation_coefficient_alpha": "absolute",
    "mass_loss_fraction": "absolute",
    "mass_loss_fraction_vs_T": "relative",
    "mass_loss_areal_density": None,
    "yield_fraction": "relative",
    "o2_yield": "relative",
    "fe3_fe2_ratio": "dex",
    "valence_ratio": None,
    "ion_intensity_ratio": "dex",
    "delta_fH": None,
    "delta_fG": "absolute",
    "H_minus_H298": "absolute",
    "heat_of_fusion": "absolute",
    "entropy_of_fusion": "absolute",
    "partial_molar_enthalpy": "absolute",
    "enthalpy_of_vaporization_2nd_law": "absolute",
    "enthalpy_of_vaporization_3rd_law": "absolute",
    "cp": "absolute",
    "S": "absolute",
    "evaporation_rate": "relative",
    "mass_loss_rate": "relative",
    "wall_deposit_mass": "relative",
    "wall_deposit_areal_rate": "relative",
    "transition_temperature": "absolute",
    "viscosity": "dex",
    "density": "relative",
    "electrical_conductivity": "dex",
    "isotope_delta": "absolute",
    "condensate_composition": "absolute",
    "liquidus_composition": "absolute",
    "evolved_gas_yield": "relative",
    "ion_intensity": "relative",
    "interaction_parameter": "absolute",
    "residue_component_composition": "absolute",
    "element_retention_ratio": "dex",
    "equilibrium_phase_composition": "absolute",
    "distribution_ratio": "dex",
    "sulfide_capacity": "absolute",
    "chloride_capacity": "absolute",
}

# Rule prefixes _decision_band_kind keeps, plus other and no_band.
BAND_KINDS = frozenset({
    "printed_1sigma",
    "printed_2sigma",
    "printed_range",
    "printed_unstated",
    "derived_scatter",
    "other",
    "no_band",
})

BAND_BASES = frozenset({
    "row_printed",
    "source_method",
    "printed_plus_derived_bound",
    "derived_bound",
    "fusion_combined_row_printed",
    "fusion_combined_source_method",
    "derived_scatter",
})


def _bytes(numeric: ResidualNumeric) -> str:
    payload = to_plain(numeric)
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def _algebra(operation: MetricOperation, candidate: Decimal, reference: Decimal) -> Decimal | None:
    """SCHEMA metric. absolute = C−R; relative = (C−R)/|R| (R≠0); dex = log10(C/R)."""

    if not candidate.is_finite() or not reference.is_finite():
        return None
    if operation is MetricOperation.ABSOLUTE:
        return candidate - reference
    if operation is MetricOperation.RELATIVE:
        if reference == 0:
            return None
        return (candidate - reference) / abs(reference)
    if operation is MetricOperation.DEX:
        if candidate <= 0 or reference <= 0:
            return None
        return Decimal(str(math.log10(float(candidate / reference))))
    return None


def test_every_quantity_has_one_pinned_metric_operation() -> None:
    assert set(EXPECTED_QUANTITY_METRIC) == {quantity.value for quantity in Quantity}
    for quantity in Quantity:
        expected = EXPECTED_QUANTITY_METRIC[quantity.value]
        produced = metric_operation(quantity)
        if expected is None:
            assert produced is None
        else:
            assert produced is MetricOperation(expected)
    assert metric_operation(None) is None


@pytest.mark.parametrize(
    ("operation", "candidate", "reference"),
    [
        (MetricOperation.ABSOLUTE, Decimal("5"), Decimal("5")),
        (MetricOperation.ABSOLUTE, Decimal("12"), Decimal("10")),
        (MetricOperation.ABSOLUTE, Decimal("3"), Decimal("8")),
        (MetricOperation.ABSOLUTE, Decimal("4"), Decimal("0")),
        (MetricOperation.RELATIVE, Decimal("5"), Decimal("5")),
        (MetricOperation.RELATIVE, Decimal("12"), Decimal("10")),
        (MetricOperation.RELATIVE, Decimal("1"), Decimal("-2")),
        (MetricOperation.RELATIVE, Decimal("1"), Decimal("0")),
        (MetricOperation.DEX, Decimal("5"), Decimal("5")),
        (MetricOperation.DEX, Decimal("10"), Decimal("1")),
        (MetricOperation.DEX, Decimal("2"), Decimal("1")),
        (MetricOperation.DEX, Decimal("1e-9"), Decimal("1")),
        (MetricOperation.DEX, Decimal("1e-300"), Decimal("1")),
        (MetricOperation.DEX, Decimal("0"), Decimal("1")),
        (MetricOperation.DEX, Decimal("-1"), Decimal("1")),
        (MetricOperation.DEX, Decimal("1"), Decimal("0")),
        (MetricOperation.DEX, Decimal("1"), Decimal("-1")),
        (MetricOperation.ABSOLUTE, Decimal("NaN"), Decimal("1")),
        (MetricOperation.RELATIVE, Decimal("1"), Decimal("Infinity")),
    ],
)
def test_compute_metric_matches_schema_algebra(
    operation: MetricOperation, candidate: Decimal, reference: Decimal
) -> None:
    assert set(MetricOperation) == {
        MetricOperation.ABSOLUTE,
        MetricOperation.RELATIVE,
        MetricOperation.DEX,
    }
    expected = _algebra(operation, candidate, reference)
    assert compute_metric(operation, candidate, reference) == expected


def test_reference_recovers_from_the_metric_algebra() -> None:
    # absolute: residual = C − R, so R = C − residual.
    assert _reference_from_metric(
        MetricOperation.ABSOLUTE, Decimal("12"), Decimal("2")
    ) == Decimal("10")
    # relative, R > 0: residual = C/R − 1, so R = C / (1 + residual).
    # 12 / 1.2 = 10. The positive root is tried first. A residual that also
    # fits a negative reference (C = 1, residual = 1.5 fits R = 0.4 and
    # R = −2) returns the positive root.
    assert _reference_from_metric(
        MetricOperation.RELATIVE, Decimal("12"), Decimal("0.2")
    ) == Decimal("10")
    assert _reference_from_metric(
        MetricOperation.RELATIVE, Decimal("1"), Decimal("1.5")
    ) == Decimal("0.4")
    # relative, R < 0, and the positive denominator is zero:
    # residual = −1, C = −4 → R = C / (1 − residual) = −4 / 2 = −2.
    assert _reference_from_metric(
        MetricOperation.RELATIVE, Decimal("-4"), Decimal("-1")
    ) == Decimal("-2")
    # dex: residual = log10(C/R), so R = C / 10^residual. Sanity: residual 0 → R = C.
    assert _reference_from_metric(
        MetricOperation.DEX, Decimal("10"), Decimal("1")
    ) == Decimal("1")
    assert _reference_from_metric(
        MetricOperation.DEX, Decimal("7"), Decimal("0")
    ) == Decimal("7")


def _band(
    width: str,
    unit: str,
    kind: str,
    basis: str,
    *,
    rule: str | None = None,
) -> DecisionBand:
    text = rule if rule is not None else f"{kind}: fixture"
    return DecisionBand(Decimal(width), unit, text, band_basis=basis)


# Each row is one populate_numeric result. ``band`` None means no band was
# supplied. ``force_fields`` are extra populate_numeric keywords.
_ROWS: tuple[dict[str, object], ...] = (
    {
        "name": "printed_1sigma-in",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": _band("3", "kJ_per_declared_mol_basis", "printed_1sigma", "row_printed"),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"row_printed","rule":"printed_1sigma: fixture",'
            '"unit":"kJ_per_declared_mol_basis","value":"3"},"operation":"absolute",'
            '"unit":"kJ_per_declared_mol_basis","value":"2"}'
        ),
    },
    {
        "name": "printed_2sigma-edge",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": _band("2", "kJ_per_declared_mol_basis", "printed_2sigma", "source_method"),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"source_method","rule":"printed_2sigma: fixture",'
            '"unit":"kJ_per_declared_mol_basis","value":"2"},"operation":"absolute",'
            '"unit":"kJ_per_declared_mol_basis","value":"2"}'
        ),
    },
    {
        "name": "printed_range-out",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": _band(
            "1", "kJ_per_declared_mol_basis", "printed_range", "printed_plus_derived_bound"
        ),
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"printed_plus_derived_bound",'
            '"rule":"printed_range: fixture","unit":"kJ_per_declared_mol_basis","value":"1"},'
            '"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"}'
        ),
    },
    {
        "name": "printed_unstated-relative-in",
        "quantity": Quantity.DENSITY,
        "candidate": "12",
        "reference": "10",
        "band": _band("1", "dimensionless", "printed_unstated", "derived_bound"),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"derived_bound","rule":"printed_unstated: fixture",'
            '"unit":"dimensionless","value":"1"},"operation":"relative",'
            '"unit":"dimensionless","value":"0.2"}'
        ),
    },
    {
        "name": "derived_scatter-dex-edge",
        "quantity": Quantity.P_SAT,
        "candidate": "10",
        "reference": "1",
        "band": _band(
            "1", "dimensionless", "derived_scatter", "fusion_combined_row_printed"
        ),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"fusion_combined_row_printed",'
            '"rule":"derived_scatter: fixture","unit":"dimensionless","value":"1"},'
            '"operation":"dex","unit":"dimensionless","value":"1"}'
        ),
    },
    {
        "name": "other-unclassified-dex-in",
        "quantity": Quantity.ACTIVITY,
        "candidate": "10",
        "reference": "1",
        "band": _band(
            "5",
            "dimensionless",
            "other",
            "fusion_combined_source_method",
            rule="unclassified_width: fixture",
        ),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"fusion_combined_source_method",'
            '"rule":"unclassified_width: fixture","unit":"dimensionless","value":"5"},'
            '"operation":"dex","unit":"dimensionless","value":"1"}'
        ),
    },
    {
        "name": "other-kems-envelope-out",
        "quantity": Quantity.P_PARTIAL,
        "candidate": "10",
        "reference": "1",
        "band": _band(
            "0.5",
            "dimensionless",
            "other",
            "derived_scatter",
            rule="KEMS p_partial measured uncertainty: fixture envelope",
        ),
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"derived_scatter",'
            '"rule":"KEMS p_partial measured uncertainty: fixture envelope",'
            '"unit":"dimensionless","value":"0.5"},"operation":"dex",'
            '"unit":"dimensionless","value":"1"}'
        ),
    },
    {
        "name": "negative-edge-match",
        "quantity": Quantity.DELTA_FG,
        "candidate": "8",
        "reference": "10",
        "band": _band("2", "kJ_per_declared_mol_basis", "printed_unstated", "derived_scatter"),
        "status": ResidualStatus.MATCH,
        "payload": (
            '{"decision_band":{"band_basis":"derived_scatter",'
            '"rule":"printed_unstated: fixture","unit":"kJ_per_declared_mol_basis","value":"2"},'
            '"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"-2"}'
        ),
    },
    {
        "name": "negative-just-outside",
        "quantity": Quantity.DELTA_FG,
        "candidate": "8",
        "reference": "10",
        "band": _band(
            "1.999", "kJ_per_declared_mol_basis", "printed_1sigma", "row_printed"
        ),
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"row_printed","rule":"printed_1sigma: fixture",'
            '"unit":"kJ_per_declared_mol_basis","value":"1.999"},"operation":"absolute",'
            '"unit":"kJ_per_declared_mol_basis","value":"-2"}'
        ),
    },
    {
        "name": "phase-mass-percent-absolute-out",
        "quantity": Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        "candidate": "10",
        "reference": "8",
        "band": _band("0.2", "mass_percent", "printed_1sigma", "row_printed"),
        "operation_override": MetricOperation.ABSOLUTE,
        "band_operation": MetricOperation.ABSOLUTE,
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"row_printed","rule":"printed_1sigma: fixture",'
            '"unit":"mass_percent","value":"0.2"},"operation":"absolute",'
            '"unit":"subtype_defined","value":"2"}'
        ),
    },
    {
        "name": "phase-mole-percent-absolute-out",
        "quantity": Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        "candidate": "4",
        "reference": "1",
        "band": _band("1", "mole_percent", "printed_range", "source_method"),
        "operation_override": MetricOperation.ABSOLUTE,
        "band_operation": MetricOperation.ABSOLUTE,
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"source_method","rule":"printed_range: fixture",'
            '"unit":"mole_percent","value":"1"},"operation":"absolute",'
            '"unit":"subtype_defined","value":"3"}'
        ),
    },
    {
        "name": "residue-mass-percent-absolute-out",
        "quantity": Quantity.RESIDUE_COMPONENT_COMPOSITION,
        "candidate": "3",
        "reference": "1",
        "band": _band("1", "mass_percent", "derived_scatter", "derived_scatter"),
        "operation_override": MetricOperation.ABSOLUTE,
        "band_operation": MetricOperation.ABSOLUTE,
        "status": ResidualStatus.MISMATCH,
        "payload": (
            '{"decision_band":{"band_basis":"derived_scatter","rule":"derived_scatter: fixture",'
            '"unit":"mass_percent","value":"1"},"operation":"absolute",'
            '"unit":"subtype_defined","value":"2"}'
        ),
    },
    {
        "name": "no-band",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"}',
    },
    {
        "name": "dex-near-zero",
        "quantity": Quantity.P_SAT,
        "candidate": "1e-9",
        "reference": "1",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"dex","unit":"dimensionless","value":"-9"}',
    },
    {
        "name": "dex-float-grain",
        "quantity": Quantity.P_SAT,
        "candidate": "2",
        "reference": "1",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"dex","unit":"dimensionless","value":"0.3010299956639812"}',
    },
    {
        "name": "dex-1e-300",
        "quantity": Quantity.P_SAT,
        "candidate": "1e-300",
        "reference": "1",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"dex","unit":"dimensionless","value":"-300"}',
    },
    {
        "name": "absolute-zero-reference",
        "quantity": Quantity.DELTA_FG,
        "candidate": "4",
        "reference": "0",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"4"}',
    },
    {
        "name": "relative-negative-reference",
        "quantity": Quantity.DENSITY,
        "candidate": "1",
        "reference": "-2",
        "band": None,
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"relative","unit":"dimensionless","value":"1.5"}',
    },
    {
        "name": "unknown-unit-fails-closed",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": _band("1", "kcal", "printed_1sigma", "row_printed"),
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"}',
    },
    {
        "name": "kj-band-on-heat-capacity-dropped",
        "quantity": Quantity.CP,
        "candidate": "50",
        "reference": "40",
        "band": _band("1", "kJ_per_declared_mol_basis", "printed_1sigma", "row_printed"),
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"J_per_declared_mol_basis_per_K","value":"10"}',
    },
    {
        "name": "phase-mass-percent-without-absolute-operation-dropped",
        "quantity": Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        "candidate": "10",
        "reference": "8",
        "band": _band("0.2", "mass_percent", "printed_1sigma", "row_printed"),
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"subtype_defined","value":"2"}',
    },
    {
        "name": "kems-band-does-not-apply-to-delta-fG",
        "quantity": Quantity.DELTA_FG,
        "candidate": "12",
        "reference": "10",
        "band": _band(
            "0.5",
            "dimensionless",
            "other",
            "derived_scatter",
            rule="KEMS p_partial measured uncertainty: fixture envelope",
        ),
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"}',
    },
    {
        "name": "printed-refusal-blocks-derived-band",
        "quantity": Quantity.DELTA_FG,
        "candidate": "15",
        "reference": "10",
        "band": _band("9", "kJ_per_declared_mol_basis", "derived_scatter", "derived_scatter"),
        "printed_refusal": {"reason": "unparseable_printed_uncertainty"},
        "status": ResidualStatus.NO_BAND,
        "payload": '{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"5"}',
    },
)


def test_band_table_covers_every_kind_and_basis() -> None:
    kinds = set()
    bases = set()
    for row in _ROWS:
        band = row["band"]
        if band is None:
            kinds.add("no_band")
            continue
        assert isinstance(band, DecisionBand)
        kinds.add(_decision_band_kind(band))
        bases.add(band.band_basis)
    assert kinds == BAND_KINDS
    assert bases == BAND_BASES
    assert _decision_band_kind(None) == "no_band"


@pytest.mark.parametrize("row", _ROWS, ids=[str(row["name"]) for row in _ROWS])
def test_populate_numeric_and_match_status_bytes(row: dict[str, object]) -> None:
    band = row["band"]
    kwargs: dict[str, object] = {
        "quantity": row["quantity"],
        "candidate": Decimal(str(row["candidate"])),
        "reference": Decimal(str(row["reference"])),
        "source_relation": SourceRelation.INDEPENDENT,
    }
    if isinstance(band, DecisionBand):
        kwargs["derived_band"] = band
    if "operation_override" in row:
        kwargs["operation_override"] = row["operation_override"]
        kwargs["band_operation"] = row["band_operation"]
    if "printed_refusal" in row:
        kwargs["printed_refusal"] = row["printed_refusal"]
        kwargs["derived_band"] = band
    numeric, reason, detail = populate_numeric(**kwargs)
    assert reason is None
    assert detail == {}
    assert isinstance(numeric, ResidualNumeric)
    assert _bytes(numeric) == row["payload"]
    assert match_status(numeric) is row["status"]
    assert numeric.value == _algebra(
        numeric.operation,
        Decimal(str(row["candidate"])),
        Decimal(str(row["reference"])),
    )


def test_zero_reference_and_unmapped_quantity_refuse() -> None:
    numeric, reason, detail = populate_numeric(
        quantity=Quantity.DENSITY,
        candidate=Decimal("1"),
        reference=Decimal("0"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert numeric is None
    assert reason is RefusalReason.METRIC_DOMAIN
    assert detail == {"operation": "relative", "candidate": "1", "reference": "0"}
    assert match_status(
        ResidualNumeric(MetricOperation.RELATIVE, "dimensionless", Decimal("0"), None)
    ) is ResidualStatus.NO_BAND

    numeric, reason, detail = populate_numeric(
        quantity=Quantity.P_SAT,
        candidate=Decimal("0"),
        reference=Decimal("1"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert numeric is None
    assert reason is RefusalReason.METRIC_DOMAIN
    assert detail == {"operation": "dex", "candidate": "0", "reference": "1"}

    numeric, reason, detail = populate_numeric(
        quantity=Quantity.FUGACITY,
        candidate=Decimal("33.7"),
        reference=Decimal("1"),
        source_relation=SourceRelation.INDEPENDENT,
    )
    assert numeric is None
    assert reason is RefusalReason.METRIC_DOMAIN
    assert detail == {"quantity": "fugacity"}


def test_dimension_mismatch_drops_the_band_and_refuses_if_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    band = DecisionBand(
        Decimal("1"),
        "kJ_per_declared_mol_basis",
        "printed_1sigma: source-owned test band",
    )
    assert band_dimension_matches(Quantity.CP, band) is False
    assert (
        decision_band_for(
            Quantity.CP, SourceRelation.INDEPENDENT, derived_band=band
        )
        is None
    )
    numeric, reason, detail = populate_numeric(
        quantity=Quantity.CP,
        candidate=Decimal("50"),
        reference=Decimal("40"),
        source_relation=SourceRelation.INDEPENDENT,
        derived_band=band,
    )
    assert reason is None and detail == {}
    assert isinstance(numeric, ResidualNumeric)
    assert numeric.decision_band is None
    assert match_status(numeric) is ResidualStatus.NO_BAND
    assert _bytes(numeric) == (
        '{"operation":"absolute","unit":"J_per_declared_mol_basis_per_K","value":"10"}'
    )

    # The dimension guard refuses a band that selection was replaced to keep.
    # Patch the module that defines populate_numeric so the guard sees it.
    owner = sys.modules[populate_numeric.__module__]
    monkeypatch.setattr(owner, "decision_band_for", lambda *args, **kwargs: band)
    forced, forced_reason, forced_detail = populate_numeric(
        quantity=Quantity.CP,
        candidate=Decimal("50"),
        reference=Decimal("40"),
        source_relation=SourceRelation.INDEPENDENT,
        derived_band=band,
    )
    assert forced is None
    assert forced_reason is RefusalReason.DECISION_RULE_MISSING
    assert forced_detail == {
        "reason": "band_dimension_mismatch:cp",
        "quantity": "cp",
        "quantity_unit": "J_per_declared_mol_basis_per_K",
        "band_unit": "kJ_per_declared_mol_basis",
        "operation": "absolute",
    }


_MOVED_NAMES = (
    "QUANTITY_METRIC",
    "_phase_component_reference_wt_percent",
    "_reference_from_metric",
    "_residual_metric_operation",
    "band_dimension_matches",
    "compute_metric",
    "decision_band_for",
    "match_status",
    "metric_operation",
    "metric_operation_for_identity",
    "populate_numeric",
)

_METRICS_IMPORT_MODULES = frozenset({
    "__future__",
    "math",
    "decimal",
    "typing",
    "simulator.battery.enums",
    "simulator.battery.identity",
    "simulator.battery.records",
})


def test_score_metrics_owns_the_block_and_imports_no_scorer() -> None:
    """The metric module owns its API and score.py imports only real callers.

    Allowed imports are the stdlib plus records, enums, and identity.
    score.py, score_report, migrate, and the engines stay out.
    """

    import simulator.battery.score as score
    import simulator.battery.score_metrics as metrics

    root = Path(metrics.__file__).resolve().parent
    metrics_tree = ast.parse((root / "score_metrics.py").read_text(encoding="utf-8"))
    for node in ast.walk(metrics_tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module in _METRICS_IMPORT_MODULES, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in _METRICS_IMPORT_MODULES, alias.name

    score_tree = ast.parse((root / "score.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    defined: list[str] = []
    for node in score_tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "simulator.battery.score_metrics":
            imported.extend(alias.name for alias in node.names)
        if isinstance(node, ast.FunctionDef) and node.name in _MOVED_NAMES:
            defined.append(node.name)
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", None) in _MOVED_NAMES:
            defined.append(target.id)
    assert defined == []
    assert set(imported) == set(_MOVED_NAMES)
    for name in _MOVED_NAMES:
        assert getattr(score, name) is getattr(metrics, name)


def test_score_report_data_import_direction() -> None:
    """Report data uses records and identity helpers, never scoring or rendering."""

    import simulator.battery.score_report_data as report_data

    root = Path(report_data.__file__).resolve().parent
    tree = ast.parse((root / "score_report_data.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                imported.add(node.module or "")
                if node.module == "simulator.battery":
                    imported.update(
                        f"simulator.battery.{alias.name}" for alias in node.names
                    )
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert "simulator.battery.records" in imported
    assert "simulator.battery.score" not in imported
    assert "simulator.battery.score_report" not in imported
