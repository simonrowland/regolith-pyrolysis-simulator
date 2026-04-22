"""
Decision Tree
==============

Implements the decision logic for the Oxygen Shuttle process:

Root Branch:
    Pyrolysis Track (default) vs Standard MRE Baseline

Path Selection (after C0b):
    Path A — Continuous adaptive pN₂ ramp (SiO₂ extraction)
    Path B — pO₂-managed Fe pyrolysis (CMAS glass preservation)

Branch Selection (after C3):
    Branch Two (preferred) — C4 Mg pyrolysis + C6 Mg thermite
    Branch One (fallback) — Skip C4, full MRE to 2.5 V

Ti Retention (during C5):
    Stop before Ti voltage → C6 produces Al-Ti alloy

C6 Proceed (after C5, Branch Two):
    Requires Mg inventory; operator confirms
"""

from __future__ import annotations

from typing import Dict, Optional

from simulator.core import (
    BatchRecord, CampaignPhase, DecisionPoint, DecisionType, MeltState,
)


class DecisionTree:
    """
    Evaluates decision points and provides recommendations.

    The recommendation is based on feedstock properties and
    current state, but the operator always has the final choice.
    """

    def __init__(self, setpoints: dict):
        self.setpoints = setpoints
        self.guidance = setpoints.get('decision_tree', {}).get('guidance', [])

    def evaluate_root_branch(self, feedstock: dict) -> DecisionPoint:
        """Root-level decision: Pyrolysis Track vs Standard MRE."""
        return DecisionPoint(
            decision_type=DecisionType.ROOT_BRANCH,
            options=['pyrolysis', 'mre_baseline'],
            recommendation='pyrolysis',
            context=(
                'Pyrolysis Track: C0→C2A/B→C3→C4→C5→C6. '
                'Solar-thermal dominant, ~1200-2000 kWh/t electrical. '
                'Standard MRE Baseline: C0→MRE ramp to 2.5V. '
                'Pure electrolysis, ~2650-4050+ kWh/t electrical.'
            ),
        )

    def evaluate_path(self, melt: MeltState,
                       record: BatchRecord,
                       feedstock: dict) -> DecisionPoint:
        """Path A/B decision after C0b completion."""
        # Default recommendation: Path A unless user needs CMAS glass
        rec = 'A'
        context = (
            'Path A (recommended): Continuous pN₂ ramp extracts '
            'Na/K/Fe/SiO₂ together. 8-20% faster overall cycle, '
            'halved C3 scope, produces fused silica glass. '
            'Path B: pO₂-managed Fe-only extraction. Preserves '
            'CMAS glass in melt for tapping as structural glass '
            '(Material 1) or terminal ceramics recipes.'
        )

        return DecisionPoint(
            decision_type=DecisionType.PATH_AB,
            options=['A', 'B'],
            recommendation=rec,
            context=context,
        )

    def evaluate_branch(self, melt: MeltState,
                         record: BatchRecord) -> DecisionPoint:
        """Branch One/Two decision after C3 completion."""
        rec = 'two'
        context = (
            'Branch Two (recommended): C4 Mg selective pyrolysis + '
            'C6 Mg thermite. MRE capped at ≤1.6 V. '
            '~1200-2000 kWh/t electrical, electrode life 5-10×. '
            'Branch One: Skip C4, full MRE to 2.5 V for '
            'Si+Al+Mg+Ca. ~2650-4050 kWh/t, electrode life 2-3×.'
        )

        return DecisionPoint(
            decision_type=DecisionType.BRANCH_ONE_TWO,
            options=['two', 'one'],
            recommendation=rec,
            context=context,
        )

    def evaluate_ti_retention(self, melt: MeltState) -> DecisionPoint:
        """Ti retention option during C5."""
        return DecisionPoint(
            decision_type=DecisionType.TI_RETENTION,
            options=['retain', 'extract'],
            recommendation='retain',
            context=(
                'Retain TiO₂ for C6: Stop MRE before 1.5-1.6 V. '
                'C6 thermite produces Al-Ti alloy instead of pure Al. '
                'Extract Ti: Continue MRE through 1.5-1.6 V window. '
                'Separate Ti tap, but higher electrode wear.'
            ),
        )

    def evaluate_c6_proceed(self, record: BatchRecord,
                             mg_inventory_kg: float = 0.0) -> DecisionPoint:
        """C6 proceed decision — requires Mg inventory check."""
        mg_needed = 55.0  # ~50-60 kg stoichiometric for 1 tonne
        has_enough = mg_inventory_kg >= mg_needed

        if has_enough:
            rec = 'yes'
            context = (
                f'Proceed with C6 Mg thermite? '
                f'Mg inventory: {mg_inventory_kg:.0f} kg '
                f'(need ~{mg_needed:.0f} kg). '
                f'Will produce Al (+ Ti alloy if retained). '
                f'Self-terminating when liquidus exceeds 1700°C.'
            )
        else:
            rec = 'no'
            context = (
                f'Insufficient Mg for C6 thermite. '
                f'Mg inventory: {mg_inventory_kg:.0f} kg '
                f'(need ~{mg_needed:.0f} kg). '
                f'Skip C6 or wait for more Mg from other lines.'
            )

        return DecisionPoint(
            decision_type=DecisionType.C6_PROCEED,
            options=['yes', 'no'],
            recommendation=rec,
            context=context,
        )
