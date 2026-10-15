"""Shared low-order spatial interreflection approximation."""
from __future__ import annotations

import numpy as np


def interreflection_field(
    direct_basis: np.ndarray,
    valid_mask: np.ndarray,
    rho: float,
) -> np.ndarray:
    """Return a mean-energy-conserving multi-bounce diffuse field.

    The first two reflected orders retain local spatial variation.  Third and
    later orders are treated as a well-mixed field.  This is the fast-screening
    approximation; thesis-grade final values are produced by Radiance.
    """
    clipped_rho = min(max(float(rho), 0.0), 0.995)
    mean_basis = float(np.mean(direct_basis[valid_mask]))
    local_gain = clipped_rho + clipped_rho ** 2
    mixed_gain = clipped_rho ** 3 / max(1.0 - clipped_rho, 1e-12)
    field = np.full_like(direct_basis, np.nan, dtype=float)
    field[valid_mask] = (
        local_gain * direct_basis[valid_mask]
        + mixed_gain * mean_basis
    )
    return field
