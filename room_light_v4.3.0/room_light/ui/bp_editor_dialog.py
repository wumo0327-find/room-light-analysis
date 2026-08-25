"""Modal host that embeds the complete BP drafting program inside RoomLight."""
from __future__ import annotations

from pathlib import Path

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
from bp_editor.model import DraftDocument
from core.complex_models import BuildingModel
from io_utils.bp_bridge import building_from_document, document_from_building


class EmbeddedBpEditorDialog(QDialog):
    """Full BP editor with an explicit in-memory handoff back to RoomLight."""

    def __init__(
        self,
        building: BuildingModel,
        active_space_id: str | None,
        document: DraftDocument | None = None,
        source_path: str | None = None,
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
        self.result_document: DraftDocument | None = None
        self.result_source_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if document is None:
            document, note = document_from_building(building)
        else:
            document = DraftDocument.from_dict(document.to_dict())
            note = "已直接载入建筑视图当前使用的BP草图。"
        self.editor = BpMainWindow(parent=self, embedded_mode=True)
        self.editor.setWindowFlag(Qt.WindowType.Window, False)
        self.editor.canvas.set_document(document)
        self.editor.current_file = Path(source_path) if source_path else None
        self.editor.imported_source = None
        self.editor._update_title()
        self.editor._show_prompt(note)
        self.editor.embedded_save_requested.connect(self._save_and_return)
        root.addWidget(self.editor, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 10)
        explanation = QLabel(
            "BP草图是建筑视图的唯一显示数据源。保存后建筑视图立即读取这份草图；"
            "可计算房间只在内存中同步生成，不创建中间rlproj文件。"
            "气象、朝向及可匹配房间的材料/热工参数继续保留。"
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#475569;padding-right:12px;")
        cancel = QPushButton("取消并返回")
        apply_button = QPushButton("✓ 保存并返回建筑视图")
        apply_button.setObjectName("primary_btn")
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self._save_and_return)
        footer.addWidget(explanation, 1)
        footer.addWidget(cancel)
        footer.addWidget(apply_button)
        root.addLayout(footer)

    def _save_and_return(self) -> None:
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
                "无法保存建筑草图",
                "请先确保墙体形成有效闭合房间，并修正窗体越界等问题。\n\n"
                + str(exc),
            )
            return
        self.result_building = building
        self.result_active_space_id = active_space_id
        self.result_document = DraftDocument.from_dict(
            self.editor.canvas.document.to_dict()
        )
        self.result_source_path = (
            str(self.editor.current_file) if self.editor.current_file else None
        )
        self.editor._show_prompt(
            f"已保存并同步：{summary.spaces}个房间、{summary.walls}段边界墙、"
            f"{summary.windows}扇窗、{summary.railings}道栏杆。"
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
