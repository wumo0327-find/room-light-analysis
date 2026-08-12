"""
core/space_geometry.py — Deterministic polygon geometry and validation  v3.5.0

The functions here are intentionally independent from CAD interpretation.  A
manual editor, an AI importer and project-file loading all pass through the
same validation gate before a space may enter physical calculations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, List, Optional, Sequence, Tuple

from core.complex_models import (
    BARRIER_KINDS,
    BARRIER_RAY_SCOPES,
    BOUNDARY_TYPES,
    LOOP_KINDS,
    OPENING_KINDS,
    BoundaryLoop,
    Point2D,
    SpaceModel,
)


EPSILON_MM = 1e-6


@dataclass(frozen=True)
class GeometryIssue:
    """One deterministic validation result."""

    code: str
    message: str
    severity: str = "error"
    object_id: str = ""


def signed_ring_area_mm2(points: Sequence[Point2D]) -> float:
    """Shoelace signed area; positive means counter-clockwise."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        following = points[(index + 1) % len(points)]
        total += point.x * following.y - following.x * point.y
    return total / 2.0


def ring_area_mm2(points: Sequence[Point2D]) -> float:
    return abs(signed_ring_area_mm2(points))


def space_floor_area_mm2(space: SpaceModel) -> float:
    outer = space.outer_loop()
    if outer is None:
        return 0.0
    area = ring_area_mm2(outer.points())
    area -= sum(ring_area_mm2(loop.points()) for loop in space.hole_loops())
    return max(0.0, area)


def _orientation(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _point_on_segment(
    point: Point2D,
    start: Point2D,
    end: Point2D,
    tolerance_mm: float = EPSILON_MM,
) -> bool:
    if abs(_orientation(start, end, point)) > tolerance_mm:
        return False
    return (
        min(start.x, end.x) - tolerance_mm
        <= point.x
        <= max(start.x, end.x) + tolerance_mm
        and min(start.y, end.y) - tolerance_mm
        <= point.y
        <= max(start.y, end.y) + tolerance_mm
    )


def segments_intersect(
    a1: Point2D,
    a2: Point2D,
    b1: Point2D,
    b2: Point2D,
    tolerance_mm: float = EPSILON_MM,
) -> bool:
    """Return True when two closed line segments intersect or touch."""
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)

    if (
        ((o1 > tolerance_mm and o2 < -tolerance_mm)
         or (o1 < -tolerance_mm and o2 > tolerance_mm))
        and ((o3 > tolerance_mm and o4 < -tolerance_mm)
             or (o3 < -tolerance_mm and o4 > tolerance_mm))
    ):
        return True

    return (
        (abs(o1) <= tolerance_mm and _point_on_segment(b1, a1, a2, tolerance_mm))
        or (abs(o2) <= tolerance_mm and _point_on_segment(b2, a1, a2, tolerance_mm))
        or (abs(o3) <= tolerance_mm and _point_on_segment(a1, b1, b2, tolerance_mm))
        or (abs(o4) <= tolerance_mm and _point_on_segment(a2, b1, b2, tolerance_mm))
    )


def point_in_ring(
    point: Point2D,
    ring: Sequence[Point2D],
    include_boundary: bool = True,
) -> bool:
    """Ray-casting point-in-polygon test for a simple ring."""
    if len(ring) < 3:
        return False
    inside = False
    previous = ring[-1]
    for current in ring:
        if _point_on_segment(point, previous, current):
            return include_boundary
        crosses = (current.y > point.y) != (previous.y > point.y)
        if crosses:
            x_at_y = (
                (previous.x - current.x)
                * (point.y - current.y)
                / (previous.y - current.y)
                + current.x
            )
            if x_at_y > point.x:
                inside = not inside
        previous = current
    return inside


def point_in_space(
    point: Point2D,
    space: SpaceModel,
    include_boundary: bool = True,
) -> bool:
    outer = space.outer_loop()
    if outer is None or not point_in_ring(point, outer.points(), include_boundary):
        return False
    return not any(
        point_in_ring(point, hole.points(), include_boundary=False)
        for hole in space.hole_loops()
    )


def _non_adjacent_self_intersections(
    points: Sequence[Point2D],
) -> List[Tuple[int, int]]:
    intersections: List[Tuple[int, int]] = []
    count = len(points)
    for i in range(count):
        a1, a2 = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or j == (i + 1) % count or (j + 1) % count == i:
                continue
            b1, b2 = points[j], points[(j + 1) % count]
            if segments_intersect(a1, a2, b1, b2):
                intersections.append((i, j))
    return intersections


def _loops_intersect(first: BoundaryLoop, second: BoundaryLoop) -> bool:
    first_points = first.points()
    second_points = second.points()
    for i, a1 in enumerate(first_points):
        a2 = first_points[(i + 1) % len(first_points)]
        for j, b1 in enumerate(second_points):
            b2 = second_points[(j + 1) % len(second_points)]
            if segments_intersect(a1, a2, b1, b2):
                return True
    return False


def validate_space(
    space: SpaceModel,
    closure_tolerance_mm: float = 1.0,
) -> List[GeometryIssue]:
    """Validate all geometry required before calculation or project export."""
    issues: List[GeometryIssue] = []
    outer_loops = [loop for loop in space.boundary_loops if loop.kind == "outer"]

    if space.height_mm <= 0.0:
        issues.append(GeometryIssue(
            "space_height_nonpositive",
            "空间层高必须大于0。",
            object_id=space.id,
        ))
    if len(outer_loops) != 1:
        issues.append(GeometryIssue(
            "outer_loop_count",
            f"空间必须且只能有一个外边界，当前为{len(outer_loops)}个。",
            object_id=space.id,
        ))

    seen_barrier_ids = set()
    for barrier in space.exterior_barriers:
        if barrier.id in seen_barrier_ids:
            issues.append(GeometryIssue(
                "duplicate_barrier_id",
                "窗外屏障ID重复。",
                object_id=barrier.id,
            ))
        seen_barrier_ids.add(barrier.id)
        if barrier.kind not in BARRIER_KINDS:
            issues.append(GeometryIssue(
                "unknown_barrier_kind",
                f"未知窗外屏障类型：{barrier.kind}。",
                object_id=barrier.id,
            ))
        if barrier.ray_scope not in BARRIER_RAY_SCOPES:
            issues.append(GeometryIssue(
                "unknown_barrier_ray_scope",
                f"未知采光屏障射线范围：{barrier.ray_scope}。",
                object_id=barrier.id,
            ))
        if barrier.length_mm <= closure_tolerance_mm:
            issues.append(GeometryIssue(
                "barrier_too_short",
                "窗外屏障平面线段长度必须大于几何容差。",
                object_id=barrier.id,
            ))
        if barrier.top_height_mm <= barrier.bottom_height_mm:
            issues.append(GeometryIssue(
                "barrier_height_invalid",
                "窗外屏障顶部高度必须大于底部高度。",
                object_id=barrier.id,
            ))
        if not 0.0 <= barrier.visible_transmittance <= 1.0:
            issues.append(GeometryIssue(
                "barrier_transmittance_invalid",
                "窗外屏障有效透光率必须在0到1之间。",
                object_id=barrier.id,
            ))

    seen_wall_ids = set()
    seen_opening_ids = set()
    for loop in space.boundary_loops:
        if loop.kind not in LOOP_KINDS:
            issues.append(GeometryIssue(
                "unknown_loop_kind",
                f"未知边界类型：{loop.kind}。",
                object_id=loop.id,
            ))
        if len(loop.segments) < 3:
            issues.append(GeometryIssue(
                "too_few_walls",
                "闭合边界至少需要三段墙。",
                object_id=loop.id,
            ))
            continue
        if not loop.is_closed(closure_tolerance_mm):
            issues.append(GeometryIssue(
                "loop_not_closed",
                "边界墙段首尾没有闭合。",
                object_id=loop.id,
            ))

        points = loop.points()
        if ring_area_mm2(points) <= EPSILON_MM:
            issues.append(GeometryIssue(
                "loop_zero_area",
                "边界面积为0。",
                object_id=loop.id,
            ))
        for first_index, second_index in _non_adjacent_self_intersections(points):
            issues.append(GeometryIssue(
                "loop_self_intersection",
                f"边界第{first_index + 1}段与第{second_index + 1}段相交。",
                object_id=loop.id,
            ))

        signed_area = signed_ring_area_mm2(points)
        if loop.kind == "outer" and signed_area < 0.0:
            issues.append(GeometryIssue(
                "outer_clockwise",
                "外边界应按逆时针方向排列。",
                "warning",
                loop.id,
            ))
        if loop.kind == "hole" and signed_area > 0.0:
            issues.append(GeometryIssue(
                "hole_counterclockwise",
                "内部空洞应按顺时针方向排列。",
                "warning",
                loop.id,
            ))

        for wall in loop.segments:
            if wall.id in seen_wall_ids:
                issues.append(GeometryIssue(
                    "duplicate_wall_id",
                    f"墙段ID重复：{wall.id}。",
                    object_id=wall.id,
                ))
            seen_wall_ids.add(wall.id)
            if wall.length_mm <= EPSILON_MM:
                issues.append(GeometryIssue(
                    "zero_length_wall",
                    "墙段长度必须大于0。",
                    object_id=wall.id,
                ))
            if wall.boundary_type not in BOUNDARY_TYPES:
                issues.append(GeometryIssue(
                    "unknown_boundary_type",
                    f"未知墙体边界类型：{wall.boundary_type}。",
                    object_id=wall.id,
                ))
            if wall.thickness_mm < 0.0:
                issues.append(GeometryIssue(
                    "negative_wall_thickness",
                    "墙厚不能为负数。",
                    object_id=wall.id,
                ))

            ordered_openings = sorted(wall.openings, key=lambda item: item.offset_mm)
            previous_end = -math.inf
            for opening in ordered_openings:
                if opening.id in seen_opening_ids:
                    issues.append(GeometryIssue(
                        "duplicate_opening_id",
                        f"洞口ID重复：{opening.id}。",
                        object_id=opening.id,
                    ))
                seen_opening_ids.add(opening.id)
                if opening.kind not in OPENING_KINDS:
                    issues.append(GeometryIssue(
                        "unknown_opening_kind",
                        f"未知洞口类型：{opening.kind}。",
                        object_id=opening.id,
                    ))
                if opening.width_mm <= 0.0 or opening.height_mm <= 0.0:
                    issues.append(GeometryIssue(
                        "opening_size_nonpositive",
                        "洞口宽度和高度必须大于0。",
                        object_id=opening.id,
                    ))
                if opening.plane_offset_mm < 0.0:
                    issues.append(GeometryIssue(
                        "opening_plane_offset_negative",
                        "洞口外移距离不能小于0。",
                        object_id=opening.id,
                    ))
                if opening.offset_mm < -closure_tolerance_mm:
                    issues.append(GeometryIssue(
                        "opening_before_wall",
                        "洞口起点超出墙段起点。",
                        object_id=opening.id,
                    ))
                if opening.end_offset_mm > wall.length_mm + closure_tolerance_mm:
                    issues.append(GeometryIssue(
                        "opening_after_wall",
                        "洞口终点超出墙段终点。",
                        object_id=opening.id,
                    ))
                if opening.sill_height_mm < 0.0:
                    issues.append(GeometryIssue(
                        "opening_below_floor",
                        "洞口底部不能低于房间地面。",
                        object_id=opening.id,
                    ))
                if opening.head_height_mm > space.height_mm + closure_tolerance_mm:
                    issues.append(GeometryIssue(
                        "opening_above_ceiling",
                        "洞口顶部超过房间层高。",
                        object_id=opening.id,
                    ))
                if opening.offset_mm < previous_end - closure_tolerance_mm:
                    issues.append(GeometryIssue(
                        "overlapping_openings",
                        "同一墙段上的洞口发生重叠。",
                        object_id=opening.id,
                    ))
                previous_end = max(previous_end, opening.end_offset_mm)

    if len(outer_loops) == 1:
        outer = outer_loops[0]
        for hole in space.hole_loops():
            if hole.segments and not point_in_ring(
                hole.segments[0].start,
                outer.points(),
                include_boundary=False,
            ):
                issues.append(GeometryIssue(
                    "hole_outside_outer",
                    "内部空洞不在房间外边界内。",
                    object_id=hole.id,
                ))
            if _loops_intersect(outer, hole):
                issues.append(GeometryIssue(
                    "hole_touches_outer",
                    "内部空洞与房间外边界相交。",
                    object_id=hole.id,
                ))

        holes = space.hole_loops()
        for i, first in enumerate(holes):
            for second in holes[i + 1:]:
                if _loops_intersect(first, second):
                    issues.append(GeometryIssue(
                        "holes_intersect",
                        "两个内部空洞发生相交。",
                        object_id=second.id,
                    ))

    if not any(issue.severity == "error" for issue in issues):
        if space_floor_area_mm2(space) <= EPSILON_MM:
            issues.append(GeometryIssue(
                "space_zero_area",
                "扣除内部空洞后，空间面积必须大于0。",
                object_id=space.id,
            ))
    return issues


def has_geometry_errors(issues: Iterable[GeometryIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)
