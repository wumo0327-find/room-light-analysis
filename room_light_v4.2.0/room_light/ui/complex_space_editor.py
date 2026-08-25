"""
ui/complex_space_editor.py — Manual complex-space editor  v4.2.0

The manual geometry editor still edits one active space at a time, while the
building canvas displays every space on the current storey and supports direct
mouse multi-selection for batch analysis.
"""
from __future__ import annotations

from copy import deepcopy
from typing import List, Optional, Sequence

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.complex_models import (
    BoundaryLoop,
    BuildingModel,
    ExteriorBarrier,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.space_geometry import (
    has_geometry_errors,
    point_in_space,
    space_floor_area_mm2,
    validate_space,
)
from core.complex_thermal import wall_azimuth_deg
from core.complex_experiments import window_selection_key


def _segment_plan_polygon(
    start: Point2D,
    end: Point2D,
    width_mm: float,
    *,
    square_caps: bool = True,
) -> List[Point2D]:
    """Return a data-coordinate strip for a finite plan segment.

    Matplotlib line widths are screen units and therefore cannot represent a
    physical wall thickness.  This helper builds the actual millimetre polygon
    used by both the whole-storey view and the space-editor preview.
    """
    dx = end.x - start.x
    dy = end.y - start.y
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-9:
        return []
    dx /= length
    dy /= length
    nx, ny = -dy, dx
    half_width = max(0.0, float(width_mm)) / 2.0
    cap = half_width if square_caps else 0.0
    start_x = start.x - dx * cap
    start_y = start.y - dy * cap
    end_x = end.x + dx * cap
    end_y = end.y + dy * cap
    return [
        Point2D(start_x + nx * half_width, start_y + ny * half_width),
        Point2D(end_x + nx * half_width, end_y + ny * half_width),
        Point2D(end_x - nx * half_width, end_y - ny * half_width),
        Point2D(start_x - nx * half_width, start_y - ny * half_width),
    ]


def _add_plan_strip(
    axis,
    start: Point2D,
    end: Point2D,
    width_mm: float,
    *,
    facecolor: str,
    edgecolor: str,
    linewidth: float,
    zorder: float,
    square_caps: bool = True,
    alpha: float = 1.0,
):
    """Add one real-width plan strip and return the Polygon patch."""
    from matplotlib.patches import Polygon

    points = _segment_plan_polygon(
        start,
        end,
        width_mm,
        square_caps=square_caps,
    )
    if not points:
        return None
    patch = Polygon(
        [(point.x, point.y) for point in points],
        closed=True,
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=linewidth,
        alpha=alpha,
        zorder=zorder,
        joinstyle="miter",
    )
    axis.add_patch(patch)
    return patch


def _draw_wall_plan_strip(
    axis,
    wall: WallSegment,
    *,
    facecolor: str,
    edgecolor: str,
    linewidth: float,
    zorder: float,
):
    """Draw a wall using ``thickness_mm`` instead of a screen-width line."""
    return _add_plan_strip(
        axis,
        wall.start,
        wall.end,
        max(1.0, float(wall.thickness_mm)),
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=linewidth,
        zorder=zorder,
        square_caps=True,
    )


def _draw_window_plan_strip(
    axis,
    wall: WallSegment,
    opening: WallOpening,
    *,
    zorder: float,
    is_shading_target: bool = False,
) -> None:
    """Cut the host wall visually and draw the real glazing-plane position."""
    host_start = wall.point_at(opening.offset_mm)
    host_end = wall.point_at(opening.end_offset_mm)
    wall_width = max(1.0, float(wall.thickness_mm))

    # Cover the wall strip first, so a window is visibly an opening instead of
    # only a cyan centreline laid over an uninterrupted solid wall.
    _add_plan_strip(
        axis,
        host_start,
        host_end,
        wall_width * 1.08,
        facecolor="#f8fafc",
        edgecolor="#f8fafc",
        linewidth=0.0,
        zorder=zorder,
        square_caps=False,
    )
    glazing_start, glazing_end = _opening_plan_points(wall, opening)
    glazing_width = (
        wall_width
        if opening.plane_offset_mm <= 0.0
        else max(30.0, min(80.0, wall_width * 0.25))
    )
    _add_plan_strip(
        axis,
        glazing_start,
        glazing_end,
        glazing_width,
        facecolor="#fbbf24" if is_shading_target else "#67e8f9",
        edgecolor="#b45309" if is_shading_target else "#0891b2",
        linewidth=2.2 if is_shading_target else 1.1,
        zorder=zorder + 0.2,
        square_caps=False,
    )
    _draw_opening_offset_connectors(
        axis,
        wall,
        opening,
        glazing_start,
        glazing_end,
    )


def _wall_thickness_summary(space: SpaceModel) -> str:
    values = sorted({
        round(float(wall.thickness_mm), 3)
        for wall in space.wall_segments()
    })
    if not values:
        return "无墙段"
    if len(values) == 1:
        return f"{values[0]:.0f} mm"
    return (
        f"{values[0]:.0f}–{values[-1]:.0f} mm"
        f"（{len(values)}种）"
    )


class ComplexSpaceSummary(QWidget):
    """Main building-view sidebar for unified v4 BP/RL projects."""

    edit_requested = pyqtSignal()
    analysis_settings_requested = pyqtSignal()
    view_changed = pyqtSignal(str)
    weather_dialog_requested = pyqtSignal()
    shading_selection_mode_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        title = QLabel("建筑视图")
        title.setStyleSheet("font-size:16px;font-weight:700;color:#1d4ed8;")
        subtitle = QLabel("BP草图直接显示｜计算模型仅在内存中同步")
        subtitle.setStyleSheet("color:#64748b;padding-bottom:4px;")
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            "background:#ffffff;border:1px solid #d0d5e0;border-radius:6px;"
            "padding:10px;color:#30364a;"
        )
        self._audit = QLabel("模型核验：等待载入")
        self._audit.setWordWrap(True)
        edit = QPushButton("打开BP建筑平面编辑器（保存后立即刷新）")
        edit.setObjectName("primary_btn")
        edit.clicked.connect(self.edit_requested)
        analysis_settings = QPushButton("设置选中房间的光学与热工参数")
        analysis_settings.clicked.connect(self.analysis_settings_requested)
        self._shade_select = QPushButton("在图中选择参数化遮阳窗")
        self._shade_select.setCheckable(True)
        self._shade_select.toggled.connect(
            self.shading_selection_mode_changed
        )
        self._shade_status = QLabel("参数化遮阳位置：尚未指定")
        self._shade_status.setWordWrap(True)
        self._shade_status.setStyleSheet(
            "background:#fffbeb;border:1px solid #fde68a;border-radius:5px;"
            "padding:7px;color:#92400e;"
        )
        view_label = QLabel("查看视图")
        view_label.setStyleSheet("font-weight:600;color:#334155;padding-top:5px;")
        self._view_combo = QComboBox()
        self._view_combo.currentIndexChanged.connect(self._emit_view_changed)
        weather_label = QLabel("气象数据")
        weather_label.setStyleSheet("font-weight:600;color:#334155;padding-top:5px;")
        self._weather_summary = QLabel("尚未设置气象数据")
        self._weather_summary.setWordWrap(True)
        self._weather_summary.setStyleSheet(
            "background:#f0fdf4;border:1px solid #bbf7d0;border-radius:5px;"
            "padding:7px;color:#166534;"
        )
        weather_button = QPushButton("⚙ 调整气象数据")
        weather_button.clicked.connect(self.weather_dialog_requested)
        note = QLabel(
            "在右侧平面图中直接点击房间即可多选；再次点击取消。"
            "右键某个房间可只选择该房间。采光、热环境和参数化实验"
            "只计算当前选中的房间。墙、窗、栏杆和尺寸编辑统一在BP建模器中完成。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#6b7280;padding:8px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self._summary)
        layout.addWidget(self._audit)
        layout.addWidget(edit)
        layout.addWidget(analysis_settings)
        layout.addWidget(self._shade_select)
        layout.addWidget(self._shade_status)
        layout.addWidget(view_label)
        layout.addWidget(self._view_combo)
        layout.addWidget(weather_label)
        layout.addWidget(self._weather_summary)
        layout.addWidget(weather_button)
        layout.addWidget(note)
        layout.addStretch()

    def _emit_view_changed(self, index: int) -> None:
        view_id = self._view_combo.itemData(index)
        if view_id:
            self.view_changed.emit(str(view_id))

    def set_weather(self, weather) -> None:
        if weather is None or not weather.is_valid():
            self._weather_summary.setText("尚未设置有效气象数据")
            return
        location = weather.location or weather.source or "未命名气象数据"
        self._weather_summary.setText(
            f"{location}\n年平均室外照度 {weather.annual_avg:.0f} lux"
        )

    def set_model(
        self,
        building: BuildingModel,
        active_space_id: Optional[str],
        selected_space_ids: Optional[Sequence[str]] = None,
        selected_shading_window_count: Optional[int] = None,
    ) -> None:
        from io_utils.model_audit import audit_building_geometry
        audit = audit_building_geometry(building)
        self._audit.setText(audit.summary())
        audit_details = audit.issues + audit.warnings
        self._audit.setToolTip(
            "\n".join(audit_details) if audit_details else "模型几何核验通过"
        )
        if audit.issues:
            audit_style = "background:#fef2f2;border:1px solid #fca5a5;color:#991b1b;"
        elif audit.warnings:
            audit_style = "background:#fffbeb;border:1px solid #fde68a;color:#92400e;"
        else:
            audit_style = "background:#f0fdf4;border:1px solid #86efac;color:#166534;"
        self._audit.setStyleSheet(
            audit_style + "border-radius:6px;padding:8px;font-size:10px;"
        )
        if selected_shading_window_count is None:
            self._shade_status.setText(
                "参数化遮阳位置：兼容模式（所选房间全部外窗）"
            )
        else:
            self._shade_status.setText(
                f"参数化遮阳位置：已选 {selected_shading_window_count} 扇外窗\n"
                "橙色窗为将安装遮阳板并参与造价计算的位置"
            )
        space = building.get_space(active_space_id) if active_space_id else None
        selected_ids = [
            space_id
            for space_id in (selected_space_ids or [])
            if building.get_space(space_id) is not None
        ]
        selected_view = self._view_combo.currentData()
        self._view_combo.blockSignals(True)
        self._view_combo.clear()
        for storey in building.storeys:
            self._view_combo.addItem(
                f"{storey.name}｜房间平面图",
                f"storey:{storey.id}",
            )
        self._view_combo.addItem("北立面（BP原始画布）", "north")
        self._view_combo.addItem("南立面（BP原始画布）", "south")
        self._view_combo.addItem("东立面（BP原始画布）", "east")
        self._view_combo.addItem("西立面（BP原始画布）", "west")
        if space is None:
            self._summary.setText(
                f"建筑：{building.name}\n"
                f"已选房间：{len(selected_ids)} 个\n"
                "尚未指定当前查看房间。"
            )
            self._view_combo.setCurrentIndex(0)
            self._view_combo.blockSignals(False)
            return
        active_storey = next(
            (
                storey
                for storey in building.storeys
                if storey.get_space(space.id) is not None
            ),
            None,
        )
        storey_name = active_storey.name if active_storey else "未命名楼层"
        window_count = sum(
            len(wall.windows())
            for wall in space.wall_segments()
        )
        selected_names = [
            building.get_space(space_id).name
            for space_id in selected_ids[:4]
        ]
        selected_summary = "、".join(selected_names)
        if len(selected_ids) > 4:
            selected_summary += f" 等{len(selected_ids)}个"
        boundary_status = (
            "已确认"
            if space.metadata.get("boundary_conditions_explicit") is not False
            else "未确认，请打开分析参数核对"
        )
        self._summary.setText(
            f"建筑：{building.name}\n"
            f"楼层：{storey_name}\n"
            f"当前房间：{space.name}\n"
            f"已选房间：{len(selected_ids)} 个"
            + (f"（{selected_summary}）\n" if selected_summary else "\n")
            + f"面积：{space_floor_area_mm2(space) / 1_000_000:.2f} ㎡\n"
            f"层高：{space.height_mm / 1000:.2f} m\n"
            f"墙段：{len(space.wall_segments())} 段\n"
            f"墙厚：{_wall_thickness_summary(space)}\n"
            f"外窗：{window_count} 扇\n"
            f"上部边界：{'室外屋面' if space.roof_exposed else '相邻室内空间'}\n"
            f"下部边界：{'地面/室外' if space.floor_exposed else '相邻室内空间'}\n"
            f"上下边界状态：{boundary_status}\n"
            f"采光遮挡墙/栏杆：{len(space.exterior_barriers)} 段"
        )
        target_index = self._view_combo.findData(selected_view)
        if target_index < 0 and active_storey is not None:
            target_index = self._view_combo.findData(
                f"storey:{active_storey.id}"
            )
        self._view_combo.setCurrentIndex(max(0, target_index))
        self._view_combo.blockSignals(False)


class ComplexSpaceCanvas(QWidget):
    """Whole-storey plan with mouse room selection and wall elevations."""

    selection_changed = pyqtSignal(object, object)
    shading_windows_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building: Optional[BuildingModel] = None
        self._active_space_id: Optional[str] = None
        self._selected_space_ids: list[str] = []
        self._shading_window_keys: list[str] = []
        self._shading_selection_mode = False
        self._view_id = ""
        self.figure = Figure(facecolor="#ffffff")
        self.canvas = FigureCanvas(self.figure)
        self._click_connection = self.canvas.mpl_connect(
            "button_press_event",
            self._on_plan_click,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def set_model(
        self,
        building: BuildingModel,
        active_space_id: Optional[str],
        selected_space_ids: Optional[Sequence[str]] = None,
        shading_window_keys: Optional[Sequence[str]] = None,
    ) -> None:
        self._building = building
        self._active_space_id = active_space_id
        self._selected_space_ids = [
            space_id
            for space_id in (selected_space_ids or [])
            if building.get_space(space_id) is not None
        ]
        if shading_window_keys is not None:
            self._shading_window_keys = [
                str(value) for value in shading_window_keys
            ]
        space = building.get_space(active_space_id) if active_space_id else None
        if not self._view_id:
            storey = self._storey_for_space(active_space_id)
            if storey is None and building.storeys:
                storey = building.storeys[0]
            self._view_id = f"storey:{storey.id}" if storey else ""
        elif self._view_id.startswith("storey:"):
            if self._display_storey() is None:
                storey = self._storey_for_space(active_space_id)
                if storey is None and building.storeys:
                    storey = building.storeys[0]
                self._view_id = f"storey:{storey.id}" if storey else ""
        elif not self._view_id.startswith("storey:"):
            if space is None or space.get_wall(self._view_id) is None:
                storey = self._storey_for_space(active_space_id)
                self._view_id = f"storey:{storey.id}" if storey else ""
        self.refresh()

    def set_view(self, view_id: str) -> None:
        self._view_id = view_id or self._view_id
        self.refresh()

    def set_shading_selection_mode(self, enabled: bool) -> None:
        self._shading_selection_mode = bool(enabled)
        self.refresh()

    def set_shading_window_keys(self, keys: Sequence[str]) -> None:
        self._shading_window_keys = [str(value) for value in keys]
        self.refresh()

    def _nearest_window_key(self, event, storey) -> Optional[str]:
        """Return a glazing segment within 12 screen pixels of the click."""
        click_x = float(event.x)
        click_y = float(event.y)
        best_key = None
        best_distance = 12.0
        for space in storey.spaces:
            for wall in space.wall_segments():
                if wall.boundary_type not in {"exterior", "ground"}:
                    continue
                for opening in wall.windows():
                    start, end = _opening_plan_points(wall, opening)
                    (x1, y1), (x2, y2) = event.inaxes.transData.transform([
                        (start.x, start.y),
                        (end.x, end.y),
                    ])
                    dx, dy = x2 - x1, y2 - y1
                    length2 = dx * dx + dy * dy
                    if length2 <= 1e-12:
                        continue
                    t = max(
                        0.0,
                        min(
                            1.0,
                            ((click_x - x1) * dx + (click_y - y1) * dy)
                            / length2,
                        ),
                    )
                    distance = (
                        (click_x - (x1 + t * dx)) ** 2
                        + (click_y - (y1 + t * dy)) ** 2
                    ) ** 0.5
                    if distance < best_distance:
                        best_distance = distance
                        best_key = window_selection_key(
                            space, wall, opening
                        )
        return best_key

    def _storey_for_space(self, space_id: Optional[str]):
        if self._building is None or not space_id:
            return None
        return next(
            (
                storey
                for storey in self._building.storeys
                if storey.get_space(space_id) is not None
            ),
            None,
        )

    def _display_storey(self):
        if self._building is None:
            return None
        if self._view_id.startswith("storey:"):
            return self._building.get_storey(self._view_id.split(":", 1)[1])
        return self._storey_for_space(self._active_space_id)

    def _on_plan_click(self, event) -> None:
        if (
            self._building is None
            or not self._view_id.startswith("storey:")
            or event.inaxes is None
            or event.xdata is None
            or event.ydata is None
        ):
            return
        storey = self._display_storey()
        if storey is None:
            return
        if self._shading_selection_mode:
            key = self._nearest_window_key(event, storey)
            if key is None:
                return
            selected = list(self._shading_window_keys)
            if key in selected:
                selected.remove(key)
            else:
                selected.append(key)
            self._shading_window_keys = selected
            self.refresh()
            self.shading_windows_changed.emit(list(selected))
            return
        point = Point2D(float(event.xdata), float(event.ydata))
        hits = [
            space
            for space in storey.spaces
            if point_in_space(point, space, include_boundary=True)
        ]
        if not hits:
            return
        clicked = min(hits, key=space_floor_area_mm2)
        selected = list(self._selected_space_ids)
        if event.button == 3:
            selected = [clicked.id]
        elif clicked.id in selected:
            selected.remove(clicked.id)
        else:
            selected.append(clicked.id)
        self._selected_space_ids = selected
        # 取消当前房间后，将“当前房间”自动移到仍被选中的最后一个房间，
        # 避免右侧显示对象不在后续批量计算范围内。
        active_id = (
            clicked.id
            if clicked.id in selected or not selected
            else selected[-1]
        )
        self._active_space_id = active_id
        self.refresh()
        self.selection_changed.emit(list(selected), active_id)

    def refresh(self) -> None:
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.set_facecolor("#f8fafc")
        building = self._building
        space = (
            building.get_space(self._active_space_id)
            if building is not None and self._active_space_id
            else None
        )
        storey = self._display_storey()
        if storey is None and space is not None:
            storey = self._storey_for_space(space.id)
        if building is None or (space is None and storey is None):
            axis.text(
                0.5, 0.5, "尚未建立复杂空间",
                ha="center", va="center", transform=axis.transAxes,
                color="#64748b", fontsize=15,
            )
            axis.set_axis_off()
            self.canvas.draw()
            return

        wall = (
            space.get_wall(self._view_id)
            if space is not None and not self._view_id.startswith("storey:")
            else None
        )
        if wall is None and storey is not None:
            if not self._view_id.startswith("storey:"):
                self._view_id = f"storey:{storey.id}"
            _draw_storey_plan(
                axis,
                storey,
                selected_space_ids=self._selected_space_ids,
                active_space_id=self._active_space_id,
                shading_window_keys=self._shading_window_keys,
            )
            axis.set_title(
                (
                    f"{building.name} — {storey.name}｜点击橙色/蓝色外窗设置遮阳位置"
                    if self._shading_selection_mode
                    else f"{building.name} — {storey.name}｜点击房间多选，右键单选"
                ),
                fontsize=14,
                fontweight="bold",
                color="#1e3a8a",
            )
        else:
            _draw_wall_elevation(
                axis,
                space,
                wall,
                north_angle_deg=building.north_angle_deg,
            )
        self.figure.tight_layout()
        self.canvas.draw()


def _draw_wall_elevation(
    axis,
    space: SpaceModel,
    wall: WallSegment,
    north_angle_deg: float = 0.0,
) -> None:
    """Draw one wall in its own local length/height coordinate system."""
    from matplotlib.patches import Rectangle

    length = wall.length_mm
    height = space.height_mm
    axis.add_patch(Rectangle(
        (0.0, 0.0),
        length,
        height,
        facecolor="#f1f5f9",
        edgecolor="#1e3a8a",
        linewidth=2.2,
    ))
    for opening in wall.openings:
        is_window = opening.kind == "window"
        axis.add_patch(Rectangle(
            (opening.offset_mm, opening.sill_height_mm),
            opening.width_mm,
            opening.height_mm,
            facecolor="#bae6fd" if is_window else "#fed7aa",
            edgecolor="#0284c7" if is_window else "#c2410c",
            linewidth=2.0,
            alpha=0.9,
        ))
        axis.text(
            opening.offset_mm + opening.width_mm / 2.0,
            opening.sill_height_mm + opening.height_mm / 2.0,
            opening.name or ("窗" if is_window else "门"),
            ha="center",
            va="center",
            fontsize=9,
            color="#075985" if is_window else "#9a3412",
        )
        if is_window:
            depth = float(space.shading.overhang_depth_mm)
            gap = float(space.shading.overhang_height_mm)
            if depth > 0.0:
                board_y = min(
                    height,
                    opening.head_height_mm + max(0.0, gap),
                )
                axis.plot(
                    [opening.offset_mm, opening.end_offset_mm],
                    [board_y, board_y],
                    color="#dc2626",
                    linewidth=4,
                )
                axis.annotate(
                    f"水平遮阳 L={depth:.0f} mm",
                    (
                        opening.offset_mm + opening.width_mm / 2.0,
                        board_y,
                    ),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    color="#b91c1c",
                )
    azimuth = wall_azimuth_deg(wall, north_angle_deg)
    boundary_names = {
        "exterior": "外墙",
        "interior": "内墙",
        "adiabatic": "绝热边界",
        "ground": "接地边界",
    }
    axis.set_xlim(-0.04 * max(length, 1.0), length * 1.04)
    axis.set_ylim(-0.06 * max(height, 1.0), height * 1.12)
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, color="#e2e8f0", linewidth=0.7)
    axis.set_xlabel("沿墙长度 / mm")
    axis.set_ylabel("距楼地面高度 / mm")
    axis.set_title(
        f"{space.name}｜{wall.name or '墙立面'}｜"
        f"{boundary_names.get(wall.boundary_type, wall.boundary_type)}｜"
        f"墙厚 {wall.thickness_mm:.0f} mm｜方位角 {azimuth:.1f}°",
        fontsize=14,
        fontweight="bold",
        color="#1e3a8a",
    )


def _draw_storey_plan(
    axis,
    storey: StoreyModel,
    selected_space_ids: Sequence[str],
    active_space_id: Optional[str],
    shading_window_keys: Sequence[str] = (),
) -> None:
    """Draw every recognised room on one storey as a clickable region."""
    selected = set(selected_space_ids)
    shading_targets = set(shading_window_keys)
    storey_barriers = {}
    for space in storey.spaces:
        for barrier in space.exterior_barriers:
            key = barrier.id or (
                round(barrier.start.x, 3),
                round(barrier.start.y, 3),
                round(barrier.end.x, 3),
                round(barrier.end.y, 3),
                barrier.kind,
            )
            storey_barriers[key] = barrier
        outer = space.outer_loop()
        if outer is None or not outer.points():
            continue
        is_selected = space.id in selected
        is_active = space.id == active_space_id
        for loop in space.boundary_loops:
            points = loop.points()
            if not points:
                continue
            xs = [point.x for point in points] + [points[0].x]
            ys = [point.y for point in points] + [points[0].y]
            if loop.kind == "hole":
                facecolor = "#ffffff"
                edgecolor = "#64748b"
            else:
                facecolor = "#bfdbfe" if is_selected else "#f8fafc"
                edgecolor = (
                    "#dc2626" if is_active
                    else "#2563eb" if is_selected
                    else "#64748b"
                )
            axis.fill(
                xs,
                ys,
                facecolor=facecolor,
                edgecolor=edgecolor,
                linewidth=3.0 if is_active else 2.0,
                alpha=0.82 if is_selected else 0.62,
                zorder=1 if loop.kind == "outer" else 4,
            )
            for wall in loop.segments:
                wall_edge = (
                    "#dc2626" if is_active
                    else "#1d4ed8" if is_selected
                    else "#475569"
                )
                wall_face = (
                    "#fee2e2" if is_active
                    else "#dbeafe" if is_selected
                    else "#e5e7eb"
                )
                wall_zorder = 7 if is_active else 6 if is_selected else 5
                _draw_wall_plan_strip(
                    axis,
                    wall,
                    facecolor=wall_face,
                    edgecolor=wall_edge,
                    linewidth=1.7 if is_active else 1.2,
                    zorder=wall_zorder,
                )
                for opening in wall.windows():
                    _draw_window_plan_strip(
                        axis,
                        wall,
                        opening,
                        zorder=8,
                        is_shading_target=(
                            window_selection_key(space, wall, opening)
                            in shading_targets
                        ),
                    )
        points = outer.points()
        centre = Point2D(
            sum(point.x for point in points) / len(points),
            sum(point.y for point in points) / len(points),
        )
        if not point_in_space(centre, space, include_boundary=True):
            centre = points[0]
        # 不使用 ✓，避免部分中文字体缺字时导出图片出现方框。
        prefix = "● " if is_active else "选 " if is_selected else ""
        axis.text(
            centre.x,
            centre.y,
            f"{prefix}{space.name}\n"
            f"{space_floor_area_mm2(space) / 1_000_000:.1f} ㎡",
            ha="center",
            va="center",
            fontsize=8.5,
            fontweight="bold" if is_selected or is_active else "normal",
            color="#991b1b" if is_active else "#1e3a8a",
            bbox=dict(
                boxstyle="round,pad=0.25",
                facecolor="#ffffff",
                edgecolor=(
                    "#fca5a5" if is_active
                    else "#93c5fd" if is_selected
                    else "#e2e8f0"
                ),
                alpha=0.90,
            ),
            zorder=9,
        )
    for barrier in storey_barriers.values():
        _draw_exterior_barrier(axis, barrier, with_label=False)
    if storey_barriers:
        axis.text(
            0.995,
            0.015,
            "粉色线：采光遮挡墙/栏杆",
            transform=axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=8,
            color="#be185d",
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor="#fff1f2",
                edgecolor="#f9a8d4",
                alpha=0.92,
            ),
            zorder=12,
        )
    axis.set_aspect("equal", adjustable="datalim")
    axis.margins(0.05)
    axis.grid(True, color="#e2e8f0", linewidth=0.7)
    axis.set_xlabel("X / mm")
    axis.set_ylabel("Y / mm")


def _draw_space_plan(axis, space: SpaceModel, show_coordinates: bool = False):
    """Draw one validated or candidate space onto a matplotlib axis."""
    for loop in space.boundary_loops:
        points = loop.points()
        if not points:
            continue
        xs = [point.x for point in points] + [points[0].x]
        ys = [point.y for point in points] + [points[0].y]
        color = "#2563eb" if loop.kind == "outer" else "#64748b"
        axis.fill(
            xs,
            ys,
            facecolor="#dbeafe" if loop.kind == "outer" else "#ffffff",
            edgecolor=color,
            linewidth=2.2,
            alpha=0.55,
            zorder=1,
        )
        for index, wall in enumerate(loop.segments):
            _draw_wall_plan_strip(
                axis,
                wall,
                facecolor="#dbeafe",
                edgecolor="#1e40af",
                linewidth=1.3,
                zorder=3,
            )
            midpoint = wall.point_at(wall.length_mm / 2.0)
            axis.text(
                midpoint.x,
                midpoint.y,
                f"墙{index + 1}\n厚{wall.thickness_mm:.0f}mm",
                fontsize=8,
                color="#1e3a8a",
                ha="center",
                va="center",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor="#ffffff",
                    edgecolor="#bfdbfe",
                    alpha=0.9,
                ),
                zorder=6,
            )
            for opening in wall.windows():
                _draw_window_plan_strip(
                    axis,
                    wall,
                    opening,
                    zorder=5,
                )
                start, end = _opening_plan_points(wall, opening)
                centre = Point2D(
                    (start.x + end.x) / 2.0,
                    (start.y + end.y) / 2.0,
                )
                axis.text(
                    centre.x,
                    centre.y,
                    opening.name or "窗",
                    fontsize=7,
                    color="#0e7490",
                    ha="center",
                    va="bottom",
                    zorder=7,
                )
        if show_coordinates:
            for index, point in enumerate(points):
                axis.scatter(
                    [point.x], [point.y],
                    s=24, color="#dc2626", zorder=8,
                )
                axis.annotate(
                    f"P{index + 1}\n({point.x:.0f},{point.y:.0f})",
                    (point.x, point.y),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=7,
                    color="#7f1d1d",
                )
    for barrier in space.exterior_barriers:
        _draw_exterior_barrier(axis, barrier, with_label=True)
    axis.set_aspect("equal", adjustable="datalim")
    axis.grid(True, color="#e2e8f0", linewidth=0.7)
    axis.set_xlabel("X / mm")
    axis.set_ylabel("Y / mm")


def _draw_exterior_barrier(
    axis,
    barrier: ExteriorBarrier,
    *,
    with_label: bool,
) -> None:
    """Draw an exterior daylight obstruction using the PDF's pink language."""
    is_open = barrier.kind in {"railing", "screen"}
    color = "#ec4899" if is_open else "#d97706"
    axis.plot(
        [barrier.start.x, barrier.end.x],
        [barrier.start.y, barrier.end.y],
        color=color,
        linewidth=2.4 if is_open else 4.0,
        linestyle="--" if is_open else "-",
        solid_capstyle="round",
        zorder=10,
    )
    if not with_label:
        return
    midpoint_x = (barrier.start.x + barrier.end.x) / 2.0
    midpoint_y = (barrier.start.y + barrier.end.y) / 2.0
    transmission = barrier.visible_transmittance
    axis.text(
        midpoint_x,
        midpoint_y,
        barrier.name or (
            f"栏杆（透光比{transmission:.2f}）"
            if is_open else "窗外墙体"
        ),
        fontsize=7,
        color="#be185d" if is_open else "#92400e",
        ha="center",
        va="bottom",
        zorder=11,
    )


def _opening_plan_points(wall: WallSegment, opening: WallOpening):
    """Return the real plan endpoints, including a recessed façade offset."""
    start = wall.point_at(opening.offset_mm)
    end = wall.point_at(opening.end_offset_mm)
    offset = float(opening.plane_offset_mm)
    if offset <= 0.0:
        return start, end
    outward_x, outward_y = wall.outward_normal
    return (
        Point2D(start.x + outward_x * offset, start.y + outward_y * offset),
        Point2D(end.x + outward_x * offset, end.y + outward_y * offset),
    )


def _draw_opening_offset_connectors(
    axis,
    wall: WallSegment,
    opening: WallOpening,
    start: Point2D,
    end: Point2D,
) -> None:
    if opening.plane_offset_mm <= 0.0:
        return
    host_start = wall.point_at(opening.offset_mm)
    host_end = wall.point_at(opening.end_offset_mm)
    for host, glazing in ((host_start, start), (host_end, end)):
        axis.plot(
            [host.x, glazing.x],
            [host.y, glazing.y],
            color="#94a3b8",
            linewidth=0.9,
            linestyle=":",
            zorder=9,
        )


class ComplexSpaceEditorDialog(QDialog):
    """Create or edit one active arbitrary-polygon space."""

    def __init__(
        self,
        building: BuildingModel,
        active_space_id: Optional[str],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("复杂空间模型编辑器")
        self.resize(1180, 760)
        self._source_building = deepcopy(building)
        self._active_space_id = active_space_id
        self.result_building: Optional[BuildingModel] = None
        self.result_active_space_id: Optional[str] = None

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        controls = QWidget()
        controls.setMinimumWidth(500)
        control_layout = QVBoxLayout(controls)
        form = QFormLayout()
        self.building_name = QLineEdit()
        self.storey_name = QLineEdit()
        self.space_name = QLineEdit()
        self.north_angle = _spin(-360.0, 360.0, 1.0, "°")
        self.space_height = _spin(100.0, 20_000.0, 100.0, " mm")
        form.addRow("建筑名称", self.building_name)
        form.addRow("楼层名称", self.storey_name)
        form.addRow("空间名称", self.space_name)
        form.addRow("正北偏角", self.north_angle)
        form.addRow("空间层高", self.space_height)
        control_layout.addLayout(form)

        template_row = QHBoxLayout()
        rectangle = QPushButton("矩形模板")
        l_shape = QPushButton("L形模板")
        rectangle.clicked.connect(self._set_rectangle_template)
        l_shape.clicked.connect(self._set_l_template)
        template_row.addWidget(rectangle)
        template_row.addWidget(l_shape)
        template_row.addStretch()
        control_layout.addLayout(template_row)

        control_layout.addWidget(QLabel(
            "外边界顶点（逆时针；墙类型指本点到下一点）"
        ))
        self.vertex_table = QTableWidget(0, 3)
        self.vertex_table.setHorizontalHeaderLabels([
            "X / mm", "Y / mm", "墙类型",
        ])
        self.vertex_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.vertex_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.vertex_table.itemChanged.connect(self._refresh_preview)
        control_layout.addWidget(self.vertex_table, 2)
        vertex_buttons = QHBoxLayout()
        add_vertex = QPushButton("添加顶点")
        remove_vertex = QPushButton("删除顶点")
        add_vertex.clicked.connect(self._add_vertex)
        remove_vertex.clicked.connect(self._remove_vertex)
        vertex_buttons.addWidget(add_vertex)
        vertex_buttons.addWidget(remove_vertex)
        vertex_buttons.addStretch()
        control_layout.addLayout(vertex_buttons)

        control_layout.addWidget(QLabel(
            "外窗（墙序号对应上方相邻顶点形成的墙段）"
        ))
        self.window_table = QTableWidget(0, 7)
        self.window_table.setHorizontalHeaderLabels([
            "墙序号", "距墙起点/mm", "宽/mm", "窗台/mm", "高/mm", "透光率",
            "窗面外移/mm",
        ])
        self.window_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.window_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.window_table.itemChanged.connect(self._refresh_preview)
        control_layout.addWidget(self.window_table, 2)
        window_buttons = QHBoxLayout()
        add_window = QPushButton("添加窗户")
        remove_window = QPushButton("删除窗户")
        add_window.clicked.connect(self._add_window)
        remove_window.clicked.connect(self._remove_window)
        window_buttons.addWidget(add_window)
        window_buttons.addWidget(remove_window)
        window_buttons.addStretch()
        control_layout.addLayout(window_buttons)
        splitter.addWidget(controls)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        self.figure = Figure(facecolor="#ffffff")
        self.canvas = FigureCanvas(self.figure)
        self.issue_label = QLabel()
        self.issue_label.setWordWrap(True)
        self.issue_label.setStyleSheet(
            "background:#f8fafc;border:1px solid #d0d5e0;"
            "padding:8px;color:#334155;"
        )
        preview_layout.addWidget(self.canvas, 1)
        preview_layout.addWidget(self.issue_label)
        splitter.addWidget(preview)
        splitter.setSizes([520, 650])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "校验并应用"
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._load_model()

    def _active_space(self) -> Optional[SpaceModel]:
        if self._active_space_id:
            return self._source_building.get_space(self._active_space_id)
        spaces = self._source_building.spaces()
        return spaces[0] if spaces else None

    def _load_model(self):
        building = self._source_building
        space = self._active_space()
        storey = next(
            (
                item
                for item in building.storeys
                if space is not None and item.get_space(space.id) is not None
            ),
            building.storeys[0] if building.storeys else None,
        )
        self.building_name.setText(building.name)
        self.storey_name.setText(storey.name if storey else "首层")
        self.space_name.setText(space.name if space else "活动室")
        self.north_angle.setValue(building.north_angle_deg)
        self.space_height.setValue(space.height_mm if space else 3600.0)

        if space and space.outer_loop():
            self._set_vertex_rows(
                space.outer_loop().points(),
                [
                    wall.boundary_type
                    for wall in space.outer_loop().segments
                ],
            )
            window_rows = []
            for wall_index, wall in enumerate(
                space.outer_loop().segments,
                start=1,
            ):
                for opening in wall.windows():
                    window_rows.append([
                        wall_index,
                        opening.offset_mm,
                        opening.width_mm,
                        opening.sill_height_mm,
                        opening.height_mm,
                        opening.visible_transmittance,
                        opening.plane_offset_mm,
                    ])
            self._set_window_rows(window_rows)
        else:
            self._set_l_template()
        self._refresh_preview()

    def _set_rectangle_template(self):
        self._set_vertex_rows([
            Point2D(0, 0),
            Point2D(6000, 0),
            Point2D(6000, 6600),
            Point2D(0, 6600),
        ])
        self._set_window_rows([])
        self._refresh_preview()

    def _set_l_template(self):
        self._set_vertex_rows([
            Point2D(0, 0),
            Point2D(9000, 0),
            Point2D(9000, 3300),
            Point2D(6000, 3300),
            Point2D(6000, 6600),
            Point2D(0, 6600),
        ])
        self._set_window_rows([])
        self._refresh_preview()

    def _set_vertex_rows(
        self,
        points: List[Point2D],
        boundary_types: Optional[List[str]] = None,
    ):
        self.vertex_table.blockSignals(True)
        self.vertex_table.setRowCount(len(points))
        for row, point in enumerate(points):
            self.vertex_table.setItem(row, 0, QTableWidgetItem(f"{point.x:g}"))
            self.vertex_table.setItem(row, 1, QTableWidgetItem(f"{point.y:g}"))
            boundary_type = (
                boundary_types[row]
                if boundary_types and row < len(boundary_types)
                else "exterior"
            )
            self.vertex_table.setItem(
                row,
                2,
                QTableWidgetItem(_boundary_type_label(boundary_type)),
            )
        self.vertex_table.blockSignals(False)

    def _set_window_rows(self, rows):
        self.window_table.blockSignals(True)
        self.window_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                self.window_table.setItem(
                    row_index,
                    column,
                    QTableWidgetItem(f"{value:g}"),
                )
        self.window_table.blockSignals(False)

    def _add_vertex(self):
        row = self.vertex_table.rowCount()
        self.vertex_table.insertRow(row)
        previous_x = _cell_float(self.vertex_table, row - 1, 0, 0.0)
        previous_y = _cell_float(self.vertex_table, row - 1, 1, 0.0)
        self.vertex_table.setItem(row, 0, QTableWidgetItem(f"{previous_x:g}"))
        self.vertex_table.setItem(
            row, 1, QTableWidgetItem(f"{previous_y + 1000:g}")
        )
        self.vertex_table.setItem(row, 2, QTableWidgetItem("外墙"))

    def _remove_vertex(self):
        rows = sorted(
            {index.row() for index in self.vertex_table.selectedIndexes()},
            reverse=True,
        )
        if not rows and self.vertex_table.rowCount():
            rows = [self.vertex_table.rowCount() - 1]
        for row in rows:
            self.vertex_table.removeRow(row)
        self._refresh_preview()

    def _add_window(self):
        row = self.window_table.rowCount()
        self.window_table.insertRow(row)
        defaults = [1, 500, 1500, 900, 1500, 0.71, 0]
        for column, value in enumerate(defaults):
            self.window_table.setItem(
                row, column, QTableWidgetItem(f"{value:g}")
            )

    def _remove_window(self):
        rows = sorted(
            {index.row() for index in self.window_table.selectedIndexes()},
            reverse=True,
        )
        if not rows and self.window_table.rowCount():
            rows = [self.window_table.rowCount() - 1]
        for row in rows:
            self.window_table.removeRow(row)
        self._refresh_preview()

    def _candidate_space(self) -> SpaceModel:
        points = [
            Point2D(
                _cell_float(self.vertex_table, row, 0),
                _cell_float(self.vertex_table, row, 1),
            )
            for row in range(self.vertex_table.rowCount())
        ]
        walls = [
            WallSegment(
                start=point,
                end=points[(index + 1) % len(points)],
                boundary_type=_boundary_type_value(
                    self.vertex_table.item(index, 2).text()
                    if self.vertex_table.item(index, 2) else "外墙"
                ),
                name=f"墙{index + 1}",
                id=f"manual_wall_{index + 1}",
            )
            for index, point in enumerate(points)
        ] if len(points) >= 2 else []

        for row in range(self.window_table.rowCount()):
            wall_number = int(round(_cell_float(
                self.window_table, row, 0, 1.0
            )))
            if not 1 <= wall_number <= len(walls):
                continue
            walls[wall_number - 1].openings.append(WallOpening(
                kind="window",
                offset_mm=_cell_float(self.window_table, row, 1),
                width_mm=_cell_float(self.window_table, row, 2),
                sill_height_mm=_cell_float(self.window_table, row, 3),
                height_mm=_cell_float(self.window_table, row, 4),
                visible_transmittance=_cell_float(
                    self.window_table, row, 5, 0.71
                ),
                plane_offset_mm=_cell_float(
                    self.window_table, row, 6, 0.0
                ),
                name=f"窗{row + 1}",
                id=f"manual_window_{row + 1}",
            ))
        source_space = self._active_space()
        preserved_holes = (
            deepcopy(source_space.hole_loops())
            if source_space else []
        )
        return SpaceModel(
            id=source_space.id if source_space else "manual_space",
            name=self.space_name.text().strip() or "未命名空间",
            height_mm=self.space_height.value(),
            floor_elevation_mm=(
                source_space.floor_elevation_mm if source_space else 0.0
            ),
            boundary_loops=[
                BoundaryLoop(
                    id="manual_outer_loop",
                    name="空间外边界",
                    kind="outer",
                    segments=walls,
                ),
                *preserved_holes,
            ],
            material=(
                deepcopy(source_space.material)
                if source_space else SpaceModel().material
            ),
            thermal=(
                deepcopy(source_space.thermal)
                if source_space else SpaceModel().thermal
            ),
            shading=(
                deepcopy(source_space.shading)
                if source_space else SpaceModel().shading
            ),
            exterior_barriers=(
                deepcopy(source_space.exterior_barriers)
                if source_space else []
            ),
            metadata=(
                deepcopy(source_space.metadata)
                if source_space else {}
            ),
        )

    def _candidate_building(self) -> BuildingModel:
        space = self._candidate_space()
        building = deepcopy(self._source_building)
        building.name = self.building_name.text().strip() or "未命名建筑"
        building.north_angle_deg = self.north_angle.value()
        source_storey = next(
            (
                item
                for item in building.storeys
                if self._active_space_id
                and item.get_space(self._active_space_id) is not None
            ),
            None,
        )
        if source_storey is None:
            source_storey = StoreyModel(
                id="manual_storey",
                name=self.storey_name.text().strip() or "首层",
                default_height_mm=space.height_mm,
                spaces=[space],
            )
            building.storeys.append(source_storey)
        else:
            source_storey.name = self.storey_name.text().strip() or "首层"
            source_storey.default_height_mm = space.height_mm
            for index, existing in enumerate(source_storey.spaces):
                if existing.id == self._active_space_id:
                    source_storey.spaces[index] = space
                    break
        return building

    def _refresh_preview(self, *_):
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        try:
            space = self._candidate_space()
            _draw_space_plan(axis, space, show_coordinates=True)
            issues = validate_space(space)
            errors = [item for item in issues if item.severity == "error"]
            warnings = [item for item in issues if item.severity == "warning"]
            area = space_floor_area_mm2(space) / 1_000_000.0
            if errors:
                self.issue_label.setStyleSheet(
                    "background:#fef2f2;border:1px solid #fca5a5;"
                    "padding:8px;color:#991b1b;"
                )
                self.issue_label.setText(
                    f"面积：{area:.2f} ㎡　错误："
                    + "；".join(item.message for item in errors[:4])
                )
            else:
                self.issue_label.setStyleSheet(
                    "background:#f0fdf4;border:1px solid #86efac;"
                    "padding:8px;color:#166534;"
                )
                text = f"几何校验通过　面积：{area:.2f} ㎡"
                if warnings:
                    text += "　提示：" + "；".join(
                        item.message for item in warnings[:3]
                    )
                self.issue_label.setText(text)
        except Exception as exc:
            self.issue_label.setText(f"数据无法解析：{exc}")
            axis.text(
                0.5, 0.5, "请检查表格数值",
                ha="center", va="center", transform=axis.transAxes,
            )
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _validate_and_accept(self):
        try:
            building = self._candidate_building()
            space = building.spaces()[0]
            issues = validate_space(space)
            if has_geometry_errors(issues):
                QMessageBox.warning(
                    self,
                    "几何校验未通过",
                    "\n".join(
                        f"• {item.message}"
                        for item in issues
                        if item.severity == "error"
                    ),
                )
                return
            self.result_building = building
            self.result_active_space_id = space.id
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "无法应用", str(exc))


def _spin(minimum, maximum, step, suffix=""):
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(2)
    widget.setSingleStep(step)
    widget.setSuffix(suffix)
    return widget


def _cell_float(
    table: QTableWidget,
    row: int,
    column: int,
    default: float = 0.0,
) -> float:
    if row < 0 or row >= table.rowCount():
        return default
    item = table.item(row, column)
    if item is None or not item.text().strip():
        return default
    return float(item.text().strip())


def _boundary_type_label(value: str) -> str:
    return {
        "exterior": "外墙",
        "interior": "内墙",
        "adiabatic": "绝热边界",
        "ground": "接地边界",
    }.get(value, value)


def _boundary_type_value(value: str) -> str:
    normalised = value.strip()
    return {
        "外墙": "exterior",
        "内墙": "interior",
        "绝热": "adiabatic",
        "绝热边界": "adiabatic",
        "接地": "ground",
        "接地边界": "ground",
    }.get(normalised, normalised)

