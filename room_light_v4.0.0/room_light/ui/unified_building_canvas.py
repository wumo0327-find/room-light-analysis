"""RL building view rendered by the exact embedded BP canvas."""
from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QPolygonF
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from bp_editor.canvas import DraftCanvas, WINDOW_EDGE
from bp_editor.model import Point, RoomFace, Wall, Window
from core.complex_experiments import window_selection_key
from core.complex_models import BuildingModel, Point2D
from core.space_geometry import point_in_space, space_floor_area_mm2
from io_utils.bp_bridge import document_from_building
from io_utils.model_audit import ModelAuditReport, audit_building_geometry


def _point_in_face(point: Point, face: RoomFace) -> bool:
    inside = False
    previous = face.points[-1]
    for current in face.points:
        dx = current.x - previous.x
        dy = current.y - previous.y
        length2 = dx * dx + dy * dy
        if length2 > 1e-12:
            t = max(0.0, min(1.0, (
                (point.x - previous.x) * dx
                + (point.y - previous.y) * dy
            ) / length2))
            if point.distance_to(Point(previous.x + t * dx, previous.y + t * dy)) <= 1.0:
                return True
        crosses = (current.y > point.y) != (previous.y > point.y)
        if crosses:
            x_at_y = (
                (previous.x - current.x) * (point.y - current.y)
                / (previous.y - current.y) + current.x
            )
            if x_at_y > point.x:
                inside = not inside
        previous = current
    return inside


class _ReviewDraftCanvas(DraftCanvas):
    room_pressed = pyqtSignal(int, int)
    bp_window_pressed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._face_space_ids: list[Optional[str]] = []
        self._selected_space_ids: set[str] = set()
        self._active_space_id: Optional[str] = None
        self._shading_mode = False
        self._bp_window_keys: dict[str, list[str]] = {}
        self._selected_shading_keys: set[str] = set()

    def set_review_state(
        self,
        *,
        face_space_ids: Sequence[Optional[str]],
        selected_space_ids: Sequence[str],
        active_space_id: Optional[str],
        shading_mode: bool,
        bp_window_keys: dict[str, list[str]],
        selected_shading_keys: Sequence[str],
    ) -> None:
        self._face_space_ids = list(face_space_ids)
        self._selected_space_ids = set(selected_space_ids)
        self._active_space_id = active_space_id
        self._shading_mode = bool(shading_mode)
        self._bp_window_keys = dict(bp_window_keys)
        self._selected_shading_keys = set(selected_shading_keys)
        self.update()

    def mousePressEvent(self, event) -> None:
        if (
            self.view_mode == "plan"
            and event.button() in {
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            }
        ):
            world = self.screen_to_world(event.position())
            if self._shading_mode:
                nearest = self.document.nearest_wall(
                    world, max(12.0 / max(self.scale, 1e-9), 80.0)
                )
                if nearest is not None:
                    wall, _projected, along, distance = nearest
                    candidates = [
                        window for window in self.document.windows
                        if window.wall_id == wall.id
                        and window.offset_mm - 80.0
                        <= along <= window.end_offset_mm + 80.0
                    ]
                    if candidates and distance <= max(wall.width_mm, 120.0):
                        window = min(
                            candidates,
                            key=lambda item: abs(
                                along - (item.offset_mm + item.width_mm / 2.0)
                            ),
                        )
                        self.bp_window_pressed.emit(window.id)
                return
            hits = [
                index for index, face in enumerate(self._rooms)
                if _point_in_face(world, face)
            ]
            if hits:
                self.room_pressed.emit(
                    min(hits, key=lambda index: self._rooms[index].area_mm2),
                    int(event.button().value),
                )
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        # Building view is a read-only review surface.  Parameter editing is
        # deliberately available only through the embedded BP editor so the
        # visible draft can never diverge silently from the calculation model.
        event.accept()

    def keyPressEvent(self, event) -> None:
        # Do not let BP drawing/edit shortcuts mutate this detached review
        # document.  Wheel/middle-button navigation remains available.
        event.ignore()

    def _paint_rooms(self, painter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for index, room in enumerate(self._rooms):
            space_id = (
                self._face_space_ids[index]
                if index < len(self._face_space_ids) else None
            )
            selected = space_id in self._selected_space_ids
            active = space_id == self._active_space_id
            fill = QColor("#bfdbfe" if selected else "#f8fafc")
            fill.setAlpha(130 if selected else 55)
            painter.setBrush(fill)
            painter.setPen(QPen(
                QColor("#dc2626" if active else "#2563eb"),
                2.2 if active else 1.2,
            ) if selected or active else Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([
                self.world_to_screen(point) for point in room.points
            ]))

    def _paint_window(self, painter, window: Window) -> None:
        super()._paint_window(painter, window)
        keys = set(self._bp_window_keys.get(window.id, []))
        if not (keys & self._selected_shading_keys):
            return
        wall = self.document.wall_by_id(window.wall_id)
        if wall is None:
            return
        low, high = self._wall_offsets(wall)
        start = wall.point_at(window.offset_mm)
        end = wall.point_at(window.end_offset_mm)
        painter.setPen(QPen(QColor("#f59e0b"), 3.0))
        for offset in (low, high):
            painter.drawLine(
                self.world_to_screen(self._offset_point(start, wall, offset)),
                self.world_to_screen(self._offset_point(end, wall, offset)),
            )
        painter.setPen(QPen(WINDOW_EDGE, 1.0))


class UnifiedBuildingCanvas(QWidget):
    """Same public API as the former matplotlib building canvas."""

    selection_changed = pyqtSignal(object, object)
    shading_windows_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building: Optional[BuildingModel] = None
        self._active_space_id: Optional[str] = None
        self._selected_space_ids: list[str] = []
        self._shading_window_keys: list[str] = []
        self._shading_selection_mode = False
        self._face_space_ids: list[Optional[str]] = []
        self._bp_window_keys: dict[str, list[str]] = {}
        self.audit_report: Optional[ModelAuditReport] = None
        self.canvas = _ReviewDraftCanvas()
        self.canvas.room_pressed.connect(self._on_room_pressed)
        self.canvas.bp_window_pressed.connect(self._on_window_pressed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def _map_faces(self, building: BuildingModel) -> list[Optional[str]]:
        by_index = {
            int(space.metadata["source_face_index"]): space.id
            for space in building.spaces()
            if str(space.metadata.get("source_face_index", "")).isdigit()
        }
        result = [by_index.get(index) for index in range(len(self.canvas._rooms))]

        # v4 projects exported from BP contain an exact source-face index.  A
        # legacy rlproj does not, so spatially match its reconstructed BP face
        # to the calculation space instead of leaving the room unselectable.
        spaces = building.spaces()
        used = {space_id for space_id in result if space_id}
        for index, face in enumerate(self.canvas._rooms):
            if result[index] is not None or len(face.points) < 3:
                continue
            twice_area = 0.0
            centroid_x = 0.0
            centroid_y = 0.0
            for start, end in zip(face.points, face.points[1:] + face.points[:1]):
                cross = start.x * end.y - end.x * start.y
                twice_area += cross
                centroid_x += (start.x + end.x) * cross
                centroid_y += (start.y + end.y) * cross
            if abs(twice_area) > 1e-9:
                probe = Point2D(
                    centroid_x / (3.0 * twice_area),
                    centroid_y / (3.0 * twice_area),
                )
            else:
                probe = Point2D(
                    sum(point.x for point in face.points) / len(face.points),
                    sum(point.y for point in face.points) / len(face.points),
                )
            candidates = [
                space for space in spaces
                if space.id not in used
                and point_in_space(probe, space, include_boundary=True)
            ]
            if candidates:
                target = min(candidates, key=space_floor_area_mm2)
                result[index] = target.id
                used.add(target.id)
        return result

    @staticmethod
    def _window_map(building: BuildingModel) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for space in building.spaces():
            for wall in space.wall_segments():
                if wall.boundary_type not in {"exterior", "ground"}:
                    continue
                for opening in wall.windows():
                    source_id = opening.metadata.get("source_bp_window_id")
                    if source_id:
                        mapping.setdefault(str(source_id), []).append(
                            window_selection_key(space, wall, opening)
                        )
        return mapping

    def set_model(
        self,
        building: BuildingModel,
        active_space_id: Optional[str],
        selected_space_ids: Optional[Sequence[str]] = None,
        shading_window_keys: Optional[Sequence[str]] = None,
    ) -> None:
        self._building = building
        self._active_space_id = active_space_id
        self._selected_space_ids = list(selected_space_ids or [])
        if shading_window_keys is not None:
            self._shading_window_keys = [str(item) for item in shading_window_keys]
        document, _note = document_from_building(building)
        self.canvas.set_document(document, fit=True)
        self._face_space_ids = self._map_faces(building)
        self._bp_window_keys = self._window_map(building)
        self.audit_report = audit_building_geometry(building)
        self._sync_review_state()

    def _sync_review_state(self) -> None:
        self.canvas.set_review_state(
            face_space_ids=self._face_space_ids,
            selected_space_ids=self._selected_space_ids,
            active_space_id=self._active_space_id,
            shading_mode=self._shading_selection_mode,
            bp_window_keys=self._bp_window_keys,
            selected_shading_keys=self._shading_window_keys,
        )

    def set_view(self, view_id: str) -> None:
        mode = str(view_id)
        if mode.startswith("storey:"):
            mode = "plan"
        if mode not in {"plan", "north", "south", "east", "west"}:
            mode = "plan"
        self.canvas.set_view_mode(mode)

    def set_shading_selection_mode(self, enabled: bool) -> None:
        self._shading_selection_mode = bool(enabled)
        self._sync_review_state()

    def set_shading_window_keys(self, keys: Sequence[str]) -> None:
        self._shading_window_keys = [str(item) for item in keys]
        self._sync_review_state()

    def _on_room_pressed(self, face_index: int, button_value: int) -> None:
        if face_index >= len(self._face_space_ids):
            return
        space_id = self._face_space_ids[face_index]
        if not space_id:
            return
        selected = list(self._selected_space_ids)
        if button_value == int(Qt.MouseButton.RightButton.value):
            selected = [space_id]
        elif space_id in selected:
            selected.remove(space_id)
        else:
            selected.append(space_id)
        self._selected_space_ids = selected
        self._active_space_id = space_id if space_id in selected or not selected else selected[-1]
        self._sync_review_state()
        self.selection_changed.emit(list(selected), self._active_space_id)

    def _on_window_pressed(self, bp_window_id: str) -> None:
        keys = self._bp_window_keys.get(bp_window_id, [])
        if not keys:
            return
        selected = list(self._shading_window_keys)
        for key in keys:
            if key in selected:
                selected.remove(key)
            else:
                selected.append(key)
        self._shading_window_keys = selected
        self._sync_review_state()
        self.shading_windows_changed.emit(list(selected))
