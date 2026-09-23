#!/usr/bin/env python3
"""Run the JANAF pure-phase score consumer; write residual JSON + markdown.

Scores MAGEMin and ThermoEngine pure-phase standard-state access against
the NIST-JANAF 4th compilation (owner ruling d-039: condensed-phase
standard-state properties only).  Two row kinds, per engine x T:

* reaction rows  -- Delta_rG(T): engine apparent-G reaction sum vs JANAF
  Delta_fG(T) reaction sum (element reference terms cancel exactly for a
  balanced reaction; see the module docstring derivation);
* phase rows     -- convention-free S(T), Cp(T), H(T)-H(298.15).

Polymorph-mismatched rows are refused typed (a mismatched polymorph is a
fake residual).  Engine-unreachable properties stay typed absences, never
zeros.  Output lands under docs-private/research/2026-09-22-janaf-p4a2/
(gitignored; not committed).

Usage:
    python scripts/janaf_pure_phase_score.py [--out DIR]
                                             [--engines both|magemin|thermoengine]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.melt_backend.pure_phase import (  # noqa: E402
    PurePhaseAccessError,
    PurePhaseProperties,
    PurePhaseUnknownSymbolError,
)
from simulator.melt_backend.pure_phase_janaf_score import (  # noqa: E402
    DEFAULT_PHASE_REQUESTS,
    DEFAULT_TEMPERATURES_K,
    ENGINE_PHASE_ACCESS,
    ENGINES,
    NO_JANAF_TABLE_FOR_POLYMORPH,
    POLYMORPH_MISMATCH,
    PRESSURE_BAR,
    REACTIONS,
    PhaseRow,
    PolymorphMismatchError,
    ReactionRow,
    RefusalRow,
    build_phase_row,
    build_reaction_row,
    janaf_values_at,
    load_janaf_table,
    preflight_phase_request,
    preflight_reaction,
)

DEFAULT_OUT = ROOT / 'docs-private' / 'research' / '2026-09-22-janaf-p4a2'


class EngineQuerier:
    """Lazy per-engine backends + per-(engine, phase, T) result cache."""

    def __init__(self) -> None:
        self._backends: Dict[str, object] = {}
        self._cache: Dict[Tuple[str, str, float], PurePhaseProperties] = {}

    def __call__(
        self, engine: str, phase_id: str, temperature_K: float
    ) -> PurePhaseProperties:
        key = (engine, phase_id, float(temperature_K))
        if key not in self._cache:
            backend = self._backend(engine)
            self._cache[key] = backend.pure_phase_properties(
                phase_id, temperature_K=temperature_K, pressure_bar=PRESSURE_BAR
            )
        return self._cache[key]

    def _backend(self, engine: str):
        if engine in self._backends:
            return self._backends[engine]
        if engine == 'magemin':
            from simulator.melt_backend.magemin import MAGEMinBackend

            # Same resolution as tests/test_pure_phase_access.py: the env
            # var overrides; otherwise initialize() probes engines.local.toml
            # and the documented binary locations itself.
            config: Dict[str, object] = {'warm_worker': False}
            binary = os.environ.get('REGOLITH_MAGEMIN_BINARY')
            if binary:
                config['binary_path'] = binary
            backend = MAGEMinBackend()
            if not backend.initialize(config):
                raise RuntimeError(
                    'MAGEMin backend unavailable (set REGOLITH_MAGEMIN_BINARY '
                    'to the compiled binary)'
                )
        elif engine == 'thermoengine':
            from simulator.melt_backend.thermoengine import ThermoEngineBackend

            backend = ThermoEngineBackend()
            backend.initialize({})
            if not backend.is_available():
                raise RuntimeError(
                    f'ThermoEngine backend unavailable: '
                    f'{backend.unavailable_reason()}'
                )
        else:  # pragma: no cover - plan only carries the two engines
            raise ValueError(f'unknown engine {engine!r}')
        self._backends[engine] = backend
        return backend

    def close(self) -> None:
        for backend in self._backends.values():
            backend.close()
        self._backends.clear()


class JanafQuerier:
    """Per-table document cache over the compilation directory."""

    def __init__(self) -> None:
        self._documents: Dict[str, Mapping] = {}

    def __call__(self, table_id: str, temperature_K: float):
        if table_id not in self._documents:
            self._documents[table_id] = load_janaf_table(table_id)
        return janaf_values_at(self._documents[table_id], temperature_K)


def _consume_row(rows, refusals, build, **fields) -> None:
    """Score one row. A bad row is a typed refusal; it does not abort the run."""

    try:
        rows.append(build())
    except PolymorphMismatchError as exc:
        refusals.append(
            RefusalRow(reason=POLYMORPH_MISMATCH, detail=str(exc), **fields)
        )
    except (
        PurePhaseUnknownSymbolError,
        PurePhaseAccessError,
        ValueError,
    ) as exc:
        refusals.append(
            RefusalRow(reason=ENGINE_PHASE_ACCESS, detail=str(exc), **fields)
        )


def build_all_rows(
    querier: EngineQuerier, janaf: JanafQuerier, engines: Tuple[str, ...]
):
    rows = []
    refusals = []

    def engine_query(engine):
        return lambda phase_id, T: querier(engine, phase_id, T)

    for reaction in REACTIONS:
        for engine in engines:
            for T in DEFAULT_TEMPERATURES_K:
                refusal = preflight_reaction(
                    reaction, engine, temperature_K=T
                )
                if refusal is not None:
                    refusals.append(refusal)
                    continue
                _consume_row(
                    rows,
                    refusals,
                    lambda reaction=reaction, engine=engine, T=T: (
                        build_reaction_row(
                            reaction,
                            engine,
                            T,
                            engine_query=engine_query(engine),
                            janaf_query=janaf,
                        )
                    ),
                    engine=engine,
                    reaction_id=reaction.reaction_id,
                    temperature_K=T,
                )
    for request in DEFAULT_PHASE_REQUESTS:
        if request.engine not in engines:
            continue
        for T in request.temperatures_K:
            refusal = preflight_phase_request(request, temperature_K=T)
            if refusal is not None:
                refusals.append(refusal)
                continue
            _consume_row(
                rows,
                refusals,
                lambda request=request, T=T: build_phase_row(
                    request,
                    T,
                    engine_query=engine_query(request.engine),
                    janaf_query=janaf,
                ),
                engine=request.engine,
                phase_id=request.phase_id,
                janaf_table_id=request.janaf_table_id,
                temperature_K=T,
            )
    return rows, refusals


# ------------------------------------------------------------- rendering --

def _fmt(value: Optional[float], digits: int = 3) -> str:
    return '-' if value is None else f'{value:.{digits}f}'


def render_markdown(rows, refusals) -> str:
    lines = [
        '# JANAF pure-phase score residuals (P4a-2)',
        '',
        'Engine apparent-G reaction sums vs JANAF Delta_fG(T) reaction sums',
        '(balanced reactions: element reference terms cancel exactly), and',
        'convention-free per-phase S / Cp / H(T)-H(298.15) vs the printed',
        'JANAF tables. P = 1 bar. Polymorph stated per row; mismatched',
        'polymorphs are refused typed, never scored.',
        '',
        '## Reaction residuals (Delta_rG, kJ/mol; residual = engine - JANAF)',
        '',
        'TE rows also report the variant with the MELTS QUARTZ_ADJUSTMENT',
        '(-1.291 kJ/mol on quartz G via enthalpy; default on) removed.',
        '',
        (
            '| engine | reaction | T (K) | SiO2 basis | d_rG engine '
            '| d_rG JANAF | residual | d_rG no qtz-adj '
            '| residual no qtz-adj | notes |'
        ),
        '|---|---|---|---|---|---|---|---|---|---|',
    ]
    reaction_rows = [r for r in rows if isinstance(r, ReactionRow)]
    for row in reaction_rows:
        lines.append(
            f"| {row.engine} | {row.reaction_id} | {row.temperature_K:.2f} "
            f"| {row.sio2_polymorph or '-'} | {_fmt(row.drG_engine_kJ_mol)} "
            f"| {_fmt(row.drG_janaf_kJ_mol)} | {_fmt(row.residual_kJ_mol)} "
            f"| {_fmt(row.drG_engine_no_quartz_adjustment_kJ_mol)} "
            f"| {_fmt(row.residual_no_quartz_adjustment_kJ_mol)} "
            f"| {'; '.join(row.notes)} |"
        )
    lines += [
        '',
        '## Phase-property residuals (engine - JANAF; % of JANAF)',
        '',
        '| engine | phase (polymorph) | JANAF table (polymorph) | T (K) '
        '| S eng | S ref | dS % | Cp eng | Cp ref | dCp % '
        '| H-H298 eng | H-H298 ref | dH kJ | absences |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]
    phase_rows = [r for r in rows if isinstance(r, PhaseRow)]
    for row in phase_rows:
        props = {p.property: p for p in row.properties}
        s, cp, h = props['S'], props['Cp'], props['H_increment']
        absences = '; '.join(
            f'{p.property}: {p.absence_reason}'
            for p in row.properties
            if p.absence_reason
        )
        notes = list(row.notes)
        if absences:
            notes.append(absences)
        lines.append(
            f'| {row.engine} | {row.phase_id} ({row.polymorph}) '
            f'| {row.janaf_table_id} ({row.janaf_polymorph}) '
            f'| {row.temperature_K:.2f} '
            f'| {_fmt(s.engine)} | {_fmt(s.janaf)} | {_fmt(s.residual_pct, 2)} '
            f'| {_fmt(cp.engine)} | {_fmt(cp.janaf)} | {_fmt(cp.residual_pct, 2)} '
            f'| {_fmt(h.engine)} | {_fmt(h.janaf)} | {_fmt(h.residual)} '
            f'| {"; ".join(notes)} |'
        )
    lines += [
        '',
        '## Typed refusals (polymorph contract; never scored)',
        '',
        '| engine | scope | T (K) | reason | detail |',
        '|---|---|---|---|---|',
    ]
    for refusal in refusals:
        scope = refusal.reaction_id or refusal.phase_id or ''
        lines.append(
            f'| {refusal.engine} | {scope} '
            f'| {refusal.temperature_K if refusal.temperature_K is not None else "-"} '
            f'| {refusal.reason} | {refusal.detail} |'
        )
    lines.append('')
    return '\n'.join(lines)


def summarize(rows, refusals) -> dict:
    max_abs = {engine: None for engine in ENGINES}
    max_abs_no_adj = None
    for row in rows:
        if not isinstance(row, ReactionRow):
            continue
        current = max_abs.get(row.engine)
        value = abs(row.residual_kJ_mol)
        if current is None or value > current:
            max_abs[row.engine] = value
        if row.residual_no_quartz_adjustment_kJ_mol is not None:
            value_wo = abs(row.residual_no_quartz_adjustment_kJ_mol)
            if max_abs_no_adj is None or value_wo > max_abs_no_adj:
                max_abs_no_adj = value_wo
    return {
        'rows': len(rows) + len(refusals),
        'scored_rows': len(rows),
        'refusals': len(refusals),
        'polymorph_refusals': sum(
            1
            for r in refusals
            if r.reason in (POLYMORPH_MISMATCH, NO_JANAF_TABLE_FOR_POLYMORPH)
        ),
        'max_abs_dRG_kJ': max_abs,
        'max_abs_dRG_no_quartz_adjustment_kJ': {'thermoengine': max_abs_no_adj},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        '--engines', default='both', choices=('both', 'magemin', 'thermoengine')
    )
    args = parser.parse_args()
    engines = ENGINES if args.engines == 'both' else (args.engines,)

    querier = EngineQuerier()
    try:
        rows, refusals = build_all_rows(querier, JanafQuerier(), engines)
    finally:
        querier.close()

    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / 'residuals.json'
    md_path = args.out / 'residuals.md'
    payload = {
        'task': 't-957 P4a-2',
        'pressure_bar': PRESSURE_BAR,
        'temperatures_K': list(DEFAULT_TEMPERATURES_K),
        'engines': list(engines),
        'residual_sign': 'engine - JANAF',
        'rows': [dataclasses.asdict(row) for row in rows],
        'refusals': [dataclasses.asdict(row) for row in refusals],
        'summary': summarize(rows, refusals),
    }
    json_path.write_text(json.dumps(payload, indent=2) + '\n')
    md_path.write_text(render_markdown(rows, refusals))
    print(json.dumps(payload['summary']))
    print(f'wrote {json_path}')
    print(f'wrote {md_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
