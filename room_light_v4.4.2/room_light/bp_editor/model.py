"""Pure data model for the standalone building-plan drafting prototype.

All world coordinates and dimensions use millimetres.  The module has no Qt
dependency so geometry, persistence and future RoomLight conversion can be
tested independently from the user interface.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import uuid
from typing import Iterable, Optional


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)

    def moved(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


@dataclass
class Wall:
    start: Point
    end: Point
    height_mm: float = 3000.0
    width_mm: float = 200.0
    axis: str = "center"
    id: str = field(default_factory=lambda: _id("wall"))

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def direction(self) -> tuple[float, float]:
        length = self.length_mm
        if length <= 1e-9:
            return 0.0, 0.0
        return (
            (self.end.x - self.start.x) / length,
            (self.end.y - self.start.y) / length,
        )

    @property
    def normal(self) -> tuple[float, float]:
        dx, dy = self.direction
        return -dy, dx

    def point_at(self, distance_mm: float) -> Point:
        dx, dy = self.direction
        return Point(
            self.start.x + dx * distance_mm,
            self.start.y + dy * distance_mm,
        )

    def project(self, point: Point) -> tuple[Point, float, float]:
        """Return closest point, distance along wall and perpendicular distance."""
        dx, dy = self.direction
        if self.length_mm <= 1e-9:
            return self.start, 0.0, point.distance_to(self.start)
        along = (
            (point.x - self.start.x) * dx
            + (point.y - self.start.y) * dy
        )
        along = max(0.0, min(self.length_mm, along))
        projected = self.point_at(along)
        return projected, along, point.distance_to(projected)


@dataclass
class Window:
    wall_id: str
    offset_mm: float
    width_mm: float = 1500.0
    sill_height_mm: float = 600.0
    height_mm: float = 1800.0
    id: str = field(default_factory=lambda: _id("window"))

    @property
    def end_offset_mm(self) -> float:
        return self.offset_mm + self.width_mm


@dataclass
class Railing:
    start: Point
    end: Point
    height_mm: float = 1100.0
    material: str = "金属栏杆"
    width_mm: float = 50.0
    id: str = field(default_factory=lambda: _id("railing"))

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


@dataclass
class Line:
    """A plain drafting line that does not enclose rooms or carry openings."""

    start: Point
    end: Point
    id: str = field(default_factory=lambda: _id("line"))

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


@dataclass
class DimensionChain:
    """One Tianzheng-style continuous point-by-point dimension chain."""

    points: list[Point]
    offset_mm: float = 500.0
    id: str = field(default_factory=lambda: _id("dimension"))


@dataclass
class RoomFace:
    """One automatically recognised closed face; no user-visible room name."""

    points: list[Point]
    area_mm2: float


@dataclass
class DraftDocument:
    walls: list[Wall] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    railings: list[Railing] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    dimensions: list[DimensionChain] = field(default_factory=list)
    grid_mm: float = 100.0
    snap_mm: float = 20.0
    format_version: str = "0.1.0"

    def wall_by_id(self, wall_id: str) -> Optional[Wall]:
        return next((wall for wall in self.walls if wall.id == wall_id), None)

    def dimension_by_id(
        self,
        dimension_id: str,
    ) -> Optional[DimensionChain]:
        return next(
            (
                dimension
                for dimension in self.dimensions
                if dimension.id == dimension_id
            ),
            None,
        )

    def add_wall(
        self,
        start: Point,
        end: Point,
        *,
        height_mm: float,
        width_mm: float,
        axis: str,
    ) -> Wall:
        wall = Wall(
            start=start,
            end=end,
            height_mm=height_mm,
            width_mm=width_mm,
            axis=axis,
        )
        if wall.length_mm <= 1.0:
            raise ValueError("墙体长度必须大于1 mm。")
        self.walls.append(wall)
        return wall

    def add_window(
        self,
        wall_id: str,
        offset_mm: float,
        *,
        width_mm: float,
        sill_height_mm: float,
        height_mm: float,
    ) -> Window:
        wall = self.wall_by_id(wall_id)
        if wall is None:
            raise ValueError("窗体必须依附于现有墙体。")
        if width_mm <= 0.0:
            raise ValueError("窗宽必须大于0。")
        if offset_mm < 0.0 or offset_mm + width_mm > wall.length_mm + 1e-6:
            raise ValueError("窗体超出墙体范围。")
        for existing in self.windows:
            if existing.wall_id != wall_id:
                continue
            if (
                offset_mm < existing.end_offset_mm - 1e-6
                and offset_mm + width_mm > existing.offset_mm + 1e-6
            ):
                raise ValueError("窗体与同一墙上的已有窗体重叠。")
        window = Window(
            wall_id=wall_id,
            offset_mm=offset_mm,
            width_mm=width_mm,
            sill_height_mm=sill_height_mm,
            height_mm=height_mm,
        )
        self.windows.append(window)
        return window

    def add_railing(
        self,
        start: Point,
        end: Point,
        *,
        height_mm: float,
        material: str,
        width_mm: float,
    ) -> Railing:
        railing = Railing(
            start=start,
            end=end,
            height_mm=height_mm,
            material=material,
            width_mm=width_mm,
        )
        if railing.length_mm <= 1.0:
            raise ValueError("栏杆长度必须大于1 mm。")
        self.railings.append(railing)
        return railing

    def add_line(self, start: Point, end: Point) -> Line:
        line = Line(start=start, end=end)
        if line.length_mm <= 1.0:
            raise ValueError("直线长度必须大于1 mm。")
        self.lines.append(line)
        return line

    def add_dimension(
        self,
        points: Iterable[Point],
        *,
        offset_mm: float,
    ) -> DimensionChain:
        cleaned = unique_points(list(points), tolerance_mm=1.0)
        if len(cleaned) < 2:
            raise ValueError("逐点标注至少需要两个不同的标注点。")
        dimension = DimensionChain(
            points=cleaned,
            offset_mm=float(offset_mm),
        )
        self.dimensions.append(dimension)
        return dimension

    def split_walls_at_junctions(
        self,
        *,
        tolerance_mm: float = 1.0,
    ) -> dict[str, list[str]]:
        """Physically split wall records at T-junctions and crossings.

        Room recognition has always treated an intersection as a graph node,
        but editing also needs that topology to exist in the document itself:
        after splitting, either side of a T-junction can be selected/deleted
        independently.  A split that would pass through a hosted window is
        deliberately ignored so an opening is never torn between two hosts.
        """
        from .geometry import point_on_segment, segment_intersection

        tolerance = max(float(tolerance_mm), 0.1)
        originals = list(self.walls)
        cuts: dict[str, list[float]] = {
            wall.id: [0.0, wall.length_mm] for wall in originals
        }

        def add_cut(wall: Wall, point: Point) -> None:
            if not point_on_segment(
                point,
                wall.start,
                wall.end,
                tolerance,
            ):
                return
            _projected, along, distance = wall.project(point)
            if distance > tolerance:
                return
            if tolerance < along < wall.length_mm - tolerance:
                cuts[wall.id].append(along)

        for first_index, first in enumerate(originals):
            for second in originals[first_index + 1:]:
                add_cut(first, second.start)
                add_cut(first, second.end)
                add_cut(second, first.start)
                add_cut(second, first.end)
                intersection = segment_intersection(
                    first.start,
                    first.end,
                    second.start,
                    second.end,
                    tolerance,
                )
                if intersection is not None:
                    add_cut(first, intersection)
                    add_cut(second, intersection)

        new_walls: list[Wall] = []
        mapping: dict[str, list[str]] = {}
        for wall in originals:
            original_start = wall.start
            direction_x, direction_y = wall.direction
            hosted = [
                window
                for window in self.windows
                if window.wall_id == wall.id
            ]
            ordered: list[float] = []
            for distance in sorted(cuts[wall.id]):
                if ordered and abs(distance - ordered[-1]) <= tolerance:
                    continue
                # Do not split through a window opening.  Splits exactly at an
                # opening edge remain valid and preserve its physical position.
                if any(
                    window.offset_mm + tolerance
                    < distance
                    < window.end_offset_mm - tolerance
                    for window in hosted
                ):
                    continue
                ordered.append(distance)
            if len(ordered) <= 2:
                new_walls.append(wall)
                mapping[wall.id] = [wall.id]
                continue

            old_id = wall.id
            pieces: list[tuple[float, float, Wall]] = []
            for index, (start_distance, end_distance) in enumerate(
                zip(ordered, ordered[1:])
            ):
                if end_distance - start_distance <= tolerance:
                    continue
                start = Point(
                    original_start.x + direction_x * start_distance,
                    original_start.y + direction_y * start_distance,
                )
                end = Point(
                    original_start.x + direction_x * end_distance,
                    original_start.y + direction_y * end_distance,
                )
                if index == 0:
                    piece = wall
                    piece.start = start
                    piece.end = end
                else:
                    piece = Wall(
                        start=start,
                        end=end,
                        height_mm=wall.height_mm,
                        width_mm=wall.width_mm,
                        axis=wall.axis,
                    )
                pieces.append((start_distance, end_distance, piece))
                new_walls.append(piece)
            mapping[old_id] = [piece.id for _start, _end, piece in pieces]

            for window in hosted:
                window_start = window.offset_mm
                window_end = window.end_offset_mm
                host_piece = next(
                    (
                        (start_distance, piece)
                        for start_distance, end_distance, piece in pieces
                        if window_start >= start_distance - tolerance
                        and window_end <= end_distance + tolerance
                    ),
                    None,
                )
                if host_piece is None:
                    # This should only be reachable for malformed imported
                    # data; keep the original first host rather than losing it.
                    continue
                start_distance, piece = host_piece
                window.wall_id = piece.id
                window.offset_mm = max(0.0, window_start - start_distance)

        self.walls = new_walls
        return mapping

    @staticmethod
    def _wall_offsets_for_axis(wall: Wall) -> tuple[float, float]:
        if wall.axis == "left":
            return 0.0, wall.width_mm
        if wall.axis == "right":
            return -wall.width_mm, 0.0
        return -wall.width_mm / 2.0, wall.width_mm / 2.0

    @classmethod
    def _walls_have_same_physical_strip(
        cls,
        first: Wall,
        second: Wall,
        *,
        tolerance_mm: float,
    ) -> bool:
        if (
            abs(first.width_mm - second.width_mm) > tolerance_mm
            or abs(first.height_mm - second.height_mm) > tolerance_mm
        ):
            return False
        first_x, first_y = first.direction
        second_x, second_y = second.direction
        if abs(first_x * second_y - first_y * second_x) > 1e-6:
            return False

        # Compare the occupied side of both axes in the first wall's normal
        # direction.  A reversed "left" wall is physically on the opposite
        # side and must not be silently merged.
        alignment = first.normal[0] * second.normal[0] + first.normal[1] * second.normal[1]
        first_interval = sorted(cls._wall_offsets_for_axis(first))
        second_interval = sorted(
            offset * alignment
            for offset in cls._wall_offsets_for_axis(second)
        )
        return (
            abs(first_interval[0] - second_interval[0]) <= tolerance_mm
            and abs(first_interval[1] - second_interval[1]) <= tolerance_mm
        )

    def merge_collinear_walls(
        self,
        *,
        tolerance_mm: float = 1.0,
    ) -> dict[str, str]:
        """Merge straight wall pieces where the shared node has no branch."""
        tolerance = max(float(tolerance_mm), 0.1)
        result: dict[str, str] = {
            wall.id: wall.id for wall in self.walls
        }

        while True:
            nodes: list[tuple[Point, list[Wall]]] = []
            for wall in self.walls:
                for point in (wall.start, wall.end):
                    node = next(
                        (
                            item
                            for item in nodes
                            if item[0].distance_to(point) <= tolerance
                        ),
                        None,
                    )
                    if node is None:
                        nodes.append((point, [wall]))
                    elif all(existing.id != wall.id for existing in node[1]):
                        node[1].append(wall)

            candidate: Optional[tuple[Point, Wall, Wall]] = None
            for node, incident in nodes:
                # Degree 2 means there is no T/cross branch at this node.
                if len(incident) != 2:
                    continue
                first, second = incident
                if not self._walls_have_same_physical_strip(
                    first,
                    second,
                    tolerance_mm=tolerance,
                ):
                    continue
                first_far = (
                    first.end
                    if first.start.distance_to(node) <= tolerance
                    else first.start
                )
                second_far = (
                    second.end
                    if second.start.distance_to(node) <= tolerance
                    else second.start
                )
                first_vector = (
                    first_far.x - node.x,
                    first_far.y - node.y,
                )
                second_vector = (
                    second_far.x - node.x,
                    second_far.y - node.y,
                )
                first_length = math.hypot(*first_vector)
                second_length = math.hypot(*second_vector)
                if first_length <= tolerance or second_length <= tolerance:
                    continue
                direction_dot = (
                    first_vector[0] * second_vector[0]
                    + first_vector[1] * second_vector[1]
                ) / (first_length * second_length)
                if direction_dot > -0.999999:
                    continue
                candidate = node, first, second
                break
            if candidate is None:
                break

            node, first, second = candidate
            # Preserve the earlier wall record/ID for stable selections and
            # references, then orient the merged segment like that wall.
            if self.walls.index(second) < self.walls.index(first):
                first, second = second, first
            first_shared_is_start = first.start.distance_to(node) <= tolerance
            first_far = first.end if first_shared_is_start else first.start
            second_far = (
                second.end
                if second.start.distance_to(node) <= tolerance
                else second.start
            )

            hosted = [
                (window, self.wall_by_id(window.wall_id))
                for window in self.windows
                if window.wall_id in {first.id, second.id}
            ]
            world_openings = []
            for window, host in hosted:
                if host is None:
                    continue
                world_openings.append((
                    window,
                    host.point_at(window.offset_mm),
                    host.point_at(window.end_offset_mm),
                ))

            if first_shared_is_start:
                first.start = second_far
                first.end = first_far
            else:
                first.start = first_far
                first.end = second_far
            self.walls.remove(second)

            for window, opening_start, opening_end in world_openings:
                direction_x, direction_y = first.direction
                start_distance = (
                    (opening_start.x - first.start.x) * direction_x
                    + (opening_start.y - first.start.y) * direction_y
                )
                end_distance = (
                    (opening_end.x - first.start.x) * direction_x
                    + (opening_end.y - first.start.y) * direction_y
                )
                window.wall_id = first.id
                window.offset_mm = min(start_distance, end_distance)

            for source_id, target_id in list(result.items()):
                if target_id == second.id:
                    result[source_id] = first.id
            result[second.id] = first.id
            result[first.id] = first.id

        return result

    def normalise_wall_topology(
        self,
        *,
        tolerance_mm: float = 1.0,
    ) -> dict[str, list[str]]:
        """Split at real branches, then merge obsolete straight subdivisions."""
        split_mapping = self.split_walls_at_junctions(
            tolerance_mm=tolerance_mm,
        )
        merge_mapping = self.merge_collinear_walls(
            tolerance_mm=tolerance_mm,
        )
        return {
            original_id: list(dict.fromkeys(
                merge_mapping.get(piece_id, piece_id)
                for piece_id in piece_ids
            ))
            for original_id, piece_ids in split_mapping.items()
        }

    def resize_wall_endpoint(
        self,
        wall_id: str,
        endpoint: str,
        new_point: Point,
        *,
        tolerance_mm: float = 1.0,
    ) -> None:
        """Drag one wall endpoint while preserving hosted window positions."""
        wall = self.wall_by_id(wall_id)
        if wall is None:
            raise ValueError("所选墙体不存在。")
        if endpoint not in {"start", "end"}:
            raise ValueError("墙体端点必须是 start 或 end。")

        hosted = [
            window for window in self.windows if window.wall_id == wall_id
        ]
        old_start = wall.start
        old_end = wall.end
        direction_x, direction_y = wall.direction
        if hosted:
            anchor = old_start if endpoint == "start" else old_end
            dx = new_point.x - anchor.x
            dy = new_point.y - anchor.y
            perpendicular = abs(dx * (-direction_y) + dy * direction_x)
            if perpendicular > max(float(tolerance_mm), 1.0):
                raise ValueError(
                    "带窗墙体只能沿原轴线拉伸，避免窗体脱离墙体。"
                )

        candidate_start = new_point if endpoint == "start" else old_start
        candidate_end = new_point if endpoint == "end" else old_end
        candidate_length = candidate_start.distance_to(candidate_end)
        if candidate_length <= 1.0:
            raise ValueError("调整后的墙体长度必须大于 1 mm。")

        offset_shift = 0.0
        if endpoint == "start":
            offset_shift = (
                (new_point.x - old_start.x) * direction_x
                + (new_point.y - old_start.y) * direction_y
            )
        replacements = {
            window.id: window.offset_mm - offset_shift
            for window in hosted
        }
        for window in hosted:
            offset = replacements[window.id]
            if (
                offset < -1e-6
                or offset + window.width_mm > candidate_length + 1e-6
            ):
                raise ValueError("调整后墙体过短，现有窗体会超出墙体范围。")

        wall.start = candidate_start
        wall.end = candidate_end
        for window in hosted:
            window.offset_mm = replacements[window.id]

    def update_wall_parameters(
        self,
        wall_id: str,
        *,
        height_mm: float,
        width_mm: float,
        axis: str,
    ) -> Wall:
        wall = self.wall_by_id(wall_id)
        if wall is None:
            raise ValueError("所选墙体不存在。")
        if height_mm <= 0.0 or width_mm <= 0.0:
            raise ValueError("墙高和墙宽必须大于 0。")
        if axis not in {"center", "left", "right"}:
            raise ValueError("未知的墙体轴线位置。")
        wall.height_mm = float(height_mm)
        wall.width_mm = float(width_mm)
        wall.axis = axis
        return wall

    def update_window_parameters(
        self,
        window_id: str,
        *,
        sill_height_mm: float,
        height_mm: float,
        width_mm: float,
    ) -> Window:
        window = next(
            (
                item
                for item in self.windows
                if item.id == window_id
            ),
            None,
        )
        if window is None:
            raise ValueError("所选窗体不存在。")
        wall = self.wall_by_id(window.wall_id)
        if wall is None:
            raise ValueError("窗体宿主墙不存在。")
        if sill_height_mm < 0.0 or height_mm <= 0.0 or width_mm <= 0.0:
            raise ValueError("窗台高不能为负，窗高和窗宽必须大于 0。")
        if window.offset_mm + width_mm > wall.length_mm + 1e-6:
            raise ValueError("修改后的窗宽超出宿主墙范围。")
        for existing in self.windows:
            if existing.id == window.id or existing.wall_id != wall.id:
                continue
            if (
                window.offset_mm < existing.end_offset_mm - 1e-6
                and window.offset_mm + width_mm
                > existing.offset_mm + 1e-6
            ):
                raise ValueError("修改后的窗体与同墙已有窗体重叠。")
        window.sill_height_mm = float(sill_height_mm)
        window.height_mm = float(height_mm)
        window.width_mm = float(width_mm)
        return window

    def resize_window_endpoint(
        self,
        window_id: str,
        endpoint: str,
        new_offset_mm: float,
        *,
        minimum_width_mm: float = 1.0,
    ) -> Window:
        """Move one hosted-window endpoint along its wall axis.

        ``new_offset_mm`` is measured from the host wall start.  The opposite
        endpoint remains fixed, so dragging either grip changes only the
        window width and position, never its sill height or window height.
        """
        window = next(
            (item for item in self.windows if item.id == window_id),
            None,
        )
        if window is None:
            raise ValueError("所选窗体不存在。")
        wall = self.wall_by_id(window.wall_id)
        if wall is None:
            raise ValueError("窗体宿主墙不存在。")
        if endpoint not in {"start", "end"}:
            raise ValueError("窗体端点必须是 start 或 end。")

        fixed_offset = (
            window.end_offset_mm
            if endpoint == "start"
            else window.offset_mm
        )
        candidate_start = (
            float(new_offset_mm)
            if endpoint == "start"
            else fixed_offset
        )
        candidate_end = (
            fixed_offset
            if endpoint == "start"
            else float(new_offset_mm)
        )
        candidate_width = candidate_end - candidate_start
        if candidate_width < max(float(minimum_width_mm), 1.0) - 1e-6:
            raise ValueError("调整后的窗宽必须大于等于 1 mm。")
        if (
            candidate_start < -1e-6
            or candidate_end > wall.length_mm + 1e-6
        ):
            raise ValueError("调整后的窗体超出宿主墙范围。")

        for existing in self.windows:
            if existing.id == window.id or existing.wall_id != wall.id:
                continue
            if (
                candidate_start < existing.end_offset_mm - 1e-6
                and candidate_end > existing.offset_mm + 1e-6
            ):
                raise ValueError("调整后的窗体与同墙已有窗体重叠。")

        window.offset_mm = max(0.0, candidate_start)
        window.width_mm = candidate_width
        return window

    def update_railing_parameters(
        self,
        railing_id: str,
        *,
        height_mm: float,
        width_mm: float,
        material: str,
    ) -> Railing:
        railing = next(
            (
                item
                for item in self.railings
                if item.id == railing_id
            ),
            None,
        )
        if railing is None:
            raise ValueError("所选栏杆不存在。")
        if height_mm <= 0.0 or width_mm <= 0.0:
            raise ValueError("栏杆高度和绘图宽度必须大于 0。")
        if not str(material).strip():
            raise ValueError("栏杆材料不能为空。")
        railing.height_mm = float(height_mm)
        railing.width_mm = float(width_mm)
        railing.material = str(material)
        return railing

    def remove_entity(self, entity_id: str) -> bool:
        for collection in (
            self.windows,
            self.railings,
            self.lines,
            self.dimensions,
        ):
            for index, entity in enumerate(collection):
                if entity.id == entity_id:
                    collection.pop(index)
                    return True
        for index, wall in enumerate(self.walls):
            if wall.id != entity_id:
                continue
            self.walls.pop(index)
            self.windows = [
                window
                for window in self.windows
                if window.wall_id != entity_id
            ]
            return True
        return False

    def entity_by_id(self, entity_id: str):
        for collection in (
            self.walls,
            self.windows,
            self.railings,
            self.lines,
            self.dimensions,
        ):
            for entity in collection:
                if entity.id == entity_id:
                    return entity
        return None

    def nearest_wall(
        self,
        point: Point,
        maximum_distance_mm: float,
    ) -> Optional[tuple[Wall, Point, float, float]]:
        best = None
        for wall in self.walls:
            projected, along, distance = wall.project(point)
            if distance > maximum_distance_mm:
                continue
            if best is None or distance < best[3]:
                best = wall, projected, along, distance
        return best

    @staticmethod
    def _vector_on_wall(
        wall: Wall,
        dx: float,
        dy: float,
        *,
        tolerance_mm: float = 1.0,
    ) -> float:
        ux, uy = wall.direction
        along = dx * ux + dy * uy
        perpendicular = abs(dx * (-uy) + dy * ux)
        if perpendicular > tolerance_mm:
            raise ValueError("单独移动/复制窗体时，位移方向必须沿宿主墙轴线。")
        return along

    def _validate_window_offsets(
        self,
        replacements: dict[str, float],
    ) -> None:
        for window in self.windows:
            offset = replacements.get(window.id, window.offset_mm)
            wall = self.wall_by_id(window.wall_id)
            if wall is None:
                raise ValueError("窗体宿主墙不存在。")
            if offset < -1e-6 or offset + window.width_mm > wall.length_mm + 1e-6:
                raise ValueError("移动后的窗体超出宿主墙范围。")
        for wall in self.walls:
            intervals = sorted(
                (
                    replacements.get(window.id, window.offset_mm),
                    replacements.get(window.id, window.offset_mm)
                    + window.width_mm,
                    window.id,
                )
                for window in self.windows
                if window.wall_id == wall.id
            )
            for first, second in zip(intervals, intervals[1:]):
                if first[1] > second[0] + 1e-6:
                    raise ValueError("移动后的窗体与同墙已有窗体重叠。")

    def move_entities(
        self,
        entity_ids: Iterable[str],
        dx: float,
        dy: float,
    ) -> set[str]:
        """Move selected entities while keeping hosted windows attached."""
        selected = set(entity_ids)
        selected_wall_ids = {
            wall.id for wall in self.walls if wall.id in selected
        }
        window_offsets: dict[str, float] = {}
        for window in self.windows:
            if window.id not in selected or window.wall_id in selected_wall_ids:
                continue
            wall = self.wall_by_id(window.wall_id)
            if wall is None:
                raise ValueError("窗体宿主墙不存在。")
            shift = self._vector_on_wall(wall, dx, dy)
            window_offsets[window.id] = window.offset_mm + shift
        self._validate_window_offsets(window_offsets)

        moved: set[str] = set()
        for wall in self.walls:
            if wall.id in selected_wall_ids:
                wall.start = wall.start.moved(dx, dy)
                wall.end = wall.end.moved(dx, dy)
                moved.add(wall.id)
        for window in self.windows:
            if window.id in window_offsets:
                window.offset_mm = window_offsets[window.id]
                moved.add(window.id)
            elif window.id in selected and window.wall_id in selected_wall_ids:
                moved.add(window.id)
        for entity in (*self.railings, *self.lines):
            if entity.id not in selected:
                continue
            entity.start = entity.start.moved(dx, dy)
            entity.end = entity.end.moved(dx, dy)
            moved.add(entity.id)
        for dimension in self.dimensions:
            if dimension.id not in selected:
                continue
            dimension.points = [
                point.moved(dx, dy) for point in dimension.points
            ]
            moved.add(dimension.id)
        return moved

    def copy_entities(
        self,
        entity_ids: Iterable[str],
        dx: float,
        dy: float,
    ) -> set[str]:
        """Copy entities; a copied wall brings all of its hosted windows."""
        selected = set(entity_ids)
        selected_wall_ids = {
            wall.id for wall in self.walls if wall.id in selected
        }
        created: set[str] = set()
        wall_map: dict[str, Wall] = {}
        for wall in list(self.walls):
            if wall.id not in selected_wall_ids:
                continue
            clone = self.add_wall(
                wall.start.moved(dx, dy),
                wall.end.moved(dx, dy),
                height_mm=wall.height_mm,
                width_mm=wall.width_mm,
                axis=wall.axis,
            )
            wall_map[wall.id] = clone
            created.add(clone.id)
        for window in list(self.windows):
            if window.wall_id not in wall_map:
                continue
            clone = self.add_window(
                wall_map[window.wall_id].id,
                window.offset_mm,
                width_mm=window.width_mm,
                sill_height_mm=window.sill_height_mm,
                height_mm=window.height_mm,
            )
            created.add(clone.id)
        for window in list(self.windows):
            if window.id not in selected or window.wall_id in selected_wall_ids:
                continue
            wall = self.wall_by_id(window.wall_id)
            if wall is None:
                raise ValueError("窗体宿主墙不存在。")
            offset = window.offset_mm + self._vector_on_wall(wall, dx, dy)
            clone = self.add_window(
                wall.id,
                offset,
                width_mm=window.width_mm,
                sill_height_mm=window.sill_height_mm,
                height_mm=window.height_mm,
            )
            created.add(clone.id)
        for railing in list(self.railings):
            if railing.id not in selected:
                continue
            clone = self.add_railing(
                railing.start.moved(dx, dy),
                railing.end.moved(dx, dy),
                height_mm=railing.height_mm,
                material=railing.material,
                width_mm=railing.width_mm,
            )
            created.add(clone.id)
        for line in list(self.lines):
            if line.id not in selected:
                continue
            clone = self.add_line(
                line.start.moved(dx, dy),
                line.end.moved(dx, dy),
            )
            created.add(clone.id)
        for dimension in list(self.dimensions):
            if dimension.id not in selected:
                continue
            clone = self.add_dimension(
                (point.moved(dx, dy) for point in dimension.points),
                offset_mm=dimension.offset_mm,
            )
            created.add(clone.id)
        return created

    @staticmethod
    def mirror_point(point: Point, axis_start: Point, axis_end: Point) -> Point:
        length = axis_start.distance_to(axis_end)
        if length <= 1.0:
            raise ValueError("镜像轴两点不能重合。")
        ux = (axis_end.x - axis_start.x) / length
        uy = (axis_end.y - axis_start.y) / length
        projection = (
            (point.x - axis_start.x) * ux
            + (point.y - axis_start.y) * uy
        )
        foot = Point(
            axis_start.x + projection * ux,
            axis_start.y + projection * uy,
        )
        return Point(2.0 * foot.x - point.x, 2.0 * foot.y - point.y)

    def mirror_entities(
        self,
        entity_ids: Iterable[str],
        axis_start: Point,
        axis_end: Point,
    ) -> set[str]:
        """Create mirrored copies and retain the source entities."""
        selected = set(entity_ids)
        selected_wall_ids = {
            wall.id for wall in self.walls if wall.id in selected
        }
        created: set[str] = set()
        wall_map: dict[str, Wall] = {}
        for wall in list(self.walls):
            if wall.id not in selected_wall_ids:
                continue
            clone = self.add_wall(
                self.mirror_point(wall.start, axis_start, axis_end),
                self.mirror_point(wall.end, axis_start, axis_end),
                height_mm=wall.height_mm,
                width_mm=wall.width_mm,
                axis=wall.axis,
            )
            wall_map[wall.id] = clone
            created.add(clone.id)
        for window in list(self.windows):
            if window.wall_id not in wall_map:
                continue
            clone = self.add_window(
                wall_map[window.wall_id].id,
                window.offset_mm,
                width_mm=window.width_mm,
                sill_height_mm=window.sill_height_mm,
                height_mm=window.height_mm,
            )
            created.add(clone.id)
        for window in list(self.windows):
            if window.id not in selected or window.wall_id in selected_wall_ids:
                continue
            source_wall = self.wall_by_id(window.wall_id)
            if source_wall is None:
                raise ValueError("窗体宿主墙不存在。")
            reflected_start = self.mirror_point(
                source_wall.point_at(window.offset_mm),
                axis_start,
                axis_end,
            )
            reflected_end = self.mirror_point(
                source_wall.point_at(window.end_offset_mm),
                axis_start,
                axis_end,
            )
            reflected_center = Point(
                (reflected_start.x + reflected_end.x) / 2.0,
                (reflected_start.y + reflected_end.y) / 2.0,
            )
            nearest = self.nearest_wall(reflected_center, 5.0)
            if nearest is None:
                raise ValueError("镜像窗体的目标位置没有可依附的墙体。")
            target_wall, _point, center_along, _distance = nearest
            tx, ty = target_wall.direction
            rx = reflected_end.x - reflected_start.x
            ry = reflected_end.y - reflected_start.y
            reflected_length = max(math.hypot(rx, ry), 1e-9)
            alignment = abs((rx * tx + ry * ty) / reflected_length)
            if alignment < 0.999:
                raise ValueError("镜像窗体与目标墙轴线方向不一致。")
            clone = self.add_window(
                target_wall.id,
                center_along - window.width_mm / 2.0,
                width_mm=window.width_mm,
                sill_height_mm=window.sill_height_mm,
                height_mm=window.height_mm,
            )
            created.add(clone.id)
        for railing in list(self.railings):
            if railing.id not in selected:
                continue
            clone = self.add_railing(
                self.mirror_point(railing.start, axis_start, axis_end),
                self.mirror_point(railing.end, axis_start, axis_end),
                height_mm=railing.height_mm,
                material=railing.material,
                width_mm=railing.width_mm,
            )
            created.add(clone.id)
        for line in list(self.lines):
            if line.id not in selected:
                continue
            clone = self.add_line(
                self.mirror_point(line.start, axis_start, axis_end),
                self.mirror_point(line.end, axis_start, axis_end),
            )
            created.add(clone.id)
        for dimension in list(self.dimensions):
            if dimension.id not in selected:
                continue
            clone = self.add_dimension(
                (
                    self.mirror_point(point, axis_start, axis_end)
                    for point in dimension.points
                ),
                offset_mm=-dimension.offset_mm,
            )
            created.add(clone.id)
        return created

    def recognised_rooms(self) -> list[RoomFace]:
        from .geometry import polygonize_walls

        return polygonize_walls(self.walls, tolerance_mm=max(self.snap_mm, 1.0))

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "units": "mm",
            "grid_mm": self.grid_mm,
            "snap_mm": self.snap_mm,
            "walls": [
                {
                    **asdict(wall),
                    "start": asdict(wall.start),
                    "end": asdict(wall.end),
                }
                for wall in self.walls
            ],
            "windows": [asdict(window) for window in self.windows],
            "railings": [
                {
                    **asdict(railing),
                    "start": asdict(railing.start),
                    "end": asdict(railing.end),
                }
                for railing in self.railings
            ],
            "lines": [
                {
                    **asdict(line),
                    "start": asdict(line.start),
                    "end": asdict(line.end),
                }
                for line in self.lines
            ],
            "dimensions": [
                {
                    **asdict(dimension),
                    "points": [
                        asdict(point)
                        for point in dimension.points
                    ],
                }
                for dimension in self.dimensions
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftDocument":
        document = cls(
            grid_mm=float(data.get("grid_mm", 100.0)),
            snap_mm=float(data.get("snap_mm", 20.0)),
            format_version=str(data.get("format_version", "0.1.0")),
        )
        document.walls = [
            Wall(
                id=str(item.get("id") or _id("wall")),
                start=Point(**item["start"]),
                end=Point(**item["end"]),
                height_mm=float(item.get("height_mm", 3000.0)),
                width_mm=float(item.get("width_mm", 200.0)),
                axis=str(item.get("axis", "center")),
            )
            for item in data.get("walls", [])
        ]
        document.windows = [
            Window(
                id=str(item.get("id") or _id("window")),
                wall_id=str(item["wall_id"]),
                offset_mm=float(item.get("offset_mm", 0.0)),
                width_mm=float(item.get("width_mm", 1500.0)),
                sill_height_mm=float(item.get("sill_height_mm", 600.0)),
                height_mm=float(item.get("height_mm", 1800.0)),
            )
            for item in data.get("windows", [])
        ]
        document.railings = [
            Railing(
                id=str(item.get("id") or _id("railing")),
                start=Point(**item["start"]),
                end=Point(**item["end"]),
                height_mm=float(item.get("height_mm", 1100.0)),
                material=str(item.get("material", "金属栏杆")),
                width_mm=float(item.get("width_mm", 50.0)),
            )
            for item in data.get("railings", [])
        ]
        document.lines = [
            Line(
                id=str(item.get("id") or _id("line")),
                start=Point(**item["start"]),
                end=Point(**item["end"]),
            )
            for item in data.get("lines", [])
        ]
        document.dimensions = [
            DimensionChain(
                id=str(item.get("id") or _id("dimension")),
                points=[
                    Point(**point)
                    for point in item.get("points", [])
                ],
                offset_mm=float(item.get("offset_mm", 500.0)),
            )
            for item in data.get("dimensions", [])
            if len(item.get("points", [])) >= 2
        ]
        valid_wall_ids = {wall.id for wall in document.walls}
        if any(window.wall_id not in valid_wall_ids for window in document.windows):
            raise ValueError("文件中存在找不到宿主墙的窗体。")
        return document

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "DraftDocument":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def unique_points(items: Iterable[Point], tolerance_mm: float) -> list[Point]:
    result: list[Point] = []
    for point in items:
        if any(point.distance_to(existing) <= tolerance_mm for existing in result):
            continue
        result.append(point)
    return result
