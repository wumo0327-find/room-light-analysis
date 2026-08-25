"""Export a BP drafting document as a RoomLight v4 building project."""
from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
from pathlib import Path

from .model import DraftDocument, Point, Railing, Wall


ROOMLIGHT_FILE_VERSION = "4.4.0"


@dataclass(frozen=True)
class RlprojExportSummary:
    path: Path
    spaces: int
    walls: int
    windows: int
    railings: int


def _point_data(point: Point) -> list[float]:
    return [float(point.x), float(point.y)]


def _point_key(point: Point, tolerance_mm: float = 1.0) -> tuple[int, int]:
    return (
        round(point.x / tolerance_mm),
        round(point.y / tolerance_mm),
    )


def _edge_key(
    start: Point,
    end: Point,
) -> tuple[tuple[int, int], tuple[int, int]]:
    return tuple(sorted((_point_key(start), _point_key(end))))


def _unbounded_projection(
    wall: Wall,
    point: Point,
) -> tuple[float, float]:
    direction_x, direction_y = wall.direction
    delta_x = point.x - wall.start.x
    delta_y = point.y - wall.start.y
    along = delta_x * direction_x + delta_y * direction_y
    perpendicular = abs(
        delta_x * (-direction_y) + delta_y * direction_x
    )
    return along, perpendicular


def _source_wall_for_edge(
    document: DraftDocument,
    start: Point,
    end: Point,
) -> Wall:
    """Find the BP wall axis supporting one recognised-room edge."""
    candidates: list[tuple[float, Wall]] = []
    for wall in document.walls:
        start_along, start_distance = _unbounded_projection(wall, start)
        end_along, end_distance = _unbounded_projection(wall, end)
        line_tolerance = max(document.snap_mm, 1.0)
        extension = wall.width_mm * 2.0 + line_tolerance
        if max(start_distance, end_distance) > line_tolerance:
            continue
        low, high = sorted((start_along, end_along))
        if low < -extension or high > wall.length_mm + extension:
            continue
        outside = max(0.0, -low) + max(0.0, high - wall.length_mm)
        candidates.append((
            outside + start_distance + end_distance,
            wall,
        ))
    if not candidates:
        raise ValueError(
            "房间边界无法对应到实体墙："
            f"({start.x:.0f}, {start.y:.0f}) → "
            f"({end.x:.0f}, {end.y:.0f})。"
        )
    return min(candidates, key=lambda item: item[0])[1]


def _canonical_face_signature(points: list[Point]) -> str:
    """Stable room-face identifier independent from its starting vertex."""
    keys = [(round(point.x, 3), round(point.y, 3)) for point in points]
    if not keys:
        return ""
    variants = []
    for sequence in (keys, list(reversed(keys))):
        variants.extend(
            tuple(sequence[index:] + sequence[:index])
            for index in range(len(sequence))
        )
    return "|".join(f"{x:g},{y:g}" for x, y in min(variants))


def _wall_offsets_on_face_edge(
    source_wall: Wall,
    edge_start: Point,
    edge_end: Point,
) -> tuple[float, float]:
    """Return physical strip offsets in the CCW face edge's left normal."""
    edge = Wall(edge_start, edge_end)
    if source_wall.axis == "left":
        low, high = 0.0, source_wall.width_mm
    elif source_wall.axis == "right":
        low, high = -source_wall.width_mm, 0.0
    else:
        low, high = -source_wall.width_mm / 2.0, source_wall.width_mm / 2.0
    alignment = (
        source_wall.direction[0] * edge.direction[0]
        + source_wall.direction[1] * edge.direction[1]
    )
    if alignment < 0.0:
        low, high = -high, -low
    return float(low), float(high)


def _offset_edge(
    start: Point,
    end: Point,
    offset_mm: float,
) -> tuple[Point, Point]:
    edge = Wall(start, end)
    nx, ny = edge.normal
    return (
        Point(start.x + nx * offset_mm, start.y + ny * offset_mm),
        Point(end.x + nx * offset_mm, end.y + ny * offset_mm),
    )


def _line_intersection(
    first: tuple[Point, Point],
    second: tuple[Point, Point],
) -> Point | None:
    ax = first[1].x - first[0].x
    ay = first[1].y - first[0].y
    bx = second[1].x - second[0].x
    by = second[1].y - second[0].y
    denominator = ax * by - ay * bx
    if abs(denominator) <= 1e-9:
        return None
    qx = second[0].x - first[0].x
    qy = second[0].y - first[0].y
    distance = (qx * by - qy * bx) / denominator
    return Point(first[0].x + ax * distance, first[0].y + ay * distance)


def _net_floor_points(
    face,
    source_walls: list[Wall],
) -> list[Point]:
    """Intersect room-side wall faces into the net usable-floor polygon."""
    offset_edges = []
    for index, start in enumerate(face.points):
        end = face.points[(index + 1) % len(face.points)]
        _low, room_side = _wall_offsets_on_face_edge(
            source_walls[index], start, end
        )
        offset_edges.append(_offset_edge(start, end, room_side))

    net_points = []
    for index, original in enumerate(face.points):
        previous = offset_edges[index - 1]
        current = offset_edges[index]
        intersection = _line_intersection(previous, current)
        max_width = max(
            source_walls[index - 1].width_mm,
            source_walls[index].width_mm,
            1.0,
        )
        if (
            intersection is None
            or intersection.distance_to(original) > max_width * 8.0
        ):
            # Collinear or pathological mitres: both offset endpoints describe
            # the same local inner face, so their midpoint is deterministic.
            intersection = Point(
                (previous[1].x + current[0].x) / 2.0,
                (previous[1].y + current[0].y) / 2.0,
            )
        net_points.append(intersection)
    return net_points


def _clean_polygon_points(
    points: list[Point],
    tolerance_mm: float = 1e-6,
) -> list[Point]:
    """Remove zero-length and redundant collinear edges after wall offset.

    A physical inner face can legitimately collapse a short wall-axis edge to
    zero length (for example a 200 mm return next to a 200 mm wall).  Keeping
    the duplicated vertex would make an otherwise valid room appear
    self-intersecting to the analytical validator.
    """
    cleaned: list[Point] = []
    for point in points:
        if not cleaned or point.distance_to(cleaned[-1]) > tolerance_mm:
            cleaned.append(point)
    if len(cleaned) > 1 and cleaned[0].distance_to(cleaned[-1]) <= tolerance_mm:
        cleaned.pop()

    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        output: list[Point] = []
        count = len(cleaned)
        for index, current in enumerate(cleaned):
            previous = cleaned[index - 1]
            following = cleaned[(index + 1) % count]
            ax = current.x - previous.x
            ay = current.y - previous.y
            bx = following.x - current.x
            by = following.y - current.y
            cross = ax * by - ay * bx
            scale = max(1.0, abs(ax) + abs(ay) + abs(bx) + abs(by))
            same_direction = ax * bx + ay * by >= -tolerance_mm
            if abs(cross) <= tolerance_mm * scale and same_direction:
                changed = True
                continue
            output.append(current)
        cleaned = output
    return cleaned


def _opening_data_for_edge(
    document: DraftDocument,
    source_wall: Wall,
    edge_start: Point,
    edge_end: Point,
    *,
    space_id: str,
    segment_index: int,
    space_height_mm: float,
    glazing_plane_offset_mm: float,
) -> list[dict]:
    edge = Wall(edge_start, edge_end)
    openings = []
    for window in document.windows:
        if window.wall_id != source_wall.id:
            continue
        world_start = source_wall.point_at(window.offset_mm)
        world_end = source_wall.point_at(window.end_offset_mm)
        start_along, start_distance = _unbounded_projection(
            edge,
            world_start,
        )
        end_along, end_distance = _unbounded_projection(
            edge,
            world_end,
        )
        if max(start_distance, end_distance) > max(document.snap_mm, 1.0):
            continue
        low, high = sorted((start_along, end_along))
        if low < -1.0 or high > edge.length_mm + 1.0:
            continue
        if window.sill_height_mm + window.height_mm > space_height_mm + 1.0:
            raise ValueError(
                f"窗体 {window.id} 顶部超过所在空间层高，"
                "请先修改墙高或窗体高度。"
            )
        openings.append({
            "id": f"{space_id}_opening_{segment_index}_{window.id}",
            "kind": "window",
            "name": "BP窗体",
            "offset_mm": max(0.0, low),
            "width_mm": high - low,
            "sill_height_mm": float(window.sill_height_mm),
            "height_mm": float(window.height_mm),
            "visible_transmittance": 0.71,
            "u_value": None,
            "solar_heat_gain_coefficient": None,
            "plane_offset_mm": float(max(0.0, glazing_plane_offset_mm)),
            "metadata": {
                "source": "building_plan_editor",
                "source_bp_window_id": window.id,
            },
        })
    return sorted(openings, key=lambda item: item["offset_mm"])


def _railing_transmittance(railing: Railing) -> float:
    material = railing.material.lower()
    if "玻璃" in material:
        return 0.75
    if "混凝土" in material or "栏板" in material:
        return 0.0
    if "金属" in material:
        return 0.55
    if "木" in material:
        return 0.45
    return 0.50


def _barriers_for_space(
    railings: list[Railing],
    space_id: str,
) -> list[dict]:
    return [
        {
            "id": f"{space_id}_barrier_{index}",
            "name": railing.material,
            "kind": "railing",
            "start": _point_data(railing.start),
            "end": _point_data(railing.end),
            "bottom_height_mm": 0.0,
            "top_height_mm": float(railing.height_mm),
            "visible_transmittance": _railing_transmittance(railing),
            "ray_scope": "all",
            "metadata": {
                "source": "building_plan_editor",
                "source_bp_railing_id": railing.id,
                "material": railing.material,
                "drawing_width_mm": railing.width_mm,
                "transmittance_is_estimate": True,
            },
        }
        for index, railing in enumerate(railings, start=1)
    ]


def build_rlproj_data(
    document: DraftDocument,
    *,
    project_name: str,
) -> tuple[dict, RlprojExportSummary]:
    """Convert recognised BP faces into a validated RoomLight JSON structure."""
    # Topology normalisation may split/merge walls.  Export from a detached
    # copy so clicking Export never changes the user's live drawing or
    # invalidates its current selection.
    document = DraftDocument.from_dict(document.to_dict())
    document.normalise_wall_topology()
    faces = document.recognised_rooms()
    if not faces:
        raise ValueError(
            "当前图纸没有识别到闭合房间，无法导出为可计算的rlproj。"
        )

    space_ids = [f"bp_space_{index}" for index in range(1, len(faces) + 1)]
    edge_owners: dict[
        tuple[tuple[int, int], tuple[int, int]],
        list[int],
    ] = {}
    for face_index, face in enumerate(faces):
        for point_index, start in enumerate(face.points):
            end = face.points[(point_index + 1) % len(face.points)]
            edge_owners.setdefault(_edge_key(start, end), []).append(
                face_index
            )

    spaces = []
    wall_count = 0
    opening_count = 0
    for face_index, face in enumerate(faces):
        space_id = space_ids[face_index]
        source_walls = [
            _source_wall_for_edge(
                document,
                start,
                face.points[(point_index + 1) % len(face.points)],
            )
            for point_index, start in enumerate(face.points)
        ]
        space_height = max(wall.height_mm for wall in source_walls)
        # Keep one raw inner-face vertex per BP host segment for traceable wall
        # metadata.  The analytical floor loop may remove collapsed/redundant
        # vertices without changing that one-to-one wall mapping.
        net_floor_points = _net_floor_points(face, source_walls)
        analytical_floor_points = _clean_polygon_points(net_floor_points)
        segments = []
        for point_index, start in enumerate(face.points):
            end = face.points[(point_index + 1) % len(face.points)]
            source_wall = source_walls[point_index]
            owners = edge_owners[_edge_key(start, end)]
            adjacent_index = next(
                (index for index in owners if index != face_index),
                None,
            )
            openings = _opening_data_for_edge(
                document,
                source_wall,
                start,
                end,
                space_id=space_id,
                segment_index=point_index + 1,
                space_height_mm=space_height,
                glazing_plane_offset_mm=max(
                    0.0,
                    -_wall_offsets_on_face_edge(source_wall, start, end)[0],
                ),
            )
            opening_count += len(openings)
            segments.append({
                "id": f"{space_id}_wall_{point_index + 1}",
                "name": "BP边界墙",
                "start": _point_data(start),
                "end": _point_data(end),
                "boundary_type": (
                    "interior" if adjacent_index is not None else "exterior"
                ),
                "thickness_mm": float(source_wall.width_mm),
                "u_value": None,
                "adjacent_space_id": (
                    space_ids[adjacent_index]
                    if adjacent_index is not None
                    else None
                ),
                "openings": openings,
                "metadata": {
                    "source": "building_plan_editor",
                    "source_bp_wall_id": source_wall.id,
                    "source_bp_axis": source_wall.axis,
                    "analysis_inner_start": _point_data(
                        net_floor_points[point_index]
                    ),
                    "analysis_inner_end": _point_data(
                        net_floor_points[(point_index + 1) % len(net_floor_points)]
                    ),
                    "analysis_length_mm": net_floor_points[
                        point_index
                    ].distance_to(net_floor_points[
                        (point_index + 1) % len(net_floor_points)
                    ]),
                },
            })
        wall_count += len(segments)
        spaces.append({
            "id": space_id,
            "name": f"房间{face_index + 1}",
            "height_mm": float(space_height),
            "floor_elevation_mm": 0.0,
            "roof_exposed": True,
            "floor_exposed": True,
            "metadata": {
                "source": "building_plan_editor",
                "source_face_area_mm2": face.area_mm2,
                "boundary_basis": "BP墙体轴线拓扑",
                "analysis_boundary_basis": "BP实体墙室内侧净边界",
                "source_face_index": face_index,
                "source_face_signature": _canonical_face_signature(face.points),
                "axis_floor_area_mm2": face.area_mm2,
                "boundary_conditions_explicit": False,
            },
            # Empty dictionaries intentionally invoke RoomLight v3.3 defaults.
            "material": {},
            "thermal": {},
            "shading": {},
            "exterior_barriers": _barriers_for_space(
                document.railings,
                space_id,
            ),
            "analysis_floor_loops": [{
                "id": f"{space_id}_net_floor",
                "kind": "outer",
                "metadata": {
                    "source": "building_plan_editor",
                    "basis": "physical_inner_wall_faces",
                },
                "points": [
                    _point_data(point) for point in analytical_floor_points
                ],
            }],
            "boundary_loops": [{
                "id": f"{space_id}_outer_loop",
                "name": "外边界",
                "kind": "outer",
                "metadata": {
                    "source": "building_plan_editor",
                },
                "segments": segments,
            }],
        })

    default_height = max(space["height_mm"] for space in spaces)
    saved_at = datetime.datetime.now().isoformat(timespec="seconds")
    data = {
        "file_version": ROOMLIGHT_FILE_VERSION,
        "project_kind": "building",
        "saved_at": saved_at,
        "active_space_id": space_ids[0],
        "building": {
            "id": "bp_export_building",
            "name": project_name,
            "north_angle_deg": 0.0,
            "location": {
                "latitude": 28.59,
                "longitude": 112.33,
                "timezone": 8,
                "orientation_deg": 0.0,
            },
            "metadata": {
                "source": "building_plan_editor_prototype_v0.1.0",
                "exported_at": saved_at,
                "selected_space_ids": space_ids,
                "source_counts": {
                    "walls": len(document.walls),
                    "windows": len(document.windows),
                    "railings": len(document.railings),
                    "lines": len(document.lines),
                    "dimensions": len(document.dimensions),
                },
                "auxiliary_geometry_not_calculated": {
                    "lines": [
                        {
                            "start": _point_data(line.start),
                            "end": _point_data(line.end),
                        }
                        for line in document.lines
                    ],
                    "dimensions": [
                        {
                            "points": [
                                _point_data(point)
                                for point in dimension.points
                            ],
                            "offset_mm": dimension.offset_mm,
                        }
                        for dimension in document.dimensions
                    ],
                },
                "conversion_note": (
                    "BP墙轴线保留为墙/窗宿主几何，实体墙室内侧另生成净空间边界；"
                    "共享边界转换为内墙；窗体挂接到原始墙轴并按墙轴位置确定玻璃面；"
                    "栏杆按材料估算有效透光率并作为"
                    "采光射线屏障。气象与构造参数使用RoomLight默认值，"
                    "打开工程后应按项目所在地复核。"
                ),
            },
            "storeys": [{
                "id": "bp_storey_1",
                "name": "首层",
                "elevation_mm": 0.0,
                "default_height_mm": float(default_height),
                "metadata": {
                    "source": "building_plan_editor",
                },
                "spaces": spaces,
            }],
        },
        "weather": None,
    }
    summary = RlprojExportSummary(
        path=Path(),
        spaces=len(spaces),
        walls=wall_count,
        windows=opening_count,
        railings=len(document.railings),
    )
    return data, summary


def export_rlproj(
    path: str | Path,
    document: DraftDocument,
    *,
    project_name: str,
) -> RlprojExportSummary:
    target = Path(path)
    if target.suffix.lower() != ".rlproj":
        target = target.with_suffix(".rlproj")
    data, summary = build_rlproj_data(
        document,
        project_name=project_name,
    )
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return RlprojExportSummary(
        path=target,
        spaces=summary.spaces,
        walls=summary.walls,
        windows=summary.windows,
        railings=summary.railings,
    )



