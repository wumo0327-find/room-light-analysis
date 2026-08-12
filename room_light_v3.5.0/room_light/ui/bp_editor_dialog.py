"""Modal host that embeds the complete BP drafting program inside RoomLight."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from bp_editor.main_window import MainWindow as BpMainWindow
from core.complex_models import BuildingModel
from io_utils.bp_bridge import building_from_document, document_from_building


class EmbeddedBpEditorDialog(QDialog):
    """Full BP editor with an explicit in-memory handoff back to RoomLight."""

    def __init__(
        self,
        building: BuildingModel,
        active_space_id: str | None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("建筑视图｜BP复杂平面建模器")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1500, 920)
        self._source_building = building
        self._source_active_space_id = active_space_id
        self.result_building: BuildingModel | None = None
        self.result_active_space_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        document, note = document_from_building(building)
        self.editor = BpMainWindow(parent=self)
        self.editor.setWindowFlag(Qt.WindowType.Window, False)
        self.editor.canvas.set_document(document)
        self.editor.current_file = None
        self.editor.imported_source = None
        self.editor._update_title()
        self.editor._show_prompt(note)
        root.addWidget(self.editor, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 10)
        explanation = QLabel(
            "BP中墙体围合区域将转换为RL第一层的可计算房间；窗和栏杆参与采光/热环境。"
            "气象、朝向及可匹配房间的材料/热工参数会继续保留。"
            "其他楼层保持不变。"
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#475569;padding-right:12px;")
        cancel = QPushButton("取消并返回")
        apply_button = QPushButton("✓ 应用到RL分析模型")
        apply_button.setObjectName("primary_btn")
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self._apply_to_roomlight)
        footer.addWidget(explanation, 1)
        footer.addWidget(cancel)
        footer.addWidget(apply_button)
        root.addLayout(footer)

    def _apply_to_roomlight(self) -> None:
        try:
            building, active_space_id, summary = building_from_document(
                self.editor.canvas.document,
                self._source_building,
                project_name=self._source_building.name or "BP建筑模型",
                previous_active_space_id=self._source_active_space_id,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "无法应用到RL",
                "请先确保墙体形成有效闭合房间，并修正窗体越界等问题。\n\n"
                + str(exc),
            )
            return
        self.result_building = building
        self.result_active_space_id = active_space_id
        QMessageBox.information(
            self,
            "模型转换完成",
            f"已生成 {summary.spaces} 个可计算房间、"
            f"{summary.walls} 段边界墙、{summary.windows} 扇窗、"
            f"{summary.railings} 道栏杆。",
        )
        self.accept()

    def done(self, result: int) -> None:
        try:
            self.editor._hide_parameter_palettes()
            self.editor.selection_properties.hide()
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self.editor)
        finally:
            super().done(result)
