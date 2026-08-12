"""
ui/complex_space_editor.py — Manual complex-space editor  v3.1.0

The first v3 editor intentionally focuses on one calculable space at a time:
outer polygon, arbitrary wall directions and rectangular wall openings.  The
underlying data model already supports multiple storeys/spaces and hole loops;
those editing controls will be added after the calculation engines migrate.
"""
from __future__ import annotations

from copy import deepcopy
from typing import List, Optional

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
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.space_geometry import (
    has_geometry_errors,
    space_floor_area_mm2,
    validate_space,
)
from core.complex_thermal import wall_azimuth_deg


class ComplexSpaceSummary(QWidget):
    """Main building-view sidebar for v3 projects."""

    edit_requested = pyqtSignal()
    view_changed = pyqtSignal(str)
    weather_dialog_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        title = QLabel("建筑视图")
        title.setStyleSheet("font-size:16px;font-weight:700;color:#1d4ed8;")
        subtitle = QLabel("复杂功能模型")
        subtitle.setStyleSheet("color:#64748b;padding-bottom:4px;")
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            "background:#ffffff;border:1px solid #d0d5e0;border-radius:6px;"
            "padding:10px;color:#30364a;"
        )
        edit = QPushButton("编辑空间几何与窗户")
        edit.setObjectName("primary_btn")
        edit.clicked.connect(self.edit_requested)
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
            "建筑视图现使用复杂空间模型。旧版矩形 .rlproj 会在打开时自动转换；"
            "参数化遮阳实验已使用当前活动空间的真实多边形和全部外窗。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#6b7280;padding:8px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self._summary)
        layout.addWidget(edit)
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
    ) -> None:
        space = building.get_space(active_space_id) if active_space_id else None
        selected_view = self._view_combo.currentData()
        self._view_combo.blockSignals(True)
        self._view_combo.clear()
        self._view_combo.addItem("平面图", "plan")
        if space is None:
            self._summary.setText(
                f"建筑：{building.name}\n尚未选择计算空间。"
            )
            self._view_combo.blockSignals(False)
            return
        storey_name = next(
            (
                storey.name
                for storey in building.storeys
                if storey.get_space(space.id) is not None
            ),
            "未命名楼层",
        )
        window_count = sum(
            len(wall.windows())
            for wall in space.wall_segments()
        )
        self._summary.setText(
            f"建筑：{building.name}\n"
            f"楼层：{storey_name}\n"
            f"空间：{space.name}\n"
            f"面积：{space_floor_area_mm2(space) / 1_000_000:.2f} ㎡\n"
            f"层高：{space.height_mm / 1000:.2f} m\n"
            f"墙段：{len(space.wall_segments())} 段\n"
            f"外窗：{window_count} 扇"
        )
        for index, wall in enumerate(space.wall_segments(), 1):
            name = wall.name or f"墙{index}"
            self._view_combo.addItem(
                f"{name}立面（{wall.length_mm / 1000:.2f} m）",
                wall.id,
            )
        target_index = self._view_combo.findData(selected_view)
        self._view_combo.setCurrentIndex(max(0, target_index))
        self._view_combo.blockSignals(False)


class ComplexSpaceCanvas(QWidget):
    """Plan and per-wall elevation preview for the active v3 space."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building: Optional[BuildingModel] = None
        self._active_space_id: Optional[str] = None
        self._view_id = "plan"
        self.figure = Figure(facecolor="#ffffff")
        self.canvas = FigureCanvas(self.figure)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def set_model(
        self,
        building: BuildingModel,
        active_space_id: Optional[str],
    ) -> None:
        self._building = building
        self._active_space_id = active_space_id
        space = building.get_space(active_space_id) if active_space_id else None
        if (
            self._view_id != "plan"
            and (space is None or space.get_wall(self._view_id) is None)
        ):
            self._view_id = "plan"
        self.refresh()

    def set_view(self, view_id: str) -> None:
        self._view_id = view_id or "plan"
        self.refresh()

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
        if space is None or space.outer_loop() is None:
            axis.text(
                0.5, 0.5, "尚未建立复杂空间",
                ha="center", va="center", transform=axis.transAxes,
                color="#64748b", fontsize=15,
            )
            axis.set_axis_off()
            self.canvas.draw()
            return

        wall = space.get_wall(self._view_id) if self._view_id != "plan" else None
        if wall is None:
            self._view_id = "plan"
            _draw_space_plan(axis, space, show_coordinates=True)
            axis.set_title(
                f"{building.name} — {space.name}｜平面图",
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
        f"方位角 {azimuth:.1f}°",
        fontsize=14,
        fontweight="bold",
        color="#1e3a8a",
    )


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
            axis.plot(
                [wall.start.x, wall.end.x],
                [wall.start.y, wall.end.y],
                color="#1e40af",
                linewidth=5,
                solid_capstyle="round",
                zorder=3,
            )
            midpoint = wall.point_at(wall.length_mm / 2.0)
            axis.text(
                midpoint.x,
                midpoint.y,
                f"墙{index + 1}",
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
                start = wall.point_at(opening.offset_mm)
                end = wall.point_at(opening.end_offset_mm)
                axis.plot(
                    [start.x, end.x],
                    [start.y, end.y],
                    color="#06b6d4",
                    linewidth=7,
                    solid_capstyle="butt",
                    zorder=5,
                )
                centre = wall.point_at(
                    opening.offset_mm + opening.width_mm / 2.0
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
    axis.set_aspect("equal", adjustable="datalim")
    axis.grid(True, color="#e2e8f0", linewidth=0.7)
    axis.set_xlabel("X / mm")
    axis.set_ylabel("Y / mm")


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
        self.window_table = QTableWidget(0, 6)
        self.window_table.setHorizontalHeaderLabels([
            "墙序号", "距墙起点/mm", "宽/mm", "窗台/mm", "高/mm", "透光率",
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
        defaults = [1, 500, 1500, 900, 1500, 0.71]
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
