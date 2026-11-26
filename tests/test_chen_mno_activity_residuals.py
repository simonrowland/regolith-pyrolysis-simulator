"""Production-path Chen 2022 a(MnO) residuals.

``compile_residual`` converts the solid reference, then calls the engine.
A stub prediction keeps the unconverted solid identity, so the census
``identity_mismatch`` is that stub, not an engine residual. Calling
``predict_with_engine`` on the raw row refuses ``reference_state_mismatch``
before the conversion. This file uses neither shortcut.

The battery dex is log10(prediction / converted liquid activity). The
printed dex is log10(prediction / the printed solid activity). No magnitude
is pinned.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from simulator.battery.score import compile_residual
from tests.test_mno_solid_liquid_reference import _MELT_ENGINES, _chen_rows


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _log10_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if numerator <= 0 or denominator <= 0:
        return None
    return numerator.ln() / Decimal(10).ln()


def _production_lines(engine) -> list[str]:
    context, rows = _chen_rows()
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    battery: list[Decimal] = []
    printed: list[Decimal] = []
    for row in rows:
        try:
            residual, candidate = compile_residual(row, engine, context=context)
        except Exception as exc:
            key = f"EXCEPTION {type(exc).__name__}"
            counts[key] += 1
            examples.setdefault(key, str(exc)[:300])
            continue
        refusal = residual.refusal
        if refusal is None and residual.numeric is not None:
            counts["scored"] += 1
            battery.append(residual.numeric.value)
            predicted = None if candidate is None else candidate.value.point
            measured = row.value.point
            if predicted is not None and measured is not None:
                ratio = _log10_ratio(predicted, measured)
                if ratio is not None:
                    printed.append(ratio)
            continue
        if refusal is None:
            counts["no-numeric"] += 1
            continue
        detail = dict(refusal.detail)
        reason = str(detail.get("reason") or refusal.reason)
        counts[reason] += 1
        examples.setdefault(reason, str(detail)[:400])
    rendered = ", ".join(f"{reason}={n}" for reason, n in sorted(counts.items()))
    lines = [f"PROD {engine.value}: {rendered}"]
    for reason, example in sorted(examples.items()):
        lines.append(f"PROD {engine.value} example {reason}: {example}")
    if battery:
        lines.append(
            f"PROD {engine.value} battery_dex n={len(battery)} "
            f"median={_median(battery)} min={min(battery)} max={max(battery)}"
        )
        lines.append(
            "PROD " + engine.value + " battery_dexes=" + ",".join(str(v) for v in battery)
        )
    if printed:
        lines.append(
            f"PROD {engine.value} printed_dex n={len(printed)} "
            f"median={_median(printed)} min={min(printed)} max={max(printed)}"
        )
        lines.append(
            "PROD " + engine.value + " printed_dexes=" + ",".join(str(v) for v in printed)
        )
    assert sum(counts.values()) == len(rows)
    return lines


def test_chen_mno_production_internal_analytical(capfd) -> None:
    engine = next(item for item in _MELT_ENGINES if item.value == "internal-analytical")
    lines = _production_lines(engine)
    with capfd.disabled():
        print("\n".join(lines), flush=True)


def test_chen_mno_production_alphamelts(capfd) -> None:
    engine = next(item for item in _MELT_ENGINES if item.value == "alphamelts")
    lines = _production_lines(engine)
    with capfd.disabled():
        print("\n".join(lines), flush=True)


def test_chen_mno_production_thermoengine(capfd) -> None:
    engine = next(item for item in _MELT_ENGINES if item.value == "thermoengine")
    lines = _production_lines(engine)
    with capfd.disabled():
        print("\n".join(lines), flush=True)


def test_chen_mno_production_openimcc(capfd) -> None:
    engine = next(item for item in _MELT_ENGINES if item.value == "openimcc")
    lines = _production_lines(engine)
    with capfd.disabled():
        print("\n".join(lines), flush=True)


def test_chen_mno_production_magemin(capfd) -> None:
    engine = next(item for item in _MELT_ENGINES if item.value == "magemin")
    lines = _production_lines(engine)
    with capfd.disabled():
        print("\n".join(lines), flush=True)
