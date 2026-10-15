"""Pareto-front adapter backed by pymoo with a deterministic fallback.

RoomLight keeps domain filtering and plotting in ``experiments.py``.  This
module owns only non-dominated sorting, so 2-D and N-D views cannot drift into
different definitions of Pareto dominance.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np


def non_dominated_indices(
    values: np.ndarray,
    *,
    maximize: Sequence[bool] | None = None,
    tolerances: Iterable[float] | None = None,
) -> np.ndarray:
    """Return indices of the first Pareto front.

    ``pymoo`` minimizes all objectives.  RoomLight's maximize flags are
    converted once here.  Values are quantized to the declared engineering
    resolution before sorting, preventing floating-point noise from creating
    a false advantage while preserving duplicate non-dominated schemes.
    """
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError("Pareto输入必须是二维数组。")
    if not len(array):
        return np.empty(0, dtype=int)
    if not np.isfinite(array).all():
        raise ValueError("Pareto输入包含NaN或无穷值。")

    flags = list(maximize or [True] * array.shape[1])
    if len(flags) != array.shape[1]:
        raise ValueError("Pareto目标方向数量与指标列数不一致。")
    resolution = np.asarray(
        list(tolerances or [0.0] * array.shape[1]), dtype=float
    )
    if resolution.shape != (array.shape[1],):
        raise ValueError("Pareto容差数量与指标列数不一致。")

    stable = array.copy()
    positive = resolution > 0.0
    if np.any(positive):
        stable[:, positive] = (
            np.rint(stable[:, positive] / resolution[positive])
            * resolution[positive]
        )
    objectives = stable.copy()
    for column, is_maximum in enumerate(flags):
        if is_maximum:
            objectives[:, column] *= -1.0

    try:
        from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting

        front = NonDominatedSorting().do(
            objectives, only_non_dominated_front=True
        )
        return np.asarray(front, dtype=int)
    except ImportError:
        # The fallback keeps saved projects usable in a minimal environment;
        # release builds install pymoo and tests assert both paths agree.
        dominated = np.zeros(len(objectives), dtype=bool)
        for index, candidate in enumerate(objectives):
            for other_index, other in enumerate(objectives):
                if index == other_index:
                    continue
                if np.all(other <= candidate) and np.any(other < candidate):
                    dominated[index] = True
                    break
        return np.flatnonzero(~dominated)


def backend_name() -> str:
    try:
        import pymoo  # noqa: F401
        return "pymoo.NonDominatedSorting"
    except ImportError:
        return "RoomLight deterministic fallback"
