"""Modeless floating parameter palettes for BP drafting commands."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .model import DimensionChain, DraftDocument, Line, Railing, Wall, Window


def _spin(
    minimum: float,
    maximum: float,
    value: float,
    step: float,
    suffix: str = " mm",
) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    widget.setDecimals(0)
    widget.setSingleStep(step)
    widget.setSuffix(suffix)
    widget.setKeyboardTracking(False)
    return widget


class _BasePalette(QDialog):
    parameters_changed = pyqtSignal(dict)
    palette_hidden = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(265)

    def finish(self, layout: QVBoxLayout) -> None:
        self.note = QLabel("参数实时生效；直接回到图面点击即可绘制。")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#64748b; padding:4px 0;")
        layout.addWidget(self.note)
        self.close_button = QPushButton("关闭参数窗（绘图命令继续）")
        self.close_button.clicked.connect(self.hide)
        layout.addWidget(self.close_button)
        self._connect_live_controls()

    def _connect_live_controls(self) -> None:
        raise NotImplementedError

    def _emit_values(self, *_args) -> None:
        self.parameters_changed.emit(self.values())

    def set_edit_mode(self, enabled: bool) -> None:
        self.setWindowTitle(
            self.edit_title if enabled else self.creation_title
        )
        self.note.setText(
            "正在编辑已选构件；参数修改后立即作用于图中对象。"
            if enabled
            else "参数实时生效；直接回到图面点击即可绘制。"
        )
        self.close_button.setText(
            "完成构件参数编辑"
            if enabled
            else "关闭参数窗（绘图命令继续）"
        )

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.palette_hidden.emit()

    def show_near_canvas(self) -> None:
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            top_right = parent.mapToGlobal(parent.rect().topRight())
            self.move(QPoint(
                top_right.x() - self.size().width() - 32,
                top_right.y() + 105,
            ))
        self.show()
        self.raise_()
        self.activateWindow()


class WallSettingsDialog(_BasePalette):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.creation_title = "墙体参数 HZQT"
        self.edit_title = "编辑墙体参数"
        self.setWindowTitle(self.creation_title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.height = _spin(
            100.0, 20_000.0, values.get("height_mm", 3000.0), 100.0,
        )
        self.width = _spin(
            50.0, 2000.0, values.get("width_mm", 200.0), 10.0,
        )
        self.axis = QComboBox()
        self.axis.addItem("轴线居中", "center")
        self.axis.addItem("轴线靠左", "left")
        self.axis.addItem("轴线靠右", "right")
        index = self.axis.findData(values.get("axis", "center"))
        self.axis.setCurrentIndex(max(0, index))
        form.addRow("墙高", self.height)
        form.addRow("墙宽", self.width)
        form.addRow("轴线位置", self.axis)
        layout.addLayout(form)
        self.finish(layout)

    def _connect_live_controls(self) -> None:
        self.height.valueChanged.connect(self._emit_values)
        self.width.valueChanged.connect(self._emit_values)
        self.axis.currentIndexChanged.connect(self._emit_values)

    def values(self) -> dict:
        return {
            "height_mm": self.height.value(),
            "width_mm": self.width.value(),
            "axis": str(self.axis.currentData()),
        }

    def set_values(self, values: dict) -> None:
        blockers = [
            QSignalBlocker(self.height),
            QSignalBlocker(self.width),
            QSignalBlocker(self.axis),
        ]
        self.height.setValue(float(values["height_mm"]))
        self.width.setValue(float(values["width_mm"]))
        index = self.axis.findData(values["axis"])
        self.axis.setCurrentIndex(max(0, index))
        del blockers


class WindowSettingsDialog(_BasePalette):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.creation_title = "窗体参数 MC"
        self.edit_title = "编辑窗体参数"
        self.setWindowTitle(self.creation_title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.sill = _spin(
            0.0, 10_000.0, values.get("sill_height_mm", 600.0), 100.0,
        )
        self.height = _spin(
            100.0, 10_000.0, values.get("height_mm", 1800.0), 100.0,
        )
        self.width = _spin(
            100.0, 20_000.0, values.get("width_mm", 1500.0), 100.0,
        )
        form.addRow("窗台高", self.sill)
        form.addRow("窗户高度", self.height)
        form.addRow("窗宽", self.width)
        layout.addLayout(form)
        self.finish(layout)

    def _connect_live_controls(self) -> None:
        self.sill.valueChanged.connect(self._emit_values)
        self.height.valueChanged.connect(self._emit_values)
        self.width.valueChanged.connect(self._emit_values)

    def values(self) -> dict:
        return {
            "sill_height_mm": self.sill.value(),
            "height_mm": self.height.value(),
            "width_mm": self.width.value(),
        }

    def set_values(self, values: dict) -> None:
        blockers = [
            QSignalBlocker(self.sill),
            QSignalBlocker(self.height),
            QSignalBlocker(self.width),
        ]
        self.sill.setValue(float(values["sill_height_mm"]))
        self.height.setValue(float(values["height_mm"]))
        self.width.setValue(float(values["width_mm"]))
        del blockers


class RailingSettingsDialog(_BasePalette):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.creation_title = "栏杆参数 LG"
        self.edit_title = "编辑栏杆参数"
        self.setWindowTitle(self.creation_title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.height = _spin(
            100.0, 5000.0, values.get("height_mm", 1100.0), 100.0,
        )
        self.width = _spin(
            10.0, 500.0, values.get("width_mm", 50.0), 10.0,
        )
        self.material = QComboBox()
        for material in ("金属栏杆", "玻璃栏杆", "混凝土栏板", "木质栏杆"):
            self.material.addItem(material)
        index = self.material.findText(values.get("material", "金属栏杆"))
        self.material.setCurrentIndex(max(0, index))
        form.addRow("栏杆高度", self.height)
        form.addRow("绘图宽度", self.width)
        form.addRow("栏杆材料", self.material)
        layout.addLayout(form)
        self.finish(layout)

    def _connect_live_controls(self) -> None:
        self.height.valueChanged.connect(self._emit_values)
        self.width.valueChanged.connect(self._emit_values)
        self.material.currentIndexChanged.connect(self._emit_values)

    def values(self) -> dict:
        return {
            "height_mm": self.height.value(),
            "width_mm": self.width.value(),
            "material": self.material.currentText(),
        }

    def set_values(self, values: dict) -> None:
        blockers = [
            QSignalBlocker(self.height),
            QSignalBlocker(self.width),
            QSignalBlocker(self.material),
        ]
        self.height.setValue(float(values["height_mm"]))
        self.width.setValue(float(values["width_mm"]))
        index = self.material.findText(str(values["material"]))
        if index < 0:
            self.material.addItem(str(values["material"]))
            index = self.material.findText(str(values["material"]))
        self.material.setCurrentIndex(max(0, index))
        del blockers


class SelectionPropertiesDialog(QDialog):
    """CAD-style Ctrl+1 property viewer for single or multiple selections."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle("特性 Ctrl+1")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        self.summary = QLabel("未选择图元")
        self.summary.setStyleSheet(
            "font-weight:700; color:#1d4ed8; padding:3px;"
        )
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(("图元", "参数", "值"))
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.tree.header().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.tree.header().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.tree, 1)
        note = QLabel(
            "显示当前选择集的实际参数；单个墙、窗或栏杆仍可双击进入快速编辑。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b; padding:3px;")
        layout.addWidget(note)
        close_button = QPushButton("关闭特性窗口")
        close_button.clicked.connect(self.hide)
        layout.addWidget(close_button)

    @staticmethod
    def _point_text(point) -> str:
        return f"({point.x:.0f}, {point.y:.0f}) mm"

    @classmethod
    def _entity_properties(
        cls,
        entity,
    ) -> tuple[str, list[tuple[str, str]]]:
        if isinstance(entity, Wall):
            axis_name = {
                "center": "居中",
                "left": "靠左",
                "right": "靠右",
            }.get(entity.axis, entity.axis)
            return "墙体", [
                ("起点", cls._point_text(entity.start)),
                ("终点", cls._point_text(entity.end)),
                ("长度", f"{entity.length_mm:.0f} mm"),
                ("墙高", f"{entity.height_mm:.0f} mm"),
                ("墙宽", f"{entity.width_mm:.0f} mm"),
                ("轴线位置", axis_name),
            ]
        if isinstance(entity, Window):
            return "窗体", [
                ("宿主墙", entity.wall_id),
                ("距墙起点", f"{entity.offset_mm:.0f} mm"),
                ("窗宽", f"{entity.width_mm:.0f} mm"),
                ("窗台高", f"{entity.sill_height_mm:.0f} mm"),
                ("窗高", f"{entity.height_mm:.0f} mm"),
            ]
        if isinstance(entity, Railing):
            return "栏杆", [
                ("起点", cls._point_text(entity.start)),
                ("终点", cls._point_text(entity.end)),
                ("长度", f"{entity.length_mm:.0f} mm"),
                ("高度", f"{entity.height_mm:.0f} mm"),
                ("绘图宽度", f"{entity.width_mm:.0f} mm"),
                ("材料", entity.material),
            ]
        if isinstance(entity, Line):
            return "直线", [
                ("起点", cls._point_text(entity.start)),
                ("终点", cls._point_text(entity.end)),
                ("长度", f"{entity.length_mm:.0f} mm"),
            ]
        if isinstance(entity, DimensionChain):
            return "尺寸标注", [
                ("测量点数量", str(len(entity.points))),
                ("标注偏移", f"{entity.offset_mm:.0f} mm"),
                (
                    "测量点",
                    "；".join(cls._point_text(point) for point in entity.points),
                ),
            ]
        return "未知图元", []

    def set_selection(
        self,
        document: DraftDocument,
        entity_ids: set[str],
    ) -> None:
        self.tree.clear()
        entities = [
            entity
            for collection in (
                document.walls,
                document.windows,
                document.railings,
                document.lines,
                document.dimensions,
            )
            for entity in collection
            if entity.id in entity_ids
        ]
        counts: dict[str, int] = {}
        for entity in entities:
            type_name, properties = self._entity_properties(entity)
            counts[type_name] = counts.get(type_name, 0) + 1
            root = QTreeWidgetItem([
                type_name,
                "图元ID",
                entity.id,
            ])
            root.setExpanded(True)
            self.tree.addTopLevelItem(root)
            for name, value in properties:
                root.addChild(QTreeWidgetItem(["", name, value]))
        if not entities:
            self.summary.setText("未选择图元")
            return
        details = "，".join(
            f"{name}{count}"
            for name, count in counts.items()
        )
        self.summary.setText(
            f"当前选择 {len(entities)} 个图元｜{details}"
        )
        self.tree.expandAll()

    def show_near_canvas(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            top_right = parent.mapToGlobal(parent.rect().topRight())
            self.move(QPoint(
                top_right.x() - self.width() - 32,
                top_right.y() + 105,
            ))
        self.show()
        self.raise_()
        self.activateWindow()
