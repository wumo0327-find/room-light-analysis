"""Batch optical/thermal boundary settings for selected v4 spaces."""
from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.complex_models import SpaceModel


def _spin(value, low, high, step, decimals=2, suffix=""):
    widget = QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setDecimals(decimals)
    widget.setSingleStep(step)
    widget.setValue(float(value))
    if suffix:
        widget.setSuffix(suffix)
    return widget


class SpaceAnalysisSettingsDialog(QDialog):
    """Apply one explicit analysis-parameter set to all selected rooms."""

    def __init__(self, spaces: Sequence[SpaceModel], parent=None):
        super().__init__(parent)
        self._spaces = list(spaces)
        if not self._spaces:
            raise ValueError("至少需要一个房间。")
        source = self._spaces[0]
        self.setWindowTitle("选中房间分析参数")
        self.setMinimumSize(420, 420)
        self.setSizeGripEnabled(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        content = QWidget()
        content.setObjectName("analysisSettingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 2, 8, 4)
        content_layout.setSpacing(7)
        self.scroll_area.setWidget(content)
        root.addWidget(self.scroll_area, 1)

        note = QLabel(
            f"将以下参数统一应用到 {len(self._spaces)} 个选中房间。"
            "当前显示第一个房间的数值；这不会改变BP平面几何。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;"
            "padding:7px;color:#1e3a8a;"
        )
        content_layout.addWidget(note)

        optical = QGroupBox("采光与玻璃")
        optical_form = QFormLayout(optical)
        self._compact_form(optical_form)
        self.rho_wall = _spin(source.material.rho_wall, 0.0, 0.95, 0.01)
        self.rho_ceiling = _spin(source.material.rho_ceiling, 0.0, 0.95, 0.01)
        self.rho_floor = _spin(source.material.rho_floor, 0.0, 0.95, 0.01)
        first_window = next(
            (
                opening
                for wall in source.wall_segments()
                for opening in wall.windows()
                if wall.boundary_type in {"exterior", "ground"}
            ),
            None,
        )
        self.visible_transmittance = _spin(
            first_window.visible_transmittance if first_window else 0.71,
            0.0, 1.0, 0.01,
        )
        optical_form.addRow("墙面反射率", self.rho_wall)
        optical_form.addRow("顶棚反射率", self.rho_ceiling)
        optical_form.addRow("地面反射率", self.rho_floor)
        optical_form.addRow("外窗可见光透射比", self.visible_transmittance)
        content_layout.addWidget(optical)

        thermal = QGroupBox("热工与使用条件")
        thermal_form = QFormLayout(thermal)
        self._compact_form(thermal_form)
        t = source.thermal
        self.u_wall = _spin(t.U_wall, 0.05, 10.0, 0.05, suffix=" W/(m²·K)")
        self.u_window = _spin(t.U_win, 0.05, 10.0, 0.05, suffix=" W/(m²·K)")
        self.u_roof = _spin(t.U_roof, 0.05, 10.0, 0.05, suffix=" W/(m²·K)")
        self.u_floor = _spin(t.U_floor, 0.05, 10.0, 0.05, suffix=" W/(m²·K)")
        self.sc_glass = _spin(t.SC_glass, 0.0, 1.5, 0.01)
        self.wall_abs = _spin(t.wall_solar_abs, 0.0, 1.0, 0.01)
        self.air_changes = _spin(t.n_ach, 0.0, 20.0, 0.1, suffix=" 次/h")
        self.people_gain = _spin(t.q_people, 0.0, 100.0, 0.5, suffix=" W/m²")
        self.equipment_gain = _spin(t.q_equipment, 0.0, 100.0, 0.5, suffix=" W/m²")
        self.lighting_gain = _spin(t.q_lighting, 0.0, 100.0, 0.5, suffix=" W/m²")
        for label, widget in (
            ("外墙传热系数", self.u_wall),
            ("外窗传热系数", self.u_window),
            ("屋面传热系数", self.u_roof),
            ("地面/外露楼板传热系数", self.u_floor),
            ("玻璃遮阳系数 SC", self.sc_glass),
            ("外墙太阳吸收率", self.wall_abs),
            ("换气次数", self.air_changes),
            ("人员热扰", self.people_gain),
            ("设备热扰", self.equipment_gain),
            ("照明热扰", self.lighting_gain),
        ):
            thermal_form.addRow(label, widget)
        content_layout.addWidget(thermal)

        boundaries = QGroupBox("上下边界条件（对中间楼层尤其重要）")
        boundary_layout = QVBoxLayout(boundaries)
        boundary_layout.setContentsMargins(12, 9, 12, 9)
        boundary_layout.setSpacing(5)
        self.roof_exposed = QCheckBox("房间上方直接接触室外屋面")
        self.floor_exposed = QCheckBox("房间下方直接接触地面或室外架空楼板")
        self.roof_exposed.setChecked(bool(source.roof_exposed))
        self.floor_exposed.setChecked(bool(source.floor_exposed))
        boundary_layout.addWidget(self.roof_exposed)
        boundary_layout.addWidget(self.floor_exposed)
        warning = QLabel(
            "普通二层且上下均为室内空间时，两项通常都应关闭；顶层开启屋面，"
            "首层贴地时开启地面。请按实际剖面确认。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#92400e;")
        boundary_layout.addWidget(warning)
        content_layout.addWidget(boundaries)
        content_layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用到选中房间")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._apply_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.setStyleSheet("""
            QScrollArea, QWidget#analysisSettingsContent {
                background: transparent;
            }
            QGroupBox {
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                margin-top: 11px;
                padding-top: 5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 5px;
                color: #2563eb;
                font-weight: 700;
            }
            QScrollBar:vertical {
                width: 13px;
                margin: 1px;
                background: #f1f5f9;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                min-height: 32px;
                background: #94a3b8;
                border-radius: 6px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
        self._fit_to_available_screen(parent)

    @staticmethod
    def _compact_form(layout: QFormLayout) -> None:
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)
        layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        layout.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

    def _fit_to_available_screen(self, parent) -> None:
        screen = (
            parent.screen()
            if parent is not None and parent.screen() is not None
            else QApplication.primaryScreen()
        )
        if screen is None:
            self.resize(620, 720)
            return
        available = screen.availableGeometry()
        width = max(420, min(660, available.width() - 48))
        height = max(420, min(780, available.height() - 72))
        self.resize(width, height)

    def _apply_and_accept(self):
        for space in self._spaces:
            space.material.rho_wall = self.rho_wall.value()
            space.material.rho_ceiling = self.rho_ceiling.value()
            space.material.rho_floor = self.rho_floor.value()
            thermal = space.thermal
            thermal.U_wall = self.u_wall.value()
            thermal.U_win = self.u_window.value()
            thermal.U_roof = self.u_roof.value()
            thermal.U_floor = self.u_floor.value()
            thermal.SC_glass = self.sc_glass.value()
            thermal.wall_solar_abs = self.wall_abs.value()
            thermal.n_ach = self.air_changes.value()
            thermal.q_people = self.people_gain.value()
            thermal.q_equipment = self.equipment_gain.value()
            thermal.q_lighting = self.lighting_gain.value()
            space.roof_exposed = self.roof_exposed.isChecked()
            space.floor_exposed = self.floor_exposed.isChecked()
            space.metadata["boundary_conditions_explicit"] = True
            for wall in space.wall_segments():
                if wall.boundary_type not in {"exterior", "ground"}:
                    continue
                wall.u_value = thermal.U_wall
                for opening in wall.windows():
                    opening.visible_transmittance = self.visible_transmittance.value()
                    opening.u_value = thermal.U_win
                    opening.solar_heat_gain_coefficient = thermal.SC_glass
        self.accept()
