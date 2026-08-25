"""Read legacy and v3 RoomLight ``.rlproj`` files into a BP document."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .model import DraftDocument, Point, Railing, Wall, Window


@dataclass
class RlprojImportResult:
    document: DraftDocument
    project_name: str
    storey_name: str
    notes: str


def _point(value: Any) -> Point:
    if isinstance(value, dict):
        return Point(float(value.get("x", 0.0)), float(value.get("y", 0.0)))
    return Point(float(value[0]), float(value[1]))


def _point_key(point: Point, tolerance_mm: float = 1.0) -> tuple[int, int]:
    return round(point.x / tolerance_mm), round(point.y / tolerance_mm)


def _segment_key(
    start: Point,
    end: Point,
) -> tuple[tuple[int, int], tuple[int, int]]:
    first, second = _point_key(start), _point_key(end)
    return tuple(sorted((first, second)))


def _append_unique_window(
    document: DraftDocument,
    wall: Wall,
    opening: dict,
    *,
    reversed_direction: bool,
) -> None:
    if str(opening.get("kind", "window")).lower() != "window":
        return
    width = float(opening.get("width_mm", 1500.0))
    source_offset = float(opening.get("offset_mm", 0.0))
    offset = (
        wall.length_mm - source_offset - width
        if reversed_direction
        else source_offset
    )
    offset = max(0.0, min(max(wall.length_mm - width, 0.0), offset))
    if width <= 0.0 or width > wall.length_mm + 1e-6:
        return
    if any(
        item.wall_id == wall.id
        and abs(item.offset_mm - offset) <= 1.0
        and abs(item.width_mm - width) <= 1.0
        for item in document.windows
    ):
        return
    try:
        document.add_window(
            wall.id,
            offset,
            width_mm=width,
            sill_height_mm=float(opening.get("sill_height_mm", 600.0)),
            height_mm=float(opening.get("height_mm", 1800.0)),
        )
    except ValueError:
        # Shared-space duplicate data can carry partially overlapping copies.
        # Keep the first valid physical opening instead of duplicating it.
        return


def _load_building(data: dict, source: Path) -> RlprojImportResult:
    building = data.get("building") or {}
    storeys = building.get("storeys") or []
    if not storeys:
        raise ValueError("该复杂空间工程没有楼层数据。")
    storey = storeys[0]
    document = DraftDocument()
    wall_by_key: dict[tuple, Wall] = {}
    used_ids: set[str] = set()

    def unique_id(candidate: str, prefix: str) -> str:
        value = candidate or f"{prefix}_{len(used_ids)}"
        if value not in used_ids:
            used_ids.add(value)
            return value
        index = 2
        while f"{value}_{index}" in used_ids:
            index += 1
        value = f"{value}_{index}"
        used_ids.add(value)
        return value

    for space in storey.get("spaces", []):
        space_height = float(
            space.get("height_mm", storey.get("default_height_mm", 3000.0))
        )
        for loop in space.get("boundary_loops", []):
            for segment in loop.get("segments", []):
                start = _point(segment.get("start", [0.0, 0.0]))
                end = _point(segment.get("end", [0.0, 0.0]))
                if start.distance_to(end) <= 1.0:
                    continue
                key = _segment_key(start, end)
                wall = wall_by_key.get(key)
                reversed_direction = False
                if wall is None:
                    wall = Wall(
                        id=unique_id(str(segment.get("id") or ""), "rlproj_wall"),
                        start=start,
                        end=end,
                        height_mm=space_height,
                        width_mm=float(segment.get("thickness_mm", 200.0)),
                        axis="center",
                    )
                    document.walls.append(wall)
                    wall_by_key[key] = wall
                else:
                    reversed_direction = (
                        wall.start.distance_to(end) <= 1.0
                        and wall.end.distance_to(start) <= 1.0
                    )
                    wall.height_mm = max(wall.height_mm, space_height)
                    wall.width_mm = max(
                        wall.width_mm,
                        float(segment.get("thickness_mm", wall.width_mm)),
                    )
                for opening in segment.get("openings", []):
                    _append_unique_window(
                        document,
                        wall,
                        opening,
                        reversed_direction=reversed_direction,
                    )

        for barrier in space.get("exterior_barriers", []):
            kind = str(barrier.get("kind", "")).lower()
            metadata = barrier.get("metadata") or {}
            source_colour = str(metadata.get("source_colour", "")).lower()
            if (
                kind not in {"railing", "guardrail", "balustrade", "balcony_railing"}
                and source_colour not in {"pink", "magenta", "粉色"}
            ):
                continue
            start = _point(barrier.get("start", [0.0, 0.0]))
            end = _point(barrier.get("end", [0.0, 0.0]))
            key = _segment_key(start, end)
            if any(_segment_key(item.start, item.end) == key for item in document.railings):
                continue
            document.railings.append(Railing(
                id=unique_id(
                    str(barrier.get("id") or ""),
                    "rlproj_railing",
                ),
                start=start,
                end=end,
                height_mm=max(
                    100.0,
                    float(barrier.get("top_height_mm", 1100.0))
                    - float(barrier.get("bottom_height_mm", 0.0)),
                ),
                material=str(metadata.get("material", "工程栏杆")),
                width_mm=float(metadata.get("drawing_width_mm", 50.0)),
            ))

    if not document.walls:
        raise ValueError("工程中没有可导入的墙体边界。")
    project_name = str(building.get("name") or source.stem)
    storey_name = str(storey.get("name") or "首层")
    return RlprojImportResult(
        document=document,
        project_name=project_name,
        storey_name=storey_name,
        notes=(
            f"已导入复杂空间工程第一楼层“{storey_name}”；"
            f"共享墙已合并，墙体{len(document.walls)}段、"
            f"窗体{len(document.windows)}个、栏杆{len(document.railings)}段。"
        ),
    )


def _load_legacy(data: dict, source: Path) -> RlprojImportResult:
    room = data.get("room") or {}
    width = float(room.get("width", 4000.0))
    length = float(room.get("length", 6000.0))
    height = float(room.get("height", 3000.0))
    thermal = room.get("thermal") or {}
    thickness = float(thermal.get("wall_thickness_mm", 240.0))
    document = DraftDocument()
    points = [
        Point(0.0, 0.0),
        Point(width, 0.0),
        Point(width, length),
        Point(0.0, length),
    ]
    keys = ("south", "east", "north", "west")
    walls: dict[str, Wall] = {}
    for index, key in enumerate(keys):
        wall = Wall(
            id=f"legacy_wall_{key}",
            start=points[index],
            end=points[(index + 1) % 4],
            height_mm=height,
            width_mm=thickness,
            axis="center",
        )
        document.walls.append(wall)
        walls[key] = wall
    for item in room.get("windows", []):
        wall_key = str(item.get("wall", "south")).lower()
        wall = walls.get(wall_key)
        if wall is None:
            continue
        width_mm = float(item.get("width", 1500.0))
        offset = float(item.get("x", 300.0))
        if wall_key in {"east", "west"}:
            offset = wall.length_mm - offset - width_mm
        _append_unique_window(
            document,
            wall,
            {
                "kind": "window",
                "offset_mm": offset,
                "width_mm": width_mm,
                "sill_height_mm": float(item.get("y", 900.0)),
                "height_mm": float(item.get("height", 1500.0)),
            },
            reversed_direction=False,
        )
    return RlprojImportResult(
        document=document,
        project_name=source.stem,
        storey_name="旧版单房间",
        notes=(
            f"已导入旧版矩形工程：墙体4段、窗体{len(document.windows)}个。"
        ),
    )


def load_rlproj(path: str | Path) -> RlprojImportResult:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    return load_rlproj_data(data, source_name=source.name)


def load_rlproj_data(
    data: dict,
    *,
    source_name: str = "内存中的RoomLight工程.rlproj",
) -> RlprojImportResult:
    """Convert an in-memory RoomLight project without creating a temp file."""
    source = Path(source_name)
    if data.get("building") is not None:
        return _load_building(data, source)
    if data.get("room") is not None:
        return _load_legacy(data, source)
    raise ValueError("无法识别该 .rlproj：既没有 building，也没有 room 数据。")
