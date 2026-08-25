"""Interactive Tianzheng-inspired plan canvas for walls, windows and railings."""
from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QKeySequence,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget

from .geometry import segment_intersection
from .model import (
    DimensionChain,
    DraftDocument,
    Line,
    Point,
    Railing,
    Wall,
    Window,
)


WALL_FILL = QColor("#f1f3f5")
WALL_EDGE = QColor("#8b949e")
WALL_AXIS = QColor("#b0b7c0")
WINDOW_COLOR = QColor("#86c9f4")
WINDOW_EDGE = QColor("#4ba3d3")
RAILING_COLOR = QColor("#80868e")
LINE_COLOR = QColor("#475569")
DIMENSION_COLOR = QColor("#239b56")
SELECTION_COLOR = QColor("#0284c7")
ROOM_COLORS = (
    QColor(219, 234, 254, 86),
    QColor(220, 252, 231, 86),
    QColor(254, 240, 138, 70),
    QColor(243, 232, 255, 82),
)


class DraftCanvas(QWidget):
    status_changed = pyqtSignal(str)
    document_changed = pyqtSignal()
    room_count_changed = pyqtSignal(int)
    entity_edit_requested = pyqtSignal(str)
    selection_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(700, 520)
        self.document = DraftDocument()
        self.scale = 0.08
        self.origin = QPointF(100.0, 620.0)
        self.mode = "select"
        self.wall_params = {
            "height_mm": 3000.0,
            "width_mm": 200.0,
            "axis": "center",
        }
        self.window_params = {
            "sill_height_mm": 600.0,
            "height_mm": 1800.0,
            "width_mm": 1500.0,
        }
        self.railing_params = {
            "height_mm": 1100.0,
            "width_mm": 50.0,
            "material": "金属栏杆",
        }
        self.ortho = True
        self.object_snap_enabled = True
        self._last_point: Optional[Point] = None
        self._hover_world: Optional[Point] = None
        self._snap_world: Optional[Point] = None
        self._snap_kind: Optional[str] = None
        self._window_preview: Optional[tuple[Wall, float]] = None
        self._selected_id: Optional[str] = None
        self._selected_ids: set[str] = set()
        self._selection_start: Optional[QPointF] = None
        self._selection_current: Optional[QPointF] = None
        self._selection_additive = False
        self._dimension_points: list[Point] = []
        self._dimension_offset_preview = 500.0
        self._active_dimension_id: Optional[str] = None
        self._edit_entity_ids: set[str] = set()
        self._edit_base: Optional[Point] = None
        self._grip_wall_id: Optional[str] = None
        self._grip_endpoint: Optional[str] = None
        self._grip_preview: Optional[Point] = None
        self._grip_wall_origin: Optional[Point] = None
        self._grip_window_id: Optional[str] = None
        self._grip_window_endpoint: Optional[str] = None
        self._grip_window_preview_offset: Optional[float] = None
        self._grip_window_origin_offset: Optional[float] = None
        self._grip_has_direction = False
        self.view_mode = "plan"
        self._panning = False
        self._pan_last = QPointF()
        self._undo_stack: list[dict] = []
        self._rooms = []

    # ------------------------------------------------------------------
    # Document and view
    def set_document(self, document: DraftDocument, *, fit: bool = True) -> None:
        self.document = document
        self.document.normalise_wall_topology()
        self._undo_stack.clear()
        self._selected_id = None
        self._selected_ids.clear()
        self._notify_selection_changed()
        self.finish_command()
        self._refresh_rooms()
        if fit:
            self.fit_document()
        self.update()
        self.document_changed.emit()

    def set_view_mode(self, mode: str) -> None:
        if mode not in {"plan", "north", "south", "east", "west"}:
            raise ValueError(f"未知视图：{mode}")
        self.view_mode = mode
        self.finish_command(quiet=True)
        self._selected_ids.clear()
        self._selected_id = None
        self._notify_selection_changed()
        self.fit_document()
        labels = {
            "plan": "平面图",
            "north": "北立面",
            "south": "南立面",
            "east": "东立面",
            "west": "西立面",
        }
        self._say(f"已切换到{labels[mode]}。立面用于核对墙高、窗台高和窗高。")

    def _project_elevation_x(self, point: Point) -> float:
        if self.view_mode in {"north", "south"}:
            value = point.x
            return -value if self.view_mode == "north" else value
        value = point.y
        return -value if self.view_mode == "east" else value

    def fit_document(self) -> None:
        if self.view_mode == "plan":
            points = [
                point
                for wall in self.document.walls
                for point in (wall.start, wall.end)
            ] + [
                point
                for railing in self.document.railings
                for point in (railing.start, railing.end)
            ] + [
                point
                for line in self.document.lines
                for point in (line.start, line.end)
            ] + [
                point
                for dimension in self.document.dimensions
                for point in dimension.points
            ]
        else:
            points = [
                Point(horizontal, height)
                for wall in self.document.walls
                for horizontal in self._elevation_wall_horizontal_bounds(wall)
                for height in (0.0, wall.height_mm)
            ] + [
                Point(self._project_elevation_x(point), height)
                for railing in self.document.railings
                for point in (railing.start, railing.end)
                for height in (0.0, railing.height_mm)
            ]
        if not points:
            self.scale = 0.08
            self.origin = QPointF(100.0, max(200.0, self.height() - 100.0))
            self.update()
            return
        min_x = min(point.x for point in points)
        max_x = max(point.x for point in points)
        min_y = min(point.y for point in points)
        max_y = max(point.y for point in points)
        world_width = max(max_x - min_x, 1000.0)
        world_height = max(max_y - min_y, 1000.0)
        self.scale = min(
            max((self.width() - 140.0) / world_width, 0.005),
            max((self.height() - 140.0) / world_height, 0.005),
        )
        drawing_width = world_width * self.scale
        drawing_height = world_height * self.scale
        left = (self.width() - drawing_width) / 2.0
        top = (self.height() - drawing_height) / 2.0
        self.origin = QPointF(
            left - min_x * self.scale,
            top + max_y * self.scale,
        )
        self.update()

    def undo(self) -> None:
        if not self._undo_stack:
            self._say("没有可撤销的操作。")
            return
        self.document = DraftDocument.from_dict(self._undo_stack.pop())
        self._selected_id = None
        self._selected_ids.clear()
        self._notify_selection_changed()
        self._refresh_rooms()
        self.update()
        self.document_changed.emit()
        self._say("已撤销上一步。")

    def _push_undo(self) -> None:
        self._undo_stack.append(self.document.to_dict())
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _refresh_rooms(self) -> None:
        self._rooms = self.document.recognised_rooms()
        self.room_count_changed.emit(len(self._rooms))

    def _notify_selection_changed(self) -> None:
        self.selection_changed.emit(set(self._selected_ids))

    # ------------------------------------------------------------------
    # Commands
    def start_wall(self, params: dict) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self.wall_params = dict(params)
        self.mode = "wall"
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._say(
            "HZQT：指定墙体起点；连续点击绘制。命令框输入长度后回车，"
            "右键/ESC结束，F8切换正交。"
        )

    def start_window(self, params: dict) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self.window_params = dict(params)
        self.mode = "window"
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._say(
            "MC：移动鼠标选择最近墙体；蓝色预览会显示两侧净距。"
            "点击按当前位置插窗，或输入距墙起点的左边距后回车。"
        )

    def start_railing(self, params: dict) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self.railing_params = dict(params)
        self.mode = "railing"
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._say(
            "LG：指定栏杆起点；连续点击绘制。命令框可输入长度，"
            "右键/ESC结束。"
        )

    def start_line(self) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self.mode = "line"
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._say(
            "L：指定直线起点和终点；可连续绘制或输入长度，"
            "右键/Enter/空格结束。"
        )

    def _start_edit_command(self, operation: str) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self._edit_entity_ids = set(self._selected_ids)
        labels = {"move": "M移动", "copy": "CO复制", "mirror": "MI镜像"}
        if self._edit_entity_ids:
            self.mode = (
                "mirror_first"
                if operation == "mirror"
                else f"{operation}_base"
            )
            self._say(
                f"{labels[operation]}：已采用当前{len(self._edit_entity_ids)}个"
                f"选中图元；指定{'镜像轴第一点' if operation == 'mirror' else '基点'}。"
            )
        else:
            self.mode = f"{operation}_select"
            self._say(
                f"{labels[operation]}：点选或框选图元，"
                "Shift/Ctrl切选，Enter或空格确认选择。"
            )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def start_move(self) -> None:
        self._start_edit_command("move")

    def start_copy(self) -> None:
        self._start_edit_command("copy")

    def start_mirror(self) -> None:
        self._start_edit_command("mirror")

    def update_wall_params(self, params: dict) -> None:
        self.wall_params = dict(params)
        self.update()

    def update_window_params(self, params: dict) -> None:
        old_width = float(self.window_params.get("width_mm", 1500.0))
        self.window_params = dict(params)
        if self._window_preview is not None:
            wall, offset = self._window_preview
            width = float(self.window_params["width_mm"])
            center = offset + old_width / 2.0
            if width <= wall.length_mm:
                offset = max(
                    0.0,
                    min(wall.length_mm - width, center - width / 2.0),
                )
                self._window_preview = wall, offset
            else:
                self._window_preview = None
        self.update()

    def update_railing_params(self, params: dict) -> None:
        self.railing_params = dict(params)
        self.update()

    def update_existing_entity_parameters(
        self,
        entity_id: str,
        params: dict,
    ) -> Optional[str]:
        """Apply one live palette change to an existing component."""
        entity = self.document.entity_by_id(entity_id)
        if entity is None:
            self._say("要编辑的构件已经不存在。")
            return None
        if isinstance(entity, Wall):
            current = {
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
                "axis": entity.axis,
            }
        elif isinstance(entity, Window):
            current = {
                "sill_height_mm": entity.sill_height_mm,
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
            }
        elif isinstance(entity, Railing):
            current = {
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
                "material": entity.material,
            }
        else:
            self._say("该图元没有可双击编辑的构件参数。")
            return None
        if all(current.get(key) == value for key, value in params.items()):
            return entity_id

        snapshot = self.document.to_dict()
        self._push_undo()
        try:
            if isinstance(entity, Wall):
                self.document.update_wall_parameters(
                    entity.id,
                    height_mm=float(params["height_mm"]),
                    width_mm=float(params["width_mm"]),
                    axis=str(params["axis"]),
                )
                mapping = self.document.normalise_wall_topology()
                resulting_ids = mapping.get(entity.id, [entity.id])
                entity_id = next(
                    (
                        candidate
                        for candidate in resulting_ids
                        if self.document.wall_by_id(candidate) is not None
                    ),
                    entity.id,
                )
            elif isinstance(entity, Window):
                self.document.update_window_parameters(
                    entity.id,
                    sill_height_mm=float(params["sill_height_mm"]),
                    height_mm=float(params["height_mm"]),
                    width_mm=float(params["width_mm"]),
                )
            else:
                self.document.update_railing_parameters(
                    entity.id,
                    height_mm=float(params["height_mm"]),
                    width_mm=float(params["width_mm"]),
                    material=str(params["material"]),
                )
        except (KeyError, TypeError, ValueError) as exc:
            self.document = DraftDocument.from_dict(snapshot)
            self._undo_stack.pop()
            self._say(str(exc))
            self.update()
            return None

        self._selected_ids = {entity_id}
        self._selected_id = entity_id
        self._notify_selection_changed()
        self._refresh_rooms()
        self.document_changed.emit()
        self.update()
        self._say("构件参数已更新；可继续在参数窗中调整。")
        return entity_id

    def start_dimension(self) -> None:
        if self.view_mode != "plan":
            self.set_view_mode("plan")
        self.finish_command(quiet=True)
        self.mode = "dimension_first"
        self._dimension_points = []
        self._active_dimension_id = None
        self._snap_world = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._say(
            "ZDBZ逐点标注：指定第一个尺寸起点。"
        )

    def complete_command(self) -> bool:
        """Enter/Space/right-click completion shared by canvas and main window."""
        if self.mode == "select":
            return False
        if self.mode in {"move_select", "copy_select", "mirror_select"}:
            if not self._selected_ids:
                self._say("尚未选择图元，请先点选或框选。")
                return False
            operation = self.mode.removesuffix("_select")
            self._edit_entity_ids = set(self._selected_ids)
            self.mode = (
                "mirror_first"
                if operation == "mirror"
                else f"{operation}_base"
            )
            self._say(
                f"已确认{len(self._edit_entity_ids)}个图元；"
                f"指定{'镜像轴第一点' if operation == 'mirror' else '基点'}。"
            )
            self.update()
            return True
        if self.mode == "copy_target":
            self.finish_command()
            return True
        if self.mode in {
            "move_base",
            "move_target",
            "copy_base",
            "mirror_first",
            "mirror_second",
        }:
            self._say("当前编辑命令尚未取得足够的基点/目标点；按ESC取消。")
            return False
        if self.mode == "dimension_continue":
            self.finish_command()
            return True
        if self.mode in {
            "dimension_first",
            "dimension_second",
            "dimension_place",
        }:
            self._say("尺寸标注尚未完成三个基础点；按ESC可取消。")
            return False
        self.finish_command()
        return True

    def cancel_command(self) -> bool:
        if self._grip_window_id is not None or self._grip_wall_id is not None:
            self._grip_window_id = None
            self._grip_window_endpoint = None
            self._grip_window_preview_offset = None
            self._grip_window_origin_offset = None
            self._grip_wall_id = None
            self._grip_endpoint = None
            self._grip_preview = None
            self._grip_wall_origin = None
            self._grip_has_direction = False
            self._snap_world = None
            self._snap_kind = None
            self.unsetCursor()
            self.update()
            self._say("已取消端点调整。")
            return True
        if self.mode == "select":
            return False
        self.finish_command(quiet=True)
        self._say("已取消当前命令。")
        return True

    def clear_selection(self) -> bool:
        if not self._selected_ids and self._selected_id is None:
            return False
        self._selected_ids.clear()
        self._selected_id = None
        self._notify_selection_changed()
        self.update()
        self._say("已取消选择。")
        return True

    def finish_command(self, *, quiet: bool = False) -> None:
        previous = self.mode
        self.mode = "select"
        self._last_point = None
        self._window_preview = None
        self._dimension_points = []
        self._active_dimension_id = None
        self._snap_world = None
        self._snap_kind = None
        self._edit_entity_ids = set()
        self._edit_base = None
        self._grip_wall_id = None
        self._grip_endpoint = None
        self._grip_preview = None
        self._grip_wall_origin = None
        self._grip_window_id = None
        self._grip_window_endpoint = None
        self._grip_window_preview_offset = None
        self._grip_window_origin_offset = None
        self._grip_has_direction = False
        self.unsetCursor()
        self.update()
        if not quiet and previous != "select":
            self._say(
                "命令结束。可输入 HZQT、MC、LG、L、M、CO 或 MI。"
            )

    def apply_numeric(self, value_mm: float) -> bool:
        if value_mm <= 0.0:
            self._say("输入值必须大于0。")
            return False
        if self._grip_window_id is not None:
            origin = self._grip_window_origin_offset
            preview = self._grip_window_preview_offset
            if origin is None or preview is None or abs(preview - origin) <= 1e-6:
                self._say("请先移动鼠标指定窗体夹点的方向，再输入移动距离。")
                return False
            self._grip_window_preview_offset = (
                origin + math.copysign(value_mm, preview - origin)
            )
            return self._commit_window_grip()
        if self._grip_wall_id is not None:
            origin = self._grip_wall_origin
            preview = self._grip_preview
            if origin is None or preview is None:
                self._say("请先移动鼠标指定墙体夹点的方向，再输入移动距离。")
                return False
            dx = preview.x - origin.x
            dy = preview.y - origin.y
            length = math.hypot(dx, dy)
            if length <= 1e-6:
                self._say("请先移动鼠标指定墙体夹点的方向，再输入移动距离。")
                return False
            self._grip_preview = Point(
                origin.x + dx / length * value_mm,
                origin.y + dy / length * value_mm,
            )
            return self._commit_wall_grip()
        if self.mode in {"wall", "railing", "line"}:
            if self._last_point is None:
                self._say("请先在图上指定起点。")
                return False
            hover = self._snap_world or self._hover_world
            if hover is None:
                self._say("请先移动鼠标指定方向。")
                return False
            dx = hover.x - self._last_point.x
            dy = hover.y - self._last_point.y
            if self.ortho:
                if abs(dx) >= abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                dx, dy, length = 1.0, 0.0, 1.0
            endpoint = Point(
                self._last_point.x + dx / length * value_mm,
                self._last_point.y + dy / length * value_mm,
            )
            self._commit_linear(endpoint)
            return True
        if self.mode == "window":
            if self._window_preview is None:
                self._say("请先把鼠标移动到目标墙体附近。")
                return False
            wall, _offset = self._window_preview
            width = float(self.window_params["width_mm"])
            offset = value_mm
            if offset + width > wall.length_mm + 1e-6:
                self._say(
                    f"该墙长{wall.length_mm:.0f} mm，输入左边距后窗体会越界。"
                )
                return False
            self._place_window(wall, offset)
            return True
        self._say("当前没有可接收数值的绘图命令。")
        return False

    def toggle_ortho(self) -> None:
        self.ortho = not self.ortho
        self._say(f"正交模式 F8：{'开' if self.ortho else '关'}。")
        self.update()

    def set_object_snap_enabled(self, enabled: bool) -> None:
        self.object_snap_enabled = bool(enabled)
        self._snap_world = None
        self._snap_kind = None
        self._say(
            f"对象捕捉 F3：{'开' if self.object_snap_enabled else '关'}。"
            "开启时捕捉端点、中点、交点、垂足、最近点和墙体边缘。"
        )
        self.update()

    def toggle_object_snap(self) -> None:
        self.set_object_snap_enabled(not self.object_snap_enabled)

    # ------------------------------------------------------------------
    # Coordinate and snapping
    def world_to_screen(self, point: Point) -> QPointF:
        return QPointF(
            self.origin.x() + point.x * self.scale,
            self.origin.y() - point.y * self.scale,
        )

    def screen_to_world(self, point: QPointF) -> Point:
        return Point(
            (point.x() - self.origin.x()) / self.scale,
            (self.origin.y() - point.y()) / self.scale,
        )

    def _snap_segments(self) -> list[tuple[Point, Point]]:
        """Return finite axes that can provide geometric object snaps."""
        segments = [
            (entity.start, entity.end)
            for collection in (
                self.document.walls,
                self.document.railings,
                self.document.lines,
            )
            for entity in collection
            if entity.start.distance_to(entity.end) > 1.0
        ]
        for wall in self.document.walls:
            segments.extend(self._wall_edge_segments(wall))
        return segments

    def _wall_edge_segments(self, wall: Wall) -> list[tuple[Point, Point]]:
        """Return the two faces and end caps of a physical wall strip."""
        low, high = self._wall_offsets(wall)
        start_low = self._offset_point(wall.start, wall, low)
        end_low = self._offset_point(wall.end, wall, low)
        start_high = self._offset_point(wall.start, wall, high)
        end_high = self._offset_point(wall.end, wall, high)
        return [
            (start_low, end_low),
            (start_high, end_high),
            (start_low, start_high),
            (end_low, end_high),
        ]

    def _active_snap_anchor(self) -> Optional[Point]:
        """Point from which perpendicular/orthogonal tracking is measured."""
        if self._last_point is not None:
            return self._last_point
        if (
            self._grip_wall_id is not None
            and self._grip_endpoint in {"start", "end"}
        ):
            wall = self.document.wall_by_id(self._grip_wall_id)
            if wall is not None:
                return (
                    wall.end
                    if self._grip_endpoint == "start"
                    else wall.start
                )
        if (
            self._grip_window_id is not None
            and self._grip_window_endpoint in {"start", "end"}
        ):
            window = self.document.entity_by_id(self._grip_window_id)
            if isinstance(window, Window):
                wall = self.document.wall_by_id(window.wall_id)
                if wall is not None:
                    fixed_offset = (
                        window.end_offset_mm
                        if self._grip_window_endpoint == "start"
                        else window.offset_mm
                    )
                    return wall.point_at(fixed_offset)
        if self.mode in {"move_target", "copy_target", "mirror_second"}:
            return self._edit_base
        if self.mode in {"dimension_second", "dimension_continue"}:
            if self._dimension_points:
                return self._dimension_points[-1]
        return None

    @staticmethod
    def _project_to_segment(
        point: Point,
        start: Point,
        end: Point,
    ) -> tuple[Point, float]:
        """Return the clamped projection and its 0..1 segment parameter."""
        dx = end.x - start.x
        dy = end.y - start.y
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-9:
            return start, 0.0
        parameter = (
            (point.x - start.x) * dx
            + (point.y - start.y) * dy
        ) / length_squared
        clamped = max(0.0, min(1.0, parameter))
        return (
            Point(start.x + dx * clamped, start.y + dy * clamped),
            parameter,
        )

    def _intersection_snap_candidates(
        self,
        raw: Point,
        segments: list[tuple[Point, Point]],
        tolerance: float,
    ) -> list[tuple[Point, str]]:
        """Find actual finite-segment intersections near the cursor aperture."""
        nearby = [
            (start, end)
            for start, end in segments
            if (
                min(start.x, end.x) - tolerance
                <= raw.x
                <= max(start.x, end.x) + tolerance
                and min(start.y, end.y) - tolerance
                <= raw.y
                <= max(start.y, end.y) + tolerance
            )
        ]
        candidates: list[tuple[Point, str]] = []
        for first_index, (first_start, first_end) in enumerate(nearby):
            for second_start, second_end in nearby[first_index + 1:]:
                intersection = segment_intersection(
                    first_start,
                    first_end,
                    second_start,
                    second_end,
                    1e-6,
                )
                if (
                    intersection is not None
                    and raw.distance_to(intersection) <= tolerance
                ):
                    candidates.append((intersection, "intersection"))
        return candidates

    def _drawing_snap(self, raw: Point) -> Point:
        special_tolerance = 13.0 / max(self.scale, 1e-9)
        nearest_tolerance = 9.0 / max(self.scale, 1e-9)
        if not self.object_snap_enabled:
            snapped = self._grid_snap(raw)
            self._snap_kind = "grid"
            anchor = self._active_snap_anchor()
            if anchor is not None and self.ortho:
                dx = snapped.x - anchor.x
                dy = snapped.y - anchor.y
                if abs(dx) >= abs(dy):
                    snapped = Point(snapped.x, anchor.y)
                else:
                    snapped = Point(anchor.x, snapped.y)
                self._snap_kind = "ortho"
            return snapped
        segments = self._snap_segments()
        candidates = [
            (point, "endpoint")
            for wall in self.document.walls
            for point in (wall.start, wall.end)
        ] + [
            (point, "endpoint")
            for railing in self.document.railings
            for point in (railing.start, railing.end)
        ] + [
            (point, "endpoint")
            for line in self.document.lines
            for point in (line.start, line.end)
        ] + [
            (point, "endpoint")
            for window in self.document.windows
            for wall in [self.document.wall_by_id(window.wall_id)]
            if wall is not None
            for point in (
                wall.point_at(window.offset_mm),
                wall.point_at(window.end_offset_mm),
            )
        ] + [
            (
                Point(
                    (wall.start.x + wall.end.x) / 2.0,
                    (wall.start.y + wall.end.y) / 2.0,
                ),
                "midpoint",
            )
            for wall in self.document.walls
        ] + [
            (
                Point(
                    (railing.start.x + railing.end.x) / 2.0,
                    (railing.start.y + railing.end.y) / 2.0,
                ),
                "midpoint",
            )
            for railing in self.document.railings
        ] + [
            (
                Point(
                    (line.start.x + line.end.x) / 2.0,
                    (line.start.y + line.end.y) / 2.0,
                ),
                "midpoint",
            )
            for line in self.document.lines
        ] + [
            (
                wall.point_at(window.offset_mm + window.width_mm / 2.0),
                "midpoint",
            )
            for window in self.document.windows
            for wall in [self.document.wall_by_id(window.wall_id)]
            if wall is not None
        ]
        for wall in self.document.walls:
            for start, end in self._wall_edge_segments(wall):
                candidates.extend([
                    (start, "wall_edge_endpoint"),
                    (end, "wall_edge_endpoint"),
                    (
                        Point(
                            (start.x + end.x) / 2.0,
                            (start.y + end.y) / 2.0,
                        ),
                        "wall_edge_midpoint",
                    ),
                ])
        candidates.extend(self._intersection_snap_candidates(
            raw,
            segments,
            special_tolerance,
        ))
        snap_anchor = self._active_snap_anchor()
        if snap_anchor is not None:
            for start, end in segments:
                foot, parameter = self._project_to_segment(
                    snap_anchor,
                    start,
                    end,
                )
                if (
                    0.0 <= parameter <= 1.0
                    and snap_anchor.distance_to(foot) > 1.0
                    and raw.distance_to(foot) <= special_tolerance
                ):
                    candidates.append((foot, "perpendicular"))
        entity_snapped = False
        if candidates:
            nearest, kind = min(
                candidates,
                key=lambda item: raw.distance_to(item[0]),
            )
            if raw.distance_to(nearest) <= special_tolerance:
                snapped = nearest
                self._snap_kind = kind
                entity_snapped = True
            else:
                snapped = raw
        else:
            snapped = raw
        if not entity_snapped and segments:
            nearest_axis, _parameter = min(
                (
                    self._project_to_segment(raw, start, end)
                    for start, end in segments
                ),
                key=lambda item: raw.distance_to(item[0]),
            )
            if raw.distance_to(nearest_axis) <= nearest_tolerance:
                snapped = nearest_axis
                self._snap_kind = "nearest"
                entity_snapped = True
        if not entity_snapped:
            snapped = self._grid_snap(raw)
            self._snap_kind = "grid"
        ortho_anchor = snap_anchor
        if ortho_anchor is not None and self.ortho and not entity_snapped:
            dx = snapped.x - ortho_anchor.x
            dy = snapped.y - ortho_anchor.y
            angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
            if min(angle, abs(angle - 180.0)) <= 8.0:
                snapped = Point(snapped.x, ortho_anchor.y)
                self._snap_kind = "ortho"
            elif abs(angle - 90.0) <= 8.0:
                snapped = Point(ortho_anchor.x, snapped.y)
                self._snap_kind = "ortho"
        return snapped

    def _grid_snap(self, point: Point) -> Point:
        spacing = max(float(self.document.grid_mm), 1.0)
        return Point(
            round(point.x / spacing) * spacing,
            round(point.y / spacing) * spacing,
        )

    def _hit_wall_grip(
        self,
        screen_point: QPointF,
    ) -> Optional[tuple[Wall, str]]:
        """Return a selected wall endpoint handle within an 8 px radius."""
        best: Optional[tuple[float, Wall, str]] = None
        for wall in self.document.walls:
            if wall.id not in self._selected_ids:
                continue
            for endpoint, point in (
                ("start", wall.start),
                ("end", wall.end),
            ):
                candidate = self.world_to_screen(point)
                distance = math.hypot(
                    candidate.x() - screen_point.x(),
                    candidate.y() - screen_point.y(),
                )
                if distance > 8.0:
                    continue
                if best is None or distance < best[0]:
                    best = distance, wall, endpoint
        if best is None:
            return None
        return best[1], best[2]

    def _hit_window_grip(
        self,
        screen_point: QPointF,
    ) -> Optional[tuple[Window, str]]:
        """Return a selected window's start/end handle within 8 px."""
        best: Optional[tuple[float, Window, str]] = None
        for window in self.document.windows:
            if window.id not in self._selected_ids:
                continue
            wall = self.document.wall_by_id(window.wall_id)
            if wall is None:
                continue
            for endpoint, offset in (
                ("start", window.offset_mm),
                ("end", window.end_offset_mm),
            ):
                candidate = self.world_to_screen(wall.point_at(offset))
                distance = math.hypot(
                    candidate.x() - screen_point.x(),
                    candidate.y() - screen_point.y(),
                )
                if distance > 8.0:
                    continue
                if best is None or distance < best[0]:
                    best = distance, window, endpoint
        if best is None:
            return None
        return best[1], best[2]

    def _window_grip_target(self, raw: Point) -> Optional[float]:
        window = self.document.entity_by_id(self._grip_window_id or "")
        if not isinstance(window, Window):
            return None
        wall = self.document.wall_by_id(window.wall_id)
        if wall is None:
            return None
        snapped = self._drawing_snap(raw)
        dx, dy = wall.direction
        along = (
            (snapped.x - wall.start.x) * dx
            + (snapped.y - wall.start.y) * dy
        )
        if self._snap_kind in {None, "grid", "ortho"}:
            self._snap_kind = "window_axis"
        return max(0.0, min(wall.length_mm, along))

    def _wall_grip_target(self, raw: Point) -> Point:
        wall = self.document.wall_by_id(self._grip_wall_id or "")
        if wall is None:
            return self._drawing_snap(raw)
        hosted = any(
            window.wall_id == wall.id
            for window in self.document.windows
        )
        if not hosted:
            return self._drawing_snap(raw)
        # A hosted window must stay on the same physical axis.  First acquire
        # CAD object snaps (including wall faces), then project the alignment
        # onto the hosted wall axis.
        snapped = self._drawing_snap(raw)
        dx, dy = wall.direction
        along = (
            (snapped.x - wall.start.x) * dx
            + (snapped.y - wall.start.y) * dy
        )
        if self._snap_kind in {None, "grid", "ortho"}:
            self._snap_kind = "wall_axis"
        return Point(
            wall.start.x + dx * along,
            wall.start.y + dy * along,
        )

    # ------------------------------------------------------------------
    # Events
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() != Qt.MouseButton.LeftButton
            or self.mode != "select"
            or self.view_mode != "plan"
        ):
            super().mouseDoubleClickEvent(event)
            return
        self._grip_wall_id = None
        self._grip_endpoint = None
        self._grip_preview = None
        self._grip_wall_origin = None
        self._grip_window_id = None
        self._grip_window_endpoint = None
        self._grip_window_preview_offset = None
        self._grip_window_origin_offset = None
        self._grip_has_direction = False
        entity_id = self._hit_test(
            self.screen_to_world(event.position())
        )
        entity = self.document.entity_by_id(entity_id or "")
        if not isinstance(entity, (Wall, Window, Railing)):
            self._say("该图元没有可编辑的构件参数。")
            return
        self._selected_ids = {entity.id}
        self._selected_id = entity.id
        self._notify_selection_changed()
        self.update()
        self.entity_edit_requested.emit(entity.id)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_last
            self.origin += delta
            self._pan_last = event.position()
            self.update()
            return
        if self._grip_window_id is not None:
            raw = self.screen_to_world(event.position())
            self._hover_world = raw
            self._grip_window_preview_offset = self._window_grip_target(raw)
            window = self.document.entity_by_id(self._grip_window_id)
            if isinstance(window, Window):
                wall = self.document.wall_by_id(window.wall_id)
                if wall is not None and self._grip_window_preview_offset is not None:
                    self._snap_world = wall.point_at(
                        self._grip_window_preview_offset
                    )
                    origin = self._grip_window_origin_offset
                    if origin is not None:
                        self._grip_has_direction = (
                            abs(self._grip_window_preview_offset - origin)
                            * self.scale > 3.0
                        )
            self.update()
            return
        if self._grip_wall_id is not None:
            raw = self.screen_to_world(event.position())
            self._hover_world = raw
            self._grip_preview = self._wall_grip_target(raw)
            self._snap_world = self._grip_preview
            if self._grip_wall_origin is not None:
                self._grip_has_direction = (
                    self._grip_wall_origin.distance_to(self._grip_preview)
                    * self.scale > 3.0
                )
            self.update()
            return
        if self._selection_start is not None:
            self._selection_current = event.position()
            self.update()
            return
        raw = self.screen_to_world(event.position())
        self._hover_world = raw
        if self.mode in {"wall", "railing", "line"}:
            self._snap_world = self._drawing_snap(raw)
        elif self.mode == "window":
            maximum = 18.0 / max(self.scale, 1e-9)
            nearest = self.document.nearest_wall(raw, maximum)
            if nearest is None:
                self._window_preview = None
                self._snap_world = None
                self._snap_kind = None
            else:
                wall, projected, along, _distance = nearest
                width = float(self.window_params["width_mm"])
                offset = max(
                    0.0,
                    min(wall.length_mm - width, along - width / 2.0),
                )
                if wall.length_mm < width:
                    self._window_preview = None
                else:
                    self._window_preview = wall, offset
                    self._snap_world = projected
                    self._snap_kind = "wall_axis"
        elif self.mode in {
            "dimension_first",
            "dimension_second",
            "dimension_continue",
        }:
            self._snap_world = self._drawing_snap(raw)
        elif self.mode == "dimension_place":
            self._snap_world = raw
            self._snap_kind = None
            baseline = Wall(
                self._dimension_points[0],
                self._dimension_points[1],
            )
            nx, ny = baseline.normal
            self._dimension_offset_preview = (
                (raw.x - baseline.start.x) * nx
                + (raw.y - baseline.start.y) * ny
            )
        elif self.mode in {
            "move_base",
            "move_target",
            "copy_base",
            "copy_target",
            "mirror_first",
            "mirror_second",
        }:
            self._snap_world = self._drawing_snap(raw)
        else:
            self._snap_world = None
            self._snap_kind = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.complete_command()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        if self.mode == "select":
            if self._grip_window_id is not None:
                raw = self.screen_to_world(event.position())
                self._grip_window_preview_offset = self._window_grip_target(raw)
                self._commit_window_grip()
                return
            if self._grip_wall_id is not None:
                raw = self.screen_to_world(event.position())
                self._grip_preview = self._wall_grip_target(raw)
                self._commit_wall_grip()
                return
            window_grip = self._hit_window_grip(event.position())
            if window_grip is not None:
                window, endpoint = window_grip
                self._grip_window_id = window.id
                self._grip_window_endpoint = endpoint
                self._grip_window_preview_offset = (
                    window.offset_mm
                    if endpoint == "start"
                    else window.end_offset_mm
                )
                self._grip_window_origin_offset = (
                    self._grip_window_preview_offset
                )
                self._grip_has_direction = False
                self._say(
                    "拖动窗体夹点调整窗宽；可吸附特殊点，"
                    "也可先指示方向再键入移动距离，ESC取消。"
                )
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update()
                return
            grip = self._hit_wall_grip(event.position())
            if grip is not None:
                wall, endpoint = grip
                self._grip_wall_id = wall.id
                self._grip_endpoint = endpoint
                self._grip_preview = (
                    wall.start if endpoint == "start" else wall.end
                )
                self._grip_wall_origin = self._grip_preview
                self._grip_has_direction = False
                self._say(
                    "拖动墙体夹点调整长短；可吸附特殊点，"
                    "也可先指示方向再键入移动距离，ESC取消。"
                )
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update()
                return
        if self.mode in {"move_base", "copy_base", "mirror_first"}:
            point = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            self._edit_base = point
            if self.mode == "move_base":
                self.mode = "move_target"
                prompt = "指定移动目标点。"
            elif self.mode == "copy_base":
                self.mode = "copy_target"
                prompt = "指定复制目标点；可连续指定多个目标，Enter结束。"
            else:
                self.mode = "mirror_second"
                prompt = "指定镜像轴第二点；默认保留原图元。"
            self._say(
                f"基点 ({point.x:.0f}, {point.y:.0f})；{prompt}"
            )
            self.update()
            return
        if self.mode in {"move_target", "copy_target", "mirror_second"}:
            point = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            if self._edit_base is None:
                self.cancel_command()
                return
            if self.mode == "mirror_second":
                if self._edit_base.distance_to(point) <= 1.0:
                    self._say("镜像轴第二点不能与第一点重合。")
                    return
                if self._apply_edit_operation("mirror", point):
                    self.finish_command(quiet=True)
            else:
                operation = (
                    "move" if self.mode == "move_target" else "copy"
                )
                completed = self._apply_edit_operation(operation, point)
                if operation == "move" and completed:
                    self.finish_command(quiet=True)
            self.update()
            return
        if self.mode in {"wall", "railing", "line"}:
            endpoint = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            if self._last_point is None:
                self._last_point = endpoint
                self._say(
                    f"起点 ({endpoint.x:.0f}, {endpoint.y:.0f})；"
                    "指定下一点或输入长度。"
                )
            else:
                self._commit_linear(endpoint)
            self.update()
            return
        if self.mode == "window":
            if self._window_preview is None:
                self._say("未找到可放置窗体的墙，请靠近墙轴线。")
                return
            self._place_window(*self._window_preview)
            return
        if self.mode == "dimension_first":
            point = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            self._dimension_points = [point]
            self.mode = "dimension_second"
            self._say(
                f"起点 ({point.x:.0f}, {point.y:.0f})；"
                "指定第二个测量点。"
            )
            self.update()
            return
        if self.mode == "dimension_second":
            point = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            if point.distance_to(self._dimension_points[0]) <= 1.0:
                self._say("第二个测量点不能与起点重合。")
                return
            self._dimension_points.append(point)
            self.mode = "dimension_place"
            self._say("移动鼠标并点击第三点，确定尺寸线标注位置。")
            self.update()
            return
        if self.mode == "dimension_place":
            self._push_undo()
            dimension = self.document.add_dimension(
                self._dimension_points[:2],
                offset_mm=self._dimension_offset_preview,
            )
            self._active_dimension_id = dimension.id
            self._selected_ids = {dimension.id}
            self._selected_id = dimension.id
            self._notify_selection_changed()
            self.document_changed.emit()
            self.mode = "dimension_continue"
            self._say(
                "基础尺寸已创建；继续点击其他测量点，"
                "Enter/空格/右键完成，ESC取消命令。"
            )
            self.update()
            return
        if self.mode == "dimension_continue":
            point = self._snap_world or self._drawing_snap(
                self.screen_to_world(event.position())
            )
            dimension = self.document.dimension_by_id(
                self._active_dimension_id or ""
            )
            if dimension is None:
                self.cancel_command()
                return
            if any(point.distance_to(existing) <= 1.0 for existing in dimension.points):
                self._say("该测量点已经存在，请选择其他点。")
                return
            self._push_undo()
            dimension.points.append(point)
            self._dimension_points = list(dimension.points)
            self.document_changed.emit()
            self._say(
                f"已追加第{len(dimension.points)}个测量点；"
                "继续取点或按Enter/空格/右键完成。"
            )
            self.update()
            return

        world = self.screen_to_world(event.position())
        hit = self._hit_test(world)
        additive = bool(
            event.modifiers()
            & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        )
        if hit is not None:
            if additive:
                if hit in self._selected_ids:
                    self._selected_ids.remove(hit)
                else:
                    self._selected_ids.add(hit)
            else:
                self._selected_ids = {hit}
            self._selected_id = next(iter(self._selected_ids), None)
        else:
            self._selection_start = event.position()
            self._selection_current = event.position()
            self._selection_additive = additive
            if not additive:
                self._selected_ids.clear()
                self._selected_id = None
        self._notify_selection_changed()
        self.update()
        if self._selected_ids:
            if self.mode in {"move_select", "copy_select", "mirror_select"}:
                self._say(
                    f"已选择{len(self._selected_ids)}个图元；"
                    "继续选择，Enter或空格确认。"
                )
            else:
                self._say(
                    f"已选择{len(self._selected_ids)}个图元；"
                    "Shift/Ctrl切选，Delete删除。"
                )
        else:
            self._say("拖动形成选择框：左→右全包含，右→左相交即选。")

    def _commit_window_grip(self) -> bool:
        if self._grip_window_id is None:
            return False
        window_id = self._grip_window_id
        endpoint = self._grip_window_endpoint or "end"
        target_offset = self._grip_window_preview_offset
        self._grip_window_id = None
        self._grip_window_endpoint = None
        self._grip_window_preview_offset = None
        self._grip_window_origin_offset = None
        self._grip_has_direction = False
        self._snap_world = None
        self._snap_kind = None
        self.unsetCursor()
        if target_offset is None:
            self.update()
            return False
        snapshot = self.document.to_dict()
        self._push_undo()
        try:
            window = self.document.resize_window_endpoint(
                window_id,
                endpoint,
                target_offset,
            )
        except ValueError as exc:
            self.document = DraftDocument.from_dict(snapshot)
            self._undo_stack.pop()
            self._say(str(exc))
            self.update()
            return False
        wall = self.document.wall_by_id(window.wall_id)
        right = (
            wall.length_mm - window.end_offset_mm
            if wall is not None
            else 0.0
        )
        self._selected_ids = {window.id}
        self._selected_id = window.id
        self._notify_selection_changed()
        self.document_changed.emit()
        self._say(
            f"窗宽已调整为 {window.width_mm:.0f} mm；"
            f"左距 {window.offset_mm:.0f} mm，右距 {right:.0f} mm。"
        )
        self.update()
        return True

    def _commit_wall_grip(self) -> bool:
        if self._grip_wall_id is None:
            return False
        wall_id = self._grip_wall_id
        endpoint = self._grip_endpoint or "end"
        target = self._grip_preview
        self._grip_wall_id = None
        self._grip_endpoint = None
        self._grip_preview = None
        self._grip_wall_origin = None
        self._grip_has_direction = False
        self._snap_world = None
        self._snap_kind = None
        self.unsetCursor()
        if target is None:
            self.update()
            return False
        snapshot = self.document.to_dict()
        self._push_undo()
        try:
            self.document.resize_wall_endpoint(
                wall_id,
                endpoint,
                target,
            )
            mapping = self.document.normalise_wall_topology()
        except ValueError as exc:
            self.document = DraftDocument.from_dict(snapshot)
            self._undo_stack.pop()
            self._say(str(exc))
            self.update()
            return False
        self._selected_ids = set(mapping.get(wall_id, [wall_id]))
        self._selected_id = next(iter(self._selected_ids), None)
        self._notify_selection_changed()
        self._refresh_rooms()
        self.document_changed.emit()
        self._say("墙体端点已调整；相交处已重新拆分为可独立编辑的墙段。")
        self.update()
        return True

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            if self.mode == "select":
                self.unsetCursor()
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._grip_window_id is not None
        ):
            if not self._grip_has_direction:
                self._say(
                    "窗体夹点已激活：移动鼠标指定方向后输入距离，"
                    "或单击特殊点完成。"
                )
                return
            self._commit_window_grip()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._grip_wall_id is not None
        ):
            if not self._grip_has_direction:
                self._say(
                    "墙体夹点已激活：移动鼠标指定方向后输入距离，"
                    "或单击特殊点完成。"
                )
                return
            self._commit_wall_grip()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._selection_start is not None
        ):
            start = self._selection_start
            end = self._selection_current or event.position()
            self._apply_box_selection(start, end, self._selection_additive)
            self._selection_start = None
            self._selection_current = None
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        before = self.screen_to_world(event.position())
        factor = 1.18 if event.angleDelta().y() > 0 else 1.0 / 1.18
        self.scale = max(0.002, min(2.0, self.scale * factor))
        after_screen = self.world_to_screen(before)
        self.origin += event.position() - after_screen
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._grip_window_id is not None:
                self._grip_window_id = None
                self._grip_window_endpoint = None
                self._grip_window_preview_offset = None
                self._grip_window_origin_offset = None
                self._grip_has_direction = False
                self._snap_world = None
                self.unsetCursor()
                self.update()
            if self._grip_wall_id is not None:
                self._grip_wall_id = None
                self._grip_endpoint = None
                self._grip_preview = None
                self._grip_wall_origin = None
                self._grip_has_direction = False
                self.unsetCursor()
                self.update()
            self.cancel_command()
            self.clear_selection()
            return
        if event.key() == Qt.Key.Key_F8:
            self.toggle_ortho()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            return
        if event.key() in {
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        }:
            if self.complete_command():
                return
        if event.key() == Qt.Key.Key_Delete and self._selected_ids:
            self._push_undo()
            removed = sum(
                1
                for entity_id in list(self._selected_ids)
                if self.document.remove_entity(entity_id)
            )
            if removed:
                self.document.normalise_wall_topology()
                self._selected_ids.clear()
                self._selected_id = None
                self._notify_selection_changed()
                self._refresh_rooms()
                self.document_changed.emit()
                self._say(f"已删除{removed}个图元。")
                self.update()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        if self.origin.y() <= 0:
            self.origin.setY(self.height() - 80.0)
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    # Mutations
    def _commit_linear(self, endpoint: Point) -> None:
        if self._last_point is None:
            self._last_point = endpoint
            return
        if self._last_point.distance_to(endpoint) <= 1.0:
            self._say("线段长度过短，未创建。")
            return
        self._push_undo()
        created_length = self._last_point.distance_to(endpoint)
        split_mapping: dict[str, list[str]] = {}
        if self.mode == "wall":
            entity = self.document.add_wall(
                self._last_point,
                endpoint,
                height_mm=float(self.wall_params["height_mm"]),
                width_mm=float(self.wall_params["width_mm"]),
                axis=str(self.wall_params["axis"]),
            )
            kind = "墙体"
            split_mapping = self.document.normalise_wall_topology()
        elif self.mode == "railing":
            entity = self.document.add_railing(
                self._last_point,
                endpoint,
                height_mm=float(self.railing_params["height_mm"]),
                width_mm=float(self.railing_params["width_mm"]),
                material=str(self.railing_params["material"]),
            )
            kind = "栏杆"
        else:
            entity = self.document.add_line(self._last_point, endpoint)
            kind = "直线"
        self._last_point = endpoint
        self._selected_id = entity.id
        self._selected_ids = set(
            split_mapping.get(entity.id, [entity.id])
            if self.mode == "wall"
            else [entity.id]
        )
        self._notify_selection_changed()
        self._refresh_rooms()
        self.document_changed.emit()
        self._say(
            f"已创建{kind}，长度 {created_length:.0f} mm；"
            "继续指定下一点，右键结束。"
        )
        self.update()

    def _apply_edit_operation(self, operation: str, target: Point) -> bool:
        if self._edit_base is None or not self._edit_entity_ids:
            self._say("编辑命令缺少选择集或基点。")
            return False
        snapshot = self.document.to_dict()
        self._push_undo()
        try:
            if operation == "move":
                dx = target.x - self._edit_base.x
                dy = target.y - self._edit_base.y
                affected = self.document.move_entities(
                    self._edit_entity_ids,
                    dx,
                    dy,
                )
                message = (
                    f"已移动{len(affected)}个图元："
                    f"ΔX={dx:.0f}，ΔY={dy:.0f} mm。"
                )
            elif operation == "copy":
                dx = target.x - self._edit_base.x
                dy = target.y - self._edit_base.y
                affected = self.document.copy_entities(
                    self._edit_entity_ids,
                    dx,
                    dy,
                )
                message = (
                    f"已复制{len(affected)}个图元："
                    f"ΔX={dx:.0f}，ΔY={dy:.0f} mm；"
                    "继续指定目标点，Enter结束。"
                )
            else:
                affected = self.document.mirror_entities(
                    self._edit_entity_ids,
                    self._edit_base,
                    target,
                )
                message = (
                    f"已生成{len(affected)}个镜像图元，原图元已保留。"
                )
            if not affected:
                raise ValueError("选择集中没有可执行该操作的图元。")
            topology = self.document.normalise_wall_topology()
        except ValueError as exc:
            self.document = DraftDocument.from_dict(snapshot)
            self._undo_stack.pop()
            self._say(str(exc))
            return False
        self._selected_ids = {
            resulting_id
            for entity_id in affected
            for resulting_id in topology.get(entity_id, [entity_id])
            if self.document.entity_by_id(resulting_id) is not None
        }
        self._selected_id = next(iter(self._selected_ids), None)
        self._notify_selection_changed()
        self._refresh_rooms()
        self.document_changed.emit()
        self._say(message)
        self.update()
        return True

    def _place_window(self, wall: Wall, offset: float) -> None:
        self._push_undo()
        try:
            window = self.document.add_window(
                wall.id,
                offset,
                width_mm=float(self.window_params["width_mm"]),
                sill_height_mm=float(self.window_params["sill_height_mm"]),
                height_mm=float(self.window_params["height_mm"]),
            )
        except ValueError as exc:
            self._undo_stack.pop()
            self._say(str(exc))
            return
        self._selected_id = window.id
        self._selected_ids = {window.id}
        self._notify_selection_changed()
        right = wall.length_mm - window.end_offset_mm
        self.document_changed.emit()
        self._say(
            f"窗体已插入：左距 {window.offset_mm:.0f} mm，"
            f"右距 {right:.0f} mm；可继续插窗。"
        )
        self.update()

    def _hit_test(self, point: Point) -> Optional[str]:
        tolerance = 10.0 / max(self.scale, 1e-9)
        for window in reversed(self.document.windows):
            wall = self.document.wall_by_id(window.wall_id)
            if wall is None:
                continue
            _projected, along, distance = wall.project(point)
            if (
                distance <= tolerance
                and window.offset_mm - tolerance
                <= along
                <= window.end_offset_mm + tolerance
            ):
                return window.id
        for railing in reversed(self.document.railings):
            virtual = Wall(railing.start, railing.end)
            _projected, _along, distance = virtual.project(point)
            if distance <= tolerance:
                return railing.id
        for line in reversed(self.document.lines):
            virtual = Wall(line.start, line.end)
            _projected, _along, distance = virtual.project(point)
            if distance <= tolerance:
                return line.id
        for dimension in reversed(self.document.dimensions):
            min_x, min_y, max_x, max_y = self._entity_bounds(dimension)
            if (
                min_x - tolerance <= point.x <= max_x + tolerance
                and min_y - tolerance <= point.y <= max_y + tolerance
            ):
                return dimension.id
        nearest = self.document.nearest_wall(point, tolerance)
        return nearest[0].id if nearest is not None else None

    def _entity_bounds(self, entity) -> tuple[float, float, float, float]:
        if isinstance(entity, Wall):
            margin = entity.width_mm / 2.0
            points = (entity.start, entity.end)
        elif isinstance(entity, Railing):
            margin = entity.width_mm / 2.0
            points = (entity.start, entity.end)
        elif isinstance(entity, Line):
            margin = 20.0
            points = (entity.start, entity.end)
        elif isinstance(entity, Window):
            wall = self.document.wall_by_id(entity.wall_id)
            if wall is None:
                return 0.0, 0.0, 0.0, 0.0
            margin = wall.width_mm / 2.0
            points = (
                wall.point_at(entity.offset_mm),
                wall.point_at(entity.end_offset_mm),
            )
        elif isinstance(entity, DimensionChain):
            margin = 120.0
            points = list(entity.points)
            baseline = Wall(entity.points[0], entity.points[1])
            nx, ny = baseline.normal
            points += [
                Point(
                    point.x + nx * entity.offset_mm,
                    point.y + ny * entity.offset_mm,
                )
                for point in entity.points
            ]
        else:
            return 0.0, 0.0, 0.0, 0.0
        return (
            min(point.x for point in points) - margin,
            min(point.y for point in points) - margin,
            max(point.x for point in points) + margin,
            max(point.y for point in points) + margin,
        )

    def _all_entities(self) -> list:
        return (
            list(self.document.walls)
            + list(self.document.windows)
            + list(self.document.railings)
            + list(self.document.lines)
            + list(self.document.dimensions)
        )

    def _apply_box_selection(
        self,
        start_screen: QPointF,
        end_screen: QPointF,
        additive: bool,
    ) -> None:
        first = self.screen_to_world(start_screen)
        second = self.screen_to_world(end_screen)
        left, right = sorted((first.x, second.x))
        bottom, top = sorted((first.y, second.y))
        crossing = end_screen.x() < start_screen.x()
        hits: set[str] = set()
        for entity in self._all_entities():
            min_x, min_y, max_x, max_y = self._entity_bounds(entity)
            if crossing:
                selected = not (
                    max_x < left or min_x > right or max_y < bottom or min_y > top
                )
            else:
                selected = (
                    min_x >= left
                    and max_x <= right
                    and min_y >= bottom
                    and max_y <= top
                )
            if selected:
                hits.add(entity.id)
        if additive:
            self._selected_ids.symmetric_difference_update(hits)
        else:
            self._selected_ids = hits
        self._selected_id = next(iter(self._selected_ids), None)
        self._notify_selection_changed()
        self._say(
            f"{'交叉框选' if crossing else '窗口框选'}完成："
            f"当前选中{len(self._selected_ids)}个图元。"
        )

    # ------------------------------------------------------------------
    # Painting
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        self._paint_grid(painter)
        if self.view_mode != "plan":
            self._paint_elevation(painter)
            painter.end()
            return
        self._paint_rooms(painter)
        self._paint_wall_network(painter)
        self._paint_railing_network(painter)
        for line in self.document.lines:
            self._paint_line(painter, line)
        for window in self.document.windows:
            self._paint_window(painter, window)
        for dimension in self.document.dimensions:
            self._paint_dimension_chain(painter, dimension)
        self._paint_selection(painter)
        self._paint_selection_box(painter)
        self._paint_preview(painter)
        self._paint_crosshair(painter)
        painter.end()

    def _paint_grid(self, painter: QPainter) -> None:
        spacing = max(self.document.grid_mm, 1.0)
        while spacing * self.scale < 18.0:
            spacing *= 5.0 if spacing * self.scale < 4.0 else 2.0
        left = self.screen_to_world(QPointF(0.0, 0.0)).x
        right = self.screen_to_world(QPointF(self.width(), 0.0)).x
        bottom = self.screen_to_world(QPointF(0.0, self.height())).y
        top = self.screen_to_world(QPointF(0.0, 0.0)).y
        painter.setPen(QPen(QColor("#edf2f7"), 1.0))
        x = math.floor(left / spacing) * spacing
        while x <= right:
            p1 = self.world_to_screen(Point(x, bottom))
            p2 = self.world_to_screen(Point(x, top))
            painter.drawLine(p1, p2)
            x += spacing
        y = math.floor(bottom / spacing) * spacing
        while y <= top:
            p1 = self.world_to_screen(Point(left, y))
            p2 = self.world_to_screen(Point(right, y))
            painter.drawLine(p1, p2)
            y += spacing

    def _paint_rooms(self, painter: QPainter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for index, room in enumerate(self._rooms):
            color = ROOM_COLORS[index % len(ROOM_COLORS)]
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([
                self.world_to_screen(point)
                for point in room.points
            ]))

    def _wall_visible_in_elevation(self, wall: Wall) -> bool:
        return wall.length_mm > 1.0

    def _elevation_wall_horizontal_bounds(
        self,
        wall: Wall,
    ) -> tuple[float, float]:
        """
        Project the joined physical wall strip, including its real width.

        A face-on wall extends by half the width of a touching wall at each
        connected endpoint.  This matches the joined plan corner and prevents
        a covered edge-on wall from leaking out as a narrow strip.
        """
        start_extension = self._connection_extension(
            wall.start,
            wall.id,
            self.document.walls,
        )
        end_extension = self._connection_extension(
            wall.end,
            wall.id,
            self.document.walls,
        )
        start = wall.point_at(-start_extension)
        end = wall.point_at(wall.length_mm + end_extension)
        low, high = self._wall_offsets(wall)
        corners = [
            self._offset_point(endpoint, wall, offset)
            for endpoint in (start, end)
            for offset in (low, high)
        ]
        projected = [
            self._project_elevation_x(point)
            for point in corners
        ]
        return min(projected), max(projected)

    def _paint_elevation(self, painter: QPainter) -> None:
        labels = {
            "north": "北立面",
            "south": "南立面",
            "east": "东立面",
            "west": "西立面",
        }
        visible_walls = [
            wall
            for wall in self.document.walls
            if self._wall_visible_in_elevation(wall)
        ]
        elevation_items: list[tuple[float, int, str, object]] = [
            (
                self._elevation_nearness(wall),
                self._elevation_wall_layer_priority(wall),
                "wall",
                wall,
            )
            for wall in visible_walls
        ]
        for railing in self.document.railings:
            x1 = self._project_elevation_x(railing.start)
            x2 = self._project_elevation_x(railing.end)
            if abs(x2 - x1) <= 1.0:
                continue
            elevation_items.append(
                (self._elevation_nearness(railing), 0, "railing", railing)
            )

        # Painter's algorithm: distant elements first, nearest elements last.
        # A nearer wall therefore covers both walls, windows and railings behind it.
        elevation_items.sort(key=lambda item: (item[0], item[1]))
        for _depth, _priority, kind, entity in elevation_items:
            if kind == "wall":
                self._paint_elevation_wall(painter, entity)
            else:
                self._paint_elevation_railing(painter, entity)

        if visible_walls:
            xs = [
                horizontal
                for wall in visible_walls
                for horizontal in self._elevation_wall_horizontal_bounds(wall)
            ]
            painter.setPen(QPen(QColor("#475569"), 1.5))
            painter.drawLine(
                self.world_to_screen(Point(min(xs), 0.0)),
                self.world_to_screen(Point(max(xs), 0.0)),
            )
        self._paint_text_box(
            painter,
            QPointF(22.0, 35.0),
            f"{labels[self.view_mode]}｜按平面前后关系遮挡｜"
            "灰白墙体・浅蓝窗体・灰色栏杆",
            QColor("#334155"),
        )

    def _elevation_nearness(self, entity) -> float:
        """Larger value means closer to the active elevation observer."""
        endpoint_scores: list[float]
        if self.view_mode == "north":
            endpoint_scores = [entity.start.y, entity.end.y]
        elif self.view_mode == "south":
            endpoint_scores = [-entity.start.y, -entity.end.y]
        elif self.view_mode == "east":
            endpoint_scores = [entity.start.x, entity.end.x]
        elif self.view_mode == "west":
            endpoint_scores = [-entity.start.x, -entity.end.x]
        else:
            endpoint_scores = [0.0]
        return max(endpoint_scores)

    def _elevation_wall_layer_priority(self, wall: Wall) -> int:
        """
        At an equal depth, paint an edge-on wall first and a face-on wall last.

        Therefore a north/south-facing wall end is visible in an east/west
        elevation only when no face-on wall at that depth covers it.
        """
        centerline_span = abs(
            self._project_elevation_x(wall.end)
            - self._project_elevation_x(wall.start)
        )
        return 0 if centerline_span <= 1.0 else 1

    def _paint_elevation_wall(self, painter: QPainter, wall: Wall) -> None:
        left, right = self._elevation_wall_horizontal_bounds(wall)
        rect = QRectF(
            self.world_to_screen(Point(left, wall.height_mm)),
            self.world_to_screen(Point(right, 0.0)),
        ).normalized()
        painter.setPen(QPen(WALL_EDGE, 1.2))
        painter.setBrush(WALL_FILL)
        painter.drawRect(rect)

        painter.setBrush(WINDOW_COLOR)
        painter.setPen(QPen(WINDOW_EDGE, 1.3))
        for window in self.document.windows:
            if window.wall_id != wall.id:
                continue
            start = wall.point_at(window.offset_mm)
            end = wall.point_at(window.end_offset_mm)
            window_x1 = self._project_elevation_x(start)
            window_x2 = self._project_elevation_x(end)
            window_left, window_right = sorted((window_x1, window_x2))
            bottom = window.sill_height_mm
            top = window.sill_height_mm + window.height_mm
            window_rect = QRectF(
                self.world_to_screen(Point(window_left, top)),
                self.world_to_screen(Point(window_right, bottom)),
            ).normalized()
            painter.drawRect(window_rect)
            painter.drawLine(
                window_rect.topLeft(),
                window_rect.bottomRight(),
            )
            painter.drawLine(
                window_rect.topRight(),
                window_rect.bottomLeft(),
            )

    def _paint_elevation_railing(
        self,
        painter: QPainter,
        railing: Railing,
    ) -> None:
        x1 = self._project_elevation_x(railing.start)
        x2 = self._project_elevation_x(railing.end)
        left, right = sorted((x1, x2))
        rect = QRectF(
            self.world_to_screen(Point(left, railing.height_mm)),
            self.world_to_screen(Point(right, 0.0)),
        ).normalized()
        railing_fill = QColor("#c9cdd2")
        railing_fill.setAlpha(150)
        painter.setBrush(railing_fill)
        painter.setPen(QPen(RAILING_COLOR, 1.4))
        painter.drawRect(rect)

    @staticmethod
    def _wall_offsets(wall: Wall) -> tuple[float, float]:
        if wall.axis == "left":
            return 0.0, wall.width_mm
        if wall.axis == "right":
            return -wall.width_mm, 0.0
        return -wall.width_mm / 2.0, wall.width_mm / 2.0

    def _offset_point(self, point: Point, wall: Wall, offset: float) -> Point:
        nx, ny = wall.normal
        return Point(point.x + nx * offset, point.y + ny * offset)

    def _wall_polygon(
        self,
        wall: Wall,
        start_distance: float = 0.0,
        end_distance: Optional[float] = None,
        expand: float = 0.0,
        joined_ends: bool = False,
    ) -> QPolygonF:
        if end_distance is None:
            end_distance = wall.length_mm
        if joined_ends:
            start_distance -= self._connection_extension(
                wall.start,
                wall.id,
                self.document.walls,
            )
            end_distance += self._connection_extension(
                wall.end,
                wall.id,
                self.document.walls,
            )
        start = wall.point_at(start_distance)
        end = wall.point_at(end_distance)
        low, high = self._wall_offsets(wall)
        low -= expand
        high += expand
        return QPolygonF([
            self.world_to_screen(self._offset_point(start, wall, low)),
            self.world_to_screen(self._offset_point(end, wall, low)),
            self.world_to_screen(self._offset_point(end, wall, high)),
            self.world_to_screen(self._offset_point(start, wall, high)),
        ])

    @staticmethod
    def _connection_extension(
        point: Point,
        entity_id: str,
        entities: list,
    ) -> float:
        extension = 0.0
        for other in entities:
            if other.id == entity_id:
                continue
            virtual = (
                other
                if isinstance(other, Wall)
                else Wall(
                    other.start,
                    other.end,
                    width_mm=other.width_mm,
                    id=other.id,
                )
            )
            _projected, _along, distance = virtual.project(point)
            if distance <= 1.0:
                extension = max(extension, virtual.width_mm / 2.0)
        return extension

    @staticmethod
    def _polygon_path(polygon: QPolygonF) -> QPainterPath:
        path = QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()
        return path

    @staticmethod
    def _infinite_line_intersection(
        first_point: Point,
        first_direction: tuple[float, float],
        second_point: Point,
        second_direction: tuple[float, float],
    ) -> Optional[Point]:
        ax, ay = first_direction
        bx, by = second_direction
        denominator = ax * by - ay * bx
        if abs(denominator) <= 1e-9:
            return None
        qx = second_point.x - first_point.x
        qy = second_point.y - first_point.y
        distance = (qx * by - qy * bx) / denominator
        return Point(
            first_point.x + ax * distance,
            first_point.y + ay * distance,
        )

    @staticmethod
    def _convex_hull(points: list[Point]) -> list[Point]:
        unique = sorted({(point.x, point.y) for point in points})
        if len(unique) <= 2:
            return [Point(x, y) for x, y in unique]

        def turn(first, second, third) -> float:
            return (
                (second[0] - first[0]) * (third[1] - first[1])
                - (second[1] - first[1]) * (third[0] - first[0])
            )

        lower = []
        for point in unique:
            while len(lower) >= 2 and turn(
                lower[-2], lower[-1], point
            ) <= 0.0:
                lower.pop()
            lower.append(point)
        upper = []
        for point in reversed(unique):
            while len(upper) >= 2 and turn(
                upper[-2], upper[-1], point
            ) <= 0.0:
                upper.pop()
            upper.append(point)
        return [
            Point(x, y)
            for x, y in lower[:-1] + upper[:-1]
        ]

    def _wall_joint_polygons(self) -> list[QPolygonF]:
        """Build axis-aware solid caps at wall graph nodes.

        The former implementation extended every wall symmetrically by half
        the neighbouring width.  That only works for centred axes and creates
        visible wall heads for left/right axes.  Here each physical boundary
        line is intersected with the neighbouring boundary lines, so the cap
        follows the actual offset side selected by the user.
        """
        tolerance = 1.0
        nodes: list[tuple[Point, list[Wall]]] = []
        for wall in self.document.walls:
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

        polygons: list[QPolygonF] = []
        for node, incident in nodes:
            if len(incident) < 2:
                continue
            non_parallel = any(
                abs(
                    first.direction[0] * second.direction[1]
                    - first.direction[1] * second.direction[0]
                ) > 1e-6
                for index, first in enumerate(incident)
                for second in incident[index + 1:]
            )
            if not non_parallel:
                continue
            candidates: list[Point] = []
            max_width = max(wall.width_mm for wall in incident)
            boundaries: list[
                tuple[Point, tuple[float, float], Wall]
            ] = []
            for wall in incident:
                for offset in self._wall_offsets(wall):
                    boundary_point = self._offset_point(node, wall, offset)
                    candidates.append(boundary_point)
                    boundaries.append(
                        (boundary_point, wall.direction, wall)
                    )
            for index, first in enumerate(boundaries):
                for second in boundaries[index + 1:]:
                    if first[2].id == second[2].id:
                        continue
                    intersection = self._infinite_line_intersection(
                        first[0],
                        first[1],
                        second[0],
                        second[1],
                    )
                    if (
                        intersection is not None
                        and intersection.distance_to(node)
                        <= max(max_width * 6.0, 10.0)
                    ):
                        candidates.append(intersection)
            hull = self._convex_hull(candidates)
            if len(hull) >= 3:
                polygons.append(QPolygonF([
                    self.world_to_screen(point) for point in hull
                ]))
        return polygons

    def _paint_wall_network(self, painter: QPainter) -> None:
        """Paint touching wall strips as one union so corners have no seams."""
        network = QPainterPath()
        for wall in self.document.walls:
            piece = self._polygon_path(
                self._wall_polygon(wall)
            )
            network = piece if network.isEmpty() else network.united(piece)
        for polygon in self._wall_joint_polygons():
            piece = self._polygon_path(polygon)
            network = piece if network.isEmpty() else network.united(piece)
        painter.setBrush(WALL_FILL)
        painter.setPen(QPen(WALL_EDGE, 1.35))
        painter.drawPath(network)
        painter.setPen(QPen(WALL_AXIS, 0.75, Qt.PenStyle.DashLine))
        for wall in self.document.walls:
            painter.drawLine(
                self.world_to_screen(wall.start),
                self.world_to_screen(wall.end),
            )

    def _paint_railing_network(self, painter: QPainter) -> None:
        """Union connected railing strips so both edge lines remain continuous."""
        network = QPainterPath()
        for railing in self.document.railings:
            virtual = Wall(
                railing.start,
                railing.end,
                width_mm=railing.width_mm,
            )
            start = -self._connection_extension(
                railing.start,
                railing.id,
                self.document.railings,
            )
            end = railing.length_mm + self._connection_extension(
                railing.end,
                railing.id,
                self.document.railings,
            )
            piece = self._polygon_path(
                self._wall_polygon(virtual, start, end)
            )
            network = piece if network.isEmpty() else network.united(piece)
        painter.setBrush(QColor("#d7dade"))
        painter.setPen(QPen(RAILING_COLOR, 1.5))
        painter.drawPath(network)

    def _paint_wall(self, painter: QPainter, wall: Wall) -> None:
        painter.setBrush(WALL_FILL)
        painter.setPen(QPen(
            SELECTION_COLOR if wall.id in self._selected_ids else WALL_EDGE,
            1.4,
        ))
        painter.drawPolygon(self._wall_polygon(wall))
        painter.setPen(QPen(WALL_AXIS, 0.8, Qt.PenStyle.DashLine))
        painter.drawLine(
            self.world_to_screen(wall.start),
            self.world_to_screen(wall.end),
        )

    def _paint_window(self, painter: QPainter, window: Window) -> None:
        wall = self.document.wall_by_id(window.wall_id)
        if wall is None:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPolygon(self._wall_polygon(
            wall,
            window.offset_mm,
            window.end_offset_mm,
            expand=35.0,
        ))
        low, high = self._wall_offsets(wall)
        start = wall.point_at(window.offset_mm)
        end = wall.point_at(window.end_offset_mm)
        selected = window.id in self._selected_ids
        pen = QPen(SELECTION_COLOR if selected else WINDOW_COLOR, 2.0)
        painter.setPen(pen)
        for ratio in (0.08, 0.36, 0.64, 0.92):
            offset = low + (high - low) * ratio
            painter.drawLine(
                self.world_to_screen(self._offset_point(start, wall, offset)),
                self.world_to_screen(self._offset_point(end, wall, offset)),
            )
        painter.setPen(QPen(WINDOW_EDGE if not selected else SELECTION_COLOR, 1.3))
        for endpoint in (start, end):
            painter.drawLine(
                self.world_to_screen(self._offset_point(endpoint, wall, low)),
                self.world_to_screen(self._offset_point(endpoint, wall, high)),
            )

    def _paint_railing(self, painter: QPainter, railing: Railing) -> None:
        virtual = Wall(railing.start, railing.end, width_mm=railing.width_mm)
        nx, ny = virtual.normal
        color = (
            SELECTION_COLOR
            if railing.id in self._selected_ids
            else RAILING_COLOR
        )
        painter.setPen(QPen(color, 1.7))
        for offset in (-railing.width_mm / 2.0, railing.width_mm / 2.0):
            painter.drawLine(
                self.world_to_screen(Point(
                    railing.start.x + nx * offset,
                    railing.start.y + ny * offset,
                )),
                self.world_to_screen(Point(
                    railing.end.x + nx * offset,
                    railing.end.y + ny * offset,
                )),
            )

    def _paint_line(self, painter: QPainter, line: Line) -> None:
        color = (
            SELECTION_COLOR if line.id in self._selected_ids else LINE_COLOR
        )
        painter.setPen(QPen(color, 1.4))
        painter.drawLine(
            self.world_to_screen(line.start),
            self.world_to_screen(line.end),
        )

    def _paint_selection(self, painter: QPainter) -> None:
        if not self._selected_ids:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(SELECTION_COLOR, 2.0))
        for wall in self.document.walls:
            if wall.id not in self._selected_ids:
                continue
            painter.drawPolygon(self._wall_polygon(wall, expand=20.0))
        for railing in self.document.railings:
            if railing.id not in self._selected_ids:
                continue
            virtual = Wall(
                railing.start,
                railing.end,
                width_mm=railing.width_mm,
            )
            painter.drawPolygon(self._wall_polygon(virtual, expand=20.0))
        for line in self.document.lines:
            if line.id not in self._selected_ids:
                continue
            painter.drawLine(
                self.world_to_screen(line.start),
                self.world_to_screen(line.end),
            )
        painter.setBrush(SELECTION_COLOR)
        painter.setPen(Qt.PenStyle.NoPen)
        for wall in self.document.walls:
            if wall.id not in self._selected_ids:
                continue
            for point in (wall.start, wall.end):
                screen = self.world_to_screen(point)
                painter.drawRect(QRectF(
                    screen.x() - 4.0,
                    screen.y() - 4.0,
                    8.0,
                    8.0,
                ))
        for window in self.document.windows:
            if window.id not in self._selected_ids:
                continue
            wall = self.document.wall_by_id(window.wall_id)
            if wall is None:
                continue
            for offset in (window.offset_mm, window.end_offset_mm):
                screen = self.world_to_screen(wall.point_at(offset))
                painter.drawRect(QRectF(
                    screen.x() - 5.0,
                    screen.y() - 5.0,
                    10.0,
                    10.0,
                ))
        for entity in (*self.document.railings, *self.document.lines):
            if entity.id not in self._selected_ids:
                continue
            for point in (entity.start, entity.end):
                screen = self.world_to_screen(point)
                painter.drawRect(QRectF(
                    screen.x() - 4.0,
                    screen.y() - 4.0,
                    8.0,
                    8.0,
                ))

    def _paint_selection_box(self, painter: QPainter) -> None:
        if self._selection_start is None or self._selection_current is None:
            return
        rect = QRectF(self._selection_start, self._selection_current).normalized()
        crossing = self._selection_current.x() < self._selection_start.x()
        color = QColor("#16a34a") if crossing else QColor("#2563eb")
        fill = QColor(color)
        fill.setAlpha(35)
        painter.fillRect(rect, fill)
        style = Qt.PenStyle.DashLine if crossing else Qt.PenStyle.SolidLine
        painter.setPen(QPen(color, 1.2, style))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _dimension_geometry(
        self,
        dimension: DimensionChain,
    ) -> tuple[Wall, list[tuple[float, Point, Point]]]:
        baseline = Wall(dimension.points[0], dimension.points[1])
        dx, dy = baseline.direction
        nx, ny = baseline.normal
        projected = []
        for point in dimension.points:
            along = (
                (point.x - baseline.start.x) * dx
                + (point.y - baseline.start.y) * dy
            )
            base = Point(
                baseline.start.x + dx * along,
                baseline.start.y + dy * along,
            )
            dim_point = Point(
                base.x + nx * dimension.offset_mm,
                base.y + ny * dimension.offset_mm,
            )
            projected.append((along, point, dim_point))
        projected.sort(key=lambda item: item[0])
        return baseline, projected

    def _paint_dimension_chain(
        self,
        painter: QPainter,
        dimension: DimensionChain,
    ) -> None:
        if len(dimension.points) < 2:
            return
        _baseline, items = self._dimension_geometry(dimension)
        color = (
            SELECTION_COLOR
            if dimension.id in self._selected_ids
            else DIMENSION_COLOR
        )
        painter.setPen(QPen(color, 1.1))
        for _along, source, target in items:
            painter.drawLine(
                self.world_to_screen(source),
                self.world_to_screen(target),
            )
            screen = self.world_to_screen(target)
            painter.drawLine(
                screen + QPointF(-4.0, 4.0),
                screen + QPointF(4.0, -4.0),
            )
        for first, second in zip(items, items[1:]):
            painter.drawLine(
                self.world_to_screen(first[2]),
                self.world_to_screen(second[2]),
            )
            distance = abs(second[0] - first[0])
            midpoint = Point(
                (first[2].x + second[2].x) / 2.0,
                (first[2].y + second[2].y) / 2.0,
            )
            self._paint_text_box(
                painter,
                self.world_to_screen(midpoint) + QPointF(3.0, -5.0),
                f"{distance:.0f}",
                color,
            )

    def _paint_preview(self, painter: QPainter) -> None:
        if (
            self._grip_window_id is not None
            and self._grip_window_preview_offset is not None
        ):
            window = self.document.entity_by_id(self._grip_window_id)
            if isinstance(window, Window):
                wall = self.document.wall_by_id(window.wall_id)
                if wall is not None:
                    fixed_offset = (
                        window.end_offset_mm
                        if self._grip_window_endpoint == "start"
                        else window.offset_mm
                    )
                    start_offset, end_offset = sorted((
                        fixed_offset,
                        self._grip_window_preview_offset,
                    ))
                    start = wall.point_at(start_offset)
                    end = wall.point_at(end_offset)
                    painter.setPen(QPen(
                        SELECTION_COLOR,
                        2.2,
                        Qt.PenStyle.DashLine,
                    ))
                    painter.drawLine(
                        self.world_to_screen(start),
                        self.world_to_screen(end),
                    )
                    midpoint = wall.point_at(
                        (start_offset + end_offset) / 2.0
                    )
                    self._paint_text_box(
                        painter,
                        self.world_to_screen(midpoint) + QPointF(8.0, -12.0),
                        f"窗宽 {end_offset - start_offset:.0f} mm",
                        SELECTION_COLOR,
                    )
        if (
            self._grip_wall_id is not None
            and self._grip_preview is not None
        ):
            wall = self.document.wall_by_id(self._grip_wall_id)
            if wall is not None:
                fixed = (
                    wall.end
                    if self._grip_endpoint == "start"
                    else wall.start
                )
                painter.setPen(QPen(
                    SELECTION_COLOR,
                    2.0,
                    Qt.PenStyle.DashLine,
                ))
                painter.drawLine(
                    self.world_to_screen(fixed),
                    self.world_to_screen(self._grip_preview),
                )
                midpoint = Point(
                    (fixed.x + self._grip_preview.x) / 2.0,
                    (fixed.y + self._grip_preview.y) / 2.0,
                )
                self._paint_text_box(
                    painter,
                    self.world_to_screen(midpoint) + QPointF(8.0, -10.0),
                    f"{fixed.distance_to(self._grip_preview):.0f} mm",
                    SELECTION_COLOR,
                )
        if (
            self.mode in {"wall", "railing", "line"}
            and self._last_point is not None
            and self._snap_world is not None
        ):
            color = {
                "wall": QColor("#2563eb"),
                "railing": RAILING_COLOR,
                "line": LINE_COLOR,
            }[self.mode]
            painter.setPen(QPen(color, 2.0, Qt.PenStyle.DashLine))
            painter.drawLine(
                self.world_to_screen(self._last_point),
                self.world_to_screen(self._snap_world),
            )
            length = self._last_point.distance_to(self._snap_world)
            midpoint = Point(
                (self._last_point.x + self._snap_world.x) / 2.0,
                (self._last_point.y + self._snap_world.y) / 2.0,
            )
            self._paint_text_box(
                painter,
                self.world_to_screen(midpoint) + QPointF(8.0, -10.0),
                f"{length:.0f} mm",
                color,
            )
        if (
            self.mode in {"move_target", "copy_target", "mirror_second"}
            and self._edit_base is not None
            and self._snap_world is not None
        ):
            color = QColor("#7c3aed")
            painter.setPen(QPen(color, 1.6, Qt.PenStyle.DashLine))
            painter.drawLine(
                self.world_to_screen(self._edit_base),
                self.world_to_screen(self._snap_world),
            )
            label = (
                "镜像轴"
                if self.mode == "mirror_second"
                else (
                    f"位移 {self._edit_base.distance_to(self._snap_world):.0f} mm"
                )
            )
            midpoint = Point(
                (self._edit_base.x + self._snap_world.x) / 2.0,
                (self._edit_base.y + self._snap_world.y) / 2.0,
            )
            self._paint_text_box(
                painter,
                self.world_to_screen(midpoint) + QPointF(8.0, -9.0),
                label,
                color,
            )
        if self.mode == "window" and self._window_preview is not None:
            wall, offset = self._window_preview
            width = float(self.window_params["width_mm"])
            start = wall.point_at(offset)
            end = wall.point_at(offset + width)
            painter.setPen(QPen(QColor("#2563eb"), 5.0))
            painter.drawLine(
                self.world_to_screen(start),
                self.world_to_screen(end),
            )
            left = offset
            right = wall.length_mm - offset - width
            self._paint_dimension(
                painter,
                wall.start,
                start,
                f"{left:.0f}",
                wall,
                -max(wall.width_mm, 200.0),
            )
            self._paint_dimension(
                painter,
                end,
                wall.end,
                f"{right:.0f}",
                wall,
                -max(wall.width_mm, 200.0),
            )
            self._paint_text_box(
                painter,
                self.world_to_screen(Point(
                    (start.x + end.x) / 2.0,
                    (start.y + end.y) / 2.0,
                )) + QPointF(6.0, -22.0),
                f"窗宽 {width:.0f}",
                QColor("#1d4ed8"),
            )
        if (
            self.mode in {"dimension_second", "dimension_continue"}
            and self._dimension_points
        ):
            painter.setPen(QPen(SELECTION_COLOR, 1.2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor("#ffffff"))
            if self.mode == "dimension_second":
                preview_points = list(self._dimension_points)
                if self._snap_world is not None:
                    preview_points.append(self._snap_world)
            else:
                preview_points = []
            for first, second in zip(preview_points, preview_points[1:]):
                painter.drawLine(
                    self.world_to_screen(first),
                    self.world_to_screen(second),
                )
            for point in self._dimension_points:
                screen = self.world_to_screen(point)
                painter.drawEllipse(screen, 4.0, 4.0)
        if (
            self.mode == "dimension_place"
            and len(self._dimension_points) >= 2
        ):
            preview = DimensionChain(
                points=list(self._dimension_points),
                offset_mm=self._dimension_offset_preview,
                id="dimension_preview",
            )
            self._paint_dimension_chain(painter, preview)
        if (
            self.mode == "dimension_continue"
            and self._snap_world is not None
            and self._active_dimension_id is not None
        ):
            dimension = self.document.dimension_by_id(
                self._active_dimension_id
            )
            if dimension is not None and not any(
                self._snap_world.distance_to(point) <= 1.0
                for point in dimension.points
            ):
                preview = DimensionChain(
                    points=list(dimension.points) + [self._snap_world],
                    offset_mm=dimension.offset_mm,
                    id=dimension.id,
                )
                self._paint_dimension_chain(painter, preview)

    def _paint_crosshair(self, painter: QPainter) -> None:
        if self._snap_world is None:
            return
        screen = self.world_to_screen(self._snap_world)
        painter.setPen(QPen(QColor("#16a34a"), 1.3))
        if self._snap_kind in {"midpoint", "wall_edge_midpoint"}:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([
                screen + QPointF(0.0, -6.0),
                screen + QPointF(6.0, 5.0),
                screen + QPointF(-6.0, 5.0),
            ]))
            label = (
                "墙边中点"
                if self._snap_kind == "wall_edge_midpoint"
                else "中点"
            )
        elif self._snap_kind == "intersection":
            painter.drawLine(
                screen + QPointF(-5.0, -5.0),
                screen + QPointF(5.0, 5.0),
            )
            painter.drawLine(
                screen + QPointF(-5.0, 5.0),
                screen + QPointF(5.0, -5.0),
            )
            label = "交点"
        elif self._snap_kind == "perpendicular":
            painter.drawPolyline(QPolygonF([
                screen + QPointF(-6.0, -6.0),
                screen + QPointF(-6.0, 5.0),
                screen + QPointF(5.0, 5.0),
            ]))
            label = "垂足"
        elif self._snap_kind == "nearest":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([
                screen + QPointF(0.0, -6.0),
                screen + QPointF(6.0, 0.0),
                screen + QPointF(0.0, 6.0),
                screen + QPointF(-6.0, 0.0),
            ]))
            label = "最近点"
        else:
            painter.drawRect(QRectF(
                screen.x() - 5.0,
                screen.y() - 5.0,
                10.0,
                10.0,
            ))
            label = {
                "endpoint": "端点",
                "wall_edge_endpoint": "墙边端点",
                "wall_axis": "墙轴线",
                "window_axis": "窗墙轴线",
                "ortho": "正交",
                "grid": "栅格",
            }.get(self._snap_kind, "")
        self._paint_text_box(
            painter,
            screen + QPointF(10.0, 18.0),
            (
                f"{label} "
                f"{self._snap_world.x:.0f}, {self._snap_world.y:.0f}"
            ).strip(),
            QColor("#166534"),
        )

    def _paint_dimension(
        self,
        painter: QPainter,
        start: Point,
        end: Point,
        text: str,
        wall: Wall,
        normal_offset: float,
    ) -> None:
        first = self._offset_point(start, wall, normal_offset)
        second = self._offset_point(end, wall, normal_offset)
        painter.setPen(QPen(QColor("#16a34a"), 1.0))
        painter.drawLine(
            self.world_to_screen(first),
            self.world_to_screen(second),
        )
        nx, ny = wall.normal
        tick = 70.0
        for point in (first, second):
            painter.drawLine(
                self.world_to_screen(Point(
                    point.x - nx * tick,
                    point.y - ny * tick,
                )),
                self.world_to_screen(Point(
                    point.x + nx * tick,
                    point.y + ny * tick,
                )),
            )
        midpoint = Point(
            (first.x + second.x) / 2.0,
            (first.y + second.y) / 2.0,
        )
        self._paint_text_box(
            painter,
            self.world_to_screen(midpoint) + QPointF(0.0, -5.0),
            text,
            QColor("#166534"),
        )

    @staticmethod
    def _paint_text_box(
        painter: QPainter,
        position: QPointF,
        text: str,
        color: QColor,
    ) -> None:
        font = QFont("Microsoft YaHei", 8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        bounds = metrics.boundingRect(text)
        rect = QRectF(
            position.x() - 3.0,
            position.y() - bounds.height(),
            bounds.width() + 8.0,
            bounds.height() + 5.0,
        )
        painter.fillRect(rect, QColor(255, 255, 255, 225))
        painter.setPen(QPen(color, 1.0))
        painter.drawText(position, text)

    def _say(self, text: str) -> None:
        self.status_changed.emit(text)
