"""MAGEMin kernel-shadow provider scaffold.

See ``engines/magemin/README.md`` and
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §4 (MAGEMin)
for the contract. ``\\goal MAGEMIN-SHADOW-PARITY`` in
``docs-private/codex-goal-queue-2026-05-14.md`` is the goal that turns
this scaffold into a kernel-registered shadow provider.

Public exports:

- :class:`MAGEMinShadowProvider` — kernel-shadow provider stub.
- :class:`MAGEMinDomainGate` — composition-range gate (14-oxide MELTS basis).
- :class:`MAGEMinParityComparator` — shadow-vs-authoritative comparator.
- :class:`ParityReport` — dataclass returned by the comparator.
"""

from engines.magemin.domain import MAGEMinDomainGate
from engines.magemin.parity import MAGEMinParityComparator, ParityReport
from engines.magemin.provider import MAGEMinShadowProvider

__all__ = [
    'MAGEMinDomainGate',
    'MAGEMinParityComparator',
    'MAGEMinShadowProvider',
    'ParityReport',
]
