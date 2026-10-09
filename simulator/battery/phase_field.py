"""Phase-field class of a printed binary point (t-1123a; physics E15/E16 rule).

This is the reader's general rule, not a per-source special case. The extract
carries the printed facts (a stated or held-diagram liquidus, and the printed
table cells that form a plateau); the per-point ``phase_field_class`` entry
carries the class and its basis. The reader accepts the class only if the rule
below holds on those printed facts.

A printed point is ``outside_single_liquid_field`` (class S) iff BOTH hold:

(i)  its printed mole fraction of the liquidus component lies on the outside
     side of the stated liquidus by more than the stated sigma
     (below ``position - sigma`` when ``outside_side`` is ``below``); and
(ii) it sits on a printed plateau at its temperature. Phase rule for a binary
     at fixed T with two condensed phases plus vapour (total pressure is the
     vapour's own, not imposed): F = C - P + 2 - 1 = 2 - 3 + 2 - 1 = 0, so every
     printed intensive quantity is one constant across that field. Test per
     printed series with a usable sigma:
         max_{i<j} |v_i - v_j| / sqrt(s_i**2 + s_j**2) <= 2
     over the plateau compositions. At most one series may fail, and only if
     the plateau record names it. Series with no printed sigma are untestable
     and are skipped (listed, never counted as passing).

A point that is not class S but lies on the outside side of the most-liquid
printed liquidus position (stated position + sigma, or any superseded/cited
position) is ``liquidus_position_contested``: it stays liquid and numeric, and
carries the stated liquidus and its sigma so a later refit can reclassify it
mechanically.

Classification never touches species phase or identity composition.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from simulator.battery.enums import NoticeKind

OUTSIDE_SINGLE_LIQUID_FIELD = "outside_single_liquid_field"
# One home for the token: the notice kind the migrator attaches (enums.py).
LIQUIDUS_POSITION_CONTESTED = NoticeKind.LIQUIDUS_POSITION_CONTESTED.value
TWO_TEST_CRITERION = "stated_liquidus_side_and_printed_plateau_within_2_sigma"
PLATEAU_Z_LIMIT = Decimal(2)
_Z_QUANTUM = Decimal("0.01")
_OUTSIDE_SIDES = frozenset({"below", "above"})
# "50.1 ± 4.0", "1.00 ± 0.02", "0.95", "(5.3)" (parenthesised = not a measured cell).
_PRINTED_CELL = re.compile(
    r"^\s*(?P<paren>\()?\s*(?P<v>[0-9]+(?:\.[0-9]+)?)\s*\)?\s*"
    r"(?:(?:±|\+-|-\+)\s*(?P<s>[0-9]+(?:\.[0-9]+)?))?\s*$"
)


@dataclass(frozen=True)
class Liquidus:
    record_id: str
    component: str
    position: Decimal
    sigma: Decimal
    outside_side: str
    superseded_positions: tuple[Decimal, ...]
    locator: Mapping[str, object]

    def beyond_sigma_band(self, x: Decimal) -> bool:
        """Test (i): outside the stated liquidus by more than its sigma."""

        if self.outside_side == "below":
            return x < self.position - self.sigma
        return x > self.position + self.sigma

    def outside_any_position(self, x: Decimal) -> bool:
        """Outside the most-liquid printed position (stated + sigma, or cited)."""

        if self.outside_side == "below":
            edge = max((self.position + self.sigma, *self.superseded_positions))
            return x < edge
        edge = min((self.position - self.sigma, *self.superseded_positions))
        return x > edge


@dataclass(frozen=True)
class Plateau:
    record_id: str
    compositions: tuple[Decimal, ...]
    observation_ids: frozenset[str]
    z_by_series: Mapping[str, Decimal]
    untestable: tuple[str, ...]
    failing: tuple[str, ...]
    problem: str | None

    @property
    def max_passing_z(self) -> Decimal | None:
        passing = [z for name, z in self.z_by_series.items() if name not in self.failing]
        return max(passing) if passing else None


@dataclass(frozen=True)
class PhaseFieldRecords:
    liquidus: Mapping[str, Liquidus]
    plateaus: Mapping[str, Plateau]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class PointPhaseField:
    kind: str | None
    payload: Mapping[str, object] | None
    problem: str | None = None

    def notice_reason(self) -> str:
        assert self.kind is not None and self.payload is not None
        blob = json.dumps(self.payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return f"{self.kind}:{blob}"


def _dec(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _printed_cell(raw: object) -> tuple[Decimal, Decimal | None] | None:
    match = _PRINTED_CELL.match(str(raw)) if raw is not None else None
    if match is None or match.group("paren"):
        return None
    sigma = match.group("s")
    return Decimal(match.group("v")), (Decimal(sigma) if sigma is not None else None)


def _observations_by_id(doc: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    out: dict[str, Mapping[str, object]] = {}
    species = doc.get("species")
    if not isinstance(species, Mapping):
        return out
    for block in species.values():
        rows = block.get("observations") if isinstance(block, Mapping) else None
        for obs in rows or ():
            if isinstance(obs, Mapping) and obs.get("observation_id"):
                out[str(obs["observation_id"])] = obs
    return out


def _series_z(
    cells: Mapping[Decimal, tuple[Decimal, Decimal]],
) -> Decimal:
    worst = Decimal(0)
    for (v_i, s_i), (v_j, s_j) in itertools.combinations(cells.values(), 2):
        denominator = (s_i * s_i + s_j * s_j).sqrt()
        if denominator == 0:
            raise ValueError("zero combined sigma")
        worst = max(worst, abs(v_i - v_j) / denominator)
    return worst.quantize(_Z_QUANTUM, rounding=ROUND_HALF_EVEN)


def _plateau(raw: Mapping[str, object], observations: Mapping[str, Mapping[str, object]]) -> Plateau:
    record_id = str(raw.get("id") or "")
    field = str(raw.get("composition_field") or "")
    compositions = tuple(c for c in (_dec(x) for x in raw.get("compositions") or ()) if c is not None)
    z_by_series: dict[str, Decimal] = {}
    untestable: list[str] = []
    seen_ids: set[str] = set()
    problem: str | None = None
    if len(compositions) < 2 or not field:
        problem = "plateau needs a composition_field and at least two compositions"
    for series in raw.get("series") or ():
        if not isinstance(series, Mapping):
            continue
        obs_id = str(series.get("observation_id") or "")
        seen_ids.add(obs_id)
        obs = observations.get(obs_id)
        values = obs.get("values") if isinstance(obs, Mapping) else None
        points = values.get("points") if isinstance(values, Mapping) else None
        if not isinstance(points, list):
            problem = problem or f"plateau series {obs_id!r} is not a printed point table"
            continue
        basis = str(series.get("sigma_basis") or "")
        if basis == "none_printed":
            untestable.append(obs_id)
            continue
        relative = _dec(series.get("relative_sigma")) if basis == "relative" else None
        cells: dict[Decimal, tuple[Decimal, Decimal]] = {}
        for point in points:
            if not isinstance(point, Mapping):
                continue
            x = _dec(point.get(field))
            if x is None or x not in compositions:
                continue
            cell = _printed_cell(point.get(str(series.get("field") or "")))
            if cell is None:
                continue
            value, printed_sigma = cell
            sigma = printed_sigma if basis == "printed" else (
                None if relative is None else relative * value
            )
            if sigma is not None:
                cells[x] = (value, sigma)
        if len(cells) != len(compositions):
            problem = problem or (
                f"plateau series {obs_id!r} lacks a usable printed cell at every plateau composition"
            )
            continue
        try:
            z_by_series[obs_id] = _series_z(cells)
        except ValueError:
            problem = problem or f"plateau series {obs_id!r} has zero sigma"
    failing = tuple(sorted(name for name, z in z_by_series.items() if z > PLATEAU_Z_LIMIT))
    named = raw.get("named_failure")
    named_id = str(named.get("observation_id") or "") if isinstance(named, Mapping) else ""
    if len(failing) > 1 or (failing and failing != (named_id,)):
        problem = problem or (
            f"plateau fails in {list(failing)}; at most one named series may fail"
        )
    if not z_by_series:
        problem = problem or "plateau has no testable series"
    return Plateau(
        record_id=record_id,
        compositions=compositions,
        observation_ids=frozenset(seen_ids),
        z_by_series=z_by_series,
        untestable=tuple(untestable),
        failing=failing,
        problem=problem,
    )


def phase_field_records(doc: Mapping[str, object]) -> PhaseFieldRecords | None:
    """Read the extract's printed liquidus and plateau records, if any."""

    raw = doc.get("phase_field_records")
    if not isinstance(raw, Mapping):
        return None
    problems: list[str] = []
    liquidus: dict[str, Liquidus] = {}
    for item in raw.get("liquidus") or ():
        if not isinstance(item, Mapping):
            continue
        record_id = str(item.get("id") or "")
        position, sigma = _dec(item.get("position")), _dec(item.get("sigma"))
        side = str(item.get("outside_side") or "")
        locator = item.get("locator")
        if not record_id or position is None or sigma is None or side not in _OUTSIDE_SIDES or not item.get("component") or not isinstance(locator, Mapping):
            problems.append(f"liquidus record {record_id!r} needs component, position, sigma, outside_side and a locator")
            continue
        superseded = tuple(
            d for d in (_dec(s) for s in item.get("superseded_positions") or ()) if d is not None
        )
        liquidus[record_id] = Liquidus(
            record_id, str(item["component"]), position, sigma, side, superseded, dict(locator)
        )
    observations = _observations_by_id(doc)
    plateaus = {
        p.record_id: p
        for p in (
            _plateau(item, observations)
            for item in raw.get("plateaus") or ()
            if isinstance(item, Mapping)
        )
    }
    return PhaseFieldRecords(liquidus, plateaus, tuple(problems))


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def classify_point(
    records: PhaseFieldRecords,
    *,
    observation_id: str,
    x_by_component: Mapping[str, Decimal],
    declared: object,
) -> PointPhaseField:
    """Apply the two-test rule to one printed point."""

    applicable = [rec for rec in records.liquidus.values() if rec.component in x_by_component]
    if not applicable:
        if declared is not None:
            return PointPhaseField(None, None, "phase_field_class without an applicable liquidus record")
        return PointPhaseField(None, None)
    if len(applicable) > 1:
        return PointPhaseField(None, None, "more than one liquidus record applies")
    liquidus = applicable[0]
    x = x_by_component[liquidus.component]
    contested_payload: dict[str, object] = {
        "component": liquidus.component,
        "x": _fmt(x),
        "stated_liquidus": _fmt(liquidus.position),
        "stated_liquidus_sigma": _fmt(liquidus.sigma),
        "outside_side": liquidus.outside_side,
        "superseded_liquidus": [_fmt(p) for p in liquidus.superseded_positions],
        "liquidus_record": liquidus.record_id,
        "locator": dict(liquidus.locator),
    }
    problem: str | None = None
    if declared is not None:
        outcome = _check_declared(records, liquidus, x, observation_id, declared)
        if outcome.kind is not None:
            return outcome
        problem = outcome.problem
    if liquidus.outside_any_position(x):
        return PointPhaseField(LIQUIDUS_POSITION_CONTESTED, contested_payload, problem)
    return PointPhaseField(None, None, problem)


def _check_declared(
    records: PhaseFieldRecords,
    liquidus: Liquidus,
    x: Decimal,
    observation_id: str,
    declared: object,
) -> PointPhaseField:
    if not isinstance(declared, Mapping) or declared.get("class") != OUTSIDE_SINGLE_LIQUID_FIELD:
        return PointPhaseField(None, None, f"phase_field_class must be {OUTSIDE_SINGLE_LIQUID_FIELD!r}")
    basis = declared.get("basis")
    if not isinstance(basis, Mapping) or basis.get("criterion") != TWO_TEST_CRITERION:
        return PointPhaseField(None, None, f"phase_field_class basis.criterion must be {TWO_TEST_CRITERION!r}")
    if not isinstance(basis.get("locator"), Mapping):
        return PointPhaseField(None, None, "phase_field_class basis needs a locator")
    if basis.get("liquidus") != liquidus.record_id:
        return PointPhaseField(None, None, "phase_field_class basis names another liquidus record")
    plateau = records.plateaus.get(str(basis.get("plateau") or ""))
    if plateau is None:
        return PointPhaseField(None, None, "phase_field_class basis names no plateau record")
    if plateau.problem is not None:
        return PointPhaseField(None, None, f"plateau {plateau.record_id!r}: {plateau.problem}")
    if not liquidus.beyond_sigma_band(x):
        return PointPhaseField(None, None, "test (i) fails: point is not beyond the stated liquidus sigma band")
    if x not in plateau.compositions or observation_id not in plateau.observation_ids:
        return PointPhaseField(None, None, "test (ii) fails: point is not in the plateau record")
    max_z = plateau.max_passing_z
    computed = {
        "x": _fmt(x),
        "liquidus": _fmt(liquidus.position),
        "liquidus_sigma": _fmt(liquidus.sigma),
        "plateau_max_z": None if max_z is None else str(max_z),
        "plateau_series_tested": len(plateau.z_by_series),
        "plateau_series_failing": list(plateau.failing),
        "plateau_series_untestable": len(plateau.untestable),
    }
    carried = basis.get("test_values")
    carried_plain = (
        {key: (list(v) if isinstance(v, Sequence) and not isinstance(v, str) else (v if isinstance(v, int) else str(v)))
         for key, v in carried.items()}
        if isinstance(carried, Mapping)
        else None
    )
    if carried_plain != computed:
        return PointPhaseField(
            None, None,
            f"phase_field_class basis.test_values {carried_plain!r} do not match the rule's {computed!r}",
        )
    payload = {
        "class": OUTSIDE_SINGLE_LIQUID_FIELD,
        "criterion": TWO_TEST_CRITERION,
        "liquidus_record": liquidus.record_id,
        "plateau_record": plateau.record_id,
        "locator": dict(basis["locator"]),
        "test_values": computed,
    }
    return PointPhaseField(OUTSIDE_SINGLE_LIQUID_FIELD, payload)


def is_outside_single_liquid_field_reason(reason: object) -> bool:
    return isinstance(reason, str) and reason.startswith(f"{OUTSIDE_SINGLE_LIQUID_FIELD}:")
