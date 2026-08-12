"""Planar wall graph utilities and automatic closed-room recognition."""
from __future__ import annotations

import math
from typing import Iterable

from model import Point, RoomFace, Wall


def cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def point_on_segment(
    point: Point,
    start: Point,
    end: Point,
    tolerance_mm: float,
) -> bool:
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    if length <= tolerance_mm:
        return point.distance_to(start) <= tolerance_mm
    distance = abs(cross(
        point.x - start.x,
        point.y - start.y,
        dx,
        dy,
    )) / length
    if distance > tolerance_mm:
        return False
    projection = (
        (point.x - start.x) * dx
        + (point.y - start.y) * dy
    ) / (length * length)
    return -tolerance_mm / length <= projection <= 1.0 + tolerance_mm / length


def segment_intersection(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
    tolerance_mm: float,
) -> Point | None:
    rx, ry = first_end.x - first_start.x, first_end.y - first_start.y
    sx, sy = second_end.x - second_start.x, second_end.y - second_start.y
    denominator = cross(rx, ry, sx, sy)
    if abs(denominator) <= max(tolerance_mm, 1e-9):
        return None
    qpx = second_start.x - first_start.x
    qpy = second_start.y - first_start.y
    t = cross(qpx, qpy, sx, sy) / denominator
    u = cross(qpx, qpy, rx, ry) / denominator
    epsilon = tolerance_mm / max(
        math.hypot(rx, ry),
        math.hypot(sx, sy),
        1.0,
    )
    if -epsilon <= t <= 1.0 + epsilon and -epsilon <= u <= 1.0 + epsilon:
        return Point(first_start.x + t * rx, first_start.y + t * ry)
    return None


def signed_area(points: list[Point]) -> float:
    return 0.5 * sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )


def _node_key(point: Point, tolerance_mm: float) -> tuple[int, int]:
    return (
        round(point.x / tolerance_mm),
        round(point.y / tolerance_mm),
    )


def _normalise_angle(value: float) -> float:
    return value % (2.0 * math.pi)


def _wall_offsets(wall: Wall) -> tuple[float, float]:
    if wall.axis == "left":
        return 0.0, wall.width_mm
    if wall.axis == "right":
        return -wall.width_mm, 0.0
    return -wall.width_mm / 2.0, wall.width_mm / 2.0


def _wall_rectangle(wall: Wall) -> list[Point]:
    low, high = _wall_offsets(wall)
    nx, ny = wall.normal

    def offset(point: Point, distance: float) -> Point:
        return Point(
            point.x + nx * distance,
            point.y + ny * distance,
        )

    return [
        offset(wall.start, low),
        offset(wall.end, low),
        offset(wall.end, high),
        offset(wall.start, high),
    ]


def _convex_polygons_overlap(
    first: list[Point],
    second: list[Point],
    tolerance_mm: float,
) -> bool:
    """Separating-axis overlap test for two convex wall rectangles."""
    for polygon in (first, second):
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            edge_x = end.x - start.x
            edge_y = end.y - start.y
            axis_length = math.hypot(edge_x, edge_y)
            if axis_length <= 1e-9:
                continue
            axis_x = -edge_y / axis_length
            axis_y = edge_x / axis_length
            first_projection = [
                point.x * axis_x + point.y * axis_y
                for point in first
            ]
            second_projection = [
                point.x * axis_x + point.y * axis_y
                for point in second
            ]
            if (
                max(first_projection)
                < min(second_projection) - tolerance_mm
                or max(second_projection)
                < min(first_projection) - tolerance_mm
            ):
                return False
    return True


def _infinite_axis_intersection(
    first: Wall,
    second: Wall,
) -> tuple[Point, float, float] | None:
    """Return intersection and unbounded distances along both wall axes."""
    first_x, first_y = first.direction
    second_x, second_y = second.direction
    denominator = cross(
        first_x,
        first_y,
        second_x,
        second_y,
    )
    if abs(denominator) <= 1e-9:
        return None
    delta_x = second.start.x - first.start.x
    delta_y = second.start.y - first.start.y
    first_distance = cross(
        delta_x,
        delta_y,
        second_x,
        second_y,
    ) / denominator
    second_distance = cross(
        delta_x,
        delta_y,
        first_x,
        first_y,
    ) / denominator
    return (
        Point(
            first.start.x + first_x * first_distance,
            first.start.y + first_y * first_distance,
        ),
        first_distance,
        second_distance,
    )


def _healed_wall_axes(
    walls: list[Wall],
    tolerance_mm: float,
) -> list[Wall]:
    """Extend only physically touching wall ends to their true axis junction.

    With an offset axis (especially ``left``/``right``), two solid wall strips
    can form a closed corner even when their drawn axis endpoints differ by up
    to the neighbouring wall thickness.  Room topology must follow the solid
    construction, not mistake that legitimate overlap for an open boundary.
    """
    if len(walls) < 2:
        return walls
    proposals: dict[tuple[int, str], tuple[float, Point]] = {}
    rectangles = [_wall_rectangle(wall) for wall in walls]

    def propose(
        wall_index: int,
        distance: float,
        intersection: Point,
        limit: float,
    ) -> None:
        wall = walls[wall_index]
        if distance < -tolerance_mm:
            extension = -distance
            endpoint = "start"
        elif distance > wall.length_mm + tolerance_mm:
            extension = distance - wall.length_mm
            endpoint = "end"
        else:
            return
        if extension > limit:
            return
        key = wall_index, endpoint
        previous = proposals.get(key)
        if previous is None or extension < previous[0]:
            proposals[key] = extension, intersection

    for first_index, first in enumerate(walls):
        for second_index in range(first_index + 1, len(walls)):
            second = walls[second_index]
            intersection = _infinite_axis_intersection(first, second)
            if intersection is None:
                continue
            point, first_distance, second_distance = intersection
            limit = max(first.width_mm, second.width_mm) * 2.0 + tolerance_mm
            if not (
                -limit
                <= first_distance
                <= first.length_mm + limit
                and -limit
                <= second_distance
                <= second.length_mm + limit
            ):
                continue
            if not _convex_polygons_overlap(
                rectangles[first_index],
                rectangles[second_index],
                tolerance_mm,
            ):
                continue
            propose(first_index, first_distance, point, limit)
            propose(second_index, second_distance, point, limit)

    healed: list[Wall] = []
    for index, wall in enumerate(walls):
        start = proposals.get((index, "start"), (0.0, wall.start))[1]
        end = proposals.get((index, "end"), (0.0, wall.end))[1]
        healed.append(Wall(
            start=start,
            end=end,
            height_mm=wall.height_mm,
            width_mm=wall.width_mm,
            axis=wall.axis,
            id=wall.id,
        ))
    return healed


def polygonize_walls(
    walls: Iterable[Wall],
    tolerance_mm: float = 20.0,
    minimum_area_mm2: float = 100_000.0,
) -> list[RoomFace]:
    """
    Split wall axes at T-junctions/intersections and enumerate bounded faces.

    The outer infinite face is clockwise and is discarded.  Counter-clockwise
    faces are recognised rooms.  Room names are intentionally not generated.
    """
    walls = [wall for wall in walls if wall.length_mm > tolerance_mm]
    walls = _healed_wall_axes(walls, tolerance_mm)
    if len(walls) < 3:
        return []
    split_points: list[list[Point]] = [
        [wall.start, wall.end] for wall in walls
    ]

    # Every wall endpoint can create a T-junction on another wall.
    endpoints = [
        point
        for wall in walls
        for point in (wall.start, wall.end)
    ]
    for index, wall in enumerate(walls):
        for point in endpoints:
            if point_on_segment(
                point, wall.start, wall.end, tolerance_mm,
            ):
                split_points[index].append(point)

    # Also split true crossing intersections.
    for first_index, first in enumerate(walls):
        for second_index in range(first_index + 1, len(walls)):
            second = walls[second_index]
            intersection = segment_intersection(
                first.start,
                first.end,
                second.start,
                second.end,
                tolerance_mm,
            )
            if intersection is None:
                continue
            split_points[first_index].append(intersection)
            split_points[second_index].append(intersection)

    nodes: dict[tuple[int, int], Point] = {}
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for wall, points in zip(walls, split_points):
        dx, dy = wall.direction
        unique: dict[tuple[int, int], Point] = {}
        for point in points:
            key = _node_key(point, tolerance_mm)
            unique.setdefault(key, point)
            nodes.setdefault(key, point)
        ordered = sorted(
            unique.items(),
            key=lambda item: (
                (item[1].x - wall.start.x) * dx
                + (item[1].y - wall.start.y) * dy
            ),
        )
        for (first_key, first), (second_key, second) in zip(
            ordered, ordered[1:]
        ):
            if first.distance_to(second) <= tolerance_mm:
                continue
            edge = tuple(sorted((first_key, second_key)))
            edges.add(edge)

    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {
        key: set() for key in nodes
    }
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)

    visited: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    faces: list[RoomFace] = []
    seen_faces: set[tuple[tuple[int, int], ...]] = set()
    maximum_steps = max(20, len(edges) * 3)

    for first, second in list(edges):
        for directed_start in ((first, second), (second, first)):
            if directed_start in visited:
                continue
            u, v = directed_start
            loop_keys: list[tuple[int, int]] = []
            complete = False
            for _step in range(maximum_steps):
                if (u, v) in visited and (u, v) != directed_start:
                    break
                visited.add((u, v))
                loop_keys.append(u)
                candidates = [
                    candidate
                    for candidate in adjacency[v]
                    if candidate != u
                ]
                if not candidates:
                    break
                reverse_angle = math.atan2(
                    nodes[u].y - nodes[v].y,
                    nodes[u].x - nodes[v].x,
                )
                # Immediately clockwise from the reverse direction keeps the
                # bounded face on the left of each traversed half-edge.
                next_key = min(
                    candidates,
                    key=lambda candidate: _normalise_angle(
                        reverse_angle
                        - math.atan2(
                            nodes[candidate].y - nodes[v].y,
                            nodes[candidate].x - nodes[v].x,
                        )
                    ),
                )
                u, v = v, next_key
                if (u, v) == directed_start:
                    complete = True
                    break
            if not complete or len(loop_keys) < 3:
                continue
            points = [nodes[key] for key in loop_keys]
            area = signed_area(points)
            if area <= minimum_area_mm2:
                continue
            rotations = [
                tuple(loop_keys[index:] + loop_keys[:index])
                for index in range(len(loop_keys))
            ]
            canonical = min(rotations)
            if canonical in seen_faces:
                continue
            seen_faces.add(canonical)
            faces.append(RoomFace(points=points, area_mm2=area))

    return sorted(
        faces,
        key=lambda face: (
            min(point.y for point in face.points),
            min(point.x for point in face.points),
        ),
    )
