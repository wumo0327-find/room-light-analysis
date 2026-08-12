"""
core/complex_daylight.py — Arbitrary-polygon daylight engine  v3.3.1

This is the v3 counterpart of ``core.daylight``.  It preserves the CIE/BRS/
Littlefair calculation method while replacing the rectangle and four-cardinal-
wall assumptions with:

* polygon-masked work-plane grids;
* windows attached to arbitrary directed wall segments;
* direct-ray occlusion by concave room boundaries;
* local-coordinate overhang geometry for every wall direction;
* post-window attenuation by adjacent walls, balcony parapets and railings.

All model geometry is stored in millimetres; numerical integration uses metres.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

from core.complex_models import (
    ExteriorBarrier,
    Point2D,
    SpaceModel,
    WallOpening,
    WallSegment,
)
from core.space_geometry import point_in_space, space_floor_area_mm2, validate_space


WORK_PLANE_MM = 750.0
GRID_MM = 250.0
WIN_DIV = 40
WALL_MARGIN_MM = 500.0


class ComplexDaylightResult:
    """Daylight result with an explicit polygon validity mask."""

    def __init__(self):
        self.grid_x = None
        self.grid_y = None
        self.valid_mask = None
        self.DF = None
        self.E_lux = None
        self.Ds = None
        self.Dext = None
        self.Dint = None
        self.E_avg = None
        self.E_min = None
        self.E_max = None
        self.U0 = None
        self.DF_avg = None
        self.DF_min = None
        self.DF_max = None
        self.E_out = None
        self.rho_bar = None
        self.grid_mm = None
        self.compliant_300 = None
        self.compliant_u0 = None
        self.method = None
        self.quick = None
        self.ndiv = None
        self.Ra = None
        self.Ra_threshold = None


def _distance_to_segment(point: Point2D, wall: WallSegment) -> float:
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return point.distance_to(wall.start)
    t = (
        (point.x - wall.start.x) * dx
        + (point.y - wall.start.y) * dy
    ) / length2
    t = max(0.0, min(1.0, t))
    projection = Point2D(wall.start.x + t * dx, wall.start.y + t * dy)
    return point.distance_to(projection)


def build_workplane_grid(
    space: SpaceModel,
    grid_mm: float = GRID_MM,
    wall_margin_mm: float = WALL_MARGIN_MM,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Build a regular bounding-box grid and mask it to the actual floor polygon.

    If the requested wall margin removes every point in a very small or narrow
    space, the function falls back to points inside the polygon without margin,
    matching the legacy engine's small-room behaviour.
    """
    outer = space.outer_loop()
    if outer is None or not outer.points():
        raise ValueError("空间没有有效外边界。")
    if grid_mm <= 0.0:
        raise ValueError("计算网格步长必须大于0。")

    points = outer.points()
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)

    x0 = min_x + wall_margin_mm + grid_mm / 2.0
    x1 = max_x - wall_margin_mm
    y0 = min_y + wall_margin_mm + grid_mm / 2.0
    y1 = max_y - wall_margin_mm
    if x1 <= x0 or y1 <= y0:
        x0 = min_x + grid_mm / 2.0
        x1 = max_x - grid_mm / 2.0
        y0 = min_y + grid_mm / 2.0
        y1 = max_y - grid_mm / 2.0

    nx = max(2, round((x1 - x0) / grid_mm) + 1)
    ny = max(2, round((y1 - y0) / grid_mm) + 1)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    walls = space.wall_segments()

    def make_mask(require_margin: bool) -> np.ndarray:
        mask = np.zeros((ny, nx), dtype=bool)
        for iy, y in enumerate(ys):
            for ix, x in enumerate(xs):
                point = Point2D(float(x), float(y))
                if not point_in_space(point, space, include_boundary=False):
                    continue
                if require_margin and walls:
                    if min(_distance_to_segment(point, wall) for wall in walls) < (
                        wall_margin_mm - 1e-6
                    ):
                        continue
                mask[iy, ix] = True
        return mask

    mask = make_mask(require_margin=True)
    if not np.any(mask):
        mask = make_mask(require_margin=False)
    if not np.any(mask):
        raise ValueError("空间内部没有可布置的采光计算点。")
    return xs, ys, mask


def _space_rho_bar(space: SpaceModel) -> float:
    material = space.material
    floor_area = space_floor_area_mm2(space) / 1_000_000.0
    ceiling_area = floor_area
    wall_gross = sum(
        wall.length_mm * space.height_mm / 1_000_000.0
        for wall in space.wall_segments()
    )
    opening_area = sum(
        opening.area_m2
        for wall in space.wall_segments()
        for opening in wall.openings
    )
    wall_net = max(0.0, wall_gross - opening_area)
    total = floor_area + ceiling_area + wall_net
    if total <= 1e-12:
        return 0.5
    return (
        material.rho_floor * floor_area
        + material.rho_ceiling * ceiling_area
        + material.rho_wall * wall_net
    ) / total


def _wall_axes_m(wall: WallSegment):
    dx, dy = wall.direction
    inward_x, inward_y = wall.inward_normal
    origin = np.array(
        [wall.start.x / 1000.0, wall.start.y / 1000.0, 0.0],
        dtype=np.float64,
    )
    u_axis = np.array([dx, dy, 0.0], dtype=np.float64)
    v_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    normal = np.array([inward_x, inward_y, 0.0], dtype=np.float64)
    return normal, u_axis, v_axis, origin


def _blocked_by_other_boundaries(
    point: np.ndarray,
    qx: np.ndarray,
    qy: np.ndarray,
    target_wall: WallSegment,
    walls: Sequence[WallSegment],
) -> np.ndarray:
    """
    Detect a boundary crossing before a ray reaches its target window plane.

    This is what makes a concave L-shaped room different from its rectangular
    bounding box: a return wall blocks direct sight of a window around a corner.
    """
    blocked = np.zeros_like(qx, dtype=bool)
    dx = qx - point[0]
    dy = qy - point[1]
    px, py = point[0], point[1]
    for wall in walls:
        if wall.id == target_wall.id:
            continue
        ax = wall.start.x / 1000.0
        ay = wall.start.y / 1000.0
        sx = (wall.end.x - wall.start.x) / 1000.0
        sy = (wall.end.y - wall.start.y) / 1000.0
        denominator = dx * sy - dy * sx
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((ax - px) * sy - (ay - py) * sx) / denominator
            u = ((ax - px) * dy - (ay - py) * dx) / denominator
        intersects = (
            np.isfinite(t)
            & np.isfinite(u)
            & (np.abs(denominator) > 1e-12)
            & (t > 1e-9)
            & (t < 1.0 - 1e-9)
            & (u >= -1e-9)
            & (u <= 1.0 + 1e-9)
        )
        blocked |= intersects
    return blocked


def _exterior_barrier_transmission(
    point: np.ndarray,
    qx: np.ndarray,
    qy: np.ndarray,
    qz: np.ndarray,
    barriers: Sequence[ExteriorBarrier],
) -> np.ndarray:
    """
    Test explicitly modelled walls/railings along the daylight ray.

    ``t=1`` is the sampled glazing element.  The barrier scope distinguishes
    a balcony/return wall beyond the glazing from a railing or screen between
    the room and a physically offset glazing plane.
    """
    transmission = np.ones_like(qx, dtype=np.float64)
    dx = qx - point[0]
    dy = qy - point[1]
    dz = qz - point[2]
    px, py = point[0], point[1]
    for barrier in barriers:
        ax = barrier.start.x / 1000.0
        ay = barrier.start.y / 1000.0
        sx = (barrier.end.x - barrier.start.x) / 1000.0
        sy = (barrier.end.y - barrier.start.y) / 1000.0
        denominator = dx * sy - dy * sx
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((ax - px) * sy - (ay - py) * sx) / denominator
            u = ((ax - px) * dy - (ay - py) * dx) / denominator
        z_intersection = point[2] + t * dz
        if barrier.ray_scope == "between_point_and_window":
            scope_mask = (t > 1e-8) & (t < 1.0 - 1e-8)
        elif barrier.ray_scope == "all":
            scope_mask = t > 1e-8
        else:
            scope_mask = t > 1.0 + 1e-8
        intersects = (
            np.isfinite(t)
            & np.isfinite(u)
            & (np.abs(denominator) > 1e-12)
            & scope_mask
            & (u >= -1e-9)
            & (u <= 1.0 + 1e-9)
            & (
                z_intersection
                >= barrier.bottom_height_mm / 1000.0 - 1e-9
            )
            & (
                z_intersection
                <= barrier.top_height_mm / 1000.0 + 1e-9
            )
        )
        if np.any(intersects):
            effective = float(np.clip(
                barrier.visible_transmittance, 0.0, 1.0
            ))
            transmission = np.where(
                intersects,
                transmission * effective,
                transmission,
            )
    return transmission


def _ds_point(
    point: np.ndarray,
    wall: WallSegment,
    opening: WallOpening,
    space: SpaceModel,
    ndiv: int,
) -> float:
    normal, u_axis, v_axis, origin = _wall_axes_m(wall)
    width = opening.width_mm / 1000.0
    height = opening.height_mm / 1000.0
    u0 = opening.offset_mm / 1000.0
    v0 = opening.sill_height_mm / 1000.0
    d_area = (width / ndiv) * (height / ndiv)

    us = u0 + (np.arange(ndiv, dtype=np.float64) + 0.5) * width / ndiv
    vs = v0 + (np.arange(ndiv, dtype=np.float64) + 0.5) * height / ndiv
    uu, vv = np.meshgrid(us, vs, indexing="ij")
    qx = origin[0] + u_axis[0] * uu + v_axis[0] * vv
    qy = origin[1] + u_axis[1] * uu + v_axis[1] * vv
    qz = origin[2] + u_axis[2] * uu + v_axis[2] * vv
    plane_offset = opening.plane_offset_mm / 1000.0
    if plane_offset > 0.0:
        # ``normal`` points into the room; the real glazing plane is displaced
        # in the opposite (outward) direction.
        qx = qx - normal[0] * plane_offset
        qy = qy - normal[1] * plane_offset

    delta_x = qx - point[0]
    delta_y = qy - point[1]
    delta_z = qz - point[2]
    radius2 = delta_x * delta_x + delta_y * delta_y + delta_z * delta_z
    radius = np.sqrt(np.maximum(radius2, 1e-18))
    sin_elevation = np.clip(delta_z / radius, -1.0, 1.0)
    sky_luminance = (1.0 + 2.0 * sin_elevation) / 3.0
    cos_window = (
        normal[0] * (-delta_x / radius)
        + normal[1] * (-delta_y / radius)
        + normal[2] * (-delta_z / radius)
    )
    cos_workplane = delta_z / radius
    valid = (
        (radius > 1e-9)
        & (sin_elevation > 0.0)
        & (cos_window > 0.0)
        & (cos_workplane > 0.0)
    )
    valid &= ~_blocked_by_other_boundaries(
        point,
        qx,
        qy,
        wall,
        space.wall_segments(),
    )

    shading = space.shading
    shading_key = str(
        opening.metadata.get("legacy_window_id", opening.id)
    )
    depth_mm, gap_mm, tilt_deg = shading.get_overhang_for(shading_key)
    if (
        getattr(shading, "type", "none") == "horizontal_overhang"
        and depth_mm > 0.0
    ):
        theta = math.radians(tilt_deg)
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        if abs(sin_theta) > 1e-9:
            length = depth_mm / 1000.0
            gap = gap_mm / 1000.0
            overhang_root_z = opening.head_height_mm / 1000.0 + gap
            tip_distance = length * sin_theta
            cot_theta = cos_theta / sin_theta
            outward_x, outward_y = -normal[0], -normal[1]
            outward_slope = outward_x * delta_x + outward_y * delta_y
            with np.errstate(divide="ignore", invalid="ignore"):
                t_star = (
                    overhang_root_z - point[2] + outward_slope * cot_theta
                ) / (delta_z + outward_slope * cot_theta)
            wx = point[0] + t_star * delta_x
            wy = point[1] + t_star * delta_y
            outward_distance = (
                (wx - origin[0]) * outward_x
                + (wy - origin[1]) * outward_y
            )
            lateral = (
                (wx - origin[0]) * u_axis[0]
                + (wy - origin[1]) * u_axis[1]
            )
            blocked = (
                np.isfinite(t_star)
                & (t_star >= 1.0)
                & (outward_distance > 0.0)
                & (outward_distance <= tip_distance)
                & (lateral >= u0 - 1e-9)
                & (lateral <= u0 + width + 1e-9)
            )
            valid &= ~blocked

    barrier_transmission = _exterior_barrier_transmission(
        point,
        qx,
        qy,
        qz,
        space.exterior_barriers,
    )
    solid_angle = np.where(valid, cos_window * d_area / radius2, 0.0)
    contribution = np.where(
        valid,
        (
            sky_luminance
            * cos_workplane
            * solid_angle
            * barrier_transmission
        ),
        0.0,
    )
    outdoor_normalisation = 7.0 * math.pi / 9.0
    daylight = (
        contribution.sum()
        * opening.visible_transmittance
        / (math.pi * outdoor_normalisation)
    )
    return float(max(0.0, daylight * 100.0))


def _dext_point(
    point: np.ndarray,
    wall: WallSegment,
    opening: WallOpening,
    space: SpaceModel,
) -> float:
    normal, u_axis, v_axis, origin = _wall_axes_m(wall)
    sill_u = (
        opening.offset_mm + opening.width_mm / 2.0
    ) / 1000.0
    sill_v = opening.sill_height_mm / 1000.0
    sill = origin + u_axis * sill_u + v_axis * sill_v
    if opening.plane_offset_mm > 0.0:
        sill = sill - normal * (opening.plane_offset_mm / 1000.0)
    vector = sill - point
    distance = float(np.linalg.norm(vector))
    if distance <= 1e-12:
        return 0.0
    horizontal = math.hypot(float(vector[0]), float(vector[1]))
    elevation = max(0.0, math.atan2(float(vector[2]), horizontal))
    value = (
        space.material.rho_ground
        * opening.visible_transmittance
        * (1.0 - math.cos(elevation))
        / 2.0
    )
    return float(max(0.0, value * 100.0))


def _dint(ds: float, dext: float, rho: float) -> float:
    rho = min(rho, 0.995)
    return float(max(0.0, rho * (ds + dext) / (1.0 - rho)))


def _exterior_windows(
    space: SpaceModel,
) -> List[Tuple[WallSegment, WallOpening]]:
    return [
        (wall, opening)
        for wall in space.wall_segments()
        if wall.boundary_type in {"exterior", "ground"}
        for opening in wall.windows()
    ]


def _quick(space: SpaceModel, outdoor_illuminance: float) -> dict:
    floor_area = space_floor_area_mm2(space) / 1_000_000.0
    windows = [
        opening
        for _wall, opening in _exterior_windows(space)
    ]
    if floor_area <= 1e-12 or not windows:
        return {"E_avg": 0.0, "DF_avg": 0.0, "WFR": 0.0}
    window_area = sum(opening.area_m2 for opening in windows)
    tau_effective = sum(
        opening.area_m2 * opening.visible_transmittance
        for opening in windows
    ) / window_area
    rho = _space_rho_bar(space)
    average = (
        outdoor_illuminance
        * tau_effective
        * window_area
        / (floor_area * max(1.0 - rho, 0.01))
    )
    return {
        "E_avg": average,
        "DF_avg": average / outdoor_illuminance * 100.0,
        "WFR": window_area / floor_area,
        "tau_eff": tau_effective,
        "rho_bar": rho,
        "A_win": window_area,
        "Af": floor_area,
    }


def compute_complex_daylight(
    space: SpaceModel,
    E_out: float = 13_500.0,
    grid_mm: float = GRID_MM,
    ndiv: int = WIN_DIV,
    store_components: bool = True,
    row_cb=None,
    ra_threshold: float = 2.0,
) -> ComplexDaylightResult:
    """Calculate daylight for one validated arbitrary-polygon space."""
    errors = [
        issue for issue in validate_space(space)
        if issue.severity == "error"
    ]
    if errors:
        raise ValueError(
            "复杂空间几何未通过校验："
            + "；".join(issue.message for issue in errors)
        )
    if E_out <= 0.0:
        raise ValueError("室外照度必须大于0。")
    if ndiv <= 0:
        raise ValueError("窗面离散数必须大于0。")

    result = ComplexDaylightResult()
    result.E_out = E_out
    result.grid_mm = grid_mm
    result.ndiv = ndiv
    result.Ra_threshold = ra_threshold
    result.method = (
        f"CIE 110-1994 + BRS + Littlefair BRE 209 "
        f"+ arbitrary polygon occlusion "
        f"+ {len(space.exterior_barriers)} explicit daylight barriers "
        f"(ndiv={ndiv})"
    )
    result.quick = _quick(space, E_out)

    xs, ys, mask = build_workplane_grid(space, grid_mm)
    result.grid_x = xs
    result.grid_y = ys
    result.valid_mask = mask
    rho = _space_rho_bar(space)
    result.rho_bar = rho
    windows = _exterior_windows(space)

    shape = mask.shape
    df_array = np.full(shape, np.nan, dtype=np.float64)
    ds_array = np.full(shape, np.nan, dtype=np.float64)
    dext_array = np.full(shape, np.nan, dtype=np.float64)
    dint_array = np.full(shape, np.nan, dtype=np.float64)
    workplane_z = WORK_PLANE_MM / 1000.0

    for iy, y in enumerate(ys):
        if row_cb is not None:
            row_cb(iy)
        for ix, x in enumerate(xs):
            if not mask[iy, ix]:
                continue
            point = np.array(
                [x / 1000.0, y / 1000.0, workplane_z],
                dtype=np.float64,
            )
            ds_total = 0.0
            dext_total = 0.0
            for wall, opening in windows:
                ds_total += _ds_point(point, wall, opening, space, ndiv)
                dext_total += _dext_point(point, wall, opening, space)
            dint = _dint(ds_total, dext_total, rho)
            ds_array[iy, ix] = ds_total
            dext_array[iy, ix] = dext_total
            dint_array[iy, ix] = dint
            df_array[iy, ix] = ds_total + dext_total + dint

    result.DF = df_array
    result.E_lux = df_array / 100.0 * E_out
    if store_components:
        result.Ds = ds_array
        result.Dext = dext_array
        result.Dint = dint_array
    _fill_stats(result)
    return result


def _fill_stats(result: ComplexDaylightResult) -> None:
    valid_df = result.DF[result.valid_mask]
    valid_e = result.E_lux[result.valid_mask]
    result.DF_avg = float(np.mean(valid_df))
    result.DF_min = float(np.min(valid_df))
    result.DF_max = float(np.max(valid_df))
    result.E_avg = float(np.mean(valid_e))
    result.E_min = float(np.min(valid_e))
    result.E_max = float(np.max(valid_e))
    result.U0 = (
        result.E_min / result.E_avg
        if result.E_avg > 1e-12 else 0.0
    )
    result.compliant_300 = result.E_avg >= 300.0
    result.compliant_u0 = result.U0 >= 0.70
    result.Ra = float(np.mean(valid_df >= result.Ra_threshold))
