"""Optimizer strategy interfaces and baselines."""

from simulator.optimize.strategy.protocol import Candidate, Strategy
from simulator.optimize.strategy.random_strategy import RandomStrategy

__all__ = [
    "Candidate",
    "RandomStrategy",
    "Strategy",
]
